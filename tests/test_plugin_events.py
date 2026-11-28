"""Where ED-Finder's EDMC plugin sends a member's journal events
("Haul — design", step 2b): routers/plugin_events.py and
edfinder_api/haul/readings.py.

Through the route itself, rate limiter included, against real PostgreSQL 18
with every real migration. The plugin signs in with a key made by the real
code of step 2a. Each account's commander is linked as Frontier's sign-in
links one; the commanders are made up, as in tests/test_journal_upload.py,
because no player's journal belongs in this repo.

The journal lines are real. The two ``ColonisationConstructionDepot`` lists
and the deliveries are the ones EDDI publishes (tests/fixtures/haul). The
site is a real construction site from the twelve real Spansh records, picked
by its system's number: its ``Docked`` line is built from that record, and the
two real lists are put on its market number. The lists did not come from that
site; every fact in them is real.

What must hold: a site's reading is kept only when it differs from the one
before it, so the game writing the same list every few seconds keeps one row,
and sending a batch again changes nothing; the site is named from a ``Docked``
line, the plugin's last one or a later one; only a linked commander's journal
is taken, with a live key; kinds the plugin does not send are refused, the
ones later steps take are left for them; nothing is added up from deliveries;
and the address answers 503 until migration 034 has been applied.

Needs RATINGS_V4_VALIDATION_DATABASE_URL. CI sets EDF_DB_TESTS_REQUIRED=1 so
it cannot be skipped there.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

os.environ.setdefault("CORS_ORIGINS", "https://plugin-events-test.invalid")

from edfinder_api.config import limiter  # noqa: E402
from edfinder_api.deps import get_pool  # noqa: E402
from edfinder_api.haul import keys, readings  # noqa: E402
from edfinder_api.journal.commanders import ISSUER, associate_verified_commander  # noqa: E402
from edfinder_api.routers import plugin_events as events_route  # noqa: E402
from tests.test_journal_upload import the_apis_own_pool  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "tests" / "fixtures" / "haul" / "eddi-colonisation-samples.jsonl"
SPANSH = ROOT / "tests" / "fixtures" / "ratings_v4_sources" / "spansh-system-dumps.zip"
# A real system with a construction site among the twelve real Spansh records.
SITE_SYSTEM = 7780836610810
ADDRESS = "/api/v1/plugin/events"
MINE, THEIRS = uuid.uuid4(), uuid.uuid4()
# Each account's commander, made up for the test.
MY_FID, THEIR_FID = "F1000011", "F1000012"


def samples() -> list[dict]:
    return [json.loads(line) for line in SAMPLES.read_text(encoding="utf-8").splitlines()]


def depot_lists() -> list[dict]:
    return [event for event in samples() if event["event"] == readings.DEPOT]


def the_site() -> dict:
    """The real construction site's Spansh record, with its system's number and name."""
    with zipfile.ZipFile(SPANSH) as archive:
        for name in archive.namelist():
            if name.endswith(".json"):
                system = json.loads(archive.read(name))["system"]
                if system["id64"] == SITE_SYSTEM:
                    for station in system.get("stations") or []:
                        if "Construction" in (station.get("type") or ""):
                            return {**station, "system_id64": system["id64"], "system_name": system["name"]}
    raise AssertionError("the construction site is missing from the Spansh records")


def docked_at(site: dict, at: str) -> dict:
    """The journal's Docked line for the site, from its Spansh record."""
    return {"timestamp": at, "event": "Docked", "StationName": site["name"],
            "StationType": "".join(site["type"].split()), "StarSystem": site["system_name"],
            "SystemAddress": site["system_id64"], "MarketID": site["id"]}


def on_the_site(depot: dict, site: dict, at: str) -> dict:
    line = copy.deepcopy(depot)
    line["MarketID"], line["timestamp"] = site["id"], at
    return line


@pytest.fixture(scope="module")
def events_database():
    from tests import test_v3_derived_product_publication as publication
    from tests.helpers import finder_seed
    from tests.ratings_v4_pg_fixture import canonical_database

    with canonical_database() as database:
        connection = database[0]
        for name in finder_seed.migration_names():
            connection.execute((publication.MIGRATIONS / name).read_text(encoding="utf-8"))
        for account in (MINE, THEIRS):
            connection.execute("INSERT INTO v3_identity.account (account_id) VALUES (%s)", (account,))
        asyncio.run(link_the_commanders(connection))
        yield connection


async def link_the_commanders(connection):
    pool = await the_apis_own_pool(connection)
    try:
        async with pool.acquire() as conn, conn.transaction():
            for account, fid in ((MINE, MY_FID), (THEIRS, THEIR_FID)):
                await associate_verified_commander(
                    conn, account_id=account, issuer=ISSUER, fid=fid, verified_at=datetime.now(timezone.utc))
    finally:
        await pool.close()


