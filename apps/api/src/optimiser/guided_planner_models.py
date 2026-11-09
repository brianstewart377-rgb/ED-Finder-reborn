from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from edfinder_api.optimiser.plan_quality import (
    EconomySoupAssessment,
    MissingPrerequisite,
    PlanPreset,
    PlanQualityReport,
)

RiskTolerance = Literal['low', 'normal', 'high']
GuidedLane = Literal['orbital', 'ground']
BodyRoleLabel = Literal['anchor', 'support', 'reserve', 'avoided']

PRESET_DEFAULT_COUNTS: dict[PlanPreset, int] = {
    'light': 4,
    'medium': 8,
    'high': 13,
    'maxed': 17,
}


@dataclass(frozen=True)
class GuidedPlanRequest:
    system_id64: int
    preset: PlanPreset
    target_economy: Optional[str] = None
    secondary_economy: Optional[str] = None
    avoid_economies: list[str] = field(default_factory=list)
    requested_count: Optional[int] = None
    risk_tolerance: RiskTolerance = 'normal'
    prefer_body_ids: list[str | int] = field(default_factory=list)
    avoid_body_ids: list[str | int] = field(default_factory=list)
    include_existing: bool = True
    include_projected: bool = False


@dataclass(frozen=True)
class GuidedBodyContext:
    body_id: str | int
    body_name: str
    body_type: str = 'Planet'
    subtype: str = ''
    is_landable: Optional[bool] = None
    is_ringed: Optional[bool] = None
    is_terraformable: Optional[bool] = None
    has_geo: Optional[bool] = None
    has_bio: Optional[bool] = None
    geo_signal_count: int = 0
    bio_signal_count: int = 0
    confidence: float = 0.75
    predicted_orbital_slots: Optional[int] = None
    predicted_ground_slots: Optional[int] = None
    occupied_orbital_slots: int = 0
    occupied_ground_slots: int = 0
    unresolved_existing_infrastructure: bool = False
    inferred_station_body_association: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_profile_row(self) -> dict[str, Any]:
        row = {
            'body_id': self.body_id,
            'body_name': self.body_name,
            'body_type': self.body_type,
            'subtype': self.subtype,
            'is_landable': self.is_landable,
            'is_ringed': self.is_ringed,
            'is_terraformable': self.is_terraformable,
            'has_geo': self.has_geo,
            'has_bio': self.has_bio,
            'geo_signal_count': self.geo_signal_count,
            'bio_signal_count': self.bio_signal_count,
            'confidence': self.confidence,
        }
        row.update(self.extra)
        return row


@dataclass(frozen=True)
class GuidedSystemContext:
    system_id64: int
    system_name: Optional[str] = None
    bodies: list[GuidedBodyContext] = field(default_factory=list)


@dataclass(frozen=True)
class GuidedPlanPlacement:
    facility_template_id: str
    facility_name: str
    body_id: str
    body_name: str
    lane: GuidedLane
    build_order: int
    is_primary_port: bool
    economy: Optional[str]
    role: str
    reason: str


@dataclass(frozen=True)
class GuidedBodyRole:
    body_id: str
    body_name: str
    role: BodyRoleLabel
    label: str
    rationale: list[str] = field(default_factory=list)
    planned_orbital_slots: int = 0
    planned_ground_slots: int = 0
    remaining_orbital_slots: Optional[int] = None
    remaining_ground_slots: Optional[int] = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GuidedPlanExplanation:
    why_this_body: list[str] = field(default_factory=list)
    why_this_structure: list[str] = field(default_factory=list)
    tradeoffs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GuidedPlanCandidateReport:
    system_id64: int
    preset: PlanPreset
    target_economies: list[str]
    title: str
    summary: str
    placements: list[GuidedPlanPlacement]
    body_roles: list[GuidedBodyRole]
    warnings: list[str]
    missing_prerequisites: list[MissingPrerequisite]
    occupied_slot_conflicts: list[str]
    unresolved_infrastructure_warnings: list[str]
    economy_discipline: EconomySoupAssessment
    quality: PlanQualityReport
    explanation: GuidedPlanExplanation
    no_strong_plan: bool = False
    no_strong_plan_reason: Optional[str] = None
