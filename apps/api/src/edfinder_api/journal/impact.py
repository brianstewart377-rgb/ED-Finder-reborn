"""Bounded own-account impact totals from verified, retained journal scans.

One statement supplies a consistent ownership/readiness snapshot. Canonical
identity keys come from the importer, not global catalogue projections. Sparse
rescans preserve known classifications, and body identity is distinct from an
observation's timestamp or source hash.
"""
from __future__ import annotations

import uuid

import asyncpg

from edfinder_api.journal.commanders import ISSUER, PROVIDER

MAX_SCAN_EVENTS = 1_000_000
QUERY_TIMEOUT_SECONDS = 5.0

_GAS_GIANTS = (
    'sudarsky class i gas giant', 'sudarsky class ii gas giant',
    'sudarsky class iii gas giant', 'sudarsky class iv gas giant',
    'sudarsky class v gas giant', 'gas giant with water based life',
    'gas giant with ammonia based life', 'helium rich gas giant', 'helium gas giant',
)
_PLANET_CLASSES = (
    'metal rich body', 'high metal content body', 'rocky body', 'icy body',
    'rocky ice body', 'earthlike body', 'water world', 'ammonia world',
    'water giant', 'water giant with life', *_GAS_GIANTS,
)
_STAR_TYPES = (
    'O', 'B', 'A', 'F', 'G', 'K', 'M', 'L', 'T', 'Y', 'TTS', 'AeBe',
    'W', 'WN', 'WNC', 'WC', 'WO', 'CS', 'C', 'CN', 'CJ', 'CH', 'CHd', 'MS', 'S',
    'D', 'DA', 'DAB', 'DAO', 'DAZ', 'DAV', 'DB', 'DBZ', 'DBV', 'DO', 'DOV',
    'DQ', 'DC', 'DCV', 'DX', 'N', 'H', 'SupermassiveBlackHole',
    'A_BlueWhiteSuperGiant', 'B_BlueWhiteSuperGiant', 'F_WhiteSuperGiant',
    'G_WhiteYellowSuperGiant', 'M_RedSuperGiant', 'M_RedGiant', 'K_OrangeGiant',
)

