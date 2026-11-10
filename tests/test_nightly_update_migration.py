"""Migration 025: the nightly jobs may update published scores and Find rows.

Runs on PostgreSQL 18 with every migration applied in production's order, so
the guards are the ones production has (including the purge-aware ones).

Needs RATINGS_V4_VALIDATION_DATABASE_URL, like the other Finder tests.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import pytest

os.environ.setdefault("CORS_ORIGINS", "https://nightly-update-test.invalid")

ROOT = Path(__file__).resolve().parents[1]
TABLES = ("system_rating_vector", "body_mechanics", "economy_opportunity",
          "system_search", "system_archetype")
ANNOUNCE = "SET LOCAL edfinder.nightly_update = 'on'"
SEEDED = 43  # a generated system that has score, search and archetype rows

TRIGGERS_SQL = """
SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid)
  FROM pg_trigger t
  JOIN pg_class c ON c.oid = t.tgrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE NOT t.tgisinternal AND n.nspname = 'v3_derived' AND c.relname = ANY(%s)
 ORDER BY 1, 2
"""
WHEN = (" WHEN ((current_setting('edfinder.nightly_update'::text, true) "
        "IS DISTINCT FROM 'on'::text))")


def triggers(connection) -> dict[tuple[str, str], str]:
    return {(table, name): definition
            for table, name, definition in connection.execute(TRIGGERS_SQL, (list(TABLES),))}


@pytest.fixture(scope="module")
def database():
    """A published generation as production has it today (migrations to 024),
    with the trigger definitions from before 025, then 025 applied."""
    from tests.helpers import finder_seed
    from tests.ratings_v4_pg_fixture import canonical_database

    with canonical_database() as opened:
        connection = opened[0]
        generation = finder_seed.build_finder_database(connection, systems=200, through=24)
        before = triggers(connection)
        finder_seed.apply_migration(connection, 25)
        yield connection, generation, before


def refused(connection, statement: str, params: tuple = ()) -> str:
    with pytest.raises(psycopg.errors.RaiseException) as caught:
        connection.execute(statement, params)
    return str(caught.value)


# ---------------------------------------------------------------------------
# What the migration changes
# ---------------------------------------------------------------------------
@pytest.mark.db
def test_every_write_guard_gains_the_condition_and_nothing_else_changes(database):
    connection, _, before = database
    after = triggers(connection)

    assert len(before) == 23  # 5 + 5 + 5 + 4 + 4, as on production
    assert set(after) == set(before)
    for key, was in before.items():
        if "TRUNCATE" in was:
            assert after[key] == was, key
        else:
            assert WHEN not in was
            assert after[key] == was.replace(
                " FOR EACH STATEMENT EXECUTE FUNCTION ",
                " FOR EACH STATEMENT" + WHEN + " EXECUTE FUNCTION "), key
    assert sum("TRUNCATE" in definition for definition in after.values()) == 5
    assert sum(WHEN in definition for definition in after.values()) == 18


@pytest.mark.db
def test_the_read_only_report_shows_which_guards_were_changed(database):
    connection, _, _ = database
    report = (ROOT / "sql/measure/nightly_update_guards.sql").read_text(encoding="utf-8")
    statement = " ".join(line for line in report.splitlines() if not line.startswith("--"))
    assert statement.lstrip().startswith("SELECT ") and statement.count(";") == 1
    # No writing keyword as a word of its own (the name nightly_update is fine).
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE\s+\w)\b",
                         statement, re.IGNORECASE)
    rows = connection.execute(report).fetchall()
    assert len(rows) == 23
    answers = [answer for _, _, answer in rows]
    assert answers.count("yes") == 18 and answers.count("never (TRUNCATE)") == 5


@pytest.mark.db
def test_running_the_migration_again_changes_nothing(database):
    from tests.helpers import finder_seed

    connection, _, _ = database
    once = triggers(connection)
    oids = connection.execute(
        "SELECT array_agg(t.oid ORDER BY t.oid) FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid WHERE c.relname = ANY(%s)", (list(TABLES),)
    ).fetchone()[0]
    finder_seed.apply_migration(connection, 25)
    assert triggers(connection) == once
    assert connection.execute(
        "SELECT array_agg(t.oid ORDER BY t.oid) FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid WHERE c.relname = ANY(%s)", (list(TABLES),)
    ).fetchone()[0] == oids  # not even dropped and recreated


# ---------------------------------------------------------------------------
# Published rows are still protected
# ---------------------------------------------------------------------------
@pytest.mark.db
@pytest.mark.parametrize("statement, message", [
    ("UPDATE v3_derived.system_rating_vector SET loaded_body_count = 9 WHERE system_id64 = %s",
     "is immutable in state PUBLISHED"),
    ("DELETE FROM v3_derived.system_rating_vector WHERE system_id64 = %s",
     "payload DELETE requires governed purge function context"),
    ("UPDATE v3_derived.system_search SET name = 'changed' WHERE system_id64 = %s",
     "system_search rows and receipts are insert-only"),
    ("DELETE FROM v3_derived.system_search WHERE system_id64 = %s",
     "payload DELETE requires governed purge function context"),
    ("UPDATE v3_derived.system_archetype SET weighted_potential = 1 WHERE system_id64 = %s",
     "system_archetype rows and receipts are insert-only"),
    ("DELETE FROM v3_derived.system_archetype WHERE system_id64 = %s",
     "system_archetype rows and receipts are insert-only"),
])
def test_a_write_without_the_announcement_is_refused_as_before(database, statement, message):
    connection, _, _ = database
    assert message in refused(connection, statement, (SEEDED,))


@pytest.mark.db
def test_an_insert_without_the_announcement_is_refused_as_before(database):
    connection, _, _ = database
    for table in ("system_rating_vector", "system_search", "system_archetype"):
        columns = [row[0] for row in connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'v3_derived' AND table_name = %s ORDER BY ordinal_position",
            (table,))]
        copied = ", ".join("999999" if column == "system_id64" else column for column in columns)
        refused(connection,
                f"INSERT INTO v3_derived.{table} ({', '.join(columns)}) "
                f"SELECT {copied} FROM v3_derived.{table} WHERE system_id64 = %s", (SEEDED,))
    for table in TABLES:
        assert connection.execute(
            f"SELECT count(*) FROM v3_derived.{table} WHERE system_id64 = 999999").fetchone()[0] == 0


@pytest.mark.db
@pytest.mark.parametrize("table", TABLES)
def test_truncate_is_refused_even_for_the_nightly_update(database, table):
    connection, _, _ = database
    with pytest.raises(psycopg.errors.RaiseException):
        with connection.transaction():
            connection.execute(ANNOUNCE)
            connection.execute(f"TRUNCATE v3_derived.{table} CASCADE")
    assert connection.execute(
        "SELECT count(*) FROM v3_derived.system_search").fetchone()[0] == 200


# ---------------------------------------------------------------------------
# What the nightly update can now do
# ---------------------------------------------------------------------------
def real_rows(generation: str):
    """Score rows for the twelve real fixture systems, made by the copied
    build code exactly as a build makes them."""
    from domain.ratings_v4_canonical import load_source_fixture
    from scripts.ratings_v4 import production_generation as build

    canonical, metadata, payloads = load_source_fixture(ROOT / "tests/fixtures/ratings_v4_sources")
    encoded, _checkpoint = build.encode_chunk(
        generation, 0, canonical, metadata, [payload["system"] for payload in payloads])
    return build, encoded


def insert_rows(connection, table: str, columns: tuple[str, ...], rows) -> None:
    from psycopg import sql

    statement = sql.SQL("INSERT INTO v3_derived.{} ({}) VALUES ({})").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() * len(columns)))
    with connection.cursor() as cursor:
        cursor.executemany(statement, rows)


@pytest.mark.db
def test_nightly_transaction_writes_real_score_rows_and_the_protection_returns(database):
    connection, generation, _ = database
    build, encoded = real_rows(generation)
    assert len(encoded["vectors"]) == 12 and encoded["bodies"] and encoded["opportunities"]

    with connection.transaction():
        connection.execute(ANNOUNCE)
        insert_rows(connection, "system_rating_vector", build.VECTOR_COLUMNS, encoded["vectors"])
        insert_rows(connection, "body_mechanics", build.BODY_COLUMNS, encoded["bodies"])
        insert_rows(connection, "economy_opportunity", build.OPPORTUNITY_COLUMNS,
                    encoded["opportunities"])

    system = encoded["vectors"][0][1]
    counts = {table: connection.execute(
        f"SELECT count(*) FROM v3_derived.{table} WHERE system_id64 = %s", (system,)).fetchone()[0]
        for table in ("system_rating_vector", "body_mechanics", "economy_opportunity")}
    assert counts["system_rating_vector"] == 1
    assert counts["body_mechanics"] > 0 and counts["economy_opportunity"] > 0

    # The announcement ended with its transaction: these rows are protected now.
    for table in counts:
        assert "is immutable in state PUBLISHED" in refused(
            connection,
            f"UPDATE v3_derived.{table} SET system_id64 = system_id64 WHERE system_id64 = %s",
            (system,))
        assert "requires governed purge function context" in refused(
            connection, f"DELETE FROM v3_derived.{table} WHERE system_id64 = %s", (system,))

    # The nightly job replaces a system's rows: children first, then the vector.
    with connection.transaction():
        connection.execute(ANNOUNCE)
        for table in ("economy_opportunity", "body_mechanics", "system_rating_vector"):
            connection.execute(
                f"DELETE FROM v3_derived.{table} WHERE system_id64 = %s", (system,))
        insert_rows(connection, "system_rating_vector", build.VECTOR_COLUMNS,
                    [row for row in encoded["vectors"] if row[1] == system])
    assert connection.execute(
        "SELECT count(*) FROM v3_derived.body_mechanics WHERE system_id64 = %s",
        (system,)).fetchone()[0] == 0


@pytest.mark.db
@pytest.mark.parametrize("table, column, value", [
    ("system_search", "name", "Renamed by the nightly update"),
    ("system_archetype", "weighted_potential", 12.5),
])
def test_nightly_transaction_replaces_a_find_row(database, table, column, value):
    connection, _, _ = database
    with connection.transaction():
        connection.execute(ANNOUNCE)
        connection.execute(
            f"CREATE TEMP TABLE kept ON COMMIT DROP AS "
            f"SELECT * FROM v3_derived.{table} WHERE system_id64 = %s", (SEEDED,))
        connection.execute(f"UPDATE kept SET {column} = %s", (value,))
        connection.execute(f"DELETE FROM v3_derived.{table} WHERE system_id64 = %s", (SEEDED,))
        connection.execute(f"INSERT INTO v3_derived.{table} SELECT * FROM kept")
    assert connection.execute(
        f"SELECT {column} FROM v3_derived.{table} WHERE system_id64 = %s",
        (SEEDED,)).fetchone()[0] == value
    refused(connection, f"UPDATE v3_derived.{table} SET {column} = {column} "
                        f"WHERE system_id64 = %s", (SEEDED,))


@pytest.mark.db
def test_a_rolled_back_nightly_transaction_leaves_nothing_and_no_announcement(database):
    connection, _, _ = database
    before = connection.execute(
        "SELECT name FROM v3_derived.system_search WHERE system_id64 = 44").fetchone()[0]
    with pytest.raises(RuntimeError, match="stop"):
        with connection.transaction():
            connection.execute(ANNOUNCE)
            connection.execute(
                "DELETE FROM v3_derived.system_search WHERE system_id64 = 44")
            raise RuntimeError("stop")
    assert connection.execute(
        "SELECT name FROM v3_derived.system_search WHERE system_id64 = 44").fetchone()[0] == before
    assert connection.execute(
        "SELECT current_setting('edfinder.nightly_update', true)").fetchone()[0] in (None, "")
    refused(connection, "DELETE FROM v3_derived.system_search WHERE system_id64 = 44")


@pytest.mark.db
def test_find_still_reads_through_the_published_views(database):
    connection, _, _ = database
    assert connection.execute("SELECT count(*) FROM v3_app.system_search").fetchone()[0] == 200
    assert connection.execute("SELECT count(*) FROM v3_app.system_archetype").fetchone()[0] == 200
