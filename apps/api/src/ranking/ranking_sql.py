"""Bounded SQL builders for the V3 Finder ranking profile.

The picked-archetype path reads migration 011's wide row. Identifiers come
only from ``PROFILE_SPEC``; values remain asyncpg parameters, except for the
three server-owned score-floor literals required for partial-index planning.

Every statement shape here is also proven against the frozen F2d plan probe
(``scripts/v3_archetype_plan_probe.py``) as generic plans at production scale:
see ``tests/test_v3_finder_galaxy_window_plan_pg18.py``.

``hide_claimed`` applies Find's claim rule (``edfinder_api.claim_rule``): it
leaves out inhabited systems and systems someone is claiming right now. The
rule is part of what qualifies a system, so every bounded window applies it
inside: a window holds the best 10,000 systems that can still be claimed, a
nearest-first page is full even next to an inhabited anchor, and each count
agrees with its page. It is one primary-key probe per row the window reads and
keeps the window's index order. The score-ordered radius page is the one
exception in placement only: its candidates subquery already returns every
system in the radius, so the rule is applied to the candidates and the GiST
walk inside is planned as before.

A picked archetype with an anchor and a radius ("this colony type near that
system") walks the nearest-first window like the Any search does, with the
type's score floor as one more condition on each row, and returns its rows
nearest first. Each row also carries the type's score and stored tier. The
whole radius is never sorted by score.

``reach_ly`` applies Find's reach rule (``edfinder_api.reach_rule``): it keeps
only systems within 250 ly (or 500 ly) of inhabited space. It is placed exactly
where the claim rule is, right after it, for the same reasons: one primary-key
probe per row a window reads. ``None`` leaves it out.

``hide_claimed`` also leaves out the systems in ``edfinder_api.fake_systems``,
which are not in the game. It is placed last, after the reach rule: a plain
comparison of each row's id64 against a short server-owned list.
"""

from __future__ import annotations

from typing import Any, Final

from edfinder_api.claim_rule import not_claimed_sql
from edfinder_api.fake_systems import not_fake_sql
from edfinder_api.reach_rule import checked_reach, in_reach_sql

from .profile import (
    ACCEPTED_ARCHETYPE_SCORE_FLOORS,
    ARCHETYPE_SCORE_FLOOR_DEFAULT,
    BODY_COUNT_COLUMNS,
    FIND_COUNT_LIMIT,
    HARD_FILTER_KEYS,
    PROFILE_SPEC,
    RANKING_COUNT_SENTINEL,
    RANKING_OFFSET_MAX,
    RANKING_OFFSET_MIN,
    RANKING_OFFSET_PLUS_LIMIT_MAX,
    RANKING_PAGE_LIMIT_MAX,
    RANKING_PAGE_LIMIT_MIN,
    RANKING_WINDOW_LIMIT,
)


# Derived from the hashed profile, never maintained as a second identifier
# list in SQL code. A copy prevents accidental mutation of the profile.
ARCHETYPE_COLUMNS: Final[dict[str, dict[str, str]]] = {
    key: dict(columns)
    for key, columns in PROFILE_SPEC["archetype_columns"].items()
}
_PICKED_SCORE_COLUMNS: Final[str] = "".join(
    f"       picked.{columns['score']} AS picked_{key}_score,\n"
    for key, columns in ARCHETYPE_COLUMNS.items()
)
_FLOOR_SQL: Final[dict[int, str]] = {
    floor: str(floor) for floor in ACCEPTED_ARCHETYPE_SCORE_FLOORS
}

SORT_SCORE: Final[str] = "score"
SORT_DISTANCE: Final[str] = "distance"
SUPPORTED_SORTS: Final[frozenset[str]] = frozenset({SORT_SCORE, SORT_DISTANCE})

# The current derived generation as an uncorrelated scalar subquery: PostgreSQL
# runs it once as an InitPlan, so even a generic plan sees the generation as a
# pseudo-constant. A ranked window over a whole generation needs this: the
# v3_app views carry derived_generation_id only through their join to the
# pointer table, and without a constant the leading index column cannot be
# skipped, so no generation-led index can supply the window ORDER BY and the
# planner sorts the entire ~198.5M-row generation (production plan-probe
# timeout, 2026-10-22). The views still apply the full PUBLISHED/READY/
# published product fence; the pin only names the same singleton row.
CURRENT_GENERATION_PIN: Final[str] = (
    "(SELECT pinned.derived_generation_id "
    "FROM v3_meta.current_derived_generation pinned WHERE pinned.singleton)"
)

_BODY_COUNT_COLUMN_SET = frozenset(BODY_COUNT_COLUMNS)
# Hard filters that read the archetype summary rather than system_search.
_SUMMARY_FILTER_KEYS = frozenset({"min_development_score"})
_COUNT_BOUND_OPS = (("_min", ">="), ("_max", "<="))


class _ParamBuilder:
    def __init__(self) -> None:
        self.params: list[Any] = []

    def add(self, value: Any) -> str:
        self.params.append(value)
        return f"${len(self.params)}"


def _count_filter_column(key: str) -> tuple[str, str] | None:
    for suffix, operator in _COUNT_BOUND_OPS:
        column = key[: -len(suffix)] if key.endswith(suffix) else ""
        if column in _BODY_COUNT_COLUMN_SET:
            return column, operator
    return None


