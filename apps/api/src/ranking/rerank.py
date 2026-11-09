"""V3 Finder F3 slice 2 — wide-row detail read and client-weighted rerank.

Pure helpers behind `POST /api/archetypes/rerank`, `GET /api/archetypes/
system/{id64}` and `POST /api/archetypes/simulate`. Every identifier comes
from the hashed ``PROFILE_SPEC`` (its ``archetype_columns`` map); every value
is an asyncpg parameter. No legacy relation is referenced.

The rerank blend is deliberately **not** part of the hashed ranking-profile
identity: its weights are supplied per request, so it is a client-weighted
post-processing step over the profile's stored, uncertainty-modified keys —
not a profile parameter. Responses still echo the profile + generation
identity that produced the underlying scores. Keeping the blend outside
``_canonical_base()`` keeps ``ranking_sha256`` (pinned by the deployed-image
attestation in ``deploy/v3-production/target-authority.json``) unchanged.

Blend definition (one wide row per system, migration 011):

    blend_e4       = sum(w_k * <k>_weighted_e4) / sum(w_k)        in [0, 1e6]
    reranked_score = round(blend_e4 / 10000)                      in [0, 100]
    order          = blend_e4 DESC, system_id64 ASC

``<k>_weighted_e4`` is the stored ``score x confidence x completeness`` key
(``STORED_WEIGHTED_KEY_EXPRESSION`` in the profile), so the rerank honours the
profile's soft uncertainty modifier exactly as `/rankings` ordering does.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from .profile import ARCHETYPE_KEYS, PROFILE_SPEC, TIER_THRESHOLDS

RERANK_ID_LIMIT: Final[int] = 500
SYSTEM_ID64_MIN: Final[int] = 0
SYSTEM_ID64_MAX: Final[int] = 2**63 - 1
RERANK_WEIGHT_MIN: Final[float] = 0.0
RERANK_WEIGHT_MAX: Final[float] = 1.0
# ``<k>_weighted_e4`` is bounded 0..1_000_000 by migration 011; dividing by this
# scale maps it back onto the 0..100 score scale the tiers are defined on.
WEIGHTED_E4_SCALE: Final[int] = 10_000
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


def tier_for(score: float) -> str:
    """Map a 0..100 score onto the profile's S/A/B/C/D tier thresholds."""

    for tier in ("S", "A", "B", "C"):
        if score >= TIER_THRESHOLDS[tier]:
            return tier
    return "D"


def equal_weights() -> dict[str, float]:
    """The generalist blend: every V3 archetype weighted equally."""

    share = 1.0 / len(ARCHETYPE_KEYS)
    return {key: share for key in ARCHETYPE_KEYS}


def single_archetype_weights(archetype: str) -> dict[str, float]:
    """A blend that weights exactly one V3 archetype fit."""

    if archetype not in ARCHETYPE_KEYS:
        raise ValueError(f"unknown archetype key: {archetype!r}")
    return {key: (1.0 if key == archetype else 0.0) for key in ARCHETYPE_KEYS}


def validate_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """Return a complete, bounded weight map or raise ``ValueError``.

    Every V3 archetype key must be present exactly once, each weight must lie
    in ``[RERANK_WEIGHT_MIN, RERANK_WEIGHT_MAX]`` and at least one weight must
    be positive (an all-zero blend has no ordering).
    """

    unknown = sorted(set(weights) - set(ARCHETYPE_KEYS))
    if unknown:
        raise ValueError(f"unknown rerank weight key(s): {unknown}")
    missing = [key for key in ARCHETYPE_KEYS if key not in weights]
    if missing:
        raise ValueError(f"missing rerank weight key(s): {missing}")
    resolved: dict[str, float] = {}
    for key in ARCHETYPE_KEYS:
        value = float(weights[key])
        if value != value or not RERANK_WEIGHT_MIN <= value <= RERANK_WEIGHT_MAX:
            raise ValueError(
                f"rerank weight {key!r} must be between {RERANK_WEIGHT_MIN} and "
                f"{RERANK_WEIGHT_MAX}"
            )
        resolved[key] = value
    if sum(resolved.values()) <= 0.0:
        raise ValueError("at least one rerank weight must be greater than zero")
    return resolved


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


def rerank_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    weights: Mapping[str, float],
    picked_archetype: str | None,
) -> list[dict[str, Any]]:
    """Blend each wide row by ``weights`` and return rows in rerank order.

    ``original_score`` is the picked archetype's raw stored score, or the
    summary ``best_colony_potential`` when no archetype is picked — the same
    primary-score rule the profile applies.
    """

    resolved = validate_weights(weights)
    if picked_archetype is not None and picked_archetype not in ARCHETYPE_KEYS:
        raise ValueError(f"unknown archetype key: {picked_archetype!r}")
    total = sum(resolved.values())

    keyed: list[tuple[float, int, dict[str, Any]]] = []
    for row in rows:
        blend_e4 = 0.0
        blend_confidence = 0.0
        for key in ARCHETYPE_KEYS:
            weight = resolved[key]
            if weight == 0.0:
                continue
            columns = _ARCHETYPE_COLUMNS[key]
            blend_e4 += weight * int(row[columns["weighted_key"]])
            blend_confidence += weight * int(row[f"{key}_confidence_ppm"])
        blend_e4 /= total
        blend_confidence /= total * PPM
        reranked_score = int(round(blend_e4 / WEIGHTED_E4_SCALE))
        if picked_archetype is None:
            original_score = float(row["best_colony_potential"])
        else:
            original_score = float(row[_ARCHETYPE_COLUMNS[picked_archetype]["score"]])
        system_id64 = int(row["system_id64"])
        keyed.append((
            blend_e4,
            system_id64,
            {
                "id64": system_id64,
                "reranked_score": reranked_score,
                "tier": tier_for(reranked_score),
                "original_score": round(original_score, 2),
                "confidence": round(blend_confidence, 4),
                "completeness": round(int(row["completeness_ppm"]) / PPM, 6),
                "primary_archetype": row["primary_archetype"],
                "secondary_archetype": row["secondary_archetype"],
            },
        ))

    keyed.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in keyed]
