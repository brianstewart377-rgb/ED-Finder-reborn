"""Ring source payload normalisation for importer scripts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


RING_ARRAY_KEYS = ('rings', 'Rings', 'ring', 'Ring')


@dataclass(frozen=True)
class RingNormalisation:
    rings: list[dict[str, Any]]
    explicit_no_rings: bool = False
    ring_array_present: bool = False


def normalise_ring_payload(payload: dict[str, Any], *, trusted_empty_means_no_rings: bool = False) -> RingNormalisation:
    raw = _first_present_key(payload, RING_ARRAY_KEYS)
    if raw is None:
        return RingNormalisation(rings=[], explicit_no_rings=False, ring_array_present=False)
    if isinstance(raw, dict):
        raw_entries = [raw]
    elif isinstance(raw, (list, tuple)):
        raw_entries = [entry for entry in raw if isinstance(entry, dict)]
    else:
        return RingNormalisation(rings=[], explicit_no_rings=False, ring_array_present=True)
    if not raw_entries:
        return RingNormalisation(rings=[], explicit_no_rings=trusted_empty_means_no_rings, ring_array_present=True)
    return RingNormalisation(rings=[_normalise_ring_entry(entry) for entry in raw_entries], ring_array_present=True)


def ring_rows_for_body(
    payload: dict[str, Any],
    *,
    system_id64: int,
    body_id: Optional[int],
    body_name: Optional[str],
    source: str,
    source_body_id: Optional[int] = None,
    trusted_empty_means_no_rings: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    result = normalise_ring_payload(payload, trusted_empty_means_no_rings=trusted_empty_means_no_rings)
    rows = []
    for ring in result.rings:
        confidence = 'source_ring_payload' if ring.get('ring_type') or ring.get('ring_class') or ring.get('ring_name') else 'partial_source_ring_payload'
        row = {
            'system_id64': system_id64,
            'body_id': body_id,
            'body_name': body_name,
            'ring_name': ring.get('ring_name'),
            'ring_type': ring.get('ring_type'),
            'ring_class': ring.get('ring_class'),
            'mass_mt': ring.get('mass_mt'),
            'inner_radius': ring.get('inner_radius'),
            'outer_radius': ring.get('outer_radius'),
            'source': source,
            'confidence': confidence,
        }
        if source_body_id is not None:
            row['source_body_id'] = source_body_id
        rows.append(row)
    return rows, result.explicit_no_rings


def _normalise_ring_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        'ring_name': _clean_text(_first_present_key(entry, ('ring_name', 'name', 'Name', 'ringName', 'RingName'))),
        'ring_type': _clean_text(_first_present_key(entry, ('ring_type', 'type', 'Type'))),
        'ring_class': _clean_text(_first_present_key(entry, ('ring_class', 'ringClass', 'RingClass', 'class', 'Class'))),
        'mass_mt': _safe_float(_first_present_key(entry, ('mass', 'Mass', 'MassMT', 'massMT', 'mass_mt'))),
        'inner_radius': _safe_float(_first_present_key(entry, ('innerRadius', 'InnerRadius', 'InnerRad', 'innerRad', 'inner_radius', 'inner_rad'))),
        'outer_radius': _safe_float(_first_present_key(entry, ('outerRadius', 'OuterRadius', 'OuterRad', 'outerRad', 'outer_radius', 'outer_rad'))),
    }


def _first_present_key(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
