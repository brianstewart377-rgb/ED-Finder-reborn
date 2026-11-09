"""
ED Finder — Archetype API Router
Version: 2.0 (V3-native)

Endpoints:
    GET  /api/archetypes/rankings                 ranked list by archetype (V3, generation-pinned)
    POST /api/archetypes/rerank                   client-weighted blend of stored V3 archetype fits
    GET  /api/archetypes/system/{id64}            full V3 archetype breakdown for one system
    GET  /api/archetypes/system/{id64}/explanation on-demand, version-gated V3 explanations
    POST /api/archetypes/simulate                 build plan scored against the stored V3 fit
    GET  /api/archetypes/profiles                 static preset rerank blends (V3 keys)

Every database-backed route here reads generation-pinned from the published
V3 `v3_app.system_search` / `v3_app.system_archetype` projections through
`edfinder_api.ranking` (`/rankings` since F3 Task 4; `/rerank`,
`/system/{id64}` and `/simulate` since F3 slice 2). The legacy V2 relations
(`mv_archetype_rankings`, `system_archetype_scores`,
`system_archetype_traits`, `systems`, `system_slot_topology`,
`economy_pair_synergy`, `body_scan_facts`, `bodies`) are no longer referenced
anywhere in this module; a static test pins that.

Fail-closed availability: `local_search._current_derived_generation` answers
404 with no PUBLISHED generation and 503 until both Finder products
(`system_search`, `system_archetype`) are READY and published (migration
015's product gate). Request bodies and parameters are validated before any
database access so a 422 is never masked by that 503.

Known V3 data gaps, flagged rather than silently degraded: no projection
carries slot/topology estimates, economy-pair synergy, contamination risk,
purity or buildability, so the legacy `/system` topology/pair/trait fields and
the legacy `/simulate` risk estimates are retired from the response models.

There is no Redis cache layer in this router any more: every response is
pinned to the generation resolved inside its own REPEATABLE READ snapshot.

Route scope:
    Development rerank and archetype endpoints live under /api/archetypes/*.
"""

from collections.abc import Mapping
import hashlib
import math
from pathlib import Path as FilePath
import re
import time
from typing import Annotated, Any, Optional
import uuid

import asyncpg
from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import AfterValidator

from shared_contracts import v3_system_archetype_model as archetype_model
from shared_contracts.v3_system_archetype_model import (
    ARCHETYPE_VERSION,
    OPPORTUNITY_READ_SQL,
    VECTOR_READ_SQL,
    SystemVectorRow,
    explain_raw_system,
    score_raw_system,
)

from edfinder_api.config import log, limiter
from edfinder_api.local_search import _current_derived_generation
from edfinder_api.models import (
    ArchetypeRankingsResponse,
    ArchetypeRerankRequest,
    ArchetypeRerankResponse,
    ArchetypeRerankWeights,
    ArchetypesProfilesResponse,
    BuildSimulateRequest,
    BuildSimulateResponse,
    SystemArchetypeExplanationResponse,
    SystemArchetypeResponse,
)
from edfinder_api.ranking.profile import (
    ACCEPTED_ARCHETYPE_SCORE_FLOORS,
    ARCHETYPE_KEYS,
    ARCHETYPE_SCORE_FLOOR_DEFAULT,
    PICKED_ARCHETYPE_FILTER_REJECTION_REASON,
    PICKED_ARCHETYPE_REJECTED_FILTERS,
    PROFILE_SPEC,
    RANKING_COUNT_SENTINEL,
    RANKING_OFFSET_MAX,
    RANKING_OFFSET_MIN,
    RANKING_OFFSET_PLUS_LIMIT_MAX,
    RANKING_PAGE_LIMIT_MAX,
    RANKING_PAGE_LIMIT_MIN,
    RANKING_VERSION,
    RANKING_WINDOW_LIMIT,
    ranking_sha256,
)
from edfinder_api.ranking.ranking_sql import build_count_query, build_ranked_query
from edfinder_api.ranking.rerank import (
    PPM,
    archetype_entry,
    build_system_archetype_detail_query,
    equal_weights,
    rerank_rows,
    single_archetype_weights,
    validate_id64s,
)
from edfinder_api.state import get_pool_singleton as get_pool

router = APIRouter(prefix='/api/archetypes', tags=['archetypes'])

# ---------------------------------------------------------------------------
# Archetype metadata
# ---------------------------------------------------------------------------
# `ARCHETYPE_KEYS` (imported from `edfinder_api.ranking.profile`) is the 8-key
# set the V3 `system_archetype` builder (`scripts/v3_system_archetype_model.py`)
# scores every system against. These are the only archetype keys this router
# accepts; the 10-key legacy V2 set is retired.
_V3_ARCHETYPE_LABELS: dict[str, str] = {
    'paradise':            'Paradise World',
    'mining_hub':          'Mining Hub',
    'manufacturing_hub':   'Manufacturing Hub',
    'megacomplex':         'Megacomplex',
    'research_hub':        'Research Hub',
    'stronghold':          'Stronghold',
    'population_capital':  'Population Capital',
    'flexible':            'Flexible Multi-Role Colony',
}

