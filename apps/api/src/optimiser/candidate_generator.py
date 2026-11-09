"""Bounded deterministic Stage 5A optimiser candidate generation."""
from __future__ import annotations

from dataclasses import replace
from collections import Counter
import logging
from typing import Any, Callable, Optional

import asyncpg

from edfinder_api.domain.facilities import FacilityTemplate, is_colony_ship
from edfinder_api.optimiser.archetype_rules import ArchetypeRule, resolve_archetype_rule
from edfinder_api.optimiser.candidate_generation_context import (
    BodyAnchor,
    _get_preview_context_and_body_rows,
    _select_body_anchors,
)
from edfinder_api.optimiser.dedupe import dedupe_candidates, placement_fingerprint
from edfinder_api.optimiser.facility_selection import (
    find_service_unlock_support,
    find_support_by_economy,
    select_port_template,
    select_support_templates,
)
from edfinder_api.optimiser.models import (
    CandidateGenerationRequest,
    CandidateGenerationResult,
    CandidatePlacement,
    OptimiserCandidate,
    candidate_placement_to_preview_placement,
)
from edfinder_api.optimiser.preview_summary import preview_summary_from_response
from edfinder_api.optimiser.system_analysis import analyse_system_strategy
from edfinder_api.simulation.build_preview import PreviewContext, simulate_build_preview

PreviewRunner = Callable[..., dict[str, Any]]
logger = logging.getLogger(__name__)


async def generate_candidates(
    request: CandidateGenerationRequest,
    catalogue: dict[str, FacilityTemplate],
    pool: asyncpg.Pool,
    *,
    preview_runner: PreviewRunner = simulate_build_preview,
) -> CandidateGenerationResult:
    rule, warnings = resolve_archetype_rule(request.target_archetype)
    logger.info(
        "optimiser.candidates.generate.start",
        extra={
            "system_id64": request.system_id64,
            "target_archetype": request.target_archetype,
            "max_candidates": request.max_candidates,
            "run_preview": request.run_preview,
        },
    )
    assumptions = [
        'Stage 5A generates bounded heuristic candidates only; Simulation Preview remains the source of truth.',
    ]

    if request.max_candidates <= 0:
        return CandidateGenerationResult(
            system_id64=request.system_id64,
            target_archetype=rule.key,
            candidate_count=0,
            candidates=[],
            warnings=warnings,
            assumptions=assumptions,
        )

    context, body_rows = await _get_preview_context_and_body_rows(pool, request.system_id64)
    logger.info(
        "optimiser.candidates.context.loaded",
        extra={"system_id64": request.system_id64, "body_count": len(body_rows), "catalogue_count": len(catalogue)},
    )

    if not catalogue:
        return CandidateGenerationResult(
            system_id64=request.system_id64,
            target_archetype=rule.key,
            candidate_count=0,
            candidates=[],
            warnings=[*warnings, 'No facility catalogue is available for candidate generation.'],
            assumptions=assumptions,
        )

    anchors = _select_body_anchors(
        rows=body_rows,
        rule=rule,
        preferred_body_ids=request.preferred_body_ids,
        limit=max(1, request.max_candidates * 2),
    )
    if not anchors:
        anchors = [BodyAnchor(None, 'system-level plan', None, 0.0, ['No body data is available; generated a system-level candidate.'], ['no_body_data'])]
        warnings.append('No body data available; generated system-level candidates only.')
    analysis = analyse_system_strategy(anchors, body_count=len(body_rows))
    assumptions.extend(analysis.opportunities[:2])
    warnings.extend(analysis.weak_points[:2])

    candidates: list[OptimiserCandidate] = []
    seen_fingerprints: set[tuple[tuple[str, str | None, bool, int], ...]] = set()

    for anchor in anchors:
        for strategy in rule.strategies:
            candidate = _build_candidate(
                rule=rule,
                strategy=strategy,
                anchor=anchor,
                anchors=anchors,
                catalogue=catalogue,
                allow_estimated_data=request.allow_estimated_data,
            )
            if candidate is None:
                continue
            if not _is_useful_candidate(candidate, catalogue):
                logger.info(
                    "optimiser.candidates.filtered_trivial",
                    extra={"candidate_id": candidate.candidate_id, "strategy": candidate.strategy},
                )
                continue

            fingerprint = placement_fingerprint(candidate.placements)
            if fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)

            if request.run_preview:
                logger.info(
                    "optimiser.candidates.preview.start",
                    extra={"candidate_id": candidate.candidate_id, "placement_count": len(candidate.placements)},
                )
                candidate = _attach_preview_summary(
                    candidate=candidate,
                    request=request,
                    catalogue=catalogue,
                    base_context=context,
                    anchor=anchor,
                    preview_runner=preview_runner,
                )

            candidates.append(candidate)
            if len(candidates) >= max(0, request.max_candidates):
                break
        if len(candidates) >= max(0, request.max_candidates):
            break

    candidates = dedupe_candidates(candidates)[:max(0, request.max_candidates)]
    logger.info(
        "optimiser.candidates.generate.complete",
        extra={"system_id64": request.system_id64, "candidate_count": len(candidates)},
    )
    return CandidateGenerationResult(
        system_id64=request.system_id64,
        target_archetype=rule.key,
        candidate_count=len(candidates),
        candidates=candidates,
        warnings=warnings,
        assumptions=assumptions,
    )


