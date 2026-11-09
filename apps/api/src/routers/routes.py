from datetime import datetime

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from edfinder_api.config import limiter
from edfinder_api.deps import get_pool
from edfinder_api.routes import store
from edfinder_api.routes.api_models import (
    ExpeditionImport,
    PlannedRouteImport,
    RouteDetail,
    RouteListResponse,
    validate_commander_id,
)

router = APIRouter(tags=['routes'])


def _scope(value: str) -> str:
    try:
        return validate_commander_id(value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/api/routes/list', response_model=RouteListResponse)
@limiter.limit('60/minute')
async def list_commander_routes(
    request: Request,
    commander_id: str = Query(..., min_length=16, max_length=128),
    route_type: str | None = Query(default=None, alias='type'),
    pool: asyncpg.Pool = Depends(get_pool),
) -> RouteListResponse:
    del request
    if route_type not in {None, 'personal', 'journal', 'spansh', 'expedition'}:
        raise HTTPException(400, 'type must be personal, journal, spansh, or expedition')
    return await store.list_routes(pool, _scope(commander_id), route_type)


@router.get('/api/routes/trail', response_model=RouteDetail)
@limiter.limit('60/minute')
async def get_personal_jump_trail(
    request: Request,
    commander_id: str = Query(..., min_length=16, max_length=128),
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    pool: asyncpg.Pool = Depends(get_pool),
) -> RouteDetail:
    del request
    detail = await store.get_personal_trail(pool, _scope(commander_id), from_date, to_date)
    if detail is None:
        raise HTTPException(404, 'No personal jump trail has been imported for this commander')
    return detail


@router.get('/api/routes/expeditions', response_model=RouteListResponse)
@limiter.limit('60/minute')
async def list_expeditions(
    request: Request,
    commander_id: str = Query(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
) -> RouteListResponse:
    del request
    return await store.list_routes(pool, _scope(commander_id), 'expedition')


@router.post('/api/routes/expeditions', response_model=RouteDetail)
@limiter.limit('20/minute')
async def save_expedition(
    request: Request,
    body: ExpeditionImport,
    pool: asyncpg.Pool = Depends(get_pool),
) -> RouteDetail:
    del request
    return await store.import_expedition(pool, body)


@router.post('/api/routes/import/spansh', response_model=RouteDetail)
@limiter.limit('20/minute')
async def import_spansh_route(
    request: Request,
    body: PlannedRouteImport,
    pool: asyncpg.Pool = Depends(get_pool),
) -> RouteDetail:
    del request
    return await store.import_spansh_route(pool, body)


@router.get('/api/routes/{route_id}', response_model=RouteDetail)
@limiter.limit('60/minute')
async def get_route_detail(
    request: Request,
    route_id: str = Path(..., min_length=36, max_length=36),
    commander_id: str = Query(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
) -> RouteDetail:
    del request
    detail = await store.get_route(pool, route_id, _scope(commander_id))
    if detail is None:
        raise HTTPException(404, f'Route {route_id} not found')
    return detail
