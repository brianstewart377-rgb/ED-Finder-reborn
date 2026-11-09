from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import (
    DerivedFeature,
    EvidenceRecord,
    EvidenceSystemFocusArea,
    EvidenceSystemSummary,
    RuleDecision,
    RuleProposal,
)

JsonObject = dict[str, Any]

_ALLOWED_ORIGINS = {'manual', 'imported', 'inferred', 'derived', 'test_fixture'}
_ALLOWED_RECORD_STATUSES = {'active', 'superseded', 'rejected', 'archived', 'quarantined'}
_ALLOWED_FRESHNESS = {'current', 'stale', 'superseded', 'expired', 'unknown'}
_ALLOWED_FEATURE_STATUSES = {'active', 'stale', 'superseded'}
_ALLOWED_CONFIDENCE = {'low', 'medium', 'high'}
_ALLOWED_PROPOSAL_STATUSES = {'pending_review', 'approved', 'rejected', 'auto_approved', 'implemented', 'superseded'}
_ALLOWED_PRIORITY = {'low', 'medium', 'high', 'critical'}
_ALLOWED_RISK = {'low', 'medium', 'high'}
_ALLOWED_DECISIONS = {'approved', 'rejected', 'superseded', 'rolled_back'}
_ALLOWED_CANONICAL_PROMOTION_TYPES = {'body_completeness', 'station_set', 'colonisation_status', 'ring_composition'}


def _strip_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _dedupe_text_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in values:
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


class EvidenceSourceCatalogEntryResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    source_name: str
    label: str
    site_url: str
    implementation_status: str
    current_usage: str
    source_category: str
    domains: list[str]
    recommended_priority: int
    ingestion_modes: list[str]
    repo_surfaces: list[str]
    why_this_matters: str
    notes: str | None = None


class EvidenceSourceCatalogResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    schema_version: str
    sources: list[EvidenceSourceCatalogEntryResponse]


