#!/usr/bin/env python3
"""
ED Finder — Cluster Summary Builder
Version: 4.0  (Macro-grid architecture)

ARCHITECTURE CHANGE in v4.0:
  Previous versions attempted to pre-compute cluster summaries for EVERY
  anchor system in the galaxy (~73M systems). This was fundamentally O(N×M)
  and would take days to complete, constantly fighting PostgreSQL timeouts
  in dense regions like the galactic core.

  v4.0 uses a MACRO-GRID approach:
    1. The galaxy is divided into 2000 LY macro-cells (built by build_grid.py).
    2. For each macro-cell, find the TOP 50 anchor systems by coverage score.
    3. Compute the full 500 LY bubble stats only for those 50 anchors.
    4. Store results in cluster_summary.

  This reduces the number of expensive spatial aggregation queries from
  ~125,000 (one per 500 LY cell) to ~500-1000 (one per 2000 LY macro-cell).
  Each query only processes 50 anchors instead of up to 50,000.

  The result: cluster build time drops from 3-24 hours to 30-90 minutes.
  Dense regions (Sol, galactic core) are handled gracefully because we only
  compute the top 50 anchors per macro-cell, not every system.

  Score bands (what they mean to a colonist):
    0–30  : Barely viable
    31–50 : Functional
    51–65 : Solid — default search threshold
    66–80 : Excellent
    81–100: Exceptional

Performance:
  v3.4 (old): 125K SQL aggregations (6 threads) → 1.5–3 h ETA
  v4.0 (new): ~800 macro-cells × 50 anchors  → 30–90 min ETA
"""

import os
import sys
import time
import logging
import argparse
import multiprocessing as mp
from multiprocessing import Queue, Value
import ctypes

import psycopg

from shared_contracts.bulk_update_helper import bulk_update_replica_mode