def _require_spec(spec: dict[str, Any]) -> None:
    """Bind emitted SQL to every hashed profile field it implements."""

    checked = (
        "primary_score_rule",
        "uncertainty",
        "tie_break",
        "archetype_keys",
        "archetype_columns",
        "archetype_score_floor",
        "stored_weighted_key_expression",
        "ranking_window",
        "picked_archetype_rejected_filters",
        "picked_archetype_filter_rejection_reason",
        "local_search_contract",
    )
    mismatched = [
        name for name in checked if spec.get(name) != PROFILE_SPEC.get(name)
    ]
    if mismatched:
        raise ValueError(
            "ranking spec is not implemented by this SQL builder; mismatched "
            f"field(s): {', '.join(mismatched)}"
        )


def _picked_columns(picked_archetype: str) -> dict[str, str]:
    try:
        return ARCHETYPE_COLUMNS[picked_archetype]
    except KeyError as exc:
        raise ValueError(f"unknown archetype key: {picked_archetype!r}") from exc


def _floor_literal(min_score: int | None) -> str:
    if min_score is None:
        min_score = ARCHETYPE_SCORE_FLOOR_DEFAULT
    try:
        return _FLOOR_SQL[int(min_score)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "min_score must be one of "
            f"{list(ACCEPTED_ARCHETYPE_SCORE_FLOORS)!r}"
        ) from exc


def _distance_expr(
    builder: _ParamBuilder,
    reference_coords: tuple[float, float, float],
) -> str:
    x, y, z = (builder.add(float(value)) for value in reference_coords)
    return (
        "sqrt("
        f"(s.x_ly - {x}) ^ 2 + "
        f"(s.y_ly - {y}) ^ 2 + "
        f"(s.z_ly - {z}) ^ 2"
        ")"
    )


def _radius_geometry(
    builder: _ParamBuilder,
    reference_coords: tuple[float, float, float],
    radius: float,
) -> tuple[str, str, str]:
    """Return ``(anchor, bounding_cube, radius_param)`` with shared binds."""

    x, y, z = (builder.add(float(value)) for value in reference_coords)
    radius_param = builder.add(float(radius))
    fx, fy, fz, fr = (
        f"{param}::double precision" for param in (x, y, z, radius_param)
    )
    anchor = f"cube(array[{fx}, {fy}, {fz}])"
    bounding_cube = (
        "cube("
        f"array[{fx} - {fr}, {fy} - {fr}, {fz} - {fr}], "
        f"array[{fx} + {fr}, {fy} + {fr}, {fz} + {fr}]"
        ")"
    )
    return anchor, bounding_cube, radius_param


def _no_pick_filters(
    builder: _ParamBuilder,
    *,
    hard_filters: dict[str, Any],
    reference_coords: tuple[float, float, float] | None,
    omit_max_distance: bool = False,
    search: str = "s",
) -> list[str]:
    """Hard-filter clauses over the search row ``search`` (alias) and the
    summary row ``sum``."""
    clauses: list[str] = []
    for key, value in hard_filters.items():
        if key not in HARD_FILTER_KEYS:
            continue
        count_filter = _count_filter_column(key)
        if count_filter:
            column, operator = count_filter
            clauses.append(f"{search}.{column} {operator} {builder.add(int(value))}")
        elif key in {"has_rings", "has_biologicals", "has_geologicals"}:
            clauses.append(f"{search}.{key} = {builder.add(bool(value))}")
        elif key == "main_star_class_in":
            values = [builder.add(item) for item in value]
            if values:
                clauses.append(f"{search}.main_star_class IN ({', '.join(values)})")
        elif key == "min_development_score":
            clauses.append(
                f"sum.best_colony_potential >= {builder.add(int(value))}"
            )
        elif key == "galaxy_region":
            clauses.append(f"{search}.galaxy_region_id = {builder.add(int(value))}")
        elif key == "max_distance_ly":
            if reference_coords is None or omit_max_distance:
                continue
            anchor, bounding_cube, radius_param = _radius_geometry(
                builder, reference_coords, float(value)
            )
            clauses.extend(
                (
                    f"s.position_ly <@ {bounding_cube}",
                    f"s.position_ly <-> {anchor} <= {radius_param}",
                )
            )
        elif key == "min_distance_ly" and reference_coords is not None:
            clauses.append(
                f"{_distance_expr(builder, reference_coords)} "
                f">= {builder.add(float(value))}"
            )
    return clauses


def _rules(system_id_sql: str, hide_claimed: bool, reach_ly: int | None) -> list[str]:
    """The claim rule, then the reach rule, as asked for. Find's view
    (``hide_claimed``) also leaves out the known fake systems, last."""
    clauses: list[str] = []
    if hide_claimed:
        clauses.append(not_claimed_sql(system_id_sql))
    if reach_ly is not None:
        clauses.append(in_reach_sql(system_id_sql, reach_ly))
    if hide_claimed:
        clauses.append(not_fake_sql(system_id_sql))
    return clauses


def _and_lines(clauses: list[str], indent: str) -> str:
    """Each clause as its own ``AND`` line after a WHERE, or nothing."""
    return "".join(f"{indent}AND {clause}\n" for clause in clauses)


def _pinned(alias: str) -> str:
    return f"{alias}.derived_generation_id = {CURRENT_GENERATION_PIN}"


