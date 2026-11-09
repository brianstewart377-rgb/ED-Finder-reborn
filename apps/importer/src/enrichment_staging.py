"""Pure helpers for offline enrichment staging and dry-run reports.

This module is intentionally side-effect free. It does not connect to a
database, invoke container tooling, or call the network. It only normalises source metadata,
builds deterministic hashes, classifies source evidence, validates staging
rows, and constructs versioned report skeletons.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePath
from typing import Any


SOURCE_CLASS_STABLE = 'stable'
SOURCE_CLASS_SEMI_STABLE = 'semi-stable'
SOURCE_CLASS_VOLATILE = 'volatile'
SOURCE_CLASS_DIAGNOSTIC_ONLY = 'diagnostic-only'
SOURCE_CLASSES = {
    SOURCE_CLASS_STABLE,
    SOURCE_CLASS_SEMI_STABLE,
    SOURCE_CLASS_VOLATILE,
    SOURCE_CLASS_DIAGNOSTIC_ONLY,
}

ENRICHMENT_SNAPSHOT_LOAD_PLAN_SCHEMA = 'enrichment_snapshot_load_plan/v1'
STATION_SNAPSHOT_DRY_RUN_SCHEMA = 'station_snapshot_enrichment_dry_run/v1'
BODY_RING_DRY_RUN_SCHEMA = 'body_ring_enrichment_dry_run/v1'
MISSION_INTELLIGENCE_DRY_RUN_SCHEMA = 'mission_intelligence_dry_run/v1'
EXPLORATION_INTELLIGENCE_DRY_RUN_SCHEMA = 'exploration_intelligence_dry_run/v1'
COLONISATION_ECONOMY_DRY_RUN_SCHEMA = 'colonisation_economy_intelligence_dry_run/v1'
ALERT_CANDIDATE_DRY_RUN_SCHEMA = 'alert_candidate_dry_run/v1'

REPORT_SCHEMAS = {
    ENRICHMENT_SNAPSHOT_LOAD_PLAN_SCHEMA,
    STATION_SNAPSHOT_DRY_RUN_SCHEMA,
    BODY_RING_DRY_RUN_SCHEMA,
    MISSION_INTELLIGENCE_DRY_RUN_SCHEMA,
    EXPLORATION_INTELLIGENCE_DRY_RUN_SCHEMA,
    COLONISATION_ECONOMY_DRY_RUN_SCHEMA,
    ALERT_CANDIDATE_DRY_RUN_SCHEMA,
}

ADAPTER_VERSION = 'v1'

SOURCE_ALIASES = {
    'edsm stations': 'edsm_nightly_stations',
    'edsm_station_snapshot': 'edsm_nightly_stations',
    'edsm_stations': 'edsm_nightly_stations',
    'edsm_nightly_station_snapshot': 'edsm_nightly_stations',
    'edsm bodies': 'edsm_nightly_bodies',
    'edsm_body_snapshot': 'edsm_nightly_bodies',
    'spansh': 'spansh_dump',
    'spansh systems': 'spansh_dump',
    'spansh_dump_file': 'spansh_dump',
    'eddn_market': 'eddn_market_data',
    'eddn_markets': 'eddn_market_data',
    'eddn_journal': 'eddn_journal_signals',
    'eddn_scan': 'eddn_journal_signals',
    'eddn_signals': 'eddn_journal_signals',
    'live_edsm': 'live_edsm_diagnostics',
    'edsm_live': 'live_edsm_diagnostics',
    'edsm_system_api': 'live_edsm_diagnostics',
}

SOURCE_CLASS_BY_ADAPTER = {
    'edsm_nightly_stations': SOURCE_CLASS_SEMI_STABLE,
    'edsm_nightly_bodies': SOURCE_CLASS_SEMI_STABLE,
    'spansh_dump': SOURCE_CLASS_STABLE,
    'eddn_market_data': SOURCE_CLASS_VOLATILE,
    'eddn_journal_signals': SOURCE_CLASS_SEMI_STABLE,
    'live_edsm_diagnostics': SOURCE_CLASS_DIAGNOSTIC_ONLY,
}

PERMANENT_COLONY_SLOT_STATION_TYPES = {
    'Coriolis',
    'Orbis',
    'Ocellus',
    'Outpost',
    'AsteroidBase',
    'PlanetaryPort',
    'PlanetaryOutpost',
}

TRANSIENT_NON_SLOT_STATION_TYPES = {
    'FleetCarrier',
    'MegaShip',
}

STATION_TYPE_LABELS = {
    'coriolis': 'Coriolis',
    'coriolisstarport': 'Coriolis',
    'orbis': 'Orbis',
    'orbisstarport': 'Orbis',
    'ocellus': 'Ocellus',
    'ocellusstarport': 'Ocellus',
    'outpost': 'Outpost',
    'asteroidbase': 'AsteroidBase',
    'planetaryport': 'PlanetaryPort',
    'planetaryoutpost': 'PlanetaryOutpost',
    'planetarysettlement': 'PlanetaryOutpost',
    'settlement': 'PlanetaryOutpost',
    'surfacesettlement': 'PlanetaryOutpost',
    'surfacestation': 'PlanetaryPort',
    'craterport': 'PlanetaryPort',
    'crateroutpost': 'PlanetaryOutpost',
    'fleetcarrier': 'FleetCarrier',
    'carrier': 'FleetCarrier',
    'megaship': 'MegaShip',
    'unknown': 'Unknown',
}

VOLATILE_FIELDS = {
    'distancetoarrival',
    'distance_to_arrival',
    'distancefromstarobservation',
    'distance_from_star_observation',
    'marketupdatedat',
    'market_updated_at',
    'commoditydemand',
    'commoditysupply',
    'buyprice',
    'sellprice',
}


def canonicalise_json_payload(payload: Any) -> str:
    """Return a stable JSON representation suitable for hashing/diffing."""
    return json.dumps(
        _canonical_json_value(payload),
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=True,
        allow_nan=False,
    )


def json_safe_value(value: Any) -> Any:
    """Return a JSON-native copy while preserving deterministic report content."""
    return _canonical_json_value(value)


def payload_fingerprint(payload: Any, *, algorithm: str = 'sha256') -> str:
    """Hash a JSON-compatible payload after stable canonicalisation."""
    hasher = hashlib.new(algorithm)
    hasher.update(canonicalise_json_payload(payload).encode('utf-8'))
    return hasher.hexdigest()


def idempotency_key(*parts: Any, algorithm: str = 'sha256') -> str:
    """Build a deterministic hash from typed key parts."""
    return payload_fingerprint(list(parts), algorithm=algorithm)


def source_record_hash(source: str, payload: Any) -> str:
    """Hash a source record with the adapter/source namespace included."""
    return idempotency_key('source_record', normalise_source_adapter(source), payload)


def source_file_key(source: str, source_file: str | PurePath, *, file_sha256: str | None = None) -> str:
    """Build a stable source-file identity from source, file name, and content hash."""
    file_name = PurePath(str(source_file)).name
    return idempotency_key('source_file', normalise_source_adapter(source), file_name, file_sha256)


def normalise_source_adapter(source: str | None) -> str:
    """Normalise known source/adapter labels into stable warehouse names."""
    text = str(source or '').strip().lower()
    text = re.sub(r'[^a-z0-9]+', '_', text).strip('_')
    if not text:
        return 'unknown_source'
    return SOURCE_ALIASES.get(text, text)


def classify_source_adapter(source: str | None) -> str:
    """Classify the overall stability of an external source adapter."""
    adapter = normalise_source_adapter(source)
    return SOURCE_CLASS_BY_ADAPTER.get(adapter, SOURCE_CLASS_SEMI_STABLE)


def classify_source_field(source: str | None, field_name: str | None) -> str:
    """Classify a source field's stability, preserving volatile observations."""
    adapter_class = classify_source_adapter(source)
    if adapter_class == SOURCE_CLASS_DIAGNOSTIC_ONLY:
        return adapter_class
    field_key = re.sub(r'[^a-z0-9]+', '', str(field_name or '').lower())
    snake_key = re.sub(r'[^a-z0-9]+', '_', str(field_name or '').lower()).strip('_')
    if field_key in VOLATILE_FIELDS or snake_key in VOLATILE_FIELDS:
        return SOURCE_CLASS_VOLATILE
    return adapter_class


