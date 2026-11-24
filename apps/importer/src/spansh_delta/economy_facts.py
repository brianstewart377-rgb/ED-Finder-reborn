"""Find's facts for each system, kept up to date each night. Ratings rebuild, build step 4.

    python -m spansh_delta.economy_facts status    read only

For every system within 500 ly of an inhabited system (``v3_state.system_reach``,
migration 028), ``v3_state.economy_facts`` (migration 031) holds one row:

  * ``bodies_known``: its stars, planets and moons that we hold. Find takes it
    as the system's orbital room ("Ratings rebuild — design", decision 10):
    nobody can say how many orbital slots one body has, but on a sheet of 394
    systems with every count read in the game, a system's total follows its
    body count.
  * ``ground_slots``: the predicted ground slots of all its bodies together.
  * ``strongest`` and ``purest``: for each of the seven economies, in the order
    of ``ECONOMIES``, one number for where the system's best body stands in
    that order (cpe/body_facts.py), or 0 where no body suits.
  * ``pairs``: the same for each of the 21 pairs, in the order of ``PAIRS``.
  * ``worked_out_at``: when the rows these were worked out from were read.
  * ``x_ly``, ``y_ly``, ``z_ly``: where the system is, and ``cell``, the 32 ly
    cube of space it is in, which the database makes from them (migration 032;
    ``edfinder_api.find_cells``). Find reads the systems near a point by the
    cells its sphere touches.

A system with no known bodies has ``bodies_known`` 0 and no numbers. Each
number packs every step of its order into one (``edfinder_api.find_numbers``),
so that the database can sort millions of systems by it. The card's details are not stored: a page
works them out again for the cards it shows, by the same code.

The nightly scoring (``spansh_delta.scores``) keeps the table up to date, by
``bring_up_to_date``:

  * systems the galaxy update changed get new rows, beside their new scores;
  * the first fill carries on for up to ``FILL_MINUTES`` after each run, from
    where it stopped, until every system in reach has a row.
    ``v3_state.economy_facts_fill`` records how far it has got;
  * once it is done, systems in reach without a row get one each night:
    systems that came within reach, mostly. When a fill finishes, rows of
    systems no longer in reach are deleted, and the rows are put in cell order
    on disk (``tidy``), so that the systems of one part of space lie together
    and a search near a system reads a few stretches of the table.

A fill is for one ``VERSION`` (``edfinder_api.find_numbers``): the rulebook's
release, and the layout of the numbers. When the version changes, a new fill starts from the beginning; until
it finishes, the table holds old and new numbers side by side. So Find (build
step 5) uses the table only once a fill of the version it runs has finished.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import sys
import time
from typing import Any

import psycopg
from psycopg import sql

from edfinder_api.cpe.body_facts import (
    ECONOMIES,
    PAIRS,
    BodyFacts,
    best_for_pair,
    pair_key,
    purest,
    purest_key,
    strongest,
    strongest_key,
)
from edfinder_api.find_numbers import (  # noqa: F401  (PackError and pack are used through this module)
    PAIR_STEPS,
    PUREST_STEPS,
    STRONGEST_STEPS,
    VERSION,
    PackError,
    pack,
)

from . import apply as galaxy
from .speed_trial import SystemRows, facts_for, planner_inputs, read

TABLE = "v3_state.economy_facts"
CELL_INDEX = "economy_facts_cell_idx"
FILLS = "v3_state.economy_facts_fill"
REACH = "v3_state.system_reach"
# Systems worked out at a time.
BATCH_SYSTEMS = 500
# How long the first fill goes on after each nightly run, at most. It runs
# after the galaxy update and the scoring, twice a day.
FILL_MINUTES = 120
# The fill says how far it has got this often. Every minute, as the galaxy
# update does, was 120 lines a run and pushed the rest out of the log.
FILL_PROGRESS_SECONDS = 600
# The body types counted in bodies_known: not barycentres or belt clusters.
COUNTED_BODY_TYPES = ("star", "planet")

# -- one system ---------------------------------------------------------------


@dataclass(frozen=True)
class SystemFacts:
    system_id64: int
    bodies_known: int
    ground_slots: int
    # None when no body is known.
    strongest: tuple[int, ...] | None
    purest: tuple[int, ...] | None
    pairs: tuple[int, ...] | None


def _best(found: BodyFacts | None, key: tuple | None, steps: Sequence[tuple[int, int, int]]) -> int:
    return 0 if found is None or key is None else pack(key, steps)


def system_facts(system_id64: int, rows: SystemRows) -> SystemFacts:
    """One system's row, from its rows in the galaxy tables."""
    known = sum(1 for body in rows.bodies
                if body["lifecycle_state"] == "ACTIVE" and body["body_type"] in COUNTED_BODY_TYPES)
    if not known:
        return SystemFacts(system_id64, 0, 0, None, None, None)
    inputs = planner_inputs(rows)
    facts = facts_for(inputs)
    ground = sum(slots for ref, slots in inputs.slots.items()
                 if slots and inputs.rows[ref]["is_landable"] is True)
    by_strength, by_purity, by_pair = [], [], []
    for economy in ECONOMIES:
        found = strongest(facts, economy)
        by_strength.append(_best(found, found and strongest_key(found, economy), STRONGEST_STEPS))
        found = purest(facts, economy)
        by_purity.append(_best(found, found and purest_key(found, economy), PUREST_STEPS))
    for first, second in PAIRS:
        found = best_for_pair(facts, first, second)
        by_pair.append(_best(found, found and pair_key(found, first, second), PAIR_STEPS))
    return SystemFacts(system_id64, known, ground, tuple(by_strength), tuple(by_purity), tuple(by_pair))


