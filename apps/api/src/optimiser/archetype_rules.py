"""Archetype guidance for bounded Stage 5A candidate generation.

These rules guide candidate construction only. The Simulation Preview engine
remains the source of truth for scoring, CP, economy, and service outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass, field


STAGE5A_STRATEGIES = (
    'main_station',
    'balanced_expansion',
    'industrial_refinery',
    'tourism_agriculture',
    'military_security',
    'support_body',
    'primary_port_bootstrap',
    # Legacy strategy names are kept so older tests/callers that construct
    # candidates directly still rank and render predictably.
    'balanced',
    'pure',
    'services_aware',
    'low_cp',
    'flexible_multirole',
)


@dataclass(frozen=True)
class ArchetypeRule:
    key: str
    label: str
    primary_economies: list[str] = field(default_factory=list)
    secondary_economies: list[str] = field(default_factory=list)
    avoid_economies: list[str] = field(default_factory=list)
    strategies: tuple[str, ...] = STAGE5A_STRATEGIES
    support_economies: list[str] = field(default_factory=list)
    support_keywords: list[str] = field(default_factory=list)
    strategic_tags: list[str] = field(default_factory=list)

    @property
    def expected_economies(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for economy in [*self.primary_economies, *self.secondary_economies, *self.support_economies]:
            if economy and economy not in seen:
                seen.add(economy)
                result.append(economy)
        return result


ARCHETYPE_RULES: dict[str, ArchetypeRule] = {
    'refinery_industrial': ArchetypeRule(
        key='refinery_industrial',
        label='Refinery / Industrial',
        primary_economies=['Refinery'],
        secondary_economies=['Industrial'],
        avoid_economies=['Extraction', 'Agriculture', 'HighTech', 'Tourism', 'Military'],
        support_economies=['Refinery', 'Industrial'],
        support_keywords=['refinery', 'industrial'],
    ),
    'extraction_refinery': ArchetypeRule(
        key='extraction_refinery',
        label='Extraction / Refinery',
        primary_economies=['Extraction'],
        secondary_economies=['Refinery'],
        support_economies=['Extraction', 'Refinery'],
        support_keywords=['extraction', 'refinery', 'mining'],
        strategic_tags=['ringed', 'geological'],
    ),
    'agriculture_terraforming': ArchetypeRule(
        key='agriculture_terraforming',
        label='Agriculture / Terraforming',
        primary_economies=['Agriculture'],
        avoid_economies=['Extraction', 'Industrial'],
        support_economies=['Agriculture'],
        support_keywords=['agriculture', 'farm', 'terraform'],
        strategic_tags=['terraforming_candidate'],
    ),
    'hitech_tourism': ArchetypeRule(
        key='hitech_tourism',
        label='HighTech / Tourism',
        primary_economies=['HighTech'],
        secondary_economies=['Tourism'],
        support_economies=['HighTech', 'Tourism'],
        support_keywords=['hightech', 'high tech', 'tourism', 'tourist'],
        strategic_tags=['exotic', 'elw_mixed'],
    ),
    'military_industrial': ArchetypeRule(
        key='military_industrial',
        label='Military / Industrial',
        primary_economies=['Military'],
        secondary_economies=['Industrial'],
        support_economies=['Military', 'Industrial'],
        support_keywords=['military', 'industrial'],
        strategic_tags=['elw_mixed', 'landable'],
    ),
    'trade_logistics': ArchetypeRule(
        key='trade_logistics',
        label='Trade / Logistics',
        primary_economies=[],
        secondary_economies=['HighTech', 'Industrial'],
        support_economies=['HighTech', 'Industrial', 'Refinery'],
        support_keywords=['trade', 'logistics', 'market', 'relay'],
    ),
    'flexible_multirole': ArchetypeRule(
        key='flexible_multirole',
        label='Flexible Multirole',
        primary_economies=[],
        secondary_economies=['Agriculture', 'Refinery', 'Industrial', 'HighTech', 'Military', 'Tourism', 'Extraction'],
        support_economies=['Agriculture', 'Refinery', 'Industrial', 'HighTech', 'Military', 'Tourism', 'Extraction'],
        support_keywords=['support', 'relay', 'hub'],
        strategic_tags=['body_diversity'],
    ),
}


def resolve_archetype_rule(target_archetype: str | None) -> tuple[ArchetypeRule, list[str]]:
    key = target_archetype or 'flexible_multirole'
    rule = ARCHETYPE_RULES.get(key)
    if rule:
        return rule, []
    fallback = ARCHETYPE_RULES['flexible_multirole']
    return fallback, [f'Unknown archetype {key}; using flexible_multirole fallback.']
