"""Structured mechanics trace output for debugging colony simulations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from edfinder_api.mechanics.confidence import ConfidenceLevel, ConfidenceSignal
from edfinder_api.mechanics.versions import SOURCE_FRONTIER_LINKS, SOURCE_MEGA_GUIDE


@dataclass(frozen=True)
class MechanicsTraceEvent:
    category: str
    label: str
    description: str
    value_before: Optional[float] = None
    value_after: Optional[float] = None
    delta: Optional[float] = None
    confidence: str = ConfidenceLevel.INFERRED.value
    source: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            'category': self.category,
            'label': self.label,
            'description': self.description,
            'confidence': self.confidence,
        }
        if self.value_before is not None:
            data['value_before'] = self.value_before
        if self.value_after is not None:
            data['value_after'] = self.value_after
        if self.delta is not None:
            data['delta'] = self.delta
        if self.source:
            data['source'] = self.source
        return data


TRACE_CATEGORIES = [
    'economy_sources',
    'strong_link_effects',
    'weak_link_effects',
    'pass_through_effects',
    'converted_port_effects',
    'port_economy_effects',
    'influence_ledger_effects',
    'regional_effects',
    'purity_effects',
    'contamination_effects',
    'cp_effects',
    'cp_repair_effects',
    'service_unlock_effects',
    'port_service_effects',
    'service_unlock_ledger_effects',
    'observation_comparison_effects',
    'confidence_adjustments',
]


def empty_trace() -> dict[str, list[dict[str, Any]]]:
    return {category: [] for category in TRACE_CATEGORIES}


def trace_simulation(
    *,
    placements: list[Any],
    topology_graph: Any,
    cp: dict[str, Any],
    economy_stack: dict[str, Any],
    services: dict[str, Any],
    confidence_signals: list[ConfidenceSignal],
    port_economy_states: list[Any] | None = None,
    influence_ledger: list[Any] | None = None,
    port_service_states: list[Any] | None = None,
    service_unlock_ledger: list[Any] | None = None,
    cp_repair_suggestions: list[Any] | None = None,
    observation_summary: Any | None = None,
    prediction_observation_diffs: list[Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    trace = empty_trace()

    for placement in placements:
        profile = getattr(placement, 'economy_profile', None)
        facility = getattr(placement, 'facility', None)
        if not profile or not facility:
            continue
        for economy, weight in profile.weights.items():
            trace['economy_sources'].append(MechanicsTraceEvent(
                category='economy_sources',
                label=f'{facility.name} -> {economy}',
                description=f'{facility.name} contributes {economy} economy pressure.',
                delta=round(float(weight), 3),
                confidence=ConfidenceLevel.VERIFIED.value if not profile.inherited else ConfidenceLevel.INFERRED.value,
                source=SOURCE_MEGA_GUIDE if profile.inherited else None,
            ).to_dict())

    for link in getattr(topology_graph, 'strong_links', []):
        trace['strong_link_effects'].append(MechanicsTraceEvent(
            category='strong_link_effects',
            label=f'{link.source_facility_name} -> {link.receiver_port_name}',
            description=(
                f'{link.source_facility_name} strongly links to the local Main Port '
                f'and adds {link.economy or "economy"} influence.'
            ),
            delta=float(link.value),
            confidence=ConfidenceLevel.VERIFIED.value,
            source=SOURCE_FRONTIER_LINKS,
        ).to_dict())

    for link in getattr(topology_graph, 'weak_links', []):
        trace['weak_link_effects'].append(MechanicsTraceEvent(
            category='weak_link_effects',
            label=f'{link.source_facility_name} -> {link.receiver_port_name}',
            description='Weak links target Main Ports on other local bodies at fixed strength.',
            delta=float(link.value),
            confidence=ConfidenceLevel.VERIFIED.value,
            source=SOURCE_FRONTIER_LINKS,
        ).to_dict())

    for link in getattr(topology_graph, 'pass_through_links', []):
        trace['pass_through_effects'].append(MechanicsTraceEvent(
            category='pass_through_effects',
            label=f'{link.source_facility_name} -> {link.orbital_receiver_name}',
            description='Surface influence passes through the Surface Main Port to the Orbital Main Port without duplicate support inflation.',
            delta=float(link.value),
            confidence=ConfidenceLevel.INFERRED.value,
            source=SOURCE_FRONTIER_LINKS,
        ).to_dict())

    for port in getattr(topology_graph, 'converted_ports', []):
        trace['converted_port_effects'].append(MechanicsTraceEvent(
            category='converted_port_effects',
            label=port.facility_name,
            description=port.reason,
            confidence=ConfidenceLevel.INFERRED.value,
            source=SOURCE_FRONTIER_LINKS,
        ).to_dict())

    for state in port_economy_states or []:
        top_two = getattr(state, 'top_two', []) or []
        label = getattr(state, 'port_name', 'Main Port')
        trace['port_economy_effects'].append(MechanicsTraceEvent(
            category='port_economy_effects',
            label=label,
            description=(
                f'{label} port economy state created with '
                f'{", ".join(top_two) if top_two else "no protected top-two economies"}.'
            ),
            value_after=round(float(getattr(state, 'purity_score', 0.0) or 0.0), 1),
            confidence=ConfidenceLevel.INFERRED.value,
            source=SOURCE_MEGA_GUIDE,
        ).to_dict())
        if top_two:
            trace['port_economy_effects'].append(MechanicsTraceEvent(
                category='port_economy_effects',
                label=f'{label} top-two protection',
                description=f'{label} protects {" / ".join(top_two)} as the dominant local economy pair.',
                confidence=ConfidenceLevel.INFERRED.value,
                source=SOURCE_MEGA_GUIDE,
            ).to_dict())
        for influence in getattr(state, 'contamination_sources', []) or []:
            trace['contamination_effects'].append(MechanicsTraceEvent(
                category='contamination_effects',
                label=f'{getattr(influence, "source_name", "Source")} -> {label}',
                description=(
                    f'{getattr(influence, "economy", "Economy")} contamination source detected via '
                    f'{getattr(influence, "influence_type", "influence")}.'
                ),
                delta=round(float(getattr(influence, 'value', 0.0) or 0.0), 3),
                confidence=ConfidenceLevel.INFERRED.value,
                source=SOURCE_FRONTIER_LINKS,
            ).to_dict())

    for influence in influence_ledger or []:
        influence_type = getattr(influence, 'influence_type', '')
        value = float(getattr(influence, 'value', 0.0) or 0.0)
        if value >= 0.4 or influence_type in {'weak_link', 'pass_through'}:
            trace['influence_ledger_effects'].append(MechanicsTraceEvent(
                category='influence_ledger_effects',
                label=f'{getattr(influence, "source_name", "Source")} -> {getattr(influence, "target_port_name", "Main Port")}',
                description=str(getattr(influence, 'reason', '') or f'{influence_type} influence recorded in the ledger.'),
                delta=round(value, 3),
                confidence=str(getattr(influence, 'confidence', ConfidenceLevel.INFERRED.value)),
                source=SOURCE_FRONTIER_LINKS,
            ).to_dict())
            if influence_type == 'weak_link':
                trace['contamination_effects'].append(MechanicsTraceEvent(
                    category='contamination_effects',
                    label=f'Weak-link contamination: {getattr(influence, "target_port_name", "Main Port")}',
                    description=str(getattr(influence, 'reason', 'Weak-link contamination pressure recorded.')),
                    delta=round(value, 3),
                    confidence=ConfidenceLevel.INFERRED.value,
                    source=SOURCE_FRONTIER_LINKS,
                ).to_dict())

    for step in cp.get('timeline', []):
        for warning in step.get('warnings', []):
            trace['cp_effects'].append(MechanicsTraceEvent(
                category='cp_effects',
                label=step.get('facility_name', 'Build step'),
                description=warning,
                value_before=float(step.get('yellow_before', 0) + step.get('green_before', 0)),
                value_after=float(step.get('yellow_after', 0) + step.get('green_after', 0)),
                confidence=ConfidenceLevel.COMMUNITY_OBSERVED.value,
            ).to_dict())

    for warning in economy_stack.get('warnings', []):
        category = 'purity_effects' if 'purity' in warning.lower() else 'contamination_effects'
        trace[category].append(MechanicsTraceEvent(
            category=category,
            label='Economy stack',
            description=str(warning),
            confidence=ConfidenceLevel.INFERRED.value,
            source=SOURCE_MEGA_GUIDE,
        ).to_dict())

    for suggestion in cp_repair_suggestions or []:
        trace['cp_repair_effects'].append(MechanicsTraceEvent(
            category='cp_repair_effects',
            label=str(getattr(suggestion, 'summary', 'CP repair suggestion')),
            description=str(getattr(suggestion, 'reason', '') or getattr(suggestion, 'expected_effect', '') or 'CP repair suggestion recorded.'),
            confidence=str(getattr(suggestion, 'confidence', ConfidenceLevel.INFERRED.value)),
            source='CP repair assistant',
        ).to_dict())

    for service, detail in services.items():
        if not isinstance(detail, dict):
            continue
        trace['service_unlock_effects'].append(MechanicsTraceEvent(
            category='service_unlock_effects',
            label=service,
            description=str(detail.get('reason') or 'Service unlock state modelled from catalogue rules.'),
            confidence=ConfidenceLevel.UNKNOWN.value if detail.get('status') == 'unknown' else ConfidenceLevel.INFERRED.value,
        ).to_dict())

    for state in port_service_states or []:
        label = getattr(state, 'port_name', 'Main Port')
        active = len(getattr(state, 'active_services', {}) or {})
        locked = len(getattr(state, 'locked_services', {}) or {})
        unknown = len(getattr(state, 'unknown_services', {}) or {})
        trace['port_service_effects'].append(MechanicsTraceEvent(
            category='port_service_effects',
            label=label,
            description=f'{label} service state created with {active} active, {locked} locked, and {unknown} unknown services.',
            confidence=ConfidenceLevel.INFERRED.value,
            source='Service dependency graph',
        ).to_dict())

    for entry in service_unlock_ledger or []:
        status = getattr(entry, 'status', '')
        if status == 'active' or getattr(entry, 'unlock_type', '') in {'strong_link_unlock', 'unknown_rule'}:
            trace['service_unlock_ledger_effects'].append(MechanicsTraceEvent(
                category='service_unlock_ledger_effects',
                label=f'{getattr(entry, "target_port_name", "Main Port")} {getattr(entry, "service", "service")}',
                description=str(getattr(entry, 'reason', '') or 'Service unlock ledger entry recorded.'),
                confidence=str(getattr(entry, 'confidence', ConfidenceLevel.INFERRED.value)),
                source='Service dependency graph',
            ).to_dict())

    if observation_summary is not None:
        status = getattr(observation_summary, 'status', 'predicted_only')
        summary_text = getattr(observation_summary, 'summary', 'Observation comparison state recorded.')
        trace['observation_comparison_effects'].append(MechanicsTraceEvent(
            category='observation_comparison_effects',
            label=str(status),
            description=str(summary_text),
            confidence=ConfidenceLevel.OBSERVED.value if getattr(observation_summary, 'observed_facts_count', 0) else ConfidenceLevel.UNKNOWN.value,
            source='Observed vs predicted comparison',
        ).to_dict())
        impact = getattr(observation_summary, 'confidence_impact', None)
        if impact:
            trace['observation_comparison_effects'].append(MechanicsTraceEvent(
                category='observation_comparison_effects',
                label=f'confidence impact: {impact}',
                description='Observation comparison is informational and does not yet alter mechanics scoring.',
                confidence=ConfidenceLevel.INFERRED.value,
                source='Observed vs predicted comparison',
            ).to_dict())
    for diff in prediction_observation_diffs or []:
        trace['observation_comparison_effects'].append(MechanicsTraceEvent(
            category='observation_comparison_effects',
            label=f'{getattr(diff, "area", "observation")} {getattr(diff, "status", "unknown")}',
            description=str(getattr(diff, 'reason', '') or 'Observed-vs-predicted diff recorded.'),
            confidence=str(getattr(diff, 'confidence', ConfidenceLevel.OBSERVED.value)),
            source='Observed vs predicted comparison',
        ).to_dict())

    for signal in confidence_signals:
        trace['confidence_adjustments'].append(MechanicsTraceEvent(
            category='confidence_adjustments',
            label=signal.area,
            description=signal.reason,
            delta=signal.impact,
            confidence=signal.level.value,
        ).to_dict())

    return trace


def regional_trace_event(*, archetype: str, regional_fit: float, weight: float) -> dict[str, Any]:
    return MechanicsTraceEvent(
        category='regional_effects',
        label=f'{archetype} regional fit',
        description='Regional fit is applied as a light recommendation adjustment and does not dominate local simulation mechanics.',
        delta=round(regional_fit * weight, 3),
        confidence=ConfidenceLevel.INFERRED.value,
        source='Regional positioning model',
    ).to_dict()
