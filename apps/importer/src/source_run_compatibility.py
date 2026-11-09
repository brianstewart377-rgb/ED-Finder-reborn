"""Compatibility helpers for new source_runs and legacy enrichment staging FKs.

Legacy enrichment staging tables, including ``staging_edsm_stations``, still
reference ``enrichment_source_runs(id)``. New Stage 19 import wrappers use the
durable ``source_runs`` ledger. These helpers create or resolve the legacy
``enrichment_source_runs`` row that future explicit stagers must use as the
legacy ``source_run_id`` value.

This module does not connect to a database, write staging rows, run imports,
or touch canonical tables.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import artifact_utils
from enrichment_staging import SOURCE_CLASSES, classify_source_adapter


BRIDGE_SCHEMA_VERSION = 'source_run_legacy_compatibility/v1'
LEGACY_SOURCE_RUNS_TABLE = 'enrichment_source_runs'
NEW_SOURCE_RUNS_TABLE = 'source_runs'
LEGACY_SOURCE_RUN_KEY_PREFIX = 'source_runs:'
TARGET_STAGING_FK = 'enrichment_source_runs(id)'
DEFAULT_SOURCE_KIND = 'offline_snapshot'


class SourceRunCompatibilityError(ValueError):
    """Raised when source-run compatibility metadata is incomplete or invalid."""


def build_enrichment_source_run_key(source_run: Mapping[str, Any] | str) -> str:
    """Build the deterministic legacy enrichment source-run key for a source run."""
    if isinstance(source_run, Mapping):
        source_run_key = source_run.get('source_run_key')
    else:
        source_run_key = source_run
    return f'{LEGACY_SOURCE_RUN_KEY_PREFIX}{_required_text(source_run_key, "source_run_key")}'


def build_enrichment_source_run_metadata(
    source_run: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build explicit dual-ledger provenance for a legacy source-run row."""
    source_run_key = _required_text(source_run.get('source_run_key'), 'source_run_key')
    source_runs_provenance = {
        'id': source_run.get('id'),
        'source_run_key': source_run_key,
        'source_name': source_run.get('source_name'),
        'source_category': source_run.get('source_category'),
        'domain': source_run.get('domain'),
        'import_scope': source_run.get('import_scope'),
        'status': source_run.get('status'),
        'source_uri': source_run.get('source_uri'),
        'source_input_sha256': source_run.get('source_input_sha256'),
        'source_manifest_sha256': source_run.get('source_manifest_sha256'),
        'started_at': source_run.get('started_at'),
        'finished_at': source_run.get('finished_at'),
        'git_commit_sha': source_run.get('git_commit_sha'),
        'importer_name': source_run.get('importer_name'),
        'importer_version': source_run.get('importer_version'),
        'trigger_context': source_run.get('trigger_context'),
        'artifact_path': source_run.get('artifact_path'),
        'artifact_sha256': source_run.get('artifact_sha256'),
        'artifact_integrity_sha256': source_run.get('artifact_integrity_sha256'),
        'safety_boundary': dict(source_run.get('safety_boundary') or {}),
        'metadata': dict(source_run.get('metadata') or {}),
    }
    bridge_metadata = {
        'schema_version': BRIDGE_SCHEMA_VERSION,
        'compatibility_bridge': True,
        'bridge_reason': (
            'legacy staging source_run_id columns reference enrichment_source_runs(id), '
            'not source_runs(id)'
        ),
        'new_source_run_table': NEW_SOURCE_RUNS_TABLE,
        'legacy_source_run_table': LEGACY_SOURCE_RUNS_TABLE,
        'target_staging_fk': TARGET_STAGING_FK,
        'legacy_source_run_key': build_enrichment_source_run_key(source_run_key),
        'source_runs_provenance': source_runs_provenance,
        'staging_policy': {
            'do_not_pass_source_runs_id_to_legacy_staging_source_run_id': True,
            'legacy_source_run_id_required_for_legacy_staging': True,
            'staging_rows_written_by_this_helper': False,
            'canonical_writes_planned': 0,
        },
    }
    bridge_metadata.update(dict(metadata or {}))
    return bridge_metadata


