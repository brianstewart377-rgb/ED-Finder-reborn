"""
ED Finder — Search Module (PostgreSQL / asyncpg)
=================================================
Version: 5.0 (V3 Finder routes)
Target:  PostgreSQL 18 + asyncpg

Changes in v5.0 (F3 slices 1, 2 and 2b):
  • `/api/local/search` (`local_db_search_v3`), `/api/search/galaxy`
    (`local_db_galaxy_search_v3`) and `/api/search/cluster`
    (`local_db_cluster_search_v3`) read only the published V3 projections
    behind the migration-015 Finder product gate; the legacy galaxy/cluster
    readers over `cluster_summary` are deleted. `local_db_search` is kept
    as an unreachable legacy reference (see the F3 section comment).

Changes in v4.0 (historical):
  • Fixed schema/API disconnect: removed references to non-existent columns
    (main_star_class is now a generated column, needs_permit is now real).
  • Added galaxy_region filter: players can filter by named ED codex region
    (Inner Orion Spur, Formorian Frontier, etc.).
  • Searched economy score: when filtering by economy, the score shown in
    results is the economy-specific score, not the overall score.
  • Updated cluster search to use the new cluster_summary schema (v4.0).
  • Removed the stale server-side rate_system() fallback — all systems now
    have pre-computed ratings from build_ratings.py v3.0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import math
import os
import time
from typing import Any, Dict, Optional

import asyncpg
from fastapi import HTTPException

from edfinder_api.helpers import (
    SOL_ID64,
    safe_coords_from_row,
    status_failure_reason_code_for_exception,
)
from edfinder_api.ranking.profile import (
    LOCAL_SEARCH_ANCHORED_RADIUS_REJECTION_REASON,
    LOCAL_SEARCH_FILTERED_ANCHORED_RADIUS_CAP_LY,
    LOCAL_SEARCH_FROM_MIN,
    LOCAL_SEARCH_GALAXY_WIDE_FILTER_REJECTION_REASON,
    LOCAL_SEARCH_MAX_RADIUS_DEFAULT_LY,
    LOCAL_SEARCH_REQUEST_RADIUS_DEFAULT_LY,
    LOCAL_SEARCH_RESULT_CAP,
    LOCAL_SEARCH_SIZE_MIN,
    PROFILE_SPEC,
    RANKING_PAGE_LIMIT_MAX,
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
    economy_enum_value,
    BODY_FILTER_COLS,
    BODY_FILTER_ALIASES,
    normalise_body_filters,
    canonical_economy_key,
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
GALAXY_WIDE_COUNT_CAP = 10_000
V3_DEFAULT_PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _economy_str(val: Any) -> str:
    if val is None:
        return "Unknown"
    s = str(val)
    if "<" in s:
        s = s.split("'")[-2] if "'" in s else s.split(".")[-1].strip(">")
    return s


def _safe_coords(row: Any) -> dict:
    return safe_coords_from_row(row)


def _safe_distance(val: Any) -> float | None:
    """Return a valid positive distance or None.

    Older galaxy-wide searches projected ``0.0`` when there was no reference.
    Converting that to ``float(0.0)`` on the wire made the frontend show
    ``0.00 LY`` for every system. Treat ``None``, non-finite, and zero as
    unknown so the UI can render ``—`` instead.
    """
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if not __import__('math').isfinite(f) or f <= 0:
        return None
    return round(f, 2)


def _archetype_tier(score: Any) -> str | None:
    if score is None:
        return None
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(s):
        return None
    if s >= 88:
        return "S"
    if s >= 76:
        return "A"
    if s >= 60:
        return "B"
    if s >= 45:
        return "C"
    return "D"


def _build_system_record(row: asyncpg.Record, bodies: list | None = None) -> dict:
    bodies = bodies or []

    return {
        "id64":              row["id64"],
        "id":                str(row["id64"]),
        "name":              row["name"],
        "coords":            _safe_coords(row),
        "distance":          _safe_distance(row.get("dist")),
        # main_star_class is a generated column (= main_star_type) — always present
        "main_star":         row.get("main_star_class") or row.get("main_star_type"),
        "archetype_score":   row.get("archetype_score"),
        "archetype_tier":    _archetype_tier(row.get("archetype_score")),
        "primary_archetype": row.get("primary_archetype"),
        "secondary_archetype": row.get("secondary_archetype"),
        "archetype_confidence": row.get("archetype_confidence"),
        "overall_development_potential": row.get("overall_development_potential"),
        "buildability_score": row.get("buildability_score"),
        "build_complexity": row.get("build_complexity"),
        "purity_score":     row.get("purity_score"),
        "contamination_risk": row.get("contamination_risk"),
        "est_total_slots":  row.get("est_total_slots"),
        "elw_count":        row.get("elw_count"),
        "ww_count":         row.get("ww_count"),
        "ammonia_count":    row.get("ammonia_count"),
        "gas_giant_count":  row.get("gas_giant_count"),
        "neutron_count":    row.get("neutron_count"),
        "black_hole_count": row.get("black_hole_count"),
        "white_dwarf_count": row.get("white_dwarf_count"),
        "landable_count":   row.get("landable_count"),
        "terraformable_count": row.get("terraformable_count"),
        "bio_signal_total": row.get("bio_signal_total"),
        "geo_signal_total": row.get("geo_signal_total"),
        "tags":             list(row.get("display_tags") or []),
        "needs_permit":      bool(row.get("needs_permit", False)),
        "population":        row.get("population"),
        "is_colonised":      int(bool(row.get("is_colonised", False))),
        "is_being_colonised": int(bool(row.get("is_being_colonised", False))),
        "controlling_minor_faction": row.get("controlling_faction"),
        "government":        _economy_str(row.get("government")),
        "allegiance":        _economy_str(row.get("allegiance")),
        "security":          _economy_str(row.get("security")),
        "primaryEconomy":    _economy_str(row.get("primary_economy")),
        "secondaryEconomy":  _economy_str(row.get("secondary_economy")),
        # Galaxy region (named ED codex region)
        "galaxy_region_id":  row.get("galaxy_region_id"),
        "galaxy_region":     row.get("galaxy_region_name"),
        "updated_at":        row["updated_at"].isoformat() if row.get("updated_at") else None,
        "bodies":            bodies,
        "source":            "local_db",
    }


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


def _apply_local_search_filters(ctx: LocalSearchContext, builder: SearchSqlBuilder) -> None:
    add = builder.add_where

    if not ctx.galaxy_wide:
        builder.wheres.append(
            f"(s.x IS NOT NULL AND s.y IS NOT NULL AND s.z IS NOT NULL "
            f"AND NOT (s.x = 0 AND s.y = 0 AND s.z = 0 AND s.id64 != {SOL_ID64}))"
        )
        builder.wheres.append(f"s.x BETWEEN {add(ctx.rx - ctx.max_dist)} AND {add(ctx.rx + ctx.max_dist)}")
        builder.wheres.append(f"s.y BETWEEN {add(ctx.ry - ctx.max_dist)} AND {add(ctx.ry + ctx.max_dist)}")
        builder.wheres.append(f"s.z BETWEEN {add(ctx.rz - ctx.max_dist)} AND {add(ctx.rz + ctx.max_dist)}")
        builder.wheres.append(
            f"((s.x-{add(ctx.rx)})*(s.x-{add(ctx.rx)}) + "
            f"(s.y-{add(ctx.ry)})*(s.y-{add(ctx.ry)}) + "
            f"(s.z-{add(ctx.rz)})*(s.z-{add(ctx.rz)})) "
            f"BETWEEN {add(ctx.min_dist * ctx.min_dist)} AND {add(ctx.max_dist * ctx.max_dist)}"
        )

    if ctx.require_empty:
        builder.wheres.append("s.population = 0")
        builder.wheres.append("s.is_colonised = FALSE")
    elif ctx.population_value is not None and ctx.population_comparison is not None:
        population_ops = {
            "equal": "=",
            "=": "=",
            "==": "=",
            "not_equal": "!=",
            "!=": "!=",
            "<>": "!=",
            "gt": ">",
            ">": ">",
            "gte": ">=",
            ">=": ">=",
            "lt": "<",
            "<": "<",
            "lte": "<=",
            "<=": "<=",
        }
        operator = population_ops.get(ctx.population_comparison)
        if operator is not None:
            builder.wheres.append(f"s.population {operator} {add(ctx.population_value)}")

    if ctx.economy_filter and ctx.economy_filter not in ("any", "Any", "Unknown", ""):
        enum_val = economy_enum_value(ctx.economy_filter)
        if enum_val is not None:
            builder.wheres.append(f"s.primary_economy = {add(enum_val)}::economy_type")

    if ctx.sec_econ_filter and ctx.sec_econ_filter not in ("any", "Any", "Unknown", ""):
        sec_enum_val = economy_enum_value(ctx.sec_econ_filter)
        if sec_enum_val is not None:
            builder.wheres.append(f"s.secondary_economy = {add(sec_enum_val)}::economy_type")

    if ctx.galaxy_region_id:
        builder.wheres.append(f"s.galaxy_region_id = {add(int(ctx.galaxy_region_id))}")

    if ctx.star_types:
        placeholders = ", ".join(add(st) for st in ctx.star_types)
        builder.wheres.append(f"s.main_star_class IN ({placeholders})")

    if ctx.min_development_score > 0:
        builder.wheres.append(f"{ctx.finder_score_expr} >= {add(ctx.min_development_score)}")

    body_filters = normalise_body_filters(ctx.body_filters)
    for filter_key, col in BODY_FILTER_COLS.items():
        rng = body_filters.get(filter_key) or {}
        if not isinstance(rng, dict):
            continue
        min_val = int(rng.get("min", 0) or 0)
        max_val = rng.get("max")
        qcol = f"r.{col}"
        if min_val > 0:
            builder.wheres.append(f"({qcol} IS NOT NULL AND {qcol} >= {add(min_val)})")
        if max_val is not None:
            builder.wheres.append(f"({qcol} IS NULL OR {qcol} <= {add(max_val)})")

    if ctx.require_bio:
        builder.wheres.append("(r.bio_signal_total IS NOT NULL AND r.bio_signal_total > 0)")
    if ctx.require_geo:
        builder.wheres.append("(r.geo_signal_total IS NOT NULL AND r.geo_signal_total > 0)")
    if ctx.require_terra:
        builder.wheres.append("(r.terraformable_count IS NOT NULL AND r.terraformable_count > 0)")


def _build_distance_expr(ctx: LocalSearchContext, builder: SearchSqlBuilder) -> str:
    coord_guard = (
        f"CASE WHEN s.x = 0 AND s.y = 0 AND s.z = 0 AND s.id64 != {SOL_ID64} "
        "THEN NULL::float "
    )
    if ctx.galaxy_wide and not ctx.has_reference_coords:
        return "NULL::float"
    return (
        f"{coord_guard}ELSE SQRT((s.x-{builder.add_select(ctx.rx)})*(s.x-{builder.add_select(ctx.rx)}) + "
        f"(s.y-{builder.add_select(ctx.ry)})*(s.y-{builder.add_select(ctx.ry)}) + "
        f"(s.z-{builder.add_select(ctx.rz)})*(s.z-{builder.add_select(ctx.rz)})) END"
    )


def _build_local_search_count_sql(where_sql: str, galaxy_wide: bool) -> str:
    if galaxy_wide:
        return f"""
            SELECT COUNT(*) FROM (
                SELECT 1
                FROM systems s
                LEFT JOIN ratings r ON r.system_id64 = s.id64
                LEFT JOIN mv_archetype_rankings m ON m.id64 = s.id64
                {where_sql}
                LIMIT {GALAXY_WIDE_COUNT_CAP}
            ) t
        """
    return f"""
        SELECT COUNT(*)
        FROM systems s
        LEFT JOIN ratings r ON r.system_id64 = s.id64
        LEFT JOIN mv_archetype_rankings m ON m.id64 = s.id64
        {where_sql}
    """


def _build_local_search_sql(
    ctx: LocalSearchContext,
    builder: SearchSqlBuilder,
    where_sql: str,
    dist_expr: str,
) -> str:
    order_sql = (
        f"ORDER BY {ctx.finder_score_expr} DESC NULLS LAST, dist ASC"
        if ctx.sort_by == "development"
        else "ORDER BY dist ASC"
    )
    return f"""
        SELECT
            s.id64, s.name, s.x, s.y, s.z,
            s.main_star_class,
            s.main_star_type,
            s.needs_permit,
            s.population,
            s.is_colonised,
            s.is_being_colonised,
            s.controlling_faction,
            s.government::text    AS government,
            s.allegiance::text    AS allegiance,
            s.security::text      AS security,
            s.primary_economy::text   AS primary_economy,
            s.secondary_economy::text AS secondary_economy,
            s.galaxy_region_id,
            gr.name               AS galaxy_region_name,
            s.updated_at,
            r.score,
            {ctx.display_score_col}   AS display_score,
            {ctx.finder_score_expr}   AS archetype_score,
            r.score_extraction,
            r.economy_suggestion,
            r.slots               AS r_slots,
            r.body_quality        AS r_body_quality,
            r.compactness         AS r_compactness,
            r.signal_quality      AS r_signal_quality,
            r.orbital_safety      AS r_orbital_safety,
            r.star_bonus          AS r_star_bonus,
            r.elw_count, r.ww_count, r.ammonia_count,
            r.gas_giant_count, r.neutron_count, r.black_hole_count,
            r.white_dwarf_count, r.landable_count, r.terraformable_count,
            r.bio_signal_total, r.geo_signal_total,
            r.terraforming_potential, r.body_diversity,
            r.confidence, r.rationale,
            r.rating_version,
            m.primary_archetype,
            m.secondary_archetype,
            m.archetype_confidence,
            m.overall_development_potential,
            m.buildability_score,
            m.build_complexity,
            m.purity_score,
            m.contamination_risk,
            m.est_total_slots,
            m.display_tags,
            {dist_expr} AS dist
        FROM systems s
        LEFT JOIN ratings r ON r.system_id64 = s.id64
        LEFT JOIN mv_archetype_rankings m ON m.id64 = s.id64
        LEFT JOIN galaxy_regions gr ON gr.id = s.galaxy_region_id
        {where_sql}
        {order_sql}
        LIMIT {builder.add_select(ctx.size)} OFFSET {builder.add_select(ctx.from_idx)}
    """


def _build_local_search_response(
    rows: list[asyncpg.Record],
    total: int,
    ctx: LocalSearchContext,
    elapsed: int,
) -> Dict[str, Any]:
    results = []
    for row in rows:
        rec = _build_system_record(row)
        rec["distance"] = _safe_distance(row["dist"])
        results.append(rec)

    resp: Dict[str, Any] = {
        "results":  results,
        "count":    len(results),
        "total":    total,
        "source":   "local_db",
        "query_ms": elapsed,
        "display_economy": ctx.economy_filter or "overall",
    }
    if ctx.galaxy_wide and total is not None and total >= GALAXY_WIDE_COUNT_CAP:
        resp["total_is_capped"] = True
    if ctx.radius_capped:
        resp["warning"] = (
            f"Search radius capped at {int(MAX_SEARCH_RADIUS):,} LY "
            f"(requested {int(ctx.max_dist_req):,} LY)."
        )
    return resp


# ---------------------------------------------------------------------------
# Core search
# ---------------------------------------------------------------------------
async def local_db_search(body: dict, pool: asyncpg.Pool) -> dict:
    """
    Execute a filtered system search against the PostgreSQL DB.

    Key features:
    - Body filters pushed into SQL via pre-computed ratings columns.
    - Galaxy region filter (named ED codex regions 1-42).
    - When filtering by economy, display_score = economy-specific score.
    - When browsing without economy filter, display_score = overall score.
    - Unified local and galaxy-wide search (galaxy_wide=True skips distance).
    """
    t0 = time.time()

    ctx = _parse_local_search_context(body)
    builder = SearchSqlBuilder()
    _apply_local_search_filters(ctx, builder)
    where_sql = builder.where_sql
    where_param_count = len(builder.params)
    dist_expr = _build_distance_expr(ctx, builder)
    count_sql = _build_local_search_count_sql(where_sql, ctx.galaxy_wide)
    sql = _build_local_search_sql(ctx, builder, where_sql, dist_expr)

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *builder.params)
        assert len(builder.params) == where_param_count + len(builder.select_params), (
            "param accounting drifted: WHERE+SELECT must equal total"
        )
        total = await conn.fetchval(count_sql, *builder.params[:where_param_count])

    elapsed = round((time.time() - t0) * 1000)
    log.debug("local_db_search: %d total, returning %d (from=%d) in %dms",
              total, len(rows), ctx.from_idx, elapsed)

    return _build_local_search_response(rows, total, ctx, elapsed)


# ---------------------------------------------------------------------------
# V3 ranked search (F3 slice 1) — POST /api/local/search, and since F3
# slice 2b also the read behind POST /api/search/galaxy.
# ---------------------------------------------------------------------------
# No route reads the legacy `local_db_search` above any more. F3 slice 1
# repointed `/api/local/search` onto `local_db_search_v3`; F3 slice 2b
# (this section onward) repointed `/api/search/galaxy` onto
# `local_db_galaxy_search_v3` (a validated galaxy-wide Any-mode call into
# `local_db_search_v3`) and `/api/search/cluster` onto
# `local_db_cluster_search_v3` (the V3 `v3_spatial.cluster_run` product gate;
# see that function for the flagged data gap). The legacy
# `local_db_galaxy_search` / `local_db_cluster_search` / `_resolve_slot_matches`
# readers over `cluster_summary` / `systems` / `ratings` were deleted with that
# repoint. `local_db_search` and its SQL helpers are retained only as the
# legacy reference implementation (its record builder still serves
# `local_db_system`, and the data-trust/population contract tests pin it);
# it is reachable from no route. The "ONE search implementation" rule in
# `routers/search.py` is per route: each search route has exactly one read
# path and never a competing fallback.
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
#
# The derived products a V3 Finder ranked read depends on. A generation may be
# published before either Finder product is attached, and a READY product stays
# hidden until its separate publication audit commits. Requiring both products
# to be READY and published keeps every unavailable state distinct from a valid
# search with no matches.
_REQUIRED_FINDER_PRODUCTS: frozenset[str] = frozenset({'system_search', 'system_archetype'})


async def _current_derived_generation(connection: asyncpg.Connection) -> dict[str, Any]:
    row = await connection.fetchrow('''
        SELECT c.derived_generation_id, c.publication_sequence
        FROM v3_meta.current_derived_generation c
        JOIN v3_meta.derived_generation d USING(derived_generation_id)
        WHERE d.lifecycle_state='PUBLISHED'
    ''')
    if row is None:
        raise HTTPException(404, 'No published Ratings V4 generation')

    # The v3_app views intentionally expose only published products. Inspect
    # the product rows themselves so a 503 can name whether each dependency is
    # missing, unfinished/failed, or READY but not yet published.
    products = await connection.fetch(
        '''
        SELECT product_code,
               lifecycle_state,
               published_at,
               product_version AS archetype_version,
               encode(manifest_sha256, 'hex') AS archetype_manifest_sha256,
               manifest ->> 'archetype_model_sha256' AS archetype_model_sha256,
               manifest ->> 'model_file_sha256' AS model_file_sha256
        FROM v3_meta.derived_product
        WHERE derived_generation_id = $1
          AND product_code = ANY($2::text[])
        ''',
        row['derived_generation_id'],
        list(_REQUIRED_FINDER_PRODUCTS),
    )
    by_code = {product['product_code']: product for product in products}
    unavailable: list[str] = []
    for product_code in sorted(_REQUIRED_FINDER_PRODUCTS):
        product = by_code.get(product_code)
        if product is None:
            unavailable.append(f'{product_code}=MISSING')
        elif product['lifecycle_state'] != 'READY':
            unavailable.append(f'{product_code}={product["lifecycle_state"]}')
        elif product['published_at'] is None:
            unavailable.append(f'{product_code}=READY-unpublished')
    if unavailable:
        raise HTTPException(
            503,
            'Finder ranking products are not available for the published '
            f'generation ({", ".join(unavailable)}).',
        )

    archetype_product = next(
        product for product in products if product['product_code'] == 'system_archetype'
    )
    identity = {
        'archetype_version': archetype_product['archetype_version'],
        'archetype_manifest_sha256': archetype_product['archetype_manifest_sha256'],
        'archetype_model_sha256': archetype_product['archetype_model_sha256'],
    }
    if any(not value for value in identity.values()):
        raise HTTPException(
            503,
            'Finder archetype product identity is incomplete for the published generation.',
        )
    return {
        **dict(row),
        **identity,
        'model_file_sha256': archetype_product.get('model_file_sha256'),
    }


async def resolve_published_generation(pool: asyncpg.Pool) -> dict[str, Any]:
    """Resolve the current PUBLISHED derived generation (id + sequence).

    Public wrapper around `_current_derived_generation` for callers (the
    search router's cache-key builder) that need the pinned generation
    *before* entering the ranked read — so a warm cache entry can never be
    served while no generation is published (404), and the cache key can be
    scoped to the exact generation the results would be computed under.
    """
    async with pool.acquire() as conn:
        return await _current_derived_generation(conn)


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
) -> None:
    """Enforce the two bounded Any-mode access paths from the F2d design."""
    if ctx.galaxy_wide:
        requested = set(hard_filters) | _explicit_galaxy_wide_distance_filters(body)
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
    unfiltered_nearest_first = (
        sort_mode == SORT_DISTANCE and not classification_filters
    )
    if (
        not unfiltered_nearest_first
        and ctx.max_dist_req > LOCAL_SEARCH_FILTERED_ANCHORED_RADIUS_CAP_LY
    ):
        raise HTTPException(
            422,
            LOCAL_SEARCH_ANCHORED_RADIUS_REJECTION_REASON,
        )


def _bounded_v3_total(qualifying: int | None) -> tuple[int, bool]:
    """Turn the cap+1 sentinel count into the public total contract."""
    count = int(qualifying) if qualifying is not None else 0
    return min(count, LOCAL_SEARCH_RESULT_CAP), count > LOCAL_SEARCH_RESULT_CAP


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


def _build_v3_system_record(row: asyncpg.Record, reference_coords: tuple[float, float, float] | None) -> dict:
    primary_score = row.get('primary_score')
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
    }


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
    _validate_v3_query_bound(body, ctx, hard_filters, sort_mode)
    # Galaxy-wide mode is deliberately unanchored. A caller may still send
    # display/reference coordinates, but letting those reach the builder would
    # bypass the bounded no-pick summary window without changing semantics.
    reference_coords = (
        (ctx.rx, ctx.ry, ctx.rz)
        if not ctx.galaxy_wide and ctx.has_reference_coords
        else None
    )
    return ctx, sort_mode, hard_filters, reference_coords


async def local_db_search_v3(body: dict, pool: asyncpg.Pool) -> dict:
    """V3-native ranked search for `POST /api/local/search`.

    Generation-pinned, reads only `v3_app.system_search` +
    `v3_app.system_archetype_summary` through
    `ranking.ranking_sql.build_ranked_query` — no legacy V2 relation and no
    other schema's relation is ever touched here.
    """
    t0 = time.time()
    ctx, sort_mode, hard_filters, reference_coords = prepare_local_search_v3(body)
    picked_economy = None

    sql, params = build_ranked_query(
        PROFILE_SPEC,
        picked_archetype=None,
        picked_economy=picked_economy,
        hard_filters=hard_filters,
        reference_coords=reference_coords,
        limit=ctx.size,
        offset=ctx.from_idx,
        sort=sort_mode,
    )
    count_sql, count_params = build_count_query(
        PROFILE_SPEC,
        picked_archetype=None,
        picked_economy=picked_economy,
        hard_filters=hard_filters,
        reference_coords=reference_coords,
        # Every count is sentinel-bounded. The builder emits LIMIT cap + 1 so
        # exactly 10,000 matches remains distinguishable from truncation.
        cap=LOCAL_SEARCH_RESULT_CAP,
        sort=sort_mode,
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
                qualifying = await conn.fetchval(count_sql, *count_params)
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

    results = [_build_v3_system_record(row, reference_coords) for row in rows]
    elapsed = round((time.time() - t0) * 1000)
    qualifying = int(qualifying) if qualifying is not None else 0
    total, total_is_capped = _bounded_v3_total(qualifying)
    log.debug('local_db_search_v3: %d qualifying (reported=%d), returning %d (from=%d) in %dms',
              qualifying, total, len(results), ctx.from_idx, elapsed)

    resp: dict[str, Any] = {
        'results':  results,
        'count':    len(results),
        'total':    total,
        'total_is_capped': total_is_capped,
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


# ---------------------------------------------------------------------------
# V3 galaxy-wide search (F3 slice 2b) — POST /api/search/galaxy ONLY.
# ---------------------------------------------------------------------------
# On V3 the galaxy route *is* the galaxy-wide Any-mode window of the ranked
# search: the same bounded access path (`summary_window`, top 10,000 by
# `weighted_potential`), the same ranking profile, the same generation pin
# and product gate, and the same envelope as `POST /api/local/search` with
# `galaxy_wide=true`. Every legacy request feature that has no V3 projection
# is rejected with 422 before any database access rather than silently
# dropped (slice-1 rule); see `prepare_galaxy_search_v3`.
GALAXY_SEARCH_LIMIT_DEFAULT = 100
_ECONOMY_ANY_VALUES: frozenset[str] = frozenset({'any', 'Any', 'Unknown', ''})


def _int_or_422(value: Any, field: str, *, default: int | None = None) -> int:
    """Coerce a request integer, answering 422 (not 503) for garbage."""
    if value is None:
        if default is None:
            raise HTTPException(422, f'{field} is required')
        return default
    if isinstance(value, bool):
        raise HTTPException(422, f'{field} must be an integer')
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f'{field} must be an integer') from exc


def prepare_galaxy_search_v3(body: dict) -> dict:
    """Validate a galaxy-wide search request without touching the database
    and return the equivalent `local_db_search_v3` body.

    Rejections (HTTP 422), each a legacy feature with no V3 projection:

    - a concrete ``economy``: no per-economy potential projection exists
      (the slice-1 rule for `/api/local/search`); ``economy="any"`` ranks
      by best colony potential;
    - ``min_score`` above 0: a hard filter on a galaxy-wide Any request,
      which the F2d bounded-access contract refuses
      (`LOCAL_SEARCH_GALAXY_WIDE_FILTER_REJECTION_REASON`);
    - ``include_colonised=false``: `v3_app.system_search` has no population
      column. The legacy route silently forced uncolonised-only results;
      the V3 route ranks every system in the published generation, and a
      caller that explicitly asks for the restriction is told so;
    - ``galaxy_region_id``: a galaxy-wide hard filter, rejected by
      `prepare_local_search_v3`;
    - a result window outside the ranking window (``limit`` 1..500,
      ``offset`` >= 0, ``offset + limit`` <= 10,000).
    """
    economy = body.get('economy')
    if economy is None:
        economy = 'any'
    if not isinstance(economy, str):
        raise HTTPException(422, 'economy must be a string')

    min_score = _int_or_422(body.get('min_score'), 'min_score', default=0)
    if min_score < 0:
        raise HTTPException(422, 'min_score must be greater than or equal to 0')
    if min_score > 0:
        raise HTTPException(
            422,
            f'min_score: {LOCAL_SEARCH_GALAXY_WIDE_FILTER_REJECTION_REASON}',
        )

    if body.get('include_colonised') is False:
        raise HTTPException(
            422,
            'include_colonised=false (uncolonised-only) is not supported by V3 '
            'Finder search (v3_app.system_search has no population column).',
        )

    limit = _int_or_422(body.get('limit'), 'limit', default=GALAXY_SEARCH_LIMIT_DEFAULT)
    offset = _int_or_422(body.get('offset'), 'offset', default=0)
    if limit > RANKING_PAGE_LIMIT_MAX:
        raise HTTPException(
            422, f'limit must be less than or equal to {RANKING_PAGE_LIMIT_MAX}',
        )

    local_body: dict[str, Any] = {
        'galaxy_wide': True,
        'reference_coords': None,
        'economy': economy if economy not in _ECONOMY_ANY_VALUES else 'any',
        'filters': {'economy': economy if economy not in _ECONOMY_ANY_VALUES else 'any'},
        'min_development_score': 0,
        'sort_by': 'development',
        'size': limit,
        'from': offset,
        'galaxy_region_id': body.get('galaxy_region_id'),
    }
    # The shared validator enforces the window, the concrete-economy
    # rejection and the galaxy-wide hard-filter rule (region) in one place.
    prepare_local_search_v3(local_body)
    return local_body


async def local_db_galaxy_search_v3(body: dict, pool: asyncpg.Pool) -> dict:
    """V3 galaxy-wide search for `POST /api/search/galaxy`.

    A validated galaxy-wide Any-mode call into `local_db_search_v3`: reads
    only `v3_app.system_search` + `v3_app.system_archetype_summary`,
    generation-pinned inside one REPEATABLE READ snapshot, and returns the
    same `SearchResponse` envelope (results, sentinel-bounded total, ranking
    identity) as `/api/local/search`.
    """
    local_body = prepare_galaxy_search_v3(body)
    return await local_db_search_v3(local_body, pool)


# ---------------------------------------------------------------------------
# V3 cluster search (F3 slice 2b) — POST /api/search/cluster ONLY.
# ---------------------------------------------------------------------------
# The legacy cluster search read the V2 `cluster_summary` macro-grid table
# (per-anchor counts of economy-viable systems inside a 500 LY bubble) plus
# `systems`/`ratings` for slot detail. Its V3 successor is the independent
# cluster product of migration 004 (`v3_spatial.cluster_run` ->
# `v3_spatial.cluster` -> `v3_spatial.cluster_member`; decision doc §8):
# clusters are domain-derived, versioned, generation-scoped outputs that a
# builder publishes per owner domain and algorithm version.
#
# Flagged data gap (not silently degraded): no Finder cluster builder exists
# in the tree, no `cluster_run` row has ever been published, and the
# per-economy viability summary the legacy `ClusterResult` fields need is a
# builder-defined `cluster.summary` contract that has not been designed. The
# V3 route therefore fails closed: it validates the request, pins the
# published generation behind the same Finder product gate as every other
# Finder route, reads the Finder-owned cluster-run state for that generation
# from `v3_spatial.cluster_run`, and answers 503 naming exactly what is
# missing. It never reads a legacy relation and never fabricates clusters
# from a request-time galaxy scan (decision doc §12: no request-time global
# calculation; clusters are not the search index).
FINDER_CLUSTER_OWNER_DOMAIN = 'finder'
CLUSTER_SEARCH_LIMIT_DEFAULT = 50
CLUSTER_SEARCH_LIMIT_MAX = 200
CLUSTER_SEARCH_MAX_REQUIREMENTS = 6
CLUSTER_SEARCH_MAX_SLOTS = 10
CLUSTER_SEARCH_MAX_SLOT_ECONOMIES = 2
CLUSTER_SEARCH_MIN_SCORE_DEFAULT = 40
CLUSTER_SLOT_MIN_SCORE_DEFAULT = 65
GALAXY_REGION_ID_MIN = 1
GALAXY_REGION_ID_MAX = 42


def _canonical_economy_or_422(value: Any, field: str) -> str:
    key = canonical_economy_key(value) if isinstance(value, str) else None
    if key is None:
        raise HTTPException(422, f'{field}: unknown economy {value!r}')
    return key


def _score_or_422(value: Any, field: str, *, default: int) -> int:
    score = _int_or_422(value, field, default=default)
    if score < 0 or score > 100:
        raise HTTPException(422, f'{field} must be between 0 and 100')
    return score


def prepare_cluster_search_v3(body: dict) -> dict[str, Any]:
    """Validate a cluster-search request without touching the database.

    Returns the normalised request (canonical economy keys, bounded window,
    finite reference coordinates or None, bounded region id). Every
    rejection is a 422 so an unavailable cluster product (503) can never
    mask a malformed request.
    """
    requirements = body.get('requirements') or []
    slots = body.get('slots') or []
    if bool(requirements) == bool(slots):
        raise HTTPException(422, 'provide exactly one of requirements or slots')
    if len(requirements) > CLUSTER_SEARCH_MAX_REQUIREMENTS:
        raise HTTPException(
            422, f'at most {CLUSTER_SEARCH_MAX_REQUIREMENTS} economy requirements',
        )
    if len(slots) > CLUSTER_SEARCH_MAX_SLOTS:
        raise HTTPException(422, f'at most {CLUSTER_SEARCH_MAX_SLOTS} slots')

    normalised_requirements: list[dict[str, Any]] = []
    for index, requirement in enumerate(requirements):
        if not isinstance(requirement, dict):
            raise HTTPException(422, f'requirements[{index}] must be an object')
        min_count = _int_or_422(
            requirement.get('min_count'), f'requirements[{index}].min_count', default=1,
        )
        if min_count < 1:
            raise HTTPException(
                422, f'requirements[{index}].min_count must be greater than or equal to 1',
            )
        normalised_requirements.append({
            'economy': _canonical_economy_or_422(
                requirement.get('economy'), f'requirements[{index}].economy',
            ),
            'min_count': min_count,
            'min_score': _score_or_422(
                requirement.get('min_score'), f'requirements[{index}].min_score',
                default=CLUSTER_SEARCH_MIN_SCORE_DEFAULT,
            ),
        })

    normalised_slots: list[dict[str, Any]] = []
    for index, slot in enumerate(slots):
        if not isinstance(slot, dict):
            raise HTTPException(422, f'slots[{index}] must be an object')
        economies = slot.get('economies') or []
        if not isinstance(economies, list) or not economies:
            raise HTTPException(422, f'slots[{index}].economies must name at least one economy')
        if len(economies) > CLUSTER_SEARCH_MAX_SLOT_ECONOMIES:
            raise HTTPException(
                422,
                f'slots[{index}].economies: at most {CLUSTER_SEARCH_MAX_SLOT_ECONOMIES} '
                'economies per slot (same-world constraint)',
            )
        normalised_slots.append({
            'economies': [
                _canonical_economy_or_422(economy, f'slots[{index}].economies')
                for economy in economies
            ],
            'min_score': _score_or_422(
                slot.get('min_score'), f'slots[{index}].min_score',
                default=CLUSTER_SLOT_MIN_SCORE_DEFAULT,
            ),
            'label': slot.get('label'),
        })

    limit = _int_or_422(body.get('limit'), 'limit', default=CLUSTER_SEARCH_LIMIT_DEFAULT)
    if limit < 1 or limit > CLUSTER_SEARCH_LIMIT_MAX:
        raise HTTPException(422, f'limit must be between 1 and {CLUSTER_SEARCH_LIMIT_MAX}')
    offset = _int_or_422(body.get('offset'), 'offset', default=0)
    if offset < 0:
        raise HTTPException(422, 'offset must be greater than or equal to 0')

    reference_coords: tuple[float, float, float] | None = None
    ref = body.get('reference_coords') or {}
    if not isinstance(ref, dict):
        raise HTTPException(422, 'reference_coords must be an object')
    present = [axis for axis in ('x', 'y', 'z') if ref.get(axis) is not None]
    if present and len(present) != 3:
        raise HTTPException(422, 'reference_coords must include x, y, z')
    if present:
        try:
            coords = tuple(float(ref[axis]) for axis in ('x', 'y', 'z'))
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, 'reference_coords must be numeric') from exc
        if not all(math.isfinite(value) for value in coords):
            raise HTTPException(422, 'reference_coords must be finite numbers')
        reference_coords = coords  # type: ignore[assignment]

    galaxy_region_id: int | None = None
    if body.get('galaxy_region_id') is not None:
        galaxy_region_id = _int_or_422(body.get('galaxy_region_id'), 'galaxy_region_id')
        if not GALAXY_REGION_ID_MIN <= galaxy_region_id <= GALAXY_REGION_ID_MAX:
            raise HTTPException(
                422,
                f'galaxy_region_id must be between {GALAXY_REGION_ID_MIN} '
                f'and {GALAXY_REGION_ID_MAX}',
            )

    return {
        'requirements': normalised_requirements,
        'slots': normalised_slots,
        'limit': limit,
        'offset': offset,
        'reference_coords': reference_coords,
        'galaxy_region_id': galaxy_region_id,
    }


async def _finder_cluster_runs(
    connection: asyncpg.Connection, derived_generation_id: Any,
) -> list[asyncpg.Record]:
    """Read the Finder-owned cluster runs attached to the pinned generation."""
    return await connection.fetch(
        '''
        SELECT cluster_run_id, algorithm, algorithm_version, lifecycle_state, published_at
        FROM v3_spatial.cluster_run
        WHERE derived_generation_id = $1
          AND owner_domain = $2
        ORDER BY created_at DESC, cluster_run_id
        ''',
        derived_generation_id,
        FINDER_CLUSTER_OWNER_DOMAIN,
    )


async def local_db_cluster_search_v3(body: dict, pool: asyncpg.Pool) -> dict:
    """V3 cluster search for `POST /api/search/cluster` — fail-closed.

    Validates the request (422), pins the published generation behind the
    Finder product gate (404 / 503, `_current_derived_generation`), then
    reads the Finder-owned cluster-run state for that generation from
    `v3_spatial.cluster_run` inside the same REPEATABLE READ snapshot and
    answers 503 naming what is missing. See the section comment above for
    the flagged data gap: until a Finder cluster builder publishes a run
    and defines the `cluster.summary` viability contract, no V3 read can
    honestly produce the `ClusterResult` fields, so this never returns 200
    and never falls back to a legacy relation.
    """
    prepare_cluster_search_v3(body)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction(isolation='repeatable_read', readonly=True):
                generation_row = await _current_derived_generation(conn)
                runs = await _finder_cluster_runs(conn, generation_row['derived_generation_id'])
    except HTTPException:
        raise
    except (asyncpg.exceptions.UndefinedTableError,
            asyncpg.exceptions.InvalidSchemaNameError) as exc:
        log.error('local_db_cluster_search_v3 missing relation: %r (sqlstate=%s)',
                  exc, getattr(exc, 'sqlstate', None))
        raise HTTPException(503, 'Finder cluster search is unavailable') from exc

    published = [run for run in runs if run['lifecycle_state'] == 'PUBLISHED']
    generation_id = generation_row['derived_generation_id']
    if not published:
        state = ', '.join(
            f"{run['algorithm']}@{run['algorithm_version']}={run['lifecycle_state']}"
            for run in runs
        ) or 'none'
        raise HTTPException(
            503,
            'Finder cluster product is not published for the current generation '
            f'{generation_id} (v3_spatial.cluster_run owner_domain='
            f'{FINDER_CLUSTER_OWNER_DOMAIN}: {state}); the Finder cluster builder '
            'and its cluster summary contract are a pending V3 derived product.',
        )
    newest = published[0]
    raise HTTPException(
        503,
        f"Finder cluster run {newest['algorithm']}@{newest['algorithm_version']} "
        f'({newest["cluster_run_id"]}) is published for generation {generation_id}, '
        'but no Finder cluster read contract exists for it yet; cluster search '
        'stays unavailable until that contract is designed and implemented.',
    )


# ---------------------------------------------------------------------------
# System detail
# ---------------------------------------------------------------------------
async def local_db_system(id64: int, pool: asyncpg.Pool) -> Optional[dict]:
    """Return full system data including bodies and stations."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                s.id64, s.name, s.x, s.y, s.z,
                s.main_star_class,
                s.main_star_type,
                s.needs_permit,
                s.population,
                s.is_colonised,
                s.is_being_colonised,
                s.controlling_faction,
                s.government::text    AS government,
                s.allegiance::text    AS allegiance,
                s.security::text      AS security,
                s.primary_economy::text   AS primary_economy,
                s.secondary_economy::text AS secondary_economy,
                s.galaxy_region_id,
                gr.name               AS galaxy_region_name,
                s.updated_at,
                r.score,
                r.score               AS display_score,
                r.score_extraction,
                r.economy_suggestion,
                r.slots               AS r_slots,
                r.body_quality        AS r_body_quality,
                r.compactness         AS r_compactness,
                r.signal_quality      AS r_signal_quality,
                r.orbital_safety      AS r_orbital_safety,
                r.star_bonus          AS r_star_bonus,
                r.elw_count, r.ww_count, r.ammonia_count,
                r.gas_giant_count, r.neutron_count, r.black_hole_count,
                r.white_dwarf_count, r.landable_count, r.terraformable_count,
                r.bio_signal_total, r.geo_signal_total,
                r.rocky_count, r.rocky_ice_count, r.icy_count, r.hmc_count,
                r.terraforming_potential, r.body_diversity,
                r.confidence, r.rationale, r.rating_version,
                NULL::float           AS dist
            FROM systems s
            LEFT JOIN ratings r ON r.system_id64 = s.id64
            LEFT JOIN galaxy_regions gr ON gr.id = s.galaxy_region_id
            WHERE s.id64 = $1
        """, id64)

        if not row:
            return None

        body_rows = await conn.fetch("""
            SELECT
                b.id, b.name,
                b.body_type::text AS type,
                b.subtype,
                b.distance_from_star,
                b.is_landable,
                b.is_tidal_lock,
                b.atmosphere_type AS atmosphere,
                b.volcanism,
                b.terraforming_state AS terraform_state,
                b.gravity AS surface_gravity,
                b.surface_temp,
                b.mass,
                b.radius,
                b.bio_signal_count AS bio_signals,
                b.geo_signal_count AS geo_signals,
                b.estimated_mapping_value AS mapped_value,
                b.estimated_scan_value AS scan_value,
                NULL::text AS ring_types,
                NULL::text AS signals,
                (b.bio_signal_count > 0 OR b.geo_signal_count > 0) AS has_signals,
                EXISTS (
                    SELECT 1 FROM body_rings br
                    WHERE br.system_id64 = b.system_id64
                      AND br.body_id = b.id
                      AND br.association_status = 'local_matched'
                ) AS has_rings
            FROM bodies b
            WHERE b.system_id64 = $1
            ORDER BY b.distance_from_star
        """, id64)

    body_list = [_normalize_body(b) for b in body_rows]
    record = _build_system_record(row, body_list)
    record["body_count"] = len(body_list)

    # Fetch stations
    async with pool.acquire() as conn:
        station_rows = await conn.fetch("""
            SELECT
                id, name,
                station_type::text AS type,
                distance_from_star,
                body_name,
                landing_pad_size::text AS landing_pad_size,
                primary_economy::text AS primary_economy,
                secondary_economy::text AS secondary_economy,
                has_market, has_shipyard, has_outfitting,
                has_refuel, has_repair, has_rearm,
                has_black_market, has_material_trader,
                has_technology_broker, has_interstellar_factors,
                has_universal_cartographics, has_search_rescue,
                updated_at
            FROM stations
            WHERE system_id64 = $1
            ORDER BY distance_from_star
        """, id64)

    stations = []
    for st in station_rows:
        services = []
        if st["has_market"]:          services.append("Market")
        if st["has_shipyard"]:        services.append("Shipyard")
        if st["has_outfitting"]:      services.append("Outfitting")
        if st["has_refuel"]:          services.append("Refuel")
        if st["has_repair"]:          services.append("Repair")
        if st["has_rearm"]:           services.append("Restock")
        if st["has_black_market"]:    services.append("Black Market")
        if st["has_material_trader"]: services.append("Material Trader")
        if st["has_technology_broker"]: services.append("Technology Broker")
        if st["has_interstellar_factors"]: services.append("Interstellar Factors")
        if st["has_universal_cartographics"]: services.append("Universal Cartographics")
        if st["has_search_rescue"]:   services.append("Search and Rescue")

        stations.append({
            "id":                   st["id"],
            "name":                 st["name"],
            "type":                 st["type"],
            "distance_to_arrival":  st["distance_from_star"],
            "body_name":            st["body_name"],
            "landingPads":          {"large": 1 if st["landing_pad_size"] == "L" else 0},
            "economies":            [
                {"name": st["primary_economy"]} if st["primary_economy"] else None,
                {"name": st["secondary_economy"]} if st["secondary_economy"] else None,
            ],
            "services":             services,
            "updated_at":           st["updated_at"].isoformat() if st["updated_at"] else None,
        })

    record["stations"] = stations
    record["source"]   = "local_db"

    return {"record": record}


# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------
AUTOCOMPLETE_CANDIDATE_LIMIT = 100
AUTOCOMPLETE_RESULT_LIMIT = 10
AUTOCOMPLETE_SQL = f"""
    SELECT system_id64 AS id64, name,
           public.cube_ll_coord(position_ly, 1) AS x,
           public.cube_ll_coord(position_ly, 2) AS y,
           public.cube_ll_coord(position_ly, 3) AS z,
           galaxy_region_id
    FROM v3_derived.system_search
    WHERE derived_generation_id = $1 AND lower(name) LIKE $2
    LIMIT {AUTOCOMPLETE_CANDIDATE_LIMIT}
"""


def _autocomplete_prefix_pattern(q: str) -> str:
    """A literal, case-insensitive prefix using PostgreSQL's LIKE escape."""
    return q.lower().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


async def local_db_autocomplete(
    q: str,
    pool: asyncpg.Pool,
    *,
    generation_row: dict[str, Any] | None = None,
) -> list:
    """Read a bounded prefix window from the published V3 search projection.

    Migration 023's generation + text_pattern_ops index supports this LIKE
    range even under en_US.utf8. Sort only the bounded candidates in Python:
    an SQL ORDER BY name would sort every matching prefix before the LIMIT.
    The route may supply its resolved generation to keep a cache entry tied
    to the same immutable rows when a publication lands during the request.
    """
    if len(q) < 2:
        return []

    async with pool.acquire() as conn:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            if generation_row is None:
                try:
                    generation_row = await _current_derived_generation(conn)
                except HTTPException as exc:
                    if exc.status_code == 404:
                        raise HTTPException(503, 'No published Ratings V4 generation') from exc
                    raise
            # A generic prepared plan cannot derive LIKE bounds from $2. The
            # API disables its statement cache, but enforce a custom plan here
            # too so alternate pools keep the same bounded index read.
            await conn.execute('SET LOCAL plan_cache_mode = force_custom_plan')
            rows = await conn.fetch(
                AUTOCOMPLETE_SQL,
                generation_row['derived_generation_id'],
                _autocomplete_prefix_pattern(q),
            )

    results = []
    for r in sorted(rows, key=lambda row: row['name'])[:AUTOCOMPLETE_RESULT_LIMIT]:
        coords = _safe_coords(r)
        results.append({
            "name":              r["name"],
            "id64":              r["id64"],
            "x":                 coords["x"],
            "y":                 coords["y"],
            "z":                 coords["z"],
            "galaxy_region_id":  r["galaxy_region_id"],
            "record": {
                "id64": r["id64"],
                "name": r["name"],
                "x":    coords["x"],
                "y":    coords["y"],
                "z":    coords["z"],
            },
        })
    return results


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
async def local_db_status(pool: asyncpg.Pool) -> dict:
    """Return info about the PostgreSQL database and import progress."""
    try:
        async with pool.acquire() as conn:
            # Use pg_class.reltuples (PG's ANALYZE-derived row estimate) instead of
            # COUNT(*) — returns in ~1ms per table vs 30+ seconds for COUNT(*) FROM
            # bodies on the 1B+ row table. Estimates are plenty accurate for a
            # status display and refresh on every autovac/analyze cycle.
            #
            # Why: build_grid.py was being starved of disk I/O by repeated polls
            # of /api/local/status from the V2 Admin tab (every 30s). Each poll
            # triggered 8 sequential COUNT(*) full scans through pgbouncer.
            counts = await conn.fetchrow("""
                SELECT
                  COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='systems'),         0) AS sys_count,
                  COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='bodies'),          0) AS body_count,
                  COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='stations'),        0) AS station_count,
                  COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='cluster_summary'), 0) AS cluster_count,
                  COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='spatial_grid'),    0) AS grid_count,
                  COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='macro_grid'),      0) AS macro_count,
                  COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='ratings'),         0) AS rated_count,
                  COALESCE((SELECT count(*)         FROM galaxy_regions),                            0) AS region_count
            """)
            sys_count     = int(counts["sys_count"])
            body_count    = int(counts["body_count"])
            station_count = int(counts["station_count"])
            cluster_count = int(counts["cluster_count"])
            grid_count    = int(counts["grid_count"])
            macro_count   = int(counts["macro_count"])
            rated_count   = int(counts["rated_count"])
            region_count  = int(counts["region_count"])

            import_rows = await conn.fetch("""
                SELECT dump_file, status, rows_processed, errors_encountered,
                       started_at, completed_at
                FROM import_meta ORDER BY id
            """)
            import_status = {
                r["dump_file"]: {
                    "status":             str(r["status"]),
                    "rows_processed":     r["rows_processed"],
                    "errors_encountered": r["errors_encountered"],
                    "started_at":         r["started_at"].isoformat() if r["started_at"] else None,
                    "completed_at":       r["completed_at"].isoformat() if r["completed_at"] else None,
                }
                for r in import_rows
            }

            db_size_bytes = await conn.fetchval(
                "SELECT pg_database_size(current_database())"
            )
            pg_version = await conn.fetchval("SELECT version()")

        return {
            "available":            True,
            "backend":              "postgresql",
            "pg_version":           pg_version,
            "systems_count":        sys_count,
            "rated_count":          rated_count,
            "body_count":           body_count,
            "station_count":        station_count,
            "cluster_count":        cluster_count,
            "grid_cells":           grid_count,
            "macro_grid_cells":     macro_count,
            "galaxy_regions":       region_count,
            "db_size_mb":           round(db_size_bytes / 1_048_576, 1) if db_size_bytes else 0,
            "import_status":        import_status,
            "max_search_radius_ly": int(MAX_SEARCH_RADIUS),
            "cluster_radius_ly":    int(CLUSTER_RADIUS_LY),
            "has_bodies":           True,
            "has_cluster_summary":  cluster_count > 0,
        }
    except Exception as exc:
        # The raw exception text (which can carry SQL, host names or DSN
        # fragments) stays in the server log; the caller only ever sees one
        # of the fixed STATUS_REASON_CODES. Callers must not cache this
        # branch — a cached failure hides recovery for the whole TTL.
        reason = status_failure_reason_code_for_exception(exc)
        log.warning("local_db_status error (%s): %s: %s", reason, type(exc).__name__, exc)
        return {"available": False, "reason": reason}


# ---------------------------------------------------------------------------
# Body normalizer (for system detail modal)
# ---------------------------------------------------------------------------
def _normalize_body(row: asyncpg.Record) -> dict:
    rings_raw = row.get("ring_types") or []
    if isinstance(rings_raw, str):
        try:
            rings_raw = json.loads(rings_raw)
        except Exception:
            rings_raw = []
    rings = [{"type": t} for t in rings_raw if t]

    signals_raw = row.get("signals") or []
    if isinstance(signals_raw, str):
        try:
            signals_raw = json.loads(signals_raw)
        except Exception:
            signals_raw = []

    if not signals_raw and row.get("has_signals"):
        volc = (row.get("volcanism") or "").strip()
        if volc and volc not in ("", "No volcanism"):
            signals_raw = [{"name": "Geological", "count": 1}]
        else:
            signals_raw = [{"name": "Biological", "count": 1}]

    return {
        "id":                    row.get("id"),
        "name":                  row.get("name"),
        "type":                  row.get("type"),
        "subtype":               row.get("subtype"),
        "distance_to_arrival":   row.get("distance_from_star"),
        "is_landable":           bool(row.get("is_landable", False)),
        "is_rotational_period_tidally_locked": bool(row.get("is_tidal_lock", False)),
        "atmosphere":            row.get("atmosphere"),
        "volcanism":             row.get("volcanism"),
        "terraforming_state":    row.get("terraform_state"),
        "gravity":               row.get("surface_gravity"),
        "surface_temperature":   row.get("surface_temp"),
        "mass":                  row.get("mass"),
        "radius":                row.get("radius"),
        "estimated_mapping_value":  row.get("mapped_value"),
        "estimated_scan_value":     row.get("scan_value"),
        "bio_signals":           row.get("bio_signals") or 0,
        "geo_signals":           row.get("geo_signals") or 0,
        "has_signals":           bool(row.get("has_signals", False)),
        "rings":                 rings,
        "signals":               signals_raw,
    }
