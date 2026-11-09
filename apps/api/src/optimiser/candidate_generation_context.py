from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import asyncpg

from edfinder_api.domain.colonisation_rules import BodyEconomyProfile, profile_body
from edfinder_api.ingest.slot_prediction import (
    INSUFFICIENT_DATA_REASON,
    PREDICTION_DISCLAIMER,
    predict_system_slots,
)
from edfinder_api.optimiser.archetype_rules import ArchetypeRule
from edfinder_api.simulation.build_preview import PreviewContext


@dataclass(frozen=True)
class BodyAnchor:
    body_id: Optional[str]
    body_name: str
    profile: Optional[BodyEconomyProfile]
    score: float
    rationale: list[str]
    tags: list[str]


async def _get_preview_context_and_body_rows(pool: asyncpg.Pool, system_id64: int) -> tuple[PreviewContext, list[dict[str, Any]]]:
    async with pool.acquire() as conn:
        system_row = await conn.fetchrow(
            """SELECT * FROM systems WHERE id64 = $1""",
            system_id64,
        )
        if not system_row:
            return PreviewContext(system_id64=system_id64), []

        body_rows = await conn.fetch(
            """SELECT * FROM bodies WHERE system_id64 = $1""",
            system_id64,
        )

    local_body_profiles: dict[str, dict[str, Any]] = {}
    clean_rows = [dict(row) for row in body_rows]
    slot_input_rows: list[dict[str, Any]] = []
    for row in clean_rows:
        profile = profile_body(row)
        if profile.body_id:
            local_body_profiles[profile.body_id] = profile.to_context_profile()
        slot_input_rows.append({
            'system_address': system_id64,
            'body_id': row.get('body_id') or row.get('id'),
            'body_name': row.get('body_name') or row.get('name'),
            'radius': row.get('radius'),
            'gravity': row.get('gravity'),
            'surface_temp': row.get('surface_temp'),
            'planet_class': row.get('planet_class') or row.get('subtype'),
            'terraform_state': row.get('terraform_state'),
            'atmosphere': row.get('atmosphere') or row.get('atmosphere_type'),
            'volcanism': row.get('volcanism') or row.get('volcanism_type'),
            'has_geo': row.get('has_geo'),
            'has_bio': row.get('has_bio'),
            'geo_signal_count': row.get('geo_signal_count'),
            'bio_signal_count': row.get('bio_signal_count'),
            'is_landable': row.get('is_landable'),
            'is_terraformable': row.get('is_terraformable'),
            'is_ringed': row.get('is_ringed'),
        })

    slot_prediction = predict_system_slots(slot_input_rows) if slot_input_rows else {
        'predicted_orbital_slots_total': None,
        'predicted_ground_slots_total': None,
        'slot_confidence': None,
        'prediction_status': 'unknown',
    }

    notes: list[str] = []
    notes.append(PREDICTION_DISCLAIMER)
    if slot_prediction.get('prediction_status') == 'unknown':
        notes.append(f'{INSUFFICIENT_DATA_REASON}. Verify in Architect Mode.')

    return PreviewContext(
        system_id64=system_id64,
        estimated_orbital_slots=slot_prediction.get('predicted_orbital_slots_total'),
        estimated_ground_slots=slot_prediction.get('predicted_ground_slots_total'),
        slot_confidence=slot_prediction.get('slot_confidence'),
        has_ringed_body=any(bool(row.get('is_ringed')) for row in clean_rows),
        local_body_profiles=local_body_profiles,
        mechanics_notes=notes,
        observed_facts=[],
    ), clean_rows


def _select_body_anchors(
    *,
    rows: list[dict[str, Any]],
    rule: ArchetypeRule,
    preferred_body_ids: list[str],
    limit: int,
) -> list[BodyAnchor]:
    preferred = {str(body_id) for body_id in preferred_body_ids}
    anchors: list[BodyAnchor] = []
    for row in rows:
        profile = profile_body(row)
        body_id = profile.body_id
        economies = set(profile.base_economies) | set(profile.modifier_economies)
        tags = set(profile.strategic_tags)
        rationale: list[str] = []
        score = profile.confidence + profile.purity

        economy_matches = [economy for economy in rule.expected_economies if economy in economies]
        if economy_matches:
            score += len(economy_matches) * 2.0
            rationale.append(f"Body supports target economies: {', '.join(economy_matches)}.")
        tag_matches = [tag for tag in rule.strategic_tags if tag in tags]
        if tag_matches:
            score += len(tag_matches)
            rationale.append(f"Body has target strategic tags: {', '.join(tag_matches)}.")
        avoid_matches = [economy for economy in rule.avoid_economies if economy in economies]
        if avoid_matches:
            score -= len(avoid_matches)
            rationale.append(f"Body includes non-target economies to watch: {', '.join(avoid_matches)}.")
        if body_id and body_id in preferred:
            score += 10.0
            rationale.append('Body was explicitly preferred in the request.')

        if not economy_matches and not tag_matches and not preferred:
            continue
        if preferred and body_id not in preferred:
            continue

        anchors.append(BodyAnchor(
            body_id=body_id,
            body_name=profile.body_name or f'Body {body_id or "unknown"}',
            profile=profile,
            score=round(score, 3),
            rationale=rationale or ['Body is available as a fallback anchor.'],
            tags=[*profile.strategic_tags, *profile.base_economies, *profile.modifier_economies],
        ))

    anchors.sort(key=lambda anchor: (-anchor.score, anchor.body_id or '', anchor.body_name))
    return anchors[:limit]
