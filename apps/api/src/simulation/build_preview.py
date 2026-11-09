"""
Deterministic Simulation Preview for a user-authored colony build plan.

This module deliberately does not optimise. The caller supplies placements;
we simulate the consequences, score the composition, and explain the result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from edfinder_api.domain.facilities import FacilityTemplate
from edfinder_api.mechanics.confidence import (
    ConfidenceLevel,
    ConfidenceSignal,
    simulation_confidence_signals,
)
from edfinder_api.mechanics.economy_rules import (
    BODY_PROFILE_CONFIDENCE_DEFAULT,
    CONTAMINATION_ECONOMIES,
    FACILITY_ECONOMY_WEIGHTS,
    LOW_PURITY_THRESHOLD,
    MODIFIER_ECONOMY_WEIGHT,
    PRIMARY_BASE_WEIGHT,
    PROFILE_FULL_CONFIDENCE,
    PROFILE_FULL_PURITY,
    SECONDARY_BASE_WEIGHT,
)
from edfinder_api.mechanics.scoring_rules import (
    BUILDABILITY_ESTIMATED_TOPOLOGY_SCORE,
    BUILDABILITY_LOW_SLOT_CONFIDENCE_PENALTY,
    BUILDABILITY_LOW_SLOT_CONFIDENCE_THRESHOLD,
    BUILDABILITY_NEGATIVE_CP_PENALTY,
    BUILDABILITY_OVER_SLOT_BASE_SCORE,
    BUILDABILITY_OVER_SLOT_MIN_SCORE,
    BUILDABILITY_OVER_SLOT_PENALTY,
    BUILDABILITY_SLOT_FIT_SCORE,
    COMPLEXITY_LOW_CONFIDENCE_THRESHOLD,
    CONFIDENCE_LOW_THRESHOLD,
    ESTIMATED_FACILITY_CONFIDENCE_MAX_PENALTY,
    ESTIMATED_FACILITY_CONFIDENCE_PENALTY,
    LOW_PURITY_CONFIDENCE_MAX_PENALTY,
    LOW_PURITY_CONFIDENCE_SCALE,
    MISSING_BODY_PROFILE_CONFIDENCE_MAX_PENALTY,
    MISSING_BODY_PROFILE_CONFIDENCE_PENALTY,
    MISSING_SLOT_CONFIDENCE_PENALTY,
    MIXED_BASE_CONFIDENCE_MAX_PENALTY,
    MIXED_BASE_CONFIDENCE_PENALTY,
    MODIFIER_CONFIDENCE_MAX_PENALTY,
    MODIFIER_CONFIDENCE_PENALTY,
    SIMULATION_CONFIDENCE_BASE,
    SIMULATION_CONFIDENCE_MAX,
    SIMULATION_CONFIDENCE_MIN,
    SIMULATION_FINAL_SCORE_WEIGHTS,
    SLOT_CONFIDENCE_BASE,
    SLOT_CONFIDENCE_WEIGHT,
)
from edfinder_api.observations.comparison import compare_prediction_to_observations
from edfinder_api.simulation.build_order import simulate_build_order
from edfinder_api.simulation.cp_repair import build_cp_repair_suggestions
from edfinder_api.simulation.economy_stack import analyse_economy_stack
from edfinder_api.simulation.port_economy import (
    aggregate_port_strengths,
    build_port_economy_states,
)
from edfinder_api.simulation.service_graph import build_port_service_states
from edfinder_api.simulation.preview_pipeline import (
    EconomySimulationState,
    ObservationComparisonState,
    PlacementResolutionState,
    ServiceSimulationState,
    SimulationPrediction,
)
from edfinder_api.simulation.preview_response import (
    assemble_preview_response,
    links_to_response,
    observation_prediction_snapshot,
)
from edfinder_api.simulation.services import model_services
from edfinder_api.simulation.topology_graph import (
    GraphPlacement,
    build_topology_graph,
    infer_location_type,
)
from edfinder_api.simulation.preview_models import (
    EconomyContributionProfile,
    PreviewContext,
    PreviewPlacement,
)


_CONTAMINATION_ECONOMIES = CONTAMINATION_ECONOMIES


@dataclass
class _ResolvedPlacement:
    spec: PreviewPlacement
    facility: FacilityTemplate
    placement_instance_id: str
    economy_profile: Optional[EconomyContributionProfile] = None
    confidence_note: Optional[str] = None


def simulate_build_preview(
    *,
    system_id64: int,
    target_archetype: str,
    placements: list[PreviewPlacement],
    catalogue: dict[str, FacilityTemplate],
    context: Optional[PreviewContext] = None,
) -> dict[str, Any]:
    """Simulate a proposed build and return a Pydantic-ready response dict."""
    context = context or PreviewContext(system_id64=system_id64)
    resolution = resolve_preview_placements(
        placements=placements,
        catalogue=catalogue,
        context=context,
    )
    prediction = build_core_prediction(
        system_id64=system_id64,
        target_archetype=target_archetype,
        context=context,
        resolution=resolution,
    )
    observation = build_observation_comparison(prediction)
    return assemble_preview_response(prediction, observation)


def resolve_preview_placements(
    *,
    placements: list[PreviewPlacement],
    catalogue: dict[str, FacilityTemplate],
    context: PreviewContext,
) -> PlacementResolutionState:
    warnings: list[str] = []
    mechanics_notes: list[str] = [
        'This is a deterministic preview of your selected build, not an automatic optimiser.',
        'T2 and T3 port CP costs escalate with each additional paid port of the same tier.',
    ]
    mechanics_notes.extend(context.mechanics_notes)

    ordered_specs = sorted(placements, key=lambda p: (p.build_order, p.facility_template_id))
    resolved: list[_ResolvedPlacement] = []
    for index, spec in enumerate(ordered_specs, start=1):
        facility = catalogue.get(spec.facility_template_id)
        if facility is None:
            warnings.append(
                f'Facility template {spec.facility_template_id!r} is not in the loaded catalogue; it was skipped.'
            )
            continue
        economy_profile, note = _placement_economy_profile(facility, spec.local_body_id, context)
        if note:
            warnings.extend(note)
        resolved.append(_ResolvedPlacement(
            spec=spec,
            facility=facility,
            placement_instance_id=_placement_instance_id(index, spec),
            economy_profile=economy_profile,
            confidence_note=' '.join(note) if note else None,
        ))

    if not resolved:
        warnings.append('No valid facilities are selected yet. Add at least one facility to preview a build.')

    return PlacementResolutionState(
        resolved_placements=resolved,
        warnings=warnings,
        mechanics_notes=mechanics_notes,
    )


def build_core_prediction(
    *,
    system_id64: int,
    target_archetype: str,
    context: PreviewContext,
    resolution: PlacementResolutionState,
) -> SimulationPrediction:
    resolved = resolution.resolved_placements
    warnings = list(resolution.warnings)
    strengths: list[str] = []
    recommendations: list[str] = []
    mechanics_notes = list(resolution.mechanics_notes)

    cp = simulate_build_order(resolved).to_cp_dict()
    cp_repair_suggestions = build_cp_repair_suggestions(placements=resolved, cp=cp)
    warnings.extend(cp['warnings'])

    topology_graph = build_topology_graph(_graph_placements(resolved, context))
    warnings.extend(topology_graph.warnings)
    _extend_topology_notes(topology_graph, warnings, mechanics_notes)

    economy = build_economy_prediction(
        resolved=resolved,
        topology_graph=topology_graph,
        target_archetype=target_archetype,
    )
    services = build_service_prediction(
        resolved=resolved,
        topology_graph=topology_graph,
    )

    warnings.extend(economy.composition['warnings'])
    strengths.extend(economy.composition['strengths'])
    recommendations.extend(economy.composition['recommendations'])

    buildability = _score_buildability(resolved, context, cp)
    warnings.extend(buildability['warnings'])
    recommendations.extend(buildability['recommendations'])

    profile_notes = _profile_mechanics_notes(economy.inherited_profiles)
    warnings.extend(_profile_warnings(economy.inherited_profiles, target_archetype))
    mechanics_notes.extend(profile_notes)

    confidence = _confidence(resolved, context, warnings)
    confidence_signals = simulation_confidence_signals(
        slot_confidence=context.slot_confidence,
        has_slot_estimates=context.estimated_orbital_slots is not None and context.estimated_ground_slots is not None,
        estimated_facility_count=sum(1 for p in resolved if p.facility.data_confidence == 'estimated'),
        inherited_profiles=economy.inherited_profiles,
        services=services.services,
        warnings=warnings,
    )
    mechanics_notes.append('Observation comparison is informational and does not yet alter mechanics scoring.')

    build_complexity = _complexity(cp, buildability, economy.composition, confidence)
    final_score = round(
        economy.composition['score'] * SIMULATION_FINAL_SCORE_WEIGHTS['composition']
        + buildability['score'] * SIMULATION_FINAL_SCORE_WEIGHTS['buildability']
        + cp['health_score'] * SIMULATION_FINAL_SCORE_WEIGHTS['cp_health'],
        1,
    )

    if cp['yellow_cp_final'] >= 0 and cp['green_cp_final'] >= 0:
        strengths.append('The proposed order ends with a non-negative CP balance.')
    if economy.links['strong_links']:
        strengths.append('Strong same-body support links are present in the proposed plan.')
    if confidence < CONFIDENCE_LOW_THRESHOLD:
        recommendations.append('Confirm slot and body data before committing resources in-game.')
    if cp['warnings']:
        recommendations.append('Move CP-generating support facilities earlier, or mark the intended first port as primary.')

    return SimulationPrediction(
        system_id64=system_id64,
        target_archetype=target_archetype,
        context=context,
        resolved_placements=resolved,
        cp=cp,
        cp_repair_suggestions=cp_repair_suggestions,
        topology_graph=topology_graph,
        economy=economy,
        services=services,
        buildability=buildability,
        confidence=confidence,
        confidence_signals=confidence_signals,
        build_complexity=build_complexity,
        final_score=final_score,
        warnings=warnings,
        strengths=strengths,
        recommendations=recommendations,
        mechanics_notes=mechanics_notes,
    )


def build_economy_prediction(
    *,
    resolved: list[_ResolvedPlacement],
    topology_graph: Any,
    target_archetype: str,
) -> EconomySimulationState:
    port_economy_states, influence_ledger = build_port_economy_states(
        placements=resolved,
        topology_graph=topology_graph,
    )
    economy_strengths = aggregate_port_strengths(port_economy_states)
    links = links_to_response(topology_graph)
    economy_composition = _to_percentages(economy_strengths)
    economy_order = sorted(economy_composition, key=economy_composition.get, reverse=True)
    inherited_profiles = _inherited_profiles(resolved)
    stack_result = analyse_economy_stack(economy_composition, target_archetype, inherited_profiles)
    composition = {
        'score': stack_result.score,
        'alignment': stack_result.alignment,
        'contamination_risk': stack_result.contamination_risk,
        'warnings': stack_result.warnings,
        'strengths': stack_result.strengths,
        'recommendations': stack_result.recommendations,
    }
    return EconomySimulationState(
        links=links,
        economy_composition=economy_composition,
        economy_order=economy_order,
        economy_stack=stack_result,
        composition=composition,
        port_economy_states=port_economy_states,
        influence_ledger=influence_ledger,
        inherited_profiles=inherited_profiles,
    )


def build_service_prediction(
    *,
    resolved: list[_ResolvedPlacement],
    topology_graph: Any,
) -> ServiceSimulationState:
    services = model_services(resolved, topology_graph)
    port_service_states, service_unlock_ledger = build_port_service_states(
        placements=resolved,
        topology_graph=topology_graph,
    )
    return ServiceSimulationState(
        services=services,
        port_service_states=port_service_states,
        service_unlock_ledger=service_unlock_ledger,
    )


def build_observation_comparison(prediction: SimulationPrediction) -> ObservationComparisonState:
    observation_summary, observation_diffs = compare_prediction_to_observations(
        prediction=observation_prediction_snapshot(prediction),
        observed_facts=prediction.context.observed_facts,
    )
    confidence_signal = ConfidenceSignal(
        area='observations',
        level=ConfidenceLevel.OBSERVED if observation_summary.observed_facts_count else ConfidenceLevel.UNKNOWN,
        reason='Observed mismatch found.' if observation_summary.mismatch_count else (
            'Observed data attached; comparison is informational.'
            if observation_summary.observed_facts_count
            else 'No observed data attached.'
        ),
    )
    return ObservationComparisonState(
        summary=observation_summary,
        diffs=observation_diffs,
        confidence_signal=confidence_signal,
    )


def _placement_instance_id(index: int, spec: PreviewPlacement) -> str:
    body = spec.local_body_id if spec.local_body_id is not None else 'none'
    return f'{spec.build_order}:{index}:{spec.facility_template_id}:{body}'


def _placement_economy_profile(
    facility: FacilityTemplate,
    local_body_id: Optional[str],
    context: PreviewContext,
) -> tuple[Optional[EconomyContributionProfile], list[str]]:
    if facility.is_colony_port and local_body_id:
        profile = context.local_body_profiles.get(str(local_body_id), {})
        contribution = _body_profile_contribution(str(local_body_id), profile)
        notes = list(contribution.caveats) if contribution else [str(caveat) for caveat in profile.get('caveats') or []]
        if contribution and contribution.weights:
            if contribution.is_mixed:
                notes.append(
                    f'{facility.name} inherits a mixed body economy from local body {local_body_id}; '
                    'all documented base/modifier economies are represented.'
                )
            return contribution, notes
        if facility.economy and facility.economy not in _CONTAMINATION_ECONOMIES:
            return _explicit_economy_profile(facility.economy), notes
        return None, notes + [
            f'{facility.name} has no explicit economy and local body {local_body_id} has no body economy profile; '
            'economy confidence is reduced.'
        ]

    if facility.economy:
        return _explicit_economy_profile(facility.economy), []

    if facility.is_port:
        return None, [
            f'{facility.name} has no explicit economy. It will behave as an economy anchor only after support '
            'facilities are added around it.'
        ]

    return None, []


def _simulate_economies(placements: list[_ResolvedPlacement], topology_graph: Any) -> dict[str, float]:
    strengths: dict[str, float] = {}

    for placement in placements:
        facility = placement.facility
        profile = placement.economy_profile
        for economy_name, share in (profile.weights if profile else {}).items():
            strengths[economy_name] = strengths.get(economy_name, 0.0) + _economy_weight(facility) * share

    for link in topology_graph.strong_links:
        if link.economy:
            strengths[link.economy] = strengths.get(link.economy, 0.0) + float(link.value)
    for link in topology_graph.weak_links:
        if link.economy:
            strengths[link.economy] = strengths.get(link.economy, 0.0) + float(link.value)

    strong_sources = {(link.source_facility_id, link.local_body_id) for link in topology_graph.strong_links}
    for link in topology_graph.pass_through_links:
        if not link.economy:
            continue
        if (link.source_facility_id, link.local_body_id) in strong_sources:
            continue
        strengths[link.economy] = strengths.get(link.economy, 0.0) + float(link.value)

    return strengths


def _graph_placements(
    placements: list[_ResolvedPlacement],
    context: PreviewContext,
) -> list[GraphPlacement]:
    graph_items: list[GraphPlacement] = []
    for placement in placements:
        body_id = str(placement.spec.local_body_id) if placement.spec.local_body_id is not None else None
        profile = context.local_body_profiles.get(body_id or '', {})
        graph_items.append(GraphPlacement(
            facility=placement.facility,
            local_body_id=body_id,
            build_order=placement.spec.build_order,
            location_type=infer_location_type(placement.facility),
            placement_instance_id=placement.placement_instance_id,
            economy=_topology_emitter_economy(placement),
            body_name=str(profile.get('body_name')) if profile.get('body_name') else None,
            parent_body_id=str(profile.get('parent_body_id')) if profile.get('parent_body_id') else None,
            is_primary_port=placement.spec.is_primary_port,
            body_profile=profile,
        ))
    return graph_items


def _extend_topology_notes(graph: Any, warnings: list[str], mechanics_notes: list[str]) -> None:
    for port in graph.converted_ports:
        warnings.append(f'{port.facility_name} is a converted port and may behave as a support emitter: {port.reason}')
    for link in [*graph.strong_links, *graph.pass_through_links]:
        for reason in getattr(link, 'modifier_reasons', []) or []:
            mechanics_notes.append(reason)
        for caveat in getattr(link, 'caveats', []) or []:
            warnings.append(caveat)
        for assumption in getattr(link, 'assumptions', []) or []:
            mechanics_notes.append(assumption)


def _score_buildability(
    placements: list[_ResolvedPlacement],
    context: PreviewContext,
    cp: dict[str, Any],
) -> dict[str, Any]:
    warnings: list[str] = []
    recommendations: list[str] = []
    orbital_slots = context.estimated_orbital_slots
    ground_slots = context.estimated_ground_slots

    orbital_used = sum(1 for p in placements if _placement_location(p.facility) == 'orbital')
    ground_used = sum(1 for p in placements if _placement_location(p.facility) == 'surface')

    if orbital_slots is None or ground_slots is None:
        warnings.append('Topology data is incomplete, so slot fit is estimated from the proposed placements only.')
        score = BUILDABILITY_ESTIMATED_TOPOLOGY_SCORE
    else:
        orbital_over = max(0, orbital_used - orbital_slots)
        ground_over = max(0, ground_used - ground_slots)
        total_over = orbital_over + ground_over
        if total_over:
            warnings.append(
                f'The plan uses {orbital_used} orbital / {ground_used} surface slots, beyond the estimated '
                f'{orbital_slots} orbital / {ground_slots} surface capacity.'
            )
            score = max(BUILDABILITY_OVER_SLOT_MIN_SCORE, BUILDABILITY_OVER_SLOT_BASE_SCORE - total_over * BUILDABILITY_OVER_SLOT_PENALTY)
            recommendations.append('Reduce facilities or choose bodies with better slot availability.')
        else:
            score = BUILDABILITY_SLOT_FIT_SCORE

    if context.slot_confidence is not None and context.slot_confidence < BUILDABILITY_LOW_SLOT_CONFIDENCE_THRESHOLD:
        warnings.append('Slot data is predicted rather than observed, so confidence is reduced.')
        score -= BUILDABILITY_LOW_SLOT_CONFIDENCE_PENALTY

    if cp['yellow_cp_final'] < 0 or cp['green_cp_final'] < 0:
        score -= BUILDABILITY_NEGATIVE_CP_PENALTY

    return {
        'score': max(0.0, min(100.0, score)),
        'warnings': warnings,
        'recommendations': recommendations,
    }


def _confidence(
    placements: list[_ResolvedPlacement],
    context: PreviewContext,
    warnings: list[str],
) -> float:
    value = SIMULATION_CONFIDENCE_BASE
    if context.estimated_orbital_slots is None or context.estimated_ground_slots is None:
        value -= MISSING_SLOT_CONFIDENCE_PENALTY
    if context.slot_confidence is not None:
        value = min(value, SLOT_CONFIDENCE_BASE + context.slot_confidence * SLOT_CONFIDENCE_WEIGHT)
    uncertain = sum(1 for p in placements if p.facility.data_confidence == 'estimated')
    value -= min(ESTIMATED_FACILITY_CONFIDENCE_MAX_PENALTY, uncertain * ESTIMATED_FACILITY_CONFIDENCE_PENALTY)
    value -= min(
        MISSING_BODY_PROFILE_CONFIDENCE_MAX_PENALTY,
        len([w for w in warnings if 'no body economy profile' in w]) * MISSING_BODY_PROFILE_CONFIDENCE_PENALTY,
    )
    inherited = _inherited_profiles(placements)
    if inherited:
        value = min(value, min(profile.confidence for profile in inherited))
        value -= min(
            LOW_PURITY_CONFIDENCE_MAX_PENALTY,
            sum(max(0.0, LOW_PURITY_THRESHOLD - profile.purity) for profile in inherited) * LOW_PURITY_CONFIDENCE_SCALE,
        )
        value -= min(
            MIXED_BASE_CONFIDENCE_MAX_PENALTY,
            sum(max(0, len(profile.base_economies) - 1) for profile in inherited) * MIXED_BASE_CONFIDENCE_PENALTY,
        )
        value -= min(
            MODIFIER_CONFIDENCE_MAX_PENALTY,
            sum(len(profile.modifier_economies) for profile in inherited) * MODIFIER_CONFIDENCE_PENALTY,
        )
    return max(SIMULATION_CONFIDENCE_MIN, min(SIMULATION_CONFIDENCE_MAX, value))


def _complexity(
    cp: dict[str, Any],
    buildability: dict[str, Any],
    composition: dict[str, Any],
    confidence: float,
) -> str:
    pressure = 0
    if cp['warnings']:
        pressure += 2
    if cp['t3_ports'] > 0:
        pressure += 2
    if cp['t2_ports'] > 2:
        pressure += 1
    if buildability['warnings']:
        pressure += 1
    if composition['alignment'] in {'partial', 'poor'}:
        pressure += 1
    if confidence < COMPLEXITY_LOW_CONFIDENCE_THRESHOLD:
        pressure += 1
    if pressure >= 5:
        return 'expert'
    if pressure >= 3:
        return 'advanced'
    if pressure >= 1:
        return 'moderate'
    return 'simple'


def _to_percentages(strengths: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, v) for v in strengths.values())
    if total <= 0:
        return {}
    ranked = sorted(strengths.items(), key=lambda item: item[1], reverse=True)
    return {economy: round((value / total) * 100.0, 1) for economy, value in ranked}


def _economy_weight(facility: FacilityTemplate) -> float:
    if facility.is_port and not facility.is_colony_port:
        return FACILITY_ECONOMY_WEIGHTS['specialised_port']
    if facility.is_colony_port:
        return FACILITY_ECONOMY_WEIGHTS['colony_port']
    return FACILITY_ECONOMY_WEIGHTS['support_or_default']


def _explicit_economy_profile(economy: str) -> EconomyContributionProfile:
    return EconomyContributionProfile(
        base_economies=[economy],
        weights={economy: FACILITY_ECONOMY_WEIGHTS['support_or_default']},
        purity=PROFILE_FULL_PURITY,
        confidence=PROFILE_FULL_CONFIDENCE,
    )


def _body_profile_contribution(body_id: str, profile: dict[str, Any]) -> Optional[EconomyContributionProfile]:
    weights = _body_profile_economy_weights(profile)
    if not weights:
        return None
    return EconomyContributionProfile(
        base_economies=[str(item) for item in profile.get('base_economies') or []],
        modifier_economies=[str(item) for item in profile.get('modifier_economies') or []],
        weights=weights,
        purity=float(profile.get('purity') if profile.get('purity') is not None else PROFILE_FULL_PURITY),
        confidence=float(profile.get('confidence') if profile.get('confidence') is not None else BODY_PROFILE_CONFIDENCE_DEFAULT),
        caveats=[str(item) for item in profile.get('caveats') or []],
        strategic_tags=[str(item) for item in profile.get('strategic_tags') or []],
        source_body_id=body_id,
        source_body_name=str(profile.get('body_name')) if profile.get('body_name') else None,
        inherited=True,
    )


def _body_profile_economy_weights(profile: dict[str, Any]) -> dict[str, float]:
    base = [str(item) for item in profile.get('base_economies') or []]
    if not base:
        single = profile.get('base_economy') or profile.get('economy')
        base = [str(single)] if single else []
    modifiers = [str(item) for item in profile.get('modifier_economies') or [] if item not in base]

    raw: dict[str, float] = {}
    for index, economy in enumerate(base):
        raw[economy] = raw.get(economy, 0.0) + (PRIMARY_BASE_WEIGHT if index == 0 else SECONDARY_BASE_WEIGHT)
    for economy in modifiers:
        raw[economy] = raw.get(economy, 0.0) + MODIFIER_ECONOMY_WEIGHT

    total = sum(raw.values())
    if total <= 0:
        return {}
    return {economy: value / total for economy, value in raw.items()}


def _support_link_economy(placement: _ResolvedPlacement) -> Optional[str]:
    if not placement.facility.is_support_facility:
        return None
    if placement.facility.economy:
        return placement.facility.economy
    profile = placement.economy_profile
    if profile and len(profile.weights) == 1:
        return next(iter(profile.weights))
    return None


def _topology_emitter_economy(placement: _ResolvedPlacement) -> Optional[str]:
    support = _support_link_economy(placement)
    if support:
        return support
    if not placement.facility.is_port:
        return None
    if placement.facility.economy and placement.facility.economy not in _CONTAMINATION_ECONOMIES:
        return placement.facility.economy
    profile = placement.economy_profile
    if profile and profile.weights:
        return max(profile.weights, key=profile.weights.get)
    return None


def _inherited_profiles(placements: list[_ResolvedPlacement]) -> list[EconomyContributionProfile]:
    return [
        placement.economy_profile
        for placement in placements
        if placement.economy_profile and placement.economy_profile.inherited
    ]


def _profile_mechanics_notes(profiles: list[EconomyContributionProfile]) -> list[str]:
    notes: list[str] = []
    for profile in profiles:
        label = profile.source_body_name or f'Body {profile.source_body_id}'
        if profile.base_economies:
            notes.append(f"{label} inherited base economies: {', '.join(profile.base_economies)}.")
        if profile.modifier_economies:
            notes.append(f"{label} modifier economy pressure: {', '.join(profile.modifier_economies)}.")
        notes.extend(profile.caveats)
    return notes


def _profile_warnings(profiles: list[EconomyContributionProfile], target_archetype: str) -> list[str]:
    warnings: list[str] = []
    for profile in profiles:
        label = profile.source_body_name or f'Body {profile.source_body_id}'
        if profile.purity < LOW_PURITY_THRESHOLD:
            warnings.append(f'{label} is a low-purity mixed economy body; specialised builds may be less stable.')
        if profile.modifier_economies:
            warnings.append(f'{label} adds modifier economy pressure from {", ".join(profile.modifier_economies)}.')
        if target_archetype == 'refinery_industrial' and 'elw_mixed' in profile.strategic_tags:
            warnings.append('ELW inheritance is broad-spectrum and may weaken a specialised Refinery / Industrial build.')
    return warnings


def _placement_location(facility: FacilityTemplate) -> str:
    if facility.allowed_location == 'surface':
        return 'surface'
    return 'orbital'
