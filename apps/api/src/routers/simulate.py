"""Simulation Preview API routes."""
from __future__ import annotations

from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Query

from edfinder_api.deps import get_pool
from edfinder_api.domain.colonisation_rules import get_target_profile, profile_body
from edfinder_api.domain.facilities import (
    FacilityTemplate,
    get_catalogue,
    load_bundled_catalogue,
    load_catalogue_from_rows,
)
from edfinder_api.ingest.slot_prediction import (
    INSUFFICIENT_DATA_REASON,
    PREDICTION_DISCLAIMER,
    predict_system_slots,
)
from edfinder_api.mechanics.scoring_rules import REGIONAL_RECOMMENDATION_WEIGHT, SIMULATION_RECOMMENDATION_WEIGHT
from edfinder_api.mechanics.versions import MECHANICS_VERSION
from edfinder_api.models import (
    FacilityTemplateResponse,
    RecommendedBuildPlan,
    RecommendedBuildsResponse,
    SimulateBuildRequest,
    SimulateBuildResponse,
)
from edfinder_api.regional.regional_analysis import response_from_row
from edfinder_api.recommendations.body_selector import select_body_candidates
from edfinder_api.recommendations.build_generator import generate_build_drafts
from edfinder_api.recommendations.plan_ranker import rank_plans
from edfinder_api.simulation.build_preview import PreviewContext, PreviewPlacement, simulate_build_preview
from edfinder_api.simulation.mechanics_trace import regional_trace_event


router = APIRouter(tags=['simulation-preview'])


