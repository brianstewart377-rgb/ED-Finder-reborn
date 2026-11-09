"""Public orchestration for Stage 6E validation review guidance."""
from __future__ import annotations

from edfinder_api.observations.comparison_models import PredictionObservationComparisonResult
from edfinder_api.observations.review.rules import build_review_signals
from edfinder_api.observations.review.shared import bucket_comparisons
from edfinder_api.observations.review.summary import build_review_summary
from edfinder_api.observations.review_models import ValidationReviewResult


def build_validation_review(
    *,
    comparison_result: PredictionObservationComparisonResult,
) -> ValidationReviewResult:
    """Build conservative review guidance from a Stage 6C comparison.

    The returned guidance describes areas that may need investigation.
    It never feeds anything back into mechanics, predictions, scoring, or
    confidence weights.
    """
    buckets = bucket_comparisons(comparison_result)
    signals = build_review_signals(
        comparison_result=comparison_result,
        buckets=buckets,
    )
    summary = build_review_summary(
        comparison_result=comparison_result,
        buckets=buckets,
        signals=signals,
    )
    return ValidationReviewResult(
        system_id64=comparison_result.system_id64,
        target_archetype=comparison_result.target_archetype,
        generated_at=comparison_result.generated_at,
        summary=summary,
        signals=signals,
        warnings=list(comparison_result.warnings),
        assumptions=list(comparison_result.assumptions),
    )


__all__ = ["build_validation_review"]
