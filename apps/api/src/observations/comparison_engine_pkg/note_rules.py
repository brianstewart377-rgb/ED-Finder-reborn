"""Stage 6C comparison — note rules.

Free-form notes are always surfaced as ``observed_only`` / ``info``.
Stage 6C does not interpret note bodies: a note is context for a human
reviewer, never a basis for contradicting a prediction.
"""
from __future__ import annotations

from edfinder_api.observations.comparison_models import (
    ComparisonArea,
    ComparisonSeverity,
    ComparisonStatus,
    PredictionObservationComparison,
)
from edfinder_api.observations.models import (
    ObservedSubjectType,
    PersistedObservedFact,
)

from edfinder_api.observations.comparison_engine_pkg.shared import (
    comparison_confidence_for,
    evidence_from_fact,
)


def observed_only_note(fact: PersistedObservedFact) -> PredictionObservationComparison:
    return PredictionObservationComparison(
        comparison_id=f'note:{fact.observation_id}',
        area=ComparisonArea.NOTE.value,
        subject_type=fact.subject_type or ObservedSubjectType.SYSTEM.value,
        subject_id=fact.subject_id,
        predicted_value=None,
        observed_value=fact.observed_value,
        status=ComparisonStatus.OBSERVED_ONLY.value,
        severity=ComparisonSeverity.INFO.value,
        confidence=comparison_confidence_for(fact),
        reason='Free-form note — recorded as observed-only context; Stage 6C does not interpret note bodies.',
        recommended_action=None,
        evidence=[evidence_from_fact(fact)],
        prediction_source=None,
    )


__all__ = ['observed_only_note']
