"""Stage 6C comparison — build outcome rules.

Owns conservative ``build_outcome`` handling. Stage 6C deliberately
keeps build-outcome interpretation at a summary-only level so we don't
make claims about partial build sequences. We surface the observed
value and reference ``final_score`` / ``confidence`` from the prediction
without asserting confirmed/contradicted unless the observation itself
carries an explicit ``confirmed`` or ``contradicted`` status.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from edfinder_api.observations.comparison_models import (
    ComparisonArea,
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
    severity_enum_from_value,
)


def compare_build_outcome(prediction: Mapping[str, Any], fact: PersistedObservedFact) -> PredictionObservationComparison:
    obs_status = fact.status
    final_score = prediction.get('final_score')
    confidence_view = prediction.get('confidence')
    predicted_view: dict[str, Any] = {}
    if final_score is not None:
        predicted_view['final_score'] = final_score
    if confidence_view is not None:
        predicted_view['confidence'] = confidence_view

    if obs_status == ObservedStatus.CONFIRMED.value:
        return _row(
            fact=fact,
            predicted_value=predicted_view or None,
            status=ComparisonStatus.CONFIRMED,
            severity=ComparisonSeverity.INFO,
            reason='User-supplied build outcome marked confirmed against the prediction.',
            action=None,
            prediction_source='final_score/confidence',
        )
    if obs_status == ObservedStatus.CONTRADICTED.value:
        return _row(
            fact=fact,
            predicted_value=predicted_view or None,
            status=ComparisonStatus.CONTRADICTED,
            severity=severity_enum_from_value(
                contradiction_severity(fact, base=ComparisonSeverity.MEDIUM),
            ),
            reason='User-supplied build outcome marked contradicted against the prediction.',
            action='Review which prediction inputs differed from the observed build.',
            prediction_source='final_score/confidence',
        )

    if obs_status in (ObservedStatus.UNKNOWN.value, ObservedStatus.UNVERIFIED.value):
        target_status = ComparisonStatus.UNVERIFIED
        reason = 'User-supplied build outcome is unknown/unverified — not used to confirm or contradict.'
    else:
        target_status = ComparisonStatus.OBSERVED_ONLY
        reason = (
            'User-supplied build outcome recorded; '
            'Stage 6C does not auto-classify build outcomes beyond user-supplied status.'
        )
    return _row(
        fact=fact,
        predicted_value=predicted_view or None,
        status=target_status,
        severity=ComparisonSeverity.INFO,
        reason=reason,
        action=None,
        prediction_source='final_score/confidence' if predicted_view else None,
    )


def _row(
    *,
    fact: PersistedObservedFact,
    predicted_value: Any | None,
    status: ComparisonStatus,
    severity: ComparisonSeverity,
    reason: str,
    action: str | None,
    prediction_source: str | None,
) -> PredictionObservationComparison:
    return PredictionObservationComparison(
        comparison_id=f'build_outcome:{fact.observation_id}',
        area=ComparisonArea.BUILD_OUTCOME.value,
        subject_type=ObservedSubjectType.BUILD.value,
        subject_id=fact.subject_id,
        predicted_value=predicted_value,
        observed_value=fact.observed_value,
        status=status.value,
        severity=severity.value,
        confidence=comparison_confidence_for(fact),
        reason=reason,
        recommended_action=action,
        evidence=[evidence_from_fact(fact)],
        prediction_source=prediction_source,
    )


__all__ = ['compare_build_outcome']
