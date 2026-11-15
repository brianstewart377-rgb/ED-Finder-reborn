"""The journal upload: a person's own flight log, kept private to their account.

The code review of 15 November 2026 found nothing tested these routes. They
matter for two reasons. They hold the most personal thing on the site, so
"only the account that sent it can read it" has to be true. And they load
their store by name at run time (routers/v3_journal.py, ``_store``), which is
the kind of link a clean-up can cut without any import failing.

These tests go through the routes themselves, rate limiter included, against
real PostgreSQL 18 with every real migration. Being signed in is stood in
for: the session cookie's lookup is replaced by a header that names one of
two accounts made for the test, so everything after "who is asking" is the
real code.

The journal sent is written for the test, because no player's journal belongs
in this repo. Every fact in it is real: it is a jump to LP 634-1 and a scan of
each of its five bodies, with the system's real address and position and the
bodies' real names, read from the answers the live site gave on 15 November
2026 (apps/web/cypress/fixtures/journeys/lp-634-1).

Needs RATINGS_V4_VALIDATION_DATABASE_URL. CI sets EDF_DB_TESTS_REQUIRED=1 so
it cannot be skipped there.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

os.environ.setdefault("CORS_ORIGINS", "https://journal-upload-test.invalid")

from edfinder_api.auth import AuthenticatedUser  # noqa: E402
from edfinder_api.config import limiter, settings  # noqa: E402
from edfinder_api.deps import get_pool  # noqa: E402
from edfinder_api.main import init_connection  # noqa: E402
from edfinder_api.routers import v3_journal as journal_route  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RECORDED = ROOT / "apps/web/cypress/fixtures/journeys/lp-634-1"
MINE, THEIRS = uuid.uuid4(), uuid.uuid4()
# The site's own address, as the API's settings have it: uploads are taken
# from pages of the site only.
THE_SITE = settings.cors_origins.split(",")[0].strip().rstrip("/")
FILE_NAME = "Journal.2026-11-15T180000.01.log"


def the_journal() -> tuple[dict, list[dict]]:
    """One journal file, as the page sends it: the file's facts and its events."""
    system = json.loads((RECORDED / "system.json").read_text())
    bodies = json.loads((RECORDED / "bodies.json").read_text())
    address = int(system["id64"])
    lines = [
        {"timestamp": "2026-11-15T18:00:00Z", "event": "Fileheader", "part": 1, "language": "English/UK",
         "Odyssey": True, "gameversion": "4.2.0.0", "build": "r312744/r0 "},
        {"timestamp": "2026-11-15T18:02:10Z", "event": "FSDJump", "StarSystem": system["name"],
         "SystemAddress": address, "StarPos": [system["x_ly"], system["y_ly"], system["z_ly"]],
         "StarClass": "DA", "JumpDist": 11.2, "FuelUsed": 1.9, "FuelLevel": 14.1},
    ]
    for number, body in enumerate(bodies):
        lines.append({"timestamp": "2026-11-15T18:03:%02dZ" % number, "event": "Scan", "ScanType": "Detailed",
                      "StarSystem": system["name"], "SystemAddress": address, "BodyName": body["name"],
                      "BodyID": number, "DistanceFromArrivalLS": body["distance_from_arrival_ls"] or 0.0,
                      "WasDiscovered": True, "WasMapped": False})
    text = "".join(json.dumps(line, separators=(", ", ":")) + "\r\n" for line in lines)
    events, offset = [], 0
    for line in lines:
        written = json.dumps(line, separators=(", ", ":"))
        events.append({
            "event_type": line["event"],
            "event_timestamp": line["timestamp"],
            "source_record_hash": hashlib.sha256(written.encode()).hexdigest(),
            "source_file": FILE_NAME,
            "source_offset": offset,
            "payload": {key: value for key, value in line.items() if key not in ("timestamp", "event")},
        })
        offset += len(written.encode()) + 2
    file = {"name": FILE_NAME, "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "size_bytes": len(text.encode()), "line_count": len(lines), "event_count": len(lines),
            "first_event_at": lines[0]["timestamp"], "last_event_at": lines[-1]["timestamp"]}
    return file, events


