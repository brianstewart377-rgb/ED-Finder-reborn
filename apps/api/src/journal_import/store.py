from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

import asyncpg

from edfinder_api.evidence_store.store import promote_canonical_evidence_for_systems
from edfinder_api.ingest.journal_normaliser import event_type_to_normaliser
from edfinder_api.journal_import.api_models import (
    JournalImportFileRef,
    JournalImportReceipt,
    JournalImportRequest,
    JournalPromotionReceipt,
    JournalPromotionSummary,
    JournalImportSummary,
    JournalTelemetryRecentRun,
    JournalTelemetryRecentSystem,
    JournalTelemetrySummaryResponse,
)
from edfinder_api.routes.store import consume_journal_observations
from edfinder_api.ring_facts import ring_rows_for_body
from edfinder_api.source_precedence import BODY_SCAN_FACT_FIELDS, merge_body_scan_fact
from shared_contracts.body_ring_association_status import BODY_RING_ASSOCIATION_STATUS_CASE_SQL

MAX_DAILY_ROWS_PER_SYNC_KEY = 200_000
DIRTY_MARK_BATCH_SIZE = 500
DIRTY_MARK_STATEMENT_TIMEOUT_MS = 5000

BODY_RING_UPSERT_SQL = f"""
    INSERT INTO body_rings (
        system_id64, body_id, source_body_id, body_name,
        ring_name, ring_type, ring_class,
        mass_mt, inner_radius, outer_radius,
        source, confidence, association_status, updated_at
    ) VALUES (
        $1, $2, $3, $4,
        $5, $6, $7,
        $8, $9, $10,
        $11, $12, {BODY_RING_ASSOCIATION_STATUS_CASE_SQL}, now()
    )
    ON CONFLICT (system_id64, body_id, ring_name, source) DO UPDATE SET
        source_body_id = COALESCE(EXCLUDED.source_body_id, body_rings.source_body_id),
        body_name = COALESCE(EXCLUDED.body_name, body_rings.body_name),
        ring_type = COALESCE(EXCLUDED.ring_type, body_rings.ring_type),
        ring_class = COALESCE(EXCLUDED.ring_class, body_rings.ring_class),
        mass_mt = COALESCE(EXCLUDED.mass_mt, body_rings.mass_mt),
        inner_radius = COALESCE(EXCLUDED.inner_radius, body_rings.inner_radius),
        outer_radius = COALESCE(EXCLUDED.outer_radius, body_rings.outer_radius),
        confidence = EXCLUDED.confidence,
        association_status = {BODY_RING_ASSOCIATION_STATUS_CASE_SQL},
        updated_at = now()
    WHERE body_rings.source_body_id IS DISTINCT FROM COALESCE(EXCLUDED.source_body_id, body_rings.source_body_id)
       OR body_rings.body_name IS DISTINCT FROM COALESCE(EXCLUDED.body_name, body_rings.body_name)
       OR body_rings.ring_type IS DISTINCT FROM COALESCE(EXCLUDED.ring_type, body_rings.ring_type)
       OR body_rings.ring_class IS DISTINCT FROM COALESCE(EXCLUDED.ring_class, body_rings.ring_class)
       OR body_rings.mass_mt IS DISTINCT FROM COALESCE(EXCLUDED.mass_mt, body_rings.mass_mt)
       OR body_rings.inner_radius IS DISTINCT FROM COALESCE(EXCLUDED.inner_radius, body_rings.inner_radius)
       OR body_rings.outer_radius IS DISTINCT FROM COALESCE(EXCLUDED.outer_radius, body_rings.outer_radius)
       OR body_rings.confidence IS DISTINCT FROM EXCLUDED.confidence
       OR body_rings.association_status IS DISTINCT FROM {BODY_RING_ASSOCIATION_STATUS_CASE_SQL}
"""


class JournalImportRateLimitError(RuntimeError):
    """Raised when a bounded journal-import safety limit is exceeded."""


