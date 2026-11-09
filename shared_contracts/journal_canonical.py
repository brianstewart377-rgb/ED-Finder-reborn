"""Reviewed journal facts -> one unpublished canonical build.

This module is imported by the operator/build lane, never by request handlers.
It enriches an indexed, validated candidate in an atomic validation phase. It
never mutates a published generation or starts a Ratings/Search rebuild.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .journal_galaxy_facts import AUDIENCE, FACT_KIND, NAMESPACE, CANONICAL_FIELDS, POLICY, exact_id, reconcile_fields

_SCHEMA = re.compile(r'v3_gen_[a-z][a-z0-9_]{0,30}\Z')


def _encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _register_source(cur, generation_id: uuid.UUID, observations: list[dict]) -> tuple[int, uuid.UUID, uuid.UUID]:
    data = _encoded(observations)
    digest = hashlib.sha256(data).digest()
    code_digest = hashlib.sha256(Path(__file__).read_bytes()).digest()
    normalizer_digest = hashlib.sha256(Path(__file__).with_name('journal_galaxy_facts.py').read_bytes()).digest()
    identity = f'{POLICY}:{generation_id}:{digest.hex()}:{code_digest.hex()}:{normalizer_digest.hex()}'
    source_run_id = uuid.uuid5(NAMESPACE, identity)
    artifact_id = uuid.uuid5(NAMESPACE, f'galaxy-artifact:{digest.hex()}')
    cur.execute('''INSERT INTO v3_source.source (source_code, display_name, authority_class)
                   VALUES ('reviewed_journal_physical', 'Reviewed journal physical facts', 'OPERATOR_ADJUDICATION')
                   ON CONFLICT (source_code) DO NOTHING''')
    cur.execute("SELECT source_id, enabled FROM v3_source.source WHERE source_code = 'reviewed_journal_physical'")
    source = cur.fetchone()
    if not source['enabled']:
        raise ValueError('Journal contribution source is disabled')
    source_id = source['source_id']
    cur.execute('''INSERT INTO v3_source.source_rights_policy
                   (source_id, policy_version, rights_class, retention_class, distribution_allowed, effective_at)
                   VALUES (%s, %s, 'CANONICAL_ELIGIBLE', 'CONTRIBUTION_DECISION_LEDGER', true, transaction_timestamp())
                   ON CONFLICT (source_id, policy_version) DO NOTHING''', (source_id, POLICY))
    cur.execute('''SELECT rights_policy_id FROM v3_source.source_rights_policy
                    WHERE source_id = %s AND policy_version = %s AND retired_at IS NULL
                      AND rights_class = 'CANONICAL_ELIGIBLE' AND distribution_allowed''', (source_id, POLICY))
    rights = cur.fetchone()
    if rights is None:
        raise ValueError('Journal source policy is not eligible')
    rights_id = rights['rights_policy_id']
    cur.execute('''INSERT INTO v3_source.source_artifact
                   (artifact_id, source_id, rights_policy_id, artifact_kind, content_sha256, size_bytes,
                    media_type, retrieved_at, retention_class)
                   VALUES (%s,%s,%s,'SANITIZED_JOURNAL_PHYSICAL',%s,%s,'application/json',
                           transaction_timestamp(),'CONTRIBUTION_DECISION_LEDGER')
                   ON CONFLICT (source_id, content_sha256) DO NOTHING''',
                (artifact_id, source_id, rights_id, digest, len(data)))
    cur.execute('''INSERT INTO v3_source.journal_physical_snapshot(artifact_id, sanitized_payload)
                   VALUES (%s,%s) ON CONFLICT (artifact_id) DO NOTHING''', (artifact_id, data))
    cur.execute('''INSERT INTO v3_source.source_run
                   (source_run_id, source_id, rights_policy_id, artifact_id, acquisition_kind,
                    trust_zone, run_state, idempotency_key, coverage_domain, scope_contract,
                    is_complete_snapshot, started_at, completed_at, importer_version,
                    importer_code_sha256, importer_config_sha256, normalizer_version, normalizer_sha256)
                   VALUES (%s,%s,%s,%s,'OPERATOR_ADJUDICATION','CANONICAL','SUCCEEDED',%s,
                           'body_physical',%s,false,transaction_timestamp(),transaction_timestamp(),
                           %s,%s,%s,%s,%s) ON CONFLICT (source_run_id) DO NOTHING''',
                (source_run_id, source_id, rights_id, artifact_id, identity,
                 Jsonb({'policy': POLICY, 'observation_count': len(observations)}),
                 POLICY, code_digest, hashlib.sha256(POLICY.encode()).digest(), POLICY, normalizer_digest))
    cur.execute('''INSERT INTO v3_meta.canonical_generation_input
                   (generation_id, input_ordinal, source_id, source_run_id, artifact_id, input_role)
                   SELECT %s, COALESCE(max(input_ordinal), -1) + 1, %s, %s, %s, 'REVIEWED_JOURNAL_PHYSICAL'
                     FROM v3_meta.canonical_generation_input WHERE generation_id = %s
                   ON CONFLICT (generation_id, source_run_id) DO NOTHING''',
                (generation_id, source_id, source_run_id, artifact_id, generation_id))
    return source_id, source_run_id, artifact_id


def reconcile_generation(conn, *, generation_id: uuid.UUID, contribution_ids: list[uuid.UUID],
                         apply: bool = False, expected_manifest_sha256: str | None = None,
                         expected_state_sha256: str | None = None) -> dict:
    if not 1 <= len(contribution_ids) <= 500 or len(set(contribution_ids)) != len(contribution_ids):
        raise ValueError('Select 1–500 distinct contribution IDs')
    if apply and not re.fullmatch(r'[0-9a-f]{64}', expected_manifest_sha256 or ''):
        raise ValueError('Applying requires the expected generation manifest SHA-256')
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT g.relation_schema, g.lifecycle_state, g.published_at,
                              g.validation_receipt, g.validation_completed_at, g.created_at,
                              encode(g.manifest_sha256,'hex') AS manifest,
                              sr.run_state AS build_run_state,
                              EXISTS (SELECT 1 FROM v3_meta.current_canonical_generation c
                                       WHERE c.generation_id = g.generation_id) AS is_current
                         FROM v3_meta.canonical_generation g
                         JOIN v3_source.source_run sr ON sr.source_run_id = g.build_source_run_id
                        WHERE g.generation_id = %s'''
                    + (' FOR UPDATE OF g' if apply else ''), (generation_id,))
        generation = cur.fetchone()
        if (generation is None or generation['lifecycle_state'] != 'READY'
                or generation['published_at'] is not None or generation['is_current']
                or generation['validation_completed_at'] is None or generation['validation_receipt'] is None):
            raise ValueError('Target must be a validated, never-published READY generation')
        if generation['build_run_state'] != 'SUCCEEDED':
            raise ValueError('The canonical source build must finish before journal reconciliation')
        schema = generation['relation_schema']
        if not _SCHEMA.fullmatch(schema):
            raise ValueError('Invalid canonical schema')
        if apply and generation['manifest'] != expected_manifest_sha256:
            raise ValueError('Generation manifest changed; review a fresh plan')
        cur.execute("SELECT to_regclass('v3_meta.derived_generation') AS relation")
        if cur.fetchone()['relation'] is not None:
            cur.execute('SELECT 1 FROM v3_meta.derived_generation WHERE canonical_generation_id = %s LIMIT 1', (generation_id,))
            if cur.fetchone():
                raise ValueError('Target already has a derived build')
        cur.execute('''SELECT count(*) AS count FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
                        JOIN pg_namespace n ON n.oid=c.relnamespace
                       WHERE n.nspname=%s AND c.relname IN ('bodies_pk','bodies_frontier_body_uidx')
                         AND i.indisvalid AND i.indisready AND i.indisunique''', (schema,))
        if cur.fetchone()['count'] != 2:
            raise ValueError('Canonical identity indexes must be built before reconciliation')
        if apply:
            cur.execute(sql.SQL('LOCK TABLE {}.bodies IN SHARE ROW EXCLUSIVE MODE').format(sql.Identifier(schema)))
            cur.execute("UPDATE v3_meta.canonical_generation SET lifecycle_state='VALIDATING' WHERE generation_id=%s", (generation_id,))
        cur.execute('''SELECT cr.contribution_id, pf.fact_payload, pf.private_fact_id
                         FROM v3_private.contribution_receipt cr
                         JOIN v3_private.private_fact pf USING (private_fact_id)
                         JOIN v3_private.private_import pi ON pi.private_import_id = pf.private_import_id
                         JOIN v3_private.journal_event e ON e.journal_event_id = pf.journal_event_id
                         JOIN v3_identity.account_commander_access a
                           ON a.account_id = e.owner_account_id AND a.commander_id = e.owner_commander_id
                         JOIN v3_identity.account owner ON owner.account_id = a.account_id
                         JOIN v3_identity.commander commander ON commander.commander_id = a.commander_id
                        WHERE cr.contribution_id = ANY(%s) AND cr.audience_code = %s
                          AND cr.sharing_policy_version = %s AND cr.contribution_state = 'ELIGIBLE'
                          AND pf.fact_kind = %s
                          AND cr.site_publication_allowed AND cr.api_redistribution_allowed
                          AND pf.withdrawn_at IS NULL AND pi.import_state = 'READY'
                          AND a.revoked_at IS NULL AND a.access_role = 'OWNER' AND owner.account_state = 'ACTIVE'
                          AND commander.commander_state = 'ACTIVE'
                          AND EXISTS (SELECT 1 FROM v3_identity.commander_external_identity ce
                                       WHERE ce.commander_id=a.commander_id AND ce.provider='frontier'
                                         AND ce.issuer='https://auth.frontierstore.net' AND ce.verified_at IS NOT NULL
                                         AND ce.subject ~ '^F[1-9][0-9]{0,19}$')
                        ORDER BY cr.contribution_id'''
                    + (' FOR SHARE OF cr, pf, pi, a, owner, commander' if apply else ''),
                    (contribution_ids, AUDIENCE, POLICY, FACT_KIND))
        candidates = cur.fetchall()
        # Inspect all admitted peers before applying any selected observation.
        # This also sees ties outside the current 500-record batch. Later reviews
        # enter only by explicit selection (or prior use in this generation).
        cur.execute('''WITH selected AS (
                           SELECT * FROM jsonb_to_recordset(%s) AS s(contribution_id uuid, fact_payload jsonb)
                       )
                       SELECT s.contribution_id, fields.key AS field
                         FROM selected s
                         JOIN v3_private.eligible_journal_galaxy_contribution peer
                           ON peer.fact_payload->>'system_id64' = s.fact_payload->>'system_id64'
                          AND peer.fact_payload->>'frontier_body_id' = s.fact_payload->>'frontier_body_id'
                          AND peer.fact_payload->>'observed_at' = s.fact_payload->>'observed_at'
                         CROSS JOIN LATERAL jsonb_each(peer.fact_payload->'fields') fields
                        WHERE peer.decided_at <= %s OR peer.contribution_id = ANY(%s)
                           OR EXISTS (SELECT 1 FROM v3_source.canonical_evidence_group eg
                                       WHERE eg.generation_id = %s AND eg.contribution_id = peer.contribution_id)
                        GROUP BY s.contribution_id, fields.key
                       HAVING count(DISTINCT fields.value) > 1
                        ORDER BY s.contribution_id, fields.key''',
                    (Jsonb([{'contribution_id': str(row['contribution_id']), 'fact_payload': row['fact_payload']}
                            for row in candidates]), generation['created_at'], contribution_ids, generation_id))
        conflicts = {}
        for row in cur.fetchall():
            conflicts.setdefault(row['contribution_id'], set()).add(row['field'])
        state_sha256 = hashlib.sha256(_encoded({
            'generation_id': str(generation_id), 'manifest': generation['manifest'],
            'validation_receipt': generation['validation_receipt'],
            'validation_completed_at': generation['validation_completed_at'].isoformat(),
            'contribution_ids': sorted(str(value) for value in contribution_ids),
            'reconciliation_code': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'normalizer_code': hashlib.sha256(Path(__file__).with_name('journal_galaxy_facts.py').read_bytes()).hexdigest(),
            'eligible_candidates': [
                {'contribution_id': str(row['contribution_id']),
                 'private_fact_id': str(row['private_fact_id']), 'payload': row['fact_payload'],
                 'conflicting_fields': sorted(conflicts.get(row['contribution_id'], ()))}
                for row in candidates
            ],
        })).hexdigest()
        if apply and state_sha256 != expected_state_sha256:
            raise ValueError('Reconciliation state or selection changed; review a fresh plan')
        # No private account/commander identifiers enter the public artifact.
        source = _register_source(cur, generation_id, [row['fact_payload'] for row in candidates]) if apply and candidates else None
        results = []
        planned_bodies = {}
        for candidate in candidates:
            cid = candidate['contribution_id']
            evidence_id = uuid.uuid5(NAMESPACE, f'canonical:{generation_id}:{cid}')
            cur.execute('SELECT 1 FROM v3_source.canonical_evidence_group WHERE canonical_evidence_group_id = %s', (evidence_id,))
            if cur.fetchone():
                results.append({'contribution_id': str(cid), 'status': 'ALREADY_RECONCILED'})
                continue
            fact = candidate['fact_payload']
            key = {'system_id64': str(exact_id(fact['system_id64'])),
                   'frontier_body_id': str(exact_id(fact['frontier_body_id'], zero=True))}
            cur.execute(sql.SQL('SELECT * FROM {}.bodies WHERE system_id64 = %s AND frontier_body_id = %s LIMIT 2')
                        .format(sql.Identifier(schema)), (int(key['system_id64']), int(key['frontier_body_id'])))
            bodies = cur.fetchall()
            if len(bodies) != 1 or bodies[0]['lifecycle_state'] != 'ACTIVE':
                results.append({'contribution_id': str(cid), 'status': 'UNRESOLVED_BODY'})
                continue
            body = bodies[0]
            cur.execute('''SELECT conflict_detail FROM v3_source.canonical_evidence_group
                            WHERE generation_id = %s AND entity_kind = 'body'
                              AND entity_key = %s AND field_group_code = 'JOURNAL_PHYSICAL' ''',
                        (generation_id, Jsonb(key)))
            field_times = {}
            for previous in cur.fetchall():
                detail = previous['conflict_detail']
                if not detail:
                    continue
                observed = datetime.fromisoformat(detail['observed_at'])
                for field in detail['accepted_fields']:
                    timestamp = datetime.fromisoformat(detail.get('field_observed_at', {}).get(field, observed.isoformat()))
                    field_times[field] = max(field_times.get(field, timestamp), timestamp)
                    if (field in conflicts.get(cid, ()) and observed == datetime.fromisoformat(fact['observed_at'])
                            and detail['decisions'][field] in ('FILL_MISSING', 'NEWER_OBSERVATION')):
                        # A manually admitted late tie must not cement a value
                        # chosen before its peer became eligible. Rebuild from
                        # the catalogue instead of guessing a rollback value.
                        raise ValueError('New equal-time conflict with applied evidence; use a fresh generation')
            plan_key = (key['system_id64'], key['frontier_body_id'])
            if not apply and plan_key in planned_bodies:
                body, field_times = planned_bodies[plan_key]
            changes, decisions = reconcile_fields(dict(body), fact, field_times,
                                                  conflicting_fields=frozenset(conflicts.get(cid, ())))
            result = {'contribution_id': str(cid), 'status': 'CHANGES_PLANNED' if changes else 'NO_CHANGE',
                      'decisions': decisions, 'changes': changes}
            if not apply:
                # Model earlier decisions in this bounded batch so a dry run
                # agrees with apply when several observations concern one body.
                next_times = dict(field_times)
                for field, decision in decisions.items():
                    if decision == 'PRESERVE_CONFLICT':
                        continue
                    observed = datetime.fromisoformat(fact['observed_at'])
                    prior = field_times.get(field, body.get('source_updated_at'))
                    next_times[field] = max(observed, prior) if decision == 'UNCHANGED' and prior else observed
                planned_bodies[plan_key] = ({**body, **changes}, next_times)
            if apply:
                if changes:
                    # Bounded writes into an unpublished build, with identity and
                    # provenance FKs required. Replica trigger suppression is unsafe.
                    if not set(changes) <= CANONICAL_FIELDS:
                        raise ValueError('Invalid canonical update field')
                    assignments = sql.SQL(', ').join(sql.SQL('{} = %s').format(sql.Identifier(field)) for field in changes)
                    cur.execute(sql.SQL('UPDATE {}.bodies SET {} WHERE body_pk = %s').format(sql.Identifier(schema), assignments),
                                (*changes.values(), body['body_pk']))
                    if cur.rowcount != 1:
                        raise ValueError('Canonical body identity changed during reconciliation')
                assert source is not None
                accepted_fields = [field for field, decision in decisions.items() if decision != 'PRESERVE_CONFLICT']
                field_observed_at = {}
                for field in accepted_fields:
                    observed = datetime.fromisoformat(fact['observed_at'])
                    prior = field_times.get(field, body.get('source_updated_at'))
                    if decisions[field] == 'UNCHANGED' and prior is not None:
                        observed = max(observed, prior)
                    field_observed_at[field] = observed.isoformat()
                cur.execute('''INSERT INTO v3_source.canonical_evidence_group
                               (canonical_evidence_group_id, generation_id, entity_kind, entity_key,
                                field_group_code, source_id, source_run_id, artifact_id, contribution_id,
                                behavior_class, reconciliation_policy_version, decision_code, value_sha256,
                                conflict_detail, decided_at)
                               VALUES (%s,%s,'body',%s,'JOURNAL_PHYSICAL',%s,%s,%s,%s,'STABLE_PHYSICAL',
                                       %s,%s,%s,%s,transaction_timestamp())''',
                            (evidence_id, generation_id, Jsonb(key), *source, cid, POLICY,
                             'APPLIED' if changes else 'PRESERVED', hashlib.sha256(_encoded(fact)).digest(),
                             Jsonb({'observed_at': fact['observed_at'], 'accepted_fields': accepted_fields,
                                    'field_observed_at': field_observed_at,
                                    'decisions': decisions, 'baseline_source_run_id': str(body['source_run_id'])})))
                result['status'] = 'APPLIED_TO_BUILD' if changes else 'PRESERVED'
            results.append(result)
        eligible_ids = {row['contribution_id'] for row in candidates}
        results.extend({'contribution_id': str(cid), 'status': 'NOT_ELIGIBLE'}
                       for cid in contribution_ids if cid not in eligible_ids)
        if apply:
            # Baseline indexes, constraints and structural validation remain in
            # force. Only allowlisted scalar values can change in this phase.
            # A failed statement, provenance write or final consent check rolls
            # back both values and this receipt to the original READY candidate.
            receipt = dict(generation['validation_receipt'])
            prior = receipt.get('journal_enrichment', {})
            receipt['journal_enrichment'] = {
                'policy': POLICY, 'status': 'VERIFIED',
                'batches': int(prior.get('batches', 0)) + 1,
                'decision_chain_sha256': hashlib.sha256(_encoded({
                    'prior': prior.get('decision_chain_sha256'), 'results': results,
                })).hexdigest(),
                'identity_indexes_verified': True, 'constraints_enforced': True,
            }
            cur.execute('''UPDATE v3_meta.canonical_generation SET lifecycle_state='READY',
                           validation_completed_at=transaction_timestamp(), validation_receipt=%s
                           WHERE generation_id=%s''', (Jsonb(receipt), generation_id))
        return {'generation_id': str(generation_id), 'manifest_sha256': generation['manifest'],
                'reconciliation_state_sha256': state_sha256,
                'applied': apply, 'published': False, 'results': results}


def reconcile_all_eligible(conn, *, generation_id: uuid.UUID) -> dict:
    """Carry reviewed observations forward on every future Spansh build.

    Freeze admission at build creation; later reviews wait for the next build.
    Read and reconcile in keyset batches of 500, with no galaxy-sized Python list.
    Unknown bodies remain unresolved and are retried in a future generation.
    """
    after = uuid.UUID(int=0)
    counts = {}
    while True:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute('''SELECT cr.contribution_id, encode(g.manifest_sha256,'hex') AS manifest
                            FROM v3_private.eligible_journal_galaxy_contribution cr
                            CROSS JOIN v3_meta.canonical_generation g
                           WHERE g.generation_id=%s AND cr.decided_at <= g.created_at
                             AND cr.contribution_id > %s ORDER BY cr.contribution_id LIMIT 500''',
                        (generation_id, after))
            batch = cur.fetchall()
        if not batch:
            break
        selected_ids = [row['contribution_id'] for row in batch]
        plan = reconcile_generation(conn, generation_id=generation_id, contribution_ids=selected_ids)
        result = reconcile_generation(conn, generation_id=generation_id,
                                      contribution_ids=selected_ids,
                                      apply=True, expected_manifest_sha256=plan['manifest_sha256'],
                                      expected_state_sha256=plan['reconciliation_state_sha256'])
        for row in result['results']:
            counts[row['status']] = counts.get(row['status'], 0) + 1
        after = batch[-1]['contribution_id']
    return {'generation_id': str(generation_id), 'published': False, 'counts': counts}
