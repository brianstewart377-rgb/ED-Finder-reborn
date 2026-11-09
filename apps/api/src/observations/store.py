from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import asyncpg

from .api_models import ObservedFactCreateRequest, ObservedFactUpdateRequest
from .models import ObservationFactSummary, PersistedObservedFact, summarise_observed_facts

_JSON_DEFAULT_TAGS = '[]'
_JSON_DEFAULT_METADATA = '{}'


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(',', ':'), sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _dt_to_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def row_to_observed_fact(row: Any) -> PersistedObservedFact:
    return PersistedObservedFact(
        observation_id=str(row['observation_id']),
        system_id64=int(row['system_id64']),
        created_at=_dt_to_str(row['created_at']) or '',
        updated_at=_dt_to_str(row['updated_at']),
        source=str(row['source']),
        fact_type=str(row['fact_type']),
        subject_type=str(row['subject_type']),
        subject_id=row['subject_id'],
        status=str(row['status']),
        observed_value=_json_loads(row['observed_value_json'], None),
        expected_value=_json_loads(row['expected_value_json'], None),
        confidence=str(row['confidence']),
        notes=row['notes'],
        build_fingerprint=row['build_fingerprint'],
        simulation_fingerprint=row['simulation_fingerprint'],
        target_archetype=row['target_archetype'],
        facility_template_id=row['facility_template_id'],
        local_body_id=row['local_body_id'],
        service_id=row['service_id'],
        economy=row['economy'],
        tags=list(_json_loads(row['tags_json'], [])),
        metadata=dict(_json_loads(row['metadata_json'], {})),
    )


