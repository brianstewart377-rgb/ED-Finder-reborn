#!/usr/bin/env python3
"""Bounded no-write external station identity load-plan artifact generator.

This tool reads staged EDSM station evidence through the Stage 18J-P9
candidate matcher and produces reviewable ``station_external_identity`` insert
plans. It never writes to ``station_external_identity`` or canonical tables.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from enrichment_staging import json_safe_value

import station_external_identity_candidates as candidates


SCHEMA_VERSION = 'station_external_identity_load_plan/v1'
TOOL_NAME = 'station_external_identity_load_plan'
TOOL_VERSION = 'v1'
DEFAULT_SAMPLE_LIMIT = 20
MAX_ROWS_CAP = 20
WRITE_FLAG_ERROR = 'write/apply/load flags are not available; this tool only emits a bounded no-write load plan'


class IdentityLoadPlanError(ValueError):
    """Raised when an identity load-plan artifact cannot be built safely."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Build a bounded no-write station_external_identity load-plan artifact.',
    )
    parser.add_argument('--dsn', required=True, help='Read-only warehouse/staging Postgres DSN.')
    parser.add_argument('--source-run-key', required=True, help='Required staged source run key filter.')
    parser.add_argument('--source-file-key', default=None, help='Optional staged source file key filter.')
    parser.add_argument('--max-rows', type=int, required=True, help=f'Required planned row cap, maximum {MAX_ROWS_CAP}.')
    parser.add_argument(
        '--sample-limit',
        type=int,
        default=DEFAULT_SAMPLE_LIMIT,
        help='Maximum rejected/conflicting samples to include in the artifact.',
    )
    parser.add_argument(
        '--input-candidate-artifact-sha256',
        default=None,
        help='Optional SHA-256 of the reviewed P9 candidate artifact.',
    )
    parser.add_argument('--json', action='store_true', help='Emit JSON to stdout. Output is JSON by default.')
    parser.add_argument('--output', default=None, help='Optional path to write the JSON artifact.')
    parser.add_argument('--apply', action='store_true', help='Unsupported; this tool is read-only.')
    parser.add_argument('--write', action='store_true', help='Unsupported; this tool is read-only.')
    parser.add_argument('--write-staging', action='store_true', help='Unsupported; this tool is read-only.')
    parser.add_argument('--commit', action='store_true', help='Unsupported; this tool is read-only.')
    parser.add_argument('--load', action='store_true', help='Unsupported; this tool is read-only.')
    args = parser.parse_args(argv)

    if args.apply or args.write or args.write_staging or args.commit or args.load:
        parser.error(WRITE_FLAG_ERROR)
    if args.max_rows < 1:
        parser.error('--max-rows must be >= 1')
    if args.max_rows > MAX_ROWS_CAP:
        parser.error(f'--max-rows must be <= {MAX_ROWS_CAP} for the first bounded load-plan stage')
    if args.sample_limit < 0:
        parser.error('--sample-limit must be >= 0')
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        with candidates.connect_read_only_db(args.dsn) as conn:
            artifact = build_load_plan_artifact_from_db(
                conn,
                source_run_key=args.source_run_key,
                source_file_key=args.source_file_key,
                max_rows=args.max_rows,
                sample_limit=args.sample_limit,
                input_candidate_artifact_sha256=args.input_candidate_artifact_sha256,
            )
    except (OSError, ValueError) as exc:
        print(f'station external identity load-plan generation failed: {exc}', file=sys.stderr)
        return 2

    output = json_dumps_artifact(artifact)
    if args.output:
        Path(args.output).write_text(output + '\n', encoding='utf-8')
    if args.json or not args.output:
        print(output)
    return 0


