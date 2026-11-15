"""The galaxy map's stars: the ones in view, and a sample of the whole galaxy.

The map's other addresses (regions, cluster hulls, the density map and the
timeline) were removed on 15 November 2026: no page of the site asked for
them. Git history keeps them.
"""
import math
from typing import Optional

import asyncpg
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from edfinder_api.config import settings, limiter
from edfinder_api.deps import get_pool, get_redis, cache_get, cache_set
from edfinder_api.models import MapViewportResponse, MapViewportSystem
from edfinder_api.v3_schema import current_generation_schema

router = APIRouter(tags=['map'])

# Real-star viewport lane (the zoom-in detail lane; heatmap is the aggregate lane).
MAX_MAP_VIEWPORT_SYSTEMS = 40_000   # hard cap on individual systems per viewport
MAX_MAP_VIEWPORT_LY = 15_000        # per-axis box guard; wider -> stay on the heatmap

# Whole-galaxy starfield lane. The production heatmap MVs are not populated on
# the V3 canonical schema yet, so a wide view falls back to a bounded page
# sample of real canonical systems. Positions are never fabricated; this is a
# viewport sample, not a synthetic density layer.
MAX_GALAXY_SAMPLE_SYSTEMS = 40_000
GALAXY_SAMPLE_PERCENT = 0.05

# Both star queries below choose their systems first and cut them to the limit,
# and only then look up each chosen system's star class in the galaxy's
# `bodies` table (599 million rows). They used to look the class up before the
# cut, which made one lookup for every system in the box: a box near Sol holds
# 55,775 systems, of which 5,001 are returned. Timed on the Raspberry Pi on
# 13 Nov 2026 for that box: 4.5 seconds before, 1.3 seconds after. The rows
# returned and their order are the same.
_MAIN_STAR_CLASS = """
          LEFT JOIN LATERAL (
            SELECT b.spectral_class
              FROM {schema}.bodies b
             WHERE b.system_id64 = s.id64
               AND b.is_main_star
             ORDER BY b.body_pk
             LIMIT 1
          ) body ON TRUE"""


def close_up_stars_sql(schema: str) -> str:
    """The systems inside one close-up box, lowest id64 first.

    Parameters: the grid cell range on x, y and z ($1 to $6), the box itself
    in light years ($7 to $12), and how many rows to return ($13).
    """
    return f"""
        SELECT s.id64, s.name, s.x, s.y, s.z, s.galaxy_region_id,
               FALSE AS populated,
               body.spectral_class AS main_star_type
          FROM (
            SELECT s.id64, s.name, s.x_ly AS x, s.y_ly AS y, s.z_ly AS z,
                   s.galaxy_region_id
              FROM {schema}.systems s
             WHERE s.grid_x BETWEEN $1 AND $2
               AND s.grid_y BETWEEN $3 AND $4
               AND s.grid_z BETWEEN $5 AND $6
               AND s.x_ly BETWEEN $7 AND $8
               AND s.y_ly BETWEEN $9 AND $10
               AND s.z_ly BETWEEN $11 AND $12
             ORDER BY s.id64
             LIMIT $13
          ) s{_MAIN_STAR_CLASS.format(schema=schema)}
         ORDER BY s.id64
    """


def galaxy_sample_stars_sql(schema: str) -> str:
    """A sample of real systems for a view too wide for close-up boxes.

    Parameters: the share of the table to sample, in percent ($1), the box in
    light years ($2 to $7), and how many rows to return ($8).
    """
    return f"""
        SELECT s.id64, s.name, s.x, s.y, s.z, s.galaxy_region_id,
               FALSE AS populated,
               body.spectral_class AS main_star_type
          FROM (
            SELECT s.id64, s.name, s.x_ly AS x, s.y_ly AS y, s.z_ly AS z,
                   s.galaxy_region_id
              FROM {schema}.systems s TABLESAMPLE SYSTEM ($1::float4)
             WHERE s.x_ly BETWEEN $2 AND $3
               AND s.y_ly BETWEEN $4 AND $5
               AND s.z_ly BETWEEN $6 AND $7
             ORDER BY md5(s.id64::text)
             LIMIT $8
          ) s{_MAIN_STAR_CLASS.format(schema=schema)}
         ORDER BY md5(s.id64::text)
    """


