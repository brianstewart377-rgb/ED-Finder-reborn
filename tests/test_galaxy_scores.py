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
    text = "\n".join(report.lines())
    assert "scores worked out again, identical:" in text and "(cannot be scored from it)" in text


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


def test_a_body_re_stamped_by_spansh_without_any_change_is_not_a_difference(galaxy, tmp_path):
    record = deepcopy(galaxy.records[HD_38179])
    for body in record["bodies"]:
        body["updateTime"] = "2026-10-01 00:00:00+00"
    report = check(galaxy, tmp_path, [record])
    assert (report.settled, report.settled_same, report.settled_restamped) == (1, 1, 1)
    assert report.agrees
    assert "only the record's date moved:" in "\n".join(report.lines())


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


# -- writing the scores that are owed ----------------------------------------


def score(galaxy: Galaxy, tmp_path: Path, records, **options) -> scores.ApplyReport:
    path = write_galaxy(tmp_path / f"galaxy_{uuid4().hex[:8]}.json.gz", records)
    return scores.apply_file(galaxy.connection, path, progress=False, **options)


def test_untouched_systems_are_left_exactly_as_they_are(galaxy, tmp_path):
    before, galaxy_before = score_rows(galaxy), snapshot(galaxy)
    report = score(galaxy, tmp_path, galaxy.records.values())
    assert score_rows(galaxy) == before and snapshot(galaxy) == galaxy_before
    # Three match their records and score the same; nine have newer records
    # than our rows, so they cannot be scored until the galaxy update has run.
    assert (report.left_alone, report.out_of_date) == (3, 9)
    assert report.added + report.changed + report.same == 0 and report.find_rows == 0


def test_after_a_galaxy_update_every_rewritten_system_gets_its_scores_and_leaves_the_list(galaxy, tmp_path):
    run(galaxy, tmp_path, galaxy.records.values())
    assert len(queue(galaxy)) == 12

    report = score(galaxy, tmp_path, galaxy.records.values())

    assert (report.added, report.cannot_score, report.still_waiting) == (0, {}, 0)
    assert report.changed + report.same == 12 and report.changed >= 1 and report.find_rows == 12
    assert queue(galaxy) == {}
    # What is stored now is exactly what the frozen code gives for the new
    # rows, table by table, and the Find rows are what the frozen query gives.
    after = check(galaxy, tmp_path, galaxy.records.values())
    assert (after.settled, after.settled_same, after.find_same) == (12, 12, 12)
    assert after.agrees and after.settled_restamped == 0 and after.waiting == 0
    # Maia's stations (45 fleet carriers among them) now count in Find.
    assert galaxy.one("SELECT station_count FROM v3_derived.system_search WHERE system_id64 = 8216113749")[0] \
        == galaxy.one("SELECT count(*) FROM {s}.stations WHERE system_id64 = 8216113749 "
                      "AND lifecycle_state = 'ACTIVE'")[0] > 45
    # The 200 other systems' rows were not touched, and each chunk is as it was.
    assert galaxy.one("SELECT count(*) FROM v3_derived.system_rating_vector")[0] == 212
    assert galaxy.one("SELECT count(DISTINCT chunk_ordinal) FROM v3_derived.system_rating_vector")[0] == 1

    # Running it again changes nothing at all.
    before = score_rows(galaxy)
    again = score(galaxy, tmp_path, galaxy.records.values())
    assert again.left_alone == 12 and again.find_rows == 0 and score_rows(galaxy) == before
    # And the tables are protected again once the scoring transaction is over.
    with pytest.raises(psycopg.errors.RaiseException, match="immutable in state PUBLISHED"):
        galaxy.connection.execute(
            "UPDATE v3_derived.system_rating_vector SET system_id64 = system_id64 WHERE system_id64 = %s",
            (SOL,))


def test_a_system_never_scored_before_gets_scores_an_archetype_and_a_find_row(galaxy, tmp_path):
    new = as_new_system(galaxy.records[HD_38179], 99158872029, "Test Sector AA-A a1")
    run(galaxy, tmp_path, [new])
    report = score(galaxy, tmp_path, [new])
    assert (report.added, report.changed, report.same, report.still_waiting) == (1, 0, 0, 0)
    assert galaxy.one("SELECT name, body_count FROM v3_derived.system_search WHERE system_id64 = %s",
                      new["id64"]) == ("Test Sector AA-A a1", 14)
    assert galaxy.one("SELECT count(*) FROM v3_derived.system_archetype WHERE system_id64 = %s",
                      new["id64"])[0] == 1
    assert galaxy.one("SELECT count(*) FROM v3_derived.body_mechanics WHERE system_id64 = %s",
                      new["id64"])[0] == galaxy.one(
        "SELECT count(*) FROM v3_derived.body_mechanics WHERE system_id64 = %s", HD_38179)[0]
    # Filed under an existing build chunk, as every score row has to be.
    assert galaxy.one(
        """SELECT count(*) FROM v3_derived.system_rating_vector v
             JOIN v3_derived.build_chunk c USING (derived_generation_id, chunk_ordinal)
            WHERE v.system_id64 = %s""", new["id64"])[0] == 1
    # The same real system under another name scores the same as the original.
    same = "SELECT potential, quality, completeness FROM v3_derived.system_rating_vector WHERE system_id64 = %s"
    assert galaxy.one(same, new["id64"]) == galaxy.one(same, HD_38179)