_EXPLANATION_UNAVAILABLE_DETAIL = 'explanation unavailable for this product version'
_MODEL_SQL_PARAMETER_ORDER = ('generation_id', 'chunk_ordinal', 'system_id64')
_PSYCOPG_NAMED_PARAMETER = re.compile(r'%\(([^)]+)\)s')


def _psycopg_sql_to_asyncpg(
    sql: str,
    parameter_order: tuple[str, ...],
) -> str:
    """Convert a fixed psycopg named-parameter contract to asyncpg slots."""
    positions = {name: index for index, name in enumerate(parameter_order, 1)}
    referenced = set(_PSYCOPG_NAMED_PARAMETER.findall(sql))
    unknown = referenced - positions.keys()
    if unknown:
        raise ValueError(f'unknown SQL parameters: {sorted(unknown)}')
    missing = positions.keys() - referenced
    if missing:
        raise ValueError(f'missing SQL parameters: {sorted(missing)}')
    return _PSYCOPG_NAMED_PARAMETER.sub(
        lambda match: f'${positions[match.group(1)]}',
        sql,
    )


_VECTOR_READ_ASYNCPG_SQL = _psycopg_sql_to_asyncpg(
    VECTOR_READ_SQL,
    _MODEL_SQL_PARAMETER_ORDER,
)
_OPPORTUNITY_READ_ASYNCPG_SQL = _psycopg_sql_to_asyncpg(
    OPPORTUNITY_READ_SQL,
    _MODEL_SQL_PARAMETER_ORDER,
)
_DEPLOYED_ARCHETYPE_MODEL_FILE_SHA256 = hashlib.sha256(
    FilePath(archetype_model.__file__).read_bytes()
).hexdigest()

_PERSISTED_ARCHETYPE_COLUMNS = tuple(
    column
    for key in archetype_model.ARCHETYPE_KEYS
    for column in (
        f'{key}_score',
        f'{key}_tier',
        f'{key}_confidence_ppm',
        f'{key}_weighted_e4',
    )
) + (
    'primary_archetype',
    'secondary_archetype',
    'best_colony_potential',
    'best_tier',
    'archetype_confidence_ppm',
    'weighted_potential',
    'completeness_ppm',
)

# The shared selectors remain byte-for-byte query contracts. Only their named
# placeholders are converted, then each complete SELECT is embedded as a CTE
# so the wide row and both source reads make one asyncpg round trip. The
# correlated version predicate leaves source columns NULL on a stored-version
# mismatch, allowing the route to return 409 without executing the model.
_SYSTEM_ARCHETYPE_READ_SQL = f'''
SELECT a.archetype_version,
       {', '.join(f'a.{column}' for column in _PERSISTED_ARCHETYPE_COLUMNS)},
       inputs.source_system_id64,
       inputs.potential, inputs.quality, inputs.quality_min, inputs.quality_max,
       inputs.completeness, inputs.confidence,
       inputs.opportunity_ordinals, inputs.opportunity_row_counts,
       inputs.opportunity_local_score_sums
  FROM v3_app.system_archetype a
  LEFT JOIN LATERAL (
      WITH vector_input AS (
{_VECTOR_READ_ASYNCPG_SQL}
      ),
      opportunity_input (
          system_id64, economy_ordinal, row_count, local_score_sum
      ) AS (
{_OPPORTUNITY_READ_ASYNCPG_SQL}
      )
      SELECT v.system_id64 AS source_system_id64,
             v.potential, v.quality, v.quality_min, v.quality_max,
             v.completeness, v.confidence,
             ARRAY(
                 SELECT o.economy_ordinal::integer
                   FROM opportunity_input o
                  ORDER BY o.economy_ordinal
             ) AS opportunity_ordinals,
             ARRAY(
                 SELECT o.row_count::integer
                   FROM opportunity_input o
                  ORDER BY o.economy_ordinal
             ) AS opportunity_row_counts,
             ARRAY(
                 SELECT o.local_score_sum::bigint
                   FROM opportunity_input o
                  ORDER BY o.economy_ordinal
             ) AS opportunity_local_score_sums
        FROM vector_input v
       WHERE v.system_id64 = a.system_id64
         AND a.archetype_version = $4
  ) inputs ON TRUE
 WHERE a.derived_generation_id = $1
   AND a.system_id64 = $3
'''


