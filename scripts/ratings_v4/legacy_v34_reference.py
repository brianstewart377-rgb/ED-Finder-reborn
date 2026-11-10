"""Static pure Ratings V3.4 reference extracted from the original scorer.

The comparison loader verifies these exact function ASTs and constant values
against apps/importer/src/build_ratings.py before using them. Importer setup,
database access, log files and the legacy overall score are excluded.
"""
from __future__ import annotations

from typing import Optional


RATING_VERSION = '3.4'


SCOOPABLE_STARS = {'O', 'B', 'A', 'F', 'G', 'K', 'M'}


def _distance_weight(ls) -> float:
    if ls is None:
        return 1.0
    try:
        ls = float(ls)
    except (TypeError, ValueError):
        return 1.0
    if ls <= 1_000:
        return 1.0
    if ls <= 10_000:
        return 0.85
    if ls <= 100_000:
        return 0.60
    return 0.30


def classify_bodies(bodies: list) -> dict:
    """
    Classify all bodies in a system and return a rich dict of counts and
    modifier flags that the scoring functions use.

    Returns:
        counts: dict with body type counts and modifier totals
        modifiers: dict with system-level flags (has_black_hole, etc.)
    """
    counts = {
        # Pure body types (no modifiers)
        'rocky_clean':    0,  # Rocky, no geo/bio/rings
        'rocky_geo':      0,  # Rocky + geo signals (adds Extraction+Industrial)
        'rocky_bio':      0,  # Rocky + bio signals (adds Agriculture+Terraforming)
        'rocky_rings':    0,  # Rocky + rings (adds Extraction)
        'rocky_mixed':    0,  # Rocky + multiple modifiers
        'rocky_ice':      0,  # Rocky Ice (inherently Industrial+Refinery)
        'icy':            0,  # Icy (Industrial only)
        'hmc':            0,  # High Metal Content (Extraction only)
        'hmc_geo':        0,  # HMC + geo signals
        'metal_rich':     0,  # Metal Rich (Extraction only)
        'gas_giant':      0,  # Gas Giant (HighTech+Industrial)
        'elw':            0,  # Earth-like World
        'ww':             0,  # Water World
        'ammonia':        0,  # Ammonia World
        # Landable counts (for surface port availability)
        'landable':       0,  # Total landable bodies
        'landable_rocky_clean': 0,  # Landable clean Rocky (ideal for CMM)
        'landable_rocky_any':   0,  # Landable Rocky (any modifier)
        'landable_hmc':         0,  # Landable HMC
        # Terraformable
        'terraformable':  0,
        # Signals (totals)
        'bio':            0,  # Total bio signal count
        'geo':            0,  # Total geo signal count
        # Tidal locking
        'tidal_lock':     0,  # Bodies with tidal locking
        # Stars
        'neutron':        0,
        'black_hole':     0,
        'white_dwarf':    0,
        # Generic counts for backward compatibility
        'rocky':          0,  # All rocky bodies (any modifier)
        'metal_rich_count': 0,
        'icy_count':      0,
        'rocky_ice_count': 0,
        'hmc_count':      0,
        'gas_giant_count': 0,
        'ring_count':      0,
        'ww_count':       0,
        'ammonia_count':  0,
        'elw_count':      0,
        # ── v3.1: distance-weighted equivalents ────────────────────────────
        # Same body counts but each body contributes its _distance_weight(ls)
        # instead of 1.0.  Scorers use these when they care about access.
        'w_landable':     0.0,
        'w_elw':          0.0,
        'w_ww':           0.0,
        'w_ammonia':      0.0,
        'w_gas_giant':    0.0,
        'w_rocky_rings':  0.0,
        'w_terraformable': 0.0,
        'w_hmc':          0.0,
        # ── v3.1: terraforming-quality score (0-100) ───────────────────────
        # Weighted by body type (HMC/Rocky ≫ Gas Giant), distance from star,
        # and main-star habitable-zone fit. Populated while iterating bodies.
        'tf_quality_acc': 0.0,   # raw accumulator; normalised later
        # ── v3.1: body-diversity information ───────────────────────────────
        # Shannon-diversity helper: we count distinct body-type buckets
        # present, then compute log-based bonus in rate_system().
        'type_bucket_counts': {},
    }

    for b in bodies:
        sub = str(b.get('subtype') or b.get('sub_type') or '').lower()
        is_landable     = bool(b.get('is_landable', False))
        is_terraformable = bool(b.get('is_terraformable', False))
        is_tidal_lock   = bool(b.get('is_tidal_lock', False))
        bio_count       = int(b.get('bio_signal_count') or 0)
        geo_count       = int(b.get('geo_signal_count') or 0)
        has_rings       = bool(b.get('has_rings', False))
        # v3.1: distance from arrival star (Ls); None if unknown
        ls = b.get('distance_from_star')
        w  = _distance_weight(ls)

        if has_rings:
            counts['ring_count'] += 1
        counts['bio'] += bio_count
        counts['geo'] += geo_count
        if is_tidal_lock:
            counts['tidal_lock'] += 1
        if is_terraformable:
            counts['terraformable'] += 1
            counts['w_terraformable'] += w
            # tf_quality weights HMC/Rocky much higher than Gas Giant
            tf_body_weight = 1.0
            if 'high metal content' in sub:
                tf_body_weight = 1.5
            elif 'rocky body' in sub or sub == 'rocky':
                tf_body_weight = 1.2
            elif 'gas giant' in sub:
                tf_body_weight = 0.3
            counts['tf_quality_acc'] += tf_body_weight * w
        if is_landable:
            counts['landable'] += 1
            counts['w_landable'] += w

        # ── Stars ─────────────────────────────────────────────────────────
        if 'neutron' in sub:
            counts['neutron'] += 1
            continue
        if 'black hole' in sub:
            counts['black_hole'] += 1
            continue
        if 'white dwarf' in sub:
            counts['white_dwarf'] += 1
            continue

        # ── Special worlds ────────────────────────────────────────────────
        is_elw = bool(b.get('is_earth_like')) or 'earth-like' in sub
        is_ww  = bool(b.get('is_water_world')) or 'water world' in sub
        is_amm = bool(b.get('is_ammonia_world')) or 'ammonia' in sub

        if is_elw:
            counts['elw'] += 1
            counts['elw_count'] += 1
            counts['w_elw'] += w
            counts['type_bucket_counts']['elw'] = counts['type_bucket_counts'].get('elw', 0) + 1
            if is_landable:
                counts['landable'] += 0  # already counted above
            continue
        if is_ww:
            counts['ww'] += 1
            counts['ww_count'] += 1
            counts['w_ww'] += w
            counts['type_bucket_counts']['ww'] = counts['type_bucket_counts'].get('ww', 0) + 1
            continue
        if is_amm:
            counts['ammonia'] += 1
            counts['ammonia_count'] += 1
            counts['w_ammonia'] += w
            counts['type_bucket_counts']['ammonia'] = counts['type_bucket_counts'].get('ammonia', 0) + 1
            continue

        # ── Gas Giants ────────────────────────────────────────────────────
        if 'gas giant' in sub:
            counts['gas_giant'] += 1
            counts['gas_giant_count'] += 1
            counts['w_gas_giant'] += w
            counts['type_bucket_counts']['gas_giant'] = counts['type_bucket_counts'].get('gas_giant', 0) + 1
            continue

        # ── Rocky bodies (most complex — contamination logic) ─────────────
        is_rocky = 'rocky body' in sub or sub == 'rocky'
        is_rocky_ice = 'rocky ice' in sub
        is_icy   = 'icy body' in sub or sub == 'icy'
        is_hmc   = 'high metal content' in sub
        is_metal_rich = 'metal-rich' in sub or 'metal rich' in sub

        if is_rocky:
            counts['rocky'] += 1
            counts['type_bucket_counts']['rocky'] = counts['type_bucket_counts'].get('rocky', 0) + 1
            has_geo   = geo_count > 0
            has_bio   = bio_count > 0
            if has_geo and has_bio:
                counts['rocky_mixed'] += 1
            elif has_geo:
                counts['rocky_geo'] += 1
            elif has_bio:
                counts['rocky_bio'] += 1
            elif has_rings:
                counts['rocky_rings'] += 1
                counts['w_rocky_rings'] += w
            else:
                counts['rocky_clean'] += 1

            if is_landable:
                counts['landable_rocky_any'] += 1
                if not has_geo and not has_bio:
                    counts['landable_rocky_clean'] += 1
            continue

        if is_rocky_ice:
            counts['rocky_ice'] += 1
            counts['rocky_ice_count'] += 1
            counts['type_bucket_counts']['rocky_ice'] = counts['type_bucket_counts'].get('rocky_ice', 0) + 1
            continue

        if is_icy:
            counts['icy'] += 1
            counts['icy_count'] += 1
            counts['type_bucket_counts']['icy'] = counts['type_bucket_counts'].get('icy', 0) + 1
            continue

        if is_hmc:
            counts['hmc'] += 1
            counts['hmc_count'] += 1
            counts['w_hmc'] += w
            counts['type_bucket_counts']['hmc'] = counts['type_bucket_counts'].get('hmc', 0) + 1
            has_geo = geo_count > 0
            has_bio = bio_count > 0
            if has_geo or has_bio:
                counts['hmc_geo'] += 1
            if is_landable:
                counts['landable_hmc'] += 1
            continue

        if is_metal_rich:
            counts['metal_rich'] += 1
            counts['metal_rich_count'] += 1
            counts['type_bucket_counts']['metal_rich'] = counts['type_bucket_counts'].get('metal_rich', 0) + 1
            continue

    return counts


