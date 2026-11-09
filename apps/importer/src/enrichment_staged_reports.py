"""Report shaping helpers for enrichment warehouse staged rows."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from enrichment_staging import canonicalise_json_payload, normalise_source_adapter
from enrichment_warehouse import (
    WAREHOUSE_STATION_WRITE_TABLES,
    assert_reconciliation_sql_is_read_only,
)
from enrichment_warehouse_sql import (
    close_cursor,
    fetchall_dicts,
    fetchone_dict,
    staged_counts_query,
    staged_source_rows_query,
    target_tables_for_source,
)


STAGED_ROWS_REPORT_SCHEMA_VERSION = 'enrichment_staged_rows_summary/v1'


def build_staged_run_report(
    conn: Any,
    *,
    source_run_key: str,
    source_file_key: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    cur = conn.cursor()
    try:
        source_sql, source_params = staged_source_rows_query(
            source_run_key=source_run_key,
            source_file_key=source_file_key,
        )
        assert_reconciliation_sql_is_read_only(source_sql)
        cur.execute(source_sql, source_params)
        source_rows = fetchall_dicts(cur)

        report_source = _report_source(source, source_rows)
        count_sql, count_params = staged_counts_query(
            report_source=report_source,
            source_run_key=source_run_key,
            source_file_key=source_file_key,
        )
        assert_reconciliation_sql_is_read_only(count_sql)
        cur.execute(count_sql, count_params)
        counts = fetchone_dict(cur)
    finally:
        close_cursor(cur)

    source_run = _source_run_summary(source_rows)
    source_files = _source_file_summaries(source_rows)
    return {
        'schema_version': STAGED_ROWS_REPORT_SCHEMA_VERSION,
        'dry_run': True,
        'filters': {
            'source_run_key': source_run_key,
            'source_file_key': source_file_key,
            'source': report_source if report_source != 'unknown_source' else source_run.get('source'),
        },
        'source_run': source_run,
        'source_files': source_files,
        'summary': {
            'source_runs': int(counts.get('source_runs') or 0),
            'source_files': int(counts.get('source_files') or 0),
            'raw_records': int(counts.get('raw_records') or 0),
            'staged_station_rows': int(counts.get('staged_station_rows') or 0),
            'staged_body_rows': int(counts.get('staged_body_rows') or 0),
            'staged_ring_rows': int(counts.get('staged_ring_rows') or 0),
            'warning_records': int(counts.get('warning_records') or 0),
            'error_records': int(counts.get('error_records') or 0),
            'source': report_source if report_source != 'unknown_source' else source_run.get('source'),
            'source_run_key': source_run_key,
            'source_file_key': source_file_key,
            'target_tables': list(target_tables_for_source(report_source)),
        },
        'warnings': [],
        'errors': [],
    }


def build_report_from_staged_rows(report: Mapping[str, Any], write_summary: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(report))
    result['dry_run'] = False
    result['source_run']['dry_run'] = False
    result['source_run']['db_id'] = write_summary.get('source_run_id')
    if result.get('source_file') is not None:
        result['source_file']['db_id'] = write_summary.get('source_file_id')

    raw_ids_by_hash = {
        str(key): value
        for key, value in dict(write_summary.get('raw_record_ids_by_hash', {})).items()
    }
    staging_ids_by_hash = {
        str(key): value
        for key, value in dict(write_summary.get('staging_station_ids_by_hash', {})).items()
    }
    body_ids_by_hash = {
        str(key): value
        for key, value in dict(write_summary.get('staging_body_ids_by_hash', {})).items()
    }
    ring_ids_by_hash = {
        str(key): value
        for key, value in dict(write_summary.get('staging_ring_ids_by_hash', {})).items()
    }
    for row in result.get('raw_records_planned', []):
        row['db_id'] = raw_ids_by_hash.get(str(row.get('source_record_hash')))
    for row in result.get('staged_rows', []):
        row_hash = str(row.get('source_record_hash'))
        row['db_id'] = staging_ids_by_hash.get(row_hash, body_ids_by_hash.get(row_hash))
    for row in result.get('staged_body_rows', []):
        row['db_id'] = body_ids_by_hash.get(str(row.get('source_record_hash')))
    for row in result.get('staged_ring_rows', []):
        row['db_id'] = ring_ids_by_hash.get(str(row.get('source_record_hash')))
    for row in result.get('planned_rows', []):
        row_hash = str(row.get('source_record_hash'))
        if row_hash in ring_ids_by_hash:
            row['db_id'] = ring_ids_by_hash[row_hash]

    result['summary'].update({
        'dry_run_only': False,
        'staging_writes_enabled': True,
        'write_mode': 'staging_only',
        'target_tables': list(write_summary.get('target_tables', WAREHOUSE_STATION_WRITE_TABLES)),
        'source_run_id': write_summary.get('source_run_id'),
        'source_file_id': write_summary.get('source_file_id'),
        'raw_records_written': int(write_summary.get('raw_records_written', 0)),
        'staging_station_rows_written': int(write_summary.get('staging_station_rows_written', 0)),
        'evidence_records_written': int(write_summary.get('evidence_records_written', 0)),
        'staging_body_rows_written': int(write_summary.get('staging_body_rows_written', 0)),
        'staging_ring_rows_written': int(write_summary.get('staging_ring_rows_written', 0)),
        'write_batches_attempted': int(write_summary.get('write_batches_attempted', 0)),
        'batch_size': write_summary.get('batch_size'),
        'errors': int(write_summary.get('errors', 0)),
        'canonical_writes_planned': 0,
    })
    return result


def _source_run_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    row = dict(rows[0])
    return {
        'db_id': row.get('source_run_id'),
        'source_run_key': row.get('source_run_key'),
        'source': row.get('source'),
        'adapter_name': row.get('adapter_name'),
        'adapter_version': row.get('adapter_version'),
        'source_class': row.get('source_class'),
        'dry_run': row.get('dry_run'),
    }


def _report_source(source: str | None, rows: Sequence[Mapping[str, Any]]) -> str:
    if source is not None:
        return normalise_source_adapter(source)
    if rows:
        return normalise_source_adapter(rows[0].get('source'))
    return normalise_source_adapter(None)


def _source_file_summaries(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get('source_file_id') is None:
            continue
        key = str(row.get('source_file_key'))
        files[key] = {
            'db_id': row.get('source_file_id'),
            'source_file_key': row.get('source_file_key'),
            'source_path': row.get('source_path'),
            'source_file_name': row.get('source_file_name'),
            'file_sha256': row.get('file_sha256'),
            'file_size_bytes': row.get('file_size_bytes'),
            'compression': row.get('compression'),
        }
    return sorted(files.values(), key=canonicalise_json_payload)
