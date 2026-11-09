from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


def json_dumps_canonical(value: Any) -> str:
    return json.dumps(value, separators=(',', ':'), sort_keys=True)


def datetime_to_utc_isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def coerce_optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        parsed = datetime.fromisoformat(stripped.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    raise TypeError('expected datetime, ISO-8601 string, or None')


def content_addressed_evidence_key(payload: Mapping[str, Any]) -> str:
    canonical = {
        'system_id64': payload['system_id64'],
        'source_name': payload['source_name'],
        'subject_type': payload['subject_type'],
        'subject_id': payload.get('subject_id'),
        'evidence_type': payload['evidence_type'],
        'observed_at': datetime_to_utc_isoformat(coerce_optional_datetime(payload.get('observed_at'))),
        'source_record_id': payload.get('source_record_id'),
        'value': payload.get('value') or {},
    }
    digest = hashlib.sha256(json_dumps_canonical(canonical).encode('utf-8')).hexdigest()
    return f'evd_{digest}'
