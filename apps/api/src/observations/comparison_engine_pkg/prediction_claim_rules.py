"""Stage 6C comparison — prediction_match / prediction_mismatch rules.

Owns ``prediction_match`` and ``prediction_mismatch`` handling.

These fact types are user claims about a subject:

* ``prediction_match`` — "I checked and the prediction was right".
* ``prediction_mismatch`` — "I checked and the prediction was wrong".

Stage 6C only elevates such a claim into ``confirmed`` /
``contradicted`` when we can locate the referenced subject in the
current prediction. If the subject is not in the prediction the claim
is surfaced as ``observed_only`` (for match) or ``unverified`` (for
mismatch), so Stage 6C never echoes a user claim as truth without
supporting prediction context.
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
    contradiction_severity,
    evidence_from_fact,
    severity_enum_from_value,
)


def compare_prediction_claim(
    fact: PersistedObservedFact,
    service_predictions: dict[str, str | None],
    economy_predictions: dict[str, bool],
    *,
    matched: bool,
) -> PredictionObservationComparison:
    subject_type = fact.subject_type
    subject_id = (
        fact.subject_id
        or fact.service_id
        or fact.economy
        or fact.facility_template_id
    )

    predicted_value = None
    prediction_source: str | None = None
    if subject_type == ObservedSubjectType.SERVICE.value and subject_id in service_predictions:
        predicted_value = service_predictions[subject_id]
        prediction_source = 'services'
    elif subject_type == ObservedSubjectType.ECONOMY.value and subject_id in economy_predictions:
        predicted_value = economy_predictions[subject_id]
        prediction_source = 'economy_composition/economy_order'

    if predicted_value is None:
        target_status = (
            ComparisonStatus.OBSERVED_ONLY if matched else ComparisonStatus.UNVERIFIED
        )
        reason = (
            'User reported a prediction match but the referenced subject is not in the current prediction.'
            if matched
            else 'User reported a prediction mismatch but the referenced subject is not in the current prediction.'
        )
        severity_enum = ComparisonSeverity.INFO
        action: str | None = None
    else:
        if matched:
            target_status = ComparisonStatus.CONFIRMED
            severity_enum = ComparisonSeverity.INFO
            reason = 'User reported a prediction match for a known predicted subject.'
            action = None
        else:
            target_status = ComparisonStatus.CONTRADICTED
            severity_enum = severity_enum_from_value(
                contradiction_severity(fact, base=ComparisonSeverity.MEDIUM),
            )
            reason = 'User reported a prediction mismatch for a known predicted subject.'
            action = 'Review prediction rules for the reported subject and the user-provided notes.'

    area = (
        ComparisonArea.SERVICE if subject_type == ObservedSubjectType.SERVICE.value else
        ComparisonArea.ECONOMY if subject_type == ObservedSubjectType.ECONOMY.value else
        ComparisonArea.OTHER
    )

    subject_type_value = (
        subject_type if subject_type in {e.value for e in ObservedSubjectType}
        else ObservedSubjectType.SYSTEM.value
    )

    return PredictionObservationComparison(
        comparison_id=f'prediction_claim:{fact.observation_id}',
        area=area.value,
        subject_type=subject_type_value,
        subject_id=subject_id,
        predicted_value=predicted_value,
        observed_value=fact.observed_value,
        status=target_status.value,
        severity=severity_enum.value,
        confidence=comparison_confidence_for(fact),
        reason=reason,
        recommended_action=action,
        evidence=[evidence_from_fact(fact)],
        prediction_source=prediction_source,
    )


__all__ = ['compare_prediction_claim']
