"""Pure V3 row adapter for the canonical surface-slot predictor.

The planner never publishes the predictor's orbital estimate. Canonical and
derived facts must have matching lineage before a planet class can be used.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from edfinder_api.ingest.slot_prediction import (
    INSUFFICIENT_DATA_REASON,
    PREDICTION_VERSION,
    predict_body_slots,
)


@dataclass(frozen=True)
class SurfaceSlotPrediction:
    predicted_surface_slots: int | None = None
    prediction_version: str | None = None
    prediction_status: Literal['predicted', 'unknown'] | None = None
    missing_inputs: list[str] | None = None
    prediction_reason: str | None = None


def prediction_generation_reason(
    canonical_generation_id: str,
    published: Mapping[str, Any] | None,
) -> str | None:
    """Explain unavailable derived facts without rejecting canonical bodies."""
    if published is None:
        return 'published derived generation is unavailable'
    if str(published['canonical_generation_id']) != canonical_generation_id:
        return 'published derived generation uses a different canonical generation'
    return None


def slot_prediction_inputs(
    body: Mapping[str, Any],
    mechanics: Mapping[str, Any] | None,
    signals: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Map canonical units and vocabulary names without filling unknown facts."""
    radius_km = body['radius_km']
    no_signals = 0 if body['signals_complete'] is True else None
    return {
        'body_id': body['body_pk'],
        'body_name': body['name'],
        'is_landable': body['is_landable'],
        'radius': radius_km * 1000 if radius_km is not None else None,
        'surface_temp': body['surface_temperature_k'],
        'gravity': body['surface_gravity_g'],
        'atmosphere': body['atmosphere'],
        'planet_class': mechanics['body_class'] if mechanics is not None else None,
        'terraform_state': body['terraforming_state'],
        'volcanism': body['volcanism'],
        'geo_signal_count': signals['geological_signals'] if signals is not None else no_signals,
        'bio_signal_count': signals['biological_signals'] if signals is not None else no_signals,
    }


def predict_surface_slots(
    body: Mapping[str, Any],
    mechanics: Mapping[str, Any] | None,
    signals: Mapping[str, Any] | None,
    *,
    generation_reason: str | None,
) -> SurfaceSlotPrediction:
    """Expose only eligible surface predictions; entered slots live in the plan."""
    if generation_reason is not None:
        return SurfaceSlotPrediction(prediction_reason=generation_reason)
    body_type = ' '.join((body['body_type'] or '').casefold().replace('_', ' ').split())
    if body_type in {'barycentre', 'belt', 'belt cluster', 'star'}:
        return SurfaceSlotPrediction(
            prediction_reason='surface prediction is not applicable to this body type',
        )
    if body['is_landable'] is False:
        return SurfaceSlotPrediction(prediction_reason='body is not landable')

    inputs = slot_prediction_inputs(body, mechanics, signals)
    prediction = predict_body_slots(inputs)
    missing = set(prediction.required_input_missing)
    # The canonical predictor treats class as an optional bonus input. S3b
    # requires its published subtype: a missing/broad class cannot establish
    # whether the high-metal-content bonus applies.
    body_class = ' '.join((inputs['planet_class'] or '').casefold().split())
    if body_class in {'', 'planet', 'unknown'}:
        missing.add('planet_class')
    if missing:
        return SurfaceSlotPrediction(
            prediction_version=PREDICTION_VERSION,
            prediction_status='unknown',
            missing_inputs=sorted(missing),
            prediction_reason=INSUFFICIENT_DATA_REASON,
        )
    return SurfaceSlotPrediction(
        predicted_surface_slots=prediction.predicted_ground_slots,
        prediction_version=prediction.prediction_version,
        prediction_status='predicted',
        missing_inputs=[],
    )
