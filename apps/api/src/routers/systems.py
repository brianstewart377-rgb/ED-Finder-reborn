"""System / body / batch detail endpoints."""
import json
from typing import Any, Optional

import asyncpg
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request

from edfinder_api.body_sorting import natural_body_sort_key_string, sort_bodies_by_hierarchy
from edfinder_api.config import settings
from edfinder_api.deps import get_pool, get_redis, cache_get, cache_set
from edfinder_api.helpers import sys_row_to_dict
from edfinder_api.models import SystemDetailResponse
from ratings_breakdown import reconstruct_score_breakdown
from edfinder_api.station_body_resolver import (
    is_transient_non_slot_station_type,
    resolve_station_body_association,
)

try:
    import edfinder_api.local_search as _ls
    _LS_AVAILABLE = True
except ImportError:
    _ls = None  # type: ignore
    _LS_AVAILABLE = False

router = APIRouter(tags=['systems'])

SYSTEM_CACHE_VERSION = 'v7'
BODY_CACHE_VERSION = 'v3'




# ---------------------------------------------------------------------------
# System detail
# ---------------------------------------------------------------------------
@router.get('/api/system/{id64}', response_model=SystemDetailResponse)
async def get_system(
    id64: int,
    pool: asyncpg.Pool = Depends(get_pool),
    redis: Optional[aioredis.Redis] = Depends(get_redis),
):
    cache_key = f'sys:{SYSTEM_CACHE_VERSION}:{id64}'
    cached = await cache_get(cache_key, redis)
    if cached:
        return cached

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT s.*,
                m.primary_archetype, m.secondary_archetype,
                m.archetype_confidence, m.overall_development_potential,
                m.buildability_score, m.build_complexity,
                m.purity_score, m.contamination_risk, m.est_total_slots,
                r.score, r.score_agriculture, r.score_refinery,
                r.score_industrial, r.score_hightech,
                r.score_military, r.score_tourism,
                r.score_extraction,
                r.economy_suggestion, r.elw_count, r.ww_count,
                r.ammonia_count, r.gas_giant_count, r.landable_count,
                r.terraformable_count, r.bio_signal_total, r.geo_signal_total,
                r.neutron_count, r.black_hole_count, r.white_dwarf_count,
                r.slots, r.body_quality, r.orbital_safety,
                r.rocky_count, r.rocky_ice_count, r.icy_count, r.hmc_count,
                r.terraforming_potential, r.body_diversity,
                r.confidence, r.rationale, r.rating_version,
                body_fresh.body_data_updated_at::text AS body_data_updated_at,
                body_fresh.body_data_sources,
                COALESCE(s.eddn_updated_at, s.updated_at)::text AS status_updated_at,
                CASE
                    WHEN s.eddn_updated_at IS NOT NULL THEN 'eddn'
                    WHEN s.updated_at IS NOT NULL THEN 'canonical'
                    ELSE NULL
                END AS status_source
            FROM systems s
            LEFT JOIN mv_archetype_rankings m ON m.id64 = s.id64
            LEFT JOIN ratings r ON r.system_id64 = s.id64
            LEFT JOIN LATERAL (
                SELECT
                    MAX(sf.updated_at) AS body_data_updated_at,
                    ARRAY(
                        SELECT DISTINCT src.source
                        FROM (
                            SELECT unnest(COALESCE(sf2.data_sources, ARRAY[]::text[])) AS source
                              FROM body_scan_facts sf2
                             WHERE sf2.system_address = s.id64
                        ) src
                        WHERE src.source IS NOT NULL
                          AND src.source <> ''
                        ORDER BY src.source
                    ) AS body_data_sources
                FROM body_scan_facts sf
                WHERE sf.system_address = s.id64
            ) body_fresh ON TRUE
            WHERE s.id64 = $1
        """, id64)

        if not row:
            raise HTTPException(404, f'System {id64} not found')

        bodies = await conn.fetch("""
            SELECT b.id, b.name, b.subtype, b.body_type,
                   b.distance_from_star, b.is_landable, b.is_terraformable,
                   b.is_earth_like, b.is_water_world, b.is_ammonia_world,
                   b.bio_signal_count, b.geo_signal_count,
                   b.surface_temp, b.radius, b.mass, b.gravity,
                   b.estimated_mapping_value, b.estimated_scan_value,
                   b.is_main_star, b.spectral_class, b.is_scoopable,
                   f.is_ringed AS _scan_is_ringed,
                   f.data_sources AS _scan_data_sources,
                   r.rings AS _rings,
                   r.ring_count AS _ring_count,
                   r.ring_sources AS _ring_sources,
                   r.ring_confidences AS _ring_confidences
            FROM bodies b
            LEFT JOIN LATERAL (
                SELECT sf.is_ringed, sf.data_sources
                FROM body_scan_facts sf
                WHERE sf.system_address = b.system_id64
                  AND (
                      sf.body_id::bigint = b.id
                      OR sf.body_name = b.name
                  )
                ORDER BY (sf.body_id::bigint = b.id) DESC,
                         (sf.body_name = b.name) DESC,
                         sf.updated_at DESC
                LIMIT 1
            ) f ON TRUE
            LEFT JOIN LATERAL (
                SELECT
                    jsonb_agg(
                        jsonb_build_object(
                            'ring_name', br.ring_name,
                            'ring_type', br.ring_type,
                            'ring_class', br.ring_class,
                            'mass_mt', br.mass_mt,
                            'inner_radius', br.inner_radius,
                            'outer_radius', br.outer_radius,
                            'source', br.source,
                            'confidence', br.confidence,
                            'updated_at', br.updated_at
                        )
                        ORDER BY br.ring_name NULLS LAST, br.id
                    ) AS rings,
                    COUNT(*)::int AS ring_count,
                    array_agg(DISTINCT br.source) AS ring_sources,
                    array_agg(DISTINCT br.confidence) AS ring_confidences
                FROM body_rings br
                WHERE br.system_id64 = b.system_id64
                  AND br.body_id = b.id
                  AND br.association_status = 'local_matched'
            ) r ON TRUE
            WHERE b.system_id64 = $1
            ORDER BY b.id ASC
        """, id64)

        has_station_links = await conn.fetchval("SELECT to_regclass('public.station_body_links') IS NOT NULL")
        if has_station_links:
            stations = await conn.fetch("""
                SELECT s.id, s.id AS market_id, s.system_id64, s.name, s.station_type,
                       s.distance_from_star, s.body_name AS station_body_name,
                       s.distance_source, s.distance_confidence, s.distance_updated_at,
                       s.station_type_source, s.station_type_confidence, s.station_type_updated_at,
                       s.body_name_source, s.body_name_confidence, s.body_name_updated_at,
                       COALESCE(l.body_name, s.body_name) AS body_name,
                       l.body_id, l.lane, l.association_status,
                       l.association_confidence, l.association_source,
                       l.resolver_notes,
                       s.landing_pad_size, s.primary_economy, s.secondary_economy,
                       s.has_market, s.has_shipyard, s.has_outfitting,
                       s.has_refuel, s.has_repair, s.has_rearm
                FROM stations s
                LEFT JOIN station_body_links l ON l.station_id = s.id
                WHERE s.system_id64 = $1
            """, id64)
        else:
            stations = await conn.fetch("""
                SELECT id, id AS market_id, system_id64, name, station_type, distance_from_star,
                       distance_source, distance_confidence, distance_updated_at,
                       station_type_source, station_type_confidence, station_type_updated_at,
                       body_name_source, body_name_confidence, body_name_updated_at,
                       body_name AS station_body_name, body_name,
                       NULL::bigint AS body_id,
                       NULL::text AS lane,
                       NULL::text AS association_status,
                       NULL::text AS association_confidence,
                       NULL::text AS association_source,
                       NULL::text AS resolver_notes,
                       landing_pad_size, primary_economy, secondary_economy,
                       has_market, has_shipyard, has_outfitting,
                       has_refuel, has_repair, has_rearm
                FROM stations WHERE system_id64 = $1
            """, id64)

    d = sys_row_to_dict(row)
    score_breakdown_bodies = [
        {
            'subtype': b['subtype'],
            'geo_signal_count': b['geo_signal_count'],
            'bio_signal_count': b['bio_signal_count'],
            'has_rings': bool(b['_ring_count']),
        }
        for b in bodies
    ]
    d['score_breakdown'] = reconstruct_score_breakdown(d, score_breakdown_bodies)
    d['bodies']   = sort_bodies_by_hierarchy(
        [_body_payload_from_row(b, d.get('name')) for b in bodies],
        system_name=d.get('name'),
    )
    d['stations'] = [_station_with_association(dict(s), d['bodies']) for s in stations]

    # Exploration value estimator (Data Enrichment #2)
    total_scan  = sum(b.get('estimated_scan_value',    0) or 0 for b in d['bodies'])
    total_map   = sum(b.get('estimated_mapping_value', 0) or 0 for b in d['bodies'])
    d['exploration_value'] = {
        'total_scan_value':    total_scan,
        'total_mapping_value': total_map,
        'combined_value':      total_scan + total_map,
    }

    result = {'record': d, 'system': d}
    await cache_set(cache_key, result, settings.ttl_system, redis)
    return result


def _station_with_association(station: dict, bodies: list[dict]) -> dict:
    if is_transient_non_slot_station_type(station.get('station_type')):
        association = resolve_station_body_association(station, bodies)
        station.update(association.to_api_dict())
        return station
    if station.get('association_status'):
        return station
    association = resolve_station_body_association(station, bodies)
    station.update(association.to_api_dict())
    return station


def _body_payload_from_row(row: Any, system_name: Optional[str] = None) -> dict[str, Any]:
    body = dict(row)
    scan_is_ringed = body.pop('_scan_is_ringed', None)
    scan_data_sources = body.pop('_scan_data_sources', None)
    rings = _normalise_ring_payload_from_db(body.pop('_rings', None))
    body.pop('_ring_count', None)
    ring_sources = _normalise_text_array(body.pop('_ring_sources', None))
    ring_confidences = _normalise_text_array(body.pop('_ring_confidences', None))
    is_ringed, ring_state = _ring_fields_from_sources(scan_is_ringed, scan_data_sources, rings)
    body['is_ringed'] = is_ringed
    body['ring_state'] = ring_state
    body['rings'] = rings if rings or is_ringed is False else None
    body['ring_count'] = len(rings) if rings else 0 if is_ringed is False else None
    body['ring_source'] = ','.join(ring_sources) if ring_sources else None
    body['ring_confidence'] = ','.join(ring_confidences) if ring_confidences else None
    body['body_sort_key'] = natural_body_sort_key_string(body.get('name'), system_name)
    return body


def _ring_fields_from_scan_fact(is_ringed: Any, data_sources: Any) -> tuple[Optional[bool], str]:
    return _ring_fields_from_sources(is_ringed, data_sources, [])


def _ring_fields_from_sources(is_ringed: Any, data_sources: Any, rings: list[dict[str, Any]]) -> tuple[Optional[bool], str]:
    if rings:
        return True, 'ringed'
    sources = _normalise_data_sources(data_sources)
    if 'eddn_scan' not in sources:
        return None, 'unknown'
    if is_ringed is None:
        return None, 'unknown'
    known_value = _coerce_bool(is_ringed)
    if known_value is None:
        return None, 'unknown'
    if known_value is True:
        return None, 'unknown'
    return known_value, 'ringed' if known_value else 'not_ringed'


def _normalise_ring_payload_from_db(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [dict(row) for row in raw if isinstance(row, dict)]


def _normalise_text_array(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        text = values.strip()
        if text.startswith('{') and text.endswith('}'):
            return [part.strip().strip('"') for part in text[1:-1].split(',') if part.strip()]
        return [text] if text else []
    try:
        return sorted({str(value) for value in values if value is not None})
    except TypeError:
        return []


def _coerce_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {'true', 't', '1', 'yes'}:
        return True
    if text in {'false', 'f', '0', 'no'}:
        return False
    return None


def _normalise_data_sources(data_sources: Any) -> set[str]:
    if data_sources is None:
        return set()
    if isinstance(data_sources, str):
        value = data_sources.strip()
        if value.startswith('{') and value.endswith('}'):
            return {
                part.strip().strip('"')
                for part in value[1:-1].split(',')
                if part.strip()
            }
        return {value}
    try:
        return {str(value) for value in data_sources if value is not None}
    except TypeError:
        return set()


@router.get('/api/local/system/{id64}')
async def local_get_system(
    id64: int,
    pool: asyncpg.Pool = Depends(get_pool),
    redis: Optional[aioredis.Redis] = Depends(get_redis),
):
    return await get_system(id64, pool, redis)


# ---------------------------------------------------------------------------
# Body detail
# ---------------------------------------------------------------------------
@router.get('/api/body/{body_id}')
async def get_body(
    body_id: int,
    pool: asyncpg.Pool = Depends(get_pool),
    redis: Optional[aioredis.Redis] = Depends(get_redis),
):
    cache_key = f'body:{BODY_CACHE_VERSION}:{body_id}'
    cached = await cache_get(cache_key, redis)
    if cached:
        return cached

    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM bodies WHERE id = $1', body_id)
        if not row:
            raise HTTPException(404, f'Body {body_id} not found')

    result = dict(row)
    await cache_set(cache_key, result, settings.ttl_system, redis)
    return result


# ---------------------------------------------------------------------------
# Batch system lookup
# ---------------------------------------------------------------------------
@router.post('/api/systems/batch')
async def batch_systems(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
    redis: Optional[aioredis.Redis] = Depends(get_redis),
):
    body = await request.json()
    id64s = body.get('ids', body.get('id64s', []))  # frontend sends 'ids'; 'id64s' kept for back-compat
    if not id64s or len(id64s) > 500:
        raise HTTPException(400, 'Provide 1-500 ids')

    result: dict[str, Any] = {}
    missing: list[int] = []
    for id64 in id64s:
        cached = await cache_get(f'sys:{SYSTEM_CACHE_VERSION}:{id64}', redis)
        if cached:
            result[str(id64)] = cached.get('record') or cached
        else:
            missing.append(id64)

    if missing:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT s.*,
                    r.score, r.score_agriculture, r.score_refinery,
                    r.score_industrial, r.score_hightech,
                    r.score_military, r.score_tourism, r.score_extraction,
                    r.economy_suggestion, r.rating_version,
                    r.slots, r.body_quality, r.orbital_safety,
                    r.terraforming_potential, r.body_diversity,
                    r.rocky_count, r.rocky_ice_count, r.icy_count, r.hmc_count,
                    r.confidence, r.rationale,
                    r.elw_count, r.ww_count, r.ammonia_count,
                    r.gas_giant_count, r.landable_count, r.terraformable_count,
                    r.bio_signal_total, r.geo_signal_total,
                    r.neutron_count, r.black_hole_count, r.white_dwarf_count
                FROM systems s
                LEFT JOIN ratings r ON r.system_id64 = s.id64
                WHERE s.id64 = ANY($1::bigint[])
            """, missing)

            bodies_rows = await conn.fetch("""
                SELECT b.system_id64, b.id, b.name, b.subtype,
                       b.distance_from_star, b.is_landable, b.is_terraformable,
                       b.is_earth_like, b.is_water_world, b.is_ammonia_world,
                       b.bio_signal_count, b.geo_signal_count,
                       b.estimated_mapping_value, b.estimated_scan_value,
                       f.is_ringed AS _scan_is_ringed,
                       f.data_sources AS _scan_data_sources,
                       r.rings AS _rings,
                       r.ring_count AS _ring_count,
                       r.ring_sources AS _ring_sources,
                       r.ring_confidences AS _ring_confidences
                FROM bodies b
                LEFT JOIN LATERAL (
                    SELECT sf.is_ringed, sf.data_sources
                    FROM body_scan_facts sf
                    WHERE sf.system_address = b.system_id64
                      AND (
                          sf.body_id::bigint = b.id
                          OR sf.body_name = b.name
                      )
                    ORDER BY (sf.body_id::bigint = b.id) DESC,
                             (sf.body_name = b.name) DESC,
                             sf.updated_at DESC
                    LIMIT 1
                ) f ON TRUE
                LEFT JOIN LATERAL (
                    SELECT
                        jsonb_agg(
                            jsonb_build_object(
                                'ring_name', br.ring_name,
                                'ring_type', br.ring_type,
                                'ring_class', br.ring_class,
                                'mass_mt', br.mass_mt,
                                'inner_radius', br.inner_radius,
                                'outer_radius', br.outer_radius,
                                'source', br.source,
                                'confidence', br.confidence,
                                'updated_at', br.updated_at
                            )
                            ORDER BY br.ring_name NULLS LAST, br.id
                        ) AS rings,
                        COUNT(*)::int AS ring_count,
                        array_agg(DISTINCT br.source) AS ring_sources,
                        array_agg(DISTINCT br.confidence) AS ring_confidences
                    FROM body_rings br
                    WHERE br.system_id64 = b.system_id64
                      AND br.body_id = b.id
                      AND br.association_status = 'local_matched'
                ) r ON TRUE
                WHERE b.system_id64 = ANY($1::bigint[])
                ORDER BY b.system_id64, b.id ASC
            """, missing)

        system_names = {row['id64']: row['name'] for row in rows}
        bodies_by_system: dict[int, list] = {}
        raw_bodies_by_system: dict[int, list] = {}
        for b in bodies_rows:
            bid = b['system_id64']
            bodies_by_system.setdefault(bid, []).append(_body_payload_from_row(b, system_names.get(bid)))
            raw_bodies_by_system.setdefault(bid, []).append({
                'subtype': b['subtype'],
                'geo_signal_count': b['geo_signal_count'],
                'bio_signal_count': b['bio_signal_count'],
                'has_rings': bool(b['_ring_count']),
            })
        for bid, system_bodies in bodies_by_system.items():
            bodies_by_system[bid] = sort_bodies_by_hierarchy(
                system_bodies,
                system_name=system_names.get(bid),
            )

        for row in rows:
            d = sys_row_to_dict(row)
            d['bodies'] = bodies_by_system.get(d['id64'], [])
            d['score_breakdown'] = reconstruct_score_breakdown(
                d, raw_bodies_by_system.get(d['id64'], []),
            )
            result[str(d['id64'])] = d
            await cache_set(f'sys:{SYSTEM_CACHE_VERSION}:{d["id64"]}', {'record': d}, settings.ttl_system, redis)

    return {'systems': result}
