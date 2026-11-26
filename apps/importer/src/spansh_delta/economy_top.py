"""Find's ranked lists across the whole band, written afresh after each scoring run.

    python -m spansh_delta.economy_top status    read only

Ratings rebuild ("Ratings rebuild — design"), build step 5. A search near a
system sorts the systems of its own sphere each time. Across the band there is
no sphere: every system within 500 ly of an inhabited system is in it, about
41 million rows of ``v3_state.economy_facts``, and sorting those for one
search would take minutes on the Pi. So the best systems of the whole band are
written down in order, ``DEPTH`` of them for each of the 36 lists
(``edfinder_api.find_numbers``: each economy by Strongest, each by Purest,
each pair, and no economy at all), in ``v3_state.economy_top`` (migration
033). Find's search across the band reads a list from its first place down
and applies the claim rule, the reach rule and the body filters as it goes.

A list is in the design's order: the list's number, highest first; then more
known bodies; systems still level are in the order of their ids, so that the
order is the same every night. A system whose number is 0 (no body suits, or
for the last list no ground slot) is in no list.

``rebuild`` writes a whole new set, in one transaction:

  1. It reads one row in a hundred of the facts and finds, for each list,
     about where the place ``DEPTH`` will fall: its cut-off. The cut-off is
     taken twice as far down as the sample says, to be on the safe side.
  2. It reads the facts once and keeps the rows at or above any list's
     cut-off, then puts each list's rows in order and keeps its first
     ``DEPTH``.
  3. A list that came out shorter than ``DEPTH`` had its cut-off too high
     after all: that list alone is read again, whole. So the sample only ever
     decides how long the work takes, never what the lists hold.
  4. The new set becomes the one searches read, and the old one is deleted.

A search sees the old set or the new one, never a mix. If anything fails the
old set stays as it was and the report says why.

The lists are made once a fill of the facts for this build's layout has
finished: before that, most systems have no row to be listed by.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timezone
import math
import sys
import time
from typing import Any

import psycopg
from psycopg import sql

from edfinder_api.find_numbers import (
    COUNT_COLUMNS,
    LAYOUT,
    LIST_COUNT,
    VERSION,
    layout_of,
    list_expressions,
)

from . import apply as galaxy
from . import economy_facts

TABLE = "v3_state.economy_top"
BUILDS = "v3_state.economy_top_build"
FACTS = economy_facts.TABLE
FILLS = economy_facts.FILLS
# How many places each list has at most.
DEPTH = 20_000
# How much of the facts table is read to find the cut-offs, in hundredths.
SAMPLE_PERCENT = 1.0
# A cut-off is taken this many times as far down the sample as place DEPTH
# falls, so that a list very seldom comes out short.
SAFETY = 2
# A rebuild that goes on this long is not going as expected: it is stopped,
# and the lists stay as they were.
TIME_LIMIT = "90min"
# A cut-off that every system in a list is above: any number above 0.
_NO_CUT_OFF = (1, 0, (1 << 63) - 1)

NOT_KEPT = "not kept in this database (migration 033 has not been applied)"
NOT_YET = "not made yet: the first fill of Find's facts has not finished"

_EXPRESSIONS = list_expressions()
_COUNTS = ", ".join(COUNT_COLUMNS)
_ROW_COLUMNS = f"system_id64, number, bodies_known, ground_slots, {_COUNTS}"
# The design's order within a list.
_ORDER = "{0}number DESC, {1}bodies_known DESC, {1}system_id64"


def _values(row: str) -> str:
    """Each list's number of one facts row, as rows of (list, number)."""
    return ", ".join(f"({number}, {row}.{expression})" for number, expression in enumerate(_EXPRESSIONS, 1))


# For each list, the row at one place of the sample: its cut-off.
CUT_OFFS_SQL = (
    "SELECT r.list_no, r.number, r.bodies_known, r.system_id64 FROM (\n"
    "    SELECT l.list_no, l.number, s.bodies_known, s.system_id64,\n"
    f"           row_number() OVER (PARTITION BY l.list_no ORDER BY {_ORDER.format('l.', 's.')}) AS place\n"
    f"    FROM {FACTS} s TABLESAMPLE SYSTEM (%s::real)\n"
    f"    CROSS JOIN LATERAL (VALUES {_values('s')}) AS l(list_no, number)\n"
    "    WHERE l.number > 0\n"
    ") r WHERE r.place = %s"
)