@router.get('/api/facility-templates', response_model=list[FacilityTemplateResponse])
async def get_facility_templates(
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[FacilityTemplateResponse]:
    catalogue = await _catalogue_or_db(pool)
    return [
        FacilityTemplateResponse(
            id=f.id,
            name=f.name,
            category=f.category,
            tier=f.tier,
            economy=f.economy,
            is_port=f.is_port,
            is_support_facility=f.is_support_facility,
            allowed_location=f.allowed_location,
            pad_size=f.pad_size,
            confidence=f.data_confidence,
            canonical_confidence=f.canonical_confidence.to_dict(),
            notes=f.stat_effects.get('note') if isinstance(f.stat_effects, dict) else None,
            prerequisites=f.prerequisites if isinstance(f.prerequisites, list) else [],
            economy_effects=f.economy_effects if isinstance(f.economy_effects, dict) else {},
            yellow_cp_generated=f.yellow_cp_generated,
            green_cp_generated=f.green_cp_generated,
            yellow_cp_cost=f.yellow_cp_cost,
            green_cp_cost=f.green_cp_cost,
            stat_effects=f.stat_effects if isinstance(f.stat_effects, dict) else {},
        )
        for f in sorted(catalogue.values(), key=lambda item: (item.tier, item.category, item.name))
    ]


@router.post('/api/simulate/build', response_model=SimulateBuildResponse)
async def post_simulate_build(
    body: SimulateBuildRequest,
    pool: asyncpg.Pool = Depends(get_pool),
) -> SimulateBuildResponse:
    catalogue = await _catalogue_or_db(pool)
    context, _body_rows = await _preview_context(pool, body.system_id64)
    result = simulate_build_preview(
        system_id64=body.system_id64,
        target_archetype=body.target_archetype,
        placements=[
            PreviewPlacement(
                facility_template_id=p.facility_template_id,
                local_body_id=p.local_body_id,
                is_primary_port=p.is_primary_port,
                build_order=p.build_order,
            )
            for p in body.placements
        ],
        catalogue=catalogue,
        context=context,
    )
    return SimulateBuildResponse.model_validate(result)


@router.get('/api/systems/{system_id64}/recommended-builds', response_model=RecommendedBuildsResponse)
async def get_recommended_builds(
    system_id64: int,
    archetype: Optional[str] = Query(None),
    pool: asyncpg.Pool = Depends(get_pool),
) -> RecommendedBuildsResponse:
    catalogue = await _catalogue_or_db(pool)
    context, body_rows = await _preview_context(pool, system_id64)
    target = archetype or await _suggested_archetype(pool, system_id64) or 'flexible_multirole'
    target_profile = get_target_profile(target)
    regional_context = await _regional_context(pool, system_id64)
    warnings: list[str] = []

    if target_profile.warning:
        warnings.append(target_profile.warning)
    if not target_profile.supported:
        return RecommendedBuildsResponse(
            system_id64=system_id64,
            mechanics_version=MECHANICS_VERSION,
            target_archetype=target,
            best_suggested_archetype=target,
            recommended_next_action='Try a blank advanced simulation; recommended build rules are not implemented for this archetype yet.',
            plans=[],
            warnings=warnings,
        )

    total_slots = (context.estimated_orbital_slots or 0) + (context.estimated_ground_slots or 0)
    candidates = select_body_candidates(
        target,
        target_profile,
        body_rows,
        slot_confidence=context.slot_confidence,
        total_slots=total_slots,
        limit=3,
    )
    if not candidates:
        warnings.append('No suitable body candidate is available for this archetype. Recommended builds are hidden rather than guessed.')

    drafts = []
    if candidates:
        drafts, draft_warnings = generate_build_drafts(
            system_id64=system_id64,
            target=target_profile,
            body=candidates[0],
            catalogue=catalogue,
            slot_confidence=context.slot_confidence,
            total_slots=total_slots,
        )
        warnings.extend(draft_warnings)
    if not drafts and not warnings:
        warnings.append('No facility catalogue data is available yet. Try Simulation Preview manually once templates load.')

    simulated = []
    for draft in drafts:
        simulation = SimulateBuildResponse.model_validate(simulate_build_preview(
            system_id64=system_id64,
            target_archetype=target,
            placements=[
                PreviewPlacement(
                    facility_template_id=p.facility_template_id,
                    local_body_id=p.local_body_id,
                    is_primary_port=p.is_primary_port,
                    build_order=p.build_order,
                )
                for p in draft.request.placements
            ],
            catalogue=catalogue,
            context=context,
        ))
        simulated.append((draft, simulation))

    ranked = rank_plans(simulated, archetype=target, regional_context=regional_context)
    plans: list[RecommendedBuildPlan] = []
    for idx, ranked_plan in enumerate(ranked):
        draft = ranked_plan.draft
        simulation = ranked_plan.simulation
        body = draft.body
        regional_fit = ranked_plan.regional_fit
        final_score = (
            round(simulation.final_score * SIMULATION_RECOMMENDATION_WEIGHT + regional_fit * REGIONAL_RECOMMENDATION_WEIGHT, 1)
            if regional_fit else simulation.final_score
        )
        rank_breakdown = dict(ranked_plan.rank_breakdown)
        if regional_fit:
            simulation.mechanics_trace.setdefault('regional_effects', []).append(
                regional_trace_event(archetype=target, regional_fit=regional_fit, weight=REGIONAL_RECOMMENDATION_WEIGHT)
            )
        assumptions = ['Body economy is estimated from documented Mega Guide rules and available scan facts.']
        if context.slot_confidence is None or context.slot_confidence < 0.75:
            assumptions.append('Slot prediction is estimated, not observed in-game.')
        if any(f.data_confidence == 'estimated' for f in catalogue.values()):
            assumptions.append('Some facility data is provisional community-derived data.')
        plans.append(RecommendedBuildPlan(
            id=draft.id,
            label=draft.label,
            summary=draft.summary,
            complexity=simulation.build_complexity,
            confidence=simulation.confidence,
            final_score=final_score,
            composition_score=simulation.composition_score,
            buildability_score=simulation.buildability_score,
            economy_result=simulation.economy_composition,
            port_economy_summary=_port_economy_summary(simulation),
            cp_result=simulation.cp,
            build_order=draft.request.placements,
            strengths=simulation.strengths[:4],
            warnings=simulation.warnings[:4],
            tradeoffs=draft.tradeoffs,
            next_actions=simulation.recommendations[:3] or ['Preview this plan, then adjust body placement before committing in-game.'],
            selected_body_id=body.body_id,
            selected_body_name=body.body_name,
            body_selection_reason=body.reason,
            mechanics_basis=draft.mechanics_basis,
            economy_caveats=body.caveats,
            assumptions=assumptions,
            regional_role=regional_context.get('regional_role'),
            nearest_colony_distance=(
                (regional_context.get('nearest_colonised_system') or {}).get('distance_ly')
                if regional_context.get('nearest_colonised_system') else None
            ),
            archetype_regional_fit=regional_fit or None,
            regional_rationale=regional_context.get('rationale') or {},
            decision_explanation=_decision_explanation(
                draft=draft,
                simulation=simulation,
                regional_fit=regional_fit,
                assumptions=assumptions,
                is_default=(idx == 0),
            ),
            rank_breakdown=rank_breakdown,
            simulation_request=draft.request,
            is_default=(idx == 0),
        ))

    next_action = (
        'Open the top-ranked recommended build in Simulation Preview.'
        if len(plans) > 1 else
        'Open the recommended build in Simulation Preview.'
        if plans else
        'Try a blank advanced simulation once more body and topology data is available.'
    )
    if context.slot_confidence is not None and context.slot_confidence < 0.55:
        warnings.append('Slot data is predicted, not observed. Advanced plans are hidden until better topology data is available.')

    return RecommendedBuildsResponse(
        system_id64=system_id64,
        mechanics_version=MECHANICS_VERSION,
        target_archetype=target,
        best_suggested_archetype=target,
        recommended_next_action=next_action,
        plans=plans,
        warnings=warnings,
    )


async def _catalogue_or_db(pool: asyncpg.Pool) -> dict[str, FacilityTemplate]:
    catalogue = get_catalogue()
    if catalogue:
        return catalogue

    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT * FROM facility_templates ORDER BY tier, id')
    if rows:
        load_catalogue_from_rows([dict(row) for row in rows])
    else:
        load_bundled_catalogue()
    return get_catalogue()


async def _preview_context(pool: asyncpg.Pool, system_id64: int) -> tuple[PreviewContext, list[dict]]:
    async with pool.acquire() as conn:
        body_rows = await conn.fetch(
            """
            SELECT system_address, body_id, body_name, 'Planet' AS body_type,
                   planet_class AS subtype, planet_class,
                   is_landable, is_terraformable,
                   CASE
                       WHEN EXISTS (
                           SELECT 1
                             FROM bodies b
                             JOIN body_rings br
                               ON br.system_id64 = b.system_id64
                              AND br.body_id = b.id
                              AND br.association_status = 'local_matched'
                            WHERE b.system_id64 = body_scan_facts.system_address
                              AND (
                                  b.id = body_scan_facts.body_id::bigint
                                  OR b.name = body_scan_facts.body_name
                              )
                       ) THEN TRUE
                       WHEN body_scan_facts.is_ringed IS FALSE THEN FALSE
                       ELSE NULL
                   END AS is_ringed,
                   has_geo, has_bio, geo_signal_count, bio_signal_count,
                   terraform_state, volcanism, atmosphere,
                   radius, gravity, surface_temp, confidence
            FROM body_scan_facts
            WHERE system_address = $1
            ORDER BY body_id
            """,
            system_id64,
        )
        if not body_rows:
            body_rows = await conn.fetch(
                """
                SELECT $1::bigint AS system_address,
                       id AS body_id, name AS body_name, body_type, subtype,
                       subtype AS planet_class,
                       is_landable, is_terraformable,
                       CASE
                           WHEN EXISTS (
                               SELECT 1
                               FROM body_rings br
                               WHERE br.system_id64 = bodies.system_id64
                                 AND br.body_id = bodies.id
                                 AND br.association_status = 'local_matched'
                           ) THEN TRUE
                           ELSE NULL
                       END AS is_ringed,
                       (geo_signal_count > 0) AS has_geo,
                       (bio_signal_count > 0) AS has_bio,
                       bio_signal_count, geo_signal_count,
                       NULL AS terraform_state,
                       NULL AS volcanism,
                       NULL AS atmosphere,
                       radius, gravity, surface_temp,
                       is_earth_like, is_water_world, is_ammonia_world,
                       0.45::numeric AS confidence
                FROM bodies
                WHERE system_id64 = $1
                ORDER BY COALESCE(distance_from_star, 999999), id
                """,
                system_id64,
            )

    body_dicts = [dict(row) for row in body_rows]
    slot_prediction = predict_system_slots(body_dicts) if body_dicts else {
        'predicted_orbital_slots_total': None,
        'predicted_ground_slots_total': None,
        'slot_confidence': None,
        'prediction_status': 'unknown',
    }
    orbital = slot_prediction.get('predicted_orbital_slots_total')
    ground = slot_prediction.get('predicted_ground_slots_total')
    slot_confidence = slot_prediction.get('slot_confidence')
    body_profiles = {}
    for body in body_dicts:
        if body.get('body_id') is None:
            continue
        profile = profile_body(body)
        body_profiles[str(body['body_id'])] = profile.to_context_profile()

    mechanics_notes = ['Body economy inheritance follows the current repo colonisation rules table.']
    if body_profiles:
        mechanics_notes.append('Body economy is estimated from scan facts; confirm unusual cases in-game.')
    mechanics_notes.append(PREDICTION_DISCLAIMER)
    if slot_prediction.get('prediction_status') == 'unknown':
        mechanics_notes.append(f'{INSUFFICIENT_DATA_REASON}. Verify in Architect Mode.')

    return PreviewContext(
        system_id64=system_id64,
        estimated_orbital_slots=orbital,
        estimated_ground_slots=ground,
        slot_confidence=slot_confidence,
        has_ringed_body=any(bool(body.get('is_ringed')) for body in body_dicts),
        local_body_profiles=body_profiles,
        mechanics_notes=mechanics_notes,
    ), body_dicts


async def _suggested_archetype(pool: asyncpg.Pool, system_id64: int) -> Optional[str]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT primary_archetype
            FROM system_archetype_scores
            WHERE system_id64 = $1
            """,
            system_id64,
        )
    return str(row['primary_archetype']) if row and row['primary_archetype'] else None


def _decision_explanation(
    *,
    draft,
    simulation: SimulateBuildResponse,
    regional_fit: float,
    assumptions: list[str],
    is_default: bool,
) -> dict[str, object]:
    why_this_plan = []
    if is_default:
        why_this_plan.append('This plan ranked highest after simulation, economy stack, buildability, confidence, warnings, and complexity penalties.')
    else:
        why_this_plan.append('This plan remains a viable alternative but ranked below the default after transparent penalties.')
    if simulation.strengths:
        why_this_plan.extend(simulation.strengths[:2])
    if regional_fit:
        why_this_plan.append(f'Regional fit contributes lightly at {regional_fit:.0f}/100 and does not dominate the local simulation.')

    why_not_simpler = []
    if simulation.build_complexity in {'advanced', 'expert'}:
        why_not_simpler.append('A simpler plan would not preserve the same economy stack or topology support.')
    elif draft.tradeoffs:
        why_not_simpler.append('Simpler alternatives are preferred unless their tradeoffs reduce economy or service coverage.')

    why_not_more_advanced = []
    if simulation.confidence < 0.7:
        why_not_more_advanced.append('More advanced plans are limited by confidence in slots, topology, or facility data.')
    if simulation.warnings:
        why_not_more_advanced.append('Additional complexity would amplify existing warnings before the current plan is verified.')

    return {
        'why_this_plan_won': why_this_plan,
        'why_not_simpler': why_not_simpler,
        'why_not_more_advanced': why_not_more_advanced,
        'main_tradeoffs': draft.tradeoffs or simulation.warnings[:3],
        'sensitive_assumptions': assumptions,
        'confidence_summary': _confidence_summary(simulation),
    }


def _port_economy_summary(simulation: SimulateBuildResponse) -> list[str]:
    summaries: list[str] = []
    states = simulation.port_economy_states or []
    if not states:
        return summaries
    main = states[0]
    top_two = [str(item) for item in main.get('top_two', []) if item]
    if top_two:
        summaries.append(f'Main port economy: {" / ".join(top_two[:2])}')
    sources = main.get('contamination_sources') or []
    if sources:
        source = sources[0]
        economy = source.get('economy') or 'off-pair economy'
        source_name = source.get('source_name') or 'another source'
        influence_type = str(source.get('influence_type') or 'influence').replace('_', ' ')
        summaries.append(f'Contamination source: {influence_type} {economy} from {source_name}')
    elif main.get('tertiary_economies'):
        summaries.append(f'Tertiary pressure: {", ".join(main.get("tertiary_economies", [])[:2])}')
    return summaries[:2]


def _confidence_summary(simulation: SimulateBuildResponse) -> str:
    if simulation.confidence >= 0.75:
        return 'High confidence for current deterministic rules; review caveats before committing in-game.'
    if simulation.confidence >= 0.55:
        return 'Medium confidence; key mechanics are modelled but some data is inferred or estimated.'
    return 'Low confidence; confirm observed slot/body/service data before treating this as a stable plan.'


async def _regional_context(pool: asyncpg.Pool, system_id64: int) -> dict:
    if not hasattr(pool, 'acquire'):
        return {}
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                'SELECT * FROM system_regional_analysis WHERE system_id64 = $1',
                system_id64,
            )
        except (asyncpg.UndefinedTableError, AttributeError):
            row = None
    return response_from_row(dict(row) if row else None, system_id64)
