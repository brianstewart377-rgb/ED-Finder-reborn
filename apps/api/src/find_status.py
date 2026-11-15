"""Find: the database status behind `/api/local/status`.

Moved here from local_search.py on 15 November 2026.
"""
from __future__ import annotations

import asyncpg

from edfinder_api.helpers import status_failure_reason_code_for_exception
from edfinder_api.local_search import CLUSTER_RADIUS_LY, log, MAX_SEARCH_RADIUS


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
async def local_db_status(pool: asyncpg.Pool) -> dict:
    """Return info about the PostgreSQL database and import progress."""
    try:
        async with pool.acquire() as conn:
            # Use pg_class.reltuples (PG's ANALYZE-derived row estimate) instead of
            # COUNT(*) — returns in ~1ms per table vs 30+ seconds for COUNT(*) FROM
            # bodies on the 1B+ row table. Estimates are plenty accurate for a
            # status display and refresh on every autovac/analyze cycle.
            #
            # Why: build_grid.py was being starved of disk I/O by repeated polls
            # of /api/local/status from the V2 Admin tab (every 30s). Each poll
            # triggered 8 sequential COUNT(*) full scans through pgbouncer.
            counts = await conn.fetchrow("""
                SELECT
                  COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='systems'),         0) AS sys_count,
                  COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='bodies'),          0) AS body_count,
                  COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='stations'),        0) AS station_count,
                  COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='cluster_summary'), 0) AS cluster_count,
                  COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='spatial_grid'),    0) AS grid_count,
                  COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='macro_grid'),      0) AS macro_count,
                  COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='ratings'),         0) AS rated_count,
                  COALESCE((SELECT count(*)         FROM galaxy_regions),                            0) AS region_count
            """)
            sys_count     = int(counts["sys_count"])
            body_count    = int(counts["body_count"])
            station_count = int(counts["station_count"])
            cluster_count = int(counts["cluster_count"])
            grid_count    = int(counts["grid_count"])
            macro_count   = int(counts["macro_count"])
            rated_count   = int(counts["rated_count"])
            region_count  = int(counts["region_count"])

            import_rows = await conn.fetch("""
                SELECT dump_file, status, rows_processed, errors_encountered,
                       started_at, completed_at
                FROM import_meta ORDER BY id
            """)
            import_status = {
                r["dump_file"]: {
                    "status":             str(r["status"]),
                    "rows_processed":     r["rows_processed"],
                    "errors_encountered": r["errors_encountered"],
                    "started_at":         r["started_at"].isoformat() if r["started_at"] else None,
                    "completed_at":       r["completed_at"].isoformat() if r["completed_at"] else None,
                }
                for r in import_rows
            }

            db_size_bytes = await conn.fetchval(
                "SELECT pg_database_size(current_database())"
            )
            pg_version = await conn.fetchval("SELECT version()")

        return {
            "available":            True,
            "backend":              "postgresql",
            "pg_version":           pg_version,
            "systems_count":        sys_count,
            "rated_count":          rated_count,
            "body_count":           body_count,
            "station_count":        station_count,
            "cluster_count":        cluster_count,
            "grid_cells":           grid_count,
            "macro_grid_cells":     macro_count,
            "galaxy_regions":       region_count,
            "db_size_mb":           round(db_size_bytes / 1_048_576, 1) if db_size_bytes else 0,
            "import_status":        import_status,
            "max_search_radius_ly": int(MAX_SEARCH_RADIUS),
            "cluster_radius_ly":    int(CLUSTER_RADIUS_LY),
            "has_bodies":           True,
            "has_cluster_summary":  cluster_count > 0,
        }
    except Exception as exc:
        # The raw exception text (which can carry SQL, host names or DSN
        # fragments) stays in the server log; the caller only ever sees one
        # of the fixed STATUS_REASON_CODES. Callers must not cache this
        # branch — a cached failure hides recovery for the whole TTL.
        reason = status_failure_reason_code_for_exception(exc)
        log.warning("local_db_status error (%s): %s: %s", reason, type(exc).__name__, exc)
        return {"available": False, "reason": reason}
