#!/usr/bin/env python3
"""
ED Finder — Topology & Slot Inference Engine
Version: 1.0

Runs AFTER build_ratings.py. Reads from the bodies + ratings tables,
computes inferred slot topology and economy pair synergy, then writes
to system_slot_topology and economy_pair_synergy.

IMPORTANT: Slot counts are ESTIMATES derived from body physics.
Frontier does not expose actual slot counts via any public API or
data feed. All slot figures produced by this script are labelled
as estimated throughout.

Usage:
    python3 build_topology.py              # process all unprocessed systems
    python3 build_topology.py --rebuild    # reprocess ALL systems
    python3 build_topology.py --dirty      # only systems flagged dirty in ratings
    python3 build_topology.py --workers 4  # override worker count (default: CPU count)
    python3 build_topology.py --chunk 10000
    python3 build_topology.py --limit 50000  # process at most N systems (dev/test)

Pipeline position:
    1. import_spansh.py   → systems, bodies, stations
    2. build_ratings.py   → ratings table (existing)
    3. build_topology.py  → system_slot_topology, economy_pair_synergy  ← THIS
    4. build_archetype_scores.py → system_archetype_scores, system_archetype_traits
"""

import os
import sys
import json
import time
import logging
import argparse
import multiprocessing as mp
from typing import Optional

import psycopg
from psycopg.rows import dict_row

from progress import (
    WorkerHeartbeat,
    startup_banner, stage_banner, done_banner, fmt_num,
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


def _connect_with_retry(dsn: str, label: str = 'topology', retries: int = 10,
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
                    f"-c idle_in_transaction_session_timeout=3600000"
                )
            )
            return conn
        except Exception as e:
            if attempt == retries:
                raise
            wait = min(delay * attempt, 60)
            logging.warning(f"  DB connect failed ({label}, attempt {attempt}/{retries}): {e}")
            time.sleep(wait)


_raw_url   = os.environ['DATABASE_URL']
DB_DSN     = _make_direct_dsn(_raw_url)
BATCH_SIZE = int(os.getenv('BATCH_SIZE', '5000'))
LOG_LEVEL  = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE   = os.getenv('LOG_FILE', '/tmp/build_topology.log')

