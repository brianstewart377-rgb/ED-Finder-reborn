"""Per-port service dependency graph and unlock ledger for Simulation Preview.

Stage 4B is intentionally additive. It keeps the existing system-level service
model intact, then explains service availability at each Main Port using the
current topology graph and documented facility-catalogue unlock text.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from edfinder_api.mechanics.service_rules import (
    MODELLED_SERVICES,
    SERVICE_STATUS_ACTIVE,
    SERVICE_STATUS_LOCKED,
    SERVICE_STATUS_UNKNOWN,
)
from edfinder_api.simulation.services import SERVICE_PHRASES


UNLOCK_PORT_DEFAULT = 'port_default'
UNLOCK_SYSTEM = 'system_unlock'
UNLOCK_STRONG_LINK = 'strong_link_unlock'
UNLOCK_LOCAL_BODY = 'local_body_unlock'
UNLOCK_INFERRED = 'inferred_unlock'
UNLOCK_UNKNOWN = 'unknown_rule'

LINK_NONE = 'none'
LINK_STRONG = 'strong'
LINK_WEAK = 'weak'
LINK_PASS_THROUGH = 'pass_through'
LINK_SYSTEM = 'system'

CONFIDENCE_COMMUNITY_OBSERVED = 'community_observed'
CONFIDENCE_INFERRED = 'inferred'
CONFIDENCE_SPECULATIVE = 'speculative'
CONFIDENCE_UNKNOWN = 'unknown'

_QUALIFIED_UNLOCK_CAVEAT = 'This service rule has qualifiers that ED-Finder cannot fully verify yet.'
_PASS_THROUGH_SERVICE_CAVEAT = 'Pass-through service unlock behaviour is not yet verified; economy pass-through does not automatically imply service pass-through.'
_CONVERTED_PORT_SERVICE_CAVEAT = 'Converted-port service unlock behaviour is inferred and should be verified in-game.'

# Conservative hints for useful locked-service recommendations when no placed
# facility currently documents that service. These are based on the bundled
# catalogue's documented unlock text and are phrased as suggestions, not hidden
# scoring mechanics.
_SERVICE_REQUIREMENT_HINTS = {
    'black_market': 'Build Pirate Base on the same local body with a strong link to this Main Port.',
    'universal_cartographics': 'Build Relay Station, Comm Station, Satellite, Research Station, or Exploration support where its documented unlock rule can apply.',
    'vista_genomics': 'Build Medical, Scientific, Relay Station, Comm Station, or Satellite support where its documented unlock rule can apply.',
    'shipyard': 'Build Military, Industrial, or High Tech support where the documented Shipyard unlock rule can apply.',
    'outfitting': 'Build Military, Industrial, or High Tech support where the documented Outfitting unlock rule can apply.',
    'crew_lounge': 'Build Space Bar support where the documented Crew Lounge unlock rule can apply.',
}


@dataclass(frozen=True)
class ServiceUnlockEntry:
    service: str
    status: str
    source_id: str | None
    source_name: str | None
    source_type: str | None
    target_port_id: str
    target_port_name: str
    local_body_id: str | None
    unlock_type: str
    link_type: str | None
    confidence: str
    reason: str
    requirements: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PortServiceState:
    port_id: str
    port_name: str
    local_body_id: str | None
    body_name: str | None
    location_type: str
    effective_role: str
    port_tier: int = 0
    port_economy: str | None = None
    active_services: dict[str, ServiceUnlockEntry] = field(default_factory=dict)
    locked_services: dict[str, ServiceUnlockEntry] = field(default_factory=dict)
    unknown_services: dict[str, ServiceUnlockEntry] = field(default_factory=dict)
    service_sources: list[ServiceUnlockEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data['active_services'] = {key: entry.to_dict() for key, entry in sorted(self.active_services.items())}
        data['locked_services'] = {key: entry.to_dict() for key, entry in sorted(self.locked_services.items())}
        data['unknown_services'] = {key: entry.to_dict() for key, entry in sorted(self.unknown_services.items())}
        data['service_sources'] = [entry.to_dict() for entry in self.service_sources]
        return data


def build_port_service_states(
    *,
    placements: list[Any],
    topology_graph: Any,
) -> tuple[list[PortServiceState], list[ServiceUnlockEntry]]:
    role_by_key = {
        _placement_key(item.placement): item.effective_role
        for item in getattr(topology_graph, 'classified_placements', [])
    }
    graph_by_id_body = {
        (placement.facility_id, str(placement.local_body_id or 'system')): placement
        for group in getattr(topology_graph, 'local_body_groups', [])
        for placement in group.facilities
    }

    states: dict[str, PortServiceState] = {}
    for group in getattr(topology_graph, 'local_body_groups', []):
        for port, role in ((getattr(group, 'main_surface_port', None), 'main_surface_port'), (getattr(group, 'main_orbital_port', None), 'main_orbital_port')):
            if port is None:
                continue
            state = PortServiceState(
                port_id=port.facility_id,
                port_name=port.facility_name,
                local_body_id=group.local_body_id,
                body_name=group.body_name,
                location_type=port.location_type,
                effective_role=role_by_key.get(_graph_key(port), role),
                port_tier=getattr(port.facility, 'tier', 0) or 0,
                port_economy=getattr(port, 'economy', None),
            )
            states[_port_key(port)] = state

    if not states:
        return [], []

    _add_port_default_services(states)
    _apply_documented_unlocks(
        states=states,
        placements=placements,
        topology_graph=topology_graph,
        graph_by_id_body=graph_by_id_body,
    )
    for state in states.values():
        _fill_missing_services(state)
        _finalise_state(state)

    ordered_states = sorted(states.values(), key=lambda item: (str(item.local_body_id or ''), item.location_type, item.port_id))
    ledger = [entry for state in ordered_states for entry in state.service_sources]
    return ordered_states, ledger


def _add_port_default_services(states: dict[str, PortServiceState]) -> None:
    for state in states.values():
        entry = ServiceUnlockEntry(
            service='commodity_market',
            status=SERVICE_STATUS_ACTIVE,
            source_id=state.port_id,
            source_name=state.port_name,
            source_type='port',
            target_port_id=state.port_id,
            target_port_name=state.port_name,
            local_body_id=state.local_body_id,
            unlock_type=UNLOCK_PORT_DEFAULT,
            link_type=LINK_NONE,
            confidence=CONFIDENCE_INFERRED,
            reason=f'{_service_label("commodity_market")} is treated as a default Main Port service in the preview.',
            requirements=[],
            caveats=['Default port services are modelled conservatively until port-type-specific observations are richer.'],
        )
        _record_entry(state, entry)


def _apply_documented_unlocks(
    *,
    states: dict[str, PortServiceState],
    placements: list[Any],
    topology_graph: Any,
    graph_by_id_body: dict[tuple[str, str], Any],
) -> None:
    strong_links = list(getattr(topology_graph, 'strong_links', []))
    converted_keys = {
        (port.facility_id, str(port.local_body_id or 'system'))
        for port in getattr(topology_graph, 'converted_ports', [])
    }
    pass_through_links = list(getattr(topology_graph, 'pass_through_links', []))
    pass_through_sources = {
        (link.source_facility_id, str(link.local_body_id or 'system'))
        for link in pass_through_links
    }
    for placement in placements:
        facility = placement.facility
        graph_placement = graph_by_id_body.get((facility.id, str(placement.spec.local_body_id or 'system')))
        source_type = 'port' if facility.is_port else 'facility'
        for unlock in _facility_unlocks(facility):
            text = str(unlock.get('description') or '')
            raw_type = str(unlock.get('type') or '')
            services = _services_in_text(text)
            if not services:
                continue
            if raw_type == 'Strong Link Unlock':
                matching_links = _matching_strong_links(strong_links, placement)
                if matching_links:
                    for link in matching_links:
                        state = states.get(_port_key_from_id(link.receiver_port_id, link.local_body_id))
                        if state is None:
                            continue
                        source_key = (facility.id, str(placement.spec.local_body_id or 'system'))
                        for service in services:
                            caveats = _rule_caveats(text)
                            confidence = CONFIDENCE_COMMUNITY_OBSERVED
                            if source_key in converted_keys:
                                caveats.append(_CONVERTED_PORT_SERVICE_CAVEAT)
                                confidence = CONFIDENCE_INFERRED
                            entry = ServiceUnlockEntry(
                                service=service,
                                status=SERVICE_STATUS_ACTIVE,
                                source_id=facility.id,
                                source_name=facility.name,
                                source_type=source_type,
                                target_port_id=link.receiver_port_id,
                                target_port_name=link.receiver_port_name,
                                local_body_id=link.local_body_id,
                                unlock_type=UNLOCK_STRONG_LINK,
                                link_type=LINK_STRONG,
                                confidence=confidence,
                                reason=f'{facility.name} strongly links to {link.receiver_port_name} and unlocks {_service_label(service)}.',
                                requirements=[],
                                caveats=_unique(caveats),
                            )
                            _record_entry(state, entry)
                    _record_unverified_pass_through_entries(
                        states=states,
                        pass_through_links=pass_through_links,
                        placement=placement,
                        services=services,
                        source_type=source_type,
                        text=text,
                    )
                else:
                    target_states = _candidate_local_states(states, graph_placement)
                    for state in target_states:
                        for service in services:
                            if service in state.active_services:
                                continue
                            source_key = (facility.id, str(placement.spec.local_body_id or 'system'))
                            caveats = _rule_caveats(text)
                            confidence = CONFIDENCE_COMMUNITY_OBSERVED
                            if source_key in pass_through_sources:
                                caveats.append(_PASS_THROUGH_SERVICE_CAVEAT)
                                confidence = CONFIDENCE_UNKNOWN
                            if source_key in converted_keys:
                                caveats.append(_CONVERTED_PORT_SERVICE_CAVEAT)
                                confidence = CONFIDENCE_INFERRED
                            entry = ServiceUnlockEntry(
                                service=service,
                                status=SERVICE_STATUS_LOCKED,
                                source_id=facility.id,
                                source_name=facility.name,
                                source_type=source_type,
                                target_port_id=state.port_id,
                                target_port_name=state.port_name,
                                local_body_id=state.local_body_id,
                                unlock_type=UNLOCK_STRONG_LINK,
                                link_type=LINK_STRONG,
                                confidence=confidence,
                                reason=f'{facility.name} documents {_service_label(service)}, but it is not strongly linked to {state.port_name}.',
                                requirements=[f'Strong-link {facility.name} to {state.port_name} on local body {state.local_body_id or "system"}.'],
                                caveats=_unique(caveats),
                            )
                            _record_entry(state, entry)
            else:
                for state in states.values():
                    for service in services:
                        status, confidence, reason, requirements, caveats = _classify_system_unlock_for_port(
                            service=service,
                            text=text,
                            facility_name=facility.name,
                            state=state,
                        )
                        entry = ServiceUnlockEntry(
                            service=service,
                            status=status,
                            source_id=facility.id,
                            source_name=facility.name,
                            source_type=source_type,
                            target_port_id=state.port_id,
                            target_port_name=state.port_name,
                            local_body_id=state.local_body_id,
                            unlock_type=UNLOCK_SYSTEM,
                            link_type=LINK_SYSTEM,
                            confidence=confidence,
                            reason=reason,
                            requirements=requirements,
                            caveats=caveats,
                        )
                        _record_entry(state, entry)


def _record_unverified_pass_through_entries(
    *,
    states: dict[str, PortServiceState],
    pass_through_links: list[Any],
    placement: Any,
    services: list[str],
    source_type: str,
    text: str,
) -> None:
    body_id = str(placement.spec.local_body_id or 'system')
    for link in pass_through_links:
        if link.source_facility_id != placement.facility.id or str(link.local_body_id or 'system') != body_id:
            continue
        state = states.get(_port_key_from_id(link.orbital_receiver_id, link.local_body_id))
        if state is None:
            continue
        for service in services:
            if service in state.active_services:
                continue
            entry = ServiceUnlockEntry(
                service=service,
                status=SERVICE_STATUS_UNKNOWN,
                source_id=placement.facility.id,
                source_name=placement.facility.name,
                source_type=source_type,
                target_port_id=state.port_id,
                target_port_name=state.port_name,
                local_body_id=state.local_body_id,
                unlock_type=UNLOCK_UNKNOWN,
                link_type=LINK_PASS_THROUGH,
                confidence=CONFIDENCE_UNKNOWN,
                reason=f'{placement.facility.name} has pass-through topology toward {state.port_name}, but service pass-through is not verified for {_service_label(service)}.',
                requirements=[],
                caveats=_unique([*_rule_caveats(text), _PASS_THROUGH_SERVICE_CAVEAT]),
            )
            _record_entry(state, entry)


def _matching_strong_links(strong_links: list[Any], placement: Any) -> list[Any]:
    body_id = str(placement.spec.local_body_id or 'system')
    return [
        link for link in strong_links
        if link.source_facility_id == placement.facility.id and str(link.local_body_id or 'system') == body_id
    ]


def _classify_system_unlock_for_port(
    *,
    service: str,
    text: str,
    facility_name: str,
    state: PortServiceState,
) -> tuple[str, str, str, list[str], list[str]]:
    fragment = _service_fragment(text, service)
    caveats = _rule_caveats(fragment)
    if not _unlock_has_qualifiers(fragment):
        return (
            SERVICE_STATUS_ACTIVE,
            CONFIDENCE_COMMUNITY_OBSERVED,
            f'{_service_label(service)} is documented as an unqualified system unlock from {facility_name}.',
            [],
            caveats,
        )
    match = _port_matches_service_qualifier(fragment, state)
    if match is True:
        return (
            SERVICE_STATUS_ACTIVE,
            CONFIDENCE_COMMUNITY_OBSERVED,
            f'{_service_label(service)} qualifier appears to match {state.port_name}: {fragment}.',
            [],
            caveats,
        )
    if match is False:
        return (
            SERVICE_STATUS_LOCKED,
            CONFIDENCE_INFERRED,
            f'{_service_label(service)} is documented by {facility_name}, but {state.port_name} does not satisfy the qualifier: {fragment}.',
            [f'Use a Main Port that satisfies this catalogue qualifier: {fragment}.'],
            caveats,
        )
    return (
        SERVICE_STATUS_UNKNOWN,
        CONFIDENCE_UNKNOWN,
        f'{_service_label(service)} has catalogue qualifiers that ED-Finder cannot fully verify for {state.port_name}: {fragment}.',
        [],
        _unique([*caveats, _QUALIFIED_UNLOCK_CAVEAT]),
    )


def _candidate_local_states(states: dict[str, PortServiceState], graph_placement: Any | None) -> list[PortServiceState]:
    if graph_placement is None:
        return list(states.values())
    local = [state for state in states.values() if str(state.local_body_id or 'system') == str(graph_placement.local_body_id or 'system')]
    return local or list(states.values())


def _fill_missing_services(state: PortServiceState) -> None:
    for service in sorted(MODELLED_SERVICES):
        if service in state.active_services or service in state.locked_services:
            continue
        if service in _SERVICE_REQUIREMENT_HINTS:
            entry = ServiceUnlockEntry(
                service=service,
                status=SERVICE_STATUS_LOCKED,
                source_id=None,
                source_name=None,
                source_type=None,
                target_port_id=state.port_id,
                target_port_name=state.port_name,
                local_body_id=state.local_body_id,
                unlock_type=UNLOCK_INFERRED,
                link_type=None,
                confidence=CONFIDENCE_INFERRED,
                reason=f'{_service_label(service)} is not active at {state.port_name}; a documented support source is not currently satisfying the rule.',
                requirements=[_SERVICE_REQUIREMENT_HINTS[service]],
                caveats=['Locked recommendation is inferred from documented catalogue unlock text and should be checked against in-game observations.'],
            )
        else:
            entry = ServiceUnlockEntry(
                service=service,
                status=SERVICE_STATUS_UNKNOWN,
                source_id=None,
                source_name=None,
                source_type=None,
                target_port_id=state.port_id,
                target_port_name=state.port_name,
                local_body_id=state.local_body_id,
                unlock_type=UNLOCK_UNKNOWN,
                link_type=None,
                confidence=CONFIDENCE_UNKNOWN,
                reason=f'{_service_label(service)} unlock behaviour is not yet verified for this port/facility combination.',
                requirements=[],
                caveats=['Treat as unknown until observed in-game or confirmed by source data.'],
            )
        _record_entry(state, entry)


def _finalise_state(state: PortServiceState) -> None:
    if state.locked_services:
        locked = ', '.join(_service_label(service) for service in sorted(state.locked_services)[:3])
        state.recommendations.append(f'Build or reposition documented support if {locked} is desired at {state.port_name}.')
    if 'black_market' in state.locked_services or 'black_market' in state.active_services:
        state.recommendations.append('Avoid Pirate Base unless Black Market is worth the economy and contamination trade-off.')
    unknown_count = len(state.unknown_services)
    if unknown_count:
        state.warnings.append(f'{unknown_count} service rule(s) remain unknown for {state.port_name}.')


def _record_entry(state: PortServiceState, entry: ServiceUnlockEntry) -> None:
    state.service_sources.append(entry)
    if entry.status == SERVICE_STATUS_ACTIVE:
        state.active_services[entry.service] = entry
        state.locked_services.pop(entry.service, None)
        state.unknown_services.pop(entry.service, None)
    elif entry.status == SERVICE_STATUS_LOCKED:
        if entry.service not in state.active_services:
            state.locked_services.setdefault(entry.service, entry)
    else:
        if entry.service not in state.active_services and entry.service not in state.locked_services:
            state.unknown_services.setdefault(entry.service, entry)


def _facility_unlocks(facility: Any) -> list[dict[str, Any]]:
    stat_effects = getattr(facility, 'stat_effects', {}) or {}
    return [item for item in stat_effects.get('unlocks', []) if isinstance(item, dict)]


def _services_in_text(text: str) -> list[str]:
    lowered = text.lower()
    return sorted(service for service, phrases in SERVICE_PHRASES.items() if any(phrase in lowered for phrase in phrases))


def _service_fragment(text: str, service: str) -> str:
    clauses = [clause.strip() for clause in text.replace(';', ',').split(',') if clause.strip()]
    phrases = SERVICE_PHRASES.get(service, [])
    for clause in clauses:
        lowered = clause.lower()
        if any(phrase in lowered for phrase in phrases):
            return clause
    return text


def _unlock_has_qualifiers(text: str) -> bool:
    lowered = text.lower()
    markers = (' at ', ' non-', 'only ', 'outpost', 'outposts', 'port', 'ports', 'surface', 'orbital', 'industrial', 'military', 'pirate', 'scientific')
    return any(marker in lowered for marker in markers)


def _port_matches_service_qualifier(text: str, state: PortServiceState) -> bool | None:
    lowered = text.lower()
    if 't1 surface' in lowered:
        return state.location_type == 'surface' and state.port_tier == 1
    if 't2 orbital' in lowered:
        return state.location_type == 'orbital' and state.port_tier == 2
    if 'surface port' in lowered or 'surface ports' in lowered:
        return state.location_type == 'surface'
    if 'orbital port' in lowered or 'orbital ports' in lowered:
        return state.location_type == 'orbital'
    # Outpost/faction/economy qualifiers are not yet reliably mapped to every
    # port template, so they remain unknown instead of being over-activated.
    if any(marker in lowered for marker in ('outpost', 'outposts', 'non-', 'pirate', 'scientific', 'military', 'industrial')):
        return None
    return None


def _rule_caveats(text: str) -> list[str]:
    lowered = text.lower()
    caveats: list[str] = []
    if _unlock_has_qualifiers(text) or ' or ' in lowered:
        caveats.append('This service rule has qualifiers that ED-Finder cannot fully verify yet.')
    return caveats


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _service_label(service: str) -> str:
    if service == 'universal_cartographics':
        return 'Universal Cartographics'
    if service == 'vista_genomics':
        return 'Vista Genomics'
    return service.replace('_', ' ').title()


def _placement_key(placement: Any) -> tuple[str, str, int]:
    return (placement.facility_id, str(placement.local_body_id or 'system'), int(placement.build_order))


def _graph_key(placement: Any) -> tuple[str, str, int]:
    return (placement.facility_id, str(placement.local_body_id or 'system'), int(placement.build_order))


def _port_key(port: Any) -> str:
    return _port_key_from_id(port.facility_id, port.local_body_id)


def _port_key_from_id(port_id: str, local_body_id: str | None) -> str:
    return f'{local_body_id or "system"}::{port_id}'


def port_service_states_to_dict(port_states: list[PortServiceState]) -> list[dict[str, Any]]:
    return [state.to_dict() for state in port_states]


def service_unlock_ledger_to_dict(ledger: list[ServiceUnlockEntry]) -> list[dict[str, Any]]:
    return [entry.to_dict() for entry in ledger]
