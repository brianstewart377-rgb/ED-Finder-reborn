"""Lightweight Simulation Preview summary extraction for Stage 5A candidates."""
from __future__ import annotations

from typing import Any

from edfinder_api.optimiser.models import CandidatePreviewSummary


def preview_summary_from_response(response: dict[str, Any]) -> CandidatePreviewSummary:
    cp = response.get('cp') or {}
    yellow_cp = cp.get('yellow_cp_final')
    green_cp = cp.get('green_cp_final')
    cp_negative = None
    if yellow_cp is not None or green_cp is not None:
        cp_negative = (yellow_cp or 0) < 0 or (green_cp or 0) < 0

    return CandidatePreviewSummary(
        final_score=response.get('final_score'),
        composition_score=response.get('composition_score'),
        buildability_score=response.get('buildability_score'),
        confidence=response.get('confidence'),
        build_complexity=response.get('build_complexity'),
        warnings_count=len(response.get('warnings') or []),
        cp_negative=cp_negative,
        top_two_alignment=response.get('top_two_alignment'),
    )
