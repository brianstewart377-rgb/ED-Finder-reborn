"""Pure construction-point accounting for the current and completed plan views."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from edfinder_api.domain import facilities
from edfinder_api.mechanics import cp_rules

from .catalogue import facility
from .checks import PlanCheck, check_step, payers_for, plan_checks
from .plan import Plan, PlanInvalid, Site, validate_plan
from .rules import RULEBOOK_RELEASE


@dataclass(frozen=True)
class Totals:
    t2: int
    t3: int


@dataclass(frozen=True)
class StepResult:
    site_id: str
    facility_id: str
    status: str
    view: Literal['now', 'after_plan', 'ignored']
    result: Literal['OK', 'BLOCKED', 'NEEDS_INFO', 'COUNTED', 'IGNORED']
    counted_port_number: int | None
    cost_t2: int
    cost_t3: int
    reward_t2: int
    reward_t3: int
    t2_before: int
    t3_before: int
    t2_after: int
    t3_after: int
    reasons: tuple[str, ...]
    rule_ids: tuple[str, ...]
    payers: tuple[str, ...]


@dataclass(frozen=True)
class Evaluation:
    rulebook_release: str
    now: Totals
    after_plan: Totals
    steps: tuple[StepResult, ...]
    plan_checks: tuple[PlanCheck, ...]
    summary: dict[str, int]
    next_port_cost: Totals


def _cost(
    template: facilities.FacilityTemplate, primary: bool, number: int,
) -> tuple[int, int, tuple[str, ...]]:
    if primary:
        return 0, 0, ('RB-0004',)
    tier = cp_rules.escalating_port_tier(template)
    if tier is not None:
        rules = ('RB-0003', 'RB-0005', 'RB-0006')
        if tier == 2:
            return cp_rules.t2_port_yellow_cost(number), 0, rules
        return 0, cp_rules.t3_port_green_cost(number), rules
    rules = ('RB-0002',) if any((
        template.yellow_cp_cost, template.green_cp_cost,
        template.yellow_cp_generated, template.green_cp_generated,
    )) else ()
    return template.yellow_cp_cost, template.green_cp_cost, rules


def evaluate_plan(plan: Plan) -> Evaluation:
    issues = validate_plan(plan)
    if issues:
        raise PlanInvalid(issues)

    t2 = t3 = counted = 0
    results: dict[str, StepResult] = {}
    bodies = {body.body_ref: body for body in plan.bodies}
    occupied: Counter[tuple[str, str]] = Counter()
    available: set[str] = set()

    def occupy(site: Site) -> None:
        available.add(site.facility_id)
        if site.body_ref is not None:
            occupied[(site.body_ref, site.slot_kind)] += 1

    # Existing construction is accounted for first, regardless of where planned
    # steps appear in the list. It is never retroactively blocked.
    for index, site in enumerate(plan.sites):
        if site.status not in ('complete', 'building'):
            continue
        template = facility(site.facility_id)
        escalating = index != 0 and cp_rules.escalating_port_tier(template) is not None
        cost_t2, cost_t3, rules = _cost(template, index == 0, counted + 1)
        reward_t2 = template.yellow_cp_generated if site.status == 'complete' else 0
        reward_t3 = template.green_cp_generated if site.status == 'complete' else 0
        before_t2, before_t3 = t2, t3
        t2 += reward_t2 - cost_t2
        t3 += reward_t3 - cost_t3
        if escalating:
            counted += 1
        occupy(site)
        results[site.site_id] = StepResult(
            site.site_id, site.facility_id, site.status, 'now', 'COUNTED',
            counted if escalating else None, cost_t2, cost_t3,
            reward_t2, reward_t3,
            before_t2, before_t3, t2, t3, (), rules, (),
        )

    now = Totals(t2, t3)
    # Building rewards are prospective and credited before planned steps.
    for site in plan.sites:
        if site.status == 'building':
            template = facility(site.facility_id)
            t2 += template.yellow_cp_generated
            t3 += template.green_cp_generated

    for index, site in enumerate(plan.sites):
        if site.status != 'planned':
            continue
        template = facility(site.facility_id)
        escalating = index != 0 and cp_rules.escalating_port_tier(template) is not None
        cost_t2, cost_t3, rules = _cost(template, index == 0, counted + 1)
        before_t2, before_t3 = t2, t3
        blocked, unknown = check_step(
            site, template, bodies.get(site.body_ref), occupied, available,
            t2, t3, cost_t2, cost_t3,
        )
        reasons = blocked + unknown
        payers = payers_for(reasons, plan.bodies, occupied, available, t2, t3)
        if blocked:
            result = 'BLOCKED'
        else:
            result = 'NEEDS_INFO' if unknown else 'OK'
            t2 += template.yellow_cp_generated - cost_t2
            t3 += template.green_cp_generated - cost_t3
            if escalating:
                counted += 1
            occupy(site)
        results[site.site_id] = StepResult(
            site.site_id, site.facility_id, site.status, 'after_plan', result,
            counted if escalating and not blocked else None,
            cost_t2, cost_t3, template.yellow_cp_generated, template.green_cp_generated,
            before_t2, before_t3, t2, t3, reasons, rules, payers,
        )

    for site in plan.sites:
        if site.status == 'demolished':
            results[site.site_id] = StepResult(
                site.site_id, site.facility_id, site.status, 'ignored', 'IGNORED',
                None, 0, 0, 0, 0, 0, 0, 0, 0, (), (), (),
            )
    steps = tuple(results[site.site_id] for site in plan.sites)
    summary = {result: sum(step.result == result for step in steps)
               for result in ('OK', 'BLOCKED', 'NEEDS_INFO', 'COUNTED', 'IGNORED')}
    return Evaluation(
        RULEBOOK_RELEASE, now, Totals(t2, t3), steps, plan_checks(plan), summary,
        Totals(cp_rules.t2_port_yellow_cost(counted + 1), cp_rules.t3_port_green_cost(counted + 1)),
    )