def _picked_page_query(
    builder: _ParamBuilder,
    *,
    picked_archetype: str,
    min_score: int | None,
    limit: int,
    offset: int,
    hide_claimed: bool = False,
    reach_ly: int | None = None,
    hard_filters: dict[str, Any] | None = None,
) -> str:
    columns = _picked_columns(picked_archetype)
    floor = _floor_literal(min_score)
    score = columns["score"]
    tier = columns["tier"]
    claim_filter = _and_lines(
        _rules("ar.system_id64", hide_claimed, reach_ly), "      "
    )
    # Body filters thin the type's window of the best 10,000; they never
    # reach the window walk itself.
    member_filters = _and_lines(
        _no_pick_filters(
            builder,
            hard_filters=hard_filters or {},
            reference_coords=None,
            search="member",
        ),
        "      ",
    )
    confidence = columns["confidence"]
    weighted = columns["weighted_key"]
    limit_param = builder.add(int(limit))
    offset_param = builder.add(int(offset))
    order = "selected_archetype_weighted_e4 DESC, system_id64 ASC"
    confidence_expr = confidence.replace(
        f"{picked_archetype}_confidence_ppm",
        f"picked.{picked_archetype}_confidence_ppm",
    )

    # Keep the partial-index window unchanged, then page only its keys after
    # the same published-search membership join as the count. Missing search
    # rows are excluded before OFFSET/LIMIT, preserving page contents. Search
    # details are fetched only for those page keys, so a deep offset does not
    # join or sort 10,000 wide search rows.
    # The final fetch reads the physical row of the generation the fenced
    # page already came from: every page row was admitted by both v3_app
    # views, so the fetch adds no visibility, and keyed by (generation,
    # system) it is one primary-key probe per page row. The fetched weighted
    # key is the window's own value for that row, so ordering by it returns
    # the window order; it makes the final sort run over the page rows only.
    return (
        "WITH archetype_window AS (\n"
        "    SELECT ar.system_id64, "
        f"ar.{weighted} AS selected_archetype_weighted_e4\n"
        "    FROM v3_app.system_archetype ar\n"
        f"    WHERE {_pinned('ar')}\n"
        f"      AND ar.{score} >= {floor}\n"
        f"{claim_filter}"
        f"    ORDER BY ar.{weighted} DESC, ar.system_id64 ASC\n"
        f"    LIMIT {RANKING_WINDOW_LIMIT}\n"
        "), ranked_page_keys AS (\n"
        "    SELECT aw.system_id64, aw.selected_archetype_weighted_e4\n"
        "    FROM archetype_window aw\n"
        "    JOIN v3_app.system_search member ON member.system_id64 = aw.system_id64\n"
        f"    WHERE {_pinned('member')}\n"
        f"{member_filters}"
        f"    ORDER BY {order}\n"
        f"    LIMIT {limit_param} OFFSET {offset_param}\n"
        "), ranked_page AS (\n"
        "    SELECT s.*, page_keys.selected_archetype_weighted_e4\n"
        "    FROM ranked_page_keys page_keys\n"
        "    JOIN v3_app.system_search s ON s.system_id64 = page_keys.system_id64\n"
        f"    WHERE {_pinned('s')}\n"
        ")\n"
        "SELECT page.*,\n"
        f"       picked.{score} AS primary_score,\n"
        f"       picked.{tier} AS selected_archetype_tier,\n"
        f"       {confidence_expr} AS ranking_confidence,\n"
        f"       ({confidence_expr} * page.completeness) AS uncertainty_factor,\n"
        "       picked.primary_archetype AS summary_primary_archetype,\n"
        "       picked.secondary_archetype AS summary_secondary_archetype,\n"
        "       picked.best_colony_potential AS summary_best_colony_potential,\n"
        "       (picked.archetype_confidence_ppm / 1000000.0)::double precision "
        "AS summary_archetype_confidence,\n"
        f"{_PICKED_SCORE_COLUMNS}"
        "       picked.weighted_potential AS weighted_potential\n"
        "FROM ranked_page page\n"
        "JOIN v3_derived.system_archetype picked\n"
        "  ON picked.derived_generation_id = page.derived_generation_id\n"
        " AND picked.system_id64 = page.system_id64\n"
        f"ORDER BY picked.{weighted} DESC, page.system_id64 ASC"
    )


def _picked_count_query(
    *,
    picked_archetype: str,
    min_score: int | None,
    hide_claimed: bool = False,
    reach_ly: int | None = None,
    builder: _ParamBuilder | None = None,
    hard_filters: dict[str, Any] | None = None,
) -> str:
    columns = _picked_columns(picked_archetype)
    floor = _floor_literal(min_score)
    score = columns["score"]
    weighted = columns["weighted_key"]
    claim_filter = _and_lines(
        _rules("ar.system_id64", hide_claimed, reach_ly), "          "
    )
    filters = _no_pick_filters(
        builder or _ParamBuilder(),
        hard_filters=hard_filters or {},
        reference_coords=None,
    )
    # Filtered, the count reads the same 10,000-row window the page thins and
    # stops at the sentinel; unfiltered, the window itself walks only to the
    # sentinel.
    window = RANKING_WINDOW_LIMIT if filters else RANKING_COUNT_SENTINEL
    bound = f"    LIMIT {RANKING_COUNT_SENTINEL}\n" if filters else ""
    # The count is taken over the same archetype window the page ranks (the
    # sentinel instead of the 10,000-row window), joined to search exactly as
    # the page joins it. The window walks the matching partial index in its
    # own order (the literal floor proves the index to a generic plan) and
    # stops at the sentinel.
    return (
        "SELECT count(*) FROM (\n"
        "    SELECT 1\n"
        "    FROM (\n"
        "        SELECT ar.system_id64\n"
        "        FROM v3_app.system_archetype ar\n"
        f"        WHERE {_pinned('ar')}\n"
        f"          AND ar.{score} >= {floor}\n"
        f"{claim_filter}"
        f"        ORDER BY ar.{weighted} DESC, ar.system_id64 ASC\n"
        f"        LIMIT {window}\n"
        "    ) aw\n"
        "    JOIN v3_app.system_search s ON s.system_id64 = aw.system_id64\n"
        f"    WHERE {_pinned('s')}\n"
        f"{_and_lines(filters, '      ')}"
        f"{bound}"
        ") t"
    )


