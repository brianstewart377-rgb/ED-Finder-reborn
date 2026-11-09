"""Summary rules for Stage 6E validation review guidance."""
from __future__ import annotations

from edfinder_api.observations.comparison_models import PredictionObservationComparisonResult
from edfinder_api.observations.review.areas import area_phrase, primary_areas
from edfinder_api.observations.review.severity import highest_severity
from edfinder_api.observations.review.shared import ReviewBuckets
from edfinder_api.observations.review_models import (
    EvidenceStrength,
    ReviewStatus,
    ValidationReviewSignal,
    ValidationReviewSummary,
)


def build_review_summary(
    *,
    comparison_result: PredictionObservationComparisonResult,
    buckets: ReviewBuckets,
    signals: list[ValidationReviewSignal],
) -> ValidationReviewSummary:
    highest = (
        highest_severity(buckets.comparisons) if buckets.comparisons else "none"
    )
    review_needed_count = sum(
        1
        for signal in signals
        if signal.status
        in {
            ReviewStatus.REVIEW_RECOMMENDED.value,
            ReviewStatus.REVIEW_HIGH_PRIORITY.value,
        }
    )
    areas = primary_areas(signals)

    if comparison_result.summary.observed_facts_count == 0:
        overall = ReviewStatus.INSUFFICIENT_EVIDENCE.value
        strength = EvidenceStrength.NONE.value
        summary_text = "No observed evidence has been recorded yet. Record evidence before reviewing prediction quality."
    elif any(signal.status == ReviewStatus.REVIEW_HIGH_PRIORITY.value for signal in signals):
        overall = ReviewStatus.REVIEW_HIGH_PRIORITY.value
        strength = EvidenceStrength.STRONG.value
        summary_text = (
            "Validation includes high-confidence needs-review rows. "
            f"Review {area_phrase(areas)} first."
        )
    elif buckets.confirmed and buckets.contradictions:
        overall = ReviewStatus.MIXED_EVIDENCE.value
        strength = EvidenceStrength.MIXED.value
        summary_text = "Evidence is mixed: some predictions are confirmed while others need review."
    elif any(signal.status == ReviewStatus.REVIEW_RECOMMENDED.value for signal in signals):
        overall = ReviewStatus.REVIEW_RECOMMENDED.value
        strength = EvidenceStrength.MODERATE.value
        summary_text = (
            "Validation includes needs-review rows. "
            f"Review {area_phrase(areas)} next."
        )
    elif buckets.confirmed and not buckets.contradictions:
        overall = ReviewStatus.NO_ACTION.value
        strength = (
            EvidenceStrength.STRONG.value
            if len(buckets.confirmed) >= 3
            else EvidenceStrength.MODERATE.value
        )
        summary_text = "Current observed evidence mostly supports the prediction. Continue gathering observations."
    elif buckets.observed_only:
        overall = ReviewStatus.MONITOR.value
        strength = EvidenceStrength.WEAK.value
        summary_text = "Observed evidence is present but not yet matched to prediction rows. Review coverage before changing assumptions."
    elif comparison_result.summary.predicted_only_count > 0:
        overall = ReviewStatus.INSUFFICIENT_EVIDENCE.value
        strength = EvidenceStrength.WEAK.value
        summary_text = "No matching observations yet. Record evidence before judging predictions."
    else:
        overall = ReviewStatus.NO_ACTION.value
        strength = EvidenceStrength.NONE.value
        summary_text = "No review action yet. Record more observations before changing assumptions."

    return ValidationReviewSummary(
        overall_review_status=overall,
        confidence_impact=comparison_result.summary.confidence_impact,
        highest_severity=highest,
        review_needed_count=review_needed_count,
        evidence_strength=strength,
        primary_review_areas=areas,
        summary=summary_text,
    )


__all__ = ["build_review_summary"]
