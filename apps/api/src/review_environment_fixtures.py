from __future__ import annotations

from typing import Any

# These support-route payloads stay synthetic and never become canonical facts.
REVIEW_SYSTEMS: tuple[dict[str, Any], ...] = (
    {'id64': 9100000000001, 'name': 'Review Wiring'},
    {'id64': 9100000000002, 'name': 'Review Aggregate'},
    {'id64': 9100000000003, 'name': 'Review Fallback'},
)
REVIEW_FALLBACK_SYSTEM_ID64 = 9100000000003


_UNKNOWN_WAREHOUSE_COVERAGE: dict[str, Any] = {
    'body_scan': {
        'status': 'unknown',
        'known_count': None,
        'total_count': None,
        'coverage_ratio': None,
        'summary': 'Review fixture coverage is intentionally synthetic and unknown.',
    },
    'station_links': {
        'status': 'unknown',
        'known_count': None,
        'total_count': None,
        'coverage_ratio': None,
        'summary': 'Review fixture station-link coverage is intentionally synthetic and unknown.',
    },
    'ring_identity': {
        'status': 'unknown',
        'known_count': None,
        'total_count': None,
        'coverage_ratio': None,
        'summary': 'Review fixture ring-identity coverage is intentionally synthetic and unknown.',
    },
    'source_freshness': {
        'canonical_updated_at': None,
        'observed_updated_at': None,
        'bounded_staging_updated_at': None,
        'status_updated_at': None,
    },
    'thin_data_reasons': [
        'Review fixtures are report-only synthetic scenarios and do not claim full selected-system coverage.',
    ],
    'summary': 'Review fixture coverage remains synthetic, report-only, and not full coverage.',
}


