"""High-level deterministic signal rules for Stage 6E review guidance."""
from __future__ import annotations

from edfinder_api.observations.comparison_models import PredictionObservationComparisonResult
from edfinder_api.observations.review.areas import group_actionable_contradictions
from edfinder_api.observations.review.shared import (
    ReviewBuckets,
    observed_only_heavy,
    predicted_only_heavy,
)
from edfinder_api.observations.review.signals import (
    confirmed_only_signal,
    contradiction_area_signal,
    facility_observed_only_signal,
    fallback_signal,
    low_confidence_contradiction_signal,
    mixed_evidence_signal,
    no_evidence_signal,
    observed_only_heavy_signal,
    predicted_only_heavy_signal,
)
from edfinder_api.observations.review_models import ValidationReviewSignal


def build_review_signals(
    *,
    comparison_result: PredictionObservationComparisonResult,
    buckets: ReviewBuckets,
) -> list[ValidationReviewSignal]:
    signals: list[ValidationReviewSignal] = []

    if comparison_result.summary.observed_facts_count == 0:
        signals.append(no_evidence_signal())

    if buckets.low_confidence_contradictions:
        signals.append(
            low_confidence_contradiction_signal(buckets.low_confidence_contradictions)
        )

    grouped = group_actionable_contradictions(buckets.actionable_contradictions)
    for area, comparisons in grouped.items():
        signals.append(contradiction_area_signal(area, comparisons))

    if buckets.facility_observed_only:
        signals.append(facility_observed_only_signal(buckets.facility_observed_only))

    if predicted_only_heavy(comparison_result, buckets):
        signals.append(predicted_only_heavy_signal(buckets.predicted_only))

    if observed_only_heavy(buckets):
        signals.append(observed_only_heavy_signal(buckets.observed_only))

    if buckets.confirmed and buckets.contradictions:
        signals.append(
            mixed_evidence_signal(
                (buckets.confirmed + buckets.contradictions)[:20]
            )
        )

    if buckets.confirmed and not buckets.contradictions and not signals:
        signals.append(confirmed_only_signal(buckets.confirmed))

    if not signals:
        signals.append(fallback_signal())

    return signals


__all__ = ["build_review_signals"]
