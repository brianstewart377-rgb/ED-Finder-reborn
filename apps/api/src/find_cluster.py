"""Find: the cluster search behind `/api/search/cluster`.

It validates the request and then fails closed until a Finder cluster
product is published and designed for (see the comment below). Moved here
from local_search.py on 15 November 2026.
"""
from __future__ import annotations

import math
from typing import Any

import asyncpg
from fastapi import HTTPException

from edfinder_api.find_generation import _current_derived_generation
from edfinder_api.local_search import _int_or_422, log
from edfinder_api.search_economies import canonical_economy_key


# ---------------------------------------------------------------------------
# V3 cluster search (F3 slice 2b) — POST /api/search/cluster ONLY.
# ---------------------------------------------------------------------------
# The legacy cluster search read the V2 `cluster_summary` macro-grid table
# (per-anchor counts of economy-viable systems inside a 500 LY bubble) plus
# `systems`/`ratings` for slot detail. Its V3 successor is the independent
# cluster product of migration 004 (`v3_spatial.cluster_run` ->
# `v3_spatial.cluster` -> `v3_spatial.cluster_member`; decision doc §8):
# clusters are domain-derived, versioned, generation-scoped outputs that a
# builder publishes per owner domain and algorithm version.
#
# Flagged data gap (not silently degraded): no Finder cluster builder exists
# in the tree, no `cluster_run` row has ever been published, and the
# per-economy viability summary the legacy `ClusterResult` fields need is a
# builder-defined `cluster.summary` contract that has not been designed. The
# V3 route therefore fails closed: it validates the request, pins the
# published generation behind the same Finder product gate as every other
# Finder route, reads the Finder-owned cluster-run state for that generation
# from `v3_spatial.cluster_run`, and answers 503 naming exactly what is
# missing. It never reads a legacy relation and never fabricates clusters
# from a request-time galaxy scan (decision doc §12: no request-time global
# calculation; clusters are not the search index).
FINDER_CLUSTER_OWNER_DOMAIN = 'finder'
CLUSTER_SEARCH_LIMIT_DEFAULT = 50
CLUSTER_SEARCH_LIMIT_MAX = 200
CLUSTER_SEARCH_MAX_REQUIREMENTS = 6
CLUSTER_SEARCH_MAX_SLOTS = 10
CLUSTER_SEARCH_MAX_SLOT_ECONOMIES = 2
CLUSTER_SEARCH_MIN_SCORE_DEFAULT = 40
CLUSTER_SLOT_MIN_SCORE_DEFAULT = 65
GALAXY_REGION_ID_MIN = 1
GALAXY_REGION_ID_MAX = 42


def _canonical_economy_or_422(value: Any, field: str) -> str:
    key = canonical_economy_key(value) if isinstance(value, str) else None
    if key is None:
        raise HTTPException(422, f'{field}: unknown economy {value!r}')
    return key


def _score_or_422(value: Any, field: str, *, default: int) -> int:
    score = _int_or_422(value, field, default=default)
    if score < 0 or score > 100:
        raise HTTPException(422, f'{field} must be between 0 and 100')
    return score


