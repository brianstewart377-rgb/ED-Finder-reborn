"""Pure body/ring enrichment dry-run planning helpers.

This module intentionally has no database or network access. It defines the
report contract and trust classification future body/ring enrichment code can
target before any apply/write path is introduced.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional


DRY_RUN_SCHEMA_VERSION = 'body_ring_enrichment_dry_run/v1'
TRUSTED_RING_ASSOCIATION_STATUS = 'local_matched'

SAFETY_RULES = (
    'Match bodies by trusted local identity before planning writes.',
    'Keep source-specific body identifiers separate from ED-Finder bodies.id.',
    'Use idempotent ring identity: system_id64, body_id, ring_name, source.',
    'Classify identity conflicts instead of guessing.',
    'Do not blindly overwrite populated body metadata with weaker source data.',
    'Preserve source and confidence provenance on every planned row.',
    'Treat missing ring facts as unknown, not not-ringed.',
    'Require trusted local_matched body_rings rows before confirming source-only is_ringed=true.',
)


def is_trusted_ring_row(row: Mapping[str, Any]) -> bool:
    """Return whether a planned/existing ring row confirms local ring state.

    A row with no association_status key at all is untrusted, not trusted —
    silently defaulting a missing trust signal to "trusted" would promote
    any caller that omits the column (a narrower SELECT, a hand-built dict)
    into the trusted set with no error. Requires the key be explicitly
    present and equal to TRUSTED_RING_ASSOCIATION_STATUS.
    """
    return (
        row.get('body_id') is not None
        and row.get('association_status') == TRUSTED_RING_ASSOCIATION_STATUS
    )


def ring_state_from_evidence(
    *,
    scan_is_ringed: Any,
    data_sources: Any,
    trusted_ring_rows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Classify consumer-visible ring state from scan and trusted ring evidence."""
    trusted_rows = [dict(row) for row in trusted_ring_rows if is_trusted_ring_row(row)]
    if trusted_rows:
        return {
            'is_ringed': True,
            'ring_state': 'ringed',
            'reason': 'trusted_body_rings',
            'trusted_ring_rows': len(trusted_rows),
        }

    sources = _normalise_data_sources(data_sources)
    scan_value = _coerce_bool(scan_is_ringed)
    if scan_value is False and 'eddn_scan' in sources:
        return {
            'is_ringed': False,
            'ring_state': 'not_ringed',
            'reason': 'trusted_scan_no_rings',
            'trusted_ring_rows': 0,
        }

    reason = 'missing_ring_evidence'
    if scan_value is True:
        reason = 'source_only_ring_true_requires_trusted_body_rings'
    elif scan_value is False:
        reason = 'false_without_trusted_scan_source'

    return {
        'is_ringed': None,
        'ring_state': 'unknown',
        'reason': reason,
        'trusted_ring_rows': 0,
    }


def build_body_ring_dry_run_report(
    *,
    source: str,
    systems: Sequence[Mapping[str, Any]] = (),
    body_updates: Sequence[Mapping[str, Any]] = (),
    ring_rows: Sequence[Mapping[str, Any]] = (),
    scan_fact_updates: Sequence[Mapping[str, Any]] = (),
    skipped: Sequence[Mapping[str, Any]] = (),
    conflicts: Sequence[Mapping[str, Any]] = (),
    fetch_errors: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build the JSON-safe dry-run report shape for future enrichment stages."""
    system_rows = _dicts(systems)
    body_update_rows = _dicts(body_updates)
    ring_plan_rows = _dicts(ring_rows)
    scan_fact_rows = _dicts(scan_fact_updates)
    skipped_rows = _dicts(skipped)
    conflict_rows = _dicts(conflicts)
    fetch_error_rows = _dicts(fetch_errors)

    trusted_ring_rows = [row for row in ring_plan_rows if is_trusted_ring_row(row)]
    trusted_ring_body_keys = {
        _body_identity_key(row)
        for row in trusted_ring_rows
    }
    source_only_true_scan_facts = [
        row for row in scan_fact_rows
        if (
            _coerce_bool(row.get('is_ringed')) is True
            and _body_identity_key(row) not in trusted_ring_body_keys
        )
    ]
    explicit_no_ring_scan_facts = [
        row for row in scan_fact_rows
        if (
            _coerce_bool(row.get('is_ringed')) is False
            and 'eddn_scan' in _normalise_data_sources(row.get('data_sources'))
        )
    ]
    dirty_system_ids = _system_ids_from(
        body_update_rows,
        trusted_ring_rows,
        explicit_no_ring_scan_facts,
    )

    return {
        'schema_version': DRY_RUN_SCHEMA_VERSION,
        'dry_run': True,
        'source': source,
        'safety_rules': list(SAFETY_RULES),
        'systems': system_rows,
        'body_updates_planned': body_update_rows,
        'ring_rows_planned': ring_plan_rows,
        'scan_fact_updates_planned': scan_fact_rows,
        'skipped': skipped_rows,
        'conflicts': conflict_rows,
        'fetch_errors': fetch_error_rows,
        'dirty_system_ids_planned': dirty_system_ids,
        'summary': {
            'systems': len(_system_ids_from(system_rows)),
            'body_updates_planned': len(body_update_rows),
            'ring_rows_planned': len(ring_plan_rows),
            'trusted_ring_rows_planned': len(trusted_ring_rows),
            'confirmed_ringed_bodies_planned': len(trusted_ring_body_keys),
            'scan_fact_updates_planned': len(scan_fact_rows),
            'explicit_no_ring_scan_facts_planned': len(explicit_no_ring_scan_facts),
            'source_only_ring_true_retained_unknown': len(source_only_true_scan_facts),
            'skipped': len(skipped_rows),
            'conflicts': len(conflict_rows),
            'fetch_errors': len(fetch_error_rows),
            'dirty_systems_planned': len(dirty_system_ids),
        },
    }


def _dicts(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _system_ids_from(*row_groups: Sequence[Mapping[str, Any]]) -> list[int]:
    ids: set[int] = set()
    for rows in row_groups:
        for row in rows:
            system_id = _read_int(_first_present(
                row.get('system_id64'),
                row.get('system_address'),
                row.get('id64'),
            ))
            if system_id is not None:
                ids.add(system_id)
    return sorted(ids)


def _body_identity_key(row: Mapping[str, Any]) -> tuple[int | None, int | None]:
    return (
        _read_int(_first_present(row.get('system_id64'), row.get('system_address'))),
        _read_int(row.get('body_id')),
    )


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _coerce_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {'true', 't', '1', 'yes'}:
        return True
    if text in {'false', 'f', '0', 'no'}:
        return False
    return None


def _normalise_data_sources(data_sources: Any) -> set[str]:
    if data_sources is None:
        return set()
    if isinstance(data_sources, str):
        value = data_sources.strip()
        if value.startswith('{') and value.endswith('}'):
            return {
                part.strip().strip('"')
                for part in value[1:-1].split(',')
                if part.strip()
            }
        return {value} if value else set()
    try:
        return {str(value) for value in data_sources if value is not None}
    except TypeError:
        return set()


def _read_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None
