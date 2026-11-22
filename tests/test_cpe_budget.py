"""Construction-point arithmetic, build order, placement checks and bounds."""
from __future__ import annotations

import json
import sys
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps' / 'api' / 'src'))

from edfinder_api.cpe import Body, Plan, Site, evaluate_plan  # noqa: E402
from edfinder_api.cpe.catalogue import facility  # noqa: E402
from edfinder_api.cpe.explain import explain_rule  # noqa: E402
from edfinder_api.cpe.plan import PlanInvalid, validate_plan  # noqa: E402
from edfinder_api.cpe.rules import RULEBOOK_RELEASE, rule  # noqa: E402
from edfinder_api.mechanics import cp_rules  # noqa: E402


BODY = Body('A', 64, 64, True, True)
PRIMARY = Site('primary', 'orbital_outpost_civilian', 'A', 'orbital', 'complete')


def site(site_id: str, facility_id: str, status: str = 'planned', **kwargs) -> Site:
    location = facility(facility_id).allowed_location
    return Site(site_id, facility_id, kwargs.pop('body_ref', 'A'),
                kwargs.pop('slot_kind', 'surface' if location == 'surface' else 'orbital'),
                status, **kwargs)


def plan(*sites: Site, bodies: tuple[Body, ...] = (BODY,)) -> Plan:
    return Plan(1, bodies, sites)


def funds(count: int) -> tuple[Site, ...]:
    return tuple(site(f'fund-{i}', 'orbital_installation_space_farm', 'complete') for i in range(count))


def balances(evaluation) -> tuple[tuple[int, int], tuple[int, int]]:
    return ((evaluation.now.t2, evaluation.now.t3),
            (evaluation.after_plan.t2, evaluation.after_plan.t3))


def test_primary_coriolis_is_free_earns_rewards_and_does_not_count() -> None:
    result = evaluate_plan(plan(site('primary', 'coriolis_station', 'complete')))
    assert balances(result) == ((0, 1), (0, 1))
    step = result.steps[0]
    assert (step.cost_t2, step.cost_t3, step.reward_t2, step.reward_t3) == (0, 0, 0, 1)
    assert step.counted_port_number is None
    assert step.rule_ids == ('RB-0004',)
    assert step.result == 'COUNTED' and step.view == 'now'
    assert (result.next_port_cost.t2, result.next_port_cost.t3) == (3, 6)


def test_complete_ports_use_one_shared_counter_through_sixth_port() -> None:
    ids = ('coriolis_station', 'coriolis_station', 'orbis_t3',
           'coriolis_station', 'coriolis_station', 'coriolis_station')
    result = evaluate_plan(plan(PRIMARY, *(site(str(i), fid, 'complete') for i, fid in enumerate(ids, 1))))
    assert [step.counted_port_number for step in result.steps] == [None, 1, 2, 3, 4, 5, 6]
    assert [(step.cost_t2, step.cost_t3) for step in result.steps] == [
        (0, 0), (3, 0), (3, 0), (0, 12), (7, 0), (9, 0), (11, 0)
    ]
    assert [(step.t2_before, step.t3_before, step.t2_after, step.t3_after) for step in result.steps] == [
        (0, 0, 1, 0), (1, 0, -2, 1), (-2, 1, -5, 2), (-5, 2, -5, -10),
        (-5, -10, -12, -9), (-12, -9, -21, -8), (-21, -8, -32, -7)
    ]
    assert balances(result) == ((-32, -7), (-32, -7))
    assert all(step.result == 'COUNTED' for step in result.steps)
    assert result.steps[-1].rule_ids == ('RB-0003', 'RB-0005', 'RB-0006')
    assert (result.next_port_cost.t2, result.next_port_cost.t3) == (13, 36)
    assert result.next_port_cost.t2 == cp_rules.t2_port_yellow_cost(7)
    assert result.next_port_cost.t3 == cp_rules.t3_port_green_cost(7)


