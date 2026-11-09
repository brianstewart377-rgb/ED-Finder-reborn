"""Read-only Stage 19 operator visibility repository helpers.

The helpers in this module accept a caller-owned asyncpg-style connection.
They build compact, sanitized view models for source-run, artifact, legacy
bridge, staging, diagnostic, and safety-gate visibility.
"""
from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from edfinder_api.operator_visibility_models import (
    BRIDGE_SCHEMA_VERSION,
    DEFAULT_DIAGNOSTIC_ROW_LIMIT,
    DEFAULT_RECENT_SOURCE_RUN_LIMIT,
    LEGACY_SOURCE_RUN_KEY_PREFIX,
    MAX_OPERATOR_VISIBILITY_LIMIT,
    PILOT_DOMAIN,
    PILOT_IMPORT_SCOPE,
    PILOT_SOURCE_NAME,
    STAGE19ANR_MARKER_KEY,
    STAGING_TABLE,
    TARGET_STAGING_FK,
    OperatorArtifactSummary,
    OperatorBridgeSummary,
    OperatorDiagnosticRowSummary,
    OperatorSafetyGateSummary,
    OperatorSourceRunDetail,
    OperatorSourceRunSummary,
    OperatorStagingImpactSummary,
)

async def list_recent_source_runs(
    conn: Any,
    limit: int = DEFAULT_RECENT_SOURCE_RUN_LIMIT,
    status: str | None = None,
    source_name: str | None = None,
    domain: str | None = None,
) -> list[OperatorSourceRunSummary]:
    bounded_limit = _bounded_limit(limit, default=DEFAULT_RECENT_SOURCE_RUN_LIMIT)
    where_clauses: list[str] = []
    params: list[Any] = []

    if status:
        params.append(status)
        where_clauses.append(f'sr.status = ${len(params)}')
    if source_name:
        params.append(source_name)
        where_clauses.append(f'sr.source_name = ${len(params)}')
    if domain:
        params.append(domain)
        where_clauses.append(f'sr.domain = ${len(params)}')

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ''
    params.append(bounded_limit)
    sql = f"""
        /* operator_visibility:list_recent_source_runs */
        SELECT
          sr.source_run_key,
          sr.source_name,
          sr.source_category,
          sr.domain,
          sr.import_scope,
          sr.status,
          sr.started_at,
          sr.finished_at,
          sr.duration_ms,
          sr.rows_read,
          sr.rows_staged,
          sr.rows_rejected,
          sr.rows_skipped,
          (sr.artifact_path IS NOT NULL) AS artifact_present,
          (
            sr.artifact_sha256 IS NOT NULL
            AND sr.artifact_integrity_sha256 IS NOT NULL
          ) AS artifact_hash_present,
          EXISTS (
            SELECT 1
            FROM enrichment_source_runs esr
            WHERE esr.source_run_key = '{LEGACY_SOURCE_RUN_KEY_PREFIX}' || sr.source_run_key
          ) AS bridge_present,
          sr.trigger_context,
          sr.git_commit_sha,
          sr.error_code,
          sr.error_summary
        FROM source_runs sr
        {where_sql}
        ORDER BY sr.started_at DESC, sr.id DESC
        LIMIT ${len(params)}
    """
    rows = await conn.fetch(sql, *params)
    return [_source_run_summary_from_row(row) for row in rows]


