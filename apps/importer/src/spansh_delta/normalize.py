"""Normalize one Spansh system to V3 canonical content and classify changes.

The canonical rows come from the frozen V3 importer adapter
(``v3_spansh.adapter.SpanshAdapter.adapt_system``) and its locked COPY column
contract (``v3_spansh.contracts.COPY_COLUMNS``).  Both are imported, never
copied or edited, so a delta layer sees exactly the rows a full import would
write for the same Spansh record.

"Content" is every stored column except run/provenance bookkeeping (source run,
observation and freshness timestamps, missing/retired lineage).  Two admission
policies exist because real Spansh data shows that re-scanning an unchanged
system rewrites two stored columns every time (see the fixture test against the
2026-08-24 production base):

* ``strict`` -- every V3-stored content column.
* ``stable`` -- ``strict`` minus :data:`ORBITAL_PHASE_COLUMNS`, the orbital
  position at scan time (mean anomaly and distance from arrival).  Those move
  on every scan without any body, ring, signal or station changing.

Which policy admits a system into a nightly layer is an owner decision (D3 in
the canonical-refresh study); the measurement CLI reports both.  Fleet carrier
rows can additionally be excluded (``include_carriers=False``), which is the
other half of D3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import struct
from typing import Any, Iterable, Mapping
from uuid import UUID

from v3_spansh.adapter import SpanshAdapter
from v3_spansh.contracts import COPY_COLUMNS

POLICIES = ("strict", "stable")

# Tables the frozen Spansh adapter writes for a system.  ``body_alias`` and
# ``station_alias`` are part of the canonical schema but the Spansh adapter
# never emits them, so they cannot change through a Spansh delta.
CONTENT_TABLES: tuple[str, ...] = (
    "systems", "bodies", "body_composition", "body_parent_evidence", "rings",
    "body_signal_current", "body_genus_current", "ring_signal_current",
    "stations", "station_placement", "station_service_current",
    "station_economy_current",
)

PROVENANCE_COLUMNS = frozenset({
    "source_id", "source_run_id", "source_updated_at", "freshness_checked_at",
    "last_present_run_id", "last_present_at", "first_missing_run_id",
    "first_missing_at", "last_missing_run_id", "retired_at", "retired_reason",
    "observed_at",
})

ORBITAL_PHASE_COLUMNS = frozenset({
    ("bodies", "mean_anomaly_deg"),
    ("bodies", "distance_from_arrival_ls"),
    ("stations", "distance_from_arrival_ls"),
})

# Stored as PostgreSQL ``real``; compare at that precision on both sides.
FLOAT4_COLUMNS = frozenset({
    ("body_composition", "ice_pct"),
    ("body_composition", "metal_pct"),
    ("body_composition", "rock_pct"),
})

JSON_COLUMNS = frozenset({
    ("body_composition", "materials"),
    ("body_composition", "atmosphere"),
    ("body_parent_evidence", "detail"),
    ("station_placement", "evidence_detail"),
})

IDENTITY_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "systems": ("id64",),
    "bodies": ("body_pk",),
    "body_composition": ("body_pk",),
    "body_parent_evidence": ("body_pk", "source_parent_ordinal"),
    "rings": ("ring_pk",),
    "body_signal_current": ("body_pk", "signal_type_id"),
    "body_genus_current": ("body_pk", "genus_id"),
    "ring_signal_current": ("ring_pk", "signal_type_id"),
    "stations": ("station_pk",),
    "station_placement": ("station_pk",),
    "station_service_current": ("station_pk", "station_service_id"),
    "station_economy_current": ("station_pk", "economy_id"),
}

STATION_KEYED_TABLES = frozenset({
    "stations", "station_placement", "station_service_current", "station_economy_current",
})

CONTENT_COLUMNS: Mapping[str, tuple[str, ...]] = {
    table: tuple(column for column in COPY_COLUMNS[table] if column not in PROVENANCE_COLUMNS)
    for table in CONTENT_TABLES
}

# The adapter only uses these for provenance columns, which are dropped.
_ADAPTER = SpanshAdapter(
    source_id=0,
    source_run_id=UUID(int=0),
    observed_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
)

Row = tuple[Any, ...]


def _float4(value: float) -> float | int:
    """The shortest decimal that round-trips PostgreSQL ``real`` storage."""
    packed = struct.pack("<f", value)
    stored = struct.unpack("<f", packed)[0]
    for digits in range(1, 10):
        candidate = float(f"{stored:.{digits}g}")
        if struct.pack("<f", candidate) == packed:
            return _plain(candidate)
    return _plain(stored)


def _plain(value: Any) -> Any:
    """Canonical JSON-compatible value: integral floats compare as integers."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite canonical value")
        return int(value) if value.is_integer() else value
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (datetime, UUID)):
        return str(value)
    # Decimal (numeric) and anything else exact: go through its text form.
    number = float(value)
    return int(number) if number.is_integer() else number


