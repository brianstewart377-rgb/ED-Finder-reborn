#!/usr/bin/env python3
"""
ED Finder — API Backend
Version: 3.1 (PostgreSQL 16 / asyncpg)

This file is the **composition root**. It wires config, state, middleware,
lifespan, exception handlers, and router mounts. All business logic lives
in routers/ and helpers/.

Endpoint surface (see individual router docstrings for detail):

  routers/meta.py       health, status, local/status, metrics
  routers/operator.py   read-only source-run/operator visibility
  routers/watchlist.py  watchlist CRUD + changelog
  routers/notes.py      per-system user notes
  routers/events.py     EDDN SSE live feed + recent
  routers/ratings_v4.py sealed V4 generation and independent economy scores
  routers/admin.py      cache stats/clear + cluster-rebuild trigger
  routers/search.py     autocomplete + local/galaxy/cluster search
  routers/systems.py    per-system / per-body detail + batch lookup
  routers/map.py        galaxy regions, cluster hulls, heatmap, timeline
  routers/archetypes.py archetype rankings, rerank, system detail, simulate, profiles
  share_router.py       /s/{id64} OG-tagged share preview + PNG
"""
import asyncio
import os
import pathlib as _pl
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import asyncpg
import sentry_sdk
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse as _FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

# Shared config, state, deps
from edfinder_api.config import settings, log, limiter
from edfinder_api.rate_limits import default_rate_limit, rate_limit_exceeded_handler
from edfinder_api.request_logging import redact_frontier_oauth_target
from edfinder_api.deps import connect_redis
from edfinder_api.state import (
    get_redis_singleton,
    metrics as _metrics,
    observe_request_duration,
    set_pool,
    set_readonly_pool,
    set_redis,
)

# Routers
from edfinder_api.routers.admin import router as admin_router, reap_stale_admin_operation_runs
from edfinder_api.routers.auth import (
    frontier_callback_compat_router,
    router as auth_router,
)
from edfinder_api.routers.archetypes import router as archetypes_router
from edfinder_api.routers.colony_planner import router as colony_planner_router
from edfinder_api.routers.evidence import router as evidence_router
from edfinder_api.ingest.eddn_client import run_eddn_simulation_ingest
from edfinder_api.routers.events import router as events_router, eddn_pubsub_bridge
from edfinder_api.routers.journal_import import router as journal_import_router
from edfinder_api.routers.exploration import router as exploration_router
from edfinder_api.routers.map import router as map_router
from edfinder_api.routers.meta import router as meta_router
from edfinder_api.routers.news import router as news_router
from edfinder_api.routers.notes import router as notes_router
from edfinder_api.routers.observations import router as observations_router
from edfinder_api.routers.operator import router as operator_router
from edfinder_api.routers.optimiser import router as optimiser_router
from edfinder_api.routers.profile import router as profile_router
from edfinder_api.routers.ratings_v4 import router as ratings_v4_router
from edfinder_api.routers.powerplay import router as powerplay_router
from edfinder_api.routers.routes import router as routes_router
from edfinder_api.routers.provenance_cockpit import router as provenance_cockpit_router
from edfinder_api.routers.search import router as search_router
from edfinder_api.routers.simulate import router as simulate_router
from edfinder_api.routers.simulation import router as simulation_router
from edfinder_api.routers.systems import router as systems_router
from edfinder_api.routers.warehouse_planner_evidence import router as warehouse_planner_evidence_router
from edfinder_api.routers.watchlist import router as watchlist_router
from edfinder_api.routers.v3 import router as v3_router
from edfinder_api.routers.plan import router as plan_router
from edfinder_api.routers.v3_journal import router as v3_journal_router
from edfinder_api.routers.v3_journal_research import router as v3_journal_research_router
from edfinder_api.routers.v3_journal_contributions import router as v3_journal_contributions_router
from edfinder_api.share_router import router as share_router

# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------
_sse_pubsub_task: Optional[asyncio.Task] = None
_eddn_simulation_ingest_task: Optional[asyncio.Task] = None