def normalise_source_run_metadata(
    *,
    source: str,
    adapter_name: str,
    adapter_version: str = ADAPTER_VERSION,
    source_kind: str = 'offline_snapshot',
    source_file_keys: Sequence[str] = (),
    run_label: str | None = None,
    dry_run: bool = True,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build deterministic source-run metadata without wall-clock timestamps."""
    adapter = normalise_source_adapter(source)
    file_keys = sorted(str(value) for value in source_file_keys)
    run_key = idempotency_key('source_run', adapter, adapter_name, adapter_version, source_kind, file_keys, run_label)
    return {
        'source_run_key': run_key,
        'source': adapter,
        'adapter_name': adapter_name,
        'adapter_version': adapter_version,
        'source_kind': source_kind,
        'source_class': classify_source_adapter(adapter),
        'run_label': run_label,
        'dry_run': bool(dry_run),
        'metadata': dict(metadata or {}),
    }


def normalise_source_file_metadata(
    *,
    source: str,
    source_file: str | PurePath,
    file_sha256: str | None,
    file_size_bytes: int | None,
    source_updated_at: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build deterministic source-file metadata for reports or future DB rows."""
    file_name = PurePath(str(source_file)).name
    return {
        'source_file_key': source_file_key(source, file_name, file_sha256=file_sha256),
        'source_file_name': file_name,
        'source_path': str(source_file),
        'file_sha256': file_sha256,
        'file_size_bytes': file_size_bytes,
        'compression': 'gzip' if file_name.endswith('.gz') else None,
        'content_type': 'application/json',
        'source_updated_at': source_updated_at,
        'metadata': dict(metadata or {}),
    }


def build_raw_record(
    *,
    source: str,
    source_run: Mapping[str, Any],
    source_file: Mapping[str, Any],
    record_index: int,
    payload: Mapping[str, Any],
    source_updated_at: str | None = None,
) -> dict[str, Any]:
    """Build a JSON-safe raw-record plan row."""
    record_hash = source_record_hash(source, payload)
    return {
        'source_run_key': source_run.get('source_run_key'),
        'source_file_key': source_file.get('source_file_key'),
        'record_index': record_index,
        'source_record_key': idempotency_key(
            'source_record_key',
            source_run.get('source_run_key'),
            source_file.get('source_file_key'),
            record_index,
            record_hash,
        ),
        'source_record_hash': record_hash,
        'source_updated_at': source_updated_at,
        'raw_payload': dict(payload),
        'validation_status': 'accepted',
        'validation_warnings': [],
    }


def validate_staging_record(
    record: Mapping[str, Any],
    *,
    required_fields: Sequence[str] = (),
    required_any: Sequence[Sequence[str]] = (),
) -> dict[str, Any]:
    """Validate a staging row without raising on ordinary source defects."""
    warnings: list[dict[str, Any]] = []
    for field in required_fields:
        if _is_missing(record.get(field)):
            warnings.append({'field': field, 'reason': 'missing_required_field'})
    for field_group in required_any:
        if not any(not _is_missing(record.get(field)) for field in field_group):
            warnings.append({
                'fields': list(field_group),
                'reason': 'missing_required_field_group',
            })
    return {
        'valid': not warnings,
        'warnings': warnings,
    }


def build_report_skeleton(
    *,
    schema_version: str,
    source_run: Mapping[str, Any],
    source_file: Mapping[str, Any] | None = None,
    raw_records: Sequence[Mapping[str, Any]] = (),
    staged_rows: Sequence[Mapping[str, Any]] = (),
    planned_rows: Sequence[Mapping[str, Any]] = (),
    skipped_rows: Sequence[Mapping[str, Any]] = (),
    conflicts: Sequence[Mapping[str, Any]] = (),
    warnings: Sequence[Mapping[str, Any]] = (),
    summary_extra: Mapping[str, Any] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Build a deterministic versioned dry-run/import-plan report."""
    if schema_version not in REPORT_SCHEMAS:
        raise ValueError(f'Unsupported enrichment report schema: {schema_version}')

    raw_record_rows = _sort_rows(raw_records)
    staged = _sort_rows(staged_rows)
    planned = _sort_rows(planned_rows)
    skipped = _sort_rows(skipped_rows)
    conflict_rows = _sort_rows(conflicts)
    warning_rows = _sort_rows(warnings)
    duplicate_groups = _source_record_duplicate_groups(raw_record_rows)
    summary = {
        'source_runs': 1,
        'source_files': 1 if source_file else 0,
        'raw_records': len(raw_record_rows),
        'staged_rows': len(staged),
        'planned_rows': len(planned),
        'skipped_rows': len(skipped),
        'conflicts': len(conflict_rows),
        'warnings': len(warning_rows),
        'skipped_row_reasons': _distribution(skipped, 'reason'),
        'warning_reasons': _distribution(warning_rows, 'reason'),
        'duplicate_source_record_hashes': len(duplicate_groups),
        'duplicate_source_records': sum(group['count'] - 1 for group in duplicate_groups),
        'confidence_distribution': _distribution(staged + planned, 'confidence'),
        'freshness_distribution': _distribution(staged + planned, 'freshness_class'),
        'source_class_distribution': _distribution(staged + planned, 'source_class'),
    }
    summary.update(dict(summary_extra or {}))
    return {
        'schema_version': schema_version,
        'dry_run': bool(dry_run),
        'source_run': dict(source_run),
        'source_file': dict(source_file) if source_file else None,
        'summary': summary,
        'raw_records_planned': raw_record_rows,
        'staged_rows': staged,
        'planned_rows': planned,
        'skipped_rows': skipped,
        'conflicts': conflict_rows,
        'warnings': warning_rows,
        'source_record_duplicate_groups': duplicate_groups,
    }


def build_enrichment_snapshot_load_plan(**kwargs: Any) -> dict[str, Any]:
    return build_report_skeleton(schema_version=ENRICHMENT_SNAPSHOT_LOAD_PLAN_SCHEMA, **kwargs)


def build_station_snapshot_enrichment_dry_run(**kwargs: Any) -> dict[str, Any]:
    return build_report_skeleton(schema_version=STATION_SNAPSHOT_DRY_RUN_SCHEMA, **kwargs)


def build_body_ring_enrichment_dry_run_skeleton(**kwargs: Any) -> dict[str, Any]:
    return build_report_skeleton(schema_version=BODY_RING_DRY_RUN_SCHEMA, **kwargs)


def build_mission_intelligence_dry_run(**kwargs: Any) -> dict[str, Any]:
    return build_report_skeleton(schema_version=MISSION_INTELLIGENCE_DRY_RUN_SCHEMA, **kwargs)


def build_exploration_intelligence_dry_run(**kwargs: Any) -> dict[str, Any]:
    return build_report_skeleton(schema_version=EXPLORATION_INTELLIGENCE_DRY_RUN_SCHEMA, **kwargs)


def build_colonisation_economy_intelligence_dry_run(**kwargs: Any) -> dict[str, Any]:
    return build_report_skeleton(schema_version=COLONISATION_ECONOMY_DRY_RUN_SCHEMA, **kwargs)


def build_alert_candidate_dry_run(**kwargs: Any) -> dict[str, Any]:
    return build_report_skeleton(schema_version=ALERT_CANDIDATE_DRY_RUN_SCHEMA, **kwargs)


def first_present(record: Mapping[str, Any], *field_names: str) -> Any:
    """Return the first non-null value from a source record."""
    for field_name in field_names:
        if field_name in record and record[field_name] is not None:
            return record[field_name]
    return None


def read_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalise_source_updated_at_value(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for field_name in ('information', 'market', 'shipyard', 'outfitting'):
            candidate = read_text(value.get(field_name))
            if candidate is not None:
                return candidate
        return None
    if isinstance(value, (list, tuple, set)):
        return None
    return read_text(value)


def read_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def read_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def normalise_station_type_label(station_type: Any) -> str | None:
    token = ''.join(ch for ch in str(station_type or '').lower() if ch.isalnum())
    if not token:
        return None
    return STATION_TYPE_LABELS.get(token, 'Unknown')


def classify_station_type_evidence(station_type: Any) -> dict[str, Any]:
    normalised = normalise_station_type_label(station_type)
    if normalised is None:
        classification = 'missing'
    elif normalised in TRANSIENT_NON_SLOT_STATION_TYPES:
        classification = 'transient_non_slot'
    elif normalised in PERMANENT_COLONY_SLOT_STATION_TYPES:
        classification = 'permanent_colony_slot'
    else:
        classification = 'unknown'
    return {
        'station_type_raw': read_text(station_type),
        'station_type_normalized': normalised,
        'station_type_classification': classification,
        'station_type_preserved_as_source_label': True,
    }


def normalise_json_array(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def normalise_edsm_body_snapshot_record(
    record: Mapping[str, Any],
    *,
    source: str,
    raw_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalise one offline EDSM body snapshot record into staging evidence."""
    source_hash = (
        str(raw_record.get('source_record_hash'))
        if raw_record and raw_record.get('source_record_hash') is not None
        else source_record_hash(source, record)
    )
    source_updated_at = source_updated_at_from_record(record)
    signals = first_present(record, 'signals', 'signalsCount', 'signals_count')
    materials = first_present(record, 'materials', 'solidComposition', 'solid_composition')
    return {
        'source_run_key': raw_record.get('source_run_key') if raw_record else None,
        'source_file_key': raw_record.get('source_file_key') if raw_record else None,
        'source_record_key': raw_record.get('source_record_key') if raw_record else None,
        'source_record_hash': source_hash,
        'system_id64': read_int(first_present(record, 'systemId64', 'system_id64', 'systemAddress', 'id64')),
        'system_name': read_text(first_present(record, 'systemName', 'system_name', 'system')),
        'source_body_id': read_int(first_present(record, 'bodyId', 'bodyID', 'body_id', 'id')),
        'body_name': read_text(first_present(record, 'name', 'bodyName', 'body_name')),
        'body_type': read_text(first_present(record, 'type', 'bodyType', 'body_type')),
        'subtype': read_text(first_present(record, 'subType', 'subtype', 'sub_type')),
        'distance_to_arrival': read_float(first_present(
            record,
            'distanceToArrival',
            'distance_to_arrival',
            'distanceFromArrival',
            'distance_from_star',
        )),
        'is_main_star': read_bool(first_present(record, 'isMainStar', 'is_main_star', 'mainStar')),
        'is_landable': read_bool(first_present(record, 'isLandable', 'is_landable', 'landable')),
        'is_terraformable': read_bool(first_present(
            record,
            'isTerraformable',
            'is_terraformable',
            'terraformable',
        )),
        'estimated_scan_value': read_int(first_present(
            record,
            'estimatedScanValue',
            'estimated_scan_value',
            'scanValue',
        )),
        'estimated_mapping_value': read_int(first_present(
            record,
            'estimatedMappingValue',
            'estimated_mapping_value',
            'mappingValue',
        )),
        'signals': dict(signals) if isinstance(signals, Mapping) else {},
        'materials': dict(materials) if isinstance(materials, Mapping) else {},
        'source_class': classify_source_adapter(source),
        'confidence': 'source_body_snapshot',
        'freshness_class': 'source_updated_at' if source_updated_at else 'file_snapshot',
        'source_updated_at': source_updated_at,
        'raw_payload': dict(record),
        'provenance': {
            'source': normalise_source_adapter(source),
            'source_body_id_is_canonical_body_id': False,
            'canonical_write_allowed': False,
        },
    }


def normalise_edsm_body_ring_snapshot_records(
    record: Mapping[str, Any],
    *,
    source: str,
    body_row: Mapping[str, Any] | None = None,
    raw_record: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Normalise source-only ring rows from one offline EDSM body record."""
    rings = first_present(record, 'rings', 'Rings')
    if not isinstance(rings, Sequence) or isinstance(rings, (str, bytes, bytearray)):
        return []

    body = body_row or normalise_edsm_body_snapshot_record(record, source=source, raw_record=raw_record)
    raw_hash = (
        str(raw_record.get('source_record_hash'))
        if raw_record and raw_record.get('source_record_hash') is not None
        else source_record_hash(source, record)
    )
    rows: list[dict[str, Any]] = []
    for ring_index, ring in enumerate(rings):
        if not isinstance(ring, Mapping):
            continue
        ring_payload = dict(ring)
        ring_hash = source_record_hash(
            source,
            {
                'body_source_record_hash': raw_hash,
                'ring_index': ring_index,
                'ring': ring_payload,
            },
        )
        ring_name = read_text(first_present(ring, 'name', 'ringName', 'ring_name', 'Name'))
        rows.append({
            'source_run_key': body.get('source_run_key'),
            'source_file_key': body.get('source_file_key'),
            'source_record_key': idempotency_key(
                'ring_source_record_key',
                body.get('source_record_key'),
                ring_index,
                ring_hash,
            ),
            'source_record_hash': ring_hash,
            'raw_body_source_record_hash': raw_hash,
            'system_id64': body.get('system_id64'),
            'system_name': body.get('system_name'),
            'source_body_id': body.get('source_body_id'),
            'body_name': body.get('body_name'),
            'ring_name': ring_name,
            'ring_type': read_text(first_present(ring, 'type', 'ringType', 'ring_type')),
            'ring_class': read_text(first_present(ring, 'class', 'ringClass', 'ring_class', 'RingClass')),
            'mass_mt': read_float(first_present(ring, 'massMT', 'mass_mt', 'mass', 'MassMT')),
            'inner_radius': read_float(first_present(
                ring,
                'innerRadius',
                'inner_radius',
                'innerRad',
                'InnerRad',
            )),
            'outer_radius': read_float(first_present(
                ring,
                'outerRadius',
                'outer_radius',
                'outerRad',
                'OuterRad',
            )),
            'association_status': 'source_only',
            'source_class': classify_source_adapter(source),
            'confidence': 'source_ring_payload',
            'freshness_class': body.get('freshness_class'),
            'source_updated_at': body.get('source_updated_at'),
            'raw_payload': {
                'body': dict(record),
                'ring': ring_payload,
                'ring_index': ring_index,
            },
            'provenance': {
                'source': normalise_source_adapter(source),
                'source_only_ring_evidence': True,
                'canonical_write_allowed': False,
            },
        })
    return rows


def source_updated_at_from_record(record: Mapping[str, Any]) -> str | None:
    return normalise_source_updated_at_value(first_present(
        record,
        'updatedAt',
        'updated_at',
        'updateTime',
        'lastUpdate',
        'lastUpdated',
        'date',
    ))


def read_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {'true', 't', '1', 'yes', 'y'}:
            return True
        if text in {'false', 'f', '0', 'no', 'n'}:
            return False
    return None


def _distribution(rows: Sequence[Mapping[str, Any]], field_name: str) -> dict[str, int]:
    counter = Counter(str(row.get(field_name)) for row in rows if row.get(field_name) is not None)
    return dict(sorted(counter.items()))


def _source_record_duplicate_groups(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        record_hash = row.get('source_record_hash')
        if record_hash is None:
            continue
        grouped.setdefault(str(record_hash), []).append(row)

    duplicates = []
    for record_hash, group in grouped.items():
        if len(group) < 2:
            continue
        duplicates.append({
            'source_record_hash': record_hash,
            'count': len(group),
            'record_indexes': sorted(
                int(row['record_index'])
                for row in group
                if isinstance(row.get('record_index'), int)
            ),
            'handling': 'reported_only_dry_run; explicit staging writes upsert by source_record_hash',
        })
    return sorted(duplicates, key=canonicalise_json_payload)


def _sort_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted((dict(row) for row in rows), key=canonicalise_json_payload)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_canonical_json_value(item) for item in value), key=canonicalise_json_payload)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