def test_building_ports_pay_and_count_now_then_earn_before_planned_sites() -> None:
    # A planned site may appear before already-started builds in the list.
    result = evaluate_plan(plan(PRIMARY, *funds(5), site('planned', 'coriolis_station'),
                                site('building', 'coriolis_station', 'building')))
    assert balances(result) == ((3, 0), (0, 2))
    planned, building = result.steps[-2:]
    assert (building.cost_t2, building.cost_t3, building.counted_port_number) == (3, 0, 1)
    assert (building.reward_t2, building.reward_t3) == (0, 0)
    assert (building.t2_after, building.t3_after, building.result, building.view) == (3, 0, 'COUNTED', 'now')
    assert (planned.cost_t2, planned.t2_before, planned.t3_before, planned.counted_port_number) == (3, 3, 1, 2)
    assert (planned.t2_after, planned.t3_after, planned.result) == (0, 2, 'OK')


def test_building_rewards_can_pay_a_planned_port() -> None:
    # Six support buildings finish first and make the first T3 port affordable.
    supports = tuple(site(f'medical-{i}', 'orbital_installation_medical', 'building') for i in range(6))
    result = evaluate_plan(plan(PRIMARY, *funds(5), *supports, site('orbis', 'orbis_t3')))
    assert balances(result) == ((0, 0), (0, 0))
    step = result.steps[-1]
    assert (step.t2_before, step.t3_before, step.cost_t3, step.result) == (0, 6, 6, 'OK')
    assert step.counted_port_number == 1


def test_demolished_sites_count_for_nothing() -> None:
    result = evaluate_plan(plan(PRIMARY, site('gone', 'coriolis_station', 'demolished'),
                                site('counted', 'coriolis_station', 'complete')))
    assert balances(result) == ((-2, 1), (-2, 1))
    ignored = result.steps[1]
    assert (ignored.result, ignored.view, ignored.counted_port_number) == ('IGNORED', 'ignored', None)
    assert (ignored.cost_t2, ignored.cost_t3, ignored.reward_t2, ignored.reward_t3,
            ignored.t2_before, ignored.t3_before, ignored.t2_after, ignored.t3_after) == (0,) * 8
    assert result.steps[2].counted_port_number == 1


def test_blocked_t3_does_not_advance_counter_and_payers_fit_known_free_slots() -> None:
    result = evaluate_plan(plan(PRIMARY, *funds(2), site('blocked', 'orbis_t3'),
                                site('passes', 'coriolis_station'),
                                bodies=(replace(BODY, surface_slots=0),)))
    assert balances(result) == ((3, 0), (0, 1))
    blocked, passed = result.steps[-2:]
    assert blocked.result == 'BLOCKED' and blocked.reasons == ('INSUFFICIENT_T3',)
    assert blocked.counted_port_number is None
    assert (blocked.cost_t2, blocked.cost_t3, blocked.t2_before, blocked.t3_before,
            blocked.t2_after, blocked.t3_after) == (0, 6, 3, 0, 3, 0)
    assert (passed.counted_port_number, passed.cost_t2, passed.result) == (1, 3, 'OK')
    assert blocked.payers == (
        'orbital_installation_government', 'orbital_installation_medical',
        'orbital_installation_space_bar',
    )
    for fid in blocked.payers:
        candidate = facility(fid)
        assert cp_rules.escalating_port_tier(candidate) is None
        assert candidate.green_cp_generated >= 1 and candidate.green_cp_cost == 0
        assert candidate.can_go_orbital
    assert not passed.payers


def test_payers_exclude_unmet_prerequisites_before_truncating() -> None:
    result = evaluate_plan(plan(PRIMARY, site('blocked', 'orbis_t3'),
                                bodies=(replace(BODY, surface_slots=0),)))
    step = result.steps[-1]
    assert (step.result, step.reasons, step.t2_before, step.t3_before) == (
        'BLOCKED', ('INSUFFICIENT_T3',), 1, 0,
    )
    assert step.payers == (
        'orbital_installation_government', 'orbital_installation_medical',
        'orbital_installation_space_bar',
    )
    assert {
        'orbital_installation_military', 'orbital_installation_research_station',
        'orbital_installation_security_station', 'tourism_installation',
    }.isdisjoint(step.payers)


def test_payers_include_candidates_with_prerequisites_available_earlier() -> None:
    result = evaluate_plan(plan(PRIMARY,
                                site('relay', 'orbital_installation_relay_station', 'complete'),
                                site('blocked', 'orbis_t3'),
                                bodies=(replace(BODY, surface_slots=0),)))
    step = result.steps[-1]
    assert (step.result, step.reasons) == ('BLOCKED', ('INSUFFICIENT_T3',))
    assert step.payers == (
        'orbital_installation_government', 'orbital_installation_medical',
        'orbital_installation_security_station', 'orbital_installation_space_bar',
    )


