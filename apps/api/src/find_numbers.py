"""The numbers in ``v3_state.economy_facts`` that put systems in Find's order.

For each economy (Strongest and Purest) and each pair, a system's row holds
one number for where its best body stands in that order
(``cpe/body_facts.py``: ``strongest_key``, ``purest_key``, ``pair_key``).
``pack`` makes the number from the order's steps, each in its own bits with
the first step highest, so that comparing two numbers compares the steps one
after another. ``unpack`` gives the steps back, and the ``read_*`` functions
name them. 0 stands for "no body suits".

The nightly scoring writes the numbers
(``apps/importer/src/spansh_delta/economy_facts.py``); Find's search reads
them (``find_economy.py``). Both use this file, so they cannot disagree about
the layout. ``VERSION`` names the rulebook's release and the layout: a fill of
the table is for one version.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Final

from edfinder_api.cpe.body_facts import ECONOMIES, PAIRS
from edfinder_api.cpe.rules import RULEBOOK_RELEASE

Steps = tuple[tuple[int, int, int], ...]

# The counts Find's body filters use, as the search table's rows have them
# (``search_economies.BODY_FILTER_COLS``). Each facts row carries a copy
# (migration 033), so that a filter is answered from the row a search already
# holds. In the order of the table's columns.
COUNT_COLUMNS: Final[tuple[str, ...]] = (
    "landable_count", "terraformable_count", "elw_count", "ww_count", "ammonia_count",
    "gas_giant_count", "hmc_count", "metal_rich_count", "rocky_count", "rocky_ice_count",
    "icy_count", "neutron_count", "black_hole_count", "white_dwarf_count", "other_star_count",
    "ring_count", "walkable_count", "bio_signal_total", "geo_signal_total",
)
# The facts row stores each count in two bytes.
COUNT_MAX: Final = 32767

# A step is (scale, offset, bits): its value times scale, plus offset, must be
# a whole number from 0 to 2**bits - 1. Fewer third economies count first, so
# those steps hold minus the count, plus 15. A room step is ground slots (0 to
# 7), then whether the body has rings or a belt that counts (0 or 1).
STRONGEST_STEPS: Final[Steps] = ((10, 0, 8), (10, 0, 6), (1, 15, 4), (10, 0, 6), (1, 0, 3), (1, 0, 1))
PUREST_STEPS: Final[Steps] = ((1, 15, 4), (1, 0, 1), (10, 0, 8), (10, 0, 6), (1, 0, 3), (1, 0, 1))
PAIR_STEPS: Final[Steps] = ((1, 15, 4), (10, 0, 8), (10, 0, 8), (10, 0, 6), (10, 0, 6), (10, 0, 7),
                            (1, 0, 3), (1, 0, 1))
# Change this when the layout above, ECONOMIES or PAIRS (cpe/body_facts.py) change.
LAYOUT: Final[str] = "facts-1"
VERSION: Final[str] = f"{RULEBOOK_RELEASE}/{LAYOUT}"


def layout_of(version: str) -> str:
    """The layout a fill's version names: what follows its last "/"."""
    return version.rsplit("/", 1)[-1]


# -- the ranked lists across the band ---------------------------------------------
#
# ``v3_state.economy_top`` (migration 033) holds the best systems of the whole
# band, in order, as numbered lists: one for each economy by Strongest, one for
# each by Purest, one for each pair, and one for no economy at all (most
# ground slots first). The nightly scoring writes them
# (``spansh_delta.economy_top``) and Find's search across the band reads them
# (``find_economy.py``), both by these numbers.

# The facts column each kind of list is in order of, and the list before its first.
_LISTS_BEFORE: Final[dict[str, int]] = {
    "strongest": 0, "purest": len(ECONOMIES), "pairs": 2 * len(ECONOMIES)}
# The list for no economy: the system's ground slots, then its known bodies.
LIST_NO_ECONOMY: Final = 2 * len(ECONOMIES) + len(PAIRS) + 1
LIST_COUNT: Final = LIST_NO_ECONOMY


