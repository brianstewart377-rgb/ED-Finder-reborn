"""V3 planner catalogue, stateless evaluation and canonical body facts."""

from __future__ import annotations

from dataclasses import asdict
from functools import cache
from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from edfinder_api.config import limiter
from edfinder_api.cpe import (
    RULEBOOK_RELEASE,
    Body,
    EconomyBody,
    EconomySystem,
    Plan,
    Site,
    evaluate_economies,
    evaluate_plan,
)
from edfinder_api.cpe.budget import _cost
from edfinder_api.cpe.catalogue import planner_facilities, prerequisites_for
from edfinder_api.cpe.economy import BodyKind, ItemKind, PortEconomy, PortRole
from edfinder_api.cpe.explain import explain_rule
from edfinder_api.cpe.plan import PlanInvalid
from edfinder_api.deps import get_readonly_pool
from edfinder_api.mechanics.cp_rules import escalating_port_tier
from edfinder_api.routers.plan_economy_facts import (
    ReserveLevel,
    aggregate_reserve_levels,
    economy_kind,
    economy_topology,
)
from edfinder_api.routers.plan_slot_predictions import (
    predict_surface_slots,
    prediction_generation_reason,
)
from edfinder_api.routers.v3 import (
    SystemId,
    _bounded,
    _numeric_system_id,
    current_canonical_generation,
)

router = APIRouter(prefix="/api/v1/plan", tags=["v3-plan"])


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PlanFacilityV1(PlanModel):
    id: str
    name: str
    category: str
    tier: int
    allowed_location: str
    economy: str | None
    is_port: bool
    escalating_port_tier: int | None
    yellow_cp_cost: int
    green_cp_cost: int
    yellow_cp_generated: int
    green_cp_generated: int
    prerequisites: list[list[str]]
    rule_ids: list[str]


class PlanCatalogueV1(PlanModel):
    rulebook_release: str
    facilities: list[PlanFacilityV1]


class PlanSystemBodyV1(PlanModel):
    body_id: str
    name: str
    direct_parent_body_id: str | None
    body_type: str | None
    is_main_star: bool | None
    is_landable: bool | None
    radius_km: float | None
    surface_gravity_g: float | None
    surface_temperature_k: float | None
    atmosphere: str | None
    terraforming_state: str | None
    volcanism: str | None
    distance_from_arrival_ls: float | None
    has_rings: bool
    geological_signals: int
    biological_signals: int
    signals_complete: bool | None
    lifecycle_state: str
    economy_kind: BodyKind | None = None
    economy_parent_id: str | None = None
    in_binary_pair: bool | None = None
    tidally_locked: bool | None = None
    volcanism_present: bool | None = None
    terraformable: bool | None = None
    predicted_surface_slots: int | None = None
    prediction_version: str | None = None
    prediction_status: Literal['predicted', 'unknown'] | None = None
    missing_inputs: list[str] | None = None
    prediction_reason: str | None = None


class PlanSystemBeltV1(PlanModel):
    belt_id: str
    name: str
    parent_body_id: str
    ring_type: str | None
    reserve: str | None
    mass_mt: float | None
    inner_radius_km: float | None
    outer_radius_km: float | None
    lifecycle_state: str


class PlanBodiesV1(PlanModel):
    generation_id: str
    publication_sequence: str
    system_id64: str
    bodies: list[PlanSystemBodyV1]
    belts: list[PlanSystemBeltV1]
    economy_facts_generation_id: str | None = None
    reserve_level: ReserveLevel | None = None
    has_black_hole: bool | None = None
    has_white_dwarf: bool | None = None
    has_neutron_star: bool | None = None
    economy_notes: list[str] = Field(default_factory=list)


class PlanBodyEconomyV1(PlanModel):
    kind: BodyKind | None = None
    parent_ref: str | None = Field(default=None, min_length=1, max_length=64)
    in_binary_pair: bool | None = None
    tidally_locked: bool | None = None
    volcanism: bool | None = None
    terraformable: bool | None = None
    has_rings: bool | None = None
    geological_signals: int | None = Field(default=None, ge=0, le=1000)
    biological_signals: int | None = Field(default=None, ge=0, le=1000)


class PlanSystemEconomyV1(PlanModel):
    reserve: ReserveLevel | None = None
    has_black_hole: bool = False
    has_white_dwarf: bool = False
    has_neutron_star: bool = False


