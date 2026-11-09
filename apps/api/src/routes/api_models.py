from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JsonObject = dict[str, Any]
RouteType = Literal['personal', 'journal', 'spansh', 'expedition']
_COMMANDER_ID_RE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')


def validate_commander_id(value: str) -> str:
    stripped = value.strip()
    if stripped == 'legacy' or not _COMMANDER_ID_RE.fullmatch(stripped):
        raise ValueError('commander_id must be 16-128 chars, alphanumeric + "_" or "-" only.')
    return stripped


class RouteWaypoint(BaseModel):
    model_config = ConfigDict(extra='forbid')

    order: int = Field(ge=0, le=100_000)
    system_id64: int | None = Field(default=None, gt=0)
    system_name: str = Field(min_length=1, max_length=128)
    x: float | None = None
    y: float | None = None
    z: float | None = None
    distance_from_previous: float | None = Field(default=None, ge=0)
    bookmarked: bool = False
    notes: str | None = Field(default=None, max_length=2_000)
    source_event_key: str | None = Field(default=None, exclude=True)

    @field_validator('system_name', 'notes', 'source_event_key')
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value

    @model_validator(mode='after')
    def coordinates_are_complete(self) -> 'RouteWaypoint':
        supplied = (self.x, self.y, self.z)
        if any(value is not None for value in supplied) and not all(value is not None for value in supplied):
            raise ValueError('waypoint coordinates must provide x, y, and z together')
        return self


class RouteEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_id64: int | None = None
    system_name: str
    x: float | None = None
    y: float | None = None
    z: float | None = None
    visited_at: str
    distance_from_planned: float | None = None
    order: int
    context: JsonObject = Field(default_factory=dict)


class RouteAlignment(BaseModel):
    model_config = ConfigDict(extra='forbid')

    planned_order: int
    waypoint: RouteWaypoint
    visited: bool
    actual_event_order: int | None = None
    visited_at: str | None = None
    distance_from_planned: float | None = None


class RouteSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')

    route_id: str
    name: str
    source: str
    type: RouteType
    created_at: str
    updated_at: str
    waypoint_count: int
    visited_count: int
    completion_percent: float
    remaining_distance: float
    current_waypoint_index: int | None = None
    metadata: JsonObject = Field(default_factory=dict)


class RouteDetail(RouteSummary):
    waypoints: list[RouteWaypoint] = Field(default_factory=list)
    events: list[RouteEvent] = Field(default_factory=list)
    planned_actual_alignment: list[RouteAlignment] = Field(default_factory=list)


class RouteListResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    routes: list[RouteSummary] = Field(default_factory=list)
    count: int


class PlannedRouteImport(BaseModel):
    model_config = ConfigDict(extra='forbid')

    commander_id: str
    name: str = Field(min_length=1, max_length=180)
    external_id: str | None = Field(default=None, max_length=255)
    route_mode: Literal['exact', 'neutron', 'carrier'] = 'exact'
    waypoints: list[RouteWaypoint] = Field(min_length=1, max_length=10_000)
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator('commander_id')
    @classmethod
    def validate_scope(cls, value: str) -> str:
        return validate_commander_id(value)

    @field_validator('name', 'external_id')
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value


class ExpeditionImport(BaseModel):
    model_config = ConfigDict(extra='forbid')

    commander_id: str
    name: str = Field(min_length=1, max_length=180)
    external_id: str | None = Field(default=None, max_length=255)
    waypoints: list[RouteWaypoint] = Field(min_length=1, max_length=10_000)
    description: str | None = Field(default=None, max_length=4_000)
    organizer: str | None = Field(default=None, max_length=180)
    departure_at: datetime | None = None
    return_at: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator('commander_id')
    @classmethod
    def validate_scope(cls, value: str) -> str:
        return validate_commander_id(value)

    @field_validator('name', 'external_id', 'description', 'organizer')
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value

    @field_validator('departure_at', 'return_at')
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
