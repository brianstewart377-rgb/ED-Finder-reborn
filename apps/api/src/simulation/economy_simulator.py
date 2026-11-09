"""
ED Finder — Simulation: Economy Simulator
==========================================
Simulates economy evolution as facilities are placed in sequence.

Design rules:
  • Pure functions — no DB, no asyncio.
  • Wraps domain/economy_state.py with simulation-step tracking.
  • Produces economy snapshots at each build step for timeline display.

The key insight this module captures:
  Economy composition is NOT static — it changes with every facility placed.
  Placing a Refinery before an Industrial facility means Refinery becomes
  primary. Placing Industrial first flips it. This module makes that
  explicit and warns when ordering creates unintended compositions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from edfinder_api.domain.economy_state import EconomyAnalysis, EconomyState
from edfinder_api.domain.facilities import FacilityTemplate
from edfinder_api.domain.placements import FacilityPlacement


@dataclass
class EconomySnapshot:
    """Economy state after a single build step."""
    step:             int
    facility_id:      str
    facility_name:    str
    economy_added:    Optional[str]
    primary:          Optional[str]
    secondary:        Optional[str]
    primary_pct:      float
    secondary_pct:    float
    composition_quality: float

    def to_dict(self) -> dict:
        return {
            'step':                self.step,
            'facility_id':         self.facility_id,
            'facility_name':       self.facility_name,
            'economy_added':       self.economy_added,
            'primary':             self.primary,
            'secondary':           self.secondary,
            'primary_pct':         round(self.primary_pct, 3),
            'secondary_pct':       round(self.secondary_pct, 3),
            'composition_quality': round(self.composition_quality, 3),
        }


@dataclass
class EconomySimulation:
    """
    Full economy simulation result: snapshots at every step + final analysis.
    """
    snapshots:           list[EconomySnapshot]
    final_analysis:      EconomyAnalysis
    flip_warnings:       list[dict]
    # flip_warnings = steps where the primary economy changed unexpectedly

    def to_dict(self) -> dict:
        return {
            'snapshots':      [s.to_dict() for s in self.snapshots],
            'final':          self.final_analysis.to_dict(),
            'flip_warnings':  self.flip_warnings,
        }


def simulate_economy_evolution(
    placements: list[FacilityPlacement],
) -> EconomySimulation:
    """
    Simulate how the economy composition evolves as facilities are placed
    in sequence.

    Returns snapshots at every step + flip warnings where primary/secondary
    swapped unexpectedly.
    """
    state     = EconomyState()
    snapshots: list[EconomySnapshot] = []
    prev_primary: Optional[str] = None
    flip_warnings: list[dict] = []

    for placement in sorted(placements, key=lambda p: p.step):
        f = placement.facility
        if f.economy:
            state.add(
                economy=f.economy,
                facility_id=f.id,
                step=placement.step,
                weight=_economy_weight(f),
            )

        analysis = state.analyse()
        snapshot = EconomySnapshot(
            step=placement.step,
            facility_id=f.id,
            facility_name=f.name,
            economy_added=f.economy,
            primary=analysis.primary,
            secondary=analysis.secondary,
            primary_pct=analysis.primary_pct,
            secondary_pct=analysis.secondary_pct,
            composition_quality=analysis.composition_quality,
        )
        snapshots.append(snapshot)

        # Detect primary economy flip
        if prev_primary and analysis.primary and analysis.primary != prev_primary:
            flip_warnings.append({
                'step':          placement.step,
                'facility':      f.name,
                'was_primary':   prev_primary,
                'now_primary':   analysis.primary,
                'message': (
                    f'⚠ Economy flipped at step {placement.step}: '
                    f'{prev_primary} → {analysis.primary} after placing {f.name}. '
                    f'If {prev_primary} was your intended primary, '
                    f'place its facilities earlier in the sequence.'
                ),
            })

        if analysis.primary:
            prev_primary = analysis.primary

    final_analysis = state.analyse()

    return EconomySimulation(
        snapshots=snapshots,
        final_analysis=final_analysis,
        flip_warnings=flip_warnings,
    )


def _economy_weight(facility: FacilityTemplate) -> float:
    if facility.is_port and not facility.is_colony_port:
        return 2.0
    return 1.0