REVIEW_WAREHOUSE_CONTRACTS: dict[int, dict[str, Any]] = {
    9100000000001: {
        'schema_version': 'warehouse_planner_evidence/v1',
        'system_id64': 9100000000001,
        'generated_at': '2026-06-21T12:00:00Z',
        'freshness': {
            'status': 'fresh',
            'evaluated_at': '2026-06-21T12:00:00Z',
        },
        'source_run': {
            'source_name': 'review_fixture',
            'run_key': 'review-fixture/review-wiring-evidence',
        },
        'evidence_envelope': {
            'status': 'available',
            'source_classes': ['canonical', 'bounded_staging', 'derived_report'],
            'semantics': [
                'canonical_truth',
                'bounded_staging_evidence',
                'report_only_review_context',
                'not_full_coverage',
            ],
            'report_only': True,
            'selected_system_only': True,
            'planner_truth_source_class': 'canonical',
            'claims_canonical_truth': False,
            'claims_full_coverage': False,
            'summary': 'Canonical planner data remains the planner truth source; review-only supporting evidence is report-only and bounded.',
        },
        'bounded_staging': {
            'status': 'available',
            'report_only': True,
            'bounded_staging_only': True,
            'source_name': 'review_fixture',
            'source_batch_label': 'review-wiring-window-25',
            'source_sha256': 'review-wiring-not-canonical',
            'source_run_key': 'review-fixture/review-wiring-bounded-staging',
            'bridge_key': 'source_runs:review-fixture/review-wiring-bounded-staging',
            'row_limit': 25,
            'available_row_limits': [25, 100],
            'matched_row_count': 3,
            'latest_source_updated_at': '2026-06-21T12:00:00Z',
            'summary': 'Bounded staging covers only a review-only 25-row window and is not full coverage.',
        },
        'coverage': _UNKNOWN_WAREHOUSE_COVERAGE,
        'evidence_summary': {
            'availability': 'report_only',
            'report_only': True,
            'manual_review_required': True,
            'items': [
                {
                    'label': 'report_only',
                    'source': 'canonical',
                    'summary': 'Canonical planner data is available and remains the planner truth source for Review Wiring.',
                },
                {
                    'label': 'report_only',
                    'source': 'warehouse_report_only',
                    'summary': 'Supporting evidence is synthetic, review-only, and explicitly non-canonical.',
                },
                {
                    'label': 'needs_review',
                    'source': 'warehouse_report_only',
                    'summary': 'Coverage is bounded and incomplete, so manual review remains required.',
                },
            ],
        },
        'warnings': [
            'Review Wiring evidence is synthetic review-only context and never canonical truth.',
        ],
    },
    9100000000002: {
        'schema_version': 'warehouse_planner_evidence/v1',
        'system_id64': 9100000000002,
        'generated_at': '2026-06-21T12:05:00Z',
        'freshness': {
            'status': 'unknown',
            'evaluated_at': None,
        },
        'source_run': {
            'source_name': 'review_fixture',
            'run_key': 'review-fixture/review-aggregate-evidence',
        },
        'evidence_envelope': {
            'status': 'unavailable',
            'source_classes': ['unavailable'],
            'semantics': ['report_only_review_context'],
            'report_only': True,
            'selected_system_only': True,
            'planner_truth_source_class': 'unavailable',
            'claims_canonical_truth': False,
            'claims_full_coverage': False,
            'summary': 'Selected-system evidence is intentionally unavailable for Review Aggregate.',
        },
        'bounded_staging': {
            'status': 'unavailable',
            'report_only': True,
            'bounded_staging_only': True,
            'source_name': 'review_fixture',
            'source_batch_label': None,
            'source_sha256': None,
            'source_run_key': None,
            'bridge_key': None,
            'row_limit': None,
            'available_row_limits': [],
            'matched_row_count': None,
            'latest_source_updated_at': None,
            'summary': 'No selected-system review evidence is linked for Review Aggregate.',
        },
        'coverage': _UNKNOWN_WAREHOUSE_COVERAGE,
        'evidence_summary': {
            'availability': 'unavailable',
            'report_only': True,
            'manual_review_required': False,
            'items': [],
        },
        'warnings': [
            'Review Aggregate is intentionally configured with unavailable selected-system evidence.',
        ],
    },
    9100000000003: {
        'schema_version': 'warehouse_planner_evidence/v1',
        'system_id64': 9100000000003,
        'generated_at': '2026-06-21T12:15:00Z',
        'freshness': {
            'status': 'not_evaluated',
            'evaluated_at': None,
        },
        'source_run': {
            'source_name': 'review_fixture',
            'run_key': 'review-fixture/review-fallback-evidence',
        },
        'evidence_envelope': {
            'status': 'not_evaluated',
            'source_classes': ['derived_report'],
            'semantics': ['report_only_review_context'],
            'report_only': True,
            'selected_system_only': True,
            'planner_truth_source_class': 'canonical',
            'claims_canonical_truth': False,
            'claims_full_coverage': False,
            'summary': 'Selected-system evidence is intentionally not evaluated for Review Fallback.',
        },
        'bounded_staging': {
            'status': 'not_evaluated',
            'report_only': True,
            'bounded_staging_only': True,
            'source_name': 'review_fixture',
            'source_batch_label': None,
            'source_sha256': None,
            'source_run_key': None,
            'bridge_key': None,
            'row_limit': None,
            'available_row_limits': [],
            'matched_row_count': None,
            'latest_source_updated_at': None,
            'summary': 'Bounded staging is intentionally not evaluated for Review Fallback.',
        },
        'coverage': _UNKNOWN_WAREHOUSE_COVERAGE,
        'evidence_summary': {
            'availability': 'report_only',
            'report_only': True,
            'manual_review_required': True,
            'items': [
                {
                    'label': 'blocked',
                    'source': 'warehouse_report_only',
                    'summary': 'This review-only case keeps the evidence path not evaluated for acceptance coverage.',
                },
            ],
        },
        'warnings': [
            'Review Fallback is intentionally configured with a not-evaluated evidence path.',
        ],
    },
}


