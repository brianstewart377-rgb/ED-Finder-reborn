"""Station sighting model, state hash and change-only writer.

Source-agnostic: the EDDN worker writes ``source='eddn'`` rows through
``record_sightings``; the Spansh one-shot loader is meant to reuse the same
model and writer with ``source='spansh'``.

A row is written only when a station's research state changes. "Current
state" is the latest stored record for the same ``(source, market_id)``,
ordered by the source's own timestamp. Records older than the current state
are counted as stale and not written, so a late, out-of-order message can never
masquerade as a change.

Nothing here accepts, stores or logs an uploader identifier: the model has no
field for one.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

OBSERVER_ROLE = 'v3_station_observer'
SOURCES = ('spansh', 'eddn')
STATE_HASH_VERSION = 'v3_observe.station_state/1'

MAX_TEXT = 128
MAX_TYPE_TEXT = 64
MAX_ECONOMIES = 8
MAX_SERVICES = 128
MAX_PAD_COUNT = 255
MAX_BODY_ID = 4095
MAX_DISTANCE_LS = 1e9
MAX_ID = (1 << 63) - 1
MAX_BATCH_ROWS = 5000
PROPORTION_DECIMALS = 4
EARLIEST_RECORD = datetime(2014, 1, 1, tzinfo=UTC)
# A source time may lead the collector's receipt time by at most this much.
# Mirrors the CHECK constraints: one far-future row would otherwise win
# station_current forever and make every later sighting stale.
MAX_FUTURE_SKEW = timedelta(hours=1)

# Session guards for every writer connection: never block DDL, a purge fence
# or a migration for long.
LOCK_TIMEOUT = '2s'
STATEMENT_TIMEOUT = '15s'

# Advisory-lock namespace serialising writers of one source, so two writers
# for the same source cannot both decide a state is new.
_LOCK_NAMESPACE = 0x0B5E_0017


class SightingRejected(ValueError):
    """A record failed validation. ``reason`` is a short, bounded label."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Economy:
    name: str
    proportion: float


@dataclass(frozen=True, slots=True)
class StationSighting:
    source: str
    market_id: int
    system_id64: int
    system_name: str | None
    station_name: str
    station_type: str | None
    economies: tuple[Economy, ...]
    services: tuple[str, ...]
    landing_pads: tuple[tuple[str, int], ...] | None
    body_id: int | None
    body_name: str | None
    distance_ls: float | None
    source_record_ts: datetime
    observed_at: datetime
    gateway_ts: datetime | None = None
    software_name: str | None = None
    software_version: str | None = None
    game_version: str | None = None
    game_build: str | None = None

    @property
    def state_sha256(self) -> bytes:
        return state_sha256(self)

    def economies_json(self) -> str:
        return json.dumps(
            [{'name': e.name, 'proportion': e.proportion} for e in self.economies],
            separators=(',', ':'),
        )

    def landing_pads_json(self) -> str | None:
        if self.landing_pads is None:
            return None
        return json.dumps(dict(self.landing_pads), separators=(',', ':'), sort_keys=True)


def _bounded_text(value: object, reason: str, *, limit: int = MAX_TEXT,
                  required: bool = False) -> str | None:
    if value is None:
        if required:
            raise SightingRejected(reason)
        return None
    if not isinstance(value, str):
        raise SightingRejected(reason)
    text = value.strip()
    if not text:
        if required:
            raise SightingRejected(reason)
        return None
    if len(text) > limit or any(ord(ch) < 32 for ch in text):
        raise SightingRejected(reason)
    try:
        # json.loads accepts escaped lone surrogates (e.g. \ud800); they would
        # only fail later, when hashing or sending the batch to PostgreSQL.
        text.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise SightingRejected(reason) from exc
    return text