def _content_value(table: str, column: str, value: Any) -> Any:
    if value is None:
        return None
    if (table, column) in JSON_COLUMNS:
        if isinstance(value, (str, bytes)):
            value = json.loads(value)
        return _plain(value)
    if (table, column) in FLOAT4_COLUMNS and not isinstance(value, bool):
        return _float4(float(value))
    return _plain(value)


def _content_row(table: str, values: Mapping[str, Any]) -> Row:
    missing = [column for column in CONTENT_COLUMNS[table] if column not in values]
    if missing:
        raise ValueError(f"{table} row lacks content columns: {missing}")
    return tuple(_content_value(table, column, values[column]) for column in CONTENT_COLUMNS[table])


_ENCODER = json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False)


def _row_key(row: Row) -> str:
    return _ENCODER.encode(row)


# Hot path for adapter output: (COPY position, column, needs the slow path).
_ADAPTER_COLUMNS: Mapping[str, tuple[tuple[int, str, bool], ...]] = {
    table: tuple(
        (COPY_COLUMNS[table].index(column), column,
         (table, column) in JSON_COLUMNS or (table, column) in FLOAT4_COLUMNS)
        for column in CONTENT_COLUMNS[table]
    )
    for table in CONTENT_TABLES
}


def _adapter_row(table: str, values: tuple[Any, ...]) -> Row:
    result = []
    for position, column, slow in _ADAPTER_COLUMNS[table]:
        value = values[position]
        kind = type(value)
        if slow:
            result.append(_content_value(table, column, value))
        elif kind is float:
            result.append(int(value) if value.is_integer() else value)
        elif value is None or kind is int or kind is str or kind is bool:
            result.append(value)
        else:
            result.append(_plain(value))
    return tuple(result)


@dataclass(frozen=True)
class NormalizedSystem:
    """V3 canonical content of one Spansh system, provenance removed.

    ``tables`` maps each of :data:`CONTENT_TABLES` to its rows, each row a tuple
    in :data:`CONTENT_COLUMNS` order, sorted by its canonical JSON text
    (``row_json``, kept so digests need not re-encode).
    """

    id64: int
    name: str
    tables: Mapping[str, tuple[Row, ...]]
    fleet_carrier_station_pks: frozenset[int]
    unmapped_vocabulary: frozenset[tuple[str, str]]
    row_json: Mapping[str, tuple[str, ...]] = field(repr=False, compare=False, default_factory=dict)

    def count(self, table: str) -> int:
        return len(self.tables[table])

    @property
    def fixed_stations(self) -> int:
        return len(self.tables["stations"]) - len(self.fleet_carrier_station_pks)

    @property
    def fleet_carriers(self) -> int:
        return len(self.fleet_carrier_station_pks)