def _build_candidate(
    *,
    rule: ArchetypeRule,
    strategy: str,
    anchor: BodyAnchor,
    anchors: list[BodyAnchor],
    catalogue: dict[str, FacilityTemplate],
    allow_estimated_data: bool,
) -> Optional[OptimiserCandidate]:
    port = select_port_template(catalogue, strategy, allow_estimated_data=allow_estimated_data)
    if port is None:
        return None

    warnings: list[str] = []
    assumptions: list[str] = []
    tags = [strategy, rule.key, *anchor.tags]

    supports = select_support_templates(catalogue, rule, strategy, allow_estimated_data=allow_estimated_data)
    primary_supports = find_support_by_economy(catalogue, rule.primary_economies, allow_estimated_data=allow_estimated_data)
    support_pool: list[FacilityTemplate] = []

    if strategy == 'main_station':
        support_pool = _support_templates_for_economies(
            catalogue,
            [*rule.expected_economies, 'Industrial', 'Refinery', 'HighTech', 'Agriculture', 'Military', 'Tourism'],
            allow_estimated_data=allow_estimated_data,
        )
        tags.extend(['main_station', 'primary_port'])
        assumptions.append('Main station candidate targets expansion/full scale around a higher-tier port.')
    elif strategy == 'balanced_expansion':
        support_pool = supports or _general_support_templates(catalogue, allow_estimated_data=allow_estimated_data)
        tags.extend(['balanced', 'support_body'])
        assumptions.append('Balanced expansion aims for a multi-body strategic plan, not just a bootstrap starter.')
    elif strategy == 'industrial_refinery':
        support_pool = _support_templates_for_economies(
            catalogue,
            ['Refinery', 'Industrial', 'Extraction'],
            allow_estimated_data=allow_estimated_data,
        )
        tags.extend(['industrial', 'refinery'])
        assumptions.append('Industrial/refinery strategy expands beyond the anchor with multiple supports where possible.')
    elif strategy == 'tourism_agriculture':
        support_pool = _support_templates_for_economies(
            catalogue,
            ['Tourism', 'Agriculture', 'HighTech'],
            allow_estimated_data=allow_estimated_data,
        )
        tags.extend(['tourism', 'agriculture'])
        assumptions.append('Tourism/agriculture strategy builds a body network for civilian economy support.')
    elif strategy == 'military_security':
        support_pool = _support_templates_for_economies(
            catalogue,
            ['Military', 'Industrial'],
            allow_estimated_data=allow_estimated_data,
        )
        tags.extend(['military', 'security'])
        assumptions.append('Military/security strategy scales support around security and logistics pressure.')
    elif strategy == 'support_body':
        support_pool = supports or _general_support_templates(catalogue, allow_estimated_data=allow_estimated_data)
        tags.extend(['support_body', 'body_diversity'])
        assumptions.append('Support-body strategy intentionally spreads placements across multiple bodies.')
    elif strategy == 'primary_port_bootstrap':
        support_pool = supports or _general_support_templates(catalogue, allow_estimated_data=allow_estimated_data)
        tags.extend(['primary_port', 'bootstrap'])
        assumptions.append('Primary-port starter is explicitly minimal bootstrap, not a full strategic recommendation.')
    elif strategy == 'balanced':
        support_pool = supports
        assumptions.append('Balanced strategy remains available as a compact compatibility fallback.')
    elif strategy == 'pure':
        support_pool = primary_supports or supports
        assumptions.append('Pure strategy prioritises primary target-economy supports.')
    elif strategy == 'services_aware':
        support_pool = supports
        service_support = find_service_unlock_support(catalogue, allow_estimated_data=allow_estimated_data)
        if service_support:
            support_pool = [service_support, *support_pool]
            assumptions.append('Service-aware strategy includes an obvious unlock support.')
        else:
            warnings.append('No obvious service-unlocking support was available in the catalogue.')
    elif strategy == 'low_cp':
        support_pool = supports
        assumptions.append('Low-CP strategy favours lower CP-cost templates over build scale.')
    elif strategy == 'flexible_multirole':
        support_pool = supports
        assumptions.append('Flexible multirole strategy samples broad support options without exhaustive search.')
    else:
        return None

    target_placements = _target_placement_count(
        strategy=strategy,
        support_pool=support_pool,
        anchor_count=len([candidate for candidate in anchors if candidate.body_id]),
    )
    minimum_placements = _minimum_placement_count(strategy)
    selected = _scaled_template_sequence(
        port=port,
        supports=support_pool,
        target_size=target_placements,
        strategy=strategy,
    )
    if len(selected) == 1:
        warnings.append('No matching support facilities were available; candidate is port-only.')

    placements = _placements_from_templates(selected, anchor.body_id, strategy=strategy, anchors=anchors)
    if not placements:
        return None
    if len(placements) < minimum_placements and strategy != 'primary_port_bootstrap':
        warnings.append('Candidate scale is limited by sparse body or facility data.')
        assumptions.append('Generate additional data/imports to unlock larger strategic plans for this system.')

    scale_tier = _candidate_scale_tier(len(placements))
    used_body_ids = sorted({placement.local_body_id for placement in placements if placement.local_body_id})
    tags.extend([f'scale_{scale_tier}', scale_tier])
    if len(used_body_ids) > 1:
        tags.append('body_diversity')
    assumptions.append(_scale_summary(scale_tier, len(placements), len(used_body_ids)))

    safe_body = anchor.body_id or 'system'
    candidate_id = f'{rule.key}_{safe_body}_{strategy}'
    label = _candidate_label(strategy, rule.label)
    return OptimiserCandidate(
        candidate_id=candidate_id,
        label=label,
        target_archetype=rule.key,
        strategy=strategy,
        placements=placements,
        rationale=[
            *anchor.rationale,
            f'Strategy: {strategy.replace("_", " ")}',
            f'Scale: {scale_tier} ({len(placements)} placements across {max(1, len(used_body_ids))} body/bodies).',
        ],
        warnings=warnings,
        assumptions=assumptions,
        tags=_unique_strings(tags),
        preview_summary=None,
    )


