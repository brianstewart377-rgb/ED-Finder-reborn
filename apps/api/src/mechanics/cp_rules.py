"""Construction-point port classification and Mega Guide cost rules."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from edfinder_api.domain.facilities import FacilityTemplate


ESCALATING_PORT_TIER_BY_ID = {
    'coriolis_station': 2,
    'asteroid_base': 2,
    'orbis_t3': 3,
    'ocellus_station': 3,
    # Treat Dodecahedron as T3 like Orbis/Ocellus; guide v2.3.0 does not name it.
    'dodecahedron_t3': 3,
    'planetary_port': 3,
}

# T1 orbital/planetary outposts and T2 settlements pay their catalogue costs
# and never advance the shared T2/T3 port escalation count. Keep these explicit
# exclusions as a guard; settlements are support facilities, not ports.
NON_ESCALATING_PORT_IDS = frozenset({
    'orbital_outpost_civilian',
    'orbital_outpost_commercial',
    'orbital_outpost_criminal',
    'orbital_outpost_industrial',
    'orbital_outpost_military',
    'orbital_outpost_scientific',
    'surface_planetary_port_outpost_civilian',
    'surface_planetary_port_outpost_industrial',
    'surface_planetary_port_outpost_scientific',
    'surface_settlement_agriculture_t2_l',
    'surface_settlement_extraction_t2_l',
    'surface_settlement_industrial_t2_l',
    'surface_settlement_military_t2_l',
    'surface_settlement_research_bio_t2_l',
    'surface_settlement_tourism_t2_l',
})

T2_PORT_MIN_YELLOW_COST = 3
T2_PORT_YELLOW_INCREMENT = 2
T3_PORT_GREEN_INCREMENT = 6

LATE_T3_BUILD_ORDER_THRESHOLD = 3

CP_COMPLEXITY_THRESHOLDS = [
    (90, 'expert'),
    (65, 'advanced'),
    (40, 'moderate'),
    (15, 'simple'),
    (0, 'trivial'),
]


def escalating_port_tier(facility: FacilityTemplate) -> int | None:
    """Classify catalogue ports, with a tier fallback for synthetic templates."""
    if facility.id in NON_ESCALATING_PORT_IDS:
        return None
    if facility.id in ESCALATING_PORT_TIER_BY_ID:
        return ESCALATING_PORT_TIER_BY_ID[facility.id]
    if facility.is_port and facility.tier in {2, 3}:
        return facility.tier
    return None


def t2_port_yellow_cost(counted_port_number: int) -> int:
    """Yellow cost of a T2 port at the shared 1-based counted port number.

    Mega Guide v2.3.0 PDF p27-28; CRE CL-0013..CL-0024, CR-0010..CR-0012.
    """
    # The p28 T3,T3,T2,T2 example totals 16 yellow; the table gives 5+7=12.
    # Keep the table from the third counted port onward: not verified in-game.
    return max(T2_PORT_MIN_YELLOW_COST, T2_PORT_YELLOW_INCREMENT * counted_port_number - 1)


def t3_port_green_cost(counted_port_number: int) -> int:
    """Green cost of a T3 port at the shared 1-based counted port number.

    Mega Guide v2.3.0 PDF p27-28; CRE CL-0013..CL-0024, CR-0010..CR-0012.
    """
    return T3_PORT_GREEN_INCREMENT * max(1, counted_port_number - 1)