async def get_source_run_detail(
    conn: Any,
    source_run_key: str,
) -> OperatorSourceRunDetail | None:
    source_run_row = await conn.fetchrow(
        f"""
        /* operator_visibility:get_source_run_detail */
        SELECT
          sr.id,
          sr.source_run_key,
          sr.source_name,
          sr.source_category,
          sr.domain,
          sr.import_scope,
          sr.status,
          sr.source_uri,
          sr.source_input_sha256,
          sr.source_manifest_sha256,
          sr.started_at,
          sr.finished_at,
          sr.duration_ms,
          sr.git_commit_sha,
          sr.importer_name,
          sr.importer_version,
          sr.trigger_context,
          sr.artifact_path,
          sr.artifact_sha256,
          sr.artifact_integrity_sha256,
          sr.rows_read,
          sr.rows_staged,
          sr.rows_rejected,
          sr.rows_skipped,
          sr.error_code,
          sr.error_summary,
          sr.safety_boundary,
          sr.metadata,
          (sr.artifact_path IS NOT NULL) AS artifact_present,
          (
            sr.artifact_sha256 IS NOT NULL
            AND sr.artifact_integrity_sha256 IS NOT NULL
          ) AS artifact_hash_present,
          EXISTS (
            SELECT 1
            FROM enrichment_source_runs esr
            WHERE esr.source_run_key = '{LEGACY_SOURCE_RUN_KEY_PREFIX}' || sr.source_run_key
          ) AS bridge_present
        FROM source_runs sr
        WHERE sr.source_run_key = $1
        """,
        source_run_key,
    )
    row = _row_to_dict(source_run_row)
    if row is None:
        return None

    summary = _source_run_summary_from_row(row)
    artifact = await get_source_run_artifacts(conn, source_run_key)
    bridge = await get_legacy_bridge_for_source_run(conn, source_run_key)
    staging_impact = None
    if bridge.bridge_present and bridge.legacy_source_run_id is not None:
        staging_impact = await get_staging_impact_for_bridge(
            conn,
            bridge.legacy_source_run_id,
            limit=DEFAULT_DIAGNOSTIC_ROW_LIMIT,
        )

    warnings = _detail_warnings(artifact, bridge, staging_impact)
    metadata = _mapping(row.get('metadata'))
    return OperatorSourceRunDetail(
        summary=summary,
        importer_name=_safe_text(row.get('importer_name')),
        importer_version=_safe_text(row.get('importer_version')),
        source_uri_redacted=_redact_uri(row.get('source_uri')),
        source_input_sha256=_safe_text(row.get('source_input_sha256')),
        source_manifest_sha256=_safe_text(row.get('source_manifest_sha256')),
        safety_boundary=_sanitized_mapping(row.get('safety_boundary')),
        metadata_summary=_metadata_summary(metadata),
        artifact_summary=artifact,
        bridge_summary=bridge,
        staging_impact_summary=staging_impact,
        validation_warnings=warnings,
        operator_notes=_operator_notes(metadata),
    )


async def get_source_run_artifacts(
    conn: Any,
    source_run_key: str,
) -> OperatorArtifactSummary:
    source_run_row = await conn.fetchrow(
        """
        /* operator_visibility:get_source_run_artifacts */
        SELECT
          source_run_key,
          status,
          artifact_path,
          artifact_sha256,
          artifact_integrity_sha256,
          rows_read,
          rows_staged,
          metadata
        FROM source_runs
        WHERE source_run_key = $1
        """,
        source_run_key,
    )
    row = _row_to_dict(source_run_row)
    if row is None:
        return OperatorArtifactSummary(
            source_run_key=source_run_key,
            artifact_path_redacted=None,
            artifact_sha256=None,
            artifact_integrity_sha256=None,
            artifact_record_present=False,
            file_exists=None,
            file_sha256_matches=None,
            integrity_hash_matches=None,
            schema_version=None,
            rows_read=0,
            rows_staged=0,
            status=None,
            validation_note='source run not found; no artifact metadata available',
        )

    metadata = _mapping(row.get('metadata'))
    artifact_record = _mapping(metadata.get('artifact_record'))
    artifact_path = row.get('artifact_path') or artifact_record.get('path')
    return OperatorArtifactSummary(
        source_run_key=str(row.get('source_run_key') or source_run_key),
        artifact_path_redacted=_redact_path(artifact_path),
        artifact_sha256=_safe_text(row.get('artifact_sha256')),
        artifact_integrity_sha256=_safe_text(row.get('artifact_integrity_sha256')),
        artifact_record_present=bool(artifact_record),
        file_exists=None,
        file_sha256_matches=None,
        integrity_hash_matches=None,
        schema_version=_schema_version(metadata),
        rows_read=_int_or_zero(row.get('rows_read')),
        rows_staged=_int_or_zero(row.get('rows_staged')),
        status=_safe_text(row.get('status')),
        validation_note=(
            'ledger metadata only; artifact file validation requires a later allowlisted path check'
        ),
    )


