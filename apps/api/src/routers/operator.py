"""Read-only operator visibility endpoints for Stage 19 source-run state."""
from __future__ import annotations

from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from edfinder_api.deps import get_pool, require_admin
from edfinder_api.operator_visibility import (
    get_legacy_bridge_for_source_run,
    get_operator_safety_gates,
    get_source_run_artifacts,
    get_source_run_detail,
    get_staging_impact_for_source_run,
    list_diagnostic_staging_rows,
    list_recent_source_runs,
    to_operator_visibility_dict,
)


router = APIRouter(tags=['operator'])


@router.get('/api/operator/source-runs', dependencies=[Depends(require_admin)])
async def operator_source_runs(
    limit: int = Query(25, ge=1),
    status: Optional[str] = None,
    source_name: Optional[str] = None,
    domain: Optional[str] = None,
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            rows = await list_recent_source_runs(
                conn,
                limit=limit,
                status=status,
                source_name=source_name,
                domain=domain,
            )
    return to_operator_visibility_dict(rows)


@router.get('/api/operator/source-run-detail', dependencies=[Depends(require_admin)])
async def operator_source_run_detail(
    source_run_key: str = Query(..., min_length=1),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            detail = await get_source_run_detail(conn, source_run_key)
    if detail is None:
        raise HTTPException(404, 'Source run not found')
    return to_operator_visibility_dict(detail)


@router.get('/api/operator/source-run-artifacts', dependencies=[Depends(require_admin)])
async def operator_source_run_artifacts(
    source_run_key: str = Query(..., min_length=1),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            summary = await get_source_run_artifacts(conn, source_run_key)
    return to_operator_visibility_dict(summary)


@router.get('/api/operator/source-run-bridge', dependencies=[Depends(require_admin)])
async def operator_source_run_bridge(
    source_run_key: str = Query(..., min_length=1),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            summary = await get_legacy_bridge_for_source_run(conn, source_run_key)
    return to_operator_visibility_dict(summary)


@router.get('/api/operator/source-run-staging-impact', dependencies=[Depends(require_admin)])
async def operator_source_run_staging_impact(
    source_run_key: str = Query(..., min_length=1),
    limit: int = Query(100, ge=1),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            impact = await get_staging_impact_for_source_run(conn, source_run_key, limit=limit)
    return {
        'source_run_key': source_run_key,
        'bridge_present': impact is not None,
        'staging_impact': to_operator_visibility_dict(impact) if impact is not None else None,
    }


@router.get('/api/operator/diagnostic-staging-rows', dependencies=[Depends(require_admin)])
async def operator_diagnostic_staging_rows(
    source_run_key: Optional[str] = None,
    limit: int = Query(100, ge=1),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            rows = await list_diagnostic_staging_rows(
                conn,
                source_run_key=source_run_key,
                limit=limit,
            )
    return to_operator_visibility_dict(rows)


@router.get('/api/operator/safety-gates', dependencies=[Depends(require_admin)])
async def operator_safety_gates(pool: asyncpg.Pool = Depends(get_pool)):
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            summary = await get_operator_safety_gates(conn)
    return to_operator_visibility_dict(summary)
