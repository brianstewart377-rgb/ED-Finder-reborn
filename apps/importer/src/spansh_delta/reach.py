"""Which systems are within 500 ly and 250 ly of an inhabited system: "reach".

    python -m spansh_delta.reach rebuild    work reach out afresh, now
    python -m spansh_delta.reach status     when it was last worked out (read only)

The Raspberry Pi keeps scores and Find rows only for the systems within 500 ly
of an inhabited system, and Find shows those within 250 ly unless it is asked
for more (docs/pi-move.md, "One band, not two"). Migration 028 holds the
answer:

  * ``v3_state.reach_anchor``: the inhabited systems it was worked out from,
    with their positions;
  * ``v3_state.system_reach``: every system within 500 ly of one of them, and
    whether it is within 250 ly too;
  * ``v3_state.reach_log``: each time it was worked out afresh.

Working it out afresh (``rebuild``) measures every system in the galaxy
against the systems that are inhabited now: those with a population in
``v3_state.system_state``, which the nightly state refresh keeps current. Only
the rows that differ are changed, in one transaction at the end, so the app can
be in use meanwhile. It uses several of the machine's cores.

The nightly scoring calls :func:`update` first. It

  * refuses if reach has never been worked out: without it the scoring would
    score systems anywhere in the galaxy;
  * works reach out afresh when that was last done a week or more ago, or when
    so many systems wait for scores (after a full galaxy file) that measuring
    them one at a time would take longer;
  * otherwise measures each system on the waiting list that has no row yet
    (mostly systems the galaxy update has just found) against the same
    inhabited systems as last time, and gives it a row if it is within 500 ly.

So a new colony counts from the next weekly rebuild. The systems it brings
within 500 ly (or 250) lie 250 to 500 ly from it, far from where anyone
colonises next, so a few days do not matter.

Without migration 028 (the old server) none of this is used, and the scoring
works as it always did.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import sys
import time
from typing import Any, Sequence

import psycopg
from psycopg import sql

from . import apply as galaxy

REACH_LY = 500
NEAR_LY = 250
# Work reach out afresh at least this often, so new colonies count.
REBUILD_EVERY = timedelta(days=7)
# With more systems than this waiting for scores, working reach out afresh for
# the whole galaxy, on several cores, is quicker than measuring each of them.
REBUILD_WHEN_WAITING = 2_000_000
# Systems from the waiting list measured in one statement.
BATCH = 20_000
# Cores for working reach out afresh. The Pi has four.
WORKERS = 4
# Working reach out afresh stops, and changes nothing, if it finds fewer
# inhabited systems, or fewer systems in reach, than this share of last time:
# colonies do not vanish, so that would mean the system state is damaged (a
# failed load, say) and the answer would be wrong.
SHRINK_LIMIT = 0.9

ANCHORS = "v3_state.reach_anchor"
REACH = "v3_state.system_reach"
LOG = "v3_state.reach_log"
# Built beside the real tables while reach is worked out afresh, then dropped.
NEXT_ANCHORS = ("v3_state", "reach_anchor_next")
NEXT_REACH = ("v3_state", "system_reach_next")

NOT_WORKED_OUT = (
    "reach has not been worked out yet, so the scoring cannot tell which systems to score. "
    "Work it out once first (python3 galaxy_update.py reach on the machine; it takes a while)")


def available(connection: psycopg.Connection[Any]) -> bool:
    """Whether this database keeps reach (migration 028)."""
    found = connection.execute("SELECT to_regclass(%s), to_regclass(%s), to_regclass(%s)",
                               (ANCHORS, REACH, LOG)).fetchone()
    return all(value is not None for value in found)


def _cube_schema(connection: psycopg.Connection[Any]) -> str:
    row = connection.execute(
        "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
        "WHERE e.extname = 'cube'").fetchone()
    if row is None:
        raise galaxy.Refused("the database lacks the cube extension, which reach is measured with")
    return row[0]


def within(cube: str, anchors: sql.Composable, radius: int) -> sql.Composed:
    """True for a system ``s`` (columns x_ly, y_ly, z_ly) within ``radius`` ly
    of one of ``anchors``. A box around the system finds, through the anchors'
    index, the ones worth measuring; then the distance itself decides."""
    c = sql.Identifier(cube)
    r = sql.Literal(radius)
    return sql.SQL(
        "EXISTS (SELECT 1 FROM {anchors} a "
        "WHERE a.position OPERATOR({c}.<@) {c}.cube("
        "ARRAY[s.x_ly - {r}, s.y_ly - {r}, s.z_ly - {r}], ARRAY[s.x_ly + {r}, s.y_ly + {r}, s.z_ly + {r}]) "
        "AND {c}.cube_distance(a.position, {c}.cube(ARRAY[s.x_ly, s.y_ly, s.z_ly])) <= {r})"
    ).format(anchors=anchors, c=c, r=r)


@dataclass
class LastTime:
    worked_out_at: datetime
    anchors: int
    systems: int
    within_250: int


def last_time(connection: psycopg.Connection[Any]) -> LastTime | None:
    row = connection.execute(
        f"SELECT worked_out_at, anchors, systems, within_250 FROM {LOG} "
        "ORDER BY reach_id DESC LIMIT 1").fetchone()
    return LastTime(*row) if row else None


@dataclass
class ReachReport:
    # Worked out afresh this time, and why; otherwise systems were measured.
    rebuilt: bool = False
    why: str = ""
    anchors: int = 0
    systems: int = 0
    within_250: int = 0
    added: int = 0
    removed: int = 0
    crossed_250: int = 0
    # Systems on the waiting list that had no row and were measured.
    measured: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"rebuilt": self.rebuilt, "anchors": self.anchors, "systems": self.systems,
                "within_250": self.within_250, "added": self.added, "removed": self.removed,
                "crossed_250": self.crossed_250, "measured": self.measured,
                "seconds": round(self.seconds, 1)}

    def lines(self) -> list[str]:
        def row(label: str, number: int) -> str:
            return f"{label:<48}{number:>10,}"

        minutes, seconds = divmod(int(self.seconds), 60)
        if self.rebuilt:
            return [f"Reach worked out afresh ({self.why}), in {minutes} min {seconds} s:",
                    row("  inhabited systems it was measured from:", self.anchors),
                    row(f"  systems within {REACH_LY} ly:", self.systems),
                    row(f"  of those, within {NEAR_LY} ly:", self.within_250),
                    row(f"  came within {REACH_LY} ly since last time:", self.added),
                    row(f"  no longer within {REACH_LY} ly:", self.removed),
                    row(f"  crossed the {NEAR_LY} ly line:", self.crossed_250)]
        return [row("Waiting systems measured for reach (new ones):", self.measured),
                row(f"  of those, within {REACH_LY} ly:", self.added)]


def _say(progress: bool, message: str) -> None:
    if progress:
        galaxy.say(message)


def rebuild(connection: psycopg.Connection[Any], target: galaxy.Target, *, why: str = "asked for",
            progress: bool = True) -> ReachReport:
    """Work reach out afresh for the whole galaxy.

    ``connection`` must be autocommit, and the caller must hold the galaxy
    update's lock, so that no galaxy update or scoring changes the systems
    meanwhile. Changes nothing unless it gets to the end.
    """
    if not connection.autocommit:
        raise ValueError("the connection must be in autocommit mode")
    started = time.monotonic()
    report = ReachReport(rebuilt=True, why=why)
    cube = _cube_schema(connection)
    c = sql.Identifier(cube)
    systems = sql.Identifier(target.schema, "systems")
    next_anchors, next_reach = sql.Identifier(*NEXT_ANCHORS), sql.Identifier(*NEXT_REACH)
    before = last_time(connection)
    settings = dict(connection.execute(
        "SELECT name, setting FROM pg_settings "
        "WHERE name IN ('statement_timeout', 'max_parallel_workers_per_gather')").fetchall())
    connection.execute("SET statement_timeout = 0")
    connection.execute(sql.SQL("SET max_parallel_workers_per_gather = {}").format(sql.Literal(WORKERS)))
    try:
        connection.execute(sql.SQL("DROP TABLE IF EXISTS {}, {}").format(next_anchors, next_reach))
        connection.execute(sql.SQL(
            "CREATE UNLOGGED TABLE {} AS SELECT system_id64, {c}.cube(ARRAY[x_ly, y_ly, z_ly]) AS position "
            "FROM v3_state.system_state WHERE population > 0").format(next_anchors, c=c))
        connection.execute(sql.SQL("CREATE INDEX ON {} USING gist (position)").format(next_anchors))
        connection.execute(sql.SQL("ANALYZE {}").format(next_anchors))
        report.anchors = connection.execute(sql.SQL("SELECT count(*) FROM {}").format(next_anchors)).fetchone()[0]
        if report.anchors == 0:
            raise galaxy.Refused("no system is inhabited according to v3_state.system_state; "
                                 "has the state refresh run? Reach was left as it was")
        if before and report.anchors < SHRINK_LIMIT * before.anchors:
            raise galaxy.Refused(
                f"only {report.anchors:,} systems are inhabited now, against {before.anchors:,} the last "
                "time reach was worked out. Colonies do not vanish like that, so the system state is "
                "probably damaged (look at the last state refresh). Reach was left as it was")
        _say(progress, f"Working out which systems are within {REACH_LY} ly of the "
                       f"{report.anchors:,} inhabited ones (this takes a while) ...")
        connection.execute(sql.SQL(
            "CREATE UNLOGGED TABLE {next} AS SELECT s.id64 AS system_id64, {near} AS within_250 "
            "FROM ONLY {systems} s WHERE {far}").format(
                next=next_reach, systems=systems, near=within(cube, next_anchors, NEAR_LY),
                far=within(cube, next_anchors, REACH_LY)))
        connection.execute(sql.SQL("ALTER TABLE {} ADD PRIMARY KEY (system_id64)").format(next_reach))
        connection.execute(sql.SQL("ANALYZE {}").format(next_reach))
        report.systems, report.within_250 = connection.execute(sql.SQL(
            "SELECT count(*), count(*) FILTER (WHERE within_250) FROM {}").format(next_reach)).fetchone()
        if before and report.systems < SHRINK_LIMIT * before.systems:
            raise galaxy.Refused(
                f"only {report.systems:,} systems are within {REACH_LY} ly now, against "
                f"{before.systems:,} last time. That is not what new colonies do, so something is "
                "wrong with the system state or the galaxy. Reach was left as it was")
        with connection.transaction():
            actions = dict(connection.execute(sql.SQL(
                "WITH merged AS (MERGE INTO {reach} r USING {next} n ON r.system_id64 = n.system_id64 "
                "WHEN MATCHED AND r.within_250 <> n.within_250 THEN UPDATE SET within_250 = n.within_250 "
                "WHEN NOT MATCHED BY TARGET THEN INSERT (system_id64, within_250) "
                "VALUES (n.system_id64, n.within_250) "
                "WHEN NOT MATCHED BY SOURCE THEN DELETE "
                "RETURNING merge_action() AS action) "
                "SELECT action, count(*) FROM merged GROUP BY action").format(
                    reach=sql.SQL(REACH), next=next_reach)).fetchall())
            report.added = actions.get("INSERT", 0)
            report.removed = actions.get("DELETE", 0)
            report.crossed_250 = actions.get("UPDATE", 0)
            connection.execute(sql.SQL(
                "MERGE INTO {anchors} a USING {next} n ON a.system_id64 = n.system_id64 "
                "WHEN MATCHED AND a.position OPERATOR({c}.<>) n.position THEN UPDATE SET position = n.position "
                "WHEN NOT MATCHED BY TARGET THEN INSERT (system_id64, position) VALUES (n.system_id64, n.position) "
                "WHEN NOT MATCHED BY SOURCE THEN DELETE").format(
                    anchors=sql.SQL(ANCHORS), next=next_anchors, c=c))
            report.seconds = time.monotonic() - started
            connection.execute(
                f"INSERT INTO {LOG} (anchors, systems, within_250, added, removed, crossed_250, seconds) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (report.anchors, report.systems, report.within_250, report.added, report.removed,
                 report.crossed_250, round(report.seconds, 1)))
    finally:
        try:
            connection.execute(sql.SQL("DROP TABLE IF EXISTS {}, {}").format(next_anchors, next_reach))
            for name, value in settings.items():
                connection.execute(sql.SQL("SET {} = {}").format(sql.Identifier(name), sql.Literal(value)))
        except psycopg.Error:
            pass
    return report


def update(connection: psycopg.Connection[Any], target: galaxy.Target, *, progress: bool = True) -> ReachReport:
    """Bring reach up to date before the scoring. See the top of this module.

    ``connection`` must be autocommit, and the caller must hold the galaxy
    update's lock.
    """
    started = time.monotonic()
    last = last_time(connection)
    if last is None:
        raise galaxy.Refused(NOT_WORKED_OUT)
    waiting = connection.execute(f"SELECT count(*) FROM {galaxy.QUEUE_TABLE}").fetchone()[0]
    since = datetime.now(timezone.utc) - last.worked_out_at
    if since >= REBUILD_EVERY:
        return rebuild(connection, target, why=f"last done {since.days} days ago", progress=progress)
    if waiting >= REBUILD_WHEN_WAITING:
        return rebuild(connection, target, why=f"{waiting:,} systems are waiting for scores",
                       progress=progress)
    report = ReachReport()
    cube = _cube_schema(connection)
    take = sql.SQL(
        "SELECT q.system_id64 FROM {queue} q WHERE q.system_id64 > %s "
        "AND NOT EXISTS (SELECT 1 FROM {reach} r WHERE r.system_id64 = q.system_id64) "
        "ORDER BY q.system_id64 LIMIT %s").format(queue=sql.SQL(galaxy.QUEUE_TABLE), reach=sql.SQL(REACH))
    measure = sql.SQL(
        "INSERT INTO {reach} (system_id64, within_250) "
        "SELECT s.id64, {near} FROM ONLY {systems} s WHERE s.id64 = ANY(%s::bigint[]) AND {far} "
        "ON CONFLICT (system_id64) DO NOTHING").format(
            reach=sql.SQL(REACH), systems=sql.Identifier(target.schema, "systems"),
            near=within(cube, sql.SQL(ANCHORS), NEAR_LY), far=within(cube, sql.SQL(ANCHORS), REACH_LY))
    after = -1
    while True:
        ids = [row[0] for row in connection.execute(take, (after, BATCH)).fetchall()]
        if not ids:
            break
        after = ids[-1]
        report.measured += len(ids)
        report.added += connection.execute(measure, (ids,)).rowcount
    report.seconds = time.monotonic() - started
    return report


def drop_outside(connection: psycopg.Connection[Any]) -> int:
    """Take off the waiting list every system further than 500 ly from an
    inhabited system. Its scores are not wanted; if a colony later brings it
    within reach, it is scored then, from its rows as they are by that time.

    A part of the list at a time, so that a very long list (after a full galaxy
    file) is not one statement that runs for an hour."""
    window = f"SELECT system_id64 FROM {galaxy.QUEUE_TABLE} WHERE system_id64 > %s ORDER BY system_id64 LIMIT %s"
    drop = (f"DELETE FROM {galaxy.QUEUE_TABLE} q WHERE q.system_id64 = ANY(%s::bigint[]) "
            f"AND NOT EXISTS (SELECT 1 FROM {REACH} r WHERE r.system_id64 = q.system_id64)")
    dropped, after = 0, -1
    while True:
        ids = [row[0] for row in connection.execute(window, (after, BATCH * 5)).fetchall()]
        if not ids:
            return dropped
        after = ids[-1]
        dropped += connection.execute(drop, (ids,)).rowcount


def without_scores(connection: psycopg.Connection[Any], generation_id: str) -> list[int]:
    """Systems in reach that have no scores in the published set, in id order:
    systems that came within reach since they were last scored, mostly."""
    return [row[0] for row in connection.execute(
        f"SELECT r.system_id64 FROM {REACH} r WHERE NOT EXISTS ("
        "SELECT 1 FROM v3_derived.system_rating_vector v "
        "WHERE v.derived_generation_id = %s AND v.system_id64 = r.system_id64) "
        "ORDER BY r.system_id64", (generation_id,)).fetchall()]


def status_lines(connection: psycopg.Connection[Any]) -> list[str]:
    if not available(connection):
        return ["Reach is not kept in this database (migration 028 has not been applied)."]
    last = last_time(connection)
    if last is None:
        return ["Reach has not been worked out yet."]
    when = last.worked_out_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    systems = connection.execute(
        f"SELECT count(*), count(*) FILTER (WHERE within_250) FROM {REACH}").fetchone()
    return [f"Reach last worked out afresh {when}, from {last.anchors:,} inhabited systems.",
            f"Systems within {REACH_LY} ly now: {systems[0]:,} (within {NEAR_LY} ly: {systems[1]:,})."]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m spansh_delta.reach",
        description="rebuild: work out afresh which systems are within 500 ly and 250 ly of an "
                    "inhabited system. status: say when that was last done; read only.")
    parser.add_argument("mode", choices=("rebuild", "status"))
    args = parser.parse_args(argv)
    connection = galaxy.connect()
    if connection is None:
        return galaxy.EXIT_NO_CONNECTION
    with connection:
        try:
            if args.mode == "status":
                for line in status_lines(connection):
                    galaxy.say(line)
                return 0
            galaxy.say("Working out reach afresh")
            if not available(connection):
                raise galaxy.Refused("this database does not keep reach (migration 028 has not been applied)")
            target = galaxy.preflight(connection, need_queue=False)
            if not connection.execute("SELECT pg_try_advisory_lock(%s)", (galaxy.LOCK_KEY,)).fetchone()[0]:
                raise galaxy.UpdateError("a galaxy update or a scoring run is going on; this did not start")
            try:
                report = rebuild(connection, target)
            finally:
                try:
                    connection.execute("SELECT pg_advisory_unlock(%s)", (galaxy.LOCK_KEY,))
                except psycopg.Error:
                    pass
        except galaxy.Refused as error:
            galaxy.say(f"REFUSED: {error}")
            return 2
        except (galaxy.UpdateError, ValueError, psycopg.Error) as error:
            galaxy.say(f"FAILED: {galaxy._reason(error) if isinstance(error, psycopg.Error) else error}")
            return 1
    for line in report.lines():
        galaxy.say(line)
    galaxy.say("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
