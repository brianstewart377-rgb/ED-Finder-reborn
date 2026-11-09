#!/usr/bin/env python3
"""Staging-only DB loader for offline enrichment snapshots.

This command is explicitly opt-in. By default it emits the same deterministic
dry-run report as the offline snapshot loader. With ``--write-staging`` and a
caller-supplied ``--dsn`` it writes only to the enrichment warehouse tables:
source runs, source files, raw records, and EDSM station/body/ring staging rows.

It never calls EDSM or other APIs, never invokes container tooling, and never
writes canonical ED-Finder tables.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from enrichment_snapshot_loader import (
    apply_source_observability_metadata,
    build_snapshot_load_report,
    build_station_snapshot_source_context,
    iter_station_snapshot_load_entries,
)
from enrichment_staging import classify_source_field, json_safe_value, normalise_source_adapter
from enrichment_warehouse import (
    WAREHOUSE_BASE_TABLES,
    WAREHOUSE_BODY_RING_WRITE_TABLES,
    WAREHOUSE_STATION_WRITE_TABLES,
    warehouse_write_tables_for_source,
)
from enrichment_warehouse_repository import (
    EnrichmentWarehouseRepository,
    REQUIRED_SCHEMA_COLUMNS as REPOSITORY_REQUIRED_SCHEMA_COLUMNS,
    build_report_from_staged_rows as repository_build_report_from_staged_rows,
    fetch_body_reconciliation_rows as repository_fetch_body_reconciliation_rows,
    fetch_ring_reconciliation_rows as repository_fetch_ring_reconciliation_rows,
    fetch_station_reconciliation_rows as repository_fetch_station_reconciliation_rows,
    upsert_raw_record as repository_upsert_raw_record,
    upsert_source_file as repository_upsert_source_file,
    upsert_source_run as repository_upsert_source_run,
    upsert_staging_body as repository_upsert_staging_body,
    upsert_staging_body_ring as repository_upsert_staging_body_ring,
    upsert_staging_station as repository_upsert_staging_station,
)


BASE_TARGET_TABLES = WAREHOUSE_BASE_TABLES
STATION_TARGET_TABLES = WAREHOUSE_STATION_WRITE_TABLES
BODY_RING_TARGET_TABLES = WAREHOUSE_BODY_RING_WRITE_TABLES
TARGET_TABLES = STATION_TARGET_TABLES
SUPPORTED_SOURCES = {'edsm_nightly_stations', 'edsm_nightly_bodies'}
DEFAULT_STREAMING_WRITE_BATCH_SIZE = 500
MAX_TRACKED_UNIQUE_TIMESTAMPS = 10_000
PREFLIGHT_SCHEMA_VERSION = 'enrichment_staging_schema_preflight/v1'
STAGED_ROWS_REPORT_SCHEMA_VERSION = 'enrichment_staged_rows_summary/v1'
RECONCILIATION_REPORT_SCHEMA_VERSION = 'enrichment_staging_reconciliation/v1'

REQUIRED_SCHEMA_COLUMNS = REPOSITORY_REQUIRED_SCHEMA_COLUMNS


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Load a local offline EDSM station or body snapshot into enrichment warehouse '
            'staging tables only. Default mode is dry-run/no-write.'
        ),
    )
    parser.add_argument('--source-file', default=None, help='Local .json or .json.gz EDSM snapshot file.')
    parser.add_argument(
        '--source',
        default=None,
        help='Offline source adapter. Supported: edsm_nightly_stations, edsm_nightly_bodies.',
    )
    parser.add_argument('--limit', type=int, default=None, help='Maximum local records to inspect.')
    parser.add_argument(
        '--batch-size',
        type=int,
        default=DEFAULT_STREAMING_WRITE_BATCH_SIZE,
        help='Maximum source records per station streaming write batch. Applies to --write-staging station loads.',
    )
    parser.add_argument('--json', action='store_true', help='Emit JSON. Output is always JSON.')
    parser.add_argument('--dry-run', action='store_true', default=True, help='Dry-run/no-write mode. This is the default.')
    parser.add_argument(
        '--write-staging',
        action='store_true',
        help='Opt in to DB writes to enrichment source/raw and source-specific staging tables only.',
    )
    parser.add_argument('--dsn', default=None, help='Non-production Postgres DSN. Required with --write-staging.')
    parser.add_argument(
        '--confirm-staging-db',
        action='store_true',
        help='Required with --write-staging to acknowledge this DSN is a non-production enrichment staging DB.',
    )
    parser.add_argument(
        '--check-staging-schema',
        action='store_true',
        help='Read-only preflight: verify required enrichment warehouse tables/columns exist, then exit.',
    )
    parser.add_argument(
        '--report-staged-run',
        action='store_true',
        help='Read-only mode: summarize already staged warehouse rows for --source-run-key.',
    )
    parser.add_argument(
        '--report-reconciliation',
        action='store_true',
        help='Read-only mode: compare staged evidence with canonical tables and emit candidate changes.',
    )
    parser.add_argument('--source-run-key', default=None, help='Optional source run key for read-only report modes.')
    parser.add_argument('--source-file-key', default=None, help='Optional source file key filter for read-only report modes.')
    parser.add_argument('--apply', action='store_true', help='Unsupported. Canonical apply mode is not implemented.')
    parser.add_argument('--write', action='store_true', help='Unsupported. Use --write-staging for warehouse staging only.')
    parser.add_argument('--commit', action='store_true', help='Unsupported. Canonical apply mode is not implemented.')
    args = parser.parse_args(argv)

    if args.apply or args.write or args.commit:
        parser.error('canonical apply/write flags are not available; use --write-staging for warehouse staging only')
    if args.limit is not None and args.limit < 0:
        parser.error('--limit must be >= 0')
    if args.batch_size < 1:
        parser.error('--batch-size must be >= 1')
    if args.write_staging and not args.dsn:
        parser.error('--write-staging requires an explicit non-production --dsn')
    if args.write_staging and not args.confirm_staging_db:
        parser.error('--write-staging requires --confirm-staging-db')
    if args.check_staging_schema and not args.dsn:
        parser.error('--check-staging-schema requires --dsn')
    if args.report_staged_run and not args.dsn:
        parser.error('--report-staged-run requires --dsn')
    if args.report_reconciliation and not args.dsn:
        parser.error('--report-reconciliation requires --dsn')
    if args.report_staged_run and not args.source_run_key:
        parser.error('--report-staged-run requires --source-run-key')
    read_only_modes = [args.check_staging_schema, args.report_staged_run, args.report_reconciliation]
    if args.dsn and not (args.write_staging or any(read_only_modes)):
        parser.error(
            '--dsn is ignored in dry-run mode; pass --write-staging, '
            '--check-staging-schema, --report-staged-run, or --report-reconciliation'
        )
    if args.write_staging and any(read_only_modes):
        parser.error('--write-staging cannot be combined with read-only preflight/report modes')
    if sum(1 for enabled in read_only_modes if enabled) > 1:
        parser.error('read-only report modes cannot be combined')
    if not any(read_only_modes):
        if not args.source_file:
            parser.error('--source-file is required for snapshot load dry-run/write modes')
        if not args.source:
            parser.error('--source is required for snapshot load dry-run/write modes')

    args.dry_run = not args.write_staging
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.check_staging_schema:
            with connect_staging_db(args.dsn) as conn:
                report = check_staging_schema(conn, source=args.source)
            print(json_dumps_report(report))
            return 0 if report['ok'] else 2
        if args.report_staged_run:
            with connect_staging_db(args.dsn) as conn:
                report = build_staged_rows_summary_report(
                    conn,
                    source_run_key=args.source_run_key,
                    source_file_key=args.source_file_key,
                    source=args.source,
                )
            print(json_dumps_report(report))
            return 0
        if args.report_reconciliation:
            with connect_staging_db(args.dsn) as conn:
                report = build_reconciliation_report(
                    conn,
                    source_run_key=args.source_run_key,
                    source_file_key=args.source_file_key,
                    source=args.source,
                    limit=args.limit,
                )
            print(json_dumps_report(report))
            return 0
        if args.write_staging:
            with connect_staging_db(args.dsn) as conn:
                report = load_station_snapshot_to_staging_db(
                    source_file=Path(args.source_file),
                    source=args.source,
                    conn=conn,
                    limit=args.limit,
                    write_staging=True,
                    batch_size=args.batch_size,
                )
        else:
            report = build_staging_loader_report(
                source_file=Path(args.source_file),
                source=args.source,
                limit=args.limit,
                write_staging=False,
            )
    except (OSError, ValueError) as exc:
        print(f'enrichment staging DB loader failed: {exc}', file=sys.stderr)
        return 2

    print(json_dumps_report(report))
    return 0


def connect_staging_db(dsn: str):
    """Connect lazily so dry-runs and tests do not import or touch Postgres."""
    import psycopg  # noqa: PLC0415

    return psycopg.connect(dsn)


def target_tables_for_source(source: str | None) -> tuple[str, ...]:
    normalised_source = normalise_source_adapter(source)
    return warehouse_write_tables_for_source(normalised_source)


def check_staging_schema(conn: Any, *, source: str | None = None) -> dict[str, Any]:
    """Read-only preflight for required enrichment warehouse tables/columns."""
    return EnrichmentWarehouseRepository(conn).check_schema(source=source)


def build_staged_rows_summary_report(
    conn: Any,
    *,
    source_run_key: str,
    source_file_key: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Read-only deterministic summary from already staged warehouse rows."""
    return EnrichmentWarehouseRepository(conn).build_staged_run_report(
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        source=source,
    )


