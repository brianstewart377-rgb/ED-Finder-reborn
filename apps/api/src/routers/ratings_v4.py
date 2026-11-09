"""Read-only, generation-pinned Ratings V4 API.

Raw V4 economy potential is served independently. Finder ranking and archetype
judgement remain separate layers and are deliberately absent from this route.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime
import json
from typing import Any, Mapping

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel

from edfinder_api.config import limiter
from edfinder_api.cpu_work import CpuWorkBusy, run_cpu_bound
from edfinder_api.deps import get_readonly_pool
from edfinder_api.domain.ratings_v4 import BodyFact, ECONOMIES, SystemFacts, opportunities_from_facts, rate_all

router = APIRouter(prefix='/api/ratings/v4', tags=['ratings-v4'])

_BODY_FIELDS = ('body_class', 'spectral_class', 'luminosity_class', 'rings',
                'biologicals', 'geologicals', 'volcanism', 'terraformable',
                'tidally_locked', 'is_main_star', 'reserve_level', 'usable_ground_opportunity')
_VECTOR_FIELDS = ('potential', 'quality', 'quality_min', 'quality_max', 'completeness', 'confidence')
EXPLANATION_CACHE_SIZE = 256
# Accessed only on the event loop; published ratings generations are immutable.
_explanation_cache: OrderedDict[tuple[str, int, str, str, str, int], dict[str, Any]] = OrderedDict()


class RatingSummaryResponse(BaseModel):
    economy: str
    potential_score: int
    specialisation_quality: int | None
    specialisation_quality_min: int
    specialisation_quality_max: int
    evidence_completeness: float
    confidence: float


class EvidenceFeatureResponse(BaseModel):
    feature_type: str
    known: bool
    weight: float
    confidence: float
    provenance: str | None
    value: str | bool | None


class SpecialisationConstraintResponse(BaseModel):
    rule_id: str
    satisfied: bool | None
    feature_type: str
    confidence: float
    provenance: str | None


class CandidateContributionResponse(BaseModel):
    candidate_id: str
    native: bool
    modifier: bool
    base_score: int
    positive_rules: tuple[str, ...]
    negative_rules: tuple[str, ...]
    positive_adjustment: int
    negative_adjustment: int
    local_score: int
    system_weight: float
    contributors: tuple[str, ...]
    evidence: tuple[EvidenceFeatureResponse, ...]


class CandidateSpecialisationResponse(BaseModel):
    candidate_id: str
    intrinsic_quality: int
    quality: int | None
    minimum_quality: int
    maximum_quality: int
    competing_economies: tuple[str, ...]
    preferred_specialisation: bool
    constraints: tuple[SpecialisationConstraintResponse, ...]


class RatingExplanationResponse(RatingSummaryResponse):
    best_candidate_id: str | None
    local_scores: tuple[int, ...]
    explanation: tuple[str, ...]
    mechanics_version: str
    scorer_version: str
    contributions: tuple[CandidateContributionResponse, ...]
    best_specialisation_candidate_id: str | None
    specialisation_candidates: tuple[CandidateSpecialisationResponse, ...]


class GenerationResponse(BaseModel):
    derived_generation_id: str
    publication_sequence: int
    published_at: datetime
    canonical_generation_id: str
    canonical_publication_sequence: int
    scorer_version: str
    mechanics_version: str
    validation_receipt: dict[str, Any]


class SystemRatingsResponse(BaseModel):
    derived_generation_id: str
    publication_sequence: int
    canonical_generation_id: str
    canonical_publication_sequence: int
    scorer_version: str
    mechanics_version: str
    system_id64: int
    ratings: list[RatingSummaryResponse]


class SystemExplanationResponse(BaseModel):
    derived_generation_id: str
    publication_sequence: int
    canonical_generation_id: str
    canonical_publication_sequence: int
    scorer_version: str
    mechanics_version: str
    system_id64: int
    ratings: list[RatingExplanationResponse]


def _provenance(value: Mapping[str, Any], *, field: str) -> str:
    return json.dumps(value | {'field': field}, sort_keys=True, separators=(',', ':'), default=str)


def _facts(manifest: Mapping[str, Any], vector: Mapping[str, Any], body_rows: list[Mapping[str, Any]]) -> SystemFacts:
    """Replay explanations from compact immutable inputs without new source claims."""
    base = {
        'derived_generation_id': str(vector['derived_generation_id']),
        'canonical_generation_id': manifest['canonical_generation_id'],
        'canonical_schema': manifest['canonical_schema'],
        'source_run_id': manifest['source_metadata']['run']['source_run_id'],
        'artifact_sha256': manifest['source_metadata']['artifact']['content_sha256'].removeprefix('\\x'),
        'system_id64': vector['system_id64'],
    }
    bodies = []
    for row in body_rows:
        if row['system_id64'] != vector['system_id64'] or row['derived_generation_id'] != vector['derived_generation_id']:
            raise ValueError('conflicting materialized mechanics identity')
        body_base = base | {'body_pk': row['body_pk'], 'source_body_id64': row['source_body_id64'],
                            'mechanics_relation': 'v3_derived.body_mechanics'}
        provenance = {field: _provenance(body_base, field=field) for field in _BODY_FIELDS}
        provenance['provenance'] = _provenance(body_base, field='body')
        if row['subtype_present']:
            provenance['body_class'] = _provenance(body_base | {
                'source': 'Retained Spansh galaxy artifact',
                # The subtype projection seals the containing source record.
                # It is deliberately stored once on the compact system vector,
                # rather than copied onto every body mechanics row.
                'source_projection_sha256': bytes(vector['subtype_projection_sha256']).hex(),
                'source_updated_at': row['subtype_observed_at'],
            }, field='subType')
        else:
            provenance['body_class'] = _provenance(body_base | {
                'relation': 'bodies', 'subtype_state': 'UNKNOWN'}, field='body_type_id')
        facts = {field: row[field] for field in _BODY_FIELDS}
        bodies.append(BodyFact(candidate_id=str(row['body_pk']), reserve_scope='body',
                               feature_provenance=provenance, **facts))
    if len(bodies) != vector['physical_body_count']:
        raise ValueError('materialized physical body count mismatch')
    system_provenance = _provenance(base | {'relation': 'systems',
                                            'loaded_body_count': vector['loaded_body_count']}, field='body_inventory')
    return SystemFacts(tuple(bodies), body_inventory_complete=True,
                       feature_provenance={'body_inventory': system_provenance, 'provenance': system_provenance})


def _stored_scores(vector: Mapping[str, Any]) -> tuple[list[Any], ...]:
    values = tuple(vector[field] for field in _VECTOR_FIELDS)
    if any(not isinstance(value, list) or len(value) != len(ECONOMIES) for value in values):
        raise ValueError('invalid stored Ratings V4 score vector')
    return values


def _summary(vector: Mapping[str, Any]) -> list[dict[str, Any]]:
    potential, quality, minimum, maximum, completeness, confidence = _stored_scores(vector)
    return [
        {'economy': economy, 'potential_score': potential[index],
         'specialisation_quality': quality[index],
         'specialisation_quality_min': minimum[index], 'specialisation_quality_max': maximum[index],
         'evidence_completeness': completeness[index] / 10000,
         'confidence': confidence[index] / 10000}
        for index, economy in enumerate(ECONOMIES)
    ]


def _system_response(current: Mapping[str, Any], vector: Mapping[str, Any], system_id64: int) -> dict[str, Any]:
    return {
        'derived_generation_id': str(current['derived_generation_id']),
        'publication_sequence': current['publication_sequence'],
        'canonical_generation_id': str(current['canonical_generation_id']),
        'canonical_publication_sequence': current['canonical_publication_sequence'],
        'scorer_version': current['scorer_version'],
        'mechanics_version': current['mechanics_version'],
        'system_id64': system_id64,
        'ratings': _summary(vector),
    }


def _replay_explanation(
    current: Mapping[str, Any], vector: Mapping[str, Any], bodies: list[Mapping[str, Any]], system_id64: int,
) -> dict[str, Any]:
    response = _system_response(current, vector, system_id64)
    facts = _facts(current['manifest'], vector, bodies)
    ratings = rate_all(opportunities_from_facts(facts))
    replayed = [{'economy': economy, **asdict(ratings[economy])} for economy in ECONOMIES]
    if response['ratings'] != [{key: row[key] for key in (
            'economy', 'potential_score', 'specialisation_quality',
            'specialisation_quality_min', 'specialisation_quality_max',
            'evidence_completeness', 'confidence')} for row in replayed]:
        raise ValueError('stored rating differs from immutable explanation replay')
    response['ratings'] = replayed
    return response


async def _current(connection: asyncpg.Connection) -> asyncpg.Record:
    row = await connection.fetchrow('''
        SELECT c.derived_generation_id, c.publication_sequence, c.published_at,
               d.canonical_generation_id, d.canonical_publication_sequence,
               d.scorer_version, d.mechanics_version, d.adapter_version, d.validation_receipt, d.manifest
        FROM v3_meta.current_derived_generation c
        JOIN v3_meta.derived_generation d USING(derived_generation_id)
        WHERE d.lifecycle_state='PUBLISHED'
    ''')
    if row is None:
        raise HTTPException(404, 'No published Ratings V4 generation')
    return row


async def _read_system(pool: asyncpg.Pool, system_id64: int, *, explanation: bool) -> dict[str, Any] | None:
    try:
        async with pool.acquire() as connection:
            async with connection.transaction(readonly=True):
                current = await _current(connection)
                vector = await connection.fetchrow('''
                    SELECT derived_generation_id,system_id64,chunk_ordinal,loaded_body_count,physical_body_count,
                           subtype_projection_sha256,potential,quality,quality_min,quality_max,completeness,confidence
                    FROM v3_derived.system_rating_vector
                    WHERE derived_generation_id=$1 AND system_id64=$2
                ''', current['derived_generation_id'], system_id64)
                if vector is None:
                    return None
                if not explanation:
                    return _system_response(current, vector, system_id64)
                # The publication sequence is part of the response and changes when a
                # retired generation is republished, so it belongs in the key too.
                cache_key = (
                    str(current['derived_generation_id']), current['publication_sequence'],
                    current['scorer_version'], current['mechanics_version'],
                    current['adapter_version'], system_id64,
                )
                cached = _explanation_cache.get(cache_key)
                if cached is not None:
                    _explanation_cache.move_to_end(cache_key)
                    return cached
                bodies = await connection.fetch('''
                    SELECT derived_generation_id,system_id64,body_pk,source_body_id64,body_class,spectral_class,
                           luminosity_class,rings,biologicals,geologicals,volcanism,terraformable,tidally_locked,
                           is_main_star,reserve_level,usable_ground_opportunity,subtype_present,subtype_observed_at
                    FROM v3_derived.body_mechanics
                    WHERE derived_generation_id=$1 AND system_id64=$2 ORDER BY body_pk
                ''', current['derived_generation_id'], system_id64)
        # Release the read-only transaction and pooled connection before replay or admission wait.
        response = await run_cpu_bound(_replay_explanation, current, vector, bodies, system_id64)
        _explanation_cache[cache_key] = response
        _explanation_cache.move_to_end(cache_key)
        if len(_explanation_cache) > EXPLANATION_CACHE_SIZE:
            _explanation_cache.popitem(last=False)
        return response
    except HTTPException:
        raise
    except CpuWorkBusy as exc:
        raise HTTPException(503, 'Server busy, try again shortly', headers={'Retry-After': '2'}) from exc
    except (asyncpg.exceptions.UndefinedTableError,
            asyncpg.exceptions.InvalidSchemaNameError) as exc:
        raise HTTPException(503, 'Ratings V4 generation is unavailable') from exc
    except (asyncpg.exceptions.QueryCanceledError,
            asyncpg.exceptions.LockNotAvailableError) as exc:
        # A statement or lock timeout (for example while a purge swap holds a
        # brief lock) is retryable, as on the Finder routes: 503, not 500.
        raise HTTPException(503, 'Ratings V4 is temporarily unavailable') from exc
    except ValueError as exc:
        raise HTTPException(503, 'Ratings V4 generation failed integrity validation') from exc


@router.get('/generation')
async def generation(pool: asyncpg.Pool = Depends(get_readonly_pool)) -> GenerationResponse:
    try:
        async with pool.acquire() as connection:
            async with connection.transaction(readonly=True):
                current = await _current(connection)
                return {
                    'derived_generation_id': str(current['derived_generation_id']),
                    'publication_sequence': current['publication_sequence'],
                    'published_at': current['published_at'],
                    'canonical_generation_id': str(current['canonical_generation_id']),
                    'canonical_publication_sequence': current['canonical_publication_sequence'],
                    'scorer_version': current['scorer_version'],
                    'mechanics_version': current['mechanics_version'],
                    'validation_receipt': current['validation_receipt'],
                }
    except HTTPException:
        raise
    except (asyncpg.exceptions.UndefinedTableError,
            asyncpg.exceptions.InvalidSchemaNameError) as exc:
        raise HTTPException(503, 'Ratings V4 generation is unavailable') from exc
    except (asyncpg.exceptions.QueryCanceledError,
            asyncpg.exceptions.LockNotAvailableError) as exc:
        # A statement or lock timeout (for example while a purge swap holds a
        # brief lock) is retryable, as on the Finder routes: 503, not 500.
        raise HTTPException(503, 'Ratings V4 is temporarily unavailable') from exc


@router.get('/systems/{system_id64}')
async def system_scores(
    system_id64: int = Path(ge=0, le=9223372036854775807), pool: asyncpg.Pool = Depends(get_readonly_pool),
) -> SystemRatingsResponse:
    result = await _read_system(pool, system_id64, explanation=False)
    if result is None:
        raise HTTPException(404, 'System is not in the published Ratings V4 generation')
    return result


@router.get('/systems/{system_id64}/explanation')
@limiter.shared_limit('30/minute', scope='system_explanation')
async def system_explanation(
    request: Request,
    system_id64: int = Path(ge=0, le=9223372036854775807), pool: asyncpg.Pool = Depends(get_readonly_pool),
) -> SystemExplanationResponse:
    result = await _read_system(pool, system_id64, explanation=True)
    if result is None:
        raise HTTPException(404, 'System is not in the published Ratings V4 generation')
    return result
