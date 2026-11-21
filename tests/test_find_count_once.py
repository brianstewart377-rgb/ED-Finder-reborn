"""Find: a search's count is asked once, and not at all where a page shows it.

Counting is the costly half of a search for one colony type near a system: it
walks outwards to the 1,001st match. On the Pi on 15 November 2026 the first
page of "Manufacturing Hub near Sol" took 10 s and each further page 5 to 7 s,
nearly all of it the same count again.

Two rules, checked in three layers:

* what the rules mean, with no database;
* real PostgreSQL 18 with the real migrations: a short page needs no count
  and shows its own, and a kept count is used only on the generation it was
  made on and only where the page does not show it to be too small;
* the route's cache: the count is asked for the first page read and kept for
  the others, under the same care for generations as the page itself.

The database layer needs RATINGS_V4_VALIDATION_DATABASE_URL. CI sets
EDF_DB_TESTS_REQUIRED=1 so it cannot be skipped there.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://find-count-once-test.invalid")

from edfinder_api import local_search  # noqa: E402
from edfinder_api.find_count import (  # noqa: E402
    count_to_keep,
    known_qualifying,
    total_from_short_page,
)
from edfinder_api.find_generation import resolve_published_generation  # noqa: E402
from edfinder_api.ranking.profile import FIND_COUNT_LIMIT  # noqa: E402
from edfinder_api.routers import search as search_route  # noqa: E402
from tests.test_find_reach import (  # noqa: E402,F401  (the shared fixture)
    _api_pool,
    reach_database,
)
from tests.test_find_type_near import PICKED, RADIUS, _expected  # noqa: E402

GENERATION = {"derived_generation_id": "11111111-1111-4111-8111-111111111111",
              "publication_sequence": 4, "archetype_version": "v3-archetype-5"}


# ---------------------------------------------------------------------------
# What the rules mean
# ---------------------------------------------------------------------------
def test_a_page_that_came_back_short_shows_how_many_matches_there_are():
    # Fewer rows than asked for: the matches have run out.
    assert total_from_short_page(3, 50, 0) == 3
    assert total_from_short_page(0, 50, 0) == 0
    assert total_from_short_page(10, 50, 950) == 960
    assert total_from_short_page(49, 50, 100) == 149
    # A full page says nothing about what follows it.
    assert total_from_short_page(50, 50, 0) is None
    assert total_from_short_page(1, 1, 999) is None
    # An empty page past the first says only "no more than the offset".
    assert total_from_short_page(0, 50, 950) is None


def test_a_kept_count_is_used_only_on_the_generation_it_was_made_on():
    kept = {"qualifying": 37, **GENERATION}
    assert known_qualifying(kept, GENERATION) == 37
    # asyncpg hands the generation id back as a UUID, the cache as text.
    import uuid
    assert known_qualifying(
        kept, {**GENERATION, "derived_generation_id": uuid.UUID(GENERATION["derived_generation_id"])}) == 37
    assert known_qualifying({**kept, "publication_sequence": 5}, GENERATION) is None
    assert known_qualifying(
        {**kept, "derived_generation_id": "22222222-2222-4222-8222-222222222222"}, GENERATION) is None
    # One past the limit is "more than Find counts"; nothing beyond is a count.
    assert known_qualifying({**kept, "qualifying": FIND_COUNT_LIMIT + 1}, GENERATION) == FIND_COUNT_LIMIT + 1
    for junk in (FIND_COUNT_LIMIT + 2, -1, "37", 37.0, True, None):
        assert known_qualifying({**kept, "qualifying": junk}, GENERATION) is None, junk
    for junk in (None, [], "37", 37, {"qualifying": 37}):
        assert known_qualifying(junk, GENERATION) is None, junk


def test_a_kept_count_that_the_page_reaches_past_is_not_used():
    # The claim rule reads tables loaded each night with no new generation,
    # so a count kept across the load can be a few out.
    kept = {"qualifying": 37, **GENERATION}
    assert known_qualifying(kept, GENERATION, shown_to=20) == 37
    # A full last page that ends exactly at the count agrees with it.
    assert known_qualifying(kept, GENERATION, shown_to=37) == 37
    # Rows 21 to 40 are on the page: there are not 37 matches.
    assert known_qualifying(kept, GENERATION, shown_to=40) is None
    # "More than 1,000" is never reached past: a page ends at 1,000.
    more = {"qualifying": FIND_COUNT_LIMIT + 1, **GENERATION}
    assert known_qualifying(more, GENERATION, shown_to=FIND_COUNT_LIMIT) == FIND_COUNT_LIMIT + 1


def test_what_is_kept_of_a_count_is_what_the_count_query_would_say():
    ranking = {"ranking": dict(GENERATION)}
    assert count_to_keep({"total": 37, "total_is_capped": False, **ranking}) == {
        "qualifying": 37, **GENERATION}
    # "1,000+" is kept as one past the limit, as the count query gives it.
    assert count_to_keep({"total": FIND_COUNT_LIMIT, "total_is_capped": True, **ranking}) == {
        "qualifying": FIND_COUNT_LIMIT + 1, **GENERATION}
    # Exactly the limit is not "more".
    assert count_to_keep({"total": FIND_COUNT_LIMIT, "total_is_capped": False, **ranking})[
        "qualifying"] == FIND_COUNT_LIMIT
    # And a kept count reads back as itself.
    kept = count_to_keep({"total": FIND_COUNT_LIMIT, "total_is_capped": True, **ranking})
    assert known_qualifying(json.loads(json.dumps(kept)), GENERATION) == FIND_COUNT_LIMIT + 1
    # Nothing to keep without the generation it was counted on.
    assert count_to_keep({"total": 37, "total_is_capped": False}) is None
    assert count_to_keep({"total": 37, "ranking": {"derived_generation_id": None,
                                                    "publication_sequence": 4}}) is None
    # Nor without the colony type model that scored it: tiers come from it.
    unscored = {key: value for key, value in GENERATION.items() if key != "archetype_version"}
    assert count_to_keep({"total": 37, "ranking": unscored}) is None
    # A count made under another colony type model is not used.
    assert known_qualifying(kept, {**GENERATION, "archetype_version": "v3-archetype-4"}) is None


def _body(**changes) -> dict:
    return {"reference_coords": {"x": 0.0, "y": 0.0, "z": 0.0},
            "filters": {"distance": {"max": RADIUS}, "population": {}, "economy": "any"},
            "body_filters": {}, "sort_by": "distance", "size": 50, "from": 0,
            "reach_ly": 250, "archetype": PICKED, "min_score": 60, **changes}


def test_every_page_of_a_search_shares_one_count_and_no_other_search_does():
    key = search_route._search_count_cache_key
    first = key(_body(), GENERATION)
    assert key(_body(**{"from": 50}), GENERATION) == first
    assert key(_body(size=20, **{"from": 980}), GENERATION) == first
    # Anything that changes which systems match is another search.
    others = [
        _body(min_score=76),
        _body(archetype=None, min_score=None),
        _body(reach_ly=500),
        _body(body_filters={"elw_count": {"min": 1}}),
        _body(filters={"distance": {"max": 100.0}, "population": {}, "economy": "any"}),
        _body(reference_coords={"x": 1.0, "y": 0.0, "z": 0.0}),
        _body(sort_by="development"),
    ]
    keys = {key(body, GENERATION) for body in others}
    assert len(keys) == len(others) and first not in keys
    # And so is the same search on another generation or publication.
    assert key(_body(), {**GENERATION, "publication_sequence": 5}) != first
    assert key(_body(), {**GENERATION, "derived_generation_id": "other"}) != first
    # Or scored by another colony type model: page and count both move.
    rescored = {**GENERATION, "archetype_version": "v3-archetype-4"}
    assert key(_body(), rescored) != first
    assert (search_route._search_cache_key(_body(), rescored)
            != search_route._search_cache_key(_body(), GENERATION))
    # It is never the page's own key.
    assert first != search_route._search_cache_key(_body(), GENERATION)


# ---------------------------------------------------------------------------
# The route's cache
# ---------------------------------------------------------------------------
class _Kept:
    """What the API's cache helpers need of Redis, kept in memory."""

    def __init__(self):
        self.values: dict[str, str] = {}
        self.lifetimes: dict[str, int] = {}

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.values[key] = value
        self.lifetimes[key] = ttl


