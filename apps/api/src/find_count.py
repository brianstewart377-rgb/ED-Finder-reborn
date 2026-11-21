"""Find: the count of matches, and keeping it for a search's other pages.

The count is the costly half of a search. It is asked last, within a time
budget, and kept for the other pages of the same search where it is safe to.
Moved here from local_search.py on 15 November 2026.
"""
from __future__ import annotations

from typing import Any

import asyncpg

from edfinder_api.ranking.profile import LOCAL_SEARCH_RESULT_CAP


def _bounded_v3_total(qualifying: int | None) -> tuple[int, bool]:
    """Turn the cap+1 sentinel count into the public total contract."""
    count = int(qualifying) if qualifying is not None else 0
    return min(count, LOCAL_SEARCH_RESULT_CAP), count > LOCAL_SEARCH_RESULT_CAP


def total_from_short_page(row_count: int, limit: int, offset: int) -> int | None:
    """The number of matches, where the page itself shows it.

    A page that came back with fewer rows than it asked for has reached the
    end of the matches, so there are ``offset + row_count`` of them and the
    count need not be asked. An empty page past the first says only that
    there are no more than ``offset``, so it is no answer.
    """
    if row_count >= limit:
        return None
    if row_count == 0 and offset > 0:
        return None
    return offset + row_count


def known_qualifying(
    known_count: Any, generation_row: Any, shown_to: int = 0,
) -> int | None:
    """A count kept from an earlier page of the same search, if it was made
    on the generation this page is read from, and this page does not show it
    to be too small. Anything else is no answer: the count is then asked
    again.

    ``shown_to`` is how far this page reaches (its offset plus its rows).
    Which systems can be claimed changes each night with no new generation
    (the claim rule reads the state tables), so a count kept across the
    night's load can be a few out. A page that reaches past the kept count
    proves it too small.
    """
    if not isinstance(known_count, dict):
        return None
    qualifying = known_count.get('qualifying')
    if isinstance(qualifying, bool) or not isinstance(qualifying, int):
        return None
    if not 0 <= qualifying <= LOCAL_SEARCH_RESULT_CAP + 1:
        return None
    if qualifying < shown_to:
        return None
    if (
        str(known_count.get('derived_generation_id'))
        != str(generation_row['derived_generation_id'])
        or known_count.get('publication_sequence')
        != generation_row['publication_sequence']
        or known_count.get('archetype_version')
        != generation_row['archetype_version']
    ):
        return None
    return qualifying


def count_to_keep(response: dict[str, Any]) -> dict[str, Any] | None:
    """What to keep of a search's count for its other pages: the number of
    matches as the count query gives it (one past the limit when there are
    more), with the generation it was counted on. Nothing is kept of a
    search whose matches were not counted: its total is no count."""
    ranking = response.get('ranking') or {}
    total = response.get('total')
    if (
        response.get('total_is_counted') is False
        or isinstance(total, bool)
        or not isinstance(total, int)
        or ranking.get('derived_generation_id') is None
        or ranking.get('publication_sequence') is None
        or ranking.get('archetype_version') is None
    ):
        return None
    return {
        'qualifying': total + (1 if response.get('total_is_capped') else 0),
        'derived_generation_id': str(ranking['derived_generation_id']),
        'publication_sequence': ranking['publication_sequence'],
        # Tiers come from the colony type scores, so a count is kept under the
        # model that scored them (part of its cache key).
        'archetype_version': ranking['archetype_version'],
    }


async def count_within(
    conn: asyncpg.Connection,
    count_sql: str,
    count_params: list[Any],
    budget_ms: int | None,
) -> int | None:
    """The number of matches, or None where counting them ran out of time.

    The count is the costly half of a search, and the only part that can
    run long: for one colony type near a system it walks outwards to the
    1,001st match. PostgreSQL ends a statement that passes its time limit,
    and inside a transaction that ends everything else in it too. Seen on
    the Pi on 15 November 2026: the first "Manufacturing Hub near Sol" after
    the weekly backup answered 503 after 18 s, though its page had been
    read in under a second.

    So the count is asked last, inside a savepoint of its own: a count that
    runs out of time is given up, and the page is still answered.
    ``budget_ms`` shortens the time it is given (never lengthens it: the
    caller passes no more than the connection's own limit); None leaves the
    connection's limit as it is.
    """
    try:
        async with conn.transaction():
            if budget_ms is not None:
                await conn.execute(
                    "SELECT set_config('statement_timeout', $1, true)",
                    str(max(1, int(budget_ms))),
                )
            return await conn.fetchval(count_sql, *count_params)
    except asyncpg.exceptions.QueryCanceledError:
        return None
