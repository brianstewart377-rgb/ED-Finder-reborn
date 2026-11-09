"""Search endpoints — autocomplete + local/galaxy/cluster searches.

Audit fix (2026-05-08, AUDIT_REPORT.md §C5 / Phase 2):
the previous file carried two parallel implementations of every search:
the canonical `local_search.local_db_*` builder, and a 200-line inline
fallback per endpoint that was triggered on any exception. The fallback
had drifted (e.g. it always sorted by `r.score` while the primary path
sorted by the economy-specific `display_score_col`), causing identical
requests to return different orderings depending on which path ran.

This file now has ONE search implementation per route. If the primary
path raises, we surface RFC 7807 problem-details with HTTP 503 instead of
silently degrading.

V3 (F3): `/api/local/search` (slice 1), `/api/search/galaxy` and
`/api/search/cluster` (slice 2b) read only the published V3 projections
through `local_search.local_db_search_v3` / `local_db_galaxy_search_v3` /
`local_db_cluster_search_v3`, each behind the migration-015 Finder product
gate (404 without a PUBLISHED generation, 503 until both Finder products
are READY and published) with request validation before any database
access. The legacy `cluster_summary` / `systems` / `ratings` readers are
gone from this module; a static test walks every string literal here to
keep it that way.
"""
import hashlib
import json
import time
from typing import Any, Optional

import asyncpg
import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from edfinder_api.config import settings, limiter, log
from edfinder_api.deps import get_pool, get_redis, cache_get, cache_set, inc_metric, log_slow
from edfinder_api.models import (
    SearchResponse, SearchFilters, LocalSearchRequest,
    GalaxySearchRequest, ClusterSearchRequest, ClusterSearchResponse, AutocompleteResponse,
)
from edfinder_api.ranking.profile import RANKING_VERSION, ranking_sha256

# Single search implementation. If this import fails the app cannot
# serve search at all — fail loud at startup, not at request time.
import edfinder_api.local_search as _ls

router = APIRouter(tags=['search'])

# The V3 projection replaces both canonical and legacy autocomplete readers.
# Include generation identity and the entire literal prefix in every key.
AUTOCOMPLETE_CACHE_VERSION = 'v4'
# Bumped v4 -> v5 at the F3 V3 cutover: pre-cutover `search:v4:*` entries were
# written by the legacy code path (different semantics, no generation/ranking
# identity) and must never be served after the repoint.
SEARCH_CACHE_VERSION = 'v5'
# Bumped v4 -> v5 at the F3 slice-2b V3 cutover of `/api/search/galaxy`:
# pre-cutover `galaxy:v4:*` entries were written by the legacy economy-ranked
# path and must never be served after the repoint. The cluster route carries
# no cache any more (see `cluster_search`).
GALAXY_CACHE_VERSION = 'v5'


def _search_cache_key(body_dict: dict, generation_row) -> str:
    """Build the `/api/local/search` cache key.

    Scoped to (a) the cutover namespace version, (b) the ranking identity
    (`RANKING_VERSION` + `ranking_sha256()`), (c) the resolved PUBLISHED
    generation (id + sequence), and (d) the full request body. So a warm
    entry can never be served across a cutover, a ranking-formula change, or
    a governed publish — each shifts the namespace and misses the old key.
    """
    return (
        f"search:{SEARCH_CACHE_VERSION}:{RANKING_VERSION}:{ranking_sha256()}:"
        f"g{generation_row['derived_generation_id']}:"
        f"s{generation_row['publication_sequence']}:"
        f"{json.dumps(body_dict, sort_keys=True, default=str)}"
    )


def _complete_coords(coords) -> dict | None:
    if not coords:
        return None
    dumped = coords.model_dump()
    if all(dumped.get(axis) is not None for axis in ('x', 'y', 'z')):
        return dumped
    return None


