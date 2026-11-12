"""Working scores out again and checking them against what is stored.

Runs on PostgreSQL 18 with every migration applied. The database holds the
twelve real fixture systems as production held them in August, with scores,
archetypes and Find rows made for them by the frozen build code through its
own paths (chunk 0). ``spansh_delta.scores`` then has to reproduce those rows
by its own route: reading our rows back out of the database, and asking for
systems by id instead of by chunk.

Needs RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import inspect
import os
from pathlib import Path
import sys
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

os.environ.setdefault("CORS_ORIGINS", "https://galaxy-scores-test.invalid")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "importer" / "src"))

from spansh_delta import apply as galaxy_update  # noqa: E402
from spansh_delta import scores  # noqa: E402
from tests.helpers import finder_seed  # noqa: E402
from tests.ratings_v4_pg_fixture import canonical_database  # noqa: E402
from tests.test_galaxy_update import (  # noqa: E402
    HD_38179, SEPTEMBER, SMOJOO, SOL, THAILE, Galaxy, _environment, as_new_system, carrier_from,
    queue, run, snapshot,
)
from tests.test_spansh_delta import write_galaxy  # noqa: E402

ANNOUNCE = "SET LOCAL edfinder.nightly_update = 'on'"
SCORE_TABLES = ("system_rating_vector", "body_mechanics", "economy_opportunity",
                "system_archetype", "system_search")


def _insert(connection, table: str, columns, rows) -> None:
    statement = sql.SQL("INSERT INTO v3_derived.{} ({}) VALUES ({})").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() * len(columns)))
    with connection.cursor() as cursor:
        cursor.executemany(statement, rows)


@pytest.fixture(scope="module")
def template():
    """Published scores for the twelve real systems, made by the frozen code's
    own chunk paths. Built once; each test gets its own copy."""
    from scripts import v3_system_archetype as archetype_build
    from scripts import v3_system_search as search_build
    from scripts.ratings_v4 import production_generation as build
    from shared_contracts import v3_system_archetype_model as model

    with canonical_database() as (connection, canonical, metadata, payloads):
        generation = finder_seed.build_finder_database(connection, systems=200)
        records = {payload["system"]["id64"]: payload["system"] for payload in payloads}
        encoded, _ = build.encode_chunk(generation, 0, canonical, metadata, list(records.values()))
        with connection.transaction():
            connection.execute(ANNOUNCE)
            _insert(connection, "system_rating_vector", build.VECTOR_COLUMNS, encoded["vectors"])
            _insert(connection, "body_mechanics", build.BODY_COLUMNS, encoded["bodies"])
            _insert(connection, "economy_opportunity", build.OPPORTUNITY_COLUMNS, encoded["opportunities"])
            # Find rows: the frozen query, for the whole of chunk 0.
            connection.execute(
                sql.SQL("INSERT INTO v3_derived.system_search ({}) ").format(
                    sql.SQL(", ").join(map(sql.Identifier, scores.FIND_COLUMNS)))
                + sql.SQL(search_build.projection_query_sql()).format(
                    schema=sql.Identifier(canonical["canonical_schema"])),
                {"generation_id": generation, "chunk_ordinal": 0})
            # Archetypes: the frozen builder's own read of chunk 0.
            raw = archetype_build._read_chunk_vectors(connection, generation, 0)
            now = datetime.now(timezone.utc)
            _insert(connection, "system_archetype", archetype_build.ALL_STORED_COLUMNS, [
                archetype_build._wide_tuple(generation, system_id, model.score_raw_system(*raw[system_id]), now)
                for system_id in sorted(records)])
        database = connection.info.dbname
        connection.close()
        yield database, canonical["canonical_schema"], records, generation


@pytest.fixture
def galaxy(template):
    database, schema, records, generation = template
    dsn = os.environ["RATINGS_V4_VALIDATION_DATABASE_URL"]
    name = "galaxy_scores_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
            sql.Identifier(name), sql.Identifier(database)))
        try:
            conninfo = make_conninfo(dsn, dbname=name)
            with psycopg.connect(conninfo, autocommit=True) as connection:
                # The old server's database, which does not keep reach (migration
                # 028 is for the Pi): there the scoring takes every waiting system.
                # The Pi's way is tested in test_reach.py.
                connection.execute("DROP TABLE v3_state.system_reach, v3_state.reach_anchor, "
                                   "v3_state.reach_log")
                found = Galaxy(connection, schema, deepcopy(records), conninfo)
                found.generation = generation
                yield found
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def check(galaxy: Galaxy, tmp_path: Path, records) -> scores.ScoreReport:
    path = write_galaxy(tmp_path / f"galaxy_{uuid4().hex[:8]}.json.gz", records)
    return scores.check_file(galaxy.connection, path, progress=False)


def score_rows(galaxy: Galaxy) -> dict[str, list[str]]:
    return {table: sorted(row[0] for row in galaxy.connection.execute(
        f"SELECT xmin::text || ' ' || t::text FROM v3_derived.{table} t")) for table in SCORE_TABLES}


# -- the frozen code, reached by a different route --------------------------


def test_find_columns_are_the_ones_the_frozen_builder_writes():
    from scripts import v3_system_search as search_build

    source = inspect.getsource(search_build._insert_chunk)
    listed = source.split("INSERT INTO v3_derived.system_search(")[1].split(")")[0]
    assert tuple(name.strip() for name in listed.split(",")) == scores.FIND_COLUMNS


def test_every_stored_score_row_of_the_real_systems_is_reproduced(galaxy):
    """Our rows read back from the database, scored for systems named by id,
    give exactly what the frozen build made from the fixture for chunk 0."""
    scorer = scores.Scorer(galaxy.connection)
    ids = sorted(galaxy.records)
    worked_out, failed = scorer.score(list(galaxy.records.values()))
    stored = scorer.stored(ids)
    assert not failed and sorted(worked_out.vectors) == ids
    assert sum(len(rows) for rows in stored.bodies.values()) == 360
    for system_id in ids:
        mine, theirs = worked_out.tables(system_id), stored.tables(system_id)
        for table, (columns, rows) in mine.items():
            assert rows and scores.differences(columns, rows, theirs[table][1]) == set(), (system_id, table)


def test_find_rows_asked_for_by_system_equal_the_chunk_query(galaxy):
    from scripts import v3_system_search as search_build

    scorer = scores.Scorer(galaxy.connection)
    by_chunk = {row[1]: tuple(row) for row in galaxy.connection.execute(
        sql.SQL(search_build.projection_query_sql()).format(schema=sql.Identifier(galaxy.schema)),
        {"generation_id": galaxy.generation, "chunk_ordinal": 0})}
    by_system = scorer.find_rows(sorted(galaxy.records))
    assert sorted(by_system) == sorted(galaxy.records) == sorted(by_chunk)
    assert by_system == by_chunk
    # Asking for some systems returns only those.
    assert sorted(scorer.find_rows([SOL, HD_38179])) == sorted([SOL, HD_38179])
    assert scorer.find_rows([]) == {}


# -- the check --------------------------------------------------------------


def test_check_reproduces_stored_scores_for_untouched_systems_and_writes_nothing(galaxy, tmp_path):
    before, galaxy_before = score_rows(galaxy), snapshot(galaxy)
    report = check(galaxy, tmp_path, galaxy.records.values())
    assert score_rows(galaxy) == before and snapshot(galaxy) == galaxy_before
    # Three of the twelve were not re-scanned between August and September;
    # the other nine have newer records than our rows.
    assert (report.systems_read, report.settled, report.out_of_date) == (12, 3, 9)
    assert (report.settled_same, report.find_same) == (3, 3)
    assert report.agrees and not report.cannot_score and report.waiting == 0
    # The same three, scored from our own rows with no record, come out the same.
    assert (report.database_same, report.database_different, report.database_unknown) == (3, {}, 0)
    text = "\n".join(report.lines())
    assert "scores worked out again, identical:" in text and "(cannot be scored from it)" in text
    assert "Same systems, scored from the database alone:" in text
    assert "identical to scoring from Spansh's records:" in text


def test_check_notices_a_stored_score_that_is_not_what_the_code_gives(galaxy, tmp_path):
    with galaxy.connection.transaction():
        galaxy.connection.execute(ANNOUNCE)
        galaxy.connection.execute(
            "UPDATE v3_derived.system_rating_vector SET potential[1] = potential[1] + 1 "
            "WHERE system_id64 = %s", (HD_38179,))
        galaxy.connection.execute(
            "UPDATE v3_derived.system_search SET station_count = 99 WHERE system_id64 = %s", (HD_38179,))
    report = check(galaxy, tmp_path, [galaxy.records[HD_38179]])
    assert not report.agrees
    assert report.settled_different == {HD_38179: ["system_rating_vector.potential"]}
    assert report.find_different == {HD_38179: ["station_count"]}
    text = "\n".join(report.lines())
    assert "1 systems differ in system_rating_vector.potential" in text
    assert f"for example system {HD_38179}: station_count" in text


def test_check_says_so_when_the_database_would_score_a_system_differently(galaxy, tmp_path, monkeypatch):
    """The check scores each system twice: from its Spansh record, and from
    our own rows. Here our rows are made to lose one body's sub-type."""
    made = scores.Scorer.records

    def losing_a_class(self, exported, scored):
        records, unknown = made(self, exported, scored)
        for record in records:
            planet = next(body for body in record["bodies"] if body.get("type") == "planet" and "subType" in body)
            del planet["subType"]
        return records, unknown

    monkeypatch.setattr(scores.Scorer, "records", losing_a_class)
    report = check(galaxy, tmp_path, [galaxy.records[HD_38179]])
    # The stored scores are still what the record gives...
    assert (report.settled_same, report.settled_different) == (1, {})
    # ...but the database alone would not have given them.
    assert list(report.database_different) == [HD_38179] and report.database_same == 0
    assert not report.agrees
    assert report.verdict.startswith("Scoring from the database did NOT always give what Spansh's records give")
    text = "\n".join(report.lines())
    assert f"for example system {HD_38179} (from the record -> from the database):" in text
    assert "1 systems differ in body_mechanics.body_class" in text


