"""Structured confidence/data-quality signals for colony planning outputs.

This module defines a canonical confidence model aligned with EDRE's (Elite Dangerous
Research Engine) confidence/source-authority taxonomy:

- SourceAuthority: who said it (official, community, live evidence, inferred, etc.)
- ConfidenceBand: how sure we are (High 85–100, Usable 70–84, Exploratory 50–69, Weak <50)
- ConfidenceLayer: which interpretation layer (observation, catalogue, operational, etc.)
- CanonicalConfidence: the single record combining all three dimensions

Display enums like ConfidenceLevel remain as *projections* of the canonical shape
for backward compatibility and UI convenience.

Reference: docs/reference/colonisation/confidence-vocabulary-reconciliation.md
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class SourceAuthority(str, Enum):
    """EDRE SA-register: source classes for confidence attribution.

    Ref: docs/reference/colonisation/confidence-vocabulary-reconciliation.md §7.1
    """
    OFFICIAL = 'official'  # SA-0001: official mechanics/patch notes
    COMMUNITY = 'community'  # SA-0002: DaftMav, Mega Guide (interpretation, not canonical)
    LIVE_EVIDENCE = 'live_evidence'  # SA-0003: in-game observation / EDDN
    INFERRED = 'inferred'  # SA-0004: derived from other layers
    NARRATIVE = 'narrative'  # SA-0005: narrative/player-submitted evidence
    IDENTITY = 'identity'  # SA-0006: identity-collision / ambiguity


class ConfidenceBand(str, Enum):
    """EDRE confidence bands based on 0–100 score.

    Ref: docs/reference/colonisation/confidence-vocabulary-reconciliation.md §3, §7.1
    """
    HIGH = 'high'  # 85–100: may drive automation
    USABLE = 'usable'  # 70–84: usable with review
    EXPLORATORY = 'exploratory'  # 50–69: exploratory only
    WEAK = 'weak'  # <50: discovery-lead
    INSUFFICIENT = 'insufficient'  # no assessment


class ConfidenceLayer(str, Enum):
    """EDRE layer taxonomy: where confidence is tracked.

    Confidence tracked separately per layer; high confidence in one does not
    imply high confidence in another. Ref: CM-0001, CM-0006A.

    Ref: docs/reference/colonisation/confidence-vocabulary-reconciliation.md §6
    """
    OBSERVATION = 'observation'  # User-submitted observed facts
    CATALOGUE = 'catalogue'  # Facility catalogue metadata
    PROJECTED_OPERATIONAL = 'projected_operational'  # Generated plan projections
    EVIDENCE_RECONCILIATION = 'evidence_reconciliation'  # Observation-vs-prediction
    MECHANICS = 'mechanics'  # Colonisation mechanics rules (future)


@dataclass(frozen=True)
class CanonicalConfidence:
    """Single source of truth for confidence, aligned with EDRE's model.

    Each confidence-bearing value carries three dimensions:
    - source_authority: who said it (SourceAuthority)
    - score: 0–100 numeric score (EDRE six-component model input)
    - band: derived band from score (ConfidenceBand)
    - layer: which interpretation layer (ConfidenceLayer)
    - reason: human-readable explanation

    Ref: docs/reference/colonisation/confidence-vocabulary-reconciliation.md §5, §8
    """
    source_authority: SourceAuthority
    score: float  # 0–100
    band: ConfidenceBand
    layer: ConfidenceLayer
    reason: str

    def __post_init__(self) -> None:
        """Validate that score matches band."""
        if self.score < 0 or self.score > 100:
            raise ValueError(f'confidence score must be 0–100, got {self.score}')

        # INSUFFICIENT is a special case: used when there truly is no assessment
        if self.band == ConfidenceBand.INSUFFICIENT:
            if self.score != 0.0:
                raise ValueError(
                    f'INSUFFICIENT band must have score 0.0, got {self.score}'
                )
            return

        expected_band = self._band_from_score(self.score)
        if self.band != expected_band:
            raise ValueError(
                f'score {self.score} maps to band {expected_band.value}, '
                f'but band is {self.band.value}'
            )

    @staticmethod
    def _band_from_score(score: float) -> ConfidenceBand:
        """Derive band from numeric score.

        Note: INSUFFICIENT is not derived from score—it's only set explicitly
        when there is truly no assessment. Use score=0.0 + band=INSUFFICIENT.
        """
        if score >= 85:
            return ConfidenceBand.HIGH
        elif score >= 70:
            return ConfidenceBand.USABLE
        elif score >= 50:
            return ConfidenceBand.EXPLORATORY
        else:
            return ConfidenceBand.WEAK

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON/API responses."""
        return {
            'source_authority': self.source_authority.value,
            'score': self.score,
            'band': self.band.value,
            'layer': self.layer.value,
            'reason': self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CanonicalConfidence:
        """Deserialize from dict."""
        return cls(
            source_authority=SourceAuthority(data['source_authority']),
            score=float(data['score']),
            band=ConfidenceBand(data['band']),
            layer=ConfidenceLayer(data['layer']),
            reason=str(data['reason']),
        )


class ConfidenceLevel(str, Enum):
    """Display projection of CanonicalConfidence for UI and backward compatibility.

    These are *derived* from the canonical shape, not independent sources of truth.
    The from_canonical() method is the canonical translation path.

    Ref: docs/reference/colonisation/confidence-vocabulary-reconciliation.md §7.1
    """
    OBSERVED = 'observed'
    VERIFIED = 'verified'
    COMMUNITY_OBSERVED = 'community_observed'
    INFERRED = 'inferred'
    ESTIMATED = 'estimated'
    SPECULATIVE = 'speculative'
    UNKNOWN = 'unknown'

    @classmethod
    def from_canonical(cls, canonical: CanonicalConfidence) -> ConfidenceLevel:
        """Derive display level from canonical confidence.

        Maps the canonical (source_authority, band) pair back to a display enum
        for UI consumption. This is the reverse mapping of the reconciliation doc.

        Ref: docs/reference/colonisation/confidence-vocabulary-reconciliation.md §7.1
        """
        # Special case: insufficient/unknown
        if canonical.band == ConfidenceBand.INSUFFICIENT:
            return cls.UNKNOWN

        # Axis-conflation split: source-authority classes vs. confidence-degree
        if canonical.source_authority == SourceAuthority.OFFICIAL:
            # Official source with high/usable band → verified
            if canonical.band in (ConfidenceBand.HIGH, ConfidenceBand.USABLE):
                return cls.VERIFIED
            # Official source with lower confidence still high-quality
            if canonical.band == ConfidenceBand.EXPLORATORY:
                return cls.COMMUNITY_OBSERVED
            return cls.ESTIMATED

        if canonical.source_authority == SourceAuthority.LIVE_EVIDENCE:
            # Live evidence (in-game observation / EDDN)
            if canonical.band in (ConfidenceBand.HIGH, ConfidenceBand.USABLE):
                return cls.OBSERVED
            if canonical.band == ConfidenceBand.EXPLORATORY:
                return cls.INFERRED
            return cls.ESTIMATED

        if canonical.source_authority == SourceAuthority.COMMUNITY:
            # Community-sourced (DaftMav, guides) — conditional per CM-0007
            if canonical.band in (ConfidenceBand.HIGH, ConfidenceBand.USABLE):
                return cls.COMMUNITY_OBSERVED
            if canonical.band == ConfidenceBand.EXPLORATORY:
                return cls.INFERRED
            return cls.ESTIMATED

        if canonical.source_authority in (SourceAuthority.INFERRED, SourceAuthority.IDENTITY):
            # Derived or ambiguous
            if canonical.band in (ConfidenceBand.HIGH, ConfidenceBand.USABLE):
                return cls.OBSERVED
            if canonical.band == ConfidenceBand.EXPLORATORY:
                return cls.INFERRED
            if canonical.band == ConfidenceBand.WEAK:
                return cls.SPECULATIVE if canonical.source_authority == SourceAuthority.IDENTITY else cls.ESTIMATED
            return cls.UNKNOWN

        if canonical.source_authority == SourceAuthority.NARRATIVE:
            # Narrative evidence (player-submitted)
            if canonical.band in (ConfidenceBand.HIGH, ConfidenceBand.USABLE):
                return cls.OBSERVED
            if canonical.band == ConfidenceBand.EXPLORATORY:
                return cls.INFERRED
            return cls.ESTIMATED

        # Fallback
        return cls.UNKNOWN

    def to_canonical(self, *, layer: ConfidenceLayer, reason: str = '') -> CanonicalConfidence:
        """Convert this display level to canonical form.

        This is the forward mapping: from legacy ConfidenceLevel → CanonicalConfidence.
        Used during adoption to populate the canonical shape from existing code.

        Args:
            layer: which interpretation layer this confidence belongs to
            reason: human-readable explanation (defaults to level description)

        Ref: docs/reference/colonisation/confidence-vocabulary-reconciliation.md §7.1
        """
        if not reason:
            reason = self._default_reason()

        # Map to (source_authority, score/band) pair
        if self == ConfidenceLevel.VERIFIED:
            # Verified: high confidence + official/live evidence
            return CanonicalConfidence(
                source_authority=SourceAuthority.OFFICIAL,
                score=90.0,
                band=ConfidenceBand.HIGH,
                layer=layer,
                reason=reason,
            )

        if self == ConfidenceLevel.OBSERVED:
            # Observed: high–usable confidence + live evidence
            return CanonicalConfidence(
                source_authority=SourceAuthority.LIVE_EVIDENCE,
                score=85.0,
                band=ConfidenceBand.HIGH,
                layer=layer,
                reason=reason,
            )

        if self == ConfidenceLevel.COMMUNITY_OBSERVED:
            # Community-observed: usable–exploratory + community source
            return CanonicalConfidence(
                source_authority=SourceAuthority.COMMUNITY,
                score=75.0,
                band=ConfidenceBand.USABLE,
                layer=layer,
                reason=reason,
            )

        if self == ConfidenceLevel.INFERRED:
            # Inferred: exploratory confidence + derived/inferred source
            return CanonicalConfidence(
                source_authority=SourceAuthority.INFERRED,
                score=60.0,
                band=ConfidenceBand.EXPLORATORY,
                layer=layer,
                reason=reason,
            )

        if self == ConfidenceLevel.ESTIMATED:
            # Estimated: weak confidence + discovery-lead
            return CanonicalConfidence(
                source_authority=SourceAuthority.INFERRED,
                score=40.0,
                band=ConfidenceBand.WEAK,
                layer=layer,
                reason=reason,
            )

        if self == ConfidenceLevel.SPECULATIVE:
            # Speculative: weak confidence + disputed/bugged (identity flag)
            return CanonicalConfidence(
                source_authority=SourceAuthority.IDENTITY,
                score=35.0,
                band=ConfidenceBand.WEAK,
                layer=layer,
                reason=reason,
            )

        if self == ConfidenceLevel.UNKNOWN:
            # Unknown: insufficient evidence, no band
            return CanonicalConfidence(
                source_authority=SourceAuthority.INFERRED,
                score=0.0,
                band=ConfidenceBand.INSUFFICIENT,
                layer=layer,
                reason=reason or 'No assessment available',
            )

        raise ValueError(f'Unknown ConfidenceLevel: {self}')

    def _default_reason(self) -> str:
        """Default explanation for each confidence level."""
        return {
            ConfidenceLevel.VERIFIED: 'Verified from official source or direct observation',
            ConfidenceLevel.OBSERVED: 'Directly observed in-game',
            ConfidenceLevel.COMMUNITY_OBSERVED: 'Community-observed from guides (conditional)',
            ConfidenceLevel.INFERRED: 'Derived from documented mechanics',
            ConfidenceLevel.ESTIMATED: 'Estimated from available data',
            ConfidenceLevel.SPECULATIVE: 'Speculative or disputed',
            ConfidenceLevel.UNKNOWN: 'No assessment available',
        }.get(self, '')


@dataclass(frozen=True)
class ConfidenceSignal:
    """Signal capturing a single confidence assessment about simulation output.

    Eventually carries both a display level (ConfidenceLevel) and the canonical
    shape (CanonicalConfidence) so that both old and new code can work.
    """
    area: str
    level: ConfidenceLevel
    reason: str
    impact: Optional[float] = None
    canonical: Optional[CanonicalConfidence] = None  # Future: always populated

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            'area': self.area,
            'level': self.level.value,
            'reason': self.reason,
        }
        if self.impact is not None:
            data['impact'] = self.impact
        if self.canonical is not None:
            data['canonical'] = self.canonical.to_dict()
        return data


