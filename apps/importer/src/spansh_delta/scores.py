"""Give systems their scores and Find rows, and check them against what is stored.

    python -m spansh_delta.scores apply          score the systems on the waiting list
    python -m spansh_delta.scores check FILE     read only

A system's scores come from its rows in the galaxy tables. One of the things
they use is each body's class ("Earth-like world", "Icy body"), which the
galaxy tables hold since migration 027 (``bodies.body_class``): the galaxy
update writes it with every row it writes. So a system the update has changed
can be scored from the database alone, at any time, with no Spansh file at
hand. The scoring itself is the frozen code (``scripts/ratings_v4``,
``scripts/v3_system_*`` and ``shared_contracts``: copied unchanged, and
called, not rewritten). It takes five things about each body from a "record";
here that record is made from our own rows.

``apply`` works through the waiting list (``v3_state.rescore_queue``), which
the galaxy update fills:

  * A system waiting for new scores gets them: each of its score tables is
    replaced where it differs, its Find row is rebuilt, and it leaves the
    waiting list. All of that is one transaction.
  * A system that only gained or lost a station has its Find row rebuilt.
  * A system that cannot be scored stays on the waiting list, and the report
    says why. One reason is a body whose class is not in the database: its
    row was written by a galaxy update from before migration 027, and the
    system has not been scored since. Its old score row is not used for the
    class, because the body may be what changed. The next galaxy update that
    lists the system writes the class.

Where the database keeps reach (migration 028, the Raspberry Pi), only systems
within 500 ly of an inhabited system are scored (``spansh_delta.reach``):

  * Reach is brought up to date first. The scoring refuses to start if it has
    never been worked out.
  * Systems further away are taken off the waiting list unscored.
  * After the waiting list, every system in reach that has no scores yet gets
    them: systems that came within reach, mostly.

``check FILE`` writes nothing. For the systems of a Spansh file whose records
say what our rows say, it answers:

  * Systems whose scoring inputs nothing has changed since they were scored:
    do we get exactly the stored scores and Find row back?
  * Do scores worked out from the database alone come out the same as scores
    worked out from Spansh's records? (A body row from before migration 027
    has no class of its own. For a system that is not waiting for new scores
    the class is taken from the body's score row, which holds the same thing.)
  * Systems that wait for new scores: can each be scored, and if not, why not?

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
from . import reach
from .normalize import NormalizedSystem, classify_material_change, normalize_system

SCORING_TABLES = galaxy.SCORING_TABLES

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
# Columns that describe the record a score was made from (a fingerprint of it,
# and when a body's sub-type was seen). They move when Spansh re-stamps a
# body, and they differ between a record of Spansh's and the same facts read
# from our own rows, without any score changing.
RECORD_STAMPS = {"subtype_projection_sha256", "subtype_observed_at"}
UNKNOWN_CLASS = "the class of one of its bodies is not in the database"
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


def _narrow(exported: Mapping[str, Any], keep: set[int]) -> Mapping[str, Any]:
    """``exported`` cut down to the systems in ``keep``."""
    if {row["id64"] for row in exported["systems"]} == keep:
        return exported
    bodies = [row for row in exported["bodies"] if row["system_id64"] in keep]
    live = {row["body_pk"] for row in bodies}
    extras = [
        {**extra, "rows": [row for row in extra["rows"] if row["body_pk"] in live]}
        if extra.get("relation") in ("rings", "body_signal_current") else extra
        for extra in exported["extras"]]
    return {**exported, "systems": [row for row in exported["systems"] if row["id64"] in keep],
            "bodies": bodies, "extras": extras}


class Scorer:
    """Runs the frozen scoring code, from Spansh records or from our own rows."""

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
        # Body type id -> the plain type, as the scoring code spells it.
        self._body_types = {
            row["body_type_id"]: row["public_code"]
            for vocabulary in self.snapshot.vocabularies if vocabulary["relation"] == "body_type"
            for row in vocabulary["rows"]}
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

    def export(self, system_ids: Sequence[int]) -> tuple[dict[str, Any], dict[int, str]]:
        """Our rows for ``system_ids``, and why not for any we could not read."""
        nothing: dict[str, Any] = {"systems": [], "bodies": [], "extras": []}
        if not system_ids:
            return nothing, {}
        try:
            return self._export(system_ids), {}
        except ValueError:
            pass
        # One of them is not in the galaxy. Find it; read the rest.
        missing: dict[int, str] = {}
        for system_id in system_ids:
            try:
                self._export([system_id])
            except ValueError as error:
                missing[system_id] = galaxy._reason(error)
        found = [system_id for system_id in system_ids if system_id not in missing]
        return (self._export(found) if found else nothing), missing

    @staticmethod
    def classes(stored: Scores, system_ids: Sequence[int]) -> dict[int, str]:
        """Each body's class as its score row has it, for these systems."""
        index = build.BODY_COLUMNS.index("body_class")
        return {row[2]: row[index] for system_id in system_ids
                for row in stored.bodies.get(system_id, ())}

    def records(self, exported: Mapping[str, Any], scored: Mapping[int, str],
                ) -> tuple[list[dict[str, Any]], dict[int, str]]:
        """What the scoring code takes from a Spansh record, made from our own rows.

        For each body that is its Spansh id, its number in the system, its
        plain type, its sub-type and when it was last seen: five things, all
        in ``exported``. A body row from before migration 027 has no class of
        its own; ``scored`` gives the class in its score row, for the systems
        whose score rows can be relied on (none that is waiting for new
        scores: see ``spansh_delta.apply.scored_classes``). Also returns the
        systems this cannot be done for, with the reason: a body whose class
        we do not know.
        """
        bodies: dict[int, list[dict[str, Any]]] = defaultdict(list)
        unknown: dict[int, str] = {}
        for row in exported["bodies"]:
            system_id = row["system_id64"]
            body_class = galaxy.class_of(row, scored)
            if body_class is None:
                unknown[system_id] = UNKNOWN_CLASS
                continue
            plain = self._body_types.get(row.get("body_type_id"))
            body: dict[str, Any] = {"id64": row.get("source_body_id64"), "bodyId": row.get("frontier_body_id")}
            if plain is not None:
                body["type"] = plain
            # The class is the sub-type where Spansh gave one, else the plain
            # type (or "Unknown"): see v3_spansh.adapter.body_class.
            if body_class != (plain or "Unknown"):
                body["subType"] = body_class
            if row.get("source_updated_at") is not None:
                body["updateTime"] = row["source_updated_at"]
            bodies[system_id].append(body)
        return ([{"id64": row["id64"], "bodies": bodies.get(row["id64"], [])}
                 for row in exported["systems"] if row["id64"] not in unknown], unknown)

    def score(self, records: Sequence[Mapping[str, Any]],
              exported: Mapping[str, Any] | None = None) -> tuple[Scores, dict[int, str]]:
        """Scores for ``records``, and for each system that could not be scored, why.

        ``exported`` is our rows for those systems if they have been read
        already; otherwise they are read here.
        """
        scores, failed = Scores(), {}
        if not records:
            return scores, failed
        try:
            if exported is None:
                exported = self._export([int(record["id64"]) for record in records])
            self._score_into(scores, records, exported)
            return scores, failed
        except (ValueError, KeyError, TypeError):
            pass
        # One system in the batch cannot be scored: find it, score the rest.
        scores = Scores()
        for record in records:
            try:
                rows = exported if exported is not None else self._export([int(record["id64"])])
                self._score_into(scores, [record], rows)
            except (ValueError, KeyError, TypeError) as error:
                failed[int(record["id64"])] = galaxy._reason(error)
        return scores, failed

    def _score_into(self, scores: Scores, records: Sequence[Mapping[str, Any]],
                    exported: Mapping[str, Any]) -> None:
        exported = _narrow(exported, {int(record["id64"]) for record in records})
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
    # The same systems scored from our own rows instead of Spansh's records.
    database_same: int = 0
    database_different: dict[int, list[str]] = field(default_factory=dict)
    database_examples: dict[int, list[str]] = field(default_factory=dict)
    database_columns: Counter = field(default_factory=Counter)
    database_unknown: int = 0
    database_failed: Counter = field(default_factory=Counter)
    seconds: float = 0.0
    stopped_early: bool = False

    @property
    def agrees(self) -> bool:
        return not self.settled_different and not self.find_different and not self.database_different

    @property
    def verdict(self) -> str:
        if self.settled_different or self.find_different:
            return "The stored scores were NOT all reproduced: see above."
        if self.database_different:
            return ("Scoring from the database did NOT always give what Spansh's records give: "
                    "see above.")
        if not self.settled_same:
            return ("This file had no system whose scores should be unchanged, "
                    "so the stored scores could not be compared.")
        if not self.database_same:
            return ("The stored scores were reproduced exactly. No system could be scored from "
                    "the database alone, so that was not compared.")
        return ("The stored scores were reproduced exactly, and scoring from the database "
                "alone gives the same.")

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
            # The stamp says which record a score was made from (a fingerprint
            # of it, and the date of a body's class). It differs when Spansh
            # has re-dated a body, and whenever the stored scores were made
            # from our own rows instead of from Spansh's record.
            out.append(row("    of those, only the record's stamp differs:", self.settled_restamped))
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
        compared = self.database_same + len(self.database_different)
        out.append(row("Same systems, scored from the database alone:", compared))
        out.append(row("  identical to scoring from Spansh's records:", self.database_same))
        out.append(row("  DIFFERENT:", len(self.database_different)))
        for name, count in self.database_columns.most_common(12):
            out.append(f"      {count:,} systems differ in {name}")
        for system_id, examples in list(self.database_examples.items())[:5]:
            out.append(f"      for example system {system_id} (from the record -> from the database):")
            out += [f"          {example}" for example in examples]
        if self.database_unknown:
            out.append(row("  not compared, a class is not in the database:", self.database_unknown))
        for reason, count in self.database_failed.most_common(4):
            out.append(f"  not compared, {count:,} x {reason}")
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
    held = galaxy.read_stored(connection, target, [system.id64 for system, _ in systems])
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
    ids = [int(record["id64"]) for record in matching]
    scores, failed = scorer.score(matching)
    stored = scorer.stored(ids)
    settled: list[int] = []
    waiting: list[int] = []
    untouched: set[int] = set()
    for system_id in ids:
        if set(queue.get(system_id, ())) & set(SCORING_TABLES):
            waiting.append(system_id)
        else:
            settled.append(system_id)
            if system_id not in queue:
                untouched.add(system_id)

    # 1. Scores that should come out exactly as stored.
    report.settled += len(settled)
    find_now, find_stored = scorer.find_rows(settled), scorer.stored_find_rows(settled)
    for system_id in settled:
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
    for system_id in waiting:
        if system_id in failed:
            report.cannot_score[system_id] = failed[system_id]
            report.cannot_reasons[failed[system_id]] += 1
            continue
        report.waiting_scored += 1
        mine, theirs = scores.tables(system_id), stored.tables(system_id)
        if any(differences(columns, rows, theirs[table][1]) - RECORD_STAMPS
               for table, (columns, rows) in mine.items()):
            report.waiting_would_change += 1

    # 3. The same systems scored from our own rows, with no record: the result
    #    must be the one the record gives.
    usable = [system_id for system_id in ids if system_id not in failed]
    exported, missing = scorer.export(usable)
    own_records, unknown = scorer.records(exported, scorer.classes(stored, settled))
    own, own_failed = scorer.score(own_records, exported)
    for system_id in usable:
        if system_id in unknown:
            report.database_unknown += 1
            continue
        if system_id in missing or system_id in own_failed:
            report.database_failed[missing.get(system_id) or own_failed[system_id]] += 1
            continue
        names, examples = [], []
        mine, theirs = own.tables(system_id), scores.tables(system_id)
        for table, (columns, rows) in mine.items():
            found = differences(columns, rows, theirs[table][1]) - RECORD_STAMPS
            names += [f"{table}.{name}" for name in sorted(found)]
            if found and len(examples) < 4:
                examples += describe(table, columns, rows, theirs[table][1])
        if names:
            report.database_different[system_id] = names
            if len(report.database_examples) < 5:
                report.database_examples[system_id] = examples[:4]
            report.database_columns.update(names)
        else:
            report.database_same += 1


