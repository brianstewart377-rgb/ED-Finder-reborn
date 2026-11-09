"""Stage 6C predicted-vs-observed comparison models.

These dataclasses describe the deterministic output of the Stage 6C
comparison engine. They are intentionally separate from the legacy
Stage 4D comparison models (``ObservedFact`` / ``PredictionObservationDiff``
/ ``ObservationSummary`` in ``observations.models``) so that:

* the Stage 4D code path used by ``simulate_build_preview`` keeps running
  unchanged;
* the new Stage 6C engine can evolve its own status / severity / summary
  vocabulary tuned to the persisted Stage 6A observed-fact contract;
* downstream Stage 6D UI rendering has a single, explicit response
  shape to consume.

Stage 6C is **comparison only**. Nothing in this module is consumed by
Simulation Preview scoring, optimiser candidate generation, or optimiser
ranking. See ``docs/ROADMAP.md`` for the
boundary.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from edfinder_api.mechanics.confidence import CanonicalConfidence


# ──────────────────────────────────────────────────────────────────────
# Enumerated vocabularies
# ──────────────────────────────────────────────────────────────────────
class ComparisonStatus(str, Enum):
    """Per-comparison status for a single (prediction subject, observation) pair."""

    CONFIRMED = 'confirmed'
    CONTRADICTED = 'contradicted'
    PREDICTED_ONLY = 'predicted_only'
    OBSERVED_ONLY = 'observed_only'
    UNKNOWN = 'unknown'
    UNVERIFIED = 'unverified'


class ComparisonSeverity(str, Enum):
    INFO = 'info'
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'


class ComparisonConfidence(str, Enum):
    """Comparison engine confidence (4-value scale).

    Maps to EDRE confidence bands via from_canonical().
    Ref: docs/reference/colonisation/confidence-vocabulary-reconciliation.md §7.2
    """
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    UNKNOWN = 'unknown'

    @classmethod
    def from_canonical(cls, canonical: CanonicalConfidence) -> ComparisonConfidence:
        """Derive 4-value confidence from canonical shape.

        Maps EDRE confidence bands to the comparison-engine display scale:
        - High (85–100) → HIGH
        - Usable/Exploratory (50–84) → MEDIUM
        - Weak (<50) → LOW
        - Insufficient → UNKNOWN

        Args:
            canonical: CanonicalConfidence instance

        Returns:
            ComparisonConfidence value
        """
        from edfinder_api.mechanics.confidence import ConfidenceBand

        band = canonical.band
        if band == ConfidenceBand.HIGH:
            return cls.HIGH
        elif band in (ConfidenceBand.USABLE, ConfidenceBand.EXPLORATORY):
            return cls.MEDIUM
        elif band == ConfidenceBand.WEAK:
            return cls.LOW
        else:  # INSUFFICIENT
            return cls.UNKNOWN


class ComparisonOverallStatus(str, Enum):
    """Top-level summary status for a full comparison run."""

    NO_OBSERVATIONS = 'no_observations'
    CONFIRMED = 'confirmed'
    MIXED = 'mixed'
    NEEDS_REVIEW = 'needs_review'
    INSUFFICIENT_EVIDENCE = 'insufficient_evidence'


class ComparisonConfidenceImpact(str, Enum):
    """How the observations move our trust in the prediction.

    Stage 6C is conservative: confidence impact is reported but **must
    not** be plumbed back into Simulation Preview scoring or optimiser
    ranking. It is a UI hint only.
    """

    NONE = 'none'
    STRENGTHENED = 'strengthened'
    WEAKENED = 'weakened'
    MIXED = 'mixed'
    INSUFFICIENT_EVIDENCE = 'insufficient_evidence'


class ComparisonArea(str, Enum):
    """High-level grouping for a comparison row.

    Areas are deliberately broad. Fine-grained subject identity goes in
    ``subject_type`` + ``subject_id``.
    """

    SERVICE = 'service'
    ECONOMY = 'economy'
    CP = 'cp'
    FACILITY = 'facility'
    BUILD_OUTCOME = 'build_outcome'
    NOTE = 'note'
    OTHER = 'other'


# ──────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ObservationEvidenceMatch:
    """One observed fact that contributed to a comparison row.

    The full ``PersistedObservedFact`` is not embedded here — only the
    fields a UI needs to render the evidence shelf alongside the
    comparison. This keeps the response compact and avoids leaking
    server-only fields like ``build_fingerprint``.
    """

    observation_id: str
    fact_type: str
    subject_type: str
    subject_id: str | None
    status: str
    confidence: str
    observed_value: Any | None = None
    expected_value: Any | None = None
    notes: str | None = None


@dataclass(frozen=True)
class PredictionObservationComparison:
    """One predicted-vs-observed comparison row.

    ``predicted_value`` and ``observed_value`` are kept loose (``Any``)
    so the same dataclass can describe a service status string, an
    economy list, or a CP numeric/object value.

    ``confidence`` is the legacy 4-value display field (for API compatibility).
    ``canonical_confidence`` carries the EDRE-aligned canonical shape when available.
    """

    comparison_id: str
    area: str
    subject_type: str
    subject_id: str | None
    predicted_value: Any | None
    observed_value: Any | None
    status: str
    severity: str
    confidence: str
    reason: str
    recommended_action: str | None = None
    evidence: list[ObservationEvidenceMatch] = field(default_factory=list)
    prediction_source: str | None = None
    canonical_confidence: CanonicalConfidence | None = None  # Future: always populated

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict, including canonical confidence if present."""
        data = asdict(self)
        if self.canonical_confidence is not None:
            data['canonical_confidence'] = self.canonical_confidence.to_dict()
        return data


