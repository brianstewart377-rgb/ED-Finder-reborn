"""Simulation and recommendation scoring constants."""
from __future__ import annotations


SIMULATION_FINAL_SCORE_WEIGHTS = {
    'composition': 0.55,
    'buildability': 0.35,
    'cp_health': 0.10,
}

RANK_SCORE_WEIGHTS = {
    'simulation_score': 0.55,
    'economy_stack_score': 0.25,
    'buildability_score': 0.15,
    'confidence_bonus': 1.0,
}

REGIONAL_RECOMMENDATION_WEIGHT = 0.07
SIMULATION_RECOMMENDATION_WEIGHT = 0.93

ADVANCED_PLAN_MIN_SLOT_CONFIDENCE = 0.6
ADVANCED_PLAN_MIN_TOTAL_SLOTS = 3
ADVANCED_PLAN_MIN_BODY_SCORE = 40
MAX_RECOMMENDED_DRAFTS = 3

BODY_SELECTOR_BASE_WEIGHTS = {
    'confidence': 10.0,
    'purity': 12.0,
}

BODY_SELECTOR_POINTS = {
    'refinery_industrial': {
        'rocky_ice': 34.0,
        'clean_rocky': 26.0,
        'industrial_support': 14.0,
        'extraction_penalty': 10.0,
        'elw_penalty': 30.0,
    },
    'extraction_refinery': {
        'extraction_base': 30.0,
        'refinery_pairing': 18.0,
        'ring_or_geo': 18.0,
        'elw_penalty': 34.0,
    },
    'agriculture_terraforming': {
        'agriculture': 30.0,
        'terraforming_tag': 28.0,
        'elw_mixed': 12.0,
        'industrial_penalty': 12.0,
    },
    'hitech_tourism': {
        'hitech': 26.0,
        'tourism': 26.0,
        'exotic_or_elw': 18.0,
    },
    'military_industrial': {
        'military': 26.0,
        'industrial': 22.0,
        'elw_mixed': 12.0,
        'landable': 8.0,
    },
    'expansion_capital': {
        'slot_capacity_cap': 30.0,
        'slot_capacity_weight': 2.5,
        'slot_confidence_weight': 20.0,
        'base_diversity_weight': 6.0,
        'modifier_diversity_weight': 4.0,
    },
    'flexible_multirole': {
        'base_diversity_weight': 8.0,
        'modifier_diversity_weight': 6.0,
        'purity_threshold': 0.75,
        'purity_bonus': 8.0,
    },
}

WARNING_PENALTY_PER_WARNING = 2.0
MAX_WARNING_PENALTY = 12.0

COMPLEXITY_PENALTY = {
    'simple': 0.0,
    'moderate': 2.0,
    'advanced': 5.0,
    'expert': 9.0,
}

CONFIDENCE_LOW_THRESHOLD = 0.65

BUILDABILITY_ESTIMATED_TOPOLOGY_SCORE = 68.0
BUILDABILITY_SLOT_FIT_SCORE = 88.0
BUILDABILITY_OVER_SLOT_BASE_SCORE = 82.0
BUILDABILITY_OVER_SLOT_MIN_SCORE = 25.0
BUILDABILITY_OVER_SLOT_PENALTY = 18.0
BUILDABILITY_LOW_SLOT_CONFIDENCE_THRESHOLD = 0.55
BUILDABILITY_LOW_SLOT_CONFIDENCE_PENALTY = 8.0
BUILDABILITY_NEGATIVE_CP_PENALTY = 15.0

SIMULATION_CONFIDENCE_BASE = 0.86
SIMULATION_CONFIDENCE_MIN = 0.2
SIMULATION_CONFIDENCE_MAX = 0.95
MISSING_SLOT_CONFIDENCE_PENALTY = 0.18
SLOT_CONFIDENCE_BASE = 0.35
SLOT_CONFIDENCE_WEIGHT = 0.65
ESTIMATED_FACILITY_CONFIDENCE_PENALTY = 0.04
ESTIMATED_FACILITY_CONFIDENCE_MAX_PENALTY = 0.15
MISSING_BODY_PROFILE_CONFIDENCE_PENALTY = 0.06
MISSING_BODY_PROFILE_CONFIDENCE_MAX_PENALTY = 0.18
LOW_PURITY_CONFIDENCE_SCALE = 0.18
LOW_PURITY_CONFIDENCE_MAX_PENALTY = 0.12
MIXED_BASE_CONFIDENCE_PENALTY = 0.025
MIXED_BASE_CONFIDENCE_MAX_PENALTY = 0.10
MODIFIER_CONFIDENCE_PENALTY = 0.025
MODIFIER_CONFIDENCE_MAX_PENALTY = 0.08
COMPLEXITY_LOW_CONFIDENCE_THRESHOLD = 0.6