def test_a_sub_type_spansh_changed_is_noticed_and_corrected_without_the_waiting_list(galaxy, tmp_path):
    record = deepcopy(galaxy.records[HD_38179])
    body = next(body for body in record["bodies"] if body.get("type") == "Planet" and body.get("subType"))
    was = body["subType"]
    now = "Rocky body" if was != "Rocky body" else "High metal content world"
    body["subType"] = now

    seen = check(galaxy, tmp_path, [record])
    assert list(seen.settled_different) == [HD_38179] and not seen.agrees
    text = "\n".join(seen.lines())
    assert f"for example system {HD_38179} (stored -> now):" in text
    assert f"body_mechanics.body_class (body {body['id64']}): {was!r} -> {now!r}" in text

    report = score(galaxy, tmp_path, [record])
    assert (report.changed, report.corrected, report.left_alone) == (1, 1, 0)
    assert "were not on the waiting list:" in "\n".join(report.lines())
    assert galaxy.one("SELECT body_class FROM v3_derived.body_mechanics WHERE body_pk = %s",
                      body["id64"])[0] == now
    assert check(galaxy, tmp_path, [record]).agrees


def test_a_system_a_carrier_left_gets_its_find_row_rebuilt_without_a_record(galaxy, tmp_path):
    run(galaxy, tmp_path, [galaxy.records[THAILE], galaxy.records[HD_38179]])
    score(galaxy, tmp_path, [galaxy.records[THAILE], galaxy.records[HD_38179]])
    count = "SELECT station_count FROM v3_derived.system_search WHERE system_id64 = %s"
    assert (galaxy.one(count, THAILE)[0], galaxy.one(count, HD_38179)[0]) == (2, 0)

    # A carrier jumps from Thaile to HD 38179. Only HD 38179 is in the file.
    arrived = deepcopy(galaxy.records[HD_38179])
    arrived["stations"] = [carrier_from(galaxy.records[THAILE])]
    run(galaxy, tmp_path, [arrived], made_at=SEPTEMBER + timedelta(days=1))
    assert queue(galaxy)[THAILE] == ["stations"]

    report = score(galaxy, tmp_path, [arrived])
    assert (report.find_only, report.still_waiting) == (1, 0)
    assert (galaxy.one(count, THAILE)[0], galaxy.one(count, HD_38179)[0]) == (1, 1)
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
    report = score(galaxy, tmp_path, [galaxy.records[SOL], galaxy.records[HD_38179]])
    assert report.cannot_score == {SOL: "unregistered source run in canonical row"}
    assert set(queue(galaxy)) == {SOL} and report.still_waiting == 1
    assert galaxy.all("SELECT xmin::text, t::text FROM v3_derived.system_rating_vector t "
                      "WHERE system_id64 = %s", SOL) == before
    assert "(still waiting)" in "\n".join(report.lines())


def test_scoring_and_the_galaxy_update_never_run_at_the_same_time(galaxy, tmp_path):
    with psycopg.connect(galaxy.conninfo, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (galaxy_update.LOCK_KEY,))
        with pytest.raises(galaxy_update.UpdateError, match="did not start"):
            score(galaxy, tmp_path, [galaxy.records[HD_38179]])
    assert score(galaxy, tmp_path, [galaxy.records[HD_38179]]).left_alone == 1


def test_command_scores_and_prints_plain_results(galaxy, tmp_path, monkeypatch, capsys):
    _environment(monkeypatch, galaxy.conninfo)
    run(galaxy, tmp_path, galaxy.records.values())
    path = write_galaxy(tmp_path / "galaxy_1day.json.gz", galaxy.records.values())
    assert scores.main(["apply", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Scoring: galaxy_1day.json.gz" in out and "Systems given their scores:" in out
    assert out.rstrip().endswith("DONE") and queue(galaxy) == {}
    galaxy.connection.execute("DROP TABLE v3_state.rescore_queue")
    assert scores.main(["apply", str(path)]) == 2
    assert "REFUSED" in capsys.readouterr().out


# -- the command ------------------------------------------------------------


def test_command_prints_a_plain_verdict(galaxy, tmp_path, monkeypatch, capsys):
    _environment(monkeypatch, galaxy.conninfo)
    path = write_galaxy(tmp_path / "galaxy_1day.json.gz", galaxy.records.values())
    assert scores.main(["check", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Score check (read only): galaxy_1day.json.gz" in out
    assert out.rstrip().endswith("CHECKED: nothing was changed. The stored scores were reproduced exactly.")

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
    monkeypatch.setenv("PGPORT", "1")
    assert scores.main(["check", str(path)]) == galaxy_update.EXIT_NO_CONNECTION
    assert "Could not connect to the database" in capsys.readouterr().out
