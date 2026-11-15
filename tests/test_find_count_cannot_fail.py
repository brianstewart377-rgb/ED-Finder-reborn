"""Find: a count that runs out of time does not take the page with it.

Counting is the costly half of a search for one colony type near a system: it
walks outwards to the 1,001st match. On the Pi on 15 November 2026 the first
"Manufacturing Hub near Sol" after the weekly backup answered 503 after 18 s:
the count had passed PostgreSQL's 15 s statement limit, and that ended the
whole search, though its page had been read in under a second.

So the count is asked last, in a savepoint of its own, and is given a short
first wait. A count that is not in by then is given up: the page is answered
with ``total_is_counted`` false, and the page asks again for the count alone
(``wait_for_count``), which gets the whole statement limit. Whatever
happens to the count, the rows are answered.

Checked in three layers: what the rules mean, with no database; real
PostgreSQL 18 with the real migrations and a count that really does run out
of time; and what the route keeps.

The database layer needs RATINGS_V4_VALIDATION_DATABASE_URL. CI sets
EDF_DB_TESTS_REQUIRED=1 so it cannot be skipped there.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://find-count-cannot-fail-test.invalid")

from edfinder_api import local_search  # noqa: E402
from edfinder_api.local_search import count_to_keep, count_within  # noqa: E402
from edfinder_api.models import LocalSearchRequest, SearchResponse  # noqa: E402
from edfinder_api.routers import search as search_route  # noqa: E402
from tests.test_find_count_once import (  # noqa: E402
    GENERATION,
    _body,
    _Kept,
    _request,
)
from tests.test_find_reach import (  # noqa: E402,F401  (the shared fixture)
    _api_pool,
    reach_database,
)
from tests.test_find_type_near import _expected  # noqa: E402

# A count that takes five seconds, and one that is in at once.
SLOW_COUNT = ("SELECT count(*) FROM (SELECT pg_sleep(5)) AS a_slow_count", [])
BUDGET_MS = 150


# ---------------------------------------------------------------------------
# What the rules mean
# ---------------------------------------------------------------------------
def test_nothing_is_kept_of_a_search_that_was_not_counted():
    uncounted = {"results": [], "count": 20, "total": 20, "total_is_capped": False,
                 "total_is_counted": False, "ranking": dict(GENERATION)}
    assert count_to_keep(uncounted) is None
    # The same answer, counted, is kept as before; so is one from before the
    # field existed.
    assert count_to_keep({**uncounted, "total_is_counted": True}) == {"qualifying": 20, **GENERATION}
    counted_before = {name: value for name, value in uncounted.items() if name != "total_is_counted"}
    assert count_to_keep(counted_before) == {"qualifying": 20, **GENERATION}


def test_the_first_wait_for_a_count_is_short_and_a_page_can_ask_for_the_whole_limit(monkeypatch):
    monkeypatch.setattr(search_route.settings, "statement_timeout_ms", 15000)
    monkeypatch.setattr(search_route.settings, "find_count_first_wait_ms", 1000)
    assert search_route._count_budget_ms(False) == 1000
    assert search_route._count_budget_ms(True) == 15000
    # Never more than the statement limit, and never nothing.
    monkeypatch.setattr(search_route.settings, "find_count_first_wait_ms", 60000)
    assert search_route._count_budget_ms(False) == 15000
    monkeypatch.setattr(search_route.settings, "find_count_first_wait_ms", 0)
    assert search_route._count_budget_ms(False) == 1


def test_a_request_does_not_wait_for_the_count_unless_it_says_so():
    assert LocalSearchRequest(galaxy_wide=True).wait_for_count is False
    assert LocalSearchRequest(galaxy_wide=True, wait_for_count=True).wait_for_count is True
    # The answer says whether its total is a count; an older answer (one
    # kept in the cache from before this, say) says nothing, which reads as
    # counted.
    fields = SearchResponse.model_fields
    assert "total_is_counted" in fields
    assert fields["total_is_counted"].default is None
    assert fields["total_is_counted"].is_required() is False


def test_the_route_keeps_nothing_of_a_count_that_ran_out_of_time(monkeypatch):
    kept = _Kept()
    budgets: list[int | None] = []

    async def search(body, pool, known_count=None, count_budget_ms=None):
        budgets.append(count_budget_ms)
        # The first asking runs out of time; the second, given longer, counts.
        if count_budget_ms == 1000:
            return {"results": [], "count": 50, "total": 50, "total_is_capped": False,
                    "total_is_counted": False, "ranking": dict(GENERATION)}
        return {"results": [], "count": 50, "total": 312, "total_is_capped": False,
                "total_is_counted": True, "ranking": dict(GENERATION)}

    monkeypatch.setattr(search_route._ls, "local_db_search_v3", search)

    async def scenario():
        run = search_route._search_counting_once
        first = await run(_body(), GENERATION, None, kept, count_budget_ms=1000)
        nothing_kept_yet = dict(kept.values)
        again = await run(_body(), GENERATION, None, kept, count_budget_ms=15000)
        return first, nothing_kept_yet, again

    first, nothing_kept_yet, again = asyncio.run(scenario())
    assert first["total_is_counted"] is False and nothing_kept_yet == {}
    assert again["total"] == 312
    assert budgets == [1000, 15000]
    # Now the count is kept, for the search's other pages.
    assert [json.loads(value) for value in kept.values.values()] == [{"qualifying": 312, **GENERATION}]


# ---------------------------------------------------------------------------
# Real PostgreSQL 18
# ---------------------------------------------------------------------------
def _search(connection, body: dict, **more) -> tuple[dict, float]:
    async def scenario():
        pool = await _api_pool(connection)
        try:
            began = time.monotonic()
            answer = await local_search.local_db_search_v3(body, pool, **more)
            return answer, time.monotonic() - began
        finally:
            await pool.close()

    return asyncio.run(scenario())


def _make_the_count_slow(monkeypatch):
    monkeypatch.setattr(local_search, "build_count_query", lambda *args, **kwargs: SLOW_COUNT)


@pytest.mark.db
def test_a_count_that_runs_out_of_time_still_answers_the_page(reach_database, monkeypatch):
    connection = reach_database
    expected = _expected(connection, floor=60, reach=250)
    assert len(expected) > 60, "the search must need several pages"
    _make_the_count_slow(monkeypatch)

    first, took = _search(connection, _request(), count_budget_ms=BUDGET_MS)
    # The rows are there, in order, and the answer came long before the
    # count would have.
    assert [row["id64"] for row in first["results"]] == expected[:20]
    assert took < 3
    assert first["total_is_counted"] is False
    # Not a count: how many matches the page shows there to be.
    assert first["total"] == 20 and first["total_is_capped"] is False
    # The claim notes are read before the count, so they are not lost either.
    assert all("claim_status" in row for row in first["results"])
    assert count_to_keep(first) is None

    third, _ = _search(connection, _request(**{"from": 40}), count_budget_ms=BUDGET_MS)
    assert [row["id64"] for row in third["results"]] == expected[40:60]
    assert third["total_is_counted"] is False and third["total"] == 60


@pytest.mark.db
def test_a_count_that_is_in_on_time_is_reported_as_a_count(reach_database):
    connection = reach_database
    expected = _expected(connection, floor=60, reach=250)
    answer, _ = _search(connection, _request(), count_budget_ms=15000)
    assert answer["total"] == len(expected)
    assert answer["total_is_counted"] is True
    assert count_to_keep(answer)["qualifying"] == len(expected)
    # And with no budget named at all, as every other caller asks.
    plain, _ = _search(connection, _request())
    assert plain["total"] == len(expected) and plain["total_is_counted"] is True


@pytest.mark.db
def test_a_page_that_shows_its_own_count_or_has_one_kept_never_waits(reach_database, monkeypatch):
    connection = reach_database
    expected = _expected(connection, floor=60, reach=250)
    _make_the_count_slow(monkeypatch)
    # The last page is short: it shows the count itself.
    last_offset = (len(expected) // 20) * 20
    if last_offset == len(expected):
        last_offset -= 20
    short, took = _search(connection, _request(**{"from": last_offset, "size": 50}),
                          count_budget_ms=BUDGET_MS)
    assert short["total"] == len(expected) and short["total_is_counted"] is True
    assert took < 3
    # A full page with the count kept from an earlier one.
    generation = short["ranking"]
    kept = {"qualifying": len(expected),
            "derived_generation_id": generation["derived_generation_id"],
            "publication_sequence": generation["publication_sequence"]}
    full, took = _search(connection, _request(), known_count=kept, count_budget_ms=BUDGET_MS)
    assert full["total"] == len(expected) and full["total_is_counted"] is True
    assert took < 3


@pytest.mark.db
def test_the_connections_own_time_limit_ends_a_count_and_not_the_search(reach_database):
    """With no budget named, PostgreSQL's statement limit is what ends a long
    count. That used to end the whole transaction; the savepoint keeps it."""
    connection = reach_database

    async def scenario():
        pool = await _api_pool(connection)
        try:
            async with pool.acquire() as conn:
                await conn.execute("SET statement_timeout = 150")
                async with conn.transaction(isolation="repeatable_read", readonly=True):
                    before = await conn.fetchval("SELECT 1")
                    began = time.monotonic()
                    counted = await count_within(conn, *SLOW_COUNT, None)
                    took = time.monotonic() - began
                    # The transaction is alive, and its limit is as it was.
                    after = await conn.fetchval("SELECT 2")
                    limit = await conn.fetchval("SHOW statement_timeout")
                await conn.execute("RESET statement_timeout")
                return before, counted, took, after, limit
        finally:
            await pool.close()

    before, counted, took, after, limit = asyncio.run(scenario())
    assert (before, counted, after) == (1, None, 2)
    assert took < 3
    assert limit == "150ms"


@pytest.mark.db
def test_a_budget_shortens_the_count_and_is_gone_when_the_count_is_given_up(reach_database):
    connection = reach_database

    async def scenario():
        pool = await _api_pool(connection)
        try:
            async with pool.acquire() as conn:
                async with conn.transaction(isolation="repeatable_read", readonly=True):
                    limit_before = await conn.fetchval("SHOW statement_timeout")
                    began = time.monotonic()
                    given_up = await count_within(conn, *SLOW_COUNT, BUDGET_MS)
                    took = time.monotonic() - began
                    limit_after = await conn.fetchval("SHOW statement_timeout")
                    counted = await count_within(conn, "SELECT 41 + $1::int", [1], 5000)
                return limit_before, given_up, took, limit_after, counted
        finally:
            await pool.close()

    limit_before, given_up, took, limit_after, counted = asyncio.run(scenario())
    assert given_up is None and took < 3
    # Rolled back with the savepoint: what follows has the limit it had.
    assert limit_after == limit_before
    assert counted == 42


@pytest.mark.db
def test_a_count_that_fails_for_another_reason_is_still_an_error(reach_database, monkeypatch):
    """Only running out of time is forgiven. A broken count must be seen."""
    connection = reach_database
    monkeypatch.setattr(
        local_search, "build_count_query",
        lambda *args, **kwargs: ("SELECT 1 / 0 AS a_broken_count", []))
    with pytest.raises(Exception, match="division by zero"):
        _search(connection, _request(), count_budget_ms=BUDGET_MS)


@pytest.mark.db
def test_through_the_route_a_slow_count_is_asked_again_and_then_kept(reach_database, monkeypatch):
    """The first asking answers the page uncounted and keeps nothing; the
    page's follow-up, given time, counts; from then on no page counts."""
    connection = reach_database
    expected = _expected(connection, floor=60, reach=250)
    kept = _Kept()
    real_count_query = local_search.build_count_query

    async def scenario():
        pool = await _api_pool(connection)
        try:
            generation = await local_search.resolve_published_generation(pool)
            run = search_route._search_counting_once
            _make_the_count_slow(monkeypatch)
            first = await run(_request(), generation, pool, kept, count_budget_ms=BUDGET_MS)
            kept_after_first = dict(kept.values)
            # The count is quick again (as it is once the data is in memory).
            monkeypatch.setattr(local_search, "build_count_query", real_count_query)
            follow_up = await run(_request(), generation, pool, kept, count_budget_ms=15000)
            # From here on a count that is asked for fails.
            monkeypatch.setattr(
                local_search, "build_count_query",
                lambda *args, **kwargs: ("SELECT 1 / 0 AS a_count_was_asked_for", []))
            second_page = await run(_request(**{"from": 20}), generation, pool, kept,
                                    count_budget_ms=BUDGET_MS)
            return first, kept_after_first, follow_up, second_page
        finally:
            await pool.close()

    first, kept_after_first, follow_up, second_page = asyncio.run(scenario())
    assert first["total_is_counted"] is False and first["total"] == 20
    assert kept_after_first == {}
    assert follow_up["total_is_counted"] is True and follow_up["total"] == len(expected)
    assert [row["id64"] for row in follow_up["results"]] == expected[:20]
    assert second_page["total"] == len(expected) and second_page["total_is_counted"] is True
    assert len(kept.values) == 1
