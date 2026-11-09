"""Topology graph for deterministic colonisation build simulation.

This module models the Frontier-described local-body rules:

* only exact same local body placements strongly link;
* parent planets and moons are separate local bodies;
* surface and orbital assets around the same body are local;
* highest tier, then earliest build order, selects Main Ports;
* Main Ports receive weak links but never emit them;
* weak links target only Main Ports on other local bodies at fixed strength.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from edfinder_api.domain.facilities import (
    FacilityTemplate,
    LOC_ORBITAL,
    LOC_RINGED_ORBITAL,
    LOC_SURFACE,
)
from edfinder_api.mechanics.link_rules import WEAK_LINK_STRENGTH
from edfinder_api.simulation.link_modifiers import modified_strong_link_value


ROLE_PRIMARY_PORT = 'primary_port'
ROLE_MAIN_SURFACE_PORT = 'main_surface_port'
ROLE_MAIN_ORBITAL_PORT = 'main_orbital_port'
ROLE_SUPPORTING_FACILITY = 'supporting_facility'
ROLE_CONVERTED_SUPPORT_PORT = 'converted_support_port'
ROLE_ORDINARY_PORT = 'ordinary_port'


@dataclass(frozen=True)
class GraphPlacement:
    facility: FacilityTemplate
    local_body_id: Optional[str]
    build_order: int
    location_type: str
    placement_instance_id: Optional[str] = None
    economy: Optional[str] = None
    body_name: Optional[str] = None
    parent_body_id: Optional[str] = None
    is_primary_port: bool = False
    body_profile: dict | None = None

    @property
    def facility_id(self) -> str:
        return self.facility.id

    @property
    def facility_name(self) -> str:
        return self.facility.name


@dataclass(frozen=True)
class ClassifiedPlacement:
    placement: GraphPlacement
    effective_role: str


@dataclass
class LocalBodyGroup:
    local_body_id: str
    body_name: Optional[str] = None
    parent_body_id: Optional[str] = None
    orbital_ports: list[GraphPlacement] = field(default_factory=list)
    surface_ports: list[GraphPlacement] = field(default_factory=list)
    facilities: list[GraphPlacement] = field(default_factory=list)
    main_orbital_port: Optional[GraphPlacement] = None
    main_surface_port: Optional[GraphPlacement] = None


@dataclass(frozen=True)
class StrongLink:
    source_facility_id: str
    source_facility_name: str
    receiver_port_id: str
    receiver_port_name: str
    local_body_id: str
    economy: Optional[str]
    value: float
    note: str
    base_value: float = 0.0
    modifier: float = 1.0
    modifier_reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass(frozen=True)
class WeakLink:
    source_facility_id: str
    source_facility_name: str
    receiver_port_id: str
    receiver_port_name: str
    source_body_id: str
    target_body_id: str
    economy: Optional[str]
    value: float
    note: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass(frozen=True)
class PassThroughLink:
    source_facility_id: str
    source_facility_name: str
    surface_port_id: str
    surface_port_name: str
    orbital_receiver_id: str
    orbital_receiver_name: str
    local_body_id: str
    economy: Optional[str]
    value: float
    note: str
    base_value: float = 0.0
    modifier: float = 1.0
    modifier_reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass(frozen=True)
class ConvertedPort:
    facility_id: str
    facility_name: str
    local_body_id: str
    location_type: str
    economy: Optional[str]
    reason: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class TopologyGraph:
    local_body_groups: list[LocalBodyGroup] = field(default_factory=list)
    classified_placements: list[ClassifiedPlacement] = field(default_factory=list)
    strong_links: list[StrongLink] = field(default_factory=list)
    weak_links: list[WeakLink] = field(default_factory=list)
    converted_ports: list[ConvertedPort] = field(default_factory=list)
    pass_through_links: list[PassThroughLink] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def infer_location_type(facility: FacilityTemplate) -> str:
    if facility.allowed_location == LOC_SURFACE:
        return 'surface'
    if facility.allowed_location in {LOC_ORBITAL, LOC_RINGED_ORBITAL}:
        return 'orbital'
    return 'orbital' if facility.is_port else 'surface'


def select_main_surface_port(ports: list[GraphPlacement]) -> Optional[GraphPlacement]:
    return _select_main_port([port for port in ports if port.location_type == 'surface'])


def select_main_orbital_port(ports: list[GraphPlacement]) -> Optional[GraphPlacement]:
    return _select_main_port([port for port in ports if port.location_type == 'orbital'])


def build_topology_graph(placements: list[GraphPlacement]) -> TopologyGraph:
    groups = _group_by_local_body(placements)
    converted: list[ConvertedPort] = []
    classified: list[ClassifiedPlacement] = []
    for group in groups:
        group.main_surface_port = select_main_surface_port(group.surface_ports)
        group.main_orbital_port = select_main_orbital_port(group.orbital_ports)
        for placement in group.facilities:
            role = _effective_role(placement, group)
            classified.append(ClassifiedPlacement(placement=placement, effective_role=role))
            if role == ROLE_CONVERTED_SUPPORT_PORT:
                converted.append(ConvertedPort(
                    facility_id=placement.facility_id,
                    facility_name=placement.facility_name,
                    local_body_id=group.local_body_id,
                    location_type=placement.location_type,
                    economy=placement.economy,
                    reason='A higher-tier or earlier same-tier port is the Main Port, so this port behaves as support.',
                ))

    graph = TopologyGraph(
        local_body_groups=groups,
        classified_placements=classified,
        converted_ports=converted,
        assumptions=_assumptions(placements),
    )
    graph.strong_links = _generate_strong_links(graph)
    graph.pass_through_links = _generate_pass_through_links(graph)
    graph.weak_links = _generate_weak_links(graph)
    return graph


def _group_by_local_body(placements: list[GraphPlacement]) -> list[LocalBodyGroup]:
    by_id: dict[str, LocalBodyGroup] = {}
    for placement in sorted(placements, key=lambda item: (item.local_body_id or '', item.build_order)):
        body_id = str(placement.local_body_id or 'system')
        group = by_id.setdefault(body_id, LocalBodyGroup(
            local_body_id=body_id,
            body_name=placement.body_name,
            parent_body_id=placement.parent_body_id,
        ))
        group.facilities.append(placement)
        if placement.facility.is_port:
            if placement.location_type == 'surface':
                group.surface_ports.append(placement)
            else:
                group.orbital_ports.append(placement)
    return list(by_id.values())


def _select_main_port(ports: list[GraphPlacement]) -> Optional[GraphPlacement]:
    if not ports:
        return None
    return sorted(ports, key=lambda port: (-port.facility.tier, port.build_order, port.facility_id))[0]


def _effective_role(placement: GraphPlacement, group: LocalBodyGroup) -> str:
    if not placement.facility.is_port:
        return ROLE_SUPPORTING_FACILITY
    if placement == group.main_surface_port:
        return ROLE_MAIN_SURFACE_PORT
    if placement == group.main_orbital_port:
        return ROLE_MAIN_ORBITAL_PORT
    if placement.economy and _can_convert_to_support(placement):
        return ROLE_CONVERTED_SUPPORT_PORT
    return ROLE_ORDINARY_PORT


def _can_convert_to_support(placement: GraphPlacement) -> bool:
    return placement.facility.is_port and bool(placement.economy)


def _generate_strong_links(graph: TopologyGraph) -> list[StrongLink]:
    links: list[StrongLink] = []
    role_by_key = {
        _key(item.placement): item.effective_role
        for item in graph.classified_placements
    }
    for group in graph.local_body_groups:
        for placement in group.facilities:
            if not _can_emit_strong_link(placement, role_by_key.get(_key(placement))):
                continue
            receiver = _strong_receiver(placement, group)
            if receiver is None or receiver == placement:
                continue
            modifier = _strong_link_modifier(placement)
            links.append(StrongLink(
                source_facility_id=placement.facility_id,
                source_facility_name=placement.facility_name,
                receiver_port_id=receiver.facility_id,
                receiver_port_name=receiver.facility_name,
                local_body_id=group.local_body_id,
                economy=placement.economy,
                value=modifier.value,
                note=f'{placement.facility_name} strongly links to local Main Port {receiver.facility_name}.',
                base_value=modifier.base_value,
                modifier=modifier.multiplier,
                modifier_reasons=modifier.reasons,
                caveats=modifier.caveats,
                assumptions=modifier.assumptions,
            ))
    return links


def _generate_pass_through_links(graph: TopologyGraph) -> list[PassThroughLink]:
    links: list[PassThroughLink] = []
    strong_sources = {(link.source_facility_id, link.local_body_id): link for link in graph.strong_links}
    for group in graph.local_body_groups:
        if not group.main_surface_port or not group.main_orbital_port:
            continue
        surface_modifier = _strong_link_modifier(group.main_surface_port)
        links.append(PassThroughLink(
            source_facility_id=group.main_surface_port.facility_id,
            source_facility_name=group.main_surface_port.facility_name,
            surface_port_id=group.main_surface_port.facility_id,
            surface_port_name=group.main_surface_port.facility_name,
            orbital_receiver_id=group.main_orbital_port.facility_id,
            orbital_receiver_name=group.main_orbital_port.facility_name,
            local_body_id=group.local_body_id,
            economy=group.main_surface_port.economy,
            value=surface_modifier.value,
            note=(
                f'Surface Main Port {group.main_surface_port.facility_name} strongly links to '
                f'orbital Main Port {group.main_orbital_port.facility_name}.'
            ),
            base_value=surface_modifier.base_value,
            modifier=surface_modifier.multiplier,
            modifier_reasons=surface_modifier.reasons,
            caveats=surface_modifier.caveats,
            assumptions=surface_modifier.assumptions,
        ))
        for placement in group.facilities:
            if placement.location_type != 'surface' or placement.facility.is_port or not placement.economy:
                continue
            strong = strong_sources.get((placement.facility_id, group.local_body_id))
            if not strong:
                continue
            links.append(PassThroughLink(
                source_facility_id=placement.facility_id,
                source_facility_name=placement.facility_name,
                surface_port_id=group.main_surface_port.facility_id,
                surface_port_name=group.main_surface_port.facility_name,
                orbital_receiver_id=group.main_orbital_port.facility_id,
                orbital_receiver_name=group.main_orbital_port.facility_name,
                local_body_id=group.local_body_id,
                economy=placement.economy,
                value=strong.value,
                note=(
                    f'{placement.facility_name} links to surface Main Port {group.main_surface_port.facility_name}; '
                    f'{group.main_surface_port.facility_name} passes influence to orbital Main Port '
                    f'{group.main_orbital_port.facility_name}.'
                ),
            ))
    return links


def _generate_weak_links(graph: TopologyGraph) -> list[WeakLink]:
    links: list[WeakLink] = []
    weak_receivers = {
        group.local_body_id: _weak_receiver(group)
        for group in graph.local_body_groups
    }
    role_by_key = {
        _key(item.placement): item.effective_role
        for item in graph.classified_placements
    }
    for group in graph.local_body_groups:
        for placement in group.facilities:
            if not _can_emit_weak_link(placement, role_by_key.get(_key(placement))):
                continue
            for target_body_id, target in weak_receivers.items():
                if target is None or target_body_id == group.local_body_id:
                    continue
                links.append(WeakLink(
                    source_facility_id=placement.facility_id,
                    source_facility_name=placement.facility_name,
                    receiver_port_id=target.facility_id,
                    receiver_port_name=target.facility_name,
                    source_body_id=group.local_body_id,
                    target_body_id=target_body_id,
                    economy=placement.economy,
                    value=WEAK_LINK_STRENGTH,
                    note=f'{placement.facility_name} weakly links to Main Port {target.facility_name} on another body.',
                ))
    return links


def _strong_receiver(placement: GraphPlacement, group: LocalBodyGroup) -> Optional[GraphPlacement]:
    if placement.location_type == 'surface':
        return group.main_surface_port or group.main_orbital_port
    return group.main_orbital_port or group.main_surface_port


def _weak_receiver(group: LocalBodyGroup) -> Optional[GraphPlacement]:
    return group.main_orbital_port or group.main_surface_port


def _can_emit_strong_link(placement: GraphPlacement, role: Optional[str]) -> bool:
    if not placement.economy:
        return False
    if role in {ROLE_MAIN_SURFACE_PORT, ROLE_MAIN_ORBITAL_PORT, ROLE_PRIMARY_PORT, ROLE_ORDINARY_PORT}:
        return False
    return placement.facility.is_support_facility or role == ROLE_CONVERTED_SUPPORT_PORT


def _can_emit_weak_link(placement: GraphPlacement, role: Optional[str]) -> bool:
    if not placement.economy:
        return False
    if role in {ROLE_MAIN_SURFACE_PORT, ROLE_MAIN_ORBITAL_PORT, ROLE_PRIMARY_PORT, ROLE_ORDINARY_PORT}:
        return False
    return placement.facility.is_support_facility or role == ROLE_CONVERTED_SUPPORT_PORT


def _strong_link_modifier(source: GraphPlacement):
    return modified_strong_link_value(
        source_tier=source.facility.tier,
        economy=source.economy,
        body_profile=source.body_profile,
    )


def _key(placement: GraphPlacement) -> str:
    return placement.placement_instance_id or f'{placement.build_order}:{placement.facility_id}:{placement.local_body_id or "system"}'


def _assumptions(placements: list[GraphPlacement]) -> list[str]:
    if any(p.local_body_id is None for p in placements):
        return ['Placements without a local body are grouped at system level and cannot form confirmed local links.']
    return []
