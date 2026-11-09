"""Bounded own-account travel heatmap from imported V3 journal travel events.

Replaces the V2 ``exploration_visits`` read (keyed by the legacy sync key),
which V3 production does not have and the V3 journal import never writes.
The source is ``v3_private.journal_event``, scoped by the signed-in
account's ``owner_account_id`` exactly like the other ``/api/v1/journal/*``
personal projections; nothing client-supplied selects whose history is read.

Travel events carry a star position. ``FSDJump`` and ``CarrierJump`` are
one row per arrival (their semantic identity includes the timestamp) and are
what ``visit_count`` counts. ``Location`` (session start/respawn) is
deduplicated by position rather than time, so it only proves presence: a
system seen only through ``Location`` counts as at least one visit. ``StarPos`` is journal-verbatim and unvalidated at import, so
every coordinate is shape- and range-checked in SQL and malformed rows are
dropped rather than failing the whole read.

Bounds: at most ``MAX_TRAVEL_EVENTS`` travel events are considered (over the
bound is an explicit ``TravelHeatmapUnavailable`` rather than a silently
partial map), one statement timeout, and at most ``limit`` aggregated points
(``truncated`` reports the cut). Aggregation is server-side: per system in
marker mode, per cubic cell in density mode — the same zoom thresholds the
V2 viewport-visits contract used.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import asyncpg

MAX_TRAVEL_EVENTS = 1_000_000
QUERY_TIMEOUT_SECONDS = 5.0
TRAVEL_EVENT_TYPES = ('FSDJump', 'CarrierJump', 'Location')
# Galaxy coordinates sit within roughly ±70,000 ly; anything outside this is
# corrupt input, never a real star position.
MAX_ABS_COORDINATE_LY = 100_000

# Marker mode at close range, density cells further out (V2 parity).
MARKER_MODE_MAX_ZOOM = 8.0


def cell_size_for_zoom(zoom: float) -> float | None:
    """None in marker mode, else the cubic density cell edge in ly."""
    if zoom <= MARKER_MODE_MAX_ZOOM:
        return None
    return float(min(5_000, max(50, round(zoom * 8 / 50) * 50)))


# One statement: the bounded owner-scoped travel scan, validated coordinates,
# the viewport filter and the aggregation. $1 account, $2 event types,
# $3 scan bound (+1 to detect overflow), $4..$9 viewport box, $10 coordinate
# bound, $11 cell size (NULL = markers), $12 point limit (+1 to detect cut).
_TRAVEL_SQL = """
WITH travel AS MATERIALIZED (
    SELECT e.event_key->>'SystemAddress' AS system_id,
           e.event_payload->'StarPos' AS star_pos,
           e.event_payload->>'StarSystem' AS system_name,
           e.event_timestamp,
           e.event_type <> 'Location' AS is_jump
      FROM v3_private.journal_event e
     WHERE e.owner_account_id = $1
       AND e.event_type = ANY($2::text[])
     LIMIT $3::bigint
), input_size AS (
    SELECT count(*) AS travel_events FROM travel
), shaped AS (
    -- CASE forces evaluation order: never index or cast a non-array or a
    -- non-number StarPos element.
    SELECT system_id, system_name, event_timestamp, is_jump,
           CASE WHEN jsonb_typeof(star_pos) = 'array' THEN
               CASE WHEN jsonb_array_length(star_pos) = 3
                     AND jsonb_typeof(star_pos->0) = 'number'
                     AND jsonb_typeof(star_pos->1) = 'number'
                     AND jsonb_typeof(star_pos->2) = 'number'
                    THEN ARRAY[(star_pos->0)::numeric, (star_pos->1)::numeric,
                               (star_pos->2)::numeric]
               END
           END AS pos
      FROM travel
     WHERE (SELECT travel_events FROM input_size) < $3::bigint
), valid AS MATERIALIZED (
    SELECT system_id, system_name, event_timestamp, is_jump,
           pos[1]::double precision AS x,
           pos[2]::double precision AS y,
           pos[3]::double precision AS z
      FROM (
        SELECT system_id, system_name, event_timestamp, is_jump,
               CASE WHEN abs(pos[1]) <= $10::numeric AND abs(pos[2]) <= $10::numeric
                     AND abs(pos[3]) <= $10::numeric
                    THEN pos END AS pos
          FROM shaped
         WHERE pos IS NOT NULL
           AND system_id ~ '^(0|[1-9][0-9]{0,19})$'
           AND (length(system_id) < 20 OR system_id COLLATE "C" <= '18446744073709551615')
      ) bounded
     WHERE pos IS NOT NULL
), in_view AS (
    SELECT * FROM valid
     WHERE x BETWEEN $4::float8 AND $5::float8 AND y BETWEEN $6::float8 AND $7::float8
       AND z BETWEEN $8::float8 AND $9::float8
), per_system AS (
    -- Visits are jump arrivals (FSDJump/CarrierJump carry a timestamped
    -- identity). Location events are deduplicated by position, not time, so
    -- they only prove presence: a Location-only system counts as at least one
    -- visit and never inflates or hides a jump count.
    SELECT system_id,
           max(system_name) AS system_name,
           avg(x) AS x, avg(y) AS y, avg(z) AS z,
           greatest(count(*) FILTER (WHERE is_jump), 1)::bigint AS visit_count,
           min(event_timestamp) AS first_visited_at,
           max(event_timestamp) AS last_visited_at
      FROM in_view
     GROUP BY system_id
), markers AS (
    SELECT 'marker'::text AS kind, system_id, system_name, x, y, z,
           visit_count, first_visited_at, last_visited_at
      FROM per_system
     WHERE $11::float8 IS NULL
), cells AS (
    SELECT 'density'::text AS kind, NULL::text AS system_id, NULL::text AS system_name,
           floor(x / $11::float8) * $11::float8 + $11::float8 / 2 AS x,
           floor(y / $11::float8) * $11::float8 + $11::float8 / 2 AS y,
           floor(z / $11::float8) * $11::float8 + $11::float8 / 2 AS z,
           sum(visit_count)::bigint AS visit_count,
           min(first_visited_at) AS first_visited_at,
           max(last_visited_at) AS last_visited_at
      FROM per_system
     WHERE $11::float8 IS NOT NULL
     GROUP BY floor(x / $11::float8), floor(y / $11::float8), floor(z / $11::float8)
), points AS (
    SELECT * FROM markers
    UNION ALL
    SELECT * FROM cells
)
SELECT (SELECT travel_events FROM input_size) AS travel_events, p.*
  FROM (SELECT 1) anchor
  LEFT JOIN LATERAL (
      SELECT * FROM points
       ORDER BY CASE WHEN kind = 'marker' THEN 0 ELSE visit_count END DESC,
                last_visited_at DESC, system_id, x, y, z
       LIMIT $12::bigint
  ) p ON TRUE
