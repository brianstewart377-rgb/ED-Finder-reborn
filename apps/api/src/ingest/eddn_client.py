"""
ED Finder — Ingest: EDDN Async Client for API Process
=======================================================
Lightweight async background task that ingests EDDN events relevant to
the simulation engine (Scan, FSSBodySignals, SAASignalsFound) and writes
them into journal_events + body_scan_facts.

Design rules:
  • Runs as a background asyncio.Task inside the FastAPI lifespan, gated
    behind EDDN_SIMULATION_INGEST_ENABLED (default ON as of 2026-08-07 —
    see config.py). Wired up 2026-08-07 after sitting unreferenced since
    its original commit; until then this docstring's claims were
    aspirational, not actual. Kept as a flag (not unconditional) so it
    can be switched off via env var without a redeploy if ever needed.
  • Does NOT block any request handler.
  • Uses the existing asyncpg pool — no separate connection.
  • Reconnects automatically on ZMQ disconnect.
  • Batches DB writes every FLUSH_INTERVAL seconds.
  • All errors are logged and swallowed — never crashes the API process.

NOTE: The existing apps/eddn/eddn_listener.py handles the full event
spectrum (systems, bodies, stations, dirty flags). This client handles
ONLY the simulation-relevant event types for body_scan_facts ingestion.
They can run side-by-side without conflict — this client writes to
different tables (journal_events, body_scan_facts).

NOTE: journal_events/body_scan_facts also have an independent, already-
live write path — the client-side journal-import lane
(apps/api/src/journal_import/store.py). Enabling this task adds a
second, separate feed into the same tables; it does not replace or
depend on that one.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import zlib
from typing import Optional, TYPE_CHECKING

from edfinder_api.evidence_store.store import promote_canonical_evidence_for_systems
from edfinder_api.ring_facts import ring_rows_for_body
from shared_contracts.body_ring_association_status import BODY_RING_ASSOCIATION_STATUS_CASE_SQL

if TYPE_CHECKING:
    import asyncpg

log = logging.getLogger('ed_finder')

EDDN_RELAY       = 'tcp://eddn.edcd.io:9500'
FLUSH_INTERVAL   = 15    # seconds between DB batch flushes
MAX_BATCH_SIZE   = 200   # flush early if batch exceeds this
RECONNECT_DELAY  = 5     # seconds between reconnect attempts

# Freshness tracking, mirroring apps/eddn/src/eddn_listener.py's
# eddn_seconds_since_flush / EddnFlushStalled pattern rather than
# introducing a second monitoring mechanism for what's architecturally the
# same kind of thing (a long-lived EDDN consumer). Set at import time
# (process start), not left unset, so a freshly-started process doesn't
# immediately read as stalled before its first flush cycle completes.
#
# Liveness semantics (tightened 2026-10-19 after the July 2026 outage
# shape — "the loop kept cycling, every flush failed, the gauge never
# moved"): the timestamp advances on a *successful* flush, and on a
# received message only while the write path is healthy (no consecutive
# flush failures outstanding). A failed flush never advances it, so a
# broken write path lets seconds_since_last_flush() grow and trip the
# existing EddnSimulationIngestFlushStalled alert instead of hiding
# behind "the loop is still cycling".
_last_flush_at: float = time.time()

# Write-path health. `_last_successful_flush_at` is 0.0 until the first
# successful DB flush of this process (Prometheus gauge: unix timestamp;
# 0 means "never"). `_flush_failures_total` is monotonic for the process
# lifetime; `_consecutive_flush_failures` resets on the next success.
_last_successful_flush_at: float = 0.0
# `_last_flush_attempt_at` is set when a non-empty batch *starts* flushing
# (success or failure, and before a hung write would block). Together with
# `_last_successful_flush_at` it tells "a relevant batch has been attempted
# since the last success" apart from "the relevant feed has simply been
# idle" — an idle feed leaves both timestamps alone, a failing or hung write
# path leaves the attempt timestamp ahead of the success one.
_last_flush_attempt_at: float = 0.0
_flush_failures_total: int = 0
_consecutive_flush_failures: int = 0

# Bounds for EDDN_SIMULATION_INGEST_MAX_CONSECUTIVE_FLUSH_FAILURES (the
# configured value is read via config.Settings; these clamp any value that
# reaches this module so a misconfiguration cannot disable the breaker).
MIN_CONSECUTIVE_FLUSH_FAILURES = 1
MAX_CONSECUTIVE_FLUSH_FAILURES = 1000
DEFAULT_MAX_CONSECUTIVE_FLUSH_FAILURES = 5


class EddnFlushFailureThresholdExceeded(RuntimeError):
    """Raised out of the ingest loop once N consecutive flushes have failed.

    The outer `run_eddn_simulation_ingest` loop catches it, logs it at
    warning level and reconnects after RECONNECT_DELAY. The pending batch
    is dropped (the same outcome as a single failed flush today) but the
    loop stops pretending to be live: `_last_flush_at` is not advanced on
    the way out, and the metrics below carry the count.
    """


def seconds_since_last_flush() -> float:
    return time.time() - _last_flush_at


def last_successful_flush_timestamp() -> float:
    """Unix timestamp of the last successful DB flush, or 0.0 if none yet."""
    return _last_successful_flush_at


def last_flush_attempt_timestamp() -> float:
    """Unix timestamp at which the last non-empty flush started, or 0.0."""
    return _last_flush_attempt_at


def flush_failures_total() -> int:
    return _flush_failures_total


def consecutive_flush_failures() -> int:
    return _consecutive_flush_failures


def max_consecutive_flush_failures() -> int:
    """Clamped breaker threshold from settings (falls back to the default)."""
    try:
        from edfinder_api.config import settings
        configured = int(settings.eddn_simulation_ingest_max_consecutive_flush_failures)
    except Exception:
        configured = DEFAULT_MAX_CONSECUTIVE_FLUSH_FAILURES
    return max(MIN_CONSECUTIVE_FLUSH_FAILURES, min(MAX_CONSECUTIVE_FLUSH_FAILURES, configured))


def _record_flush_success() -> None:
    global _last_flush_at, _last_successful_flush_at, _consecutive_flush_failures
    now = time.time()
    _last_flush_at = now
    _last_successful_flush_at = now
    _consecutive_flush_failures = 0


def _record_flush_attempt() -> None:
    global _last_flush_attempt_at
    _last_flush_attempt_at = time.time()


def _record_flush_failure() -> int:
    """Count a failed flush; returns the new consecutive-failure count."""
    global _flush_failures_total, _consecutive_flush_failures
    _flush_failures_total += 1
    _consecutive_flush_failures += 1
    return _consecutive_flush_failures


def _write_path_healthy() -> bool:
    return _consecutive_flush_failures == 0


DIRTY_MARK_BATCH_SIZE = 500
DIRTY_MARK_STATEMENT_TIMEOUT_MS = 5000

BODY_RING_UPSERT_SQL = f"""
    INSERT INTO body_rings (
        system_id64, body_id, source_body_id, body_name,
        ring_name, ring_type, ring_class,
        mass_mt, inner_radius, outer_radius,
        source, confidence, association_status, updated_at
    ) VALUES (
        $1, $2, $3, $4,
        $5, $6, $7,
        $8, $9, $10,
        $11, $12, {BODY_RING_ASSOCIATION_STATUS_CASE_SQL}, now()
    )
    ON CONFLICT (system_id64, body_id, ring_name, source) DO UPDATE SET
        source_body_id = COALESCE(EXCLUDED.source_body_id, body_rings.source_body_id),
        body_name    = COALESCE(EXCLUDED.body_name, body_rings.body_name),
        ring_type    = COALESCE(EXCLUDED.ring_type, body_rings.ring_type),
        ring_class   = COALESCE(EXCLUDED.ring_class, body_rings.ring_class),
        mass_mt      = COALESCE(EXCLUDED.mass_mt, body_rings.mass_mt),
        inner_radius = COALESCE(EXCLUDED.inner_radius, body_rings.inner_radius),
        outer_radius = COALESCE(EXCLUDED.outer_radius, body_rings.outer_radius),
        confidence   = EXCLUDED.confidence,
        association_status = {BODY_RING_ASSOCIATION_STATUS_CASE_SQL},
        updated_at   = now()
    WHERE body_rings.source_body_id IS DISTINCT FROM COALESCE(EXCLUDED.source_body_id, body_rings.source_body_id)
       OR body_rings.body_name IS DISTINCT FROM COALESCE(EXCLUDED.body_name, body_rings.body_name)
       OR body_rings.ring_type IS DISTINCT FROM COALESCE(EXCLUDED.ring_type, body_rings.ring_type)
       OR body_rings.ring_class IS DISTINCT FROM COALESCE(EXCLUDED.ring_class, body_rings.ring_class)
       OR body_rings.mass_mt IS DISTINCT FROM COALESCE(EXCLUDED.mass_mt, body_rings.mass_mt)
       OR body_rings.inner_radius IS DISTINCT FROM COALESCE(EXCLUDED.inner_radius, body_rings.inner_radius)
       OR body_rings.outer_radius IS DISTINCT FROM COALESCE(EXCLUDED.outer_radius, body_rings.outer_radius)
       OR body_rings.confidence IS DISTINCT FROM EXCLUDED.confidence
       OR body_rings.association_status IS DISTINCT FROM {BODY_RING_ASSOCIATION_STATUS_CASE_SQL}
