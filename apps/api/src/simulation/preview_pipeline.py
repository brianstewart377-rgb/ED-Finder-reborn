"""Internal state objects for the Simulation Preview pipeline.

These dataclasses deliberately model engine state rather than public API response
shape. They keep Stage 4E as a behaviour-preserving architecture hardening pass:
calculation stages can pass typed state forward without using the eventual
response dictionary as scratch space.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edfinder_api.mechanics.confidence import ConfidenceSignal


@dataclass
class PlacementResolutionState:
    """Result of resolving user-requested placements against the catalogue."""

    resolved_placements: list[Any]
    warnings: list[str] = field(default_factory=list)
    mechanics_notes: list[str] = field(default_factory=list)


@dataclass
class EconomySimulationState:
    """Economy outputs produced by topology and port-economy propagation."""

    links: dict[str, Any]
    economy_composition: dict[str, float]
    economy_order: list[str]
    economy_stack: Any
    composition: dict[str, Any]
    port_economy_states: list[Any]
    influence_ledger: list[Any]
    inherited_profiles: list[Any]


@dataclass
class ServiceSimulationState:
    """Service graph outputs for the resolved preview plan."""

    services: dict[str, Any]
    port_service_states: list[Any]
    service_unlock_ledger: list[Any]


@dataclass
class SimulationPrediction:
    """Internal prediction state before conversion to the public response dict."""

    system_id64: int
    target_archetype: str
    context: Any
    resolved_placements: list[Any]
    cp: dict[str, Any]
    cp_repair_suggestions: list[Any]
    topology_graph: Any
    economy: EconomySimulationState
    services: ServiceSimulationState
    buildability: dict[str, Any]
    confidence: float
    confidence_signals: list[ConfidenceSignal]
    build_complexity: str
    final_score: float
    warnings: list[str]
    strengths: list[str]
    recommendations: list[str]
    mechanics_notes: list[str]


@dataclass
class ObservationComparisonState:
    """Observed-vs-predicted comparison output and its advisory signal."""

    summary: Any
    diffs: list[Any]
    confidence_signal: ConfidenceSignal