# ---------------------------------------------------------------------------
# GET /api/archetypes/rankings (F3 Task 4 — V3, generation-pinned)
# ---------------------------------------------------------------------------
# Repointed off the legacy `mv_archetype_rankings` materialized view onto the
# published V3 `v3_app.system_archetype` / `v3_app.system_search` projections
# via `ranking.ranking_sql.build_ranked_query`/`build_count_query`, mirroring
# the `local_db_search_v3` pattern in `edfinder_api.local_search` (F3 Task 3):
# a plain, DB-pool-taking helper function that the thin, rate-limited route
# handler delegates to, so the helper stays directly unit-testable without
# going through slowapi's `Request`-bound limiter decorator.
#
# The picked-archetype access path is deliberately unfiltered: a fixed partial
# index supplies the first 10,000 stored weighted keys, after which the page
# fetch touches at most 500 wide rows. Any hard-filter parameter is rejected
# before SQL construction so it cannot silently change the meaning of that
# bounded window.
async def _archetype_rankings_v3(
    *,
    archetype: str,
    min_score: int,
    galaxy_region: Optional[int],
    max_distance_ly: Optional[float],
    has_elw: Optional[bool],
    min_slots: Optional[int],
    max_contamination: Optional[float],
    limit: int,
    offset: int,
    pool: asyncpg.Pool,
) -> dict:
    t0 = time.monotonic()

    if archetype not in ARCHETYPE_KEYS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown archetype '{archetype}'. "
                f"Valid values: {sorted(ARCHETYPE_KEYS)}"
            ),
        )
    if min_score not in ACCEPTED_ARCHETYPE_SCORE_FLOORS:
        raise HTTPException(
            status_code=422,
            detail=f'min_score must be one of {list(ACCEPTED_ARCHETYPE_SCORE_FLOORS)}',
        )
    if offset < RANKING_OFFSET_MIN or offset > RANKING_OFFSET_MAX:
        raise HTTPException(
            status_code=422,
            detail=f'offset must be between 0 and {RANKING_WINDOW_LIMIT - 1}',
        )
    if limit < RANKING_PAGE_LIMIT_MIN or limit > RANKING_PAGE_LIMIT_MAX:
        raise HTTPException(
            status_code=422,
            detail=(
                f'limit must be between {RANKING_PAGE_LIMIT_MIN} and '
                f'{RANKING_PAGE_LIMIT_MAX}'
            ),
        )
    if offset + limit > RANKING_OFFSET_PLUS_LIMIT_MAX:
        raise HTTPException(
            status_code=422,
            detail=(
                'offset + limit must be less than or equal to '
                f'{RANKING_OFFSET_PLUS_LIMIT_MAX}'
            ),
        )

    filter_values = {
        'galaxy_region': galaxy_region,
        'max_distance_ly': max_distance_ly,
        'has_elw': has_elw,
        'min_slots': min_slots,
        'max_contamination': max_contamination,
    }
    rejected = [
        name
        for name in PICKED_ARCHETYPE_REJECTED_FILTERS
        if filter_values[name] is not None
    ]
    if rejected:
        raise HTTPException(
            status_code=422,
            detail=(
                f'{", ".join(rejected)}: '
                f'{PICKED_ARCHETYPE_FILTER_REJECTION_REASON}'
            ),
        )

    sql, params = build_ranked_query(
        PROFILE_SPEC,
        picked_archetype=archetype,
        picked_economy=None,
        hard_filters={},
        reference_coords=None,
        limit=limit,
        offset=offset,
        min_score=min_score,
    )
    count_sql, count_params = build_count_query(
        PROFILE_SPEC,
        picked_archetype=archetype,
        picked_economy=None,
        hard_filters={},
        reference_coords=None,
        min_score=min_score,
    )

    try:
        async with pool.acquire() as conn:
            # REPEATABLE READ so the generation pin, the ranked page and the
            # COUNT share one snapshot — a governed publish mid-request can no
            # longer return a page and a total from two different generations.
            async with conn.transaction(isolation='repeatable_read', readonly=True):
                generation_row = await _current_derived_generation(conn)
                rows = await conn.fetch(sql, *params)
                total = await conn.fetchval(count_sql, *count_params)
    except HTTPException:
        raise
    except (asyncpg.exceptions.UndefinedTableError,
            asyncpg.exceptions.InvalidSchemaNameError) as exc:
        log.error('archetype_rankings_v3 missing relation: %r (sqlstate=%s)',
                  exc, getattr(exc, 'sqlstate', None))
        raise HTTPException(503, 'Archetype rankings are unavailable') from exc
    except asyncpg.PostgresError as e:
        log.error('archetypes.rankings DB error: %s', e)
        raise HTTPException(
            status_code=503,
            detail={'type': 'https://httpstatuses.com/503',
                    'title': 'Database error', 'status': 503, 'detail': str(e)},
        )

    query_ms = int((time.monotonic() - t0) * 1000)
    results = [_build_v3_ranking_row(row, archetype) for row in rows]
    qualifying = int(total) if total is not None else len(results)

    return {
        'archetype':       archetype,
        'archetype_label': _V3_ARCHETYPE_LABELS.get(archetype, archetype),
        'results':         results,
        'total':           min(qualifying, RANKING_WINDOW_LIMIT),
        'count':           len(results),
        'is_truncated':    qualifying == RANKING_COUNT_SENTINEL,
        'source':          f'v3:{RANKING_VERSION}',
        'query_ms':        query_ms,
        'ranking': {
            'ranking_version': RANKING_VERSION,
            'ranking_sha256': ranking_sha256(),
            'derived_generation_id': str(generation_row['derived_generation_id']),
            'publication_sequence': generation_row['publication_sequence'],
            'archetype_version': generation_row['archetype_version'],
            'archetype_manifest_sha256': generation_row['archetype_manifest_sha256'],
            'archetype_model_sha256': generation_row['archetype_model_sha256'],
        },
    }


