#!/usr/bin/env python3
"""Backfill station_body_links from existing station/body data.

Safe defaults:
- dry-run unless --apply is provided
- preserves existing confirmed links unless --overwrite-confirmed is provided
- supports --limit and --system-id64 for small production probes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any

import psycopg
from psycopg.rows import dict_row
from shared_contracts.evidence_identity import (
    content_addressed_evidence_key as _content_addressed_evidence_key,
    datetime_to_utc_isoformat as _dt_to_str,
)

from api_source_resolver import add_api_source_to_path


API_SRC = add_api_source_to_path(__file__, required_paths=('station_body_resolver.py',))

from station_body_resolver import build_station_body_link_rows  # noqa: E402


UPSERT_COLUMNS = (
    'station_id',
    'market_id',
    'system_id64',
    'body_id',
    'body_name',
    'lane',
    'association_status',
    'association_confidence',
    'association_source',
    'resolver_notes',
)
CANONICAL_EVIDENCE_SOURCE = 'canonical_app_data'
CANONICAL_EVIDENCE_TYPE = 'station_set'
CANONICAL_EVIDENCE_TRIGGER = 'station_body_link_backfill'


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(',', ':'), sort_keys=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Backfill normalized station/body occupied-slot links.')
    parser.add_argument('--dsn', default=os.environ.get('DATABASE_URL'), help='Postgres DSN. Defaults to DATABASE_URL.')
    parser.add_argument('--system-id64', type=int, default=None, help='Restrict to one system id64.')
    parser.add_argument('--limit', type=int, default=None, help='Limit number of systems processed.')
    parser.add_argument('--apply', action='store_true', help='Write links. Without this, runs as dry-run.')
    parser.add_argument('--dry-run', action='store_true', help='Force dry-run mode, even if --apply is absent.')
    parser.add_argument('--overwrite-confirmed', action='store_true', help='Allow resolver output to overwrite existing confirmed links.')
    return parser.parse_args()


def fetch_system_ids(conn, *, system_id64: int | None, limit: int | None) -> list[int]:
    if system_id64 is not None:
        return [system_id64]
    sql = 'SELECT DISTINCT system_id64 FROM stations ORDER BY system_id64'
    params: list[Any] = []
    if limit is not None:
        sql += ' LIMIT %s'
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [int(row[0]) for row in cur.fetchall()]


def fetch_system_payload(conn, system_id64: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, dict[str, Any]]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT id, system_id64, name, distance_from_star
            FROM bodies
            WHERE system_id64 = %s
        """, (system_id64,))
        bodies = [dict(row) for row in cur.fetchall()]

        cur.execute("""
            SELECT id, id AS market_id, system_id64, name, station_type::text AS station_type,
                   distance_from_star, distance_source, distance_confidence,
                   body_name AS station_body_name, body_name,
                   body_name_source, body_name_confidence
            FROM stations
            WHERE system_id64 = %s
        """, (system_id64,))
        stations = [dict(row) for row in cur.fetchall()]

        cur.execute("""
            SELECT station_id, market_id, system_id64, body_id, body_name, lane,
                   association_status, association_confidence, association_source,
                   resolver_notes
            FROM station_body_links
            WHERE system_id64 = %s
        """, (system_id64,))
        existing = {int(row['station_id']): dict(row) for row in cur.fetchall()}

    return bodies, stations, existing


def upsert_links(conn, rows, *, overwrite_confirmed: bool) -> None:
    if not rows:
        return
    values = [row.to_db_tuple() for row in rows if row.station_id is not None and row.system_id64 is not None]
    if not values:
        return

    conflict_filter = '' if overwrite_confirmed else "WHERE station_body_links.association_status <> 'confirmed'"
    sql = f"""
        INSERT INTO station_body_links ({', '.join(UPSERT_COLUMNS)})
        VALUES ({', '.join(['%s'] * len(UPSERT_COLUMNS))})
        ON CONFLICT (station_id) DO UPDATE SET
            market_id = EXCLUDED.market_id,
            system_id64 = EXCLUDED.system_id64,
            body_id = EXCLUDED.body_id,
            body_name = EXCLUDED.body_name,
            lane = EXCLUDED.lane,
            association_status = EXCLUDED.association_status,
            association_confidence = EXCLUDED.association_confidence,
            association_source = EXCLUDED.association_source,
            resolver_notes = EXCLUDED.resolver_notes,
            updated_at = NOW()
        {conflict_filter}
    """
    with conn.cursor() as cur:
        cur.executemany(sql, values)


