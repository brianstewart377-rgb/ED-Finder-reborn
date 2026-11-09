"""Stage 6C comparison — service rules.

Owns service-presence comparison logic:

* ``compare_service`` — predicted service vs matched observation(s).
* ``observed_only_service`` — observation with no matching prediction.
* ``service_status_decision`` — internal helper picking
  confirmed/contradicted/unverified for a predicted-observed pair.

Stage 6C semantics:

* ``active`` is the only predicted status that counts as present.
* ``unknown`` / ``unverified`` observations never confirm or contradict.
* Severity for contradictions is clamped by observation confidence
  (low-confidence observations cap at LOW, medium at MEDIUM).
"""
from __future__ import annotations

from edfinder_api.observations.comparison_models import (
    ComparisonArea,
    ComparisonConfidence,
    ComparisonSeverity,
    ComparisonStatus,
    PredictionObservationComparison,
)
from edfinder_api.observations.models import (
    ObservedStatus,
    ObservedSubjectType,
    PersistedObservedFact,
)

from edfinder_api.observations.comparison_engine_pkg.shared import (
    comparison_confidence_for,
    contradiction_severity,
    evidence_from_fact,
)


def _service_present(predicted_status: str | None) -> bool:
    """``active`` counts as present; ``locked`` and ``unknown`` do not."""
    if predicted_status is None:
        return False
    return predicted_status.lower() == 'active'


def compare_service(
    service_id: str,
    predicted_status: str | None,
    observed_facts: list[PersistedObservedFact],
) -> PredictionObservationComparison:
    if not observed_facts:
        return PredictionObservationComparison(
            comparison_id=f'service:{service_id}',
            area=ComparisonArea.SERVICE.value,
            subject_type=ObservedSubjectType.SERVICE.value,
            subject_id=service_id,
            predicted_value=predicted_status,
            observed_value=None,
            status=ComparisonStatus.PREDICTED_ONLY.value,
            severity=ComparisonSeverity.INFO.value,
            confidence=ComparisonConfidence.UNKNOWN.value,
            reason='Prediction includes this service but no observed evidence has been recorded yet.',
            recommended_action=None,
            evidence=[],
            prediction_source='services',
        )

    # Within a bucket the first fact is treated as primary for the status
    # decision; all facts in the bucket are still attached as evidence.
    primary = observed_facts[0]
    predicted_present = _service_present(predicted_status)
    status, severity, reason, action = _service_status_decision(
        predicted_status, predicted_present, primary,
    )
    return PredictionObservationComparison(
        comparison_id=f'service:{service_id}',
        area=ComparisonArea.SERVICE.value,
        subject_type=ObservedSubjectType.SERVICE.value,
        subject_id=service_id,
        predicted_value=predicted_status,
        observed_value=primary.observed_value,
        status=status,
        severity=severity,
        confidence=comparison_confidence_for(primary),
        reason=reason,
        recommended_action=action,
        evidence=[evidence_from_fact(fact) for fact in observed_facts],
        prediction_source='services',
    )


def _service_status_decision(
    predicted_status: str | None,
    predicted_present: bool,
    fact: PersistedObservedFact,
) -> tuple[str, str, str, str | None]:
    obs_status = fact.status
    if obs_status in (ObservedStatus.UNKNOWN.value, ObservedStatus.UNVERIFIED.value):
        return (
            ComparisonStatus.UNVERIFIED.value,
            ComparisonSeverity.INFO.value,
            'Observation marked as unknown or unverified — not used to confirm or contradict the prediction.',
            None,
        )

    observed_present = obs_status in (
        ObservedStatus.OBSERVED_PRESENT.value,
        ObservedStatus.CONFIRMED.value,
    )
    observed_absent = obs_status in (
        ObservedStatus.OBSERVED_ABSENT.value,
        ObservedStatus.CONTRADICTED.value,
    )

    if predicted_present and observed_present:
        return (
            ComparisonStatus.CONFIRMED.value,
            ComparisonSeverity.INFO.value,
            f'Predicted active and observed present (status={predicted_status}).',
            None,
        )
    if predicted_present and observed_absent:
        severity = contradiction_severity(fact, base=ComparisonSeverity.HIGH)
        return (
            ComparisonStatus.CONTRADICTED.value,
            severity,
            'Prediction expected this service to be active but the observation reports it as absent.',
            'Review service unlock rules and the observed port/facility combination.',
        )
    if not predicted_present and observed_present:
        severity = contradiction_severity(fact, base=ComparisonSeverity.MEDIUM)
        return (
            ComparisonStatus.CONTRADICTED.value,
            severity,
            'Prediction did not show this service as active but the observation reports it as present.',
            'Review service unlock rules for the observed facility mix.',
        )
    if not predicted_present and observed_absent:
        return (
            ComparisonStatus.CONFIRMED.value,
            ComparisonSeverity.INFO.value,
            'Prediction did not expect this service active and the observation confirms it is absent.',
            None,
        )
    # observed_present / observed_absent both False here = unmapped status
    return (
        ComparisonStatus.UNVERIFIED.value,
        ComparisonSeverity.INFO.value,
        'Observation status does not clearly indicate presence or absence — not used to confirm or contradict.',
        None,
    )


def observed_only_service(fact: PersistedObservedFact) -> PredictionObservationComparison:
    service_id = fact.service_id or fact.subject_id or ''
    obs_status = fact.status
    if obs_status in (ObservedStatus.UNKNOWN.value, ObservedStatus.UNVERIFIED.value):
        status = ComparisonStatus.UNVERIFIED.value
        reason = 'Observation marked unknown/unverified and has no matching predicted service.'
    else:
        status = ComparisonStatus.OBSERVED_ONLY.value
        reason = 'Observed service has no matching entry in the current prediction.'
    return PredictionObservationComparison(
        comparison_id=f'service:{service_id}',
        area=ComparisonArea.SERVICE.value,
        subject_type=ObservedSubjectType.SERVICE.value,
        subject_id=service_id,
        predicted_value=None,
        observed_value=fact.observed_value,
        status=status,
        severity=ComparisonSeverity.INFO.value,
        confidence=comparison_confidence_for(fact),
        reason=reason,
        recommended_action=None,
        evidence=[evidence_from_fact(fact)],
        prediction_source=None,
    )


__all__ = ['compare_service', 'observed_only_service']
