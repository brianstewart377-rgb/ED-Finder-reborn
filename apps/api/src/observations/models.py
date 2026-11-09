"""Observed-facts domain models.

This module hosts two related but distinct sets of models:

1. Stage 4D legacy comparison models (``ObservedFact``,
   ``PredictionObservationDiff``, ``ObservationSummary``, plus the
   ``ObservationArea`` / ``ObservationSourceType`` / ``ObservationComparisonStatus``
   / ``ObservationSeverity`` enums). These describe ad-hoc observed facts and
   the predicted-vs-observed comparison summary used by older simulation
   integrations. They are retained so that pre-existing comparison code paths
   keep working until the Stage 6 comparison engine replaces them.

2. Stage 6A persisted-observation models (``PersistedObservedFact``,
   ``ObservationFactSummary``, ``ObservationSource``, ``ObservedFactType``,
   ``ObservedSubjectType``, ``ObservedStatus``, ``ObservedConfidence`` and
   ``summarise_observed_facts``). These define the new passive
   "evidence shelf" data contract used by the Stage 6A CRUD API.

Stage 6A creates a *passive* evidence shelf for manual / test-fixture
observations. Observations are recorded **separately** from predictions and
**must not** mutate simulation mechanics, optimiser ranking, candidate
generation, CP / economy / service / buildability rules, or Simulation
Preview scoring. Later stages (6B/6C) may compare predictions with
observations or add ingestion UIs, but this module only defines the
observation data contract; it does not consume observations to change
predicted behaviour.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from edfinder_api.mechanics.confidence import CanonicalConfidence


STANDARD_CONFIDENCE_LABELS = {
    'observed',
    'verified',
    'community_observed',
    'inferred',
    'estimated',
    'speculative',
    'unknown',
}


class ObservationArea(str, Enum):
    SLOTS = 'slots'
    SERVICES = 'services'
    SERVICE_UNLOCKS = 'service_unlocks'
    ECONOMY_OUTCOME = 'economy_outcome'
    BUILD_STEP = 'build_step'
    CP_BALANCE = 'cp_balance'
    COLONY_PROGRESS = 'colony_progress'
    TOPOLOGY = 'topology'


class ObservationSourceType(str, Enum):
    JOURNAL_UPLOAD = 'journal_upload'
    MANUAL_ENTRY = 'manual_entry'
    EDMC_IMPORT = 'edmc_import'
    API_IMPORT = 'api_import'
    TEST_FIXTURE = 'test_fixture'
    UNKNOWN = 'unknown'


class ObservationComparisonStatus(str, Enum):
    CONFIRMED = 'confirmed'
    MISMATCH = 'mismatch'
    OBSERVED_ONLY = 'observed_only'
    PREDICTED_ONLY = 'predicted_only'
    UNKNOWN = 'unknown'


class ObservationSeverity(str, Enum):
    INFO = 'info'
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'


@dataclass(frozen=True)
class ObservedFact:
    area: str
    subject_id: str
    subject_type: str
    observed_value: Any
    source_type: str
    observed_at: str | None = None
    system_id64: int | None = None
    body_id: str | None = None
    facility_id: str | None = None
    commander: str | None = None
    raw_event_ref: str | None = None
    confidence: str = 'observed'
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PredictionObservationDiff:
    area: str
    subject_id: str
    subject_type: str
    predicted_value: Any
    observed_value: Any
    status: str
    severity: str
    confidence: str
    reason: str
    recommended_action: str | None = None
    source_type: str | None = None
    observed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObservationSummary:
    status: str
    observed_facts_count: int
    confirmed_count: int
    mismatch_count: int
    observed_only_count: int
    predicted_only_count: int
    unknown_count: int
    confidence_impact: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def observed_fact_from_any(value: Any) -> ObservedFact:
    if isinstance(value, ObservedFact):
        return value
    if isinstance(value, dict):
        confidence = str(value.get('confidence') or 'observed')
        if confidence not in STANDARD_CONFIDENCE_LABELS:
            confidence = 'unknown'
        return ObservedFact(
            area=str(value.get('area') or ''),
            subject_id=str(value.get('subject_id') or ''),
            subject_type=str(value.get('subject_type') or ''),
            observed_value=value.get('observed_value'),
            source_type=str(value.get('source_type') or ObservationSourceType.UNKNOWN.value),
            observed_at=value.get('observed_at'),
            system_id64=value.get('system_id64'),
            body_id=value.get('body_id'),
            facility_id=value.get('facility_id'),
            commander=value.get('commander') or value.get('source_commander'),
            raw_event_ref=value.get('raw_event_ref'),
            confidence=confidence,
            notes=list(value.get('notes') or []),
        )
    raise TypeError(f'Unsupported observed fact value: {type(value)!r}')


# ══════════════════════════════════════════════════════════════════════
# Stage 6A persisted observed-fact foundation
# ══════════════════════════════════════════════════════════════════════
class ObservationSource(str, Enum):
    # Stage 6A accepts ``manual`` and ``test_fixture`` sources through the
    # public API. ``imported`` and ``inferred`` are reserved enum values for
    # later ingestion/comparison stages (e.g. EDMC/journal ingestion in 6B,
    # automated inference in 6C) and are intentionally rejected by Stage 6A
    # request validation so they cannot be silently introduced before those
    # stages define their own provenance rules.
    MANUAL = 'manual'
    IMPORTED = 'imported'
    INFERRED = 'inferred'
    TEST_FIXTURE = 'test_fixture'


class ObservedFactType(str, Enum):
    SERVICE_PRESENCE = 'service_presence'
    ECONOMY_PRESENCE = 'economy_presence'
    FACILITY_STATE = 'facility_state'
    CP_VALUE = 'cp_value'
    BUILD_OUTCOME = 'build_outcome'
    PREDICTION_MATCH = 'prediction_match'
    PREDICTION_MISMATCH = 'prediction_mismatch'
    NOTE = 'note'


class ObservedSubjectType(str, Enum):
    SYSTEM = 'system'
    BODY = 'body'
    FACILITY = 'facility'
    SERVICE = 'service'
    ECONOMY = 'economy'
    BUILD = 'build'
    SIMULATION = 'simulation'
    CP = 'cp'


class ObservedStatus(str, Enum):
    OBSERVED_PRESENT = 'observed_present'
    OBSERVED_ABSENT = 'observed_absent'
    CONFIRMED = 'confirmed'
    CONTRADICTED = 'contradicted'
    UNKNOWN = 'unknown'
    UNVERIFIED = 'unverified'


class ObservedConfidence(str, Enum):
    """User-submitted observation confidence (3-value scale).

    Maps to EDRE confidence bands via from_canonical().
    Ref: docs/reference/colonisation/confidence-vocabulary-reconciliation.md §7.2
    """
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'

    @classmethod
    def from_canonical(cls, canonical: CanonicalConfidence) -> ObservedConfidence:
        """Derive 3-value confidence from canonical shape.

        Maps EDRE confidence bands to the 3-value display scale:
        - High (85–100) → HIGH
        - Usable/Exploratory (50–84) → MEDIUM
        - Weak/Insufficient (<50) → LOW

        Args:
            canonical: CanonicalConfidence instance

        Returns:
            ObservedConfidence value
        """
        from edfinder_api.mechanics.confidence import ConfidenceBand

        band = canonical.band
        if band == ConfidenceBand.HIGH:
            return cls.HIGH
        elif band in (ConfidenceBand.USABLE, ConfidenceBand.EXPLORATORY):
            return cls.MEDIUM
        else:  # WEAK or INSUFFICIENT
            return cls.LOW


JsonValue = Any


@dataclass(frozen=True)
class PersistedObservedFact:
    """A persisted observed fact with canonical confidence tracking.

    ``confidence`` is the legacy 3-value display field (for API compatibility).
    ``canonical_confidence`` carries the EDRE-aligned canonical shape when available.
    """
    observation_id: str
    system_id64: int
    created_at: str
    updated_at: str | None
    source: str
    fact_type: str
    subject_type: str
    subject_id: str | None
    status: str
    observed_value: JsonValue | None = None
    expected_value: JsonValue | None = None
    confidence: str = ObservedConfidence.MEDIUM.value
    notes: str | None = None
    build_fingerprint: str | None = None
    simulation_fingerprint: str | None = None
    target_archetype: str | None = None
    facility_template_id: str | None = None
    local_body_id: str | None = None
    service_id: str | None = None
    economy: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    canonical_confidence: CanonicalConfidence | None = None  # Future: always populated

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.canonical_confidence is not None:
            data['canonical_confidence'] = self.canonical_confidence.to_dict()
        return data


@dataclass(frozen=True)
class ObservationFactSummary:
    total_count: int
    by_fact_type: dict[str, int]
    by_status: dict[str, int]
    by_confidence: dict[str, int]
    latest_observed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarise_observed_facts(facts: list[PersistedObservedFact]) -> ObservationFactSummary:
    by_fact_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    latest_observed_at: str | None = None

    for fact in facts:
        by_fact_type[fact.fact_type] = by_fact_type.get(fact.fact_type, 0) + 1
        by_status[fact.status] = by_status.get(fact.status, 0) + 1
        by_confidence[fact.confidence] = by_confidence.get(fact.confidence, 0) + 1
        observed_at = fact.updated_at or fact.created_at
        if observed_at and (latest_observed_at is None or observed_at > latest_observed_at):
            latest_observed_at = observed_at

    return ObservationFactSummary(
        total_count=len(facts),
        by_fact_type=by_fact_type,
        by_status=by_status,
        by_confidence=by_confidence,
        latest_observed_at=latest_observed_at,
    )
