"""Work out scores and Find rows again, and check them against what is stored.

    python -m spansh_delta.scores check FILE     read only
    python -m spansh_delta.scores apply FILE     write the scores that are owed

A system's scores come from two things: its rows in the galaxy tables, and the
sub-type of each body ("Earth-like world", "Icy body"), which the galaxy tables
do not hold. Only Spansh's record has it. So a system can be scored only while
its record is in hand, and only if that record and our rows describe the same
bodies. This module takes the systems of FILE for which that is true and runs
the frozen scoring code on them (``scripts/ratings_v4``, ``scripts/v3_system_*``
and ``shared_contracts``: copied unchanged, and called, not rewritten).

``check`` writes nothing. It answers two questions:

  * Systems whose scoring inputs nothing has changed since they were scored:
    do we get exactly the stored scores and Find row back? If so, this code
    and the stored data agree, and it is safe to let it write.
  * Systems the galaxy update has rewritten and that wait for new scores: can
    each be scored, and if not, why not?

``apply`` is run on the same file straight after the galaxy update:

  * A system waiting for new scores, whose rows match its record, gets them:
    each of its score tables is replaced where it differs, its Find row is
    rebuilt, and it leaves the waiting list. All of that is one transaction.
  * A system that is not waiting is left alone, unless its scores come out
    different (Spansh changed a body's sub-type and nothing else). Then it is
    treated the same way.
  * A system that only gained or lost a station needs no record: its Find row
    is rebuilt from what is stored, wherever it is on the waiting list.
  * A system that cannot be scored stays on the waiting list.

The score tables refuse changes except from a transaction that announces
itself as the nightly update (migration 025). Every write here does that.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence

import psycopg
from psycopg import sql

from scripts import v3_system_archetype as archetype_build
from scripts import v3_system_search as search_build
from scripts.ratings_v4 import production_generation as build
from scripts.ratings_v4.canonical_stream import CanonicalSnapshot
from shared_contracts import v3_system_archetype_model as model

from . import apply as galaxy
from .normalize import NormalizedSystem, classify_material_change, normalize_system

# The galaxy tables the scores are worked out from. A change in any other
# table (stations, composition, genera) leaves a system's scores as they are.
SCORING_TABLES = ("systems", "bodies", "rings", "body_signal_current")

# The columns of a Find row, in the order the frozen builder writes them
# (scripts/v3_system_search.py, _insert_chunk). A test holds the two together.
FIND_COLUMNS = (
    "derived_generation_id", "system_id64", "name", "x_ly", "y_ly", "z_ly", "position_ly",
    "galaxy_region_id", "region_name", "main_star_class", "body_count", "landable_count",
    "station_count", "has_rings", "has_biologicals", "has_geologicals",
    "has_terraformable", "source_observed_at", "completeness", "confidence",
    "elw_count", "ww_count", "ammonia_count", "terraformable_count", "gas_giant_count",
    "hmc_count", "metal_rich_count", "rocky_count", "rocky_ice_count", "icy_count",
    "black_hole_count", "neutron_count", "white_dwarf_count", "other_star_count",
    "ring_count", "walkable_count", "bio_signal_total", "geo_signal_total",
)
ARCHETYPE_COLUMNS = ("derived_generation_id", "system_id64", *archetype_build.STORED_VALUE_COLUMNS)

# Columns that say which build wrote a row, not what the row says.
_NOT_CONTENT = {"derived_generation_id", "chunk_ordinal"}
# Columns that describe the Spansh record a score was made from (a fingerprint
# of it, and when a body's sub-type was seen). They move when Spansh re-stamps
# a body, without any score changing.
RECORD_STAMPS = {"subtype_projection_sha256", "subtype_observed_at"}
_CHUNK_PREDICATE = "AND v.chunk_ordinal=%(chunk_ordinal)s"
_SYSTEMS_PREDICATE = "AND v.system_id64=ANY(%(system_ids)s::bigint[])"


def find_rows_sql(schema: str) -> sql.Composable:
    """The frozen Find-row query, asked for named systems instead of one chunk."""
    text = search_build.projection_query_sql()
    if text.count(_CHUNK_PREDICATE) != 1:
        raise RuntimeError("the Find-row query no longer selects its systems the way this expects")
    return sql.SQL(text.replace(_CHUNK_PREDICATE, _SYSTEMS_PREDICATE)).format(
        schema=sql.Identifier(schema))


def _plain(value: Any) -> Any:
    if isinstance(value, memoryview):
        return bytes(value)
    if isinstance(value, (list, tuple)):
        return tuple(_plain(item) for item in value)
    return value


@dataclass
class Scores:
    """Score rows by table, each a list of plain tuples, for some systems."""

    vectors: dict[int, tuple] = field(default_factory=dict)
    bodies: dict[int, list[tuple]] = field(default_factory=lambda: defaultdict(list))
    opportunities: dict[int, list[tuple]] = field(default_factory=lambda: defaultdict(list))
    archetypes: dict[int, tuple] = field(default_factory=dict)

    def tables(self, system_id: int) -> dict[str, tuple[tuple[str, ...], list[tuple]]]:
        return {
            "system_rating_vector": (build.VECTOR_COLUMNS,
                                     [self.vectors[system_id]] if system_id in self.vectors else []),
            "body_mechanics": (build.BODY_COLUMNS, self.bodies.get(system_id, [])),
            "economy_opportunity": (build.OPPORTUNITY_COLUMNS, self.opportunities.get(system_id, [])),
            "system_archetype": (ARCHETYPE_COLUMNS,
                                 [self.archetypes[system_id]] if system_id in self.archetypes else []),
        }


def _row_key(row: tuple) -> tuple:
    # after the generation: system, then body and economy where the table has them
    return tuple(str(value) for value in row[1:4])


def describe(table: str, columns: Sequence[str], ours: Sequence[tuple], stored: Sequence[tuple],
             limit: int = 2) -> list[str]:
    """A few differences between two sets of rows, as "stored -> now"."""
    if len(ours) != len(stored):
        return [f"{table}: {len(stored)} rows stored, {len(ours)} now"]
    out: list[str] = []
    for mine, theirs in zip(sorted(ours, key=_row_key), sorted(stored, key=_row_key), strict=True):
        for name, left, right in zip(columns, mine, theirs, strict=True):
            if name in _NOT_CONTENT or name in RECORD_STAMPS or _plain(left) == _plain(right):
                continue
            where = f" (body {mine[2]})" if table in ("body_mechanics", "economy_opportunity") else ""
            out.append(f"{table}.{name}{where}: {repr(_plain(right))[:60]} -> {repr(_plain(left))[:60]}")
            if len(out) >= limit:
                return out
    return out


def differences(columns: Sequence[str], ours: Sequence[tuple], stored: Sequence[tuple]) -> set[str]:
    """Names of the columns in which two sets of rows differ ("rows" if the
    number of rows does)."""
    if len(ours) != len(stored):
        return {"rows"}
    found: set[str] = set()
    for mine, theirs in zip(sorted(ours, key=_row_key), sorted(stored, key=_row_key), strict=True):
        for name, left, right in zip(columns, mine, theirs, strict=True):
            if name not in _NOT_CONTENT and _plain(left) != _plain(right):
                found.add(name)
    return found


class Scorer:
    """Runs the frozen scoring code for systems whose records are in hand."""

    def __init__(self, connection: psycopg.Connection[Any]):
        self.connection = connection
        self.snapshot = CanonicalSnapshot.pin(connection)
        rows = connection.execute(
            """SELECT g.derived_generation_id::text
                 FROM v3_meta.current_derived_generation c
                 JOIN v3_meta.derived_generation g USING (derived_generation_id)
                WHERE g.lifecycle_state = 'PUBLISHED'"""
        ).fetchall()
        if len(rows) != 1:
            raise galaxy.Refused("there is not exactly one published set of scores")
        self.generation_id: str = rows[0][0]
        self.metadata = self._manifest()
        self._find_rows = find_rows_sql(self.snapshot.schema)
        self._new_chunk: int | None = None
        connection.execute("SET jit = off")  # as the frozen builder does for this query

    def _manifest(self) -> dict[str, Any]:
        """The source runs whose rows the scoring code accepts.

        It accepts a row only if the run that wrote it is listed as an input
        of the galaxy. The full import is listed in the database. Galaxy
        updates are recorded as runs but not in that list, so they are added
        here. A galaxy update commits whole batches, so every row it wrote is
        complete even if the run later stopped early: such a run is listed as
        succeeded. One still RUNNING is left out.
        """
        metadata = dict(self.snapshot.metadata)
        inputs = list(metadata.get("generation_inputs") or [])
        ordinal = max((int(entry["input"]["input_ordinal"]) for entry in inputs), default=-1)
        for run, artifact, source in self.connection.execute(
            """SELECT to_jsonb(r), to_jsonb(a), to_jsonb(s)
                 FROM v3_source.source_run r
                 JOIN v3_source.source_artifact a USING (artifact_id)
                 JOIN v3_source.source s ON s.source_id = r.source_id
                WHERE r.acquisition_kind = 'DELTA' AND r.trust_zone = 'CANONICAL'
                  AND r.importer_version = %s
                  AND r.run_state IN ('SUCCEEDED', 'PARTIAL', 'FAILED')
                ORDER BY r.started_at, r.source_run_id""",
            (galaxy.UPDATER_VERSION,),
        ):
            ordinal += 1
            inputs.append({
                "input": {
                    "generation_id": metadata["generation_id"], "source_run_id": run["source_run_id"],
                    "source_id": run["source_id"], "artifact_id": run["artifact_id"],
                    "input_ordinal": ordinal, "input_role": "GALAXY_UPDATE",
                },
                "run": {**run, "run_state": "SUCCEEDED"}, "artifact": artifact, "source": source,
            })
        metadata["generation_inputs"] = inputs
        return metadata

    def _export(self, system_ids: Sequence[int]) -> dict[str, Any]:
        """Our rows for these systems, without rows that are marked retired."""
        exported = self.snapshot.export_chunk(self.connection, system_ids)
        bodies = [row for row in exported["bodies"] if row.get("lifecycle_state") != "RETIRED"]
        live = {row["body_pk"] for row in bodies}
        extras = []
        for extra in exported["extras"]:
            if extra.get("relation") in ("rings", "body_signal_current"):
                extra = {**extra, "rows": [
                    row for row in extra["rows"]
                    if row["body_pk"] in live and row.get("lifecycle_state") != "RETIRED"]}
            extras.append(extra)
        return {**exported, "bodies": bodies, "extras": extras}

    def score(self, records: Sequence[Mapping[str, Any]]) -> tuple[Scores, dict[int, str]]:
        """Scores for ``records``, and for each system that could not be scored, why."""
        scores, failed = Scores(), {}
        try:
            self._score_into(scores, records)
            return scores, failed
        except (ValueError, KeyError, TypeError):
            pass
        # One system in the batch cannot be scored: find it, score the rest.
        scores = Scores()
        for record in records:
            try:
                self._score_into(scores, [record])
            except (ValueError, KeyError, TypeError) as error:
                failed[int(record["id64"])] = galaxy._reason(error)
        return scores, failed

    def _score_into(self, scores: Scores, records: Sequence[Mapping[str, Any]]) -> None:
        if not records:
            return
        exported = self._export([int(record["id64"]) for record in records])
        result, _ = build.encode_chunk(self.generation_id, 0, exported, self.metadata, records)
        groups: dict[int, dict[int, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        for row in result["opportunities"]:
            scores.opportunities[row[1]].append(tuple(row))
            entry = groups[row[1]][row[3]]
            entry[0] += 1
            entry[1] += row[6]
        for row in result["bodies"]:
            scores.bodies[row[1]].append(tuple(row))
        for row in result["vectors"]:
            system_id = row[1]
            scores.vectors[system_id] = tuple(row)
            # The archetype model's own inputs: the six score lists, and per
            # economy how many opportunities there are and their summed score.
            scored = model.score_raw_system(
                model.SystemVectorRow.from_row((system_id, *row[6:12])),
                tuple((economy, count, total)
                      for economy, (count, total) in sorted(groups[system_id].items())))
            scores.archetypes[system_id] = (
                self.generation_id, system_id,
                *(scored[column] for column in archetype_build.STORED_VALUE_COLUMNS))

    def _read(self, table: str, columns: Sequence[str], system_ids: Sequence[int]) -> list[tuple]:
        query = sql.SQL(
            "SELECT {} FROM v3_derived.{} WHERE derived_generation_id = %s "
            "AND system_id64 = ANY(%s::bigint[])"
        ).format(sql.SQL(", ").join(map(sql.Identifier, columns)), sql.Identifier(table))
        return self.connection.execute(query, (self.generation_id, list(system_ids))).fetchall()

    def stored(self, system_ids: Sequence[int]) -> Scores:
        """The score rows we hold for these systems."""
        scores = Scores()
        for row in self._read("system_rating_vector", build.VECTOR_COLUMNS, system_ids):
            scores.vectors[row[1]] = row
        for row in self._read("body_mechanics", build.BODY_COLUMNS, system_ids):
            scores.bodies[row[1]].append(row)
        for row in self._read("economy_opportunity", build.OPPORTUNITY_COLUMNS, system_ids):
            scores.opportunities[row[1]].append(row)
        for row in self._read("system_archetype", ARCHETYPE_COLUMNS, system_ids):
            scores.archetypes[row[1]] = row
        return scores

    def find_rows(self, system_ids: Sequence[int]) -> dict[int, tuple]:
        """Find rows worked out now from the stored galaxy and score rows."""
        rows = self.connection.execute(
            self._find_rows, {"generation_id": self.generation_id, "system_ids": list(system_ids)})
        return {row[1]: tuple(row) for row in rows}

    def stored_find_rows(self, system_ids: Sequence[int]) -> dict[int, tuple]:
        return {row[1]: tuple(row) for row in self._read("system_search", FIND_COLUMNS, system_ids)}

    # -- writing ------------------------------------------------------------

    def new_chunk(self) -> int:
        """The build chunk a system that has never been scored is filed under.

        Every score row names a chunk of the original build, and that list of
        chunks is closed. New systems all go under the last one.
        """
        if self._new_chunk is None:
            row = self.connection.execute(
                "SELECT max(chunk_ordinal) FROM v3_derived.build_chunk WHERE derived_generation_id = %s",
                (self.generation_id,)).fetchone()
            if row is None or row[0] is None:
                raise galaxy.Refused("the published scores have no build chunks to file new systems under")
            self._new_chunk = int(row[0])
        return self._new_chunk

    def _replace(self, cursor: psycopg.Cursor[Any], table: str, columns: Sequence[str],
                 system_ids: Sequence[int], rows: Sequence[tuple]) -> None:
        if not system_ids:
            return
        cursor.execute(
            sql.SQL("DELETE FROM v3_derived.{} WHERE derived_generation_id = %s "
                    "AND system_id64 = ANY(%s::bigint[])").format(sql.Identifier(table)),
            (self.generation_id, list(system_ids)))
        if rows:
            with cursor.copy(sql.SQL("COPY v3_derived.{} ({}) FROM STDIN").format(
                    sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns)))) as copy:
                for row in rows:
                    copy.write_row(row)

    def write(self, scores: Scores, replace: Mapping[str, Sequence[int]],
              find_rows: Sequence[int], done: Sequence[int]) -> None:
        """One transaction: replace the named systems' rows table by table,
        rebuild Find rows, and take ``done`` off the waiting list."""
        stored_chunks = dict(self.connection.execute(
            "SELECT system_id64, chunk_ordinal FROM v3_derived.system_rating_vector "
            "WHERE derived_generation_id = %s AND system_id64 = ANY(%s::bigint[])",
            (self.generation_id, list(replace.get("system_rating_vector", ())))).fetchall())
        now = datetime.now(timezone.utc)
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute("SET LOCAL edfinder.nightly_update = 'on'")
            # The links between these tables are checked when the transaction
            # ends, so the order of the replacements does not matter.
            ids = list(replace.get("economy_opportunity", ()))
            self._replace(cursor, "economy_opportunity", build.OPPORTUNITY_COLUMNS, ids,
                          [row for system_id in ids for row in scores.opportunities.get(system_id, ())])
            ids = list(replace.get("body_mechanics", ()))
            self._replace(cursor, "body_mechanics", build.BODY_COLUMNS, ids,
                          [row for system_id in ids for row in scores.bodies.get(system_id, ())])
            ids = list(replace.get("system_rating_vector", ()))
            self._replace(cursor, "system_rating_vector", build.VECTOR_COLUMNS, ids, [
                (*scores.vectors[system_id][:2],
                 stored_chunks[system_id] if system_id in stored_chunks else self.new_chunk(),
                 *scores.vectors[system_id][3:])
                for system_id in ids])
            ids = list(replace.get("system_archetype", ()))
            self._replace(cursor, "system_archetype", archetype_build.ALL_STORED_COLUMNS, ids, [
                (*scores.archetypes[system_id][:2], archetype_build.PRODUCT_VERSION,
                 *scores.archetypes[system_id][2:], now)
                for system_id in ids])
            if find_rows:
                # Rebuilt last: the Find row reads the score rows just written.
                cursor.execute(
                    "DELETE FROM v3_derived.system_search WHERE derived_generation_id = %s "
                    "AND system_id64 = ANY(%s::bigint[])", (self.generation_id, list(find_rows)))
                cursor.execute(
                    sql.SQL("INSERT INTO v3_derived.system_search ({}) ").format(
                        sql.SQL(", ").join(map(sql.Identifier, FIND_COLUMNS))) + self._find_rows,
                    {"generation_id": self.generation_id, "system_ids": list(find_rows)})
            if done:
                cursor.execute(
                    sql.SQL("DELETE FROM {} WHERE system_id64 = ANY(%s::bigint[])").format(
                        sql.SQL(galaxy.QUEUE_TABLE)), (list(done),))


# -- the check --------------------------------------------------------------


@dataclass
class ScoreReport:
    file_name: str = ""
    systems_read: int = 0
    not_usable: int = 0
    not_held: int = 0
    out_of_date: int = 0
    # Systems whose scores should come out exactly as stored.
    settled: int = 0
    settled_same: int = 0
    settled_restamped: int = 0
    settled_different: dict[int, list[str]] = field(default_factory=dict)
    examples: dict[int, list[str]] = field(default_factory=dict)
    differing_columns: Counter = field(default_factory=Counter)
    no_stored_scores: int = 0
    find_same: int = 0
    find_different: dict[int, list[str]] = field(default_factory=dict)
    find_columns: Counter = field(default_factory=Counter)
    # Systems the galaxy update rewrote, waiting for new scores.
    waiting: int = 0
    waiting_scored: int = 0
    waiting_would_change: int = 0
    cannot_score: dict[int, str] = field(default_factory=dict)
    cannot_reasons: Counter = field(default_factory=Counter)
    seconds: float = 0.0
    stopped_early: bool = False

    @property
    def agrees(self) -> bool:
        return not self.settled_different and not self.find_different

    @property
    def verdict(self) -> str:
        if not self.agrees:
            return "The stored scores were NOT all reproduced: see above."
        if not self.settled_same:
            return ("This file had no system whose scores should be unchanged, "
                    "so the stored scores could not be compared.")
        return "The stored scores were reproduced exactly."

    def lines(self) -> list[str]:
        def row(label: str, number: int, note: str = "") -> str:
            return f"{label:<48}{number:>10,}" + (f"   {note}" if note else "")

        out = [row("Systems in the file:", self.systems_read,
                   "(stopped at the limit)" if self.stopped_early else "")]
        if self.not_usable:
            out.append(row("  not usable:", self.not_usable))
        if self.not_held:
            out.append(row("  not in our galaxy yet:", self.not_held))
        out.append(row("  our rows differ from the file:", self.out_of_date,
                       "(cannot be scored from it)"))
        out.append(row("Systems whose scores should be unchanged:", self.settled))
        out.append(row("  scores worked out again, identical:", self.settled_same))
        if self.settled_restamped:
            out.append(row("    of those, only the record's date moved:", self.settled_restamped))
        out.append(row("  scores worked out again, DIFFERENT:", len(self.settled_different)))
        for name, count in self.differing_columns.most_common(12):
            out.append(f"      {count:,} systems differ in {name}")
        for system_id, examples in list(self.examples.items())[:5]:
            out.append(f"      for example system {system_id} (stored -> now):")
            out += [f"          {example}" for example in examples]
        if self.no_stored_scores:
            out.append(row("  have no stored scores at all:", self.no_stored_scores))
        out.append(row("  Find row worked out again, identical:", self.find_same))
        out.append(row("  Find row worked out again, DIFFERENT:", len(self.find_different)))
        for name, count in self.find_columns.most_common(12):
            out.append(f"      {count:,} systems differ in {name}")
        for system_id, names in list(self.find_different.items())[:5]:
            out.append(f"      for example system {system_id}: {', '.join(names[:6])}")
        out.append(row("Systems waiting for new scores, in this file:", self.waiting))
        out.append(row("  can be scored:", self.waiting_scored))
        out.append(row("    of those, scores would change:", self.waiting_would_change))
        out.append(row("  cannot be scored:", len(self.cannot_score)))
        for reason, count in self.cannot_reasons.most_common(8):
            out.append(f"      {count:,} x {reason}")
        for system_id, reason in list(self.cannot_score.items())[:5]:
            out.append(f"      for example system {system_id}: {reason}")
        minutes, seconds = divmod(int(self.seconds), 60)
        out.append(f"Took {minutes} min {seconds} s.")
        return out


def _queued(connection: psycopg.Connection[Any], system_ids: Sequence[int]) -> dict[int, list[str]]:
    if connection.execute("SELECT to_regclass(%s)", (galaxy.QUEUE_TABLE,)).fetchone()[0] is None:
        return {}
    return dict(connection.execute(
        sql.SQL("SELECT system_id64, changed_tables FROM {} WHERE system_id64 = ANY(%s::bigint[])").format(
            sql.SQL(galaxy.QUEUE_TABLE)), (list(system_ids),)).fetchall())


def _matching(connection: psycopg.Connection[Any], target: galaxy.Target,
              records: Sequence[Mapping[str, Any]], report: Any,
              ) -> tuple[list[Mapping[str, Any]], dict[int, list[str]]]:
    """The records whose scoring inputs are exactly what we hold, and which of
    this batch's systems are on the waiting list (with what changed)."""
    systems: list[tuple[NormalizedSystem, Mapping[str, Any]]] = []
    for record in records:
        report.systems_read += 1
        try:
            systems.append((normalize_system(record), record))
        except (ValueError, TypeError, KeyError):
            report.not_usable += 1
    held = galaxy.read_stored(connection, target.schema, [system.id64 for system, _ in systems])
    queue = _queued(connection, [system.id64 for system, _ in systems])
    matching: list[Mapping[str, Any]] = []
    for system, record in systems:
        ours = held.get(system.id64)
        if ours is None:
            report.not_held += 1
        # Only the tables the scores are worked out from have to match.
        elif classify_material_change(system, ours.tables, policy="strict", include_carriers=True,
                                      tables=SCORING_TABLES).status == "unchanged":
            matching.append(record)
        else:
            report.out_of_date += 1
    return matching, queue