REVIEW_PROVENANCE_CONTRACTS: dict[int, dict[str, Any]] = {
    9100000000001: {
        'schema_version': 'stage20a_provenance_cockpit/v1',
        'system': {
            'id64': 9100000000001,
            'name': 'Review Wiring',
            'primary_archetype': 'hightech_refinery',
        },
        'provenance_summary': {
            'state': 'available',
            'latest_source_run_key': 'review-fixture/review-wiring-provenance',
            'warehouse_state': 'available',
            'planner_evidence_state': 'available',
        },
        'evidence_panels': {
            'source_run': {
                'state': 'available',
                'source_name': 'review_fixture',
                'rows_read': 25,
                'rows_staged': 25,
                'artifact_name': 'review-wiring-provenance.json',
            },
            'warehouse': {
                'state': 'available',
                'report_only': True,
                'canonical_writes_planned': 0,
                'stale_records': 0,
            },
            'planner': {
                'state': 'available',
                'observed_facts_count': 2,
                'projected_build_count': 1,
                'manual_review_required': True,
            },
        },
        'guardrails': {
            'stage19_paused': True,
            'stage19_production_activation_complete': False,
            'next_stage19_write_lane_authorized': False,
            'canonical_apply_complete': False,
            'rebaseline_complete': False,
            'scheduler_enabled': False,
            'db_writes_authorized': False,
            'stage19_operator_commands_authorized': False,
        },
        'warnings': [
            'Review Wiring provenance is synthetic review-only context; canonical planner data remains the planner truth source.',
        ],
        'ui_hints': {
            'severity': 'info',
            'empty_state_key': None,
        },
    },
    9100000000002: {
        'schema_version': 'stage20a_provenance_cockpit/v1',
        'system': {
            'id64': 9100000000002,
            'name': 'Review Aggregate',
            'primary_archetype': 'refinery_extraction',
        },
        'provenance_summary': {
            'state': 'unknown',
            'latest_source_run_key': None,
            'warehouse_state': 'unknown',
            'planner_evidence_state': 'unknown',
        },
        'evidence_panels': {
            'source_run': {
                'state': 'unknown',
                'source_name': 'review_fixture',
                'rows_read': None,
                'rows_staged': None,
                'artifact_name': None,
            },
            'warehouse': {
                'state': 'unknown',
                'report_only': True,
                'canonical_writes_planned': 0,
                'stale_records': 0,
            },
            'planner': {
                'state': 'unknown',
                'observed_facts_count': 0,
                'projected_build_count': 0,
                'manual_review_required': False,
            },
        },
        'guardrails': {
            'stage19_paused': True,
            'stage19_production_activation_complete': False,
            'next_stage19_write_lane_authorized': False,
            'canonical_apply_complete': False,
            'rebaseline_complete': False,
            'scheduler_enabled': False,
            'db_writes_authorized': False,
            'stage19_operator_commands_authorized': False,
        },
        'warnings': [
            'Review Aggregate keeps the provenance fallback in an unknown state for review-only coverage.',
        ],
        'ui_hints': {
            'severity': 'warning',
            'empty_state_key': 'review-aggregate-provenance-unavailable',
        },
    },
    9100000000003: {
        'schema_version': 'stage20a_provenance_cockpit/v1',
        'system': {
            'id64': 9100000000003,
            'name': 'Review Fallback',
            'primary_archetype': 'industrial_hightech',
        },
        'provenance_summary': {
            'state': 'unknown',
            'latest_source_run_key': None,
            'warehouse_state': 'unknown',
            'planner_evidence_state': 'unknown',
        },
        'evidence_panels': {
            'source_run': {
                'state': 'unknown',
                'source_name': 'review_fixture',
                'rows_read': None,
                'rows_staged': None,
                'artifact_name': None,
            },
            'warehouse': {
                'state': 'unknown',
                'report_only': True,
                'canonical_writes_planned': 0,
                'stale_records': 0,
            },
            'planner': {
                'state': 'unknown',
                'observed_facts_count': 0,
                'projected_build_count': 0,
                'manual_review_required': True,
            },
        },
        'guardrails': {
            'stage19_paused': True,
            'stage19_production_activation_complete': False,
            'next_stage19_write_lane_authorized': False,
            'canonical_apply_complete': False,
            'rebaseline_complete': False,
            'scheduler_enabled': False,
            'db_writes_authorized': False,
            'stage19_operator_commands_authorized': False,
        },
        'warnings': [
            'Review Fallback exercises the provenance fallback while planner evidence remains not evaluated.',
        ],
        'ui_hints': {
            'severity': 'neutral',
            'empty_state_key': 'review-fallback-provenance-fallback',
        },
    },
}


REQUIRED_REVIEW_SYSTEM_NAMES = tuple(system['name'] for system in REVIEW_SYSTEMS)
