from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JsonObject = dict[str, Any]
_SYNC_KEY_RE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')
_DECIMAL_ID_RE = re.compile(r'^\d+$')
_MAX_BIGINT = 9_223_372_036_854_775_807

_ALLOWED_EVENT_TYPES = {
    'ApproachBody',
    'CarrierJump',
    'CodexEntry',
    'Commander',
    'Died',
    'Disembark',
    'Docked',
    'Embark',
    'Fileheader',
    'FSDJump',
    'FSDTarget',
    'FSSAllBodiesFound',
    'FSSBodySignals',
    'FSSDiscoveryScan',
    'Location',
    'Liftoff',
    'LeaveBody',
    'LoadGame',
    'MultiSellExplorationData',
    'NavRoute',
    'NavRouteClear',
    'Resurrect',
    'SAAScanComplete',
    'SAASignalsFound',
    'Scan',
    'ScanOrganic',
    'Screenshot',
    'SellExplorationData',
    'SellOrganicData',
    'Touchdown',
}


def _strip_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class JournalImportFileRef(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str = Field(min_length=1, max_length=255)
    event_count: int = Field(ge=0, le=1_000_000)

    @field_validator('name')
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()


class JournalImportClientManifest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    parser_version: str = Field(min_length=1, max_length=64)
    files: list[JournalImportFileRef] = Field(default_factory=list, max_length=200)

    @field_validator('parser_version')
    @classmethod
    def strip_parser_version(cls, value: str) -> str:
        return value.strip()


class JournalObservationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    observation_key: str = Field(min_length=16, max_length=128)
    source_file: str = Field(min_length=1, max_length=255)
    source_offset: int = Field(default=0, ge=0, le=_MAX_BIGINT)
    event_type: str = Field(min_length=1, max_length=64)
    observed_at: datetime | None = None
    system_id64: str | None = Field(default=None, min_length=1, max_length=19)
    system_name: str | None = Field(default=None, max_length=128)
    subject_type: Literal['system', 'body', 'route']
    subject_id: str | None = Field(default=None, max_length=128)
    summary: str | None = Field(default=None, max_length=300)
    payload: JsonObject = Field(default_factory=dict)
    privacy_boundary: JsonObject = Field(default_factory=dict)

    @field_validator('observation_key', 'source_file', 'system_name', 'subject_id', 'summary')
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return _strip_text(value)

    @field_validator('event_type')
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        stripped = value.strip()
        if stripped not in _ALLOWED_EVENT_TYPES:
            raise ValueError(f'event_type must be one of {sorted(_ALLOWED_EVENT_TYPES)}')
        return stripped

    @field_validator('system_id64', mode='before')
    @classmethod
    def validate_system_id64(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError('system_id64 must be a positive decimal string')
        text = str(value).strip()
        if not _DECIMAL_ID_RE.fullmatch(text) or int(text) <= 0 or int(text) > _MAX_BIGINT:
            raise ValueError('system_id64 must be a positive signed-64-bit decimal string')
        return text

    @field_validator('observed_at', mode='before')
    @classmethod
    def validate_observed_at(cls, value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            value = datetime.fromisoformat(stripped.replace('Z', '+00:00'))
        if not isinstance(value, datetime):
            raise ValueError('observed_at must be an ISO-8601 timestamp')
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_validator('payload', 'privacy_boundary')
    @classmethod
    def validate_json_object(cls, value: JsonObject) -> JsonObject:
        if not isinstance(value, dict):
            raise ValueError('payload and privacy_boundary must be objects')
        return value

    @model_validator(mode='after')
    def route_events_use_the_route_subject(self) -> 'JournalObservationInput':
        if self.event_type in {'NavRoute', 'NavRouteClear'} and self.subject_type != 'route':
            raise ValueError('NavRoute events must use subject_type="route"')
        return self


class JournalImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    sync_key: str = Field(min_length=16, max_length=128)
    client_manifest: JournalImportClientManifest
    evidence_mode: Literal['staging_only'] = 'staging_only'
    observations: list[JournalObservationInput] = Field(default_factory=list, max_length=50_000)

    @field_validator('sync_key')
    @classmethod
    def validate_sync_key(cls, value: str) -> str:
        stripped = value.strip()
        if stripped == 'legacy':
            raise ValueError('sync_key="legacy" is reserved for migration')
        if not _SYNC_KEY_RE.match(stripped):
            raise ValueError('sync_key must be 16-128 chars, alphanumeric + "_" or "-" only.')
        return stripped


class JournalImportSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')

    observations_received: int
    observations_staged: int
    duplicates_skipped: int
    conflicts_flagged: int = 0
    files_seen: int
    event_counts: dict[str, int] = Field(default_factory=dict)


class JournalImportReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid')

    run_key: str
    status: str
    parser_version: str
    started_at: str | None = None
    finished_at: str | None = None
    files: list[JournalImportFileRef] = Field(default_factory=list)
    summary: JournalImportSummary


class JournalPromotionSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')

    staged_rows_seen: int
    eligible_rows: int
    skipped_rows: int
    facts_promoted: int
    ring_rows_promoted: int
    ring_rows_unresolved: int
    dirty_systems_marked: int
    canonical_evidence_promoted: int = 0
    canonical_evidence_deduped: int = 0
    event_counts: dict[str, int] = Field(default_factory=dict)
    resolution_counts: dict[str, int] = Field(default_factory=dict)


class JournalPromotionReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid')

    run_key: str
    status: str
    promoted_at: str | None = None
    duration_ms: int = 0
    summary: JournalPromotionSummary


class JournalTelemetryRecentRun(BaseModel):
    model_config = ConfigDict(extra='forbid')

    run_key: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    observations_staged: int = 0
    duplicates_skipped: int = 0
    event_counts: dict[str, int] = Field(default_factory=dict)


class JournalTelemetryRecentSystem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_id64: str = Field(min_length=1, max_length=19)
    system_name: str
    last_observed_at: str | None = None
    event_count: int = 0
    event_types: list[str] = Field(default_factory=list)

    @field_validator('system_id64', mode='before')
    @classmethod
    def stringify_system_id64(cls, value: object) -> str:
        return str(value)


class JournalTelemetrySummaryResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    sync_key: str = Field(min_length=16, max_length=128)
    runs_count: int = 0
    last_imported_at: str | None = None
    observations_staged: int = 0
    duplicates_skipped: int = 0
    systems_observed: int = 0
    body_observation_count: int = 0
    docked_observation_count: int = 0
    event_counts: dict[str, int] = Field(default_factory=dict)
    recent_runs: list[JournalTelemetryRecentRun] = Field(default_factory=list)
    recent_systems: list[JournalTelemetryRecentSystem] = Field(default_factory=list)
