from __future__ import annotations

import base64
import binascii
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Sequence

import asyncpg

from edfinder_api.exploration.api_models import (
    ExplorationFactRow,
    ExplorationFactsResponse,
    ExplorationBodySummary,
    ExplorationCodexByRegionResponse,
    ExplorationCodexRegion,
    ExplorationCodexSummary,
    ExplorationImportReceipt,
    ExplorationImportRequest,
    ExplorationImportSummary,
    ExplorationOrganicSummary,
    ExplorationSystemSummaryResponse,
    ExplorationTrailPoint,
    ExplorationTrailResponse,
    ExplorationViewportVisit,
    ExplorationViewportVisitsResponse,
    ExplorationVisitSummary,
)

MAX_DAILY_ROWS_PER_SYNC_KEY = 50_000
DEFAULT_FACTS_LIMIT = 5_000
MAX_FACTS_LIMIT = 5_000
DEFAULT_TRAIL_LIMIT = 250
MAX_TRAIL_LIMIT = 1_000
MAX_VIEWPORT_VISITS = 40_000


class ExplorationImportRateLimitError(RuntimeError):
    """Raised when the bounded exploration-import daily row budget is exceeded."""


def _json_object(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {}
        decoded = json.loads(stripped)
        return dict(decoded) if isinstance(decoded, dict) else {}
    return dict(value)


def _dt_to_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _encode_fact_cursor(observed_at: object, fact_id: int) -> str:
    raw = json.dumps([_dt_to_str(observed_at), fact_id], separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def decode_fact_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padding = '=' * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(cursor + padding))
        observed_at = datetime.fromisoformat(str(value[0]).replace('Z', '+00:00'))
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        return observed_at.astimezone(timezone.utc), int(value[1])
    except (ValueError, TypeError, IndexError, json.JSONDecodeError, binascii.Error) as exc:
        raise ValueError('cursor is not a valid exploration facts cursor') from exc


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _daily_rows_for_sync_key(conn: asyncpg.Connection, sync_key: str) -> int:
    count = await conn.fetchval(
        '''
        SELECT COUNT(*)
        FROM exploration_facts
        WHERE sync_key = $1
          AND created_at >= (NOW() - INTERVAL '1 day')
        ''',
        sync_key,
    )
    return int(count or 0)


async def import_exploration_batch(
    pool: asyncpg.Pool,
    request: ExplorationImportRequest,
) -> ExplorationImportReceipt:
    event_counts: Counter[str] = Counter(observation.event_type for observation in request.observations)
    rows_read = len(request.observations)
    rows_staged = 0
    rows_skipped = 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Serialize imports for the same anonymous owner. Without this, two
            # concurrent transactions can interleave their delete/rebuild pass
            # and leave otherwise valid facts missing from the projections.
            await conn.execute(
                'SELECT pg_advisory_xact_lock(hashtextextended($1, 0))',
                request.sync_key,
            )
            daily_rows_before = await _daily_rows_for_sync_key(conn, request.sync_key)

            incoming_keys = [observation.observation_key for observation in request.observations]
            existing_rows = await conn.fetch(
                '''
                SELECT source_record_hash
                FROM exploration_facts
                WHERE sync_key = $1
                  AND source_record_hash = ANY($2::text[])
                ''',
                request.sync_key,
                incoming_keys,
            )
            existing_key_set = {row['source_record_hash'] for row in existing_rows}
            novel_key_set = set(incoming_keys) - existing_key_set
            novel_count = len(novel_key_set)

            if daily_rows_before + novel_count > MAX_DAILY_ROWS_PER_SYNC_KEY:
                raise ExplorationImportRateLimitError(
                    f'Exploration import row budget exceeded for this sync key: '
                    f'{daily_rows_before + novel_count:,} rows in the last 24h '
                    f'(limit {MAX_DAILY_ROWS_PER_SYNC_KEY:,}).'
                )

            # A single INSERT ... SELECT keeps 10K-20K event imports bounded to
            # one database round trip.  De-duplicate the request first because
            # PostgreSQL cannot resolve the same ON CONFLICT target twice in a
            # single statement.
            unique_observations = {}
            for observation in request.observations:
                unique_observations.setdefault(observation.observation_key, observation)
            serialized_rows = json.dumps([
                {
                    'source_record_hash': observation.observation_key,
                    'event_type': observation.event_type,
                    'system_id64': observation.system_id64,
                    'system_name': observation.system_name,
                    'body_id': observation.body_id,
                    'body_name': observation.body_name,
                    'observed_at': observation.observed_at.isoformat(),
                    'payload_json': observation.payload,
                }
                for observation in unique_observations.values()
            ], allow_nan=False) or '[]'
            inserted_rows = await conn.fetch(
                '''
                INSERT INTO exploration_facts (
                    sync_key, source, source_record_hash, event_type,
                    system_id64, system_name, body_id, body_name,
                    observed_at, payload_json
                )
                SELECT $1, $2, row.source_record_hash, row.event_type,
                       row.system_id64, row.system_name, row.body_id, row.body_name,
                       row.observed_at, row.payload_json
                FROM jsonb_to_recordset(COALESCE($3::text::jsonb, '[]'::jsonb)) AS row(
                    source_record_hash text, event_type text, system_id64 bigint,
                    system_name text, body_id integer, body_name text,
                    observed_at timestamptz, payload_json jsonb
                )
                ON CONFLICT (sync_key, source_record_hash) DO NOTHING
                RETURNING source_record_hash
                ''',
                request.sync_key,
                request.source,
                serialized_rows,
            )
            rows_staged = len(inserted_rows)
            rows_skipped = rows_read - rows_staged

            projection_row = await conn.fetchrow(
                'SELECT * FROM rebuild_exploration_projections($1)',
                request.sync_key,
            )
            projection_counts = {
                key: int(projection_row[key] or 0)
                for key in ('visits', 'bodies', 'organisms', 'sales', 'codex', 'route_legs')
            } if projection_row is not None else {}

    return ExplorationImportReceipt(
        sync_key=request.sync_key,
        status='succeeded',
        summary=ExplorationImportSummary(
            observations_received=rows_read,
            observations_staged=rows_staged,
            duplicates_skipped=rows_skipped,
            event_counts=dict(event_counts),
            projections_rebuilt=projection_counts,
        ),
    )


async def get_exploration_facts(
    pool: asyncpg.Pool,
    sync_key: str,
    *,
    limit: int = DEFAULT_FACTS_LIMIT,
    cursor: str | None = None,
    event_types: Sequence[str] | None = None,
    system_id64: int | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
) -> ExplorationFactsResponse:
    if limit < 1 or limit > MAX_FACTS_LIMIT:
        raise ValueError(f'limit must be between 1 and {MAX_FACTS_LIMIT}')
    params: list[object] = [sync_key]
    filters = ['sync_key = $1']

    def add_filter(sql: str, value: object) -> None:
        params.append(value)
        filters.append(sql.format(index=len(params)))

    if event_types:
        add_filter('event_type = ANY(${index}::text[])', list(event_types))
    if system_id64 is not None:
        add_filter('system_id64 = ${index}', system_id64)
    if from_at is not None:
        add_filter('observed_at >= ${index}', _utc_datetime(from_at))
    if to_at is not None:
        add_filter('observed_at <= ${index}', _utc_datetime(to_at))
    aggregate_where = ' AND '.join(filters)
    if cursor:
        cursor_at, cursor_id = decode_fact_cursor(cursor)
        params.extend([cursor_at, cursor_id])
        filters.append(f'(observed_at, id) < (${len(params) - 1}, ${len(params)})')

    params.append(limit + 1)
    page_limit_param = len(params)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f'''
            SELECT id, event_type, system_id64, system_name, body_id, body_name, observed_at,
                   payload_json, source
            FROM exploration_facts
            WHERE {' AND '.join(filters)}
            ORDER BY observed_at DESC, id DESC
            LIMIT ${page_limit_param}
            ''',
            *params,
        )
        aggregates = await conn.fetchrow(
            f'''
            SELECT COALESCE(SUM(n), 0)::bigint AS total_count,
                   COALESCE(jsonb_object_agg(event_type, n), '{{}}'::jsonb) AS event_counts
            FROM (
                SELECT event_type, COUNT(*)::bigint AS n
                FROM exploration_facts
                WHERE {aggregate_where}
                GROUP BY event_type
            ) grouped
            ''',
            *params[:len(params) - (3 if cursor else 1)],
        )

    truncated = len(rows) > limit
    rows = rows[:limit]

    facts = [
        ExplorationFactRow(
            fact_id=int(row['id']),
            event_type=str(row['event_type']),
            system_id64=int(row['system_id64']),
            system_name=row['system_name'],
            body_id=row['body_id'],
            body_name=row['body_name'],
            observed_at=_dt_to_str(row['observed_at']) or '',
            payload=_json_object(row['payload_json']),
            source=str(row['source']),
        )
        for row in rows
    ]
    return ExplorationFactsResponse(
        sync_key=sync_key,
        facts=facts,
        count=len(facts),
        truncated=truncated,
        next_cursor=(
            _encode_fact_cursor(rows[-1]['observed_at'], int(rows[-1]['id']))
            if truncated and rows else None
        ),
        total_count=int(aggregates['total_count'] or 0),
        event_counts={key: int(value) for key, value in _json_object(aggregates['event_counts']).items()},
    )


