"""Profile sync — single-slot JSONB paste-bin keyed by user-chosen sync key.

Endpoints
---------
GET  /api/profile/sync/{sync_key}  → {blob, updated_at} | 404
PUT  /api/profile/sync/{sync_key}  → {updated_at, blob_bytes}

The sync key IS the credential — users pick a hard-to-guess string and
share it across devices. Two devices with the same key share one slot;
last-write-wins. No auth header, no JWT, no session — same trust model
as /api/watchlist.

Validation: keys must match  ^[A-Za-z0-9_-]{16,128}$  (matches the
CHECK constraint in sql/007_profile_sync.sql).
"""
import json
import re
from typing import Any

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field

from edfinder_api.config import limiter
from edfinder_api.deps import get_pool

router = APIRouter(tags=['profile-sync'])

_SYNC_KEY_RE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')
_MAX_BLOB_BYTES = 1_048_576   # 1 MiB; matches DB CHECK constraint.


class ProfileSyncBlob(BaseModel):
    """The shape is intentionally `Any` — the frontend owns the schema.

    This is a pastebin slot; we don't validate sub-fields because the
    feature set evolves on the client and the backend shouldn't be a
    moving target every time we add a new tab.
    """
    blob: Any = Field(..., description='Arbitrary client-managed payload.')


def _validate_sync_key(sync_key: str) -> None:
    if not _SYNC_KEY_RE.match(sync_key):
        raise HTTPException(
            status_code=400,
            detail='sync_key must be 16-128 chars, alphanumeric + "_" or "-" only.',
        )


@router.get('/api/profile/sync/{sync_key}')
@limiter.limit('30/minute')
async def get_profile_sync(
    request: Request,
    sync_key: str = Path(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
):
    _validate_sync_key(sync_key)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            'SELECT blob, updated_at, blob_bytes FROM profile_sync WHERE sync_key = $1',
            sync_key,
        )
    if row is None:
        raise HTTPException(404, 'No profile slot for sync_key (run a Push first)')
    return {
        'blob':       row['blob'],   # asyncpg auto-decodes JSONB → dict
        'updated_at': row['updated_at'].isoformat(),
        'blob_bytes': row['blob_bytes'],
    }


@router.put('/api/profile/sync/{sync_key}')
@limiter.limit('10/minute')
async def put_profile_sync(
    request: Request,
    sync_key: str = Path(..., min_length=16, max_length=128),
    body: ProfileSyncBlob = Body(...),
    pool: asyncpg.Pool = Depends(get_pool),
):
    _validate_sync_key(sync_key)

    # Size check uses a serialised form, but we pass the raw dict to
    # asyncpg — the JSONB codec registered in main.py's pool init
    # encodes once. (Audit fix Phase 6: the previous `payload = json.dumps(...)`
    # path double-encoded under the codec, storing a JSON-string of a
    # JSON-string in the JSONB column.)
    serialised = json.dumps(body.blob, separators=(',', ':'))
    size = len(serialised.encode('utf-8'))
    if size > _MAX_BLOB_BYTES:
        raise HTTPException(
            413,
            f'Profile blob too large: {size} bytes (max {_MAX_BLOB_BYTES}). '
            'Trim Pinned / Compare / Colony lists or split across keys.',
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO profile_sync (sync_key, blob, blob_bytes, updated_at)
                 VALUES ($1, $2, $3, now())
            ON CONFLICT (sync_key) DO UPDATE
                SET blob       = EXCLUDED.blob,
                    blob_bytes = EXCLUDED.blob_bytes,
                    updated_at = now()
            RETURNING updated_at, blob_bytes
            """,
            sync_key, body.blob, size,
        )
    return {
        'updated_at': row['updated_at'].isoformat(),
        'blob_bytes': row['blob_bytes'],
    }


@router.delete('/api/profile/sync/{sync_key}')
@limiter.limit('5/minute')
async def delete_profile_sync(
    request: Request,
    sync_key: str = Path(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Wipe a sync slot. Safe to call when the slot doesn't exist."""
    _validate_sync_key(sync_key)
    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM profile_sync WHERE sync_key = $1', sync_key)
    return {'ok': True}