@pytest.fixture
def database(events_database):
    events_database.execute("DELETE FROM v3_private.haul_site_reading")
    events_database.execute("DELETE FROM v3_identity.plugin_key")
    limiter.reset()
    return events_database


def through_the_address(connection, scenario):
    """Run ``scenario(client, pool, key)`` with a live key of MINE's."""
    async def run():
        pool = await the_apis_own_pool(connection)
        app = FastAPI()
        app.state.limiter = limiter
        app.include_router(events_route.router)
        app.dependency_overrides[get_pool] = lambda: pool
        try:
            async with pool.acquire() as conn, conn.transaction():
                _, key = await keys.make_key(conn, MINE, "gaming PC")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://plugin.test") as client:
                return await scenario(client, pool, key)
        finally:
            await pool.close()
    return asyncio.run(run())


def send(client, key, events, fid=MY_FID, docked=None):
    body = {"fid": fid, "plugin_version": "0.1.0", "events": events}
    if docked is not None:
        body["docked"] = docked
    return client.post(ADDRESS, json=body, headers={"Authorization": "Bearer %s" % key})


def named(rows, site) -> list[bool]:
    """Whether each kept reading carries the site's name. Compared here, not in
    an assert, so that a failing test never prints the name."""
    return [row[4] == site["name"] for row in rows]


def kept(database) -> list[tuple]:
    return database.execute(
        """SELECT owner_account_id, market_id, read_at, system_id64, site_name, site_type,
                  progress, complete, failed, needs
             FROM v3_private.haul_site_reading ORDER BY read_at""").fetchall()


def test_a_sites_list_is_kept_once_named_and_never_added_up(database):
    site = the_site()
    first, second = depot_lists()
    batch = [
        docked_at(site, "2026-11-28T10:00:00Z"),
        on_the_site(first, site, "2026-11-28T10:00:02Z"),
        # The game writes the same list again while the player stays docked.
        on_the_site(first, site, "2026-11-28T10:00:17Z"),
        on_the_site(first, site, "2026-11-28T10:00:32Z"),
        # Deliveries are history only: they never change the list.
        *[event for event in samples() if event["event"] == "ColonisationContribution"],
        on_the_site(second, site, "2026-11-28T10:05:00Z"),
    ]

    async def scenario(client, pool, key):
        return await send(client, key, batch), await send(client, key, batch)

    answer, again = through_the_address(database, scenario)
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"received": len(batch), "kept": 2, "repeated": 2, "left_for_later": 3,
                             "refused": 0, "reasons": []}
    # Sending the same batch again changes nothing.
    assert again.json()["kept"] == 0 and again.json()["repeated"] == 4
    rows = kept(database)
    assert len(rows) == 2 and named(rows, site) == [True, True]
    for row, real in zip(rows, (first, second), strict=True):
        assert row[0] == MINE and row[1] == site["id"]
        assert (row[3], row[5]) == (site["system_id64"], "SpaceConstructionDepot")
        assert row[6] == pytest.approx(real["ConstructionProgress"], rel=1e-6)
        assert (row[7], row[8]) == (real["ConstructionComplete"], real["ConstructionFailed"])
        assert row[9] == sorted(
            ({"symbol": good["Name"][1:-len("_name;")].lower(), "required": good["RequiredAmount"],
              "provided": good["ProvidedAmount"], "payment": good["Payment"]}
             for good in real["ResourcesRequired"]), key=lambda need: need["symbol"])


def test_a_site_is_named_from_the_plugins_last_docked_line_or_a_later_one(database):
    site = the_site()
    first, second = depot_lists()

    async def scenario(client, pool, key):
        # The plugin's last Docked line, sent with the batch.
        await send(client, key, [on_the_site(first, site, "2026-11-28T11:00:05Z")],
                   docked=docked_at(site, "2026-11-28T11:00:00Z"))
        # A reading with no Docked line before it is kept unnamed...
        await send(client, key, [on_the_site(second, site, "2026-11-28T12:00:05Z")])
        return await send(client, key, [docked_at(site, "2026-11-28T12:10:00Z")])

    through_the_address(database, scenario)
    rows = kept(database)
    assert named(rows, site) == [True, True]
    # ...and is named by the latest reading that knew the site.
    assert all(row[3] == site["system_id64"] for row in rows)


def test_an_unnamed_reading_is_named_by_a_later_docked_line(database):
    site = the_site()
    first, _ = depot_lists()

    async def scenario(client, pool, key):
        await send(client, key, [on_the_site(first, site, "2026-11-28T13:00:05Z")])
        before = kept(database)
        await send(client, key, [docked_at(site, "2026-11-28T13:20:00Z")])
        return before

    before = through_the_address(database, scenario)
    assert [row[4] is None for row in before] == [True]
    assert named(kept(database), site) == [True]


def test_the_sites_name_is_read_from_the_record_and_never_written_here():
    # The site is picked by its system's number; this file holds neither its
    # name nor its system's.
    site = the_site()
    text = Path(__file__).read_text(encoding="utf-8")
    assert site["name"] not in text and site["system_name"] not in text


