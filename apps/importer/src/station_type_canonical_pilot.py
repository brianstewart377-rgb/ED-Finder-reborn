"""Stage 18J station-type-only canonical pilot dry-run planner.

This module consumes a read-only enrichment reconciliation report and produces a
stricter, station-type-only canonical pilot artifact. Its default mode is
always dry-run. The guarded apply helpers are deliberately separate, require a
checksumed artifact plus explicit approval parameters, and update only
``stations.station_type`` after re-validating canonical pre-images.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from api_source_resolver import add_api_source_to_path


API_SRC = add_api_source_to_path(__file__, required_paths=('station_body_resolver.py',))

from station_body_resolver import (  # noqa: E402
    is_permanent_colony_slot_station_type,
    is_transient_non_slot_station_type,
    normalise_station_type_label,
)


DRY_RUN_SCHEMA_VERSION = 'station_type_canonical_pilot_dry_run/v1'
APPLY_SCHEMA_VERSION = 'station_type_canonical_pilot_apply/v1'
VERIFICATION_SCHEMA_VERSION = 'station_type_canonical_pilot_verification/v1'
ROLLBACK_PREIMAGE_SCHEMA_VERSION = 'station_type_canonical_pilot_rollback_preimage/v1'
TOOL_NAME = 'station_type_canonical_pilot'
TOOL_VERSION = 'v1'
APPROVED_TABLE = 'stations'
APPROVED_FIELD = 'station_type'
DEFAULT_LIMIT = 5
MAX_FIRST_PILOT_LIMIT = 20
DEFAULT_BLOCKED_CANDIDATE_SAMPLE_LIMIT = 100
ELIGIBLE_OLD_TYPE_LABELS = {None, '', 'Unknown'}
VOLATILE_RISK_FLAGS = {'volatile_source_class', 'volatile_source_evidence'}
SOURCE_ONLY_RISK_FLAGS = {'source_only_evidence'}
AMBIGUOUS_RISK_FLAGS = {'ambiguous_canonical_match'}
STATION_BODY_LINK_ONLY_RISK_FLAGS = {'missing_station_body_name'}
REPORT_ONLY_STATION_TYPE_REVIEW_FLAGS = {'canonical_difference_review'}
BLOCKING_RISK_FLAGS = {
    'ambiguous_canonical_match',
    'ambiguous_staged_body_evidence',
    'insufficient_identifiers',
    'missing_staged_body_evidence',
    'missing_station_body_name',
    'source_only_association',
    'source_only_evidence',
    'stale_source_evidence',
    'undated_source_evidence',
    'volatile_source_class',
    'volatile_source_evidence',
}
STATION_TYPE_DRY_RUN_ALLOWED_RISK_FLAGS = (
    STATION_BODY_LINK_ONLY_RISK_FLAGS
    | REPORT_ONLY_STATION_TYPE_REVIEW_FLAGS
)
STATION_TYPE_DRY_RUN_REJECTION_REASONS = (
    'rejected_ambiguous_identity',
    'rejected_source_only_insert',
    'rejected_missing_external_identity',
    'rejected_volatile_evidence',
    'rejected_transient_non_slot',
    'rejected_non_station_type_change',
    'rejected_missing_station_type_delta',
    'rejected_ineligible_canonical_old_value',
    'rejected_missing_provenance',
    'rejected_freshness',
    'rejected_by_max_row_bound',
)


class Stage18JPlanError(ValueError):
    """Raised when a Stage 18J dry-run artifact cannot be constructed safely."""


def canonical_json(value: Any) -> str:
    """Return stable JSON for hashing, diffs, and fixture comparisons."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def artifact_sha256(value: Mapping[str, Any]) -> str:
    """Hash an artifact after removing its self-referential integrity block."""
    payload = deepcopy(dict(value))
    payload.pop('artifact_integrity', None)
    return hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()


