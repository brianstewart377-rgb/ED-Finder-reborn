"""Small deterministic system strategy analysis for optimiser candidates."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any



@dataclass(frozen=True)
class SystemStrategyAnalysis:
    strongest_economies: list[str] = field(default_factory=list)
    opportunities: list[str] = field(default_factory=list)
    weak_points: list[str] = field(default_factory=list)
    sparse_data: bool = False


def analyse_system_strategy(anchors: list[Any], *, body_count: int) -> SystemStrategyAnalysis:
    economy_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    for anchor in anchors:
        if not anchor.profile:
            continue
        economy_counts.update(anchor.profile.base_economies)
        economy_counts.update(anchor.profile.modifier_economies)
        tag_counts.update(anchor.profile.strategic_tags)

    strongest = [economy for economy, _count in economy_counts.most_common(3)]
    opportunities: list[str] = []
    if strongest:
        opportunities.append(f"System body data points toward {', '.join(strongest)} pressure.")
    if tag_counts.get('terraforming_candidate'):
        opportunities.append('Terraforming candidate bodies may support agriculture or civilian economy planning.')
    if tag_counts.get('ringed') or tag_counts.get('geological'):
        opportunities.append('Ringed or geological bodies may support extraction/refinery planning.')
    if body_count >= 2:
        opportunities.append('Multiple candidate bodies allow a support-body plan instead of a single-site bootstrap.')

    weak_points: list[str] = []
    if not strongest:
        weak_points.append('No strong economy direction was found in the available body data.')
    if body_count == 0:
        weak_points.append('No local body rows are available for body-specific planning.')

    return SystemStrategyAnalysis(
        strongest_economies=strongest,
        opportunities=opportunities,
        weak_points=weak_points,
        sparse_data=body_count == 0 or not strongest,
    )
