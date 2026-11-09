from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import asyncpg

from .api_models import (
    CommanderPowerplayResponse,
    PowerplayChangeEvent,
    PowerplayContribution,
    PowerplayCycleSnapshot,
    PowerplayHistoryResponse,
    PowerplayImportReceipt,
    PowerplayImportRequest,
    PowerplaySystemState,
    PowerplaySystemsResponse,
)
from .parser import (
    ParsedPowerplayEvent,
    cycle_start_for,
    parse_powerplay_event,
    project_commander_state,
)

DEFAULT_SYSTEM_LIMIT = 10_000
MAX_SYSTEM_LIMIT = 40_000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


async def _rebuild_commander_state(
    conn: asyncpg.Connection,
    commander_key: str,
) -> None:
    rows = await conn.fetch(
        '''
        SELECT event_type, observed_at, game_build, source_payload, source,
               source_version, confidence
        FROM commander_powerplay_events
        WHERE commander_key = $1
        ORDER BY observed_at ASC, id ASC
        ''',
        commander_key,
    )
    parsed_events: list[ParsedPowerplayEvent] = []
    for row in rows:
        payload = _json(row['source_payload'], {})
        parsed = parse_powerplay_event(
            payload,
            observed_at=row['observed_at'],
            game_build=row['game_build'],
            source=str(row['source']),
            source_version=str(row['source_version']),
            confidence=float(row['confidence']),
        )
        if parsed is not None:
            parsed_events.append(parsed)

    projection = project_commander_state(parsed_events)
    if projection.last_updated is None:
        await conn.execute(
            'DELETE FROM commander_powerplay_state WHERE commander_key = $1',
            commander_key,
        )
        return

    last_event = parsed_events[-1]
    await conn.execute(
        '''
        INSERT INTO commander_powerplay_state (
            commander_key, pledge, rank, merits, last_updated, source,
            source_version, confidence, value_provenance, rebuilt_at
        ) VALUES (
            $1, $2::jsonb, $3::jsonb, $4::jsonb, $5, $6, $7, $8,
            $9::jsonb, NOW()
        )
        ON CONFLICT (commander_key) DO UPDATE SET
            pledge = EXCLUDED.pledge,
            rank = EXCLUDED.rank,
            merits = EXCLUDED.merits,
            last_updated = EXCLUDED.last_updated,
            source = EXCLUDED.source,
            source_version = EXCLUDED.source_version,
            confidence = EXCLUDED.confidence,
            value_provenance = EXCLUDED.value_provenance,
            rebuilt_at = NOW()
        ''',
        commander_key,
        projection.pledge,
        projection.rank,
        projection.merits,
        projection.last_updated,
        last_event.value_provenance[next(iter(last_event.value_provenance))]['source']
        if last_event.value_provenance else 'journal',
        last_event.value_provenance[next(iter(last_event.value_provenance))]['version']
        if last_event.value_provenance else 'unknown',
        last_event.confidence,
        projection.value_provenance,
    )


