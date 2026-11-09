from __future__ import annotations

from fastapi import APIRouter

from edfinder_api.provenance_cockpit import build_provenance_cockpit
from edfinder_api.provenance_cockpit_models import ProvenanceCockpitResponse


router = APIRouter(tags=['colony-planner'])


@router.get(
    '/api/colony-planner/system/{id64}/provenance-cockpit',
    response_model=ProvenanceCockpitResponse,
    include_in_schema=False,
)
async def provenance_cockpit(id64: int) -> ProvenanceCockpitResponse:
    return build_provenance_cockpit(id64)