def test_a_body_re_stamped_by_spansh_without_any_change_is_not_a_difference(galaxy, tmp_path):
    record = deepcopy(galaxy.records[HD_38179])
    for body in record["bodies"]:
        body["updateTime"] = "2026-10-01 00:00:00+00"
    report = check(galaxy, tmp_path, [record])
    assert (report.settled, report.settled_same, report.settled_restamped) == (1, 1, 1)
    assert report.agrees
    assert "only the record's stamp differs:" in "\n".join(report.lines())


def test_after_a_galaxy_update_rewritten_systems_can_be_scored(galaxy, tmp_path):
    run(galaxy, tmp_path, galaxy.records.values())
    report = check(galaxy, tmp_path, galaxy.records.values())
    assert report.out_of_date == 0 and not report.cannot_score
    # Three systems were rewritten without any change to what scores are made
    # from (they gained stations or ring signals). Their rows now come from
    # the galaxy update's run, and their scores must still come out the same.
    assert (report.settled, report.settled_same, report.settled_different) == (3, 3, {})
    # The other nine were re-scanned: they wait, and every one can be scored.
    assert (report.waiting, report.waiting_scored) == (9, 9)
    assert 1 <= report.waiting_would_change <= 9
    # Rewritten systems' Find rows are not expected to match (new dates,
    # station counts), so they are not compared.
    assert report.find_same == 0 and not report.find_different