def list_no(column: str, index: int) -> int:
    """The list for the ``index``-th number (from 1) of a facts column."""
    size = len(PAIRS) if column == "pairs" else len(ECONOMIES)
    if column not in _LISTS_BEFORE or not 1 <= index <= size:
        raise ValueError(f"no list for {column}[{index}]")
    return _LISTS_BEFORE[column] + index


def list_expressions() -> tuple[str, ...]:
    """What each list is in order of, as an expression on a facts row: list 1
    first. Made from this file's own names only."""
    return (*(f"strongest[{index}]" for index in range(1, len(ECONOMIES) + 1)),
            *(f"purest[{index}]" for index in range(1, len(ECONOMIES) + 1)),
            *(f"pairs[{index}]" for index in range(1, len(PAIRS) + 1)),
            "ground_slots::bigint")


class PackError(ValueError):
    """A step's value does not fit the layout, or a number is not of it."""


def _flat(key: Iterable[Any]) -> list[float]:
    out: list[float] = []
    for step in key:
        if isinstance(step, tuple):
            out.extend(_flat(step))
        else:
            out.append(float(step))
    return out


def pack(key: tuple, steps: Sequence[tuple[int, int, int]]) -> int:
    """One number that sorts as the key does: each step in its own bits, the
    first step highest. A leading bit makes it above 0, which stands for "no
    body suits"."""
    values = _flat(key)
    if len(values) != len(steps):
        raise PackError(f"{len(values)} steps where the layout has {len(steps)}")
    packed, width = 0, 0
    for value, (scale, offset, bits) in zip(values, steps, strict=True):
        scaled = value * scale + offset
        whole = round(scaled)
        if abs(scaled - whole) > 1e-6 or not 0 <= whole < (1 << bits):
            raise PackError(f"{value} does not fit (times {scale}, plus {offset}, in {bits} bits)")
        packed = (packed << bits) | whole
        width += bits
    return (1 << width) | packed


def unpack(number: int, steps: Sequence[tuple[int, int, int]]) -> tuple[float, ...]:
    """The steps ``pack`` made this number from, in order."""
    width = sum(bits for _, _, bits in steps)
    if isinstance(number, bool) or not isinstance(number, int) or number >> width != 1:
        raise PackError(f"{number!r} is not a number of this layout")
    values = []
    for scale, offset, bits in reversed(steps):
        values.append(round(((number & ((1 << bits) - 1)) - offset) / scale, 2))
        number >>= bits
    return tuple(reversed(values))


def read_strongest(number: int) -> dict[str, Any]:
    """The steps of ``strongest_key``, named."""
    ground_part, each_adds, third, starts, ground_slots, asteroids = unpack(number, STRONGEST_STEPS)
    return {
        "ground_part": ground_part, "each_facility_adds": each_adds, "third_economies": int(-third),
        "port_starts_with": starts, "ground_slots": int(ground_slots), "asteroid_base_room": bool(asteroids),
    }


def read_purest(number: int) -> dict[str, Any]:
    """The steps of ``purest_key``, named."""
    third, starts_with_it, ground_part, each_adds, ground_slots, asteroids = unpack(number, PUREST_STEPS)
    return {
        "third_economies": int(-third), "port_starts_with_it": bool(starts_with_it),
        "ground_part": ground_part, "each_facility_adds": each_adds,
        "ground_slots": int(ground_slots), "asteroid_base_room": bool(asteroids),
    }


def read_pair(number: int) -> dict[str, Any]:
    """The steps of ``pair_key``, named. Which of the two economies is the
    weaker is not in the number."""
    (third, weaker_part, stronger_part, weaker_adds, stronger_adds, starts, ground_slots,
     asteroids) = unpack(number, PAIR_STEPS)
    return {
        "third_economies": int(-third), "weaker_ground_part": weaker_part,
        "stronger_ground_part": stronger_part, "weaker_each_facility_adds": weaker_adds,
        "stronger_each_facility_adds": stronger_adds, "port_starts_with_both": starts,
        "ground_slots": int(ground_slots), "asteroid_base_room": bool(asteroids),
    }