# Every list in one reading of the facts. Innermost (c), the rows at or above
# any list's cut-off by number and known bodies, which is quick to test 36
# times a row; OFFSET 0 keeps PostgreSQL from mixing that test into what
# follows. (By number alone, a number that very many systems share would let
# them all through.) Then each kept row's 36 numbers, those at or above their
# list's whole cut-off (number, then known bodies, then id), put in order list
# by list.
EVERY_LIST_SQL = (
    f"INSERT INTO {TABLE} (build_id, list_no, place, {_ROW_COLUMNS})\n"
    f"SELECT %(build)s, r.list_no, r.place, {', '.join('r.' + c for c in _ROW_COLUMNS.split(', '))}\n"
    "FROM (\n"
    f"    SELECT l.list_no, l.number, c.system_id64, c.bodies_known, c.ground_slots, "
    f"{', '.join('c.' + c for c in COUNT_COLUMNS)},\n"
    f"           row_number() OVER (PARTITION BY l.list_no ORDER BY {_ORDER.format('l.', 'c.')}) AS place\n"
    "    FROM (\n"
    f"        SELECT f.system_id64, f.bodies_known, f.ground_slots, f.strongest, f.purest, f.pairs, "
    f"{', '.join('f.' + c for c in COUNT_COLUMNS)}\n"
    f"        FROM {FACTS} f\n"
    "        WHERE "
    + "\n           OR ".join(
        f"(f.{expression}, f.bodies_known) >= ((%(numbers)s::bigint[])[{number}], "
        f"(%(bodies)s::integer[])[{number}])" for number, expression in enumerate(_EXPRESSIONS, 1))
    + "\n        OFFSET 0\n"
    "    ) c\n"
    f"    CROSS JOIN LATERAL (VALUES {_values('c')}) AS l(list_no, number)\n"
    "    WHERE l.number > 0\n"
    "      AND (l.number, c.bodies_known, -c.system_id64)\n"
    "          >= ((%(numbers)s::bigint[])[l.list_no], (%(bodies)s::integer[])[l.list_no],\n"
    "              -((%(ids)s::bigint[])[l.list_no]))\n"
    ") r\n"
    "WHERE r.place <= %(depth)s"
)


def one_list_sql(list_no: int) -> str:
    """One list read whole: the facts' best ``depth`` rows for it, in order."""
    expression = _EXPRESSIONS[list_no - 1]
    return (
        f"INSERT INTO {TABLE} (build_id, list_no, place, {_ROW_COLUMNS})\n"
        f"SELECT %(build)s, {int(list_no)}, row_number() OVER (ORDER BY {_ORDER.format('r.', 'r.')}), "
        f"{', '.join('r.' + c for c in _ROW_COLUMNS.split(', '))}\n"
        "FROM (\n"
        f"    SELECT f.system_id64, f.{expression} AS number, f.bodies_known, f.ground_slots, "
        f"{', '.join('f.' + c for c in COUNT_COLUMNS)}\n"
        f"    FROM {FACTS} f\n"
        f"    WHERE f.{expression} > 0\n"
        f"    ORDER BY f.{expression} DESC, f.bodies_known DESC, f.system_id64\n"
        "    LIMIT %(depth)s\n"
        ") r"
    )


@dataclass
class ListsReport:
    """What the nightly scoring did to the lists."""

    # Why nothing was done, when nothing was.
    skipped: str | None = None
    # Why the rebuild failed, when it did: the lists are as they were.
    failed: str | None = None
    build_id: int | None = None
    depth: int = DEPTH
    lists: int = 0
    places: int = 0
    # Lists read again whole, because their cut-off turned out too high.
    read_whole: list[int] = field(default_factory=list)
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        if self.skipped:
            return {"skipped": self.skipped}
        if self.failed:
            return {"failed": self.failed}
        return {"build_id": self.build_id, "depth": self.depth, "lists": self.lists, "places": self.places,
                "read_whole": len(self.read_whole), "seconds": round(self.seconds, 1)}

    def lines(self) -> list[str]:
        if self.skipped:
            return [f"Find's lists across the band: {self.skipped}"]
        if self.failed:
            return [f"Find's lists across the band could NOT be written afresh: {self.failed}. "
                    "Searches across the band read the lists as they were; the next run tries again."]
        minutes, seconds = divmod(int(self.seconds), 60)
        out = [f"Find's lists across the band ({VERSION}): {self.lists} lists written afresh, "
               f"{self.places:,} places in all (up to {self.depth:,} each), {minutes} min {seconds} s."]
        if self.read_whole:
            out.append(f"  {len(self.read_whole)} of them had to be read from the whole table "
                       f"(lists {', '.join(str(number) for number in self.read_whole)}).")
        return out


def kept(connection: psycopg.Connection[Any]) -> bool:
    """Whether this database has the lists' tables (migration 033) and the
    facts they are made from."""
    return connection.execute(
        "SELECT to_regclass(%s) IS NOT NULL AND to_regclass(%s) IS NOT NULL", (TABLE, BUILDS)
    ).fetchone()[0] and economy_facts.available(connection)


