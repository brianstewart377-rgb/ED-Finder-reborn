from __future__ import annotations

import json
from collections.abc import Mapping
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import uuid4

import asyncpg

from edfinder_api.observations.store import observed_fact_summary
from shared_contracts.evidence_identity import (
    coerce_optional_datetime as _coerce_optional_datetime,
    content_addressed_evidence_key as _content_addressed_evidence_key,
    datetime_to_utc_isoformat as _dt_to_str,
)

from .api_models import (
    CanonicalEvidencePromotionRequest,
    DerivedFeatureCreateRequest,
    EvidenceRecordCreateRequest,
    RuleDecisionRequest,
    RuleProposalCreateRequest,
)
from .models import (
    DerivedFeature,
    EvidenceRecord,
    EvidenceSystemFocusArea,
    EvidenceSystemSummary,
    RuleDecision,
    RuleProposal,
)

_DEFAULT_EVIDENCE_FRESHNESS_POLICY: tuple[int, int] = (30, 180)
_EVIDENCE_FRESHNESS_POLICIES: dict[str, tuple[int, int]] = {
    'body_completeness': (90, 365),
    'body_scan': (90, 365),
    'body_signal_scan': (30, 180),
    'colonisation_status': (3, 14),
    'operator_note': (30, 180),
    'ring_composition': (90, 365),
    'service_snapshot': (7, 30),
    'station_set': (7, 30),
}
_CANONICAL_PROMOTION_SOURCE = 'canonical_app_data'
_CANONICAL_PROMOTION_ALLOWED_TYPES = {'body_completeness', 'station_set', 'colonisation_status', 'ring_composition'}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(',', ':'), sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _dedupe_text_list(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalise_freshness_policies(
    overrides: Mapping[str, tuple[int, int]] | None = None,
) -> tuple[dict[str, tuple[int, int]], tuple[int, int]]:
    policies = dict(_EVIDENCE_FRESHNESS_POLICIES)
    default_policy = _DEFAULT_EVIDENCE_FRESHNESS_POLICY
    if overrides:
        for evidence_type, policy in overrides.items():
            if evidence_type == '*':
                default_policy = policy
                continue
            policies[evidence_type] = policy
    return policies, default_policy


def _group_freshness_policies(
    overrides: Mapping[str, tuple[int, int]] | None = None,
) -> tuple[dict[tuple[int, int], list[str]], tuple[int, int]]:
    policies, default_policy = _normalise_freshness_policies(overrides)
    grouped: dict[tuple[int, int], list[str]] = defaultdict(list)
    for evidence_type, policy in policies.items():
        grouped[policy].append(evidence_type)
    return {policy: sorted(types) for policy, types in grouped.items()}, default_policy


def _stamp_supersession_metadata(metadata: Mapping[str, Any] | None, superseded_count: int) -> dict[str, Any]:
    stamped = dict(metadata or {})
    lifecycle_value = stamped.get('lifecycle')
    lifecycle = dict(lifecycle_value) if isinstance(lifecycle_value, Mapping) else {}
    lifecycle['superseded_record_count'] = superseded_count
    stamped['lifecycle'] = lifecycle
    return stamped


def row_to_evidence_record(row: Any) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_key=str(row['evidence_key']),
        system_id64=int(row['system_id64']),
        source_name=str(row['source_name']),
        origin=str(row['origin']),
        subject_type=str(row['subject_type']),
        subject_id=row['subject_id'],
        evidence_type=str(row['evidence_type']),
        record_status=str(row['record_status']),
        freshness_status=str(row['freshness_status']),
        confidence=str(row['confidence']),
        summary=row['summary'],
        source_record_id=row['source_record_id'],
        source_run_key=row['source_run_key'],
        observed_at=_dt_to_str(row['observed_at']),
        collected_at=_dt_to_str(row['collected_at']),
        expires_at=_dt_to_str(row['expires_at']),
        value=dict(_json_loads(row['value_json'], {})),
        provenance=dict(_json_loads(row['provenance_json'], {})),
        tags=list(_json_loads(row['tags_json'], [])),
        metadata=dict(_json_loads(row['metadata_json'], {})),
        created_at=_dt_to_str(row['created_at']),
        updated_at=_dt_to_str(row['updated_at']),
    )


def row_to_derived_feature(row: Any) -> DerivedFeature:
    return DerivedFeature(
        feature_key=str(row['feature_key']),
        system_id64=int(row['system_id64']),
        feature_name=str(row['feature_name']),
        feature_version=str(row['feature_version']),
        feature_status=str(row['feature_status']),
        confidence=str(row['confidence']),
        summary=row['summary'],
        derived_from_run_key=row['derived_from_run_key'],
        derived_at=_dt_to_str(row['derived_at']),
        expires_at=_dt_to_str(row['expires_at']),
        value=dict(_json_loads(row['value_json'], {})),
        evidence_refs=list(_json_loads(row['evidence_refs_json'], [])),
        metadata=dict(_json_loads(row['metadata_json'], {})),
        created_at=_dt_to_str(row['created_at']),
        updated_at=_dt_to_str(row['updated_at']),
    )


def row_to_rule_proposal(row: Any) -> RuleProposal:
    return RuleProposal(
        proposal_key=str(row['proposal_key']),
        proposal_type=str(row['proposal_type']),
        domain=str(row['domain']),
        scope_type=str(row['scope_type']),
        scope_key=str(row['scope_key']),
        status=str(row['status']),
        priority=str(row['priority']),
        risk_level=str(row['risk_level']),
        auto_approval_eligible=bool(row['auto_approval_eligible']),
        summary=str(row['summary']),
        proposed_by=str(row['proposed_by']),
        decided_by=row['decided_by'],
        decision_notes=row['decision_notes'],
        proposed_change=dict(_json_loads(row['proposed_change_json'], {})),
        evidence_refs=list(_json_loads(row['evidence_refs_json'], [])),
        impact_summary=dict(_json_loads(row['impact_summary_json'], {})),
        metadata=dict(_json_loads(row['metadata_json'], {})),
        created_at=_dt_to_str(row['created_at']),
        updated_at=_dt_to_str(row['updated_at']),
        decided_at=_dt_to_str(row['decided_at']),
    )