def _attach_preview_summary(
    *,
    candidate: OptimiserCandidate,
    request: CandidateGenerationRequest,
    catalogue: dict[str, FacilityTemplate],
    base_context: PreviewContext,
    anchor: BodyAnchor,
    preview_runner: PreviewRunner,
) -> OptimiserCandidate:
    context = replace(
        base_context,
        local_body_profiles=dict(base_context.local_body_profiles),
        mechanics_notes=list(base_context.mechanics_notes),
        observed_facts=list(base_context.observed_facts),
    )
    if anchor.profile and anchor.body_id:
        context.local_body_profiles[anchor.body_id] = anchor.profile.to_context_profile()

    try:
        response = preview_runner(
            system_id64=request.system_id64,
            target_archetype=candidate.target_archetype,
            placements=[candidate_placement_to_preview_placement(p) for p in candidate.placements],
            catalogue=catalogue,
            context=context,
        )
        return replace(candidate, preview_summary=preview_summary_from_response(response))
    except Exception as exc:  # pragma: no cover - exercised through tests with deterministic monkeypatches.
        logger.exception("optimiser.candidates.preview.failed", extra={"candidate_id": candidate.candidate_id})
        return replace(candidate, warnings=[*candidate.warnings, f'Preview failed for candidate: {exc}'])


