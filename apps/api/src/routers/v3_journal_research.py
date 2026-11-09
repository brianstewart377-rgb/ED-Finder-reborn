"""Account-scoped V3 journal research lane (``/api/v1/journal/research-*``).

Wire contract: docs/superpowers/plans/2026-08-28-v3-journal-intelligence-foundation.md
Task 3. Consent/export logic lives in ``edfinder_api.journal.consent`` and
``edfinder_api.journal.export`` (Task 4, parallel) and is imported lazily so
this router stays import-checkable before those modules land.

Consent is explicit, versioned and revocable; upload never depends on it.
Exports are deterministic sanitized EDRE evidence batches; a WITHDRAW
supersedes prior lineage batches (supersede-not-delete) and blocks further
exports with 403 while no GRANT is effective.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from edfinder_api.auth import AuthenticatedUser, get_request_user, require_same_origin
from edfinder_api.config import limiter
from edfinder_api.deps import get_pool

router = APIRouter(prefix="/api/v1/journal", tags=["v3-journal-research"])

# docs/ROADMAP.md decision D4: exports lack colonisation events the CRE can use.
# Resuming requires a reviewed code change once those events are supported.
RESEARCH_EXPORT_PAUSED = True

MAX_EXPORT_OBSERVATIONS_DEFAULT = 1_000
MAX_EXPORT_OBSERVATIONS_CAP = 10_000


class V1Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class V3ResearchConsentState(V1Model):
    decision: Literal["GRANT", "WITHDRAW", "NONE"]
    consent_version: str | None
    sanitized_contract_version: str | None
    purpose: str | None
    audience_code: str | None
    decided_at: str | None
    withdrawable: bool


class V3ResearchConsentRequest(V1Model):
    decision: Literal["GRANT", "WITHDRAW"]


class V3ResearchExportRequest(V1Model):
    limit: int = Field(
        default=MAX_EXPORT_OBSERVATIONS_DEFAULT,
        ge=1,
        le=MAX_EXPORT_OBSERVATIONS_CAP,
    )


class V3ResearchExportReceipt(V1Model):
    export_batch_id: str
    sanitized_contract_version: str
    consent_version: str
    lineage_token: str
    observation_count: int
    payload_sha256: str
    batch_state: str
    created_at: str


class V3ResearchExportDetail(V1Model):
    receipt: V3ResearchExportReceipt
    payload: dict[str, Any]


Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=200)]


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _hex(value: object) -> str:
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _consent() -> Any:
    return importlib.import_module("edfinder_api.journal.consent")


def _export() -> Any:
    return importlib.import_module("edfinder_api.journal.export")


def _sanitize() -> Any:
    return importlib.import_module("edfinder_api.journal.sanitize")


async def _require_user(request: Request) -> AuthenticatedUser:
    user = await get_request_user(request)
    if user is None:
        raise HTTPException(401, "Sign-in required")
    return user


def _research_export_paused_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "type": "https://httpstatuses.com/503",
            "title": "Research exports paused",
            "status": 503,
            "detail": (
                "Research exports are paused because they cannot yet carry "
                "colonisation events the CRE can use."
            ),
            "reason": "research_export_paused",
        },
    )


def _state_from_effective(state: dict[str, Any] | None) -> V3ResearchConsentState:
    if state is None:
        return V3ResearchConsentState(
            decision="NONE",
            consent_version=None,
            sanitized_contract_version=None,
            purpose=None,
            audience_code=None,
            decided_at=None,
            withdrawable=False,
        )
    decision = str(state["decision"])
    return V3ResearchConsentState(
        decision=decision,  # type: ignore[assignment]
        consent_version=str(state["consent_version"]),
        sanitized_contract_version=str(state["sanitized_contract_version"]),
        purpose=str(state["purpose"]),
        audience_code=str(state["audience_code"]),
        decided_at=_iso(state.get("decided_at")),
        withdrawable=(decision == "GRANT"),
    )


def _receipt_from_row(row: dict[str, Any], payload_sha256_hex: str) -> V3ResearchExportReceipt:
    return V3ResearchExportReceipt(
        export_batch_id=str(row["export_batch_id"]),
        sanitized_contract_version=str(row["sanitized_contract_version"]),
        consent_version=str(row["consent_version"]),
        lineage_token=str(row["lineage_token"]),
        observation_count=int(row["observation_count"]),
        payload_sha256=payload_sha256_hex,
        batch_state=str(row["batch_state"]),
        created_at=_iso(row["created_at"]),
    )


async def _active_lineage_tokens(pool: asyncpg.Pool, account_id: uuid.UUID) -> list[str]:
    rows = await pool.fetch(
        """SELECT DISTINCT lineage_token
             FROM v3_private.research_export_batch
            WHERE owner_account_id = $1 AND batch_state = 'CREATED'""",
        account_id,
    )
    return [str(row["lineage_token"]) for row in rows]


def _observation_sort_key(row: dict[str, Any]) -> tuple:
    """Mirror Task 4's deterministic (event_type, canonical key JSON,
    event_timestamp) ordering — jsonb arrives as a dict via the pool codec."""
    return (
        row["event_type"],
        json.dumps(
            row["event_key"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
        row["event_timestamp"],
    )


async def _rebuild_observations(
    pool: asyncpg.Pool,
    account_id: uuid.UUID,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Rebuild the deterministic observation list for one account.

    Mirrors Task 4's ``build_export`` row selection byte-for-byte: same SQL
    (all event types, jsonb key order, timestamp), then the same Python
    re-sort, then ``sanitize_observation`` (None entries excluded). When the
    receipt manifest stores a raw-row ``limit`` (backend fix wave: receipt-
    internal ``manifest.limit``), the same limit is applied so a
    limit-truncated export replays byte-for-byte; without it (full-set
    exports) no limit is applied.
    """
    sanitize = _sanitize()
    if limit is None:
        rows = await pool.fetch(
            """SELECT event_type, event_key, event_payload, event_timestamp, source_record_hash
                 FROM v3_private.journal_event
                WHERE owner_account_id = $1
                ORDER BY event_timestamp DESC, event_key, source_record_hash""",
            account_id,
        )
    else:
        rows = await pool.fetch(
            """SELECT event_type, event_key, event_payload, event_timestamp, source_record_hash
                 FROM v3_private.journal_event
                WHERE owner_account_id = $1
                ORDER BY event_timestamp DESC, event_key, source_record_hash
                LIMIT $2""",
            account_id,
            limit,
        )
    rows.sort(key=_observation_sort_key)
    observations: list[dict[str, Any]] = []
    for row in rows:
        observation = sanitize.sanitize_observation(dict(row))
        if observation is not None:
            observations.append(observation)
    return observations


