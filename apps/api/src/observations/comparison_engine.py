"""Compatibility import for the Stage 6C comparison engine.

The implementation lives in
``observations.comparison_engine_pkg.engine`` (and the supporting
per-domain modules under ``observations.comparison_engine_pkg.*``) so
the comparison tool can grow without turning this file into a
monolith.

This wrapper exists so that pre-existing and external imports of the
form::

    from edfinder_api.observations.comparison_engine import compare_prediction_to_observations

continue to work after the Stage 6C hardening modularisation. This is
the documented **stable public import path** for the Stage 6C engine
— new code should prefer it.

The package is deliberately named ``comparison_engine_pkg`` rather
than ``comparison`` because ``observations/comparison.py`` already
exists as the Stage 4D in-pipeline comparison module with a different
signature and result shape; a sibling package named ``comparison``
would silently shadow it.

No comparison logic is implemented here.
"""
from __future__ import annotations

from edfinder_api.observations.comparison_engine_pkg.engine import compare_prediction_to_observations

__all__ = ['compare_prediction_to_observations']