def _json_object(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {}
        decoded = json.loads(stripped)
        if isinstance(decoded, dict):
            return dict(decoded)
        return {}
    return dict(value)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _git_sha() -> str:
    raw = os.getenv('GIT_COMMIT_SHA') or os.getenv('RAILWAY_GIT_COMMIT_SHA') or 'unknown'
    raw = raw.strip()
    return raw[:40] if raw else 'unknown'


def _command_row_count(status: str | None) -> int:
    if not status:
        return 0
    try:
        return int(str(status).rsplit(' ', 1)[-1])
    except (TypeError, ValueError):
        return 0


def _chunks(values: list[int], size: int):
    size = max(1, int(size))
    for start in range(0, len(values), size):
        yield values[start:start + size]


async def _daily_rows_for_sync_key(conn: asyncpg.Connection, sync_key: str) -> int:
    rows = await conn.fetchval(
        '''
        SELECT COALESCE(SUM(rows_read), 0)
        FROM source_runs
        WHERE source_name = 'frontier_journal'
          AND started_at >= (NOW() - INTERVAL '1 day')
          AND COALESCE(metadata->>'sync_key', safety_boundary->>'sync_key', '') = $1
        ''',
        sync_key,
    )
    return int(rows or 0)


def _build_file_refs(files: list[dict[str, object]]) -> list[JournalImportFileRef]:
    refs: list[JournalImportFileRef] = []
    for item in files:
        refs.append(
            JournalImportFileRef(
                name=str(item.get('name') or ''),
                event_count=int(item.get('event_count') or 0),
            ),
        )
    return refs


async def import_journal_batch(
    pool: asyncpg.Pool,
    request: JournalImportRequest,
) -> JournalImportReceipt:
    run_key = f'jrnl-{_utc_now().strftime("%Y%m%d%H%M%S")}-{uuid4().hex[:8]}'
    started_at = _utc_now()
    started_perf = perf_counter()
    event_counts = Counter(observation.event_type for observation in request.observations)
    rows_read = len(request.observations)
    rows_staged = 0
    rows_skipped = 0
    inserted_observations = []

    async with pool.acquire() as conn:
        async with conn.transaction():
            daily_rows_before = await _daily_rows_for_sync_key(conn, request.sync_key)
            if daily_rows_before + rows_read > MAX_DAILY_ROWS_PER_SYNC_KEY:
                raise JournalImportRateLimitError(
                    f'Journal import row budget exceeded for this sync key: '
                    f'{daily_rows_before + rows_read:,} rows in the last 24h '
                    f'(limit {MAX_DAILY_ROWS_PER_SYNC_KEY:,}).'
                )

            await conn.execute(
                '''
                INSERT INTO source_runs (
                    source_run_key,
                    source_name,
                    source_category,
                    domain,
                    import_scope,
                    status,
                    source_manifest_sha256,
                    started_at,
                    git_commit_sha,
                    importer_name,
                    importer_version,
                    trigger_context,
                    rows_read,
                    rows_staged,
                    rows_rejected,
                    rows_skipped,
                    safety_boundary,
                    metadata
                ) VALUES (
                    $1, 'frontier_journal', 'source_of_evidence', 'systems', 'staging_only',
                    'running', NULL, $2, $3, 'frontend_journal_import', 'a1', 'user_upload',
                    $4, 0, 0, 0, $5::jsonb, $6::jsonb
                )
                ''',
                run_key,
                started_at,
                _git_sha(),
                rows_read,
                {
                    'privacy_strip_before_network': True,
                    'canonical_write_path_opened': False,
                    'allowlisted_client_parse': True,
                    'sync_key': request.sync_key,
                    'evidence_mode': 'staging_only',
                    'daily_sync_key_row_limit': MAX_DAILY_ROWS_PER_SYNC_KEY,
                    'daily_sync_key_rows_before_run': daily_rows_before,
                },
                {
                    'parser_version': request.client_manifest.parser_version,
                    'sync_key': request.sync_key,
                    'files': [item.model_dump(mode='json') for item in request.client_manifest.files],
                    'event_counts': dict(event_counts),
                },
            )

            for observation in request.observations:
                inserted = await conn.fetchrow(
                    '''
                    INSERT INTO journal_import_staging (
                        source_run_key,
                        sync_key,
                        source_file_name,
                        source_offset,
                        source_record_hash,
                        event_type,
                        system_id64,
                        system_name,
                        subject_type,
                        subject_id,
                        observed_at,
                        summary,
                        payload_json,
                        privacy_boundary
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::timestamptz, $12, $13::jsonb, $14::jsonb
                    )
                    ON CONFLICT (sync_key, source_record_hash) DO NOTHING
                    RETURNING source_record_hash
                    ''',
                    run_key,
                    request.sync_key,
                    observation.source_file,
                    observation.source_offset,
                    observation.observation_key,
                    observation.event_type,
                    int(observation.system_id64) if observation.system_id64 is not None else None,
                    observation.system_name,
                    observation.subject_type,
                    observation.subject_id,
                    observation.observed_at,
                    observation.summary,
                    observation.payload,
                    observation.privacy_boundary,
                )
                if inserted is None:
                    rows_skipped += 1
                    continue

                rows_staged += 1
                inserted_observations.append(observation)

            if inserted_observations:
                await consume_journal_observations(conn, request.sync_key, inserted_observations)

            finished_at = _utc_now()
            duration_ms = int((perf_counter() - started_perf) * 1000)
            await conn.execute(
                '''
                UPDATE source_runs
                SET
                    status = 'succeeded',
                    finished_at = $2::timestamptz,
                    duration_ms = $3,
                    rows_read = $4,
                    rows_staged = $5,
                    rows_skipped = $6,
                    updated_at = NOW(),
                    metadata = metadata || $7::jsonb
                WHERE source_run_key = $1
                ''',
                run_key,
                finished_at,
                duration_ms,
                rows_read,
                rows_staged,
                rows_skipped,
                {
                    'summary': {
                        'observations_received': rows_read,
                        'observations_staged': rows_staged,
                        'duplicates_skipped': rows_skipped,
                        'conflicts_flagged': 0,
                        'files_seen': len(request.client_manifest.files),
                        'event_counts': dict(event_counts),
                        'evidence_mode': 'staging_only',
                        'daily_sync_key_rows_before_run': daily_rows_before,
                        'daily_sync_key_rows_after_run': daily_rows_before + rows_read,
                    },
                },
            )

    return JournalImportReceipt(
        run_key=run_key,
        status='succeeded',
        parser_version=request.client_manifest.parser_version,
        started_at=_dt_to_str(started_at),
        finished_at=_dt_to_str(finished_at),
        files=request.client_manifest.files,
        summary=JournalImportSummary(
            observations_received=rows_read,
            observations_staged=rows_staged,
            duplicates_skipped=rows_skipped,
            conflicts_flagged=0,
            files_seen=len(request.client_manifest.files),
            event_counts=dict(event_counts),
        ),
    )


async def get_journal_import_receipt(
    pool: asyncpg.Pool,
    run_key: str,
) -> JournalImportReceipt | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            '''
            SELECT
                source_run_key,
                status,
                started_at,
                finished_at,
                metadata,
                rows_read,
                rows_staged,
                rows_skipped
            FROM source_runs
            WHERE source_run_key = $1
              AND source_name = 'frontier_journal'
            ''',
            run_key,
        )
    if row is None:
        return None

    metadata = _json_object(row['metadata'])
    summary = dict(metadata.get('summary') or {})
    return JournalImportReceipt(
        run_key=str(row['source_run_key']),
        status=str(row['status']),
        parser_version=str(metadata.get('parser_version') or 'unknown'),
        started_at=_dt_to_str(row['started_at']),
        finished_at=_dt_to_str(row['finished_at']),
        files=_build_file_refs(list(metadata.get('files') or [])),
        summary=JournalImportSummary(
            observations_received=int(summary.get('observations_received') or row['rows_read'] or 0),
            observations_staged=int(summary.get('observations_staged') or row['rows_staged'] or 0),
            duplicates_skipped=int(summary.get('duplicates_skipped') or row['rows_skipped'] or 0),
            conflicts_flagged=int(summary.get('conflicts_flagged') or 0),
            files_seen=int(summary.get('files_seen') or len(metadata.get('files') or [])),
            event_counts=dict(summary.get('event_counts') or {}),
        ),
    )