async def get_legacy_bridge_for_source_run(
    conn: Any,
    source_run_key: str,
) -> OperatorBridgeSummary:
    bridge_key = _bridge_key(source_run_key)
    row = await conn.fetchrow(
        """
        /* operator_visibility:get_legacy_bridge_for_source_run */
        SELECT
          id,
          source_run_key,
          dry_run,
          adapter_name,
          adapter_version,
          metadata
        FROM enrichment_source_runs
        WHERE source_run_key = $1
        """,
        bridge_key,
    )
    if row is None:
        return OperatorBridgeSummary(
            bridge_key=bridge_key,
            legacy_source_run_id=None,
            source_run_key=source_run_key,
            bridge_present=False,
            dry_run=None,
            adapter_name=None,
            adapter_version=None,
            target_staging_fk=TARGET_STAGING_FK,
            metadata_has_compatibility_bridge=False,
            staging_policy_blocks_source_runs_id=False,
        )
    return _bridge_summary_from_row(row, source_run_key=source_run_key)


async def get_staging_impact_for_bridge(
    conn: Any,
    legacy_source_run_id: int,
    limit: int = DEFAULT_DIAGNOSTIC_ROW_LIMIT,
) -> OperatorStagingImpactSummary:
    bounded_limit = _bounded_limit(limit, default=DEFAULT_DIAGNOSTIC_ROW_LIMIT)
    legacy_id = _required_positive_int(legacy_source_run_id, 'legacy_source_run_id')
    bridge_row = await _get_bridge_by_legacy_id(conn, legacy_id)
    bridge_key = _safe_text(bridge_row.get('source_run_key')) if bridge_row else None
    source_run_key = _source_run_key_from_bridge_key(bridge_key)
    source_runs_id = await _get_source_runs_id(conn, source_run_key) if source_run_key else None

    counts_row = await conn.fetchrow(
        """
        /* operator_visibility:get_staging_impact_for_bridge:counts */
        SELECT
          (
            SELECT COUNT(*)::int
            FROM staging_edsm_stations
            WHERE source_run_id = $1
          ) AS rows_total,
          (
            SELECT COUNT(*)::int
            FROM staging_edsm_stations
            WHERE source_run_id = $1
              AND source_class = 'diagnostic-only'
              AND confidence = 'diagnostic-only'
          ) AS rows_diagnostic_only,
          (
            SELECT COUNT(*)::int
            FROM staging_edsm_stations
            WHERE source_run_id = $1
              AND provenance->>'canonical_write_allowed' = 'false'
          ) AS rows_canonical_write_blocked,
          (
            SELECT COUNT(*)::int
            FROM staging_edsm_stations
            WHERE source_run_id = $1
              AND provenance ? $3
          ) AS rows_with_stage_markers,
          (
            SELECT COUNT(*)::int
            FROM staging_edsm_stations
            WHERE source_run_id = $2
          ) AS rows_using_source_runs_id
        """,
        legacy_id,
        source_runs_id,
        STAGE19ANR_MARKER_KEY,
    )
    counts = _row_to_dict(counts_row) or {}
    sample_rows = await conn.fetch(
        """
        /* operator_visibility:get_staging_impact_for_bridge:sample */
        SELECT
          id,
          source_run_id,
          station_name,
          station_type,
          system_name,
          source_class,
          confidence,
          provenance
        FROM staging_edsm_stations
        WHERE source_run_id = $1
        ORDER BY id
        LIMIT $2
        """,
        legacy_id,
        bounded_limit,
    )
    rows_total = _int_or_zero(counts.get('rows_total'))
    warnings: list[str] = []
    if bridge_row is None:
        warnings.append('legacy bridge row was not found for the supplied enrichment_source_runs.id')
    if source_run_key and source_runs_id is None:
        warnings.append('new source_runs.id could not be resolved from the bridge key')
    return OperatorStagingImpactSummary(
        source_run_key=source_run_key,
        bridge_key=bridge_key,
        legacy_source_run_id=legacy_id,
        staging_table=STAGING_TABLE,
        rows_total=rows_total,
        rows_diagnostic_only=_int_or_zero(counts.get('rows_diagnostic_only')),
        rows_canonical_write_blocked=_int_or_zero(
            counts.get('rows_canonical_write_blocked')
        ),
        rows_with_stage_markers=_int_or_zero(counts.get('rows_with_stage_markers')),
        rows_using_legacy_bridge_id=rows_total,
        rows_using_source_runs_id=_int_or_zero(counts.get('rows_using_source_runs_id')),
        sample_rows=[_diagnostic_row_from_row(row) for row in sample_rows],
        warnings=warnings,
    )


