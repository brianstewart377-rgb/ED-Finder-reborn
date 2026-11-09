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


class V3JournalImportReceipt(V1Model):
    import_id: str
    # 'READY' on create; private_import.import_state on fetch.
    status: str
    files_received: int
    files_skipped: int
    files_admitted: int
    events_received: int
    events_inserted: int
    duplicates_skipped: int
    privacy_stripped_fields: int
    event_counts: dict[str, int]
    started_at: str
    finished_at: str


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


class V3JournalSystemRow(V1Model):
    system_id64: str
    system_name: str | None
    first_observed_at: str | None
    last_observed_at: str | None
    visit_count: int


class V3JournalBodyRow(V1Model):
    system_id64: str
    body_id: str
    body_name: str | None
    first_observed_at: str | None
    last_observed_at: str | None
    scan_count: int


class V3CodexEntryRow(V1Model):
    entry_id: str
    name: str | None
    category: str | None
    subcategory: str | None
    region: str | None
    system_id64: str | None
    body_id: str | None
    first_observed_at: str | None
    last_observed_at: str | None


class V3OrganicProgressRow(V1Model):
    genus: str
    species: str
    variant: str | None
    stages: list[str]
    first_observed_at: str | None
    last_observed_at: str | None


class V3SaleRow(V1Model):
    bio_data: list[dict[str, Any]]
    market_id: str | None
    observed_at: str


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


class V3StationEconomyShare(V1Model):
    # Journal economy token (e.g. ``$economy_Industrial;``) and its share of
    # the station's economy mix (0..1) exactly as the game reported it.
    name: str
    name_localised: str | None
    proportion: float | None


class V3DockedObservation(V1Model):
    observed_at: str
    system_id64: str | None
    system_name: str | None
    station_name: str | None
    station_type: str | None
    station_government: str | None
    station_allegiance: str | None
    dist_from_star_ls: float | None
    economies: list[V3StationEconomyShare]
    services: list[str]


class V3StationHistory(V1Model):
    market_id: str
    observation_count: int = Field(ge=1)
    first_docked_at: str
    last_docked_at: str
    # True when observation_count exceeds the observations returned; the
    # most recent ``max_observations`` are kept, listed oldest first.
    observations_truncated: bool
    observations: list[V3DockedObservation]


class V3StationObservationsPage(V1Model):
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    has_more: bool
    stations: list[V3StationHistory]


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


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
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