@pytest.mark.parametrize(('fund_count', 'expected_payers'), [
    (0, ()),
    (1, ('orbital_installation_government', 'orbital_installation_medical',
         'orbital_installation_space_bar')),
])
def test_payers_must_be_affordable_at_the_blocked_step(fund_count, expected_payers) -> None:
    result = evaluate_plan(plan(site('primary', 'coriolis_station', 'complete'),
                                *funds(fund_count), site('blocked', 'orbis_t3'),
                                bodies=(replace(BODY, surface_slots=0),)))
    step = result.steps[-1]
    assert (step.result, step.reasons, step.t2_before, step.t3_before) == (
        'BLOCKED', ('INSUFFICIENT_T3',), fund_count, 1,
    )
    assert step.payers == expected_payers


def test_payers_sort_by_reward_before_id_and_ignore_full_or_unknown_slots() -> None:
    result = evaluate_plan(plan(PRIMARY, site('blocked', 'orbis_t3')))
    assert result.steps[-1].payers == (
        'surface_settlement_agriculture_t2_l', 'surface_settlement_extraction_t2_l',
        'surface_settlement_industrial_t2_l', 'surface_settlement_military_t2_l',
        'surface_settlement_research_bio_t2_l',
    )
    for body in (replace(BODY, orbital_slots=1, surface_slots=0),
                 replace(BODY, orbital_slots=None, surface_slots=None)):
        result = evaluate_plan(plan(PRIMARY, site('blocked', 'orbis_t3'), bodies=(body,)))
        assert result.steps[-1].payers == ()


def test_insufficient_t2_payers_and_no_free_slot_lookup() -> None:
    result = evaluate_plan(plan(PRIMARY, site('blocked', 'coriolis_station'),
                                bodies=(replace(BODY, orbital_slots=1),)))
    step = result.steps[-1]
    assert step.result == 'BLOCKED' and 'INSUFFICIENT_T2' in step.reasons
    assert 'NO_FREE_SLOT' in step.reasons
    assert step.payers == (
        'surface_planetary_port_outpost_civilian', 'surface_planetary_port_outpost_industrial',
        'surface_planetary_port_outpost_scientific', 'surface_settlement_agriculture_t1_m',
        'surface_settlement_agriculture_t1_s',
    )
    assert balances(result) == ((1, 0), (1, 0))


def test_zero_cost_does_not_block_with_negative_totals() -> None:
    result = evaluate_plan(plan(PRIMARY, site('debt-t2', 'coriolis_station', 'complete'),
                                site('debt-t3', 'orbis_t3', 'complete'),
                                site('free', 'orbital_installation_space_farm')))
    assert balances(result) == ((-2, -5), (-1, -5))
    step = result.steps[-1]
    assert (step.result, step.cost_t2, step.cost_t3, step.reasons) == ('OK', 0, 0, ())


def test_industrial_hub_needs_an_earlier_or_started_mining_outpost() -> None:
    hub = site('hub', 'industrial_facility')
    mine = site('mine', 'orbital_installation_mining_outpost')
    early = evaluate_plan(plan(PRIMARY, hub, mine))
    assert early.steps[1].result == 'BLOCKED' and early.steps[1].reasons == ('PREREQUISITE_MISSING',)
    assert balances(early) == ((1, 0), (2, 0))
    ordered = evaluate_plan(plan(PRIMARY, mine, hub))
    assert ordered.steps[-1].result == 'OK'
    assert balances(ordered) == ((1, 0), (1, 1))
    started = evaluate_plan(plan(PRIMARY, hub, replace(mine, status='building')))
    assert started.steps[1].result == 'OK'
    assert balances(started) == ((1, 0), (1, 1))
    demolished = evaluate_plan(plan(PRIMARY, hub, replace(mine, status='demolished')))
    assert demolished.steps[1].reasons == ('PREREQUISITE_MISSING',)


def test_blocked_prerequisite_does_not_satisfy_later_hub() -> None:
    full = replace(BODY, orbital_slots=1)
    result = evaluate_plan(plan(PRIMARY, site('mine', 'orbital_installation_mining_outpost'),
                                site('hub', 'industrial_facility'), bodies=(full,)))
    assert result.steps[1].reasons == ('NO_FREE_SLOT',)
    assert result.steps[2].reasons == ('PREREQUISITE_MISSING',)
    assert balances(result) == ((1, 0), (1, 0))


