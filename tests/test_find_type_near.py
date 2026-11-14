"""Find: a colony type near a system.

A picked type with an anchor and a radius walks the nearest-first window, with
the type's score floor as one more condition on each row, and lists the
systems nearest first with the type's score and tier. Three layers, as for the
claim and reach rules:

* the SQL the builder writes (no database);
* real PostgreSQL 18 with the real migrations, against an independent query
  over the views;
* production-sized statistics: the walk uses the position index and reads
  the archetype rows by key, never a full scan.

The database layers need RATINGS_V4_VALIDATION_DATABASE_URL. CI sets
EDF_DB_TESTS_REQUIRED=1 so they cannot be skipped there.
"""
from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://find-type-near-test.invalid")

from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from edfinder_api.ranking.profile import (  # noqa: E402
    ACCEPTED_ARCHETYPE_SCORE_FLOORS,
    ARCHETYPE_KEYS,
    PROFILE_SPEC,
    RANKING_WINDOW_LIMIT,
)
from edfinder_api.ranking.ranking_sql import (  # noqa: E402
    ARCHETYPE_COLUMNS,
    SORT_DISTANCE,
    SORT_SCORE,
    build_count_query,
    build_ranked_query,
)
from tests.test_find_reach import (  # noqa: E402,F401  (the shared fixture)
    _api_pool,
    reach_database,
    shown,
)

ANCHOR = (0.0, 0.0, 0.0)
RADIUS = 250.0
# A small page, so every radius spans several pages.
PAGE = 20
PICKED = ARCHETYPE_KEYS[0]


def near(*, floor: int = 60, reach: int | None = 250, filters: dict | None = None,
         limit: int = 50, offset: int = 0, radius: float = RADIUS, archetype: str = PICKED):
    return build_ranked_query(
        PROFILE_SPEC, picked_archetype=archetype, picked_economy=None,
        hard_filters={"max_distance_ly": radius, **(filters or {})},
        reference_coords=ANCHOR, limit=limit, offset=offset, sort=SORT_DISTANCE,
        min_score=floor, hide_claimed=True, reach_ly=reach)


def near_count(*, floor: int = 60, reach: int | None = 250, filters: dict | None = None,
               radius: float = RADIUS):
    return build_count_query(
        PROFILE_SPEC, picked_archetype=PICKED, picked_economy=None,
        hard_filters={"max_distance_ly": radius, **(filters or {})},
        reference_coords=ANCHOR, cap=RANKING_WINDOW_LIMIT, sort=SORT_DISTANCE,
        min_score=floor, hide_claimed=True, reach_ly=reach)


# ---------------------------------------------------------------------------
# The SQL the builder writes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("floor", ACCEPTED_ARCHETYPE_SCORE_FLOORS)
def test_the_floor_and_the_rules_are_inside_the_nearest_first_window(floor):
    columns = ARCHETYPE_COLUMNS[PICKED]
    for sql, _ in (near(floor=floor), near_count(floor=floor)):
        window_end = sql.index("ORDER BY s.position_ly <->")
        assert sql.index(f"sum.{columns['score']} >= {floor}") < window_end
        assert sql.index("v3_state.system_state") < window_end
        assert sql.index("v3_state.system_reach") < window_end
        assert "JOIN v3_app.system_archetype sum ON sum.system_id64 = s.system_id64" in sql
        # Never the type's galaxy-wide window, never a sort of the radius by score.
        assert "archetype_window" not in sql
        assert "LIMIT NULLIF" not in sql


def test_the_page_carries_the_type_score_and_tier_and_goes_nearest_first():
    columns = ARCHETYPE_COLUMNS[PICKED]
    sql, _ = near()
    assert f"sum.{columns['score']} AS selected_archetype_score" in sql
    assert f"sum.{columns['tier']} AS selected_archetype_tier" in sql
    assert sql.rstrip().split("\n")[-2] == (
        "ORDER BY nearest.distance_ly ASC, "
        "nearest.selected_archetype_weighted_e4 DESC, nearest.system_id64 ASC"
    )


def test_body_filters_join_the_walk():
    sql, params = near(filters={"elw_count_min": 1, "main_star_class_in": ["K"]})
    assert "s.elw_count >= " in sql
    assert "s.main_star_class IN (" in sql
    assert 1 in params and "K" in params


