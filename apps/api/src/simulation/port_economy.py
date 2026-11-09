"""Per-port economy propagation and influence ledger for Simulation Preview.

This module is intentionally additive. It consumes the existing topology graph,
resolved placements, and body inheritance profiles, then builds a transparent
ledger explaining how each Main Port receives economy pressure.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from edfinder_api.mechanics.economy_rules import FACILITY_ECONOMY_WEIGHTS


INFLUENCE_DIRECT_FACILITY = 'direct_facility'
INFLUENCE_BODY_INHERITANCE = 'body_inheritance'
INFLUENCE_STRONG_LINK = 'strong_link'
INFLUENCE_WEAK_LINK = 'weak_link'
INFLUENCE_PASS_THROUGH = 'pass_through'
INFLUENCE_CONVERTED_PORT = 'converted_port'
INFLUENCE_MODIFIER = 'modifier'
INFLUENCE_REGIONAL_CONTEXT = 'regional_context'

_CONFIDENCE_INFERRED = 'inferred'
_CONFIDENCE_COMMUNITY_OBSERVED = 'community_observed'
_CONFIDENCE_ESTIMATED = 'estimated'
_BODY_INHERITANCE_LOW_CONFIDENCE_CAVEAT = 'Body inheritance confidence is below the verified threshold.'
_TERTIARY_THRESHOLD = 15.0


@dataclass(frozen=True)
class EconomyInfluence:
    source_id: str
    source_name: str
    source_type: str
    target_port_id: str
    target_port_name: str
    local_body_id: str | None
    economy: str
    value: float
    influence_type: str
    link_type: str | None
    confidence: str
    reason: str
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data['value'] = round(float(self.value), 3)
        return data


@dataclass
class PortEconomyState:
    port_id: str
    port_name: str
    local_body_id: str | None
    body_name: str | None
    location_type: str
    effective_role: str
    inherited_economies: dict[str, float] = field(default_factory=dict)
    direct_economies: dict[str, float] = field(default_factory=dict)
    strong_link_economies: dict[str, float] = field(default_factory=dict)
    weak_link_economies: dict[str, float] = field(default_factory=dict)
    pass_through_economies: dict[str, float] = field(default_factory=dict)
    converted_port_economies: dict[str, float] = field(default_factory=dict)
    final_economy_strengths: dict[str, float] = field(default_factory=dict)
    final_economy_composition: dict[str, float] = field(default_factory=dict)
    economy_order: list[str] = field(default_factory=list)
    top_two: list[str] = field(default_factory=list)
    tertiary_economies: list[str] = field(default_factory=list)
    purity_score: float = 0.0
    contamination_risk: str = 'low'
    contamination_sources: list[EconomyInfluence] = field(default_factory=list)
    influences: list[EconomyInfluence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data['inherited_economies'] = _round_map(self.inherited_economies)
        data['direct_economies'] = _round_map(self.direct_economies)
        data['strong_link_economies'] = _round_map(self.strong_link_economies)
        data['weak_link_economies'] = _round_map(self.weak_link_economies)
        data['pass_through_economies'] = _round_map(self.pass_through_economies)
        data['converted_port_economies'] = _round_map(self.converted_port_economies)
        data['final_economy_strengths'] = _round_map(self.final_economy_strengths)
        data['final_economy_composition'] = _round_map(self.final_economy_composition, ndigits=1)
        data['purity_score'] = round(float(self.purity_score), 1)
        data['contamination_sources'] = [item.to_dict() for item in self.contamination_sources]
        data['influences'] = [item.to_dict() for item in self.influences]
        return data


def build_port_economy_states(
    *,
    placements: list[Any],
    topology_graph: Any,
) -> tuple[list[PortEconomyState], list[EconomyInfluence]]:
    """Build per-Main-Port economy states and a flat influence ledger."""
    role_by_key = {
        _placement_key(item.placement): item.effective_role
        for item in getattr(topology_graph, 'classified_placements', [])
    }
    resolved_by_key = {_resolved_key(item): item for item in placements}
    states: dict[str, PortEconomyState] = {}

    for group in getattr(topology_graph, 'local_body_groups', []):
        for port, role in ((getattr(group, 'main_surface_port', None), 'main_surface_port'), (getattr(group, 'main_orbital_port', None), 'main_orbital_port')):
            if port is None:
                continue
            state = PortEconomyState(
                port_id=port.facility_id,
                port_name=port.facility_name,
                local_body_id=group.local_body_id,
                body_name=group.body_name,
                location_type=port.location_type,
                effective_role=role_by_key.get(_graph_key(port), role),
            )
            states[_port_key(port)] = state

    if not states:
        return [], []

    for group in getattr(topology_graph, 'local_body_groups', []):
        for graph_placement in group.facilities:
            resolved = resolved_by_key.get(_graph_key(graph_placement))
            if resolved is None:
                continue
            profile = getattr(resolved, 'economy_profile', None)
            if profile is None or not getattr(profile, 'weights', None):
                continue
            # Direct influence is reserved for Main Ports: colony ports may inherit
            # body economies and specialised Main Ports may carry their own economy.
            # Support facilities deliberately contribute through strong, weak,
            # pass-through, or converted-port links so their economy is not counted
            # once as direct pressure and again as linked pressure.
            if not _is_direct_port_influence_source(graph_placement, group):
                continue
            target = graph_placement
            if target is None:
                continue
            state = states.get(_port_key(target))
            if state is None:
                continue
            source_type = 'port' if graph_placement.facility.is_port else 'facility'
            base_weight = _economy_weight(graph_placement.facility)
            inherited = bool(getattr(profile, 'inherited', False) and graph_placement.facility.is_port)
            for economy, share in profile.weights.items():
                value = float(base_weight) * float(share)
                if value <= 0:
                    continue
                influence_type = INFLUENCE_BODY_INHERITANCE if inherited else INFLUENCE_DIRECT_FACILITY
                caveats = list(getattr(profile, 'caveats', []) or [])
                confidence = _profile_confidence(profile) if inherited else _CONFIDENCE_COMMUNITY_OBSERVED
                if inherited and confidence == _CONFIDENCE_ESTIMATED:
                    caveats.append(_BODY_INHERITANCE_LOW_CONFIDENCE_CAVEAT)
                reason = (
                    f'{graph_placement.facility_name} inherits {economy} from local body economy profile.'
                    if inherited else
                    f'{graph_placement.facility_name} directly contributes {economy} to the local Main Port economy stack.'
                )
                influence = EconomyInfluence(
                    source_id=graph_placement.facility_id,
                    source_name=graph_placement.facility_name,
                    source_type=source_type,
                    target_port_id=target.facility_id,
                    target_port_name=target.facility_name,
                    local_body_id=group.local_body_id,
                    economy=str(economy),
                    value=value,
                    influence_type=influence_type,
                    link_type=None,
                    confidence=confidence,
                    reason=reason,
                    caveats=caveats,
                )
                _add_influence(state, influence)
                _add_value(state.inherited_economies if inherited else state.direct_economies, str(economy), value)

    for link in getattr(topology_graph, 'strong_links', []):
        state = states.get(_port_key_from_id(link.receiver_port_id, getattr(link, 'local_body_id', None)))
        if state is None or not link.economy:
            continue
        source_type = 'converted_port' if _is_converted(topology_graph, link.source_facility_id, link.local_body_id) else 'facility'
        caveats = list(getattr(link, 'caveats', []) or [])
        if source_type == 'converted_port':
            caveats.append('Converted-port support behaviour is inferred and should be verified in-game.')
        influence_type = INFLUENCE_CONVERTED_PORT if source_type == 'converted_port' else INFLUENCE_STRONG_LINK
        influence = EconomyInfluence(
            source_id=link.source_facility_id,
            source_name=link.source_facility_name,
            source_type=source_type,
            target_port_id=link.receiver_port_id,
            target_port_name=link.receiver_port_name,
            local_body_id=link.local_body_id,
            economy=str(link.economy),
            value=float(link.value),
            influence_type=influence_type,
            link_type='strong',
            confidence=_CONFIDENCE_INFERRED,
            reason=getattr(link, 'note', '') or f'{link.source_facility_name} strongly links to {link.receiver_port_name}.',
            caveats=_unique(caveats),
        )
        _add_influence(state, influence)
        _add_value(state.converted_port_economies if source_type == 'converted_port' else state.strong_link_economies, str(link.economy), float(link.value))

    for link in getattr(topology_graph, 'weak_links', []):
        state = states.get(_port_key_from_id(link.receiver_port_id, getattr(link, 'target_body_id', None)))
        if state is None or not link.economy:
            continue
        source_type = 'converted_port' if _is_converted(topology_graph, link.source_facility_id, link.source_body_id) else 'facility'
        caveats = ['Weak links are fixed at 0.05 and only target non-local Main Ports.']
        if source_type == 'converted_port':
            caveats.append('Converted-port weak-link emission is inferred and should be verified in-game.')
        influence_type = INFLUENCE_CONVERTED_PORT if source_type == 'converted_port' else INFLUENCE_WEAK_LINK
        influence = EconomyInfluence(
            source_id=link.source_facility_id,
            source_name=link.source_facility_name,
            source_type=source_type,
            target_port_id=link.receiver_port_id,
            target_port_name=link.receiver_port_name,
            local_body_id=link.target_body_id,
            economy=str(link.economy),
            value=float(link.value),
            influence_type=influence_type,
            link_type='weak',
            confidence=_CONFIDENCE_INFERRED,
            reason=getattr(link, 'note', '') or f'{link.source_facility_name} weakly links to {link.receiver_port_name}.',
            caveats=caveats,
        )
        _add_influence(state, influence)
        _add_value(state.converted_port_economies if source_type == 'converted_port' else state.weak_link_economies, str(link.economy), float(link.value))

    counted_strong = {(link.source_facility_id, link.local_body_id, link.receiver_port_id) for link in getattr(topology_graph, 'strong_links', [])}
    for link in getattr(topology_graph, 'pass_through_links', []):
        state = states.get(_port_key_from_id(link.orbital_receiver_id, getattr(link, 'local_body_id', None)))
        if state is None or not link.economy:
            continue
        if (link.source_facility_id, link.local_body_id, link.orbital_receiver_id) in counted_strong:
            continue
        caveats = list(getattr(link, 'caveats', []) or [])
        influence = EconomyInfluence(
            source_id=link.source_facility_id,
            source_name=link.source_facility_name,
            source_type='port' if link.source_facility_id == link.surface_port_id else 'facility',
            target_port_id=link.orbital_receiver_id,
            target_port_name=link.orbital_receiver_name,
            local_body_id=link.local_body_id,
            economy=str(link.economy),
            value=float(link.value),
            influence_type=INFLUENCE_PASS_THROUGH,
            link_type='pass_through',
            confidence=_CONFIDENCE_INFERRED,
            reason=getattr(link, 'note', '') or f'{link.source_facility_name} passes influence through {link.surface_port_name}.',
            caveats=caveats,
        )
        _add_influence(state, influence)
        _add_value(state.pass_through_economies, str(link.economy), float(link.value))

    for state in states.values():
        _finalise_state(state)

    ordered_states = sorted(states.values(), key=lambda item: (str(item.local_body_id or ''), item.location_type, item.port_id))
    ledger = [influence for state in ordered_states for influence in state.influences]
    return ordered_states, ledger


def aggregate_port_strengths(port_states: list[PortEconomyState]) -> dict[str, float]:
    strengths: dict[str, float] = {}
    for state in port_states:
        for economy, value in state.final_economy_strengths.items():
            _add_value(strengths, economy, value)
    return strengths


def _is_direct_port_influence_source(graph_placement: Any, group: Any) -> bool:
    if not graph_placement.facility.is_port:
        return False
    return graph_placement == getattr(group, 'main_surface_port', None) or graph_placement == getattr(group, 'main_orbital_port', None)


def _add_influence(state: PortEconomyState, influence: EconomyInfluence) -> None:
    state.influences.append(influence)
    _add_value(state.final_economy_strengths, influence.economy, influence.value)


def _add_value(target: dict[str, float], economy: str, value: float) -> None:
    if not economy or value <= 0:
        return
    target[economy] = target.get(economy, 0.0) + float(value)


def _finalise_state(state: PortEconomyState) -> None:
    total = sum(max(0.0, value) for value in state.final_economy_strengths.values())
    if total <= 0:
        state.final_economy_composition = {}
        state.economy_order = []
        state.top_two = []
        state.tertiary_economies = []
        state.purity_score = 0.0
        state.contamination_risk = 'low'
        state.recommendations.append('Add local support facilities or body-backed colony ports to form a visible economy stack.')
        return

    ordered = sorted(state.final_economy_strengths.items(), key=lambda item: item[1], reverse=True)
    state.economy_order = [economy for economy, _ in ordered]
    state.final_economy_composition = {economy: (value / total) * 100.0 for economy, value in ordered}
    state.top_two = state.economy_order[:2]
    state.tertiary_economies = [economy for economy in state.economy_order[2:] if state.final_economy_composition.get(economy, 0.0) >= _TERTIARY_THRESHOLD]
    state.purity_score = sum(state.final_economy_composition.get(economy, 0.0) for economy in state.top_two)

    if state.purity_score >= 85 and not state.tertiary_economies:
        state.contamination_risk = 'low'
    elif state.purity_score >= 70:
        state.contamination_risk = 'medium'
    else:
        state.contamination_risk = 'high'

    if state.top_two:
        state.strengths.append(f'{state.port_name} protects {" / ".join(state.top_two)} as its top economy pair.')
    if state.tertiary_economies:
        joined = ', '.join(state.tertiary_economies)
        state.warnings.append(f'{joined} is above the tertiary pressure threshold for {state.port_name}.')
        state.recommendations.append(f'Reduce or isolate {joined} sources if the intended top-two economy pair should remain dominant.')
    if state.contamination_risk != 'low':
        state.recommendations.append('Prefer same-body support for the desired pair and move off-pair emitters to bodies without Main Ports.')
    else:
        state.recommendations.append('Maintain the current local support pattern; contamination pressure is currently contained.')

    contamination = set(state.tertiary_economies)
    if len(state.top_two) < 2 and len(state.economy_order) > 1:
        contamination.update(state.economy_order[1:])
    state.contamination_sources = [
        influence for influence in state.influences
        if influence.economy in contamination
    ]


def _economy_weight(facility: Any) -> float:
    if facility.is_port and not facility.is_colony_port:
        return FACILITY_ECONOMY_WEIGHTS['specialised_port']
    if facility.is_colony_port:
        return FACILITY_ECONOMY_WEIGHTS['colony_port']
    return FACILITY_ECONOMY_WEIGHTS['support_or_default']


def _profile_confidence(profile: Any) -> str:
    confidence = float(getattr(profile, 'confidence', 0.0) or 0.0)
    if confidence >= 0.75:
        return _CONFIDENCE_INFERRED
    return _CONFIDENCE_ESTIMATED


def _round_map(values: dict[str, float], *, ndigits: int = 3) -> dict[str, float]:
    return {key: round(float(value), ndigits) for key, value in sorted(values.items(), key=lambda item: item[1], reverse=True)}


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _placement_key(placement: Any) -> tuple[str, str, int]:
    return (placement.facility_id, str(placement.local_body_id or 'system'), int(placement.build_order))


def _graph_key(placement: Any) -> tuple[str, str, int]:
    return (placement.facility_id, str(placement.local_body_id or 'system'), int(placement.build_order))


def _resolved_key(placement: Any) -> tuple[str, str, int]:
    return (
        placement.facility.id,
        str(placement.spec.local_body_id or 'system'),
        int(placement.spec.build_order),
    )


def _port_key(port: Any) -> str:
    return _port_key_from_id(port.facility_id, port.local_body_id)


def _port_key_from_id(port_id: str, local_body_id: str | None) -> str:
    return f'{local_body_id or "system"}::{port_id}'


def _is_converted(topology_graph: Any, facility_id: str, local_body_id: str | None) -> bool:
    for port in getattr(topology_graph, 'converted_ports', []):
        if port.facility_id == facility_id and str(port.local_body_id or 'system') == str(local_body_id or 'system'):
            return True
    return False


def port_states_to_dict(port_states: list[PortEconomyState]) -> list[dict[str, Any]]:
    return [state.to_dict() for state in port_states]


def influence_ledger_to_dict(ledger: list[EconomyInfluence]) -> list[dict[str, Any]]:
    return [item.to_dict() for item in ledger]