_NO_PICK_COLUMNS = (
    "SELECT s.*,\n"
    "       sum.best_colony_potential AS primary_score,\n"
    "       s.confidence AS ranking_confidence,\n"
    "       (s.confidence * s.completeness) AS uncertainty_factor,\n"
    "       sum.primary_archetype AS summary_primary_archetype,\n"
    "       sum.secondary_archetype AS summary_secondary_archetype,\n"
    "       sum.best_colony_potential AS summary_best_colony_potential,\n"
    "       sum.archetype_confidence AS summary_archetype_confidence,\n"
    "       sum.weighted_potential AS weighted_potential\n"
)
_NO_PICK_FROM = (
    "FROM v3_app.system_search s\n"
    "JOIN v3_app.system_archetype_summary sum ON sum.system_id64 = s.system_id64\n"
)


def _no_pick_knn_page(
    builder: _ParamBuilder,
    *,
    hard_filters: dict[str, Any],
    reference_coords: tuple[float, float, float],
    limit: int,
    offset: int,
    hide_claimed: bool = False,
    reach_ly: int | None = None,
) -> str:
    radius = float(hard_filters["max_distance_ly"])
    anchor, bounding_cube, radius_param = _radius_geometry(
        builder, reference_coords, radius
    )
    clauses = [_pinned("s"), _pinned("sum"), f"s.position_ly <@ {bounding_cube}"]
    clauses.extend(
        _no_pick_filters(
            builder,
            hard_filters=hard_filters,
            reference_coords=reference_coords,
            omit_max_distance=True,
        )
    )
    clauses.extend(_rules("s.system_id64", hide_claimed, reach_ly))
    inner_limit = builder.add(int(offset + limit))
    limit_param = builder.add(int(limit))
    offset_param = builder.add(int(offset))
    where_sql = "\n      AND ".join(clauses)
    return (
        "SELECT nearest.*\n"
        "FROM (\n"
        "    SELECT s.*,\n"
        "           sum.best_colony_potential AS primary_score,\n"
        "           s.confidence AS ranking_confidence,\n"
        "           (s.confidence * s.completeness) AS uncertainty_factor,\n"
        "           sum.primary_archetype AS summary_primary_archetype,\n"
        "           sum.secondary_archetype AS summary_secondary_archetype,\n"
        "           sum.best_colony_potential AS summary_best_colony_potential,\n"
        "           sum.archetype_confidence AS summary_archetype_confidence,\n"
        "           sum.weighted_potential AS weighted_potential,\n"
        f"           s.position_ly <-> {anchor} AS distance_ly\n"
        "    FROM v3_app.system_search s\n"
        "    JOIN v3_app.system_archetype_summary sum "
        "ON sum.system_id64 = s.system_id64\n"
        f"    WHERE {where_sql}\n"
        f"    ORDER BY s.position_ly <-> {anchor}, "
        "sum.weighted_potential DESC NULLS LAST, s.system_id64 ASC\n"
        f"    LIMIT {inner_limit}\n"
        ") nearest\n"
        f"WHERE nearest.distance_ly <= {radius_param}\n"
        "ORDER BY nearest.distance_ly ASC, "
        "nearest.weighted_potential DESC NULLS LAST, nearest.system_id64 ASC\n"
        f"LIMIT {limit_param} OFFSET {offset_param}"
    )


def _no_pick_knn_count(
    builder: _ParamBuilder,
    *,
    hard_filters: dict[str, Any],
    reference_coords: tuple[float, float, float],
    sentinel: int,
    hide_claimed: bool = False,
    reach_ly: int | None = None,
) -> str:
    radius = float(hard_filters["max_distance_ly"])
    anchor, bounding_cube, radius_param = _radius_geometry(
        builder, reference_coords, radius
    )
    filters = _no_pick_filters(
        builder,
        hard_filters=hard_filters,
        reference_coords=reference_coords,
        omit_max_distance=True,
    )
    filters.extend(_rules("s.system_id64", hide_claimed, reach_ly))
    sentinel_param = builder.add(int(sentinel))
    search_clauses = [_pinned("s"), f"s.position_ly <@ {bounding_cube}"]
    if any(key in hard_filters for key in _SUMMARY_FILTER_KEYS):
        # A summary-column filter decides which rows are nearest, so the
        # archetype join stays inside the nearest-first window.
        inner_from = (
            "        FROM v3_app.system_search s\n"
            "        JOIN v3_app.system_archetype_summary sum "
            "ON sum.system_id64 = s.system_id64\n"
        )
        inner_clauses = [*search_clauses, _pinned("sum"), *filters]
        outer_join = ""
        outer_clauses = [f"nearest.distance_ly <= {radius_param}"]
    else:
        # Both Finder products cover exactly the generation's systems (product
        # validation and the publication gate), so the nearest search rows
        # are the nearest ranked rows. Only the distance prefix decides which
        # rows fall inside the radius, so the tie-break uses search columns
        # and the archetype probe runs after the nearest-first window, once
        # per row the sentinel admits.
        inner_from = "        FROM v3_app.system_search s\n"
        inner_clauses = [*search_clauses, *filters]
        outer_join = (
            "    JOIN v3_app.system_archetype_summary sum "
            "ON sum.system_id64 = nearest.system_id64\n"
        )
        outer_clauses = [_pinned("sum"), f"nearest.distance_ly <= {radius_param}"]
    inner_where = "\n          AND ".join(inner_clauses)
    outer_where = "\n      AND ".join(outer_clauses)
    # The literal window bound equals the largest accepted sentinel, so it
    # never truncates the parameterized sentinel; it gives a generic plan a
    # known row bound.
    return (
        "SELECT count(*) FROM (\n"
        "    SELECT nearest.system_id64\n"
        "    FROM (\n"
        "        SELECT s.system_id64, "
        f"s.position_ly <-> {anchor} AS distance_ly\n"
        f"{inner_from}"
        f"        WHERE {inner_where}\n"
        f"        ORDER BY s.position_ly <-> {anchor}, s.system_id64 ASC\n"
        f"        LIMIT {RANKING_COUNT_SENTINEL}\n"
        "    ) nearest\n"
        f"{outer_join}"
        f"    WHERE {outer_where}\n"
        f"    LIMIT {sentinel_param}\n"
        ") bounded"
    )


