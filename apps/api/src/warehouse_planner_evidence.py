from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from edfinder_api.config import settings
from edfinder_api.enrichment_operator_status import read_warehouse_status_snapshot
from edfinder_api.warehouse_planner_evidence_models import (
    WarehousePlannerEvidenceBoundedStaging,
    WarehousePlannerEvidenceContract,
    WarehousePlannerEvidenceCoverage,
    WarehousePlannerEvidenceCoverageFreshness,
    WarehousePlannerEvidenceCoverageMetric,
    WarehousePlannerEvidenceEnvelope,
    WarehousePlannerEvidenceFreshness,
    WarehousePlannerEvidenceItem,
    WarehousePlannerEvidenceSourceRun,
    WarehousePlannerEvidenceSummary,
)
from edfinder_api.warehouse_planner_evidence_provider import (
    LivePlannerEvidenceResult,
    load_stage19bb_closeout_metadata,
)


SCHEMA_VERSION = 'warehouse_planner_evidence/v1'


def build_warehouse_planner_evidence(
    id64: int,
    *,
    live_result: LivePlannerEvidenceResult | None = None,
) -> WarehousePlannerEvidenceContract:
    warehouse_status = read_warehouse_status_snapshot(settings.enrichment_warehouse_status_json_path)

    generated_at = (
        _text_or_none(_mapping(warehouse_status.get('artifact')).get('updated_at'))
        or _text_or_none(_mapping(warehouse_status.get('latest_reconciliation_run')).get('generated_at'))
        or datetime.now(timezone.utc).isoformat()
    )

    freshness = WarehousePlannerEvidenceFreshness(
        status=_freshness_status(warehouse_status, live_result),
        evaluated_at=_evaluated_at(live_result),
    )
    source_run = WarehousePlannerEvidenceSourceRun(
        source_name='warehouse_reconciliation' if _source_run_key(warehouse_status) else None,
        run_key=_source_run_key(warehouse_status),
    )

    if live_result is not None:
        bounded_staging = WarehousePlannerEvidenceBoundedStaging.model_validate(
            _model_payload(live_result.bounded_staging)
        )
        coverage = WarehousePlannerEvidenceCoverage.model_validate(
            _model_payload(live_result.coverage)
        )
        items = [
            WarehousePlannerEvidenceItem.model_validate(_model_payload(item))
            for item in live_result.items
        ]
        return WarehousePlannerEvidenceContract(
            schema_version=SCHEMA_VERSION,
            system_id64=id64,
            generated_at=generated_at,
            freshness=freshness,
            source_run=source_run,
            evidence_envelope=_build_evidence_envelope(
                status=live_result.envelope_status,
                items=items,
                bounded_staging=bounded_staging,
            ),
            bounded_staging=bounded_staging,
            coverage=coverage,
            evidence_summary=WarehousePlannerEvidenceSummary(
                availability=live_result.availability,
                report_only=True,
                manual_review_required=live_result.manual_review_required,
                items=items,
            ),
            warnings=live_result.warnings[:8] or _fallback_warnings(warehouse_status),
        )

    return WarehousePlannerEvidenceContract(
        schema_version=SCHEMA_VERSION,
        system_id64=id64,
        generated_at=generated_at,
        freshness=freshness,
        source_run=source_run,
        evidence_envelope=_build_evidence_envelope(
            status=_fallback_envelope_status(warehouse_status),
            items=[],
            bounded_staging=_default_bounded_staging_contract(status='not_evaluated'),
        ),
        bounded_staging=_default_bounded_staging_contract(status='not_evaluated'),
        coverage=_default_coverage_contract(),
        evidence_summary=WarehousePlannerEvidenceSummary(
            availability='unavailable',
            report_only=True,
            manual_review_required=warehouse_status.get('available') is True,
            items=[],
        ),
        warnings=_fallback_warnings(warehouse_status),
    )


def _default_coverage_contract() -> WarehousePlannerEvidenceCoverage:
    unknown_metric = WarehousePlannerEvidenceCoverageMetric(
        status='unknown',
        summary='Coverage is unknown for this metric in the current runtime.',
    )
    return WarehousePlannerEvidenceCoverage(
        body_scan=unknown_metric,
        station_links=unknown_metric,
        ring_identity=unknown_metric,
        source_freshness=WarehousePlannerEvidenceCoverageFreshness(),
        thin_data_reasons=['Selected-system coverage has not been evaluated in this runtime yet.'],
        summary='Coverage remains unknown because selected-system evidence has not been evaluated in this runtime.',
    )


def _fallback_warnings(warehouse_status: Mapping[str, Any]) -> list[str]:
    warnings = _safe_rows(warehouse_status.get('warnings'))
    if warehouse_status.get('available') is False:
        message = _text_or_none(warehouse_status.get('message'))
        if message:
            warnings.append(message)
    elif warehouse_status.get('available') is True:
        warnings.append(
            'No safe per-system warehouse evidence is published for this system yet; planner fallback must remain in place.'
        )
    else:
        warnings.append(
            'Per-system warehouse evidence has not been evaluated for this system yet; planner fallback must remain in place.'
        )
    return warnings[:8]


