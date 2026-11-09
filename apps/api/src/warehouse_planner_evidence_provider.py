from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import asyncpg

from edfinder_api.observations.store import observed_fact_summary
from edfinder_api.warehouse_planner_evidence_models import (
    WarehousePlannerEvidenceBoundedStaging,
    WarehousePlannerEvidenceCoverage,
    WarehousePlannerEvidenceCoverageFreshness,
    WarehousePlannerEvidenceCoverageMetric,
    WarehousePlannerEvidenceItem,
)


def _resolve_authority_path() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        path = candidate / 'docs' / 'colonisation-redesign' / 'stage-19-state-authority.json'
        if path.is_file():
            return path
    return here.parent / 'docs' / 'colonisation-redesign' / 'stage-19-state-authority.json'


AUTHORITY_PATH = _resolve_authority_path()


@dataclass(frozen=True)
class LivePlannerEvidenceResult:
    availability: str
    envelope_status: str
    items: list[WarehousePlannerEvidenceItem]
    freshness_status: str
    evaluated_at: str | None
    manual_review_required: bool
    bounded_staging: WarehousePlannerEvidenceBoundedStaging
    coverage: WarehousePlannerEvidenceCoverage = field(
        default_factory=lambda: _unknown_coverage(
            'Selected-system coverage has not been evaluated in this runtime yet.',
        ),
    )
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Stage19bbRunMetadata:
    row_limit: int
    source_run_key: str
    bridge_key: str


@dataclass(frozen=True)
class Stage19bbCloseoutMetadata:
    source_name: str
    source_batch_label: str
    source_sha256: str
    runs: tuple[Stage19bbRunMetadata, ...]


async def load_live_planner_evidence(pool: asyncpg.Pool, id64: int) -> LivePlannerEvidenceResult:
    """Load selected-system evidence; every database failure is classified.

    The pool acquire, system lookup, count/probe queries and the bounded
    staging query are direct calls; a timeout, lost connection or other
    database error on any of them is the same outage the bounded mapping
    exists to report, so it is raised as `PlannerEvidenceQueryError` rather
    than escaping as an unclassified 500. `_safe_fetchval` keeps its own,
    narrower "missing schema means None" handling inside.
    """
    try:
        return await _load_live_planner_evidence(pool, id64)
    except PlannerEvidenceQueryError:
        raise
    except (asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError) as exc:
        raise PlannerEvidenceQueryError('canonical evidence', exc) from exc