# -- the tables ---------------------------------------------------------------


NOT_KEPT = "not kept in this database (migrations 031 and 032 have not both been applied)"


def available(connection: psycopg.Connection[Any]) -> bool:
    """Whether this database keeps the facts, with where each system is
    (migrations 031 and 032), and reach (028)."""
    return connection.execute(
        "SELECT to_regclass(%s) IS NOT NULL AND to_regclass(%s) IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass(%s) "
        "AND attname = 'cell' AND NOT attisdropped)", (FILLS, REACH, TABLE)).fetchone()[0]


@dataclass(frozen=True)
class Fill:
    fill_id: int
    version: str
    started_at: datetime
    finished_at: datetime | None
    last_system_id64: int | None
    systems: int
    # When its rows were put in cell order on disk (``tidy``).
    tidied_at: datetime | None


def latest_fill(connection: psycopg.Connection[Any]) -> Fill | None:
    row = connection.execute(
        f"SELECT fill_id, version, started_at, finished_at, last_system_id64, systems, tidied_at "
        f"FROM {FILLS} ORDER BY fill_id DESC LIMIT 1").fetchone()
    return Fill(*row) if row else None


def _start_fill(connection: psycopg.Connection[Any]) -> Fill:
    row = connection.execute(
        f"INSERT INTO {FILLS} (version) VALUES (%s) "
        "RETURNING fill_id, version, started_at, finished_at, last_system_id64, systems, tidied_at",
        (VERSION,)).fetchone()
    return Fill(*row)


UPSERT = (
    f"INSERT INTO {TABLE} (system_id64, bodies_known, ground_slots, strongest, purest, pairs, "
    "worked_out_at, x_ly, y_ly, z_ly) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (system_id64) DO UPDATE SET bodies_known = excluded.bodies_known, "
    "ground_slots = excluded.ground_slots, strongest = excluded.strongest, "
    "purest = excluded.purest, pairs = excluded.pairs, worked_out_at = excluded.worked_out_at, "
    "x_ly = excluded.x_ly, y_ly = excluded.y_ly, z_ly = excluded.z_ly"
)
POSITIONS_SQL = "SELECT id64, x_ly, y_ly, z_ly FROM {systems} WHERE id64 = ANY(%s::bigint[])"


def _list(numbers: tuple[int, ...] | None) -> list[int] | None:
    return None if numbers is None else list(numbers)


