"""Canonical colonisation mechanics used by recommendations and simulation.

The rules in this module are derived from the repo's Mega Guide notes:

* ``docs/colonisation-redesign/COLONISATION_ENGINE_REDESIGN.md``.
* the current repo body-to-economy reference notes captured during the
  Stage 25 mechanics consolidation.
* Existing importer classifiers in ``build_ratings.py`` and
  ``build_topology.py``.

TODO: If the full external Colonisation Mega Guide document is checked into
the repo later, reconcile this module against that source directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


ECONOMIES = {'Agriculture', 'Refinery', 'Industrial', 'HighTech', 'Military', 'Tourism', 'Extraction'}


@dataclass(frozen=True)
class BodyEconomyProfile:
    body_id: Optional[str]
    body_name: Optional[str]
    body_type: str
    subtype: str
    base_economies: list[str] = field(default_factory=list)
    modifier_economies: list[str] = field(default_factory=list)
    strategic_tags: list[str] = field(default_factory=list)
    purity: float = 0.7
    confidence: float = 0.55
    caveats: list[str] = field(default_factory=list)

    @property
    def primary_economy(self) -> Optional[str]:
        return self.base_economies[0] if self.base_economies else None

    def to_context_profile(self) -> dict[str, Any]:
        return {
            'body_id': self.body_id,
            'body_name': self.body_name,
            'body_type': self.body_type,
            'subtype': self.subtype,
            'base_economy': self.primary_economy,  # compatibility for older callers
            'base_economies': self.base_economies,
            'modifier_economies': self.modifier_economies,
            'strategic_tags': self.strategic_tags,
            'purity': self.purity,
            'confidence': self.confidence,
            'caveats': self.caveats,
        }


@dataclass(frozen=True)
class TargetProfile:
    key: str
    primary_economies: list[str]
    secondary_economies: list[str] = field(default_factory=list)
    strategic_tags: list[str] = field(default_factory=list)
    avoid_dominant: list[str] = field(default_factory=list)
    supported: bool = True
    warning: Optional[str] = None

    @property
    def expected_economies(self) -> list[str]:
        return [*self.primary_economies, *self.secondary_economies]


TARGET_PROFILES: dict[str, TargetProfile] = {
    'refinery_industrial': TargetProfile(
        key='refinery_industrial',
        primary_economies=['Refinery'],
        secondary_economies=['Industrial'],
        avoid_dominant=['Extraction', 'Agriculture', 'HighTech', 'Tourism', 'Military'],
    ),
    'extraction_refinery': TargetProfile(
        key='extraction_refinery',
        primary_economies=['Extraction'],
        secondary_economies=['Refinery'],
        strategic_tags=['ringed', 'geological'],
    ),
    'agriculture_terraforming': TargetProfile(
        key='agriculture_terraforming',
        primary_economies=['Agriculture'],
        strategic_tags=['terraforming_candidate'],
        avoid_dominant=['Extraction', 'Industrial'],
        warning='Terraforming is modelled as a strategic tag, not as a fake Industrial economy.',
    ),
    'hitech_tourism': TargetProfile(
        key='hitech_tourism',
        primary_economies=['HighTech'],
        secondary_economies=['Tourism'],
        strategic_tags=['exotic', 'elw_mixed'],
    ),
    'military_industrial': TargetProfile(
        key='military_industrial',
        primary_economies=['Military'],
        secondary_economies=['Industrial'],
        strategic_tags=['elw_mixed', 'landable'],
    ),
    'expansion_capital': TargetProfile(
        key='expansion_capital',
        primary_economies=[],
        strategic_tags=['slot_rich', 'body_diversity'],
    ),
    'flexible_multirole': TargetProfile(
        key='flexible_multirole',
        primary_economies=[],
        strategic_tags=['body_diversity'],
    ),
}


def get_target_profile(archetype: str) -> TargetProfile:
    profile = TARGET_PROFILES.get(archetype)
    if profile:
        return profile
    return TargetProfile(
        key=archetype,
        primary_economies=[],
        supported=False,
        warning='Recommended build rules are not implemented for this archetype yet.',
    )


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