async def _reap_stale_admin_runs_on_startup(pool: asyncpg.Pool) -> None:
    """Run the normal admin-operation recovery unless explicitly disabled."""
    if not settings.admin_operation_startup_reap_enabled:
        log.info('Startup admin operation reaping disabled by configuration')
        return

    try:
        reaped = await reap_stale_admin_operation_runs(pool)
        if reaped:
            log.warning('Reaped %d stale admin operation runs during startup', reaped)
    except asyncpg.exceptions.UndefinedTableError:
        log.warning('admin_job_runs table missing during startup reap; skipping stale admin run cleanup')


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _sse_pubsub_task, _eddn_simulation_ingest_task
    log.info(f"ED Finder API backend v{settings.app_version} starting ...")

    async def _init_conn(conn: asyncpg.Connection) -> None:
        """Per-connection initialiser.

        Audit fix (2026-05-08, AUDIT_REPORT.md §Phase 6): without this
        codec, asyncpg returns JSONB columns as strings, which made
        /api/profile/sync round-trip a stringified JSON instead of the
        original dict. Registering the codec at pool-init time fixes
        every JSONB read across the app.

        NOTE: `statement_timeout` is NOT set here — pgBouncer's
        transaction-pool mode wipes session-level SETs when it returns
        a server connection to its own pool (verified in prod
        2026-05-09: `SHOW statement_timeout` came back `0` over a
        pooled connection). Instead, it lives in the
        `server_settings={'statement_timeout': …}` startup parameters
        on `asyncpg.create_pool()` above, which pgBouncer preserves.
        """
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

    async def _create_pool(
        dsn: str,
        *,
        application_name: str,
        min_size: int,
        max_size: int,
    ) -> asyncpg.Pool:
        return await asyncpg.create_pool(
            dsn=dsn,
            min_size=min_size,
            max_size=max_size,
            # asyncpg's client-side ceiling — kept generous as a final
            # backstop. The real query budget is enforced server-side via
            # `statement_timeout` below.
            command_timeout=300,
            # pgBouncer transaction-pool mode requires prepared-statement cache off.
            statement_cache_size=0,
            server_settings={
                'application_name': application_name,
                # `statement_timeout` MUST live here, not in `init=_init_conn`.
                # asyncpg sends `server_settings` as protocol-level startup
                # parameters which pgBouncer transaction-pool mode preserves
                # across pooled connections, whereas a `SET statement_timeout`
                # issued from the init callback is wiped by pgBouncer's
                # implicit `DISCARD ALL` / `RESET` between transactions.
                # Verified in prod 2026-05-09: `SHOW statement_timeout` over
                # an asyncpg+pgbouncer pooled conn returned `0` (= unlimited)
                # despite the init callback running. Making it a startup
                # parameter fixes this without disabling pgbouncer pooling.
                'statement_timeout': str(settings.statement_timeout_ms),
            },
            init=_init_conn,
        )

    pool = await _create_pool(
        settings.database_url,
        application_name='ed_finder_api',
        min_size=5,
        max_size=20,
    )
    set_pool(pool)

    readonly_pool = pool
    readonly_dsn = settings.database_readonly_url
    if readonly_dsn and readonly_dsn != settings.database_url:
        readonly_pool = await _create_pool(
            readonly_dsn,
            application_name='ed_finder_api_readonly',
            min_size=1,
            max_size=5,
        )
        log.info('PostgreSQL read-only pool ready ✓')
    else:
        log.info('PostgreSQL read-only pool not configured; reusing primary pool')
    set_readonly_pool(readonly_pool)

    await _reap_stale_admin_runs_on_startup(pool)

    redis = None
    try:
        redis = await connect_redis()
        set_redis(redis)
        log.info("Redis connected ✓ (pool max=%d)", settings.redis_max_connections)
    except Exception as e:
        log.warning(f"Redis unavailable ({e}) — running without cache")
        set_redis(None)
        redis = None

    # The bridge always starts. When Redis was down at boot it keeps
    # retrying the connection in the background with capped exponential
    # backoff and installs the client (set_redis) once it succeeds, so the
    # live feed — and the cache — recover without an API restart.
    _sse_pubsub_task = asyncio.create_task(eddn_pubsub_bridge())
    log.info(
        "EDDN→SSE pub/sub bridge started ✓%s",
        '' if redis is not None else ' (Redis unavailable at boot; bridge will retry in the background)',
    )

    if settings.eddn_simulation_ingest_enabled:
        _eddn_simulation_ingest_task = asyncio.create_task(run_eddn_simulation_ingest(pool))
        log.info("EDDN simulation ingest started ✓")

    # Load facility catalogue into memory (simulation engine domain layer)
    try:
        async with pool.acquire() as _conn:
            _rows = await _conn.fetch('SELECT * FROM facility_templates ORDER BY tier, id')
            from edfinder_api.domain.facilities import load_bundled_catalogue, load_catalogue_from_rows
            if _rows:
                load_catalogue_from_rows([dict(r) for r in _rows])
                log.info(f"Facility catalogue loaded ✓ ({len(_rows)} facilities)")
            else:
                _catalogue = load_bundled_catalogue()
                log.info(f"Bundled facility catalogue loaded ✓ ({len(_catalogue)} facilities)")
    except Exception as _e:
        try:
            from edfinder_api.domain.facilities import load_bundled_catalogue
            _catalogue = load_bundled_catalogue()
            log.warning(f"Facility catalogue DB load failed ({_e}) — using bundled catalogue ({len(_catalogue)} facilities)")
        except Exception as _fallback_e:
            log.warning(
                f"Facility catalogue load failed ({_e}); bundled fallback failed ({_fallback_e}) "
                "— simulation endpoints will use empty catalogue"
            )

    log.info("PostgreSQL pool ready ✓")
    app.state.pool  = pool
    app.state.readonly_pool = readonly_pool
    app.state.redis = redis
    yield

    if _sse_pubsub_task:
        _sse_pubsub_task.cancel()
        try:
            await _sse_pubsub_task
        except (asyncio.CancelledError, Exception):
            pass
    if _eddn_simulation_ingest_task:
        _eddn_simulation_ingest_task.cancel()
        try:
            await _eddn_simulation_ingest_task
        except (asyncio.CancelledError, Exception):
            pass
    if readonly_pool and readonly_pool is not pool:
        await readonly_pool.close()
    if pool:   await pool.close()
    # The bridge may have connected Redis after boot; close whatever client
    # is installed now, not only the one this function created.
    redis = get_redis_singleton() or redis
    if redis:  await redis.aclose()
    set_redis(None)
    log.info("Shutdown complete")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title='ED Finder API',
    version=settings.app_version,
    description='Search 186M Elite Dangerous star systems by economy, body types, and distance.',
    lifespan=lifespan,
    dependencies=[Depends(default_rate_limit)],
    docs_url='/docs',
    redoc_url='/redoc',
)