def score_refinery(counts: dict) -> int:
    """
    Score a system for Refinery economy (0-100).

    Based on official mechanics:
    - Rocky bodies = Refinery base economy
    - Geo signals on Rocky = adds Extraction+Industrial (severe contamination)
    - Bio signals on Rocky = adds Agriculture+Terraforming (moderate contamination)
    - Rings on Rocky = adds Extraction (mild contamination — Extraction doesn't consume CMM)
    - Rocky Ice = Industrial+Refinery (mixed, moderate penalty)
    - HMC = Extraction only, but can host Refinery Hubs (workable with effort)
    - Zero landable bodies = cap at 25 (cannot build surface CMM port)

    Contamination philosophy: penalise but don't zero out contaminated bodies,
    because the Top Two Economies rule means you can compensate with Refinery Hubs.
    The penalty reflects the extra effort required.
    """
    score = 0.0

    # Clean Rocky: ideal Refinery body (Refinery only, no contamination)
    # 4 clean Rocky bodies = 72 pts (solid Refinery score)
    # 6 clean Rocky bodies = 100 pts (exceptional)
    score += min(counts['rocky_clean'], 6) * 18.0       # up to 6 clean Rocky = 108 pts (capped at 100)

    # Rocky + Rings: Refinery + Extraction (mild contamination)
    # Extraction doesn't consume CMM, so this is manageable
    score += min(counts['rocky_rings'], 4) * 11.0       # 61% of clean Rocky value

    # Rocky + Bio signals: Refinery + Agriculture + Terraforming (moderate contamination)
    # Need extra Refinery Hubs to stay in top 2
    score += min(counts['rocky_bio'], 3) * 6.0          # 33% of clean Rocky value

    # Rocky + Geo signals: Refinery + Extraction + Industrial (severe contamination)
    # Industrial competes directly with Refinery for CMM production
    score += min(counts['rocky_geo'], 3) * 3.5          # 19% of clean Rocky value

    # Rocky + multiple modifiers: worst case
    score += min(counts['rocky_mixed'], 2) * 2.0        # 11% of clean Rocky value

    # Rocky Ice: inherently Industrial+Refinery (mixed from the start)
    score += min(counts['rocky_ice'], 4) * 7.0          # 39% of clean Rocky value

    # HMC: Extraction only, but can host Refinery Hubs for CMM
    # Workable but requires significant extra construction effort
    score += min(counts['hmc'], 4) * 5.0                # 28% of clean Rocky value

    # Surface port requirement: if no landable bodies, CMM Composite is impossible
    # Cap the score — the system is still useful for Insulating Membranes (orbital)
    # but severely limited for the most valuable Refinery commodities
    if counts['landable'] == 0:
        score = min(score, 25.0)
    elif counts['landable_rocky_clean'] == 0 and counts['landable_rocky_any'] == 0 and counts['landable_hmc'] == 0:
        # Has landable bodies but none are Rocky or HMC — CMM is very hard
        score = min(score, 40.0)

    return min(int(score), 100)


