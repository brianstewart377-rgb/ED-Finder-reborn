"""
ED Finder — Centralised search/SQL column mappings.
====================================================

Single source of truth for:

  * Canonical economy → ratings-table column mapping
  * Canonical economy → cluster_summary count column mapping
  * Body-filter request keys → ratings columns
  * Camel-case → snake-case alias map for body filters

Audit fix (2026-05-08, AUDIT_REPORT.md §H8 / §C5):
the previous repo had four separate copies of these dicts spread across
``local_search.py``, ``routers/search.py``, ``routers/map.py`` and the
retired ratings rerank router. They had already drifted in two ways:

  1. ``local_search.ECONOMY_SCORE_COL`` was missing ``"extraction"`` —
     so if a user filtered by ``economy=extraction``, the display-score
     and ORDER BY silently fell back to the overall ``r.score``,
     producing a different ordering than the same query in the inline
     fallback (which DID know about extraction).
  2. The retired ratings rerank router keyed by Title-case (``'Agriculture'``)
     while every other site keyed by lower-case (``'agriculture'``).

Centralising eliminates both bugs and gives us one place to add a new
economy in the future. Functions return strings already prefixed where
the call site uses them with an alias (``r.score_*``); call sites that
need bare column names use the un-prefixed lookup.
"""
from __future__ import annotations

from typing import Mapping, Optional

# ── Canonical list of supported economies ──────────────────────────────────
# Order matches the `economy_type` enum in sql/001_schema.sql.
ECONOMIES: tuple[str, ...] = (
    'agriculture',
    'refinery',
    'industrial',
    'hightech',
    'military',
    'tourism',
    'extraction',
)

# Common synonyms users / older callers may pass.
ECONOMY_ALIASES: Mapping[str, str] = {
    'high tech':  'hightech',
    'high-tech':  'hightech',
}

# Canonical → PostgreSQL `economy_type` enum literal mapping.
# The enum is Title-cased ("Agriculture", "HighTech", …) — see
# sql/001_schema.sql. Casting raw user input (e.g. "high tech") directly
# to ::economy_type raises InvalidTextRepresentationError, which after
# the Phase-2 search refactor surfaces as an HTTP 503 problem-detail
# (no silent inline-SQL fallback). Use `economy_enum_value()` below
# whenever you need the wire form for an SQL cast.
ECONOMY_ENUM_LITERALS: Mapping[str, str] = {
    'agriculture': 'Agriculture',
    'refinery':    'Refinery',
    'industrial':  'Industrial',
    'hightech':    'HighTech',
    'military':    'Military',
    'tourism':     'Tourism',
    'extraction':  'Extraction',
}


def _canon(name: Optional[str]) -> Optional[str]:
    """Lower-case + alias-resolve. Returns None for empty/unknown."""
    if not name:
        return None
    n = name.strip().lower()
    n = ECONOMY_ALIASES.get(n, n)
    return n if n in ECONOMIES else None


def canonical_economy_key(name: Optional[str]) -> Optional[str]:
    """Resolve a user-supplied economy to the lower-case DB column key."""
    return _canon(name)


def economy_enum_value(name: Optional[str]) -> Optional[str]:
    """Resolve any user-supplied economy form to its PostgreSQL enum literal.

    Accepts every shape the frontend / API consumers historically send —
    'Agriculture', 'agriculture', 'High Tech', 'high-tech', 'HighTech',
    'hightech', etc. Returns the exact spelling required by the
    `economy_type` enum, or None when the input is empty / unknown
    (caller should treat None as "no economy filter").

    >>> economy_enum_value('Agriculture')
    'Agriculture'
    >>> economy_enum_value('high tech')
    'HighTech'
    >>> economy_enum_value('High Tech')
    'HighTech'
    >>> economy_enum_value('extraction')
    'Extraction'
    >>> economy_enum_value('any') is None
    True
    >>> economy_enum_value('') is None
    True
    >>> economy_enum_value('Foo') is None
    True
    """
    canon = _canon(name)
    if canon is None:
        return None
    return ECONOMY_ENUM_LITERALS.get(canon)


# ── ratings.score_<economy> lookups ────────────────────────────────────────
def ratings_score_column(name: Optional[str], *, alias: str = '') -> str:
    """Return the ``ratings`` table column for the per-economy score, or
    ``r.score`` (overall) when ``name`` is missing/unknown.

    >>> ratings_score_column('agriculture')
    'score_agriculture'
    >>> ratings_score_column('agriculture', alias='r')
    'r.score_agriculture'
    >>> ratings_score_column(None)
    'score'
    >>> ratings_score_column('Tourism', alias='r')
    'r.score_tourism'
    """
    canon = _canon(name)
    col = f'score_{canon}' if canon else 'score'
    return f'{alias}.{col}' if alias else col


# ── mv_archetype_rankings score lookups ─────────────────────────────────────
ARCHETYPE_SCORE_COLS: Mapping[str, str] = {
    'agriculture': 'score_agriculture_terraforming',
    'refinery':    'score_refinery_industrial',
    'industrial':  'score_refinery_industrial',
    'hightech':    'score_hitech_tourism',
    'military':    'score_military_industrial',
    'tourism':     'score_hitech_tourism',
    'extraction':  'score_extraction_refinery',
}