def row_to_rule_decision(row: Any) -> RuleDecision:
    decision_id = row['decision_id'] if 'decision_id' in row else row['id']
    return RuleDecision(
        decision_id=int(decision_id),
        proposal_key=str(row['proposal_key']),
        decision=str(row['decision']),
        decided_by=str(row['decided_by']),
        reason=row['reason'],
        metadata=dict(_json_loads(row['metadata_json'], {})),
        created_at=_dt_to_str(row['created_at']),
    )


async def _create_evidence_record_with_conn(
    conn: asyncpg.Connection,
    request: EvidenceRecordCreateRequest,
) -> EvidenceRecord:
    payload = request.model_dump(mode='json')
    evidence_key = _content_addressed_evidence_key(payload)
    for attempt in range(2):
        existing_row = await conn.fetchrow(
            '''
            SELECT *
            FROM evidence_records
            WHERE evidence_key = $1
            ''',
            evidence_key,
        )
        if existing_row is not None:
            return row_to_evidence_record(existing_row)

        try:
            superseded_count = await _supersede_active_evidence_records(conn, payload, replacement_key=evidence_key)
            metadata = _stamp_supersession_metadata(payload.get('metadata'), superseded_count)
            row = await conn.fetchrow(
                '''
                INSERT INTO evidence_records (
                    evidence_key,
                    system_id64,
                    source_name,
                    origin,
                    subject_type,
                    subject_id,
                    evidence_type,
                    record_status,
                    freshness_status,
                    confidence,
                    summary,
                    source_record_id,
                    source_run_key,
                    observed_at,
                    collected_at,
                    expires_at,
                    value_json,
                    provenance_json,
                    tags_json,
                    metadata_json
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                    $11, $12, $13, $14::timestamptz, $15::timestamptz, $16::timestamptz,
                    $17::jsonb, $18::jsonb, $19::jsonb, $20::jsonb
                )
                ON CONFLICT (evidence_key) DO UPDATE
                    SET updated_at = evidence_records.updated_at
                RETURNING *
                ''',
                evidence_key,
                payload['system_id64'],
                payload['source_name'],
                payload['origin'],
                payload['subject_type'],
                payload.get('subject_id'),
                payload['evidence_type'],
                payload['record_status'],
                payload['freshness_status'],
                payload['confidence'],
                payload.get('summary'),
                payload.get('source_record_id'),
                payload.get('source_run_key'),
                _coerce_optional_datetime(payload.get('observed_at')),
                _coerce_optional_datetime(payload.get('collected_at')),
                _coerce_optional_datetime(payload.get('expires_at')),
                payload.get('value') or {},
                payload.get('provenance') or {},
                payload.get('tags') or [],
                metadata,
            )
            return row_to_evidence_record(row)
        except asyncpg.exceptions.UniqueViolationError:
            if attempt == 1:
                raise
    raise RuntimeError('evidence record insert retry exhausted unexpectedly')


async def _find_equivalent_active_evidence_record(
    conn: asyncpg.Connection,
    payload: Mapping[str, Any],
) -> EvidenceRecord | None:
    row = await conn.fetchrow(
        '''
        SELECT *
        FROM evidence_records
        WHERE system_id64 = $1
          AND source_name = $2
          AND subject_type = $3
          AND subject_id IS NOT DISTINCT FROM $4
          AND evidence_type = $5
          AND record_status = 'active'
        ORDER BY COALESCE(observed_at, collected_at, created_at) DESC, evidence_key DESC
        LIMIT 1
        ''',
        payload['system_id64'],
        payload['source_name'],
        payload['subject_type'],
        payload.get('subject_id'),
        payload['evidence_type'],
    )
    if row is None:
        return None

    same_value = dict(_json_loads(row['value_json'], {})) == dict(payload.get('value') or {})
    same_summary = (row['summary'] or None) == (payload.get('summary') or None)
    same_confidence = str(row['confidence']) == str(payload['confidence'])
    same_origin = str(row['origin']) == str(payload['origin'])
    same_freshness = str(row['freshness_status']) == str(payload['freshness_status'])

    if all((same_value, same_summary, same_confidence, same_origin, same_freshness)):
        return row_to_evidence_record(row)
    return None


async def create_evidence_record(
    pool: asyncpg.Pool,
    request: EvidenceRecordCreateRequest,
) -> EvidenceRecord:
    async with pool.acquire() as conn:
        async with conn.transaction():
            return await _create_evidence_record_with_conn(conn, request)


async def list_evidence_records(
    pool: asyncpg.Pool,
    *,
    system_id64: int | None = None,
    source_name: str | None = None,
    origin: str | None = None,
    record_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[EvidenceRecord], int]:
    where, args_for_count = _build_optional_filters(
        ('system_id64', system_id64),
        ('source_name', source_name),
        ('origin', origin),
        ('record_status', record_status),
    )
    args = list(args_for_count)
    args.extend([limit, offset])
    limit_pos = len(args) - 1
    offset_pos = len(args)
    async with pool.acquire() as conn:
        total = await conn.fetchval(f'SELECT count(*) FROM evidence_records WHERE {where}', *args_for_count)
        rows = await conn.fetch(
            f'''
            SELECT * FROM evidence_records
            WHERE {where}
            ORDER BY COALESCE(observed_at, collected_at, created_at) DESC, evidence_key DESC
            LIMIT ${limit_pos} OFFSET ${offset_pos}
            ''',
            *args,
        )
    return [row_to_evidence_record(row) for row in rows], int(total or 0)