async def get_exploration_trail(
    pool: asyncpg.Pool,
    sync_key: str,
    *,
    limit: int = DEFAULT_TRAIL_LIMIT,
    cursor: int | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
) -> ExplorationTrailResponse:
    params: list[object] = [sync_key]
    filters = ['r.sync_key = $1']
    for clause, value in (
        ('r.sequence_no < ${index}', cursor),
        ('r.arrived_at >= ${index}', _utc_datetime(from_at)),
        ('r.arrived_at <= ${index}', _utc_datetime(to_at)),
    ):
        if value is not None:
            params.append(value)
            filters.append(clause.format(index=len(params)))
    params.append(limit + 1)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f'''
            SELECT r.sequence_no, r.visit_fact_id, r.to_system_id64, r.to_system_name,
                   r.arrived_at, r.to_x, r.to_y, r.to_z, v.galaxy_region_id,
                   r.from_system_id64, r.distance_ly
            FROM exploration_expedition_routes r
            JOIN exploration_visits v ON v.fact_id = r.visit_fact_id
            WHERE {' AND '.join(filters)}
            ORDER BY r.sequence_no DESC
            LIMIT ${len(params)}
            ''',
            *params,
        )
    truncated = len(rows) > limit
    rows = rows[:limit]
    points = [
        ExplorationTrailPoint(
            sequence=int(row['sequence_no']), fact_id=int(row['visit_fact_id']),
            system_id64=int(row['to_system_id64']), system_name=row['to_system_name'],
            visited_at=_dt_to_str(row['arrived_at']) or '', x=row['to_x'], y=row['to_y'], z=row['to_z'],
            galaxy_region_id=row['galaxy_region_id'], from_system_id64=row['from_system_id64'],
            distance_ly=row['distance_ly'],
        )
        for row in rows
    ]
    return ExplorationTrailResponse(
        sync_key=sync_key, points=points, count=len(points), truncated=truncated,
        next_cursor=int(rows[-1]['sequence_no']) if truncated and rows else None,
    )


