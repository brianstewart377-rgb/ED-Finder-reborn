"""Stage 6C comparison — facility-state rules.

Owns observed-only handling for ``facility_state`` observations.

Stage 6C does not yet attempt to compare a structured per-facility state
map from the prediction (the prediction layer does not expose one in a
single canonical place). Facility observations are therefore surfaced as
``observed_only`` context until a future stage introduces a comparable
facility prediction view.
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


def observed_only_facility(fact: PersistedObservedFact) -> PredictionObservationComparison:
    subject = fact.facility_template_id or fact.subject_id or ''
    return PredictionObservationComparison(
        comparison_id=f'facility:{subject}' if subject else 'facility',
        area=ComparisonArea.FACILITY.value,
        subject_type=ObservedSubjectType.FACILITY.value,
        subject_id=subject or None,
        predicted_value=None,
        observed_value=fact.observed_value,
        status=ComparisonStatus.OBSERVED_ONLY.value,
        severity=ComparisonSeverity.INFO.value,
        confidence=comparison_confidence_for(fact),
        reason='Prediction does not expose a comparable facility-state map; observation kept as observed_only.',
        recommended_action=None,
        evidence=[evidence_from_fact(fact)],
        prediction_source=None,
    )


__all__ = ['observed_only_facility']