def _picked_knn_page(
    builder: _ParamBuilder,
    *,
    picked_archetype: str,
    min_score: int | None,
    hard_filters: dict[str, Any],
    reference_coords: tuple[float, float, float],
    limit: int,
    offset: int,
    hide_claimed: bool = False,
    reach_ly: int | None = None,
) -> str:
    """Nearest-first page of the systems that fit a picked type.

    The nearest-first window of the Any search, joined to the wide archetype
    row instead of the summary view (both read v3_derived.system_archetype)
    so the type's score floor can be one more condition on each row. The row
    is aliased ``sum`` so the summary hard filters keep their meaning."""
    columns = _picked_columns(picked_archetype)
    floor = _floor_literal(min_score)
    score = columns["score"]
    tier = columns["tier"]
    weighted = columns["weighted_key"]
    radius = float(hard_filters["max_distance_ly"])
    anchor, bounding_cube, radius_param = _radius_geometry(
        builder, reference_coords, radius
    )
    clauses = [
        _pinned("s"),
        _pinned("sum"),
        f"s.position_ly <@ {bounding_cube}",
        f"sum.{score} >= {floor}",
    ]
    clauses.extend(
        _no_pick_filters(
            builder,
            hard_filters=hard_filters,
            reference_coords=reference_coords,
            omit_max_distance=True,
        )
    )
    clauses.extend(_rules("s.system_id64", hide_claimed, reach_ly))
    inner_limit = builder.add(int(offset + limit))
    limit_param = builder.add(int(limit))
    offset_param = builder.add(int(offset))
    where_sql = "\n      AND ".join(clauses)
    return (
        "SELECT nearest.*\n"
        "FROM (\n"
        "    SELECT s.*,\n"
        "           sum.best_colony_potential AS primary_score,\n"
        "           s.confidence AS ranking_confidence,\n"
        "           (s.confidence * s.completeness) AS uncertainty_factor,\n"
        "           sum.primary_archetype AS summary_primary_archetype,\n"
        "           sum.secondary_archetype AS summary_secondary_archetype,\n"
        "           sum.best_colony_potential AS summary_best_colony_potential,\n"
        "           (sum.archetype_confidence_ppm / 1000000.0)::double precision "
        "AS summary_archetype_confidence,\n"
        "           sum.weighted_potential AS weighted_potential,\n"
        f"           sum.{score} AS selected_archetype_score,\n"
        f"           sum.{tier} AS selected_archetype_tier,\n"
        f"           sum.{weighted} AS selected_archetype_weighted_e4,\n"
        f"           s.position_ly <-> {anchor} AS distance_ly\n"
        "    FROM v3_app.system_search s\n"
        "    JOIN v3_app.system_archetype sum ON sum.system_id64 = s.system_id64\n"
        f"    WHERE {where_sql}\n"
        f"    ORDER BY s.position_ly <-> {anchor}, "
        f"sum.{weighted} DESC, s.system_id64 ASC\n"
        f"    LIMIT {inner_limit}\n"
        ") nearest\n"
        f"WHERE nearest.distance_ly <= {radius_param}\n"
        "ORDER BY nearest.distance_ly ASC, "
        "nearest.selected_archetype_weighted_e4 DESC, nearest.system_id64 ASC\n"
        f"LIMIT {limit_param} OFFSET {offset_param}"
    )


