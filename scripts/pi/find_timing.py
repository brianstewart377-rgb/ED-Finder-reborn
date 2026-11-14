"""Time Find's searches on this machine. Reads only; changes nothing.

Run it inside the API container, so it uses the live code, the API's own
database settings and the same queries a visitor's search runs:

    curl -fsSL <address of this file> | sudo docker exec -i edfinder-api python -

Each search is run three times, straight to the database (the API's Redis
cache is not in the way):

    first       as it comes: what it needs may not be in memory yet
    again       straight away: what it needs is now in memory
    no JIT      again, with PostgreSQL's JIT compiler off for the connection

"first" minus "again" is time spent reading the drives; "again" minus "no JIT"
is time PostgreSQL spends compiling the query (JIT) before running it.

Only timings and result counts are printed. The places searched near are Sol
and Colonia, both public.
"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Any, Awaitable, Callable

SOL = {"x": 0.0, "y": 0.0, "z": 0.0}
COLONIA = {"x": -9530.5, "y": -910.28125, "z": 19808.125}
REACH = 250
PAGE = 50
ONE_EARTH_LIKE = {"elw_count": {"min": 1}}


def near(coords: dict, radius: int, **extra: Any) -> dict:
    return {
        "reference_coords": coords,
        "filters": {"distance": {"max": radius}},
        "sort_by": "distance",
        "size": PAGE,
        "from": 0,
        "reach_ly": REACH,
        **extra,
    }


def galaxy(**extra: Any) -> dict:
    return {
        "galaxy_wide": True,
        "sort_by": "development",
        "size": PAGE,
        "from": 0,
        "reach_ly": REACH,
        **extra,
    }


def scenarios(first_type: str) -> list[tuple[str, dict | None]]:
    """(label, search body). ``None`` is the plain type ranking route."""
    return [
        ("Any, near Sol, 250 ly", near(SOL, 250)),
        ("Any, near Sol, 500 ly", near(SOL, 500)),
        ("Any, near Colonia, 250 ly", near(COLONIA, 250)),
        ("Any, galaxy-wide", galaxy()),
        ("Any, galaxy-wide, 1+ Earth-like", galaxy(body_filters=ONE_EARTH_LIKE)),
        ("Type, near Sol, 250 ly", near(SOL, 250, archetype=first_type, min_score=60)),
        ("Type, galaxy-wide", None),
        ("Type, galaxy-wide, 1+ Earth-like",
         galaxy(archetype=first_type, min_score=60, body_filters=ONE_EARTH_LIKE)),
    ]


async def _timed(run: Callable[[], Awaitable[dict]]) -> tuple[float, str]:
    started = time.perf_counter()
    try:
        answer = await run()
    except Exception as exc:  # report and carry on with the next search
        return (time.perf_counter() - started) * 1000, f"FAILED: {type(exc).__name__}"
    total = answer.get("total")
    capped = answer.get("total_is_capped") or answer.get("is_truncated")
    shown = f"{total:,}{'+' if capped else ''} found" if isinstance(total, int) else ""
    return (time.perf_counter() - started) * 1000, shown


async def main() -> int:
    import asyncpg

    from edfinder_api import local_search
    from edfinder_api.config import settings
    from edfinder_api.ranking.profile import ARCHETYPE_KEYS
    from edfinder_api.routers import archetypes

    dsn = settings.database_readonly_url or settings.database_url

    async def pool(jit: bool) -> asyncpg.Pool:
        server_settings = {
            "application_name": "find_timing",
            "statement_timeout": str(settings.statement_timeout_ms),
        }
        if not jit:
            server_settings["jit"] = "off"
        return await asyncpg.create_pool(
            dsn=dsn, min_size=1, max_size=1, statement_cache_size=0,
            server_settings=server_settings,
        )

    with_jit, without_jit = await pool(True), await pool(False)
    try:
        async with with_jit.acquire() as connection:
            facts = []
            for name in ("jit", "jit_above_cost", "shared_buffers",
                         "effective_cache_size", "work_mem", "random_page_cost",
                         "statement_timeout"):
                facts.append(f"{name}={await connection.fetchval(f'SHOW {name}')}")
        print("Database: " + ", ".join(facts))
        first_type = ARCHETYPE_KEYS[0]
        print(f"Type used: {first_type}; reach {REACH} ly; {PAGE} results a page.")
        print(f"{'search':36} {'first':>9} {'again':>9} {'no JIT':>9}  result")
        for label, body in scenarios(first_type):
            def runner(pool_: asyncpg.Pool, body: dict | None = body) -> Callable[[], Awaitable[dict]]:
                if body is None:
                    return lambda: archetypes._archetype_rankings_v3(
                        archetype=first_type, min_score=60, galaxy_region=None,
                        max_distance_ly=None, has_elw=None, min_slots=None,
                        max_contamination=None, limit=PAGE, offset=0,
                        pool=pool_, reach_ly=REACH)
                return lambda: local_search.local_db_search_v3(dict(body), pool_)

            first, shown = await _timed(runner(with_jit))
            again, _ = await _timed(runner(with_jit))
            no_jit, _ = await _timed(runner(without_jit))
            print(f"{label:36} {first:7.0f}ms {again:7.0f}ms {no_jit:7.0f}ms  {shown}")
    finally:
        await with_jit.close()
        await without_jit.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