@dataclass(frozen=True)
class PredictionObservationComparisonSummary:
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


@dataclass(frozen=True)
class PredictionObservationComparisonResult:
    system_id64: int
    target_archetype: str | None
    generated_at: str
    summary: PredictionObservationComparisonSummary
    comparisons: list[PredictionObservationComparison]
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────
# Serialisation helpers
# ──────────────────────────────────────────────────────────────────────
# ``asdict`` already produces JSON-safe primitives for the shapes we
# emit (str/int/float/bool/None/list/dict). We still wrap them in named
# helpers so call sites read self-documenting and tests can assert on
# exact dict keys without poking at dataclass internals.
def evidence_match_to_dict(match: ObservationEvidenceMatch) -> dict[str, Any]:
    return asdict(match)


def comparison_to_dict(comparison: PredictionObservationComparison) -> dict[str, Any]:
    return asdict(comparison)


def comparison_summary_to_dict(summary: PredictionObservationComparisonSummary) -> dict[str, Any]:
    return asdict(summary)


def comparison_result_to_dict(result: PredictionObservationComparisonResult) -> dict[str, Any]:
    return asdict(result)


def comparison_result_from_dict(
    payload: Mapping[str, Any],
) -> PredictionObservationComparisonResult:
    """Rehydrate a JSON-safe Stage 6C result for Stage 6E review.

    The review endpoint accepts a pre-computed comparison result so a
    caller that already rendered Stage 6C rows does not make the backend
    load evidence and run the comparison engine a second time.
    """
    summary_payload = payload.get('summary')
    if not isinstance(summary_payload, Mapping):
        raise TypeError('comparison result summary must be a mapping')

    comparison_payloads = payload.get('comparisons', [])
    if not isinstance(comparison_payloads, list):
        raise TypeError('comparison result comparisons must be a list')

    comparisons: list[PredictionObservationComparison] = []
    for comparison_payload in comparison_payloads:
        if not isinstance(comparison_payload, Mapping):
            raise TypeError('comparison result row must be a mapping')
        row = dict(comparison_payload)
        evidence_payloads = row.pop('evidence', [])
        if not isinstance(evidence_payloads, list):
            raise TypeError('comparison result evidence must be a list')
        row['evidence'] = [
            ObservationEvidenceMatch(**dict(evidence_payload))
            for evidence_payload in evidence_payloads
            if isinstance(evidence_payload, Mapping)
        ]
        comparisons.append(PredictionObservationComparison(**row))

    return PredictionObservationComparisonResult(
        system_id64=int(payload['system_id64']),
        target_archetype=payload.get('target_archetype'),
        generated_at=str(payload['generated_at']),
        summary=PredictionObservationComparisonSummary(**dict(summary_payload)),
        comparisons=comparisons,
        warnings=list(payload.get('warnings', [])),
        assumptions=list(payload.get('assumptions', [])),
    )


__all__ = [
    'ComparisonArea',
    'ComparisonConfidence',
    'ComparisonConfidenceImpact',
    'ComparisonOverallStatus',
    'ComparisonSeverity',
    'ComparisonStatus',
    'ObservationEvidenceMatch',
    'PredictionObservationComparison',
    'PredictionObservationComparisonResult',
    'PredictionObservationComparisonSummary',
    'comparison_result_to_dict',
    'comparison_result_from_dict',
    'comparison_summary_to_dict',
    'comparison_to_dict',
    'evidence_match_to_dict',
]

