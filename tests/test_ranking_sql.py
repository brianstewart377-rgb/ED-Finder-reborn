"""Pure SQL-builder tests for the wide-row F2d ranking paths."""

from copy import deepcopy
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))

from edfinder_api.ranking.profile import (  # noqa: E402
    ARCHETYPE_KEYS,
    PROFILE_SPEC,
    RANKING_VERSION,
    ranking_sha256,
)
from edfinder_api.ranking.ranking_sql import (  # noqa: E402
    ARCHETYPE_COLUMNS,
    build_count_query,
    build_ranked_query,
)

LEGACY = (
    "mv_archetype_rankings",
    "system_archetype_scores",
    "system_archetype_traits",
    "FROM systems",
    "public.",
)


def _picked_page(key="paradise", floor=60, limit=50, offset=0):
    return build_ranked_query(
        PROFILE_SPEC,
        picked_archetype=key,
        picked_economy=None,
        hard_filters={},
        reference_coords=None,
        min_score=floor,
        limit=limit,
        offset=offset,
    )


def _picked_count(key="paradise", floor=60):
    return build_count_query(
        PROFILE_SPEC,
        picked_archetype=key,
        picked_economy=None,
        hard_filters={},
        reference_coords=None,
        min_score=floor,
    )


def _assert_no_legacy(sql):
    for token in LEGACY:
        assert token not in sql


def test_column_map_is_derived_from_hashed_spec_for_all_eight_keys():
    assert ARCHETYPE_COLUMNS == PROFILE_SPEC["archetype_columns"]
    assert tuple(ARCHETYPE_COLUMNS) == ARCHETYPE_KEYS
    for key, columns in ARCHETYPE_COLUMNS.items():
        assert columns["score"] == f"{key}_score"
        assert columns["tier"] == f"{key}_tier"
        assert f"{key}_confidence_ppm / 1000000.0" in columns["confidence"]
        assert columns["weighted_key"] == f"{key}_weighted_e4"


@pytest.mark.parametrize("floor", [60, 76, 88])
def test_picked_floor_is_server_literal_and_uses_matching_columns(floor):
    sql, params = _picked_page("mining_hub", floor)
    assert f"ar.mining_hub_score >= {floor}" in sql
    assert "ar.mining_hub_weighted_e4" in sql
    assert "picked.mining_hub_tier AS selected_archetype_tier" in sql
    assert "picked.mining_hub_confidence_ppm / 1000000.0" in sql
    assert floor not in params


@pytest.mark.parametrize("floor", [0, 59, 70, 100, None, "60 OR true"])
def test_no_other_floor_can_produce_sql(floor):
    if floor is None:
        # None intentionally means the server default, never user input.
        sql, _ = _picked_page(floor=floor)
        assert "paradise_score >= 60" in sql
    else:
        with pytest.raises(ValueError):
            _picked_page(floor=floor)


PIN = (
    "(SELECT pinned.derived_generation_id "
    "FROM v3_meta.current_derived_generation pinned WHERE pinned.singleton)"
)


