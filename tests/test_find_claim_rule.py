"""Find's claim rule: inhabited systems and systems being claimed are hidden.

Three layers:

* the SQL the builder writes (no database): the rule adds exactly one clause to
  each query shape, in the documented place, and changes nothing else;
* real PostgreSQL 18 with the real migrations: every shape returns the best
  systems that can still be claimed, in the same order as before, and its
  count agrees with its pages;
* the same database with production-sized statistics: the rule reads the big
  Finder tables through the same indexes as before and reaches
  ``v3_state.system_state`` only through its primary key.

The database layers need RATINGS_V4_VALIDATION_DATABASE_URL (a local
PostgreSQL 18 database named ratings_v4_validation). CI sets
EDF_DB_TESTS_REQUIRED=1 so they cannot be skipped there.
"""
from __future__ import annotations

import asyncio
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://find-claim-rule-test.invalid")

from edfinder_api import claim_rule  # noqa: E402
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
#          count takes a cap)
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
# Shapes that rank through a window of RANKING_WINDOW_LIMIT rows.
WINDOWED = ("picked_archetype", "galaxy_wide")
# Shapes that are not anchored, so the seeded database has more matches than
# the RANKING_WINDOW_LIMIT rows a caller can page through.
UNANCHORED = (*WINDOWED, "galaxy_wide_filtered")


def page(shape: str, *, hide: bool, limit: int = 50, offset: int = 0):
    kwargs = SHAPES[shape][0]
    return build_ranked_query(
        PROFILE_SPEC, picked_economy=None, limit=limit, offset=offset,
        hide_claimed=hide, **kwargs)


def count(shape: str, *, hide: bool):
    kwargs, _, _, capped = SHAPES[shape]
    extra = {"cap": RANKING_WINDOW_LIMIT} if capped else {}
    return build_count_query(
        PROFILE_SPEC, picked_economy=None, hide_claimed=hide, **extra, **kwargs)


def _without_rule(sql: str, alias: str) -> tuple[str, int]:
    clause = re.escape(claim_rule.not_claimed_sql(f"{alias}.system_id64"))
    return re.subn(rf"(?:\n\s+AND {clause}|WHERE {clause}\n)", "", sql)


# ---------------------------------------------------------------------------
# The SQL the builder writes
# ---------------------------------------------------------------------------
def test_rule_text_names_the_table_and_the_two_hidden_states():
    clause = claim_rule.not_claimed_sql("s.system_id64")
    assert clause == (
        "NOT EXISTS (SELECT 1 FROM v3_state.system_state claim "
        "WHERE claim.system_id64 = s.system_id64 AND "
        "(claim.population > 0 OR claim.stations_updated_at >= now() - interval '30 days'))"
    )
    assert claim_rule.CLAIM_RECENT_DAYS == 30
    assert claim_rule.HIDDEN_STATUSES == {"inhabited", "being_claimed"}


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("build", [page, count], ids=["page", "count"])
def test_rule_is_off_unless_asked_for(shape, build):
    sql, params = build(shape, hide=False)
    assert "v3_state" not in sql
    kwargs, _, _, capped = SHAPES[shape]
    if build is page:
        default = build_ranked_query(PROFILE_SPEC, picked_economy=None, limit=50, offset=0, **kwargs)
    else:
        extra = {"cap": RANKING_WINDOW_LIMIT} if capped else {}
        default = build_count_query(PROFILE_SPEC, picked_economy=None, **extra, **kwargs)
    assert (sql, params) == default


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("build", [page, count], ids=["page", "count"])
def test_rule_adds_one_clause_and_nothing_else(shape, build):
    off_sql, off_params = build(shape, hide=False)
    on_sql, on_params = build(shape, hide=True)
    alias = SHAPES[shape][1 if build is page else 2]
    stripped, removed = _without_rule(on_sql, alias)
    assert removed == 1, on_sql
    assert stripped == off_sql
    assert on_params == off_params  # the rule takes no request input
    assert on_sql.count("v3_state.system_state") == 1


@pytest.mark.parametrize("shape", WINDOWED)
def test_rule_is_inside_the_bounded_window(shape):
    """A window holds the best 10,000 systems that can still be claimed, so
    the rule is applied before the window's LIMIT, in the page and the count."""
    page_sql, _ = page(shape, hide=True)
    count_sql, _ = count(shape, hide=True)
    assert page_sql.index("v3_state") < page_sql.index(f"LIMIT {RANKING_WINDOW_LIMIT}\n")
    assert count_sql.index("v3_state") < count_sql.index(f"LIMIT {RANKING_WINDOW_LIMIT + 1}\n")


