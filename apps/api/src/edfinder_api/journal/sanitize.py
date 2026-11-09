"""V3 journal sanitization: A1-A14 allowlist mapping (Task 4).

Maps a normalized ``v3_private.journal_event`` row (with its canonical
event key and stripped payload) to a sanitized export observation, or None
for excluded event types. Authority: the A1-A14 sanitization table in the
implementation plan and the V3 journal-intelligence decision doc's
ED-Finder -> EDRE boundary (sanitization model). Fail-closed: any field not
listed in the A1-A14 table is never copied into the output.

Hard exclusions enforced here: Latitude/Longitude (default excluded, even
when present in the event key or payload), credits (Value/Bonus/TotalValue),
MarketID, Commander name/FID, and the entire rows of travel/identity events
(return None). The output carries ``observed_week`` (ISO-8601 week bucket)
only — never an exact timestamp. ``body_id`` is the journal body id from the
event key — never ed-finder's ``bodies.id``; this module has no access to
canonical tables at all.

Row contract (dict)::

    {
        "event_type": str,
        "event_key": dict | str(json)      # canonical key, identity.py output
        "event_payload": dict | str(json), # allowlist-stripped payload
        "event_timestamp": datetime,       # tz-aware UTC (naive -> assumed UTC)
        "source_record_hash": bytes | str  # 32-byte hash; hex string accepted
    }

Exported event set (everything else returns None): Scan, FSSBodySignals,
SAASignalsFound, SAAScanComplete, CodexEntry, ScanOrganic, SellOrganicData,
FSSDiscoveryScan, FSSAllBodiesFound. FSSDiscoveryScan/FSSAllBodiesFound are
system-level observations with no body fields (recorded deviation).

Limit interaction: the export builder's ``limit`` applies to RAW rows
BEFORE this module's exclusion — an account with excluded event types
exports fewer observations than the limit, never more (the applied limit is
persisted as ``manifest.limit``).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

OBSERVATION_NAMESPACE_UUID = UUID('6ba7b811-9dad-11d1-80b4-00c04fd430c8')

EVIDENCE_QUALITY = 'OBSERVED_PERSONAL'

# Event types never exported; sanitize_observation returns None for these.
EXCLUDED_EVENT_TYPES = frozenset({
    'FSDJump', 'CarrierJump', 'Location', 'Touchdown', 'Liftoff', 'ApproachBody',
    'LeaveBody', 'Disembark', 'Embark', 'Screenshot', 'Docked', 'Fileheader',
    'LoadGame', 'Commander', 'Died', 'Resurrect', 'SellExplorationData',
    'MultiSellExplorationData', 'FSDTarget', 'NavRoute', 'NavRouteClear',
})

# The exported set = the 30-event allowlist minus EXCLUDED_EVENT_TYPES.
EXPORTED_EVENT_TYPES = frozenset({
    'Scan', 'FSSBodySignals', 'SAASignalsFound', 'SAAScanComplete',
    'CodexEntry', 'ScanOrganic', 'SellOrganicData', 'FSSDiscoveryScan',
    'FSSAllBodiesFound',
})

# Event types whose payload contributes a normalized body_environment block.
_ENVIRONMENT_EVENT_TYPES = frozenset({'Scan', 'FSSBodySignals', 'SAASignalsFound'})

# Event types whose payload contributes a signals/genuses block.
_SIGNALS_EVENT_TYPES = frozenset({'FSSBodySignals', 'SAASignalsFound'})

# body_environment: normalized key -> journal payload field(s). Journal
# strings/floats pass through verbatim except the two documented unit
# conversions below (arbitrated shapes, cre-export-schema.md):
#   radius_km         = journal Radius metres / 1000, rounded 4dp
#   surface_gravity_g = journal SurfaceGravity m/s^2 / 9.80665, rounded 6dp
# ``body_class`` comes from PlanetClass, ``star_type`` from StarType
# (arbitration: single source per key; no BodyClass conflation).
_ENVIRONMENT_FIELD_MAP: dict[str, tuple[str, ...]] = {
    'body_class': ('PlanetClass',),
    'star_type': ('StarType',),
    'atmosphere': ('Atmosphere',),
    'volcanism': ('Volcanism',),
    'mass_em': ('MassEM',),
    'radius_km': ('Radius',),
    'surface_gravity_g': ('SurfaceGravity',),
    'surface_temperature_k': ('SurfaceTemperature',),
    'landable': ('Landable',),
    'terraform_state': ('TerraformState',),
}

# Arbitrated unit conversions (divisor, decimal places).
_RADIUS_M_TO_KM = (1000.0, 4)
_SURFACE_GRAVITY_MS2_TO_G = (9.80665, 6)


def minimize_time(dt: datetime) -> str:
    """ISO-8601 week bucket for a timestamp: ``f"{iso_year}-W{iso_week:02d}"``.

    The exact timestamp never leaves this function; consumers get the week
    only. Naive datetimes are assumed UTC (matching the journal identity
    contract). ISO-8601 weeks can belong to a different calendar year at
    the edges (e.g. 2027-01-01 -> "2026-W53").
    """
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        # Naive timestamps are assumed UTC (journal identity contract).
        dt = dt.replace(tzinfo=timezone.utc)
    iso = dt.isocalendar()
    return f'{iso.year}-W{iso.week:02d}'


def _as_dict(value: Any, *, field: str) -> dict:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f'{field} is not valid JSON: {exc}') from exc
        if not isinstance(decoded, dict):
            raise ValueError(f'{field} must decode to a JSON object')
        return decoded
    if not isinstance(value, dict):
        raise ValueError(f'{field} must be a dict or JSON object string')
    return value


def _canonical_key_json(event_key: dict) -> str:
    """Deterministic JSON of the canonical event key (sorted keys, compact
    separators, ascii-escaped) — the uuid5 namespace-string input."""
    return json.dumps(event_key, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _source_hash_hex(source_record_hash: Any) -> str:
    if isinstance(source_record_hash, (bytes, bytearray)):
        return bytes(source_record_hash).hex()
    if isinstance(source_record_hash, str):
        return source_record_hash.lower()
    raise ValueError(f'source_record_hash must be bytes or hex str, got {type(source_record_hash).__name__}')


def _converted_number(value: object, *, divisor: float, precision: int, field: str) -> float:
    """Journal numeric -> converted unit (fail-closed on non-numeric)."""
    if isinstance(value, bool):
        raise ValueError(f'{field} must be numeric, got {value!r}')
    if not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'{field} must be numeric, got {value!r}') from exc
    return round(float(value) / divisor, precision)


def _environment_block(payload: dict) -> dict | None:
    """Normalized body/environment facts from Scan/FSSBodySignals/
    SAASignalsFound payloads; absent keys are omitted (fail-closed). The two
    unit-converted keys (radius_km, surface_gravity_g) are converted per the
    arbitrated shapes; every other value passes through journal-native."""
    block: dict[str, Any] = {}
    for normalized_name, journal_names in _ENVIRONMENT_FIELD_MAP.items():
        for journal_name in journal_names:
            if journal_name in payload and payload[journal_name] is not None:
                if normalized_name == 'radius_km':
                    divisor, precision = _RADIUS_M_TO_KM
                    block[normalized_name] = _converted_number(
                        payload[journal_name], divisor=divisor, precision=precision,
                        field=journal_name,
                    )
                elif normalized_name == 'surface_gravity_g':
                    divisor, precision = _SURFACE_GRAVITY_MS2_TO_G
                    block[normalized_name] = _converted_number(
                        payload[journal_name], divisor=divisor, precision=precision,
                        field=journal_name,
                    )
                else:
                    block[normalized_name] = payload[journal_name]
                break
    return block or None


def _signals_block(event_type: str, payload: dict) -> dict | None:
    """Signals and Genuses for FSSBodySignals / SAASignalsFound (arbitrated
    shapes, cre-export-schema.md): ``signals`` entries are PROJECTED down to
    ``{"Type": str, "Count": int}`` only (journal extras like
    ``Type_Localised`` are dropped); ``genuses`` is a SEPARATE optional
    string-array field of canonical genus tokens from ``SAASignalsFound.
    Genuses`` (localised names are not allowlisted). Null values inside
    entries are dropped — the EDRE consumer schema rejects JSON nulls, so no
    null may ever be emitted."""
    block: dict[str, Any] = {}
    if event_type not in _SIGNALS_EVENT_TYPES:
        return None
    if 'Signals' in payload and payload['Signals'] is not None:
        signals = payload['Signals']
        if not isinstance(signals, list):
            raise ValueError('payload Signals must be a list')
        projected: list[dict[str, Any]] = []
        for entry in signals:
            if not isinstance(entry, dict):
                continue
            item: dict[str, Any] = {}
            if entry.get('Type') is not None:
                item['Type'] = entry['Type']
            if entry.get('Count') is not None:
                item['Count'] = entry['Count']
            if item:
                projected.append(item)
        if projected:
            block['signals'] = projected
    if 'Genuses' in payload and payload['Genuses'] is not None:
        genuses = payload['Genuses']
        if not isinstance(genuses, list):
            raise ValueError('payload Genuses must be a list')
        block['genuses'] = [
            entry['Genus']
            for entry in genuses
            if isinstance(entry, dict) and entry.get('Genus') is not None
        ]
    return block or None


def _sale_block(payload: dict) -> dict | None:
    """SellOrganicData sale OBJECT per the arbitrated shape (cre-export-
    schema.md): ``{"count": int, "items": [{"genus", "species", "variant?"}]}``
    where ``count`` = the NUMBER of BioData items (the journal carries a
    list, not per-item counts) and each item carries genus/species plus
    variant when present (variant omitted otherwise, never null). MarketID,
    Value/Bonus/TotalValue and localised Name are hard-excluded; per-item
    journal ``Count`` is never emitted."""
    bio_data = payload.get('BioData')
    if bio_data is None:
        return None
    if not isinstance(bio_data, list):
        raise ValueError('payload BioData must be a list')
    items: list[dict] = []
    for item in bio_data:
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {}
        for token_key in ('Genus', 'Species', 'Variant'):
            if item.get(token_key) is not None:
                entry[token_key.lower()] = item[token_key]
        if entry:
            items.append(entry)
    if not items:
        return None
    return {'count': len(items), 'items': items}


def sanitize_observation(event_row: dict) -> dict | None:
    """Map a journal_event row to a sanitized export observation (A1-A14).

    Returns None for excluded event types (travel/identity/credit/route
    events). Raises ValueError on structurally invalid rows. The output dict
    contains ONLY the A1-A14 allowlisted fields; hard exclusions (lat/long,
    credits, MarketID, commander identity) can never appear.
    """
    event_type = event_row['event_type']
    if event_type not in EXPORTED_EVENT_TYPES:
        return None

    event_key = _as_dict(event_row['event_key'], field='event_key')
    payload = _as_dict(event_row['event_payload'], field='event_payload')
    event_timestamp = event_row['event_timestamp']

    observation: dict[str, Any] = {
        'observation_id': str(uuid.uuid5(
            OBSERVATION_NAMESPACE_UUID,
            f'{event_type}:{_canonical_key_json(event_key)}',
        )),
        'observation_type': event_type,
        'observed_week': minimize_time(event_timestamp),
        'source_event_sha256': _source_hash_hex(event_row['source_record_hash']),
        'evidence_quality': EVIDENCE_QUALITY,
    }

    # system / body identity from the canonical event key. Canonical keys
    # already carry decimal strings (identity contract); str() is a
    # fail-safe so the export shape is always decimal-string regardless of
    # the input representation. Null values are treated as absent.
    system_id64: str | None = None
    if event_key.get('SystemAddress') is not None:
        system_id64 = str(event_key['SystemAddress'])
    body_id: str | None = None
    if event_key.get('BodyID') is not None:
        body_id = str(event_key['BodyID'])

    # system_name: observed value only (payload, never derived). Fallback
    # chain covers every exported event type's journal-native name field:
    # SystemName (FSSDiscoveryScan/FSSAllBodiesFound), StarSystem
    # (Scan/ScanOrganic/SAAScanComplete/FSSBodySignals/SAASignalsFound/
    # CodexEntry), and System (CodexEntry's journal field — kept in the
    # allowlist so the fail-closed name requirement is satisfiable).
    system_name = payload.get('SystemName')
    if system_name is None:
        system_name = payload.get('StarSystem')
    if system_name is None:
        system_name = payload.get('System')

    # EDRE contract (schema review directive): system_id64 and system_name
    # are ALWAYS present for every exported observation type EXCEPT
    # SellOrganicData, which omits both. A row that cannot satisfy the
    # contract is an integrity anomaly — fail closed rather than emit a
    # schema-invalid observation.
    if event_type != 'SellOrganicData':
        if system_id64 is None:
            raise ValueError(
                f'{event_type} export requires event_key SystemAddress '
                '(contract: system_id64 always present)'
            )
        if system_name is None:
            raise ValueError(
                f'{event_type} export requires a payload system name '
                '(SystemName/StarSystem) (contract: system_name always present)'
            )
        observation['system_id64'] = system_id64
        observation['system_name'] = system_name
    # else: SellOrganicData has no system identity (key = MarketID +
    # BioDataSha256) and deliberately OMITS both system fields.

    if body_id is not None:
        observation['body_id'] = body_id

    # body_name: payload BodyName, else string form of payload Body.
    body_name = payload.get('BodyName')
    if body_name is None and payload.get('Body') is not None:
        body_name = str(payload['Body'])
    if body_name is not None:
        observation['body_name'] = body_name

    # Per-type allowlisted blocks.
    if event_type in _ENVIRONMENT_EVENT_TYPES:
        env = _environment_block(payload)
        if env is not None:
            observation['body_environment'] = env
    if event_type in _SIGNALS_EVENT_TYPES:
        signals = _signals_block(event_type, payload)
        if signals is not None:
            observation.update(signals)
    if event_type == 'CodexEntry':
        if payload.get('Region') is not None:
            observation['codex_region'] = payload['Region']
        if event_key.get('EntryID') is not None:
            observation['codex_entry_id'] = event_key['EntryID']
    if event_type == 'ScanOrganic':
        for token_key in ('Genus', 'Species', 'Variant'):
            if payload.get(token_key) is not None:
                observation[token_key.lower()] = payload[token_key]
    if event_type == 'SellOrganicData':
        sale = _sale_block(payload)
        if sale is not None:
            observation['sale'] = sale

    # Game version/build attach (client V3 parser rolling state).
    if payload.get('GameVersion') is not None:
        observation['game_version'] = payload['GameVersion']
    if payload.get('GameBuild') is not None:
        observation['game_build'] = payload['GameBuild']

    return observation