@pytest.mark.parametrize("key", ARCHETYPE_KEYS)
@pytest.mark.parametrize("floor", (60, 76, 88))
def test_picked_page_adds_rival_scores_only_to_the_unchanged_final_fetch(key, floor):
    sql, params = _picked_page(key=key, floor=floor, limit=50, offset=75)
    window, remainder = sql.split("), ranked_page_keys AS (\n", 1)
    keys, remainder = remainder.split("), ranked_page AS (\n", 1)
    details, fetch = remainder.split(")\nSELECT page.*,\n", 1)
    assert sql.count("v3_app.system_archetype") == 1
    assert "FROM v3_app.system_archetype ar" in sql
    # All fenced reads name the published generation as an InitPlan constant
    # so the generation-led indexes supply the window order (2026-10-22).
    assert f"WHERE ar.derived_generation_id = {PIN}" in window
    assert f"WHERE member.derived_generation_id = {PIN}" in keys
    assert f"WHERE s.derived_generation_id = {PIN}" in details
    assert window == (
        "WITH archetype_window AS (\n"
        f"    SELECT ar.system_id64, ar.{key}_weighted_e4 "
        "AS selected_archetype_weighted_e4\n"
        "    FROM v3_app.system_archetype ar\n"
        f"    WHERE ar.derived_generation_id = {PIN}\n"
        f"      AND ar.{key}_score >= {floor}\n"
        f"    ORDER BY ar.{key}_weighted_e4 DESC, ar.system_id64 ASC\n"
        "    LIMIT 10000\n"
    )
    assert "LIMIT 10000" in window
    assert sql.count("ORDER BY") == 3
    assert f"ORDER BY ar.{key}_weighted_e4 DESC" in window
    assert sql.count("selected_archetype_weighted_e4 DESC") == 1
    assert sql.count("JOIN v3_app.system_search") == 2
    # Search membership is checked before paging, but only window keys are
    # selected, allowing the same index-only search join as the count.
    selected_keys, key_from = keys.split("    FROM ", 1)
    assert selected_keys == (
        "    SELECT aw.system_id64, aw.selected_archetype_weighted_e4\n"
    )
    assert key_from.startswith("archetype_window aw\n")
    assert (
        "JOIN v3_app.system_search member "
        "ON member.system_id64 = aw.system_id64" in keys
    )
    assert keys == (
        "    SELECT aw.system_id64, aw.selected_archetype_weighted_e4\n"
        "    FROM archetype_window aw\n"
        "    JOIN v3_app.system_search member ON member.system_id64 = aw.system_id64\n"
        f"    WHERE member.derived_generation_id = {PIN}\n"
        "    ORDER BY selected_archetype_weighted_e4 DESC, system_id64 ASC\n"
        "    LIMIT $1 OFFSET $2\n"
    )
    # Only the admitted page keys join to search details. In particular,
    # s.* must never widen the 10,000-row membership page before LIMIT/OFFSET.
    assert details == (
        "    SELECT s.*, page_keys.selected_archetype_weighted_e4\n"
        "    FROM ranked_page_keys page_keys\n"
        "    JOIN v3_app.system_search s ON s.system_id64 = page_keys.system_id64\n"
        f"    WHERE s.derived_generation_id = {PIN}\n"
    )
    assert sql.count("s.*") == 1
    assert "LIMIT" not in details
    assert "OFFSET" not in details
    # The final fetch reads the physical row of the page's own generation
    # (every page row already passed both fenced views) under the alias the
    # plan probe requires, and sorts the page rows by the fetched key. Its
    # existing projection, join and order remain byte-for-byte unchanged.
    # The eight new, non-clashing aliases appear only in this final SELECT;
    # page.* already carries the search projection's own score columns.
    select_list, _ = fetch.split("FROM ranked_page page\n", 1)
    for score_key in ARCHETYPE_KEYS:
        expression = f"       picked.{score_key}_score AS picked_{score_key}_score,\n"
        assert sql.count(expression) == 1
        assert select_list.count(expression) == 1
        fetch = fetch.replace(expression, "")
    assert fetch == (
        f"       picked.{key}_score AS primary_score,\n"
        f"       picked.{key}_tier AS selected_archetype_tier,\n"
        f"       (picked.{key}_confidence_ppm / 1000000.0)::double precision "
        "AS ranking_confidence,\n"
        f"       ((picked.{key}_confidence_ppm / 1000000.0)::double precision "
        "* page.completeness) AS uncertainty_factor,\n"
        "       picked.primary_archetype AS summary_primary_archetype,\n"
        "       picked.secondary_archetype AS summary_secondary_archetype,\n"
        "       picked.best_colony_potential AS summary_best_colony_potential,\n"
        "       (picked.archetype_confidence_ppm / 1000000.0)::double precision "
        "AS summary_archetype_confidence,\n"
        "       picked.weighted_potential AS weighted_potential\n"
        "FROM ranked_page page\n"
        "JOIN v3_derived.system_archetype picked\n"
        "  ON picked.derived_generation_id = page.derived_generation_id\n"
        " AND picked.system_id64 = page.system_id64\n"
        f"ORDER BY picked.{key}_weighted_e4 DESC, page.system_id64 ASC"
    )
    assert sql.endswith(f"ORDER BY picked.{key}_weighted_e4 DESC, page.system_id64 ASC")
    assert params == [50, 75]
    _assert_no_legacy(sql)


def test_picked_page_rival_display_fields_preserve_the_pinned_ranking_identity():
    # These are the deployed pins in test_ranking_profile_identity.py. The
    # rival display fields do not change the hashed ranking semantics.
    for key in ARCHETYPE_KEYS:
        for floor in (60, 76, 88):
            sql, _ = _picked_page(key=key, floor=floor, limit=50, offset=9950)
            assert f"picked.{key}_score AS picked_{key}_score" in sql
    assert RANKING_VERSION == "v3-colony-potential-2"
    assert ranking_sha256() == (
        "ec3533063f422f5859a26baf422974f9d330466f2ad675505fac2743bbca89e4"
    )


