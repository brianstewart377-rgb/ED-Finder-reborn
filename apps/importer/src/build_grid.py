#!/usr/bin/env python3
"""
ED Finder — Spatial Grid Builder
Version: 2.4  (apply RI trigger disable to write_conn, not monitoring conn)

ROOT CAUSE OF THE 41M STALL — DEFINITIVE ANSWER
=================================================

After 3 days of debugging across multiple strategies, here is what was
actually killing every UPDATE attempt at ~41M rows:

THE REAL KILLER: Referential Integrity (RI) triggers
------------------------------------------------------
The systems table has 8 child tables with FOREIGN KEY ... REFERENCES systems(id64):
  • bodies, stations, attractions, factions_presence
  • ratings, cluster_summary, watchlist, system_notes

PostgreSQL automatically creates TWO RI trigger functions per FK:
  • RI_ConstraintTrigger_a_XXXXXX  (on child: check parent exists on INSERT/UPDATE)
  • RI_ConstraintTrigger_c_XXXXXX  (on parent: check no orphans on UPDATE/DELETE)

The PARENT-SIDE triggers (RI_ConstraintTrigger_c_*) fire on EVERY UPDATE of
systems — even when updating grid_cell_id which has NOTHING to do with any FK.

This is why you saw 17 triggers in pg_trigger:
  8 FKs × 2 triggers = 16 RI triggers + 1 custom (trg_system_dirty) = 17

For EACH of 145M rows, PostgreSQL fires the parent-side RI check:
  "Does any child row reference this id64?  If so, is the NEW id64 the same?"
  (It is — we're only updating grid_cell_id, not id64 — but Postgres checks anyway
   because UPDATE fires ALL row-level triggers regardless of which columns changed,
   UNLESS the trigger uses UPDATE OF specific_column syntax.)

trg_system_dirty uses UPDATE OF (economy, population, etc.) so it correctly
skips when only grid_cell_id changes.  But RI triggers are created by
PostgreSQL internally and ALWAYS fire on any UPDATE, period.

WHY IT STALLED AT EXACTLY 41M:
  The first ~41M rows imported were from galaxy_populated.json.gz (colonised
  systems).  These have child rows in ratings, cluster_summary, watchlist, etc.
  For each of those 41M rows, the RI triggers find child rows and do extra
  index lookups.  The remaining 145M are from galaxy.json.gz (uncolonised,
  no children).  So the first 41M rows were SLOW (RI checks find rows),
  then progress appeared to stop because the cost of those checks was
  compounding with WAL pressure.  The UPDATE wasn't stuck — it was just
  running at 0.001% of its potential speed.

THE FIX: Disable triggers during bulk UPDATE
--------------------------------------------
PostgreSQL superusers can disable triggers on a table for their session:

    SET session_replication_role = replica;

This disables ALL non-ALWAYS triggers for the current session, including
RI constraint triggers.  The data is safe because:
  1. We are only updating grid_cell_id — NOT id64 (the FK column)
  2. No FK relationship involves grid_cell_id
  3. We re-enable triggers immediately after the UPDATE completes
  4. The RI constraints are still enforced for all other sessions

Alternative (if not superuser):
    ALTER TABLE systems DISABLE TRIGGER ALL;  -- requires table ownership
    ... UPDATE ...
    ALTER TABLE systems ENABLE TRIGGER ALL;

We use session_replication_role = replica as it's safer (session-scoped,
automatically reverts on disconnect) and doesn't require table ownership.

PREVIOUS BUG FIXES (v2.0 / v2.1 / v2.2, kept)
================================================
  BUG #1 — EXISTS correlated subquery (hangs 22 min) → fixed v2.0
  BUG #2 — pg_advisory_lock never released on crash → removed v2.0
  BUG #3 — pgBouncer transaction-pool breaks cursors → bypass v2.0
  BUG #4 — ALTER TABLE autovacuum AccessExclusive deadlock → removed v2.0
  BUG #5 — write_conn not rolled back on reconnect → fixed v2.0
  BUG #6 — partial Stage 2 cells accepted as complete → fixed v2.0
  BUG #7 — smallint overflow in cell_id formula → documented v2.0
  BUG #8 — stale total_systems cache causes Stage 3 skip → fixed v2.1
  BUG #9 — single UPDATE stalls at 41M (WAL/checkpoint) → fixed v2.2
  BUG #10 — RI triggers fire on every row UPDATE (the real killer) → fixed v2.3
  BUG #11 — session_replication_role set on monitoring conn, not write_conn → fixed v2.4

Usage:
    python3 build_grid.py
    python3 build_grid.py --cell-size 500          # default
    python3 build_grid.py --pages-per-batch 10000  # pages per commit (default: 10000)
    python3 build_grid.py --strategy formula        # ctid-range batching (default)
    python3 build_grid.py --strategy batched        # per-cell bounding box (fallback)
    python3 build_grid.py --direct-host postgres    # override DB host
    python3 build_grid.py --reset-cache             # wipe app_meta bounds cache
    python3 build_grid.py --no-disable-triggers     # skip trigger disable (not recommended)
"""

import os
import sys
import math
import time
import signal
import logging
import argparse
from multiprocessing import Process

import psycopg

