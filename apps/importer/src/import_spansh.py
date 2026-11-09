#!/usr/bin/env python3
"""
ED Finder — Spansh Dump Importer (PostgreSQL / Psycopg 3 COPY edition)
Version: 3.0  (galaxy region lookup + structured error logging)

NEW in v3.0:
  • galaxy_region_id populated for every system during import using the
    klightspeed/EliteDangerousRegionMap algorithm (RegionMapData.py).
    Each system gets a SMALLINT (1-42) identifying its named ED codex region
    (Inner Orion Spur, Formorian Frontier, Galactic Centre, etc.).
    The lookup is pure integer arithmetic — zero DB overhead per row.
  • Structured error logging: per-record failures are written to the
    import_errors table so you can query exactly which systems/bodies/stations
    failed and why, without grepping 500MB log files.
  • errors_encountered counter tracked in import_meta so the API can surface
    import health in real-time.
  • needs_permit field populated from Spansh data (defaults FALSE if absent).

FIX in v2.5:
  • Progress bar desync on resume fixed (tqdm initialised after fast-forward).
  • import_stations: stations missing 'id' or 'systemId64' now counted/logged.

FIX in v2.4:
  • _make_direct_dsn() added: automatically rewrites DATABASE_URL to bypass
    pgBouncer (port 5433 → 5432, @pgbouncer: → @postgres:).

Why PostgreSQL COPY instead of INSERT ... ON CONFLICT:
  • COPY is the fastest possible PostgreSQL bulk-load method.
  • Strategy: COPY into a temp table, then INSERT ... ON CONFLICT from temp
    into the real table. This gives us both speed AND upsert semantics.

Server:   Hetzner AX41-SSD — i7-8700 (6C/12T), 128 GB RAM, 3×1 TB NVMe RAID-5
Database: PostgreSQL 16

Usage:
    python3 import_spansh.py --all                   # import all dumps
    python3 import_spansh.py --file galaxy.json.gz   # import one file
    python3 import_spansh.py --all --resume          # resume from checkpoint
    python3 import_spansh.py --download-only         # download files then exit
    python3 import_spansh.py --download --all        # download then import
    python3 import_spansh.py --status                # show import progress
    python3 import_spansh.py --errors                # show recent import errors
"""

import os
import sys
import gzip
import json
import time
import logging
import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Tuple

import decimal
import ijson
import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
from tqdm import tqdm
from ring_facts import ring_rows_for_body
from body_ring_enrichment_plan import TRUSTED_RING_ASSOCIATION_STATUS
from shared_contracts.bulk_update_helper import bulk_update_replica_mode

# ---------------------------------------------------------------------------
# Galaxy Region Lookup (klightspeed/EliteDangerousRegionMap)
# ---------------------------------------------------------------------------
# Audit fix (2026-05-08, AUDIT_REPORT.md §M12): the 2 099-line
# RegionMapData.py was pure data masquerading as code. The same RLE map
# now lives in data/region_map.json, loaded by region_map.py.
from region_map import find_galaxy_region, is_available as _region_lookup_available
_REGION_LOOKUP_AVAILABLE = _region_lookup_available()


def _json_dumps(obj) -> Optional[str]:
    """json.dumps that converts Decimal → float (ijson returns Decimal for numbers)."""
    if obj is None:
        return None
    def _default(o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        raise TypeError(f"Not serializable: {type(o)}")
    return json.dumps(obj, default=_default)


def _extract_system_coords(sys_obj: dict) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return x/y/z when all coordinates are present; otherwise preserve unknown as NULL."""
    coords = sys_obj.get('coords') or {}
    coords = coords if isinstance(coords, dict) else {}
    cx = coords.get('x') if coords.get('x') is not None else sys_obj.get('x')
    cy = coords.get('y') if coords.get('y') is not None else sys_obj.get('y')
    cz = coords.get('z') if coords.get('z') is not None else sys_obj.get('z')
    if cx is None or cy is None or cz is None:
        return None, None, None
    try:
        return float(cx), float(cy), float(cz)
    except (TypeError, ValueError):
        return None, None, None

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _make_direct_dsn(url: str) -> str:
    """
    Ensure the DSN points directly at postgres (port 5432), not pgBouncer (5433).
    pgBouncer transaction-pool mode is incompatible with COPY and long-running
    import transactions — it can silently drop connections mid-import.
    """
    direct = os.getenv('DB_DSN_DIRECT', '')
    if direct:
        return direct
    if ':5433/' in url:
        url = url.replace(':5433/', ':5432/')
    url = url.replace('@pgbouncer:', '@postgres:')
    return url

DB_DSN          = _make_direct_dsn(os.environ['DATABASE_URL'])  # fail-fast: no insecure fallback
DUMP_DIR        = Path(os.getenv('DUMP_DIR', '/data/dumps'))
BATCH_SIZE      = int(os.getenv('BATCH_SIZE', '50000'))
LOG_LEVEL       = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE        = os.getenv('LOG_FILE', '/data/logs/import.log')

SPANSH_BASE     = 'https://downloads.spansh.co.uk'

DUMP_FILES      = [
    'galaxy.json.gz',
    'galaxy_populated.json.gz',
    'galaxy_stations.json.gz',
]

DELTA_FILES     = [
    'systems_1day.json.gz',
    'systems_1week.json.gz',
    'systems_1month.json.gz',
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s,%(msecs)03d [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
    ]
)
log = logging.getLogger('import_spansh')

if _REGION_LOOKUP_AVAILABLE:
    log.info("Galaxy region lookup: ENABLED (42 named ED codex regions)")
else:
    log.warning("Galaxy region lookup: DISABLED (region_map.json not found)")

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def get_conn() -> psycopg.Connection:
    conn = psycopg.connect(DB_DSN, options='-c statement_timeout=0')
    conn.autocommit = False
    return conn


def resolve_index_sql_path() -> Path:
    """Locate the vendored image SQL or the repository-root SQL in development."""
    source_dir = Path(__file__).resolve().parent
    for base_dir in (source_dir, *source_dir.parents):
        candidate = base_dir / 'sql' / '002_indexes.sql'
        if candidate.is_file():
            return candidate
    return source_dir / 'sql' / '002_indexes.sql'


def set_import_optimisations(conn):
    """Apply session-level settings for maximum bulk-load speed."""
    with conn.cursor() as cur:
        cur.execute("SET synchronous_commit = off")
        cur.execute("SET work_mem = '256MB'")
        cur.execute("SET maintenance_work_mem = '4GB'")
    conn.commit()
    log.info("Import optimisations applied (synchronous_commit=off, work_mem=256MB)")


# ---------------------------------------------------------------------------
# Structured error logging
# ---------------------------------------------------------------------------
_error_batch: List[Tuple] = []
_error_batch_size = 500

def log_import_error(conn, dump_file: str, record_id: Optional[int],
                     record_type: str, exc: Exception,
                     raw_snippet: Optional[str] = None):
    """
    Queue a structured error record. Flushed in batches to import_errors table.
    This replaces grepping log files — query import_errors directly to diagnose failures.
    """
    _error_batch.append((
        dump_file,
        record_id,
        record_type,
        type(exc).__name__,
        str(exc)[:500],
        raw_snippet[:500] if raw_snippet else None,
    ))
    if len(_error_batch) >= _error_batch_size:
        flush_error_batch(conn, dump_file)


def flush_error_batch(conn, dump_file: str):
    """Write accumulated error records to the import_errors table."""
    if not _error_batch:
        return
    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO import_errors
                    (dump_file, record_id, record_type, error_class, error_message, raw_snippet)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                _error_batch
            )
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning(f"Failed to write error batch to import_errors: {e}")
    finally:
        _error_batch.clear()


def increment_error_count(conn, dump_file: str, count: int = 1):
    """Increment the errors_encountered counter in import_meta."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE import_meta
                SET errors_encountered = errors_encountered + %s,
                    updated_at = NOW()
                WHERE dump_file = %s
            """, (count, dump_file))
        conn.commit()
    except Exception as e:
        # A failed statement leaves the connection's transaction aborted
        # until rolled back — conn is reused for every later flush in the
        # same import run, so skipping this would turn one lost counter
        # update into every subsequent operation on conn also failing.
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning(f"Failed to increment error count for {dump_file}: {e}")


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------
def get_checkpoint(conn, dump_file: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT last_checkpoint FROM import_meta WHERE dump_file = %s",
            (dump_file,)
        )
        row = cur.fetchone()
        return row[0] if row else 0


def save_checkpoint(conn, dump_file: str, offset: int, rows: int, bytes_pos: int = 0):
    """Save checkpoint to import_meta table."""
    with conn.cursor() as cur:
        if bytes_pos > 0:
            cur.execute("""
                UPDATE import_meta
                SET last_checkpoint = %s,
                    rows_processed  = %s,
                    bytes_processed = %s,
                    updated_at      = NOW()
                WHERE dump_file = %s
            """, (rows, rows, bytes_pos, dump_file))
        else:
            cur.execute("""
                UPDATE import_meta
                SET last_checkpoint = %s,
                    rows_processed  = %s,
                    updated_at      = NOW()
                WHERE dump_file = %s
            """, (rows, rows, dump_file))
    conn.commit()


def save_checkpoint_safely(conn, dump_file: str, offset: int, rows: int, bytes_pos: int = 0) -> None:
    """save_checkpoint(), but a failure is logged and rolled back instead
    of silently swallowed. Used by every periodic (non-interrupt) call
    site — a lost checkpoint update during a multi-hour import used to be
    invisible, and worse, left conn's transaction aborted for every later
    flush in the same run since the failed UPDATE was never rolled back."""
    try:
        save_checkpoint(conn, dump_file, offset, rows, bytes_pos)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning(f"Failed to save checkpoint for {dump_file}: {e}")


def mark_running(conn, dump_file: str, total_bytes: int):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE import_meta
            SET status      = 'running',
                started_at  = COALESCE(started_at, NOW()),
                bytes_total = %s,
                updated_at  = NOW()
            WHERE dump_file = %s
        """, (total_bytes, dump_file))
    conn.commit()