async def _version_cycle_snapshots(
    conn: asyncpg.Connection,
    commander_key: str,
) -> int:
    cycle_rows = await conn.fetch(
        '''
        SELECT DISTINCT cycle_start
        FROM powerplay_observations
        WHERE commander_key = $1
        ORDER BY cycle_start
        ''',
        commander_key,
    )
    versions_inserted = 0
    for cycle_row in cycle_rows:
        cycle_start = cycle_row['cycle_start']
        rows = await conn.fetch(
            '''
            SELECT DISTINCT ON (system_address)
                   system_address, system_name, controlling_power, control_state,
                   control_progress, reinforcement_points, undermining_points,
                   powers, observed_at, game_build, source, source_version,
                   confidence, value_provenance
            FROM powerplay_observations
            WHERE commander_key = $1
              AND observed_at < $2::timestamptz + INTERVAL '7 days'
            ORDER BY system_address, observed_at DESC, id DESC
            ''',
            commander_key,
            cycle_start,
        )
        if not rows:
            continue
        snapshot: dict[str, Any] = {}
        for row in rows:
            snapshot[str(row['system_address'])] = {
                'system_address': int(row['system_address']),
                'system_name': row['system_name'],
                'controlling_power': _json(row['controlling_power']),
                'control_state': _json(row['control_state']),
                'control_progress': _json(row['control_progress']),
                'reinforcement_points': _json(row['reinforcement_points']),
                'undermining_points': _json(row['undermining_points']),
                'powers': _json(row['powers'], []),
                'observed_at': _iso(row['observed_at']),
                'game_build': row['game_build'],
                'value_provenance': _json(row['value_provenance'], {}),
            }
        captured_at = max(row['observed_at'] for row in rows)
        newest = max(rows, key=lambda row: row['observed_at'])
        digest = _snapshot_hash(snapshot)
        inserted = await conn.fetchval(
            '''
            INSERT INTO powerplay_cycles (
                commander_key, week, cycle_start, captured_at, control_snapshot,
                snapshot_hash, source, source_version, confidence, value_provenance
            ) VALUES (
                $1, $2::date, $3, $4, $5::jsonb, $6, $7, $8, $9, $10::jsonb
            )
            ON CONFLICT (commander_key, cycle_start, snapshot_hash) DO NOTHING
            RETURNING id
            ''',
            commander_key,
            cycle_start.date(),
            cycle_start,
            captured_at,
            snapshot,
            digest,
            newest['source'],
            newest['source_version'],
            newest['confidence'],
            {
                'control_snapshot': {
                    'source': newest['source'],
                    'version': newest['source_version'],
                    'confidence': float(newest['confidence']),
                    'observed_at': _iso(captured_at),
                },
            },
        )
        versions_inserted += int(inserted is not None)
    return versions_inserted


async def import_powerplay_events(
    pool: asyncpg.Pool,
    request: PowerplayImportRequest,
) -> PowerplayImportReceipt:
    system_staged = 0
    commander_staged = 0
    duplicates = 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            for event in sorted(request.events, key=lambda item: item.observed_at):
                parsed = parse_powerplay_event(
                    event.source_payload,
                    observed_at=event.observed_at,
                    game_build=event.game_build,
                    source=request.source,
                    source_version=request.source_version,
                )
                if parsed is None:
                    continue
                if parsed.kind == 'system':
                    values = parsed.values
                    inserted = await conn.fetchval(
                        '''
                        INSERT INTO powerplay_observations (
                            commander_key, source, source_version, source_record_hash,
                            source_event, system_address, system_name,
                            controlling_power, control_state, control_progress,
                            reinforcement_points, undermining_points, powers,
                            observed_at, cycle_start, game_build, source_payload,
                            confidence, value_provenance
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7,
                            $8::jsonb, $9::jsonb, $10::jsonb, $11::jsonb,
                            $12::jsonb, $13::jsonb, $14, $15, $16,
                            $17::jsonb, $18, $19::jsonb
                        )
                        ON CONFLICT (commander_key, source, source_record_hash) DO NOTHING
                        RETURNING id
                        ''',
                        request.commander_key,
                        request.source,
                        request.source_version,
                        event.observation_key,
                        parsed.event_type,
                        parsed.system_address,
                        parsed.system_name,
                        values.get('controlling_power'),
                        values.get('control_state'),
                        values.get('control_progress'),
                        values.get('reinforcement_points'),
                        values.get('undermining_points'),
                        values.get('powers', []),
                        parsed.observed_at,
                        parsed.cycle_start,
                        parsed.game_build,
                        parsed.source_payload,
                        parsed.confidence,
                        parsed.value_provenance,
                    )
                    if inserted is None:
                        duplicates += 1
                    else:
                        system_staged += 1
                else:
                    inserted = await conn.fetchval(
                        '''
                        INSERT INTO commander_powerplay_events (
                            commander_key, source, source_version, source_record_hash,
                            event_type, observed_at, cycle_start, game_build,
                            source_payload, confidence, value_provenance
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8,
                            $9::jsonb, $10, $11::jsonb
                        )
                        ON CONFLICT (commander_key, source, source_record_hash) DO NOTHING
                        RETURNING id
                        ''',
                        request.commander_key,
                        request.source,
                        request.source_version,
                        event.observation_key,
                        parsed.event_type,
                        parsed.observed_at,
                        parsed.cycle_start,
                        parsed.game_build,
                        parsed.source_payload,
                        parsed.confidence,
                        parsed.value_provenance,
                    )
                    if inserted is None:
                        duplicates += 1
                    else:
                        commander_staged += 1

            await _rebuild_commander_state(conn, request.commander_key)
            cycles_versioned = await _version_cycle_snapshots(conn, request.commander_key)

    return PowerplayImportReceipt(
        commander_key=request.commander_key,
        events_received=len(request.events),
        system_observations_staged=system_staged,
        commander_events_staged=commander_staged,
        duplicates_skipped=duplicates,
        cycles_versioned=cycles_versioned,
    )


