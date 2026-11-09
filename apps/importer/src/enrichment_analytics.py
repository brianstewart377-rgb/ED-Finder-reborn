"""Pure report-only analytics helpers for enrichment staging evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from enrichment_staging import canonicalise_json_payload


ANALYTICS_SIGNALS_SCHEMA_VERSION = 'enrichment_analytics_signals/v1'
COLONISATION_CANDIDATE_SIGNALS_SCHEMA_VERSION = 'colonisation_candidate_signals/v1'
MISSION_DENSITY_SIGNALS_SCHEMA_VERSION = 'mission_density_signals/v1'


def build_enrichment_analytics_signals(reconciliation_report: Mapping[str, Any]) -> dict[str, Any]:
    """Build deterministic report-only quality signals from reconciliation output."""
    station_candidates = _candidate_rows(reconciliation_report.get('station_candidates', ()))
    body_candidates = _candidate_rows(reconciliation_report.get('body_candidates', ()))
    ring_candidates = _candidate_rows(reconciliation_report.get('ring_candidates', ()))
    all_candidates = station_candidates + body_candidates + ring_candidates
    warning_rows = _rows(reconciliation_report.get('warnings', ()))
    error_rows = _rows(reconciliation_report.get('errors', ()))

    station_signals = _station_quality_signals(station_candidates)
    body_signals = _body_quality_signals(body_candidates)
    ring_signals = _ring_quality_signals(ring_candidates)
    coverage_signals = _source_coverage_signals(all_candidates, warning_rows)
    warnings = _analytics_warnings(all_candidates, warning_rows)

    return {
        'schema_version': ANALYTICS_SIGNALS_SCHEMA_VERSION,
        'dry_run': True,
        'summary': {
            'station_candidates': len(station_candidates),
            'body_candidates': len(body_candidates),
            'ring_candidates': len(ring_candidates),
            'total_candidates': len(all_candidates),
            'missing_system_identifiers': sum(
                1 for candidate in all_candidates if _missing_system_identity(candidate)
            ),
            'missing_body_identifiers': sum(
                1 for candidate in body_candidates + ring_candidates if _missing_body_identity(candidate)
            ),
            'ambiguous_station_matches': sum(
                1 for candidate in station_candidates
                if candidate.get('candidate_action') == 'ambiguous_match'
            ),
            'staged_records_without_canonical_match': sum(
                1 for candidate in all_candidates
                if candidate.get('candidate_action') == 'candidate_insert_missing_canonical'
            ),
            'rings_without_body_match': sum(
                1 for candidate in ring_candidates
                if not candidate.get('canonical') or candidate.get('canonical', {}).get('body_id') is None
            ),
            'warnings': len(warnings),
            'errors': len(error_rows),
        },
        'station_quality_signals': _sort_rows(station_signals),
        'body_quality_signals': _sort_rows(body_signals),
        'ring_quality_signals': _sort_rows(ring_signals),
        'source_coverage_signals': _sort_rows(coverage_signals),
        'warnings': _sort_rows(warnings),
        'errors': error_rows,
    }


def build_colonisation_candidate_signals(analytics_report: Mapping[str, Any]) -> dict[str, Any]:
    """Build conservative report-only colonisation review signals from analytics output."""
    station_signals = [
        signal for signal in _rows(analytics_report.get('station_quality_signals', ()))
        if _has_system_identity(signal)
    ]
    source_signals = [
        signal for signal in _rows(analytics_report.get('source_coverage_signals', ()))
        if _has_system_identity(signal)
    ]
    body_signals = [
        signal for signal in _rows(analytics_report.get('body_quality_signals', ()))
        if _has_system_identity(signal)
    ]
    ring_signals = [
        signal for signal in _rows(analytics_report.get('ring_quality_signals', ()))
        if _has_system_identity(signal)
    ]
    system_buckets = _system_signal_buckets(station_signals + source_signals + body_signals + ring_signals)
    candidates = []
    for system_key, signals in sorted(system_buckets.items()):
        severities = Counter(signal.get('severity', 'review') for signal in signals)
        candidates.append({
            'system_key': system_key,
            'candidate_action': 'needs_review',
            'confidence_bucket': _confidence_bucket(severities),
            'evidence_signals': len(signals),
            'signal_types': sorted({str(signal.get('signal')) for signal in signals}),
            'warnings': sorted({
                str(signal.get('signal'))
                for signal in signals
                if signal.get('severity') in {'warning', 'review'}
            }),
        })
    return {
        'schema_version': COLONISATION_CANDIDATE_SIGNALS_SCHEMA_VERSION,
        'dry_run': True,
        'summary': {
            'systems_considered': len(candidates),
            'review_candidates': len(candidates),
            'canonical_writes_planned': 0,
        },
        'candidate_systems': _sort_rows(candidates),
        'warnings': [],
        'errors': [],
    }


def build_mission_density_signals(reconciliation_report: Mapping[str, Any]) -> dict[str, Any]:
    """Build conservative report-only mission density evidence counts by system."""
    station_candidates = _candidate_rows(reconciliation_report.get('station_candidates', ()))
    body_candidates = _candidate_rows(reconciliation_report.get('body_candidates', ()))
    ring_candidates = _candidate_rows(reconciliation_report.get('ring_candidates', ()))
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {
        'system_key': '',
        'station_evidence_count': 0,
        'body_evidence_count': 0,
        'ring_evidence_count': 0,
        'review_flags': set(),
    })
    for entity, candidates in (
        ('station', station_candidates),
        ('body', body_candidates),
        ('ring', ring_candidates),
    ):
        for candidate in candidates:
            key = _system_key(candidate)
            bucket = buckets[key]
            bucket['system_key'] = key
            bucket[f'{entity}_evidence_count'] += 1
            action = candidate.get('candidate_action')
            if action in {'ambiguous_match', 'insufficient_evidence', 'candidate_insert_missing_canonical'}:
                bucket['review_flags'].add(str(action))

    signals = []
    for bucket in buckets.values():
        signals.append({
            'system_key': bucket['system_key'],
            'station_evidence_count': bucket['station_evidence_count'],
            'body_evidence_count': bucket['body_evidence_count'],
            'ring_evidence_count': bucket['ring_evidence_count'],
            'review_flags': sorted(bucket['review_flags']),
        })
    return {
        'schema_version': MISSION_DENSITY_SIGNALS_SCHEMA_VERSION,
        'dry_run': True,
        'summary': {
            'systems_considered': len(signals),
            'station_evidence_count': sum(signal['station_evidence_count'] for signal in signals),
            'body_evidence_count': sum(signal['body_evidence_count'] for signal in signals),
            'ring_evidence_count': sum(signal['ring_evidence_count'] for signal in signals),
            'canonical_writes_planned': 0,
        },
        'system_signals': _sort_rows(signals),
        'warnings': [],
        'errors': [],
    }


def _station_quality_signals(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    signals = []
    for candidate in candidates:
        action = candidate.get('candidate_action')
        if action == 'ambiguous_match':
            signals.append(_signal(candidate, 'ambiguous_station_match', 'warning'))
        if action == 'candidate_insert_missing_canonical':
            signals.append(_signal(candidate, 'station_missing_canonical_match', 'review'))
        if _missing_system_identity(candidate):
            signals.append(_signal(candidate, 'missing_system_identifier', 'warning'))
    return signals


def _body_quality_signals(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    signals = []
    for candidate in candidates:
        if _missing_system_identity(candidate):
            signals.append(_signal(candidate, 'missing_system_identifier', 'warning'))
        if _missing_body_identity(candidate):
            signals.append(_signal(candidate, 'missing_body_identifier', 'warning'))
        if candidate.get('candidate_action') == 'candidate_insert_missing_canonical':
            signals.append(_signal(candidate, 'body_missing_canonical_match', 'review'))
    return signals


def _ring_quality_signals(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    signals = []
    for candidate in candidates:
        if _missing_body_identity(candidate):
            signals.append(_signal(candidate, 'missing_body_identifier', 'warning'))
        if not candidate.get('canonical') or candidate.get('canonical', {}).get('body_id') is None:
            signals.append(_signal(candidate, 'ring_without_canonical_body_match', 'review'))
        if candidate.get('candidate_action') == 'candidate_insert_missing_canonical':
            signals.append(_signal(candidate, 'ring_missing_canonical_match', 'review'))
    return signals


def _source_coverage_signals(
    candidates: Sequence[Mapping[str, Any]],
    warning_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    signals = []
    action_counts = Counter(str(candidate.get('candidate_action')) for candidate in candidates)
    for action, count in sorted(action_counts.items()):
        signals.append({
            'signal': f'candidate_action:{action}',
            'severity': 'info',
            'candidate_action': action,
            'count': count,
        })

    hashes = [
        candidate.get('source', {}).get('source_record_hash')
        for candidate in candidates
        if candidate.get('source', {}).get('source_record_hash') is not None
    ]
    duplicate_hashes = sorted(hash_value for hash_value, count in Counter(hashes).items() if count > 1)
    for hash_value in duplicate_hashes:
        signals.append({
            'signal': 'duplicate_source_record_hash',
            'severity': 'review',
            'source_record_hash': hash_value,
            'count': hashes.count(hash_value),
        })

    if candidates:
        warning_rate = len(warning_rows) / len(candidates)
        if warning_rate >= 0.25:
            signals.append({
                'signal': 'high_warning_rate',
                'severity': 'review',
                'warning_rate': round(warning_rate, 4),
                'warnings': len(warning_rows),
                'candidates': len(candidates),
            })
    return signals


def _analytics_warnings(
    candidates: Sequence[Mapping[str, Any]],
    warning_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    warnings = [dict(row) for row in warning_rows]
    if candidates and len(warning_rows) / len(candidates) >= 0.25:
        warnings.append({
            'reason': 'high_reconciliation_warning_rate',
            'warnings': len(warning_rows),
            'candidates': len(candidates),
        })
    return warnings


def _signal(candidate: Mapping[str, Any], signal: str, severity: str) -> dict[str, Any]:
    source = dict(candidate.get('source') or {})
    return {
        'signal': signal,
        'severity': severity,
        'entity': candidate.get('entity'),
        'candidate_action': candidate.get('candidate_action'),
        'source_record_hash': source.get('source_record_hash'),
        'system_id64': source.get('system_id64'),
        'system_name': source.get('system_name'),
        'body_name': source.get('body_name'),
        'ring_name': source.get('ring_name'),
    }


def _missing_system_identity(candidate: Mapping[str, Any]) -> bool:
    source = candidate.get('source') or {}
    return _missing(source.get('system_id64')) and _missing(source.get('system_name'))


def _has_system_identity(signal: Mapping[str, Any]) -> bool:
    return not _missing(signal.get('system_id64')) or not _missing(signal.get('system_name'))


def _missing_body_identity(candidate: Mapping[str, Any]) -> bool:
    source = candidate.get('source') or {}
    return _missing(source.get('source_body_id')) and _missing(source.get('body_name'))


def _system_signal_buckets(signals: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        buckets[_system_key_from_source(signal)].append(dict(signal))
    return buckets


def _system_key(candidate: Mapping[str, Any]) -> str:
    return _system_key_from_source(candidate.get('source') or {})


def _system_key_from_source(source: Mapping[str, Any]) -> str:
    if not _missing(source.get('system_id64')):
        return f"id64:{source.get('system_id64')}"
    if not _missing(source.get('system_name')):
        return f"name:{source.get('system_name')}"
    return 'unknown-system'


def _confidence_bucket(severities: Counter[str]) -> str:
    if severities.get('warning'):
        return 'low'
    if severities.get('review'):
        return 'needs_review'
    return 'moderate'


def _candidate_rows(rows: Sequence[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    return _sort_rows(rows)


def _rows(rows: Sequence[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _sort_rows(rows: Sequence[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    return sorted(_rows(rows), key=canonicalise_json_payload)


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False