async def list_diagnostic_staging_rows(
    conn: Any,
    source_run_key: str | None = None,
    limit: int = DEFAULT_DIAGNOSTIC_ROW_LIMIT,
) -> list[OperatorDiagnosticRowSummary]:
    bounded_limit = _bounded_limit(limit, default=DEFAULT_DIAGNOSTIC_ROW_LIMIT)
    if source_run_key:
        bridge = await get_legacy_bridge_for_source_run(conn, source_run_key)
        if not bridge.bridge_present or bridge.legacy_source_run_id is None:
            return []
        rows = await conn.fetch(
            """
            /* operator_visibility:list_diagnostic_staging_rows:by_source_run */
            SELECT
              id,
              source_run_id,
              station_name,
              station_type,
              system_name,
              source_class,
              confidence,
              provenance
            FROM staging_edsm_stations
            WHERE source_run_id = $1
              AND (
                source_class = 'diagnostic-only'
                OR confidence = 'diagnostic-only'
                OR provenance ? $2
              )
            ORDER BY id DESC
            LIMIT $3
            """,
            bridge.legacy_source_run_id,
            STAGE19ANR_MARKER_KEY,
            bounded_limit,
        )
    else:
        rows = await conn.fetch(
            """
            /* operator_visibility:list_diagnostic_staging_rows */
            SELECT
              id,
              source_run_id,
              station_name,
              station_type,
              system_name,
              source_class,
              confidence,
              provenance
            FROM staging_edsm_stations
            WHERE
              source_class = 'diagnostic-only'
              OR confidence = 'diagnostic-only'
              OR provenance ? $1
            ORDER BY id DESC
            LIMIT $2
            """,
            STAGE19ANR_MARKER_KEY,
            bounded_limit,
        )
    return [_diagnostic_row_from_row(row) for row in rows]