def _answer(total: int, capped: bool, generation: dict = GENERATION) -> dict:
    return {"results": [], "count": 0, "total": total, "total_is_capped": capped,
            "ranking": dict(generation)}


def test_the_route_asks_for_the_count_on_the_first_page_and_keeps_it_for_the_others(monkeypatch):
    kept = _Kept()
    asked: list[dict | None] = []

    async def search(body, pool, known_count=None, count_budget_ms=None):
        asked.append(known_count)
        # As local_db_search_v3 does: the kept count if there is one.
        return _answer(known_count["qualifying"] - 1, True) if known_count else _answer(1000, True)

    monkeypatch.setattr(search_route._ls, "local_db_search_v3", search)

    async def scenario():
        run = search_route._search_counting_once
        first = await run(_body(), GENERATION, None, kept)
        second = await run(_body(**{"from": 50}), GENERATION, None, kept)
        last = await run(_body(**{"from": 950}), GENERATION, None, kept)
        # Another search has no count yet.
        other = await run(_body(min_score=76), GENERATION, None, kept)
        return first, second, last, other

    first, second, last, other = asyncio.run(scenario())
    counted = {"qualifying": 1001, **GENERATION}
    assert asked == [None, counted, counted, None]
    assert [r["total"] for r in (first, second, last, other)] == [1000, 1000, 1000, 1000]
    # Two searches, so two counts are kept, each for the search cache's hour.
    assert len(kept.values) == 2
    assert set(kept.lifetimes.values()) == {search_route.settings.ttl_search}


