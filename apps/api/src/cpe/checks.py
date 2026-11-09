"""Order checks and the bounded facility lookup for funding a planned step."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from edfinder_api.domain import facilities
from edfinder_api.mechanics import cp_rules

from .catalogue import planner_facilities, prerequisites_for
from .plan import Body, Plan, Site


@dataclass(frozen=True)
class PlanCheck:
    code: str
    message: str
    rule_ids: tuple[str, ...]


def plan_checks(plan: Plan) -> tuple[PlanCheck, ...]:
    if sum(site.status == 'building' for site in plan.sites) > 5:
        return (PlanCheck('MORE_THAN_FIVE_BUILDING', 'More than five sites are under construction.', ('RB-0007',)),)
    return ()


def check_step(
    site: Site,
    template: facilities.FacilityTemplate,
    body: Body | None,
    occupied: Mapping[tuple[str, str], int],
    available: set[str],
    t2: int,
    t3: int,
    cost_t2: int,
    cost_t3: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return blocking reasons and missing information, without changing state."""
    blocked: list[str] = []
    unknown: list[str] = []
    if cost_t2 > 0 and t2 < cost_t2:
        blocked.append('INSUFFICIENT_T2')
    if cost_t3 > 0 and t3 < cost_t3:
        blocked.append('INSUFFICIENT_T3')
    if any(not entry.intersection(available) for entry in prerequisites_for(site.facility_id)):
        blocked.append('PREREQUISITE_MISSING')
    if body is None:
        unknown.append('NOT_PLACED')
    else:
        slots = body.orbital_slots if site.slot_kind == 'orbital' else body.surface_slots
        if slots is None:
            unknown.append('SLOTS_UNKNOWN')
        elif occupied.get((body.body_ref, site.slot_kind), 0) >= slots:
            blocked.append('NO_FREE_SLOT')
        if site.slot_kind == 'surface':
            if body.is_landable is False:
                blocked.append('NOT_LANDABLE')
            elif body.is_landable is None:
                unknown.append('LANDABLE_UNKNOWN')
        if template.needs_ringed_body:
            if body.is_ringed is False:
                blocked.append('NOT_RINGED')
            elif body.is_ringed is None:
                unknown.append('RINGED_UNKNOWN')
    return tuple(blocked), tuple(unknown)


def _fits(
    template: facilities.FacilityTemplate,
    bodies: tuple[Body, ...],
    occupied: Mapping[tuple[str, str], int],
) -> bool:
    for body in bodies:
        if (
            template.can_go_orbital
            and (not template.needs_ringed_body or body.is_ringed is True)
            and body.orbital_slots is not None
            and occupied.get((body.body_ref, 'orbital'), 0) < body.orbital_slots
        ):
            return True
        if (
            template.can_go_surface
            and body.is_landable is True
            and body.surface_slots is not None
            and occupied.get((body.body_ref, 'surface'), 0) < body.surface_slots
        ):
            return True
    return False


def payers_for(
    reasons: tuple[str, ...],
    bodies: tuple[Body, ...],
    occupied: Mapping[tuple[str, str], int],
    available: set[str],
    t2: int,
    t3: int,
) -> tuple[str, ...]:
    missing_t2 = 'INSUFFICIENT_T2' in reasons
    missing_t3 = 'INSUFFICIENT_T3' in reasons
    if not missing_t2 and not missing_t3:
        return ()
    candidates: list[tuple[int, str]] = []
    for template in planner_facilities().values():
        if cp_rules.escalating_port_tier(template) is not None:
            continue
        if any(not entry.intersection(available) for entry in prerequisites_for(template.id)):
            continue
        if template.yellow_cp_cost > 0 and t2 < template.yellow_cp_cost:
            continue
        if template.green_cp_cost > 0 and t3 < template.green_cp_cost:
            continue
        points = max(
            template.yellow_cp_generated if missing_t2 and template.yellow_cp_cost == 0 else 0,
            template.green_cp_generated if missing_t3 and template.green_cp_cost == 0 else 0,
        )
        if points > 0 and _fits(template, bodies, occupied):
            candidates.append((-points, template.id))
    return tuple(facility_id for _, facility_id in sorted(candidates)[:5])