def build_station_set_evidence_payload(conn, system_id64: int) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                s.id64,
                s.name AS system_name,
                COUNT(st.id)::int AS station_count,
                COUNT(l.station_id)::int FILTER (
                    WHERE l.association_status = 'local_matched'
                ) AS linked_station_count,
                MAX(
                    GREATEST(
                        COALESCE(st.distance_updated_at, '-infinity'::timestamptz),
                        COALESCE(st.station_type_updated_at, '-infinity'::timestamptz),
                        COALESCE(st.body_name_updated_at, '-infinity'::timestamptz),
                        COALESCE(l.updated_at, '-infinity'::timestamptz)
                    )
                ) AS observed_at
            FROM systems s
            LEFT JOIN stations st ON st.system_id64 = s.id64
            LEFT JOIN station_body_links l ON l.station_id = st.id
            WHERE s.id64 = %s
            GROUP BY s.id64, s.name
            """,
            (system_id64,),
        )
        row = cur.fetchone()
    if not row:
        return None

    station_count = int(row['station_count'] or 0)
    if station_count <= 0:
        return None
    linked_station_count = int(row['linked_station_count'] or 0)
    unresolved_station_count = max(0, station_count - linked_station_count)
    summary = (
        f"Canonical station data for {row['system_name']} currently includes {station_count} stations; "
        f'{linked_station_count}/{station_count} local station-body links are matched.'
    )
    confidence = 'high' if linked_station_count >= station_count else 'medium'
    observed_at = _dt_to_str(row.get('observed_at'))
    payload = {
        'system_id64': int(row['id64']),
        'source_name': CANONICAL_EVIDENCE_SOURCE,
        'origin': 'derived',
        'subject_type': 'system',
        'subject_id': str(row['id64']),
        'evidence_type': CANONICAL_EVIDENCE_TYPE,
        'record_status': 'active',
        'freshness_status': 'current',
        'confidence': confidence,
        'summary': summary,
        'source_record_id': None,
        'source_run_key': None,
        'observed_at': observed_at,
        'collected_at': None,
        'expires_at': None,
        'value': {
            'system_name': row['system_name'],
            'station_count': station_count,
            'linked_station_count': linked_station_count,
            'unresolved_station_count': unresolved_station_count,
            'station_link_runtime_available': True,
        },
        'provenance': {
            'promotion_lane': 'system_canonical_evidence/v1',
            'trigger_context': CANONICAL_EVIDENCE_TRIGGER,
            'source_tables': ['stations', 'station_body_links'],
            'station_data_updated_at': observed_at,
        },
        'tags': ['canonical_promotion', 'coverage'],
        'metadata': {
            'promotion_lane': 'system_canonical_evidence/v1',
            'trigger_context': CANONICAL_EVIDENCE_TRIGGER,
            'requested_evidence_type': CANONICAL_EVIDENCE_TYPE,
        },
    }
    payload['evidence_key'] = _content_addressed_evidence_key(payload)
    return payload


def promote_station_set_evidence(conn, system_id64: int) -> str:
    payload = build_station_set_evidence_payload(conn, system_id64)
    if payload is None:
        return 'missing'

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT *
            FROM evidence_records
            WHERE system_id64 = %s
              AND source_name = %s
              AND subject_type = %s
              AND subject_id IS NOT DISTINCT FROM %s
              AND evidence_type = %s
              AND record_status = 'active'
            ORDER BY COALESCE(observed_at, collected_at, created_at) DESC, evidence_key DESC
            LIMIT 1
            """,
            (
                payload['system_id64'],
                payload['source_name'],
                payload['subject_type'],
                payload['subject_id'],
                payload['evidence_type'],
            ),
        )
        active = cur.fetchone()
        if active is not None:
            active_value = dict(active.get('value_json') or {})
            if (
                active_value == payload['value']
                and (active.get('summary') or None) == payload['summary']
                and str(active.get('confidence')) == str(payload['confidence'])
                and str(active.get('origin')) == str(payload['origin'])
                and str(active.get('freshness_status')) == str(payload['freshness_status'])
            ):
                return 'deduped'

        cur.execute(
            """
            SELECT *
            FROM evidence_records
            WHERE evidence_key = %s
            """,
            (payload['evidence_key'],),
        )
        existing = cur.fetchone()
        if existing is not None:
            return 'deduped'

        cur.execute(
            """
            WITH updated AS (
                UPDATE evidence_records
                   SET record_status = 'superseded',
                       freshness_status = 'superseded',
                       updated_at = now(),
                       metadata_json = COALESCE(metadata_json, '{}'::jsonb) || jsonb_build_object(
                           'superseded_by',
                           %s::text,
                           'superseded_at',
                           to_jsonb(now())
                       )
                 WHERE system_id64 = %s
                   AND subject_type = %s
                   AND subject_id IS NOT DISTINCT FROM %s
                   AND evidence_type = %s
                   AND record_status = 'active'
                RETURNING 1
            )
            SELECT COUNT(*)::int AS superseded_count FROM updated
            """,
            (
                payload['evidence_key'],
                payload['system_id64'],
                payload['subject_type'],
                payload['subject_id'],
                payload['evidence_type'],
            ),
        )
        superseded = cur.fetchone()
        superseded_count = int((superseded or {}).get('superseded_count') or 0)
        metadata = dict(payload['metadata'])
        metadata['lifecycle'] = {'superseded_record_count': superseded_count}
        cur.execute(
            """
            INSERT INTO evidence_records (
                evidence_key,
                system_id64,
                source_name,
                origin,
                subject_type,
                subject_id,
                evidence_type,
                record_status,
                freshness_status,
                confidence,
                summary,
                source_record_id,
                source_run_key,
                observed_at,
                collected_at,
                expires_at,
                value_json,
                provenance_json,
                tags_json,
                metadata_json
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s::timestamptz, %s::timestamptz, %s::timestamptz,
                %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb
            )
            """,
            (
                payload['evidence_key'],
                payload['system_id64'],
                payload['source_name'],
                payload['origin'],
                payload['subject_type'],
                payload['subject_id'],
                payload['evidence_type'],
                payload['record_status'],
                payload['freshness_status'],
                payload['confidence'],
                payload['summary'],
                payload['source_record_id'],
                payload['source_run_key'],
                payload['observed_at'],
                payload['collected_at'],
                payload['expires_at'],
                _json_dumps(payload['value']),
                _json_dumps(payload['provenance']),
                _json_dumps(payload['tags']),
                _json_dumps(metadata),
            ),
        )
    return 'created'


