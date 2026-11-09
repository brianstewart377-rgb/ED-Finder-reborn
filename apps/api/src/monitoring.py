"""Read-only helpers for operational metrics exposed by the API."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def data_invariants_receipt_metrics(
    paths: Iterable[str | Path],
    *,
    now: datetime | None = None,
) -> tuple[int, float]:
    """Return status (-1 unknown, 0 failed, 1 passed) and receipt age.

    Receipt paths are fixed operator-produced JSON artifacts mounted read-only.
    Invalid, missing, or incomplete files are ignored. When several receipts
    exist, the most recently completed one is authoritative.
    """
    latest: tuple[datetime, int] | None = None
    for raw_path in paths:
        try:
            payload = json.loads(Path(raw_path).read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError, TypeError):
            continue

        completed_at = _parse_utc(payload.get('completed_at_utc'))
        status = payload.get('status')
        if completed_at is None or status not in {'passed', 'failed'}:
            continue
        candidate = (completed_at, 1 if status == 'passed' else 0)
        if latest is None or candidate[0] > latest[0]:
            latest = candidate

    if latest is None:
        return -1, float('nan')

    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_seconds = max(0.0, (observed_at - latest[0]).total_seconds())
    return latest[1], age_seconds
