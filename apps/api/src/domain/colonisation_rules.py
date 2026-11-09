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


def profile_body(row: dict[str, Any]) -> BodyEconomyProfile:
    """Return the Mega Guide-derived economy profile for one body row."""
    subtype = _body_subtype(row)
    body_type = str(row.get('body_type') or '').lower()
    body_id = row.get('body_id') or row.get('id')
    name = row.get('body_name') or row.get('name')
    base: list[str] = []
    modifiers: list[str] = []
    tags: list[str] = []
    caveats: list[str] = []
    confidence = float(row.get('confidence') or 0.55)

    is_ringed = _flag(row, 'is_ringed') or _flag(row, 'has_rings')
    has_bio = _flag(row, 'has_bio') or int(row.get('bio_signal_count') or 0) > 0
    has_geo = _flag(row, 'has_geo') or int(row.get('geo_signal_count') or 0) > 0 or bool(row.get('volcanism'))
    is_terraformable = _flag(row, 'is_terraformable') or 'terraform' in str(row.get('terraform_state') or row.get('terraforming_state') or '').lower()
    is_landable = _flag(row, 'is_landable')

    if is_ringed:
        tags.append('ringed')
    if has_bio:
        tags.append('bio')
    if has_geo:
        tags.append('geological')
    if is_terraformable:
        tags.append('terraforming_candidate')
    if is_landable:
        tags.append('landable')

    if 'earth-like' in subtype or 'earthlike' in subtype or _flag(row, 'is_earth_like'):
        base.extend(['Tourism', 'HighTech', 'Agriculture', 'Military'])
        tags.append('elw_mixed')
        caveats.append('ELW is mixed economy: Agriculture, HighTech, Military, and Tourism; not Industrial.')
    elif 'water world' in subtype or _flag(row, 'is_water_world'):
        base.extend(['Tourism', 'Agriculture'])
    elif 'ammonia' in subtype or _flag(row, 'is_ammonia_world'):
        base.append('Tourism')
        modifiers.append('HighTech')
        tags.append('exotic')
        caveats.append('Ammonia HighTech value is treated as exotic/supporting, not a pure base economy.')
    elif 'gas giant' in subtype:
        base.extend(['HighTech', 'Industrial'])
    elif 'black hole' in subtype or 'neutron' in subtype or 'white dwarf' in subtype or body_type == 'star':
        if 'black hole' in subtype or 'neutron' in subtype or 'white dwarf' in subtype:
            base.extend(['Tourism', 'HighTech'])
            tags.append('exotic')
    elif 'high metal content' in subtype:
        base.append('Extraction')
        if has_geo:
            modifiers.append('Industrial')
        if has_bio:
            modifiers.append('Agriculture')
            tags.append('terraforming_pressure')
        if is_terraformable:
            tags.append('terraforming_candidate')
    elif 'metal rich' in subtype or 'metal-rich' in subtype:
        base.append('Extraction')
    elif 'rocky ice' in subtype or 'rocky-ice' in subtype:
        base.extend(['Industrial', 'Refinery'])
    elif 'rocky' in subtype:
        base.append('Refinery')
        if is_ringed:
            modifiers.append('Extraction')
        if has_bio:
            modifiers.append('Agriculture')
            tags.append('terraforming_pressure')
        if has_geo:
            modifiers.extend(['Industrial', 'Extraction'])
    elif 'icy' in subtype:
        base.append('Industrial')

    base = _unique_economies(base)
    modifiers = [e for e in _unique_economies(modifiers) if e not in base]
    purity = _purity(base, modifiers)
    if not base and not modifiers:
        confidence = min(confidence, 0.35)
        caveats.append('No documented body-to-economy rule matched this body.')

    return BodyEconomyProfile(
        body_id=str(body_id) if body_id is not None else None,
        body_name=str(name) if name is not None else None,
        body_type=str(row.get('body_type') or ''),
        subtype=str(row.get('subtype') or row.get('planet_class') or ''),
        base_economies=base,
        modifier_economies=modifiers,
        strategic_tags=_unique(tags),
        purity=purity,
        confidence=max(0.2, min(0.95, confidence)),
        caveats=_unique(caveats),
    )


def _body_subtype(row: dict[str, Any]) -> str:
    return str(row.get('subtype') or row.get('planet_class') or row.get('body_type') or '').lower()


def _flag(row: dict[str, Any], key: str) -> bool:
    value = row.get(key)
    if isinstance(value, str):
        return value.lower() in {'true', 't', '1', 'yes', 'y'}
    return bool(value)


def _unique_economies(items: list[str]) -> list[str]:
    return [item for item in _unique(items) if item in ECONOMIES]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _purity(base: list[str], modifiers: list[str]) -> float:
    count = len(base) + len(modifiers)
    if count <= 1:
        return 1.0
    if count == 2:
        return 0.78
    if count == 3:
        return 0.62
    return 0.45
