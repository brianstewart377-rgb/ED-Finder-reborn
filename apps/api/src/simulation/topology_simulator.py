"""
ED Finder — Simulation: Topology Simulator
============================================
Interprets system_slot_topology data in the context of a colony build.

Design rules:
  • Pure functions — no DB, no asyncio.
  • Input: topology row dict from system_slot_topology.
  • Output: structured topology context for use in buildability analysis.

This module bridges the topology build output (from build_topology.py)
into the simulation engine. It does NOT recalculate topology — it
interprets what's already stored and expresses it in simulation terms:

  "This system has a ringed gas giant → Asteroid Base is viable"
  "This system has a viable surface port location → T2 surface port possible"
  "This system has deep orbital anchor → stable T3 orbital viable"
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TopologyContext:
    """
    Simulation-ready interpretation of system_slot_topology.

    All fields are safe to pass to buildability and CP simulators.
    Defaults are conservative (assume worst case when data is missing).
    """
    orbital_slots:          int     = 0
    surface_slots:          int     = 0
    has_ringed_body:        bool    = False
    has_viable_surface:     bool    = True
    has_deep_anchor:        bool    = False
    orbital_synergy:        float   = 0.0
    ground_synergy:         float   = 0.0
    build_flexibility:      float   = 0.0
    contamination_risk:     float   = 0.0
    strong_link_potential:  float   = 0.0
    weak_link_stability:    float   = 0.0
    nesting_potential:      float   = 0.0

    # Derived slot confidence from topology
    slot_confidence:        float   = 0.5

    def to_dict(self) -> dict:
        return {
            'orbital_slots':          self.orbital_slots,
            'surface_slots':          self.surface_slots,
            'has_ringed_body':        self.has_ringed_body,
            'has_viable_surface':     self.has_viable_surface,
            'has_deep_anchor':        self.has_deep_anchor,
            'orbital_synergy':        round(self.orbital_synergy, 3),
            'ground_synergy':         round(self.ground_synergy, 3),
            'build_flexibility':      round(self.build_flexibility, 3),
            'contamination_risk':     round(self.contamination_risk, 3),
            'strong_link_potential':  round(self.strong_link_potential, 3),
            'weak_link_stability':    round(self.weak_link_stability, 3),
            'nesting_potential':      round(self.nesting_potential, 3),
            'slot_confidence':        round(self.slot_confidence, 3),
        }


def topology_from_row(topo_row: Optional[dict]) -> TopologyContext:
    """
    Build a TopologyContext from a system_slot_topology row dict.
    Safe with None input — returns conservative defaults.
    """
    if not topo_row:
        return TopologyContext()

    def _f(key: str, default: float = 0.0) -> float:
        v = topo_row.get(key)
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    def _i(key: str, default: int = 0) -> int:
        v = topo_row.get(key)
        try:
            return int(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    def _b(key: str, default: bool = False) -> bool:
        v = topo_row.get(key)
        if v is None:
            return default
        return bool(v)

    orbital_slots  = _i('estimated_orbital_slots')
    surface_slots  = _i('estimated_ground_slots')

    # Confidence: if topology was computed, it's at least moderate
    # (build_topology.py uses body data which has DSS/FSS confidence baked in)
    has_data = orbital_slots > 0 or surface_slots > 0
    slot_confidence = 0.65 if has_data else 0.30

    # Ringed body: inferred from has_ringed_gas_giant or topology trait
    has_ringed = _b('has_ringed_gas_giant') or _b('has_ringed_body')

    return TopologyContext(
        orbital_slots=orbital_slots,
        surface_slots=surface_slots,
        has_ringed_body=has_ringed,
        has_viable_surface=_b('has_viable_surface_port', default=True),
        has_deep_anchor=_b('has_deep_orbital_anchor'),
        orbital_synergy=_f('orbital_synergy'),
        ground_synergy=_f('ground_synergy'),
        build_flexibility=_f('build_flexibility'),
        contamination_risk=_f('contamination_risk'),
        strong_link_potential=_f('strong_link_potential'),
        weak_link_stability=_f('weak_link_stability'),
        nesting_potential=_f('nesting_potential'),
        slot_confidence=slot_confidence,
    )


def topology_from_traits(traits_row: Optional[dict]) -> TopologyContext:
    """
    Do not derive slot counts from system_archetype_traits.

    Stage 17G makes the validated slot predictor the only runtime source for
    predicted capacity. This legacy adapter remains as a safe compatibility
    no-op so an accidental caller cannot reintroduce trait-derived estimates.
    """
    return TopologyContext(slot_confidence=0.0)


def summarise_topology(ctx: TopologyContext) -> list[str]:
    """
    Generate human-readable summary points about the system topology.
    Used in API response explanations.
    """
    points: list[str] = []
    total = ctx.orbital_slots + ctx.surface_slots

    if total == 0:
        points.append('No slot data available — topology not yet computed for this system.')
        return points

    points.append(
        f'{ctx.orbital_slots} orbital slot{"s" if ctx.orbital_slots != 1 else ""} + '
        f'{ctx.surface_slots} surface slot{"s" if ctx.surface_slots != 1 else ""} '
        f'= {total} total buildable locations.'
    )

    if ctx.has_ringed_body:
        points.append(
            'Ringed body present — Asteroid Base placement viable '
            '(Extraction economy anchor, strong orbital slot use).'
        )

    if ctx.has_deep_anchor:
        points.append(
            'Deep orbital anchor detected — stable, high-value orbital insertion point '
            'ideal for a primary T2/T3 port.'
        )

    if ctx.orbital_synergy > 0.6:
        points.append(
            f'High orbital synergy ({ctx.orbital_synergy:.2f}) — '
            'orbital facilities will reinforce each other effectively.'
        )

    if ctx.contamination_risk > 0.5:
        points.append(
            f'⚠ Elevated contamination risk ({ctx.contamination_risk:.2f}) — '
            'facility placement order matters here. '
            'Lock primary economy early to avoid composition dilution.'
        )

    if ctx.build_flexibility > 0.6:
        points.append(
            f'High build flexibility ({ctx.build_flexibility:.2f}) — '
            'this system supports a variety of build strategies.'
        )
    elif ctx.build_flexibility < 0.3 and total > 0:
        points.append(
            f'Low build flexibility ({ctx.build_flexibility:.2f}) — '
            'limited slot variety constrains viable build paths.'
        )

    if ctx.slot_confidence < 0.5:
        points.append(
            f'⚠ Slot confidence: {ctx.slot_confidence:.0%} — '
            'estimates based on limited body scan data. '
            'Actual slot counts confirmed only in-game.'
        )

    return points
