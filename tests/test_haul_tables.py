"""Haul's tables (migration 034). "Haul — design", step 1.

The migration only creates tables: no statement in it reads or writes a row.
It creates the eight tables the owner agreed on 28 November, in the agreed
schemas, and can run again. The private ones are in the two schemas the
nightly small backup takes. The market tables hold a real station's market,
taken from the twelve real Spansh records, and a station's goods go with it.
A plugin key is kept only as a 32-byte SHA-256.

Runs on PostgreSQL 18 with every migration applied; needs
RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

os.environ.setdefault("CORS_ORIGINS", "https://haul-tables-test.invalid")

from tests.helpers import finder_seed  # noqa: E402
from tests.test_galaxy_scores import template  # noqa: E402,F401
from tests.test_reach import pi  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "v3" / "migrations" / "034_v3_haul_tables.sql"

TABLES = {
    ("v3_vocab", "commodity"),
    ("v3_identity", "plugin_key"),
    ("v3_private", "haul_site_reading"),
    ("v3_private", "haul_carrier"),
    ("v3_private", "haul_carrier_stock"),
    ("v3_state", "haul_market_fill"),
    ("v3_state", "haul_market_station"),
    ("v3_state", "haul_market"),
}


def code_of(text: str) -> str:
    """The SQL without its comments (no string in this file holds '--')."""
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def haul_tables(pi) -> set[tuple[str, str]]:
    return set(pi.all(
        "SELECT schemaname, tablename FROM pg_tables "
        "WHERE tablename LIKE 'haul\\_%%' OR (schemaname, tablename) IN "
        "(('v3_vocab', 'commodity'), ('v3_identity', 'plugin_key'))"))


# -- the file -----------------------------------------------------------------------


def test_the_migration_only_creates_tables():
    text = MIGRATION.read_text(encoding="utf-8")
    assert text.lstrip().startswith("--")
    assert "\nBEGIN;\n" in text and text.rstrip().endswith("COMMIT;")
    assert "SET LOCAL lock_timeout = '5s';" in text
    assert b"\r\n" not in MIGRATION.read_bytes()
    statements = [" ".join(part.split()) for part in code_of(text).split(";") if part.strip()]
    allowed = ("BEGIN", "COMMIT", "SET LOCAL lock_timeout", "CREATE TABLE IF NOT EXISTS ",
               "CREATE INDEX IF NOT EXISTS ", "COMMENT ON TABLE ")
    for statement in statements:
        assert statement.startswith(allowed), statement
    # "ON DELETE CASCADE" says what happens later, when a row goes; it is not
    # a statement that removes anything now.
    code = code_of(text).replace("ON DELETE CASCADE", "")
    for word in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "DROP", "ALTER", "COPY", "SELECT"):
        assert not re.search(r"\b%s\b" % word, code), word
    created = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)\.(\w+)", text)
    assert set(created) == TABLES and len(created) == len(TABLES)


def test_the_private_tables_are_in_the_schemas_the_small_backup_takes():
    backup = (ROOT / "deploy" / "backup.py").read_text(encoding="utf-8")
    assert '"EDFINDER_BACKUP_SCHEMAS": "v3_identity v3_private"' in backup
    private = {schema for schema, table in TABLES
               if table in {"plugin_key", "haul_site_reading", "haul_carrier", "haul_carrier_stock"}}
    assert private == {"v3_identity", "v3_private"}


# -- in the database ------------------------------------------------------------------


def test_the_migration_makes_eight_empty_tables_and_can_run_again(pi):
    assert haul_tables(pi) == TABLES
    finder_seed.apply_migration(pi.connection, 34)
    assert haul_tables(pi) == TABLES
    for schema, table in sorted(TABLES):
        assert pi.one("SELECT count(*) FROM %s.%s" % (schema, table)) == (0,), table


def a_real_station(pi) -> tuple[int, dict]:
    """The first fixed station with a market and landing pads in the twelve
    real Spansh records, with its system."""
    for id64, record in sorted(pi.records.items()):
        for station in record.get("stations") or []:
            carrier = "Carrier" in (station.get("type") or "")
            market = station.get("market") or {}
            if not carrier and station.get("landingPads") and any(
                    (good.get("supply") or 0) > 0 for good in market.get("commodities") or []):
                return id64, station
    raise AssertionError("no fixed station with a market in the fixture")


def test_a_real_market_fits_and_goes_with_its_station(pi):
    id64, station = a_real_station(pi)
    goods = [good for good in station["market"]["commodities"] if (good.get("supply") or 0) > 0]
    pads = station["landingPads"]
    largest = "L" if pads.get("large") else "M" if pads.get("medium") else "S"
    connection = pi.connection
    for good in goods:
        connection.execute(
            "INSERT INTO v3_vocab.commodity (commodity_id, symbol, display_name, category) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (good["commodityId"], good["symbol"].lower(), good["name"], good["category"]))
    (fill_id,) = connection.execute(
        "INSERT INTO v3_state.haul_market_fill (file_modified_at) VALUES (now()) RETURNING fill_id").fetchone()
    connection.execute(
        "INSERT INTO v3_state.haul_market_station (market_id, system_id64, station_name, station_type, "
        "largest_pad, distance_to_arrival_ls, market_updated_at, seen_fill_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (station["id"], id64, station["name"], station.get("type"), largest,
         station.get("distanceToArrival"), station["market"]["updateTime"], fill_id))
    for good in goods:
        connection.execute(
            "INSERT INTO v3_state.haul_market (market_id, commodity_id, buy_price, supply) VALUES (%s, %s, %s, %s)",
            (station["id"], good["commodityId"], good["buyPrice"], good["supply"]))
    assert pi.one("SELECT count(*) FROM v3_state.haul_market WHERE market_id = %s", station["id"]) == (len(goods),)

    # A good with nothing in stock is never kept.
    with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
        connection.execute("UPDATE v3_state.haul_market SET supply = 0 WHERE market_id = %s", (station["id"],))
    # The game's symbols are kept in lower case.
    with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
        connection.execute("UPDATE v3_vocab.commodity SET symbol = upper(symbol)")

    # A station that has gone takes its goods with it.
    connection.execute("DELETE FROM v3_state.haul_market_station WHERE market_id = %s", (station["id"],))
    assert pi.one("SELECT count(*) FROM v3_state.haul_market") == (0,)


def test_a_plugin_key_is_kept_only_as_its_sha256(pi):
    connection = pi.connection
    account = uuid.uuid4()
    connection.execute("INSERT INTO v3_identity.account (account_id) VALUES (%s)", (account,))
    key = secrets.token_urlsafe(32)
    connection.execute(
        "INSERT INTO v3_identity.plugin_key (plugin_key_id, account_id, key_sha256, label) "
        "VALUES (%s, %s, %s, 'gaming PC')",
        (uuid.uuid4(), account, hashlib.sha256(key.encode()).digest()))
    with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
        connection.execute(
            "INSERT INTO v3_identity.plugin_key (plugin_key_id, account_id, key_sha256, label) "
            "VALUES (%s, %s, %s, 'the key itself')", (uuid.uuid4(), account, key.encode()))
    with pytest.raises(psycopg.errors.UniqueViolation), connection.transaction():
        connection.execute(
            "INSERT INTO v3_identity.plugin_key (plugin_key_id, account_id, key_sha256, label) "
            "VALUES (%s, %s, %s, 'twice')", (uuid.uuid4(), account, hashlib.sha256(key.encode()).digest()))
    assert pi.one("SELECT count(*) FROM v3_identity.plugin_key WHERE revoked_at IS NULL") == (1,)