async def sweep_evidence_record_freshness(
    pool: asyncpg.Pool,
    *,
    now: datetime | None = None,
    policy_overrides: Mapping[str, tuple[int, int]] | None = None,
) -> dict[str, int]:
    as_of = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    grouped_policies, default_policy = _group_freshness_policies(policy_overrides)
    known_evidence_types = sorted(
        evidence_type
        for evidence_types in grouped_policies.values()
        for evidence_type in evidence_types
    )
    summary = {'expired': 0, 'stale': 0}

    async with pool.acquire() as conn:
        async with conn.transaction():
            summary['expired'] += await _expire_evidence_records_by_expires_at(conn, as_of)
            summary['expired'] += await _update_freshness_by_age(
                conn,
                as_of=as_of,
                age_days=default_policy[1],
                target_status='expired',
                eligible_statuses=['current', 'stale', 'unknown'],
                excluded_evidence_types=known_evidence_types,
            )
            for policy, evidence_types in grouped_policies.items():
                summary['expired'] += await _update_freshness_by_age(
                    conn,
                    as_of=as_of,
                    age_days=policy[1],
                    target_status='expired',
                    eligible_statuses=['current', 'stale', 'unknown'],
                    evidence_types=evidence_types,
                )

            summary['stale'] += await _update_freshness_by_age(
                conn,
                as_of=as_of,
                age_days=default_policy[0],
                target_status='stale',
                eligible_statuses=['current', 'unknown'],
                excluded_evidence_types=known_evidence_types,
            )
            for policy, evidence_types in grouped_policies.items():
                summary['stale'] += await _update_freshness_by_age(
                    conn,
                    as_of=as_of,
                    age_days=policy[0],
                    target_status='stale',
                    eligible_statuses=['current', 'unknown'],
                    evidence_types=evidence_types,
                )

    return summary


async def create_derived_feature(
    pool: asyncpg.Pool,
    request: DerivedFeatureCreateRequest,
) -> DerivedFeature:
    feature_key = f'feat_{uuid4().hex}'
    payload = request.model_dump(mode='json')
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            '''
            INSERT INTO derived_features (
                feature_key,
                system_id64,
                feature_name,
                feature_version,
                feature_status,
                confidence,
                summary,
                derived_from_run_key,
                derived_at,
                expires_at,
                value_json,
                evidence_refs_json,
                metadata_json
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9::timestamptz, $10::timestamptz,
                $11::jsonb, $12::jsonb, $13::jsonb
            )
            RETURNING *
            ''',
            feature_key,
            payload['system_id64'],
            payload['feature_name'],
            payload['feature_version'],
            payload['feature_status'],
            payload['confidence'],
            payload.get('summary'),
            payload.get('derived_from_run_key'),
            _coerce_optional_datetime(payload.get('derived_at')),
            _coerce_optional_datetime(payload.get('expires_at')),
            payload.get('value') or {},
            _dedupe_text_list(payload.get('evidence_refs') or []),
            payload.get('metadata') or {},
        )
    return row_to_derived_feature(row)


async def list_derived_features(
    pool: asyncpg.Pool,
    *,
    system_id64: int | None = None,
    feature_name: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[DerivedFeature], int]:
    where, args_for_count = _build_optional_filters(
        ('system_id64', system_id64),
        ('feature_name', feature_name),
    )
    args = list(args_for_count)
    args.extend([limit, offset])
    limit_pos = len(args) - 1
    offset_pos = len(args)
    async with pool.acquire() as conn:
        total = await conn.fetchval(f'SELECT count(*) FROM derived_features WHERE {where}', *args_for_count)
        rows = await conn.fetch(
            f'''
            SELECT * FROM derived_features
            WHERE {where}
            ORDER BY COALESCE(derived_at, created_at) DESC, feature_key DESC
            LIMIT ${limit_pos} OFFSET ${offset_pos}
            ''',
            *args,
        )
    return [row_to_derived_feature(row) for row in rows], int(total or 0)


async def create_rule_proposal(
    pool: asyncpg.Pool,
    request: RuleProposalCreateRequest,
) -> RuleProposal:
    proposal_key = f'prop_{uuid4().hex}'
    payload = request.model_dump(mode='json')
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            '''
            INSERT INTO rule_proposals (
                proposal_key,
                proposal_type,
                domain,
                scope_type,
                scope_key,
                status,
                priority,
                risk_level,
                auto_approval_eligible,
                summary,
                proposed_by,
                decision_notes,
                proposed_change_json,
                evidence_refs_json,
                impact_summary_json,
                metadata_json
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9,
                $10, $11, $12, $13::jsonb, $14::jsonb, $15::jsonb, $16::jsonb
            )
            RETURNING *
            ''',
            proposal_key,
            payload['proposal_type'],
            payload['domain'],
            payload['scope_type'],
            payload['scope_key'],
            payload['status'],
            payload['priority'],
            payload['risk_level'],
            payload['auto_approval_eligible'],
            payload['summary'],
            payload['proposed_by'],
            payload.get('decision_notes'),
            payload.get('proposed_change') or {},
            _dedupe_text_list(payload.get('evidence_refs') or []),
            payload.get('impact_summary') or {},
            payload.get('metadata') or {},
        )
    return row_to_rule_proposal(row)


async def list_rule_proposals(
    pool: asyncpg.Pool,
    *,
    status: str | None = None,
    statuses: list[str] | None = None,
    domain: str | None = None,
    scope_key: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[RuleProposal], int]:
    where, args_for_count = _build_rule_proposal_filters(
        status=status,
        statuses=statuses,
        domain=domain,
        scope_key=scope_key,
    )
    args = list(args_for_count)
    args.extend([limit, offset])
    limit_pos = len(args) - 1
    offset_pos = len(args)
    async with pool.acquire() as conn:
        total = await conn.fetchval(f'SELECT count(*) FROM rule_proposals WHERE {where}', *args_for_count)
        rows = await conn.fetch(
            f'''
            SELECT * FROM rule_proposals
            WHERE {where}
            ORDER BY created_at DESC, proposal_key DESC
            LIMIT ${limit_pos} OFFSET ${offset_pos}
            ''',
            *args,
        )
    return [row_to_rule_proposal(row) for row in rows], int(total or 0)


