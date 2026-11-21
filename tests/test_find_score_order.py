"""Find: galaxy-wide lists are best first by the score Find shows.

Without a system to search near, Find lists the best systems first: for Any by
the best colony potential, for one colony type by that type's score. Since
ranking -3 (21 November 2026) that is the score Find shows. The stored
weighted key (the score x confidence x completeness) only breaks ties between
equal scores; before, it set the whole order, so a higher score could sit far
below a lower one. Migration 031's indexes walk the new order, so each window
is still read straight from an index at production size.

Real PostgreSQL 18 with the real migrations, and production-sized statistics
for the plans. The database layers need RATINGS_V4_VALIDATION_DATABASE_URL;
CI sets EDF_DB_TESTS_REQUIRED=1 so they cannot be skipped there.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://find-score-order-test.invalid")

from edfinder_api.ranking.profile import (  # noqa: E402
    ARCHETYPE_KEYS,
    FIND_COUNT_LIMIT,
    PROFILE_SPEC,
)
from edfinder_api.ranking.ranking_sql import (  # noqa: E402
    ARCHETYPE_COLUMNS,
    build_count_query,
    build_ranked_query,
)
from tests.test_find_reach import (  # noqa: E402,F401  (the shared fixture)
    _production_reach_statistics,
    _scans,
    reach_database,
    shown,
)

PICKED = ARCHETYPE_KEYS[0]
FLOORS = {60: "b", 76: "a", 88: "s"}
PAGE = 500


@pytest.fixture(scope="module")
def score_database(reach_database):
    """The shared Find database, with the best colony potential spread over
    0-100 (the seed gives every system the same one) so that the Any list
    has many different scores, and many ties to break."""
    connection = reach_database
    with connection.transaction():
        # The nightly update's switch: the only way rows of a published
        # generation may change (migration 025).
        connection.execute("SET LOCAL edfinder.nightly_update = 'on'")
        connection.execute(
            "UPDATE v3_derived.system_archetype "
            "SET best_colony_potential = mod(system_id64 * 7919, 101)::smallint")
    connection.execute("VACUUM (ANALYZE) v3_derived.system_archetype")
    yield connection


def _page(*, picked: str | None, offset: int, floor: int = 60):
    return build_ranked_query(
        PROFILE_SPEC, picked_archetype=picked, picked_economy=None,
        hard_filters={}, reference_coords=None, limit=PAGE, offset=offset,
        min_score=floor if picked else None, hide_claimed=True, reach_ly=250)


def _count(*, picked: str | None, floor: int = 60):
    return build_count_query(
        PROFILE_SPEC, picked_archetype=picked, picked_economy=None,
        hard_filters={}, reference_coords=None,
        cap=None if picked else FIND_COUNT_LIMIT,
        min_score=floor if picked else None, hide_claimed=True, reach_ly=250)


def _served(connection, picked: str | None) -> list[tuple[int, float, float]]:
    """(id64, score shown, tie-break key) for every row Find serves, as deep
    as it pages."""
    from tests.helpers.finder_seed import run_statement

    key = "selected_archetype_weighted_e4" if picked else "weighted_potential"
    rows: list[tuple[int, float, float]] = []
    for offset in range(0, FIND_COUNT_LIMIT, PAGE):
        sql, params = _page(picked=picked, offset=offset)
        page = run_statement(
            connection, f"SELECT p.system_id64, p.primary_score, p.{key} FROM ({sql}) p", params)
        rows.extend((int(i), float(score), float(tie)) for i, score, tie in page)
        if len(page) < PAGE:
            break
    return rows


def _expected(connection, picked: str | None) -> list[int]:
    """The same list read straight from the views in the order Find promises:
    the score shown, then the weighted key, then id64."""
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
    ordered = [int(row[0]) for row in connection.execute(statement).fetchall()]
    return [i for i in ordered if shown(i, 250)][:FIND_COUNT_LIMIT]


@pytest.mark.db
@pytest.mark.parametrize("picked", [None, PICKED], ids=["any", "type"])
def test_the_galaxy_list_is_best_first_by_the_score_shown(score_database, picked):
    connection = score_database
    served = _served(connection, picked)
    assert len(served) == FIND_COUNT_LIMIT
    scores = [score for _, score, _ in served]
    # Never a higher score below a lower one, all the way down.
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) > 1, "the seed must give the list several scores"
    # Equal scores keep the old factor's order: the weighted key breaks ties.
    ties = 0
    for (_, score, key), (_, next_score, next_key) in zip(served, served[1:], strict=False):
        if score == next_score:
            ties += 1
            assert key >= next_key
    assert ties > 0, "the seed must give the list ties to break"
    assert [i for i, _, _ in served] == _expected(connection, picked)


@pytest.mark.db
@pytest.mark.parametrize("picked", [None, PICKED], ids=["any", "type"])
@pytest.mark.parametrize("build", ["page", "count"])
def test_the_window_walks_the_score_index_at_production_size(score_database, picked, build):
    from tests.helpers import finder_seed

    connection = score_database
    finder_seed.import_production_statistics(connection)
    _production_reach_statistics(connection)
    statement = _page(picked=picked, offset=0) if build == "page" else _count(picked=picked)
    plan = finder_seed.generic_plan(connection, *statement)
    scans = _scans(plan, {"system_archetype"})
    assert not any(kind == "Seq Scan" for kind, *_ in scans), scans
    expected = f"system_archetype_{picked}_b_score" if picked else "system_archetype_summary_score"
    # The window is the first read of the archetype rows: the 031 index, in
    # its own order, so the generation is never sorted.
    assert (
        ("Index Scan", "system_archetype", expected, "Forward") in scans
        or ("Index Only Scan", "system_archetype", expected, "Forward") in scans
    ), scans


@pytest.mark.db
@pytest.mark.parametrize("floor", sorted(FLOORS))
def test_each_tier_floor_walks_its_own_score_index(score_database, floor):
    from tests.helpers import finder_seed

    connection = score_database
    finder_seed.import_production_statistics(connection)
    _production_reach_statistics(connection)
    plan = finder_seed.generic_plan(connection, *_page(picked=PICKED, offset=0, floor=floor))
    scans = _scans(plan, {"system_archetype"})
    assert any(index == f"system_archetype_{PICKED}_{FLOORS[floor]}_score"
               for _, _, index, _ in scans), scans


def test_migration_031_adds_one_score_index_per_old_weighted_index():
    """031 mirrors 011: the summary index and every type's three tier-floor
    indexes, with the score put first."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / "sql/v3/migrations/031_v3_finder_order_by_score.sql").read_text()
    assert text.count("CREATE INDEX") == 1 + 3 * len(ARCHETYPE_KEYS)
    assert "CREATE INDEX system_archetype_summary_score" in text
    assert ("(derived_generation_id, best_colony_potential DESC,\n"
            "        weighted_potential DESC NULLS LAST, system_id64)") in text
    for key in ARCHETYPE_KEYS:
        for floor, tier in FLOORS.items():
            assert (
                f"CREATE INDEX system_archetype_{key}_{tier}_score\n"
                "    ON v3_derived.system_archetype\n"
                f"       (derived_generation_id, {key}_score DESC, {key}_weighted_e4 DESC, "
                "system_id64)\n"
                f"    WHERE {key}_score >= {floor};"
            ) in text
    assert "DROP" not in text
    assert text.rstrip().endswith("COMMIT;")