def build_load_plan_artifact_from_db(
    conn: Any,
    *,
    source_run_key: str,
    source_file_key: str | None = None,
    max_rows: int,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    input_candidate_artifact_sha256: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    rows = candidates.fetch_candidate_rows(
        conn,
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        limit=None,
    )
    return build_load_plan_artifact_from_rows(
        rows,
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        max_rows=max_rows,
        sample_limit=sample_limit,
        input_candidate_artifact_sha256=input_candidate_artifact_sha256,
        generated_at=generated_at,
    )


def build_load_plan_artifact_from_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_run_key: str,
    source_file_key: str | None = None,
    max_rows: int,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    input_candidate_artifact_sha256: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if max_rows < 1:
        raise IdentityLoadPlanError('max_rows must be >= 1')
    if max_rows > MAX_ROWS_CAP:
        raise IdentityLoadPlanError(f'max_rows must be <= {MAX_ROWS_CAP}')
    if sample_limit < 0:
        raise IdentityLoadPlanError('sample_limit must be >= 0')

    candidate_rows = [
        candidates.build_candidate(group)
        for group in candidates._group_by_staging_station(rows)  # noqa: SLF001 - reuse P9 grouping contract.
    ]
    candidate_rows = sorted(candidate_rows, key=canonical_json)

    status_counts = Counter({status: 0 for status in candidates.CANDIDATE_STATUSES})
    skipped_reason_counts: Counter[str] = Counter()
    rejected_reason_counts: Counter[str] = Counter()
    conflicting_reason_counts: Counter[str] = Counter()
    eligible_confirmed_candidates_seen = 0
    planned_rows: list[dict[str, Any]] = []
    sample_rejected_candidates: list[dict[str, Any]] = []
    sample_conflicting_candidates: list[dict[str, Any]] = []

    for candidate in candidate_rows:
        status = str(candidate.get('candidate_status'))
        status_counts[status] += 1
        skip_reason = _skip_reason(candidate)
        if skip_reason is None:
            eligible_confirmed_candidates_seen += 1
            if len(planned_rows) < max_rows:
                planned_rows.append(_planned_row(candidate))
            else:
                skipped_reason_counts['eligible_beyond_max_rows'] += 1
            continue

        skipped_reason_counts[skip_reason] += 1
        if status == 'rejected':
            rejected_reason_counts[skip_reason] += 1
            if len(sample_rejected_candidates) < sample_limit:
                sample_rejected_candidates.append(_sample_candidate(candidate, reason=skip_reason))
        elif status == 'conflicting':
            conflicting_reason_counts[skip_reason] += 1
            if len(sample_conflicting_candidates) < sample_limit:
                sample_conflicting_candidates.append(_sample_candidate(candidate, reason=skip_reason))

    now = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    artifact: dict[str, Any] = {
        'schema_version': SCHEMA_VERSION,
        'generated_at': now,
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
        'max_rows': max_rows,
        'filters': {
            'source': candidates.SOURCE,
            'source_run_key': source_run_key,
            'source_file_key': source_file_key,
            'sample_limit': sample_limit,
        },
        'input_candidate_artifact_sha256': input_candidate_artifact_sha256,
        'summary': {
            'total_candidates_seen': len(candidate_rows),
            'eligible_confirmed_candidates_seen': eligible_confirmed_candidates_seen,
            'planned_rows_count': len(planned_rows),
            'candidate_status_counts': _ordered_counts(status_counts, candidates.CANDIDATE_STATUSES),
            'skipped_reason_counts': dict(sorted(skipped_reason_counts.items())),
            'rejected_reason_counts': dict(sorted(rejected_reason_counts.items())),
            'conflicting_reason_counts': dict(sorted(conflicting_reason_counts.items())),
            'sample_rejected_candidates_included': len(sample_rejected_candidates),
            'sample_conflicting_candidates_included': len(sample_conflicting_candidates),
            'canonical_writes_planned': 0,
            'station_type_writes_planned': 0,
            'identity_rows_written': 0,
        },
        'planned_db_defaults': {
            'evidence_first_seen_at': 'DEFAULT now()',
            'evidence_last_seen_at': 'DEFAULT now()',
            'created_at': 'DEFAULT now()',
            'updated_at': 'DEFAULT now()',
        },
        'planned_rows': planned_rows,
        'sample_rejected_candidates': sample_rejected_candidates,
        'sample_conflicting_candidates': sample_conflicting_candidates,
    }
    artifact['artifact_integrity'] = {
        'hash_algorithm': 'sha256',
        'canonical_json_sha256': artifact_sha256(artifact),
        'canonicalization': 'json.dumps(sort_keys=True,separators=(comma,colon),ensure_ascii=True,allow_nan=False) excluding artifact_integrity',
    }
    return json_safe_value(artifact)


def json_dumps_artifact(artifact: Mapping[str, Any]) -> str:
    return canonical_json(json_safe_value(artifact))


def canonical_json(value: Any) -> str:
    return json.dumps(json_safe_value(value), sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def artifact_sha256(value: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(value))
    payload.pop('artifact_integrity', None)
    return hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()


def _skip_reason(candidate: Mapping[str, Any]) -> str | None:
    status = str(candidate.get('candidate_status'))
    if status == 'rejected':
        return str(candidate.get('rejection_reason') or 'rejected_candidate')
    if status == 'conflicting':
        return str(candidate.get('conflict_reason') or 'conflicting_candidate')
    if status == 'proposed':
        return 'proposed_candidate_not_planned'
    if status != 'confirmed_candidate':
        return f'{status}_candidate_not_planned'
    if candidate.get('match_basis') != 'system_id64_normalized_station_name':
        return 'unsupported_match_basis'

    source = _mapping(candidate.get('source_identity'))
    canonical_match = _mapping(candidate.get('canonical_match'))
    match_proof = _mapping(candidate.get('match_proof'))
    if int(match_proof.get('canonical_station_match_count') or 0) != 1:
        return 'not_exactly_one_canonical_station_match'
    if not canonical_match.get('canonical_station_id'):
        return 'missing_canonical_station_match'
    if source.get('market_id') is None and source.get('edsm_station_id') is None:
        return 'missing_external_station_id'
    if not all(source.get(field) for field in ('source_run_key', 'source_file_key', 'source_record_hash')):
        return 'missing_source_provenance'
    return None


def _planned_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(candidate.get('source_identity'))
    canonical_match = _mapping(candidate.get('canonical_match'))
    row = {
        'plan_row_id': _plan_row_id(candidate),
        'candidate_id': candidate.get('candidate_id'),
        'canonical_station_id': canonical_match.get('canonical_station_id'),
        'system_id64': source.get('system_id64'),
        'station_name': source.get('station_name'),
        'source': source.get('source'),
        'market_id': source.get('market_id'),
        'edsm_station_id': source.get('edsm_station_id'),
        'source_run_key': source.get('source_run_key'),
        'source_file_key': source.get('source_file_key'),
        'source_record_hash': source.get('source_record_hash'),
        'source_updated_at': source.get('source_updated_at'),
        'confidence': source.get('confidence'),
        'freshness_class': source.get('freshness_class'),
        'identity_status': 'confirmed',
        'conflict_reason': None,
        'match_basis': candidate.get('match_basis'),
        'canonical_writes_planned': 0,
        'station_type_writes_planned': 0,
        'identity_rows_written': 0,
    }
    return json_safe_value(row)


def _sample_candidate(candidate: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    return json_safe_value({
        'candidate_id': candidate.get('candidate_id'),
        'candidate_status': candidate.get('candidate_status'),
        'proposed_identity_status': candidate.get('proposed_identity_status'),
        'skip_reason': reason,
        'match_basis': candidate.get('match_basis'),
        'conflict_reason': candidate.get('conflict_reason'),
        'rejection_reason': candidate.get('rejection_reason'),
        'source_identity': candidate.get('source_identity'),
        'canonical_match': candidate.get('canonical_match'),
        'canonical_matches': candidate.get('canonical_matches'),
        'match_proof': candidate.get('match_proof'),
    })


def _plan_row_id(candidate: Mapping[str, Any]) -> str:
    row = {
        'candidate_id': candidate.get('candidate_id'),
        'source_identity': candidate.get('source_identity'),
        'canonical_match': candidate.get('canonical_match'),
        'identity_status': 'confirmed',
    }
    return hashlib.sha256(canonical_json(['station_external_identity_load_plan_row', row]).encode('utf-8')).hexdigest()


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _ordered_counts(counts: Mapping[str, int], keys: Sequence[str]) -> dict[str, int]:
    return {key: int(counts.get(key, 0)) for key in keys}


if __name__ == '__main__':
    raise SystemExit(main())