"""

# Event types we process for the simulation engine
SIMULATION_EVENT_TYPES = {'Scan', 'FSSBodySignals', 'SAASignalsFound'}


def _command_row_count(status: str | None) -> int:
    if not status:
        return 0
    try:
        return int(str(status).rsplit(' ', 1)[-1])
    except (TypeError, ValueError):
        return 0


def _chunks(values: list[int], size: int):
    size = max(1, int(size))
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _is_probable_belt_source(*, source_body_id: Optional[int], body_name: Optional[str], ring_name: Optional[str]) -> bool:
    if source_body_id == 0:
        return True
    haystack = ' '.join(part for part in (body_name, ring_name) if part).lower()
    return ' belt' in haystack or haystack.endswith('belt')


async def run_eddn_simulation_ingest(pool: 'asyncpg.Pool') -> None:
    """
    Async entry point. Call as:
        asyncio.create_task(run_eddn_simulation_ingest(pool))

    Runs indefinitely — handles reconnects internally.
    Catches all exceptions to prevent crashing the API process.
    """
    log.info('EDDN simulation ingest task started')
    while True:
        try:
            await _run_ingest_loop(pool)
        except asyncio.CancelledError:
            log.info('EDDN simulation ingest task cancelled — shutting down')
            raise
        except EddnFlushFailureThresholdExceeded as e:
            log.warning(
                'EDDN simulation ingest write path unhealthy: %s — reconnecting in %ss',
                e, RECONNECT_DELAY,
            )
            await asyncio.sleep(RECONNECT_DELAY)
        except Exception as e:
            log.warning(f'EDDN simulation ingest loop error: {e} — reconnecting in {RECONNECT_DELAY}s')
            await asyncio.sleep(RECONNECT_DELAY)


async def _run_ingest_loop(pool: 'asyncpg.Pool') -> None:
    """Single connection lifetime. Reconnects on exception."""
    global _last_flush_at
    try:
        import zmq
        import zmq.asyncio as azmq
    except ImportError:
        log.warning('pyzmq not available — EDDN simulation ingest disabled')
        # Sleep forever rather than fast-loop on ImportError
        await asyncio.sleep(86400)
        return

    ctx    = azmq.Context()
    socket = ctx.socket(zmq.SUB)
    socket.setsockopt(zmq.SUBSCRIBE, b'')
    socket.setsockopt(zmq.RCVTIMEO, 30_000)
    socket.connect(EDDN_RELAY)

    log.info(f'EDDN simulation ingest connected to {EDDN_RELAY}')

    # Pending batch accumulator
    pending_journal: list[dict] = []
    pending_facts:   list[dict] = []
    last_flush = time.monotonic()

    try:
        while True:
            # ── Receive ───────────────────────────────────────────────────
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=35.0)
            except (asyncio.TimeoutError, zmq.Again):
                # Quiet feed — no message within RCVTIMEO / the wait_for window.
                # pyzmq raises zmq.Again on the socket RCVTIMEO (30s), which fires
                # before the 35s wait_for; it is a normal quiet-feed cycle, not an
                # error. Flush what we have and reconnect.
                break
            except Exception as e:
                # ZMQ / decode-layer error — flush what we have and reconnect.
                log.debug(f'EDDN recv error: {e}')
                break

            # A successful receive proves the connection is alive and data is
            # flowing — even a non-simulation event we filter out below. Advance
            # the liveness timestamp here so a busy feed carrying only
            # non-simulation events does not look stalled, while a genuinely
            # dead/silent connection (recv keeps timing out or erroring, never
            # reaching this line) correctly lets seconds_since_last_flush() grow
            # and trip the stall alarm.
            #
            # Only while the write path is healthy: once a flush has failed and
            # not yet been followed by a success, receiving more messages we
            # cannot persist is not liveness, and advancing here is exactly how
            # the July 2026 outage stayed invisible for 13 days.
            if _write_path_healthy():
                _last_flush_at = time.time()

            # ── Decode ────────────────────────────────────────────────────
            try:
                data  = zlib.decompress(raw)
                frame = json.loads(data)
            except Exception:
                continue

            schema = frame.get('$schemaRef', '')
            if 'journal' not in schema.lower():
                continue

            message    = frame.get('message', {})
            event_type = message.get('event', '')

            if event_type not in SIMULATION_EVENT_TYPES:
                continue

            # ── Normalise ─────────────────────────────────────────────────
            try:
                from edfinder_api.ingest.journal_normaliser import (
                    build_journal_event_row,
                    event_type_to_normaliser,
                )
                journal_row = build_journal_event_row(message, event_type, source='eddn')
                pending_journal.append(journal_row)

                normaliser = event_type_to_normaliser(event_type)
                if normaliser:
                    fact = normaliser(message)
                    if fact:
                        pending_facts.append(fact)

            except Exception as e:
                log.debug(f'EDDN normalise error ({event_type}): {e}')
                continue

            # ── Flush check ───────────────────────────────────────────────
            now = time.monotonic()
            if (now - last_flush >= FLUSH_INTERVAL
                    or len(pending_journal) >= MAX_BATCH_SIZE):
                flushed = await _flush_batch(pool, pending_journal, pending_facts)
                pending_journal.clear()
                pending_facts.clear()
                last_flush = now
                # Liveness advances only on a successful flush (see
                # _record_flush_success). A failed flush leaves the gauge
                # where it was; `_flush_batch` has already counted it.
                _raise_if_flush_threshold_exceeded(flushed)

    finally:
        # Final flush before reconnect. On the normal exit paths (quiet-feed
        # timeout, recv error → break) nothing is propagating and the
        # breaker applies exactly as it does in the loop; when an exception
        # is already in flight (cancellation, or the in-loop breaker) we
        # must not raise from here, because that would replace it.
        try:
            await _flush_pending_on_exit(
                pool, pending_journal, pending_facts,
                exception_in_flight=sys.exception() is not None,
            )
        finally:
            socket.close()
            ctx.term()


async def _flush_pending_on_exit(
    pool: 'asyncpg.Pool',
    pending_journal: list[dict],
    pending_facts: list[dict],
    *,
    exception_in_flight: bool,
) -> None:
    """Flush what is pending when the connection loop exits.

    Liveness is refreshed by `_flush_batch` itself when, and only when, the
    write succeeded. Never refresh it unconditionally here: a dead
    connection reaches this with nothing pending, and a failed final flush
    is not liveness either. A failed final flush counts towards the
    consecutive-failure breaker like any other — a low-volume outage where
    every batch is flushed from this path must still trip it — unless an
    exception is already propagating out of the loop.
    """
    if not pending_journal and not pending_facts:
        return
    flushed = False
    try:
        flushed = await _flush_batch(pool, pending_journal, pending_facts)
    except Exception as e:
        log.warning(f'EDDN final flush error: {e}')
    if flushed:
        return
    log.debug('EDDN final flush did not succeed; liveness not refreshed')
    if exception_in_flight:
        return
    _raise_if_flush_threshold_exceeded(False)


def _raise_if_flush_threshold_exceeded(flushed: bool) -> None:
    """Trip the breaker once N consecutive flushes have failed."""
    if flushed:
        return
    failures = _consecutive_flush_failures
    limit = max_consecutive_flush_failures()
    if failures >= limit:
        raise EddnFlushFailureThresholdExceeded(
            f'{failures} consecutive EDDN flush failures (limit {limit})'
        )


async def _flush_batch(
    pool: 'asyncpg.Pool',
    journal_rows: list[dict],
    fact_rows: list[dict],
) -> bool:
    """
    Write a batch of journal events and body_scan_facts to DB.

    Returns True when the batch was committed, False when the write failed.
    Errors are logged but do not propagate — the caller decides whether the
    consecutive-failure count has crossed the configured threshold. An
    empty batch is a no-op and returns True (nothing to fail).
    """
    if not journal_rows and not fact_rows:
        return True

    _record_flush_attempt()
    dirty_system_ids: set[int] = set()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # ── journal_events ─────────────────────────────────────────
                if journal_rows:
                    await conn.executemany("""
                        INSERT INTO journal_events
                            (system_address, system_name, body_id, body_name,
                             event_type, event_timestamp, source, raw_event)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """, [
                        (
                            r.get('system_address'),
                            r.get('system_name'),
                            r.get('body_id'),
                            r.get('body_name'),
                            r['event_type'],
                            r.get('event_timestamp'),
                            r['source'],
                            json.dumps(r['raw_event']),
                        )
                        for r in journal_rows
                    ])

                # ── body_scan_facts ────────────────────────────────────────
                if fact_rows:
                    source_ring_rows = _ring_rows_from_scan_facts(fact_rows)
                    ring_rows, unresolved_ring_rows = await _resolve_eddn_ring_rows(conn, source_ring_rows)
                    if unresolved_ring_rows:
                        reason_counts: dict[str, int] = {}
                        for row in unresolved_ring_rows:
                            reason = str(row.get('reason') or 'unknown')
                            reason_counts[reason] = reason_counts.get(reason, 0) + 1
                        log.info(
                            'EDDN flush skipped unresolved ring rows',
                            extra={
                                'rings_skipped_unmatched_body': reason_counts.get('local_body_not_found_by_name', 0),
                                'rings_skipped_ambiguous_body': reason_counts.get('local_body_name_not_unique', 0),
                                'rings_skipped_belt_source': reason_counts.get('belt_source_evidence', 0),
                                'rings_skipped_other': sum(
                                    count for reason, count in reason_counts.items()
                                    if reason not in {
                                        'local_body_not_found_by_name',
                                        'local_body_name_not_unique',
                                        'belt_source_evidence',
                                    }
                                ),
                            },
                        )
                    await conn.executemany("""
                        INSERT INTO body_scan_facts (
                            system_address, body_id, body_name,
                            radius, mass_em, gravity,
                            surface_temp, surface_pressure,
                            planet_class, terraform_state,
                            atmosphere, volcanism,
                            semi_major_axis, orbital_period, parents,
                            has_geo, has_bio,
                            geo_signal_count, bio_signal_count,
                            is_landable, is_terraformable, is_ringed,
                            data_sources, confidence, updated_at
                        ) VALUES (
                            $1, $2, $3,
                            $4, $5, $6,
                            $7, $8,
                            $9, $10,
                            $11, $12,
                            $13, $14, $15,
                            $16, $17,
                            $18, $19,
                            $20, $21, $22,
                            $23, $24, now()
                        )
                        ON CONFLICT (system_address, body_id) DO UPDATE SET
                            body_name        = COALESCE(EXCLUDED.body_name, body_scan_facts.body_name),
                            radius           = COALESCE(EXCLUDED.radius, body_scan_facts.radius),
                            mass_em          = COALESCE(EXCLUDED.mass_em, body_scan_facts.mass_em),
                            gravity          = COALESCE(EXCLUDED.gravity, body_scan_facts.gravity),
                            surface_temp     = COALESCE(EXCLUDED.surface_temp, body_scan_facts.surface_temp),
                            surface_pressure = COALESCE(EXCLUDED.surface_pressure, body_scan_facts.surface_pressure),
                            planet_class     = COALESCE(EXCLUDED.planet_class, body_scan_facts.planet_class),
                            terraform_state  = COALESCE(EXCLUDED.terraform_state, body_scan_facts.terraform_state),
                            atmosphere       = COALESCE(EXCLUDED.atmosphere, body_scan_facts.atmosphere),
                            volcanism        = COALESCE(EXCLUDED.volcanism, body_scan_facts.volcanism),
                            semi_major_axis  = COALESCE(EXCLUDED.semi_major_axis, body_scan_facts.semi_major_axis),
                            orbital_period   = COALESCE(EXCLUDED.orbital_period, body_scan_facts.orbital_period),
                            parents          = COALESCE(EXCLUDED.parents, body_scan_facts.parents),
                            has_geo          = GREATEST(EXCLUDED.has_geo, body_scan_facts.has_geo),
                            has_bio          = GREATEST(EXCLUDED.has_bio, body_scan_facts.has_bio),
                            geo_signal_count = GREATEST(EXCLUDED.geo_signal_count, body_scan_facts.geo_signal_count),
                            bio_signal_count = GREATEST(EXCLUDED.bio_signal_count, body_scan_facts.bio_signal_count),
                            is_landable      = COALESCE(EXCLUDED.is_landable, body_scan_facts.is_landable),
                            is_terraformable = COALESCE(EXCLUDED.is_terraformable, body_scan_facts.is_terraformable),
                            is_ringed        = CASE
                                WHEN body_scan_facts.is_ringed IS TRUE OR EXCLUDED.is_ringed IS TRUE THEN TRUE
                                WHEN EXCLUDED.is_ringed IS FALSE THEN FALSE
                                ELSE body_scan_facts.is_ringed
                            END,
                            -- Take higher confidence value, merge sources
                            confidence       = GREATEST(EXCLUDED.confidence, body_scan_facts.confidence),
                            data_sources     = (
                                SELECT ARRAY(
                                    SELECT DISTINCT unnest(
                                        body_scan_facts.data_sources || EXCLUDED.data_sources
                                    )
                                )
                            ),
                            updated_at       = now()
                    """, [
                        (
                            r['system_address'], r['body_id'],
                            r.get('body_name'),
                            r.get('radius'), r.get('mass_em'), r.get('gravity'),
                            r.get('surface_temp'), r.get('surface_pressure'),
                            r.get('planet_class'), r.get('terraform_state'),
                            r.get('atmosphere'), r.get('volcanism'),
                            r.get('semi_major_axis'), r.get('orbital_period'),
                            json.dumps(r['parents']) if r.get('parents') else None,
                            r.get('has_geo', False), r.get('has_bio', False),
                            r.get('geo_signal_count', 0), r.get('bio_signal_count', 0),
                            r.get('is_landable', False), r.get('is_terraformable', False),
                            r.get('is_ringed'),
                            r.get('data_sources', []),
                            r.get('confidence', 0.4),
                        )
                        for r in fact_rows
                    ])
                    for row in ring_rows:
                        status = await conn.execute(BODY_RING_UPSERT_SQL, *_ring_row_tuple(row))
                        if _command_row_count(status):
                            dirty_system_ids.add(int(row['system_id64']))

            if dirty_system_ids:
                dirty_result = await _mark_dirty_systems_incremental(conn, dirty_system_ids)
                if dirty_result['failed']:
                    log.warning(
                        'EDDN dirty marking failed for one or more batches',
                        extra=dirty_result,
                    )
                evidence_promotion = await promote_canonical_evidence_for_systems(
                    conn,
                    system_ids=dirty_system_ids,
                    evidence_types=['body_completeness', 'ring_composition'],
                    trigger_context='eddn_simulation_ingest',
                )
                if evidence_promotion['warnings']:
                    log.debug(
                        'EDDN canonical evidence promotion warnings',
                        extra={'warnings': evidence_promotion['warnings']},
                    )

        log.debug(
            f'EDDN flush: {len(journal_rows)} journal events, '
            f'{len(fact_rows)} body_scan_facts'
        )
        _record_flush_success()
        return True

    except Exception as e:
        consecutive = _record_flush_failure()
        log.warning(
            'EDDN flush failed (%s consecutive, %s total this process): %s',
            consecutive, _flush_failures_total, e,
        )
        return False


def _ring_rows_from_scan_facts(fact_rows: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for fact in fact_rows:
        source_body_id = int(fact['body_id']) if fact.get('body_id') is not None else None
        source_rows, _explicit_no_rings = ring_rows_for_body(
            {'rings': fact.get('rings') or []},
            system_id64=int(fact['system_address']),
            body_id=None,
            body_name=fact.get('body_name'),
            source='eddn_scan',
            source_body_id=source_body_id,
            trusted_empty_means_no_rings=False,
        )
        rows.extend(source_rows)
    return rows


async def _resolve_eddn_ring_rows(conn: 'asyncpg.Connection', rows: list[dict]) -> tuple[list[dict], list[dict]]:
    if not rows:
        return [], []

    system_ids = sorted({int(row['system_id64']) for row in rows if row.get('system_id64') is not None})
    body_names = sorted({str(row['body_name']) for row in rows if row.get('body_name')})
    if not system_ids or not body_names:
        return _resolve_ring_rows_with_local_bodies(rows, [])

    local_bodies = await conn.fetch("""
        SELECT system_id64, id, name
          FROM bodies
         WHERE system_id64 = ANY($1::bigint[])
           AND name = ANY($2::text[])
    """, system_ids, body_names)
    return _resolve_ring_rows_with_local_bodies(rows, local_bodies)


async def _mark_dirty_systems_incremental(
    conn: 'asyncpg.Connection',
    system_ids,
    *,
    batch_size: int = DIRTY_MARK_BATCH_SIZE,
) -> dict[str, int | list[int]]:
    ordered_ids = sorted({int(value) for value in system_ids if value is not None})
    result: dict[str, int | list[int]] = {
        'requested': len(ordered_ids),
        'marked': 0,
        'already_dirty': 0,
        'skipped_missing': 0,
        'failed': 0,
        'failed_ids': [],
    }
    for chunk in _chunks(ordered_ids, batch_size):
        try:
            async with conn.transaction():
                await conn.execute(
                    f"SET LOCAL statement_timeout = {max(1, int(DIRTY_MARK_STATEMENT_TIMEOUT_MS))}"
                )
                row = await conn.fetchrow("""
                    WITH requested AS (
                        SELECT unnest($1::bigint[]) AS id64
                    ),
                    existing AS (
                        SELECT s.id64, s.rating_dirty
                          FROM systems s
                          JOIN requested r ON r.id64 = s.id64
                    ),
                    updated AS (
                        UPDATE systems s
                           SET rating_dirty = TRUE,
                               updated_at = NOW()
                          FROM requested r
                         WHERE s.id64 = r.id64
                           AND s.rating_dirty IS DISTINCT FROM TRUE
                        RETURNING s.id64
                    )
                    SELECT
                        (SELECT COUNT(*)::int FROM updated) AS marked,
                        (SELECT COUNT(*)::int FROM existing WHERE rating_dirty IS TRUE) AS already_dirty,
                        (
                            (SELECT COUNT(*)::int FROM requested)
                            - (SELECT COUNT(*)::int FROM existing)
                        ) AS skipped_missing
                """, chunk)
            result['marked'] = int(result['marked']) + int(row['marked'] or 0)
            result['already_dirty'] = int(result['already_dirty']) + int(row['already_dirty'] or 0)
            result['skipped_missing'] = int(result['skipped_missing']) + int(row['skipped_missing'] or 0)
        except Exception:
            failed_ids = result['failed_ids']
            assert isinstance(failed_ids, list)
            failed_ids.extend(chunk)
            result['failed'] = int(result['failed']) + len(chunk)
    return result


def _resolve_ring_rows_with_local_bodies(
    rows: list[dict],
    local_bodies,
) -> tuple[list[dict], list[dict]]:
    local_by_name: dict[tuple[int, str], list[dict]] = {}
    for body in local_bodies:
        system_id64 = body.get('system_id64') if isinstance(body, dict) else body['system_id64']
        body_name = body.get('name') if isinstance(body, dict) else body['name']
        body_id = body.get('id') if isinstance(body, dict) else body['id']
        if system_id64 is None or not body_name or body_id is None:
            continue
        key = (int(system_id64), str(body_name))
        local_by_name.setdefault(key, []).append({
            'system_id64': int(system_id64),
            'id': int(body_id),
            'name': str(body_name),
        })

    resolved: list[dict] = []
    skipped: list[dict] = []
    for row in rows:
        body_name = row.get('body_name')
        if _is_probable_belt_source(
            source_body_id=row.get('source_body_id'),
            body_name=body_name,
            ring_name=row.get('ring_name'),
        ):
            skipped.append({**row, 'reason': 'belt_source_evidence'})
            continue
        if not row.get('ring_name'):
            skipped.append({**row, 'reason': 'missing_ring_identity'})
            continue
        if row.get('system_id64') is None or not body_name:
            skipped.append({**row, 'reason': 'missing_system_or_body_name'})
            continue
        matches = local_by_name.get((int(row['system_id64']), str(body_name)), [])
        if len(matches) != 1:
            skipped.append({
                **row,
                'reason': 'local_body_not_found_by_name' if not matches else 'local_body_name_not_unique',
            })
            continue
        match = matches[0]
        resolved.append({
            **row,
            'body_id': match['id'],
            'body_name': match['name'],
            'association_status': 'local_matched',
        })
    return resolved, skipped


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
    )
