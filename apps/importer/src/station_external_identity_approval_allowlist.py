#!/usr/bin/env python3
"""Offline approval allowlist generator for external station identity rows.

This tool reads a local ``station_external_identity_review_packet/v1`` JSON
artifact and emits a deterministic allowlist for a future controlled
``station_external_identity`` load. It does not accept a DSN and never connects
to a database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 'station_external_identity_load_approval_allowlist/v1'
REVIEW_PACKET_SCHEMA_VERSION = 'station_external_identity_review_packet/v1'
TOOL_NAME = 'station_external_identity_approval_allowlist'
TOOL_VERSION = 'v1'
MAX_ROWS_CAP = 20
REVIEWER_DECISION = 'approve_selected_identity_rows'
ALLOWED_REVIEW_STATUSES = ('needs_manual_review', 'reviewed_for_identity_load')
FORBIDDEN_FLAG_ERROR = (
    'write/apply/load/import/reconciliation/summarizer/station-type/canonical flags are not available; '
    'this tool only emits an offline approval allowlist artifact'
)
REQUIRED_CHECK_KEYS = (
    'canonical_station_id_present',
    'system_id64_present',
    'station_name_present',
    'source_run_key_present',
    'source_file_key_present',
    'source_record_hash_present',
    'external_id_present',
    'identity_status_is_confirmed',
    'conflict_reason_is_null',
    'station_type_write_not_planned',
)


class IdentityApprovalAllowlistError(ValueError):
    """Raised when an approval allowlist cannot be built safely."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Build an offline station_external_identity approval allowlist from a review packet.',
    )
    parser.add_argument('--review-packet', required=True, help='Local station_external_identity_review_packet/v1 JSON file.')
    parser.add_argument('--expected-review-packet-sha256', required=True, help='Expected SHA-256 of the review packet file.')
    parser.add_argument('--output', required=True, help='Path to write the JSON approval allowlist.')
    parser.add_argument('--json', action='store_true', help='Emit JSON to stdout after writing output.')
    parser.add_argument('--confirm-reviewed', action='store_true', help='Required confirmation that rows were reviewed for identity load.')
    parser.add_argument(
        '--reviewer-decision',
        required=True,
        help=f'Required decision string, must be {REVIEWER_DECISION}.',
    )
    parser.add_argument('--max-rows', type=int, required=True, help=f'Maximum approved rows, maximum {MAX_ROWS_CAP}.')
    parser.add_argument(
        '--reviewer',
        default='stage-18j-p14c-offline-review',
        help='Reviewer label recorded in the allowlist metadata.',
    )
    parser.add_argument('--write', action='store_true', help='Unsupported; this tool is offline allowlist-only.')
    parser.add_argument('--apply', action='store_true', help='Unsupported; canonical apply is blocked.')
    parser.add_argument('--canonical', action='store_true', help='Unsupported; canonical work is blocked.')
    parser.add_argument('--canonical-apply', action='store_true', help='Unsupported; canonical apply is blocked.')
    parser.add_argument('--station-type', action='store_true', help='Unsupported; station-type writes are blocked.')
    parser.add_argument('--station-type-dry-run', action='store_true', help='Unsupported; station-type dry-run is blocked.')
    parser.add_argument('--load', action='store_true', help='Unsupported; this tool does not load rows.')
    parser.add_argument('--commit', action='store_true', help='Unsupported; this tool does not create commits.')
    parser.add_argument('--reconciliation', action='store_true', help='Unsupported; reconciliation is blocked.')
    parser.add_argument('--import', dest='run_import', action='store_true', help='Unsupported; imports are blocked.')
    parser.add_argument('--summarizer', action='store_true', help='Unsupported; summarizer runs are blocked.')
    args = parser.parse_args(argv)

    if (
        args.write
        or args.apply
        or args.canonical
        or args.canonical_apply
        or args.station_type
        or args.station_type_dry_run
        or args.load
        or args.commit
        or args.reconciliation
        or args.run_import
        or args.summarizer
    ):
        parser.error(FORBIDDEN_FLAG_ERROR)
    if not args.confirm_reviewed:
        parser.error('--confirm-reviewed is required')
    if args.reviewer_decision != REVIEWER_DECISION:
        parser.error(f'--reviewer-decision must be {REVIEWER_DECISION}')
    if args.max_rows < 1:
        parser.error('--max-rows must be >= 1')
    if args.max_rows > MAX_ROWS_CAP:
        parser.error(f'--max-rows must be <= {MAX_ROWS_CAP}')
    if not args.reviewer.strip():
        parser.error('--reviewer must be non-empty')
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        artifact = build_allowlist_from_file(
            args.review_packet,
            expected_review_packet_sha256=args.expected_review_packet_sha256,
            max_rows=args.max_rows,
            reviewer=args.reviewer,
            reviewer_decision=args.reviewer_decision,
            confirm_reviewed=args.confirm_reviewed,
        )
    except (OSError, ValueError) as exc:
        print(f'station external identity approval allowlist generation failed: {exc}', file=sys.stderr)
        return 2

    output = json_dumps_artifact(artifact)
    Path(args.output).write_text(output + '\n', encoding='utf-8')
    if args.json:
        print(output)
    return 0


