"""Find: the systems near a point, in order for one economy or for a pair.

Ratings rebuild ("Ratings rebuild — design"), build step 5. Beside the old
search (``local_search.py``), which it does not change.

A search names one economy, with the order Strongest or Purest, or two
economies together, and a point with a radius. It reads
``v3_state.economy_facts`` (migrations 031 to 033), which the nightly
scoring keeps (``apps/importer/src/spansh_delta/economy_facts.py``): each
system's row has one number for where its best body stands in each order
(``find_numbers.py``), how many of its bodies are known, where it is, and
the counts Find's body filters use.

The order of the systems is the design's:

  1. the number, highest first: the order of the systems' best bodies, as
     ``cpe/body_facts.py`` gives it;
  2. more known bodies first, which stands for the system's orbital room
     (decision 10);
  3. systems still level are level; the nearer is listed first.

How it is read:

  * The systems come from the facts table alone, by the 32 ly cells the
    search's sphere touches (``find_cells.py``). The body-count filters are
    applied there too, to the counts on the facts row itself (migration 033),
    so a filter costs no look-up however few systems pass it. What is left
    is sorted.
  * Find's other rules are then applied to the sorted systems one by one,
    until the page is full: the fake systems, the claim rule, the reach rule,
    and that the system has a search row to show. So a page costs a few
    hundred look-ups, not one for every system in the sphere.
  * The count walks the same systems to the 1,001st match, within a time
    budget (``find_count.count_within``).

Nothing here guesses: a system whose row has 0 for the asked order has no
body that suits it, and is left out.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any, Final

import asyncpg
from fastapi import HTTPException

from edfinder_api import find_cells
from edfinder_api.claim_rule import apply_claim_notes, claim_notes, not_claimed_sql
from edfinder_api.cpe.body_facts import ECONOMIES, PAIRS
from edfinder_api.fake_systems import not_fake_sql
from edfinder_api.find_count import _bounded_v3_total, count_within, total_from_short_page
from edfinder_api.find_numbers import (
    COUNT_COLUMNS,
    LAYOUT,
    VERSION,
    layout_of,
    read_pair,
    read_purest,
    read_strongest,
)
from edfinder_api.local_search import body_filter_bounds
from edfinder_api.ranking.profile import (
    BODY_COUNT_COLUMNS,
    LOCAL_SEARCH_RESULT_CAP,
    RANKING_PAGE_LIMIT_MAX,
)
from edfinder_api.ranking.ranking_sql import CURRENT_GENERATION_PIN
from edfinder_api.reach_rule import DEFAULT_REACH_LY, checked_reach, in_reach_sql

FACTS = "v3_state.economy_facts"
FILLS = "v3_state.economy_facts_fill"
# The widest search near a point. Wider than this, the systems to sort are in
# the millions.
RADIUS_MAX_LY = 250.0
RADIUS_DEFAULT_LY = 100.0
ORDER_STRONGEST = "strongest"
ORDER_PUREST = "purest"
ORDER_PAIR = "pair"
ONE_ECONOMY_ORDERS = (ORDER_STRONGEST, ORDER_PUREST)
DEFAULT_PAGE_SIZE = 50
_READERS = {ORDER_STRONGEST: read_strongest, ORDER_PUREST: read_purest, ORDER_PAIR: read_pair}
# The search row's columns a result carries, beside its name and main star.
_COUNTS = ("body_count", "station_count", *BODY_COUNT_COLUMNS)


@dataclass(frozen=True)
class EconomySearch:
    """A checked request."""

    economies: tuple[str, ...]
    order: str
    # The facts column the order's numbers are in, and which of them (from 1).
    column: str
    index: int
    centre: tuple[float, float, float]
    radius_ly: float
    reach_ly: int
    bounds: dict[str, int]
    size: int
    offset: int


def _refuse(detail: str) -> HTTPException:
    return HTTPException(422, detail)


def prepare(body: dict[str, Any]) -> EconomySearch:
    """Check a request without touching the database; 422 for one that cannot
    be answered."""
    economies = body.get("economies")
    if not isinstance(economies, (list, tuple)) or not 1 <= len(economies) <= 2:
        raise _refuse("economies must name one economy, or two for a pair.")
    for economy in economies:
        if economy not in ECONOMIES:
            raise _refuse(f"Unknown economy {economy!r}. Valid values: {list(ECONOMIES)}")
    order = body.get("order")
    if len(economies) == 1:
        order = ORDER_STRONGEST if order is None else order
        if order not in ONE_ECONOMY_ORDERS:
            raise _refuse(f"order must be one of {list(ONE_ECONOMY_ORDERS)} for one economy.")
        column, index = order, ECONOMIES.index(economies[0]) + 1
        named = (economies[0],)
    else:
        if economies[0] == economies[1]:
            raise _refuse("A pair is two different economies.")
        if order not in (None, ORDER_PAIR):
            raise _refuse("order is for one economy; a pair has one order.")
        named = tuple(sorted(economies, key=ECONOMIES.index))
        order, column, index = ORDER_PAIR, "pairs", PAIRS.index(named) + 1

    coords = body.get("reference_coords") or {}
    try:
        centre = tuple(float(coords[axis]) for axis in ("x", "y", "z"))
    except (KeyError, TypeError, ValueError) as error:
        raise _refuse("reference_coords must include x, y and z.") from error
    radius = body.get("radius_ly", RADIUS_DEFAULT_LY)
    try:
        radius = float(radius)
        # Refuses a centre that is not a finite point of the galaxy.
        find_cells.cell_of(*centre)
    except (TypeError, ValueError) as error:
        raise _refuse("reference_coords and radius_ly must be finite numbers within the galaxy.") from error
    if not math.isfinite(radius) or not 0 < radius <= RADIUS_MAX_LY:
        raise _refuse(f"radius_ly must be above 0 and at most {RADIUS_MAX_LY:g}.")

    try:
        reach_ly = checked_reach(body.get("reach_ly", DEFAULT_REACH_LY))
    except ValueError as error:
        raise _refuse(str(error)) from error
    bounds = body_filter_bounds(body.get("body_filters"))

    size, offset = body.get("size", DEFAULT_PAGE_SIZE), body.get("from", 0)
    for name, value in (("size", size), ("from", offset)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise _refuse(f"{name} must be a whole number.")
    if not 1 <= size <= RANKING_PAGE_LIMIT_MAX:
        raise _refuse(f"size must be from 1 to {RANKING_PAGE_LIMIT_MAX}.")
    if offset < 0 or offset + size > LOCAL_SEARCH_RESULT_CAP:
        raise _refuse(f"from must be 0 or more, and from + size at most {LOCAL_SEARCH_RESULT_CAP}.")
    return EconomySearch(economies=named, order=order, column=column, index=index, centre=centre,  # type: ignore[arg-type]
                         radius_ly=radius, reach_ly=reach_ly, bounds=bounds, size=size, offset=offset)


# -- the statements -----------------------------------------------------------------


class _Params:
    def __init__(self) -> None:
        self.values: list[Any] = []

    def add(self, value: Any) -> str:
        self.values.append(value)
        return f"${len(self.values)}"


def _in_sphere(search: EconomySearch, params: _Params) -> tuple[list[str], str]:
    """The conditions that pick the sphere's systems out of the facts table,
    and the expression for a system's distance from the centre."""
    cells = params.add(find_cells.cells_within(*search.centre, search.radius_ly))
    x, y, z = (params.add(value) for value in search.centre)
    squared = f"(f.x_ly - {x}) ^ 2 + (f.y_ly - {y}) ^ 2 + (f.z_ly - {z}) ^ 2"
    reach = params.add(search.radius_ly * search.radius_ly)
    number = f"f.{search.column}[{int(search.index)}]"
    where = [f"f.cell = ANY({cells}::bigint[])", f"{squared} <= {reach}", f"{number} > 0"]
    return where + count_filters(search.bounds, params, "f."), f"sqrt({squared})"