# ---------------------------------------------------------------------------
# Internal: RFC 7807 503 response
# ---------------------------------------------------------------------------
def _search_unavailable(detail_msg: str, *, hint: str = '') -> JSONResponse:
    """Surface a search backend failure as RFC 7807 problem-details.

    The audit's Phase 2 contract is "no silent fallback" — when the SQL
    builder raises, callers MUST be able to tell it failed (so they can
    retry, alert, or back off) instead of receiving subtly wrong data
    from a parallel implementation.
    """
    body = {
        'type':    'https://ed-finder.app/problem/search-unavailable',
        'title':   'Search backend temporarily unavailable',
        'status':  503,
        'detail':  detail_msg if settings.expose_error_detail else 'Search service is temporarily unavailable.',
        'hint':    hint or 'Retry in a few seconds; if the problem persists, check /api/health.',
    }
    return JSONResponse(status_code=503, content=body, media_type='application/problem+json')


# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------
@router.get('/api/local/autocomplete', response_model=AutocompleteResponse)
@limiter.limit('60/minute')
async def autocomplete(
    request: Request,
    q: str = '',
    limit: int = 10,
    pool: asyncpg.Pool = Depends(get_pool),
    redis: Optional[aioredis.Redis] = Depends(get_redis),
):
    if len(q) < 2:
        return {'results': []}

    try:
        # Resolve before consulting Redis, so an unpublished generation or
        # unavailable Finder product cannot be hidden by a warm cache entry.
        generation_row = await _ls.resolve_published_generation(pool)
        result_limit = max(0, min(limit, _ls.AUTOCOMPLETE_RESULT_LIMIT))
        query_hash = hashlib.sha256(q.lower().encode('utf-8')).hexdigest()
        cache_key = (
            f'ac:{AUTOCOMPLETE_CACHE_VERSION}:'
            f'g{generation_row["derived_generation_id"]}:'
            f's{generation_row["publication_sequence"]}:'
            f'{result_limit}:{query_hash}'
        )
        cached = await cache_get(cache_key, redis)
        if cached:
            return cached

        results = await _ls.local_db_autocomplete(q, pool, generation_row=generation_row)
    except Exception as exc:
        log.error('v3 autocomplete failed: %s', exc, exc_info=True)
        return _search_unavailable(f'autocomplete: {exc!r}',
                                    hint='Try again in a few seconds.')

    result = {'results': results[:result_limit], 'source': 'local_db'}
    await cache_set(cache_key, result, settings.ttl_autocomplete, redis)
    return result


