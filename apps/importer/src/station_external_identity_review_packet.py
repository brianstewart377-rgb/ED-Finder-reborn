#!/usr/bin/env python3
"""Offline planned-row review packet generator for external station identity.

This tool reads a local ``station_external_identity_load_plan/v1`` JSON
artifact and emits a deterministic manual review packet for the bounded
planned rows. It does not accept a DSN and never connects to a database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 'station_external_identity_review_packet/v1'
SOURCE_SCHEMA_VERSION = 'station_external_identity_load_plan/v1'
TOOL_NAME = 'station_external_identity_review_packet'
TOOL_VERSION = 'v1'
MAX_PLANNED_ROWS_CAP = 20
WRITE_FLAG_ERROR = 'write/apply/load/commit flags are not available; this tool only emits an offline review packet'
MANUAL_REVIEW_STATUS = 'needs_manual_review'
REVIEW_CHECK_KEYS: tuple[str, ...] = (
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


class IdentityReviewPacketError(ValueError):
    """Raised when a review packet cannot be built safely."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Build an offline manual review packet from a bounded station_external_identity load-plan artifact.',
    )
    parser.add_argument('--load-plan-artifact', required=True, help='Local station_external_identity_load_plan/v1 JSON file.')
    parser.add_argument('--expected-load-plan-sha256', required=True, help='Expected SHA-256 of the load-plan artifact file.')
    parser.add_argument('--output', default=None, help='Optional path to write the JSON review packet.')
    parser.add_argument('--json', action='store_true', help='Emit JSON to stdout. Output is JSON by default.')
    parser.add_argument(
        '--max-planned-rows',
        type=int,
        default=MAX_PLANNED_ROWS_CAP,
        help=f'Maximum planned rows to include, capped at {MAX_PLANNED_ROWS_CAP}.',
    )
    parser.add_argument('--apply', action='store_true', help='Unsupported; this tool is offline review-only.')
    parser.add_argument('--write', action='store_true', help='Unsupported; this tool is offline review-only.')
    parser.add_argument('--write-staging', action='store_true', help='Unsupported; this tool is offline review-only.')
    parser.add_argument('--load', action='store_true', help='Unsupported; this tool is offline review-only.')
    parser.add_argument('--commit', action='store_true', help='Unsupported; this tool is offline review-only.')
    args = parser.parse_args(argv)

    if args.apply or args.write or args.write_staging or args.load or args.commit:
        parser.error(WRITE_FLAG_ERROR)
    if args.max_planned_rows < 1:
        parser.error('--max-planned-rows must be >= 1')
    if args.max_planned_rows > MAX_PLANNED_ROWS_CAP:
        parser.error(f'--max-planned-rows must be <= {MAX_PLANNED_ROWS_CAP}')
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        artifact = build_review_packet_from_file(
            args.load_plan_artifact,
            expected_load_plan_sha256=args.expected_load_plan_sha256,
            max_planned_rows=args.max_planned_rows,
        )
    except (OSError, ValueError) as exc:
        print(f'station external identity review packet generation failed: {exc}', file=sys.stderr)
        return 2

    output = json_dumps_artifact(artifact)
    if args.output:
        Path(args.output).write_text(output + '\n', encoding='utf-8')
    if args.json or not args.output:
        print(output)
    return 0


def build_review_packet_from_file(
    load_plan_artifact: str | Path,
    *,
    expected_load_plan_sha256: str,
    max_planned_rows: int = MAX_PLANNED_ROWS_CAP,
) -> dict[str, Any]:
    if max_planned_rows < 1:
        raise IdentityReviewPacketError('max_planned_rows must be >= 1')
    if max_planned_rows > MAX_PLANNED_ROWS_CAP:
        raise IdentityReviewPacketError(f'max_planned_rows must be <= {MAX_PLANNED_ROWS_CAP}')

    path = Path(load_plan_artifact)
    if not path.is_file():
        raise IdentityReviewPacketError(f'load-plan artifact is missing: {path}')

    payload = path.read_bytes()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_load_plan_sha256.lower():
        raise IdentityReviewPacketError(
            f'load-plan artifact checksum mismatch. Expected {expected_load_plan_sha256}, got {actual_sha256}',
        )

    try:
        load_plan = json.loads(payload.decode('utf-8'))
    except json.JSONDecodeError as exc:
        raise IdentityReviewPacketError(f'load-plan artifact is not valid JSON: {exc}') from exc
    if not isinstance(load_plan, Mapping):
        raise IdentityReviewPacketError('load-plan artifact must be a JSON object')

    return build_review_packet(
        load_plan,
        source_artifact_basename=path.name,
        source_artifact_size_bytes=len(payload),
        source_artifact_sha256=actual_sha256,
        max_planned_rows=max_planned_rows,
    )


