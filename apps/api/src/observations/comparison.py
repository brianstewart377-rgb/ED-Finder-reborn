"""Deterministic observed-vs-predicted comparison helpers.

These helpers compare attached observed facts to a simulation prediction. They do
not alter mechanics confidence or scoring; they only report review signals.
"""
from __future__ import annotations

from typing import Any

from edfinder_api.observations.models import (
    ObservationArea,
    ObservationComparisonStatus,
    ObservationSeverity,
    ObservationSummary,
    ObservedFact,
    PredictionObservationDiff,
    observed_fact_from_any,
)


def compare_prediction_to_observations(
    *,
    prediction: dict[str, Any],
    observed_facts: list[ObservedFact | dict[str, Any]],
) -> tuple[ObservationSummary, list[PredictionObservationDiff]]:
    facts = [observed_fact_from_any(fact) for fact in observed_facts]
    if not facts:
        return _predicted_only_summary(), []

    diffs = [_compare_fact(prediction, fact) for fact in facts]
    confirmed = sum(1 for diff in diffs if diff.status == ObservationComparisonStatus.CONFIRMED.value)
    mismatch = sum(1 for diff in diffs if diff.status == ObservationComparisonStatus.MISMATCH.value)
    observed_only = sum(1 for diff in diffs if diff.status == ObservationComparisonStatus.OBSERVED_ONLY.value)
    predicted_only = sum(1 for diff in diffs if diff.status == ObservationComparisonStatus.PREDICTED_ONLY.value)
    unknown = sum(1 for diff in diffs if diff.status == ObservationComparisonStatus.UNKNOWN.value)
    impact = _confidence_impact(diffs)
    summary_text = _summary_text(confirmed, mismatch, observed_only, unknown)
    return ObservationSummary(
        status='has_observations',
        observed_facts_count=len(facts),
        confirmed_count=confirmed,
        mismatch_count=mismatch,
        observed_only_count=observed_only,
        predicted_only_count=predicted_only,
        unknown_count=unknown,
        confidence_impact=impact,
        summary=summary_text,
    ), diffs


def compare_slot_predictions(prediction: dict[str, Any], fact: ObservedFact) -> PredictionObservationDiff:
    predicted = _predicted_slot_value(prediction, fact)
    if predicted is None:
        return _observed_only(fact, 'Observed slot fact has no matching predicted slot value in this simulation output.')
    if predicted == fact.observed_value:
        return _confirmed(fact, predicted, 'Observed slot count matches predicted slot count.')
    return _mismatch(
        fact,
        predicted,
        ObservationSeverity.MEDIUM.value,
        'Observed slot count differs from predicted slot count.',
        'Review slot prediction rules for this body type.',
    )


def compare_service_predictions(prediction: dict[str, Any], fact: ObservedFact) -> PredictionObservationDiff:
    predicted = _predicted_service_value(prediction, fact)
    if predicted is None:
        return _observed_only(fact, 'Observed service fact has no matching prediction in this simulation output.')
    if str(predicted) == str(fact.observed_value):
        return _confirmed(fact, predicted, 'Observed service status matches predicted service status.')
    severity = ObservationSeverity.HIGH.value if 'active' in {str(predicted), str(fact.observed_value)} else ObservationSeverity.MEDIUM.value
    return _mismatch(
        fact,
        predicted,
        severity,
        'Observed service status differs from predicted service status.',
        'Review service unlock rules and the observed port/facility combination.',
    )


def compare_economy_predictions(prediction: dict[str, Any], fact: ObservedFact) -> PredictionObservationDiff:
    predicted = _predicted_economy_value(prediction, fact)
    if predicted is None:
        return _observed_only(fact, 'Observed economy fact has no matching prediction in this simulation output.')
    if _normalise(predicted) == _normalise(fact.observed_value):
        return _confirmed(fact, predicted, 'Observed economy outcome matches predicted economy outcome.')
    return _mismatch(
        fact,
        predicted,
        ObservationSeverity.MEDIUM.value,
        'Observed economy outcome differs from predicted economy outcome.',
        'Review body inheritance, facility economy pressure, and topology influence rules.',
    )