async def get_viewport_visits(
    pool: asyncpg.Pool,
    sync_key: str,
    *,
    min_x: float,
    max_x: float,
    min_y: float,
    max_y: float,
    min_z: float,
    max_z: float,
    zoom: float,
    limit: int,
) -> ExplorationViewportVisitsResponse:
    lo_x, hi_x = sorted((min_x, max_x))
    lo_y, hi_y = sorted((min_y, max_y))
    lo_z, hi_z = sorted((min_z, max_z))
    marker_mode = zoom <= 8
    cell_size = None if marker_mode else float(min(5_000, max(50, round(zoom * 8 / 50) * 50)))
    completion_cte = '''
        WITH completion AS (
            SELECT sync_key, system_id64,
                   COUNT(*) > 0
                   AND BOOL_AND(fss_state = 'scanned')
                   AND BOOL_AND(dss_state = 'mapped') AS complete
            FROM exploration_body_completeness
            WHERE sync_key = $1
            GROUP BY sync_key, system_id64
        )
    '''
    async with pool.acquire() as conn:
        if marker_mode:
            rows = await conn.fetch(
                completion_cte + '''
                SELECT 'marker'::text AS kind, v.system_id64, MAX(v.system_name) AS system_name,
                       AVG(v.x)::real AS x, AVG(v.y)::real AS y, AVG(v.z)::real AS z,
                       MAX(v.galaxy_region_id) AS galaxy_region_id, COUNT(*)::bigint AS visit_count,
                       MIN(v.visited_at) AS first_visited_at, MAX(v.visited_at) AS last_visited_at,
                       COALESCE(BOOL_OR(c.complete), FALSE) AS complete
                FROM exploration_visits v
                LEFT JOIN completion c ON c.sync_key = v.sync_key AND c.system_id64 = v.system_id64
                WHERE v.sync_key = $1 AND v.x BETWEEN $2 AND $3 AND v.y BETWEEN $4 AND $5
                  AND v.z BETWEEN $6 AND $7
                GROUP BY v.system_id64
                ORDER BY last_visited_at DESC
                LIMIT $8
                ''',
                sync_key, lo_x, hi_x, lo_y, hi_y, lo_z, hi_z, limit + 1,
            )
        else:
            rows = await conn.fetch(
                completion_cte + '''
                SELECT 'density'::text AS kind, NULL::bigint AS system_id64,
                       NULL::text AS system_name,
                       (FLOOR(v.x / $8) * $8 + $8 / 2)::real AS x,
                       (FLOOR(v.y / $8) * $8 + $8 / 2)::real AS y,
                       (FLOOR(v.z / $8) * $8 + $8 / 2)::real AS z,
                       NULL::smallint AS galaxy_region_id, COUNT(*)::bigint AS visit_count,
                       MIN(v.visited_at) AS first_visited_at, MAX(v.visited_at) AS last_visited_at,
                       BOOL_AND(COALESCE(c.complete, FALSE)) AS complete
                FROM exploration_visits v
                LEFT JOIN completion c ON c.sync_key = v.sync_key AND c.system_id64 = v.system_id64
                WHERE v.sync_key = $1 AND v.x BETWEEN $2 AND $3 AND v.y BETWEEN $4 AND $5
                  AND v.z BETWEEN $6 AND $7
                GROUP BY FLOOR(v.x / $8), FLOOR(v.y / $8), FLOOR(v.z / $8)
                ORDER BY visit_count DESC
                LIMIT $9
                ''',
                sync_key, lo_x, hi_x, lo_y, hi_y, lo_z, hi_z, cell_size, limit + 1,
            )
    truncated = len(rows) > limit
    rows = rows[:limit]
    visits = [
        ExplorationViewportVisit(
            kind=row['kind'], system_id64=row['system_id64'], system_name=row['system_name'],
            x=float(row['x']), y=float(row['y']), z=float(row['z']),
            galaxy_region_id=row['galaxy_region_id'], visit_count=int(row['visit_count']),
            first_visited_at=_dt_to_str(row['first_visited_at']) or '',
            last_visited_at=_dt_to_str(row['last_visited_at']) or '',
            completion_state='complete' if row['complete'] else 'partial',
            cell_size=cell_size,
        )
        for row in rows
    ]
    return ExplorationViewportVisitsResponse(
        sync_key=sync_key, mode='markers' if marker_mode else 'density', visits=visits,
        count=len(visits), truncated=truncated, cell_size=cell_size,
    )


