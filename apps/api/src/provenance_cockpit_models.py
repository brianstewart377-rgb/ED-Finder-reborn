from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


ProvenanceState = Literal['available', 'stale', 'unknown']


class ProvenanceCockpitSystem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id64: int
    name: str | None = None
    primary_archetype: str | None = None


class ProvenanceSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')

    state: ProvenanceState
    latest_source_run_key: str | None = None
    warehouse_state: ProvenanceState
    planner_evidence_state: ProvenanceState


class SourceRunEvidencePanel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    state: ProvenanceState
    source_name: str | None = None
    rows_read: int | None = None
    rows_staged: int | None = None
    artifact_name: str | None = None


class WarehouseEvidencePanel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    state: ProvenanceState
    report_only: bool
    canonical_writes_planned: int
    stale_records: int | None = None


class PlannerEvidencePanel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    state: ProvenanceState
    observed_facts_count: int
    projected_build_count: int
    manual_review_required: bool


class EvidencePanels(BaseModel):
    model_config = ConfigDict(extra='forbid')

    source_run: SourceRunEvidencePanel
    warehouse: WarehouseEvidencePanel
    planner: PlannerEvidencePanel


class GuardrailsSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')

    stage19_paused: bool
    stage19_production_activation_complete: bool
    next_stage19_write_lane_authorized: bool
    canonical_apply_complete: bool
    rebaseline_complete: bool
    scheduler_enabled: bool
    db_writes_authorized: bool
    stage19_operator_commands_authorized: bool


class UiHints(BaseModel):
    model_config = ConfigDict(extra='forbid')

    severity: Literal['info', 'warning', 'neutral']
    empty_state_key: str | None = None


class ProvenanceCockpitResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    schema_version: Literal['stage20a_provenance_cockpit/v1']
    system: ProvenanceCockpitSystem
    provenance_summary: ProvenanceSummary
    evidence_panels: EvidencePanels
    guardrails: GuardrailsSummary
    warnings: list[str]
    ui_hints: UiHints