def upload(file: dict, events: list[dict]) -> dict:
    return {"parser_version": "journal-import-worker-v3", "files": [file], "events": events}


@pytest.fixture(scope="module")
def journal_database():
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
def database(journal_database, monkeypatch):
    """The database with nobody's journal in it, and "who is asking" read from a header."""
    journal_database.execute(
        "TRUNCATE v3_private.private_import, v3_private.journal_event, v3_private.journal_import_file CASCADE")
    limiter.reset()

    async def who_is_asking(request):
        account = {"mine": MINE, "theirs": THEIRS}.get(request.headers.get("X-Test-Account", ""))
        if account is None:
            return None
        return AuthenticatedUser(account_id=account, commander_name=None, is_owner=False,
                                 recent_auth_at=datetime.now(timezone.utc), session_id=uuid.uuid4())
    monkeypatch.setattr(journal_route, "get_request_user", who_is_asking)
    return journal_database


async def the_apis_own_pool(connection):
    """A pool whose connections are set up as the running API sets its own up
    (main.py, init_connection). The upload hands the database whole JSON
    values, which only a connection set up that way accepts."""
    import asyncpg
    from psycopg.conninfo import conninfo_to_dict

    where = conninfo_to_dict(os.environ["RATINGS_V4_VALIDATION_DATABASE_URL"])
    return await asyncpg.create_pool(
        host=where.get("host"), port=int(where.get("port", 5432)), user=where.get("user"),
        password=where.get("password"), database=connection.info.dbname,
        min_size=1, max_size=2, statement_cache_size=0, init=init_connection)


def through_the_routes(connection, scenario):
    async def run():
        pool = await the_apis_own_pool(connection)
        app = FastAPI()
        app.state.limiter = limiter
        app.include_router(journal_route.router)
        app.dependency_overrides[get_pool] = lambda: pool
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://journal.test") as client:
                return await scenario(client)
        finally:
            await pool.close()
    return asyncio.run(run())


def me(**more) -> dict:
    return {"X-Test-Account": "mine", "Origin": THE_SITE, **more}


def them(**more) -> dict:
    return {"X-Test-Account": "theirs", "Origin": THE_SITE, **more}


def stored(connection) -> int:
    return connection.execute("SELECT count(*) FROM v3_private.journal_event").fetchone()[0]


READS = ["/summary", "/systems", "/bodies", "/codex", "/organics", "/sales", "/galaxy-impact",
         "/station-observations",
         "/travel-heatmap?min_x=-50&max_x=50&min_y=-50&max_y=50&min_z=-50&max_z=50&zoom=1",
         "/imports/00000000-0000-4000-8000-000000000000"]


@pytest.mark.parametrize("path", READS)
def test_nobody_reads_a_journal_without_signing_in(database, path):
    async def scenario(client):
        return await client.get("/api/v1/journal" + path)

    answer = through_the_routes(database, scenario)
    assert answer.status_code == 401 and answer.json()["detail"] == "Sign-in required"


def test_nobody_sends_a_journal_without_signing_in(database):
    file, events = the_journal()

    async def scenario(client):
        return await client.post("/api/v1/journal/imports", json=upload(file, events),
                                 headers={"Origin": THE_SITE})

    assert through_the_routes(database, scenario).status_code == 401
    assert stored(database) == 0


@pytest.mark.parametrize("origin", [None, "https://somewhere-else.example"])
def test_a_journal_is_taken_only_from_a_page_of_the_site_itself(database, origin):
    """A signed-in person's browser must not be made to upload by another site."""
    file, events = the_journal()
    headers = {"X-Test-Account": "mine", **({"Origin": origin} if origin else {})}

    async def scenario(client):
        return await client.post("/api/v1/journal/imports", json=upload(file, events), headers=headers)

    answer = through_the_routes(database, scenario)
    assert answer.status_code == 403 and answer.json()["detail"] == "Trusted request origin required"
    assert stored(database) == 0