async def get_exploration_summary(
    pool: asyncpg.Pool,
    sync_key: str,
    system_id64: int,
) -> ExplorationSystemSummaryResponse:
    async with pool.acquire() as conn:
        identity = await conn.fetchrow(
            '''
            SELECT COALESCE(MAX(v.system_name), MAX(s.name)) AS system_name,
                   MAX(COALESCE(v.galaxy_region_id, s.galaxy_region_id)) AS galaxy_region_id,
                   COUNT(v.fact_id)::bigint AS visit_count, MIN(v.visited_at) AS first_visited_at,
                   MAX(v.visited_at) AS last_visited_at
            FROM (SELECT $2::bigint AS id64) wanted
            LEFT JOIN systems s ON s.id64 = wanted.id64
            LEFT JOIN exploration_visits v ON v.sync_key = $1 AND v.system_id64 = wanted.id64
            ''',
            sync_key, system_id64,
        )
        body = await conn.fetchrow(
            '''
            WITH expected AS (
                SELECT MAX(COALESCE(
                    exploration_jsonb_bigint(payload_json, 'BodyCount'),
                    exploration_jsonb_bigint(payload_json, 'Count')
                ))::integer AS n,
                BOOL_OR(event_type = 'FSSAllBodiesFound') AS all_found
                FROM exploration_facts
                WHERE sync_key = $1 AND system_id64 = $2
                  AND event_type IN ('FSSDiscoveryScan', 'FSSAllBodiesFound')
            )
            SELECT e.n AS expected, COUNT(b.body_key)::integer AS observed,
                   COUNT(*) FILTER (WHERE b.fss_state = 'scanned')::integer AS scanned,
                   COUNT(*) FILTER (WHERE b.dss_state = 'mapped')::integer AS mapped,
                   COALESCE(e.all_found OR (e.n > 0 AND COUNT(*) FILTER (WHERE b.fss_state = 'scanned') >= e.n), FALSE) AS fss_complete,
                   COALESCE(COUNT(b.body_key) > 0 AND BOOL_AND(b.dss_state = 'mapped'), FALSE) AS dss_complete,
                   COALESCE(AVG(b.map_progress), 0)::double precision AS map_progress
            FROM expected e
            LEFT JOIN exploration_body_completeness b
              ON b.sync_key = $1 AND b.system_id64 = $2
            GROUP BY e.n, e.all_found
            ''',
            sync_key, system_id64,
        )
        organic = await conn.fetchrow(
            '''
            SELECT COUNT(*)::integer AS organisms,
                   COUNT(*) FILTER (WHERE sample_stage >= 1)::integer AS logged,
                   COUNT(*) FILTER (WHERE sample_stage >= 2)::integer AS sampled,
                   COUNT(*) FILTER (WHERE analysed)::integer AS analysed,
                   COUNT(*) FILTER (WHERE sold)::integer AS sold,
                   COALESCE((
                       SELECT SUM(s.total_value) FROM exobiology_sales s
                       WHERE s.sync_key = $1 AND EXISTS (
                           SELECT 1 FROM exobiology_organisms o
                           WHERE o.sync_key = $1 AND o.system_id64 = $2
                             AND (o.genus = s.genus OR (s.species IS NOT NULL AND o.species = s.species))
                       )
                   ), 0)::bigint AS sale_value
            FROM exobiology_organisms
            WHERE sync_key = $1 AND system_id64 = $2
            ''',
            sync_key, system_id64,
        )
        codex = await conn.fetchrow(
            '''
            SELECT COUNT(*)::integer AS observed,
                   COUNT(*) FILTER (WHERE sold_status = 'pending')::integer AS pending,
                   COUNT(*) FILTER (WHERE sold_status = 'sold')::integer AS sold
            FROM codex_observations WHERE sync_key = $1 AND system_id64 = $2
            ''',
            sync_key, system_id64,
        )
    return ExplorationSystemSummaryResponse(
        sync_key=sync_key, system_id64=system_id64, system_name=identity['system_name'],
        galaxy_region_id=identity['galaxy_region_id'],
        visits=ExplorationVisitSummary(
            visit_count=int(identity['visit_count'] or 0),
            first_visited_at=_dt_to_str(identity['first_visited_at']),
            last_visited_at=_dt_to_str(identity['last_visited_at']),
        ),
        bodies=ExplorationBodySummary(**dict(body)),
        organics=ExplorationOrganicSummary(**dict(organic)),
        codex=ExplorationCodexSummary(**dict(codex)),
    )


