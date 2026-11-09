"""Stage 6C comparison — CP (construction points) rules.

Owns CP comparison logic. Accepts two observed shapes:

* a numeric scalar — interpreted as final yellow CP (the prediction's
  primary CP value);
* a dict of ``{<cp_field>: value, ...}`` — overlapping keys only are
  compared.

CP comparison is observation-driven: we emit one row per CP observation,
not one row per predicted CP sub-field, to avoid noise.
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
    scalar_equal,
)


def compare_cp(prediction: Mapping[str, Any], fact: PersistedObservedFact) -> PredictionObservationComparison:
    cp = prediction.get('cp')
    subject_id = fact.subject_id or 'cp'

    if not isinstance(cp, Mapping):
        return PredictionObservationComparison(
            comparison_id=f'cp:{subject_id}',
            area=ComparisonArea.CP.value,
            subject_type=ObservedSubjectType.CP.value,
            subject_id=subject_id,
            predicted_value=None,
            observed_value=fact.observed_value,
            status=ComparisonStatus.OBSERVED_ONLY.value,
            severity=ComparisonSeverity.INFO.value,
            confidence=comparison_confidence_for(fact),
            reason='Prediction did not include a `cp` block — observation kept as observed_only.',
            recommended_action=None,
            evidence=[evidence_from_fact(fact)],
            prediction_source=None,
        )

    obs_status = fact.status
    if obs_status in (ObservedStatus.UNKNOWN.value, ObservedStatus.UNVERIFIED.value):
        return PredictionObservationComparison(
            comparison_id=f'cp:{subject_id}',
            area=ComparisonArea.CP.value,
            subject_type=ObservedSubjectType.CP.value,
            subject_id=subject_id,
            predicted_value=cp_predicted_view(cp),
            observed_value=fact.observed_value,
            status=ComparisonStatus.UNVERIFIED.value,
            severity=ComparisonSeverity.INFO.value,
            confidence=comparison_confidence_for(fact),
            reason='Observation marked unknown/unverified — not used to confirm or contradict CP prediction.',
            recommended_action=None,
            evidence=[evidence_from_fact(fact)],
            prediction_source='cp',
        )

    matched, predicted_view, mismatch_keys = _cp_match(cp, fact.observed_value)
    if matched is None:
        return PredictionObservationComparison(
            comparison_id=f'cp:{subject_id}',
            area=ComparisonArea.CP.value,
            subject_type=ObservedSubjectType.CP.value,
            subject_id=subject_id,
            predicted_value=predicted_view,
            observed_value=fact.observed_value,
            status=ComparisonStatus.UNVERIFIED.value,
            severity=ComparisonSeverity.INFO.value,
            confidence=comparison_confidence_for(fact),
            reason='Observed CP value shape could not be compared against the prediction (expected number or {yellow, green} object).',
            recommended_action=None,
            evidence=[evidence_from_fact(fact)],
            prediction_source='cp',
        )

    if matched:
        return PredictionObservationComparison(
            comparison_id=f'cp:{subject_id}',
            area=ComparisonArea.CP.value,
            subject_type=ObservedSubjectType.CP.value,
            subject_id=subject_id,
            predicted_value=predicted_view,
            observed_value=fact.observed_value,
            status=ComparisonStatus.CONFIRMED.value,
            severity=ComparisonSeverity.INFO.value,
            confidence=comparison_confidence_for(fact),
            reason='Observed CP balance matches the predicted CP balance.',
            recommended_action=None,
            evidence=[evidence_from_fact(fact)],
            prediction_source='cp',
        )

    severity = contradiction_severity(fact, base=ComparisonSeverity.MEDIUM)
    return PredictionObservationComparison(
        comparison_id=f'cp:{subject_id}',
        area=ComparisonArea.CP.value,
        subject_type=ObservedSubjectType.CP.value,
        subject_id=subject_id,
        predicted_value=predicted_view,
        observed_value=fact.observed_value,
        status=ComparisonStatus.CONTRADICTED.value,
        severity=severity,
        confidence=comparison_confidence_for(fact),
        reason=(
            'Observed CP balance differs from predicted CP balance'
            + (f' on keys: {sorted(mismatch_keys)}.' if mismatch_keys else '.')
        ),
        recommended_action='Review CP rules, primary-port exemption, build order, and CP repair suggestions.',
        evidence=[evidence_from_fact(fact)],
        prediction_source='cp',
    )


def cp_predicted_view(cp: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the CP fields a comparison row typically cares about.

    Limits the predicted view to the high-signal keys so we don't repeat
    large nested CP timelines in every comparison row.
    """
    keys = (
        'yellow_cp_final', 'green_cp_final',
        'yellow_cp_generated', 'green_cp_generated',
        'yellow_cp_spent', 'green_cp_spent',
        't2_ports', 't3_ports',
    )
    return {k: cp.get(k) for k in keys if k in cp}


def _cp_match(cp: Mapping[str, Any], observed: Any) -> tuple[bool | None, dict[str, Any], list[str]]:
    """Compare observed CP against the predicted ``cp`` block.

    Returns ``(matched, predicted_view, mismatch_keys)``. ``matched`` is
    ``None`` when the shape was unsupported.
    """
    predicted_view = cp_predicted_view(cp)

    if isinstance(observed, (int, float)) and not isinstance(observed, bool):
        target = cp.get('yellow_cp_final')
        if target is None:
            return None, predicted_view, []
        try:
            equal = float(target) == float(observed)
        except (TypeError, ValueError):
            return None, predicted_view, []
        return equal, predicted_view, [] if equal else ['yellow_cp_final']

    if isinstance(observed, Mapping):
        mismatch: list[str] = []
        any_compared = False
        for key, obs_value in observed.items():
            if not isinstance(key, str):
                continue
            if key not in cp:
                continue
            any_compared = True
            pred_value = cp.get(key)
            if scalar_equal(pred_value, obs_value):
                continue
            mismatch.append(key)
        if not any_compared:
            return None, predicted_view, []
        return (not mismatch), predicted_view, mismatch

    return None, predicted_view, []


__all__ = ['compare_cp', 'cp_predicted_view']
