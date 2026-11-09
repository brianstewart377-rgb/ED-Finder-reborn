from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EvidenceRecord:
    evidence_key: str
    system_id64: int
    source_name: str
    origin: str
    subject_type: str
    subject_id: str | None
    evidence_type: str
    record_status: str
    freshness_status: str
    confidence: str
    summary: str | None
    source_record_id: str | None
    source_run_key: str | None
    observed_at: str | None
    collected_at: str | None
    expires_at: str | None
    value: dict[str, Any]
    provenance: dict[str, Any]
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'evidence_key': self.evidence_key,
            'system_id64': self.system_id64,
            'source_name': self.source_name,
            'origin': self.origin,
            'subject_type': self.subject_type,
            'subject_id': self.subject_id,
            'evidence_type': self.evidence_type,
            'record_status': self.record_status,
            'freshness_status': self.freshness_status,
            'confidence': self.confidence,
            'summary': self.summary,
            'source_record_id': self.source_record_id,
            'source_run_key': self.source_run_key,
            'observed_at': self.observed_at,
            'collected_at': self.collected_at,
            'expires_at': self.expires_at,
            'value': self.value,
            'provenance': self.provenance,
            'tags': list(self.tags),
            'metadata': dict(self.metadata),
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }


@dataclass(slots=True)
class DerivedFeature:
    feature_key: str
    system_id64: int
    feature_name: str
    feature_version: str
    feature_status: str
    confidence: str
    summary: str | None
    derived_from_run_key: str | None
    derived_at: str | None
    expires_at: str | None
    value: dict[str, Any]
    evidence_refs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'feature_key': self.feature_key,
            'system_id64': self.system_id64,
            'feature_name': self.feature_name,
            'feature_version': self.feature_version,
            'feature_status': self.feature_status,
            'confidence': self.confidence,
            'summary': self.summary,
            'derived_from_run_key': self.derived_from_run_key,
            'derived_at': self.derived_at,
            'expires_at': self.expires_at,
            'value': self.value,
            'evidence_refs': list(self.evidence_refs),
            'metadata': dict(self.metadata),
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }


@dataclass(slots=True)
class RuleProposal:
    proposal_key: str
    proposal_type: str
    domain: str
    scope_type: str
    scope_key: str
    status: str
    priority: str
    risk_level: str
    auto_approval_eligible: bool
    summary: str
    proposed_by: str
    decided_by: str | None
    decision_notes: str | None
    proposed_change: dict[str, Any]
    evidence_refs: list[str] = field(default_factory=list)
    impact_summary: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None
    decided_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'proposal_key': self.proposal_key,
            'proposal_type': self.proposal_type,
            'domain': self.domain,
            'scope_type': self.scope_type,
            'scope_key': self.scope_key,
            'status': self.status,
            'priority': self.priority,
            'risk_level': self.risk_level,
            'auto_approval_eligible': self.auto_approval_eligible,
            'summary': self.summary,
            'proposed_by': self.proposed_by,
            'decided_by': self.decided_by,
            'decision_notes': self.decision_notes,
            'proposed_change': self.proposed_change,
            'evidence_refs': list(self.evidence_refs),
            'impact_summary': dict(self.impact_summary),
            'metadata': dict(self.metadata),
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'decided_at': self.decided_at,
        }


@dataclass(slots=True)
class RuleDecision:
    decision_id: int
    proposal_key: str
    decision: str
    decided_by: str
    reason: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'decision_id': self.decision_id,
            'proposal_key': self.proposal_key,
            'decision': self.decision,
            'decided_by': self.decided_by,
            'reason': self.reason,
            'metadata': dict(self.metadata),
            'created_at': self.created_at,
        }


@dataclass(slots=True)
class EvidenceSystemFocusArea:
    key: str
    label: str
    posture: str
    summary: str
    evidence_type: str | None = None
    evidence_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'key': self.key,
            'label': self.label,
            'posture': self.posture,
            'summary': self.summary,
            'evidence_type': self.evidence_type,
            'evidence_key': self.evidence_key,
        }


@dataclass(slots=True)
class EvidenceSystemSummary:
    schema_version: str
    system_id64: int
    observed_fact_count: int
    imported_record_count: int
    derived_feature_count: int
    open_rule_proposal_count: int
    focus_areas: list[EvidenceSystemFocusArea] = field(default_factory=list)
    records: list[EvidenceRecord] = field(default_factory=list)
    derived_features: list[DerivedFeature] = field(default_factory=list)
    open_rule_proposals: list[RuleProposal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'system_id64': self.system_id64,
            'observed_fact_count': self.observed_fact_count,
            'imported_record_count': self.imported_record_count,
            'derived_feature_count': self.derived_feature_count,
            'open_rule_proposal_count': self.open_rule_proposal_count,
            'focus_areas': [focus_area.to_dict() for focus_area in self.focus_areas],
            'records': [record.to_dict() for record in self.records],
            'derived_features': [feature.to_dict() for feature in self.derived_features],
            'open_rule_proposals': [proposal.to_dict() for proposal in self.open_rule_proposals],
        }
