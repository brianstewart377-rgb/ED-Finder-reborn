"""A ring's or belt's size: what the database holds, and what it truly is.

The columns ``rings.inner_radius_km`` and ``rings.outer_radius_km`` do not
hold kilometres.

Spansh's galaxy file gives a ring's inner and outer edge in metres, as the
game's own journal does (Saturn's D Ring starts 74,500,000 from Saturn's
centre: 74,500 km). The import stores that number as it comes
(apps/importer/src/v3_spansh/adapter.py), under a name that says kilometres.
So every stored value is 1,000 times too big.

The stored numbers are left as they are, for the same reasons as a body's
surface pressure (shared_contracts/surface_pressure.py): every row has the
same mistake, old and new alike, because the nightly update goes through the
same import, and putting them right would mean rewriting a whole table. So
the mistake is undone here, in one place, wherever the number is read.

If the import is ever corrected, the stored rows must be corrected in the
same step and this function changed with them. One alone would make every
ring size wrong again. tests/test_ring_sizes.py holds the three together on
real records.
"""
from __future__ import annotations

METRES_PER_KILOMETRE = 1000.0


def true_kilometres(stored: float | None) -> float | None:
    """A ring's or belt's edge in kilometres, from the number the database holds."""
    if stored is None:
        return None
    return stored / METRES_PER_KILOMETRE
