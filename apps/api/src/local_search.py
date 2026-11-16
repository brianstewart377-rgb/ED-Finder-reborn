"""
Find: the search behind `/api/local/search` (`local_db_search_v3`), near a
star or galaxy-wide, over the published V3 projections, with the count of
matches asked for within a time budget.

The rest of Find lives beside this file:

  • find_generation.py: the published data set every Finder read is pinned to;
  • find_count.py: the count of matches, and keeping it for a search's other pages;
  • find_autocomplete.py: name suggestions for `/api/local/autocomplete`;
  • find_status.py: the database status for `/api/local/status`.

Every search reads only the published V3 projections behind the
migration-015 Finder product gate. The V2 search and system-page readers
were removed on 15 November 2026, and `/api/search/galaxy` and
`/api/search/cluster` (which no page called) on 16 November; git history
keeps them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import math
import os
import time
from typing import Any

import asyncpg
from fastapi import HTTPException

from edfinder_api.claim_rule import apply_claim_notes, claim_notes
from edfinder_api.find_count import (
    _bounded_v3_total,
    count_within,
    known_qualifying,
    total_from_short_page,
)
from edfinder_api.find_generation import _current_derived_generation
from edfinder_api.reach_rule import checked_reach
from edfinder_api.ranking.profile import (
    ACCEPTED_ARCHETYPE_SCORE_FLOORS,
    ARCHETYPE_KEYS,
    ARCHETYPE_SCORE_FLOOR_DEFAULT,
    LOCAL_SEARCH_ANCHORED_RADIUS_REJECTION_REASON,
    LOCAL_SEARCH_FILTERED_ANCHORED_RADIUS_CAP_LY,
    LOCAL_SEARCH_FROM_MIN,
    LOCAL_SEARCH_GALAXY_WIDE_FILTER_REJECTION_REASON,
    LOCAL_SEARCH_MAX_RADIUS_DEFAULT_LY,
    LOCAL_SEARCH_REQUEST_RADIUS_DEFAULT_LY,
    LOCAL_SEARCH_RESULT_CAP,
    LOCAL_SEARCH_SIZE_MIN,
    PROFILE_SPEC,
    RANKING_VERSION,
    TIER_THRESHOLDS,
    ranking_sha256,
)
from edfinder_api.ranking.ranking_sql import (
    build_count_query,
    build_ranked_query,
    SORT_DISTANCE,
    SORT_SCORE,
)
from edfinder_api.search_economies import (
    ratings_score_column,
    archetype_score_column,
    BODY_FILTER_COLS,
    BODY_FILTER_ALIASES,
    normalise_body_filters,
)

log = logging.getLogger("ed-finder.local_search")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MAX_SEARCH_RADIUS = float(os.getenv(
    "MAX_SEARCH_RADIUS_LY", str(LOCAL_SEARCH_MAX_RADIUS_DEFAULT_LY),
))
DEFAULT_PAGE_SIZE = 50_000
MAX_PAGE_SIZE     = 200_000
CLUSTER_RADIUS_LY = float(os.getenv("CLUSTER_RADIUS_LY", "500"))
V3_DEFAULT_PAGE_SIZE = 50


@dataclass(frozen=True)
class LocalSearchContext:
    size: int
    from_idx: int
    sort_by: str
    galaxy_wide: bool
    has_reference_coords: bool
    rx: float
    ry: float
    rz: float
    min_dist: float
    max_dist_req: float
    max_dist: float
    radius_capped: bool
    require_empty: bool
    population_comparison: str | None
    population_value: int | None
    body_filters: dict
    require_bio: bool
    require_geo: bool
    require_terra: bool
    star_types: list
    min_development_score: int
    economy_filter: Any
    sec_econ_filter: Any
    galaxy_region_id: Any
    display_score_col: str
    finder_score_expr: str


@dataclass
class SearchSqlBuilder:
    params: list[Any] = field(default_factory=list)
    wheres: list[str] = field(default_factory=list)
    select_params: list[Any] = field(default_factory=list)
    next_param_index: int = 1

    def add_where(self, val: Any) -> str:
        self.params.append(val)
        idx = self.next_param_index
        self.next_param_index += 1
        return f"${idx}"

    def add_select(self, val: Any) -> str:
        self.params.append(val)
        self.select_params.append(val)
        idx = self.next_param_index
        self.next_param_index += 1
        return f"${idx}"

    @property
    def where_sql(self) -> str:
        return ("WHERE " + " AND ".join(self.wheres)) if self.wheres else ""


def _parse_local_search_context(body: dict) -> LocalSearchContext:
    filters = body.get("filters", {})
    # The public router sends ``None`` when a galaxy-wide request omits
    # reference coordinates.  Treat that the same as an absent mapping; local
    # searches still fail closed below when no complete coordinates exist.
    ref = body.get("reference_coords") or {}
    size = min(int(body.get("size", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
    from_idx = int(body.get("from", 0))
    sort_by = body.get("sort_by", "development")
    galaxy_wide = bool(body.get("galaxy_wide", False))

    ref_has_coords = all(ref.get(axis) is not None for axis in ("x", "y", "z"))
    if not galaxy_wide and not ref_has_coords:
        raise ValueError("reference_coords must include x, y, z for distance-bounded search")
    rx = float(ref["x"]) if ref_has_coords else 0.0
    ry = float(ref["y"]) if ref_has_coords else 0.0
    rz = float(ref["z"]) if ref_has_coords else 0.0

    # RangeFilter.model_dump() emits omitted bounds as an explicit ``None``
    # (the key is present), so ``dict.get("min", 0)`` returns ``None`` for a
    # one-sided range and ``float(None)`` used to raise TypeError -> 503 for a
    # perfectly valid ``{"max": 500}`` / ``{"min": 10}`` request. Normalise the
    # optional bounds explicitly instead.
    dist_filter = filters.get("distance") or {}
    _min_raw = dist_filter.get("min")
    _max_raw = dist_filter.get("max")
    min_dist = float(_min_raw) if _min_raw is not None else 0.0
    max_dist_req = (
        float(_max_raw)
        if _max_raw is not None
        else float(LOCAL_SEARCH_REQUEST_RADIUS_DEFAULT_LY)
    )
    max_dist = min(max_dist_req, MAX_SEARCH_RADIUS)
    radius_capped = max_dist < max_dist_req

    # Reject non-finite coordinates/distances up front. `float('nan')`/`inf`
    # pass Pydantic's float coercion, then flow into the Euclidean distance and
    # the cube bounding predicate: a galaxy-wide request with an infinite anchor
    # returns every row with distance=inf and fails JSON serialisation, while a
    # distance-bounded request errors inside the cube predicate and surfaces as
    # a misleading 503. Fail closed with a 422 instead.
    if not all(math.isfinite(v) for v in (rx, ry, rz, min_dist, max_dist_req)):
        raise HTTPException(422, 'reference_coords and distance bounds must be finite numbers')

    pop_filter = filters.get("population", {})
    pop_val_raw = pop_filter.get("value")
    try:
        pop_val = int(pop_val_raw) if pop_val_raw is not None else None
    except (TypeError, ValueError):
        pop_val = None
    pop_cmp = str(pop_filter.get("comparison", "equal") or "equal").strip().lower()
    require_empty = pop_val == 0 and pop_cmp in ("equal", "=", "==")

    body_filters = body.get("body_filters", {}) or {}
    require_bio = bool(body.get("require_bio", False))
    require_geo = bool(body.get("require_geo", False))
    require_terra = bool(body.get("require_terra", False))
    star_types = body.get("star_types", []) or []
    min_development_score = int(body.get("min_development_score", 0))
    economy_filter = body.get("economy") or body.get("filters", {}).get("economy")
    sec_econ_filter = body.get("secondary_economy")
    galaxy_region_id = body.get("galaxy_region_id")

    display_score_col = ratings_score_column(economy_filter, alias="r")
    primary_score_col = archetype_score_column(economy_filter, alias="m")
    finder_score_expr = f"COALESCE({primary_score_col}, {display_score_col})"

    return LocalSearchContext(
        size=size,
        from_idx=from_idx,
        sort_by=sort_by,
        galaxy_wide=galaxy_wide,
        has_reference_coords=ref_has_coords,
        rx=rx,
        ry=ry,
        rz=rz,
        min_dist=min_dist,
        max_dist_req=max_dist_req,
        max_dist=max_dist,
        radius_capped=radius_capped,
        require_empty=require_empty,
        population_comparison=pop_cmp if pop_val is not None else None,
        population_value=pop_val,
        body_filters=body_filters,
        require_bio=require_bio,
        require_geo=require_geo,
        require_terra=require_terra,
        star_types=star_types,
        min_development_score=min_development_score,
        economy_filter=economy_filter,
        sec_econ_filter=sec_econ_filter,
        galaxy_region_id=galaxy_region_id,
        display_score_col=display_score_col,
        finder_score_expr=finder_score_expr,
    )


# ---------------------------------------------------------------------------
# V3 ranked search (F3 slice 1) — POST /api/local/search.
# ---------------------------------------------------------------------------
# `/api/local/search` reads `local_db_search_v3`. The "ONE search
# implementation" rule in `routers/search.py` is per route: each search route
# has exactly one read path and never a competing fallback.
#
# `star_types`, `require_bio`, and `require_geo` map onto the
# `main_star_class_in` / `has_biologicals` / `has_geologicals`
# `ranking.profile.HARD_FILTER_KEYS` entries (see `_v3_hard_filters` below).
# `population`/min-population filters have no corresponding
# `v3_app.system_search` column at all (no population projection exists in
# the F1 relation), so `local_db_search_v3` rejects a request that asks for
# one with HTTP 422 rather than silently ignoring it — silently dropping a
# filter a caller explicitly asked for would return results the caller
# would read as "no matches for that filter" when really the filter was
# never applied.
# Body-filter request keys that resolve to a real `v3_app.system_search`
# column: the canonical/short names (`BODY_FILTER_COLS`) plus the camelCase
# aliases (`BODY_FILTER_ALIASES`). A body filter outside this set has no
# projection, so `_v3_hard_filters` rejects it (422) rather than dropping it.
_KNOWN_BODY_FILTER_KEYS: frozenset[str] = frozenset(BODY_FILTER_COLS) | frozenset(BODY_FILTER_ALIASES)

# `LocalSearchRequest.sort_by` -> ranking-builder ordering mode. The request
# model already lowercases and maps the legacy 'rating' alias onto
# 'development'; anything not in this map is rejected (422) by
# `local_db_search_v3` rather than silently ignored.
_SORT_BY_TO_MODE: dict[str, str] = {
    'development': SORT_SCORE,
    'distance': SORT_DISTANCE,
}


def _v3_hard_filters(ctx: LocalSearchContext) -> dict[str, Any]:
    """Map `LocalSearchContext` body/feature filters onto the
    `HARD_FILTER_KEYS` convention.

    Every declared body-count field resolves (via `BODY_FILTER_COLS`) to a
    real `v3_app.system_search` column, and **both** range bounds are
    enforced: `min` -> `<col>_min` (`s.<col> >= n`), `max` -> `<col>_max`
    (`s.<col> <= n`). A `max` of 0 is a real constraint ("none of this body
    type") and is emitted; a `min` of 0 (or below) is a no-op over a
    non-negative count and is skipped.

    A body-filter key that does not resolve to a projected column is rejected
    with HTTP 422 rather than silently dropped — silently ignoring a filter
    the caller explicitly set would return results the caller reads as "no
    matches for that filter" when the filter was never applied.
    """
    raw_body_filters = ctx.body_filters or {}
    for key in raw_body_filters:
        if key not in _KNOWN_BODY_FILTER_KEYS:
            raise HTTPException(
                422,
                f"Unsupported body filter '{key}' — no v3_app.system_search "
                "projection exists for it.",
            )

    body_filters = normalise_body_filters(raw_body_filters)
    hard_filters: dict[str, Any] = {}

    for key, rng in body_filters.items():
        col = BODY_FILTER_COLS.get(key)
        if col is None:
            # A camelCase alias whose snake_case canonical
            # `normalise_body_filters` already added (and which carries the
            # value); skip the alias key itself.
            continue
        if not isinstance(rng, dict):
            continue
        mn = rng.get('min')
        mx = rng.get('max')
        # Two request keys can alias the same projection column (e.g. `elw` and
        # `elw_count`). Merge to the MOST RESTRICTIVE bound (max of mins, min of
        # maxes) rather than letting whichever alias is visited last silently
        # overwrite the other and return systems that violate a requested bound.
        if mn is not None and int(mn) > 0:
            key_min = f'{col}_min'
            hard_filters[key_min] = max(int(mn), int(hard_filters.get(key_min, int(mn))))
        if mx is not None:
            key_max = f'{col}_max'
            hard_filters[key_max] = min(int(mx), int(hard_filters.get(key_max, int(mx))))

    # require_terra means "at least one terraformable body": a floor of 1,
    # never weaker than an explicit terraformable_count minimum.
    if ctx.require_terra:
        existing_terra_min = int(hard_filters.get('terraformable_count_min', 0))
        hard_filters['terraformable_count_min'] = max(existing_terra_min, 1)

    if ctx.min_development_score and ctx.min_development_score > 0:
        hard_filters['min_development_score'] = ctx.min_development_score

    if ctx.galaxy_region_id:
        hard_filters['galaxy_region'] = int(ctx.galaxy_region_id)

    if not ctx.galaxy_wide and ctx.has_reference_coords:
        hard_filters['max_distance_ly'] = ctx.max_dist
        # Lower distance bound (annulus search): only meaningful with a
        # reference point and a positive minimum. Enforced on both the page
        # and the count via the shared builder.
        if ctx.min_dist and ctx.min_dist > 0:
            hard_filters['min_distance_ly'] = ctx.min_dist

    if ctx.require_bio:
        hard_filters['has_biologicals'] = True

    if ctx.require_geo:
        hard_filters['has_geologicals'] = True

    if ctx.star_types:
        hard_filters['main_star_class_in'] = list(ctx.star_types)

    return hard_filters


def _validate_v3_window(ctx: LocalSearchContext) -> None:
    """Fail closed on an unbounded or nonsensical V3 result window.

    The public Pydantic model enforces the same contract. Keeping the check at
    the execution boundary protects direct/internal callers too, and ensures a
    future route cannot accidentally issue an oversized OFFSET/LIMIT query.
    """
    if ctx.from_idx < LOCAL_SEARCH_FROM_MIN:
        raise HTTPException(
            422,
            f'from must be greater than or equal to {LOCAL_SEARCH_FROM_MIN}',
        )
    if ctx.size < LOCAL_SEARCH_SIZE_MIN:
        raise HTTPException(
            422,
            f'size must be greater than or equal to {LOCAL_SEARCH_SIZE_MIN}',
        )
    if ctx.from_idx + ctx.size > LOCAL_SEARCH_RESULT_CAP:
        raise HTTPException(
            422,
            f'from + size must be less than or equal to {LOCAL_SEARCH_RESULT_CAP}',
        )


def _explicit_galaxy_wide_distance_filters(body: dict) -> set[str]:
    """Return explicitly requested distance bounds for an unanchored query.

    `_v3_hard_filters` deliberately records radius bounds only for anchored
    requests. In galaxy-wide mode an explicitly supplied distance bound is
    nevertheless a hard-filter request and must be rejected rather than
    silently ignored.
    """
    distance = ((body.get('filters') or {}).get('distance') or {})
    if not isinstance(distance, dict):
        return set()
    names: set[str] = set()
    if distance.get('min') is not None:
        names.add('min_distance_ly')
    if distance.get('max') is not None:
        names.add('max_distance_ly')
    return names


def _validate_v3_query_bound(
    body: dict,
    ctx: LocalSearchContext,
    hard_filters: dict[str, Any],
    sort_mode: str,
    *,
    picked: bool = False,
) -> None:
    """Enforce the two bounded Any-mode access paths from the F2d design."""
    if ctx.galaxy_wide:
        # Galaxy-wide, filters thin the window of the best 10,000 systems (or
        # of the picked type's best 10,000). Only what needs a system to
        # measure from, and the overall-potential floor for a picked type,
        # cannot be applied there.
        requested = _explicit_galaxy_wide_distance_filters(body)
        if picked and 'min_development_score' in hard_filters:
            requested = requested | {'min_development_score'}
        if requested:
            names = ', '.join(sorted(requested))
            raise HTTPException(
                422,
                f'{names}: {LOCAL_SEARCH_GALAXY_WIDE_FILTER_REJECTION_REASON}',
            )
        return

    # max_distance_ly is the anchored KNN path's intrinsic radius, not a hard
    # filter for access-path classification. Every other predicate is.
    classification_filters = set(hard_filters) - {'max_distance_ly'}
    # A picked type's score floor narrows the nearest-first walk just as a
    # hard filter does, so it takes the same radius cap.
    unfiltered_nearest_first = (
        sort_mode == SORT_DISTANCE and not classification_filters and not picked
    )
    if (
        not unfiltered_nearest_first
        and ctx.max_dist_req > LOCAL_SEARCH_FILTERED_ANCHORED_RADIUS_CAP_LY
    ):
        raise HTTPException(
            422,
            LOCAL_SEARCH_ANCHORED_RADIUS_REJECTION_REASON,
        )


def _v3_tier(score: Any) -> str | None:
    if score is None:
        return None
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(s):
        return None
    if s >= TIER_THRESHOLDS['S']:
        return 'S'
    if s >= TIER_THRESHOLDS['A']:
        return 'A'
    if s >= TIER_THRESHOLDS['B']:
        return 'B'
    if s >= TIER_THRESHOLDS['C']:
        return 'C'
    return 'D'


def _v3_distance(row: asyncpg.Record, reference_coords: tuple[float, float, float] | None) -> float | None:
    if reference_coords is None:
        return None
    rx, ry, rz = reference_coords
    dx = row['x_ly'] - rx
    dy = row['y_ly'] - ry
    dz = row['z_ly'] - rz
    return round(math.sqrt(dx * dx + dy * dy + dz * dz), 2)


def _build_v3_system_record(
    row: asyncpg.Record,
    reference_coords: tuple[float, float, float] | None,
    *,
    picked_window: bool = False,
) -> dict:
    # `archetype_score` is the overall potential. Every Any shape selects it
    # as primary_score; a picked type's galaxy window selects the type's
    # score there instead, and the overall potential as the summary column.
    primary_score = row.get('summary_best_colony_potential', row.get('primary_score'))
    return {
        'id64':              row['system_id64'],
        'id':                str(row['system_id64']),
        'name':              row['name'],
        'coords':            {'x': row['x_ly'], 'y': row['y_ly'], 'z': row['z_ly']},
        'distance':          _v3_distance(row, reference_coords),
        'main_star':         row.get('main_star_class'),
        'main_star_type':    row.get('main_star_class'),
        'archetype_score':   float(primary_score) if primary_score is not None else None,
        'archetype_tier':    _v3_tier(primary_score),
        # The system's actual summary facts (finding #20): its strongest
        # archetype, runner-up, overall development potential, and archetype
        # classification confidence -- read from the joined summary rather than
        # omitted or inferred. These map onto the DECLARED SearchResult fields
        # (overall_development_potential / archetype_confidence) the same way
        # the legacy builder does; emitting an undocumented key instead left
        # both declared fields null for every V3 result.
        'primary_archetype':   row.get('summary_primary_archetype'),
        'secondary_archetype': row.get('summary_secondary_archetype'),
        'overall_development_potential': (
            float(row['summary_best_colony_potential'])
            if row.get('summary_best_colony_potential') is not None else None
        ),
        'archetype_confidence': (
            float(row['summary_archetype_confidence'])
            if row.get('summary_archetype_confidence') is not None else None
        ),
        'uncertainty_factor': row.get('uncertainty_factor'),
        'confidence':        row.get('confidence'),
        'completeness':      row.get('completeness'),
        'body_count':        row.get('body_count'),
        'station_count':     row.get('station_count'),
        'landable_count':    row.get('landable_count'),
        'elw_count':         row.get('elw_count'),
        'ww_count':          row.get('ww_count'),
        'ammonia_count':     row.get('ammonia_count'),
        'gas_giant_count':   row.get('gas_giant_count'),
        'terraformable_count': row.get('terraformable_count'),
        'bio_signal_total':  row.get('bio_signal_total'),
        'geo_signal_total':  row.get('geo_signal_total'),
        'neutron_count':     row.get('neutron_count'),
        'black_hole_count':  row.get('black_hole_count'),
        'white_dwarf_count': row.get('white_dwarf_count'),
        'has_rings':         row.get('has_rings'),
        'has_biologicals':   row.get('has_biologicals'),
        'has_geologicals':   row.get('has_geologicals'),
        'has_terraformable': row.get('has_terraformable'),
        'galaxy_region_id':  row.get('galaxy_region_id'),
        'galaxy_region':     row.get('region_name'),
        'tags':              [],
        'source':            'v3_app',
        **_selected_fit(row, picked_window=picked_window),
    }


def _selected_fit(row: asyncpg.Record, *, picked_window: bool = False) -> dict:
    """The picked type's score and stored tier, when the search named one."""
    score = row.get('primary_score') if picked_window else row.get('selected_archetype_score')
    if score is None:
        return {}
    return {
        'selected_archetype_score': int(score),
        'selected_archetype_tier': row.get('selected_archetype_tier'),
    }


def reach_from_body(body: dict) -> int | None:
    """The request's reach (250 or 500 ly from inhabited space), or None when
    it names none. The routes always send one; a direct caller that sends
    none gets no reach rule."""
    value = body.get('reach_ly')
    if value is None:
        return None
    try:
        return checked_reach(value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def picked_from_body(body: dict) -> tuple[str | None, int | None]:
    """The colony type asked for, and its score floor.

    ``(None, None)`` when the request names no type (or "any"). Near a system
    a type is searched nearest first, within the filtered radius cap;
    galaxy-wide it is ranked by its fit (``sort_by="development"``), in the
    type's window of the best 10,000, which body filters can thin.
    ``GET /api/archetypes/rankings`` answers the same galaxy-wide ranking
    without filters.
    """
    archetype = body.get('archetype')
    min_score = body.get('min_score')
    if archetype in (None, '', 'any'):
        if min_score is not None:
            raise HTTPException(422, 'min_score needs an archetype.')
        return None, None
    if archetype not in ARCHETYPE_KEYS:
        raise HTTPException(
            422, f"Unknown archetype {archetype!r}. Valid values: {sorted(ARCHETYPE_KEYS)}",
        )
    if min_score is None:
        min_score = ARCHETYPE_SCORE_FLOOR_DEFAULT
    if isinstance(min_score, bool) or min_score not in ACCEPTED_ARCHETYPE_SCORE_FLOORS:
        raise HTTPException(
            422, f'min_score must be one of {list(ACCEPTED_ARCHETYPE_SCORE_FLOORS)}',
        )
    sort_by = (body.get('sort_by') or 'development').strip().lower()
    near = not body.get('galaxy_wide') and bool(body.get('reference_coords'))
    if near and _SORT_BY_TO_MODE.get(sort_by) != SORT_DISTANCE:
        raise HTTPException(
            422, 'archetype: a colony type near a system is sorted by distance (sort_by="distance").',
        )
    if not near and _SORT_BY_TO_MODE.get(sort_by) == SORT_DISTANCE:
        raise HTTPException(
            422, 'archetype: a colony type galaxy-wide is ranked by its fit (sort_by="development").',
        )
    return str(archetype), int(min_score)


def prepare_local_search_v3(
    body: dict,
) -> tuple[LocalSearchContext, str, dict[str, Any], tuple[float, float, float] | None]:
    """Validate a V3 Finder search request without touching the database.

    Every request-shape rejection (window, population, economy, sort_by,
    body-filter keys and the bounded Any-mode access paths) happens here, so
    the search route can answer 422 for a disallowed request *before* it
    resolves the published generation. Otherwise an unpublished Finder
    product would mask the 422 with a 503, unlike the rankings route.
    """
    # `_parse_local_search_context` is shared with legacy routes whose default
    # page is 50,000. The V3 public request model defaults to 50, so preserve
    # that bounded contract for direct/internal callers that omit `size` too.
    ctx = _parse_local_search_context({'size': V3_DEFAULT_PAGE_SIZE, **body})
    _validate_v3_window(ctx)

    # `v3_app.system_search` has no population column at all (see the module
    # comment above): a caller who explicitly asked for ANY population
    # constraint must get a clear 422, not results that were silently never
    # filtered on population. Check the raw filter for every declared shape --
    # the legacy `{value, comparison}` form AND the `{min, max}` range form --
    # since only the former reaches `ctx.population_value` (a range would
    # otherwise be dropped in silence).
    _pop_filter = (body.get('filters') or {}).get('population') or {}
    if isinstance(_pop_filter, dict) and any(
        _pop_filter.get(k) is not None for k in ('value', 'min', 'max')
    ):
        if ctx.galaxy_wide:
            raise HTTPException(
                422,
                f'population: {LOCAL_SEARCH_GALAXY_WIDE_FILTER_REJECTION_REASON}',
            )
        raise HTTPException(
            422,
            'population filter is not supported by V3 Finder search '
            '(v3_app.system_search has no population column).',
        )

    economy_filter = ctx.economy_filter
    if economy_filter and economy_filter not in ('any', 'Any', 'Unknown', ''):
        # No per-economy potential projection exists yet, so a concrete
        # economy cannot actually be ranked by that economy's potential.
        # Reject it rather than silently ranking by overall colony potential
        # while advertising the requested economy (which returned results
        # that looked economy-tuned but were not).
        raise HTTPException(
            422,
            'economy-specific ranking is not yet supported by V3 Finder '
            'search (no per-economy potential projection exists); '
            'use economy="any".',
        )

    # Map the request's sort_by onto a builder ordering mode, rejecting
    # anything unsupported rather than silently keeping score-first order for
    # a caller who asked for something else (the UI sends 'development' or
    # 'distance'; the model normalises 'rating' -> 'development').
    sort_by = (ctx.sort_by or 'development').strip().lower()
    if sort_by not in _SORT_BY_TO_MODE:
        raise HTTPException(
            422,
            f"unsupported sort_by {sort_by!r}; supported: "
            f"{', '.join(sorted(_SORT_BY_TO_MODE))}.",
        )
    sort_mode = _SORT_BY_TO_MODE[sort_by]

    hard_filters = _v3_hard_filters(ctx)
    picked, _ = picked_from_body(body)
    _validate_v3_query_bound(body, ctx, hard_filters, sort_mode, picked=picked is not None)
    reach_from_body(body)
    # Galaxy-wide mode is deliberately unanchored. A caller may still send
    # display/reference coordinates, but letting those reach the builder would
    # bypass the bounded no-pick summary window without changing semantics.
    reference_coords = (
        (ctx.rx, ctx.ry, ctx.rz)
        if not ctx.galaxy_wide and ctx.has_reference_coords
        else None
    )
    return ctx, sort_mode, hard_filters, reference_coords


async def local_db_search_v3(
    body: dict,
    pool: asyncpg.Pool,
    known_count: dict[str, Any] | None = None,
    count_budget_ms: int | None = None,
) -> dict:
    """V3-native ranked search for `POST /api/local/search`.

    Generation-pinned, reads `v3_app.system_search` +
    `v3_app.system_archetype_summary` through
    `ranking.ranking_sql.build_ranked_query` — no legacy V2 relation is ever
    touched here. The one other relation is `v3_state.system_state`, for
    Find's claim rule (`edfinder_api.claim_rule`): inhabited systems and
    systems being claimed are left out, and each result says whether it is
    free or a claim that ended with an unknown outcome.

    The count is the costly half of a search near a system for one colony
    type: it walks outwards to the 1,001st match. It does not depend on the
    page asked for, so it is asked once for a search. A page that came back
    short has reached the end of the matches: it shows the count itself, and
    that is used before anything kept. Otherwise ``known_count``, the count
    kept from an earlier page (`count_to_keep`), is used if it was made on
    the generation this page is read from and the page does not disprove it.

    A count that has to be asked is asked last and may run out of time
    (`count_within`; ``count_budget_ms`` is how long it is given). The page
    is answered all the same, with ``total_is_counted`` false and ``total``
    the number of matches the page itself shows there to be: its offset
    plus its rows.
    """
    t0 = time.time()
    ctx, sort_mode, hard_filters, reference_coords = prepare_local_search_v3(body)
    reach_ly = reach_from_body(body)
    picked_archetype, min_score = picked_from_body(body)
    picked_economy = None

    sql, params = build_ranked_query(
        PROFILE_SPEC,
        picked_archetype=picked_archetype,
        min_score=min_score,
        picked_economy=picked_economy,
        hard_filters=hard_filters,
        reference_coords=reference_coords,
        limit=ctx.size,
        offset=ctx.from_idx,
        sort=sort_mode,
        # Find shows only systems that can still be claimed (claim_rule),
        # within the asked reach of inhabited space (reach_rule).
        hide_claimed=True,
        reach_ly=reach_ly,
    )
    count_sql, count_params = build_count_query(
        PROFILE_SPEC,
        picked_archetype=picked_archetype,
        min_score=min_score,
        picked_economy=picked_economy,
        hard_filters=hard_filters,
        reference_coords=reference_coords,
        # Every count is sentinel-bounded. The builder emits LIMIT cap + 1 so
        # exactly FIND_COUNT_LIMIT matches stays distinguishable from more.
        cap=LOCAL_SEARCH_RESULT_CAP,
        sort=sort_mode,
        hide_claimed=True,
        reach_ly=reach_ly,
    )

    try:
        async with pool.acquire() as conn:
            # REPEATABLE READ so the generation pin, the page SELECT and the
            # COUNT all read one snapshot: a governed publish between the
            # statements can no longer hand back a G1 page with a G2 total,
            # or a total pinned to a different generation than the results.
            async with conn.transaction(isolation='repeatable_read', readonly=True):
                generation_row = await _current_derived_generation(conn)
                rows = await conn.fetch(sql, *params)
                qualifying = total_from_short_page(
                    len(rows), ctx.size, ctx.from_idx)
                if qualifying is None:
                    qualifying = known_qualifying(
                        known_count, generation_row,
                        shown_to=ctx.from_idx + len(rows))
                notes = await claim_notes(conn, [row['system_id64'] for row in rows])
                # Last, and in a savepoint: a count that runs out of time
                # must not take the page with it.
                if qualifying is None:
                    qualifying = await count_within(
                        conn, count_sql, count_params, count_budget_ms)
    except HTTPException:
        raise
    except (asyncpg.exceptions.UndefinedTableError,
            asyncpg.exceptions.InvalidSchemaNameError) as exc:
        # Record the underlying cause server-side (the outer route rethrows a
        # generic HTTPException, which FastAPI would not log) so a missing/
        # renamed V3 relation is diagnosable rather than an opaque 503.
        log.error('local_db_search_v3 missing relation: %r (sqlstate=%s)',
                  exc, getattr(exc, 'sqlstate', None))
        raise HTTPException(503, 'Finder search is unavailable') from exc

    picked_window = picked_archetype is not None and reference_coords is None
    results = [
        _build_v3_system_record(row, reference_coords, picked_window=picked_window)
        for row in rows
    ]
    if picked_archetype is not None:
        for result in results:
            result['selected_archetype'] = picked_archetype
    apply_claim_notes(results, notes)
    elapsed = round((time.time() - t0) * 1000)
    counted = qualifying is not None
    if not counted:
        # Not a count: how many matches this page shows there to be.
        qualifying = ctx.from_idx + len(results)
        log.warning(
            'local_db_search_v3: the count ran out of time (budget=%s ms); '
            'answering the page (from=%d, rows=%d) uncounted after %d ms',
            count_budget_ms, ctx.from_idx, len(results), elapsed)
    qualifying = int(qualifying)
    total, total_is_capped = _bounded_v3_total(qualifying)
    log.debug('local_db_search_v3: %d qualifying (reported=%d), returning %d (from=%d) in %dms',
              qualifying, total, len(results), ctx.from_idx, elapsed)

    resp: dict[str, Any] = {
        'results':  results,
        'count':    len(results),
        'total':    total,
        'total_is_capped': total_is_capped,
        'total_is_counted': counted,
        'source':   f'v3:{RANKING_VERSION}',
        'query_ms': elapsed,
        'display_economy': 'overall',  # only "any"/no-pick reaches here (concrete economy → 422 above)
        'ranking': {
            'ranking_version': RANKING_VERSION,
            'ranking_sha256': ranking_sha256(),
            'derived_generation_id': str(generation_row['derived_generation_id']),
            'publication_sequence': generation_row['publication_sequence'],
            'archetype_version': generation_row['archetype_version'],
            'archetype_manifest_sha256': generation_row['archetype_manifest_sha256'],
            'archetype_model_sha256': generation_row['archetype_model_sha256'],
        },
    }
    if ctx.radius_capped:
        resp['warning'] = (
            f"Search radius capped at {int(MAX_SEARCH_RADIUS):,} LY "
            f"(requested {int(ctx.max_dist_req):,} LY)."
        )
    return resp