def facts_ready(connection: psycopg.Connection[Any]) -> bool:
    """Whether a fill of the facts for this build's layout has finished: the
    same test as Find's search makes before it reads them."""
    return any(layout_of(row[0]) == LAYOUT for row in connection.execute(
        f"SELECT version FROM {FILLS} WHERE finished_at IS NOT NULL").fetchall())


def cut_offs(connection: psycopg.Connection[Any], depth: int,
             percent: float = SAMPLE_PERCENT) -> list[tuple[int, int, int]]:
    """For each list, in order, a row (number, known bodies, id) that place
    ``depth`` is expected to be above; ``_NO_CUT_OFF`` for a list the sample
    holds too few systems of to say."""
    place = max(1, math.ceil(SAFETY * depth * percent / 100))
    found = {row[0]: tuple(int(value) for value in row[1:])
             for row in connection.execute(CUT_OFFS_SQL, (percent, place)).fetchall()}
    return [found.get(number, _NO_CUT_OFF) for number in range(1, LIST_COUNT + 1)]


def rebuild(connection: psycopg.Connection[Any], *, depth: int = DEPTH,
            percent: float = SAMPLE_PERCENT, progress: bool = True) -> ListsReport:
    """Write every list afresh from the facts and make the new set the one
    searches read. ``connection`` must be autocommit."""
    report = ListsReport(depth=depth)
    if not kept(connection):
        report.skipped = NOT_KEPT
        return report
    if not facts_ready(connection):
        report.skipped = NOT_YET
        return report
    if progress:
        galaxy.say("  Find's lists across the band: writing them afresh (a few minutes) ...")
    started = time.monotonic()
    before = connection.execute("SELECT setting FROM pg_settings WHERE name = 'statement_timeout'").fetchone()[0]
    connection.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(TIME_LIMIT)))
    try:
        with connection.transaction():
            build = connection.execute(
                f"INSERT INTO {BUILDS} (version, depth) VALUES (%s, %s) RETURNING build_id",
                (VERSION, depth)).fetchone()[0]
            cuts = cut_offs(connection, depth, percent)
            connection.execute(EVERY_LIST_SQL, {
                "build": build, "depth": depth, "numbers": [cut[0] for cut in cuts],
                "bodies": [cut[1] for cut in cuts], "ids": [cut[2] for cut in cuts]})
            lengths = dict(connection.execute(
                f"SELECT list_no, count(*) FROM {TABLE} WHERE build_id = %s GROUP BY list_no", (build,)).fetchall())
            for number in range(1, LIST_COUNT + 1):
                if lengths.get(number, 0) < depth and cuts[number - 1] != _NO_CUT_OFF:
                    connection.execute(f"DELETE FROM {TABLE} WHERE build_id = %s AND list_no = %s",
                                       (build, number))
                    lengths[number] = connection.execute(
                        one_list_sql(number), {"build": build, "depth": depth}).rowcount
                    report.read_whole.append(number)
            connection.execute(f"UPDATE {BUILDS} SET is_current = false WHERE is_current")
            connection.execute(
                f"UPDATE {BUILDS} SET finished_at = clock_timestamp(), is_current = true WHERE build_id = %s",
                (build,))
            connection.execute(f"DELETE FROM {BUILDS} WHERE build_id <> %s", (build,))
            connection.execute(f"ANALYZE {TABLE}")
        report.build_id = build
        report.lists = sum(1 for length in lengths.values() if length)
        report.places = sum(lengths.values())
    except psycopg.Error as error:
        report.failed = galaxy._reason(error)
    finally:
        connection.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(before)))
    report.seconds = time.monotonic() - started
    return report


def status_lines(connection: psycopg.Connection[Any]) -> list[str]:
    if not kept(connection):
        return [f"Find's lists across the band are {NOT_KEPT}."]
    row = connection.execute(
        f"SELECT b.build_id, b.version, b.depth, b.finished_at, "
        f"(SELECT count(*) FROM {TABLE} t WHERE t.build_id = b.build_id), "
        f"(SELECT count(DISTINCT t.list_no) FROM {TABLE} t WHERE t.build_id = b.build_id) "
        f"FROM {BUILDS} b WHERE b.is_current").fetchone()
    if row is None:
        return ["Find's lists across the band: not made yet. The scoring makes them after each run, "
                "once the first fill of Find's facts has finished."]
    _, version, depth, finished_at, places, lists = row
    when = finished_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    note = "" if layout_of(version) == LAYOUT else (
        f" (this code reads {LAYOUT}: searches across the band wait for the next rebuild)")
    return [f"Find's lists across the band: written {when} for {version}, {lists} lists, "
            f"{places:,} places in all (up to {depth:,} each).{note}"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m spansh_delta.economy_top",
        description="Find's ranked lists across the band (migration 033). Read only.")
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