_SUMMARY_SQL = """
WITH scans AS MATERIALIZED (
    SELECT e.event_key->>'SystemAddress' AS system_id,
           e.event_key->>'BodyID' AS body_id,
           e.event_payload, e.event_timestamp, e.journal_event_id
      FROM v3_private.journal_event e
      JOIN v3_identity.account_commander_access a
        ON a.account_id = e.owner_account_id AND a.commander_id = e.owner_commander_id
       AND a.access_role = 'OWNER' AND a.revoked_at IS NULL
      JOIN v3_identity.commander c
        ON c.commander_id = a.commander_id AND c.commander_state = 'ACTIVE'
      JOIN v3_identity.account owner
        ON owner.account_id = a.account_id AND owner.account_state = 'ACTIVE'
     WHERE e.owner_account_id = $1 AND e.event_type = 'Scan'
       -- Resolve readiness through ANY retained file occurrence, not the
       -- event's originating import. The same Scan can appear in several
       -- imported files (v3_private.journal_file_event); dedup keeps the
       -- journal_event attached to its first import, so gating on that import
       -- alone drops the scan when the first import is later WITHDRAWN/REJECTED
       -- even though another still-READY, ADMITTED file still retains it.
       AND EXISTS (
           SELECT 1 FROM v3_private.journal_file_event fe
             JOIN v3_private.journal_import_file jif
               USING (journal_file_id, owner_account_id)
             JOIN v3_private.private_import pi
               ON pi.private_import_id = jif.private_import_id
              AND pi.owner_account_id = jif.owner_account_id
            WHERE fe.journal_event_id = e.journal_event_id
              AND fe.owner_account_id = e.owner_account_id
              AND pi.owner_commander_id = e.owner_commander_id
              AND pi.import_state = 'READY'
              AND jif.file_state = 'ADMITTED'
       )
       AND EXISTS (
           SELECT 1 FROM v3_identity.commander_external_identity ce
            WHERE ce.commander_id = c.commander_id AND ce.provider = $3 AND ce.issuer = $4
              AND ce.verified_at IS NOT NULL AND ce.subject ~ '^F[1-9][0-9]{0,19}$'
       )
     LIMIT $2
), input_size AS (
    SELECT count(*) AS scan_events FROM scans
), valid AS MATERIALIZED (
    SELECT system_id, body_id, event_timestamp, journal_event_id,
           CASE
               WHEN lower(btrim(event_payload->>'PlanetClass')) = ANY($5::text[])
                   THEN lower(btrim(event_payload->>'PlanetClass'))
               WHEN event_payload->>'StarType' = ANY($6::text[]) THEN 'star'
           END AS body_class,
           CASE WHEN jsonb_typeof(event_payload->'TerraformState') = 'string'
                THEN event_payload->>'TerraformState'
                WHEN jsonb_typeof(event_payload->'TerraformState') = 'null'
                THEN '' END AS terraform_state
      FROM scans
     WHERE (SELECT scan_events FROM input_size) < $2
       AND system_id ~ '^(0|[1-9][0-9]{0,19})$'
       AND body_id ~ '^(0|[1-9][0-9]{0,19})$'
       AND (length(system_id) < 20 OR system_id COLLATE "C" <= '18446744073709551615')
       AND (length(body_id) < 20 OR body_id COLLATE "C" <= '18446744073709551615')
), bodies AS (
    SELECT DISTINCT system_id, body_id FROM valid
), classified AS (
    SELECT DISTINCT ON (system_id, body_id) system_id, body_id, body_class
      FROM valid WHERE body_class IS NOT NULL
     ORDER BY system_id, body_id, event_timestamp DESC, journal_event_id DESC
), terraforming AS (
    SELECT DISTINCT ON (system_id, body_id) system_id, body_id, terraform_state
      FROM valid WHERE terraform_state IS NOT NULL
     ORDER BY system_id, body_id, event_timestamp DESC, journal_event_id DESC
)
SELECT (SELECT scan_events FROM input_size) AS scan_events,
       count(DISTINCT b.system_id)::int AS systems_discovered,
       count(*)::int AS bodies_scanned,
       count(*) FILTER (WHERE c.body_class = 'earthlike body')::int AS earth_like_worlds,
       count(*) FILTER (WHERE c.body_class = 'water world')::int AS water_worlds,
       count(*) FILTER (WHERE c.body_class = 'ammonia world')::int AS ammonia_worlds,
       count(*) FILTER (WHERE t.terraform_state = 'Terraformable'
                         AND c.body_class IS DISTINCT FROM 'star')::int AS terraformable_candidates,
       count(*) FILTER (WHERE c.body_class = ANY($7::text[]))::int AS gas_giants
  FROM bodies b
  LEFT JOIN classified c USING (system_id, body_id)
  LEFT JOIN terraforming t USING (system_id, body_id)
"""


class GalaxyImpactUnavailable(RuntimeError):
    """The complete summary could not be computed inside its resource bound."""


async def galaxy_impact(pool: asyncpg.Pool, account_id: uuid.UUID) -> dict[str, int]:
    row = await pool.fetchrow(
        _SUMMARY_SQL, account_id, MAX_SCAN_EVENTS + 1, PROVIDER, ISSUER,
        list(_PLANET_CLASSES), list(_STAR_TYPES), list(_GAS_GIANTS),
        timeout=QUERY_TIMEOUT_SECONDS,
    )
    if row is None or row['scan_events'] > MAX_SCAN_EVENTS:
        raise GalaxyImpactUnavailable('Scan history exceeds summary bound')
    return {key: int(value) for key, value in row.items() if key != 'scan_events'}
