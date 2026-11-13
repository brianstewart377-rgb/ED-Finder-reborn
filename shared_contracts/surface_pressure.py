"""A body's surface pressure: what the database holds, and what it truly is.

The column ``bodies.surface_pressure_atm`` does not hold atmospheres.

Spansh's galaxy file gives the pressure in atmospheres already (Earth:
0.999). The import took that number for pascals and divided it by 101,325
before storing it (apps/importer/src/v3_spansh/adapter.py). So every stored
value is 101,325 times too small (Earth: 0.00000986).

The stored numbers are left as they are. Putting them right would mean
rewriting a large share of the biggest table in the database, and every row
has the same mistake, old and new alike: the nightly update goes through the
same import. So the mistake is undone here, in one place, wherever the
number is read.

If the import is ever corrected, the stored rows must be corrected in the
same step and these functions changed with them. One alone would make every
pressure wrong again. tests/test_atmosphere_and_pressure.py holds the three
together on a real record.
"""
from __future__ import annotations

PASCALS_PER_ATMOSPHERE = 101_325.0


def true_atmospheres(stored: float | None) -> float | None:
    """The pressure in atmospheres, from the number the database holds."""
    if stored is None:
        return None
    return stored * PASCALS_PER_ATMOSPHERE


def true_pascals(stored: float | None) -> float | None:
    """The pressure in pascals, from the number the database holds."""
    atmospheres = true_atmospheres(stored)
    if atmospheres is None:
        return None
    return atmospheres * PASCALS_PER_ATMOSPHERE
