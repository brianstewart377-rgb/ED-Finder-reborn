"""The 32 ly cubes of space that ``v3_state.economy_facts`` sorts systems into
(migration 032), so that Find can read the systems near a point from that one
table.

``cell_of`` must give what the table's ``cell`` column gives for the same
coordinates: tests/test_economy_facts.py holds the two together. Dividing by
32 is exact for a floating point number, so Python and PostgreSQL agree.
"""

from __future__ import annotations

import math
from typing import Final

CELL_LY: Final[int] = 32
# floor(coordinate / 32) + 4096 must be 0 to 8191: 13 bits an axis.
_OFFSET: Final[int] = 4096
_BITS: Final[int] = 13
LIMIT_LY: Final[int] = _OFFSET * CELL_LY


def _axis(value: float) -> int:
    if not math.isfinite(value) or not -LIMIT_LY <= value < LIMIT_LY:
        raise ValueError(f"a coordinate must be from -{LIMIT_LY} to below {LIMIT_LY} ly, not {value!r}")
    return math.floor(value / CELL_LY) + _OFFSET


def cell_of(x_ly: float, y_ly: float, z_ly: float) -> int:
    """The cell these coordinates are in, as the ``cell`` column numbers it."""
    return (_axis(z_ly) << (2 * _BITS)) | (_axis(y_ly) << _BITS) | _axis(x_ly)


def cells_within(x_ly: float, y_ly: float, z_ly: float, radius_ly: float) -> list[int]:
    """Every cell a sphere touches, in ascending order.

    A cell is in if its nearest point to the centre is within the radius, so
    no system inside the sphere is missed; a cell at the edge also holds
    systems outside it, which the search leaves out by their own distance.
    Cells past the edge of the numbered space hold no system and are left out.
    """
    if not math.isfinite(radius_ly) or radius_ly < 0:
        raise ValueError(f"a radius must be 0 or more, not {radius_ly!r}")
    centre = (x_ly, y_ly, z_ly)
    for value in centre:
        _axis(value)
    spans = []
    for value in centre:
        low = max(math.floor((value - radius_ly) / CELL_LY), -_OFFSET)
        high = min(math.floor((value + radius_ly) / CELL_LY), _OFFSET - 1)
        spans.append(range(low, high + 1))

    def gap(value: float, index: int) -> float:
        """How far the centre is from the cell's slab on one axis."""
        start = index * CELL_LY
        return max(start - value, 0.0, value - (start + CELL_LY))

    reach = radius_ly * radius_ly
    found = []
    for cz in spans[2]:
        dz = gap(z_ly, cz) ** 2
        for cy in spans[1]:
            dy = dz + gap(y_ly, cy) ** 2
            if dy > reach:
                continue
            for cx in spans[0]:
                if dy + gap(x_ly, cx) ** 2 <= reach:
                    found.append(((cz + _OFFSET) << (2 * _BITS)) | ((cy + _OFFSET) << _BITS) | (cx + _OFFSET))
    return found