os.makedirs(os.path.dirname(os.path.abspath(LOG_FILE)), exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('build_topology')


# ---------------------------------------------------------------------------
# Slot estimation
# ---------------------------------------------------------------------------
# These are COMMUNITY-DERIVED estimates from observation of the in-game
# Architect Mode slot display. They are NOT from any official Frontier API.

def estimate_body_slots(body: dict) -> tuple:
    """
    Estimate (orbital_slots, ground_slots) for a single body.

    Ground slots are non-zero only for landable bodies.
    Returns a tuple of (int, int).
    """
    sub        = str(body.get('subtype') or '').lower()
    is_land    = bool(body.get('is_landable', False))
    radius_km  = float(body.get('radius') or 0)
    body_type  = str(body.get('body_type') or '').lower()
    sc         = str(body.get('spectral_class') or '')
    is_main    = bool(body.get('is_main_star', False))
    has_rings  = bool(body.get('has_rings', False))

    # ── Stars ─────────────────────────────────────────────────────────────────
    if body_type == 'star':
        if is_main:
            first = sc[0].upper() if sc else ''
            if first in ('O', 'B', 'A'):
                return (10, 0)
            elif first in ('F', 'G', 'K'):
                return (7, 0)
            else:
                return (4, 0)
        else:
            return (5, 0)

    # ── Gas Giants ────────────────────────────────────────────────────────────
    if 'gas giant' in sub:
        return (5 if has_rings else 3, 0)

    # ── Special worlds ────────────────────────────────────────────────────────
    if 'earth-like' in sub or 'earthlike' in sub:
        return (4, 8 if is_land else 0)
    if 'water world' in sub:
        return (3, 0)
    if 'ammonia' in sub:
        return (3, 0)

    # ── Rocky Ice ─────────────────────────────────────────────────────────────
    if 'rocky ice' in sub:
        return (3, 3 if is_land else 0)

    # ── Rocky Bodies ──────────────────────────────────────────────────────────
    if 'rocky body' in sub or sub == 'rocky':
        if radius_km > 5000:
            return (3, 6 if is_land else 0)
        elif radius_km > 2500:
            return (2, 4 if is_land else 0)
        else:
            return (2, 2 if is_land else 0)

    # ── High Metal Content ────────────────────────────────────────────────────
    if 'high metal content' in sub:
        if radius_km > 4000:
            return (3, 5 if is_land else 0)
        else:
            return (2, 3 if is_land else 0)

    # ── Metal Rich ────────────────────────────────────────────────────────────
    if 'metal-rich' in sub or 'metal rich' in sub:
        return (2, 3 if is_land else 0)

    # ── Icy Bodies ────────────────────────────────────────────────────────────
    if 'icy body' in sub or sub == 'icy':
        if is_land and radius_km > 2000:
            return (2, 2)
        return (2, 0)

    # ── Default fallback ──────────────────────────────────────────────────────
    return (1, 1 if is_land else 0)


# ---------------------------------------------------------------------------
# Topology metrics
# ---------------------------------------------------------------------------

def _distance_weight(ls) -> float:
    """Weight a body by its distance from the arrival star."""
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


def compute_topology_metrics(bodies: list, counts: dict) -> dict:
    """
    Compute system-level topology metrics from the full body list and
    the pre-computed classify_bodies() counts dict.

    Returns a dict matching the system_slot_topology column set.
    """
    # ── Slot estimation ───────────────────────────────────────────────────────
    total_orbital = 0
    total_ground  = 0
    orbital_quality = 0.0
    ground_quality  = 0.0
    body_slot_list  = []

    for body in bodies:
        orb, gnd = estimate_body_slots(body)
        dw = _distance_weight(body.get('distance_from_star'))

        total_orbital += orb
        total_ground  += gnd
        orbital_quality += orb * dw * 10.0
        if gnd > 0:
            ground_quality += gnd * dw * 10.0

        body_slot_list.append({
            'body_id':       body.get('body_id'),
            'body_name':     body.get('name', ''),
            'body_type':     str(body.get('body_type') or ''),
            'subtype':       str(body.get('subtype') or ''),
            'distance_ls':   body.get('distance_from_star'),
            'est_orbital':   orb,
            'est_ground':    gnd,
            'is_landable':   bool(body.get('is_landable', False)),
        })

    orbital_quality = min(orbital_quality, 100.0)
    ground_quality  = min(ground_quality,  100.0)
    total_slots     = total_orbital + total_ground

    slot_density = (orbital_quality + ground_quality) / 2.0 if total_slots > 0 else 0.0

    # ── Strong link potential ─────────────────────────────────────────────────
    # Driven by body types that produce strong economy links.
    strong_link_raw = sum([
        counts.get('elw', 0)          * 25,
        counts.get('ww', 0)           * 18,
        counts.get('ammonia', 0)      * 20,
        counts.get('gas_giant', 0)    * 12,
        counts.get('rocky_rings', 0)  * 8,
        counts.get('black_hole', 0)   * 22,
        counts.get('neutron', 0)      * 18,
        counts.get('white_dwarf', 0)  * 10,
    ])
    strong_link_potential = min(float(strong_link_raw), 100.0)

    # ── Weak link stability ───────────────────────────────────────────────────
    # High stability = system resists weak-link degradation.
    destabilisers = (
        counts.get('tidal_lock', 0) * 8 +
        counts.get('icy', 0)        * 4
    )
    weak_link_stability = float(max(100 - destabilisers, 0))

    # ── Orbital synergy ───────────────────────────────────────────────────────
    orbital_raw = (
        counts.get('gas_giant', 0)     * 6 +
        counts.get('elw', 0)           * 5 +
        counts.get('ww', 0)            * 4 +
        counts.get('ammonia', 0)       * 4 +
        counts.get('rocky_ice', 0)     * 3 +
        counts.get('icy', 0)           * 3 +
        counts.get('rocky_clean', 0)   * 3 +
        counts.get('hmc', 0)           * 3 +
        counts.get('metal_rich', 0)    * 2
    )
    orbital_synergy = min(float(orbital_raw * 5), 100.0)

    # ── Ground synergy ────────────────────────────────────────────────────────
    ground_raw = (
        counts.get('landable', 0)              * 10 +
        counts.get('landable_rocky_clean', 0)  * 5 +
        counts.get('landable_hmc', 0)          * 4
    )
    ground_synergy = min(float(ground_raw * 4), 100.0)

    # ── Build flexibility ─────────────────────────────────────────────────────
    # Proxy for body diversity — more distinct types = more build paths.
    type_counts = [
        min(counts.get(k, 0), 1)
        for k in ('rocky_clean', 'rocky_ice', 'icy', 'hmc', 'metal_rich',
                  'gas_giant', 'elw', 'ww', 'ammonia', 'rocky_geo', 'rocky_bio')
    ]
    distinct_types = sum(type_counts)
    build_flexibility = min(float(distinct_types / 11.0 * 100), 100.0)

    # ── Nesting potential ─────────────────────────────────────────────────────
    nesting_potential = min(
        float(counts.get('gas_giant', 0) * 20 + counts.get('rocky_clean', 0) * 8),
        100.0
    )

    # ── Contamination risk (topology-level estimate) ──────────────────────────
    # Counts body types that add competing economies.
    contaminators = (
        counts.get('rocky_geo', 0)   * 0.85 +
        counts.get('rocky_mixed', 0) * 0.95 +
        counts.get('rocky_bio', 0)   * 0.55 +
        counts.get('rocky_rings', 0) * 0.25 +
        counts.get('ammonia', 0)     * 0.40 +
        counts.get('gas_giant', 0)   * 0.30
    )
    total_relevant = max(sum([
        counts.get('rocky_geo', 0),
        counts.get('rocky_mixed', 0),
        counts.get('rocky_bio', 0),
        counts.get('rocky_rings', 0),
        counts.get('ammonia', 0),
        counts.get('gas_giant', 0),
        counts.get('rocky_clean', 0),
        counts.get('rocky_ice', 0),
        counts.get('icy', 0),
        counts.get('hmc', 0),
        counts.get('elw', 0),
    ]), 1)
    contamination_risk = round(min(contaminators / total_relevant, 1.0), 3)

    # ── Topology flags ────────────────────────────────────────────────────────
    has_viable_surface_port = (
        counts.get('landable', 0) > 0 and
        (counts.get('landable_rocky_any', 0) + counts.get('landable_hmc', 0)) > 0
    )
    has_deep_orbital_anchor = counts.get('gas_giant', 0) > 0
    has_ringed_gas_giant    = counts.get('rocky_rings', 0) > 0
    has_binary              = counts.get('secondary_star', 0) > 0

    return {
        'estimated_orbital_slots': int(total_orbital),
        'estimated_ground_slots':  int(total_ground),
        'estimated_total_slots':   int(total_slots),
        'orbital_slot_quality':    round(orbital_quality, 2),
        'ground_slot_quality':     round(ground_quality, 2),
        'slot_density_score':      round(slot_density, 2),
        'body_slots':              body_slot_list,
        'local_body_groups':       [],   # populated separately if parents[] available
        'strong_link_potential':   round(strong_link_potential, 2),
        'weak_link_stability':     round(weak_link_stability, 2),
        'nesting_potential':       round(nesting_potential, 2),
        'orbital_synergy':         round(orbital_synergy, 2),
        'ground_synergy':          round(ground_synergy, 2),
        'build_flexibility':       round(build_flexibility, 2),
        'contamination_risk':      contamination_risk,
        'has_viable_surface_port': has_viable_surface_port,
        'has_deep_orbital_anchor': has_deep_orbital_anchor,
        'has_ringed_gas_giant':    has_ringed_gas_giant,
        'has_binary_or_trinary':   has_binary,
    }


# ---------------------------------------------------------------------------
# Economy pair synergy
# ---------------------------------------------------------------------------

# Global baseline synergy values (0-1).
# Loaded from pair_synergy_constants at runtime; these are the hard-coded
# fallback if the DB table hasn't been seeded yet.
BASE_SYNERGY = {
    'Refinery+Industrial':   0.95,
    'Agriculture+Tourism':   0.91,
    'HighTech+Tourism':      0.88,
    'Extraction+Refinery':   0.82,
    'Agriculture+HighTech':  0.79,
    'HighTech+Military':     0.76,
    'Industrial+Military':   0.74,
    'Refinery+Military':     0.58,
    'Extraction+Industrial': 0.55,
    'Agriculture+Refinery':  0.28,
    'Tourism+Refinery':      0.22,
}

PAIR_MODIFIERS = {
    'Refinery+Industrial': {
        'rocky_ice':   +0.08,
        'icy':         +0.05,
        'rocky_clean': +0.04,
        'rocky_geo':   -0.12,
        'rocky_bio':   -0.08,
    },
    'Agriculture+Tourism': {
        'elw':         +0.12,
        'ww':          +0.08,
        'ammonia':     +0.06,
        'terraformable': +0.04,
        'tidal_lock':  -0.06,
        'icy':         -0.05,
    },
    'HighTech+Tourism': {
        'black_hole':  +0.15,
        'neutron':     +0.10,
        'ammonia':     +0.10,
        'gas_giant':   +0.06,
        'elw':         +0.08,
    },
    'Extraction+Refinery': {
        'hmc':         +0.10,
        'hmc_geo':     +0.08,
        'metal_rich':  +0.06,
        'rocky_rings': +0.04,
        'rocky_geo':   -0.05,
    },
    'Agriculture+HighTech': {
        'elw':         +0.10,
        'ww':          +0.05,
        'rocky_bio':   +0.04,
        'rocky_geo':   -0.08,
        'icy':         -0.04,
    },
    'HighTech+Military': {
        'elw':         +0.10,
        'neutron':     +0.08,
        'black_hole':  +0.07,
        'gas_giant':   +0.05,
    },
    'Industrial+Military': {
        'icy':         +0.08,
        'rocky_ice':   +0.06,
        'rocky_clean': +0.04,
        'rocky_geo':   -0.06,
    },
}

# Economy pairs to compute synergy for (canonical order, alphabetic within pair)
ECONOMY_PAIRS = [
    ('Agriculture', 'HighTech'),
    ('Agriculture', 'Tourism'),
    ('Agriculture', 'Refinery'),
    ('Extraction',  'Industrial'),
    ('Extraction',  'Refinery'),
    ('HighTech',    'Military'),
    ('HighTech',    'Tourism'),
    ('Industrial',  'Military'),
    ('Refinery',    'Industrial'),
    ('Refinery',    'Military'),
    ('Tourism',     'Refinery'),
]


def _load_base_synergy(conn) -> dict:
    """Load pair_synergy_constants from DB into a lookup dict."""
    synergy = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT economy_a, economy_b, base_synergy "
                "FROM pair_synergy_constants"
            )
            for row in cur.fetchall():
                key = f"{row[0]}+{row[1]}"
                alt = f"{row[1]}+{row[0]}"
                synergy[key] = float(row[2])
                synergy[alt] = float(row[2])
    except Exception as e:
        log.warning(f"Could not load pair_synergy_constants: {e}. Using defaults.")
    return synergy or BASE_SYNERGY.copy()