def normalize_system(record: Mapping[str, Any]) -> NormalizedSystem:
    """Map one raw Spansh system through the frozen V3 adapter.

    Raises ``ValueError`` exactly where the V3 import would reject the system
    (missing name or coordinates, conflicting Market IDs, invalid counts).
    """
    batch = _ADAPTER.adapt_system(dict(record))
    unexpected = set(batch.rows) - set(CONTENT_TABLES)
    if unexpected:
        raise ValueError(f"adapter emitted tables outside the content contract: {sorted(unexpected)}")
    tables: dict[str, tuple[Row, ...]] = {}
    texts: dict[str, tuple[str, ...]] = {}
    for table in CONTENT_TABLES:
        width = len(COPY_COLUMNS[table])
        keyed = []
        for values in batch.rows.get(table, ()):
            if len(values) != width:
                raise ValueError(f"adapter row width does not match COPY contract for {table}")
            row = _adapter_row(table, values)
            keyed.append((_row_key(row), row))
        keyed.sort(key=_first)
        tables[table] = tuple(row for _, row in keyed)
        texts[table] = tuple(text for text, _ in keyed)
    station_columns = CONTENT_COLUMNS["stations"]
    pk_index = station_columns.index("station_pk")
    carrier_index = station_columns.index("is_fleet_carrier")
    carriers = frozenset(row[pk_index] for row in tables["stations"] if row[carrier_index] is True)
    if len(tables["systems"]) != 1:
        raise ValueError("adapter did not emit exactly one systems row")
    system_row = tables["systems"][0]
    return NormalizedSystem(
        id64=int(record["id64"]),
        name=str(system_row[CONTENT_COLUMNS["systems"].index("name")]),
        tables=tables,
        fleet_carrier_station_pks=carriers,
        unmapped_vocabulary=frozenset(batch.unmapped),
        row_json=texts,
    )


def _first(item: tuple[str, Row]) -> str:
    return item[0]


def _policy_columns(policy: str) -> Mapping[str, tuple[int, ...]]:
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}")
    result = {}
    for table in CONTENT_TABLES:
        result[table] = tuple(
            index for index, column in enumerate(CONTENT_COLUMNS[table])
            if policy == "strict" or (table, column) not in ORBITAL_PHASE_COLUMNS
        )
    return result


_POLICY_COLUMNS = {policy: _policy_columns(policy) for policy in POLICIES}
_STATION_PK_INDEX = {table: CONTENT_COLUMNS[table].index("station_pk") for table in STATION_KEYED_TABLES}
_PHASE_TABLES = frozenset(table for table, _ in ORBITAL_PHASE_COLUMNS)


def _projected(tables: Mapping[str, Iterable[Row]], *, policy: str, include_carriers: bool,
               carriers: frozenset[int], only: Iterable[str] | None = None) -> dict[str, list[str]]:
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}")
    selected = _POLICY_COLUMNS[policy]
    result: dict[str, list[str]] = {}
    for table in (CONTENT_TABLES if only is None else only):
        indexes = selected[table]
        rows = []
        for row in tables[table]:
            if not include_carriers and table in STATION_KEYED_TABLES and \
                    row[_STATION_PK_INDEX[table]] in carriers:
                continue
            rows.append(_row_key(tuple(row[index] for index in indexes)))
        result[table] = sorted(rows)
    return result


DIGEST_NAMES = ("strict", "strict_excluding_carriers", "stable", "stable_excluding_carriers")


def content_digests(system: NormalizedSystem) -> dict[str, bytes]:
    """SHA-256 of the system's V3 content under every policy / carrier choice.

    Each table contributes its name and its rows' canonical JSON in sorted
    order, so the digest ignores row order and provenance.  Equal content gives
    equal digests across nights, runs and machines.
    """
    hashes = {name: hashlib.sha256() for name in DIGEST_NAMES}
    carriers = system.fleet_carrier_station_pks
    for table in CONTENT_TABLES:
        rows = system.tables[table]
        strict_texts = system.row_json.get(table)
        if strict_texts is None or len(strict_texts) != len(rows):
            strict_texts = tuple(sorted(_row_key(row) for row in rows))
            rows = tuple(sorted(rows, key=_row_key))
        filter_carriers = bool(carriers) and table in STATION_KEYED_TABLES
        pk = _STATION_PK_INDEX.get(table)
        strict_flags = [row[pk] in carriers for row in rows] if filter_carriers else None
        if table in _PHASE_TABLES:
            indexes = _POLICY_COLUMNS["stable"][table]
            stable_pairs = sorted(
                (_row_key(tuple(row[index] for index in indexes)),
                 filter_carriers and row[pk] in carriers)
                for row in rows
            )
            stable_texts = [text for text, _ in stable_pairs]
            stable_flags = [flag for _, flag in stable_pairs] if filter_carriers else None
        else:
            stable_texts, stable_flags = list(strict_texts), strict_flags
        for name, texts, flags, exclude in (
            ("strict", strict_texts, strict_flags, False),
            ("strict_excluding_carriers", strict_texts, strict_flags, True),
            ("stable", stable_texts, stable_flags, False),
            ("stable_excluding_carriers", stable_texts, stable_flags, True),
        ):
            if exclude and flags is not None:
                texts = [text for text, flag in zip(texts, flags, strict=True) if not flag]
            digest = hashes[name]
            digest.update(table.encode() + b"\x1e")
            digest.update("\n".join(texts).encode())
            digest.update(b"\x1d")
    return {name: digest.digest() for name, digest in hashes.items()}


