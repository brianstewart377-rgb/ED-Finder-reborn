"""Find: body filters galaxy-wide.

Without a system to search near, filters thin the window of the best 10,000
systems in reach (or, for a picked type, the type's best 10,000): the window
walk is the unfiltered one, a filtered page never reads more than the window,
and the count is taken over the window: exact below 1,000, and stopped at
1,001 ("1,000+") above, as Find counts everywhere. Real PostgreSQL 18 with the
real migrations, and production-sized statistics for the plans.

The database layers need RATINGS_V4_VALIDATION_DATABASE_URL. CI sets
EDF_DB_TESTS_REQUIRED=1 so they cannot be skipped there.
"""
from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://find-galaxy-filters-test.invalid")

from edfinder_api.ranking.profile import (  # noqa: E402
    ARCHETYPE_KEYS,
    FIND_COUNT_LIMIT,
    PROFILE_SPEC,
    RANKING_COUNT_SENTINEL,
    RANKING_WINDOW_LIMIT,
)
from edfinder_api.ranking.ranking_sql import (  # noqa: E402
    ARCHETYPE_COLUMNS,
    build_count_query,
    build_ranked_query,
)
from tests.test_find_reach import (  # noqa: E402,F401  (the shared fixture)
    _api_pool,
    _production_reach_statistics,
    _scans,
    reach_database,
    shown,
)

PICKED = ARCHETYPE_KEYS[0]
ONLY_O = {"main_star_class_in": ["O"]}
ONLY_K = {"main_star_class_in": ["K"]}
PAGE = 50


def galaxy(*, picked: str | None, filters: dict, limit: int = PAGE, offset: int = 0):
    return build_ranked_query(
        PROFILE_SPEC, picked_archetype=picked, picked_economy=None,
        hard_filters=filters, reference_coords=None, limit=limit, offset=offset,
        min_score=60 if picked else None, hide_claimed=True, reach_ly=250)


def galaxy_count(*, picked: str | None, filters: dict):
    return build_count_query(
        PROFILE_SPEC, picked_archetype=picked, picked_economy=None,
        hard_filters=filters, reference_coords=None,
        cap=None if picked else FIND_COUNT_LIMIT,
        min_score=60 if picked else None, hide_claimed=True, reach_ly=250)


def _window(connection, picked: str | None, limit: int | None = RANKING_WINDOW_LIMIT) -> list[int]:
    """The best 10,000 shown systems (or every one, with no limit),
    unfiltered, straight from the views."""
    if picked:
        columns = ARCHETYPE_COLUMNS[picked]
        statement = (
            "SELECT a.system_id64 FROM v3_app.system_archetype a "
            f"WHERE a.{columns['score']} >= 60 "
            f"ORDER BY a.{columns['score']} DESC, a.{columns['weighted_key']} DESC, "
            "a.system_id64 ASC")
    else:
        statement = (
            "SELECT sum.system_id64 FROM v3_app.system_archetype_summary sum "
            "ORDER BY sum.best_colony_potential DESC, "
            "sum.weighted_potential DESC NULLS LAST, sum.system_id64 ASC")
    ordered = [int(r[0]) for r in connection.execute(statement).fetchall()]
    return [i for i in ordered if shown(i, 250)][:limit]


def _star(connection, star_class: str) -> set[int]:
    return {int(r[0]) for r in connection.execute(
        "SELECT system_id64 FROM v3_app.system_search WHERE main_star_class = %s",
        (star_class,)).fetchall()}


def _star_o(connection) -> set[int]:
    return _star(connection, "O")


def _all_ids(connection, *, picked: str | None, filters: dict) -> list[int]:
    """Every page Find serves, as deep as it pages."""
    from tests.helpers.finder_seed import run_statement

    ids: list[int] = []
    for offset in range(0, FIND_COUNT_LIMIT, PAGE):
        sql, params = galaxy(picked=picked, filters=filters, offset=offset)
        rows = run_statement(connection, f"SELECT p.system_id64 FROM ({sql}) p", params)
        ids.extend(int(r[0]) for r in rows)
        if len(rows) < PAGE:
            break
    return ids