def compute_system_pair_synergy(
    economy_a: str,
    economy_b: str,
    counts: dict,
    topology: dict,
    base_synergy: dict,
) -> float:
    """
    Compute system-specific pair synergy score (0-1).
    Starts from the global baseline and applies body-type modifiers.
    """
    pair_key = f'{economy_a}+{economy_b}'
    alt_key  = f'{economy_b}+{economy_a}'

    base = base_synergy.get(pair_key, base_synergy.get(alt_key, 0.50))
    mods = PAIR_MODIFIERS.get(pair_key, PAIR_MODIFIERS.get(alt_key, {}))

    adjusted = base
    for body_key, modifier in mods.items():
        count = counts.get(body_key, 0)
        if count > 0:
            effect = modifier + modifier * 0.5 * (min(count, 4) - 1)
            adjusted += effect

    # Topology modifier
    if topology:
        cr = topology.get('contamination_risk', 0.5)
        if cr < 0.20:
            adjusted += 0.05
        elif cr > 0.70:
            adjusted -= 0.10

    return round(max(0.0, min(1.0, adjusted)), 3)


def compute_contamination_risk(counts: dict, target_pair: tuple) -> dict:
    """
    Estimate the risk that a third economy contaminates the target pair.

    Returns dict: {risk_score, primary_contaminant, contamination_paths, mitigation}
    """
    economy_a, economy_b = target_pair

    BODY_ECONOMY_ADDS = {
        'rocky_geo':   ['Extraction', 'Industrial'],
        'rocky_bio':   ['Agriculture', 'Terraforming'],
        'rocky_rings': ['Extraction'],
        'rocky_mixed': ['Extraction', 'Industrial', 'Agriculture', 'Terraforming'],
        'hmc_geo':     ['Extraction'],
        'ammonia':     ['HighTech', 'Tourism'],
        'elw':         ['Agriculture', 'HighTech', 'Military', 'Tourism'],
        'ww':          ['Agriculture', 'Tourism'],
        'gas_giant':   ['HighTech', 'Industrial'],
        'neutron':     ['HighTech', 'Tourism'],
        'black_hole':  ['HighTech', 'Tourism'],
    }
    SEVERITY = {
        'rocky_geo':   0.85,
        'rocky_mixed': 0.95,
        'rocky_bio':   0.55,
        'rocky_rings': 0.25,
        'ammonia':     0.40,
        'gas_giant':   0.30,
        'elw':         0.35,
        'ww':          0.25,
        'hmc_geo':     0.50,
        'neutron':     0.20,
        'black_hole':  0.20,
    }

    contaminant_scores = {}
    contamination_events = []

    for body_key, economies in BODY_ECONOMY_ADDS.items():
        count = counts.get(body_key, 0)
        if count == 0:
            continue
        severity = SEVERITY.get(body_key, 0.20)
        for eco in economies:
            if eco not in (economy_a, economy_b):
                score = severity * min(count, 3) / 3.0
                contaminant_scores[eco] = contaminant_scores.get(eco, 0) + score
                contamination_events.append({
                    'body':     body_key,
                    'economy':  eco,
                    'severity': round(severity, 2),
                    'count':    count,
                })

    if not contaminant_scores:
        return {
            'risk_score':          0.0,
            'primary_contaminant': None,
            'contamination_paths': [],
            'mitigation':          'Low risk — standard build order sufficient',
        }

    overall_risk = min(
        sum(contaminant_scores.values()) / max(len(contaminant_scores), 1),
        1.0
    )
    primary = max(contaminant_scores, key=contaminant_scores.get)

    if overall_risk < 0.20:
        mitigation = 'Low risk — standard build order sufficient'
    elif overall_risk < 0.45:
        mitigation = (
            f'Moderate {primary} contamination risk. '
            f'Sequence {economy_a} facilities first to establish top-2 '
            f'before {primary} gains foothold.'
        )
    else:
        mitigation = (
            f'High {primary} contamination risk. '
            f'Refinery/Industrial Hubs required to maintain '
            f'{economy_a}+{economy_b} dominance. Consider dedicated '
            f'strong-link facilities on contaminating bodies.'
        )

    return {
        'risk_score':          round(overall_risk, 3),
        'primary_contaminant': primary,
        'contamination_paths': contamination_events[:5],
        'mitigation':          mitigation,
    }


