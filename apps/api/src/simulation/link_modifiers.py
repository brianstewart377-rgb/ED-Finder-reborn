"""Strong-link modifier rules for colony topology simulation.

Weak links are deliberately excluded here: Frontier notes say weak links are
fixed-strength cross-body influences and are not modified.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edfinder_api.mechanics.link_rules import LINK_MODIFIER_DELTAS, MIN_STRONG_LINK_MODIFIER, STRONG_LINK_BY_TIER


@dataclass(frozen=True)
class StrongLinkModifier:
    base_value: float
    multiplier: float
    value: float
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


def modified_strong_link_value(
    *,
    source_tier: int,
    economy: str | None,
    body_profile: dict[str, Any] | None = None,
) -> StrongLinkModifier:
    base = STRONG_LINK_BY_TIER.get(source_tier, STRONG_LINK_BY_TIER[1])
    profile = body_profile or {}
    multiplier, reasons, caveats, assumptions = _modifier_components(economy, profile)
    value = max(MIN_STRONG_LINK_MODIFIER, base * multiplier)
    return StrongLinkModifier(
        base_value=round(base, 3),
        multiplier=round(multiplier, 3),
        value=round(value, 3),
        reasons=reasons,
        caveats=caveats,
        assumptions=assumptions,
    )


def _modifier_components(
    economy: str | None,
    profile: dict[str, Any],
) -> tuple[float, list[str], list[str], list[str]]:
    if not economy:
        return 1.0, [], [], []

    multiplier = 1.0
    reasons: list[str] = []
    caveats: list[str] = []
    assumptions: list[str] = []
    subtype = str(profile.get('subtype') or profile.get('planet_class') or profile.get('body_type') or '').lower()
    tags = {str(item) for item in profile.get('strategic_tags') or []}
    base_economies = {str(item) for item in profile.get('base_economies') or []}
    reserves = str(profile.get('reserve_level') or profile.get('reserves') or '').lower()

    def add(delta: float, reason: str) -> None:
        nonlocal multiplier
        multiplier += delta
        reasons.append(reason)

    is_elw = 'elw_mixed' in tags or 'earth-like' in subtype or 'earthlike' in subtype
    is_water = 'water world' in subtype
    is_ammonia = 'ammonia' in subtype or 'exotic' in tags and 'Tourism' in base_economies
    has_bio = 'bio' in tags or bool(profile.get('has_bio')) or int(profile.get('bio_signal_count') or 0) > 0
    has_geo = 'geological' in tags or bool(profile.get('has_geo')) or int(profile.get('geo_signal_count') or 0) > 0 or bool(profile.get('volcanism'))
    terraformable = 'terraforming_candidate' in tags or bool(profile.get('is_terraformable'))
    icy = 'icy' in subtype
    tidally_locked = bool(profile.get('is_tidally_locked') or profile.get('tidally_locked'))
    exotic_star = any(item in subtype for item in ('black hole', 'white dwarf', 'neutron'))
    rich_reserves = any(item in reserves for item in ('major', 'pristine'))
    poor_reserves = any(item in reserves for item in ('depleted', 'low'))

    if economy == 'Agriculture':
        if is_elw:
            add(LINK_MODIFIER_DELTAS['agriculture_elw'], 'ELW agriculture strong-link boost.')
        if is_water:
            add(LINK_MODIFIER_DELTAS['agriculture_water_world'], 'Water World agriculture strong-link boost.')
        if has_bio:
            add(LINK_MODIFIER_DELTAS['agriculture_bio'], 'Organic/bio signal agriculture boost.')
        if terraformable:
            add(LINK_MODIFIER_DELTAS['agriculture_terraformable'], 'Terraformable body agriculture boost.')
            caveats.append('Terraformable strong-link boost is low-confidence and may be bugged in-game.')
            assumptions.append('Terraformable modifier applied with low confidence.')
        if icy:
            add(LINK_MODIFIER_DELTAS['agriculture_icy_malus'], 'Icy body agriculture malus.')
        if tidally_locked:
            add(LINK_MODIFIER_DELTAS['agriculture_tidally_locked_malus'], 'Tidally locked agriculture malus.')
    elif economy == 'Extraction':
        if has_geo:
            add(LINK_MODIFIER_DELTAS['extraction_geo'], 'Volcanism/geological extraction boost.')
        if rich_reserves:
            add(LINK_MODIFIER_DELTAS['extraction_rich_reserves'], 'Major/pristine reserve extraction boost.')
        if poor_reserves:
            add(LINK_MODIFIER_DELTAS['extraction_poor_reserves_malus'], 'Low/depleted reserve extraction malus.')
    elif economy == 'HighTech':
        if is_elw or is_water or is_ammonia:
            add(LINK_MODIFIER_DELTAS['hitech_exotic_body'], 'Exotic/high-value body HighTech boost.')
        if has_geo:
            add(LINK_MODIFIER_DELTAS['hitech_geo'], 'Geological HighTech support boost.')
        if has_bio:
            add(LINK_MODIFIER_DELTAS['hitech_bio'], 'Biological HighTech support boost.')
    elif economy == 'Tourism':
        if is_elw or is_water or is_ammonia or exotic_star:
            add(LINK_MODIFIER_DELTAS['tourism_exotic'], 'Exotic body/star Tourism boost.')
        if has_geo:
            add(LINK_MODIFIER_DELTAS['tourism_geo'], 'Geological Tourism interest boost.')
        if has_bio:
            add(LINK_MODIFIER_DELTAS['tourism_bio'], 'Biological Tourism interest boost.')
    elif economy in {'Industrial', 'Refinery'}:
        if rich_reserves:
            add(LINK_MODIFIER_DELTAS['industrial_refinery_rich_reserves'], 'Major/pristine reserve Industrial/Refinery boost.')

    return max(0.0, multiplier), reasons, caveats, assumptions