async def get_operator_safety_gates(conn: Any) -> OperatorSafetyGateSummary:
    active_row = await conn.fetchrow(
        """
        /* operator_visibility:get_operator_safety_gates:active */
        SELECT COUNT(*)::int AS active_source_runs
        FROM source_runs
        WHERE source_name = $1
          AND domain = $2
          AND import_scope = $3
          AND status IN ('planned', 'running')
        """,
        PILOT_SOURCE_NAME,
        PILOT_DOMAIN,
        PILOT_IMPORT_SCOPE,
    )
    failed_row = await conn.fetchrow(
        """
        /* operator_visibility:get_operator_safety_gates:failed */
        SELECT COUNT(*)::int AS failed_unrecovered_source_runs
        FROM source_runs
        WHERE source_name = $1
          AND domain = $2
          AND import_scope = $3
          AND status IN ('failed', 'rejected')
        """,
        PILOT_SOURCE_NAME,
        PILOT_DOMAIN,
        PILOT_IMPORT_SCOPE,
    )
    latest_row = await conn.fetchrow(
        """
        /* operator_visibility:get_operator_safety_gates:latest */
        SELECT
          source_run_key,
          artifact_path,
          artifact_sha256,
          artifact_integrity_sha256
        FROM source_runs
        WHERE source_name = $1
          AND domain = $2
          AND import_scope = $3
          AND status = 'succeeded'
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """,
        PILOT_SOURCE_NAME,
        PILOT_DOMAIN,
        PILOT_IMPORT_SCOPE,
    )

    active = _row_to_dict(active_row) or {}
    failed = _row_to_dict(failed_row) or {}
    latest = _row_to_dict(latest_row)

    active_count = _int_or_zero(active.get('active_source_runs'))
    failed_count = _int_or_zero(failed.get('failed_unrecovered_source_runs'))
    latest_source_run_key = _safe_text(latest.get('source_run_key')) if latest else None
    latest_artifacts_present = bool(
        latest
        and latest.get('artifact_path')
        and latest.get('artifact_sha256')
        and latest.get('artifact_integrity_sha256')
    )

    bridge_fk_path_verified = False
    diagnostic_rows_isolated = False
    if latest_source_run_key:
        bridge = await get_legacy_bridge_for_source_run(conn, latest_source_run_key)
        if bridge.bridge_present and bridge.legacy_source_run_id is not None:
            impact = await get_staging_impact_for_bridge(conn, bridge.legacy_source_run_id, limit=25)
            bridge_fk_path_verified = (
                bridge.target_staging_fk == TARGET_STAGING_FK
                and bridge.metadata_has_compatibility_bridge
                and bridge.staging_policy_blocks_source_runs_id
                and impact.rows_using_source_runs_id == 0
            )
            diagnostic_rows_isolated = (
                impact.rows_total == 0
                or (
                    impact.rows_total == impact.rows_diagnostic_only
                    and impact.rows_total == impact.rows_canonical_write_blocked
                )
            )

    no_running_source_runs = active_count == 0
    no_failed_unrecovered_source_runs = failed_count == 0
    scheduler_assumed_disabled = True
    canonical_apply_assumed_disabled = True
    blockers: list[str] = []
    if not no_running_source_runs:
        blockers.append(
            f'{active_count} planned/running source run(s) exist for '
            f'{PILOT_SOURCE_NAME}/{PILOT_DOMAIN}/{PILOT_IMPORT_SCOPE}'
        )
    if not latest_artifacts_present:
        blockers.append('latest succeeded pilot source run artifact metadata is missing or incomplete')
    if not bridge_fk_path_verified:
        blockers.append('legacy bridge FK path is missing or not verified')
    if not diagnostic_rows_isolated:
        blockers.append('diagnostic staging rows are not fully isolated/canonical-write-blocked')
    if not no_failed_unrecovered_source_runs:
        blockers.append(
            f'{failed_count} failed/rejected source run(s) exist without a recovery marker in this skeleton'
        )

    notes = [
        'Scheduler state is assumed disabled from Stage 19 repository state; no service manager check is performed.',
        'Canonical apply is assumed disabled from Stage 19 repository state; no canonical executor check is performed.',
    ]
    return OperatorSafetyGateSummary(
        no_running_source_runs=no_running_source_runs,
        latest_artifacts_present=latest_artifacts_present,
        bridge_fk_path_verified=bridge_fk_path_verified,
        diagnostic_rows_isolated=diagnostic_rows_isolated,
        no_failed_unrecovered_source_runs=no_failed_unrecovered_source_runs,
        scheduler_assumed_disabled=scheduler_assumed_disabled,
        canonical_apply_assumed_disabled=canonical_apply_assumed_disabled,
        safe_to_proceed=not blockers,
        blockers=blockers,
        latest_source_run_key=latest_source_run_key,
        notes=notes,
    )


async def get_staging_impact_for_source_run(
    conn: Any,
    source_run_key: str,
    limit: int = DEFAULT_DIAGNOSTIC_ROW_LIMIT,
) -> OperatorStagingImpactSummary | None:
    bridge = await get_legacy_bridge_for_source_run(conn, source_run_key)
    if not bridge.bridge_present or bridge.legacy_source_run_id is None:
        return None
    return await get_staging_impact_for_bridge(conn, bridge.legacy_source_run_id, limit=limit)


