from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JsonObject = dict[str, Any]
_SYNC_KEY_RE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')
_DECIMAL_ID_RE = re.compile(r'^\d+$')
_MAX_BIGINT = 9_223_372_036_854_775_807
MAX_PAYLOAD_BYTES = 32_768
EDSM_VISIT_EVENT_TYPES = {'FSDJump', 'Location'}

ALLOWED_EXPLORATION_EVENT_TYPES = {
    'CarrierJump',
    'FSDJump',
    'Location',
    'Scan',
    'FSSDiscoveryScan',
    'FSSAllBodiesFound',
    'SAASignalsFound',
    'FSSBodySignals',
    'CodexEntry',
    'SAAScanComplete',
    'ScanOrganic',
    'SellOrganicData',
    'SellExplorationData',
    'MultiSellExplorationData',
    'RedeemVoucher',
}


def _strip_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _reject_nul_byte(value: str) -> None:
    # PostgreSQL TEXT columns cannot store a NUL (0x00) byte, but it is a
    # perfectly valid JSON string character - without this check, a NUL
    # anywhere in these fields reaches the INSERT and PostgreSQL rejects it,
    # turning one malformed observation into a 500 that rolls back the whole
    # batch instead of a clean 422 for the one bad row.
    if '\x00' in value:
        raise ValueError('value must not contain a NUL character')


