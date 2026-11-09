"""Stage 6C comparison — summary.

Aggregates per-row comparisons into a top-level summary with an overall
status, a confidence-impact hint, and a human-readable summary string.

Both ``status`` and ``confidence_impact`` are **UI hints only**: Stage
6C never plumbs them back into Simulation Preview scoring or optimiser
ranking.
"""
from __future__ import annotations

from edfinder_api.observations.comparison_models import (
    ComparisonConfidenceImpact,
    ComparisonOverallStatus,
    ComparisonStatus,
    PredictionObservationComparison,
    PredictionObservationComparisonSummary,
)


def build_summary(
    *,
    observed_facts_count: int,
    comparisons: list[PredictionObservationComparison],
) -> PredictionObservationComparisonSummary:
    confirmed = sum(1 for c in comparisons if c.status == ComparisonStatus.CONFIRMED.value)
    contradicted = sum(1 for c in comparisons if c.status == ComparisonStatus.CONTRADICTED.value)
    observed_only = sum(1 for c in comparisons if c.status == ComparisonStatus.OBSERVED_ONLY.value)
    predicted_only = sum(1 for c in comparisons if c.status == ComparisonStatus.PREDICTED_ONLY.value)
    unknown = sum(1 for c in comparisons if c.status == ComparisonStatus.UNKNOWN.value)
    unverified = sum(1 for c in comparisons if c.status == ComparisonStatus.UNVERIFIED.value)

    if observed_facts_count == 0:
        overall = ComparisonOverallStatus.NO_OBSERVATIONS
    elif contradicted > 0 and confirmed > 0:
        overall = ComparisonOverallStatus.MIXED
    elif contradicted > 0:
        overall = ComparisonOverallStatus.NEEDS_REVIEW
    elif confirmed > 0:
        overall = ComparisonOverallStatus.CONFIRMED
    else:
        overall = ComparisonOverallStatus.INSUFFICIENT_EVIDENCE

    if observed_facts_count == 0:
        impact = ComparisonConfidenceImpact.NONE
    elif confirmed > 0 and contradicted == 0:
        impact = ComparisonConfidenceImpact.STRENGTHENED
    elif contradicted > 0 and confirmed == 0:
        impact = ComparisonConfidenceImpact.WEAKENED
    elif confirmed > 0 and contradicted > 0:
        impact = ComparisonConfidenceImpact.MIXED
    else:
        impact = ComparisonConfidenceImpact.INSUFFICIENT_EVIDENCE

    summary_text = _summary_text(
        overall=overall,
        confirmed=confirmed,
        contradicted=contradicted,
        observed_only=observed_only,
        predicted_only=predicted_only,
        unverified=unverified,
    )

    return PredictionObservationComparisonSummary(
        status=overall.value,
        observed_facts_count=observed_facts_count,
        compared_predictions_count=len(comparisons),
        confirmed_count=confirmed,
        contradicted_count=contradicted,
        observed_only_count=observed_only,
        predicted_only_count=predicted_only,
        unknown_count=unknown,
        unverified_count=unverified,
        confidence_impact=impact.value,
        summary=summary_text,
    )


def _summary_text(
    *,
    overall: ComparisonOverallStatus,
    confirmed: int,
    contradicted: int,
    observed_only: int,
    predicted_only: int,
    unverified: int,
) -> str:
    if overall == ComparisonOverallStatus.NO_OBSERVATIONS:
        return 'No observed evidence recorded for this system yet. Comparison shows prediction-only rows.'
    parts: list[str] = []
    if confirmed:
        parts.append(f'{confirmed} confirmed')
    if contradicted:
        parts.append(f'{contradicted} contradicted (review)')
    if observed_only:
        parts.append(f'{observed_only} observed-only')
    if predicted_only:
        parts.append(f'{predicted_only} predicted-only')
    if unverified:
        parts.append(f'{unverified} unverified')
    head = {
        ComparisonOverallStatus.CONFIRMED: 'Observations support the prediction',
        ComparisonOverallStatus.MIXED: 'Observations partially support the prediction — some entries need review',
        ComparisonOverallStatus.NEEDS_REVIEW: 'Observations contradict parts of the prediction — review recommended',
        ComparisonOverallStatus.INSUFFICIENT_EVIDENCE: 'Insufficient evidence to confirm or contradict the prediction',
    }.get(overall, 'Comparison complete')
    if parts:
        return f'{head}: {", ".join(parts)}.'
    return f'{head}.'


__all__ = ['build_summary']