# The largest whole number PostgreSQL's bigint holds.
_BIGGEST: Final = (1 << 63) - 1


def count_filters(bounds: dict[str, int], params: _Params, prefix: str) -> list[str]:
    """The body-count filters as conditions on a row that carries the counts
    (the facts table's, or a ranked list's). A row without counts, of a system
    that has no search row, passes none of them."""
    conditions = []
    for key, value in bounds.items():
        column, operator = key[:-4], {"_min": ">=", "_max": "<="}[key[-4:]]
        if column not in COUNT_COLUMNS:
            raise ValueError(f"not a body-count column: {column!r}")
        # The columns are two bytes wide and a request may ask for any whole
        # number. Compared as eight-byte numbers, "at least 40,000" finds
        # nothing and "at most 40,000" everything, instead of failing.
        asked = max(-_BIGGEST - 1, min(int(value), _BIGGEST))
        conditions.append(f"{prefix}{column} {operator} {params.add(asked)}::bigint")
    return conditions


def _rules(search: EconomySearch, params: _Params, system_id_sql: str) -> list[str]:
    """Find's rules for one system that are not on its facts row: it is real,
    it can still be claimed, it is in reach, and it has a search row to show.
    Each is a look-up by the system's id."""
    # Cheapest first: the page applies them to a system in this order, and
    # stops at the first one that fails.
    return [
        not_fake_sql(system_id_sql),
        not_claimed_sql(system_id_sql),
        in_reach_sql(system_id_sql, search.reach_ly),
        "EXISTS (SELECT 1 FROM v3_app.system_search s "
        f"WHERE s.derived_generation_id = {CURRENT_GENERATION_PIN} "
        f"AND s.system_id64 = {system_id_sql})",
    ]