class EvidenceRecordBase(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_id64: int = Field(gt=0)
    source_name: str = Field(min_length=1, max_length=64)
    origin: str = 'imported'
    subject_type: str = Field(min_length=1, max_length=64)
    subject_id: str | None = Field(default=None, max_length=128)
    evidence_type: str = Field(min_length=1, max_length=64)
    record_status: str = 'active'
    freshness_status: str = 'current'
    confidence: str = 'medium'
    summary: str | None = Field(default=None, max_length=300)
    source_record_id: str | None = Field(default=None, max_length=128)
    source_run_key: str | None = Field(default=None, max_length=255)
    observed_at: str | None = None
    collected_at: str | None = None
    expires_at: str | None = None
    value: JsonObject = Field(default_factory=dict)
    provenance: JsonObject = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator('origin')
    @classmethod
    def validate_origin(cls, value: str) -> str:
        if value not in _ALLOWED_ORIGINS:
            raise ValueError(f'origin must be one of {sorted(_ALLOWED_ORIGINS)}')
        return value

    @field_validator('record_status')
    @classmethod
    def validate_record_status(cls, value: str) -> str:
        if value not in _ALLOWED_RECORD_STATUSES:
            raise ValueError(f'record_status must be one of {sorted(_ALLOWED_RECORD_STATUSES)}')
        return value

    @field_validator('freshness_status')
    @classmethod
    def validate_freshness(cls, value: str) -> str:
        if value not in _ALLOWED_FRESHNESS:
            raise ValueError(f'freshness_status must be one of {sorted(_ALLOWED_FRESHNESS)}')
        return value

    @field_validator('confidence')
    @classmethod
    def validate_confidence(cls, value: str) -> str:
        if value not in _ALLOWED_CONFIDENCE:
            raise ValueError(f'confidence must be one of {sorted(_ALLOWED_CONFIDENCE)}')
        return value

    @field_validator('subject_id', 'summary', 'source_record_id', 'source_run_key')
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional(value)

    @field_validator('tags')
    @classmethod
    def dedupe_tags(cls, value: list[str]) -> list[str]:
        return _dedupe_text_list(value)

    @model_validator(mode='after')
    def validate_lifecycle_consistency(self) -> 'EvidenceRecordBase':
        if self.record_status == 'active' and self.freshness_status == 'superseded':
            raise ValueError('active evidence records cannot carry freshness_status=superseded')
        if self.record_status == 'superseded' and self.freshness_status != 'superseded':
            raise ValueError('superseded evidence records must carry freshness_status=superseded')
        return self


class EvidenceRecordCreateRequest(EvidenceRecordBase):
    pass


class EvidenceRecordResponse(EvidenceRecordBase):
    evidence_key: str
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_domain(cls, record: EvidenceRecord) -> 'EvidenceRecordResponse':
        return cls.model_validate(record.to_dict())


class EvidenceRecordListResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    records: list[EvidenceRecordResponse]
    total: int
    limit: int
    offset: int


class CanonicalEvidencePromotionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    evidence_types: list[str] = Field(default_factory=lambda: ['body_completeness', 'station_set'])

    @field_validator('evidence_types')
    @classmethod
    def validate_evidence_types(cls, value: list[str]) -> list[str]:
        deduped = _dedupe_text_list(value or [])
        if not deduped:
            raise ValueError('evidence_types must include at least one canonical evidence type')
        invalid = [item for item in deduped if item not in _ALLOWED_CANONICAL_PROMOTION_TYPES]
        if invalid:
            raise ValueError(
                f'evidence_types must be drawn from {sorted(_ALLOWED_CANONICAL_PROMOTION_TYPES)}'
            )
        return deduped


class CanonicalEvidencePromotionResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    schema_version: str
    system_id64: int
    promoted_count: int
    warnings: list[str] = Field(default_factory=list)
    records: list[EvidenceRecordResponse] = Field(default_factory=list)


class DerivedFeatureBase(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_id64: int = Field(gt=0)
    feature_name: str = Field(min_length=1, max_length=64)
    feature_version: str = Field(default='v1', min_length=1, max_length=32)
    feature_status: str = 'active'
    confidence: str = 'medium'
    summary: str | None = Field(default=None, max_length=300)
    derived_from_run_key: str | None = Field(default=None, max_length=255)
    derived_at: str | None = None
    expires_at: str | None = None
    value: JsonObject = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator('feature_status')
    @classmethod
    def validate_feature_status(cls, value: str) -> str:
        if value not in _ALLOWED_FEATURE_STATUSES:
            raise ValueError(f'feature_status must be one of {sorted(_ALLOWED_FEATURE_STATUSES)}')
        return value

    @field_validator('confidence')
    @classmethod
    def validate_confidence(cls, value: str) -> str:
        if value not in _ALLOWED_CONFIDENCE:
            raise ValueError(f'confidence must be one of {sorted(_ALLOWED_CONFIDENCE)}')
        return value

    @field_validator('summary', 'derived_from_run_key')
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional(value)

    @field_validator('evidence_refs')
    @classmethod
    def dedupe_evidence_refs(cls, value: list[str]) -> list[str]:
        return _dedupe_text_list(value)


class DerivedFeatureCreateRequest(DerivedFeatureBase):
    pass


class DerivedFeatureResponse(DerivedFeatureBase):
    feature_key: str
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_domain(cls, feature: DerivedFeature) -> 'DerivedFeatureResponse':
        return cls.model_validate(feature.to_dict())


class DerivedFeatureListResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    features: list[DerivedFeatureResponse]
    total: int
    limit: int
    offset: int


class RuleProposalBase(BaseModel):
    model_config = ConfigDict(extra='forbid')

    proposal_type: str = Field(min_length=1, max_length=64)
    domain: str = Field(min_length=1, max_length=64)
    scope_type: str = Field(min_length=1, max_length=64)
    scope_key: str = Field(min_length=1, max_length=128)
    status: str = 'pending_review'
    priority: str = 'medium'
    risk_level: str = 'medium'
    auto_approval_eligible: bool = False
    summary: str = Field(min_length=1, max_length=300)
    proposed_by: str = Field(min_length=1, max_length=128)
    decision_notes: str | None = Field(default=None, max_length=2000)
    proposed_change: JsonObject = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    impact_summary: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator('status')
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in _ALLOWED_PROPOSAL_STATUSES:
            raise ValueError(f'status must be one of {sorted(_ALLOWED_PROPOSAL_STATUSES)}')
        return value

    @field_validator('priority')
    @classmethod
    def validate_priority(cls, value: str) -> str:
        if value not in _ALLOWED_PRIORITY:
            raise ValueError(f'priority must be one of {sorted(_ALLOWED_PRIORITY)}')
        return value

    @field_validator('risk_level')
    @classmethod
    def validate_risk(cls, value: str) -> str:
        if value not in _ALLOWED_RISK:
            raise ValueError(f'risk_level must be one of {sorted(_ALLOWED_RISK)}')
        return value

    @field_validator('decision_notes')
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional(value)

    @field_validator('evidence_refs')
    @classmethod
    def dedupe_evidence_refs(cls, value: list[str]) -> list[str]:
        return _dedupe_text_list(value)


class RuleProposalCreateRequest(RuleProposalBase):
    pass


class RuleProposalResponse(RuleProposalBase):
    proposal_key: str
    decided_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    decided_at: str | None = None

    @classmethod
    def from_domain(cls, proposal: RuleProposal) -> 'RuleProposalResponse':
        return cls.model_validate(proposal.to_dict())


class RuleProposalListResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    proposals: list[RuleProposalResponse]
    total: int
    limit: int
    offset: int


class RuleDecisionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    decision: str
    decided_by: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=2000)
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator('decision')
    @classmethod
    def validate_decision(cls, value: str) -> str:
        if value not in _ALLOWED_DECISIONS:
            raise ValueError(f'decision must be one of {sorted(_ALLOWED_DECISIONS)}')
        return value

    @field_validator('reason')
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional(value)


class RuleDecisionResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    decision_id: int
    proposal_key: str
    decision: str
    decided_by: str
    reason: str | None = None
    metadata: JsonObject = Field(default_factory=dict)
    created_at: str | None = None

    @classmethod
    def from_domain(cls, decision: RuleDecision) -> 'RuleDecisionResponse':
        return cls.model_validate(decision.to_dict())


class EvidenceSystemFocusAreaResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    key: str
    label: str
    posture: str
    summary: str
    evidence_type: str | None = None
    evidence_key: str | None = None

    @classmethod
    def from_domain(cls, focus_area: EvidenceSystemFocusArea) -> 'EvidenceSystemFocusAreaResponse':
        return cls.model_validate(focus_area.to_dict())


class EvidenceSystemSummaryResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    schema_version: str
    system_id64: int
    observed_fact_count: int
    imported_record_count: int
    derived_feature_count: int
    open_rule_proposal_count: int
    focus_areas: list[EvidenceSystemFocusAreaResponse]
    records: list[EvidenceRecordResponse]
    derived_features: list[DerivedFeatureResponse]
    open_rule_proposals: list[RuleProposalResponse]

    @classmethod
    def from_domain(cls, summary: EvidenceSystemSummary) -> 'EvidenceSystemSummaryResponse':
        return cls.model_validate(summary.to_dict())