async def _is_supersede_batch(
    pool: asyncpg.Pool,
    account_id: uuid.UUID,
    batch_id: uuid.UUID,
    manifest: dict[str, Any] | None,
) -> bool:
    if isinstance(manifest, dict) and manifest.get("kind") == "supersede":
        return True
    return bool(await pool.fetchval(
        """SELECT EXISTS(
                SELECT 1 FROM v3_private.research_export_batch
                 WHERE superseded_by_batch_id = $1 AND owner_account_id = $2
             )""",
        batch_id,
        account_id,
    ))


async def _superseded_export_batch_ids(
    pool: asyncpg.Pool,
    account_id: uuid.UUID,
    supersede_batch_id: uuid.UUID,
) -> list[str]:
    rows = await pool.fetch(
        """SELECT export_batch_id
             FROM v3_private.research_export_batch
            WHERE superseded_by_batch_id = $1 AND owner_account_id = $2
            ORDER BY created_at, export_batch_id""",
        supersede_batch_id,
        account_id,
    )
    return [str(row["export_batch_id"]) for row in rows]


def _generated_at(row: dict[str, Any]) -> str:
    """Reproduce the export build's stamped ``generated_at``.

    Task 4's ``build_export`` stamps ``generated_at`` with
    ``datetime.isoformat(timespec='seconds')`` + a trailing ``Z`` and stores
    the same instant as the receipt's ``created_at``. The rebuild must
    reproduce that exact string, not Python's default microsecond/``+00:00``
    form, or the payload bytes diverge.
    """
    manifest = row.get("manifest")
    if isinstance(manifest, dict):
        value = manifest.get("generated_at")
        if value:
            return str(value)
    value = row["created_at"]
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return str(value)


def _rebuild_supersede_payload(
    export_module: Any,
    *,
    lineage_token: str,
    consent_version: str,
    superseded_export_batch_ids: list[str],
    generated_at: str,
) -> dict[str, Any]:
    return {
        "schema": export_module.EXPORT_SCHEMA,
        "schema_version": export_module.EXPORT_SCHEMA_VERSION,
        "kind": "supersede",
        "lineage_token": lineage_token,
        "superseded_export_batch_ids": superseded_export_batch_ids,
        "consent_version": consent_version,
        "generated_at": generated_at,
        "manifest": {"withdrawal": True},
    }


async def _rebuilt_payload(
    pool: asyncpg.Pool,
    user: AuthenticatedUser,
    row: dict[str, Any],
) -> dict[str, Any]:
    export_module = _export()
    batch_id = uuid.UUID(str(row["export_batch_id"]))
    if await _is_supersede_batch(pool, user.account_id, batch_id, row.get("manifest")):
        superseded_ids = await _superseded_export_batch_ids(pool, user.account_id, batch_id)
        return _rebuild_supersede_payload(
            export_module,
            lineage_token=str(row["lineage_token"]),
            consent_version=str(row["consent_version"]),
            superseded_export_batch_ids=superseded_ids,
            generated_at=_generated_at(row),
        )
    manifest = row.get("manifest")
    limit: int | None = None
    if isinstance(manifest, dict):
        stored_limit = manifest.get("limit")
        if stored_limit is not None:
            limit = int(stored_limit)
    observations = await _rebuild_observations(pool, user.account_id, limit=limit)
    return export_module.build_export_payload(
        export_batch_id=str(batch_id),
        lineage_token=str(row["lineage_token"]),
        consent_version=str(row["consent_version"]),
        generated_at=_generated_at(row),
        observations=observations,
    )