"""


class TravelHeatmapUnavailable(RuntimeError):
    """The heatmap could not be computed inside its resource bound."""


async def travel_heatmap(
    pool: asyncpg.Pool,
    account_id: uuid.UUID,
    *,
    min_x: float, max_x: float,
    min_y: float, max_y: float,
    min_z: float, max_z: float,
    zoom: float,
    limit: int,
) -> dict[str, Any]:
    lo_x, hi_x = sorted((min_x, max_x))
    lo_y, hi_y = sorted((min_y, max_y))
    lo_z, hi_z = sorted((min_z, max_z))
    cell_size = cell_size_for_zoom(zoom)
    # The whole pool operation is bounded, not only the statement: a saturated
    # pool must become the retryable 503 too (Pool.fetch does not apply its
    # timeout to connection acquisition).
    rows = await asyncio.wait_for(
        pool.fetch(
            _TRAVEL_SQL, account_id, list(TRAVEL_EVENT_TYPES), MAX_TRAVEL_EVENTS + 1,
            lo_x, hi_x, lo_y, hi_y, lo_z, hi_z, MAX_ABS_COORDINATE_LY, cell_size, limit + 1,
            timeout=QUERY_TIMEOUT_SECONDS,
        ),
        timeout=QUERY_TIMEOUT_SECONDS,
    )
    if not rows or rows[0]['travel_events'] > MAX_TRAVEL_EVENTS:
        raise TravelHeatmapUnavailable('Travel history exceeds the heatmap bound')
    points = [row for row in rows if row['kind'] is not None]
    truncated = len(points) > limit
    points = points[:limit]
    return {
        'mode': 'markers' if cell_size is None else 'density',
        'cell_size_ly': cell_size,
        'truncated': truncated,
        'points': [
            {
                'kind': row['kind'],
                'system_id64': row['system_id'],
                'system_name': row['system_name'],
                'x': float(row['x']),
                'y': float(row['y']),
                'z': float(row['z']),
                'visit_count': int(row['visit_count']),
                'first_visited_at': row['first_visited_at'].isoformat(),
                'last_visited_at': row['last_visited_at'].isoformat(),
            }
            for row in points
        ],
    }
