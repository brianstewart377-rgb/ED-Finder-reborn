from __future__ import annotations

import json
from typing import Any

import psycopg
from shared_contracts.evidence_identity import (
    content_addressed_evidence_key as _content_addressed_evidence_key,
    datetime_to_utc_isoformat as _dt_to_str,
)


CANONICAL_EVIDENCE_SOURCE = 'canonical_app_data'
CANONICAL_EVIDENCE_TYPE_STATION_SET = 'station_set'


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(',', ':'), sort_keys=True)


def build_station_set_evidence_payload(
    conn: Any,
    system_id64: int,
    *,
    trigger_context: str,
    source_run_key: str | None = None,
) -> dict[str, Any] | None:
    cur = conn.cursor()
    try:
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
        row = _fetchone_mapping(cur)
    finally:
        close = getattr(cur, 'close', None)
        if callable(close):
            close()
    if not row:
        return None

    station_count = int(row.get('station_count') or 0)
    if station_count <= 0:
        return None
    linked_station_count = int(row.get('linked_station_count') or 0)
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
        'evidence_type': CANONICAL_EVIDENCE_TYPE_STATION_SET,
        'record_status': 'active',
        'freshness_status': 'current',
        'confidence': confidence,
        'summary': summary,
        'source_record_id': None,
        'source_run_key': source_run_key,
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
            'trigger_context': trigger_context,
            'source_run_key': source_run_key,
            'source_tables': ['stations', 'station_body_links'],
            'station_data_updated_at': observed_at,
        },
        'tags': ['canonical_promotion', 'coverage'],
        'metadata': {
            'promotion_lane': 'system_canonical_evidence/v1',
            'trigger_context': trigger_context,
            'requested_evidence_type': CANONICAL_EVIDENCE_TYPE_STATION_SET,
        },
    }
    payload['evidence_key'] = _content_addressed_evidence_key(payload)
    return payload


def promote_station_set_evidence(
    conn: Any,
    system_id64: int,
    *,
    trigger_context: str,
    source_run_key: str | None = None,
) -> str:
    payload = build_station_set_evidence_payload(
        conn,
        system_id64,
        trigger_context=trigger_context,
        source_run_key=source_run_key,
    )
    if payload is None:
        return 'missing'

    for attempt in range(2):
        cur = conn.cursor()
        try:
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
            active = _fetchone_mapping(cur)
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
            existing = _fetchone_mapping(cur)
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
            superseded = _fetchone_mapping(cur)
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
            break
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            if attempt == 1:
                raise
            continue
        finally:
            close = getattr(cur, 'close', None)
            if callable(close):
                close()
    return 'created'


def _fetchone_mapping(cur: Any) -> dict[str, Any] | None:
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    description = getattr(cur, 'description', None)
    if description:
        keys = [col[0] for col in description]
        return dict(zip(keys, row, strict=True))
    return None
