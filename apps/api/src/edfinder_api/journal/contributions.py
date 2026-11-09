"""Explicit account offers and operator review for journal galaxy enrichment.

Offering or reviewing a contribution never updates a canonical generation.
Research consent is deliberately not consulted or inferred by this module.
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone

from fastapi import HTTPException

from .commanders import verified_commanders
from shared_contracts.journal_galaxy_facts import AUDIENCE, NAMESPACE, FACT_KIND, POLICY, PURPOSE, normalize_scan
MAX_OFFER_EVENTS = 50_000


async def offer_import(pool, account_id: uuid.UUID, import_id: uuid.UUID, *, file_sha256: list[str]) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(hashtext($1))', f'journal-offer:{account_id}')
            imported = await conn.fetchrow(
                '''SELECT private_import_id FROM v3_private.private_import
                    WHERE private_import_id = $1 AND owner_account_id = $2
                      AND import_state = 'READY' FOR SHARE''', import_id, account_id,
            )
            if imported is None:
                raise HTTPException(404, 'Owned, ready journal import not found')
            owned = {row['commander_id'] for row in await verified_commanders(conn, account_id)}
            events = await conn.fetch(
                '''SELECT e.* FROM v3_private.journal_event e
                    WHERE e.owner_account_id = $2 AND EXISTS (
                        SELECT 1 FROM v3_private.journal_file_event occurrence
                        JOIN v3_private.journal_import_file f USING (journal_file_id, owner_account_id)
                        WHERE occurrence.journal_event_id = e.journal_event_id
                          AND occurrence.owner_account_id = e.owner_account_id
                          AND f.private_import_id = $1 AND f.content_sha256 = ANY($4::bytea[]))
                    ORDER BY e.journal_event_id LIMIT $3''', import_id, account_id, MAX_OFFER_EVENTS + 1,
                [bytes.fromhex(value) for value in file_sha256],
            )
            if len(events) > MAX_OFFER_EVENTS:
                raise HTTPException(422, 'Import exceeds the bounded contribution limit')
            facts, skipped = [], Counter()
            now = datetime.now(timezone.utc)
            for event in events:
                if event['owner_commander_id'] not in owned:
                    skipped['commander_not_verified'] += 1
                    continue
                try:
                    normalized = normalize_scan(dict(event), now=now)
                except (ValueError, OverflowError) as exc:
                    skipped[str(exc) if isinstance(exc, ValueError) else 'invalid_physical_value'] += 1
                    continue
                fact_id = uuid.uuid5(NAMESPACE, f'{POLICY}:{account_id}:{event["journal_event_id"]}')
                facts.append((fact_id, event, normalized))
            offered = 0
            for start in range(0, len(facts), 1000):
                batch = facts[start:start + 1000]
                # Private evidence/receipts only; FK enforcement must stay on.
                await conn.execute(
                    '''INSERT INTO v3_private.private_fact
                       (private_fact_id, private_import_id, owner_account_id, source_run_id,
                        fact_kind, fact_key, fact_payload, observed_at, journal_event_id)
                       SELECT * FROM unnest($1::uuid[], $2::uuid[], $3::uuid[], $4::uuid[],
                           $5::text[], $6::jsonb[], $7::jsonb[], $8::timestamptz[], $9::uuid[])
                       ON CONFLICT (private_fact_id) DO NOTHING''',
                    [row[0] for row in batch], [row[1]['private_import_id'] for row in batch], [account_id] * len(batch),
                    [row[1]['source_run_id'] for row in batch], [FACT_KIND] * len(batch),
                    [{'SystemAddress': row[2]['system_id64'], 'BodyID': row[2]['frontier_body_id']} for row in batch],
                    [row[2] for row in batch], [row[1]['event_timestamp'] for row in batch],
                    [row[1]['journal_event_id'] for row in batch],
                )
                ids = await conn.fetch(
                    '''INSERT INTO v3_private.contribution_receipt
                       (contribution_id, private_fact_id, contributing_account_id, audience_code,
                        contribution_purpose, site_publication_allowed, api_redistribution_allowed,
                        bulk_redistribution_allowed, attribution_mode, sharing_policy_version,
                        contribution_state)
                       SELECT id, fact, $3, $4, $5, true, true, false, 'NONE', $6, 'OFFERED'
                         FROM unnest($1::uuid[], $2::uuid[]) AS input(id, fact)
                       ON CONFLICT (contribution_id) DO NOTHING RETURNING contribution_id''',
                    [uuid.uuid5(NAMESPACE, f'offer:{row[0]}') for row in batch],
                    [row[0] for row in batch], account_id, AUDIENCE, PURPOSE, POLICY,
                )
                offered += len(ids)
            return {'new_offers': offered, 'already_offered': len(facts) - offered, 'skipped': dict(skipped)}


async def list_contributions(pool, *, account_id: uuid.UUID | None, offset: int, limit: int) -> list[dict]:
    rows = await pool.fetch(
        '''SELECT cr.contribution_id, cr.contribution_state, cr.offered_at, cr.decided_at,
                  pf.fact_payload AS observation,
                  EXISTS (SELECT 1 FROM v3_source.canonical_evidence_group eg
                           WHERE eg.contribution_id = cr.contribution_id) AS used_in_generation
             FROM v3_private.contribution_receipt cr
             JOIN v3_private.private_fact pf ON pf.private_fact_id = cr.private_fact_id
            WHERE cr.audience_code = $1 AND cr.sharing_policy_version = $2
              AND ($3::uuid IS NULL OR cr.contributing_account_id = $3)
            ORDER BY cr.offered_at DESC, cr.contribution_id DESC OFFSET $4 LIMIT $5''',
        AUDIENCE, POLICY, account_id, offset, limit,
    )
    return [dict(row) for row in rows]


async def review(pool, *, ids: list[uuid.UUID], eligible: bool, actor_id: uuid.UUID, reason: str) -> int:
    from edfinder_api.auth import write_security_audit_event

    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                '''SELECT cr.contribution_id FROM v3_private.contribution_receipt cr
                     JOIN v3_private.private_fact pf USING (private_fact_id)
                     JOIN v3_private.private_import pi ON pi.private_import_id = pf.private_import_id
                     JOIN v3_private.journal_event e ON e.journal_event_id = pf.journal_event_id
                     JOIN v3_identity.account_commander_access a
                       ON a.account_id = e.owner_account_id AND a.commander_id = e.owner_commander_id
                    WHERE cr.contribution_id = ANY($1::uuid[]) AND cr.audience_code = $2
                      AND cr.sharing_policy_version = $3 AND cr.contribution_state = 'OFFERED'
                      AND pf.withdrawn_at IS NULL AND pi.import_state = 'READY'
                      AND a.revoked_at IS NULL AND a.access_role = 'OWNER'
                    ORDER BY cr.contribution_id FOR UPDATE OF cr FOR SHARE OF a, pi, pf''',
                ids, AUDIENCE, POLICY,
            )
            accepted_ids = [row['contribution_id'] for row in rows]
            await conn.execute(
                '''UPDATE v3_private.contribution_receipt
                      SET contribution_state = $2, decided_at = clock_timestamp()
                    WHERE contribution_id = ANY($1::uuid[])''',
                accepted_ids, 'ELIGIBLE' if eligible else 'REJECTED',
            )
            await write_security_audit_event(
                conn, event_type='journal.contribution.reviewed', succeeded=True,
                account_id=actor_id, metadata={'count': len(accepted_ids), 'eligible': eligible, 'reason': reason},
            )
            return len(accepted_ids)


async def withdraw(pool, account_id: uuid.UUID, contribution_id: uuid.UUID) -> bool:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                '''SELECT contribution_state FROM v3_private.contribution_receipt
                    WHERE contribution_id = $1 AND contributing_account_id = $2
                      AND audience_code = $3 AND sharing_policy_version = $4 FOR UPDATE''',
                contribution_id, account_id, AUDIENCE, POLICY,
            )
            if row is None:
                raise HTTPException(404, 'Contribution not found')
            if row['contribution_state'] == 'WITHDRAWN':
                return False
            await conn.execute(
                '''UPDATE v3_private.contribution_receipt
                      SET contribution_state = 'WITHDRAWN', withdrawn_at = transaction_timestamp()
                    WHERE contribution_id = $1''', contribution_id,
            )
            await conn.execute(
                '''INSERT INTO v3_private.contribution_withdrawal
                   (contribution_withdrawal_id, contribution_id, requested_by_account_id,
                    requested_at, withdrawal_reason)
                   VALUES ($1, $2, $3, transaction_timestamp(), 'Contributor withdrew galaxy sharing')''',
                uuid.uuid5(NAMESPACE, f'withdraw:{contribution_id}'), contribution_id, account_id,
            )
            # Published generations remain immutable. This durable request is
            # consumed by the separately operated replacement-generation lane.
            await conn.execute(
                '''INSERT INTO v3_async.outbox_message
                   (outbox_message_id, producer_scope, idempotency_key, aggregate_type,
                    aggregate_id, event_type, contract_version, payload, occurred_at)
                   VALUES ($1, 'journal-contributions', $2, 'contribution', $2,
                           'journal.contribution.withdrawn', '1', $3, transaction_timestamp())
                   ON CONFLICT DO NOTHING''',
                uuid.uuid5(NAMESPACE, f'withdraw-outbox:{contribution_id}'), str(contribution_id),
                {'contribution_id': str(contribution_id), 'requires_replacement_generation': True},
            )
            return True