def _placements_from_templates(
    templates: list[FacilityTemplate],
    body_id: Optional[str],
    *,
    strategy: str,
    anchors: list[BodyAnchor],
) -> list[CandidatePlacement]:
    placements: list[CandidatePlacement] = []
    primary_assigned = False
    support_body_cycle = _support_body_cycle(strategy=strategy, anchors=anchors, primary_body_id=body_id)
    support_cursor = 0
    for template in templates:
        is_primary = bool(template.is_port and not primary_assigned)
        primary_assigned = primary_assigned or is_primary
        placement_body_id = body_id
        if not is_primary and support_body_cycle:
            placement_body_id = support_body_cycle[support_cursor % len(support_body_cycle)]
            support_cursor += 1
        placements.append(CandidatePlacement(
            facility_template_id=template.id,
            local_body_id=placement_body_id,
            is_primary_port=is_primary,
            build_order=len(placements) + 1,
        ))
    return placements


def _support_templates_for_economies(
    catalogue: dict[str, FacilityTemplate],
    economies: list[str],
    *,
    allow_estimated_data: bool,
) -> list[FacilityTemplate]:
    supports = find_support_by_economy(catalogue, economies, allow_estimated_data=allow_estimated_data)
    if supports:
        return supports
    return _general_support_templates(catalogue, allow_estimated_data=allow_estimated_data)


def _general_support_templates(
    catalogue: dict[str, FacilityTemplate],
    *,
    allow_estimated_data: bool,
) -> list[FacilityTemplate]:
    supports = [
        template
        for template in catalogue.values()
        if template.is_support_facility and (allow_estimated_data or template.data_confidence != 'estimated')
    ]
    return sorted(supports, key=lambda t: (t.tier, -(t.yellow_cp_generated + t.green_cp_generated), t.name, t.id))


def _support_body_cycle(
    *,
    strategy: str,
    anchors: list[BodyAnchor],
    primary_body_id: Optional[str],
) -> list[Optional[str]]:
    ordered: list[Optional[str]] = []
    if primary_body_id:
        ordered.append(primary_body_id)
    ordered.extend([
        anchor.body_id
        for anchor in anchors
        if anchor.body_id and anchor.body_id != primary_body_id
    ])
    unique_ordered: list[Optional[str]] = []
    seen: set[Optional[str]] = set()
    for body in ordered:
        if body in seen:
            continue
        seen.add(body)
        unique_ordered.append(body)
    if not unique_ordered:
        return [primary_body_id]
    if strategy == 'primary_port_bootstrap':
        return unique_ordered[:1]
    if strategy in {'main_station', 'balanced_expansion', 'support_body'}:
        return unique_ordered[: min(4, len(unique_ordered))]
    if strategy in {'industrial_refinery', 'tourism_agriculture', 'military_security', 'balanced', 'pure', 'services_aware', 'flexible_multirole'}:
        return unique_ordered[: min(3, len(unique_ordered))]
    return unique_ordered[:1]