@pytest.mark.parametrize("shape", ["nearest_first", "nearest_first_summary_filter"])
def test_rule_is_inside_the_nearest_first_window(shape):
    """Near an inhabited anchor the nearest systems are mostly inhabited, so
    the rule must be applied before the nearest-first LIMIT, not after it."""
    page_sql, _ = page(shape, hide=True)
    count_sql, _ = count(shape, hide=True)
    for sql in (page_sql, count_sql):
        assert sql.index("v3_state") < sql.index("ORDER BY s.position_ly <->")


@pytest.mark.parametrize("shape", ["radius_by_score", "radius_by_score_filtered"])
def test_rule_is_outside_the_radius_candidates_subquery(shape):
    """The candidates subquery keeps its LIMIT and so its own plan."""
    page_sql, _ = page(shape, hide=True)
    assert page_sql.index(") candidates\n") < page_sql.index("v3_state")
    assert "candidates.system_id64" in page_sql


def test_apply_claim_notes_marks_free_and_noted_systems():
    results = [{"id64": 1}, {"id64": 2}]
    claim_rule.apply_claim_notes(results, {
        2: {"claim_status": "claim_ended_unknown",
            "claim_station_seen_at": "2026-08-01T00:00:00+00:00"},
    })
    assert results == [
        {"id64": 1, "claim_status": "free", "claim_station_seen_at": None},
        {"id64": 2, "claim_status": "claim_ended_unknown",
         "claim_station_seen_at": "2026-08-01T00:00:00+00:00"},
    ]


# ---------------------------------------------------------------------------
# Real PostgreSQL 18
# ---------------------------------------------------------------------------
# Enough systems that every unanchored shape still has more than 10,000
# matches after the hidden systems are taken out.
SYSTEMS = 40_000


def expected_status(system_id: int) -> str:
    """How ``finder_database`` marks the seeded systems."""
    if system_id % 5 == 0:
        return "inhabited"
    if system_id % 10 == 1:
        return "being_claimed"
    if system_id % 10 == 6:
        return "claim_ended_unknown"
    return "free"


def hidden(system_id: int) -> bool:
    return expected_status(system_id) in claim_rule.HIDDEN_STATUSES


