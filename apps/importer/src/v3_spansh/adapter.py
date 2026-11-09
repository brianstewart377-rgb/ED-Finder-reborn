"""Pure Spansh record -> frozen V3 canonical row adapter."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID

from .contracts import (
    EARTH_GRAVITY_M_S2,
    GRID_EDGE_LY,
    KM_PER_AU,
    KM_PER_SOLAR_RADIUS,
    PASCALS_PER_ATMOSPHERE,
    finite_float,
    macro_grid_key,
    stable_int63,
)


def _enumerated_vocabulary(tokens: tuple[str, ...], *, start: int = 1) -> dict[str, tuple[int, str]]:
    return {token.casefold(): (identifier, token) for identifier, token in enumerate(tokens, start=start)}


VOCABULARY: dict[str, dict[str, tuple[int, str]]] = {
    "body_type": {
        "star": (1, "Star"), "planet": (2, "Planet"),
        "barycentre": (3, "Barycentre"), "belt cluster": (4, "Belt Cluster"),
    },
    "terraforming_state": {
        "not terraformable": (1, "Not terraformable"),
        "terraformable": (2, "Terraformable"),
        "terraformed": (3, "Terraformed"),
        "terraforming": (4, "Terraforming"),
    },
    "atmosphere_classification": _enumerated_vocabulary((
        "Ammonia", "Ammonia and Oxygen", "Ammonia-rich", "Argon", "Argon-rich",
        "Carbon dioxide", "Carbon dioxide-rich", "Helium", "Hot Argon", "Hot Argon-rich",
        "Hot Carbon dioxide", "Hot Carbon dioxide-rich", "Hot Metallic vapour",
        "Hot Silicate vapour", "Hot Sulphur dioxide", "Hot Water", "Hot Water-rich",
        "Hot thick Ammonia", "Hot thick Ammonia-rich", "Hot thick Argon",
        "Hot thick Argon-rich", "Hot thick Carbon dioxide", "Hot thick Carbon dioxide-rich",
        "Hot thick Metallic vapour", "Hot thick Methane", "Hot thick Methane-rich",
        "Hot thick Nitrogen", "Hot thick Silicate vapour", "Hot thick Sulphur dioxide",
        "Hot thick Water", "Hot thick Water-rich", "Hot thin Carbon dioxide",
        "Hot thin Metallic vapour", "Hot thin Silicate vapour", "Hot thin Sulphur dioxide",
        "Methane", "Methane-rich", "Neon-rich", "Nitrogen", "No atmosphere", "Oxygen",
        "Suitable for water-based life", "Sulphur dioxide", "Thick Ammonia",
        "Thick Ammonia and Oxygen", "Thick Ammonia-rich", "Thick Argon", "Thick Argon-rich",
        "Thick Carbon dioxide", "Thick Carbon dioxide-rich", "Thick Helium", "Thick Methane",
        "Thick Methane-rich", "Thick Nitrogen", "Thick No atmosphere",
        "Thick Suitable for water-based life", "Thick Sulphur dioxide", "Thick Water",
        "Thick Water-rich", "Thin Ammonia", "Thin Ammonia and Oxygen", "Thin Ammonia-rich",
        "Thin Argon", "Thin Argon-rich", "Thin Carbon dioxide", "Thin Carbon dioxide-rich",
        "Thin Helium", "Thin Methane", "Thin Methane-rich", "Thin Neon", "Thin Neon-rich",
        "Thin Nitrogen", "Thin Oxygen", "Thin Sulphur dioxide", "Thin Water",
        "Thin Water-rich", "Water", "Water-rich",
    )),
    "volcanism_type": _enumerated_vocabulary((
        "Carbon Dioxide Geysers", "Major Carbon Dioxide Geysers", "Major Metallic Magma",
        "Major Rocky Magma", "Major Silicate Vapour Geysers", "Major Water Geysers",
        "Major Water Magma", "Metallic Magma", "Minor Ammonia Magma",
        "Minor Carbon Dioxide Geysers", "Minor Metallic Magma", "Minor Methane Magma",
        "Minor Nitrogen Magma", "Minor Rocky Magma", "Minor Silicate Vapour Geysers",
        "Minor Water Geysers", "Minor Water Magma", "No volcanism", "Rocky Magma",
        "Silicate Vapour Geysers", "Water Geysers", "Water Magma",
    )),
    "ring_type": {
        "rocky": (1, "Rocky"), "icy": (2, "Icy"),
        "metal rich": (3, "Metal Rich"), "metallic": (4, "Metallic"),
    },
    "reserve_type": {
        "depleted": (1, "Depleted"), "low": (2, "Low"),
        "common": (3, "Common"), "major": (4, "Major"), "pristine": (5, "Pristine"),
    },
    "signal_type": {
        "$saa_signaltype_biological;": (1, "Biological"),
        "$saa_signaltype_geological;": (2, "Geological"),
        "$saa_signaltype_human;": (3, "Human"),
        "$saa_signaltype_thargoid;": (4, "Thargoid"),
        "$saa_signaltype_guardian;": (5, "Guardian"),
        "$saa_signaltype_other;": (6, "Other"),
        "$saa_signaltype_planetanomaly;": (7, "Planet Anomaly"),
        # The current Spansh artifact uses these Frontier mining commodity
        # tokens as ring-signal types.  They are the governed, supported
        # hotspot vocabulary.  Rare non-hotspot tokens remain unmapped and
        # replayable in v3_source.unmapped_vocabulary.
        "alexandrite": (101, "Alexandrite"),
        "benitoite": (102, "Benitoite"),
        "bromellite": (103, "Bromellite"),
        "grandidierite": (104, "Grandidierite"),
        "lowtemperaturediamond": (105, "Low Temperature Diamond"),
        "monazite": (106, "Monazite"),
        "musgravite": (107, "Musgravite"),
        "opal": (108, "Void Opal"),
        "painite": (109, "Painite"),
        "platinum": (110, "Platinum"),
        "rhodplumsite": (111, "Rhodplumsite"),
        "serendibite": (112, "Serendibite"),
        "tritium": (113, "Tritium"),
    },
    "genus": {
        "$codex_ent_aleoids_genus_name;": (1, "Aleoida"),
        "$codex_ent_bacterial_genus_name;": (2, "Bacterium"),
        "$codex_ent_brancae_name;": (3, "Brain Tree"),
        "$codex_ent_cactoid_genus_name;": (4, "Cactoida"),
        "$codex_ent_clypeus_genus_name;": (5, "Clypeus"),
        "$codex_ent_conchas_genus_name;": (6, "Concha"),
        "$codex_ent_cone_name;": (7, "Bark Mound"),
        "$codex_ent_electricae_genus_name;": (8, "Electricae"),
        "$codex_ent_fonticulus_genus_name;": (9, "Fonticulua"),
        "$codex_ent_fumerolas_genus_name;": (10, "Fumerola"),
        "$codex_ent_fungoids_genus_name;": (11, "Fungoida"),
        "$codex_ent_ground_struct_ice_name;": (12, "Crystalline Shard"),
        "$codex_ent_osseus_genus_name;": (13, "Osseus"),
        "$codex_ent_recepta_genus_name;": (14, "Recepta"),
        "$codex_ent_shrubs_genus_name;": (15, "Frutexa"),
        "$codex_ent_sphere_name;": (16, "Anemone"),
        "$codex_ent_stratum_genus_name;": (17, "Stratum"),
        "$codex_ent_tube_name;": (18, "Sinuous Tuber"),
        "$codex_ent_tubus_genus_name;": (19, "Tubus"),
        "$codex_ent_tussocks_genus_name;": (20, "Tussock"),
        "$codex_ent_vents_name;": (21, "Gas Vent"),
    },
    "station_type": {
        "coriolis starport": (1, "Coriolis Starport"),
        "orbis starport": (2, "Orbis Starport"),
        "ocellus starport": (3, "Ocellus Starport"),
        "outpost": (4, "Outpost"), "planetary port": (5, "Planetary Port"),
        "planetary outpost": (6, "Planetary Outpost"),
        "asteroid base": (7, "Asteroid Base"),
        "megaship": (8, "Megaship"), "fleet carrier": (9, "Fleet Carrier"),
        "settlement": (10, "Settlement"),
        "mega ship": (8, "Megaship"), "drake-class carrier": (9, "Fleet Carrier"),
        "dodec starport": (11, "Dodec Starport"),
        "dockable planet station": (12, "Dockable Planet Station"),
        "planetary construction depot": (13, "Planetary Construction Depot"),
        "space construction depot": (14, "Space Construction Depot"),
        "surface settlement": (15, "Surface Settlement"),
    },
    "station_service": _enumerated_vocabulary((
        "Flight Controller", "Station Operations", "Dock", "Autodock", "Station Menu",
        "Contacts", "Refuel", "Market", "Repair", "Workshop", "Missions",
        "Search and Rescue", "Missions Generated", "Restock", "Social Space",
        "Black Market", "Interstellar Factors Contact", "Powerplay", "System Colonisation",
        "Crew Lounge", "Universal Cartographics", "Outfitting", "Livery",
        "Construction Services", "Shop", "Bartender", "Vista Genomics", "Shipyard",
        "Pioneer Supplies", "Apex Interstellar", "Tuning", "Fleet Carrier Fuel",
        "Fleet Carrier Management", "Frontline Solutions", "Refinery Contact",
        "Redemption Office", "Technology Broker", "Material Trader", "Squadron Bank",
        "Fleet Carrier Administration", "On Dock Mission", "Fleet Carrier Vendor",
    )),
    "economy": {
        "none": (1, "None"), "tourism": (2, "Tourism"),
        "agriculture": (3, "Agriculture"), "colony": (4, "Colony"),
        "extraction": (5, "Extraction"), "high tech": (6, "High Tech"),
        "industrial": (7, "Industrial"), "military": (8, "Military"),
        "refinery": (9, "Refinery"), "service": (10, "Service"),
        "terraforming": (11, "Terraforming"), "prison": (12, "Prison"),
        "damaged": (13, "Damaged"), "repair": (14, "Repair"),
        "rescue": (15, "Rescue"), "carrier": (16, "Carrier"),
        "private enterprise": (17, "Private Enterprise"),
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _timestamp(value: object, fallback: datetime) -> datetime:
    text = _text(value)
    if not text:
        return fallback
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return fallback


def _json_map(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict) or not value:
        return None
    result: dict[str, float] = {}
    for key, raw in value.items():
        number = finite_float(raw)
        if number is not None and 0 <= number <= 100:
            result[str(key)] = number
    return result or None


def _map_complete(values: dict[str, float] | None) -> bool | None:
    if values is None:
        return None
    return abs(sum(values.values()) - 100.0) <= 0.0001


def _public_code(value: str) -> str:
    return "_".join(value.casefold().replace("-", " ").split())[:63] or "unknown"


def station_market_id(station: dict[str, Any]) -> int | None:
    """Return the current Spansh Station.id Market ID, with legacy aliases.

    The current galaxy contract requires ``Station.id``.  The two older field
    spellings are accepted only as compatibility inputs; contradictory values
    are an identity conflict and fail closed.
    """
    values: list[tuple[str, int]] = []
    for field_name in ("id", "marketId", "marketID"):
        raw = station.get(field_name)
        if raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"station {field_name} is not an integer Market ID: {raw!r}") from exc
        if value < 0:
            raise ValueError(f"station {field_name} Market ID is negative: {value}")
        values.append((field_name, value))
    if not values:
        return None
    distinct = {value for _, value in values}
    if len(distinct) != 1:
        raise ValueError(f"station Market ID fields conflict: {values!r}")
    return values[0][1]


def _signal_map(entity: dict[str, Any]) -> dict[str, Any] | None:
    signal_container = entity.get("signals")
    if not isinstance(signal_container, dict):
        return None
    nested = signal_container.get("signals")
    if isinstance(nested, dict):
        return nested
    # Compatibility with the historical direct-map shape.  Metadata is not a
    # signal token.
    direct = {
        str(token): count
        for token, count in signal_container.items()
        if token not in {"updateTime", "genuses"}
    }
    return direct


def _signal_count(raw: object, *, entity_name: str, token: object) -> int:
    value = finite_float(raw)
    if value is None or value < 0 or not value.is_integer() or value > 2_147_483_647:
        raise ValueError(f"invalid signal count for {entity_name} / {token}: {raw!r}")
    return int(value)


def _station_display_name(station: dict[str, Any]) -> str | None:
    return _text(station.get("carrierName") or station.get("realName") or station.get("name"))


def _station_is_carrier(station: dict[str, Any]) -> bool:
    station_type = _text(station.get("type") or station.get("stationType"))
    return (station_type or "").casefold() in {"fleet carrier", "drake-class carrier"}


def _economy_observations(station: dict[str, Any]) -> dict[str, tuple[str, object]]:
    result: dict[str, tuple[str, object]] = {}
    economies = station.get("economies") or []
    if isinstance(economies, dict):
        economies = [{"name": key, "proportion": value} for key, value in economies.items()]
    for economy in economies if isinstance(economies, list) else []:
        if isinstance(economy, str):
            name, raw_number = economy, None
        elif isinstance(economy, dict):
            name = _text(economy.get("name") or economy.get("economy"))
            raw_number = economy.get("proportion", economy.get("share", economy.get("weight")))
        else:
            continue
        if name:
            result[name.casefold()] = (name, raw_number)
    return result


def _merge_station_observations(
    left: tuple[dict[str, Any], int | None],
    right: tuple[dict[str, Any], int | None],
    *,
    fallback_observed: datetime,
) -> tuple[dict[str, Any], int | None]:
    left_station, left_body_pk = left
    right_station, right_body_pk = right
    market_id = station_market_id(left_station)
    if market_id != station_market_id(right_station):
        raise ValueError("attempted to merge different station Market IDs")
    if left_body_pk is not None and right_body_pk is not None and left_body_pk != right_body_pk:
        raise ValueError(f"Market ID {market_id} is nested under incompatible bodies")
    if _station_is_carrier(left_station) != _station_is_carrier(right_station):
        raise ValueError(f"Market ID {market_id} conflicts between carrier and fixed-station identities")

    left_time = _timestamp(left_station.get("updateTime"), fallback_observed)
    right_time = _timestamp(right_station.get("updateTime"), fallback_observed)
    left_key = (left_time, json.dumps(left_station, sort_keys=True, separators=(",", ":"), default=str))
    right_key = (right_time, json.dumps(right_station, sort_keys=True, separators=(",", ":"), default=str))
    preferred, secondary = (right_station, left_station) if right_key > left_key else (left_station, right_station)
    preferred_time, secondary_time = (right_time, left_time) if right_key > left_key else (left_time, right_time)

    preferred_name = _station_display_name(preferred)
    secondary_name = _station_display_name(secondary)
    preferred_type = _text(preferred.get("type") or preferred.get("stationType"))
    secondary_type = _text(secondary.get("type") or secondary.get("stationType"))
    if preferred_time == secondary_time and not _station_is_carrier(preferred):
        if preferred_name and secondary_name and preferred_name.casefold() != secondary_name.casefold():
            raise ValueError(f"Market ID {market_id} has incompatible same-time station names")
        preferred_type_id = VOCABULARY["station_type"].get((preferred_type or "").casefold(), (None, ""))[0]
        secondary_type_id = VOCABULARY["station_type"].get((secondary_type or "").casefold(), (None, ""))[0]
        if preferred_type_id != secondary_type_id and preferred_type and secondary_type:
            raise ValueError(f"Market ID {market_id} has incompatible same-time station types")

    merged = dict(preferred)
    for field_name in (
        "id", "marketId", "marketID", "name", "realName", "carrierName", "type",
        "stationType", "distanceToArrival", "primaryEconomy", "secondaryEconomy",
        "latitude", "longitude", "updateTime",
    ):
        if merged.get(field_name) is None and secondary.get(field_name) is not None:
            merged[field_name] = secondary[field_name]

    services: dict[str, object] = {}
    for observation in (left_station, right_station):
        raw_services = observation.get("services")
        if isinstance(raw_services, list):
            for service in raw_services:
                token = service.get("name") if isinstance(service, dict) else service
                token_text = _text(token)
                if token_text:
                    services[token_text.casefold()] = token_text
    if services:
        merged["services"] = [services[key] for key in sorted(services)]

    economies: dict[str, tuple[str, object]] = {}
    for observation in (secondary, preferred):
        economies.update(_economy_observations(observation))
    if economies:
        merged["economies"] = [
            {"name": economies[key][0], "proportion": economies[key][1]}
            for key in sorted(economies)
        ]
    merged["_merged_observation_count"] = (
        int(left_station.get("_merged_observation_count", 1))
        + int(right_station.get("_merged_observation_count", 1))
    )
    return merged, left_body_pk if left_body_pk is not None else right_body_pk


@dataclass
class CanonicalBatch:
    rows: dict[str, list[tuple[Any, ...]]] = field(default_factory=dict)
    unmapped: set[tuple[str, str]] = field(default_factory=set)
    raw_economy_evidence: list[dict[str, Any]] = field(default_factory=list)
    feature_flags: set[str] = field(default_factory=set)

    def add(self, table: str, *values: Any) -> None:
        self.rows.setdefault(table, []).append(tuple(values))

    def merge(self, other: "CanonicalBatch") -> None:
        for table, rows in other.rows.items():
            self.rows.setdefault(table, []).extend(rows)
        self.unmapped.update(other.unmapped)
        self.raw_economy_evidence.extend(other.raw_economy_evidence)
        self.feature_flags.update(other.feature_flags)

    @property
    def rows_written(self) -> int:
        return sum(len(rows) for rows in self.rows.values())

    def logical_sha256_payload(self) -> bytes:
        normalized = {
            table: [[_stable_json_value(value) for value in row] for row in rows]
            for table, rows in sorted(self.rows.items())
        }
        return json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()


def _stable_json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


class SpanshAdapter:
    """Convert one streamed system without any database dependency."""

    def __init__(self, *, source_id: int, source_run_id: UUID, observed_at: datetime | None = None):
        self.source_id = source_id
        self.source_run_id = source_run_id
        self.observed_at = observed_at or _now()

    def _vocab(self, batch: CanonicalBatch, domain: str, raw: object) -> int | None:
        token = _text(raw)
        if not token:
            return None
        mapped = VOCABULARY.get(domain, {}).get(token.casefold())
        if mapped:
            return mapped[0]
        batch.unmapped.add((domain, token))
        return None

    def adapt_system(self, record: dict[str, Any]) -> CanonicalBatch:
        batch = CanonicalBatch()
        id64 = int(record["id64"])
        name = _text(record.get("name"))
        coords = record.get("coords") or {}
        x, y, z = (finite_float(coords.get(axis)) for axis in ("x", "y", "z"))
        if name is None or x is None or y is None or z is None:
            raise ValueError(f"system {id64} is missing name or finite coordinates")
        observed = _timestamp(record.get("date") or record.get("updateTime"), self.observed_at)
        bodies = [body for body in (record.get("bodies") or []) if isinstance(body, dict)]
        gx, gy, gz = (math.floor(value / GRID_EDGE_LY) for value in (x, y, z))
        source_body_count = record.get("bodyCount")
        try:
            source_body_count = int(source_body_count) if source_body_count is not None else None
        except (TypeError, ValueError):
            source_body_count = None
        batch.add(
            "systems", id64, name, x, y, z, source_body_count, len(bodies), None,
            gx, gy, gz, macro_grid_key(gx, gy, gz), self.source_id,
            self.source_run_id, observed, self.observed_at, "ACTIVE",
            self.source_run_id, observed, None, None, None, None, None,
        )

        body_keys: dict[int, int] = {}
        body_ids64: dict[int, int] = {}
        for body in bodies:
            local_id = body.get("bodyId")
            body_id64 = body.get("id64")
            body_pk = int(body_id64) if body_id64 is not None else stable_int63("body", id64, local_id, body.get("name"))
            if local_id is not None:
                body_keys[int(local_id)] = body_pk
            if body_id64 is not None:
                body_ids64[int(body_id64)] = body_pk

        nested_stations: list[tuple[dict[str, Any], int | None]] = []
        for body in bodies:
            self._adapt_body(batch, id64, body, body_keys, body_ids64)
            local_id = body.get("bodyId")
            body_pk = body_keys.get(int(local_id)) if local_id is not None else body_ids64.get(int(body.get("id64", -1)))
            for station in body.get("stations") or []:
                if isinstance(station, dict):
                    nested_stations.append((station, body_pk))

        stations: list[tuple[dict[str, Any], int | None]] = [
            (station, None) for station in (record.get("stations") or []) if isinstance(station, dict)
        ]
        stations.extend(nested_stations)
        deduplicated: dict[tuple[str, object], tuple[dict[str, Any], int | None]] = {}
        for station, body_pk in stations:
            market_id = station_market_id(station)
            key = ("market", market_id) if market_id is not None else (
                "observation", stable_int63(id64, body_pk, station.get("name"), station.get("type"))
            )
            previous = deduplicated.get(key)
            if previous is None:
                deduplicated[key] = (station, body_pk)
            elif market_id is not None:
                deduplicated[key] = _merge_station_observations(
                    previous, (station, body_pk), fallback_observed=observed,
                )
        for station, body_pk in deduplicated.values():
            self._adapt_station(batch, id64, station, body_pk, observed)
        return batch

    def _adapt_body(
        self,
        batch: CanonicalBatch,
        system_id64: int,
        body: dict[str, Any],
        body_keys: dict[int, int],
        body_ids64: dict[int, int],
    ) -> None:
        local_id = body.get("bodyId")
        source_id64 = body.get("id64")
        body_pk = (
            int(source_id64) if source_id64 is not None
            else stable_int63("body", system_id64, local_id, body.get("name"))
        )
        name = _text(body.get("name"))
        if not name:
            raise ValueError(f"body {body_pk} is missing name")
        observed = _timestamp(body.get("updateTime"), self.observed_at)
        body_type = _text(body.get("type"))
        body_type_id = self._vocab(batch, "body_type", body_type)
        if body_type:
            batch.feature_flags.add(body_type.casefold())

        direct_parent = None
        parents = body.get("parents")
        if isinstance(parents, list) and parents and isinstance(parents[0], dict) and parents[0]:
            raw_label, raw_id = next(iter(parents[0].items()))
            try:
                raw_id_int = int(raw_id)
            except (TypeError, ValueError):
                raw_id_int = None
            direct_parent = body_keys.get(raw_id_int) if raw_id_int is not None else None
            state = "RESOLVED" if direct_parent is not None else "UNRESOLVED"
            batch.add(
                "body_parent_evidence", body_pk, system_id64, self.source_run_id, 0,
                str(raw_label), raw_id_int, state, direct_parent,
                json.dumps({"source_path": "parents[0]", "additional_parent_count": max(0, len(parents) - 1)}),
            )

        semi_au = finite_float(body.get("semiMajorAxis"))
        radius = finite_float(body.get("radius"))
        if body_type and body_type.casefold() == "star":
            solar_radius = finite_float(body.get("solarRadius"))
            if solar_radius is not None:
                radius = solar_radius * KM_PER_SOLAR_RADIUS
        pressure_pa = finite_float(body.get("surfacePressure"))
        gravity = finite_float(body.get("gravity"))
        if body.get("gravityUnit") == "m/s2" and gravity is not None:
            gravity /= EARTH_GRAVITY_M_S2
        batch.add(
            "bodies", body_pk, system_id64,
            int(source_id64) if source_id64 is not None else None,
            int(local_id) if local_id is not None else None, direct_parent, name,
            body_type_id, self._vocab(batch, "atmosphere_classification", body.get("atmosphereType")),
            self._vocab(batch, "terraforming_state", body.get("terraformingState")),
            self._vocab(batch, "volcanism_type", body.get("volcanismType")),
            body.get("isLandable"), body.get("rotationalPeriodTidallyLocked"),
            body.get("mainStar"), _text(body.get("spectralClass")),
            _text(body.get("luminosity")), finite_float(body.get("absoluteMagnitude")),
            radius, finite_float(body.get("earthMasses")), finite_float(body.get("solarMasses")),
            gravity, pressure_pa / PASCALS_PER_ATMOSPHERE if pressure_pa is not None else None,
            finite_float(body.get("surfaceTemperature")),
            semi_au * KM_PER_AU if semi_au is not None else None,
            finite_float(body.get("orbitalPeriod")), finite_float(body.get("rotationalPeriod")),
            finite_float(body.get("orbitalEccentricity")), finite_float(body.get("orbitalInclination")),
            finite_float(body.get("ascendingNode")), finite_float(body.get("argOfPeriapsis")),
            finite_float(body.get("meanAnomaly")), finite_float(body.get("axialTilt")),
            finite_float(body.get("distanceToArrival")), finite_float(body.get("age")),
            isinstance(body.get("signals"), dict),
            isinstance((body.get("signals") or {}).get("genuses"), list) if isinstance(body.get("signals"), dict) else False,
            self.source_id, self.source_run_id, observed, self.observed_at, "ACTIVE",
            self.source_run_id, observed, None, None, None, None, None,
        )

        solid = _json_map(body.get("solidComposition"))
        atmosphere = _json_map(body.get("atmosphereComposition"))
        materials = _json_map(body.get("materials"))
        if solid or materials or atmosphere:
            batch.add(
                "body_composition", body_pk,
                solid.get("Ice") if solid else None,
                solid.get("Metal") if solid else None,
                solid.get("Rock") if solid else None,
                json.dumps(materials) if materials else None,
                _map_complete(materials),
                json.dumps(atmosphere) if atmosphere else None,
                _map_complete(atmosphere), self.source_run_id, observed,
            )
            batch.feature_flags.add("composition")

        signals = body.get("signals")
        if isinstance(signals, dict):
            raw_signals = _signal_map(body)
            if raw_signals is not None:
                for token, count in raw_signals.items():
                    signal_id = self._vocab(batch, "signal_type", token)
                    if signal_id is not None:
                        batch.add(
                            "body_signal_current", body_pk, signal_id,
                            _signal_count(count, entity_name=name, token=token),
                            self.source_run_id, _timestamp(signals.get("updateTime"), observed),
                        )
                        batch.feature_flags.add("signals")
            for genus in signals.get("genuses") or []:
                genus_id = self._vocab(batch, "genus", genus)
                if genus_id is not None:
                    batch.add("body_genus_current", body_pk, genus_id, self.source_run_id, _timestamp(signals.get("updateTime"), observed))
                    batch.feature_flags.add("genera")

        for ring in body.get("rings") or []:
            if isinstance(ring, dict):
                self._adapt_ring(
                    batch, system_id64, body_pk, ring, observed,
                    body_reserve_level=body.get("reserveLevel"),
                )
        for belt in body.get("belts") or []:
            if isinstance(belt, dict):
                self._adapt_ring(
                    batch, system_id64, body_pk, belt, observed,
                    forced_kind="BELT", body_reserve_level=body.get("reserveLevel"),
                )

    def _adapt_ring(
        self, batch: CanonicalBatch, system_id64: int, body_pk: int,
        ring: dict[str, Any], observed: datetime, *, forced_kind: str | None = None,
        body_reserve_level: object = None,
    ) -> None:
        fallback_kind = "belt" if forced_kind == "BELT" else "ring"
        name = _text(ring.get("name")) or f"body-{body_pk}-{fallback_kind}"
        source_id64 = ring.get("id64")
        ring_pk = int(source_id64) if source_id64 is not None else stable_int63("ring", body_pk, name)
        raw_type = _text(ring.get("type") or ring.get("ringType"))
        kind = forced_kind or ("BELT" if "belt" in name.casefold() else "RING")
        batch.add(
            "rings", ring_pk, body_pk, system_id64,
            int(source_id64) if source_id64 is not None else None, kind, name,
            self._vocab(batch, "ring_type", raw_type),
            self._vocab(batch, "reserve_type", ring.get("reserveLevel") or body_reserve_level),
            finite_float(ring.get("innerRadius")), finite_float(ring.get("outerRadius")),
            finite_float(ring.get("mass")), isinstance(ring.get("signals"), dict),
            self.source_id, self.source_run_id, observed, self.observed_at, "ACTIVE",
            self.source_run_id, observed, None, None, None, None, None,
        )
        batch.feature_flags.add("rings")
        if kind == "BELT":
            batch.feature_flags.add("belts")
        signal_map = _signal_map(ring)
        if signal_map is not None:
            signal_container = ring.get("signals")
            signal_observed = _timestamp(
                signal_container.get("updateTime") if isinstance(signal_container, dict) else None,
                observed,
            )
            batch.feature_flags.add("ring_signal_evidence")
            for token, count in signal_map.items():
                signal_id = self._vocab(batch, "signal_type", token)
                if signal_id is not None:
                    batch.add(
                        "ring_signal_current", ring_pk, signal_id,
                        _signal_count(count, entity_name=name, token=token),
                        self.source_run_id, signal_observed,
                    )
                    batch.feature_flags.add("ring_signals")

    def _station_economies(self, station: dict[str, Any]) -> Iterable[tuple[str, object, bool, bool]]:
        primary = _text(station.get("primaryEconomy"))
        secondary = _text(station.get("secondaryEconomy"))
        emitted: set[str] = set()
        economies = station.get("economies") or []
        if isinstance(economies, dict):
            economies = [{"name": key, "proportion": value} for key, value in economies.items()]
        for economy in economies if isinstance(economies, list) else []:
            if isinstance(economy, str):
                name, raw_number = economy, None
            elif isinstance(economy, dict):
                name = _text(economy.get("name") or economy.get("economy"))
                raw_number = economy.get("proportion", economy.get("share", economy.get("weight")))
            else:
                continue
            if not name:
                continue
            emitted.add(name.casefold())
            yield name, raw_number, name.casefold() == (primary or "").casefold(), name.casefold() == (secondary or "").casefold()
        for name, is_primary, is_secondary in ((primary, True, False), (secondary, False, True)):
            if name and name.casefold() not in emitted:
                yield name, None, is_primary, is_secondary

    def _adapt_station(
        self, batch: CanonicalBatch, system_id64: int, station: dict[str, Any],
        body_pk: int | None, system_observed: datetime,
    ) -> None:
        market_id = station_market_id(station)
        name = _station_display_name(station)
        if not name:
            return
        station_pk = market_id if market_id is not None else stable_int63(
            "station-observation", system_id64, body_pk, name, station.get("type")
        )
        observed = _timestamp(station.get("updateTime"), system_observed)
        station_type = _text(station.get("type") or station.get("stationType"))
        services = station.get("services")
        economies = station.get("economies")
        is_carrier = _station_is_carrier(station)
        batch.add(
            "stations", station_pk, market_id, system_id64, name,
            self._vocab(batch, "station_type", station_type),
            finite_float(station.get("distanceToArrival")), isinstance(services, list),
            isinstance(economies, (list, dict)), is_carrier, self.source_id,
            self.source_run_id, observed, self.observed_at, "ACTIVE",
            self.source_run_id, observed, None, None, None, None, None,
        )
        association = "BODY_RESOLVED" if body_pk is not None else "SYSTEM_ONLY"
        batch.add(
            "station_placement", station_pk, system_id64, body_pk,
            finite_float(station.get("latitude")), finite_float(station.get("longitude")),
            association, self.source_run_id, observed,
            json.dumps({
                "source_path": "bodies[].stations" if body_pk else "stations",
                "source_station_id": station.get("id"),
                "source_name": station.get("name"),
                "real_name": station.get("realName"),
                "carrier_name": station.get("carrierName"),
                "merged_observation_count": int(station.get("_merged_observation_count", 1)),
            }),
        )
        for service in services or []:
            token = service.get("name") if isinstance(service, dict) else service
            service_id = self._vocab(batch, "station_service", token)
            if service_id is not None:
                batch.add("station_service_current", station_pk, service_id, self.source_run_id, observed)
                batch.feature_flags.add("station_services")
        for economy, raw_number, is_primary, is_secondary in self._station_economies(station):
            economy_id = self._vocab(batch, "economy", economy)
            if economy_id is None:
                continue
            # Source-specific calibration is absent.  Designations remain useful,
            # but Spansh's numeric scale is evidence only and MUST NOT be copied,
            # divided by 100, or otherwise promoted to canonical weight.
            batch.add(
                "station_economy_current", station_pk, economy_id, None,
                is_primary, is_secondary, self.source_run_id, observed,
            )
            batch.raw_economy_evidence.append({
                "station_name": name,
                "market_id": market_id,
                "economy": economy,
                "primary": is_primary,
                "secondary": is_secondary,
                "raw_spansh_number": finite_float(raw_number),
                "canonical_economy_weight": None,
                "calibration_state": "GATED_UNCALIBRATED",
            })
            batch.feature_flags.add("station_economies")
        batch.feature_flags.add("stations")