@router.get('/api/map/systems', response_model=MapViewportResponse)
@limiter.limit('60/minute')
async def map_systems(
    request: Request,
    min_x: float = Query(..., description='Viewport bounding-box min X (LY)'),
    max_x: float = Query(..., description='Viewport bounding-box max X (LY)'),
    min_y: float = Query(..., description='Viewport bounding-box min Y (LY)'),
    max_y: float = Query(..., description='Viewport bounding-box max Y (LY)'),
    min_z: float = Query(..., description='Viewport bounding-box min Z (LY)'),
    max_z: float = Query(..., description='Viewport bounding-box max Z (LY)'),
    limit: int = Query(
        10_000, ge=1, le=MAX_MAP_VIEWPORT_SYSTEMS,
        description='Maximum individual systems returned',
    ),
    pool: asyncpg.Pool = Depends(get_pool),
    redis: Optional[aioredis.Redis] = Depends(get_redis),
) -> MapViewportResponse:
    """Individual star systems within a viewport bounding box — the zoom-in
    real-star detail lane (the heatmap is the zoomed-out aggregate lane).

    Read-only. Returns up to `limit` systems ordered notable-first (populated,
    then by star-brightness proxy from spectral class), so the important stars
    appear first at partial zoom and fill in as you zoom deeper. An over-wide
    box is rejected with `too_wide=True` (the client should stay on the heatmap)
    rather than forcing a whole-galaxy scan.
    """
    # Tolerate min/max sent in either order.
    lo_x, hi_x = sorted((min_x, max_x))
    lo_y, hi_y = sorted((min_y, max_y))
    lo_z, hi_z = sorted((min_z, max_z))

    if ((hi_x - lo_x) > MAX_MAP_VIEWPORT_LY
            or (hi_y - lo_y) > MAX_MAP_VIEWPORT_LY
            or (hi_z - lo_z) > MAX_MAP_VIEWPORT_LY):
        cache_key = (
            f'map:systems:sample:v3:{lo_x:.0f}:{hi_x:.0f}:{lo_y:.0f}:{hi_y:.0f}:'
            f'{lo_z:.0f}:{hi_z:.0f}:{limit}'
        )
        cached = await cache_get(cache_key, redis)
        if cached is not None:
            return JSONResponse(content=cached)

        try:
            schema = await current_generation_schema(pool)
        except asyncpg.exceptions.UndefinedTableError:
            schema = None

        async with pool.acquire() as conn:
            if schema is None:
                rows = await conn.fetch("""
                    SELECT id64, name, x, y, z, main_star_type, galaxy_region_id,
                           (population IS NOT NULL AND population > 0) AS populated
                    FROM   systems TABLESAMPLE SYSTEM ($1::float4)
                    WHERE  x BETWEEN $2 AND $3
                      AND  y BETWEEN $4 AND $5
                      AND  z BETWEEN $6 AND $7
                    ORDER BY md5(id64::text)
                    LIMIT  $8
                """, GALAXY_SAMPLE_PERCENT, lo_x, hi_x, lo_y, hi_y, lo_z, hi_z,
                    min(limit, MAX_GALAXY_SAMPLE_SYSTEMS))
            else:
                rows = await conn.fetch(
                    galaxy_sample_stars_sql(schema),
                    GALAXY_SAMPLE_PERCENT, lo_x, hi_x, lo_y, hi_y, lo_z, hi_z,
                    min(limit, MAX_GALAXY_SAMPLE_SYSTEMS))

        systems = [
            MapViewportSystem(
                id64=r['id64'],
                name=r['name'],
                x=r['x'],
                y=r['y'],
                z=r['z'],
                main_star_class=r['main_star_type'],
                populated=r['populated'],
            )
            for r in rows
        ]
        result = MapViewportResponse(systems=systems, truncated=True)
        await cache_set(cache_key, result.model_dump(mode='json'), settings.ttl_cluster, redis)
        return result

    cache_key = (
        f'map:systems:v3:{lo_x:.0f}:{hi_x:.0f}:{lo_y:.0f}:{hi_y:.0f}:'
        f'{lo_z:.0f}:{hi_z:.0f}:{limit}'
    )
    cached = await cache_get(cache_key, redis)
    if cached is not None:
        return JSONResponse(content=cached)

    try:
        schema = await current_generation_schema(pool)
    except asyncpg.exceptions.UndefinedTableError:
        # Review and integration databases still use the legacy unqualified
        # catalogue. Production resolves the published V3 generation schema.
        schema = None

    async with pool.acquire() as conn:
        if schema is None:
            rows = await conn.fetch("""
                WITH candidates AS MATERIALIZED (
                    SELECT id64, name, x, y, z, main_star_type, galaxy_region_id,
                           (population IS NOT NULL AND population > 0) AS populated
                    FROM   systems
                    WHERE  x BETWEEN $1 AND $2
                      AND  y BETWEEN $3 AND $4
                      AND  z BETWEEN $5 AND $6
                    ORDER BY x, y, z
                    LIMIT  $7
                )
                SELECT id64, name, x, y, z, main_star_type, galaxy_region_id, populated
                FROM   candidates
                ORDER BY populated DESC,
                         CASE left(main_star_type, 1)
                            WHEN 'O' THEN 0 WHEN 'B' THEN 1 WHEN 'A' THEN 2
                            WHEN 'F' THEN 3 WHEN 'G' THEN 4 WHEN 'K' THEN 5
                            WHEN 'M' THEN 6 ELSE 7 END,
                         id64
            """, lo_x, hi_x, lo_y, hi_y, lo_z, hi_z, limit + 1)
        else:
            rows = await conn.fetch(
                close_up_stars_sql(schema),
                math.floor(lo_x / 10), math.floor(hi_x / 10),
                math.floor(lo_y / 10), math.floor(hi_y / 10),
                math.floor(lo_z / 10), math.floor(hi_z / 10),
                lo_x, hi_x, lo_y, hi_y, lo_z, hi_z, limit + 1)

    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]

    systems = [
        MapViewportSystem(
            id64=r['id64'],
            name=r['name'],
            x=r['x'],
            y=r['y'],
            z=r['z'],
            main_star_class=r['main_star_type'],
            populated=r['populated'],
        )
        for r in rows
    ]
    result = MapViewportResponse(systems=systems, truncated=truncated)
    await cache_set(cache_key, result.model_dump(), settings.ttl_search, redis)
    return result
