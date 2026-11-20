"""Systems Find leaves out because they are not real.

The galaxy data holds a few systems that only exist because someone sent
made-up reports to EDDN, usually while testing a tool. They are not in the
game, so Find must never offer them as a place to colonise. Each one is
listed here by its id64, with the evidence that it is fake.

The list is applied beside Find's claim rule (``claim_rule``) and reach rule
(``reach_rule``), in the same place in every query shape, so a page and its
count always agree. The ids are server-owned integers, never request input.

To add a system: check in Spansh or Inara that it is not in the game, add its
id64 and name here with the evidence, and bump ``SEARCH_CACHE_VERSION`` in
``routers/search.py`` so no kept page still shows it.
"""

from __future__ import annotations

from typing import Final

# id64 -> name, as the galaxy data spells it.
FAKE_SYSTEMS: Final[dict[int, str]] = {
    # One light year from Sol at (-1, 0, 0), where the game has no system.
    # Spansh last had a report of it on 23 October 2023, with a station
    # called "Metallic Base 2" and a faction called "Test Gang".
    7780433924818: "Test",
}


def not_fake_sql(system_id_sql: str) -> str:
    """A WHERE clause that leaves out every system in ``FAKE_SYSTEMS``.

    ``system_id_sql`` is the SQL expression for the system's id64 in the
    surrounding query, for example ``s.system_id64``.
    """
    ids = ", ".join(str(int(system_id)) for system_id in sorted(FAKE_SYSTEMS))
    return f"{system_id_sql} NOT IN ({ids})"