# ---------------------------------------------------------------------------
# Local search
# ---------------------------------------------------------------------------
@router.post('/api/local/search', response_model=SearchResponse)
@limiter.limit(settings.rate_limit_search)
async def local_search_endpoint(
    request: Request,
    req: LocalSearchRequest,
    background_tasks: BackgroundTasks,
    pool: asyncpg.Pool = Depends(get_pool),
    redis: Optional[aioredis.Redis] = Depends(get_redis),
):
    background_tasks.add_task(inc_metric, 'db_queries')
    t0 = time.time()

    # Convert Pydantic sub-models to plain dicts before handing off to
    # local_search.py — that module reaches into the payload with
    # `.get()` and `.items()`, so model instances would AttributeError.
    # (Audit Phase 2 follow-up: the request models were tightened from
    # `Optional[dict]` to real Pydantic types so the OpenAPI schema is
    # portable across Pydantic versions; downstream still works on dicts.)
    _filters = (req.filters or SearchFilters())
    reference_coords = _complete_coords(req.reference_coords)
    if not req.galaxy_wide and reference_coords is None:
        raise HTTPException(
            status_code=422,
            detail='reference_coords must include x, y, z unless galaxy_wide is true.',
        )

    body_dict = {
        'reference_coords': reference_coords,
        'filters': {
            'distance':   (_filters.distance.model_dump()   if _filters.distance   else {}),
            'population': (_filters.population.model_dump() if _filters.population else {}),
            'economy':    _filters.economy or 'any',
        },
        'body_filters':   (req.body_filters.model_dump(exclude_none=True)
                           if req.body_filters else {}),
        'require_bio':    req.require_bio,
        'require_geo':    req.require_geo,
        'require_terra':  req.require_terra,
        'star_types':     req.star_types or [],
        'min_development_score': req.min_development_score or 0,
        'economy':        _filters.economy or 'any',
        'size':           req.size,
        'from':           req.from_,
        'sort_by':        req.sort_by or 'development',
        'galaxy_wide':    req.galaxy_wide,
        'galaxy_region_id': req.galaxy_region_id,
    }

    # Reject a disallowed request shape (422) before any database access, so
    # an unpublished Finder product (503) can never mask it. The rankings
    # route validates in the same order. local_db_search_v3 repeats this
    # pure check for direct callers.
    try:
        _ls.prepare_local_search_v3(body_dict)
    except HTTPException:
        raise
    except Exception as exc:
        # Keep the ranked read's error boundary: an unexpected failure here was
        # previously raised inside local_db_search_v3 and classified as 503.
        log.error(
            'prepare_local_search_v3 failed: type=%s repr=%r',
            type(exc).__name__, exc, exc_info=True,
        )
        return _search_unavailable(
            f'local search: {type(exc).__name__}: {exc}',
            hint='Reduce search radius or filter scope; if persistent, retry shortly.',
        )

    # Resolve the pinned PUBLISHED generation BEFORE consulting the cache.
    # This does two things a body-only key could not: (a) a request with no
    # published generation gets a 404 even on a cache hit, instead of a warm
    # entry masking the unavailable product; (b) the cache key is scoped to
    # the exact generation + ranking identity, so a governed publish or a
    # ranking-formula change can never serve a stale/legacy entry. A pinned
    # generation lookup is a single-row indexed read on v3_meta.
    try:
        generation_row = await _ls.resolve_published_generation(pool)
    except HTTPException:
        # Deliberate 4xx (e.g. 404 no published generation, 503 products not
        # ready) — surface it, don't mask.
        raise
    except Exception as exc:
        # The pre-cache generation lookup runs before the ranked-read try block
        # below, so without this it would bypass the route's error boundary and
        # surface as an uncaught 500 (missing V3 schema, connection acquire
        # failure, etc.). Classify it as search unavailability (503), the same
        # as a failure from the ranked read itself.
        log.error(
            'resolve_published_generation failed: type=%s repr=%r sqlstate=%s',
            type(exc).__name__, exc, getattr(exc, 'sqlstate', None),
            exc_info=True,
        )
        return _search_unavailable(
            f'local search: {type(exc).__name__}: {exc}',
            hint='Retry in a few seconds; if the problem persists, check /api/health.',
        )

    # Cache key includes every dimension that affects the result set — the
    # request body, the ranking identity, AND the resolved generation —
    # otherwise the cache silently serves stale data when sliders move, when
    # the ranking changes, or when a new generation is published.
    cache_key = _search_cache_key(body_dict, generation_row)
    cached = await cache_get(cache_key, redis)
    if cached:
        return cached

    try:
        result = await _ls.local_db_search_v3(body_dict, pool)
    except HTTPException:
        # A deliberate 4xx from the V3 path (e.g. no published Ratings V4
        # generation yet) — let FastAPI handle it, don't mask it as a 503.
        raise
    except Exception as exc:
        # Surface — don't mask. The previous code masked here and silently
        # served different ordering than callers expected (audit §C5).
        log.error(
            'local_db_search_v3 failed: type=%s repr=%r sqlstate=%s',
            type(exc).__name__, exc, getattr(exc, 'sqlstate', None),
            exc_info=True,
        )
        return _search_unavailable(
            f'local search: {type(exc).__name__}: {exc}',
            hint='Reduce search radius or filter scope; if persistent, retry shortly.',
        )

    # Store under the generation the result was ACTUALLY computed under, not the
    # one resolved before the cache lookup. A governed publish landing between
    # resolve_published_generation() above and the ranked read makes
    # local_db_search_v3() compute against the newer generation; keying the
    # store off the pre-resolve generation would file that newer data under the
    # old generation's key, to be served verbatim if that generation is ever
    # republished/rolled back. `result['ranking']` reports the exact snapshot the
    # rows came from (derived_generation_id + publication_sequence), so the
    # stored value always matches its key. With no race the two keys are
    # byte-identical, so this is a no-op in the common path.
    ranking_identity = result.get('ranking') or {}
    store_key = (
        _search_cache_key(body_dict, ranking_identity)
        if ranking_identity.get('derived_generation_id') is not None
        and ranking_identity.get('publication_sequence') is not None
        else cache_key
    )
    await cache_set(store_key, result, settings.ttl_search, redis)
    background_tasks.add_task(log_slow, 'local_search', (time.time() - t0) * 1000)
    return result


