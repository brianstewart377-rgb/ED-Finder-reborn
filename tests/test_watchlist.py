"""The watchlist: each browser's own list of systems, kept under its own key.

The code review of 15 November 2026 found that nothing tested the watchlist,
though people use it and its key is the only thing that keeps one person's
list from another's ("the sync_key IS the credential",
apps/api/src/routers/watchlist.py). These tests go through the routes
themselves, rate limiter and all, against real PostgreSQL 18 with the real
migrations and the systems of the retained cohort
(tests/fixtures/ratings_v4_sources).

Needs RATINGS_V4_VALIDATION_DATABASE_URL. CI sets EDF_DB_TESTS_REQUIRED=1 so
it cannot be skipped there.
"""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest
from fastapi import FastAPI

os.environ.setdefault("CORS_ORIGINS", "https://watchlist-test.invalid")

from edfinder_api.config import limiter  # noqa: E402
from edfinder_api.deps import get_pool  # noqa: E402
from edfinder_api.routers import watchlist as watchlist_route  # noqa: E402
from tests.test_find_reach import _api_pool  # noqa: E402

# Two browsers' keys, made up for these tests.
MINE = "mine-0123456789abcdef"
THEIRS = "theirs-0123456789abcdef"


@pytest.fixture(scope="module")
def watchlist_database():
    from tests import test_v3_derived_product_publication as publication
    from tests.helpers import finder_seed
    from tests.ratings_v4_pg_fixture import canonical_database

    with canonical_database() as database:
        connection = database[0]
        # The real migrations in production's order, as far as the watchlist's (014).
        for name in finder_seed.migration_names(through=14):
            connection.execute((publication.MIGRATIONS / name).read_text(encoding="utf-8"))
        yield connection


@pytest.fixture
def database(watchlist_database):
    """The database with nobody's watchlist in it, and the rate limiter's count at nought."""
    watchlist_database.execute("TRUNCATE v3_private.watchlist CASCADE")
    limiter.reset()
    return watchlist_database


def real_systems(connection, count: int) -> list[dict]:
    """Systems of the published generation, as the database has them."""
    (schema,) = connection.execute(
        """
        SELECT generation.relation_schema
          FROM v3_meta.current_canonical_generation current
          JOIN v3_meta.canonical_generation generation USING (generation_id)
         WHERE current.singleton
        """).fetchone()
    rows = connection.execute(
        f"SELECT id64, name, x_ly, y_ly, z_ly FROM {schema}.systems ORDER BY id64 LIMIT %s",
        (count,)).fetchall()
    assert len(rows) == count, "the retained cohort holds fewer systems than this test needs"
    return [{"id64": row[0], "name": row[1], "x": row[2], "y": row[3], "z": row[4]} for row in rows]


def through_the_routes(connection, scenario):
    """Run `scenario` with a client that reaches the watchlist's own routes."""
    async def run():
        pool = await _api_pool(connection)
        app = FastAPI()
        app.state.limiter = limiter
        app.include_router(watchlist_route.router)
        app.dependency_overrides[get_pool] = lambda: pool
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://watchlist.test") as client:
                return await scenario(client)
        finally:
            await pool.close()
    return asyncio.run(run())


async def listed(client: httpx.AsyncClient, key: str) -> list[dict]:
    answer = await client.get("/api/v2/watchlist/%s" % key)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["sync_key"] == key
    return body["watchlist"]


def test_a_new_keys_watchlist_is_empty(database):
    assert through_the_routes(database, lambda client: listed(client, MINE)) == []


def test_a_system_is_kept_with_the_name_and_place_the_database_has(database):
    first, second = real_systems(database, 2)

    async def scenario(client):
        for system in (first, second):
            answer = await client.post("/api/v2/watchlist/%s/%d" % (MINE, system["id64"]))
            assert answer.status_code == 200 and answer.json() == {"ok": True, "sync_key": MINE}
        return await listed(client, MINE)

    kept = through_the_routes(database, scenario)
    assert {row["system_id64"] for row in kept} == {first["id64"], second["id64"]}
    by_id = {row["system_id64"]: row for row in kept}
    for system in (first, second):
        row = by_id[system["id64"]]
        assert row["name"] == system["name"]
        assert (row["x"], row["y"], row["z"]) == (system["x"], system["y"], system["z"])
        # Not known yet for a system only just added: said as unknown, not guessed.
        assert row["population"] is None and row["is_colonised"] is None and row["score"] is None


def test_adding_a_system_twice_keeps_it_once(database):
    (system,) = real_systems(database, 1)

    async def scenario(client):
        for _ in range(2):
            assert (await client.post("/api/v2/watchlist/%s/%d" % (MINE, system["id64"]))).status_code == 200
        return await listed(client, MINE)

    assert [row["system_id64"] for row in through_the_routes(database, scenario)] == [system["id64"]]


def test_one_key_cannot_see_or_change_what_is_kept_under_another(database):
    """The key is all that separates two people's lists."""
    (system,) = real_systems(database, 1)

    async def scenario(client):
        assert (await client.post("/api/v2/watchlist/%s/%d" % (MINE, system["id64"]))).status_code == 200
        seen_by_them = await listed(client, THEIRS)
        # They remove "their" entry for the same system, and set an alert on it.
        removed = await client.delete("/api/v2/watchlist/%s/%d" % (THEIRS, system["id64"]))
        alerted = await client.patch("/api/v2/watchlist/%s/%d/alert" % (THEIRS, system["id64"]),
                                     json={"min_development_score": 99, "economy": "Theirs"})
        return seen_by_them, removed.status_code, alerted.status_code, await listed(client, MINE)

    seen_by_them, removed, alerted, mine = through_the_routes(database, scenario)
    assert seen_by_them == []
    assert removed == 200 and alerted == 200, "nothing of theirs to change, and it says nothing either way"
    assert [row["system_id64"] for row in mine] == [system["id64"]]
    assert mine[0]["alert_min_development_score"] is None and mine[0]["alert_economy"] is None


