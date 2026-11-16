"""V3 Finder F3 slice 2 — the wide-row detail read behind one system's archetypes.

Pure helpers behind `GET /api/archetypes/system/{id64}`. Every identifier
comes from the hashed ``PROFILE_SPEC`` (its ``archetype_columns`` map); every
value is an asyncpg parameter. No legacy relation is referenced.

The client-weighted rerank this module was named for, and the build
simulation that shared its read, were removed on 16 November 2026 with
`POST /api/archetypes/rerank` and `/simulate`: no page of the site called
them. Git history keeps them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from .profile import ARCHETYPE_KEYS, PROFILE_SPEC

RERANK_ID_LIMIT: Final[int] = 500
SYSTEM_ID64_MIN: Final[int] = 0
SYSTEM_ID64_MAX: Final[int] = 2**63 - 1
PPM: Final[float] = 1_000_000.0

_ARCHETYPE_COLUMNS: Final[dict[str, dict[str, str]]] = {
    key: dict(columns) for key, columns in PROFILE_SPEC["archetype_columns"].items()
}

# The non-archetype wide-row columns the detail read selects, in a fixed order.
_SUMMARY_COLUMNS: Final[tuple[str, ...]] = (
    "archetype_version",
    "primary_archetype",
    "secondary_archetype",
    "best_colony_potential",
    "best_tier",
    "archetype_confidence_ppm",
    "weighted_potential",
    "completeness_ppm",
)


def validate_id64s(id64s: Iterable[int]) -> list[int]:
    """Deduplicate and bound a caller-supplied id64 list (order preserved)."""

    seen: set[int] = set()
    resolved: list[int] = []
    for raw in id64s:
        value = int(raw)
        if not SYSTEM_ID64_MIN <= value <= SYSTEM_ID64_MAX:
            raise ValueError("id64 values must fit a non-negative bigint")
        if value in seen:
            continue
        seen.add(value)
        resolved.append(value)
    if not resolved:
        raise ValueError("id64s list is required")
    if len(resolved) > RERANK_ID_LIMIT:
        raise ValueError(f"Maximum {RERANK_ID_LIMIT} systems per request")
    return resolved


def build_system_archetype_detail_query(
    spec: Mapping[str, Any],
    *,
    id64s: Sequence[int],
) -> tuple[str, list[Any]]:
    """One generation-pinned read of the search facts + wide archetype row.

    Returns ``(sql, params)`` selecting every ``v3_app.system_search`` column
    plus the wide row's per-archetype score/tier/confidence/weighted key and
    summary columns for each requested system. The ``v3_app.*`` views already
    pin the published generation; the caller resolves the generation row in
    the same REPEATABLE READ snapshot for the response identity block.
    """

    if spec.get("archetype_columns") != PROFILE_SPEC["archetype_columns"]:
        raise ValueError("ranking spec archetype_columns are not implemented by this builder")
    ids = validate_id64s(id64s)
    archetype_select = ",\n       ".join(
        f"a.{_ARCHETYPE_COLUMNS[key][field]}"
        for key in ARCHETYPE_KEYS
        for field in ("score", "tier", "weighted_key")
    )
    confidence_select = ",\n       ".join(
        f"a.{key}_confidence_ppm" for key in ARCHETYPE_KEYS
    )
    summary_select = ",\n       ".join(f"a.{column}" for column in _SUMMARY_COLUMNS)
    sql = (
        "SELECT s.*,\n"
        f"       {archetype_select},\n"
        f"       {confidence_select},\n"
        f"       {summary_select}\n"
        "FROM v3_app.system_search s\n"
        "JOIN v3_app.system_archetype a ON a.system_id64 = s.system_id64\n"
        "WHERE s.system_id64 = ANY($1::bigint[])\n"
        "ORDER BY s.system_id64 ASC"
    )
    return sql, [ids]


def archetype_entry(row: Mapping[str, Any], key: str) -> dict[str, Any]:
    """The stored per-archetype fit for ``key`` from one wide row."""

    columns = _ARCHETYPE_COLUMNS[key]
    return {
        "score": int(row[columns["score"]]),
        "tier": str(row[columns["tier"]]),
        "confidence": round(int(row[f"{key}_confidence_ppm"]) / PPM, 6),
        "weighted_e4": int(row[columns["weighted_key"]]),
    }