def build_allowlist_from_file(
    review_packet_path: str | Path,
    *,
    expected_review_packet_sha256: str,
    max_rows: int,
    reviewer: str,
    reviewer_decision: str = REVIEWER_DECISION,
    confirm_reviewed: bool = True,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if max_rows < 1:
        raise IdentityApprovalAllowlistError('max_rows must be >= 1')
    if max_rows > MAX_ROWS_CAP:
        raise IdentityApprovalAllowlistError(f'max_rows must be <= {MAX_ROWS_CAP}')
    if not confirm_reviewed:
        raise IdentityApprovalAllowlistError('confirm_reviewed is required')
    if reviewer_decision != REVIEWER_DECISION:
        raise IdentityApprovalAllowlistError(f'reviewer_decision must be {REVIEWER_DECISION}')
    if not reviewer.strip():
        raise IdentityApprovalAllowlistError('reviewer must be non-empty')

    review_packet, actual_sha256, size_bytes = _load_json_file_with_sha(
        review_packet_path,
        expected_sha256=expected_review_packet_sha256,
    )
    return build_allowlist(
        review_packet,
        source_review_packet_basename=Path(review_packet_path).name,
        source_review_packet_sha256=actual_sha256,
        source_review_packet_size_bytes=size_bytes,
        max_rows=max_rows,
        reviewer=reviewer,
        reviewer_decision=reviewer_decision,
        generated_at=generated_at,
    )


def build_allowlist(
    review_packet: Mapping[str, Any],
    *,
    source_review_packet_basename: str,
    source_review_packet_sha256: str,
    source_review_packet_size_bytes: int,
    max_rows: int,
    reviewer: str,
    reviewer_decision: str = REVIEWER_DECISION,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if max_rows < 1:
        raise IdentityApprovalAllowlistError('max_rows must be >= 1')
    if max_rows > MAX_ROWS_CAP:
        raise IdentityApprovalAllowlistError(f'max_rows must be <= {MAX_ROWS_CAP}')
    if reviewer_decision != REVIEWER_DECISION:
        raise IdentityApprovalAllowlistError(f'reviewer_decision must be {REVIEWER_DECISION}')
    if not reviewer.strip():
        raise IdentityApprovalAllowlistError('reviewer must be non-empty')

    source_integrity = _mapping(review_packet.get('artifact_integrity'))
    source_review_packet_integrity_sha256 = source_integrity.get('canonical_json_sha256')
    if not isinstance(source_review_packet_integrity_sha256, str) or not source_review_packet_integrity_sha256:
        raise IdentityApprovalAllowlistError('review packet artifact_integrity canonical_json_sha256 is required')
    selected_items = _select_review_items(review_packet, max_rows=max_rows)
    approved_review_item_ids = [str(item['review_item_id']) for item in selected_items]
    approved_plan_row_ids = [str(_mapping(item['planned_row'])['plan_row_id']) for item in selected_items]
    now = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    declaration = (
        'Approval is limited to loading the listed external identity evidence rows '
        'into station_external_identity; it does not approve station-type writes or canonical apply.'
    )

    artifact: dict[str, Any] = {
        'schema_version': SCHEMA_VERSION,
        'generated_at': now,
        'tool': {
            'name': TOOL_NAME,
            'version': TOOL_VERSION,
        },
        'offline': True,
        'read_only': True,
        'approval_record_created': False,
        'source_review_packet_basename': source_review_packet_basename,
        'source_review_packet_sha256': source_review_packet_sha256,
        'source_review_packet_size_bytes': source_review_packet_size_bytes,
        'source_review_packet_integrity_sha256': source_review_packet_integrity_sha256,
        'review_packet_sha256': source_review_packet_sha256,
        'reviewer_decision': reviewer_decision,
        'reviewer': reviewer.strip(),
        'reviewed_at': now,
        'declaration': declaration,
        'reviewer_attestation': {
            'reviewed_packet_schema': REVIEW_PACKET_SCHEMA_VERSION,
            'reviewed_packet_sha256': source_review_packet_sha256,
            'reviewed_packet_integrity_sha256': source_review_packet_integrity_sha256,
            'reviewed_rows_count': len(selected_items),
            'decision_scope': 'external_identity_evidence_load_only',
            'does_not_approve_station_type_writes': True,
            'does_not_approve_canonical_apply': True,
            'does_not_create_production_approval_record': True,
        },
        'max_rows': max_rows,
        'approved_review_item_ids': approved_review_item_ids,
        'approved_plan_row_ids': approved_plan_row_ids,
        'approved_rows_count': len(selected_items),
        'identity_rows_written': 0,
        'canonical_writes_planned': 0,
        'station_type_writes_planned': 0,
        'safety_summary': {
            'review_packet_schema_valid': True,
            'review_packet_sha256_verified': True,
            'manual_review_items_approved': len(selected_items),
            'all_required_checks_passed': True,
            'source_provenance_present': True,
            'external_id_present': True,
            'identity_status_confirmed': True,
            'conflict_reason_null': True,
            'station_type_writes_planned': 0,
            'canonical_writes_planned': 0,
            'identity_rows_written': 0,
            'approval_record_created': False,
            'db_connections_allowed': False,
            'db_write_statements_included': False,
            'identity_load_performed': False,
            'station_type_dry_run_performed': False,
            'canonical_apply_performed': False,
        },
    }
    artifact['artifact_integrity'] = {
        'hash_algorithm': 'sha256',
        'canonical_json_sha256': artifact_sha256(artifact),
        'canonicalization': 'json.dumps(sort_keys=True,separators=(comma,colon),ensure_ascii=True,allow_nan=False) excluding artifact_integrity',
    }
    return _json_clone(artifact)


def json_dumps_artifact(artifact: Mapping[str, Any]) -> str:
    return canonical_json(artifact)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def artifact_sha256(value: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(value))
    payload.pop('artifact_integrity', None)
    return hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()


def _load_json_file_with_sha(
    path_value: str | Path,
    *,
    expected_sha256: str,
) -> tuple[dict[str, Any], str, int]:
    path = Path(path_value)
    if not path.is_file():
        raise IdentityApprovalAllowlistError(f'review packet is missing: {path}')
    payload = path.read_bytes()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256.lower():
        raise IdentityApprovalAllowlistError(
            f'review packet checksum mismatch. Expected {expected_sha256}, got {actual_sha256}',
        )
    try:
        data = json.loads(payload.decode('utf-8'))
    except json.JSONDecodeError as exc:
        raise IdentityApprovalAllowlistError(f'review packet is not valid JSON: {exc}') from exc
    if not isinstance(data, Mapping):
        raise IdentityApprovalAllowlistError('review packet must be a JSON object')
    return dict(data), actual_sha256, len(payload)


def _select_review_items(review_packet: Mapping[str, Any], *, max_rows: int) -> list[dict[str, Any]]:
    _validate_review_packet_safety(review_packet)
    items = review_packet.get('manual_review_items')
    if not isinstance(items, list):
        raise IdentityApprovalAllowlistError('review packet manual_review_items must be a list')
    selected = [_validate_review_item(item_value) for item_value in items[:max_rows]]
    if not selected:
        raise IdentityApprovalAllowlistError('review packet selected zero review rows')

    planned_rows = review_packet.get('planned_rows')
    if planned_rows is not None:
        if not isinstance(planned_rows, list):
            raise IdentityApprovalAllowlistError('review packet planned_rows must be a list')
        embedded_rows = [item['planned_row'] for item in selected]
        if embedded_rows != planned_rows[:len(selected)]:
            raise IdentityApprovalAllowlistError('embedded manual review rows must match top-level planned_rows')
    return selected


def _validate_review_packet_safety(review_packet: Mapping[str, Any]) -> None:
    if review_packet.get('schema_version') != REVIEW_PACKET_SCHEMA_VERSION:
        raise IdentityApprovalAllowlistError(f'review packet schema_version must be {REVIEW_PACKET_SCHEMA_VERSION}')
    if review_packet.get('identity_rows_written') != 0:
        raise IdentityApprovalAllowlistError('review packet identity_rows_written must be 0')
    if review_packet.get('canonical_writes_planned') != 0:
        raise IdentityApprovalAllowlistError('review packet canonical_writes_planned must be 0')
    if review_packet.get('station_type_writes_planned') != 0:
        raise IdentityApprovalAllowlistError('review packet station_type_writes_planned must be 0')
    if review_packet.get('approval_record_created') is True:
        raise IdentityApprovalAllowlistError('review packet approval_record_created must be false')
    summary = _mapping(review_packet.get('summary'))
    for key in ('canonical_writes_planned', 'station_type_writes_planned', 'identity_rows_written'):
        value = summary.get(key)
        if value is not None and value != 0:
            raise IdentityApprovalAllowlistError(f'review packet summary {key} must be 0')
    if summary.get('approval_record_created') is True:
        raise IdentityApprovalAllowlistError('review packet summary approval_record_created must be false')


def _validate_review_item(item_value: Any) -> dict[str, Any]:
    if not isinstance(item_value, Mapping):
        raise IdentityApprovalAllowlistError('manual review item must be an object')
    item = dict(item_value)
    review_item_id = item.get('review_item_id')
    if not review_item_id:
        raise IdentityApprovalAllowlistError('manual review item is missing review_item_id')
    review_status = item.get('review_status')
    if review_status not in ALLOWED_REVIEW_STATUSES:
        allowed = ', '.join(ALLOWED_REVIEW_STATUSES)
        raise IdentityApprovalAllowlistError(f'manual review item review_status must be one of: {allowed}')
    row = item.get('planned_row')
    if not isinstance(row, Mapping) or not row:
        raise IdentityApprovalAllowlistError('manual review item is missing planned_row')
    row = _json_clone(row)
    if not row.get('plan_row_id'):
        raise IdentityApprovalAllowlistError('planned row is missing plan_row_id')
    checks = item.get('checks')
    if not isinstance(checks, Mapping) or not checks:
        raise IdentityApprovalAllowlistError('manual review item is missing checks')
    failed_checks = [key for key in REQUIRED_CHECK_KEYS if checks.get(key) is not True]
    if failed_checks:
        raise IdentityApprovalAllowlistError('manual review item has failed required checks: ' + ', '.join(failed_checks))
    if row.get('identity_status') != 'confirmed':
        raise IdentityApprovalAllowlistError('planned row identity_status must be confirmed')
    if row.get('conflict_reason') is not None:
        raise IdentityApprovalAllowlistError('planned row conflict_reason must be null')
    for field in ('source_run_key', 'source_file_key', 'source_record_hash'):
        if not row.get(field):
            raise IdentityApprovalAllowlistError(f'planned row is missing source provenance field: {field}')
    if row.get('market_id') is None and row.get('edsm_station_id') is None:
        raise IdentityApprovalAllowlistError('planned row must have market_id or edsm_station_id')
    if row.get('station_type_writes_planned') != 0:
        raise IdentityApprovalAllowlistError('planned row station_type_writes_planned must be 0')
    if row.get('canonical_writes_planned') != 0:
        raise IdentityApprovalAllowlistError('planned row canonical_writes_planned must be 0')
    item['review_item_id'] = str(review_item_id)
    item['planned_row'] = row
    item['checks'] = _json_clone(checks)
    return item


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json_clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


if __name__ == '__main__':
    raise SystemExit(main())
