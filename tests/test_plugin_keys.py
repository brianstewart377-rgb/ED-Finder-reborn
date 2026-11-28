"""Keys for ED-Finder's EDMC plugin ("Haul — design", step 2a).

A member lists, makes and cancels their own keys on the Account page
(routers/plugin_keys.py, edfinder_api/haul/keys.py). These tests go through
the routes themselves, rate limiter included, against real PostgreSQL 18 with
every real migration, as tests/test_journal_upload.py does: being signed in
is stood in for by a header naming one of two accounts made for the test, so
everything after "who is asking" is the real code.

What must hold: the key is shown once and only its SHA-256 is kept; a member
sees and cancels only their own keys; making one needs the site's own origin
and a recent sign-in; a member has at most five live keys; a cancelled key,
or one of a closed account, is never accepted; the routes answer 503 until
migration 034 has made the table; and making and cancelling are written to
the security record.

Needs RATINGS_V4_VALIDATION_DATABASE_URL. CI sets EDF_DB_TESTS_REQUIRED=1 so
it cannot be skipped there.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

os.environ.setdefault("CORS_ORIGINS", "https://plugin-keys-test.invalid")

from edfinder_api.auth import AuthenticatedUser  # noqa: E402
from edfinder_api.config import limiter, settings  # noqa: E402
from edfinder_api.deps import get_pool  # noqa: E402
from edfinder_api.haul import keys  # noqa: E402
from edfinder_api.routers import plugin_keys as keys_route  # noqa: E402
from tests.test_journal_upload import the_apis_own_pool  # noqa: E402

MINE, THEIRS = uuid.uuid4(), uuid.uuid4()
KEYS = "/api/v1/plugin/keys"
THE_SITE = settings.cors_origins.split(",")[0].strip().rstrip("/")


@pytest.fixture(scope="module")
def keys_database():
    from tests import test_v3_derived_product_publication as publication
    from tests.helpers import finder_seed
    from tests.ratings_v4_pg_fixture import canonical_database

    with canonical_database() as database:
        connection = database[0]
        for name in finder_seed.migration_names():
            connection.execute((publication.MIGRATIONS / name).read_text(encoding="utf-8"))
        for account in (MINE, THEIRS):
            connection.execute("INSERT INTO v3_identity.account (account_id) VALUES (%s)", (account,))
        yield connection


@pytest.fixture
def database(keys_database, monkeypatch):
    """No keys yet, and "who is asking" read from a header. ``X-Test-Old``
    stands for a sign-in longer ago than a key needs."""
    keys_database.execute("DELETE FROM v3_identity.plugin_key")
    keys_database.execute("DELETE FROM v3_identity.security_audit_event WHERE event_code LIKE 'plugin_key.%'")
    limiter.reset()

    async def who_is_asking(request):
        account = {"mine": MINE, "theirs": THEIRS}.get(request.headers.get("X-Test-Account", ""))
        if account is None:
            return None
        signed_in = datetime.now(timezone.utc) - (timedelta(days=1) if request.headers.get("X-Test-Old") else timedelta())
        return AuthenticatedUser(account_id=account, commander_name=None, is_owner=False,
                                 recent_auth_at=signed_in, session_id=uuid.uuid4())
    monkeypatch.setattr(keys_route, "get_request_user", who_is_asking)
    return keys_database


def through_the_routes(connection, scenario):
    async def run():
        pool = await the_apis_own_pool(connection)
        app = FastAPI()
        app.state.limiter = limiter
        app.include_router(keys_route.router)
        app.dependency_overrides[get_pool] = lambda: pool
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://keys.test") as client:
                return await scenario(client, pool)
        finally:
            await pool.close()
    return asyncio.run(run())


def me(**more) -> dict:
    return {"X-Test-Account": "mine", "Origin": THE_SITE, **more}


def them(**more) -> dict:
    return {"X-Test-Account": "theirs", "Origin": THE_SITE, **more}


def test_a_key_is_shown_once_and_only_its_sha256_is_kept(database):
    async def scenario(client, pool):
        made = await client.post(KEYS, json={"label": "  gaming   PC "}, headers=me())
        listed = await client.get(KEYS, headers=me())
        return made, listed

    made, listed = through_the_routes(database, scenario)
    assert made.status_code == 201, made.text
    secret = made.json()["secret"]
    assert secret.startswith(keys.PREFIX) and len(secret) > 40
    assert made.json()["key"]["label"] == "gaming PC"
    assert listed.status_code == 200
    assert [row["label"] for row in listed.json()] == ["gaming PC"]
    assert secret not in listed.text and "secret" not in listed.text
    (kept,) = database.execute("SELECT key_sha256 FROM v3_identity.plugin_key").fetchone()
    assert bytes(kept) == hashlib.sha256(secret.encode()).digest()
    every_column = json.dumps([list(row) for row in database.execute(
        "SELECT * FROM v3_identity.plugin_key").fetchall()], default=str)
    assert secret not in every_column
    (event,) = database.execute(
        "SELECT event_code, outcome, account_id FROM v3_identity.security_audit_event "
        "WHERE event_code LIKE 'plugin_key.%'").fetchall()
    assert event == ("plugin_key.created", "SUCCEEDED", MINE)


def test_a_member_sees_and_cancels_only_their_own_keys(database):
    async def scenario(client, pool):
        mine = (await client.post(KEYS, json={"label": "mine"}, headers=me())).json()
        theirs = (await client.post(KEYS, json={"label": "theirs"}, headers=them())).json()
        my_list = await client.get(KEYS, headers=me())
        not_mine = await client.delete("%s/%s" % (KEYS, theirs["key"]["plugin_key_id"]), headers=me())
        cancelled = await client.delete("%s/%s" % (KEYS, mine["key"]["plugin_key_id"]), headers=me())
        again = await client.delete("%s/%s" % (KEYS, mine["key"]["plugin_key_id"]), headers=me())
        async with pool.acquire() as conn:
            accepted = (await keys.account_for_key(conn, mine["secret"]),
                        await keys.account_for_key(conn, theirs["secret"]))
        return my_list, not_mine, cancelled, again, accepted

    my_list, not_mine, cancelled, again, accepted = through_the_routes(database, scenario)
    assert [row["label"] for row in my_list.json()] == ["mine"]
    assert not_mine.status_code == 404
    assert cancelled.status_code == 204
    assert again.status_code == 404
    # The cancelled key is never accepted again; the other member's still is.
    assert accepted == (None, THEIRS)
    codes = [row[0] for row in database.execute(
        "SELECT event_code FROM v3_identity.security_audit_event WHERE event_code LIKE 'plugin_key.%' "
        "ORDER BY security_audit_event_id").fetchall()]
    assert codes == ["plugin_key.created", "plugin_key.created", "plugin_key.revoked"]


def test_nobody_makes_or_lists_a_key_without_signing_in(database):
    async def scenario(client, pool):
        return (await client.get(KEYS),
                await client.post(KEYS, json={"label": "x"}, headers={"Origin": THE_SITE}))

    listed, made = through_the_routes(database, scenario)
    assert listed.status_code == 401 and made.status_code == 401
    assert database.execute("SELECT count(*) FROM v3_identity.plugin_key").fetchone() == (0,)


def test_making_a_key_needs_the_sites_own_origin_and_a_recent_sign_in(database):
    async def scenario(client, pool):
        elsewhere = await client.post(KEYS, json={"label": "x"},
                                      headers={"X-Test-Account": "mine", "Origin": "https://elsewhere.invalid"})
        no_origin = await client.post(KEYS, json={"label": "x"}, headers={"X-Test-Account": "mine"})
        old = await client.post(KEYS, json={"label": "x"}, headers=me(**{"X-Test-Old": "1"}))
        return elsewhere, no_origin, old

    elsewhere, no_origin, old = through_the_routes(database, scenario)
    assert elsewhere.status_code == 403 and no_origin.status_code == 403
    assert old.status_code == 403 and old.json()["detail"] == "Sign in again to make a key"
    assert database.execute("SELECT count(*) FROM v3_identity.plugin_key").fetchone() == (0,)


def test_a_member_has_at_most_five_live_keys_and_each_needs_a_name(database):
    async def scenario(client, pool):
        made = [await client.post(KEYS, json={"label": "PC %d" % number}, headers=me())
                for number in range(keys.MAX_ACTIVE_KEYS + 1)]
        blank = await client.post(KEYS, json={"label": "   "}, headers=me())
        control = await client.post(KEYS, json={"label": "PC​"}, headers=me())
        too_long = await client.post(KEYS, json={"label": "x" * (keys.LABEL_MAX + 1)}, headers=me())
        cancelled = await client.delete("%s/%s" % (KEYS, made[0].json()["key"]["plugin_key_id"]), headers=me())
        after = await client.post(KEYS, json={"label": "new PC"}, headers=me())
        return made, blank, control, too_long, cancelled, after

    made, blank, control, too_long, cancelled, after = through_the_routes(database, scenario)
    assert [answer.status_code for answer in made] == [201] * keys.MAX_ACTIVE_KEYS + [409]
    assert blank.status_code == 422 and control.status_code == 422 and too_long.status_code == 422
    assert cancelled.status_code == 204 and after.status_code == 201


def test_a_closed_account_s_key_is_not_accepted(database):
    async def scenario(client, pool):
        made = (await client.post(KEYS, json={"label": "PC"}, headers=them())).json()
        async with pool.acquire() as conn:
            before = await keys.account_for_key(conn, made["secret"])
            await conn.execute(
                "UPDATE v3_identity.account SET account_state = 'CLOSED', closed_at = now() WHERE account_id = $1",
                THEIRS)
            after = await keys.account_for_key(conn, made["secret"])
            await conn.execute(
                "UPDATE v3_identity.account SET account_state = 'ACTIVE', closed_at = NULL WHERE account_id = $1",
                THEIRS)
            nonsense = [await keys.account_for_key(conn, text)
                        for text in (None, "", "edfp_", made["secret"] + "x", "x" * 300)]
        return before, after, nonsense

    before, after, nonsense = through_the_routes(database, scenario)
    assert before == THEIRS and after is None
    assert nonsense == [None] * 5


def test_using_a_key_notes_it_at_most_every_few_minutes(database):
    async def scenario(client, pool):
        made = (await client.post(KEYS, json={"label": "PC"}, headers=me())).json()
        start = datetime.now(timezone.utc) + timedelta(seconds=1)
        async with pool.acquire() as conn:
            seen = []
            for minutes in (0, 1, 2, keys.LAST_USED_EVERY_SECONDS // 60 + 1):
                await keys.account_for_key(conn, made["secret"], now=start + timedelta(minutes=minutes))
                seen.append(await conn.fetchval("SELECT last_used_at FROM v3_identity.plugin_key"))
        return start, seen

    start, seen = through_the_routes(database, scenario)
    assert seen[0] == seen[1] == seen[2] == start
    assert seen[3] == start + timedelta(minutes=keys.LAST_USED_EVERY_SECONDS // 60 + 1)


def test_the_routes_answer_503_until_migration_034_is_applied(database):
    database.execute("ALTER TABLE v3_identity.plugin_key RENAME TO plugin_key_hidden")
    try:
        async def scenario(client, pool):
            return (await client.get(KEYS, headers=me()),
                    await client.post(KEYS, json={"label": "PC"}, headers=me()))

        listed, made = through_the_routes(database, scenario)
        assert listed.status_code == 503 and made.status_code == 503
        assert listed.json()["detail"] == keys_route.NOT_READY
    finally:
        database.execute("ALTER TABLE v3_identity.plugin_key_hidden RENAME TO plugin_key")