def score_to_band(score: float) -> ConfidenceBand:
    """Convert 0–1 or 0–100 numeric score to confidence band.

    Common pattern for archetype scoring and body-data completeness.
    Handles both 0–1 and 0–100 ranges (normalizes 0–1 to 0–100 first).
    """
    normalized = score * 100 if score <= 1.0 else score
    if normalized >= 85:
        return ConfidenceBand.HIGH
    elif normalized >= 70:
        return ConfidenceBand.USABLE
    elif normalized >= 50:
        return ConfidenceBand.EXPLORATORY
    else:
        return ConfidenceBand.WEAK


def score_to_confidence_level(score: float, *, default_source: SourceAuthority = SourceAuthority.INFERRED) -> ConfidenceLevel:
    """Quick conversion: numeric score → display ConfidenceLevel.

    Used for archetype scoring and similar numeric-confidence cases.
    Normalizes 0–1 and 0–100 range to band, then to display level.
    """
    band = score_to_band(score)
    canonical = CanonicalConfidence(
        source_authority=default_source,
        score=min(100.0, score * 100 if score <= 1.0 else score),
        band=band,
        layer=ConfidenceLayer.PROJECTED_OPERATIONAL,
        reason='Numeric confidence score',
    )
    return ConfidenceLevel.from_canonical(canonical)


