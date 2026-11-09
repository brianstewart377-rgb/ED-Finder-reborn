from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import asyncpg

from edfinder_api.routes.api_models import (
    ExpeditionImport,
    PlannedRouteImport,
    RouteAlignment,
    RouteDetail,
    RouteEvent,
    RouteListResponse,
    RouteSummary,
    RouteWaypoint,
)


def _json(value: object, fallback: object) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        return json.loads(value)
    return value


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _coords(value: object) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        return float(value[0]), float(value[1]), float(value[2])
    except (TypeError, ValueError):
        return None


def _distance(a: dict[str, Any] | None, b: dict[str, Any] | None) -> float | None:
    if not a or not b:
        return None
    values = (a.get('x'), a.get('y'), a.get('z'), b.get('x'), b.get('y'), b.get('z'))
    if any(value is None for value in values):
        return None
    return math.dist(
        (float(a['x']), float(a['y']), float(a['z'])),
        (float(b['x']), float(b['y']), float(b['z'])),
    )


def _same_system(waypoint: dict[str, Any], event: dict[str, Any]) -> bool:
    waypoint_id = waypoint.get('system_id64')
    event_id = event.get('system_id64')
    if waypoint_id is not None and event_id is not None:
        return int(waypoint_id) == int(event_id)
    return str(waypoint.get('system_name') or '').casefold() == str(event.get('system_name') or '').casefold()


async def _hydrate_waypoints(conn: asyncpg.Connection, raw: object) -> list[dict[str, Any]]:
    waypoints = [dict(item) for item in list(_json(raw, [])) if isinstance(item, dict)]
    missing_ids = [int(item['system_id64']) for item in waypoints if item.get('system_id64') and item.get('x') is None]
    missing_names = [str(item['system_name']) for item in waypoints if not item.get('system_id64') and item.get('x') is None]
    rows = []
    if missing_ids:
        rows.extend(await conn.fetch('SELECT id64, name, x, y, z FROM systems WHERE id64 = ANY($1::bigint[])', missing_ids))
    if missing_names:
        rows.extend(await conn.fetch('SELECT id64, name, x, y, z FROM systems WHERE name = ANY($1::text[])', missing_names))
    by_id = {int(row['id64']): row for row in rows}
    by_name = {str(row['name']).casefold(): row for row in rows}
    previous: dict[str, Any] | None = None
    for index, waypoint in enumerate(waypoints):
        row = by_id.get(int(waypoint['system_id64'])) if waypoint.get('system_id64') else by_name.get(str(waypoint.get('system_name', '')).casefold())
        if row:
            waypoint['system_id64'] = int(row['id64'])
            waypoint['system_name'] = str(row['name'])
            if row['x'] is not None and row['y'] is not None and row['z'] is not None:
                waypoint.update(x=float(row['x']), y=float(row['y']), z=float(row['z']))
        waypoint['order'] = index
        if waypoint.get('distance_from_previous') is None and previous is not None:
            waypoint['distance_from_previous'] = _distance(previous, waypoint)
        waypoint.setdefault('bookmarked', False)
        waypoint.setdefault('notes', None)
        previous = waypoint
    return waypoints


async def _upsert_route(
    conn: asyncpg.Connection,
    *,
    commander_id: str,
    name: str,
    source: str,
    route_type: str,
    external_id: str | None,
    waypoints: list[dict[str, Any]],
    metadata: dict[str, Any],
    route_id: UUID | None = None,
) -> UUID:
    hydrated = await _hydrate_waypoints(conn, waypoints)
    identifier = route_id or uuid4()
    if external_id:
        existing = await conn.fetchval(
            'SELECT route_id FROM routes WHERE commander_id = $1 AND source = $2 AND external_id = $3',
            commander_id, source, external_id,
        )
        if existing:
            identifier = existing
    await conn.execute(
        '''
        INSERT INTO routes (route_id, name, source, waypoints, commander_id, type, external_id, metadata)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8::jsonb)
        ON CONFLICT (route_id) DO UPDATE SET
            name = EXCLUDED.name,
            waypoints = CASE
                WHEN routes.type = 'personal' AND jsonb_array_length(EXCLUDED.waypoints) = 0
                    THEN routes.waypoints
                ELSE EXCLUDED.waypoints
            END,
            external_id = COALESCE(EXCLUDED.external_id, routes.external_id),
            metadata = routes.metadata || EXCLUDED.metadata,
            updated_at = NOW()
        ''',
        identifier, name, source, hydrated, commander_id, route_type, external_id, metadata,
    )
    return identifier


