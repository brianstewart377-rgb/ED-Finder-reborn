"""Watchlist endpoints — scoped by user-chosen sync_key.

Audit fix (2026-05-08, AUDIT_REPORT.md §H1 / Phase 3):
    The old /api/watchlist surface was a single global namespace —
    every visitor shared the same watchlist. This file now requires
    a sync_key path parameter (16-128 chars, [A-Za-z0-9_-]). The old
    un-scoped endpoints return HTTP 410 Gone with a migration hint.

The sync_key IS the credential. Same trust model as /api/profile/sync.
"""
import re

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Request

from edfinder_api.config import limiter
from edfinder_api.deps import get_pool
from edfinder_api.helpers import safe_coords_from_row
from edfinder_api.models import WatchlistAlert
from edfinder_api.v3_schema import current_generation_schema

router = APIRouter(tags=['watchlist'])

_SYNC_KEY_RE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')
_LEGACY_KEY  = 'legacy'   # reserved for the migration row-tag, not for clients


def _validate_sync_key(sync_key: str) -> None:
    if sync_key == _LEGACY_KEY:
        raise HTTPException(
            status_code=400,
            detail='sync_key="legacy" is reserved for migration; choose a real key.',
        )
    if not _SYNC_KEY_RE.fullmatch(sync_key):
        raise HTTPException(
            status_code=400,
            detail='sync_key must be 16-128 chars, alphanumeric + "_" or "-" only.',
        )


# ---------------------------------------------------------------------------
# /api/v2/watchlist/{sync_key}  — sync_key-scoped surface
# ---------------------------------------------------------------------------
@router.get('/api/v2/watchlist/{sync_key}')
@limiter.limit('60/minute')
async def get_watchlist(
    request: Request,
    sync_key: str = Path(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
):
    _validate_sync_key(sync_key)
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT w.*,
                   NULL::integer AS score,
                   NULL::text AS economy_suggestion,
                   w.alert_min_score AS alert_min_development_score,
                   NULL::text AS primary_archetype,
                   NULL::text AS secondary_archetype,
                   NULL::integer AS archetype_score,
                   NULL::integer AS buildability_score,
                   NULL::integer AS purity_score
              FROM v3_private.watchlist w
             WHERE w.sync_key = $1
          ORDER BY w.added_at DESC, w.system_id64
        """, sync_key)
    return {'sync_key': sync_key, 'watchlist': [dict(r) for r in rows]}


@router.post('/api/v2/watchlist/{sync_key}/{id64}')
@limiter.limit('20/minute')
async def add_watchlist(
    request: Request,
    id64: int = Path(..., ge=1, le=9223372036854775807),
    sync_key: str = Path(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
):
    _validate_sync_key(sync_key)
    schema = await current_generation_schema(pool)
    if schema is None:
        raise HTTPException(503, 'No published canonical generation')
    async with pool.acquire() as conn:
        # The shared resolver validates the schema identifier; values remain
        # parameterised. Saving never depends on a Finder derived build.
        sys_row = await conn.fetchrow(
            f'SELECT name, x_ly AS x, y_ly AS y, z_ly AS z '
            f'FROM {schema}.systems WHERE id64 = $1',
            id64,
        )
        if not sys_row:
            raise HTTPException(404, f'System {id64} not found')
        coords = safe_coords_from_row({'id64': id64, **dict(sys_row)})
        await conn.execute("""
            INSERT INTO v3_private.watchlist (sync_key, system_id64, name, x, y, z)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (sync_key, system_id64) DO NOTHING
        """, sync_key, id64, sys_row['name'], coords['x'], coords['y'], coords['z'])
    return {'ok': True, 'sync_key': sync_key}


@router.delete('/api/v2/watchlist/{sync_key}/{id64}')
@limiter.limit('20/minute')
async def remove_watchlist(
    request: Request,
    id64: int = Path(..., ge=1, le=9223372036854775807),
    sync_key: str = Path(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
):
    _validate_sync_key(sync_key)
    async with pool.acquire() as conn:
        await conn.execute(
            'DELETE FROM v3_private.watchlist WHERE sync_key = $1 AND system_id64 = $2',
            sync_key, id64,
        )
    return {'ok': True}


@router.patch('/api/v2/watchlist/{sync_key}/{id64}/alert')
@limiter.limit('20/minute')
async def update_alert(
    request: Request,
    alert: WatchlistAlert,
    id64: int = Path(..., ge=1, le=9223372036854775807),
    sync_key: str = Path(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
):
    _validate_sync_key(sync_key)
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE v3_private.watchlist
               SET alert_min_score = $1, alert_economy = $2
             WHERE sync_key = $3 AND system_id64 = $4
        """, alert.min_development_score, alert.economy, sync_key, id64)
    return {'ok': True}


@router.get('/api/v2/watchlist/{sync_key}/changes')
@limiter.limit('60/minute')
async def watchlist_changes(
    request: Request,
    sync_key: str = Path(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Changes for systems currently in this sync_key's watchlist."""
    _validate_sync_key(sync_key)
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.id, c.system_id64, c.system_name, c.change_type,
                   c.old_value, c.new_value, c.detected_at
              FROM v3_private.watchlist_changelog c
              JOIN v3_private.watchlist w
                ON w.system_id64 = c.system_id64
               AND w.sync_key    = c.sync_key
               AND w.sync_key    = $1
          ORDER BY c.detected_at DESC, c.id DESC
             LIMIT 100
        """, sync_key)
    return {'changes': [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# Legacy un-scoped endpoints — HTTP 410 Gone
# ---------------------------------------------------------------------------
_GONE_BODY = {
    'type':   'https://ed-finder.app/problem/sync-key-required',
    'title':  'Endpoint moved — sync_key required',
    'status': 410,
    'detail':
        'The unscoped watchlist surface was retired in v3.5 (audit §H1). '
        'Use /api/v2/watchlist/{sync_key}[/...] instead. Pick a 16-128 char '
        'string of [A-Za-z0-9_-]; share it across your devices to sync.',
}


def _gone() -> dict:
    raise HTTPException(status_code=410, detail=_GONE_BODY)


@router.get('/api/watchlist')
async def legacy_get():     _gone()

@router.post('/api/watchlist/{id64}')
async def legacy_post(id64: int):  _gone()

@router.delete('/api/watchlist/{id64}')
async def legacy_delete(id64: int): _gone()

@router.patch('/api/watchlist/{id64}/alert')
async def legacy_patch(id64: int, alert: WatchlistAlert): _gone()

@router.get('/api/watchlist/changes')
async def legacy_changes():   _gone()

@router.get('/api/watchlist/changelog')
async def legacy_changelog(): _gone()