def test_a_system_with_a_retired_body_can_still_be_scored(galaxy, tmp_path):
    run(galaxy, tmp_path, galaxy.records.values())
    without = deepcopy(galaxy.records[SMOJOO])
    ringed = next(body for body in without["bodies"] if body.get("rings"))
    without["bodies"].remove(ringed)
    run(galaxy, tmp_path, [without], made_at=SEPTEMBER + timedelta(days=1))
    assert galaxy.one("SELECT lifecycle_state FROM {s}.bodies WHERE body_pk = %s", ringed["id64"])[0] == "RETIRED"
    report = check(galaxy, tmp_path, [without])
    assert (report.waiting, report.waiting_scored, report.cannot_score) == (1, 1, {})
    worked_out, failed = scores.Scorer(galaxy.connection).score([without])
    assert not failed and ringed["id64"] not in {row[2] for row in worked_out.bodies[SMOJOO]}


def test_rows_from_an_update_that_stopped_early_are_accepted_but_not_from_one_still_running(
        galaxy, tmp_path):
    run(galaxy, tmp_path, galaxy.records.values())
    galaxy.connection.execute(
        "UPDATE v3_source.source_run SET run_state = 'FAILED', failure_code = 'X' "
        "WHERE acquisition_kind = 'DELTA'")
    assert not check(galaxy, tmp_path, galaxy.records.values()).cannot_score

    galaxy.connection.execute(
        "UPDATE v3_source.source_run SET run_state = 'RUNNING', completed_at = NULL, failure_code = NULL "
        "WHERE acquisition_kind = 'DELTA'")
    report = check(galaxy, tmp_path, galaxy.records.values())
    assert len(report.cannot_score) == 12
    assert report.cannot_reasons == {"unregistered source run in canonical row": 12}


def test_one_system_that_cannot_be_scored_does_not_stop_the_others(galaxy, tmp_path):
    # Give one system's bodies to a run the scoring code has never heard of.
    stranger = uuid4()
    galaxy.connection.execute(
        """INSERT INTO v3_source.source_run
           SELECT (jsonb_populate_record(r, jsonb_build_object(
                      'source_run_id', %s::text, 'idempotency_key', 'stranger', 'run_state', 'RUNNING',
                      'completed_at', NULL))).*
             FROM v3_source.source_run r LIMIT 1""", (str(stranger),))
    galaxy.connection.execute(
        f"UPDATE {galaxy.schema}.bodies SET source_run_id = %s WHERE system_id64 = %s", (stranger, HD_38179))
    report = check(galaxy, tmp_path, galaxy.records.values())
    assert report.cannot_score == {HD_38179: "unregistered source run in canonical row"}
    assert (report.settled, report.settled_same) == (3, 2)
    assert "1 x unregistered source run in canonical row" in "\n".join(report.lines())


# -- scoring the waiting list, from the database alone -----------------------
#
# This galaxy is the old server's: no body row has a class of its own (they
# were all written before migration 027) and every body has a score row.


def waiting(galaxy: Galaxy, **options) -> scores.ApplyReport:
    return scores.apply_waiting(galaxy.connection, progress=False, **options)


def agree(columns, ours, theirs) -> bool:
    """The same scores; only the stamps of the record they came from may differ."""
    return scores.differences(columns, ours, theirs) <= scores.RECORD_STAMPS