@pytest.mark.db
@pytest.mark.parametrize("picked", [None, PICKED], ids=["any", "type"])
def test_filters_thin_the_window_and_the_count_is_exact(reach_database, picked):
    """Few matches (O stars, one system in 97): every page, and an exact count."""
    from tests.helpers.finder_seed import run_statement

    connection = reach_database
    window = _window(connection, picked)
    star_o = _star_o(connection)
    expected = [i for i in window if i in star_o]
    assert 0 < len(expected) < len(window) and len(expected) < FIND_COUNT_LIMIT
    if picked is None:
        # Only the window's: the whole ranking holds more O stars than it does.
        # (The picked type's window holds every system that fits it here.)
        everywhere = _window(connection, None, limit=None)
        assert len(everywhere) > RANKING_WINDOW_LIMIT
        assert len(expected) < len([i for i in everywhere if i in star_o])

    assert _all_ids(connection, picked=picked, filters=ONLY_O) == expected
    sql, params = galaxy_count(picked=picked, filters=ONLY_O)
    assert run_statement(connection, sql, params)[0][0] == len(expected)


@pytest.mark.db
@pytest.mark.parametrize("picked", [None, PICKED], ids=["any", "type"])
def test_a_filtered_count_stops_at_1000_and_more(reach_database, picked):
    """Many matches (K stars, nearly all): the pages end at 1,000, all from
    the window, and the count stops at 1,001, which Find shows as "1,000+"."""
    from tests.helpers.finder_seed import run_statement

    connection = reach_database
    star_k = _star(connection, "K")
    thinned = [i for i in _window(connection, picked) if i in star_k]
    assert len(thinned) > FIND_COUNT_LIMIT

    assert _all_ids(connection, picked=picked, filters=ONLY_K) == thinned[:FIND_COUNT_LIMIT]
    sql, params = galaxy_count(picked=picked, filters=ONLY_K)
    assert run_statement(connection, sql, params)[0][0] == RANKING_COUNT_SENTINEL


@pytest.mark.db
@pytest.mark.parametrize("picked", [None, PICKED], ids=["any", "type"])
@pytest.mark.parametrize("build", ["page", "count"])
def test_the_filtered_window_keeps_its_index_walk_at_production_size(reach_database, picked, build):
    from tests.helpers import finder_seed

    connection = reach_database
    finder_seed.import_production_statistics(connection)
    _production_reach_statistics(connection)
    make = galaxy if build == "page" else galaxy_count
    plain = finder_seed.generic_plan(connection, *make(picked=picked, filters={}))
    filtered = finder_seed.generic_plan(connection, *make(picked=picked, filters=ONLY_O))
    big = {"system_search", "system_archetype"}
    plain_scans, filtered_scans = _scans(plain, big), _scans(filtered, big)
    assert not any(kind == "Seq Scan" for kind, *_ in filtered_scans), filtered_scans
    # The window walk over the archetype rows is the unfiltered one.
    window_index = {s for s in plain_scans if s[1] == "system_archetype"}
    assert window_index and window_index <= set(filtered_scans)


@pytest.mark.db
def test_the_search_answers_filters_and_a_type_galaxy_wide(reach_database):
    from edfinder_api import local_search

    connection = reach_database
    star_o = _star_o(connection)

    async def scenario():
        pool = await _api_pool(connection)
        try:
            any_type = await local_search.local_db_search_v3(
                {"galaxy_wide": True, "size": PAGE, "from": 0, "reach_ly": 250,
                 "star_types": ["O"]}, pool)
            one_type = await local_search.local_db_search_v3(
                {"galaxy_wide": True, "size": PAGE, "from": 0, "reach_ly": 250,
                 "star_types": ["O"], "archetype": PICKED, "min_score": 60}, pool)
        finally:
            await pool.close()
        return any_type, one_type

    any_type, one_type = asyncio.run(scenario())
    expected_any = [i for i in _window(connection, None) if i in star_o]
    expected_type = [i for i in _window(connection, PICKED) if i in star_o]
    assert [r["id64"] for r in any_type["results"]] == expected_any[:PAGE]
    assert any_type["total"] == len(expected_any) and any_type["total_is_capped"] is False
    assert [r["id64"] for r in one_type["results"]] == expected_type[:PAGE]
    assert one_type["total"] == len(expected_type) and one_type["total_is_capped"] is False

    columns = ARCHETYPE_COLUMNS[PICKED]
    stored = {int(i): (int(score), int(overall)) for i, score, overall in connection.execute(
        f"SELECT system_id64, {columns['score']}, best_colony_potential "
        "FROM v3_app.system_archetype WHERE system_id64 = ANY(%s)",
        ([r["id64"] for r in one_type["results"]],)).fetchall()}
    for row in one_type["results"]:
        score, overall = stored[row["id64"]]
        # The type's fit is the selected score; the overall potential stays
        # in its own field.
        assert row["selected_archetype"] == PICKED
        assert row["selected_archetype_score"] == score
        assert row["archetype_score"] == overall
