"""Score every system's colony types afresh, from score inputs already stored.

    python -m spansh_delta.colony_types apply [--limit N]

Run once after a deploy that changes how colony types are scored: a new
``ARCHETYPE_VERSION`` in ``shared_contracts/v3_system_archetype_model.py``.
Until then each colony type row still says what the old rule gave, and the
API will not explain a row the deployed rule did not write.

A colony type score is worked out from two things the nightly scoring keeps
for each system: its seven economy potentials (``system_rating_vector``) and,
per economy, how many bodies suit it and how well (``economy_opportunity``).
Neither depends on how colony types are scored, so neither is touched here,
and no Spansh file is needed. Find's rows hold no colony type scores (Find
reads them from the colony type rows), so they are not touched either.

Every row whose version is not the deployed model's is worked out again and
replaced in place: systems in order, a batch at a time, one transaction per
batch. So this can be stopped and started again at any point; a second run
finds the finished systems already done. It takes the same lock as the
nightly scoring, so the two never run at the same time.

The score tables refuse changes except from a transaction that announces
itself as the nightly update (migration 025). Every write here does that.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any, Sequence

import psycopg
from psycopg import sql

from scripts import v3_system_archetype as archetype_build
from shared_contracts import v3_system_archetype_model as model

from . import apply as galaxy

BATCH_SYSTEMS = 5_000
SCORE_COLUMNS = ("archetype_version", *archetype_build.STORED_VALUE_COLUMNS, "computed_at")
# Tier S: the summary line counts systems at or above it, before and after.
TOP_TIER = 88

# The model's own two reads (VECTOR_READ_SQL, OPPORTUNITY_READ_SQL), asked for
# a batch of systems instead of one system or one build chunk. A test holds
# them to the model's: the same rows for every system.
VECTOR_BATCH_SQL = """
SELECT v.system_id64, v.potential, v.quality, v.quality_min, v.quality_max,
       v.completeness, v.confidence
  FROM v3_derived.system_rating_vector v
 WHERE v.derived_generation_id = %(generation_id)s
   AND v.system_id64 = ANY(%(system_ids)s::bigint[])
 ORDER BY v.system_id64
"""
OPPORTUNITY_BATCH_SQL = """
SELECT o.system_id64, o.economy_ordinal, count(*)::int, sum(o.local_score)::bigint
  FROM v3_derived.economy_opportunity o
  JOIN v3_derived.system_rating_vector v
    ON v.derived_generation_id = o.derived_generation_id
   AND v.system_id64 = o.system_id64
 WHERE o.derived_generation_id = %(generation_id)s
   AND o.system_id64 = ANY(%(system_ids)s::bigint[])
 GROUP BY o.system_id64, o.economy_ordinal
 ORDER BY o.system_id64, o.economy_ordinal
