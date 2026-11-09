"""V3 Finder ranking-profile identity (F2c, Task 1).

Pure, DB-free identity for the versioned ranking profile that turns the V3
derived projections (`v3_app.system_search`, `v3_app.system_archetype`,
`v3_app.system_archetype_summary`) into an ordering. This module holds
**identity only**:

- `RANKING_VERSION` — the current profile's version string.
- `PROFILE_SPEC` — the canonical, hashable ranking definition (primary-score
  rule, uncertainty modifier, tie-break, tier thresholds, hard-filter keys,
  archetype keys) plus its own recorded `ranking_sha256`.
- `ranking_sha256()` — recomputes the sha256 of the canonical spec so a
  drift-guard test can assert the recorded value never silently diverges
  from the live definition.
- `RANKING_VERSIONS` / `resolve()` — a small registry so routers select a
  version rather than hand-writing ranking logic.

No database access and no SQL live here — the parameterized-SQL builder
that consumes this spec is a separate, later task (F2c Task 2). Pure
stdlib only (`hashlib`, `json`).

See docs/superpowers/specs/2026-09-27-v3-finder-f2c-f3-ranking-design.md and
docs/superpowers/plans/2026-09-27-v3-finder-f2c-f3-ranking.md (Task 1).
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any, Final

RANKING_VERSION: Final[str] = "v3-colony-potential-2"

# The 8 V3-native archetype keys computed by F2b (scripts/v3_system_archetype_
# model.py's `ARCHETYPE_KEYS` is the source of truth). Duplicated here as a
# plain literal rather than imported: the deployed API image only ships
# `apps/api/src/` (see apps/api/Dockerfile), not the repo-root `scripts/`
# tree, and this module must stay pure/DB-free with no cross-tree coupling.
# Keep in sync if the archetype set ever changes.
ARCHETYPE_KEYS: Final[tuple[str, ...]] = (
    "paradise",
    "mining_hub",
    "manufacturing_hub",
    "megacomplex",
    "research_hub",
    "stronghold",
    "population_capital",
    "flexible",
)

# Tier thresholds from the F2b projection contract: S>=88, A>=76, B>=60,
# C>=45, else D.
TIER_THRESHOLDS: Final[dict[str, int]] = {"S": 88, "A": 76, "B": 60, "C": 45}

# The body/feature-count columns `v3_app.system_search` projects (F1
# `010_v3_system_search_body_type_counts.sql` + the earlier `landable_count`).
# Every one is a real non-negative integer column, so every one supports both
# a `>= min` and a `<= max` hard filter — the ranking-SQL builder maps
# `<col>_min`/`<col>_max` onto `s.<col> >= $n` / `s.<col> <= $n`. This is the
# single source of truth for which body-count filters are projection-backed;
# `local_search._v3_hard_filters` rejects any requested body filter that does
# not resolve into this set rather than silently dropping it.
BODY_COUNT_COLUMNS: Final[tuple[str, ...]] = (
    "elw_count",
    "ww_count",
    "ammonia_count",
    "gas_giant_count",
    "landable_count",
    "terraformable_count",
    "bio_signal_total",
    "geo_signal_total",
    "neutron_count",
    "black_hole_count",
    "white_dwarf_count",
    "hmc_count",
    "metal_rich_count",
    "rocky_count",
    "rocky_ice_count",
    "icy_count",
    "other_star_count",
    "ring_count",
    "walkable_count",
)

# The `<col>_min` / `<col>_max` hard-filter keys generated from the count
# columns above, in a fixed (column-order, then min-before-max) order so the
# hashed identity is deterministic.
_BODY_COUNT_FILTER_KEYS: Final[tuple[str, ...]] = tuple(
    f"{col}_{bound}" for col in BODY_COUNT_COLUMNS for bound in ("min", "max")
)

# Hard filters are applied first, before any score/order computation, and
# never gate on uncertainty (unknown != absent). Keys mirror the F1
# `system_search` facts + body-type counts (both range bounds) plus the
# distance/region bounds from
# `docs/superpowers/specs/2026-09-27-v3-finder-f2c-f3-ranking-design.md`.
HARD_FILTER_KEYS: Final[tuple[str, ...]] = (
    *_BODY_COUNT_FILTER_KEYS,
    "has_rings",
    "has_biologicals",
    "has_geologicals",
    "main_star_class_in",
    "min_development_score",
    "min_distance_ly",
    "max_distance_ly",
    "galaxy_region",
)

# The stored key's only ranking tie-break. Distance is a separate primary sort
# for anchored nearest-first requests, not part of picked-archetype ranking.
TIE_BREAK: Final[tuple[str, ...]] = ("system_id64",)

ARCHETYPE_SCORE_FLOOR_DEFAULT: Final[int] = 60
ACCEPTED_ARCHETYPE_SCORE_FLOORS: Final[tuple[int, ...]] = (60, 76, 88)
RANKING_WINDOW_LIMIT: Final[int] = 10_000
RANKING_OFFSET_PLUS_LIMIT_MAX: Final[int] = 10_000
RANKING_COUNT_SENTINEL: Final[int] = 10_001
RANKING_PAGE_LIMIT_MIN: Final[int] = 1
RANKING_PAGE_LIMIT_MAX: Final[int] = 500
RANKING_OFFSET_MIN: Final[int] = 0
RANKING_OFFSET_MAX: Final[int] = 9_999

PICKED_ARCHETYPE_REJECTED_FILTERS: Final[tuple[str, ...]] = (
    "galaxy_region",
    "max_distance_ly",
    "has_elw",
    "min_slots",
    "max_contamination",
)
PICKED_ARCHETYPE_FILTER_REJECTION_REASON: Final[str] = (
    "archetype ranking is unfiltered in the initial product; "
    "filter in Any mode or drop the filter"
)

LOCAL_SEARCH_RESULT_CAP: Final[int] = 10_000
LOCAL_SEARCH_FROM_MIN: Final[int] = 0
LOCAL_SEARCH_SIZE_MIN: Final[int] = 1
LOCAL_SEARCH_REQUEST_RADIUS_DEFAULT_LY: Final[int] = 500
LOCAL_SEARCH_MAX_RADIUS_DEFAULT_LY: Final[int] = 10_000
LOCAL_SEARCH_FILTERED_ANCHORED_RADIUS_CAP_LY: Final[int] = 500
LOCAL_SEARCH_GALAXY_WIDE_FILTER_REJECTION_REASON: Final[str] = (
    "pick an anchor area or remove the filter"
)
LOCAL_SEARCH_ANCHORED_RADIUS_REJECTION_REASON: Final[str] = (
    "reduce the radius, sort by distance, or remove the filter"
)

# This text is deliberately part of the identity verbatim.  It is the exact
# integer arithmetic used by migration 011 and the wide-row builder.
STORED_WEIGHTED_KEY_EXPRESSION: Final[str] = (
    "((<key>_score::bigint * <key>_confidence_ppm * completeness_ppm) "
    "+ 50000000) / 100000000"
)


def _archetype_columns() -> dict[str, dict[str, str]]:
    """Return the fixed key-to-wide-column map used by ranking SQL."""

    return {
        key: {
            "score": f"{key}_score",
            "tier": f"{key}_tier",
            "confidence": (
                f"({key}_confidence_ppm / 1000000.0)::double precision"
            ),
            "weighted_key": f"{key}_weighted_e4",
        }
        for key in ARCHETYPE_KEYS
    }


def _json(value: Any) -> str:
    """Canonical JSON encoding.

    Mirrors the `_json`/hashing style in `scripts/v3_system_archetype.py`:
    sorted keys, compact separators, ASCII-safe, no NaN/Infinity — so the
    same logical spec always serialises to the same bytes.
    """

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _canonical_base() -> dict[str, Any]:
    """The canonical ranking spec, excluding its own recorded sha.

    This is the single source of truth both `PROFILE_SPEC` and
    `ranking_sha256()`'s default hash are built from, so the two can never
    drift apart by construction.
    """

    return {
        "ranking_version": RANKING_VERSION,
        "primary_score_rule": {
            "archetype": "archetype_columns[picked_archetype].score",
            "economy": "potential_score",
            "none": "system_archetype_summary.best_colony_potential",
        },
        "uncertainty": {
            # Soft modifier only (unknown != absent) — never a hard filter.
            # Starting curve is the plain product of confidence and
            # completeness; both bounded [0, 1]. This curve is a tunable,
            # benchmark-calibrated profile parameter and is part of the
            # hashed identity, so any future retune bumps ranking_sha256.
            "factor_expr": "confidence * completeness",
            "confidence_range": [0.0, 1.0],
            "completeness_range": [0.0, 1.0],
        },
        "tie_break": list(TIE_BREAK),
        "tier_thresholds": dict(TIER_THRESHOLDS),
        "hard_filter_keys": list(HARD_FILTER_KEYS),
        "archetype_keys": list(ARCHETYPE_KEYS),
        "archetype_columns": _archetype_columns(),
        "archetype_score_floor": {
            "default": ARCHETYPE_SCORE_FLOOR_DEFAULT,
            "accepted": list(ACCEPTED_ARCHETYPE_SCORE_FLOORS),
        },
        "stored_weighted_key_expression": STORED_WEIGHTED_KEY_EXPRESSION,
        "ranking_window": {
            "limit": RANKING_WINDOW_LIMIT,
            "offset_plus_limit_max": RANKING_OFFSET_PLUS_LIMIT_MAX,
            "page_limit_min": RANKING_PAGE_LIMIT_MIN,
            "page_limit_max": RANKING_PAGE_LIMIT_MAX,
            "offset_min": RANKING_OFFSET_MIN,
            "offset_max": RANKING_OFFSET_MAX,
            "count_sentinel": RANKING_COUNT_SENTINEL,
            "is_truncated_rule": "qualifying = 10001",
            "total_rule": "min(qualifying, 10000)",
        },
        "picked_archetype_rejected_filters": list(
            PICKED_ARCHETYPE_REJECTED_FILTERS
        ),
        "picked_archetype_filter_rejection_reason": (
            PICKED_ARCHETYPE_FILTER_REJECTION_REASON
        ),
        "local_search_contract": {
            "request_bounds": {
                "from_min": LOCAL_SEARCH_FROM_MIN,
                "size_min": LOCAL_SEARCH_SIZE_MIN,
                "from_plus_size_max": RANKING_OFFSET_PLUS_LIMIT_MAX,
            },
            "count": {
                "cap": LOCAL_SEARCH_RESULT_CAP,
                "sentinel_rule": "cap + 1",
                "total_rule": "min(qualifying, cap)",
                "total_is_capped_rule": "qualifying > cap",
                "applies_to": ["galaxy-wide", "anchored"],
            },
            "galaxy_wide_any": {
                "rejected_hard_filters": list(HARD_FILTER_KEYS),
                "rejection_rule": "reject any hard filter",
                "reason": LOCAL_SEARCH_GALAXY_WIDE_FILTER_REJECTION_REASON,
            },
            "anchored_any": {
                "intrinsic_radius_key": "max_distance_ly",
                "intrinsic_radius_is_hard_filter": False,
                "min_distance_ly_is_hard_filter": True,
                "other_filter_rule": "every other hard filter remains hard",
                "nearest_first_sort": "distance",
                "score_sort": "development",
                "request_radius_default_ly": (
                    LOCAL_SEARCH_REQUEST_RADIUS_DEFAULT_LY
                ),
                "route_max_radius_setting": "MAX_SEARCH_RADIUS",
                "route_max_radius_default_ly": (
                    LOCAL_SEARCH_MAX_RADIUS_DEFAULT_LY
                ),
                "unfiltered_nearest_first_radius_rule": (
                    "accepted through the route maximum"
                ),
                "restricted_request_rule": (
                    "any hard filter or score-sorted"
                ),
                "restricted_radius_cap_ly": (
                    LOCAL_SEARCH_FILTERED_ANCHORED_RADIUS_CAP_LY
                ),
                "restricted_radius_reason": (
                    LOCAL_SEARCH_ANCHORED_RADIUS_REJECTION_REASON
                ),
            },
        },
    }


def ranking_sha256(spec: Mapping[str, Any] | None = None) -> str:
    """sha256 hex digest of a canonicalized ranking spec.

    Excludes the spec's own `ranking_sha256` field (a spec can't hash
    itself). With no argument, hashes the live `_canonical_base()` — the
    drift guard: `ranking_sha256() == PROFILE_SPEC["ranking_sha256"]` must
    always hold. Accepts an explicit spec-shaped mapping so callers (tests)
    can hash an arbitrary/mutated spec, e.g. to prove the guard would catch
    a silent definition change.
    """

    source = _canonical_base() if spec is None else spec
    payload = {key: value for key, value in source.items() if key != "ranking_sha256"}
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


PROFILE_SPEC: Final[dict[str, Any]] = {
    **_canonical_base(),
    "ranking_sha256": ranking_sha256(),
}

RANKING_VERSIONS: Final[dict[str, dict[str, Any]]] = {
    RANKING_VERSION: PROFILE_SPEC,
}


def resolve(version: str) -> dict[str, Any]:
    """Resolve a `ranking_version` string to its frozen `PROFILE_SPEC`.

    Raises `KeyError` for an unknown version — callers (routers) turn this
    into a 4xx rather than silently falling back to a different profile.
    """

    try:
        return RANKING_VERSIONS[version]
    except KeyError as exc:
        raise KeyError(f"Unknown ranking_version: {version!r}") from exc