def _default_bounded_staging_contract(
    *,
    status: str,
    source_run_key: str | None = None,
    bridge_key: str | None = None,
    row_limit: int | None = None,
    available_row_limits: list[int] | None = None,
    matched_row_count: int | None = None,
    latest_source_updated_at: str | None = None,
    summary: str | None = None,
) -> WarehousePlannerEvidenceBoundedStaging:
    metadata = load_stage19bb_closeout_metadata()
    return WarehousePlannerEvidenceBoundedStaging(
        status=status,  # type: ignore[arg-type]
        report_only=True,
        bounded_staging_only=True,
        source_name=metadata.source_name if metadata else None,
        source_batch_label=metadata.source_batch_label if metadata else None,
        source_sha256=metadata.source_sha256 if metadata else None,
        source_run_key=source_run_key,
        bridge_key=bridge_key,
        row_limit=row_limit,
        available_row_limits=available_row_limits or [],
        matched_row_count=matched_row_count,
        latest_source_updated_at=latest_source_updated_at,
        summary=summary,
    )


def _build_evidence_envelope(
    *,
    status: str,
    items: list[WarehousePlannerEvidenceItem],
    bounded_staging: WarehousePlannerEvidenceBoundedStaging,
) -> WarehousePlannerEvidenceEnvelope:
    source_classes = _source_classes(items, bounded_staging)
    semantics = _semantics(items, bounded_staging)
    source_labels = {
        'canonical': 'canonical evidence',
        'observed_facts': 'observed-facts evidence',
        'bounded_staging': 'bounded staging evidence',
        'derived_report': 'derived report evidence',
        'unavailable': 'no linked selected-system evidence',
    }
    status_text = {
        'available': 'Selected-system evidence is available in this read-only planner envelope.',
        'unavailable': 'Selected-system evidence is unavailable in this read-only planner envelope.',
        'not_evaluated': 'Selected-system evidence was not evaluated in this runtime.',
        'unknown': 'Selected-system evidence remains unknown in this runtime.',
    }[status]
    source_summary = ', '.join(source_labels[value] for value in source_classes)
    return WarehousePlannerEvidenceEnvelope(
        status=status,  # type: ignore[arg-type]
        source_classes=source_classes,
        semantics=semantics,
        report_only=True,
        selected_system_only=True,
        planner_truth_source_class='canonical' if any(item.source == 'canonical' for item in items) else 'unavailable',
        claims_canonical_truth=False,
        claims_full_coverage=False,
        summary=f'{status_text} Source classes: {source_summary}.',
    )


def _source_classes(
    items: list[WarehousePlannerEvidenceItem],
    bounded_staging: WarehousePlannerEvidenceBoundedStaging,
) -> list[str]:
    values: list[str] = []
    for item in items:
        mapped = {
            'canonical': 'canonical',
            'observed': 'observed_facts',
            'warehouse_report_only': 'derived_report',
            'unknown': 'unavailable',
        }[item.source]
        if mapped not in values:
            values.append(mapped)
    if bounded_staging.status == 'available' and 'bounded_staging' not in values:
        values.append('bounded_staging')
    if not values:
        values.append('unavailable')
    return values


def _semantics(
    items: list[WarehousePlannerEvidenceItem],
    bounded_staging: WarehousePlannerEvidenceBoundedStaging,
) -> list[str]:
    values = ['report_only_review_context', 'not_full_coverage']
    if any(item.source == 'canonical' for item in items):
        values.insert(0, 'canonical_truth')
    if any(item.source == 'observed' for item in items) and 'observed_report' not in values:
        values.append('observed_report')
    if bounded_staging.status == 'available' and 'bounded_staging_evidence' not in values:
        values.append('bounded_staging_evidence')
    return values


def _fallback_envelope_status(warehouse_status: Mapping[str, Any]) -> str:
    if warehouse_status.get('available') is True:
        return 'not_evaluated'
    if warehouse_status.get('available') is False:
        return 'unavailable'
    return 'unknown'


def _freshness_status(
    warehouse_status: Mapping[str, Any],
    live_result: LivePlannerEvidenceResult | None,
) -> str:
    evidence_health = _mapping(warehouse_status.get('evidence_health'))
    stale_records = _int_or_none(evidence_health.get('stale_records')) or 0
    if live_result is not None:
        return live_result.freshness_status
    if stale_records > 0:
        return 'stale'
    if warehouse_status.get('available') is True:
        return 'not_evaluated'
    return 'unknown'


def _evaluated_at(live_result: LivePlannerEvidenceResult | None) -> str | None:
    if live_result is not None:
        return live_result.evaluated_at
    return None


def _source_run_key(warehouse_status: Mapping[str, Any]) -> str | None:
    latest = _mapping(warehouse_status.get('latest_reconciliation_run'))
    artifact = _mapping(warehouse_status.get('artifact'))
    report_name = _text_or_none(latest.get('report_file_name'))
    artifact_name = _text_or_none(artifact.get('file_name'))
    if report_name:
        return f'warehouse/{report_name}'
    if artifact_name:
        return f'warehouse/{artifact_name}'
    return None


def _safe_rows(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        text = _text_or_none(item)
        if text:
            rows.append(text)
    return rows


def _model_payload(value: Any) -> Any:
    if hasattr(value, 'model_dump'):
        return value.model_dump()
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None