def prepare_cluster_search_v3(body: dict) -> dict[str, Any]:
    """Validate a cluster-search request without touching the database.

    Returns the normalised request (canonical economy keys, bounded window,
    finite reference coordinates or None, bounded region id). Every
    rejection is a 422 so an unavailable cluster product (503) can never
    mask a malformed request.
    """
    requirements = body.get('requirements') or []
    slots = body.get('slots') or []
    if bool(requirements) == bool(slots):
        raise HTTPException(422, 'provide exactly one of requirements or slots')
    if len(requirements) > CLUSTER_SEARCH_MAX_REQUIREMENTS:
        raise HTTPException(
            422, f'at most {CLUSTER_SEARCH_MAX_REQUIREMENTS} economy requirements',
        )
    if len(slots) > CLUSTER_SEARCH_MAX_SLOTS:
        raise HTTPException(422, f'at most {CLUSTER_SEARCH_MAX_SLOTS} slots')

    normalised_requirements: list[dict[str, Any]] = []
    for index, requirement in enumerate(requirements):
        if not isinstance(requirement, dict):
            raise HTTPException(422, f'requirements[{index}] must be an object')
        min_count = _int_or_422(
            requirement.get('min_count'), f'requirements[{index}].min_count', default=1,
        )
        if min_count < 1:
            raise HTTPException(
                422, f'requirements[{index}].min_count must be greater than or equal to 1',
            )
        normalised_requirements.append({
            'economy': _canonical_economy_or_422(
                requirement.get('economy'), f'requirements[{index}].economy',
            ),
            'min_count': min_count,
            'min_score': _score_or_422(
                requirement.get('min_score'), f'requirements[{index}].min_score',
                default=CLUSTER_SEARCH_MIN_SCORE_DEFAULT,
            ),
        })

    normalised_slots: list[dict[str, Any]] = []
    for index, slot in enumerate(slots):
        if not isinstance(slot, dict):
            raise HTTPException(422, f'slots[{index}] must be an object')
        economies = slot.get('economies') or []
        if not isinstance(economies, list) or not economies:
            raise HTTPException(422, f'slots[{index}].economies must name at least one economy')
        if len(economies) > CLUSTER_SEARCH_MAX_SLOT_ECONOMIES:
            raise HTTPException(
                422,
                f'slots[{index}].economies: at most {CLUSTER_SEARCH_MAX_SLOT_ECONOMIES} '
                'economies per slot (same-world constraint)',
            )
        normalised_slots.append({
            'economies': [
                _canonical_economy_or_422(economy, f'slots[{index}].economies')
                for economy in economies
            ],
            'min_score': _score_or_422(
                slot.get('min_score'), f'slots[{index}].min_score',
                default=CLUSTER_SLOT_MIN_SCORE_DEFAULT,
            ),
            'label': slot.get('label'),
        })

    limit = _int_or_422(body.get('limit'), 'limit', default=CLUSTER_SEARCH_LIMIT_DEFAULT)
    if limit < 1 or limit > CLUSTER_SEARCH_LIMIT_MAX:
        raise HTTPException(422, f'limit must be between 1 and {CLUSTER_SEARCH_LIMIT_MAX}')
    offset = _int_or_422(body.get('offset'), 'offset', default=0)
    if offset < 0:
        raise HTTPException(422, 'offset must be greater than or equal to 0')

    reference_coords: tuple[float, float, float] | None = None
    ref = body.get('reference_coords') or {}
    if not isinstance(ref, dict):
        raise HTTPException(422, 'reference_coords must be an object')
    present = [axis for axis in ('x', 'y', 'z') if ref.get(axis) is not None]
    if present and len(present) != 3:
        raise HTTPException(422, 'reference_coords must include x, y, z')
    if present:
        try:
            coords = tuple(float(ref[axis]) for axis in ('x', 'y', 'z'))
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, 'reference_coords must be numeric') from exc
        if not all(math.isfinite(value) for value in coords):
            raise HTTPException(422, 'reference_coords must be finite numbers')
        reference_coords = coords  # type: ignore[assignment]

    galaxy_region_id: int | None = None
    if body.get('galaxy_region_id') is not None:
        galaxy_region_id = _int_or_422(body.get('galaxy_region_id'), 'galaxy_region_id')
        if not GALAXY_REGION_ID_MIN <= galaxy_region_id <= GALAXY_REGION_ID_MAX:
            raise HTTPException(
                422,
                f'galaxy_region_id must be between {GALAXY_REGION_ID_MIN} '
                f'and {GALAXY_REGION_ID_MAX}',
            )

    return {
        'requirements': normalised_requirements,
        'slots': normalised_slots,
        'limit': limit,
        'offset': offset,
        'reference_coords': reference_coords,
        'galaxy_region_id': galaxy_region_id,
    }


async def _finder_cluster_runs(
    connection: asyncpg.Connection, derived_generation_id: Any,
) -> list[asyncpg.Record]:
    """Read the Finder-owned cluster runs attached to the pinned generation."""
    return await connection.fetch(
        '''
        SELECT cluster_run_id, algorithm, algorithm_version, lifecycle_state, published_at
        FROM v3_spatial.cluster_run
        WHERE derived_generation_id = $1
          AND owner_domain = $2
        ORDER BY created_at DESC, cluster_run_id
        ''',
        derived_generation_id,
        FINDER_CLUSTER_OWNER_DOMAIN,
    )


async def local_db_cluster_search_v3(body: dict, pool: asyncpg.Pool) -> dict:
    """V3 cluster search for `POST /api/search/cluster` — fail-closed.

    Validates the request (422), pins the published generation behind the
    Finder product gate (404 / 503, `_current_derived_generation`), then
    reads the Finder-owned cluster-run state for that generation from
    `v3_spatial.cluster_run` inside the same REPEATABLE READ snapshot and
    answers 503 naming what is missing. See the section comment above for
    the flagged data gap: until a Finder cluster builder publishes a run
    and defines the `cluster.summary` viability contract, no V3 read can
    honestly produce the `ClusterResult` fields, so this never returns 200
    and never falls back to a legacy relation.
    """
    prepare_cluster_search_v3(body)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction(isolation='repeatable_read', readonly=True):
                generation_row = await _current_derived_generation(conn)
                runs = await _finder_cluster_runs(conn, generation_row['derived_generation_id'])
    except HTTPException:
        raise
    except (asyncpg.exceptions.UndefinedTableError,
            asyncpg.exceptions.InvalidSchemaNameError) as exc:
        log.error('local_db_cluster_search_v3 missing relation: %r (sqlstate=%s)',
                  exc, getattr(exc, 'sqlstate', None))
        raise HTTPException(503, 'Finder cluster search is unavailable') from exc

    published = [run for run in runs if run['lifecycle_state'] == 'PUBLISHED']
    generation_id = generation_row['derived_generation_id']
    if not published:
        state = ', '.join(
            f"{run['algorithm']}@{run['algorithm_version']}={run['lifecycle_state']}"
            for run in runs
        ) or 'none'
        raise HTTPException(
            503,
            'Finder cluster product is not published for the current generation '
            f'{generation_id} (v3_spatial.cluster_run owner_domain='
            f'{FINDER_CLUSTER_OWNER_DOMAIN}: {state}); the Finder cluster builder '
            'and its cluster summary contract are a pending V3 derived product.',
        )
    newest = published[0]
    raise HTTPException(
        503,
        f"Finder cluster run {newest['algorithm']}@{newest['algorithm_version']} "
        f'({newest["cluster_run_id"]}) is published for generation {generation_id}, '
        'but no Finder cluster read contract exists for it yet; cluster search '
        'stays unavailable until that contract is designed and implemented.',
    )