def best_rival(scores: Mapping[str, int], picked: str) -> tuple[str, int]:
    """Return the best other non-Flexible fit, breaking ties by key order."""
    rival = max(
        (key for key in ARCHETYPE_KEYS if key != picked and key != 'flexible'),
        key=scores.__getitem__,
    )
    return rival, scores[rival]


def _build_v3_ranking_row(row: asyncpg.Record, archetype: str) -> dict:
    """Shape one `build_ranked_query` row into an `ArchetypeRankingRow`.

    `build_ranked_query` selects `s.*` (the `v3_app.system_search` columns)
    plus the selected wide-row score, stored tier, confidence and weighted
    ordering key, all eight stored scores as `picked_<key>_score`, and the
    summary aliases
    (`summary_primary_archetype`/`summary_secondary_archetype`/
    `summary_best_colony_potential`/`summary_archetype_confidence`).

    Two distinct confidences are surfaced, never reconstructed by dividing
    `uncertainty_factor` back out by `completeness` (undefined at
    `completeness = 0`): `confidence` is the selected fit's evidence
    confidence (`ranking_confidence`), and `archetype_confidence` is the
    summary's classification-separation confidence for the system's own
    primary/secondary archetypes (`summary_archetype_confidence`).
    """
    x, y, z = row.get('x_ly'), row.get('y_ly'), row.get('z_ly')
    distance_to_sol = (
        round(math.sqrt(x * x + y * y + z * z), 2)
        if x is not None and y is not None and z is not None
        else None
    )

    completeness = row.get('completeness')
    # `confidence` (badge) = the selected fit's evidence confidence
    # (the wide row's `<key>_confidence_ppm / 1000000.0`, exposed as
    # ranking_confidence). `archetype_confidence` keeps its established
    # meaning: the summary's classification-separation confidence for the
    # system's own primary/secondary archetypes -- read from the summary alias,
    # not overwritten with the selected-fit value.
    ranking_confidence = row.get('ranking_confidence')
    selected_fit_confidence = (
        round(float(ranking_confidence), 4) if ranking_confidence is not None else None
    )
    summary_confidence = row.get('summary_archetype_confidence')
    archetype_confidence = (
        round(float(summary_confidence), 4) if summary_confidence is not None else None
    )

    score = float(row['primary_score']) if row['primary_score'] is not None else 0.0
    rival_archetype, rival_score = best_rival(
        {key: int(row[f'picked_{key}_score']) for key in ARCHETYPE_KEYS},
        archetype,
    )
    elw_count = row.get('elw_count')

    return {
        'id64':                row['system_id64'],
        'name':                row['name'],
        'coords':              {'x': x, 'y': y, 'z': z},
        'distance_to_sol':     distance_to_sol,
        'score':               score,
        'tier':                row['selected_archetype_tier'],
        # The system's ACTUAL primary/secondary archetype and overall best
        # potential, read from the summary (finding #20) -- not the requested
        # archetype. The requested one is exposed as `selected_archetype`.
        'selected_archetype':  archetype,
        'rival_archetype':     rival_archetype,
        'rival_score':         rival_score,
        'primary_archetype':   row.get('summary_primary_archetype'),
        'secondary_archetype': row.get('summary_secondary_archetype'),
        'archetype_confidence': archetype_confidence,
        'overall_development_potential': (
            float(row['summary_best_colony_potential'])
            if row.get('summary_best_colony_potential') is not None else None
        ),
        'buildability_score':  None,
        'build_complexity':    None,
        'purity_score':        None,
        'contamination_risk':  None,
        'confidence':          selected_fit_confidence,
        'has_elw':             bool(elw_count) if elw_count is not None else None,
        'elw_count':           elw_count,
        'landable_count':      row.get('landable_count'),
        'est_total_slots':     None,
        'tags':                [],
        'completeness':        completeness,
    }


def _indexed_score_floor(value: int) -> int:
    """Restrict the score floor after parsing the query string as an integer."""
    if value not in ACCEPTED_ARCHETYPE_SCORE_FLOORS:
        raise ValueError('Input should be 60, 76 or 88')
    return value


@router.get(
    '/rankings',
    response_model=ArchetypeRankingsResponse,
    summary='Ranked systems by colony archetype',
    description=(
        'Returns systems ranked by a specific colony archetype score, read '
        'generation-pinned from the published V3 `v3_app.system_archetype` '
        'projection via the ranking profile.'
    ),
)
@limiter.limit('60/minute')
async def get_archetype_rankings(
    request: Request,
    archetype:       str            = Query(..., description='Colony archetype key'),
    min_score: Annotated[
        int,
        AfterValidator(_indexed_score_floor),
        Query(json_schema_extra={'enum': list(ACCEPTED_ARCHETYPE_SCORE_FLOORS)}),
    ] = ARCHETYPE_SCORE_FLOOR_DEFAULT,
    galaxy_region:   Optional[int]  = Query(None, ge=0, le=2_147_483_647),
    max_distance_ly: Optional[float]= Query(None, ge=0),
    has_elw:         Optional[bool] = Query(None),
    min_slots:       Optional[int]  = Query(None, ge=0),
    max_contamination: Optional[float] = Query(None, ge=0, le=100),
    limit:           int            = Query(
        50, ge=RANKING_PAGE_LIMIT_MIN, le=RANKING_PAGE_LIMIT_MAX
    ),
    offset:          int            = Query(
        RANKING_OFFSET_MIN, ge=RANKING_OFFSET_MIN, le=RANKING_OFFSET_MAX
    ),
):
    if offset + limit > RANKING_OFFSET_PLUS_LIMIT_MAX:
        raise HTTPException(
            422,
            'offset + limit must be less than or equal to '
            f'{RANKING_OFFSET_PLUS_LIMIT_MAX}',
        )
    pool = get_pool()
    return await _archetype_rankings_v3(
        archetype=archetype,
        min_score=min_score,
        galaxy_region=galaxy_region,
        max_distance_ly=max_distance_ly,
        has_elw=has_elw,
        min_slots=min_slots,
        max_contamination=max_contamination,
        limit=limit,
        offset=offset,
        pool=pool,
    )


