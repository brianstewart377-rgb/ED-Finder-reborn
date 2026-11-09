"""Read-only, sanitized enrichment status snapshot helpers.

The API must not run the enrichment guard, invoke Docker, or call live APIs
from a request handler. Operators can publish the output of
``station_enrichment_status.py --json`` to a shared JSON artifact, and this
module reduces that artifact to a UI-safe status model.
"""
from __future__ import annotations

from typing import Any

from enrichment_operator_status_constants import MAX_STATUS_ARTIFACT_BYTES, SENSITIVE_TEXT_MARKERS
from enrichment_operator_status_io import _artifact_info, _read_snapshot, _unavailable, _warehouse_unavailable
from enrichment_operator_status_station import sanitize_station_enrichment_status
from enrichment_operator_status_warehouse import sanitize_warehouse_status
from pydantic import ValidationError
from shared_contracts.enrichment_artifact_contracts import validate_warehouse_status_artifact

__all__ = [
    'MAX_STATUS_ARTIFACT_BYTES',
    'SENSITIVE_TEXT_MARKERS',
    'read_enrichment_status_snapshot',
    'read_warehouse_status_snapshot',
    'sanitize_station_enrichment_status',
    'sanitize_warehouse_status',
]


def read_enrichment_status_snapshot(path_value: str | None) -> dict[str, Any]:
    return _read_snapshot(
        path_value,
        unavailable_fn=_unavailable,
        sanitize_fn=sanitize_station_enrichment_status,
        not_configured_message='Enrichment status artifact is not configured.',
        missing_message='Enrichment status artifact is unavailable.',
        invalid_file_message='Enrichment status artifact is not a regular file.',
        too_large_message='Enrichment status artifact is too large to display safely.',
        invalid_json_message='Enrichment status artifact is not valid JSON.',
        unreadable_message='Enrichment status artifact could not be read.',
    )


def read_warehouse_status_snapshot(path_value: str | None) -> dict[str, Any]:
    return _read_snapshot(
        path_value,
        unavailable_fn=_warehouse_unavailable,
        sanitize_fn=_validate_and_sanitize_warehouse_status,
        not_configured_message='Warehouse status artifact is not configured.',
        missing_message='Warehouse status artifact is unavailable.',
        invalid_file_message='Warehouse status artifact is not a regular file.',
        too_large_message='Warehouse status artifact is too large to display safely.',
        invalid_json_message='Warehouse status artifact is not valid JSON.',
        unreadable_message='Warehouse status artifact could not be read.',
    )


def _validate_and_sanitize_warehouse_status(
    payload: dict[str, Any],
    *,
    artifact_path=None,
) -> dict[str, Any]:
    try:
        validate_warehouse_status_artifact(payload)
    except ValidationError:
        return {
            **_warehouse_unavailable(
                'invalid',
                'Warehouse status artifact does not match the shared contract.',
            ),
            'artifact': _artifact_info(artifact_path, exists=True),
        }
    return sanitize_warehouse_status(payload, artifact_path=artifact_path)