async def create_rule_decision(
    pool: asyncpg.Pool,
    proposal_key: str,
    request: RuleDecisionRequest,
) -> RuleDecision | None:
    status_map = {
        'approved': 'approved',
        'rejected': 'rejected',
        'superseded': 'superseded',
        'rolled_back': 'superseded',
    }
    payload = request.model_dump(mode='json')
    async with pool.acquire() as conn:
        async with conn.transaction():
            proposal_exists = await conn.fetchval(
                'SELECT 1 FROM rule_proposals WHERE proposal_key = $1',
                proposal_key,
            )
            if proposal_exists is None:
                return None
            row = await conn.fetchrow(
                '''
                INSERT INTO rule_decisions (
                    proposal_key,
                    decision,
                    decided_by,
                    reason,
                    metadata_json
                ) VALUES (
                    $1, $2, $3, $4, $5::jsonb
                )
                RETURNING *
                ''',
                proposal_key,
                payload['decision'],
                payload['decided_by'],
                payload.get('reason'),
                payload.get('metadata') or {},
            )
            await conn.execute(
                '''
                UPDATE rule_proposals
                SET status = $2,
                    decided_by = $3,
                    decision_notes = COALESCE($4, decision_notes),
                    decided_at = now(),
                    updated_at = now()
                WHERE proposal_key = $1
                ''',
                proposal_key,
                status_map[payload['decision']],
                payload['decided_by'],
                payload.get('reason'),
            )
    return row_to_rule_decision(row) if row else None


async def build_evidence_system_summary(
    pool: asyncpg.Pool,
    system_id64: int,
    *,
    record_limit: int = 5,
    feature_limit: int = 5,
    proposal_limit: int = 5,
) -> EvidenceSystemSummary:
    observed = await observed_fact_summary(pool, system_id64)
    records, imported_record_count = await list_evidence_records(
        pool,
        system_id64=system_id64,
        record_status='active',
        limit=record_limit,
        offset=0,
    )
    features, derived_feature_count = await list_derived_features(
        pool,
        system_id64=system_id64,
        limit=feature_limit,
        offset=0,
    )
    open_proposals, open_rule_proposal_count = await list_rule_proposals(
        pool,
        statuses=['pending_review', 'approved', 'auto_approved'],
        scope_key=str(system_id64),
        limit=proposal_limit,
        offset=0,
    )
    focus_areas = await _build_evidence_focus_areas(
        pool,
        system_id64=system_id64,
        records=records,
    )
    return EvidenceSystemSummary(
        schema_version='evidence_store/v1',
        system_id64=system_id64,
        observed_fact_count=int(observed.get('total_count', 0) or 0),
        imported_record_count=imported_record_count,
        derived_feature_count=derived_feature_count,
        open_rule_proposal_count=open_rule_proposal_count,
        focus_areas=focus_areas,
        records=records,
        derived_features=features,
        open_rule_proposals=open_proposals,
    )


async def promote_system_canonical_evidence(
    pool: asyncpg.Pool,
    system_id64: int,
    request: CanonicalEvidencePromotionRequest,
) -> tuple[list[EvidenceRecord], list[str]]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            promotion = await promote_canonical_evidence_for_systems(
                conn,
                system_ids=[system_id64],
                evidence_types=request.evidence_types,
                trigger_context='operator_manual_canonical_promotion',
            )
    return promotion['records'], promotion['warnings']


async def promote_canonical_evidence_for_systems(
    conn: asyncpg.Connection,
    *,
    system_ids: Iterable[int],
    evidence_types: list[str],
    source_run_key: str | None = None,
    trigger_context: str | None = None,
) -> dict[str, Any]:
    promoted: list[EvidenceRecord] = []
    warnings: list[str] = []
    deduped_count = 0

    for system_id64 in sorted({int(system_id) for system_id in system_ids if system_id is not None}):
        payloads, payload_warnings = await _build_canonical_evidence_requests(
            conn,
            system_id64=system_id64,
            evidence_types=evidence_types,
            source_run_key=source_run_key,
            trigger_context=trigger_context,
        )
        warnings.extend(payload_warnings)
        for payload in payloads:
            existing = await _find_equivalent_active_evidence_record(conn, payload.model_dump(mode='json'))
            if existing is not None:
                deduped_count += 1
                warnings.append(
                    f'{payload.evidence_type} for system {system_id64} already matches the active canonical evidence record.'
                )
                continue
            promoted.append(await _create_evidence_record_with_conn(conn, payload))

    return {
        'records': promoted,
        'warnings': warnings,
        'created_count': len(promoted),
        'deduped_count': deduped_count,
    }


def _build_optional_filters(*filters: tuple[str, Any]) -> tuple[str, list[Any]]:
    conditions = ['1 = 1']
    args: list[Any] = []
    for column, value in filters:
        if value is None:
            continue
        args.append(value)
        conditions.append(f'{column} = ${len(args)}')
    return ' AND '.join(conditions), args


