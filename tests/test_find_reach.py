"""Find's reach rule: only systems within 250 ly (or 500 ly) of inhabited space.

Three layers, as for the claim rule (tests/test_find_claim_rule.py):

* the SQL the builder writes (no database): the rule adds exactly one clause
  to each query shape, right after the claim rule, and changes nothing else;
* real PostgreSQL 18 with the real migrations: every shape returns the best
  systems that can be claimed and are in reach, in the same order as before,
  and its count agrees with its pages;
* the same database with production-sized statistics: the big Finder tables
  are read through the same indexes as before, and ``v3_state.system_reach``
  only through its primary key.

The database layers need RATINGS_V4_VALIDATION_DATABASE_URL (a local
PostgreSQL 18 database named ratings_v4_validation). CI sets
EDF_DB_TESTS_REQUIRED=1 so they cannot be skipped there.
"""
from __future__ import annotations

import asyncio
import os
import re
from collections import Counter

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://find-reach-test.invalid")

from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from edfinder_api import claim_rule, reach_rule  # noqa: E402
from edfinder_api.ranking.profile import (  # noqa: E402
    ARCHETYPE_KEYS,
    PROFILE_SPEC,
    RANKING_PAGE_LIMIT_MAX,
    RANKING_WINDOW_LIMIT,
)
from edfinder_api.ranking.ranking_sql import (  # noqa: E402
    SORT_DISTANCE,
    SORT_SCORE,
    build_count_query,
    build_ranked_query,
)

ANCHOR = (0.0, 0.0, 0.0)
RADIUS = {"max_distance_ly": 500.0}
PICKED = ARCHETYPE_KEYS[0]

# name -> (keyword arguments shared by page and count, the alias the rule
#          reads in the page, the alias it reads in the count, whether the
#          count takes a cap). The same shapes as the claim rule's tests.
SHAPES: dict[str, tuple[dict, str, str, bool]] = {
    "picked_archetype": (
        dict(picked_archetype=PICKED, hard_filters={}, reference_coords=None, min_score=60),
        "ar", "ar", False),
    "galaxy_wide": (
        dict(picked_archetype=None, hard_filters={}, reference_coords=None),
        "sum", "sum", True),
    "galaxy_wide_filtered": (
        dict(picked_archetype=None, hard_filters={"main_star_class_in": ["K"]},
             reference_coords=None), "s", "s", True),
    "radius_by_score": (
        dict(picked_archetype=None, hard_filters=dict(RADIUS), reference_coords=ANCHOR,
             sort=SORT_SCORE), "candidates", "s", True),
    "radius_by_score_filtered": (
        dict(picked_archetype=None,
             hard_filters={**RADIUS, "main_star_class_in": ["K"]},
             reference_coords=ANCHOR, sort=SORT_SCORE), "candidates", "s", True),
    "nearest_first": (
        dict(picked_archetype=None, hard_filters=dict(RADIUS), reference_coords=ANCHOR,
             sort=SORT_DISTANCE), "s", "s", True),
    "nearest_first_summary_filter": (
        dict(picked_archetype=None,
             hard_filters={**RADIUS, "min_development_score": 1},
             reference_coords=ANCHOR, sort=SORT_DISTANCE), "s", "s", True),
}
WINDOWED = ("picked_archetype", "galaxy_wide")
UNANCHORED = (*WINDOWED, "galaxy_wide_filtered")
REACHES = reach_rule.REACH_CHOICES


def page(shape: str, *, reach: int | None, hide: bool = True, limit: int = 50, offset: int = 0):
    kwargs = SHAPES[shape][0]
    return build_ranked_query(
        PROFILE_SPEC, picked_economy=None, limit=limit, offset=offset,
        hide_claimed=hide, reach_ly=reach, **kwargs)


def count(shape: str, *, reach: int | None, hide: bool = True):
    kwargs, _, _, capped = SHAPES[shape]
    extra = {"cap": RANKING_WINDOW_LIMIT} if capped else {}
    return build_count_query(
        PROFILE_SPEC, picked_economy=None, hide_claimed=hide, reach_ly=reach,
        **extra, **kwargs)


def _without_reach(sql: str, alias: str, reach: int) -> tuple[str, int]:
    clause = re.escape(reach_rule.in_reach_sql(f"{alias}.system_id64", reach))
    return re.subn(rf"(?:\n\s+AND {clause}|WHERE {clause}\n)", "", sql)


