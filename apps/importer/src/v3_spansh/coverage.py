"""Bounded real-source coverage and vocabulary observations for Phase 4B."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from .adapter import CanonicalBatch, VOCABULARY, station_market_id
from .contracts import COPY_COLUMNS, finite_float


def _text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _composition_state(value: object) -> str | None:
    if not isinstance(value, dict) or not value:
        return None
    numbers = [finite_float(item) for item in value.values()]
    if any(item is None or item < 0 or item > 100 for item in numbers):
        return "partial"
    return "complete" if abs(sum(item for item in numbers if item is not None) - 100.0) <= 0.0001 else "partial"


@dataclass
class SourceCoverageInventory:
    counts: Counter[str] = field(default_factory=Counter)
    first_examples: dict[str, dict[str, Any]] = field(default_factory=dict)
    vocabulary_counts: Counter[tuple[str, str]] = field(default_factory=Counter)
    vocabulary_examples: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    economy_numeric_values: Counter[str] = field(default_factory=Counter)
    economy_numeric_min: float | None = None
    economy_numeric_max: float | None = None
    high_body_fanout: int = 0
    high_body_fanout_example: dict[str, Any] | None = None

    def _example(self, key: str, system: dict[str, Any], **detail: Any) -> None:
        self.first_examples.setdefault(key, {
            "system_id64": str(system.get("id64")),
            "system_name": _text(system.get("name")),
            **{name: _json_value(value) for name, value in detail.items()},
        })

    def _vocabulary(
        self, domain: str, raw: object, system: dict[str, Any], entity_name: str | None,
    ) -> None:
        token = _text(raw)
        if not token:
            return
        key = (domain, token)
        self.vocabulary_counts[key] += 1
        self.vocabulary_examples.setdefault(key, {
            "system_id64": str(system.get("id64")),
            "system_name": _text(system.get("name")),
            "entity_name": entity_name,
        })

    def _economy_number(self, value: object) -> None:
        number = finite_float(value)
        if number is None:
            return
        self.counts["raw_economy_numeric_values"] += 1
        self.economy_numeric_min = number if self.economy_numeric_min is None else min(self.economy_numeric_min, number)
        self.economy_numeric_max = number if self.economy_numeric_max is None else max(self.economy_numeric_max, number)
        self.economy_numeric_values[format(number, ".12g")] += 1

    def _observe_station(
        self, system: dict[str, Any], station: dict[str, Any], *, nested: bool,
    ) -> int | None:
        name = _text(station.get("name"))
        self.counts["station_observations"] += 1
        self.counts["nested_station_observations" if nested else "top_level_station_observations"] += 1
        try:
            market_id = station_market_id(station)
        except ValueError:
            market_id = None
            self.counts["invalid_market_id_observations"] += 1
        if market_id is None:
            self.counts["stations_without_market_id"] += 1
            self._example("station_without_market_id", system, station_name=name, nested=nested)
        station_type = _text(station.get("type") or station.get("stationType"))
        self._vocabulary("station_type", station_type, system, name)
        if station_type and "carrier" in station_type.casefold():
            self.counts["carrier_observations"] += 1
            self._example("carrier", system, station_name=name, station_type=station_type)
        services = station.get("services")
        if isinstance(services, list):
            if services:
                self.counts["stations_with_services"] += 1
            for service in services:
                token = service.get("name") if isinstance(service, dict) else service
                self._vocabulary("station_service", token, system, name)
        primary = _text(station.get("primaryEconomy"))
        secondary = _text(station.get("secondaryEconomy"))
        if primary:
            self.counts["primary_economy_designations"] += 1
            self._vocabulary("economy", primary, system, name)
        if secondary:
            self.counts["secondary_economy_designations"] += 1
            self._vocabulary("economy", secondary, system, name)
        economies = station.get("economies") or []
        if isinstance(economies, dict):
            economies = [{"name": key, "proportion": value} for key, value in economies.items()]
        if isinstance(economies, list) and economies:
            self.counts["stations_with_economies"] += 1
        for economy in economies if isinstance(economies, list) else []:
            if isinstance(economy, str):
                token, raw_number = economy, None
            elif isinstance(economy, dict):
                token = economy.get("name") or economy.get("economy")
                raw_number = economy.get("proportion", economy.get("share", economy.get("weight")))
            else:
                continue
            self._vocabulary("economy", token, system, name)
            self._economy_number(raw_number)
        return market_id

    def observe(self, system: dict[str, Any], batch: CanonicalBatch) -> None:
        self.counts["systems"] += 1
        bodies = [item for item in (system.get("bodies") or []) if isinstance(item, dict)]
        self.counts["bodies"] += len(bodies)
        if len(bodies) > self.high_body_fanout:
            self.high_body_fanout = len(bodies)
            self.high_body_fanout_example = {
                "system_id64": str(system.get("id64")),
                "system_name": _text(system.get("name")),
                "loaded_bodies": len(bodies),
                "source_body_count": system.get("bodyCount"),
            }
        source_body_count = system.get("bodyCount")
        try:
            body_count_mismatch = source_body_count is not None and int(source_body_count) != len(bodies)
        except (TypeError, ValueError):
            body_count_mismatch = True
        if body_count_mismatch:
            self.counts["source_body_count_mismatches"] += 1
            self._example(
                "source_body_count_mismatch", system,
                source_body_count=source_body_count, loaded_body_count=len(bodies),
            )

        canonical_rings = {
            str(row[COPY_COLUMNS["rings"].index("name")]): {
                column: _json_value(value)
                for column, value in zip(COPY_COLUMNS["rings"], row, strict=True)
            }
            for row in batch.rows.get("rings", [])
        }
        for body in bodies:
            body_name = _text(body.get("name"))
            body_type = _text(body.get("type"))
            self._vocabulary("body_type", body_type, system, body_name)
            self._vocabulary("terraforming_state", body.get("terraformingState"), system, body_name)
            if body_type and body_type.casefold() == "barycentre":
                self.counts["barycentres"] += 1
                self._example("barycentre", system, body_name=body_name, body_id=body.get("bodyId"))
            parents = body.get("parents")
            if isinstance(parents, list) and parents:
                self.counts["bodies_with_parent_evidence"] += 1
                if len(parents) > 1:
                    self.counts["direct_parent_chains"] += 1
                    self._example("direct_parent_chain", system, body_name=body_name, parents=parents)
                if any(isinstance(parent, dict) and any(str(key).casefold() == "null" for key in parent) for parent in parents):
                    self.counts["source_parent_type_null"] += 1
                    self._example("source_parent_type_null", system, body_name=body_name, parents=parents)
            physical_fields = (
                "radius", "gravity", "surfacePressure", "semiMajorAxis",
                "orbitalPeriod", "rotationalPeriod", "distanceToArrival",
            )
            if any(body.get(field) is None for field in physical_fields):
                self.counts["bodies_with_sparse_physical_fields"] += 1
                self._example("sparse_physical_fields", system, body_name=body_name)
            for field_name in ("solidComposition", "materials", "atmosphereComposition"):
                state = _composition_state(body.get(field_name))
                if state:
                    self.counts[f"{state}_composition_maps"] += 1
                    self._example(
                        f"{state}_composition_map", system,
                        body_name=body_name, field=field_name, values=body.get(field_name),
                    )
            signals = body.get("signals")
            if isinstance(signals, dict):
                signal_map = signals.get("signals")
                if isinstance(signal_map, dict) and signal_map:
                    self.counts["bodies_with_signals"] += 1
                    self.counts["body_signal_entries"] += len(signal_map)
                    self._example("body_signals", system, body_name=body_name, signals=signal_map)
                    for token in signal_map:
                        self._vocabulary("signal_type", token, system, body_name)
                genera = signals.get("genuses")
                if isinstance(genera, list) and genera:
                    self.counts["bodies_with_genera"] += 1
                    self.counts["genus_entries"] += len(genera)
                    self._example("body_genera", system, body_name=body_name, genera=genera)
                    for token in genera:
                        self._vocabulary("genus", token, system, body_name)
            belts = body.get("belts")
            if "belts" in body:
                self.counts["bodies_with_belts_key"] += 1
            if isinstance(belts, list) and belts:
                self.counts["bodies_with_non_empty_belts"] += 1
            for source_field, physical_objects in (
                ("rings", body.get("rings")),
                ("belts", belts),
            ):
                for ring in physical_objects or []:
                    if not isinstance(ring, dict):
                        continue
                    ring_name = _text(ring.get("name"))
                    if source_field == "rings":
                        self.counts["ring_observations"] += 1
                    self._vocabulary(
                        "ring_type", ring.get("type") or ring.get("ringType"),
                        system, ring_name,
                    )
                    self._vocabulary(
                        "reserve_type", ring.get("reserveLevel"), system, ring_name,
                    )
                    ring_signal_container = ring.get("signals")
                    if isinstance(ring_signal_container, dict):
                        nested_signals = ring_signal_container.get("signals")
                        if isinstance(nested_signals, dict):
                            ring_signal_map = nested_signals
                        else:
                            ring_signal_map = {
                                str(token): count
                                for token, count in ring_signal_container.items()
                                if token not in {"updateTime", "genuses"}
                            }
                        self.counts["rings_belts_with_signal_information"] += 1
                        self.counts["ring_signal_entries"] += len(ring_signal_map)
                        for token in ring_signal_map:
                            self._vocabulary("signal_type", token, system, ring_name)
                    is_belt = source_field == "belts" or bool(
                        ring_name and "belt" in ring_name.casefold()
                    )
                    self.counts[
                        "belt_observations" if is_belt else "ring_only_observations"
                    ] += 1
                    self._example(
                        "belt" if is_belt else "ring", system,
                        parent_body_name=body_name,
                        source_field=source_field,
                        source_ring=_json_value(ring),
                        canonical_normalized_result=canonical_rings.get(ring_name or ""),
                    )

        parent_columns = COPY_COLUMNS["body_parent_evidence"]
        state_index = parent_columns.index("resolution_state")
        for row in batch.rows.get("body_parent_evidence", []):
            state = str(row[state_index]).casefold()
            self.counts[f"parent_{state}"] += 1
            if state == "unresolved":
                self._example("unresolved_parent", system)

        top_level_market_ids: set[int] = set()
        nested_market_ids: set[int] = set()
        for station in system.get("stations") or []:
            if isinstance(station, dict):
                market_id = self._observe_station(system, station, nested=False)
                if market_id is not None:
                    top_level_market_ids.add(market_id)
        for body in bodies:
            for station in body.get("stations") or []:
                if isinstance(station, dict):
                    market_id = self._observe_station(system, station, nested=True)
                    if market_id is not None:
                        nested_market_ids.add(market_id)
        duplicates = top_level_market_ids & nested_market_ids
        if duplicates:
            self.counts["duplicate_top_level_nested_market_ids"] += len(duplicates)
            self._example("duplicate_top_level_nested_market_id", system, market_ids=sorted(duplicates))

    def as_dict(self) -> dict[str, Any]:
        known: list[dict[str, Any]] = []
        unmapped: list[dict[str, Any]] = []
        for (domain, raw_token), count in sorted(self.vocabulary_counts.items()):
            mapped = VOCABULARY.get(domain, {}).get(raw_token.casefold())
            item = {
                "affected_domain": domain,
                "raw_token": raw_token,
                "occurrence_count": count,
                "first_source_example": self.vocabulary_examples[(domain, raw_token)],
            }
            if mapped:
                known.append({
                    **item,
                    "canonical_id": mapped[0],
                    "canonical_display_name": mapped[1],
                    "disposition": "MAPPED_CANONICAL",
                })
            else:
                unmapped.append({
                    **item,
                    "disposition": "RAW_RETAINED_UNMAPPED_REVIEW_CANDIDATE",
                })
        return {
            "coverage_counts": dict(sorted(self.counts.items())),
            "first_source_examples": self.first_examples,
            "high_body_fanout": {
                "loaded_bodies": self.high_body_fanout,
                "first_source_example": self.high_body_fanout_example,
            },
            "economy_raw_numeric_distribution": {
                "count": self.counts["raw_economy_numeric_values"],
                "minimum": self.economy_numeric_min,
                "maximum": self.economy_numeric_max,
                "value_counts": dict(self.economy_numeric_values.most_common()),
                "canonical_transform_promoted": False,
            },
            "vocabulary_audit": {
                "known_canonical_tokens": known,
                "unmapped_tokens": unmapped,
            },
        }
