"""Stage 19T local EDSM station import MVP wrapper.

This helper accepts the existing local EDSM station snapshot shapes handled by
``enrichment_snapshot_loader``: a JSON array of station objects, a JSONL stream
of station objects, or a nested system object with a ``stations`` collection.
Useful source fields include ``id``, ``marketId``, ``name``, ``systemName``,
``systemId64``, ``type``, ``distanceToArrival``, ``services``, ``economy``,
``secondEconomy``, and ``updatedAt``.

It plans normalised source evidence and can stage rows only through an explicit
caller-supplied stager. The repository staging table currently keys
``staging_edsm_stations.source_run_id`` to ``enrichment_source_runs(id)``, so
this wrapper does not ship a default stager that would misuse the new
``source_runs`` ID. It writes a source-run artifact and completes the new
``source_runs`` ledger row. It does not open database connections, call the
network, or write canonical data.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import artifact_utils
import source_run_artifacts
from enrichment_snapshot_loader import (
    iter_station_snapshot_load_entries,
    source_file_format_metadata,
)
from enrichment_staging import (
    first_present,
    normalise_source_adapter,
    read_text,
)


SCHEMA_VERSION = 'stage_19t_edsm_station_import_mvp/v1'
IMPORTER_NAME = 'stage_19t_edsm_station_import_mvp'
IMPORTER_VERSION = 'v1'
SOURCE_ADAPTER = 'edsm_nightly_stations'
STAGING_TABLE = 'staging_edsm_stations'
SOURCE_RUN_TABLE = 'source_runs'
NOISY_TRANSIENT_SOURCE_TYPES = frozenset((
    'Drake-Class Carrier',
    'Space Construction Depot',
))
MAX_ARTIFACT_DETAIL_ROWS = 50

SAFETY_BOUNDARY = {
    'repo_only': True,
    'local_file_only': True,
    'target_tables': [SOURCE_RUN_TABLE, STAGING_TABLE],
    'canonical_writes_planned': 0,
    'station_type_mapping_writes_planned': 0,
    'production_db_connection_opened': False,
    'scheduled_import_enabled': False,
    'timer_enabled': False,
    'service_enabled': False,
    'canonical_apply_enabled': False,
}


class EdsmStationImportError(ValueError):
    """Raised for Stage 19T wrapper validation or staging failures."""


def run_edsm_station_import(
    conn: Any,
    *,
    source_file: str | Path,
    artifact_path: str | Path,
    git_commit_sha: str,
    trigger_context: str,
    source_run_key: str | None = None,
    generated_at: datetime | str | None = None,
    finished_at: datetime | None = None,
    station_stager: Callable[..., int] | None = None,
    cancel_requested: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run the local-file staging wrapper against a caller-owned connection."""
    source_path = Path(source_file)
    if not source_path.is_file():
        raise EdsmStationImportError(f'local EDSM station source file does not exist: {source_path}')

    source_input_sha256 = artifact_utils.sha256_file(source_path)
    source_uri = _source_uri(source_path)
    file_format = source_file_format_metadata(source_path)
    source_run_key = source_run_key or build_source_run_key(source_input_sha256)

    source_run_kwargs = build_source_run_kwargs(
        source_run_key=source_run_key,
        source_uri=source_uri,
        source_input_sha256=source_input_sha256,
        git_commit_sha=git_commit_sha,
        trigger_context=trigger_context,
        file_format=file_format,
    )

    def operation(source_run: Mapping[str, Any]) -> source_run_artifacts.SourceRunArtifactOutcome:
        plan: dict[str, Any] | None = None
        try:
            plan = build_edsm_station_import_plan(
                source_file=source_path,
                source_run_key=str(source_run['source_run_key']),
                source_uri=source_uri,
                source_input_sha256=source_input_sha256,
                file_format=file_format,
                limit=limit,
            )

            if cancel_requested:
                status = 'cancelled'
                error_code = 'edsm_station_import_cancelled'
                error_summary = 'EDSM station import was cancelled before staging rows.'
                rows_staged = 0
            elif plan['rows_staged'] == 0:
                status = 'rejected'
                error_code = 'edsm_station_input_rejected'
                error_summary = 'No valid EDSM station rows were available to stage.'
                rows_staged = 0
            else:
                rows_staged = run_explicit_station_stager(
                    conn,
                    source_run=source_run,
                    rows=plan['staged_rows'],
                    station_stager=station_stager,
                )
                if rows_staged != plan['rows_staged']:
                    raise EdsmStationImportError(
                        'staging row count mismatch: '
                        f'planned {plan["rows_staged"]}, wrote {rows_staged}'
                    )
                status = 'succeeded'
                error_code = None
                error_summary = None

            payload = build_edsm_station_import_artifact(
                source_run_kwargs=source_run_kwargs,
                source_uri=source_uri,
                source_input_sha256=source_input_sha256,
                file_format=file_format,
                plan=plan,
                status=status,
                rows_staged=rows_staged,
                error_code=error_code,
                error_summary=error_summary,
                generated_at=generated_at,
            )
            return source_run_artifacts.SourceRunArtifactOutcome(
                payload=payload,
                status=status,
                rows_read=plan['rows_read'],
                rows_staged=rows_staged,
                rows_rejected=plan['rows_rejected'],
                rows_skipped=plan['rows_skipped'],
                error_code=error_code,
                error_summary=error_summary,
                metadata=_completion_metadata(plan, status=status),
                finished_at=finished_at,
            )
        except Exception as exc:
            failure_plan = plan or empty_import_plan(
                source_uri=source_uri,
                source_input_sha256=source_input_sha256,
                file_format=file_format,
            )
            error_summary = _error_summary(exc)
            payload = build_edsm_station_import_artifact(
                source_run_kwargs=source_run_kwargs,
                source_uri=source_uri,
                source_input_sha256=source_input_sha256,
                file_format=file_format,
                plan=failure_plan,
                status='failed',
                rows_staged=0,
                error_code='edsm_station_import_failed',
                error_summary=error_summary,
                generated_at=generated_at,
            )
            return source_run_artifacts.SourceRunArtifactOutcome(
                payload=payload,
                status='failed',
                rows_read=failure_plan['rows_read'],
                rows_staged=0,
                rows_rejected=failure_plan['rows_rejected'],
                rows_skipped=failure_plan['rows_skipped'],
                error_code='edsm_station_import_failed',
                error_summary=error_summary,
                metadata=_completion_metadata(failure_plan, status='failed'),
                finished_at=finished_at,
            )

    return source_run_artifacts.run_source_run_artifact_flow(
        conn,
        source_run_kwargs=source_run_kwargs,
        artifact_path=artifact_path,
        operation=operation,
    )