def score_agriculture(counts: dict, main_star_type: Optional[str]) -> int:
    """
    Score a system for Agriculture economy (0-100).

    Based on official mechanics:
    - ELW: inherent Agriculture + strong link boosted by orbiting ELW
    - Water World: inherent Agriculture
    - Terraformable bodies: Agriculture strong link boosted
    - Bio signals: Agriculture + Terraforming added, strong link boosted
    - Tidal locking: Agriculture strong link decreased
    - Icy bodies: Agriculture strong link decreased
    - Scoopable star: bonus (supports long-term population growth)
    """
    score = 0.0

    # ELW: Agriculture + HighTech + Military + Tourism inherent
    # Strong link boosted by orbiting ELW — the gold standard for Agriculture
    score += min(counts['elw'], 4) * 20.0               # up to 4 ELWs = 80 pts

    # Water World: Agriculture + Tourism inherent
    score += min(counts['ww'], 4) * 12.0                # up to 4 WWs = 48 pts

    # Terraformable bodies: Agriculture strong link boosted
    score += min(counts['terraformable'], 5) * 5.0      # up to 5 terraformables = 25 pts

    # Bio signals: Agriculture + Terraforming added, strong link boosted
    # Diminishing returns — 10 bio signals is not 10x better than 1
    bio_score = min(counts['bio'], 15) * 2.0            # up to 15 bio signals = 30 pts
    score += bio_score

    # Tidal locking: Agriculture strong link decreased
    # This is a LINK penalty, not an economy penalty — moderate reduction
    tidal_penalty = min(counts['tidal_lock'], 5) * 3.0
    score -= tidal_penalty

    # Icy bodies: Agriculture strong link decreased
    icy_penalty = min(counts['icy'], 5) * 2.0
    score -= icy_penalty

    # Scoopable main star: supports population growth and trade routes
    if main_star_type and main_star_type[0].upper() in SCOOPABLE_STARS:
        score += 8.0

    return min(max(int(score), 0), 100)