async def _load_live_planner_evidence(pool: asyncpg.Pool, id64: int) -> LivePlannerEvidenceResult:
    warnings: list[str] = []

    async with pool.acquire() as conn:
        system = await conn.fetchrow(
            'SELECT id64, name FROM systems WHERE id64 = $1',
            id64,
        )
        if not system:
            return LivePlannerEvidenceResult(
                availability='unavailable',
                envelope_status='unknown',
                items=[],
                freshness_status='unknown',
                evaluated_at=None,
                manual_review_required=True,
                bounded_staging=_default_bounded_staging(status='unavailable'),
                coverage=_unknown_coverage(
                    'System is not present in canonical app data, so selected-system coverage remains unavailable.',
                ),
                warnings=[
                    'System is not present in canonical app data; selected-system evidence remains unavailable.',
                ],
            )

        body_count = await conn.fetchval(
            'SELECT COUNT(*)::int FROM bodies WHERE system_id64 = $1',
            id64,
        ) or 0
        station_count = await conn.fetchval(
            'SELECT COUNT(*)::int FROM stations WHERE system_id64 = $1',
            id64,
        ) or 0
        scan_fact_count = await conn.fetchval(
            'SELECT COUNT(*)::int FROM body_scan_facts WHERE system_address = $1',
            id64,
        ) or 0

        has_station_links = bool(
            await conn.fetchval("SELECT to_regclass('public.station_body_links') IS NOT NULL")
        )
        linked_station_count: int | None = None
        if has_station_links:
            linked_station_count = await conn.fetchval(
                """
                SELECT COUNT(*)::int
                FROM station_body_links l
                JOIN stations s ON s.id = l.station_id
                WHERE s.system_id64 = $1
                  AND l.association_status = 'local_matched'
                """,
                id64,
            ) or 0
        ringed_body_count = await conn.fetchval(
            """
            SELECT COUNT(*)::int
            FROM body_scan_facts
            WHERE system_address = $1
              AND ring_count > 0
            """,
            id64,
        ) or 0
        ring_identity_count = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT body_id)::int
            FROM body_rings
            WHERE system_id64 = $1
              AND body_id IS NOT NULL
              AND association_status = 'local_matched'
            """,
            id64,
        ) or 0

        latest_canonical_at = await _safe_fetchval(
            conn,
            """
            SELECT MAX(ts)::text
            FROM (
                SELECT MAX(sf.updated_at) AS ts
                FROM body_scan_facts sf
                WHERE sf.system_address = $1
                UNION ALL
                SELECT MAX(s.distance_updated_at) AS ts
                FROM stations s
                WHERE s.system_id64 = $1
                UNION ALL
                SELECT MAX(s.station_type_updated_at) AS ts
                FROM stations s
                WHERE s.system_id64 = $1
                UNION ALL
                SELECT MAX(s.body_name_updated_at) AS ts
                FROM stations s
                WHERE s.system_id64 = $1
            ) AS evidence_ts
            """,
            id64,
        )
        latest_status_at = await _safe_fetchval(
            conn,
            """
            SELECT COALESCE(eddn_updated_at, updated_at)::text
            FROM systems
            WHERE id64 = $1
            """,
            id64,
            stage='system status',
        )
        if latest_canonical_at is None:
            warnings.append(
                'Canonical evidence timestamps are unavailable; freshness remains conservative.'
            )
        bounded_staging = await _load_stage19bb_bounded_staging_evidence(conn, id64)


    observed_summary = await _safe_observed_summary(pool, id64)
    observed_total = _int_or_zero(observed_summary.get('total_count'))
    observed_latest_at = _text_or_none(observed_summary.get('latest_observed_at'))
    coverage = _build_coverage(
        body_count=body_count,
        scan_fact_count=scan_fact_count,
        station_count=station_count,
        linked_station_count=linked_station_count,
        ringed_body_count=ringed_body_count,
        ring_identity_count=ring_identity_count,
        canonical_updated_at=latest_canonical_at,
        observed_updated_at=observed_latest_at,
        bounded_staging_updated_at=bounded_staging.latest_source_updated_at,
        status_updated_at=latest_status_at,
    )

    items: list[WarehousePlannerEvidenceItem] = []

    if body_count > 0 or station_count > 0:
        linked_summary = (
            f'; {linked_station_count} local station-body links are matched'
            if linked_station_count is not None
            else ''
        )
        items.append(
            WarehousePlannerEvidenceItem(
                label='report_only',
                source='canonical',
                summary=(
                    f'Canonical app data for {system["name"]} includes {body_count} bodies and '
                    f'{station_count} stations{linked_summary}.'
                ),
            )
        )

    if observed_total > 0:
        items.append(
            WarehousePlannerEvidenceItem(
                label='needs_review',
                source='observed',
                summary=(
                    f'Observed evidence includes {observed_total} persisted facts'
                    f'{_fact_type_summary(observed_summary)}'
                    f'{_latest_summary(observed_latest_at)}.'
                ),
            )
        )

    if station_count > 0 and linked_station_count == 0:
        items.append(
            WarehousePlannerEvidenceItem(
                label='unresolved',
                source='unknown',
                summary='Station rows exist for this system, but no matched local station-body links are confirmed yet.',
            )
        )

    if bounded_staging.status == 'available' and bounded_staging.summary:
        items.append(
            WarehousePlannerEvidenceItem(
                label='report_only',
                source='warehouse_report_only',
                summary=bounded_staging.summary,
            )
        )

    if observed_summary.get('_warning'):
        warnings.append(str(observed_summary['_warning']))

    if not items:
        return LivePlannerEvidenceResult(
            availability='unavailable',
            envelope_status='not_evaluated' if bounded_staging.status == 'not_evaluated' else 'unavailable',
            items=[],
            freshness_status='unknown',
            evaluated_at=None,
            manual_review_required=True,
            bounded_staging=bounded_staging,
                coverage=coverage,
            warnings=[
                'No safe selected-system evidence is currently linked for this system; unavailable remains unknown.',
                *bounded_staging_warnings(bounded_staging),
                *warnings,
            ][:8],
        )

    if observed_total == 0:
        warnings.append(
            'No persisted observed facts were found for this system; selected-system evidence currently relies on canonical data only.'
        )

    warnings.extend(bounded_staging_warnings(bounded_staging))

    return LivePlannerEvidenceResult(
        availability='report_only',
        envelope_status='available',
        items=items[:6],
        freshness_status='not_evaluated',
        evaluated_at=_max_timestamp(latest_canonical_at, observed_latest_at, bounded_staging.latest_source_updated_at),
        manual_review_required=any(item.label in {'needs_review', 'verify', 'unresolved'} for item in items),
        bounded_staging=bounded_staging,
        coverage=coverage,
        warnings=warnings[:8],
    )


class PlannerEvidenceQueryError(RuntimeError):
    """A selected-system evidence query failed for a reason other than missing schema.

    Raised instead of silently reporting "evidence unavailable" when a query
    times out, is cancelled, or fails in any way that is not an undefined
    table/column. The message is bounded and built only from the exception
    class name and SQLSTATE — never the raw database message, which can
    carry SQL text, host names or DSN fragments.
    """

    def __init__(self, stage: str, exc: BaseException) -> None:
        sqlstate = getattr(exc, 'sqlstate', None)
        code = f' sqlstate={sqlstate}' if isinstance(sqlstate, str) and sqlstate.isalnum() else ''
        self.stage = stage[:64]
        self.error_type = type(exc).__name__[:64]
        super().__init__(f'{self.stage} query failed: {self.error_type}{code}')


# Only these mean "the evidence is not there to read". Everything else —
# QueryCanceledError (statement_timeout), asyncio timeouts, connection
# loss, permission errors — is an error and must surface as one.
_MISSING_SCHEMA_ERRORS = (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError)


async def _safe_fetchval(conn: asyncpg.Connection, query: str, *args: Any, stage: str = 'canonical evidence') -> Any:
    try:
        return await conn.fetchval(query, *args)
    except _MISSING_SCHEMA_ERRORS:
        return None
    except (asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError) as exc:
        raise PlannerEvidenceQueryError(stage, exc) from exc


async def _safe_observed_summary(pool: asyncpg.Pool, id64: int) -> dict[str, Any]:
    # The acquire and the to_regclass probe run under the same mapping as the
    # summary itself: a pool/connection failure or a timeout here is the same
    # outage the mapping exists to report, not "evidence is missing".
    try:
        async with pool.acquire() as conn:
            observed_table_present = bool(
                await conn.fetchval("SELECT to_regclass('public.observed_facts') IS NOT NULL")
            )
    except (asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError) as exc:
        raise PlannerEvidenceQueryError('observed evidence probe', exc) from exc
    if not observed_table_present:
        return {'_warning': 'Observed evidence storage is unavailable; selected-system evidence currently uses canonical data only.'}
    try:
        summary = await observed_fact_summary(pool, id64)
        if isinstance(summary, dict):
            return summary
    except _MISSING_SCHEMA_ERRORS:
        return {'_warning': 'Observed evidence could not be summarized safely; selected-system evidence currently uses canonical data only.'}
    except (asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError) as exc:
        raise PlannerEvidenceQueryError('observed evidence', exc) from exc
    return {'_warning': 'Observed evidence summary returned an unexpected shape; selected-system evidence currently uses canonical data only.'}


def _fact_type_summary(summary: dict[str, Any]) -> str:
    value = summary.get('by_fact_type')
    if not isinstance(value, dict) or not value:
        return ''
    ranked = sorted(
        (
            (str(key), count)
            for key, count in value.items()
            if isinstance(key, str) and isinstance(count, int)
        ),
        key=lambda item: (-item[1], item[0]),
    )[:2]
    if not ranked:
        return ''
    return ' across ' + ', '.join(f'{label}:{count}' for label, count in ranked)


def _latest_summary(value: str | None) -> str:
    return f'; latest observed at {value}' if value else ''


def _max_timestamp(*values: str | None) -> str | None:
    present = [value for value in values if isinstance(value, str) and value.strip()]
    return max(present) if present else None


def _default_bounded_staging(
    *,
    status: str = 'not_evaluated',
    summary: str | None = None,
    available_row_limits: list[int] | None = None,
    matched_row_count: int | None = None,
    latest_source_updated_at: str | None = None,
    source_run_key: str | None = None,
    bridge_key: str | None = None,
    row_limit: int | None = None,
) -> WarehousePlannerEvidenceBoundedStaging:
    metadata = load_stage19bb_closeout_metadata()
    return WarehousePlannerEvidenceBoundedStaging(
        status=status,
        report_only=True,
        bounded_staging_only=True,
        source_name=metadata.source_name if metadata else None,
        source_batch_label=metadata.source_batch_label if metadata else None,
        source_sha256=metadata.source_sha256 if metadata else None,
        source_run_key=source_run_key,
        bridge_key=bridge_key,
        row_limit=row_limit,
        available_row_limits=available_row_limits or [],
        matched_row_count=matched_row_count,
        latest_source_updated_at=latest_source_updated_at,
        summary=summary,
    )


def _unknown_coverage(summary: str) -> WarehousePlannerEvidenceCoverage:
    unknown_metric = WarehousePlannerEvidenceCoverageMetric(
        status='unknown',
        summary='Coverage is unknown for this metric in the current runtime.',
    )
    return WarehousePlannerEvidenceCoverage(
        body_scan=unknown_metric,
        station_links=unknown_metric,
        ring_identity=unknown_metric,
        source_freshness=WarehousePlannerEvidenceCoverageFreshness(),
        thin_data_reasons=[summary],
        summary=summary,
    )


def _build_coverage(
    *,
    body_count: int,
    scan_fact_count: int,
    station_count: int,
    linked_station_count: int | None,
    ringed_body_count: int,
    ring_identity_count: int,
    canonical_updated_at: str | None,
    observed_updated_at: str | None,
    bounded_staging_updated_at: str | None,
    status_updated_at: str | None,
) -> WarehousePlannerEvidenceCoverage:
    body_scan = _coverage_metric(
        label='body scans',
        known_count=scan_fact_count,
        total_count=body_count,
    )
    station_links = _coverage_metric(
        label='station links',
        known_count=linked_station_count,
        total_count=station_count,
    )
    ring_identity = _coverage_metric(
        label='ring identities',
        known_count=ring_identity_count,
        total_count=ringed_body_count,
        not_applicable_label='No ring-bearing bodies are currently known in canonical scan facts.',
    )

    thin_data_reasons: list[str] = []
    for reason in (
        _thin_reason('Body scan coverage', body_scan),
        _thin_reason('Station-link coverage', station_links),
        _thin_reason('Ring identity coverage', ring_identity),
    ):
        if reason:
            thin_data_reasons.append(reason)

    summary = (
        f'Coverage summary: {body_scan.summary} {station_links.summary} {ring_identity.summary}'
    )
    if not thin_data_reasons:
        thin_data_reasons.append('Selected-system coverage is coherent across the currently linked canonical lanes.')

    return WarehousePlannerEvidenceCoverage(
        body_scan=body_scan,
        station_links=station_links,
        ring_identity=ring_identity,
        source_freshness=WarehousePlannerEvidenceCoverageFreshness(
            canonical_updated_at=canonical_updated_at,
            observed_updated_at=observed_updated_at,
            bounded_staging_updated_at=bounded_staging_updated_at,
            status_updated_at=status_updated_at,
        ),
        thin_data_reasons=thin_data_reasons[:4],
        summary=summary,
    )


def _coverage_metric(
    *,
    label: str,
    known_count: int | None,
    total_count: int,
    not_applicable_label: str | None = None,
) -> WarehousePlannerEvidenceCoverageMetric:
    if known_count is None:
        return WarehousePlannerEvidenceCoverageMetric(
            status='unknown',
            known_count=None,
            total_count=total_count,
            coverage_ratio=None,
            summary=f'{label.capitalize()} remain unknown in this runtime.',
        )
    if total_count <= 0:
        return WarehousePlannerEvidenceCoverageMetric(
            status='not_applicable',
            known_count=known_count,
            total_count=total_count,
            coverage_ratio=None,
            summary=not_applicable_label or f'No {label} are required for this selected system yet.',
        )

    ratio = min(1.0, max(0.0, known_count / total_count))
    if known_count >= total_count:
        status = 'complete'
    elif known_count > 0:
        status = 'partial'
    else:
        status = 'missing'

    return WarehousePlannerEvidenceCoverageMetric(
        status=status,
        known_count=known_count,
        total_count=total_count,
        coverage_ratio=round(ratio, 4),
        summary=f'{known_count}/{total_count} {label} are currently covered.',
    )


def _thin_reason(prefix: str, metric: WarehousePlannerEvidenceCoverageMetric) -> str | None:
    if metric.status == 'partial':
        return f'{prefix} is partial: {metric.summary}'
    if metric.status == 'missing':
        return f'{prefix} is missing: {metric.summary}'
    if metric.status == 'unknown':
        return f'{prefix} is unknown: {metric.summary}'
    return None


def bounded_staging_warnings(value: WarehousePlannerEvidenceBoundedStaging) -> list[str]:
    if value.status == 'available':
        return [
            'Stage 19BB bounded staging evidence is available for this system as report-only context only; it is not canonical truth and does not imply full EDSM coverage.',
        ]
    if value.status == 'unavailable':
        return [
            'No Stage 19BB bounded staging evidence is linked to this selected system in the committed closeout runs; bounded staging remains unavailable.',
        ]
    return [
        'Stage 19BB bounded staging evidence is not safely queryable in this runtime; bounded staging remains not evaluated.',
    ]


@lru_cache(maxsize=1)
def load_stage19bb_closeout_metadata() -> Stage19bbCloseoutMetadata | None:
    try:
        authority = json.loads(AUTHORITY_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None

    closeout = authority.get('stage19bb_execution_closeout')
    if not isinstance(closeout, dict):
        return None

    runs_value = closeout.get('runs')
    if not isinstance(runs_value, list) or not runs_value:
        return None

    runs: list[Stage19bbRunMetadata] = []
    for run in runs_value:
        if not isinstance(run, dict):
            continue
        row_limit = run.get('limit')
        source_run_key = run.get('source_run_key')
        bridge_key = run.get('bridge_key')
        if not isinstance(row_limit, int) or not isinstance(source_run_key, str) or not isinstance(bridge_key, str):
            continue
        runs.append(
            Stage19bbRunMetadata(
                row_limit=row_limit,
                source_run_key=source_run_key,
                bridge_key=bridge_key,
            )
        )

    source_name = closeout.get('source_name')
    source_batch_label = closeout.get('source_batch_label')
    source_sha256 = closeout.get('approved_source_sha256')
    if not runs or not isinstance(source_name, str) or not isinstance(source_batch_label, str) or not isinstance(source_sha256, str):
        return None

    return Stage19bbCloseoutMetadata(
        source_name=source_name,
        source_batch_label=source_batch_label,
        source_sha256=source_sha256,
        runs=tuple(sorted(runs, key=lambda item: item.row_limit)),
    )


async def _load_stage19bb_bounded_staging_evidence(
    conn: asyncpg.Connection,
    id64: int,
) -> WarehousePlannerEvidenceBoundedStaging:
    metadata = load_stage19bb_closeout_metadata()
    if metadata is None:
        return _default_bounded_staging(status='not_evaluated')

    table_state = await conn.fetchrow(
        """
        SELECT
          to_regclass('public.enrichment_source_runs') IS NOT NULL AS has_bridge,
          to_regclass('public.staging_edsm_stations') IS NOT NULL AS has_staging
        """
    )
    has_bridge = bool(_row_value(table_state, 'has_bridge'))
    has_staging = bool(_row_value(table_state, 'has_staging'))
    if not (has_bridge and has_staging):
        return _default_bounded_staging(status='not_evaluated')

    rows = await conn.fetch(
        """
        SELECT
          esr.source_run_key AS bridge_key,
          COUNT(*)::int AS matched_row_count,
          MAX(ses.source_updated_at)::text AS latest_source_updated_at
        FROM enrichment_source_runs esr
        JOIN staging_edsm_stations ses
          ON ses.source_run_id = esr.id
        WHERE esr.source_run_key = ANY($1::text[])
          AND ses.system_id64 = $2
        GROUP BY esr.source_run_key
        """,
        [run.bridge_key for run in metadata.runs],
        id64,
    )

    by_bridge_key = {
        str(_row_value(row, 'bridge_key')): {
            'matched_row_count': _int_or_zero(_row_value(row, 'matched_row_count')),
            'latest_source_updated_at': _text_or_none(_row_value(row, 'latest_source_updated_at')),
        }
        for row in rows
    }
    available_runs = [
        run for run in metadata.runs
        if by_bridge_key.get(run.bridge_key, {}).get('matched_row_count', 0) > 0
    ]
    if not available_runs:
        return _default_bounded_staging(status='unavailable')

    best_run = max(available_runs, key=lambda item: item.row_limit)
    best_row = by_bridge_key[best_run.bridge_key]
    limits = sorted(run.row_limit for run in available_runs)
    count = best_row['matched_row_count']
    plural = '' if count == 1 else 's'
    return _default_bounded_staging(
        status='available',
        source_run_key=best_run.source_run_key,
        bridge_key=best_run.bridge_key,
        row_limit=best_run.row_limit,
        available_row_limits=limits,
        matched_row_count=count,
        latest_source_updated_at=best_row['latest_source_updated_at'],
        summary=(
            f'Stage 19BB bounded staging evidence includes {count} staging row{plural} '
            f'for this system in the approved {best_run.row_limit}-row context; it remains bounded staging-only review context, '
            'not canonical truth and not full EDSM coverage.'
        ),
    )


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _row_value(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return None


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