async def _build_evidence_focus_areas(
    pool: asyncpg.Pool,
    *,
    system_id64: int,
    records: list[EvidenceRecord],
) -> list[EvidenceSystemFocusArea]:
    active_by_type: dict[str, EvidenceRecord] = {}
    for record in records:
        if record.record_status != 'active':
            continue
        active_by_type.setdefault(record.evidence_type, record)

    async with pool.acquire() as conn:
        system_row = await conn.fetchrow(
            """
            SELECT
                id64,
                body_count,
                is_colonised,
                is_being_colonised,
                COALESCE(eddn_updated_at, updated_at)::text AS status_updated_at
            FROM systems
            WHERE id64 = $1
            """,
            system_id64,
        )
        body_row_count = int(
            await conn.fetchval(
                'SELECT COUNT(*)::int FROM bodies WHERE system_id64 = $1',
                system_id64,
            )
            or 0
        )
        station_count = int(
            await conn.fetchval(
                'SELECT COUNT(*)::int FROM stations WHERE system_id64 = $1',
                system_id64,
            )
            or 0
        )
        scan_fact_count = int(
            await conn.fetchval(
                'SELECT COUNT(*)::int FROM body_scan_facts WHERE system_address = $1',
                system_id64,
            )
            or 0
        )
        ringed_body_count = int(
            await conn.fetchval(
                """
                SELECT COUNT(*)::int
                FROM body_scan_facts
                WHERE system_address = $1
                  AND is_ringed = true
                """,
                system_id64,
            )
            or 0
        )
        ring_identity_count = int(
            await conn.fetchval(
                """
                SELECT COUNT(DISTINCT body_id)::int
                FROM body_rings
                WHERE system_id64 = $1
                  AND body_id IS NOT NULL
                  AND association_status = 'local_matched'
                """,
                system_id64,
            )
            or 0
        )

    if system_row is None:
        return []

    catalogue_body_count = int(system_row['body_count'] or 0)
    focus_areas = [
        _focus_area_from_record(
            key='colonisation_status',
            label='Colonisation status',
            record=_first_active_record(active_by_type, 'colonisation_status'),
            fallback_summary=_canonical_colonisation_summary(system_row),
        ),
        _focus_area_from_record(
            key='station_set',
            label='Station set',
            record=_first_active_record(active_by_type, 'station_set', 'service_snapshot'),
            fallback_summary=_canonical_station_summary(station_count),
        ),
        _focus_area_from_record(
            key='body_completeness',
            label='Body coverage',
            record=_first_active_record(active_by_type, 'body_completeness', 'body_scan'),
            fallback_summary=_canonical_body_summary(
                catalogue_body_count=catalogue_body_count,
                body_row_count=body_row_count,
                scan_fact_count=scan_fact_count,
            ),
        ),
        _focus_area_from_record(
            key='ring_composition',
            label='Ring composition',
            record=_first_active_record(active_by_type, 'ring_composition'),
            fallback_summary=_canonical_ring_summary(
                ringed_body_count=ringed_body_count,
                ring_identity_count=ring_identity_count,
                scan_fact_count=scan_fact_count,
            ),
        ),
    ]
    return [focus_area for focus_area in focus_areas if focus_area is not None]


async def _build_canonical_evidence_requests(
    conn: asyncpg.Connection,
    *,
    system_id64: int,
    evidence_types: list[str],
    source_run_key: str | None = None,
    trigger_context: str | None = None,
) -> tuple[list[EvidenceRecordCreateRequest], list[str]]:
    requested = [evidence_type for evidence_type in evidence_types if evidence_type in _CANONICAL_PROMOTION_ALLOWED_TYPES]
    if not requested:
        return [], ['No supported canonical evidence types were requested.']

    system_row = await conn.fetchrow(
        """
        SELECT
            id64,
            name,
            body_count,
            is_colonised,
            is_being_colonised,
            updated_at::text AS updated_at
        FROM systems
        WHERE id64 = $1
        """,
        system_id64,
    )
    if system_row is None:
        raise LookupError(f'System {system_id64} not found')

    payloads: list[EvidenceRecordCreateRequest] = []
    warnings: list[str] = []

    if 'body_completeness' in requested:
        body_payload = await _build_body_completeness_promotion_request(
            conn,
            system_row,
            source_run_key=source_run_key,
            trigger_context=trigger_context,
        )
        if body_payload is None:
            warnings.append('Body completeness could not be promoted because canonical body coverage is still absent.')
        else:
            payloads.append(body_payload)

    if 'station_set' in requested:
        station_payload = await _build_station_set_promotion_request(
            conn,
            system_row,
            source_run_key=source_run_key,
            trigger_context=trigger_context,
        )
        if station_payload is None:
            warnings.append('Station set could not be promoted because canonical station rows are still absent.')
        else:
            payloads.append(station_payload)

    if 'colonisation_status' in requested:
        colonisation_payload = await _build_colonisation_status_promotion_request(
            system_row,
            source_run_key=source_run_key,
            trigger_context=trigger_context,
        )
        if colonisation_payload is None:
            warnings.append('Colonisation status could not be promoted because canonical status data is still absent.')
        else:
            payloads.append(colonisation_payload)

    if 'ring_composition' in requested:
        ring_payload = await _build_ring_composition_promotion_request(
            conn,
            system_row,
            source_run_key=source_run_key,
            trigger_context=trigger_context,
        )
        if ring_payload is None:
            warnings.append('Ring composition could not be promoted because canonical ring-bearing body data is still absent.')
        else:
            payloads.append(ring_payload)

    return payloads, warnings


