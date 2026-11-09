"""Pure quality checks for future Guided Planner generated plans.

Stage 17N.3-A keeps this module out of the live optimiser route. It defines a
small, deterministic contract that future Light/Medium/High/Maxed generation
can call before presenting a plan to users.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import re
from typing import Literal, Optional

from edfinder_api.domain.facilities import FacilityTemplate
from edfinder_api.optimiser.archetype_rules import resolve_archetype_rule
from edfinder_api.optimiser.models import OptimiserCandidate


QualityStatus = Literal['ok', 'warning', 'reject']
PlanPreset = Literal['light', 'medium', 'high', 'maxed']

PRESET_COUNT_RANGES: dict[PlanPreset, tuple[int, int | None]] = {
    'light': (2, 5),
    'medium': (6, 10),
    'high': (11, 16),
    'maxed': (17, None),
}

ECONOMY_SOUP_REJECT_UNRELATED = 4
ECONOMY_SOUP_WARN_UNIQUE = 4
ECONOMY_SOUP_WARN_ONE_OFFS = 3
DOMINANT_ECONOMY_MIN_SHARE = 0.40


@dataclass(frozen=True)
class PlanBodySlotState:
    body_id: str
    predicted_orbital_slots: Optional[int] = None
    predicted_ground_slots: Optional[int] = None
    occupied_orbital_slots: int = 0
    occupied_ground_slots: int = 0
    unresolved_existing_infrastructure: bool = False
    inferred_station_body_association: bool = False


@dataclass(frozen=True)
class PlanQualityOptions:
    target_archetype: Optional[str] = None
    target_economies: list[str] = field(default_factory=list)
    preset: Optional[PlanPreset] = None
    requested_count: Optional[int] = None
    allow_mixed_economies: bool = False
    require_body_assignment: bool = True


@dataclass(frozen=True)
class MissingPrerequisite:
    placement_index: int
    facility_template_id: str
    description: str


@dataclass(frozen=True)
class EconomySoupAssessment:
    status: QualityStatus
    economies: dict[str, int]
    target_economies: list[str]
    dominant_economies: list[str]
    unrelated_economies: list[str]
    reasons: list[str] = field(default_factory=list)
    suggested_fix: Optional[str] = None


@dataclass(frozen=True)
class PlanQualityReport:
    status: QualityStatus
    placement_count: int
    body_count: int
    preset_range: tuple[int, int | None] | None
    economy_soup: EconomySoupAssessment
    missing_prerequisites: list[MissingPrerequisite] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)
    suggested_fixes: list[str] = field(default_factory=list)


def validate_generated_plan_quality(
    plan: OptimiserCandidate,
    catalogue: dict[str, FacilityTemplate],
    *,
    options: PlanQualityOptions | None = None,
    body_slots: list[PlanBodySlotState] | None = None,
    placement_lanes: dict[int, Literal['orbital', 'ground']] | None = None,
) -> PlanQualityReport:
    """Validate a generated candidate against the Guided Planner contract.

    The report is intentionally descriptive. Existing generation/ranking code
    can keep returning candidates unchanged until a later stage wires these
    checks into candidate filtering or presentation.
    """
    options = options or PlanQualityOptions(target_archetype=plan.target_archetype)
    target_economies = _target_economies(plan, options)
    warnings: list[str] = []
    rejections: list[str] = []
    suggested_fixes: list[str] = []

    placements = list(plan.placements)
    placement_count = len(placements)
    body_ids = [p.local_body_id for p in placements if p.local_body_id]
    body_count = len(set(body_ids))

    if placement_count == 0:
        rejections.append('Generated plan has no placements.')
        suggested_fixes.append('Return "No strong coherent plan found" instead of an empty plan.')

    if options.require_body_assignment:
        missing_body = [p for p in placements if not p.local_body_id]
        if missing_body:
            rejections.append('Every generated placement must have a body assignment before it is user-facing.')
            suggested_fixes.append('Assign each placement to an anchor or support body, or keep the plan hidden.')

    _validate_scale(
        plan=plan,
        placement_count=placement_count,
        body_count=body_count,
        options=options,
        warnings=warnings,
        rejections=rejections,
        suggested_fixes=suggested_fixes,
    )
    _validate_body_slots(
        plan=plan,
        catalogue=catalogue,
        body_slots=body_slots or [],
        placement_lanes=placement_lanes or {},
        warnings=warnings,
        rejections=rejections,
        suggested_fixes=suggested_fixes,
    )
    missing_prerequisites = _missing_prerequisites(plan, catalogue)
    if missing_prerequisites:
        warnings.append(
            f'Generated plan has {len(missing_prerequisites)} missing prerequisite warning(s).'
        )
        suggested_fixes.append('Include prerequisite structures in the plan or explain why they are deferred.')

    economy_soup = detect_economy_soup(
        plan,
        catalogue,
        target_economies=target_economies,
        preset=options.preset,
        allow_mixed=options.allow_mixed_economies,
    )
    if economy_soup.status == 'reject':
        rejections.extend(economy_soup.reasons)
        if economy_soup.suggested_fix:
            suggested_fixes.append(economy_soup.suggested_fix)
    elif economy_soup.status == 'warning':
        warnings.extend(economy_soup.reasons)
        if economy_soup.suggested_fix:
            suggested_fixes.append(economy_soup.suggested_fix)

    raw_id_hits = _raw_id_explanation_hits(plan)
    if raw_id_hits:
        warnings.append(
            'User-facing explanation contains raw identifiers: '
            + ', '.join(raw_id_hits[:3])
            + ('.' if len(raw_id_hits) <= 3 else ', ...')
        )
        suggested_fixes.append('Replace raw template/archetype/body IDs with body names and structure labels.')

    status = 'reject' if rejections else ('warning' if warnings else 'ok')
    return PlanQualityReport(
        status=status,
        placement_count=placement_count,
        body_count=body_count,
        preset_range=PRESET_COUNT_RANGES.get(options.preset) if options.preset else None,
        economy_soup=economy_soup,
        missing_prerequisites=missing_prerequisites,
        warnings=_unique(warnings),
        rejections=_unique(rejections),
        suggested_fixes=_unique(suggested_fixes),
    )


def detect_economy_soup(
    plan: OptimiserCandidate,
    catalogue: dict[str, FacilityTemplate],
    *,
    target_economies: list[str] | None = None,
    preset: PlanPreset | None = None,
    allow_mixed: bool = False,
) -> EconomySoupAssessment:
    """Detect uncontrolled economy mixing in a generated plan."""
    counts = _economy_counts(plan, catalogue)
    target = _unique([_normalise_economy(economy) for economy in (target_economies or []) if economy])
    total = sum(counts.values())
    unique_economies = sorted(counts)
    unrelated = [
        economy for economy in unique_economies
        if target and economy not in target
    ]
    dominant = [
        economy for economy, count in counts.most_common(2)
        if total and count / total >= DOMINANT_ECONOMY_MIN_SHARE
    ]
    reasons: list[str] = []
    suggested_fix: Optional[str] = None

    if not counts:
        return EconomySoupAssessment(
            status='warning',
            economies={},
            target_economies=target,
            dominant_economies=[],
            unrelated_economies=[],
            reasons=['Generated plan has no economy-producing support structures.'],
            suggested_fix='Add support structures that match the target economy spine.',
        )

    text = _plan_text(plan)
    mixed_explained = allow_mixed or preset == 'maxed' and any(
        word in text for word in ('mixed', 'broad', 'maxed', 'full', 'tradeoff', 'contamination')
    )

    if target and not any(economy in counts for economy in target):
        reasons.append('Generated plan ignores the requested target economy.')
        suggested_fix = 'Regenerate around the requested primary/secondary economy pair.'
        return EconomySoupAssessment(
            status='reject',
            economies=dict(counts),
            target_economies=target,
            dominant_economies=dominant,
            unrelated_economies=unrelated,
            reasons=reasons,
            suggested_fix=suggested_fix,
        )

    one_offs = [economy for economy, count in counts.items() if count == 1]
    if (
        len(unrelated) >= ECONOMY_SOUP_REJECT_UNRELATED
        and len(unique_economies) >= 5
        and not mixed_explained
    ):
        reasons.append(
            'Generated plan mixes too many unrelated economy families without a target reason.'
        )
        suggested_fix = 'Constrain the plan to one primary economy, one secondary economy, and justified support structures.'
        return EconomySoupAssessment(
            status='reject',
            economies=dict(counts),
            target_economies=target,
            dominant_economies=dominant,
            unrelated_economies=unrelated,
            reasons=reasons,
            suggested_fix=suggested_fix,
        )

    if len(unique_economies) >= ECONOMY_SOUP_WARN_UNIQUE and not dominant and not mixed_explained:
        reasons.append('Generated plan has no dominant primary or secondary economy.')
    if len(one_offs) >= ECONOMY_SOUP_WARN_ONE_OFFS and not mixed_explained:
        reasons.append('Generated plan spreads one-off structures across too many economies.')
    if len(unrelated) >= 2 and not mixed_explained:
        reasons.append('Generated plan adds multiple non-target support economies without an explanation.')

    status: QualityStatus = 'warning' if reasons else 'ok'
    if reasons and suggested_fix is None:
        suggested_fix = 'Label the plan as mixed/risky or remove non-target one-off structures.'
    return EconomySoupAssessment(
        status=status,
        economies=dict(counts),
        target_economies=target,
        dominant_economies=dominant,
        unrelated_economies=unrelated,
        reasons=_unique(reasons),
        suggested_fix=suggested_fix,
    )


def _validate_scale(
    *,
    plan: OptimiserCandidate,
    placement_count: int,
    body_count: int,
    options: PlanQualityOptions,
    warnings: list[str],
    rejections: list[str],
    suggested_fixes: list[str],
) -> None:
    if options.preset:
        minimum, maximum = PRESET_COUNT_RANGES[options.preset]
        if placement_count < minimum:
            message = (
                f'{options.preset.title()} plan has {placement_count} placements; '
                f'expected at least {minimum}.'
            )
            if _has_scale_limitation_explanation(plan):
                warnings.append(message)
            else:
                rejections.append(message)
                suggested_fixes.append('Return "No strong coherent plan found" or recommend a lower preset.')
        if maximum is not None and placement_count > maximum:
            warnings.append(
                f'{options.preset.title()} plan has {placement_count} placements; expected no more than {maximum}.'
            )
            suggested_fixes.append('Move excess placements to the next larger preset or explain the overage.')
        if options.preset in {'medium', 'high', 'maxed'} and body_count < 2:
            warnings.append(f'{options.preset.title()} plan should normally assign roles across multiple bodies.')

    if options.requested_count is not None:
        tolerance = max(1, round(options.requested_count * 0.2))
        if abs(placement_count - options.requested_count) > tolerance:
            message = (
                f'Generated plan has {placement_count} placements; requested about {options.requested_count}.'
            )
            if _has_scale_limitation_explanation(plan):
                warnings.append(message)
            else:
                rejections.append(message)
                suggested_fixes.append('Do not pad with unrelated structures; return a scale warning or lower-plan recommendation.')


def _validate_body_slots(
    *,
    plan: OptimiserCandidate,
    catalogue: dict[str, FacilityTemplate],
    body_slots: list[PlanBodySlotState],
    placement_lanes: dict[int, Literal['orbital', 'ground']],
    warnings: list[str],
    rejections: list[str],
    suggested_fixes: list[str],
) -> None:
    if not body_slots:
        return

    slot_by_body = {state.body_id: state for state in body_slots}
    known_usage: dict[str, Counter[str]] = defaultdict(Counter)
    unknown_lane_usage: Counter[str] = Counter()

    for index, placement in enumerate(plan.placements, start=1):
        body_id = placement.local_body_id
        if not body_id:
            continue
        template = catalogue.get(placement.facility_template_id)
        if template is None:
            warnings.append(f'Placement {placement.facility_template_id} is not in the facility catalogue.')
            continue
        lane = placement_lanes.get(index) or _placement_lane(template)
        if lane is None:
            warnings.append(
                f'Placement {template.name} can use orbital or ground slots; generated plan does not choose a lane.'
            )
            unknown_lane_usage[body_id] += 1
        else:
            known_usage[body_id][lane] += 1

    for body_id, usage in known_usage.items():
        state = slot_by_body.get(body_id)
        if state is None:
            warnings.append(f'No slot state was supplied for planned body {body_id}.')
            continue
        _check_lane_capacity(
            body_id=body_id,
            lane='orbital',
            predicted=state.predicted_orbital_slots,
            occupied=state.occupied_orbital_slots,
            planned=usage.get('orbital', 0),
            rejections=rejections,
        )
        _check_lane_capacity(
            body_id=body_id,
            lane='ground',
            predicted=state.predicted_ground_slots,
            occupied=state.occupied_ground_slots,
            planned=usage.get('ground', 0),
            rejections=rejections,
        )

    for body_id, unknown_count in unknown_lane_usage.items():
        state = slot_by_body.get(body_id)
        if state is None:
            warnings.append(f'No slot state was supplied for planned body {body_id}.')
            continue
        total_predicted = _sum_known(state.predicted_orbital_slots, state.predicted_ground_slots)
        if total_predicted is None:
            warnings.append(f'Planned body {body_id} has unresolved slot capacity for lane-flexible structures.')
            continue
        occupied_total = state.occupied_orbital_slots + state.occupied_ground_slots
        known_total = sum(known_usage.get(body_id, {}).values())
        if occupied_total + known_total + unknown_count > total_predicted:
            rejections.append(
                f'Planned body {body_id} exceeds confirmed total slot capacity once occupied slots are counted.'
            )

    for state in body_slots:
        if state.unresolved_existing_infrastructure:
            warnings.append(f'Body {state.body_id} has unresolved existing infrastructure; slot confidence is reduced.')
        if state.inferred_station_body_association:
            warnings.append(f'Body {state.body_id} has inferred station/body association; verify before relying on occupancy.')

    if any('occupied' in item or 'capacity' in item for item in rejections):
        suggested_fixes.append('Move placements to reserve bodies or lower the requested preset.')


def _check_lane_capacity(
    *,
    body_id: str,
    lane: str,
    predicted: Optional[int],
    occupied: int,
    planned: int,
    rejections: list[str],
) -> None:
    if planned <= 0 or predicted is None:
        return
    if occupied >= predicted:
        rejections.append(
            f'Body {body_id} has no confirmed remaining {lane} slots; generated plan uses occupied capacity.'
        )
    elif occupied + planned > predicted:
        rejections.append(
            f'Body {body_id} overflows {lane} slots: {occupied} occupied + {planned} planned > {predicted} predicted.'
        )


def _missing_prerequisites(
    plan: OptimiserCandidate,
    catalogue: dict[str, FacilityTemplate],
) -> list[MissingPrerequisite]:
    placed_templates = [
        catalogue[p.facility_template_id]
        for p in plan.placements
        if p.facility_template_id in catalogue
    ]
    missing: list[MissingPrerequisite] = []
    for index, placement in enumerate(plan.placements, start=1):
        template = catalogue.get(placement.facility_template_id)
        if template is None:
            continue
        for description in _prerequisite_descriptions(template):
            if not _prerequisite_satisfied(description, placed_templates, template):
                missing.append(MissingPrerequisite(
                    placement_index=index,
                    facility_template_id=placement.facility_template_id,
                    description=description,
                ))
    return missing


def _prerequisite_descriptions(template: FacilityTemplate) -> list[str]:
    descriptions: list[str] = []
    for item in template.prerequisites or []:
        if isinstance(item, str):
            description = item
        elif isinstance(item, dict):
            description = str(item.get('description') or item.get('facility') or item.get('prerequisite') or '')
        else:
            description = ''
        description = description.strip()
        if not description or _is_slot_only_prerequisite(description):
            continue
        descriptions.append(description)
    return descriptions


def _prerequisite_satisfied(
    description: str,
    placed_templates: list[FacilityTemplate],
    requiring_template: FacilityTemplate,
) -> bool:
    tokens = _significant_tokens(description)
    if not tokens:
        return True
    for template in placed_templates:
        if template.id == requiring_template.id:
            continue
        haystack = ' '.join([
            template.id,
            template.name,
            template.category,
            template.economy or '',
        ]).lower().replace('_', ' ')
        if all(token in haystack for token in tokens):
            return True
        if any(token in haystack for token in tokens) and len(tokens) == 1:
            return True
    return False


def _target_economies(plan: OptimiserCandidate, options: PlanQualityOptions) -> list[str]:
    if options.target_economies:
        return _unique([_normalise_economy(economy) for economy in options.target_economies])
    rule, _warnings = resolve_archetype_rule(options.target_archetype or plan.target_archetype)
    return _unique([_normalise_economy(economy) for economy in rule.expected_economies])


def _economy_counts(
    plan: OptimiserCandidate,
    catalogue: dict[str, FacilityTemplate],
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for placement in plan.placements:
        template = catalogue.get(placement.facility_template_id)
        if template is None or not template.economy or template.is_colony_port:
            continue
        counts[_normalise_economy(template.economy)] += 1
    return counts


def _placement_lane(template: FacilityTemplate) -> str | None:
    if template.needs_orbital:
        return 'orbital'
    if template.needs_surface:
        return 'ground'
    return None


def _sum_known(*values: Optional[int]) -> Optional[int]:
    if any(value is None for value in values):
        return None
    return sum(int(value or 0) for value in values)


def _normalise_economy(economy: str) -> str:
    value = str(economy).strip()
    if not value:
        return value
    lowered = value.lower().replace(' ', '')
    aliases = {
        'hightech': 'HighTech',
        'hitech': 'HighTech',
    }
    if lowered in aliases:
        return aliases[lowered]
    return value[:1].upper() + value[1:]


def _plan_text(plan: OptimiserCandidate) -> str:
    return ' '.join([
        plan.label,
        plan.target_archetype,
        plan.strategy,
        *plan.tags,
        *plan.rationale,
        *plan.warnings,
        *plan.assumptions,
    ]).lower()


def _has_scale_limitation_explanation(plan: OptimiserCandidate) -> bool:
    text = _plan_text(plan)
    return any(word in text for word in ('capacity', 'limited', 'sparse', 'no strong coherent plan', 'lower preset'))


def _raw_id_explanation_hits(plan: OptimiserCandidate) -> list[str]:
    text_items = [plan.label, *plan.rationale, *plan.warnings, *plan.assumptions]
    hits: list[str] = []
    for item in text_items:
        hits.extend(re.findall(r'\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b', item))
        hits.extend(re.findall(r'\bbody[0-9a-z_-]+\b', item.lower()))
    return _unique(hits)


def _significant_tokens(value: str) -> list[str]:
    stop = {
        'a', 'an', 'and', 'at', 'available', 'body', 'facility', 'installation',
        'lane', 'or', 'orbital', 'port', 'requires', 'settlement', 'slot',
        'surface', 'the', 'tier', 't1', 't2', 't3',
    }
    tokens = [
        token for token in re.findall(r'[a-z0-9]+', value.lower())
        if token not in stop and len(token) > 2
    ]
    return _unique(tokens)


def _is_slot_only_prerequisite(value: str) -> bool:
    lowered = value.lower()
    return 'slot' in lowered and not any(
        word in lowered for word in ('settlement', 'installation', 'facility', 'hub', 'farm', 'relay', 'military')
    )


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