async def get_journal_telemetry_summary(
    pool: asyncpg.Pool,
    sync_key: str,
    *,
    recent_run_limit: int = 5,
    recent_system_limit: int = 8,
) -> JournalTelemetrySummaryResponse:
    async with pool.acquire() as conn:
        aggregate_row = await conn.fetchrow(
            '''
            WITH scoped_runs AS (
                SELECT source_run_key,
                       started_at,
                       finished_at,
                       rows_staged,
                       rows_skipped
                FROM source_runs
                WHERE source_name = 'frontier_journal'
                  AND COALESCE(metadata->>'sync_key', safety_boundary->>'sync_key', '') = $1
            ),
            scoped_stage AS (
                SELECT stage.*
                FROM journal_import_staging stage
                JOIN scoped_runs runs
                  ON runs.source_run_key = stage.source_run_key
            )
            SELECT
                (SELECT COUNT(*)::int FROM scoped_runs) AS runs_count,
                (SELECT MAX(finished_at)::text FROM scoped_runs) AS last_imported_at,
                (SELECT COALESCE(SUM(rows_staged), 0)::int FROM scoped_runs) AS observations_staged,
                (SELECT COALESCE(SUM(rows_skipped), 0)::int FROM scoped_runs) AS duplicates_skipped,
                (SELECT COUNT(DISTINCT system_id64)::int FROM scoped_stage) AS systems_observed,
                (
                    SELECT COUNT(*)::int
                    FROM scoped_stage
                    WHERE event_type IN ('Scan', 'FSSBodySignals', 'SAASignalsFound', 'FSSDiscoveryScan', 'FSSAllBodiesFound')
                ) AS body_observation_count,
                (
                    SELECT COUNT(*)::int
                    FROM scoped_stage
                    WHERE event_type = 'Docked'
                ) AS docked_observation_count
            ''',
            sync_key,
        )
        event_rows = await conn.fetch(
            '''
            SELECT stage.event_type, COUNT(*)::int AS event_count
            FROM journal_import_staging stage
            JOIN source_runs runs
              ON runs.source_run_key = stage.source_run_key
            WHERE runs.source_name = 'frontier_journal'
              AND COALESCE(runs.metadata->>'sync_key', runs.safety_boundary->>'sync_key', '') = $1
            GROUP BY stage.event_type
            ORDER BY COUNT(*) DESC, stage.event_type ASC
            ''',
            sync_key,
        )
        run_rows = await conn.fetch(
            '''
            SELECT source_run_key,
                   status,
                   started_at,
                   finished_at,
                   rows_staged,
                   rows_skipped,
                   metadata
            FROM source_runs
            WHERE source_name = 'frontier_journal'
              AND COALESCE(metadata->>'sync_key', safety_boundary->>'sync_key', '') = $1
            ORDER BY started_at DESC
            LIMIT $2
            ''',
            sync_key,
            recent_run_limit,
        )
        system_rows = await conn.fetch(
            '''
            WITH ranked AS (
                SELECT
                    stage.system_id64,
                    COALESCE(
                        NULLIF(MAX(stage.system_name) FILTER (WHERE stage.system_name IS NOT NULL), ''),
                        'System ' || stage.system_id64::text
                    ) AS system_name,
                    MAX(stage.observed_at) AS last_observed_at,
                    COUNT(*)::int AS event_count,
                    ARRAY(
                        SELECT DISTINCT event_type
                        FROM journal_import_staging stage2
                        JOIN source_runs runs2
                          ON runs2.source_run_key = stage2.source_run_key
                        WHERE runs2.source_name = 'frontier_journal'
                          AND COALESCE(runs2.metadata->>'sync_key', runs2.safety_boundary->>'sync_key', '') = $1
                          AND stage2.system_id64 = stage.system_id64
                        ORDER BY event_type
                    ) AS event_types
                FROM journal_import_staging stage
                JOIN source_runs runs
                  ON runs.source_run_key = stage.source_run_key
                WHERE runs.source_name = 'frontier_journal'
                  AND COALESCE(runs.metadata->>'sync_key', runs.safety_boundary->>'sync_key', '') = $1
                  AND stage.system_id64 IS NOT NULL
                GROUP BY stage.system_id64
            )
            SELECT *
            FROM ranked
            ORDER BY last_observed_at DESC NULLS LAST, event_count DESC, system_id64 DESC
            LIMIT $2
            ''',
            sync_key,
            recent_system_limit,
        )

    aggregate = dict(aggregate_row or {})
    event_counts = {str(row['event_type']): int(row['event_count'] or 0) for row in event_rows}
    recent_runs: list[JournalTelemetryRecentRun] = []
    for row in run_rows:
        metadata = _json_object(row['metadata'])
        summary = dict(metadata.get('summary') or {})
        recent_runs.append(
            JournalTelemetryRecentRun(
                run_key=str(row['source_run_key']),
                status=str(row['status']),
                started_at=_dt_to_str(row['started_at']),
                finished_at=_dt_to_str(row['finished_at']),
                observations_staged=int(summary.get('observations_staged') or row['rows_staged'] or 0),
                duplicates_skipped=int(summary.get('duplicates_skipped') or row['rows_skipped'] or 0),
                event_counts=dict(summary.get('event_counts') or {}),
            )
        )
    recent_systems = [
        JournalTelemetryRecentSystem(
            system_id64=str(row['system_id64']),
            system_name=str(row['system_name']),
            last_observed_at=_dt_to_str(row['last_observed_at']),
            event_count=int(row['event_count'] or 0),
            event_types=[str(event_type) for event_type in list(row['event_types'] or [])],
        )
        for row in system_rows
    ]
    return JournalTelemetrySummaryResponse(
        sync_key=sync_key,
        runs_count=int(aggregate.get('runs_count') or 0),
        last_imported_at=_dt_to_str(aggregate.get('last_imported_at')),
        observations_staged=int(aggregate.get('observations_staged') or 0),
        duplicates_skipped=int(aggregate.get('duplicates_skipped') or 0),
        systems_observed=int(aggregate.get('systems_observed') or 0),
        body_observation_count=int(aggregate.get('body_observation_count') or 0),
        docked_observation_count=int(aggregate.get('docked_observation_count') or 0),
        event_counts=event_counts,
        recent_runs=recent_runs,
        recent_systems=recent_systems,
    )


