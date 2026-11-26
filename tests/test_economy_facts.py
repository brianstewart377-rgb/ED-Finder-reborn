"""Find's facts for each system within 500 ly (spansh_delta.economy_facts,
migrations 031 to 033). Ratings rebuild, build steps 4 and 5.

The galaxy is the twelve real fixture systems, as in tests/test_reach.py:
with Sol inhabited, eight of them are within 500 ly. The facts come from the
planner's own code (cpe/body_facts.py); these tests check that each stored
number sorts systems exactly as that code's orders do, that the nightly
scoring keeps the rows up to date, that the first fill can stop and carry on,
and that each row says where its system is, in coordinates and as a cell
(edfinder_api.find_cells), with the rows put in cell order on disk when a
fill finishes, and that each row carries the counts Find filters on, the
same as the system's search row has them.

Runs on PostgreSQL 18 with every migration applied; needs
RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from functools import cmp_to_key
import itertools
import os
from pathlib import Path
import sys

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://economy-facts-test.invalid")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "importer" / "src"))

from edfinder_api import find_cells  # noqa: E402
from edfinder_api.find_numbers import COUNT_COLUMNS, COUNT_MAX  # noqa: E402
from edfinder_api.ranking.profile import BODY_COUNT_COLUMNS  # noqa: E402
from edfinder_api.search_economies import BODY_FILTER_COLS  # noqa: E402
from edfinder_api.cpe.body_facts import (  # noqa: E402
    ECONOMIES,
    PAIRS,
    best_for_pair,
    pair_key,
    purest,
    purest_key,
    strongest,
    strongest_key,
)
from spansh_delta import economy_facts as facts  # noqa: E402
from spansh_delta import speed_trial  # noqa: E402
from tests.helpers import finder_seed  # noqa: E402
from tests.test_galaxy_scores import template  # noqa: E402,F401
from tests.test_galaxy_update import SEPTEMBER, SOL, fill_classes, run  # noqa: E402
from tests.test_reach import WITHIN_500_OF_SOL, inhabit, pi, waiting, work_out  # noqa: E402,F401


def rows(pi) -> dict[int, tuple]:
    return {row[0]: row[1:] for row in pi.all(
        "SELECT system_id64, bodies_known, ground_slots, strongest, purest, pairs FROM v3_state.economy_facts")}


def body_facts_of(pi, system_id64: int):
    """Each body's facts, worked out straight from the galaxy tables."""
    found = speed_trial.read(pi.connection, pi.schema, [system_id64])[system_id64]
    return speed_trial.facts_for(speed_trial.planner_inputs(found))


def filled(pi, tmp_path) -> facts.FactsReport:
    """Sol inhabited, reach worked out, every system written once, and the
    waiting list worked through: the first fill runs to its end."""
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    run(pi, tmp_path, pi.records.values())
    return waiting(pi).facts


# -- the migration --------------------------------------------------------------


def test_the_migrations_add_two_empty_tables_and_can_run_again(pi):
    assert facts.available(pi.connection)
    assert pi.one("SELECT count(*) FROM v3_state.economy_facts") == (0,)
    assert pi.one("SELECT count(*) FROM v3_state.economy_facts_fill") == (0,)
    finder_seed.apply_migration(pi.connection, 31)
    finder_seed.apply_migration(pi.connection, 32)
    finder_seed.apply_migration(pi.connection, 33)
    assert facts.status_lines(pi.connection) == ["Economy facts: no fill has started yet; 0 rows."]