def content_digest(system: NormalizedSystem, *, policy: str = "stable",
                   include_carriers: bool = True) -> bytes:
    """SHA-256 of the system's V3 content under one admission policy."""
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}")
    name = policy if include_carriers else f"{policy}_excluding_carriers"
    return content_digests(system)[name]


def base_rows_from_export(export: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    """Adapt a canonical export (``CanonicalSnapshot.export_chunk`` shape or the
    committed ``canonical.json`` fixtures) to ``{table: [column-keyed rows]}``.

    Only relations of the canonical generation schema are taken; vocabularies
    are ignored.  Tables the export does not contain are absent, and
    :func:`classify_material_change` refuses to compare them unless told to
    restrict the comparison with ``tables=``.
    """
    schema = export.get("canonical_schema")
    result: dict[str, list[Mapping[str, Any]]] = {}
    for table in ("systems", "bodies"):
        if table in export:
            result[table] = list(export[table])
    for extra in export.get("extras", ()):
        relation = extra.get("relation")
        if relation in CONTENT_TABLES and (schema is None or extra.get("schema") == schema):
            result[relation] = list(extra.get("rows", ()))
    return result


@dataclass(frozen=True)
class MaterialChange:
    """Admission verdict for one system against the version the app shows."""

    id64: int
    status: str  # "new" | "changed" | "unchanged"
    policy: str
    include_carriers: bool
    compared_tables: tuple[str, ...]
    changed_tables: tuple[str, ...]
    changed_columns: tuple[str, ...]
    rows_added: int
    rows_removed: int

    @property
    def admit(self) -> bool:
        return self.status != "unchanged"


def _base_for_system(id64: int, base_rows: Mapping[str, Iterable[Mapping[str, Any]]],
                     tables: tuple[str, ...]) -> dict[str, list[Mapping[str, Any]]]:
    """Select one system's rows from rows that may cover a whole chunk."""
    def rows(table: str) -> list[Mapping[str, Any]]:
        return list(base_rows.get(table, ()))

    systems = [row for row in rows("systems") if int(row["id64"]) == id64]
    bodies = [row for row in rows("bodies") if int(row["system_id64"]) == id64]
    body_pks = {int(row["body_pk"]) for row in bodies}
    rings = [row for row in rows("rings") if int(row["system_id64"]) == id64]
    ring_pks = {int(row["ring_pk"]) for row in rings}
    stations = [row for row in rows("stations") if int(row["system_id64"]) == id64]
    station_pks = {int(row["station_pk"]) for row in stations}
    selected: dict[str, list[Mapping[str, Any]]] = {
        "systems": systems, "bodies": bodies, "rings": rings, "stations": stations,
    }
    for table in tables:
        if table in selected:
            continue
        if table in ("body_composition", "body_signal_current", "body_genus_current"):
            selected[table] = [row for row in rows(table) if int(row["body_pk"]) in body_pks]
        elif table == "body_parent_evidence":
            selected[table] = [row for row in rows(table) if int(row["system_id64"]) == id64]
        elif table == "ring_signal_current":
            selected[table] = [row for row in rows(table) if int(row["ring_pk"]) in ring_pks]
        elif table == "station_placement":
            selected[table] = [row for row in rows(table) if int(row["system_id64"]) == id64]
        elif table in STATION_KEYED_TABLES:
            selected[table] = [row for row in rows(table) if int(row["station_pk"]) in station_pks]
    return selected


# Child tables are selected through their parent's keys, so the parent must
# be in the base export even when it is not itself compared.
_PARENT_TABLE = {
    "body_composition": "bodies",
    "body_signal_current": "bodies",
    "body_genus_current": "bodies",
    "ring_signal_current": "rings",
    "station_service_current": "stations",
    "station_economy_current": "stations",
}


def _keyed(projected: list[str], identity: list[int]) -> dict[str, list[Any]]:
    result = {}
    for text in projected:
        values = json.loads(text)
        result[_row_key(tuple(values[index] for index in identity))] = values
    return result


def classify_material_change(
    system: NormalizedSystem,
    base_rows: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    *,
    policy: str = "stable",
    include_carriers: bool = True,
    tables: Iterable[str] | None = None,
) -> MaterialChange:
    """Decide whether anything V3 stores changed for this system.

    ``base_rows`` holds column-keyed canonical rows (``to_jsonb`` / psycopg dict
    rows) of the generation the app currently shows; rows of other systems are
    ignored.  Without base rows -- or when the base has no row for the system --
    the system is ``new`` and is admitted.

    ``tables`` restricts the comparison (default: all :data:`CONTENT_TABLES`).
    Every compared table must be present in ``base_rows`` (an empty list means
    "no rows"); a missing table is refused rather than read as "no rows", which
    would turn an incomplete export into a false change.

    Faction, population, power, security, government, market and other fields
    the V3 canonical schema does not store never reach the comparison: the
    adapter does not map them.
    """
    compared = tuple(CONTENT_TABLES if tables is None else tables)
    unknown = set(compared) - set(CONTENT_TABLES)
    if unknown or not compared:
        raise ValueError(f"tables must be a non-empty subset of {CONTENT_TABLES}")
    if "systems" not in compared:
        raise ValueError("the systems table must be compared")

    def verdict(status: str, changed: tuple[str, ...] = (), columns: tuple[str, ...] = (),
                added: int = 0, removed: int = 0) -> MaterialChange:
        return MaterialChange(system.id64, status, policy, include_carriers, compared,
                              changed, columns, added, removed)

    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}")
    if base_rows is None:
        return verdict("new")
    needed = set(compared) | {_PARENT_TABLE[table] for table in compared if table in _PARENT_TABLE}
    if not include_carriers and set(compared) & STATION_KEYED_TABLES:
        needed.add("stations")
    missing = [table for table in CONTENT_TABLES if table in needed and table not in base_rows]
    if missing:
        raise ValueError(f"base rows lack compared tables: {missing}")
    base = _base_for_system(system.id64, base_rows, compared)
    if not base["systems"]:
        return verdict("new")
    if len(base["systems"]) != 1:
        raise ValueError(f"base holds {len(base['systems'])} rows for system {system.id64}")

    base_tables = {table: [_content_row(table, row) for row in base[table]] for table in compared}
    base_carriers = frozenset(
        int(row["station_pk"]) for row in base["stations"] if row.get("is_fleet_carrier") is True
    )
    # Each snapshot is filtered by its own carrier membership, as
    # content_digests does: a station that became (or stopped being) a fleet
    # carrier is a change to the carrier-free view.
    new_projection = _projected(system.tables, policy=policy, include_carriers=include_carriers,
                                carriers=system.fleet_carrier_station_pks, only=compared)
    base_projection = _projected(base_tables, policy=policy, include_carriers=include_carriers,
                                 carriers=base_carriers, only=compared)

    changed_tables: list[str] = []
    changed_columns: set[str] = set()
    added = removed = 0
    selected = _POLICY_COLUMNS[policy]
    for table in compared:
        if new_projection[table] == base_projection[table]:
            continue
        changed_tables.append(table)
        names = [CONTENT_COLUMNS[table][index] for index in selected[table]]
        identity = [names.index(column) for column in IDENTITY_COLUMNS[table]]
        new_rows = _keyed(new_projection[table], identity)
        base_rows_by_key = _keyed(base_projection[table], identity)
        added += len(new_rows.keys() - base_rows_by_key.keys())
        removed += len(base_rows_by_key.keys() - new_rows.keys())
        for key in new_rows.keys() & base_rows_by_key.keys():
            for position, (left, right) in enumerate(zip(new_rows[key], base_rows_by_key[key], strict=True)):
                if left != right:
                    changed_columns.add(f"{table}.{names[position]}")
        if len(new_rows) != len(new_projection[table]) or \
                len(base_rows_by_key) != len(base_projection[table]):
            changed_columns.add(f"{table}.<duplicate identity>")
    if not changed_tables:
        return verdict("unchanged")
    return verdict("changed", tuple(changed_tables), tuple(sorted(changed_columns)), added, removed)


