"""Review area mapping, ordering, and backend phrasing helpers."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from edfinder_api.observations.comparison_models import ComparisonArea, PredictionObservationComparison
from edfinder_api.observations.review_models import ReviewArea, ReviewStatus, ValidationReviewSignal

AREA_ORDER = [
    ReviewArea.SERVICE_RULES.value,
    ReviewArea.ECONOMY_RULES.value,
    ReviewArea.CP_RULES.value,
    ReviewArea.FACILITY_RULES.value,
    ReviewArea.BUILD_OUTCOME.value,
    ReviewArea.PREDICTION_CLAIMS.value,
    ReviewArea.EVIDENCE_QUALITY.value,
    ReviewArea.GENERAL.value,
]

NON_ACTION_STATUSES = {
    ReviewStatus.REVIEW_HIGH_PRIORITY.value,
    ReviewStatus.REVIEW_RECOMMENDED.value,
    ReviewStatus.MIXED_EVIDENCE.value,
    ReviewStatus.MONITOR.value,
    ReviewStatus.INSUFFICIENT_EVIDENCE.value,
}


def review_area_for_comparison(comparison: PredictionObservationComparison) -> str:
    if comparison.area == ComparisonArea.SERVICE.value:
        return ReviewArea.SERVICE_RULES.value
    if comparison.area == ComparisonArea.ECONOMY.value:
        return ReviewArea.ECONOMY_RULES.value
    if comparison.area == ComparisonArea.CP.value:
        return ReviewArea.CP_RULES.value
    if comparison.area == ComparisonArea.FACILITY.value:
        return ReviewArea.FACILITY_RULES.value
    if comparison.area == ComparisonArea.BUILD_OUTCOME.value:
        return ReviewArea.BUILD_OUTCOME.value
    if comparison.subject_type == "prediction":
        return ReviewArea.PREDICTION_CLAIMS.value
    return ReviewArea.GENERAL.value


def group_actionable_contradictions(
    contradictions: Iterable[PredictionObservationComparison],
) -> dict[str, list[PredictionObservationComparison]]:
    grouped: dict[str, list[PredictionObservationComparison]] = defaultdict(list)
    for comparison in contradictions:
        grouped[review_area_for_comparison(comparison)].append(comparison)
    return {area: grouped[area] for area in AREA_ORDER if grouped.get(area)}


def area_title_action(area: str) -> tuple[str, str]:
    if area == ReviewArea.SERVICE_RULES.value:
        return (
            "Service prediction rules may need review",
            "Review service unlock assumptions and facility/service mapping.",
        )
    if area == ReviewArea.ECONOMY_RULES.value:
        return (
            "Economy prediction rules may need review",
            "Review economy inheritance, facility pressure, and economy composition assumptions.",
        )
    if area == ReviewArea.CP_RULES.value:
        return (
            "CP calculation may need review",
            "Review CP source values and final CP calculation.",
        )
    if area == ReviewArea.BUILD_OUTCOME.value:
        return (
            "Build outcome interpretation may need review",
            "Review build outcome assumptions and the observed outcome notes.",
        )
    if area == ReviewArea.PREDICTION_CLAIMS.value:
        return (
            "Prediction claim evidence may need review",
            "Review the recorded prediction-match or prediction-mismatch claim.",
        )
    return (
        "Validation area may need review",
        "Review high-confidence needs-review rows first.",
    )


def primary_areas(signals: list[ValidationReviewSignal]) -> list[str]:
    areas = {
        signal.area
        for signal in signals
        if signal.status in NON_ACTION_STATUSES and signal.area != ReviewArea.GENERAL.value
    }
    if not areas:
        areas = {
            signal.area for signal in signals if signal.status in NON_ACTION_STATUSES
        }
    if not areas:
        areas = {signal.area for signal in signals}
    return [area for area in AREA_ORDER if area in areas]


def area_phrase(areas: list[str]) -> str:
    labels = {
        ReviewArea.SERVICE_RULES.value: "service assumptions",
        ReviewArea.ECONOMY_RULES.value: "economy assumptions",
        ReviewArea.CP_RULES.value: "CP assumptions",
        ReviewArea.FACILITY_RULES.value: "facility coverage",
        ReviewArea.BUILD_OUTCOME.value: "build outcome assumptions",
        ReviewArea.PREDICTION_CLAIMS.value: "prediction claim evidence",
        ReviewArea.EVIDENCE_QUALITY.value: "evidence quality",
        ReviewArea.GENERAL.value: "validation evidence",
    }
    picked = [labels.get(area, area) for area in areas[:2]]
    if not picked:
        return "validation evidence"
    if len(picked) == 1:
        return picked[0]
    return f"{picked[0]} and {picked[1]}"


__all__ = [
    "AREA_ORDER",
    "NON_ACTION_STATUSES",
    "area_phrase",
    "area_title_action",
    "group_actionable_contradictions",
    "primary_areas",
    "review_area_for_comparison",
]
