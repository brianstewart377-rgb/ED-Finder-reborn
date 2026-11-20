"""Find leaves out the systems in its list of fake systems.

Two layers, as for the claim and reach rules (tests/test_find_claim_rule.py,
tests/test_find_reach.py):

* the SQL the builder writes (no database): Find's view adds the list once to
  each query shape, after the claim and reach rules, and nothing else;
* real PostgreSQL 18 with the real migrations: a listed system is gone from
  every page and every count, and the pages are otherwise unchanged.

The database layer needs RATINGS_V4_VALIDATION_DATABASE_URL (a local
PostgreSQL 18 database named ratings_v4_validation). CI sets
EDF_DB_TESTS_REQUIRED=1 so it cannot be skipped there.
"""
from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://find-fake-systems-test.invalid")

from edfinder_api import claim_rule, fake_systems, reach_rule  # noqa: E402
from edfinder_api.ranking.profile import (  # noqa: E402
    FIND_COUNT_LIMIT,
    PROFILE_SPEC,
    RANKING_COUNT_SENTINEL,
    RANKING_WINDOW_LIMIT,
)
from edfinder_api.ranking.ranking_sql import (  # noqa: E402
    build_count_query,
    build_ranked_query,
)
from tests.test_find_reach import PICKED, SHAPES  # noqa: E402

TEST_SYSTEM = 7780433924818


def page(shape: str, *, hide: bool, reach: int | None = None):
    kwargs = SHAPES[shape][0]
    return build_ranked_query(
        PROFILE_SPEC, picked_economy=None, limit=50, offset=0,
        hide_claimed=hide, reach_ly=reach, **kwargs)


def count(shape: str, *, hide: bool, reach: int | None = None):
    kwargs, _, _, capped = SHAPES[shape]
    extra = {"cap": FIND_COUNT_LIMIT} if capped else {}
    return build_count_query(
        PROFILE_SPEC, picked_economy=None, hide_claimed=hide, reach_ly=reach,
        **extra, **kwargs)


# ---------------------------------------------------------------------------
# The SQL the builder writes
# ---------------------------------------------------------------------------
def test_the_list_holds_the_test_system_next_to_sol():
    assert fake_systems.FAKE_SYSTEMS[TEST_SYSTEM] == "Test"
    assert fake_systems.not_fake_sql("s.system_id64") == "s.system_id64 NOT IN (7780433924818)"


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("build", [page, count], ids=["page", "count"])
def test_only_finds_view_leaves_them_out(shape, build):
    off_sql, _ = build(shape, hide=False, reach=reach_rule.REACH_NEAR_LY)
    on_sql, _ = build(shape, hide=True, reach=reach_rule.REACH_NEAR_LY)
    assert str(TEST_SYSTEM) not in off_sql
    alias = SHAPES[shape][1 if build is page else 2]
    fake = fake_systems.not_fake_sql(f"{alias}.system_id64")
    assert on_sql.count(fake) == 1, on_sql
    assert on_sql.count(str(TEST_SYSTEM)) == 1
    # Last, after the claim rule and the reach rule.
    claim = claim_rule.not_claimed_sql(f"{alias}.system_id64")
    reach = reach_rule.in_reach_sql(f"{alias}.system_id64", reach_rule.REACH_NEAR_LY)
    assert on_sql.index(claim) < on_sql.index(reach) < on_sql.index(fake)


@pytest.mark.parametrize("shape", ["picked_archetype", "galaxy_wide"])
def test_the_list_is_inside_the_bounded_window(shape):
    """A window holds the best 10,000 real systems, so the list is applied
    before the window's LIMIT, as the claim rule is."""
    page_sql, _ = page(shape, hide=True)
    count_sql, _ = count(shape, hide=True)
    assert page_sql.index(str(TEST_SYSTEM)) < page_sql.index(f"LIMIT {RANKING_WINDOW_LIMIT}\n")
    assert count_sql.index(str(TEST_SYSTEM)) < count_sql.index(f"LIMIT {RANKING_COUNT_SENTINEL}\n")


@pytest.mark.parametrize("shape", ["nearest_first", "nearest_first_summary_filter"])
def test_the_list_is_inside_the_nearest_first_window(shape):
    """The made-up "Test" is the nearest system to Sol, so it is left out
    before the nearest-first LIMIT, or a page near Sol comes back one short."""
    for sql, _ in (page(shape, hide=True), count(shape, hide=True)):
        assert sql.index(str(TEST_SYSTEM)) < sql.index("ORDER BY s.position_ly <->")


# ---------------------------------------------------------------------------
# Real PostgreSQL 18
# ---------------------------------------------------------------------------
SYSTEMS = 2_000
# Few enough seeded systems are this near Sol that the nearest-first count is
# exact, not "1,000+".
RADIUS = 400


@pytest.fixture(scope="module")
def fake_database():
    from tests.helpers import finder_seed
    from tests.ratings_v4_pg_fixture import canonical_database

    with canonical_database() as database:
        connection = database[0]
        finder_seed.build_finder_database(connection, systems=SYSTEMS)
        yield connection


def _ids(response: dict) -> list[int]:
    return [int(result["id64"]) for result in response["results"]]


@pytest.mark.db
def test_find_leaves_a_listed_system_out_of_every_page_and_count(fake_database, monkeypatch):
    from edfinder_api import local_search
    from edfinder_api.routers import archetypes
    from tests.test_find_claim_rule import _api_pool
    from tests.test_find_reach import within

    connection = fake_database

    async def searches():
        pool = await _api_pool(connection)
        try:
            galaxy = await local_search.local_db_search_v3(
                {"galaxy_wide": True, "size": 50, "from": 0}, pool)
            nearest = await local_search.local_db_search_v3(
                {"reference_coords": {"x": 0.0, "y": 0.0, "z": 0.0},
                 "filters": {"distance": {"max": RADIUS}},
                 "sort_by": "distance", "size": 50, "from": 0}, pool)
            ranked = await archetypes._archetype_rankings_v3(
                pool=pool, archetype=PICKED, min_score=60, limit=50, offset=0,
                galaxy_region=None, max_distance_ly=None, has_elw=None,
                min_slots=None, max_contamination=None)
        finally:
            await pool.close()
        return galaxy, nearest, ranked

    before = asyncio.run(searches())
    near = within(connection, RADIUS)
    assert 50 < len(near) < FIND_COUNT_LIMIT
    nearest_before = before[1]
    assert nearest_before["total"] == len(near) and nearest_before["total_is_capped"] is False

    # Pretend the first system of each page is fake.
    listed = {_ids(response)[0] for response in before}
    monkeypatch.setattr(fake_systems, "FAKE_SYSTEMS", {i: "made up" for i in listed})
    after = asyncio.run(searches())

    for old, new in zip(before, after, strict=True):
        assert len(_ids(new)) == len(_ids(old)) == 50
        assert not listed & set(_ids(new))
        kept = [i for i in _ids(old) if i not in listed]
        # The same systems in the same order, the page filled from beyond.
        assert _ids(new)[: len(kept)] == kept
    nearest_after = after[1]
    assert nearest_after["total"] == len(near) - len(listed & set(near))
    assert nearest_after["total_is_capped"] is False
