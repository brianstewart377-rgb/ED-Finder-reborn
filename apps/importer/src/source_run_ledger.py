"""Small repository helper for the Stage 19 source_runs ledger."""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


SOURCE_RUNS_TABLE = 'source_runs'

ALLOWED_STATUSES = frozenset((
    'planned',
    'running',
    'succeeded',
    'failed',
    'rejected',
    'superseded',
    'cancelled',
))
INITIAL_STATUSES = frozenset(('planned', 'running'))
OPEN_STATUSES = ('planned', 'running')
SUPERSEDABLE_STATUSES = ('succeeded',)

ALLOWED_SOURCE_NAMES = frozenset((
    'edsm',
    'spansh',
    'inara',
    'daftmav',
    'mega_guide',
    'operator_artifact',
    'local_generated_artifact',
    'mission_observation',
    'frontier_journal',
    'edcd',
    'canonn',
    'planner_reference_archive',
))

ALLOWED_SOURCE_CATEGORIES = frozenset((
    'source_of_truth',
    'source_of_evidence',
    'source_of_inspiration',
    'manual_operator_source',
    'derived_source',
))

ALLOWED_DOMAINS = frozenset((
    'systems',
    'stars',
    'bodies',
    'rings',
    'belt_clusters',
    'stations',
    'settlements',
    'station_services',
    'markets',
    'shipyard_outfitting',
    'factions_bgs',
    'economies_security',
    'construction_sites',
    'fleet_carriers_transient',
    'materials_resources',
    'facility_templates',
    'rules_reference',
    'mission_intelligence',
    'operator_artifacts',
))

ALLOWED_IMPORT_SCOPES = frozenset((
    'raw_capture_only',
    'staging_only',
    'warehouse_fact_refresh',
    'reconciliation_candidate',
    'review_packet',
    'approval_allowlist',
    'bounded_write_reviewed',
    'canonical_apply',
))

ROW_COUNT_FIELDS = ('rows_read', 'rows_staged', 'rows_rejected', 'rows_skipped')


class SourceRunLedgerError(ValueError):
    """Base error for source-run helper validation and state failures."""


class SourceRunStateError(SourceRunLedgerError):
    """Raised when a requested source-run transition is not valid."""