def default_data_quality(*, has_regional_context: bool = False) -> dict[str, str]:
    return {
        'slots': 'estimated',
        'facility_catalogue': ConfidenceLevel.COMMUNITY_OBSERVED.value,
        'topology': ConfidenceLevel.INFERRED.value,
        'economy_stack': ConfidenceLevel.INFERRED.value,
        'services': ConfidenceLevel.ESTIMATED.value,
        'regional_position': ConfidenceLevel.INFERRED.value if has_regional_context else ConfidenceLevel.UNKNOWN.value,
    }


def simulation_confidence_signals(
    *,
    slot_confidence: Optional[float],
    has_slot_estimates: bool,
    estimated_facility_count: int,
    inherited_profiles: list[Any],
    services: dict[str, Any],
    warnings: list[str],
) -> list[ConfidenceSignal]:
    signals = [
        ConfidenceSignal(
            area='facility_catalogue',
            level=ConfidenceLevel.COMMUNITY_OBSERVED,
            reason='Facility catalogue is community observed from the DaftMav workbook.',
        ),
        ConfidenceSignal(
            area='topology',
            level=ConfidenceLevel.INFERRED,
            reason='Topology roles are inferred from selected placements, local body ids, tiers, and build order.',
        ),
        ConfidenceSignal(
            area='economy_stack',
            level=ConfidenceLevel.INFERRED,
            reason='Economy stack is simulated from documented body inheritance, facility economies, and topology links.',
        ),
    ]
    if not has_slot_estimates:
        signals.append(ConfidenceSignal(
            area='slots',
            level=ConfidenceLevel.ESTIMATED,
            reason='No observed slot data available; using selected placements and predicted topology capacity.',
            impact=-0.18,
        ))
    elif slot_confidence is not None and slot_confidence < 0.75:
        signals.append(ConfidenceSignal(
            area='slots',
            level=ConfidenceLevel.ESTIMATED,
            reason='Slot data is estimated from body scan data rather than observed colony outcomes.',
            impact=round((0.75 - slot_confidence) * -0.2, 3),
        ))
    else:
        signals.append(ConfidenceSignal(
            area='slots',
            level=ConfidenceLevel.INFERRED,
            reason='Slot estimates are present and used as planning constraints.',
        ))
    if estimated_facility_count:
        signals.append(ConfidenceSignal(
            area='facility_catalogue',
            level=ConfidenceLevel.ESTIMATED,
            reason=f'{estimated_facility_count} selected facility templates are marked estimated.',
            impact=round(-min(0.15, estimated_facility_count * 0.04), 3),
        ))
    if any(getattr(profile, 'purity', 1.0) < 0.6 for profile in inherited_profiles):
        signals.append(ConfidenceSignal(
            area='body_inheritance',
            level=ConfidenceLevel.INFERRED,
            reason='Low-purity mixed body inheritance can reduce specialised economy stability.',
            impact=-0.08,
        ))
    if any('Terraformable strong-link boost' in warning for warning in warnings):
        signals.append(ConfidenceSignal(
            area='link_modifiers',
            level=ConfidenceLevel.SPECULATIVE,
            reason='Terraformable agriculture modifier is speculative/bugged and is not treated as verified.',
            impact=-0.03,
        ))
    unknown_services = [
        service for service, detail in services.items()
        if isinstance(detail, dict) and detail.get('status') == ConfidenceLevel.UNKNOWN.value
    ]
    if unknown_services:
        signals.append(ConfidenceSignal(
            area='services',
            level=ConfidenceLevel.UNKNOWN,
            reason='Some service unlock mechanics are not documented enough to claim active or locked.',
            impact=-0.04,
        ))
    else:
        signals.append(ConfidenceSignal(
            area='services',
            level=ConfidenceLevel.INFERRED,
            reason='Service states are inferred from documented catalogue unlocks and strong-link topology.',
        ))
    return signals


