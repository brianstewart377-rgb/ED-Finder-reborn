"""Admin + cache-management endpoints.

Every write endpoint in this module is guarded by `require_admin`, which
is disabled entirely unless the ADMIN_TOKEN env var is set. Nginx further
restricts /api/admin/* to 127.0.0.1 as defence in depth.
"""
from datetime import datetime, timezone
from typing   import Any, Optional

import asyncpg
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from edfinder_api.config import limiter, log, settings
from edfinder_api.deps import get_pool, get_readonly_pool, get_redis, require_admin
from edfinder_api.enrichment_operator_status import (
    read_enrichment_status_snapshot,
    read_warehouse_status_snapshot,
)
from edfinder_api.helpers import CLUSTER_REBUILD_GONE_PROBLEM
from edfinder_api.models import CacheStatsResponse, RetiredRouteProblem
from shared_contracts.data_invariant_contracts import (
    ADMIN_DATA_INVARIANT_CHECK_KEYS as _ADMIN_DATA_INVARIANT_CHECK_KEYS,
    COLONISATION_STATUS_AGE_BUCKETS_SQL,
    SHARED_DATA_INVARIANT_SCALAR_CHECKS_BY_KEY,
    normalise_colonisation_status_age_buckets,
)
from edfinder_api.state import active_jobs, active_jobs_lock, metrics

router = APIRouter(tags=['admin'])

_SCHEDULED_TRIGGER_SQL = """
LOWER(COALESCE(trigger_context, '')) LIKE 'scheduled%'
OR LOWER(COALESCE(trigger_context, '')) LIKE '%scheduler%'
OR LOWER(COALESCE(trigger_context, '')) LIKE '%cron%'
OR LOWER(COALESCE(trigger_context, '')) LIKE '%nightly%'
"""
_ADMIN_JOB_KEYS = ('cluster_rebuild', 'ratings_rebuild')
_ADMIN_OPERATION_TIMEOUT_SECONDS = 300
_ADMIN_OPERATION_SPECS: dict[str, dict[str, Any]] = {
    'telemetry_hot_log_snapshot': {
        'job_key': 'telemetry_hot_log_snapshot',
        'script_name': 'telemetry_hot_log_snapshot.py',
        'success_message': 'Telemetry hot-log snapshot completed.',
        'failure_message': 'Telemetry hot-log snapshot failed.',
    },
    'data_invariants': {
        'job_key': 'data_invariants',
        'script_name': 'data_invariants.py',
        'success_message': 'Data invariants check passed.',
        'failure_message': 'Data invariants check reported failures.',
    },
}
_ADMIN_SCRIPT_JOB_KEYS = tuple(
    str(spec['job_key'])
    for spec in _ADMIN_OPERATION_SPECS.values()
)

_TELEMETRY_JOURNAL_STAGING_SQL = """
SELECT
    COUNT(*)::int AS total_rows,
    COUNT(*) FILTER (WHERE created_at < NOW() - INTERVAL '7 days')::int AS older_than_7d,
    COUNT(*) FILTER (WHERE created_at < NOW() - INTERVAL '30 days')::int AS older_than_30d,
    COUNT(*) FILTER (WHERE created_at < NOW() - INTERVAL '90 days')::int AS older_than_90d,
    COUNT(DISTINCT system_id64)::int AS distinct_systems,
    MAX(created_at)::text AS latest_created_at,
    MIN(created_at)::text AS oldest_created_at
FROM journal_import_staging;
"""

_TELEMETRY_FRONTIER_RUNS_SQL = """
SELECT
    COUNT(*)::int AS total_runs,
    COALESCE(SUM(rows_read), 0)::int AS rows_read,
    COALESCE(SUM(rows_staged), 0)::int AS rows_staged,
    COUNT(*) FILTER (WHERE started_at < NOW() - INTERVAL '30 days')::int AS older_than_30d,
    COUNT(*) FILTER (WHERE started_at < NOW() - INTERVAL '90 days')::int AS older_than_90d,
    MAX(finished_at)::text AS latest_finished_at,
    MIN(started_at)::text AS oldest_started_at
FROM source_runs
WHERE source_name = 'frontier_journal';
"""

_TELEMETRY_OBSERVED_FACTS_SQL = """
SELECT
    COUNT(*)::int AS imported_rows,
    COUNT(*) FILTER (WHERE created_at < NOW() - INTERVAL '30 days')::int AS older_than_30d,
    COUNT(*) FILTER (WHERE created_at < NOW() - INTERVAL '90 days')::int AS older_than_90d,
    MAX(created_at)::text AS latest_created_at,
    MIN(created_at)::text AS oldest_created_at
FROM observed_facts
WHERE COALESCE(source, source_type, '') IN ('imported', 'frontier_journal');
"""