def test_our_own_rows_give_the_scores_that_spansh_records_give(galaxy):
    # 360 bodies that get scores and 29 barycentres; none has a class of its own.
    assert galaxy.one("SELECT count(*), count(body_class) FROM {s}.bodies") == (389, 0)
    scorer = scores.Scorer(galaxy.connection)
    ids = sorted(galaxy.records)
    from_records, failed = scorer.score(list(galaxy.records.values()))
    exported, missing = scorer.export(ids)
    # None of them is waiting for new scores, so their score rows can be
    # relied on for the classes.
    records, unknown = scorer.records(exported, scorer.classes(scorer.stored(ids), ids))
    from_rows, failed_rows = scorer.score(records, exported)
    assert not (failed or missing or unknown or failed_rows)
    assert sorted(record["id64"] for record in records) == ids
    for system_id in ids:
        theirs = from_records.tables(system_id)
        for table, (columns, rows) in from_rows.tables(system_id).items():
            assert rows and agree(columns, rows, theirs[table][1]), (system_id, table)
    # What was made from our rows is what the scoring code calls a record.
    body = records[0]["bodies"][0]
    assert set(body) <= {"id64", "bodyId", "type", "subType", "updateTime"} and body["type"] in ("star", "planet")


def test_an_old_row_is_not_rewritten_just_because_its_class_is_not_recorded(galaxy, tmp_path):
    """Every night Spansh lists thousands of systems in which nothing we store
    changed. Their body rows have no class of their own; the class in their
    score rows says they are unchanged, and they are left alone."""
    run(galaxy, tmp_path, galaxy.records.values())
    waiting(galaxy)
    galaxy.connection.execute(f"UPDATE {galaxy.schema}.bodies SET body_class = NULL")
    before = snapshot(galaxy)

    report = run(galaxy, tmp_path, galaxy.records.values(), made_at=SEPTEMBER + timedelta(days=1))

    assert (report.changed, report.unchanged) == (0, 12)
    after = snapshot(galaxy)
    assert {name for name in after if after[name] != before[name]} <= {"runs", "files"}
    assert queue(galaxy) == {}


def test_a_class_spansh_changed_reaches_the_scores_through_the_waiting_list(galaxy, tmp_path):
    """What happened to ten systems on 11 November: Spansh filled in a class,
    nothing else about the system changed, and our galaxy rows could not show
    it. Now the galaxy update sees it, and the scores follow."""
    record = deepcopy(galaxy.records[HD_38179])
    body = next(body for body in record["bodies"] if body.get("type") == "Planet" and body.get("subType"))
    was = body["subType"]
    now = "Rocky body" if was != "Rocky body" else "High metal content world"
    body["subType"] = now

    # Read only: this is our rows being behind the file, not a wrong score.
    seen = check(galaxy, tmp_path, [record])
    assert (seen.out_of_date, seen.settled, seen.settled_different) == (1, 0, {})

    run(galaxy, tmp_path, [record])
    assert "bodies" in queue(galaxy)[HD_38179]
    assert galaxy.one("SELECT body_class FROM {s}.bodies WHERE body_pk = %s", body["id64"])[0] == now

    report = waiting(galaxy)
    assert (report.changed, report.cannot_score, report.still_waiting) == (1, {}, 0)
    assert galaxy.one("SELECT body_class, subtype_present FROM v3_derived.body_mechanics WHERE body_pk = %s",
                      body["id64"]) == (now, True)
    after = check(galaxy, tmp_path, [record])
    assert after.agrees and (after.settled_same, after.database_same) == (1, 1)


def test_the_waiting_list_is_scored_with_no_file_at_hand(galaxy, tmp_path):
    run(galaxy, tmp_path, galaxy.records.values())
    assert len(queue(galaxy)) == 12
    # What Spansh's records give for the rows as they now are.
    expected, failed = scores.Scorer(galaxy.connection).score(list(galaxy.records.values()))
    assert not failed

    report = waiting(galaxy)

    assert (report.systems_read, report.added, report.cannot_score, report.still_waiting) == (12, 0, {}, 0)
    # Nine were re-scanned and are scored again. The other three only gained
    # stations or ring signals: their scores stand and their Find rows are rebuilt.
    assert report.changed + report.same == 9 and report.changed >= 1
    assert (report.find_rows, report.find_only) == (9, 3)
    assert queue(galaxy) == {}
    stored = scores.Scorer(galaxy.connection).stored(sorted(galaxy.records))
    for system_id in galaxy.records:
        theirs = stored.tables(system_id)
        for table, (columns, rows) in expected.tables(system_id).items():
            assert agree(columns, rows, theirs[table][1]), (system_id, table)
    # The read-only check agrees, and now compares all twelve both ways.
    after = check(galaxy, tmp_path, galaxy.records.values())
    assert (after.settled, after.settled_same, after.find_same, after.database_same) == (12, 12, 12, 12)
    assert after.agrees and after.waiting == 0 and after.database_unknown == 0
    # Maia's stations (45 fleet carriers among them) now count in Find.
    assert galaxy.one("SELECT station_count FROM v3_derived.system_search WHERE system_id64 = 8216113749")[0] \
        == galaxy.one("SELECT count(*) FROM {s}.stations WHERE system_id64 = 8216113749 "
                      "AND lifecycle_state = 'ACTIVE'")[0] > 45
    # The 200 other systems' rows were not touched, and each chunk is as it was.
    assert galaxy.one("SELECT count(*) FROM v3_derived.system_rating_vector")[0] == 212
    assert galaxy.one("SELECT count(DISTINCT chunk_ordinal) FROM v3_derived.system_rating_vector")[0] == 1
    text = "\n".join(report.lines())
    assert "Systems taken from the waiting list:" in text and "Systems given their scores:" in text

    # With nothing waiting, a second run touches nothing at all.
    before = score_rows(galaxy)
    again = waiting(galaxy)
    assert (again.systems_read, again.find_rows) == (0, 0) and score_rows(galaxy) == before
    # And the tables are protected again once the scoring transaction is over.
    with pytest.raises(psycopg.errors.RaiseException, match="immutable in state PUBLISHED"):
        galaxy.connection.execute(
            "UPDATE v3_derived.system_rating_vector SET system_id64 = system_id64 WHERE system_id64 = %s",
            (SOL,))


