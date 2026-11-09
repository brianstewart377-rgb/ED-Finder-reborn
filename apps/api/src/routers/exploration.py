import math
from datetime import datetime

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from edfinder_api.config import limiter
from edfinder_api.deps import get_pool
from edfinder_api.exploration.api_models import (
    _SYNC_KEY_RE,
    ALLOWED_EXPLORATION_EVENT_TYPES,
    ExplorationCodexByRegionResponse,
    ExplorationFactsResponse,
    ExplorationImportReceipt,
    ExplorationImportRequest,
    ExplorationSystemSummaryResponse,
    ExplorationTrailResponse,
    ExplorationViewportVisitsResponse,
)
from edfinder_api.exploration import store

router = APIRouter(tags=['exploration'])


def _validate_sync_key(sync_key: str) -> str:
    stripped = sync_key.strip()
    if stripped == 'legacy':
        raise HTTPException(400, 'sync_key="legacy" is reserved for migration')
    if not _SYNC_KEY_RE.match(stripped):
        raise HTTPException(400, 'sync_key must be 16-128 chars, alphanumeric + "_" or "-" only.')
    return stripped


@router.post('/api/exploration/import', response_model=ExplorationImportReceipt)
@limiter.limit('10/minute')
async def import_exploration(
    request: Request,
    body: ExplorationImportRequest,
    pool: asyncpg.Pool = Depends(get_pool),
) -> ExplorationImportReceipt:
    try:
        return await store.import_exploration_batch(pool, body)
    except store.ExplorationImportRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.get('/api/exploration/facts/{sync_key}', response_model=ExplorationFactsResponse)
@limiter.limit('60/minute')
async def get_exploration_facts_for_sync_key(
    request: Request,
    sync_key: str = Path(..., min_length=16, max_length=128),
    limit: int = Query(store.DEFAULT_FACTS_LIMIT, ge=1, le=store.MAX_FACTS_LIMIT),
    cursor: str | None = Query(None, max_length=512),
    event_type: list[str] | None = Query(None),
    system_id64: int | None = Query(None, gt=0),
    from_at: datetime | None = Query(None),
    to_at: datetime | None = Query(None),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ExplorationFactsResponse:
    del request
    validated_sync_key = _validate_sync_key(sync_key)
    invalid_types = set(event_type or ()) - ALLOWED_EXPLORATION_EVENT_TYPES
    if invalid_types:
        raise HTTPException(422, f'Unsupported exploration event types: {sorted(invalid_types)}')
    if from_at is not None and to_at is not None and from_at > to_at:
        raise HTTPException(422, 'from_at must not be later than to_at')
    try:
        return await store.get_exploration_facts(
            pool, validated_sync_key, limit=limit, cursor=cursor,
            event_types=event_type, system_id64=system_id64, from_at=from_at, to_at=to_at,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/api/exploration/trail', response_model=ExplorationTrailResponse)
@limiter.limit('60/minute')
async def get_exploration_trail(
    request: Request,
    sync_key: str = Query(..., min_length=16, max_length=128),
    limit: int = Query(store.DEFAULT_TRAIL_LIMIT, ge=1, le=store.MAX_TRAIL_LIMIT),
    cursor: int | None = Query(None, ge=1),
    from_at: datetime | None = Query(None),
    to_at: datetime | None = Query(None),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ExplorationTrailResponse:
    del request
    if from_at is not None and to_at is not None and from_at > to_at:
        raise HTTPException(422, 'from_at must not be later than to_at')
    return await store.get_exploration_trail(
        pool, _validate_sync_key(sync_key), limit=limit, cursor=cursor,
        from_at=from_at, to_at=to_at,
    )


@router.get('/api/exploration/viewport-visits', response_model=ExplorationViewportVisitsResponse)
@limiter.limit('60/minute')
async def get_exploration_viewport_visits(
    request: Request,
    sync_key: str = Query(..., min_length=16, max_length=128),
    min_x: float = Query(...),
    max_x: float = Query(...),
    min_y: float = Query(...),
    max_y: float = Query(...),
    min_z: float = Query(...),
    max_z: float = Query(...),
    zoom: float = Query(..., gt=0, le=5_000),
    limit: int = Query(20_000, ge=1, le=store.MAX_VIEWPORT_VISITS),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ExplorationViewportVisitsResponse:
    del request
    bounds = (min_x, max_x, min_y, max_y, min_z, max_z, zoom)
    if not all(math.isfinite(value) for value in bounds):
        raise HTTPException(422, 'viewport bounds and zoom must be finite')
    return await store.get_viewport_visits(
        pool, _validate_sync_key(sync_key), min_x=min_x, max_x=max_x,
        min_y=min_y, max_y=max_y, min_z=min_z, max_z=max_z,
        zoom=zoom, limit=limit,
    )


@router.get('/api/exploration/summary', response_model=ExplorationSystemSummaryResponse)
@limiter.limit('60/minute')
async def get_exploration_summary(
    request: Request,
    sync_key: str = Query(..., min_length=16, max_length=128),
    system_id64: int = Query(..., gt=0),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ExplorationSystemSummaryResponse:
    del request
    return await store.get_exploration_summary(pool, _validate_sync_key(sync_key), system_id64)


@router.get('/api/exploration/codex-by-region', response_model=ExplorationCodexByRegionResponse)
@limiter.limit('30/minute')
async def get_exploration_codex_by_region(
    request: Request,
    sync_key: str = Query(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ExplorationCodexByRegionResponse:
    del request
    return await store.get_codex_by_region(pool, _validate_sync_key(sync_key))