# ---------------------------------------------------------------------------
# Worker process
# ---------------------------------------------------------------------------

def _classify_bodies_simple(bodies: list) -> dict:
    """
    Lightweight body classifier for topology use.
    Produces the subset of classify_bodies() counts needed by topology functions.
    For full classification, build_ratings.py's classify_bodies() is authoritative.
    """
    counts = {k: 0 for k in [
        'rocky_clean', 'rocky_geo', 'rocky_bio', 'rocky_rings', 'rocky_mixed',
        'rocky_ice', 'icy', 'hmc', 'hmc_geo', 'metal_rich', 'gas_giant',
        'elw', 'ww', 'ammonia', 'landable', 'landable_rocky_clean',
        'landable_rocky_any', 'landable_hmc', 'terraformable',
        'bio', 'geo', 'tidal_lock', 'neutron', 'black_hole', 'white_dwarf',
        'secondary_star',
    ]}

    for body in bodies:
        sub  = str(body.get('subtype') or '').lower()
        btype = str(body.get('body_type') or '').lower()
        land  = bool(body.get('is_landable', False))
        bio   = int(body.get('bio_signal_count') or 0)
        geo   = int(body.get('geo_signal_count') or 0)
        terra = bool(body.get('is_terraformable', False))
        tlock = bool(body.get('is_tidally_locked', False))
        is_main = bool(body.get('is_main_star', False))

        if btype == 'star':
            if not is_main:
                counts['secondary_star'] += 1
            if 'neutron' in sub:
                counts['neutron'] += 1
            elif 'black hole' in sub:
                counts['black_hole'] += 1
            elif 'white dwarf' in sub or sub in ('d', 'da', 'db', 'dc'):
                counts['white_dwarf'] += 1
            continue

        if 'gas giant' in sub:
            counts['gas_giant'] += 1
            continue

        if 'earth-like' in sub or 'earthlike' in sub:
            counts['elw'] += 1
            if land:
                counts['landable'] += 1
            continue

        if 'water world' in sub:
            counts['ww'] += 1
            continue

        if 'ammonia' in sub:
            counts['ammonia'] += 1
            continue

        if 'rocky ice' in sub:
            counts['rocky_ice'] += 1
            if land:
                counts['landable'] += 1
            continue

        if 'high metal content' in sub:
            if geo > 0:
                counts['hmc_geo'] += 1
            else:
                counts['hmc'] += 1
            if land:
                counts['landable'] += 1
                counts['landable_hmc'] += 1
            continue

        if 'metal-rich' in sub or 'metal rich' in sub:
            counts['metal_rich'] += 1
            if land:
                counts['landable'] += 1
            continue

        if 'icy body' in sub or sub == 'icy':
            counts['icy'] += 1
            if land:
                counts['landable'] += 1
            continue

        if 'rocky body' in sub or sub == 'rocky':
            modifiers = (1 if geo > 0 else 0) + (1 if bio > 0 else 0)
            has_rings = bool(body.get('has_rings', False))
            ring_mod  = 1 if has_rings else 0
            total_mods = modifiers + ring_mod

            if total_mods == 0:
                counts['rocky_clean'] += 1
                if land:
                    counts['landable_rocky_clean'] += 1
                    counts['landable_rocky_any']   += 1
            elif total_mods >= 2:
                counts['rocky_mixed'] += 1
                if land:
                    counts['landable_rocky_any'] += 1
            elif geo > 0:
                counts['rocky_geo'] += 1
                if land:
                    counts['landable_rocky_any'] += 1
            elif bio > 0:
                counts['rocky_bio'] += 1
                if land:
                    counts['landable_rocky_any'] += 1
            elif has_rings:
                counts['rocky_rings'] += 1
                if land:
                    counts['landable_rocky_any'] += 1

            if land:
                counts['landable'] += 1
            continue

        # Generic landable fallback
        if land:
            counts['landable'] += 1

        if terra:
            counts['terraformable'] += 1
        if tlock:
            counts['tidal_lock'] += 1
        counts['bio'] += bio
        counts['geo'] += geo

    return counts