_TELEMETRY_EVIDENCE_SQL = """
SELECT
    COUNT(*)::int AS total_rows,
    COUNT(*) FILTER (WHERE record_status = 'active')::int AS active_rows,
    COUNT(*) FILTER (WHERE record_status = 'superseded')::int AS superseded_rows,
    COUNT(*) FILTER (WHERE record_status = 'quarantined')::int AS quarantined_rows,
    MAX(created_at)::text AS latest_created_at,
    MIN(created_at)::text AS oldest_created_at
FROM evidence_records
WHERE source_name IN ('frontier_journal', 'canonical_app_data');
"""

async def _create_admin_job_run(
    conn: asyncpg.Connection,
    *,
    job_key: str,
    started_at: str,
    trigger_source: str = 'admin',
    details: dict[str, Any] | None = None,
) -> int:
    return int(
        await conn.fetchval(
            """
            INSERT INTO admin_job_runs (
                job_key,
                trigger_source,
                status,
                started_at,
                details_json
            )
            VALUES ($1, $2, 'running', $3::timestamptz, $4::jsonb)
            RETURNING id
            """,
            job_key,
            trigger_source,
            started_at,
            details or {},
        )
    )


async def _finalize_admin_job_run(
    conn: asyncpg.Connection,
    *,
    job_run_id: int,
    status: str,
    finished_at: str,
    exit_code: int | None = None,
    error_text: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        """
        UPDATE admin_job_runs
        SET
            status = $2,
            finished_at = $3::timestamptz,
            exit_code = $4,
            error_text = $5,
            details_json = COALESCE(details_json, '{}'::jsonb) || $6::jsonb,
            updated_at = NOW()
        WHERE id = $1
        """,
        job_run_id,
        status,
        finished_at,
        exit_code,
        error_text,
        details or {},
    )


async def _load_latest_admin_job_runs(
    conn: asyncpg.Connection,
) -> dict[str, dict[str, Any] | None]:
    rows = await conn.fetch(
        """
        SELECT
          id,
          job_key,
          status,
          started_at::text AS started_at,
          finished_at::text AS finished_at,
          exit_code,
          error_text,
          details_json
        FROM (
          SELECT
            id,
            job_key,
            status,
            started_at,
            finished_at,
            exit_code,
            error_text,
            details_json,
            ROW_NUMBER() OVER (
              PARTITION BY job_key
              ORDER BY started_at DESC, id DESC
            ) AS recency_rank
          FROM admin_job_runs
          WHERE job_key = ANY($1::text[])
        ) ranked
        WHERE recency_rank = 1
        """,
        list(_ADMIN_JOB_KEYS),
    )
    latest: dict[str, dict[str, Any] | None] = {job_key: None for job_key in _ADMIN_JOB_KEYS}
    for row in rows:
        details = dict(row['details_json'] or {})
        latest[str(row['job_key'])] = {
            'job_run_id': int(row['id']),
            'status': str(row['status']),
            'start_time': row['started_at'],
            'end_time': row['finished_at'],
            'exit_code': row['exit_code'],
            'error': row['error_text'],
            'dirty_before': details.get('dirty_before'),
            'cleared': details.get('cleared'),
        }
    return latest