def test_a_type_near_a_system_is_only_ever_nearest_first():
    with pytest.raises(ValueError, match="sorted by distance"):
        build_ranked_query(
            PROFILE_SPEC, picked_archetype=PICKED, picked_economy=None,
            hard_filters={"max_distance_ly": RADIUS}, reference_coords=ANCHOR,
            limit=50, offset=0, sort=SORT_SCORE, min_score=60)
    with pytest.raises(ValueError, match="count cap"):
        build_count_query(
            PROFILE_SPEC, picked_archetype=PICKED, picked_economy=None,
            hard_filters={"max_distance_ly": RADIUS}, reference_coords=ANCHOR,
            sort=SORT_DISTANCE, min_score=60)
    # Galaxy-wide, the type keeps its own window and still refuses filters.
    with pytest.raises(ValueError, match="hard filters"):
        build_ranked_query(
            PROFILE_SPEC, picked_archetype=PICKED, picked_economy=None,
            hard_filters={"main_star_class_in": ["K"]}, reference_coords=None,
            limit=50, offset=0, min_score=60)


def test_the_request_takes_a_type_only_near_a_system():
    from edfinder_api.local_search import picked_from_body, prepare_local_search_v3
    from edfinder_api.models import LocalSearchRequest

    anchored = {
        "reference_coords": {"x": 0.0, "y": 0.0, "z": 0.0},
        "filters": {"distance": {"max": 250}},
        "sort_by": "distance",
    }
    assert picked_from_body({**anchored, "archetype": PICKED}) == (PICKED, 60)
    assert picked_from_body({**anchored, "archetype": PICKED, "min_score": 88}) == (PICKED, 88)
    assert picked_from_body({**anchored, "archetype": "any"}) == (None, None)
    assert picked_from_body(anchored) == (None, None)
    prepare_local_search_v3({**anchored, "archetype": PICKED, "min_score": 76})

    refused = [
        {**anchored, "archetype": "nonsense"},
        {**anchored, "archetype": PICKED, "min_score": 70},
        {**anchored, "min_score": 60},
        {**anchored, "archetype": PICKED, "sort_by": "development"},
        {"galaxy_wide": True, "archetype": PICKED},
        {"archetype": PICKED, "sort_by": "distance"},
    ]
    for body in refused:
        with pytest.raises(HTTPException) as caught:
            picked_from_body(body)
        assert caught.value.status_code == 422, body
    # The floor narrows the walk like a hard filter, so the 500 ly cap applies.
    with pytest.raises(HTTPException) as caught:
        prepare_local_search_v3(
            {**anchored, "filters": {"distance": {"max": 1000}}, "archetype": PICKED})
    assert caught.value.status_code == 422
    prepare_local_search_v3({**anchored, "filters": {"distance": {"max": 1000}}})

    assert LocalSearchRequest(archetype=PICKED, min_score=76).min_score == 76
    with pytest.raises(ValidationError):
        LocalSearchRequest(archetype=PICKED, min_score=70)


# ---------------------------------------------------------------------------
# Real PostgreSQL 18
# ---------------------------------------------------------------------------
def _expected(connection, *, floor: int, reach: int, radius: float = RADIUS,
              only_k: bool = False) -> list[int]:
    """Straight from the views, not through the builder."""
    columns = ARCHETYPE_COLUMNS[PICKED]
    star = "AND s.main_star_class = 'K' " if only_k else ""
    rows = connection.execute(
        "SELECT s.system_id64 FROM v3_app.system_search s "
        "JOIN v3_app.system_archetype a ON a.system_id64 = s.system_id64 "
        f"WHERE a.{columns['score']} >= %s {star}"
        "AND sqrt(s.x_ly ^ 2 + s.y_ly ^ 2 + s.z_ly ^ 2) <= %s "
        "ORDER BY sqrt(s.x_ly ^ 2 + s.y_ly ^ 2 + s.z_ly ^ 2), "
        f"a.{columns['weighted_key']} DESC, s.system_id64",
        (floor, radius),
    ).fetchall()
    return [int(r[0]) for r in rows if shown(int(r[0]), reach)]