def mark_complete(conn, dump_file: str, rows: int):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE import_meta
            SET status          = 'complete',
                completed_at    = NOW(),
                rows_processed  = %s,
                updated_at      = NOW()
            WHERE dump_file = %s
        """, (rows, dump_file))
    conn.commit()


def mark_failed(conn, dump_file: str, error: str):
    try:
        conn.rollback()
    except Exception:
        pass
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE import_meta
                SET status        = 'failed',
                    error_message = %s,
                    updated_at    = NOW()
                WHERE dump_file = %s
            """, (error[:500], dump_file))
        conn.commit()
    except Exception as e:
        log.error(f"mark_failed itself failed: {e}")


# ---------------------------------------------------------------------------
# COPY helper
# ---------------------------------------------------------------------------
def copy_records(conn, table: str, columns: List[str], rows: List[Tuple]) -> int:
    if not rows:
        return 0
    copy_statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table),
        sql.SQL(', ').join(map(sql.Identifier, columns)),
    )
    with conn.cursor() as cur:
        with cur.copy(copy_statement) as copy:
            for row in rows:
                copy.write_row(row)
    conn.commit()
    return len(rows)


def _run_with_deadlock_retry(conn, work, *, label, attempts=4, base_delay=0.5):
    """Run `work()`, a self-contained self-committing unit against `conn`.
    On a Postgres deadlock, roll back and retry with exponential backoff.
    Re-raises after the final attempt so a persistent deadlock still fails
    honestly (never a silent success)."""
    for attempt in range(1, attempts + 1):
        try:
            return work()
        except psycopg.errors.DeadlockDetected:
            # bulk_update_replica_mode already rolls back failed work and
            # commits its role restoration.  Other callers can still arrive
            # here with an aborted transaction and require the rollback.
            is_idle = getattr(getattr(conn, 'info', None), 'transaction_status', None) == TransactionStatus.IDLE
            if not is_idle:
                conn.rollback()
            if attempt == attempts:
                log.error(f"{label}: deadlock persisted after {attempts} attempts — giving up")
                raise
            delay = base_delay * (2 ** (attempt - 1))
            log.warning(f"{label}: deadlock on attempt {attempt}/{attempts}, retrying in {delay:.1f}s")
            time.sleep(delay)


def upsert_via_temp(conn, target_table: str, columns: List[str],
                    rows: List[Tuple], conflict_col: str,
                    update_cols: Optional[List[str]] = None,
                    guard_col: Optional[str] = None,
                    returning_col: Optional[str] = None):
    """guard_col: if set, an update is applied only when this column's
    incoming value matches the existing row's value. Use for tables where
    conflict_col alone doesn't establish ownership (e.g. bodies.id is a
    source-supplied id that can collide across systems) so a colliding row
    from a different owner is a no-op instead of a silent re-parent.

    returning_col: only meaningful together with guard_col. If set, also
    compute the set of (guard_col value, this column's value) pairs among
    the attempted rows whose guard_col was rejected, and return
    (count, rejected_keys) instead of a bare count. rejected_keys entries
    are (guard_col_value, conflict_col_value) pairs rather than bare
    conflict_col values: a bare value can't tell a caller which specific
    owner's attempt was rejected when the same conflict_col value was
    attempted by more than one owner.

    Rejection is always determined from the actual post-write state of
    target_table, never from which row happened to survive in-batch
    de-duplication: every row originally attempted in this call (before
    de-duplication) whose guard_col value doesn't match target_table's
    actual final guard_col value for that conflict_col is rejected. This
    matters when conflict_col already has a row under an owner that isn't
    the batch's last occurrence for that value — de-duplication picks the
    last occurrence to attempt writing, but if guard_col blocks that write
    the pre-existing owner remains the true final owner and must not be
    reported as rejected merely because an in-batch heuristic guessed a
    different "winner".

    This is deliberately a direct post-write ownership comparison rather
    than reading back RETURNING from the main statement: RETURNING only
    reports rows the UPDATE actually touched, and the same-owner no-op case
    (nothing changed, so the change-detection half of where_clause is
    false) also produces no RETURNING row even though guard_col was not
    rejected — conflating the two would wrongly treat an unchanged,
    correctly-owned row as rejected. Use this so dependent rows keyed off
    conflict_col (e.g. body_rings.body_id -> bodies.id) are not written for
    a row whose owning upsert was rejected by guard_col — otherwise a
    rejected collision still lets its dependent rows attach as trusted data
    to the wrong owner."""
    if not rows:
        return (0, set()) if returning_col else 0
    conflict_col_index = columns.index(conflict_col)
    guard_col_index = columns.index(guard_col) if guard_col and returning_col else None
    original_rows = rows

    def _normalize_conflict_value(value):
        # A conflict_col value arriving as a numeric string in some source
        # records and a native int in others (e.g. inconsistent JSON typing
        # upstream) must still collide on the same dedup key here - the
        # temp table's COPY-based cast to the target column type means both
        # representations resolve to the same row identity at the database
        # level regardless of what Python type carried it in.
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return value
        return value

    rows_by_conflict_value = {}
    for row in rows:
        conflict_key = _normalize_conflict_value(row[conflict_col_index])
        rows_by_conflict_value[conflict_key] = row
    duplicate_rows_dropped = len(rows) - len(rows_by_conflict_value)
    if duplicate_rows_dropped:
        rows = list(rows_by_conflict_value.values())
        log.warning(
            f"upsert_via_temp({target_table}): dropped "
            f"{duplicate_rows_dropped:,} duplicate row(s) for conflict_col "
            f"{conflict_col}"
        )
    if update_cols is None:
        update_cols = [c for c in columns if c != conflict_col]
    temp = f"_tmp_{target_table}"
    target_ident = sql.Identifier(target_table)
    temp_ident = sql.Identifier(temp)
    column_list = sql.SQL(', ').join(map(sql.Identifier, columns))
    set_clause = sql.SQL(', ').join(
        sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(column), sql.Identifier(column))
        for column in update_cols
    )
    excluded_change_cols = {'updated_at', 'rating_dirty', 'cluster_dirty'}
    if guard_col:
        excluded_change_cols = excluded_change_cols | {guard_col}
    change_cols = [
        c for c in update_cols
        if c not in excluded_change_cols
    ]
    guard_clause = None
    if guard_col:
        guard_clause = sql.SQL("{}.{} = EXCLUDED.{}").format(
            target_ident,
            sql.Identifier(guard_col),
            sql.Identifier(guard_col),
        )
    change_clause = None
    if change_cols:
        change_clause = sql.SQL(' OR ').join(
            sql.SQL("{}.{} IS DISTINCT FROM EXCLUDED.{}").format(
                target_ident,
                sql.Identifier(column),
                sql.Identifier(column),
            )
            for column in change_cols
        )
    if guard_clause and change_clause:
        where_clause = sql.SQL("\n            WHERE {}\n              AND ({})").format(
            guard_clause, change_clause,
        )
    elif guard_clause:
        where_clause = sql.SQL("\n            WHERE {}").format(guard_clause)
    elif change_clause:
        where_clause = sql.SQL("\n            WHERE {}").format(change_clause)
    else:
        where_clause = sql.SQL('')

    with conn.cursor() as cur:
        cur.execute(sql.SQL("""
            CREATE TEMP TABLE IF NOT EXISTS {}
            (LIKE {} INCLUDING DEFAULTS)
            ON COMMIT DELETE ROWS
        """).format(temp_ident, target_ident))
    conn.commit()

    def _do():
        with conn.cursor() as cur:
            cur.execute(sql.SQL("TRUNCATE {}").format(temp_ident))
            copy_statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
                sql.Identifier(temp),
                sql.SQL(', ').join(map(sql.Identifier, columns)),
            )
            with cur.copy(copy_statement) as copy:
                for row in rows:
                    copy.write_row(row)
            # System conflicts are high-volume parent-table updates.  Apply
            # replica mode only around that target statement: body/station
            # upserts intentionally retain FK and custom-trigger enforcement.
            mode = (
                bulk_update_replica_mode(conn)
                if target_table == 'systems'
                else nullcontext(conn)
            )
            with mode:
                cur.execute(sql.SQL("""
                    INSERT INTO {} ({})
                    SELECT {} FROM {}
                    ON CONFLICT ({}) DO UPDATE
                    SET {}{}
                """).format(
                    target_ident,
                    column_list,
                    column_list,
                    temp_ident,
                    sql.Identifier(conflict_col),
                    set_clause,
                    where_clause,
                ))
                count = cur.rowcount
                rejected_keys = set() if returning_col else None
                if returning_col and guard_col:
                    distinct_conflict_values = list({
                        _normalize_conflict_value(row[conflict_col_index])
                        for row in original_rows
                    })
                    cur.execute(sql.SQL("""
                        SELECT {}, {}
                        FROM {}
                        WHERE {} = ANY(%s)
                    """).format(
                        sql.Identifier(conflict_col),
                        sql.Identifier(guard_col),
                        target_ident,
                        sql.Identifier(conflict_col),
                    ), (distinct_conflict_values,))
                    actual_owner_by_conflict_value = dict(cur.fetchall())
                    for row in original_rows:
                        conflict_key = _normalize_conflict_value(row[conflict_col_index])
                        guard_value = row[guard_col_index]
                        if actual_owner_by_conflict_value.get(conflict_key) != guard_value:
                            rejected_keys.add((guard_value, row[conflict_col_index]))
                conn.commit()
        return (count, rejected_keys) if returning_col else count

    return _run_with_deadlock_retry(conn, _do, label=f"upsert_via_temp({target_table})")


