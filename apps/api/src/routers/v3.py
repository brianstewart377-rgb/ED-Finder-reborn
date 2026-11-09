"""Strict, pointer-driven V3 read API.

All integer identities cross this API boundary as decimal strings so browser
clients never lose 64-bit precision.  Queries resolve the single canonical
generation pointer and never inspect BUILDING/READY candidates.
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated, Awaitable, Literal, TypeVar

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict

from edfinder_api.deps import get_readonly_pool

router = APIRouter(prefix="/api/v1", tags=["v3-canonical"])
_SCHEMA = re.compile(r"^v3_gen_[a-z][a-z0-9_]{0,30}$")
SystemId = Annotated[str, Path(pattern=r"^[0-9]{1,19}$")]


# Every canonical read is bounded as a whole, connection acquisition included
# (asyncpg's per-query timeout does not cover acquiring a pool connection), so
# a saturated read-only pool is a retryable 503 rather than a hung request.
READ_TIMEOUT_SECONDS = 5.0
_T = TypeVar("_T")


async def _bounded(operation: Awaitable[_T]) -> _T:
    try:
        return await asyncio.wait_for(operation, timeout=READ_TIMEOUT_SECONDS)
    except TimeoutError:
        raise HTTPException(503, "V3 canonical read timed out; please retry") from None


class V1Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GenerationV1(V1Model):
    generation_id: str
    generation_key: str
    publication_sequence: str
    published_at: str


class SystemV1(V1Model):
    id64: str
    name: str
    x_ly: float
    y_ly: float
    z_ly: float
    source_body_count: int | None
    loaded_body_count: int
    lifecycle_state: str
    source_updated_at: str | None


class BodySignalV1(V1Model):
    signal_type: str
    count: int


class BodyV1(V1Model):
    body_id: str
    source_body_id64: str | None
    frontier_body_id: str | None
    direct_parent_body_id: str | None
    name: str
    body_type: str | None
    body_class: str | None = None
    body_class_source: Literal["ratings-v4-derived"] | None = None
    is_main_star: bool | None
    spectral_class: str | None
    luminosity_class: str | None
    is_landable: bool | None
    radius_km: float | None
    planet_mass_earth: float | None
    stellar_mass_solar: float | None
    surface_pressure_atm: float | None
    semi_major_axis_km: float | None
    distance_from_arrival_ls: float | None
    lifecycle_state: str
    signals: list[BodySignalV1]


class RingSignalV1(V1Model):
    signal_type: str
    count: int


class RingV1(V1Model):
    ring_id: str
    body_id: str
    source_ring_id64: str | None
    kind: Literal["RING", "BELT"]
    name: str
    ring_type: str | None
    reserve_type: str | None
    inner_radius_km: float | None
    outer_radius_km: float | None
    mass_mt: float | None
    signals_complete: bool | None
    source_updated_at: str | None
    lifecycle_state: str
    signals: list[RingSignalV1]


class StationEconomyV1(V1Model):
    economy: str
    economy_weight: float | None
    is_primary: bool
    is_secondary: bool


class StationV1(V1Model):
    station_id: str
    market_id: str | None
    name: str
    station_type: str | None
    distance_from_arrival_ls: float | None
    body_id: str | None
    association_state: str
    lifecycle_state: str
    economies: list[StationEconomyV1]


def _numeric_system_id(value: str | int) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        raise HTTPException(422, "system_id must be an unsigned decimal int64") from None
    if numeric < 0 or numeric > 9_223_372_036_854_775_807:
        raise HTTPException(422, "system_id is outside signed int64 range")
    return numeric


async def current_canonical_generation(pool: asyncpg.Pool) -> tuple[str, GenerationV1]:
    """Resolve the single published canonical generation (schema, identity).

    Shared by every generation-pinned canonical read (this router and the
    share/OG preview). 503 when nothing is published; 500 when the pointer
    names an unsafe relation schema.
    """
    row = await _bounded(pool.fetchrow(
        """SELECT generation.generation_id,generation.generation_key,
                  generation.relation_schema,current.publication_sequence,current.published_at
             FROM v3_meta.current_canonical_generation current
             JOIN v3_meta.canonical_generation generation USING(generation_id)
            WHERE current.singleton"""
    ))
    if row is None:
        raise HTTPException(503, "No published V3 canonical generation")
    schema = str(row["relation_schema"])
    if not _SCHEMA.fullmatch(schema):
        raise HTTPException(500, "Unsafe canonical generation relation schema")
    generation = GenerationV1(
        generation_id=str(row["generation_id"]),
        generation_key=str(row["generation_key"]),
        publication_sequence=str(row["publication_sequence"]),
        published_at=row["published_at"].isoformat(),
    )
    return schema, generation


@router.get("/generation", response_model=GenerationV1, operation_id="getV3CurrentGeneration")
async def current_generation(pool: asyncpg.Pool = Depends(get_readonly_pool)) -> GenerationV1:
    _, generation = await current_canonical_generation(pool)
    return generation


@router.get("/systems/{system_id}", response_model=SystemV1, operation_id="getV3System")
async def system(system_id: SystemId, pool: asyncpg.Pool = Depends(get_readonly_pool)) -> SystemV1:
    numeric_id = _numeric_system_id(system_id)
    schema, _ = await current_canonical_generation(pool)
    row = await _bounded(pool.fetchrow(
        f"""SELECT id64,name,x_ly,y_ly,z_ly,source_body_count,loaded_body_count,
                   lifecycle_state,source_updated_at
              FROM {schema}.systems WHERE id64=$1""",
        numeric_id,
    ))
    if row is None:
        raise HTTPException(404, f"V3 system {system_id} not found")
    return SystemV1(
        id64=str(row["id64"]), name=row["name"], x_ly=row["x_ly"], y_ly=row["y_ly"], z_ly=row["z_ly"],
        source_body_count=row["source_body_count"], loaded_body_count=row["loaded_body_count"],
        lifecycle_state=row["lifecycle_state"],
        source_updated_at=row["source_updated_at"].isoformat() if row["source_updated_at"] else None,
    )


@router.get("/systems/{system_id}/bodies", response_model=list[BodyV1], operation_id="listV3SystemBodies")
async def bodies(system_id: SystemId, pool: asyncpg.Pool = Depends(get_readonly_pool)) -> list[BodyV1]:
    numeric_id = _numeric_system_id(system_id)
    schema, generation = await current_canonical_generation(pool)
    # Subtype enrichment is optional: an absent published derived generation or
    # mechanics row must never make the canonical body inventory unavailable.
    # Match canonical lineage because body_pk is local to its generation, and
    # require subtype_present so broad fallback values are not reported as facts.
    # Finder product publication is unrelated to these published mechanics.
    rows = await _bounded(pool.fetch(
        f"""SELECT body.body_pk,body.source_body_id64,body.frontier_body_id,
                   body.direct_parent_body_pk,body.name,body_type.public_code AS body_type,
                   body.is_main_star,body.spectral_class,body.luminosity_class,body.is_landable,
                   body.radius_km,body.planet_mass_earth,body.stellar_mass_solar,
                   body.surface_pressure_atm,body.semi_major_axis_km,body.distance_from_arrival_ls,
                   body.lifecycle_state,mechanics.body_class
              FROM {schema}.bodies body
              LEFT JOIN v3_vocab.body_type body_type USING(body_type_id)
              LEFT JOIN LATERAL (
                  SELECT mechanics.body_class
                    FROM v3_meta.current_derived_generation current
                    JOIN v3_meta.derived_generation derived USING(derived_generation_id)
                    JOIN v3_derived.body_mechanics mechanics USING(derived_generation_id)
                   WHERE current.singleton AND derived.lifecycle_state='PUBLISHED'
                     AND derived.canonical_generation_id=$2::uuid
                     AND mechanics.system_id64=body.system_id64
                     AND mechanics.body_pk=body.body_pk AND mechanics.subtype_present
              ) mechanics ON true
             WHERE body.system_id64=$1 ORDER BY body.body_pk""",
        numeric_id,
        generation.generation_id,
    ))
    signal_rows = await _bounded(pool.fetch(
        f"""SELECT signal.body_pk,signal_type.public_code AS signal_type,signal.signal_count
              FROM {schema}.body_signal_current signal
              JOIN {schema}.bodies body USING(body_pk)
              JOIN v3_vocab.signal_type signal_type USING(signal_type_id)
             WHERE body.system_id64=$1 ORDER BY signal.body_pk,signal_type.signal_type_id""",
        numeric_id,
    ))
    signals_by_body: dict[int, list[BodySignalV1]] = {}
    for signal in signal_rows:
        signals_by_body.setdefault(int(signal["body_pk"]), []).append(BodySignalV1(
            signal_type=signal["signal_type"],
            count=signal["signal_count"],
        ))
    return [BodyV1(
        body_id=str(row["body_pk"]),
        source_body_id64=str(row["source_body_id64"]) if row["source_body_id64"] is not None else None,
        frontier_body_id=str(row["frontier_body_id"]) if row["frontier_body_id"] is not None else None,
        direct_parent_body_id=str(row["direct_parent_body_pk"]) if row["direct_parent_body_pk"] is not None else None,
        name=row["name"], body_type=row["body_type"],
        body_class=row["body_class"],
        body_class_source="ratings-v4-derived" if row["body_class"] is not None else None,
        is_main_star=row["is_main_star"], spectral_class=row["spectral_class"],
        luminosity_class=row["luminosity_class"], is_landable=row["is_landable"],
        radius_km=row["radius_km"],
        planet_mass_earth=row["planet_mass_earth"], stellar_mass_solar=row["stellar_mass_solar"],
        surface_pressure_atm=row["surface_pressure_atm"], semi_major_axis_km=row["semi_major_axis_km"],
        distance_from_arrival_ls=row["distance_from_arrival_ls"],
        lifecycle_state=row["lifecycle_state"],
        signals=signals_by_body.get(int(row["body_pk"]), []),
    ) for row in rows]


@router.get("/systems/{system_id}/rings", response_model=list[RingV1], operation_id="listV3SystemRings")
async def rings(system_id: SystemId, pool: asyncpg.Pool = Depends(get_readonly_pool)) -> list[RingV1]:
    numeric_id = _numeric_system_id(system_id)
    schema, _ = await current_canonical_generation(pool)
    rows = await _bounded(pool.fetch(
        f"""SELECT ring.ring_pk,ring.body_pk,ring.source_ring_id64,ring.kind,ring.name,
                   ring_type.public_code AS ring_type,reserve_type.public_code AS reserve_type,
                   ring.inner_radius_km,ring.outer_radius_km,ring.mass_mt,ring.signals_complete,
                   ring.source_updated_at,ring.lifecycle_state
              FROM {schema}.rings ring
              LEFT JOIN v3_vocab.ring_type ring_type USING(ring_type_id)
              LEFT JOIN v3_vocab.reserve_type reserve_type USING(reserve_type_id)
             WHERE ring.system_id64=$1 ORDER BY ring.ring_pk""",
        numeric_id,
    ))
    signal_rows = await _bounded(pool.fetch(
        f"""SELECT signal.ring_pk,signal_type.public_code AS signal_type,signal.signal_count
              FROM {schema}.ring_signal_current signal
              JOIN {schema}.rings ring USING(ring_pk)
              JOIN v3_vocab.signal_type signal_type USING(signal_type_id)
             WHERE ring.system_id64=$1 ORDER BY signal.ring_pk,signal_type.signal_type_id""",
        numeric_id,
    ))
    signals_by_ring: dict[int, list[RingSignalV1]] = {}
    for signal in signal_rows:
        signals_by_ring.setdefault(int(signal["ring_pk"]), []).append(RingSignalV1(
            signal_type=signal["signal_type"],
            count=signal["signal_count"],
        ))
    return [RingV1(
        ring_id=str(row["ring_pk"]),
        body_id=str(row["body_pk"]),
        source_ring_id64=str(row["source_ring_id64"]) if row["source_ring_id64"] is not None else None,
        kind=row["kind"],
        name=row["name"],
        ring_type=row["ring_type"],
        reserve_type=row["reserve_type"],
        inner_radius_km=row["inner_radius_km"],
        outer_radius_km=row["outer_radius_km"],
        mass_mt=row["mass_mt"],
        signals_complete=row["signals_complete"],
        source_updated_at=row["source_updated_at"].isoformat() if row["source_updated_at"] else None,
        lifecycle_state=row["lifecycle_state"],
        signals=signals_by_ring.get(int(row["ring_pk"]), []),
    ) for row in rows]


@router.get("/systems/{system_id}/stations", response_model=list[StationV1], operation_id="listV3SystemStations")
async def stations(system_id: SystemId, pool: asyncpg.Pool = Depends(get_readonly_pool)) -> list[StationV1]:
    numeric_id = _numeric_system_id(system_id)
    schema, _ = await current_canonical_generation(pool)
    rows = await _bounded(pool.fetch(
        f"""SELECT station.station_pk,station.market_id,station.name,
                   station_type.public_code AS station_type,station.distance_from_arrival_ls,
                   placement.body_pk,placement.association_state,station.lifecycle_state
              FROM {schema}.stations station
              LEFT JOIN v3_vocab.station_type station_type USING(station_type_id)
              JOIN {schema}.station_placement placement USING(station_pk,system_id64)
             WHERE station.system_id64=$1 ORDER BY station.station_pk""",
         numeric_id,
    ))
    station_pks = [row["station_pk"] for row in rows]
    economies_by_station: dict = {}
    if station_pks:
        economy_rows = await _bounded(pool.fetch(
            f"""SELECT economy_current.station_pk, economy.public_code,
                       economy_current.economy_weight,
                       economy_current.is_primary, economy_current.is_secondary
                  FROM {schema}.station_economy_current economy_current
                  JOIN v3_vocab.economy economy USING(economy_id)
                 WHERE economy_current.station_pk = ANY($1::bigint[])
                 ORDER BY economy_current.station_pk, economy.economy_id""",
            station_pks,
        ))
        for economy in economy_rows:
            economies_by_station.setdefault(economy["station_pk"], []).append(
                StationEconomyV1(
                    economy=economy["public_code"],
                    economy_weight=economy["economy_weight"],
                    is_primary=economy["is_primary"],
                    is_secondary=economy["is_secondary"],
                )
            )
    result: list[StationV1] = []
    for row in rows:
        result.append(StationV1(
            station_id=str(row["station_pk"]),
            market_id=str(row["market_id"]) if row["market_id"] is not None else None,
            name=row["name"], station_type=row["station_type"],
            distance_from_arrival_ls=row["distance_from_arrival_ls"],
            body_id=str(row["body_pk"]) if row["body_pk"] is not None else None,
            association_state=row["association_state"],
            lifecycle_state=row["lifecycle_state"],
            economies=economies_by_station.get(row["station_pk"], []),
        ))
    return result