# -- scoring the waiting list -------------------------------------------------


@dataclass
class ApplyReport:
    systems_read: int = 0
    added: int = 0
    changed: int = 0
    same: int = 0
    cannot_score: dict[int, str] = field(default_factory=dict)
    cannot_reasons: Counter = field(default_factory=Counter)
    find_rows: int = 0
    find_only: int = 0
    still_waiting: int = 0
    seconds: float = 0.0
    stopped_early: bool = False
    # Where the database keeps reach: how it was brought up to date, how many
    # waiting systems were further than 500 ly, and how many systems in reach
    # had no scores yet.
    reach_report: reach.ReachReport | None = None
    outside_reach: int = 0
    unscored_in_reach: int = 0

    def as_dict(self) -> dict[str, Any]:
        found = {
            "systems_read": self.systems_read, "added": self.added, "changed": self.changed,
            "same": self.same, "cannot_score": len(self.cannot_score),
            "find_rows": self.find_rows + self.find_only, "still_waiting": self.still_waiting,
            "seconds": round(self.seconds, 1), "stopped_early": self.stopped_early,
        }
        if self.reach_report is not None:
            found.update(reach=self.reach_report.as_dict(), outside_reach=self.outside_reach,
                         unscored_in_reach=self.unscored_in_reach)
        return found

    def lines(self) -> list[str]:
        def row(label: str, number: int, note: str = "") -> str:
            return f"{label:<48}{number:>10,}" + (f"   {note}" if note else "")

        out = list(self.reach_report.lines()) if self.reach_report is not None else []
        if self.reach_report is not None:
            out.append(row(f"Taken off the waiting list, further than {reach.REACH_LY} ly:",
                           self.outside_reach, "(not scored)"))
        out += [row("Systems taken from the waiting list:", self.systems_read,
                    "(stopped at the limit)" if self.stopped_early else "")]
        if self.reach_report is not None:
            out.append(row(f"Systems within {reach.REACH_LY} ly that had no scores:", self.unscored_in_reach))
        out += [row("Systems given their scores:", self.added + self.changed + self.same),
               row("  never scored before:", self.added),
               row("  scores changed:", self.changed),
               row("  scores the same as before:", self.same),
               row("  could not be scored:", len(self.cannot_score), "(still waiting)")]
        for reason, count in self.cannot_reasons.most_common(8):
            out.append(f"      {count:,} x {reason}")
        for system_id, reason in list(self.cannot_score.items())[:5]:
            out.append(f"      for example system {system_id}: {reason}")
        if UNKNOWN_CLASS in self.cannot_reasons:
            out.append("      (Its rows were last written before migration 027, when classes began to be "
                       "kept.\n       The next galaxy update that lists the system fills them in.)")
        out.append(row("Find rows rebuilt:", self.find_rows + self.find_only))
        if self.find_only:
            out.append(row("  of those, for a station coming or going only:", self.find_only))
        out.append(row("Systems still waiting for new scores:", self.still_waiting))
        minutes, seconds = divmod(int(self.seconds), 60)
        out.append(f"Took {minutes} min {seconds} s.")
        return out