# ---------------------------------------------------------------------------
# Galaxy search (V3, F3 slice 2b)
# ---------------------------------------------------------------------------
def _galaxy_cache_key(body_dict: dict, generation_row) -> str:
    """`/api/search/galaxy` cache key: namespace version + ranking identity +
    the pinned PUBLISHED generation + the full request body, for the same
    reasons as `_search_cache_key`."""
    return (
        f"galaxy:{GALAXY_CACHE_VERSION}:{RANKING_VERSION}:{ranking_sha256()}:"
        f"g{generation_row['derived_generation_id']}:"
        f"s{generation_row['publication_sequence']}:"
        f"{json.dumps(body_dict, sort_keys=True, default=str)}"
    )


@router.post('/api/search/galaxy', response_model=SearchResponse)
@limiter.limit(settings.rate_limit_search)
async def galaxy_search(
    request: Request,
    req: GalaxySearchRequest,
    background_tasks: BackgroundTasks,
    pool: asyncpg.Pool = Depends(get_pool),
    redis: Optional[aioredis.Redis] = Depends(get_redis),
):
    # Galaxy-wide ranked search on the V3 projections.
    # The V3 galaxy search is the galaxy-wide Any-mode window of the ranked
    # search (`local_search.local_db_galaxy_search_v3`): the same bounded
    # access path, ranking profile, generation pin, product gate and
    # `SearchResponse` envelope as `POST /api/local/search` with
    # `galaxy_wide=true`. Legacy request features with no V3 projection
    # (a concrete `economy`, `min_score > 0`, `include_colonised=false`, any
    # undeclared field) answer 422 before any database access; the legacy
    # route's implicit
    # uncolonised-only restriction is gone because `v3_app.system_search`
    # carries no population column.
    background_tasks.add_task(inc_metric, 'db_queries')
    t0 = time.time()

    body_dict = {
        'economy':   req.economy.strip() if isinstance(req.economy, str) else req.economy,
        'min_score': req.min_score,
        'limit':     req.limit,
        'offset':    req.offset,
        # Forwarded so an explicit legacy uncolonised-only request reaches the
        # V3 validator (422) instead of being silently widened to all systems.
        'include_colonised': req.include_colonised,
    }

    # Reject a disallowed request shape (422) before any database access, so
    # an unpublished Finder product (503) can never mask it.
    try:
        _ls.prepare_galaxy_search_v3(body_dict)
    except HTTPException:
        raise
    except Exception as exc:
        log.error(
            'prepare_galaxy_search_v3 failed: type=%s repr=%r',
            type(exc).__name__, exc, exc_info=True,
        )
        return _search_unavailable(
            f'galaxy search: {type(exc).__name__}: {exc}',
            hint='Reduce the result window; if persistent, retry shortly.',
        )

    # Pin the PUBLISHED generation before consulting the cache (see the
    # local-search route for why the key must be generation-scoped).
    try:
        generation_row = await _ls.resolve_published_generation(pool)
    except HTTPException:
        raise
    except Exception as exc:
        log.error(
            'resolve_published_generation failed: type=%s repr=%r sqlstate=%s',
            type(exc).__name__, exc, getattr(exc, 'sqlstate', None),
            exc_info=True,
        )
        return _search_unavailable(
            f'galaxy search: {type(exc).__name__}: {exc}',
            hint='Retry in a few seconds; if the problem persists, check /api/health.',
        )

    cache_key = _galaxy_cache_key(body_dict, generation_row)
    cached = await cache_get(cache_key, redis)
    if cached:
        return cached

    try:
        result = await _ls.local_db_galaxy_search_v3(body_dict, pool)
    except HTTPException:
        # A deliberate 4xx/503 from the V3 path (no published generation,
        # products not ready) — surface it, don't mask it as a generic 503.
        raise
    except Exception as exc:
        log.error(
            'local_db_galaxy_search_v3 failed: type=%s repr=%r sqlstate=%s',
            type(exc).__name__, exc, getattr(exc, 'sqlstate', None),
            exc_info=True,
        )
        return _search_unavailable(f'galaxy search: {type(exc).__name__}: {exc}')

    # Store under the generation the result was ACTUALLY computed under (a
    # governed publish may land between the pin above and the ranked read).
    ranking_identity = result.get('ranking') or {}
    store_key = (
        _galaxy_cache_key(body_dict, ranking_identity)
        if ranking_identity.get('derived_generation_id') is not None
        and ranking_identity.get('publication_sequence') is not None
        else cache_key
    )
    await cache_set(store_key, result, settings.ttl_search, redis)
    background_tasks.add_task(log_slow, 'galaxy_search', (time.time() - t0) * 1000)
    return result


