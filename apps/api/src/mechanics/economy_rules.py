"""Economy inheritance and stack-scoring mechanics constants."""
from __future__ import annotations


CONTAMINATION_ECONOMIES = {'Colony', 'Prison', 'Damaged', 'Rescue', 'Repair', 'None'}

PRIMARY_BASE_WEIGHT = 1.0
SECONDARY_BASE_WEIGHT = 0.8
MODIFIER_ECONOMY_WEIGHT = 0.45

MIXED_TOLERANT_ARCHETYPES = {'hitech_tourism', 'flexible_multirole', 'expansion_capital'}

ECONOMY_STACK_SCORE_WEIGHTS = {
    'purity': 0.35,
    'archetype_fit': 0.65,
}

ECONOMY_STACK_FIT_SCORES = {
    'flexible': 74.0,
    'single_excellent': 92.0,
    'single_partial': 70.0,
    'single_poor': 35.0,
    'pair_excellent': 96.0,
    'pair_good_flipped': 84.0,
    'pair_partial': 58.0,
    'pair_poor': 32.0,
}

PURITY_BASE_SCORE = 92.0
LOW_PURITY_THRESHOLD = 0.6
LOW_PURITY_REFERENCE = 0.7
TERTIARY_PRESSURE_THRESHOLD = 15.0
TERTIARY_HEAVY_PRESSURE_THRESHOLD = 18.0
TERTIARY_HIGH_RISK_THRESHOLD = 24.0
BROAD_STACK_THRESHOLD = 8.0
BROAD_STACK_ALLOWED_COUNT = 3
BROAD_STACK_PENALTY_PER_ECONOMY = 7.0
TERTIARY_LIGHT_PENALTY = 9.0
TERTIARY_HEAVY_PENALTY = 16.0
LOW_PURITY_SPECIALISED_PENALTY = 10.0
ELW_REFINERY_INDUSTRIAL_PENALTY = 12.0

BODY_PROFILE_CONFIDENCE_DEFAULT = 0.7
PROFILE_FULL_PURITY = 1.0
PROFILE_FULL_CONFIDENCE = 1.0

FACILITY_ECONOMY_WEIGHTS = {
    'specialised_port': 2.0,
    'colony_port': 1.2,
    'support_or_default': 1.0,
}