def test_military_hub_requires_the_installation_not_only_a_settlement() -> None:
    settlement = site('settlement', 'surface_settlement_military_t1_s', 'complete')
    hub = site('hub', 'military_installation')
    missing = evaluate_plan(plan(PRIMARY, settlement, hub))
    assert missing.steps[-1].reasons == ('PREREQUISITE_MISSING',)
    assert balances(missing) == ((2, 0), (2, 0))
    satisfied = evaluate_plan(plan(PRIMARY, settlement,
                                    site('installation', 'orbital_installation_military'), hub))
    assert [step.result for step in satisfied.steps[-2:]] == ['OK', 'OK']
    assert balances(satisfied) == ((2, 0), (0, 2))


def test_known_slots_fill_and_block_the_next_site() -> None:
    result = evaluate_plan(plan(PRIMARY, site('first', 'orbital_installation_space_farm'),
                                site('second', 'orbital_installation_space_farm'),
                                bodies=(replace(BODY, orbital_slots=2),)))
    assert result.steps[1].result == 'OK'
    assert result.steps[2].result == 'BLOCKED' and result.steps[2].reasons == ('NO_FREE_SLOT',)
    assert balances(result) == ((1, 0), (2, 0))


def test_unknown_slots_allow_payment_and_reward_with_explicit_reason() -> None:
    result = evaluate_plan(plan(PRIMARY, site('medical', 'orbital_installation_medical'),
                                bodies=(replace(BODY, orbital_slots=None),)))
    step = result.steps[-1]
    assert (step.result, step.reasons) == ('NEEDS_INFO', ('SLOTS_UNKNOWN',))
    assert (step.cost_t2, step.reward_t3, step.t2_after, step.t3_after) == (1, 1, 0, 1)
    assert balances(result) == ((1, 0), (0, 1))


def test_not_placed_is_needs_info_and_assumed_to_go_ahead() -> None:
    result = evaluate_plan(plan(PRIMARY, site('unplaced', 'orbital_installation_space_farm', body_ref=None)))
    assert result.steps[-1].result == 'NEEDS_INFO'
    assert 'NOT_PLACED' in result.steps[-1].reasons
    assert balances(result) == ((1, 0), (2, 0))


@pytest.mark.parametrize(('landable', 'reason', 'expected_result', 'after'), [
    (False, 'NOT_LANDABLE', 'BLOCKED', (1, 0)),
    (None, 'LANDABLE_UNKNOWN', 'NEEDS_INFO', (2, 0)),
])
def test_surface_landability(landable, reason, expected_result, after) -> None:
    result = evaluate_plan(plan(PRIMARY, site('surface', 'surface_settlement_agriculture_t1_s'),
                                bodies=(replace(BODY, is_landable=landable),)))
    assert result.steps[-1].result == expected_result and reason in result.steps[-1].reasons
    assert balances(result) == ((1, 0), after)


@pytest.mark.parametrize(('ringed', 'reason', 'expected_result', 'after', 'number'), [
    (False, 'NOT_RINGED', 'BLOCKED', (3, 0), None),
    (None, 'RINGED_UNKNOWN', 'NEEDS_INFO', (0, 1), 1),
])
def test_asteroid_base_needs_rings(ringed, reason, expected_result, after, number) -> None:
    result = evaluate_plan(plan(PRIMARY, *funds(2), site('asteroid', 'asteroid_base'),
                                bodies=(replace(BODY, is_ringed=ringed),)))
    assert result.steps[-1].result == expected_result and reason in result.steps[-1].reasons
    assert result.steps[-1].counted_port_number == number
    assert balances(result) == ((3, 0), after)


def test_six_building_sites_report_plan_level_rule_check() -> None:
    buildings = tuple(site(str(i), 'orbital_installation_space_farm', 'building') for i in range(6))
    result = evaluate_plan(plan(PRIMARY, *buildings))
    assert balances(result) == ((1, 0), (7, 0))
    assert len(result.plan_checks) == 1
    assert result.plan_checks[0].code == 'MORE_THAN_FIVE_BUILDING'
    assert result.plan_checks[0].rule_ids == ('RB-0007',)
    assert all('MORE_THAN_FIVE_BUILDING' not in step.reasons for step in result.steps)
    assert not evaluate_plan(plan(PRIMARY, *buildings[:5])).plan_checks