def check_batch(connection: psycopg.Connection[Any], target: galaxy.Target, scorer: Scorer,
                records: Sequence[Mapping[str, Any]], report: ScoreReport) -> None:
    matching, queue = _matching(connection, target, records, report)
    settled: list[Mapping[str, Any]] = []
    waiting: list[Mapping[str, Any]] = []
    untouched: set[int] = set()
    for record in matching:
        system_id = int(record["id64"])
        if set(queue.get(system_id, ())) & set(SCORING_TABLES):
            waiting.append(record)
        else:
            settled.append(record)
            if system_id not in queue:
                untouched.add(system_id)

    # 1. Scores that should come out exactly as stored.
    report.settled += len(settled)
    scores, failed = scorer.score(settled)
    ids = [int(record["id64"]) for record in settled]
    stored = scorer.stored(ids)
    find_now, find_stored = scorer.find_rows(ids), scorer.stored_find_rows(ids)
    for system_id in ids:
        if system_id in failed:
            report.cannot_score[system_id] = failed[system_id]
            report.cannot_reasons[failed[system_id]] += 1
            continue
        if system_id not in stored.vectors:
            report.no_stored_scores += 1
            continue
        names: list[str] = []
        examples: list[str] = []
        restamped = False
        mine, theirs = scores.tables(system_id), stored.tables(system_id)
        for table, (columns, rows) in mine.items():
            found = differences(columns, rows, theirs[table][1])
            restamped = restamped or bool(found & RECORD_STAMPS)
            names += [f"{table}.{name}" for name in sorted(found - RECORD_STAMPS)]
            if found - RECORD_STAMPS and len(examples) < 4:
                examples += describe(table, columns, rows, theirs[table][1])
        if names:
            report.settled_different[system_id] = names
            if len(report.examples) < 5:
                report.examples[system_id] = examples[:4]
            report.differing_columns.update(names)
        else:
            report.settled_same += 1
            report.settled_restamped += restamped
        if system_id not in untouched:
            # The galaxy update rewrote this system: its Find row is owed a
            # new date and station count, so it is not expected to match.
            continue
        find_names = sorted(differences(
            FIND_COLUMNS,
            [find_now[system_id]] if system_id in find_now else [],
            [find_stored[system_id]] if system_id in find_stored else []))
        if find_names:
            report.find_different[system_id] = find_names
            report.find_columns.update(find_names)
        else:
            report.find_same += 1

    # 2. Systems waiting for new scores: can each be scored?
    report.waiting += len(waiting)
    scores, failed = scorer.score(waiting)
    ids = [int(record["id64"]) for record in waiting]
    stored = scorer.stored(ids)
    for system_id in ids:
        if system_id in failed:
            report.cannot_score[system_id] = failed[system_id]
            report.cannot_reasons[failed[system_id]] += 1
            continue
        report.waiting_scored += 1
        mine, theirs = scores.tables(system_id), stored.tables(system_id)
        if any(differences(columns, rows, theirs[table][1]) - RECORD_STAMPS
               for table, (columns, rows) in mine.items()):
            report.waiting_would_change += 1