def test_systems_that_are_not_waiting_are_left_exactly_as_they_are(galaxy, tmp_path):
    run(galaxy, tmp_path, [galaxy.records[HD_38179]])
    others = "SELECT xmin::text || ' ' || t::text FROM v3_derived.{} t WHERE system_id64 <> %s ORDER BY 1"
    before = {table: galaxy.all(others.format(table), HD_38179) for table in SCORE_TABLES}
    report = waiting(galaxy)
    assert report.systems_read == 1 and report.still_waiting == 0
    assert {table: galaxy.all(others.format(table), HD_38179) for table in SCORE_TABLES} == before


def test_a_system_never_scored_before_gets_scores_an_archetype_and_a_find_row(galaxy, tmp_path):
    new = as_new_system(galaxy.records[HD_38179], 99158872029, "Test Sector AA-A a1")
    run(galaxy, tmp_path, [new])
    report = waiting(galaxy)
    assert (report.added, report.changed, report.same, report.still_waiting) == (1, 0, 0, 0)
    assert galaxy.one("SELECT name, body_count FROM v3_derived.system_search WHERE system_id64 = %s",
                      new["id64"]) == ("Test Sector AA-A a1", 14)
    assert galaxy.one("SELECT count(*) FROM v3_derived.system_archetype WHERE system_id64 = %s",
                      new["id64"])[0] == 1
    classes = ("SELECT array_agg(body_class ORDER BY source_body_id64) FROM v3_derived.body_mechanics "
               "WHERE system_id64 = %s")
    assert galaxy.one(classes, new["id64"]) == galaxy.one(classes, HD_38179)
    # Filed under an existing build chunk, as every score row has to be.
    assert galaxy.one(
        """SELECT count(*) FROM v3_derived.system_rating_vector v
             JOIN v3_derived.build_chunk c USING (derived_generation_id, chunk_ordinal)
            WHERE v.system_id64 = %s""", new["id64"])[0] == 1
    # The same real system under another name scores the same as the original.
    same = "SELECT potential, quality, completeness FROM v3_derived.system_rating_vector WHERE system_id64 = %s"
    assert galaxy.one(same, new["id64"]) == galaxy.one(same, HD_38179)


def test_a_system_whose_classes_are_not_in_the_database_waits_and_says_why(galaxy, tmp_path):
    new = as_new_system(galaxy.records[HD_38179], 99158872029, "Test Sector AA-A a1")
    run(galaxy, tmp_path, [new, galaxy.records[SOL]])
    # As a galaxy update from before migration 027 left a new system: no class
    # in its rows, and no score row yet to take one from.
    galaxy.connection.execute(
        f"UPDATE {galaxy.schema}.bodies SET body_class = NULL WHERE system_id64 = %s", (new["id64"],))

    report = waiting(galaxy)

    assert report.cannot_score == {new["id64"]: scores.UNKNOWN_CLASS}
    assert (report.changed + report.same, report.still_waiting) == (1, 1)
    assert set(queue(galaxy)) == {new["id64"]}
    assert galaxy.one("SELECT count(*) FROM v3_derived.system_rating_vector WHERE system_id64 = %s",
                      new["id64"])[0] == 0
    text = "\n".join(report.lines())
    assert "1 x the class of one of its bodies is not in the database" in text and "(still waiting)" in text
    assert "The next galaxy update that lists the system fills them in." in text
    # The read-only check says the same thing in its own words.
    seen = check(galaxy, tmp_path, [new])
    assert (seen.out_of_date, seen.waiting) == (1, 0)

    # The next galaxy update that lists the system fills its classes in...
    again = run(galaxy, tmp_path, [new], made_at=SEPTEMBER + timedelta(days=1))
    assert (again.changed, dict(again.systems_by_table)) == (1, {"bodies": 1})
    # ...and then it is scored like any other.
    report = waiting(galaxy)
    assert (report.added, report.cannot_score, report.still_waiting) == (1, {}, 0)