def upsert_body_rings(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    values = [_ring_row_tuple(row) for row in rows]

    def _do():
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO body_rings (
                    system_id64, body_id, source_body_id, body_name,
                    ring_name, ring_type, ring_class,
                    mass_mt, inner_radius, outer_radius,
                    source, confidence, association_status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (system_id64, body_id, ring_name, source) DO UPDATE SET
                    source_body_id      = COALESCE(EXCLUDED.source_body_id, body_rings.source_body_id),
                    body_name           = COALESCE(EXCLUDED.body_name, body_rings.body_name),
                    ring_type           = COALESCE(EXCLUDED.ring_type, body_rings.ring_type),
                    ring_class          = COALESCE(EXCLUDED.ring_class, body_rings.ring_class),
                    mass_mt             = COALESCE(EXCLUDED.mass_mt, body_rings.mass_mt),
                    inner_radius        = COALESCE(EXCLUDED.inner_radius, body_rings.inner_radius),
                    outer_radius        = COALESCE(EXCLUDED.outer_radius, body_rings.outer_radius),
                    confidence          = EXCLUDED.confidence,
                    association_status  = EXCLUDED.association_status,
                    updated_at          = NOW()
                """,
                values,
            )
            system_ids = sorted({row['system_id64'] for row in rows})
            # Keep body_rings INSERT triggers active.  Replica mode starts
            # only after that statement and before the parent systems update.
            with bulk_update_replica_mode(conn):
                cur.execute(
                    """
                    UPDATE systems
                       SET rating_dirty = TRUE,
                           cluster_dirty = TRUE,
                           updated_at = NOW()
                     WHERE id64 = ANY(%s)
                    """,
                    (system_ids,),
                )
                count = len(rows)
                conn.commit()
        return count

    return _run_with_deadlock_retry(conn, _do, label="upsert_body_rings")


def _filter_rings_by_rejected_bodies(
    ring_batch: list[dict], rejected_body_ids: set,
) -> tuple[list[dict], int]:
    """Drop ring rows whose owning body upsert was rejected by the
    system_id64 ownership guard (a colliding Spansh body id already owned
    by another system — see the bodies.id hazard in CLAUDE.md). This is
    the filter body_ring_rows_from_spansh_body's 'local_matched' comment
    depends on: association_status is only a true claim for a ring whose
    body survived this check. Extracted from flush_rings()'s closure into
    a standalone function (2026-08-08, Emergent recurrence report A2) so
    the invariant is independently testable instead of living only in an
    inline list comprehension nothing else could exercise."""
    keep = [
        r for r in ring_batch
        if (r.get('system_id64'), r.get('body_id')) not in rejected_body_ids
    ]
    return keep, len(ring_batch) - len(keep)


def _ring_row_tuple(row: dict) -> tuple:
    return (
        row.get('system_id64'),
        row.get('body_id'),
        row.get('source_body_id'),
        row.get('body_name'),
        row.get('ring_name'),
        row.get('ring_type'),
        row.get('ring_class'),
        row.get('mass_mt'),
        row.get('inner_radius'),
        row.get('outer_radius'),
        row.get('source'),
        row.get('confidence'),
        row.get('association_status'),
    )


def body_ring_rows_from_spansh_body(system_id64: int, body_id: int, body_name: str, body_record: dict) -> list[dict]:
    rows, _explicit_no_rings = ring_rows_for_body(
        body_record,
        system_id64=system_id64,
        body_id=body_id,
        body_name=body_name,
        source='spansh_dump',
        source_body_id=body_id,
        trusted_empty_means_no_rings=False,
    )
    for row in rows:
        # Every row returned here only ever reaches upsert_body_rings() via
        # flush_rings(), which filters ring_batch against rejected_body_ids
        # first - a ring survives that filter only when its owning body's
        # upsert was NOT rejected by the system_id64 ownership guard. The
        # body linkage is therefore already verified locally by the time
        # this runs, the same guarantee eddn_listener.py's
        # BODY_RING_ASSOCIATION_STATUS_CASE_SQL computes via a live query
        # for its own 'local_matched' case - set explicitly here instead of
        # left to fall through to body_rings.association_status's schema
        # default, which silently asserts the same claim with no code
        # anyone can audit.
        row['association_status'] = TRUSTED_RING_ASSOCIATION_STATUS
    return rows


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------
VALID_ECONOMIES = {
    'Agriculture', 'Refinery', 'Industrial', 'HighTech',
    'Military', 'Tourism', 'Extraction', 'Colony',
    'Terraforming', 'Prison', 'Damaged', 'Rescue',
    'Repair', 'Carrier', 'None', 'Unknown'
}
VALID_SECURITY   = {'High', 'Medium', 'Low', 'Anarchy', 'Lawless', 'Unknown'}
VALID_ALLEGIANCE = {
    'Federation', 'Empire', 'Alliance', 'Independent',
    'Thargoid', 'Guardian', 'PilotsFederation', 'None', 'Unknown'
}
VALID_GOVERNMENT = {
    'Democracy', 'Dictatorship', 'Feudal', 'Patronage',
    'Corporate', 'Cooperative', 'Theocracy', 'Anarchy',
    'Communism', 'Confederacy', 'None', 'Unknown'
}
VALID_STATION_TYPES = {
    'Coriolis', 'Orbis', 'Ocellus', 'Outpost',
    'PlanetaryPort', 'PlanetaryOutpost', 'MegaShip',
    'AsteroidBase', 'FleetCarrier', 'Unknown'
}
SCOOPABLE_STARS = {'O', 'B', 'A', 'F', 'G', 'K', 'M'}


def norm_economy(v) -> str:
    if not v:
        return 'Unknown'
    v = str(v).strip().replace(' ', '').replace('$economy_', '').replace(';', '')
    mapping = {
        'hightech': 'HighTech', 'high_tech': 'HighTech',
        'agriculture': 'Agriculture', 'agri': 'Agriculture',
        'refinery': 'Refinery', 'industrial': 'Industrial',
        'military': 'Military', 'tourism': 'Tourism',
        'extraction': 'Extraction', 'colony': 'Colony',
        'terraforming': 'Terraforming', 'prison': 'Prison',
        'damaged': 'Damaged', 'rescue': 'Rescue',
        'repair': 'Repair', 'carrier': 'Carrier',
        'none': 'None', 'unknown': 'Unknown', '': 'Unknown',
    }
    normalised = mapping.get(v.lower(), v)
    return normalised if normalised in VALID_ECONOMIES else 'Unknown'


def norm_security(v) -> str:
    if not v:
        return 'Unknown'
    v = str(v).strip().replace('$GAlAXY_MAP_INFO_state_', '').replace(';', '')
    mapping = {
        'high': 'High', 'medium': 'Medium', 'low': 'Low',
        'anarchy': 'Anarchy', 'lawless': 'Lawless', 'unknown': 'Unknown',
    }
    return mapping.get(v.lower(), 'Unknown')


def norm_allegiance(v) -> str:
    if not v:
        return 'Unknown'
    mapping = {
        'federation': 'Federation', 'empire': 'Empire',
        'alliance': 'Alliance', 'independent': 'Independent',
        'thargoid': 'Thargoid', 'guardian': 'Guardian',
        'pilotsfederation': 'PilotsFederation',
        'none': 'None', 'unknown': 'Unknown',
    }
    return mapping.get(str(v).lower().replace(' ', ''), 'Unknown')


def norm_government(v) -> str:
    if not v:
        return 'Unknown'
    mapping = {
        'democracy': 'Democracy', 'dictatorship': 'Dictatorship',
        'feudal': 'Feudal', 'patronage': 'Patronage',
        'corporate': 'Corporate', 'cooperative': 'Cooperative',
        'theocracy': 'Theocracy', 'anarchy': 'Anarchy',
        'communism': 'Communism', 'confederacy': 'Confederacy',
        'none': 'None', 'unknown': 'Unknown',
    }
    return mapping.get(str(v).lower().replace(' ', '').replace('$government_', '').replace(';', ''), 'Unknown')


def norm_station_type(v) -> str:
    if not v:
        return 'Unknown'
    token = ''.join(ch for ch in str(v).lower() if ch.isalnum())
    mapping = {
        'coriolis': 'Coriolis', 'orbis': 'Orbis', 'ocellus': 'Ocellus',
        'outpost': 'Outpost', 'planetaryport': 'PlanetaryPort',
        'planetaryoutpost': 'PlanetaryOutpost', 'megaship': 'MegaShip',
        'asteroidbase': 'AsteroidBase', 'fleetcarrier': 'FleetCarrier',
        'carrier': 'FleetCarrier',
        'coriolisstarport': 'Coriolis',
        'orbisstarport': 'Orbis',
        'ocellusstarport': 'Ocellus',
        'planetarysettlement': 'PlanetaryOutpost',
        'settlement': 'PlanetaryOutpost',
        'surfacesettlement': 'PlanetaryOutpost',
        'surfacestation': 'PlanetaryPort', 'craterport': 'PlanetaryPort',
        'crateroutpost': 'PlanetaryOutpost',
        'unknown': 'Unknown',
    }
    normalised = mapping.get(token, 'Unknown')
    return normalised if normalised in VALID_STATION_TYPES else 'Unknown'


def _first_present(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _first_text(*values) -> str | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, dict):
            value = _first_text(value.get('name'), value.get('bodyName'), value.get('body_name'))
        text = str(value).strip()
        if text:
            return text
    return None


def station_type_from_record(station: dict) -> str:
    return norm_station_type(_first_text(
        station.get('type'),
        station.get('stationType'),
        station.get('station_type'),
    ))


def station_distance_from_record(station: dict):
    return _first_present(
        station.get('distanceToArrival'),
        station.get('distanceFromArrival'),
        station.get('distanceFromArrivalLS'),
        station.get('distance_from_star'),
    )


def station_body_name_from_record(station: dict) -> str | None:
    return _first_text(
        station.get('bodyName'),
        station.get('body_name'),
        station.get('stationBodyName'),
        station.get('station_body_name'),
        station.get('body'),
    )


def parse_ts(v) -> Optional[str]:
    if not v:
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
        s = str(v).strip()
        try:
            dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
            return dt.isoformat()
        except ValueError:
            pass
        return s
    except Exception:
        return None


def parse_bool(v) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return str(v).lower() in ('true', '1', 'yes')


def _to_signal_count(val) -> int:
    if val is None:
        return 0
    if isinstance(val, list):
        return len(val)
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def _parse_bio_signals(b: dict) -> int:
    sig = b.get('signals')
    if sig is None:
        return _to_signal_count(b.get('bio_signal_count'))
    if isinstance(sig, dict):
        return _to_signal_count(sig.get('genuses'))
    if isinstance(sig, list):
        for s in sig:
            if isinstance(s, dict) and str(s.get('type', '')).lower() in ('biology', 'biological'):
                return _to_signal_count(s.get('count'))
        return 0
    return 0


def _parse_geo_signals(b: dict) -> int:
    sig = b.get('signals')
    if sig is None:
        return _to_signal_count(b.get('geo_signal_count'))
    if isinstance(sig, dict):
        return _to_signal_count(sig.get('geology'))
    if isinstance(sig, list):
        for s in sig:
            if isinstance(s, dict) and str(s.get('type', '')).lower() in ('geology', 'geological'):
                return _to_signal_count(s.get('count'))
        return 0
    return 0


def _parse_system_population(sys_obj: dict) -> int | None:
    value = sys_obj.get('population')
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _require_name(obj: dict, entity_id) -> Optional[str]:
    """systems.name, bodies.name, and stations.name are all NOT NULL with
    no default. A source record missing entity_id or name is malformed,
    not a legitimate "no name" entity - upsert_via_temp's SET clause has
    no NULL/empty guard, so writing '' here would blank out an existing
    real name on the next conflict (F-014, docs/audits/round6-report.md).
    Callers should skip the record entirely when this returns None."""
    if not entity_id:
        return None
    name = obj.get('name')
    return name if name else None


# ---------------------------------------------------------------------------
# IMPORTER 1 — galaxy.json.gz  (systems + bodies + stations, all-in-one)
def import_galaxy(conn, dump_path: Path, resume_offset: int = 0) -> int:
    """
    Parse galaxy.json.gz and upsert systems, bodies, and stations.
    v3.0: galaxy_region_id populated for every system via findRegion().
    v3.0: Structured errors written to import_errors table.
    """
    log.info(f"Importing systems+bodies+stations from {dump_path.name} ...")
    set_import_optimisations(conn)

    file_size = dump_path.stat().st_size
    mark_running(conn, dump_path.name, file_size)

    SYS_COLS = [
        'id64', 'name', 'x', 'y', 'z',
        'primary_economy', 'secondary_economy',
        'population', 'is_colonised', 'is_being_colonised',
        'controlling_faction',
        'security', 'allegiance', 'government',
        'needs_permit',
        'main_star_type', 'main_star_subtype', 'main_star_is_scoopable',
        'has_body_data', 'body_count', 'data_quality',
        'galaxy_region_id',
        'first_discovered_at', 'updated_at',
        'rating_dirty', 'cluster_dirty',
    ]

    BODY_COLS = [
        'id', 'system_id64', 'name',
        'body_type', 'subtype', 'is_main_star',
        'distance_from_star', 'orbital_period',
        'radius', 'mass', 'gravity', 'surface_temp', 'surface_pressure',
        'atmosphere_type', 'atmosphere_composition',
        'volcanism', 'materials',
        'terraforming_state', 'is_terraformable', 'is_landable',
        'is_water_world', 'is_earth_like', 'is_ammonia_world',
        'bio_signal_count', 'geo_signal_count',
        'spectral_class', 'luminosity', 'stellar_mass',
        'is_scoopable',
        'estimated_mapping_value', 'estimated_scan_value',
        'first_discovered_at', 'updated_at',
    ]

    STA_COLS = [
        'id', 'system_id64', 'name', 'station_type',
        'distance_from_star', 'body_name',
        'landing_pad_size',
        'has_market', 'has_shipyard', 'has_outfitting',
        'has_refuel', 'has_repair', 'has_rearm',
        'has_black_market', 'has_material_trader',
        'has_technology_broker', 'has_interstellar_factors',
        'has_universal_cartographics', 'has_search_rescue',
        'primary_economy', 'secondary_economy',
        'controlling_faction', 'allegiance', 'government',
        'updated_at',
    ]

    sys_batch   = []
    body_batch  = []
    ring_batch  = []
    sta_batch   = []
    total_rows  = 0
    skip_count  = 0
    last_save   = time.time()
    rejected_body_ids: set = set()  # (system_id64, body_id) pairs rejected by the guard
    rings_skipped_rejected_body = 0

    def flush_systems():
        if sys_batch:
            upsert_via_temp(conn, 'systems', SYS_COLS, sys_batch, 'id64')
            sys_batch.clear()

    def flush_bodies():
        flush_systems()
        if body_batch:
            _count, rejected_owner_keys = upsert_via_temp(
                conn, 'bodies', BODY_COLS, body_batch, 'id',
                guard_col='system_id64', returning_col='id',
            )
            if rejected_owner_keys:
                rejected_body_ids.update(rejected_owner_keys)
            body_batch.clear()

    def flush_rings():
        nonlocal rings_skipped_rejected_body
        flush_bodies()
        if ring_batch:
            keep, skipped = _filter_rings_by_rejected_bodies(ring_batch, rejected_body_ids)
            if skipped:
                rings_skipped_rejected_body += skipped
                log.info(
                    f"Skipping {skipped} ring row(s) whose owning body upsert "
                    "was rejected by the system_id64 ownership guard "
                    "(colliding Spansh body id already owned by another system)"
                )
            if keep:
                upsert_body_rings(conn, keep)
            ring_batch.clear()
        # Every currently-queued ring row has now been resolved (kept or
        # dropped) against the rejections seen so far — safe to reset before
        # the next span of flush_bodies() calls accumulates new ones.
        rejected_body_ids.clear()

    def flush_stations():
        flush_systems()
        if sta_batch:
            upsert_via_temp(conn, 'stations', STA_COLS, sta_batch, 'id')
            sta_batch.clear()

    with open(dump_path, 'rb') as f_raw, gzip.open(f_raw, 'rb') as f:
        if resume_offset > 0:
            log.info(f"Resuming from row {resume_offset:,} — fast-forwarding stream ...")
            _item_iter = ijson.items(f, 'item')
            for _i, _ in enumerate(_item_iter, 1):
                if _i % 500_000 == 0:
                    log.info(f"  fast-forward: {_i:,} / {resume_offset:,} rows skipped ...")
                if _i >= resume_offset:
                    break
            log.info(f"Fast-forward complete — continuing import from row {resume_offset:,}")
            items_iter = _item_iter
        else:
            items_iter = ijson.items(f, 'item')

        pbar = tqdm(
            total=file_size,
            initial=f_raw.tell(),
            unit='B', unit_scale=True, unit_divisor=1024,
            desc=dump_path.name,
        )

        try:
            for sys_obj in items_iter:
                id64 = sys_obj.get('id64')
                name = _require_name(sys_obj, id64)
                if name is None:
                    continue

                now_iso = datetime.now(timezone.utc).isoformat()

                # ── Main star detection ────────────────────────────────────
                bodies_raw = sys_obj.get('bodies', []) or []
                main_star_type      = None
                main_star_subtype   = None
                main_star_scoopable = None
                has_body_data = len(bodies_raw) > 0

                for b in bodies_raw:
                    if b.get('mainStar') or b.get('isMainStar') or b.get('is_main_star'):
                        sc = b.get('spectralClass') or b.get('spectral_class') or ''
                        main_star_type    = sc[:1] if sc else None
                        main_star_subtype = sc[1:] if len(sc) > 1 else None
                        main_star_scoopable = main_star_type in SCOOPABLE_STARS if main_star_type else None
                        break

                # ── Galaxy region lookup ───────────────────────────────────
                try:
                    sx, sy, sz = _extract_system_coords(sys_obj)
                    region_id = find_galaxy_region(sx, sz) if sx is not None else None
                except Exception:
                    sx, sy, sz = None, None, None
                    region_id = None

                # ── Controlling faction ────────────────────────────────────
                controlling  = None
                factions_raw = sys_obj.get('factions', []) or []
                for fac in factions_raw:
                    if fac.get('isControlling') or fac.get('is_controlling'):
                        controlling = fac.get('name')
                        break
                if not controlling:
                    controlling = sys_obj.get('controllingFaction') or sys_obj.get('controlling_faction')

                # ── Systems row ────────────────────────────────────────────
                try:
                    needs_permit = bool(
                        sys_obj.get('needsPermit') or
                        sys_obj.get('needs_permit', False)
                    )
                    sys_batch.append((
                        id64,
                        name,
                        sx, sy, sz,
                        norm_economy(sys_obj.get('primaryEconomy') or sys_obj.get('primary_economy')),
                        norm_economy(sys_obj.get('secondaryEconomy') or sys_obj.get('secondary_economy')),
                        _parse_system_population(sys_obj),
                        bool(sys_obj.get('isColonised') or sys_obj.get('is_colonised', False)),
                        bool(sys_obj.get('isBeingColonised') or sys_obj.get('is_being_colonised', False)),
                        controlling,
                        norm_security(sys_obj.get('security')),
                        norm_allegiance(sys_obj.get('allegiance')),
                        norm_government(sys_obj.get('government')),
                        needs_permit,
                        main_star_type,
                        main_star_subtype,
                        main_star_scoopable,
                        has_body_data,
                        len(bodies_raw),
                        2 if has_body_data else 0,
                        region_id,
                        parse_ts(sys_obj.get('date') or sys_obj.get('first_discovered_at')),
                        now_iso,
                        True,   # rating_dirty
                        True,   # cluster_dirty
                    ))
                except Exception as _e:
                    skip_count += 1
                    log.debug(f"Skipping system id64={id64}: {_e}")
                    log_import_error(conn, dump_path.name, id64, 'system', _e)
                    continue

                # ── Bodies rows ────────────────────────────────────────────
                for b in bodies_raw:
                    bid = b.get('id64') or b.get('id') or b.get('bodyId')
                    body_name = _require_name(b, bid)
                    if body_name is None:
                        continue
                    try:
                        btype_raw = b.get('type', 'Unknown')
                        btype = 'Star' if btype_raw == 'Star' else \
                                'Planet' if btype_raw == 'Planet' else \
                                'Unknown'
                        sc = b.get('spectralClass') or b.get('spectral_class') or ''
                        atm_comp = b.get('atmosphereComposition') or b.get('atmosphere_composition')
                        mats     = b.get('materials')
                        sub_type = b.get('subType') or b.get('subtype') or ''
                        is_main_star = bool(
                            b.get('mainStar') or
                            b.get('isMainStar') or
                            b.get('is_main_star', False)
                        )
                        is_earth_like  = (sub_type == 'Earth-like world') or bool(b.get('isEarthLike') or b.get('is_earth_like', False))
                        is_water_world = (sub_type == 'Water world') or bool(b.get('isWaterWorld') or b.get('is_water_world', False))
                        is_ammonia     = (sub_type == 'Ammonia world') or bool(b.get('isAmmoniaWorld') or b.get('is_ammonia_world', False))
                        tf_state = b.get('terraformingState') or b.get('terraforming_state') or ''
                        is_terraformable = (
                            tf_state == 'Terraformable' or
                            bool(b.get('isTerraformingCandidate') or b.get('is_terraformable', False))
                        )
                        mass = b.get('earthMasses') or b.get('mass')
                        stellar_mass = b.get('solarMasses') or b.get('stellar_mass')

                        body_batch.append((
                            bid, id64,
                            body_name,
                            btype,
                            sub_type or None,
                            is_main_star,
                            _first_present(b.get('distanceToArrival'), b.get('distance_from_star')),
                            b.get('orbitalPeriod') or b.get('orbital_period'),
                            b.get('radius'),
                            mass,
                            b.get('gravity'),
                            b.get('surfaceTemperature') or b.get('surface_temp'),
                            b.get('surfacePressure') or b.get('surface_pressure'),
                            b.get('atmosphereType') or b.get('atmosphere_type'),
                            _json_dumps(atm_comp),
                            b.get('volcanismType') or b.get('volcanism'),
                            _json_dumps(mats),
                            tf_state or None,
                            is_terraformable,
                            bool(b.get('isLandable') or b.get('is_landable', False)),
                            is_water_world,
                            is_earth_like,
                            is_ammonia,
                            _parse_bio_signals(b),
                            _parse_geo_signals(b),
                            sc if sc else None,
                            b.get('luminosity'),
                            stellar_mass,
                            (sc[:1] in SCOOPABLE_STARS) if sc else None,
                            b.get('estimatedMappingValue') or b.get('estimated_mapping_value'),
                            b.get('estimatedScanValue') or b.get('estimated_scan_value'),
                            parse_ts(b.get('updateTime') or b.get('updated_at')),
                            now_iso,
                        ))
                        ring_batch.extend(body_ring_rows_from_spansh_body(id64, bid, body_name, b))
                    except Exception as _e:
                        skip_count += 1
                        log.debug(f"Skipping body id={bid} in system {id64}: {_e}")
                        log_import_error(conn, dump_path.name, bid, 'body', _e)
                        continue

                # ── Stations rows ──────────────────────────────────────────
                stations_raw = sys_obj.get('stations', []) or []
                for s in stations_raw:
                    sid = s.get('id') or s.get('marketId') or s.get('market_id')
                    sta_name = _require_name(s, sid)
                    if sta_name is None:
                        skip_count += 1
                        log.debug(f"Skipping station with no id/name in system {id64}")
                        continue
                    try:
                        svcs = (s.get('services') or
                                s.get('otherServices') or
                                s.get('other_services') or [])
                        svcs_lower = {str(x).lower() for x in svcs}
                        landing_pads = s.get('landingPads') or {}
                        if not isinstance(landing_pads, dict):
                            landing_pads = {}
                        pad_size = ('L' if landing_pads.get('large') else
                                    'M' if landing_pads.get('medium') else
                                    s.get('landing_pad_size'))
                        has_market    = (s.get('market') is not None or
                                         'market' in svcs_lower or
                                         bool(s.get('hasMarket') or s.get('has_market', False)))
                        has_shipyard  = (s.get('shipyard') is not None or
                                         'shipyard' in svcs_lower or
                                         bool(s.get('hasShipyard') or s.get('has_shipyard', False)))
                        has_outfitting= (s.get('outfitting') is not None or
                                         'outfitting' in svcs_lower or
                                         bool(s.get('hasOutfitting') or s.get('has_outfitting', False)))
                        sta_batch.append((
                            sid, id64,
                            sta_name,
                            station_type_from_record(s),
                            station_distance_from_record(s),
                            station_body_name_from_record(s),
                            pad_size,
                            has_market,
                            has_shipyard,
                            has_outfitting,
                            'refuel' in svcs_lower or bool(s.get('has_refuel', False)),
                            'repair' in svcs_lower or bool(s.get('has_repair', False)),
                            'restock' in svcs_lower or 'rearm' in svcs_lower or bool(s.get('has_rearm', False)),
                            'black market' in svcs_lower or bool(s.get('has_black_market', False)),
                            'material trader' in svcs_lower or bool(s.get('has_material_trader', False)),
                            'technology broker' in svcs_lower or bool(s.get('has_technology_broker', False)),
                            'interstellar factors contact' in svcs_lower or 'interstellar factors' in svcs_lower or bool(s.get('has_interstellar_factors', False)),
                            'universal cartographics' in svcs_lower or bool(s.get('has_universal_cartographics', False)),
                            'search and rescue' in svcs_lower or bool(s.get('has_search_rescue', False)),
                            norm_economy(s.get('primaryEconomy') or s.get('primary_economy')),
                            norm_economy(s.get('secondaryEconomy') or s.get('secondary_economy')),
                            s.get('controllingFaction') or s.get('controlling_faction'),
                            norm_allegiance(s.get('allegiance')),
                            norm_government(s.get('government')),
                            parse_ts(s.get('updateTime') or s.get('updated_at')) or now_iso,
                        ))
                    except Exception as _e:
                        skip_count += 1
                        log.debug(f"Skipping station id={sid} in system {id64}: {_e}")
                        log_import_error(conn, dump_path.name, sid, 'station', _e)
                        continue

                total_rows += 1

                if len(sys_batch) >= BATCH_SIZE:
                    flush_systems()
                if len(body_batch) >= BATCH_SIZE:
                    flush_bodies()
                if len(ring_batch) >= BATCH_SIZE:
                    flush_rings()
                if len(sta_batch) >= BATCH_SIZE:
                    flush_stations()

                if time.time() - last_save > 60:
                    flush_systems()
                    flush_bodies()
                    flush_rings()
                    flush_stations()
                    flush_error_batch(conn, dump_path.name)
                    if skip_count > 0:
                        increment_error_count(conn, dump_path.name, skip_count)
                        skip_count = 0
                    save_checkpoint_safely(conn, dump_path.name, 0, total_rows + resume_offset, f_raw.tell())
                    last_save = time.time()
                    pbar.n = f_raw.tell()
                    pbar.refresh()

        except KeyboardInterrupt:
            log.info("Interrupted — saving checkpoint ...")
            flush_systems()
            flush_bodies()
            flush_rings()
            flush_stations()
            flush_error_batch(conn, dump_path.name)
            save_checkpoint(conn, dump_path.name, 0, total_rows + resume_offset, f_raw.tell())
            log.info(f"Checkpoint saved at row {total_rows + resume_offset:,} "
                     f"({skip_count:,} records skipped)")
            sys.exit(0)

        flush_systems()
        flush_bodies()
        flush_rings()
        flush_stations()
        flush_error_batch(conn, dump_path.name)
        if skip_count > 0:
            increment_error_count(conn, dump_path.name, skip_count)
        pbar.close()

    if skip_count:
        log.warning(f"Skipped {skip_count:,} malformed records during galaxy import")
    if rings_skipped_rejected_body:
        log.warning(
            f"Skipped {rings_skipped_rejected_body:,} ring row(s) for bodies "
            "whose upsert was rejected by the system_id64 ownership guard "
            "(colliding Spansh body id)"
        )
    mark_complete(conn, dump_path.name, total_rows)
    log.info(f"galaxy.json.gz complete: {total_rows:,} systems imported")
    return total_rows


# ---------------------------------------------------------------------------
# IMPORTER 2 — galaxy_populated.json.gz  (faction/economy enrichment)
# ---------------------------------------------------------------------------
def import_populated(conn, dump_path: Path, resume_offset: int = 0) -> int:
    """Enrich populated systems with faction, economy, security, government data."""
    log.info(f"Importing populated systems from {dump_path.name} ...")
    set_import_optimisations(conn)

    file_size = dump_path.stat().st_size
    mark_running(conn, dump_path.name, file_size)

    SYS_ENRICH_COLS = [
        'id64', 'name', 'x', 'y', 'z',
        'primary_economy', 'secondary_economy',
        'population', 'security', 'allegiance', 'government',
        'controlling_faction', 'is_colonised',
        'updated_at', 'rating_dirty', 'cluster_dirty',
    ]

    sys_batch  = []
    fac_batch  = []
    sf_batch   = []
    total_rows = 0
    skip_count = 0
    last_save  = time.time()

    def flush_sys():
        if not sys_batch:
            return
        upsert_via_temp(conn, 'systems', SYS_ENRICH_COLS, sys_batch, 'id64',
                        update_cols=[c for c in SYS_ENRICH_COLS if c != 'id64'])
        sys_batch.clear()

    def flush_system_factions():
        if not sf_batch:
            return
        with conn.cursor() as _cur:
            for row in sf_batch:
                try:
                    _cur.execute("""
                        INSERT INTO system_factions
                            (system_id64, faction_id, influence, state, is_controlling, updated_at)
                        SELECT %s, f.id, %s, %s, %s, NOW()
                        FROM factions f
                        WHERE f.name = %s
                        ON CONFLICT (system_id64, faction_id) DO UPDATE
                        SET influence      = EXCLUDED.influence,
                            state          = EXCLUDED.state,
                            is_controlling = EXCLUDED.is_controlling,
                            updated_at     = NOW()
                    """, (row[0], row[2], row[3], row[4], row[1]))
                except Exception as _e:
                    log.debug(f"system_factions upsert skipped: {_e}")
        conn.commit()
        sf_batch.clear()

    def flush_factions():
        if not fac_batch:
            return
        seen: dict = {}
        for name, alleg, gov in fac_batch:
            seen[name] = (name, alleg, gov)
        deduped = list(seen.values())
        with conn.cursor() as _cur:
            _cur.executemany(
                """
                INSERT INTO factions (name, allegiance, government)
                VALUES (%s, %s, %s)
                ON CONFLICT (name) DO UPDATE
                SET allegiance = EXCLUDED.allegiance,
                    government = EXCLUDED.government,
                    updated_at = NOW()
                """,
                deduped
            )
        conn.commit()
        fac_batch.clear()

    with open(dump_path, 'rb') as f_raw, gzip.open(f_raw, 'rb') as f:
        if resume_offset > 0:
            log.info(f"Resuming from row {resume_offset:,} — fast-forwarding stream ...")
            _item_iter = ijson.items(f, 'item')
            for _i, _ in enumerate(_item_iter, 1):
                if _i % 100_000 == 0:
                    log.info(f"  fast-forward: {_i:,} / {resume_offset:,} rows skipped ...")
                if _i >= resume_offset:
                    break
            log.info(f"Fast-forward complete — continuing import from row {resume_offset:,}")
            items_iter = _item_iter
        else:
            items_iter = ijson.items(f, 'item')

        pbar = tqdm(
            total=file_size, initial=f_raw.tell(),
            unit='B', unit_scale=True, unit_divisor=1024,
            desc=dump_path.name,
        )

        try:
            for sys_obj in items_iter:
                id64 = sys_obj.get('id64')
                name = _require_name(sys_obj, id64)
                if name is None:
                    continue

                now_iso = datetime.now(timezone.utc).isoformat()

                controlling = None
                factions_raw = sys_obj.get('factions', []) or []
                for fac in factions_raw:
                    fname = fac.get('name')
                    if fname:
                        falleg = norm_allegiance(fac.get('allegiance'))
                        fgov   = norm_government(fac.get('government'))
                        fac_batch.append((fname, falleg, fgov))
                        is_ctrl = bool(fac.get('isControlling') or fac.get('is_controlling'))
                        if is_ctrl:
                            controlling = fname
                        sf_batch.append((
                            id64, fname,
                            float(fac.get('influence') or 0),
                            fac.get('state'),
                            is_ctrl,
                        ))

                if not controlling:
                    controlling = sys_obj.get('controllingFaction')

                try:
                    sx, sy, sz = _extract_system_coords(sys_obj)
                    sys_batch.append((
                        id64,
                        name,
                        sx, sy, sz,
                        norm_economy(sys_obj.get('primaryEconomy') or sys_obj.get('primary_economy')),
                        norm_economy(sys_obj.get('secondaryEconomy') or sys_obj.get('secondary_economy')),
                        _parse_system_population(sys_obj),
                        norm_security(sys_obj.get('security')),
                        norm_allegiance(sys_obj.get('allegiance')),
                        norm_government(sys_obj.get('government')),
                        controlling,
                        True,
                        now_iso,
                        True,
                        True,
                    ))
                except Exception as _e:
                    skip_count += 1
                    log.debug(f"Skipping populated system id64={id64}: {_e}")
                    log_import_error(conn, dump_path.name, id64, 'system', _e)
                    continue

                total_rows += 1

                if len(sys_batch) >= BATCH_SIZE:
                    flush_sys()
                if len(fac_batch) >= BATCH_SIZE:
                    flush_factions()
                    flush_system_factions()
                if len(sf_batch) >= BATCH_SIZE:
                    flush_factions()
                    flush_system_factions()

                if time.time() - last_save > 60:
                    flush_sys()
                    flush_factions()
                    flush_system_factions()
                    flush_error_batch(conn, dump_path.name)
                    save_checkpoint_safely(conn, dump_path.name, 0, total_rows + resume_offset, f_raw.tell())
                    last_save = time.time()
                    pbar.n = f_raw.tell()
                    pbar.refresh()

        except KeyboardInterrupt:
            log.info("Interrupted — saving checkpoint ...")
            flush_sys()
            flush_factions()
            flush_system_factions()
            flush_error_batch(conn, dump_path.name)
            save_checkpoint(conn, dump_path.name, 0, total_rows + resume_offset, f_raw.tell())
            sys.exit(0)

        flush_sys()
        flush_factions()
        flush_system_factions()
        flush_error_batch(conn, dump_path.name)
        pbar.close()

    mark_complete(conn, dump_path.name, total_rows)
    log.info(f"galaxy_populated.json.gz complete: {total_rows:,} systems enriched")
    return total_rows


# ---------------------------------------------------------------------------
# IMPORTER 3 — galaxy_stations.json.gz  (station refresh)
# ---------------------------------------------------------------------------
def import_stations(conn, dump_path: Path, resume_offset: int = 0) -> int:
    """Re-import all stations with latest market/service/economy state."""
    log.info(f"Importing stations from {dump_path.name} ...")
    set_import_optimisations(conn)

    file_size = dump_path.stat().st_size
    mark_running(conn, dump_path.name, file_size)

    STA_COLS = [
        'id', 'system_id64', 'name', 'station_type',
        'distance_from_star', 'body_name',
        'landing_pad_size',
        'has_market', 'has_shipyard', 'has_outfitting',
        'has_refuel', 'has_repair', 'has_rearm',
        'has_black_market', 'has_material_trader',
        'has_technology_broker', 'has_interstellar_factors',
        'has_universal_cartographics', 'has_search_rescue',
        'primary_economy', 'secondary_economy',
        'controlling_faction', 'allegiance', 'government',
        'updated_at',
    ]

    sta_batch  = []
    total_rows = 0
    skip_count = 0
    last_save  = time.time()

    def flush():
        if sta_batch:
            upsert_via_temp(conn, 'stations', STA_COLS, sta_batch, 'id')
            sta_batch.clear()

    with open(dump_path, 'rb') as f_raw, gzip.open(f_raw, 'rb') as f:
        if resume_offset > 0:
            log.info(f"Resuming from row {resume_offset:,} — fast-forwarding stream ...")
            _item_iter = ijson.items(f, 'item')
            for _i, _ in enumerate(_item_iter, 1):
                if _i % 100_000 == 0:
                    log.info(f"  fast-forward: {_i:,} / {resume_offset:,} rows skipped ...")
                if _i >= resume_offset:
                    break
            log.info(f"Fast-forward complete — continuing import from row {resume_offset:,}")
            items_iter = _item_iter
        else:
            items_iter = ijson.items(f, 'item')

        pbar = tqdm(
            total=file_size, initial=f_raw.tell(),
            unit='B', unit_scale=True, unit_divisor=1024,
            desc=dump_path.name,
        )

        try:
            for s in items_iter:
                sid      = s.get('id') or s.get('marketId') or s.get('market_id')
                sys_id64 = s.get('systemId64') or s.get('system_id64')
                sta_name = _require_name(s, sid and sys_id64)
                if sta_name is None:
                    continue

                now_iso  = datetime.now(timezone.utc).isoformat()
                try:
                    svcs = (s.get('services') or
                            s.get('otherServices') or
                            s.get('other_services') or [])
                    svcs_lower = {str(x).lower() for x in svcs}
                    landing_pads = s.get('landingPads') or {}
                    if not isinstance(landing_pads, dict):
                        landing_pads = {}
                    has_market     = (s.get('market') is not None or 'market' in svcs_lower or bool(s.get('hasMarket') or s.get('has_market', False)))
                    has_shipyard   = (s.get('shipyard') is not None or 'shipyard' in svcs_lower or bool(s.get('hasShipyard') or s.get('has_shipyard', False)))
                    has_outfitting = (s.get('outfitting') is not None or 'outfitting' in svcs_lower or bool(s.get('hasOutfitting') or s.get('has_outfitting', False)))

                    sta_batch.append((
                        sid, sys_id64,
                        sta_name,
                        station_type_from_record(s),
                        station_distance_from_record(s),
                        station_body_name_from_record(s),
                        'L' if landing_pads.get('large') else ('M' if landing_pads.get('medium') else s.get('landing_pad_size')),
                        has_market,
                        has_shipyard,
                        has_outfitting,
                        'refuel' in svcs_lower,
                        'repair' in svcs_lower,
                        'restock' in svcs_lower or 'rearm' in svcs_lower,
                        'black market' in svcs_lower,
                        'material trader' in svcs_lower,
                        'technology broker' in svcs_lower,
                        'interstellar factors contact' in svcs_lower or 'interstellar factors' in svcs_lower,
                        'universal cartographics' in svcs_lower,
                        'search and rescue' in svcs_lower,
                        norm_economy(s.get('primaryEconomy') or s.get('primary_economy')),
                        norm_economy(s.get('secondaryEconomy') or s.get('secondary_economy')),
                        s.get('controllingFaction') or s.get('controlling_faction'),
                        norm_allegiance(s.get('allegiance')),
                        norm_government(s.get('government')),
                        parse_ts(s.get('updateTime') or s.get('updated_at')) or now_iso,
                    ))
                except Exception as _e:
                    skip_count += 1
                    log.debug(f"Skipping station id={sid} in system {sys_id64}: {_e}")
                    log_import_error(conn, dump_path.name, sid, 'station', _e)
                    continue

                total_rows += 1

                if len(sta_batch) >= BATCH_SIZE:
                    flush()

                if time.time() - last_save > 60:
                    flush()
                    flush_error_batch(conn, dump_path.name)
                    save_checkpoint_safely(conn, dump_path.name, 0, total_rows + resume_offset, f_raw.tell())
                    last_save = time.time()
                    pbar.n = f_raw.tell()
                    pbar.refresh()

        except KeyboardInterrupt:
            log.info("Interrupted — saving checkpoint ...")
            flush()
            flush_error_batch(conn, dump_path.name)
            save_checkpoint(conn, dump_path.name, 0, total_rows + resume_offset, f_raw.tell())
            sys.exit(0)

        flush()
        flush_error_batch(conn, dump_path.name)
        if skip_count > 0:
            increment_error_count(conn, dump_path.name, skip_count)
        pbar.close()

    if skip_count:
        log.warning(f"Skipped {skip_count:,} malformed station records")
    mark_complete(conn, dump_path.name, total_rows)
    log.info(f"galaxy_stations.json.gz complete: {total_rows:,} stations imported")
    return total_rows


# ---------------------------------------------------------------------------
# IMPORTER 4 — systems delta files (1day / 1week / 1month)
# ---------------------------------------------------------------------------
def import_systems_delta(conn, dump_path: Path, resume_offset: int = 0) -> int:
    """Import a Spansh systems delta file (flat list of system objects)."""
    log.info(f"Importing systems delta from {dump_path.name} ...")
    set_import_optimisations(conn)

    file_size = dump_path.stat().st_size
    mark_running(conn, dump_path.name, file_size)

    SYS_COLS = [
        'id64', 'name', 'x', 'y', 'z',
        'primary_economy', 'secondary_economy',
        'population', 'security', 'allegiance', 'government',
        'controlling_faction', 'galaxy_region_id',
        'updated_at', 'rating_dirty', 'cluster_dirty',
    ]

    # Confirmed by direct inspection of real systems_1day.json.gz (84,118
    # records) and systems_1week.json.gz (978,213 records) on 2026-08-06:
    # every record in both files has exactly {coords, id64, mainStar,
    # name, updateTime} - these seven fields are never present, 0 of
    # over 1M records checked across both files. Without this exclusion,
    # norm_economy/norm_security/norm_allegiance/norm_government's
    # missing-input fallback to 'Unknown' and _parse_system_population's
    # fallback to None meant every conflict on this import path was
    # unconditionally overwriting an existing system's real economy,
    # population, security, government, allegiance, and controlling
    # faction - confirmed as the active, ongoing worst case of F-014
    # (docs/audits/round6-report.md), not just a latent risk: this
    # import runs nightly (1-day) and weekly (1-week) per
    # scripts/nightly_update.sh. Still included in SYS_COLS/the INSERT
    # so a brand-new system seen for the first time still gets a
    # baseline value - only excluded from update_cols so an existing
    # system's already-known values are never touched by this source.
    DELTA_NEVER_PROVIDES = {
        'primary_economy', 'secondary_economy', 'population',
        'security', 'allegiance', 'government', 'controlling_faction',
    }

    sys_batch  = []
    total_rows = 0
    last_save  = time.time()

    def flush():
        if sys_batch:
            upsert_via_temp(conn, 'systems', SYS_COLS, sys_batch, 'id64',
                            update_cols=[
                                c for c in SYS_COLS
                                if c != 'id64' and c not in DELTA_NEVER_PROVIDES
                            ])
            sys_batch.clear()

    with open(dump_path, 'rb') as f_raw, gzip.open(f_raw, 'rb') as f:
        pbar = tqdm(
            total=file_size, initial=0,
            unit='B', unit_scale=True, unit_divisor=1024,
            desc=dump_path.name,
        )

        _rows_skipped = 0
        try:
            for sys_obj in ijson.items(f, 'item'):
                if _rows_skipped < resume_offset:
                    _rows_skipped += 1
                    continue

                id64 = sys_obj.get('id64')
                name = _require_name(sys_obj, id64)
                if name is None:
                    continue

                now_iso = datetime.now(timezone.utc).isoformat()

                controlling = sys_obj.get('controllingFaction') or sys_obj.get('controlling_faction')
                if not controlling:
                    for fac in (sys_obj.get('factions') or []):
                        if fac.get('isControlling'):
                            controlling = fac.get('name')
                            break

                try:
                    sx, sy, sz = _extract_system_coords(sys_obj)
                    region_id = find_galaxy_region(sx, sz) if sx is not None else None
                except Exception:
                    sx, sy, sz = None, None, None
                    region_id = None

                sys_batch.append((
                    id64,
                    name,
                    sx, sy, sz,
                    norm_economy(sys_obj.get('primaryEconomy') or sys_obj.get('primary_economy')),
                    norm_economy(sys_obj.get('secondaryEconomy') or sys_obj.get('secondary_economy')),
                    _parse_system_population(sys_obj),
                    norm_security(sys_obj.get('security')),
                    norm_allegiance(sys_obj.get('allegiance')),
                    norm_government(sys_obj.get('government')),
                    controlling,
                    region_id,
                    now_iso,
                    True,
                    True,
                ))

                total_rows += 1

                if len(sys_batch) >= BATCH_SIZE:
                    flush()

                if time.time() - last_save > 60:
                    flush()
                    save_checkpoint_safely(conn, dump_path.name, 0, total_rows + resume_offset, f_raw.tell())
                    last_save = time.time()
                    pbar.n = f_raw.tell()
                    pbar.refresh()

        except KeyboardInterrupt:
            flush()
            save_checkpoint(conn, dump_path.name, 0, total_rows + resume_offset, f_raw.tell())
            sys.exit(0)

        flush()
        pbar.close()

    mark_complete(conn, dump_path.name, total_rows)
    log.info(f"{dump_path.name} complete: {total_rows:,} systems updated")
    return total_rows


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------
def _download_with_aria2(url: str, dest: Path) -> bool:
    import shutil
    import subprocess
    if not shutil.which('aria2c'):
        return False
    log.info("  Using aria2c (16 parallel connections) ...")
    cmd = [
        'aria2c', '--continue=true',
        '--split=16', '--max-connection-per-server=16',
        '--min-split-size=10M', '--max-tries=5', '--retry-wait=10',
        '--file-allocation=falloc',
        '--dir', str(dest.parent), '--out', dest.name, url,
    ]
    return subprocess.run(cmd).returncode == 0


def _download_with_wget(url: str, dest: Path) -> bool:
    import shutil
    import subprocess
    if not shutil.which('wget'):
        return False
    log.info("  Using wget (resumable) ...")
    return subprocess.run(['wget', '--continue', '--show-progress', '-O', str(dest), url]).returncode == 0


def _download_with_curl(url: str, dest: Path) -> bool:
    import shutil
    import subprocess
    if not shutil.which('curl'):
        return False
    log.info("  Using curl (resumable) ...")
    return subprocess.run(['curl', '-L', '-C', '-', '--progress-bar', '-o', str(dest), url]).returncode == 0


def download_dumps(files: list):
    """Download Spansh dump files using the fastest available method."""
    import urllib.request
    DUMP_DIR.mkdir(parents=True, exist_ok=True)

    for fname in files:
        dest = DUMP_DIR / fname
        if dest.exists():
            log.info(f"Already exists: {fname} ({dest.stat().st_size / 1e9:.1f} GB) — skipping")
            continue

        url = f"{SPANSH_BASE}/{fname}"
        log.info(f"Downloading {url}")
        tmp = dest.with_suffix(dest.suffix + '.tmp')
        ok  = False

        for method in (_download_with_aria2, _download_with_wget, _download_with_curl):
            try:
                if method(url, tmp):
                    tmp.rename(dest)
                    log.info(f"✅ {fname}: {dest.stat().st_size / 1e9:.1f} GB")
                    ok = True
                    break
            except Exception as e:
                log.warning(f"  {method.__name__} failed: {e}")

        if not ok:
            log.warning("  Falling back to urllib ...")
            try:
                def _progress(bc, bs, total):
                    if total > 0:
                        print(f"\r  {fname}: {bc*bs/total*100:.1f}% ({bc*bs/1e9:.1f}/{total/1e9:.1f} GB)",
                              end='', flush=True)
                urllib.request.urlretrieve(url, tmp, reporthook=_progress)  # nosemgrep: dynamic-urllib-use-detected -- url is built from the hardcoded SPANSH_BASE for a fixed set of known dump filenames
                print()
                tmp.rename(dest)
                log.info(f"✅ {fname}: {dest.stat().st_size / 1e9:.1f} GB")
            except Exception as e:
                log.error(f"❌ Failed to download {fname}: {e}")
                tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------
def show_status(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT dump_file, status, rows_processed, bytes_processed,
                   bytes_total, errors_encountered,
                   CASE WHEN bytes_total > 0
                       THEN round(bytes_processed::numeric / bytes_total * 100, 1)
                       ELSE 0 END AS pct,
                   started_at, completed_at, error_message
            FROM import_meta ORDER BY id
        """)
        rows = cur.fetchall()

    print(f"\n{'File':<35} {'Status':<10} {'Rows':>12} {'Errors':>8} {'Progress':>10} {'Started':<22}")
    print("-" * 105)
    for r in rows:
        fname, status, rows_proc, bytes_proc, bytes_total, errors, pct, started, completed, err = r
        started_str = started.strftime('%Y-%m-%d %H:%M') if started else 'not started'
        pct_str = f"{pct}%" if pct else "0%"
        err_str = f"{errors:,}" if errors else "0"
        print(f"{fname:<35} {str(status):<10} {(rows_proc or 0):>12,} {err_str:>8} {pct_str:>10} {started_str:<22}")
        if err:
            print(f"  LAST ERROR: {err[:80]}")
    print()


def show_errors(conn, limit: int = 50):
    """Show recent structured import errors from the import_errors table."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT dump_file, record_type, record_id, error_class,
                   error_message, occurred_at
            FROM import_errors
            ORDER BY occurred_at DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()

    if not rows:
        print("\nNo import errors recorded.")
        return

    print(f"\n{'Time':<22} {'File':<30} {'Type':<10} {'ID':>15} {'Error'}")
    print("-" * 110)
    for r in rows:
        dump_file, rec_type, rec_id, err_class, err_msg, occurred = r
        t = occurred.strftime('%Y-%m-%d %H:%M:%S') if occurred else '?'
        rid = str(rec_id) if rec_id else 'N/A'
        print(f"{t:<22} {dump_file:<30} {rec_type:<10} {rid:>15}  [{err_class}] {err_msg[:60]}")
    print()


# ---------------------------------------------------------------------------
# Importer dispatch map
# ---------------------------------------------------------------------------
IMPORTER_MAP = {
    'galaxy.json.gz':           import_galaxy,
    'galaxy_populated.json.gz': import_populated,
    'galaxy_stations.json.gz':  import_stations,
    'systems_1day.json.gz':     import_systems_delta,
    'systems_1week.json.gz':    import_systems_delta,
    'systems_1month.json.gz':   import_systems_delta,
}

IMPORT_ORDER = [
    'galaxy.json.gz',
    'galaxy_populated.json.gz',
    'galaxy_stations.json.gz',
]


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------
def main() -> int:
    global DUMP_DIR, BATCH_SIZE

    parser = argparse.ArgumentParser(
        description='ED Finder — Spansh dump importer v3.0 (PostgreSQL COPY + region lookup)'
    )
    parser.add_argument('--all',           action='store_true', help='Import all dumps in order')
    parser.add_argument('--file',          type=str,            help='Import a specific dump file')
    parser.add_argument('--resume',        action='store_true', help='Resume from last checkpoint')
    parser.add_argument('--download',      action='store_true', help='Download dumps before importing')
    parser.add_argument('--download-only', action='store_true', help='Download dumps then exit')
    parser.add_argument('--status',        action='store_true', help='Show import status')
    parser.add_argument('--errors',        action='store_true', help='Show recent import errors from DB')
    parser.add_argument('--probe',         type=str,            help='Print field keys from dump file')
    parser.add_argument('--dump-dir',      type=str,            help=f'Dump directory (default: {DUMP_DIR})')
    parser.add_argument('--batch-size',    type=int,            help=f'Batch size for COPY (default: {BATCH_SIZE})')
    args = parser.parse_args()

    if args.dump_dir:
        DUMP_DIR = Path(args.dump_dir)
    if args.batch_size:
        BATCH_SIZE = args.batch_size

    conn = get_conn()

    if args.status:
        show_status(conn)
        return 0

    if args.errors:
        show_errors(conn)
        return 0

    if args.download or getattr(args, 'download_only', False):
        files = [args.file] if args.file else IMPORT_ORDER
        download_dumps(files)

    if getattr(args, 'download_only', False):
        log.info("Download complete. Run with --all (or --file) to import.")
        return 0

    files_to_import = IMPORT_ORDER if args.all else ([args.file] if args.file else [])
    if not files_to_import:
        parser.print_help()
        return 0

    def _get_status(fname: str) -> Optional[str]:
        try:
            with conn.cursor() as _cur:
                _cur.execute("SELECT status FROM import_meta WHERE dump_file = %s", (fname,))
                row = _cur.fetchone()
                return str(row[0]) if row else None
        except Exception:
            return None

    total_start = time.time()
    failures: list[str] = []
    for fname in files_to_import:
        dump_path = DUMP_DIR / fname
        if not dump_path.exists():
            log.error(f"Dump file not found: {dump_path}")
            log.error(f"Run with --download-only first, or place files in {DUMP_DIR}")
            failures.append(f"{fname}: dump file missing")
            continue

        importer_fn = IMPORTER_MAP.get(fname)
        if not importer_fn:
            log.error(f"No importer for: {fname}")
            failures.append(f"{fname}: no importer registered")
            continue

        current_status = _get_status(fname)
        if current_status == 'complete' and not args.resume:
            log.info(f"⏭  {fname}: already complete — skipping (use --resume to re-run)")
            continue

        auto_resume = (current_status == 'running') or args.resume
        resume_offset = get_checkpoint(conn, fname) if auto_resume else 0
        if resume_offset > 0:
            log.info(f"Auto-resuming {fname} from row {resume_offset:,} (status was: {current_status})")
        elif auto_resume and resume_offset == 0:
            log.info(f"No checkpoint found for {fname} — starting from beginning")

        start = time.time()
        try:
            rows = importer_fn(conn, dump_path, resume_offset)
            elapsed = time.time() - start
            log.info(f"✅ {fname}: {rows:,} rows in {elapsed/3600:.2f}h")
        except Exception as e:
            log.error(f"❌ {fname} failed: {e}", exc_info=True)
            mark_failed(conn, fname, str(e))
            failures.append(f"{fname}: {e}")

    total_elapsed = time.time() - total_start

    # Auto-rebuild indexes if they were dropped for the import
    if args.all or args.file:
        try:
            # Phase 1: check index count in its own committed read-transaction.
            # We must commit before changing conn.autocommit: Psycopg requires
            # the connection to be idle when switching transaction mode.
            with conn.cursor() as _cur:
                _cur.execute("SELECT count(*) FROM pg_indexes WHERE tablename = 'systems' AND indexname NOT LIKE '%pkey%'")
                idx_count = _cur.fetchone()[0]
            conn.commit()  # close the read transaction before touching autocommit

            if idx_count < 5:
                log.info("--- AUTOMATIC INDEX REBUILD ---")
                log.info("Indexes are missing (likely dropped for import). Starting rebuild from 002_indexes.sql ...")
                log.info("This will take 1-3 hours. Do not interrupt.")
                sql_path = resolve_index_sql_path()
                if sql_path.exists():
                    # Phase 2: run CREATE INDEX statements — requires autocommit mode.
                    # Only flip autocommit after the read transaction is fully closed.
                    old_autocommit = conn.autocommit
                    conn.autocommit = True
                    try:
                        with conn.cursor() as _idx_cur:
                            with open(sql_path, 'r') as sql_f:
                                sql_commands = sql_f.read()
                            _idx_cur.execute(sql_commands)
                        log.info("✅ Indexes rebuilt successfully.")
                    finally:
                        conn.autocommit = old_autocommit
                else:
                    log.error(f"Could not find {sql_path} — automatic index rebuild failed.")
                    failures.append(f"automatic index rebuild: missing {sql_path}")
            else:
                log.info(f"Indexes already exist ({idx_count} found) — skipping auto-rebuild.")
        except Exception as e:
            log.error(f"Failed to auto-rebuild indexes: {e}")
            failures.append(f"automatic index rebuild: {e}")

    if failures:
        log.error(
            f"Import run completed with {len(failures)} failure(s) in "
            f"{total_elapsed/3600:.2f} hours: {'; '.join(failures)}"
        )
    else:
        log.info(f"All imports complete in {total_elapsed/3600:.2f} hours")

    log.info("Next steps (run in this exact order):")
    log.info("  1. python3 build_grid.py      — build 500 LY + 2000 LY spatial grids (~1-2h)")
    log.info("  2. python3 build_ratings.py   — compute economy scores (~3-5h)")
    log.info("  3. python3 build_clusters.py  — build cluster summaries (~2-4h)")
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