VALID_PLAN = plan(PRIMARY)
INVALID_PLANS = [
    ('BAD_SYSTEM', replace(VALID_PLAN, system_id64=0)),
    ('BAD_SYSTEM', replace(VALID_PLAN, system_id64=2**63)),
    ('NO_SITES', plan()),
    ('TOO_MANY_SITES', plan(*(replace(PRIMARY, site_id=str(i)) for i in range(301)))),
    ('TOO_MANY_BODIES', replace(VALID_PLAN, bodies=tuple(replace(BODY, body_ref=str(i)) for i in range(1001)))),
    ('BAD_SITE_ID', plan(replace(PRIMARY, site_id=''))),
    ('BAD_SITE_ID', plan(replace(PRIMARY, site_id='x' * 65))),
    ('BAD_SITE_ID', plan(PRIMARY, PRIMARY)),
    ('BAD_NAME', plan(replace(PRIMARY, name='x' * 65))),
    ('UNKNOWN_FACILITY', plan(replace(PRIMARY, facility_id='missing'))),
    ('UNKNOWN_FACILITY', plan(replace(PRIMARY, facility_id='colony_ship'))),
    ('UNKNOWN_BODY', plan(replace(PRIMARY, body_ref='missing'))),
    ('BAD_SLOTS', plan(PRIMARY, bodies=(replace(BODY, orbital_slots=-1),))),
    ('BAD_SLOTS', plan(PRIMARY, bodies=(replace(BODY, surface_slots=65),))),
    ('LOCATION_MISMATCH', plan(replace(PRIMARY, slot_kind='surface'))),
    ('LOCATION_MISMATCH', plan(PRIMARY, site('surface', 'refinery', slot_kind='orbital'))),
    ('LOCATION_MISMATCH', plan(PRIMARY, site('ringed', 'asteroid_base', slot_kind='surface'))),
    ('PRIMARY_NOT_PORT', plan(site('primary', 'orbital_installation_space_farm', 'complete'))),
    ('PRIMARY_DEMOLISHED', plan(replace(PRIMARY, status='demolished'))),
]


@pytest.mark.parametrize(('code', 'invalid'), INVALID_PLANS, ids=[code for code, _ in INVALID_PLANS])
def test_validation_reports_issues_and_evaluation_refuses(code: str, invalid: Plan) -> None:
    issues = validate_plan(invalid)
    assert code in {issue.code for issue in issues}
    assert all(issue.message for issue in issues)
    with pytest.raises(PlanInvalid) as caught:
        evaluate_plan(invalid)
    assert list(caught.value.issues) == issues


def test_inclusive_bounds_are_valid() -> None:
    maximum = plan(*(replace(PRIMARY, site_id=str(i), name='x' * 64) for i in range(300)),
                   bodies=(replace(BODY, orbital_slots=0, surface_slots=64),))
    assert validate_plan(replace(maximum, system_id64=2**63 - 1)) == []
    assert validate_plan(plan(replace(PRIMARY, site_id='x' * 64), bodies=(replace(BODY, orbital_slots=None),))) == []
    assert validate_plan(replace(VALID_PLAN, bodies=tuple(replace(BODY, body_ref='A' if i == 0 else str(i))
                                                        for i in range(1000)))) == []