def test_a_count_that_was_read_is_not_kept_again_so_it_ends_with_its_hour(monkeypatch):
    kept = _Kept()

    async def search(body, pool, known_count=None, count_budget_ms=None):
        return _answer(37, False)

    monkeypatch.setattr(search_route._ls, "local_db_search_v3", search)

    async def scenario():
        run = search_route._search_counting_once
        await run(_body(), GENERATION, None, kept)
        writes = dict(kept.values)
        kept.lifetimes.clear()
        await run(_body(**{"from": 50}), GENERATION, None, kept)
        return writes

    writes = asyncio.run(scenario())
    assert kept.values == writes
    assert kept.lifetimes == {}, "reading a kept count must not start its hour again"


def test_a_page_that_shows_another_count_replaces_the_kept_one(monkeypatch):
    kept = _Kept()
    totals = iter([37, 36])

    async def search(body, pool, known_count=None, count_budget_ms=None):
        # The second page came back short: 36 matches, whatever was kept.
        return _answer(next(totals), False)

    monkeypatch.setattr(search_route._ls, "local_db_search_v3", search)

    async def scenario():
        run = search_route._search_counting_once
        await run(_body(), GENERATION, None, kept)
        kept.lifetimes.clear()
        await run(_body(**{"from": 20}), GENERATION, None, kept)

    asyncio.run(scenario())
    key = search_route._search_count_cache_key(_body(), GENERATION)
    assert json.loads(kept.values[key]) == {"qualifying": 36, **GENERATION}
    # It is a new count, so its hour starts now.
    assert kept.lifetimes == {key: search_route.settings.ttl_search}


def test_a_count_is_kept_under_the_generation_it_was_counted_on(monkeypatch):
    kept = _Kept()
    newer = {**GENERATION, "publication_sequence": 5}

    async def search(body, pool, known_count=None, count_budget_ms=None):
        # A publish landed between the route's look-up and the read.
        return _answer(12, False, newer)

    monkeypatch.setattr(search_route._ls, "local_db_search_v3", search)
    asyncio.run(search_route._search_counting_once(_body(), GENERATION, None, kept))
    assert list(kept.values) == [search_route._search_count_cache_key(_body(), newer)]
    assert json.loads(next(iter(kept.values.values()))) == {"qualifying": 12, **newer}