def _journal_fact_source(event_type: str) -> str:
    mapping = {
        'Scan': 'frontier_journal_scan',
        'FSSBodySignals': 'frontier_journal_fssbodysignals',
        'SAASignalsFound': 'frontier_journal_saasignals',
    }
    return mapping.get(event_type, 'frontier_journal')


def _payload_from_stage_row(row: asyncpg.Record) -> dict[str, object]:
    payload = _json_object(row['payload_json'])
    if row['system_id64'] is not None:
        payload.setdefault('SystemAddress', int(row['system_id64']))
    if row['system_name'] and not payload.get('StarSystem'):
        payload['StarSystem'] = str(row['system_name'])
    if str(row['subject_type']) == 'body':
        subject_id = row['subject_id']
        if subject_id:
            try:
                payload.setdefault('BodyID', int(subject_id))
            except (TypeError, ValueError):
                payload.setdefault('BodyName', str(subject_id))
    return payload


def _build_promotion_fact_rows(stage_rows: list[asyncpg.Record]) -> tuple[list[dict], dict[str, int], int]:
    fact_rows: list[dict] = []
    event_counts: Counter[str] = Counter()
    skipped_rows = 0

    for row in stage_rows:
        event_type = str(row['event_type'])
        normaliser = event_type_to_normaliser(event_type)
        if normaliser is None:
            skipped_rows += 1
            continue
        fact = normaliser(_payload_from_stage_row(row))
        if fact is None:
            skipped_rows += 1
            continue
        fact['data_sources'] = [_journal_fact_source(event_type)]
        fact_rows.append(fact)
        event_counts[event_type] += 1

    return fact_rows, dict(event_counts), skipped_rows


