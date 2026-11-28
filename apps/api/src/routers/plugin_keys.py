"""Keys for ED-Finder's EDMC plugin (``/api/v1/plugin/keys``).

"Haul — design", step 2a. A signed-in member lists, makes and cancels their
own keys on the Account page. A key is shown once, in the answer that makes
it; only its SHA-256 is kept. Making a key needs a recent sign-in, as linking
another identity does, because a key lets a program send to the member's
account. Every write needs the site's own origin. Making and cancelling are
written to the security record.

Until migration 034 has been applied the routes answer 503.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from edfinder_api.auth import AuthenticatedUser, get_request_user, require_same_origin, write_security_audit_event
from edfinder_api.config import limiter
from edfinder_api.deps import get_pool
from edfinder_api.haul import keys

router = APIRouter(prefix="/api/v1/plugin/keys", tags=["plugin-keys"])

NOT_READY = "Plugin keys are not ready on this site yet."


class PluginKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_key_id: uuid.UUID
    label: str
    created_at: datetime
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class PluginKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=200)


class NewPluginKey(BaseModel):
    """The key itself is in ``secret``, here and nowhere else, ever."""
    model_config = ConfigDict(extra="forbid")

    key: PluginKey
    secret: str


async def _member(request: Request) -> AuthenticatedUser:
    user = await get_request_user(request)
    if user is None:
        raise HTTPException(401, "Sign-in required")
    return user


async def _ready(conn) -> None:
    if not await keys.ready(conn):
        raise HTTPException(503, NOT_READY)


@router.get("", response_model=list[PluginKey], operation_id="listPluginKeys")
async def list_plugin_keys(request: Request, pool: asyncpg.Pool = Depends(get_pool)) -> list[PluginKey]:
    user = await _member(request)
    async with pool.acquire() as conn:
        await _ready(conn)
        return [PluginKey(**row) for row in await keys.list_keys(conn, user.account_id)]


@router.post("", response_model=NewPluginKey, status_code=201, operation_id="createPluginKey")
@limiter.limit("10/hour")
async def create_plugin_key(
    request: Request, body: PluginKeyRequest, pool: asyncpg.Pool = Depends(get_pool),
) -> NewPluginKey:
    require_same_origin(request)
    user = await _member(request)
    if not user.recently_authenticated:
        raise HTTPException(403, "Sign in again to make a key")
    async with pool.acquire() as conn:
        await _ready(conn)
        try:
            keys.clean_label(body.label)
        except keys.KeyRefused as refused:
            raise HTTPException(422, str(refused)) from None
        async with conn.transaction():
            try:
                row, secret = await keys.make_key(conn, user.account_id, body.label)
            except keys.KeyRefused as refused:
                raise HTTPException(409, str(refused)) from None
            await write_security_audit_event(
                conn, event_type="plugin_key.created", succeeded=True, account_id=user.account_id,
                metadata={"plugin_key_id": str(row["plugin_key_id"])})
    return NewPluginKey(key=PluginKey(**row), secret=secret)


@router.delete("/{plugin_key_id}", status_code=204, operation_id="revokePluginKey")
async def revoke_plugin_key(
    request: Request, plugin_key_id: uuid.UUID, pool: asyncpg.Pool = Depends(get_pool),
) -> Response:
    require_same_origin(request)
    user = await _member(request)
    async with pool.acquire() as conn:
        await _ready(conn)
        async with conn.transaction():
            if not await keys.revoke_key(conn, user.account_id, plugin_key_id):
                raise HTTPException(404, "No such key")
            await write_security_audit_event(
                conn, event_type="plugin_key.revoked", succeeded=True, account_id=user.account_id,
                metadata={"plugin_key_id": str(plugin_key_id)})
    return Response(status_code=204)
