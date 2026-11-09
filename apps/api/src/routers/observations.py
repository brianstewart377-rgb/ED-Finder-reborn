import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from edfinder_api.config import limiter
from edfinder_api.deps import get_pool, require_admin
from edfinder_api.observations import store
from edfinder_api.observations.api_models import (
    ObservedFactCreateRequest,
    ObservedFactDeleteResponse,
    ObservedFactListResponse,
    ObservedFactResponse,
    ObservedFactUpdateRequest,
    ObservationFactSummaryResponse,
    PredictionObservationCompareRequest,
    PredictionObservationCompareResponse,
    ValidationReviewRequest,
    ValidationReviewResponse,
)
# Stage 6C: import the comparison engine directly from its submodule so we
# do NOT accidentally pick up the Stage 4D ``compare_prediction_to_observations``
# re-exported by ``observations/__init__.py``. The Stage 6C engine has a
# different signature and a different result shape.
from edfinder_api.observations.comparison_engine import (
    compare_prediction_to_observations as compare_prediction_to_observations_stage6c,
)
from edfinder_api.observations.comparison_models import (
    comparison_result_from_dict,
    comparison_result_to_dict,
)
from edfinder_api.observations.review_engine import build_validation_review
from edfinder_api.observations.review_models import review_result_to_dict

router = APIRouter(tags=['observations'])

_OPERATOR_MUTATION_LIMIT = '20/minute'


async def _load_or_convert_comparison_facts(
    *,
    body: PredictionObservationCompareRequest | ValidationReviewRequest,
    pool: asyncpg.Pool,
):
    """Shared Stage 6C/6E fact-loading semantics."""
    if body.observed_facts is None:
        facts, _total = await store.list_observed_facts_for_comparison(
            pool,
            system_id64=body.system_id64,
            target_archetype=body.target_archetype,
            limit=body.fact_load_limit,
            offset=0,
        )
        return list(facts)
    return [fact_input.to_persisted() for fact_input in body.observed_facts]


@router.post(
    '/api/observations/facts',
    response_model=ObservedFactResponse,
    dependencies=[Depends(require_admin)],
)
@limiter.limit(_OPERATOR_MUTATION_LIMIT)
async def create_observed_fact(
    request: Request,
    body: ObservedFactCreateRequest,
    pool: asyncpg.Pool = Depends(get_pool),
) -> ObservedFactResponse:
    fact = await store.create_observed_fact(pool, body)
    return ObservedFactResponse.from_domain(fact)