# -- writing the scores that are owed ----------------------------------------


@dataclass
class ApplyReport:
    file_name: str = ""
    systems_read: int = 0
    not_usable: int = 0
    not_held: int = 0
    out_of_date: int = 0
    left_alone: int = 0
    added: int = 0
    changed: int = 0
    same: int = 0
    corrected: int = 0
    cannot_score: dict[int, str] = field(default_factory=dict)
    cannot_reasons: Counter = field(default_factory=Counter)
    find_rows: int = 0
    find_only: int = 0
    still_waiting: int = 0
    seconds: float = 0.0
    stopped_early: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.file_name, "systems_read": self.systems_read, "out_of_date": self.out_of_date,
            "added": self.added, "changed": self.changed, "same": self.same,
            "corrected": self.corrected, "cannot_score": len(self.cannot_score),
            "find_rows": self.find_rows + self.find_only, "still_waiting": self.still_waiting,
            "seconds": round(self.seconds, 1), "stopped_early": self.stopped_early,
        }

    def lines(self) -> list[str]:
        def row(label: str, number: int, note: str = "") -> str:
            return f"{label:<48}{number:>10,}" + (f"   {note}" if note else "")

        out = [row("Systems in the file:", self.systems_read,
                   "(stopped at the limit)" if self.stopped_early else "")]
        if self.not_usable:
            out.append(row("  not usable:", self.not_usable))
        if self.not_held:
            out.append(row("  not in our galaxy yet:", self.not_held))
        if self.out_of_date:
            out.append(row("  our rows differ from the file:", self.out_of_date, "(not scored)"))
        out.append(row("  not waiting and scores unchanged:", self.left_alone, "(left alone)"))
        out.append(row("Systems given their scores:", self.added + self.changed + self.same))
        out.append(row("  never scored before:", self.added))
        out.append(row("  scores changed:", self.changed))
        out.append(row("  scores the same as before:", self.same))
        if self.corrected:
            out.append(row("  of all those, were not on the waiting list:", self.corrected,
                           "(Spansh changed a sub-type)"))
        out.append(row("  could not be scored:", len(self.cannot_score), "(still waiting)"))
        for reason, count in self.cannot_reasons.most_common(8):
            out.append(f"      {count:,} x {reason}")
        for system_id, reason in list(self.cannot_score.items())[:5]:
            out.append(f"      for example system {system_id}: {reason}")
        out.append(row("Find rows rebuilt:", self.find_rows + self.find_only))
        if self.find_only:
            out.append(row("  for systems that only gained or lost a station:", self.find_only))
        out.append(row("Systems still waiting for new scores:", self.still_waiting))
        minutes, seconds = divmod(int(self.seconds), 60)
        out.append(f"Took {minutes} min {seconds} s.")
        return out


