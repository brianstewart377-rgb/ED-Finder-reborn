"""Placement fingerprinting and candidate deduplication for Stage 5A.

The fingerprint is intentionally order-sensitive. Two candidates using the same
facilities on the same bodies but in a different build order are distinct
because build order affects CP timing and repair suggestions in Simulation
Preview.
"""
from __future__ import annotations

from edfinder_api.optimiser.models import CandidatePlacement, OptimiserCandidate


def placement_fingerprint(placements: list[CandidatePlacement]) -> tuple[tuple[str, str | None, bool, int], ...]:
    """Return a deterministic ordered fingerprint for a complete placement plan."""
    return tuple(
        (placement.facility_template_id, placement.local_body_id, placement.is_primary_port, placement.build_order)
        for placement in sorted(placements, key=lambda p: (p.build_order, p.facility_template_id, p.local_body_id or ''))
    )


def dedupe_candidates(candidates: list[OptimiserCandidate]) -> list[OptimiserCandidate]:
    seen: set[tuple[tuple[str, str | None, bool, int], ...]] = set()
    result: list[OptimiserCandidate] = []
    for candidate in candidates:
        fingerprint = placement_fingerprint(candidate.placements)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(candidate)
    return result
