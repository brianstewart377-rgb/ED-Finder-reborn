"""Account-scoped V3 journal projections (personal exploration views).

Counters are diagnostics only — they feed personal summaries, never
confidence inputs. Every query is scoped by ``owner_account_id = $1``
(account isolation is a hard invariant of the private trust zone).
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

# Body-observation event types (the unique-bodies and scanned-bodies
# projections operate over exactly this set).
BODY_OBSERVATION_TYPES: frozenset[str] = frozenset({
    'Scan', 'FSSBodySignals', 'SAASignalsFound', 'SAAScanComplete',
    'Touchdown', 'Liftoff', 'ApproachBody', 'LeaveBody', 'Location',
    'Disembark', 'Embark', 'Screenshot',
})

# Interpolated into the two SQL statements below via f-strings. The
# provenance is constant-only: this literal is built at import time from the
# frozen compile-time BODY_OBSERVATION_TYPES set above — never from user or
# database data — so the interpolation is not an injection vector (the
# review's parameterization audit flags f-string SQL, and this comment is
# the recorded disposition for these two sites).
_BODY_OBSERVATION_SQL = ', '.join(repr(value) for value in sorted(BODY_OBSERVATION_TYPES))

_UNIQUE_BIO_OBSERVATIONS_SQL = '''
    SELECT COUNT(*)::int
      FROM (
        -- ScanOrganic: distinct (system, body, genus, species, variant).
        -- UNION (not UNION ALL) dedupes: repeated ScanType stages of the
        -- same organism (Log + Sample) are ONE unique bio observation, and
        -- a biology CodexEntry row can coincide with a ScanOrganic row.
        SELECT event_key->>'SystemAddress' AS system_id64,
               event_key->>'BodyID' AS body_id,
               event_payload->>'Genus' AS genus,
               event_payload->>'Species' AS species,
               event_payload->>'Variant' AS variant
          FROM v3_private.journal_event
         WHERE owner_account_id = $1 AND event_type = 'ScanOrganic'
           AND event_payload->>'Genus' IS NOT NULL
        UNION
        -- biology CodexEntry name tokens (category carries the biology flag)
        SELECT event_key->>'SystemAddress', event_key->>'BodyID',
               event_payload->>'Name', NULL, NULL
          FROM v3_private.journal_event
         WHERE owner_account_id = $1 AND event_type = 'CodexEntry'
           AND COALESCE(event_payload->>'Category', '') ILIKE '%biology%'
        UNION
        -- SAASignalsFound Genuses: genus-level only
        SELECT event_key->>'SystemAddress', event_key->>'BodyID',
               genus.value, NULL, NULL
          FROM v3_private.journal_event,
               jsonb_array_elements_text(
                   COALESCE(event_payload->'Genuses', '[]'::jsonb)
               ) AS genus(value)
         WHERE owner_account_id = $1 AND event_type = 'SAASignalsFound'
      ) AS bio_observations
'''

_SYSTEMS_OBSERVED_SQL = '''
    SELECT COUNT(DISTINCT event_key->>'SystemAddress')::int
      FROM v3_private.journal_event
     WHERE owner_account_id = $1
       AND event_key ? 'SystemAddress'
'''


def _as_dict(row: Any) -> dict[str, Any]:
    """Normalize an asyncpg.Record or plain dict to a dict."""
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, 'items'):
        return dict(row.items())
    return dict(row)


def _as_object(value: object) -> object:
    """Normalize a jsonb value that may arrive as a JSON string."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


async def journal_summary(pool: asyncpg.Pool, account_id: object) -> dict[str, Any]:
    """Per-account diagnostic counters (§2.4 of the decision doc)."""
    async with pool.acquire() as conn:
        events_stored = int(await conn.fetchval(
            '''
            SELECT count(*)::int
              FROM v3_private.journal_event
             WHERE owner_account_id = $1
            ''',
            account_id,
        ) or 0)
        unique_bodies = int(await conn.fetchval(
            f'''
            SELECT COUNT(DISTINCT (event_key->>'SystemAddress',
                                   event_key->>'BodyID'))::int
              FROM v3_private.journal_event
             WHERE owner_account_id = $1
               AND event_type IN ({_BODY_OBSERVATION_SQL})
               AND event_key ? 'SystemAddress'
               AND event_key ? 'BodyID'
            ''',
            account_id,
        ) or 0)
        unique_bio_observations = int(await conn.fetchval(
            _UNIQUE_BIO_OBSERVATIONS_SQL,
            account_id,
        ) or 0)
        systems_observed = int(await conn.fetchval(
            _SYSTEMS_OBSERVED_SQL,
            account_id,
        ) or 0)
        last_row = await conn.fetchrow(
            '''
            SELECT max(imported_at) AS last_imported_at
              FROM v3_private.private_import
             WHERE owner_account_id = $1
            ''',
            account_id,
        )
        count_rows = await conn.fetch(
            '''
            SELECT event_type, count(*)::int AS event_count
              FROM v3_private.journal_event
             WHERE owner_account_id = $1
             GROUP BY event_type
             ORDER BY event_type
            ''',
            account_id,
        )
    last_imported_at = None
    if last_row is not None:
        last_imported_at = last_row.get('last_imported_at') if isinstance(last_row, dict) else last_row['last_imported_at']
    event_counts = {
        str(row['event_type']): int(row['event_count'] or 0)
        for row in count_rows
    }
    return {
        'events_stored': events_stored,
        'unique_bodies': unique_bodies,
        'unique_bio_observations': unique_bio_observations,
        'systems_observed': systems_observed,
        'last_imported_at': last_imported_at,
        'event_counts': event_counts,
    }