def _target_placement_count(
    *,
    strategy: str,
    support_pool: list[FacilityTemplate],
    anchor_count: int,
) -> int:
    unique_support_count = len(_unique_templates(support_pool))
    if strategy == 'primary_port_bootstrap':
        return 4
    if strategy in {'main_station', 'balanced_expansion', 'support_body'}:
        if unique_support_count >= 12 and anchor_count >= 3:
            return 16
        if unique_support_count >= 8:
            return 12
        return 9
    if unique_support_count >= 6:
        return 8
    if unique_support_count >= 4:
        return 6
    return 5


def _minimum_placement_count(strategy: str) -> int:
    if strategy == 'primary_port_bootstrap':
        return 2
    if strategy in {'main_station', 'balanced_expansion', 'support_body'}:
        return 9
    return 5


def _scaled_template_sequence(
    *,
    port: FacilityTemplate,
    supports: list[FacilityTemplate],
    target_size: int,
    strategy: str,
) -> list[FacilityTemplate]:
    selected: list[FacilityTemplate] = [port]
    if not supports:
        return selected

    unique_supports = _unique_templates(supports)
    for template in unique_supports:
        if len(selected) >= target_size:
            break
        selected.append(template)
    if len(selected) >= target_size:
        return selected

    # Sparse catalogues should still produce usable multi-structure plans. Reuse
    # supports in a bounded way so candidates can reach starter/expansion scale.
    max_repeats = 1 if strategy == 'primary_port_bootstrap' else (2 if target_size < 12 else 3)
    support_counts = Counter(template.id for template in selected if template.id != port.id)
    support_index = 0
    safety = 0
    while len(selected) < target_size and support_index < 10_000:
        template = unique_supports[support_index % len(unique_supports)]
        support_index += 1
        safety += 1
        if support_counts[template.id] >= max_repeats:
            if safety > len(unique_supports) * 3:
                break
            continue
        selected.append(template)
        support_counts[template.id] += 1
        safety = 0
    return selected


def _candidate_scale_tier(placement_count: int) -> str:
    if placement_count <= 4:
        return 'bootstrap'
    if placement_count <= 8:
        return 'starter'
    if placement_count <= 14:
        return 'expansion'
    return 'full'


def _scale_summary(scale_tier: str, placement_count: int, body_count: int) -> str:
    readable = {
        'bootstrap': 'bootstrap/minimal',
        'starter': 'starter',
        'expansion': 'expansion',
        'full': 'ambitious/full',
    }.get(scale_tier, scale_tier)
    return f'Suggested build scale is {readable}: {placement_count} placements across {max(1, body_count)} body/bodies.'


def _is_useful_candidate(candidate: OptimiserCandidate, catalogue: dict[str, FacilityTemplate]) -> bool:
    if len(candidate.placements) < 2:
        return False
    templates = [catalogue.get(placement.facility_template_id) for placement in candidate.placements]
    known_templates = [template for template in templates if template is not None]
    if known_templates and all(is_colony_ship(template) for template in known_templates):
        return False
    support_count = sum(1 for template in known_templates if template.is_support_facility)
    has_strategic_tag = any(
        tag in candidate.tags
        for tag in ('industrial', 'refinery', 'tourism', 'agriculture', 'military', 'security', 'balanced', 'support_body', 'main_station')
    )
    return support_count >= 1 and has_strategic_tag


def _candidate_label(strategy: str, rule_label: str) -> str:
    labels = {
        'main_station': f'Main station {rule_label} candidate',
        'balanced_expansion': f'Balanced expansion {rule_label} plan',
        'industrial_refinery': 'Industrial / refinery starter',
        'tourism_agriculture': 'Tourism / agriculture hub starter',
        'military_security': 'Military / security stabiliser',
        'support_body': f'Support-body {rule_label} plan',
        'primary_port_bootstrap': 'Primary-port bootstrap starter',
    }
    return labels.get(strategy, f'{strategy.replace("_", " ").title()} {rule_label} candidate')


def _unique_templates(templates: list[FacilityTemplate]) -> list[FacilityTemplate]:
    seen: set[str] = set()
    result: list[FacilityTemplate] = []
    for template in templates:
        if template.id in seen:
            continue
        seen.add(template.id)
        result.append(template)
    return result


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
