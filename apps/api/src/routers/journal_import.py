import re

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Request

from edfinder_api.config import limiter
from edfinder_api.deps import get_pool, require_admin
from edfinder_api.journal_import.api_models import (
    JournalImportReceipt,
    JournalImportRequest,
    JournalPromotionReceipt,
    JournalTelemetrySummaryResponse,
)
from edfinder_api.journal_import import store

router = APIRouter(tags=['journal-import'])
_SYNC_KEY_RE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')


def _validate_sync_key(sync_key: str) -> None:
    if sync_key == 'legacy':
        raise HTTPException(400, 'sync_key="legacy" is reserved for migration')
    if not _SYNC_KEY_RE.match(sync_key):
        raise HTTPException(400, 'sync_key must be 16-128 chars, alphanumeric + "_" or "-" only.')


@router.post('/api/journal/import', response_model=JournalImportReceipt)
@limiter.limit('5/minute')
async def import_frontier_journal(
    request: Request,
    body: JournalImportRequest,
    pool: asyncpg.Pool = Depends(get_pool),
) -> JournalImportReceipt:
    try:
        return await store.import_journal_batch(pool, body)
    except store.JournalImportRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.get('/api/journal/imports/{run_key}', response_model=JournalImportReceipt)
@limiter.limit('30/minute')
async def get_frontier_journal_import(
    request: Request,
    run_key: str,
    pool: asyncpg.Pool = Depends(get_pool),
) -> JournalImportReceipt:
    receipt = await store.get_journal_import_receipt(pool, run_key)
    if receipt is None:
        raise HTTPException(404, f'Journal import run {run_key} not found')
    return receipt


@router.get('/api/journal/telemetry/{sync_key}', response_model=JournalTelemetrySummaryResponse)
@limiter.limit('60/minute')
async def get_frontier_journal_telemetry(
    request: Request,
    sync_key: str = Path(..., min_length=16, max_length=128),
    pool: asyncpg.Pool = Depends(get_pool),
) -> JournalTelemetrySummaryResponse:
    del request
    _validate_sync_key(sync_key)
    return await store.get_journal_telemetry_summary(pool, sync_key)


@router.post(
    '/api/journal/imports/{run_key}/promote',
    response_model=JournalPromotionReceipt,
    dependencies=[Depends(require_admin)],
)
@limiter.limit('5/minute')
async def promote_frontier_journal_import(
    request: Request,
    run_key: str,
    pool: asyncpg.Pool = Depends(get_pool),
) -> JournalPromotionReceipt:
    receipt = await store.promote_journal_batch(pool, run_key)
    if receipt is None:
        raise HTTPException(404, f'Journal import run {run_key} not found')
    return receipt