@router.get("/research-consent", response_model=V3ResearchConsentState, operation_id="getV3ResearchConsent")
@limiter.limit("60/minute")
async def get_v3_research_consent(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
) -> V3ResearchConsentState:
    user = await _require_user(request)
    current = await _consent().effective_consent(pool, user.account_id)
    return _state_from_effective(current)


@router.put("/research-consent", response_model=V3ResearchConsentState, operation_id="putV3ResearchConsent")
@limiter.limit("5/minute")
async def put_v3_research_consent(
    request: Request,
    body: V3ResearchConsentRequest,
    pool: asyncpg.Pool = Depends(get_pool),
) -> V3ResearchConsentState:
    user = await _require_user(request)
    require_same_origin(request)
    if RESEARCH_EXPORT_PAUSED and body.decision == "GRANT":
        raise _research_export_paused_error()
    consent = _consent()
    export_module = _export()
    current = await consent.effective_consent(pool, user.account_id)
    if body.decision == "GRANT":
        if current is not None and current["decision"] == "GRANT":
            raise HTTPException(
                409,
                "An active GRANT already exists for this consent version",
            )
    else:
        if current is None or current["decision"] != "GRANT":
            raise HTTPException(409, "No active GRANT to withdraw")
        # Supersede-not-delete: retire every CREATED lineage batch before the
        # withdrawal row is recorded.
        for lineage_token in await _active_lineage_tokens(pool, user.account_id):
            await export_module.supersede_lineage_batches(
                pool,
                user.account_id,
                lineage_token,
            )
    try:
        state = await consent.record_consent(pool, user.account_id, decision=body.decision)
    except HTTPException:
        raise
    except consent.ConsentStateError as exc:
        # Task 4's append-only ledger may refuse (e.g. re-grant while
        # withdrawn, or a duplicate GRANT decision row).
        raise HTTPException(409, str(exc)) from exc
    return _state_from_effective(state)


@router.post("/research-exports", response_model=V3ResearchExportReceipt, operation_id="createV3ResearchExport")
@limiter.limit("5/minute")
async def create_v3_research_export(
    request: Request,
    body: V3ResearchExportRequest,
    pool: asyncpg.Pool = Depends(get_pool),
) -> V3ResearchExportReceipt:
    user = await _require_user(request)
    require_same_origin(request)
    if RESEARCH_EXPORT_PAUSED:
        raise _research_export_paused_error()
    current = await _consent().effective_consent(pool, user.account_id)
    if current is None or current["decision"] != "GRANT":
        raise HTTPException(
            403,
            "Research export requires an active GRANT consent",
        )
    _, sha_hex, receipt_row = await _export().build_export(
        pool,
        user.account_id,
        limit=body.limit,
    )
    return _receipt_from_row(dict(receipt_row), str(sha_hex))


@router.get(
    "/research-exports/{export_batch_id}",
    response_model=V3ResearchExportDetail,
    operation_id="getV3ResearchExport",
)
@limiter.limit("60/minute")
async def get_v3_research_export(
    export_batch_id: uuid.UUID,
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
) -> V3ResearchExportDetail:
    user = await _require_user(request)
    row = await pool.fetchrow(
        """SELECT export_batch_id, sanitized_contract_version, consent_version,
                  lineage_token, observation_count, payload_sha256, batch_state,
                  manifest, created_at
             FROM v3_private.research_export_batch
            WHERE export_batch_id = $1 AND owner_account_id = $2""",
        export_batch_id,
        user.account_id,
    )
    if row is None:
        raise HTTPException(404, f"Research export batch {export_batch_id} not found")
    payload = await _rebuilt_payload(pool, user, dict(row))
    rebuilt_sha = hashlib.sha256(_export().payload_bytes(payload)).hexdigest()
    stored_sha = _hex(row["payload_sha256"])
    if rebuilt_sha != stored_sha:
        raise HTTPException(
            409,
            "Reconstructed export payload no longer matches the receipt "
            "(source data changed since the export)",
        )
    receipt = _receipt_from_row(dict(row), stored_sha)
    return V3ResearchExportDetail(receipt=receipt, payload=payload)


@router.get("/research-exports", response_model=list[V3ResearchExportReceipt], operation_id="listV3ResearchExports")
@limiter.limit("60/minute")
async def list_v3_research_exports(
    request: Request,
    offset: Offset = 0,
    limit: Limit = 50,
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[V3ResearchExportReceipt]:
    user = await _require_user(request)
    rows = await pool.fetch(
        """SELECT export_batch_id, sanitized_contract_version, consent_version,
                  lineage_token, observation_count, payload_sha256, batch_state,
                  manifest, created_at
             FROM v3_private.research_export_batch
            WHERE owner_account_id = $1
            ORDER BY created_at DESC, export_batch_id
            LIMIT $2 OFFSET $3""",
        user.account_id,
        limit,
        offset,
    )
    return [_receipt_from_row(dict(row), _hex(row["payload_sha256"])) for row in rows]