async def _build_body_completeness_promotion_request(
    conn: asyncpg.Connection,
    system_row: Any,
    *,
    source_run_key: str | None = None,
    trigger_context: str | None = None,
) -> EvidenceRecordCreateRequest | None:
    system_id64 = int(_row_value(system_row, 'id64') or 0)
    system_name = str(_row_value(system_row, 'name') or system_id64)
    catalogue_body_count = int(_row_value(system_row, 'body_count') or 0)
    system_updated_at = _text_or_none(_row_value(system_row, 'updated_at'))
    body_row_count = int(
        await conn.fetchval(
            'SELECT COUNT(*)::int FROM bodies WHERE system_id64 = $1',
            system_id64,
        )
        or 0
    )
    scan_fact_count = int(
        await conn.fetchval(
            'SELECT COUNT(*)::int FROM body_scan_facts WHERE system_address = $1',
            system_id64,
        )
        or 0
    )
    body_data_updated_at = _text_or_none(
        await conn.fetchval(
            'SELECT MAX(updated_at)::text FROM body_scan_facts WHERE system_address = $1',
            system_id64,
        )
    )

    total_body_count = max(catalogue_body_count, body_row_count, scan_fact_count)
    if total_body_count <= 0:
        return None

    known_body_count = max(body_row_count, scan_fact_count)
    coverage_ratio = round(known_body_count / total_body_count, 4) if total_body_count > 0 else None
    if scan_fact_count > 0:
        summary = (
            f'Canonical body coverage for {system_name} currently covers '
            f'{min(scan_fact_count, total_body_count)}/{total_body_count} scanned bodies.'
        )
        confidence = 'high' if scan_fact_count >= total_body_count else 'medium'
    elif body_row_count > 0:
        summary = (
            f'Canonical body coverage for {system_name} currently includes '
            f'{body_row_count}/{total_body_count} body rows.'
        )
        confidence = 'medium'
    else:
        summary = f'Canonical system metadata for {system_name} currently expects {total_body_count} bodies.'
        confidence = 'low'

    observed_at = body_data_updated_at or system_updated_at
    return EvidenceRecordCreateRequest(
        system_id64=system_id64,
        source_name=_CANONICAL_PROMOTION_SOURCE,
        origin='derived',
        subject_type='system',
        subject_id=str(system_id64),
        evidence_type='body_completeness',
        source_run_key=source_run_key,
        freshness_status='current',
        confidence=confidence,
        summary=summary,
        observed_at=observed_at,
        value={
            'system_name': system_name,
            'catalogue_body_count': catalogue_body_count,
            'canonical_body_row_count': body_row_count,
            'scan_fact_count': scan_fact_count,
            'known_body_count': known_body_count,
            'total_body_count': total_body_count,
            'coverage_ratio': coverage_ratio,
        },
        provenance={
            'promotion_lane': 'system_canonical_evidence/v1',
            'trigger_context': trigger_context,
            'source_run_key': source_run_key,
            'source_tables': ['systems', 'bodies', 'body_scan_facts'],
            'body_data_updated_at': body_data_updated_at,
            'system_updated_at': system_updated_at,
        },
        tags=['canonical_promotion', 'coverage'],
        metadata={
            'promotion_lane': 'system_canonical_evidence/v1',
            'trigger_context': trigger_context,
            'requested_evidence_type': 'body_completeness',
        },
    )


async def _build_station_set_promotion_request(
    conn: asyncpg.Connection,
    system_row: Any,
    *,
    source_run_key: str | None = None,
    trigger_context: str | None = None,
) -> EvidenceRecordCreateRequest | None:
    system_id64 = int(_row_value(system_row, 'id64') or 0)
    system_name = str(_row_value(system_row, 'name') or system_id64)
    station_count = int(
        await conn.fetchval(
            'SELECT COUNT(*)::int FROM stations WHERE system_id64 = $1',
            system_id64,
        )
        or 0
    )
    if station_count <= 0:
        return None

    has_station_links = bool(
        await conn.fetchval("SELECT to_regclass('public.station_body_links') IS NOT NULL")
    )
    linked_station_count: int | None = None
    if has_station_links:
        linked_station_count = int(
            await conn.fetchval(
                """
                SELECT COUNT(*)::int
                FROM station_body_links l
                JOIN stations s ON s.id = l.station_id
                WHERE s.system_id64 = $1
                  AND l.association_status = 'local_matched'
                """,
                system_id64,
            )
            or 0
        )

    latest_station_updated_at = _text_or_none(
        await conn.fetchval(
            """
            SELECT MAX(ts)::text
            FROM (
                SELECT MAX(distance_updated_at) AS ts
                FROM stations
                WHERE system_id64 = $1
                UNION ALL
                SELECT MAX(station_type_updated_at) AS ts
                FROM stations
                WHERE system_id64 = $1
                UNION ALL
                SELECT MAX(body_name_updated_at) AS ts
                FROM stations
                WHERE system_id64 = $1
            ) station_ts
            """,
            system_id64,
        )
    )
    unresolved_station_count = (
        max(0, station_count - linked_station_count)
        if linked_station_count is not None
        else None
    )
    if linked_station_count is None:
        summary = f'Canonical station data for {system_name} currently includes {station_count} stations.'
        confidence = 'medium'
    else:
        summary = (
            f'Canonical station data for {system_name} currently includes {station_count} stations; '
            f'{linked_station_count}/{station_count} local station-body links are matched.'
        )
        confidence = 'high' if linked_station_count >= station_count else 'medium'

    return EvidenceRecordCreateRequest(
        system_id64=system_id64,
        source_name=_CANONICAL_PROMOTION_SOURCE,
        origin='derived',
        subject_type='system',
        subject_id=str(system_id64),
        evidence_type='station_set',
        source_run_key=source_run_key,
        freshness_status='current',
        confidence=confidence,
        summary=summary,
        observed_at=latest_station_updated_at,
        value={
            'system_name': system_name,
            'station_count': station_count,
            'linked_station_count': linked_station_count,
            'unresolved_station_count': unresolved_station_count,
            'station_link_runtime_available': has_station_links,
        },
        provenance={
            'promotion_lane': 'system_canonical_evidence/v1',
            'trigger_context': trigger_context,
            'source_run_key': source_run_key,
            'source_tables': ['stations', 'station_body_links'] if has_station_links else ['stations'],
            'station_data_updated_at': latest_station_updated_at,
        },
        tags=['canonical_promotion', 'coverage'],
        metadata={
            'promotion_lane': 'system_canonical_evidence/v1',
            'trigger_context': trigger_context,
            'requested_evidence_type': 'station_set',
        },
    )