ORDER_SQL = "{0}number DESC, {0}bodies_known DESC, {0}distance ASC, {0}system_id64 ASC"


def page_query(search: EconomySearch) -> tuple[str, list[Any]]:
    """One page, in order.

    Three levels, each kept apart from the next by ``OFFSET 0``, which
    PostgreSQL does not look through:

      * innermost (``c``), the sphere's systems from the facts table, sorted;
      * around it (``k``), whether each sorted system passes Find's rules,
        as one true-or-false column. Written as a column, a rule is always
        looked up for one system at a time, as that system is reached;
        written as a condition, PostgreSQL may instead join every system in
        the sphere to the rules' tables and sort again afterwards;
      * outermost, the systems that pass, until the page is full.

    Each level names the order, which is how PostgreSQL knows that the
    systems reaching it are already in that order and does not sort again.
    """
    params = _Params()
    conditions, distance = _in_sphere(search, params)
    where = "\n          AND ".join(conditions)
    rules = "\n               AND ".join(_rules(search, params, "c.system_id64"))
    limit, offset = params.add(search.size), params.add(search.offset)
    return (
        "SELECT k.system_id64, k.number, k.bodies_known, k.ground_slots,\n"
        "       k.x_ly, k.y_ly, k.z_ly, k.distance\n"
        "FROM (\n"
        "    SELECT c.system_id64, c.number, c.bodies_known, c.ground_slots,\n"
        "           c.x_ly, c.y_ly, c.z_ly, c.distance,\n"
        f"           (   {rules}) AS kept\n"
        "    FROM (\n"
        f"        SELECT f.system_id64, f.{search.column}[{int(search.index)}] AS number,\n"
        "               f.bodies_known, f.ground_slots, f.x_ly, f.y_ly, f.z_ly,\n"
        f"               {distance} AS distance\n"
        f"        FROM {FACTS} f\n"
        f"        WHERE {where}\n"
        f"        ORDER BY {ORDER_SQL.format('')}\n"
        "        OFFSET 0\n"
        "    ) c\n"
        f"    ORDER BY {ORDER_SQL.format('c.')}\n"
        "    OFFSET 0\n"
        ") k\n"
        "WHERE k.kept\n"
        f"ORDER BY {ORDER_SQL.format('k.')}\n"
        f"LIMIT {limit} OFFSET {offset}"
    ), params.values


def count_query(search: EconomySearch, cap: int = LOCAL_SEARCH_RESULT_CAP) -> tuple[str, list[Any]]:
    """How many systems match, counted to one past ``cap``."""
    params = _Params()
    conditions, _ = _in_sphere(search, params)
    where = "\n      AND ".join(conditions)
    rules = "\n      AND ".join(_rules(search, params, "f.system_id64"))
    return (
        "SELECT count(*) FROM (\n"
        "    SELECT 1\n"
        f"    FROM {FACTS} f\n"
        f"    WHERE {where}\n"
        f"      AND {rules}\n"
        f"    LIMIT {int(cap) + 1}\n"
        ") counted"
    ), params.values


DETAILS_SQL = (
    "SELECT s.system_id64, s.name, s.main_star_class, "
    + ", ".join(f"s.{column}" for column in _COUNTS)
    + " FROM v3_app.system_search s "
    f"WHERE s.derived_generation_id = {CURRENT_GENERATION_PIN} "
    "AND s.system_id64 = ANY($1::bigint[])"
)


# -- whether the facts can be read ----------------------------------------------------

LATEST_FILLS_SQL = (
    f"SELECT fill_id, version, started_at, finished_at, systems, tidied_at FROM {FILLS} "
    "ORDER BY fill_id DESC LIMIT 20"
)
IN_REACH_SQL = "SELECT systems FROM v3_state.reach_log ORDER BY reach_id DESC LIMIT 1"


