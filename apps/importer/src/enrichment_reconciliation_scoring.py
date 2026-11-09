"""Report-only confidence and risk scoring for enrichment reconciliation."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


CONFIDENCE_MODEL_SCHEMA_VERSION = 'enrichment_reconciliation_confidence/v1'
BLOCKING_RISK_FLAGS = {
    'ambiguous_canonical_match',
    'ambiguous_staged_body_evidence',
    'insufficient_identifiers',
    'missing_staged_body_evidence',
    'missing_station_body_name',
}
STALE_RISK_FLAGS = {'stale_source_evidence', 'undated_source_evidence'}
VOLATILE_RISK_FLAGS = {'volatile_source_class', 'volatile_source_evidence'}
RISKY_RISK_FLAGS = {
    'canonical_difference_review',
    'source_only_association',
    'source_only_evidence',
}


def candidate_confidence(
    *,
    entity: str,
    action: str,
    source_identity: Mapping[str, Any],
    canonical_matches: Sequence[Mapping[str, Any]],
    differences: Sequence[Mapping[str, Any]],
    warnings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build transparent confidence metadata for a report-only candidate."""
    identifier_quality = identifier_quality_for_candidate(entity, source_identity, action)
    source_freshness = source_freshness_impact(source_identity)
    risk_flags = candidate_risk_flags(action, warnings, source_identity, source_freshness)
    evidence_quality = evidence_quality_for_candidate(action, identifier_quality, canonical_matches, differences)
    reconciliation_state = reconciliation_state_for_candidate(action, canonical_matches, risk_flags)
    risk_class = risk_class_for_candidate(risk_flags)
    review_classifications = review_classifications_for_candidate(reconciliation_state, risk_flags)

    if action in {'insufficient_evidence', 'ambiguous_match'}:
        confidence = 'low'
    elif action == 'no_change':
        confidence = 'high' if identifier_quality == 'stable' and not risk_flags else 'medium'
    elif action == 'candidate_update':
        confidence = 'medium' if identifier_quality in {'stable', 'partial'} else 'low'
    elif action == 'candidate_insert_missing_canonical':
        confidence = 'medium' if identifier_quality == 'stable' else 'low'
    else:
        confidence = 'low'

    confidence = confidence_after_freshness(confidence, source_freshness, risk_flags)
    future_review_candidate = future_canonical_review_marker(
        action=action,
        risk_class=risk_class,
        risk_flags=risk_flags,
    )
    return {
        'confidence_model_version': CONFIDENCE_MODEL_SCHEMA_VERSION,
        'confidence': confidence,
        'confidence_level': confidence,
        'confidence_reasons': confidence_reasons(
            action=action,
            identifier_quality=identifier_quality,
            evidence_quality=evidence_quality,
            risk_flags=risk_flags,
            source_freshness=source_freshness,
            differences=differences,
        ),
        'confidence_explanations': confidence_explanations(
            action=action,
            identifier_quality=identifier_quality,
            evidence_quality=evidence_quality,
            risk_flags=risk_flags,
            source_freshness=source_freshness,
            reconciliation_state=reconciliation_state,
            risk_class=risk_class,
            differences=differences,
        ),
        'evidence_quality': evidence_quality,
        'identifier_quality': identifier_quality,
        'reconciliation_state': reconciliation_state,
        'risk_class': risk_class,
        'risk_flags': risk_flags,
        'risk_explanations': risk_explanations(risk_flags),
        'review_classifications': review_classifications,
        'source_freshness': source_freshness,
        'future_canonical_review_candidate': future_review_candidate,
        'report_only': True,
        'canonical_writes_planned': 0,
    }


def identifier_quality_for_candidate(entity: str, source_identity: Mapping[str, Any], action: str) -> str:
    if action == 'insufficient_evidence':
        return 'missing'
    if action == 'ambiguous_match':
        return 'ambiguous'

    has_system_id = not _missing(source_identity.get('system_id64'))
    has_system_name = not _missing(source_identity.get('system_name'))
    if entity == 'station':
        has_stable_entity_id = (
            not _missing(source_identity.get('market_id'))
            or not _missing(source_identity.get('edsm_station_id'))
        )
        has_entity_name = not _missing(source_identity.get('station_name'))
    elif entity == 'body':
        has_stable_entity_id = not _missing(source_identity.get('source_body_id'))
        has_entity_name = not _missing(source_identity.get('body_name'))
    else:
        has_stable_entity_id = (
            not _missing(source_identity.get('source_body_id'))
            and not _missing(source_identity.get('ring_name'))
        )
        has_entity_name = (
            not _missing(source_identity.get('body_name'))
            and not _missing(source_identity.get('ring_name'))
        )

    if has_system_id and has_stable_entity_id:
        return 'stable'
    if (has_system_id or has_system_name) and (has_stable_entity_id or has_entity_name):
        return 'partial'
    return 'missing'