def waiting_batch(scorer: Scorer, queue: Mapping[int, Sequence[str]], report: ApplyReport, *,
                  from_the_list: bool = True) -> None:
    """Deal with one batch of systems from the waiting list, from our own rows.

    ``queue`` gives, for each, the galaxy tables that changed. One transaction;
    if the database refuses a row, one transaction per system instead, so that
    only the system at fault stays on the waiting list. ``from_the_list`` is
    False for systems in reach that have no scores, which are not on it.
    """
    if from_the_list:
        report.systems_read += len(queue)
    scoring = [system_id for system_id, tables in queue.items() if set(tables) & set(SCORING_TABLES)]
    stored = scorer.stored(list(queue))
    exported, cannot = scorer.export(scoring)
    # Each body's class has to be in its own row. These systems' rows changed
    # after their score rows were made, so a score row cannot vouch for one.
    records, unknown = scorer.records(exported, {})
    scores, failed = scorer.score(records, exported)
    cannot = {**cannot, **unknown, **failed}

    replace: dict[str, list[int]] = defaultdict(list)
    # What happens to each system that is written: "find_only", "added",
    # "changed" or "same". Counted once its transaction has gone through.
    outcome: dict[int, str] = {}
    for system_id in queue:
        if system_id in cannot:
            continue
        if system_id not in scoring:
            # Only a station came or went. The scores stand; the Find row,
            # which counts stations, is rebuilt. A system with no scores at
            # all has no Find row to rebuild and stays where it is.
            if system_id in stored.vectors:
                outcome[system_id] = "find_only"
            continue
        mine, theirs = scores.tables(system_id), stored.tables(system_id)
        found = {table: differences(columns, rows, theirs[table][1])
                 for table, (columns, rows) in mine.items()}
        for table, names in found.items():
            if names:
                replace[table].append(system_id)
        if system_id not in stored.vectors:
            outcome[system_id] = "added"
        elif any(names - RECORD_STAMPS for names in found.values()):
            outcome[system_id] = "changed"
        else:
            outcome[system_id] = "same"

    written = list(outcome)
    if written:
        try:
            scorer.write(scores, replace, written, written)
        except galaxy.ROW_ERRORS:
            # The database refused a row. Go again one system at a time.
            written = []
            for system_id in outcome:
                try:
                    scorer.write(scores, {table: [system_id] for table, ids in replace.items()
                                          if system_id in ids}, [system_id], [system_id])
                    written.append(system_id)
                except galaxy.ROW_ERRORS as error:
                    cannot[system_id] = galaxy._database_reason(error)
    for system_id in written:
        if outcome[system_id] == "find_only":
            report.find_only += 1
        else:
            report.find_rows += 1
            setattr(report, outcome[system_id], getattr(report, outcome[system_id]) + 1)
    for system_id, reason in cannot.items():
        report.cannot_score[system_id] = reason
        report.cannot_reasons[reason] += 1


