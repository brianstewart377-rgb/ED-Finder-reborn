"""Public response assembly for Simulation Preview.

Stage 4E keeps the calculation pipeline separate from the public API response
shape. This module owns conversion from internal prediction/observation state to
the Pydantic-ready response dictionary returned by ``simulate_build_preview``.
"""
from __future__ import annotations

from typing import Any, Optional

from edfinder_api.mechanics.confidence import default_data_quality, signals_to_dict
from edfinder_api.mechanics.versions import MECHANICS_VERSION
from edfinder_api.observations.schemas import (
    observation_summary_to_dict,
    prediction_observation_diffs_to_dict,
)
from edfinder_api.simulation.cp_repair import cp_repair_suggestions_to_dict
from edfinder_api.simulation.mechanics_trace import trace_simulation
from edfinder_api.simulation.port_economy import influence_ledger_to_dict, port_states_to_dict
from edfinder_api.simulation.preview_pipeline import ObservationComparisonState, SimulationPrediction
from edfinder_api.simulation.service_graph import (
    port_service_states_to_dict,
    service_unlock_ledger_to_dict,
)
from edfinder_api.simulation.topology_graph import GraphPlacement


def assemble_preview_response(
    prediction: SimulationPrediction,
    observation: ObservationComparisonState,
) -> dict[str, Any]:
    """Convert internal simulation state into the stable public response shape."""
    cp = prediction.cp
    economy = prediction.economy
    service_state = prediction.services
    buildability = prediction.buildability
    all_confidence_signals = [*prediction.confidence_signals, observation.confidence_signal]

    response = {
        'system_id64': prediction.system_id64,
        'mechanics_version': MECHANICS_VERSION,
        'target_archetype': prediction.target_archetype,
        'final_score': prediction.final_score,
        'composition_score': round(economy.composition['score'], 1),
        'buildability_score': round(buildability['score'], 1),
        'build_complexity': prediction.build_complexity,
        'confidence': round(prediction.confidence, 2),
        'cp': {
            'yellow_cp_final': cp['yellow_cp_final'],
            'green_cp_final': cp['green_cp_final'],
            'yellow_cp_generated': cp['yellow_cp_generated'],
            'green_cp_generated': cp['green_cp_generated'],
            'yellow_cp_spent': cp['yellow_cp_spent'],
            'green_cp_spent': cp['green_cp_spent'],
            't2_ports': cp['t2_ports'],
            't3_ports': cp['t3_ports'],
            'warnings': cp['warnings'],
        },
        'cp_timeline': cp['timeline'],
        'cp_repair_suggestions': cp_repair_suggestions_to_dict(prediction.cp_repair_suggestions),
        'economy_composition': economy.economy_composition,
        'economy_order': economy.economy_order,
        'economy_stack': economy.economy_stack.to_dict(),
        'port_economy_states': port_states_to_dict(economy.port_economy_states),
        'influence_ledger': influence_ledger_to_dict(economy.influence_ledger),
        'inherited_economies': [_profile_to_response(profile) for profile in economy.inherited_profiles],
        'topology': _topology_to_response(prediction.topology_graph),
        'services': service_state.services,
        'port_service_states': port_service_states_to_dict(service_state.port_service_states),
        'service_unlock_ledger': service_unlock_ledger_to_dict(service_state.service_unlock_ledger),
        'data_quality': default_data_quality(),
        'confidence_signals': signals_to_dict(all_confidence_signals),
        'top_two_alignment': economy.composition['alignment'],
        'contamination_risk': economy.composition['contamination_risk'],
        'warnings': _unique(prediction.warnings),
        'strengths': _unique(prediction.strengths),
        'recommendations': _unique(prediction.recommendations),
        'mechanics_notes': _unique(prediction.mechanics_notes),
        'links': economy.links,
        'observation_summary': observation_summary_to_dict(observation.summary),
        'prediction_observation_diffs': prediction_observation_diffs_to_dict(observation.diffs),
    }
    response['mechanics_trace'] = trace_simulation(
        placements=prediction.resolved_placements,
        topology_graph=prediction.topology_graph,
        cp=cp,
        economy_stack=economy.economy_stack.to_dict(),
        services=service_state.services,
        confidence_signals=all_confidence_signals,
        port_economy_states=economy.port_economy_states,
        influence_ledger=economy.influence_ledger,
        port_service_states=service_state.port_service_states,
        service_unlock_ledger=service_state.service_unlock_ledger,
        cp_repair_suggestions=prediction.cp_repair_suggestions,
        observation_summary=observation.summary,
        prediction_observation_diffs=observation.diffs,
    )
    return response