# ---------------------------------------------------------------------------
# The SQL the builder writes
# ---------------------------------------------------------------------------
def test_rule_text_names_the_table_and_the_two_bands():
    assert reach_rule.in_reach_sql("s.system_id64", 250) == (
        "EXISTS (SELECT 1 FROM v3_state.system_reach reach "
        "WHERE reach.system_id64 = s.system_id64 AND reach.within_250)"
    )
    assert reach_rule.in_reach_sql("s.system_id64", 500) == (
        "EXISTS (SELECT 1 FROM v3_state.system_reach reach "
        "WHERE reach.system_id64 = s.system_id64)"
    )
    assert reach_rule.REACH_CHOICES == (250, 500)
    assert reach_rule.DEFAULT_REACH_LY == 250


@pytest.mark.parametrize("bad", [0, 100, 300, 1000, "250", True, None, 250.5])
def test_only_the_two_bands_are_accepted(bad):
    with pytest.raises(ValueError):
        reach_rule.checked_reach(bad)
    with pytest.raises(ValueError):
        reach_rule.in_reach_sql("s.system_id64", bad)  # type: ignore[arg-type]
    if bad is not None:
        with pytest.raises(ValueError):
            page("galaxy_wide", reach=bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            count("galaxy_wide", reach=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("build", [page, count], ids=["page", "count"])
def test_rule_is_off_unless_asked_for(shape, build):
    sql, params = build(shape, reach=None)
    assert "system_reach" not in sql
    kwargs, _, _, capped = SHAPES[shape]
    if build is page:
        default = build_ranked_query(
            PROFILE_SPEC, picked_economy=None, limit=50, offset=0, hide_claimed=True, **kwargs)
    else:
        extra = {"cap": RANKING_WINDOW_LIMIT} if capped else {}
        default = build_count_query(
            PROFILE_SPEC, picked_economy=None, hide_claimed=True, **extra, **kwargs)
    assert (sql, params) == default


@pytest.mark.parametrize("reach", REACHES)
@pytest.mark.parametrize("hide", [True, False], ids=["with-claim-rule", "alone"])
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("build", [page, count], ids=["page", "count"])
def test_rule_adds_one_clause_and_nothing_else(shape, build, hide, reach):
    off_sql, off_params = build(shape, reach=None, hide=hide)
    on_sql, on_params = build(shape, reach=reach, hide=hide)
    alias = SHAPES[shape][1 if build is page else 2]
    stripped, removed = _without_reach(on_sql, alias, reach)
    assert removed == 1, on_sql
    assert stripped == off_sql
    assert on_params == off_params  # the band is written by the server, not bound
    assert on_sql.count("v3_state.system_reach") == 1
    if hide:
        # Right after the claim rule, in the same place.
        claim = claim_rule.not_claimed_sql(f"{alias}.system_id64")
        assert on_sql.index(claim) < on_sql.index("v3_state.system_reach")


@pytest.mark.parametrize("shape", WINDOWED)
def test_rule_is_inside_the_bounded_window(shape):
    """A window holds the best 10,000 systems in reach, so the rule is applied
    before the window's LIMIT, in the page and the count."""
    page_sql, _ = page(shape, reach=250)
    count_sql, _ = count(shape, reach=250)
    assert page_sql.index("system_reach") < page_sql.index(f"LIMIT {RANKING_WINDOW_LIMIT}\n")
    assert count_sql.index("system_reach") < count_sql.index(f"LIMIT {RANKING_WINDOW_LIMIT + 1}\n")


@pytest.mark.parametrize("shape", ["nearest_first", "nearest_first_summary_filter"])
def test_rule_is_inside_the_nearest_first_window(shape):
    page_sql, _ = page(shape, reach=250)
    count_sql, _ = count(shape, reach=250)
    for sql in (page_sql, count_sql):
        assert sql.index("system_reach") < sql.index("ORDER BY s.position_ly <->")


@pytest.mark.parametrize("shape", ["radius_by_score", "radius_by_score_filtered"])
def test_rule_is_outside_the_radius_candidates_subquery(shape):
    page_sql, _ = page(shape, reach=250)
    assert page_sql.index(") candidates\n") < page_sql.index("system_reach")
    assert "WHERE reach.system_id64 = candidates.system_id64" in page_sql


def test_the_requests_take_the_band_and_refuse_others():
    from edfinder_api.local_search import prepare_galaxy_search_v3, reach_from_body
    from edfinder_api.models import GalaxySearchRequest, LocalSearchRequest

    assert LocalSearchRequest(galaxy_wide=True).reach_ly == 250
    assert LocalSearchRequest(galaxy_wide=True, reach_ly=500).reach_ly == 500
    assert GalaxySearchRequest().reach_ly == 250
    for model in (LocalSearchRequest, GalaxySearchRequest):
        with pytest.raises(ValidationError):
            model(reach_ly=300)
    assert reach_from_body({}) is None
    assert reach_from_body({"reach_ly": 500}) == 500
    with pytest.raises(HTTPException) as refused:
        reach_from_body({"reach_ly": 300})
    assert refused.value.status_code == 422
    assert prepare_galaxy_search_v3({"reach_ly": 500})["reach_ly"] == 500


def test_the_rankings_route_takes_the_band():
    from fastapi.testclient import TestClient

    from edfinder_api.main import app

    schema = app.openapi()
    parameters = {
        p["name"]: p for p in schema["paths"]["/api/archetypes/rankings"]["get"]["parameters"]
    }
    assert parameters["reach_ly"]["schema"]["enum"] == [250, 500]
    assert parameters["reach_ly"]["schema"]["default"] == 250
    with TestClient(app) as client:
        refused = client.get("/api/archetypes/rankings",
                             params={"archetype": PICKED, "reach_ly": 300})
    assert refused.status_code == 422


# ---------------------------------------------------------------------------
# Real PostgreSQL 18
# ---------------------------------------------------------------------------
SYSTEMS = 40_000
# v3_state.system_reach on the Pi on 14 November 2026.
PRODUCTION_REACH_SYSTEMS = 39_300_288


def reach_of(system_id: int) -> int | None:
    """How ``reach_database`` places the seeded systems: 250, 500 or None."""
    if system_id % 7 == 3:
        return None
    if system_id % 7 in (4, 5):
        return 500
    return 250


def in_reach(system_id: int, band: int) -> bool:
    found = reach_of(system_id)
    return found is not None and found <= band


def hidden_by_claim(system_id: int) -> bool:
    return system_id % 5 == 0 or system_id % 10 == 1


def shown(system_id: int, band: int) -> bool:
    return not hidden_by_claim(system_id) and in_reach(system_id, band)


@pytest.fixture(scope="module")
def reach_database():
    from tests.helpers import finder_seed
    from tests.ratings_v4_pg_fixture import canonical_database

    with canonical_database() as database:
        connection = database[0]
        finder_seed.build_finder_database(connection, systems=SYSTEMS)
        first, last = finder_seed.FIRST_SYSTEM_ID, finder_seed.FIRST_SYSTEM_ID + SYSTEMS - 1
        # The claim rule's seeding (tests/test_find_claim_rule.py), so the two
        # rules are tested together as Find applies them.
        connection.execute(
            """
            INSERT INTO v3_state.system_state
                (system_id64, name, x_ly, y_ly, z_ly, population, station_count,
                 system_updated_at, stations_updated_at, content_hash, source)
            SELECT n, 'State ' || n, 0, 0, 0,
                   CASE WHEN mod(n, 5) = 0 THEN 1000 ELSE 0 END, 1, now(),
                   CASE WHEN mod(n, 5) = 0 THEN now() - interval '200 days'
                        WHEN mod(n, 10) = 1 THEN now() - interval '29 days'
                        ELSE now() - interval '31 days' END,
                   'test', 'test'
              FROM generate_series(%s::bigint, %s::bigint) n
             WHERE mod(n, 5) = 0 OR mod(n, 10) IN (1, 6)
            """,
            (first, last),
        )
        connection.execute(
            """
            INSERT INTO v3_state.system_reach (system_id64, within_250)
            SELECT n, mod(n, 7) NOT IN (4, 5)
              FROM generate_series(%s::bigint, %s::bigint) n
             WHERE mod(n, 7) <> 3
            """,
            (first, last),
        )
        for table in ("system_state", "system_reach"):
            connection.execute(f"ALTER TABLE v3_state.{table} SET (autovacuum_enabled = false)")
            connection.execute(f"VACUUM (ANALYZE) v3_state.{table}")
        yield connection


def _all_page_ids(connection, shape: str, *, reach: int | None) -> list[int]:
    from tests.helpers.finder_seed import run_statement

    ids: list[int] = []
    limit = RANKING_PAGE_LIMIT_MAX
    for offset in range(0, RANKING_WINDOW_LIMIT, limit):
        sql, params = page(shape, reach=reach, limit=limit, offset=offset)
        rows = run_statement(connection, f"SELECT page.system_id64 FROM ({sql}) page", params)
        ids.extend(int(row[0]) for row in rows)
        if len(rows) < limit:
            break
    return ids


def _full_order(connection, shape: str) -> list[int]:
    """Every system a windowed shape ranks, in its order, read straight from
    the views, not through the builder."""
    from edfinder_api.ranking.ranking_sql import ARCHETYPE_COLUMNS

    if shape == "picked_archetype":
        columns = ARCHETYPE_COLUMNS[PICKED]
        statement = (
            "SELECT a.system_id64 FROM v3_app.system_archetype a "
            f"WHERE a.{columns['score']} >= 60 "
            f"ORDER BY a.{columns['weighted_key']} DESC, a.system_id64 ASC"
        )
    else:
        only_k = "WHERE s.main_star_class = 'K' " if shape == "galaxy_wide_filtered" else ""
        statement = (
            "SELECT sum.system_id64 FROM v3_app.system_archetype_summary sum "
            "JOIN v3_app.system_search s ON s.system_id64 = sum.system_id64 "
            f"{only_k}"
            "ORDER BY sum.weighted_potential DESC NULLS LAST, sum.system_id64 ASC"
        )
    return [int(row[0]) for row in connection.execute(statement).fetchall()]


@pytest.mark.db
@pytest.mark.parametrize("reach", REACHES)
@pytest.mark.parametrize("shape", SHAPES)
def test_every_shape_returns_the_best_systems_in_reach(reach_database, shape, reach):
    from tests.helpers.finder_seed import run_statement

    connection = reach_database
    claimable = _all_page_ids(connection, shape, reach=None)
    after = _all_page_ids(connection, shape, reach=reach)

    assert sum(not in_reach(i, reach) for i in claimable) > 20, "the shape must meet out-of-reach systems"
    assert after and all(shown(i, reach) for i in after)
    if reach == 500:
        assert any(reach_of(i) == 500 for i in after), "the wider band must add systems"

    if shape in UNANCHORED:
        everything = _full_order(connection, shape)
        visible = [i for i in everything if shown(i, reach)]
        if shape != "picked_archetype":
            # More matches than a caller can page through, so the window must
            # be refilled from further down the ranking, not left short. (The
            # picked type's score floor leaves fewer than that at 250 ly.)
            assert len(visible) > RANKING_WINDOW_LIMIT
        assert after == visible[:RANKING_WINDOW_LIMIT]
        expected = min(len(visible), RANKING_WINDOW_LIMIT + 1)
    else:
        assert len(claimable) < RANKING_WINDOW_LIMIT
        assert after == [i for i in claimable if in_reach(i, reach)]
        expected = len(after)

    sql, params = count(shape, reach=reach)
    assert run_statement(connection, sql, params)[0][0] == expected


BIG_TABLES = {"system_search", "system_archetype"}


def _scans(plan: dict, relations: set[str]) -> Counter:
    from tests.helpers.finder_seed import plan_nodes

    return Counter(
        (node["Node Type"], node["Relation Name"], node.get("Index Name"),
         node.get("Scan Direction"))
        for node in plan_nodes(plan)
        if node.get("Relation Name") in relations
    )


def _production_reach_statistics(connection) -> None:
    rows = connection.execute(
        """
        SELECT c.relname, c.reltuples, pg_relation_size(c.oid) / current_setting('block_size')::int
          FROM pg_class c
         WHERE c.oid = 'v3_state.system_reach'::regclass
            OR c.oid IN (SELECT indexrelid FROM pg_index
                          WHERE indrelid = 'v3_state.system_reach'::regclass)
        """
    ).fetchall()
    table_tuples = next(tuples for name, tuples, _ in rows if name == "system_reach")
    for name, tuples, pages in rows:
        scaled = round(PRODUCTION_REACH_SYSTEMS * float(tuples) / float(table_tuples))
        connection.execute(
            "SELECT pg_restore_relation_stats('schemaname', 'v3_state', 'relname', %s::text, "
            "'relpages', %s::integer, 'reltuples', %s::real, 'relallvisible', %s::integer)",
            (name, int(pages), float(scaled), int(pages) if name == "system_reach" else 0),
        )


@pytest.mark.db
@pytest.mark.parametrize("reach", REACHES)
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("build", [page, count], ids=["page", "count"])
def test_rule_keeps_the_production_plan_for_the_big_tables(reach_database, shape, build, reach):
    from tests.helpers import finder_seed

    connection = reach_database
    imported = finder_seed.import_production_statistics(connection)
    assert imported["system_search"] == finder_seed.PRODUCTION_SYSTEMS
    _production_reach_statistics(connection)

    before = finder_seed.generic_plan(connection, *build(shape, reach=None))
    after = finder_seed.generic_plan(connection, *build(shape, reach=reach))

    big_before = _scans(before, BIG_TABLES)
    big_after = _scans(after, BIG_TABLES)
    assert big_before, "the plan must read the Finder tables"
    assert not any(kind == "Seq Scan" for kind, *_ in big_before + big_after)
    if shape == "galaxy_wide_filtered":
        # The API refuses this shape (a galaxy-wide search with filters gets
        # 422), and with no window to keep, PostgreSQL may drive it from
        # either big table; only the absence of a full scan matters here.
        pass
    else:
        # Same scans, same indexes, same directions on the big tables.
        assert big_after == big_before

    reach_scans = _scans(after, {"system_reach"})
    assert reach_scans, "the rule must be in the plan"
    assert not _scans(before, {"system_reach"})
    for kind, _, index, _ in reach_scans:
        assert kind in {"Index Scan", "Index Only Scan"}, reach_scans
        assert index == "system_reach_pkey"

    if shape != "galaxy_wide_filtered":
        # The probe is one primary-key lookup per row, as the inner side of a
        # nested loop, so a window keeps its index order (the key is unique,
        # so PostgreSQL plans the EXISTS as an inner join). The refused shape
        # has no window to keep.
        assert _joins_over(after, "system_reach") == ["Nested Loop"]


def _joins_over(plan: dict, relation: str) -> list[str]:
    """The join node above each scan of ``relation`` on the inner side."""
    found: list[str] = []

    def walk(node: dict) -> None:
        for child in node.get("Plans") or []:
            if (child.get("Relation Name") == relation
                    and child.get("Parent Relationship") == "Inner"):
                found.append(node["Node Type"])
            walk(child)

    walk(plan)
    return found


# ---------------------------------------------------------------------------
# The API functions behind the routes, on a real asyncpg pool
# ---------------------------------------------------------------------------
async def _api_pool(connection):
    import asyncpg
    from psycopg.conninfo import conninfo_to_dict

    settings = conninfo_to_dict(os.environ["RATINGS_V4_VALIDATION_DATABASE_URL"])
    return await asyncpg.create_pool(
        host=settings.get("host"),
        port=int(settings.get("port", 5432)),
        user=settings.get("user"),
        password=settings.get("password"),
        database=connection.info.dbname,
        min_size=1, max_size=2, statement_cache_size=0,
    )


@pytest.mark.db
def test_search_and_rankings_keep_to_the_asked_reach(reach_database):
    from edfinder_api import local_search
    from edfinder_api.routers import archetypes

    connection = reach_database

    async def scenario(reach: int):
        pool = await _api_pool(connection)
        try:
            galaxy = await local_search.local_db_search_v3(
                {"galaxy_wide": True, "size": 200, "from": 0, "reach_ly": reach}, pool)
            nearest = await local_search.local_db_search_v3(
                {"reference_coords": {"x": 0.0, "y": 0.0, "z": 0.0},
                 "filters": {"distance": {"max": 500}},
                 "sort_by": "distance", "size": 200, "from": 0, "reach_ly": reach}, pool)
            ranked = await archetypes._archetype_rankings_v3(
                pool=pool, archetype=PICKED, min_score=60, limit=200, offset=0,
                galaxy_region=None, max_distance_ly=None, has_elw=None,
                min_slots=None, max_contamination=None, reach_ly=reach)
        finally:
            await pool.close()
        return galaxy, nearest, ranked

    for reach in REACHES:
        galaxy, nearest, ranked = asyncio.run(scenario(reach))
        for response in (galaxy, nearest, ranked):
            assert response["count"] == len(response["results"]) == 200
            assert all(shown(r["id64"], reach) for r in response["results"])
        # The nearest-first total counts only what this band shows.
        assert nearest["total"] == len(_all_page_ids(connection, "nearest_first", reach=reach))
        assert galaxy["total"] == RANKING_WINDOW_LIMIT and galaxy["total_is_capped"] is True