# ---------------------------------------------------------------------------
# Cluster search (V3, F3 slice 2b — fail-closed until the cluster product exists)
# ---------------------------------------------------------------------------
@router.post('/api/search/cluster', response_model=ClusterSearchResponse)
@limiter.limit(settings.rate_limit_search)
async def cluster_search(
    request: Request,
    req: ClusterSearchRequest,
    background_tasks: BackgroundTasks,
    pool: asyncpg.Pool = Depends(get_pool),
):
    # Multi-economy cluster search on the V3 cluster product.
    # Reads only V3 relations (`local_search.local_db_cluster_search_v3`):
    # the request is validated first (400/422), then the published generation
    # is pinned behind the Finder product gate (404/503), then the
    # Finder-owned `v3_spatial.cluster_run` state for that generation is
    # read. No Finder cluster run has been built or published yet and the
    # per-economy viability summary the `ClusterResult` fields need has no
    # V3 contract, so the route currently answers 503 naming that gap rather
    # than reading the retired V2 macro-grid relation or fabricating
    # clusters at request time. There is no cache: nothing cacheable is
    # produced until the product exists, and any future entry must be
    # generation-scoped like `_search_cache_key`.
    background_tasks.add_task(inc_metric, 'db_queries')

    # Validation: either requirements or slots must be provided
    # (model_validator on ClusterSearchRequest already enforces this)
    if not req.requirements and not req.slots:
        raise HTTPException(400, 'At least one economy requirement must be specified')
    if req.requirements and len(req.requirements) > 6:
        raise HTTPException(400, 'Maximum 6 economy requirements')
    if req.slots and len(req.slots) > 10:
        raise HTTPException(400, 'Maximum 10 slots')

    # Pass the raw coordinates (not `_complete_coords`, which collapses a
    # partial object to None) so the V3 validator rejects a partial anchor
    # with 422 instead of silently treating it as "no reference point".
    reference_coords = (
        req.reference_coords.model_dump() if req.reference_coords is not None else None
    )
    body_dict: dict[str, Any] = {
        'limit':            req.limit,
        'offset':           req.offset,
        'reference_coords': reference_coords,
        'galaxy_region_id': req.galaxy_region_id,
    }
    if req.slots:
        body_dict['slots'] = [s.model_dump() for s in req.slots]
    else:
        body_dict['requirements'] = [r.model_dump() for r in req.requirements]

    # 422 before any database access, so the 503 product gate can never
    # mask a malformed request.
    try:
        _ls.prepare_cluster_search_v3(body_dict)
    except HTTPException:
        raise
    except Exception as exc:
        log.error(
            'prepare_cluster_search_v3 failed: type=%s repr=%r',
            type(exc).__name__, exc, exc_info=True,
        )
        return _search_unavailable(f'cluster search: {type(exc).__name__}: {exc}')

    try:
        result = await _ls.local_db_cluster_search_v3(body_dict, pool)
    except HTTPException:
        # Deliberate 404/503 from the V3 product gate — surface it.
        raise
    except Exception as exc:
        log.error(
            'local_db_cluster_search_v3 failed: type=%s repr=%r sqlstate=%s',
            type(exc).__name__, exc, getattr(exc, 'sqlstate', None),
            exc_info=True,
        )
        return _search_unavailable(f'cluster search: {type(exc).__name__}: {exc}')

    return result