def test_picked_count_has_fixed_sentinel_over_the_ordered_window():
    sql, params = _picked_count(floor=88)
    assert "paradise_score >= 88" in sql
    assert f"WHERE ar.derived_generation_id = {PIN}" in sql
    assert f"WHERE s.derived_generation_id = {PIN}" in sql
    window, join = sql.split(") aw", 1)
    # The sentinel bounds the archetype window in the partial index's own
    # order before the page's search join, exactly like the page window.
    assert "ORDER BY ar.paradise_weighted_e4 DESC, ar.system_id64 ASC" in window
    assert "LIMIT 10001" in window
    assert "JOIN v3_app.system_search s ON s.system_id64 = aw.system_id64" in join
    assert "LIMIT" not in join
    assert params == []


def test_unknown_and_adversarial_archetype_keys_never_reach_sql():
    for key in ("unknown", "paradise; DROP TABLE systems", 'paradise"'):
        with pytest.raises(ValueError):
            _picked_page(key=key)


def test_picked_path_rejects_filters_instead_of_filtering_the_window():
    with pytest.raises(ValueError):
        build_ranked_query(
            PROFILE_SPEC,
            picked_archetype="paradise",
            picked_economy=None,
            hard_filters={"galaxy_region": 7},
            reference_coords=None,
            min_score=60,
            limit=50,
            offset=0,
        )


def test_no_user_value_is_interpolated_into_picked_sql():
    sql, params = _picked_page(limit=417, offset=283)
    assert "417" not in sql and "283" not in sql
    assert params == [417, 283]
    # The only bare numeric literals are the hashed, server-owned floor,
    # window and confidence-scale constants.
    numbers = {int(value) for value in re.findall(r"(?<!\$)\b\d+\b", sql)}
    assert numbers <= {60, 10000, 1000000, 0}


def test_no_pick_keeps_summary_join_and_canonical_order():
    sql, params = build_ranked_query(
        PROFILE_SPEC,
        picked_archetype=None,
        picked_economy=None,
        hard_filters={},
        reference_coords=None,
        limit=50,
        offset=0,
    )
    assert "v3_app.system_archetype_summary" in sql
    assert "sum.weighted_potential DESC NULLS LAST" in sql
    assert "s.system_id64 ASC" in sql
    assert "LIMIT 10000" in sql
    window, outer = sql.split(")\nSELECT", 1)
    assert f"WHERE sum.derived_generation_id = {PIN}" in window
    assert f"WHERE s.derived_generation_id = {PIN}" in outer
    assert params == [50, 0]
    _assert_no_legacy(sql)


def test_count_cap_means_cap_plus_one_sentinel():
    sql, params = build_count_query(
        PROFILE_SPEC,
        picked_archetype=None,
        picked_economy=None,
        hard_filters={},
        reference_coords=None,
        cap=10_000,
    )
    window, outer = sql.split(") sum", 1)
    # The galaxy count is taken over the galaxy page's ordered window; the
    # literal bound equals the largest sentinel so the parameter decides.
    assert f"WHERE sum.derived_generation_id = {PIN}" in window
    assert "ORDER BY sum.weighted_potential DESC NULLS LAST, sum.system_id64 ASC" in window
    assert "LIMIT 10001" in window
    assert f"WHERE s.derived_generation_id = {PIN}" in outer
    assert "LIMIT $1" in outer
    assert params == [10_001]


def test_count_sentinel_cannot_exceed_the_literal_window_bound(monkeypatch):
    import edfinder_api.ranking.ranking_sql as ranking_sql_module

    monkeypatch.setattr(ranking_sql_module, "RANKING_WINDOW_LIMIT", 10_001)
    with pytest.raises(ValueError, match="sentinel"):
        ranking_sql_module.build_count_query(
            PROFILE_SPEC,
            picked_archetype=None,
            picked_economy=None,
            hard_filters={},
            reference_coords=None,
            cap=10_001,
        )


def test_anchored_nearest_first_has_knn_order_inside_limit_radius_outside():
    sql, params = build_ranked_query(
        PROFILE_SPEC,
        picked_archetype=None,
        picked_economy=None,
        hard_filters={"max_distance_ly": 10_000.0},
        reference_coords=(1.0, 2.0, 3.0),
        sort="distance",
        limit=50,
        offset=75,
    )
    inner, outer = sql.split(") nearest", 1)
    assert "s.position_ly <@ cube(" in inner
    assert f"s.derived_generation_id = {PIN}" in inner
    assert f"sum.derived_generation_id = {PIN}" in inner
    assert "ORDER BY s.position_ly <->" in inner
    assert "sum.weighted_potential DESC NULLS LAST, s.system_id64 ASC" in inner
    assert "LIMIT $5" in inner
    assert "nearest.distance_ly <= $4" in outer
    assert params == [1.0, 2.0, 3.0, 10_000.0, 125, 50, 75]