def test_an_old_score_row_does_not_vouch_for_a_system_that_is_waiting(galaxy, tmp_path):
    """A galaxy update from before migration 027 rewrote a system without its
    classes, and the system was not scored again before this code arrived. Its
    score rows are older than its rows, so they may say what a body used to
    be. The system waits; it is not given scores made from the old class."""
    record = deepcopy(galaxy.records[HD_38179])
    body = next(body for body in record["bodies"] if body.get("type") == "Planet" and body.get("subType"))
    was = body["subType"]
    now = "Rocky body" if was != "Rocky body" else "High metal content world"
    body["subType"] = now
    run(galaxy, tmp_path, [record])
    galaxy.connection.execute(
        f"UPDATE {galaxy.schema}.bodies SET body_class = NULL WHERE system_id64 = %s", (HD_38179,))
    assert "bodies" in queue(galaxy)[HD_38179]
    stored_class = "SELECT body_class FROM v3_derived.body_mechanics WHERE body_pk = %s"
    assert galaxy.one(stored_class, body["id64"])[0] == was
    before = score_rows(galaxy)

    report = waiting(galaxy)

    assert report.cannot_score == {HD_38179: scores.UNKNOWN_CLASS} and report.still_waiting == 1
    assert score_rows(galaxy) == before
    # Read only: our rows cannot be said to hold what the record says.
    seen = check(galaxy, tmp_path, [record])
    assert (seen.out_of_date, seen.settled, seen.waiting) == (1, 0, 0)

    # The next update that lists the system writes its classes, though
    # nothing else about it differs...
    again = run(galaxy, tmp_path, [record], made_at=SEPTEMBER + timedelta(days=1))
    assert (again.changed, dict(again.systems_by_table)) == (1, {"bodies": 1})
    assert galaxy.one("SELECT count(*) FROM {s}.bodies WHERE system_id64 = %s "
                      "AND lifecycle_state = 'ACTIVE' AND body_class IS NULL", HD_38179)[0] == 0
    # ...and then its scores follow.
    report = waiting(galaxy)
    assert (report.changed, report.cannot_score, report.still_waiting) == (1, {}, 0)
    assert galaxy.one(stored_class, body["id64"])[0] == now
    assert check(galaxy, tmp_path, [record]).agrees


def test_a_station_coming_or_going_does_not_put_a_systems_classes_in_doubt(galaxy, tmp_path):
    """A system can be on the waiting list only because a station came or
    went. Nothing its scores are made from has changed, so its score rows
    still say what its bodies are, and it is not rewritten for its classes."""
    run(galaxy, tmp_path, galaxy.records.values())
    waiting(galaxy)
    galaxy.connection.execute(f"UPDATE {galaxy.schema}.bodies SET body_class = NULL")
    galaxy.connection.execute(
        f"INSERT INTO {galaxy_update.QUEUE_TABLE} (system_id64, changed_tables, source_run_id) "
        "SELECT %s, '{stations}', source_run_id FROM v3_source.source_run "
        "WHERE acquisition_kind = 'DELTA' LIMIT 1", (SOL,))

    report = run(galaxy, tmp_path, [galaxy.records[SOL]], made_at=SEPTEMBER + timedelta(days=1))
    assert (report.changed, report.unchanged) == (0, 1)
    assert queue(galaxy) == {SOL: ["stations"]}

    # The same system waiting for new scores is another matter.
    galaxy.connection.execute(
        f"UPDATE {galaxy_update.QUEUE_TABLE} SET changed_tables = '{{rings,stations}}' WHERE system_id64 = %s",
        (SOL,))
    report = run(galaxy, tmp_path, [galaxy.records[SOL]], made_at=SEPTEMBER + timedelta(days=2))
    assert (report.changed, dict(report.systems_by_table)) == (1, {"bodies": 1})
    assert queue(galaxy) == {SOL: ["bodies", "rings", "stations"]}


def test_a_system_with_a_retired_body_is_scored_without_it(galaxy, tmp_path):
    run(galaxy, tmp_path, galaxy.records.values())
    waiting(galaxy)
    without = deepcopy(galaxy.records[SMOJOO])
    ringed = next(body for body in without["bodies"] if body.get("rings"))
    without["bodies"].remove(ringed)
    run(galaxy, tmp_path, [without], made_at=SEPTEMBER + timedelta(days=1))
    assert galaxy.one("SELECT lifecycle_state FROM {s}.bodies WHERE body_pk = %s", ringed["id64"])[0] == "RETIRED"
    report = waiting(galaxy)
    assert (report.changed, report.cannot_score, report.still_waiting) == (1, {}, 0)
    assert galaxy.one("SELECT count(*) FROM v3_derived.body_mechanics WHERE body_pk = %s", ringed["id64"])[0] == 0
    assert galaxy.one("SELECT body_count FROM v3_derived.system_search WHERE system_id64 = %s", SMOJOO)[0] \
        == len(without["bodies"])


