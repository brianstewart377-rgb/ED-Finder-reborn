#!/usr/bin/env python3
"""
ED Finder — Ratings Computer
Canonical scorer: Ratings v3.4 Best-Build Potential
Lineage baseline: v3.0 colonisation-accurate scoring rewrite

COMPLETE REWRITE of the scoring system based on official Elite Dangerous
Trailblazers Update 3 economy mechanics (April 2025) and the System
Colonisation wiki.

Key design principles:
  1. Scores reflect ACTUAL colonisation utility, not just body counts.
  2. Contamination is modelled: geo/bio signals on Rocky bodies reduce the
     Refinery score because they add competing economies (Extraction+Industrial
     or Agriculture+Terraforming) that require extra Refinery Hubs to overcome.
  3. Surface vs orbital distinction: systems with zero landable bodies are
     capped for Refinery (cannot produce CMM Composite without a surface port).
  4. The Top Two Economies rule (Aug 2025) means contamination is manageable
     with Refinery Hubs — so contaminated bodies are penalised but not zeroed.
  5. Economy scores are independent (0-100 each). The overall score reflects
     the system's best economy + slot capacity + strategic assets + safety.
  6. Display: show the searched economy score when filtering, primary economy
     score when browsing. Both are stored in the ratings table.

Score Bands (what they mean to a colonist):
  0–30  : Barely viable. Single-economy, few bodies, limited slots.
  31–50 : Functional. Good for one economy but missing key assets.
  51–65 : Solid. Good body mix, clean economies, worth considering.
  66–80 : Excellent. Multiple strong economies, good slot count, strategic assets.
  81–100: Exceptional. Near-perfect body composition, ELWs, clean stacks.

Official sources:
  - Trailblazers Update 3 patch notes (April 25, 2025)
  - System Colonisation wiki (elite-dangerous.fandom.com)

Usage:
    python3 build_ratings.py              # rate all unrated systems (resume-safe)
    python3 build_ratings.py --rebuild    # re-rate ALL systems from scratch
    python3 build_ratings.py --dirty      # only re-rate systems flagged dirty
    python3 build_ratings.py --workers 4  # set worker count (default: CPU count)
    python3 build_ratings.py --chunk 50000 # systems per worker chunk
"""

import os
import sys
import time
import logging
import argparse
import datetime
import multiprocessing as mp
from typing import Optional

import psycopg

from shared_contracts.bulk_update_helper import bulk_update_replica_mode

