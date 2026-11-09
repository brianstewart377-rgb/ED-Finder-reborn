"""Deterministic Stage 5B ranking for Stage 5A optimiser candidates.

The ranker consumes already-generated candidates and their lightweight preview
summaries. It does not call Simulation Preview, mutate candidates, or expand the
candidate search space.
"""
from __future__ import annotations

from edfinder_api.optimiser.models import (
    CandidateRankBreakdown,
    CandidateRankingResult,
    OptimiserCandidate,
    RankedOptimiserCandidate,
)

_STRATEGY_MODIFIERS = {
    'main_station': 4.5,
    'balanced_expansion': 4.25,
    'industrial_refinery': 4.0,
    'tourism_agriculture': 3.75,
    'military_security': 3.5,
    'support_body': 3.25,
    'primary_port_bootstrap': -2.0,
    'balanced': 4.0,
    'pure': 3.0,
    'services_aware': 2.5,
    'low_cp': 2.0,
    'flexible_multirole': 1.0,
}

_ALIGNMENT_MODIFIERS = {
    'strong': 5.0,
    'good': 4.0,
    'moderate': 3.0,
    'partial': 2.0,
    'weak': 0.5,
    'poor': 0.0,
}


def rank_candidates(
    candidates: list[OptimiserCandidate],
    *,
    target_archetype: str,
) -> CandidateRankingResult:
    """Rank candidates deterministically without mutating the input list."""
    ranked_without_positions = [
        _score_candidate(candidate)
        for candidate in candidates
    ]
    ranked_without_positions.sort(
        key=lambda item: (-item.rank_score, item.candidate_id)
    )
    ranked = [
        RankedOptimiserCandidate(
            candidate_id=item.candidate_id,
            rank=index,
            rank_score=item.rank_score,
            rank_tier=item.rank_tier,
            rank_breakdown=item.rank_breakdown,
        )
        for index, item in enumerate(ranked_without_positions, start=1)
    ]
    return CandidateRankingResult(
        target_archetype=target_archetype,
        ranked_candidates=ranked,
        warnings=[],
        assumptions=[
            'Stage 5B ranking is deterministic and heuristic; Simulation Preview remains the source of truth.',
            'Ranking uses Stage 5A candidate metadata and lightweight preview summaries only.',
        ],
    )


def _score_candidate(candidate: OptimiserCandidate) -> RankedOptimiserCandidate:
    summary = candidate.preview_summary
    reasons: list[str] = []
    preview_score_component = 0.0
    composition_component = 0.0
    buildability_component = 0.0
    confidence_component = 0.0
    alignment_component = 0.0
    missing_preview_penalty = 0.0

    if summary is None:
        missing_preview_penalty = -20.0
        reasons.append('Candidate has not been preview-scored; ranking confidence is reduced.')
    else:
        preview_score_component = _scale(summary.final_score, 35.0)
        composition_component = _scale(summary.composition_score, 20.0)
        buildability_component = _scale(summary.buildability_score, 20.0)
        confidence_component = _scale_confidence(summary.confidence, 15.0)
        alignment_component = _alignment_component(summary.top_two_alignment)
        if summary.confidence is not None and summary.confidence < 0.5:
            reasons.append('Preview confidence is low.')
        if summary.warnings_count:
            reasons.append(f'Preview returned {summary.warnings_count} warning(s).')
        if summary.cp_negative:
            reasons.append('Preview indicates negative CP pressure.')

    candidate_warning_penalty = -min(12.0, len(candidate.warnings) * 3.0)
    if candidate.warnings:
        reasons.append(f'Candidate has {len(candidate.warnings)} candidate warning(s).')

    preview_warning_penalty = 0.0
    cp_penalty = 0.0
    if summary is not None:
        preview_warning_penalty = -min(10.0, summary.warnings_count * 2.0)
        cp_penalty = -12.0 if summary.cp_negative else 0.0

    strategy_modifier = _STRATEGY_MODIFIERS.get(candidate.strategy, 0.0)
    total = _clamp(
        preview_score_component
        + composition_component
        + buildability_component
        + confidence_component
        + alignment_component
        + strategy_modifier
        + preview_warning_penalty
        + candidate_warning_penalty
        + cp_penalty
        + missing_preview_penalty,
        0.0,
        100.0,
    )
    total = round(total, 2)

    breakdown = CandidateRankBreakdown(
        preview_score_component=round(preview_score_component, 2),
        composition_component=round(composition_component, 2),
        buildability_component=round(buildability_component, 2),
        confidence_component=round(confidence_component, 2),
        alignment_component=round(alignment_component, 2),
        warning_penalty=round(preview_warning_penalty + candidate_warning_penalty + missing_preview_penalty, 2),
        cp_penalty=round(cp_penalty, 2),
        strategy_modifier=round(strategy_modifier, 2),
        total_score=total,
        reasons=reasons,
    )
    return RankedOptimiserCandidate(
        candidate_id=candidate.candidate_id,
        rank=0,
        rank_score=total,
        rank_tier=_tier(total),
        rank_breakdown=breakdown,
    )


def _scale(value: float | None, max_points: float) -> float:
    if value is None:
        return 0.0
    return _clamp(value, 0.0, 100.0) / 100.0 * max_points


def _scale_confidence(value: float | None, max_points: float) -> float:
    if value is None:
        return 0.0
    if value <= 1.0:
        return _clamp(value, 0.0, 1.0) * max_points
    return _clamp(value, 0.0, 100.0) / 100.0 * max_points


def _alignment_component(value: str | None) -> float:
    if value is None:
        return 0.0
    return _ALIGNMENT_MODIFIERS.get(str(value).lower(), 0.0)


def _tier(score: float) -> str:
    if score >= 85.0:
        return 'excellent'
    if score >= 70.0:
        return 'strong'
    if score >= 55.0:
        return 'viable'
    if score >= 40.0:
        return 'risky'
    return 'weak'


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