def _process_system(system_id64: int, bodies: list, base_synergy: dict) -> dict:
    """
    Process one system: compute topology metrics and pair synergy.
    Returns a dict ready for DB upsert.
    """
    counts   = _classify_bodies_simple(bodies)
    topology = compute_topology_metrics(bodies, counts)

    # Compute pair synergy for all canonical pairs
    pair_rows = []
    for eco_a, eco_b in ECONOMY_PAIRS:
        cont = compute_contamination_risk(counts, (eco_a, eco_b))
        synergy = compute_system_pair_synergy(eco_a, eco_b, counts, topology, base_synergy)
        synergy_score = round(synergy * 100, 2)
        pair_rows.append({
            'system_id64':        system_id64,
            'economy_a':          eco_a,
            'economy_b':          eco_b,
            'synergy_score':      synergy_score,
            'purity_achievable':  round(1.0 - cont['risk_score'], 3),
            'contamination_paths': cont['contamination_paths'],
        })

    return {
        'system_id64': system_id64,
        'topology':    topology,
        'pairs':       pair_rows,
    }


def _write_topology_batch(conn, cur, topo_batch: list, pair_batch: list):
    """Upsert a batch of topology rows and pair synergy rows."""
    if not topo_batch:
        return

    # ── system_slot_topology upsert ───────────────────────────────────────────
    topo_args = [(
        r['system_id64'],
        r['estimated_orbital_slots'],
        r['estimated_ground_slots'],
        r['estimated_total_slots'],
        r['orbital_slot_quality'],
        r['ground_slot_quality'],
        r['slot_density_score'],
        json.dumps(r['body_slots']),
        json.dumps(r['local_body_groups']),
        r['strong_link_potential'],
        r['weak_link_stability'],
        r['nesting_potential'],
        r['orbital_synergy'],
        r['ground_synergy'],
        r['build_flexibility'],
        r['contamination_risk'],
        r['has_viable_surface_port'],
        r['has_deep_orbital_anchor'],
        r['has_ringed_gas_giant'],
        r['has_binary_or_trinary'],
    ) for r in topo_batch]

    cur.executemany("""
        INSERT INTO system_slot_topology (
            system_id64,
            estimated_orbital_slots, estimated_ground_slots, estimated_total_slots,
            orbital_slot_quality, ground_slot_quality, slot_density_score,
            body_slots, local_body_groups,
            strong_link_potential, weak_link_stability, nesting_potential,
            orbital_synergy, ground_synergy, build_flexibility, contamination_risk,
            has_viable_surface_port, has_deep_orbital_anchor,
            has_ringed_gas_giant, has_binary_or_trinary,
            updated_at
        ) VALUES (
            %s,
            %s, %s, %s,
            %s, %s, %s,
            %s::jsonb, %s::jsonb,
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s,
            %s, %s,
            NOW()
        )
        ON CONFLICT (system_id64) DO UPDATE SET
            estimated_orbital_slots = EXCLUDED.estimated_orbital_slots,
            estimated_ground_slots  = EXCLUDED.estimated_ground_slots,
            estimated_total_slots   = EXCLUDED.estimated_total_slots,
            orbital_slot_quality    = EXCLUDED.orbital_slot_quality,
            ground_slot_quality     = EXCLUDED.ground_slot_quality,
            slot_density_score      = EXCLUDED.slot_density_score,
            body_slots              = EXCLUDED.body_slots,
            local_body_groups       = EXCLUDED.local_body_groups,
            strong_link_potential   = EXCLUDED.strong_link_potential,
            weak_link_stability     = EXCLUDED.weak_link_stability,
            nesting_potential       = EXCLUDED.nesting_potential,
            orbital_synergy         = EXCLUDED.orbital_synergy,
            ground_synergy          = EXCLUDED.ground_synergy,
            build_flexibility       = EXCLUDED.build_flexibility,
            contamination_risk      = EXCLUDED.contamination_risk,
            has_viable_surface_port = EXCLUDED.has_viable_surface_port,
            has_deep_orbital_anchor = EXCLUDED.has_deep_orbital_anchor,
            has_ringed_gas_giant    = EXCLUDED.has_ringed_gas_giant,
            has_binary_or_trinary   = EXCLUDED.has_binary_or_trinary,
            updated_at              = NOW()
    """, topo_args)

    # ── Mark archetype scores dirty for every system we just touched ─────────
    # build_archetype_scores.py --dirty reads system_archetype_scores.dirty = TRUE.
    # Nothing else sets this flag, so topology must propagate it.
    # Use INSERT ... ON CONFLICT so rows are created (dirty=TRUE) even if
    # build_archetype_scores.py has never run yet for a given system.
    system_id64s = [r['system_id64'] for r in topo_batch]
    cur.executemany("""
        INSERT INTO system_archetype_scores (system_id64, dirty)
        VALUES (%s, TRUE)
        ON CONFLICT (system_id64) DO UPDATE SET dirty = TRUE
    """, [(sid,) for sid in system_id64s])

    # ── economy_pair_synergy upsert ───────────────────────────────────────────
    if pair_batch:
        pair_args = [(
            p['system_id64'],
            p['economy_a'],
            p['economy_b'],
            p['synergy_score'],
            p['purity_achievable'],
            json.dumps(p['contamination_paths']),
        ) for p in pair_batch]

        cur.executemany("""
            INSERT INTO economy_pair_synergy (
                system_id64, economy_a, economy_b,
                synergy_score, purity_achievable, contamination_paths
            ) VALUES (%s, %s::economy_type, %s::economy_type, %s, %s, %s::jsonb)
            ON CONFLICT (system_id64, economy_a, economy_b) DO UPDATE SET
                synergy_score       = EXCLUDED.synergy_score,
                purity_achievable   = EXCLUDED.purity_achievable,
                contamination_paths = EXCLUDED.contamination_paths
        """, pair_args)

    conn.commit()


