"""Signal constructors for Stage 6E validation review guidance."""
from __future__ import annotations

from edfinder_api.observations.comparison_models import (
    ComparisonConfidence,
    ComparisonSeverity,
    PredictionObservationComparison,
)
from edfinder_api.observations.review.areas import area_title_action
from edfinder_api.observations.review.severity import (
    has_high_priority_contradiction,
    highest_confidence,
    highest_severity,
)
from edfinder_api.observations.review.shared import comparison_ids
from edfinder_api.observations.review_models import ReviewArea, ReviewStatus, ValidationReviewSignal


def signal(
    *,
    signal_id: str,
    area: str,
    severity: str,
    confidence: str,
    status: str,
    title: str,
    message: str,
    recommended_action: str | None = None,
    comparison_ids_: list[str] | None = None,
) -> ValidationReviewSignal:
    return ValidationReviewSignal(
        signal_id=signal_id,
        area=area,
        severity=severity,
        confidence=confidence,
        status=status,
        title=title,
        message=message,
        recommended_action=recommended_action,
        comparison_ids=comparison_ids_ or [],
    )


def no_evidence_signal() -> ValidationReviewSignal:
    return signal(
        signal_id="evidence:none",
        area=ReviewArea.EVIDENCE_QUALITY.value,
        severity=ComparisonSeverity.INFO.value,
        confidence=ComparisonConfidence.UNKNOWN.value,
        status=ReviewStatus.INSUFFICIENT_EVIDENCE.value,
        title="No observed evidence recorded",
        message="No observed evidence has been recorded yet. Record evidence before reviewing prediction quality.",
        recommended_action="Record observed services, economies, CP, or notes.",
    )


def low_confidence_contradiction_signal(
    comparisons: list[PredictionObservationComparison],
) -> ValidationReviewSignal:
    return signal(
        signal_id="evidence:low_confidence_contradictions",
        area=ReviewArea.EVIDENCE_QUALITY.value,
        severity=highest_severity(comparisons),
        confidence=ComparisonConfidence.LOW.value,
        status=ReviewStatus.MONITOR.value,
        title="Contradiction is based on low-confidence evidence",
        message="Some needs-review rows are based on low-confidence evidence. Treat them as review leads to confirm, not as high-priority review items.",
        recommended_action="Confirm in-game before changing assumptions.",
        comparison_ids_=comparison_ids(comparisons),
    )


def contradiction_area_signal(
    area: str,
    comparisons: list[PredictionObservationComparison],
) -> ValidationReviewSignal:
    status = (
        ReviewStatus.REVIEW_HIGH_PRIORITY.value
        if has_high_priority_contradiction(comparisons)
        else ReviewStatus.REVIEW_RECOMMENDED.value
    )
    title, action = area_title_action(area)
    return signal(
        signal_id=f"{area}:contradicted",
        area=area,
        severity=highest_severity(comparisons),
        confidence=highest_confidence(comparisons),
        status=status,
        title=title,
        message=f"{len(comparisons)} needs-review comparison row(s) point to this area. This is a review lead, not an automatic rule change.",
        recommended_action=action,
        comparison_ids_=comparison_ids(comparisons),
    )


def facility_observed_only_signal(
    comparisons: list[PredictionObservationComparison],
) -> ValidationReviewSignal:
    return signal(
        signal_id="facility_rules:observed_only",
        area=ReviewArea.FACILITY_RULES.value,
        severity=ComparisonSeverity.INFO.value,
        confidence=highest_confidence(comparisons),
        status=ReviewStatus.MONITOR.value,
        title="Facility evidence is not yet matched to prediction",
        message="Facility observations are present, but Stage 6C does not yet match them to structured facility predictions.",
        recommended_action="Add facility prediction extraction in a later validation stage if needed.",
        comparison_ids_=comparison_ids(comparisons),
    )


def predicted_only_heavy_signal(
    comparisons: list[PredictionObservationComparison],
) -> ValidationReviewSignal:
    return signal(
        signal_id="evidence:predicted_only_heavy",
        area=ReviewArea.EVIDENCE_QUALITY.value,
        severity=ComparisonSeverity.INFO.value,
        confidence=ComparisonConfidence.UNKNOWN.value,
        status=ReviewStatus.INSUFFICIENT_EVIDENCE.value,
        title="Many predictions have no matching observations",
        message="Many predictions have not been checked by matching observations. This is missing evidence, not a failure signal.",
        recommended_action="Record observed evidence before judging prediction quality.",
        comparison_ids_=comparison_ids(comparisons, limit=20),
    )


def observed_only_heavy_signal(
    comparisons: list[PredictionObservationComparison],
) -> ValidationReviewSignal:
    return signal(
        signal_id="general:observed_only_heavy",
        area=ReviewArea.GENERAL.value,
        severity=ComparisonSeverity.INFO.value,
        confidence=highest_confidence(comparisons),
        status=ReviewStatus.MONITOR.value,
        title="Observed evidence is not yet represented in prediction",
        message="Observed-only evidence is present without matching prediction rows. This is a coverage lead, not a mechanics verdict.",
        recommended_action="Review whether prediction extraction covers this evidence type.",
        comparison_ids_=comparison_ids(comparisons, limit=20),
    )


def mixed_evidence_signal(
    comparisons: list[PredictionObservationComparison],
) -> ValidationReviewSignal:
    return signal(
        signal_id="general:mixed_evidence",
        area=ReviewArea.GENERAL.value,
        severity=highest_severity(comparisons),
        confidence=highest_confidence(comparisons),
        status=ReviewStatus.MIXED_EVIDENCE.value,
        title="Validation evidence is mixed",
        message="Some predictions are supported while others may need review.",
        recommended_action="Review high-confidence needs-review rows first.",
        comparison_ids_=comparison_ids(comparisons, limit=20),
    )


def confirmed_only_signal(
    comparisons: list[PredictionObservationComparison],
) -> ValidationReviewSignal:
    return signal(
        signal_id="general:confirmed_only",
        area=ReviewArea.GENERAL.value,
        severity=ComparisonSeverity.INFO.value,
        confidence=highest_confidence(comparisons),
        status=ReviewStatus.NO_ACTION.value,
        title="Evidence currently supports the prediction",
        message="Current observed evidence mostly supports the prediction. Continue gathering observations.",
        recommended_action="Continue gathering observations.",
        comparison_ids_=comparison_ids(comparisons, limit=20),
    )


def fallback_signal() -> ValidationReviewSignal:
    return signal(
        signal_id="general:no_review_action",
        area=ReviewArea.GENERAL.value,
        severity=ComparisonSeverity.INFO.value,
        confidence=ComparisonConfidence.UNKNOWN.value,
        status=ReviewStatus.NO_ACTION.value,
        title="No review action yet",
        message="There is not enough matched evidence to suggest a review area yet.",
        recommended_action="Record more observations before changing assumptions.",
    )


__all__ = [
    "confirmed_only_signal",
    "contradiction_area_signal",
    "facility_observed_only_signal",
    "fallback_signal",
    "low_confidence_contradiction_signal",
    "mixed_evidence_signal",
    "no_evidence_signal",
    "observed_only_heavy_signal",
    "predicted_only_heavy_signal",
    "signal",
]
