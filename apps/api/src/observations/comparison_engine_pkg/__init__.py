"""Stage 6C comparison engine subpackage.

The Stage 6C predicted-vs-observed comparison engine lives here, split
into focused modules so it can grow without becoming a god file:

* ``engine`` — public orchestration entry point.
* ``prediction_extractors`` — pull predicted services/economies from
  a Simulation Preview prediction mapping.
* ``observation_index`` — bucket persisted observed facts by fact type
  and subject for O(1) lookup.
* ``service_rules`` / ``economy_rules`` / ``cp_rules`` /
  ``facility_rules`` / ``build_outcome_rules`` /
  ``prediction_claim_rules`` / ``note_rules`` — per-domain comparison.
* ``summary`` — top-level summary + confidence_impact.
* ``shared`` — common helpers (evidence projection, severity clamp,
  scalar equality, severity enum lookup).

Package naming note (Stage 6C hardening): this package is deliberately
named ``comparison_engine_pkg`` rather than the shorter ``comparison``
because ``observations/comparison.py`` already exists as the Stage 4D
**in-pipeline** comparison module (different signature, different
result shape). A package named ``observations/comparison/`` would
silently shadow that module via Python's import resolution and break
``simulation.build_preview``. The public stable import path is the
compatibility wrapper ``observations.comparison_engine``.

Direct imports of this package work too::

    from edfinder_api.observations.comparison_engine_pkg import compare_prediction_to_observations

but new code should prefer the compatibility wrapper, which is the
documented Stage 6C public surface.

Stage 6C is comparison only — none of these modules may be imported by
simulation scoring, optimiser generation, optimiser ranking, or
mechanics. A static passivity test enforces that boundary.
"""
from __future__ import annotations

from edfinder_api.observations.comparison_engine_pkg.engine import compare_prediction_to_observations

__all__ = ['compare_prediction_to_observations']
