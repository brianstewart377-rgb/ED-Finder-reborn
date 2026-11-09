#!/usr/bin/env python3
"""Build a compact, bounded-memory summary of reconciliation JSON artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import ijson
from ijson.common import ObjectBuilder
from shared_contracts.enrichment_artifact_contracts import validate_compact_warehouse_status_artifact


SUMMARY_SCHEMA_VERSION = 'enrichment_reconciliation_artifact_summary/v1'
RECONCILIATION_SCHEMA_VERSION = 'enrichment_staging_reconciliation/v1'
HASH_CHUNK_SIZE = 1024 * 1024
TOP_LIST_LIMIT = 20
CANDIDATE_SECTIONS = (
    'station_candidates',
    'body_candidates',
    'ring_candidates',
    'station_body_association_candidates',
)
TOP_OBJECTS = {
    'summary',
    'source_coverage_summary',
    'warehouse_coverage_report',
    'confidence_risk_summary',
}
BLOCKING_ACTIONS = {'ambiguous_match', 'insufficient_evidence'}
SCALAR_EVENTS = {'string', 'number', 'boolean', 'null'}
SENSITIVE_KEY_RE = re.compile(r'(dsn|password|secret|token|credential|source_path|path)\b', re.IGNORECASE)
SENSITIVE_TEXT_RE = re.compile(
    r'(^/|[A-Za-z]:\\|postgres(?:ql)?://|'
    r'pass' r'word=|'
    r'PG' r'PASSWORD=|'
    r'SEC' r'RET=|'
    r'TOK' r'EN=)',
    re.IGNORECASE,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Build a compact report-only summary of a reconciliation artifact without DB access.',
    )
    parser.add_argument('--artifact', required=True, help='Path to the reconciliation JSON artifact.')
    parser.add_argument('--output', required=True, help='Path for the compact summary JSON.')
    parser.add_argument(
        '--max-candidate-samples',
        type=int,
        default=50,
        help='Maximum sanitized candidate samples to include. Default: 50.',
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = build_compact_summary(
            Path(args.artifact),
            max_candidate_samples=args.max_candidate_samples,
        )
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, sort_keys=True, indent=2) + '\n',
            encoding='utf-8',
        )
    except (OSError, ValueError, ijson.JSONError) as exc:
        print(f'reconciliation artifact summary failed: {exc}', file=sys.stderr)
        return 2
    return 0


def build_compact_summary(artifact_path: Path, *, max_candidate_samples: int = 50) -> dict[str, Any]:
    if max_candidate_samples < 0:
        raise ValueError('--max-candidate-samples must be >= 0')
    artifact_size = artifact_path.stat().st_size
    artifact_sha256 = _sha256_file(artifact_path)

    parsed = _stream_reconciliation_artifact(
        artifact_path,
        max_candidate_samples=max_candidate_samples,
    )
    artifact_schema_version = parsed['artifact_schema_version']
    if artifact_schema_version != RECONCILIATION_SCHEMA_VERSION:
        raise ValueError(
            f'unsupported reconciliation artifact schema {artifact_schema_version!r}; '
            f'expected {RECONCILIATION_SCHEMA_VERSION!r}'
        )

    summary_counts = _summary_counts(parsed['top_objects'].get('summary', {}))
    canonical_writes_planned = parsed['canonical_writes_planned']
    if canonical_writes_planned is None:
        canonical_writes_planned = summary_counts.get('canonical_writes_planned')

    candidate_action_counts = {
        section: _counter_dict(parsed['candidate_action_counts'][section])
        for section in CANDIDATE_SECTIONS
    }
    candidate_action_counts['all'] = _counter_dict(parsed['candidate_action_counts']['all'])

    summary_payload = _json_safe_value({
        'schema_version': SUMMARY_SCHEMA_VERSION,
        'safe_for_git': False,
        'source_artifact_basename': artifact_path.name,
        'source_artifact_sha256': artifact_sha256,
        'source_artifact_size_bytes': artifact_size,
        'artifact_schema_version': artifact_schema_version,
        'canonical_writes_planned': canonical_writes_planned,
        'artifact_summary_counts': summary_counts,
        'station_candidate_count': parsed['candidate_counts']['station_candidates'],
        'candidate_counts': {
            section: parsed['candidate_counts'][section]
            for section in CANDIDATE_SECTIONS
        },
        'candidate_action_counts': candidate_action_counts,
        'candidate_update_counts': _action_count_summary(parsed['candidate_action_counts'], 'candidate_update'),
        'candidate_insert_missing_canonical_counts': _action_count_summary(
            parsed['candidate_action_counts'],
            'candidate_insert_missing_canonical',
        ),
        'top_blocking_reasons': _top_counter(parsed['blocking_reasons'], label='reason'),
        'top_confidence_counts': _top_counter(parsed['confidence_counts']),
        'top_confidence_level_counts': _top_counter(parsed['confidence_level_counts']),
        'top_risk_class_counts': _top_counter(parsed['risk_class_counts']),
        'top_reconciliation_state_counts': _top_counter(parsed['reconciliation_state_counts']),
        'confidence_risk_counts': _confidence_risk_counts(
            parsed['top_objects'].get('confidence_risk_summary', {}),
        ),
        'source_coverage_summary_counts': _source_coverage_counts(
            parsed['top_objects'].get('source_coverage_summary', {}),
        ),
        'warehouse_coverage_summary_counts': _warehouse_coverage_counts(
            parsed['top_objects'].get('warehouse_coverage_report', {}),
        ),
        'candidate_samples': {
            'max_samples': max_candidate_samples,
            'samples_included': len(parsed['candidate_samples']),
            'samples': parsed['candidate_samples'],
        },
    })
    validate_compact_warehouse_status_artifact(summary_payload)
    return summary_payload


def _stream_reconciliation_artifact(artifact_path: Path, *, max_candidate_samples: int) -> dict[str, Any]:
    candidate_counts = Counter({section: 0 for section in CANDIDATE_SECTIONS})
    candidate_action_counts: dict[str, Counter[str]] = {
        section: Counter()
        for section in CANDIDATE_SECTIONS
    }
    candidate_action_counts['all'] = Counter()

    parsed: dict[str, Any] = {
        'artifact_schema_version': None,
        'canonical_writes_planned': None,
        'top_objects': {},
        'candidate_counts': candidate_counts,
        'candidate_action_counts': candidate_action_counts,
        'blocking_reasons': Counter(),
        'confidence_counts': Counter(),
        'confidence_level_counts': Counter(),
        'risk_class_counts': Counter(),
        'reconciliation_state_counts': Counter(),
        'candidate_samples': [],
    }

    candidate_builder: ObjectBuilder | None = None
    candidate_section: str | None = None
    candidate_depth = 0
    top_object_builder: ObjectBuilder | None = None
    top_object_name: str | None = None
    top_object_depth = 0

    with artifact_path.open('rb') as handle:
        for prefix, event, value in ijson.parse(handle):
            if candidate_builder is not None:
                candidate_builder.event(event, value)
                candidate_depth = _updated_depth(candidate_depth, event)
                if candidate_depth == 0:
                    _record_candidate(
                        parsed,
                        section=str(candidate_section),
                        candidate=candidate_builder.value,
                        max_candidate_samples=max_candidate_samples,
                    )
                    candidate_builder = None
                    candidate_section = None
                continue

            if top_object_builder is not None:
                top_object_builder.event(event, value)
                top_object_depth = _updated_depth(top_object_depth, event)
                if top_object_depth == 0:
                    parsed['top_objects'][str(top_object_name)] = top_object_builder.value
                    top_object_builder = None
                    top_object_name = None
                continue

            if event == 'start_map' and prefix in TOP_OBJECTS:
                top_object_builder = ObjectBuilder()
                top_object_name = prefix
                top_object_depth = 0
                top_object_builder.event(event, value)
                top_object_depth = _updated_depth(top_object_depth, event)
                continue

            if event == 'start_map':
                section = _candidate_section_for_prefix(prefix)
                if section is not None:
                    candidate_builder = ObjectBuilder()
                    candidate_section = section
                    candidate_depth = 0
                    candidate_builder.event(event, value)
                    candidate_depth = _updated_depth(candidate_depth, event)
                    continue

            if prefix == 'schema_version' and event in SCALAR_EVENTS:
                parsed['artifact_schema_version'] = value
                if value != RECONCILIATION_SCHEMA_VERSION:
                    raise ValueError(
                        f'unsupported reconciliation artifact schema {value!r}; '
                        f'expected {RECONCILIATION_SCHEMA_VERSION!r}'
                    )
                continue

            if prefix in {'canonical_writes_planned', 'summary.canonical_writes_planned'} and event in SCALAR_EVENTS:
                parsed['canonical_writes_planned'] = value

    return parsed


def _record_candidate(
    parsed: dict[str, Any],
    *,
    section: str,
    candidate: Any,
    max_candidate_samples: int,
) -> None:
    if not isinstance(candidate, Mapping):
        return

    parsed['candidate_counts'][section] += 1
    action = _safe_counter_key(candidate.get('candidate_action'))
    if action is not None:
        parsed['candidate_action_counts'][section][action] += 1
        parsed['candidate_action_counts']['all'][action] += 1

    for source_key, counter_name in (
        ('confidence', 'confidence_counts'),
        ('confidence_level', 'confidence_level_counts'),
        ('risk_class', 'risk_class_counts'),
        ('reconciliation_state', 'reconciliation_state_counts'),
    ):
        counter_key = _safe_counter_key(candidate.get(source_key))
        if counter_key is not None:
            parsed[counter_name][counter_key] += 1

    if _is_blocked_candidate(candidate):
        reasons = _safe_string_list(candidate.get('risk_flags'))
        if not reasons and action is not None:
            reasons = [action]
        if not reasons:
            state = _safe_counter_key(candidate.get('reconciliation_state'))
            reasons = [state] if state is not None else ['blocked_unknown_reason']
        for reason in reasons:
            parsed['blocking_reasons'][reason] += 1

    if len(parsed['candidate_samples']) < max_candidate_samples:
        parsed['candidate_samples'].append(_candidate_sample(section, candidate))


def _candidate_sample(section: str, candidate: Mapping[str, Any]) -> dict[str, Any]:
    sample: dict[str, Any] = {
        'section': section,
    }
    for key in (
        'entity',
        'candidate_action',
        'reconciliation_state',
        'confidence',
        'confidence_level',
        'risk_class',
        'evidence_quality',
        'identifier_quality',
    ):
        value = _safe_scalar(candidate.get(key), key=key)
        if value is not None:
            sample[key] = value

    source = candidate.get('source')
    if isinstance(source, Mapping):
        source_identity = _sanitized_source_identity(source)
        if source_identity:
            sample['source_identity'] = source_identity

    for key in ('risk_flags', 'review_classifications'):
        values = _safe_string_list(candidate.get(key))
        if values:
            sample[key] = values

    differences = candidate.get('differences')
    fields = _difference_fields(differences)
    if fields:
        sample['difference_fields'] = fields

    warnings = candidate.get('warnings')
    warning_reasons = _warning_reasons(warnings)
    if warning_reasons:
        sample['warning_reasons'] = warning_reasons

    canonical_matches = candidate.get('canonical_matches')
    if isinstance(canonical_matches, Sequence) and not isinstance(canonical_matches, (str, bytes, bytearray)):
        sample['canonical_match_count'] = len(canonical_matches)

    review_marker = _future_review_marker(candidate.get('future_canonical_review_candidate'))
    if review_marker:
        sample['future_canonical_review_candidate'] = review_marker

    return sample


def _sanitized_source_identity(source: Mapping[str, Any]) -> dict[str, Any]:
    allowed_keys = (
        'system_id64',
        'system_name',
        'market_id',
        'edsm_station_id',
        'station_name',
        'source_body_id',
        'body_name',
        'ring_name',
        'source',
        'source_class',
        'confidence',
        'freshness_class',
        'source_updated_at',
        'source_record_hash',
    )
    sanitized: dict[str, Any] = {}
    for key in allowed_keys:
        value = _safe_scalar(source.get(key), key=key)
        if value is not None:
            sanitized[key] = value
    return sanitized


def _summary_counts(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, Mapping):
        return {}
    return {
        str(key): _json_safe_value(value)
        for key, value in sorted(summary.items())
        if _is_safe_key(str(key)) and _is_compact_scalar(value)
    }


def _confidence_risk_counts(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, Mapping):
        return {}
    count_keys = (
        'schema_version',
        'canonical_writes_planned',
        'confidence_distribution',
        'confidence_level_distribution',
        'evidence_quality_distribution',
        'identifier_quality_distribution',
        'reconciliation_state_distribution',
        'risk_class_distribution',
        'risk_flag_distribution',
        'review_classification_distribution',
        'source_freshness_impact_distribution',
        'future_canonical_review_candidates',
    )
    return _known_key_summary(summary, count_keys)


def _source_coverage_counts(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, Mapping):
        return {}
    result = _known_key_summary(summary, ('schema_version', 'canonical_writes_planned', 'warnings'))
    entities = summary.get('entities')
    if isinstance(entities, Mapping):
        result['entities'] = {}
        for entity_name in ('station', 'body', 'ring'):
            entity = entities.get(entity_name)
            if not isinstance(entity, Mapping):
                continue
            result['entities'][entity_name] = _known_key_summary(
                entity,
                (
                    'candidates',
                    'candidate_actions',
                    'confidence',
                    'missing_system_identifiers',
                    'volatile_warnings',
                ),
            )
    ring_evidence = summary.get('ring_evidence')
    if isinstance(ring_evidence, Mapping):
        result['ring_evidence'] = _known_key_summary(
            ring_evidence,
            (
                'staged_ring_candidates',
                'trusted_local_matched_ring_candidates',
                'missing_ring_arrays_state',
                'ringed_truth_requires_trusted_body_rings',
            ),
        )
    return result


def _warehouse_coverage_counts(report: Any) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        return {}
    result = _known_key_summary(report, ('schema_version', 'canonical_writes_planned'))
    summary = report.get('summary')
    if isinstance(summary, Mapping):
        result['summary'] = {
            str(key): _json_safe_value(value)
            for key, value in sorted(summary.items())
            if _is_safe_key(str(key)) and _is_compact_scalar(value)
        }
    operator_review = report.get('operator_review')
    if isinstance(operator_review, Mapping):
        needs_attention = operator_review.get('needs_attention_buckets')
        if isinstance(needs_attention, Mapping):
            result['needs_attention_buckets'] = {
                str(key): _json_safe_value(value)
                for key, value in sorted(needs_attention.items())
                if _is_safe_key(str(key)) and _is_compact_scalar(value)
            }
    return result


def _known_key_summary(source: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    return {
        key: _json_safe_value(source[key])
        for key in keys
        if key in source and _is_safe_key(key) and _is_compact_summary_value(source[key])
    }


def _action_count_summary(action_counts: Mapping[str, Counter[str]], action: str) -> dict[str, int]:
    result = {
        section: int(action_counts[section].get(action, 0))
        for section in CANDIDATE_SECTIONS
    }
    result['all'] = int(action_counts['all'].get(action, 0))
    return result


def _candidate_section_for_prefix(prefix: str) -> str | None:
    for section in CANDIDATE_SECTIONS:
        if prefix == f'{section}.item':
            return section
    return None


def _updated_depth(depth: int, event: str) -> int:
    if event in {'start_map', 'start_array'}:
        return depth + 1
    if event in {'end_map', 'end_array'}:
        return depth - 1
    return depth


def _is_blocked_candidate(candidate: Mapping[str, Any]) -> bool:
    action = candidate.get('candidate_action')
    risk_class = candidate.get('risk_class')
    classifications = set(_safe_string_list(candidate.get('review_classifications')))
    return risk_class == 'blocked' or action in BLOCKING_ACTIONS or 'blocked' in classifications


def _difference_fields(differences: Any) -> list[str]:
    if not isinstance(differences, Sequence) or isinstance(differences, (str, bytes, bytearray)):
        return []
    fields = {
        str(difference.get('field'))
        for difference in differences
        if isinstance(difference, Mapping)
        and _safe_counter_key(difference.get('field')) is not None
    }
    return sorted(fields)


def _warning_reasons(warnings: Any) -> list[str]:
    if not isinstance(warnings, Sequence) or isinstance(warnings, (str, bytes, bytearray)):
        return []
    reasons = {
        _safe_counter_key(warning.get('reason'))
        for warning in warnings
        if isinstance(warning, Mapping)
    }
    return sorted(reason for reason in reasons if reason is not None)


def _future_review_marker(review: Any) -> dict[str, Any]:
    if not isinstance(review, Mapping):
        return {}
    result = _known_key_summary(
        review,
        (
            'marker',
            'auto_promote_to_canonical',
            'canonical_writes_planned',
        ),
    )
    return result


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def _top_counter(
    counter: Counter[str],
    *,
    limit: int = TOP_LIST_LIMIT,
    label: str = 'value',
) -> list[dict[str, Any]]:
    return [
        {label: key, 'count': int(count)}
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    values = {
        key
        for key in (_safe_counter_key(item) for item in value)
        if key is not None
    }
    return sorted(values)


def _safe_counter_key(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    if _looks_sensitive_text(text):
        return '[redacted]'
    return text


def _safe_scalar(value: Any, *, key: str) -> Any:
    if value is None:
        return None
    if not _is_safe_key(key):
        return None
    if isinstance(value, (str, int, float, bool, Decimal)):
        if isinstance(value, str) and _looks_sensitive_text(value):
            return None
        return _json_safe_value(value)
    return None


def _is_compact_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool, Decimal))


def _is_compact_summary_value(value: Any) -> bool:
    if _is_compact_scalar(value):
        return True
    if isinstance(value, Mapping):
        return all(
            isinstance(key, str)
            and _is_safe_key(key)
            and _is_compact_summary_value(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return all(_is_compact_scalar(item) for item in value)
    return False


def _is_safe_key(key: str) -> bool:
    return SENSITIVE_KEY_RE.search(key) is None


def _looks_sensitive_text(text: str) -> bool:
    return SENSITIVE_TEXT_RE.search(text) is not None


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
            if _is_safe_key(str(key))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe_value(item) for item in value]
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while True:
            chunk = handle.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == '__main__':
    raise SystemExit(main())