def summarize(rows) -> Counter:
    counts: Counter = Counter()
    for row in rows:
        counts[f'status:{row.association_status}'] += 1
        counts[f'confidence:{row.association_confidence}'] += 1
        counts[f'lane:{row.lane}'] += 1
        counts[f'source:{row.association_source}'] += 1
    return counts


def main() -> int:
    args = parse_args()
    if not args.dsn:
        print('DATABASE_URL or --dsn is required.', file=sys.stderr)
        return 2
    dry_run = args.dry_run or not args.apply

    with psycopg.connect(args.dsn) as conn:
        system_ids = fetch_system_ids(conn, system_id64=args.system_id64, limit=args.limit)
        total_counts: Counter = Counter()
        total_rows = 0
        evidence_counts: Counter = Counter()

        for system_id64 in system_ids:
            bodies, stations, existing = fetch_system_payload(conn, system_id64)
            rows = build_station_body_link_rows(
                stations,
                bodies,
                existing,
                no_overwrite_confirmed=not args.overwrite_confirmed,
            )
            total_counts.update(summarize(rows))
            total_rows += len(rows)
            if not dry_run:
                upsert_links(conn, rows, overwrite_confirmed=args.overwrite_confirmed)
                evidence_counts[promote_station_set_evidence(conn, system_id64)] += 1

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    mode = 'dry-run' if dry_run else 'apply'
    print(f'station body link backfill {mode}: systems={len(system_ids)} rows={total_rows}')
    for key, count in sorted(total_counts.items()):
        print(f'  {key}={count}')
    if evidence_counts:
        for key, count in sorted(evidence_counts.items()):
            print(f'  evidence:{key}={count}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
