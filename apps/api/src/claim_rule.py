"""Find's claim rule: which systems a commander can still claim.

The rule lives here once, so the ranked search (which hides systems) and the
note on a result (which explains one) can never disagree.

It reads ``v3_state.system_state`` (migration 024), which is filled from
Spansh's populated-systems and stations files by
``apps/importer/src/spansh_system_state.py``. A system has a row there only if
it has a population or a station that is not a fleet carrier.

    status                what it means                                 in Find
    --------------------  --------------------------------------------  ----------
    inhabited             population above zero                         hidden
    being_claimed         no population, and a station was seen there   hidden
                          in the last CLAIM_RECENT_DAYS days
    claim_ended_unknown   no population, and its station was last seen  shown,
                          longer ago than that                          with a note
    free                  no row in the table                           shown

A first port must be finished within four weeks of a claim, so a station that
nobody has reported for CLAIM_RECENT_DAYS days is either a finished colony whose
population Spansh has not picked up yet, or a claim that lapsed. The data cannot
tell those apart, which is why such a system is shown with a note, not hidden.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Iterable

CLAIM_RECENT_DAYS: Final[int] = 30

STATUS_INHABITED: Final[str] = "inhabited"
STATUS_BEING_CLAIMED: Final[str] = "being_claimed"
STATUS_CLAIM_ENDED_UNKNOWN: Final[str] = "claim_ended_unknown"
STATUS_FREE: Final[str] = "free"
HIDDEN_STATUSES: Final[frozenset[str]] = frozenset({STATUS_INHABITED, STATUS_BEING_CLAIMED})

# Both fragments read the row through the alias ``claim``. CLAIM_RECENT_DAYS is
# a server-owned integer, never request input.
_RECENT_SQL: Final[str] = (
    f"claim.stations_updated_at >= now() - interval '{CLAIM_RECENT_DAYS} days'"
)
_HIDDEN_SQL: Final[str] = f"(claim.population > 0 OR {_RECENT_SQL})"
_STATUS_SQL: Final[str] = (
    f"CASE WHEN claim.population > 0 THEN '{STATUS_INHABITED}' "
    f"WHEN {_RECENT_SQL} THEN '{STATUS_BEING_CLAIMED}' "
    f"ELSE '{STATUS_CLAIM_ENDED_UNKNOWN}' END"
)


def not_claimed_sql(system_id_sql: str) -> str:
    """A WHERE clause that keeps only systems Find may show.

    ``system_id_sql`` is the SQL expression for the system's id64 in the
    surrounding query, for example ``s.system_id64``. It is one primary-key
    probe per candidate row.
    """
    return (
        "NOT EXISTS (SELECT 1 FROM v3_state.system_state claim "
        f"WHERE claim.system_id64 = {system_id_sql} AND {_HIDDEN_SQL})"
    )


CLAIM_STATUS_LOOKUP_SQL: Final[str] = (
    "SELECT claim.system_id64,\n"
    f"       {_STATUS_SQL} AS claim_status,\n"
    "       claim.stations_updated_at\n"
    "FROM v3_state.system_state claim\n"
    "WHERE claim.system_id64 = ANY($1::bigint[])"
)


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


async def claim_notes(connection: Any, system_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """Claim status for one page of results, keyed by id64.

    A system with no row in ``v3_state.system_state`` is absent from the
    answer: it is free. Run this on the same connection and transaction as the
    page it annotates, so both read one snapshot.
    """
    ids = sorted({int(system_id) for system_id in system_ids})
    if not ids:
        return {}
    rows = await connection.fetch(CLAIM_STATUS_LOOKUP_SQL, ids)
    return {
        int(row["system_id64"]): {
            "claim_status": row["claim_status"],
            "claim_station_seen_at": _iso(row["stations_updated_at"]),
        }
        for row in rows
    }


def apply_claim_notes(results: list[dict[str, Any]], notes: dict[int, dict[str, Any]]) -> None:
    """Write ``claim_status`` and ``claim_station_seen_at`` onto each result."""
    for result in results:
        note = notes.get(int(result["id64"]))
        if note is None:
            result["claim_status"] = STATUS_FREE
            result["claim_station_seen_at"] = None
        else:
            result.update(note)