def test_only_a_linked_commanders_journal_is_taken_with_a_live_key(database):
    site = the_site()
    first, _ = depot_lists()
    batch = [docked_at(site, "2026-11-28T14:00:00Z"), on_the_site(first, site, "2026-11-28T14:00:02Z")]

    async def scenario(client, pool, key):
        theirs = await send(client, key, batch, fid=THEIR_FID)
        nobody = await send(client, key, batch, fid="F9999999")
        nonsense = await send(client, key, batch, fid="not-a-fid")
        no_key = await client.post(ADDRESS, json={"fid": MY_FID, "plugin_version": "0.1.0", "events": batch})
        wrong_key = await send(client, keys.PREFIX + "x" * 43, batch)
        async with pool.acquire() as conn:
            (key_id,) = [row["plugin_key_id"] for row in await keys.list_keys(conn, MINE)]
            await keys.revoke_key(conn, MINE, key_id)
        cancelled = await send(client, key, batch)
        return theirs, nobody, nonsense, no_key, wrong_key, cancelled

    theirs, nobody, nonsense, no_key, wrong_key, cancelled = through_the_address(database, scenario)
    assert [answer.status_code for answer in (theirs, nobody, nonsense)] == [403, 403, 403]
    assert theirs.json()["detail"] == events_route.NOT_LINKED
    assert [answer.status_code for answer in (no_key, wrong_key, cancelled)] == [401, 401, 401]
    assert kept(database) == []


def test_kinds_the_plugin_does_not_send_are_refused_and_bad_lists_with_them(database):
    site = the_site()
    first, _ = depot_lists()
    broken = on_the_site(first, site, "2026-11-28T15:00:10Z")
    broken["ConstructionProgress"] = 1.5
    twice = on_the_site(first, site, "2026-11-28T15:00:20Z")
    twice["ResourcesRequired"].append(dict(twice["ResourcesRequired"][0]))
    batch = [
        {"timestamp": "2026-11-28T15:00:00Z", "event": "FSDJump", "StarSystem": "Sol"},
        {"event": readings.DEPOT, "MarketID": site["id"]},
        broken, twice,
        on_the_site(first, site, "2026-11-28T15:00:30Z"),
    ]

    async def scenario(client, pool, key):
        return await send(client, key, batch)

    answer = through_the_address(database, scenario)
    assert answer.status_code == 200
    receipt = answer.json()
    assert (receipt["kept"], receipt["refused"]) == (1, 4)
    assert any("between 0 and 1" in reason for reason in receipt["reasons"])
    assert any("listed twice" in reason for reason in receipt["reasons"])
    assert len(kept(database)) == 1


def test_a_batch_is_at_most_500_events(database):
    site = the_site()
    first, _ = depot_lists()
    start = datetime(2026, 11, 28, 16, tzinfo=timezone.utc)
    batch = [on_the_site(first, site, (start + timedelta(seconds=15 * n)).strftime("%Y-%m-%dT%H:%M:%SZ"))
             for n in range(events_route.MAX_EVENTS + 1)]

    async def scenario(client, pool, key):
        return await send(client, key, batch)

    assert through_the_address(database, scenario).status_code == 422
    assert kept(database) == []


def test_good_names_are_the_games_symbols_in_lower_case():
    assert readings.symbol_of("$aluminium_name;") == "aluminium"
    assert readings.symbol_of("$CMMComposite_name;") == "cmmcomposite"
    assert readings.symbol_of("Aluminium") == "aluminium"
    for bad in (None, 7, "", "$_name;", "Liquid oxygen", "x" * 81):
        with pytest.raises(readings.Refused):
            readings.symbol_of(bad)


def test_the_address_answers_503_until_migration_034_is_applied(database):
    site = the_site()
    first, _ = depot_lists()

    async def make_key():
        pool = await the_apis_own_pool(database)
        try:
            async with pool.acquire() as conn, conn.transaction():
                return (await keys.make_key(conn, MINE, "PC"))[1]
        finally:
            await pool.close()

    key = asyncio.run(make_key())
    database.execute("ALTER TABLE v3_private.haul_site_reading RENAME TO haul_site_reading_hidden")
    try:
        async def run():
            pool = await the_apis_own_pool(database)
            app = FastAPI()
            app.state.limiter = limiter
            app.include_router(events_route.router)
            app.dependency_overrides[get_pool] = lambda: pool
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://plugin.test") as client:
                    return await send(client, key, [on_the_site(first, site, "2026-11-28T17:00:00Z")])
            finally:
                await pool.close()

        answer = asyncio.run(run())
        assert answer.status_code == 503 and answer.json()["detail"] == events_route.NOT_READY
    finally:
        database.execute("ALTER TABLE v3_private.haul_site_reading_hidden RENAME TO haul_site_reading")
