"""Planner facilities from the bundled catalogue, independent of startup state."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from types import MappingProxyType

from edfinder_api.domain import facilities


PREREQUISITE_FACILITY_IDS: dict[str, frozenset[str]] = {
    'Settlement - Research Bio': frozenset({
        'surface_settlement_research_bio_t1_m',
        'surface_settlement_research_bio_t1_s',
        'surface_settlement_research_bio_t2_l',
    }),
    'Settlement - Military': frozenset({
        'surface_settlement_military_t1_m',
        'surface_settlement_military_t1_s',
        'surface_settlement_military_t2_l',
    }),
    'Installation - Relay Station': frozenset({'orbital_installation_relay_station'}),
    'Satellite ➔ Tourism Settlement': frozenset({
        'surface_settlement_tourism_t1_m',
        'surface_settlement_tourism_t1_s',
        'surface_settlement_tourism_t2_l',
    }),
    'Settlement - Extraction': frozenset({
        'surface_settlement_extraction_t1_m',
        'surface_settlement_extraction_t1_s',
        'surface_settlement_extraction_t2_l',
    }),
    'Settlement - Agriculture': frozenset({
        'surface_settlement_agriculture_t1_m',
        'surface_settlement_agriculture_t1_s',
        'surface_settlement_agriculture_t2_l',
    }),
    'Installation - Space Farm': frozenset({'orbital_installation_space_farm'}),
    'Installation - Mining Outpost': frozenset({'orbital_installation_mining_outpost'}),
    'Mil. Settlement ➔ Mil. Installation': frozenset({'orbital_installation_military'}),
    'Installation - Comm Station': frozenset({'orbital_installation_comm_station'}),
    'Installation - Satellite': frozenset({'orbital_installation_satellite'}),
}


@cache
def _catalogue() -> tuple[
    Mapping[str, facilities.FacilityTemplate],
    dict[str, tuple[frozenset[str], ...]],
]:
    data = json.loads(facilities._BUNDLED_CATALOGUE_PATH.read_text(encoding='utf-8'))
    templates = {
        facility_id: template
        for facility_id, template in facilities.templates_from_json_data(data).items()
        if not facilities.is_colony_ship(template)
    }
    prerequisites = {}
    for facility_id, template in templates.items():
        # Every description must be reviewed, including the direct end of chains.
        prerequisites[facility_id] = tuple(
            PREREQUISITE_FACILITY_IDS[item['description']]
            for item in template.prerequisites
        )
    return MappingProxyType(templates), prerequisites


def planner_facilities() -> Mapping[str, facilities.FacilityTemplate]:
    """Return the planner's read-only, bundled facility mapping."""
    return _catalogue()[0]


def facility(facility_id: str) -> facilities.FacilityTemplate:
    """Look up a buildable facility, raising KeyError for unknown ids."""
    return planner_facilities()[facility_id]


def prerequisites_for(facility_id: str) -> tuple[frozenset[str], ...]:
    """Return requirements that all apply; any id within each satisfies it."""
    return _catalogue()[1][facility_id]
