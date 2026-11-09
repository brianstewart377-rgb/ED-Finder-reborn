"""Versioned report-only warehouse evidence coverage summaries."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from enrichment_staging import canonicalise_json_payload


WAREHOUSE_COVERAGE_REPORT_SCHEMA_VERSION = 'enrichment_warehouse_coverage_report/v1'
EXAMPLE_LIMIT = 5


def build_warehouse_coverage_report(
    *,
    station_candidates: Sequence[Mapping[str, Any]],
    body_candidates: Sequence[Mapping[str, Any]],
    ring_candidates: Sequence[Mapping[str, Any]],
    station_body_association_candidates: Sequence[Mapping[str, Any]],
    source_coverage_rows: Sequence[Mapping[str, Any]] = (),
    warnings: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build deterministic operator coverage sections from report-only evidence."""
    stations = _candidate_rows(station_candidates)
    bodies = _candidate_rows(body_candidates)
    rings = _candidate_rows(ring_candidates)
    associations = _candidate_rows(station_body_association_candidates)
    source_rows = _source_rows(source_coverage_rows)
    warning_rows = _rows(warnings)

    station_evidence = _station_evidence_section(stations, bodies, rings)
    station_body_links = _station_body_link_section(stations, associations)
    ring_evidence = _ring_evidence_section(bodies, rings)
    source_freshness = _source_freshness_section(stations + bodies + rings, source_rows)
    source_quality = _source_quality_section(stations + bodies + rings, source_rows, warning_rows)
    source_formats = _source_format_section(stations + bodies + rings, source_rows)
    high_value_systems = _high_value_systems_section(
        stations=stations,
        bodies=bodies,
        rings=rings,
        station_evidence=station_evidence,
        station_body_links=station_body_links,
        ring_evidence=ring_evidence,
        source_freshness=source_freshness,
        source_quality=source_quality,
    )
    needs_attention = {
        'systems_missing_station_evidence': station_evidence['systems_missing_station_evidence']['count'],
        'unknown_ring_evidence_bodies': ring_evidence['bodies_with_unknown_ring_evidence']['count'],
        'unresolved_station_body_links': station_body_links['unresolved_stations']['count'],
        'stale_or_undated_sources': source_freshness['stale_or_undated_evidence']['records_without_source_updated_at'],
        'skipped_or_malformed_raw_records': source_quality['malformed_or_skipped_source_rows']['count'],
        'duplicate_source_records': source_quality['duplicate_source_records']['duplicate_records'],
        'source_identity_conflicts': source_quality['source_identity_conflicts']['count'],
        'high_value_systems_needing_better_evidence': high_value_systems['count'],
    }

    return {
        'schema_version': WAREHOUSE_COVERAGE_REPORT_SCHEMA_VERSION,
        'dry_run': True,
        'report_only': True,
        'canonical_writes_planned': 0,
        'summary': {
            'systems_with_station_evidence': station_evidence['systems_with_station_evidence']['count'],
            'systems_missing_station_evidence': station_evidence['systems_missing_station_evidence']['count'],
            'trusted_ring_evidence_bodies': ring_evidence['bodies_with_trusted_ring_evidence']['count'],
            'unknown_ring_evidence_bodies': ring_evidence['bodies_with_unknown_ring_evidence']['count'],
            'explicit_no_ring_evidence_bodies': ring_evidence['bodies_with_explicit_no_ring_evidence']['count'],
            'confirmed_station_body_links': station_body_links['stations_with_confirmed_body_links']['count'],
            'inferred_or_verify_station_body_links': (
                station_body_links['stations_with_inferred_or_verify_body_links']['count']
            ),
            'unresolved_stations': station_body_links['unresolved_stations']['count'],
            'source_files_considered': len(source_rows),
            'malformed_or_skipped_source_rows': source_quality['malformed_or_skipped_source_rows']['count'],
            'source_identity_conflicts': source_quality['source_identity_conflicts']['count'],
            'high_value_systems_needing_better_evidence': high_value_systems['count'],
            'canonical_writes_planned': 0,
        },
        'station_evidence': station_evidence,
        'ring_evidence': ring_evidence,
        'station_body_links': station_body_links,
        'source_freshness': source_freshness,
        'source_quality': source_quality,
        'source_formats': source_formats,
        'high_value_systems_needing_better_evidence': high_value_systems,
        'operator_review': {
            'report_only': True,
            'canonical_writes_planned': 0,
            'needs_attention_buckets': needs_attention,
            'review_guidance': [
                'Treat coverage gaps as operator review signals, not write instructions.',
                'Unknown station/body/ring evidence remains unknown until a trusted source proves it.',
                'Source-only ring evidence does not confirm ring truth without trusted local body_rings rows.',
                'Explicit no-ring coverage is counted only from trusted scan facts, not missing or empty arrays.',
            ],
        },
        'warnings': [],
        'errors': [],
    }


