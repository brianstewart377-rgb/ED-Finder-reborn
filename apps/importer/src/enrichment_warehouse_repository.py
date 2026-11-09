"""Repository layer for enrichment warehouse SQL.

The CLI modules should orchestrate parsing/report output. This module owns the
warehouse SQL templates and calls the boundary safety checks before executing
warehouse writes or read-only reconciliation queries.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from enrichment_reconciliation import (
    body_reconciliation_candidates,
    confidence_risk_summary,
    ring_reconciliation_candidates,
    sort_candidate_rows,
    source_coverage_summary,
    station_body_association_candidates,
    station_reconciliation_candidates,
)
from enrichment_analytics import (
    build_colonisation_candidate_signals,
    build_enrichment_analytics_signals,
    build_mission_density_signals,
)
from enrichment_coverage_reports import build_warehouse_coverage_report
from enrichment_staged_reports import (
    build_report_from_staged_rows as staged_build_report_from_staged_rows,
    build_staged_run_report as staged_build_staged_run_report,
)
from enrichment_staging import normalise_source_adapter
from enrichment_warehouse_sql import (
    REQUIRED_SCHEMA_COLUMNS,
    body_reconciliation_query,
    close_cursor as _close_cursor,
    execute_staging_write as _execute_staging_write,
    fetchall_dicts as _fetchall_dicts,
    jsonb as _jsonb,
    returned_id as _returned_id,
    ring_reconciliation_query,
    schema_columns_query,
    source_coverage_query,
    station_reconciliation_query,
    target_tables_for_source as sql_target_tables_for_source,
)
from enrichment_write_plans import (
    build_body_ring_staging_write_plan,
    build_station_staging_write_plan,
)
from enrichment_warehouse import (
    EVIDENCE_RECORDS_TABLE,
    WAREHOUSE_BODY_RING_WRITE_TABLES,
    WAREHOUSE_RAW_RECORDS_TABLE,
    WAREHOUSE_SOURCE_FILES_TABLE,
    WAREHOUSE_SOURCE_RUNS_TABLE,
    WAREHOUSE_STAGING_BODIES_TABLE,
    WAREHOUSE_STAGING_BODY_RINGS_TABLE,
    WAREHOUSE_STAGING_STATIONS_TABLE,
    WAREHOUSE_STATION_WRITE_TABLES,
    assert_reconciliation_sql_is_read_only,
)


SUPPORTED_SOURCES = {'edsm_nightly_stations', 'edsm_nightly_bodies'}
PREFLIGHT_SCHEMA_VERSION = 'enrichment_staging_schema_preflight/v1'
RECONCILIATION_REPORT_SCHEMA_VERSION = 'enrichment_staging_reconciliation/v1'
DEFAULT_WRITE_BATCH_SIZE = 500
_SOURCE_RUN_LEDGER_PREFIXES = ('stage-', 'stage19')


class EnrichmentWarehouseRepository:
    """Small repository wrapper around an explicit DB connection."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def check_schema(self, *, source: str | None = None) -> dict[str, Any]:
        return check_schema(self.conn, source=source)

    def build_staged_run_report(
        self,
        *,
        source_run_key: str,
        source_file_key: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        return build_staged_run_report(
            self.conn,
            source_run_key=source_run_key,
            source_file_key=source_file_key,
            source=source,
        )

    def build_reconciliation_report(
        self,
        *,
        source_run_key: str | None = None,
        source_file_key: str | None = None,
        source: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return build_reconciliation_report(
            self.conn,
            source_run_key=source_run_key,
            source_file_key=source_file_key,
            source=source,
            limit=limit,
        )

    def write_station_snapshot_report(
        self,
        report: Mapping[str, Any],
        *,
        batch_size: int = DEFAULT_WRITE_BATCH_SIZE,
    ) -> dict[str, Any]:
        return write_station_snapshot_report(self.conn, report, batch_size=batch_size)

    def write_body_ring_snapshot_report(
        self,
        report: Mapping[str, Any],
        *,
        batch_size: int = DEFAULT_WRITE_BATCH_SIZE,
    ) -> dict[str, Any]:
        return write_body_ring_snapshot_report(self.conn, report, batch_size=batch_size)


def target_tables_for_source(source: str | None) -> tuple[str, ...]:
    return sql_target_tables_for_source(source)


def iter_batches(rows: Sequence[Mapping[str, Any]], *, batch_size: int = DEFAULT_WRITE_BATCH_SIZE) -> list[list[Mapping[str, Any]]]:
    """Split planned rows into deterministic non-empty batches."""
    size = _normalise_batch_size(batch_size)
    return [list(rows[index:index + size]) for index in range(0, len(rows), size)]


def check_schema(conn: Any, *, source: str | None = None) -> dict[str, Any]:
    target_tables = target_tables_for_source(source)
    cur = conn.cursor()
    try:
        sql, params = schema_columns_query(target_tables)
        assert_reconciliation_sql_is_read_only(sql)
        cur.execute(sql, params)
        rows = _fetchall_dicts(cur)
    finally:
        _close_cursor(cur)

    existing_columns: dict[str, set[str]] = {table: set() for table in target_tables}
    for row in rows:
        table_name = str(row.get('table_name'))
        column_name = str(row.get('column_name'))
        if table_name in existing_columns:
            existing_columns[table_name].add(column_name)

    missing_tables = [table for table, columns in existing_columns.items() if not columns]
    missing_columns = [
        {'table': table, 'column': column}
        for table, required_columns in REQUIRED_SCHEMA_COLUMNS.items()
        if table in target_tables
        for column in required_columns
        if column not in existing_columns.get(table, set())
    ]
    ok = not missing_tables and not missing_columns
    return {
        'schema_version': PREFLIGHT_SCHEMA_VERSION,
        'ok': ok,
        'dry_run': True,
        'target_tables': list(target_tables),
        'source': normalise_source_adapter(source) if source else None,
        'missing_tables': missing_tables,
        'missing_columns': missing_columns,
        'summary': {
            'expected_tables': len(target_tables),
            'existing_tables': len([columns for columns in existing_columns.values() if columns]),
            'missing_tables': len(missing_tables),
            'missing_columns': len(missing_columns),
            'errors': 0 if ok else 1,
        },
    }


def build_staged_run_report(
    conn: Any,
    *,
    source_run_key: str,
    source_file_key: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    return staged_build_staged_run_report(
        conn,
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
    if limit is not None and limit < 0:
        raise ValueError('limit must be >= 0')
    normalised_source = normalise_source_adapter(source) if source else None
    if normalised_source is not None and normalised_source not in SUPPORTED_SOURCES:
        raise ValueError(f'unsupported offline source {source!r}; supported sources: {sorted(SUPPORTED_SOURCES)}')

    include_stations = normalised_source in (None, 'edsm_nightly_stations')
    include_bodies = normalised_source in (None, 'edsm_nightly_bodies')

    station_candidates = (
        station_reconciliation_candidates(
            fetch_station_reconciliation_rows(
                conn,
                source_run_key=source_run_key,
                source_file_key=source_file_key,
                limit=limit,
            )
        )
        if include_stations
        else []
    )
    body_candidates = (
        body_reconciliation_candidates(
            fetch_body_reconciliation_rows(
                conn,
                source_run_key=source_run_key,
                source_file_key=source_file_key,
                limit=limit,
            )
        )
        if include_bodies
        else []
    )
    ring_candidates = (
        ring_reconciliation_candidates(
            fetch_ring_reconciliation_rows(
                conn,
                source_run_key=source_run_key,
                source_file_key=source_file_key,
                limit=limit,
            )
        )
        if include_bodies
        else []
    )
    source_coverage_rows = fetch_source_coverage_rows(
        conn,
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        source=normalised_source,
    )

    station_candidates = sort_candidate_rows(station_candidates)
    body_candidates = sort_candidate_rows(body_candidates)
    ring_candidates = sort_candidate_rows(ring_candidates)
    warnings = sort_candidate_rows(
        warning
        for candidate in station_candidates + body_candidates + ring_candidates
        for warning in candidate.get('warnings', [])
    )
    association_candidates = station_body_association_candidates(station_candidates, body_candidates)
    all_candidates = station_candidates + body_candidates + ring_candidates + association_candidates
    report = {
        'schema_version': RECONCILIATION_REPORT_SCHEMA_VERSION,
        'dry_run': True,
        'filters': {
            'source_run_key': source_run_key,
            'source_file_key': source_file_key,
            'source': normalised_source,
            'limit': limit,
        },
        'summary': {
            'staged_station_rows_considered': len(station_candidates),
            'staged_body_rows_considered': len(body_candidates),
            'staged_ring_rows_considered': len(ring_candidates),
            'canonical_matches_found': sum(1 for candidate in all_candidates if candidate.get('canonical')),
            'canonical_misses': sum(
                1 for candidate in all_candidates
                if candidate.get('candidate_action') == 'candidate_insert_missing_canonical'
            ),
            'candidate_station_updates': sum(
                1 for candidate in station_candidates
                if candidate.get('candidate_action') == 'candidate_update'
            ),
            'candidate_body_updates': sum(
                1 for candidate in body_candidates
                if candidate.get('candidate_action') == 'candidate_update'
            ),
            'candidate_ring_updates': sum(
                1 for candidate in ring_candidates
                if candidate.get('candidate_action') == 'candidate_update'
            ),
            'ambiguous_matches': sum(
                1 for candidate in all_candidates
                if candidate.get('candidate_action') == 'ambiguous_match'
            ),
            'insufficient_evidence': sum(
                1 for candidate in all_candidates
                if candidate.get('candidate_action') == 'insufficient_evidence'
            ),
            'warnings': len(warnings),
            'errors': 0,
            'canonical_writes_planned': 0,
            'station_body_association_candidates': len(association_candidates),
        },
        'station_candidates': station_candidates,
        'body_candidates': body_candidates,
        'ring_candidates': ring_candidates,
        'station_body_association_candidates': association_candidates,
        'source_coverage_summary': source_coverage_summary(
            station_candidates,
            body_candidates,
            ring_candidates,
            warnings,
        ),
        'warehouse_coverage_report': build_warehouse_coverage_report(
            station_candidates=station_candidates,
            body_candidates=body_candidates,
            ring_candidates=ring_candidates,
            station_body_association_candidates=association_candidates,
            source_coverage_rows=source_coverage_rows,
            warnings=warnings,
        ),
        'confidence_risk_summary': confidence_risk_summary(all_candidates),
        'warnings': warnings,
        'errors': [],
    }
    analytics_signals = build_enrichment_analytics_signals(report)
    report['analytics_signals'] = analytics_signals
    report['colonisation_signals'] = build_colonisation_candidate_signals(analytics_signals)
    report['mission_density_signals'] = build_mission_density_signals(report)
    return report


def fetch_station_reconciliation_rows(
    conn: Any,
    *,
    source_run_key: str | None,
    source_file_key: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    sql, params = station_reconciliation_query(
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        limit=limit,
    )
    return _select_rows(conn, sql, params)


def fetch_body_reconciliation_rows(
    conn: Any,
    *,
    source_run_key: str | None,
    source_file_key: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    sql, params = body_reconciliation_query(
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        limit=limit,
    )
    return _select_rows(conn, sql, params)


def fetch_ring_reconciliation_rows(
    conn: Any,
    *,
    source_run_key: str | None,
    source_file_key: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    sql, params = ring_reconciliation_query(
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        limit=limit,
    )
    return _select_rows(conn, sql, params)


def fetch_source_coverage_rows(
    conn: Any,
    *,
    source_run_key: str | None,
    source_file_key: str | None,
    source: str | None,
) -> list[dict[str, Any]]:
    sql, params = source_coverage_query(
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        source=source,
    )
    return _select_rows(conn, sql, params)


def write_station_snapshot_report(
    conn: Any,
    report: Mapping[str, Any],
    *,
    batch_size: int = DEFAULT_WRITE_BATCH_SIZE,
) -> dict[str, Any]:
    if report.get('source_run', {}).get('source') == 'edsm_nightly_bodies':
        return write_body_ring_snapshot_report(conn, report, batch_size=batch_size)

    plan = build_station_staging_write_plan(report)
    batch_size = _normalise_batch_size(batch_size)
    batches_attempted = 0
    cur = conn.cursor()
    try:
        source_run_id = upsert_source_run(cur, plan['source_run'])
        source_file_id = upsert_source_file(cur, source_run_id, plan['source_file'])

        raw_ids_by_hash: dict[str, int] = {}
        raw_write_attempts = 0
        for batch in iter_batches(plan['raw_records'], batch_size=batch_size):
            batches_attempted += 1
            for raw_record in batch:
                raw_record_id = upsert_raw_record(cur, source_run_id, source_file_id, raw_record)
                raw_ids_by_hash[str(raw_record['source_record_hash'])] = raw_record_id
                raw_write_attempts += 1

        staging_write_attempts = 0
        staging_ids_by_hash: dict[str, int] = {}
        evidence_write_attempts = 0
        for batch in iter_batches(plan['station_rows'], batch_size=batch_size):
            batches_attempted += 1
            for station_row in batch:
                record_hash = str(station_row['source_record_hash'])
                parent_record_hash = str(
                    (station_row.get('provenance') or {}).get('parent_source_record_hash')
                    or ''
                )
                staging_id = upsert_staging_station(
                    cur,
                    source_run_id,
                    source_file_id,
                    raw_ids_by_hash.get(record_hash) or raw_ids_by_hash.get(parent_record_hash),
                    station_row,
                )
                staging_ids_by_hash[record_hash] = staging_id
                staging_write_attempts += 1
                evidence_write_attempts += 1

        return {
            'source_run_id': source_run_id,
            'source_file_id': source_file_id,
            'raw_records_written': raw_write_attempts,
            'staging_station_rows_written': staging_write_attempts,
            'evidence_records_written': evidence_write_attempts,
            'write_batches_attempted': batches_attempted,
            'batch_size': batch_size,
            'raw_record_ids_by_hash': raw_ids_by_hash,
            'staging_station_ids_by_hash': staging_ids_by_hash,
            'target_tables': list(WAREHOUSE_STATION_WRITE_TABLES),
            'errors': 0,
        }
    finally:
        _close_cursor(cur)


def write_body_ring_snapshot_report(
    conn: Any,
    report: Mapping[str, Any],
    *,
    batch_size: int = DEFAULT_WRITE_BATCH_SIZE,
) -> dict[str, Any]:
    plan = build_body_ring_staging_write_plan(report)
    batch_size = _normalise_batch_size(batch_size)
    batches_attempted = 0
    cur = conn.cursor()
    try:
        source_run_id = upsert_source_run(cur, plan['source_run'])
        source_file_id = upsert_source_file(cur, source_run_id, plan['source_file'])

        raw_ids_by_hash: dict[str, int] = {}
        raw_write_attempts = 0
        for batch in iter_batches(plan['raw_records'], batch_size=batch_size):
            batches_attempted += 1
            for raw_record in batch:
                raw_record_id = upsert_raw_record(cur, source_run_id, source_file_id, raw_record)
                raw_ids_by_hash[str(raw_record['source_record_hash'])] = raw_record_id
                raw_write_attempts += 1

        body_ids_by_hash: dict[str, int] = {}
        body_write_attempts = 0
        for batch in iter_batches(plan['body_rows'], batch_size=batch_size):
            batches_attempted += 1
            for body_row in batch:
                record_hash = str(body_row['source_record_hash'])
                body_id = upsert_staging_body(
                    cur,
                    source_run_id,
                    source_file_id,
                    raw_ids_by_hash.get(record_hash),
                    body_row,
                )
                body_ids_by_hash[record_hash] = body_id
                body_write_attempts += 1

        ring_ids_by_hash: dict[str, int] = {}
        ring_write_attempts = 0
        for batch in iter_batches(plan['ring_rows'], batch_size=batch_size):
            batches_attempted += 1
            for ring_row in batch:
                body_record_hash = str(ring_row.get('raw_body_source_record_hash') or '')
                ring_hash = str(ring_row['source_record_hash'])
                ring_id = upsert_staging_body_ring(
                    cur,
                    source_run_id,
                    source_file_id,
                    raw_ids_by_hash.get(body_record_hash),
                    ring_row,
                )
                ring_ids_by_hash[ring_hash] = ring_id
                ring_write_attempts += 1

        return {
            'source_run_id': source_run_id,
            'source_file_id': source_file_id,
            'raw_records_written': raw_write_attempts,
            'staging_body_rows_written': body_write_attempts,
            'staging_ring_rows_written': ring_write_attempts,
            'write_batches_attempted': batches_attempted,
            'batch_size': batch_size,
            'raw_record_ids_by_hash': raw_ids_by_hash,
            'staging_body_ids_by_hash': body_ids_by_hash,
            'staging_ring_ids_by_hash': ring_ids_by_hash,
            'target_tables': list(WAREHOUSE_BODY_RING_WRITE_TABLES),
            'errors': 0,
        }
    finally:
        _close_cursor(cur)


def upsert_source_run(cur: Any, source_run: Mapping[str, Any]) -> int:
    sql = f"""
        INSERT INTO {WAREHOUSE_SOURCE_RUNS_TABLE} (
            source_run_key,
            source,
            adapter_name,
            adapter_version,
            source_kind,
            source_class,
            run_label,
            dry_run,
            metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (source_run_key) DO UPDATE SET
            source = EXCLUDED.source,
            adapter_name = EXCLUDED.adapter_name,
            adapter_version = EXCLUDED.adapter_version,
            source_kind = EXCLUDED.source_kind,
            source_class = EXCLUDED.source_class,
            run_label = EXCLUDED.run_label,
            dry_run = EXCLUDED.dry_run,
            metadata = EXCLUDED.metadata
        RETURNING id
        """
    _execute_staging_write(
        cur,
        sql,
        (
            source_run.get('source_run_key'),
            source_run.get('source'),
            source_run.get('adapter_name'),
            source_run.get('adapter_version'),
            source_run.get('source_kind'),
            source_run.get('source_class'),
            source_run.get('run_label'),
            bool(source_run.get('dry_run')),
            _jsonb(source_run.get('metadata', {})),
        ),
    )
    return _returned_id(cur)


def upsert_source_file(cur: Any, source_run_id: int, source_file: Mapping[str, Any]) -> int:
    sql = f"""
        INSERT INTO {WAREHOUSE_SOURCE_FILES_TABLE} (
            source_run_id,
            source_file_key,
            source_path,
            source_file_name,
            content_type,
            compression,
            file_size_bytes,
            file_sha256,
            source_updated_at,
            metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (source_run_id, source_file_key) DO UPDATE SET
            source_path = EXCLUDED.source_path,
            source_file_name = EXCLUDED.source_file_name,
            content_type = EXCLUDED.content_type,
            compression = EXCLUDED.compression,
            file_size_bytes = EXCLUDED.file_size_bytes,
            file_sha256 = EXCLUDED.file_sha256,
            source_updated_at = EXCLUDED.source_updated_at,
            metadata = EXCLUDED.metadata
        RETURNING id
        """
    _execute_staging_write(
        cur,
        sql,
        (
            source_run_id,
            source_file.get('source_file_key'),
            source_file.get('source_path'),
            source_file.get('source_file_name'),
            source_file.get('content_type', 'application/json'),
            source_file.get('compression'),
            source_file.get('file_size_bytes'),
            source_file.get('file_sha256'),
            source_file.get('source_updated_at'),
            _jsonb(source_file.get('metadata', {})),
        ),
    )
    return _returned_id(cur)


def upsert_raw_record(cur: Any, source_run_id: int, source_file_id: int, raw_record: Mapping[str, Any]) -> int:
    sql = f"""
        INSERT INTO {WAREHOUSE_RAW_RECORDS_TABLE} (
            source_run_id,
            source_file_id,
            record_index,
            source_record_key,
            source_record_hash,
            source_updated_at,
            raw_payload,
            validation_status,
            validation_warnings
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
        ON CONFLICT (source_run_id, source_file_id, source_record_hash) DO UPDATE SET
            source_record_key = COALESCE({WAREHOUSE_RAW_RECORDS_TABLE}.source_record_key, EXCLUDED.source_record_key),
            source_updated_at = EXCLUDED.source_updated_at,
            raw_payload = EXCLUDED.raw_payload,
            validation_status = EXCLUDED.validation_status,
            validation_warnings = EXCLUDED.validation_warnings
        RETURNING id
        """
    _execute_staging_write(
        cur,
        sql,
        (
            source_run_id,
            source_file_id,
            raw_record.get('record_index'),
            raw_record.get('source_record_key'),
            raw_record.get('source_record_hash'),
            raw_record.get('source_updated_at'),
            _jsonb(raw_record.get('raw_payload', {})),
            raw_record.get('validation_status', 'accepted'),
            _jsonb(raw_record.get('validation_warnings', [])),
        ),
    )
    return _returned_id(cur)


def upsert_staging_station(
    cur: Any,
    source_run_id: int,
    source_file_id: int,
    raw_record_id: int | None,
    station_row: Mapping[str, Any],
) -> int:
    sql = f"""
        INSERT INTO {WAREHOUSE_STAGING_STATIONS_TABLE} (
            source_run_id,
            source_file_id,
            raw_record_id,
            source_record_key,
            source_record_hash,
            system_id64,
            system_name,
            market_id,
            edsm_station_id,
            station_name,
            station_type,
            distance_to_arrival,
            body_name,
            services,
            economies,
            controlling_faction,
            allegiance,
            government,
            source_class,
            confidence,
            freshness_class,
            source_updated_at,
            raw_payload,
            provenance
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s,
            %s, %s::jsonb, %s::jsonb
        )
        ON CONFLICT (source_run_id, source_record_hash) DO UPDATE SET
            source_file_id = EXCLUDED.source_file_id,
            raw_record_id = EXCLUDED.raw_record_id,
            source_record_key = EXCLUDED.source_record_key,
            system_id64 = EXCLUDED.system_id64,
            system_name = EXCLUDED.system_name,
            market_id = EXCLUDED.market_id,
            edsm_station_id = EXCLUDED.edsm_station_id,
            station_name = EXCLUDED.station_name,
            station_type = EXCLUDED.station_type,
            distance_to_arrival = EXCLUDED.distance_to_arrival,
            body_name = EXCLUDED.body_name,
            services = EXCLUDED.services,
            economies = EXCLUDED.economies,
            controlling_faction = EXCLUDED.controlling_faction,
            allegiance = EXCLUDED.allegiance,
            government = EXCLUDED.government,
            source_class = EXCLUDED.source_class,
            confidence = EXCLUDED.confidence,
            freshness_class = EXCLUDED.freshness_class,
            source_updated_at = EXCLUDED.source_updated_at,
            raw_payload = EXCLUDED.raw_payload,
            provenance = EXCLUDED.provenance
        RETURNING id
        """
    _execute_staging_write(
        cur,
        sql,
        (
            source_run_id,
            source_file_id,
            raw_record_id,
            station_row.get('source_record_key'),
            station_row.get('source_record_hash'),
            station_row.get('system_id64'),
            station_row.get('system_name'),
            station_row.get('market_id'),
            station_row.get('edsm_station_id'),
            station_row.get('station_name'),
            station_row.get('station_type'),
            station_row.get('distance_to_arrival'),
            station_row.get('body_name'),
            _jsonb(station_row.get('services', [])),
            _jsonb(station_row.get('economies', [])),
            station_row.get('controlling_faction'),
            station_row.get('allegiance'),
            station_row.get('government'),
            station_row.get('source_class'),
            station_row.get('confidence'),
            station_row.get('freshness_class'),
            station_row.get('source_updated_at'),
            _jsonb(station_row.get('raw_payload', {})),
            _jsonb(station_row.get('provenance', {})),
        ),
    )
    staging_station_id = _returned_id(cur)
    upsert_station_evidence_record(
        cur,
        staging_station_id=staging_station_id,
        source_run_id=source_run_id,
        source_file_id=source_file_id,
        raw_record_id=raw_record_id,
        station_row=station_row,
        evidence_source_run_key=_evidence_source_run_key_for_station_row(station_row),
    )
    return staging_station_id


def upsert_station_evidence_record(
    cur: Any,
    *,
    staging_station_id: int,
    source_run_id: int,
    source_file_id: int,
    raw_record_id: int | None,
    station_row: Mapping[str, Any],
    evidence_source_run_key: str | None = None,
) -> int:
    market_id = station_row.get('market_id')
    edsm_station_id = station_row.get('edsm_station_id')
    subject_id = (
        str(market_id)
        if market_id is not None
        else str(edsm_station_id)
        if edsm_station_id is not None
        else f'staging:{staging_station_id}'
    )
    source_record_hash = str(station_row.get('source_record_hash') or staging_station_id)
    evidence_key = f'edsm-station:{source_record_hash}'
    source_record_id = (
        str(station_row.get('source_record_key'))
        if station_row.get('source_record_key') is not None
        else source_record_hash
    )
    station_name = station_row.get('station_name') or subject_id
    system_name = station_row.get('system_name') or f"system {station_row.get('system_id64')}"
    summary = f'EDSM station snapshot for {station_name} in {system_name}'
    freshness_status = 'current' if station_row.get('source_updated_at') else 'unknown'
    value = {
        'station_name': station_row.get('station_name'),
        'station_type': station_row.get('station_type'),
        'system_name': station_row.get('system_name'),
        'system_id64': station_row.get('system_id64'),
        'market_id': market_id,
        'edsm_station_id': edsm_station_id,
        'distance_to_arrival': station_row.get('distance_to_arrival'),
        'body_name': station_row.get('body_name'),
        'services': list(station_row.get('services') or []),
        'economies': list(station_row.get('economies') or []),
        'controlling_faction': station_row.get('controlling_faction'),
        'allegiance': station_row.get('allegiance'),
        'government': station_row.get('government'),
    }
    provenance = {
        'source': 'edsm',
        'staging_source_run_id': source_run_id,
        'staging_source_file_id': source_file_id,
        'staging_raw_record_id': raw_record_id,
        'staging_station_id': staging_station_id,
        'source_record_hash': source_record_hash,
        'source_record_key': station_row.get('source_record_key'),
        'staging_provenance': dict(station_row.get('provenance') or {}),
    }
    tags = [
        'edsm',
        'station_snapshot',
        str(station_row.get('source_class') or 'unknown_source_class'),
    ]
    metadata = {
        'writer': 'enrichment_warehouse_repository',
        'source_adapter': 'edsm_nightly_stations',
        'freshness_class': station_row.get('freshness_class'),
        'confidence_class': station_row.get('confidence'),
    }
    sql = f"""
        INSERT INTO {EVIDENCE_RECORDS_TABLE} (
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
            value_json,
            provenance_json,
            tags_json,
            metadata_json
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb
        )
        ON CONFLICT (evidence_key) DO UPDATE SET
            system_id64 = EXCLUDED.system_id64,
            subject_id = EXCLUDED.subject_id,
            freshness_status = EXCLUDED.freshness_status,
            confidence = EXCLUDED.confidence,
            summary = EXCLUDED.summary,
            source_record_id = EXCLUDED.source_record_id,
            source_run_key = COALESCE(EXCLUDED.source_run_key, {EVIDENCE_RECORDS_TABLE}.source_run_key),
            observed_at = EXCLUDED.observed_at,
            value_json = EXCLUDED.value_json,
            provenance_json = EXCLUDED.provenance_json,
            tags_json = EXCLUDED.tags_json,
            metadata_json = EXCLUDED.metadata_json,
            updated_at = NOW()
        RETURNING id
        """
    _execute_staging_write(
        cur,
        sql,
        (
            evidence_key,
            station_row.get('system_id64'),
            'edsm',
            'imported',
            'station',
            subject_id,
            'station_snapshot',
            'active',
            freshness_status,
            'medium',
            summary,
            source_record_id,
            evidence_source_run_key,
            station_row.get('source_updated_at'),
            _jsonb(value),
            _jsonb(provenance),
            _jsonb(tags),
            _jsonb(metadata),
        ),
    )
    return _returned_id(cur)


def _evidence_source_run_key_for_station_row(station_row: Mapping[str, Any]) -> str | None:
    source_run_key = str(station_row.get('source_run_key') or '').strip()
    if source_run_key.startswith(_SOURCE_RUN_LEDGER_PREFIXES):
        return source_run_key
    return None


def upsert_staging_body(
    cur: Any,
    source_run_id: int,
    source_file_id: int,
    raw_record_id: int | None,
    body_row: Mapping[str, Any],
) -> int:
    sql = f"""
        INSERT INTO {WAREHOUSE_STAGING_BODIES_TABLE} (
            source_run_id,
            source_file_id,
            raw_record_id,
            source_record_key,
            source_record_hash,
            system_id64,
            system_name,
            source_body_id,
            body_name,
            body_type,
            subtype,
            distance_to_arrival,
            is_main_star,
            is_landable,
            is_terraformable,
            estimated_scan_value,
            estimated_mapping_value,
            signals,
            materials,
            source_class,
            confidence,
            freshness_class,
            source_updated_at,
            raw_payload,
            provenance
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s,
            %s, %s, %s::jsonb, %s::jsonb
        )
        ON CONFLICT (source_run_id, source_record_hash) DO UPDATE SET
            source_file_id = EXCLUDED.source_file_id,
            raw_record_id = EXCLUDED.raw_record_id,
            source_record_key = EXCLUDED.source_record_key,
            system_id64 = EXCLUDED.system_id64,
            system_name = EXCLUDED.system_name,
            source_body_id = EXCLUDED.source_body_id,
            body_name = EXCLUDED.body_name,
            body_type = EXCLUDED.body_type,
            subtype = EXCLUDED.subtype,
            distance_to_arrival = EXCLUDED.distance_to_arrival,
            is_main_star = EXCLUDED.is_main_star,
            is_landable = EXCLUDED.is_landable,
            is_terraformable = EXCLUDED.is_terraformable,
            estimated_scan_value = EXCLUDED.estimated_scan_value,
            estimated_mapping_value = EXCLUDED.estimated_mapping_value,
            signals = EXCLUDED.signals,
            materials = EXCLUDED.materials,
            source_class = EXCLUDED.source_class,
            confidence = EXCLUDED.confidence,
            freshness_class = EXCLUDED.freshness_class,
            source_updated_at = EXCLUDED.source_updated_at,
            raw_payload = EXCLUDED.raw_payload,
            provenance = EXCLUDED.provenance
        RETURNING id
        """
    _execute_staging_write(
        cur,
        sql,
        (
            source_run_id,
            source_file_id,
            raw_record_id,
            body_row.get('source_record_key'),
            body_row.get('source_record_hash'),
            body_row.get('system_id64'),
            body_row.get('system_name'),
            body_row.get('source_body_id'),
            body_row.get('body_name'),
            body_row.get('body_type'),
            body_row.get('subtype'),
            body_row.get('distance_to_arrival'),
            body_row.get('is_main_star'),
            body_row.get('is_landable'),
            body_row.get('is_terraformable'),
            body_row.get('estimated_scan_value'),
            body_row.get('estimated_mapping_value'),
            _jsonb(body_row.get('signals', {})),
            _jsonb(body_row.get('materials', {})),
            body_row.get('source_class'),
            body_row.get('confidence'),
            body_row.get('freshness_class'),
            body_row.get('source_updated_at'),
            _jsonb(body_row.get('raw_payload', {})),
            _jsonb(body_row.get('provenance', {})),
        ),
    )
    return _returned_id(cur)


def upsert_staging_body_ring(
    cur: Any,
    source_run_id: int,
    source_file_id: int,
    raw_record_id: int | None,
    ring_row: Mapping[str, Any],
) -> int:
    sql = f"""
        INSERT INTO {WAREHOUSE_STAGING_BODY_RINGS_TABLE} (
            source_run_id,
            source_file_id,
            raw_record_id,
            source_record_key,
            source_record_hash,
            system_id64,
            system_name,
            source_body_id,
            body_name,
            ring_name,
            ring_type,
            ring_class,
            mass_mt,
            inner_radius,
            outer_radius,
            association_status,
            source_class,
            confidence,
            freshness_class,
            source_updated_at,
            raw_payload,
            provenance
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb
        )
        ON CONFLICT (source_run_id, source_record_hash) DO UPDATE SET
            source_file_id = EXCLUDED.source_file_id,
            raw_record_id = EXCLUDED.raw_record_id,
            source_record_key = EXCLUDED.source_record_key,
            system_id64 = EXCLUDED.system_id64,
            system_name = EXCLUDED.system_name,
            source_body_id = EXCLUDED.source_body_id,
            body_name = EXCLUDED.body_name,
            ring_name = EXCLUDED.ring_name,
            ring_type = EXCLUDED.ring_type,
            ring_class = EXCLUDED.ring_class,
            mass_mt = EXCLUDED.mass_mt,
            inner_radius = EXCLUDED.inner_radius,
            outer_radius = EXCLUDED.outer_radius,
            association_status = EXCLUDED.association_status,
            source_class = EXCLUDED.source_class,
            confidence = EXCLUDED.confidence,
            freshness_class = EXCLUDED.freshness_class,
            source_updated_at = EXCLUDED.source_updated_at,
            raw_payload = EXCLUDED.raw_payload,
            provenance = EXCLUDED.provenance
        RETURNING id
        """
    _execute_staging_write(
        cur,
        sql,
        (
            source_run_id,
            source_file_id,
            raw_record_id,
            ring_row.get('source_record_key'),
            ring_row.get('source_record_hash'),
            ring_row.get('system_id64'),
            ring_row.get('system_name'),
            ring_row.get('source_body_id'),
            ring_row.get('body_name'),
            ring_row.get('ring_name'),
            ring_row.get('ring_type'),
            ring_row.get('ring_class'),
            ring_row.get('mass_mt'),
            ring_row.get('inner_radius'),
            ring_row.get('outer_radius'),
            ring_row.get('association_status', 'source_only'),
            ring_row.get('source_class'),
            ring_row.get('confidence'),
            ring_row.get('freshness_class'),
            ring_row.get('source_updated_at'),
            _jsonb(ring_row.get('raw_payload', {})),
            _jsonb(ring_row.get('provenance', {})),
        ),
    )
    return _returned_id(cur)


def _select_rows(conn: Any, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
    assert_reconciliation_sql_is_read_only(sql)
    cur = conn.cursor()
    try:
        cur.execute(sql, tuple(params))
        return _fetchall_dicts(cur)
    finally:
        _close_cursor(cur)


def _normalise_batch_size(batch_size: int | None) -> int:
    if batch_size is None:
        return DEFAULT_WRITE_BATCH_SIZE
    if batch_size < 1:
        raise ValueError('batch_size must be >= 1')
    return int(batch_size)



def build_report_from_staged_rows(report: Mapping[str, Any], write_summary: Mapping[str, Any]) -> dict[str, Any]:
    return staged_build_report_from_staged_rows(report, write_summary)