# -- timestamp profile (no base needed) -------------------------------------

def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _signal_time(entity: Mapping[str, Any]) -> object:
    signals = entity.get("signals")
    return signals.get("updateTime") if isinstance(signals, dict) else None


def _is_carrier(station: Mapping[str, Any]) -> bool:
    station_type = station.get("type") or station.get("stationType")
    return isinstance(station_type, str) and station_type.strip().casefold() in {
        "fleet carrier", "drake-class carrier",
    }


@dataclass(frozen=True)
class TouchProfile:
    """Which parts of a raw record carry update times inside a window.

    A cheap, base-free proxy for admission: a system whose only in-window
    timestamp is the system ``date`` was most likely touched for faction /
    population / power state, which V3 does not store.  It over-counts real
    change (re-scans and market updates also bump timestamps); the base or
    previous-night comparisons are the precise measures.
    """

    system_time: datetime | None
    latest_body_time: datetime | None
    latest_fixed_station_time: datetime | None
    latest_carrier_time: datetime | None
    # bodies (body, body signal, ring or belt signal times) | fixed_stations |
    # carriers_only | system_only | outside_window | no_timestamps
    category: str

    @property
    def latest_v3_time(self) -> datetime | None:
        values = [value for value in (self.latest_body_time, self.latest_fixed_station_time,
                                      self.latest_carrier_time) if value is not None]
        return max(values) if values else None