def build_enrichment_source_run_row(
    source_run: Mapping[str, Any],
    *,
    source: str | None = None,
    adapter_name: str | None = None,
    adapter_version: str | None = None,
    source_kind: str = DEFAULT_SOURCE_KIND,
    source_class: str | None = None,
    run_label: str | None = None,
    dry_run: bool = True,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map a new ``source_runs`` row/metadata into legacy enrichment shape."""
    source_name = _required_text(source or source_run.get('source_name'), 'source')
    resolved_adapter_name = _required_text(
        adapter_name or source_run.get('importer_name'),
        'adapter_name',
    )
    resolved_adapter_version = _required_text(
        adapter_version or source_run.get('importer_version'),
        'adapter_version',
    )
    resolved_source_class = source_class or classify_source_adapter(source_name)
    if resolved_source_class not in SOURCE_CLASSES:
        raise SourceRunCompatibilityError(f'invalid source_class: {resolved_source_class!r}')

    source_run_key = _required_text(source_run.get('source_run_key'), 'source_run_key')
    return {
        'source_run_key': build_enrichment_source_run_key(source_run_key),
        'source': source_name,
        'adapter_name': resolved_adapter_name,
        'adapter_version': resolved_adapter_version,
        'source_kind': _required_text(source_kind, 'source_kind'),
        'source_class': resolved_source_class,
        'run_label': run_label if run_label is not None else source_run_key,
        'dry_run': bool(dry_run),
        'source_started_at': source_run.get('started_at'),
        'source_completed_at': source_run.get('finished_at'),
        'metadata': build_enrichment_source_run_metadata(source_run, metadata=metadata),
    }


def get_enrichment_source_run_by_key(conn: Any, source_run_key: str) -> dict[str, Any] | None:
    """Fetch a legacy enrichment source-run row by its deterministic bridge key."""
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT
                id,
                source_run_key,
                source,
                adapter_name,
                adapter_version,
                source_kind,
                source_class,
                run_label,
                dry_run,
                source_started_at,
                source_completed_at,
                metadata
            FROM {LEGACY_SOURCE_RUNS_TABLE}
            WHERE source_run_key = %s
            """,
            (_required_text(source_run_key, 'source_run_key'),),
        )
        return _row_to_dict(cur.fetchone(), cur)
    finally:
        _close_cursor(cur)


