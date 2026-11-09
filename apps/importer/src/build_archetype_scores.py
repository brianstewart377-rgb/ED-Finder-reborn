#!/usr/bin/env python3
"""
ED Finder — Archetype Scoring Engine
Version: 1.0

Runs AFTER build_topology.py. Reads from bodies + ratings +
system_slot_topology tables. Writes to system_archetype_scores
and system_archetype_traits.

Each system receives 10 independent archetype scores (0-100). The
primary_archetype field records whichever archetype scores highest.
A structured JSONB rationale is generated for the primary archetype.

Usage:
    python3 build_archetype_scores.py              # unscored systems only
    python3 build_archetype_scores.py --rebuild    # rescore everything
    python3 build_archetype_scores.py --dirty      # only dirty rows
    python3 build_archetype_scores.py --workers 4
    python3 build_archetype_scores.py --chunk 10000
    python3 build_archetype_scores.py --limit 50000  # dev/test

Pipeline position:
    1. import_spansh.py
    2. build_ratings.py
    3. build_topology.py   → system_slot_topology
    4. build_archetype_scores.py  ← THIS
    5. REFRESH MATERIALIZED VIEW CONCURRENTLY mv_archetype_rankings
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
    startup_banner, stage_banner, done_banner,
    fmt_num,
)

# Import body diversity from the existing ratings engine
try:
    from build_ratings import compute_body_diversity
    _HAVE_RATINGS = True
except ImportError:
    _HAVE_RATINGS = False

# Import topology helpers
try:
    from build_topology import (
        compute_contamination_risk, _classify_bodies_simple,
        _load_base_synergy, BASE_SYNERGY,
    )
    _HAVE_TOPOLOGY = True
except ImportError:
    _HAVE_TOPOLOGY = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _make_direct_dsn(url: str) -> str:
    direct = os.getenv('DB_DSN_DIRECT', '')
    if direct:
        return direct
    if ':5433/' in url:
        url = url.replace(':5433/', ':5432/')
    url = url.replace('@pgbouncer:', '@postgres:')
    return url


def _connect_with_retry(dsn: str, label: str = 'archetype', retries: int = 10,
                        delay: float = 5.0):
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg.connect(
                dsn,
                keepalives=1, keepalives_idle=60,
                keepalives_interval=10, keepalives_count=6,
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
            logging.warning(f"DB connect failed ({label}, {attempt}/{retries}): {e}")
            time.sleep(wait)


_raw_url   = os.environ['DATABASE_URL']
DB_DSN     = _make_direct_dsn(_raw_url)
BATCH_SIZE = int(os.getenv('BATCH_SIZE', '5000'))
LOG_LEVEL  = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE   = os.getenv('LOG_FILE', '/tmp/build_archetype_scores.log')

os.makedirs(os.path.dirname(os.path.abspath(LOG_FILE)), exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('build_archetype_scores')


# ---------------------------------------------------------------------------
# Archetype definitions
# ---------------------------------------------------------------------------
# 10 colony archetypes. Each defines:
#   label              Human-readable name
#   description        One-line description
#   economy_pair       Target economy pair (or None for flexible archetypes)
#   body_weights       {body_type_key: weight 0-1} — contribution multipliers
#   slot_preference    'ground_heavy' | 'orbital_heavy' | 'balanced'
#   requires_landable  If True, score is severely capped with zero landable bodies
#   contamination_tolerance  0-1: how much contamination is acceptable
#   purity_multiplier  Score multiplier for clean stacks (1.0 = no bonus)
#   diversity_bonus    Score multiplier for high body diversity (optional)
#   tags               Default tags for this archetype

ARCHETYPE_DEFINITIONS = {

    'refinery_industrial': {
        'label':       'Refinery / Industrial Megacomplex',
        'description': 'Rocky and Icy body manufacturing hub',
        'economy_pair': ('Refinery', 'Industrial'),
        'body_weights': {
            'rocky_clean':  1.00,
            'rocky_ice':    0.80,
            'icy':          0.70,
            'rocky_rings':  0.55,
            'hmc':          0.35,
            'rocky_geo':    0.15,
            'rocky_bio':    0.20,
        },
        'slot_preference':          'ground_heavy',
        'requires_landable':        True,
        'contamination_tolerance':  0.30,
        'purity_multiplier':        1.35,
        'buildability_profile':     'standard',
        'tags': ['Manufacturing', 'CMM Composite', 'Refinery Hub'],
    },

    'extraction_refinery': {
        'label':       'Extraction / Refinery Mining Hub',
        'description': 'HMC and metal-rich mining support system',
        'economy_pair': ('Extraction', 'Refinery'),
        'body_weights': {
            'hmc':          1.00,
            'metal_rich':   0.90,
            'rocky_rings':  0.70,
            'hmc_geo':      0.80,
            'rocky_geo':    0.50,
        },
        'slot_preference':          'balanced',
        'requires_landable':        False,
        'contamination_tolerance':  0.40,
        'purity_multiplier':        1.20,
        'buildability_profile':     'mining_focus',
        'tags': ['Mining Hub', 'Extraction', 'Metal-Rich'],
    },

    'agriculture_terraforming': {
        'label':       'Agriculture / Terraforming Colony',
        'description': 'Population growth and terraforming focus',
        'economy_pair': ('Agriculture', 'Tourism'),
        'body_weights': {
            'elw':          1.00,
            'ww':           0.80,
            'terraformable': 0.60,
            'rocky_bio':    0.45,
            'ammonia':      0.30,
        },
        'slot_preference':          'ground_heavy',
        'requires_landable':        True,
        'contamination_tolerance':  0.35,
        'purity_multiplier':        1.40,
        'buildability_profile':     'growth_focus',
        'tags': ['Agriculture', 'Terraforming', 'ELW', 'Population Growth'],
    },

    'hitech_tourism': {
        'label':       'HighTech / Tourism Prestige Colony',
        'description': 'Prestige system with ELW, exotics, and high-tech industry',
        'economy_pair': ('HighTech', 'Tourism'),
        'body_weights': {
            'elw':         1.00,
            'ammonia':     0.90,
            'black_hole':  0.85,
            'neutron':     0.70,
            'white_dwarf': 0.55,
            'gas_giant':   0.65,
            'ww':          0.60,
        },
        'slot_preference':          'orbital_heavy',
        'requires_landable':        False,
        'contamination_tolerance':  0.45,
        'purity_multiplier':        1.30,
        'buildability_profile':     'prestige',
        'tags': ['Prestige', 'HighTech', 'Tourism', 'Exotic'],
    },

    'expansion_capital': {
        'label':       'Expansion Capital',
        'description': 'Strategic node for further colonisation chains',
        'economy_pair': ('Industrial', 'HighTech'),
        'body_weights': {
            'elw':         0.70,
            'gas_giant':   0.65,
            'rocky_clean': 0.60,
            'icy':         0.60,
            'hmc':         0.55,
        },
        'slot_preference':          'balanced',
        'requires_landable':        True,
        'contamination_tolerance':  0.55,
        'purity_multiplier':        1.00,
        'diversity_bonus':          1.30,
        'buildability_profile':     'flexible',
        'tags': ['Expansion', 'Capital', 'Strategic', 'Flexible'],
    },

    'trade_logistics': {
        'label':       'Trade / Logistics Hub',
        'description': 'Trade infrastructure and carrier support',
        'economy_pair': ('Industrial', 'Refinery'),
        'body_weights': {
            'rocky_ice':   0.80,
            'rocky_clean': 0.70,
            'icy':         0.65,
            'gas_giant':   0.50,
            'hmc':         0.45,
        },
        'slot_preference':          'balanced',
        'requires_landable':        True,
        'contamination_tolerance':  0.40,
        'purity_multiplier':        1.15,
        'buildability_profile':     'standard',
        'tags': ['Trade', 'Logistics', 'Carrier Support'],
    },

    'population_capital': {
        'label':       'Population Capital',
        'description': 'Maximum population growth potential',
        'economy_pair': ('Agriculture', 'HighTech'),
        'body_weights': {
            'elw':          1.00,
            'terraformable': 0.80,
            'ww':           0.70,
            'rocky_bio':    0.50,
            'gas_giant':    0.40,
        },
        'slot_preference':          'ground_heavy',
        'requires_landable':        True,
        'contamination_tolerance':  0.30,
        'purity_multiplier':        1.45,
        'buildability_profile':     'growth_focus',
        'tags': ['Population', 'Agriculture', 'ELW', 'Growth'],
    },

    'ax_forward_base': {
        'label':       'AX Forward Operating Base',
        'description': 'Anti-xeno military infrastructure hub',
        'economy_pair': ('Military', 'HighTech'),
        'body_weights': {
            'elw':         1.00,
            'neutron':     0.80,
            'black_hole':  0.70,
            'gas_giant':   0.60,
            'rocky_clean': 0.50,
        },
        'slot_preference':          'balanced',
        'requires_landable':        True,
        'contamination_tolerance':  0.50,
        'purity_multiplier':        1.20,
        'strategic_bonus':          0.20,
        'buildability_profile':     'military',
        'tags': ['AX', 'Military', 'Forward Base', 'Anti-Xeno'],
    },

    'military_industrial': {
        'label':       'Military / Industrial Complex',
        'description': 'Defensive stronghold with manufacturing capacity',
        'economy_pair': ('Military', 'Industrial'),
        'body_weights': {
            'elw':         0.90,
            'gas_giant':   0.70,
            'icy':         0.65,
            'rocky_clean': 0.60,
            'neutron':     0.50,
            'black_hole':  0.45,
        },
        'slot_preference':          'balanced',
        'requires_landable':        True,
        'contamination_tolerance':  0.40,
        'purity_multiplier':        1.25,
        'buildability_profile':     'military',
        'tags': ['Military', 'Industrial', 'Defence', 'Stronghold'],
    },

    'flexible_multirole': {
        'label':       'Flexible Multi-Role Colony',
        'description': 'High diversity, multiple viable specialisation paths',
        'economy_pair': None,
        'body_weights': {},   # Diversity metric drives score
        'slot_preference':          'balanced',
        'requires_landable':        False,
        'contamination_tolerance':  0.70,
        'purity_multiplier':        1.00,
        'diversity_bonus':          1.50,
        'buildability_profile':     'flexible',
        'tags': ['Multi-Role', 'Flexible', 'Generalist'],
    },
}

# Ordered list for deterministic iteration
ARCHETYPE_KEYS = list(ARCHETYPE_DEFINITIONS.keys())


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def _body_diversity(counts: dict) -> float:
    """
    Compute body diversity score 0-30.
    Counts distinct body types weighted by rarity.
    Falls back to internal implementation if build_ratings not importable.
    """
    if _HAVE_RATINGS:
        try:
            return compute_body_diversity(counts)
        except Exception:
            pass
    # Internal fallback: count of distinct non-zero body type keys
    rare_types = {'elw', 'ww', 'ammonia', 'black_hole', 'neutron', 'white_dwarf'}
    score = 0.0
    for k, v in counts.items():
        if v > 0:
            score += 3.0 if k in rare_types else 1.0
    return min(score, 30.0)


def compute_archetype_score(
    archetype_key: str,
    counts: dict,
    topology: Optional[dict],
    pair_synergy: float,
) -> dict:
    """
    Compute a 0-100 archetype score for one archetype.

    Returns dict with score components for explainability.
    """
    defn = ARCHETYPE_DEFINITIONS[archetype_key]

    # ── 1. Body composition (0-60 pts) ────────────────────────────────────────
    body_score = 0.0
    body_notes = []
    for body_key, weight in defn['body_weights'].items():
        count = counts.get(body_key, 0)
        if count > 0:
            contribution = min(count, 3) * weight + max(count - 3, 0) * weight * 0.4
            body_score += contribution * 20.0
            body_notes.append(f"{count}× {body_key.replace('_', ' ')}")

    body_score = min(body_score, 60.0)

    # flexible_multirole has no body weights — use diversity directly
    if archetype_key == 'flexible_multirole':
        diversity = _body_diversity(counts)
        body_score = diversity / 30.0 * 60.0

    # ── 2. Topology score (0-25 pts) ──────────────────────────────────────────
    topo_score = 0.0
    if topology:
        slot_pref = defn.get('slot_preference', 'balanced')
        if slot_pref == 'ground_heavy':
            topo_score = topology.get('ground_synergy', 0) * 0.25
        elif slot_pref == 'orbital_heavy':
            topo_score = topology.get('orbital_synergy', 0) * 0.25
        else:
            topo_score = (
                topology.get('orbital_synergy', 0) * 0.12 +
                topology.get('ground_synergy', 0) * 0.13
            )
        topo_score = min(topo_score, 25.0)

    # ── 3. Purity / contamination multiplier ──────────────────────────────────
    contamination = topology.get('contamination_risk', 0.5) if topology else 0.5
    tolerance     = defn['contamination_tolerance']
    purity_factor = 1.0

    if contamination > tolerance:
        excess = (contamination - tolerance) / max(1.0 - tolerance, 0.01)
        purity_factor = 1.0 - (excess * (1.0 - 1.0 / defn['purity_multiplier']))
        purity_factor = max(purity_factor, 0.40)   # floor at 40% of raw score
    else:
        clean_ratio   = 1.0 - (contamination / max(tolerance, 0.01))
        purity_factor = 1.0 + (clean_ratio * (defn['purity_multiplier'] - 1.0))

    # ── 4. Pair synergy boost (0-15 pts) ──────────────────────────────────────
    synergy_pts = pair_synergy * 15.0 if pair_synergy else 0.0

    # ── 5. Diversity bonus (expansion / flexible archetypes) ──────────────────
    diversity_factor = 1.0
    if 'diversity_bonus' in defn:
        diversity = _body_diversity(counts)
        div_norm  = diversity / 30.0
        diversity_factor = 1.0 + (div_norm * (defn['diversity_bonus'] - 1.0))

    # ── 6. Surface port requirement check ─────────────────────────────────────
    if defn.get('requires_landable') and counts.get('landable', 0) == 0:
        body_score *= 0.40   # severe penalty: no surface port possible

    # ── 7. Compose ────────────────────────────────────────────────────────────
    raw   = (body_score + topo_score + synergy_pts) * purity_factor * diversity_factor
    final = round(min(float(raw), 100.0), 2)

    return {
        'score':              final,
        'body_contribution':  round(body_score, 2),
        'topology_contribution': round(topo_score, 2),
        'purity_factor':      round(purity_factor, 3),
        'synergy_pts':        round(synergy_pts, 2),
        'contamination_risk': round(float(contamination), 3),
        'diversity_factor':   round(diversity_factor, 3),
        'notes':              body_notes,
    }


def compute_overall_development_potential(
    archetype_scores: dict,
    diversity: float,
    has_standout: bool,
    buildability: float,
) -> float:
    """
    Composite development potential across all archetypes.
    Supporting metric — NOT the primary ranking signal.
    """
    top3 = sorted(archetype_scores.values(), reverse=True)[:3]
    top3_avg = sum(top3) / 3 if top3 else 0

    diversity_bonus           = (diversity / 30.0) * 20.0
    buildability_contribution = buildability * 0.20

    raw = top3_avg * 0.60 + diversity_bonus + buildability_contribution

    # Rarity gate: systems without a standout body are capped at 82
    if not has_standout and raw > 82:
        raw = 82.0

    return round(min(raw, 100.0), 2)


def compute_buildability(
    counts: dict,
    topology: Optional[dict],
    archetype_key: str,
    pair_synergy: float,
) -> dict:
    """
    Compute buildability score + complexity for the given archetype.
    Returns dict: {buildability_score, build_complexity, cp_efficiency,
                   t3_scaling_viability, slot_efficiency}
    """
    defn = ARCHETYPE_DEFINITIONS[archetype_key]

    # CP efficiency: fraction of relevant bodies that are high-value
    primary_bodies = sum(
        counts.get(k, 0) * w
        for k, w in defn['body_weights'].items()
        if w >= 0.70
    )
    all_bodies = max(sum(counts.get(k, 0) for k in defn['body_weights']), 1)
    cp_efficiency = min(primary_bodies / all_bodies * 100, 100.0) if defn['body_weights'] else 50.0

    # T3 scaling: strong link potential is the proxy
    t3_scaling = topology.get('strong_link_potential', 30.0) if topology else 30.0

    # Slot efficiency: how well orbital/ground balance matches archetype
    slot_pref = defn.get('slot_preference', 'balanced')
    ground  = topology.get('ground_synergy', 50) if topology else 50
    orbital = topology.get('orbital_synergy', 50) if topology else 50

    if slot_pref == 'ground_heavy':
        slot_efficiency = ground * 0.70 + orbital * 0.30
    elif slot_pref == 'orbital_heavy':
        slot_efficiency = orbital * 0.70 + ground * 0.30
    else:
        slot_efficiency = (ground + orbital) / 2.0

    # Contamination management penalty (up to -30)
    contamination = topology.get('contamination_risk', 0.5) if topology else 0.5
    contamination_penalty = contamination * 30

    # Flexibility bonus (up to +10)
    flexibility    = topology.get('build_flexibility', 50) if topology else 50
    flexibility_bonus = flexibility * 0.10

    raw = (
        cp_efficiency   * 0.35 +
        t3_scaling      * 0.25 +
        slot_efficiency * 0.20 +
        flexibility_bonus
    ) - contamination_penalty

    score = round(max(0.0, min(100.0, raw)), 2)

    # Complexity classification
    if score >= 80 and contamination < 0.20:
        complexity = 'simple'
    elif score >= 65 and contamination < 0.40:
        complexity = 'moderate'
    elif score >= 45:
        complexity = 'advanced'
    else:
        complexity = 'expert'

    return {
        'buildability_score':    score,
        'build_complexity':      complexity,
        'cp_efficiency':         round(cp_efficiency, 2),
        't3_scaling_viability':  round(t3_scaling, 2),
        'slot_efficiency':       round(slot_efficiency, 2),
    }


# ---------------------------------------------------------------------------
# Rationale generation
# ---------------------------------------------------------------------------

def _tier(score: float) -> str:
    if score >= 88:  return 'S'
    if score >= 76:  return 'A'
    if score >= 60:  return 'B'
    if score >= 45:  return 'C'
    return 'D'


def _score_word(score: float) -> str:
    if score >= 88: return 'Exceptional'
    if score >= 76: return 'Excellent'
    if score >= 60: return 'Solid'
    if score >= 45: return 'Functional'
    return 'Limited'


def _build_positives(archetype_key, counts, topology, buildability) -> list:
    positives = []

    # High-value body presences
    if counts.get('elw', 0) >= 1:
        positives.append(f"{counts['elw']}× Earth-like World — strong link anchor")
    if counts.get('black_hole', 0) >= 1:
        positives.append('Black Hole — high Tourism + HighTech strong link')
    if counts.get('neutron', 0) >= 1:
        positives.append('Neutron Star — Tourism + HighTech boost')
    if archetype_key in ('refinery_industrial', 'trade_logistics'):
        if counts.get('rocky_clean', 0) >= 3:
            positives.append(f"{counts['rocky_clean']} clean Rocky bodies — ideal for Refinery")
        if counts.get('rocky_ice', 0) >= 2:
            positives.append(f"{counts['rocky_ice']} Rocky-Ice bodies — Refinery + Industrial hybrid")
    if archetype_key == 'extraction_refinery':
        if counts.get('hmc', 0) >= 2:
            positives.append(f"{counts['hmc']} HMC bodies — core Extraction resource")
        if counts.get('metal_rich', 0) >= 1:
            positives.append(f"{counts['metal_rich']} Metal-Rich bodies — Extraction bonus")
    if topology:
        if topology.get('strong_link_potential', 0) >= 60:
            positives.append('Strong link potential ≥60 — T3 scaling viable')
        if topology.get('contamination_risk', 1) < 0.20:
            positives.append('Very low contamination risk — clean economy stack')
    if buildability.get('build_complexity') in ('trivial', 'simple'):
        positives.append('Simple build order — straightforward construction path')
    if counts.get('terraformable', 0) >= 3:
        positives.append(f"{counts['terraformable']} terraformable bodies — Agriculture + population growth")
    return positives


def _build_risks(counts, topology, contamination_data, buildability) -> list:
    risks = []
    if counts.get('rocky_geo', 0) >= 2:
        risks.append(
            f"{counts['rocky_geo']} geo-signal Rocky bodies — "
            f"moderate Extraction/Industrial contamination risk"
        )
    if counts.get('rocky_bio', 0) >= 2:
        risks.append(
            f"{counts['rocky_bio']} bio-signal Rocky bodies — "
            f"Agriculture/Terraforming contamination risk"
        )
    if contamination_data:
        cr = contamination_data.get('risk_score', 0)
        if cr > 0.60:
            contam = contamination_data.get('primary_contaminant', 'unknown')
            risks.append(
                f"High {contam} contamination risk ({cr:.0%}) — "
                f"Refinery Hubs required to maintain top-2 economies"
            )
    if topology:
        if topology.get('weak_link_stability', 100) < 40:
            risks.append('Low weak-link stability — tidal-lock bodies reduce Agriculture efficiency')
    if buildability.get('build_complexity') == 'expert':
        risks.append('Expert complexity — multi-phase build requires deep game knowledge')
    return risks


def _suggest_build_path(archetype_key, counts, contamination_data) -> str:
    defn  = ARCHETYPE_DEFINITIONS[archetype_key]
    pair  = defn.get('economy_pair')
    risk  = contamination_data.get('risk_score', 0) if contamination_data else 0
    contam = contamination_data.get('primary_contaminant', '') if contamination_data else ''

    if pair is None:
        return (
            "No fixed build order — this is a flexible system. "
            "Choose the economy pair that best matches your goals."
        )

    eco_a, eco_b = pair
    if risk < 0.25:
        return (
            f"Straightforward: establish {eco_a} facilities first, then add "
            f"{eco_b} to complete the pair. Low contamination risk."
        )
    elif risk < 0.50:
        return (
            f"Sequence {eco_a} facilities first to establish top-2 dominance. "
            f"Monitor {contam} contamination from "
            f"{counts.get('rocky_geo', 0) + counts.get('rocky_bio', 0)} "
            f"contaminating bodies. Add Refinery Hubs on contaminating bodies "
            f"early to suppress third-economy bleed."
        )
    else:
        return (
            f"High contamination from {contam} bodies. "
            f"Place {eco_a} facilities on the cleanest bodies first. "
            f"Dedicate Refinery Hubs to each contaminating body before "
            f"expanding {eco_b} capacity. Consider avoiding the most "
            f"contaminated bodies entirely."
        )


def _compute_display_tags(
    archetype_key, counts, topology, buildability, contamination_data
) -> list:
    tags = []

    # Body type tags
    if counts.get('elw', 0) >= 1:
        n = counts['elw']
        tags.append('ELW' if n == 1 else f'{n}× ELW')
    if counts.get('black_hole', 0) >= 1:
        tags.append('Black Hole')
    if counts.get('neutron', 0) >= 1:
        tags.append('Neutron Star')
    if counts.get('rocky_clean', 0) >= 3:
        tags.append(f"{counts['rocky_clean']} Clean Rocky")
    if counts.get('rocky_ice', 0) >= 2:
        tags.append('Rocky-Ice')
    if counts.get('hmc', 0) >= 2:
        tags.append(f"{counts['hmc']} HMC")

    # Quality tags
    cr = contamination_data.get('risk_score', 1) if contamination_data else 0.5
    if cr < 0.20:
        tags.append('Low Contamination')
    elif cr > 0.60:
        tags.append('High Contamination')

    if buildability.get('build_complexity') in ('trivial', 'simple'):
        tags.append('T3 Friendly')
    elif buildability.get('build_complexity') == 'expert':
        tags.append('Expert Build')

    # Slot tags
    if topology:
        slots = topology.get('estimated_total_slots', 0)
        if slots >= 40:
            tags.append(f'{slots}+ Slots')
        elif slots >= 25:
            tags.append(f'{slots} Slots')

    if counts.get('terraformable', 0) >= 3:
        tags.append('Terraformable')
    if counts.get('landable', 0) >= 8:
        tags.append(f"{counts['landable']} Landable")

    return tags[:8]


def generate_structured_rationale(
    archetype_key: str,
    archetype_score: dict,
    counts: dict,
    topology: Optional[dict],
    buildability: dict,
    contamination_data: Optional[dict],
    confidence: float,
) -> dict:
    """
    Generate a full structured rationale for a system's primary archetype.

    Returns JSONB-ready dict matching the rationale schema:
    {summary, tier, headline, positives[], risks[], complexity,
     build_path, tags[], score_breakdown, data_confidence}
    """
    defn  = ARCHETYPE_DEFINITIONS[archetype_key]
    score = archetype_score['score']
    t     = _tier(score)

    summary = (
        f"{_score_word(score)} candidate for {defn['label']}. "
        f"{counts.get('rocky_clean', 0)} clean Rocky, "
        f"{counts.get('elw', 0)} ELW."
    )[:200]

    positives = _build_positives(archetype_key, counts, topology, buildability)
    risks     = _build_risks(counts, topology, contamination_data, buildability)
    tags      = _compute_display_tags(
        archetype_key, counts, topology, buildability, contamination_data
    )

    complexity_map = {
        'trivial':  'Trivial — straightforward single-economy build',
        'simple':   'Simple — clean pair, standard build order',
        'moderate': 'Moderate — some contamination management required',
        'advanced': 'Advanced — nested ports or tight sequencing needed',
        'expert':   'Expert — multi-phase build, deep game knowledge required',
    }
    complexity_str = complexity_map.get(
        buildability.get('build_complexity', 'moderate'), 'Moderate'
    )

    return {
        'summary':    summary,
        'tier':       t,
        'headline':   f"{int(score)} — {defn['label']}",
        'positives':  positives[:6],
        'risks':      risks[:4],
        'complexity': complexity_str,
        'build_path': _suggest_build_path(archetype_key, counts, contamination_data),
        'tags':       tags,
        'score_breakdown': {
            'body_composition':   archetype_score.get('body_contribution', 0),
            'topology':           archetype_score.get('topology_contribution', 0),
            'pair_synergy_pts':   archetype_score.get('synergy_pts', 0),
            'purity_factor':      archetype_score.get('purity_factor', 1.0),
            'contamination_risk': archetype_score.get('contamination_risk', 0),
            'diversity_factor':   archetype_score.get('diversity_factor', 1.0),
        },
        'data_confidence': round(confidence, 3),
    }


# ---------------------------------------------------------------------------
# System scoring (single-system entry point)
# ---------------------------------------------------------------------------

def score_system(
    system_id64: int,
    bodies: list,
    topology_row: Optional[dict],
    pair_synergy_rows: list,
    confidence: float,
    base_synergy: dict,
) -> dict:
    """
    Score a single system across all 10 archetypes.

    Returns a dict ready for DB upsert into system_archetype_scores
    and system_archetype_traits.
    """
    # Classify bodies using the lightweight classifier in build_topology
    if _HAVE_TOPOLOGY:
        counts = _classify_bodies_simple(bodies)
    else:
        # Minimal fallback
        counts = {}

    # Build pair synergy lookup
    pair_lookup = {}
    for pr in pair_synergy_rows:
        key = f"{pr['economy_a']}+{pr['economy_b']}"
        alt = f"{pr['economy_b']}+{pr['economy_a']}"
        val = float(pr.get('synergy_score', 50)) / 100.0
        pair_lookup[key] = val
        pair_lookup[alt] = val

    # Topology dict (may be None)
    topo = topology_row

    # Contamination for primary pair (computed below per archetype)
    # Compute all 10 archetype scores
    archetype_results = {}
    for key in ARCHETYPE_KEYS:
        defn       = ARCHETYPE_DEFINITIONS[key]
        pair       = defn.get('economy_pair')
        pair_score = 0.0

        if pair:
            pk = f"{pair[0]}+{pair[1]}"
            pair_score = pair_lookup.get(pk, 0.50)

        result = compute_archetype_score(key, counts, topo, pair_score)
        archetype_results[key] = result

    # Determine primary + secondary archetypes
    sorted_archetypes = sorted(
        archetype_results.items(), key=lambda kv: kv[1]['score'], reverse=True
    )
    primary_key   = sorted_archetypes[0][0]
    secondary_key = sorted_archetypes[1][0] if len(sorted_archetypes) > 1 else 'unknown'
    primary_score = archetype_results[primary_key]

    # Archetype confidence: gap between 1st and 2nd score
    score1 = sorted_archetypes[0][1]['score']
    score2 = sorted_archetypes[1][1]['score'] if len(sorted_archetypes) > 1 else 0
    archetype_confidence = round(
        min((score1 - score2) / max(score1, 1) * 2.0, 1.0), 3
    )

    # Buildability for primary archetype
    primary_pair  = ARCHETYPE_DEFINITIONS[primary_key].get('economy_pair')
    primary_synergy = 0.0
    if primary_pair:
        pk = f"{primary_pair[0]}+{primary_pair[1]}"
        primary_synergy = pair_lookup.get(pk, 0.50)

    buildability = compute_buildability(counts, topo, primary_key, primary_synergy)

    # Contamination for primary pair
    if primary_pair:
        contamination_data = compute_contamination_risk(counts, primary_pair)
    else:
        contamination_data = {'risk_score': 0.0, 'primary_contaminant': None,
                              'contamination_paths': [], 'mitigation': ''}

    # Purity score (inverse of contamination risk, 0-100)
    purity_score      = round((1.0 - contamination_data['risk_score']) * 100, 2)
    contamination_pct = round(contamination_data['risk_score'] * 100, 2)
    stable_top_two    = round(1.0 - contamination_data['risk_score'] * 0.8, 3)

    # Overall development potential
    diversity     = _body_diversity(counts)
    has_standout  = any([
        counts.get('elw', 0) >= 1,
        counts.get('black_hole', 0) >= 1,
        counts.get('neutron', 0) >= 1,
        counts.get('ww', 0) >= 2,
        counts.get('terraformable', 0) >= 5,
        counts.get('ammonia', 0) >= 1,
    ])
    odp = compute_overall_development_potential(
        {k: v['score'] for k, v in archetype_results.items()},
        diversity, has_standout, buildability['buildability_score'],
    )

    # Structured rationale for primary archetype
    rationale = generate_structured_rationale(
        primary_key, primary_score, counts, topo,
        buildability, contamination_data, confidence,
    )

    # Score breakdown JSONB
    score_breakdown = {
        'per_archetype': {
            k: {'score': v['score'], 'body': v['body_contribution'],
                'topo': v['topology_contribution'], 'purity': v['purity_factor']}
            for k, v in archetype_results.items()
        },
        'primary_archetype':  primary_key,
        'primary_contamination': contamination_data.get('risk_score', 0),
    }

    # Build display tags
    display_tags = _compute_display_tags(
        primary_key, counts, topo, buildability, contamination_data
    )

    # Topology-derived slot counts
    est_orbital = int(topo.get('estimated_orbital_slots', 0)) if topo else 0
    est_ground  = int(topo.get('estimated_ground_slots',  0)) if topo else 0
    est_total   = int(topo.get('estimated_total_slots',   0)) if topo else 0

    return {
        'scores': {
            'system_id64':                   system_id64,
            'primary_archetype':             primary_key,
            'secondary_archetype':           secondary_key,
            'archetype_confidence':          archetype_confidence,
            'score_refinery_industrial':     archetype_results['refinery_industrial']['score'],
            'score_extraction_refinery':     archetype_results['extraction_refinery']['score'],
            'score_agriculture_terraforming':archetype_results['agriculture_terraforming']['score'],
            'score_hitech_tourism':          archetype_results['hitech_tourism']['score'],
            'score_expansion_capital':       archetype_results['expansion_capital']['score'],
            'score_trade_logistics':         archetype_results['trade_logistics']['score'],
            'score_population_capital':      archetype_results['population_capital']['score'],
            'score_ax_forward_base':         archetype_results['ax_forward_base']['score'],
            'score_military_industrial':     archetype_results['military_industrial']['score'],
            'score_flexible_multirole':      archetype_results['flexible_multirole']['score'],
            'overall_development_potential': odp,
            'buildability_score':            buildability['buildability_score'],
            'build_complexity':              buildability['build_complexity'],
            'cp_efficiency':                 buildability['cp_efficiency'],
            't3_scaling_viability':          buildability['t3_scaling_viability'],
            'slot_efficiency':               buildability['slot_efficiency'],
            'purity_score':                  purity_score,
            'contamination_risk':            contamination_pct,
            'stable_top_two_prob':           stable_top_two,
            'confidence':                    confidence,
            'score_breakdown':               score_breakdown,
            'rationale':                     rationale,
            'dirty':                         False,
        },
        'traits': {
            'system_id64':        system_id64,
            'has_elw':            counts.get('elw', 0) > 0,
            'has_water_world':    counts.get('ww', 0) > 0,
            'has_ammonia_world':  counts.get('ammonia', 0) > 0,
            'has_black_hole':     counts.get('black_hole', 0) > 0,
            'has_neutron_star':   counts.get('neutron', 0) > 0,
            'has_white_dwarf':    counts.get('white_dwarf', 0) > 0,
            'has_ringed_body':    counts.get('rocky_rings', 0) > 0,
            'has_terraformables': counts.get('terraformable', 0) > 0,
            'has_pristine_res':   False,   # populated from reserve_level when available
            'has_bio_signals':    counts.get('bio', 0) > 0,
            'has_geo_signals':    counts.get('geo', 0) > 0,
            'is_scoopable_star':  False,   # populated from main_star_type when available
            'elw_count':          int(counts.get('elw', 0)),
            'ww_count':           int(counts.get('ww', 0)),
            'ammonia_count':      int(counts.get('ammonia', 0)),
            'gas_giant_count':    int(counts.get('gas_giant', 0)),
            'rocky_clean_count':  int(counts.get('rocky_clean', 0)),
            'rocky_ice_count':    int(counts.get('rocky_ice', 0)),
            'icy_count':          int(counts.get('icy', 0)),
            'hmc_count':          int(counts.get('hmc', 0)),
            'metal_rich_count':   int(counts.get('metal_rich', 0)),
            'landable_count':     int(counts.get('landable', 0)),
            'terraformable_count':int(counts.get('terraformable', 0)),
            'bio_signal_total':   int(counts.get('bio', 0)),
            'geo_signal_total':   int(counts.get('geo', 0)),
            'total_body_count':   len(bodies),
            'est_orbital_slots':  est_orbital,
            'est_ground_slots':   est_ground,
            'est_total_slots':    est_total,
            'display_tags':       display_tags,
        },
    }


# ---------------------------------------------------------------------------
# DB write helpers
# ---------------------------------------------------------------------------

def _write_scores_batch(conn, cur, scores_batch: list, traits_batch: list):
    """Upsert a batch of archetype score + traits rows."""
    if not scores_batch:
        return

    # system_archetype_scores upsert
    score_args = [(
        r['system_id64'],
        r['primary_archetype'],
        r['secondary_archetype'],
        r['archetype_confidence'],
        r['score_refinery_industrial'],
        r['score_extraction_refinery'],
        r['score_agriculture_terraforming'],
        r['score_hitech_tourism'],
        r['score_expansion_capital'],
        r['score_trade_logistics'],
        r['score_population_capital'],
        r['score_ax_forward_base'],
        r['score_military_industrial'],
        r['score_flexible_multirole'],
        r['overall_development_potential'],
        r['buildability_score'],
        r['build_complexity'],
        r['cp_efficiency'],
        r['t3_scaling_viability'],
        r['slot_efficiency'],
        r['purity_score'],
        r['contamination_risk'],
        r['stable_top_two_prob'],
        r['confidence'],
        json.dumps(r['score_breakdown']),
        json.dumps(r['rationale']),
        r['dirty'],
    ) for r in scores_batch]

    cur.executemany("""
        INSERT INTO system_archetype_scores (
            system_id64,
            primary_archetype, secondary_archetype, archetype_confidence,
            score_refinery_industrial, score_extraction_refinery,
            score_agriculture_terraforming, score_hitech_tourism,
            score_expansion_capital, score_trade_logistics,
            score_population_capital, score_ax_forward_base,
            score_military_industrial, score_flexible_multirole,
            overall_development_potential,
            buildability_score, build_complexity,
            cp_efficiency, t3_scaling_viability, slot_efficiency,
            purity_score, contamination_risk, stable_top_two_prob,
            confidence, score_breakdown, rationale, dirty,
            updated_at
        ) VALUES (
            %s,
            %s::colony_archetype, %s::colony_archetype, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s,
            %s, %s::build_complexity, %s, %s, %s,
            %s, %s, %s,
            %s, %s::jsonb, %s::jsonb, %s,
            NOW()
        )
        ON CONFLICT (system_id64) DO UPDATE SET
            primary_archetype              = EXCLUDED.primary_archetype,
            secondary_archetype            = EXCLUDED.secondary_archetype,
            archetype_confidence           = EXCLUDED.archetype_confidence,
            score_refinery_industrial      = EXCLUDED.score_refinery_industrial,
            score_extraction_refinery      = EXCLUDED.score_extraction_refinery,
            score_agriculture_terraforming = EXCLUDED.score_agriculture_terraforming,
            score_hitech_tourism           = EXCLUDED.score_hitech_tourism,
            score_expansion_capital        = EXCLUDED.score_expansion_capital,
            score_trade_logistics          = EXCLUDED.score_trade_logistics,
            score_population_capital       = EXCLUDED.score_population_capital,
            score_ax_forward_base          = EXCLUDED.score_ax_forward_base,
            score_military_industrial      = EXCLUDED.score_military_industrial,
            score_flexible_multirole       = EXCLUDED.score_flexible_multirole,
            overall_development_potential  = EXCLUDED.overall_development_potential,
            buildability_score             = EXCLUDED.buildability_score,
            build_complexity               = EXCLUDED.build_complexity,
            cp_efficiency                  = EXCLUDED.cp_efficiency,
            t3_scaling_viability           = EXCLUDED.t3_scaling_viability,
            slot_efficiency                = EXCLUDED.slot_efficiency,
            purity_score                   = EXCLUDED.purity_score,
            contamination_risk             = EXCLUDED.contamination_risk,
            stable_top_two_prob            = EXCLUDED.stable_top_two_prob,
            confidence                     = EXCLUDED.confidence,
            score_breakdown                = EXCLUDED.score_breakdown,
            rationale                      = EXCLUDED.rationale,
            dirty                          = EXCLUDED.dirty,
            updated_at                     = NOW()
    """, score_args)

    # system_archetype_traits upsert
    if traits_batch:
        traits_args = [(
            r['system_id64'],
            r['has_elw'], r['has_water_world'], r['has_ammonia_world'],
            r['has_black_hole'], r['has_neutron_star'], r['has_white_dwarf'],
            r['has_ringed_body'], r['has_terraformables'],
            r['has_pristine_res'], r['has_bio_signals'],
            r['has_geo_signals'], r['is_scoopable_star'],
            r['elw_count'], r['ww_count'], r['ammonia_count'],
            r['gas_giant_count'], r['rocky_clean_count'], r['rocky_ice_count'],
            r['icy_count'], r['hmc_count'], r['metal_rich_count'],
            r['landable_count'], r['terraformable_count'],
            r['bio_signal_total'], r['geo_signal_total'], r['total_body_count'],
            r['est_orbital_slots'], r['est_ground_slots'], r['est_total_slots'],
            r['display_tags'],
        ) for r in traits_batch]

        cur.executemany("""
            INSERT INTO system_archetype_traits (
                system_id64,
                has_elw, has_water_world, has_ammonia_world,
                has_black_hole, has_neutron_star, has_white_dwarf,
                has_ringed_body, has_terraformables,
                has_pristine_res, has_bio_signals,
                has_geo_signals, is_scoopable_star,
                elw_count, ww_count, ammonia_count,
                gas_giant_count, rocky_clean_count, rocky_ice_count,
                icy_count, hmc_count, metal_rich_count,
                landable_count, terraformable_count,
                bio_signal_total, geo_signal_total, total_body_count,
                est_orbital_slots, est_ground_slots, est_total_slots,
                display_tags, updated_at
            ) VALUES (
                %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, NOW()
            )
            ON CONFLICT (system_id64) DO UPDATE SET
                has_elw             = EXCLUDED.has_elw,
                has_water_world     = EXCLUDED.has_water_world,
                has_ammonia_world   = EXCLUDED.has_ammonia_world,
                has_black_hole      = EXCLUDED.has_black_hole,
                has_neutron_star    = EXCLUDED.has_neutron_star,
                has_white_dwarf     = EXCLUDED.has_white_dwarf,
                has_ringed_body     = EXCLUDED.has_ringed_body,
                has_terraformables  = EXCLUDED.has_terraformables,
                has_pristine_res    = EXCLUDED.has_pristine_res,
                has_bio_signals     = EXCLUDED.has_bio_signals,
                has_geo_signals     = EXCLUDED.has_geo_signals,
                is_scoopable_star   = EXCLUDED.is_scoopable_star,
                elw_count           = EXCLUDED.elw_count,
                ww_count            = EXCLUDED.ww_count,
                ammonia_count       = EXCLUDED.ammonia_count,
                gas_giant_count     = EXCLUDED.gas_giant_count,
                rocky_clean_count   = EXCLUDED.rocky_clean_count,
                rocky_ice_count     = EXCLUDED.rocky_ice_count,
                icy_count           = EXCLUDED.icy_count,
                hmc_count           = EXCLUDED.hmc_count,
                metal_rich_count    = EXCLUDED.metal_rich_count,
                landable_count      = EXCLUDED.landable_count,
                terraformable_count = EXCLUDED.terraformable_count,
                bio_signal_total    = EXCLUDED.bio_signal_total,
                geo_signal_total    = EXCLUDED.geo_signal_total,
                total_body_count    = EXCLUDED.total_body_count,
                est_orbital_slots   = EXCLUDED.est_orbital_slots,
                est_ground_slots    = EXCLUDED.est_ground_slots,
                est_total_slots     = EXCLUDED.est_total_slots,
                display_tags        = EXCLUDED.display_tags,
                updated_at          = NOW()
        """, traits_args)

    conn.commit()


# ---------------------------------------------------------------------------
# Worker process
# ---------------------------------------------------------------------------

def worker_process(worker_id: int, system_ids: list, db_dsn: str):
    """Worker: fetch bodies + topology, compute archetype scores, write DB."""
    hb   = WorkerHeartbeat(worker_id, len(system_ids))
    conn = _connect_with_retry(db_dsn, label=f'archetype_worker_{worker_id}')
    cur  = conn.cursor(row_factory=dict_row)

    base_synergy  = _load_base_synergy(conn) if _HAVE_TOPOLOGY else BASE_SYNERGY.copy()
    processed     = 0
    errors        = 0
    scores_batch  = []
    traits_batch  = []
    WRITE_EVERY   = 500

    try:
        for system_id64 in system_ids:
            hb.tick(processed, errors)
            try:
                # Fetch bodies
                cur.execute("""
                    SELECT id AS body_id, name, body_type, subtype,
                           is_landable, is_terraformable,
                           bio_signal_count, geo_signal_count,
                           distance_from_star, radius, gravity,
                           EXISTS (
                             SELECT 1
                             FROM body_rings br
                             WHERE br.system_id64 = bodies.system_id64
                               AND br.body_id = bodies.id
                               AND br.association_status = 'local_matched'
                           ) AS has_rings
                    FROM bodies WHERE system_id64 = %s
                """, (system_id64,))
                bodies = [dict(r) for r in cur.fetchall()]

                # Fetch topology row (may not exist)
                cur.execute("""
                    SELECT estimated_orbital_slots, estimated_ground_slots,
                           estimated_total_slots, orbital_synergy, ground_synergy,
                           build_flexibility, contamination_risk,
                           strong_link_potential, weak_link_stability,
                           nesting_potential, has_viable_surface_port,
                           has_deep_orbital_anchor
                    FROM system_slot_topology WHERE system_id64 = %s
                """, (system_id64,))
                topo_row = cur.fetchone()
                topo = dict(topo_row) if topo_row else None

                # Fetch pair synergy rows
                cur.execute("""
                    SELECT economy_a, economy_b, synergy_score
                    FROM economy_pair_synergy WHERE system_id64 = %s
                """, (system_id64,))
                pair_rows = [dict(r) for r in cur.fetchall()]

                # Fetch confidence from ratings table
                cur.execute(
                    "SELECT confidence FROM ratings WHERE system_id64 = %s",
                    (system_id64,)
                )
                conf_row   = cur.fetchone()
                confidence = float(conf_row['confidence']) if conf_row else 0.85

                # Score
                result = score_system(
                    system_id64, bodies, topo, pair_rows,
                    confidence, base_synergy,
                )

                scores_batch.append(result['scores'])
                traits_batch.append(result['traits'])
                processed += 1

                if len(scores_batch) >= WRITE_EVERY:
                    _write_scores_batch(conn, cur, scores_batch, traits_batch)
                    scores_batch.clear()
                    traits_batch.clear()

            except Exception as e:
                errors += 1
                log.warning(f"Worker {worker_id}: error on {system_id64}: {e}")
                conn.rollback()

        if scores_batch:
            _write_scores_batch(conn, cur, scores_batch, traits_batch)

    finally:
        cur.close()
        conn.close()

    return {'worker_id': worker_id, 'processed': processed, 'errors': errors}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _resolve_fetch_limit(limit: Optional[int]) -> int:
    """None means "no --limit given" -> apply the safety cap. 0 is a
    legitimate explicit request (e.g. a dry-run/smoke-test invocation) and
    must be honored as zero, not treated as falsy and silently replaced
    with the 10M cap — `limit or 10_000_000` did exactly that."""
    return 10_000_000 if limit is None else limit


def _fetch_system_ids(conn, mode: str, limit: Optional[int]) -> list:
    resolved_limit = _resolve_fetch_limit(limit)
    with conn.cursor() as cur:
        if mode == 'dirty':
            cur.execute("""
                SELECT system_id64 FROM system_archetype_scores
                WHERE dirty = TRUE
                ORDER BY system_id64
                LIMIT %s
            """, (resolved_limit,))
        elif mode == 'rebuild':
            cur.execute("""
                SELECT id64 FROM systems ORDER BY id64 LIMIT %s
            """, (resolved_limit,))
        else:
            cur.execute("""
                SELECT r.system_id64
                FROM ratings r
                LEFT JOIN system_archetype_scores a ON a.system_id64 = r.system_id64
                WHERE a.system_id64 IS NULL
                ORDER BY r.system_id64
                LIMIT %s
            """, (resolved_limit,))
        return [row[0] for row in cur.fetchall()]


def main():
    parser = argparse.ArgumentParser(description='ED Finder — Archetype Scorer')
    parser.add_argument('--rebuild', action='store_true')
    parser.add_argument('--dirty',   action='store_true')
    parser.add_argument('--workers', type=int, default=mp.cpu_count())
    parser.add_argument('--chunk',   type=int, default=BATCH_SIZE)
    parser.add_argument('--limit',   type=int, default=None)
    args = parser.parse_args()

    startup_banner(log, 'build_archetype_scores', '1.0')
    t_start = time.time()

    mode = 'dirty' if args.dirty else ('rebuild' if args.rebuild else 'new')
    log.info(f"Mode: {mode} | Workers: {args.workers} | Chunk: {args.chunk}")

    conn = _connect_with_retry(DB_DSN, label='archetype_main')
    try:
        stage_banner(log, 1, 2, 'Fetching system IDs')
        system_ids = _fetch_system_ids(conn, mode, args.limit)
        log.info(f"  {fmt_num(len(system_ids))} systems to score")
    finally:
        conn.close()

    if not system_ids:
        log.info("Nothing to score.")
        done_banner(log, 'build_archetype_scores', time.time() - t_start)
        return

    chunks = [
        system_ids[i:i + args.chunk]
        for i in range(0, len(system_ids), args.chunk)
    ]

    stage_banner(log, 2, 2, 'Scoring archetypes')
    with mp.Pool(processes=args.workers) as pool:
        results = pool.starmap(
            worker_process,
            [(i % args.workers, chunk, DB_DSN) for i, chunk in enumerate(chunks)]
        )

    total_processed = sum(r['processed'] for r in results)
    total_errors    = sum(r['errors']    for r in results)

    done_banner(log, 'build_archetype_scores', time.time() - t_start)
    log.info(f"  Processed: {fmt_num(total_processed)} | Errors: {fmt_num(total_errors)}")
    if total_errors:
        log.warning(f"  {total_errors} systems failed — check log for details")

    log.info(
        "Run: REFRESH MATERIALIZED VIEW CONCURRENTLY mv_archetype_rankings; "
        "to update the rankings view."
    )


if __name__ == '__main__':
    main()
