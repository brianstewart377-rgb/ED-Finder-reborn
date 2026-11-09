"""Strong/weak link mechanics constants."""
from __future__ import annotations


STRONG_LINK_BY_TIER: dict[int, float] = {
    1: 0.4,
    2: 0.8,
    3: 1.2,
}

WEAK_LINK_STRENGTH = 0.05
MIN_STRONG_LINK_MODIFIER = 0.1

LINK_MODIFIER_DELTAS = {
    'agriculture_elw': 0.25,
    'agriculture_water_world': 0.18,
    'agriculture_bio': 0.16,
    'agriculture_terraformable': 0.10,
    'agriculture_icy_malus': -0.45,
    'agriculture_tidally_locked_malus': -0.25,
    'extraction_geo': 0.20,
    'extraction_rich_reserves': 0.22,
    'extraction_poor_reserves_malus': -0.55,
    'hitech_exotic_body': 0.18,
    'hitech_geo': 0.08,
    'hitech_bio': 0.08,
    'tourism_exotic': 0.22,
    'tourism_geo': 0.08,
    'tourism_bio': 0.08,
    'industrial_refinery_rich_reserves': 0.18,
}