def signals_to_dict(signals: list[ConfidenceSignal]) -> list[dict[str, Any]]:
    return [signal.to_dict() for signal in signals]


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3: Facility catalogue & archetype scoring
# ──────────────────────────────────────────────────────────────────────────────

def facility_data_confidence_to_canonical(
    data_confidence: str,
) -> CanonicalConfidence:
    """Map facility catalogue data_confidence to canonical form.

    Facility data_confidence indicates certainty of CP values:
    - 'confirmed' → High (in authoritative community catalogue, e.g. DaftMav)
    - 'observed' → Usable (community reports, high confidence)
    - 'estimated' → Exploratory (extrapolated / not widely verified)
    - (unrecognized) → Insufficient (no assessment)

    Ref: docs/reference/colonisation/confidence-vocabulary-reconciliation.md §7.3
    """
    if data_confidence == 'confirmed':
        # 'confirmed' means in authoritative catalogue (DaftMav), not live-observed.
        # Use OFFICIAL to represent community-authoritative source (SA-0002).
        return CanonicalConfidence(
            source_authority=SourceAuthority.OFFICIAL,
            score=92.0,
            band=ConfidenceBand.HIGH,
            layer=ConfidenceLayer.CATALOGUE,
            reason='Facility in authoritative community catalogue (DaftMav)',
        )
    elif data_confidence == 'observed':
        return CanonicalConfidence(
            source_authority=SourceAuthority.COMMUNITY,
            score=80.0,
            band=ConfidenceBand.USABLE,
            layer=ConfidenceLayer.CATALOGUE,
            reason='Seen in community reports, high confidence',
        )
    elif data_confidence == 'estimated':
        return CanonicalConfidence(
            source_authority=SourceAuthority.INFERRED,
            score=55.0,
            band=ConfidenceBand.EXPLORATORY,
            layer=ConfidenceLayer.CATALOGUE,
            reason='Extrapolated / not widely verified',
        )
    else:
        # Unrecognized or missing data_confidence: no assessment.
        return CanonicalConfidence(
            source_authority=SourceAuthority.INFERRED,
            score=0.0,
            band=ConfidenceBand.INSUFFICIENT,
            layer=ConfidenceLayer.CATALOGUE,
            reason=f'Unrecognized data_confidence value: {data_confidence!r}',
        )


def archetype_numeric_confidence_to_canonical(
    score: float,
    *,
    layer: ConfidenceLayer = ConfidenceLayer.PROJECTED_OPERATIONAL,
    reason: str = 'Numeric archetype confidence',
) -> CanonicalConfidence:
    """Map numeric archetype confidence (0–1 or 0–100) to canonical form.

    Used for:
    - archetype.data_confidence (body-data completeness)
    - archetype_confidence (archetype match strength)

    Both are numeric 0–1 floats that map to confidence bands.
    Converted to 0–100 for canonical scoring.

    Ref: docs/reference/colonisation/confidence-vocabulary-reconciliation.md §7.4
    """
    normalized_score = score * 100 if score <= 1.0 else score
    normalized_score = min(100.0, max(0.0, normalized_score))
    band = score_to_band(score)

    return CanonicalConfidence(
        source_authority=SourceAuthority.INFERRED,
        score=normalized_score,
        band=band,
        layer=layer,
        reason=reason,
    )
