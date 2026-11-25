"""Find by economy: ``POST /api/find/economy`` (``find_economy.py``).

Ratings rebuild, build step 5: the new search, beside ``/api/local/search``,
which it does not change. No page calls it yet.

One search implementation, as for the other search routes: a request that
cannot be answered is refused (422) before the database is touched, facts
that cannot be read yet answer 503 with the reason, and any other failure
answers 503 as RFC 7807 problem details rather than a made-up page.

``scope`` says where to look: near a point (the default), or across the whole
band, in the ranked lists the nightly scoring writes.
"""
import json
import time
from typing import Optional

import asyncpg
import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from edfinder_api.config import limiter, log, settings
from edfinder_api.deps import cache_get, cache_set, get_pool, get_redis, inc_metric, log_slow
import edfinder_api.find_economy as _find
from edfinder_api.models_find_economy import EconomySearchRequest, EconomySearchResponse
from edfinder_api.routers.search import _count_budget_ms, _search_unavailable

router = APIRouter(tags=['search'])

# Bump when what a kept answer holds changes.
CACHE_VERSION = 'v2'


def _cache_key(body: dict) -> str:
    """An answer is kept for an hour under everything that shapes it. The
    facts change each night; the fill an answer was read from is in the
    answer itself (``facts``)."""
    return (f"find-economy:{CACHE_VERSION}:{_find.VERSION}:"
            f"{json.dumps(body, sort_keys=True, default=str)}")


@router.post('/api/find/economy', response_model=EconomySearchResponse)
@limiter.limit(settings.rate_limit_search)
async def find_by_economy(
    request: Request,
    req: EconomySearchRequest,
    background_tasks: BackgroundTasks,
    pool: asyncpg.Pool = Depends(get_pool),
    redis: Optional[aioredis.Redis] = Depends(get_redis),
):
    background_tasks.add_task(inc_metric, 'db_queries')
    started = time.time()
    body = {
        'scope': req.scope,
        'economies': list(req.economies),
        'order': req.order,
        'reference_coords': req.reference_coords.model_dump() if req.reference_coords else None,
        # Near a point, 100 ly where it is left out; across the band there is none.
        'radius_ly': (_find.RADIUS_DEFAULT_LY if req.radius_ly is None and req.scope == _find.SCOPE_NEAR
                      else req.radius_ly),
        'reach_ly': req.reach_ly,
        'body_filters': req.body_filters.model_dump(exclude_none=True) if req.body_filters else {},
        'size': req.size,
        'from': req.from_,
    }
    # 422 before any database access.
    _find.prepare(body)
    cache_key = _cache_key(body)
    cached = await cache_get(cache_key, redis)
    if cached:
        return cached
    try:
        result = await _find.answer(
            body, pool, count_budget_ms=_count_budget_ms(req.wait_for_count))
    except HTTPException:
        raise
    except Exception as exc:
        log.error('find_economy.answer failed: type=%s repr=%r sqlstate=%s',
                  type(exc).__name__, exc, getattr(exc, 'sqlstate', None), exc_info=True)
        return _search_unavailable(
            f'find by economy: {type(exc).__name__}: {exc}',
            hint='Search a smaller distance; if it keeps happening, retry shortly.')
    # An answer whose matches were not counted is not kept: the next asking
    # (the page's own follow-up for the count) must reach the database.
    if result['total_is_counted']:
        await cache_set(cache_key, result, settings.ttl_search, redis)
    background_tasks.add_task(log_slow, 'find_economy', (time.time() - started) * 1000)
    return result