def _station_evidence_section(
    station_candidates: Sequence[Mapping[str, Any]],
    body_candidates: Sequence[Mapping[str, Any]],
    ring_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    station_systems = _systems_from_candidates(station_candidates)
    body_or_ring_systems = _systems_from_candidates(list(body_candidates) + list(ring_candidates))
    missing_station_systems = {
        key: value
        for key, value in body_or_ring_systems.items()
        if key not in station_systems and key != 'unknown-system'
    }
    return {
        'systems_with_station_evidence': _count_examples(station_systems.values()),
        'systems_missing_station_evidence': _count_examples(missing_station_systems.values()),
        'scope_note': (
            'Missing station evidence is measured against systems that have staged body or ring evidence '
            'in this warehouse report; it is not a galaxy-wide canonical systems count.'
        ),
    }


def _station_body_link_section(
    station_candidates: Sequence[Mapping[str, Any]],
    association_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    association_by_hash = {
        str(_source(candidate).get('source_record_hash')): candidate
        for candidate in association_candidates
        if not _missing(_source(candidate).get('source_record_hash'))
    }
    by_state: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in station_candidates:
        state = _station_link_state(candidate, association_by_hash)
        by_state[state].append(_station_example(candidate, extra={
            'candidate_action': candidate.get('candidate_action'),
            'station_body_link_status': _station_body_link(candidate).get('association_status'),
        }))
    return {
        'stations_with_confirmed_body_links': _count_examples(by_state['confirmed']),
        'stations_with_inferred_or_verify_body_links': _count_examples(by_state['inferred_or_verify']),
        'unresolved_stations': _count_examples(by_state['unresolved']),
        'state_definitions': {
            'confirmed': 'A canonical station_body_links row is present with association_status=confirmed.',
            'inferred_or_verify': (
                'A canonical station_body_links inferred row exists, or staged station body_name is '
                'supported by exactly one staged body row. This remains verify/report-only evidence.'
            ),
            'unresolved': 'No confirmed or single supported body-link evidence is available.',
        },
    }


def _ring_evidence_section(
    body_candidates: Sequence[Mapping[str, Any]],
    ring_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    trusted_ring_bodies = {
        _body_key(_source(candidate)): _body_example_from_source(_source(candidate), extra={
            'ring_name': _source(candidate).get('ring_name'),
            'trusted_evidence': 'canonical_body_rings.local_matched',
        })
        for candidate in ring_candidates
        if _trusted_ring_candidate(candidate)
    }
    source_only_ring_bodies = {
        _body_key(_source(candidate)): _body_example_from_source(_source(candidate), extra={
            'ring_name': _source(candidate).get('ring_name'),
            'source_only_ring_evidence': True,
        })
        for candidate in ring_candidates
        if not _trusted_ring_candidate(candidate)
    }
    explicit_no_ring_bodies = {
        _body_key(_source(candidate)): _body_example_from_source(_source(candidate), extra={
            'ring_semantics': 'trusted_body_scan_fact_false',
        })
        for candidate in body_candidates
        if _trusted_explicit_no_ring_candidate(candidate)
    }
    empty_arrays_not_promoted = {
        _body_key(_source(candidate)): _body_example_from_source(_source(candidate), extra={
            'ring_array_state': 'empty',
            'handling': 'source_evidence_only_not_canonical_no_rings',
        })
        for candidate in body_candidates
        if _source(candidate).get('ring_array_state') == 'empty'
        and _body_key(_source(candidate)) not in explicit_no_ring_bodies
    }

    unknown_ring_bodies: dict[str, dict[str, Any]] = {}
    for candidate in body_candidates:
        key = _body_key(_source(candidate))
        if key in trusted_ring_bodies or key in explicit_no_ring_bodies:
            continue
        unknown_ring_bodies[key] = _body_example_from_source(_source(candidate), extra={
            'ring_array_state': _source(candidate).get('ring_array_state') or 'unknown',
            'unknown_reason': _ring_unknown_reason(candidate, key in source_only_ring_bodies),
        })
    for key, example in source_only_ring_bodies.items():
        if key not in trusted_ring_bodies and key not in explicit_no_ring_bodies:
            unknown_ring_bodies.setdefault(key, {
                **example,
                'unknown_reason': 'source_only_ring_evidence_not_trusted_ring_truth',
            })

    return {
        'bodies_with_trusted_ring_evidence': _count_examples(trusted_ring_bodies.values()),
        'bodies_with_unknown_ring_evidence': _count_examples(unknown_ring_bodies.values()),
        'bodies_with_explicit_no_ring_evidence': _count_examples(explicit_no_ring_bodies.values()),
        'source_only_ring_evidence_not_confirmed': _count_examples(source_only_ring_bodies.values()),
        'empty_source_arrays_not_promoted': _count_examples(empty_arrays_not_promoted.values()),
        'missing_ring_arrays_state': 'unknown_not_false',
        'ringed_truth_requires_trusted_body_rings': True,
        'explicit_no_ring_rule': (
            'Only trusted local scan facts with is_ringed=false count as explicit no-ring evidence here. '
            'Missing ring arrays and empty source arrays are not promoted to canonical no-rings.'
        ),
    }


def _source_freshness_section(
    candidates: Sequence[Mapping[str, Any]],
    source_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    freshness = Counter(
        str(_source(candidate).get('freshness_class'))
        for candidate in candidates
        if not _missing(_source(candidate).get('freshness_class'))
    )
    by_source: dict[str, dict[str, Any]] = {}
    for row in source_rows:
        source = str(row.get('source') or 'unknown_source')
        bucket = by_source.setdefault(source, {
            'source': source,
            'raw_records': 0,
            'records_with_source_updated_at': 0,
            'records_without_source_updated_at': 0,
            'latest_source_updated_at': None,
        })
        bucket['raw_records'] += _int(row.get('raw_records'))
        bucket['records_with_source_updated_at'] += _int(row.get('records_with_source_updated_at'))
        bucket['records_without_source_updated_at'] += _int(row.get('records_without_source_updated_at'))
        latest = _text(row.get('latest_source_updated_at'))
        if latest is not None and (bucket['latest_source_updated_at'] is None or latest > bucket['latest_source_updated_at']):
            bucket['latest_source_updated_at'] = latest

    records_without_timestamps = sum(_int(row.get('records_without_source_updated_at')) for row in source_rows)
    return {
        'freshness_class_distribution': dict(sorted(freshness.items())),
        'source_timestamp_coverage_by_source': _sort_rows(by_source.values()),
        'stale_or_undated_evidence': {
            'records_without_source_updated_at': records_without_timestamps,
            'file_snapshot_candidate_records': freshness.get('file_snapshot', 0),
            'review_rule': (
                'No wall-clock age threshold is applied in this deterministic report. '
                'file_snapshot and missing source_updated_at evidence require operator freshness review.'
            ),
        },
    }


def _source_quality_section(
    candidates: Sequence[Mapping[str, Any]],
    source_rows: Sequence[Mapping[str, Any]],
    warnings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    malformed_or_skipped = sum(
        _int(row.get('skipped_raw_records'))
        + _int(row.get('invalid_raw_records'))
        + _int(row.get('conflict_raw_records'))
        for row in source_rows
    )
    warning_distribution: Counter[str] = Counter()
    for row in source_rows:
        warning_distribution.update(_json_mapping(row.get('warning_reason_distribution')))
    warning_distribution.update(
        str(row.get('reason'))
        for row in warnings
        if not _missing(row.get('reason'))
    )
    duplicate_source_records = sum(_int(row.get('duplicate_source_records')) for row in source_rows)
    duplicate_hash_groups = sum(_int(row.get('duplicate_source_record_hashes')) for row in source_rows)
    if not source_rows:
        hashes = [
            _source(candidate).get('source_record_hash')
            for candidate in candidates
            if not _missing(_source(candidate).get('source_record_hash'))
        ]
        duplicate_hash_groups = sum(1 for _hash, count in Counter(hashes).items() if count > 1)
        duplicate_source_records = sum(count - 1 for _hash, count in Counter(hashes).items() if count > 1)

    identity_conflicts = _source_identity_conflicts(candidates)
    return {
        'malformed_or_skipped_source_rows': {
            'count': malformed_or_skipped,
            'warning_reason_distribution': dict(sorted(warning_distribution.items())),
        },
        'duplicate_source_records': {
            'duplicate_hash_groups': duplicate_hash_groups,
            'duplicate_records': duplicate_source_records,
            'handling': 'report_only; staging upserts remain keyed by source run and source_record_hash',
        },
        'source_identity_conflicts': {
            'count': len(identity_conflicts),
            'examples': _limited_examples(identity_conflicts),
            'handling': 'report_only_conflict_no_canonical_write',
        },
    }


def _source_format_section(
    candidates: Sequence[Mapping[str, Any]],
    source_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    source_distribution = Counter(
        str(row.get('source') or 'unknown_source')
        for row in source_rows
    )
    if not source_distribution:
        source_distribution.update(
            str(_source(candidate).get('source') or 'unknown_source')
            for candidate in candidates
        )
    return {
        'source_type_distribution': dict(sorted(source_distribution.items())),
        'source_class_distribution': _distribution_from_rows(source_rows, 'source_class'),
        'source_format_distribution': _distribution_from_rows(source_rows, 'source_format'),
        'source_format_version_distribution': _distribution_from_rows(source_rows, 'source_format_version'),
        'record_stream_shape_distribution': _distribution_from_rows(source_rows, 'record_stream_shape'),
        'source_files': _limited_examples(
            {
                'source_run_key': row.get('source_run_key'),
                'source_file_key': row.get('source_file_key'),
                'source': row.get('source'),
                'source_format': row.get('source_format'),
                'source_format_version': row.get('source_format_version'),
                'record_stream_shape': row.get('record_stream_shape'),
                'raw_records': row.get('raw_records'),
            }
            for row in source_rows
        ),
    }


def _high_value_systems_section(
    *,
    stations: Sequence[Mapping[str, Any]],
    bodies: Sequence[Mapping[str, Any]],
    rings: Sequence[Mapping[str, Any]],
    station_evidence: Mapping[str, Any],
    station_body_links: Mapping[str, Any],
    ring_evidence: Mapping[str, Any],
    source_freshness: Mapping[str, Any],
    source_quality: Mapping[str, Any],
) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {
        'system_key': '',
        'system_id64': None,
        'system_name': None,
        'station_evidence_count': 0,
        'body_evidence_count': 0,
        'ring_evidence_count': 0,
        'trusted_ring_evidence_count': 0,
        'review_reasons': set(),
    })
    for entity, rows in (('station', stations), ('body', bodies), ('ring', rings)):
        for candidate in rows:
            source = _source(candidate)
            key = _system_key(source)
            bucket = buckets[key]
            bucket['system_key'] = key
            bucket['system_id64'] = source.get('system_id64')
            bucket['system_name'] = source.get('system_name')
            bucket[f'{entity}_evidence_count'] += 1
            if entity == 'ring' and _trusted_ring_candidate(candidate):
                bucket['trusted_ring_evidence_count'] += 1
            action = candidate.get('candidate_action')
            if action in {'ambiguous_match', 'insufficient_evidence', 'candidate_insert_missing_canonical'}:
                bucket['review_reasons'].add(str(action))

    for example in station_evidence['systems_missing_station_evidence']['examples']:
        buckets[example['system_key']]['review_reasons'].add('missing_station_evidence')
    for example in station_body_links['unresolved_stations']['examples']:
        buckets[example['system_key']]['review_reasons'].add('unresolved_station_body_link')
    for example in ring_evidence['bodies_with_unknown_ring_evidence']['examples']:
        buckets[example['system_key']]['review_reasons'].add('unknown_ring_evidence')
    if source_quality['source_identity_conflicts']['count']:
        for conflict in source_quality['source_identity_conflicts']['examples']:
            system_key = conflict.get('system_key')
            if system_key in buckets:
                buckets[system_key]['review_reasons'].add('source_identity_conflict')

    candidates = []
    for bucket in buckets.values():
        evidence_count = (
            bucket['station_evidence_count']
            + bucket['body_evidence_count']
            + bucket['ring_evidence_count']
        )
        if not bucket['review_reasons']:
            continue
        if bucket['trusted_ring_evidence_count'] < 1 and evidence_count < 2:
            continue
        candidates.append({
            'system_key': bucket['system_key'],
            'system_id64': bucket['system_id64'],
            'system_name': bucket['system_name'],
            'station_evidence_count': bucket['station_evidence_count'],
            'body_evidence_count': bucket['body_evidence_count'],
            'ring_evidence_count': bucket['ring_evidence_count'],
            'trusted_ring_evidence_count': bucket['trusted_ring_evidence_count'],
            'candidate_action': 'needs_better_evidence',
            'review_reasons': sorted(bucket['review_reasons']),
            'report_only': True,
        })
    return {
        'count': len(candidates),
        'examples': _limited_examples(candidates, limit=10),
        'selection_rule': (
            'Conservative report-only bucket: systems with trusted ring evidence or multiple staged evidence rows '
            'and at least one coverage/reconciliation review reason.'
        ),
    }


def _station_link_state(
    station_candidate: Mapping[str, Any],
    association_by_hash: Mapping[str, Mapping[str, Any]],
) -> str:
    link = _station_body_link(station_candidate)
    status = link.get('association_status')
    if status == 'confirmed':
        return 'confirmed'
    if status == 'inferred':
        return 'inferred_or_verify'
    source_hash = _source(station_candidate).get('source_record_hash')
    association = association_by_hash.get(str(source_hash))
    if association and association.get('candidate_action') == 'station_body_supported_by_staged_body':
        return 'inferred_or_verify'
    return 'unresolved'


def _station_body_link(station_candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    canonical = station_candidate.get('canonical')
    if not isinstance(canonical, Mapping):
        return {}
    link = canonical.get('station_body_link')
    return link if isinstance(link, Mapping) else {}


def _trusted_ring_candidate(candidate: Mapping[str, Any]) -> bool:
    canonical = candidate.get('canonical')
    if not isinstance(canonical, Mapping):
        return False
    return canonical.get('association_status') == 'local_matched'


def _trusted_explicit_no_ring_candidate(candidate: Mapping[str, Any]) -> bool:
    canonical = candidate.get('canonical')
    if not isinstance(canonical, Mapping):
        return False
    scan_fact = canonical.get('ring_scan_fact')
    return isinstance(scan_fact, Mapping) and scan_fact.get('is_ringed') is False


def _ring_unknown_reason(candidate: Mapping[str, Any], has_source_only_ring: bool) -> str:
    if has_source_only_ring:
        return 'source_only_ring_evidence_not_trusted_ring_truth'
    state = _source(candidate).get('ring_array_state')
    if state == 'missing':
        return 'missing_ring_array_unknown_not_false'
    if state == 'empty':
        return 'empty_source_array_not_canonical_no_rings'
    if state == 'non_array':
        return 'malformed_ring_array_unknown'
    return 'no_trusted_ring_or_no_ring_evidence'


def _systems_from_candidates(candidates: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    systems = {}
    for candidate in candidates:
        source = _source(candidate)
        key = _system_key(source)
        systems[key] = _system_example_from_source(source)
    return dict(sorted(systems.items()))


def _source_identity_conflicts(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        key = _candidate_identity_key(candidate)
        if key is not None:
            grouped[key].append(candidate)

    conflicts = []
    for identity_key, group in grouped.items():
        hashes = sorted({
            str(_source(candidate).get('source_record_hash'))
            for candidate in group
            if not _missing(_source(candidate).get('source_record_hash'))
        })
        if len(hashes) < 2:
            continue
        first_source = _source(group[0])
        conflicts.append({
            'entity': group[0].get('entity'),
            'system_key': _system_key(first_source),
            'source_identity_key': identity_key,
            'source_record_hashes': hashes,
            'handling': 'report_only_conflict_no_canonical_write',
        })
    return _sort_rows(conflicts)


def _candidate_identity_key(candidate: Mapping[str, Any]) -> str | None:
    source = _source(candidate)
    entity = candidate.get('entity')
    system_key = _system_key(source)
    if system_key == 'unknown-system':
        return None
    if entity == 'station':
        entity_key = _first_identity(source, 'market_id', 'edsm_station_id', 'station_name')
    elif entity == 'body':
        entity_key = _first_identity(source, 'source_body_id', 'body_name')
    elif entity == 'ring':
        body_key = _first_identity(source, 'source_body_id', 'body_name')
        ring_name = _normalise_key(source.get('ring_name'))
        entity_key = f'{body_key}|ring:{ring_name}' if body_key and ring_name else None
    else:
        return None
    if entity_key is None:
        return None
    return f'{entity}|{system_key}|{entity_key}'


def _first_identity(source: Mapping[str, Any], *fields: str) -> str | None:
    for field in fields:
        value = source.get(field)
        if _missing(value):
            continue
        if isinstance(value, str):
            return f'{field}:{value.strip().lower()}'
        return f'{field}:{value}'
    return None


def _system_key(source: Mapping[str, Any]) -> str:
    if not _missing(source.get('system_id64')):
        return f"id64:{source.get('system_id64')}"
    if not _missing(source.get('system_name')):
        return f"name:{str(source.get('system_name')).strip()}"
    return 'unknown-system'


def _body_key(source: Mapping[str, Any]) -> str:
    system_key = _system_key(source)
    if not _missing(source.get('source_body_id')):
        return f"{system_key}|body_id:{source.get('source_body_id')}"
    if not _missing(source.get('body_name')):
        return f"{system_key}|body_name:{str(source.get('body_name')).strip().lower()}"
    return f'{system_key}|unknown-body'


def _normalise_key(value: Any) -> str | None:
    if _missing(value):
        return None
    return str(value).strip().lower()


def _system_example_from_source(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'system_key': _system_key(source),
        'system_id64': source.get('system_id64'),
        'system_name': source.get('system_name'),
    }


def _station_example(candidate: Mapping[str, Any], *, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source = _source(candidate)
    return {
        **_system_example_from_source(source),
        'station_name': source.get('station_name'),
        'source_record_hash': source.get('source_record_hash'),
        **dict(extra or {}),
    }


def _body_example_from_source(source: Mapping[str, Any], *, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        **_system_example_from_source(source),
        'body_key': _body_key(source),
        'source_body_id': source.get('source_body_id'),
        'body_name': source.get('body_name'),
        'source_record_hash': source.get('source_record_hash'),
        **dict(extra or {}),
    }


def _count_examples(rows: Sequence[Mapping[str, Any]] | Any) -> dict[str, Any]:
    row_list = _sort_rows(rows)
    return {
        'count': len(row_list),
        'examples': row_list[:EXAMPLE_LIMIT],
    }


def _distribution_from_rows(rows: Sequence[Mapping[str, Any]], field_name: str) -> dict[str, int]:
    return dict(sorted(Counter(
        str(row.get(field_name))
        for row in rows
        if not _missing(row.get(field_name))
    ).items()))


def _limited_examples(rows: Sequence[Mapping[str, Any]] | Any, *, limit: int = EXAMPLE_LIMIT) -> list[dict[str, Any]]:
    return _sort_rows(rows)[:limit]


def _candidate_rows(rows: Sequence[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    return _sort_rows(rows)


def _source_rows(rows: Sequence[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    return _sort_rows(rows)


def _rows(rows: Sequence[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    if isinstance(rows, Mapping):
        return [dict(rows)]
    if isinstance(rows, (str, bytes, bytearray)):
        return []
    try:
        iterable = list(rows)
    except TypeError:
        return []
    return [dict(row) for row in iterable if isinstance(row, Mapping)]


def _sort_rows(rows: Sequence[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    return sorted(_rows(rows), key=canonicalise_json_payload)


def _source(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    source = candidate.get('source')
    return source if isinstance(source, Mapping) else {}


def _json_mapping(value: Any) -> dict[str, int]:
    if isinstance(value, Mapping):
        return {
            str(key): _int(item)
            for key, item in value.items()
        }
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, Mapping):
            return {
                str(key): _int(item)
                for key, item in parsed.items()
            }
    return {}


def _int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False
