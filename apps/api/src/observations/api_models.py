from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import (
    ObservationFactSummary,
    ObservationSource,
    ObservedConfidence,
    ObservedFactType,
    ObservedStatus,
    ObservedSubjectType,
    PersistedObservedFact,
)

JsonValue = str | int | float | bool | dict[str, Any] | list[Any] | None

_ALLOWED_STAGE_6A_SOURCES = {ObservationSource.MANUAL, ObservationSource.TEST_FIXTURE}
_MAX_TAGS = 20
_MAX_NOTE_LENGTH = 2000


def _normalise_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    normalised: list[str] = []
    for tag in tags:
        value = str(tag).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalised.append(value)
        if len(normalised) >= _MAX_TAGS:
            break
    return normalised


class ObservedFactBase(BaseModel):
    model_config = ConfigDict(extra='forbid')

    source: ObservationSource = ObservationSource.MANUAL
    fact_type: ObservedFactType
    subject_type: ObservedSubjectType
    subject_id: str | None = None
    status: ObservedStatus
    observed_value: JsonValue = None
    expected_value: JsonValue = None
    confidence: ObservedConfidence = ObservedConfidence.MEDIUM
    notes: str | None = Field(default=None, max_length=_MAX_NOTE_LENGTH)
    build_fingerprint: str | None = None
    simulation_fingerprint: str | None = None
    target_archetype: str | None = None
    facility_template_id: str | None = None
    local_body_id: str | None = None
    service_id: str | None = None
    economy: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator('source')
    @classmethod
    def stage_6a_sources_only(cls, value: ObservationSource) -> ObservationSource:
        if value not in _ALLOWED_STAGE_6A_SOURCES:
            raise ValueError('Stage 6A accepts only manual or test_fixture observations')
        return value

    @field_validator('tags')
    @classmethod
    def normalise_tags(cls, value: list[str]) -> list[str]:
        return _normalise_tags(value)

    @field_validator('metadata')
    @classmethod
    def metadata_must_be_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError('metadata must be an object')
        return value

    @model_validator(mode='after')
    def encourage_structured_subject_fields(self) -> 'ObservedFactBase':
        if self.fact_type == ObservedFactType.SERVICE_PRESENCE and not self.service_id:
            raise ValueError('service_presence observations require service_id')
        if self.fact_type == ObservedFactType.ECONOMY_PRESENCE and not self.economy:
            raise ValueError('economy_presence observations require economy')
        if self.fact_type == ObservedFactType.FACILITY_STATE and not self.facility_template_id:
            raise ValueError('facility_state observations require facility_template_id')
        return self


class ObservedFactCreateRequest(ObservedFactBase):
    system_id64: int = Field(gt=0)


class ObservedFactUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    source: ObservationSource | None = None
    fact_type: ObservedFactType | None = None
    subject_type: ObservedSubjectType | None = None
    subject_id: str | None = None
    status: ObservedStatus | None = None
    observed_value: JsonValue = None
    expected_value: JsonValue = None
    confidence: ObservedConfidence | None = None
    notes: str | None = Field(default=None, max_length=_MAX_NOTE_LENGTH)
    build_fingerprint: str | None = None
    simulation_fingerprint: str | None = None
    target_archetype: str | None = None
    facility_template_id: str | None = None
    local_body_id: str | None = None
    service_id: str | None = None
    economy: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None

    @field_validator('source')
    @classmethod
    def stage_6a_sources_only(cls, value: ObservationSource | None) -> ObservationSource | None:
        if value is not None and value not in _ALLOWED_STAGE_6A_SOURCES:
            raise ValueError('Stage 6A accepts only manual or test_fixture observations')
        return value

    @field_validator('tags')
    @classmethod
    def normalise_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalise_tags(value)

    @field_validator('metadata')
    @classmethod
    def metadata_must_be_object(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and not isinstance(value, dict):
            raise ValueError('metadata must be an object')
        return value


class ObservedFactResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    observation_id: str
    system_id64: int
    created_at: str
    updated_at: str | None
    source: str
    fact_type: str
    subject_type: str
    subject_id: str | None
    status: str
    observed_value: JsonValue = None
    expected_value: JsonValue = None
    confidence: str
    notes: str | None = None
    build_fingerprint: str | None = None
    simulation_fingerprint: str | None = None
    target_archetype: str | None = None
    facility_template_id: str | None = None
    local_body_id: str | None = None
    service_id: str | None = None
    economy: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    canonical_confidence: dict[str, Any] | None = None  # EDRE-aligned canonical shape

    @classmethod
    def from_domain(cls, fact: PersistedObservedFact) -> 'ObservedFactResponse':
        return cls.model_validate(fact.to_dict())


class ObservationFactSummaryResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    total_count: int
    by_fact_type: dict[str, int]
    by_status: dict[str, int]
    by_confidence: dict[str, int]
    latest_observed_at: str | None

    @classmethod
    def from_domain(cls, summary: ObservationFactSummary) -> 'ObservationFactSummaryResponse':
        return cls.model_validate(summary.to_dict())


class ObservedFactListResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    facts: list[ObservedFactResponse]
    total: int
    limit: int
    offset: int
    summary: ObservationFactSummaryResponse


class ObservedFactDeleteResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    observation_id: str
    deleted: bool


# ──────────────────────────────────────────────────────────────────────
# Stage 6C — predicted-vs-observed comparison request/response models
# ──────────────────────────────────────────────────────────────────────
# These models are the HTTP contract for ``POST /api/observations/compare``.
# They are intentionally separate from the Stage 4D comparison models so
# Stage 6C can evolve its vocabulary independently and so the simulation
# pipeline's existing schema is not disturbed.
#
# Validation goals:
#   * Reject negative / zero system_id64 (matches existing observation
#     endpoint behaviour).
#   * Force ``prediction`` to be an object/dict — refuse strings, lists,
#     numbers. Stage 6C compares prediction *blocks*, not scalars.
#   * Accept an optional ``observed_facts`` list of fact-shaped objects;
#     when omitted the router loads persisted facts for ``system_id64``.
#     When provided, the router uses the supplied list verbatim and does
#     not hit the database for facts (Mode B).
#
# The response models mirror the dataclass shapes from
# ``observations.comparison_models`` 1:1 so that ``asdict`` output round-trips
# straight through ``model_validate``.


class _ObservedFactInputBase(BaseModel):
    """Shared shape for fact-like objects accepted by Mode B compare calls.

    This is a lenient mirror of ``PersistedObservedFact`` used only to
    accept user-supplied observation payloads in a compare request. We
    deliberately do NOT reuse ``ObservedFactCreateRequest`` because that
    model enforces Stage 6A write-side rules (e.g. ``stage_6a_sources_only``
    rejecting ``inferred``/``imported``). The compare endpoint is read-only
    over its inputs, so it accepts any of the Stage 6A enum values.

    **Stage 6C Mode B source policy (deliberate):**

    * The ``source`` field accepts any ``ObservationSource`` value,
      including the Stage 6A reserved values ``imported`` and
      ``inferred``.
    * Mode B inputs are **never persisted**. They are passed straight
      into the pure comparison engine and discarded after the response
      is built. There is no path from this model to a database write.
    * Stage 6A / 6B persisted creation (``POST /api/observations/facts``,
      ``PATCH /api/observations/facts/{id}``) remains restricted to
      ``manual`` / ``test_fixture`` via the existing
      ``stage_6a_sources_only`` validator on
      ``ObservedFactCreateRequest`` / ``ObservedFactUpdateRequest``.
    * This split allows callers (tests, offline/dry-run flows, future
      ingestion prototypes) to dry-run a compare against
      imported/inferred-shaped evidence without weakening the
      write-side trust rules.
    """

    model_config = ConfigDict(extra='forbid')

    observation_id: str = Field(min_length=1)
    system_id64: int = Field(gt=0)
    created_at: str = Field(min_length=1)
    updated_at: str | None = None
    source: ObservationSource = ObservationSource.MANUAL
    fact_type: ObservedFactType
    subject_type: ObservedSubjectType
    subject_id: str | None = None
    status: ObservedStatus
    observed_value: JsonValue = None
    expected_value: JsonValue = None
    confidence: ObservedConfidence = ObservedConfidence.MEDIUM
    notes: str | None = Field(default=None, max_length=_MAX_NOTE_LENGTH)
    build_fingerprint: str | None = None
    simulation_fingerprint: str | None = None
    target_archetype: str | None = None
    facility_template_id: str | None = None
    local_body_id: str | None = None
    service_id: str | None = None
    economy: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator('tags')
    @classmethod
    def _normalise_tags(cls, value: list[str]) -> list[str]:
        return _normalise_tags(value)

    @field_validator('metadata')
    @classmethod
    def _metadata_must_be_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError('metadata must be an object')
        return value

    def to_persisted(self) -> PersistedObservedFact:
        return PersistedObservedFact(
            observation_id=self.observation_id,
            system_id64=self.system_id64,
            created_at=self.created_at,
            updated_at=self.updated_at,
            source=self.source.value,
            fact_type=self.fact_type.value,
            subject_type=self.subject_type.value,
            subject_id=self.subject_id,
            status=self.status.value,
            observed_value=self.observed_value,
            expected_value=self.expected_value,
            confidence=self.confidence.value,
            notes=self.notes,
            build_fingerprint=self.build_fingerprint,
            simulation_fingerprint=self.simulation_fingerprint,
            target_archetype=self.target_archetype,
            facility_template_id=self.facility_template_id,
            local_body_id=self.local_body_id,
            service_id=self.service_id,
            economy=self.economy,
            tags=list(self.tags),
            metadata=dict(self.metadata),
        )


class ObservedFactInput(_ObservedFactInputBase):
    """Fact-shaped object accepted by the Stage 6C compare endpoint."""


class PredictionObservationCompareRequest(BaseModel):
    """Request payload for ``POST /api/observations/compare``.

    Two modes:

    * **Mode A** — supply only ``system_id64``, ``target_archetype`` and
      ``prediction``. The router loads observed facts for the system
      from the persisted Stage 6A store.
    * **Mode B** — supply ``observed_facts`` explicitly (in addition to
      the other fields). The router will NOT hit the database for facts
      and will use the supplied list verbatim.
    """

    model_config = ConfigDict(extra='forbid')

    system_id64: int = Field(gt=0)
    target_archetype: str | None = None
    prediction: dict[str, Any]
    observed_facts: list[ObservedFactInput] | None = None
    fact_load_limit: int = Field(default=500, ge=1, le=2000)

    @field_validator('prediction')
    @classmethod
    def _prediction_must_be_object(cls, value: Any) -> dict[str, Any]:
        # Pydantic will already coerce ``dict[str, Any]`` from JSON
        # objects but reject arrays/strings/numbers at parse time. We
        # add a belt-and-braces check so callers using raw Python see
        # the same error message as JSON callers.
        if not isinstance(value, dict):
            raise ValueError('prediction must be a JSON object')
        return value


class ObservationEvidenceMatchResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    observation_id: str
    fact_type: str
    subject_type: str
    subject_id: str | None
    status: str
    confidence: str
    observed_value: JsonValue = None
    expected_value: JsonValue = None
    notes: str | None = None


class PredictionObservationComparisonResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    comparison_id: str
    area: str
    subject_type: str
    subject_id: str | None
    predicted_value: JsonValue = None
    observed_value: JsonValue = None
    status: str
    severity: str
    confidence: str
    reason: str
    recommended_action: str | None = None
    evidence: list[ObservationEvidenceMatchResponse] = Field(default_factory=list)
    prediction_source: str | None = None
    canonical_confidence: dict[str, Any] | None = None


class PredictionObservationComparisonSummaryResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    status: str
    observed_facts_count: int
    compared_predictions_count: int
    confirmed_count: int
    contradicted_count: int
    observed_only_count: int
    predicted_only_count: int
    unknown_count: int
    unverified_count: int
    confidence_impact: str
    summary: str


class PredictionObservationCompareResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_id64: int
    target_archetype: str | None
    generated_at: str
    summary: PredictionObservationComparisonSummaryResponse
    comparisons: list[PredictionObservationComparisonResponse] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────
# Stage 6E — validation review guidance request/response models
# ─────────────────────────────────────────────────────────────────────
# These models mirror the Stage 6C compare request for caller
# convenience, then return the structured advisory review guidance built
# from the comparison result. The endpoint is passive: it does not run
# Simulation Preview, mutate observations, or feed anything back into
# mechanics/scoring/ranking.


class ValidationReviewRequest(BaseModel):
    """Request payload for ``POST /api/observations/review``.

    Callers may supply either the Stage 6C compare inputs (legacy/direct
    mode) or a pre-computed Stage 6C response. The frontend uses the
    pre-computed form so Validation does not load observations and run
    the comparison engine twice.
    """

    model_config = ConfigDict(extra='forbid')

    system_id64: int = Field(gt=0)
    target_archetype: str | None = None
    prediction: dict[str, Any] | None = None
    observed_facts: list[ObservedFactInput] | None = None
    fact_load_limit: int = Field(default=500, ge=1, le=2000)
    comparison_result: PredictionObservationCompareResponse | None = None

    @model_validator(mode='after')
    def _exactly_one_review_input(self) -> ValidationReviewRequest:
        if (self.prediction is None) == (self.comparison_result is None):
            raise ValueError('supply exactly one of prediction or comparison_result')
        if self.comparison_result is not None:
            if self.observed_facts is not None:
                raise ValueError('observed_facts cannot be combined with comparison_result')
            if self.comparison_result.system_id64 != self.system_id64:
                raise ValueError('comparison_result system_id64 must match request system_id64')
            if self.comparison_result.target_archetype != self.target_archetype:
                raise ValueError('comparison_result target_archetype must match request target_archetype')
        return self


class ValidationReviewSignalResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    signal_id: str
    area: str
    severity: str
    confidence: str
    status: str
    title: str
    message: str
    recommended_action: str | None = None
    comparison_ids: list[str] = Field(default_factory=list)


class ValidationReviewSummaryResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    overall_review_status: str
    confidence_impact: str
    highest_severity: str
    review_needed_count: int
    evidence_strength: str
    primary_review_areas: list[str] = Field(default_factory=list)
    summary: str


class ValidationReviewResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_id64: int
    target_archetype: str | None
    generated_at: str
    summary: ValidationReviewSummaryResponse
    signals: list[ValidationReviewSignalResponse] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
