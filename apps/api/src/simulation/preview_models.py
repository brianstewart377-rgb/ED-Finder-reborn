from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class PreviewPlacement:
    facility_template_id: str
    local_body_id: Optional[str] = None
    is_primary_port: bool = False
    build_order: int = 1


@dataclass
class PreviewContext:
    system_id64: int
    estimated_orbital_slots: Optional[int] = None
    estimated_ground_slots: Optional[int] = None
    slot_confidence: Optional[float] = None
    has_ringed_body: Optional[bool] = None
    local_body_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    mechanics_notes: list[str] = field(default_factory=list)
    observed_facts: list[Any] = field(default_factory=list)


@dataclass
class EconomyContributionProfile:
    base_economies: list[str] = field(default_factory=list)
    modifier_economies: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    purity: float = 1.0
    confidence: float = 1.0
    caveats: list[str] = field(default_factory=list)
    strategic_tags: list[str] = field(default_factory=list)
    source_body_id: Optional[str] = None
    source_body_name: Optional[str] = None
    inherited: bool = False

    @property
    def is_mixed(self) -> bool:
        return len(self.base_economies) + len(self.modifier_economies) > 1