def score_industrial(counts: dict) -> int:
    """
    Score a system for Industrial economy (0-100).

    Based on official mechanics:
    - Icy bodies: Industrial only (pure, ideal)
    - Rocky Ice: Industrial + Refinery (mixed, still good)
    - Gas Giants: HighTech + Industrial (good pairing)
    - Geo signals: Extraction + Industrial added (bonus for Industrial)
    - Pristine/Major reserves: Industrial strong link boosted (not in body data)
    """
    score = 0.0

    # Icy: pure Industrial — ideal
    score += min(counts['icy'], 6) * 14.0               # up to 6 Icy = 84 pts

    # Rocky Ice: Industrial + Refinery — good but mixed
    score += min(counts['rocky_ice'], 4) * 10.0         # up to 4 Rocky Ice = 40 pts

    # Gas Giants: HighTech + Industrial — excellent pairing
    score += min(counts['gas_giant'], 4) * 8.0          # up to 4 Gas Giants = 32 pts

    # Geo signals: Extraction + Industrial added — bonus for Industrial
    # (Extraction pairs well with Industrial)
    score += min(counts['geo'], 10) * 2.0               # up to 10 geo signals = 20 pts

    return min(int(score), 100)


def score_hightech(counts: dict) -> int:
    """
    Score a system for High Tech economy (0-100).

    Based on official mechanics:
    - ELW: Agriculture + HighTech + Military + Tourism inherent, strong link boosted
    - Ammonia World: HighTech + Tourism inherent, strong link boosted
    - Gas Giant: HighTech + Industrial inherent
    - Geo signals: HighTech strong link boosted
    - Bio signals: HighTech strong link boosted
    - Black Hole / Neutron Star / White Dwarf: HighTech + Tourism inherent
    """
    score = 0.0

    # ELW: inherent HighTech + strong link boosted
    score += min(counts['elw'], 4) * 20.0               # up to 4 ELWs = 80 pts

    # Ammonia World: inherent HighTech + strong link boosted
    score += min(counts['ammonia'], 3) * 18.0           # up to 3 Ammonia = 54 pts

    # Gas Giant: inherent HighTech + Industrial
    score += min(counts['gas_giant'], 4) * 10.0         # up to 4 Gas Giants = 40 pts

    # Black Hole / Neutron Star / White Dwarf: inherent HighTech + Tourism
    exotic_count = min(counts['neutron'] + counts['black_hole'] + counts['white_dwarf'], 2)
    score += exotic_count * 10.0

    # Geo signals: HighTech strong link boosted
    score += min(counts['geo'], 10) * 2.0

    # Bio signals: HighTech strong link boosted
    score += min(counts['bio'], 10) * 1.5

    return min(int(score), 100)