def _ring_rows_from_scan_facts(fact_rows: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for fact in fact_rows:
        source_body_id = int(fact['body_id']) if fact.get('body_id') is not None else None
        source_rows, _explicit_no_rings = ring_rows_for_body(
            {'rings': fact.get('rings') or []},
            system_id64=int(fact['system_address']),
            body_id=None,
            body_name=fact.get('body_name'),
            source=str((fact.get('data_sources') or ['frontier_journal_scan'])[0]),
            source_body_id=source_body_id,
            trusted_empty_means_no_rings=False,
        )
        rows.extend(source_rows)
    return rows


async def _merge_journal_facts_with_canonical(
    conn: asyncpg.Connection,
    fact_rows: list[dict],
) -> tuple[list[dict], dict[str, int]]:
    if not fact_rows:
        return [], {}

    system_ids = sorted({int(row['system_address']) for row in fact_rows if row.get('system_address') is not None})
    body_ids = sorted({int(row['body_id']) for row in fact_rows if row.get('body_id') is not None})
    if not system_ids or not body_ids:
        return fact_rows, {}

    existing_rows = await conn.fetch(
        """
        SELECT
            system_address, body_id, body_name,
            radius, mass_em, gravity,
            surface_temp, surface_pressure,
            planet_class, terraform_state,
            atmosphere, volcanism,
            semi_major_axis, orbital_period, parents,
            has_geo, has_bio,
            geo_signal_count, bio_signal_count,
            is_landable, is_terraformable, is_ringed,
            data_sources, confidence
          FROM body_scan_facts
         WHERE system_address = ANY($1::bigint[])
           AND body_id = ANY($2::integer[])
        """,
        system_ids,
        body_ids,
    )
    existing_by_key = {
        (int(row['system_address']), int(row['body_id'])): {field: row[field] for field in BODY_SCAN_FACT_FIELDS}
        for row in existing_rows
    }

    merged_rows: list[dict] = []
    resolution_counts: Counter[str] = Counter()
    for row in fact_rows:
        key = (int(row['system_address']), int(row['body_id']))
        decision = merge_body_scan_fact(existing_by_key.get(key), row)
        merged_rows.append(decision.row)
        resolution_counts[decision.resolution] += 1
    return merged_rows, dict(resolution_counts)


def _is_probable_belt_source(*, source_body_id: int | None, body_name: str | None, ring_name: str | None) -> bool:
    if source_body_id == 0:
        return True
    haystack = ' '.join(part for part in (body_name, ring_name) if part).lower()
    return ' belt' in haystack or haystack.endswith('belt')


def _resolve_ring_rows_with_local_bodies(
    rows: list[dict],
    local_bodies,
) -> tuple[list[dict], list[dict]]:
    local_by_name: dict[tuple[int, str], list[dict]] = {}
    for body in local_bodies:
        system_id64 = body.get('system_id64') if isinstance(body, dict) else body['system_id64']
        body_name = body.get('name') if isinstance(body, dict) else body['name']
        body_id = body.get('id') if isinstance(body, dict) else body['id']
        if system_id64 is None or not body_name or body_id is None:
            continue
        local_by_name.setdefault((int(system_id64), str(body_name)), []).append({
            'system_id64': int(system_id64),
            'id': int(body_id),
            'name': str(body_name),
        })

    resolved: list[dict] = []
    skipped: list[dict] = []
    for row in rows:
        body_name = row.get('body_name')
        if _is_probable_belt_source(
            source_body_id=row.get('source_body_id'),
            body_name=body_name,
            ring_name=row.get('ring_name'),
        ):
            skipped.append({**row, 'reason': 'belt_source_evidence'})
            continue
        if not row.get('ring_name'):
            skipped.append({**row, 'reason': 'missing_ring_identity'})
            continue
        if row.get('system_id64') is None or not body_name:
            skipped.append({**row, 'reason': 'missing_system_or_body_name'})
            continue
        matches = local_by_name.get((int(row['system_id64']), str(body_name)), [])
        if len(matches) != 1:
            skipped.append({
                **row,
                'reason': 'local_body_not_found_by_name' if not matches else 'local_body_name_not_unique',
            })
            continue
        match = matches[0]
        resolved.append({
            **row,
            'body_id': match['id'],
            'body_name': match['name'],
            'association_status': 'local_matched',
        })
    return resolved, skipped


async def _resolve_journal_ring_rows(
    conn: asyncpg.Connection,
    rows: list[dict],
) -> tuple[list[dict], list[dict]]:
    if not rows:
        return [], []

    system_ids = sorted({int(row['system_id64']) for row in rows if row.get('system_id64') is not None})
    body_names = sorted({str(row['body_name']) for row in rows if row.get('body_name')})
    if not system_ids or not body_names:
        return _resolve_ring_rows_with_local_bodies(rows, [])

    local_bodies = await conn.fetch(
        """
        SELECT system_id64, id, name
          FROM bodies
         WHERE system_id64 = ANY($1::bigint[])
           AND name = ANY($2::text[])
        """,
        system_ids,
        body_names,
    )
    return _resolve_ring_rows_with_local_bodies(rows, local_bodies)


def _ring_row_tuple(row: dict) -> tuple:
    return (
        row.get('system_id64'),
        row.get('body_id'),
        row.get('source_body_id'),
        row.get('body_name'),
        row.get('ring_name'),
        row.get('ring_type'),
        row.get('ring_class'),
        row.get('mass_mt'),
        row.get('inner_radius'),
        row.get('outer_radius'),
        row.get('source'),
        row.get('confidence'),
    )


async def _mark_dirty_systems_incremental(
    conn: asyncpg.Connection,
    system_ids: set[int],
    *,
    batch_size: int = DIRTY_MARK_BATCH_SIZE,
) -> dict[str, int | list[int]]:
    ordered_ids = sorted({int(value) for value in system_ids if value is not None})
    result: dict[str, int | list[int]] = {
        'requested': len(ordered_ids),
        'marked': 0,
        'already_dirty': 0,
        'skipped_missing': 0,
        'failed': 0,
        'failed_ids': [],
    }
    for chunk in _chunks(ordered_ids, batch_size):
        try:
            await conn.execute(
                f"SET LOCAL statement_timeout = {max(1, int(DIRTY_MARK_STATEMENT_TIMEOUT_MS))}"
            )
            row = await conn.fetchrow(
                """
                WITH requested AS (
                    SELECT unnest($1::bigint[]) AS id64
                ),
                existing AS (
                    SELECT s.id64, s.rating_dirty
                      FROM systems s
                      JOIN requested r ON r.id64 = s.id64
                ),
                updated AS (
                    UPDATE systems s
                       SET rating_dirty = TRUE,
                           updated_at = NOW()
                      FROM requested r
                     WHERE s.id64 = r.id64
                       AND s.rating_dirty IS DISTINCT FROM TRUE
                    RETURNING s.id64
                )
                SELECT
                    (SELECT COUNT(*)::int FROM updated) AS marked,
                    (SELECT COUNT(*)::int FROM existing WHERE rating_dirty IS TRUE) AS already_dirty,
                    (
                        (SELECT COUNT(*)::int FROM requested)
                        - (SELECT COUNT(*)::int FROM existing)
                    ) AS skipped_missing
                """,
                chunk,
            )
            result['marked'] = int(result['marked']) + int(row['marked'] or 0)
            result['already_dirty'] = int(result['already_dirty']) + int(row['already_dirty'] or 0)
            result['skipped_missing'] = int(result['skipped_missing']) + int(row['skipped_missing'] or 0)
        except Exception:
            failed_ids = result['failed_ids']
            assert isinstance(failed_ids, list)
            failed_ids.extend(chunk)
            result['failed'] = int(result['failed']) + len(chunk)
    return result


async def promote_journal_batch(
    pool: asyncpg.Pool,
    run_key: str,
) -> JournalPromotionReceipt | None:
    promoted_at = _utc_now()
    started_perf = perf_counter()

    async with pool.acquire() as conn:
        async with conn.transaction():
            source_run = await conn.fetchrow(
                """
                SELECT source_run_key
                  FROM source_runs
                 WHERE source_run_key = $1
                   AND source_name = 'frontier_journal'
                """,
                run_key,
            )
            if source_run is None:
                return None

            stage_rows = await conn.fetch(
                """
                SELECT event_type, system_id64, system_name, subject_type, subject_id, payload_json
                  FROM journal_import_staging
                 WHERE source_run_key = $1
                 ORDER BY created_at ASC, id ASC
                """,
                run_key,
            )

            fact_rows, event_counts, skipped_rows = _build_promotion_fact_rows(stage_rows)
            source_ring_rows = _ring_rows_from_scan_facts(fact_rows)
            ring_rows, unresolved_ring_rows = await _resolve_journal_ring_rows(conn, source_ring_rows)

            if fact_rows:
                fact_rows, resolution_counts = await _merge_journal_facts_with_canonical(conn, fact_rows)
                await conn.executemany(
                    """
                    INSERT INTO body_scan_facts (
                        system_address, body_id, body_name,
                        radius, mass_em, gravity,
                        surface_temp, surface_pressure,
                        planet_class, terraform_state,
                        atmosphere, volcanism,
                        semi_major_axis, orbital_period, parents,
                        has_geo, has_bio,
                        geo_signal_count, bio_signal_count,
                        is_landable, is_terraformable, is_ringed,
                        data_sources, confidence, updated_at
                    ) VALUES (
                        $1, $2, $3,
                        $4, $5, $6,
                        $7, $8,
                        $9, $10,
                        $11, $12,
                        $13, $14, $15,
                        $16, $17,
                        $18, $19,
                        $20, $21, $22,
                        $23, $24, now()
                    )
                    ON CONFLICT (system_address, body_id) DO UPDATE SET
                        body_name        = COALESCE(EXCLUDED.body_name, body_scan_facts.body_name),
                        radius           = COALESCE(EXCLUDED.radius, body_scan_facts.radius),
                        mass_em          = COALESCE(EXCLUDED.mass_em, body_scan_facts.mass_em),
                        gravity          = COALESCE(EXCLUDED.gravity, body_scan_facts.gravity),
                        surface_temp     = COALESCE(EXCLUDED.surface_temp, body_scan_facts.surface_temp),
                        surface_pressure = COALESCE(EXCLUDED.surface_pressure, body_scan_facts.surface_pressure),
                        planet_class     = COALESCE(EXCLUDED.planet_class, body_scan_facts.planet_class),
                        terraform_state  = COALESCE(EXCLUDED.terraform_state, body_scan_facts.terraform_state),
                        atmosphere       = COALESCE(EXCLUDED.atmosphere, body_scan_facts.atmosphere),
                        volcanism        = COALESCE(EXCLUDED.volcanism, body_scan_facts.volcanism),
                        semi_major_axis  = COALESCE(EXCLUDED.semi_major_axis, body_scan_facts.semi_major_axis),
                        orbital_period   = COALESCE(EXCLUDED.orbital_period, body_scan_facts.orbital_period),
                        parents          = COALESCE(EXCLUDED.parents, body_scan_facts.parents),
                        has_geo          = GREATEST(EXCLUDED.has_geo, body_scan_facts.has_geo),
                        has_bio          = GREATEST(EXCLUDED.has_bio, body_scan_facts.has_bio),
                        geo_signal_count = GREATEST(EXCLUDED.geo_signal_count, body_scan_facts.geo_signal_count),
                        bio_signal_count = GREATEST(EXCLUDED.bio_signal_count, body_scan_facts.bio_signal_count),
                        is_landable      = COALESCE(EXCLUDED.is_landable, body_scan_facts.is_landable),
                        is_terraformable = COALESCE(EXCLUDED.is_terraformable, body_scan_facts.is_terraformable),
                        is_ringed        = CASE
                            WHEN body_scan_facts.is_ringed IS TRUE OR EXCLUDED.is_ringed IS TRUE THEN TRUE
                            WHEN EXCLUDED.is_ringed IS FALSE THEN FALSE
                            ELSE body_scan_facts.is_ringed
                        END,
                        confidence       = GREATEST(EXCLUDED.confidence, body_scan_facts.confidence),
                        data_sources     = (
                            SELECT ARRAY(
                                SELECT DISTINCT unnest(
                                    body_scan_facts.data_sources || EXCLUDED.data_sources
                                )
                            )
                        ),
                        updated_at       = now()
                    """,
                    [
                        (
                            row['system_address'],
                            row['body_id'],
                            row.get('body_name'),
                            row.get('radius'),
                            row.get('mass_em'),
                            row.get('gravity'),
                            row.get('surface_temp'),
                            row.get('surface_pressure'),
                            row.get('planet_class'),
                            row.get('terraform_state'),
                            row.get('atmosphere'),
                            row.get('volcanism'),
                            row.get('semi_major_axis'),
                            row.get('orbital_period'),
                            row.get('parents'),
                            row.get('has_geo', False),
                            row.get('has_bio', False),
                            row.get('geo_signal_count', 0),
                            row.get('bio_signal_count', 0),
                            row.get('is_landable', False),
                            row.get('is_terraformable', False),
                            row.get('is_ringed'),
                            row.get('data_sources', []),
                            row.get('confidence', 0.4),
                        )
                        for row in fact_rows
                    ],
                )
            else:
                resolution_counts = {}

            dirty_system_ids = {
                int(row['system_address'])
                for row in fact_rows
                if row.get('system_address') is not None
            }

            for row in ring_rows:
                status = await conn.execute(
                    BODY_RING_UPSERT_SQL,
                    *_ring_row_tuple(row),
                )
                if _command_row_count(status):
                    dirty_system_ids.add(int(row['system_id64']))

            dirty_result = await _mark_dirty_systems_incremental(conn, dirty_system_ids) if dirty_system_ids else {
                'marked': 0,
            }
            evidence_promotion = await promote_canonical_evidence_for_systems(
                conn,
                system_ids=dirty_system_ids,
                evidence_types=['body_completeness', 'ring_composition'],
                source_run_key=run_key,
                trigger_context='journal_promotion',
            ) if dirty_system_ids else {
                'records': [],
                'warnings': [],
                'created_count': 0,
                'deduped_count': 0,
            }

            duration_ms = int((perf_counter() - started_perf) * 1000)
            summary = {
                'staged_rows_seen': len(stage_rows),
                'eligible_rows': len(fact_rows),
                'skipped_rows': skipped_rows,
                'facts_promoted': len(fact_rows),
                'ring_rows_promoted': len(ring_rows),
                'ring_rows_unresolved': len(unresolved_ring_rows),
                'dirty_systems_marked': int(dirty_result.get('marked') or 0),
                'canonical_evidence_promoted': int(evidence_promotion.get('created_count') or 0),
                'canonical_evidence_deduped': int(evidence_promotion.get('deduped_count') or 0),
                'event_counts': event_counts,
                'resolution_counts': resolution_counts,
            }

            await conn.execute(
                """
                UPDATE source_runs
                   SET updated_at = NOW(),
                       metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb
                 WHERE source_run_key = $1
                """,
                run_key,
                {
                    'journal_promotion': {
                        'scope': 'simulation_facts',
                        'promoted_at': _dt_to_str(promoted_at),
                        'duration_ms': duration_ms,
                        'summary': summary,
                        'canonical_evidence_warnings': list(evidence_promotion.get('warnings') or []),
                    },
                },
            )

    return JournalPromotionReceipt(
        run_key=run_key,
        status='succeeded',
        promoted_at=_dt_to_str(promoted_at),
        duration_ms=duration_ms,
        summary=JournalPromotionSummary.model_validate(summary),
    )
