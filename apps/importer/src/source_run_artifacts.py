"""Prep helpers for pairing source_runs ledger updates with JSON artifacts."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import artifact_utils
import source_run_ledger


FINAL_STATUSES = frozenset(('succeeded', 'failed', 'rejected', 'cancelled'))


@dataclass(frozen=True)
class SourceRunArtifactOutcome:
    """Result returned by a future import operation before ledger completion."""

    payload: Mapping[str, Any]
    status: str = 'succeeded'
    rows_read: int | None = None
    rows_staged: int | None = None
    rows_rejected: int | None = None
    rows_skipped: int | None = None
    error_code: str | None = None
    error_summary: str | None = None
    metadata: Mapping[str, Any] | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None


def build_artifact_payload_shell(
    *,
    schema_version: str,
    source_run_key: str,
    source_name: str,
    source_category: str,
    domain: str,
    import_scope: str,
    git_commit_sha: str,
    importer_name: str,
    importer_version: str,
    trigger_context: str,
    generated_at: datetime | str | None = None,
    source_uri: str | None = None,
    source_input_sha256: str | None = None,
    source_manifest_sha256: str | None = None,
    safety_boundary: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    summary: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the standard outer shape for a source-run JSON artifact."""
    return {
        'schema_version': schema_version,
        'generated_at': _format_generated_at(generated_at),
        'source_run': {
            'source_run_key': source_run_key,
            'source_name': source_name,
            'source_category': source_category,
            'domain': domain,
            'import_scope': import_scope,
            'git_commit_sha': git_commit_sha,
            'importer_name': importer_name,
            'importer_version': importer_version,
            'trigger_context': trigger_context,
        },
        'source': {
            'uri': source_uri,
            'input_sha256': source_input_sha256,
            'manifest_sha256': source_manifest_sha256,
        },
        'safety_boundary': dict(safety_boundary or {}),
        'metadata': dict(metadata or {}),
        'summary': dict(summary or {}),
        'payload': dict(payload or {}),
    }


def write_source_run_artifact(
    path: str | Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Write a JSON artifact and return metadata ready for source_runs completion."""
    record = artifact_utils.write_json_artifact(path, payload)
    return {
        'path': record['path'],
        'artifact_path': record['path'],
        'file_sha256': record['file_sha256'],
        'artifact_sha256': record['file_sha256'],
        'artifact_integrity_sha256': record['artifact_integrity_sha256'],
        'hash_algorithm': record['hash_algorithm'],
        'integrity_key': record['integrity_key'],
        'bytes_written': record['bytes_written'],
    }


def artifact_completion_metadata(
    artifact_record: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return completion metadata that preserves the artifact write record."""
    completion_metadata = dict(metadata or {})
    completion_metadata['artifact_record'] = {
        'path': artifact_record['artifact_path'],
        'file_sha256': artifact_record['file_sha256'],
        'artifact_sha256': artifact_record['artifact_sha256'],
        'artifact_integrity_sha256': artifact_record['artifact_integrity_sha256'],
        'bytes_written': artifact_record['bytes_written'],
        'hash_algorithm': artifact_record['hash_algorithm'],
        'integrity_key': artifact_record['integrity_key'],
    }
    return completion_metadata


def complete_source_run_with_artifact(
    conn: Any,
    source_run_key: str,
    *,
    status: str,
    artifact_record: Mapping[str, Any],
    rows_read: int | None = None,
    rows_staged: int | None = None,
    rows_rejected: int | None = None,
    rows_skipped: int | None = None,
    error_code: str | None = None,
    error_summary: str | None = None,
    finished_at: datetime | None = None,
    duration_ms: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Complete a source run with artifact path, file hash, and integrity hash."""
    kwargs = {
        'rows_read': rows_read,
        'rows_staged': rows_staged,
        'rows_rejected': rows_rejected,
        'rows_skipped': rows_skipped,
        'artifact_path': artifact_record['artifact_path'],
        'artifact_sha256': artifact_record['artifact_sha256'],
        'artifact_integrity_sha256': artifact_record['artifact_integrity_sha256'],
        'finished_at': finished_at,
        'duration_ms': duration_ms,
        'metadata': artifact_completion_metadata(artifact_record, metadata),
    }

    if status == 'succeeded':
        return source_run_ledger.complete_source_run_success(conn, source_run_key, **kwargs)
    if status == 'failed':
        return source_run_ledger.complete_source_run_failed(
            conn,
            source_run_key,
            error_code=error_code or '',
            error_summary=error_summary or '',
            **kwargs,
        )
    if status == 'rejected':
        return source_run_ledger.complete_source_run_rejected(
            conn,
            source_run_key,
            error_code=error_code or '',
            error_summary=error_summary or '',
            **kwargs,
        )
    if status == 'cancelled':
        return source_run_ledger.complete_source_run_cancelled(
            conn,
            source_run_key,
            error_code=error_code,
            error_summary=error_summary,
            **kwargs,
        )
    raise source_run_ledger.SourceRunLedgerError(f'unsupported artifact completion status: {status}')

def run_source_run_artifact_flow(
    conn: Any,
    *,
    source_run_kwargs: Mapping[str, Any],
    artifact_path: str | Path,
    operation: Callable[[Mapping[str, Any]], SourceRunArtifactOutcome],
) -> dict[str, Any]:
    """Example callback flow for future import jobs that produce an artifact."""
    source_run = source_run_ledger.create_source_run(conn, **dict(source_run_kwargs))
    outcome = operation(source_run)
    if outcome.status not in FINAL_STATUSES:
        raise source_run_ledger.SourceRunLedgerError(f'unsupported artifact completion status: {outcome.status}')

    artifact_record = write_source_run_artifact(artifact_path, outcome.payload)
    completion = complete_source_run_with_artifact(
        conn,
        source_run['source_run_key'],
        status=outcome.status,
        artifact_record=artifact_record,
        rows_read=outcome.rows_read,
        rows_staged=outcome.rows_staged,
        rows_rejected=outcome.rows_rejected,
        rows_skipped=outcome.rows_skipped,
        error_code=outcome.error_code,
        error_summary=outcome.error_summary,
        finished_at=outcome.finished_at,
        duration_ms=outcome.duration_ms,
        metadata=outcome.metadata,
    )
    return {
        'source_run': source_run,
        'artifact_record': artifact_record,
        'completion': completion,
    }

def _format_generated_at(value: datetime | str | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc).replace(microsecond=0)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    return str(value)
