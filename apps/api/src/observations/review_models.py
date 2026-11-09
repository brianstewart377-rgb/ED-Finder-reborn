"""Stage 6E validation review guidance models.

Review guidance sits on top of the Stage 6C comparison result. It is
structured, deterministic, and passive: it helps a user decide what to
investigate next, but it never changes predictions, confidence values,
scoring, optimiser ranking, or mechanics.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ReviewStatus(str, Enum):
    NO_ACTION = 'no_action'
    MONITOR = 'monitor'
    REVIEW_RECOMMENDED = 'review_recommended'
    REVIEW_HIGH_PRIORITY = 'review_high_priority'
    INSUFFICIENT_EVIDENCE = 'insufficient_evidence'
    MIXED_EVIDENCE = 'mixed_evidence'


class ReviewArea(str, Enum):
    SERVICE_RULES = 'service_rules'
    ECONOMY_RULES = 'economy_rules'
    CP_RULES = 'cp_rules'
    FACILITY_RULES = 'facility_rules'
    BUILD_OUTCOME = 'build_outcome'
    PREDICTION_CLAIMS = 'prediction_claims'
    EVIDENCE_QUALITY = 'evidence_quality'
    GENERAL = 'general'


class EvidenceStrength(str, Enum):
    NONE = 'none'
    WEAK = 'weak'
    MODERATE = 'moderate'
    STRONG = 'strong'
    MIXED = 'mixed'


@dataclass(frozen=True)
class ValidationReviewSignal:
    signal_id: str
    area: str
    severity: str
    confidence: str
    status: str
    title: str
    message: str
    recommended_action: str | None = None
    comparison_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationReviewSummary:
    overall_review_status: str
    confidence_impact: str
    highest_severity: str
    review_needed_count: int
    evidence_strength: str
    primary_review_areas: list[str]
    summary: str


@dataclass(frozen=True)
class ValidationReviewResult:
    system_id64: int
    target_archetype: str | None
    generated_at: str
    summary: ValidationReviewSummary
    signals: list[ValidationReviewSignal]
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


def review_signal_to_dict(signal: ValidationReviewSignal) -> dict[str, Any]:
    return asdict(signal)


def review_summary_to_dict(summary: ValidationReviewSummary) -> dict[str, Any]:
    return asdict(summary)


def review_result_to_dict(result: ValidationReviewResult) -> dict[str, Any]:
    return asdict(result)


__all__ = [
    'EvidenceStrength',
    'ReviewArea',
    'ReviewStatus',
    'ValidationReviewResult',
    'ValidationReviewSignal',
    'ValidationReviewSummary',
    'review_result_to_dict',
    'review_signal_to_dict',
    'review_summary_to_dict',
]