def _picked_knn_count(
    builder: _ParamBuilder,
    *,
    picked_archetype: str,
    min_score: int | None,
    hard_filters: dict[str, Any],
    reference_coords: tuple[float, float, float],
    sentinel: int,
    hide_claimed: bool = False,
    reach_ly: int | None = None,
) -> str:
    """Count for ``_picked_knn_page``: the same nearest-first window, with the
    floor inside it, stopping at the sentinel."""
    columns = _picked_columns(picked_archetype)
    floor = _floor_literal(min_score)
    score = columns["score"]
    radius = float(hard_filters["max_distance_ly"])
    anchor, bounding_cube, radius_param = _radius_geometry(
        builder, reference_coords, radius
    )
    clauses = [
        _pinned("s"),
        _pinned("sum"),
        f"s.position_ly <@ {bounding_cube}",
        f"sum.{score} >= {floor}",
    ]
    clauses.extend(
        _no_pick_filters(
            builder,
            hard_filters=hard_filters,
            reference_coords=reference_coords,
            omit_max_distance=True,
        )
    )
    clauses.extend(_rules("s.system_id64", hide_claimed, reach_ly))
    sentinel_param = builder.add(int(sentinel))
    where_sql = "\n          AND ".join(clauses)
    return (
        "SELECT count(*) FROM (\n"
        "    SELECT nearest.system_id64\n"
        "    FROM (\n"
        "        SELECT s.system_id64, "
        f"s.position_ly <-> {anchor} AS distance_ly\n"
        "        FROM v3_app.system_search s\n"
        "        JOIN v3_app.system_archetype sum ON sum.system_id64 = s.system_id64\n"
        f"        WHERE {where_sql}\n"
        f"        ORDER BY s.position_ly <-> {anchor}, s.system_id64 ASC\n"
        f"        LIMIT {RANKING_COUNT_SENTINEL}\n"
        "    ) nearest\n"
        f"    WHERE nearest.distance_ly <= {radius_param}\n"
        f"    LIMIT {sentinel_param}\n"
        ") bounded"
    )


def _no_pick_page(
    builder: _ParamBuilder,
    *,
    hard_filters: dict[str, Any],
    reference_coords: tuple[float, float, float] | None,
    limit: int,
    offset: int,
    hide_claimed: bool = False,
    reach_ly: int | None = None,
) -> str:
    if (
        reference_coords is not None
        and hard_filters.get("max_distance_ly") is not None
    ):
        return _no_pick_radius_score_page(
            builder,
            hard_filters=hard_filters,
            reference_coords=reference_coords,
            limit=limit,
            offset=offset,
            hide_claimed=hide_claimed,
            reach_ly=reach_ly,
        )
    filters = _no_pick_filters(
        builder,
        hard_filters=hard_filters,
        reference_coords=reference_coords,
    )
    limit_param = builder.add(int(limit))
    offset_param = builder.add(int(offset))
    claim_filter = "".join(
        f"\n  AND {clause}"
        for clause in _rules("s.system_id64", hide_claimed, reach_ly)
    )
    window_claim_filter = "".join(
        f"\n      AND {clause}"
        for clause in _rules("sum.system_id64", hide_claimed, reach_ly)
    )
    if reference_coords is None:
        # Galaxy-wide, filters thin the window of the best 10,000 systems:
        # the walk is the same with or without them.
        window_filters = "".join(f"\n  AND {clause}" for clause in filters)
        return (
            "WITH summary_window AS (\n"
            "    SELECT sum.*\n"
            "    FROM v3_app.system_archetype_summary sum\n"
            f"    WHERE {_pinned('sum')}{window_claim_filter}\n"
            "    ORDER BY sum.weighted_potential DESC NULLS LAST, "
            "sum.system_id64 ASC\n"
            f"    LIMIT {RANKING_WINDOW_LIMIT}\n"
            ")\n"
            f"{_NO_PICK_COLUMNS}"
            "FROM summary_window sum\n"
            "JOIN v3_app.system_search s ON s.system_id64 = sum.system_id64\n"
            f"WHERE {_pinned('s')}{window_filters}\n"
            "ORDER BY sum.weighted_potential DESC NULLS LAST, s.system_id64 ASC\n"
            f"LIMIT {limit_param} OFFSET {offset_param}"
        )
    where_sql = "\n  AND ".join([_pinned("s"), _pinned("sum"), *filters])
    return (
        f"{_NO_PICK_COLUMNS}"
        f"{_NO_PICK_FROM}"
        f"WHERE {where_sql}{claim_filter}\n"
        "ORDER BY sum.weighted_potential DESC NULLS LAST, s.system_id64 ASC\n"
        f"LIMIT {limit_param} OFFSET {offset_param}"
    )


def _no_pick_radius_score_page(
    builder: _ParamBuilder,
    *,
    hard_filters: dict[str, Any],
    reference_coords: tuple[float, float, float],
    limit: int,
    offset: int,
    hide_claimed: bool = False,
    reach_ly: int | None = None,
) -> str:
    """Score-ordered page inside a radius.

    The candidate subquery is ordered by distance so PostgreSQL walks the
    bounding box with the GiST index (one probe per candidate per stored
    generation) instead of a bitmap heap scan joined by hash; the outer ORDER
    BY alone decides the page order, so the result is unchanged."""
    radius = float(hard_filters["max_distance_ly"])
    anchor, bounding_cube, radius_param = _radius_geometry(
        builder, reference_coords, radius
    )
    clauses = [
        _pinned("s"),
        _pinned("sum"),
        f"s.position_ly <@ {bounding_cube}",
        f"s.position_ly <-> {anchor} <= {radius_param}",
    ]
    clauses.extend(
        _no_pick_filters(
            builder,
            hard_filters=hard_filters,
            reference_coords=reference_coords,
            omit_max_distance=True,
        )
    )
    limit_param = builder.add(int(limit))
    offset_param = builder.add(int(offset))
    where_sql = "\n      AND ".join(clauses)
    columns = _NO_PICK_COLUMNS.replace("\n", "\n    ").rstrip()
    # The candidates subquery keeps its LIMIT, so PostgreSQL plans it on its
    # own: the claim rule out here cannot change the GiST walk inside.
    outside_rules = _rules("candidates.system_id64", hide_claimed, reach_ly)
    claim_where = (
        "WHERE " + "\n  AND ".join(outside_rules) + "\n" if outside_rules else ""
    )
    # NULLIF(x, x) is always NULL, i.e. LIMIT ALL: no candidate is ever
    # dropped. Written over a parameter it stays unknown to a generic plan,
    # which then costs walking the candidates in GiST distance order rather
    # than a bitmap heap scan plus a full sort; a custom plan folds it away.
    return (
        "SELECT candidates.*\n"
        "FROM (\n"
        f"    {columns}\n"
        "    FROM v3_app.system_search s\n"
        "    JOIN v3_app.system_archetype_summary sum "
        "ON sum.system_id64 = s.system_id64\n"
        f"    WHERE {where_sql}\n"
        f"    ORDER BY s.position_ly <-> {anchor}\n"
        f"    LIMIT NULLIF({limit_param}::bigint, {limit_param}::bigint)\n"
        ") candidates\n"
        f"{claim_where}"
        "ORDER BY candidates.weighted_potential DESC NULLS LAST, "
        "candidates.system_id64 ASC\n"
        f"LIMIT {limit_param} OFFSET {offset_param}"
    )


