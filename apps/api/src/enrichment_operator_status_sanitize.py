from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from enrichment_operator_status_constants import SENSITIVE_TEXT_MARKERS


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _basename(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value).name


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_error(value: Any) -> str | None:
    return _safe_text(value)


def _safe_text(value: Any) -> str | None:
    text = _text_or_none(value)
    if not text:
        return None
    lowered = text.lower()
    if '/' in text or '\\' in text or any(marker in lowered for marker in SENSITIVE_TEXT_MARKERS):
        return 'unavailable'
    return text


def _safe_schema_version(value: Any) -> str | None:
    text = _text_or_none(value)
    if not text:
        return None
    parts = text.split('/')
    if len(parts) == 2:
        family, version = parts
        family_safe = family.replace('_', '').replace('-', '').replace('.', '').isalnum()
        version_safe = version.startswith('v') and version[1:].isdigit()
        if family_safe and version_safe:
            return text
    return _safe_text(text)


def _safe_distribution(value: Any) -> dict[str, int] | None:
    mapping = _mapping(value)
    if not mapping:
        return None
    safe: dict[str, int] = {}
    for key, item in mapping.items():
        safe_key = _safe_text(key)
        safe_value = _int_or_none(item)
        if safe_key is not None and safe_value is not None:
            safe[safe_key] = safe_value
    return dict(sorted(safe.items()))


def _safe_warning_rows(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    warnings: list[str] = []
    for item in value[:5]:
        if isinstance(item, Mapping):
            text = _safe_text(item.get('reason') or item.get('message') or item.get('error'))
        else:
            text = _safe_text(item)
        if text:
            warnings.append(text)
    return warnings


def _entity_candidate_count(entities: Mapping[str, Any], entity: str) -> int | None:
    return _int_or_none(_mapping(entities.get(entity)).get('candidates'))


def _dist_count(mapping: Mapping[str, Any], key: str) -> int | None:
    if not mapping:
        return None
    value = _int_or_none(mapping.get(key))
    return value if value is not None else 0


def _first_distribution(*values: Any) -> dict[str, int] | None:
    for value in values:
        dist = _safe_distribution(value)
        if dist:
            return dist
    return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        parsed = _int_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _length_or_none(value: Any) -> int | None:
    if isinstance(value, list):
        return len(value)
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _number_or_none(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
