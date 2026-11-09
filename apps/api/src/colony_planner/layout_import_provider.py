from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from .layout_import_models import LayoutImportResponse, LayoutImportSource, LayoutImportSummary


class LayoutImportProvider(Protocol):
    async def import_layout(self, system_id64: int, source: LayoutImportSource) -> LayoutImportResponse:
        """Fetch or refresh layout data for a system without touching planner state."""


class ConfiguredLayoutImportProvider:
    """Stage 10E.1 provider stub.

    TODO Stage 10E.2: wire this to a bounded-timeout Spansh fetcher and DB
    upsert path. The route contract is intentionally live in 10E.1, but this
    provider does not fetch remote data or overwrite Build Plan placements.
    """

    async def import_layout(self, system_id64: int, source: LayoutImportSource) -> LayoutImportResponse:
        warning = 'Live layout-source import is not wired yet; no local layout rows were changed.'
        return LayoutImportResponse(
            system_id64=system_id64,
            source=source,
            status='partial',
            fetched_at=datetime.now(UTC),
            summary=LayoutImportSummary(
                bodies_found=0,
                stations_found=0,
                bodies_upserted=0,
                stations_upserted=0,
                warnings_count=1,
            ),
            warnings=[warning],
            errors=[],
        )


def get_layout_import_provider() -> LayoutImportProvider:
    return ConfiguredLayoutImportProvider()
