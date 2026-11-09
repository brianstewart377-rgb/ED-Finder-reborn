"""Regional role classification from raw colonisation-distance metrics."""
from __future__ import annotations

from typing import Optional

from edfinder_api.mechanics.regional_rules import REGIONAL_ROLE_THRESHOLDS


def classify_regional_role(
    *,
    nearest_distance_ly: Optional[float],
    within_25: int,
    within_50: int,
    within_100: int,
    within_250: int,
) -> str:
    t = REGIONAL_ROLE_THRESHOLDS
    if nearest_distance_ly is None:
        return 'unknown'
    if nearest_distance_ly > t['isolated_frontier_nearest_min'] and within_100 == 0:
        return 'isolated_frontier'
    if within_50 >= t['oversaturated_within_50_min'] or within_100 >= t['oversaturated_within_100_min']:
        return 'oversaturated_region'
    if nearest_distance_ly < t['dense_nearest_max'] and within_50 >= t['dense_within_50_min']:
        return 'dense_developed_cluster'
    if t['emerging_nearest_min'] <= nearest_distance_ly <= t['emerging_nearest_max'] and t['emerging_within_100_min'] <= within_100 <= t['emerging_within_100_max']:
        return 'emerging_cluster'
    if t['frontier_nearest_min'] <= nearest_distance_ly <= t['frontier_nearest_max'] and within_100 <= t['frontier_within_100_max']:
        return 'frontier_hub'
    if (
        t['bridge_nearest_min'] <= nearest_distance_ly <= t['bridge_nearest_max']
        and t['bridge_within_100_min'] <= within_100 <= t['bridge_within_100_max']
        and t['bridge_within_250_min'] <= within_250 <= t['bridge_within_250_max']
        and within_50 <= t['bridge_within_50_max']
    ):
        return 'bridge_system'
    return 'unknown'