@router.get('/api/observations/facts', response_model=ObservedFactListResponse)
async def list_observed_facts(
    system_id64: int = Query(..., gt=0),
    fact_type: str | None = None,
    subject_type: str | None = None,
    status: str | None = None,
    target_archetype: str | None = None,
    build_fingerprint: str | None = None,
    simulation_fingerprint: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ObservedFactListResponse:
    facts, total = await store.list_observed_facts(
        pool,
        system_id64=system_id64,
        fact_type=fact_type,
        subject_type=subject_type,
        status=status,
        target_archetype=target_archetype,
        build_fingerprint=build_fingerprint,
        simulation_fingerprint=simulation_fingerprint,
        limit=limit,
        offset=offset,
    )
    # Summary must describe the full filtered result set, not just the
    # paginated page returned above. We re-run the same filter without
    # limit/offset so total and summary describe identical row sets.
    summary = await store.summarise_observed_facts_for_filter(
        pool,
        system_id64=system_id64,
        fact_type=fact_type,
        subject_type=subject_type,
        status=status,
        target_archetype=target_archetype,
        build_fingerprint=build_fingerprint,
        simulation_fingerprint=simulation_fingerprint,
    )
    return ObservedFactListResponse(
        facts=[ObservedFactResponse.from_domain(fact) for fact in facts],
        total=total,
        limit=limit,
        offset=offset,
        summary=ObservationFactSummaryResponse.from_domain(summary),
    )


@router.get('/api/observations/facts/{observation_id}', response_model=ObservedFactResponse)
async def get_observed_fact(
    observation_id: str,
    pool: asyncpg.Pool = Depends(get_pool),
) -> ObservedFactResponse:
    fact = await store.get_observed_fact(pool, observation_id)
    if fact is None:
        raise HTTPException(404, f'Observed fact {observation_id} not found')
    return ObservedFactResponse.from_domain(fact)


@router.patch(
    '/api/observations/facts/{observation_id}',
    response_model=ObservedFactResponse,
    dependencies=[Depends(require_admin)],
)
@limiter.limit(_OPERATOR_MUTATION_LIMIT)
async def update_observed_fact(
    request: Request,
    observation_id: str,
    body: ObservedFactUpdateRequest,
    pool: asyncpg.Pool = Depends(get_pool),
) -> ObservedFactResponse:
    fact = await store.update_observed_fact(pool, observation_id, body)
    if fact is None:
        raise HTTPException(404, f'Observed fact {observation_id} not found')
    return ObservedFactResponse.from_domain(fact)


@router.delete(
    '/api/observations/facts/{observation_id}',
    response_model=ObservedFactDeleteResponse,
    dependencies=[Depends(require_admin)],
)
@limiter.limit(_OPERATOR_MUTATION_LIMIT)
async def delete_observed_fact(
    request: Request,
    observation_id: str,
    pool: asyncpg.Pool = Depends(get_pool),
) -> ObservedFactDeleteResponse:
    deleted = await store.delete_observed_fact(pool, observation_id)
    if not deleted:
        raise HTTPException(404, f'Observed fact {observation_id} not found')
    return ObservedFactDeleteResponse(observation_id=observation_id, deleted=True)


# ──────────────────────────────────────────────────────────────────────
# Stage 6C — predicted-vs-observed comparison endpoint
# ──────────────────────────────────────────────────────────────────────
@router.post(
    '/api/observations/compare',
    response_model=PredictionObservationCompareResponse,
)
async def compare_prediction_against_observations(
    body: PredictionObservationCompareRequest,
    pool: asyncpg.Pool = Depends(get_pool),
) -> PredictionObservationCompareResponse:
    """Run the Stage 6C deterministic comparison engine.

    Two modes:

    * **Mode A** — ``observed_facts`` omitted from the request. The
      router loads up to ``fact_load_limit`` persisted facts for
      ``system_id64`` via ``store.list_observed_facts_for_comparison``
      and passes them to the engine.

      Fact-loading semantics (Stage 6C hardening):
        * If ``target_archetype`` is omitted/null in the request, all
          facts for the system are loaded.
        * If ``target_archetype`` is supplied, facts are included when
          their ``target_archetype`` matches the requested value **or**
          is ``NULL`` — i.e. system-level general evidence (notes,
          service evidence, economy evidence, CP observations not tied
          to a specific target) is included alongside target-specific
          evidence. Facts targeting a *different* explicit archetype
          are excluded.
    * **Mode B** — ``observed_facts`` provided. The supplied list is
      converted to ``PersistedObservedFact`` objects and passed to the
      engine verbatim. The database is NOT queried for facts in this
      mode.

      Source semantics (Stage 6C hardening): Mode B supplied facts may
      carry any ``ObservationSource`` value including ``imported`` and
      ``inferred``. This is deliberate — Mode B is read-only and the
      supplied facts are never persisted, so Stage 6A's reserved-source
      restriction (manual/test_fixture only on create/update) does not
      apply. Persistence still goes exclusively through Stage 6A's
      ``POST /api/observations/facts`` endpoint and remains restricted.

    The handler is **passive**: it never invokes simulation, optimiser,
    or ranking code, and never mutates persisted observations. It only
    reads observations (Mode A) and runs the pure comparison engine.
    """
    observed = await _load_or_convert_comparison_facts(body=body, pool=pool)

    result = compare_prediction_to_observations_stage6c(
        system_id64=body.system_id64,
        target_archetype=body.target_archetype,
        prediction=body.prediction,
        observed_facts=observed,
    )

    # ``comparison_result_to_dict`` produces JSON-safe primitives that
    # round-trip directly into the response Pydantic model.
    return PredictionObservationCompareResponse.model_validate(
        comparison_result_to_dict(result),
    )


@router.post(
    '/api/observations/review',
    response_model=ValidationReviewResponse,
)
async def review_prediction_validation(
    body: ValidationReviewRequest,
    pool: asyncpg.Pool = Depends(get_pool),
) -> ValidationReviewResponse:
    """Build Stage 6E review guidance from a Stage 6C comparison.

    A caller may pass ``comparison_result`` to reuse work already done by
    the compare endpoint. Legacy/direct callers may instead pass the
    compare inputs, in which case this handler retains the same Mode A/B
    fact-loading semantics. It does not run Simulation Preview, optimiser
    generation, optimiser ranking, or any mechanics module.
    """
    if body.comparison_result is not None:
        comparison_result = comparison_result_from_dict(
            body.comparison_result.model_dump(),
        )
    else:
        observed = await _load_or_convert_comparison_facts(body=body, pool=pool)
        comparison_result = compare_prediction_to_observations_stage6c(
            system_id64=body.system_id64,
            target_archetype=body.target_archetype,
            prediction=body.prediction or {},
            observed_facts=observed,
        )
    review_result = build_validation_review(comparison_result=comparison_result)
    return ValidationReviewResponse.model_validate(review_result_to_dict(review_result))
