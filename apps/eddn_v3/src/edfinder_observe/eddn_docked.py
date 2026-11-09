"""EDDN ``journal/1`` ``Docked`` envelope -> ``StationSighting``.

Pure functions, no I/O. Every outcome other than a sighting is a bounded
``reason`` label:

* ``Filtered`` -- a well-formed message we do not want (other schema, other
  event, fleet carrier, Legacy galaxy);
* ``SightingRejected`` -- a message that failed validation.

Privacy: the EDDN header is reduced to an explicit allowlist the moment a
frame is decoded. ``uploaderID`` is never looked up, copied, stored or logged,
and the raw message is never kept.
"""
from __future__ import annotations

import json
import re
import zlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from .sightings import SightingRejected, StationSighting, build_sighting

JOURNAL_SCHEMA = 'https://eddn.edcd.io/schemas/journal/1'
DOCKED_EVENT = 'Docked'

# The only header keys this package ever reads.
HEADER_ALLOWLIST = ('softwareName', 'softwareVersion', 'gatewayTimestamp', 'gameversion', 'gamebuild')

# StationType values that identify player-owned carriers (EDDN spells it
# "FleetCarrier"; Spansh dumps use display names). Compared after removing
# spaces, hyphens and underscores, case-insensitively.
_CARRIER_TYPES = frozenset({'fleetcarrier', 'drakeclasscarrier', 'squadroncarrier'})

_ECONOMY_TOKEN = re.compile(r'^\$economy_(?P<name>[^;]{1,120});$')
_LIVE_GAME_VERSION = re.compile(r'^(?P<major>\d{1,3})\.\d')


# Docked fields that feed the state hash and must therefore be present.
_STATE_KEYS = ('StationType', 'StationEconomies', 'StationServices', 'LandingPads')


class Filtered(Exception):
    """A well-formed message this worker deliberately ignores."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def decode_frame(raw: bytes, *, max_bytes: int) -> dict:
    """zlib-decompress and parse one relay frame, bounded to ``max_bytes``.

    Returns a new dict holding only ``$schemaRef``, the allowlisted header and
    ``message``. The original header object is discarded unread beyond the
    allowlist.
    """
    if not isinstance(raw, (bytes, bytearray, memoryview)) or len(raw) > max_bytes:
        raise SightingRejected('oversize')
    inflater = zlib.decompressobj()
    try:
        data = inflater.decompress(bytes(raw), max_bytes + 1)
    except zlib.error as exc:
        raise SightingRejected('decode') from exc
    if len(data) > max_bytes or inflater.unconsumed_tail:
        raise SightingRejected('oversize')
    if not inflater.eof or inflater.unused_data:
        # Truncated (no end marker / checksum) or trailing bytes: corrupt frame.
        raise SightingRejected('decode')
    try:
        frame = json.loads(data)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise SightingRejected('decode') from exc
    if not isinstance(frame, dict):
        raise SightingRejected('decode')
    return {
        '$schemaRef': frame.get('$schemaRef'),
        'header': allowlisted_header(frame.get('header')),
        'message': frame.get('message'),
    }


def allowlisted_header(header: object) -> dict[str, object]:
    """Copy only the allowlisted header keys. Never iterates the header."""
    if not isinstance(header, Mapping):
        return {}
    return {key: header.get(key) for key in HEADER_ALLOWLIST}


def is_fleet_carrier(station_type: object) -> bool:
    if not isinstance(station_type, str):
        return False
    folded = re.sub(r'[\s_-]+', '', station_type).casefold()
    return folded in _CARRIER_TYPES


def is_live_game_version(game_version: object) -> bool:
    """Live (4.x+) journal or CAPI-Live; Legacy (3.x, CAPI-Legacy) is not."""
    if not isinstance(game_version, str):
        return False
    if game_version.startswith('CAPI-Live-'):
        return True
    match = _LIVE_GAME_VERSION.match(game_version)
    return bool(match) and int(match.group('major')) >= 4


def parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not 10 <= len(value) <= 40:
        raise SightingRejected('invalid_timestamp')
    text = value[:-1] + '+00:00' if value.endswith('Z') else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SightingRejected('invalid_timestamp') from exc
    if parsed.tzinfo is None:
        raise SightingRejected('invalid_timestamp')
    return parsed.astimezone(UTC)


def economy_name(token: object) -> object:
    """``$economy_Industrial;`` -> ``Industrial``; anything else unchanged."""
    if isinstance(token, str):
        match = _ECONOMY_TOKEN.match(token)
        if match:
            return match.group('name')
    return token


def normalise_docked(
    frame: Mapping[str, object],
    *,
    received_at: datetime,
    max_event_age: timedelta,
    max_clock_skew: timedelta,
) -> StationSighting:
    """Turn one decoded frame into a sighting, or raise Filtered/SightingRejected."""
    if frame.get('$schemaRef') != JOURNAL_SCHEMA:
        raise Filtered('schema')
    message = frame.get('message')
    if not isinstance(message, dict):
        raise SightingRejected('invalid_message')
    if message.get('event') != DOCKED_EVENT:
        raise Filtered('event')
    header = allowlisted_header(frame.get('header'))

    if is_fleet_carrier(message.get('StationType')):
        raise Filtered('fleet_carrier')
    game_version = header.get('gameversion')
    if not is_live_game_version(game_version):
        raise Filtered('legacy')

    gateway_ts = parse_timestamp(header.get('gatewayTimestamp'))
    event_ts = parse_timestamp(message.get('timestamp'))
    oldest = received_at - max_event_age
    newest = received_at + max_clock_skew
    if (event_ts > gateway_ts + max_clock_skew or event_ts < gateway_ts - max_event_age
            or not oldest <= event_ts <= newest or not oldest <= gateway_ts <= newest):
        raise SightingRejected('invalid_timestamp')

    # Every hashed state field must be present. A missing list is unknown, not
    # empty: treating it as empty would record a spurious removal and then a
    # spurious restoration. Present-but-empty lists are real state.
    if any(message.get(key) is None for key in _STATE_KEYS):
        raise SightingRejected('incomplete_state')
    # A blank StationType is unknown too; it must not canonicalise to None and
    # be recorded as a real removal of a previously known type.
    station_type = message.get('StationType')
    if isinstance(station_type, str) and not station_type.strip():
        raise SightingRejected('incomplete_state')

    raw_economies = message.get('StationEconomies')
    if not isinstance(raw_economies, list):
        raise SightingRejected('invalid_economies')
    economies = []
    for item in raw_economies:
        if not isinstance(item, dict):
            raise SightingRejected('invalid_economies')
        economies.append((economy_name(item.get('Name')), item.get('Proportion')))

    services = message.get('StationServices')
    if not isinstance(services, list):
        raise SightingRejected('invalid_services')

    return build_sighting(
        source='eddn',
        market_id=message.get('MarketID'),
        system_id64=message.get('SystemAddress'),
        system_name=message.get('StarSystem'),
        station_name=message.get('StationName'),
        station_type=message.get('StationType'),
        economies=economies,
        services=services,
        landing_pads=message.get('LandingPads'),
        distance_ls=message.get('DistFromStarLS'),
        source_record_ts=event_ts,
        observed_at=received_at,
        gateway_ts=gateway_ts,
        software_name=header.get('softwareName'),
        software_version=header.get('softwareVersion'),
        game_version=game_version,
        game_build=header.get('gamebuild'),
    )
