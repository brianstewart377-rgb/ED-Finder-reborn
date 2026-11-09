"""Stepwise CP build-order timeline for Simulation Preview."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edfinder_api.domain.facilities import FacilityTemplate
from edfinder_api.mechanics.cp_rules import escalating_port_tier
from edfinder_api.simulation.cp_simulator import port_cp_cost


@dataclass
class TimelineStep:
    step: int
    facility_template_id: str
    facility_name: str
    yellow_before: int
    yellow_after: int
    green_before: int
    green_after: int
    yellow_delta: int
    green_delta: int
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class BuildOrderResult:
    yellow_cp_final: int
    green_cp_final: int
    yellow_cp_generated: int
    green_cp_generated: int
    yellow_cp_spent: int
    green_cp_spent: int
    t2_ports: int
    t3_ports: int
    warnings: list[str]
    health_score: float
    timeline: list[TimelineStep]

    def to_cp_dict(self) -> dict[str, Any]:
        return {
            'yellow_cp_final': self.yellow_cp_final,
            'green_cp_final': self.green_cp_final,
            'yellow_cp_generated': self.yellow_cp_generated,
            'green_cp_generated': self.green_cp_generated,
            'yellow_cp_spent': self.yellow_cp_spent,
            'green_cp_spent': self.green_cp_spent,
            't2_ports': self.t2_ports,
            't3_ports': self.t3_ports,
            'warnings': self.warnings,
            'health_score': round(self.health_score, 1),
            'timeline': [step.to_dict() for step in self.timeline],
        }


def simulate_build_order(placements: list[Any]) -> BuildOrderResult:
    yellow_generated = green_generated = 0
    yellow_spent = green_spent = 0
    counted_ports = 0
    has_paid_t2 = False
    total_t2 = total_t3 = 0
    warnings: list[str] = []
    timeline: list[TimelineStep] = []

    for placement in placements:
        facility: FacilityTemplate = placement.facility
        spec = placement.spec
        yellow_before = yellow_generated - yellow_spent
        green_before = green_generated - green_spent
        yellow_cost = green_cost = 0
        step_warnings: list[str] = []
        step_notes: list[str] = []

        port_tier = escalating_port_tier(facility)
        if port_tier == 2:
            total_t2 += 1
        elif port_tier == 3:
            total_t3 += 1

        if facility.is_port and spec.is_primary_port:
            step_notes.append('primary-port exemption applied; no escalating CP cost is charged.')
        elif port_tier is not None:
            counted_ports += 1
            yellow_cost, green_cost = port_cp_cost(port_tier, counted_ports)
            if port_tier == 2:
                has_paid_t2 = True
                if counted_ports >= 3:
                    step_notes.append('guide table; guide example disagrees; not verified in-game')
            elif has_paid_t2:
                step_warnings.append('Build T3 ports earlier to avoid later escalation.')
        else:
            yellow_cost = int(facility.yellow_cp_cost or 0)
            green_cost = int(facility.green_cp_cost or 0)

        if yellow_before < yellow_cost or green_before < green_cost:
            step_warnings.append(
                f'This sequence goes CP-negative at step {spec.build_order}. Move CP-generating support before this port.'
            )

        yellow_spent += yellow_cost
        green_spent += green_cost
        yellow_generated += int(facility.yellow_cp_generated or 0)
        green_generated += int(facility.green_cp_generated or 0)
        yellow_after = yellow_generated - yellow_spent
        green_after = green_generated - green_spent

        timeline.append(TimelineStep(
            step=spec.build_order,
            facility_template_id=facility.id,
            facility_name=facility.name,
            yellow_before=yellow_before,
            yellow_after=yellow_after,
            green_before=green_before,
            green_after=green_after,
            yellow_delta=yellow_after - yellow_before,
            green_delta=green_after - green_before,
            warnings=step_warnings,
            notes=step_notes,
        ))
        warnings.extend(step_warnings)
        warnings.extend(step_notes)

    yellow_final = yellow_generated - yellow_spent
    green_final = green_generated - green_spent
    shortage = abs(min(0, yellow_final)) + abs(min(0, green_final))
    generated = max(1, yellow_generated + green_generated)
    health_score = max(0.0, min(100.0, 100.0 - (shortage / generated) * 100.0))

    return BuildOrderResult(
        yellow_cp_final=yellow_final,
        green_cp_final=green_final,
        yellow_cp_generated=yellow_generated,
        green_cp_generated=green_generated,
        yellow_cp_spent=yellow_spent,
        green_cp_spent=green_spent,
        t2_ports=total_t2,
        t3_ports=total_t3,
        warnings=_unique(warnings),
        health_score=health_score,
        timeline=timeline,
    )


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result