def candidate_risk_flags(
    action: str,
    warnings: Sequence[Mapping[str, Any]],
    source_identity: Mapping[str, Any],
    source_freshness: Mapping[str, Any],
) -> list[str]:
    flags: list[str] = []
    if action == 'ambiguous_match':
        flags.append('ambiguous_canonical_match')
    if action == 'insufficient_evidence':
        flags.append('insufficient_identifiers')
    if action == 'candidate_update':
        flags.append('canonical_difference_review')
    if action == 'candidate_insert_missing_canonical':
        flags.append('source_only_evidence')
    if any(warning.get('reason') == 'volatile_source_evidence_not_canonical_update' for warning in warnings):
        flags.append('volatile_source_evidence')
    if source_identity.get('source_class') == 'volatile':
        flags.append('volatile_source_class')
    freshness_impact = source_freshness.get('freshness_impact')
    if freshness_impact == 'file_snapshot_review':
        flags.append('stale_source_evidence')
    elif freshness_impact == 'undated_source_review':
        flags.append('undated_source_evidence')
    return sorted(flags)


def evidence_quality_for_candidate(
    action: str,
    identifier_quality: str,
    canonical_matches: Sequence[Mapping[str, Any]],
    differences: Sequence[Mapping[str, Any]],
) -> str:
    if action in {'insufficient_evidence', 'ambiguous_match'}:
        return 'weak'
    if identifier_quality == 'stable' and len(canonical_matches) == 1 and not differences:
        return 'strong'
    if identifier_quality in {'stable', 'partial'} and (canonical_matches or differences):
        return 'moderate'
    if identifier_quality == 'stable':
        return 'moderate'
    return 'weak'


def source_freshness_impact(source_identity: Mapping[str, Any]) -> dict[str, Any]:
    freshness_class = source_identity.get('freshness_class')
    source_updated_at = source_identity.get('source_updated_at')
    if freshness_class == 'source_updated_at' and not _missing(source_updated_at):
        freshness_impact = 'timestamped_source'
        review_reason = 'source_timestamp_present'
    elif freshness_class == 'file_snapshot':
        freshness_impact = 'file_snapshot_review'
        review_reason = 'file_snapshot_without_record_timestamp'
    elif _missing(source_updated_at):
        freshness_impact = 'undated_source_review'
        review_reason = 'source_updated_at_missing'
    else:
        freshness_impact = 'source_timestamp_unknown_semantics'
        review_reason = 'source_timestamp_semantics_need_review'
    return {
        'freshness_class': freshness_class,
        'source_updated_at': source_updated_at,
        'freshness_impact': freshness_impact,
        'review_reason': review_reason,
        'wall_clock_age_threshold_applied': False,
    }


def reconciliation_state_for_candidate(
    action: str,
    canonical_matches: Sequence[Mapping[str, Any]],
    risk_flags: Sequence[str],
) -> str:
    if set(risk_flags) & BLOCKING_RISK_FLAGS:
        return 'blocked'
    if action == 'no_change' and len(canonical_matches) == 1:
        return 'confirmed'
    if action in {'candidate_insert_missing_canonical', 'candidate_update'}:
        return 'source_only'
    if action == 'insufficient_evidence':
        return 'unresolved'
    return 'unknown'


def risk_class_for_candidate(risk_flags: Sequence[str]) -> str:
    flags = set(risk_flags)
    if flags & BLOCKING_RISK_FLAGS:
        return 'blocked'
    if flags & VOLATILE_RISK_FLAGS:
        return 'volatile'
    if flags & STALE_RISK_FLAGS:
        return 'stale'
    if flags & RISKY_RISK_FLAGS:
        return 'risky'
    return 'clear'


def review_classifications_for_candidate(
    reconciliation_state: str,
    risk_flags: Sequence[str],
) -> list[str]:
    classifications = {'report_only', reconciliation_state}
    flags = set(risk_flags)
    if flags & BLOCKING_RISK_FLAGS:
        classifications.add('blocked')
        classifications.add('unknown')
    if flags & RISKY_RISK_FLAGS:
        classifications.add('risky')
    if flags & STALE_RISK_FLAGS:
        classifications.add('stale')
    if flags & VOLATILE_RISK_FLAGS:
        classifications.add('volatile')
    if reconciliation_state == 'source_only':
        classifications.add('source_only')
    if reconciliation_state in {'unknown', 'unresolved'}:
        classifications.add('unknown')
    return sorted(classifications)


