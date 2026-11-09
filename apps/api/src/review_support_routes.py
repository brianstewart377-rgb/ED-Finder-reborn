from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from edfinder_api.models import CacheStatsResponse
from edfinder_api.deps import get_pool
from edfinder_api.routers.archetypes import get_system_archetypes
from edfinder_api.routers.auth import AuthSessionResponse
from edfinder_api.routers.evidence import evidence_system_summary


router = APIRouter(tags=['review-support'])
REVIEW_SCENARIO_MODES = frozenset({
    'normal', 'api_failure', 'empty_results',
    'rankings_api_failure', 'rankings_empty_results',
})


@router.post('/api/review/scenario/{mode}', include_in_schema=False)
async def set_review_scenario(request: Request, mode: str) -> dict[str, str]:
    """Select a bounded failure/edge mode inside the isolated Review Lab only."""
    if mode not in REVIEW_SCENARIO_MODES:
        raise HTTPException(status_code=400, detail='Unsupported Review Lab scenario mode')
    request.app.state.review_scenario = mode
    return {'scenario': mode}


async def _review_event_stream():
    # Keep the review-only SSE endpoint alive so the frontend can degrade
    # cleanly to recent-events polling without a 404 transport error.
    while True:
        yield b': review-only keepalive\n\n'
        await asyncio.sleep(15)


@router.get('/api/events/live', include_in_schema=False)
async def review_live_events() -> StreamingResponse:
    return StreamingResponse(_review_event_stream(), media_type='text/event-stream')


@router.get('/api/events/recent', include_in_schema=False)
async def review_recent_events() -> dict[str, object]:
    return {
        'events': [],
        'jobs': {},
    }


@router.get('/api/news/latest', include_in_schema=False)
async def review_latest_news(limit: int = 8) -> dict[str, object]:
    return {
        'items': [],
        'source_url': 'review-only://synthetic-empty-news',
        'fetched_at': '1970-01-01T00:00:00Z',
        'stale': False,
    }


@router.get(
    '/api/auth/session',
    response_model=AuthSessionResponse,
    include_in_schema=False,
)
@router.get(
    '/api/v1/auth/session',
    response_model=AuthSessionResponse,
    include_in_schema=False,
)
async def review_auth_session() -> AuthSessionResponse:
    """Expose the real signed-out session envelope without Frontier access.

    Review Lab never receives OAuth credentials or production cookies. The SPA
    still performs its normal account bootstrap, so return the same canonical
    unauthenticated contract as production instead of allowing a transport 404
    to obscure unrelated browser verification.

    Both the legacy and the canonical V3 auth paths are served here while the
    application lane moves to `/api/v1/auth`, mirroring the transitional alias
    the edge keeps for the registered Frontier callback.
    """
    return AuthSessionResponse(
        authenticated=False,
        user=None,
        owner_claim_available=False,
    )


@router.get('/api/archetypes/system/{id64}', include_in_schema=False)
async def review_system_archetypes(request: Request, id64: int):
    return await get_system_archetypes(request, id64)


@router.get('/api/evidence/systems/{system_id64}/summary', include_in_schema=False)
async def review_evidence_system_summary(system_id64: int):
    return await evidence_system_summary(system_id64, await get_pool())


@router.get(
    '/api/cache/stats',
    response_model=CacheStatsResponse,
    include_in_schema=False,
)
async def review_cache_stats() -> CacheStatsResponse:
    return CacheStatsResponse(
        cache_hits=0,
        cache_misses=0,
        redis_hits=0,
        redis_misses=0,
        redis_memory_mb=0.0,
        db_cache_rows=0,
    )
