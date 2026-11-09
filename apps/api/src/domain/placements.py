"""
ED Finder — Domain: Facility Placements
=========================================
Models a single facility placement within a colony simulation.

Design rules:
  • Pure Python dataclasses — no DB, no FastAPI, no asyncio.
  • FacilityPlacement is the unit of simulation: one placed facility.
  • PlacementContext carries the system constraints that placements are
    validated against (slot availability, ringed bodies, etc.).
  • All validation returns structured results — never raises for domain errors.

Separation:
  FacilityTemplate  (facilities.py)    — what a facility IS
  FacilityPlacement (this file)        — where a facility IS PLACED
  EconomyState      (economy_state.py) — what the economy LOOKS LIKE
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from edfinder_api.domain.facilities import FacilityTemplate


# ---------------------------------------------------------------------------
# Placement location
# ---------------------------------------------------------------------------
PLACEMENT_ORBITAL  = 'orbital'
PLACEMENT_SURFACE  = 'surface'


@dataclass
class FacilityPlacement:
    """
    A single facility placed within a colony simulation step.

    step         — 1-based order in the build sequence
    facility     — the template being placed
    location     — 'orbital' or 'surface'
    body_id      — optional: which body this is placed around/on
    notes        — optional human-readable annotation
    """
    step:      int
    facility:  FacilityTemplate
    location:  str                   # PLACEMENT_ORBITAL | PLACEMENT_SURFACE
    body_id:   Optional[int] = None
    notes:     str = ''

    @property
    def uses_orbital_slot(self) -> bool:
        return self.location == PLACEMENT_ORBITAL

    @property
    def uses_surface_slot(self) -> bool:
        return self.location == PLACEMENT_SURFACE

    def to_dict(self) -> dict:
        return {
            'step':         self.step,
            'facility_id':  self.facility.id,
            'facility_name': self.facility.name,
            'location':     self.location,
            'body_id':      self.body_id,
            'economy':      self.facility.economy,
            'tier':         self.facility.tier,
            'is_port':      self.facility.is_port,
            'yellow_cp_generated': self.facility.yellow_cp_generated,
            'green_cp_generated':  self.facility.green_cp_generated,
            'yellow_cp_cost':      self.facility.yellow_cp_cost,
            'green_cp_cost':       self.facility.green_cp_cost,
            'notes':        self.notes,
        }


@dataclass
class PlacementContext:
    """
    System constraints that placements are validated against.

    Constructed from:
      • buildability_analysis (slot counts)
      • system_slot_topology  (ringed bodies, anchor flags)
      • system_archetype_scores (primary archetype)
    """
    system_id64:          int
    orbital_slots:        int
    surface_slots:        int
    has_ringed_body:      bool      = False
    has_viable_surface:   bool      = True
    has_deep_anchor:      bool      = False
    slot_confidence:      float     = 0.5

    # Running totals — updated as placements are added
    used_orbital_slots:   int       = 0
    used_surface_slots:   int       = 0

    @property
    def remaining_orbital(self) -> int:
        return max(0, self.orbital_slots - self.used_orbital_slots)

    @property
    def remaining_surface(self) -> int:
        return max(0, self.surface_slots - self.used_surface_slots)

    @property
    def remaining_total(self) -> int:
        return self.remaining_orbital + self.remaining_surface

    def can_place_orbital(self) -> bool:
        return self.remaining_orbital > 0

    def can_place_surface(self) -> bool:
        return self.remaining_surface > 0

    def can_place_ringed(self) -> bool:
        return self.has_ringed_body and self.can_place_orbital()

    def consume_slot(self, location: str) -> None:
        """Mutate context after a placement is committed."""
        if location == PLACEMENT_ORBITAL:
            self.used_orbital_slots += 1
        else:
            self.used_surface_slots += 1


@dataclass
class PlacementValidation:
    """Result of validating a proposed placement against a PlacementContext."""
    valid:    bool
    reason:   str = ''
    warnings: list[str] = field(default_factory=list)


def validate_placement(
    facility: FacilityTemplate,
    location: str,
    context: PlacementContext,
) -> PlacementValidation:
    """
    Validate that a facility can be placed in the given location
    given the current system context.

    Returns PlacementValidation — never raises.
    """
    warnings: list[str] = []

    # ── Location feasibility ──────────────────────────────────────────────
    if location == PLACEMENT_ORBITAL:
        if not facility.can_go_orbital:
            return PlacementValidation(
                False,
                f'{facility.name} cannot be placed in orbit '
                f'(allowed: {facility.allowed_location})'
            )
        if facility.needs_ringed_body and not context.has_ringed_body:
            return PlacementValidation(
                False,
                f'{facility.name} requires a ringed body — none present in this system.'
            )
        if context.remaining_orbital == 0:
            return PlacementValidation(
                False,
                f'No orbital slots remaining '
                f'({context.orbital_slots} total, {context.used_orbital_slots} used).'
            )
        if context.slot_confidence < 0.5:
            warnings.append(
                f'Orbital slot count has low confidence ({context.slot_confidence:.0%}) — '
                'actual slot availability may differ.'
            )

    elif location == PLACEMENT_SURFACE:
        if not facility.can_go_surface:
            return PlacementValidation(
                False,
                f'{facility.name} cannot be placed on a surface '
                f'(allowed: {facility.allowed_location})'
            )
        if not context.has_viable_surface:
            return PlacementValidation(
                False,
                'No viable landable surface in this system.'
            )
        if context.remaining_surface == 0:
            return PlacementValidation(
                False,
                f'No surface slots remaining '
                f'({context.surface_slots} total, {context.used_surface_slots} used).'
            )

    else:
        return PlacementValidation(False, f'Unknown location: {location!r}')

    return PlacementValidation(True, warnings=warnings)


def choose_location(
    facility: FacilityTemplate,
    context: PlacementContext,
) -> Optional[str]:
    """
    Auto-select the best location for a facility given context.
    Returns None if no valid location exists.

    Strategy:
      • Forced locations (orbital-only, surface-only) → use that or fail.
      • Orbital-or-surface → prefer orbital if slots available and ringed
        bodies present (for anchor facilities); else prefer surface.
      • Ringed-orbital → orbital only if ringed body present.
    """
    if facility.needs_orbital:
        if facility.needs_ringed_body:
            if context.can_place_ringed():
                return PLACEMENT_ORBITAL
            return None
        if context.can_place_orbital():
            return PLACEMENT_ORBITAL
        return None

    if facility.needs_surface:
        if context.can_place_surface():
            return PLACEMENT_SURFACE
        return None

    # orbital_or_surface — choose based on availability and preference
    # Ports prefer orbital for access; support facilities prefer surface to
    # preserve orbital slots for ports.
    if facility.is_port:
        if context.can_place_orbital():
            return PLACEMENT_ORBITAL
        if context.can_place_surface():
            return PLACEMENT_SURFACE
    else:
        if context.can_place_surface():
            return PLACEMENT_SURFACE
        if context.can_place_orbital():
            return PLACEMENT_ORBITAL

    return None