def compare_cp_predictions(prediction: dict[str, Any], fact: ObservedFact) -> PredictionObservationDiff:
    predicted = _predicted_cp_value(prediction, fact)
    if predicted is None:
        return _observed_only(fact, 'Observed CP fact has no matching CP prediction in this simulation output.')
    if _normalise(predicted) == _normalise(fact.observed_value):
        return _confirmed(fact, predicted, 'Observed CP balance matches predicted CP balance.')
    return _mismatch(
        fact,
        predicted,
        ObservationSeverity.HIGH.value,
        'Observed CP balance differs from predicted final CP balance.',
        'Review CP rules, primary-port exemption, build order, and CP repair suggestions.',
    )


def _compare_fact(prediction: dict[str, Any], fact: ObservedFact) -> PredictionObservationDiff:
    if fact.observed_value is None:
        return PredictionObservationDiff(
            area=fact.area,
            subject_id=fact.subject_id,
            subject_type=fact.subject_type,
            predicted_value=None,
            observed_value=None,
            status=ObservationComparisonStatus.UNKNOWN.value,
            severity=ObservationSeverity.LOW.value,
            confidence='unknown',
            reason='Observed fact is present but its observed value is unknown or incomplete.',
            source_type=fact.source_type,
            observed_at=fact.observed_at,
        )
    if fact.area == ObservationArea.SLOTS.value:
        return compare_slot_predictions(prediction, fact)
    if fact.area in {ObservationArea.SERVICES.value, ObservationArea.SERVICE_UNLOCKS.value}:
        return compare_service_predictions(prediction, fact)
    if fact.area == ObservationArea.ECONOMY_OUTCOME.value:
        return compare_economy_predictions(prediction, fact)
    if fact.area == ObservationArea.CP_BALANCE.value:
        return compare_cp_predictions(prediction, fact)
    return _observed_only(fact, 'Observed fact has no matching prediction in this simulation output.')


def _predicted_slot_value(prediction: dict[str, Any], fact: ObservedFact) -> Any:
    subject = fact.subject_id.lower()
    if subject in {'orbital_slots', 'system_orbital_slots'}:
        return prediction.get('topology', {}).get('estimated_orbital_slots') or prediction.get('estimated_orbital_slots')
    if subject in {'ground_slots', 'surface_slots', 'system_ground_slots'}:
        return prediction.get('topology', {}).get('estimated_ground_slots') or prediction.get('estimated_ground_slots')
    topology = prediction.get('topology', {})
    if isinstance(topology, dict):
        slots = topology.get('slots')
        if isinstance(slots, dict):
            return slots.get(fact.subject_id)
    return None


def _predicted_service_value(prediction: dict[str, Any], fact: ObservedFact) -> Any:
    service_id = _service_id_from_fact(fact)
    for state in prediction.get('port_service_states', []) or []:
        if fact.body_id and str(state.get('local_body_id')) != str(fact.body_id):
            continue
        if fact.facility_id and str(state.get('port_id')) != str(fact.facility_id):
            continue
        for bucket in ('active_services', 'locked_services', 'unknown_services'):
            services = state.get(bucket) or {}
            if service_id in services:
                return services[service_id].get('status')
    services = prediction.get('services') or {}
    if service_id in services and isinstance(services[service_id], dict):
        return services[service_id].get('status')
    return None


def _predicted_economy_value(prediction: dict[str, Any], fact: ObservedFact) -> Any:
    if fact.subject_id in {'top_two', 'system_top_two'}:
        stack = prediction.get('economy_stack') or {}
        if isinstance(stack, dict) and stack.get('top_two') is not None:
            return stack.get('top_two')
        return prediction.get('economy_order', [])[:2]
    if fact.subject_id in {'composition', 'economy_composition'}:
        return prediction.get('economy_composition')
    for state in prediction.get('port_economy_states', []) or []:
        if fact.facility_id and str(state.get('port_id')) != str(fact.facility_id):
            continue
        if fact.body_id and str(state.get('local_body_id')) != str(fact.body_id):
            continue
        if fact.subject_id.endswith(':top_two') or fact.subject_id == str(state.get('port_id')):
            return state.get('top_two')
    return None


def _predicted_cp_value(prediction: dict[str, Any], fact: ObservedFact) -> Any:
    cp = prediction.get('cp') or {}
    if fact.subject_id in {'final', 'final_balance', 'cp_final'}:
        return {'yellow_cp_final': cp.get('yellow_cp_final'), 'green_cp_final': cp.get('green_cp_final')}
    return cp.get(fact.subject_id)