def record_touch_profile(record: Mapping[str, Any], *, window_start: datetime | None) -> TouchProfile:
    """Profile the update times of one raw Spansh system record."""
    system_time = _parse_time(record.get("date")) or _parse_time(record.get("updateTime"))
    body_times: list[datetime] = []
    fixed_times: list[datetime] = []
    carrier_times: list[datetime] = []
    stations: list[Mapping[str, Any]] = [s for s in (record.get("stations") or []) if isinstance(s, dict)]
    for body in record.get("bodies") or []:
        if not isinstance(body, dict):
            continue
        values = [body.get("updateTime"), _signal_time(body)]
        # Ring and belt signal inventories map to ring_signal_current.
        for ring in [*(body.get("rings") or []), *(body.get("belts") or [])]:
            if isinstance(ring, dict):
                values.append(ring.get("updateTime"))
                values.append(_signal_time(ring))
        for value in values:
            parsed = _parse_time(value)
            if parsed is not None:
                body_times.append(parsed)
        stations.extend(s for s in (body.get("stations") or []) if isinstance(s, dict))
    for station in stations:
        parsed = _parse_time(station.get("updateTime"))
        if parsed is not None:
            (carrier_times if _is_carrier(station) else fixed_times).append(parsed)
    latest_body = max(body_times) if body_times else None
    latest_fixed = max(fixed_times) if fixed_times else None
    latest_carrier = max(carrier_times) if carrier_times else None

    def inside(value: datetime | None) -> bool:
        return value is not None and (window_start is None or value >= window_start)

    if inside(latest_body):
        category = "bodies"
    elif inside(latest_fixed):
        category = "fixed_stations"
    elif inside(latest_carrier):
        category = "carriers_only"
    elif inside(system_time):
        category = "system_only"
    elif system_time is None and latest_body is None and latest_fixed is None and latest_carrier is None:
        category = "no_timestamps"
    else:
        category = "outside_window"
    return TouchProfile(system_time, latest_body, latest_fixed, latest_carrier, category)