async def import_spansh_route(pool: asyncpg.Pool, request: PlannedRouteImport) -> RouteDetail:
    async with pool.acquire() as conn:
        async with conn.transaction():
            route_id = await _upsert_route(
                conn,
                commander_id=request.commander_id,
                name=request.name,
                source='spansh',
                route_type='spansh',
                external_id=request.external_id,
                waypoints=[item.model_dump(mode='json') for item in request.waypoints],
                metadata={**request.metadata, 'route_mode': request.route_mode},
            )
    detail = await get_route(pool, str(route_id), request.commander_id)
    assert detail is not None
    return detail


async def import_expedition(pool: asyncpg.Pool, request: ExpeditionImport) -> RouteDetail:
    metadata = {
        **request.metadata,
        'description': request.description,
        'organizer': request.organizer,
        'departure_at': request.departure_at.isoformat() if request.departure_at else None,
        'return_at': request.return_at.isoformat() if request.return_at else None,
    }
    waypoints = [
        {**item.model_dump(mode='json'), 'bookmarked': True}
        for item in request.waypoints
    ]
    async with pool.acquire() as conn:
        async with conn.transaction():
            route_id = await _upsert_route(
                conn,
                commander_id=request.commander_id,
                name=request.name,
                source='expedition',
                route_type='expedition',
                external_id=request.external_id,
                waypoints=waypoints,
                metadata=metadata,
            )
    detail = await get_route(pool, str(route_id), request.commander_id)
    assert detail is not None
    return detail


async def _append_event(
    conn: asyncpg.Connection,
    route_id: UUID,
    event: dict[str, Any],
    *,
    planned_waypoints: list[dict[str, Any]] | None = None,
) -> None:
    await conn.fetchrow('SELECT route_id FROM routes WHERE route_id = $1 FOR UPDATE', route_id)
    source_key = event.get('source_event_key')
    if source_key and await conn.fetchval(
        'SELECT 1 FROM route_events WHERE route_id = $1 AND source_event_key = $2', route_id, source_key,
    ):
        return
    event_order = int(await conn.fetchval(
        'SELECT COALESCE(MAX(event_order), -1) + 1 FROM route_events WHERE route_id = $1', route_id,
    ))
    distance_from_planned = None
    if planned_waypoints:
        distances = [distance for distance in (_distance(event, waypoint) for waypoint in planned_waypoints) if distance is not None]
        distance_from_planned = min(distances) if distances else None
    await conn.execute(
        '''
        INSERT INTO route_events (
            route_id, system_id64, system_name, x, y, z, visited_at,
            distance_from_planned, event_order, source_event_key, context
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
        ''',
        route_id, event.get('system_id64'), event['system_name'], event.get('x'), event.get('y'),
        event.get('z'), event['visited_at'], distance_from_planned, event_order, source_key,
        event.get('context', {}),
    )


