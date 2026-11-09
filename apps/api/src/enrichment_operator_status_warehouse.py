from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from enrichment_operator_status_io import _artifact_info
from enrichment_operator_status_sanitize import (
    _bool_or_none,
    _dist_count,
    _entity_candidate_count,
    _first_distribution,
    _first_int,
    _int_or_none,
    _length_or_none,
    _mapping,
    _safe_distribution,
    _safe_schema_version,
    _safe_text,
    _safe_warning_rows,
)


def _compact_summary_report_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    schema_version = _safe_schema_version(payload.get('schema_version'))
    source_schema_version = _safe_schema_version(
        payload.get('artifact_schema_version')
        or payload.get('source_schema_version')
        or payload.get('source_artifact_schema_version')
    )

    summary = _mapping(payload.get('summary'))
    counts = _mapping(payload.get('counts'))
    candidates = _mapping(payload.get('candidates'))
    distributions = _mapping(payload.get('distributions'))
    confidence = _mapping(payload.get('confidence_risk_summary'))
    source_coverage = _mapping(payload.get('source_coverage_summary'))
    warehouse_coverage = _mapping(payload.get('warehouse_coverage_summary'))
    artifact = _mapping(payload.get('artifact'))
    filters = _mapping(payload.get('filters'))

    canonical_writes = _first_int(
        payload.get('canonical_writes_planned'),
        summary.get('canonical_writes_planned'),
        counts.get('canonical_writes_planned'),
    )
    station_candidates = _first_int(
        payload.get('station_candidate_count'),
        summary.get('station_candidate_count'),
        summary.get('station_candidates'),
        counts.get('station_candidate_count'),
        counts.get('station_candidates'),
        candidates.get('station_candidate_count'),
        candidates.get('station_candidates'),
    )
    body_candidates = _first_int(
        payload.get('body_candidate_count'),
        summary.get('body_candidate_count'),
        summary.get('body_candidates'),
        counts.get('body_candidate_count'),
        counts.get('body_candidates'),
        candidates.get('body_candidate_count'),
        candidates.get('body_candidates'),
    )
    ring_candidates = _first_int(
        payload.get('ring_candidate_count'),
        summary.get('ring_candidate_count'),
        summary.get('ring_candidates'),
        counts.get('ring_candidate_count'),
        counts.get('ring_candidates'),
        candidates.get('ring_candidate_count'),
        candidates.get('ring_candidates'),
    )

    candidate_action_counts = _first_distribution(
        payload.get('candidate_action_counts'),
        summary.get('candidate_action_counts'),
        counts.get('candidate_action_counts'),
        distributions.get('candidate_action_counts'),
        payload.get('action_counts'),
        summary.get('action_counts'),
        distributions.get('action_counts'),
    )
    risk_distribution = _first_distribution(
        confidence.get('risk_class_distribution'),
        payload.get('risk_class_distribution'),
        summary.get('risk_class_distribution'),
        distributions.get('risk_class_distribution'),
    )
    confidence_distribution = _first_distribution(
        confidence.get('confidence_distribution'),
        payload.get('confidence_distribution'),
        summary.get('confidence_distribution'),
        distributions.get('confidence_distribution'),
    )
    reconciliation_state_distribution = _first_distribution(
        payload.get('reconciliation_state_counts'),
        summary.get('reconciliation_state_counts'),
        distributions.get('reconciliation_state_counts'),
        payload.get('reconciliation_state_distribution'),
        distributions.get('reconciliation_state_distribution'),
    )

    blocked_count = _dist_count(risk_distribution, 'blocked')
    risky_count = _dist_count(risk_distribution, 'risky')
    stale_count = _dist_count(risk_distribution, 'stale')
    volatile_count = _dist_count(risk_distribution, 'volatile')

    source_entities = _mapping(source_coverage.get('entities'))
    ring_evidence = _mapping(source_coverage.get('ring_evidence'))
    coverage_summary = _mapping(warehouse_coverage.get('summary'))

    warnings = _safe_warning_rows(payload.get('warnings'))
    errors = _safe_warning_rows(payload.get('errors'))

    return {
        'available': True,
        'configured': True,
        'state': _warehouse_state(
            canonical_writes=canonical_writes,
            errors=len(errors) if errors else _int_or_none(summary.get('errors')),
            blocked=blocked_count,
            risky=risky_count,
            stale=stale_count,
            volatile=volatile_count,
            warning_count=len(warnings) if warnings else _int_or_none(summary.get('warnings')),
        ),
        'message': 'Compact warehouse reconciliation summary loaded.',
        'source': 'warehouse_reconciliation_status_json',
        'artifact': None,
        'latest_snapshot_load': {
            'source_run_key': _safe_text(filters.get('source_run_key')),
            'source_file_key': _safe_text(filters.get('source_file_key')),
            'source': _safe_text(filters.get('source') or artifact.get('source_artifact_basename')),
            'source_files_considered': _first_int(
                source_coverage.get('source_files_considered'),
                coverage_summary.get('source_files_considered'),
            ),
            'source_type_distribution': _first_distribution(
                payload.get('source_type_distribution'),
                summary.get('source_type_distribution'),
                distributions.get('source_type_distribution'),
            ),
            'source_format_distribution': _first_distribution(
                payload.get('source_format_distribution'),
                summary.get('source_format_distribution'),
                distributions.get('source_format_distribution'),
            ),
        },
        'latest_reconciliation_run': {
            'schema_version': schema_version,
            'coverage_schema_version': source_schema_version,
            'dry_run': _bool_or_none(payload.get('dry_run')),
            'report_only': _bool_or_none(payload.get('report_only')),
            'canonical_writes_planned': canonical_writes,
            'staged_station_rows_considered': _first_int(
                summary.get('staged_station_rows_considered'),
                counts.get('staged_station_rows_considered'),
                station_candidates,
            ),
            'staged_body_rows_considered': _first_int(
                summary.get('staged_body_rows_considered'),
                counts.get('staged_body_rows_considered'),
                body_candidates,
            ),
            'staged_ring_rows_considered': _first_int(
                summary.get('staged_ring_rows_considered'),
                counts.get('staged_ring_rows_considered'),
                ring_candidates,
            ),
            'canonical_matches_found': _first_int(
                summary.get('canonical_matches_found'),
                counts.get('canonical_matches_found'),
            ),
            'canonical_misses': _first_int(
                summary.get('canonical_misses'),
                counts.get('canonical_misses'),
            ),
            'ambiguous_matches': _first_int(
                summary.get('ambiguous_matches'),
                counts.get('ambiguous_matches'),
            ),
            'insufficient_evidence': _first_int(
                summary.get('insufficient_evidence'),
                counts.get('insufficient_evidence'),
            ),
            'warnings': len(warnings) if warnings else _int_or_none(summary.get('warnings')),
            'errors': len(errors) if errors else _int_or_none(summary.get('errors')),
        },
        'source_coverage': {
            'station_candidates': _first_int(station_candidates, _entity_candidate_count(source_entities, 'station')),
            'body_candidates': _first_int(body_candidates, _entity_candidate_count(source_entities, 'body')),
            'ring_candidates': _first_int(ring_candidates, _entity_candidate_count(source_entities, 'ring')),
            'systems_with_station_evidence': _first_int(
                source_coverage.get('systems_with_station_evidence'),
                coverage_summary.get('systems_with_station_evidence'),
            ),
            'systems_missing_station_evidence': _first_int(
                source_coverage.get('systems_missing_station_evidence'),
                coverage_summary.get('systems_missing_station_evidence'),
            ),
            'trusted_ring_evidence_bodies': _first_int(
                source_coverage.get('trusted_ring_evidence_bodies'),
                coverage_summary.get('trusted_ring_evidence_bodies'),
            ),
            'unknown_ring_evidence_bodies': _first_int(
                source_coverage.get('unknown_ring_evidence_bodies'),
                coverage_summary.get('unknown_ring_evidence_bodies'),
            ),
            'explicit_no_ring_evidence_bodies': _first_int(
                source_coverage.get('explicit_no_ring_evidence_bodies'),
                coverage_summary.get('explicit_no_ring_evidence_bodies'),
            ),
            'staged_ring_candidates': _first_int(
                ring_evidence.get('staged_ring_candidates'),
                ring_candidates,
            ),
            'trusted_local_matched_ring_candidates': _int_or_none(
                ring_evidence.get('trusted_local_matched_ring_candidates')
            ),
        },
        'evidence_health': {
            'unresolved_stations': _first_int(
                summary.get('unresolved_stations'),
                coverage_summary.get('unresolved_stations'),
            ),
            'blocked_conflicts': blocked_count,
            'risky_conflicts': risky_count,
            'stale_records': stale_count,
            'volatile_records': volatile_count,
            'stale_or_undated_source_records': _first_int(
                summary.get('stale_or_undated_source_records'),
                coverage_summary.get('stale_or_undated_source_records'),
            ),
            'malformed_or_skipped_rows': _first_int(
                summary.get('malformed_or_skipped_rows'),
                coverage_summary.get('malformed_or_skipped_rows'),
            ),
            'duplicate_source_records': _first_int(
                summary.get('duplicate_source_records'),
                coverage_summary.get('duplicate_source_records'),
            ),
            'source_identity_conflicts': _first_int(
                summary.get('source_identity_conflicts'),
                coverage_summary.get('source_identity_conflicts'),
            ),
            'high_value_systems_needing_better_evidence': _first_int(
                summary.get('high_value_systems_needing_better_evidence'),
                coverage_summary.get('high_value_systems_needing_better_evidence'),
            ),
        },
        'canonical_safety': {
            'canonical_tables_untouched': canonical_writes == 0 if canonical_writes is not None else None,
            'canonical_writes_planned': canonical_writes,
            'dry_run': _bool_or_none(payload.get('dry_run')),
            'report_only': _bool_or_none(payload.get('report_only')),
        },
        'warnings': warnings[:8],
        'errors': errors[:8],
        'compact_summary_distributions': {
            'candidate_action_counts': _safe_distribution(candidate_action_counts),
            'risk_class_distribution': _safe_distribution(risk_distribution),
            'confidence_distribution': _safe_distribution(confidence_distribution),
            'reconciliation_state_distribution': _safe_distribution(reconciliation_state_distribution),
        },
    }


