"""Rank generated recommended build plans by simulated result quality."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from edfinder_api.mechanics.scoring_rules import (
    COMPLEXITY_PENALTY,
    MAX_WARNING_PENALTY,
    REGIONAL_RECOMMENDATION_WEIGHT,
    RANK_SCORE_WEIGHTS,
    WARNING_PENALTY_PER_WARNING,
)
from edfinder_api.models import SimulateBuildResponse
from edfinder_api.recommendations.build_generator import BuildPlanDraft


@dataclass(frozen=True)
class RankedPlan:
    draft: BuildPlanDraft
    simulation: SimulateBuildResponse
    rank_score: float
    rank_breakdown: dict[str, float]
    regional_fit: float = 0.0


def rank_plans(
    items: list[tuple[BuildPlanDraft, SimulateBuildResponse]],
    *,
    archetype: Optional[str] = None,
    regional_context: Optional[dict] = None,
    regional_fit_by_plan: Optional[dict[str, float]] = None,
) -> list[RankedPlan]:
    ranked = []
    for draft, simulation in items:
        regional_fit = _regional_fit_for_plan(
            draft=draft,
            archetype=archetype,
            regional_context=regional_context,
            regional_fit_by_plan=regional_fit_by_plan,
        )
        breakdown = rank_breakdown(simulation, regional_fit=regional_fit)
        ranked.append(RankedPlan(
            draft=draft,
            simulation=simulation,
            rank_score=breakdown['final_rank_score'],
            rank_breakdown=breakdown,
            regional_fit=regional_fit,
        ))
    return sorted(ranked, key=lambda item: item.rank_score, reverse=True)


def _rank_score(simulation: SimulateBuildResponse, *, regional_fit: float = 0.0) -> float:
    return rank_breakdown(simulation, regional_fit=regional_fit)['final_rank_score']


def rank_breakdown(simulation: SimulateBuildResponse, *, regional_fit: float = 0.0) -> dict[str, float]:
    warning_penalty = min(MAX_WARNING_PENALTY, len(simulation.warnings) * WARNING_PENALTY_PER_WARNING)
    complexity_penalty = COMPLEXITY_PENALTY[simulation.build_complexity]
    simulation_component = simulation.final_score * RANK_SCORE_WEIGHTS['simulation_score']
    economy_component = simulation.composition_score * RANK_SCORE_WEIGHTS['economy_stack_score']
    buildability_component = simulation.buildability_score * RANK_SCORE_WEIGHTS['buildability_score']
    confidence_penalty = max(0.0, (1.0 - simulation.confidence) * RANK_SCORE_WEIGHTS['confidence_bonus'])
    # Confidence is a small bonus reduced by a low-confidence penalty, not a
    # full 0-100 weighted score. Local simulation quality stays dominant.
    confidence_component = RANK_SCORE_WEIGHTS['confidence_bonus'] - confidence_penalty
    regional_component = regional_fit * REGIONAL_RECOMMENDATION_WEIGHT
    # Reserved for service-aware ranking v2. Service unlocks are exposed today,
    # but they do not yet move recommendation order.
    service_component = 0.0
    final = (
        simulation_component
        + economy_component
        + buildability_component
        + confidence_component
        + regional_component
        + service_component
        - warning_penalty
        - complexity_penalty
    )
    return {
        'simulation_score': round(simulation_component, 2),
        'economy_stack_score': round(economy_component, 2),
        'buildability_score': round(buildability_component, 2),
        'regional_fit_score': round(regional_component, 2),
        'service_score': round(service_component, 2),
        'confidence_penalty': round(confidence_penalty, 2),
        'complexity_penalty': round(complexity_penalty, 2),
        'warning_penalty': round(warning_penalty, 2),
        'final_rank_score': round(final, 2),
    }


def _regional_fit_for_plan(
    *,
    draft: BuildPlanDraft,
    archetype: Optional[str],
    regional_context: Optional[dict],
    regional_fit_by_plan: Optional[dict[str, float]],
) -> float:
    if regional_fit_by_plan and draft.id in regional_fit_by_plan:
        return _clamp_fit(float(regional_fit_by_plan[draft.id] or 0.0))
    if not archetype or not regional_context:
        return 0.0
    fit = regional_context.get('archetype_regional_fit') or {}
    if not isinstance(fit, dict):
        return 0.0
    return _clamp_fit(float(fit.get(archetype, 0.0) or 0.0))


def _clamp_fit(value: float) -> float:
    return max(0.0, min(100.0, value))
