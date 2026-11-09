"""
ED Finder — Simulation: Buildability Analysis
===============================================
Analyses how buildable a system actually is — the gap between
"theoretically good archetype score" and "practically achievable build".

Design rules:
  • Pure functions — no DB, no asyncio.
  • Combines slot predictions + CP analysis + composition analysis.
  • Output is consumed by the API layer and stored in buildability_analysis.
  • Complexity labels are human-readable and actionable.

The core question this module answers:
  "Given what we know about this system's bodies and CP budget,
   what is the BEST build actually achievable?"

Separation from archetype scores:
  Archetype scores answer: "Is this system theoretically good?"
  Buildability answers:    "Can you actually build what you want here?"

A system can score highly for Refinery/Industrial but have only 3 orbital
slots — making a full T3 megacomplex impossible. Buildability captures this.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from edfinder_api.domain.facilities import FacilityTemplate, get_catalogue, is_colony_ship
from edfinder_api.mechanics.cp_rules import t2_port_yellow_cost
from edfinder_api.simulation.cp_simulator import (
    CPAnalysis, analyse_cp_budget, complexity_label,
)


SIMULATION_VERSION = '1.0.0'


@dataclass
class BuildabilityResult:
    """
    Full buildability analysis for a system.
    Stored in buildability_analysis table and returned by the API.
    """
    system_id64:                int

    # Slot summary
    estimated_orbital_slots:    int
    estimated_ground_slots:     int
    slot_confidence:            float

    # CP capacity
    estimated_yellow_cp:        int
    estimated_green_cp:         int
    max_t2_ports:               int
    max_t3_ports:               int

    # Risk scores (0–100)
    cp_bottleneck_score:        float
    slot_exhaustion_risk:       float
    build_order_sensitivity:    float

    # Complexity
    build_complexity:           str

    # Structured output
    bottlenecks:                list[dict]
    opportunities:              list[dict]
    recommended_build_order:    list[dict]
    warnings:                   list[str]

    def to_dict(self) -> dict:
        return {
            'system_id64':               self.system_id64,
            'estimated_orbital_slots':   self.estimated_orbital_slots,
            'estimated_ground_slots':    self.estimated_ground_slots,
            'slot_confidence':           round(self.slot_confidence, 3),
            'slot_confidence_label':     _confidence_label(self.slot_confidence),
            'estimated_yellow_cp':       self.estimated_yellow_cp,
            'estimated_green_cp':        self.estimated_green_cp,
            'max_t2_ports':              self.max_t2_ports,
            'max_t3_ports':              self.max_t3_ports,
            'cp_bottleneck_score':       round(self.cp_bottleneck_score, 1),
            'slot_exhaustion_risk':      round(self.slot_exhaustion_risk, 1),
            'build_order_sensitivity':   round(self.build_order_sensitivity, 1),
            'build_complexity':          self.build_complexity,
            'bottlenecks':               self.bottlenecks,
            'opportunities':             self.opportunities,
            'recommended_build_order':   self.recommended_build_order,
            'warnings':                  self.warnings,
        }

    def to_db_row(self) -> dict:
        """Dict for upserting into buildability_analysis."""
        return {
            'system_id64':                 self.system_id64,
            'estimated_orbital_slots':     self.estimated_orbital_slots,
            'estimated_ground_slots':      self.estimated_ground_slots,
            'slot_confidence':             self.slot_confidence,
            'estimated_yellow_cp_capacity': self.estimated_yellow_cp,
            'estimated_green_cp_capacity':  self.estimated_green_cp,
            'max_t2_ports_estimate':       self.max_t2_ports,
            'max_t3_ports_estimate':       self.max_t3_ports,
            'cp_bottleneck_score':         self.cp_bottleneck_score,
            'slot_exhaustion_risk':        self.slot_exhaustion_risk,
            'build_order_sensitivity':     self.build_order_sensitivity,
            'build_complexity':            self.build_complexity,
            'bottlenecks':                 self.bottlenecks,
            'opportunities':               self.opportunities,
            'recommended_build_order':     self.recommended_build_order,
        }


def analyse_buildability(
    system_id64: int,
    orbital_slots: int,
    surface_slots: int,
    slot_confidence: float,
    has_ringed_body: bool = False,
    has_viable_surface: bool = True,
    has_deep_anchor: bool = False,
    archetype_key: Optional[str] = None,
    topo_row: Optional[dict] = None,
) -> BuildabilityResult:
    """
    Main entry point for buildability analysis.

    Produces a complete BuildabilityResult from system topology data.
    Does NOT require body_scan_facts — works from slot estimates alone.
    """
    catalogue = get_catalogue()
    all_facilities = list(catalogue.values())

    # Select a representative set of support facilities for this archetype
    target_facilities = _select_facilities_for_archetype(
        archetype_key, all_facilities
    )

    # CP analysis
    cp: CPAnalysis = analyse_cp_budget(
        facilities=target_facilities,
        orbital_slots=orbital_slots,
        surface_slots=surface_slots,
        has_ringed_body=has_ringed_body,
    )

    # ── Slot exhaustion risk ──────────────────────────────────────────────
    total_slots = orbital_slots + surface_slots
    if total_slots == 0:
        slot_exhaustion_risk = 100.0
    elif cp.max_t2_ports_affordable >= total_slots:
        # CP is not the limit — slots might be
        slot_exhaustion_risk = min(100.0, max(0.0,
            100.0 - (total_slots / max(1, _target_port_count(archetype_key))) * 40.0
        ))
    else:
        # Already CP-limited; slot exhaustion is secondary
        slot_exhaustion_risk = 20.0

    # ── Build order sensitivity ───────────────────────────────────────────
    # High if: T3 ports present (order matters a lot), or tight CP budget
    t3_viable = cp.max_t3_ports_affordable > 0
    cp_tightness = min(100.0, max(0.0,
        100.0 - (cp.yellow_generated / max(1, _expected_yellow_need(total_slots))) * 100.0
    ))
    build_order_sensitivity = min(100.0,
        (50.0 if t3_viable else 10.0) + cp_tightness * 0.5
    )

    # ── Complexity label ──────────────────────────────────────────────────
    complexity = complexity_label(
        cp_bottleneck_score=cp.cp_bottleneck_score,
        slot_exhaustion_risk=slot_exhaustion_risk,
        build_order_sensitivity=build_order_sensitivity,
        t3_ports=cp.max_t3_ports_affordable,
    )

    # ── Opportunities ─────────────────────────────────────────────────────
    opportunities = list(cp.opportunities)
    if topo_row:
        if topo_row.get('has_viable_surface_port'):
            opportunities.append({
                'type':   'surface_port_viable',
                'detail': 'Topology confirms viable surface port location.',
            })
        if topo_row.get('has_deep_orbital_anchor'):
            opportunities.append({
                'type':   'deep_anchor',
                'detail': 'Deep orbital anchor detected — stable high-value orbital slot.',
            })
        strong_link = topo_row.get('strong_link_potential', 0) or 0
        if strong_link > 0.6:
            opportunities.append({
                'type':   'strong_link',
                'detail': f'Strong economy link potential ({strong_link:.2f}) — ideal for paired economy builds.',
            })

    # ── Warnings ──────────────────────────────────────────────────────────
    warnings: list[str] = []
    if slot_confidence < 0.5:
        warnings.append(
            f'Slot estimates have low confidence ({slot_confidence:.0%}) — '
            'based on limited body scan data. Actual slot counts may differ significantly.'
        )
    if total_slots < 4:
        warnings.append(
            f'Only {total_slots} total slots estimated — '
            'very limited build capacity. Consider this system for a support role only.'
        )
    if orbital_slots == 0:
        warnings.append(
            'No orbital slots estimated — cannot place orbital ports. '
            'Surface-only colony with significant limitations.'
        )

    # ── Recommended build order ───────────────────────────────────────────
    recommended_build_order = _generate_build_order(
        archetype_key=archetype_key,
        orbital_slots=orbital_slots,
        surface_slots=surface_slots,
        has_ringed_body=has_ringed_body,
        cp=cp,
        catalogue=catalogue,
    )

    return BuildabilityResult(
        system_id64=system_id64,
        estimated_orbital_slots=orbital_slots,
        estimated_ground_slots=surface_slots,
        slot_confidence=slot_confidence,
        estimated_yellow_cp=cp.yellow_generated,
        estimated_green_cp=cp.green_generated,
        max_t2_ports=cp.max_t2_ports_affordable,
        max_t3_ports=cp.max_t3_ports_affordable,
        cp_bottleneck_score=cp.cp_bottleneck_score,
        slot_exhaustion_risk=round(slot_exhaustion_risk, 1),
        build_order_sensitivity=round(build_order_sensitivity, 1),
        build_complexity=complexity,
        bottlenecks=cp.bottlenecks,
        opportunities=opportunities,
        recommended_build_order=recommended_build_order,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _select_facilities_for_archetype(
    archetype_key: Optional[str],
    all_facilities: list[FacilityTemplate],
) -> list[FacilityTemplate]:
    """
    Select a representative facility set for CP analysis.
    Uses the archetype's preferred economy to choose support facilities.
    """
    from edfinder_api.simulation.composition import _archetype_to_economies
    target_economies = _archetype_to_economies(archetype_key or '') if archetype_key else []

    selected: list[FacilityTemplate] = []

    # Always include colony ship
    colony_ships = [f for f in all_facilities if is_colony_ship(f)]
    selected.extend(colony_ships[:1])

    # Include support facilities for target economies
    for eco in target_economies[:2]:
        eco_facilities = [
            f for f in all_facilities
            if f.economy == eco and f.is_support_facility
        ]
        selected.extend(eco_facilities[:3])

    # Fill with generic support facilities if needed
    if len(selected) < 5:
        generic = [
            f for f in all_facilities
            if f.is_support_facility and f not in selected
        ]
        selected.extend(generic[:5 - len(selected)])

    return selected


def _target_port_count(archetype_key: Optional[str]) -> int:
    """Approximate number of ports needed for this archetype."""
    _COUNTS = {
        'refinery_industrial':      6,
        'extraction_refinery':      5,
        'agriculture_terraforming': 4,
        'hitech_tourism':           5,
        'expansion_capital':        6,
        'trade_logistics':          5,
        'population_capital':       4,
        'ax_forward_base':          4,
        'military_industrial':      5,
        'flexible_multirole':       4,
    }
    return _COUNTS.get(archetype_key or '', 4)


def _expected_yellow_need(total_slots: int) -> int:
    """Expected yellow CP needed to fill all slots with T2 ports."""
    total = sum(t2_port_yellow_cost(n) for n in range(1, min(total_slots, 6) + 1))
    return total or 1


def _generate_build_order(
    archetype_key: Optional[str],
    orbital_slots: int,
    surface_slots: int,
    has_ringed_body: bool,
    cp: CPAnalysis,
    catalogue: dict[str, FacilityTemplate],
) -> list[dict]:
    """
    Generate a recommended step-by-step build order.

    Strategy:
      1. Colony ship / initial settlement (T1)
      2. Support facilities to build CP
      3. T2 orbital port (if slots available)
      4. More support facilities
      5. Additional T2 ports
      6. T3 port (if CP and slots allow)

    Returns list of step dicts.
    """
    from edfinder_api.simulation.composition import _archetype_to_economies
    steps: list[dict] = []
    step = 1

    target_economies = _archetype_to_economies(archetype_key or '') if archetype_key else []
    primary_eco   = target_economies[0] if target_economies else 'Industrial'
    secondary_eco = target_economies[1] if len(target_economies) > 1 else 'Refinery'

    # Step 1: Colony establishment
    steps.append({
        'step':        step,
        'action':      'Establish colony',
        'facility':    'colony_ship',
        'location':    'orbital',
        'reason':      'Required first placement — establishes the system as a colony.',
        'economy_effect': 'Colony (temporary)',
    })
    step += 1

    # Step 2: Primary economy support
    primary_support = [
        f for f in catalogue.values()
        if f.economy == primary_eco and f.is_support_facility
    ]
    for f in primary_support[:2]:
        steps.append({
            'step':        step,
            'action':      'Build primary economy support',
            'facility':    f.id,
            'location':    'surface' if f.needs_surface else 'orbital_or_surface',
            'reason':      f'Generates {f.yellow_cp_generated} yellow CP and establishes {primary_eco} economy.',
            'economy_effect': primary_eco,
        })
        step += 1

    # Step 3: First T2 orbital port
    if orbital_slots > 0:
        # Prefer ringed body for Asteroid Base if available
        if has_ringed_body and 'extraction' in (archetype_key or '').lower():
            steps.append({
                'step':        step,
                'action':      'Place Asteroid Base (T2)',
                'facility':    'asteroid_base',
                'location':    'ringed_orbital',
                'reason':      'Ringed body available — Asteroid Base provides Extraction economy anchor and strong orbital slot use.',
                'economy_effect': 'Extraction',
            })
        else:
            steps.append({
                'step':        step,
                'action':      'Place first T2 orbital port',
                'facility':    'coriolis_station',
                'location':    'orbital',
                'reason':      'First T2 port is cheapest — place early while CP budget is healthy.',
                'economy_effect': primary_eco,
            })
        step += 1

    # Step 4: Secondary economy support
    secondary_support = [
        f for f in catalogue.values()
        if f.economy == secondary_eco and f.is_support_facility
    ]
    for f in secondary_support[:2]:
        steps.append({
            'step':        step,
            'action':      'Build secondary economy support',
            'facility':    f.id,
            'location':    'surface' if f.needs_surface else 'orbital_or_surface',
            'reason':      f'Adds {secondary_eco} economy — creates the target {primary_eco}/{secondary_eco} pair.',
            'economy_effect': secondary_eco,
        })
        step += 1

    # Step 5: Additional T2 ports (up to CP limit)
    additional_t2 = min(cp.max_t2_ports_affordable - 1, orbital_slots + surface_slots - 2)
    for i in range(max(0, additional_t2)):
        steps.append({
            'step':        step,
            'action':      f'Place T2 port #{i + 2}',
            'facility':    'orbis_station',
            'location':    'orbital',
            'reason':      f'Escalating cost — place at step {step} to maintain CP headroom.',
            'economy_effect': f'{primary_eco} or {secondary_eco} (set by support facilities)',
        })
        step += 1

    # Step 6: T3 port if viable
    if cp.max_t3_ports_affordable > 0:
        steps.append({
            'step':        step,
            'action':      'Place T3 primary port',
            'facility':    'orbis_t3',
            'location':    'orbital',
            'reason':      (
                'T3 port placed last — highest CP cost. '
                'By this point your economy composition is locked by earlier placements. '
                'Ensure primary economy is firmly established before this step.'
            ),
            'economy_effect': f'{primary_eco} (locked by earlier facilities)',
        })
        step += 1

    return steps


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.85:
        return 'High'
    if confidence >= 0.65:
        return 'Moderate'
    if confidence >= 0.45:
        return 'Low'
    return 'Estimated'
