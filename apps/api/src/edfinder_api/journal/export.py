"""V3 deterministic sanitized export builder + supersede-not-delete (Task 4).

Builds ``ed-finder-cre-journal-observation-export`` v1.0.0 payloads from
sanitized observations (see sanitize.py), writes ``research_export_batch``
receipts into ``v3_private``, and supersedes prior batches on consent
withdrawal. The payload is deterministic: rows are ordered by
(event_type, canonical key json, event_timestamp), serialized with
``json.dumps(sort_keys=True, separators=(",", ":"))``, and the payload
SHA-256 is stored in the receipt so replay is provably byte-identical.

Determinism contract with routers: ``build_export`` stamps ``generated_at``
at wall-clock build time and stores the same instant as the receipt's
``created_at``. Rebuilding a payload byte-identically therefore requires
passing ``generated_at=<receipt created_at>`` to ``build_export_payload``.

Supersede (consent withdrawal): CREATED batches of one lineage token are
marked SUPERSEDED with ``superseded_by_batch_id`` pointing at a new ledger
row whose payload is the ``kind: "supersede"`` batch (manifest
``{"withdrawal": true}``). Observations and receipts are never deleted
(R8: remove from future reconciliation; recomputation is EDRE-side).

Consent gating note: this module does NOT check consent — the research
router (Task 3) owns the "no active GRANT -> 403" gate and orchestrates
``supersede_lineage_batches`` on withdrawal.

The export has no access to canonical galaxy tables at all — SQL here
touches only ``v3_private.journal_event`` and ``v3_private.research_export_batch``.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg

from edfinder_api.journal.consent import CONSENT_VERSION, SANITIZED_CONTRACT_VERSION
from edfinder_api.journal.sanitize import sanitize_observation

# Schema id retained for wire-contract compatibility after the CRE→EDRE rename (2026-08-29).
EXPORT_SCHEMA = 'ed-finder-cre-journal-observation-export'
EXPORT_SCHEMA_VERSION = '1.0.0'

# Exact per plan; counts are ATTESTED, never EDRE-verifiable (Q2/Q9).
ATTESTATION_NOTE = (
    'attested-not-proven: one Commander = one observer chain; '
    'lineage tokens do not verify cross-export independence'
)

# Receipt INSERT column order (mirrored by the unit-test fake pool).
_EXPORT_BATCH_COLUMNS = (
    'export_batch_id', 'owner_account_id', 'consent_version',
    'sanitized_contract_version', 'lineage_token', 'observation_count',
    'manifest', 'payload_sha256', 'batch_state', 'superseded_by_batch_id',
    'created_at',
)

_SELECT_EVENTS_SQL = """
SELECT event_type, event_key, event_payload, event_timestamp, source_record_hash
FROM v3_private.journal_event
WHERE owner_account_id = $1
ORDER BY event_timestamp DESC, event_key, source_record_hash
LIMIT $2
"""


def _new_lineage_token() -> str:
    """Opaque per-export lineage token matching ``^[A-Za-z0-9_-]{16,64}$``."""
    # token_urlsafe already uses the url-safe base64 alphabet (no '+', '/',
    # or '=' padding); the replaces are a belt-and-braces guarantee for the
    # DB CHECK constraint.
    token = secrets.token_urlsafe(16).replace('+', '-').replace('/', '_')
    return token[:64]


def _stamp_now() -> tuple[datetime, str]:
    """One instant in two forms: the tz-aware ``datetime`` bound into
    timestamptz columns (never an ISO string — asyncpg type fidelity) and
    the second-precision ``Z``-suffixed string stamped into the payload's
    ``generated_at`` (the deterministic serialization form). Both derive
    from the SAME instant so receipt ``created_at`` equals ``generated_at``
    (replay contract)."""
    instant = datetime.now(timezone.utc)
    return instant, instant.isoformat(timespec='seconds').replace('+00:00', 'Z')


def build_export_payload(
    *,
    export_batch_id: str,
    lineage_token: str,
    consent_version: str,
    generated_at: str,
    observations: list[dict],
) -> dict:
    """Exact v1.0.0 export payload shape (plan 'Export payload shape').

    The manifest is the EDRE-visible manifest and MUST stay exactly the
    schema's ``exportManifest`` (EDRE consumer schema enforces
    ``additionalProperties: false``). Replay metadata such as the raw-row
    ``limit`` is persisted separately in the RECEIPT's manifest column (see
    ``build_export``), never inside the payload manifest.
    """
    manifest: dict[str, Any] = {
        'observation_count': len(observations),
        'contributing_accounts_attested': 1,
        'attestation_note': ATTESTATION_NOTE,
    }
    return {
        'schema': EXPORT_SCHEMA,
        'schema_version': EXPORT_SCHEMA_VERSION,
        'export_batch_id': export_batch_id,
        'lineage_token': lineage_token,
        'consent_version': consent_version,
        'sanitized_contract_version': SANITIZED_CONTRACT_VERSION,
        'generated_at': generated_at,
        'manifest': manifest,
        'observations': observations,
    }


def payload_bytes(payload: dict) -> bytes:
    """Deterministic byte serialization: sorted keys, compact separators."""
    return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _observation_sort_key(row: dict):
    # MUST stay byte-identical to the research router's rebuild sort key
    # (v3_journal_research._observation_sort_key): jsonb always arrives as a
    # dict via the pool codec, so the canonical form is the json.dumps string.
    return (
        row['event_type'],
        json.dumps(
            row['event_key'],
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=True,
        ),
        row['event_timestamp'],
    )


async def build_export(
    pool: asyncpg.Pool,
    account_id: uuid.UUID,
    *,
    limit: int,
) -> tuple[dict, str, dict]:
    """Build one deterministic sanitized export for an account.

    Returns ``(payload, payload_sha256_hex, receipt_row)``. The receipt row
    is written to ``v3_private.research_export_batch`` with
    ``batch_state='CREATED'``. Excluded event types are dropped by
    ``sanitize_observation``; the surviving observations are deterministically
    ordered by (event_type, canonical key json, event_timestamp).

    ``limit`` applies to RAW ROWS BEFORE sanitization exclusion: an account
    with excluded (travel/identity) events exports FEWER observations than
    the limit — never more. Rows are selected newest-first (chronological)
    so limit truncation keeps the most recent events rather than biasing the
    surviving set toward alphabetically-earlier event types. The applied
    limit and a ``truncated`` flag are persisted in the RECEIPT's manifest
    column (receipt-internal replay metadata — deliberately NOT in the
    EDRE-visible payload manifest, whose frozen consumer schema forbids
    extra keys) so a limit-truncated export is replayable: the rebuild reads
    the limit back from the stored receipt manifest.
    """
    rows = await pool.fetch(_SELECT_EVENTS_SQL, account_id, limit + 1)
    truncated = len(rows) > limit
    rows = rows[:limit]
    event_rows = [
        {
            'event_type': row['event_type'],
            'event_key': row['event_key'],
            'event_payload': row['event_payload'],
            'event_timestamp': row['event_timestamp'],
            'source_record_hash': row['source_record_hash'],
        }
        for row in rows
    ]
    # SQL LIMIT is the primary cap; this slice is a deterministic backstop
    # so the exported observation count never exceeds the limit.
    event_rows = event_rows[:limit]
    event_rows.sort(key=_observation_sort_key)

    observations: list[dict] = []
    for event_row in event_rows:
        observation = sanitize_observation(event_row)
        if observation is not None:
            observations.append(observation)

    created_at_dt, generated_at = _stamp_now()
    export_batch_id = str(uuid.uuid4())
    lineage_token = _new_lineage_token()
    payload = build_export_payload(
        export_batch_id=export_batch_id,
        lineage_token=lineage_token,
        consent_version=CONSENT_VERSION,
        generated_at=generated_at,
        observations=observations,
    )
    # Receipt-internal replay metadata: the raw-row limit is needed by the
    # rebuild to reproduce a limit-truncated export byte-for-byte. It lives
    # in the RECEIPT manifest only — the payload manifest (EDRE-visible) is
    # schema-frozen and must not carry it.
    receipt_manifest = {**payload['manifest'], 'limit': limit, 'truncated': truncated}
    payload_bytes_ = payload_bytes(payload)
    sha_hex = hashlib.sha256(payload_bytes_).hexdigest()

    receipt_row = {
        'export_batch_id': export_batch_id,
        'owner_account_id': str(account_id),
        'consent_version': CONSENT_VERSION,
        'sanitized_contract_version': SANITIZED_CONTRACT_VERSION,
        'lineage_token': lineage_token,
        'observation_count': len(observations),
        'manifest': receipt_manifest,
        'payload_sha256': sha_hex,
        'batch_state': 'CREATED',
        'superseded_by_batch_id': None,
        'created_at': created_at_dt,
    }

    params = (
        uuid.UUID(export_batch_id),
        account_id,
        CONSENT_VERSION,
        SANITIZED_CONTRACT_VERSION,
        lineage_token,
        len(observations),
        # Bind the manifest as a DICT, never pre-encoded JSON text: the app
        # pool's jsonb codec (encoder=json.dumps, main.py _init_conn)
        # re-encodes a str as a JSON string scalar -> CHECK violation.
        receipt_manifest,
        bytes.fromhex(sha_hex),
        'CREATED',
        None,
        created_at_dt,
    )
    await pool.execute(
        f'INSERT INTO v3_private.research_export_batch '
        f'({", ".join(_EXPORT_BATCH_COLUMNS)}) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)',
        *params,
    )
    return payload, sha_hex, receipt_row


async def supersede_lineage_batches(
    pool: asyncpg.Pool,
    account_id: uuid.UUID,
    lineage_token: str,
) -> dict:
    """Supersede-not-delete on consent withdrawal for one lineage token.

    Marks every CREATED batch of the account+token SUPERSEDED with
    ``superseded_by_batch_id`` pointing at a new ledger row whose payload is
    the ``kind: "supersede"`` batch (manifest ``{"withdrawal": true}``), and
    returns that supersede payload. When no CREATED batches exist, returns
    the supersede payload with an empty ``superseded_export_batch_ids`` list
    and writes no receipt. Observations and receipts are never deleted.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            # (created_at, export_batch_id) ordering MUST match the router's
            # superseded-id rebuild exactly: created_at is second-precision
            # in the payload's generated_at, so same-instant batches need the
            # uuid tiebreak or the supersede replay can reorder ids and 409.
            rows = await conn.fetch(
                'SELECT export_batch_id, lineage_token, created_at '
                'FROM v3_private.research_export_batch '
                "WHERE owner_account_id = $1 AND lineage_token = $2 AND batch_state = 'CREATED' "
                'ORDER BY created_at, export_batch_id',
                account_id,
                lineage_token,
            )
            superseded_ids = [str(row['export_batch_id']) for row in rows]

            created_at_dt, generated_at = _stamp_now()
            supersede_payload: dict[str, Any] = {
                'schema': EXPORT_SCHEMA,
                'schema_version': EXPORT_SCHEMA_VERSION,
                'kind': 'supersede',
                'lineage_token': lineage_token,
                'superseded_export_batch_ids': superseded_ids,
                'consent_version': CONSENT_VERSION,
                'generated_at': generated_at,
                'manifest': {'withdrawal': True},
            }

            # Nothing to supersede: return the (empty) supersede payload but
            # persist NO receipt row — an empty-id supersede batch is
            # schema-invalid (minItems: 1) and must never be stored/served.
            if not superseded_ids:
                return supersede_payload

            supersede_batch_id = uuid.uuid4()
            sha_hex = hashlib.sha256(payload_bytes(supersede_payload)).hexdigest()
            params = (
                supersede_batch_id,
                account_id,
                CONSENT_VERSION,
                SANITIZED_CONTRACT_VERSION,
                lineage_token,
                0,
                # Dict, not pre-encoded text — see build_export's note on the
                # app pool's jsonb codec (str would become a jsonb string).
                {'withdrawal': True},
                bytes.fromhex(sha_hex),
                'SUPERSEDED',
                None,
                created_at_dt,
            )
            await conn.execute(
                f'INSERT INTO v3_private.research_export_batch '
                f'({", ".join(_EXPORT_BATCH_COLUMNS)}) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)',
                *params,
            )
            await conn.execute(
                'UPDATE v3_private.research_export_batch '
                "SET batch_state = 'SUPERSEDED', superseded_by_batch_id = $3 "
                "WHERE owner_account_id = $1 AND lineage_token = $2 AND batch_state = 'CREATED'",
                account_id,
                lineage_token,
                supersede_batch_id,
            )
            return supersede_payload
