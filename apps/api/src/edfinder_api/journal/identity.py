"""V3.0 semantic event identity for account-scoped journal events.

Implements the V3.0 event identity contract table (single source of truth
in the 2026-08-28 implementation plan, backed by the journal-intelligence
decision doc): keys are canonical JSON objects built server-side from the
event payload, with record timestamps deliberately excluded for stable
identity classes. Travel-chronology events (FSDJump/CarrierJump) are the
recorded exception: EventTimestamp is part of the key and required.

Verified commander imports extend the key for ``Scan`` and ``Docked`` with
the observation itself (``RecordHash`` + ``ObservedAt``); that extension lives
in ``store.OBSERVATION_HISTORY_EVENTS`` because it depends on verified
ownership, not on the payload alone.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from edfinder_api.journal.event_contract import JOURNAL_EVENT_ALLOWLIST

_UINT64_MAX = (1 << 64) - 1

# Content-addressed events: the identity is the raw journal line hash.
_CONTENT_ADDRESSED_EVENTS = frozenset({
    'Fileheader', 'LoadGame', 'Commander', 'Died', 'Resurrect',
    'SellExplorationData', 'MultiSellExplorationData', 'FSDTarget',
    'NavRoute', 'NavRouteClear',
})

# Travel-chronology exception: repeated jumps to one system are distinct
# personal-history facts, so EventTimestamp is part of the key.
_TRAVEL_CHRONOLOGY_EVENTS = frozenset({'FSDJump', 'CarrierJump'})

# Semantic key fields per event type, in canonical key order.
_SEMANTIC_KEY_FIELDS: dict[str, tuple[str, ...]] = {
    'CodexEntry': ('SystemAddress', 'BodyID', 'EntryID', 'Latitude', 'Longitude'),
    'ScanOrganic': ('SystemAddress', 'BodyID', 'Genus', 'Species', 'Variant', 'ScanType'),
    'Scan': ('SystemAddress', 'BodyID'),
    'FSSBodySignals': ('SystemAddress', 'BodyID'),
    'SAASignalsFound': ('SystemAddress', 'BodyID'),
    'SAAScanComplete': ('SystemAddress', 'BodyID'),
    'Touchdown': ('SystemAddress', 'BodyID', 'Latitude', 'Longitude'),
    'Liftoff': ('SystemAddress', 'BodyID', 'Latitude', 'Longitude'),
    'ApproachBody': ('SystemAddress', 'BodyID'),
    'LeaveBody': ('SystemAddress', 'BodyID'),
    'Location': ('SystemAddress', 'BodyID', 'Latitude', 'Longitude'),
    'Disembark': ('SystemAddress', 'BodyID'),
    'Embark': ('SystemAddress', 'BodyID'),
    'Screenshot': ('SystemAddress', 'BodyID', 'Filename'),
    'Docked': ('SystemAddress', 'StationName'),
    'FSSDiscoveryScan': ('SystemAddress',),
    'FSSAllBodiesFound': ('SystemAddress',),
    'SellOrganicData': ('MarketID', 'BioDataSha256'),
}


def _uint64_decimal(value: object, *, field: str) -> str:
    """Canonicalize a uint64 as a decimal string.

    Fail-closed on non-integral floats: ``int(1.5)`` truncates to ``1`` and
    would silently collide with the real uint64 ``1`` (the review's finding)
    — a fractional SystemAddress/BodyID from a buggy or malicious client is
    rejected instead. Integral floats (``1.0``) are accepted.
    """
    if isinstance(value, bool):
        raise ValueError(f'{field} must be a uint64, got bool')
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f'{field} must be an integral uint64, got {value!r}')
        number = int(value)
    else:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'{field} must be a uint64, got {value!r}') from exc
    if number < 0 or number > _UINT64_MAX:
        raise ValueError(f'{field} out of uint64 range: {number}')
    return str(number)


def _round4_decimal(value: object, *, field: str) -> str:
    """Canonicalize a coordinate rounded to 4 decimals."""
    if isinstance(value, bool):
        raise ValueError(f'{field} must be numeric, got bool')
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field} must be numeric, got {value!r}') from exc
    return str(round(number, 4))


def _trimmed_string(value: object, *, field: str) -> str:
    """Canonicalize a string field (trimmed, verbatim content)."""
    if not isinstance(value, str):
        raise ValueError(f'{field} must be a string, got {value!r}')
    return value.strip()


def _normalize_event_timestamp(value: object) -> datetime:
    """Parse/UTC-normalize an event timestamp (naive -> assumed UTC)."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime):
        raise ValueError(f'event_timestamp must be an ISO-8601 timestamp, got {value!r}')
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _event_timestamp_key(value: object) -> str:
    normalized = _normalize_event_timestamp(value)
    return normalized.isoformat(timespec='seconds').replace('+00:00', 'Z')


def _source_hash_hex(source_record_hash: object) -> str:
    if isinstance(source_record_hash, str):
        return source_record_hash.lower()
    if isinstance(source_record_hash, bytes):
        return source_record_hash.hex()
    raise ValueError(f'source_record_hash must be bytes or hex str, got {source_record_hash!r}')


def canonical_key(obj: dict) -> dict:
    """Recursive sorted-key canonicalization; integers become decimal strings.

    Lists keep their order (order is semantic for arrays like BioData);
    dict keys are sorted recursively; bool is kept distinct from int 1.
    """
    if not isinstance(obj, dict):
        raise ValueError(f'canonical_key expects a dict, got {type(obj).__name__}')
    return {key: _canonicalize(value) for key, value in sorted(obj.items())}


def _canonicalize(value: object) -> object:
    if isinstance(value, dict):
        return {key: _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    return value


def bio_data_sha256(bio_data: list[dict]) -> bytes:
    """SHA-256 of the canonical-sorted BioData array (SellOrganicData).

    The array is order-insensitive: every entry is canonicalized
    (sorted keys, ints as decimal strings) and the entries are sorted by
    their canonical JSON before hashing.
    """
    if not isinstance(bio_data, list):
        raise ValueError(f'bio_data must be a list, got {type(bio_data).__name__}')
    canonical_entries: list[dict] = []
    for entry in bio_data:
        if not isinstance(entry, dict):
            raise ValueError(f'bio_data entries must be dicts, got {type(entry).__name__}')
        canonical_entries.append(canonical_key(entry))
    canonical_entries.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(',', ':')))
    blob = json.dumps(canonical_entries, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(blob).digest()


def event_identity(
    event_type: str,
    event_payload: dict,
    source_record_hash: bytes,
    *,
    event_timestamp: object = None,
) -> dict:
    """Build the canonical semantic ``event_key`` per the V3.0 contract.

    Content-addressed events key on ``{"source_record_hash": <hex>}``.
    FSDJump/CarrierJump require ``event_timestamp`` (travel-chronology
    exception; timestamp is never alone — SystemAddress is required too).
    All other listed fields are included only when present, per the
    contract's "key shape varies by presence" rule. Raises ``ValueError``
    on unknown event types or non-canonicalizable field values.
    """
    if event_type not in JOURNAL_EVENT_ALLOWLIST:
        raise ValueError(
            f'Unknown journal event type: {event_type!r}; '
            f'allowlist = {sorted(JOURNAL_EVENT_ALLOWLIST)}',
        )
    if not isinstance(event_payload, dict):
        raise ValueError(f'event_payload must be a dict, got {type(event_payload).__name__}')

    if event_type in _CONTENT_ADDRESSED_EVENTS:
        return {'source_record_hash': _source_hash_hex(source_record_hash)}

    if event_type in _TRAVEL_CHRONOLOGY_EVENTS:
        if event_timestamp is None:
            raise ValueError(f'{event_type} requires event_timestamp (travel-chronology key)')
        if 'SystemAddress' not in event_payload:
            raise ValueError(f'{event_type} requires SystemAddress in the payload')
        return {
            'SystemAddress': _uint64_decimal(event_payload['SystemAddress'], field='SystemAddress'),
            'EventTimestamp': _event_timestamp_key(event_timestamp),
        }

    key: dict[str, str] = {}
    for field in _SEMANTIC_KEY_FIELDS[event_type]:
        if field == 'BioDataSha256':
            if 'BioData' not in event_payload:
                continue
            key[field] = bio_data_sha256(event_payload['BioData']).hex()
            continue
        if field == 'BodyID' and event_type == 'ScanOrganic' and 'BodyID' not in event_payload:
            if 'Body' not in event_payload:
                continue
            key[field] = _uint64_decimal(event_payload['Body'], field='Body')
            continue
        if field not in event_payload:
            continue
        if field in ('SystemAddress', 'BodyID', 'MarketID'):
            key[field] = _uint64_decimal(event_payload[field], field=field)
        elif field in ('Latitude', 'Longitude'):
            key[field] = _round4_decimal(event_payload[field], field=field)
        elif field == 'EntryID':
            # Contract: "EntryID -> string as-is" — no trim, no type
            # coercion beyond str() (journal EntryIDs may be numeric).
            key[field] = str(event_payload[field])
        else:
            key[field] = _trimmed_string(event_payload[field], field=field)
    return key