def build_reconciliation_report(
    conn: Any,
    *,
    source_run_key: str | None = None,
    source_file_key: str | None = None,
    source: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Read-only comparison of staged warehouse evidence against canonical rows."""
    report = EnrichmentWarehouseRepository(conn).build_reconciliation_report(
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        source=source,
        limit=limit,
    )
    return json_safe_value(report)


def json_dumps_report(report: Mapping[str, Any]) -> str:
    """Dump deterministic report JSON after converting DB-native scalar values."""
    return json.dumps(json_safe_value(report), sort_keys=True, indent=2)


def fetch_station_reconciliation_rows(
    conn: Any,
    *,
    source_run_key: str | None,
    source_file_key: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    return repository_fetch_station_reconciliation_rows(
        conn,
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        limit=limit,
    )


def fetch_body_reconciliation_rows(
    conn: Any,
    *,
    source_run_key: str | None,
    source_file_key: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    return repository_fetch_body_reconciliation_rows(
        conn,
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        limit=limit,
    )


def fetch_ring_reconciliation_rows(
    conn: Any,
    *,
    source_run_key: str | None,
    source_file_key: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    return repository_fetch_ring_reconciliation_rows(
        conn,
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        limit=limit,
    )


def build_staging_loader_report(
    *,
    source_file: Path,
    source: str,
    limit: int | None = None,
    write_staging: bool = False,
) -> dict[str, Any]:
    """Build the deterministic report used by dry-run and DB write modes."""
    normalised_source = normalise_source_adapter(source)
    if normalised_source not in SUPPORTED_SOURCES:
        raise ValueError(f'unsupported offline source {source!r}; supported sources: {sorted(SUPPORTED_SOURCES)}')
    target_tables = target_tables_for_source(normalised_source)

    report = build_snapshot_load_report(
        source_file=source_file,
        source=normalised_source,
        limit=limit,
    )
    report = deepcopy(report)
    report['dry_run'] = not write_staging
    report['source_run']['dry_run'] = not write_staging
    report['summary'].update({
        'errors': 0,
        'write_mode': 'staging_only' if write_staging else 'dry_run',
        'dry_run_only': not write_staging,
        'staging_writes_enabled': bool(write_staging),
        'target_tables': list(target_tables) if write_staging else [],
        'raw_records_written': 0,
        'staging_station_rows_written': 0,
        'staging_body_rows_written': 0,
        'staging_ring_rows_written': 0,
        'canonical_writes_planned': 0,
    })
    return report


def load_station_snapshot_to_staging_db(
    *,
    source_file: Path,
    source: str,
    conn: Any | None = None,
    limit: int | None = None,
    write_staging: bool = False,
    batch_size: int = DEFAULT_STREAMING_WRITE_BATCH_SIZE,
) -> dict[str, Any]:
    """Optionally write one local station snapshot report to staging tables."""
    if batch_size < 1:
        raise ValueError('batch_size must be >= 1')
    normalised_source = normalise_source_adapter(source)
    if normalised_source not in SUPPORTED_SOURCES:
        raise ValueError(f'unsupported offline source {source!r}; supported sources: {sorted(SUPPORTED_SOURCES)}')
    if write_staging and conn is None:
        raise ValueError('write_staging requires an explicit database connection')
    if write_staging and normalised_source == 'edsm_nightly_stations':
        return stream_station_snapshot_to_staging_db(
            source_file=source_file,
            source=normalised_source,
            conn=conn,
            limit=limit,
            batch_size=batch_size,
        )
    report = build_staging_loader_report(
        source_file=source_file,
        source=normalised_source,
        limit=limit,
        write_staging=write_staging,
    )
    if not write_staging:
        return report

    preflight = check_staging_schema(conn, source=normalised_source)
    if not preflight['ok']:
        _rollback(conn)
        raise ValueError(
            'enrichment staging schema preflight failed: '
            f"missing_tables={preflight['missing_tables']} "
            f"missing_columns={preflight['missing_columns']}"
        )

    try:
        write_summary = write_station_snapshot_report(conn, report)
        _commit(conn)
    except Exception:
        _rollback(conn)
        raise
    return build_report_from_staged_rows(report, write_summary)


def stream_station_snapshot_to_staging_db(
    *,
    source_file: Path,
    source: str,
    conn: Any,
    limit: int | None = None,
    batch_size: int = DEFAULT_STREAMING_WRITE_BATCH_SIZE,
) -> dict[str, Any]:
    """Stream a station snapshot into staging rows without retaining the full plan."""
    if batch_size < 1:
        raise ValueError('batch_size must be >= 1')
    context = build_station_snapshot_source_context(source_file=source_file, source=source)
    normalised_source = context['source']
    source_run = deepcopy(context['source_run'])
    source_file_summary = deepcopy(context['source_file'])
    file_format = context['file_format']
    source_run['dry_run'] = False

    preflight = check_staging_schema(conn, source=normalised_source)
    if not preflight['ok']:
        _rollback(conn)
        raise ValueError(
            'enrichment staging schema preflight failed: '
            f"missing_tables={preflight['missing_tables']} "
            f"missing_columns={preflight['missing_columns']}"
        )

    source_run_id: int | None = None
    source_file_id: int | None = None
    batch_raw_records: list[Mapping[str, Any]] = []
    batch_station_rows: list[Mapping[str, Any]] = []
    batch_source_records = 0
    stats = _new_station_streaming_stats(file_format=file_format)

    try:
        source_run_id, source_file_id = _upsert_source_context(conn, source_run, source_file_summary)

        for entry in iter_station_snapshot_load_entries(
            source_file=source_file,
            source=normalised_source,
            source_run=source_run,
            source_file_summary=source_file_summary,
            limit=limit,
        ):
            _record_station_streaming_entry(stats, entry)
            raw_record = entry.get('raw_record')
            if raw_record is not None:
                batch_raw_records.append(raw_record)
            batch_station_rows.extend(entry['staged_rows'])
            batch_source_records += 1

            if batch_source_records >= batch_size:
                _flush_station_streaming_batch(
                    conn,
                    source_run_id=source_run_id,
                    source_file_id=source_file_id,
                    raw_records=batch_raw_records,
                    station_rows=batch_station_rows,
                    stats=stats,
                )
                _commit(conn)
                batch_raw_records = []
                batch_station_rows = []
                batch_source_records = 0

        if batch_source_records:
            _flush_station_streaming_batch(
                conn,
                source_run_id=source_run_id,
                source_file_id=source_file_id,
                raw_records=batch_raw_records,
                station_rows=batch_station_rows,
                stats=stats,
            )
            _commit(conn)

        source_summary = _station_streaming_source_summary(stats, file_format=file_format)
        apply_source_observability_metadata(
            source_run=source_run,
            source_file=source_file_summary,
            source_summary=source_summary,
        )
        final_source_run_id, final_source_file_id = _upsert_source_context(conn, source_run, source_file_summary)
        source_run_id = source_run_id or final_source_run_id
        source_file_id = source_file_id or final_source_file_id
        _commit(conn)
    except Exception:
        _rollback(conn)
        raise

    source_run['db_id'] = source_run_id
    source_file_summary['db_id'] = source_file_id
    return _build_station_streaming_write_report(
        source_run=source_run,
        source_file=source_file_summary,
        source_summary=source_summary,
        stats=stats,
        batch_size=batch_size,
        target_tables=target_tables_for_source(normalised_source),
    )


def _upsert_source_context(
    conn: Any,
    source_run: Mapping[str, Any],
    source_file: Mapping[str, Any],
) -> tuple[int, int]:
    cur = conn.cursor()
    try:
        source_run_id = upsert_source_run(cur, source_run)
        source_file_id = upsert_source_file(cur, source_run_id, source_file)
        return source_run_id, source_file_id
    finally:
        _close_cursor(cur)


def _flush_station_streaming_batch(
    conn: Any,
    *,
    source_run_id: int,
    source_file_id: int,
    raw_records: Sequence[Mapping[str, Any]],
    station_rows: Sequence[Mapping[str, Any]],
    stats: dict[str, Any],
) -> None:
    if not raw_records and not station_rows:
        return
    cur = conn.cursor()
    try:
        raw_ids_by_hash: dict[str, int] = {}
        for raw_record in raw_records:
            raw_record_id = upsert_raw_record(cur, source_run_id, source_file_id, raw_record)
            raw_ids_by_hash[str(raw_record['source_record_hash'])] = raw_record_id
            stats['raw_records_written'] += 1

        for station_row in station_rows:
            record_hash = str(station_row['source_record_hash'])
            parent_record_hash = str(
                (station_row.get('provenance') or {}).get('parent_source_record_hash')
                or ''
            )
            upsert_staging_edsm_station(
                # Keep imported EDSM evidence even if canonical reconciliation/apply never runs.
                cur,
                source_run_id,
                source_file_id,
                raw_ids_by_hash.get(record_hash) or raw_ids_by_hash.get(parent_record_hash),
                station_row,
            )
            stats['staging_station_rows_written'] += 1
            stats['evidence_records_written'] += 1
        stats['batches_written'] += 1
    finally:
        _close_cursor(cur)


def _new_station_streaming_stats(*, file_format: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'records_seen': 0,
        'raw_records': 0,
        'staged_edsm_stations': 0,
        'skipped_rows': 0,
        'warnings': 0,
        'nested_station_collections': 0,
        'nested_station_records_extracted': 0,
        'nested_station_records_skipped': 0,
        'raw_records_written': 0,
        'staging_station_rows_written': 0,
        'evidence_records_written': 0,
        'batches_written': 0,
        'unsupported_source_shapes': 0,
        'malformed_rows': 0,
        'records_with_source_updated_at': 0,
        'records_without_source_updated_at': 0,
        'earliest_source_updated_at': None,
        'latest_source_updated_at': None,
        'unique_source_updated_at_values': set(),
        'unique_source_updated_at_values_is_lower_bound': False,
        'warning_reason_distribution': Counter(),
        'skipped_row_reason_distribution': Counter(),
        'confidence_distribution': Counter(),
        'freshness_distribution': Counter(),
        'source_class_distribution': Counter(),
        'source_format': file_format.get('source_format'),
        'source_format_version': file_format.get('source_format_version'),
        'record_stream_shape': file_format.get('record_stream_shape'),
    }


def _record_station_streaming_entry(stats: dict[str, Any], entry: Mapping[str, Any]) -> None:
    stats['records_seen'] += 1
    raw_record = entry.get('raw_record')
    if raw_record is not None:
        stats['raw_records'] += 1
        _record_source_updated_at(stats, raw_record.get('source_updated_at'))

    staged_rows = entry.get('staged_rows') or ()
    skipped_rows = entry.get('skipped_rows') or ()
    warnings = entry.get('warnings') or ()
    stats['staged_edsm_stations'] += len(staged_rows)
    stats['skipped_rows'] += len(skipped_rows)
    stats['warnings'] += len(warnings)
    stats['nested_station_collections'] += int(entry.get('nested_station_collections', 0))
    stats['nested_station_records_extracted'] += int(entry.get('nested_station_records_extracted', 0))
    stats['nested_station_records_skipped'] += int(entry.get('nested_station_records_skipped', 0))

    for warning in warnings:
        reason = warning.get('reason')
        if reason is not None:
            stats['warning_reason_distribution'][str(reason)] += 1
            if reason == 'unsupported_source_shape':
                stats['unsupported_source_shapes'] += 1
    for skipped_row in skipped_rows:
        reason = skipped_row.get('reason')
        if reason is not None:
            stats['skipped_row_reason_distribution'][str(reason)] += 1
            if reason in {
                'record_is_not_object',
                'invalid_station_snapshot_record',
                'invalid_body_snapshot_record',
                'invalid_ring_snapshot_record',
                'ring_record_is_not_object',
                'nested_station_record_is_not_object',
            }:
                stats['malformed_rows'] += 1
    for row in staged_rows:
        _count_field(stats['confidence_distribution'], row.get('confidence'))
        _count_field(stats['freshness_distribution'], row.get('freshness_class'))
        _count_field(stats['source_class_distribution'], row.get('source_class'))


def _record_source_updated_at(stats: dict[str, Any], source_updated_at: Any) -> None:
    if source_updated_at is None:
        stats['records_without_source_updated_at'] += 1
        return
    timestamp = str(source_updated_at)
    stats['records_with_source_updated_at'] += 1
    earliest = stats['earliest_source_updated_at']
    latest = stats['latest_source_updated_at']
    if earliest is None or timestamp < earliest:
        stats['earliest_source_updated_at'] = timestamp
    if latest is None or timestamp > latest:
        stats['latest_source_updated_at'] = timestamp
    unique_values: set[str] = stats['unique_source_updated_at_values']
    if timestamp in unique_values or len(unique_values) < MAX_TRACKED_UNIQUE_TIMESTAMPS:
        unique_values.add(timestamp)
    else:
        stats['unique_source_updated_at_values_is_lower_bound'] = True


def _station_streaming_source_summary(
    stats: Mapping[str, Any],
    *,
    file_format: Mapping[str, Any],
) -> dict[str, Any]:
    timestamp_summary = _station_streaming_timestamp_summary(stats)
    freshness_distribution = _counter_dict(stats['freshness_distribution'])
    return {
        'source_format': file_format.get('source_format'),
        'source_format_version': file_format.get('source_format_version'),
        'record_stream_shape': file_format.get('record_stream_shape'),
        'source_timestamp_summary': timestamp_summary,
        'source_freshness_summary': {
            'freshness_distribution': freshness_distribution,
            'records_with_source_updated_at': timestamp_summary['records_with_source_updated_at'],
            'records_without_source_updated_at': timestamp_summary['records_without_source_updated_at'],
            'freshness_preserves_unknown': True,
        },
        'unsupported_source_shapes': stats['unsupported_source_shapes'],
        'malformed_rows': stats['malformed_rows'],
        'warning_reason_distribution': _counter_dict(stats['warning_reason_distribution']),
        'skipped_row_reason_distribution': _counter_dict(stats['skipped_row_reason_distribution']),
    }


def _station_streaming_timestamp_summary(stats: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'records_with_source_updated_at': stats['records_with_source_updated_at'],
        'records_without_source_updated_at': stats['records_without_source_updated_at'],
        'unique_source_updated_at_values': len(stats['unique_source_updated_at_values']),
        'unique_source_updated_at_values_is_lower_bound': bool(
            stats['unique_source_updated_at_values_is_lower_bound']
        ),
        'earliest_source_updated_at': stats['earliest_source_updated_at'],
        'latest_source_updated_at': stats['latest_source_updated_at'],
    }


def _build_station_streaming_write_report(
    *,
    source_run: Mapping[str, Any],
    source_file: Mapping[str, Any],
    source_summary: Mapping[str, Any],
    stats: Mapping[str, Any],
    batch_size: int,
    target_tables: Sequence[str],
) -> dict[str, Any]:
    summary = {
        'source_runs': 1,
        'source_files': 1,
        'raw_records': stats['raw_records'],
        'raw_records_written': stats['raw_records_written'],
        'staged_rows': stats['staged_edsm_stations'],
        'staged_edsm_stations': stats['staged_edsm_stations'],
        'planned_rows': 0,
        'skipped_rows': stats['skipped_rows'],
        'conflicts': 0,
        'warnings': stats['warnings'],
        'errors': 0,
        'records_seen': stats['records_seen'],
        'nested_station_collections': stats['nested_station_collections'],
        'nested_station_records_extracted': stats['nested_station_records_extracted'],
        'nested_station_records_skipped': stats['nested_station_records_skipped'],
        'staging_station_rows_written': stats['staging_station_rows_written'],
        'evidence_records_written': stats['evidence_records_written'],
        'staging_body_rows_written': 0,
        'staging_ring_rows_written': 0,
        'batches_written': stats['batches_written'],
        'write_batches_attempted': stats['batches_written'],
        'batch_size': batch_size,
        'write_mode': 'staging_only',
        'dry_run_only': False,
        'staging_writes_enabled': True,
        'target_tables': list(target_tables),
        'canonical_writes_planned': 0,
        'compact_write_summary': True,
        'output_records_materialized': False,
        'raw_records_materialized': 0,
        'staged_rows_materialized': 0,
        'duplicate_source_record_tracking': (
            'not_accumulated_in_streaming_write; '
            'warehouse_upsert_keys_are_idempotent'
        ),
        'idempotency_model': (
            'source_run_key/source_file_key/source_record_hash upserts make '
            'reruns idempotent; an interrupted batch may leave committed '
            'warehouse staging evidence for retry'
        ),
        'distance_to_arrival_classification': classify_source_field(
            source_run.get('source'),
            'distanceToArrival',
        ),
        'confidence_distribution': _counter_dict(stats['confidence_distribution']),
        'freshness_distribution': _counter_dict(stats['freshness_distribution']),
        'source_class_distribution': _counter_dict(stats['source_class_distribution']),
        'skipped_row_reasons': _counter_dict(stats['skipped_row_reason_distribution']),
        'warning_reasons': _counter_dict(stats['warning_reason_distribution']),
        'duplicate_source_record_hashes': None,
        'duplicate_source_records': None,
        **dict(source_summary),
    }
    return {
        'schema_version': 'enrichment_snapshot_load_plan/v1',
        'dry_run': False,
        'source_run': dict(source_run),
        'source_file': dict(source_file),
        'summary': summary,
        'raw_records_planned': [],
        'staged_rows': [],
        'planned_rows': [],
        'skipped_rows': [],
        'conflicts': [],
        'warnings': [],
        'errors': [],
        'source_record_duplicate_groups': [],
        'compact_write_summary': True,
    }


def _counter_dict(counter: Mapping[Any, int]) -> dict[str, int]:
    return {
        str(key): int(counter[key])
        for key in sorted(counter, key=str)
    }


def _count_field(counter: Counter, value: Any) -> None:
    if value is not None:
        counter[str(value)] += 1


def write_station_snapshot_report(conn: Any, report: Mapping[str, Any]) -> dict[str, Any]:
    """Write a prepared report into enrichment warehouse tables only."""
    return EnrichmentWarehouseRepository(conn).write_station_snapshot_report(report)


def write_body_ring_snapshot_report(conn: Any, report: Mapping[str, Any]) -> dict[str, Any]:
    """Write prepared body/ring source evidence into staging tables only."""
    return EnrichmentWarehouseRepository(conn).write_body_ring_snapshot_report(report)


def build_report_from_staged_rows(report: Mapping[str, Any], write_summary: Mapping[str, Any]) -> dict[str, Any]:
    """Attach staging-write identifiers and counts to a deterministic report."""
    return repository_build_report_from_staged_rows(report, write_summary)


def upsert_source_run(cur: Any, source_run: Mapping[str, Any]) -> int:
    return repository_upsert_source_run(cur, source_run)


def upsert_source_file(cur: Any, source_run_id: int, source_file: Mapping[str, Any]) -> int:
    return repository_upsert_source_file(cur, source_run_id, source_file)


def upsert_raw_record(cur: Any, source_run_id: int, source_file_id: int, raw_record: Mapping[str, Any]) -> int:
    return repository_upsert_raw_record(cur, source_run_id, source_file_id, raw_record)


def upsert_staging_edsm_station(
    cur: Any,
    source_run_id: int,
    source_file_id: int,
    raw_record_id: int | None,
    station_row: Mapping[str, Any],
) -> int:
    return repository_upsert_staging_station(
        cur,
        source_run_id,
        source_file_id,
        raw_record_id,
        station_row,
    )


def upsert_staging_edsm_body(
    cur: Any,
    source_run_id: int,
    source_file_id: int,
    raw_record_id: int | None,
    body_row: Mapping[str, Any],
) -> int:
    return repository_upsert_staging_body(
        cur,
        source_run_id,
        source_file_id,
        raw_record_id,
        body_row,
    )


def upsert_staging_body_ring(
    cur: Any,
    source_run_id: int,
    source_file_id: int,
    raw_record_id: int | None,
    ring_row: Mapping[str, Any],
) -> int:
    return repository_upsert_staging_body_ring(
        cur,
        source_run_id,
        source_file_id,
        raw_record_id,
        ring_row,
    )


def _commit(conn: Any) -> None:
    commit = getattr(conn, 'commit', None)
    if callable(commit):
        commit()


def _rollback(conn: Any) -> None:
    rollback = getattr(conn, 'rollback', None)
    if callable(rollback):
        rollback()


def _close_cursor(cur: Any) -> None:
    close = getattr(cur, 'close', None)
    if callable(close):
        close()


if __name__ == '__main__':
    raise SystemExit(main())
