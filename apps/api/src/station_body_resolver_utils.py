from __future__ import annotations

from math import isfinite
from typing import Any, Mapping


DISTANCE_MATCH_TOLERANCE_LS = 0.01
TRUSTED_STATION_METADATA_SOURCE = 'edsm_system_api'
TRUSTED_STATION_IDENTITY_CONFIDENCE = 'exact_station_identity'

LANE_ORBITAL_TYPES = {
    'Coriolis',
    'Orbis',
    'Ocellus',
    'Outpost',
    'AsteroidBase',
}

LANE_SURFACE_TYPES = {
    'PlanetaryPort',
    'PlanetaryOutpost',
}

LANE_NON_COLONY_TYPES = {
    'FleetCarrier',
    'MegaShip',
}

PERMANENT_COLONY_SLOT_TYPES = LANE_ORBITAL_TYPES | LANE_SURFACE_TYPES
TRANSIENT_NON_SLOT_TYPES = LANE_NON_COLONY_TYPES

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


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _clean_body_name(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, Mapping):
            value = _clean_body_name(value.get('name'), value.get('bodyName'), value.get('body_name'))
        text = _clean_text(value)
        if text:
            return text
    return None


def _read_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _read_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and isfinite(value):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return parsed if isfinite(parsed) else None
    return None


def _first_float(*values: Any) -> float | None:
    for value in values:
        parsed = _read_float(value)
        if parsed is not None:
            return parsed
    return None


def _station_distance_evidence(station: Mapping[str, Any]) -> float | None:
    return _first_float(
        station.get('distance_from_star'),
        station.get('distance_to_arrival'),
        station.get('distanceToArrival'),
        station.get('distanceFromArrival'),
        station.get('distanceFromArrivalLS'),
    )


def _has_trusted_station_metadata(
    station: Mapping[str, Any],
    *,
    source_key: str,
    confidence_key: str,
) -> bool:
    return (
        _clean_text(station.get(source_key)) == TRUSTED_STATION_METADATA_SOURCE
        and _clean_text(station.get(confidence_key)) == TRUSTED_STATION_IDENTITY_CONFIDENCE
    )


def _normalise_name(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    return ' '.join(value.strip().lower().split())


def _normalise_token(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    return ''.join(ch for ch in value.lower() if ch.isalnum())


def _join_notes(*notes: str | None) -> str | None:
    clean = [note.strip() for note in notes if isinstance(note, str) and note.strip()]
    return ' '.join(clean) if clean else None
