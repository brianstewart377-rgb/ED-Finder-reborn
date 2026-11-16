"""Account-scoped V3 journal projections (personal exploration views).

Counters are diagnostics only — they feed personal summaries, never
confidence inputs. Every query is scoped by ``owner_account_id = $1``
(account isolation is a hard invariant of the private trust zone).
"""

from __future__ import annotations

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