def apply_batch(connection: psycopg.Connection[Any], target: galaxy.Target, scorer: Scorer,
                records: Sequence[Mapping[str, Any]], report: ApplyReport) -> None:
    matching, queue = _matching(connection, target, records, report)
    scores, failed = scorer.score(matching)
    for system_id, reason in failed.items():
        report.cannot_score[system_id] = reason
        report.cannot_reasons[reason] += 1
    ids = [int(record["id64"]) for record in matching if int(record["id64"]) not in failed]
    stored = scorer.stored(ids)
    replace: dict[str, list[int]] = defaultdict(list)
    find_rows: list[int] = []
    done: list[int] = []
    for system_id in ids:
        mine, theirs = scores.tables(system_id), stored.tables(system_id)
        found = {table: differences(columns, rows, theirs[table][1])
                 for table, (columns, rows) in mine.items()}
        never_scored = system_id not in stored.vectors
        differs = any(names - RECORD_STAMPS for names in found.values())
        if system_id not in queue and not differs and not never_scored:
            report.left_alone += 1
            continue
        for table, names in found.items():
            if names:
                replace[table].append(system_id)
        find_rows.append(system_id)
        if system_id in queue:
            done.append(system_id)
        else:
            report.corrected += 1
        if never_scored:
            report.added += 1
        elif differs:
            report.changed += 1
        else:
            report.same += 1
    if find_rows:
        scorer.write(scores, replace, find_rows, done)
        report.find_rows += len(find_rows)


