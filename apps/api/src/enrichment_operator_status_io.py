from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from enrichment_operator_status_constants import MAX_STATUS_ARTIFACT_BYTES


def _unavailable(state: str, message: str) -> dict[str, Any]:
    return {
        'available': False,
        'configured': state != 'not_configured',
        'state': state,
        'message': message,
        'source': 'station_enrichment_status_json',
        'artifact': None,
        'checkpoint': None,
        'latest_run': None,
        'latest_batch': None,
        'latest_report': None,
        'latest_progress': None,
        'rate_limit': None,
        'warnings': [],
    }


def _warehouse_unavailable(state: str, message: str) -> dict[str, Any]:
    return {
        'available': False,
        'configured': state != 'not_configured',
        'state': state,
        'message': message,
        'source': 'warehouse_reconciliation_status_json',
        'artifact': None,
        'latest_snapshot_load': None,
        'latest_reconciliation_run': None,
        'source_coverage': None,
        'evidence_health': None,
        'canonical_safety': None,
        'warnings': [],
        'errors': [],
    }


def _artifact_info(path: Path | None, *, exists: bool) -> dict[str, Any] | None:
    if path is None:
        return None
    updated_at = None
    age_seconds = None
    try:
        stat = path.stat()
    except OSError:
        stat = None
    if stat is not None:
        updated = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        updated_at = updated.isoformat()
        age_seconds = max(0, int((datetime.now(timezone.utc) - updated).total_seconds()))
    return {
        'file_name': path.name,
        'exists': exists,
        'updated_at': updated_at,
        'age_seconds': age_seconds,
        'path_visible': False,
    }


SnapshotUnavailable = Callable[[str, str], dict[str, Any]]
SnapshotSanitizer = Callable[..., dict[str, Any]]


def _read_snapshot(
    path_value: str | None,
    *,
    unavailable_fn: SnapshotUnavailable,
    sanitize_fn: SnapshotSanitizer,
    not_configured_message: str,
    missing_message: str,
    invalid_file_message: str,
    too_large_message: str,
    invalid_json_message: str,
    unreadable_message: str,
) -> dict[str, Any]:
    if not path_value:
        return unavailable_fn('not_configured', not_configured_message)

    path = Path(path_value).expanduser()
    if not path.exists():
        return {
            **unavailable_fn('missing', missing_message),
            'artifact': _artifact_info(path, exists=False),
        }
    if not path.is_file():
        return {
            **unavailable_fn('invalid', invalid_file_message),
            'artifact': _artifact_info(path, exists=True),
        }

    try:
        size = path.stat().st_size
    except OSError:
        size = None
    if size is not None and size > MAX_STATUS_ARTIFACT_BYTES:
        return {
            **unavailable_fn('too_large', too_large_message),
            'artifact': _artifact_info(path, exists=True),
        }

    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {
            **unavailable_fn('invalid_json', invalid_json_message),
            'artifact': _artifact_info(path, exists=True),
        }
    except OSError:
        return {
            **unavailable_fn('unreadable', unreadable_message),
            'artifact': _artifact_info(path, exists=True),
        }
    if not isinstance(payload, Mapping):
        return {
            **unavailable_fn('invalid_json', invalid_json_message),
            'artifact': _artifact_info(path, exists=True),
        }

    return sanitize_fn(payload, artifact_path=path)
