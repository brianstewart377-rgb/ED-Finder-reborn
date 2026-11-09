"""Stage 6C comparison — prediction extractors.

Pulls the predicted-side facts out of a Simulation Preview prediction
mapping. Each extractor is tolerant: shapes that don't type-check are
skipped silently rather than raising, because Stage 6C is a comparison
engine — refusing to compare just because the prediction shape is unusual
would defeat its purpose. The engine layer surfaces extractor "didn't
find anything" cases as ``assumptions`` strings in the result.

This module owns nothing about observations and produces no comparison
rows. Its outputs feed the rule modules.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def extract_service_predictions(prediction: Mapping[str, Any]) -> dict[str, str | None]:
    """Pull predicted service IDs → status from the prediction.

    Reads two complementary fields:

    * ``services`` — top-level mapping of ``service_id -> {status,...}``
      produced by Simulation Preview.
    * ``port_service_states`` — per-port buckets (active/locked/unknown).
      Status precedence: ``active`` > ``locked`` > top-level value.

    Tolerant of partial shapes — anything that fails type checks is
    skipped rather than raising.
    """
    result: dict[str, str | None] = {}

    services_field = prediction.get('services')
    if isinstance(services_field, Mapping):
        for service_id, payload in services_field.items():
            if not isinstance(service_id, str):
                continue
            if isinstance(payload, Mapping):
                status = payload.get('status')
                result[service_id] = str(status) if status is not None else None
            else:
                # services map may carry simple presence values
                result[service_id] = str(payload) if payload is not None else None

    port_states = prediction.get('port_service_states')
    if isinstance(port_states, list):
        for state in port_states:
            if not isinstance(state, Mapping):
                continue
            for bucket, override_status in (
                ('active_services', 'active'),
                ('locked_services', 'locked'),
                ('unknown_services', 'unknown'),
            ):
                services = state.get(bucket)
                if not isinstance(services, Mapping):
                    continue
                for service_id in services.keys():
                    if not isinstance(service_id, str):
                        continue
                    # Active wins over locked wins over unknown wins
                    # over the (possibly null) top-level value.
                    if (
                        result.get(service_id) is None
                        or (override_status == 'active')
                        or (override_status == 'locked' and result.get(service_id) != 'active')
                    ):
                        result[service_id] = override_status

    return result


def extract_economy_predictions(prediction: Mapping[str, Any]) -> dict[str, bool]:
    """Pull predicted economy names → present-bool from the prediction.

    Combines ``economy_composition`` (dict of economy → weight, present
    iff weight > 0) and ``economy_order`` (list, ordered most→least).
    A name appearing in either source is considered predicted-present.
    Anything that fails type checks is skipped, not raised.
    """
    result: dict[str, bool] = {}

    composition = prediction.get('economy_composition')
    if isinstance(composition, Mapping):
        for name, weight in composition.items():
            if not isinstance(name, str):
                continue
            try:
                w = float(weight) if weight is not None else 0.0
            except (TypeError, ValueError):
                w = 0.0
            if w > 0:
                result[name] = True

    order = prediction.get('economy_order')
    if isinstance(order, list):
        for name in order:
            if isinstance(name, str):
                result.setdefault(name, True)

    return result


__all__ = [
    'extract_economy_predictions',
    'extract_service_predictions',
]
