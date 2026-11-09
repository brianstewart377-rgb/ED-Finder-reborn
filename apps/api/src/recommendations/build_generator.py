"""Generate recommended build candidates from selected bodies and templates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from edfinder_api.domain.colonisation_rules import TargetProfile
from edfinder_api.domain.facilities import FacilityTemplate
from edfinder_api.mechanics.scoring_rules import (
    ADVANCED_PLAN_MIN_BODY_SCORE,
    ADVANCED_PLAN_MIN_SLOT_CONFIDENCE,
    ADVANCED_PLAN_MIN_TOTAL_SLOTS,
    MAX_RECOMMENDED_DRAFTS,
)
from edfinder_api.models import SimulateBuildPlacement, SimulateBuildRequest
from edfinder_api.recommendations.body_selector import BodyCandidate


@dataclass(frozen=True)
class BuildPlanDraft:
    id: str
    label: str
    summary: str
    request: SimulateBuildRequest
    body: BodyCandidate
    tradeoffs: list[str]
    mechanics_basis: list[str]


def generate_build_drafts(
    *,
    system_id64: int,
    target: TargetProfile,
    body: BodyCandidate,
    catalogue: dict[str, FacilityTemplate],
    slot_confidence: Optional[float],
    total_slots: int,
) -> tuple[list[BuildPlanDraft], list[str]]:
    warnings: list[str] = []
    if not target.supported:
        return [], [target.warning or 'Recommended build rules are not implemented for this archetype yet.']
    if not body.body_id:
        return [], ['No suitable body candidate is available for this archetype.']
    if not catalogue:
        return [], ['No facility catalogue data is available yet. Try Simulation Preview manually once templates load.']

    primary = target.primary_economies[0] if target.primary_economies else None
    secondary = target.secondary_economies[0] if target.secondary_economies else None
    if target.warning:
        warnings.append(target.warning)

    simple_ids = ['colony_ship', _support_for(catalogue, primary)]
    if secondary:
        simple_ids.append(_support_for(catalogue, secondary))
    elif primary:
        simple_ids.append(_support_for(catalogue, primary, offset=1))

    balanced_ids = [
        'colony_ship',
        _support_for(catalogue, primary),
        _support_for(catalogue, primary, offset=1),
        _preferred_t2_port(catalogue, target, body),
    ]
    if secondary:
        balanced_ids.append(_support_for(catalogue, secondary))

    drafts: list[BuildPlanDraft] = []
    for plan_id, label, ids, tradeoffs in [
        ('simple', 'Simple recommended build', simple_ids, ['Lower ceiling than larger plans, but easier to verify in-game.']),
        ('balanced', 'Balanced recommended build', balanced_ids, ['Build order matters; preview before swapping support facilities.']),
    ]:
        placements = _placements_for(catalogue, body.body_id, ids)
        if placements:
            drafts.append(BuildPlanDraft(
                id=plan_id,
                label=label,
                summary=_summary(label, target, body),
                request=SimulateBuildRequest(system_id64=system_id64, target_archetype=target.key, placements=placements),
                body=body,
                tradeoffs=tradeoffs,
                mechanics_basis=_mechanics_basis(target, body),
            ))

    if _can_generate_advanced(slot_confidence, total_slots, body) and 'orbis_t3' in catalogue:
        advanced_ids = [
            'colony_ship',
            _support_for(catalogue, primary),
            _support_for(catalogue, primary, offset=1),
            _support_for(catalogue, secondary),
            _preferred_t2_port(catalogue, target, body),
            'orbis_t3',
        ]
        placements = _placements_for(catalogue, body.body_id, advanced_ids)
        if placements:
            drafts.append(BuildPlanDraft(
                id='advanced',
                label='Advanced high-capacity build',
                summary=_summary('Advanced high-capacity build', target, body),
                request=SimulateBuildRequest(system_id64=system_id64, target_archetype=target.key, placements=placements),
                body=body,
                tradeoffs=['Higher CP pressure; only shown when slot confidence and body suitability support it.'],
                mechanics_basis=_mechanics_basis(target, body),
            ))
    elif slot_confidence is not None and slot_confidence < ADVANCED_PLAN_MIN_SLOT_CONFIDENCE:
        warnings.append('Advanced plans are hidden because slot confidence is too low.')

    return drafts[:MAX_RECOMMENDED_DRAFTS], warnings


def _placements_for(
    catalogue: dict[str, FacilityTemplate],
    body_id: Optional[str],
    facility_ids: list[Optional[str]],
) -> list[SimulateBuildPlacement]:
    placements: list[SimulateBuildPlacement] = []
    primary_assigned = False
    for facility_id in facility_ids:
        if not facility_id or facility_id not in catalogue:
            continue
        facility = catalogue[facility_id]
        is_primary = facility.is_port and not primary_assigned
        primary_assigned = primary_assigned or is_primary
        placements.append(SimulateBuildPlacement(
            facility_template_id=facility_id,
            local_body_id=body_id,
            is_primary_port=is_primary,
            build_order=len(placements) + 1,
        ))
    return placements


def _support_for(catalogue: dict[str, FacilityTemplate], economy: Optional[str], *, offset: int = 0) -> Optional[str]:
    if not economy:
        return None
    matches = [f for f in catalogue.values() if f.economy == economy and f.is_support_facility]
    matches.sort(key=lambda f: (f.tier, f.name))
    return matches[min(offset, len(matches) - 1)].id if matches else None


def _preferred_t2_port(
    catalogue: dict[str, FacilityTemplate],
    target: TargetProfile,
    body: BodyCandidate,
) -> Optional[str]:
    if 'ringed' in body.profile.strategic_tags and 'asteroid_base' in catalogue:
        return 'asteroid_base'
    if any('landable' == tag for tag in body.profile.strategic_tags):
        if target.key in {'agriculture_terraforming', 'military_industrial'} and 'planetary_port' in catalogue:
            return 'planetary_port'
    return 'coriolis_station' if 'coriolis_station' in catalogue else 'orbis_station'


def _can_generate_advanced(slot_confidence: Optional[float], total_slots: int, body: BodyCandidate) -> bool:
    return bool(
        slot_confidence is not None
        and slot_confidence >= ADVANCED_PLAN_MIN_SLOT_CONFIDENCE
        and total_slots >= ADVANCED_PLAN_MIN_TOTAL_SLOTS
        and body.score >= ADVANCED_PLAN_MIN_BODY_SCORE
    )


def _summary(label: str, target: TargetProfile, body: BodyCandidate) -> str:
    body_label = body.body_name or f'Body {body.body_id}'
    if target.primary_economies:
        economies = ' / '.join([*target.primary_economies, *target.secondary_economies])
        return f'{label} for {economies}, anchored on {body_label}.'
    return f'{label} focused on slot capacity and flexible economy options, anchored on {body_label}.'


def _mechanics_basis(target: TargetProfile, body: BodyCandidate) -> list[str]:
    notes = [body.reason]
    if target.strategic_tags:
        notes.append(f"Target strategic tags: {', '.join(target.strategic_tags)}.")
    if body.profile.modifier_economies:
        notes.append(f"Modifier economies detected: {', '.join(body.profile.modifier_economies)}.")
    return notes