async def _build_colonisation_status_promotion_request(
    system_row: Any,
    *,
    source_run_key: str | None = None,
    trigger_context: str | None = None,
) -> EvidenceRecordCreateRequest | None:
    system_id64 = int(_row_value(system_row, 'id64') or 0)
    system_name = str(_row_value(system_row, 'name') or system_id64)
    is_colonised = _row_value(system_row, 'is_colonised')
    is_being_colonised = _row_value(system_row, 'is_being_colonised')
    status_updated_at = _text_or_none(_row_value(system_row, 'status_updated_at'))
    system_updated_at = _text_or_none(_row_value(system_row, 'updated_at'))
    observed_at = status_updated_at or system_updated_at

    if observed_at is None and is_colonised is None and is_being_colonised is None:
        return None

    if is_colonised:
        state = 'colonised'
        confidence = 'high'
    elif is_being_colonised:
        state = 'being_colonised'
        confidence = 'high'
    else:
        state = 'not_colonised'
        confidence = 'medium'

    summary = f'Canonical system status for {system_name} currently says this system is {state.replace("_", " ")}.'
    if observed_at:
        summary = (
            f'Canonical system status for {system_name} currently says this system is '
            f'{state.replace("_", " ")} as of {observed_at}.'
        )

    return EvidenceRecordCreateRequest(
        system_id64=system_id64,
        source_name=_CANONICAL_PROMOTION_SOURCE,
        origin='derived',
        subject_type='system',
        subject_id=str(system_id64),
        evidence_type='colonisation_status',
        source_run_key=source_run_key,
        freshness_status='current',
        confidence=confidence,
        summary=summary,
        observed_at=observed_at,
        value={
            'system_name': system_name,
            'status': state,
            'is_colonised': bool(is_colonised),
            'is_being_colonised': bool(is_being_colonised),
            'status_updated_at': observed_at,
        },
        provenance={
            'promotion_lane': 'system_canonical_evidence/v1',
            'trigger_context': trigger_context,
            'source_run_key': source_run_key,
            'source_tables': ['systems'],
            'status_updated_at': observed_at,
        },
        tags=['canonical_promotion', 'status'],
        metadata={
            'promotion_lane': 'system_canonical_evidence/v1',
            'trigger_context': trigger_context,
            'requested_evidence_type': 'colonisation_status',
        },
    )


async def _build_ring_composition_promotion_request(
    conn: asyncpg.Connection,
    system_row: Any,
    *,
    source_run_key: str | None = None,
    trigger_context: str | None = None,
) -> EvidenceRecordCreateRequest | None:
    system_id64 = int(_row_value(system_row, 'id64') or 0)
    system_name = str(_row_value(system_row, 'name') or system_id64)
    ringed_body_count = int(
        await conn.fetchval(
            'SELECT COUNT(*)::int FROM body_scan_facts WHERE system_address = $1 AND is_ringed IS TRUE',
            system_id64,
        )
        or 0
    )
    ring_identity_count = int(
        await conn.fetchval(
            'SELECT COUNT(DISTINCT body_id)::int FROM body_rings WHERE system_id64 = $1',
            system_id64,
        )
        or 0
    )
    latest_ring_updated_at = _text_or_none(
        await conn.fetchval(
            'SELECT MAX(updated_at)::text FROM body_rings WHERE system_id64 = $1',
            system_id64,
        )
    )
    body_data_updated_at = _text_or_none(
        await conn.fetchval(
            'SELECT MAX(updated_at)::text FROM body_scan_facts WHERE system_address = $1',
            system_id64,
        )
    )
    observed_at = latest_ring_updated_at or body_data_updated_at or _text_or_none(_row_value(system_row, 'updated_at'))

    if ringed_body_count <= 0 and ring_identity_count <= 0:
        return None

    if ringed_body_count > 0 and ring_identity_count > 0:
        summary = (
            f'Canonical ring data for {system_name} currently identifies '
            f'{min(ring_identity_count, ringed_body_count)}/{ringed_body_count} ring-bearing bodies.'
        )
        confidence = 'high' if ring_identity_count >= ringed_body_count else 'medium'
    elif ringed_body_count > 0:
        summary = (
            f'Canonical scan facts for {system_name} currently show {ringed_body_count} ring-bearing bodies, '
            'but ring identities are still incomplete.'
        )
        confidence = 'medium'
    else:
        summary = f'Canonical ring data for {system_name} currently records {ring_identity_count} ring-bearing bodies.'
        confidence = 'medium'

    coverage_ratio = (
        round(min(ring_identity_count, ringed_body_count) / ringed_body_count, 4)
        if ringed_body_count > 0
        else None
    )

    return EvidenceRecordCreateRequest(
        system_id64=system_id64,
        source_name=_CANONICAL_PROMOTION_SOURCE,
        origin='derived',
        subject_type='system',
        subject_id=str(system_id64),
        evidence_type='ring_composition',
        source_run_key=source_run_key,
        freshness_status='current',
        confidence=confidence,
        summary=summary,
        observed_at=observed_at,
        value={
            'system_name': system_name,
            'ringed_body_count': ringed_body_count,
            'ring_identity_count': ring_identity_count,
            'coverage_ratio': coverage_ratio,
        },
        provenance={
            'promotion_lane': 'system_canonical_evidence/v1',
            'trigger_context': trigger_context,
            'source_run_key': source_run_key,
            'source_tables': ['body_scan_facts', 'body_rings'],
            'ring_data_updated_at': latest_ring_updated_at,
            'body_data_updated_at': body_data_updated_at,
        },
        tags=['canonical_promotion', 'coverage'],
        metadata={
            'promotion_lane': 'system_canonical_evidence/v1',
            'trigger_context': trigger_context,
            'requested_evidence_type': 'ring_composition',
        },
    )


def _first_active_record(
    active_by_type: Mapping[str, EvidenceRecord],
    *evidence_types: str,
) -> EvidenceRecord | None:
    for evidence_type in evidence_types:
        record = active_by_type.get(evidence_type)
        if record is not None:
            return record
    return None


def _focus_area_from_record(
    *,
    key: str,
    label: str,
    record: EvidenceRecord | None,
    fallback_summary: str | None,
) -> EvidenceSystemFocusArea | None:
    if record is not None:
        return EvidenceSystemFocusArea(
            key=key,
            label=label,
            posture='evidence_linked',
            summary=record.summary or f'Active {label.lower()} evidence is linked for this system.',
            evidence_type=record.evidence_type,
            evidence_key=record.evidence_key,
        )
    if fallback_summary is not None:
        return EvidenceSystemFocusArea(
            key=key,
            label=label,
            posture='canonical_present',
            summary=fallback_summary,
        )
    return EvidenceSystemFocusArea(
        key=key,
        label=label,
        posture='missing',
        summary=f'No linked evidence or canonical {label.lower()} data is available yet.',
    )