# Filters a picked type's galaxy-wide window can take: the ones over the
# search row. The window is the type's own ranking, so the overall-potential
# floor has no meaning there, and without a system there is no distance.
_PICKED_WINDOW_REFUSED_FILTERS: Final[frozenset[str]] = frozenset(
    {"min_development_score", "max_distance_ly", "min_distance_ly"}
)


def _check_picked_window_filters(hard_filters: dict[str, Any]) -> None:
    refused = sorted(set(hard_filters) & _PICKED_WINDOW_REFUSED_FILTERS)
    if refused:
        raise ValueError(
            "a picked type galaxy-wide does not accept " + ", ".join(refused)
        )


def _picked_near(
    reference_coords: tuple[float, float, float] | None,
    hard_filters: dict[str, Any],
    sort: str,
) -> bool:
    """Whether a picked type is asked for near a system. That search is
    nearest first only: ranking a whole radius by a type's score would sort
    every system in it."""
    if reference_coords is None or hard_filters.get("max_distance_ly") is None:
        return False
    if sort != SORT_DISTANCE:
        raise ValueError("a picked type near a system is sorted by distance")
    return True


def build_ranked_query(
    spec: dict[str, Any],
    *,
    picked_archetype: str | None,
    picked_economy: str | None,
    hard_filters: dict[str, Any],
    reference_coords: tuple[float, float, float] | None,
    limit: int,
    offset: int,
    sort: str = SORT_SCORE,
    min_score: int | None = None,
    hide_claimed: bool = False,
    reach_ly: int | None = None,
) -> tuple[str, list[Any]]:
    """Build a bounded ranked-page query and its asyncpg parameters.

    ``hide_claimed`` leaves out inhabited systems and systems being claimed,
    and ``reach_ly`` (250 or 500) systems further than that from inhabited
    space (see the module docstring for where each query shape applies them)."""

    _require_spec(spec)
    if reach_ly is not None:
        reach_ly = checked_reach(reach_ly)
    if picked_economy:
        raise ValueError("picked_economy has no V3 projection")
    if sort not in SUPPORTED_SORTS:
        raise ValueError(f"unsupported sort: {sort!r}")
    if (
        limit < RANKING_PAGE_LIMIT_MIN
        or limit > RANKING_PAGE_LIMIT_MAX
        or offset < RANKING_OFFSET_MIN
        or offset > RANKING_OFFSET_MAX
        or offset + limit > RANKING_OFFSET_PLUS_LIMIT_MAX
    ):
        raise ValueError("limit/offset go past what Find counts and pages")
    builder = _ParamBuilder()
    if picked_archetype is not None and _picked_near(
        reference_coords, hard_filters, sort
    ):
        sql = _picked_knn_page(
            builder,
            picked_archetype=picked_archetype,
            min_score=min_score,
            hard_filters=hard_filters,
            reference_coords=reference_coords,  # type: ignore[arg-type]
            limit=limit,
            offset=offset,
            hide_claimed=hide_claimed,
            reach_ly=reach_ly,
        )
    elif picked_archetype is not None:
        _check_picked_window_filters(hard_filters)
        sql = _picked_page_query(
            builder,
            picked_archetype=picked_archetype,
            min_score=min_score,
            limit=limit,
            offset=offset,
            hide_claimed=hide_claimed,
            reach_ly=reach_ly,
            hard_filters=hard_filters,
        )
    elif (
        sort == SORT_DISTANCE
        and reference_coords is not None
        and hard_filters.get("max_distance_ly") is not None
    ):
        sql = _no_pick_knn_page(
            builder,
            hard_filters=hard_filters,
            reference_coords=reference_coords,
            limit=limit,
            offset=offset,
            hide_claimed=hide_claimed,
            reach_ly=reach_ly,
        )
    else:
        sql = _no_pick_page(
            builder,
            hard_filters=hard_filters,
            reference_coords=reference_coords,
            limit=limit,
            offset=offset,
            hide_claimed=hide_claimed,
            reach_ly=reach_ly,
        )
    return sql, builder.params


