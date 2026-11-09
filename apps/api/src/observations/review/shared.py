"""Shared buckets and small helpers for Stage 6E review rules."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

from edfinder_api.observations.comparison_models import (
    ComparisonArea,
    ComparisonStatus,
    PredictionObservationComparison,
    PredictionObservationComparisonResult,
)
from edfinder_api.observations.review.severity import is_low_confidence


@dataclass(frozen=True)
class ReviewBuckets:
    comparisons: list[PredictionObservationComparison]
    contradictions: list[PredictionObservationComparison]
    low_confidence_contradictions: list[PredictionObservationComparison]
    actionable_contradictions: list[PredictionObservationComparison]
    confirmed: list[PredictionObservationComparison]
    observed_only: list[PredictionObservationComparison]
    predicted_only: list[PredictionObservationComparison]
    facility_observed_only: list[PredictionObservationComparison]


def bucket_comparisons(
    comparison_result: PredictionObservationComparisonResult,
) -> ReviewBuckets:
    comparisons = list(comparison_result.comparisons)
    contradictions = [
        comparison
        for comparison in comparisons
        if comparison.status == ComparisonStatus.CONTRADICTED.value
    ]
    low_confidence_contradictions = [
        comparison for comparison in contradictions if is_low_confidence(comparison)
    ]
    actionable_contradictions = [
        comparison for comparison in contradictions if not is_low_confidence(comparison)
    ]
    confirmed = [
        comparison
        for comparison in comparisons
        if comparison.status == ComparisonStatus.CONFIRMED.value
    ]
    observed_only = [
        comparison
        for comparison in comparisons
        if comparison.status == ComparisonStatus.OBSERVED_ONLY.value
    ]
    predicted_only = [
        comparison
        for comparison in comparisons
        if comparison.status == ComparisonStatus.PREDICTED_ONLY.value
    ]
    facility_observed_only = [
        comparison
        for comparison in observed_only
        if comparison.area == ComparisonArea.FACILITY.value
    ]
    return ReviewBuckets(
        comparisons=comparisons,
        contradictions=contradictions,
        low_confidence_contradictions=low_confidence_contradictions,
        actionable_contradictions=actionable_contradictions,
        confirmed=confirmed,
        observed_only=observed_only,
        predicted_only=predicted_only,
        facility_observed_only=facility_observed_only,
    )


def comparison_ids(
    comparisons: Iterable[PredictionObservationComparison],
    *,
    limit: int | None = None,
) -> list[str]:
    picked = list(comparisons)
    if limit is not None:
        picked = picked[:limit]
    return [comparison.comparison_id for comparison in picked]


def predicted_only_heavy(
    comparison_result: PredictionObservationComparisonResult,
    buckets: ReviewBuckets,
) -> bool:
    if not buckets.predicted_only:
        return False
    if comparison_result.summary.observed_facts_count == 0:
        return True
    checked = len(buckets.confirmed) + len(buckets.contradictions)
    return len(buckets.predicted_only) >= 3 and len(buckets.predicted_only) > checked


def observed_only_heavy(buckets: ReviewBuckets) -> bool:
    if not buckets.observed_only:
        return False
    checked = len(buckets.confirmed) + len(buckets.contradictions)
    return len(buckets.observed_only) >= 3 and len(buckets.observed_only) >= checked


__all__ = [
    "ReviewBuckets",
    "bucket_comparisons",
    "comparison_ids",
    "observed_only_heavy",
    "predicted_only_heavy",
]
