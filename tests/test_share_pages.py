"""Shared links: the page a link-preview robot reads, and the picture it shows.

A link like https://ed-finder.app/s/<system> is what people paste into Discord.
A person who follows it is sent to the system's page. A robot that fetches it
to draw a preview gets a small page naming the system, and a picture
(/api/share/og/<system>) pinned to the same data set, so the words and the
picture cannot be about different versions of the galaxy.

The code review of 15 November 2026 found nothing tested this. These tests go
through the two routes themselves, rate limiter included, against real
PostgreSQL 18 with the systems of the retained cohort
(tests/fixtures/ratings_v4_sources).

Needs RATINGS_V4_VALIDATION_DATABASE_URL. CI sets EDF_DB_TESTS_REQUIRED=1 so
it cannot be skipped there.
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import uuid

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

os.environ.setdefault("CORS_ORIGINS", "https://share-pages-test.invalid")

from edfinder_api import share_router  # noqa: E402
from edfinder_api.config import limiter  # noqa: E402
from edfinder_api.deps import get_readonly_pool  # noqa: E402
from tests.test_find_reach import _api_pool  # noqa: E402

DISCORD = {"User-Agent": "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
           "X-Forwarded-Host": "ed-finder.app", "X-Forwarded-Proto": "https"}
A_PERSON = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/145.0"}


@pytest.fixture(scope="module")
def share_database():
    from tests.ratings_v4_pg_fixture import canonical_database

    with canonical_database() as database:
        yield database[0]


def the_generation(connection) -> tuple[str, str]:
    """The published data set: its schema and its id."""
    schema, generation = connection.execute(
        """
        SELECT generation.relation_schema, generation.generation_id::text
          FROM v3_meta.current_canonical_generation current
          JOIN v3_meta.canonical_generation generation USING (generation_id)
         WHERE current.singleton
        """).fetchone()
    return schema, generation


def a_real_system(connection) -> dict:
    schema, _ = the_generation(connection)
    row = connection.execute(
        f"SELECT id64, name, x_ly, y_ly, z_ly FROM {schema}.systems ORDER BY id64 LIMIT 1").fetchone()
    return {"id64": row[0], "name": row[1], "x": row[2], "y": row[3], "z": row[4]}


class Kept:
    """A cache that keeps what it is given, as Redis does for the pictures."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.values[key] = value


class NoDatabase:
    """A database pool that fails the test if anything is asked of it."""

    def __getattr__(self, name):
        raise AssertionError("the database must not be asked for this (%s)" % name)


def through_the_routes(connection, scenario, cache=None, with_database=True):
    async def run():
        limiter.reset()
        pool = await _api_pool(connection) if with_database else None
        app = FastAPI()
        app.state.limiter = limiter
        app.state.redis = cache
        app.include_router(share_router.router)

        app.dependency_overrides[get_readonly_pool] = lambda: pool if pool is not None else NoDatabase()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://share.test") as client:
                return await scenario(client)
        finally:
            if pool is not None:
                await pool.close()
    return asyncio.run(run())


def meta(page: str, name: str) -> str:
    found = re.search(r'<meta (?:property|name)="%s" content="([^"]*)">' % re.escape(name), page)
    assert found, "the page has no %s" % name
    return found.group(1)


def test_a_person_who_follows_a_shared_link_is_sent_to_the_systems_page(share_database):
    async def scenario(client):
        return await client.get("/s/10477373803", headers=A_PERSON)

    # No database is given to this one: a person is sent on before any is asked.
    answer = through_the_routes(share_database, scenario, with_database=False)
    assert answer.status_code == 302
    assert answer.headers["location"] == "/inspect?system=10477373803"


def test_a_preview_robot_is_told_the_systems_name_place_and_picture(share_database):
    system = a_real_system(share_database)
    _, generation = the_generation(share_database)

    async def scenario(client):
        return await client.get("/s/%d" % system["id64"], headers=DISCORD)

    answer = through_the_routes(share_database, scenario)
    assert answer.status_code == 200 and answer.headers["content-type"].startswith("text/html")
    page = answer.text
    assert meta(page, "og:title") == "%s — ED Finder" % system["name"]
    described = meta(page, "og:description")
    assert "Coordinates %.1f / %.1f / %.1f ly" % (system["x"], system["y"], system["z"]) in described
    assert "catalogued bod" in described
    # The link a person would open, and the picture, on the public address.
    assert meta(page, "og:url") == "https://ed-finder.app/inspect?system=%d" % system["id64"]
    assert meta(page, "og:image") == "https://ed-finder.app/api/share/og/%d?generation=%s" % (
        system["id64"], generation), "the picture is pinned to the data set the words came from"
    assert meta(page, "twitter:image") == meta(page, "og:image")
    assert (meta(page, "og:image:width"), meta(page, "og:image:height")) == ("1200", "630")