def _paged(connection, **kwargs) -> list[int]:
    from tests.helpers.finder_seed import run_statement

    ids: list[int] = []
    limit = PAGE
    for offset in range(0, RANKING_WINDOW_LIMIT, limit):
        sql, params = near(limit=limit, offset=offset, **kwargs)
        rows = run_statement(connection, f"SELECT p.system_id64 FROM ({sql}) p", params)
        ids.extend(int(r[0]) for r in rows)
        if len(rows) < limit:
            break
    return ids


@pytest.mark.db
@pytest.mark.parametrize("reach", [250, 500])
@pytest.mark.parametrize("floor", ACCEPTED_ARCHETYPE_SCORE_FLOORS)
def test_every_page_lists_the_fitting_systems_nearest_first(reach_database, floor, reach):
    from tests.helpers.finder_seed import run_statement

    connection = reach_database
    expected = _expected(connection, floor=floor, reach=reach)
    assert len(expected) > 2 * PAGE, "the radius must need several pages"
    assert _paged(connection, floor=floor, reach=reach) == expected
    sql, params = near_count(floor=floor, reach=reach)
    assert run_statement(connection, sql, params)[0][0] == len(expected)


@pytest.mark.db
def test_the_rows_carry_the_stored_score_and_tier(reach_database):
    from tests.helpers.finder_seed import run_statement

    connection = reach_database
    columns = ARCHETYPE_COLUMNS[PICKED]
    sql, params = near(floor=76)
    rows = run_statement(
        connection,
        f"SELECT p.system_id64, p.selected_archetype_score, p.selected_archetype_tier, "
        f"p.distance_ly FROM ({sql}) p", params)
    assert rows
    stored = dict(
        ((int(i), (int(score), tier)) for i, score, tier in connection.execute(
            f"SELECT system_id64, {columns['score']}, {columns['tier']} "
            "FROM v3_app.system_archetype WHERE system_id64 = ANY(%s)",
            ([int(r[0]) for r in rows],),
        ).fetchall()))
    for system_id, score, tier, distance in rows:
        assert (int(score), tier) == stored[int(system_id)]
        assert score >= 76 and distance <= RADIUS
    distances = [float(r[3]) for r in rows]
    assert distances == sorted(distances)


@pytest.mark.db
def test_body_filters_narrow_the_type_near_a_system(reach_database):
    connection = reach_database
    expected = _expected(connection, floor=60, reach=250, only_k=True)
    assert expected
    assert _paged(connection, floor=60, reach=250,
                  filters={"main_star_class_in": ["K"]}) == expected


@pytest.mark.db
@pytest.mark.parametrize("build", ["page", "count"])
def test_the_walk_keeps_to_the_indexes_at_production_size(reach_database, build):
    from tests.helpers import finder_seed
    from tests.test_find_reach import _production_reach_statistics, _scans

    connection = reach_database
    finder_seed.import_production_statistics(connection)
    _production_reach_statistics(connection)
    statement = near(radius=500.0) if build == "page" else near_count(radius=500.0)
    plan = finder_seed.generic_plan(connection, *statement)
    scans = _scans(plan, {"system_search", "system_archetype", "system_state", "system_reach"})
    assert not any(kind == "Seq Scan" for kind, *_ in scans), scans
    assert ("Index Scan", "system_search", "system_search_position_gist", "Forward") in scans
    archetype = [s for s in scans if s[1] == "system_archetype"]
    assert archetype and all(index == "system_archetype_pkey" for _, _, index, _ in archetype)


@pytest.mark.db
def test_the_search_answers_a_type_near_a_system(reach_database):
    from edfinder_api import local_search

    connection = reach_database

    async def scenario():
        pool = await _api_pool(connection)
        try:
            return await local_search.local_db_search_v3(
                {"reference_coords": {"x": 0.0, "y": 0.0, "z": 0.0},
                 "filters": {"distance": {"max": RADIUS}},
                 "sort_by": "distance", "size": 50, "from": 0, "reach_ly": 250,
                 "archetype": PICKED, "min_score": 76}, pool)
        finally:
            await pool.close()

    response = asyncio.run(scenario())
    expected = _expected(connection, floor=76, reach=250)
    assert [r["id64"] for r in response["results"]] == expected[:50]
    assert response["total"] == len(expected)
    for row in response["results"]:
        assert row["selected_archetype"] == PICKED
        assert row["selected_archetype_score"] >= 76
        assert row["selected_archetype_tier"] in {"S", "A", "B", "C", "D"}
        assert row["claim_status"] in {"free", "claim_ended_unknown"}