def test_migration_032_empties_the_tables_once_only(pi, tmp_path):
    """The rows written before it have no coordinates, so it empties both
    tables the first time. Run again, it keeps what is there."""
    filled(pi, tmp_path)
    before = pi.all("SELECT system_id64, cell, worked_out_at FROM v3_state.economy_facts ORDER BY 1")
    fills = pi.all("SELECT fill_id, systems, finished_at FROM v3_state.economy_facts_fill")
    finder_seed.apply_migration(pi.connection, 32)
    assert pi.all("SELECT system_id64, cell, worked_out_at FROM v3_state.economy_facts ORDER BY 1") == before
    assert pi.all("SELECT fill_id, systems, finished_at FROM v3_state.economy_facts_fill") == fills
    assert len(before) == 8
    # As it finds a database that has 031 only: rows without coordinates.
    pi.connection.execute("DROP INDEX v3_state.economy_facts_cell_idx")
    pi.connection.execute(
        "ALTER TABLE v3_state.economy_facts DROP COLUMN cell, DROP COLUMN x_ly, DROP COLUMN y_ly, "
        "DROP COLUMN z_ly")
    pi.connection.execute("ALTER TABLE v3_state.economy_facts_fill DROP COLUMN tidied_at")
    assert not facts.available(pi.connection)
    finder_seed.apply_migration(pi.connection, 32)
    assert facts.available(pi.connection)
    assert pi.one("SELECT count(*) FROM v3_state.economy_facts") == (0,)
    assert pi.one("SELECT count(*) FROM v3_state.economy_facts_fill") == (0,)
    # The next scoring run starts the fill again, from the first system.
    report = facts.FactsReport()
    facts.carry_on(pi.connection, pi.schema, report, progress=False)
    assert set(rows(pi)) == WITHIN_500_OF_SOL and report.filled == 8
    assert pi.one("SELECT fill_id FROM v3_state.economy_facts_fill") == (1,)


# -- the counts Find filters on (migration 033) ------------------------------------------

COUNTS = ", ".join(COUNT_COLUMNS)
WITHOUT_COUNTS = "UPDATE v3_state.economy_facts SET " + ", ".join(f"{column} = NULL" for column in COUNT_COLUMNS)


def counts_in(pi, relation: str) -> dict[int, tuple]:
    return {row[0]: tuple(row[1:]) for row in pi.all(f"SELECT system_id64, {COUNTS} FROM {relation}")}


def test_the_counts_are_the_ones_finds_filters_use():
    assert len(COUNT_COLUMNS) == len(set(COUNT_COLUMNS)) == 19
    assert set(COUNT_COLUMNS) == set(BODY_FILTER_COLS.values()) == set(BODY_COUNT_COLUMNS)