def build_source_run_key(source_input_sha256: str) -> str:
    """Build a stable source-run key for one local file digest."""
    return f'stage-19t-edsm-stations-{source_input_sha256[:16]}'


def build_source_run_kwargs(
    *,
    source_run_key: str,
    source_uri: str,
    source_input_sha256: str,
    git_commit_sha: str,
    trigger_context: str,
    file_format: Mapping[str, Any],
) -> dict[str, Any]:
    """Return validated ``source_runs`` create kwargs for this wrapper."""
    return {
        'source_run_key': source_run_key,
        'source_name': 'edsm',
        'source_category': 'source_of_evidence',
        'domain': 'stations',
        'import_scope': 'staging_only',
        'git_commit_sha': git_commit_sha,
        'importer_name': IMPORTER_NAME,
        'importer_version': IMPORTER_VERSION,
        'trigger_context': trigger_context,
        'status': 'running',
        'source_uri': source_uri,
        'source_input_sha256': source_input_sha256,
        'safety_boundary': SAFETY_BOUNDARY,
        'metadata': {
            'stage': '19t',
            'source_adapter': SOURCE_ADAPTER,
            'input_format': dict(file_format),
        },
    }


def build_edsm_station_import_plan(
    *,
    source_file: str | Path,
    source_run_key: str,
    source_uri: str,
    source_input_sha256: str,
    file_format: Mapping[str, Any] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Parse and validate a local EDSM station file into staging-ready rows."""
    source_path = Path(source_file)
    normalised_source = normalise_source_adapter(SOURCE_ADAPTER)
    source_file_summary = {
        'source_file_key': artifact_utils.sha256_text(f'{normalised_source}:{source_input_sha256}'),
        'source_path': source_uri,
        'file_sha256': source_input_sha256,
        'metadata': dict(file_format or source_file_format_metadata(source_path)),
    }
    source_run = {'source_run_key': source_run_key}

    rows_read = 0
    raw_records: list[dict[str, Any]] = []
    staged_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for entry in iter_station_snapshot_load_entries(
        source_file=source_path,
        source=normalised_source,
        source_run=source_run,
        source_file_summary=source_file_summary,
        limit=limit,
    ):
        rows_read += 1
        raw_record = entry.get('raw_record')
        if isinstance(raw_record, Mapping):
            raw_records.append(dict(raw_record))
        staged_rows.extend(_copy_rows(entry.get('staged_rows', ())))
        warnings.extend(_copy_rows(entry.get('warnings', ())))
        for skipped in _copy_rows(entry.get('skipped_rows', ())):
            if skipped.get('reason') == 'invalid_station_snapshot_record':
                rejected_rows.append(skipped)
            else:
                skipped_rows.append(skipped)

    station_type_counts = _station_type_counts(staged_rows, rejected_rows, skipped_rows)
    noisy_counts = {
        station_type: count
        for station_type, count in station_type_counts.items()
        if station_type in NOISY_TRANSIENT_SOURCE_TYPES
    }
    warning_reason_counts = _reason_counts(warnings)
    rejection_reason_counts = _reason_counts(rejected_rows)
    skipped_reason_counts = _reason_counts(skipped_rows)

    return {
        'source_uri': source_uri,
        'source_input_sha256': source_input_sha256,
        'file_format': dict(source_file_summary['metadata']),
        'rows_read': rows_read,
        'rows_staged': len(staged_rows),
        'rows_rejected': len(rejected_rows),
        'rows_skipped': len(skipped_rows),
        'raw_records': raw_records,
        'staged_rows': staged_rows,
        'rejected_rows': rejected_rows,
        'skipped_rows': skipped_rows,
        'warnings': warnings,
        'source_station_type_counts': station_type_counts,
        'noisy_transient_station_type_counts': noisy_counts,
        'warning_reason_counts': warning_reason_counts,
        'rejection_reason_counts': rejection_reason_counts,
        'skipped_reason_counts': skipped_reason_counts,
    }


def empty_import_plan(
    *,
    source_uri: str,
    source_input_sha256: str,
    file_format: Mapping[str, Any],
) -> dict[str, Any]:
    """Return an artifact-safe zero-row plan for parse or staging failures."""
    return {
        'source_uri': source_uri,
        'source_input_sha256': source_input_sha256,
        'file_format': dict(file_format),
        'rows_read': 0,
        'rows_staged': 0,
        'rows_rejected': 0,
        'rows_skipped': 0,
        'raw_records': [],
        'staged_rows': [],
        'rejected_rows': [],
        'skipped_rows': [],
        'warnings': [],
        'source_station_type_counts': {},
        'noisy_transient_station_type_counts': {},
        'warning_reason_counts': {},
        'rejection_reason_counts': {},
        'skipped_reason_counts': {},
    }


def build_edsm_station_import_artifact(
    *,
    source_run_kwargs: Mapping[str, Any],
    source_uri: str,
    source_input_sha256: str,
    file_format: Mapping[str, Any],
    plan: Mapping[str, Any],
    status: str,
    rows_staged: int,
    error_code: str | None,
    error_summary: str | None,
    generated_at: datetime | str | None,
) -> dict[str, Any]:
    """Build the Stage 19T artifact payload before integrity is attached."""
    summary = {
        'status': status,
        'rows_read': plan['rows_read'],
        'rows_staged': rows_staged,
        'rows_rejected': plan['rows_rejected'],
        'rows_skipped': plan['rows_skipped'],
        'source_station_type_counts': dict(plan['source_station_type_counts']),
        'noisy_transient_station_type_counts': dict(plan['noisy_transient_station_type_counts']),
        'noisy_transient_station_types': sorted(plan['noisy_transient_station_type_counts']),
        'warning_reason_counts': dict(plan['warning_reason_counts']),
        'rejection_reason_counts': dict(plan['rejection_reason_counts']),
        'skipped_reason_counts': dict(plan['skipped_reason_counts']),
        'staging_target_table': STAGING_TABLE,
        'source_run_target_table': SOURCE_RUN_TABLE,
        'error_code': error_code,
        'error_summary': error_summary,
        'safety_summary': dict(SAFETY_BOUNDARY),
    }
    payload = {
        'input_format': dict(file_format),
        'staged_source_record_hashes': [
            row.get('source_record_hash')
            for row in plan['staged_rows'][:MAX_ARTIFACT_DETAIL_ROWS]
        ],
        'rejections': _detail_rows(plan['rejected_rows']),
        'skipped_rows': _detail_rows(plan['skipped_rows']),
        'warnings': _detail_rows(plan['warnings']),
        'station_type_mapping_written': False,
    }
    return source_run_artifacts.build_artifact_payload_shell(
        schema_version=SCHEMA_VERSION,
        source_run_key=str(source_run_kwargs['source_run_key']),
        source_name=str(source_run_kwargs['source_name']),
        source_category=str(source_run_kwargs['source_category']),
        domain=str(source_run_kwargs['domain']),
        import_scope=str(source_run_kwargs['import_scope']),
        git_commit_sha=str(source_run_kwargs['git_commit_sha']),
        importer_name=str(source_run_kwargs['importer_name']),
        importer_version=str(source_run_kwargs['importer_version']),
        trigger_context=str(source_run_kwargs['trigger_context']),
        generated_at=generated_at,
        source_uri=source_uri,
        source_input_sha256=source_input_sha256,
        safety_boundary=SAFETY_BOUNDARY,
        metadata={
            'stage': '19t',
            'source_adapter': SOURCE_ADAPTER,
            'local_file_only': True,
        },
        summary=summary,
        payload=payload,
    )


def run_explicit_station_stager(
    conn: Any,
    *,
    source_run: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    station_stager: Callable[..., int] | None,
) -> int:
    """Run a caller-supplied stager, failing closed when none is provided."""
    if station_stager is None:
        raise EdsmStationImportError(
            'staging execution requires an explicit compatible station_stager; '
            'staging_edsm_stations.source_run_id currently expects enrichment_source_runs.id, '
            'not source_runs.id from this wrapper'
        )
    return station_stager(conn, source_run=source_run, rows=rows)


def _source_uri(source_file: Path) -> str:
    return source_file.resolve().as_uri()


def _copy_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _station_type_counts(*row_groups: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row_group in row_groups:
        for row in row_group:
            station_type = _station_type_label(row)
            counts[station_type or 'Unknown'] += 1
    return dict(sorted(counts.items()))


def _station_type_label(row: Mapping[str, Any]) -> str | None:
    station_type = read_text(row.get('station_type'))
    if station_type:
        return station_type
    raw_payload = row.get('raw_payload')
    if isinstance(raw_payload, Mapping):
        return read_text(first_present(raw_payload, 'type', 'stationType', 'station_type'))
    return None


def _reason_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        reason = read_text(row.get('reason')) or 'unknown'
        counts[reason] += 1
    return dict(sorted(counts.items()))


def _detail_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows[:MAX_ARTIFACT_DETAIL_ROWS]]


def _completion_metadata(plan: Mapping[str, Any], *, status: str) -> dict[str, Any]:
    return {
        'stage': '19t',
        'status': status,
        'source_station_type_counts': dict(plan['source_station_type_counts']),
        'noisy_transient_station_type_counts': dict(plan['noisy_transient_station_type_counts']),
        'safety_boundary': dict(SAFETY_BOUNDARY),
    }


def _error_summary(exc: Exception) -> str:
    return f'{type(exc).__name__}: {exc}'[:500]
