"""
ED Finder — Domain: Economy State
===================================
Models the economy composition of a system at a point in simulation.

Design rules:
  • Pure Python — no DB, no FastAPI, no asyncio.
  • Composable: EconomyState is built up incrementally as facilities are placed.
  • Deterministic: same inputs → same outputs, always.
  • Explainable: every state change records what caused it.

The core insight ED colonisation mechanics impose:
  Economy proportion is determined by the RATIO of economy-producing
  facilities, not by explicit assignment. The ORDER of placement affects
  which economies become primary vs secondary when the system is settled.

Separation:
  FacilityTemplate  (facilities.py) — what a facility IS
  FacilityPlacement (placements.py) — where a facility IS PLACED
  EconomyState      (this file)     — what the economy LOOKS LIKE now
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Economy ordering — better compositions score higher
# ---------------------------------------------------------------------------

# Ideal "top-two" pairings for each primary archetype.
# Key = primary economy, value = list of preferred secondaries (best first).
IDEAL_PAIRS: dict[str, list[str]] = {
    'Refinery':     ['Industrial', 'Extraction'],
    'Industrial':   ['Refinery', 'Military'],
    'Extraction':   ['Refinery', 'Industrial'],
    'Agriculture':  ['Industrial', 'Tourism'],
    'HighTech':     ['Industrial', 'Tourism'],
    'Military':     ['Industrial', 'HighTech'],
    'Tourism':      ['HighTech', 'Agriculture'],
}

# Economies that contaminate a build if they appear unexpectedly
# (e.g. Colony leaking in from an unplaced settlement)
CONTAMINATION_RISK_ECONOMIES = {'Colony', 'Prison', 'Damaged', 'Rescue', 'Repair', 'None'}


@dataclass
class EconomyState:
    """
    Tracks economy composition during a colony simulation.

    economy_counts: dict mapping economy name → number of contributing facilities
    history:        list of change records for explainability

    Usage:
        state = EconomyState()
        state.add('Refinery', facility_id='refinery', step=1)
        state.add('Industrial', facility_id='industrial_facility', step=2)
        result = state.analyse()
    """
    economy_counts: dict[str, int]   = field(default_factory=dict)
    history:        list[dict]       = field(default_factory=list)

    def add(
        self,
        economy: str,
        facility_id: str = '',
        step: int = 0,
        weight: float = 1.0,
    ) -> None:
        """
        Register that a placed facility contributes `economy`.
        weight allows fractional contributions (e.g. 0.5 for weak contributors).
        """
        if not economy or economy in ('None', 'Unknown', ''):
            return
        current = self.economy_counts.get(economy, 0.0)
        self.economy_counts[economy] = current + weight
        self.history.append({
            'step':        step,
            'facility_id': facility_id,
            'economy':     economy,
            'weight':      weight,
        })

    def total_weight(self) -> float:
        return sum(self.economy_counts.values())

    def proportions(self) -> dict[str, float]:
        """Economy → proportion (0.0–1.0). Sums to 1.0."""
        total = self.total_weight()
        if total == 0:
            return {}
        return {
            eco: round(count / total, 4)
            for eco, count in self.economy_counts.items()
        }

    def ranked(self) -> list[tuple[str, float]]:
        """Returns [(economy, proportion)] sorted by proportion DESC."""
        props = self.proportions()
        return sorted(props.items(), key=lambda x: x[1], reverse=True)

    def primary(self) -> Optional[str]:
        ranked = self.ranked()
        return ranked[0][0] if ranked else None

    def secondary(self) -> Optional[str]:
        ranked = self.ranked()
        return ranked[1][0] if len(ranked) > 1 else None

    def tertiary(self) -> Optional[str]:
        ranked = self.ranked()
        return ranked[2][0] if len(ranked) > 2 else None

    def has_contamination(self) -> bool:
        return bool(CONTAMINATION_RISK_ECONOMIES & set(self.economy_counts.keys()))

    def contaminating_economies(self) -> list[str]:
        return [e for e in self.economy_counts if e in CONTAMINATION_RISK_ECONOMIES]

    def analyse(self) -> 'EconomyAnalysis':
        """
        Produce a full composition analysis.
        This is the main output consumed by simulation and API layers.
        """
        ranked = self.ranked()
        if not ranked:
            return EconomyAnalysis(
                primary=None, secondary=None, tertiary=None,
                primary_pct=0.0, secondary_pct=0.0,
                top_two_pct=0.0,
                top_two_alignment='none',
                pair_quality_score=0.0,
                composition_quality=0.0,
                has_contamination=False,
                contaminating=[], ranked=[], warnings=[],
            )

        primary    = ranked[0][0] if len(ranked) > 0 else None
        primary_pct = ranked[0][1] if len(ranked) > 0 else 0.0
        secondary   = ranked[1][0] if len(ranked) > 1 else None
        secondary_pct = ranked[1][1] if len(ranked) > 1 else 0.0
        tertiary    = ranked[2][0] if len(ranked) > 2 else None
        top_two_pct = primary_pct + secondary_pct

        # Pair quality: how well does the top-two match the ideal?
        pair_quality = _score_pair_quality(primary, secondary, primary_pct, secondary_pct)

        # Dominance balance: penalise if primary completely dominates (no synergy)
        dominance_penalty = max(0.0, primary_pct - 0.65) * 0.5

        # Tertiary contamination: penalise if a third economy is eating share
        tertiary_penalty = 0.0
        tertiary_pct = ranked[2][1] if len(ranked) > 2 else 0.0
        if tertiary and tertiary_pct > 0.12:
            tertiary_penalty = (tertiary_pct - 0.12) * 0.8

        # Contamination: hard penalty for toxic economies leaking in
        contaminating = self.contaminating_economies()
        contamination_penalty = 0.25 * len(contaminating)

        composition_quality = max(0.0, min(1.0,
            pair_quality
            - dominance_penalty
            - tertiary_penalty
            - contamination_penalty
        ))

        warnings: list[str] = []
        if dominance_penalty > 0.05:
            warnings.append(
                f'{primary} dominates at {primary_pct:.0%} — secondary economy '
                f'has minimal synergy impact. Add more {secondary or "secondary"} facilities.'
            )
        if tertiary_penalty > 0.05 and tertiary:
            warnings.append(
                f'{tertiary} is leaking in at {tertiary_pct:.0%} — '
                'this will dilute your primary/secondary pair composition.'
            )
        for eco in contaminating:
            warnings.append(
                f'Contamination: {eco} economy present — '
                'may corrupt intended economy composition.'
            )

        return EconomyAnalysis(
            primary=primary,
            secondary=secondary,
            tertiary=tertiary,
            primary_pct=round(primary_pct, 4),
            secondary_pct=round(secondary_pct, 4),
            top_two_pct=round(top_two_pct, 4),
            top_two_alignment=_alignment_label(primary_pct, secondary_pct),
            pair_quality_score=round(pair_quality, 4),
            composition_quality=round(composition_quality, 4),
            has_contamination=bool(contaminating),
            contaminating=contaminating,
            ranked=[{'economy': e, 'proportion': p} for e, p in ranked],
            warnings=warnings,
        )


@dataclass
class EconomyAnalysis:
    """
    Output of EconomyState.analyse().
    Consumed by simulation layer and serialised directly to API responses.
    """
    primary:             Optional[str]
    secondary:           Optional[str]
    tertiary:            Optional[str]
    primary_pct:         float    # 0.0–1.0
    secondary_pct:       float
    top_two_pct:         float    # primary + secondary combined
    top_two_alignment:   str      # 'dominant', 'balanced', 'weak', 'solo', 'none'
    pair_quality_score:  float    # 0.0–1.0, how good is the pair
    composition_quality: float    # 0.0–1.0, overall composition score
    has_contamination:   bool
    contaminating:       list[str]
    ranked:              list[dict]   # [{'economy': str, 'proportion': float}]
    warnings:            list[str]

    def to_dict(self) -> dict:
        return {
            'primary':             self.primary,
            'secondary':           self.secondary,
            'tertiary':            self.tertiary,
            'primary_pct':         self.primary_pct,
            'secondary_pct':       self.secondary_pct,
            'top_two_pct':         self.top_two_pct,
            'top_two_alignment':   self.top_two_alignment,
            'pair_quality_score':  self.pair_quality_score,
            'composition_quality': self.composition_quality,
            'has_contamination':   self.has_contamination,
            'contaminating':       self.contaminating,
            'ranked':              self.ranked,
            'warnings':            self.warnings,
        }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _score_pair_quality(
    primary: Optional[str],
    secondary: Optional[str],
    primary_pct: float,
    secondary_pct: float,
) -> float:
    """
    Score how good the top-two economy pairing is.

    Rules:
      • Ideal pair for this primary  → 1.0 base
      • Acceptable pair              → 0.7 base
      • Unknown primary              → 0.4 base
      • Bonus for balanced split (35-50% each)
      • Penalty for very lopsided split (>65% primary)
    """
    if not primary:
        return 0.0

    ideal_secondaries = IDEAL_PAIRS.get(primary, [])
    if secondary and secondary in ideal_secondaries:
        idx = ideal_secondaries.index(secondary)
        # Best secondary = 1.0, second-best = 0.85
        base = 1.0 if idx == 0 else 0.85
    elif secondary:
        base = 0.55  # non-ideal but has a secondary
    else:
        base = 0.30  # no secondary economy at all

    # Balance bonus: reward a genuine 40/40 split
    balance = 1.0 - abs(primary_pct - secondary_pct) if secondary else 0.0
    balance_bonus = max(0.0, balance - 0.3) * 0.15

    # Lopsidedness penalty
    lopsided_penalty = max(0.0, primary_pct - 0.60) * 0.3

    return max(0.0, min(1.0, base + balance_bonus - lopsided_penalty))


def _alignment_label(primary_pct: float, secondary_pct: float) -> str:
    """
    Human-readable label for how the top-two economies are split.

    dominant  — primary >65%, secondary exists but weak
    balanced  — both between 30-55%
    weak      — secondary exists but <20%
    solo      — no meaningful secondary
    none      — no economies at all
    """
    if primary_pct == 0:
        return 'none'
    if secondary_pct < 0.05:
        return 'solo'
    if secondary_pct < 0.20:
        return 'weak'
    if primary_pct > 0.65:
        return 'dominant'
    return 'balanced'
