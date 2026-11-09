"""Reconstructs the retired ``ratings.score_breakdown`` JSONB shape at read time.

``score_breakdown`` was dropped from ``build_ratings.py`` (Phase B step 1,
storage recovery 2026-07-12) because every value inside it either duplicates
a standalone `ratings` column or is cheaply recomputable from one. This
module rebuilds the exact same dict shape the old column held, so API
consumers and the frontend contract do not change.

``_classify_rocky_subsplits`` mirrors the rocky branch of
``apps/importer/src/build_ratings.py::classify_bodies`` (the ``is_rocky``
block). If that branch's classification rules change, this function must
change with it — there is no shared import between the two apps to catch
drift automatically, so a cross-check test comparing both outputs against
the same fixture bodies is the intended guardrail (see plan §7).

``primary_economy``/``secondary_economy``/``top_pair`` mirror the tie-break
logic in ``rate_system()`` (``sorted_ecos`` + ``COMPLEMENTARY_PAIRS``).
Note ``primary_economy`` is NOT the same value as the stored
``economy_suggestion`` column: ``economy_suggestion`` is ``None`` whenever
the top economy scores below 20, but ``primary_economy`` here is always the
actual top-ranked economy. Do not substitute one for the other.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# Mirrors build_ratings.py's COMPLEMENTARY_PAIRS (rate_system(), v3.2 overall
# score computation). Order matters for the max() tie-break below only in
# the degenerate case of an exact pair-score tie, matching original behaviour.
_COMPLEMENTARY_PAIRS: tuple[tuple[str, str], ...] = (
    ('Extraction',  'Refinery'),
    ('Refinery',    'Industrial'),
    ('Industrial',  'HighTech'),
    ('HighTech',    'Tourism'),
    ('Agriculture', 'Tourism'),
    ('HighTech',    'Military'),
    ('Industrial',  'Military'),
    ('Agriculture', 'HighTech'),
)


def _value_or_zero(value: Any) -> Any:
    """Return zero for absent rating values while preserving numeric types."""
    return 0 if value is None else value


def _rank_economies(economies: Mapping[str, Any]) -> tuple[str | None, str | None, dict]:
    """Mirrors build_ratings.py rate_system() lines ~1181-1220 and ~1246-1252."""
    if not economies:
        return None, None, {'a': None, 'b': None, 'a_score': 0, 'b_score': 0, 'pair_score': 0}

    sorted_ecos = sorted(economies.items(), key=lambda kv: kv[1], reverse=True)

    pair_scores = [
        (a, b, (economies[a] + economies[b]) / 2)
        for a, b in _COMPLEMENTARY_PAIRS
        if a in economies and b in economies
    ]
    best_a, best_b, best_pair = max(pair_scores, key=lambda t: t[2])

    top_score = sorted_ecos[0][1]
    tied_top = [eco for eco, sc in sorted_ecos if sc == top_score]
    if len(tied_top) >= 2:
        if best_a in tied_top:
            primary_eco = best_a
        elif best_b in tied_top:
            primary_eco = best_b
        else:
            primary_eco = tied_top[0]
    else:
        primary_eco = sorted_ecos[0][0]

    secondary_eco = next((eco for eco, _ in sorted_ecos if eco != primary_eco), None)

    top_pair = {
        'a':          best_a,
        'b':          best_b,
        'a_score':    int(economies[best_a]),
        'b_score':    int(economies[best_b]),
        'pair_score': int(round(best_pair)),
    }
    return primary_eco, secondary_eco, top_pair


def _classify_rocky_subsplits(bodies: Sequence[Mapping[str, Any]]) -> dict:
    """Mirrors build_ratings.py classify_bodies() rocky branch, lines ~380-401.

    Expects each body mapping to carry `subtype` (or `sub_type`),
    `geo_signal_count`, `bio_signal_count`, and `has_rings`.
    """
    rocky_clean = rocky_geo = rocky_bio = rocky_rings = 0

    for b in bodies:
        sub = str(b.get('subtype') or b.get('sub_type') or '').lower()
        is_rocky = 'rocky body' in sub or sub == 'rocky'
        if not is_rocky:
            continue

        geo_count = int(b.get('geo_signal_count') or 0)
        bio_count = int(b.get('bio_signal_count') or 0)
        has_rings = bool(b.get('has_rings', False))
        has_geo = geo_count > 0
        has_bio = bio_count > 0

        if has_geo and has_bio:
            continue  # rocky_mixed — not one of the 4 reconstructed keys
        elif has_geo:
            rocky_geo += 1
        elif has_bio:
            rocky_bio += 1
        elif has_rings:
            rocky_rings += 1
        else:
            rocky_clean += 1

    return {
        'rocky_clean': rocky_clean,
        'rocky_geo':   rocky_geo,
        'rocky_bio':   rocky_bio,
        'rocky_rings': rocky_rings,
    }


def reconstruct_score_breakdown(
    rating_row: Mapping[str, Any],
    bodies: Sequence[Mapping[str, Any]] = (),
) -> dict:
    """Rebuilds the retired score_breakdown JSONB shape from stored columns.

    `bodies` is only iterated when `rating_row['rocky_count']` is truthy —
    93.3% of systems have rocky_count == 0, so most calls skip body
    classification entirely and reconstruct purely from `rating_row`.
    """
    economies = {
        'Agriculture': _value_or_zero(rating_row.get('score_agriculture')),
        'Refinery':    _value_or_zero(rating_row.get('score_refinery')),
        'Industrial':  _value_or_zero(rating_row.get('score_industrial')),
        'HighTech':    _value_or_zero(rating_row.get('score_hightech')),
        'Military':    _value_or_zero(rating_row.get('score_military')),
        'Tourism':     _value_or_zero(rating_row.get('score_tourism')),
        'Extraction':  _value_or_zero(rating_row.get('score_extraction')),
    }

    dimensions = {
        'slots':        _value_or_zero(rating_row.get('slots')),
        'strategic':    _value_or_zero(rating_row.get('body_quality')),
        'safety':       _value_or_zero(rating_row.get('orbital_safety')),
        'terraforming': _value_or_zero(rating_row.get('terraforming_potential')),
        'diversity':    _value_or_zero(rating_row.get('body_diversity')),
    }

    if rating_row.get('rocky_count'):
        rocky = _classify_rocky_subsplits(bodies)
    else:
        rocky = {'rocky_clean': 0, 'rocky_geo': 0, 'rocky_bio': 0, 'rocky_rings': 0}

    bodies_out = {
        'rocky_clean':   rocky['rocky_clean'],
        'rocky_geo':     rocky['rocky_geo'],
        'rocky_bio':     rocky['rocky_bio'],
        'rocky_rings':   rocky['rocky_rings'],
        'rocky_ice':     _value_or_zero(rating_row.get('rocky_ice_count')),
        'icy':           _value_or_zero(rating_row.get('icy_count')),
        'hmc':           _value_or_zero(rating_row.get('hmc_count')),
        'gas_giant':     _value_or_zero(rating_row.get('gas_giant_count')),
        'elw':           _value_or_zero(rating_row.get('elw_count')),
        'ww':            _value_or_zero(rating_row.get('ww_count')),
        'ammonia':       _value_or_zero(rating_row.get('ammonia_count')),
        'landable':      _value_or_zero(rating_row.get('landable_count')),
        'terraformable': _value_or_zero(rating_row.get('terraformable_count')),
        'bio':           _value_or_zero(rating_row.get('bio_signal_total')),
        'geo':           _value_or_zero(rating_row.get('geo_signal_total')),
    }

    primary_eco, secondary_eco, top_pair = _rank_economies(economies)

    has_standout = (
        (rating_row.get('elw_count') or 0)           >= 1 or
        (rating_row.get('ammonia_count') or 0)       >= 1 or
        (rating_row.get('black_hole_count') or 0)    >= 1 or
        (rating_row.get('neutron_count') or 0)       >= 1 or
        (rating_row.get('ww_count') or 0)            >= 2 or
        (rating_row.get('terraformable_count') or 0) >= 5
    )

    return {
        'economies':         economies,
        'dimensions':        dimensions,
        'bodies':            bodies_out,
        'primary_economy':   primary_eco,
        'secondary_economy': secondary_eco,
        'top_pair':          top_pair,
        'has_standout':      has_standout,
        'rationale':         rating_row.get('rationale'),
        'confidence':        rating_row.get('confidence'),
        'rating_version':    rating_row.get('rating_version'),
    }