async def get_codex_by_region(
    pool: asyncpg.Pool,
    sync_key: str,
) -> ExplorationCodexByRegionResponse:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            '''
            WITH global_counts AS (
                SELECT COALESCE(region_name, 'Unknown') AS region,
                       COUNT(DISTINCT COALESCE(entry_id::text, entry_name))::integer AS global_entries
                FROM staging_codex_entries
                GROUP BY COALESCE(region_name, 'Unknown')
            ), personal AS (
                SELECT region, MAX(region_id) AS region_id,
                       COUNT(DISTINCT entry_id)::integer AS personal_entries,
                       COUNT(DISTINCT entry_id) FILTER (WHERE sold_status = 'sold')::integer AS sold_entries,
                       COALESCE(jsonb_object_agg(category, category_count), '{}'::jsonb) AS categories
                FROM (
                    SELECT region, region_id, entry_id, sold_status,
                           COALESCE(category, 'Uncategorised') AS category,
                           COUNT(*) OVER (PARTITION BY region, COALESCE(category, 'Uncategorised'))::integer AS category_count
                    FROM codex_observations WHERE sync_key = $1
                ) observations
                GROUP BY region
            )
            SELECT COALESCE(g.region, p.region) AS region, p.region_id,
                   COALESCE(g.global_entries, 0) AS global_entries,
                   COALESCE(p.personal_entries, 0) AS personal_entries,
                   COALESCE(p.sold_entries, 0) AS sold_entries,
                   p.categories
            FROM global_counts g FULL OUTER JOIN personal p ON p.region = g.region
            ORDER BY COALESCE(g.region, p.region)
            ''',
            sync_key,
        )
    regions = [
        ExplorationCodexRegion(
            region=row['region'], region_id=row['region_id'],
            global_entries=int(row['global_entries']), personal_entries=int(row['personal_entries']),
            sold_entries=int(row['sold_entries']),
            completion_percent=(
                round(int(row['personal_entries']) / int(row['global_entries']) * 100, 2)
                if int(row['global_entries']) > 0 else None
            ),
            categories={key: int(value) for key, value in _json_object(row['categories']).items()},
        ) for row in rows
    ]
    global_entries = sum(region.global_entries for region in regions)
    personal_entries = sum(region.personal_entries for region in regions)
    return ExplorationCodexByRegionResponse(
        sync_key=sync_key, regions=regions, global_entries=global_entries,
        personal_entries=personal_entries,
        completion_percent=(round(personal_entries / global_entries * 100, 2) if global_entries else None),
    )
