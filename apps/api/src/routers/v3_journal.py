"""Account-scoped V3 journal personal lane (``/api/v1/journal/*``).

Wire contract: docs/superpowers/plans/2026-08-28-v3-journal-intelligence-foundation.md
Task 3. The store/projection implementations live in the ``edfinder_api.journal.*``
package (parallel tasks) and are imported lazily so this router stays
import-checkable before those modules land.

Auth: every endpoint requires an authenticated account session (401 when
absent). Writes additionally require a trusted browser origin
(``require_same_origin``). All journal data is scoped by ``owner_account_id``
from the session — never by anything client-supplied.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timezone
import math
from typing import Annotated, Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from edfinder_api.auth import AuthenticatedUser, get_request_user, require_same_origin
from edfinder_api.config import limiter
from edfinder_api.deps import get_pool
# Frozen 30-event allowlist — single source of truth is
# edfinder_api.journal.event_contract.JOURNAL_EVENT_ALLOWLIST (Task 2).
from edfinder_api.journal.event_contract import JOURNAL_EVENT_ALLOWLIST

router = APIRouter(prefix="/api/v1/journal", tags=["v3-journal"])

# Quotas (plan Global Constraints).
MAX_EVENTS_PER_REQUEST = 50_000
MAX_FILES_PER_IMPORT = 200
MAX_FILE_EVENT_COUNT = 1_000_000


class V1Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class V3JournalFileRef(V1Model):
    name: str = Field(min_length=1, max_length=512)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    line_count: int = Field(ge=0)
    event_count: int = Field(ge=0, le=MAX_FILE_EVENT_COUNT)
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None

    @field_validator("first_event_at", "last_event_at", mode="before")
    @classmethod
    def _utc_normalized(cls, value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if not isinstance(value, datetime):
            raise ValueError("file event timestamps must be ISO-8601 timestamps")
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class V3JournalEventInput(V1Model):
    event_type: str
    event_timestamp: datetime
    source_record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_file: str = Field(min_length=1, max_length=512)
    source_offset: int = Field(ge=0)
    payload: dict[str, Any]

    @field_validator("event_type")
    @classmethod
    def _event_type_allowlisted(cls, value: str) -> str:
        if value not in JOURNAL_EVENT_ALLOWLIST:
            raise ValueError("event_type is not in the journal allowlist")
        return value

    @field_validator("event_timestamp", mode="before")
    @classmethod
    def _utc_normalized(cls, value: object) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if not isinstance(value, datetime):
            raise ValueError("event_timestamp must be an ISO-8601 timestamp")
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class V3JournalImportRequest(V1Model):
    parser_version: str = Field(min_length=1, max_length=64)
    files: list[V3JournalFileRef] = Field(min_length=1, max_length=MAX_FILES_PER_IMPORT)
    events: list[V3JournalEventInput] = Field(default_factory=list, max_length=MAX_EVENTS_PER_REQUEST)


class V3HeldJournalFile(V1Model):
    name: str
    reason: str


class V3VerifiedImportReceipt(V1Model):
    import_ids: list[str]
    files_admitted: int
    files_skipped: int
    events_inserted: int
    duplicates_skipped: int
    held_files: list[V3HeldJournalFile]


class V3JournalSummaryResponse(V1Model):
    events_stored: int
    unique_bodies: int
    unique_bio_observations: int
    systems_observed: int
    last_imported_at: str | None
    event_counts: dict[str, int]
    imported_files: int


class GalaxyImpactSummary(V1Model):
    systems_discovered: int = Field(ge=0)
    bodies_scanned: int = Field(ge=0)
    earth_like_worlds: int = Field(ge=0)
    water_worlds: int = Field(ge=0)
    ammonia_worlds: int = Field(ge=0)
    terraformable_candidates: int = Field(ge=0)
    gas_giants: int = Field(ge=0)


class V3TravelHeatmapPoint(V1Model):
    kind: Literal["marker", "density"]
    # Lossless decimal SystemAddress in marker mode; None for density cells.
    system_id64: str | None
    system_name: str | None
    x: float
    y: float
    z: float
    visit_count: int = Field(ge=1)
    first_visited_at: str
    last_visited_at: str


class V3TravelHeatmapResponse(V1Model):
    mode: Literal["markers", "density"]
    cell_size_ly: float | None
    points: list[V3TravelHeatmapPoint]
    count: int = Field(ge=0)
    truncated: bool


Offset = Annotated[int, Query(ge=0)]
Coordinate = Annotated[float, Query(ge=-200_000, le=200_000)]
Limit = Annotated[int, Query(ge=1, le=1_000)]
StationOffset = Annotated[int, Query(ge=0, le=100_000)]
StationLimit = Annotated[int, Query(ge=1, le=100)]
StationObservationLimit = Annotated[int, Query(ge=1, le=200)]
MarketIdFilter = Annotated[str | None, Query(pattern=r"^[0-9]{1,20}$")]


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _store() -> Any:
    return importlib.import_module("edfinder_api.journal.store")


def _projections() -> Any:
    return importlib.import_module("edfinder_api.journal.projections")


async def _require_user(request: Request) -> AuthenticatedUser:
    user = await get_request_user(request)
    if user is None:
        raise HTTPException(401, "Sign-in required")
    return user


async def _event_counts(pool: asyncpg.Pool, account_id: uuid.UUID) -> dict[str, int]:
    rows = await pool.fetch(
        """SELECT event_type, count(*)::int AS n
             FROM v3_private.journal_event
            WHERE owner_account_id = $1
            GROUP BY event_type""",
        account_id,
    )
    return {str(row["event_type"]): int(row["n"]) for row in rows}


async def _imported_files(pool: asyncpg.Pool, account_id: uuid.UUID) -> int:
    return int(await pool.fetchval(
        "SELECT count(*) FROM v3_private.journal_import_file WHERE owner_account_id = $1",
        account_id,
    ))


# Chunked, resumable imports issue several requests per selection (one per
# batch), so the limit is higher than a one-shot import needed. Each request is
# authenticated and content-idempotent, and the client backs off on 429.
@router.post('/verified-imports', response_model=V3VerifiedImportReceipt,
             operation_id='createVerifiedJournalImport')
@limiter.limit('30/minute')
async def create_verified_journal_import(
    request: Request, body: V3JournalImportRequest,
    pool: asyncpg.Pool = Depends(get_pool),
) -> V3VerifiedImportReceipt:
    from edfinder_api.journal.commanders import verified_commanders
    from edfinder_api.journal.ownership import classify_files

    user = await _require_user(request)
    require_same_origin(request)
    files = [item.model_dump() for item in body.files]
    events = [item.model_dump() for item in body.events]
    names = {item['name'] for item in files}
    if any(event['source_file'] not in names for event in events):
        raise HTTPException(422, 'Event refers to a file absent from the manifest')
    async with pool.acquire() as conn:
        commanders = {
            row['journal_fid']: row
            for row in await verified_commanders(conn, user.account_id)
        }
    ownership = classify_files(files, events, set(commanders))
    held = [V3HeldJournalFile(name=row.name, reason=row.reason)
            for row in ownership if row.reason is not None]
    result = V3VerifiedImportReceipt(
        import_ids=[], files_admitted=0, files_skipped=0,
        events_inserted=0, duplicates_skipped=0, held_files=held,
    )
    # Each commander group commits independently. If a later global failure
    # interrupts the response, retrying the original selection is file/event
    # idempotent; held files have never entered the acquisition ledger.
    for fid, commander in commanders.items():
        accepted = {row.name for row in ownership if row.reason is None and row.fid == fid}
        if not accepted:
            continue
        async with pool.acquire() as conn, conn.transaction():
            # Receipt recovery and admission share the store's account lock.
            await conn.execute('SELECT pg_advisory_xact_lock(hashtext($1::text)::bigint)', str(user.account_id))
            # Recover original receipts on retries, scoped to the selected hashes.
            # Historical unassigned events are held; a prior account-level file hash
            # is not evidence that its events belong to this verified commander.
            existing = await conn.fetch(
                '''SELECT f.content_sha256, f.private_import_id,
                          EXISTS (SELECT 1 FROM v3_private.journal_file_event occurrence
                                   WHERE occurrence.journal_file_id=f.journal_file_id) AS has_events,
                          EXISTS (SELECT 1 FROM v3_private.journal_file_event occurrence
                                   JOIN v3_private.journal_event e USING (journal_event_id, owner_account_id)
                                   WHERE occurrence.journal_file_id=f.journal_file_id
                                     AND e.owner_commander_id IS DISTINCT FROM $3) AS unverified
                     FROM v3_private.journal_import_file f
                    WHERE f.owner_account_id=$1 AND f.content_sha256=ANY($2::bytea[])''',
                user.account_id,
                [bytes.fromhex(item['content_sha256']) for item in files if item['name'] in accepted],
                commander['commander_id'],
            )
            by_hash = {bytes(row['content_sha256']).hex(): row for row in existing}
            retry_ids = set()
            for item in files:
                if item['name'] not in accepted:
                    continue
                prior = by_hash.get(item['content_sha256'])
                if prior is None:
                    continue
                accepted.discard(item['name'])
                if prior['unverified'] or not prior['has_events']:
                    result.held_files.append(V3HeldJournalFile(name=item['name'], reason='previous_import_needs_ownership_review'))
                else:
                    result.files_skipped += 1
                    retry_ids.add(str(prior['private_import_id']))
            result.import_ids.extend(sorted(retry_ids))
            if not accepted:
                continue
            store = _store()
            try:
                import_id, counts = await store.import_journal_batch(
                    pool, account_id=user.account_id, commander_id=commander['commander_id'],
                    commander_fid=fid, connection=conn,
                    parser_version=body.parser_version,
                    files=[item for item in files if item['name'] in accepted],
                    events=[event for event in events if event['source_file'] in accepted],
                )
            except store.JournalQuotaExceededError as exc:
                raise HTTPException(429, 'Import paused at the daily quota; retry is safe') from exc
            except ValueError:
                # One malformed commander group must not discard the other groups.
                result.held_files.extend(
                    V3HeldJournalFile(name=name, reason='validation_failed') for name in sorted(accepted)
                )
                continue
            result.import_ids.append(str(import_id))
            result.files_admitted += counts.files_admitted
            result.files_skipped += counts.files_skipped
            result.events_inserted += counts.events_inserted
            result.duplicates_skipped += counts.duplicates_skipped
            labels = []
            for event in events:
                if event['source_file'] not in accepted or event['event_type'] not in ('Commander', 'LoadGame'):
                    continue
                payload = event['payload']
                label = payload.get('Name' if event['event_type'] == 'Commander' else 'Commander')
                if payload.get('FID') == fid and isinstance(label, str) and label.strip():
                    labels.append((event['event_timestamp'], label.strip()[:128]))
            if labels:
                observed_at, label = max(labels)
                await conn.execute(
                    '''UPDATE v3_identity.commander c
                          SET commander_name=$2, journal_name_observed_at=$3, updated_at=transaction_timestamp()
                        WHERE commander_id=$1 AND (journal_name_observed_at IS NULL OR journal_name_observed_at < $3)
                          AND EXISTS (SELECT 1 FROM v3_identity.account_commander_access a
                                      WHERE a.commander_id=c.commander_id AND a.account_id=$4
                                        AND a.access_role='OWNER' AND a.revoked_at IS NULL)''',
                    commander['commander_id'], label, observed_at, user.account_id,
                )
    result.import_ids = sorted(set(result.import_ids))
    return result


@router.get('/galaxy-impact', response_model=GalaxyImpactSummary, operation_id='getJournalGalaxyImpact')
@limiter.limit('30/minute')
async def get_journal_galaxy_impact(
    request: Request,
    response: Response,
    pool: asyncpg.Pool = Depends(get_pool),
) -> GalaxyImpactSummary:
    from edfinder_api.journal.impact import GalaxyImpactUnavailable, galaxy_impact

    user = await _require_user(request)
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        counts = await galaxy_impact(pool, user.account_id)
    except (GalaxyImpactUnavailable, TimeoutError, asyncpg.QueryCanceledError) as exc:
        raise HTTPException(
            503, 'Your galaxy impact is unavailable right now. Please try again.',
            headers={'Cache-Control': 'private, no-store'},
        ) from exc
    return GalaxyImpactSummary(**counts)


@router.get('/travel-heatmap', response_model=V3TravelHeatmapResponse,
            operation_id='getV3JournalTravelHeatmap')
@limiter.limit('60/minute')
async def get_v3_journal_travel_heatmap(
    request: Request,
    response: Response,
    min_x: Coordinate,
    max_x: Coordinate,
    min_y: Coordinate,
    max_y: Coordinate,
    min_z: Coordinate,
    max_z: Coordinate,
    zoom: Annotated[float, Query(ge=0.001, le=5_000)],
    limit: Annotated[int, Query(ge=1, le=20_000)] = 5_000,
    pool: asyncpg.Pool = Depends(get_pool),
) -> V3TravelHeatmapResponse:
    """The signed-in account's imported travel (FSDJump/CarrierJump/Location
    star positions) inside one viewport box, aggregated server-side."""
    from edfinder_api.journal.travel import TravelHeatmapUnavailable, travel_heatmap

    user = await _require_user(request)
    response.headers['Cache-Control'] = 'private, no-store'
    if not all(math.isfinite(value) for value in (min_x, max_x, min_y, max_y, min_z, max_z, zoom)):
        raise HTTPException(422, 'viewport bounds and zoom must be finite')
    try:
        data = await travel_heatmap(
            pool, user.account_id,
            min_x=min_x, max_x=max_x, min_y=min_y, max_y=max_y, min_z=min_z, max_z=max_z,
            zoom=zoom, limit=limit,
        )
    except (TravelHeatmapUnavailable, TimeoutError, asyncpg.QueryCanceledError) as exc:
        raise HTTPException(
            503, 'Your travel heatmap is unavailable right now. Please try again.',
            headers={'Cache-Control': 'private, no-store'},
        ) from exc
    points = [V3TravelHeatmapPoint(**point) for point in data['points']]
    return V3TravelHeatmapResponse(
        mode=data['mode'], cell_size_ly=data['cell_size_ly'], points=points,
        count=len(points), truncated=data['truncated'],
    )


@router.get("/summary", response_model=V3JournalSummaryResponse, operation_id="getV3JournalSummary")
@limiter.limit("60/minute")
async def get_v3_journal_summary(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
) -> V3JournalSummaryResponse:
    user = await _require_user(request)
    data = await _projections().journal_summary(pool, user.account_id)
    return V3JournalSummaryResponse(
        events_stored=int(data["events_stored"]),
        unique_bodies=int(data["unique_bodies"]),
        unique_bio_observations=int(data["unique_bio_observations"]),
        systems_observed=int(data["systems_observed"]),
        last_imported_at=_iso(data.get("last_imported_at")),
        event_counts=await _event_counts(pool, user.account_id),
        imported_files=await _imported_files(pool, user.account_id),
    )