async def facts_state(connection: asyncpg.Connection) -> dict[str, Any]:
    """Which fill of the facts this build can read, or 503 where there is none.

    A fill is for one version: the rulebook's release, and the layout of the
    numbers (``find_numbers.VERSION``). The numbers can be read once a fill
    of this build's layout has finished. While a later fill is under way (a
    new rulebook, a week on the Pi), rows of both rulebooks stand side by
    side: the answer is still given, and says so (``updating``).
    """
    try:
        fills = await connection.fetch(LATEST_FILLS_SQL)
    except (asyncpg.exceptions.UndefinedTableError, asyncpg.exceptions.UndefinedColumnError,
            asyncpg.exceptions.InvalidSchemaNameError) as error:
        raise HTTPException(503, "Find's facts for each system are not kept in this database yet.") from error
    if not fills:
        raise HTTPException(503, "Find's facts for each system have not been worked out yet: "
                                 "the first fill has not started.")
    latest = fills[0]
    finished = next((fill for fill in fills
                     if fill["finished_at"] is not None and layout_of(fill["version"]) == LAYOUT), None)
    if finished is None:
        if layout_of(latest["version"]) == LAYOUT and latest["finished_at"] is None:
            try:
                in_reach = await connection.fetchval(IN_REACH_SQL)
            except asyncpg.PostgresError:
                in_reach = None
            of = f" of {in_reach:,}" if in_reach else ""
            raise HTTPException(
                503, "Find's facts for each system are still being worked out: "
                     f"{latest['systems']:,}{of} systems done so far.")
        raise HTTPException(503, "Find's facts for each system were worked out for another build "
                                 "of ED-Finder; the next nightly run starts them again.")
    updating = latest["fill_id"] != finished["fill_id"] or finished["version"] != VERSION
    return {
        "version": finished["version"],
        "fill_id": int(finished["fill_id"]),
        "finished_at": finished["finished_at"].isoformat(),
        "in_order_on_disk": finished["tidied_at"] is not None,
        "updating": updating,
        "updating_to": (latest["version"] if latest["fill_id"] != finished["fill_id"] else VERSION)
        if updating else None,
        "updating_systems_done": int(latest["systems"])
        if updating and latest["fill_id"] != finished["fill_id"] else None,
    }


# -- the search ---------------------------------------------------------------------


def _record(row: asyncpg.Record, details: asyncpg.Record, order: str) -> dict[str, Any]:
    return {
        "id64": row["system_id64"],
        "id": str(row["system_id64"]),
        "name": details["name"],
        "coords": {"x": row["x_ly"], "y": row["y_ly"], "z": row["z_ly"]},
        "distance": round(float(row["distance"]), 2),
        "main_star": details["main_star_class"],
        **{column: details[column] for column in _COUNTS},
        "bodies_known": int(row["bodies_known"]),
        "ground_slots": int(row["ground_slots"]),
        "order_number": int(row["number"]),
        "best_body": _READERS[order](int(row["number"])),
    }


async def search_near(
    body: dict[str, Any],
    pool: asyncpg.Pool,
    count_budget_ms: int | None = None,
) -> dict[str, Any]:
    """The answer to ``POST /api/find/economy``."""
    started = time.time()
    search = prepare(body)
    page_sql, page_params = page_query(search)
    count_sql, count_params = count_query(search)
    async with pool.acquire() as connection:
        # One snapshot for the fill, the page, its details and its count.
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            # The page stops long before the work PostgreSQL expects of it, so
            # a program built for the whole of that work would be time wasted.
            await connection.execute("SET LOCAL jit = off")
            state = await facts_state(connection)
            try:
                rows = await connection.fetch(page_sql, *page_params)
            except asyncpg.exceptions.UndefinedColumnError as error:
                # A body filter, on a database whose facts rows have no counts yet.
                raise HTTPException(
                    503, "Find's facts do not hold the body counts yet (migration 033 has not been "
                         "applied), so a search with a body filter cannot be made.") from error
            ids = [row["system_id64"] for row in rows]
            details = {found["system_id64"]: found
                       for found in await connection.fetch(DETAILS_SQL, ids)} if ids else {}
            notes = await claim_notes(connection, ids)
            qualifying = total_from_short_page(len(rows), search.size, search.offset)
            if qualifying is None:
                # Last, and in a savepoint: a count that runs out of time must
                # not take the page with it.
                qualifying = await count_within(connection, count_sql, count_params, count_budget_ms)
    results = [_record(row, details[row["system_id64"]], search.order) for row in rows]
    apply_claim_notes(results, notes)
    counted = qualifying is not None
    if not counted:
        # Not a count: how many matches this page shows there to be.
        qualifying = search.offset + len(results)
    total, total_is_capped = _bounded_v3_total(int(qualifying))
    return {
        "results": results,
        "count": len(results),
        "total": total,
        "total_is_capped": total_is_capped,
        "total_is_counted": counted,
        "query_ms": round((time.time() - started) * 1000),
        "source": f"facts:{state['version']}",
        "economies": list(search.economies),
        "order": search.order,
        "radius_ly": search.radius_ly,
        "reach_ly": search.reach_ly,
        "facts": state,
    }
