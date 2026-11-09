from __future__ import annotations

import logging

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from edfinder_api.deps import get_pool
from edfinder_api.warehouse_planner_evidence import build_warehouse_planner_evidence
from edfinder_api.warehouse_planner_evidence_models import WarehousePlannerEvidenceContract
from edfinder_api.warehouse_planner_evidence_provider import (
    PlannerEvidenceQueryError,
    load_live_planner_evidence,
)

log = logging.getLogger('ed_finder')


router = APIRouter(tags=['colony-planner'])


@router.get(
    '/api/colony-planner/system/{id64}/warehouse-planner-evidence',
    response_model=WarehousePlannerEvidenceContract,
    include_in_schema=False,
)
async def warehouse_planner_evidence(
    id64: int,
    pool: asyncpg.Pool = Depends(get_pool),
) -> WarehousePlannerEvidenceContract:
    try:
        live_result = await load_live_planner_evidence(pool, id64)
    except PlannerEvidenceQueryError as exc:
        # A timed-out or failed evidence query is an error, not "no evidence".
        # The detail is the bounded message (class name + SQLSTATE only).
        log.warning('warehouse planner evidence query failed for %s: %s', id64, exc)
        raise HTTPException(503, detail=str(exc)) from exc
    return build_warehouse_planner_evidence(id64, live_result=live_result)
