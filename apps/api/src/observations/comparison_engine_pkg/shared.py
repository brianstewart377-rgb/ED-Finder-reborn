"""Stage 6C comparison — shared helpers.

Common utilities used by every rule module (service, economy, CP,
facility, build_outcome, prediction_claim, note). Kept in one place so
the rule modules stay focused on their own decision logic.

Stage 6C is **comparison only**. Nothing in this module is consumed by
simulation scoring, optimiser candidate generation, or optimiser
ranking. A static passivity test enforces that boundary.
"""
from __future__ import annotations

from typing import Any

from edfinder_api.observations.comparison_models import (
    ComparisonConfidence,
    ComparisonSeverity,
    ObservationEvidenceMatch,
)
from edfinder_api.observations.models import (
    ObservedConfidence,
    PersistedObservedFact,
)


# ──────────────────────────────────────────────────────────────────────
# Evidence + confidence + severity helpers
# ──────────────────────────────────────────────────────────────────────
def evidence_from_fact(fact: PersistedObservedFact) -> ObservationEvidenceMatch:
    """Project a persisted fact down to the compact evidence shape returned
    to the comparison-response consumer.

    We intentionally drop server-side fields (e.g. ``build_fingerprint``)
    from this view; consumers can always re-query the Stage 6A facts
    endpoint if they need the full row.
    """
    return ObservationEvidenceMatch(
        observation_id=fact.observation_id,
        fact_type=fact.fact_type,
        subject_type=fact.subject_type,
        subject_id=fact.subject_id,
        status=fact.status,
        confidence=fact.confidence,
        observed_value=fact.observed_value,
        expected_value=fact.expected_value,
        notes=fact.notes,
    )


def comparison_confidence_for(fact: PersistedObservedFact) -> str:
    """Translate observation confidence → comparison confidence vocabulary."""
    if fact.confidence in {c.value for c in ObservedConfidence}:
        return fact.confidence
    return ComparisonConfidence.UNKNOWN.value


def contradiction_severity(fact: PersistedObservedFact, *, base: ComparisonSeverity) -> str:
    """Pick a contradiction severity, clamped by observation confidence.

    Rule (preserved from the Stage 6C monolithic engine):

    * LOW-confidence observations can never produce HIGH-severity
      contradictions — they cap at LOW.
    * MEDIUM-confidence observations cap at MEDIUM (a base of HIGH is
      clamped down).
    * HIGH-confidence observations honour the caller's base.

    Low confidence is a deliberate signal that the observer is unsure;
    Stage 6C trusts that hint rather than overruling it.
    """
    confidence = fact.confidence or ObservedConfidence.MEDIUM.value
    if confidence == ObservedConfidence.LOW.value:
        return ComparisonSeverity.LOW.value
    if confidence == ObservedConfidence.MEDIUM.value:
        if base == ComparisonSeverity.HIGH:
            return ComparisonSeverity.MEDIUM.value
        return base.value
    # high confidence — honour caller's base
    return base.value


def severity_enum_from_value(value: str) -> ComparisonSeverity:
    """Recover a ComparisonSeverity enum member from its string value."""
    for member in ComparisonSeverity:
        if member.value == value:
            return member
    return ComparisonSeverity.INFO


# ──────────────────────────────────────────────────────────────────────
# Scalar / equality helpers
# ──────────────────────────────────────────────────────────────────────
def scalar_equal(a: Any, b: Any) -> bool:
    """Equality that treats ints and floats as comparable.

    Booleans are checked first because ``bool`` is a subclass of ``int``
    and we do NOT want ``True == 1`` or ``False == 0`` to register as a
    match here.
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b


__all__ = [
    'comparison_confidence_for',
    'contradiction_severity',
    'evidence_from_fact',
    'scalar_equal',
    'severity_enum_from_value',
]