# Decorators use app state; the dependency enforces defaults after routing.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(',') if o.strip()],
    allow_methods=['GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS'],
    allow_headers=['Content-Type', 'X-Admin-Token', 'Authorization'],
    allow_credentials=True,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware('http')
async def metrics_middleware(request: Request, call_next: Any) -> Response:
    # A Prometheus scrape must not manufacture API traffic or latency.
    if request.url.path == '/api/metrics':
        return await call_next(request)

    start = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        # Record failed requests too; the exception handler owns the response,
        # while this middleware owns the complete request-duration contract.
        duration_seconds = time.perf_counter() - start
        _metrics['requests_total'] += 1
        observe_request_duration(duration_seconds)
        duration_ms = duration_seconds * 1000
        if duration_ms > 2000:
            log.warning('Slow request %s %s: %.0fms', request.method, request.url.path, duration_ms)
    return response


# ---------------------------------------------------------------------------
# Exception handlers (RFC 7807 Problem Details)
# ---------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def problem_details_handler(request: Request, exc: HTTPException):
    request_target = redact_frontier_oauth_target(str(request.url))
    return JSONResponse(
        status_code=exc.status_code,
        content={
            'type':     f'https://httpstatuses.com/{exc.status_code}',
            'title':    exc.detail if isinstance(exc.detail, str) else 'Error',
            'status':   exc.status_code,
            'detail':   exc.detail,
            'instance': request_target,
        },
        headers=getattr(exc, 'headers', None),
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    _metrics['errors_total'] += 1
    request_target = redact_frontier_oauth_target(str(request.url))
    log.exception('Unhandled error on %s %s', request.method, request_target)
    # This handler is what stops the exception from ever reaching an ASGI
    # middleware, so sentry_sdk's automatic capture never fires — report
    # explicitly instead. No-ops when SENTRY_DSN isn't set (config.py).
    sentry_sdk.capture_exception(exc)
    detail = str(exc) if settings.expose_error_detail else 'Internal server error'
    return JSONResponse(
        status_code=500,
        content={
            'type':   'https://httpstatuses.com/500',
            'title':  'Internal Server Error',
            'status': 500,
            'detail': detail,
        },
    )


# ---------------------------------------------------------------------------
# Router mounts
#
# Order matters only for the SPA fallback at the bottom: API routes must
# match before `/{full_path:path}` swallows them. The /s/<id64> share
# router is mounted first so its ~^/s/[0-9]+$ pattern beats the SPA.
# ---------------------------------------------------------------------------
app.include_router(share_router)
app.include_router(auth_router)
app.include_router(frontier_callback_compat_router)
app.include_router(meta_router)
app.include_router(news_router)
app.include_router(watchlist_router)
app.include_router(notes_router)
app.include_router(profile_router)
app.include_router(ratings_v4_router)
app.include_router(powerplay_router)
app.include_router(admin_router)
app.include_router(events_router)
app.include_router(evidence_router)
app.include_router(journal_import_router)
app.include_router(exploration_router)
app.include_router(routes_router)
app.include_router(search_router)
app.include_router(systems_router)
app.include_router(map_router)
app.include_router(archetypes_router)
app.include_router(simulate_router)
app.include_router(simulation_router)
app.include_router(optimiser_router)
app.include_router(observations_router)
app.include_router(colony_planner_router)
app.include_router(provenance_cockpit_router)
app.include_router(warehouse_planner_evidence_router)
app.include_router(operator_router)
app.include_router(v3_router)
app.include_router(plan_router)
app.include_router(v3_journal_router)
app.include_router(v3_journal_research_router)
app.include_router(v3_journal_contributions_router)


# ---------------------------------------------------------------------------
# SPA fallback + static files
#
# In Docker the production bundle is baked at /app/frontend/ (next to
# apps/api/src/), which is what `__file__.parent.parent / 'frontend'`
# resolves to. In a local dev clone the equivalent location is
# `frontend/dist/` (post-`yarn build`). Try both, in that order, so
# `python -m uvicorn main:app` works directly from the repo root without
# rebuilding the image.
# ---------------------------------------------------------------------------
def _resolve_frontend_dir() -> _pl.Path:
    here       = _pl.Path(__file__).resolve().parent
    candidates = [
        here.parent / 'frontend',                    # Docker baked layout
        here.parent.parent.parent / 'frontend' / 'dist',  # local dev
    ]
    for c in candidates:
        if c.is_dir():
            return c
    # No bundle on disk yet — fall back to the Docker location so the
    # 404 path still works; static-mount block below skips when missing.
    return candidates[0]


_FRONTEND_DIR = _resolve_frontend_dir()

# Mount static assets directory before registering the catch-all so that
# /static/<file> is served by StaticFiles (which includes its own safe path
# resolution).
if _FRONTEND_DIR.is_dir():
    app.mount('/static', StaticFiles(directory=str(_FRONTEND_DIR)), name='static')


_FRONTEND_REAL = _FRONTEND_DIR.resolve()


def _safe_frontend_path(raw: str) -> Optional[_pl.Path]:
    """Resolve a user-supplied path relative to the frontend dir, rejecting
    anything that escapes via '..' or symlinks."""
    if not raw:
        return None
    try:
        candidate = (_FRONTEND_DIR / raw).resolve()
    except (OSError, RuntimeError):
        return None
    # Must be inside the frontend directory (or equal to it).
    if candidate != _FRONTEND_REAL and _FRONTEND_REAL not in candidate.parents:
        return None
    return candidate


@app.get('/{full_path:path}', include_in_schema=False)
async def spa_fallback(full_path: str):
    # API paths should have been matched already; if someone requests
    # /api/<unknown> return 404 JSON instead of serving index.html.
    if full_path.startswith('api/'):
        raise HTTPException(404, f'API endpoint {full_path} not found')

    # No frontend bundle present (e.g. local dev before `yarn build`):
    # short-circuit with a 404 instead of FileResponse'ing a missing file.
    if not _FRONTEND_DIR.is_dir():
        raise HTTPException(
            404,
            'Frontend bundle not built. Run `cd frontend && yarn build`.',
        )

    candidate = _safe_frontend_path(full_path)
    if candidate and candidate.is_file():
        return _FileResponse(str(candidate))
    # Fall back to index.html for SPA routing
    return _FileResponse(str(_FRONTEND_DIR / 'index.html'))


if __name__ == '__main__':
    import uvicorn
    port = int(os.environ.get('PORT', 5000))
    uvicorn.run(app, host='0.0.0.0', port=port, log_level=settings.log_level.lower())
