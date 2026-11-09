"""Small, versioned galaxy-fact allowlist and deterministic reconciliation.

V1 accepts Live (4.x) Scan physical fields on exact system/body identifiers.
No commander identity, travel chronology, credits, names or station state is
included. New/unknown bodies remain unresolved until canonical identity exists.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timedelta, timezone

POLICY = 'journal-galaxy-physical-v1'
AUDIENCE = 'ED_FINDER_GALAXY'
NAMESPACE = uuid.UUID('96414097-a25c-4efc-b451-412d6b1e0a08')
PURPOSE = 'Shared galaxy physical facts for ED-Finder Search and ratings'
FACT_KIND = 'journal_galaxy_physical_v1'
FIELDS = {
    'Radius': ('radius_km', 1000, 0, None),
    'MassEM': ('planet_mass_earth', 1, 0, None),
    'StellarMass': ('stellar_mass_solar', 1, 0, None),
    'SurfaceGravity': ('surface_gravity_g', 9.80665, 0, None),
    'SurfacePressure': ('surface_pressure_atm', 101325, 0, None),
    'SurfaceTemperature': ('surface_temperature_k', 1, 0, None),
    'SemiMajorAxis': ('semi_major_axis_km', 1000, 0, None),
    'OrbitalPeriod': ('orbital_period_days', 86400, 0, None),
    'RotationPeriod': ('rotation_period_days', 86400, None, None),
    'Eccentricity': ('eccentricity', 1, 0, 1),
    'OrbitalInclination': ('orbital_inclination_deg', 1, -180, 180),
    'AxialTilt': ('axial_tilt_rad', 1, -math.pi, math.pi),
    'DistanceFromArrivalLS': ('distance_from_arrival_ls', 1, 0, None),
    'Age_MY': ('stellar_age_myr', 1, 0, None),
    'AbsoluteMagnitude': ('absolute_magnitude', 1, None, None),
}
CANONICAL_FIELDS = frozenset(row[0] for row in FIELDS.values()) | {'is_landable'}


def exact_id(value: object, *, zero: bool = False) -> int:
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r'0|[1-9][0-9]{0,18}', value):
        result = int(value)
    else:
        raise ValueError('missing_or_invalid_identity')
    if result < (0 if zero else 1) or result > 2**63 - 1:
        raise ValueError('missing_or_invalid_identity')
    return result


def normalize_scan(event: dict, *, now: datetime | None = None) -> dict:
    if event['event_type'] != 'Scan':
        raise ValueError('unsupported_event')
    payload = event['event_payload']
    if not re.fullmatch(r'4(?:\.[0-9]+)+', str(payload.get('GameVersion', ''))):
        raise ValueError('unverified_game_version')
    observed = event['event_timestamp']
    if not isinstance(observed, datetime) or observed.tzinfo is None:
        raise ValueError('invalid_observation_time')
    if observed > (now or datetime.now(timezone.utc)) + timedelta(minutes=5):
        raise ValueError('future_observation')
    fields = {}
    for source, (target, divisor, lower, upper) in FIELDS.items():
        value = payload.get(source)
        if value is None:
            continue
        if type(value) not in (int, float):
            raise ValueError('invalid_physical_value')
        try:
            converted = float(value) / divisor
        except (OverflowError, ValueError) as exc:
            raise ValueError('invalid_physical_value') from exc
        if (not math.isfinite(converted) or (lower is not None and converted < lower)
                or (upper is not None and converted > upper)):
            raise ValueError('invalid_physical_value')
        fields[target] = converted
    if 'Landable' in payload:
        if type(payload['Landable']) is not bool:
            raise ValueError('invalid_physical_value')
        fields['is_landable'] = payload['Landable']
    if not fields:
        raise ValueError('no_eligible_fields')
    return {
        'system_id64': str(exact_id(payload.get('SystemAddress'))),
        'frontier_body_id': str(exact_id(payload.get('BodyID'), zero=True)),
        'observed_at': observed.astimezone(timezone.utc).isoformat(),
        'fields': fields,
    }


def reconcile_fields(current: dict, observation: dict, field_times: dict[str, datetime], *,
                     conflicting_fields: frozenset[str] = frozenset()) -> tuple[dict, dict]:
    """No last-upload-wins. Conflicting unknown/equal/newer facts are preserved.

    Missing stable physical values may be filled by an older valid observation.
    A non-null value only changes when evidence proves the incoming observation
    is newer. Field-level provenance is used before the row's source timestamp.
    """
    observed = datetime.fromisoformat(observation['observed_at'])
    if observed.tzinfo is None:
        raise ValueError('invalid_observation_time')
    bounds = {target: (lower, upper) for target, _, lower, upper in FIELDS.values()}
    changes, decisions = {}, {}
    for field, value in observation['fields'].items():
        if field not in CANONICAL_FIELDS:
            raise ValueError('unsupported_canonical_field')
        if field == 'is_landable':
            if type(value) is not bool:
                raise ValueError('invalid_physical_value')
        else:
            lower, upper = bounds[field]
            try:
                valid = (type(value) in (int, float) and math.isfinite(value)
                         and (lower is None or value >= lower) and (upper is None or value <= upper))
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError('invalid_physical_value')
        existing = current.get(field)
        recorded = field_times.get(field, current.get('source_updated_at'))
        if field in conflicting_fields:
            decisions[field] = 'PRESERVE_CONFLICT'
        elif existing is None:
            changes[field], decisions[field] = value, 'FILL_MISSING'
        elif existing == value:
            decisions[field] = 'UNCHANGED'
        elif recorded is not None and observed > recorded:
            changes[field], decisions[field] = value, 'NEWER_OBSERVATION'
        else:
            decisions[field] = 'PRESERVE_CONFLICT'
    return changes, decisions