def test_with_no_cache_every_page_is_counted_as_before(monkeypatch):
    asked: list[dict | None] = []

    async def search(body, pool, known_count=None, count_budget_ms=None):
        asked.append(known_count)
        return _answer(1000, True)

    monkeypatch.setattr(search_route._ls, "local_db_search_v3", search)
    monkeypatch.setattr(search_route, "cache_get", lambda key, redis: _none())
    monkeypatch.setattr(search_route, "cache_set", lambda key, value, ttl, redis: _none())

    async def scenario():
        await search_route._search_counting_once(_body(), GENERATION, None, None)
        await search_route._search_counting_once(_body(**{"from": 50}), GENERATION, None, None)

    asyncio.run(scenario())
    assert asked == [None, None]


async def _none():
    return None


# ---------------------------------------------------------------------------
# Real PostgreSQL 18
# ---------------------------------------------------------------------------
def _search(connection, body: dict, known_count=None) -> dict:
    async def scenario():
        pool = await _api_pool(connection)
        try:
            return await local_search.local_db_search_v3(body, pool, known_count=known_count)
        finally:
            await pool.close()

    return asyncio.run(scenario())


def _request(**changes) -> dict:
    return {"reference_coords": {"x": 0.0, "y": 0.0, "z": 0.0},
            "filters": {"distance": {"max": RADIUS}},
            "sort_by": "distance", "size": 20, "from": 0, "reach_ly": 250,
            "archetype": PICKED, "min_score": 60, **changes}


def _refuse_to_count(monkeypatch):
    """Make any count that is asked for fail, loudly."""
    monkeypatch.setattr(
        local_search, "build_count_query",
        lambda *args, **kwargs: ("SELECT 1 / 0 AS a_count_was_asked_for", []))


@pytest.mark.db
def test_the_first_page_counts_and_the_others_use_that_count(reach_database, monkeypatch):
    connection = reach_database
    expected = _expected(connection, floor=60, reach=250)
    assert len(expected) > 60, "the search must need several pages"

    first = _search(connection, _request())
    assert first["total"] == len(expected)
    kept = count_to_keep(first)
    assert kept["qualifying"] == len(expected)

    # With the count kept, no page asks for it again: a count would fail.
    _refuse_to_count(monkeypatch)
    for offset in (20, 40):
        page = _search(connection, _request(**{"from": offset}), known_count=kept)
        assert [row["id64"] for row in page["results"]] == expected[offset:offset + 20]
        assert page["total"] == len(expected)
        assert page["total_is_capped"] is False


@pytest.mark.db
def test_a_count_from_another_generation_is_not_used(reach_database, monkeypatch):
    connection = reach_database
    expected = _expected(connection, floor=60, reach=250)
    first = _search(connection, _request())
    stale = {**count_to_keep(first), "qualifying": 7,
             "publication_sequence": first["ranking"]["publication_sequence"] + 1}
    # The wrong number is not shown: the page is counted again.
    assert _search(connection, _request(**{"from": 20}), known_count=stale)["total"] == len(expected)
    # And counting again is what happened: with counting refused, it fails.
    _refuse_to_count(monkeypatch)
    with pytest.raises(Exception, match="division by zero"):
        _search(connection, _request(**{"from": 20}), known_count=stale)


