"""Per-system user notes — sync_key-scoped CRUD on `system_notes`.

Audit fix (2026-05-08, AUDIT_REPORT.md §H1 / Phase 3):
    The old /api/systems/{id64}/note surface was global; everyone shared
    one note per system. Notes are now keyed by (sync_key, system_id64).
    Old un-scoped endpoints return HTTP 410 Gone.
"""
import re

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Request

from edfinder_api.config import limiter
from edfinder_api.deps import get_pool
from edfinder_api.models import NoteBody

router = APIRouter(tags=['notes'])

_SYNC_KEY_RE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')


def _validate_sync_key(sync_key: str) -> None:
    if sync_key == 'legacy':
        raise HTTPException(400, 'sync_key="legacy" is reserved for migration.')
    if not _SYNC_KEY_RE.match(sync_key):
        raise HTTPException(
            400,
            'sync_key must be 16-128 chars, alphanumeric + "_" or "-" only.',
        )


# ---------------------------------------------------------------------------
# /api/v2/systems/{sync_key}/{id64}/note  — sync_key-scoped
# ---------------------------------------------------------------------------
@router.get('/api/v2/systems/{sync_key}/{id64}/note')
@limiter.limit('60/minute')
async def get_note(
    request: Request,
    id64: int,
    sync_key: str = Path(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
):
    _validate_sync_key(sync_key)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            'SELECT note, updated_at FROM system_notes '
            'WHERE sync_key = $1 AND system_id64 = $2',
            sync_key, id64,
        )
    return {
        'sync_key':   sync_key,
        'system_id64': id64,
        'note':       row['note'] if row else '',
        'updated_at': str(row['updated_at']) if row else None,
    }


@router.post('/api/v2/systems/{sync_key}/{id64}/note')
@limiter.limit('20/minute')
async def save_note(
    request: Request,
    id64: int,
    body: NoteBody,
    sync_key: str = Path(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
):
    _validate_sync_key(sync_key)
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO system_notes (sync_key, system_id64, note, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (sync_key, system_id64) DO UPDATE
               SET note = $3, updated_at = NOW()
        """, sync_key, id64, body.note)
    return {'ok': True}


@router.delete('/api/v2/systems/{sync_key}/{id64}/note')
@limiter.limit('20/minute')
async def delete_note(
    request: Request,
    id64: int,
    sync_key: str = Path(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
):
    _validate_sync_key(sync_key)
    async with pool.acquire() as conn:
        await conn.execute(
            'DELETE FROM system_notes WHERE sync_key = $1 AND system_id64 = $2',
            sync_key, id64,
        )
    return {'ok': True}


@router.get('/api/v2/systems/{sync_key}/notes')
@limiter.limit('60/minute')
async def list_all_notes(
    request: Request,
    sync_key: str = Path(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """All notes for a single sync_key — used by the v2 frontend's
    'My Notes' tab."""
    _validate_sync_key(sync_key)
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT n.system_id64, s.name, n.note, n.updated_at
              FROM system_notes n
              JOIN systems s ON s.id64 = n.system_id64
             WHERE n.sync_key = $1
          ORDER BY n.updated_at DESC
             LIMIT 500
        """, sync_key)
    return {'sync_key': sync_key, 'notes': [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# Legacy un-scoped endpoints — HTTP 410 Gone
# ---------------------------------------------------------------------------
_GONE_BODY = {
    'type':   'https://ed-finder.app/problem/sync-key-required',
    'title':  'Endpoint moved — sync_key required',
    'status': 410,
    'detail':
        'The unscoped notes surface was retired in v3.5 (audit §H1). '
        'Use /api/v2/systems/{sync_key}/{id64}/note instead.',
}


def _gone():
    raise HTTPException(status_code=410, detail=_GONE_BODY)


@router.get('/api/systems/{id64}/note')
async def legacy_get(id64: int):    _gone()

@router.post('/api/systems/{id64}/note')
async def legacy_post(id64: int, body: NoteBody): _gone()

@router.delete('/api/systems/{id64}/note')
async def legacy_delete(id64: int): _gone()
