#!/usr/bin/env python3
"""Read-only external station identity candidate artifact generator.

This tool reads staged EDSM station evidence and produces deterministic JSON
candidate rows for later review. It never writes to ``station_external_identity``
or any canonical table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from enrichment_staging import json_safe_value
from enrichment_warehouse import assert_reconciliation_sql_is_read_only


SCHEMA_VERSION = 'station_external_identity_candidates/v1'
TOOL_NAME = 'station_external_identity_candidates'
TOOL_VERSION = 'v1'
DEFAULT_SAMPLE_LIMIT = 100
SOURCE = 'edsm_nightly_stations'
CONFIRMED_CANDIDATE_CONFIDENCE = {'exact_station_identity', 'source_station_snapshot', 'high'}
CONFIRMED_CANDIDATE_FRESHNESS = {'source_updated_at', 'file_snapshot', 'current', 'recent'}
CANDIDATE_STATUSES = ('proposed', 'confirmed_candidate', 'conflicting', 'rejected')


class IdentityCandidateError(ValueError):
    """Raised when an identity candidate artifact cannot be built safely."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Build a read-only external station identity candidate artifact from staged EDSM station evidence.',
    )
    parser.add_argument('--dsn', required=True, help='Read-only warehouse/staging Postgres DSN.')
    parser.add_argument('--source-run-key', required=True, help='Required staged source run key filter.')
    parser.add_argument('--source-file-key', default=None, help='Optional staged source file key filter.')
    parser.add_argument('--limit', type=int, default=None, help='Maximum staged station rows to inspect.')
    parser.add_argument(
        '--sample-limit',
        type=int,
        default=DEFAULT_SAMPLE_LIMIT,
        help='Maximum sample candidates to include in the artifact.',
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
        parser.error('write/apply/load flags are not available; this tool only emits a read-only candidate artifact')
    if args.limit is not None and args.limit < 0:
        parser.error('--limit must be >= 0')
    if args.sample_limit < 0:
        parser.error('--sample-limit must be >= 0')
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        with connect_read_only_db(args.dsn) as conn:
            artifact = build_candidate_artifact_from_db(
                conn,
                source_run_key=args.source_run_key,
                source_file_key=args.source_file_key,
                limit=args.limit,
                sample_limit=args.sample_limit,
            )
    except (OSError, ValueError) as exc:
        print(f'station external identity candidate generation failed: {exc}', file=sys.stderr)
        return 2

    output = json_dumps_artifact(artifact)
    if args.output:
        Path(args.output).write_text(output + '\n', encoding='utf-8')
    if args.json or not args.output:
        print(output)
    return 0


def connect_read_only_db(dsn: str):
    """Connect lazily so tests and imports do not require Postgres."""
    import psycopg  # noqa: PLC0415

    conn = psycopg.connect(dsn, autocommit=False)
    conn.read_only = True
    return conn


def build_candidate_artifact_from_db(
    conn: Any,
    *,
    source_run_key: str,
    source_file_key: str | None = None,
    limit: int | None = None,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    generated_at: str | None = None,
) -> dict[str, Any]:
    rows = fetch_candidate_rows(
        conn,
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        limit=limit,
    )
    return build_candidate_artifact_from_rows(
        rows,
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        limit=limit,
        sample_limit=sample_limit,
        generated_at=generated_at,
    )


def fetch_candidate_rows(
    conn: Any,
    *,
    source_run_key: str,
    source_file_key: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    sql, params = candidate_rows_query(
        source_run_key=source_run_key,
        source_file_key=source_file_key,
        limit=limit,
    )
    assert_reconciliation_sql_is_read_only(sql)
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return _fetchall_dicts(cur)
    finally:
        close = getattr(cur, 'close', None)
        if callable(close):
            close()


def candidate_rows_query(
    *,
    source_run_key: str,
    source_file_key: str | None,
    limit: int | None,
) -> tuple[str, list[Any]]:
    limit_clause = 'LIMIT %s' if limit is not None else ''
    params: list[Any] = [SOURCE, source_run_key, source_file_key, source_file_key]
    if limit is not None:
        params.append(limit)
    return (
        f"""
        WITH staged AS (
            SELECT
                ss.id AS staging_station_id,
                ss.source_record_key,
                ss.source_record_hash,
                ss.system_id64,
                ss.system_name,
                ss.market_id,
                ss.edsm_station_id,
                ss.station_name,
                ss.source_class,
                ss.confidence,
                ss.freshness_class,
                ss.source_updated_at,
                sr.source_run_key,
                sr.source,
                sf.source_file_key
            FROM staging_edsm_stations ss
            JOIN enrichment_source_runs sr ON sr.id = ss.source_run_id
            LEFT JOIN enrichment_source_files sf ON sf.id = ss.source_file_id
            WHERE sr.source = %s
              AND sr.source_run_key = %s
              AND (%s IS NULL OR sf.source_file_key = %s)
            ORDER BY ss.system_id64 NULLS LAST, ss.station_name NULLS LAST, ss.id
            {limit_clause}
        )
        SELECT
            staged.*,
            sys.id64 AS canonical_system_id64,
            sys.name AS canonical_system_name,
            st.id AS canonical_station_id,
            st.name AS canonical_station_name,
            st.station_type AS canonical_station_type,
            COUNT(st.id) OVER (PARTITION BY staged.staging_station_id)::integer AS canonical_station_match_count
        FROM staged
        LEFT JOIN systems sys
          ON staged.system_id64 IS NOT NULL
         AND sys.id64 = staged.system_id64
        LEFT JOIN stations st
          ON st.system_id64 = sys.id64
         AND staged.station_name IS NOT NULL
         AND lower(btrim(st.name)) = lower(btrim(staged.station_name))
        ORDER BY staged.system_id64 NULLS LAST, staged.station_name NULLS LAST,
                 staged.staging_station_id, st.id NULLS LAST
        """,
        params,
    )


def build_candidate_artifact_from_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_run_key: str,
    source_file_key: str | None = None,
    limit: int | None = None,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if limit is not None and limit < 0:
        raise IdentityCandidateError('limit must be >= 0')
    if sample_limit < 0:
        raise IdentityCandidateError('sample_limit must be >= 0')

    groups = _group_by_staging_station(rows)
    candidates: list[dict[str, Any]] = []
    status_counts = Counter({status: 0 for status in CANDIDATE_STATUSES})
    conflict_reason_counts: Counter[str] = Counter()
    match_basis_counts: Counter[str] = Counter()
    source_identity_coverage = Counter({
        'source_market_id_present': 0,
        'source_edsm_station_id_present': 0,
        'source_station_name_present': 0,
        'source_system_id64_present': 0,
        'source_market_id_missing': 0,
        'source_edsm_station_id_missing': 0,
        'source_station_name_missing': 0,
        'source_system_id64_missing': 0,
    })
    canonical_match_coverage = Counter({
        'canonical_station_match_count_0': 0,
        'canonical_station_match_count_1': 0,
        'canonical_station_match_count_multiple': 0,
    })

    for group in groups:
        candidate = build_candidate(group)
        candidates.append(candidate)
        status = str(candidate['candidate_status'])
        status_counts[status] += 1
        match_basis_counts[str(candidate['match_basis'])] += 1
        if status == 'conflicting':
            conflict_reason_counts[str(candidate['conflict_reason'])] += 1
        _record_source_coverage(source_identity_coverage, candidate)
        match_count = int(candidate['match_proof']['canonical_station_match_count'])
        if match_count == 0:
            canonical_match_coverage['canonical_station_match_count_0'] += 1
        elif match_count == 1:
            canonical_match_coverage['canonical_station_match_count_1'] += 1
        else:
            canonical_match_coverage['canonical_station_match_count_multiple'] += 1

    candidates = sorted(candidates, key=canonical_json)
    sampled = candidates[:sample_limit]
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
        'identity_rows_written': 0,
        'filters': {
            'source': SOURCE,
            'source_run_key': source_run_key,
            'source_file_key': source_file_key,
            'limit': limit,
            'sample_limit': sample_limit,
        },
        'summary': {
            'total_staged_rows_inspected': len(groups),
            'sample_candidates_included': len(sampled),
            'candidate_status_counts': _ordered_counts(status_counts, CANDIDATE_STATUSES),
            'conflict_reason_counts': dict(sorted(conflict_reason_counts.items())),
            'match_basis_counts': dict(sorted(match_basis_counts.items())),
            'source_identity_coverage': _ordered_counts(
                source_identity_coverage,
                (
                    'source_market_id_present',
                    'source_edsm_station_id_present',
                    'source_station_name_present',
                    'source_system_id64_present',
                    'source_market_id_missing',
                    'source_edsm_station_id_missing',
                    'source_station_name_missing',
                    'source_system_id64_missing',
                ),
            ),
            'canonical_match_coverage': _ordered_counts(
                canonical_match_coverage,
                (
                    'canonical_station_match_count_0',
                    'canonical_station_match_count_1',
                    'canonical_station_match_count_multiple',
                ),
            ),
            'canonical_writes_planned': 0,
            'station_type_writes_planned': 0,
            'identity_rows_written': 0,
        },
        'sample_candidates': sampled,
    }
    artifact['artifact_integrity'] = {
        'hash_algorithm': 'sha256',
        'canonical_json_sha256': artifact_sha256(artifact),
        'canonicalization': 'json.dumps(sort_keys=True,separators=(comma,colon),ensure_ascii=True,allow_nan=False) excluding artifact_integrity',
    }
    return json_safe_value(artifact)


def build_candidate(group: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not group:
        raise IdentityCandidateError('candidate group is empty')
    first = dict(group[0])
    canonical_matches = _canonical_matches(group)
    match_count = len(canonical_matches)
    source = _source_identity(first)
    normalized_source_name = _normalise_name(source.get('station_name'))
    external_ids_present = source.get('market_id') is not None or source.get('edsm_station_id') is not None
    provenance_complete = all(source.get(field) for field in ('source_run_key', 'source_file_key', 'source_record_hash'))

    status = 'proposed'
    conflict_reason = None
    rejection_reason = None
    if not external_ids_present:
        status = 'rejected'
        rejection_reason = 'missing_external_station_id'
        match_basis = 'missing_external_station_id'
    elif not provenance_complete:
        status = 'rejected'
        rejection_reason = 'missing_source_provenance'
        match_basis = 'missing_source_provenance'
    elif source.get('system_id64') is None:
        status = 'rejected'
        rejection_reason = 'missing_system_id64'
        match_basis = 'missing_system_id64'
    elif normalized_source_name is None:
        status = 'rejected'
        rejection_reason = 'missing_station_name'
        match_basis = 'missing_station_name'
    elif match_count == 0:
        status = 'rejected'
        rejection_reason = 'source_only_no_canonical_station_match'
        match_basis = 'no_canonical_station_match'
    elif match_count > 1:
        status = 'conflicting'
        conflict_reason = 'ambiguous_canonical_station_match'
        match_basis = 'ambiguous_system_id64_normalized_station_name'
    else:
        canonical = canonical_matches[0]
        normalized_canonical_name = _normalise_name(canonical.get('station_name'))
        if _read_int(source.get('system_id64')) != _read_int(canonical.get('system_id64')):
            status = 'conflicting'
            conflict_reason = 'system_id64_mismatch'
            match_basis = 'system_id64_mismatch'
        elif normalized_source_name != normalized_canonical_name:
            status = 'conflicting'
            conflict_reason = 'station_name_mismatch'
            match_basis = 'station_name_mismatch'
        elif _confirmed_candidate_source_quality(source):
            status = 'confirmed_candidate'
            match_basis = 'system_id64_normalized_station_name'
        else:
            status = 'proposed'
            match_basis = 'system_id64_normalized_station_name_needs_review'

    canonical_match = canonical_matches[0] if len(canonical_matches) == 1 else None
    candidate = {
        'candidate_id': _candidate_id(source, canonical_match),
        'candidate_status': status,
        'proposed_identity_status': _proposed_identity_status(status),
        'match_basis': match_basis,
        'source_identity': source,
        'canonical_match': canonical_match,
        'canonical_matches': canonical_matches,
        'match_proof': {
            'required_match_basis': 'system_id64_normalized_station_name',
            'canonical_station_match_count': match_count,
            'normalised_source_station_name': normalized_source_name,
            'external_identity_proof_required_for_station_type': True,
            'internal_station_id_not_used_as_external_proof': True,
            'station_body_links_not_used_as_identity_proof': True,
        },
        'canonical_writes_planned': 0,
        'station_type_writes_planned': 0,
        'identity_rows_written': 0,
    }
    if conflict_reason is not None:
        candidate['conflict_reason'] = conflict_reason
    if rejection_reason is not None:
        candidate['rejection_reason'] = rejection_reason
    return json_safe_value(candidate)


def json_dumps_artifact(artifact: Mapping[str, Any]) -> str:
    return canonical_json(json_safe_value(artifact))


def canonical_json(value: Any) -> str:
    return json.dumps(json_safe_value(value), sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def artifact_sha256(value: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(value))
    payload.pop('artifact_integrity', None)
    return hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()


def _fetchall_dicts(cur: Any) -> list[dict[str, Any]]:
    rows = cur.fetchall()
    if not rows:
        return []
    first = rows[0]
    if isinstance(first, Mapping):
        return [dict(row) for row in rows]
    description = getattr(cur, 'description', None)
    if not description:
        raise IdentityCandidateError('cursor returned positional rows without column descriptions')
    columns = [str(item[0]) for item in description]
    return [dict(zip(columns, row, strict=False)) for row in rows]


def _group_by_staging_station(rows: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for index, row in enumerate(rows):
        key = row.get('staging_station_id')
        if key is None:
            key = f'row:{index}'
        grouped.setdefault(str(key), []).append(row)
    return [
        grouped[key]
        for key in sorted(
            grouped,
            key=lambda item: (
                _sort_value(grouped[item][0].get('system_id64')),
                _sort_value(grouped[item][0].get('station_name')),
                _sort_value(grouped[item][0].get('staging_station_id')),
            ),
        )
    ]


def _source_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'staging_station_id': row.get('staging_station_id'),
        'source': row.get('source'),
        'source_run_key': row.get('source_run_key'),
        'source_file_key': row.get('source_file_key'),
        'source_record_key': row.get('source_record_key'),
        'source_record_hash': row.get('source_record_hash'),
        'source_updated_at': row.get('source_updated_at'),
        'system_id64': _read_int(row.get('system_id64')),
        'system_name': row.get('system_name'),
        'station_name': row.get('station_name'),
        'normalised_station_name': _normalise_name(row.get('station_name')),
        'market_id': _read_int(row.get('market_id')),
        'edsm_station_id': _read_int(row.get('edsm_station_id')),
        'confidence': row.get('confidence'),
        'freshness_class': row.get('freshness_class'),
        'source_class': row.get('source_class'),
    }


def _canonical_matches(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    for row in rows:
        station_id = _read_int(row.get('canonical_station_id'))
        if station_id is None:
            continue
        matches[str(station_id)] = {
            'canonical_station_id': station_id,
            'system_id64': _read_int(row.get('canonical_system_id64')),
            'system_name': row.get('canonical_system_name'),
            'station_name': row.get('canonical_station_name'),
            'normalised_station_name': _normalise_name(row.get('canonical_station_name')),
            'station_type': row.get('canonical_station_type'),
        }
    return sorted(matches.values(), key=canonical_json)


def _confirmed_candidate_source_quality(source: Mapping[str, Any]) -> bool:
    return (
        source.get('confidence') in CONFIRMED_CANDIDATE_CONFIDENCE
        and source.get('freshness_class') in CONFIRMED_CANDIDATE_FRESHNESS
    )


def _proposed_identity_status(candidate_status: str) -> str:
    if candidate_status == 'confirmed_candidate':
        return 'confirmed'
    if candidate_status == 'conflicting':
        return 'conflicting'
    if candidate_status == 'rejected':
        return 'rejected'
    return 'proposed'


def _candidate_id(source: Mapping[str, Any], canonical_match: Mapping[str, Any] | None) -> str:
    return hashlib.sha256(canonical_json([
        'station_external_identity_candidate',
        source.get('source_run_key'),
        source.get('source_file_key'),
        source.get('source_record_hash'),
        source.get('market_id'),
        source.get('edsm_station_id'),
        source.get('system_id64'),
        _normalise_name(source.get('station_name')),
        _read_int((canonical_match or {}).get('canonical_station_id')),
    ]).encode('utf-8')).hexdigest()


def _record_source_coverage(counts: Counter[str], candidate: Mapping[str, Any]) -> None:
    source = candidate.get('source_identity') or {}
    _record_present_missing(counts, 'source_market_id', source.get('market_id'))
    _record_present_missing(counts, 'source_edsm_station_id', source.get('edsm_station_id'))
    _record_present_missing(counts, 'source_station_name', source.get('station_name'))
    _record_present_missing(counts, 'source_system_id64', source.get('system_id64'))


def _record_present_missing(counts: Counter[str], prefix: str, value: Any) -> None:
    suffix = 'missing' if _missing(value) else 'present'
    counts[f'{prefix}_{suffix}'] += 1


def _ordered_counts(counts: Mapping[str, int], keys: Sequence[str]) -> dict[str, int]:
    return {key: int(counts.get(key, 0)) for key in keys}


def _normalise_name(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r'\s+', ' ', str(value).strip().lower())
    return text or None


def _read_int(value: Any) -> int | None:
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _missing(value: Any) -> bool:
    return value is None or value == ''


def _sort_value(value: Any) -> tuple[int, str]:
    if value is None:
        return (1, '')
    return (0, str(value))


if __name__ == '__main__':
    raise SystemExit(main())
