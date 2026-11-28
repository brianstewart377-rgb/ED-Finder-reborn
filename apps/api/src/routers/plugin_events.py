"""Where ED-Finder's EDMC plugin sends a member's journal events
(``POST /api/v1/plugin/events``). "Haul — design", step 2b.

The plugin is a program on the member's PC, not a browser, so it signs in
with the member's key (``Authorization: Bearer <key>``, made on the Account
page, step 2a) instead of a session cookie. The site then checks that the
journal is that of one of the member's own verified commanders, as the
journal upload does; any other commander's is refused whole.

Only the kinds of event Haul uses may be sent (``readings.KINDS``), at most
500 at a time. Construction-site readings are kept now; the rest are taken
up by later steps. Sending the same events twice changes nothing.

Until migration 034 has been applied the address answers 503.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from edfinder_api.config import limiter
from edfinder_api.deps import get_pool
from edfinder_api.haul import keys, readings
from edfinder_api.journal.commanders import FID_PATTERN, verified_commanders

router = APIRouter(prefix="/api/v1/plugin", tags=["plugin"])

MAX_EVENTS = 500
NOT_READY = "Haul is not ready on this site yet."
NOT_LINKED = "This commander is not linked to your ED-Finder account."


class PluginBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The Frontier ID of the commander whose journal this is.
    fid: str = Field(min_length=2, max_length=24)
    plugin_version: str = Field(min_length=1, max_length=20)
    # The last Docked event the plugin saw before these events, so that a
    # site reading in this batch can be named.
    docked: Optional[dict[str, Any]] = None
    events: list[dict[str, Any]] = Field(max_length=MAX_EVENTS)


class PluginReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    received: int
    kept: int
    repeated: int
    left_for_later: int
    refused: int
    reasons: list[str]


def _key_from(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


@router.post("/events", response_model=PluginReceipt, operation_id="sendPluginEvents")
@limiter.limit("240/minute")
async def send_plugin_events(
    request: Request,
    batch: PluginBatch,
    authorization: Optional[str] = Header(default=None),
    pool: asyncpg.Pool = Depends(get_pool),
) -> PluginReceipt:
    secret = _key_from(authorization)
    if secret is None:
        raise HTTPException(401, "A plugin key is needed", headers={"WWW-Authenticate": "Bearer"})
    async with pool.acquire() as conn:
        if not (await keys.ready(conn) and await readings.ready(conn)):
            raise HTTPException(503, NOT_READY)
        async with conn.transaction():
            account = await keys.account_for_key(conn, secret)
            if account is None:
                raise HTTPException(401, "This plugin key is not accepted", headers={"WWW-Authenticate": "Bearer"})
            linked = {row["journal_fid"] for row in await verified_commanders(conn, account)}
            if not FID_PATTERN.fullmatch(batch.fid) or batch.fid not in linked:
                raise HTTPException(403, NOT_LINKED)
            outcome = await readings.take(conn, account, batch.events, batch.docked)
    return PluginReceipt(
        received=outcome.received, kept=outcome.kept, repeated=outcome.repeated,
        left_for_later=outcome.left_for_later, refused=outcome.refused, reasons=outcome.reasons)
