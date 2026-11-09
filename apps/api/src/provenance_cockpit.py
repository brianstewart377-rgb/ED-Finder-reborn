from __future__ import annotations

import json
from functools import lru_cache
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Mapping

from edfinder_api.provenance_cockpit_models import (
    EvidencePanels,
    GuardrailsSummary,
    PlannerEvidencePanel,
    ProvenanceCockpitResponse,
    ProvenanceCockpitSystem,
    ProvenanceSummary,
    SourceRunEvidencePanel,
    UiHints,
    WarehouseEvidencePanel,
)


def _resolve_authority_path() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        path = candidate / 'docs' / 'colonisation-redesign' / 'stage-19-state-authority.json'
        if path.is_file():
            return path
    return here.parent / 'docs' / 'colonisation-redesign' / 'stage-19-state-authority.json'


AUTHORITY_PATH = _resolve_authority_path()
SCHEMA_VERSION = 'stage20a_provenance_cockpit/v1'


def build_provenance_cockpit(id64: int) -> ProvenanceCockpitResponse:
    authority, authority_warning = _load_authority_snapshot()
    current_safety_state = _current_safety_state(authority)
    warnings = _safe_warning_rows([authority_warning])
    return ProvenanceCockpitResponse(
        schema_version=SCHEMA_VERSION,
        system=ProvenanceCockpitSystem(
            id64=id64,
            name=None,
            primary_archetype='unknown',
        ),
        provenance_summary=ProvenanceSummary(
            state='unknown',
            latest_source_run_key=None,
            warehouse_state='unknown',
            planner_evidence_state='unknown',
        ),
        evidence_panels=EvidencePanels(
            source_run=SourceRunEvidencePanel(
                state='unknown',
                source_name=None,
                rows_read=None,
                rows_staged=None,
                artifact_name=None,
            ),
            warehouse=WarehouseEvidencePanel(
                state='unknown',
                report_only=True,
                canonical_writes_planned=0,
                stale_records=None,
            ),
            planner=PlannerEvidencePanel(
                state='unknown',
                observed_facts_count=0,
                projected_build_count=0,
                manual_review_required=True,
            ),
        ),
        guardrails=_guardrails(current_safety_state),
        warnings=[
            'No provenance artifact is configured for this system yet; unknown values remain unknown.',
            *warnings,
        ],
        ui_hints=UiHints(
            severity='neutral',
            empty_state_key='provenance.unknown',
        ),
    )


def _guardrails(stage20: Mapping[str, Any]) -> GuardrailsSummary:
    return GuardrailsSummary(
        stage19_paused=_bool_or_default(stage20.get('stage19_remains_paused'), True),
        stage19_production_activation_complete=bool(stage20.get('stage19_production_activation_complete')),
        next_stage19_write_lane_authorized=bool(stage20.get('next_stage19_write_lane_authorized')),
        canonical_apply_complete=bool(stage20.get('canonical_apply_complete')),
        rebaseline_complete=bool(stage20.get('rebaseline_complete')),
        scheduler_enabled=bool(stage20.get('scheduler_enabled')),
        db_writes_authorized=bool(stage20.get('db_writes_authorized')),
        stage19_operator_commands_authorized=bool(stage20.get('stage19_operator_commands_authorized')),
    )
@lru_cache(maxsize=1)
def _load_authority_snapshot() -> tuple[dict[str, Any], str | None]:
    try:
        return json.loads(AUTHORITY_PATH.read_text(encoding='utf-8')), None
    except FileNotFoundError:
        return {}, 'Current authority snapshot is unavailable; global safety status is shown conservatively.'
    except JSONDecodeError:
        return {}, 'Current authority snapshot is malformed; global safety status is shown conservatively.'


def _current_safety_state(authority: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ('stage22', 'stage21', 'stage20'):
        value = authority.get(key)
        if isinstance(value, Mapping):
            return value
    return {}
def _bool_or_default(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _safe_warning_rows(value: Any) -> list[str]:
    if isinstance(value, list):
        rows = [_text_or_none(item) for item in value]
        return [row for row in rows if row]
    text = _text_or_none(value)
    return [text] if text else []