def archetype_score_column(name: Optional[str], *, alias: str = '') -> str:
    """Return the mv_archetype_rankings score column for an economy-aligned
    archetype, or overall_development_potential when the economy is missing.

    This keeps Finder/search aligned with the archetype cutover without
    removing the legacy ratings fields from the response.
    """
    canon = _canon(name)
    col = ARCHETYPE_SCORE_COLS.get(canon, 'overall_development_potential')
    return f'{alias}.{col}' if alias else col


# ── cluster_summary.<economy>_count lookups ────────────────────────────────
def cluster_count_column(name: Optional[str], *, alias: str = 'cs') -> Optional[str]:
    """Return the ``cluster_summary`` count column for an economy, or
    ``None`` if the economy isn't recognised.

    Note: cluster_summary only carries the six "core" economies
    (no extraction column today).
    """
    canon = _canon(name)
    if canon is None or canon == 'extraction':
        return None
    return f'{alias}.{canon}_count' if alias else f'{canon}_count'


# ── Body filter columns ────────────────────────────────────────────────────
# The server accepts body-filter keys in snake_case; older/legacy callers may
# still send camelCase, which we accept via BODY_FILTER_ALIASES. (The snake_case
# key contract was originally defined by BODY_BACKEND_KEY in the retired React
# reference tree frontend/src/features/search/useSearch.ts — historical evidence
# only per CLAUDE.md, NOT the live apps/web caller.)
BODY_FILTER_COLS: Mapping[str, str] = {
    # Body-type counts
    'landable':      'landable_count',
    'landable_count': 'landable_count',
    'terraformable': 'terraformable_count',
    'terraformable_count': 'terraformable_count',
    'elw':           'elw_count',
    'elw_count':     'elw_count',
    'ww':            'ww_count',
    'ww_count':      'ww_count',
    'ammonia':       'ammonia_count',
    'ammonia_count': 'ammonia_count',
    'gas_giant':     'gas_giant_count',
    'gas_giant_count': 'gas_giant_count',
    'hmc':           'hmc_count',
    'hmc_count':     'hmc_count',
    'metal_rich':    'metal_rich_count',
    'metal_rich_count': 'metal_rich_count',
    'rocky':         'rocky_count',
    'rocky_count':   'rocky_count',
    'rocky_ice':     'rocky_ice_count',
    'rocky_ice_count': 'rocky_ice_count',
    'icy':           'icy_count',
    'icy_count':     'icy_count',
    # Star-type counts (stored on ratings)
    'neutron':       'neutron_count',
    'neutron_count': 'neutron_count',
    'black_hole':    'black_hole_count',
    'black_hole_count': 'black_hole_count',
    'white_dwarf':   'white_dwarf_count',
    'white_dwarf_count': 'white_dwarf_count',
    'other_star':    'other_star_count',
    'other_star_count': 'other_star_count',
    # Body-system aggregates (sql/008_body_filter_aggregates.sql)
    'rings':         'ring_count',
    'ring_count':    'ring_count',
    'walkable':      'walkable_count',
    'walkable_count': 'walkable_count',
    # Signal totals
    'bio':           'bio_signal_total',
    'bio_signal_total': 'bio_signal_total',
    'geo':           'geo_signal_total',
    'geo_signal_total': 'geo_signal_total',
}

# Backwards-compat aliases for older camelCase callers.
BODY_FILTER_ALIASES: Mapping[str, str] = {
    'gasGiant':   'gas_giant',
    'blackHole':  'black_hole',
    'whiteDwarf': 'white_dwarf',
    'metalRich':  'metal_rich',
    'rockyIce':   'rocky_ice',
}


def normalise_body_filters(body_filters: dict) -> dict:
    """Return a copy of ``body_filters`` with camelCase aliases resolved
    to their snake_case canonicals (without overwriting an existing
    canonical key)."""
    if not body_filters:
        return {}
    out = dict(body_filters)
    for alias, canonical in BODY_FILTER_ALIASES.items():
        if alias in out and canonical not in out:
            out[canonical] = out[alias]
    return out


def body_filter_column(key: str, *, alias: str = '') -> Optional[str]:
    """Resolve a body-filter request key to a ratings column. Returns
    ``None`` if ``key`` isn't a known filter.
    """
    col = BODY_FILTER_COLS.get(key)
    if col is None:
        return None
    return f'{alias}.{col}' if alias else col


__all__ = [
    'ECONOMIES',
    'ECONOMY_ALIASES',
    'ECONOMY_ENUM_LITERALS',
    'canonical_economy_key',
    'economy_enum_value',
    'ratings_score_column',
    'archetype_score_column',
    'cluster_count_column',
    'BODY_FILTER_COLS',
    'BODY_FILTER_ALIASES',
    'normalise_body_filters',
    'body_filter_column',
]