def confidence_after_freshness(
    confidence: str,
    source_freshness: Mapping[str, Any],
    risk_flags: Sequence[str],
) -> str:
    if source_freshness.get('freshness_impact') == 'timestamped_source':
        return confidence
    if set(risk_flags) & BLOCKING_RISK_FLAGS:
        return 'low'
    if confidence == 'high':
        return 'medium'
    return confidence


def future_canonical_review_marker(
    *,
    action: str,
    risk_class: str,
    risk_flags: Sequence[str],
) -> dict[str, Any]:
    is_candidate = action in {
        'candidate_insert_missing_canonical',
        'candidate_update',
        'station_body_supported_by_staged_body',
    }
    if risk_class == 'blocked':
        marker = 'blocked_from_future_canonical_review'
    elif is_candidate:
        marker = 'future_canonical_review_candidate'
    else:
        marker = 'not_a_future_canonical_review_candidate'
    return {
        'marker': marker,
        'report_only': True,
        'auto_promote_to_canonical': False,
        'canonical_writes_planned': 0,
        'reason': future_review_reason(action, risk_class, risk_flags),
    }


def future_review_reason(action: str, risk_class: str, risk_flags: Sequence[str]) -> str:
    if risk_class == 'blocked':
        return 'blocked risk flags require manual resolution before any future design review'
    if action == 'candidate_update':
        return 'stable staged fields differ from canonical data; review only, no automatic eligibility'
    if action == 'candidate_insert_missing_canonical':
        return 'source evidence has no canonical match; review only, no automatic insert eligibility'
    if action == 'station_body_supported_by_staged_body':
        return 'station/body association is staged-source supported; review only, no automatic link eligibility'
    if risk_flags:
        return 'candidate has review-only risk flags'
    return 'candidate does not propose a future canonical review action'


def association_confidence_metadata(
    *,
    action: str,
    confidence: str,
    source_identity: Mapping[str, Any],
    risk_flags: Sequence[str],
) -> dict[str, Any]:
    source_freshness = source_freshness_impact(source_identity)
    combined_risk_flags = sorted(set(risk_flags) | set(_freshness_risk_flags(source_freshness)))
    risk_class = risk_class_for_candidate(combined_risk_flags)
    reconciliation_state = association_reconciliation_state(action)
    confidence = confidence_after_freshness(confidence, source_freshness, combined_risk_flags)
    return {
        'confidence_model_version': CONFIDENCE_MODEL_SCHEMA_VERSION,
        'confidence': confidence,
        'confidence_level': confidence,
        'confidence_reasons': confidence_reasons(
            action=action,
            identifier_quality='source_body_name',
            evidence_quality='moderate' if action == 'station_body_supported_by_staged_body' else 'weak',
            risk_flags=combined_risk_flags,
            source_freshness=source_freshness,
            differences=[],
        ),
        'confidence_explanations': confidence_explanations(
            action=action,
            identifier_quality='source_body_name',
            evidence_quality='moderate' if action == 'station_body_supported_by_staged_body' else 'weak',
            risk_flags=combined_risk_flags,
            source_freshness=source_freshness,
            reconciliation_state=reconciliation_state,
            risk_class=risk_class,
            differences=[],
        ),
        'evidence_quality': 'moderate' if action == 'station_body_supported_by_staged_body' else 'weak',
        'identifier_quality': 'source_body_name',
        'reconciliation_state': reconciliation_state,
        'risk_class': risk_class,
        'risk_flags': combined_risk_flags,
        'risk_explanations': risk_explanations(combined_risk_flags),
        'review_classifications': review_classifications_for_candidate(
            reconciliation_state,
            combined_risk_flags,
        ),
        'source_freshness': source_freshness,
        'future_canonical_review_candidate': future_canonical_review_marker(
            action=action,
            risk_class=risk_class,
            risk_flags=combined_risk_flags,
        ),
        'report_only': True,
        'canonical_writes_planned': 0,
    }


def association_reconciliation_state(action: str) -> str:
    if action == 'station_body_supported_by_staged_body':
        return 'inferred_verify'
    if action in {'station_body_name_missing', 'station_body_unresolved_staged_body'}:
        return 'unresolved'
    if action == 'station_body_ambiguous_staged_body':
        return 'blocked'
    return 'unknown'


