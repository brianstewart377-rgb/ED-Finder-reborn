"""Serializable schema helpers for observation comparison outputs."""
from __future__ import annotations

from typing import Any

from edfinder_api.observations.models import ObservationSummary, PredictionObservationDiff


def observation_summary_to_dict(summary: ObservationSummary) -> dict[str, Any]:
    return summary.to_dict()


def prediction_observation_diffs_to_dict(diffs: list[PredictionObservationDiff]) -> list[dict[str, Any]]:
    return [diff.to_dict() for diff in diffs]


def predicted_only_summary() -> dict[str, Any]:
    return ObservationSummary(
        status='predicted_only',
        observed_facts_count=0,
        confirmed_count=0,
        mismatch_count=0,
        observed_only_count=0,
        predicted_only_count=0,
        unknown_count=0,
        confidence_impact='none',
        summary='No observed player data is attached to this simulation yet. Results are predicted from current mechanics rules.',
    ).to_dict()
