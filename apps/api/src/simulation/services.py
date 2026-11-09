"""Explainable service unlock modelling from the facility catalogue."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edfinder_api.mechanics.service_rules import (
    MODELLED_SERVICES,
    SERVICE_STATUS_ACTIVE,
    SERVICE_STATUS_LOCKED,
    SERVICE_STATUS_UNKNOWN,
)


SERVICE_PHRASES = {
    'commodity_market': ['commodities', 'commodity market'],
    'shipyard': ['shipyard'],
    'outfitting': ['outfitting'],
    'universal_cartographics': ['uc', 'universal cartographics'],
    'vista_genomics': ['vg', 'vista genomics'],
    'black_market': ['black market'],
    'crew_lounge': ['crew lounge'],
    'pioneer_supplies': ['pioneer supplies'],
}


@dataclass(frozen=True)
class ServiceState:
    status: str
    reason: str
    requirements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'status': self.status,
            'reason': self.reason,
            'requirements': self.requirements,
        }


def model_services(placements: list[Any], topology_graph: Any) -> dict[str, dict[str, Any]]:
    active: dict[str, ServiceState] = {}
    locked: dict[str, ServiceState] = {}
    linked_sources = {link.source_facility_id for link in topology_graph.strong_links}

    for placement in placements:
        facility = placement.facility
        for unlock in _facility_unlocks(facility):
            text = str(unlock.get('description') or '')
            unlock_type = str(unlock.get('type') or '')
            for service, phrases in SERVICE_PHRASES.items():
                if not _mentions(text, phrases):
                    continue
                if unlock_type == 'Strong Link Unlock':
                    if facility.id in linked_sources:
                        active[service] = ServiceState(
                            status=SERVICE_STATUS_ACTIVE,
                            reason=f'{_service_label(service)} unlocked by strong-linked {facility.name}.',
                        )
                    else:
                        locked.setdefault(service, ServiceState(
                            status=SERVICE_STATUS_LOCKED,
                            reason=f'{facility.name} documents {_service_label(service)}, but it is not strongly linked.',
                            requirements=[f'Strong-link {facility.name} to a local Main Port.'],
                        ))
                else:
                    active[service] = ServiceState(
                        status=SERVICE_STATUS_ACTIVE,
                        reason=f'{_service_label(service)} documented as a system unlock from {facility.name}.',
                    )

    result: dict[str, dict[str, Any]] = {}
    for service in MODELLED_SERVICES:
        state = active.get(service) or locked.get(service) or ServiceState(
            status=SERVICE_STATUS_UNKNOWN,
            reason='No documented service unlock rule is present in the loaded facility catalogue for this build.',
        )
        result[service] = state.to_dict()
    return result


def _facility_unlocks(facility: Any) -> list[dict[str, Any]]:
    stat_effects = getattr(facility, 'stat_effects', {}) or {}
    return [item for item in stat_effects.get('unlocks', []) if isinstance(item, dict)]


def _mentions(text: str, phrases: list[str]) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in phrases)


def _service_label(service: str) -> str:
    return service.replace('_', ' ').title()
