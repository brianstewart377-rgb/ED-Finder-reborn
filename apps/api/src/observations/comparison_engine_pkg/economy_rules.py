"""Stage 6C comparison — economy rules.

Owns economy-presence comparison logic. Stage 6C uses **conservative
name-keyed matching** here, by deliberate design:

* Economy evidence is matched **by economy name**.
* A predicted economy ``extraction`` is **not** contradicted just because
  an observation reports a *different* economy (e.g. ``tourism``) as
  present. That observation becomes an ``observed_only`` row for
  ``tourism`` and ``extraction`` remains ``predicted_only`` until
  evidence for ``extraction`` is recorded.
* To mark a predicted economy as contradicted, the user (or an upstream
  source) must record an observation for **that** economy with status
  ``observed_absent`` or ``contradicted``.

This is intentionally narrow. Two consequences:

* Stage 6C never overclaims that the prediction is wrong based on
  evidence for a sibling economy that is unrelated to the predicted one.
* Mutual-exclusion rules between economies live in the mechanics layer,
  not here. Stage 6C is not allowed to invent them.

Stage 6D may layer richer multi-economy heuristics on top of this
output, but the engine itself keeps the contract narrow.
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


def compare_economy(
    economy_name: str,
    predicted_present: bool,
    observed_facts: list[PersistedObservedFact],
) -> PredictionObservationComparison:
    """Compare a single predicted economy against same-name observations.

    See module docstring for why this is intentionally name-keyed and
    conservative.
    """
    if not observed_facts:
        return PredictionObservationComparison(
            comparison_id=f'economy:{economy_name}',
            area=ComparisonArea.ECONOMY.value,
            subject_type=ObservedSubjectType.ECONOMY.value,
            subject_id=economy_name,
            predicted_value=predicted_present,
            observed_value=None,
            status=ComparisonStatus.PREDICTED_ONLY.value,
            severity=ComparisonSeverity.INFO.value,
            confidence=ComparisonConfidence.UNKNOWN.value,
            reason='Prediction includes this economy but no observed evidence has been recorded yet.',
            recommended_action=None,
            evidence=[],
            prediction_source='economy_composition/economy_order',
        )

    primary = observed_facts[0]
    obs_status = primary.status

    if obs_status in (ObservedStatus.UNKNOWN.value, ObservedStatus.UNVERIFIED.value):
        return PredictionObservationComparison(
            comparison_id=f'economy:{economy_name}',
            area=ComparisonArea.ECONOMY.value,
            subject_type=ObservedSubjectType.ECONOMY.value,
            subject_id=economy_name,
            predicted_value=predicted_present,
            observed_value=primary.observed_value,
            status=ComparisonStatus.UNVERIFIED.value,
            severity=ComparisonSeverity.INFO.value,
            confidence=comparison_confidence_for(primary),
            reason='Observation marked unknown/unverified — not used to confirm or contradict the predicted economy.',
            recommended_action=None,
            evidence=[evidence_from_fact(fact) for fact in observed_facts],
            prediction_source='economy_composition/economy_order',
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
        status, severity, reason, action = (
            ComparisonStatus.CONFIRMED.value,
            ComparisonSeverity.INFO.value,
            'Predicted economy is also recorded as present in the observation.',
            None,
        )
    elif predicted_present and observed_absent:
        status, severity, reason, action = (
            ComparisonStatus.CONTRADICTED.value,
            contradiction_severity(primary, base=ComparisonSeverity.MEDIUM),
            'Prediction expected this economy but the observation reports it as absent.',
            'Review economy inheritance and facility economy pressure for the observed body.',
        )
    elif not predicted_present and observed_present:
        status, severity, reason, action = (
            ComparisonStatus.CONTRADICTED.value,
            contradiction_severity(primary, base=ComparisonSeverity.MEDIUM),
            'Prediction did not include this economy but the observation reports it as present.',
            'Review economy inheritance, facility economy pressure, and topology rules.',
        )
    elif not predicted_present and observed_absent:
        status, severity, reason, action = (
            ComparisonStatus.CONFIRMED.value,
            ComparisonSeverity.INFO.value,
            'Prediction did not include this economy and observation confirms it is absent.',
            None,
        )
    else:
        status, severity, reason, action = (
            ComparisonStatus.UNVERIFIED.value,
            ComparisonSeverity.INFO.value,
            'Observation status does not clearly indicate presence or absence — not used to confirm or contradict.',
            None,
        )

    return PredictionObservationComparison(
        comparison_id=f'economy:{economy_name}',
        area=ComparisonArea.ECONOMY.value,
        subject_type=ObservedSubjectType.ECONOMY.value,
        subject_id=economy_name,
        predicted_value=predicted_present,
        observed_value=primary.observed_value,
        status=status,
        severity=severity,
        confidence=comparison_confidence_for(primary),
        reason=reason,
        recommended_action=action,
        evidence=[evidence_from_fact(fact) for fact in observed_facts],
        prediction_source='economy_composition/economy_order',
    )


def observed_only_economy(fact: PersistedObservedFact) -> PredictionObservationComparison:
    """Emit an ``observed_only`` row for an observation about a
    *different* economy than any predicted economy.

    This is the conservative-matching path: we never elevate an
    "observed economy X present" claim into a contradiction against a
    predicted economy Y. Per-economy contradiction needs per-economy
    evidence.
    """
    economy_name = fact.economy or fact.subject_id or ''
    obs_status = fact.status
    if obs_status in (ObservedStatus.UNKNOWN.value, ObservedStatus.UNVERIFIED.value):
        status = ComparisonStatus.UNVERIFIED.value
        reason = 'Observation marked unknown/unverified and has no matching predicted economy.'
    else:
        status = ComparisonStatus.OBSERVED_ONLY.value
        reason = (
            'Observed economy has no matching entry in the current prediction. '
            'Stage 6C does not infer a contradiction against other predicted economies — '
            'record an observed_absent/contradicted fact for the predicted economy to mark it contradicted.'
        )
    return PredictionObservationComparison(
        comparison_id=f'economy:{economy_name}',
        area=ComparisonArea.ECONOMY.value,
        subject_type=ObservedSubjectType.ECONOMY.value,
        subject_id=economy_name,
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


__all__ = ['compare_economy', 'observed_only_economy']