def build_count_query(
    spec: dict[str, Any],
    *,
    picked_archetype: str | None,
    picked_economy: str | None,
    hard_filters: dict[str, Any],
    reference_coords: tuple[float, float, float] | None,
    cap: int | None = None,
    sort: str = SORT_SCORE,
    min_score: int | None = None,
    hide_claimed: bool = False,
    reach_ly: int | None = None,
) -> tuple[str, list[Any]]:
    """Build a count query; ``cap`` means a ``cap + 1`` sentinel limit.

    ``hide_claimed`` and ``reach_ly`` must match the page query it counts for."""

    _require_spec(spec)
    if reach_ly is not None:
        reach_ly = checked_reach(reach_ly)
    if picked_economy:
        raise ValueError("picked_economy has no V3 projection")
    if sort not in SUPPORTED_SORTS:
        raise ValueError(f"unsupported sort: {sort!r}")
    # Find counts no further than FIND_COUNT_LIMIT. The SQL's literal window
    # bounds (RANKING_COUNT_SENTINEL) rely on this: no sentinel exceeds them.
    if cap is not None and (cap < 1 or cap > FIND_COUNT_LIMIT):
        raise ValueError("count cap goes past what Find counts")
    builder = _ParamBuilder()
    if picked_archetype is not None and _picked_near(
        reference_coords, hard_filters, sort
    ):
        if cap is None:
            raise ValueError("a picked type near a system needs a count cap")
        sql = _picked_knn_count(
            builder,
            picked_archetype=picked_archetype,
            min_score=min_score,
            hard_filters=hard_filters,
            reference_coords=reference_coords,  # type: ignore[arg-type]
            sentinel=int(cap) + 1,
            hide_claimed=hide_claimed,
            reach_ly=reach_ly,
        )
        return sql, builder.params
    if picked_archetype is not None:
        _check_picked_window_filters(hard_filters)
        sql = _picked_count_query(
            picked_archetype=picked_archetype,
            min_score=min_score,
            hide_claimed=hide_claimed,
            reach_ly=reach_ly,
            builder=builder,
            hard_filters=hard_filters,
        )
        return sql, builder.params

    sentinel = int(cap) + 1 if cap is not None else None
    if (
        sort == SORT_DISTANCE
        and reference_coords is not None
        and hard_filters.get("max_distance_ly") is not None
        and sentinel is not None
    ):
        sql = _no_pick_knn_count(
            builder,
            hard_filters=hard_filters,
            reference_coords=reference_coords,
            sentinel=sentinel,
            hide_claimed=hide_claimed,
            reach_ly=reach_ly,
        )
        return sql, builder.params

    filters = _no_pick_filters(
        builder,
        hard_filters=hard_filters,
        reference_coords=reference_coords,
    )
    window_claim_filter = "".join(
        f"\n          AND {clause}"
        for clause in _rules("sum.system_id64", hide_claimed, reach_ly)
    )
    if reference_coords is None and filters:
        # Filtered galaxy-wide: over the 10,000-row window the page thins,
        # stopping at the sentinel when there is one.
        bound = ""
        if sentinel is not None:
            bound = f"    LIMIT {builder.add(sentinel)}\n"
        sql = (
            "SELECT count(*) FROM (\n"
            "    SELECT 1\n"
            "    FROM (\n"
            "        SELECT sum.system_id64, sum.best_colony_potential\n"
            "        FROM v3_app.system_archetype_summary sum\n"
            f"        WHERE {_pinned('sum')}{window_claim_filter}\n"
            "        ORDER BY sum.weighted_potential DESC NULLS LAST, "
            "sum.system_id64 ASC\n"
            f"        LIMIT {RANKING_WINDOW_LIMIT}\n"
            "    ) sum\n"
            "    JOIN v3_app.system_search s ON s.system_id64 = sum.system_id64\n"
            f"    WHERE {_pinned('s')}\n"
            f"{_and_lines(filters, '      ')}"
            f"{bound}"
            ") bounded"
        )
        return sql, builder.params
    if sentinel is not None and not filters:
        # The galaxy-wide count is taken over the same summary window the
        # galaxy page ranks (the sentinel instead of the 10,000-row window),
        # joined to search exactly as the page joins it. The literal window
        # bound equals the largest accepted sentinel, so it never truncates the
        # parameterized sentinel; it gives a generic plan a known row bound.
        sentinel_param = builder.add(sentinel)
        sql = (
            "SELECT count(*) FROM (\n"
            "    SELECT 1\n"
            "    FROM (\n"
            "        SELECT sum.system_id64\n"
            "        FROM v3_app.system_archetype_summary sum\n"
            f"        WHERE {_pinned('sum')}{window_claim_filter}\n"
            "        ORDER BY sum.weighted_potential DESC NULLS LAST, "
            "sum.system_id64 ASC\n"
            f"        LIMIT {RANKING_COUNT_SENTINEL}\n"
            "    ) sum\n"
            "    JOIN v3_app.system_search s ON s.system_id64 = sum.system_id64\n"
            f"    WHERE {_pinned('s')}\n"
            f"    LIMIT {sentinel_param}\n"
            ") bounded"
        )
        return sql, builder.params
    clauses = [_pinned("sum"), _pinned("s"), *filters]
    clauses.extend(_rules("s.system_id64", hide_claimed, reach_ly))
    where_sql = "\n  AND ".join(clauses)
    base = (
        "SELECT 1\n"
        "FROM v3_app.system_archetype_summary sum\n"
        "JOIN v3_app.system_search s ON s.system_id64 = sum.system_id64\n"
        f"WHERE {where_sql}\n"
    )
    if sentinel is None:
        sql = "SELECT count(*)\n" + base.replace("SELECT 1\n", "", 1)
    else:
        sentinel_param = builder.add(sentinel)
        sql = f"SELECT count(*) FROM (\n{base}LIMIT {sentinel_param}\n) bounded"
    return sql.rstrip(), builder.params