def to_operator_visibility_dict(value: Any) -> Any:
    if is_dataclass(value):
        return {
            key: to_operator_visibility_dict(item)
            for key, item in asdict(value).items()
        }
    if isinstance(value, list):
        return [to_operator_visibility_dict(item) for item in value]
    if isinstance(value, tuple):
        return [to_operator_visibility_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_operator_visibility_dict(item) for key, item in value.items()}
    return value


async def _get_bridge_by_legacy_id(conn: Any, legacy_source_run_id: int) -> dict[str, Any] | None:
    bridge_row = await conn.fetchrow(
        """
        /* operator_visibility:get_bridge_by_legacy_id */
        SELECT
          id,
          source_run_key,
          dry_run,
          adapter_name,
          adapter_version,
          metadata
        FROM enrichment_source_runs
        WHERE id = $1
        """,
        legacy_source_run_id,
    )
    return _row_to_dict(bridge_row)


async def _get_source_runs_id(conn: Any, source_run_key: str | None) -> int | None:
    if not source_run_key:
        return None
    source_run_row = await conn.fetchrow(
        """
        /* operator_visibility:get_source_runs_id */
        SELECT id
        FROM source_runs
        WHERE source_run_key = $1
        """,
        source_run_key,
    )
    row = _row_to_dict(source_run_row)
    if row is None:
        return None
    value = row.get('id')
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _source_run_summary_from_row(row_value: Any) -> OperatorSourceRunSummary:
    row = _row_to_dict(row_value) or {}
    bridge_present = _bool(row.get('bridge_present'))
    return OperatorSourceRunSummary(
        source_run_key=str(row.get('source_run_key') or ''),
        source_name=_safe_text(row.get('source_name')),
        source_category=_safe_text(row.get('source_category')),
        domain=_safe_text(row.get('domain')),
        import_scope=_safe_text(row.get('import_scope')),
        status=_safe_text(row.get('status')),
        started_at=row.get('started_at'),
        finished_at=row.get('finished_at'),
        duration_ms=_int_or_none(row.get('duration_ms')),
        rows_read=_int_or_zero(row.get('rows_read')),
        rows_staged=_int_or_zero(row.get('rows_staged')),
        rows_rejected=_int_or_zero(row.get('rows_rejected')),
        rows_skipped=_int_or_zero(row.get('rows_skipped')),
        artifact_present=_bool(row.get('artifact_present')),
        artifact_hash_present=_bool(row.get('artifact_hash_present')),
        bridge_present=bridge_present,
        staging_rows_known=bridge_present,
        trigger_context=_safe_text(row.get('trigger_context')),
        git_commit_sha=_safe_text(row.get('git_commit_sha')),
        error_code=_safe_text(row.get('error_code')),
        error_summary=_safe_text(row.get('error_summary')),
    )


def _bridge_summary_from_row(row_value: Any, *, source_run_key: str) -> OperatorBridgeSummary:
    row = _row_to_dict(row_value) or {}
    metadata = _mapping(row.get('metadata'))
    staging_policy = _mapping(metadata.get('staging_policy'))
    return OperatorBridgeSummary(
        bridge_key=str(row.get('source_run_key') or _bridge_key(source_run_key)),
        legacy_source_run_id=_int_or_none(row.get('id')),
        source_run_key=source_run_key,
        bridge_present=True,
        dry_run=_bool_or_none(row.get('dry_run')),
        adapter_name=_safe_text(row.get('adapter_name')),
        adapter_version=_safe_text(row.get('adapter_version')),
        target_staging_fk=_safe_text(metadata.get('target_staging_fk')) or TARGET_STAGING_FK,
        metadata_has_compatibility_bridge=(
            metadata.get('compatibility_bridge') is True
            or metadata.get('schema_version') == BRIDGE_SCHEMA_VERSION
        ),
        staging_policy_blocks_source_runs_id=(
            staging_policy.get('do_not_pass_source_runs_id_to_legacy_staging_source_run_id') is True
        ),
    )


