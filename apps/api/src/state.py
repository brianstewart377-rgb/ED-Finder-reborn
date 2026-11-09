"""Mutable application state.

FastAPI's dependency-injection is stateless; this module is the place we
hang the long-lived singletons that the lifespan initialises (DB pool,
Redis, SSE queues, counters, active jobs).

Routers should NOT import these symbols directly for DB/Redis access —
use `deps.get_pool` / `deps.get_redis` so FastAPI's Depends() wiring
keeps working. Direct import is fine for the lifespan (which owns these)
and for diagnostic handlers like /api/metrics and /api/events.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import asyncpg
import redis.asyncio as aioredis


# ── Connection singletons, set by the lifespan at startup. ──────────────
_pool:  Optional[asyncpg.Pool]      = None
_readonly_pool: Optional[asyncpg.Pool] = None
_redis: Optional[aioredis.Redis]    = None


def set_pool(pool: Optional[asyncpg.Pool]) -> None:
    global _pool
    _pool = pool


def set_readonly_pool(pool: Optional[asyncpg.Pool]) -> None:
    global _readonly_pool
    _readonly_pool = pool


def set_redis(redis: Optional[aioredis.Redis]) -> None:
    global _redis
    _redis = redis


def get_pool_singleton() -> Optional[asyncpg.Pool]:
    return _pool


def get_readonly_pool_singleton() -> Optional[asyncpg.Pool]:
    return _readonly_pool


def get_redis_singleton() -> Optional[aioredis.Redis]:
    return _redis


# ── Process-wide metrics. Read by /api/metrics, mutated everywhere. ─────
metrics: dict[str, Any] = {
    'requests_total':  0,
    'cache_hits':      0,
    'cache_misses':    0,
    'db_queries':      0,
    'errors_total':    0,
    'startup_time':    time.time(),
    'request_duration_seconds_sum': 0.0,
    'request_duration_seconds_count': 0,
    'request_duration_seconds_buckets': {
        0.05: 0,
        0.1: 0,
        0.25: 0,
        0.5: 0,
        1.0: 0,
        2.0: 0,
        5.0: 0,
        10.0: 0,
    },
}


def observe_request_duration(duration_seconds: float) -> None:
    """Record one request in a bounded, label-free Prometheus histogram."""
    metrics['request_duration_seconds_sum'] += duration_seconds
    metrics['request_duration_seconds_count'] += 1
    for upper_bound in metrics['request_duration_seconds_buckets']:
        if duration_seconds <= upper_bound:
            metrics['request_duration_seconds_buckets'][upper_bound] += 1


# ── Background job tracking (e.g. cluster rebuild). ─────────────────────
active_jobs:       dict[str, Any] = {}
active_jobs_lock:  asyncio.Lock   = asyncio.Lock()


# ── Live-feed SSE clients, one queue per connection. ────────────────────
sse_clients: list[asyncio.Queue] = []
