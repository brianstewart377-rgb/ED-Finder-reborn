"""V3 versioned research-consent ledger (Task 4, decision doc R7).

Explicit, revocable, versioned research-contribution consent for the
ED-Finder -> EDRE evidence pipeline. Decisions are append-only per
(account, consent_version, decision) in ``v3_private.research_consent``;
effective state is the row with the greatest ``decided_at``. A WITHDRAW row
supersedes the matching GRANT row by ``decided_at``; decision history is
preserved (a GRANT and a later WITHDRAW row coexist).

Consent vocabulary (frozen)::

    CONSENT_VERSION = "1.0"
    SANITIZED_CONTRACT_VERSION = "1.0.0"
    PURPOSE = "CRE_RESEARCH_EVIDENCE"
    AUDIENCE = "CRE"

Purpose/audience values are frozen v1.0 consent vocabulary; the legacy CRE
spelling is retained for compatibility after the CRE→EDRE rename.

State machine: NONE -> GRANT (ok) -> duplicate GRANT (ConsentStateError,
routers map to 409) -> WITHDRAW (ok) -> re-GRANT (refused) -> duplicate
WITHDRAW (refused). Consent is never inferred from journal upload or
account creation — upload must never be conditional on consent, and this
module is deliberately not referenced from the journal import path.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg

CONSENT_VERSION = '1.0'
SANITIZED_CONTRACT_VERSION = '1.0.0'
PURPOSE = 'CRE_RESEARCH_EVIDENCE'
AUDIENCE = 'CRE'

_VALID_DECISIONS = frozenset({'GRANT', 'WITHDRAW'})


class ConsentStateError(Exception):
    """Raised when a consent decision cannot be recorded under the
    append-only decision ledger (duplicate GRANT, or WITHDRAW without an
    effective GRANT). Routers map this to HTTP 409 Conflict."""

    status_code = 409

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# Documented INSERT column order (mirrored by the unit-test fake pool).
_CONSENT_COLUMNS = (
    'research_consent_id', 'owner_account_id', 'consent_version',
    'sanitized_contract_version', 'purpose', 'audience_code', 'decision',
    'decided_at', 'withdrawn_at',
)

_EFFECTIVE_CONSENT_SQL = """
SELECT consent_version, sanitized_contract_version, purpose, audience_code,
       decision, decided_at
FROM v3_private.research_consent
WHERE owner_account_id = $1
ORDER BY decided_at DESC, research_consent_id DESC
LIMIT 1
"""

# The decision ledger is append-only per (account, consent_version,
# decision): a GRANT row can exist at most once, even after a WITHDRAW.
# This check refuses any new GRANT while a GRANT row exists for the
# version — an active grant AND a withdrawn-but-recorded grant alike
# (the DB UNIQUE would reject a second GRANT row either way).
_GRANT_EXISTS_SQL = """
SELECT 1
FROM v3_private.research_consent
WHERE owner_account_id = $1
  AND consent_version = $2
  AND decision = 'GRANT'
LIMIT 1
"""

_INSERT_CONSENT_SQL = (
    'INSERT INTO v3_private.research_consent '
    f'({", ".join(_CONSENT_COLUMNS)}) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)'
)


async def effective_consent(pool: asyncpg.Pool, account_id: uuid.UUID) -> dict | None:
    """The account's effective consent state, or None when no decision row
    exists. The latest ``decided_at`` row wins (R7); ``decided_at`` is
    returned as the DB datetime (tz-aware UTC)."""
    rows = await pool.fetch(_EFFECTIVE_CONSENT_SQL, account_id)
    if not rows:
        return None
    row = rows[0]
    return {
        'decision': row['decision'],
        'consent_version': row['consent_version'],
        'sanitized_contract_version': row['sanitized_contract_version'],
        'purpose': row['purpose'],
        'audience_code': row['audience_code'],
        'decided_at': row['decided_at'],
    }


async def record_consent(
    pool: asyncpg.Pool,
    account_id: uuid.UUID,
    *,
    decision: str,
) -> dict:
    """Record one append-only consent decision and return the effective state.

    Raises ``ConsentStateError`` (router -> 409) when the transition is not
    permitted: GRANT while a GRANT decision for this consent version is
    already recorded (effective or withdrawn — the ledger is append-only),
    or WITHDRAW with no effective GRANT. A WITHDRAW row carries
    ``withdrawn_at`` (DB CHECK: (decision = 'WITHDRAW') =
    (withdrawn_at IS NOT NULL)). Raises ValueError for any decision other
    than GRANT/WITHDRAW.
    """
    if decision not in _VALID_DECISIONS:
        raise ValueError(f'decision must be one of {sorted(_VALID_DECISIONS)}, got {decision!r}')

    current = await effective_consent(pool, account_id)
    if decision == 'GRANT':
        grant_rows = await pool.fetch(_GRANT_EXISTS_SQL, account_id, CONSENT_VERSION)
        if grant_rows:
            raise ConsentStateError(
                'A GRANT decision for consent version '
                f'{CONSENT_VERSION!r} is already recorded for this account; '
                'decisions are append-only per version'
            )
    else:  # WITHDRAW
        if current is None or current['decision'] != 'GRANT':
            raise ConsentStateError(
                'WITHDRAW requires an effective GRANT decision; '
                f'current effective decision: {current["decision"] if current else "NONE"}'
            )

    now = datetime.now(timezone.utc)
    params: tuple[Any, ...] = (
        uuid.uuid4(),
        account_id,
        CONSENT_VERSION,
        SANITIZED_CONTRACT_VERSION,
        PURPOSE,
        AUDIENCE,
        decision,
        now,
        now if decision == 'WITHDRAW' else None,
    )
    try:
        await pool.execute(_INSERT_CONSENT_SQL, *params)
    except asyncpg.exceptions.UniqueViolationError as exc:
        # Race guard: another request recorded the same (account, version,
        # decision) between our effective check and this insert.
        raise ConsentStateError(
            f'Consent decision {decision!r} for consent version '
            f'{CONSENT_VERSION!r} was already recorded for this account'
        ) from exc

    effective = await effective_consent(pool, account_id)
    assert effective is not None  # the row we just inserted
    return effective
