"""Find: name suggestions behind `/api/local/autocomplete`.

Moved here from local_search.py on 15 November 2026.
"""
from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import HTTPException

from edfinder_api.find_generation import _current_derived_generation
from edfinder_api.helpers import safe_coords_from_row


def _safe_coords(row: Any) -> dict:
    return safe_coords_from_row(row)




# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------
AUTOCOMPLETE_CANDIDATE_LIMIT = 100
AUTOCOMPLETE_RESULT_LIMIT = 10
AUTOCOMPLETE_SQL = f"""
    SELECT system_id64 AS id64, name,
           public.cube_ll_coord(position_ly, 1) AS x,
           public.cube_ll_coord(position_ly, 2) AS y,
           public.cube_ll_coord(position_ly, 3) AS z,
           galaxy_region_id
    FROM v3_derived.system_search
    WHERE derived_generation_id = $1 AND lower(name) LIKE $2
    LIMIT {AUTOCOMPLETE_CANDIDATE_LIMIT}
"""


def _autocomplete_prefix_pattern(q: str) -> str:
    """A literal, case-insensitive prefix using PostgreSQL's LIKE escape."""
    return q.lower().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


async def local_db_autocomplete(
    q: str,
    pool: asyncpg.Pool,
    *,
    generation_row: dict[str, Any] | None = None,
) -> list:
    """Read a bounded prefix window from the published V3 search projection.

    Migration 023's generation + text_pattern_ops index supports this LIKE
    range even under en_US.utf8. Sort only the bounded candidates in Python:
    an SQL ORDER BY name would sort every matching prefix before the LIMIT.
    The route may supply its resolved generation to keep a cache entry tied
    to the same immutable rows when a publication lands during the request.
    """
    if len(q) < 2:
        return []

    async with pool.acquire() as conn:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            if generation_row is None:
                try:
                    generation_row = await _current_derived_generation(conn)
                except HTTPException as exc:
                    if exc.status_code == 404:
                        raise HTTPException(503, 'No published Ratings V4 generation') from exc
                    raise
            # A generic prepared plan cannot derive LIKE bounds from $2. The
            # API disables its statement cache, but enforce a custom plan here
            # too so alternate pools keep the same bounded index read.
            await conn.execute('SET LOCAL plan_cache_mode = force_custom_plan')
            rows = await conn.fetch(
                AUTOCOMPLETE_SQL,
                generation_row['derived_generation_id'],
                _autocomplete_prefix_pattern(q),
            )

    results = []
    for r in sorted(rows, key=lambda row: row['name'])[:AUTOCOMPLETE_RESULT_LIMIT]:
        coords = _safe_coords(r)
        results.append({
            "name":              r["name"],
            "id64":              r["id64"],
            "x":                 coords["x"],
            "y":                 coords["y"],
            "z":                 coords["z"],
            "galaxy_region_id":  r["galaxy_region_id"],
            "record": {
                "id64": r["id64"],
                "name": r["name"],
                "x":    coords["x"],
                "y":    coords["y"],
                "z":    coords["z"],
            },
        })
    return results