def sanitize_warehouse_status(
    payload: Mapping[str, Any],
    *,
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    if payload.get('schema_version') == 'enrichment_reconciliation_artifact_summary/v1':
        compact_status = _compact_summary_report_payload(payload)
        compact_status['artifact'] = _artifact_info(artifact_path, exists=True)
        return compact_status

    report = _warehouse_report_payload(payload)
    summary = _mapping(report.get('summary'))
    filters = _mapping(report.get('filters'))
    coverage = _mapping(report.get('warehouse_coverage_report'))
    coverage_summary = _mapping(coverage.get('summary'))
    coverage_operator = _mapping(coverage.get('operator_review'))
    needs_attention = _mapping(coverage_operator.get('needs_attention_buckets'))
    source_freshness = _mapping(coverage.get('source_freshness'))
    stale_or_undated = _mapping(source_freshness.get('stale_or_undated_evidence'))
    source_quality = _mapping(coverage.get('source_quality'))
    source_formats = _mapping(coverage.get('source_formats'))
    confidence = _mapping(report.get('confidence_risk_summary'))
    source_coverage = _mapping(report.get('source_coverage_summary'))
    source_entities = _mapping(source_coverage.get('entities'))
    ring_evidence = _mapping(source_coverage.get('ring_evidence'))
    risk_classes = _mapping(confidence.get('risk_class_distribution'))

    canonical_writes = _first_int(
        summary.get('canonical_writes_planned'),
        coverage.get('canonical_writes_planned'),
        coverage_summary.get('canonical_writes_planned'),
    )
    report_only = _bool_or_none(coverage.get('report_only'))
    dry_run = _bool_or_none(report.get('dry_run'))
    errors_count = _first_int(summary.get('errors'), _length_or_none(report.get('errors')))
    blocked_count = _dist_count(risk_classes, 'blocked')
    risky_count = _dist_count(risk_classes, 'risky')
    stale_count = _dist_count(risk_classes, 'stale')
    volatile_count = _dist_count(risk_classes, 'volatile')
    warning_count = _first_int(summary.get('warnings'), _length_or_none(report.get('warnings')))
    state = _warehouse_state(
        canonical_writes=canonical_writes,
        errors=errors_count,
        blocked=blocked_count,
        risky=risky_count,
        stale=stale_count,
        volatile=volatile_count,
        warning_count=warning_count,
    )

    return {
        'available': True,
        'configured': True,
        'state': state,
        'message': _warehouse_message_for_state(state),
        'source': 'warehouse_reconciliation_status_json',
        'artifact': _artifact_info(artifact_path, exists=True),
        'latest_snapshot_load': {
            'source_run_key': _safe_text(filters.get('source_run_key')),
            'source_file_key': _safe_text(filters.get('source_file_key')),
            'source': _safe_text(filters.get('source')),
            'source_files_considered': _int_or_none(coverage_summary.get('source_files_considered')),
            'source_type_distribution': _safe_distribution(source_formats.get('source_type_distribution')),
            'source_format_distribution': _safe_distribution(source_formats.get('source_format_distribution')),
        },
        'latest_reconciliation_run': {
            'schema_version': _safe_schema_version(report.get('schema_version')),
            'coverage_schema_version': _safe_schema_version(coverage.get('schema_version')),
            'dry_run': dry_run,
            'report_only': report_only,
            'canonical_writes_planned': canonical_writes,
            'staged_station_rows_considered': _int_or_none(summary.get('staged_station_rows_considered')),
            'staged_body_rows_considered': _int_or_none(summary.get('staged_body_rows_considered')),
            'staged_ring_rows_considered': _int_or_none(summary.get('staged_ring_rows_considered')),
            'canonical_matches_found': _int_or_none(summary.get('canonical_matches_found')),
            'canonical_misses': _int_or_none(summary.get('canonical_misses')),
            'ambiguous_matches': _int_or_none(summary.get('ambiguous_matches')),
            'insufficient_evidence': _int_or_none(summary.get('insufficient_evidence')),
            'warnings': warning_count,
            'errors': errors_count,
        },
        'source_coverage': {
            'station_candidates': _entity_candidate_count(source_entities, 'station'),
            'body_candidates': _entity_candidate_count(source_entities, 'body'),
            'ring_candidates': _entity_candidate_count(source_entities, 'ring'),
            'systems_with_station_evidence': _int_or_none(coverage_summary.get('systems_with_station_evidence')),
            'systems_missing_station_evidence': _int_or_none(coverage_summary.get('systems_missing_station_evidence')),
            'trusted_ring_evidence_bodies': _int_or_none(coverage_summary.get('trusted_ring_evidence_bodies')),
            'unknown_ring_evidence_bodies': _int_or_none(coverage_summary.get('unknown_ring_evidence_bodies')),
            'explicit_no_ring_evidence_bodies': _int_or_none(coverage_summary.get('explicit_no_ring_evidence_bodies')),
            'staged_ring_candidates': _int_or_none(ring_evidence.get('staged_ring_candidates')),
            'trusted_local_matched_ring_candidates': _int_or_none(
                ring_evidence.get('trusted_local_matched_ring_candidates')
            ),
        },
        'evidence_health': {
            'unresolved_stations': _int_or_none(coverage_summary.get('unresolved_stations')),
            'blocked_conflicts': blocked_count,
            'risky_conflicts': risky_count,
            'stale_records': stale_count,
            'volatile_records': volatile_count,
            'stale_or_undated_source_records': _first_int(
                stale_or_undated.get('records_without_source_updated_at'),
                needs_attention.get('stale_or_undated_sources'),
            ),
            'malformed_or_skipped_rows': _first_int(
                coverage_summary.get('malformed_or_skipped_source_rows'),
                needs_attention.get('skipped_or_malformed_raw_records'),
                _mapping(source_quality.get('malformed_or_skipped_source_rows')).get('count'),
            ),
            'duplicate_source_records': _first_int(
                needs_attention.get('duplicate_source_records'),
                _mapping(source_quality.get('duplicate_source_records')).get('duplicate_records'),
            ),
            'source_identity_conflicts': _first_int(
                coverage_summary.get('source_identity_conflicts'),
                needs_attention.get('source_identity_conflicts'),
                _mapping(source_quality.get('source_identity_conflicts')).get('count'),
            ),
            'high_value_systems_needing_better_evidence': _first_int(
                coverage_summary.get('high_value_systems_needing_better_evidence'),
                needs_attention.get('high_value_systems_needing_better_evidence'),
            ),
        },
        'canonical_safety': {
            'canonical_tables_untouched': canonical_writes == 0 if canonical_writes is not None else None,
            'canonical_writes_planned': canonical_writes,
            'dry_run': dry_run,
            'report_only': report_only,
        },
        'warnings': _safe_warning_rows(report.get('warnings')),
        'errors': _safe_warning_rows(report.get('errors')),
    }


def _warehouse_state(
    *,
    canonical_writes: int | None,
    errors: int | None,
    blocked: int | None,
    risky: int | None,
    stale: int | None,
    volatile: int | None,
    warning_count: int | None,
) -> str:
    if canonical_writes is not None and canonical_writes != 0:
        return 'unsafe'
    if errors:
        return 'error'
    if blocked:
        return 'blocked'
    if risky or stale or volatile or warning_count:
        return 'warning'
    return 'available'


def _warehouse_message_for_state(state: str) -> str:
    return {
        'available': 'Warehouse status artifact loaded.',
        'blocked': 'Warehouse status has blocked reconciliation evidence for review.',
        'error': 'Warehouse status artifact reports errors.',
        'missing': 'Warehouse status artifact is unavailable.',
        'unsafe': 'Warehouse status reports planned canonical writes.',
        'warning': 'Warehouse status loaded with review warnings.',
    }.get(state, 'Warehouse status artifact loaded.')


def _warehouse_report_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    embedded = payload.get('reconciliation_report')
    if isinstance(embedded, Mapping):
        return embedded
    return payload
