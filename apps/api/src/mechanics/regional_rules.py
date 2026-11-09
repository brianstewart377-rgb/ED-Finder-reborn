"""Regional role and archetype-fit rule constants."""
from __future__ import annotations


REGIONAL_ROLE_THRESHOLDS = {
    'isolated_frontier_nearest_min': 150.0,
    'oversaturated_within_50_min': 12,
    'oversaturated_within_100_min': 35,
    'dense_nearest_max': 25.0,
    'dense_within_50_min': 6,
    'emerging_nearest_min': 20.0,
    'emerging_nearest_max': 80.0,
    'emerging_within_100_min': 3,
    'emerging_within_100_max': 10,
    'frontier_nearest_min': 50.0,
    'frontier_nearest_max': 150.0,
    'frontier_within_100_max': 3,
    'bridge_nearest_min': 40.0,
    'bridge_nearest_max': 160.0,
    'bridge_within_100_min': 4,
    'bridge_within_100_max': 12,
    'bridge_within_250_min': 2,
    'bridge_within_250_max': 24,
    'bridge_within_50_max': 3,
}

REGIONAL_DISTANCE_BUCKETS = (25, 50, 100, 250)

REGIONAL_SCORE_WEIGHTS = {
    'isolation_distance_scale': 180.0,
    'density_within_25': 8.0,
    'density_within_50': 4.0,
    'density_within_100': 1.5,
    'density_within_250': 0.25,
    'competition_within_25': 10.0,
    'competition_within_50': 5.0,
    'competition_extra_within_100': 2.0,
    'competition_within_100_free': 10,
    'expansion_target_distance': 90.0,
    'expansion_distance_penalty': 0.65,
    'expansion_dense_50_free': 4,
    'expansion_dense_50_penalty': 5.0,
}

REGIONAL_ARCHETYPE_ROLE_BONUSES = {
    'expansion_capital': {'frontier_hub': 12, 'bridge_system': 10, 'isolated_frontier': 4, 'oversaturated_region': -25},
    'extraction_refinery': {'isolated_frontier': 14, 'frontier_hub': 8, 'oversaturated_region': -24},
    'refinery_industrial': {'frontier_hub': 8, 'bridge_system': 7, 'oversaturated_region': -18},
    'hitech_tourism': {'dense_developed_cluster': 14, 'emerging_cluster': 10, 'isolated_frontier': -24},
    'agriculture_terraforming': {'frontier_hub': 8, 'emerging_cluster': 6, 'oversaturated_region': -14},
    'flexible_multirole': {'bridge_system': 8, 'emerging_cluster': 7, 'unknown': -8},
}

REGIONAL_ARCHETYPE_FORMULAS = {
    'refinery_base_score': 75.0,
    'agriculture_base_score': 80.0,
    'flexible_base_score': 72.0,
    'extraction_isolation_weight': 0.55,
    'extraction_low_competition_weight': 0.35,
    'refinery_target_distance': 70.0,
    'refinery_distance_penalty': 0.35,
    'refinery_low_competition_weight': 0.15,
    'hitech_density_weight': 0.55,
    'hitech_access_weight': 0.25,
    'agriculture_target_distance': 80.0,
    'agriculture_distance_penalty': 0.3,
    'agriculture_dense_50_free': 8,
    'agriculture_dense_50_penalty': 4.0,
    'flexible_density_target': 45.0,
    'flexible_density_penalty': 0.25,
}

REGIONAL_FIT_LABEL_THRESHOLDS = {
    'excellent': 85.0,
    'good': 70.0,
    'mixed': 50.0,
}