def create_enrichment_source_run_for_source_run(
    conn: Any,
    source_run: Mapping[str, Any],
    **row_options: Any,
) -> dict[str, Any]:
    """Insert or update the legacy enrichment source-run compatibility row."""
    row = build_enrichment_source_run_row(source_run, **row_options)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            INSERT INTO {LEGACY_SOURCE_RUNS_TABLE} (
                source_run_key,
                source,
                adapter_name,
                adapter_version,
                source_kind,
                source_class,
                run_label,
                dry_run,
                source_started_at,
                source_completed_at,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (source_run_key) DO UPDATE SET
                source = EXCLUDED.source,
                adapter_name = EXCLUDED.adapter_name,
                adapter_version = EXCLUDED.adapter_version,
                source_kind = EXCLUDED.source_kind,
                source_class = EXCLUDED.source_class,
                run_label = EXCLUDED.run_label,
                dry_run = EXCLUDED.dry_run,
                source_started_at = COALESCE(EXCLUDED.source_started_at, enrichment_source_runs.source_started_at),
                source_completed_at = COALESCE(EXCLUDED.source_completed_at, enrichment_source_runs.source_completed_at),
                metadata = enrichment_source_runs.metadata || EXCLUDED.metadata
            RETURNING
                id,
                source_run_key,
                source,
                adapter_name,
                adapter_version,
                source_kind,
                source_class,
                run_label,
                dry_run,
                source_started_at,
                source_completed_at,
                metadata
            """,
            (
                row['source_run_key'],
                row['source'],
                row['adapter_name'],
                row['adapter_version'],
                row['source_kind'],
                row['source_class'],
                row['run_label'],
                row['dry_run'],
                row['source_started_at'],
                row['source_completed_at'],
                _jsonb(row['metadata']),
            ),
        )
        created = _row_to_dict(cur.fetchone(), cur)
        if created is None:
            raise SourceRunCompatibilityError('legacy enrichment source-run write did not return a row')
        return created
    finally:
        _close_cursor(cur)


def get_or_create_enrichment_source_run_for_source_run(
    conn: Any,
    source_run: Mapping[str, Any],
    **row_options: Any,
) -> dict[str, Any]:
    """Resolve the legacy source-run row, inserting it if absent."""
    bridge_key = build_enrichment_source_run_key(source_run)
    existing = get_enrichment_source_run_by_key(conn, bridge_key)
    if existing is not None:
        return existing
    return create_enrichment_source_run_for_source_run(conn, source_run, **row_options)


def build_legacy_staging_context(
    *,
    source_run: Mapping[str, Any],
    enrichment_source_run: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the explicit legacy FK context future staging writers need."""
    legacy_id = enrichment_source_run.get('id')
    if isinstance(legacy_id, bool) or legacy_id is None:
        raise SourceRunCompatibilityError('enrichment_source_run.id is required for legacy staging context')
    try:
        legacy_source_run_id = int(legacy_id)
    except (TypeError, ValueError) as exc:
        raise SourceRunCompatibilityError('enrichment_source_run.id must be an integer') from exc

    return {
        'source_run': dict(source_run),
        'enrichment_source_run': dict(enrichment_source_run),
        'legacy_source_run_id': legacy_source_run_id,
        'target_staging_fk': TARGET_STAGING_FK,
    }


def get_or_create_legacy_staging_context(
    conn: Any,
    source_run: Mapping[str, Any],
    **row_options: Any,
) -> dict[str, Any]:
    """Create/resolve the legacy row and return the FK context for stagers."""
    enrichment_source_run = get_or_create_enrichment_source_run_for_source_run(
        conn,
        source_run,
        **row_options,
    )
    return build_legacy_staging_context(
        source_run=source_run,
        enrichment_source_run=enrichment_source_run,
    )


def _jsonb(value: Mapping[str, Any]) -> str:
    return artifact_utils.canonical_json(value)


def _row_to_dict(row: Any, cursor: Any | None = None) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    if hasattr(row, 'keys'):
        return {key: row[key] for key in row.keys()}
    if _is_positional_row(row):
        column_names = _cursor_column_names(cursor)
        if len(column_names) != len(row):
            raise TypeError(
                'compatibility helper cursor row length does not match cursor.description'
            )
        return dict(zip(column_names, row, strict=True))
    raise TypeError('compatibility helper cursor rows must be mapping-like')


def _is_positional_row(row: Any) -> bool:
    return isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray))


def _cursor_column_names(cursor: Any | None) -> list[str]:
    description = getattr(cursor, 'description', None)
    if not description:
        raise TypeError(
            'compatibility helper cursor rows must be mapping-like or '
            'cursor.description must define columns'
        )
    return [_description_column_name(column) for column in description]


def _description_column_name(column: Any) -> str:
    if isinstance(column, str):
        return column
    name = getattr(column, 'name', None)
    if name is not None:
        return str(name)
    try:
        return str(column[0])
    except (TypeError, IndexError, KeyError):
        raise TypeError(
            'compatibility helper cursor.description entries must expose column names'
        ) from None


def _close_cursor(cur: Any) -> None:
    close = getattr(cur, 'close', None)
    if callable(close):
        close()


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceRunCompatibilityError(f'{field} is required')
    return value.strip()


__all__ = [
    'BRIDGE_SCHEMA_VERSION',
    'DEFAULT_SOURCE_KIND',
    'LEGACY_SOURCE_RUNS_TABLE',
    'LEGACY_SOURCE_RUN_KEY_PREFIX',
    'NEW_SOURCE_RUNS_TABLE',
    'TARGET_STAGING_FK',
    'SourceRunCompatibilityError',
    'build_enrichment_source_run_key',
    'build_enrichment_source_run_metadata',
    'build_enrichment_source_run_row',
    'build_legacy_staging_context',
    'create_enrichment_source_run_for_source_run',
    'get_enrichment_source_run_by_key',
    'get_or_create_enrichment_source_run_for_source_run',
    'get_or_create_legacy_staging_context',
]