def _ranking_identity(
    generation_row: dict[str, Any],
) -> dict[str, Any]:
    return {
        'ranking_version': RANKING_VERSION,
        'ranking_sha256': ranking_sha256(),
        'derived_generation_id': str(generation_row['derived_generation_id']),
        'publication_sequence': generation_row['publication_sequence'],
        'archetype_version': generation_row['archetype_version'],
        'archetype_manifest_sha256': generation_row['archetype_manifest_sha256'],
        'archetype_model_sha256': generation_row['archetype_model_sha256'],
    }


def _raise_for_explanation_identity_mismatch(
    generation_row: dict[str, Any],
    *,
    expected_generation_id: uuid.UUID | None,
    expected_archetype_version: str | None,
    expected_manifest_sha256: str | None,
) -> None:
    mismatched_expectation = (
        expected_generation_id is not None
        and str(expected_generation_id) != str(generation_row['derived_generation_id'])
    ) or (
        expected_archetype_version is not None
        and expected_archetype_version != generation_row['archetype_version']
    ) or (
        expected_manifest_sha256 is not None
        and expected_manifest_sha256 != generation_row['archetype_manifest_sha256']
    )
    deployed_product_mismatch = (
        generation_row['archetype_version'] != ARCHETYPE_VERSION
        or generation_row.get('model_file_sha256')
        != _DEPLOYED_ARCHETYPE_MODEL_FILE_SHA256
    )
    if mismatched_expectation or deployed_product_mismatch:
        raise HTTPException(409, _EXPLANATION_UNAVAILABLE_DETAIL)


def _raise_for_recomputed_mismatch(
    *,
    id64: int,
    generation_id: Any,
    stored_row: asyncpg.Record,
    recomputed: dict[str, Any],
) -> None:
    for column in _PERSISTED_ARCHETYPE_COLUMNS:
        if stored_row[column] == recomputed[column]:
            continue
        log.error(
            'archetype explanation mismatch for generation=%s system=%s '
            'column=%s stored=%r recomputed=%r',
            generation_id,
            id64,
            column,
            stored_row[column],
            recomputed[column],
        )
        raise HTTPException(
            503,
            f'archetype explanation mismatch for column {column}',
        )


async def _system_archetype_explanation_v3(
    *,
    id64: int,
    expected_generation_id: uuid.UUID | None,
    expected_archetype_version: str | None,
    expected_manifest_sha256: str | None,
    pool: asyncpg.Pool,
) -> dict[str, Any]:
    try:
        async with pool.acquire() as conn:
            async with conn.transaction(isolation='repeatable_read', readonly=True):
                generation_row = await _current_derived_generation(conn)
                _raise_for_explanation_identity_mismatch(
                    generation_row,
                    expected_generation_id=expected_generation_id,
                    expected_archetype_version=expected_archetype_version,
                    expected_manifest_sha256=expected_manifest_sha256,
                )

                generation_id = generation_row['derived_generation_id']
                stored_row = await conn.fetchrow(
                    _SYSTEM_ARCHETYPE_READ_SQL,
                    generation_id,
                    None,
                    id64,
                    ARCHETYPE_VERSION,
                )
                if stored_row is None:
                    raise HTTPException(
                        404,
                        f'No published archetype row for system {id64}',
                    )
                if (
                    stored_row['archetype_version']
                    != generation_row['archetype_version']
                    or stored_row['archetype_version'] != ARCHETYPE_VERSION
                ):
                    raise HTTPException(409, _EXPLANATION_UNAVAILABLE_DETAIL)

                if stored_row['source_system_id64'] is None:
                    log.error(
                        'archetype explanation source vector missing for '
                        'generation=%s system=%s',
                        generation_id,
                        id64,
                    )
                    raise HTTPException(
                        503,
                        'archetype explanation source inputs are unavailable',
                    )

                vector_row = SystemVectorRow(
                    system_id64=int(stored_row['source_system_id64']),
                    potential=tuple(stored_row['potential']),
                    quality=tuple(stored_row['quality']),
                    quality_min=tuple(stored_row['quality_min']),
                    quality_max=tuple(stored_row['quality_max']),
                    completeness=tuple(stored_row['completeness']),
                    confidence=tuple(stored_row['confidence']),
                )
                opportunity_groups = tuple(zip(
                    stored_row['opportunity_ordinals'],
                    stored_row['opportunity_row_counts'],
                    stored_row['opportunity_local_score_sums'],
                    strict=True,
                ))
                recomputed = score_raw_system(vector_row, opportunity_groups)
                explanations = explain_raw_system(vector_row, opportunity_groups)
                _raise_for_recomputed_mismatch(
                    id64=id64,
                    generation_id=generation_id,
                    stored_row=stored_row,
                    recomputed=recomputed,
                )
    except HTTPException:
        raise
    except (
        asyncpg.exceptions.UndefinedTableError,
        asyncpg.exceptions.InvalidSchemaNameError,
    ) as exc:
        log.error(
            'system_archetype_explanation_v3 missing relation: %r (sqlstate=%s)',
            exc,
            getattr(exc, 'sqlstate', None),
        )
        raise HTTPException(503, 'Archetype explanations are unavailable') from exc
    except asyncpg.PostgresError as exc:
        log.error('archetypes.system.explanation DB error: %s', exc)
        raise HTTPException(503, 'Archetype explanations are unavailable') from exc

    return {
        'id64': id64,
        'archetypes': [
            {
                'key': key,
                'score': stored_row[f'{key}_score'],
                'tier': stored_row[f'{key}_tier'],
                'confidence': stored_row[f'{key}_confidence_ppm'] / 1_000_000.0,
                'weighted_e4': stored_row[f'{key}_weighted_e4'],
                'explanation': explanations[key],
            }
            for key in archetype_model.ARCHETYPE_KEYS
        ],
        'summary': {
            'primary_archetype': stored_row['primary_archetype'],
            'secondary_archetype': stored_row['secondary_archetype'],
            'best_colony_potential': stored_row['best_colony_potential'],
            'best_tier': stored_row['best_tier'],
            'archetype_confidence': (
                stored_row['archetype_confidence_ppm'] / 1_000_000.0
            ),
            'weighted_potential': stored_row['weighted_potential'],
        },
        'completeness': stored_row['completeness_ppm'] / 1_000_000.0,
        'ranking': _ranking_identity(generation_row),
    }