def build_review_packet(
    load_plan: Mapping[str, Any],
    *,
    source_artifact_basename: str,
    source_artifact_size_bytes: int,
    source_artifact_sha256: str,
    max_planned_rows: int = MAX_PLANNED_ROWS_CAP,
) -> dict[str, Any]:
    if max_planned_rows < 1:
        raise IdentityReviewPacketError('max_planned_rows must be >= 1')
    if max_planned_rows > MAX_PLANNED_ROWS_CAP:
        raise IdentityReviewPacketError(f'max_planned_rows must be <= {MAX_PLANNED_ROWS_CAP}')
    if load_plan.get('schema_version') != SOURCE_SCHEMA_VERSION:
        raise IdentityReviewPacketError(f'load-plan artifact schema_version must be {SOURCE_SCHEMA_VERSION}')
    _assert_load_plan_is_no_write(load_plan)

    planned_rows_raw = load_plan.get('planned_rows')
    if not isinstance(planned_rows_raw, list):
        raise IdentityReviewPacketError('load-plan artifact planned_rows must be a list')

    planned_rows = [_json_clone(row) for row in planned_rows_raw[:max_planned_rows]]
    source_summary = _mapping(load_plan.get('summary'))
    filters = _mapping(load_plan.get('filters'))
    source_integrity = _mapping(load_plan.get('artifact_integrity'))
    manual_review_items = [
        _manual_review_item(row, index=index, source_artifact_sha256=source_artifact_sha256)
        for index, row in enumerate(planned_rows, start=1)
    ]

    artifact: dict[str, Any] = {
        'schema_version': SCHEMA_VERSION,
        'tool': {
            'name': TOOL_NAME,
            'version': TOOL_VERSION,
        },
        'dry_run': True,
        'read_only': True,
        'report_only': True,
        'canonical_writes_planned': 0,
        'station_type_writes_planned': 0,
        'identity_rows_planned': len(planned_rows),
        'identity_rows_written': 0,
        'approval_record_created': False,
        'max_planned_rows': max_planned_rows,
        'source_artifact': {
            'artifact_type': SOURCE_SCHEMA_VERSION,
            'basename': source_artifact_basename,
            'size_bytes': source_artifact_size_bytes,
            'sha256': source_artifact_sha256,
            'artifact_integrity_sha256': source_integrity.get('canonical_json_sha256'),
        },
        'source_scope': {
            'source': filters.get('source'),
            'source_run_key': filters.get('source_run_key'),
            'source_file_key': filters.get('source_file_key'),
            'source_max_rows': load_plan.get('max_rows'),
            'source_identity_rows_planned': load_plan.get('identity_rows_planned'),
            'source_planned_rows_count': source_summary.get('planned_rows_count'),
            'total_candidates_seen': source_summary.get('total_candidates_seen'),
            'eligible_confirmed_candidates_seen': source_summary.get('eligible_confirmed_candidates_seen'),
            'eligible_beyond_max_rows': _count(source_summary, 'skipped_reason_counts', 'eligible_beyond_max_rows'),
            'source_only_no_canonical_station_match': _count(
                source_summary,
                'rejected_reason_counts',
                'source_only_no_canonical_station_match',
            ),
            'ambiguous_canonical_station_match': _count(
                source_summary,
                'conflicting_reason_counts',
                'ambiguous_canonical_station_match',
            ),
            'candidate_status_counts': _json_clone(source_summary.get('candidate_status_counts') or {}),
        },
        'summary': {
            'planned_rows_available': len(planned_rows_raw),
            'planned_rows_included': len(planned_rows),
            'planned_rows_count': len(planned_rows),
            'planned_rows_capped': len(planned_rows_raw) > len(planned_rows),
            'manual_review_items_count': len(manual_review_items),
            'manual_review_status_counts': {MANUAL_REVIEW_STATUS: len(manual_review_items)},
            'canonical_writes_planned': 0,
            'station_type_writes_planned': 0,
            'identity_rows_written': 0,
            'approval_record_created': False,
        },
        'planned_rows': planned_rows,
        'manual_review_items': manual_review_items,
        'safety_boundaries': {
            'db_connections_allowed': False,
            'dsn_accepted': False,
            'identity_load_allowed': False,
            'station_type_dry_run_allowed': False,
            'canonical_apply_allowed': False,
            'approval_record_allowed': False,
        },
    }
    artifact['artifact_integrity'] = {
        'hash_algorithm': 'sha256',
        'canonical_json_sha256': artifact_sha256(artifact),
        'canonicalization': 'json.dumps(sort_keys=True,separators=(comma,colon),ensure_ascii=True,allow_nan=False) excluding artifact_integrity',
    }
    return artifact