def test_a_robot_asking_about_a_system_that_does_not_exist_is_told_so(share_database):
    async def scenario(client):
        return await client.get("/s/9000000000000000001", headers=DISCORD)

    answer = through_the_routes(share_database, scenario)
    assert answer.status_code == 404
    assert "No catalogue system exists for id64 9000000000000000001" in answer.text


def test_the_picture_is_a_real_card_of_the_promised_size(share_database):
    system = a_real_system(share_database)
    _, generation = the_generation(share_database)

    async def scenario(client):
        pinned = await client.get("/api/share/og/%d?generation=%s" % (system["id64"], generation))
        following = await client.get("/api/share/og/%d" % system["id64"])
        return pinned, following

    pinned, following = through_the_routes(share_database, scenario)
    for answer in (pinned, following):
        assert answer.status_code == 200 and answer.headers["content-type"] == "image/png"
        picture = Image.open(io.BytesIO(answer.content))
        assert picture.format == "PNG" and picture.size == (1200, 630)
        # Something is drawn on it: a card of one flat colour would be a broken card.
        assert len(picture.convert("RGB").getcolors(maxcolors=1 << 24)) > 16
    # A pinned picture never changes, so it may be kept for a week. One that
    # follows the newest data set may be kept for an hour.
    assert pinned.headers["cache-control"] == "public, max-age=604800, immutable"
    assert following.headers["cache-control"] == "public, max-age=3600"
    assert pinned.content == following.content, "the same system on the same data set is the same card"


def test_a_picture_drawn_once_is_served_from_the_cache_the_next_time(share_database):
    system = a_real_system(share_database)
    _, generation = the_generation(share_database)
    cache = Kept()

    async def scenario(client):
        address = "/api/share/og/%d?generation=%s" % (system["id64"], generation)
        return await client.get(address), await client.get(address)

    first, second = through_the_routes(share_database, scenario, cache=cache)
    assert (first.headers["x-cache"], second.headers["x-cache"]) == ("MISS", "HIT")
    assert first.content == second.content and second.content.startswith(share_router.PNG_SIGNATURE)
    # Kept under the data set it was drawn from, so a new data set gets a new card.
    assert list(cache.values) == ["og:v3:%s:%d" % (generation, system["id64"])]


def test_no_picture_for_a_system_or_a_data_set_that_does_not_exist(share_database):
    system = a_real_system(share_database)
    _, generation = the_generation(share_database)

    async def scenario(client):
        no_system = await client.get("/api/share/og/9000000000000000001?generation=%s" % generation)
        no_generation = await client.get("/api/share/og/%d?generation=%s" % (system["id64"], uuid.uuid4()))
        not_an_id = await client.get("/api/share/og/%d?generation=newest" % system["id64"])
        return no_system.status_code, no_generation.status_code, not_an_id.status_code

    assert through_the_routes(share_database, scenario) == (404, 404, 422)


def test_a_name_cannot_put_markup_into_the_page():
    # A system's name is text from the game's data; it is never trusted as HTML.
    assert share_router._html_escape('<script>alert("x")</script> & co') == (
        "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; co")


@pytest.mark.parametrize("headers, expected", [
    # What the web server passes on from the public site.
    ({"X-Forwarded-Host": "ed-finder.app", "X-Forwarded-Proto": "https"}, "https://ed-finder.app"),
    # A hop that says "http" for a public name is not believed.
    ({"X-Forwarded-Host": "ed-finder.app", "X-Forwarded-Proto": "http"}, "https://ed-finder.app"),
    # A host that is not a plain name falls back to the public site.
    ({"X-Forwarded-Host": "evil.example/@x", "X-Forwarded-Proto": "https"}, "https://ed-finder.app"),
    # Local development keeps plain http.
    ({"X-Forwarded-Host": "localhost:5173", "X-Forwarded-Proto": "http"}, "http://localhost:5173"),
])
def test_the_address_in_a_preview_is_the_public_sites_and_never_a_forged_one(
        share_database, headers, expected):
    system = a_real_system(share_database)

    async def scenario(client):
        return await client.get("/s/%d" % system["id64"],
                                headers={"User-Agent": DISCORD["User-Agent"], **headers})

    page = through_the_routes(share_database, scenario).text
    assert meta(page, "og:url") == "%s/inspect?system=%d" % (expected, system["id64"])
