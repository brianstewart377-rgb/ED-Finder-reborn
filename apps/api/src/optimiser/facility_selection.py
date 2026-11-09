"""Catalogue-driven facility selection for Stage 5A optimiser candidates."""
from __future__ import annotations

from typing import Iterable, Optional

from edfinder_api.domain.facilities import FacilityTemplate, is_colony_ship
from edfinder_api.optimiser.archetype_rules import ArchetypeRule


def _text(template: FacilityTemplate) -> str:
    parts = [template.id, template.name, template.category, template.economy or '']
    parts.extend(str(value) for value in (template.stat_effects or {}).values() if isinstance(value, str))
    for unlock in (template.stat_effects or {}).get('unlocks', []) or []:
        if isinstance(unlock, dict):
            parts.extend(str(value) for value in unlock.values())
    return ' '.join(parts).lower()


def facility_matches_economy(template: FacilityTemplate, economy: str) -> bool:
    if template.economy == economy:
        return True
    needle = economy.lower().replace(' ', '')
    haystack = _text(template).replace(' ', '')
    return needle in haystack


def find_primary_port_templates(catalogue: dict[str, FacilityTemplate]) -> list[FacilityTemplate]:
    ports = [template for template in catalogue.values() if template.is_port]
    return sorted(ports, key=lambda t: (t.tier, t.yellow_cp_cost + t.green_cp_cost, t.name, t.id))


def find_support_by_economy(
    catalogue: dict[str, FacilityTemplate],
    economies: Iterable[str],
    *,
    allow_estimated_data: bool = True,
) -> list[FacilityTemplate]:
    wanted = [economy for economy in economies if economy]
    supports = [
        template
        for template in catalogue.values()
        if template.is_support_facility
        and (allow_estimated_data or template.data_confidence != 'estimated')
        and any(facility_matches_economy(template, economy) for economy in wanted)
    ]
    return sorted(supports, key=lambda t: (t.tier, -(t.yellow_cp_generated + t.green_cp_generated), t.name, t.id))


def find_service_unlock_support(
    catalogue: dict[str, FacilityTemplate],
    *,
    allow_estimated_data: bool = True,
) -> Optional[FacilityTemplate]:
    supports = [
        template
        for template in catalogue.values()
        if template.is_support_facility
        and (allow_estimated_data or template.data_confidence != 'estimated')
        and bool((template.stat_effects or {}).get('unlocks'))
    ]
    supports.sort(key=lambda t: (t.tier, t.name, t.id))
    return supports[0] if supports else None


def select_port_template(
    catalogue: dict[str, FacilityTemplate],
    strategy: str,
    *,
    allow_estimated_data: bool = True,
) -> Optional[FacilityTemplate]:
    ports = [p for p in find_primary_port_templates(catalogue) if allow_estimated_data or p.data_confidence != 'estimated']
    if not ports:
        return None
    if strategy != 'primary_port_bootstrap':
        strategic_ports = [port for port in ports if not is_colony_ship(port)]
        if strategic_ports:
            ports = strategic_ports
    if strategy == 'low_cp':
        return min(ports, key=lambda p: (p.yellow_cp_cost + p.green_cp_cost, p.tier, p.name, p.id))
    if strategy in {'services_aware', 'balanced', 'balanced_expansion', 'support_body'}:
        return min(ports, key=lambda p: (abs(p.tier - 2), p.yellow_cp_cost + p.green_cp_cost, p.name, p.id))
    if strategy == 'main_station':
        return max(ports, key=lambda p: (p.tier, -(p.yellow_cp_cost + p.green_cp_cost), p.name, p.id))
    return ports[0]


def select_support_templates(
    catalogue: dict[str, FacilityTemplate],
    rule: ArchetypeRule,
    strategy: str,
    *,
    allow_estimated_data: bool = True,
) -> list[FacilityTemplate]:
    economies = rule.expected_economies
    supports = find_support_by_economy(catalogue, economies, allow_estimated_data=allow_estimated_data)
    if not supports and rule.support_keywords:
        keywords = [keyword.lower() for keyword in rule.support_keywords]
        supports = [
            template
            for template in catalogue.values()
            if template.is_support_facility
            and (allow_estimated_data or template.data_confidence != 'estimated')
            and any(keyword in _text(template) for keyword in keywords)
        ]
        supports.sort(key=lambda t: (t.tier, t.name, t.id))

    if strategy == 'low_cp':
        supports.sort(key=lambda t: (t.yellow_cp_cost + t.green_cp_cost, t.tier, t.name, t.id))
    elif strategy == 'pure' and rule.primary_economies:
        primary = find_support_by_economy(catalogue, rule.primary_economies, allow_estimated_data=allow_estimated_data)
        secondary = [support for support in supports if support not in primary]
        supports = [*primary, *secondary]
    return supports
