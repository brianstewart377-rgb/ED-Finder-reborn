"""Verified Frontier customer/FID association, separate from parent login identity.

The auth token's actual customer_id maps to journal F<customer_id>. Never use
the parent account subject, a display name, or a client-uploaded FID as proof.
Reference: EDCD/EDMarketConnector companion.py Auth.refresh customer_id check.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException

PROVIDER = 'frontier'
ISSUER = 'https://auth.frontierstore.net'
FID_PATTERN = re.compile(r'F[1-9][0-9]{0,19}\Z')


def fid_from_customer_id(value: object) -> str | None:
    # Unknown formats leave an ordinary account login usable but unassociated.
    # bool, float, signed numbers and empty/parent fallbacks are never accepted.
    if type(value) not in (int, str):
        return None
    result = f'F{value}'
    return result if FID_PATTERN.fullmatch(result) else None


async def associate_verified_commander(
    conn: Any,
    *,
    account_id: uuid.UUID,
    issuer: str,
    fid: str,
    verified_at: datetime,
    commander_name: str | None = None,
) -> uuid.UUID:
    """Call only inside the verified OAuth transaction; never from an upload.

    Different customers sharing a parent login get different commanders. A
    conflicting owner fails the transaction instead of transferring access or
    attaching the new FID to whichever commander happened to be selected first.

    ``commander_name`` is the real in-game CMDR name (from Frontier CAPI
    ``/profile``, fail-open). A non-empty name is stored on insert and
    replaces an existing placeholder; ``None``/empty preserves the current
    placeholder behaviour so a fail-open login never erases a real name.
    """
    if issuer != ISSUER or not FID_PATTERN.fullmatch(fid):
        raise ValueError('Invalid verified Frontier journal identity')
    clean_name = commander_name.strip()[:128] if commander_name and commander_name.strip() else None
    placeholder = f'Verified commander {fid}'
    stored_name = clean_name or placeholder
    await conn.execute(
        'SELECT pg_advisory_xact_lock(hashtext($1))', f'frontier-journal:{fid}',
    )
    row = await conn.fetchrow(
        '''SELECT ce.commander_id, c.commander_state, c.commander_name,
                  c.journal_name_observed_at
             FROM v3_identity.commander_external_identity ce
             JOIN v3_identity.commander c USING (commander_id)
            WHERE ce.provider = $1 AND ce.issuer = $2 AND ce.subject = $3
            FOR UPDATE OF ce, c''',
        PROVIDER, issuer, fid,
    )
    if row is None:
        commander_id = uuid.uuid4()
        await conn.execute(
            '''INSERT INTO v3_identity.commander (commander_id, commander_name)
               VALUES ($1, $2)''', commander_id, stored_name,
        )
        await conn.execute(
            '''INSERT INTO v3_identity.commander_external_identity
               (commander_external_identity_id, commander_id, provider, issuer,
                subject, verified_at) VALUES ($1, $2, $3, $4, $5, $6)''',
            uuid.uuid4(), commander_id, PROVIDER, issuer, fid, verified_at,
        )
    else:
        commander_id = row['commander_id']
        owner = await conn.fetchval(
            '''SELECT account_id FROM v3_identity.account_commander_access
                WHERE commander_id = $1 AND access_role = 'OWNER'
                  AND revoked_at IS NULL FOR UPDATE''', commander_id,
        )
        if row['commander_state'] != 'ACTIVE' or (
            owner is not None and owner != account_id
        ):
            raise HTTPException(409, 'Commander association requires account review')
        await conn.execute(
            '''UPDATE v3_identity.commander_external_identity SET verified_at = $4
                WHERE provider = $1 AND issuer = $2 AND subject = $3''',
            PROVIDER, issuer, fid, verified_at,
        )
        existing = row['commander_name']
        is_placeholder = (
            not existing
            or existing == 'Verified commander'
            or existing.startswith('Verified commander ')
        )
        if clean_name and existing != clean_name:
            await conn.execute(
                '''UPDATE v3_identity.commander
                   SET commander_name = $2, updated_at = transaction_timestamp()
                 WHERE commander_id = $1''',
                commander_id, clean_name,
            )
        elif is_placeholder and existing != placeholder:
            await conn.execute(
                '''UPDATE v3_identity.commander
                   SET commander_name = $2, updated_at = transaction_timestamp()
                 WHERE commander_id = $1''',
                commander_id, placeholder,
            )
    await conn.execute(
        '''INSERT INTO v3_identity.account_commander_access
           (account_id, commander_id, access_role, granted_at)
           VALUES ($1, $2, 'OWNER', $3)
           ON CONFLICT (account_id, commander_id) DO UPDATE
           SET access_role = 'OWNER', revoked_at = NULL, granted_at = EXCLUDED.granted_at''',
        account_id, commander_id, verified_at,
    )
    return commander_id


async def verified_commanders(conn: Any, account_id: uuid.UUID) -> list[dict]:
    rows = await conn.fetch(
        '''SELECT c.commander_id, c.commander_name, ce.subject AS journal_fid,
                  ce.verified_at
             FROM v3_identity.account_commander_access a
             JOIN v3_identity.commander c USING (commander_id)
             JOIN v3_identity.commander_external_identity ce USING (commander_id)
             JOIN v3_identity.account owner ON owner.account_id = a.account_id
            WHERE a.account_id = $1 AND a.revoked_at IS NULL
              AND a.access_role = 'OWNER' AND c.commander_state = 'ACTIVE'
              AND owner.account_state = 'ACTIVE'
              AND ce.provider = $2 AND ce.issuer = $3 AND ce.verified_at IS NOT NULL
            ORDER BY ce.verified_at, c.commander_id''',
        account_id, PROVIDER, ISSUER,
    )
    return [dict(row) for row in rows if FID_PATTERN.fullmatch(row['journal_fid'])]
