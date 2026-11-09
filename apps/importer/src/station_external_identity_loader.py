#!/usr/bin/env python3
"""Controlled external station identity loader and dry-run planner.

The dry-run mode validates a local review packet and emits a compact execution
plan without opening a database connection. The write-reviewed mode requires a
separate approval allowlist artifact and writes only allowlisted rows to
``station_external_identity``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 'station_external_identity_load_execution_plan/v1'
REVIEW_PACKET_SCHEMA_VERSION = 'station_external_identity_review_packet/v1'
APPROVAL_ALLOWLIST_SCHEMA_VERSION = 'station_external_identity_load_approval_allowlist/v1'
TOOL_NAME = 'station_external_identity_loader'
TOOL_VERSION = 'v1'
MAX_ROWS_CAP = 20
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
PLANNED_ROW_INSERT_COLUMNS = (
    'canonical_station_id',
    'system_id64',
    'station_name',
    'source',
    'market_id',
    'edsm_station_id',
    'source_run_key',
    'source_file_key',
    'source_record_hash',
    'source_updated_at',
    'confidence',
    'freshness_class',
    'identity_status',
    'conflict_reason',
)
FORBIDDEN_FLAG_ERROR = (
    'write/apply/import/reconciliation/summarizer/station-type flags are not available; '
    'use --dry-run or --write-reviewed only'
)


class IdentityLoaderError(ValueError):
    """Raised when a review packet cannot be loaded safely."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Validate or load reviewed external station identity rows.',
    )
    parser.add_argument('--review-packet', required=True, help='Local station_external_identity_review_packet/v1 JSON file.')
    parser.add_argument('--expected-review-packet-sha256', required=True, help='Expected SHA-256 of the review packet file.')
    parser.add_argument('--dsn', required=True, help='Database DSN. Dry-run mode accepts this but does not connect.')
    parser.add_argument('--max-rows', type=int, required=True, help=f'Maximum selected rows, maximum {MAX_ROWS_CAP}.')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true', help='Validate and emit a load execution plan without DB writes.')
    mode.add_argument('--write-reviewed', action='store_true', help='Write allowlisted reviewed rows to station_external_identity.')
    parser.add_argument('--output', required=True, help='Path to write the JSON execution plan.')
    parser.add_argument('--json', action='store_true', help='Emit JSON to stdout after writing output.')
    parser.add_argument(
        '--approved-review-items-file',
        default=None,
        help='Required for --write-reviewed. JSON allowlist of approved review_item_ids or plan_row_ids.',
    )
    parser.add_argument('--confirm-write-reviewed', action='store_true', help='Required confirmation for --write-reviewed.')
    parser.add_argument(
        '--confirm-station-external-identity-only',
        action='store_true',
        help='Required confirmation that only station_external_identity may be written.',
    )
    parser.add_argument(
        '--confirm-no-canonical-writes',
        action='store_true',
        help='Required confirmation that canonical writes and apply remain blocked.',
    )
    parser.add_argument('--apply', action='store_true', help='Unsupported; canonical apply is blocked.')
    parser.add_argument('--canonical-apply', action='store_true', help='Unsupported; canonical apply is blocked.')
    parser.add_argument('--station-type-dry-run', action='store_true', help='Unsupported; station-type dry-run is blocked.')
    parser.add_argument('--reconciliation', action='store_true', help='Unsupported; reconciliation is blocked.')
    parser.add_argument('--import', dest='run_import', action='store_true', help='Unsupported; imports are blocked.')
    parser.add_argument('--summarizer', action='store_true', help='Unsupported; summarizer runs are blocked.')
    parser.add_argument('--write', action='store_true', help='Unsupported alias; use --write-reviewed.')
    parser.add_argument('--commit', action='store_true', help='Unsupported; commits are not a loader mode.')
    args = parser.parse_args(argv)

    if (
        args.apply
        or args.canonical_apply
        or args.station_type_dry_run
        or args.reconciliation
        or args.run_import
        or args.summarizer
        or args.write
        or args.commit
    ):
        parser.error(FORBIDDEN_FLAG_ERROR)
    if args.max_rows < 1:
        parser.error('--max-rows must be >= 1')
    if args.max_rows > MAX_ROWS_CAP:
        parser.error(f'--max-rows must be <= {MAX_ROWS_CAP}')
    if args.write_reviewed:
        if not args.approved_review_items_file:
            parser.error('--write-reviewed requires --approved-review-items-file')
        missing_confirmations = [
            name
            for name, confirmed in (
                ('--confirm-write-reviewed', args.confirm_write_reviewed),
                ('--confirm-station-external-identity-only', args.confirm_station_external_identity_only),
                ('--confirm-no-canonical-writes', args.confirm_no_canonical_writes),
            )
            if not confirmed
        ]
        if missing_confirmations:
            parser.error('--write-reviewed requires ' + ', '.join(missing_confirmations))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.dry_run:
            artifact = build_execution_plan_from_files(
                review_packet_path=args.review_packet,
                expected_review_packet_sha256=args.expected_review_packet_sha256,
                max_rows=args.max_rows,
                dry_run=True,
                write_reviewed=False,
                approved_review_items_file=args.approved_review_items_file,
            )
        else:
            with connect_write_db(args.dsn) as conn:
                artifact = build_execution_plan_from_files(
                    review_packet_path=args.review_packet,
                    expected_review_packet_sha256=args.expected_review_packet_sha256,
                    max_rows=args.max_rows,
                    dry_run=False,
                    write_reviewed=True,
                    approved_review_items_file=args.approved_review_items_file,
                    conn=conn,
                )
    except (OSError, ValueError) as exc:
        print(f'station external identity loader failed: {exc}', file=sys.stderr)
        return 2

    output = json_dumps_artifact(artifact)
    Path(args.output).write_text(output + '\n', encoding='utf-8')
    if args.json:
        print(output)
    return 0