def find_only_pass(connection: psycopg.Connection[Any], scorer: Scorer, report: ApplyReport) -> None:
    """Systems on the waiting list for a reason that does not touch their scores
    (a fleet carrier came or went): rebuild the Find row, which needs no record."""
    last = -1
    while True:
        ids = [row[0] for row in connection.execute(
            sql.SQL("SELECT system_id64 FROM {} WHERE NOT (changed_tables && %s::text[]) "
                    "AND system_id64 > %s ORDER BY system_id64 LIMIT 500").format(sql.SQL(galaxy.QUEUE_TABLE)),
            (list(SCORING_TABLES), last))]
        if not ids:
            return
        last = ids[-1]
        scored = [row[0] for row in scorer._read("system_rating_vector", ("system_id64",), ids)]
        if scored:
            scorer.write(Scores(), {}, scored, scored)
            report.find_only += len(scored)


def apply_file(connection: psycopg.Connection[Any], path: Path, *, limit: int | None = None,
               progress: bool = True) -> ApplyReport:
    """Write the scores owed to the systems of ``path``. ``connection`` must be autocommit."""
    if not connection.autocommit:
        raise ValueError("the connection must be in autocommit mode")
    report = ApplyReport(path.name)
    started = time.monotonic()
    connection.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(galaxy.STATEMENT_TIMEOUT)))
    connection.execute("SET lock_timeout = '60s'")
    target = galaxy.preflight(connection, need_queue=True)
    if not connection.execute("SELECT pg_try_advisory_lock(%s)", (galaxy.LOCK_KEY,)).fetchone()[0]:
        raise galaxy.UpdateError("a galaxy update or another scoring run is going on; this one did not start")
    try:
        scorer = Scorer(connection)
        reading = galaxy.Report("apply", path.name)
        last = started
        for records in galaxy._batches(path, reading, limit):
            apply_batch(connection, target, scorer, records, report)
            if progress and time.monotonic() - last >= galaxy.PROGRESS_SECONDS:
                last = time.monotonic()
                galaxy.say(f"  ... {report.systems_read:,} systems read, "
                           f"{report.added + report.changed + report.same:,} given scores, "
                           f"{int(last - started) // 60} min")
        report.stopped_early = reading.stopped_early
        find_only_pass(connection, scorer, report)
        report.still_waiting = connection.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.SQL(galaxy.QUEUE_TABLE))).fetchone()[0]
    finally:
        try:
            connection.execute("SELECT pg_advisory_unlock(%s)", (galaxy.LOCK_KEY,))
        except psycopg.Error:
            pass
    report.seconds = time.monotonic() - started
    return report


