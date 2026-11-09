"""Account-scoped offers, separate sharing permission, and owner-only review."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from edfinder_api.auth import get_request_user, require_same_origin
from edfinder_api.config import limiter
from edfinder_api.deps import get_pool
from edfinder_api.journal import contributions

router = APIRouter(prefix='/api/v1/journal/galaxy-contributions', tags=['v3-journal-contributions'])


class OfferRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    # An explicit action for this import only; never inherited from research consent.
    policy_version: Literal['journal-galaxy-physical-v1']
    share_on_site_and_api: Literal[True]
    file_sha256: list[Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]] = Field(min_length=1, max_length=200)


class OfferReceipt(BaseModel):
    new_offers: int
    already_offered: int
    skipped: dict[str, int]


class ContributionRow(BaseModel):
    contribution_id: uuid.UUID
    contribution_state: str
    offered_at: datetime
    decided_at: datetime | None
    observation: dict[str, Any]
    used_in_generation: bool


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    contribution_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    decision: Literal['ELIGIBLE', 'REJECTED']
    reason: str = Field(min_length=1, max_length=500)


async def _user(request: Request, *, owner: bool = False, write: bool = False):
    user = await get_request_user(request)
    if user is None:
        raise HTTPException(401, 'Sign-in required')
    if owner and (not user.is_owner or not user.recently_authenticated):
        raise HTTPException(403, 'Recently authenticated owner required for review')
    if write:
        require_same_origin(request)
    return user


@router.post('/imports/{import_id}', response_model=OfferReceipt, operation_id='offerJournalGalaxyFacts')
@limiter.limit('10/minute')
async def offer_journal_galaxy_facts(import_id: uuid.UUID, request: Request, body: OfferRequest,
                                   pool: asyncpg.Pool = Depends(get_pool)):
    user = await _user(request, write=True)
    return await contributions.offer_import(pool, user.account_id, import_id, file_sha256=body.file_sha256)


@router.get('', response_model=list[ContributionRow], operation_id='listJournalGalaxyContributions')
async def list_journal_galaxy_contributions(request: Request, offset: int = Query(0, ge=0),
                                          limit: int = Query(50, ge=1, le=200),
                                          pool: asyncpg.Pool = Depends(get_pool)):
    user = await _user(request)
    return await contributions.list_contributions(pool, account_id=user.account_id, offset=offset, limit=limit)


@router.get('/review', response_model=list[ContributionRow], operation_id='reviewJournalGalaxyQueue')
async def review_journal_galaxy_queue(request: Request, offset: int = Query(0, ge=0),
                                     limit: int = Query(50, ge=1, le=200),
                                     pool: asyncpg.Pool = Depends(get_pool)):
    await _user(request, owner=True)
    return await contributions.list_contributions(pool, account_id=None, offset=offset, limit=limit)


@router.post('/review', response_model=dict[str, int], operation_id='decideJournalGalaxyContributions')
@limiter.limit('10/minute')
async def decide_journal_galaxy_contributions(request: Request, body: ReviewRequest,
                                             pool: asyncpg.Pool = Depends(get_pool)):
    user = await _user(request, owner=True, write=True)
    count = await contributions.review(pool, ids=body.contribution_ids,
                                       eligible=body.decision == 'ELIGIBLE', actor_id=user.account_id,
                                       reason=body.reason)
    return {'reviewed': count}


@router.post('/{contribution_id}/withdraw', response_model=dict[str, bool], operation_id='withdrawJournalGalaxyContribution')
@limiter.limit('30/minute')
async def withdraw_journal_galaxy_contribution(contribution_id: uuid.UUID, request: Request,
                                              pool: asyncpg.Pool = Depends(get_pool)):
    user = await _user(request, write=True)
    changed = await contributions.withdraw(pool, user.account_id, contribution_id)
    return {'withdrawn': True, 'changed': changed}
