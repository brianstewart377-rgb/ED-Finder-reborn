"""The Pi's clean-up settings for its big tables (deploy/pi/cleanup-settings.sql).

Runs on PostgreSQL 18 against a galaxy with every migration applied; needs
RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import finder_seed
from tests.ratings_v4_pg_fixture import canonical_database

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_FILE = ROOT / "deploy" / "pi" / "cleanup-settings.sql"
WANTED = {
    "autovacuum_enabled": "on",
    "autovacuum_vacuum_scale_factor": "0",
    "autovacuum_vacuum_threshold": "1000000",
    "autovacuum_vacuum_insert_scale_factor": "0",
    "autovacuum_vacuum_insert_threshold": "1000000",
    "autovacuum_analyze_scale_factor": "0",
    "autovacuum_analyze_threshold": "2000000",
}
GALAXY_TABLES = ("bodies", "body_composition", "systems", "rings", "body_signal_current",
                 "station_service_current")
SCORE_TABLES = ("economy_opportunity", "body_mechanics", "system_search", "system_rating_vector",
                "system_archetype")


@pytest.fixture(scope="module")
def database():
    with canonical_database() as (connection, canonical, _, _payloads):
        for name in finder_seed.migration_names():
            connection.execute((ROOT / "sql" / "v3" / "migrations" / name).read_text(encoding="utf-8"))
        yield connection, canonical["canonical_schema"]


def settings_of(connection, schema: str, table: str) -> dict[str, str]:
    row = connection.execute(
        "SELECT c.reloptions FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = %s", (schema, table)).fetchone()
    return dict(option.split("=", 1) for option in (row[0] or []))


def test_the_big_tables_are_cleaned_up_after_a_million_changed_rows(database):
    connection, galaxy = database
    small = settings_of(connection, "v3_state", "system_state")
    connection.execute(SETTINGS_FILE.read_text(encoding="utf-8"))
    for table in GALAXY_TABLES:
        assert settings_of(connection, galaxy, table) == WANTED, table
    for table in SCORE_TABLES:
        assert settings_of(connection, "v3_derived", table) == WANTED, table
    # Other tables keep what they had.
    assert settings_of(connection, "v3_state", "system_state") == small
    # Running it again changes nothing.
    connection.execute(SETTINGS_FILE.read_text(encoding="utf-8"))
    assert all(settings_of(connection, galaxy, table) == WANTED for table in GALAXY_TABLES)


def test_the_file_says_how_to_run_it_on_the_pi():
    text = SETTINGS_FILE.read_text(encoding="utf-8")
    assert ("docker exec -i edfinder-v3-phase4c-full-20260827_r5-postgres psql -X -v ON_ERROR_STOP=1 "
            "-U edfinder_v3 -d edfinder_v3_phase4c_full_20260827_r5 < /opt/edfinder/cleanup-settings.sql") in text
    assert "SET LOCAL lock_timeout" in text
