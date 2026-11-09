from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends

from edfinder_api.deps import get_pool
from edfinder_api.provenance_cockpit import build_provenance_cockpit
from edfinder_api.provenance_cockpit_models import ProvenanceCockpitResponse
from edfinder_api.review_contract_store import load_review_provenance_contract


router = APIRouter(tags=['colony-planner'])


@router.get(
    '/api/colony-planner/system/{id64}/provenance-cockpit',
    response_model=ProvenanceCockpitResponse,
    include_in_schema=False,
)
async def provenance_cockpit(
    id64: int,
    pool: asyncpg.Pool = Depends(get_pool),
) -> ProvenanceCockpitResponse:
    review_contract = await load_review_provenance_contract(pool, id64)
    if review_contract is not None:
        return review_contract
    return build_provenance_cockpit(id64)
