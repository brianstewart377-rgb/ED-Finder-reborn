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
