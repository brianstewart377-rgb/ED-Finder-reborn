from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


LayoutImportSource = Literal['spansh']
LayoutImportStatus = Literal['success', 'partial', 'failed']


class LayoutImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    source: LayoutImportSource = 'spansh'


class LayoutImportSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')

    bodies_found: int = Field(ge=0)
    stations_found: int = Field(ge=0)
    bodies_upserted: int = Field(ge=0)
    stations_upserted: int = Field(ge=0)
    warnings_count: int = Field(ge=0)


class LayoutImportResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_id64: int
    source: LayoutImportSource
    status: LayoutImportStatus
    fetched_at: datetime
    summary: LayoutImportSummary
    warnings: list[str]
    errors: list[str]
