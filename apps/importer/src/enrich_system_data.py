#!/usr/bin/env python3
"""Coordinated trusted station and ring enrichment backfill.

Default mode is dry-run. Every database write is gated by an explicit apply
flag, and dirty marking is a separate explicit write. The script is deliberately
batch-safe: callers can scope by one system, cap work with --limit, and resume
with the idempotent upserts plus an optional checkpoint file.
"""
from __future__ import annotations

import argparse
import gzip
import inspect
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from api_source_resolver import add_api_source_to_path


API_SRC = add_api_source_to_path(__file__, required_paths=('station_body_resolver.py',))

import edsm_station_enrichment_probe as edsm_probe  # noqa: E402
from body_ring_enrichment_plan import TRUSTED_RING_ASSOCIATION_STATUS  # noqa: E402
from dirty_flags import mark_systems_rating_dirty  # noqa: E402
from ring_facts import normalise_ring_payload, ring_rows_for_body  # noqa: E402


DEFAULT_EDSM_RATE_LIMIT_SECONDS = edsm_probe.DEFAULT_HTTP_REQUEST_DELAY_SECONDS
DEFAULT_SPANSH_SOURCE = 'spansh_dump'
INT32_MIN = -2147483648
INT32_MAX = 2147483647
TRUSTED_RING_SOURCES = {
    'edsm': edsm_probe.TRUSTED_EDSM_SOURCE,
    'spansh': DEFAULT_SPANSH_SOURCE,
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Dry-run/apply trusted station distance/body and body ring enrichment in safe batches.',
    )
    parser.add_argument('--dsn', default=os.environ.get('DATABASE_URL'), help='Postgres DSN. Defaults to DATABASE_URL.')
    parser.add_argument('--dry-run', action='store_true', help='Force dry-run mode. This is the default without apply flags.')
    parser.add_argument('--system-id64', type=int, default=None, help='Restrict work to one system address/id64.')
    parser.add_argument('--system-name', default=None, help='Restrict work to one system name.')
    parser.add_argument('--limit', type=int, default=None, help='Maximum systems to process.')
    parser.add_argument('--source', choices=('edsm', 'spansh', 'local'), default='edsm', help='Evidence source to read.')
    parser.add_argument('--stations', action='store_true', help='Plan/apply trusted station metadata and exact links.')
    parser.add_argument('--rings', action='store_true', help='Plan/apply trusted body ring rows.')
    parser.add_argument('--apply-station-metadata', action='store_true', help='Write trusted station metadata/provenance only.')
    parser.add_argument('--apply-confirmed-links', action='store_true', help='Write exact confirmed station_body_links only.')
    parser.add_argument('--apply-rings', action='store_true', help='Write trusted body_rings rows and ringed scan facts only.')
    parser.add_argument('--mark-dirty', action='store_true', help='Mark systems rating_dirty after applied station/ring fact changes.')
    parser.add_argument('--json', action='store_true', help='Emit machine-readable JSON.')
    parser.add_argument('--checkpoint-file', default=None, help='Optional JSON checkpoint storing processed system ids.')
    parser.add_argument(
        '--checkpoint-read-only',
        action='store_true',
        help='Use --checkpoint-file as a skip list without adding systems to it during this run.',
    )
    parser.add_argument('--spansh-file', default=None, help='Spansh galaxy/system dump to scan when --source spansh --rings is used.')
    parser.add_argument('--timeout', type=float, default=edsm_probe.DEFAULT_TIMEOUT_SECONDS, help='EDSM request timeout.')
    parser.add_argument('--edsm-retries', type=int, default=edsm_probe.DEFAULT_HTTP_RETRIES, help='EDSM retry attempts after the initial request.')
    parser.add_argument(
        '--edsm-retry-backoff-seconds',
        type=float,
        default=edsm_probe.DEFAULT_HTTP_RETRY_BACKOFF_SECONDS,
        help='Initial EDSM retry backoff in seconds; later retries use exponential backoff.',
    )
    parser.add_argument(
        '--edsm-429-backoff-seconds',
        type=float,
        default=edsm_probe.DEFAULT_HTTP_429_BACKOFF_SECONDS,
        help='Initial EDSM 429 retry backoff in seconds when Retry-After is absent.',
    )
    parser.add_argument(
        '--edsm-429-backoff-multiplier',
        type=float,
        default=edsm_probe.DEFAULT_HTTP_429_BACKOFF_MULTIPLIER,
        help='Multiplier for repeated EDSM 429 retry backoff when Retry-After is absent.',
    )
    parser.add_argument(
        '--rate-limit-seconds',
        '--edsm-request-delay-seconds',
        type=float,
        default=DEFAULT_EDSM_RATE_LIMIT_SECONDS,
        help='Delay between EDSM systems and between station/body endpoint requests.',
    )
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> list[str]:
    errors: list[str] = []
    applying_station = args.apply_station_metadata or args.apply_confirmed_links
    applying_fact = applying_station or args.apply_rings
    applying = applying_fact or args.mark_dirty

    if not args.stations and not args.rings:
        errors.append('Select at least one enrichment area: --stations and/or --rings.')
    if args.dry_run and applying:
        errors.append('--dry-run cannot be combined with apply or --mark-dirty flags.')
    if applying and args.system_id64 is None and args.system_name is None and args.limit is None:
        errors.append('Unscoped writes require --limit, --system-id64, or --system-name.')
    if applying_station and not args.stations:
        errors.append('Station apply flags require --stations.')
    if args.apply_rings and not args.rings:
        errors.append('--apply-rings requires --rings.')
    if applying_fact and not args.mark_dirty:
        errors.append('Apply flags require --mark-dirty so affected systems are queued for deferred rebuild/cache work.')
    if args.mark_dirty and not applying_fact:
        errors.append('--mark-dirty only marks systems changed by an apply flag in this run.')
    if args.stations and args.source != 'edsm':
        errors.append('Station enrichment currently supports --source edsm only.')
    if args.apply_rings and args.source == 'local':
        errors.append('--source local is audit-only for rings; it cannot apply rows.')
    if args.source == 'spansh' and args.rings and not args.spansh_file:
        errors.append('--source spansh --rings requires --spansh-file.')
    if args.checkpoint_read_only and not args.checkpoint_file:
        errors.append('--checkpoint-read-only requires --checkpoint-file.')
    if args.limit is not None and args.limit < 1:
        errors.append('--limit must be greater than zero.')
    if args.timeout <= 0:
        errors.append('--timeout must be greater than zero.')
    if args.edsm_retries < 0:
        errors.append('--edsm-retries must be zero or greater.')
    if args.edsm_retry_backoff_seconds < 0:
        errors.append('--edsm-retry-backoff-seconds must be zero or greater.')
    if args.edsm_429_backoff_seconds < 0:
        errors.append('--edsm-429-backoff-seconds must be zero or greater.')
    if args.edsm_429_backoff_multiplier < 1:
        errors.append('--edsm-429-backoff-multiplier must be at least 1.')
    if args.rate_limit_seconds < 0:
        errors.append('--rate-limit-seconds must be zero or greater.')
    if not args.dsn:
        errors.append('DATABASE_URL or --dsn is required.')
    return errors