def test_a_system_a_carrier_left_gets_its_find_row_rebuilt(galaxy, tmp_path):
    run(galaxy, tmp_path, [galaxy.records[THAILE], galaxy.records[HD_38179]])
    waiting(galaxy)
    count = "SELECT station_count FROM v3_derived.system_search WHERE system_id64 = %s"
    assert (galaxy.one(count, THAILE)[0], galaxy.one(count, HD_38179)[0]) == (2, 0)

    # A carrier jumps from Thaile to HD 38179. Only HD 38179 is in the file.
    arrived = deepcopy(galaxy.records[HD_38179])
    arrived["stations"] = [carrier_from(galaxy.records[THAILE])]
    run(galaxy, tmp_path, [arrived], made_at=SEPTEMBER + timedelta(days=1))
    listed = queue(galaxy)
    assert listed[THAILE] == ["stations"] and "stations" in listed[HD_38179]
    assert not set(listed[HD_38179]) & set(scores.SCORING_TABLES)
    scores_before = [galaxy.all(f"SELECT xmin::text || ' ' || t::text FROM v3_derived.{table} t "
                                "WHERE system_id64 = ANY(%s) ORDER BY 1", [THAILE, HD_38179])
                     for table in SCORE_TABLES[:4]]

    report = waiting(galaxy)

    # Neither system's scores were touched: only their Find rows.
    assert (report.find_only, report.find_rows, report.still_waiting) == (2, 0, 0)
    assert report.added + report.changed + report.same == 0
    assert (galaxy.one(count, THAILE)[0], galaxy.one(count, HD_38179)[0]) == (1, 1)
    assert [galaxy.all(f"SELECT xmin::text || ' ' || t::text FROM v3_derived.{table} t "
                       "WHERE system_id64 = ANY(%s) ORDER BY 1", [THAILE, HD_38179])
            for table in SCORE_TABLES[:4]] == scores_before
    assert "for a station coming or going only:" in "\n".join(report.lines())


def test_a_system_that_cannot_be_scored_stays_on_the_waiting_list(galaxy, tmp_path):
    run(galaxy, tmp_path, [galaxy.records[SOL], galaxy.records[HD_38179]])
    stranger = uuid4()
    galaxy.connection.execute(
        """INSERT INTO v3_source.source_run
           SELECT (jsonb_populate_record(r, jsonb_build_object(
                      'source_run_id', %s::text, 'idempotency_key', 'stranger', 'run_state', 'RUNNING',
                      'completed_at', NULL))).*
             FROM v3_source.source_run r WHERE r.acquisition_kind = 'DELTA' LIMIT 1""", (str(stranger),))
    galaxy.connection.execute(
        f"UPDATE {galaxy.schema}.bodies SET source_run_id = %s WHERE system_id64 = %s", (stranger, SOL))
    before = galaxy.all("SELECT xmin::text, t::text FROM v3_derived.system_rating_vector t "
                        "WHERE system_id64 = %s", SOL)
    report = waiting(galaxy)
    assert report.cannot_score == {SOL: "unregistered source run in canonical row"}
    assert set(queue(galaxy)) == {SOL} and report.still_waiting == 1
    # HD 38179, in the same batch, was dealt with all the same.
    assert report.find_only == 1
    assert galaxy.all("SELECT xmin::text, t::text FROM v3_derived.system_rating_vector t "
                      "WHERE system_id64 = %s", SOL) == before
    assert "(still waiting)" in "\n".join(report.lines())


def test_a_score_row_the_database_refuses_keeps_only_its_own_system_waiting(galaxy, tmp_path):
    run(galaxy, tmp_path, [galaxy.records[SOL], galaxy.records[HD_38179], galaxy.records[THAILE]])
    assert set(queue(galaxy)) == {SOL, HD_38179, THAILE}
    # A rule that Sol's new scores break, and nothing else does.
    galaxy.connection.execute(
        f"ALTER TABLE v3_derived.system_rating_vector ADD CONSTRAINT not_sol CHECK (system_id64 <> {SOL}) NOT VALID")
    sol = ("SELECT xmin::text || ' ' || t::text FROM v3_derived.{} t WHERE system_id64 = %s ORDER BY 1")
    before = {table: galaxy.all(sol.format(table), SOL) for table in SCORE_TABLES}

    report = waiting(galaxy)

    assert list(report.cannot_score) == [SOL] and report.cannot_score[SOL].startswith("not_sol: ")
    assert set(queue(galaxy)) == {SOL} and report.still_waiting == 1
    # The other two, in the same batch, were dealt with: counted once each.
    assert report.find_rows + report.find_only == 2
    assert report.added + report.changed + report.same == report.find_rows
    # Sol is exactly as it was, in every score table.
    assert {table: galaxy.all(sol.format(table), SOL) for table in SCORE_TABLES} == before
    assert check(galaxy, tmp_path, [galaxy.records[HD_38179], galaxy.records[THAILE]]).agrees
    assert "1 x not_sol: " in "\n".join(report.lines())

    # Once the rule is gone, Sol is scored on the next run.
    galaxy.connection.execute("ALTER TABLE v3_derived.system_rating_vector DROP CONSTRAINT not_sol")
    report = waiting(galaxy)
    assert (report.systems_read, report.cannot_score, report.still_waiting) == (1, {}, 0)