def worker_process(worker_id: int, system_ids: list, db_dsn: str):
    """
    Worker process: fetches bodies for the given system IDs, computes
    topology + pair synergy, and writes to DB.
    """
    hb = WorkerHeartbeat(worker_id, len(system_ids))
    conn = _connect_with_retry(db_dsn, label=f'topology_worker_{worker_id}')
    cur  = conn.cursor(row_factory=dict_row)

    # Load base synergy from DB once per worker
    base_synergy = _load_base_synergy(conn)

    processed = 0
    errors    = 0
    topo_batch = []
    pair_batch = []
    WRITE_EVERY = 500

    try:
        for system_id64 in system_ids:
            hb.tick(processed, errors)
            try:
                # Fetch bodies for this system
                cur.execute("""
                    SELECT
                        b.id AS body_id, b.name, b.body_type, b.subtype,
                        b.is_landable, b.is_terraformable,
                        b.bio_signal_count, b.geo_signal_count,
                        b.distance_from_star, b.radius, b.gravity,
                        EXISTS (
                          SELECT 1
                          FROM body_rings br
                          WHERE br.system_id64 = b.system_id64
                            AND br.body_id = b.id
                            AND br.association_status = 'local_matched'
                        ) AS has_rings,
                        s.is_main_star, s.spectral_class
                    FROM bodies b
                    LEFT JOIN (
                        SELECT id64, main_star_type AS spectral_class,
                               TRUE AS is_main_star
                        FROM systems WHERE id64 = %s
                    ) s ON TRUE
                    WHERE b.system_id64 = %s
                """, (system_id64, system_id64))

                rows = cur.fetchall()
                if not rows:
                    continue

                bodies = [dict(r) for r in rows]
                result = _process_system(system_id64, bodies, base_synergy)

                topo_batch.append({**result['topology'], 'system_id64': system_id64})
                pair_batch.extend(result['pairs'])
                processed += 1

                if len(topo_batch) >= WRITE_EVERY:
                    _write_topology_batch(conn, cur, topo_batch, pair_batch)
                    topo_batch.clear()
                    pair_batch.clear()

            except Exception as e:
                errors += 1
                log.warning(f"  Worker {worker_id}: error on {system_id64}: {e}")
                conn.rollback()

        # Write remaining
        if topo_batch:
            _write_topology_batch(conn, cur, topo_batch, pair_batch)

    finally:
        cur.close()
        conn.close()

    return {'worker_id': worker_id, 'processed': processed, 'errors': errors}


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def _fetch_system_ids(conn, mode: str, limit: Optional[int]) -> list:
    """Fetch system IDs to process based on mode."""
    with conn.cursor() as cur:
        if mode == 'dirty':
            cur.execute("""
                SELECT DISTINCT r.system_id64
                FROM ratings r
                JOIN systems s ON s.id64 = r.system_id64
                LEFT JOIN system_slot_topology t ON t.system_id64 = r.system_id64
                WHERE s.rating_dirty = TRUE
                   OR t.system_id64 IS NULL
                ORDER BY r.system_id64
                LIMIT %s
            """, (limit or 10_000_000,))
        elif mode == 'rebuild':
            cur.execute("""
                SELECT id64 FROM systems
                ORDER BY id64
                LIMIT %s
            """, (limit or 10_000_000,))
        else:
            # Default: systems with ratings but no topology yet
            cur.execute("""
                SELECT r.system_id64
                FROM ratings r
                LEFT JOIN system_slot_topology t ON t.system_id64 = r.system_id64
                WHERE t.system_id64 IS NULL
                ORDER BY r.system_id64
                LIMIT %s
            """, (limit or 10_000_000,))

        return [row[0] for row in cur.fetchall()]