def is_dry_run(args: argparse.Namespace) -> bool:
    return not (args.apply_station_metadata or args.apply_confirmed_links or args.apply_rings or args.mark_dirty)


def run(
    args: argparse.Namespace,
    *,
    connect: Callable[[str], Any] = psycopg.connect,
    edsm_fetcher: Callable[..., dict[str, Any]] = edsm_probe.fetch_edsm_system,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    dry_run = is_dry_run(args)
    report = _new_report(args, dry_run=dry_run)
    checkpoint = _load_checkpoint(args.checkpoint_file)

    with connect(args.dsn) as conn:
        if args.stations:
            station_systems = select_station_systems(conn, args)
            for index, system in enumerate(_skip_checkpointed(station_systems, checkpoint)):
                station_report = process_station_system(
                    conn,
                    system,
                    args=args,
                    dry_run=dry_run,
                    edsm_fetcher=edsm_fetcher,
                    sleep=sleep,
                )
                _merge_station_report(report, station_report)
                if not dry_run and not args.checkpoint_read_only and not station_report.get('fetch_failed'):
                    _checkpoint_system(args.checkpoint_file, checkpoint, system)
                if index + 1 < len(station_systems) and args.rate_limit_seconds > 0:
                    sleep(args.rate_limit_seconds)

        if args.rings:
            if args.source == 'spansh':
                for system_payload in iter_spansh_system_payloads(
                    Path(args.spansh_file),
                    system_id64=args.system_id64,
                    system_name=args.system_name,
                    limit=args.limit,
                    checkpoint=checkpoint,
                ):
                    system = _system_from_source_payload(system_payload)
                    ring_report = process_ring_system_payload(
                        conn,
                        system_payload,
                        system=system,
                        source=args.source,
                        dry_run=dry_run,
                        apply_rings=args.apply_rings,
                    )
                    _merge_ring_report(report, ring_report)
                    if not dry_run and not args.checkpoint_read_only:
                        _checkpoint_system(args.checkpoint_file, checkpoint, system)
            elif args.source == 'edsm':
                ring_systems = select_ring_systems(conn, args)
                for index, system in enumerate(_skip_checkpointed(ring_systems, checkpoint)):
                    local_bodies = fetch_local_bodies(conn, system['id64'])
                    try:
                        edsm_payload = _call_edsm_fetcher(
                            edsm_fetcher,
                            system['name'],
                            args=args,
                            sleep=sleep,
                            system_id64=_read_int(system.get('id64')),
                        )
                    except Exception as exc:
                        if not edsm_probe.is_edsm_fetch_error(exc):
                            raise
                        fetch_error = _station_fetch_error_entry(system, exc)
                        _log_stderr(
                            f"Skipping EDSM ring enrichment system={system.get('name')!r} "
                            f"id64={_read_int(system.get('id64'))} reason={fetch_error['message']}"
                        )
                        _merge_ring_report(report, _ring_fetch_failed_report(fetch_error))
                        if index + 1 < len(ring_systems) and args.rate_limit_seconds > 0:
                            sleep(args.rate_limit_seconds)
                        continue
                    ring_report = process_ring_system_payload(
                        conn,
                        edsm_payload.get('bodies') or {},
                        system=system,
                        local_bodies=local_bodies,
                        source=args.source,
                        dry_run=dry_run,
                        apply_rings=args.apply_rings,
                    )
                    _merge_ring_report(report, ring_report)
                    if not dry_run and not args.checkpoint_read_only:
                        _checkpoint_system(args.checkpoint_file, checkpoint, system)
                    if index + 1 < len(ring_systems) and args.rate_limit_seconds > 0:
                        sleep(args.rate_limit_seconds)
            else:
                ring_report = audit_local_ring_state(conn, args)
                _merge_ring_report(report, ring_report)

        if args.mark_dirty and report['dirty']['system_ids']:
            marked = mark_systems_rating_dirty(conn, report['dirty']['system_ids'])
            report['dirty']['marked'] = marked
        elif report['dirty']['system_ids']:
            report['dirty']['skipped'].append({
                'reason': 'mark_dirty_not_requested',
                'system_ids': sorted(report['dirty']['system_ids']),
            })

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    _finalise_report(report)
    if args.stations:
        _log_stderr(
            'EDSM station enrichment fetch_failed='
            f"{len(report['stations']['systems_fetch_failed'])} "
            f"fetch_errors={len(report['stations']['fetch_errors'])} "
            f"rate_limit_errors={len(report['stations']['rate_limit_errors'])}"
        )
    return report


def select_station_systems(conn, args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.system_id64 is not None or args.system_name:
        return [_fetch_one_system(conn, system_id64=args.system_id64, system_name=args.system_name)]

    limit_clause = 'LIMIT %s' if args.limit is not None else ''
    checkpointed = sorted(_load_checkpoint(args.checkpoint_file))
    checkpoint_clause = 'AND s.id64 <> ALL(%s)' if checkpointed else ''
    params: list[Any] = [
        edsm_probe.TRUSTED_EDSM_SOURCE,
        edsm_probe.TRUSTED_EDSM_SOURCE,
        edsm_probe.TRUSTED_EDSM_SOURCE,
        edsm_probe.TRUSTED_EDSM_SOURCE,
    ]
    if checkpointed:
        params.append(checkpointed)
    if args.limit is not None:
        params.append(args.limit)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"""
            SELECT DISTINCT s.id64, s.name
              FROM systems s
              JOIN stations st ON st.system_id64 = s.id64
             WHERE (
                    st.distance_source IS DISTINCT FROM %s
                 OR st.station_type_source IS DISTINCT FROM %s
                 OR st.body_name_source IS DISTINCT FROM %s
                 OR (
                    st.body_name_source = %s
                    AND st.body_name IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1
                          FROM station_body_links sbl
                         WHERE sbl.station_id = st.id
                           AND sbl.association_status = 'confirmed'
                    )
                 )
               )
               {checkpoint_clause}
             ORDER BY s.name
             {limit_clause}
        """, params)
        return [dict(row) for row in cur.fetchall()]


def select_ring_systems(conn, args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.system_id64 is not None or args.system_name:
        return [_fetch_one_system(conn, system_id64=args.system_id64, system_name=args.system_name)]

    limit_clause = 'LIMIT %s' if args.limit is not None else ''
    checkpointed = sorted(_load_checkpoint(args.checkpoint_file))
    checkpoint_clause = 'AND s.id64 <> ALL(%s)' if checkpointed else ''
    params: list[Any] = []
    if checkpointed:
        params.append(checkpointed)
    if args.limit is not None:
        params.append(args.limit)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"""
            SELECT s.id64, s.name
              FROM systems s
             WHERE EXISTS (
                    SELECT 1 FROM bodies b WHERE b.system_id64 = s.id64
                  )
               AND NOT EXISTS (
                    SELECT 1 FROM body_rings br WHERE br.system_id64 = s.id64
                  )
               {checkpoint_clause}
             ORDER BY s.name
             {limit_clause}
        """, params)
        return [dict(row) for row in cur.fetchall()]


def process_station_system(
    conn,
    system: Mapping[str, Any],
    *,
    args: argparse.Namespace,
    dry_run: bool,
    edsm_fetcher: Callable[..., dict[str, Any]],
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    local = edsm_probe.fetch_local_payload(conn, system_name=system.get('name'), system_id64=_read_int(system.get('id64')))
    system_id64 = _read_int(local['system'].get('id64'))
    system_name = _clean_text(local['system'].get('name')) or _clean_text(system.get('name')) or ''
    _log_stderr(f'Fetching EDSM station enrichment system={system_name!r} id64={system_id64}')
    try:
        payload = _call_edsm_fetcher(
            edsm_fetcher,
            system_name,
            args=args,
            sleep=sleep,
            system_id64=system_id64,
        )
    except Exception as exc:
        if not edsm_probe.is_edsm_fetch_error(exc):
            raise
        fetch_error = _station_fetch_error_entry(local['system'], exc)
        _log_stderr(
            f"Skipping EDSM station enrichment system={system_name!r} "
            f'id64={system_id64} reason={fetch_error["message"]}'
        )
        return {
            'system': fetch_error['system'],
            'counts': {
                'fetch_errors': 1,
                'systems_fetch_failed': 1,
                **({'rate_limit_errors': 1} if fetch_error.get('rate_limited') else {}),
            },
            'metadata_updates_planned': [],
            'metadata_updates_applied': [],
            'confirmed_link_updates_planned': [],
            'confirmed_link_updates_applied': [],
            'conflicts': [],
            'skipped': [],
            'ignored_transient_non_slot': [],
            'unresolved': [],
            'fetch_errors': [fetch_error],
            'rate_limit_errors': [fetch_error] if fetch_error.get('rate_limited') else [],
            'systems_fetch_failed': [fetch_error['system']],
            'fetch_failed': True,
            'dirty_system_ids': [],
            'raw_report': None,
        }
    report = edsm_probe.build_enrichment_report(
        local_system=local['system'],
        local_stations=local['stations'],
        local_bodies=local['bodies'],
        existing_links=local['existing_links'],
        edsm_stations_payload=payload.get('stations') or {},
        edsm_bodies_payload=payload.get('bodies') or {},
        network_enabled=True,
    )
    dirty_ids: set[int] = set()
    if not dry_run and args.apply_station_metadata:
        applied, skipped = edsm_probe.apply_metadata_updates(conn, report)
        edsm_probe.apply_metadata_result(report, applied, skipped)
        if applied:
            dirty_ids.add(int(local['system']['id64']))
    if not dry_run and args.apply_confirmed_links:
        applied_links, skipped_links = edsm_probe.apply_confirmed_link_updates(conn, report)
        edsm_probe.apply_confirmed_links_result(report, applied_links, skipped_links)
        if applied_links:
            dirty_ids.add(int(local['system']['id64']))
    return {
        'system': report['system'],
        'counts': report['counts'],
        'metadata_updates_planned': report.get('metadata_updates_planned', []),
        'metadata_updates_applied': report.get('metadata_updates_applied', []),
        'confirmed_link_updates_planned': report.get('confirmed_link_updates_planned', []),
        'confirmed_link_updates_applied': report.get('confirmed_link_updates_applied', []),
        'conflicts': report.get('conflicts', []),
        'skipped': report.get('skipped', []),
        'ignored_transient_non_slot': report.get('ignored_transient_non_slot', []),
        'unresolved': report.get('unresolved', []),
        'fetch_errors': [],
        'rate_limit_errors': [],
        'systems_fetch_failed': [],
        'fetch_failed': False,
        'dirty_system_ids': sorted(dirty_ids),
        'raw_report': report,
    }


def process_ring_system_payload(
    conn,
    source_payload: Mapping[str, Any],
    *,
    system: Mapping[str, Any],
    source: str,
    dry_run: bool,
    apply_rings: bool,
    local_bodies: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    local_bodies = list(local_bodies) if local_bodies is not None else fetch_local_bodies(conn, int(system['id64']))
    plan = build_ring_plan(
        system=system,
        local_bodies=local_bodies,
        source_payload=source_payload,
        source=source,
    )
    applied: list[dict[str, Any]] = []
    scan_fact_applied: list[dict[str, Any]] = []
    apply_skipped: list[dict[str, Any]] = []
    scan_fact_skipped: list[dict[str, Any]] = []
    if apply_rings and not dry_run:
        applied, scan_fact_applied, scan_fact_skipped, apply_skipped = apply_ring_rows(conn, plan['rows'])
    dirty_ids = sorted({
        int(row['system_id64'])
        for row in [*applied, *scan_fact_applied]
        if _read_int(row.get('system_id64')) is not None
    })
    return {
        **plan,
        'applied': applied,
        'scan_fact_applied': scan_fact_applied,
        'scan_fact_skipped': scan_fact_skipped,
        'apply_skipped': apply_skipped,
        'dirty_system_ids': dirty_ids,
    }


def build_ring_plan(
    *,
    system: Mapping[str, Any],
    local_bodies: Sequence[Mapping[str, Any]],
    source_payload: Mapping[str, Any],
    source: str,
) -> dict[str, Any]:
    source_label = TRUSTED_RING_SOURCES[source]
    allow_body_id_match = source == 'spansh'
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    counts = Counter()

    for body_payload in _extract_source_bodies(source_payload):
        body_name = _body_name_from_payload(body_payload)
        normalised = normalise_ring_payload(body_payload, trusted_empty_means_no_rings=False)
        if not normalised.ring_array_present:
            counts['missing_ring_array_unknown'] += 1
            skipped.append({
                'body_name': body_name,
                'reason': 'missing_ring_array_unknown',
            })
            continue

        match, match_conflict = match_local_body(
            local_bodies,
            body_payload,
            allow_body_id_match=allow_body_id_match,
        )
        if match_conflict is not None:
            conflicts.append(match_conflict)
        if match is None:
            counts['unmatched_body'] += 1
            skipped.append({
                'body_name': body_name,
                'reason': 'body_not_matched_exactly',
            })
            continue

        body_rows, explicit_no_rings = ring_rows_for_body(
            body_payload,
            system_id64=int(system['id64']),
            body_id=_read_int(match.get('id')),
            body_name=_clean_text(match.get('name')),
            source=source_label,
            trusted_empty_means_no_rings=False,
        )
        if explicit_no_rings:
            conflicts.append({
                'body_name': body_name,
                'type': 'unexpected_no_ring_proof_from_bulk_source',
                'message': 'Bulk ring backfill does not write no-ring evidence from empty arrays.',
            })
        if not body_rows:
            counts['empty_ring_array_not_no_ring_proof'] += 1
            skipped.append({
                'body_id': _read_int(match.get('id')),
                'body_name': _clean_text(match.get('name')),
                'reason': 'empty_ring_array_not_no_ring_proof',
            })
            continue

        for row in body_rows:
            if not row.get('ring_name'):
                counts['missing_ring_identity'] += 1
                skipped.append({
                    'body_id': row.get('body_id'),
                    'body_name': row.get('body_name'),
                    'reason': 'missing_ring_identity',
                })
                continue
            # match_local_body() (above) only ever returns a match drawn from
            # local_bodies, which fetch_local_bodies() scoped to this exact
            # system_id64 — body_id here is therefore already a verified
            # local match, same guarantee import_spansh.py's
            # body_ring_rows_from_spansh_body() establishes via its own
            # rejected_body_ids filter. Set explicitly (2026-08-08, Emergent
            # recurrence report B1) instead of leaving it to
            # body_rings.association_status's NOT NULL DEFAULT
            # 'local_matched' to silently assert the same claim with no
            # code anyone can audit — see CLAUDE.md's Known Hazards section.
            row['association_status'] = TRUSTED_RING_ASSOCIATION_STATUS
            rows.append(row)

    counts['ring_rows_planned'] = len(rows)
    counts['bodies_with_ring_rows'] = len({(row.get('body_id'), row.get('body_name')) for row in rows})
    return {
        'system': {'id64': int(system['id64']), 'name': _clean_text(system.get('name'))},
        'source': source_label,
        'rows': rows,
        'skipped': skipped,
        'conflicts': conflicts,
        'counts': dict(sorted(counts.items())),
    }


def apply_ring_rows(
    conn,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, Any, Any, Any]] = set()
    for row in rows:
        key = (row.get('system_id64'), row.get('body_id'), row.get('ring_name'), row.get('source'))
        if key in seen_keys:
            skipped.append({**dict(row), 'reason': 'duplicate_ring_row_in_batch'})
            continue
        seen_keys.add(key)
        if None in key:
            skipped.append({**dict(row), 'reason': 'missing_ring_upsert_key'})
            continue
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                INSERT INTO body_rings (
                    system_id64, body_id, body_name,
                    ring_name, ring_type, ring_class,
                    mass_mt, inner_radius, outer_radius,
                    source, confidence, association_status, updated_at
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW()
                )
                ON CONFLICT (system_id64, body_id, ring_name, source) DO UPDATE SET
                    body_name          = COALESCE(EXCLUDED.body_name, body_rings.body_name),
                    ring_type          = COALESCE(EXCLUDED.ring_type, body_rings.ring_type),
                    ring_class         = COALESCE(EXCLUDED.ring_class, body_rings.ring_class),
                    mass_mt            = COALESCE(EXCLUDED.mass_mt, body_rings.mass_mt),
                    inner_radius       = COALESCE(EXCLUDED.inner_radius, body_rings.inner_radius),
                    outer_radius       = COALESCE(EXCLUDED.outer_radius, body_rings.outer_radius),
                    confidence         = EXCLUDED.confidence,
                    association_status = EXCLUDED.association_status,
                    updated_at         = NOW()
                WHERE body_rings.body_name IS DISTINCT FROM COALESCE(EXCLUDED.body_name, body_rings.body_name)
                   OR body_rings.ring_type IS DISTINCT FROM COALESCE(EXCLUDED.ring_type, body_rings.ring_type)
                   OR body_rings.ring_class IS DISTINCT FROM COALESCE(EXCLUDED.ring_class, body_rings.ring_class)
                   OR body_rings.mass_mt IS DISTINCT FROM COALESCE(EXCLUDED.mass_mt, body_rings.mass_mt)
                   OR body_rings.inner_radius IS DISTINCT FROM COALESCE(EXCLUDED.inner_radius, body_rings.inner_radius)
                   OR body_rings.outer_radius IS DISTINCT FROM COALESCE(EXCLUDED.outer_radius, body_rings.outer_radius)
                   OR body_rings.confidence IS DISTINCT FROM EXCLUDED.confidence
                   OR body_rings.association_status IS DISTINCT FROM EXCLUDED.association_status
                RETURNING system_id64, body_id, body_name, ring_name, source, confidence
            """, _ring_row_tuple(row))
            returned = cur.fetchone()
        if returned:
            applied.append(_dict_row(returned))

    scan_fact_applied, scan_fact_skipped = apply_ringed_scan_facts(conn, rows)
    return applied, scan_fact_applied, scan_fact_skipped, skipped


def apply_ringed_scan_facts(_conn, rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Skip scan-fact writes unless a source BodyID is available.

    body_rings.body_id stores ED-Finder bodies.id (BIGINT). body_scan_facts.body_id
    stores the journal/source BodyID (INTEGER). The coordinated ring backfill
    only plans ED-Finder body IDs, so writing them to body_scan_facts would either
    overflow the current schema or corrupt that identity.

    _scan_fact_skip_reason() has exactly two outcomes and both are skips —
    there is currently no code path in this function that can populate
    `applied`; it always returns []. That's by design, not a bug or a
    forgotten TODO (see Emergent recurrence report B4, 2026-08-08): this
    function keeps the tuple[applied, skipped] shape so a real write path
    can be added later (e.g. if body_scan_facts.body_id is ever widened to
    BIGINT) without changing every caller's unpacking again.
    """
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    facts: dict[tuple[int, int], Mapping[str, Any]] = {}
    for row in rows:
        system_id64 = _read_int(row.get('system_id64'))
        body_id = _read_int(row.get('body_id'))
        if system_id64 is None or body_id is None:
            continue
        facts[(system_id64, body_id)] = row

    for (system_id64, body_id), row in facts.items():
        skipped.append({
            'system_id64': system_id64,
            'body_id': body_id,
            'body_name': _clean_text(row.get('body_name')),
            'reason': _scan_fact_skip_reason(body_id),
        })
    return applied, skipped


def _scan_fact_skip_reason(body_id: int) -> str:
    if body_id < INT32_MIN or body_id > INT32_MAX:
        return 'body_scan_facts_body_id_schema_mismatch'
    return 'body_scan_facts_source_body_id_unavailable'


def audit_local_ring_state(conn, args: argparse.Namespace) -> dict[str, Any]:
    where = []
    params: list[Any] = []
    if args.system_id64 is not None:
        where.append('s.id64 = %s')
        params.append(args.system_id64)
    if args.system_name:
        where.append('lower(s.name) = lower(%s)')
        params.append(args.system_name)
    limit_clause = 'LIMIT %s' if args.limit is not None else ''
    if args.limit is not None:
        params.append(args.limit)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ''
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"""
            SELECT s.id64, s.name,
                   (
                       SELECT count(*)
                         FROM bodies b
                        WHERE b.system_id64 = s.id64
                   ) AS bodies,
                   (
                       SELECT count(*)
                         FROM body_rings br
                        WHERE br.system_id64 = s.id64
                   ) AS ring_rows,
                   (
                       SELECT count(DISTINCT br.body_id)
                         FROM body_rings br
                         JOIN bodies b
                           ON b.system_id64 = br.system_id64
                          AND b.id = br.body_id
                        WHERE br.system_id64 = s.id64
                   ) AS ringed_bodies
              FROM systems s
             {where_sql}
             ORDER BY s.name
             {limit_clause}
        """, params)
        systems = [_dict_row(row) for row in cur.fetchall()]
    return {
        'system': None,
        'source': 'local',
        'rows': [],
        'applied': [],
        'scan_fact_applied': [],
        'scan_fact_skipped': [],
        'skipped': [],
        'apply_skipped': [],
        'conflicts': [],
        'counts': {
            'systems_audited': len(systems),
            'ring_rows_planned': 0,
        },
        'systems': systems,
        'dirty_system_ids': [],
    }


def fetch_local_bodies(conn, system_id64: int) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT id, system_id64, name, body_type::text AS body_type, subtype, distance_from_star
              FROM bodies
             WHERE system_id64 = %s
        """, (system_id64,))
        return [_dict_row(row) for row in cur.fetchall()]


def iter_spansh_system_payloads(
    path: Path,
    *,
    system_id64: int | None,
    system_name: str | None,
    limit: int | None,
    checkpoint: set[int] | None = None,
) -> Iterator[dict[str, Any]]:
    yielded = 0
    wanted_name = _normalise_name(system_name)
    with _open_text_or_gzip(path) as handle:
        for item in _iter_json_array(handle):
            if not isinstance(item, Mapping):
                continue
            system = _system_from_source_payload(item)
            id64 = _read_int(system.get('id64'))
            if id64 is None:
                continue
            if checkpoint and id64 in checkpoint:
                continue
            if system_id64 is not None and id64 != system_id64:
                continue
            if wanted_name and _normalise_name(system.get('name')) != wanted_name:
                continue
            yield dict(item)
            yielded += 1
            if limit is not None and yielded >= limit:
                break


def match_local_body(
    local_bodies: Sequence[Mapping[str, Any]],
    payload: Mapping[str, Any],
    *,
    allow_body_id_match: bool,
) -> tuple[Mapping[str, Any] | None, dict[str, Any] | None]:
    payload_name = _body_name_from_payload(payload)
    payload_id = _body_id_from_payload(payload)
    if allow_body_id_match and payload_id is not None:
        id_matches = [body for body in local_bodies if _read_int(body.get('id')) == payload_id]
        if len(id_matches) == 1:
            body = id_matches[0]
            if payload_name and _normalise_name(body.get('name')) != _normalise_name(payload_name):
                return None, {
                    'type': 'body_id_name_mismatch',
                    'body_id': payload_id,
                    'payload_body_name': payload_name,
                    'local_body_name': _clean_text(body.get('name')),
                    'message': 'Source body id matched one local body but the body name differed.',
                }
            return body, None
        if len(id_matches) > 1:
            return None, {
                'type': 'multiple_local_body_id_matches',
                'body_id': payload_id,
            }

    name_matches = [
        body for body in local_bodies
        if _normalise_name(body.get('name')) == _normalise_name(payload_name)
    ]
    if len(name_matches) == 1:
        return name_matches[0], None
    if len(name_matches) > 1:
        return None, {
            'type': 'multiple_local_body_name_matches',
            'payload_body_name': payload_name,
        }
    return None, None


def _fetch_one_system(conn, *, system_id64: int | None, system_name: str | None) -> dict[str, Any]:
    if system_id64 is None and system_name is None:
        raise ValueError('--system-id64 or --system-name is required for single-system lookup.')
    with conn.cursor(row_factory=dict_row) as cur:
        if system_id64 is not None:
            cur.execute("SELECT id64, name FROM systems WHERE id64 = %s", (system_id64,))
        else:
            cur.execute("SELECT id64, name FROM systems WHERE lower(name) = lower(%s) ORDER BY name LIMIT 2", (system_name,))
        rows = [dict(row) for row in cur.fetchall()]
    if not rows:
        raise LookupError('Local system not found.')
    if len(rows) > 1:
        raise LookupError(f'Local system name {system_name!r} matched multiple rows.')
    return rows[0]


def _call_edsm_fetcher(
    edsm_fetcher: Callable[..., dict[str, Any]],
    system_name: str,
    *,
    args: argparse.Namespace,
    sleep: Callable[[float], None],
    system_id64: int | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    supported_kwargs = {
        'timeout': args.timeout,
        'retries': getattr(args, 'edsm_retries', edsm_probe.DEFAULT_HTTP_RETRIES),
        'retry_backoff_seconds': getattr(
            args,
            'edsm_retry_backoff_seconds',
            edsm_probe.DEFAULT_HTTP_RETRY_BACKOFF_SECONDS,
        ),
        'request_delay_seconds': getattr(args, 'rate_limit_seconds', edsm_probe.DEFAULT_HTTP_REQUEST_DELAY_SECONDS),
        'rate_limit_backoff_seconds': getattr(
            args,
            'edsm_429_backoff_seconds',
            edsm_probe.DEFAULT_HTTP_429_BACKOFF_SECONDS,
        ),
        'rate_limit_backoff_multiplier': getattr(
            args,
            'edsm_429_backoff_multiplier',
            edsm_probe.DEFAULT_HTTP_429_BACKOFF_MULTIPLIER,
        ),
        'sleep': sleep,
        'logger': _log_stderr,
        'system_id64': system_id64,
    }
    for key, value in supported_kwargs.items():
        if _callable_supports_kwarg(edsm_fetcher, key):
            kwargs[key] = value
    return edsm_fetcher(system_name, **kwargs)


def _callable_supports_kwarg(func: Callable[..., Any], name: str) -> bool:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return True
    for param in signature.parameters.values():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
    return name in signature.parameters


def _station_fetch_error_entry(system: Mapping[str, Any], exc: BaseException) -> dict[str, Any]:
    system_id64 = _read_int(system.get('id64'))
    system_name = _clean_text(system.get('name'))
    cause = exc.__cause__ or exc
    status_code = _read_int(getattr(exc, 'status_code', None)) or _read_int(getattr(cause, 'code', None))
    rate_limited = bool(getattr(exc, 'rate_limited', False)) or status_code == 429
    retry_after_header = getattr(exc, 'retry_after_header', None)
    if retry_after_header is None:
        retry_after_header = edsm_probe._retry_after_header(cause)
    retry_after_seconds = getattr(exc, 'retry_after_seconds', None)
    if retry_after_seconds is None:
        retry_after_seconds = edsm_probe._retry_after_seconds(retry_after_header)
    return {
        'system': {'id64': system_id64, 'name': system_name},
        'system_id64': system_id64,
        'system_name': system_name,
        'reason': 'edsm_fetch_failed',
        'error_type': cause.__class__.__name__,
        'message': str(exc),
        'endpoint': getattr(exc, 'endpoint', None),
        'attempts': getattr(exc, 'attempts', None),
        'status_code': status_code,
        'rate_limited': rate_limited,
        'retry_after_seconds': retry_after_seconds,
        'retry_after_header': retry_after_header,
    }


def _ring_fetch_failed_report(fetch_error: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'system': None,
        'rows': [],
        'applied': [],
        'scan_fact_applied': [],
        'scan_fact_skipped': [],
        'skipped': [],
        'apply_skipped': [],
        'conflicts': [],
        'fetch_errors': [dict(fetch_error)],
        'rate_limit_errors': [dict(fetch_error)] if fetch_error.get('rate_limited') else [],
        'systems_fetch_failed': [fetch_error['system']],
        'counts': {
            'fetch_errors': 1,
            'systems_fetch_failed': 1,
            **({'rate_limit_errors': 1} if fetch_error.get('rate_limited') else {}),
        },
        'dirty_system_ids': [],
    }


def _log_stderr(message: str) -> None:
    print(message, file=sys.stderr)


def _extract_source_bodies(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    for key in ('bodies', 'Bodies', 'body', 'BodiesList'):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    return []


def _system_from_source_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    id64 = _read_int(_first_present(
        payload.get('id64'),
        payload.get('systemId64'),
        payload.get('system_id64'),
        payload.get('SystemAddress'),
    ))
    return {
        'id64': id64,
        'name': _clean_text(_first_present(
            payload.get('name'),
            payload.get('systemName'),
            payload.get('system_name'),
            payload.get('StarSystem'),
        )),
    }


def _body_id_from_payload(payload: Mapping[str, Any]) -> int | None:
    return _read_int(_first_present(
        payload.get('id'),
        payload.get('bodyId'),
        payload.get('bodyID'),
        payload.get('body_id'),
    ))


def _body_name_from_payload(payload: Mapping[str, Any]) -> str | None:
    return _clean_text(_first_present(
        payload.get('name'),
        payload.get('bodyName'),
        payload.get('body_name'),
        payload.get('BodyName'),
    ))


def _iter_json_array(handle) -> Iterator[Any]:
    start = handle.read(1)
    handle.seek(0)
    if start != '[':
        data = json.load(handle)
        if isinstance(data, list):
            yield from data
        elif isinstance(data, Mapping):
            for key in ('systems', 'Systems', 'bodies'):
                value = data.get(key)
                if isinstance(value, list):
                    yield from value
                    return
            yield data
        return
    try:
        import ijson
    except ImportError:
        data = json.load(handle)
        yield from data if isinstance(data, list) else []
        return
    yield from ijson.items(handle, 'item')


def _open_text_or_gzip(path: Path):
    if path.suffix == '.gz':
        return gzip.open(path, 'rt', encoding='utf-8')
    return path.open('r', encoding='utf-8')


def _new_report(args: argparse.Namespace, *, dry_run: bool) -> dict[str, Any]:
    return {
        'dry_run': dry_run,
        'source': args.source,
        'scope': {
            'system_id64': args.system_id64,
            'system_name': args.system_name,
            'limit': args.limit,
        },
        'stations': {
            'systems': [],
            'metadata_updates_planned': [],
            'metadata_updates_applied': [],
            'confirmed_link_updates_planned': [],
            'confirmed_link_updates_applied': [],
            'conflicts': [],
            'skipped': [],
            'ignored_transient_non_slot': [],
            'unresolved': [],
            'fetch_errors': [],
            'rate_limit_errors': [],
            'systems_fetch_failed': [],
            'counts': {},
        },
        'rings': {
            'systems': [],
            'rows_planned': [],
            'applied': [],
            'scan_fact_applied': [],
            'scan_fact_skipped': [],
            'skipped': [],
            'apply_skipped': [],
            'conflicts': [],
            'fetch_errors': [],
            'rate_limit_errors': [],
            'systems_fetch_failed': [],
            'counts': {},
        },
        'dirty': {
            'system_ids': set(),
            'marked': 0,
            'skipped': [],
        },
        'summary': {},
    }


def _merge_station_report(report: dict[str, Any], station_report: Mapping[str, Any]) -> None:
    section = report['stations']
    if not station_report.get('fetch_failed'):
        section['systems'].append(station_report['system'])
    for key in (
        'metadata_updates_planned',
        'metadata_updates_applied',
        'confirmed_link_updates_planned',
        'confirmed_link_updates_applied',
        'conflicts',
        'skipped',
        'ignored_transient_non_slot',
        'unresolved',
        'fetch_errors',
        'rate_limit_errors',
        'systems_fetch_failed',
    ):
        section[key].extend(station_report.get(key, []))
    for key, value in station_report.get('counts', {}).items():
        section['counts'][key] = section['counts'].get(key, 0) + int(value)
    report['dirty']['system_ids'].update(station_report.get('dirty_system_ids', []))


def _merge_ring_report(report: dict[str, Any], ring_report: Mapping[str, Any]) -> None:
    section = report['rings']
    if ring_report.get('system'):
        section['systems'].append(ring_report['system'])
    section['systems'].extend(ring_report.get('systems', []))
    section['rows_planned'].extend(ring_report.get('rows', []))
    for key in (
        'applied',
        'scan_fact_applied',
        'scan_fact_skipped',
        'skipped',
        'apply_skipped',
        'conflicts',
        'fetch_errors',
        'rate_limit_errors',
        'systems_fetch_failed',
    ):
        section[key].extend(ring_report.get(key, []))
    for key, value in ring_report.get('counts', {}).items():
        section['counts'][key] = section['counts'].get(key, 0) + int(value)
    report['dirty']['system_ids'].update(ring_report.get('dirty_system_ids', []))


def _finalise_report(report: dict[str, Any]) -> None:
    dirty_ids = sorted(report['dirty']['system_ids'])
    report['dirty']['system_ids'] = dirty_ids
    station_conflicts = len(report['stations']['conflicts'])
    ring_conflicts = len(report['rings']['conflicts'])
    station_fetch_errors = len(report['stations']['fetch_errors'])
    station_rate_limit_errors = len(report['stations']['rate_limit_errors'])
    station_systems_fetch_failed = len(report['stations']['systems_fetch_failed'])
    ring_fetch_errors = len(report['rings']['fetch_errors'])
    ring_rate_limit_errors = len(report['rings']['rate_limit_errors'])
    ring_systems_fetch_failed = len(report['rings']['systems_fetch_failed'])
    report['summary'] = {
        'systems_processed': len({
            *[system.get('id64') for system in report['stations']['systems']],
            *[system.get('id64') for system in report['rings']['systems']],
        } - {None}),
        'stations': {
            'planned': (
                len(report['stations']['metadata_updates_planned'])
                + len(report['stations']['confirmed_link_updates_planned'])
            ),
            'applied': (
                len(report['stations']['metadata_updates_applied'])
                + len(report['stations']['confirmed_link_updates_applied'])
            ),
            'skipped': len(report['stations']['skipped']),
            'conflicts': station_conflicts,
            'fetch_errors': station_fetch_errors,
            'rate_limit_errors': station_rate_limit_errors,
            'systems_fetch_failed': station_systems_fetch_failed,
        },
        'rings': {
            'planned': len(report['rings']['rows_planned']),
            'applied': len(report['rings']['applied']),
            'scan_fact_applied': len(report['rings']['scan_fact_applied']),
            'scan_fact_skipped': len(report['rings']['scan_fact_skipped']),
            'skipped': len(report['rings']['skipped']) + len(report['rings']['apply_skipped']),
            'conflicts': ring_conflicts,
            'fetch_errors': ring_fetch_errors,
            'rate_limit_errors': ring_rate_limit_errors,
            'systems_fetch_failed': ring_systems_fetch_failed,
        },
        'dirty_systems_planned': len(dirty_ids),
        'dirty_systems_marked': report['dirty']['marked'],
        'conflicts': station_conflicts + ring_conflicts,
        'fetch_errors': station_fetch_errors + ring_fetch_errors,
        'rate_limit_errors': station_rate_limit_errors + ring_rate_limit_errors,
        'systems_fetch_failed': station_systems_fetch_failed + ring_systems_fetch_failed,
    }


def _load_checkpoint(path: str | None) -> set[int]:
    if not path:
        return set()
    checkpoint_path = Path(path)
    if not checkpoint_path.exists():
        return set()
    try:
        payload = json.loads(checkpoint_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return set()
    return {
        int(value)
        for value in payload.get('processed_system_id64s', [])
        if _read_int(value) is not None
    }


def _checkpoint_system(path: str | None, checkpoint: set[int], system: Mapping[str, Any]) -> None:
    if not path:
        return
    id64 = _read_int(system.get('id64'))
    if id64 is None:
        return
    checkpoint.add(id64)
    checkpoint_path = Path(path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(json.dumps({
        'processed_system_id64s': sorted(checkpoint),
        'last_system_id64': id64,
    }, indent=2), encoding='utf-8')


def _skip_checkpointed(systems: Sequence[Mapping[str, Any]], checkpoint: set[int]) -> list[dict[str, Any]]:
    return [
        dict(system)
        for system in systems
        if _read_int(system.get('id64')) not in checkpoint
    ]


def _ring_row_tuple(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row.get('system_id64'),
        row.get('body_id'),
        row.get('body_name'),
        row.get('ring_name'),
        row.get('ring_type'),
        row.get('ring_class'),
        row.get('mass_mt'),
        row.get('inner_radius'),
        row.get('outer_radius'),
        row.get('source'),
        row.get('confidence'),
        row.get('association_status'),
    )


def _dict_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return dict(row)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _read_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalise_name(value: Any) -> str:
    text = _clean_text(value)
    return ' '.join(text.lower().split()) if text else ''


def _json_default(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def render_text_report(report: Mapping[str, Any]) -> str:
    lines = [
        f"enrich_system_data source={report['source']} dry_run={report['dry_run']}",
        'summary:',
    ]
    for key, value in report['summary'].items():
        lines.append(f'  {key}: {value}')
    return '\n'.join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    errors = validate_args(args)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    try:
        report = run(args)
    except Exception as exc:
        print(f'enrich_system_data failed: {exc}', file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=_json_default))
    else:
        print(render_text_report(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
