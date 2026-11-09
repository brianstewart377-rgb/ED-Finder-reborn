"""Meta endpoints — health, status, metrics.

Anything diagnostic / non-business-logic lives here. The minimal deployment
health probe stays public; detailed status and metrics remain operator-only at
the edge or through owner authentication.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Optional

import asyncpg
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from edfinder_api.config import settings
from edfinder_api.deps import get_pool, get_redis, cache_get, cache_set, require_admin
from edfinder_api.helpers import (
    STATUS_REASON_CODES,
    STATUS_REASON_CONNECTION,
    STATUS_REASON_SCHEMA,
    STATUS_REASON_TIMEOUT,
    STATUS_REASON_UNKNOWN,
)
from edfinder_api.ingest.eddn_client import (
    consecutive_flush_failures as eddn_simulation_ingest_consecutive_flush_failures,
    flush_failures_total as eddn_simulation_ingest_flush_failures_total,
    last_flush_attempt_timestamp as eddn_simulation_ingest_last_flush_attempt_timestamp,
    last_successful_flush_timestamp as eddn_simulation_ingest_last_successful_flush_timestamp,
    max_consecutive_flush_failures as eddn_simulation_ingest_max_consecutive_flush_failures,
    seconds_since_last_flush as eddn_simulation_ingest_seconds_since_flush,
)
from edfinder_api.models import HealthResponse, StatusResponse
from edfinder_api.monitoring import data_invariants_receipt_metrics
from edfinder_api.routers.events import (
    bridge_reconnects_total as sse_bridge_reconnects_total,
    bridge_state as sse_bridge_state,
)
from edfinder_api.state import metrics

log = logging.getLogger('ed_finder')

router = APIRouter(tags=['meta'])

# Local_search may be unavailable in minimal deployments (CI, local dev).
try:
    import edfinder_api.local_search as _ls
    _LS_AVAILABLE = True
except ImportError:
    _ls = None  # type: ignore
    _LS_AVAILABLE = False


@router.get('/api/health', response_model=HealthResponse)
async def health(pool: asyncpg.Pool = Depends(get_pool)):
    """Cheap, bounded liveness probe.

    Bounded at 2 s with `asyncio.wait_for` so a wedged pool (every
    connection held by a slow query) cannot hang the healthcheck for
    the asyncpg `command_timeout` of 5 minutes. nginx's
    `proxy_read_timeout` on /api/health is 300s and Cloudflare's edge
    timeout is ~100s, so an unbounded probe means CF returns its own
    524 to the user before nginx ever gives up — and the api container
    keeps showing (healthy) to Docker's own healthcheck because that
    runs from inside the container against localhost without going
    through any of those timeouts.

    Capping the probe at 2 s makes /api/health honest about pool
    exhaustion: if no connection is acquirable inside that window, we
    return 503 with a clear message and Docker can mark the container
    unhealthy and restart it, breaking the wedge.
    """
    async def _probe() -> None:
        async with pool.acquire() as conn:
            await conn.fetchval('SELECT 1')

    try:
        await asyncio.wait_for(_probe(), timeout=2.0)
        return HealthResponse(
            status='ok',
            database='connected',
            version=settings.app_version,
            build_sha=settings.build_sha,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            503,
            detail='database probe timed out after 2s — pool likely exhausted',
        )
    except Exception as e:
        raise HTTPException(503, detail=str(e))


@router.get(
    '/api/status',
    response_model=StatusResponse,
    dependencies=[Depends(require_admin)],
)
async def status(
    pool:  asyncpg.Pool              = Depends(get_pool),
    redis: Optional[aioredis.Redis]  = Depends(get_redis),
):
    cache_key = 'status:main'
    cached = await cache_get(cache_key, redis)
    if cached:
        return cached

    async with pool.acquire() as conn:
        # pg_class.reltuples is PG's ANALYZE-derived row estimate (~1ms)
        # vs 30+ seconds for COUNT(*) FROM bodies on a 1B+ row table.
        # Estimates auto-refresh on every autovac/analyze cycle - plenty
        # accurate for a status display. cluster_summary stays as COUNT(*)
        # because the table is small and has a WHERE filter.
        counts = await conn.fetchrow("""
            SELECT
              COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='systems'), 0) AS sys_count,
              COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='bodies'),  0) AS body_count,
              COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='ratings'), 0) AS rated_count
        """)
        sys_count  = int(counts['sys_count'])
        body_count = int(counts['body_count'])
        rated      = int(counts['rated_count'])
        clustered  = await conn.fetchval(
            'SELECT COUNT(*) FROM cluster_summary WHERE coverage_score IS NOT NULL'
        )
        meta_rows  = await conn.fetch('SELECT key, value FROM app_meta')
        meta = {r['key']: r['value'] for r in meta_rows}

    result = {
        'available':            True,
        'systems_count':        sys_count,
        'body_count':           body_count,
        'rated_count':          rated,
        'clustered_count':      clustered,
        'import_complete':      meta.get('import_complete') == 'true',
        'ratings_built':        meta.get('ratings_built')   == 'true',
        'grid_built':           meta.get('grid_built')      == 'true',
        'clusters_built':       meta.get('clusters_built')  == 'true',
        'eddn_enabled':         meta.get('eddn_enabled')    == 'true',
        'last_nightly_update':  meta.get('last_nightly_update', 'never'),
        'schema_version':       meta.get('schema_version', '1.0'),
        'max_search_radius_ly': 500,
        'has_body_data':        body_count > 0,
        'version':              settings.app_version,
    }
    await cache_set(cache_key, result, settings.ttl_status, redis)
    return result


@router.get(
    '/api/local/status',
    response_model=StatusResponse,
    dependencies=[Depends(require_admin)],
)
async def local_status(
    pool:  asyncpg.Pool              = Depends(get_pool),
    redis: Optional[aioredis.Redis]  = Depends(get_redis),
):
    # Cache mirrors /api/status — without it, V2 Admin polls this every 30s
    # and triggered 8 sequential COUNT(*) queries (including COUNT(*) FROM
    # bodies on a ~1B row table) on every poll, starving build_grid.py of
    # disk I/O during the grid backfill.
    cache_key = 'status:local'
    cached = await cache_get(cache_key, redis)
    if cached:
        return cached
    if _LS_AVAILABLE:
        try:
            result = await _ls.local_db_status(pool)
        except Exception as exc:
            log.warning('local_db_status error: %s', exc)
        else:
            if result.get('available') is True:
                await cache_set(cache_key, result, settings.ttl_status, redis)
                return result
            # Failure branch: local_db_status swallowed a DB error and
            # returned {available: False, reason: <STATUS_REASON_* code>}
            # (it logged the raw exception itself). Never cache that (a 60 s
            # cache turns a one-off blip into a minute of "unavailable" and
            # hides recovery) and re-map the reason so that even a producer
            # that regressed to raw text cannot leak DSN fragments, host
            # names or SQL to the caller.
            log.warning('local_db_status unavailable: %s', result.get('reason'))
            return {
                **{k: v for k, v in result.items() if k != 'reason'},
                'available': False,
                'reason': status_failure_reason_code(result.get('reason')),
            }
    return await status(pool, redis)


def status_failure_reason_code(raw_reason: object) -> str:
    """Map a local_db_status failure reason to a fixed reason code.

    ``local_db_status`` already classifies the exception by type and hands us
    one of STATUS_REASON_CODES (defined in ``edfinder_api.helpers``); that is
    passed through unchanged. Anything else is treated as raw text and
    classified best-effort as defence in depth, so a producer that regresses
    to ``str(exc)`` still cannot leak it: the output is always one of
    STATUS_REASON_CODES, never the input.
    """
    if isinstance(raw_reason, str) and raw_reason in STATUS_REASON_CODES:
        return raw_reason
    text = str(raw_reason or '').lower()
    if 'timeout' in text or 'canceling statement' in text or 'cancelled' in text or 'canceled' in text:
        return STATUS_REASON_TIMEOUT
    if 'does not exist' in text or 'undefined' in text:
        return STATUS_REASON_SCHEMA
    if ('connect' in text or 'pool' in text or 'too many' in text
            or 'closed' in text or 'refused' in text):
        return STATUS_REASON_CONNECTION
    return STATUS_REASON_UNKNOWN


@router.get('/api/metrics', response_class=PlainTextResponse, include_in_schema=False)
async def metrics_endpoint():
    uptime = time.time() - metrics['startup_time']
    invariant_paths = [
        path.strip()
        for path in settings.data_invariants_receipt_paths.split(',')
        if path.strip()
    ]
    invariant_status, invariant_age = data_invariants_receipt_metrics(invariant_paths)
    invariant_age_text = 'NaN' if math.isnan(invariant_age) else f'{invariant_age:g}'
    lines = [
        '# HELP ed_finder_requests_total Total HTTP requests excluding metrics scrapes',
        '# TYPE ed_finder_requests_total counter',
        f'ed_finder_requests_total {metrics["requests_total"]}',
        '# HELP ed_finder_cache_hits_total Redis cache hits',
        '# TYPE ed_finder_cache_hits_total counter',
        f'ed_finder_cache_hits_total {metrics["cache_hits"]}',
        '# HELP ed_finder_cache_misses_total Redis cache misses',
        '# TYPE ed_finder_cache_misses_total counter',
        f'ed_finder_cache_misses_total {metrics["cache_misses"]}',
        '# HELP ed_finder_errors_total Total unhandled errors',
        '# TYPE ed_finder_errors_total counter',
        f'ed_finder_errors_total {metrics["errors_total"]}',
        '# HELP ed_finder_db_queries_total Total DB queries dispatched',
        '# TYPE ed_finder_db_queries_total counter',
        f'ed_finder_db_queries_total {metrics["db_queries"]}',
        '# HELP ed_finder_http_request_duration_seconds HTTP request duration excluding metrics scrapes',
        '# TYPE ed_finder_http_request_duration_seconds histogram',
    ]
    duration_count = metrics['request_duration_seconds_count']
    for upper_bound, count in metrics['request_duration_seconds_buckets'].items():
        lines.append(
            'ed_finder_http_request_duration_seconds_bucket'
            f'{{le="{upper_bound:g}"}} {count}'
        )
    lines.extend([
        'ed_finder_http_request_duration_seconds_bucket'
        f'{{le="+Inf"}} {duration_count}',
        'ed_finder_http_request_duration_seconds_sum '
        f'{metrics["request_duration_seconds_sum"]:.9f}',
        f'ed_finder_http_request_duration_seconds_count {duration_count}',
        '# HELP ed_finder_data_invariants_success Latest data-invariants receipt status (-1 unknown, 0 failed, 1 passed)',
        '# TYPE ed_finder_data_invariants_success gauge',
        f'ed_finder_data_invariants_success {invariant_status}',
        '# HELP ed_finder_data_invariants_age_seconds Age of the latest valid data-invariants receipt',
        '# TYPE ed_finder_data_invariants_age_seconds gauge',
        f'ed_finder_data_invariants_age_seconds {invariant_age_text}',
        '# HELP ed_finder_uptime_seconds Seconds since startup',
        '# TYPE ed_finder_uptime_seconds gauge',
        f'ed_finder_uptime_seconds {uptime:.0f}',
        '# HELP ed_finder_eddn_simulation_ingest_seconds_since_flush Seconds since the EDDN simulation ingest task last completed a flush cycle',
        '# TYPE ed_finder_eddn_simulation_ingest_seconds_since_flush gauge',
        f'ed_finder_eddn_simulation_ingest_seconds_since_flush {eddn_simulation_ingest_seconds_since_flush():.1f}',
        '# HELP ed_finder_eddn_simulation_ingest_flush_failures_total Failed EDDN simulation ingest DB flushes since process start',
        '# TYPE ed_finder_eddn_simulation_ingest_flush_failures_total counter',
        f'ed_finder_eddn_simulation_ingest_flush_failures_total {eddn_simulation_ingest_flush_failures_total()}',
        '# HELP ed_finder_eddn_simulation_ingest_consecutive_flush_failures Consecutive failed EDDN simulation ingest flushes since the last successful one',
        '# TYPE ed_finder_eddn_simulation_ingest_consecutive_flush_failures gauge',
        f'ed_finder_eddn_simulation_ingest_consecutive_flush_failures {eddn_simulation_ingest_consecutive_flush_failures()}',
        '# HELP ed_finder_eddn_simulation_ingest_last_successful_flush_timestamp_seconds Unix time of the last successful EDDN simulation ingest DB flush (0 = none since process start)',
        '# TYPE ed_finder_eddn_simulation_ingest_last_successful_flush_timestamp_seconds gauge',
        f'ed_finder_eddn_simulation_ingest_last_successful_flush_timestamp_seconds {eddn_simulation_ingest_last_successful_flush_timestamp():.0f}',
        '# HELP ed_finder_eddn_simulation_ingest_last_flush_attempt_timestamp_seconds Unix time at which the last non-empty EDDN simulation ingest DB flush started, success or failure (0 = none since process start)',
        '# TYPE ed_finder_eddn_simulation_ingest_last_flush_attempt_timestamp_seconds gauge',
        f'ed_finder_eddn_simulation_ingest_last_flush_attempt_timestamp_seconds {eddn_simulation_ingest_last_flush_attempt_timestamp():.0f}',
        '# HELP ed_finder_eddn_simulation_ingest_enabled Whether the EDDN simulation ingest task is enabled in this process (EDDN_SIMULATION_INGEST_ENABLED)',
        '# TYPE ed_finder_eddn_simulation_ingest_enabled gauge',
        f'ed_finder_eddn_simulation_ingest_enabled {1 if settings.eddn_simulation_ingest_enabled else 0}',
        '# HELP ed_finder_eddn_simulation_ingest_max_consecutive_flush_failures Consecutive flush failures at which the ingest loop drops its batch and reconnects (EDDN_SIMULATION_INGEST_MAX_CONSECUTIVE_FLUSH_FAILURES, clamped)',
        '# TYPE ed_finder_eddn_simulation_ingest_max_consecutive_flush_failures gauge',
        f'ed_finder_eddn_simulation_ingest_max_consecutive_flush_failures {eddn_simulation_ingest_max_consecutive_flush_failures()}',
        '# HELP ed_finder_sse_bridge_state EDDN to SSE Redis pub/sub bridge state (0 not started, 1 connecting, 2 subscribed, 3 retrying, 4 stopped)',
        '# TYPE ed_finder_sse_bridge_state gauge',
        f'ed_finder_sse_bridge_state {sse_bridge_state()}',
        '# HELP ed_finder_sse_bridge_reconnects_total EDDN to SSE bridge connect or subscribe attempts that followed a failure',
        '# TYPE ed_finder_sse_bridge_reconnects_total counter',
        f'ed_finder_sse_bridge_reconnects_total {sse_bridge_reconnects_total()}',
    ])
    return '\n'.join(lines) + '\n'