"""


@dataclass
class Report:
    version: str = model.ARCHETYPE_VERSION
    to_do: int = 0
    rescored: int = 0
    # Systems with a colony type row but no score inputs: left as they are.
    without_inputs: list[int] = field(default_factory=list)
    higher: int = 0
    lower: int = 0
    same: int = 0
    top_before: int = 0
    top_after: int = 0
    left: int = 0
    stopped_early: bool = False
    seconds: float = 0.0

    def lines(self) -> list[str]:
        minutes, seconds = divmod(int(self.seconds), 60)
        rate = f", {self.rescored / self.seconds:,.0f} a second" if self.seconds >= 1 else ""
        lines = [
            f"Colony types scored with {self.version}: {self.rescored:,} of {self.to_do:,} "
            f"systems, in {minutes} min {seconds} s{rate}.",
            f"  Best colony potential: {self.higher:,} higher, {self.lower:,} lower, "
            f"{self.same:,} the same.",
            f"  Tier S ({TOP_TIER} or more): {self.top_before:,} of these systems before, "
            f"{self.top_after:,} now.",
        ]
        if self.without_inputs:
            shown = ", ".join(str(system_id) for system_id in self.without_inputs[:5])
            lines.append(f"  {len(self.without_inputs):,} systems have no score inputs and were "
                         f"left as they were (for example {shown}). The nightly scoring "
                         f"scores them when they next change.")
        if self.stopped_early:
            lines.append(f"  Stopped at the limit: {self.left:,} systems still have the old "
                         f"scores. Run it again to carry on.")
        else:
            lines.append(f"  Systems still with older colony type scores: {self.left:,}.")
        return lines


def _published_generation(connection: psycopg.Connection[Any]) -> str:
    rows = connection.execute(
        """SELECT g.derived_generation_id::text
             FROM v3_meta.current_derived_generation c
             JOIN v3_meta.derived_generation g USING (derived_generation_id)
            WHERE g.lifecycle_state = 'PUBLISHED'"""
    ).fetchall()
    if len(rows) != 1:
        raise galaxy.Refused("there is not exactly one published set of scores")
    return rows[0][0]


def _still_to_do(connection: psycopg.Connection[Any], generation_id: str) -> int:
    return connection.execute(
        "SELECT count(*) FROM v3_derived.system_archetype "
        "WHERE derived_generation_id = %s AND archetype_version <> %s",
        (generation_id, model.ARCHETYPE_VERSION)).fetchone()[0]


def score_batch(connection: psycopg.Connection[Any], generation_id: str,
                system_ids: Sequence[int]) -> tuple[dict[int, dict[str, Any]], list[int]]:
    """The deployed model's colony type scores for these systems, and the
    systems that have no score inputs to work them out from."""
    scope = {"generation_id": generation_id, "system_ids": list(system_ids)}
    vectors = {int(row[0]): model.SystemVectorRow.from_row(row)
               for row in connection.execute(VECTOR_BATCH_SQL, scope)}
    groups: dict[int, list[tuple[int, int, int]]] = {}
    for system_id, ordinal, count, local_sum in connection.execute(OPPORTUNITY_BATCH_SQL, scope):
        groups.setdefault(int(system_id), []).append((int(ordinal), int(count), int(local_sum)))
    scored = {system_id: model.score_raw_system(vector, tuple(groups.get(system_id, ())))
              for system_id, vector in vectors.items()}
    return scored, [system_id for system_id in system_ids if system_id not in vectors]


def _write(connection: psycopg.Connection[Any], generation_id: str,
           scored: dict[int, dict[str, Any]]) -> None:
    now = datetime.now(timezone.utc)
    columns = sql.SQL(", ").join(map(sql.Identifier, SCORE_COLUMNS))
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL edfinder.nightly_update = 'on'")
        cursor.execute("CREATE TEMP TABLE IF NOT EXISTS colony_type_rows "
                       "(LIKE v3_derived.system_archetype) ON COMMIT DELETE ROWS")
        with cursor.copy(sql.SQL(
                "COPY colony_type_rows (derived_generation_id, system_id64, {}) FROM STDIN"
        ).format(columns)) as copy:
            for system_id, values in scored.items():
                copy.write_row((generation_id, system_id, model.ARCHETYPE_VERSION,
                                *(values[column] for column in archetype_build.STORED_VALUE_COLUMNS),
                                now))
        cursor.execute(sql.SQL(
            "UPDATE v3_derived.system_archetype a SET ({columns}) = ROW({new}) "
            "FROM colony_type_rows t "
            "WHERE a.derived_generation_id = %s AND a.system_id64 = t.system_id64"
        ).format(columns=columns,
                 new=sql.SQL(", ").join(sql.Identifier("t", name) for name in SCORE_COLUMNS)),
            (generation_id,))
        if cursor.rowcount != len(scored):
            raise galaxy.UpdateError(
                f"{cursor.rowcount} colony type rows were replaced, not {len(scored)}; "
                "nothing from this batch was kept")


def rescore(connection: psycopg.Connection[Any], *, limit: int | None = None,
            progress: bool = True, batch_systems: int = BATCH_SYSTEMS) -> Report:
    """Give every colony type row the deployed model's scores.

    ``connection`` must be autocommit.
    """
    if not connection.autocommit:
        raise ValueError("the connection must be in autocommit mode")
    report = Report()
    started = time.monotonic()
    connection.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(galaxy.STATEMENT_TIMEOUT)))
    connection.execute("SET lock_timeout = '60s'")
    if not connection.execute("SELECT pg_try_advisory_lock(%s)", (galaxy.LOCK_KEY,)).fetchone()[0]:
        raise galaxy.UpdateError("a galaxy update or a scoring run is going on; this one did not start")
    try:
        generation_id = _published_generation(connection)
        report.to_do = _still_to_do(connection, generation_id)
        if progress:
            galaxy.say(f"{report.to_do:,} systems have colony type scores from an older rule.")
        take = ("SELECT system_id64, archetype_version, best_colony_potential "
                "FROM v3_derived.system_archetype "
                "WHERE derived_generation_id = %s AND system_id64 > %s "
                "ORDER BY system_id64 LIMIT %s")
        last, after = started, -1
        while True:
            if limit is not None and report.rescored >= limit:
                report.stopped_early = True
                break
            rows = connection.execute(take, (generation_id, after, batch_systems)).fetchall()
            if not rows:
                break
            after = rows[-1][0]
            before = {int(system_id): best for system_id, version, best in rows
                      if version != model.ARCHETYPE_VERSION}
            if limit is not None:
                before = dict(list(before.items())[:limit - report.rescored])
            if not before:
                continue
            scored, missing = score_batch(connection, generation_id, list(before))
            report.without_inputs.extend(missing)
            if scored:
                _write(connection, generation_id, scored)
            for system_id, values in scored.items():
                old, new = before[system_id], values["best_colony_potential"]
                report.higher += new > old
                report.lower += new < old
                report.same += new == old
                report.top_before += old >= TOP_TIER
                report.top_after += new >= TOP_TIER
            report.rescored += len(scored)
            if progress and time.monotonic() - last >= galaxy.PROGRESS_SECONDS:
                last = time.monotonic()
                done, elapsed = report.rescored, last - started
                remaining = max(report.to_do - done - len(report.without_inputs), 0)
                eta = f", about {int(remaining * elapsed / done) // 60} min to go" if done else ""
                galaxy.say(f"  ... {done:,} of {report.to_do:,} systems done, "
                           f"{int(elapsed) // 60} min{eta}")
        report.left = _still_to_do(connection, generation_id)
    finally:
        try:
            connection.execute("SELECT pg_advisory_unlock(%s)", (galaxy.LOCK_KEY,))
        except psycopg.Error:
            pass
    report.seconds = time.monotonic() - started
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m spansh_delta.colony_types",
        description="apply: score every system's colony types with the deployed rule, "
                    "from the score inputs already stored.")
    parser.add_argument("mode", choices=("apply",))
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after this many systems (for timing)")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        galaxy.say("FAILED: --limit must be 1 or more")
        return 1
    connection = galaxy.connect()
    if connection is None:
        return galaxy.EXIT_NO_CONNECTION
    with connection:
        galaxy.say(f"Scoring colony types afresh with {model.ARCHETYPE_VERSION}")
        try:
            report = rescore(connection, limit=args.limit)
        except galaxy.UpdateError as error:
            galaxy.say(f"FAILED: {error}")
            return 1
        for line in report.lines():
            galaxy.say(line)
        galaxy.say("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