def connect_write_db(dsn: str):
    """Connect lazily so tests and dry-run imports do not require Postgres."""
    import psycopg  # noqa: PLC0415

    return psycopg.connect(dsn, autocommit=False)


def build_execution_plan_from_files(
    *,
    review_packet_path: str | Path,
    expected_review_packet_sha256: str,
    max_rows: int,
    dry_run: bool,
    write_reviewed: bool,
    approved_review_items_file: str | Path | None = None,
    conn: Any | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    review_packet, packet_sha256, packet_size = _load_json_file_with_sha(
        review_packet_path,
        expected_sha256=expected_review_packet_sha256,
        label='review packet',
    )
    approval_allowlist = None
    approval_allowlist_sha256 = None
    if approved_review_items_file:
        approval_allowlist, approval_allowlist_sha256, _approval_size = _load_json_file(
            approved_review_items_file,
            label='approval allowlist',
        )
    return build_execution_plan(
        review_packet,
        review_packet_basename=Path(review_packet_path).name,
        review_packet_sha256=packet_sha256,
        review_packet_size_bytes=packet_size,
        max_rows=max_rows,
        dry_run=dry_run,
        write_reviewed=write_reviewed,
        approval_allowlist=approval_allowlist,
        approval_allowlist_sha256=approval_allowlist_sha256,
        conn=conn,
        generated_at=generated_at,
    )


def build_execution_plan(
    review_packet: Mapping[str, Any],
    *,
    review_packet_basename: str,
    review_packet_sha256: str,
    review_packet_size_bytes: int,
    max_rows: int,
    dry_run: bool,
    write_reviewed: bool,
    approval_allowlist: Mapping[str, Any] | None = None,
    approval_allowlist_sha256: str | None = None,
    conn: Any | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if max_rows < 1:
        raise IdentityLoaderError('max_rows must be >= 1')
    if max_rows > MAX_ROWS_CAP:
        raise IdentityLoaderError(f'max_rows must be <= {MAX_ROWS_CAP}')
    if dry_run == write_reviewed:
        raise IdentityLoaderError('exactly one of dry_run or write_reviewed must be selected')

    approval = _validate_approval_allowlist(
        approval_allowlist,
        review_packet_sha256=review_packet_sha256,
        require_approval=write_reviewed,
    )
    selected_items = _select_valid_review_items(
        review_packet,
        max_rows=max_rows,
        approval=approval,
        require_approval=write_reviewed,
    )

    insert_results: list[dict[str, Any]] = []
    if write_reviewed:
        if conn is None:
            raise IdentityLoaderError('write_reviewed requires a database connection')
        insert_results = insert_reviewed_identity_rows(conn, selected_items)

    identity_rows_written = sum(1 for result in insert_results if result.get('inserted') is True)
    duplicate_rows_skipped = sum(1 for result in insert_results if result.get('inserted') is False)
    now = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    selected_review_item_ids = [str(item['review_item_id']) for item in selected_items]
    selected_plan_row_ids = [str(_mapping(item['planned_row']).get('plan_row_id')) for item in selected_items]

    artifact: dict[str, Any] = {
        'schema_version': SCHEMA_VERSION,
        'generated_at': now,
        'tool': {
            'name': TOOL_NAME,
            'version': TOOL_VERSION,
        },
        'dry_run': dry_run,
        'write_reviewed': write_reviewed,
        'canonical_writes_planned': 0,
        'station_type_writes_planned': 0,
        'identity_rows_selected': len(selected_items),
        'identity_rows_written': identity_rows_written,
        'approval_record_created': False,
        'max_rows': max_rows,
        'review_packet_basename': review_packet_basename,
        'review_packet_sha256': review_packet_sha256,
        'review_packet_size_bytes': review_packet_size_bytes,
        'selected_review_item_ids': selected_review_item_ids,
        'selected_plan_row_ids': selected_plan_row_ids,
        'validation_summary': {
            'review_packet_schema_valid': True,
            'review_packet_sha256_verified': True,
            'approval_allowlist_required': write_reviewed,
            'approval_allowlist_present': approval_allowlist is not None,
            'approval_allowlist_sha256': approval_allowlist_sha256,
            'manual_review_items_selected': len(selected_items),
            'all_required_checks_passed': True,
            'identity_status_confirmed': True,
            'conflict_reason_null': True,
            'source_provenance_present': True,
            'external_id_present': True,
            'canonical_writes_planned': 0,
            'station_type_writes_planned': 0,
            'identity_rows_written': identity_rows_written,
            'approval_record_created': False,
            'duplicate_rows_skipped': duplicate_rows_skipped,
        },
        'refusal_reasons': [],
        'inserted_row_ids': [result['row_id'] for result in insert_results if result.get('inserted') is True],
        'insert_results': insert_results,
    }
    if approval is not None:
        artifact['approval_allowlist'] = {
            'schema_version': approval['schema_version'],
            'review_packet_sha256': approval['review_packet_sha256'],
            'approved_review_item_ids_count': len(approval['approved_review_item_ids']),
            'approved_plan_row_ids_count': len(approval['approved_plan_row_ids']),
            'reviewer': approval['reviewer'],
            'reviewed_at': approval['reviewed_at'],
            'declaration': approval['declaration'],
            'artifact_sha256': approval_allowlist_sha256,
        }
    artifact['artifact_integrity'] = {
        'hash_algorithm': 'sha256',
        'canonical_json_sha256': artifact_sha256(artifact),
        'canonicalization': 'json.dumps(sort_keys=True,separators=(comma,colon),ensure_ascii=True,allow_nan=False) excluding artifact_integrity',
    }
    return _json_clone(artifact)


def insert_reviewed_identity_rows(conn: Any, selected_items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    sql = _identity_insert_sql()
    _assert_identity_insert_sql_is_safe(sql)
    cur = conn.cursor()
    results: list[dict[str, Any]] = []
    try:
        for item in selected_items:
            row = _mapping(item.get('planned_row'))
            cur.execute(sql, _insert_params(row))
            returned = cur.fetchone()
            inserted = returned is not None
            row_id = returned[0] if inserted else None
            results.append({
                'review_item_id': item.get('review_item_id'),
                'plan_row_id': row.get('plan_row_id'),
                'inserted': inserted,
                'row_id': row_id,
            })
        commit = getattr(conn, 'commit', None)
        if callable(commit):
            commit()
    except Exception:
        rollback = getattr(conn, 'rollback', None)
        if callable(rollback):
            rollback()
        raise
    finally:
        close = getattr(cur, 'close', None)
        if callable(close):
            close()
    return results


def json_dumps_artifact(artifact: Mapping[str, Any]) -> str:
    return canonical_json(artifact)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def artifact_sha256(value: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(value))
    payload.pop('artifact_integrity', None)
    return hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()


def _load_json_file_with_sha(path_value: str | Path, *, expected_sha256: str, label: str) -> tuple[dict[str, Any], str, int]:
    payload, actual_sha256, size_bytes = _read_file_with_sha(path_value, label=label)
    if actual_sha256 != expected_sha256.lower():
        raise IdentityLoaderError(f'{label} checksum mismatch. Expected {expected_sha256}, got {actual_sha256}')
    return _parse_json_object(payload, label=label), actual_sha256, size_bytes


def _load_json_file(path_value: str | Path, *, label: str) -> tuple[dict[str, Any], str, int]:
    payload, actual_sha256, size_bytes = _read_file_with_sha(path_value, label=label)
    return _parse_json_object(payload, label=label), actual_sha256, size_bytes


def _read_file_with_sha(path_value: str | Path, *, label: str) -> tuple[bytes, str, int]:
    path = Path(path_value)
    if not path.is_file():
        raise IdentityLoaderError(f'{label} is missing: {path}')
    payload = path.read_bytes()
    return payload, hashlib.sha256(payload).hexdigest(), len(payload)


def _parse_json_object(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        data = json.loads(payload.decode('utf-8'))
    except json.JSONDecodeError as exc:
        raise IdentityLoaderError(f'{label} is not valid JSON: {exc}') from exc
    if not isinstance(data, dict):
        raise IdentityLoaderError(f'{label} must be a JSON object')
    return data


def _select_valid_review_items(
    review_packet: Mapping[str, Any],
    *,
    max_rows: int,
    approval: Mapping[str, Any] | None,
    require_approval: bool,
) -> list[dict[str, Any]]:
    _validate_review_packet_safety(review_packet)
    items = review_packet.get('manual_review_items')
    if not isinstance(items, list):
        raise IdentityLoaderError('review packet manual_review_items must be a list')
    validated_items = [_validate_review_item(item_value) for item_value in items]
    planned_rows = review_packet.get('planned_rows')
    if planned_rows is not None:
        embedded_rows = [
            item['planned_row']
            for item in validated_items
        ]
        if embedded_rows != planned_rows:
            raise IdentityLoaderError('embedded manual review rows must match top-level planned_rows')

    selected: list[dict[str, Any]] = []
    approved_review_ids = set(approval.get('approved_review_item_ids', ())) if approval is not None else set()
    approved_plan_row_ids = set(approval.get('approved_plan_row_ids', ())) if approval is not None else set()
    for item in validated_items:
        row = _mapping(item['planned_row'])
        approved = item['review_item_id'] in approved_review_ids or row.get('plan_row_id') in approved_plan_row_ids
        if require_approval and not approved:
            continue
        selected.append(item)

    if not require_approval:
        selected = selected[:max_rows]
    if len(selected) > max_rows:
        raise IdentityLoaderError('selected row count exceeds max_rows')
    if require_approval and not selected:
        raise IdentityLoaderError('approval allowlist selected zero review rows')
    return selected


def _validate_review_packet_safety(review_packet: Mapping[str, Any]) -> None:
    if review_packet.get('schema_version') != REVIEW_PACKET_SCHEMA_VERSION:
        raise IdentityLoaderError(f'review packet schema_version must be {REVIEW_PACKET_SCHEMA_VERSION}')
    if review_packet.get('identity_rows_written') != 0:
        raise IdentityLoaderError('review packet identity_rows_written must be 0')
    if review_packet.get('canonical_writes_planned') != 0:
        raise IdentityLoaderError('review packet canonical_writes_planned must be 0')
    if review_packet.get('station_type_writes_planned') != 0:
        raise IdentityLoaderError('review packet station_type_writes_planned must be 0')
    if review_packet.get('approval_record_created') is True:
        raise IdentityLoaderError('review packet approval_record_created must be false')
    summary = _mapping(review_packet.get('summary'))
    for key in ('canonical_writes_planned', 'station_type_writes_planned', 'identity_rows_written'):
        value = summary.get(key)
        if value is not None and value != 0:
            raise IdentityLoaderError(f'review packet summary {key} must be 0')
    if summary.get('approval_record_created') is True:
        raise IdentityLoaderError('review packet summary approval_record_created must be false')


def _validate_review_item(item_value: Any) -> dict[str, Any]:
    if not isinstance(item_value, Mapping):
        raise IdentityLoaderError('manual review item must be an object')
    item = dict(item_value)
    if not item.get('review_item_id'):
        raise IdentityLoaderError('manual review item is missing review_item_id')
    row = item.get('planned_row')
    if not isinstance(row, Mapping) or not row:
        raise IdentityLoaderError('manual review item is missing planned_row')
    checks = item.get('checks')
    if not isinstance(checks, Mapping) or not checks:
        raise IdentityLoaderError('manual review item is missing checks')
    failed_checks = [
        key
        for key in REQUIRED_CHECK_KEYS
        if checks.get(key) is not True
    ]
    if failed_checks:
        raise IdentityLoaderError('manual review item has failed required checks: ' + ', '.join(failed_checks))
    if row.get('identity_status') != 'confirmed':
        raise IdentityLoaderError('planned row identity_status must be confirmed')
    if row.get('conflict_reason') is not None:
        raise IdentityLoaderError('planned row conflict_reason must be null')
    for field in ('source_run_key', 'source_file_key', 'source_record_hash'):
        if not row.get(field):
            raise IdentityLoaderError(f'planned row is missing source provenance field: {field}')
    if row.get('market_id') is None and row.get('edsm_station_id') is None:
        raise IdentityLoaderError('planned row must have market_id or edsm_station_id')
    if row.get('station_type_writes_planned') != 0:
        raise IdentityLoaderError('planned row station_type_writes_planned must be 0')
    if row.get('canonical_writes_planned') != 0:
        raise IdentityLoaderError('planned row canonical_writes_planned must be 0')
    item['planned_row'] = _json_clone(row)
    item['checks'] = _json_clone(checks)
    return item


def _validate_approval_allowlist(
    approval_allowlist: Mapping[str, Any] | None,
    *,
    review_packet_sha256: str,
    require_approval: bool,
) -> dict[str, Any] | None:
    if approval_allowlist is None:
        if require_approval:
            raise IdentityLoaderError('write-reviewed requires an approval allowlist')
        return None
    if approval_allowlist.get('schema_version') != APPROVAL_ALLOWLIST_SCHEMA_VERSION:
        raise IdentityLoaderError(f'approval allowlist schema_version must be {APPROVAL_ALLOWLIST_SCHEMA_VERSION}')
    if approval_allowlist.get('review_packet_sha256') != review_packet_sha256:
        raise IdentityLoaderError('approval allowlist review_packet_sha256 does not match review packet')

    approved_review_item_ids = _string_list(approval_allowlist.get('approved_review_item_ids'), 'approved_review_item_ids')
    approved_plan_row_ids = _string_list(approval_allowlist.get('approved_plan_row_ids'), 'approved_plan_row_ids')
    if not approved_review_item_ids and not approved_plan_row_ids:
        raise IdentityLoaderError('approval allowlist must include approved review item ids or plan row ids')

    reviewer = _required_string(approval_allowlist, 'reviewer')
    reviewed_at = _required_string(approval_allowlist, 'reviewed_at')
    declaration = _required_string(approval_allowlist, 'declaration')
    return {
        'schema_version': APPROVAL_ALLOWLIST_SCHEMA_VERSION,
        'review_packet_sha256': review_packet_sha256,
        'approved_review_item_ids': approved_review_item_ids,
        'approved_plan_row_ids': approved_plan_row_ids,
        'reviewer': reviewer,
        'reviewed_at': reviewed_at,
        'declaration': declaration,
    }


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise IdentityLoaderError(f'approval allowlist {field_name} must be a list of non-empty strings')
    return list(value)


def _required_string(value: Mapping[str, Any], field_name: str) -> str:
    field = value.get(field_name)
    if not isinstance(field, str) or not field.strip():
        raise IdentityLoaderError(f'approval allowlist {field_name} must be a non-empty string')
    return field


def _identity_insert_sql() -> str:
    return """
        INSERT INTO station_external_identity (
            canonical_station_id,
            system_id64,
            station_name,
            source,
            market_id,
            edsm_station_id,
            source_run_key,
            source_file_key,
            source_record_hash,
            source_updated_at,
            confidence,
            freshness_class,
            identity_status,
            conflict_reason
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING id
    """


def _assert_identity_insert_sql_is_safe(sql: str) -> None:
    text = _strip_sql_comments(sql).upper()
    if re.search(r'\b(UPDATE|DELETE|MERGE|TRUNCATE|DROP|ALTER)\b', text):
        raise IdentityLoaderError('identity loader SQL must not contain unsafe write or DDL keywords')
    if not re.search(r'\bINSERT\s+INTO\s+STATION_EXTERNAL_IDENTITY\b', text):
        raise IdentityLoaderError('identity loader SQL may only insert into station_external_identity')
    forbidden_tables = ('SYSTEMS', 'STATIONS', 'BODIES', 'BODY_RINGS', 'BODY_SCAN_FACTS', 'STATION_BODY_LINKS')
    for table in forbidden_tables:
        if re.search(rf'\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO|TRUNCATE|DROP\s+TABLE|ALTER\s+TABLE)\s+{table}\b', text):
            raise IdentityLoaderError(f'identity loader SQL must not write canonical table {table.lower()}')
    if 'STATION_TYPE' in text:
        raise IdentityLoaderError('identity loader SQL must not reference station_type')


def _insert_params(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(column) for column in PLANNED_ROW_INSERT_COLUMNS)


def _strip_sql_comments(sql: str) -> str:
    without_line_comments = re.sub(r'--.*?$', '', sql, flags=re.MULTILINE)
    return re.sub(r'/\*.*?\*/', '', without_line_comments, flags=re.DOTALL)


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _json_clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


if __name__ == '__main__':
    raise SystemExit(main())