def test_each_row_carries_the_counts_as_its_systems_search_row_has_them(pi, tmp_path):
    filled(pi, tmp_path)
    stored, shown = counts_in(pi, "v3_state.economy_facts"), counts_in(pi, "v3_app.system_search")
    assert set(stored) == WITHIN_500_OF_SOL
    for system_id64, counts in stored.items():
        assert counts == shown[system_id64], system_id64
    # The real systems differ, and most of the counts are in use among them.
    assert len(set(stored.values())) == 8
    in_use = [column for position, column in enumerate(COUNT_COLUMNS)
              if any(counts[position] for counts in stored.values())]
    assert len(in_use) >= 12, in_use
    # Both tables that carry the counts have them as columns of two bytes.
    for table in ("economy_facts", "economy_top"):
        kinds = dict(pi.all(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'v3_state' AND table_name = %s", table))
        assert {kinds[column] for column in COUNT_COLUMNS} == {"smallint"}, table


def test_a_changed_systems_counts_follow_its_search_row_the_same_night(pi, tmp_path):
    filled(pi, tmp_path)
    before = counts_in(pi, "v3_state.economy_facts")[SOL]
    without = deepcopy(pi.records[SOL])
    ringed = next(body for body in without["bodies"] if body.get("rings") and body.get("type") == "Planet")
    without["bodies"].remove(ringed)
    run(pi, tmp_path, [without], made_at=SEPTEMBER + timedelta(days=1))
    assert waiting(pi).facts.changed == 1
    after = counts_in(pi, "v3_state.economy_facts")[SOL]
    assert after == counts_in(pi, "v3_app.system_search")[SOL] and after != before
    rings = COUNT_COLUMNS.index("ring_count")
    assert after[rings] < before[rings]


def test_a_count_too_big_for_the_row_is_stored_as_the_largest_that_fits(pi, tmp_path, monkeypatch):
    filled(pi, tmp_path)
    before = counts_in(pi, "v3_state.economy_facts")[SOL]
    # As the search table would answer for a system with 40,000 biological
    # signals (its rows cannot be changed by hand: they are insert-only).
    assert facts.COUNTS_SQL.count(", bio_signal_total,") == 1
    monkeypatch.setattr(facts, "COUNTS_SQL", facts.COUNTS_SQL.replace(
        ", bio_signal_total,", ", 40000 AS bio_signal_total,"))
    facts.changed(pi.connection, pi.schema, [SOL], facts.FactsReport())
    after = counts_in(pi, "v3_state.economy_facts")[SOL]
    signals = COUNT_COLUMNS.index("bio_signal_total")
    assert after[signals] == COUNT_MAX == 32767
    assert after[:signals] + after[signals + 1:] == before[:signals] + before[signals + 1:]


def test_a_system_with_no_search_row_has_a_row_without_counts(pi, tmp_path, monkeypatch):
    filled(pi, tmp_path)
    numbers = rows(pi)[SOL]
    # As the search table would answer if it had no row for Sol.
    monkeypatch.setattr(facts, "COUNTS_SQL", facts.COUNTS_SQL + f" AND system_id64 <> {SOL}")
    facts.changed(pi.connection, pi.schema, sorted(WITHIN_500_OF_SOL), facts.FactsReport())
    stored = counts_in(pi, "v3_state.economy_facts")
    assert stored[SOL] == (None,) * 19 and rows(pi)[SOL] == numbers
    assert all(None not in counts for system_id64, counts in stored.items() if system_id64 != SOL)


def test_rows_written_before_the_counts_get_them_from_a_new_fill_and_the_migration_touches_no_row(
        pi, tmp_path):
    """The Pi on 26 November 2026: a fill under way for the layout before this
    one (facts-1), whose rows have no counts. Migration 033 once copied the
    counts into such rows in one statement; that was too heavy for the Pi, and
    it now only adds the columns. The scoring starts a fill for this layout
    instead, and that writes every row again, with its counts."""
    filled(pi, tmp_path)
    counts = counts_in(pi, "v3_state.economy_facts")
    older = facts.VERSION.rsplit("/", 1)[0] + "/facts-1"
    assert older != facts.VERSION
    pi.connection.execute(WITHOUT_COUNTS)
    pi.connection.execute("DELETE FROM v3_state.economy_top_build")
    pi.connection.execute(
        "UPDATE v3_state.economy_facts_fill SET version = %s, finished_at = NULL, tidied_at = NULL, "
        "systems = 3, last_system_id64 = %s", (older, sorted(WITHIN_500_OF_SOL)[2]))

    # The migration reads and writes no row, however often it is run.
    stored = "SELECT system_id64, xmin::text, ctid::text FROM v3_state.economy_facts ORDER BY 1"
    versions = pi.all(stored)
    finder_seed.apply_migration(pi.connection, 33)
    assert pi.all(stored) == versions
    assert set(counts_in(pi, "v3_state.economy_facts").values()) == {(None,) * 19}
    migration = (ROOT / "sql/v3/migrations/033_v3_economy_facts_counts_and_lists.sql").read_text()
    statements = " ".join(line for line in migration.splitlines() if not line.lstrip().startswith("--"))
    kinds = {" ".join(statement.split()[:2]) for statement in statements.split(";") if statement.strip()}
    assert kinds == {"BEGIN", "SET LOCAL", "ALTER TABLE", "COMMENT ON", "CREATE TABLE", "CREATE UNIQUE",
                     "COMMIT"}, kinds

    # The status says a new fill is coming; the next run starts it from the
    # beginning and carries it through.
    assert f"(this code runs {facts.VERSION}: a new fill starts with the next run)" in facts.status_lines(
        pi.connection)[0]
    report = facts.FactsReport()
    facts.carry_on(pi.connection, pi.schema, report, progress=False)
    fill = facts.latest_fill(pi.connection)
    assert (fill.version, fill.systems, fill.finished_at is not None) == (facts.VERSION, 8, True)
    assert (report.filled, report.fill_finished_now) == (8, True)
    assert counts_in(pi, "v3_state.economy_facts") == counts
    # The fill it replaced is left as it was, unfinished.
    assert pi.all("SELECT version, systems, finished_at FROM v3_state.economy_facts_fill ORDER BY fill_id") \
        == [(older, 3, None), (facts.VERSION, 8, fill.finished_at)]


def before_033(pi) -> None:
    """The database as it is with migrations 031 and 032 only."""
    pi.connection.execute("DROP TABLE v3_state.economy_top, v3_state.economy_top_build")
    pi.connection.execute("ALTER TABLE v3_state.economy_facts "
                          + ", ".join(f"DROP COLUMN {column}" for column in COUNT_COLUMNS))


def test_no_row_is_ever_written_without_its_counts(pi, tmp_path):
    """Without migration 033 the scoring keeps no facts at all, and says so.
    Once it is applied the fill starts. So a finished fill means every row
    was written with its counts, whichever of the build and the migration
    came first."""
    before_033(pi)
    assert not facts.available(pi.connection)
    report = filled(pi, tmp_path)
    assert report.skipped == "not kept in this database (migrations 031, 032 and 033 have not all been applied)"
    assert rows(pi) == {} and facts.latest_fill(pi.connection) is None
    assert facts.status_lines(pi.connection) == [
        "Economy facts are not kept in this database (migrations 028, 031, 032 and 033)."]

    finder_seed.apply_migration(pi.connection, 33)
    assert facts.available(pi.connection)
    after = waiting(pi).facts
    assert (after.skipped, after.filled, after.fill_finished_now) == (None, 8, True)
    assert counts_in(pi, "v3_state.economy_facts") == {
        system_id64: counts for system_id64, counts in counts_in(pi, "v3_app.system_search").items()
        if system_id64 in WITHIN_500_OF_SOL}
    assert "without" not in "\n".join(after.lines())


def test_the_list_tables_hold_one_whole_set_at_a_time(pi):
    """Migration 033's two tables for the search across the band."""
    import psycopg

    columns = [row[0] for row in pi.all(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'v3_state' "
        "AND table_name = 'economy_top' ORDER BY ordinal_position")]
    assert columns == ["build_id", "list_no", "place", "system_id64", "number", "bodies_known",
                       "ground_slots", *COUNT_COLUMNS]
    first = pi.one("INSERT INTO v3_state.economy_top_build (version, depth, finished_at, is_current) "
                   "VALUES ('test/facts-1', 3, now(), true) RETURNING build_id")[0]
    pi.connection.execute(
        "INSERT INTO v3_state.economy_top (build_id, list_no, place, system_id64, number, bodies_known, "
        "ground_slots) VALUES (%s, 36, 1, %s, 9, 4, 9)", (first, SOL))
    for wrong, why in (
            # A second set cannot be current beside the first.
            ("INSERT INTO v3_state.economy_top_build (version, depth, finished_at, is_current) "
             "VALUES ('test/facts-1', 3, now(), true)", psycopg.errors.UniqueViolation),
            # A set is not read before it is whole.
            ("INSERT INTO v3_state.economy_top_build (version, depth, is_current) "
             "VALUES ('test/facts-1', 3, true)", psycopg.errors.CheckViolation),
            # One system, one place in a list; and there are 36 lists.
            (f"INSERT INTO v3_state.economy_top (build_id, list_no, place, system_id64, number, bodies_known, "
             f"ground_slots) VALUES ({first}, 36, 2, {SOL}, 9, 4, 9)", psycopg.errors.UniqueViolation),
            (f"INSERT INTO v3_state.economy_top (build_id, list_no, place, system_id64, number, bodies_known, "
             f"ground_slots) VALUES ({first}, 37, 1, {SOL}, 9, 4, 9)", psycopg.errors.CheckViolation),
            # A system with nothing to be placed by is not in a list.
            (f"INSERT INTO v3_state.economy_top (build_id, list_no, place, system_id64, number, bodies_known, "
             f"ground_slots) VALUES ({first}, 1, 1, {SOL}, 0, 4, 9)", psycopg.errors.CheckViolation)):
        with pytest.raises(why):
            pi.connection.execute(wrong)
    # A set's rows go with it.
    pi.connection.execute("DELETE FROM v3_state.economy_top_build WHERE build_id = %s", (first,))
    assert pi.one("SELECT count(*) FROM v3_state.economy_top") == (0,)


# -- where each system is ----------------------------------------------------------


def test_each_row_says_where_its_system_is_and_the_cell_is_the_one_python_works_out(pi, tmp_path):
    filled(pi, tmp_path)
    stored = pi.all("SELECT system_id64, x_ly, y_ly, z_ly, cell FROM v3_state.economy_facts")
    assert len(stored) == 8
    for system_id64, x_ly, y_ly, z_ly, cell in stored:
        coords = pi.records[system_id64]["coords"]
        assert (x_ly, y_ly, z_ly) == (coords["x"], coords["y"], coords["z"])
        assert cell == find_cells.cell_of(x_ly, y_ly, z_ly)
    # Sol is at the corner of a cell; systems on both sides of zero are here.
    assert find_cells.cell_of(0.0, 0.0, 0.0) == (4096 << 26) | (4096 << 13) | 4096
    assert any(x_ly < 0 for _, x_ly, _, _, _ in stored) and any(z_ly < 0 for _, _, _, z_ly, _ in stored)
    assert len({cell for *_, cell in stored}) > 1


@pytest.mark.parametrize("x_ly, y_ly, z_ly", [
    (0.0, 0.0, 0.0), (-0.03125, 31.96875, 32.0), (-32.0, -32.03125, 63.96875),
    (-131072.0, 131071.96875, 65279.0), (25.21875, -20.90625, 25899.96875),
    (-1111.5625, -134.21875, 65269.75),
])
def test_python_and_the_database_put_a_point_in_the_same_cell(pi, x_ly, y_ly, z_ly):
    """The column's own expression, on points at and beside cell edges."""
    expression = pi.one(
        "SELECT pg_get_expr(d.adbin, d.adrelid) FROM pg_attrdef d JOIN pg_attribute a "
        "ON a.attrelid = d.adrelid AND a.attnum = d.adnum "
        "WHERE d.adrelid = 'v3_state.economy_facts'::regclass AND a.attname = 'cell'")[0]
    in_database = pi.one(
        f"SELECT {expression} FROM (SELECT %s::float8 AS x_ly, %s::float8 AS y_ly, %s::float8 AS z_ly) point",
        x_ly, y_ly, z_ly)[0]
    assert in_database == find_cells.cell_of(x_ly, y_ly, z_ly)


def test_a_point_outside_the_cells_is_refused_by_python_and_by_the_table(pi):
    for point in ((131072.0, 0.0, 0.0), (0.0, -131072.03125, 0.0), (0.0, 0.0, float("nan"))):
        with pytest.raises(ValueError, match="a coordinate must be from"):
            find_cells.cell_of(*point)
    with pytest.raises(Exception, match="economy_facts_x_ly_check"):
        pi.connection.execute(
            "INSERT INTO v3_state.economy_facts (system_id64, bodies_known, ground_slots, worked_out_at, "
            "x_ly, y_ly, z_ly) VALUES (1, 0, 0, now(), 131072, 0, 0)")


def test_when_a_fill_finishes_the_rows_are_put_in_cell_order_on_disk(pi, tmp_path):
    report = filled(pi, tmp_path)
    fill = facts.latest_fill(pi.connection)
    assert fill.finished_at is not None and fill.tidied_at is not None
    assert report.tidy_seconds is not None and report.tidy_failed is None
    assert "The rows were put in order on disk for searching near a system" in "\n".join(report.lines())
    on_disk = [row[0] for row in pi.all("SELECT cell FROM v3_state.economy_facts ORDER BY ctid")]
    assert on_disk == sorted(on_disk) and len(on_disk) == 8
    assert ", rows in order on disk since " in facts.status_lines(pi.connection)[0]
    # It is done once for a fill: the next run leaves the table alone.
    again = waiting(pi).facts
    assert again.tidy_seconds is None and facts.latest_fill(pi.connection).tidied_at == fill.tidied_at


def test_a_tidy_that_fails_is_reported_and_tried_again_the_next_run(pi, tmp_path):
    fill_classes(pi.connection, pi.schema, pi.records.values())
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    pi.connection.execute("ALTER INDEX v3_state.economy_facts_cell_idx RENAME TO put_aside")
    pi.connection.execute("SET statement_timeout = '7min'")
    report = facts.FactsReport()
    facts.carry_on(pi.connection, pi.schema, report, progress=False)
    fill = facts.latest_fill(pi.connection)
    assert fill.finished_at is not None and fill.tidied_at is None and report.tidy_seconds is None
    assert "economy_facts_cell_idx" in report.tidy_failed
    said = "\n".join(report.lines())
    assert "The rows could NOT be put in order on disk" in said and "the next run tries again" in said
    assert ", rows not yet in order on disk" in facts.status_lines(pi.connection)[0]
    assert report.as_dict()["tidy_failed"] == report.tidy_failed
    # The statement time limit is as it was before the attempt.
    assert pi.one("SHOW statement_timeout") == ("7min",)
    pi.connection.execute("ALTER INDEX v3_state.put_aside RENAME TO economy_facts_cell_idx")
    report = facts.FactsReport()
    facts.carry_on(pi.connection, pi.schema, report, progress=False)
    assert facts.latest_fill(pi.connection).tidied_at is not None and report.tidy_failed is None


def test_a_system_the_galaxy_tables_do_not_place_is_reported_and_gets_no_row(pi, tmp_path, monkeypatch):
    fill_classes(pi.connection, pi.schema, pi.records.values())
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    monkeypatch.setattr(facts, "POSITIONS_SQL", facts.POSITIONS_SQL + f" AND id64 <> {SOL}")
    report = facts.FactsReport()
    facts.changed(pi.connection, pi.schema, sorted(WITHIN_500_OF_SOL), report)
    assert report.failed == {SOL: "LookupError: the galaxy tables do not say where it is"}
    assert set(rows(pi)) == WITHIN_500_OF_SOL - {SOL} and report.changed == 7


# -- the numbers sort as the planner's orders do ------------------------------------


def test_each_number_sorts_bodies_exactly_as_its_order_does(pi):
    """For every pair of bodies in the twelve real systems, the packed numbers
    compare the way the planner's order keys do."""
    fill_classes(pi.connection, pi.schema, pi.records.values())
    compared = 0
    for system_id64 in pi.records:
        bodies = body_facts_of(pi, system_id64)
        for economy in ECONOMIES:
            able = [body for body in bodies if body.adds[economy] > 0]
            for key, steps in ((strongest_key, facts.STRONGEST_STEPS), (purest_key, facts.PUREST_STEPS)):
                for one, two in itertools.combinations(able, 2):
                    first, second = key(one, economy), key(two, economy)
                    packed = (facts.pack(first, steps), facts.pack(second, steps))
                    assert (first > second) == (packed[0] > packed[1])
                    assert (first == second) == (packed[0] == packed[1])
                    compared += 1
        for left, right in PAIRS:
            able = [body for body in bodies if body.adds[left] > 0 and body.adds[right] > 0]
            for one, two in itertools.combinations(able, 2):
                first, second = pair_key(one, left, right), pair_key(two, left, right)
                packed = (facts.pack(first, facts.PAIR_STEPS), facts.pack(second, facts.PAIR_STEPS))
                assert (first > second) == (packed[0] > packed[1])
                assert (first == second) == (packed[0] == packed[1])
                compared += 1
    assert compared > 10_000


def test_a_number_is_above_zero_and_refuses_what_it_cannot_hold():
    steps = ((10, 0, 4), (1, 15, 4))
    assert facts.pack((0.0, 0), steps) == 1 << 8 | 15
    assert facts.pack((0.3, -2), steps) > facts.pack((0.2, 0), steps)
    with pytest.raises(facts.PackError, match="does not fit"):
        facts.pack((0.15, 0), steps)
    with pytest.raises(facts.PackError, match="does not fit"):
        facts.pack((1.6, 0), steps)
    with pytest.raises(facts.PackError, match="does not fit"):
        facts.pack((0.1, -16), steps)
    with pytest.raises(facts.PackError, match="steps where the layout has"):
        facts.pack((0.1,), steps)


# -- the first fill and the rows it writes -----------------------------------------


def test_the_first_fill_gives_every_system_in_reach_its_row(pi, tmp_path):
    report = filled(pi, tmp_path)
    found = rows(pi)
    assert set(found) == WITHIN_500_OF_SOL
    for system_id64, row in found.items():
        expected = facts.system_facts(
            system_id64, speed_trial.read(pi.connection, pi.schema, [system_id64])[system_id64])
        assert row == (expected.bodies_known, expected.ground_slots, list(expected.strongest),
                       list(expected.purest), list(expected.pairs))
    # Sol: its stars, planets and moons, not its belt clusters or barycentres.
    record = pi.records[SOL]
    counted = sum(1 for body in record["bodies"] if body.get("type") in ("Star", "Planet"))
    assert found[SOL][0] == counted
    fill = facts.latest_fill(pi.connection)
    assert (fill.version, fill.systems, fill.finished_at is not None) == (facts.VERSION, 8, True)
    # Seven of them changed in the galaxy tables; the eighth only gained or
    # lost a station. The fill then worked all eight out.
    assert (report.changed, report.filled, report.failed, report.fill_finished_now) == (7, 8, {}, True)
    text = "\n".join(report.lines())
    assert "The first fill finished: 8 systems." in text
    assert report.as_dict()["fill_finished"] is True
    assert facts.status_lines(pi.connection)[0].startswith(f"Economy facts: the fill for {facts.VERSION} started ")


def test_sorting_systems_by_a_stored_number_follows_the_order_of_their_best_bodies(pi, tmp_path):
    filled(pi, tmp_path)
    stored = rows(pi)
    for position, economy in enumerate(ECONOMIES):
        for column, choose, key in ((2, strongest, strongest_key), (3, purest, purest_key)):
            best = {system_id64: choose(body_facts_of(pi, system_id64), economy) for system_id64 in stored}
            able = [system_id64 for system_id64, body in best.items() if body is not None]
            assert {system_id64 for system_id64 in stored if stored[system_id64][column][position] > 0} \
                == set(able)

            def by_key(one, two, key=key, best=best, economy=economy):
                first, second = key(best[one], economy), key(best[two], economy)
                return (first > second) - (first < second)

            def by_number(one, two, column=column, position=position):
                first, second = stored[one][column][position], stored[two][column][position]
                return (first > second) - (first < second)

            for one, two in itertools.combinations(able, 2):
                assert by_key(one, two) == by_number(one, two)
            assert sorted(able, key=cmp_to_key(by_key)) == sorted(able, key=cmp_to_key(by_number))
    for position, (left, right) in enumerate(PAIRS):
        best = {system_id64: best_for_pair(body_facts_of(pi, system_id64), left, right)
                for system_id64 in stored}
        for one, two in itertools.combinations([s for s, body in best.items() if body is not None], 2):
            first, second = pair_key(best[one], left, right), pair_key(best[two], left, right)
            numbers = stored[one][4][position], stored[two][4][position]
            assert (first > second) == (numbers[0] > numbers[1])


def test_the_first_fill_carries_on_where_it_stopped(pi, tmp_path, monkeypatch):
    fill_classes(pi.connection, pi.schema, pi.records.values())
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    monkeypatch.setattr(facts, "BATCH_SYSTEMS", 3)
    # A clock that allows one batch and then says the time is up.
    ticks = iter([0, 0, 0])
    monkeypatch.setattr(facts, "_now", lambda: next(ticks, 10_000))
    report = facts.FactsReport()
    facts.carry_on(pi.connection, pi.schema, report, minutes=1, progress=False)
    monkeypatch.undo()
    fill = facts.latest_fill(pi.connection)
    first_three = sorted(WITHIN_500_OF_SOL)[:3]
    assert (fill.systems, fill.last_system_id64, fill.finished_at) == (3, first_three[-1], None)
    assert set(rows(pi)) == set(first_three) and report.filled == 3
    assert "The first fill has done 3 of 8 systems within 500 ly" in "\n".join(report.lines())
    # The next run carries on from there, and finishes.
    report = facts.FactsReport()
    facts.carry_on(pi.connection, pi.schema, report, progress=False)
    fill = facts.latest_fill(pi.connection)
    assert (fill.systems, fill.finished_at is not None, report.filled) == (8, True, 5)
    assert set(rows(pi)) == WITHIN_500_OF_SOL


def test_the_fill_says_how_far_it_has_got_every_ten_minutes(pi, monkeypatch, capsys):
    fill_classes(pi.connection, pi.schema, pi.records.values())
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    monkeypatch.setattr(facts, "BATCH_SYSTEMS", 2)
    # Each look at the clock is 4 minutes on: four batches take 8 looks.
    clock = iter(range(0, 100_000, 240))
    monkeypatch.setattr(facts, "_now", lambda: next(clock))
    facts.carry_on(pi.connection, pi.schema, facts.FactsReport(), minutes=10_000)
    said = [line for line in capsys.readouterr().out.splitlines() if "the fill has done" in line]
    assert facts.FILL_PROGRESS_SECONDS == 600
    assert 1 <= len(said) < 4 and said[0].endswith("systems")


def test_a_changed_system_gets_its_new_row_the_same_night(pi, tmp_path):
    filled(pi, tmp_path)
    before = rows(pi)[SOL]
    stamp = pi.one("SELECT worked_out_at FROM v3_state.economy_facts WHERE system_id64 = %s", SOL)[0]
    without = deepcopy(pi.records[SOL])
    ringed = next(body for body in without["bodies"] if body.get("rings") and body.get("type") == "Planet")
    without["bodies"].remove(ringed)
    run(pi, tmp_path, [without], made_at=SEPTEMBER + timedelta(days=1))
    report = waiting(pi).facts
    assert report.changed == 1 and report.came_within_reach == 0 and report.filled == 0
    after = rows(pi)[SOL]
    assert after[0] == before[0] - 1
    assert pi.one("SELECT worked_out_at FROM v3_state.economy_facts WHERE system_id64 = %s", SOL)[0] > stamp


def test_a_system_that_comes_within_reach_gets_its_row(pi, tmp_path):
    filled(pi, tmp_path)
    pi.connection.execute("DELETE FROM v3_state.economy_facts WHERE system_id64 = %s", (SOL,))
    report = waiting(pi).facts
    assert report.came_within_reach == 1 and SOL in rows(pi)


def test_a_new_version_starts_a_new_fill_and_rows_out_of_reach_go_when_it_ends(pi, tmp_path, monkeypatch):
    filled(pi, tmp_path)
    # A row for a system that is no longer within 500 ly.
    outside = next(system_id64 for system_id64 in pi.records if system_id64 not in WITHIN_500_OF_SOL)
    pi.connection.execute(
        "INSERT INTO v3_state.economy_facts (system_id64, bodies_known, ground_slots, worked_out_at, "
        "x_ly, y_ly, z_ly) VALUES (%s, 0, 0, now(), 0, 0, 0)", (outside,))
    later = "a later rulebook/" + facts.VERSION.rsplit("/", 1)[1]
    monkeypatch.setattr(facts, "VERSION", later)
    assert "a new fill starts with the next run" in facts.status_lines(pi.connection)[0]
    report = facts.FactsReport()
    facts.carry_on(pi.connection, pi.schema, report, progress=False)
    fill = facts.latest_fill(pi.connection)
    assert (fill.version, fill.systems, fill.finished_at is not None) == (later, 8, True)
    assert report.deleted_outside_reach == 1 and set(rows(pi)) == WITHIN_500_OF_SOL
    assert pi.one("SELECT count(*) FROM v3_state.economy_facts_fill") == (2,)


def test_a_system_that_cannot_be_worked_out_keeps_its_row_and_the_report_says_why(pi, tmp_path, monkeypatch):
    filled(pi, tmp_path)
    before = rows(pi)

    def refuse(system_id64, found):
        raise facts.PackError("does not fit")

    monkeypatch.setattr(facts, "system_facts", refuse)
    report = facts.FactsReport()
    facts.changed(pi.connection, pi.schema, [SOL], report)
    assert report.failed == {SOL: "PackError: does not fit"} and report.changed == 0
    assert rows(pi) == before
    assert f"for example system {SOL}: PackError: does not fit" in "\n".join(report.lines())


def test_without_the_migrations_the_scoring_says_so_and_goes_on(pi, tmp_path):
    pi.connection.execute("DROP TABLE v3_state.economy_facts, v3_state.economy_facts_fill")
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    run(pi, tmp_path, pi.records.values())
    report = waiting(pi)
    assert report.facts.skipped == (
        "not kept in this database (migrations 031, 032 and 033 have not all been applied)")
    assert "Economy facts: not kept in this database" in "\n".join(report.lines())
    assert report.added + report.changed + report.same == 7
