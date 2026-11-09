from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from edfinder_api.colony_planner.layout_import_models import (
    LayoutImportRequest,
    LayoutImportResponse,
    LayoutImportSummary,
)
from edfinder_api.colony_planner.layout_import_provider import (
    LayoutImportProvider,
    get_layout_import_provider,
)
from edfinder_api.config import log


router = APIRouter(tags=['colony-planner'])


@router.post(
    '/api/colony-planner/system/{id64}/import-layout',
    response_model=LayoutImportResponse,
)
async def import_system_layout(
    id64: int,
    body: LayoutImportRequest | None = None,
    provider: LayoutImportProvider = Depends(get_layout_import_provider),
) -> LayoutImportResponse:
    request = body or LayoutImportRequest()
    log.info(
        'colony_layout_import_attempt',
        extra={'system_id64': id64, 'source': request.source},
    )
    try:
        result = await provider.import_layout(id64, request.source)
    except Exception as exc:
        log.warning(
            'colony_layout_import_failed',
            extra={'system_id64': id64, 'source': request.source, 'error': str(exc)},
        )
        return LayoutImportResponse(
            system_id64=id64,
            source=request.source,
            status='failed',
            fetched_at=datetime.now(UTC),
            summary=LayoutImportSummary(
                bodies_found=0,
                stations_found=0,
                bodies_upserted=0,
                stations_upserted=0,
                warnings_count=0,
            ),
            warnings=[],
            errors=[str(exc) or 'Layout import provider failed.'],
        )

    log.info(
        'colony_layout_import_outcome',
        extra={
            'system_id64': id64,
            'source': result.source,
            'status': result.status,
            'bodies_found': result.summary.bodies_found,
            'stations_found': result.summary.stations_found,
            'warnings_count': result.summary.warnings_count,
        },
    )
    return result
