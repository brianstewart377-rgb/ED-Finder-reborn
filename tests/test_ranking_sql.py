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
    FIND_COUNT_LIMIT,
    PROFILE_SPEC,
    RANKING_COUNT_SENTINEL,
    RANKING_VERSION,
    RANKING_WINDOW_LIMIT,
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
        f"    SELECT ar.system_id64, ar.{key}_score AS selected_archetype_score, "
        f"ar.{key}_weighted_e4 AS selected_archetype_weighted_e4\n"
        "    FROM v3_app.system_archetype ar\n"
        f"    WHERE ar.derived_generation_id = {PIN}\n"
        f"      AND ar.{key}_score >= {floor}\n"
        f"    ORDER BY ar.{key}_score DESC, ar.{key}_weighted_e4 DESC, "
        "ar.system_id64 ASC\n"
        "    LIMIT 10000\n"
    )
    assert "LIMIT 10000" in window
    assert sql.count("ORDER BY") == 3
    # Find's score order (ORDER_RULE): the score shown, then the stored
    # weighted key only to break ties. Migration 031's indexes walk it.
    assert f"ORDER BY ar.{key}_score DESC, ar.{key}_weighted_e4 DESC" in window
    assert sql.count("selected_archetype_weighted_e4 DESC") == 1
    assert sql.count("JOIN v3_app.system_search") == 2
    # Search membership is checked before paging, but only window keys are
    # selected, allowing the same index-only search join as the count.
    selected_keys, key_from = keys.split("    FROM ", 1)
    assert selected_keys == (
        "    SELECT aw.system_id64, aw.selected_archetype_score, "
        "aw.selected_archetype_weighted_e4\n"
    )
    assert key_from.startswith("archetype_window aw\n")
    assert (
        "JOIN v3_app.system_search member "
        "ON member.system_id64 = aw.system_id64" in keys
    )
    assert keys == (
        "    SELECT aw.system_id64, aw.selected_archetype_score, "
        "aw.selected_archetype_weighted_e4\n"
        "    FROM archetype_window aw\n"
        "    JOIN v3_app.system_search member ON member.system_id64 = aw.system_id64\n"
        f"    WHERE member.derived_generation_id = {PIN}\n"
        "    ORDER BY selected_archetype_score DESC, "
        "selected_archetype_weighted_e4 DESC, system_id64 ASC\n"
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
        f"ORDER BY picked.{key}_score DESC, picked.{key}_weighted_e4 DESC, "
        "page.system_id64 ASC"
    )
    assert sql.endswith(
        f"ORDER BY picked.{key}_score DESC, picked.{key}_weighted_e4 DESC, "
        "page.system_id64 ASC"
    )
    assert params == [50, 75]
    _assert_no_legacy(sql)


def test_picked_page_rival_display_fields_preserve_the_pinned_ranking_identity():
    # These are the deployed pins in test_ranking_profile_identity.py. The
    # rival display fields do not change the hashed ranking semantics.
    for key in ARCHETYPE_KEYS:
        for floor in (60, 76, 88):
            sql, _ = _picked_page(key=key, floor=floor, limit=50, offset=950)
            assert f"picked.{key}_score AS picked_{key}_score" in sql
    # -3 on 21 November 2026: lists sort by the score Find shows, and the
    # uncertainty factor only breaks ties (ORDER_RULE). What ranks first
    # changed, so the version did too.
    assert RANKING_VERSION == "v3-colony-potential-3"
    # The fingerprint covers the paging contract too: it changed on 14
    # November 2026 when Find began to count and page to 1,000, not 10,000.
    assert ranking_sha256() == (
        "45c14e9357b7786ed25bff3083024118cffbcb89a668f627b4cf18ce2a21d935"
    )


def test_picked_count_has_fixed_sentinel_over_the_ordered_window():
    sql, params = _picked_count(floor=88)
    assert "paradise_score >= 88" in sql
    assert f"WHERE ar.derived_generation_id = {PIN}" in sql
    assert f"WHERE s.derived_generation_id = {PIN}" in sql
    window, join = sql.split(") aw", 1)
    # The sentinel bounds the archetype window in the partial index's own
    # order before the page's search join, exactly like the page window.
    assert (
        "ORDER BY ar.paradise_score DESC, ar.paradise_weighted_e4 DESC, "
        "ar.system_id64 ASC" in window
    )
    # Find counts to 1,000: the sentinel is 1,001 (FIND_COUNT_LIMIT + 1).
    assert "LIMIT 1001" in window
    assert "JOIN v3_app.system_search s ON s.system_id64 = aw.system_id64" in join
    assert "LIMIT" not in join
    assert params == []