def _uncertainty(observed_at: datetime, confidence: float, now: datetime) -> tuple[str, list[str]]:
    reasons = ['raw-journal-values-not-normalised']
    age = now - observed_at
    if age.total_seconds() < 0:
        reasons.append('observation-timestamp-is-in-the-future')
        return 'high', reasons
    if observed_at < cycle_start_for(now):
        reasons.append('not-observed-in-current-powerplay-cycle')
        return 'high', reasons
    if age > timedelta(days=2) or confidence < 0.8:
        reasons.append('observation-is-aging')
        return 'medium', reasons
    return 'low', reasons


async def get_current_systems(
    pool: asyncpg.Pool,
    commander_key: str,
    *,
    limit: int = DEFAULT_SYSTEM_LIMIT,
    now: datetime | None = None,
) -> PowerplaySystemsResponse:
    limit = max(1, min(limit, MAX_SYSTEM_LIMIT))
    current_time = now or _utc_now()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            '''
            WITH latest AS (
                SELECT DISTINCT ON (system_address) *
                FROM powerplay_observations
                WHERE commander_key = $1
                ORDER BY system_address, observed_at DESC, id DESC
            )
            SELECT latest.*, systems.name AS canonical_name,
                   systems.x, systems.y, systems.z
            FROM latest
            LEFT JOIN systems ON systems.id64 = latest.system_address
            ORDER BY latest.observed_at DESC, latest.system_address
            LIMIT $2
            ''',
            commander_key,
            limit + 1,
        )
    truncated = len(rows) > limit
    systems = []
    for row in rows[:limit]:
        confidence = float(row['confidence'])
        uncertainty, reasons = _uncertainty(row['observed_at'], confidence, current_time)
        systems.append(PowerplaySystemState(
            system_address=int(row['system_address']),
            system_name=row['system_name'] or row['canonical_name'],
            x=float(row['x']) if row['x'] is not None else None,
            y=float(row['y']) if row['y'] is not None else None,
            z=float(row['z']) if row['z'] is not None else None,
            controlling_power=_json(row['controlling_power']),
            control_state=_json(row['control_state']),
            control_progress=_json(row['control_progress']),
            reinforcement_points=_json(row['reinforcement_points']),
            undermining_points=_json(row['undermining_points']),
            powers=list(_json(row['powers'], [])),
            observed_at=_iso(row['observed_at']),
            cycle_start=_iso(row['cycle_start']),
            game_build=row['game_build'],
            source_payload=dict(_json(row['source_payload'], {})),
            observation_age_seconds=max(0, int((current_time - row['observed_at']).total_seconds())),
            uncertainty=uncertainty,
            uncertainty_reasons=reasons,
            value_provenance=dict(_json(row['value_provenance'], {})),
        ))
    return PowerplaySystemsResponse(
        commander_key=commander_key,
        systems=systems,
        count=len(systems),
        truncated=truncated,
    )