def observation_prediction_snapshot(prediction: SimulationPrediction) -> dict[str, Any]:
    """Build the internal comparison shape without mutating the public response."""
    economy = prediction.economy
    return {
        'system_id64': prediction.system_id64,
        'target_archetype': prediction.target_archetype,
        'final_score': prediction.final_score,
        'composition_score': round(economy.composition['score'], 1),
        'buildability_score': round(prediction.buildability['score'], 1),
        'build_complexity': prediction.build_complexity,
        'confidence': round(prediction.confidence, 2),
        'cp': {
            'yellow_cp_final': prediction.cp['yellow_cp_final'],
            'green_cp_final': prediction.cp['green_cp_final'],
            'yellow_cp_generated': prediction.cp['yellow_cp_generated'],
            'green_cp_generated': prediction.cp['green_cp_generated'],
            'yellow_cp_spent': prediction.cp['yellow_cp_spent'],
            'green_cp_spent': prediction.cp['green_cp_spent'],
            't2_ports': prediction.cp['t2_ports'],
            't3_ports': prediction.cp['t3_ports'],
            'warnings': prediction.cp['warnings'],
        },
        'economy_composition': economy.economy_composition,
        'economy_order': economy.economy_order,
        'economy_stack': economy.economy_stack.to_dict(),
        'topology': _topology_to_response(prediction.topology_graph),
        'services': prediction.services.services,
        'port_service_states': port_service_states_to_dict(prediction.services.port_service_states),
        'estimated_orbital_slots': prediction.context.estimated_orbital_slots,
        'estimated_ground_slots': prediction.context.estimated_ground_slots,
    }


def topology_to_response(graph: Any) -> dict[str, Any]:
    """Expose topology serialization for focused contract tests."""
    return _topology_to_response(graph)


def links_to_response(graph: Any) -> dict[str, list[dict[str, Any]]]:
    """Expose link serialization for the prediction pipeline."""
    return _links_to_response(graph)


def _topology_to_response(graph: Any) -> dict[str, Any]:
    return {
        'local_body_groups': [
            {
                'local_body_id': group.local_body_id,
                'body_name': group.body_name,
                'parent_body_id': group.parent_body_id,
                'main_surface_port': _placement_summary(group.main_surface_port),
                'main_orbital_port': _placement_summary(group.main_orbital_port),
                'facility_count': len(group.facilities),
                'surface_port_count': len(group.surface_ports),
                'orbital_port_count': len(group.orbital_ports),
            }
            for group in graph.local_body_groups
        ],
        'roles': [
            {
                'facility_id': item.placement.facility_id,
                'facility_name': item.placement.facility_name,
                'local_body_id': item.placement.local_body_id,
                'location_type': item.placement.location_type,
                'effective_role': item.effective_role,
            }
            for item in graph.classified_placements
        ],
        'strong_links': [link.to_dict() for link in graph.strong_links],
        'weak_links': [link.to_dict() for link in graph.weak_links],
        'pass_through_links': [link.to_dict() for link in graph.pass_through_links],
        'converted_ports': [port.to_dict() for port in graph.converted_ports],
        'assumptions': graph.assumptions,
        'warnings': graph.warnings,
    }


def _links_to_response(graph: Any) -> dict[str, list[dict[str, Any]]]:
    return {
        'strong_links': [
            {
                'port_facility_id': link.receiver_port_id,
                'support_facility_id': link.source_facility_id,
                'local_body_id': link.local_body_id,
                'economy': link.economy,
                'value': link.value,
                'note': link.note,
            }
            for link in graph.strong_links
        ],
        'weak_links': [
            {
                'port_facility_id': link.receiver_port_id,
                'support_facility_id': link.source_facility_id,
                'local_body_id': link.source_body_id,
                'economy': link.economy,
                'value': link.value,
                'note': link.note,
            }
            for link in graph.weak_links
        ],
    }


def _placement_summary(placement: Optional[GraphPlacement]) -> Optional[dict[str, Any]]:
    if placement is None:
        return None
    return {
        'facility_id': placement.facility_id,
        'facility_name': placement.facility_name,
        'tier': placement.facility.tier,
        'build_order': placement.build_order,
        'location_type': placement.location_type,
        'economy': placement.economy,
    }


def _profile_to_response(profile: Any) -> dict[str, Any]:
    return {
        'source_body_id': profile.source_body_id,
        'source_body_name': profile.source_body_name,
        'base_economies': profile.base_economies,
        'modifier_economies': profile.modifier_economies,
        'weights': profile.weights,
        'purity': profile.purity,
        'confidence': profile.confidence,
        'caveats': profile.caveats,
        'strategic_tags': profile.strategic_tags,
    }


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
