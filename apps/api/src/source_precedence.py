from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

BODY_SCAN_FACT_FIELDS = (
    'system_address',
    'body_id',
    'body_name',
    'radius',
    'mass_em',
    'gravity',
    'surface_temp',
    'surface_pressure',
    'planet_class',
    'terraform_state',
    'atmosphere',
    'volcanism',
    'semi_major_axis',
    'orbital_period',
    'parents',
    'has_geo',
    'has_bio',
    'geo_signal_count',
    'bio_signal_count',
    'is_landable',
    'is_terraformable',
    'is_ringed',
    'data_sources',
    'confidence',
)

MergeResolution = Literal[
    'insert',
    'prefer_incoming',
    'fill_missing',
    'preserve_existing',
    'preserve_existing_consensus',
]

_BODY_SCAN_SOURCE_FAMILY_ALIASES = {
    'manual': 'manual',
    'manual_operator_source': 'manual',
    'frontier_journal': 'frontier_journal',
    'frontier_journal_scan': 'frontier_journal',
    'frontier_journal_fssbodysignals': 'frontier_journal',
    'frontier_journal_saasignals': 'frontier_journal',
    'eddn': 'eddn',
    'eddn_scan': 'eddn',
    'eddn_fssbodysignals': 'eddn',
    'eddn_saasignals': 'eddn',
    'eddn_journal_signals': 'eddn',
    'spansh': 'spansh',
    'spansh_import': 'spansh',
    'spansh_dump': 'spansh',
}

_BODY_SCAN_SOURCE_RANK = {
    'unknown': 0,
    'spansh': 10,
    'eddn': 20,
    'frontier_journal': 30,
    'manual': 40,
}


@dataclass(frozen=True)
class BodyScanMergeDecision:
    row: dict[str, Any]
    resolution: MergeResolution


def source_family(source: str | None) -> str:
    text = str(source or '').strip().lower()
    if not text:
        return 'unknown'
    return _BODY_SCAN_SOURCE_FAMILY_ALIASES.get(text, text)


def body_scan_source_rank(source: str | None) -> int:
    return _BODY_SCAN_SOURCE_RANK.get(source_family(source), 0)


def merge_body_scan_fact(
    existing: Mapping[str, Any] | None,
    incoming: Mapping[str, Any],
) -> BodyScanMergeDecision:
    merged = {field: incoming.get(field) for field in BODY_SCAN_FACT_FIELDS}
    incoming_sources = _normalise_sources(incoming.get('data_sources'))
    existing_sources = _normalise_sources(existing.get('data_sources') if existing else None)
    incoming_source = incoming_sources[0] if incoming_sources else 'unknown'
    incoming_family = source_family(incoming_source)
    existing_families = {source_family(source) for source in existing_sources if source_family(source) != 'unknown'}
    non_journal_existing_families = {
        family for family in existing_families if family != 'frontier_journal'
    }
    same_family = incoming_family in existing_families and incoming_family != 'unknown'
    highest_existing_rank = max((body_scan_source_rank(source) for source in existing_sources), default=0)
    incoming_rank = body_scan_source_rank(incoming_source)

    if existing is None:
        merged['data_sources'] = incoming_sources
        return BodyScanMergeDecision(
            row=_finalise_body_scan_row(merged),
            resolution='insert',
        )

    preserve_consensus = (
        incoming_family == 'frontier_journal'
        and len(non_journal_existing_families) >= 2
    )

    if preserve_consensus:
        resolution: MergeResolution = 'preserve_existing_consensus'
        merged = _preserve_existing_non_null(existing, merged)
    elif same_family or incoming_rank > highest_existing_rank:
        resolution = 'prefer_incoming' if _incoming_overrides_existing(existing, incoming) else 'fill_missing'
        merged = _fill_from_existing_when_missing(existing, merged)
    else:
        resolution = 'preserve_existing'
        merged = _preserve_existing_non_null(existing, merged)

    merged['data_sources'] = _merge_sources(existing_sources, incoming_sources)
    existing_confidence = existing.get('confidence')
    incoming_confidence = incoming.get('confidence')
    if resolution in {'prefer_incoming', 'fill_missing'}:
        merged['confidence'] = _max_confidence(existing_confidence, incoming_confidence)
    else:
        merged['confidence'] = existing_confidence if existing_confidence is not None else incoming_confidence

    return BodyScanMergeDecision(
        row=_finalise_body_scan_row(merged),
        resolution=resolution,
    )


def _normalise_sources(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    ordered: list[str] = []
    for item in value:
        source = str(item or '').strip()
        if source and source not in ordered:
            ordered.append(source)
    return ordered


def _merge_sources(existing_sources: list[str], incoming_sources: list[str]) -> list[str]:
    ordered: list[str] = []
    for source in [*existing_sources, *incoming_sources]:
        if source and source not in ordered:
            ordered.append(source)
    return ordered


def _fill_from_existing_when_missing(
    existing: Mapping[str, Any],
    merged: dict[str, Any],
) -> dict[str, Any]:
    for field in BODY_SCAN_FACT_FIELDS:
        if field in {'data_sources', 'confidence'}:
            continue
        if merged.get(field) is None and existing.get(field) is not None:
            merged[field] = existing.get(field)
    return merged


def _preserve_existing_non_null(
    existing: Mapping[str, Any],
    merged: dict[str, Any],
) -> dict[str, Any]:
    for field in BODY_SCAN_FACT_FIELDS:
        if field in {'data_sources', 'confidence'}:
            continue
        if existing.get(field) is not None:
            merged[field] = existing.get(field)
    return merged


def _incoming_overrides_existing(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    for field in BODY_SCAN_FACT_FIELDS:
        if field in {'data_sources', 'confidence'}:
            continue
        existing_value = existing.get(field)
        incoming_value = incoming.get(field)
        if existing_value is not None and incoming_value is not None and incoming_value != existing_value:
            return True
    return False


def _max_confidence(left: Any, right: Any) -> Any:
    left_value = float(left or 0)
    right_value = float(right or 0)
    return left if left_value >= right_value else right


def _finalise_body_scan_row(row: dict[str, Any]) -> dict[str, Any]:
    final = dict(row)
    final['data_sources'] = _normalise_sources(final.get('data_sources'))
    if final.get('confidence') is None:
        final['confidence'] = 0.0
    return final