def test_removing_takes_a_system_off_and_removing_it_again_is_harmless(database):
    first, second = real_systems(database, 2)

    async def scenario(client):
        for system in (first, second):
            await client.post("/api/v2/watchlist/%s/%d" % (MINE, system["id64"]))
        codes = [(await client.delete("/api/v2/watchlist/%s/%d" % (MINE, first["id64"]))).status_code
                 for _ in range(2)]
        return codes, await listed(client, MINE)

    codes, kept = through_the_routes(database, scenario)
    assert codes == [200, 200]
    assert [row["system_id64"] for row in kept] == [second["id64"]]


def test_a_system_the_database_does_not_have_is_refused_and_nothing_is_kept(database):
    nowhere = 9_000_000_000_000_000_001

    async def scenario(client):
        answer = await client.post("/api/v2/watchlist/%s/%d" % (MINE, nowhere))
        return answer.status_code, answer.json(), await listed(client, MINE)

    status, body, kept = through_the_routes(database, scenario)
    assert status == 404 and "not found" in body["detail"]
    assert kept == []


def test_an_alert_is_kept_with_the_system(database):
    (system,) = real_systems(database, 1)

    async def scenario(client):
        await client.post("/api/v2/watchlist/%s/%d" % (MINE, system["id64"]))
        answer = await client.patch("/api/v2/watchlist/%s/%d/alert" % (MINE, system["id64"]),
                                    json={"min_development_score": 70, "economy": "Refinery"})
        assert answer.status_code == 200
        too_high = await client.patch("/api/v2/watchlist/%s/%d/alert" % (MINE, system["id64"]),
                                      json={"min_development_score": 101})
        return too_high.status_code, await listed(client, MINE)

    too_high, kept = through_the_routes(database, scenario)
    assert too_high == 422, "a score above 100 is refused before it reaches the database"
    assert kept[0]["alert_min_development_score"] == 70 and kept[0]["alert_economy"] == "Refinery"


def test_changes_are_shown_only_under_the_key_they_were_noticed_for(database):
    (system,) = real_systems(database, 1)

    async def scenario(client):
        for key in (MINE, THEIRS):
            await client.post("/api/v2/watchlist/%s/%d" % (key, system["id64"]))
        # What the job that watches for changes would write, for my entry only.
        database.execute(
            """
            INSERT INTO v3_private.watchlist_changelog
                (sync_key, system_id64, system_name, change_type, old_value, new_value)
            VALUES (%s, %s, %s, 'population', '0', '1200')
            """, (MINE, system["id64"], system["name"]))
        mine = (await client.get("/api/v2/watchlist/%s/changes" % MINE)).json()["changes"]
        theirs = (await client.get("/api/v2/watchlist/%s/changes" % THEIRS)).json()["changes"]
        # Taking the system off my list takes its changes with it.
        await client.delete("/api/v2/watchlist/%s/%d" % (MINE, system["id64"]))
        after = (await client.get("/api/v2/watchlist/%s/changes" % MINE)).json()["changes"]
        return mine, theirs, after

    mine, theirs, after = through_the_routes(database, scenario)
    assert [(row["system_id64"], row["change_type"], row["old_value"], row["new_value"]) for row in mine] == [
        (system["id64"], "population", "0", "1200")]
    assert theirs == [] and after == []


@pytest.mark.parametrize("key, status", [
    ("fifteen-chars-ok", 200),            # 16 characters: the shortest allowed
    ("fifteen-chars-o", 422),             # 15: too short to be a key at all
    ("has.a.dot-0123456789", 400),        # the right length, a character that is not allowed
    ("k" * 128, 200),
    ("k" * 129, 422),
])
def test_only_a_key_of_the_right_shape_is_taken(database, key, status):
    async def scenario(client):
        return (await client.get("/api/v2/watchlist/%s" % key)).status_code

    assert through_the_routes(database, scenario) == status


def test_the_old_addresses_without_a_key_say_where_to_go(database):
    """Before keys, every visitor shared one watchlist. Those addresses must
    never answer with anybody's list again."""
    async def scenario(client):
        answers = [await client.get("/api/watchlist"),
                   await client.post("/api/watchlist/10477373803"),
                   await client.delete("/api/watchlist/10477373803"),
                   await client.get("/api/watchlist/changes"),
                   await client.get("/api/watchlist/changelog")]
        return [(answer.status_code, answer.json()["detail"]["detail"]) for answer in answers]

    for status, said in through_the_routes(database, scenario):
        assert status == 410
        assert "/api/v2/watchlist/{sync_key}" in said


def test_a_browser_that_asks_too_fast_is_slowed_down(database):
    """Adding is limited to 20 a minute from one address, so a key cannot be
    used to fill the table."""
    (system,) = real_systems(database, 1)

    async def scenario(client):
        return [(await client.post("/api/v2/watchlist/%s/%d" % (MINE, system["id64"]))).status_code
                for _ in range(21)]

    codes = through_the_routes(database, scenario)
    assert codes[:20] == [200] * 20
    assert codes[20] == 429