@router.get(
    '/system/{id64}/explanation',
    response_model=SystemArchetypeExplanationResponse,
    summary='On-demand V3 archetype explanations for a single system',
)
@limiter.limit('120/minute')
async def get_system_archetype_explanation(
    request: Request,
    id64: int = Path(ge=0, le=9_223_372_036_854_775_807),
    expected_generation_id: uuid.UUID | None = Query(None),
    expected_archetype_version: str | None = Query(
        None,
        min_length=1,
        max_length=128,
    ),
    expected_manifest_sha256: str | None = Query(
        None,
        pattern=r'^[0-9a-f]{64}$',
    ),
):
    return await _system_archetype_explanation_v3(
        id64=id64,
        expected_generation_id=expected_generation_id,
        expected_archetype_version=expected_archetype_version,
        expected_manifest_sha256=expected_manifest_sha256,
        pool=get_pool(),
    )


# ---------------------------------------------------------------------------
# Shared F3 slice-2 wide-row read (/rerank, /system/{id64}, /simulate)
# ---------------------------------------------------------------------------
# One generation-pinned read of `v3_app.system_search` joined to the published
# `v3_app.system_archetype` wide row for a bounded id64 list. The caller has
# already validated the request shape (422 before any DB access). The
# generation pin and the row read share one REPEATABLE READ snapshot so a
# governed publish mid-request cannot pair rows from one generation with the
# identity block of another.
async def _read_system_archetype_rows(
    pool: asyncpg.Pool,
    id64s: list[int],
    *,
    surface: str,
) -> tuple[dict[str, Any], list[asyncpg.Record]]:
    sql, params = build_system_archetype_detail_query(PROFILE_SPEC, id64s=id64s)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction(isolation='repeatable_read', readonly=True):
                generation_row = await _current_derived_generation(conn)
                rows = await conn.fetch(sql, *params)
    except HTTPException:
        raise
    except (asyncpg.exceptions.UndefinedTableError,
            asyncpg.exceptions.InvalidSchemaNameError) as exc:
        log.error('%s missing relation: %r (sqlstate=%s)',
                  surface, exc, getattr(exc, 'sqlstate', None))
        raise HTTPException(503, f'{surface} is unavailable') from exc
    except asyncpg.PostgresError as e:
        log.error('%s DB error: %s', surface, e)
        raise HTTPException(
            status_code=503,
            detail={'type': 'https://httpstatuses.com/503',
                    'title': 'Database error', 'status': 503, 'detail': str(e)},
        )
    return generation_row, list(rows)


def _distance_to_sol(row: asyncpg.Record) -> float:
    x, y, z = float(row['x_ly']), float(row['y_ly']), float(row['z_ly'])
    return round(math.sqrt(x * x + y * y + z * z), 2)


