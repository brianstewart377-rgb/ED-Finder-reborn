from __future__ import annotations

import asyncpg

from edfinder_api.provenance_cockpit_models import ProvenanceCockpitResponse
from edfinder_api.review_environment_fixtures import (
    REVIEW_PROVENANCE_CONTRACTS,
    REVIEW_WAREHOUSE_CONTRACTS,
)
from edfinder_api.warehouse_planner_evidence_models import WarehousePlannerEvidenceContract


async def load_review_warehouse_contract(
    pool: asyncpg.Pool,
    id64: int,
) -> WarehousePlannerEvidenceContract | None:
    # Review-only control payloads need no legacy public.app_meta table or seed.
    payload = REVIEW_WAREHOUSE_CONTRACTS.get(id64)
    if payload is None:
        return None
    return WarehousePlannerEvidenceContract.model_validate(payload)


async def load_review_provenance_contract(
    pool: asyncpg.Pool,
    id64: int,
) -> ProvenanceCockpitResponse | None:
    payload = REVIEW_PROVENANCE_CONTRACTS.get(id64)
    if payload is None:
        return None
    return ProvenanceCockpitResponse.model_validate(payload)