def test_unknown_and_adversarial_archetype_keys_never_reach_sql():
    for key in ("unknown", "paradise; DROP TABLE systems", 'paradise"'):
        with pytest.raises(ValueError):
            _picked_page(key=key)


def test_picked_window_is_thinned_by_search_filters_never_widened():
    """Body filters on a type galaxy-wide thin the type's window of the best
    10,000: the window walk is byte-for-byte the unfiltered one, and the
    filters are on the membership join after it, as bound parameters."""
    plain, plain_params = _picked_page(key="paradise", floor=60, limit=50, offset=0)
    sql, params = build_ranked_query(
        PROFILE_SPEC,
        picked_archetype="paradise",
        picked_economy=None,
        hard_filters={"galaxy_region": 7, "elw_count_min": 2},
        reference_coords=None,
        min_score=60,
        limit=50,
        offset=0,
    )
    window = sql.split("), ranked_page_keys AS (\n", 1)[0]
    assert window == plain.split("), ranked_page_keys AS (\n", 1)[0]
    keys = sql.split("), ranked_page_keys AS (\n", 1)[1].split("), ranked_page AS (\n", 1)[0]
    assert "member.galaxy_region_id = $1" in keys
    assert "member.elw_count >= $2" in keys
    assert params == [7, 2, 50, 0]
    assert len(plain_params) == 2
    count_sql, count_params = build_count_query(
        PROFILE_SPEC,
        picked_archetype="paradise",
        picked_economy=None,
        hard_filters={"galaxy_region": 7},
        reference_coords=None,
        min_score=60,
    )
    # Filtered, the count reads the same 10,000-row window and, like every
    # count, stops at the sentinel.
    window, join = count_sql.split(") aw", 1)
    assert "LIMIT 10000\n" in window and "LIMIT 1001\n" in join
    assert "s.galaxy_region_id = $1" in join
    assert count_params == [7]
    for refused in ({"min_development_score": 50}, {"max_distance_ly": 100.0}):
        with pytest.raises(ValueError, match="does not accept"):
            build_ranked_query(
                PROFILE_SPEC,
                picked_archetype="paradise",
                picked_economy=None,
                hard_filters=refused,
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
    assert "LIMIT 10000" in sql
    window, outer = sql.split(")\nSELECT", 1)
    assert f"WHERE sum.derived_generation_id = {PIN}" in window
    assert f"WHERE s.derived_generation_id = {PIN}" in outer
    # Find's score order (ORDER_RULE): the score shown first, the stored
    # weighted key only to break ties, in the window and on the page.
    assert (
        "ORDER BY sum.best_colony_potential DESC, "
        "sum.weighted_potential DESC NULLS LAST, sum.system_id64 ASC" in window
    )
    assert (
        "ORDER BY sum.best_colony_potential DESC, "
        "sum.weighted_potential DESC NULLS LAST, s.system_id64 ASC" in outer
    )
    assert params == [50, 0]
    _assert_no_legacy(sql)


def test_count_cap_means_cap_plus_one_sentinel():
    sql, params = build_count_query(
        PROFILE_SPEC,
        picked_archetype=None,
        picked_economy=None,
        hard_filters={},
        reference_coords=None,
        cap=1_000,
    )
    window, outer = sql.split(") sum", 1)
    # The galaxy count is taken over the galaxy page's ordered window; the
    # literal bound equals the largest sentinel so the parameter decides.
    assert f"WHERE sum.derived_generation_id = {PIN}" in window
    assert (
        "ORDER BY sum.best_colony_potential DESC, "
        "sum.weighted_potential DESC NULLS LAST, sum.system_id64 ASC" in window
    )
    assert "LIMIT 1001" in window
    assert f"WHERE s.derived_generation_id = {PIN}" in outer
    assert "LIMIT $1" in outer
    assert params == [1_001]


def _galaxy_count(cap, hard_filters=None):
    return build_count_query(
        PROFILE_SPEC,
        picked_archetype=None,
        picked_economy=None,
        hard_filters=hard_filters or {},
        reference_coords=None,
        cap=cap,
    )


def test_find_counts_no_further_than_1000():
    # Counting is most of a search's work; Find counts to 1,000 ("1,000+")
    # and pages no deeper, while the windows filters thin stay 10,000.
    assert FIND_COUNT_LIMIT == 1_000 and RANKING_WINDOW_LIMIT == 10_000
    for cap in (0, FIND_COUNT_LIMIT + 1, RANKING_WINDOW_LIMIT):
        with pytest.raises(ValueError, match="count cap goes past what Find counts"):
            _galaxy_count(cap)
    with pytest.raises(ValueError, match="go past what Find counts"):
        _picked_page(key="paradise", floor=60, limit=50, offset=951)
    sql, _ = _picked_page(key="paradise", floor=60, limit=50, offset=950)
    assert "LIMIT 10000\n" in sql


def test_no_accepted_cap_goes_past_the_literal_window_bound():
    # The counts' literal window bound is one past the furthest Find counts,
    # so the parameter (cap + 1) always decides where a count stops.
    assert RANKING_COUNT_SENTINEL == FIND_COUNT_LIMIT + 1
    sql, params = _galaxy_count(FIND_COUNT_LIMIT)
    assert f"LIMIT {RANKING_COUNT_SENTINEL}\n" in sql
    assert params == [RANKING_COUNT_SENTINEL]


def test_a_filtered_galaxy_count_reads_the_window_and_stops_at_the_sentinel():
    sql, params = _galaxy_count(FIND_COUNT_LIMIT, {"main_star_class_in": ["K"]})
    window, outer = sql.split(") sum", 1)
    # The window is the best 10,000 the page thins, walked as unfiltered.
    assert "LIMIT 10000\n" in window and "main_star_class" not in window
    assert "s.main_star_class IN ($1)" in outer
    assert "LIMIT $2\n" in outer
    assert params == ["K", RANKING_COUNT_SENTINEL]


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
        cap=1_000,
    )
    inner, outer = sql.split(") nearest", 1)
    assert "ORDER BY s.position_ly <->" in inner
    # Search columns alone order the nearest-first window, so the archetype
    # probe runs once per admitted row after it; the literal bound equals the
    # largest sentinel, and the parameterized sentinel decides.
    assert ", s.system_id64 ASC\n" in inner
    assert "weighted_potential" not in inner
    assert "system_archetype_summary" not in inner
    assert "LIMIT 1001" in inner
    assert "JOIN v3_app.system_archetype_summary sum ON sum.system_id64 = nearest.system_id64" in outer
    assert f"sum.derived_generation_id = {PIN}" in outer
    assert "nearest.distance_ly <= $4" in outer
    assert "LIMIT $5" in outer
    assert params[-1] == 1_001


def test_anchored_count_keeps_summary_filters_inside_the_nearest_window():
    sql, params = build_count_query(
        PROFILE_SPEC,
        picked_archetype=None,
        picked_economy=None,
        hard_filters={"max_distance_ly": 500.0, "min_development_score": 60},
        reference_coords=(1.0, 2.0, 3.0),
        sort="distance",
        cap=1_000,
    )
    inner, outer = sql.split(") nearest", 1)
    assert "JOIN v3_app.system_archetype_summary sum ON sum.system_id64 = s.system_id64" in inner
    assert "sum.best_colony_potential >= $5" in inner
    assert "system_archetype_summary" not in outer
    assert params == [1.0, 2.0, 3.0, 500.0, 60, 1_001]


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
    assert (
        "ORDER BY candidates.primary_score DESC, "
        "candidates.weighted_potential DESC NULLS LAST, "
        "candidates.system_id64 ASC" in outer
    )
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
    _picked_page(limit=1, offset=999)
    with pytest.raises(ValueError):
        _picked_page(limit=2, offset=999)
    with pytest.raises(ValueError):
        _picked_page(limit=1, offset=1_000)
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
        "order_rule",
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