# ---------------------------------------------------------------------------
# GET /api/archetypes/profiles — static preset rerank blends
# ---------------------------------------------------------------------------
# One single-archetype preset per V3 key plus the generalist equal blend.
# Derived from `ARCHETYPE_KEYS`, never hand-tuned: a preset is a documented
# weight vector, not product tuning.
_PROFILES: list[dict[str, Any]] = [
    {
        'id':          key,
        'label':       _V3_ARCHETYPE_LABELS[key],
        'description': f'{_V3_ARCHETYPE_LABELS[key]} fit only',
        'archetype':   key,
        'weights':     single_archetype_weights(key),
    }
    for key in ARCHETYPE_KEYS
] + [
    {
        'id':          'generalist',
        'label':       'Generalist',
        'description': 'Equal weight on every V3 archetype fit',
        'archetype':   None,
        'weights':     equal_weights(),
    },
]
_PROFILE_BY_ID: dict[str, dict[str, Any]] = {profile['id']: profile for profile in _PROFILES}


@router.get(
    '/profiles',
    response_model=ArchetypesProfilesResponse,
    summary='Preset rerank weight profiles (V3 archetype keys)',
)
@limiter.limit('120/minute')
async def get_profiles(request: Request):
    return {'profiles': _PROFILES}


# ---------------------------------------------------------------------------
# POST /api/archetypes/rerank (F3 slice 2 — V3, generation-pinned)
# ---------------------------------------------------------------------------

def _resolve_rerank_weights(
    body: ArchetypeRerankRequest,
) -> tuple[ArchetypeRerankWeights, Optional[str]]:
    """Resolve the effective blend before any database access.

    Order: `profile` (unknown id -> 422, never a silent fallback), explicit
    `weights`, a single-archetype blend when only `archetype` is given, else
    the generalist equal blend.
    """
    if body.profile is not None:
        preset = _PROFILE_BY_ID.get(body.profile)
        if preset is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown profile {body.profile!r}. "
                    f"Valid values: {sorted(_PROFILE_BY_ID)}"
                ),
            )
        return ArchetypeRerankWeights(**preset['weights']), preset['id']
    if body.weights is not None:
        return body.weights, None
    if body.archetype is not None:
        return ArchetypeRerankWeights(**single_archetype_weights(body.archetype)), None
    return ArchetypeRerankWeights(**equal_weights()), None


async def _archetype_rerank_v3(*, body: ArchetypeRerankRequest, pool: asyncpg.Pool) -> dict:
    t0 = time.monotonic()
    weights, profile_applied = _resolve_rerank_weights(body)
    try:
        id64s = validate_id64s(body.id64s)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    generation_row, rows = await _read_system_archetype_rows(
        pool, id64s, surface='Archetype rerank',
    )
    results = rerank_rows(
        rows,
        weights=weights.model_dump(),
        picked_archetype=body.archetype,
    )
    found = {result['id64'] for result in results}
    query_ms = int((time.monotonic() - t0) * 1000)
    return {
        'archetype':       body.archetype,
        'profile_applied': profile_applied,
        'weights_applied': weights.model_dump(),
        'results':         results,
        'missing_id64s':   [id64 for id64 in id64s if id64 not in found],
        'query_ms':        query_ms,
        'ranking':         _ranking_identity(generation_row),
    }


@router.post(
    '/rerank',
    response_model=ArchetypeRerankResponse,
    summary='Rerank systems by a weighted blend of their V3 archetype fits',
    description=(
        'Reorders the supplied systems by a client-weighted blend of their '
        'stored, uncertainty-modified V3 archetype keys, read generation-pinned '
        'from the published `v3_app.system_archetype` projection. Runs no new '
        'search and persists nothing.'
    ),
)
@limiter.limit('30/minute')
async def post_archetype_rerank(request: Request, body: ArchetypeRerankRequest):
    return await _archetype_rerank_v3(body=body, pool=get_pool())


# ---------------------------------------------------------------------------
# GET /api/archetypes/system/{id64} (F3 slice 2 — V3, generation-pinned)
# ---------------------------------------------------------------------------

def _build_system_archetype_detail(
    row: asyncpg.Record,
    generation_row: dict[str, Any],
    query_ms: int,
) -> dict[str, Any]:
    archetypes_out = {
        key: {**archetype_entry(row, key), 'label': _V3_ARCHETYPE_LABELS[key]}
        for key in ARCHETYPE_KEYS
    }
    return {
        'id64':             row['system_id64'],
        'name':             row['name'],
        'coords':           {'x': row['x_ly'], 'y': row['y_ly'], 'z': row['z_ly']},
        'distance_to_sol':  _distance_to_sol(row),
        'main_star_class':  row['main_star_class'],
        'galaxy_region_id': row['galaxy_region_id'],
        'galaxy_region':    row['region_name'],
        'archetypes':       archetypes_out,
        'primary_archetype':    row['primary_archetype'],
        'secondary_archetype':  row['secondary_archetype'],
        'archetype_confidence': round(int(row['archetype_confidence_ppm']) / PPM, 6),
        'overall_development_potential': int(row['best_colony_potential']),
        'best_tier':          row['best_tier'],
        'weighted_potential': float(row['weighted_potential']),
        'confidence':   float(row['confidence']),
        'completeness': float(row['completeness']),
        'facts': {
            'body_count':          row['body_count'],
            'landable_count':      row['landable_count'],
            'station_count':       row['station_count'],
            'has_rings':           row['has_rings'],
            'has_biologicals':     row['has_biologicals'],
            'has_geologicals':     row['has_geologicals'],
            'has_terraformable':   row['has_terraformable'],
            'elw_count':           row.get('elw_count'),
            'ww_count':            row.get('ww_count'),
            'terraformable_count': row.get('terraformable_count'),
            'gas_giant_count':     row.get('gas_giant_count'),
            'ring_count':          row.get('ring_count'),
            'bio_signal_total':    row.get('bio_signal_total'),
            'geo_signal_total':    row.get('geo_signal_total'),
        },
        'query_ms': query_ms,
        'ranking':  _ranking_identity(generation_row),
    }


