"""Resolve existing station rows to body/lane associations.

The resolver is intentionally conservative. It can produce confirmed exact
matches, inferred matches with explicit source/notes, or unresolved records.
It never fabricates body ids and never converts unknown station types into
permanent colony-slot occupancy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from edfinder_api.station_body_resolver_utils import (
    DISTANCE_MATCH_TOLERANCE_LS,
    LANE_NON_COLONY_TYPES,
    LANE_ORBITAL_TYPES,
    LANE_SURFACE_TYPES,
    PERMANENT_COLONY_SLOT_TYPES,
    STATION_TYPE_LABELS,
    TRANSIENT_NON_SLOT_TYPES,
    _clean_body_name,
    _clean_text,
    _has_trusted_station_metadata,
    _join_notes,
    _normalise_name,
    _normalise_token,
    _read_float,
    _read_int,
    _station_distance_evidence,
)


@dataclass(frozen=True)
class StationBodyAssociation:
    station_id: int | None
    market_id: int | None
    system_id64: int | None
    body_id: int | None
    body_name: str | None
    lane: str
    association_status: str
    association_confidence: str
    association_source: str
    resolver_notes: str | None

    def to_api_dict(self) -> dict[str, Any]:
        return {
            'body_id': self.body_id,
            'body_name': self.body_name,
            'lane': self.lane,
            'association_status': self.association_status,
            'association_confidence': self.association_confidence,
            'association_source': self.association_source,
            'resolver_notes': self.resolver_notes,
        }

    def to_db_tuple(self) -> tuple[Any, ...]:
        return (
            self.station_id,
            self.market_id,
            self.system_id64,
            self.body_id,
            self.body_name,
            self.lane,
            self.association_status,
            self.association_confidence,
            self.association_source,
            self.resolver_notes,
        )


def classify_station_lane(station_type: Any) -> tuple[str, str | None]:
    canonical = normalise_station_type_label(station_type)
    if canonical is None:
        return 'unknown', 'Station type missing; lane cannot be classified.'
    if canonical == 'Unknown':
        return 'unknown', f'Station type {station_type!s} is not mapped to a colony slot lane.'
    if canonical in LANE_NON_COLONY_TYPES:
        return 'unknown', f'{station_type} is not treated as permanent colony-slot infrastructure.'
    if canonical in LANE_SURFACE_TYPES:
        return 'surface', None
    if canonical in LANE_ORBITAL_TYPES:
        return 'orbital', None
    return 'unknown', f'Station type {station_type!s} is not mapped to a colony slot lane.'


def is_permanent_colony_slot_station_type(station_type: Any) -> bool:
    return normalise_station_type_label(station_type) in PERMANENT_COLONY_SLOT_TYPES


def is_transient_non_slot_station_type(station_type: Any) -> bool:
    return normalise_station_type_label(station_type) in TRANSIENT_NON_SLOT_TYPES


def normalise_station_type_label(station_type: Any) -> str | None:
    token = _normalise_token(station_type)
    if not token:
        return None
    return STATION_TYPE_LABELS.get(token, 'Unknown')


def resolve_station_body_association(
    station: Mapping[str, Any],
    bodies: Sequence[Mapping[str, Any]],
    *,
    existing_link: Mapping[str, Any] | None = None,
    no_overwrite_confirmed: bool = True,
    distance_tolerance_ls: float = DISTANCE_MATCH_TOLERANCE_LS,
) -> StationBodyAssociation:
    """Resolve one station against bodies in the same system.

    A current confirmed/manual link is preserved by default for permanent station
    types so a weaker resolver pass cannot downgrade curated truth.
    """
    station_type = station.get('station_type') or station.get('stationType') or station.get('type')
    station_id = _read_int(station.get('station_id')) or _read_int(station.get('id'))
    market_id = _read_int(station.get('market_id')) or station_id
    system_id64 = _read_int(station.get('system_id64'))
    raw_body_name = _clean_body_name(
        station.get('station_body_name'),
        station.get('body_name'),
        station.get('bodyName'),
        station.get('body'),
    )
    trusted_body_name = _has_trusted_station_metadata(
        station,
        source_key='body_name_source',
        confidence_key='body_name_confidence',
    )
    lane, lane_note = classify_station_lane(station_type)

    if is_transient_non_slot_station_type(station_type):
        return _unresolved(
            station_id,
            market_id,
            system_id64,
            None,
            lane,
            'Transient/mobile infrastructure is ignored for station_body_links.',
            'transient_non_slot',
            lane_note,
        )

    preserved = _preserved_confirmed_link(station, existing_link, no_overwrite_confirmed)
    if preserved is not None:
        return preserved

    explicit_body_id = _read_int(station.get('body_id')) or _read_int(station.get('local_body_id'))
    if explicit_body_id is not None:
        match = _body_by_id(bodies, explicit_body_id)
        if match is not None:
            return _association(
                station_id,
                market_id,
                system_id64,
                match,
                lane,
                'confirmed',
                'exact',
                'resolver_body_id',
                lane_note,
            )
        return _unresolved(
            station_id,
            market_id,
            system_id64,
            raw_body_name,
            lane,
            'Station body_id did not match a body in this system.',
            'resolver_body_id',
            lane_note,
        )

    if raw_body_name:
        name_matches = [body for body in bodies if _normalise_name(body.get('name')) == _normalise_name(raw_body_name)]
        if len(name_matches) == 1:
            if trusted_body_name:
                return _association(
                    station_id,
                    market_id,
                    system_id64,
                    name_matches[0],
                    lane,
                    'confirmed',
                    'exact',
                    'edsm_body_name',
                    _join_notes(lane_note, 'Trusted EDSM bodyName matched one local same-system body.'),
                )
            return _association(
                station_id,
                market_id,
                system_id64,
                name_matches[0],
                lane,
                'inferred',
                'weak_inference',
                'resolver_body_name',
                _join_notes(lane_note, 'Legacy station body_name matched one local body but has no trusted provenance.'),
            )
        if len(name_matches) > 1:
            return _unresolved(
                station_id,
                market_id,
                system_id64,
                raw_body_name,
                lane,
                'Station body_name matched multiple bodies in this system.',
                'resolver_body_name',
                lane_note,
            )

    station_distance = _station_distance_evidence(station)
    if station_distance is not None:
        trusted_distance = _has_trusted_station_metadata(
            station,
            source_key='distance_source',
            confidence_key='distance_confidence',
        )
        distance_confidence = 'strong_inference' if trusted_distance else 'weak_inference'
        distance_source = 'edsm_distance' if trusted_distance else 'resolver_distance'
        distance_note = (
            f'Unique trusted EDSM station distance match within {distance_tolerance_ls:g} ls.'
            if trusted_distance
            else f'Unique legacy station distance match within {distance_tolerance_ls:g} ls.'
        )
        distance_matches = [
            body for body in bodies
            if _distance_matches(station_distance, _read_float(body.get('distance_from_star')), distance_tolerance_ls)
        ]
        if len(distance_matches) == 1:
            return _association(
                station_id,
                market_id,
                system_id64,
                distance_matches[0],
                lane,
                'inferred',
                distance_confidence,
                distance_source,
                _join_notes(
                    distance_note,
                    lane_note,
                ),
            )
        if len(distance_matches) > 1:
            return _unresolved(
                station_id,
                market_id,
                system_id64,
                raw_body_name,
                lane,
                f'distance_from_star matched {len(distance_matches)} bodies within {distance_tolerance_ls:g} ls.',
                'resolver_distance',
                lane_note,
            )

    return _unresolved(
        station_id,
        market_id,
        system_id64,
        raw_body_name,
        lane,
        'No exact body id, exact body_name, or unique distance_from_star match is available.',
        'unknown',
        lane_note,
    )


def build_station_body_link_rows(
    stations: Sequence[Mapping[str, Any]],
    bodies: Sequence[Mapping[str, Any]],
    existing_links: Mapping[int, Mapping[str, Any]] | None = None,
    *,
    no_overwrite_confirmed: bool = True,
) -> list[StationBodyAssociation]:
    links = existing_links or {}
    out: list[StationBodyAssociation] = []
    for station in stations:
        station_type = station.get('station_type') or station.get('stationType') or station.get('type')
        if is_transient_non_slot_station_type(station_type):
            continue
        station_id = _read_int(station.get('station_id')) or _read_int(station.get('id'))
        out.append(resolve_station_body_association(
            station,
            bodies,
            existing_link=links.get(station_id) if station_id is not None else None,
            no_overwrite_confirmed=no_overwrite_confirmed,
        ))
    return out


def _preserved_confirmed_link(
    station: Mapping[str, Any],
    existing_link: Mapping[str, Any] | None,
    no_overwrite_confirmed: bool,
) -> StationBodyAssociation | None:
    if not existing_link or not no_overwrite_confirmed:
        return None
    if existing_link.get('association_status') != 'confirmed':
        return None
    return StationBodyAssociation(
        station_id=_read_int(existing_link.get('station_id')) or _read_int(station.get('id')),
        market_id=_read_int(existing_link.get('market_id')) or _read_int(station.get('market_id')) or _read_int(station.get('id')),
        system_id64=_read_int(existing_link.get('system_id64')) or _read_int(station.get('system_id64')),
        body_id=_read_int(existing_link.get('body_id')),
        body_name=_clean_text(existing_link.get('body_name')) or _clean_text(station.get('body_name')),
        lane=_clean_text(existing_link.get('lane')) or 'unknown',
        association_status='confirmed',
        association_confidence=_clean_text(existing_link.get('association_confidence')) or 'exact',
        association_source=_clean_text(existing_link.get('association_source')) or 'manual',
        resolver_notes=_clean_text(existing_link.get('resolver_notes')),
    )


def _association(
    station_id: int | None,
    market_id: int | None,
    system_id64: int | None,
    body: Mapping[str, Any],
    lane: str,
    status: str,
    confidence: str,
    source: str,
    notes: str | None,
) -> StationBodyAssociation:
    return StationBodyAssociation(
        station_id=station_id,
        market_id=market_id,
        system_id64=system_id64 or _read_int(body.get('system_id64')),
        body_id=_read_int(body.get('id')) or _read_int(body.get('body_id')),
        body_name=_clean_text(body.get('name')) or _clean_text(body.get('body_name')),
        lane=lane,
        association_status=status,
        association_confidence=confidence,
        association_source=source,
        resolver_notes=notes,
    )


def _unresolved(
    station_id: int | None,
    market_id: int | None,
    system_id64: int | None,
    body_name: str | None,
    lane: str,
    notes: str,
    source: str,
    lane_note: str | None,
) -> StationBodyAssociation:
    return StationBodyAssociation(
        station_id=station_id,
        market_id=market_id,
        system_id64=system_id64,
        body_id=None,
        body_name=body_name,
        lane=lane,
        association_status='unresolved',
        association_confidence='unresolved',
        association_source=source,
        resolver_notes=_join_notes(notes, lane_note),
    )


def _body_by_id(bodies: Sequence[Mapping[str, Any]], body_id: int) -> Mapping[str, Any] | None:
    for body in bodies:
        candidate_id = _read_int(body.get('id')) or _read_int(body.get('body_id'))
        if candidate_id == body_id:
            return body
    return None


def _distance_matches(left: float, right: float | None, tolerance: float) -> bool:
    if right is None:
        return False
    return abs(left - right) <= tolerance
