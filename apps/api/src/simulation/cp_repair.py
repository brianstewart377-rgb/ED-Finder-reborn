"""Small CP sequence repair assistant for Simulation Preview.

This module does not optimise the full build order. It reads the already-produced
CP timeline and emits conservative, actionable repair suggestions for the user's
current sequence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from edfinder_api.mechanics.cp_rules import LATE_T3_BUILD_ORDER_THRESHOLD

STANDARD_CONFIDENCE_LABELS = {
    'observed',
    'verified',
    'community_observed',
    'inferred',
    'estimated',
    'speculative',
    'unknown',
}

SEVERITY_ORDER = {
    'critical': 0,
    'high': 1,
    'medium': 2,
    'low': 3,
    'info': 4,
}

MAX_REPAIRS_PER_PROBLEM = 3
MAX_REPAIRS_TOTAL = 6


@dataclass(frozen=True)
class CPRepairAction:
    action_type: str
    facility_template_id: str | None = None
    facility_name: str | None = None
    from_step: int | None = None
    to_step: int | None = None
    target_step: int | None = None
    set_primary_port: bool | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CPRepairSuggestion:
    suggestion_id: str
    type: str
    severity: str
    summary: str
    reason: str
    affected_steps: list[int]
    expected_effect: str
    action: str
    suggested_action: CPRepairAction | None
    confidence: str = 'inferred'
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data['suggested_action'] = self.suggested_action.to_dict() if self.suggested_action else None
        return data


def build_cp_repair_suggestions(*, placements: list[Any], cp: dict[str, Any]) -> list[CPRepairSuggestion]:
    timeline = list(cp.get('timeline') or [])
    if not placements or not timeline:
        return []

    placement_by_step = {int(item.spec.build_order): item for item in placements}
    suggestions: list[CPRepairSuggestion] = []

    negative_steps = [step for step in timeline if _is_negative_step(step)]
    first_pressure_step = int(negative_steps[0].get('step', 0)) if negative_steps else None
    for step in negative_steps:
        suggestions.append(_cp_negative_suggestion(step))
        move = _move_support_earlier_suggestion(step, placements)
        if move is not None:
            suggestions.append(move)
        else:
            delay = _delay_expensive_port_suggestion(step, placement_by_step)
            if delay is not None:
                suggestions.append(delay)
            add = _add_cp_generator_suggestion(step)
            if add is not None:
                suggestions.append(add)

    fragile = _fragile_sequence_suggestion(timeline, cp)
    if fragile is not None:
        suggestions.append(fragile)
        if first_pressure_step is None and fragile.affected_steps:
            first_pressure_step = min(fragile.affected_steps)

    primary = _primary_port_suggestion(placements, first_pressure_step)
    if primary is not None:
        suggestions.append(primary)

    for step in timeline:
        t3 = _t3_escalation_suggestion(step, placement_by_step)
        if t3 is not None:
            suggestions.append(t3)

    reduce_paid_ports = _reduce_paid_port_count_suggestion(placements, negative_steps)
    if reduce_paid_ports is not None:
        suggestions.append(reduce_paid_ports)
    split_plan = _split_advanced_plan_suggestion(negative_steps)
    if split_plan is not None:
        suggestions.append(split_plan)

    return _dedupe_sort_and_cap(suggestions)


def cp_repair_suggestions_to_dict(suggestions: list[CPRepairSuggestion]) -> list[dict[str, Any]]:
    return [suggestion.to_dict() for suggestion in suggestions]


def _is_negative_step(step: dict[str, Any]) -> bool:
    return int(step.get('yellow_after', 0)) < 0 or int(step.get('green_after', 0)) < 0


def _cp_negative_suggestion(step: dict[str, Any]) -> CPRepairSuggestion:
    step_no = int(step.get('step', 0))
    yellow_after = int(step.get('yellow_after', 0))
    green_after = int(step.get('green_after', 0))
    deficit = abs(min(0, yellow_after)) + abs(min(0, green_after))
    severity = 'critical' if deficit >= 10 else 'high'
    return CPRepairSuggestion(
        suggestion_id=f'cp_negative_step_{step_no}',
        type='cp_negative_detected',
        severity=severity,
        summary=f'CP goes negative at step {step_no}',
        reason=f'{step.get("facility_name", "This step")} leaves the sequence at Yellow {yellow_after} and Green {green_after}.',
        affected_steps=[step_no],
        expected_effect='Repairing the first deficit should make the current order easier to build without changing final scoring.',
        action='Move CP-generating support earlier, mark the intended first port as primary if appropriate, or move this paid port later.',
        suggested_action=CPRepairAction(
            action_type='review_sequence',
            target_step=step_no,
            notes=['Review the nearby paid port, primary-port choice, and CP-generating support order.'],
        ),
        confidence='inferred',
        caveats=['This is a local repair suggestion, not proof of the globally optimal build order.'],
    )


def _move_support_earlier_suggestion(step: dict[str, Any], placements: list[Any]) -> CPRepairSuggestion | None:
    step_no = int(step.get('step', 0))
    needs_green = int(step.get('green_after', 0)) < 0
    candidates = [
        item for item in placements
        if int(item.spec.build_order) > step_no
        and not item.facility.is_port
        and (int(item.facility.yellow_cp_generated or 0) > 0 or int(item.facility.green_cp_generated or 0) > 0)
        and (not needs_green or int(item.facility.green_cp_generated or 0) > 0)
    ]
    if not candidates:
        return None
    candidate = sorted(candidates, key=lambda item: int(item.spec.build_order))[0]
    from_step = int(candidate.spec.build_order)
    earliest_repair_step = max(1, step_no - 1)
    target_phrase = (
        f'before step {step_no}, after any required initial anchor'
        if step_no > 1 else
        'as early as the sequence allows before the CP-negative paid port'
    )
    return CPRepairSuggestion(
        suggestion_id=f'move_{_slug(candidate.facility.id)}_before_step_{step_no}',
        type='move_cp_generator_earlier',
        severity='high' if _is_negative_step(step) else 'medium',
        summary=f'Move {candidate.facility.name} earlier',
        reason=f'{candidate.facility.name} currently appears at step {from_step} after a CP deficit has already occurred.',
        affected_steps=[step_no, from_step],
        expected_effect='Generating CP before the deficit should reduce or remove the temporary negative balance.',
        action=f'Move {candidate.facility.name} {target_phrase}.',
        suggested_action=CPRepairAction(
            action_type='move_facility_earlier',
            facility_template_id=candidate.facility.id,
            facility_name=candidate.facility.name,
            from_step=from_step,
            to_step=earliest_repair_step,
            notes=['Do not move support before the initial colony anchor unless that is valid in-game.'],
        ),
        confidence='inferred',
        caveats=[
            'The simulator does not reorder every dependency; confirm the moved facility is still valid in the intended build plan.',
            'Do not move support before the initial colony anchor unless that is valid in-game.',
        ],
    )


def _primary_port_suggestion(placements: list[Any], pressure_step: int | None) -> CPRepairSuggestion | None:
    if pressure_step is None:
        return None
    if any(item.facility.is_port and item.spec.is_primary_port for item in placements):
        return None
    ports = sorted((item for item in placements if item.facility.is_port), key=lambda item: int(item.spec.build_order))
    if not ports:
        return None
    port = ports[0]
    port_step = int(port.spec.build_order)
    if port_step > pressure_step:
        return None
    return CPRepairSuggestion(
        suggestion_id=f'mark_primary_{_slug(port.facility.id)}_step_{port_step}',
        type='mark_primary_port',
        severity='high',
        summary=f'Mark {port.facility.name} as the primary port',
        reason=f'{port.facility.name} is the earliest port and no primary port is currently marked before the early CP pressure point.',
        affected_steps=[port_step, pressure_step] if pressure_step != port_step else [port_step],
        expected_effect='Using the primary-port exemption on the intended first Main Port should reduce early paid-port CP pressure.',
        action=f'Mark {port.facility.name} at step {port_step} as the primary port if it is intended to be the initial colony Main Port.',
        suggested_action=CPRepairAction(
            action_type='mark_primary_port',
            facility_template_id=port.facility.id,
            facility_name=port.facility.name,
            target_step=port_step,
            set_primary_port=True,
            notes=['Only one port should receive the primary-port exemption.', 'Only use this if this is genuinely the first/primary colony port in-game.'],
        ),
        confidence='inferred',
        caveats=['Only use this if this is genuinely the first/primary colony port in-game.'],
    )


def _delay_expensive_port_suggestion(step: dict[str, Any], placement_by_step: dict[int, Any]) -> CPRepairSuggestion | None:
    step_no = int(step.get('step', 0))
    placement = placement_by_step.get(step_no)
    if placement is None or not placement.facility.is_port or placement.spec.is_primary_port:
        return None
    return CPRepairSuggestion(
        suggestion_id=f'delay_{_slug(placement.facility.id)}_step_{step_no}',
        type='delay_expensive_port',
        severity='high',
        summary=f'Delay {placement.facility.name} until CP is available',
        reason=f'{placement.facility.name} is a paid port at the CP-negative step and no later CP generator was available to move earlier.',
        affected_steps=[step_no],
        expected_effect='Delaying the paid port until after more CP generation should reduce the temporary deficit.',
        action=f'Move {placement.facility.name} later, after CP-generating support has created enough buffer.',
        suggested_action=CPRepairAction(
            action_type='delay_facility',
            facility_template_id=placement.facility.id,
            facility_name=placement.facility.name,
            from_step=step_no,
            notes=['Choose the later step after enough support has generated CP; this assistant does not compute a full replacement order.'],
        ),
        confidence='inferred',
        caveats=['This is a conservative local repair; verify the delayed port still fits the intended colony sequence.'],
    )


def _add_cp_generator_suggestion(step: dict[str, Any]) -> CPRepairSuggestion | None:
    step_no = int(step.get('step', 0))
    return CPRepairSuggestion(
        suggestion_id=f'add_cp_generator_before_step_{step_no}',
        type='add_cp_generator',
        severity='high',
        summary=f'Add CP-generating support before step {step_no}',
        reason='No later CP-generating support was available to move earlier for this deficit.',
        affected_steps=[step_no],
        expected_effect='Adding support that generates the missing CP before the deficit should make the paid step easier to afford.',
        action=f'Add or move a CP-generating support facility before step {step_no}, after any required initial anchor.',
        suggested_action=CPRepairAction(
            action_type='add_cp_generator',
            target_step=step_no,
            notes=['Do not assume a specific facility template; choose a valid CP-generating support facility for the plan.'],
        ),
        confidence='inferred',
        caveats=['No specific facility is recommended because this pass is not a full optimiser.'],
    )


def _t3_escalation_suggestion(step: dict[str, Any], placement_by_step: dict[int, Any]) -> CPRepairSuggestion | None:
    warnings = ' '.join(step.get('warnings') or [])
    step_no = int(step.get('step', 0))
    placement = placement_by_step.get(step_no)
    if 'T3 ports earlier' not in warnings:
        if placement is None or not placement.facility.is_port or placement.facility.tier != 3:
            return None
        if placement.spec.is_primary_port or step_no <= LATE_T3_BUILD_ORDER_THRESHOLD:
            return None
    facility_name = step.get('facility_name') or getattr(getattr(placement, 'facility', None), 'name', 'T3 port')
    facility_id = getattr(getattr(placement, 'facility', None), 'id', 't3_port')
    return CPRepairSuggestion(
        suggestion_id=f'review_t3_timing_step_{step_no}',
        type='port_escalation_pressure',
        severity='medium',
        summary=f'Review T3 timing for {facility_name}',
        reason='Late T3 or paid-port sequencing can increase CP pressure in this order.',
        affected_steps=[step_no],
        expected_effect='Moving the T3 port earlier or using a valid primary-port exemption may reduce escalation pressure.',
        action=f'Review whether {facility_name} should be earlier in the order or designated primary where appropriate.',
        suggested_action=CPRepairAction(
            action_type='review_sequence',
            facility_template_id=facility_id,
            facility_name=facility_name,
            target_step=step_no,
            notes=['T3 timing advice is conservative and should be checked against the intended staged build.'],
        ),
        confidence='inferred',
        caveats=['T3 escalation guidance is conservative and does not attempt a full reorder optimisation.'],
    )


def _fragile_sequence_suggestion(timeline: list[dict[str, Any]], cp: dict[str, Any]) -> CPRepairSuggestion | None:
    if int(cp.get('yellow_cp_final', 0)) < 0 or int(cp.get('green_cp_final', 0)) < 0:
        return None
    if not timeline:
        return None
    min_yellow = min(int(step.get('yellow_after', 0)) for step in timeline)
    min_green = min(int(step.get('green_after', 0)) for step in timeline)
    final_step = int(timeline[-1].get('step', 0))
    final_green = int(cp.get('green_cp_final', 0))
    green_before_final = [int(step.get('green_after', 0)) for step in timeline[:-1]]
    fragile_green_final = final_green > 0 and green_before_final and max(green_before_final) <= 0
    if min_yellow > 0 and min_green > 0 and not fragile_green_final:
        return None
    affected = [int(step.get('step', 0)) for step in timeline if int(step.get('yellow_after', 0)) <= 0 or int(step.get('green_after', 0)) <= 0]
    if fragile_green_final and final_step not in affected:
        affected.append(final_step)
    return CPRepairSuggestion(
        suggestion_id='fragile_sequence',
        type='sequence_is_valid_but_fragile',
        severity='medium',
        summary='This sequence is valid but fragile',
        reason='One or both CP balances reach zero or only become safely positive at the end, leaving little buffer if the build order changes.',
        affected_steps=affected[:6],
        expected_effect='Moving CP-generating support earlier would create a safer buffer.',
        action='Move an early CP-generating support facility before the first low-buffer step if practical, after any required initial anchor.',
        suggested_action=CPRepairAction(
            action_type='review_sequence',
            target_step=min(affected) if affected else None,
            notes=['This is a buffer improvement, not a required repair for a currently negative final balance.'],
        ),
        confidence='inferred',
        caveats=['This suggestion is suppressed when the sequence already ends CP-negative because deficit repairs are more important.'],
    )


def _reduce_paid_port_count_suggestion(placements: list[Any], negative_steps: list[dict[str, Any]]) -> CPRepairSuggestion | None:
    paid_ports = [item for item in placements if item.facility.is_port and not item.spec.is_primary_port and item.facility.tier in {2, 3}]
    if len(paid_ports) < 3 or not negative_steps:
        return None
    affected = [int(step.get('step', 0)) for step in negative_steps[:3]]
    return CPRepairSuggestion(
        suggestion_id='reduce_paid_t2_t3_port_count',
        type='reduce_paid_t2_t3_port_count',
        severity='medium',
        summary='Reduce paid T2/T3 port pressure in the first phase',
        reason='Multiple paid T2/T3 ports are present while the sequence has CP deficits.',
        affected_steps=affected,
        expected_effect='Simplifying the first phase can reduce escalating port CP pressure before adding more ports later.',
        action='Move one or more paid T2/T3 ports into a later phase after CP support is online.',
        suggested_action=CPRepairAction(
            action_type='review_sequence',
            notes=['This assistant does not choose which port to remove; review the staged plan manually.'],
        ),
        confidence='inferred',
        caveats=['This is a staging suggestion, not a recommendation to remove ports from the final colony design.'],
    )


def _split_advanced_plan_suggestion(negative_steps: list[dict[str, Any]]) -> CPRepairSuggestion | None:
    severe_steps = [int(step.get('step', 0)) for step in negative_steps]
    if len(severe_steps) < 3:
        return None
    return CPRepairSuggestion(
        suggestion_id='split_advanced_plan',
        type='split_advanced_plan',
        severity='medium',
        summary='Split this advanced build into a simpler first phase',
        reason='Several CP-negative steps suggest the current order may be trying to place too much before enough CP support is online.',
        affected_steps=severe_steps[:6],
        expected_effect='A smaller first phase should be easier to stabilise before adding later ports or expensive facilities.',
        action='Split the build into a CP-generating first phase and a later expansion phase.',
        suggested_action=CPRepairAction(
            action_type='split_plan',
            notes=['This is planning guidance only; no automatic phase split is computed.'],
        ),
        confidence='inferred',
        caveats=['A full optimiser is intentionally deferred.'],
    )


def _dedupe_sort_and_cap(suggestions: list[CPRepairSuggestion]) -> list[CPRepairSuggestion]:
    seen: set[str] = set()
    ordered: list[CPRepairSuggestion] = []
    for suggestion in suggestions:
        if suggestion.confidence not in STANDARD_CONFIDENCE_LABELS or not suggestion.suggestion_id:
            continue
        if suggestion.suggestion_id in seen:
            continue
        seen.add(suggestion.suggestion_id)
        ordered.append(suggestion)

    ordered = sorted(ordered, key=lambda item: (SEVERITY_ORDER.get(item.severity, 99), item.affected_steps or [999], item.type))
    per_problem: dict[int, int] = {}
    capped: list[CPRepairSuggestion] = []
    for suggestion in ordered:
        problem_key = min(suggestion.affected_steps) if suggestion.affected_steps else -1
        count = per_problem.get(problem_key, 0)
        if count >= MAX_REPAIRS_PER_PROBLEM:
            continue
        capped.append(suggestion)
        per_problem[problem_key] = count + 1
        if len(capped) >= MAX_REPAIRS_TOTAL:
            break
    return capped


def _slug(value: str) -> str:
    return ''.join(char if char.isalnum() else '_' for char in value.lower()).strip('_') or 'item'