def _diagnostic_row_from_row(row_value: Any) -> OperatorDiagnosticRowSummary:
    row = _row_to_dict(row_value) or {}
    provenance = _mapping(row.get('provenance'))
    marker_keys = sorted(
        key for key in provenance.keys()
        if key == STAGE19ANR_MARKER_KEY or key.startswith('stage19')
    )
    canonical_allowed = provenance.get('canonical_write_allowed')
    return OperatorDiagnosticRowSummary(
        row_id=_int_or_zero(row.get('id')),
        legacy_source_run_id=_int_or_none(row.get('source_run_id')),
        station_name=_safe_text(row.get('station_name')),
        station_type=_safe_text(row.get('station_type')),
        system_name=_safe_text(row.get('system_name')),
        source_class=_safe_text(row.get('source_class')),
        confidence=_safe_text(row.get('confidence')),
        marker_keys=marker_keys,
        canonical_write_allowed=canonical_allowed if isinstance(canonical_allowed, bool) else None,
    )


def _detail_warnings(
    artifact: OperatorArtifactSummary,
    bridge: OperatorBridgeSummary,
    impact: OperatorStagingImpactSummary | None,
) -> list[str]:
    warnings: list[str] = []
    if artifact.artifact_path_redacted is None:
        warnings.append('source run has no artifact path in the ledger')
    if artifact.artifact_sha256 is None or artifact.artifact_integrity_sha256 is None:
        warnings.append('source run artifact hashes are incomplete')
    if not bridge.bridge_present:
        warnings.append('legacy enrichment_source_runs bridge is missing')
    elif not bridge.staging_policy_blocks_source_runs_id:
        warnings.append('bridge metadata does not record the source_runs.id staging block policy')
    if impact is not None and impact.rows_using_source_runs_id:
        warnings.append('staging rows exist under the new source_runs.id; legacy bridge FK path is suspect')
    return warnings


def _metadata_summary(metadata: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in (
        'stage',
        'operator_stage',
        'source_adapter',
        'bounded_rehearsal',
        'staging_rows_written_by_explicit_stager',
        'operation',
    ):
        if key in metadata:
            summary[key] = _safe_json_scalar(metadata.get(key))
    if isinstance(metadata.get('input_format'), Mapping):
        summary['input_format_keys'] = sorted(str(key) for key in metadata['input_format'].keys())
    artifact_record = _mapping(metadata.get('artifact_record'))
    if artifact_record:
        summary['artifact_record_present'] = True
        summary['artifact_record_keys'] = sorted(
            key for key in artifact_record.keys()
            if key not in {'path', 'artifact_path'}
        )
        if artifact_record.get('bytes_written') is not None:
            summary['artifact_bytes_written'] = _int_or_none(artifact_record.get('bytes_written'))
    return summary


def _operator_notes(metadata: Mapping[str, Any]) -> list[str]:
    raw_notes = metadata.get('operator_notes')
    if isinstance(raw_notes, str):
        note = _safe_text(raw_notes)
        return [note] if note else []
    if isinstance(raw_notes, Sequence) and not isinstance(raw_notes, (str, bytes, bytearray)):
        return [note for note in (_safe_text(item) for item in raw_notes) if note]
    return []


def _schema_version(metadata: Mapping[str, Any]) -> str | None:
    for key in ('schema_version', 'artifact_schema_version'):
        value = _safe_text(metadata.get(key))
        if value:
            return value
    return None


def _sanitized_mapping(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in _mapping(value).items():
        key_text = str(key)
        if _looks_sensitive_key(key_text):
            result[key_text] = '[redacted]'
        else:
            result[key_text] = _safe_json_scalar(item)
    return result


def _safe_json_scalar(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): ('[redacted]' if _looks_sensitive_key(str(key)) else _safe_json_scalar(item))
            for key, item in value.items()
            if str(key) not in {'path', 'artifact_path', 'source_path'}
        }
    if isinstance(value, list):
        return [_safe_json_scalar(item) for item in value[:10]]
    if isinstance(value, tuple):
        return [_safe_json_scalar(item) for item in value[:10]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _safe_text(value) if isinstance(value, str) else value
    return _safe_text(value)


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {key: row[key] for key in row.keys()}


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _bounded_limit(value: int | None, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, MAX_OPERATOR_VISIBILITY_LIMIT))


def _required_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f'{field} must be a positive integer')
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field} must be a positive integer') from exc
    if parsed < 1:
        raise ValueError(f'{field} must be a positive integer')
    return parsed