def check_file(connection: psycopg.Connection[Any], path: Path, *, limit: int | None = None,
               progress: bool = True) -> ScoreReport:
    """Read only. ``connection`` must be autocommit."""
    if not connection.autocommit:
        raise ValueError("the connection must be in autocommit mode")
    report = ScoreReport(path.name)
    started = time.monotonic()
    connection.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(galaxy.STATEMENT_TIMEOUT)))
    target = galaxy.preflight(connection, need_queue=False)
    scorer = Scorer(connection)
    reading = galaxy.Report("check", path.name)
    last = started
    for records in galaxy._batches(path, reading, limit):
        check_batch(connection, target, scorer, records, report)
        if progress and time.monotonic() - last >= galaxy.PROGRESS_SECONDS:
            last = time.monotonic()
            galaxy.say(f"  ... {report.systems_read:,} systems read, "
                       f"{report.settled + report.waiting:,} scored, {int(last - started) // 60} min")
    report.stopped_early = reading.stopped_early
    report.seconds = time.monotonic() - started
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m spansh_delta.scores",
        description="Work out scores from a Spansh galaxy file. check is read only; "
                    "apply writes the scores that are owed.")
    parser.add_argument("mode", choices=("check", "apply"),
                        help="check: read only. apply: write the scores that are owed.")
    parser.add_argument("file", type=Path, help="a Spansh galaxy*.json.gz file")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many systems")
    args = parser.parse_args(argv)
    if not args.file.is_file():
        galaxy.say(f"FAILED: there is no file {args.file}")
        return 1
    connection = galaxy.connect()
    if connection is None:
        return galaxy.EXIT_NO_CONNECTION
    with connection:
        galaxy.say(f"Score check (read only): {args.file.name}" if args.mode == "check"
                   else f"Scoring: {args.file.name}")
        try:
            if args.mode == "apply":
                written = apply_file(connection, args.file, limit=args.limit)
                for line in written.lines():
                    galaxy.say(line)
                if os.environ.get("EDFINDER_GALAXY_JSON") == "1":
                    galaxy.say("JSON " + json.dumps(written.as_dict(), sort_keys=True))
                galaxy.say("DONE")
                return 0
            report = check_file(connection, args.file, limit=args.limit)
        except galaxy.Refused as error:
            galaxy.say(f"REFUSED: {error}")
            return 2
        except (galaxy.UpdateError, ValueError, OSError, psycopg.Error) as error:
            galaxy.say(f"FAILED: {galaxy._reason(error) if isinstance(error, psycopg.Error) else error}")
            return 1
    for line in report.lines():
        galaxy.say(line)
    if os.environ.get("EDFINDER_GALAXY_JSON") == "1":
        galaxy.say("JSON " + json.dumps({
            "settled": report.settled, "identical": report.settled_same,
            "different": len(report.settled_different), "waiting": report.waiting,
            "cannot_score": len(report.cannot_score)}, sort_keys=True))
    galaxy.say("CHECKED: nothing was changed. " + report.verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