def test_a_journal_is_kept_and_its_receipt_says_what_was_taken(database):
    file, events = the_journal()

    async def scenario(client):
        sent = await client.post("/api/v1/journal/imports", json=upload(file, events), headers=me())
        assert sent.status_code == 200, sent.text
        receipt = sent.json()
        again = await client.get("/api/v1/journal/imports/%s" % receipt["import_id"], headers=me())
        summary = await client.get("/api/v1/journal/summary", headers=me())
        return receipt, again, summary.json()

    receipt, again, summary = through_the_routes(database, scenario)
    assert receipt["status"] == "READY"
    assert (receipt["files_received"], receipt["files_admitted"], receipt["files_skipped"]) == (1, 1, 0)
    assert (receipt["events_received"], receipt["events_inserted"], receipt["duplicates_skipped"]) == (7, 7, 0)
    assert receipt["event_counts"] == {"Fileheader": 1, "FSDJump": 1, "Scan": 5}
    assert receipt["privacy_stripped_fields"] == 0
    # The receipt can be read again by the account that sent it.
    assert again.status_code == 200 and again.json()["import_id"] == receipt["import_id"]
    assert summary["events_stored"] == 7
    assert stored(database) == 7
    # What is kept is the real system, under the account that sent it.
    kept = database.execute(
        """SELECT owner_account_id, event_payload->>'StarSystem', (event_payload->>'SystemAddress')::bigint
             FROM v3_private.journal_event WHERE event_type = 'FSDJump'""").fetchall()
    assert kept == [(MINE, "LP 634-1", 6131166483162)]


def test_the_same_file_sent_twice_is_kept_once(database):
    file, events = the_journal()

    async def scenario(client):
        first = await client.post("/api/v1/journal/imports", json=upload(file, events), headers=me())
        second = await client.post("/api/v1/journal/imports", json=upload(file, events), headers=me())
        return first.json(), second

    first, second = through_the_routes(database, scenario)
    assert first["events_inserted"] == 7
    assert second.status_code == 200, second.text
    assert second.json()["events_inserted"] == 0
    assert stored(database) == 7


def test_what_one_account_sent_another_cannot_read(database):
    file, events = the_journal()

    async def scenario(client):
        sent = await client.post("/api/v1/journal/imports", json=upload(file, events), headers=me())
        receipt = sent.json()
        their_summary = await client.get("/api/v1/journal/summary", headers=them())
        their_look = await client.get("/api/v1/journal/imports/%s" % receipt["import_id"], headers=them())
        their_systems = await client.get("/api/v1/journal/systems", headers=them())
        my_summary = await client.get("/api/v1/journal/summary", headers=me())
        return their_summary.json(), their_look.status_code, their_systems.json(), my_summary.json()

    their_summary, their_look, their_systems, my_summary = through_the_routes(database, scenario)
    assert my_summary["events_stored"] == 7
    assert their_summary["events_stored"] == 0 and their_summary["systems_observed"] == 0
    assert their_look == 404, "another account is not even told that the import exists"
    assert their_systems == []


def test_fields_the_site_has_no_use_for_are_not_kept(database):
    """The page strips a journal down before sending it; the API does it again,
    so a changed or copied page cannot store more about a person than the
    site means to keep."""
    file, events = the_journal()
    jump = next(event for event in events if event["event_type"] == "FSDJump")
    jump["payload"] = {**jump["payload"], "Commander": "Somebody", "FID": "F0000000", "Wanted": True}

    async def scenario(client):
        return await client.post("/api/v1/journal/imports", json=upload(file, events), headers=me())

    answer = through_the_routes(database, scenario)
    assert answer.status_code == 200, answer.text
    assert answer.json()["privacy_stripped_fields"] == 3
    (payload,) = database.execute(
        "SELECT event_payload FROM v3_private.journal_event WHERE event_type = 'FSDJump'").fetchone()
    assert not {"Commander", "FID", "Wanted"} & set(payload)
    assert payload["StarSystem"] == "LP 634-1"


def test_an_event_the_site_does_not_take_is_refused_and_nothing_is_kept(database):
    file, events = the_journal()
    events[1] = {**events[1], "event_type": "ReceiveText", "payload": {"From": "Somebody", "Message": "o7"}}

    async def scenario(client):
        return await client.post("/api/v1/journal/imports", json=upload(file, events), headers=me())

    answer = through_the_routes(database, scenario)
    assert answer.status_code == 422
    assert stored(database) == 0, "not the six good events either: a batch is taken whole or not at all"