@router.post("/imports", response_model=V3JournalImportReceipt, operation_id="createV3JournalImport")
@limiter.limit("5/minute")
async def create_v3_journal_import(
    request: Request,
    body: V3JournalImportRequest,
    pool: asyncpg.Pool = Depends(get_pool),
) -> V3JournalImportReceipt:
    user = await _require_user(request)
    require_same_origin(request)
    files = [
        {
            "name": ref.name,
            "content_sha256": ref.content_sha256,
            "size_bytes": ref.size_bytes,
            "line_count": ref.line_count,
            "event_count": ref.event_count,
            "first_event_at": ref.first_event_at,
            "last_event_at": ref.last_event_at,
        }
        for ref in body.files
    ]
    events = [
        {
            "event_type": event.event_type,
            "event_timestamp": event.event_timestamp,
            "source_record_hash": event.source_record_hash,
            "source_file": event.source_file,
            "source_offset": event.source_offset,
            "payload": event.payload,
        }
        for event in body.events
    ]
    store = _store()
    try:
        import_id, counts = await store.import_journal_batch(
            pool,
            account_id=user.account_id,
            commander_id=None,
            parser_version=body.parser_version,
            files=files,
            events=events,
        )
    except store.JournalQuotaExceededError as exc:
        raise HTTPException(429, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = await pool.fetchrow(
        """SELECT imported_at
             FROM v3_private.private_import
            WHERE private_import_id = $1 AND owner_account_id = $2""",
        import_id,
        user.account_id,
    )
    started_at = row["imported_at"] if row else datetime.now(timezone.utc)
    finished_at = datetime.now(timezone.utc)
    return V3JournalImportReceipt(
        import_id=str(import_id),
        status="READY",
        files_received=counts.files_received,
        files_skipped=counts.files_skipped,
        files_admitted=counts.files_admitted,
        events_received=counts.events_received,
        events_inserted=counts.events_inserted,
        duplicates_skipped=counts.duplicates_skipped,
        privacy_stripped_fields=counts.privacy_stripped_fields,
        event_counts=dict(counts.event_counts),
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
    )


@router.get("/imports/{import_id}", response_model=V3JournalImportReceipt, operation_id="getV3JournalImport")
@limiter.limit("60/minute")
async def get_v3_journal_import(
    import_id: uuid.UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
) -> V3JournalImportReceipt:
    user = await _require_user(request)
    # Migration 003 persists no per-import request counters; the GET receipt
    # reconstructs the persisted subset (files/events actually admitted under
    # this import) and reports zeros for the transient request-level counters.
    row = await pool.fetchrow(
        """SELECT pi.private_import_id, pi.import_state, pi.imported_at,
                  (SELECT count(*)::int
                     FROM v3_private.journal_import_file f
                    WHERE f.private_import_id = pi.private_import_id
                      AND f.owner_account_id = pi.owner_account_id) AS files_admitted,
                  (SELECT count(*)::int
                     FROM v3_private.journal_event ev
                    WHERE ev.private_import_id = pi.private_import_id
                      AND ev.owner_account_id = pi.owner_account_id) AS events_inserted
             FROM v3_private.private_import pi
            WHERE pi.private_import_id = $1 AND pi.owner_account_id = $2""",
        import_id,
        user.account_id,
    )
    if row is None:
        raise HTTPException(404, f"Journal import {import_id} not found")
    imported_at = _iso(row["imported_at"])
    return V3JournalImportReceipt(
        import_id=str(row["private_import_id"]),
        status=str(row["import_state"]),
        files_received=0,
        files_skipped=0,
        files_admitted=int(row["files_admitted"]),
        events_received=0,
        events_inserted=int(row["events_inserted"]),
        duplicates_skipped=0,
        privacy_stripped_fields=0,
        event_counts={},
        started_at=imported_at or "",
        finished_at=imported_at or "",
    )


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


@router.get("/systems", response_model=list[V3JournalSystemRow], operation_id="listV3JournalSystems")
@limiter.limit("60/minute")
async def list_v3_journal_systems(
    request: Request,
    offset: Offset = 0,
    limit: Limit = 50,
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[V3JournalSystemRow]:
    user = await _require_user(request)
    rows = await _projections().visited_systems(pool, user.account_id, offset=offset, limit=limit)
    return [
        V3JournalSystemRow(
            system_id64=str(row["system_id64"]),
            system_name=_opt_str(row.get("system_name")),
            first_observed_at=_iso(row.get("first_observed_at")),
            last_observed_at=_iso(row.get("last_observed_at")),
            visit_count=int(row["visit_count"]),
        )
        for row in rows
    ]


@router.get("/bodies", response_model=list[V3JournalBodyRow], operation_id="listV3JournalBodies")
@limiter.limit("60/minute")
async def list_v3_journal_bodies(
    request: Request,
    offset: Offset = 0,
    limit: Limit = 50,
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[V3JournalBodyRow]:
    user = await _require_user(request)
    rows = await _projections().scanned_bodies(pool, user.account_id, offset=offset, limit=limit)
    return [
        V3JournalBodyRow(
            system_id64=str(row["system_id64"]),
            body_id=str(row["body_id"]),
            body_name=_opt_str(row.get("body_name")),
            first_observed_at=_iso(row.get("first_observed_at")),
            last_observed_at=_iso(row.get("last_observed_at")),
            scan_count=int(row["scan_count"]),
        )
        for row in rows
    ]


@router.get("/codex", response_model=list[V3CodexEntryRow], operation_id="listV3JournalCodex")
@limiter.limit("60/minute")
async def list_v3_journal_codex(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[V3CodexEntryRow]:
    user = await _require_user(request)
    rows = await _projections().codex_entries(pool, user.account_id)
    return [
        V3CodexEntryRow(
            entry_id=str(row["entry_id"]),
            name=_opt_str(row.get("name")),
            category=_opt_str(row.get("category")),
            subcategory=_opt_str(row.get("subcategory")),
            region=_opt_str(row.get("region")),
            system_id64=_opt_str(row.get("system_id64")),
            body_id=_opt_str(row.get("body_id")),
            first_observed_at=_iso(row.get("first_observed_at")),
            last_observed_at=_iso(row.get("last_observed_at")),
        )
        for row in rows
    ]


@router.get("/organics", response_model=list[V3OrganicProgressRow], operation_id="listV3JournalOrganics")
@limiter.limit("60/minute")
async def list_v3_journal_organics(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[V3OrganicProgressRow]:
    user = await _require_user(request)
    rows = await _projections().organic_progress(pool, user.account_id)
    return [
        V3OrganicProgressRow(
            genus=str(row["genus"]),
            species=str(row["species"]),
            variant=_opt_str(row.get("variant")),
            stages=[str(stage) for stage in (row.get("stages") or [])],
            first_observed_at=_iso(row.get("first_observed_at")),
            last_observed_at=_iso(row.get("last_observed_at")),
        )
        for row in rows
    ]


@router.get("/sales", response_model=list[V3SaleRow], operation_id="listV3JournalSales")
@limiter.limit("60/minute")
async def list_v3_journal_sales(
    request: Request,
    offset: Offset = 0,
    limit: Limit = 50,
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[V3SaleRow]:
    user = await _require_user(request)
    rows = await _projections().sale_history(pool, user.account_id, offset=offset, limit=limit)
    return [
        V3SaleRow(
            bio_data=[dict(item) for item in (row.get("bio_data") or [])],
            market_id=_opt_str(row.get("market_id")),
            observed_at=_iso(row.get("observed_at")) or "",
        )
        for row in rows
    ]


@router.get(
    "/station-observations",
    response_model=V3StationObservationsPage,
    operation_id="listV3JournalStationObservations",
)
@limiter.limit("60/minute")
async def list_v3_journal_station_observations(
    request: Request,
    response: Response,
    offset: StationOffset = 0,
    limit: StationLimit = 25,
    max_observations: StationObservationLimit = 50,
    market_id: MarketIdFilter = None,
    pool: asyncpg.Pool = Depends(get_pool),
) -> V3StationObservationsPage:
    """Your own Docked history per station (MarketID), newest station first.

    Built only from journals you imported. Each observation is one docking
    with the station's economy mix and services at that moment, listed
    oldest first, so a before/after experiment reads top-down.
    """
    user = await _require_user(request)
    response.headers["Cache-Control"] = "private, no-store"
    stations, has_more = await _projections().station_observations(
        pool,
        user.account_id,
        offset=offset,
        limit=limit,
        max_observations=max_observations,
        market_id=market_id,
    )
    return V3StationObservationsPage(
        offset=offset,
        limit=limit,
        has_more=has_more,
        stations=[
            V3StationHistory(
                market_id=str(station["market_id"]),
                observation_count=int(station["observation_count"]),
                first_docked_at=_iso(station["first_docked_at"]) or "",
                last_docked_at=_iso(station["last_docked_at"]) or "",
                observations_truncated=int(station["observation_count"]) > len(station["observations"]),
                observations=[
                    V3DockedObservation(
                        observed_at=_iso(item["observed_at"]) or "",
                        system_id64=item["system_id64"],
                        system_name=item["system_name"],
                        station_name=item["station_name"],
                        station_type=item["station_type"],
                        station_government=item["station_government"],
                        station_allegiance=item["station_allegiance"],
                        dist_from_star_ls=item["dist_from_star_ls"],
                        economies=[V3StationEconomyShare(**economy) for economy in item["economies"]],
                        services=list(item["services"]),
                    )
                    for item in station["observations"]
                ],
            )
            for station in stations
        ],
    )
