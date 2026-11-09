"""Prototype Guided Planner candidate reports.

This module is intentionally not wired to a public route. Stage 17N.3-B uses it
as a backend/test-first generator for Light, Medium, High, and Maxed candidate
reports before any Guided Planner UI exists.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from edfinder_api.domain.colonisation_rules import BodyEconomyProfile, profile_body
from edfinder_api.domain.facilities import FacilityTemplate, is_colony_ship
from edfinder_api.optimiser.models import CandidatePlacement, OptimiserCandidate
from edfinder_api.optimiser.plan_quality import (
    PRESET_COUNT_RANGES,
    PlanBodySlotState,
    PlanPreset,
    PlanQualityOptions,
    PlanQualityReport,
    validate_generated_plan_quality,
)
from edfinder_api.optimiser.guided_planner_models import (
    BodyRoleLabel,
    GuidedBodyContext,
    GuidedBodyRole,
    GuidedLane,
    GuidedPlanCandidateReport,
    GuidedPlanExplanation,
    GuidedPlanPlacement,
    GuidedPlanRequest,
    GuidedSystemContext,
    PRESET_DEFAULT_COUNTS,
)

@dataclass
class _BodyOption:
    body: GuidedBodyContext
    profile: BodyEconomyProfile
    score: float
    available_orbital: Optional[int]
    available_ground: Optional[int]
    role: BodyRoleLabel
    rationale: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def body_id(self) -> str:
        return str(self.body.body_id)

    @property
    def body_name(self) -> str:
        return self.body.body_name


@dataclass
class _MutableCapacity:
    orbital: Optional[int]
    ground: Optional[int]


def generate_guided_plan_report(
    request: GuidedPlanRequest,
    system: GuidedSystemContext,
    catalogue: dict[str, FacilityTemplate],
) -> GuidedPlanCandidateReport:
    """Generate one quality-gated Guided Planner prototype report."""
    target_economies = _target_economies(request, system)
    if not target_economies:
        return _no_strong_report(
            request=request,
            system=system,
            catalogue=catalogue,
            target_economies=[],
            reason='No target economy could be determined from the request or system body data.',
        )
    if not catalogue:
        return _no_strong_report(
            request=request,
            system=system,
            catalogue=catalogue,
            target_economies=target_economies,
            reason='No facility catalogue is available for Guided Planner generation.',
        )

    body_options = _rank_body_options(request, system, target_economies)
    candidate_bodies = [body for body in body_options if body.role != 'avoided' and _has_capacity(body)]
    anchor = next((body for body in candidate_bodies if _matches_any_target(body.profile, target_economies)), None)
    if anchor is None:
        return _no_strong_report(
            request=request,
            system=system,
            catalogue=catalogue,
            target_economies=target_economies,
            reason='No body with available capacity supports the requested target economy.',
            body_options=body_options,
        )

    support_templates = _support_templates(catalogue, target_economies, request.avoid_economies)
    if not support_templates:
        return _no_strong_report(
            request=request,
            system=system,
            catalogue=catalogue,
            target_economies=target_economies,
            reason='No support structures match the requested target economy pair.',
            body_options=body_options,
        )

    desired_count = _desired_count(request)
    min_count, _max_count = PRESET_COUNT_RANGES[request.preset]
    capacities = _initial_capacities(candidate_bodies)
    placements: list[GuidedPlanPlacement] = []
    placement_lanes: dict[int, GuidedLane] = {}

    port = _select_port(catalogue, request)
    if port is None:
        return _no_strong_report(
            request=request,
            system=system,
            catalogue=catalogue,
            target_economies=target_economies,
            reason='No non-colony station template is available for the plan anchor.',
            body_options=body_options,
        )

    if not _append_placement(
        placements=placements,
        placement_lanes=placement_lanes,
        template=port,
        body=anchor,
        capacities=capacities,
        is_primary_port=True,
        role=_anchor_role_label(target_economies),
        reason=f'{anchor.body_name} is the strongest available anchor for {" / ".join(target_economies)}.',
    ):
        return _no_strong_report(
            request=request,
            system=system,
            catalogue=catalogue,
            target_economies=target_economies,
            reason='The selected anchor body has no lane capacity for a main station.',
            body_options=body_options,
        )

    support_cursor = 0
    safety = 0
    while len(placements) < desired_count and safety < desired_count * max(4, len(support_templates) + len(candidate_bodies)):
        safety += 1
        template = support_templates[support_cursor % len(support_templates)]
        support_cursor += 1
        prerequisite = _first_missing_prerequisite_template(template, placements, catalogue, request.avoid_economies)
        if prerequisite and len(placements) < desired_count:
            _place_support(
                placements=placements,
                placement_lanes=placement_lanes,
                template=prerequisite,
                bodies=candidate_bodies,
                capacities=capacities,
                target_economies=target_economies,
                role='Prerequisite Support',
            )
            if len(placements) >= desired_count:
                break

        _place_support(
            placements=placements,
            placement_lanes=placement_lanes,
            template=template,
            bodies=candidate_bodies,
            capacities=capacities,
            target_economies=target_economies,
            role=_support_role(template, target_economies),
        )

        if _all_relevant_capacity_exhausted(candidate_bodies, capacities):
            break

    if len(placements) < min_count:
        attempted = _candidate_from_report(request, target_economies, placements)
        quality = _validate_report_candidate(request, target_economies, attempted, catalogue, system, placement_lanes)
        return _no_strong_report(
            request=request,
            system=system,
            catalogue=catalogue,
            target_economies=target_economies,
            reason=(
                f'Only {len(placements)} coherent placement(s) fit available capacity; '
                f'{request.preset.title()} needs at least {min_count}.'
            ),
            body_options=body_options,
            attempted_quality=quality,
        )

    candidate = _candidate_from_report(request, target_economies, placements)
    quality = _validate_report_candidate(request, target_economies, candidate, catalogue, system, placement_lanes)
    if quality.status == 'reject':
        return _no_strong_report(
            request=request,
            system=system,
            catalogue=catalogue,
            target_economies=target_economies,
            reason='Generated plan failed the Guided Planner quality gate.',
            body_options=body_options,
            attempted_quality=quality,
        )

    body_roles = _body_roles(body_options, placements, capacities, target_economies)
    explanation = _explanation(request, target_economies, body_roles, placements, quality)
    warnings = _unique([
        *quality.warnings,
        *[warning for role in body_roles for warning in role.warnings],
    ])
    return GuidedPlanCandidateReport(
        system_id64=request.system_id64,
        preset=request.preset,
        target_economies=target_economies,
        title=_plan_title(request.preset, target_economies),
        summary=_plan_summary(request, target_economies, placements, body_roles),
        placements=placements,
        body_roles=body_roles,
        warnings=warnings,
        missing_prerequisites=quality.missing_prerequisites,
        occupied_slot_conflicts=_slot_conflicts(quality),
        unresolved_infrastructure_warnings=_unresolved_warnings(quality),
        economy_discipline=quality.economy_soup,
        quality=quality,
        explanation=explanation,
        no_strong_plan=False,
    )


def guided_plan_report_to_dict(report: GuidedPlanCandidateReport) -> dict[str, Any]:
    """Return a JSON-ready dict for tests or future prototype routes."""
    return asdict(report)


def _target_economies(request: GuidedPlanRequest, system: GuidedSystemContext) -> list[str]:
    requested = [
        _normalise_economy(item)
        for item in [request.target_economy, request.secondary_economy]
        if item
    ]
    if requested:
        return _unique([item for item in requested if item not in _normalised_set(request.avoid_economies)])

    counts: Counter[str] = Counter()
    for body in system.bodies:
        profile = profile_body(body.to_profile_row())
        counts.update(profile.base_economies)
        counts.update(profile.modifier_economies)
    avoided = _normalised_set(request.avoid_economies)
    return [economy for economy, _count in counts.most_common(2) if economy not in avoided]


def _rank_body_options(
    request: GuidedPlanRequest,
    system: GuidedSystemContext,
    target_economies: list[str],
) -> list[_BodyOption]:
    preferred = {str(item) for item in request.prefer_body_ids}
    avoided = {str(item) for item in request.avoid_body_ids}
    avoid_economies = _normalised_set(request.avoid_economies)
    options: list[_BodyOption] = []
    for body in system.bodies:
        profile = profile_body(body.to_profile_row())
        body_id = str(body.body_id)
        available_orbital = _remaining(body.predicted_orbital_slots, body.occupied_orbital_slots if request.include_existing else 0)
        available_ground = _remaining(body.predicted_ground_slots, body.occupied_ground_slots if request.include_existing else 0)
        economies = set(profile.base_economies) | set(profile.modifier_economies)
        target_matches = [economy for economy in target_economies if economy in economies]
        avoid_matches = sorted(economies & avoid_economies)
        score = profile.confidence + profile.purity
        rationale: list[str] = []
        warnings: list[str] = []
        role: BodyRoleLabel = 'reserve'

        if target_matches:
            score += len(target_matches) * 4.0
            role = 'support'
            rationale.append(f'{body.body_name} supports {" / ".join(target_matches)}.')
        if body_id in preferred:
            score += 8.0
            rationale.append(f'{body.body_name} was preferred in the request.')
        if avoid_matches:
            score -= len(avoid_matches) * 5.0
            rationale.append(f'{body.body_name} has avoided economy pressure: {", ".join(avoid_matches)}.')
            role = 'avoided'
        if body_id in avoided:
            score -= 20.0
            rationale.append(f'{body.body_name} was explicitly avoided in the request.')
            role = 'avoided'
        if body.unresolved_existing_infrastructure:
            warnings.append(f'{body.body_name} has unresolved existing infrastructure.')
        if body.inferred_station_body_association:
            warnings.append(f'{body.body_name} has inferred station/body association.')
        if not _capacity_positive(available_orbital) and not _capacity_positive(available_ground):
            warnings.append(f'{body.body_name} has no known free slots.')

        options.append(_BodyOption(
            body=body,
            profile=profile,
            score=round(score, 3),
            available_orbital=available_orbital,
            available_ground=available_ground,
            role=role,
            rationale=rationale or [f'{body.body_name} is available as reserve capacity.'],
            warnings=warnings,
        ))

    options.sort(key=lambda item: (-item.score, item.body_name, item.body_id))
    assigned_anchor = False
    for option in options:
        if option.role == 'support' and not assigned_anchor:
            option.role = 'anchor'
            assigned_anchor = True
    return options


def _support_templates(
    catalogue: dict[str, FacilityTemplate],
    target_economies: list[str],
    avoid_economies: list[str],
) -> list[FacilityTemplate]:
    avoided = _normalised_set(avoid_economies)
    target_order = {economy: index for index, economy in enumerate(target_economies)}
    supports = [
        template
        for template in catalogue.values()
        if template.is_support_facility
        and template.economy
        and _normalise_economy(template.economy) in target_order
        and _normalise_economy(template.economy) not in avoided
    ]
    supports.sort(key=lambda item: (
        target_order.get(_normalise_economy(item.economy or ''), 99),
        item.tier,
        item.name,
        item.id,
    ))
    return supports


def _select_port(catalogue: dict[str, FacilityTemplate], request: GuidedPlanRequest) -> FacilityTemplate | None:
    ports = [template for template in catalogue.values() if template.is_port and not is_colony_ship(template)]
    if not ports:
        ports = [template for template in catalogue.values() if template.is_port]
    if not ports:
        return None
    if request.preset == 'light':
        return min(ports, key=lambda item: (item.tier, item.yellow_cp_cost + item.green_cp_cost, item.name, item.id))
    if request.preset == 'maxed':
        return max(ports, key=lambda item: (item.tier, -(item.yellow_cp_cost + item.green_cp_cost), item.name, item.id))
    return min(ports, key=lambda item: (abs(item.tier - 2), item.yellow_cp_cost + item.green_cp_cost, item.name, item.id))


def _append_placement(
    *,
    placements: list[GuidedPlanPlacement],
    placement_lanes: dict[int, GuidedLane],
    template: FacilityTemplate,
    body: _BodyOption,
    capacities: dict[str, _MutableCapacity],
    is_primary_port: bool,
    role: str,
    reason: str,
) -> bool:
    lane = _choose_lane(template, body, capacities)
    if lane is None:
        return False
    capacity = capacities[body.body_id]
    if lane == 'orbital' and capacity.orbital is not None:
        capacity.orbital -= 1
    if lane == 'ground' and capacity.ground is not None:
        capacity.ground -= 1
    order = len(placements) + 1
    placements.append(GuidedPlanPlacement(
        facility_template_id=template.id,
        facility_name=template.name,
        body_id=body.body_id,
        body_name=body.body_name,
        lane=lane,
        build_order=order,
        is_primary_port=is_primary_port,
        economy=template.economy,
        role=role,
        reason=reason,
    ))
    placement_lanes[order] = lane
    return True


def _place_support(
    *,
    placements: list[GuidedPlanPlacement],
    placement_lanes: dict[int, GuidedLane],
    template: FacilityTemplate,
    bodies: list[_BodyOption],
    capacities: dict[str, _MutableCapacity],
    target_economies: list[str],
    role: str,
) -> bool:
    planned_by_body = Counter(placement.body_id for placement in placements)
    sorted_bodies = sorted(
        bodies,
        key=lambda body: (
            0 if template.economy and _normalise_economy(template.economy) in body.profile.base_economies + body.profile.modifier_economies else 1,
            planned_by_body.get(body.body_id, 0),
            0 if body.role == 'anchor' else 1,
            -_capacity_score(body.body_id, capacities),
            body.body_name,
        ),
    )
    for body in sorted_bodies:
        if body.role == 'avoided':
            continue
        if _append_placement(
            placements=placements,
            placement_lanes=placement_lanes,
            template=template,
            body=body,
            capacities=capacities,
            is_primary_port=False,
            role=role,
            reason=_structure_reason(template, body, target_economies),
        ):
            return True
    return False


def _first_missing_prerequisite_template(
    template: FacilityTemplate,
    placements: list[GuidedPlanPlacement],
    catalogue: dict[str, FacilityTemplate],
    avoid_economies: list[str],
) -> FacilityTemplate | None:
    if not template.prerequisites:
        return None
    placed_ids = {placement.facility_template_id for placement in placements}
    avoided = _normalised_set(avoid_economies)
    for item in template.prerequisites:
        description = _prerequisite_description(item)
        if not description:
            continue
        if _prerequisite_satisfied_by_ids(description, placed_ids, catalogue):
            continue
        match = _find_prerequisite_template(description, catalogue, avoided)
        if match and match.id not in placed_ids:
            return match
    return None


def _find_prerequisite_template(
    description: str,
    catalogue: dict[str, FacilityTemplate],
    avoided_economies: set[str],
) -> FacilityTemplate | None:
    tokens = _tokens(description)
    matches: list[FacilityTemplate] = []
    for template in catalogue.values():
        if not template.is_support_facility:
            continue
        if template.economy and _normalise_economy(template.economy) in avoided_economies:
            continue
        haystack = ' '.join([template.id, template.name, template.category, template.economy or '']).lower().replace('_', ' ')
        if tokens and all(token in haystack for token in tokens):
            matches.append(template)
    matches.sort(key=lambda item: (item.tier, item.name, item.id))
    return matches[0] if matches else None


def _prerequisite_satisfied_by_ids(
    description: str,
    placed_ids: set[str],
    catalogue: dict[str, FacilityTemplate],
) -> bool:
    tokens = _tokens(description)
    for template_id in placed_ids:
        template = catalogue.get(template_id)
        if not template:
            continue
        haystack = ' '.join([template.id, template.name, template.category, template.economy or '']).lower().replace('_', ' ')
        if tokens and all(token in haystack for token in tokens):
            return True
    return False


def _candidate_from_report(
    request: GuidedPlanRequest,
    target_economies: list[str],
    placements: list[GuidedPlanPlacement],
) -> OptimiserCandidate:
    target_label = ' / '.join(target_economies) if target_economies else 'selected economy'
    return OptimiserCandidate(
        candidate_id=f'guided-{request.preset}-{request.system_id64}',
        label=_plan_title(request.preset, target_economies),
        target_archetype='guided_planner_prototype',
        strategy='guided_planner_prototype',
        placements=[
            CandidatePlacement(
                facility_template_id=placement.facility_template_id,
                local_body_id=placement.body_id,
                is_primary_port=placement.is_primary_port,
                build_order=placement.build_order,
            )
            for placement in placements
        ],
        rationale=[
            f'This plan targets {target_label} with a coherent economy spine.',
            f'{request.preset.title()} preset uses {len(placements)} placement(s) before Preview review.',
        ],
        warnings=[],
        assumptions=['Prototype Guided Planner report; run Simulation Preview before committing resources.'],
        tags=['guided_planner', request.preset, *[_tag_economy(economy) for economy in target_economies]],
        preview_summary=None,
    )


def _validate_report_candidate(
    request: GuidedPlanRequest,
    target_economies: list[str],
    candidate: OptimiserCandidate,
    catalogue: dict[str, FacilityTemplate],
    system: GuidedSystemContext,
    placement_lanes: dict[int, GuidedLane],
) -> PlanQualityReport:
    return validate_generated_plan_quality(
        candidate,
        catalogue,
        options=PlanQualityOptions(
            target_economies=target_economies,
            preset=request.preset,
            requested_count=request.requested_count,
            allow_mixed_economies=request.preset == 'maxed' and request.risk_tolerance == 'high',
            require_body_assignment=True,
        ),
        body_slots=[
            PlanBodySlotState(
                body_id=str(body.body_id),
                predicted_orbital_slots=body.predicted_orbital_slots,
                predicted_ground_slots=body.predicted_ground_slots,
                occupied_orbital_slots=body.occupied_orbital_slots if request.include_existing else 0,
                occupied_ground_slots=body.occupied_ground_slots if request.include_existing else 0,
                unresolved_existing_infrastructure=body.unresolved_existing_infrastructure,
                inferred_station_body_association=body.inferred_station_body_association,
            )
            for body in system.bodies
        ],
        placement_lanes=placement_lanes,
    )


def _no_strong_report(
    *,
    request: GuidedPlanRequest,
    system: GuidedSystemContext,
    catalogue: dict[str, FacilityTemplate],
    target_economies: list[str],
    reason: str,
    body_options: list[_BodyOption] | None = None,
    attempted_quality: PlanQualityReport | None = None,
) -> GuidedPlanCandidateReport:
    empty_candidate = OptimiserCandidate(
        candidate_id=f'guided-{request.preset}-{request.system_id64}-none',
        label='No strong coherent plan found',
        target_archetype='guided_planner_prototype',
        strategy='guided_planner_prototype',
        placements=[],
        rationale=[reason],
        warnings=[reason],
        assumptions=['Do not pad Guided Planner output with unrelated structures to hit a requested count.'],
        tags=['guided_planner', request.preset],
        preview_summary=None,
    )
    quality = attempted_quality or validate_generated_plan_quality(
        empty_candidate,
        catalogue,
        options=PlanQualityOptions(
            target_economies=target_economies,
            preset=request.preset,
            requested_count=request.requested_count,
            require_body_assignment=True,
        ),
        body_slots=[
            PlanBodySlotState(
                body_id=str(body.body_id),
                predicted_orbital_slots=body.predicted_orbital_slots,
                predicted_ground_slots=body.predicted_ground_slots,
                occupied_orbital_slots=body.occupied_orbital_slots if request.include_existing else 0,
                occupied_ground_slots=body.occupied_ground_slots if request.include_existing else 0,
                unresolved_existing_infrastructure=body.unresolved_existing_infrastructure,
                inferred_station_body_association=body.inferred_station_body_association,
            )
            for body in system.bodies
        ],
    )
    roles = _body_roles(body_options or _rank_body_options(request, system, target_economies), [], {}, target_economies)
    warnings = _unique([reason, *quality.warnings, *quality.rejections])
    return GuidedPlanCandidateReport(
        system_id64=request.system_id64,
        preset=request.preset,
        target_economies=target_economies,
        title='No strong coherent plan found',
        summary=reason,
        placements=[],
        body_roles=roles,
        warnings=warnings,
        missing_prerequisites=quality.missing_prerequisites,
        occupied_slot_conflicts=_slot_conflicts(quality),
        unresolved_infrastructure_warnings=_unresolved_warnings(quality),
        economy_discipline=quality.economy_soup,
        quality=quality,
        explanation=GuidedPlanExplanation(
            why_this_body=[item for role in roles for item in role.rationale[:1]][:3],
            why_this_structure=[],
            tradeoffs=[reason, *quality.suggested_fixes],
        ),
        no_strong_plan=True,
        no_strong_plan_reason=reason,
    )


def _body_roles(
    body_options: list[_BodyOption],
    placements: list[GuidedPlanPlacement],
    capacities: dict[str, _MutableCapacity],
    target_economies: list[str],
) -> list[GuidedBodyRole]:
    planned: dict[str, Counter[str]] = defaultdict(Counter)
    used = {placement.body_id for placement in placements}
    for placement in placements:
        planned[placement.body_id][placement.lane] += 1

    roles: list[GuidedBodyRole] = []
    for option in body_options:
        if option.role == 'avoided':
            label = 'Avoided Body'
        elif option.body_id in used and option.role == 'anchor':
            label = _anchor_role_label(target_economies)
        elif option.body_id in used:
            label = _support_body_label(option, target_economies)
        else:
            label = 'Reserve Body'
        capacity = capacities.get(option.body_id)
        roles.append(GuidedBodyRole(
            body_id=option.body_id,
            body_name=option.body_name,
            role=option.role if option.body_id in used or option.role == 'avoided' else 'reserve',
            label=label,
            rationale=option.rationale,
            planned_orbital_slots=planned[option.body_id].get('orbital', 0),
            planned_ground_slots=planned[option.body_id].get('ground', 0),
            remaining_orbital_slots=capacity.orbital if capacity else option.available_orbital,
            remaining_ground_slots=capacity.ground if capacity else option.available_ground,
            warnings=option.warnings,
        ))
    return roles


def _explanation(
    request: GuidedPlanRequest,
    target_economies: list[str],
    body_roles: list[GuidedBodyRole],
    placements: list[GuidedPlanPlacement],
    quality: PlanQualityReport,
) -> GuidedPlanExplanation:
    active_roles = [role for role in body_roles if role.planned_orbital_slots or role.planned_ground_slots]
    return GuidedPlanExplanation(
        why_this_body=[
            f'{role.body_name}: {role.label}. {" ".join(role.rationale[:1])}'
            for role in active_roles
        ],
        why_this_structure=[
            f'{placement.facility_name} on {placement.body_name}: {placement.reason}'
            for placement in placements[:8]
        ],
        tradeoffs=_unique([
            f'{request.preset.title()} plan targets {" / ".join(target_economies)} and should still be previewed before use.',
            *quality.warnings,
            *quality.suggested_fixes,
        ]),
    )


def _plan_title(preset: PlanPreset, target_economies: list[str]) -> str:
    target = ' / '.join(target_economies) if target_economies else 'Guided'
    return f'{preset.title()} {target} Guided Plan'


def _plan_summary(
    request: GuidedPlanRequest,
    target_economies: list[str],
    placements: list[GuidedPlanPlacement],
    body_roles: list[GuidedBodyRole],
) -> str:
    used_body_count = sum(1 for role in body_roles if role.planned_orbital_slots or role.planned_ground_slots)
    return (
        f'{request.preset.title()} prototype report with {len(placements)} placements across '
        f'{max(1, used_body_count)} body/bodies, targeting {" / ".join(target_economies)}.'
    )


def _desired_count(request: GuidedPlanRequest) -> int:
    minimum, maximum = PRESET_COUNT_RANGES[request.preset]
    desired = request.requested_count or PRESET_DEFAULT_COUNTS[request.preset]
    if maximum is not None:
        desired = min(desired, maximum)
    return max(minimum, desired)


def _initial_capacities(body_options: list[_BodyOption]) -> dict[str, _MutableCapacity]:
    return {
        option.body_id: _MutableCapacity(option.available_orbital, option.available_ground)
        for option in body_options
    }


def _choose_lane(
    template: FacilityTemplate,
    body: _BodyOption,
    capacities: dict[str, _MutableCapacity],
) -> GuidedLane | None:
    capacity = capacities[body.body_id]
    if template.needs_ringed_body and not body.body.is_ringed:
        return None
    if template.needs_orbital:
        return 'orbital' if _capacity_positive(capacity.orbital) else None
    if template.needs_surface:
        return 'ground' if _capacity_positive(capacity.ground) else None
    orbital = _capacity_value(capacity.orbital)
    ground = _capacity_value(capacity.ground)
    if orbital <= 0 and ground <= 0:
        return None
    return 'orbital' if orbital >= ground else 'ground'


def _remaining(predicted: Optional[int], occupied: int) -> Optional[int]:
    if predicted is None:
        return None
    return max(0, int(predicted) - max(0, int(occupied)))


def _capacity_positive(value: Optional[int]) -> bool:
    return value is None or value > 0


def _capacity_value(value: Optional[int]) -> int:
    return 99 if value is None else int(value)


def _has_capacity(body: _BodyOption) -> bool:
    return _capacity_positive(body.available_orbital) or _capacity_positive(body.available_ground)


def _capacity_score(body_id: str, capacities: dict[str, _MutableCapacity]) -> int:
    capacity = capacities[body_id]
    return _capacity_value(capacity.orbital) + _capacity_value(capacity.ground)


def _all_relevant_capacity_exhausted(
    bodies: list[_BodyOption],
    capacities: dict[str, _MutableCapacity],
) -> bool:
    return all(_capacity_score(body.body_id, capacities) <= 0 for body in bodies if body.role != 'avoided')


def _matches_any_target(profile: BodyEconomyProfile, target_economies: list[str]) -> bool:
    economies = set(profile.base_economies) | set(profile.modifier_economies)
    return any(economy in economies for economy in target_economies)


def _anchor_role_label(target_economies: list[str]) -> str:
    primary = target_economies[0] if target_economies else 'Colony'
    return f'Main Station / {primary} Core'


def _support_body_label(option: _BodyOption, target_economies: list[str]) -> str:
    for economy in target_economies:
        if economy in option.profile.base_economies or economy in option.profile.modifier_economies:
            return f'{economy} Support'
    return 'Support Body'


def _support_role(template: FacilityTemplate, target_economies: list[str]) -> str:
    economy = _normalise_economy(template.economy or '')
    if economy in target_economies:
        return f'{economy} Support'
    return 'Support Structure'


def _structure_reason(
    template: FacilityTemplate,
    body: _BodyOption,
    target_economies: list[str],
) -> str:
    economy = _normalise_economy(template.economy or '')
    if economy in target_economies:
        return f'{template.name} reinforces the {economy} spine on {body.body_name}.'
    return f'{template.name} is included as prerequisite/support for the target spine.'


def _slot_conflicts(quality: PlanQualityReport) -> list[str]:
    return [
        item for item in quality.rejections
        if 'slot' in item.lower() or 'capacity' in item.lower() or 'occupied' in item.lower()
    ]


def _unresolved_warnings(quality: PlanQualityReport) -> list[str]:
    return [
        item for item in quality.warnings
        if 'unresolved' in item.lower() or 'inferred station/body' in item.lower()
    ]


def _prerequisite_description(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get('description') or item.get('facility') or item.get('prerequisite') or '').strip()
    return ''


def _tokens(value: str) -> list[str]:
    stop = {'a', 'an', 'and', 'at', 'body', 'facility', 'installation', 'or', 'port', 'requires', 'settlement', 'the'}
    return _unique([
        token for token in value.lower().replace('_', ' ').replace('-', ' ').split()
        if token and token not in stop and len(token) > 2
    ])


def _normalise_economy(value: str) -> str:
    text = str(value).strip()
    if text.lower().replace(' ', '') in {'hightech', 'hitech'}:
        return 'HighTech'
    return text[:1].upper() + text[1:] if text else text


def _normalised_set(values: list[str]) -> set[str]:
    return {_normalise_economy(value) for value in values if value}


def _tag_economy(value: str) -> str:
    return value.lower().replace(' ', '-')


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