class PlanBodyV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_ref: str = Field(min_length=1, max_length=64, strict=True)
    orbital_slots: int | None = Field(default=None, ge=0, le=64, strict=True)
    surface_slots: int | None = Field(default=None, ge=0, le=64, strict=True)
    is_landable: bool | None = Field(default=None, strict=True)
    is_ringed: bool | None = Field(default=None, strict=True)
    economy: PlanBodyEconomyV1 | None = None


class PlanSiteV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: str = Field(min_length=1, max_length=64, strict=True)
    facility_id: str = Field(min_length=1, max_length=64, strict=True)
    body_ref: str | None = Field(default=None, min_length=1, max_length=64, strict=True)
    slot_kind: Literal["orbital", "surface"]
    status: Literal["planned", "building", "complete", "demolished"]
    name: str | None = Field(default=None, max_length=64, strict=True)


class PlanRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_id64: str = Field(pattern=r"^[0-9]{1,19}$", strict=True)
    bodies: list[PlanBodyV1] = Field(default_factory=list, max_length=1000)
    sites: list[PlanSiteV1] = Field(min_length=1, max_length=300)
    system_economy: PlanSystemEconomyV1 | None = None


class PlanTotalsV1(PlanModel):
    t2: int
    t3: int


class PlanStepV1(PlanModel):
    site_id: str
    facility_id: str
    status: str
    view: Literal["now", "after_plan", "ignored"]
    result: Literal["OK", "BLOCKED", "NEEDS_INFO", "COUNTED", "IGNORED"]
    counted_port_number: int | None
    cost_t2: int
    cost_t3: int
    reward_t2: int
    reward_t3: int
    t2_before: int
    t3_before: int
    t2_after: int
    t3_after: int
    reasons: list[str]
    rule_ids: list[str]
    payers: list[str]


class PlanCheckV1(PlanModel):
    code: str
    message: str
    rule_ids: list[str]


class PlanSummaryV1(PlanModel):
    OK: int
    BLOCKED: int
    NEEDS_INFO: int
    COUNTED: int
    IGNORED: int


class PlanRuleExplanationV1(PlanModel):
    id: str
    title: str
    status: str
    statement: str
    quote: str
    page: int | None


class EconomyModifierV1(PlanModel):
    condition: str
    delta: float


class EconomyItemV1(PlanModel):
    kind: ItemKind
    base: float
    modifiers: list[EconomyModifierV1]
    floored: bool
    value: float
    source_site_ids: list[str]
    via_site_id: str | None
    rule_ids: list[str]
    count: int | None


class EconomyV1(PlanModel):
    name: str
    strength: float
    items: list[EconomyItemV1]


class PortEconomyV1(PlanModel):
    site_id: str
    body_ref: str | None
    role: PortRole
    supports: str | None
    economies: list[EconomyV1]


class EconomyNoteV1(PlanModel):
    code: str
    site_id: str | None
    body_ref: str | None
    message: str


class PlanEconomiesV1(PlanModel):
    now: list[PortEconomyV1]
    after_plan: list[PortEconomyV1]
    notes: list[EconomyNoteV1]


class PlanEvaluationV1(PlanModel):
    rulebook_release: str
    system_id64: str
    now: PlanTotalsV1
    after_plan: PlanTotalsV1
    next_port_cost: PlanTotalsV1
    steps: list[PlanStepV1]
    plan_checks: list[PlanCheckV1]
    summary: PlanSummaryV1
    explanations: dict[str, PlanRuleExplanationV1]
    economies: PlanEconomiesV1


class PlanIssueV1(PlanModel):
    code: str
    site_id: str | None
    message: str


class PlanInvalidV1(PlanModel):
    error: Literal["plan_invalid"]
    issues: list[PlanIssueV1]


