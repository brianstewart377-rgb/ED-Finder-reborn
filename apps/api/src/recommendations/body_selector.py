"""Archetype-specific body selection for recommended colony builds."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from edfinder_api.domain.colonisation_rules import BodyEconomyProfile, TargetProfile, profile_body
from edfinder_api.mechanics.scoring_rules import BODY_SELECTOR_BASE_WEIGHTS, BODY_SELECTOR_POINTS


@dataclass(frozen=True)
class BodyCandidate:
    profile: BodyEconomyProfile
    score: float
    reason: str
    caveats: list[str]

    @property
    def body_id(self) -> Optional[str]:
        return self.profile.body_id

    @property
    def body_name(self) -> Optional[str]:
        return self.profile.body_name


def build_body_candidates(rows: list[dict[str, Any]]) -> list[BodyCandidate]:
    """Build unscored candidates from raw DB rows."""
    candidates: list[BodyCandidate] = []
    for row in rows:
        profile = profile_body(row)
        candidates.append(BodyCandidate(profile=profile, score=0.0, reason='', caveats=profile.caveats))
    return candidates


def select_body_candidates(
    archetype: str,
    target: TargetProfile,
    rows: list[dict[str, Any]],
    *,
    slot_confidence: Optional[float] = None,
    total_slots: int = 0,
    limit: int = 3,
) -> list[BodyCandidate]:
    if not target.supported:
        return []

    raw = build_body_candidates(rows)
    scored: list[BodyCandidate] = []
    for candidate in raw:
        score, reason = _score(archetype, target, candidate.profile, slot_confidence, total_slots)
        if score > 0:
            scored.append(BodyCandidate(
                profile=candidate.profile,
                score=round(score, 2),
                reason=reason,
                caveats=candidate.profile.caveats,
            ))
    return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]


def _score(
    archetype: str,
    target: TargetProfile,
    profile: BodyEconomyProfile,
    slot_confidence: Optional[float],
    total_slots: int,
) -> tuple[float, str]:
    economies = set(profile.base_economies) | set(profile.modifier_economies)
    tags = set(profile.strategic_tags)
    reasons: list[str] = []
    score = (
        profile.confidence * BODY_SELECTOR_BASE_WEIGHTS['confidence']
        + profile.purity * BODY_SELECTOR_BASE_WEIGHTS['purity']
    )

    def add(points: float, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(reason)

    if archetype == 'refinery_industrial':
        points = BODY_SELECTOR_POINTS['refinery_industrial']
        if profile.subtype.lower().find('rocky ice') >= 0:
            add(points['rocky_ice'], 'Rocky-Ice supports an Industrial + Refinery hybrid.')
        if 'Refinery' in profile.base_economies and 'Industrial' not in profile.base_economies:
            add(points['clean_rocky'], 'Clean Rocky body supports Refinery without ELW-style mixed contamination.')
        if 'Industrial' in economies:
            add(points['industrial_support'], 'Industrial support opportunity is present.')
        if 'Extraction' in economies:
            score -= points['extraction_penalty']
            reasons.append('Extraction is present, so keep it tertiary for this archetype.')
        if 'elw_mixed' in tags:
            score -= points['elw_penalty']
            reasons.append('ELW is mixed economy and not a Refinery/Industrial anchor.')
    elif archetype == 'extraction_refinery':
        points = BODY_SELECTOR_POINTS['extraction_refinery']
        if 'Extraction' in profile.base_economies:
            add(points['extraction_base'], 'Body supports Extraction as a base economy.')
        if 'Refinery' in economies:
            add(points['refinery_pairing'], 'Refinery pairing opportunity is present.')
        if 'ringed' in tags or 'geological' in tags:
            add(points['ring_or_geo'], 'Ring or geological signals reinforce the mining stack.')
        if 'elw_mixed' in tags:
            score -= points['elw_penalty']
            reasons.append('ELW rarity is not relevant to Extraction/Refinery selection.')
    elif archetype == 'agriculture_terraforming':
        points = BODY_SELECTOR_POINTS['agriculture_terraforming']
        has_target_signal = False
        if 'Agriculture' in economies:
            add(points['agriculture'], 'Agriculture is supported by the body profile.')
            has_target_signal = True
        if 'terraforming_candidate' in tags or 'terraforming_pressure' in tags:
            add(points['terraforming_tag'], 'Terraforming is represented as a strategic planning tag.')
            has_target_signal = True
        if 'elw_mixed' in tags:
            add(points['elw_mixed'], 'ELW has strong Agriculture value but remains mixed economy.')
            has_target_signal = True
        if 'Industrial' in economies:
            score -= points['industrial_penalty']
            reasons.append('Industrial pressure is non-target for Agriculture/Terraforming.')
        if not has_target_signal:
            return 0.0, ''
    elif archetype == 'hitech_tourism':
        points = BODY_SELECTOR_POINTS['hitech_tourism']
        if 'HighTech' in economies:
            add(points['hitech'], 'HighTech value is present.')
        if 'Tourism' in economies:
            add(points['tourism'], 'Tourism value is present.')
        if 'exotic' in tags or 'elw_mixed' in tags:
            add(points['exotic_or_elw'], 'Exotic or ELW mixed value supports prestige planning.')
    elif archetype == 'military_industrial':
        points = BODY_SELECTOR_POINTS['military_industrial']
        if 'Military' in economies:
            add(points['military'], 'Military value is present.')
        if 'Industrial' in economies:
            add(points['industrial'], 'Industrial support opportunity is present.')
        if 'elw_mixed' in tags:
            add(points['elw_mixed'], 'ELW contributes mixed Military value, with caveats.')
        if 'landable' in tags:
            add(points['landable'], 'Landable body supports surface military/industrial placement.')
    elif archetype == 'expansion_capital':
        points = BODY_SELECTOR_POINTS['expansion_capital']
        add(
            min(points['slot_capacity_cap'], total_slots * points['slot_capacity_weight']),
            'Expansion planning prioritises total slot capacity.',
        )
        if slot_confidence is not None:
            add(slot_confidence * points['slot_confidence_weight'], 'Slot confidence supports a higher-capacity plan.')
        add(
            len(profile.base_economies) * points['base_diversity_weight']
            + len(profile.modifier_economies) * points['modifier_diversity_weight'],
            'Body diversity supports flexible expansion.',
        )
    elif archetype == 'flexible_multirole':
        points = BODY_SELECTOR_POINTS['flexible_multirole']
        add(
            len(profile.base_economies) * points['base_diversity_weight']
            + len(profile.modifier_economies) * points['modifier_diversity_weight'],
            'Mixed economy options support flexible multirole planning.',
        )
        if profile.purity >= points['purity_threshold']:
            add(points['purity_bonus'], 'Low contamination helps keep options open.')
    else:
        return 0.0, ''

    if not reasons:
        return 0.0, ''
    return max(0.0, score), reasons[0]