async def create_observed_fact(
    pool: asyncpg.Pool,
    request: ObservedFactCreateRequest,
) -> PersistedObservedFact:
    observation_id = f'obs_{uuid4().hex}'
    payload = request.model_dump(mode='json')
    # The ``observed_facts`` table existed before Stage 6A with legacy
    # comparison columns (Stage 4D shape):
    #   area                = fact_type
    #   source_type         = source
    #   observed_value      = observed_value_json
    #   facility_id         = facility_template_id
    #   body_id             = local_body_id
    # Stage 6A writes both the new CRUD contract columns and the legacy
    # aliases so older Stage 4D comparison/trace code can keep reading the
    # same table until a later migration removes or fully normalises those
    # columns. The duplicated parameter positions ($3/$4/$8/$15/$16 below)
    # are intentional and mirror this mapping.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            '''
            INSERT INTO observed_facts (
                observation_id, system_id64, area, source, source_type, fact_type,
                subject_type, subject_id, status, observed_value, observed_value_json,
                expected_value_json, confidence, notes, build_fingerprint,
                simulation_fingerprint, target_archetype, facility_template_id,
                facility_id, local_body_id, body_id, service_id, economy, tags_json,
                metadata_json, created_at
            ) VALUES (
                $1, $2, $3, $4, $4, $3,
                $5, $6, $7, $8::jsonb, $8::jsonb,
                $9::jsonb, $10, $11, $12,
                $13, $14, $15,
                $15, $16, $16, $17, $18, $19::jsonb,
                $20::jsonb, now()
            )
            RETURNING *
            ''',
            observation_id,
            payload['system_id64'],
            payload['fact_type'],
            payload['source'],
            payload['subject_type'],
            payload.get('subject_id'),
            payload['status'],
            _json_dumps(payload.get('observed_value')),
            _json_dumps(payload.get('expected_value')),
            payload['confidence'],
            payload.get('notes'),
            payload.get('build_fingerprint'),
            payload.get('simulation_fingerprint'),
            payload.get('target_archetype'),
            payload.get('facility_template_id'),
            payload.get('local_body_id'),
            payload.get('service_id'),
            payload.get('economy'),
            _json_dumps(payload.get('tags') or []),
            _json_dumps(payload.get('metadata') or {}),
        )
    return row_to_observed_fact(row)


async def list_observed_facts(
    pool: asyncpg.Pool,
    *,
    system_id64: int,
    fact_type: str | None = None,
    subject_type: str | None = None,
    status: str | None = None,
    target_archetype: str | None = None,
    build_fingerprint: str | None = None,
    simulation_fingerprint: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[PersistedObservedFact], int]:
    where, args_for_count = _build_filter_clause(
        system_id64=system_id64,
        fact_type=fact_type,
        subject_type=subject_type,
        status=status,
        target_archetype=target_archetype,
        build_fingerprint=build_fingerprint,
        simulation_fingerprint=simulation_fingerprint,
    )
    args = list(args_for_count)
    args.extend([limit, offset])
    limit_pos = len(args) - 1
    offset_pos = len(args)

    async with pool.acquire() as conn:
        total = await conn.fetchval(f'SELECT count(*) FROM observed_facts WHERE {where}', *args_for_count)
        rows = await conn.fetch(
            f'''
            SELECT * FROM observed_facts
            WHERE {where}
            ORDER BY created_at DESC, observation_id DESC
            LIMIT ${limit_pos} OFFSET ${offset_pos}
            ''',
            *args,
        )
    return [row_to_observed_fact(row) for row in rows], int(total or 0)


def _build_filter_clause(
    *,
    system_id64: int,
    fact_type: str | None,
    subject_type: str | None,
    status: str | None,
    target_archetype: str | None,
    build_fingerprint: str | None,
    simulation_fingerprint: str | None,
) -> tuple[str, list[Any]]:
    """Build the shared WHERE clause used by both list and summary queries.

    Returning ``(where_sql, args)`` keeps list_observed_facts and
    summarise_observed_facts_for_filter consistent so the paginated list
    and the full-filter summary always describe the same result set.
    """
    conditions = ['system_id64 = $1']
    args: list[Any] = [system_id64]
    filters = {
        'fact_type': fact_type,
        'subject_type': subject_type,
        'status': status,
        'target_archetype': target_archetype,
        'build_fingerprint': build_fingerprint,
        'simulation_fingerprint': simulation_fingerprint,
    }
    for column, value in filters.items():
        if value is None:
            continue
        args.append(value)
        conditions.append(f'{column} = ${len(args)}')
    return ' AND '.join(conditions), args


async def summarise_observed_facts_for_filter(
    pool: asyncpg.Pool,
    *,
    system_id64: int,
    fact_type: str | None = None,
    subject_type: str | None = None,
    status: str | None = None,
    target_archetype: str | None = None,
    build_fingerprint: str | None = None,
    simulation_fingerprint: str | None = None,
) -> 'ObservationFactSummary':
    """Summarise ALL observed facts matching the given filters.

    Stage 6A's list endpoint returns ``summary`` over the full filtered
    result set (not just the paginated page) so callers can show
    accurate by_fact_type / by_status / by_confidence / total_count
    breakdowns alongside the paginated facts. The query intentionally
    ignores limit/offset; Stage 6A data volume per system is small
    enough that a SELECT-all-then-aggregate is acceptable. If volumes
    grow, this function is the single place to replace with DB-side
    GROUP BY aggregations without changing the router contract.
    """
    where, args = _build_filter_clause(
        system_id64=system_id64,
        fact_type=fact_type,
        subject_type=subject_type,
        status=status,
        target_archetype=target_archetype,
        build_fingerprint=build_fingerprint,
        simulation_fingerprint=simulation_fingerprint,
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f'SELECT * FROM observed_facts WHERE {where} ORDER BY created_at DESC, observation_id DESC',
            *args,
        )
    facts = [row_to_observed_fact(row) for row in rows]
    return summarise_observed_facts(facts)


async def get_observed_fact(pool: asyncpg.Pool, observation_id: str) -> PersistedObservedFact | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM observed_facts WHERE observation_id = $1', observation_id)
    return row_to_observed_fact(row) if row else None


async def update_observed_fact(
    pool: asyncpg.Pool,
    observation_id: str,
    request: ObservedFactUpdateRequest,
) -> PersistedObservedFact | None:
    existing = await get_observed_fact(pool, observation_id)
    if existing is None:
        return None

    current = existing.to_dict()
    updates = request.model_dump(mode='json', exclude_unset=True)
    current.update(updates)
    current['updated_at'] = datetime.now(timezone.utc).isoformat()

    # See create_observed_fact() for the legacy-column compatibility mapping
    # (area=fact_type, source_type=source, observed_value=observed_value_json,
    # facility_id=facility_template_id, body_id=local_body_id). The UPDATE
    # below keeps both the new and legacy columns in sync so any Stage 4D
    # reader still observes a consistent row.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            '''
            UPDATE observed_facts
            SET source = $2,
                source_type = $2,
                fact_type = $3,
                area = $3,
                subject_type = $4,
                subject_id = $5,
                status = $6,
                observed_value_json = $7::jsonb,
                observed_value = $7::jsonb,
                expected_value_json = $8::jsonb,
                confidence = $9,
                notes = $10,
                build_fingerprint = $11,
                simulation_fingerprint = $12,
                target_archetype = $13,
                facility_template_id = $14,
                facility_id = $14,
                local_body_id = $15,
                body_id = $15,
                service_id = $16,
                economy = $17,
                tags_json = $18::jsonb,
                metadata_json = $19::jsonb,
                updated_at = now()
            WHERE observation_id = $1
            RETURNING *
            ''',
            observation_id,
            current['source'],
            current['fact_type'],
            current['subject_type'],
            current.get('subject_id'),
            current['status'],
            _json_dumps(current.get('observed_value')),
            _json_dumps(current.get('expected_value')),
            current['confidence'],
            current.get('notes'),
            current.get('build_fingerprint'),
            current.get('simulation_fingerprint'),
            current.get('target_archetype'),
            current.get('facility_template_id'),
            current.get('local_body_id'),
            current.get('service_id'),
            current.get('economy'),
            _json_dumps(current.get('tags') or []),
            _json_dumps(current.get('metadata') or {}),
        )
    return row_to_observed_fact(row) if row else None


async def delete_observed_fact(pool: asyncpg.Pool, observation_id: str) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute('DELETE FROM observed_facts WHERE observation_id = $1', observation_id)
    return result.endswith(' 1')


async def observed_fact_summary(pool: asyncpg.Pool, system_id64: int) -> dict[str, Any]:
    facts, _ = await list_observed_facts(pool, system_id64=system_id64, limit=1000, offset=0)
    return summarise_observed_facts(facts).to_dict()


# ──────────────────────────────────────────────────────────────────────
# Stage 6C — comparison-specific fact loader
# ──────────────────────────────────────────────────────────────────────
async def list_observed_facts_for_comparison(
    pool: asyncpg.Pool,
    *,
    system_id64: int,
    target_archetype: str | None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[PersistedObservedFact], int]:
    """Load observed facts for a Stage 6C compare-endpoint Mode A call.

    Filtering semantics (deliberately different from
    ``list_observed_facts``):

    * If ``target_archetype`` is ``None`` — load all facts for the
      system (same shape as the Stage 6A list endpoint with no target
      filter).
    * If ``target_archetype`` is supplied — include facts whose
      ``target_archetype`` matches the requested value **or** is
      ``NULL``. System-level evidence (notes, service evidence, economy
      evidence, CP observations) frequently has ``target_archetype IS
      NULL`` and remains relevant regardless of the planner's currently
      selected archetype. We do NOT want a per-target compare call to
      hide that general evidence.

    This helper exists specifically for Stage 6C; ``list_observed_facts``
    still honours the strict ``target_archetype = $X`` filter so the
    Stage 6A list endpoint contract is unchanged.
    """
    if target_archetype is None:
        return await list_observed_facts(
            pool,
            system_id64=system_id64,
            limit=limit,
            offset=offset,
        )

    args: list[Any] = [system_id64, target_archetype]
    where = 'system_id64 = $1 AND (target_archetype = $2 OR target_archetype IS NULL)'
    args_for_count = list(args)
    args.extend([limit, offset])
    limit_pos = len(args) - 1
    offset_pos = len(args)

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f'SELECT count(*) FROM observed_facts WHERE {where}',
            *args_for_count,
        )
        rows = await conn.fetch(
            f'''
            SELECT * FROM observed_facts
            WHERE {where}
            ORDER BY created_at DESC, observation_id DESC
            LIMIT ${limit_pos} OFFSET ${offset_pos}
            ''',
            *args,
        )
    return [row_to_observed_fact(row) for row in rows], int(total or 0)