@cache
def _catalogue_response() -> PlanCatalogueV1:
    return PlanCatalogueV1(
        rulebook_release=RULEBOOK_RELEASE,
        facilities=[
            PlanFacilityV1(
                id=template.id,
                name=template.name,
                category=template.category,
                tier=template.tier,
                allowed_location=template.allowed_location,
                economy=template.economy,
                is_port=template.is_port,
                escalating_port_tier=escalating_port_tier(template),
                yellow_cp_cost=template.yellow_cp_cost,
                green_cp_cost=template.green_cp_cost,
                yellow_cp_generated=template.yellow_cp_generated,
                green_cp_generated=template.green_cp_generated,
                prerequisites=[sorted(entry) for entry in prerequisites_for(template.id)],
                # The first non-primary port's position affects its cost, but
                # never its citations. Reuse the evaluator's citation source.
                rule_ids=list(_cost(template, False, 1)[2]),
            )
            for _, template in sorted(planner_facilities().items())
        ],
    )


@router.get("/catalogue", response_model=PlanCatalogueV1, operation_id="getV3PlanCatalogue")
async def get_catalogue() -> PlanCatalogueV1:
    return _catalogue_response()


@router.get(
    "/systems/{system_id}/bodies",
    response_model=PlanBodiesV1,
    operation_id="listV3PlanBodies",
)
async def bodies(
    system_id: SystemId, pool: asyncpg.Pool = Depends(get_readonly_pool),
) -> PlanBodiesV1:
    numeric_id = _numeric_system_id(system_id)
    schema, generation = await current_canonical_generation(pool)
    rows = await _bounded(pool.fetch(
        f"""SELECT body.body_pk,body.name,body.direct_parent_body_pk,
                   body_type.public_code AS body_type,body.is_main_star,body.is_landable,
                   body.radius_km,body.surface_gravity_g,body.surface_temperature_k,
                   atmosphere.display_name AS atmosphere,
                   terraforming.display_name AS terraforming_state,
                   volcanism.display_name AS volcanism,body.distance_from_arrival_ls,
                   body.signals_complete,body.lifecycle_state
              FROM {schema}.bodies body
              LEFT JOIN v3_vocab.body_type body_type USING(body_type_id)
              LEFT JOIN v3_vocab.atmosphere_classification atmosphere
                     USING(atmosphere_classification_id)
              LEFT JOIN v3_vocab.terraforming_state terraforming USING(terraforming_state_id)
              LEFT JOIN v3_vocab.volcanism_type volcanism USING(volcanism_type_id)
             WHERE body.system_id64=$1 ORDER BY body.body_pk""",
        numeric_id,
    ))
    ring_rows = await _bounded(pool.fetch(
        f"""SELECT ring.body_pk
              FROM {schema}.rings ring
             WHERE ring.system_id64=$1 AND ring.kind='RING'
               AND ring.lifecycle_state='ACTIVE'
             GROUP BY ring.body_pk""",
        numeric_id,
    ))
    signal_rows = await _bounded(pool.fetch(
        f"""SELECT signal.body_pk,
                   COALESCE(SUM(signal.signal_count) FILTER
                       (WHERE signal_type.display_name='Geological'),0) AS geological_signals,
                   COALESCE(SUM(signal.signal_count) FILTER
                       (WHERE signal_type.display_name='Biological'),0) AS biological_signals
              FROM {schema}.body_signal_current signal
              JOIN {schema}.bodies body USING(body_pk)
              JOIN v3_vocab.signal_type signal_type USING(signal_type_id)
             WHERE body.system_id64=$1
               AND signal_type.display_name IN ('Geological','Biological')
             GROUP BY signal.body_pk""",
        numeric_id,
    ))
    belt_rows = await _bounded(pool.fetch(
        f"""SELECT ring.ring_pk,ring.name,ring.body_pk,
                   ring_type.display_name AS ring_type,
                   reserve_type.display_name AS reserve,
                   ring.mass_mt,ring.inner_radius_km,ring.outer_radius_km,
                   ring.lifecycle_state
              FROM {schema}.rings ring
              LEFT JOIN v3_vocab.ring_type ring_type USING(ring_type_id)
              LEFT JOIN v3_vocab.reserve_type reserve_type USING(reserve_type_id)
             WHERE ring.system_id64=$1 AND ring.kind='BELT'
             ORDER BY ring.ring_pk""",
        numeric_id,
    ))
    published = await _bounded(pool.fetchrow("""
        SELECT c.derived_generation_id,d.canonical_generation_id
          FROM v3_meta.current_derived_generation c
          JOIN v3_meta.derived_generation d USING(derived_generation_id)
         WHERE d.lifecycle_state='PUBLISHED'
    """))
    facts_generation_id = None
    mechanics_by_body = {}
    topology = {}
    reserve_level = None
    economy_notes = []
    active_kinds = set()
    generation_reason = prediction_generation_reason(generation.generation_id, published)
    if generation_reason is None:
        assert published is not None
        facts_generation_id = published["derived_generation_id"]
        mechanics_rows = await _bounded(pool.fetch("""
            SELECT body_pk,body_class,spectral_class,luminosity_class,volcanism,
                   terraformable,tidally_locked,reserve_level
              FROM v3_derived.body_mechanics
             WHERE derived_generation_id=$1 AND system_id64=$2
             ORDER BY body_pk
        """, facts_generation_id, numeric_id))
        mechanics_by_body = {row["body_pk"]: row for row in mechanics_rows}
        topology = economy_topology(rows)
        reserve_level, economy_notes = aggregate_reserve_levels(rows, mechanics_by_body, belt_rows)
        active_kinds = {
            economy_kind(mechanics["body_class"], mechanics["spectral_class"])
            for row in rows
            if row["lifecycle_state"] == "ACTIVE"
            and (mechanics := mechanics_by_body.get(row["body_pk"])) is not None
        }
    ringed_bodies = {row["body_pk"] for row in ring_rows}
    signals_by_body = {row["body_pk"]: row for row in signal_rows}
    result = []
    for row in rows:
        signal = signals_by_body.get(row["body_pk"])
        mechanics = mechanics_by_body.get(row["body_pk"])
        parent_id, binary_pair = topology.get(row["body_pk"], (None, None))
        result.append(PlanSystemBodyV1(
            body_id=str(row["body_pk"]),
            name=row["name"],
            direct_parent_body_id=(
                str(row["direct_parent_body_pk"])
                if row["direct_parent_body_pk"] is not None else None
            ),
            body_type=row["body_type"],
            is_main_star=row["is_main_star"],
            is_landable=row["is_landable"],
            radius_km=row["radius_km"],
            surface_gravity_g=row["surface_gravity_g"],
            surface_temperature_k=row["surface_temperature_k"],
            atmosphere=row["atmosphere"],
            terraforming_state=row["terraforming_state"],
            volcanism=row["volcanism"],
            distance_from_arrival_ls=row["distance_from_arrival_ls"],
            has_rings=row["body_pk"] in ringed_bodies,
            geological_signals=signal["geological_signals"] if signal else 0,
            biological_signals=signal["biological_signals"] if signal else 0,
            signals_complete=row["signals_complete"],
            lifecycle_state=row["lifecycle_state"],
            economy_kind=(
                economy_kind(mechanics["body_class"], mechanics["spectral_class"])
                if mechanics is not None else None
            ),
            economy_parent_id=parent_id,
            in_binary_pair=binary_pair,
            tidally_locked=mechanics["tidally_locked"] if mechanics is not None else None,
            volcanism_present=mechanics["volcanism"] if mechanics is not None else None,
            terraformable=mechanics["terraformable"] if mechanics is not None else None,
            **asdict(predict_surface_slots(
                row, mechanics, signal, generation_reason=generation_reason,
            )),
        ))
    return PlanBodiesV1(
        generation_id=generation.generation_id,
        publication_sequence=generation.publication_sequence,
        # Echo the canonical decimal form, so "000123" and "123" answer alike.
        system_id64=str(numeric_id),
        economy_facts_generation_id=str(facts_generation_id) if facts_generation_id is not None else None,
        reserve_level=reserve_level,
        has_black_hole="black_hole" in active_kinds if facts_generation_id is not None else None,
        has_white_dwarf="white_dwarf" in active_kinds if facts_generation_id is not None else None,
        has_neutron_star="neutron_star" in active_kinds if facts_generation_id is not None else None,
        economy_notes=economy_notes,
        bodies=result,
        belts=[
            PlanSystemBeltV1(
                belt_id=str(row["ring_pk"]),
                name=row["name"],
                parent_body_id=str(row["body_pk"]),
                ring_type=row["ring_type"],
                reserve=row["reserve"],
                mass_mt=row["mass_mt"],
                inner_radius_km=row["inner_radius_km"],
                outer_radius_km=row["outer_radius_km"],
                lifecycle_state=row["lifecycle_state"],
            )
            for row in belt_rows
        ],
    )


