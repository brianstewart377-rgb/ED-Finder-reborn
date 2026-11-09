from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from edfinder_api.config import limiter, log, settings
from edfinder_api.deps import get_pool
from edfinder_api.local_search import _current_derived_generation
from edfinder_api.ranking.profile import ARCHETYPE_KEYS, RANKING_VERSION
from edfinder_api.review_provenance_cockpit import router as review_provenance_cockpit_router
from edfinder_api.review_runtime_guard import validate_review_runtime_env
from edfinder_api.review_support_routes import router as review_support_router
from edfinder_api.review_warehouse_planner_evidence import router as review_warehouse_planner_evidence_router
from edfinder_api.routers.colony_planner import router as colony_planner_router
from edfinder_api.routers.archetypes import _ranking_identity, _V3_ARCHETYPE_LABELS
from edfinder_api.routers.map import router as map_router
from edfinder_api.routers.meta import router as meta_router
from edfinder_api.routers.search import router as search_router
from edfinder_api.routers.simulate import router as simulate_router
from edfinder_api.routers.simulation import router as simulation_router
from edfinder_api.routers.systems import router as systems_router
from edfinder_api.routers.watchlist import router as watchlist_router
from edfinder_api.state import metrics as _metrics, set_pool, set_redis


validate_review_runtime_env(dict(os.environ))


@asynccontextmanager
async def lifespan(app: FastAPI):
    async def _init_conn(conn: asyncpg.Connection) -> None:
        import json as _json

        await conn.set_type_codec(
            'jsonb',
            encoder=_json.dumps,
            decoder=_json.loads,
            schema='pg_catalog',
        )
        await conn.set_type_codec(
            'json',
            encoder=_json.dumps,
            decoder=_json.loads,
            schema='pg_catalog',
        )

    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=8,
        command_timeout=300,
        statement_cache_size=0,
        server_settings={
            'application_name': 'ed_finder_review_api',
            'statement_timeout': str(settings.statement_timeout_ms),
        },
        init=_init_conn,
    )
    set_pool(pool)

    redis = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=settings.redis_max_connections,
        socket_connect_timeout=3,
        socket_timeout=3,
        retry_on_timeout=True,
    )
    try:
        await redis.ping()
        set_redis(redis)
    except Exception:
        await redis.aclose()
        set_redis(None)
        redis = None

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch('SELECT * FROM facility_templates ORDER BY tier, id')
        from edfinder_api.domain.facilities import load_bundled_catalogue, load_catalogue_from_rows

        if rows:
            load_catalogue_from_rows([dict(row) for row in rows])
        else:
            load_bundled_catalogue()
    except Exception:
        from edfinder_api.domain.facilities import load_bundled_catalogue
        load_bundled_catalogue()

    app.state.pool = pool
    app.state.redis = redis
    app.state.review_scenario = 'normal'
    log.info('Isolated review API ready')
    yield

    if pool:
        await pool.close()
    if redis:
        await redis.aclose()


app = FastAPI(
    title='ED Finder Review API',
    version=settings.app_version,
    description='Isolated local review environment API.',
    lifespan=lifespan,
    docs_url='/docs',
    redoc_url='/redoc',
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(',') if o.strip()],
    allow_methods=['GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS'],
    allow_headers=['Content-Type', 'X-Admin-Token', 'Authorization'],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware('http')
async def review_scenario_middleware(request: Request, call_next: Any) -> Response:
    """Inject only Review Lab-owned synthetic search states.

    The normal application router remains untouched. These modes exist only in
    review_main.py, which is guarded to the disposable Review Lab runtime.
    """
    mode = getattr(request.app.state, 'review_scenario', 'normal')
    if request.method == 'GET' and request.url.path == '/api/archetypes/rankings':
        if mode == 'rankings_api_failure':
            return JSONResponse(
                status_code=503,
                media_type='application/problem+json',
                headers={'x-edfinder-review-failure': 'rankings-api-failure'},
                content={
                    'type': 'https://ed-finder.invalid/problem/review-lab-rankings-failure',
                    'title': 'Synthetic Review Lab rankings failure',
                    'status': 503,
                },
            )
        if mode == 'rankings_empty_results':
            archetype = request.query_params.get('archetype')
            if archetype not in ARCHETYPE_KEYS:
                return JSONResponse(status_code=422, content={'detail': 'Unsupported archetype'})
            # The empty page is synthetic; its identity still comes from the
            # isolated published fixture, using the ordinary product gates.
            pool = await get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction(isolation='repeatable_read', readonly=True):
                    generation = await _current_derived_generation(conn)
            return JSONResponse(
                status_code=200,
                content={
                    'archetype': archetype,
                    'archetype_label': _V3_ARCHETYPE_LABELS[archetype],
                    'results': [],
                    'total': 0,
                    'count': 0,
                    'is_truncated': False,
                    'source': f'v3:{RANKING_VERSION}',
                    'query_ms': 0,
                    'ranking': _ranking_identity(generation),
                },
            )
    if request.method == 'POST' and request.url.path == '/api/local/search':
        if mode == 'api_failure':
            return JSONResponse(
                status_code=503,
                media_type='application/problem+json',
                headers={'x-edfinder-review-failure': 'api-failure'},
                content={
                    'type': 'https://ed-finder.invalid/problem/review-lab-search-failure',
                    'title': 'Synthetic Review Lab search failure',
                    'status': 503,
                },
            )
        if mode == 'empty_results':
            return JSONResponse(
                status_code=200,
                content={
                    'results': [],
                    'total': 0,
                    'count': 0,
                    'source': 'review_lab_synthetic_empty',
                },
            )
        # Normal mode reaches the production V3 search router and its published
        # generation/READY-product gates, using the isolated seeded database.
    return await call_next(request)


@app.middleware('http')
async def metrics_middleware(request: Request, call_next: Any) -> Response:
    _metrics['requests_total'] += 1
    start = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start) * 1000
    if duration_ms > 2000:
        log.warning('Slow review request %s %s: %.0fms', request.method, request.url.path, duration_ms)
    return response


@app.exception_handler(HTTPException)
async def problem_details_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            'type': f'https://httpstatuses.com/{exc.status_code}',
            'title': exc.detail if isinstance(exc.detail, str) else 'Error',
            'status': exc.status_code,
            'detail': exc.detail,
            'instance': str(request.url),
        },
        headers=getattr(exc, 'headers', None),
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    _metrics['errors_total'] += 1
    log.exception('Unhandled review error on %s %s', request.method, request.url)
    detail = str(exc) if settings.expose_error_detail else 'Internal server error'
    return JSONResponse(
        status_code=500,
        content={
            'type': 'https://httpstatuses.com/500',
            'title': 'Internal Server Error',
            'status': 500,
            'detail': detail,
        },
    )


app.include_router(meta_router)
app.include_router(search_router)
app.include_router(systems_router)
app.include_router(simulate_router)
app.include_router(simulation_router)
app.include_router(review_support_router)
app.include_router(watchlist_router)
app.include_router(colony_planner_router)
app.include_router(map_router)
app.include_router(review_provenance_cockpit_router)
app.include_router(review_warehouse_planner_evidence_router)
