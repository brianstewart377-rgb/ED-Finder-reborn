import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from edfinder_api.config import limiter
from edfinder_api.deps import get_pool, get_readonly_pool
from edfinder_api.powerplay import store
from edfinder_api.powerplay.api_models import (
    CommanderPowerplayResponse,
    PowerplayHistoryResponse,
    PowerplayImportReceipt,
    PowerplayImportRequest,
    PowerplaySystemsResponse,
    validate_commander_key,
)

router = APIRouter(prefix='/api/powerplay', tags=['powerplay'])


def _commander_key(value: str) -> str:
    try:
        return validate_commander_key(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/import', response_model=PowerplayImportReceipt)
@limiter.limit('10/minute')
async def import_powerplay_journal(
    request: Request,
    body: PowerplayImportRequest,
    pool: asyncpg.Pool = Depends(get_pool),
) -> PowerplayImportReceipt:
    del request
    return await store.import_powerplay_events(pool, body)


@router.get('/systems', response_model=PowerplaySystemsResponse)
@limiter.limit('60/minute')
async def powerplay_systems(
    request: Request,
    commander_key: str = Query(..., min_length=16, max_length=128),
    limit: int = Query(10_000, ge=1, le=40_000),
    pool: asyncpg.Pool = Depends(get_readonly_pool),
) -> PowerplaySystemsResponse:
    del request
    return await store.get_current_systems(pool, _commander_key(commander_key), limit=limit)


@router.get('/commander', response_model=CommanderPowerplayResponse)
@limiter.limit('60/minute')
async def powerplay_commander(
    request: Request,
    commander_key: str = Query(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_readonly_pool),
) -> CommanderPowerplayResponse:
    del request
    return await store.get_commander_state(pool, _commander_key(commander_key))


@router.get('/history', response_model=PowerplayHistoryResponse)
@limiter.limit('30/minute')
async def powerplay_history(
    request: Request,
    commander_key: str = Query(..., min_length=16, max_length=128),
    cycle_limit: int = Query(52, ge=1, le=260),
    change_limit: int = Query(2_000, ge=1, le=20_000),
    pool: asyncpg.Pool = Depends(get_readonly_pool),
) -> PowerplayHistoryResponse:
    del request
    return await store.get_history(
        pool,
        _commander_key(commander_key),
        cycle_limit=cycle_limit,
        change_limit=change_limit,
    )