def main():
    parser = argparse.ArgumentParser(description='ED Finder — Topology Builder')
    parser.add_argument('--rebuild',  action='store_true',
                        help='Reprocess all systems (default: unprocessed only)')
    parser.add_argument('--dirty',    action='store_true',
                        help='Only reprocess dirty systems')
    parser.add_argument('--workers',  type=int, default=mp.cpu_count(),
                        help='Worker process count (default: CPU count)')
    parser.add_argument('--chunk',    type=int, default=BATCH_SIZE,
                        help='Systems per worker chunk')
    parser.add_argument('--limit',    type=int, default=None,
                        help='Max systems to process (dev/test)')
    args = parser.parse_args()

    startup_banner(log, 'build_topology', '1.0')
    t_start = time.time()

    mode = 'dirty' if args.dirty else ('rebuild' if args.rebuild else 'new')
    log.info(f"Mode: {mode} | Workers: {args.workers} | Chunk: {args.chunk}")

    conn = _connect_with_retry(DB_DSN, label='topology_main')
    try:
        stage_banner(log, 1, 2, 'Fetching system IDs')
        system_ids = _fetch_system_ids(conn, mode, args.limit)
        log.info(f"  {fmt_num(len(system_ids))} systems to process")
    finally:
        conn.close()

    if not system_ids:
        log.info("Nothing to do.")
        done_banner(log, 'build_topology', time.time() - t_start)
        return

    # Split into chunks for workers
    chunks = [
        system_ids[i:i + args.chunk]
        for i in range(0, len(system_ids), args.chunk)
    ]
    log.info(f"  {len(chunks)} chunks across {args.workers} workers")

    stage_banner(log, 2, 2, 'Processing topology')

    with mp.Pool(processes=args.workers) as pool:
        results = pool.starmap(
            worker_process,
            [(i % args.workers, chunk, DB_DSN) for i, chunk in enumerate(chunks)]
        )

    total_processed = sum(r['processed'] for r in results)
    total_errors    = sum(r['errors']    for r in results)

    done_banner(log, 'build_topology', time.time() - t_start)
    log.info(f"  Processed: {fmt_num(total_processed)} | Errors: {fmt_num(total_errors)}")
    if total_errors > 0:
        log.warning(f"  {total_errors} systems failed — check log for details")


if __name__ == '__main__':
    main()
