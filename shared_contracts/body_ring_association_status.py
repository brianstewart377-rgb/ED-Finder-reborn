"""Canonical body_rings.association_status classification SQL.

Single source of truth for the CASE expression that decides whether a ring
row's owning body is a confirmed local match, an unresolved identity, or
belt-source evidence. Previously defined independently in three files
(apps/eddn/src/eddn_listener.py, apps/api/src/ingest/eddn_client.py,
apps/api/src/journal_import/store.py) with no shared module — identical at
the time this was extracted, but nothing enforced that, and CLAUDE.md's own
"Known hazards" section documents this as exactly the kind of copy that
silently drifts. Import this constant instead of redefining it.

Positional placeholders ($1-$5, $11) match each call site's own parameter
order for its body_rings UPSERT — this is asyncpg-style SQL text meant to be
embedded via an f-string into a larger query, not executed standalone.
"""
from __future__ import annotations

BODY_RING_ASSOCIATION_STATUS_CASE_SQL = """
CASE
    WHEN $11 = 'eddn_scan'
         AND NOT EXISTS (
             SELECT 1
             FROM bodies b
             WHERE b.id = $2::bigint
               AND b.system_id64 = $1::bigint
         )
         AND (
             $3::bigint = 0
             OR $2::bigint = 0
             OR $4 ILIKE '%% belt%%'
             OR $5 ILIKE '%% belt%%'
         )
        THEN 'belt_source_evidence'
    WHEN EXISTS (
             SELECT 1
             FROM bodies b
             WHERE b.id = $2::bigint
               AND b.system_id64 = $1::bigint
               AND (
                   $4 IS NULL
                   OR b.name = $4
               )
         )
        THEN 'local_matched'
    ELSE 'unresolved_body_identity'
END
""".strip()
