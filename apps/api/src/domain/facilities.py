"""
ED Finder — Domain: Facility Templates
=======================================
Reusable domain objects for facility rules and templates.

Design rules:
  • Pure Python dataclasses — no DB imports, no FastAPI, no asyncio.
  • Loaded once at startup from the facility_templates DB table.
  • All simulation code operates on these objects, not raw dicts.
  • FacilityTemplate is immutable after construction.

Separation:
  raw DB row  →  FacilityTemplate (this file)
  placement   →  FacilityPlacement (placements.py)
  economy     →  EconomyState (economy_state.py)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from edfinder_api.mechanics.confidence import CanonicalConfidence


# ---------------------------------------------------------------------------
# Slot location constants — mirrors facility_templates.allowed_location
# ---------------------------------------------------------------------------
LOC_ORBITAL         = 'orbital'
LOC_SURFACE         = 'surface'
LOC_ORBITAL_SURFACE = 'orbital_or_surface'
LOC_RINGED_ORBITAL  = 'ringed_orbital'

# Build complexity thresholds
COMPLEXITY_SIMPLE   = 'simple'
COMPLEXITY_MODERATE = 'moderate'
COMPLEXITY_ADVANCED = 'advanced'
COMPLEXITY_EXPERT   = 'expert'


@dataclass(frozen=True)
class FacilityTemplate:
    """
    Immutable description of a buildable ED facility.

    Mirrors the facility_templates table. All simulation code consumes
    this object rather than raw dicts so that mechanics are testable
    without a database.

    CP values are ESTIMATES derived from observed colonisation behaviour.
    Check stat_effects['data_confidence'] for per-facility certainty:
      'confirmed'  — verified from multiple in-game observations
      'observed'   — seen in community reports, high confidence
      'estimated'  — extrapolated / not widely verified
    """
    id:                   str
    name:                 str
    category:             str
    tier:                 int
    economy:              Optional[str]

    is_port:              bool
    is_colony_port:       bool
    is_support_facility:  bool

    yellow_cp_generated:  int
    green_cp_generated:   int
    yellow_cp_cost:       int
    green_cp_cost:        int

    strong_link_value:    float
    weak_link_value:      float

    allowed_location:     str
    pad_size:             Optional[str]

    prerequisites:        list[dict]
    economy_effects:      dict[str, Any]
    stat_effects:         dict[str, Any]

    # ── Derived helpers ───────────────────────────────────────────────────

    @property
    def needs_orbital(self) -> bool:
        return self.allowed_location in (LOC_ORBITAL, LOC_RINGED_ORBITAL)

    @property
    def needs_surface(self) -> bool:
        return self.allowed_location == LOC_SURFACE

    @property
    def needs_ringed_body(self) -> bool:
        return self.allowed_location == LOC_RINGED_ORBITAL

    @property
    def can_go_orbital(self) -> bool:
        return self.allowed_location in (
            LOC_ORBITAL, LOC_RINGED_ORBITAL, LOC_ORBITAL_SURFACE
        )

    @property
    def can_go_surface(self) -> bool:
        return self.allowed_location in (LOC_SURFACE, LOC_ORBITAL_SURFACE)

    @property
    def net_yellow_cp(self) -> int:
        """CP generated minus CP cost. Positive = net contributor."""
        return self.yellow_cp_generated - self.yellow_cp_cost

    @property
    def net_green_cp(self) -> int:
        return self.green_cp_generated - self.green_cp_cost

    @property
    def data_confidence(self) -> str:
        return self.stat_effects.get('data_confidence', 'estimated')

    @property
    def canonical_confidence(self) -> CanonicalConfidence:
        """Get EDRE-aligned canonical confidence for this facility's CP data.

        Converts the string data_confidence ('confirmed', 'observed', 'estimated')
        to the canonical shape for use in canonical workflows.

        Lazily imported to avoid circular dependencies with mechanics.confidence.
        """
        from edfinder_api.mechanics.confidence import facility_data_confidence_to_canonical
        return facility_data_confidence_to_canonical(self.data_confidence)

    @property
    def produces_economy(self) -> bool:
        return bool(self.economy)

    @classmethod
    def from_db_row(cls, row: dict) -> 'FacilityTemplate':
        """Construct from an asyncpg row dict."""
        return cls(
            id=row['id'],
            name=row['name'],
            category=row['category'],
            tier=row['tier'],
            economy=row.get('economy'),
            is_port=row.get('is_port', False),
            is_colony_port=row.get('is_colony_port', False),
            is_support_facility=row.get('is_support_facility', False),
            yellow_cp_generated=row.get('yellow_cp_generated', 0),
            green_cp_generated=row.get('green_cp_generated', 0),
            yellow_cp_cost=row.get('yellow_cp_cost', 0),
            green_cp_cost=row.get('green_cp_cost', 0),
            strong_link_value=float(row.get('strong_link_value', 0)),
            weak_link_value=float(row.get('weak_link_value', 0.05)),
            allowed_location=row.get('allowed_location', LOC_ORBITAL_SURFACE),
            pad_size=row.get('pad_size'),
            prerequisites=row.get('prerequisites') or [],
            economy_effects=row.get('economy_effects') or {},
            stat_effects=row.get('stat_effects') or {},
        )

    def to_dict(self) -> dict:
        return {
            'id':                   self.id,
            'name':                 self.name,
            'category':             self.category,
            'tier':                 self.tier,
            'economy':              self.economy,
            'is_port':              self.is_port,
            'is_colony_port':       self.is_colony_port,
            'is_support_facility':  self.is_support_facility,
            'yellow_cp_generated':  self.yellow_cp_generated,
            'green_cp_generated':   self.green_cp_generated,
            'yellow_cp_cost':       self.yellow_cp_cost,
            'green_cp_cost':        self.green_cp_cost,
            'strong_link_value':    self.strong_link_value,
            'weak_link_value':      self.weak_link_value,
            'allowed_location':     self.allowed_location,
            'pad_size':             self.pad_size,
            'prerequisites':        self.prerequisites,
            'economy_effects':      self.economy_effects,
            'data_confidence':      self.data_confidence,
        }


def is_colony_ship(template: FacilityTemplate) -> bool:
    """Identify the bootstrap ship independently of colony-type port economies."""
    return template.id == 'colony_ship'


# ---------------------------------------------------------------------------
# In-memory catalogue — loaded once at startup by the loader function below
# ---------------------------------------------------------------------------
_CATALOGUE: dict[str, FacilityTemplate] = {}
_BUNDLED_CATALOGUE_PATH = Path(__file__).with_name('facility_catalogue_v1.json')


def get_catalogue() -> dict[str, FacilityTemplate]:
    """Return the loaded facility catalogue. Empty until load_catalogue() called."""
    return _CATALOGUE


def get_facility(facility_id: str) -> Optional[FacilityTemplate]:
    return _CATALOGUE.get(facility_id)


def load_catalogue_from_rows(rows: list[dict]) -> None:
    """
    Populate the in-memory catalogue from DB rows.
    Call once at API startup from the lifespan handler.
    Thread-safe for reads after initial load (GIL protects the dict swap).
    """
    global _CATALOGUE
    _CATALOGUE = {r['id']: FacilityTemplate.from_db_row(r) for r in rows}


def templates_from_json_data(data: dict[str, Any]) -> dict[str, FacilityTemplate]:
    """Convert catalogue JSON to a fresh mapping without changing startup state."""
    return {
        item['id']: FacilityTemplate.from_db_row(_template_json_to_row(item))
        for item in data.get('templates') or []
    }


def load_catalogue_from_json_data(data: dict[str, Any]) -> None:
    """
    Populate the in-memory catalogue from a bundled/generated catalogue JSON.

    The V1 JSON is generated from DaftMav's workbook and intentionally mirrors
    the database row shape so the simulator can consume the richer catalogue
    without needing a schema migration.
    """
    global _CATALOGUE
    _CATALOGUE = templates_from_json_data(data)


def load_catalogue_from_json_path(path: Path | str) -> None:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    load_catalogue_from_json_data(data)


def load_bundled_catalogue() -> dict[str, FacilityTemplate]:
    """Load and return the DaftMav-derived bundled catalogue."""
    load_catalogue_from_json_path(_BUNDLED_CATALOGUE_PATH)
    return get_catalogue()


def facilities_by_tier(tier: int) -> list[FacilityTemplate]:
    return [f for f in _CATALOGUE.values() if f.tier == tier]


def facilities_by_economy(economy: str) -> list[FacilityTemplate]:
    return [f for f in _CATALOGUE.values() if f.economy == economy]


def port_facilities() -> list[FacilityTemplate]:
    return [f for f in _CATALOGUE.values() if f.is_port]


def _template_json_to_row(template: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': template['id'],
        'name': template['name'],
        'category': template.get('category', 'support'),
        'tier': template.get('tier', 1),
        'economy': template.get('economy'),
        'is_port': template.get('is_port', False),
        'is_colony_port': template.get('is_colony_port', False),
        'is_support_facility': template.get('is_support_facility', False),
        'yellow_cp_generated': template.get('yellow_cp_generated', 0),
        'green_cp_generated': template.get('green_cp_generated', 0),
        'yellow_cp_cost': template.get('yellow_cp_cost', 0),
        'green_cp_cost': template.get('green_cp_cost', 0),
        'strong_link_value': template.get('strong_link_value', 0),
        'weak_link_value': template.get('weak_link_value', 0.05),
        'allowed_location': template.get('allowed_location', LOC_ORBITAL_SURFACE),
        'pad_size': template.get('pad_size'),
        'prerequisites': template.get('prerequisites') or [],
        'economy_effects': template.get('economy_effects') or {},
        'stat_effects': template.get('stat_effects') or {},
    }
