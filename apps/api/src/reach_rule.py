"""Find's reach rule: how far from inhabited space Find looks.

Every system within 500 ly of an inhabited system has scores, kept up to date
every night (docs/pi-move.md, "One band, not two"). Find shows the systems
within 250 ly by default and the whole 500 ly band on request (the owner,
11 November 2026).

It reads ``v3_state.system_reach`` (migration 028), which the nightly scoring
works out afresh once a week from the inhabited systems of the time
(``apps/importer/src/spansh_delta/reach.py``). A system has a row there only if
it is within 500 ly of an inhabited system; ``within_250`` says whether it is
within 250 ly too.

The rule is applied beside the claim rule (``claim_rule``), in the same place
in every query shape: one primary-key probe per row a window reads.
"""

from __future__ import annotations

from typing import Final

REACH_NEAR_LY: Final[int] = 250
REACH_FAR_LY: Final[int] = 500
REACH_CHOICES: Final[tuple[int, int]] = (REACH_NEAR_LY, REACH_FAR_LY)
DEFAULT_REACH_LY: Final[int] = REACH_NEAR_LY


def checked_reach(reach_ly: object) -> int:
    """``reach_ly`` as one of the two choices, or ValueError."""
    if isinstance(reach_ly, bool) or reach_ly not in REACH_CHOICES:
        raise ValueError(f"reach_ly must be one of {list(REACH_CHOICES)}")
    return int(reach_ly)  # type: ignore[call-overload]


def in_reach_sql(system_id_sql: str, reach_ly: int) -> str:
    """A WHERE clause that keeps only systems within ``reach_ly`` of
    inhabited space.

    ``system_id_sql`` is the SQL expression for the system's id64 in the
    surrounding query, for example ``s.system_id64``. ``reach_ly`` is checked
    against the two choices, so no request text reaches the SQL.
    """
    band = checked_reach(reach_ly)
    within_250 = " AND reach.within_250" if band == REACH_NEAR_LY else ""
    return (
        "EXISTS (SELECT 1 FROM v3_state.system_reach reach "
        f"WHERE reach.system_id64 = {system_id_sql}{within_250})"
    )