def _contains_nul_byte(value: object) -> bool:
    # payload is arbitrary client-supplied JSON; a NUL nested anywhere inside
    # it (a string value, a list item, or a dict key) hits the same PostgreSQL
    # jsonb rejection as a NUL in a plain TEXT field - json.dumps escapes it
    # as a valid JSON unicode escape sequence, but Postgres's jsonb parser then rejects
    # that escape ("unsupported Unicode escape sequence") when the value is
    # bound and cast, so it must be caught here instead.
    if isinstance(value, str):
        return '\x00' in value
    if isinstance(value, dict):
        return any(_contains_nul_byte(key) or _contains_nul_byte(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_nul_byte(item) for item in value)
    return False


class ExplorationObservationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    observation_key: str = Field(min_length=16, max_length=128)
    event_type: str = Field(min_length=1, max_length=64)
    observed_at: datetime
    system_id64: str = Field(min_length=1, max_length=19)
    system_name: str | None = Field(default=None, max_length=128)
    body_id: str | None = Field(default=None, min_length=1, max_length=10)
    body_name: str | None = Field(default=None, max_length=128)
    payload: JsonObject = Field(default_factory=dict)

    @field_validator('system_name', 'body_name')
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is not None:
            _reject_nul_byte(value)
        return _strip_text(value)

    @field_validator('system_id64', mode='before')
    @classmethod
    def validate_system_id64(cls, value: object) -> str:
        if isinstance(value, bool):
            raise ValueError('system_id64 must be a positive decimal string')
        text = str(value).strip()
        if not _DECIMAL_ID_RE.fullmatch(text) or int(text) <= 0 or int(text) > _MAX_BIGINT:
            raise ValueError('system_id64 must be a positive signed-64-bit decimal string')
        return text

    @field_validator('body_id', mode='before')
    @classmethod
    def validate_body_id(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError('body_id must be a non-negative decimal string')
        text = str(value).strip()
        if not _DECIMAL_ID_RE.fullmatch(text) or int(text) > 2_147_483_647:
            raise ValueError('body_id must be a non-negative 32-bit decimal string')
        return text

    @field_validator('observation_key')
    @classmethod
    def reject_blank_observation_key(cls, value: str) -> str:
        # observation_key is a client-computed dedupe hash, not free-text, so it is
        # validated as-is (unstripped) against Field(min_length=16, max_length=128).
        # Field alone only checks length, not content, so a whitespace-only value of
        # valid length would otherwise pass; reject it explicitly without normalizing
        # the stored value (no stripping/truncation here).
        _reject_nul_byte(value)
        if not value.strip():
            raise ValueError('observation_key must not be blank or whitespace-only')
        return value

    @field_validator('event_type')
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        stripped = value.strip()
        if stripped not in ALLOWED_EXPLORATION_EVENT_TYPES:
            raise ValueError(f'event_type must be one of {sorted(ALLOWED_EXPLORATION_EVENT_TYPES)}')
        return stripped

    @field_validator('observed_at', mode='before')
    @classmethod
    def validate_observed_at(cls, value: object) -> datetime:
        if isinstance(value, str):
            stripped = value.strip()
            value = datetime.fromisoformat(stripped.replace('Z', '+00:00'))
        if not isinstance(value, datetime):
            raise ValueError('observed_at must be an ISO-8601 timestamp')
        try:
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        except OverflowError as exc:
            raise ValueError(f'observed_at is outside the representable timestamp range: {exc}') from exc

    @field_validator('payload')
    @classmethod
    def validate_payload(cls, value: JsonObject) -> JsonObject:
        if not isinstance(value, dict):
            raise ValueError('payload must be an object')
        try:
            serialized_size = len(json.dumps(value, allow_nan=False))
        except ValueError as exc:
            raise ValueError(f'payload contains a value PostgreSQL JSON cannot store: {exc}') from exc
        if serialized_size > MAX_PAYLOAD_BYTES:
            raise ValueError(f'payload exceeds the {MAX_PAYLOAD_BYTES}-byte limit ({serialized_size} bytes)')
        if _contains_nul_byte(value):
            raise ValueError('payload must not contain a NUL character')
        return value


class ExplorationImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    sync_key: str = Field(min_length=16, max_length=128)
    source: Literal['journal', 'edsm'] = 'journal'
    observations: list[ExplorationObservationInput] = Field(default_factory=list, max_length=20_000)

    @field_validator('sync_key')
    @classmethod
    def validate_sync_key(cls, value: str) -> str:
        stripped = value.strip()
        if stripped == 'legacy':
            raise ValueError('sync_key="legacy" is reserved for migration')
        if not _SYNC_KEY_RE.match(stripped):
            raise ValueError('sync_key must be 16-128 chars, alphanumeric + "_" or "-" only.')
        return stripped

    @model_validator(mode='after')
    def validate_edsm_batches_are_visit_only(self) -> ExplorationImportRequest:
        # The design doc's source contract: EDSM only ever supplies timestamped
        # system visits, never scan/mapping/exobiology/Codex detail. Without this
        # check, a mislabeled or malformed batch could fabricate those facets
        # under source="edsm" even though EDSM structurally cannot provide them.
        if self.source == 'edsm':
            invalid_types = {
                observation.event_type
                for observation in self.observations
                if observation.event_type not in EDSM_VISIT_EVENT_TYPES
            }
            if invalid_types:
                raise ValueError(
                    f'source="edsm" batches may only contain visit events '
                    f'{sorted(EDSM_VISIT_EVENT_TYPES)}, got: {sorted(invalid_types)}'
                )
        return self


class ExplorationImportSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')

    observations_received: int
    observations_staged: int
    duplicates_skipped: int
    event_counts: dict[str, int] = Field(default_factory=dict)
    projections_rebuilt: dict[str, int] = Field(default_factory=dict)


class ExplorationImportReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid')

    sync_key: str
    status: str
    summary: ExplorationImportSummary


class ExplorationFactRow(BaseModel):
    model_config = ConfigDict(extra='forbid')

    fact_id: int
    event_type: str
    system_id64: int
    system_name: str | None = None
    body_id: int | None = None
    body_name: str | None = None
    observed_at: str
    payload: JsonObject = Field(default_factory=dict)
    source: str


class ExplorationFactsResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    sync_key: str
    facts: list[ExplorationFactRow] = Field(default_factory=list)
    count: int
    truncated: bool
    next_cursor: str | None = None
    total_count: int
    event_counts: dict[str, int] = Field(default_factory=dict)


class ExplorationTrailPoint(BaseModel):
    model_config = ConfigDict(extra='forbid')

    sequence: int
    fact_id: int
    system_id64: int
    system_name: str | None = None
    visited_at: str
    x: float | None = None
    y: float | None = None
    z: float | None = None
    galaxy_region_id: int | None = None
    from_system_id64: int | None = None
    distance_ly: float | None = None


class ExplorationTrailResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    sync_key: str
    points: list[ExplorationTrailPoint] = Field(default_factory=list)
    count: int
    truncated: bool
    next_cursor: int | None = None


class ExplorationViewportVisit(BaseModel):
    model_config = ConfigDict(extra='forbid')

    kind: Literal['marker', 'density']
    system_id64: int | None = None
    system_name: str | None = None
    x: float
    y: float
    z: float
    galaxy_region_id: int | None = None
    visit_count: int
    first_visited_at: str
    last_visited_at: str
    completion_state: Literal['complete', 'partial']
    cell_size: float | None = None


class ExplorationViewportVisitsResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    sync_key: str
    mode: Literal['markers', 'density']
    visits: list[ExplorationViewportVisit] = Field(default_factory=list)
    count: int
    truncated: bool
    cell_size: float | None = None


class ExplorationVisitSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')

    visit_count: int = 0
    first_visited_at: str | None = None
    last_visited_at: str | None = None


class ExplorationBodySummary(BaseModel):
    model_config = ConfigDict(extra='forbid')

    expected: int | None = None
    observed: int = 0
    scanned: int = 0
    mapped: int = 0
    fss_complete: bool = False
    dss_complete: bool = False
    map_progress: float = 0


class ExplorationOrganicSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')

    organisms: int = 0
    logged: int = 0
    sampled: int = 0
    analysed: int = 0
    sold: int = 0
    sale_value: int = 0


class ExplorationCodexSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')

    observed: int = 0
    pending: int = 0
    sold: int = 0


class ExplorationSystemSummaryResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    sync_key: str
    system_id64: int
    system_name: str | None = None
    galaxy_region_id: int | None = None
    visits: ExplorationVisitSummary
    bodies: ExplorationBodySummary
    organics: ExplorationOrganicSummary
    codex: ExplorationCodexSummary


class ExplorationCodexRegion(BaseModel):
    model_config = ConfigDict(extra='forbid')

    region: str
    region_id: int | None = None
    global_entries: int
    personal_entries: int
    sold_entries: int
    completion_percent: float | None = None
    categories: dict[str, int] = Field(default_factory=dict)


class ExplorationCodexByRegionResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    sync_key: str
    regions: list[ExplorationCodexRegion] = Field(default_factory=list)
    global_entries: int
    personal_entries: int
    completion_percent: float | None = None