from progress import (
    ProgressReporter, WorkerHeartbeat,
    startup_banner, stage_banner, done_banner, crash_hint,
    fmt_num, fmt_rate, fmt_pct,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _make_direct_dsn(url: str) -> str:
    """Bypass pgBouncer — transaction-pool mode breaks long-running workers."""
    direct = os.getenv('DB_DSN_DIRECT', '')
    if direct:
        return direct
    if ':5433/' in url:
        url = url.replace(':5433/', ':5432/')
    url = url.replace('@pgbouncer:', '@postgres:')
    return url


def _connect_with_retry(dsn: str, label: str = 'ratings', retries: int = 10,
                        delay: float = 5.0):
    """Connect with exponential back-off retries."""
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg.connect(
                dsn,
                keepalives=1,
                keepalives_idle=60,
                keepalives_interval=10,
                keepalives_count=6,
                options=(
                    f"-c application_name={label} "
                    f"-c statement_timeout=0 "
                    f"-c lock_timeout=0 "
                    f"-c idle_in_transaction_session_timeout=3600000"
                )
            )
            return conn
        except Exception as e:
            if attempt == retries:
                log.error(f"FATAL: Cannot connect to database ({label}): {e}")
                raise
            wait = min(delay * attempt, 60)
            log.warning(f"  DB connect failed ({label}, attempt {attempt}/{retries}): {e}")
            log.warning(f"  Retrying in {wait:.0f}s ...")
            time.sleep(wait)


_raw_url   = os.environ['DATABASE_URL']  # fail-fast: no insecure 'edfinder:edfinder' fallback
DB_DSN     = _make_direct_dsn(_raw_url)
BATCH_SIZE = int(os.getenv('BATCH_SIZE', '5000'))
LOG_LEVEL  = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE   = os.getenv('LOG_FILE', '/tmp/build_ratings.log')

os.makedirs(os.path.dirname(os.path.abspath(LOG_FILE)), exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('build_ratings')

RATING_VERSION = '3.4'

RATING_INSERT_COLUMNS = (
    'system_id64', 'score',
    'score_agriculture', 'score_refinery', 'score_industrial',
    'score_hightech', 'score_military', 'score_tourism',
    'economy_suggestion',
    'elw_count', 'ww_count', 'ammonia_count', 'gas_giant_count',
    'ring_count',
    'rocky_count', 'metal_rich_count', 'icy_count', 'rocky_ice_count',
    'hmc_count', 'landable_count', 'terraformable_count',
    'bio_signal_total', 'geo_signal_total',
    'neutron_count', 'black_hole_count', 'white_dwarf_count',
    'slots', 'body_quality', 'compactness', 'signal_quality',
    'orbital_safety', 'star_bonus',
    'score_extraction', 'terraforming_potential', 'body_diversity',
    'confidence', 'rationale',
    'rating_version', 'updated_at',
)

RATING_VALUES_TEMPLATE = (
    "(%s,%s,%s,%s,%s,%s,%s,%s,"
    "%s::economy_type,"
    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
    "%s,%s,%s,%s,%s,%s,"
    "%s,%s,%s,%s,%s,"
    "%s,%s)"
)

RATING_CONFLICT_UPDATE_COLUMNS = tuple(
    c for c in RATING_INSERT_COLUMNS
    if c not in {'system_id64', 'updated_at'}
)
RATING_CONFLICT_SET_SQL = ",\n            ".join(
    f"{c:<22} = EXCLUDED.{c}" for c in RATING_CONFLICT_UPDATE_COLUMNS
) + ",\n            updated_at              = NOW()"

DIRTY_CLEANUP_CHUNK_SIZE = int(os.getenv(
    'RATING_DIRTY_CLEANUP_CHUNK',
    os.getenv('DIRTY_CLEANUP_CHUNK_SIZE', '1000'),
))
DIRTY_CLEANUP_RETRIES = int(os.getenv('RATING_DIRTY_CLEANUP_RETRIES', '3'))


def _rating_template_placeholder_count(template: str = RATING_VALUES_TEMPLATE) -> int:
    return template.count('%s')


assert len(RATING_INSERT_COLUMNS) == _rating_template_placeholder_count(), (
    "ratings INSERT shape drifted: columns and values template differ"
)
assert 'rating_version' in RATING_INSERT_COLUMNS
assert 'rating_version' in RATING_CONFLICT_UPDATE_COLUMNS

# ---------------------------------------------------------------------------
# Body classification
# ---------------------------------------------------------------------------
# These are the OFFICIAL body-type-to-economy overrides from Trailblazers
# Update 3 (April 2025). The scoring engine uses these to determine how
# each body contributes to (or contaminates) each economy.

SCOOPABLE_STARS = {'O', 'B', 'A', 'F', 'G', 'K', 'M'}


# ---------------------------------------------------------------------------
# v3.1 — distance-from-arrival-star awareness
# ---------------------------------------------------------------------------
# In-game, bodies >100k Ls from the arrival star are essentially unusable for
# CMM colonial ports (pad access requires sub-cruise in reasonable time), and
# bodies at 200-500 Ls are the sweet spot.  We decay a body's contribution
# smoothly from 1.0 (at the arrival star) toward 0.3 (beyond 100k Ls).
#
#     distance_from_star (Ls)        weight
#     ────────────────────────────  ─────────
#     None (unknown)                   1.00   (don't penalise missing data)
#     0 - 1,000 Ls (ideal)             1.00
#     1,000 - 10,000 Ls                0.85
#     10,000 - 100,000 Ls              0.60
#     > 100,000 Ls                     0.30
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Economy scoring functions
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Overall score and system profile
# ---------------------------------------------------------------------------

def compute_slot_score(counts: dict) -> int:
    """
    Score the system's slot capacity (0-100).

    A good colonisation system needs BOTH surface and orbital slots.
    The score rewards a healthy mix.
    """
    landable  = counts['landable']
    orbital   = (counts['gas_giant'] + counts['elw'] + counts['ww'] +
                 counts['ammonia'] + counts['rocky_clean'] + counts['rocky_rings'] +
                 counts['rocky_bio'] + counts['rocky_geo'] + counts['rocky_ice'] +
                 counts['icy'] + counts['hmc'] + counts['metal_rich'])

    # Surface slot score (landable bodies)
    if landable == 0:
        surface_score = 0
    elif landable <= 2:
        surface_score = 25
    elif landable <= 5:
        surface_score = 50
    elif landable <= 10:
        surface_score = 75
    else:
        surface_score = 100

    # Orbital slot score (total bodies)
    if orbital <= 2:
        orbital_score = 20
    elif orbital <= 5:
        orbital_score = 50
    elif orbital <= 10:
        orbital_score = 75
    else:
        orbital_score = 100

    # Bonus for having BOTH surface and orbital (the ideal mix)
    mix_bonus = 15 if (landable > 0 and orbital > 2) else 0

    return min(int((surface_score * 0.5 + orbital_score * 0.35) + mix_bonus), 100)


def compute_strategic_score(counts: dict, main_star_type: Optional[str]) -> int:
    """
    Score the system's strategic assets (0-100).

    Strategic assets are things that add long-term value regardless of economy:
    ELWs, terraformables, scoopable star, bio signals, exotic objects.
    """
    score = 0.0

    # ELW: the most valuable strategic asset in the game
    score += min(counts['elw'], 3) * 25.0               # up to 3 ELWs = 75 pts

    # Terraformable bodies: future population growth potential
    score += min(counts['terraformable'], 5) * 6.0      # up to 5 = 30 pts

    # Scoopable main star: enables fuel scooping, supports trade routes
    if main_star_type and main_star_type[0].upper() in SCOOPABLE_STARS:
        score += 15.0

    # Bio signals: unique biological content (Tourism + Agriculture bonus)
    score += min(counts['bio'], 10) * 1.5               # up to 10 = 15 pts

    # Exotic objects: unique attractions (Tourism + HighTech)
    exotic = counts['neutron'] + counts['black_hole'] + counts['white_dwarf']
    score += min(exotic, 3) * 5.0                       # up to 3 = 15 pts

    # Water Worlds: Agriculture + Tourism, aesthetically valuable
    score += min(counts['ww'], 3) * 5.0

    return min(int(score), 100)


def compute_safety_score(counts: dict, main_star_type: Optional[str]) -> int:
    """
    Score the system's safety for colonisation (0-100).

    Starts at 100, penalised for hazardous objects near the main star.
    Note: Black Holes and Neutron Stars are actually GOOD for Tourism/HighTech,
    so we only penalise White Dwarfs (exclusion zone hazard with no upside).
    """
    score = 100

    # White Dwarfs: exclusion zone hazard, no economy benefit
    score -= min(counts['white_dwarf'], 2) * 15

    # Neutron Stars: dangerous but boost Tourism/HighTech — minor penalty
    score -= min(counts['neutron'], 2) * 5

    # Black Holes: dangerous but boost Tourism/HighTech — minor penalty
    score -= min(counts['black_hole'], 2) * 5

    return max(score, 0)


# ---------------------------------------------------------------------------
# v3.1 — Terraforming potential, body diversity, rationale generator
# ---------------------------------------------------------------------------

def compute_terraforming_potential(counts: dict, main_star_type: Optional[str]) -> int:
    """
    Score terraforming potential (0-100) — distinct from raw count.

    Old rating used `min(terraformable, 5) * 6` which treats every
    terraformable body identically.  Reality: a terraformable HMC at 200 Ls
    orbiting a G-class star is the holy grail; a terraformable Gas Giant is
    game-impossible.  `tf_quality_acc` is already weighted by body type and
    distance; we just need to normalise and apply a main-star bonus.
    """
    # Normalise the accumulator: 5 well-placed, well-typed bodies ≈ 60 pts.
    base = min(counts['tf_quality_acc'] * 12, 75)

    # Habitable-zone star bonus: G/K/M > F/A ≫ O/B (bluer stars make HZ narrow).
    if main_star_type:
        st = main_star_type[0].upper()
        if st in ('G', 'K'):
            base += 15                 # sweet spot
        elif st in ('F', 'M'):
            base += 10
        elif st in ('A',):
            base += 5

    return int(min(base, 100))


def compute_body_diversity(counts: dict) -> int:
    """
    Shannon-diversity bonus (0-30) rewarding systems with a varied mix of
    body types.  A system with (ELW + WW + Ring + HMC + GG) scores higher
    than one with (12 HMCs) — diversity supports multiple economies and
    future colonisation paths.
    """
    buckets = counts.get('type_bucket_counts', {})
    total = sum(buckets.values())
    if total == 0:
        return 0
    # Shannon entropy H = -Σ(p * log2(p)), max ≈ log2(N_buckets).
    import math
    H = -sum(
        (c / total) * math.log2(c / total)
        for c in buckets.values() if c > 0
    )
    # Normalise: max entropy for this many buckets.
    n_buckets = len(buckets)
    if n_buckets <= 1:
        return 0
    H_max = math.log2(n_buckets)
    return int((H / H_max) * 30)


def generate_rationale(counts: dict, scores: dict, primary_eco: str,
                       tf_score: int, diversity: int,
                       main_star_type: Optional[str]) -> str:
    """
    Produce a one-line explainer (<=160 chars) describing why this system
    scored as it did.  Designed to be surfaced in the UI directly under the
    star rating so CMDRs don't have to parse the breakdown dict.

    Rationale text is deliberately structured:
      Primary score: ...
      Factors: ...
      Caveat: ...

    Avoid old shorthand phrasing that makes mixed bodies such as ELWs read
    like pure economy sources. ELWs can contribute to Agriculture, HighTech,
    Military, and Tourism potential, but the mixed-economy caveat must stay
    visible.
    """
    parts = []
    primary_score = scores[primary_eco]

    # Lead phrase keyed to primary economy.
    if primary_score >= 60:
        lead = f"Strong {primary_eco}"
    elif primary_score >= 40:
        lead = f"Moderate {primary_eco}"
    elif primary_score >= 20:
        lead = f"{primary_eco}-leaning"
    else:
        lead = "Low-yield system"
    parts.append(f"Primary score: {lead} ({int(primary_score)})")

    # Highlights — only mention bodies that actually contribute to the
    # primary economy's score function. Each helper looks at `counts` and
    # returns a list of human-readable phrases in priority order. We take
    # the first three.
    builder = ECON_HIGHLIGHT_BUILDERS.get(primary_eco, _highlights_generic)
    highlights = builder(counts)
    if primary_eco == 'Military' and main_star_type:
        highlights.insert(0, f"{main_star_type} star inheritance")
    if primary_eco == 'Industrial' and not highlights:
        highlights.append("industrial support facility potential")
    if highlights:
        parts.append("Factors: " + ", ".join(highlights[:3]))

    # Slot note — universal across economies.
    caveats = []
    if counts.get('elw') and primary_eco in {'Agriculture', 'HighTech', 'Military', 'Tourism'}:
        caveats.append("ELW mixed: Agri/HT/Mil/Tourism")
    if primary_eco == 'Industrial' and counts.get('elw'):
        caveats.append("ELW is not an Industrial driver")
    if counts.get('landable', 0) == 0:
        caveats.append("no surface slots")
    if counts.get('white_dwarf'):
        caveats.append("white-dwarf hazard")
    if caveats:
        parts.append("Caveat: " + "; ".join(caveats[:2]))

    # Hazard / diversity tail — universal.
    context = []
    if diversity >= 20:
        context.append("varied body mix")
    if tf_score >= 70:
        context.append("high terraforming potential")
    if counts.get('landable', 0) >= 5:
        context.insert(0, f"{counts['landable']} landable")
    if context:
        parts.append("Context: " + ", ".join(context[:2]))

    return ("; ".join(parts))[:160]


# ---------------------------------------------------------------------------
# Per-economy highlight builders — invoked by generate_rationale().
#
# Each returns a list of "{N} {kind}" phrases for bodies that actually
# contribute to the corresponding score_<economy> function above. Mirror
# any change you make to the score_* functions here, otherwise the UI
# rationale will drift back into describing bodies that didn't score.
# ---------------------------------------------------------------------------
def _h_refinery(c: dict) -> list:
    out = []
    if c.get('rocky_clean'):  out.append(f"{c['rocky_clean']} clean rocky")
    if c.get('rocky_rings'):  out.append(f"{c['rocky_rings']} rocky-ringed")
    if c.get('hmc'):          out.append(f"{c['hmc']} HMC")
    if c.get('rocky_ice'):    out.append(f"{c['rocky_ice']} rocky-ice")
    return out


def _h_industrial(c: dict) -> list:
    out = []
    if c.get('icy'):          out.append(f"{c['icy']} icy")
    if c.get('rocky_ice'):    out.append(f"{c['rocky_ice']} rocky-ice")
    if c.get('gas_giant'):    out.append(f"{c['gas_giant']} gas giant")
    if c.get('geo'):          out.append(f"{c['geo']} geo signal")
    return out


def _h_hightech(c: dict) -> list:
    out = []
    if c.get('elw'):          out.append(f"{c['elw']} ELW mixed")
    if c.get('ammonia'):      out.append(f"{c['ammonia']} AW")
    if c.get('gas_giant'):    out.append(f"{c['gas_giant']} gas giant")
    exotic = c.get('black_hole', 0) + c.get('neutron', 0) + c.get('white_dwarf', 0)
    if exotic:                 out.append(f"{exotic} exotic star")
    if c.get('geo'):          out.append(f"{c['geo']} geo signal")
    if c.get('bio'):          out.append(f"{c['bio']} bio signal")
    return out


def _h_military(c: dict) -> list:
    out = []
    if c.get('elw'):          out.append(f"{c['elw']} ELW mixed")
    if c.get('landable'):     out.append(f"{c['landable']} landable support")
    if c.get('gas_giant'):    out.append(f"{c['gas_giant']} GG inheritance")
    if c.get('rocky_clean') or c.get('rocky_rings'):
        rocky = c.get('rocky_clean', 0) + c.get('rocky_rings', 0)
        out.append(f"{rocky} rocky surface")
    exotic = c.get('black_hole', 0) + c.get('neutron', 0)
    if exotic:                 out.append(f"{exotic} exotic star")
    return out


def _h_tourism(c: dict) -> list:
    out = []
    if c.get('elw'):          out.append(f"{c['elw']} ELW mixed")
    if c.get('ww'):           out.append(f"{c['ww']} WW")
    if c.get('ammonia'):      out.append(f"{c['ammonia']} AW")
    if c.get('black_hole'):   out.append(f"{c['black_hole']} black hole")
    if c.get('neutron'):      out.append(f"{c['neutron']} neutron")
    if c.get('white_dwarf'):  out.append(f"{c['white_dwarf']} white dwarf")
    return out


def _h_agriculture(c: dict) -> list:
    out = []
    if c.get('elw'):           out.append(f"{c['elw']} ELW mixed")
    if c.get('ww'):            out.append(f"{c['ww']} WW")
    if c.get('terraformable'): out.append(f"{c['terraformable']} terraformable")
    if c.get('bio'):           out.append(f"{c['bio']} bio signal")
    return out


def _h_extraction(c: dict) -> list:
    out = []
    if c.get('hmc'):          out.append(f"{c['hmc']} HMC")
    if c.get('metal_rich'):   out.append(f"{c['metal_rich']} metal-rich")
    if c.get('rocky_rings'):  out.append(f"{c['rocky_rings']} rocky-ringed")
    if c.get('geo'):          out.append(f"{c['geo']} geo signal")
    return out


def _highlights_generic(c: dict) -> list:
    """Defensive fallback for an unknown primary_eco. Lists everything that's
    universally noteworthy, conservatively. Should never be called in
    practice — kept only so a future new economy doesn't crash the rationale
    generator before its builder is wired in."""
    out = []
    if c.get('elw'):          out.append(f"{c['elw']} ELW")
    if c.get('ww'):           out.append(f"{c['ww']} WW")
    if c.get('terraformable'):out.append(f"{c['terraformable']} terraformable")
    return out


ECON_HIGHLIGHT_BUILDERS = {
    'Refinery':    _h_refinery,
    'Industrial':  _h_industrial,
    'HighTech':    _h_hightech,
    'Military':    _h_military,
    'Tourism':     _h_tourism,
    'Agriculture': _h_agriculture,
    'Extraction':  _h_extraction,
}


def compute_confidence(last_updated, report_count: int = 1) -> float:
    """
    Confidence score in [0.70, 1.00] based on data freshness.

    Scans from 2015 Spansh dumps are less trustworthy than a 2026 EDDN
    event with multiple independent reports.  `last_updated` is expected
    to be a datetime or ISO string; `report_count` is the number of
    independent reporters (if tracked).

    We cap the floor at 0.70 so stale-but-still-useful systems don't
    vanish from search results — this is a soft signal, not a filter.
    """
    if last_updated is None:
        return 0.85
    try:
        from datetime import datetime, timezone
        if isinstance(last_updated, str):
            # Handle ISO 8601 with 'Z' suffix
            ts = datetime.fromisoformat(last_updated.replace('Z', '+00:00'))
        elif isinstance(last_updated, datetime):
            ts = last_updated
        else:
            return 0.85
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - ts).days
    except (TypeError, ValueError, AttributeError):
        return 0.85

    # 0 days   → 1.00
    # 365 days → 0.95
    # 3 years  → 0.80
    # 5+ years → 0.70
    if age_days <= 30:
        conf = 1.00
    elif age_days <= 365:
        conf = 1.00 - (age_days - 30) * (0.05 / 335)
    elif age_days <= 3 * 365:
        conf = 0.95 - (age_days - 365) * (0.15 / (2 * 365))
    else:
        conf = max(0.80 - (age_days - 3 * 365) * (0.10 / (2 * 365)), 0.70)

    # Multiple independent reports modestly restore confidence.
    if report_count >= 3:
        conf = min(conf + 0.05, 1.00)

    return round(conf, 3)


def rate_system(system_id64: int, bodies: list, main_star_type: Optional[str],
                last_updated=None, report_count: int = 1) -> dict:
    """
    Compute the full colonisation rating for a system (v3.1).

    Returns a dict matching the ratings table schema, plus v3.1 fields:
        terraforming_potential, body_diversity, confidence, rationale.

    The overall score is NOT a simple average of economy scores.
    It reflects: "If I build the best possible economy here, how good is
    the system overall?" — weighted by slot capacity, strategic assets, safety.

    Economy scores are stored independently so the frontend can show the
    searched economy score when filtering, or the primary economy score
    when browsing without a filter.

    v3.1 notes:
      - Extraction is now primary-eligible (v3.0 excluded it, so pure-mining
        systems scored mediocre overall).
      - distance-from-arrival weighting is already baked into classify_bodies.
      - last_updated (datetime or ISO str) and report_count drive the
        confidence field. Both are optional; defaults preserve old behaviour.
    """
    counts = classify_bodies(bodies)

    # ── Compute all economy scores ─────────────────────────────────────────
    raw_scores = {
        'Agriculture': score_agriculture(counts, main_star_type),
        'Refinery':    score_refinery(counts),
        'Industrial':  score_industrial(counts),
        'HighTech':    score_hightech(counts),
        'Military':    score_military(counts, main_star_type),
        'Tourism':     score_tourism(counts),
        'Extraction':  score_extraction(counts),
    }

    # ── v3.4: Cross-economy attenuation ─────────────────────────────────────
    # A system cannot simultaneously be exceptional at 5+ economies.
    # The top two economies keep full score; the 3rd is reduced 15%,
    # 4th+ reduced 30%. This preserves discriminative power and prevents
    # the "everything is 100" saturation problem.
    scores = attenuate_economy_scores(raw_scores)

    # ── Compute dimensional scores ─────────────────────────────────────────
    slot_score      = compute_slot_score(counts)
    strategic_score = compute_strategic_score(counts, main_star_type)
    safety_score    = compute_safety_score(counts, main_star_type)

    # ── v3.1: terraforming potential + body diversity ──────────────────────
    tf_potential = compute_terraforming_potential(counts, main_star_type)
    diversity    = compute_body_diversity(counts)

    # ── Determine primary and secondary economy ────────────────────────────
    # v3.1: Extraction is now included symmetrically, so a pure mining system
    # can surface as "Primary: Extraction" instead of being forced into some
    # runner-up economy it doesn't actually fit.
    #
    # v3.3 (2026-05-10): when two or more economies tie at the top, the
    # previous code picked whichever one came first in `scores.items()` —
    # i.e. dict insertion order, which is silent and arbitrary. For
    # HD 49188 (id64 167244365) this caused Refinery to be displayed as
    # primary even though Military scored exactly the same and the
    # complementary-pair card showed an Industrial+Military top pairing.
    # We now break ties using the COMPLEMENTARY_PAIRS computation (see
    # below): when there's a tie, prefer the economy that's part of the
    # highest-scoring complementary pair, because that's the one the
    # rest of the UI is already recommending. If neither tied economy is
    # in `best_a`/`best_b`, fall through to dict-order — non-arbitrary
    # cases never reach that branch in practice.
    sorted_ecos = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    # Compute the complementary-pair winner FIRST so it's available as a
    # tiebreak signal for primary_eco selection.
    COMPLEMENTARY_PAIRS = [
        ('Extraction',  'Refinery'),
        ('Refinery',    'Industrial'),
        ('Industrial',  'HighTech'),
        ('HighTech',    'Tourism'),
        ('Agriculture', 'Tourism'),
        ('HighTech',    'Military'),
        ('Industrial',  'Military'),
        ('Agriculture', 'HighTech'),
    ]
    pair_scores = [(a, b, (scores[a] + scores[b]) / 2)
                   for a, b in COMPLEMENTARY_PAIRS]
    best_a, best_b, best_pair = max(pair_scores, key=lambda t: t[2])

    # If multiple economies tie at the top score, pick the one that
    # appears in the winning complementary pair. This makes "Suggested
    # economy" line up with the pair-card the UI is already showing,
    # instead of being dict-insertion-order roulette.
    top_score = sorted_ecos[0][1]
    tied_top  = [eco for eco, sc in sorted_ecos if sc == top_score]
    if len(tied_top) >= 2:
        if best_a in tied_top:
            primary_eco = best_a
        elif best_b in tied_top:
            primary_eco = best_b
        else:
            primary_eco = tied_top[0]   # fall through — deterministic
    else:
        primary_eco = sorted_ecos[0][0]
    primary_score = top_score

    economy_suggestion = primary_eco if primary_score >= 20 else None

    # ── Overall score: v3.2 — even-handed, pair-aware, rarity-gated ─────
    # v3.1 weighted only the BEST single economy at 42% and added a +10
    # safety baseline. v3.2 uses complementary economy pairs (Trailblazers
    # Update 3) + top-3 average + a rarity gate at 85+. (Pair computation
    # was moved up to feed the primary_eco tiebreak above.)
    top3_avg = sum(sorted([s for s in scores.values()], reverse=True)[:3]) / 3
    strategic_bonus = (tf_potential / 100.0) * 4 + (diversity / 100.0) * 3
    raw_overall = best_pair * 0.60 + top3_avg * 0.35 + strategic_bonus

    has_standout = (
        counts['elw']           >= 1 or
        counts['ammonia']       >= 1 or
        counts['black_hole']    >= 1 or
        counts['neutron']       >= 1 or
        counts['ww']            >= 2 or
        counts['terraformable'] >= 5
    )
    if not has_standout and raw_overall > 84:
        raw_overall = 84.0

    overall = int(min(raw_overall, 100))

    # ── Rationale (one-line explainer) ─────────────────────────────────────
    rationale = generate_rationale(counts, scores, primary_eco,
                                   tf_potential, diversity, main_star_type)

    # ── Confidence (data freshness) ────────────────────────────────────────
    confidence = compute_confidence(last_updated, report_count)

    # ── Score components (for API popover display) ─────────────────────────
    # These map to the score_components field in the API response
    star_bonus = 0
    if main_star_type:
        st = main_star_type[0].upper()
        if st in ('O', 'B'):
            star_bonus = 10
        elif st in ('A', 'F'):
            star_bonus = 7
        elif st in ('G', 'K'):
            star_bonus = 5
        elif st == 'M':
            star_bonus = 3
        elif st == 'N':
            star_bonus = 4
        elif st == 'H':
            star_bonus = 2

    return {
        'system_id64':        system_id64,
        'score':              overall,
        'score_agriculture':  scores['Agriculture'],
        'score_refinery':     scores['Refinery'],
        'score_industrial':   scores['Industrial'],
        'score_hightech':     scores['HighTech'],
        'score_military':     scores['Military'],
        'score_tourism':      scores['Tourism'],
        'economy_suggestion': economy_suggestion,
        # Body counts (for frontend filters)
        'elw_count':          counts['elw'],
        'ww_count':           counts['ww'],
        'ammonia_count':      counts['ammonia'],
        'gas_giant_count':    counts['gas_giant'],
        'ring_count':         counts['ring_count'],
        'rocky_count':        counts['rocky'],
        'metal_rich_count':   counts['metal_rich'],
        'icy_count':          counts['icy'],
        'rocky_ice_count':    counts['rocky_ice'],
        'hmc_count':          counts['hmc'],
        'landable_count':     counts['landable'],
        'terraformable_count': counts['terraformable'],
        'bio_signal_total':   counts['bio'],
        'geo_signal_total':   counts['geo'],
        'neutron_count':      counts['neutron'],
        'black_hole_count':   counts['black_hole'],
        'white_dwarf_count':  counts['white_dwarf'],
        # Score components (for API score_components field)
        'slots':              slot_score,
        'body_quality':       strategic_score,
        'compactness':        min(int(counts['landable'] / max(counts['landable'] + counts['gas_giant'] + 1, 1) * 100), 100),
        'signal_quality':     min(int((min(counts['bio'], 10) * 5 + min(counts['geo'], 5) * 4)), 100),
        'orbital_safety':     safety_score,
        'star_bonus':         star_bonus,
        # v3.1 fields — stored as separate columns so the rerank endpoint
        # can weight them without recomputing.
        'score_extraction':       scores['Extraction'],
        'terraforming_potential': tf_potential,
        'body_diversity':         diversity,
        'confidence':             confidence,
        'rationale':              rationale,
        'rating_version':         RATING_VERSION,
    }


# ---------------------------------------------------------------------------
# Worker function (runs in separate process)
# ---------------------------------------------------------------------------

def _chunks(items: list, chunk_size: int):
    for start in range(0, len(items), max(1, chunk_size)):
        yield items[start:start + chunk_size]


def _clean_ids_for_batch(id64s: list, failed_ids: set) -> list:
    return [i for i in id64s if i not in failed_ids]


def _is_transient_dirty_cleanup_error(exc: Exception) -> bool:
    if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
        return True
    query_canceled = getattr(psycopg.errors, 'QueryCanceled', None)
    if query_canceled is not None and isinstance(exc, query_canceled):
        return True
    msg = str(exc).lower()
    return any(token in msg for token in (
        'statement timeout',
        'canceling statement due to statement timeout',
        'server closed the connection',
        'connection already closed',
        'could not receive data from server',
    ))


def _mark_ratings_clean(conn, cur, clean_ids: list, worker_id: int,
                        chunk_size: int = DIRTY_CLEANUP_CHUNK_SIZE,
                        retries: int = DIRTY_CLEANUP_RETRIES,
                        retry_delay: float = 0.5) -> tuple[int, int]:
    """Clear systems.rating_dirty after ratings writes have committed.

    Returns (rows_marked_clean, ids_left_dirty). Failures here intentionally do
    not roll back successful rating upserts; dirty rows can be retried next run.
    """
    if not clean_ids:
        return 0, 0

    marked = 0
    left_dirty = 0

    for chunk in _chunks(clean_ids, chunk_size):
        for attempt in range(1, retries + 1):
            try:
                # Re-enter for each retry because a rollback also rolls back
                # an uncommitted SET from a failed first attempt.
                with bulk_update_replica_mode(conn):
                    cur.execute("SET LOCAL statement_timeout = 0")
                    cur.execute("SET LOCAL lock_timeout = 0")
                    cur.execute("SET LOCAL idle_in_transaction_session_timeout = 3600000")
                    cur.execute("""
                        UPDATE systems s
                           SET rating_dirty = FALSE
                          FROM (SELECT unnest(%s::bigint[]) AS id64) clean
                         WHERE s.id64 = clean.id64
                           AND s.rating_dirty = TRUE
                    """, (chunk,))
                    marked += max(cur.rowcount, 0)
                    conn.commit()
                break
            except Exception as e:
                if attempt < retries and _is_transient_dirty_cleanup_error(e):
                    wait = retry_delay * attempt
                    log.warning(
                        f"Worker {worker_id}: dirty cleanup chunk "
                        f"{len(chunk)} failed transiently "
                        f"(attempt {attempt}/{retries}): {e}; retrying in {wait:.1f}s"
                    )
                    time.sleep(wait)
                    continue
                left_dirty += len(chunk)
                log.error(
                    f"Worker {worker_id}: dirty cleanup chunk failed; "
                    f"{len(chunk)} systems remain dirty: {e}"
                )
                break

    log.info(
        f"Worker {worker_id}: marked {fmt_num(marked)} systems clean "
        f"in chunks of {fmt_num(chunk_size)}; {fmt_num(left_dirty)} left dirty"
    )
    return marked, left_dirty


def worker_process(worker_id: int, system_batch: list, db_dsn: str) -> tuple:
    """
    Process a chunk of systems.
    v3.0: Uses new classify_bodies() which fetches is_tidal_lock and trusted
    ring evidence in addition to the existing body fields.
    """
    conn = _connect_with_retry(db_dsn, label=f'ratings-worker-{worker_id}')
    conn.autocommit = False
    cur  = conn.cursor()

    processed    = 0
    errors       = 0
    rating_batch = []
    failed_ids: set = set()

    hb = WorkerHeartbeat(worker_id, total=len(system_batch),
                         label="ratings", interval=60.0)

    # ── Batch-fetch all bodies for this chunk ─────────────────────────────
    # v3.0: fetch is_tidal_lock and trusted ring evidence for contamination scoring
    id64s = [s[0] for s in system_batch]
    bodies_by_system: dict = {}

    BODY_CHUNK = 5000
    for start in range(0, len(id64s), BODY_CHUNK):
        slice_ids = id64s[start: start + BODY_CHUNK]
        try:
            cur.execute("""
                SELECT system_id64,
                       subtype,
                       is_earth_like, is_water_world, is_ammonia_world,
                       is_landable, is_terraformable, is_tidal_lock,
                       bio_signal_count, geo_signal_count,
                       distance_from_star,
                       EXISTS (
                         SELECT 1
                         FROM body_rings br
                         WHERE br.system_id64 = bodies.system_id64
                           AND br.body_id = bodies.id
                           AND br.association_status = 'local_matched'
                       ) AS has_rings
                FROM   bodies
                WHERE  system_id64 = ANY(%s)
            """, (slice_ids,))
            for row in cur:
                sid = row[0]
                bodies_by_system.setdefault(sid, []).append({
                    'subtype':          row[1],
                    'is_earth_like':    row[2],
                    'is_water_world':   row[3],
                    'is_ammonia_world': row[4],
                    'is_landable':      row[5],
                    'is_terraformable': row[6],
                    'is_tidal_lock':    row[7],
                    'bio_signal_count': row[8],
                    'geo_signal_count': row[9],
                    'distance_from_star': row[10],
                    'has_rings':          row[11],
                })
        except Exception as e:
            log.error(f"Worker {worker_id}: body fetch error (offset {start}): {e}")
            # Reset aborted transaction so subsequent queries on this conn can proceed.
            try: conn.rollback()
            except Exception: pass

    # ── Fetch systems.updated_at so confidence can reflect data freshness
    last_updated_by_system: dict = {}
    try:
        cur.execute(
            "SELECT id64, updated_at FROM systems WHERE id64 = ANY(%s)",
            (id64s,)
        )
        for sid, ts in cur:
            last_updated_by_system[sid] = ts
    except Exception as e:
        log.debug(f"Worker {worker_id}: updated_at fetch skipped ({e})")
        # Reset aborted transaction so the ratings INSERT below isn't poisoned.
        try: conn.rollback()
        except Exception: pass

    # ── Compute ratings ────────────────────────────────────────────────────
    for system_id64, main_star_type in system_batch:
        try:
            bodies = bodies_by_system.get(system_id64, [])
            last_updated = last_updated_by_system.get(system_id64)
            if not bodies:
                errors += 1
                failed_ids.add(system_id64)
                log.warning(
                    "Worker %s: no bodies fetched for %s; leaving rating_dirty set for retry",
                    worker_id,
                    system_id64,
                )
                continue
            rating = rate_system(system_id64, bodies, main_star_type,
                                 last_updated=last_updated)
            rating_batch.append(rating)
            processed += 1
        except Exception as e:
            errors += 1
            failed_ids.add(system_id64)
            log.debug(f"Worker {worker_id}: rating error for {system_id64}: {e}")
            continue

        hb.tick(processed, errors)

        if len(rating_batch) >= BATCH_SIZE:
            try:
                _write_ratings(conn, cur, rating_batch)
            except Exception as e:
                log.error(f"Worker {worker_id}: write error: {e}")
                conn.rollback()
                for r in rating_batch:
                    failed_ids.add(r['system_id64'])
            rating_batch.clear()

    # Final flush
    if rating_batch:
        try:
            _write_ratings(conn, cur, rating_batch)
        except Exception as e:
            log.error(f"Worker {worker_id}: final write error: {e}")
            conn.rollback()
            for r in rating_batch:
                failed_ids.add(r['system_id64'])

    # Mark systems clean — disable RI triggers to avoid 17-trigger overhead
    clean_ids = _clean_ids_for_batch(id64s, failed_ids)
    if failed_ids:
        log.warning(
            f"Worker {worker_id}: {len(failed_ids)} systems kept dirty "
            f"(write failed) — will retry next run"
        )
    if clean_ids:
        marked, left_dirty = _mark_ratings_clean(conn, cur, clean_ids, worker_id)
        if left_dirty:
            log.warning(
                f"Worker {worker_id}: rating writes committed, but "
                f"{fmt_num(left_dirty)} dirty flags remain uncleared"
            )

    cur.close()
    conn.close()
    return processed, errors


def _rating_row_tuple(r: dict, now_iso: str) -> tuple:
    return (
        r['system_id64'],
        r['score'],
        r['score_agriculture'], r['score_refinery'],
        r['score_industrial'],  r['score_hightech'],
        r['score_military'],    r['score_tourism'],
        r['economy_suggestion'],
        r['elw_count'],         r['ww_count'],
        r['ammonia_count'],     r['gas_giant_count'],
        r['ring_count'],
        r['rocky_count'],       r['metal_rich_count'],
        r['icy_count'],         r['rocky_ice_count'],
        r['hmc_count'],         r['landable_count'],
        r['terraformable_count'],
        r['bio_signal_total'],  r['geo_signal_total'],
        r['neutron_count'],     r['black_hole_count'],
        r['white_dwarf_count'],
        r.get('slots'),
        r.get('body_quality'),
        r.get('compactness'),
        r.get('signal_quality'),
        r.get('orbital_safety'),
        r.get('star_bonus'),
        # v3.1 columns
        r.get('score_extraction'),
        r.get('terraforming_potential'),
        r.get('body_diversity'),
        r.get('confidence'),
        r.get('rationale'),
        r.get('rating_version', RATING_VERSION),
        now_iso,
    )


def _ratings_insert_sql() -> str:
    columns_sql = ",\n            ".join(RATING_INSERT_COLUMNS)
    return f"""
        INSERT INTO ratings (
            {columns_sql}
        ) VALUES {RATING_VALUES_TEMPLATE}
        ON CONFLICT (system_id64) DO UPDATE SET
            {RATING_CONFLICT_SET_SQL}
        """


def _write_ratings(conn, cur, batch: list) -> None:
    # Replica mode is intentionally not used for this mixed INSERT/UPDATE:
    # new ratings rows must retain their FK check against systems. The costly
    # parent-table trigger fan-out applies to UPDATE systems, whose cleanup is
    # separately protected by bulk_update_replica_mode above.
    """Upsert a batch of rating records — single commit per call."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rows = [_rating_row_tuple(r, now_iso) for r in batch]
    expected_len = len(RATING_INSERT_COLUMNS)
    assert all(len(row) == expected_len for row in rows), (
        "ratings INSERT shape drifted: row tuple and column list differ"
    )

    cur.executemany(_ratings_insert_sql(), rows)
    conn.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Build pre-computed colonisation ratings (Ratings v3.4 Best-Build Potential)'
    )
    parser.add_argument('--rebuild',     action='store_true',
                        help='Re-rate ALL systems, not just unrated ones')
    parser.add_argument('--dirty',       action='store_true',
                        help='Only re-rate systems with rating_dirty = TRUE')
    parser.add_argument('--workers',     type=int, default=mp.cpu_count(),
                        help='Parallel worker processes (default: CPU count)')
    parser.add_argument('--max-workers', type=int, default=None,
                        help='Hard cap on worker count')
    parser.add_argument('--chunk',       type=int, default=50_000,
                        help='Systems per worker chunk (default: 50000)')
    parser.add_argument('--limit',       type=int, default=None,
                        help='Stop after N systems total (for testing)')
    args = parser.parse_args()

    worker_count = args.workers
    if args.max_workers and worker_count > args.max_workers:
        log.info(f"  Note: Capping workers from {worker_count} to {args.max_workers}")
        worker_count = args.max_workers

    mode_label = "REBUILD ALL" if args.rebuild else ("DIRTY ONLY" if args.dirty else "RESUME (unrated only)")
    startup_banner(log, "Ratings Computer", "Ratings v3.4 Best-Build Potential", [
        ("Mode",       mode_label),
        ("Workers",    str(worker_count)),
        ("Chunk size", f"{args.chunk:,} systems"),
        ("Log file",   LOG_FILE),
        ("DB",         DB_DSN.split('@')[-1]),
        ("Scoring",    "Official ED Trailblazers Update 3 mechanics"),
    ])

    stage_banner(log, 1, 3, "Diagnostic counts")
    try:
        conn = _connect_with_retry(DB_DSN, label='ratings-diag')
    except Exception as e:
        log.error(f"FATAL: Cannot connect to database: {e}")
        sys.exit(1)

    with conn.cursor() as diag:
        diag.execute("SELECT COUNT(*) FROM systems WHERE has_body_data = TRUE")
        total_with_bodies = diag.fetchone()[0]
        # Full rebuilds and dirty-only passes do not need a full-table ratings
        # count before work starts. On production-sized datasets that scan can
        # take many minutes and delays the actual rerate unnecessarily.
        if args.rebuild or args.dirty:
            already_rated = None
        else:
            diag.execute("SELECT COUNT(*) FROM ratings")
            already_rated = diag.fetchone()[0]
    conn.close()

    log.info(f"  Systems with body data : {fmt_num(total_with_bodies)}")
    if already_rated is None:
        log.info("  Already rated          : skipped for rebuild/dirty preflight")
        log.info("  Remaining (resume)     : skipped for rebuild/dirty preflight")
        log.info("  Coverage               : skipped for rebuild/dirty preflight")
        remaining = None
    else:
        remaining = total_with_bodies - already_rated
        log.info(f"  Already rated          : {fmt_num(already_rated)}")
        log.info(f"  Remaining (resume)     : {fmt_num(remaining)}")
        log.info(f"  Coverage               : {fmt_pct(already_rated, total_with_bodies)}")
    log.info("")
    log.info("  Score bands:")
    log.info("    0–30  : Barely viable (single economy, few bodies)")
    log.info("    31–50 : Functional (good for one economy, missing assets)")
    log.info("    51–65 : Solid (good body mix, clean economies)")
    log.info("    66–80 : Excellent (multiple strong economies, strategic assets)")
    log.info("    81–100: Exceptional (ELWs, clean stacks, near-perfect)")

    if args.rebuild:
        to_process = total_with_bodies
    elif args.dirty:
        to_process = None
    else:
        to_process = remaining

    if to_process == 0 and not args.rebuild:
        log.info("  ✓ All systems already rated — nothing to do!")
        log.info("    Use --rebuild to force re-rate everything.")
        return

    if to_process is None:
        log.info("  Estimated time         : unknown until dirty stream drains")
    else:
        est_h = to_process / 1_000_000 / max(worker_count, 1) * 0.8
        log.info(f"  Estimated time         : {est_h:.1f}h with {worker_count} workers")

    stage_banner(log, 2, 3, "Stream & rate systems")
    crash_hint(log, "automatically from the last rated system")

    stream_conn = _connect_with_retry(DB_DSN, label='ratings-stream')
    stream_conn.autocommit = False
    stream_conn.read_only = True

    with stream_conn.cursor(name='ratings_stream') as stream_cur:
        stream_cur.itersize = args.chunk

        if args.dirty:
            log.info("  Query: dirty systems only")
            stream_cur.execute("""
                SELECT id64, main_star_type
                FROM   systems
                WHERE  rating_dirty = TRUE
                  AND  has_body_data = TRUE
                ORDER BY id64
            """)
        elif args.rebuild:
            log.info("  Query: ALL systems with body data")
            stream_cur.execute("""
                SELECT s.id64, s.main_star_type
                FROM   systems s
                WHERE  s.has_body_data = TRUE
                ORDER BY s.id64
            """)
        else:
            log.info("  Query: unrated systems (resume-safe)")
            stream_cur.execute("""
                SELECT s.id64, s.main_star_type
                FROM   systems s
                LEFT JOIN ratings r ON r.system_id64 = s.id64
                WHERE  s.has_body_data  = TRUE
                  AND  r.system_id64   IS NULL
                ORDER BY s.id64
            """)

        script_start    = time.time()
        total_processed = 0
        total_errors    = 0
        chunks_dispatched = 0
        limit_remaining   = args.limit or 999_999_999
        pending_results   = []
        progress_total = args.limit if args.limit is not None else to_process
        progress = ProgressReporter(log, total=progress_total,
                                    label="ratings", interval=60, heartbeat=180)

        with mp.Pool(processes=worker_count) as pool:
            while limit_remaining > 0:
                fetch_n = min(args.chunk, limit_remaining)
                batch   = stream_cur.fetchmany(fetch_n)
                if not batch:
                    log.info("  Stream exhausted — all qualifying systems dispatched.")
                    break

                limit_remaining   -= len(batch)
                chunks_dispatched += 1

                pending_results.append(
                    pool.apply_async(worker_process, (chunks_dispatched, batch, DB_DSN))
                )

                if len(pending_results) >= worker_count * 2:
                    pending_results[0].wait()
                    still_pending = []
                    for res in pending_results:
                        if res.ready():
                            try:
                                p, e = res.get()
                                total_processed += p
                                total_errors    += e
                                progress.update(p, errors=e)
                            except Exception as ex:
                                log.error(f"Worker task failed: {ex}")
                                total_errors += args.chunk
                        else:
                            still_pending.append(res)
                    pending_results = still_pending

            log.info(f"  All {chunks_dispatched} chunks dispatched — draining pool...")
            for done in pending_results:
                try:
                    p, e = done.get()
                    total_processed += p
                    total_errors    += e
                    progress.update(p, errors=e)
                except Exception as ex:
                    log.error(f"Worker task failed during drain: {ex}")
                    total_errors += args.chunk

        progress.finish()

    stream_conn.close()
    elapsed = time.time() - script_start

    stage_banner(log, 3, 3, "Finalise — write app_meta")
    conn2 = _connect_with_retry(DB_DSN, label='ratings-finalise')
    with conn2.cursor() as cur:
        cur.execute("""
            INSERT INTO app_meta (key, value, updated_at)
            VALUES ('ratings_built', 'true', NOW())
            ON CONFLICT (key) DO UPDATE SET value = 'true', updated_at = NOW()
        """)
        cur.execute("""
            SELECT COUNT(*), COUNT(*) FILTER (WHERE score = 0),
                   MIN(score), MAX(score), AVG(score)::int
            FROM ratings
        """)
        row = cur.fetchone()
    conn2.commit()
    conn2.close()

    done_banner(log, "Ratings Complete", elapsed, [
        f"New ratings this run : {fmt_num(total_processed)}",
        f"Total in table       : {fmt_num(row[0])}",
        f"Zero scores          : {fmt_num(row[1])} ({fmt_pct(row[1], row[0])})",
        f"Score range          : {row[2]} – {row[3]}  (avg {row[4]})",
        f"Errors               : {fmt_num(total_errors)}",
        f"Speed                : {fmt_rate(total_processed, elapsed)}",
    ])

    if total_errors:
        log.warning(f"  {total_errors:,} systems were skipped due to errors — check DEBUG logs")

    log.info("Next step: python3 build_clusters.py")


if __name__ == '__main__':
    main()