def _canonical_colonisation_summary(system_row: Any) -> str | None:
    status_updated_at = _text_or_none(_row_value(system_row, 'status_updated_at'))
    is_colonised = _row_value(system_row, 'is_colonised')
    is_being_colonised = _row_value(system_row, 'is_being_colonised')
    if status_updated_at is None and is_colonised is None and is_being_colonised is None:
        return None
    if is_colonised:
        state = 'colonised'
    elif is_being_colonised:
        state = 'being colonised'
    else:
        state = 'not currently flagged as colonised'
    if status_updated_at:
        return f'Canonical system status currently says this system is {state}; latest status evidence was updated at {status_updated_at}.'
    return f'Canonical system status currently says this system is {state}.'


def _canonical_station_summary(station_count: int) -> str | None:
    if station_count <= 0:
        return None
    suffix = '' if station_count == 1 else 's'
    return f'Canonical station data currently includes {station_count} station{suffix} for this system.'


def _canonical_body_summary(
    *,
    catalogue_body_count: int,
    body_row_count: int,
    scan_fact_count: int,
) -> str | None:
    total_body_count = max(catalogue_body_count, body_row_count, scan_fact_count)
    if total_body_count <= 0:
        return None
    if scan_fact_count > 0:
        known = min(scan_fact_count, total_body_count)
        return f'Canonical body data currently covers {known}/{total_body_count} scanned bodies for this system.'
    if body_row_count > 0:
        return f'Canonical body data currently includes {body_row_count}/{total_body_count} body rows for this system.'
    return f'Canonical system metadata currently expects {total_body_count} bodies for this system.'


def _canonical_ring_summary(
    *,
    ringed_body_count: int,
    ring_identity_count: int,
    scan_fact_count: int,
) -> str | None:
    if ringed_body_count > 0 and ring_identity_count > 0:
        return (
            'Canonical ring data currently identifies '
            f'{min(ring_identity_count, ringed_body_count)}/{ringed_body_count} ring-bearing bodies.'
        )
    if ringed_body_count > 0:
        return None
    if scan_fact_count > 0:
        return 'Canonical scan facts currently show no ring-bearing bodies in this system.'
    return None


async def _supersede_active_evidence_records(
    conn: asyncpg.Connection,
    payload: Mapping[str, Any],
    *,
    replacement_key: str,
) -> int:
    if payload.get('record_status') != 'active':
        return 0

    return int(
        await conn.fetchval(
            '''
            WITH updated AS (
                UPDATE evidence_records
                   SET record_status = 'superseded',
                       freshness_status = 'superseded',
                       updated_at = now(),
                       metadata_json = COALESCE(metadata_json, '{}'::jsonb) || jsonb_build_object(
                           'superseded_by',
                           $5::text,
                           'superseded_at',
                           to_jsonb(now())
                       )
                 WHERE system_id64 = $1
                   AND subject_type = $2
                   AND subject_id IS NOT DISTINCT FROM $3
                   AND evidence_type = $4
                   AND record_status = 'active'
                RETURNING 1
            )
            SELECT count(*) FROM updated
            ''',
            payload['system_id64'],
            payload['subject_type'],
            payload.get('subject_id'),
            payload['evidence_type'],
            replacement_key,
        )
        or 0
    )


async def _expire_evidence_records_by_expires_at(
    conn: asyncpg.Connection,
    as_of: datetime,
) -> int:
    return int(
        await conn.fetchval(
            '''
            WITH updated AS (
                UPDATE evidence_records
                   SET freshness_status = 'expired',
                       updated_at = now()
                 WHERE record_status = 'active'
                   AND freshness_status <> 'expired'
                   AND expires_at IS NOT NULL
                   AND expires_at <= $1::timestamptz
                RETURNING 1
            )
            SELECT count(*) FROM updated
            ''',
            as_of,
        )
        or 0
    )


async def _update_freshness_by_age(
    conn: asyncpg.Connection,
    *,
    as_of: datetime,
    age_days: int,
    target_status: str,
    eligible_statuses: list[str],
    evidence_types: list[str] | None = None,
    excluded_evidence_types: list[str] | None = None,
) -> int:
    cutoff = as_of - timedelta(days=age_days)
    args: list[Any] = [cutoff, eligible_statuses]
    type_clause = ''
    if evidence_types:
        args.append(evidence_types)
        type_clause = f' AND evidence_type = ANY(${len(args)}::text[])'
    elif excluded_evidence_types:
        args.append(excluded_evidence_types)
        type_clause = f' AND NOT (evidence_type = ANY(${len(args)}::text[]))'

    query = f'''
        WITH updated AS (
            UPDATE evidence_records
               SET freshness_status = '{target_status}',
                   updated_at = now()
             WHERE record_status = 'active'
               AND freshness_status = ANY($2::text[])
               AND COALESCE(observed_at, collected_at, created_at) <= $1::timestamptz
               {type_clause}
            RETURNING 1
        )
        SELECT count(*) FROM updated
    '''
    return int(await conn.fetchval(query, *args) or 0)


def _build_rule_proposal_filters(
    *,
    status: str | None,
    statuses: list[str] | None,
    domain: str | None,
    scope_key: str | None,
) -> tuple[str, list[Any]]:
    conditions = ['1 = 1']
    args: list[Any] = []
    if status is not None:
        args.append(status)
        conditions.append(f'status = ${len(args)}')
    if statuses:
        args.append(statuses)
        conditions.append(f'status = ANY(${len(args)}::text[])')
    if domain is not None:
        args.append(domain)
        conditions.append(f'domain = ${len(args)}')
    if scope_key is not None:
        args.append(scope_key)
        conditions.append(f'scope_key = ${len(args)}')
    return ' AND '.join(conditions), args


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