def _port_economy(port: PortEconomy) -> PortEconomyV1:
    return PortEconomyV1(
        site_id=port.site_id,
        body_ref=port.body_ref,
        role=port.role,
        supports=port.supports,
        economies=[
            EconomyV1(
                name=economy.name,
                strength=economy.strength,
                items=[
                    EconomyItemV1(**{
                        **asdict(item),
                        "modifiers": [
                            EconomyModifierV1(condition=condition, delta=delta)
                            for condition, delta in item.modifiers
                        ],
                        "source_site_ids": list(item.source_site_ids),
                        "rule_ids": list(item.rule_ids),
                    })
                    for item in economy.items
                ],
            )
            for economy in port.economies
        ],
    )


@router.post(
    "/evaluate",
    response_model=PlanEvaluationV1,
    operation_id="evaluateV3Plan",
    # A plan that is well-formed JSON but breaks a planner rule is a 400 with
    # PlanInvalidV1; FastAPI keeps 422 (HTTPValidationError) for malformed
    # bodies, so the OpenAPI contract describes both shapes accurately.
    responses={400: {"model": PlanInvalidV1}},
)
@limiter.limit("60/minute")
async def evaluate(request: Request, body: PlanRequestV1) -> PlanEvaluationV1 | JSONResponse:
    plan = Plan(
        system_id64=int(body.system_id64),
        bodies=tuple(Body(**item.model_dump(exclude={"economy"})) for item in body.bodies),
        sites=tuple(Site(**item.model_dump()) for item in body.sites),
    )
    try:
        evaluation = evaluate_plan(plan)
    except PlanInvalid as exc:
        invalid = PlanInvalidV1(
            error="plan_invalid",
            issues=[PlanIssueV1(**asdict(issue)) for issue in exc.issues],
        )
        return JSONResponse(status_code=400, content=invalid.model_dump())

    economy_bodies = tuple(
        EconomyBody(item.body_ref, **item.economy.model_dump())
        if item.economy is not None else EconomyBody(item.body_ref)
        for item in body.bodies
    )
    economy_system = (
        EconomySystem(**body.system_economy.model_dump())
        if body.system_economy is not None else EconomySystem()
    )
    try:
        economy_now = evaluate_economies(plan.sites, economy_bodies, economy_system, view="now")
        economy_after = evaluate_economies(
            plan.sites, economy_bodies, economy_system, view="after_plan",
        )
    except ValueError as exc:
        invalid = PlanInvalidV1(
            error="plan_invalid",
            issues=[PlanIssueV1(code="ECONOMY_INPUT", site_id=None, message=str(exc))],
        )
        return JSONResponse(status_code=400, content=invalid.model_dump())

    cited = {
        rule_id
        for item in (*evaluation.steps, *evaluation.plan_checks)
        for rule_id in item.rule_ids
    }
    cited.update(
        rule_id
        for port in (*economy_now.ports, *economy_after.ports)
        for economy in port.economies
        for item in economy.items
        for rule_id in item.rule_ids
    )
    return PlanEvaluationV1(
        rulebook_release=evaluation.rulebook_release,
        system_id64=body.system_id64,
        now=PlanTotalsV1(**asdict(evaluation.now)),
        after_plan=PlanTotalsV1(**asdict(evaluation.after_plan)),
        next_port_cost=PlanTotalsV1(**asdict(evaluation.next_port_cost)),
        steps=[
            PlanStepV1(**{
                **asdict(step),
                "reasons": list(step.reasons),
                "rule_ids": list(step.rule_ids),
                "payers": list(step.payers),
            })
            for step in evaluation.steps
        ],
        plan_checks=[
            PlanCheckV1(code=check.code, message=check.message, rule_ids=list(check.rule_ids))
            for check in evaluation.plan_checks
        ],
        summary=PlanSummaryV1(**evaluation.summary),
        explanations={
            rule_id: PlanRuleExplanationV1(**explain_rule(rule_id))
            for rule_id in sorted(cited)
        },
        economies=PlanEconomiesV1(
            now=[_port_economy(port) for port in economy_now.ports],
            after_plan=[_port_economy(port) for port in economy_after.ports],
            notes=[
                EconomyNoteV1(**asdict(note))
                for note in dict.fromkeys((*economy_now.notes, *economy_after.notes))
            ],
        ),
    )