def json_dumps_artifact(artifact: Mapping[str, Any]) -> str:
    return canonical_json(artifact)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def artifact_sha256(value: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(value))
    payload.pop('artifact_integrity', None)
    return hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()


def _manual_review_item(
    planned_row: Mapping[str, Any],
    *,
    index: int,
    source_artifact_sha256: str,
) -> dict[str, Any]:
    row = _json_clone(planned_row)
    return {
        'review_item_id': hashlib.sha256(
            canonical_json([
                'station_external_identity_review_packet_item',
                source_artifact_sha256,
                index,
                row.get('plan_row_id'),
                row,
            ]).encode('utf-8'),
        ).hexdigest(),
        'planned_row_index': index,
        'review_status': MANUAL_REVIEW_STATUS,
        'planned_row': row,
        'checks': _planned_row_checks(row),
        'reviewer_notes': None,
    }


def _planned_row_checks(row: Mapping[str, Any]) -> dict[str, bool]:
    checks = {
        'canonical_station_id_present': row.get('canonical_station_id') is not None,
        'system_id64_present': row.get('system_id64') is not None,
        'station_name_present': bool(row.get('station_name')),
        'source_run_key_present': bool(row.get('source_run_key')),
        'source_file_key_present': bool(row.get('source_file_key')),
        'source_record_hash_present': bool(row.get('source_record_hash')),
        'external_id_present': row.get('market_id') is not None or row.get('edsm_station_id') is not None,
        'identity_status_is_confirmed': row.get('identity_status') == 'confirmed',
        'conflict_reason_is_null': row.get('conflict_reason') is None,
        'station_type_write_not_planned': row.get('station_type_writes_planned') == 0,
    }
    return {key: bool(checks[key]) for key in REVIEW_CHECK_KEYS}


def _assert_load_plan_is_no_write(load_plan: Mapping[str, Any]) -> None:
    required_true = ('dry_run', 'read_only', 'report_only')
    for key in required_true:
        if load_plan.get(key) is not True:
            raise IdentityReviewPacketError(f'load-plan artifact must have {key} = true')

    required_zero = ('canonical_writes_planned', 'station_type_writes_planned', 'identity_rows_written')
    for key in required_zero:
        if load_plan.get(key) != 0:
            raise IdentityReviewPacketError(f'load-plan artifact must have {key} = 0')

    summary = _mapping(load_plan.get('summary'))
    for key in required_zero:
        value = summary.get(key)
        if value is not None and value != 0:
            raise IdentityReviewPacketError(f'load-plan artifact summary must have {key} = 0')


def _count(parent: Mapping[str, Any], child_key: str, key: str) -> int:
    child = parent.get(child_key)
    if isinstance(child, Mapping):
        value = child.get(key, 0)
        if isinstance(value, int):
            return value
    return 0


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _json_clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


if __name__ == '__main__':
    raise SystemExit(main())