def _bridge_key(source_run_key: str) -> str:
    return f'{LEGACY_SOURCE_RUN_KEY_PREFIX}{source_run_key}'


def _source_run_key_from_bridge_key(bridge_key: str | None) -> str | None:
    if not bridge_key or not bridge_key.startswith(LEGACY_SOURCE_RUN_KEY_PREFIX):
        return None
    return bridge_key[len(LEGACY_SOURCE_RUN_KEY_PREFIX):]


def _redact_uri(value: Any) -> str | None:
    text = _safe_text(value)
    if not text:
        return None
    parsed = urlsplit(text)
    if parsed.scheme:
        filename = PurePosixPath(parsed.path).name
        if parsed.scheme == 'file':
            return f'file://.../{filename}' if filename else 'file://...'
        if parsed.username or parsed.password:
            return f'{parsed.scheme}://[redacted]/{filename}' if filename else f'{parsed.scheme}://[redacted]'
        return text
    return _redact_path(text)


def _redact_path(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    text = _SECRET_ASSIGNMENT_RE.sub(r'\1=[redacted]', text)
    normalised = text.replace('\\', '/')
    parsed = urlsplit(normalised)
    if parsed.scheme:
        return _redact_uri(normalised)
    path = PurePosixPath(normalised)
    parts = path.parts
    private_markers = {'home', 'Users', 'tmp', 'var'}
    if normalised.startswith('/') or normalised.startswith('~') or any(part in private_markers for part in parts):
        return f'.../{path.name}' if path.name else '...'
    if len(parts) > 3:
        return str(PurePosixPath(*parts[-2:]))
    return normalised


def _safe_text(value: Any, max_length: int = 400) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    text = _SECRET_ASSIGNMENT_RE.sub(r'\1=[redacted]', text)
    text = _PATH_IN_TEXT_RE.sub(lambda match: _redact_path(match.group(0)) or '[redacted]', text)
    if len(text) > max_length:
        return f'{text[:max_length - 3]}...'
    return text


def _looks_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in ('password', 'secret', 'token', 'api_key', 'apikey', 'dsn'))


def _int_or_zero(value: Any) -> int:
    parsed = _int_or_none(value)
    return parsed if parsed is not None else 0


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool:
    return value is True or value == 'true' or value == 't' or value == 1


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if value in ('true', 't', '1', 1):
        return True
    if value in ('false', 'f', '0', 0):
        return False
    return None


_SECRET_ASSIGNMENT_RE = re.compile(
    r'(?i)\b(password|secret|token|api_key|apikey|dsn)\s*=\s*[^,\s;]+'
)
_PATH_IN_TEXT_RE = re.compile(r'(?<![\w:/.-])(?:/home|/var|/tmp)/[^\s,;]+')


__all__ = [
    'MAX_OPERATOR_VISIBILITY_LIMIT',
    'OperatorArtifactSummary',
    'OperatorBridgeSummary',
    'OperatorDiagnosticRowSummary',
    'OperatorSafetyGateSummary',
    'OperatorSourceRunDetail',
    'OperatorSourceRunSummary',
    'OperatorStagingImpactSummary',
    'get_legacy_bridge_for_source_run',
    'get_operator_safety_gates',
    'get_source_run_artifacts',
    'get_source_run_detail',
    'get_staging_impact_for_bridge',
    'get_staging_impact_for_source_run',
    'list_diagnostic_staging_rows',
    'list_recent_source_runs',
    'to_operator_visibility_dict',
]
