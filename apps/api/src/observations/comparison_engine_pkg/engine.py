"""Stage 6C comparison — orchestration engine.

This module owns ONLY the public orchestration entry point. All detailed
rule logic lives in the per-domain rule modules in this package:

* ``prediction_extractors`` — pull predicted services/economies/CP out
  of the prediction mapping.
* ``observation_index`` — bucket observations by fact type / subject.
* ``service_rules`` / ``economy_rules`` / ``cp_rules`` /
  ``facility_rules`` / ``build_outcome_rules`` /
  ``prediction_claim_rules`` / ``note_rules`` — per-domain comparison.
* ``summary`` — top-level summary + confidence_impact.
* ``shared`` — common helpers (evidence projection, severity clamping,
  scalar equality).

Design rules — DO NOT relax without an explicit stage decision:

1. **Pure / deterministic.** No database access, no network access,
   no global state, no time-dependent branching unless ``now`` is
   injected. The same inputs always produce the same output.
2. **No mutation.** Neither the ``prediction`` mapping nor the
   ``observed_facts`` list (or its members) is mutated.
3. **No scoring impact.** Nothing in this module is imported by
   simulation scoring, optimiser candidate generation, or optimiser
   ranking. A static passivity test enforces this.
4. **Conservative semantics.**
   * Missing observations do not contradict predictions.
   * Unknown/unverified observations do not confirm or contradict;
     they surface as ``unverified``.
   * Low-confidence observations cannot produce high-severity
     contradictions.
   * Notes never contradict — they are observed-only context.
   * Economy evidence is matched by economy name — an observation for
     a *different* economy never auto-contradicts a predicted economy.
   * One observation is evidence, not proof.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from edfinder_api.observations.comparison_models import (
    PredictionObservationComparison,
    PredictionObservationComparisonResult,
)
from edfinder_api.observations.models import PersistedObservedFact
from edfinder_api.observations.comparison_engine_pkg.observation_index import index_observations
from edfinder_api.observations.comparison_engine_pkg.prediction_extractors import (
    extract_economy_predictions,
    extract_service_predictions,
)
from edfinder_api.observations.comparison_engine_pkg.service_rules import (
    compare_service,
    observed_only_service,
)
from edfinder_api.observations.comparison_engine_pkg.economy_rules import (
    compare_economy,
    observed_only_economy,
)
from edfinder_api.observations.comparison_engine_pkg.cp_rules import compare_cp
from edfinder_api.observations.comparison_engine_pkg.facility_rules import observed_only_facility
from edfinder_api.observations.comparison_engine_pkg.build_outcome_rules import compare_build_outcome
from edfinder_api.observations.comparison_engine_pkg.prediction_claim_rules import compare_prediction_claim
from edfinder_api.observations.comparison_engine_pkg.note_rules import observed_only_note
from edfinder_api.observations.comparison_engine_pkg.summary import build_summary


def compare_prediction_to_observations(
    *,
    system_id64: int,
    target_archetype: str | None,
    prediction: Mapping[str, Any],
    observed_facts: list[PersistedObservedFact],
    now: Callable[[], datetime] | None = None,
) -> PredictionObservationComparisonResult:
    """Compare a prediction against observed facts and return a result.

    ``now`` is injectable purely so tests can pin ``generated_at``. The
    default uses UTC ``datetime.now`` and serialises to ISO-8601.
    """
    if not isinstance(prediction, Mapping):
        # The router-level Pydantic model already enforces this, but the
        # engine is also called directly by tests/utilities, so guard
        # here too. We refuse to silently coerce.
        raise TypeError('prediction must be a Mapping[str, Any]')

    generated_at = (now() if now else datetime.now(timezone.utc)).isoformat()
    comparisons: list[PredictionObservationComparison] = []
    assumptions: list[str] = []
    warnings: list[str] = []

    obs_index = index_observations(observed_facts)

    # 1. Service comparisons — walk the prediction's predicted services
    #    first so predicted_only rows are emitted in a deterministic
    #    order, then process any observed services with no prediction.
    service_predictions = extract_service_predictions(prediction)
    for service_id, predicted_status in sorted(service_predictions.items()):
        matched = obs_index.pop_service(service_id)
        comparisons.append(compare_service(service_id, predicted_status, matched))

    # Any service observations left unmatched are observed_only.
    for _service_id, facts in sorted(obs_index.services_remaining().items()):
        for fact in facts:
            comparisons.append(observed_only_service(fact))

    # 2. Economy comparisons — same pattern. Conservative name-keyed
    #    matching: differing observed economies never auto-contradict
    #    predicted ones (see economy_rules.py module docstring).
    economy_predictions = extract_economy_predictions(prediction)
    for economy_name, predicted_present in sorted(economy_predictions.items()):
        matched = obs_index.pop_economy(economy_name)
        comparisons.append(compare_economy(economy_name, predicted_present, matched))

    for _economy_name, facts in sorted(obs_index.economies_remaining().items()):
        for fact in facts:
            comparisons.append(observed_only_economy(fact))

    # 3. CP comparisons — observation-driven; we only emit a row per
    #    observation. We deliberately do not emit predicted_only rows
    #    for every CP sub-field to avoid noise.
    for fact in obs_index.cp_facts():
        comparisons.append(compare_cp(prediction, fact))

    # 4. Facility state — observation-driven; observed_only in Stage 6C.
    for fact in obs_index.facility_facts():
        comparisons.append(observed_only_facility(fact))

    # 5. Build outcome — observation-driven, conservative summary-only.
    for fact in obs_index.build_outcome_facts():
        comparisons.append(compare_build_outcome(prediction, fact))

    # 6. prediction_match / prediction_mismatch — user claims about a
    #    subject; only elevated to confirmed/contradicted when the
    #    referenced subject is known to the prediction.
    for fact in obs_index.prediction_match_facts():
        comparisons.append(
            compare_prediction_claim(
                fact, service_predictions, economy_predictions, matched=True,
            )
        )
    for fact in obs_index.prediction_mismatch_facts():
        comparisons.append(
            compare_prediction_claim(
                fact, service_predictions, economy_predictions, matched=False,
            )
        )

    # 7. Notes — never contradict; always observed_only/info.
    for fact in obs_index.note_facts():
        comparisons.append(observed_only_note(fact))

    if not service_predictions:
        assumptions.append(
            'Prediction did not expose a `services` map; '
            'service comparisons rely on observed facts only.'
        )
    if not economy_predictions:
        assumptions.append(
            'Prediction did not expose `economy_composition` or `economy_order`; '
            'economy comparisons rely on observed facts only.'
        )
    if 'cp' not in prediction:
        assumptions.append(
            'Prediction did not expose a `cp` map; CP comparisons rely on observed facts only.'
        )

    summary = build_summary(
        observed_facts_count=len(observed_facts),
        comparisons=comparisons,
    )

    return PredictionObservationComparisonResult(
        system_id64=system_id64,
        target_archetype=target_archetype,
        generated_at=generated_at,
        summary=summary,
        comparisons=comparisons,
        warnings=warnings,
        assumptions=assumptions,
    )


__all__ = ['compare_prediction_to_observations']