@pytest.mark.db
def test_a_page_that_holds_every_match_is_not_counted(reach_database, monkeypatch):
    connection = reach_database
    expected = _expected(connection, floor=88, reach=250)
    assert 0 < len(expected) < 200, "few enough to fit one page"
    _refuse_to_count(monkeypatch)
    page = _search(connection, _request(min_score=88, size=200))
    assert [row["id64"] for row in page["results"]] == expected
    assert page["total"] == len(expected)
    assert page["total_is_capped"] is False
    # The last page of a longer list, the same: it came back short.
    longer = _expected(connection, floor=60, reach=250)
    size = 50
    offset = (len(longer) // size) * size
    assert 0 < len(longer) - offset < size, "the last page must be a short one"
    last = _search(connection, _request(size=size, **{"from": offset}))
    assert [row["id64"] for row in last["results"]] == longer[offset:]
    assert last["total"] == len(longer)


@pytest.mark.db
def test_a_short_page_shows_its_own_count_whatever_was_kept(reach_database, monkeypatch):
    connection = reach_database
    longer = _expected(connection, floor=60, reach=250)
    size = 50
    offset = (len(longer) // size) * size
    first = _search(connection, _request(size=size))
    # A count kept across a night's load, three too many.
    out_of_date = {**count_to_keep(first), "qualifying": len(longer) + 3}
    _refuse_to_count(monkeypatch)
    last = _search(connection, _request(size=size, **{"from": offset}), known_count=out_of_date)
    assert [row["id64"] for row in last["results"]] == longer[offset:]
    assert last["total"] == len(longer)
    # And what would now be kept is the page's own count.
    assert count_to_keep(last)["qualifying"] == len(longer)


@pytest.mark.db
def test_a_kept_count_that_a_full_page_reaches_past_is_asked_again(reach_database, monkeypatch):
    connection = reach_database
    expected = _expected(connection, floor=60, reach=250)
    assert len(expected) > 60
    first = _search(connection, _request())
    # Kept as 25, but rows 21 to 40 exist: the page proves it too small.
    too_small = {**count_to_keep(first), "qualifying": 25}
    page = _search(connection, _request(**{"from": 20}), known_count=too_small)
    assert len(page["results"]) == 20
    assert page["total"] == len(expected)
    _refuse_to_count(monkeypatch)
    with pytest.raises(Exception, match="division by zero"):
        _search(connection, _request(**{"from": 20}), known_count=too_small)


@pytest.mark.db
def test_a_full_page_is_still_counted(reach_database, monkeypatch):
    connection = reach_database
    _refuse_to_count(monkeypatch)
    with pytest.raises(Exception, match="division by zero"):
        _search(connection, _request())


@pytest.mark.db
def test_a_search_with_no_matches_is_not_counted_twice(reach_database, monkeypatch):
    connection = reach_database
    _refuse_to_count(monkeypatch)
    # Nothing is this close to Sol in the seeded galaxy and fits.
    empty = _search(connection, _request(filters={"distance": {"max": 0.001}}, min_score=88))
    assert empty["results"] == [] and empty["total"] == 0
    assert empty["total_is_capped"] is False


@pytest.mark.db
def test_through_the_route_only_the_first_page_read_is_counted(reach_database, monkeypatch):
    """The route's keeping and the search's use of it, together, on real data."""
    connection = reach_database
    expected = _expected(connection, floor=60, reach=250)
    assert len(expected) > 60
    kept = _Kept()

    async def scenario():
        pool = await _api_pool(connection)
        try:
            generation = await resolve_published_generation(pool)
            run = search_route._search_counting_once
            first = await run(_request(), generation, pool, kept)
            # From here on a count that is asked for fails.
            _refuse_to_count(monkeypatch)
            second = await run(_request(**{"from": 20}), generation, pool, kept)
            other_size = await run(_request(size=7, **{"from": 40}), generation, pool, kept)
            with pytest.raises(Exception, match="division by zero"):
                # Another search (a higher tier) has no count yet.
                await run(_request(min_score=76), generation, pool, kept)
            return first, second, other_size
        finally:
            await pool.close()

    first, second, other_size = asyncio.run(scenario())
    assert [row["id64"] for row in first["results"]] == expected[:20]
    assert [row["id64"] for row in second["results"]] == expected[20:40]
    assert [row["id64"] for row in other_size["results"]] == expected[40:47]
    assert first["total"] == second["total"] == other_size["total"] == len(expected)
    # One search was counted, so one count is kept.
    assert len(kept.values) == 1