def _service_id_from_fact(fact: ObservedFact) -> str:
    if ':' in fact.subject_id:
        return fact.subject_id.split(':')[-1]
    return fact.subject_id


def _confirmed(fact: ObservedFact, predicted: Any, reason: str) -> PredictionObservationDiff:
    return PredictionObservationDiff(
        area=fact.area,
        subject_id=fact.subject_id,
        subject_type=fact.subject_type,
        predicted_value=predicted,
        observed_value=fact.observed_value,
        status=ObservationComparisonStatus.CONFIRMED.value,
        severity=ObservationSeverity.INFO.value,
        confidence=fact.confidence if fact.confidence in {'observed', 'verified', 'community_observed'} else 'observed',
        reason=reason,
        source_type=fact.source_type,
        observed_at=fact.observed_at,
    )


def _mismatch(fact: ObservedFact, predicted: Any, severity: str, reason: str, action: str) -> PredictionObservationDiff:
    return PredictionObservationDiff(
        area=fact.area,
        subject_id=fact.subject_id,
        subject_type=fact.subject_type,
        predicted_value=predicted,
        observed_value=fact.observed_value,
        status=ObservationComparisonStatus.MISMATCH.value,
        severity=severity,
        confidence=fact.confidence if fact.confidence in {'observed', 'verified', 'community_observed'} else 'observed',
        reason=reason,
        recommended_action=action,
        source_type=fact.source_type,
        observed_at=fact.observed_at,
    )


def _observed_only(fact: ObservedFact, reason: str) -> PredictionObservationDiff:
    return PredictionObservationDiff(
        area=fact.area,
        subject_id=fact.subject_id,
        subject_type=fact.subject_type,
        predicted_value=None,
        observed_value=fact.observed_value,
        status=ObservationComparisonStatus.OBSERVED_ONLY.value,
        severity=ObservationSeverity.LOW.value,
        confidence=fact.confidence if fact.confidence in {'observed', 'verified', 'community_observed'} else 'observed',
        reason=reason,
        recommended_action='Review whether this observed fact should become part of the prediction model.',
        source_type=fact.source_type,
        observed_at=fact.observed_at,
    )


def _predicted_only_summary() -> ObservationSummary:
    return ObservationSummary(
        status=ObservationComparisonStatus.PREDICTED_ONLY.value,
        observed_facts_count=0,
        confirmed_count=0,
        mismatch_count=0,
        observed_only_count=0,
        predicted_only_count=0,
        unknown_count=0,
        confidence_impact='none',
        summary='No observed player data is attached to this simulation yet. Results are predicted from current mechanics rules.',
    )


def _confidence_impact(diffs: list[PredictionObservationDiff]) -> str:
    mismatches = [diff for diff in diffs if diff.status == ObservationComparisonStatus.MISMATCH.value]
    if any(diff.severity == ObservationSeverity.HIGH.value for diff in mismatches):
        return 'reduce_confidence'
    if mismatches:
        return 'review_required'
    if any(diff.status == ObservationComparisonStatus.OBSERVED_ONLY.value for diff in diffs):
        return 'review_required'
    if any(diff.status == ObservationComparisonStatus.CONFIRMED.value for diff in diffs):
        return 'increase_possible'
    return 'unknown'


def _summary_text(confirmed: int, mismatch: int, observed_only: int, unknown: int) -> str:
    parts: list[str] = []
    if confirmed:
        parts.append(f'{confirmed} prediction{"s" if confirmed != 1 else ""} match observed data')
    if mismatch:
        parts.append(f'{mismatch} prediction{"s" if mismatch != 1 else ""} differ from observed data and should be reviewed')
    if observed_only:
        parts.append(f'{observed_only} observed fact{"s" if observed_only != 1 else ""} have no matching prediction')
    if unknown:
        parts.append(f'{unknown} observed fact{"s" if unknown != 1 else ""} are incomplete or unknown')
    return '. '.join(parts) + ('.' if parts else 'Observed data is attached, but no comparable predictions were found.')


def _normalise(value: Any) -> Any:
    if isinstance(value, list):
        return sorted(str(item) for item in value)
    if isinstance(value, dict):
        return {str(key): value[key] for key in sorted(value)}
    return value