def test_anchored_nearest_first_count_uses_knn_sentinel_inside_radius():
    sql, params = build_count_query(
        PROFILE_SPEC,
        picked_archetype=None,
        picked_economy=None,
        hard_filters={"max_distance_ly": 500.0},
        reference_coords=(1.0, 2.0, 3.0),
        sort="distance",
        cap=10_000,
    )
    inner, outer = sql.split(") nearest", 1)
    assert "ORDER BY s.position_ly <->" in inner
    # Search columns alone order the nearest-first window, so the archetype
    # probe runs once per admitted row after it; the literal bound equals the
    # largest sentinel, and the parameterized sentinel decides.
    assert ", s.system_id64 ASC\n" in inner
    assert "weighted_potential" not in inner
    assert "system_archetype_summary" not in inner
    assert "LIMIT 10001" in inner
    assert "JOIN v3_app.system_archetype_summary sum ON sum.system_id64 = nearest.system_id64" in outer
    assert f"sum.derived_generation_id = {PIN}" in outer
    assert "nearest.distance_ly <= $4" in outer
    assert "LIMIT $5" in outer
    assert params[-1] == 10_001


def test_anchored_count_keeps_summary_filters_inside_the_nearest_window():
    sql, params = build_count_query(
        PROFILE_SPEC,
        picked_archetype=None,
        picked_economy=None,
        hard_filters={"max_distance_ly": 500.0, "min_development_score": 60},
        reference_coords=(1.0, 2.0, 3.0),
        sort="distance",
        cap=10_000,
    )
    inner, outer = sql.split(") nearest", 1)
    assert "JOIN v3_app.system_archetype_summary sum ON sum.system_id64 = s.system_id64" in inner
    assert "sum.best_colony_potential >= $5" in inner
    assert "system_archetype_summary" not in outer
    assert params == [1.0, 2.0, 3.0, 500.0, 60, 10_001]


def test_anchored_score_page_walks_candidates_in_distance_order_without_truncation():
    sql, params = build_ranked_query(
        PROFILE_SPEC,
        picked_archetype=None,
        picked_economy=None,
        hard_filters={"max_distance_ly": 500.0},
        reference_coords=(1.0, 2.0, 3.0),
        sort="score",
        limit=24,
        offset=48,
    )
    inner, outer = sql.split(") candidates", 1)
    assert "s.position_ly <@ cube(" in inner
    assert "s.position_ly <-> cube(array[$1::double precision" in inner
    assert "<= $4" in inner
    assert "ORDER BY s.position_ly <-> cube(" in inner
    # NULLIF(x, x) is always NULL (LIMIT ALL): no candidate is dropped.
    assert "LIMIT NULLIF($5::bigint, $5::bigint)" in inner
    assert "ORDER BY candidates.weighted_potential DESC NULLS LAST, candidates.system_id64 ASC" in outer
    assert "LIMIT $5 OFFSET $6" in outer
    assert params == [1.0, 2.0, 3.0, 500.0, 24, 48]


def test_anchored_filters_remain_parameterized():
    marker = "unlikely-star-class-marker"
    sql, params = build_ranked_query(
        PROFILE_SPEC,
        picked_archetype=None,
        picked_economy=None,
        hard_filters={
            "max_distance_ly": 500.0,
            "min_distance_ly": 25.0,
            "main_star_class_in": [marker],
            "has_biologicals": True,
        },
        reference_coords=(0.0, 0.0, 0.0),
        sort="distance",
        limit=50,
        offset=0,
    )
    assert marker not in sql
    assert marker in params and 25.0 in params and True in params


def test_page_window_bound_fails_closed_in_builder():
    with pytest.raises(ValueError):
        _picked_page(limit=2, offset=9_999)
    with pytest.raises(ValueError):
        _picked_page(limit=501, offset=0)


@pytest.mark.parametrize(
    "field",
    [
        "archetype_columns",
        "archetype_score_floor",
        "stored_weighted_key_expression",
        "ranking_window",
        "picked_archetype_rejected_filters",
        "picked_archetype_filter_rejection_reason",
        "local_search_contract",
        "tie_break",
    ],
)
def test_builder_rejects_mutated_hashed_contract(field):
    changed = deepcopy(PROFILE_SPEC)
    changed[field] = None
    with pytest.raises(ValueError):
        build_ranked_query(
            changed,
            picked_archetype=None,
            picked_economy=None,
            hard_filters={},
            reference_coords=None,
            limit=10,
            offset=0,
        )