def build_station_type_pilot_dry_run(
    reconciliation_report: Mapping[str, Any],
    *,
    limit: int | None = DEFAULT_LIMIT,
    allow_edsm_station_id: bool = True,
    allow_undated_source_exception: bool = False,
    reconciliation_artifact_sha256: str | None = None,
    reconciliation_artifact_basename: str | None = None,
    reconciliation_artifact_size_bytes: int | None = None,
    blocked_candidate_sample_limit: int | None = DEFAULT_BLOCKED_CANDIDATE_SAMPLE_LIMIT,
    generated_at: str | None = None,
    git_commit: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic dry-run artifact for exact station type promotion."""
    if limit is None:
        raise Stage18JPlanError('explicit max-row bound is required')
    if limit < 0:
        raise Stage18JPlanError('limit must be >= 0')
    if limit > MAX_FIRST_PILOT_LIMIT:
        raise Stage18JPlanError(f'limit must be <= {MAX_FIRST_PILOT_LIMIT} for the first Stage 18J pilot')
    blocked_sample_limit = _validate_blocked_candidate_sample_limit(blocked_candidate_sample_limit)

    source_scope = _source_scope(
        reconciliation_report,
        reconciliation_artifact_sha256=reconciliation_artifact_sha256,
        reconciliation_artifact_basename=reconciliation_artifact_basename,
        reconciliation_artifact_size_bytes=reconciliation_artifact_size_bytes,
    )
    eligible: list[dict[str, Any]] = []
    blocked_samples: list[dict[str, Any]] = []
    blocked_count = 0
    rejection_reason_counts: dict[str, int] = {}
    identity_coverage_summary = _new_identity_coverage_summary()
    rows_considered = 0

    for candidate in reconciliation_report.get('station_candidates', []) or []:
        if not isinstance(candidate, Mapping):
            continue
        rows_considered += 1
        _record_identity_coverage(identity_coverage_summary, candidate, allow_edsm_station_id=allow_edsm_station_id)
        decision = evaluate_station_type_candidate(
            candidate,
            allow_edsm_station_id=allow_edsm_station_id,
            allow_undated_source_exception=allow_undated_source_exception,
        )
        if decision['eligible'] and len(eligible) < limit:
            eligible.append(decision['candidate'])
        elif decision['eligible']:
            blocked_count = _record_blocked_candidate(
                {
                    'candidate_id': decision['candidate']['candidate_id'],
                    'source_identity': decision['candidate']['source_identity'],
                    'canonical': decision['candidate']['canonical'],
                    'rejection_reasons': ['rejected_by_max_row_bound'],
                    'blocking_reasons': ['rejected_by_max_row_bound'],
                    'eligible_if_limit_allows': True,
                },
                blocked_samples=blocked_samples,
                blocked_count=blocked_count,
                rejection_reason_counts=rejection_reason_counts,
                sample_limit=blocked_sample_limit,
            )
        else:
            blocked_count = _record_blocked_candidate(
                decision['blocked_candidate'],
                blocked_samples=blocked_samples,
                blocked_count=blocked_count,
                rejection_reason_counts=rejection_reason_counts,
                sample_limit=blocked_sample_limit,
            )

    eligible = sorted(eligible, key=canonical_json)
    blocked = sorted(blocked_samples, key=canonical_json)
    rejection_reason_counts = dict(sorted(rejection_reason_counts.items()))
    rejection_summary = _rejection_summary(rejection_reason_counts)
    now = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    artifact: dict[str, Any] = {
        'schema_version': DRY_RUN_SCHEMA_VERSION,
        'generated_at': now,
        'tool': {
            'name': TOOL_NAME,
            'version': TOOL_VERSION,
            'git_commit': git_commit,
        },
        'dry_run': True,
        'pilot_scope': {
            'canonical_table': APPROVED_TABLE,
            'canonical_field': APPROVED_FIELD,
            'default_limit': DEFAULT_LIMIT,
            'max_first_pilot_limit': MAX_FIRST_PILOT_LIMIT,
            'apply_requires_separate_guarded_mode': True,
            'dry_run_only': True,
            'canonical_writes_planned': 0,
        },
        'source_scope': source_scope,
        'filters': {
            'limit': limit,
            'max_row_bound': limit,
            'blocked_candidate_sample_limit': blocked_sample_limit,
            'allow_edsm_station_id': allow_edsm_station_id,
            'allow_undated_source_exception': allow_undated_source_exception,
            'requires_external_identity_proof': True,
            'requires_update_only_candidate_action': True,
            'requires_station_type_only_delta': True,
            'excludes_station_body_association_writes': True,
            'excludes_volatile_evidence': True,
            'excludes_transient_non_slot_station_types': True,
        },
        'summary': {
            **rejection_summary,
            'total_candidates_seen': rows_considered,
            'station_candidates_considered': rows_considered,
            'eligible_station_type_updates': len(eligible),
            'eligible_candidates': len(eligible),
            'blocked_candidates': blocked_count,
            'blocked_candidate_samples_included': len(blocked),
            'blocked_candidate_sample_limit': blocked_sample_limit,
            'blocked_candidate_samples_omitted': max(blocked_count - len(blocked), 0),
            'rejection_reason_counts': rejection_reason_counts,
            'blocked_by_reason': rejection_reason_counts,
            'canonical_writes_planned': 0,
            'dry_run_only': True,
            'apply_run': False,
            'approval_record_created': False,
            'approved_table': APPROVED_TABLE,
            'approved_field': APPROVED_FIELD,
            'errors': 0,
            'warnings': 0,
        },
        'eligible_candidates': eligible,
        'identity_coverage_summary': identity_coverage_summary,
        'blocked_candidate_output': {
            'sampled': True,
            'sample_limit': blocked_sample_limit,
            'samples_included': len(blocked),
            'total_blocked_candidates': blocked_count,
            'samples_omitted': max(blocked_count - len(blocked), 0),
            'sampling': 'first_n_by_input_order_sorted_for_output',
        },
        'blocked_candidates': blocked,
        'operator_review': {
            'manual_approval_required': True,
            'apply_requires_separate_guarded_tool': True,
            'ordinary_warehouse_loaders_must_remain_canonical_read_only': True,
            'production_artifact_approved': False,
            'approval_record_created': False,
            'review_guidance': [
                'Approve only a checksumed dry-run artifact with a small row count.',
                'Apply must be table-specific, field-specific, source-run-specific, and fail closed.',
                'Source-only inserts, ambiguous identity, volatile evidence, transient station types, and non-station-type deltas remain blocked.',
                'Missing station_body_name remains a blocker for station/body-link work, not for externally proven station-type comparison.',
            ],
        },
    }
    artifact['artifact_integrity'] = {
        'hash_algorithm': 'sha256',
        'canonical_json_sha256': artifact_sha256(artifact),
        'canonicalization': 'json.dumps(sort_keys=True,separators=(comma,colon),ensure_ascii=True,allow_nan=False) excluding artifact_integrity',
    }
    return artifact


def evaluate_station_type_candidate(
    candidate: Mapping[str, Any],
    *,
    allow_edsm_station_id: bool = True,
    allow_undated_source_exception: bool = False,
) -> dict[str, Any]:
    """Evaluate one reconciliation station candidate against Stage 18J rules."""
    source = _mapping(candidate.get('source'))
    canonical = _mapping(candidate.get('canonical'))
    canonical_matches = _sequence_of_mappings(candidate.get('canonical_matches'))
    differences = _sequence_of_mappings(candidate.get('differences'))
    warnings = _sequence_of_mappings(candidate.get('warnings'))
    source_station_type = _station_type_difference_value(differences)
    old_value = canonical.get('station_type')
    new_value = normalise_station_type_label(source_station_type)
    source_system_id64 = _read_int(source.get('system_id64'))
    canonical_system_id64 = _read_int(canonical.get('system_id64'))
    canonical_station_id = _read_int(canonical.get('station_id'))
    source_market_id = _read_int(source.get('market_id'))
    source_edsm_station_id = _read_int(source.get('edsm_station_id'))
    canonical_market_id = _read_int(canonical.get('market_id'))
    canonical_edsm_station_id = _read_int(canonical.get('edsm_station_id'))
    source_name = source.get('station_name')
    canonical_name = canonical.get('station_name')
    candidate_action = candidate.get('candidate_action')
    risk_flags = set(candidate.get('risk_flags') or [])
    review_classifications = set(candidate.get('review_classifications') or [])

    identifier_match_type = _station_external_identifier_match_type(
        source_market_id=source_market_id,
        canonical_market_id=canonical_market_id,
        source_edsm_station_id=source_edsm_station_id,
        canonical_edsm_station_id=canonical_edsm_station_id,
        allow_edsm_station_id=allow_edsm_station_id,
    )

    checks = {
        'entity_is_station': candidate.get('entity') == 'station',
        'target_difference_is_station_type_only': _has_only_station_type_difference(differences),
        'has_no_non_station_type_difference': not _has_non_station_type_difference(differences),
        'candidate_action_is_update': candidate_action == 'candidate_update',
        'not_source_only_insert': candidate_action != 'candidate_insert_missing_canonical',
        'not_ambiguous_identity': candidate_action != 'ambiguous_match' and len(canonical_matches) == 1 and bool(canonical),
        'canonical_station_exists': canonical_station_id is not None,
        'source_system_id64_present': source_system_id64 is not None,
        'system_id64_matches': source_system_id64 is not None and source_system_id64 == canonical_system_id64,
        'external_station_identifier_matches': identifier_match_type is not None,
        'internal_primary_key_not_identity_proof': True,
        'station_name_matches': _normalise_name(source_name) is not None and _normalise_name(source_name) == _normalise_name(canonical_name),
        'source_station_type_present': source_station_type not in (None, ''),
        'source_station_type_normalised': new_value is not None,
        'station_type_delta_present': _station_type_delta_present(old_value, new_value),
        'source_station_type_permanent': bool(new_value) and is_permanent_colony_slot_station_type(new_value),
        'source_station_type_not_transient': bool(new_value) and not is_transient_non_slot_station_type(new_value),
        'canonical_old_value_eligible': _canonical_old_value_eligible(old_value),
        'no_volatile_evidence': not _has_volatile_evidence(candidate, source=source, warnings=warnings),
        'no_unhandled_blocking_risk_flags': not _unhandled_station_type_blocking_risk_flags(risk_flags),
        'source_record_hash_present': bool(source.get('source_record_hash')),
        'source_run_key_present': bool(source.get('source_run_key')),
        'source_file_key_present': bool(source.get('source_file_key')),
        'freshness_allowed': _freshness_allowed(candidate, allow_undated_source_exception=allow_undated_source_exception),
        'no_station_body_association_write': True,
        'canonical_writes_planned_zero': _mapping(candidate).get('canonical_writes_planned', 0) in (0, None),
    }

    rejection_reasons = _station_type_rejection_reasons(
        candidate=candidate,
        checks=checks,
        risk_flags=risk_flags,
        review_classifications=review_classifications,
    )
    candidate_id = _candidate_id(source=source, canonical_station_id=canonical_station_id, new_value=new_value)
    common = {
        'candidate_id': candidate_id,
        'source_identity': {
            'source_run_key': source.get('source_run_key'),
            'source_file_key': source.get('source_file_key'),
            'source_record_key': source.get('source_record_key'),
            'source_record_hash': source.get('source_record_hash'),
            'system_id64': source.get('system_id64'),
            'system_name': source.get('system_name'),
            'market_id': source.get('market_id'),
            'edsm_station_id': source.get('edsm_station_id'),
            'station_name': source_name,
            'station_type': source_station_type,
            'normalised_station_type': new_value,
            'source_class': source.get('source_class'),
            'confidence': source.get('confidence'),
            'freshness_class': source.get('freshness_class'),
            'source_updated_at': source.get('source_updated_at'),
        },
        'canonical': {
            'station_id': canonical_station_id,
            'system_id64': canonical_system_id64,
            'market_id': canonical_market_id,
            'edsm_station_id': canonical_edsm_station_id,
            'station_name': canonical_name,
            'station_type': old_value,
        },
        'match_proof': {
            'canonical_match_count': len(canonical_matches),
            'identifier_match_type': identifier_match_type,
            'source_market_id': source_market_id,
            'canonical_market_id': canonical_market_id,
            'source_edsm_station_id': source_edsm_station_id,
            'canonical_edsm_station_id': canonical_edsm_station_id,
            'source_system_id64': source_system_id64,
            'canonical_system_id64': canonical_system_id64,
            'normalised_source_station_name': _normalise_name(source_name),
            'normalised_canonical_station_name': _normalise_name(canonical_name),
            'external_identity_proof_required': True,
            'internal_primary_key_is_not_identity_proof': True,
            'allow_edsm_station_id': allow_edsm_station_id,
        },
        'eligibility_checks': checks,
        'source_reconciliation': {
            'candidate_action': candidate.get('candidate_action'),
            'confidence': candidate.get('confidence'),
            'risk_class': candidate.get('risk_class'),
            'risk_flags': sorted(candidate.get('risk_flags') or []),
            'review_classifications': sorted(candidate.get('review_classifications') or []),
            'reconciliation_state': candidate.get('reconciliation_state'),
            'source_freshness': candidate.get('source_freshness'),
            'report_only': candidate.get('report_only'),
            'canonical_writes_planned': candidate.get('canonical_writes_planned'),
        },
    }

    if rejection_reasons:
        return {
            'eligible': False,
            'blocked_candidate': {
                **common,
                'rejection_reasons': rejection_reasons,
                'blocking_reasons': rejection_reasons,
            },
        }

    eligible_candidate = {
        **common,
        'canonical_table': APPROVED_TABLE,
        'canonical_pk': canonical_station_id,
        'canonical_system_id64': canonical_system_id64,
        'canonical_station_name': canonical_name,
        'field': APPROVED_FIELD,
        'old_value': old_value,
        'new_value': new_value,
        'rollback_pre_image': {
            'canonical_table': APPROVED_TABLE,
            'canonical_pk': canonical_station_id,
            'field': APPROVED_FIELD,
            'pre_image_value': old_value,
            'planned_value': new_value,
        },
        'audit_metadata': {
            'reason_codes': ['exact_station_identity_station_type_promotion'],
            'requires_manual_approval': True,
            'source_record_hash': source.get('source_record_hash'),
        },
    }
    return {'eligible': True, 'candidate': eligible_candidate}


def validate_apply_request(
    artifact: Mapping[str, Any],
    *,
    artifact_sha256_expected: str,
    expected_candidate_count: int,
    approved_table: str,
    approved_field: str,
    approved_source_run: str,
    approved_source_file: str | None = None,
    approval_id: str | None = None,
    confirmation: bool = False,
    max_rows: int | None = None,
) -> list[dict[str, Any]]:
    """Validate operator approval parameters before any canonical apply."""
    if not confirmation:
        raise Stage18JPlanError('explicit Stage 18J confirmation flag is required')
    if not approval_id:
        raise Stage18JPlanError('approval id/text is required')
    if artifact.get('schema_version') != DRY_RUN_SCHEMA_VERSION:
        raise Stage18JPlanError('unsupported dry-run artifact schema')
    recomputed_sha = artifact_sha256(artifact)
    if recomputed_sha != artifact_sha256_expected:
        raise Stage18JPlanError('dry-run artifact checksum mismatch')
    embedded_sha = _mapping(artifact.get('artifact_integrity')).get('canonical_json_sha256')
    if embedded_sha and embedded_sha != recomputed_sha:
        raise Stage18JPlanError('dry-run artifact embedded checksum is stale')
    if approved_table != APPROVED_TABLE:
        raise Stage18JPlanError('approved table must be stations')
    if approved_field != APPROVED_FIELD:
        raise Stage18JPlanError('approved field must be station_type')

    candidates = _sequence_of_mappings(artifact.get('eligible_candidates'))
    if len(candidates) != expected_candidate_count:
        raise Stage18JPlanError('expected candidate count does not match artifact')
    if expected_candidate_count < 0:
        raise Stage18JPlanError('expected candidate count must be >= 0')
    if max_rows is None:
        raise Stage18JPlanError('explicit max rows is required')
    if max_rows < 0:
        raise Stage18JPlanError('max rows must be >= 0')
    if max_rows > MAX_FIRST_PILOT_LIMIT:
        raise Stage18JPlanError(f'max rows must be <= {MAX_FIRST_PILOT_LIMIT} for the first Stage 18J pilot')
    if len(candidates) > max_rows:
        raise Stage18JPlanError('artifact candidate count exceeds approved max rows')

    source_scope = _mapping(artifact.get('source_scope'))
    if source_scope.get('source_run_key') != approved_source_run:
        raise Stage18JPlanError('approved source run does not match artifact')
    if approved_source_file is not None and source_scope.get('source_file_key') != approved_source_file:
        raise Stage18JPlanError('approved source file does not match artifact')

    for row in candidates:
        if row.get('canonical_table') != APPROVED_TABLE or row.get('field') != APPROVED_FIELD:
            raise Stage18JPlanError('candidate targets an unapproved table or field')
        if _mapping(row.get('source_identity')).get('source_run_key') != approved_source_run:
            raise Stage18JPlanError('candidate source run does not match approval')
        if approved_source_file is not None and _mapping(row.get('source_identity')).get('source_file_key') != approved_source_file:
            raise Stage18JPlanError('candidate source file does not match approval')
    return candidates


def apply_station_type_pilot(
    conn: Any,
    artifact: Mapping[str, Any],
    *,
    artifact_sha256_expected: str,
    expected_candidate_count: int,
    approved_table: str,
    approved_field: str,
    approved_source_run: str,
    approved_source_file: str | None = None,
    approval_id: str | None = None,
    confirmation: bool = False,
    max_rows: int | None = None,
    apply_run_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Apply an approved station-type pilot artifact through a DB connection."""
    candidates = validate_apply_request(
        artifact,
        artifact_sha256_expected=artifact_sha256_expected,
        expected_candidate_count=expected_candidate_count,
        approved_table=approved_table,
        approved_field=approved_field,
        approved_source_run=approved_source_run,
        approved_source_file=approved_source_file,
        approval_id=approval_id,
        confirmation=confirmation,
        max_rows=max_rows,
    )
    run_id = apply_run_id or _apply_run_id(artifact_sha256_expected, approval_id, len(candidates))
    now = generated_at or _utc_now()
    row_results: list[dict[str, Any]] = []
    rollback_rows: list[dict[str, Any]] = []
    cur = conn.cursor()
    try:
        for candidate in candidates:
            station_id = _read_int(candidate.get('canonical_pk'))
            system_id64 = _read_int(candidate.get('canonical_system_id64'))
            old_value = candidate.get('old_value')
            new_value = candidate.get('new_value')
            cur.execute(
                """
                SELECT id, system_id64, name, station_type::text AS station_type
                FROM stations
                WHERE id = %s AND system_id64 = %s
                FOR UPDATE
                """,
                (station_id, system_id64),
            )
            current = _fetchone_mapping(cur)
            if not current:
                raise Stage18JPlanError(f'canonical station missing before apply: {station_id}')
            if _normalise_name(current.get('name')) != _normalise_name(candidate.get('canonical_station_name')):
                raise Stage18JPlanError(f'canonical station identity pre-image mismatch for station {station_id}')
            if _normalise_station_type_value(current.get('station_type')) != _normalise_station_type_value(old_value):
                raise Stage18JPlanError(f'canonical pre-image mismatch for station {station_id}')
            cur.execute(
                """
                UPDATE stations
                SET station_type = %s::station_type
                WHERE id = %s
                  AND system_id64 = %s
                  AND station_type::text IS NOT DISTINCT FROM %s
                RETURNING id, system_id64, name, station_type::text AS station_type
                """,
                (new_value, station_id, system_id64, old_value),
            )
            updated = _fetchone_mapping(cur)
            if not updated:
                raise Stage18JPlanError(f'canonical station update did not affect the approved row: {station_id}')
            row_results.append({
                'candidate_id': candidate.get('candidate_id'),
                'canonical_table': APPROVED_TABLE,
                'canonical_pk': station_id,
                'field': APPROVED_FIELD,
                'old_value': old_value,
                'new_value': new_value,
                'result': 'applied',
            })
            rollback_rows.append({
                'candidate_id': candidate.get('candidate_id'),
                'canonical_table': APPROVED_TABLE,
                'canonical_pk': station_id,
                'canonical_system_id64': system_id64,
                'field': APPROVED_FIELD,
                'pre_image_value': old_value,
                'applied_value': new_value,
                'source_trace': _mapping(candidate.get('source_identity')),
            })
        verification = verify_station_type_apply(conn, candidates)
        if not verification['summary']['ok']:
            raise Stage18JPlanError('post-apply verification failed')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        close = getattr(cur, 'close', None)
        if callable(close):
            close()

    audit = {
        'schema_version': APPLY_SCHEMA_VERSION,
        'apply_run_id': run_id,
        'generated_at': now,
        'tool': {'name': TOOL_NAME, 'version': TOOL_VERSION},
        'dry_run_artifact_sha256': artifact_sha256_expected,
        'approval': {
            'approval_id': approval_id,
            'approved_table': approved_table,
            'approved_field': approved_field,
            'approved_source_run': approved_source_run,
            'approved_source_file': approved_source_file,
            'expected_candidate_count': expected_candidate_count,
            'max_rows': max_rows,
        },
        'summary': {
            'planned': len(candidates),
            'applied': len(row_results),
            'skipped': 0,
            'blocked': 0,
            'canonical_table': APPROVED_TABLE,
            'canonical_field': APPROVED_FIELD,
        },
        'rows': row_results,
        'rollback_preimage': build_rollback_preimage(
            apply_run_id=run_id,
            dry_run_artifact_sha256=artifact_sha256_expected,
            rows=rollback_rows,
            generated_at=now,
        ),
        'post_apply_verification': verification,
    }
    audit['artifact_integrity'] = {
        'hash_algorithm': 'sha256',
        'canonical_json_sha256': artifact_sha256(audit),
    }
    return audit


def verify_station_type_apply(conn: Any, candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Verify approved rows now have the approved station_type values."""
    rows: list[dict[str, Any]] = []
    cur = conn.cursor()
    try:
        for candidate in candidates:
            station_id = _read_int(candidate.get('canonical_pk'))
            system_id64 = _read_int(candidate.get('canonical_system_id64'))
            expected = candidate.get('new_value')
            cur.execute(
                """
                SELECT id, system_id64, name, station_type::text AS station_type
                FROM stations
                WHERE id = %s AND system_id64 = %s
                """,
                (station_id, system_id64),
            )
            current = _fetchone_mapping(cur)
            actual = current.get('station_type') if current else None
            rows.append({
                'candidate_id': candidate.get('candidate_id'),
                'canonical_pk': station_id,
                'canonical_system_id64': system_id64,
                'field': APPROVED_FIELD,
                'expected_value': expected,
                'actual_value': actual,
                'ok': _normalise_station_type_value(actual) == _normalise_station_type_value(expected),
            })
    finally:
        close = getattr(cur, 'close', None)
        if callable(close):
            close()
    artifact = {
        'schema_version': VERIFICATION_SCHEMA_VERSION,
        'generated_at': _utc_now(),
        'summary': {
            'checked': len(rows),
            'ok': all(row['ok'] for row in rows),
            'canonical_table': APPROVED_TABLE,
            'canonical_field': APPROVED_FIELD,
        },
        'rows': rows,
    }
    artifact['artifact_integrity'] = {
        'hash_algorithm': 'sha256',
        'canonical_json_sha256': artifact_sha256(artifact),
    }
    return artifact


def build_rollback_preimage(
    *,
    apply_run_id: str,
    dry_run_artifact_sha256: str,
    rows: Sequence[Mapping[str, Any]],
    generated_at: str | None = None,
) -> dict[str, Any]:
    artifact = {
        'schema_version': ROLLBACK_PREIMAGE_SCHEMA_VERSION,
        'apply_run_id': apply_run_id,
        'generated_at': generated_at or _utc_now(),
        'dry_run_artifact_sha256': dry_run_artifact_sha256,
        'summary': {
            'rows': len(rows),
            'canonical_table': APPROVED_TABLE,
            'canonical_field': APPROVED_FIELD,
        },
        'rows': sorted([dict(row) for row in rows], key=canonical_json),
    }
    artifact['artifact_integrity'] = {
        'hash_algorithm': 'sha256',
        'canonical_json_sha256': artifact_sha256(artifact),
    }
    return artifact


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Stage 18J station-type canonical pilot tooling.')
    parser.add_argument('--reconciliation-report', default=None, help='Path to read-only warehouse reconciliation JSON for dry-run mode.')
    parser.add_argument('--output', default=None, help='Optional output path. Defaults to stdout only.')
    parser.add_argument('--limit', type=int, default=DEFAULT_LIMIT, help='Explicit max-row bound for eligible dry-run candidates.')
    parser.add_argument('--blocked-candidate-sample-limit', type=int, default=DEFAULT_BLOCKED_CANDIDATE_SAMPLE_LIMIT, help='Maximum blocked candidate samples to include in dry-run output.')
    parser.add_argument('--allow-edsm-station-id', action='store_true', help='Compatibility flag; edsm_station_id is allowed as external identity by the strict filter.')
    parser.add_argument('--allow-undated-source-exception', action='store_true', help='Allow otherwise eligible undated/file-snapshot rows in dry-run review.')
    parser.add_argument('--json', action='store_true', help='Accepted for compatibility; output is always JSON.')
    parser.add_argument('--quiet', action='store_true', help='When --output is set, do not also print the JSON artifact to stdout.')
    parser.add_argument('--dry-run', action='store_true', default=True, help='Dry-run mode. This is the default.')
    parser.add_argument('--apply', action='store_true', help='Run guarded apply mode from a checksumed dry-run artifact.')
    parser.add_argument('--artifact', default=None, help='Dry-run artifact path for apply mode.')
    parser.add_argument('--artifact-sha256', default=None, help='Expected dry-run artifact SHA-256 for apply mode.')
    parser.add_argument('--expected-candidate-count', type=int, default=None, help='Expected eligible candidate count for apply mode.')
    parser.add_argument('--approved-table', default=None, help='Approved canonical table for apply mode; must be stations.')
    parser.add_argument('--approved-field', default=None, help='Approved canonical field for apply mode; must be station_type.')
    parser.add_argument('--approved-source-run', default=None, help='Approved source run key for apply mode.')
    parser.add_argument('--approved-source-file', default=None, help='Optional approved source file key for apply mode.')
    parser.add_argument('--approval-id', default=None, help='Operator approval reference for apply mode.')
    parser.add_argument('--confirm-station-type-canonical-pilot', action='store_true', help='Required explicit apply confirmation.')
    parser.add_argument('--max-rows', type=int, default=None, help='Maximum rows approved for apply mode.')
    parser.add_argument('--dsn', default=None, help='Canonical apply DSN. Required for apply mode.')
    parser.add_argument('--write', action='store_true', help='Unsupported alias; use --apply with explicit approval parameters.')
    parser.add_argument('--commit', action='store_true', help='Unsupported alias; use --apply with explicit approval parameters.')
    args = parser.parse_args(argv)
    if args.write or args.commit:
        parser.error('--write/--commit are not supported; use --apply with explicit Stage 18J approval parameters')
    if not args.apply and args.limit is None:
        parser.error('dry-run mode requires an explicit --limit max-row bound')
    if not args.apply and args.blocked_candidate_sample_limit is not None and args.blocked_candidate_sample_limit < 0:
        parser.error('--blocked-candidate-sample-limit must be >= 0')
    if args.apply:
        required = {
            '--artifact': args.artifact,
            '--artifact-sha256': args.artifact_sha256,
            '--expected-candidate-count': args.expected_candidate_count,
            '--approved-table': args.approved_table,
            '--approved-field': args.approved_field,
            '--approved-source-run': args.approved_source_run,
            '--approval-id': args.approval_id,
            '--max-rows': args.max_rows,
            '--dsn': args.dsn,
        }
        missing = [name for name, value in required.items() if value in (None, '')]
        if missing:
            parser.error('apply mode requires ' + ', '.join(missing))
        if not args.confirm_station_type_canonical_pilot:
            parser.error('apply mode requires --confirm-station-type-canonical-pilot')
    elif not args.reconciliation_report:
        parser.error('--reconciliation-report is required in dry-run mode')
    elif args.dsn:
        parser.error('--dsn is only valid in apply mode')
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.apply:
            with open(args.artifact, 'r', encoding='utf-8') as handle:
                dry_run_artifact = json.load(handle)
            conn = _connect_postgres(args.dsn)
            artifact = apply_station_type_pilot(
                conn,
                dry_run_artifact,
                artifact_sha256_expected=args.artifact_sha256,
                expected_candidate_count=args.expected_candidate_count,
                approved_table=args.approved_table,
                approved_field=args.approved_field,
                approved_source_run=args.approved_source_run,
                approved_source_file=args.approved_source_file,
                approval_id=args.approval_id,
                confirmation=args.confirm_station_type_canonical_pilot,
                max_rows=args.max_rows,
            )
        else:
            reconciliation_report_path = Path(args.reconciliation_report)
            with open(reconciliation_report_path, 'r', encoding='utf-8') as handle:
                reconciliation_report = json.load(handle)
            artifact = build_station_type_pilot_dry_run(
                reconciliation_report,
                limit=args.limit,
                allow_edsm_station_id=True,
                allow_undated_source_exception=args.allow_undated_source_exception,
                reconciliation_artifact_sha256=_file_sha256(reconciliation_report_path),
                reconciliation_artifact_basename=reconciliation_report_path.name,
                reconciliation_artifact_size_bytes=reconciliation_report_path.stat().st_size,
                blocked_candidate_sample_limit=args.blocked_candidate_sample_limit,
            )
    except (OSError, ValueError, TypeError) as exc:
        print(f'Stage 18J station type pilot failed: {exc}', file=sys.stderr)
        return 2

    output = json.dumps(artifact, sort_keys=True, indent=2)
    if args.output:
        Path(args.output).write_text(output + '\n', encoding='utf-8')
    if not args.quiet or not args.output:
        print(output)
    return 0


def _connect_postgres(dsn: str) -> Any:
    try:
        import psycopg  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only in real apply environments
        raise Stage18JPlanError('psycopg is required for guarded apply mode') from exc
    return psycopg.connect(dsn)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _fetchone_mapping(cur: Any) -> dict[str, Any] | None:
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    description = getattr(cur, 'description', None)
    if description:
        keys = [col[0] for col in description]
        return dict(zip(keys, row, strict=True))
    return None


def _normalise_station_type_value(value: Any) -> str | None:
    if value is None:
        return None
    return normalise_station_type_label(value)


def _apply_run_id(artifact_sha: str, approval_id: str | None, count: int) -> str:
    return hashlib.sha256(canonical_json(['stage18j_apply_run', artifact_sha, approval_id, count]).encode('utf-8')).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _source_scope(
    report: Mapping[str, Any],
    *,
    reconciliation_artifact_sha256: str | None = None,
    reconciliation_artifact_basename: str | None = None,
    reconciliation_artifact_size_bytes: int | None = None,
) -> dict[str, Any]:
    filters = _mapping(report.get('filters'))
    return {
        'input_schema_version': report.get('schema_version'),
        'input_dry_run': report.get('dry_run'),
        'input_artifact_basename': reconciliation_artifact_basename,
        'input_artifact_sha256': reconciliation_artifact_sha256,
        'input_artifact_size_bytes': reconciliation_artifact_size_bytes,
        'source_run_key': filters.get('source_run_key'),
        'source_file_key': filters.get('source_file_key'),
        'source': filters.get('source'),
        'input_canonical_writes_planned': _mapping(report.get('summary')).get('canonical_writes_planned'),
    }


def _station_external_identifier_match_type(
    *,
    source_market_id: int | None,
    canonical_market_id: int | None,
    source_edsm_station_id: int | None,
    canonical_edsm_station_id: int | None,
    allow_edsm_station_id: bool,
) -> str | None:
    # canonical.station_id is the database update target. It is not accepted as
    # external identity proof unless the canonical payload also exposes the
    # matching external identity field.
    if source_market_id is not None and canonical_market_id is not None and source_market_id == canonical_market_id:
        return 'market_id'
    if (
        allow_edsm_station_id
        and source_edsm_station_id is not None
        and canonical_edsm_station_id is not None
        and source_edsm_station_id == canonical_edsm_station_id
    ):
        return 'edsm_station_id'
    return None


def _station_type_rejection_reasons(
    *,
    candidate: Mapping[str, Any],
    checks: Mapping[str, bool],
    risk_flags: set[Any],
    review_classifications: set[Any],
) -> list[str]:
    reasons: set[str] = set()
    action = candidate.get('candidate_action')
    if action == 'ambiguous_match' or not checks.get('not_ambiguous_identity'):
        reasons.add('rejected_ambiguous_identity')
    if action == 'candidate_insert_missing_canonical' or risk_flags & SOURCE_ONLY_RISK_FLAGS:
        reasons.add('rejected_source_only_insert')
    if (
        not checks.get('canonical_station_exists')
        or not checks.get('source_system_id64_present')
        or not checks.get('external_station_identifier_matches')
        or not checks.get('system_id64_matches')
        or not checks.get('station_name_matches')
    ):
        reasons.add('rejected_missing_external_identity')
    if not checks.get('no_volatile_evidence') or 'volatile' in review_classifications:
        reasons.add('rejected_volatile_evidence')
    if not checks.get('source_station_type_permanent') or not checks.get('source_station_type_not_transient'):
        reasons.add('rejected_transient_non_slot')
    if not checks.get('entity_is_station') or not checks.get('has_no_non_station_type_difference'):
        reasons.add('rejected_non_station_type_change')
    if (
        action not in {'candidate_update', 'candidate_insert_missing_canonical', 'ambiguous_match'}
        or not checks.get('source_station_type_present')
        or not checks.get('source_station_type_normalised')
        or not checks.get('station_type_delta_present')
    ):
        reasons.add('rejected_missing_station_type_delta')
    if not checks.get('canonical_old_value_eligible'):
        reasons.add('rejected_ineligible_canonical_old_value')
    if (
        not checks.get('source_record_hash_present')
        or not checks.get('source_run_key_present')
        or not checks.get('source_file_key_present')
    ):
        reasons.add('rejected_missing_provenance')
    if not checks.get('freshness_allowed'):
        reasons.add('rejected_freshness')

    unhandled_risk_flags = _unhandled_station_type_blocking_risk_flags(risk_flags)
    if unhandled_risk_flags:
        if unhandled_risk_flags & AMBIGUOUS_RISK_FLAGS:
            reasons.add('rejected_ambiguous_identity')
        elif unhandled_risk_flags & SOURCE_ONLY_RISK_FLAGS:
            reasons.add('rejected_source_only_insert')
        elif unhandled_risk_flags & VOLATILE_RISK_FLAGS:
            reasons.add('rejected_volatile_evidence')
        else:
            reasons.add('rejected_non_station_type_change')
    if not checks.get('canonical_writes_planned_zero'):
        reasons.add('rejected_non_station_type_change')
    return sorted(reasons)


def _unhandled_station_type_blocking_risk_flags(risk_flags: set[Any]) -> set[str]:
    normalized = {str(flag) for flag in risk_flags}
    return normalized - STATION_TYPE_DRY_RUN_ALLOWED_RISK_FLAGS


def _has_volatile_evidence(
    candidate: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    warnings: Sequence[Mapping[str, Any]],
) -> bool:
    risk_flags = {str(flag) for flag in candidate.get('risk_flags') or []}
    review_classifications = {str(item) for item in candidate.get('review_classifications') or []}
    if source.get('source_class') == 'volatile':
        return True
    if candidate.get('risk_class') == 'volatile':
        return True
    if risk_flags & VOLATILE_RISK_FLAGS:
        return True
    if 'volatile' in review_classifications:
        return True
    return any(warning.get('reason') == 'volatile_source_evidence_not_canonical_update' for warning in warnings)


def _station_type_delta_present(old_value: Any, new_value: Any) -> bool:
    if new_value is None:
        return False
    return _normalise_station_type_value(old_value) != new_value


def _station_type_difference_value(differences: Sequence[Mapping[str, Any]]) -> Any:
    for difference in differences:
        if difference.get('field') == APPROVED_FIELD:
            return difference.get('staged')
    return None


def _has_only_station_type_difference(differences: Sequence[Mapping[str, Any]]) -> bool:
    return len(differences) == 1 and differences[0].get('field') == APPROVED_FIELD


def _has_non_station_type_difference(differences: Sequence[Mapping[str, Any]]) -> bool:
    return any(difference.get('field') != APPROVED_FIELD for difference in differences)


def _canonical_old_value_eligible(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if text == '':
        return True
    return normalise_station_type_label(text) == 'Unknown'


def _freshness_allowed(candidate: Mapping[str, Any], *, allow_undated_source_exception: bool) -> bool:
    freshness = _mapping(candidate.get('source_freshness'))
    impact = freshness.get('freshness_impact')
    if impact == 'timestamped_source':
        return True
    if impact in {'file_snapshot_review', 'undated_source_review'}:
        return allow_undated_source_exception
    return False


def _candidate_id(*, source: Mapping[str, Any], canonical_station_id: int | None, new_value: str | None) -> str:
    return hashlib.sha256(canonical_json([
        'stage18j_station_type_candidate',
        source.get('source_run_key'),
        source.get('source_file_key'),
        source.get('source_record_hash'),
        canonical_station_id,
        APPROVED_FIELD,
        new_value,
    ]).encode('utf-8')).hexdigest()


def _validate_blocked_candidate_sample_limit(value: int | None) -> int | None:
    if value is None:
        return None
    if value < 0:
        raise Stage18JPlanError('blocked candidate sample limit must be >= 0')
    return value


def _record_blocked_candidate(
    candidate: Mapping[str, Any],
    *,
    blocked_samples: list[dict[str, Any]],
    blocked_count: int,
    rejection_reason_counts: dict[str, int],
    sample_limit: int | None,
) -> int:
    row = dict(candidate)
    for reason in row.get('rejection_reasons') or row.get('blocking_reasons') or []:
        key = str(reason)
        rejection_reason_counts[key] = rejection_reason_counts.get(key, 0) + 1
    if sample_limit is None or len(blocked_samples) < sample_limit:
        blocked_samples.append(row)
    return blocked_count + 1


def _new_identity_coverage_summary() -> dict[str, int]:
    return {
        'total_candidates_seen': 0,
        'source_market_id_present': 0,
        'source_market_id_missing': 0,
        'canonical_market_id_present': 0,
        'canonical_market_id_missing': 0,
        'market_id_both_present': 0,
        'market_id_both_missing': 0,
        'market_id_source_only': 0,
        'market_id_canonical_only': 0,
        'market_id_match': 0,
        'market_id_mismatch': 0,
        'source_edsm_station_id_present': 0,
        'source_edsm_station_id_missing': 0,
        'canonical_edsm_station_id_present': 0,
        'canonical_edsm_station_id_missing': 0,
        'edsm_station_id_both_present': 0,
        'edsm_station_id_both_missing': 0,
        'edsm_station_id_source_only': 0,
        'edsm_station_id_canonical_only': 0,
        'edsm_station_id_match': 0,
        'edsm_station_id_mismatch': 0,
        'source_system_id64_present': 0,
        'source_system_id64_missing': 0,
        'canonical_system_id64_present': 0,
        'canonical_system_id64_missing': 0,
        'system_id64_both_present': 0,
        'system_id64_match': 0,
        'system_id64_mismatch': 0,
        'source_station_name_present': 0,
        'source_station_name_missing': 0,
        'canonical_station_name_present': 0,
        'canonical_station_name_missing': 0,
        'station_name_both_present': 0,
        'station_name_match': 0,
        'station_name_mismatch': 0,
        'canonical_match_count_exactly_one': 0,
        'canonical_match_count_not_one': 0,
        'canonical_match_count_zero': 0,
        'canonical_match_count_multiple': 0,
        'canonical_station_present': 0,
        'canonical_station_missing': 0,
        'canonical_station_present_but_external_ids_missing_in_payload': 0,
        'possible_canonical_external_ids_omitted_from_reconciliation_payload': 0,
        'external_identity_proof_present': 0,
        'external_identity_proof_absent': 0,
        'internal_primary_key_only_not_identity_proof': 0,
    }


def _record_identity_coverage(
    counts: dict[str, int],
    candidate: Mapping[str, Any],
    *,
    allow_edsm_station_id: bool,
) -> None:
    source = _mapping(candidate.get('source'))
    canonical = _mapping(candidate.get('canonical'))
    canonical_matches = _sequence_of_mappings(candidate.get('canonical_matches'))

    source_market_id = _read_int(source.get('market_id'))
    canonical_market_id = _read_int(canonical.get('market_id'))
    source_edsm_station_id = _read_int(source.get('edsm_station_id'))
    canonical_edsm_station_id = _read_int(canonical.get('edsm_station_id'))
    source_system_id64 = _read_int(source.get('system_id64'))
    canonical_system_id64 = _read_int(canonical.get('system_id64'))
    canonical_station_id = _read_int(canonical.get('station_id'))
    source_name = _normalise_name(source.get('station_name'))
    canonical_name = _normalise_name(canonical.get('station_name'))

    counts['total_candidates_seen'] += 1

    _record_pair_coverage(
        counts,
        prefix='market_id',
        source_value=source_market_id,
        canonical_value=canonical_market_id,
    )
    _record_pair_coverage(
        counts,
        prefix='edsm_station_id',
        source_value=source_edsm_station_id,
        canonical_value=canonical_edsm_station_id,
    )
    _record_pair_coverage(
        counts,
        prefix='system_id64',
        source_value=source_system_id64,
        canonical_value=canonical_system_id64,
        include_source_only=False,
    )
    _record_pair_coverage(
        counts,
        prefix='station_name',
        source_value=source_name,
        canonical_value=canonical_name,
        include_source_only=False,
    )

    match_count = len(canonical_matches)
    if match_count == 1:
        counts['canonical_match_count_exactly_one'] += 1
    else:
        counts['canonical_match_count_not_one'] += 1
        if match_count == 0:
            counts['canonical_match_count_zero'] += 1
        else:
            counts['canonical_match_count_multiple'] += 1

    if canonical_station_id is None:
        counts['canonical_station_missing'] += 1
    else:
        counts['canonical_station_present'] += 1
        if canonical_market_id is None and canonical_edsm_station_id is None:
            counts['canonical_station_present_but_external_ids_missing_in_payload'] += 1
            counts['possible_canonical_external_ids_omitted_from_reconciliation_payload'] += 1

    identifier_match_type = _station_external_identifier_match_type(
        source_market_id=source_market_id,
        canonical_market_id=canonical_market_id,
        source_edsm_station_id=source_edsm_station_id,
        canonical_edsm_station_id=canonical_edsm_station_id,
        allow_edsm_station_id=allow_edsm_station_id,
    )
    if identifier_match_type is None:
        counts['external_identity_proof_absent'] += 1
    else:
        counts['external_identity_proof_present'] += 1

    if (
        identifier_match_type is None
        and canonical_station_id is not None
        and canonical_station_id in {source_market_id, source_edsm_station_id}
    ):
        counts['internal_primary_key_only_not_identity_proof'] += 1


def _record_pair_coverage(
    counts: dict[str, int],
    *,
    prefix: str,
    source_value: Any,
    canonical_value: Any,
    include_source_only: bool = True,
) -> None:
    if source_value is None:
        counts[f'source_{prefix}_missing'] += 1
    else:
        counts[f'source_{prefix}_present'] += 1
    if canonical_value is None:
        counts[f'canonical_{prefix}_missing'] += 1
    else:
        counts[f'canonical_{prefix}_present'] += 1

    if source_value is not None and canonical_value is not None:
        counts[f'{prefix}_both_present'] += 1
        if source_value == canonical_value:
            counts[f'{prefix}_match'] += 1
        else:
            counts[f'{prefix}_mismatch'] += 1
    elif include_source_only and source_value is None and canonical_value is None:
        counts[f'{prefix}_both_missing'] += 1
    elif include_source_only and source_value is None:
        counts[f'{prefix}_canonical_only'] += 1
    elif include_source_only and canonical_value is None:
        counts[f'{prefix}_source_only'] += 1


def _blocked_reason_distribution(blocked: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in blocked:
        reasons = row.get('rejection_reasons') or row.get('blocking_reasons') or []
        for reason in reasons:
            counts[str(reason)] = counts.get(str(reason), 0) + 1
    return dict(sorted(counts.items()))


def _rejection_summary(rejection_reason_counts: Mapping[str, int]) -> dict[str, int]:
    return {
        reason: int(rejection_reason_counts.get(reason, 0))
        for reason in STATION_TYPE_DRY_RUN_REJECTION_REASONS
    }


def _normalise_name(value: Any) -> str | None:
    if value is None:
        return None
    text = ' '.join(str(value).strip().lower().split())
    return text or None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _read_int(value: Any) -> int | None:
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
