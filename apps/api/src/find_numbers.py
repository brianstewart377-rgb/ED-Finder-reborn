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

from edfinder_api.cpe.rules import RULEBOOK_RELEASE

Steps = tuple[tuple[int, int, int], ...]

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