@dataclass
class FactsReport:
    """What the nightly scoring did to the facts."""

    # Why nothing was done, when nothing was.
    skipped: str | None = None
    changed: int = 0
    came_within_reach: int = 0
    filled: int = 0
    failed: dict[int, str] = field(default_factory=dict)
    fill: Fill | None = None
    in_reach: int = 0
    fill_finished_now: bool = False
    deleted_outside_reach: int = 0
    # Putting the rows in cell order on disk: how long it took, or why it failed.
    tidy_seconds: float | None = None
    tidy_failed: str | None = None

    def as_dict(self) -> dict[str, Any]:
        if self.skipped:
            return {"skipped": self.skipped}
        found: dict[str, Any] = {
            "version": VERSION, "changed": self.changed, "came_within_reach": self.came_within_reach,
            "filled": self.filled, "failed": len(self.failed),
        }
        if self.fill is not None:
            found.update(fill_systems=self.fill.systems, fill_finished=self.fill.finished_at is not None,
                         fill_tidied=self.fill.tidied_at is not None, in_reach=self.in_reach)
        if self.tidy_failed:
            found["tidy_failed"] = self.tidy_failed
        return found

    def lines(self) -> list[str]:
        def row(label: str, number: int, note: str = "") -> str:
            return f"{label:<48}{number:>10,}" + (f"   {note}" if note else "")

        if self.skipped:
            return [f"Economy facts: {self.skipped}"]
        out = [f"Economy facts ({VERSION}):",
               row("  worked out again, their system changed:", self.changed),
               row("  worked out for systems that came within reach:", self.came_within_reach),
               row("  worked out by the first fill:", self.filled),
               row("  could not be worked out:", len(self.failed))]
        for system_id, reason in list(self.failed.items())[:5]:
            out.append(f"      for example system {system_id}: {reason}")
        if self.fill is not None:
            if self.fill.finished_at is None:
                out.append(f"  The first fill has done {self.fill.systems:,} of {self.in_reach:,} systems "
                           f"within 500 ly; it carries on after the next run.")
            elif self.fill_finished_now:
                out.append(f"  The first fill finished: {self.fill.systems:,} systems. Rows of "
                           f"{self.deleted_outside_reach:,} systems no longer within 500 ly were deleted.")
        if self.tidy_seconds is not None:
            minutes, seconds = divmod(int(self.tidy_seconds), 60)
            out.append(f"  The rows were put in order on disk for searching near a system: "
                       f"{minutes} min {seconds} s.")
        if self.tidy_failed:
            out.append(f"  The rows could NOT be put in order on disk: {self.tidy_failed}. Searches near a "
                       "system will be slow until that works; the next run tries again.")
        return out


def write(connection: psycopg.Connection[Any], schema: str, system_ids: Sequence[int],
          report: FactsReport, *, fill: Fill | None = None) -> int:
    """Work these systems out from the galaxy tables and store their rows; with
    ``fill``, move it on past them in the same transaction. Returns how many rows
    were written. A system that cannot be worked out keeps the row it had, and
    the report says why."""
    if not system_ids:
        return 0
    read_at = connection.execute("SELECT now()").fetchone()[0]
    rows = read(connection, schema, system_ids)
    positions = {row[0]: row[1:] for row in connection.execute(
        sql.SQL(POSITIONS_SQL).format(systems=sql.Identifier(schema, "systems")),
        (list(system_ids),)).fetchall()}
    found: list[tuple[SystemFacts, tuple[float, float, float]]] = []
    for system_id in system_ids:
        try:
            if system_id not in positions:
                raise LookupError("the galaxy tables do not say where it is")
            found.append((system_facts(system_id, rows[system_id]), positions[system_id]))
        except Exception as error:  # noqa: BLE001  (one odd system must not stop the night)
            report.failed[system_id] = f"{type(error).__name__}: {error}"
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.executemany(UPSERT, [
                (item.system_id64, item.bodies_known, item.ground_slots, _list(item.strongest),
                 _list(item.purest), _list(item.pairs), read_at, *position)
                for item, position in found])
        if fill is not None:
            connection.execute(
                f"UPDATE {FILLS} SET last_system_id64 = %s, systems = systems + %s WHERE fill_id = %s",
                (max(system_ids), len(system_ids), fill.fill_id))
    return len(found)


def _now() -> float:
    return time.monotonic()


def _in_batches(ids: Sequence[int]) -> Iterable[Sequence[int]]:
    for start in range(0, len(ids), BATCH_SYSTEMS):
        yield ids[start:start + BATCH_SYSTEMS]


def without_facts(connection: psycopg.Connection[Any]) -> list[int]:
    """Systems in reach that have no row, in id order."""
    return [row[0] for row in connection.execute(
        f"SELECT r.system_id64 FROM {REACH} r WHERE NOT EXISTS ("
        f"SELECT 1 FROM {TABLE} f WHERE f.system_id64 = r.system_id64) ORDER BY r.system_id64").fetchall()]


def changed(connection: psycopg.Connection[Any], schema: str, system_ids: Sequence[int],
            report: FactsReport) -> None:
    """Systems whose galaxy rows changed: new rows for them (nightly scoring)."""
    if report.skipped is None:
        for batch in _in_batches(list(system_ids)):
            report.changed += write(connection, schema, batch, report)


