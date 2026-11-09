from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


FULL_RECONCILIATION_SCHEMA_VERSION = 'enrichment_staging_reconciliation/v1'
COMPACT_RECONCILIATION_SCHEMA_VERSION = 'enrichment_reconciliation_artifact_summary/v1'


class _BaseArtifactContract(BaseModel):
    model_config = ConfigDict(extra='allow')


class FullWarehouseStatusArtifact(_BaseArtifactContract):
    schema_version: Literal['enrichment_staging_reconciliation/v1']
    dry_run: bool | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any]
    source_coverage_summary: dict[str, Any]
    warehouse_coverage_report: dict[str, Any]
    confidence_risk_summary: dict[str, Any]
    warnings: list[Any]
    errors: list[Any]


class CompactWarehouseStatusArtifact(_BaseArtifactContract):
    schema_version: Literal['enrichment_reconciliation_artifact_summary/v1']
    artifact_schema_version: Literal['enrichment_staging_reconciliation/v1']
    safe_for_git: bool
    source_artifact_basename: str
    source_artifact_sha256: str = Field(min_length=64, max_length=64)
    source_artifact_size_bytes: int = Field(ge=0)
    canonical_writes_planned: int | None = Field(default=None, ge=0)
    artifact_summary_counts: dict[str, Any] = Field(default_factory=dict)
    candidate_counts: dict[str, Any] = Field(default_factory=dict)
    candidate_action_counts: dict[str, Any] = Field(default_factory=dict)
    candidate_samples: dict[str, Any] = Field(default_factory=dict)
    confidence_risk_counts: dict[str, Any] = Field(default_factory=dict)
    source_coverage_summary_counts: dict[str, Any] = Field(default_factory=dict)
    warehouse_coverage_summary_counts: dict[str, Any] = Field(default_factory=dict)


WAREHOUSE_STATUS_ARTIFACT_ADAPTER = TypeAdapter(
    FullWarehouseStatusArtifact | CompactWarehouseStatusArtifact,
)


def validate_warehouse_status_artifact(payload: Any) -> FullWarehouseStatusArtifact | CompactWarehouseStatusArtifact:
    return WAREHOUSE_STATUS_ARTIFACT_ADAPTER.validate_python(payload)


def validate_compact_warehouse_status_artifact(payload: Any) -> CompactWarehouseStatusArtifact:
    return CompactWarehouseStatusArtifact.model_validate(payload)


def warehouse_status_artifact_json_schemas() -> dict[str, dict[str, Any]]:
    return {
        FULL_RECONCILIATION_SCHEMA_VERSION: FullWarehouseStatusArtifact.model_json_schema(),
        COMPACT_RECONCILIATION_SCHEMA_VERSION: CompactWarehouseStatusArtifact.model_json_schema(),
    }