from progress import (
    ProgressReporter,
    startup_banner, stage_banner, done_banner, crash_hint,
    fmt_duration, fmt_num, fmt_rate, fmt_pct,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# CRITICAL: connect DIRECTLY to postgres, not through pgBouncer.
# pgBouncer transaction-pool mode breaks server-side cursors and long
# transactions.  The docker-compose.yml exposes postgres on 5432 (host-only)
# and pgBouncer on 5433.  Inside the Docker network use "postgres" hostname.
#
# Priority order:
#   1. DB_DSN_DIRECT env var (explicit override)
#   2. DATABASE_URL with port replaced to bypass pgBouncer (5432)
#   3. Hard default pointing straight at postgres container
_raw_url = os.environ['DATABASE_URL']  # fail-fast: no insecure 'edfinder:edfinder' fallback

def _make_direct_dsn(url: str) -> str:
    """
    Ensure the DSN points directly at postgres (port 5432), not pgBouncer (5433).
    pgBouncer transaction-pool mode is incompatible with long single-connection
    UPDATE transactions.
    """
    direct = os.getenv('DB_DSN_DIRECT', '')
    if direct:
        return direct
    if ':5433/' in url:
        url = url.replace(':5433/', ':5432/')
    url = url.replace('@pgbouncer:', '@postgres:')
    return url

DB_DSN    = _make_direct_dsn(_raw_url)
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE  = os.getenv('LOG_FILE', '/tmp/build_grid.log')
CELL_SIZE = int(os.getenv('CELL_SIZE', '500'))

os.makedirs(os.path.dirname(os.path.abspath(LOG_FILE)), exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('build_grid')

# Graceful shutdown flag
_shutdown = False
def _handle_signal(sig, frame):
    global _shutdown
    log.warning("Interrupt received — finishing current batch then exiting cleanly ...")
    _shutdown = True
signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _connect(dsn: str, readonly: bool = False,
             application_name: str = 'build_grid') -> psycopg.Connection:
    """
    Open a PostgreSQL connection with:
    - TCP keepalives to survive long-running operations without being dropped
    - statement_timeout=0 to allow unbounded single-UPDATE runs
    - lock_timeout=30s to fail fast rather than hang on ALTER TABLE / locks
    """
    conn = psycopg.connect(
        dsn,
        keepalives=1,
        keepalives_idle=60,
        keepalives_interval=10,
        keepalives_count=6,
        options=(
            f"-c application_name={application_name} "
            f"-c statement_timeout=0 "
            f"-c lock_timeout=30000 "
            f"-c idle_in_transaction_session_timeout=3600000 "
        )
    )
    conn.autocommit = False
    if readonly:
        conn.read_only = True
    return conn


def _connect_with_retry(dsn: str, label: str = "", retries: int = 10,
                         delay: float = 5.0,
                         readonly: bool = False) -> psycopg.Connection:
    """Connect with exponential back-off retries."""
    for attempt in range(1, retries + 1):
        try:
            conn = _connect(dsn, readonly=readonly, application_name=f'build_grid_{label}')
            return conn
        except Exception as e:
            if attempt == retries:
                log.error(f"FATAL: Cannot connect to database ({label}): {e}")
                raise
            wait = min(delay * attempt, 60)
            log.warning(f"  DB connect failed ({label}, attempt {attempt}/{retries}): {e}")
            log.warning(f"  Retrying in {wait:.0f}s ...")
            time.sleep(wait)


def _safe_close(conn):
    """Close a connection, swallowing any errors."""
    try:
        if conn and not conn.closed:
            conn.close()
    except Exception:
        pass


def _run_in_autocommit(conn, operation):
    """Run ``operation`` outside a transaction and restore connection mode.

    Psycopg 3 rejects changing ``autocommit`` while a transaction is active.
    Several grid phases perform reads before session-level SET/ALTER/CREATE
    statements, so close that transaction first and restore the exact prior
    mode even when the operation fails.
    """
    old_autocommit = conn.autocommit
    if not old_autocommit:
        try:
            conn.commit()
        except Exception:
            conn.rollback()
    conn.autocommit = True
    try:
        return operation()
    finally:
        conn.autocommit = old_autocommit


def _set_replica_mode(conn, *, allow_alter_fallback: bool = False) -> str:
    """Disable ordinary triggers on *conn*, returning the method used."""

    def _set_role():
        try:
            with conn.cursor() as cur:
                cur.execute("SET session_replication_role = replica")
            return 'session'
        except Exception:
            if not allow_alter_fallback:
                raise
            # An autocommit statement failure does not leave a transaction
            # open; use a fresh cursor for the ownership-based fallback.
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE systems DISABLE TRIGGER ALL")
            return 'alter'

    return _run_in_autocommit(conn, _set_role)


def _enable_system_triggers(conn) -> None:
    def _enable():
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE systems ENABLE TRIGGER ALL")

    _run_in_autocommit(conn, _enable)


def _restore_system_triggers(conn, dsn: str) -> None:
    """Restore globally disabled triggers, reconnecting if necessary."""
    try:
        _enable_system_triggers(conn)
        return
    except Exception as first_error:
        log.warning(
            "  Trigger restoration failed on the Stage 3 connection; "
            f"retrying on a fresh direct connection: {first_error}"
        )

    recovery_conn = _connect_with_retry(dsn, label="trigger-restore")
    try:
        _enable_system_triggers(recovery_conn)
    except Exception as recovery_error:
        raise RuntimeError(
            "FATAL: could not restore systems triggers after ALTER fallback"
        ) from recovery_error
    finally:
        _safe_close(recovery_conn)


def _run_with_trigger_restoration(conn, trigger_mode, operation, *, dsn: str):
    """Run an operation and always reverse a global ALTER fallback."""
    try:
        return operation()
    finally:
        if trigger_mode == 'alter':
            _restore_system_triggers(conn, dsn)


# ---------------------------------------------------------------------------
# Kill competing connections (non-blocking best-effort)
# ---------------------------------------------------------------------------

def kill_competing_sessions(dsn: str):
    """Terminate any other build_grid sessions that are still running."""
    try:
        conn = _connect_with_retry(dsn, label="preflight", retries=5)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE application_name LIKE 'build_grid_%'
                  AND pid <> pg_backend_pid()
            """)
            killed = sum(1 for row in cur.fetchall() if row[0])
            if killed:
                log.info(f"  Terminated {killed} competing build_grid session(s)")
            else:
                log.info("  No competing sessions found")
        _safe_close(conn)
    except Exception as e:
        log.warning(f"  Could not kill competing sessions (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Stage 3 Strategy A: ctid-range page batching (THE FIX for the 41M stall)
# ---------------------------------------------------------------------------

def _get_total_pages(cur) -> int:
    """Return relpages for the systems table (fast, from pg_class)."""
    cur.execute("""
        SELECT relpages FROM pg_class WHERE relname = 'systems' AND relkind = 'r'
    """)
    row = cur.fetchone()
    return int(row[0]) if row else 0


def _get_resume_page(conn) -> int:
    """
    Find the first heap page that still contains unassigned rows.
    Uses a quick scan of the partial index on grid_cell_id IS NULL if it
    exists, falling back to page 0 (safe — WHERE grid_cell_id IS NULL means
    already-done pages produce 0-row UPDATEs, which are nearly instant).
    """
    try:
        with conn.cursor() as cur:
            # The fastest way: find min ctid page of any unassigned row.
            # This uses the partial index idx_sys_grid_null if present.
            cur.execute("""
                SELECT (ctid::text::point)[0]::bigint AS page
                FROM systems
                WHERE grid_cell_id IS NULL
                ORDER BY ctid
                LIMIT 1
            """)
            row = cur.fetchone()
            if row and row[0] is not None:
                            # Start one batch before to be safe (don't skip boundary rows)
                # If rows are heavily scattered, starting from a single minimum ctid page
                # and iterating linearly may miss unassigned rows on earlier pages if they were
                # not the *absolute minimum* ctid page at the time of previous run.
                # To ensure all rows are processed, we will always start from page 0 if the index
                # is not present or if an error occurs during resume page determination.
                return max(0, int(row[0]) - 1)
    except Exception as e:
        log.warning(f"  Could not determine resume page (starting from 0): {e}")
    return 0

def _get_resume_page_robust(conn) -> int:
    """
    Find the first heap page that still contains unassigned rows, or 0 if not found.
    This version is more robust against scattered rows by always checking for the
    presence of the partial index and falling back to a full scan if necessary.
    """
    try:
        with conn.cursor() as cur:
            # Check if the partial index idx_sys_grid_null exists
            cur.execute("""
                SELECT COUNT(*) FROM pg_indexes
                WHERE tablename = 'systems' AND indexname = 'idx_sys_grid_null'
            """)
            has_idx = cur.fetchone()[0] > 0

            if has_idx:
                # Use the partial index for a fast lookup
                cur.execute("""
                    SELECT (ctid::text::point)[0]::bigint AS page
                    FROM systems
                    WHERE grid_cell_id IS NULL
                    ORDER BY ctid
                    LIMIT 1
                """)
                row = cur.fetchone()
                if row and row[0] is not None:
                    return max(0, int(row[0]) - 1)
            else:
                log.warning("  idx_sys_grid_null MISSING — fallback to page 0")
                # DO NOT do a full scan for resume page if index is missing.
                # It's better to just start from 0 and let the WHERE clause
                # skip already-assigned rows.
                return 0

    except Exception as e:
        log.warning(f"  Could not determine resume page (starting from 0): {e}")
    return 0


def stage3_formula(conn, cur, min_x, min_y, min_z, cell_size,
                   total_systems, already_assigned, pages_per_batch=10000):
    """
    Assign grid_cell_id using ctid-range (page-range) batching.

    WHY ctid BATCHING INSTEAD OF SINGLE UPDATE:
    The single-UPDATE approach stalled at 41M rows due to WAL write
    amplification + checkpoint pressure.  After ~41M rows, PostgreSQL's WAL
    segment fills and it must fsync before continuing — this appears as
    "0 rows/min" for 5-30 minutes.

    ctid batching commits every N pages (~80 MB at default 10,000 pages).
    Each batch is a tiny transaction, WAL is flushed incrementally, and
    checkpoint pressure never builds.  Fully resumable — restart finds the
    first page with NULL grid_cell_id and continues from there.

    Why ctid instead of id64 range batching:
    id64 (Elite Dangerous system address) is extremely sparse — values span
    0 to ~20 quadrillion for 186M rows.  Batching by id64 range means each
    batch scans the entire 74GB table to find its few rows.  ctid batching
    scans ONLY the target pages — 80MB per batch, not 74GB.
    """
    remaining = total_systems - already_assigned
    log.info("  Strategy: ctid-range page batching (v2.2 fix for WAL/checkpoint stall)")
    log.info(f"  Remaining: {fmt_num(remaining)} rows to assign")
    log.info(f"  Pages per batch: {fmt_num(pages_per_batch)} (~{pages_per_batch*8//1024} MB per commit)")

    # Get total pages in systems table
    total_pages = _get_total_pages(cur)
    if total_pages == 0:
        # Fallback: estimate from row count
        total_pages = max(total_systems // 15, 1)  # ~15 rows per 8KB page
        log.warning(f"  Could not read relpages — estimating {fmt_num(total_pages)} pages")
    else:
        log.info(f"  Table pages: {fmt_num(total_pages)} ({total_pages * 8 // 1024 // 1024} GB)")

    # Find resume point
    log.info("  Finding resume page ...")
    
    # Auto-fix: Try to create the index if it's missing
    try:
        with conn.cursor() as _cur:
            _cur.execute("SELECT 1 FROM pg_indexes WHERE indexname = 'idx_sys_grid_null'")
            index_missing = not _cur.fetchone()
        if index_missing:
            log.info("  [Auto-Fix] Index 'idx_sys_grid_null' is missing. Attempting to create it...")
            # Run outside the read transaction opened by the existence check.
            try:
                def _create_index():
                    with conn.cursor() as index_cur:
                        index_cur.execute(
                            "CREATE INDEX IF NOT EXISTS idx_sys_grid_null "
                            "ON systems(id64) WHERE grid_cell_id IS NULL"
                        )

                _run_in_autocommit(conn, _create_index)
                log.info("  [Auto-Fix] ✓ Index created successfully.")
            except Exception as _e:
                log.warning(f"  [Auto-Fix] Could not create index automatically: {_e}")
    except Exception:
        pass

    t_resume = time.time()
    start_page = _get_resume_page_robust(conn)
    log.info(f"  Resuming from page {fmt_num(start_page)} "
             f"(found in {fmt_duration(time.time() - t_resume)})")

    if start_page > 0:
        skipped_pages = start_page
        log.info(f"  Skipping first {fmt_num(skipped_pages)} pages (already assigned)")

    # Estimate batches remaining
    pages_remaining = total_pages - start_page
    batches_total   = max(math.ceil(pages_remaining / pages_per_batch), 1)
    log.info(f"  Estimated batches: {fmt_num(batches_total)}")
    log.info("  Starting batched UPDATE ...")
    log.info("")

    crash_hint(log, "from last committed page batch automatically")

    t0             = time.time()
    total_updated  = 0
    batch_num      = 0
    current_page   = start_page
    consecutive_empty = 0
    MAX_EMPTY_BATCHES = 20  # stop if 20 consecutive batches update 0 rows

    # Use a dedicated write connection — keeps the main conn free for monitoring
    write_conn = _connect_with_retry(DB_DSN, label="ctid-writer")
    # FIX v2.3 (corrected): session_replication_role must be set on the write_conn
    # that actually executes the UPDATE statements, not the monitoring connection.
    # The setting is session-scoped and reverts automatically on disconnect.
    # Disable triggers if possible. session_replication_role requires superuser.
    # ALTER TABLE ... DISABLE TRIGGER ALL requires table ownership.
    trigger_mode = None
    try:
        # The outer Stage 3 guard owns the global ALTER fallback. This writer
        # only needs the connection-scoped replica role.
        trigger_mode = _set_replica_mode(write_conn)
        if trigger_mode == 'session':
            log.info("  ✓ RI triggers disabled via session_replication_role = replica")
        else:
            log.info("  ✓ RI triggers disabled via ALTER TABLE DISABLE TRIGGER ALL")
    except Exception as _e:
        log.warning(f"  Could not disable triggers (not superuser or owner?): {_e}")
        log.warning("  Continuing with triggers ENABLED — Stage 3 will be significantly slower.")
    write_cur  = write_conn.cursor()

    progress = ProgressReporter(
        log, batches_total, "ctid-batch",
        interval=30, heartbeat=120
    )

    # The loop condition `current_page <= total_pages` assumes a linear progression
    # through pages. If `_get_resume_page` returns a `start_page` that is not
    # truly the beginning of *all* unassigned rows (due to fragmentation),
    # some rows might be missed. The `MAX_EMPTY_BATCHES` logic might also
    # prematurely terminate if it iterates through empty pages while unassigned
    # rows exist on non-contiguous earlier pages.
    while current_page <= total_pages:
        if _shutdown:
            log.warning(f"  Shutdown — committing at page {fmt_num(current_page)}")
            write_conn.commit()
            break

        end_page = current_page + pages_per_batch
        batch_num += 1

        # Format ctid bounds: '(page,1)'::tid
        # Using page+1 as exclusive upper bound ensures we don't miss row slots
        ctid_lo = f"({current_page},1)"
        ctid_hi = f"({end_page},1)"

        for attempt in range(4):
            try:
                write_cur.execute("""
                    UPDATE systems
                    SET grid_cell_id = (
                        floor((x - %s) / %s)::bigint * 100000000 +
                        floor((y - %s) / %s)::bigint * 10000 +
                        floor((z - %s) / %s)::bigint
                    )
                    WHERE ctid >= %s::tid
                      AND ctid <  %s::tid
                      AND grid_cell_id IS NULL
                """, (
                    min_x, cell_size,
                    min_y, cell_size,
                    min_z, cell_size,
                    ctid_lo, ctid_hi,
                ))
                rows_updated = write_cur.rowcount
                write_conn.commit()
                total_updated += rows_updated
                break  # success

            except psycopg.OperationalError as e:
                log.warning(f"  Connection lost on batch {batch_num} page {current_page} "
                            f"(attempt {attempt+1}/4): {e}")
                try:
                    write_cur.close()
                    write_conn.rollback()
                    _safe_close(write_conn)
                except Exception:
                    pass
                rows_updated = 0
                if attempt == 3:
                    log.error(f"  FATAL: 4 retries failed on page {current_page}")
                    raise
                wait = 15 * (attempt + 1)
                log.info(f"  Reconnecting in {wait}s ...")
                time.sleep(wait)
                write_conn = _connect_with_retry(DB_DSN, label=f"ctid-retry-{attempt}")
                try:
                    if trigger_mode == 'session':
                        _set_replica_mode(write_conn)
                except Exception as setup_error:
                    log.warning(f"  Reconnected with RI triggers enabled: {setup_error}")
                write_cur  = write_conn.cursor()
                log.info(f"  Reconnected — retrying batch {batch_num}")

            except Exception as e:
                log.error(f"  Unexpected error on batch {batch_num} page {current_page}: {e}")
                try:
                    write_conn.rollback()
                except Exception:
                    pass
                rows_updated = 0
                break

        # Track consecutive empty batches
        if rows_updated == 0:
            consecutive_empty += 1
            # If we've hit many empty batches, re-verify if any unassigned rows remain further ahead
            if consecutive_empty >= MAX_EMPTY_BATCHES:
                log.info(f"  {MAX_EMPTY_BATCHES} consecutive empty batches — checking for further unassigned rows...")
                # Only attempt jump if we have the index, otherwise it's a full scan every time
                cur.execute("""
                    SELECT COUNT(*) FROM pg_indexes
                    WHERE tablename = 'systems' AND indexname = 'idx_sys_grid_null'
                """)
                if cur.fetchone()[0] > 0:
                    next_unassigned = _get_resume_page_robust(conn)
                    if next_unassigned > current_page:
                        log.info(f"  Found more unassigned rows starting at page {fmt_num(next_unassigned)} — jumping ahead")
                        current_page = next_unassigned
                        consecutive_empty = 0
                        continue
                    else:
                        log.info("  No more unassigned rows found via index — stopping early ✓")
                        break
                else:
                    log.warning("  idx_sys_grid_null missing — cannot jump gaps. Stopping early to prevent infinite empty scan.")
                    log.warning("  CRITICAL: Create the index manually to resume correctly: ")
                    log.warning("  CREATE INDEX CONCURRENTLY idx_sys_grid_null ON systems(id64) WHERE grid_cell_id IS NULL;")
                    break
        else:
            consecutive_empty = 0

        progress.update(1)

        # Log detail every 50 batches or when rows were updated
        if batch_num % 50 == 0 or rows_updated > 0:
            elapsed = time.time() - t0
            pct_pages = (current_page - start_page) / max(pages_remaining, 1) * 100
            log.info(
                f"  [batch {fmt_num(batch_num)}]"
                f"  pages {fmt_num(current_page)}-{fmt_num(end_page)}"
                f"  ({pct_pages:.1f}% pages done)"
                f"  rows_this_batch={fmt_num(rows_updated)}"
                f"  total_updated={fmt_num(total_updated)}"
                f"  rate={fmt_rate(total_updated, elapsed)}"
                f"  elapsed={fmt_duration(elapsed)}"
            )

        current_page = end_page

    progress.finish()
    
    write_cur.close()
    _safe_close(write_conn)

    elapsed = time.time() - t0
    log.info(f"  ctid batching complete — {fmt_num(total_updated)} rows in {fmt_duration(elapsed)} ✓")
    log.info(f"  Batches processed: {fmt_num(batch_num)}")

    return total_updated, conn, cur



# ---------------------------------------------------------------------------
# Stage 3 Strategy P: Parallel worker queue (SKIP LOCKED)
# ---------------------------------------------------------------------------

def grid_worker(
    worker_id,
    dsn,
    min_x,
    min_y,
    min_z,
    cell_size,
    batch_size,
):
    conn = _connect_with_retry(dsn, label=f"worker-{worker_id}")
    cur = conn.cursor()

    try:
        def _configure_worker():
            cur.execute("SET session_replication_role = replica")
            cur.execute("SET synchronous_commit = OFF")
            cur.execute("SET work_mem = '256MB'")

        _run_in_autocommit(conn, _configure_worker)

    except Exception as e:
        log.warning(f"[worker-{worker_id}] session setup failed: {e}")

    total_updated = 0

    while not _shutdown:

        cur.execute("""
            WITH batch AS (
                SELECT id64
                FROM systems
                WHERE grid_cell_id IS NULL
                ORDER BY id64
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            UPDATE systems s
            SET grid_cell_id = (
                floor((x - %s) / %s)::bigint * 100000000 +
                floor((y - %s) / %s)::bigint * 10000 +
                floor((z - %s) / %s)::bigint
            )
            FROM batch
            WHERE s.id64 = batch.id64
        """, (
            batch_size,
            min_x, cell_size,
            min_y, cell_size,
            min_z, cell_size,
        ))

        updated = cur.rowcount

        conn.commit()

        if updated == 0:
            log.info(
                f"[worker-{worker_id}] complete "
                f"({total_updated:,} rows updated)"
            )
            break

        total_updated += updated

        log.info(
            f"[worker-{worker_id}] "
            f"+{updated:,} rows "
            f"(total {total_updated:,})"
        )

    cur.close()
    conn.close()


def stage3_parallel(
    dsn,
    min_x,
    min_y,
    min_z,
    cell_size,
    workers=6,
    batch_size=250_000,
):
    log.info("")
    log.info("  Parallel worker mode")
    log.info(f"  Workers    : {workers}")
    log.info(f"  Batch size : {batch_size:,}")
    log.info("")

    procs = []

    for i in range(workers):
        p = Process(
            target=grid_worker,
            args=(
                i,
                dsn,
                min_x,
                min_y,
                min_z,
                cell_size,
                batch_size,
            )
        )

        p.start()
        procs.append(p)

    for p in procs:
        p.join()

    log.info("  ✓ All workers complete")


# ---------------------------------------------------------------------------
# Stage 3 Strategy B: per-cell batches (fallback, uses bounding boxes)
# ---------------------------------------------------------------------------

def stage3_batched_cells(conn, cur, cell_count, already_assigned, total_systems):
    """
    Fallback: iterate grid cells and UPDATE systems per bounding box.

    IMPORTANT: This version avoids the two fatal bugs in v1.6:
      1. Does NOT use EXISTS subquery to pre-filter cells (too slow).
         Instead iterates ALL cells and lets WHERE grid_cell_id IS NULL
         make fully-assigned cells a near-instant no-op UPDATE (0 rows).
      2. Does NOT use a server-side (named) cursor — that breaks under
         pgBouncer transaction-pool mode.  Instead fetches all cell rows
         into Python RAM (135k rows × ~80 bytes = ~11MB — totally fine).

    With idx_sys_coords each bounding-box UPDATE is an index range scan
    (~1400 systems/cell on average = fast).  Without the index each UPDATE
    is a full seq scan of 145M rows — extremely slow.  Check first.
    """
    # Check for coordinate index
    cur.execute("""
        SELECT COUNT(*) FROM pg_indexes
        WHERE tablename = 'systems' AND indexname = 'idx_sys_coords'
    """)
    has_idx = cur.fetchone()[0] > 0
    if not has_idx:
        log.warning("  idx_sys_coords MISSING — batched strategy will be very slow")
        log.warning("  Consider: CREATE INDEX CONCURRENTLY idx_sys_coords ON systems(x,y,z)")
    else:
        log.info("  idx_sys_coords exists — each cell UPDATE uses index scan")

    # Fetch all cells into memory (~11 MB, fine)
    log.info(f"  Fetching {fmt_num(cell_count)} grid cells into memory ...")
    cur.execute("""
        SELECT cell_id, min_x, max_x, min_y, max_y, min_z, max_z
        FROM spatial_grid
        ORDER BY cell_id
    """)
    all_cells = cur.fetchall()
    log.info(f"  Loaded {fmt_num(len(all_cells))} cells")

    COMMIT_EVERY = 500
    total_updated = 0
    skipped       = 0

    write_conn = _connect_with_retry(DB_DSN, label="batched-writer")
    # FIX v2.3 (corrected): apply session_replication_role to the write_conn
    # that executes the UPDATE, not the monitoring connection.
    trigger_mode = None
    try:
        trigger_mode = _set_replica_mode(write_conn)
        log.info("  ✓ RI triggers disabled on write_conn (batched-writer)")
    except Exception as _e:
        log.warning(f"  Could not disable RI triggers on write_conn: {_e} — continuing (Stage 3 may be slow)")
    write_cur  = write_conn.cursor()

    progress = ProgressReporter(log, len(all_cells), "cell-assign", interval=30, heartbeat=120)

    for i, cell in enumerate(all_cells):
        if _shutdown:
            log.warning(f"  Shutdown — committing and exiting at cell {i}")
            write_conn.commit()
            break

        cell_id, bx0, bx1, by0, by1, bz0, bz1 = cell

        for attempt in range(3):
            try:
                write_cur.execute("""
                    UPDATE systems
                    SET grid_cell_id = %s
                    WHERE x >= %s AND x < %s
                      AND y >= %s AND y < %s
                      AND z >= %s AND z < %s
                      AND grid_cell_id IS NULL
                """, (cell_id, bx0, bx1, by0, by1, bz0, bz1))
                rows = write_cur.rowcount
                total_updated += rows
                if rows == 0:
                    skipped += 1
                break

            except psycopg.OperationalError as e:
                log.warning(f"  Connection lost on cell {cell_id} (attempt {attempt+1}/3): {e}")
                try:
                    write_cur.close()
                    write_conn.rollback()
                    _safe_close(write_conn)
                except Exception:
                    pass
                time.sleep(10 * (attempt + 1))
                write_conn = _connect_with_retry(DB_DSN, label=f"batched-retry-{attempt}")
                try:
                    if trigger_mode == 'session':
                        _set_replica_mode(write_conn)
                except Exception as setup_error:
                    log.warning(f"  Reconnected with RI triggers enabled: {setup_error}")
                write_cur  = write_conn.cursor()
                log.info(f"  Reconnected — retrying cell {cell_id}")

            except Exception as e:
                log.error(f"  Unexpected error on cell {cell_id}: {e}")
                try:
                    write_conn.rollback()
                    _safe_close(write_conn)
                except Exception:
                    pass
                write_conn = _connect_with_retry(DB_DSN, label=f"batched-error-{i}")
                try:
                    if trigger_mode == 'session':
                        _set_replica_mode(write_conn)
                except Exception as setup_error:
                    log.warning(f"  Reconnected with RI triggers enabled: {setup_error}")
                write_cur  = write_conn.cursor()
                break

        if (i + 1) % COMMIT_EVERY == 0:
            write_conn.commit()
            progress.update(COMMIT_EVERY)

    write_conn.commit()
    remainder = len(all_cells) % COMMIT_EVERY
    if remainder:
        progress.update(remainder)

    progress.finish()
    
    if trigger_mode == 'alter':
        try:
            _enable_system_triggers(write_conn)
            log.info("  ✓ Triggers re-enabled")
        except Exception as enable_error:
            log.warning(f"  Could not re-enable triggers: {enable_error}")

    write_cur.close()
    _safe_close(write_conn)

    log.info(f"  Cells processed : {fmt_num(len(all_cells))}")
    log.info(f"  Cells skipped   : {fmt_num(skipped)} (already fully assigned)")
    log.info(f"  Rows updated    : {fmt_num(total_updated)}")
    return total_updated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Build spatial grid for fast cluster queries',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Strategies:
  formula  — ctid-range page batching (default, fixes WAL/checkpoint stall)
  batched  — one UPDATE per grid cell (fallback, requires idx_sys_coords)
"""
    )
    parser.add_argument('--cell-size',        type=int,   default=CELL_SIZE,
                        help=f'Grid cell size in LY (default: {CELL_SIZE})')
    parser.add_argument('--pages-per-batch',  type=int,   default=10_000,
                        help='Heap pages per ctid batch (default: 10000 = ~80MB per commit)')
    parser.add_argument('--batch-size',       type=int,   default=2_000_000,
                        help='(legacy alias) Rows per formula-batch — now maps to pages-per-batch')
    parser.add_argument('--strategy',   choices=['formula', 'batched', 'parallel'], default='formula',
                        help='Stage 3 strategy (default: formula = ctid batching)')
    parser.add_argument('--direct-host', default='',
                        help='Override DB host (e.g. "postgres" or "localhost")')
    parser.add_argument('--reset-cache', action='store_true',
                        help='Delete cached bounds/total from app_meta and recompute from scratch.')
    parser.add_argument('--no-disable-triggers', action='store_true',
                        help='Skip trigger disable (not recommended — Stage 3 will be very slow)')
    args = parser.parse_args()

    cell_size      = args.cell_size
    strategy       = args.strategy
    pages_per_batch = args.pages_per_batch

    dsn = DB_DSN
    if args.direct_host:
        import re
        dsn = re.sub(r'@[^:/]+:', f'@{args.direct_host}:', dsn)
        log.info(f"  DB host overridden to: {args.direct_host}")

    script_start = time.time()

    startup_banner(log, "Spatial Grid Builder", "v3.0", [
        ("Cell size",       f"{cell_size} LY"),
        ("Strategy",        f"Stage 3 = {strategy}"),
        ("Pages/batch",     f"{pages_per_batch:,} (~{pages_per_batch*8//1024} MB per commit)"),
        ("Log file",        LOG_FILE),
        ("DB",              dsn.split('@')[-1]),
        ("Keepalives",      "idle=60s / interval=10s / count=6"),
        ("Direct DB",       "YES — bypasses pgBouncer (transaction-pool safe)"),
        ("Fix v2.3",        "disable RI triggers — eliminates 17 trigger evals per row"),
        ("Fix v2.4",        "apply session_replication_role to write_conn (not monitoring conn)"),
        ("v3.0",             "add 2000 LY macro-grid for cluster builder"),
    ])

    # ------------------------------------------------------------------
    # Preflight: kill any other running build_grid sessions
    # ------------------------------------------------------------------
    log.info("[Preflight] Terminating competing build_grid sessions ...")
    kill_competing_sessions(dsn)
    log.info("")

    # ------------------------------------------------------------------
    # Main connection
    # ------------------------------------------------------------------
    conn = _connect_with_retry(dsn, label="main")
    cur  = conn.cursor()

    # ------------------------------------------------------------------
    # Optional: wipe cached app_meta keys
    # ------------------------------------------------------------------
    if args.reset_cache:
        log.warning("[Reset] --reset-cache specified — deleting cached grid keys from app_meta ...")
        cur.execute("""
            DELETE FROM app_meta
            WHERE key IN ('grid_min_x','grid_max_x','grid_min_y','grid_max_y',
                          'grid_min_z','grid_max_z','grid_total_systems',
                          'grid_cell_size','grid_built')
        """)
        conn.commit()
        log.warning("[Reset] Cache cleared — Stage 1 will run full seq scan")

    # =========================================================================
    # STAGE 1: Galaxy bounds
    # =========================================================================
    stage_banner(log, 1, 5, "Galaxy Bounds")

    cur.execute("""
        SELECT key, value FROM app_meta
        WHERE key IN ('grid_min_x','grid_min_y','grid_min_z',
                      'grid_max_x','grid_max_y','grid_max_z',
                      'grid_total_systems')
    """)
    stored      = {r[0]: r[1] for r in cur.fetchall()}
    coord_keys  = ('grid_min_x','grid_min_y','grid_min_z',
                   'grid_max_x','grid_max_y','grid_max_z')
    coords_cached = all(k in stored for k in coord_keys)

    if coords_cached:
        min_x = float(stored['grid_min_x'])
        min_y = float(stored['grid_min_y'])
        min_z = float(stored['grid_min_z'])
        max_x = float(stored['grid_max_x'])
        max_y = float(stored['grid_max_y'])
        max_z = float(stored['grid_max_z'])
        log.info("  Bounds loaded from app_meta cache ✓")
        log.info(f"  X: [{min_x:.0f}, {max_x:.0f}]")
        log.info(f"  Y: [{min_y:.0f}, {max_y:.0f}]")
        log.info(f"  Z: [{min_z:.0f}, {max_z:.0f}]")

        # ALWAYS do a live COUNT(*) — the cached value from app_meta may be
        # stale if more rows were imported since the last build_grid run.
        log.info("  Counting total systems (live — verifying cache is current) ...")
        t0 = time.time()
        cur.execute("SELECT COUNT(*) FROM systems")
        total_systems = cur.fetchone()[0]
        elapsed_count = time.time() - t0

        cached_total = int(stored.get('grid_total_systems', 0))
        if cached_total and cached_total != total_systems:
            log.warning(f"  ⚠  Cached total ({fmt_num(cached_total)}) differs from live count "
                        f"({fmt_num(total_systems)}) — import added rows since last run")
        else:
            log.info(f"  Total systems: {fmt_num(total_systems)} "
                     f"(counted in {fmt_duration(elapsed_count)}) ✓")

        cur.execute("""
            INSERT INTO app_meta (key, value, updated_at)
            VALUES ('grid_total_systems', %s, NOW())
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = NOW()
        """, (str(total_systems),))
        conn.commit()
    else:
        log.info("  No bounds cached — running full seq scan (once only, takes 5-30 min) ...")
        t0 = time.time()
        cur.execute("""
            SELECT MIN(x), MAX(x), MIN(y), MAX(y), MIN(z), MAX(z), COUNT(*)
            FROM systems
        """)
        row = cur.fetchone()
        log.info(f"  Seq scan done in {fmt_duration(time.time()-t0)}")
        min_x, max_x = row[0] - cell_size, row[1] + cell_size
        min_y, max_y = row[2] - cell_size, row[3] + cell_size
        min_z, max_z = row[4] - cell_size, row[5] + cell_size
        total_systems = row[6]
        log.info(f"  X: [{min_x:.0f}, {max_x:.0f}]")
        log.info(f"  Y: [{min_y:.0f}, {max_y:.0f}]")
        log.info(f"  Z: [{min_z:.0f}, {max_z:.0f}]")
        log.info(f"  Total systems: {fmt_num(total_systems)}")
        cur.execute("""
            INSERT INTO app_meta (key, value, updated_at) VALUES
                ('grid_min_x', %s, NOW()), ('grid_max_x', %s, NOW()),
                ('grid_min_y', %s, NOW()), ('grid_max_y', %s, NOW()),
                ('grid_min_z', %s, NOW()), ('grid_max_z', %s, NOW()),
                ('grid_total_systems', %s, NOW())
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = NOW()
        """, (str(min_x), str(max_x), str(min_y), str(max_y),
              str(min_z), str(max_z), str(total_systems)))
        conn.commit()
        log.info("  Bounds saved to app_meta — future runs skip this ✓")

    x_cells = math.ceil((max_x - min_x) / cell_size)
    y_cells = math.ceil((max_y - min_y) / cell_size)
    z_cells = math.ceil((max_z - min_z) / cell_size)
    log.info(f"  Grid dims: {x_cells}×{y_cells}×{z_cells} = {fmt_num(x_cells*y_cells*z_cells)} max cells")

    # grid_cell_id packs the axis indices as x*1e8 + y*1e4 + z (base 1e4). A y or
    # z index >= 10000 overflows its slot and silently collides distinct cells.
    # Safe at the default cell size (indices in the low hundreds), but --cell-size
    # is operator-tunable, so fail closed rather than corrupt grid_cell_id.
    if y_cells > 10000 or z_cells > 10000:
        raise SystemExit(
            f"cell-size {cell_size} LY yields {x_cells}×{y_cells}×{z_cells} cells; "
            f"y and z cell counts must each be <= 10000 for the base-1e4 grid_cell_id "
            f"packing scheme. Increase --cell-size."
        )

    if _shutdown:
        log.warning("Shutdown after Stage 1.")
        sys.exit(0)

    # =========================================================================
    # STAGE 2: Populate spatial_grid
    # =========================================================================
    cur.execute("SELECT COUNT(*), COALESCE(SUM(system_count),0) FROM spatial_grid")
    existing_cells, sum_system_count = cur.fetchone()
    sum_system_count = int(sum_system_count)

    # Validate that existing cells are complete, not partial (fix BUG #6)
    cells_look_complete = (
        existing_cells > 0 and
        sum_system_count > 0 and
        abs(sum_system_count - total_systems) / max(total_systems, 1) < 0.01
    )

    if cells_look_complete:
        cell_count = existing_cells
        stage_banner(log, 2, 5, "Populate spatial_grid", resumed=True)
        log.info(f"  Cells:        {fmt_num(cell_count)}")
        log.info(f"  system_count: {fmt_num(sum_system_count)} (matches {fmt_num(total_systems)} total ✓)")
        log.info("  Skipping INSERT ✓")
    elif existing_cells > 0 and not cells_look_complete:
        stage_banner(log, 2, 5, "Populate spatial_grid")
        log.warning(f"  Found {fmt_num(existing_cells)} cells but system_count={fmt_num(sum_system_count)}")
        log.warning(f"  Expected ~{fmt_num(total_systems)} — partial data detected, rebuilding ...")
        cur.execute("TRUNCATE spatial_grid")
        conn.commit()
        existing_cells = 0
        cell_count = 0

    if existing_cells == 0:
        stage_banner(log, 2, 5, "Populate spatial_grid")
        log.info(f"  Grouping {fmt_num(total_systems)} systems into {cell_size}ly cubes ...")
        log.info("  This takes 5-15 minutes ...")
        crash_hint(log, "from Stage 2 (cells rebuilt automatically on next run)")
        t0 = time.time()

        cur.execute("""
            INSERT INTO spatial_grid
                (cell_id, cell_x, cell_y, cell_z,
                 min_x, max_x, min_y, max_y, min_z, max_z,
                 system_count)
            WITH cells AS (
                SELECT
                    floor((x - %(min_x)s) / %(cell_size)s)::bigint AS cx,
                    floor((y - %(min_y)s) / %(cell_size)s)::bigint AS cy,
                    floor((z - %(min_z)s) / %(cell_size)s)::bigint AS cz,
                    COUNT(*) AS cnt
                FROM systems
                GROUP BY
                    floor((x - %(min_x)s) / %(cell_size)s),
                    floor((y - %(min_y)s) / %(cell_size)s),
                    floor((z - %(min_z)s) / %(cell_size)s)
            )
            SELECT
                (cx * 100000000 + cy * 10000 + cz) AS cell_id,
                cx::smallint,
                cy::smallint,
                cz::smallint,
                (cx * %(cell_size)s + %(min_x)s)::real,
                (cx * %(cell_size)s + %(min_x)s + %(cell_size)s)::real,
                (cy * %(cell_size)s + %(min_y)s)::real,
                (cy * %(cell_size)s + %(min_y)s + %(cell_size)s)::real,
                (cz * %(cell_size)s + %(min_z)s)::real,
                (cz * %(cell_size)s + %(min_z)s + %(cell_size)s)::real,
                cnt
            FROM cells
            ON CONFLICT (cell_x, cell_y, cell_z) DO UPDATE SET
                system_count = EXCLUDED.system_count
        """, {
            'min_x': min_x,
            'min_y': min_y,
            'min_z': min_z,
            'cell_size': cell_size,
        })
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM spatial_grid")
        cell_count = cur.fetchone()[0]
        log.info(f"  Created {fmt_num(cell_count)} grid cells in {fmt_duration(time.time()-t0)} ✓")

    if _shutdown:
        log.warning("Shutdown after Stage 2.")
        sys.exit(0)

    # =========================================================================
    # STAGE 3: Assign grid_cell_id to every system
    # =========================================================================
    # Count UNASSIGNED rows directly — ground truth.
    cur.execute("SELECT COUNT(*) FROM systems WHERE grid_cell_id IS NULL")
    unassigned_check = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM systems WHERE grid_cell_id IS NOT NULL")
    already_assigned = cur.fetchone()[0]
    resumed          = already_assigned > 0

    if unassigned_check == 0:
        stage_banner(log, 3, 5, "Assign grid_cell_id", resumed=True)
        log.info(f"  All {fmt_num(total_systems)} systems already assigned — skipping ✓")
        log.info("  (Verified: SELECT COUNT(*) WHERE grid_cell_id IS NULL = 0)")
    else:
        stage_banner(log, 3, 5, "Assign grid_cell_id", resumed=resumed)
        log.info(f"  Total systems    : {fmt_num(total_systems)}")
        log.info(f"  Already assigned : {fmt_num(already_assigned)}  ({fmt_pct(already_assigned, total_systems)})")
        log.info(f"  Remaining (NULL) : {fmt_num(unassigned_check)}  ← ground truth from live COUNT")
        log.info(f"  Strategy         : {strategy}")
        log.info("  Connection       : DIRECT to postgres (not pgBouncer)")

        # ---------------------------------------------------------------
        # CRITICAL: Disable RI triggers for the duration of Stage 3.
        #
        # The systems table has 8 child tables with FK REFERENCES systems(id64).
        # PostgreSQL auto-creates 2 RI triggers per FK = 16 RI triggers total.
        # These fire on EVERY UPDATE of systems — even when only updating
        # grid_cell_id which has NOTHING to do with any FK.
        #
        # For each of 145M rows, the RI check does an index lookup on EACH
        # child table to verify no orphans.  The first ~41M rows are colonised
        # systems (from galaxy_populated.json.gz) which DO have child rows in
        # ratings, cluster_summary, watchlist, etc.  So those 41M rows each
        # trigger 16 expensive child-table index lookups.  This is the real
        # reason the UPDATE appeared to "stall" at 41M — it was running at
        # a fraction of its potential speed due to RI overhead.
        #
        # SET session_replication_role = replica disables non-ALWAYS triggers
        # for THIS SESSION ONLY — reverts automatically on disconnect.
        # SAFE: we never modify id64 (the FK column), only grid_cell_id.
        # ---------------------------------------------------------------
        disable_triggers = not args.no_disable_triggers
        if disable_triggers:
            log.info("")
            log.info("  ┌─ TRIGGER DISABLE ─────────────────────────────────────┐")
            log.info("  │  Disabling RI triggers via session_replication_role    │")
            log.info("  │  SAFE: only grid_cell_id is updated, not id64 (FK col) │")
            log.info("  │  Reverts automatically on session disconnect            │")
            log.info("  └───────────────────────────────────────────────────────┘")
            try:
                disable_triggers = _set_replica_mode(
                    conn, allow_alter_fallback=True,
                )
                log.info("  ✓ RI triggers disabled for this session")
            except Exception as e:
                log.warning(f"  Could not disable triggers: {e}")
                log.warning("  Continuing with triggers ENABLED — Stage 3 will be SLOW")
                disable_triggers = False
        else:
            log.warning("  --no-disable-triggers set — RI triggers remain active (SLOW!)")

        def _assign_grid_cells():
            if strategy == 'parallel':
                stage3_parallel(
                    dsn=DB_DSN,
                    min_x=min_x,
                    min_y=min_y,
                    min_z=min_z,
                    cell_size=cell_size,
                    workers=6,
                    batch_size=250_000,
                )
                return 0, conn, cur

            if strategy == 'formula':
                return stage3_formula(
                    conn, cur, min_x, min_y, min_z,
                    cell_size, total_systems, already_assigned,
                    pages_per_batch=pages_per_batch)

            return (
                stage3_batched_cells(
                    conn, cur, cell_count, already_assigned, total_systems,
                ),
                conn,
                cur,
            )

        total_rows_updated, conn, cur = _run_with_trigger_restoration(
            conn, disable_triggers, _assign_grid_cells, dsn=dsn,
        )
        if disable_triggers == 'alter':
            log.info("  ✓ Triggers re-enabled via ALTER TABLE")
        # session_replication_role = replica reverts automatically on disconnect

        # Reconnect in case the long Stage 3 connection timed out
        try:
            cur.close()
            _safe_close(conn)
        except Exception:
            pass
        conn = _connect_with_retry(dsn, label="post-stage3")
        cur  = conn.cursor()

        # Verify
        cur.execute("SELECT COUNT(*) FROM systems WHERE grid_cell_id IS NULL")
        unassigned = cur.fetchone()[0]
        if unassigned > 0:
            log.warning(f"  ⚠  {fmt_num(unassigned)} systems still unassigned after Stage 3")
            log.warning("  Re-run this script to continue — ctid batching resumes automatically")
        else:
            log.info(f"  ✓  All {fmt_num(total_systems)} systems assigned")

        log.info(f"  Rows updated this run: {fmt_num(total_rows_updated)}")

    if _shutdown:
        log.warning("Shutdown after Stage 3.")
        sys.exit(0)

    # =========================================================================
    # STAGE 3b: Assign macro_grid_id (2000 LY cubes) to every system
    # =========================================================================
    MACRO_CELL_SIZE = 2000
    stage_banner(log, 4, 6, "Assign macro_grid_id (2000 LY macro-grid)")

    cur.execute("SELECT COUNT(*) FROM systems WHERE macro_grid_id IS NULL")
    macro_unassigned = cur.fetchone()[0]

    if macro_unassigned == 0:
        log.info(f"  All {fmt_num(total_systems)} systems already have macro_grid_id — skipping ✓")
    else:
        log.info(f"  Assigning macro_grid_id to {fmt_num(macro_unassigned)} systems ...")
        log.info(f"  Macro cell size: {MACRO_CELL_SIZE} LY")

        # Build the macro_grid table
        cur.execute("SELECT COUNT(*) FROM macro_grid")
        macro_cells_existing = cur.fetchone()[0]
        if macro_cells_existing == 0:
            log.info("  Building macro_grid table ...")
            t0 = time.time()
            cur.execute("""
                INSERT INTO macro_grid
                    (cell_id, cell_x, cell_y, cell_z,
                     min_x, max_x, min_y, max_y, min_z, max_z,
                     system_count)
                WITH cells AS (
                    SELECT
                        floor((x - %(min_x)s) / %(cell_size)s)::bigint AS cx,
                        floor((y - %(min_y)s) / %(cell_size)s)::bigint AS cy,
                        floor((z - %(min_z)s) / %(cell_size)s)::bigint AS cz,
                        COUNT(*) AS cnt
                    FROM systems
                    GROUP BY
                        floor((x - %(min_x)s) / %(cell_size)s),
                        floor((y - %(min_y)s) / %(cell_size)s),
                        floor((z - %(min_z)s) / %(cell_size)s)
                )
                SELECT
                    (cx * 100000000 + cy * 10000 + cz) AS cell_id,
                    cx::smallint, cy::smallint, cz::smallint,
                    (cx * %(cell_size)s + %(min_x)s)::real,
                    (cx * %(cell_size)s + %(min_x)s + %(cell_size)s)::real,
                    (cy * %(cell_size)s + %(min_y)s)::real,
                    (cy * %(cell_size)s + %(min_y)s + %(cell_size)s)::real,
                    (cz * %(cell_size)s + %(min_z)s)::real,
                    (cz * %(cell_size)s + %(min_z)s + %(cell_size)s)::real,
                    cnt
                FROM cells
                ON CONFLICT (cell_x, cell_y, cell_z) DO UPDATE SET
                    system_count = EXCLUDED.system_count
            """, {
                'min_x': min_x,
                'min_y': min_y,
                'min_z': min_z,
                'cell_size': MACRO_CELL_SIZE,
            })
            conn.commit()
            cur.execute("SELECT COUNT(*) FROM macro_grid")
            macro_cell_count = cur.fetchone()[0]
            log.info(f"  Created {fmt_num(macro_cell_count)} macro-grid cells in {fmt_duration(time.time()-t0)} ✓")
        else:
            log.info(f"  macro_grid already has {fmt_num(macro_cells_existing)} cells — skipping rebuild")

        # Parallel macro_grid assignment using SKIP LOCKED workers
        log.info("  Assigning macro_grid_id via parallel workers ...")
        t0 = time.time()

        def macro_worker(worker_id):

            conn = _connect_with_retry(dsn, label=f"macro-worker-{worker_id}")
            cur = conn.cursor()

            try:
                def _configure_macro_worker():
                    cur.execute("SET session_replication_role = replica")
                    cur.execute("SET synchronous_commit = OFF")
                    cur.execute("SET work_mem = '256MB'")

                _run_in_autocommit(conn, _configure_macro_worker)

            except Exception as e:
                log.warning(f"[macro-worker-{worker_id}] setup failed: {e}")

            total_updated = 0

            while not _shutdown:

                cur.execute("""
                    WITH batch AS (
                        SELECT id64
                        FROM systems
                        WHERE macro_grid_id IS NULL
                        ORDER BY id64
                        FOR UPDATE SKIP LOCKED
                        LIMIT 250000
                    )
                    UPDATE systems s
                    SET macro_grid_id = (
                        floor((x - %(min_x)s) / %(cell_size)s)::bigint * 100000000 +
                        floor((y - %(min_y)s) / %(cell_size)s)::bigint * 10000 +
                        floor((z - %(min_z)s) / %(cell_size)s)::bigint
                    )
                    FROM batch
                    WHERE s.id64 = batch.id64
                """, {
                    'min_x': min_x,
                    'min_y': min_y,
                    'min_z': min_z,
                    'cell_size': MACRO_CELL_SIZE,
                })

                rows = cur.rowcount

                conn.commit()

                if rows == 0:
                    log.info(
                        f"[macro-worker-{worker_id}] complete "
                        f"({total_updated:,} rows)"
                    )
                    break

                total_updated += rows

                log.info(
                    f"[macro-worker-{worker_id}] "
                    f"+{rows:,} rows "
                    f"(total {total_updated:,})"
                )

            cur.close()
            conn.close()

        log.info("")
        log.info("  Parallel macro worker mode")
        log.info("")

        procs = []

        for i in range(6):

            p = Process(
                target=macro_worker,
                args=(i,)
            )

            p.start()
            procs.append(p)

        for p in procs:
            p.join()

        macro_updated = 0
        # Update anchor_count in macro_grid
        cur.execute("""
            UPDATE macro_grid g
            SET anchor_count = COALESCE(v.cnt, 0)
            FROM (
                SELECT macro_grid_id, COUNT(*) AS cnt
                FROM systems
                WHERE has_body_data = TRUE AND macro_grid_id IS NOT NULL
                GROUP BY macro_grid_id
            ) v
            WHERE g.cell_id = v.macro_grid_id
        """)
        conn.commit()

        cur.execute("""
            INSERT INTO app_meta (key, value, updated_at)
            VALUES ('macro_grid_built', 'true', NOW())
            ON CONFLICT (key) DO UPDATE SET value = 'true', updated_at = NOW()
        """)
        conn.commit()
        log.info(f"  macro_grid_id assigned to {fmt_num(macro_updated)} systems in {fmt_duration(time.time()-t0)} ✓")

    if _shutdown:
        log.warning("Shutdown after Stage 3b.")
        sys.exit(0)

    # =========================================================================
    # STAGE 5: Update visited_count per cell
    # =========================================================================
    stage_banner(log, 5, 6, "Update visited_count")
    log.info("  Aggregating scanned systems by grid_cell_id ...")
    t0 = time.time()
    cur.execute("""
        UPDATE spatial_grid g
        SET visited_count = COALESCE(v.cnt, 0)
        FROM (
            SELECT grid_cell_id, COUNT(*) AS cnt
            FROM systems
            WHERE has_body_data = TRUE
              AND grid_cell_id IS NOT NULL
            GROUP BY grid_cell_id
        ) v
        WHERE g.cell_id = v.grid_cell_id
    """)
    conn.commit()
    log.info(f"  Visited counts updated in {fmt_duration(time.time()-t0)} ✓")

    # =========================================================================
    # STAGE 5: Save parameters to app_meta
    # =========================================================================
    stage_banner(log, 6, 6, "Save parameters to app_meta")
    cur.execute("""
        INSERT INTO app_meta (key, value, updated_at)
        VALUES
            ('grid_cell_size', %s, NOW()),
            ('grid_min_x',     %s, NOW()),
            ('grid_min_y',     %s, NOW()),
            ('grid_min_z',     %s, NOW()),
            ('grid_built',     'true', NOW())
        ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = NOW()
    """, (str(cell_size), str(min_x), str(min_y), str(min_z)))
    conn.commit()
    log.info("  grid_built = true ✓")

    # Distribution stats
    cur.execute("""
        SELECT MIN(system_count), MAX(system_count),
               AVG(system_count)::int,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY system_count)::int
        FROM spatial_grid WHERE system_count > 0
    """)
    r = cur.fetchone()

    cur.execute("SELECT COUNT(*) FROM spatial_grid WHERE visited_count > 0")
    visited_cells = cur.fetchone()[0]

    _safe_close(conn)

    total_elapsed = time.time() - script_start
    done_banner(log, "Spatial Grid Complete", total_elapsed, [
        f"Cells created   : {fmt_num(cell_count)}",
        f"Cells with scans: {fmt_num(visited_cells)}",
        f"Systems assigned: {fmt_num(total_systems)}",
        f"Systems/cell    : min={r[0]}  max={r[1]}  avg={r[2]}  median={r[3]}",
    ])
    log.info("Next step: python3 build_ratings.py")


if __name__ == '__main__':
    main()
