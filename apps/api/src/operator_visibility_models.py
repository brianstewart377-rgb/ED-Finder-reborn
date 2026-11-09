from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_OPERATOR_VISIBILITY_LIMIT = 100
DEFAULT_RECENT_SOURCE_RUN_LIMIT = 25
DEFAULT_DIAGNOSTIC_ROW_LIMIT = 100
LEGACY_SOURCE_RUN_KEY_PREFIX = 'source_runs:'
TARGET_STAGING_FK = 'enrichment_source_runs(id)'
BRIDGE_SCHEMA_VERSION = 'source_run_legacy_compatibility/v1'
STAGING_TABLE = 'staging_edsm_stations'
DIAGNOSTIC_ONLY = 'diagnostic-only'
STAGE19ANR_MARKER_KEY = 'stage19anr_diagnostic_mark'
PILOT_SOURCE_NAME = 'edsm'
PILOT_DOMAIN = 'stations'
PILOT_IMPORT_SCOPE = 'staging_only'


@dataclass(frozen=True)
class OperatorSourceRunSummary:
    source_run_key: str
    source_name: str | None
    source_category: str | None
    domain: str | None
    import_scope: str | None
    status: str | None
    started_at: Any
    finished_at: Any
    duration_ms: int | None
    rows_read: int
    rows_staged: int
    rows_rejected: int
    rows_skipped: int
    artifact_present: bool
    artifact_hash_present: bool
    bridge_present: bool
    staging_rows_known: bool
    trigger_context: str | None
    git_commit_sha: str | None
    error_code: str | None
    error_summary: str | None


@dataclass(frozen=True)
class OperatorArtifactSummary:
    source_run_key: str
    artifact_path_redacted: str | None
    artifact_sha256: str | None
    artifact_integrity_sha256: str | None
    artifact_record_present: bool
    file_exists: bool | None
    file_sha256_matches: bool | None
    integrity_hash_matches: bool | None
    schema_version: str | None
    rows_read: int
    rows_staged: int
    status: str | None
    validation_note: str


@dataclass(frozen=True)
class OperatorBridgeSummary:
    bridge_key: str
    legacy_source_run_id: int | None
    source_run_key: str
    bridge_present: bool
    dry_run: bool | None
    adapter_name: str | None
    adapter_version: str | None
    target_staging_fk: str
    metadata_has_compatibility_bridge: bool
    staging_policy_blocks_source_runs_id: bool


@dataclass(frozen=True)
class OperatorDiagnosticRowSummary:
    row_id: int
    legacy_source_run_id: int | None
    station_name: str | None
    station_type: str | None
    system_name: str | None
    source_class: str | None
    confidence: str | None
    marker_keys: list[str]
    canonical_write_allowed: bool | None


@dataclass(frozen=True)
class OperatorStagingImpactSummary:
    source_run_key: str | None
    bridge_key: str | None
    legacy_source_run_id: int
    staging_table: str
    rows_total: int
    rows_diagnostic_only: int
    rows_canonical_write_blocked: int
    rows_with_stage_markers: int
    rows_using_legacy_bridge_id: int
    rows_using_source_runs_id: int
    sample_rows: list[OperatorDiagnosticRowSummary]
    warnings: list[str]


@dataclass(frozen=True)
class OperatorSourceRunDetail:
    summary: OperatorSourceRunSummary
    importer_name: str | None
    importer_version: str | None
    source_uri_redacted: str | None
    source_input_sha256: str | None
    source_manifest_sha256: str | None
    safety_boundary: dict[str, Any]
    metadata_summary: dict[str, Any]
    artifact_summary: OperatorArtifactSummary
    bridge_summary: OperatorBridgeSummary
    staging_impact_summary: OperatorStagingImpactSummary | None
    validation_warnings: list[str]
    operator_notes: list[str]


@dataclass(frozen=True)
class OperatorSafetyGateSummary:
    no_running_source_runs: bool
    latest_artifacts_present: bool
    bridge_fk_path_verified: bool
    diagnostic_rows_isolated: bool
    no_failed_unrecovered_source_runs: bool
    scheduler_assumed_disabled: bool
    canonical_apply_assumed_disabled: bool
    safe_to_proceed: bool
    blockers: list[str]
    latest_source_run_key: str | None
    notes: list[str]
