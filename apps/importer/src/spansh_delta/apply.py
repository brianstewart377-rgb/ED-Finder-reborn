"""Bring the galaxy tables up to date from a Spansh galaxy file.

    python -m spansh_delta.apply check FILE    read only: say what would change
    python -m spansh_delta.apply apply FILE    make the changes

FILE is one of Spansh's ``galaxy*.json.gz`` files (normally ``galaxy_1day``).
The database is found the standard libpq way, through the ``PG*`` environment
variables (PGHOST, PGUSER, PGDATABASE, ...). If EDFINDER_GALAXY_USE_DATABASE_URL
is 1, the API's own DATABASE_URL setting is used instead. No connection string
is read from the command line and none is ever printed.

For every system in the file:

  1. Its rows are worked out with the same adapter the full import used
     (``v3_spansh.adapter``), so they are exactly what a full import would
     store.
  2. They are compared with the rows we hold. Identical: nothing is written.
  3. Different or new: the system's rows are replaced, and the system is added
     to ``v3_state.rescore_queue`` in the same transaction, so its scores and
     Find row get rebuilt by the re-score job.

How rows are replaced:

  * ``systems``, ``bodies``, ``rings`` and ``stations`` rows are inserted or
    updated in place. A body, ring or station that the file no longer lists is
    kept and marked RETIRED, never deleted: every reader already skips rows
    that are not ACTIVE, and nothing that points at the row has to be touched.
    If it is listed again later it simply becomes ACTIVE again.
  * The small detail tables (composition, signals, genera, station services,
    economies and placement) are deleted and re-inserted for the system.
  * A fleet carrier that moved keeps its one row, which moves with it. The
    system it left is queued too, because its station count changed.

A file never overwrites what a newer file wrote: a system is skipped when its
stored rows came from a file made after this one.

Systems are handled in batches, one transaction each. If the database refuses
a row in a batch, its systems are retried one at a time, so one bad system is
set aside (and listed in the report) without stopping the rest. Any other
database error stops the run. Running the same file twice is safe:
the second run finds everything already up to date.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterator, Mapping, Sequence
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

from v3_spansh.adapter import VOCABULARY, CanonicalBatch, SpanshAdapter
from v3_spansh.contracts import COPY_COLUMNS, NORMALIZER_VERSION

from .normalize import (
    CONTENT_TABLES,
    IDENTITY_COLUMNS,
    NormalizedSystem,
    classify_material_change,
    normalize_system,
)
from .stream import SpanshStream

UPDATER_VERSION = "galaxy-update-1"
# Exit codes of the command: 0 done, 1 failed, 2 refused (nothing changed),
# 3 could not connect to the database.
EXIT_NO_CONNECTION = 3
# Every statement here is a lookup by key that takes well under a second. One
# that runs this long means the database is not answering as expected: stop.
STATEMENT_TIMEOUT = "15min"
BATCH_SYSTEMS = 200
PROGRESS_SECONDS = 60
# Session-level advisory lock: only one update may write at a time.
LOCK_KEY = int.from_bytes(hashlib.sha256(b"edfinder.galaxy_update").digest()[:8], "big", signed=True)
RETIRED_REASON = "absent from the Spansh system record"
QUEUE_TABLE = "v3_state.rescore_queue"

# Rows other rows point at. They are updated in place and never deleted.
PARENT_KEY = {"bodies": "body_pk", "rings": "ring_pk", "stations": "station_pk"}
# Detail rows nothing points at. They are deleted and re-inserted per system.
LEAF_PARENT = {
    "body_composition": "bodies",
    "body_signal_current": "bodies",
    "body_genus_current": "bodies",
    "ring_signal_current": "rings",
    "station_placement": "stations",
    "station_service_current": "stations",
    "station_economy_current": "stations",
}
WRITE_ORDER = ("systems", *PARENT_KEY, *LEAF_PARENT)
assert set(WRITE_ORDER) == set(CONTENT_TABLES)

# Adapter vocabulary domain -> v3_vocab table (the map the full import seeds).
VOCABULARY_TABLES = {
    "body_type": "body_type",
    "terraforming_state": "terraforming_state",
    "atmosphere_classification": "atmosphere_classification",
    "volcanism_type": "volcanism_type",
    "ring_type": "ring_type",
    "reserve_type": "reserve_type",
    "signal_type": "signal_type",
    "genus": "genus",
    "station_type": "station_type",
    "station_service": "station_service",
    "economy": "economy",
}

# A system whose rows the database refuses (a broken rule, a value out of
# range) is set aside. Any other database error stops the run at once.
ROW_ERRORS = (psycopg.errors.IntegrityError, psycopg.errors.DataError,
              psycopg.errors.CardinalityViolation)
# Stop when this many systems were set aside: something is wrong with the
# file or the database, not with one odd system.
MAX_SET_ASIDE = 25
MAX_SET_ASIDE_SHARE = 0.02

_SCHEMA = re.compile(r"^v3_gen_[a-z][a-z0-9_]{0,55}$")
_KEY_INDEX = {table: COPY_COLUMNS[table].index(key) for table, key in PARENT_KEY.items()}


class UpdateError(Exception):
    """Something went wrong; the message says what, in plain words."""


class Refused(UpdateError):
    """The database is not in the shape this tool needs. Nothing was changed."""


def say(message: str = "") -> None:
    print(message, flush=True)


# -- what a run found -------------------------------------------------------


@dataclass
class Report:
    mode: str
    file_name: str = ""
    systems_read: int = 0
    unchanged: int = 0
    changed: int = 0
    new: int = 0
    older: int = 0
    repeated: int = 0
    unusable: Counter = field(default_factory=Counter)
    set_aside: dict[int, str] = field(default_factory=dict)
    systems_by_table: Counter = field(default_factory=Counter)
    rows: Counter = field(default_factory=Counter)
    retired: Counter = field(default_factory=Counter)
    carriers_moved: int = 0
    queued: int = 0
    unknown_words: Counter = field(default_factory=Counter)
    seconds: float = 0.0
    stopped_early: bool = False

    @property
    def to_write(self) -> int:
        return self.changed + self.new

    def as_dict(self) -> dict[str, Any]:
        return {
            "updater": UPDATER_VERSION,
            "mode": self.mode,
            "file": self.file_name,
            "systems_read": self.systems_read,
            "unchanged": self.unchanged,
            "changed": self.changed,
            "new": self.new,
            "older_than_stored": self.older,
            "repeated_in_file": self.repeated,
            "unusable": dict(self.unusable),
            "set_aside": len(self.set_aside),
            "systems_by_table": dict(self.systems_by_table),
            "rows": dict(self.rows),
            "retired": dict(self.retired),
            "carriers_moved": self.carriers_moved,
            "queued": self.queued,
            "unknown_words": len(self.unknown_words),
            "seconds": round(self.seconds, 1),
            "stopped_early": self.stopped_early,
        }

    def lines(self) -> list[str]:
        check = self.mode == "check"
        out = [
            f"Systems in the file:              {self.systems_read:>12,}"
            + ("   (stopped at the limit)" if self.stopped_early else ""),
            f"  already up to date:             {self.unchanged:>12,}",
            f"  changed:                        {self.changed:>12,}",
            f"  new to us:                      {self.new:>12,}",
        ]
        if self.older:
            out.append(f"  already newer here:             {self.older:>12,}   (left alone)")
        if self.repeated:
            out.append(f"  listed twice in a row:          {self.repeated:>12,}   (the last one wins)")
        unusable = sum(self.unusable.values())
        if unusable:
            out.append(f"  not usable:                     {unusable:>12,}")
            for reason, count in self.unusable.most_common(5):
                out.append(f"      {count:,} x {reason}")
        if self.set_aside:
            out.append(f"  set aside after an error:       {len(self.set_aside):>12,}")
            for system_id, reason in list(self.set_aside.items())[:10]:
                out.append(f"      system {system_id}: {reason}")
        if self.to_write:
            out.append("Systems that would be rewritten, by what changed:" if check
                       else "Systems rewritten, by what changed:")
            for table in WRITE_ORDER:
                if self.systems_by_table[table]:
                    out.append(f"  {table:<32}{self.systems_by_table[table]:>12,}")
            out.append(("Rows that would be written:" if check else "Rows written:").ljust(34)
                       + f"{sum(self.rows.values()):>12,}")
            for table, key in (("bodies", "Bodies"), ("rings", "Rings"), ("stations", "Stations")):
                if self.retired[table]:
                    out.append(f"{key} no longer listed:".ljust(34)
                               + f"{self.retired[table]:>12,}   (kept, marked retired)")
            if self.carriers_moved:
                out.append(f"Stations that changed system:     {self.carriers_moved:>12,}   (fleet carriers)")
        out.append(("Systems that would be queued:" if check else "Systems queued for new scores:").ljust(34)
                   + f"{self.queued:>12,}")
        if self.unknown_words:
            out.append(f"Words this version does not know: {len(self.unknown_words):>12,}"
                       "   (stored as blank)")
            for (domain, token), count in self.unknown_words.most_common(8):
                out.append(f"      {domain}: \"{token}\" in {count:,} systems")
        minutes, seconds = divmod(int(self.seconds), 60)
        out.append(f"Took {minutes} min {seconds} s.")
        return out


# -- the database we are writing to -----------------------------------------


@dataclass(frozen=True)
class Target:
    generation_id: str
    schema: str


def _table(schema: str, table: str) -> sql.Identifier:
    return sql.Identifier(schema, table)


def preflight(connection: psycopg.Connection[Any], *, need_queue: bool = True) -> Target:
    """Check the database has the shape this tool was written for.

    Read only. Raises :class:`Refused` with every problem found, so nothing is
    half done on a database that differs from the one this was tested on.
    """
    rows = connection.execute(
        """SELECT g.generation_id::text, g.relation_schema
             FROM v3_meta.current_canonical_generation c
             JOIN v3_meta.canonical_generation g USING (generation_id)
            WHERE g.lifecycle_state = 'PUBLISHED'"""
    ).fetchall()
    if len(rows) != 1 or not _SCHEMA.fullmatch(rows[0][1] or ""):
        raise Refused("there is not exactly one published galaxy to update")
    target = Target(rows[0][0], rows[0][1])
    problems: list[str] = []

    columns: dict[str, dict[str, bool]] = {table: {} for table in CONTENT_TABLES}
    for table, name, required in connection.execute(
        """SELECT c.relname, a.attname,
                  a.attnotnull AND NOT a.atthasdef AND a.attidentity = '' AND a.attgenerated = ''
             FROM pg_attribute a
             JOIN pg_class c ON c.oid = a.attrelid
             JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = ANY(%s) AND c.relkind = 'r'
              AND a.attnum > 0 AND NOT a.attisdropped""",
        (target.schema, list(CONTENT_TABLES)),
    ):
        columns[table][name] = required
    for table in CONTENT_TABLES:
        have = columns[table]
        if not have:
            problems.append(f"table {table} is missing")
            continue
        missing = [name for name in COPY_COLUMNS[table] if name not in have]
        if missing:
            problems.append(f"table {table} lacks columns {missing}")
        extra = [name for name, required in have.items() if required and name not in COPY_COLUMNS[table]]
        if extra:
            problems.append(f"table {table} has required columns this tool does not fill: {extra}")

    keys = dict(connection.execute(
        """SELECT c.relname, array_agg(a.attname::text ORDER BY k.position)
             FROM pg_index i
             JOIN pg_class c ON c.oid = i.indrelid
             JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL unnest(i.indkey::smallint[]) WITH ORDINALITY AS k(attnum, position)
             JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
            WHERE n.nspname = %s AND c.relname = ANY(%s) AND i.indisprimary
            GROUP BY c.relname""",
        (target.schema, list(CONTENT_TABLES)),
    ).fetchall())
    for table in CONTENT_TABLES:
        if columns[table] and tuple(keys.get(table) or ()) != tuple(IDENTITY_COLUMNS[table]):
            problems.append(f"table {table} has primary key {keys.get(table)}, "
                            f"expected {list(IDENTITY_COLUMNS[table])}")

    triggers = connection.execute(
        """SELECT c.relname, t.tgname
             FROM pg_trigger t
             JOIN pg_class c ON c.oid = t.tgrelid
             JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = ANY(%s) AND NOT t.tgisinternal
            ORDER BY 1, 2""",
        (target.schema, list(CONTENT_TABLES)),
    ).fetchall()
    for table, name in triggers:
        problems.append(f"table {table} has a trigger ({name}) this tool does not know about")

    for domain, table in VOCABULARY_TABLES.items():
        wanted = {identifier for identifier, _ in VOCABULARY.get(domain, {}).values()}
        if not wanted:
            continue
        if connection.execute("SELECT to_regclass(%s)", (f"v3_vocab.{table}",)).fetchone()[0] is None:
            problems.append(f"vocabulary table v3_vocab.{table} is missing")
            continue
        have_ids = {row[0] for row in connection.execute(
            sql.SQL("SELECT {} FROM v3_vocab.{}").format(
                sql.Identifier(f"{table}_id"), sql.Identifier(table)))}
        missing_ids = sorted(wanted - have_ids)
        if missing_ids:
            problems.append(f"vocabulary v3_vocab.{table} lacks ids {missing_ids[:10]}"
                            + (" ..." if len(missing_ids) > 10 else ""))

    if need_queue and connection.execute("SELECT to_regclass(%s)", (QUEUE_TABLE,)).fetchone()[0] is None:
        problems.append(f"{QUEUE_TABLE} does not exist (migration 026 has not been applied)")

    if problems:
        raise Refused("this database is not in the shape the galaxy update needs:\n  - "
                      + "\n  - ".join(problems))
    return target


# -- reading what we hold ---------------------------------------------------


def _fetch(connection: psycopg.Connection[Any], schema: str, table: str, column: str,
           values: Sequence[int]) -> list[dict[str, Any]]:
    if not values:
        return []
    query = sql.SQL("SELECT to_jsonb(t) FROM {} t WHERE {} = ANY(%s::bigint[])").format(
        _table(schema, table), sql.Identifier(column))
    return [row[0] for row in connection.execute(query, (list(values),))]


def _stamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


@dataclass
class Stored:
    """What we hold for one system. Retired rows are left out of ``tables``."""

    tables: dict[str, list[dict[str, Any]]]
    # When the file that last wrote this system was made.
    checked_at: datetime | None
    live_keys: dict[str, set[int]]
    all_keys: dict[str, set[int]]


def read_stored(connection: psycopg.Connection[Any], schema: str,
                system_ids: Sequence[int]) -> dict[int, Stored]:
    """Rows held for each of ``system_ids`` that we have; absent ids are new."""
    stored: dict[int, Stored] = {}
    for row in _fetch(connection, schema, "systems", "id64", system_ids):
        # The adapter never supplies a region, so one filled in by another job
        # is not a difference (and is kept when the row is rewritten).
        row = {**row, "galaxy_region_id": None}
        stored[int(row["id64"])] = Stored(
            {table: [] for table in CONTENT_TABLES},
            _stamp(row.get("freshness_checked_at")),
            {table: set() for table in PARENT_KEY},
            {table: set() for table in PARENT_KEY},
        )
        stored[int(row["id64"])].tables["systems"].append(row)

    owner: dict[str, dict[int, int]] = {table: {} for table in PARENT_KEY}
    for table, key in PARENT_KEY.items():
        for row in _fetch(connection, schema, table, "system_id64", system_ids):
            system = stored.get(int(row["system_id64"]))
            if system is None:
                continue
            system.all_keys[table].add(int(row[key]))
            if row.get("lifecycle_state") == "RETIRED":
                continue
            system.tables[table].append(row)
            system.live_keys[table].add(int(row[key]))
            owner[table][int(row[key])] = int(row["system_id64"])

    for table, parent in LEAF_PARENT.items():
        key = PARENT_KEY[parent]
        for row in _fetch(connection, schema, table, key, list(owner[parent])):
            stored[owner[parent][int(row[key])]].tables[table].append(row)
    return stored


# -- one system from the file -----------------------------------------------


@dataclass
class Incoming:
    """One system that has to be written."""

    id64: int
    status: str  # "new" | "changed"
    changed_tables: tuple[str, ...]
    batch: CanonicalBatch
    keys: dict[str, set[int]]
    held: Stored | None
    words: frozenset[tuple[str, str]]


def _reason(error: BaseException) -> str:
    text = str(error).splitlines()[0] if str(error) else type(error).__name__
    # Keep the kind of problem, not the system it happened to.
    return re.sub(r"\d{3,}", "N", text)[:120]


def _database_reason(error: psycopg.Error) -> str:
    diag = getattr(error, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    message = getattr(diag, "message_primary", None) or str(error).splitlines()[0]
    return f"{constraint}: {message}"[:200] if constraint else str(message)[:200]


# -- writing ----------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    source_id: int
    source_run_id: UUID
    observed_at: datetime


@dataclass
class Plan:
    """Exactly which rows one transaction touches, worked out beforehand."""

    items: list[Incoming]
    # Parent rows to mark retired: held and live, and not listed any more.
    retire: dict[str, list[int]]
    # Parent rows whose detail rows are deleted before the new ones go in.
    clear: dict[str, list[int]]
    # Systems a station left, and what that changed for them.
    left_behind: dict[int, tuple[str, ...]]
    carriers_moved: int

    @property
    def queue(self) -> dict[int, tuple[str, ...]]:
        queue = {item.id64: item.changed_tables for item in self.items}
        for system_id, tables in self.left_behind.items():
            queue[system_id] = tuple(sorted({*queue.get(system_id, ()), *tables}))
        return queue


class Writer:
    """Writes batches of systems. One per connection.

    Every statement names the rows it touches by primary key, so each one is an
    index lookup whatever the planner thinks of the table sizes.
    """

    def __init__(self, connection: psycopg.Connection[Any], target: Target, run: Run):
        self.connection = connection
        self.schema = target.schema
        self.run = run
        self._recorded_words: set[tuple[str, str]] = set()
        self._stage = {table: sql.Identifier(f"galaxy_update_{table}") for table in WRITE_ORDER}
        for table in WRITE_ORDER:
            connection.execute(sql.SQL(
                "CREATE TEMP TABLE IF NOT EXISTS {} (LIKE {}) ON COMMIT DELETE ROWS"
            ).format(self._stage[table], _table(self.schema, table)))

    def _columns(self, table: str) -> sql.Composable:
        return sql.SQL(", ").join(map(sql.Identifier, COPY_COLUMNS[table]))

    def _upsert(self, table: str) -> sql.Composable:
        identity = IDENTITY_COLUMNS[table]
        updates = []
        for column in COPY_COLUMNS[table]:
            if column in identity:
                continue
            name = sql.Identifier(column)
            if (table, column) == ("systems", "galaxy_region_id"):
                updates.append(sql.SQL("{c} = COALESCE(EXCLUDED.{c}, t.{c})").format(c=name))
            else:
                updates.append(sql.SQL("{c} = EXCLUDED.{c}").format(c=name))
        return sql.SQL(
            "INSERT INTO {target} AS t ({columns}) SELECT {columns} FROM {stage} "
            "ON CONFLICT ({identity}) DO UPDATE SET {updates}"
        ).format(
            target=_table(self.schema, table), columns=self._columns(table), stage=self._stage[table],
            identity=sql.SQL(", ").join(map(sql.Identifier, identity)),
            updates=sql.SQL(", ").join(updates),
        )

    def _retire(self, table: str) -> sql.Composable:
        # A retired body gives up its slot number, so a replacement can take it.
        extra = sql.SQL(", frontier_body_id = NULL") if table == "bodies" else sql.SQL("")
        return sql.SQL(
            "UPDATE {target} t SET lifecycle_state = 'RETIRED', retired_at = %(at)s, "
            "retired_reason = %(reason)s, "
            "first_missing_run_id = COALESCE(t.first_missing_run_id, %(run)s), "
            "first_missing_at = COALESCE(t.first_missing_at, %(at)s), "
            "last_missing_run_id = %(run)s{extra} "
            "WHERE t.{key} = ANY(%(keys)s::bigint[]) AND t.system_id64 = ANY(%(systems)s::bigint[]) "
            "AND t.lifecycle_state <> 'RETIRED' AND t.source_id = %(source)s"
        ).format(target=_table(self.schema, table), key=sql.Identifier(PARENT_KEY[table]), extra=extra)

    def write(self, plan: Plan) -> Counter:
        """Carry out ``plan`` in one transaction. Returns how many rows were
        marked retired, per table."""
        retired: Counter = Counter()
        items = plan.items
        params = {
            "systems": [item.id64 for item in items], "run": self.run.source_run_id,
            "at": self.run.observed_at, "reason": RETIRED_REASON, "source": self.run.source_id,
        }
        words = {word for item in items for word in item.words}
        with self.connection.transaction(), self.connection.cursor() as cursor:
            for table in WRITE_ORDER:
                columns = COPY_COLUMNS[table]
                if not any(item.batch.rows.get(table) for item in items):
                    continue
                with cursor.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(
                        self._stage[table], self._columns(table))) as copy:
                    for item in items:
                        for row in item.batch.rows.get(table, ()):
                            if len(row) != len(columns):
                                raise UpdateError(f"{table} row has {len(row)} values, expected {len(columns)}")
                            copy.write_row(row)
            cursor.execute(self._upsert("systems"))
            for table in PARENT_KEY:
                if plan.retire[table]:
                    cursor.execute(self._retire(table), {**params, "keys": plan.retire[table]})
                    retired[table] += cursor.rowcount
            for table, parent in LEAF_PARENT.items():
                if plan.clear[parent]:
                    cursor.execute(
                        sql.SQL("DELETE FROM {} WHERE {} = ANY(%s::bigint[])").format(
                            _table(self.schema, table), sql.Identifier(PARENT_KEY[parent])),
                        (plan.clear[parent],))
            for table in PARENT_KEY:
                cursor.execute(self._upsert(table))
            for table in LEAF_PARENT:
                cursor.execute(sql.SQL("INSERT INTO {} ({columns}) SELECT {columns} FROM {}").format(
                    _table(self.schema, table), self._stage[table], columns=self._columns(table)))
            cursor.executemany(
                sql.SQL(
                    "INSERT INTO {queue} AS q (system_id64, changed_tables, source_run_id) "
                    "VALUES (%s, %s::text[], %s) ON CONFLICT (system_id64) DO UPDATE SET "
                    "changed_tables = ARRAY(SELECT DISTINCT name COLLATE \"C\" FROM "
                    "unnest(q.changed_tables || EXCLUDED.changed_tables) AS name ORDER BY 1), "
                    "last_queued_at = now(), source_run_id = EXCLUDED.source_run_id"
                ).format(queue=sql.SQL(QUEUE_TABLE)),
                [(system_id, list(tables), self.run.source_run_id)
                 for system_id, tables in plan.queue.items()],
            )
            fresh = sorted(words - self._recorded_words)
            if fresh:
                cursor.executemany(
                    """INSERT INTO v3_source.unmapped_vocabulary(
                           source_run_id, vocabulary_domain, raw_token, normalized_candidate,
                           first_observed_at, detail)
                       VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                       ON CONFLICT (source_run_id, vocabulary_domain, raw_token) DO NOTHING""",
                    [(self.run.source_run_id, domain, token, "_".join(token.casefold().split()),
                      self.run.observed_at, json.dumps({"source": "spansh", "updater": UPDATER_VERSION}))
                     for domain, token in fresh],
                )
        self._recorded_words.update(words)
        return retired


# -- one batch --------------------------------------------------------------


def _owners(connection: psycopg.Connection[Any], schema: str, table: str,
            keys: Sequence[int]) -> dict[int, tuple[int, str]]:
    if not keys:
        return {}
    key = sql.Identifier(PARENT_KEY[table])
    query = sql.SQL(
        "SELECT {key}, system_id64, lifecycle_state FROM {table} WHERE {key} = ANY(%s::bigint[])"
    ).format(key=key, table=_table(schema, table))
    return {int(row[0]): (int(row[1]), row[2]) for row in connection.execute(query, (list(keys),))}


def plan_systems(connection: psycopg.Connection[Any], target: Target,
                 systems: Sequence[tuple[NormalizedSystem, Mapping[str, Any]]],
                 report: Report, run: Run) -> Plan:
    """Compare ``systems`` with what we hold and work out what to write.

    Read only. Systems that need nothing, or cannot be written, are counted in
    ``report`` here; the ones in the returned plan are counted once written.
    """
    adapter = SpanshAdapter(source_id=run.source_id, source_run_id=run.source_run_id,
                            observed_at=run.observed_at)
    stored = read_stored(connection, target.schema, [system.id64 for system, _ in systems])
    items: list[Incoming] = []
    for system, record in systems:
        held = stored.get(system.id64)
        change = classify_material_change(system, held.tables if held else None,
                                          policy="strict", include_carriers=True)
        if change.status == "unchanged":
            report.unchanged += 1
            continue
        if held is not None and held.checked_at is not None and held.checked_at > run.observed_at:
            report.older += 1
            continue
        batch = adapter.adapt_system(dict(record))
        keys = {table: {int(row[_KEY_INDEX[table]]) for row in batch.rows.get(table, ())}
                for table in PARENT_KEY}
        tables = change.changed_tables if change.status == "changed" else tuple(
            table for table in CONTENT_TABLES if batch.rows.get(table))
        items.append(Incoming(system.id64, change.status, tables, batch, keys, held,
                              system.unmapped_vocabulary))

    # A body or ring id that already belongs to another system cannot simply be
    # taken over: other rows point at it. Set that system aside.
    for table in ("bodies", "rings"):
        owners = _owners(connection, target.schema, table,
                         sorted({key for item in items for key in item.keys[table]}))
        for item in list(items):
            clash = [key for key in item.keys[table] if key in owners and owners[key][0] != item.id64]
            if clash:
                items.remove(item)
                report.set_aside[item.id64] = (
                    f"{PARENT_KEY[table]} {clash[0]} already belongs to system {owners[clash[0]][0]}")

    left_behind: dict[int, tuple[str, ...]] = {}
    moved = 0
    owners = _owners(connection, target.schema, "stations",
                     sorted({key for item in items for key in item.keys["stations"]}))
    for item in items:
        for key in item.keys["stations"]:
            previous = owners.get(key)
            if previous is not None and previous[0] != item.id64:
                moved += 1
                if previous[1] != "RETIRED":
                    left_behind[previous[0]] = ("stations",)

    retire: dict[str, list[int]] = {}
    clear: dict[str, list[int]] = {}
    for table in PARENT_KEY:
        listed = {key for item in items for key in item.keys[table]}
        live = {key for item in items if item.held for key in item.held.live_keys[table]}
        held_keys = {key for item in items if item.held for key in item.held.all_keys[table]}
        retire[table] = sorted(live - listed)
        clear[table] = sorted(held_keys | listed)
    return Plan(items, retire, clear, left_behind, moved)


def _count(report: Report, plan: Plan, retired: Mapping[str, int]) -> None:
    for item in plan.items:
        setattr(report, item.status, getattr(report, item.status) + 1)
        for table in item.changed_tables:
            report.systems_by_table[table] += 1
        for table in WRITE_ORDER:
            report.rows[table] += len(item.batch.rows.get(table, ()))
    for table, number in retired.items():
        report.retired[table] += number
    report.queued += len(plan.queue)
    report.carriers_moved += plan.carriers_moved


def process_batch(connection: psycopg.Connection[Any], target: Target, records: Sequence[Mapping[str, Any]],
                  report: Report, *, run: Run, writer: Writer | None) -> None:
    """Compare one batch of Spansh systems with what we hold; write if asked."""
    systems: list[tuple[NormalizedSystem, Mapping[str, Any]]] = []
    for record in records:
        report.systems_read += 1
        try:
            system = normalize_system(record)
        except (ValueError, TypeError, KeyError) as error:
            report.unusable[_reason(error)] += 1
            continue
        systems.append((system, record))
        for word in system.unmapped_vocabulary:
            report.unknown_words[word] += 1

    before = (report.unchanged, report.older, dict(report.set_aside))
    plan = plan_systems(connection, target, systems, report, run)
    if writer is None or not plan.items:
        _count(report, plan, {table: len(keys) for table, keys in plan.retire.items()})
        return
    try:
        _count(report, plan, writer.write(plan))
        return
    except ROW_ERRORS:
        pass
    # The database refused a row. Go again one system at a time, each compared
    # afresh with what is stored now, so only the system at fault is set aside.
    report.unchanged, report.older, report.set_aside = before[0], before[1], before[2]
    for pair in systems:
        plan = plan_systems(connection, target, [pair], report, run)
        if not plan.items:
            continue
        try:
            _count(report, plan, writer.write(plan))
        except ROW_ERRORS as error:
            report.set_aside[pair[0].id64] = _database_reason(error)


# -- registering the run ----------------------------------------------------


@dataclass(frozen=True)
class FileFacts:
    path: Path
    size: int
    sha256: str
    made_at: datetime


def file_facts(path: Path, made_at: datetime | None) -> FileFacts:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    stat = path.stat()
    return FileFacts(path, stat.st_size, digest.hexdigest(),
                     made_at or datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc))


def _sha(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def register_run(connection: psycopg.Connection[Any], facts: FileFacts) -> Run:
    """Record the file and this run, the way the full import records its own.

    Applying the same file again continues under the same run.
    """
    here = Path(__file__).resolve().parent
    code = _sha(Path(__file__).read_bytes())
    normalizer = _sha((here.parent / "v3_spansh" / "adapter.py").read_bytes())
    config = {"updater": UPDATER_VERSION, "policy": "strict", "carriers": True}
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("SELECT source_id FROM v3_source.source WHERE source_code = 'spansh'")
        row = cursor.fetchone()
        if row is None:
            raise Refused("the database has no 'spansh' source: was the galaxy imported here?")
        source_id = int(row[0])
        cursor.execute(
            """SELECT rights_policy_id, retention_class FROM v3_source.source_rights_policy
                WHERE source_id = %s AND rights_class = 'CANONICAL_ELIGIBLE' AND retired_at IS NULL
                ORDER BY effective_at DESC, rights_policy_id DESC LIMIT 1""",
            (source_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise Refused("the 'spansh' source has no rights policy to record this file under")
        policy_id, retention = int(row[0]), row[1]
        cursor.execute(
            """UPDATE v3_source.source_run
                  SET run_state = 'FAILED', completed_at = clock_timestamp(),
                      failure_code = 'DID_NOT_FINISH',
                      failure_detail = 'an earlier galaxy update stopped before the end'
                WHERE source_id = %s AND acquisition_kind = 'DELTA' AND run_state = 'RUNNING'
                  AND importer_version = %s""",
            (source_id, UPDATER_VERSION),
        )
        cursor.execute(
            """INSERT INTO v3_source.source_artifact(
                   artifact_id, source_id, rights_policy_id, artifact_kind, endpoint_identity,
                   content_sha256, size_bytes, media_type, compression_code, retrieved_at,
                   effective_at, retention_class, storage_locator)
               VALUES (%s, %s, %s, 'GALAXY_DELTA', %s, %s, %s, 'application/json', 'gzip', %s, %s, %s, %s)
               ON CONFLICT (source_id, content_sha256) DO NOTHING""",
            (uuid4(), source_id, policy_id, facts.path.name, bytes.fromhex(facts.sha256), facts.size,
             facts.made_at, facts.made_at, retention, facts.path.name),
        )
        cursor.execute(
            "SELECT artifact_id FROM v3_source.source_artifact WHERE source_id = %s AND content_sha256 = %s",
            (source_id, bytes.fromhex(facts.sha256)),
        )
        artifact_id = cursor.fetchone()[0]
        cursor.execute(
            """INSERT INTO v3_source.source_run(
                   source_run_id, source_id, rights_policy_id, artifact_id, acquisition_kind,
                   trust_zone, run_state, idempotency_key, scope_contract, is_complete_snapshot,
                   started_at, effective_at, importer_version, importer_code_sha256,
                   importer_config_sha256, normalizer_version, normalizer_sha256)
               VALUES (%s, %s, %s, %s, 'DELTA', 'CANONICAL', 'RUNNING', %s, %s::jsonb, false,
                       clock_timestamp(), %s, %s, %s, %s, %s, %s)
               ON CONFLICT (source_id, idempotency_key) DO UPDATE
                   SET run_state = 'RUNNING', completed_at = NULL, failure_code = NULL,
                       failure_detail = NULL
               RETURNING source_run_id""",
            (uuid4(), source_id, policy_id, artifact_id, f"galaxy-update:{facts.sha256}",
             json.dumps({"file": facts.path.name}), facts.made_at, UPDATER_VERSION, code,
             _sha(json.dumps(config, sort_keys=True).encode()), NORMALIZER_VERSION, normalizer),
        )
        return Run(source_id, cursor.fetchone()[0], facts.made_at)


def finish_run(connection: psycopg.Connection[Any], run: Run, report: Report, *,
               failure: str | None = None) -> None:
    state = "FAILED" if failure else ("PARTIAL" if report.stopped_early else "SUCCEEDED")
    connection.execute(
        """UPDATE v3_source.source_run
              SET run_state = %s, completed_at = clock_timestamp(), scope_contract = %s::jsonb,
                  failure_code = %s, failure_detail = %s
            WHERE source_run_id = %s""",
        (state, json.dumps(report.as_dict()), "GALAXY_UPDATE_FAILED" if failure else None,
         failure[:500] if failure else None, run.source_run_id),
    )


# -- the whole file ---------------------------------------------------------


def _batches(path: Path, report: Report, limit: int | None) -> Iterator[list[Mapping[str, Any]]]:
    batch: list[Mapping[str, Any]] = []
    seen: set[int] = set()
    read = 0
    for item in SpanshStream(path):
        if limit is not None and read >= limit:
            report.stopped_early = True
            break
        read += 1
        system_id = item.record["id64"]
        # A system listed twice is applied twice, in file order.
        if system_id in seen:
            report.repeated += 1
        if len(batch) >= BATCH_SYSTEMS or system_id in seen:
            yield batch
            batch, seen = [], set()
        batch.append(item.record)
        seen.add(system_id)
    if batch:
        yield batch


def run_file(connection: psycopg.Connection[Any], path: Path, *, mode: str,
             made_at: datetime | None = None, limit: int | None = None,
             progress: bool = True) -> Report:
    """Check or apply one Spansh galaxy file. ``connection`` must be autocommit."""
    if mode not in ("check", "apply"):
        raise ValueError("mode must be 'check' or 'apply'")
    if not connection.autocommit:
        raise ValueError("the connection must be in autocommit mode")
    report = Report(mode, path.name)
    started = time.monotonic()
    connection.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(STATEMENT_TIMEOUT)))
    connection.execute("SET lock_timeout = '60s'")
    target = preflight(connection, need_queue=mode == "apply")
    writer: Writer | None = None
    if mode == "apply":
        if not connection.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
            raise UpdateError("another galaxy update is running; this one did not start")
        run = register_run(connection, file_facts(path, made_at))
        writer = Writer(connection, target, run)
    else:
        stamp = made_at or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        run = Run(0, UUID(int=0), stamp)
    try:
        last = started
        for records in _batches(path, report, limit):
            process_batch(connection, target, records, report, run=run, writer=writer)
            allowed = max(MAX_SET_ASIDE, int(report.systems_read * MAX_SET_ASIDE_SHARE))
            if len(report.set_aside) > allowed:
                raise UpdateError(
                    f"{len(report.set_aside)} systems could not be applied; stopping. "
                    "What was applied so far is complete and stays.")
            if progress and time.monotonic() - last >= PROGRESS_SECONDS:
                last = time.monotonic()
                say(f"  ... {report.systems_read:,} systems read, "
                    f"{report.to_write:,} changed or new, {int(last - started) // 60} min")
        report.seconds = time.monotonic() - started
        if writer is not None:
            finish_run(connection, run, report)
    except BaseException as error:
        report.seconds = time.monotonic() - started
        if writer is not None:
            try:
                finish_run(connection, run, report, failure=_reason(error))
            except psycopg.Error:
                pass
        raise
    finally:
        if writer is not None:
            try:
                connection.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
            except psycopg.Error:
                pass
    return report


def _when(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise argparse.ArgumentTypeError("the time needs a time zone, for example 2026-11-10T02:14:00+00:00")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m spansh_delta.apply",
        description="Bring the galaxy tables up to date from a Spansh galaxy file.")
    parser.add_argument("mode", choices=("check", "apply"),
                        help="check: read only, say what would change. apply: make the changes.")
    parser.add_argument("file", type=Path, help="a Spansh galaxy*.json.gz file")
    parser.add_argument("--made-at", type=_when, default=None,
                        help="when Spansh made the file (default: the file's own time)")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after this many systems (for timing a big file)")
    args = parser.parse_args(argv)
    if not args.file.is_file():
        say(f"FAILED: there is no file {args.file}")
        return 1
    if args.limit is not None and args.limit < 1:
        say("FAILED: --limit must be 1 or more")
        return 1
    conninfo = ""
    if os.environ.get("EDFINDER_GALAXY_USE_DATABASE_URL") == "1":
        conninfo = os.environ.get("DATABASE_URL", "")
        if not conninfo:
            say("Could not connect to the database: EDFINDER_GALAXY_USE_DATABASE_URL is 1 "
                "but DATABASE_URL is not set.")
            return EXIT_NO_CONNECTION
    try:
        # The error text of a failed connection can repeat what it was given,
        # so it is never printed.
        connection = psycopg.connect(conninfo, autocommit=True,
                                     application_name="edfinder-galaxy-update")
    except (psycopg.Error, ValueError):
        say("Could not connect to the database: it did not accept this connection. "
            "(The connection details are not shown.)")
        return EXIT_NO_CONNECTION
    with connection:
        size = args.file.stat().st_size
        say(f"Galaxy update ({args.mode}): {args.file.name}, "
            + (f"{size / 1e9:.2f} GB" if size >= 1e9 else f"{size / 1e6:.1f} MB"))
        try:
            report = run_file(connection, args.file, mode=args.mode, made_at=args.made_at,
                              limit=args.limit)
        except Refused as error:
            say(f"REFUSED: {error}")
            say("Nothing was changed.")
            return 2
        except (UpdateError, ValueError, OSError, psycopg.Error) as error:
            say(f"FAILED: {_reason(error) if isinstance(error, psycopg.Error) else error}")
            return 1
    for line in report.lines():
        say(line)
    if os.environ.get("EDFINDER_GALAXY_JSON") == "1":
        say("JSON " + json.dumps(report.as_dict(), sort_keys=True))
    say("CHECKED: nothing was changed." if args.mode == "check" else "DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