def _journal_waypoints(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(payload.get('Route') or []):
        if not isinstance(raw, dict):
            continue
        coords = _coords(raw.get('StarPos'))
        result.append({
            'order': index,
            'system_id64': raw.get('SystemAddress'),
            'system_name': raw.get('StarSystem') or raw.get('SystemName') or f'Waypoint {index + 1}',
            'x': coords[0] if coords else None,
            'y': coords[1] if coords else None,
            'z': coords[2] if coords else None,
            'distance_from_previous': None,
            'bookmarked': bool(raw.get('Bookmark') or raw.get('Bookmarked')),
            'notes': raw.get('Notes'),
        })
    return result


async def consume_journal_observations(
    conn: asyncpg.Connection,
    commander_id: str,
    observations: Iterable[object],
) -> None:
    ordered = sorted(
        observations,
        key=lambda item: getattr(item, 'observed_at', None) or datetime.min.replace(tzinfo=timezone.utc),
    )
    personal_id = uuid5(NAMESPACE_URL, f'edfinder:personal-history:{commander_id}')
    for observation in ordered:
        event_type = str(getattr(observation, 'event_type'))
        payload = dict(getattr(observation, 'payload') or {})
        observed_at = getattr(observation, 'observed_at', None) or datetime.now(timezone.utc)
        source_key = str(getattr(observation, 'observation_key'))
        if event_type == 'NavRoute':
            waypoints = _journal_waypoints(payload)
            if not waypoints:
                continue
            await conn.execute(
                '''UPDATE routes SET metadata = metadata || '{"status":"superseded"}'::jsonb, updated_at = NOW()
                   WHERE commander_id = $1 AND type = 'journal' AND metadata->>'status' = 'active' ''',
                commander_id,
            )
            await _upsert_route(
                conn,
                commander_id=commander_id,
                name=str(payload.get('RouteName') or f'Journal route {observed_at:%Y-%m-%d %H:%M}'),
                source='journal',
                route_type='journal',
                external_id=source_key,
                waypoints=waypoints,
                metadata={'status': 'active', 'journal_event': 'NavRoute'},
            )
            continue
        if event_type == 'NavRouteClear':
            await conn.execute(
                '''UPDATE routes SET metadata = metadata || $2::jsonb, updated_at = NOW()
                   WHERE commander_id = $1 AND type = 'journal' AND metadata->>'status' = 'active' ''',
                commander_id, {'status': 'cleared', 'cleared_at': observed_at.isoformat()},
            )
            continue
        if event_type not in {'FSDJump', 'CarrierJump', 'Location'}:
            continue
        system_name = str(getattr(observation, 'system_name', None) or payload.get('StarSystem') or 'Unknown system')
        coords = _coords(payload.get('StarPos'))
        actual = {
            'system_id64': int(getattr(observation, 'system_id64', None) or payload.get('SystemAddress'))
                if (getattr(observation, 'system_id64', None) or payload.get('SystemAddress')) is not None
                else None,
            'system_name': system_name,
            'x': coords[0] if coords else None,
            'y': coords[1] if coords else None,
            'z': coords[2] if coords else None,
            'visited_at': observed_at,
            'source_event_key': source_key,
            'context': {'event_type': event_type, 'source_file': getattr(observation, 'source_file', None)},
        }
        await _upsert_route(
            conn,
            commander_id=commander_id,
            name='Personal jump history',
            source='journal',
            route_type='personal',
            external_id='personal-jump-history',
            waypoints=[],
            metadata={'rolling_history': True},
            route_id=personal_id,
        )
        await _append_event(conn, personal_id, actual)
        previous_waypoint = await conn.fetchval(
            "SELECT waypoints->-1 FROM routes WHERE route_id = $1",
            personal_id,
        )
        personal_order = int(await conn.fetchval(
            "SELECT jsonb_array_length(waypoints) FROM routes WHERE route_id = $1",
            personal_id,
        ))
        previous_waypoint_dict = dict(_json(previous_waypoint, {})) if previous_waypoint else None
        await conn.execute(
            '''UPDATE routes
               SET waypoints = waypoints || $2::jsonb, updated_at = NOW()
               WHERE route_id = $1
                 AND NOT EXISTS (
                   SELECT 1 FROM jsonb_array_elements(waypoints) item
                   WHERE item->>'source_event_key' = $3
                 )''',
            personal_id,
            [{
                'order': personal_order,
                'system_id64': actual['system_id64'],
                'system_name': actual['system_name'],
                'x': actual['x'], 'y': actual['y'], 'z': actual['z'],
                'distance_from_previous': _distance(previous_waypoint_dict, actual),
                'bookmarked': False,
                'notes': None,
                'source_event_key': source_key,
            }],
            source_key,
        )
        active = await conn.fetchrow(
            '''SELECT route_id, waypoints FROM routes
               WHERE commander_id = $1 AND type = 'journal' AND metadata->>'status' = 'active'
               ORDER BY updated_at DESC LIMIT 1''',
            commander_id,
        )
        if active:
            await _append_event(
                conn,
                active['route_id'],
                actual,
                planned_waypoints=[dict(item) for item in _json(active['waypoints'], [])],
            )


def _detail_from_rows(row: asyncpg.Record, event_rows: Iterable[asyncpg.Record], waypoints: list[dict[str, Any]]) -> RouteDetail:
    events = [dict(event) for event in event_rows]
    matched_events: set[int] = set()
    alignment: list[RouteAlignment] = []
    for index, waypoint in enumerate(waypoints):
        match_index = next(
            (event_index for event_index, event in enumerate(events) if event_index not in matched_events and _same_system(waypoint, event)),
            None,
        )
        event = events[match_index] if match_index is not None else None
        if match_index is not None:
            matched_events.add(match_index)
        alignment.append(RouteAlignment(
            planned_order=index,
            waypoint=RouteWaypoint.model_validate({**waypoint, 'order': index}),
            visited=event is not None,
            actual_event_order=int(event['event_order']) if event else None,
            visited_at=_iso(event['visited_at']) if event else None,
            distance_from_planned=float(event['distance_from_planned']) if event and event['distance_from_planned'] is not None else None,
        ))
    waypoint_count = len(waypoints)
    visited_count = len(matched_events) if waypoint_count else len(events)
    completion = 100.0 if waypoint_count == 0 and events else (visited_count / waypoint_count * 100 if waypoint_count else 0.0)
    current_index = next((item.planned_order for item in alignment if not item.visited), None)
    remaining = 0.0
    if waypoint_count and current_index is not None:
        last_event = events[-1] if events else None
        first_leg = _distance(last_event, waypoints[current_index])
        if first_leg is not None:
            remaining += first_leg
        elif current_index > 0:
            remaining += float(waypoints[current_index].get('distance_from_previous') or 0)
        for index in range(current_index + 1, waypoint_count):
            remaining += float(waypoints[index].get('distance_from_previous') or _distance(waypoints[index - 1], waypoints[index]) or 0)
    metadata = dict(_json(row['metadata'], {}))
    route_events = [RouteEvent(
        system_id64=int(event['system_id64']) if event['system_id64'] is not None else None,
        system_name=str(event['system_name']),
        x=float(event['x']) if event['x'] is not None else None,
        y=float(event['y']) if event['y'] is not None else None,
        z=float(event['z']) if event['z'] is not None else None,
        visited_at=_iso(event['visited_at']),
        distance_from_planned=float(event['distance_from_planned']) if event['distance_from_planned'] is not None else None,
        order=int(event['event_order']),
        context=dict(_json(event['context'], {})),
    ) for event in events]
    return RouteDetail(
        route_id=str(row['route_id']), name=str(row['name']), source=str(row['source']), type=str(row['type']),
        created_at=_iso(row['created_at']), updated_at=_iso(row['updated_at']), waypoint_count=waypoint_count,
        visited_count=visited_count, completion_percent=round(completion, 1), remaining_distance=round(remaining, 2),
        current_waypoint_index=current_index, metadata=metadata,
        waypoints=[RouteWaypoint.model_validate({**waypoint, 'order': index}) for index, waypoint in enumerate(waypoints)],
        events=route_events, planned_actual_alignment=alignment,
    )


async def _get_route_with_conn(
    conn: asyncpg.Connection,
    route_id: str,
    commander_id: str,
    *,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> RouteDetail | None:
    try:
        identifier = UUID(route_id)
    except ValueError:
        return None
    row = await conn.fetchrow('SELECT * FROM routes WHERE route_id = $1 AND commander_id = $2', identifier, commander_id)
    if row is None:
        return None
    waypoints = await _hydrate_waypoints(conn, row['waypoints'])
    event_rows = await conn.fetch(
        '''SELECT * FROM route_events
           WHERE route_id = $1
             AND ($2::timestamptz IS NULL OR visited_at >= $2)
             AND ($3::timestamptz IS NULL OR visited_at <= $3)
           ORDER BY visited_at ASC, event_order ASC''',
        identifier, from_date, to_date,
    )
    if str(row['type']) == 'personal' and (from_date or to_date):
        event_keys = {str(event['source_event_key']) for event in event_rows if event['source_event_key']}
        waypoints = [item for item in waypoints if str(item.get('source_event_key')) in event_keys]
    return _detail_from_rows(row, event_rows, waypoints)


async def get_route(pool: asyncpg.Pool, route_id: str, commander_id: str) -> RouteDetail | None:
    async with pool.acquire() as conn:
        return await _get_route_with_conn(conn, route_id, commander_id)


async def list_routes(pool: asyncpg.Pool, commander_id: str, route_type: str | None = None) -> RouteListResponse:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            '''SELECT route_id FROM routes
               WHERE commander_id = $1 AND ($2::text IS NULL OR type = $2)
               ORDER BY updated_at DESC LIMIT 200''',
            commander_id, route_type,
        )
        details = [await _get_route_with_conn(conn, str(row['route_id']), commander_id) for row in rows]
    summaries = [RouteSummary.model_validate(detail.model_dump(exclude={'waypoints', 'events', 'planned_actual_alignment'})) for detail in details if detail]
    return RouteListResponse(routes=summaries, count=len(summaries))


async def get_personal_trail(
    pool: asyncpg.Pool,
    commander_id: str,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> RouteDetail | None:
    async with pool.acquire() as conn:
        route_id = await conn.fetchval(
            "SELECT route_id FROM routes WHERE commander_id = $1 AND type = 'personal' ORDER BY updated_at DESC LIMIT 1",
            commander_id,
        )
        if route_id is None:
            return None
        return await _get_route_with_conn(conn, str(route_id), commander_id, from_date=from_date, to_date=to_date)