async def visited_systems(
    pool: asyncpg.Pool,
    account_id: object,
    offset: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Systems observed across travel/body events, most recent first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            '''
            SELECT
                event_key->>'SystemAddress' AS system_id64,
                MAX(COALESCE(event_payload->>'StarSystem', event_payload->>'System'))
                    AS system_name,
                MIN(event_timestamp) AS first_observed_at,
                MAX(event_timestamp) AS last_observed_at,
                COUNT(*)::int AS visit_count
              FROM v3_private.journal_event
             WHERE owner_account_id = $1
               AND event_key ? 'SystemAddress'
             GROUP BY event_key->>'SystemAddress'
             ORDER BY last_observed_at DESC, system_id64
             OFFSET $2 LIMIT $3
            ''',
            account_id,
            offset,
            limit,
        )
    return [_as_dict(row) for row in rows]


async def scanned_bodies(
    pool: asyncpg.Pool,
    account_id: object,
    offset: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Bodies observed across the body-observation event types."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f'''
            SELECT
                event_key->>'SystemAddress' AS system_id64,
                event_key->>'BodyID' AS body_id,
                MAX(event_payload->>'BodyName') AS body_name,
                MIN(event_timestamp) AS first_observed_at,
                MAX(event_timestamp) AS last_observed_at,
                COUNT(*)::int AS scan_count
              FROM v3_private.journal_event
             WHERE owner_account_id = $1
               AND event_type IN ({_BODY_OBSERVATION_SQL})
               AND event_key ? 'SystemAddress'
               AND event_key ? 'BodyID'
             GROUP BY event_key->>'SystemAddress', event_key->>'BodyID'
             ORDER BY last_observed_at DESC, system_id64, body_id
             OFFSET $2 LIMIT $3
            ''',
            account_id,
            offset,
            limit,
        )
    return [_as_dict(row) for row in rows]


async def codex_entries(pool: asyncpg.Pool, account_id: object) -> list[dict[str, Any]]:
    """CodexEntry observations deduplicated per EntryID."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            '''
            SELECT
                event_key->>'EntryID' AS entry_id,
                MAX(event_payload->>'Name') AS name,
                MAX(event_payload->>'Category') AS category,
                MAX(event_payload->>'SubCategory') AS subcategory,
                MAX(event_payload->>'Region') AS region,
                MAX(event_key->>'SystemAddress') AS system_id64,
                MAX(event_key->>'BodyID') AS body_id,
                MIN(event_timestamp) AS first_observed_at,
                MAX(event_timestamp) AS last_observed_at
              FROM v3_private.journal_event
             WHERE owner_account_id = $1 AND event_type = 'CodexEntry'
             GROUP BY event_key->>'EntryID'
             ORDER BY last_observed_at DESC, entry_id
            ''',
            account_id,
        )
    return [_as_dict(row) for row in rows]


async def organic_progress(pool: asyncpg.Pool, account_id: object) -> list[dict[str, Any]]:
    """ScanOrganic progress grouped by (genus, species, variant)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            '''
            SELECT
                event_payload->>'Genus' AS genus,
                event_payload->>'Species' AS species,
                event_payload->>'Variant' AS variant,
                ARRAY_AGG(DISTINCT event_payload->>'ScanType'
                          ORDER BY event_payload->>'ScanType') AS stages,
                MIN(event_timestamp) AS first_observed_at,
                MAX(event_timestamp) AS last_observed_at
              FROM v3_private.journal_event
             WHERE owner_account_id = $1 AND event_type = 'ScanOrganic'
             GROUP BY event_payload->>'Genus', event_payload->>'Species', event_payload->>'Variant'
             ORDER BY last_observed_at DESC, genus, species, variant
            ''',
            account_id,
        )
    return [_as_dict(row) for row in rows]


async def sale_history(
    pool: asyncpg.Pool,
    account_id: object,
    offset: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """SellOrganicData events, most recent first (payload retained for replay)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            '''
            SELECT
                event_payload->'BioData' AS bio_data,
                event_payload->>'MarketID' AS market_id,
                event_timestamp AS observed_at
              FROM v3_private.journal_event
             WHERE owner_account_id = $1 AND event_type = 'SellOrganicData'
             ORDER BY event_timestamp DESC
             OFFSET $2 LIMIT $3
            ''',
            account_id,
            offset,
            limit,
        )
    result = []
    for row in rows:
        item = _as_dict(row)
        item['bio_data'] = _as_object(item.get('bio_data'))
        result.append(item)
    return result


# Station history for in-game experiments ("dock, note the economy mix and
# services, build a facility, dock again later"). Reads only the caller's own
# Docked rows. A journal line re-imported after the 2026-10-21 Docked identity
# change can exist twice (once under the plain V3.0 key, once under the
# verified-import observation key); DISTINCT ON the line hash keeps one copy.
# Stations without a valid decimal MarketID are not addressable and are
# skipped. All values are bound parameters.
_STATION_OBSERVATIONS_SQL = r'''
    WITH docked AS (
        SELECT DISTINCT ON (e.source_record_hash)
               e.event_payload->>'MarketID' AS market_id,
               e.event_timestamp,
               e.event_payload,
               e.source_record_hash
          FROM v3_private.journal_event e
         WHERE e.owner_account_id = $1
           AND e.event_type = 'Docked'
           AND (e.event_payload->>'MarketID') ~ '^[0-9]{1,20}$'
           AND ($5::text IS NULL OR e.event_payload->>'MarketID' = $5::text)
         ORDER BY e.source_record_hash, e.created_at, e.journal_event_id
    ),
    stations AS (
        SELECT market_id,
               count(*)::int AS observation_count,
               min(event_timestamp) AS first_docked_at,
               max(event_timestamp) AS last_docked_at
          FROM docked
         GROUP BY market_id
         ORDER BY last_docked_at DESC, market_id
         OFFSET $2 LIMIT $3
    ),
    ranked AS (
        SELECT d.market_id, d.event_timestamp, d.event_payload, d.source_record_hash,
               row_number() OVER (
                   PARTITION BY d.market_id
                   ORDER BY d.event_timestamp DESC, d.source_record_hash DESC
               ) AS recency
          FROM docked d
          JOIN stations s USING (market_id)
    )
    SELECT s.market_id, s.observation_count, s.first_docked_at, s.last_docked_at,
           r.event_timestamp AS observed_at, r.event_payload AS payload
      FROM stations s
      JOIN ranked r USING (market_id)
     WHERE r.recency <= $4
     ORDER BY s.last_docked_at DESC, s.market_id, r.event_timestamp, r.source_record_hash
'''

# Output bounds for one observation (journal values are the caller's own,
# but the response stays bounded regardless of what was stored).
MAX_STATION_ECONOMIES = 32
MAX_STATION_SERVICES = 128
_MAX_TEXT = 256


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[:_MAX_TEXT]
    return None


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    return number if number == number and number not in (float('inf'), float('-inf')) else None


def _decimal_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return str(value)
    if isinstance(value, str) and value.strip().isdigit() and len(value.strip()) <= 20:
        return value.strip()
    return None


def docked_observation(observed_at: object, payload: object) -> dict[str, Any]:
    """Shape one stored Docked payload into the typed station observation."""
    data = _as_object(payload)
    if not isinstance(data, dict):
        data = {}
    economies = []
    raw_economies = data.get('StationEconomies')
    for item in raw_economies if isinstance(raw_economies, list) else []:
        if len(economies) >= MAX_STATION_ECONOMIES:
            break
        if not isinstance(item, dict) or _text(item.get('Name')) is None:
            continue
        economies.append({
            'name': _text(item.get('Name')),
            'name_localised': _text(item.get('Name_Localised')),
            'proportion': _number(item.get('Proportion')),
        })
    raw_services = data.get('StationServices')
    services = [
        text for text in (
            _text(item) for item in (raw_services if isinstance(raw_services, list) else [])
        ) if text is not None
    ][:MAX_STATION_SERVICES]
    return {
        'observed_at': observed_at,
        'system_id64': _decimal_id(data.get('SystemAddress')),
        'system_name': _text(data.get('StarSystem')),
        'station_name': _text(data.get('StationName')),
        'station_type': _text(data.get('StationType')),
        'station_government': _text(data.get('StationGovernment')),
        'station_allegiance': _text(data.get('StationAllegiance')),
        'dist_from_star_ls': _number(data.get('DistFromStarLS')),
        'economies': economies,
        'services': services,
    }


async def station_observations(
    pool: asyncpg.Pool,
    account_id: object,
    *,
    offset: int = 0,
    limit: int = 25,
    max_observations: int = 50,
    market_id: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Owner-scoped Docked history per station (MarketID), newest station first.

    Returns ``(stations, has_more)``. Each station carries its total
    observation count and at most ``max_observations`` observations — the
    most recent ones — listed oldest first so a before/after reads top-down.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _STATION_OBSERVATIONS_SQL,
            account_id,
            offset,
            limit + 1,
            max_observations,
            market_id,
        )
    stations: list[dict[str, Any]] = []
    by_market: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = _as_dict(row)
        key = str(item['market_id'])
        station = by_market.get(key)
        if station is None:
            station = {
                'market_id': key,
                'observation_count': int(item['observation_count']),
                'first_docked_at': item['first_docked_at'],
                'last_docked_at': item['last_docked_at'],
                'observations': [],
            }
            by_market[key] = station
            stations.append(station)
        station['observations'].append(docked_observation(item['observed_at'], item['payload']))
    has_more = len(stations) > limit
    return stations[:limit], has_more
