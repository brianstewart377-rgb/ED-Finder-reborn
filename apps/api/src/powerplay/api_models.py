from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .parser import SUPPORTED_EVENTS

_COMMANDER_KEY_RE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')
MAX_SOURCE_PAYLOAD_BYTES = 32_768


def validate_commander_key(value: str) -> str:
    stripped = value.strip()
    if stripped == 'legacy' or not _COMMANDER_KEY_RE.match(stripped):
        raise ValueError('commander_key must be 16-128 chars, alphanumeric + "_" or "-" only')
    return stripped


def _as_utc(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    if not isinstance(value, datetime):
        raise ValueError('observed_at must be an ISO-8601 timestamp')
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class PowerplayJournalEventInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    observation_key: str = Field(min_length=16, max_length=128)
    event_type: str = Field(min_length=1, max_length=64)
    observed_at: datetime
    game_build: str | None = Field(default=None, max_length=64)
    source_payload: dict[str, Any]

    @field_validator('event_type')
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        value = value.strip()
        if value not in SUPPORTED_EVENTS:
            raise ValueError(f'event_type must be one of {sorted(SUPPORTED_EVENTS)}')
        return value

    @field_validator('observed_at', mode='before')
    @classmethod
    def validate_observed_at(cls, value: object) -> datetime:
        return _as_utc(value)

    @field_validator('source_payload')
    @classmethod
    def validate_source_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'source_payload is not PostgreSQL-compatible JSON: {exc}') from exc
        if len(encoded.encode('utf-8')) > MAX_SOURCE_PAYLOAD_BYTES:
            raise ValueError(f'source_payload exceeds {MAX_SOURCE_PAYLOAD_BYTES} bytes')
        if '\x00' in encoded:
            raise ValueError('source_payload must not contain NUL characters')
        return value

    @model_validator(mode='after')
    def event_matches_payload(self) -> PowerplayJournalEventInput:
        if self.source_payload.get('event') != self.event_type:
            raise ValueError('event_type must match source_payload.event')
        return self


class PowerplayImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    commander_key: str = Field(min_length=16, max_length=128)
    source: Literal['journal'] = 'journal'
    source_version: str = Field(min_length=1, max_length=64)
    events: list[PowerplayJournalEventInput] = Field(default_factory=list, max_length=50_000)

    @field_validator('commander_key')
    @classmethod
    def validate_key(cls, value: str) -> str:
        return validate_commander_key(value)


class PowerplayImportReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid')

    commander_key: str
    events_received: int
    system_observations_staged: int
    commander_events_staged: int
    duplicates_skipped: int
    cycles_versioned: int


class PowerplayValueEvidence(BaseModel):
    model_config = ConfigDict(extra='allow')

    source: str
    version: str
    confidence: float
    observed_at: str


class PowerplaySystemState(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_address: int
    system_name: str | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    controlling_power: Any = None
    control_state: Any = None
    control_progress: Any = None
    reinforcement_points: Any = None
    undermining_points: Any = None
    powers: list[Any] = Field(default_factory=list)
    observed_at: str
    cycle_start: str
    game_build: str | None = None
    source_payload: dict[str, Any]
    observation_age_seconds: int
    uncertainty: Literal['low', 'medium', 'high']
    uncertainty_reasons: list[str] = Field(default_factory=list)
    value_provenance: dict[str, PowerplayValueEvidence] = Field(default_factory=dict)


class PowerplaySystemsResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    commander_key: str
    systems: list[PowerplaySystemState]
    count: int
    truncated: bool
    snapshot_version: str = 'powerplay-systems/v1'


class PowerplayContribution(BaseModel):
    model_config = ConfigDict(extra='forbid')

    observed_at: str
    power: Any = None
    merits_gained: Any = None
    total_merits: Any = None
    source: str
    version: str
    confidence: float


class CommanderPowerplayResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    commander_key: str
    pledge: Any = None
    rank: Any = None
    merits: Any = None
    last_updated: str | None = None
    cycle_start: str
    cycle_merits_earned: Any = 0
    value_provenance: dict[str, PowerplayValueEvidence] = Field(default_factory=dict)
    recent_contributions: list[PowerplayContribution] = Field(default_factory=list)
    snapshot_version: str = 'commander-powerplay/v1'


class PowerplayChangeEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_address: int
    system_name: str | None = None
    observed_at: str
    cycle_start: str
    changes: dict[str, dict[str, Any]]
    source: str
    version: str
    confidence: float


class PowerplayCycleSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')

    week: str
    cycle_start: str
    captured_at: str
    control_snapshot: dict[str, Any]
    snapshot_hash: str
    source: str
    version: str
    confidence: float


class PowerplayHistoryResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    commander_key: str
    cycles: list[PowerplayCycleSnapshot]
    change_events: list[PowerplayChangeEvent]
    snapshot_version: str = 'powerplay-history/v1'
