from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from edfinder_api.deps import get_pool
from edfinder_api.review_contract_store import load_review_warehouse_contract
from edfinder_api.review_environment_fixtures import REVIEW_FALLBACK_SYSTEM_ID64
from edfinder_api.warehouse_planner_evidence import build_warehouse_planner_evidence
from edfinder_api.warehouse_planner_evidence_models import WarehousePlannerEvidenceContract
from edfinder_api.warehouse_planner_evidence_provider import (
    PlannerEvidenceQueryError,
    load_live_planner_evidence,
)


router = APIRouter(tags=['colony-planner'])


@router.get(
    '/api/colony-planner/system/{id64}/warehouse-planner-evidence',
    response_model=WarehousePlannerEvidenceContract,
    include_in_schema=False,
)
async def warehouse_planner_evidence(
    id64: int,
    pool: asyncpg.Pool = Depends(get_pool),
) -> WarehousePlannerEvidenceContract | JSONResponse:
    if id64 == REVIEW_FALLBACK_SYSTEM_ID64:
        return JSONResponse(
            status_code=503,
            media_type='application/problem+json',
            content={
                'type': 'https://ed-finder.local/problem/review-fallback-dedicated-evidence-unavailable',
                'title': 'Dedicated warehouse evidence intentionally unavailable in review runtime',
                'status': 503,
                'detail': (
                    'Review Fallback keeps the dedicated warehouse evidence route unreadable in the '
                    'isolated review runtime so the existing provenance fallback can be exercised.'
                ),
                'system_id64': id64,
                'review_runtime_only': True,
                'fallback_route': f'/api/colony-planner/system/{id64}/provenance-cockpit',
            },
        )
    review_contract = await load_review_warehouse_contract(pool, id64)
    if review_contract is not None:
        return review_contract
    try:
        live_result = await load_live_planner_evidence(pool, id64)
    except PlannerEvidenceQueryError as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    return build_warehouse_planner_evidence(id64, live_result=live_result)
