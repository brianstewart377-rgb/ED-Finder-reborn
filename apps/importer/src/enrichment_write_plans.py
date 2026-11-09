"""Pure write-plan builders for enrichment warehouse staging loads."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from enrichment_staging import canonicalise_json_payload, normalise_source_adapter
from enrichment_warehouse import warehouse_write_tables_for_source


WRITE_PLAN_SCHEMA_VERSION = 'enrichment_staging_write_plan/v1'


def build_station_staging_write_plan(report: Mapping[str, Any]) -> dict[str, Any]:
    """Build a deterministic staging-only write plan for station snapshots."""
    if _report_source(report) == 'edsm_nightly_bodies':
        return build_body_ring_staging_write_plan(report)

    return _build_write_plan(
        report,
        station_rows=_rows(report.get('staged_rows', ())),
        body_rows=(),
        ring_rows=(),
    )


def build_body_ring_staging_write_plan(report: Mapping[str, Any]) -> dict[str, Any]:
    """Build a deterministic staging-only write plan for body/ring snapshots."""
    return _build_write_plan(
        report,
        station_rows=(),
        body_rows=_rows(report.get('staged_body_rows', report.get('staged_rows', ()))),
        ring_rows=_rows(report.get('staged_ring_rows', report.get('planned_rows', ()))),
    )


def build_staging_write_plan(report: Mapping[str, Any]) -> dict[str, Any]:
    """Build the appropriate staging-only write plan for a supported snapshot report."""
    if _report_source(report) == 'edsm_nightly_bodies':
        return build_body_ring_staging_write_plan(report)
    return build_station_staging_write_plan(report)


def _build_write_plan(
    report: Mapping[str, Any],
    *,
    station_rows: Sequence[Mapping[str, Any]],
    body_rows: Sequence[Mapping[str, Any]],
    ring_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    source = _report_source(report)
    raw_records = _rows(report.get('raw_records_planned', ()))
    skipped_rows = _rows(report.get('skipped_rows', ()))
    warnings = _rows(report.get('warnings', ()))
    errors = _rows(report.get('errors', ()))
    conflicts = _rows(report.get('conflicts', ()))
    records_seen = int(report.get('summary', {}).get('records_seen') or len(raw_records) + len(skipped_rows))

    summary = {
        'source': source,
        'records_seen': records_seen,
        'raw_records': len(raw_records),
        'station_staging_rows': len(station_rows),
        'body_staging_rows': len(body_rows),
        'ring_staging_rows': len(ring_rows),
        'skipped_rows': len(skipped_rows),
        'warnings': len(warnings),
        'errors': len(errors),
        'conflicts': len(conflicts),
        'canonical_writes_planned': 0,
        'target_tables': list(warehouse_write_tables_for_source(source)),
    }

    return {
        'schema_version': WRITE_PLAN_SCHEMA_VERSION,
        'dry_run': bool(report.get('dry_run', True)),
        'source': source,
        'source_run': deepcopy(dict(report.get('source_run', {}))),
        'source_file': deepcopy(dict(report.get('source_file') or {})),
        'summary': summary,
        'raw_records': raw_records,
        'station_rows': _rows(station_rows),
        'body_rows': _rows(body_rows),
        'ring_rows': _rows(ring_rows),
        'skipped_rows': skipped_rows,
        'warnings': warnings,
        'errors': errors,
        'conflicts': conflicts,
    }


def _report_source(report: Mapping[str, Any]) -> str:
    source = (
        report.get('summary', {}).get('source')
        or report.get('source_run', {}).get('source')
        or report.get('source')
    )
    return normalise_source_adapter(str(source or 'unknown_source'))


def _rows(rows: Sequence[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return []
    copied = [deepcopy(dict(row)) for row in rows if isinstance(row, Mapping)]
    return sorted(copied, key=canonicalise_json_payload)
