"""Severity and confidence helpers for validation review guidance."""
from __future__ import annotations

from collections.abc import Iterable

from edfinder_api.observations.comparison_models import (
    ComparisonConfidence,
    ComparisonSeverity,
    PredictionObservationComparison,
)

SEVERITY_RANK = {
    "none": 0,
    ComparisonSeverity.INFO.value: 1,
    ComparisonSeverity.LOW.value: 2,
    ComparisonSeverity.MEDIUM.value: 3,
    ComparisonSeverity.HIGH.value: 4,
}

CONFIDENCE_RANK = {
    ComparisonConfidence.UNKNOWN.value: 0,
    ComparisonConfidence.LOW.value: 1,
    ComparisonConfidence.MEDIUM.value: 2,
    ComparisonConfidence.HIGH.value: 3,
}


def highest_severity(comparisons: Iterable[PredictionObservationComparison]) -> str:
    highest = "none"
    for comparison in comparisons:
        if SEVERITY_RANK.get(comparison.severity, 0) > SEVERITY_RANK[highest]:
            highest = comparison.severity
    return highest


def highest_confidence(comparisons: Iterable[PredictionObservationComparison]) -> str:
    highest = ComparisonConfidence.UNKNOWN.value
    for comparison in comparisons:
        if CONFIDENCE_RANK.get(comparison.confidence, 0) > CONFIDENCE_RANK[highest]:
            highest = comparison.confidence
    return highest


def is_low_confidence(comparison: PredictionObservationComparison) -> bool:
    return comparison.confidence == ComparisonConfidence.LOW.value


def is_high_priority_contradiction(comparison: PredictionObservationComparison) -> bool:
    return (
        comparison.severity == ComparisonSeverity.HIGH.value
        and comparison.confidence
        in {ComparisonConfidence.HIGH.value, ComparisonConfidence.MEDIUM.value}
    )


def has_high_priority_contradiction(
    comparisons: Iterable[PredictionObservationComparison],
) -> bool:
    return any(is_high_priority_contradiction(comparison) for comparison in comparisons)


__all__ = [
    "CONFIDENCE_RANK",
    "SEVERITY_RANK",
    "has_high_priority_contradiction",
    "highest_confidence",
    "highest_severity",
    "is_high_priority_contradiction",
    "is_low_confidence",
]