from progress import (
    ProgressReporter,
    startup_banner, done_banner,
    fmt_num, fmt_duration,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATABASE_URL   = os.environ['DATABASE_URL']  # fail-fast: no insecure 'edfinder:edfinder' fallback
LOG_LEVEL      = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE       = os.getenv('LOG_FILE', '/tmp/build_clusters.log')
DEFAULT_RADIUS = 500    # LY — the standard colonisation bubble radius
DEFAULT_SCORE  = 65     # Minimum score for a system to be considered viable
TOP_N_ANCHORS  = 50     # Top N anchors to compute per macro-cell
DEFAULT_CELL_TIMEOUT = 1800  # 30 min — proven at 187M-system scale
FULL_CLEANUP_STATEMENT_TIMEOUT = '4h'  # >2x the observed 1h41m production cleanup

os.makedirs(os.path.dirname(os.path.abspath(LOG_FILE)), exist_ok=True)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger('build_clusters')

# ---------------------------------------------------------------------------
# SQL: Find top N candidate anchors in a macro-cell
# ---------------------------------------------------------------------------
# Step 1: Find the top-scoring anchor systems within a macro-cell.
# We rank by overall score DESC to pick the most promising anchors.
# Only systems with body data (has_body_data=TRUE) are considered anchors.
FIND_ANCHORS_SQL = """
    SELECT s.id64, s.x, s.y, s.z, r.score
    FROM systems s
    JOIN ratings r ON r.system_id64 = s.id64
    WHERE s.macro_grid_id = %(macro_cell_id)s
      AND s.has_body_data = TRUE
      AND r.score IS NOT NULL
      AND r.score >= %(min_score)s
    ORDER BY r.score DESC
    LIMIT %(top_n)s
"""

# ---------------------------------------------------------------------------
# SQL: Compute 500 LY bubble stats for a single anchor
# ---------------------------------------------------------------------------
# Step 2: For each anchor, find all viable systems within 500 LY and
# aggregate their economy scores.
AGGREGATE_SQL = """
WITH viable AS (
    SELECT
        s.id64, s.x, s.y, s.z,
        r.score_agriculture, r.score_refinery, r.score_industrial,
        r.score_hightech, r.score_military, r.score_tourism
    FROM systems s
    JOIN ratings r ON r.system_id64 = s.id64
    WHERE s.grid_cell_id = ANY(%(search_cells)s)
      AND s.population = 0
      AND (r.score_agriculture >= %(min_score)s OR r.score_refinery >= %(min_score)s OR
           r.score_industrial  >= %(min_score)s OR r.score_hightech  >= %(min_score)s OR
           r.score_military    >= %(min_score)s OR r.score_tourism   >= %(min_score)s)
),
pairs AS (
    SELECT
        v.id64  AS viable_id,
        v.score_agriculture, v.score_refinery, v.score_industrial,
        v.score_hightech, v.score_military, v.score_tourism
    FROM viable v
    WHERE v.id64 <> %(anchor_id)s
      AND in_bounding_box(v.x, v.y, v.z, %(anchor_x)s, %(anchor_y)s, %(anchor_z)s, %(radius)s)
      AND distance_ly(v.x, v.y, v.z, %(anchor_x)s, %(anchor_y)s, %(anchor_z)s) <= %(radius)s
)
SELECT
    COUNT(*) FILTER (WHERE score_agriculture >= %(min_score)s)              AS agriculture_count,
    MAX(score_agriculture) FILTER (WHERE score_agriculture >= %(min_score)s) AS agriculture_best,
    (SELECT viable_id FROM pairs WHERE score_agriculture >= %(min_score)s
     ORDER BY score_agriculture DESC LIMIT 1)                               AS agriculture_top_id,
    COUNT(*) FILTER (WHERE score_refinery    >= %(min_score)s)              AS refinery_count,
    MAX(score_refinery)    FILTER (WHERE score_refinery    >= %(min_score)s) AS refinery_best,
    (SELECT viable_id FROM pairs WHERE score_refinery >= %(min_score)s
     ORDER BY score_refinery DESC LIMIT 1)                                  AS refinery_top_id,
    COUNT(*) FILTER (WHERE score_industrial  >= %(min_score)s)              AS industrial_count,
    MAX(score_industrial)  FILTER (WHERE score_industrial  >= %(min_score)s) AS industrial_best,
    (SELECT viable_id FROM pairs WHERE score_industrial >= %(min_score)s
     ORDER BY score_industrial DESC LIMIT 1)                                AS industrial_top_id,
    COUNT(*) FILTER (WHERE score_hightech    >= %(min_score)s)              AS hightech_count,
    MAX(score_hightech)    FILTER (WHERE score_hightech    >= %(min_score)s) AS hightech_best,
    (SELECT viable_id FROM pairs WHERE score_hightech >= %(min_score)s
     ORDER BY score_hightech DESC LIMIT 1)                                  AS hightech_top_id,
    COUNT(*) FILTER (WHERE score_military    >= %(min_score)s)              AS military_count,
    MAX(score_military)    FILTER (WHERE score_military    >= %(min_score)s) AS military_best,
    (SELECT viable_id FROM pairs WHERE score_military >= %(min_score)s
     ORDER BY score_military DESC LIMIT 1)                                  AS military_top_id,
    COUNT(*) FILTER (WHERE score_tourism     >= %(min_score)s)              AS tourism_count,
    MAX(score_tourism)     FILTER (WHERE score_tourism     >= %(min_score)s) AS tourism_best,
    (SELECT viable_id FROM pairs WHERE score_tourism >= %(min_score)s
     ORDER BY score_tourism DESC LIMIT 1)                                   AS tourism_top_id,
    COUNT(*)                                                                 AS total_viable
FROM pairs
"""

# Intentionally keep replica mode off for this mixed INSERT/UPDATE upsert:
# new cluster_summary child rows must retain their FK check against systems.
# The costly parent-table RI-trigger storm applies to UPDATE systems, not this
# bounded derived-table write.
INSERT_SQL = """
    INSERT INTO cluster_summary (
        system_id64,
        agriculture_count, agriculture_best, agriculture_top_id,
        refinery_count,    refinery_best,    refinery_top_id,
        industrial_count,  industrial_best,  industrial_top_id,
        hightech_count,    hightech_best,    hightech_top_id,
        military_count,    military_best,    military_top_id,
        tourism_count,     tourism_best,     tourism_top_id,
        total_viable, coverage_score, economy_diversity,
        search_radius, macro_grid_id, dirty, computed_at, updated_at
    ) VALUES {values_template}
    ON CONFLICT (system_id64) DO UPDATE SET
        agriculture_count  = EXCLUDED.agriculture_count,
        agriculture_best   = EXCLUDED.agriculture_best,
        agriculture_top_id = EXCLUDED.agriculture_top_id,
        refinery_count     = EXCLUDED.refinery_count,
        refinery_best      = EXCLUDED.refinery_best,
        refinery_top_id    = EXCLUDED.refinery_top_id,
        industrial_count   = EXCLUDED.industrial_count,
        industrial_best    = EXCLUDED.industrial_best,
        industrial_top_id  = EXCLUDED.industrial_top_id,
        hightech_count     = EXCLUDED.hightech_count,
        hightech_best      = EXCLUDED.hightech_best,
        hightech_top_id    = EXCLUDED.hightech_top_id,
        military_count     = EXCLUDED.military_count,
        military_best      = EXCLUDED.military_best,
        military_top_id    = EXCLUDED.military_top_id,
        tourism_count      = EXCLUDED.tourism_count,
        tourism_best       = EXCLUDED.tourism_best,
        tourism_top_id     = EXCLUDED.tourism_top_id,
        total_viable       = EXCLUDED.total_viable,
        coverage_score     = EXCLUDED.coverage_score,
        economy_diversity  = EXCLUDED.economy_diversity,
        macro_grid_id      = EXCLUDED.macro_grid_id,
        dirty              = FALSE,
        updated_at         = NOW()
"""

INSERT_TEMPLATE = """(%s,
    %s,%s,%s, %s,%s,%s, %s,%s,%s,
    %s,%s,%s, %s,%s,%s, %s,%s,%s,
    %s,%s,%s, %s,%s, FALSE, NOW(), NOW())"""


def _cluster_insert_sql() -> str:
    """Return the single-row shape used by Psycopg 3 ``executemany``."""
    return INSERT_SQL.format(values_template=INSERT_TEMPLATE)

# ---------------------------------------------------------------------------
# Coverage score (Python side — mirrors the SQL function)
# ---------------------------------------------------------------------------
def _row_to_dict(description, row) -> dict:
    """Map a positional DB row to its cursor column names."""
    columns = [column[0] for column in description]
    return dict(zip(columns, row, strict=True))


def compute_coverage_score(rd: dict) -> float:
    """
    Compute a weighted coverage score (0-100) for an anchor system.
    Reflects how well a 500 LY bubble around this anchor covers all
    economy types a colonist might need.

    Weights reflect the relative importance of each economy for a
    self-sufficient colonisation empire:
    - Agriculture: food supply (essential)
    - Refinery: CMM Composites and raw materials (essential)
    - Industrial: manufacturing (essential)
    - HighTech: advanced components (important)
    - Military: security (important)
    - Tourism: wealth generation (useful)
    """
    weights = {
        'agriculture': 0.22,
        'refinery':    0.22,
        'industrial':  0.20,
        'hightech':    0.18,
        'military':    0.12,
        'tourism':     0.06,
    }
    score = 0.0
    for eco, weight in weights.items():
        best  = rd.get(f'{eco}_best') or 0
        count = rd.get(f'{eco}_count') or 0
        if best > 0:
            # Score = (best_quality / 100) + count_bonus (diminishing returns)
            count_bonus = min(count / 5.0, 1.0) * 0.15
            score += (best / 100.0 + count_bonus) * weight
    return round(min(score * 100, 100.0), 1)


# ---------------------------------------------------------------------------
# DB connection helper
# ---------------------------------------------------------------------------
def _connect_with_retry(worker_id: int, db_url: str, cell_timeout: int = 120,
                        max_attempts: int = 10) -> tuple:
    """Connect with exponential backoff retry. Returns (conn, cur)."""
    for attempt in range(1, max_attempts + 1):
        try:
            conn = psycopg.connect(db_url)
            conn.autocommit = False
            cur = conn.cursor()
            cur.execute(f"SET statement_timeout = '{cell_timeout}s'")
            conn.commit()
            if attempt > 1:
                print(f"[W{worker_id}] Reconnected after {attempt} attempts", flush=True)
            return conn, cur
        except Exception as e:
            wait = min(2 ** attempt, 60)
            print(f"[W{worker_id}] DB connect attempt {attempt} failed: {e} — retrying in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"[W{worker_id}] Could not connect to DB after {max_attempts} attempts")


def _clear_full_rebuild_dirty_flags(conn, cur) -> int:
    """Clear eligible dirty flags with a timeout sized for production scale."""
    with bulk_update_replica_mode(conn):
        cur.execute(
            f"SET statement_timeout = '{FULL_CLEANUP_STATEMENT_TIMEOUT}'"
        )
        cur.execute(
            "UPDATE systems SET cluster_dirty = FALSE "
            "WHERE has_body_data = TRUE "
            "AND macro_grid_id IS NOT NULL "
            "AND cluster_dirty = TRUE"
        )
        cleared = cur.rowcount
        conn.commit()
    return cleared


def _clear_cell_dirty_flags(conn, cur, macro_cell_id: int) -> int:
    """Clear only still-dirty, body-backed systems in one completed cell."""
    with bulk_update_replica_mode(conn):
        cur.execute(
            "UPDATE systems SET cluster_dirty = FALSE "
            "WHERE macro_grid_id = %s "
            "AND has_body_data = TRUE "
            "AND cluster_dirty = TRUE",
            (macro_cell_id,),
        )
        cleared = cur.rowcount
        conn.commit()
    return cleared


# ---------------------------------------------------------------------------
# Worker function
# ---------------------------------------------------------------------------
def worker_fn(worker_id: int, macro_queue: Queue, done_counter, db_url: str,
              radius: float, min_score: int, dirty_only: bool,
              cell_timeout: int = DEFAULT_CELL_TIMEOUT,
              top_n: int = TOP_N_ANCHORS):
    """
    Pull macro-cells from the queue and process each one.

    For each macro-cell:
    1. Find the top N anchor systems by score.
    2. For each anchor, compute its 500 LY bubble stats.
    3. Write results to cluster_summary.
    """
    try:
        conn, cur = _connect_with_retry(worker_id, db_url, cell_timeout)
    except Exception as e:
        print(f"[W{worker_id}] DB connect failed permanently: {e}", flush=True)
        return

    while True:
        try:
            macro_cell_id = macro_queue.get(timeout=5)
        except Exception:
            break

        if macro_cell_id is None:
            break

        # Step 1: Find top N anchors in this macro-cell
        try:
            cur.execute(FIND_ANCHORS_SQL, {
                'macro_cell_id': macro_cell_id,
                'min_score':     min_score,
                'top_n':         top_n,
            })
            anchors = cur.fetchall()
        except Exception as e:
            print(f"[W{worker_id}] Macro-cell {macro_cell_id} anchor query failed: {e}", flush=True)
            try:
                conn.rollback()
            except Exception:
                pass
            with done_counter.get_lock():
                done_counter.value += 1
            continue

        if not anchors:
            with done_counter.get_lock():
                done_counter.value += 1
            continue

        write_batch = []
        cell_complete = True

        # Step 2: Compute 500 LY bubble for each anchor
        for anchor_id, anchor_x, anchor_y, anchor_z, anchor_score in anchors:
            # Build 27-cell neighbourhood for the viable systems search
            # First find the 500 LY grid cell for this anchor
            try:
                cur.execute(
                    "SELECT grid_cell_id FROM systems WHERE id64 = %s",
                    (anchor_id,)
                )
                row = cur.fetchone()
                if not row or not row[0]:
                    cell_complete = False
                    continue
                anchor_cell = row[0]
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                cell_complete = False
                continue

            # Decode cell coordinates from cell_id
            vcz = anchor_cell % 10000
            rem = anchor_cell // 10000
            vcy = rem % 10000
            vcx = rem // 10000
            search_cells = [
                (vcx + dx) * 100_000_000 + (vcy + dy) * 10_000 + (vcz + dz)
                for dx in range(-1, 2)
                for dy in range(-1, 2)
                for dz in range(-1, 2)
            ]

            try:
                cur.execute(AGGREGATE_SQL, {
                    'search_cells': search_cells,
                    'anchor_id':    anchor_id,
                    'anchor_x':     anchor_x,
                    'anchor_y':     anchor_y,
                    'anchor_z':     anchor_z,
                    'radius':       radius,
                    'min_score':    min_score,
                })
                row = cur.fetchone()
            except Exception as e:
                print(f"[W{worker_id}] Anchor {anchor_id} aggregation failed: {e}", flush=True)
                try:
                    conn.rollback()
                except Exception:
                    pass
                cell_complete = False
                continue

            if not row:
                cell_complete = False
                continue

            rd = _row_to_dict(cur.description, row)

            coverage  = compute_coverage_score(rd)
            diversity = sum(1 for eco in ('agriculture', 'refinery', 'industrial',
                                          'hightech', 'military', 'tourism')
                            if (rd.get(f'{eco}_count') or 0) > 0)

            write_batch.append((
                anchor_id,
                rd['agriculture_count'], rd['agriculture_best'], rd['agriculture_top_id'],
                rd['refinery_count'],    rd['refinery_best'],    rd['refinery_top_id'],
                rd['industrial_count'],  rd['industrial_best'],  rd['industrial_top_id'],
                rd['hightech_count'],    rd['hightech_best'],    rd['hightech_top_id'],
                rd['military_count'],    rd['military_best'],    rd['military_top_id'],
                rd['tourism_count'],     rd['tourism_best'],     rd['tourism_top_id'],
                rd['total_viable'],      coverage,               diversity,
                radius,                  macro_cell_id,
            ))

        # Step 3: Write batch to cluster_summary
        if write_batch:
            try:
                cur.executemany(_cluster_insert_sql(), write_batch)
                conn.commit()
            except psycopg.OperationalError as e:
                print(f"[W{worker_id}] Write lost connection: {e} — reconnecting", flush=True)
                try:
                    try:
                        cur.close()
                        conn.close()
                    except Exception:
                        pass
                    conn, cur = _connect_with_retry(worker_id, db_url, cell_timeout)
                    cur.executemany(_cluster_insert_sql(), write_batch)
                    conn.commit()
                except Exception as e2:
                    print(f"[W{worker_id}] Write failed after reconnect: {e2}", flush=True)
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    cell_complete = False
            except Exception as e:
                print(f"[W{worker_id}] Write error: {e}", flush=True)
                try:
                    conn.rollback()
                except Exception:
                    pass
                cell_complete = False

        # Clear dirty flags for systems in this macro-cell if dirty-only mode
        if dirty_only and cell_complete:
            try:
                _clear_cell_dirty_flags(conn, cur, macro_cell_id)
            except Exception as e:
                print(
                    f"[W{worker_id}] Macro-cell {macro_cell_id} dirty cleanup failed: {e}",
                    flush=True,
                )
                try:
                    conn.rollback()
                except Exception:
                    pass

        with done_counter.get_lock():
            done_counter.value += 1

    cur.close()
    conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='Build cluster_summary (v4.0 — Macro-grid architecture)'
    )
    parser.add_argument('--workers',      type=int,   default=6)
    parser.add_argument('--radius',       type=float, default=DEFAULT_RADIUS)
    parser.add_argument('--min-score',    type=int,   default=DEFAULT_SCORE,
                        help=f'Minimum economy score for viable systems (default: {DEFAULT_SCORE}). '
                             f'Score bands: 0-30=barely viable, 31-50=functional, '
                             f'51-65=solid, 66-80=excellent, 81-100=exceptional.')
    parser.add_argument('--top-n',        type=int,   default=TOP_N_ANCHORS,
                        help=f'Top N anchors to compute per macro-cell (default: {TOP_N_ANCHORS})')
    parser.add_argument('--dirty-only',   action='store_true',
                        help='Only rebuild clusters for dirty anchors')
    parser.add_argument('--cell-timeout', type=int,   default=DEFAULT_CELL_TIMEOUT,
                        help=f'Max seconds per anchor query (default: {DEFAULT_CELL_TIMEOUT})')
    args = parser.parse_args()

    script_start = time.time()
    startup_banner(log, "Cluster Summary Builder", "4.0 (Macro-grid)", [
        ("Architecture",  "2000 LY macro-grid — top-N anchors per cell"),
        ("Radius",        f"{args.radius} LY"),
        ("Min score",     f"{args.min_score} (65=solid, 80=excellent)"),
        ("Top N anchors", f"{args.top_n} per macro-cell"),
        ("Workers",       str(args.workers)),
        ("Dirty only",    str(args.dirty_only)),
    ])

    try:
        conn = psycopg.connect(DATABASE_URL)
    except Exception as e:
        log.error(f"FATAL: Cannot connect to database: {e}")
        sys.exit(1)

    cur = conn.cursor()
    cur.execute(f"SET statement_timeout = '{args.cell_timeout}s'")

    # ------------------------------------------------------------------
    # Step 1: Find all viable macro-cells
    # ------------------------------------------------------------------
    log.info("  Finding viable macro-cells ...")

    if args.dirty_only:
        cur.execute("""
            SELECT DISTINCT s.macro_grid_id
            FROM systems s
            JOIN ratings r ON r.system_id64 = s.id64
            WHERE s.cluster_dirty = TRUE
              AND s.macro_grid_id IS NOT NULL
              AND s.has_body_data = TRUE
              AND r.score IS NOT NULL
              AND r.score >= %s
        """, (args.min_score,))
    else:
        cur.execute("""
            SELECT DISTINCT s.macro_grid_id
            FROM systems s
            JOIN ratings r ON r.system_id64 = s.id64
            WHERE s.macro_grid_id IS NOT NULL
              AND s.has_body_data = TRUE
              AND r.score IS NOT NULL
              AND r.score >= %s
        """, (args.min_score,))

    viable_cells = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()

    if not viable_cells:
        log.warning("No viable macro-cells found. Are ratings and macro-grid built? Exiting.")
        sys.exit(0)

    total_cells = len(viable_cells)
    log.info(f"  Found {fmt_num(total_cells)} viable macro-cells.")
    log.info(f"  Each cell: top {args.top_n} anchors × 500 LY bubble aggregation")
    log.info(f"  Estimated anchors to process: {fmt_num(total_cells * args.top_n)}")
    log.info(f"  Launching {args.workers} parallel workers ...")

    # ------------------------------------------------------------------
    # Step 2: Fill the work queue
    # ------------------------------------------------------------------
    macro_queue  = Queue()
    done_counter = Value(ctypes.c_int, 0)

    for cell in viable_cells:
        macro_queue.put(cell)
    for _ in range(args.workers):
        macro_queue.put(None)  # sentinels

    # ------------------------------------------------------------------
    # Step 3: Start workers
    # ------------------------------------------------------------------
    workers = []
    for wid in range(args.workers):
        p = mp.Process(
            target=worker_fn,
            args=(wid, macro_queue, done_counter, DATABASE_URL,
                  args.radius, args.min_score, args.dirty_only,
                  args.cell_timeout, args.top_n),
            daemon=True,
        )
        p.start()
        workers.append(p)

    # ------------------------------------------------------------------
    # Step 4: Progress reporting
    # ------------------------------------------------------------------
    progress = ProgressReporter(log, total=total_cells, label="macro-cells", interval=30)
    last_done = 0

    while any(p.is_alive() for p in workers):
        time.sleep(5)
        current = done_counter.value
        delta = current - last_done
        if delta > 0:
            progress.update(delta)
            last_done = current

    current = done_counter.value
    delta = current - last_done
    if delta > 0:
        progress.update(delta)

    for p in workers:
        p.join()

    # ------------------------------------------------------------------
    # Step 5: Mark build complete
    # ------------------------------------------------------------------
    conn2 = psycopg.connect(DATABASE_URL)
    cur2  = conn2.cursor()
    cur2.execute("""
        INSERT INTO app_meta (key, value, updated_at)
        VALUES ('clusters_built', 'true', NOW())
        ON CONFLICT (key) DO UPDATE SET value = 'true', updated_at = NOW()
    """)

    if not args.dirty_only:
        log.info("Clearing cluster_dirty flags after full rebuild ...")
        full_cleanup_started = time.monotonic()
        full_cleanup_cleared = _clear_full_rebuild_dirty_flags(conn2, cur2)
        full_cleanup_elapsed = time.monotonic() - full_cleanup_started
        log.info(
            "Cleared %s cluster_dirty flags after full rebuild in %s.",
            fmt_num(full_cleanup_cleared),
            fmt_duration(full_cleanup_elapsed),
        )

    # Quick stats
    cur2.execute("SELECT COUNT(*), AVG(coverage_score)::int, MAX(coverage_score) FROM cluster_summary")
    stats = cur2.fetchone()
    conn2.commit()
    cur2.close()
    conn2.close()

    elapsed = time.time() - script_start
    done_banner(log, "Cluster Build Complete", elapsed, [
        f"Macro-cells processed : {fmt_num(total_cells)}",
        f"Anchors in table      : {fmt_num(stats[0])}",
        f"Avg coverage score    : {stats[1]}",
        f"Max coverage score    : {stats[2]}",
        f"Workers used          : {args.workers}",
        f"Total time            : {fmt_duration(elapsed)}",
    ])


if __name__ == '__main__':
    main()