async def get_commander_state(
    pool: asyncpg.Pool,
    commander_key: str,
    *,
    now: datetime | None = None,
) -> CommanderPowerplayResponse:
    current_cycle = cycle_start_for(now or _utc_now())
    async with pool.acquire() as conn:
        state = await conn.fetchrow(
            'SELECT * FROM commander_powerplay_state WHERE commander_key = $1',
            commander_key,
        )
        rows = await conn.fetch(
            '''
            SELECT observed_at, source_payload, source, source_version, confidence
            FROM commander_powerplay_events
            WHERE commander_key = $1
              AND event_type = 'PowerplayMerits'
              AND observed_at >= $2
              AND observed_at < $2::timestamptz + INTERVAL '7 days'
            ORDER BY observed_at DESC, id DESC
            LIMIT 100
            ''',
            commander_key,
            current_cycle,
        )
    earned: Any = 0
    contributions: list[PowerplayContribution] = []
    for row in rows:
        payload = dict(_json(row['source_payload'], {}))
        gain = payload.get('MeritsGained')
        if _number(gain):
            earned += gain
        contributions.append(PowerplayContribution(
            observed_at=_iso(row['observed_at']),
            power=payload.get('Power'),
            merits_gained=gain,
            total_merits=payload.get('TotalMerits'),
            source=str(row['source']),
            version=str(row['source_version']),
            confidence=float(row['confidence']),
        ))
    return CommanderPowerplayResponse(
        commander_key=commander_key,
        pledge=_json(state['pledge']) if state else None,
        rank=_json(state['rank']) if state else None,
        merits=_json(state['merits']) if state else None,
        last_updated=_iso(state['last_updated']) if state else None,
        cycle_start=_iso(current_cycle),
        cycle_merits_earned=earned,
        value_provenance=dict(_json(state['value_provenance'], {})) if state else {},
        recent_contributions=contributions,
    )


async def get_history(
    pool: asyncpg.Pool,
    commander_key: str,
    *,
    cycle_limit: int = 52,
    change_limit: int = 2_000,
) -> PowerplayHistoryResponse:
    async with pool.acquire() as conn:
        cycle_rows = await conn.fetch(
            '''
            SELECT DISTINCT ON (cycle_start)
                   week, cycle_start, captured_at, control_snapshot, snapshot_hash,
                   source, source_version, confidence
            FROM powerplay_cycles
            WHERE commander_key = $1
            ORDER BY cycle_start DESC, captured_at DESC, id DESC
            LIMIT $2
            ''',
            commander_key,
            max(1, min(cycle_limit, 260)),
        )
        observation_rows = await conn.fetch(
            '''
            SELECT system_address, system_name, controlling_power, control_state,
                   control_progress, reinforcement_points, undermining_points,
                   powers, observed_at, cycle_start, source, source_version,
                   confidence
            FROM powerplay_observations
            WHERE commander_key = $1
            ORDER BY observed_at ASC, id ASC
            LIMIT $2
            ''',
            commander_key,
            max(1, min(change_limit, 20_000)),
        )

    cycles = [PowerplayCycleSnapshot(
        week=str(row['week']),
        cycle_start=_iso(row['cycle_start']),
        captured_at=_iso(row['captured_at']),
        control_snapshot=dict(_json(row['control_snapshot'], {})),
        snapshot_hash=str(row['snapshot_hash']),
        source=str(row['source']),
        version=str(row['source_version']),
        confidence=float(row['confidence']),
    ) for row in cycle_rows]

    previous: dict[int, dict[str, Any]] = {}
    changes: list[PowerplayChangeEvent] = []
    fields = (
        'controlling_power', 'control_state', 'control_progress',
        'reinforcement_points', 'undermining_points', 'powers',
    )
    for row in observation_rows:
        address = int(row['system_address'])
        current = {field: _json(row[field], [] if field == 'powers' else None) for field in fields}
        before = previous.get(address, {})
        delta = {
            field: {'from': before.get(field), 'to': current[field]}
            for field in fields
            if field not in before or before.get(field) != current[field]
        }
        if delta:
            changes.append(PowerplayChangeEvent(
                system_address=address,
                system_name=row['system_name'],
                observed_at=_iso(row['observed_at']),
                cycle_start=_iso(row['cycle_start']),
                changes=delta,
                source=str(row['source']),
                version=str(row['source_version']),
                confidence=float(row['confidence']),
            ))
        previous[address] = current

    return PowerplayHistoryResponse(
        commander_key=commander_key,
        cycles=cycles,
        change_events=list(reversed(changes[-change_limit:])),
    )