def apply_waiting(connection: psycopg.Connection[Any], *, limit: int | None = None,
                  progress: bool = True) -> ApplyReport:
    """Score the systems on the waiting list from the database alone.

    ``connection`` must be autocommit. Systems are taken in batches, one
    transaction each, so this can be stopped and started again at any point.
    """
    if not connection.autocommit:
        raise ValueError("the connection must be in autocommit mode")
    report = ApplyReport()
    started = time.monotonic()
    connection.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(galaxy.STATEMENT_TIMEOUT)))
    connection.execute("SET lock_timeout = '60s'")
    target = galaxy.preflight(connection, need_queue=True)
    if not connection.execute("SELECT pg_try_advisory_lock(%s)", (galaxy.LOCK_KEY,)).fetchone()[0]:
        raise galaxy.UpdateError("a galaxy update or another scoring run is going on; this one did not start")
    try:
        scorer = Scorer(connection)
        if reach.available(connection):
            report.reach_report = reach.update(connection, target, progress=progress)
            report.outside_reach = reach.drop_outside(connection)
        take = sql.SQL("SELECT system_id64, changed_tables FROM {} WHERE system_id64 > %s "
                       "ORDER BY system_id64 LIMIT %s").format(sql.SQL(galaxy.QUEUE_TABLE))
        last, after = started, -1
        while True:
            size = galaxy.BATCH_SYSTEMS if limit is None else min(galaxy.BATCH_SYSTEMS, limit - report.systems_read)
            if size <= 0:
                report.stopped_early = True
                break
            rows = connection.execute(take, (after, size)).fetchall()
            if not rows:
                break
            after = rows[-1][0]
            waiting_batch(scorer, {int(system_id): list(tables) for system_id, tables in rows}, report)
            if progress and time.monotonic() - last >= galaxy.PROGRESS_SECONDS:
                last = time.monotonic()
                galaxy.say(f"  ... {report.systems_read:,} systems taken from the waiting list, "
                           f"{report.added + report.changed + report.same:,} given scores, "
                           f"{int(last - started) // 60} min")
        if report.reach_report is not None and not report.stopped_early:
            # Systems in reach with no scores: ones that came within reach.
            unscored = reach.without_scores(connection, scorer.generation_id)
            report.unscored_in_reach = len(unscored)
            position = 0
            while position < len(unscored):
                # A limit counts these systems too.
                size = galaxy.BATCH_SYSTEMS if limit is None else min(
                    galaxy.BATCH_SYSTEMS, limit - report.systems_read - position)
                if size <= 0:
                    report.stopped_early = True
                    break
                batch = unscored[position:position + size]
                waiting_batch(scorer, {system_id: list(SCORING_TABLES) for system_id in batch},
                              report, from_the_list=False)
                position += len(batch)
                if progress and time.monotonic() - last >= galaxy.PROGRESS_SECONDS:
                    last = time.monotonic()
                    galaxy.say(f"  ... {position:,} of {len(unscored):,} systems in reach "
                               f"without scores done, {int(last - started) // 60} min")
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
        description="apply: give new scores to the systems on the waiting list. "
                    "check: work scores out again from a Spansh galaxy file and compare; read only.")
    parser.add_argument("mode", choices=("check", "apply"),
                        help="check: read only, needs a file. apply: score the waiting list.")
    parser.add_argument("file", type=Path, nargs="?", default=None,
                        help="a Spansh galaxy*.json.gz file, for check. apply does not use one "
                             "(one is accepted, and ignored, so older launchers still work).")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many systems")
    args = parser.parse_args(argv)
    if args.mode == "check" and args.file is None:
        galaxy.say("FAILED: check needs a Spansh galaxy file to compare with")
        return 1
    if args.mode == "check" and not args.file.is_file():
        galaxy.say(f"FAILED: there is no file {args.file}")
        return 1
    if args.limit is not None and args.limit < 1:
        galaxy.say("FAILED: --limit must be 1 or more")
        return 1
    connection = galaxy.connect()
    if connection is None:
        return galaxy.EXIT_NO_CONNECTION
    with connection:
        galaxy.say(f"Score check (read only): {args.file.name}" if args.mode == "check"
                   else "Scoring the systems on the waiting list")
        try:
            if args.mode == "apply":
                written = apply_waiting(connection, limit=args.limit)
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
            "cannot_score": len(report.cannot_score),
            "database_same": report.database_same,
            "database_different": len(report.database_different),
            "database_unknown": report.database_unknown}, sort_keys=True))
    galaxy.say("CHECKED: nothing was changed. " + report.verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