async def _load_recent_admin_operation_runs(
    conn: asyncpg.Connection,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT
          id,
          job_key,
          status,
          started_at::text AS started_at,
          finished_at::text AS finished_at,
          exit_code,
          error_text,
          details_json
        FROM admin_job_runs
        WHERE job_key = ANY($1::text[])
           OR details_json ? 'operation_key'
        ORDER BY started_at DESC, id DESC
        LIMIT $2
        """,
        list(_ADMIN_SCRIPT_JOB_KEYS),
        limit,
    )
    history: list[dict[str, Any]] = []
    for row in rows:
        details = dict(row['details_json'] or {})
        history.append({
            'job_run_id': int(row['id']),
            'job_key': str(row['job_key']),
            'operation_key': details.get('operation_key'),
            'script_name': details.get('script_name'),
            'status': str(row['status']),
            'started_at': row['started_at'],
            'finished_at': row['finished_at'],
            'exit_code': row['exit_code'],
            'error_text': row['error_text'],
            'output_text': details.get('output_text'),
        })
    return history


async def _reap_stale_admin_job_runs(conn: asyncpg.Connection) -> int:
    return int(
        await conn.fetchval(
            """
            WITH updated AS (
                UPDATE admin_job_runs
                SET
                    status = 'failed',
                    finished_at = COALESCE(finished_at, NOW()),
                    error_text = COALESCE(error_text, 'orphaned by restart or timeout'),
                    updated_at = NOW()
                WHERE status = 'running'
                  AND (
                      job_key = ANY($2::text[])
                      OR details_json ? 'operation_key'
                  )
                  AND started_at < NOW() - make_interval(secs => $1::int)
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM updated
            """,
            _ADMIN_OPERATION_TIMEOUT_SECONDS,
            list(_ADMIN_SCRIPT_JOB_KEYS),
        )
        or 0
    )


async def reap_stale_admin_operation_runs(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await _reap_stale_admin_job_runs(conn)


async def _configure_admin_operation_session(
    conn: asyncpg.Connection,
    *,
    application_name: str,
    production_safe: bool = False,
) -> None:
    await conn.execute("SELECT set_config('statement_timeout', '0', true)")
    await conn.execute("SELECT set_config('lock_timeout', '0', true)")
    await conn.execute("SELECT set_config('application_name', $1, true)", application_name)
    if production_safe:
        await conn.execute("SELECT set_config('max_parallel_workers_per_gather', '0', true)")
        await conn.execute("SELECT set_config('work_mem', '4MB', true)")
        await conn.execute("SELECT set_config('enable_hashjoin', 'off', true)")


def _truncate_admin_output(value: str, *, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + '\n...[truncated]'


def _render_admin_report(title: str, sections: list[tuple[str, list[tuple[str, Any]]]]) -> str:
    lines = [title]
    for section_name, rows in sections:
        lines.append(f'{section_name}:')
        for key, value in rows:
            lines.append(f'  {key}: {value}')
    return '\n'.join(lines)


async def _run_telemetry_hot_log_snapshot_operation(conn: asyncpg.Connection) -> tuple[bool, str]:
    await _configure_admin_operation_session(
        conn,
        application_name='admin_telemetry_hot_log_snapshot',
        production_safe=True,
    )
    journal_import_staging = dict(await conn.fetchrow(_TELEMETRY_JOURNAL_STAGING_SQL))
    frontier_runs = dict(await conn.fetchrow(_TELEMETRY_FRONTIER_RUNS_SQL))
    observed_facts = dict(await conn.fetchrow(_TELEMETRY_OBSERVED_FACTS_SQL))
    evidence_records = dict(await conn.fetchrow(_TELEMETRY_EVIDENCE_SQL))
    output = _render_admin_report(
        'ED-Finder telemetry hot-log snapshot',
        [
            ('journal_import_staging', list(journal_import_staging.items())),
            ('frontier_journal_source_runs', list(frontier_runs.items())),
            ('imported_observed_facts', list(observed_facts.items())),
            ('durable_evidence_records', list(evidence_records.items())),
        ],
    )
    return True, output


async def _run_data_invariants_operation(conn: asyncpg.Connection) -> tuple[bool, str]:
    await _configure_admin_operation_session(
        conn,
        application_name='admin_data_invariants',
        production_safe=True,
    )

    shared_counts: dict[str, int] = {}
    for key in _ADMIN_DATA_INVARIANT_CHECK_KEYS:
        check = SHARED_DATA_INVARIANT_SCALAR_CHECKS_BY_KEY[key]
        shared_counts[key] = int(await conn.fetchval(check.sql) or 0)

    colonisation_row = await conn.fetchrow(COLONISATION_STATUS_AGE_BUCKETS_SQL)
    colonisation = normalise_colonisation_status_age_buckets(dict(colonisation_row or {}))

    failed = any(
        [
            shared_counts['stored_zero_body_count'],
            shared_counts['stored_missing_body_flag'],
            shared_counts['dirty_truthful_no_bodies'],
            shared_counts['evidence_active_duplicate_subjects'],
            shared_counts['evidence_superseded_freshness_drift'],
            shared_counts['evidence_active_superseded_freshness'],
            colonisation['age_over_14d'],
            shared_counts['ring_association_status_drift'],
            shared_counts['trusted_ring_rows_without_local_body'],
            shared_counts['trusted_ring_body_name_mismatch'],
            shared_counts['duplicate_trusted_ring_rows'],
            shared_counts['confirmed_station_links_without_body'],
            shared_counts['station_link_body_system_mismatch'],
            shared_counts['station_link_station_system_mismatch'],
            shared_counts['station_link_body_name_mismatch'],
            shared_counts['confirmed_station_links_unknown_lane'],
            shared_counts['confirmed_station_links_nonexact'],
        ]
    )

    output = _render_admin_report(
        'ED-Finder data invariants',
        [
            ('summary', [
                ('query_profile', 'production-safe'),
                ('zero_body_count_drift', shared_counts['stored_zero_body_count']),
                ('missing_body_flag_rows', shared_counts['stored_missing_body_flag']),
                ('dirty_truthful_no_bodies', shared_counts['dirty_truthful_no_bodies']),
                ('evidence_active_dupes', shared_counts['evidence_active_duplicate_subjects']),
                ('evidence_superseded_drift', shared_counts['evidence_superseded_freshness_drift']),
                ('evidence_active_freshness', shared_counts['evidence_active_superseded_freshness']),
            ]),
            ('colonisation_freshness', list(colonisation.items())),
            ('ring_identity', [
                ('status_drift', shared_counts['ring_association_status_drift']),
                ('trusted_no_body', shared_counts['trusted_ring_rows_without_local_body']),
                ('trusted_name_drift', shared_counts['trusted_ring_body_name_mismatch']),
                ('duplicate_trusted', shared_counts['duplicate_trusted_ring_rows']),
            ]),
            ('station_links', [
                ('confirmed_no_body', shared_counts['confirmed_station_links_without_body']),
                ('body_system_drift', shared_counts['station_link_body_system_mismatch']),
                ('station_system_drift', shared_counts['station_link_station_system_mismatch']),
                ('body_name_drift', shared_counts['station_link_body_name_mismatch']),
                ('unknown_lane', shared_counts['confirmed_station_links_unknown_lane']),
                ('nonexact_confirmed', shared_counts['confirmed_station_links_nonexact']),
            ]),
            ('result', [('status', 'FAIL' if failed else 'PASS')]),
        ],
    )
    return (not failed), output


@router.get(
    '/api/cache/stats',
    response_model=CacheStatsResponse,
    dependencies=[Depends(require_admin)],
)
async def cache_stats(
    pool:  asyncpg.Pool              = Depends(get_pool),
    redis: Optional[aioredis.Redis]  = Depends(get_redis),
):
    stats: dict[str, Any] = {
        'cache_hits':   metrics['cache_hits'],
        'cache_misses': metrics['cache_misses'],
    }
    if redis:
        try:
            info = await redis.info('stats')
            stats['redis_hits']      = info.get('keyspace_hits', 0)
            stats['redis_misses']    = info.get('keyspace_misses', 0)
            stats['redis_memory_mb'] = round(
                int((await redis.info('memory')).get('used_memory', 0)) / 1e6, 1
            )
        except Exception:
            pass
    async with pool.acquire() as conn:
        stats['db_cache_rows'] = await conn.fetchval(
            'SELECT COUNT(*) FROM api_cache WHERE expires_at > NOW()'
        )
    return stats


@router.post('/api/cache/clear', dependencies=[Depends(require_admin)])
async def cache_clear(
    pool:  asyncpg.Pool              = Depends(get_pool),
    redis: Optional[aioredis.Redis]  = Depends(get_redis),
):
    """Flush ED Finder cache keys + expired api_cache rows. X-Admin-Token required.

    Audit fix (2026-05-08, AUDIT_REPORT.md §L9): previously this called
    `redis.flushdb()` which also wiped slowapi rate-limit keys, briefly
    disabling per-IP throttling right after a flush. Now we SCAN+DEL
    only the cache prefixes we own.
    """
    deleted = 0
    redis_cleared = redis is None
    if redis:
        try:
            # Cache key prefixes used by ED Finder routers (status, search,
            # autocomplete, system, body, galaxy, cluster, map, og).
            patterns = (
                'status:*', 'search:*', 'ac:*', 'sys:*', 'body:*',
                'galaxy:*', 'cluster:*', 'map:*', 'og:*',
                'arch:*', 'elite-news:*', 'sim:*',
            )
            for pattern in patterns:
                async for key in redis.scan_iter(match=pattern, count=500):
                    await redis.delete(key)
                    deleted += 1
            redis_cleared = True
        except Exception as exc:
            log.warning('Redis cache clear failed after deleting %d keys: %s', deleted, exc)
    async with pool.acquire() as conn:
        deleted_db = await conn.execute('DELETE FROM api_cache')
    partial = not redis_cleared
    return {
        'ok': not partial,
        'partial': partial,
        'redis_cleared': redis_cleared,
        'message': (
            f'Cache clear incomplete: Redis failed after {deleted} keys; '
            f'{deleted_db.split()[-1]} DB rows removed'
            if partial else
            f'Cache cleared ({deleted} Redis keys removed, {deleted_db.split()[-1]} DB rows removed)'
        ),
    }


class _ProblemJSONResponse(JSONResponse):
    media_type = 'application/problem+json'


@router.post(
    '/api/admin/rebuild-clusters',
    status_code=410,
    response_model=RetiredRouteProblem,
    response_class=_ProblemJSONResponse,
    response_description='Retired V2 route; the problem detail is the only authenticated outcome.',
    dependencies=[Depends(require_admin)],
)
@limiter.limit('1/minute')
async def trigger_rebuild_clusters(
    request: Request,
):
    """Retired V2 route — always answers 410 Gone.

    V2 shelled into Docker from the API container; the V3 API has no container runtime, so cluster rebuilds run only through the governed operator workflows. Admin auth is still required.
    """
    # Why 410 rather than the old background job: V2 ran
    # `docker compose run importer build_clusters.py` from inside the API
    # container. V3 never mounts a Docker socket or compose project into the
    # API, so the shell-out failed on every call and recorded a "failed"
    # admin job that looked like a transient error. Nothing is executed or
    # recorded here; the problem detail lives in helpers.CLUSTER_REBUILD_GONE_PROBLEM.
    # 410 is the route's primary (and only) status, declared on the decorator
    # so the generated clients carry no impossible 200 branch.
    return dict(CLUSTER_REBUILD_GONE_PROBLEM)


@router.post('/api/admin/rebuild-ratings', dependencies=[Depends(require_admin)])
@limiter.limit('1/minute')
async def trigger_rebuild_ratings(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Recompute ratings for systems flagged as `rating_dirty`.

    For the preview pod this runs synchronously on a tiny dataset
    (~40 systems). Production should swap in the real pipeline that
    walks bodies/stations and recomputes the per-economy scores.
    Rate-limited to 1/min and X-Admin-Token gated.
    """
    job_id = 'ratings_rebuild'
    started_at = datetime.now(timezone.utc).isoformat()
    async with pool.acquire() as conn:
        job_run_id = await _create_admin_job_run(
            conn,
            job_key=job_id,
            started_at=started_at,
            details={'requested_via': 'admin_api'},
        )
    async with active_jobs_lock:
        active_jobs[job_id] = {
            'status': 'running',
            'start_time': started_at,
            'end_time': None,
            'exit_code': None,
            'error': None,
            'job_run_id': job_run_id,
        }

    try:
        async with pool.acquire() as conn:
            # Pick everything flagged dirty (or, if nothing's dirty, everything —
            # makes the button useful even on a clean DB for forced refresh).
            dirty = await conn.fetchval(
                "SELECT COUNT(*) FROM systems WHERE rating_dirty = true"
            )
            target_clause = (
                'WHERE r.system_id64 IN (SELECT id64 FROM systems WHERE rating_dirty = true)'
                if dirty else ''
            )
            await conn.execute(
                f"""
                UPDATE ratings r SET
                  score_agriculture = LEAST(98, GREATEST(20,
                      25 + CASE WHEN s.primary_economy = 'Agriculture' THEN 55 ELSE 0 END
                         + CASE WHEN s.security = 'High' THEN 8 ELSE 0 END
                         + LEAST(15, s.body_count * 2)
                         - CASE WHEN s.primary_economy = 'Industrial' THEN 18 ELSE 0 END))::smallint,
                  score_refinery    = LEAST(98, GREATEST(20,
                      30 + CASE WHEN s.primary_economy = 'Refinery' THEN 55 ELSE 0 END
                         + LEAST(20, s.body_count * 2)
                         + CASE WHEN s.allegiance = 'Federation' THEN 6 ELSE 0 END))::smallint,
                  score_industrial  = LEAST(98, GREATEST(15,
                      28 + CASE WHEN s.primary_economy = 'Industrial' THEN 60 ELSE 0 END
                         + CASE WHEN s.population > 1000000 THEN 12 ELSE 0 END
                         - CASE WHEN s.primary_economy = 'Tourism' THEN 22 ELSE 0 END))::smallint,
                  score_hightech    = LEAST(98, GREATEST(15,
                      22 + CASE WHEN s.primary_economy = 'HighTech' THEN 65 ELSE 0 END
                         + CASE WHEN s.population > 100000000 THEN 14 ELSE 0 END
                         + CASE WHEN s.allegiance = 'Empire' THEN 8 ELSE 0 END))::smallint,
                  score_military    = LEAST(95, GREATEST(15,
                      20 + CASE WHEN s.primary_economy = 'Military'   THEN 55 ELSE 0 END
                         + CASE WHEN s.allegiance = 'Empire' THEN 16 ELSE 0 END
                         + CASE WHEN s.allegiance = 'Federation' THEN 12 ELSE 0 END
                         + CASE WHEN s.security = 'High' THEN 8 ELSE 0 END))::smallint,
                  score_tourism     = LEAST(98, GREATEST(15,
                      18 + CASE WHEN s.primary_economy = 'Tourism' THEN 60 ELSE 0 END
                         + CASE WHEN s.galaxy_region_id = 1 THEN 22 ELSE 0 END
                         - CASE WHEN s.primary_economy = 'Industrial' THEN 18 ELSE 0 END))::smallint
                FROM systems s
                WHERE r.system_id64 = s.id64
                  {('AND r.system_id64 IN (SELECT id64 FROM systems WHERE rating_dirty = true)') if dirty else ''}
                """
            )
            await conn.execute(
                f"""
                UPDATE ratings r SET score = GREATEST(
                  COALESCE(r.score_agriculture,0), COALESCE(r.score_refinery,0),
                  COALESCE(r.score_industrial,0),  COALESCE(r.score_hightech,0),
                  COALESCE(r.score_military,0),    COALESCE(r.score_tourism,0)
                )::smallint
                {target_clause}
                """
            )
            cleared = await conn.execute(
                "UPDATE systems SET rating_dirty = false WHERE rating_dirty = true"
            )
            n_cleared = int(cleared.split()[-1]) if cleared else 0
    except Exception as exc:
        async with active_jobs_lock:
            active_jobs[job_id] = {
                'status': 'failed',
                'start_time': started_at,
                'end_time': datetime.now(timezone.utc).isoformat(),
                'exit_code': None,
                'error': str(exc),
                'job_run_id': job_run_id,
            }
        async with pool.acquire() as conn:
            await _finalize_admin_job_run(
                conn,
                job_run_id=job_run_id,
                status='failed',
                finished_at=datetime.now(timezone.utc).isoformat(),
                exit_code=None,
                error_text=str(exc),
            )
        raise

    finished_at = datetime.now(timezone.utc).isoformat()
    async with active_jobs_lock:
        active_jobs[job_id] = {
            'status': 'completed',
            'start_time': started_at,
            'end_time': finished_at,
            'exit_code': 0,
            'error': None,
            'dirty_before': dirty,
            'cleared': n_cleared,
            'job_run_id': job_run_id,
        }
    async with pool.acquire() as conn:
        await _finalize_admin_job_run(
            conn,
            job_run_id=job_run_id,
            status='completed',
            finished_at=finished_at,
            exit_code=0,
            error_text=None,
            details={
                'dirty_before': dirty,
                'cleared': n_cleared,
            },
        )

    return {
        'ok':           True,
        'message':      f'Rebuilt ratings for {dirty if dirty else "all"} systems'
                        + (f', cleared {n_cleared} dirty flags' if n_cleared else ''),
        'dirty_before': dirty,
        'cleared':      n_cleared,
    }


@router.post(
    '/api/admin/operations/{operation_key}',
    dependencies=[Depends(require_admin)],
)
@limiter.limit('5/minute')
async def run_admin_operation(
    request: Request,
    operation_key: str,
    pool: asyncpg.Pool = Depends(get_pool),
    readonly_pool: asyncpg.Pool = Depends(get_readonly_pool),
):
    del request
    spec = _ADMIN_OPERATION_SPECS.get(operation_key)
    if spec is None:
        raise HTTPException(status_code=404, detail=f'Unknown admin operation: {operation_key}')

    started_at = datetime.now(timezone.utc).isoformat()
    async with pool.acquire() as conn:
        job_run_id = await _create_admin_job_run(
            conn,
            job_key=str(spec['job_key']),
            started_at=started_at,
            details={
                'requested_via': 'admin_api',
                'operation_key': operation_key,
                'script_name': str(spec['script_name']),
                'execution_mode': 'in_process_read_only',
                    'database_access': (
                        'dedicated_readonly_pool'
                        if settings.database_readonly_url
                        else 'primary_pool_readonly_transaction'
                    ),
            },
        )

    try:
        async with readonly_pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                if operation_key == 'telemetry_hot_log_snapshot':
                    ok, output_text = await _run_telemetry_hot_log_snapshot_operation(conn)
                elif operation_key == 'data_invariants':
                    ok, output_text = await _run_data_invariants_operation(conn)
                else:
                    raise HTTPException(status_code=404, detail=f'Unknown admin operation: {operation_key}')

        finished_at = datetime.now(timezone.utc).isoformat()
        output_text = _truncate_admin_output(output_text.strip() or '(no output)')
        status = 'completed' if ok else 'failed'
        message = (
            str(spec['success_message'])
            if ok
            else str(spec['failure_message'])
        )
        async with pool.acquire() as conn:
            await _finalize_admin_job_run(
                conn,
                job_run_id=job_run_id,
                status=status,
                finished_at=finished_at,
                exit_code=0 if ok else 1,
                error_text=None if ok else message,
                details={
                    'operation_key': operation_key,
                    'output_text': output_text,
                },
            )
        return {
            'ok': ok,
            'message': message,
            'operation_key': operation_key,
            'job_run_id': job_run_id,
            'status': status,
            'exit_code': 0 if ok else 1,
            'output_text': output_text,
        }
    except Exception as exc:
        finished_at = datetime.now(timezone.utc).isoformat()
        output_text = _truncate_admin_output(str(exc).strip() or '(operation failed without output)')
        async with pool.acquire() as conn:
            await _finalize_admin_job_run(
                conn,
                job_run_id=job_run_id,
                status='failed',
                finished_at=finished_at,
                exit_code=1,
                error_text=str(exc),
                details={
                    'operation_key': operation_key,
                    'output_text': output_text,
                },
            )
        return {
            'ok': False,
            'message': f'{spec["failure_message"]} {exc}',
            'operation_key': operation_key,
            'job_run_id': job_run_id,
            'status': 'failed',
            'exit_code': 1,
            'output_text': output_text,
        }


@router.get(
    '/api/admin/operations/history',
    dependencies=[Depends(require_admin)],
)
async def admin_operation_history(
    pool: asyncpg.Pool = Depends(get_pool),
    readonly_pool: asyncpg.Pool = Depends(get_readonly_pool),
    limit: int = Query(6, ge=1, le=20),
):
    await reap_stale_admin_operation_runs(pool)
    async with readonly_pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            operations = await _load_recent_admin_operation_runs(conn, limit=limit)
    return {
        'schema_version': 'admin_operation_history/v1',
        'read_only': True,
        'operations': operations,
    }


@router.get(
    '/api/admin/cron-status',
    dependencies=[Depends(require_admin)],
)
async def admin_cron_status(
    pool: asyncpg.Pool = Depends(get_pool),
    readonly_pool: asyncpg.Pool = Depends(get_readonly_pool),
):
    """Return a read-only summary of recent cron/scheduler-like activity."""
    await reap_stale_admin_operation_runs(pool)
    async with readonly_pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            nightly_update = await conn.fetchval(
                "SELECT value FROM meta WHERE key = 'last_nightly_update'"
            ) or 'never'

            scheduled_summary = dict(await conn.fetchrow(
                f"""
                SELECT
                  COUNT(*) FILTER (WHERE started_at >= NOW() - INTERVAL '24 hours')::int AS runs_last_24h,
                  COUNT(*) FILTER (
                    WHERE started_at >= NOW() - INTERVAL '24 hours'
                      AND LOWER(COALESCE(status, '')) IN ('failed', 'error')
                  )::int AS failed_runs_last_24h,
                  MAX(started_at)::text AS latest_started_at,
                  MAX(finished_at)::text AS latest_finished_at
                FROM source_runs
                WHERE {_SCHEDULED_TRIGGER_SQL}
                """
            ))

            scheduled_recent_sources = [
                dict(row)
                for row in await conn.fetch(
                    f"""
                    SELECT
                      source_name,
                      domain,
                      trigger_context,
                      status,
                      started_at,
                      finished_at,
                      rows_read,
                      rows_staged
                    FROM (
                      SELECT
                        source_name,
                        domain,
                        trigger_context,
                        status,
                        started_at,
                        finished_at,
                        rows_read,
                        rows_staged,
                        ROW_NUMBER() OVER (
                          PARTITION BY source_name, COALESCE(domain, '')
                          ORDER BY started_at DESC
                        ) AS recency_rank
                      FROM source_runs
                      WHERE {_SCHEDULED_TRIGGER_SQL}
                    ) ranked
                    WHERE recency_rank = 1
                    ORDER BY started_at DESC NULLS LAST
                    LIMIT 8
                    """
                )
            ]

            ratings_backlog = dict(await conn.fetchrow(
                """
                SELECT
                  COUNT(*)::int AS dirty_systems,
                  MIN(updated_at)::text AS oldest_dirty_updated_at,
                  MAX(updated_at)::text AS newest_dirty_updated_at
                FROM systems
                WHERE rating_dirty = true
                """
            ))
            jobs = await _load_latest_admin_job_runs(conn)

    return {
        'schema_version': 'admin_cron_status/v1',
        'read_only': True,
        'last_nightly_update': nightly_update,
        'scheduled_source_runs': {
            'runs_last_24h': int(scheduled_summary.get('runs_last_24h') or 0),
            'failed_runs_last_24h': int(scheduled_summary.get('failed_runs_last_24h') or 0),
            'latest_started_at': scheduled_summary.get('latest_started_at'),
            'latest_finished_at': scheduled_summary.get('latest_finished_at'),
            'recent_sources': scheduled_recent_sources,
        },
        'ratings_backlog': {
            'dirty_systems': int(ratings_backlog.get('dirty_systems') or 0),
            'oldest_dirty_updated_at': ratings_backlog.get('oldest_dirty_updated_at'),
            'newest_dirty_updated_at': ratings_backlog.get('newest_dirty_updated_at'),
        },
        'jobs': jobs,
    }


@router.get(
    '/api/admin/enrichment/station-status',
    dependencies=[Depends(require_admin)],
)
async def station_enrichment_operator_status():
    """Return a sanitized read-only station enrichment status snapshot.

    This endpoint deliberately reads only a configured JSON artifact produced
    by `station_enrichment_status.py --json`. It never invokes the enrichment
    script, Docker, external data providers, or the database.
    """
    return read_enrichment_status_snapshot(settings.enrichment_status_json_path)


@router.get(
    '/api/admin/enrichment/warehouse-status',
    dependencies=[Depends(require_admin)],
)
async def warehouse_enrichment_operator_status():
    """Return a sanitized read-only warehouse reconciliation/status snapshot.

    This endpoint reads only a configured JSON artifact. It never runs
    warehouse importer scripts, Docker, live APIs, or database queries.
    """
    return read_warehouse_status_snapshot(settings.enrichment_warehouse_status_json_path)

@router.get(
    '/api/admin/data-status',
    dependencies=[Depends(require_admin)],
)
async def admin_data_status(pool: asyncpg.Pool = Depends(get_pool)):
    """Return a read-only admin snapshot of core data status.

    This endpoint is intentionally status-only. It runs inside a read-only
    database transaction and does not perform imports, migrations, station-type
    writes, canonical writes, or canonical apply.
    """
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            transaction_read_only = await conn.fetchval('SHOW transaction_read_only')

            station_counts = dict(await conn.fetchrow(
                """
                SELECT
                  COUNT(*)::int AS total_station_rows,
                  COUNT(*) FILTER (WHERE station_type::text = 'Unknown')::int AS unknown_station_rows,
                  COUNT(*) FILTER (WHERE station_type::text = 'Coriolis')::int AS coriolis_station_rows,
                  COUNT(*) FILTER (WHERE station_type::text = 'Dodec')::int AS dodec_station_rows,
                  COUNT(*) FILTER (WHERE station_type_source IS NOT NULL)::int AS rows_with_station_type_source
                FROM stations
                """
            ))

            station_type_counts = [
                dict(row)
                for row in await conn.fetch(
                    """
                    SELECT
                      station_type::text AS station_type,
                      COUNT(*)::int AS rows
                    FROM stations
                    GROUP BY station_type
                    ORDER BY station_type
                    """
                )
            ]

            station_type_source_counts = [
                dict(row)
                for row in await conn.fetch(
                    """
                    SELECT
                      COALESCE(station_type_source, 'NULL') AS station_type_source,
                      station_type::text AS station_type,
                      COUNT(*)::int AS rows
                    FROM stations
                    GROUP BY station_type_source, station_type
                    ORDER BY station_type_source, station_type
                    """
                )
            ]

            identity_counts = dict(await conn.fetchrow(
                """
                SELECT
                  COUNT(*)::int AS total_identity_rows,
                  COUNT(*) FILTER (WHERE identity_status = 'confirmed')::int AS confirmed_identity_rows,
                  COUNT(*) FILTER (WHERE conflict_reason IS NOT NULL)::int AS rows_with_conflict_reason,
                  COUNT(*) FILTER (WHERE edsm_station_id IS NOT NULL)::int AS rows_with_edsm_station_id,
                  COUNT(*) FILTER (WHERE market_id IS NOT NULL)::int AS rows_with_market_id
                FROM station_external_identity
                """
            ))

            identity_source_status_counts = [
                dict(row)
                for row in await conn.fetch(
                    """
                    SELECT
                      source,
                      identity_status,
                      COUNT(*)::int AS rows
                    FROM station_external_identity
                    GROUP BY source, identity_status
                    ORDER BY source, identity_status
                    """
                )
            ]

            unknown_station_source_counts = [
                dict(row)
                for row in await conn.fetch(
                    """
                    SELECT
                      stg.station_type::text AS source_station_type,
                      COUNT(*)::int AS rows
                    FROM station_external_identity sei
                    JOIN stations s
                      ON s.id = sei.canonical_station_id
                    JOIN public.staging_edsm_stations stg
                      ON stg.edsm_station_id = sei.edsm_station_id
                    WHERE sei.identity_status = 'confirmed'
                      AND s.station_type = 'Unknown'
                    GROUP BY stg.station_type
                    ORDER BY rows DESC, source_station_type NULLS FIRST
                    """
                )
            ]

            recent_station_type_updates = [
                dict(row)
                for row in await conn.fetch(
                    """
                    SELECT
                      id AS canonical_station_id,
                      name AS canonical_station_name,
                      system_id64,
                      station_type::text AS station_type,
                      station_type_source,
                      station_type_updated_at
                    FROM stations
                    WHERE station_type_source IS NOT NULL
                    ORDER BY station_type_updated_at DESC NULLS LAST, id
                    LIMIT 20
                    """
                )
            ]

    return {
        'schema_version': 'admin_data_status/v1',
        'read_only': True,
        'transaction_read_only': transaction_read_only,
        'station_counts': station_counts,
        'station_type_counts': station_type_counts,
        'station_type_source_counts': station_type_source_counts,
        'identity_counts': identity_counts,
        'identity_source_status_counts': identity_source_status_counts,
        'unknown_station_source_counts': unknown_station_source_counts,
        'recent_station_type_updates': recent_station_type_updates,
        'policy_summary': {
            'dodec_supported': station_counts.get('dodec_station_rows', 0) > 0,
            'fleet_carriers_remain_unknown': any(
                row.get('source_station_type') == 'Drake-Class Carrier'
                for row in unknown_station_source_counts
            ),
            'construction_depots_remain_unknown': any(
                row.get('source_station_type') == 'Space Construction Depot'
                for row in unknown_station_source_counts
            ),
        },
        'safety_summary': {
            'db_read_only_confirmed': transaction_read_only == 'on',
            'db_writes_performed': False,
            'migrations_performed': False,
            'station_type_writes_performed': False,
            'canonical_apply_performed': False,
        },
    }