def test_every_emitted_rule_resolves_and_explanations_include_source_quote() -> None:
    buildings = tuple(site(f'building-{i}', 'orbital_installation_space_farm', 'building') for i in range(6))
    result = evaluate_plan(plan(PRIMARY, site('port', 'coriolis_station', 'complete'),
                                *buildings, site('support', 'orbital_installation_medical'),
                                site('blocked', 'orbis_t3')))
    assert result.rulebook_release == RULEBOOK_RELEASE
    emitted = {rid for step in result.steps for rid in step.rule_ids}
    emitted.update(rid for check in result.plan_checks for rid in check.rule_ids)
    assert emitted == {'RB-0002', 'RB-0003', 'RB-0004', 'RB-0005', 'RB-0006', 'RB-0007'}
    for rid in emitted:
        reference = rule(rid)
        explanation = explain_rule(rid)
        assert explanation == {key: getattr(reference, key) for key in
                               ('id', 'title', 'status', 'statement', 'quote', 'page')}
        assert explanation['quote']
    assert set(result.summary) == {'OK', 'BLOCKED', 'NEEDS_INFO', 'COUNTED', 'IGNORED'}
    assert sum(result.summary.values()) == len(result.steps)
    for name, count in result.summary.items():
        assert count == sum(step.result == name for step in result.steps)
    assert json.loads(json.dumps(asdict(result)))['rulebook_release'] == RULEBOOK_RELEASE
    with pytest.raises(FrozenInstanceError):
        result.rulebook_release = 'changed'
    with pytest.raises(FrozenInstanceError):
        result.steps[0].cost_t2 = 999
    with pytest.raises(FrozenInstanceError):
        PRIMARY.name = 'changed'
    with pytest.raises(FrozenInstanceError):
        BODY.orbital_slots = 1
    with pytest.raises(FrozenInstanceError):
        VALID_PLAN.system_id64 = 2


def test_planned_primary_is_free_earns_reward_and_does_not_advance_counter() -> None:
    result = evaluate_plan(plan(site('primary', 'coriolis_station')))
    assert balances(result) == ((0, 0), (0, 1))
    primary = result.steps[0]
    assert (primary.result, primary.view, primary.counted_port_number) == ('OK', 'after_plan', None)
    assert (primary.cost_t2, primary.cost_t3, primary.reward_t2, primary.reward_t3) == (0, 0, 0, 1)
    assert primary.rule_ids == ('RB-0004',)
    assert (result.next_port_cost.t2, result.next_port_cost.t3) == (3, 6)


def test_needs_info_prerequisite_passes_and_satisfies_later_hub() -> None:
    result = evaluate_plan(plan(PRIMARY,
                                site('mine', 'orbital_installation_mining_outpost', body_ref=None),
                                site('hub', 'industrial_facility')))
    assert result.steps[1].result == 'NEEDS_INFO'
    assert 'NOT_PLACED' in result.steps[1].reasons
    assert result.steps[2].result == 'OK'
    assert balances(result) == ((1, 0), (1, 1))


def test_demolished_sites_leave_slots_free_and_building_sites_occupy_slots() -> None:
    known = replace(BODY, orbital_slots=2)
    sites = (PRIMARY, site('removed', 'orbital_installation_space_farm', 'demolished'),
             site('started', 'orbital_installation_space_farm', 'building'),
             site('blocked', 'orbital_installation_space_farm'))
    full = evaluate_plan(plan(*sites, bodies=(known,)))
    assert full.steps[-1].reasons == ('NO_FREE_SLOT',)
    assert balances(full) == ((1, 0), (2, 0))
    freed = evaluate_plan(plan(*sites[:2], replace(sites[2], status='demolished'), sites[3], bodies=(known,)))
    assert freed.steps[-1].result == 'OK'
    assert balances(freed) == ((1, 0), (2, 0))


def test_slot_capacity_is_separate_for_each_body_and_kind() -> None:
    body_a = replace(BODY, orbital_slots=1, surface_slots=1)
    body_b = replace(BODY, body_ref='B', orbital_slots=1, surface_slots=0)
    result = evaluate_plan(plan(PRIMARY,
                                site('other-orbit', 'orbital_installation_space_farm', body_ref='B'),
                                site('surface', 'surface_settlement_agriculture_t1_s'),
                                bodies=(body_a, body_b)))
    assert [step.result for step in result.steps] == ['COUNTED', 'OK', 'OK']
    assert balances(result) == ((1, 0), (3, 0))


def test_payer_fit_uses_slots_after_earlier_passed_steps() -> None:
    body = replace(BODY, orbital_slots=2, surface_slots=0)
    result = evaluate_plan(plan(PRIMARY, site('before', 'orbis_t3'),
                                site('uses-slot', 'orbital_installation_space_farm'),
                                site('after', 'orbis_t3'), bodies=(body,)))
    assert result.steps[1].payers == (
        'orbital_installation_government', 'orbital_installation_medical',
        'orbital_installation_space_bar',
    )
    assert result.steps[2].result == 'OK'
    assert result.steps[3].payers == ()
    assert {'INSUFFICIENT_T3', 'NO_FREE_SLOT'} <= set(result.steps[3].reasons)
    assert balances(result) == ((1, 0), (2, 0))