def _positive_id(value: object, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= MAX_ID:
        raise SightingRejected(reason)
    return value


def _aware(value: object, reason: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SightingRejected(reason)
    value = value.astimezone(UTC)
    if value < EARLIEST_RECORD:
        raise SightingRejected(reason)
    return value


def build_sighting(
    *,
    source: str,
    market_id: object,
    system_id64: object,
    station_name: object,
    station_type: object,
    economies: Iterable[tuple[object, object]],
    services: Iterable[object],
    landing_pads: dict[str, object] | None,
    source_record_ts: datetime,
    observed_at: datetime,
    system_name: object = None,
    body_id: object = None,
    body_name: object = None,
    distance_ls: object = None,
    gateway_ts: datetime | None = None,
    software_name: object = None,
    software_version: object = None,
    game_version: object = None,
    game_build: object = None,
) -> StationSighting:
    """Validate and canonicalise one sighting. Raises ``SightingRejected``."""
    if source not in SOURCES:
        raise SightingRejected('invalid_source')

    economy_items: dict[str, float] = {}
    for count, (name, proportion) in enumerate(economies, start=1):
        if count > MAX_ECONOMIES:
            raise SightingRejected('invalid_economies')
        label = _bounded_text(name, 'invalid_economies', required=True)
        if isinstance(proportion, bool) or not isinstance(proportion, (int, float)):
            raise SightingRejected('invalid_economies')
        share = float(proportion)
        if not math.isfinite(share) or not 0.0 <= share <= 1.0 or label in economy_items:
            raise SightingRejected('invalid_economies')
        # "+ 0.0" turns -0.0 into 0.0, so equal states always hash equally.
        economy_items[label] = round(share, PROPORTION_DECIMALS) + 0.0
    canonical_economies = tuple(
        Economy(name, share) for name, share in sorted(economy_items.items()))

    service_items: set[str] = set()
    for count, service in enumerate(services, start=1):
        if count > MAX_SERVICES:
            raise SightingRejected('invalid_services')
        service_items.add(_bounded_text(service, 'invalid_services', required=True))

    pads: tuple[tuple[str, int], ...] | None = None
    if landing_pads is not None:
        if not isinstance(landing_pads, dict):
            raise SightingRejected('invalid_landing_pads')
        normalised: dict[str, int] = {}
        for key, value in landing_pads.items():
            size = key.casefold() if isinstance(key, str) else None
            if (size not in {'small', 'medium', 'large'} or size in normalised
                    or isinstance(value, bool) or not isinstance(value, int)
                    or not 0 <= value <= MAX_PAD_COUNT):
                raise SightingRejected('invalid_landing_pads')
            normalised[size] = value
        # A partial snapshot is unknown, not "these pads were removed".
        if set(normalised) != {'small', 'medium', 'large'}:
            raise SightingRejected('incomplete_state')
        pads = tuple(sorted(normalised.items()))

    if body_id is not None and (isinstance(body_id, bool) or not isinstance(body_id, int)
                                or not 0 <= body_id <= MAX_BODY_ID):
        raise SightingRejected('invalid_body')
    distance: float | None = None
    if distance_ls is not None:
        if isinstance(distance_ls, bool) or not isinstance(distance_ls, (int, float)):
            raise SightingRejected('invalid_distance')
        distance = float(distance_ls)
        if not math.isfinite(distance) or not 0.0 <= distance <= MAX_DISTANCE_LS:
            raise SightingRejected('invalid_distance')

    if source == 'spansh' and any(v is not None for v in (
            gateway_ts, software_name, software_version, game_build)):
        raise SightingRejected('invalid_source_fields')
    if source == 'eddn' and (body_id is not None or body_name is not None):
        raise SightingRejected('invalid_source_fields')

    sighting = StationSighting(
        source=source,
        market_id=_positive_id(market_id, 'invalid_market_id'),
        system_id64=_positive_id(system_id64, 'invalid_system_id64'),
        system_name=_bounded_text(system_name, 'invalid_system_name'),
        station_name=_bounded_text(station_name, 'invalid_station_name', required=True),
        station_type=_bounded_text(station_type, 'invalid_station_type', limit=MAX_TYPE_TEXT),
        economies=canonical_economies,
        services=tuple(sorted(service_items)),
        landing_pads=pads,
        body_id=body_id,
        body_name=_bounded_text(body_name, 'invalid_body'),
        distance_ls=distance,
        source_record_ts=_aware(source_record_ts, 'invalid_timestamp'),
        observed_at=_aware(observed_at, 'invalid_timestamp'),
        gateway_ts=None if gateway_ts is None else _aware(gateway_ts, 'invalid_timestamp'),
        software_name=_bounded_text(software_name, 'invalid_header'),
        software_version=_bounded_text(software_version, 'invalid_header'),
        game_version=_bounded_text(game_version, 'invalid_header', limit=MAX_TYPE_TEXT),
        game_build=_bounded_text(game_build, 'invalid_header', limit=MAX_TYPE_TEXT),
    )
    limit = sighting.observed_at + MAX_FUTURE_SKEW
    if sighting.source_record_ts > limit or (
            sighting.gateway_ts is not None and sighting.gateway_ts > limit):
        raise SightingRejected('invalid_timestamp')
    return sighting


def state_sha256(sighting: StationSighting) -> bytes:
    """Hash of the research state only.

    Covers the system (megaships move but keep their MarketID), name, type,
    economies (sorted, proportions rounded), services (sorted, deduplicated),
    landing pads and body. Deliberately excludes timestamps, distance (it
    drifts with orbital position), system name text and header fields, so
    background churn never creates a row.
    """
    state = {
        'v': STATE_HASH_VERSION,
        'system_id64': sighting.system_id64,
        'station_name': sighting.station_name,
        'station_type': sighting.station_type,
        'economies': [[e.name, format(e.proportion, f'.{PROPORTION_DECIMALS}f')]
                      for e in sighting.economies],
        'services': list(sighting.services),
        'landing_pads': None if sighting.landing_pads is None else dict(sighting.landing_pads),
        'body_id': sighting.body_id,
        'body_name': sighting.body_name,
    }
    encoded = json.dumps(state, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(encoded.encode('utf-8')).digest()


@dataclass(frozen=True, slots=True)
class WriteResult:
    inserted: int = 0
    unchanged: int = 0
    stale: int = 0


def configure_observer_session(conn) -> None:
    """Drop to the restricted role and set the session guards.

    Call once per connection, on an existing application credential. The role
    name and timeouts are constants, never caller input.
    """
    from psycopg import sql

    conn.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(OBSERVER_ROLE)))
    conn.execute(sql.SQL('SET lock_timeout = {}').format(sql.Literal(LOCK_TIMEOUT)))
    conn.execute(sql.SQL('SET statement_timeout = {}').format(sql.Literal(STATEMENT_TIMEOUT)))
    if not conn.autocommit:
        conn.commit()


_CURRENT_SQL = """
    SELECT market_id, state_sha256, source_record_ts
    FROM v3_observe.station_current
    WHERE source = %s::v3_observe.sighting_source AND market_id = ANY(%s::bigint[])
"""

_INSERT_SQL = """
    INSERT INTO v3_observe.station_sighting (
        source, market_id, system_id64, system_name, station_name, station_type,
        economies, services, landing_pads, body_id, body_name, distance_ls,
        state_sha256, source_record_ts, gateway_ts, observed_at,
        software_name, software_version, game_version, game_build)
    VALUES (%s::v3_observe.sighting_source, %s, %s, %s, %s, %s,
            %s::jsonb, %s::text[], %s::jsonb, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s)
"""


def _row(s: StationSighting, digest: bytes) -> tuple:
    return (
        s.source, s.market_id, s.system_id64, s.system_name, s.station_name, s.station_type,
        s.economies_json(), list(s.services), s.landing_pads_json(), s.body_id, s.body_name,
        s.distance_ls, digest, s.source_record_ts, s.gateway_ts, s.observed_at,
        s.software_name, s.software_version, s.game_version, s.game_build,
    )


class SeenWatermarks:
    """Bounded in-process high-water mark of source time per (source, market_id).

    An unchanged sighting writes no row, so the database alone cannot know that
    state A was re-observed at t3 after being stored at t1. Without this, a
    delayed frame with state B from t2 would compare only against t1 and wrongly
    become current. The mark advances for every accepted sighting (changed or
    unchanged) once its batch commits. It is least-recently-used and bounded;
    after a restart it starts empty, so protection then rests on the stored
    rows plus the normaliser's event-age window.
    """

    def __init__(self, max_entries: int):
        if max_entries < 1:
            raise ValueError('max_entries must be positive')
        self.max_entries = max_entries
        self._marks: OrderedDict[tuple[str, int], datetime] = OrderedDict()

    def __len__(self) -> int:
        return len(self._marks)

    def get(self, source: str, market_id: int) -> datetime | None:
        key = (source, market_id)
        mark = self._marks.get(key)
        if mark is not None:
            self._marks.move_to_end(key)
        return mark

    def advance(self, source: str, market_id: int, ts: datetime) -> None:
        key = (source, market_id)
        mark = self._marks.get(key)
        self._marks[key] = ts if mark is None or ts > mark else mark
        self._marks.move_to_end(key)
        while len(self._marks) > self.max_entries:
            self._marks.popitem(last=False)


def record_sightings(conn, sightings: Sequence[StationSighting], *,
                     seen: SeenWatermarks | None = None) -> WriteResult:
    """Write the changed sightings of one batch in a single transaction.

    Bulk-write safety: this deliberately does not use
    shared_contracts.bulk_update_helper.bulk_update_replica_mode. It only
    appends to v3_observe.station_sighting; it never updates systems, bodies,
    clusters or ratings, and the table's CHECK constraints and append-only
    triggers must stay enforced. Never run it in replica mode.

    The connection must already be configured by ``configure_observer_session``
    (verified here, fail closed). On any error the transaction rolls back and
    the exception propagates; nothing is partially written.

    A sighting older than the latest known source time for its station (stored
    row, an earlier sighting in this batch, or ``seen``) is stale and skipped.
    Unchanged sightings advance that time, so a delayed frame cannot overwrite
    a state that was re-observed later.
    """
    if len(sightings) > MAX_BATCH_ROWS:
        raise ValueError(f'batch exceeds {MAX_BATCH_ROWS} rows')
    if not sightings:
        return WriteResult()
    from psycopg.pq import TransactionStatus

    # transaction() inside an open transaction would only be a savepoint and
    # would not commit; require a real top-level transaction per batch.
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError('record_sightings needs an idle connection (one transaction per batch)')
    inserted = unchanged = stale = 0
    advanced: list[tuple[str, int, datetime]] = []
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute('SELECT current_user')
            if cur.fetchone()[0] != OBSERVER_ROLE:
                raise PermissionError(f'writer session is not running as {OBSERVER_ROLE}')
            by_source: dict[str, list[tuple[int, StationSighting]]] = {}
            for index, sighting in enumerate(sightings):
                by_source.setdefault(sighting.source, []).append((index, sighting))
            for source in sorted(by_source):
                cur.execute('SELECT pg_advisory_xact_lock(%s, %s)',
                            (_LOCK_NAMESPACE, SOURCES.index(source)))
                items = sorted(by_source[source],
                               key=lambda pair: (pair[1].source_record_ts, pair[0]))
                market_ids = sorted({s.market_id for _, s in items})
                cur.execute(_CURRENT_SQL, (source, market_ids))
                stored = cur.fetchall()
                current = {mid: bytes(digest) for mid, digest, _ in stored}
                latest = {mid: ts for mid, _, ts in stored}
                if seen is not None:
                    for mid in market_ids:
                        mark = seen.get(source, mid)
                        if mark is not None and (mid not in latest or mark > latest[mid]):
                            latest[mid] = mark
                pending = []
                for _, sighting in items:
                    digest = sighting.state_sha256
                    mid, ts = sighting.market_id, sighting.source_record_ts
                    if mid in latest and ts < latest[mid]:
                        stale += 1
                        continue
                    latest[mid] = ts
                    advanced.append((source, mid, ts))
                    if current.get(mid) == digest:
                        unchanged += 1
                        continue
                    pending.append(_row(sighting, digest))
                    current[mid] = digest
                if pending:
                    cur.executemany(_INSERT_SQL, pending)
                    inserted += len(pending)
    if seen is not None:
        for source, mid, ts in advanced:
            seen.advance(source, mid, ts)
    return WriteResult(inserted=inserted, unchanged=unchanged, stale=stale)


def record_sighting(conn, sighting: StationSighting, *,
                    seen: SeenWatermarks | None = None) -> WriteResult:
    """Single-row convenience wrapper over ``record_sightings``."""
    return record_sightings(conn, [sighting], seen=seen)