def test_a_waiting_system_that_is_not_in_the_galaxy_does_not_stop_the_others(galaxy, tmp_path):
    run(galaxy, tmp_path, [galaxy.records[HD_38179]])
    galaxy.connection.execute(
        f"INSERT INTO {galaxy_update.QUEUE_TABLE} (system_id64, changed_tables, source_run_id) "
        "SELECT 424242, '{bodies}', source_run_id FROM v3_source.source_run "
        "WHERE acquisition_kind = 'DELTA' LIMIT 1")
    report = waiting(galaxy)
    assert list(report.cannot_score) == [424242] and report.find_only == 1
    assert set(queue(galaxy)) == {424242}


def test_the_waiting_list_can_be_worked_through_a_part_at_a_time(galaxy, tmp_path):
    run(galaxy, tmp_path, galaxy.records.values())
    first = waiting(galaxy, limit=5)
    assert (first.systems_read, first.stopped_early, first.still_waiting) == (5, True, 7)
    assert "(stopped at the limit)" in "\n".join(first.lines())
    rest = waiting(galaxy)
    assert (rest.systems_read, rest.stopped_early, rest.still_waiting) == (7, False, 0)


def test_scoring_and_the_galaxy_update_never_run_at_the_same_time(galaxy, tmp_path):
    run(galaxy, tmp_path, [galaxy.records[HD_38179]])
    with psycopg.connect(galaxy.conninfo, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (galaxy_update.LOCK_KEY,))
        with pytest.raises(galaxy_update.UpdateError, match="did not start"):
            waiting(galaxy)
    assert waiting(galaxy).systems_read == 1


def test_command_scores_the_waiting_list_and_prints_plain_results(galaxy, tmp_path, monkeypatch, capsys):
    _environment(monkeypatch, galaxy.conninfo)
    run(galaxy, tmp_path, galaxy.records.values())
    assert scores.main(["apply"]) == 0
    out = capsys.readouterr().out
    assert "Scoring the systems on the waiting list" in out and "Systems given their scores:" in out
    assert out.rstrip().endswith("DONE") and queue(galaxy) == {}
    # A launcher from before this change still names a file. It is not needed.
    assert scores.main(["apply", str(tmp_path / "not-there.json.gz")]) == 0
    assert "Systems taken from the waiting list:" in capsys.readouterr().out
    assert scores.main(["apply", "--limit", "0"]) == 1
    galaxy.connection.execute("DROP TABLE v3_state.rescore_queue")
    assert scores.main(["apply"]) == 2
    assert "REFUSED" in capsys.readouterr().out


# -- the command ------------------------------------------------------------


def test_command_prints_a_plain_verdict(galaxy, tmp_path, monkeypatch, capsys):
    _environment(monkeypatch, galaxy.conninfo)
    path = write_galaxy(tmp_path / "galaxy_1day.json.gz", galaxy.records.values())
    assert scores.main(["check", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Score check (read only): galaxy_1day.json.gz" in out
    assert "identical to scoring from Spansh's records:" in out
    assert out.rstrip().endswith("CHECKED: nothing was changed. The stored scores were reproduced exactly, "
                                 "and scoring from the database alone gives the same.")

    with galaxy.connection.transaction():
        galaxy.connection.execute(ANNOUNCE)
        galaxy.connection.execute(
            "UPDATE v3_derived.system_archetype SET flexible_score = flexible_score + 1 "
            "WHERE system_id64 = %s", (HD_38179,))
    assert scores.main(["check", str(path)]) == 0
    out = capsys.readouterr().out
    assert "system_archetype.flexible_score" in out
    assert out.rstrip().endswith("were NOT all reproduced: see above.")

    # A file with nothing to compare says so, and does not claim a match.
    stranger = deepcopy(galaxy.records[HD_38179])
    stranger["id64"] = 99158872029
    other = write_galaxy(tmp_path / "other.json.gz", [stranger])
    assert scores.main(["check", str(other)]) == 0
    out = capsys.readouterr().out
    assert "not in our galaxy yet:" in out
    assert out.rstrip().endswith("so the stored scores could not be compared.")

    assert scores.main(["check", str(tmp_path / "missing.json.gz")]) == 1
    assert scores.main(["check"]) == 1
    assert "check needs a Spansh galaxy file" in capsys.readouterr().out
    monkeypatch.setenv("PGPORT", "1")
    assert scores.main(["check", str(path)]) == galaxy_update.EXIT_NO_CONNECTION
    assert "Could not connect to the database" in capsys.readouterr().out