def _freshness_risk_flags(source_freshness: Mapping[str, Any]) -> list[str]:
    freshness_impact = source_freshness.get('freshness_impact')
    if freshness_impact == 'file_snapshot_review':
        return ['stale_source_evidence']
    if freshness_impact == 'undated_source_review':
        return ['undated_source_evidence']
    return []


def confidence_reasons(
    *,
    action: str,
    identifier_quality: str,
    evidence_quality: str,
    risk_flags: Sequence[str],
    source_freshness: Mapping[str, Any],
    differences: Sequence[Mapping[str, Any]],
) -> list[str]:
    reasons = [
        f'action:{action}',
        f'identifier_quality:{identifier_quality}',
        f'evidence_quality:{evidence_quality}',
        f"freshness_impact:{source_freshness.get('freshness_impact')}",
    ]
    if differences:
        reasons.append('stable_field_differences_present')
    reasons.extend(f'risk:{flag}' for flag in risk_flags)
    return sorted(reasons)


def confidence_explanations(
    *,
    action: str,
    identifier_quality: str,
    evidence_quality: str,
    risk_flags: Sequence[str],
    source_freshness: Mapping[str, Any],
    reconciliation_state: str,
    risk_class: str,
    differences: Sequence[Mapping[str, Any]],
) -> list[str]:
    explanations = [
        _action_explanation(action),
        f'Identifier quality is {identifier_quality}.',
        f'Evidence quality is {evidence_quality}.',
        f'Reconciliation state is {reconciliation_state}.',
        f'Risk class is {risk_class}.',
        _freshness_explanation(source_freshness),
        'Output is report-only; it is not a write plan.',
    ]
    if differences:
        explanations.append('Stable staged fields differ from the matched canonical row.')
    explanations.extend(risk_explanations(risk_flags))
    return sorted(dict.fromkeys(explanations))


def risk_explanations(risk_flags: Sequence[str]) -> list[str]:
    mapping = {
        'ambiguous_canonical_match': 'Multiple canonical rows matched; manual review is required.',
        'ambiguous_staged_body_evidence': 'More than one staged body row matched this station body_name.',
        'canonical_difference_review': 'Stable source evidence differs from canonical data; keep it in review.',
        'insufficient_identifiers': 'Source evidence is missing identifiers needed for a canonical conclusion.',
        'missing_staged_body_evidence': 'No staged body row supports this station body_name.',
        'missing_station_body_name': 'Station evidence has no body_name; association remains unknown.',
        'source_only_association': 'Station/body association is supported only by staged source evidence.',
        'source_only_evidence': 'Source evidence has no trusted canonical match and remains source-only.',
        'stale_source_evidence': 'Source evidence came from a file snapshot without a record timestamp; review freshness.',
        'undated_source_evidence': 'Source evidence has no source_updated_at timestamp; freshness remains unknown.',
        'volatile_source_class': 'The source class is volatile and must remain review-only evidence.',
        'volatile_source_evidence': 'Volatile source evidence is retained for review and must not churn canonical rows.',
    }
    return [mapping[flag] for flag in sorted(risk_flags) if flag in mapping]


def _action_explanation(action: str) -> str:
    return {
        'ambiguous_match': 'Staged evidence matched more than one canonical row.',
        'candidate_insert_missing_canonical': 'No canonical row matched this staged evidence.',
        'candidate_update': 'One canonical row matched, but stable staged fields differ.',
        'insufficient_evidence': 'Staged evidence is too sparse to reconcile.',
        'no_change': 'Staged evidence matches one canonical row.',
        'station_body_ambiguous_staged_body': 'Station body_name matched more than one staged body row.',
        'station_body_name_missing': 'Station evidence has no source body_name.',
        'station_body_supported_by_staged_body': 'Station body_name is supported by exactly one staged body row.',
        'station_body_unresolved_staged_body': 'Station body_name has no matching staged body row.',
    }.get(action, f'Reconciliation action is {action}.')


def _freshness_explanation(source_freshness: Mapping[str, Any]) -> str:
    impact = source_freshness.get('freshness_impact')
    if impact == 'timestamped_source':
        return 'Source freshness is timestamped by source_updated_at.'
    if impact == 'file_snapshot_review':
        return 'Source freshness depends on the snapshot file and needs operator review.'
    if impact == 'undated_source_review':
        return 'Source freshness is unknown because source_updated_at is missing.'
    return 'Source freshness semantics need review.'


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False
