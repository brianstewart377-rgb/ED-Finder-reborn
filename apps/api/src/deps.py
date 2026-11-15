"""FastAPI dependencies + shared cache helpers.

Routers do:

    from edfinder_api.deps import get_pool, get_redis
    @router.get(...)
    async def endpoint(pool = Depends(get_pool), redis = Depends(get_redis)):
        ...

`require_admin` accepts either the legacy server-side ADMIN_TOKEN (for CLI and
automation) or an owner Frontier web session (for the browser dashboards).
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from typing import Any, Optional

import asyncpg
import redis.asyncio as aioredis
from fastapi import HTTPException, Request

from edfinder_api.auth import get_request_user, require_same_origin
from edfinder_api.config import settings
from edfinder_api.state import (
    get_pool_singleton, get_readonly_pool_singleton, get_redis_singleton, metrics, set_redis,
)

log = logging.getLogger('ed_finder')


# Cache is a latency optimization, not a correctness dependency. Keep Redis I/O
# on a short leash so a slow cache does not dominate request time.
_CACHE_IO_TIMEOUT_SECONDS = 0.1

# A cache that cannot be reached must not go unnoticed: every answer is then
# worked out afresh, and a Find count takes seconds instead of milliseconds.
# Each failure is counted (/api/metrics) and said in the log, but at most once
# a minute, so a cache that is away does not fill the log.
_CACHE_WARNING_EVERY_SECONDS = 60.0
_last_cache_warning: Optional[float] = None


def _cache_failed(doing: str, error: BaseException) -> None:
    """Count a cache failure and, at most once a minute, say so in the log.

    The key is left out on purpose: some keys are made from what a person
    searched for.
    """
    global _last_cache_warning
    metrics['cache_errors'] = metrics.get('cache_errors', 0) + 1
    now = time.monotonic()
    if _last_cache_warning is not None and now - _last_cache_warning < _CACHE_WARNING_EVERY_SECONDS:
        return
    _last_cache_warning = now
    log.warning(
        'the cache could not be %s (%s); answers are worked out afresh until it can. '
        '%d cache failures since the API started.',
        doing, type(error).__name__, metrics['cache_errors'],
    )


# ── DB / cache providers for Depends() ──────────────────────────────────
async def get_pool() -> asyncpg.Pool:
    pool = get_pool_singleton()
    if pool is None:
        raise HTTPException(503, 'Database pool not initialised')
    return pool


async def get_readonly_pool() -> asyncpg.Pool:
    pool = get_readonly_pool_singleton() or get_pool_singleton()
    if pool is None:
        raise HTTPException(503, 'Database pool not initialised')
    return pool


async def get_redis() -> Optional[aioredis.Redis]:
    return get_redis_singleton()


async def connect_redis() -> aioredis.Redis:
    """Build the process Redis client from settings and verify it with PING.

    Shared by the lifespan (boot-time connect) and the EDDN→SSE bridge
    (background reconnect when Redis was down at boot) so both use the
    same bounded socket timeouts. Raises on failure; the caller decides
    whether that is fatal (it never is — Redis is a cache, not truth).
    The connection URL is never logged here.
    """
    client = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=settings.redis_max_connections,
        socket_connect_timeout=3,
        socket_timeout=3,
        retry_on_timeout=True,
    )
    try:
        await asyncio.wait_for(client.ping(), timeout=3.0)
    except BaseException:
        try:
            await client.aclose()
        except Exception:  # noqa: S110 - tidying up; the connect error below is the one that matters
            pass
        raise
    return client


# How long to wait before trying the cache again, doubling each time up to
# the longest wait.
_CACHE_RETRY_FIRST_SECONDS = 1.0
_CACHE_RETRY_LONGEST_SECONDS = 60.0


async def connect_cache_when_it_is_back() -> None:
    """Keep trying to reach the cache after the API has started without it.

    The start-up connects once and carries on without a cache if that fails.
    Without this, the API would then run uncached until it was next
    restarted: right answers, but every Find count worked out afresh. This
    tries again, waiting a little longer each time, and installs the client
    once the cache answers. It ends as soon as a client is installed.

    Only for a cache that was away at the start. One that goes away later
    needs nothing: its client reconnects by itself, and the failures in
    between are counted and logged by cache_get and cache_set.
    The connection's address is never logged.
    """
    wait = _CACHE_RETRY_FIRST_SECONDS
    while get_redis_singleton() is None:
        await asyncio.sleep(wait)
        try:
            client = await connect_redis()
        except Exception as error:
            wait = min(wait * 2.0, _CACHE_RETRY_LONGEST_SECONDS)
            log.warning('the cache is still away (%s); trying again in %.0f s',
                        type(error).__name__, wait)
            continue
        set_redis(client)
        log.info('the cache was reached after the API had started without it; answers are kept again')


def secrets_equal(supplied: str, expected: str) -> bool:
    """Constant-time comparison that never raises on client text.

    ``hmac.compare_digest`` raises ``TypeError`` for a ``str`` containing
    non-ASCII characters, which turned a malformed token or OAuth state into a
    logged 500. Comparing the UTF-8 bytes keeps the comparison constant-time
    and makes any non-matching input an ordinary mismatch.
    """
    return hmac.compare_digest(supplied.encode('utf-8'), expected.encode('utf-8'))


# ── Admin bearer-token auth. ────────────────────────────────────────────
async def require_admin(request: Request) -> None:
    token = settings.admin_token
    supplied = request.headers.get('X-Admin-Token') or ''
    auth = request.headers.get('Authorization', '')
    if auth.lower().startswith('bearer '):
        supplied = supplied or auth[7:].strip()

    # Preserve non-browser operator automation during the OAuth transition.
    if token and supplied and secrets_equal(supplied, token):
        return

    user = await get_request_user(request)
    if user is not None and user.is_owner:
        require_same_origin(request)
        return
    if user is not None:
        raise HTTPException(403, 'Owner access required')
    if supplied:
        raise HTTPException(401, 'Invalid admin token')
    raise HTTPException(401, 'Owner sign-in required')


# ── Redis cache helpers. Tolerant of an absent Redis (returns None). ────
async def cache_get(
    key: str,
    redis: Optional[aioredis.Redis] = None,
) -> Optional[Any]:
    r = redis or get_redis_singleton()
    if not r:
        return None
    try:
        v = await asyncio.wait_for(r.get(key), timeout=_CACHE_IO_TIMEOUT_SECONDS)
        if v:
            value = json.loads(v)
            metrics['cache_hits'] += 1
            return value
    except Exception as error:
        # A miss as far as the caller is concerned, but not a silent one.
        _cache_failed('read', error)
    metrics['cache_misses'] += 1
    return None


async def cache_set(
    key: str,
    value: Any,
    ttl: int,
    redis: Optional[aioredis.Redis] = None,
) -> None:
    r = redis or get_redis_singleton()
    if not r:
        return
    try:
        await asyncio.wait_for(
            r.setex(key, ttl, json.dumps(value, default=str)),
            timeout=_CACHE_IO_TIMEOUT_SECONDS,
        )
    except Exception as error:
        _cache_failed('written', error)


# ── Small metric / slow-query helpers. ──────────────────────────────────
def inc_metric(key: str) -> None:
    metrics[key] = metrics.get(key, 0) + 1


def log_slow(endpoint: str, duration_ms: float) -> None:
    if duration_ms > 2000:
        log.warning('Slow query on %s: %.0fms', endpoint, duration_ms)
