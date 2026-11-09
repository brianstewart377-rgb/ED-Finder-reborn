"""
ED Finder — Simulation: Economy Composition Modelling
======================================================
Analyses economy composition quality for a given set of placed facilities.

Design rules:
  • Pure functions — no DB, no asyncio.
  • Builds on domain/economy_state.py primitives.
  • Produces human-readable recommendations, not just scores.

The core question this module answers:
  "Given these facilities and their economies, how GOOD is the
   resulting economy composition for this system's intended archetype?"

Key concepts:
  Top-two alignment  — primary + secondary economies should be a known-good pair
  Tertiary dilution  — a strong third economy hurts the primary pair
  Dominance balance  — neither economy should completely drown the other
  Composition order  — the ORDER facilities are placed affects which economy
                       becomes primary (higher-count = primary)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from edfinder_api.domain.economy_state import EconomyAnalysis, EconomyState, IDEAL_PAIRS
from edfinder_api.domain.facilities import FacilityTemplate
from edfinder_api.domain.placements import FacilityPlacement


@dataclass
class CompositionResult:
    """
    Full composition analysis for a simulated build.
    Consumed by the API layer and frontend.
    """
    analysis:               EconomyAnalysis
    archetype_alignment:    float       # 0.0–1.0 match to intended archetype
    archetype_key:          Optional[str]
    ordering_risk:          float       # 0.0–1.0; high = placement order matters a lot
    recommendations:        list[str]
    warnings:               list[str]

    def to_dict(self) -> dict:
        return {
            **self.analysis.to_dict(),
            'archetype_alignment':  round(self.archetype_alignment, 3),
            'archetype_key':        self.archetype_key,
            'ordering_risk':        round(self.ordering_risk, 3),
            'recommendations':      self.recommendations,
            'composition_warnings': self.warnings,
        }

    @property
    def composition_score(self) -> float:
        """0.0–1.0 overall composition score, capped by archetype alignment."""
        return round(
            self.analysis.composition_quality * 0.7
            + self.archetype_alignment * 0.3,
            3
        )


def analyse_composition(
    placements: list[FacilityPlacement],
    archetype_key: Optional[str] = None,
) -> CompositionResult:
    """
    Main entry point: analyse economy composition from a list of placements.

    archetype_key: e.g. 'refinery_industrial'. If provided, alignment with
                   the intended archetype pair is scored separately.
    """
    state = EconomyState()
    for placement in placements:
        f = placement.facility
        if f.economy:
            state.add(
                economy=f.economy,
                facility_id=f.id,
                step=placement.step,
                weight=_economy_weight(f),
            )

    analysis = state.analyse()

    # ── Archetype alignment ───────────────────────────────────────────────
    archetype_alignment = _score_archetype_alignment(analysis, archetype_key)

    # ── Ordering risk ─────────────────────────────────────────────────────
    ordering_risk = _estimate_ordering_risk(state, analysis)

    # ── Recommendations ───────────────────────────────────────────────────
    recommendations = _build_recommendations(analysis, archetype_key, ordering_risk)
    warnings = list(analysis.warnings)

    return CompositionResult(
        analysis=analysis,
        archetype_alignment=archetype_alignment,
        archetype_key=archetype_key,
        ordering_risk=ordering_risk,
        recommendations=recommendations,
        warnings=warnings,
    )


def simulate_composition_variants(
    base_facilities: list[FacilityTemplate],
    candidate_additions: list[FacilityTemplate],
    archetype_key: Optional[str] = None,
) -> list[dict]:
    """
    Test adding each candidate facility to the base set and score
    the resulting compositions.

    Returns list of dicts sorted by composition_score DESC.
    Useful for "what should I add next?" recommendations.
    """
    results = []
    for candidate in candidate_additions:
        # Build placements list: base facilities + candidate
        placements = [
            FacilityPlacement(step=i + 1, facility=f, location='orbital')
            for i, f in enumerate(base_facilities)
        ]
        placements.append(
            FacilityPlacement(
                step=len(placements) + 1,
                facility=candidate,
                location='orbital',
            )
        )
        result = analyse_composition(placements, archetype_key)
        results.append({
            'facility_id':          candidate.id,
            'facility_name':        candidate.name,
            'economy':              candidate.economy,
            'composition_score':    result.composition_score,
            'composition_quality':  result.analysis.composition_quality,
            'archetype_alignment':  result.archetype_alignment,
            'top_two_alignment':    result.analysis.top_two_alignment,
            'primary':              result.analysis.primary,
            'secondary':            result.analysis.secondary,
        })

    return sorted(results, key=lambda x: x['composition_score'], reverse=True)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _economy_weight(facility: FacilityTemplate) -> float:
    """
    Economy contribution weight for a facility.
    Ports contribute more than support facilities to the economy composition.
    """
    if facility.is_colony_port:
        return 1.0
    if facility.is_port:
        return 2.0    # ports dominate economy composition
    return 1.0        # support facilities contribute equally


def _score_archetype_alignment(
    analysis: EconomyAnalysis,
    archetype_key: Optional[str],
) -> float:
    """
    How well does the composition match the intended archetype's
    preferred economy pair?

    0.0 = completely misaligned
    1.0 = perfect match
    """
    if not archetype_key or not analysis.primary:
        return 0.5  # unknown — neutral score

    # Derive expected economies from archetype key
    expected = _archetype_to_economies(archetype_key)
    if not expected:
        return 0.5

    primary_expected   = expected[0] if expected else None
    secondary_expected = expected[1] if len(expected) > 1 else None

    primary_match   = analysis.primary == primary_expected
    secondary_match = analysis.secondary == secondary_expected

    if primary_match and secondary_match:
        return min(1.0, 0.85 + analysis.analysis.composition_quality * 0.15
                   if hasattr(analysis, 'analysis') else 0.95)
    if primary_match:
        return 0.70
    if analysis.primary in expected:
        return 0.55   # right economy, wrong position
    return 0.25


def _score_archetype_alignment(
    analysis: EconomyAnalysis,
    archetype_key: Optional[str],
) -> float:
    if not archetype_key or not analysis.primary:
        return 0.5

    expected = _archetype_to_economies(archetype_key)
    if not expected:
        return 0.5

    primary_expected   = expected[0] if expected else None
    secondary_expected = expected[1] if len(expected) > 1 else None

    primary_match   = analysis.primary == primary_expected
    secondary_match = analysis.secondary == secondary_expected

    # Bonus for composition quality
    quality_bonus = analysis.composition_quality * 0.10

    if primary_match and secondary_match:
        return min(1.0, 0.85 + quality_bonus)
    if primary_match and not secondary_match:
        return min(0.75, 0.65 + quality_bonus)
    if analysis.primary in expected:
        return min(0.60, 0.50 + quality_bonus)
    return max(0.0, 0.25 - (0.05 * len([
        e for e in (analysis.primary, analysis.secondary) if e and e not in expected
    ])))


def _estimate_ordering_risk(
    state: EconomyState,
    analysis: EconomyAnalysis,
) -> float:
    """
    Estimate how sensitive the final composition is to facility placement order.

    High risk = the counts are very close between economies, meaning that
    placing one facility before another could flip primary/secondary.

    Returns 0.0–1.0.
    """
    ranked = analysis.ranked
    if len(ranked) < 2:
        return 0.0

    primary_pct   = ranked[0]['proportion'] if ranked else 0.0
    secondary_pct = ranked[1]['proportion'] if len(ranked) > 1 else 0.0

    # Tight gap = high ordering risk
    gap = primary_pct - secondary_pct
    if gap < 0.05:
        return 0.90   # almost tied — very order-sensitive
    if gap < 0.10:
        return 0.70
    if gap < 0.20:
        return 0.40
    return 0.15


def _build_recommendations(
    analysis: EconomyAnalysis,
    archetype_key: Optional[str],
    ordering_risk: float,
) -> list[str]:
    """Generate actionable, human-readable recommendations."""
    recs = []

    if not analysis.primary:
        recs.append('No economy-producing facilities placed yet. Add support facilities or a colony port.')
        return recs

    # Top-two alignment advice
    if analysis.top_two_alignment == 'solo':
        ideal_secondary = (IDEAL_PAIRS.get(analysis.primary) or ['Industrial'])[0]
        recs.append(
            f'Only {analysis.primary} economy present. '
            f'Add {ideal_secondary} facilities to create a productive economy pair '
            f'and unlock synergy bonuses.'
        )

    elif analysis.top_two_alignment == 'weak':
        recs.append(
            f'{analysis.secondary} is present but weak ({analysis.secondary_pct:.0%}). '
            f'Add more {analysis.secondary} support facilities to strengthen the pair.'
        )

    elif analysis.top_two_alignment == 'dominant':
        recs.append(
            f'{analysis.primary} dominates at {analysis.primary_pct:.0%}. '
            f'Consider adding more {analysis.secondary or "secondary"} facilities '
            f'to improve synergy and economy pair strength.'
        )

    # Ordering risk warning
    if ordering_risk > 0.65:
        recs.append(
            f'⚠ Placement order is critical — '
            f'{analysis.primary} and {analysis.secondary} are closely matched. '
            f'Place your intended primary economy facilities FIRST to lock in the '
            f'economy hierarchy before adding secondary facilities.'
        )

    # Contamination advice
    if analysis.has_contamination:
        for eco in analysis.contaminating:
            recs.append(
                f'⚠ {eco} economy is contaminating this build. '
                f'Review settlement placement — Colony/Prison/Damaged economies '
                f'dilute your intended composition.'
            )

    # Archetype-specific advice
    if archetype_key:
        expected = _archetype_to_economies(archetype_key)
        if expected and analysis.primary and analysis.primary not in expected:
            recs.append(
                f'Your primary economy ({analysis.primary}) does not match the '
                f'intended archetype ({archetype_key.replace("_", " / ").title()}). '
                f'Target economy: {" + ".join(expected)}.'
            )

    return recs


# Archetype → expected economies. Terraforming is intentionally not mapped to
# Industrial; it is a strategic tag in domain.colonisation_rules.
_ARCHETYPE_ECONOMY_MAP: dict[str, list[str]] = {
    'refinery_industrial':       ['Refinery', 'Industrial'],
    'extraction_refinery':       ['Extraction', 'Refinery'],
    'agriculture_terraforming':  ['Agriculture'],
    'hitech_tourism':            ['HighTech', 'Tourism'],
    'expansion_capital':         [],
    'trade_logistics':           ['Industrial', 'Extraction'],
    'population_capital':        ['Agriculture'],
    'ax_forward_base':           ['Military', 'Industrial'],
    'military_industrial':       ['Military', 'Industrial'],
    'flexible_multirole':        [],   # no specific pair
}


def _archetype_to_economies(archetype_key: str) -> list[str]:
    return _ARCHETYPE_ECONOMY_MAP.get(archetype_key, [])