async def _system_archetype_v3(*, id64: int, pool: asyncpg.Pool) -> dict[str, Any]:
    t0 = time.monotonic()
    generation_row, rows = await _read_system_archetype_rows(
        pool, [id64], surface='Archetype detail',
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f'No published archetype row for system {id64}',
        )
    query_ms = int((time.monotonic() - t0) * 1000)
    return _build_system_archetype_detail(rows[0], generation_row, query_ms)


@router.get(
    '/system/{id64}',
    response_model=SystemArchetypeResponse,
    summary='Full V3 archetype breakdown for a single system',
    description=(
        'Reads the published `v3_app.system_archetype` wide row and the '
        '`v3_app.system_search` facts for one system, generation-pinned. '
        'Explanations are served on demand by `/system/{id64}/explanation`.'
    ),
)
@limiter.limit('120/minute')
async def get_system_archetypes(
    request: Request,
    id64: int = Path(ge=0, le=9_223_372_036_854_775_807),
):
    return await _system_archetype_v3(id64=id64, pool=get_pool())


# ---------------------------------------------------------------------------
# POST /api/archetypes/simulate (F3 slice 2 — V3, generation-pinned)
# ---------------------------------------------------------------------------

_SIMULATE_DISCLAIMER = (
    'simulation_score is the published V3 archetype fit for the planned '
    'archetype. It does not model construction order or facility placement, '
    'and no V3 projection carries contamination, purity, buildability or '
    'slot-topology estimates.'
)
_LOW_COMPLETENESS_THRESHOLD = 0.5


def _simulation_recommendations(
    row: asyncpg.Record,
    *,
    planned: str,
    planned_fit: dict[str, Any],
    planned_facilities: list,
) -> list[str]:
    recs: list[str] = []
    primary = row['primary_archetype']
    if primary != planned:
        primary_fit = archetype_entry(row, primary)
        recs.append(
            f'The published V3 model rates this system strongest as '
            f'{_V3_ARCHETYPE_LABELS.get(primary, primary)} '
            f'({primary_fit["score"]}, tier {primary_fit["tier"]}); the planned '
            f'{_V3_ARCHETYPE_LABELS[planned]} fit scores {planned_fit["score"]} '
            f'(tier {planned_fit["tier"]}).'
        )
    completeness = float(row['completeness'])
    if completeness < _LOW_COMPLETENESS_THRESHOLD:
        recs.append(
            f'Body data is {completeness:.0%} complete and the planned fit\'s '
            f'confidence is {planned_fit["confidence"]:.0%}. Scan more bodies '
            'before committing to this plan.'
        )
    t3_count = sum(1 for facility in planned_facilities if facility.tier == 3)
    if t3_count > 0:
        recs.append(
            f'{t3_count} T3 facilities planned. '
            'T3 scaling requires at least one strong-link anchor body.'
        )
    return recs or ['No V3 model warnings for this plan.']


async def _build_simulate_v3(*, body: BuildSimulateRequest, pool: asyncpg.Pool) -> dict[str, Any]:
    generation_row, rows = await _read_system_archetype_rows(
        pool, [body.id64], surface='Build simulation',
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f'No published archetype row for system {body.id64}',
        )
    row = rows[0]
    planned = body.planned_archetype
    planned_fit = archetype_entry(row, planned)
    return {
        'id64':                  body.id64,
        'planned_archetype':     planned,
        'simulation_score':      planned_fit['score'],
        'tier':                  planned_fit['tier'],
        'confidence':            planned_fit['confidence'],
        'completeness':          float(row['completeness']),
        'primary_archetype':     row['primary_archetype'],
        'secondary_archetype':   row['secondary_archetype'],
        'best_colony_potential': int(row['best_colony_potential']),
        'recommendations':       _simulation_recommendations(
            row,
            planned=planned,
            planned_fit=planned_fit,
            planned_facilities=body.planned_facilities,
        ),
        'disclaimer':            _SIMULATE_DISCLAIMER,
        'ranking':               _ranking_identity(generation_row),
    }


@router.post(
    '/simulate',
    response_model=BuildSimulateResponse,
    summary='Build simulation — score a planned archetype against the V3 fit',
    description=(
        'Scores a planned build against the system\'s published V3 fit for the '
        'planned archetype, generation-pinned. Best-effort guidance only: '
        'economy outcomes depend on construction order and undocumented '
        'Frontier weighting, and no V3 projection carries contamination, purity '
        'or buildability estimates.'
    ),
)
@limiter.limit('20/minute')
async def post_simulate(request: Request, body: BuildSimulateRequest):
    return await _build_simulate_v3(body=body, pool=get_pool())