def tidy(connection: psycopg.Connection[Any], fill: Fill, report: FactsReport, *,
         progress: bool = True) -> None:
    """Put the table's rows in cell order on disk, so that the systems of one
    part of space lie together, and note on the fill that it was done.

    CLUSTER writes the whole table afresh: it needs as much free disk again as
    the table takes, and nothing can read the table while it runs. If it fails
    the table is as it was, the report says why, and the next run tries again.
    """
    if progress:
        galaxy.say("  Economy facts: putting the rows in order on disk for searching near a system "
                   "(this takes a while) ...")
    started = _now()
    before = connection.execute("SELECT setting FROM pg_settings WHERE name = 'statement_timeout'").fetchone()[0]
    connection.execute("SET statement_timeout = 0")
    try:
        connection.execute(f"CLUSTER {TABLE} USING {CELL_INDEX}")
        connection.execute(f"ANALYZE {TABLE}")
        connection.execute(f"UPDATE {FILLS} SET tidied_at = now() WHERE fill_id = %s", (fill.fill_id,))
        report.tidy_seconds = _now() - started
    except psycopg.Error as error:
        report.tidy_failed = galaxy._reason(error)
    finally:
        connection.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(before)))


def carry_on(connection: psycopg.Connection[Any], schema: str, report: FactsReport, *,
             minutes: float = FILL_MINUTES, progress: bool = True) -> None:
    """After the waiting list: carry the fill on, or once it is done, give rows to
    systems in reach that have none. A finished fill whose rows are not yet in
    order on disk is tidied (``tidy``)."""
    if report.skipped is not None:
        return
    fill = latest_fill(connection)
    if fill is None or fill.version != VERSION:
        fill = _start_fill(connection)
        if progress:
            galaxy.say(f"  Economy facts: a fill for {VERSION} starts.")
    if fill.finished_at is not None:
        for batch in _in_batches(without_facts(connection)):
            report.came_within_reach += write(connection, schema, batch, report)
    else:
        deadline = _now() + minutes * 60
        take = (f"SELECT system_id64 FROM {REACH} WHERE system_id64 > %s "
                "ORDER BY system_id64 LIMIT %s")
        last = _now()
        while _now() < deadline:
            after = -(1 << 63) if fill.last_system_id64 is None else fill.last_system_id64
            batch = [row[0] for row in connection.execute(take, (after, BATCH_SYSTEMS)).fetchall()]
            if not batch:
                with connection.transaction():
                    report.deleted_outside_reach = connection.execute(
                        f"DELETE FROM {TABLE} f WHERE NOT EXISTS ("
                        f"SELECT 1 FROM {REACH} r WHERE r.system_id64 = f.system_id64)").rowcount
                    connection.execute(f"UPDATE {FILLS} SET finished_at = now() WHERE fill_id = %s",
                                       (fill.fill_id,))
                report.fill_finished_now = True
                break
            report.filled += write(connection, schema, batch, report, fill=fill)
            fill = latest_fill(connection)
            if progress and _now() - last >= FILL_PROGRESS_SECONDS:
                last = _now()
                galaxy.say(f"  ... economy facts: the fill has done {fill.systems:,} systems")
    fill = latest_fill(connection)
    if fill.finished_at is not None and fill.tidied_at is None:
        tidy(connection, fill, report, progress=progress)
    report.fill = latest_fill(connection)
    report.in_reach = connection.execute(f"SELECT count(*) FROM {REACH}").fetchone()[0]


def status_lines(connection: psycopg.Connection[Any]) -> list[str]:
    if not available(connection):
        return ["Economy facts are not kept in this database (migrations 028, 031 and 032)."]
    fill = latest_fill(connection)
    rows = connection.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
    if fill is None:
        return [f"Economy facts: no fill has started yet; {rows:,} rows."]
    def when(moment: datetime) -> str:
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if fill.finished_at is None:
        state = f"{fill.systems:,} systems done so far"
    else:
        state = "finished " + when(fill.finished_at) + (
            ", rows in order on disk since " + when(fill.tidied_at) if fill.tidied_at
            else ", rows not yet in order on disk")
    note = "" if fill.version == VERSION else f" (this code runs {VERSION}: a new fill starts with the next run)"
    return [f"Economy facts: the fill for {fill.version} started {when(fill.started_at)}, "
            f"{state}; {rows:,} rows.{note}"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m spansh_delta.economy_facts",
        description="Find's facts for each system within 500 ly (migration 031). Read only.")
    parser.add_argument("command", choices=("status",))
    parser.parse_args(argv)
    connection = galaxy.connect()
    if connection is None:
        return galaxy.EXIT_NO_CONNECTION
    with connection:
        try:
            for line in status_lines(connection):
                galaxy.say(line)
        except psycopg.Error as error:
            galaxy.say(f"FAILED: {galaxy._reason(error)}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