def score_military(counts: dict, main_star_type: Optional[str]) -> int:
    """
    Score a system for Military economy (0-100).

    Based on official mechanics:
    - ELW: inherent Military (among others)
    - Brown Dwarfs / other star types: Military inherent
    - Landable bodies: provide surface slots for Military Settlements
    - Gas Giants: useful for Military (orbital Military facilities)
    - Black Holes / Neutron Stars: HighTech + Tourism (not Military directly,
      but their presence boosts system prestige)
    """
    score = 0.0

    # ELW: inherent Military + Agriculture + HighTech + Tourism
    score += min(counts['elw'], 4) * 18.0               # up to 4 ELWs = 72 pts

    # Landable bodies: surface slots for Military Settlements
    # Military Settlements are critical for offsetting security drain
    score += min(counts['landable'], 10) * 5.0          # up to 10 landable = 50 pts

    # Gas Giants: useful for orbital Military facilities
    score += min(counts['gas_giant'], 3) * 6.0          # up to 3 Gas Giants = 18 pts

    # Rocky bodies: landable rocky = Military Settlement slots
    score += min(counts['rocky_clean'] + counts['rocky_rings'], 4) * 4.0

    # Exotic objects: boost system prestige (indirectly useful for Military)
    exotic = min(counts['neutron'] + counts['black_hole'], 2)
    score += exotic * 5.0

    # Non-scoopable main star (Brown Dwarf etc.) = inherent Military economy
    if main_star_type and main_star_type[0].upper() not in SCOOPABLE_STARS:
        if main_star_type[0].upper() not in ('N', 'H', 'D'):  # not neutron/BH/WD
            score += 10.0

    return min(int(score), 100)


def score_tourism(counts: dict) -> int:
    """
    Score a system for Tourism economy (0-100).

    Based on official mechanics:
    - ELW: inherent Tourism + strong link boosted
    - Water World: inherent Tourism + strong link boosted
    - Ammonia World: inherent Tourism + strong link boosted
    - Black Hole in system: Tourism strong link boosted
    - White Dwarf in system: Tourism strong link boosted
    - Neutron Star in system: Tourism strong link boosted
    - Geo signals: Tourism strong link boosted
    - Bio signals: Tourism strong link boosted
    """
    score = 0.0

    # ELW: inherent Tourism + strong link boosted by orbiting ELW
    score += min(counts['elw'], 4) * 18.0               # up to 4 ELWs = 72 pts

    # Water World: inherent Tourism + strong link boosted
    score += min(counts['ww'], 4) * 12.0                # up to 4 WWs = 48 pts

    # Ammonia World: inherent Tourism + strong link boosted
    score += min(counts['ammonia'], 3) * 14.0           # up to 3 Ammonia = 42 pts

    # Exotic objects: massive Tourism boost (unique attractions)
    # Black Hole: Tourism strong link boosted — unique attraction
    score += min(counts['black_hole'], 2) * 25.0        # up to 2 BHs = 50 pts

    # White Dwarf: Tourism strong link boosted
    score += min(counts['white_dwarf'], 2) * 12.0       # up to 2 WDs = 24 pts

    # Neutron Star: Tourism strong link boosted
    score += min(counts['neutron'], 2) * 10.0           # up to 2 Neutrons = 20 pts

    # Geo signals: Tourism strong link boosted
    score += min(counts['geo'], 10) * 1.5

    # Bio signals: Tourism strong link boosted
    score += min(counts['bio'], 10) * 1.5

    return min(int(score), 100)


def score_extraction(counts: dict) -> int:
    """
    Score a system for Extraction economy (0-100).

    Based on official mechanics:
    - HMC / Metal Rich: inherent Extraction
    - Geo signals (volcanism): Extraction strong link boosted
    - Rings: Extraction added to any body with rings
    - Pristine/Major reserves: Extraction strong link boosted (not in body data)
    """
    score = 0.0

    # HMC: pure Extraction — ideal
    score += min(counts['hmc'], 6) * 14.0               # up to 6 HMC = 84 pts

    # Metal Rich: pure Extraction
    score += min(counts['metal_rich'], 4) * 12.0        # up to 4 Metal Rich = 48 pts

    # Geo signals: Extraction strong link boosted (volcanism)
    score += min(counts['geo'], 10) * 2.5               # up to 10 geo = 25 pts

    # Rocky with rings: Extraction added
    score += min(counts['rocky_rings'], 3) * 5.0

    return min(int(score), 100)


def attenuate_economy_scores(raw_scores: dict) -> dict:
    """Apply v3.4 cross-economy attenuation to final stored scores."""
    sorted_scores = sorted(raw_scores.items(), key=lambda kv: kv[1], reverse=True)
    scores: dict[str, int] = {}
    for rank, (eco, sc) in enumerate(sorted_scores):
        if rank == 0 or rank == 1:
            scores[eco] = sc
        elif rank == 2:
            scores[eco] = int(sc * 0.85)
        else:
            scores[eco] = int(sc * 0.70)
    return {k: max(0, min(100, v)) for k, v in scores.items()}