@pytest.fixture(scope="module")
def finder_database():
    from tests.helpers import finder_seed
    from tests.ratings_v4_pg_fixture import canonical_database

    with canonical_database() as database:
        connection = database[0]
        finder_seed.build_finder_database(connection, systems=SYSTEMS)
        first, last = finder_seed.FIRST_SYSTEM_ID, finder_seed.FIRST_SYSTEM_ID + SYSTEMS - 1
        connection.execute(
            """
            INSERT INTO v3_state.system_state
                (system_id64, name, x_ly, y_ly, z_ly, population, station_count,
                 system_updated_at, stations_updated_at, content_hash, source)
            SELECT n, 'State ' || n, 0, 0, 0,
                   CASE WHEN mod(n, 5) = 0 THEN 1000 ELSE 0 END,
                   1,
                   now(),
                   CASE WHEN mod(n, 5) = 0 THEN now() - interval '200 days'
                        WHEN mod(n, 10) = 1 THEN now() - interval '29 days'
                        ELSE now() - interval '31 days' END,
                   'test', 'test'
              FROM generate_series(%s::bigint, %s::bigint) n
             WHERE mod(n, 5) = 0 OR mod(n, 10) IN (1, 6)
            """,
            (first, last),
        )
        # As many other rows as production has, so the table is production-sized
        # in fact and not only in its statistics. Their ids sort before every
        # seeded system: a merge join against the table then has to walk all of
        # them, as it would in production where ids are spread over the range.
        connection.execute(
            """
            INSERT INTO v3_state.system_state
                (system_id64, name, x_ly, y_ly, z_ly, population,
                 system_updated_at, content_hash, source)
            SELECT -n, 'Elsewhere ' || n, 0, 0, 0, 1000, now(), 'test', 'test'
              FROM generate_series(1, %s) n
            """,
            (finder_seed.PRODUCTION_STATE_SYSTEMS - SYSTEMS // 2,),
        )
        connection.execute("VACUUM (ANALYZE) v3_state.system_state")
        yield connection


def _ids(rows: list[tuple], connection) -> list[int]:
    return [int(row[0]) for row in rows]


def _all_page_ids(connection, shape: str, *, hide: bool) -> list[int]:
    from tests.helpers.finder_seed import run_statement

    ids: list[int] = []
    limit = RANKING_PAGE_LIMIT_MAX
    for offset in range(0, RANKING_WINDOW_LIMIT, limit):
        sql, params = page(shape, hide=hide, limit=limit, offset=offset)
        rows = run_statement(
            connection, f"SELECT page.system_id64 FROM ({sql}) page", params)
        ids.extend(int(row[0]) for row in rows)
        if len(rows) < limit:
            break
    return ids


def _full_order(connection, shape: str) -> list[int]:
    """Every system a windowed shape ranks, in its order, with no window: read
    straight from the views, not through the builder."""
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
@pytest.mark.parametrize("shape", SHAPES)
def test_every_shape_returns_the_best_systems_that_can_still_be_claimed(finder_database, shape):
    from tests.helpers.finder_seed import run_statement

    connection = finder_database
    before = _all_page_ids(connection, shape, hide=False)
    after = _all_page_ids(connection, shape, hide=True)

    assert sum(hidden(i) for i in before) > 20, "the shape must meet hidden systems"
    assert {expected_status(i) for i in after} == {"free", "claim_ended_unknown"}

    if shape in UNANCHORED:
        # There are more matches than a caller can page through: with the rule
        # the 10,000 must be refilled from further down the ranking, not left
        # short by the hidden systems.
        everything = _full_order(connection, shape)
        visible = [i for i in everything if not hidden(i)]
        assert len(visible) > RANKING_WINDOW_LIMIT
        assert before == everything[:RANKING_WINDOW_LIMIT]
        assert after == visible[:RANKING_WINDOW_LIMIT]
        # Each count stops at its sentinel, one past the window.
        expected_counts = {False: RANKING_WINDOW_LIMIT + 1, True: RANKING_WINDOW_LIMIT + 1}
    else:
        assert len(before) < RANKING_WINDOW_LIMIT  # nothing was cut off
        # Same systems, same order, only the hidden ones gone.
        assert after == [i for i in before if not hidden(i)]
        expected_counts = {False: len(before), True: len(after)}

    for hide in (False, True):
        sql, params = count(shape, hide=hide)
        assert run_statement(connection, sql, params)[0][0] == expected_counts[hide]


@pytest.mark.db
def test_first_nearest_page_is_full_next_to_hidden_neighbours(finder_database):
    """A page of 50 nearest systems holds 50 visible systems, not 50 minus the
    hidden ones among the nearest 50."""
    from tests.helpers.finder_seed import run_statement

    connection = finder_database
    off_sql, off_params = page("nearest_first", hide=False, limit=50)
    on_sql, on_params = page("nearest_first", hide=True, limit=50)
    nearest = [int(r[0]) for r in run_statement(
        connection, f"SELECT p.system_id64 FROM ({off_sql}) p", off_params)]
    visible = [int(r[0]) for r in run_statement(
        connection, f"SELECT p.system_id64 FROM ({on_sql}) p", on_params)]
    assert any(hidden(i) for i in nearest)
    assert len(visible) == 50
    assert not any(hidden(i) for i in visible)


class _AsyncpgShaped:
    """The one asyncpg call ``claim_notes`` makes, served by psycopg."""

    def __init__(self, connection):
        self._connection = connection

    async def fetch(self, statement: str, ids: list[int]):
        from psycopg.rows import dict_row

        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(statement.replace("$1", "%s"), (ids,))
            return cursor.fetchall()


@pytest.mark.db
def test_claim_notes_reports_each_status_and_the_thirty_day_line(finder_database):
    connection = finder_database
    first = 42
    sample = list(range(first, first + 40))
    notes = asyncio.run(claim_rule.claim_notes(_AsyncpgShaped(connection), sample))

    assert set(notes) == {i for i in sample if expected_status(i) != "free"}
    for system_id, note in notes.items():
        assert note["claim_status"] == expected_status(system_id)
        seen = datetime.fromisoformat(note["claim_station_seen_at"])
        age = datetime.now(timezone.utc) - seen
        if note["claim_status"] == "being_claimed":      # seeded 29 days ago
            assert timedelta(days=28) < age < timedelta(days=30)
        elif note["claim_status"] == "claim_ended_unknown":  # seeded 31 days ago
            assert timedelta(days=30) < age < timedelta(days=32)

    results = [{"id64": i} for i in sample]
    claim_rule.apply_claim_notes(results, notes)
    assert Counter(r["claim_status"] for r in results) == Counter(
        expected_status(i) for i in sample)
    assert asyncio.run(claim_rule.claim_notes(_AsyncpgShaped(connection), [])) == {}


# ---------------------------------------------------------------------------
# Production-sized statistics: how the big tables are read must not change
# ---------------------------------------------------------------------------
BIG_TABLES = {"system_search", "system_archetype"}


def _scans(plan: dict, relations: set[str]) -> Counter:
    from tests.helpers.finder_seed import plan_nodes

    return Counter(
        (node["Node Type"], node["Relation Name"], node.get("Index Name"),
         node.get("Scan Direction"))
        for node in plan_nodes(plan)
        if node.get("Relation Name") in relations
    )


@pytest.mark.db
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("build", [page, count], ids=["page", "count"])
def test_rule_keeps_the_production_plan_for_the_big_tables(finder_database, shape, build):
    from tests.helpers import finder_seed

    connection = finder_database
    imported = finder_seed.import_production_statistics(connection)
    assert imported["system_search"] == finder_seed.PRODUCTION_SYSTEMS
    assert imported["system_archetype"] == finder_seed.PRODUCTION_SYSTEMS

    before = finder_seed.generic_plan(connection, *build(shape, hide=False))
    after = finder_seed.generic_plan(connection, *build(shape, hide=True))

    big_before = _scans(before, BIG_TABLES)
    assert big_before, "the plan must read the Finder tables"
    assert not any(kind == "Seq Scan" for kind, *_ in big_before)
    # Same scans, same indexes, same directions on the 198.5-million-row tables.
    assert _scans(after, BIG_TABLES) == big_before

    state_scans = _scans(after, {"system_state"})
    assert state_scans, "the rule must be in the plan"
    assert not _scans(before, {"system_state"})
    radius_page = build is page and shape.startswith("radius_by_score")
    for kind, _, index, _ in state_scans:
        if radius_page and kind == "Seq Scan":
            # Outside any window, against every system in the radius, reading
            # the whole small state table once is a sound choice.
            continue
        assert kind in {"Index Scan", "Index Only Scan"}, state_scans
        assert index == "system_state_pkey"

    anti_joins = [
        node["Node Type"] for node in finder_seed.plan_nodes(after)
        if "Anti" in str(node.get("Join Type"))
    ]
    assert len(anti_joins) == 1, anti_joins
    if not radius_page and shape != "galaxy_wide_filtered":
        # Inside an ordered window the rule must be a probe per row: a nested
        # loop keeps the window's index order, where a merge or hash join
        # would have to sort the whole generation first.
        assert anti_joins == ["Nested Loop"], anti_joins


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


def _assert_only_claimable(results: list[dict]) -> None:
    assert results
    for result in results:
        status = expected_status(result["id64"])
        assert status in {"free", "claim_ended_unknown"}
        assert result["claim_status"] == status
        if status == "free":
            assert result["claim_station_seen_at"] is None
        else:
            assert datetime.fromisoformat(result["claim_station_seen_at"])


@pytest.mark.db
def test_search_and_rankings_hide_claimed_systems_and_note_the_rest(finder_database):
    from edfinder_api import local_search
    from edfinder_api.routers import archetypes

    connection = finder_database

    async def scenario():
        pool = await _api_pool(connection)
        try:
            galaxy = await local_search.local_db_search_v3(
                {"galaxy_wide": True, "size": 200, "from": 0}, pool)
            nearest = await local_search.local_db_search_v3(
                {"reference_coords": {"x": 0.0, "y": 0.0, "z": 0.0},
                 "filters": {"distance": {"max": 500}},
                 "sort_by": "distance", "size": 200, "from": 0}, pool)
            by_score = await local_search.local_db_search_v3(
                {"reference_coords": {"x": 0.0, "y": 0.0, "z": 0.0},
                 "filters": {"distance": {"max": 250}},
                 "sort_by": "development", "size": 200, "from": 0}, pool)
            ranked = await archetypes._archetype_rankings_v3(
                pool=pool, archetype=PICKED, min_score=60, limit=200, offset=0,
                galaxy_region=None, max_distance_ly=None, has_elw=None,
                min_slots=None, max_contamination=None)
        finally:
            await pool.close()
        return galaxy, nearest, by_score, ranked

    galaxy, nearest, by_score, ranked = asyncio.run(scenario())

    for response in (galaxy, nearest, by_score, ranked):
        assert response["count"] == len(response["results"]) == 200
        _assert_only_claimable(response["results"])
        noted = [r for r in response["results"] if r["claim_status"] == "claim_ended_unknown"]
        assert noted, "the page must show a system with the note"

    # Totals count only what Find can show.
    in_radius = [i for i in _all_page_ids(connection, "nearest_first", hide=True)]
    assert nearest["total"] == len(in_radius)
    assert galaxy["total"] == RANKING_WINDOW_LIMIT and galaxy["total_is_capped"] is True
    assert ranked["total"] == RANKING_WINDOW_LIMIT and ranked["is_truncated"] is True