class SourceRunLedger:
    """Repository wrapper around a caller-owned DB-API connection."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def create_source_run(self, **kwargs: Any) -> dict[str, Any]:
        return create_source_run(self.conn, **kwargs)

    def mark_source_run_running(self, source_run_key: str, **kwargs: Any) -> dict[str, Any]:
        return mark_source_run_running(self.conn, source_run_key, **kwargs)

    def complete_source_run_success(self, source_run_key: str, **kwargs: Any) -> dict[str, Any]:
        return complete_source_run_success(self.conn, source_run_key, **kwargs)

    def complete_source_run_failed(self, source_run_key: str, **kwargs: Any) -> dict[str, Any]:
        return complete_source_run_failed(self.conn, source_run_key, **kwargs)

    def complete_source_run_rejected(self, source_run_key: str, **kwargs: Any) -> dict[str, Any]:
        return complete_source_run_rejected(self.conn, source_run_key, **kwargs)

    def complete_source_run_cancelled(self, source_run_key: str, **kwargs: Any) -> dict[str, Any]:
        return complete_source_run_cancelled(self.conn, source_run_key, **kwargs)

    def supersede_source_run(self, source_run_key: str, **kwargs: Any) -> dict[str, Any]:
        return supersede_source_run(self.conn, source_run_key, **kwargs)

    def get_active_source_run(self, **kwargs: Any) -> dict[str, Any] | None:
        return get_active_source_run(self.conn, **kwargs)

    def assert_no_active_source_run(self, **kwargs: Any) -> None:
        assert_no_active_source_run(self.conn, **kwargs)


def create_source_run(
    conn: Any,
    *,
    source_run_key: str,
    source_name: str,
    source_category: str,
    domain: str,
    import_scope: str,
    git_commit_sha: str,
    importer_name: str,
    importer_version: str,
    trigger_context: str,
    status: str = 'planned',
    source_uri: str | None = None,
    source_input_sha256: str | None = None,
    source_manifest_sha256: str | None = None,
    started_at: datetime | None = None,
    rows_read: int = 0,
    rows_staged: int = 0,
    rows_rejected: int = 0,
    rows_skipped: int = 0,
    safety_boundary: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _validate_required_text(source_run_key, 'source_run_key')
    _validate_source_contract(source_name, source_category, domain, import_scope)
    _validate_allowed(status, ALLOWED_STATUSES, 'status')
    if status not in INITIAL_STATUSES:
        raise SourceRunLedgerError('new source runs must start as planned or running')
    for field, value in {
        'git_commit_sha': git_commit_sha,
        'importer_name': importer_name,
        'importer_version': importer_version,
        'trigger_context': trigger_context,
    }.items():
        _validate_required_text(value, field)
    counts = _normalise_row_counts(
        rows_read=rows_read,
        rows_staged=rows_staged,
        rows_rejected=rows_rejected,
        rows_skipped=rows_skipped,
    )
    started = started_at or _utc_now()

    sql = f"""
        INSERT INTO {SOURCE_RUNS_TABLE} (
            source_run_key,
            source_name,
            source_category,
            domain,
            import_scope,
            status,
            source_uri,
            source_input_sha256,
            source_manifest_sha256,
            started_at,
            git_commit_sha,
            importer_name,
            importer_version,
            trigger_context,
            rows_read,
            rows_staged,
            rows_rejected,
            rows_skipped,
            safety_boundary,
            metadata
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb
        )
        RETURNING id, source_run_key, status
        """
    return _execute_returning_one(
        conn,
        sql,
        (
            source_run_key,
            source_name,
            source_category,
            domain,
            import_scope,
            status,
            source_uri,
            source_input_sha256,
            source_manifest_sha256,
            started,
            git_commit_sha,
            importer_name,
            importer_version,
            trigger_context,
            counts['rows_read'],
            counts['rows_staged'],
            counts['rows_rejected'],
            counts['rows_skipped'],
            _jsonb(safety_boundary or {}),
            _jsonb(metadata or {}),
        ),
    )


def mark_source_run_running(
    conn: Any,
    source_run_key: str,
    *,
    started_at: datetime | None = None,
) -> dict[str, Any]:
    _validate_required_text(source_run_key, 'source_run_key')
    sql = f"""
        UPDATE {SOURCE_RUNS_TABLE}
        SET
            status = %s,
            started_at = COALESCE(%s, started_at),
            updated_at = NOW()
        WHERE source_run_key = %s
          AND status = 'planned'
        RETURNING id, source_run_key, status
        """
    return _execute_transition(
        conn,
        sql,
        ('running', started_at, source_run_key),
        source_run_key=source_run_key,
        transition='running',
    )


def complete_source_run_success(
    conn: Any,
    source_run_key: str,
    *,
    rows_read: int | None = None,
    rows_staged: int | None = None,
    rows_rejected: int | None = None,
    rows_skipped: int | None = None,
    artifact_path: str | None = None,
    artifact_sha256: str | None = None,
    artifact_integrity_sha256: str | None = None,
    finished_at: datetime | None = None,
    duration_ms: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _complete_source_run(
        conn,
        source_run_key,
        status='succeeded',
        rows_read=rows_read,
        rows_staged=rows_staged,
        rows_rejected=rows_rejected,
        rows_skipped=rows_skipped,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        artifact_integrity_sha256=artifact_integrity_sha256,
        error_code=None,
        error_summary=None,
        finished_at=finished_at,
        duration_ms=duration_ms,
        metadata=metadata,
    )


def complete_source_run_failed(
    conn: Any,
    source_run_key: str,
    *,
    error_code: str,
    error_summary: str,
    rows_read: int | None = None,
    rows_staged: int | None = None,
    rows_rejected: int | None = None,
    rows_skipped: int | None = None,
    artifact_path: str | None = None,
    artifact_sha256: str | None = None,
    artifact_integrity_sha256: str | None = None,
    finished_at: datetime | None = None,
    duration_ms: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _validate_required_text(error_code, 'error_code')
    _validate_required_text(error_summary, 'error_summary')
    return _complete_source_run(
        conn,
        source_run_key,
        status='failed',
        rows_read=rows_read,
        rows_staged=rows_staged,
        rows_rejected=rows_rejected,
        rows_skipped=rows_skipped,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        artifact_integrity_sha256=artifact_integrity_sha256,
        error_code=error_code,
        error_summary=error_summary,
        finished_at=finished_at,
        duration_ms=duration_ms,
        metadata=metadata,
    )


def complete_source_run_rejected(
    conn: Any,
    source_run_key: str,
    *,
    error_code: str,
    error_summary: str,
    rows_read: int | None = None,
    rows_staged: int | None = None,
    rows_rejected: int | None = None,
    rows_skipped: int | None = None,
    artifact_path: str | None = None,
    artifact_sha256: str | None = None,
    artifact_integrity_sha256: str | None = None,
    finished_at: datetime | None = None,
    duration_ms: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _validate_required_text(error_code, 'error_code')
    _validate_required_text(error_summary, 'error_summary')
    return _complete_source_run(
        conn,
        source_run_key,
        status='rejected',
        rows_read=rows_read,
        rows_staged=rows_staged,
        rows_rejected=rows_rejected,
        rows_skipped=rows_skipped,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        artifact_integrity_sha256=artifact_integrity_sha256,
        error_code=error_code,
        error_summary=error_summary,
        finished_at=finished_at,
        duration_ms=duration_ms,
        metadata=metadata,
    )


def complete_source_run_cancelled(
    conn: Any,
    source_run_key: str,
    *,
    error_code: str | None = None,
    error_summary: str | None = None,
    rows_read: int | None = None,
    rows_staged: int | None = None,
    rows_rejected: int | None = None,
    rows_skipped: int | None = None,
    artifact_path: str | None = None,
    artifact_sha256: str | None = None,
    artifact_integrity_sha256: str | None = None,
    finished_at: datetime | None = None,
    duration_ms: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _complete_source_run(
        conn,
        source_run_key,
        status='cancelled',
        rows_read=rows_read,
        rows_staged=rows_staged,
        rows_rejected=rows_rejected,
        rows_skipped=rows_skipped,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        artifact_integrity_sha256=artifact_integrity_sha256,
        error_code=error_code,
        error_summary=error_summary,
        finished_at=finished_at,
        duration_ms=duration_ms,
        metadata=metadata,
    )


def supersede_source_run(
    conn: Any,
    source_run_key: str,
    *,
    finished_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _validate_required_text(source_run_key, 'source_run_key')
    finished = finished_at or _utc_now()
    sql = f"""
        UPDATE {SOURCE_RUNS_TABLE}
        SET
            status = %s,
            finished_at = COALESCE(finished_at, GREATEST(%s, started_at)),
            metadata = metadata || %s::jsonb,
            updated_at = NOW()
        WHERE source_run_key = %s
          AND status = ANY(%s)
        RETURNING id, source_run_key, status
        """
    return _execute_transition(
        conn,
        sql,
        ('superseded', finished, _jsonb(metadata or {}), source_run_key, list(SUPERSEDABLE_STATUSES)),
        source_run_key=source_run_key,
        transition='superseded',
    )


def get_active_source_run(
    conn: Any,
    *,
    source_name: str,
    domain: str,
    import_scope: str,
) -> dict[str, Any] | None:
    _validate_source_name_domain_scope(source_name, domain, import_scope)
    sql = f"""
        SELECT
            id,
            source_run_key,
            source_name,
            domain,
            import_scope,
            status,
            started_at
        FROM {SOURCE_RUNS_TABLE}
        WHERE source_name = %s
          AND domain = %s
          AND import_scope = %s
          AND status = 'running'
        ORDER BY started_at DESC
        LIMIT 1
        """
    cur = conn.cursor()
    try:
        cur.execute(sql, (source_name, domain, import_scope))
        return _row_to_dict(cur.fetchone())
    finally:
        _close_cursor(cur)


def assert_no_active_source_run(
    conn: Any,
    *,
    source_name: str,
    domain: str,
    import_scope: str,
) -> None:
    active_run = get_active_source_run(
        conn,
        source_name=source_name,
        domain=domain,
        import_scope=import_scope,
    )
    if active_run is not None:
        raise SourceRunStateError(
            'active source run already exists for '
            f'{source_name}/{domain}/{import_scope}: {active_run.get("source_run_key")}'
        )


def _complete_source_run(
    conn: Any,
    source_run_key: str,
    *,
    status: str,
    rows_read: int | None,
    rows_staged: int | None,
    rows_rejected: int | None,
    rows_skipped: int | None,
    artifact_path: str | None,
    artifact_sha256: str | None,
    artifact_integrity_sha256: str | None,
    error_code: str | None,
    error_summary: str | None,
    finished_at: datetime | None,
    duration_ms: int | None,
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    _validate_required_text(source_run_key, 'source_run_key')
    _validate_allowed(status, ALLOWED_STATUSES, 'status')
    if status not in {'succeeded', 'failed', 'rejected', 'cancelled'}:
        raise SourceRunLedgerError(f'unsupported completion status: {status}')
    counts = _normalise_optional_row_counts(
        rows_read=rows_read,
        rows_staged=rows_staged,
        rows_rejected=rows_rejected,
        rows_skipped=rows_skipped,
    )
    if duration_ms is not None and duration_ms < 0:
        raise SourceRunLedgerError('duration_ms must be >= 0')
    finished = finished_at or _utc_now()

    sql = f"""
        UPDATE {SOURCE_RUNS_TABLE}
        SET
            status = %s,
            finished_at = GREATEST(%s, started_at),
            duration_ms = COALESCE(%s, duration_ms),
            rows_read = COALESCE(%s, rows_read),
            rows_staged = COALESCE(%s, rows_staged),
            rows_rejected = COALESCE(%s, rows_rejected),
            rows_skipped = COALESCE(%s, rows_skipped),
            artifact_path = COALESCE(%s, artifact_path),
            artifact_sha256 = COALESCE(%s, artifact_sha256),
            artifact_integrity_sha256 = COALESCE(%s, artifact_integrity_sha256),
            error_code = COALESCE(%s, error_code),
            error_summary = COALESCE(%s, error_summary),
            metadata = metadata || %s::jsonb,
            updated_at = NOW()
        WHERE source_run_key = %s
          AND status = ANY(%s)
        RETURNING id, source_run_key, status
        """
    return _execute_transition(
        conn,
        sql,
        (
            status,
            finished,
            duration_ms,
            counts['rows_read'],
            counts['rows_staged'],
            counts['rows_rejected'],
            counts['rows_skipped'],
            artifact_path,
            artifact_sha256,
            artifact_integrity_sha256,
            error_code,
            error_summary,
            _jsonb(metadata or {}),
            source_run_key,
            list(OPEN_STATUSES),
        ),
        source_run_key=source_run_key,
        transition=status,
    )


def _execute_transition(
    conn: Any,
    sql: str,
    params: tuple[Any, ...],
    *,
    source_run_key: str,
    transition: str,
) -> dict[str, Any]:
    row = _execute_returning_one_or_none(conn, sql, params)
    if row is None:
        raise SourceRunStateError(f'source run {source_run_key!r} cannot transition to {transition}')
    return row


def _execute_returning_one(conn: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    row = _execute_returning_one_or_none(conn, sql, params)
    if row is None:
        raise SourceRunLedgerError('source-run write did not return a row')
    return row


def _execute_returning_one_or_none(conn: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return _row_to_dict(cur.fetchone())
    finally:
        _close_cursor(cur)


def _normalise_row_counts(
    *,
    rows_read: int,
    rows_staged: int,
    rows_rejected: int,
    rows_skipped: int,
) -> dict[str, int]:
    counts = {
        'rows_read': rows_read,
        'rows_staged': rows_staged,
        'rows_rejected': rows_rejected,
        'rows_skipped': rows_skipped,
    }
    for field, value in counts.items():
        _validate_non_negative_int(value, field)
    return counts


def _normalise_optional_row_counts(
    *,
    rows_read: int | None,
    rows_staged: int | None,
    rows_rejected: int | None,
    rows_skipped: int | None,
) -> dict[str, int | None]:
    counts = {
        'rows_read': rows_read,
        'rows_staged': rows_staged,
        'rows_rejected': rows_rejected,
        'rows_skipped': rows_skipped,
    }
    for field, value in counts.items():
        if value is not None:
            _validate_non_negative_int(value, field)
    return counts


def _validate_source_contract(
    source_name: str,
    source_category: str,
    domain: str,
    import_scope: str,
) -> None:
    _validate_source_name_domain_scope(source_name, domain, import_scope)
    _validate_allowed(source_category, ALLOWED_SOURCE_CATEGORIES, 'source_category')


def _validate_source_name_domain_scope(source_name: str, domain: str, import_scope: str) -> None:
    _validate_allowed(source_name, ALLOWED_SOURCE_NAMES, 'source_name')
    _validate_allowed(domain, ALLOWED_DOMAINS, 'domain')
    _validate_allowed(import_scope, ALLOWED_IMPORT_SCOPES, 'import_scope')


def _validate_allowed(value: str, allowed_values: frozenset[str], field: str) -> None:
    _validate_required_text(value, field)
    if value not in allowed_values:
        raise SourceRunLedgerError(f'invalid {field}: {value!r}')


def _validate_required_text(value: str | None, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SourceRunLedgerError(f'{field} is required')


def _validate_non_negative_int(value: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SourceRunLedgerError(f'{field} must be an integer')
    if value < 0:
        raise SourceRunLedgerError(f'{field} must be >= 0')


def _jsonb(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    if hasattr(row, 'keys'):
        return {key: row[key] for key in row.keys()}
    if isinstance(row, (tuple, list)):
        keys = ('id', 'source_run_key', 'status')
        return {key: row[index] for index, key in enumerate(keys[:len(row)])}
    raise TypeError('source-run cursor rows must be mapping-like or tuple-like')


def _close_cursor(cur: Any) -> None:
    close = getattr(cur, 'close', None)
    if close is not None:
        close()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    'ALLOWED_DOMAINS',
    'ALLOWED_IMPORT_SCOPES',
    'ALLOWED_SOURCE_CATEGORIES',
    'ALLOWED_SOURCE_NAMES',
    'ALLOWED_STATUSES',
    'SOURCE_RUNS_TABLE',
    'SourceRunLedger',
    'SourceRunLedgerError',
    'SourceRunStateError',
    'assert_no_active_source_run',
    'complete_source_run_cancelled',
    'complete_source_run_failed',
    'complete_source_run_rejected',
    'complete_source_run_success',
    'create_source_run',
    'get_active_source_run',
    'mark_source_run_running',
    'supersede_source_run',
]
