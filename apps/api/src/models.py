"""Pydantic request + response models.

These are data-shape contracts — keep side-effects out, do not import
DB / Redis / config from here. All models referenced by more than one
router live here; router-local one-off shapes can stay next to their
endpoint.

Audit Phase 2 follow-up (2026-05-09 — see AUDIT_REPORT.md §M3 / Phase 7
follow-up): every response model below is now strictly typed *and*
allows extras (`model_config = {"extra": "allow"}`).

Why both?

* Strict fields → openapi-typescript can generate real TypeScript types
  from /openapi.json instead of `Record<string, never>` / `unknown`,
  which is what made the api.gen.ts CI drift-check toothless.
* Extras allowed → adding a column on the SQL side or a key in
  `helpers.sys_row_to_dict` will *not* be silently stripped by
  Pydantic's response serializer (which was the original bug behind
  the `source/query_ms/display_economy` regression in d43fde8).

If you tighten extras here, audit `helpers.sys_row_to_dict` and the
SQL projections in `routers/systems.py` + `local_search.py` first.

Strictness policy (2026-05-09 follow-up to the search-503 RCA):

* **Row models** (`SystemRow`, `SystemDetailRow`, `BodyModel`,
  `StationModel`, `AutocompleteHit`,
  `RangeFilter`, `BodyCountFilter`, `BodyFilters`,
  `ExplorationValueModel`) → `extra='allow'`. SQL projections
  legitimately drift; silently dropping an existing field would be a
  worse bug than passing through an unknown one.
* **Response envelopes** (`SearchResponse`, `AutocompleteResponse`,
  `StatusResponse`, `CacheStatsResponse`, `SystemDetailResponse`,
  `HealthResponse`) → `extra='forbid'`. The
  top-level envelope shape changes rarely and intentionally;
  forgetting to update the model when adding a key should be a loud
  failure (Pydantic ValidationError → 500), not silently shipped
  back to the client where typed consumers would 'unknown'-out the
  field. The drift-check CI job already protects the wire contract,
  so model_config drift can't go un-noticed.

Note on dict-typed request fields (2026-05-09 follow-up): every
request-side `Optional[dict]` has been replaced with a proper sub-model
(RangeFilter, BodyFilters, etc.) or `Optional[Any]`
where the shape is genuinely opaque. Pydantic 2.10+ emits
`additionalProperties: false` for `dict` fields, which openapi-typescript
renders as the strict-empty `Record<string, never>` — strict-mode TS
then refuses to pass real values through that type. Using sub-models
keeps the schema portable across Pydantic versions and gives the
frontend useful types out of the box.
"""
from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from edfinder_api.models_economy import EconomyFilterField, EconomyNameField
from edfinder_api.ranking.profile import (
    RANKING_OFFSET_PLUS_LIMIT_MAX,
    RANKING_PAGE_LIMIT_MAX,
    RANKING_PAGE_LIMIT_MIN,
)



# ══════════════════════════════════════════════════════════════════════
# Shared mixins / sub-models
# ══════════════════════════════════════════════════════════════════════
class CoordsModel(BaseModel):
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None


class RangeFilter(BaseModel):
    """Inclusive numeric range used by `SearchFilters.distance` /
    `.population`. Either bound is optional — omit `min` for an upper-
    only filter, omit `max` for a lower-only one. (Frontend always
    sends both today; keep them optional so a future client can omit.)"""
    model_config = ConfigDict(extra='allow')

    min: Optional[float] = None
    max: Optional[float] = None


class BodyCountFilter(BaseModel):
    """One body-count slider in `LocalSearchRequest.body_filters`. Each
    body-type key (`elw_count`, `ww_count`, …) maps to a min/max range,
    same shape as `RangeFilter` — duplicated here for naming clarity in
    the generated TS."""
    model_config = ConfigDict(extra='allow')

    min: Optional[int] = None
    max: Optional[int] = None


class BodyFilters(BaseModel):
    """`LocalSearchRequest.body_filters`. Keys mirror the
    `helpers.sys_row_to_dict` body-count column names. All optional —
    a missing key means "no filter on that body type"."""
    model_config = ConfigDict(extra='allow')

    elw_count:           Optional[BodyCountFilter] = None
    ww_count:            Optional[BodyCountFilter] = None
    ammonia_count:       Optional[BodyCountFilter] = None
    gas_giant_count:     Optional[BodyCountFilter] = None
    landable_count:      Optional[BodyCountFilter] = None
    terraformable_count: Optional[BodyCountFilter] = None
    bio_signal_total:    Optional[BodyCountFilter] = None
    geo_signal_total:    Optional[BodyCountFilter] = None
    neutron_count:       Optional[BodyCountFilter] = None
    black_hole_count:    Optional[BodyCountFilter] = None
    white_dwarf_count:   Optional[BodyCountFilter] = None
    hmc_count:           Optional[BodyCountFilter] = None
    metal_rich_count:    Optional[BodyCountFilter] = None
    rocky_count:         Optional[BodyCountFilter] = None
    rocky_ice_count:     Optional[BodyCountFilter] = None
    icy_count:           Optional[BodyCountFilter] = None
    other_star_count:    Optional[BodyCountFilter] = None
    ring_count:          Optional[BodyCountFilter] = None
    walkable_count:      Optional[BodyCountFilter] = None


class BodyRingModel(BaseModel):
    model_config = ConfigDict(extra='allow')

    ring_name:    Optional[str]   = None
    ring_type:    Optional[str]   = None
    ring_class:   Optional[str]   = None
    mass_mt:      Optional[float] = None
    inner_radius: Optional[float] = None
    outer_radius: Optional[float] = None
    source:       Optional[str]   = None
    confidence:   Optional[str]   = None
    updated_at:   Optional[Any]   = None


class BodyModel(BaseModel):
    model_config = ConfigDict(extra='allow')

    id:                      Optional[int]   = None
    name:                    Optional[str]   = None
    subtype:                 Optional[str]   = None
    body_type:               Optional[str]   = None
    distance_from_star:      Optional[float] = None
    is_landable:             Optional[bool]  = None
    is_terraformable:        Optional[bool]  = None
    is_earth_like:           Optional[bool]  = None
    is_water_world:          Optional[bool]  = None
    is_ammonia_world:        Optional[bool]  = None
    bio_signal_count:        Optional[int]   = None
    geo_signal_count:        Optional[int]   = None
    surface_temp:            Optional[float] = None
    radius:                  Optional[float] = None
    mass:                    Optional[float] = None
    gravity:                 Optional[float] = None
    estimated_mapping_value: Optional[int]   = None
    estimated_scan_value:    Optional[int]   = None
    is_main_star:            Optional[bool]  = None
    spectral_class:          Optional[str]   = None
    is_scoopable:            Optional[bool]  = None
    is_ringed:               Optional[bool]  = None
    ring_state:              Optional[Literal['ringed', 'not_ringed', 'unknown']] = None
    rings:                   Optional[list[BodyRingModel]] = None
    ring_count:              Optional[int]   = None
    ring_source:             Optional[str]   = None
    ring_confidence:         Optional[str]   = None
    body_sort_key:           Optional[str]   = None


class StationModel(BaseModel):
    model_config = ConfigDict(extra='allow')

    id:                 Optional[int]   = None
    market_id:          Optional[int]   = None
    name:               Optional[str]   = None
    station_type:       Optional[str]   = None
    distance_from_star: Optional[float] = None
    distance_source:    Optional[str]   = None
    distance_confidence: Optional[str]  = None
    distance_updated_at: Optional[Any]  = None
    station_type_source: Optional[str]  = None
    station_type_confidence: Optional[str] = None
    station_type_updated_at: Optional[Any] = None
    body_id:            Optional[int]   = None
    body_name:          Optional[str]   = None
    station_body_name:  Optional[str]   = None
    body_name_source:   Optional[str]   = None
    body_name_confidence: Optional[str] = None
    body_name_updated_at: Optional[Any] = None
    lane:               Optional[str]   = None
    association_status: Optional[str]   = None
    association_confidence: Optional[str] = None
    association_source: Optional[str]   = None
    resolver_notes:     Optional[str]   = None
    landing_pad_size:   Optional[str]   = None
    primary_economy:    Optional[str]   = None
    secondary_economy:  Optional[str]   = None
    has_market:         Optional[bool]  = None
    has_shipyard:       Optional[bool]  = None
    has_outfitting:     Optional[bool]  = None
    has_refuel:         Optional[bool]  = None
    has_repair:         Optional[bool]  = None
    has_rearm:          Optional[bool]  = None


class ExplorationValueModel(BaseModel):
    model_config = ConfigDict(extra='allow')

    total_scan_value:    int = 0
    total_mapping_value: int = 0
    combined_value:      int = 0


# ══════════════════════════════════════════════════════════════════════
# Search row (camelCase) — `/api/local/search`, `/api/systems/batch`
# ══════════════════════════════════════════════════════════════════════
class SystemRow(BaseModel):
    """One result row inside `SearchResponse.results`.

    Keep aligned with `helpers.sys_row_to_dict` (single source of truth
    for the camelCase translator).
    """
    model_config = ConfigDict(extra='allow')

    id64:                int
    name:                str                   = 'Unknown'
    coords:              Optional[CoordsModel] = None
    distance:            Optional[float]       = None
    population:          Optional[int]         = None
    primaryEconomy:      Optional[str]         = None
    secondaryEconomy:    Optional[str]         = None
    security:            Optional[str]         = None
    allegiance:          Optional[str]         = None
    government:          Optional[str]         = None
    is_colonised:        Optional[bool]        = None
    is_being_colonised:  Optional[bool]        = None
    main_star_type:      Optional[str]         = None
    main_star_subtype:   Optional[str]         = None
    archetype_score:     Optional[float]       = None
    archetype_tier:      Optional[TierValue]   = None
    primary_archetype:   Optional[str]         = None
    secondary_archetype: Optional[str]         = None
    archetype_confidence: Optional[float]      = None
    overall_development_potential: Optional[float] = None
    buildability_score:  Optional[float]       = None
    build_complexity:    Optional[str]         = None
    purity_score:        Optional[float]       = None
    contamination_risk:  Optional[float]       = None
    est_total_slots:     Optional[int]         = None
    tags:                list[str]             = Field(default_factory=list)
    # Body counts surfaced flat for filter pills (the search SQL
    # projects these from the ratings row).
    elw_count:            Optional[int] = None
    ww_count:             Optional[int] = None
    ammonia_count:        Optional[int] = None
    gas_giant_count:      Optional[int] = None
    landable_count:       Optional[int] = None
    terraformable_count:  Optional[int] = None
    bio_signal_total:     Optional[int] = None
    geo_signal_total:     Optional[int] = None
    neutron_count:        Optional[int] = None
    black_hole_count:     Optional[int] = None
    white_dwarf_count:    Optional[int] = None

    # F2c/F3 confidence badge (V3 ranking path only; None on legacy rows).
    # `confidence` is the raw per-archetype/economy confidence used by the
    # ranking profile's uncertainty modifier; `completeness` is the
    # `v3_app.system_search` data-completeness fraction it is multiplied
    # against. Explicit (not left to `extra='allow'`) so clients can render
    # a confidence badge without depending on undocumented extra keys.
    confidence:           Optional[float] = None
    completeness:         Optional[float] = None


# Backwards-compatible alias — older imports used `SystemModel`.
SystemModel = SystemRow


# ══════════════════════════════════════════════════════════════════════
# Detail row (snake_case) — `/api/system/{id64}`
# ══════════════════════════════════════════════════════════════════════
class SystemDetailRow(BaseModel):
    """Full system detail returned by `/api/system/{id64}`.

    The detail endpoint does *not* go through the camelCase translator —
    it returns the joined ratings + bodies + stations rows snake_case
    as PostgreSQL emits them (see `routers/systems.py::get_system`).
    Frontend renders these strings directly.
    """
    model_config = ConfigDict(extra='allow')

    id64:               int
    name:               str
    x:                  Optional[float] = None
    y:                  Optional[float] = None
    z:                  Optional[float] = None
    population:         Optional[int] = None
    primary_economy:    Optional[str]  = None
    secondary_economy:  Optional[str]  = None
    security:           Optional[str]  = None
    allegiance:         Optional[str]  = None
    government:         Optional[str]  = None
    is_colonised:       Optional[bool] = None
    is_being_colonised: Optional[bool] = None
    main_star_type:     Optional[str]  = None
    main_star_subtype:  Optional[str]  = None
    body_data_updated_at: Optional[str] = None
    body_data_sources: Optional[list[str]] = None
    status_updated_at: Optional[str] = None
    status_source: Optional[str] = None

    # Archetype / buildability (from LEFT JOIN mv_archetype_rankings in the
    # /api/system/{id64} handler — nullable when no ranking row exists). CQ-030:
    # previously flowed through as extra='allow' untyped extras, so absent from
    # the OpenAPI schema and the frontend had to hand-augment. Declared here so
    # the schema is honest.
    primary_archetype:             Optional[str]   = None
    secondary_archetype:           Optional[str]   = None
    archetype_confidence:          Optional[float] = None
    overall_development_potential: Optional[float] = None
    buildability_score:            Optional[float] = None
    build_complexity:              Optional[str]   = None
    purity_score:                  Optional[float] = None
    contamination_risk:            Optional[float] = None
    est_total_slots:               Optional[int]   = None

    # Flat rating fields (joined from ratings table; snake_case here).
    score:                  Optional[float] = None
    score_agriculture:      Optional[float] = None
    score_refinery:         Optional[float] = None
    score_industrial:       Optional[float] = None
    score_hightech:         Optional[float] = None
    score_military:         Optional[float] = None
    score_tourism:          Optional[float] = None
    score_extraction:       Optional[float] = None
    economy_suggestion:     Optional[str]   = None
    rating_version:         Optional[str]   = None

    # Flat body counts.
    elw_count:              Optional[int] = None
    ww_count:               Optional[int] = None
    ammonia_count:          Optional[int] = None
    gas_giant_count:        Optional[int] = None
    landable_count:         Optional[int] = None
    terraformable_count:    Optional[int] = None
    bio_signal_total:       Optional[int] = None
    geo_signal_total:       Optional[int] = None
    neutron_count:          Optional[int] = None
    black_hole_count:       Optional[int] = None
    white_dwarf_count:      Optional[int] = None

    # v3.1 rating fields.
    terraforming_potential: Optional[float] = None
    body_diversity:         Optional[float] = None
    confidence:             Optional[float] = None
    rationale:              Optional[str]   = None

    bodies:   list[BodyModel]    = Field(default_factory=list)
    stations: list[StationModel] = Field(default_factory=list)

    exploration_value: Optional[ExplorationValueModel] = None


# ══════════════════════════════════════════════════════════════════════
# Response wrappers
# ══════════════════════════════════════════════════════════════════════
class RankingIdentity(BaseModel):
    """Echoes the versioned V3 ranking profile + pinned derived generation
    that produced a ranked response (F2c/F3 Task 5), so a client or an
    operator can tell exactly which profile+generation combination is
    behind a given result set without spelunking through logs.

    `ranking_sha256` is `edfinder_api.ranking.profile.ranking_sha256()` at
    response time -- it must always equal the live profile's hash, never a
    value baked in ahead of time.
    """
    model_config = ConfigDict(extra='forbid')

    ranking_version:       str
    ranking_sha256:        str
    # v3_meta.current_derived_generation.derived_generation_id is a UUID (see
    # sql/v3/migrations/006). Declaring it int made FastAPI response-model
    # validation reject every real ranked response as a 500. Match the Ratings
    # V4 response models: carry it as the UUID string.
    derived_generation_id: str
    publication_sequence:  int
    archetype_version:         str
    archetype_manifest_sha256: str
    archetype_model_sha256:    str


class SearchResponse(BaseModel):
    """`/api/local/search` response."""
    # Envelope-level forbid: any unrecognised top-level key is a sign
    # the model is out of date with what `local_search.py` returns —
    # surface that loudly via Pydantic ValidationError (→ 500) so it
    # gets fixed in the same PR. Row-level extras flow through under
    # `results: list[SystemRow]` where SystemRow keeps `extra='allow'`.
    model_config = ConfigDict(extra='forbid')

    results: list[SystemRow]
    total:   int = 0
    count:   int = 0
    # Optional — only set by local_db_search, helpful for ops visibility
    # so you can tell which code path served the response without
    # spelunking through logs. (FastAPI silently drops fields not in
    # the response model; without these, source/query_ms/display_economy
    # were being stripped before reaching the client — see d43fde8.)
    source:           Optional[str] = None
    query_ms:         Optional[int] = None
    display_economy:  Optional[str] = None
    # True when `total` was clamped at the galaxy-wide cap (currently
    # 10 000) instead of being a precise count of all matches. Render
    # as "X+" in the UI when set. Absent on local (distance-bounded)
    # searches where the precise count is cheap.
    total_is_capped:  Optional[bool] = None
    # Surfaces the radius-cap notice from `local_db_search`. UI can
    # render this as a small banner next to the results count.
    warning:          Optional[str] = None
    # Every successful V3 Finder envelope carries the complete ranking and
    # published archetype-product identity. Product readiness is checked
    # before the read, so a partial/identity-free envelope is never valid.
    ranking:          RankingIdentity


class SystemDetailResponse(BaseModel):
    """`/api/system/{id64}` response. Backend returns the same row
    twice under `record` and `system` for legacy compat."""
    model_config = ConfigDict(extra='forbid')

    record: SystemDetailRow
    system: SystemDetailRow


class HealthResponse(BaseModel):
    status:   str
    database: str
    version:  str
    build_sha: str


class AutocompleteHit(BaseModel):
    model_config = ConfigDict(extra='allow')

    id64:           int
    name:           str
    x:              Optional[float] = None
    y:              Optional[float] = None
    z:              Optional[float] = None
    population:     Optional[int] = None
    primaryEconomy: Optional[str] = None


class AutocompleteResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    results: list[AutocompleteHit]
    source:  Optional[str] = None


class StatusResponse(BaseModel):
    """`/api/status` and `/api/local/status`."""
    model_config = ConfigDict(extra='forbid')

    available:            bool
    systems_count:        int = 0
    body_count:           int = 0
    rated_count:          int = 0
    clustered_count:      int = 0
    import_complete:      bool = False
    ratings_built:        bool = False
    grid_built:           bool = False
    clusters_built:       bool = False
    eddn_enabled:         bool = False
    last_nightly_update:  str = 'never'
    schema_version:       str = '1.0'
    max_search_radius_ly: int = 500
    has_body_data:        bool = False
    version:              str = ''
    # Optional fields surfaced by /api/local/status (real DB-backed
    # variant of /api/status). Listed here so the envelope can be
    # `forbid` without breaking that endpoint.
    backend:              Optional[str]   = None
    pg_version:           Optional[str]   = None
    station_count:        Optional[int]   = None
    cluster_count:        Optional[int]   = None
    grid_cells:           Optional[int]   = None
    macro_grid_cells:     Optional[int]   = None
    galaxy_regions:       Optional[int]   = None
    db_size_mb:           Optional[float] = None
    import_status:        Optional[Any]   = None
    cluster_radius_ly:    Optional[int]   = None
    has_cluster_summary:  Optional[bool]  = None
    has_bodies:           Optional[bool]  = None
    reason:               Optional[str]   = None  # populated when available=False


class RetiredRouteProblem(BaseModel):
    """RFC 9457 problem detail for a retired route that answers 410 Gone."""
    model_config = ConfigDict(extra='forbid')

    type:   str
    title:  str
    status: int
    detail: str


class CacheStatsResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    cache_hits:       int = 0
    cache_misses:     int = 0
    redis_hits:       Optional[int]   = None
    redis_misses:     Optional[int]   = None
    redis_memory_mb:  Optional[float] = None
    db_cache_rows:    int = 0


class WatchlistAlert(BaseModel):
    min_development_score: Optional[int] = Field(
        default=None,
        ge=0, le=100,
        validation_alias=AliasChoices('min_development_score', 'min_score'),
        serialization_alias='min_development_score',
    )
    economy: Optional[str] = Field(default=None, max_length=64)

    model_config = {'populate_by_name': True}


class NoteBody(BaseModel):
    note: str


# ══════════════════════════════════════════════════════════════════════
# Request models
# ══════════════════════════════════════════════════════════════════════
class SearchFilters(BaseModel):
    distance:   Optional[RangeFilter] = None
    population: Optional[RangeFilter] = None
    # economy: strict typed union (Agriculture, Refinery, Industrial,
    # HighTech, Military, Tourism, Extraction, 'any'). The validator
    # normalises historical wire forms ('high tech', 'High Tech',
    # 'hightech', …) to the canonical PG enum literal — see
    # _normalise_economy_name for the full mapping. Unknown strings
    # fall through and are caught by Pydantic's union check, returning
    # a 422 with a clear "input should be 'Agriculture' or 'Refinery'
    # or …" message instead of the previous 503 from PostgreSQL.
    economy:    EconomyFilterField    = None


class LocalSearchRequest(BaseModel):
    filters:          Optional[SearchFilters] = None
    reference_coords: Optional[CoordsModel]   = None
    sort_by:          Optional[str]            = 'development'
    # size: 1..500. A missing lower bound let size=-1 through Pydantic and
    # become a negative SQL LIMIT (a DB error surfaced as a 503); from_ needs
    # an upper bound so an oversized offset can't overflow asyncpg int encoding.
    size:             int                      = Field(
        default=50, ge=RANKING_PAGE_LIMIT_MIN, le=RANKING_PAGE_LIMIT_MAX
    )
    from_:            int                      = Field(default=0, ge=0, le=2_147_483_647, alias='from')
    body_filters:     Optional[BodyFilters]    = None
    require_bio:      Optional[bool]           = None
    require_geo:      Optional[bool]           = None
    require_terra:    Optional[bool]           = None
    star_types:       Optional[list[str]]      = None
    # Bounded to the 0–100 development-score domain (the V3 projection's
    # best_colony_potential is a smallint CHECK 0..100). Without the upper
    # bound a value >32767 overflowed the smallint bind and surfaced as a
    # misleading 503 instead of a 422.
    min_development_score: Optional[int] = Field(default=None, ge=0, le=100)
    # Region scoping forwarded to the V3 ranked search. Bounded to the named
    # galactic-region domain (1..42), consistent with ClusterSearchRequest, so
    # an out-of-range id fails validation (422) rather than overflowing the
    # smallint bind. Without this field the value was silently dropped and the
    # region predicate was unreachable through the public endpoint.
    galaxy_region_id: Optional[int] = Field(default=None, ge=1, le=42)
    galaxy_wide:      bool                     = False

    model_config = {'populate_by_name': True}

    @field_validator('sort_by', mode='before')
    @classmethod
    def _normalise_sort_by(cls, value: object) -> object:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered == 'rating':
                return 'development'
            return lowered
        return value

    @model_validator(mode='after')
    def _bounded_result_window(self) -> 'LocalSearchRequest':
        if self.from_ + self.size > RANKING_OFFSET_PLUS_LIMIT_MAX:
            raise ValueError(
                'from + size must be less than or equal to '
                f'{RANKING_OFFSET_PLUS_LIMIT_MAX}'
            )
        return self


class GalaxySearchRequest(BaseModel):
    # extra='forbid' (F3 slice 2b): the V3 galaxy search rejects every legacy
    # feature it cannot honour, so an undeclared field (e.g. a legacy
    # `galaxy_region_id`) must be a 422, never silently discarded.
    model_config = ConfigDict(extra='forbid')

    # `EconomyFilter` — Agriculture | Refinery | Industrial | HighTech
    # | Military | Tourism | Extraction | 'any'. Old wire forms are
    # accepted via the model's normalising validator; see SearchFilters.
    economy:   EconomyFilterField = 'any'
    min_score: int = Field(default=0, ge=0, le=100)
    limit:     int = Field(default=100, le=500)
    offset:    int = Field(default=0, ge=0)
    # Declared so the legacy uncolonised-only request is explicit: `false`
    # answers 422 (v3_app.system_search has no population column); `true` or
    # omitted ranks every system in the published generation.
    include_colonised: Optional[bool] = None


class ClusterRequirement(BaseModel):
    # Cluster requirements MUST name a real economy — there's no
    # 'any' here, an empty cluster requirement is meaningless.
    economy:   EconomyNameField
    min_count: int = Field(default=1, ge=1)
    min_score: int = Field(default=40, ge=0, le=100)


class SlotRequirement(BaseModel):
    """One colony world the user wants to build.

    economies: 1-2 economy names that must ALL score >= min_score
    on a single system (same-world constraint).

    archetype_key: optional shorthand — if provided, economies and
    min_score are derived from TARGET_PROFILES and can be omitted.
    """
    economies:     list[EconomyNameField] = Field(default_factory=list)
    archetype_key: Optional[str] = None
    min_score:     int = Field(default=65, ge=0, le=100)
    label:         Optional[str] = None  # display label, auto-generated if omitted

    @model_validator(mode='after')
    def resolve_archetype(self) -> 'SlotRequirement':
        if self.archetype_key and not self.economies:
            from edfinder_api.domain.colonisation_rules import get_target_profile
            profile = get_target_profile(self.archetype_key)
            self.economies = [
                e for e in profile.expected_economies
                if e != 'Extraction'  # no cluster_summary column for Extraction
            ]
            if not self.label:
                self.label = self.archetype_key.replace('_', ' ').title()
        if not self.economies:
            raise ValueError(
                'Slot must specify at least one economy or a valid archetype_key'
            )
        if len(self.economies) > 2:
            raise ValueError(
                'A slot can have at most 2 economies (same-world constraint)'
            )
        if not self.label:
            self.label = ' + '.join(self.economies)
        return self


class ClusterSearchRequest(BaseModel):
    # Legacy format (still works)
    requirements:     list[ClusterRequirement] = Field(default_factory=list)
    # New slot-based format
    slots:            list[SlotRequirement] = Field(default_factory=list)
    limit:            int = Field(default=50, le=200)
    offset:           int = Field(default=0, ge=0)
    reference_coords: Optional[CoordsModel] = None
    galaxy_region_id: Optional[int] = Field(default=None, ge=1, le=42)

    @model_validator(mode='after')
    def validate_input(self) -> 'ClusterSearchRequest':
        if not self.requirements and not self.slots:
            raise ValueError(
                'At least one economy requirement must be specified'
            )
        if self.requirements and self.slots:
            raise ValueError(
                'Provide requirements OR slots, not both'
            )
        return self


class ClusterResult(BaseModel):
    # Result model — extra='allow' per models.py strictness policy (SQL
    # projections legitimately carry enriched fields). Fields typed here drive
    # the generated api.gen.ts. The shape is the retained legacy
    # `cluster_summary` row contract; F3 slice 2b repointed the route onto
    # the V3 cluster product (`local_search.local_db_cluster_search_v3`),
    # which fails closed (503) until a Finder cluster run and its summary
    # contract exist -- the reshape onto V3 fields happens with that
    # product, not before.
    model_config = ConfigDict(extra='allow')

    anchor_id64:       int
    system_id64:       int
    anchor_name:       str
    anchor_x:          float
    anchor_y:          float
    anchor_z:          float
    anchor_coords:     CoordsModel
    galaxy_region_id:  Optional[int] = None
    galaxy_region:     Optional[str] = None
    coverage_score:    Optional[float] = None
    economy_diversity: int
    total_viable:      int
    agriculture_count: int
    agriculture_best:  Optional[int] = None
    agriculture_top_id: Optional[int] = None
    refinery_count:    int
    refinery_best:     Optional[int] = None
    refinery_top_id:   Optional[int] = None
    industrial_count:  int
    industrial_best:   Optional[int] = None
    industrial_top_id: Optional[int] = None
    hightech_count:    int
    hightech_best:     Optional[int] = None
    hightech_top_id:   Optional[int] = None
    military_count:    int
    military_best:     Optional[int] = None
    military_top_id:   Optional[int] = None
    tourism_count:     int
    tourism_best:      Optional[int] = None
    tourism_top_id:    Optional[int] = None
    distance_ly:       Optional[float] = None
    cluster_radius_ly: float
    slots:             Optional[list[dict]] = None


class ClusterSearchResponse(BaseModel):
    # Response envelope for POST /api/search/cluster. extra='allow' (not
    # 'forbid') because the endpoint has success and error-dict return shapes;
    # error is optional to cover the {error, clusters:[]} early-return paths.
    model_config = ConfigDict(extra='allow')

    clusters:          list[ClusterResult] = Field(default_factory=list)
    count:             Optional[int] = None
    cluster_radius_ly: Optional[float] = None
    query_ms:          Optional[int] = None
    slots:             Optional[list[dict]] = None
    requirements:      Optional[list[dict]] = None
    error:             Optional[str] = None


# ══════════════════════════════════════════════════════════════════════
# Archetype engine models (v4.0 — Phase 3)
#
# Strictness policy (consistent with existing models above):
#   • Row / result models  → extra='allow'   (SQL projections legitimately drift)
#   • Response envelopes   → extra='forbid'  (loud failure if contract breaks)
#   • Request models       → extra='forbid'  (prevent silent input discarding)
# ══════════════════════════════════════════════════════════════════════

# ── Valid archetype keys (single source of truth) ────────────────────
# The 8 V3-native archetype keys the F2b/F2d `system_archetype` wide row
# scores every system against. Must stay identical, in order, to
# `edfinder_api.ranking.profile.ARCHETYPE_KEYS` (tests pin the equality);
# written out as a literal so the OpenAPI enum is static. The legacy
# 10-key V2 set (`refinery_industrial`, ...) is retired with F3 slice 2.
ArchetypeKey = Literal[
    'paradise',
    'mining_hub',
    'manufacturing_hub',
    'megacomplex',
    'research_hub',
    'stronghold',
    'population_capital',
    'flexible',
]

# ── Valid tier values ─────────────────────────────────────────────────
TierValue = Literal['S', 'A', 'B', 'C', 'D']


class SystemArchetypeExplanationEntry(BaseModel):
    """One recomputed and stored-value-verified V3 archetype explanation."""
    model_config = ConfigDict(extra='forbid')

    key: str
    score: int
    tier: TierValue
    confidence: float
    weighted_e4: int
    explanation: dict[str, Any]


class SystemArchetypeExplanationSummary(BaseModel):
    """The verified summary columns from the published wide archetype row."""
    model_config = ConfigDict(extra='forbid')

    primary_archetype: str
    secondary_archetype: Optional[str] = None
    best_colony_potential: int
    best_tier: TierValue
    archetype_confidence: float
    weighted_potential: float


class SystemArchetypeExplanationResponse(BaseModel):
    """On-demand V3 archetype explanations pinned to one product identity."""
    model_config = ConfigDict(extra='forbid')

    id64: int
    archetypes: list[SystemArchetypeExplanationEntry]
    summary: SystemArchetypeExplanationSummary
    completeness: float
    ranking: RankingIdentity


class ArchetypeRerankWeights(BaseModel):
    """
    Per-archetype blend weights for `POST /api/archetypes/rerank` (F3 slice 2).

    One weight per V3 archetype key, each in [0, 1]; every key is required
    (a partial vector is a 422, never silently filled) and at least one must
    be greater than zero. The blend is normalised by the weight sum, so the
    weights need not sum to 1.0 and a single non-zero weight reranks by that
    archetype alone. A request that omits `weights` entirely gets the
    generalist (equal) blend from the route, not from field defaults.

    The legacy five V2 dimensions (`purity`, `buildability`, `slots`,
    `expansion`, `logistics`) have no V3 projection and are retired.
    """
    model_config = ConfigDict(extra='forbid')

    paradise:           float = Field(ge=0.0, le=1.0)
    mining_hub:         float = Field(ge=0.0, le=1.0)
    manufacturing_hub:  float = Field(ge=0.0, le=1.0)
    megacomplex:        float = Field(ge=0.0, le=1.0)
    research_hub:       float = Field(ge=0.0, le=1.0)
    stronghold:         float = Field(ge=0.0, le=1.0)
    population_capital: float = Field(ge=0.0, le=1.0)
    flexible:           float = Field(ge=0.0, le=1.0)

    @model_validator(mode='after')
    def _at_least_one_positive(self) -> 'ArchetypeRerankWeights':
        if sum(self.model_dump().values()) <= 0.0:
            raise ValueError('at least one rerank weight must be greater than zero')
        return self


class ArchetypeScore(BaseModel):
    """One stored V3 archetype fit inside SystemArchetypeResponse.archetypes.

    Read verbatim from the published wide row (migration 011). The on-demand,
    version-gated explanation lives at `GET /api/archetypes/system/{id64}/
    explanation`; no rationale or stored explanation exists in V3.
    """
    model_config = ConfigDict(extra='forbid')

    score:       int
    tier:        TierValue
    label:       str
    confidence:  float
    weighted_e4: int


class SystemArchetypeFacts(BaseModel):
    """The `v3_app.system_search` facts carried by SystemArchetypeResponse."""
    model_config = ConfigDict(extra='forbid')

    body_count:          int
    landable_count:      int
    station_count:       int
    has_rings:           bool
    has_biologicals:     bool
    has_geologicals:     bool
    has_terraformable:   bool
    elw_count:           Optional[int] = None
    ww_count:            Optional[int] = None
    terraformable_count: Optional[int] = None
    gas_giant_count:     Optional[int] = None
    ring_count:          Optional[int] = None
    bio_signal_total:    Optional[int] = None
    geo_signal_total:    Optional[int] = None


# ── Response: GET /api/archetypes/system/{id64} ───────────────────────
class SystemArchetypeResponse(BaseModel):
    """Full V3 archetype breakdown for one system (F3 slice 2).

    Generation-pinned read of `v3_app.system_search` + the published
    `v3_app.system_archetype` wide row. The legacy V2 fields with no V3
    projection -- `buildability_score`, `build_complexity`, `cp_efficiency`,
    `t3_scaling_viability`, `slot_efficiency`, `purity_score`,
    `contamination_risk`, `stable_top_two_prob`, `topology`, `economy_pairs`
    and `tags` -- are retired rather than returned as permanent nulls; a later
    derived product may restore slot/topology detail.
    """
    model_config = ConfigDict(extra='forbid')

    id64:             int
    name:             str
    coords:           CoordsModel
    distance_to_sol:  float
    main_star_class:  Optional[str] = None
    galaxy_region_id: Optional[int] = None
    galaxy_region:    Optional[str] = None

    # Per-archetype fit map: archetype_key -> ArchetypeScore (all 8 V3 keys)
    archetypes:       dict[str, ArchetypeScore]

    primary_archetype:    str
    secondary_archetype:  Optional[str] = None
    archetype_confidence: float

    # Summary `best_colony_potential` keeps the established response name.
    overall_development_potential: int
    best_tier:          TierValue
    weighted_potential: float

    # Evidence badge from the search projection (unknown != absent).
    confidence:   float
    completeness: float

    facts:    SystemArchetypeFacts
    query_ms: Optional[int] = None
    ranking:  RankingIdentity


# ── Result row: GET /api/archetypes/rankings ─────────────────────────
class ArchetypeRankingRow(BaseModel):
    """One result row inside ArchetypeRankingsResponse."""
    model_config = ConfigDict(extra='allow')

    id64:             int
    name:             str
    coords:           Optional[CoordsModel] = None
    distance_to_sol:  Optional[float]       = None
    score:            float
    tier:             TierValue
    # The archetype the caller ranked BY (echoes the request). Distinct from
    # `primary_archetype`, which is the system's own strongest archetype read
    # from the summary -- the two often differ.
    selected_archetype:   Optional[str]   = None
    rival_archetype: Optional[str] = Field(
        None,
        description=(
            'Highest-scoring archetype other than the selected archetype and '
            'Flexible; ties use ARCHETYPE_KEYS order.'
        ),
    )
    rival_score: Optional[int] = Field(
        None,
        description='Rival archetype integer score (0–100) from the same stored system row.',
    )
    primary_archetype:    Optional[str]   = None
    secondary_archetype:  Optional[str]   = None
    archetype_confidence: Optional[float] = None
    overall_development_potential: Optional[float] = None
    buildability_score:  Optional[float]  = None
    build_complexity:    Optional[str]    = None
    purity_score:        Optional[float]  = None
    contamination_risk:  Optional[float]  = None
    confidence:          Optional[float]  = None
    # F2c/F3 confidence badge (Task 5): the `v3_app.system_search`
    # data-completeness fraction the uncertainty modifier multiplies
    # `confidence` against. Explicit, not left to `extra='allow'`.
    completeness:        Optional[float]  = None
    has_elw:             Optional[bool]   = None
    elw_count:           Optional[int]    = None
    landable_count:      Optional[int]    = None
    est_total_slots:     Optional[int]    = None
    tags:                list[str]        = Field(default_factory=list)


# ── Response: GET /api/archetypes/rankings ───────────────────────────
class ArchetypeRankingsResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    archetype:       str
    archetype_label: str
    results:         list[ArchetypeRankingRow]
    total:           int = 0
    count:           int = 0
    is_truncated:    bool
    source:          Optional[str] = None
    query_ms:        Optional[int] = None
    _cached:         Optional[bool] = None
    # Always set by the generation-pinned V3 `/rankings` path.
    ranking:         RankingIdentity


# ── Request: POST /api/archetypes/rerank ─────────────────────────────
class ArchetypeRerankRequest(BaseModel):
    """
    Rerank a set of systems by a client-weighted blend of their stored V3
    archetype fits (F3 slice 2).

    Weight resolution order: `profile` (a preset from
    `GET /api/archetypes/profiles`; unknown ids are rejected with 422), then
    explicit `weights`, then -- when only `archetype` is given -- a blend that
    weights that archetype alone, else the generalist equal blend.
    `archetype` also selects which raw stored score `original_score` reports
    (default: the summary `best_colony_potential`).
    """
    model_config = ConfigDict(extra='forbid')

    id64s:     list[Annotated[int, Field(ge=0, le=9_223_372_036_854_775_807)]] = Field(
        ..., min_length=1, max_length=500,
    )
    archetype: Optional[ArchetypeKey]           = None
    weights:   Optional[ArchetypeRerankWeights] = None
    profile:   Optional[str]                    = Field(None, min_length=1, max_length=64)


# ── Result row: POST /api/archetypes/rerank ───────────────────────────
class ArchetypeRerankRow(BaseModel):
    """One reranked system.

    `reranked_score` is the blend of the stored uncertainty-modified keys
    (`score x confidence x completeness`, see `ranking.rerank`) on the 0..100
    scale and is the ordering key; `tier` is derived from it. `original_score`
    is the raw stored score the request's `archetype` selects (or the summary
    `best_colony_potential`). V3 stores no rationale; explanations are served
    by `GET /api/archetypes/system/{id64}/explanation`.
    """
    model_config = ConfigDict(extra='forbid')

    id64:                int
    reranked_score:      int
    tier:                TierValue
    original_score:      Optional[float] = None
    confidence:          Optional[float] = None
    completeness:        Optional[float] = None
    primary_archetype:   Optional[str]   = None
    secondary_archetype: Optional[str]   = None


# ── Response: POST /api/archetypes/rerank ─────────────────────────────
class ArchetypeRerankResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    archetype:       Optional[str]              = None
    profile_applied: Optional[str]              = None
    weights_applied: ArchetypeRerankWeights
    results:         list[ArchetypeRerankRow]
    # Requested ids with no published wide row, so a caller can tell an
    # unknown system from a low-ranked one.
    missing_id64s:   list[int]                  = Field(default_factory=list)
    query_ms:        Optional[int]              = None
    ranking:         RankingIdentity


# ── Request: POST /api/archetypes/simulate ───────────────────────────
class PlannedFacility(BaseModel):
    """One facility in a build simulation plan."""
    model_config = ConfigDict(extra='allow')

    type:   str = Field(min_length=1, max_length=128)  # e.g. 'StarPort', 'RefineryHub'
    tier:   Optional[int] = Field(None, ge=1, le=3)
    body:   Optional[str] = Field(None, max_length=256)  # body key or name


class BuildSimulateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id64:              int = Field(ge=0, le=9_223_372_036_854_775_807)
    planned_archetype: ArchetypeKey
    planned_facilities: list[PlannedFacility] = Field(default_factory=list, max_length=200)


# ── Response: POST /api/archetypes/simulate ──────────────────────────
class BuildSimulateResponse(BaseModel):
    """Build simulation against the published V3 archetype fit (F3 slice 2).

    `simulation_score` is the system's stored fit for `planned_archetype`.
    The legacy `contamination_risk`, `purity_score` and `buildability_score`
    estimates have no V3 projection and are retired; `disclaimer` states
    that gap on every response.
    """
    model_config = ConfigDict(extra='forbid')

    id64:                  int
    planned_archetype:     str
    simulation_score:      int
    tier:                  TierValue
    confidence:            float
    completeness:          float
    primary_archetype:     str
    secondary_archetype:   Optional[str] = None
    best_colony_potential: int
    recommendations:       list[str]     = Field(default_factory=list)
    disclaimer:            str
    ranking:               RankingIdentity


# ── Response: simulation/buildability endpoints ───────────────────────
# -- Request/response: POST /api/simulate/build -----------------------------
class SimulateBuildPlacement(BaseModel):
    """One user-selected facility placement in Simulation Preview."""
    model_config = ConfigDict(extra='forbid')

    facility_template_id: str
    local_body_id:        Optional[str] = None
    is_primary_port:      bool          = False
    build_order:          int           = Field(default=1, ge=1)


class SimulateBuildRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_id64:       int
    target_archetype:  str
    placements:        list[SimulateBuildPlacement] = Field(default_factory=list)


class SimulationCPResult(BaseModel):
    model_config = ConfigDict(extra='forbid')

    yellow_cp_final:     int
    green_cp_final:      int
    yellow_cp_generated: int = 0
    green_cp_generated:  int = 0
    yellow_cp_spent:     int = 0
    green_cp_spent:      int = 0
    t2_ports:            int
    t3_ports:            int
    warnings:            list[str] = Field(default_factory=list)


class SimulationLink(BaseModel):
    model_config = ConfigDict(extra='forbid')

    port_facility_id:    Optional[str] = None
    support_facility_id: str
    local_body_id:       Optional[str] = None
    economy:             Optional[str] = None
    value:               float = 0.0
    note:                str


class SimulationLinks(BaseModel):
    model_config = ConfigDict(extra='forbid')

    strong_links: list[SimulationLink] = Field(default_factory=list)
    weak_links:   list[SimulationLink] = Field(default_factory=list)


class SimulationInheritedEconomy(BaseModel):
    model_config = ConfigDict(extra='forbid')

    source_body_id:     Optional[str] = None
    source_body_name:   Optional[str] = None
    base_economies:     list[str] = Field(default_factory=list)
    modifier_economies: list[str] = Field(default_factory=list)
    weights:            dict[str, float] = Field(default_factory=dict)
    purity:             float
    confidence:         float
    caveats:            list[str] = Field(default_factory=list)
    strategic_tags:     list[str] = Field(default_factory=list)


class SimulateBuildResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_id64:         int
    mechanics_version:   str = ''
    target_archetype:    str
    final_score:         float
    composition_score:   float
    buildability_score:  float
    build_complexity:    Literal['simple', 'moderate', 'advanced', 'expert']
    confidence:          float
    cp:                  SimulationCPResult
    cp_timeline:         list[dict[str, Any]] = Field(default_factory=list)
    cp_repair_suggestions: list[dict[str, Any]] = Field(default_factory=list)
    observation_summary: dict[str, Any] = Field(default_factory=dict)
    prediction_observation_diffs: list[dict[str, Any]] = Field(default_factory=list)
    economy_composition: dict[str, float] = Field(default_factory=dict)
    economy_order:       list[str] = Field(default_factory=list)
    economy_stack:       dict[str, Any] = Field(default_factory=dict)
    port_economy_states: list[dict[str, Any]] = Field(default_factory=list)
    influence_ledger:    list[dict[str, Any]] = Field(default_factory=list)
    inherited_economies: list[SimulationInheritedEconomy] = Field(default_factory=list)
    topology:            dict[str, Any] = Field(default_factory=dict)
    services:            dict[str, Any] = Field(default_factory=dict)
    port_service_states: list[dict[str, Any]] = Field(default_factory=list)
    service_unlock_ledger: list[dict[str, Any]] = Field(default_factory=list)
    data_quality:        dict[str, str] = Field(default_factory=dict)
    confidence_signals:  list[dict[str, Any]] = Field(default_factory=list)
    mechanics_trace:     dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    top_two_alignment:   str
    contamination_risk:  str
    warnings:            list[str] = Field(default_factory=list)
    strengths:           list[str] = Field(default_factory=list)
    recommendations:     list[str] = Field(default_factory=list)
    mechanics_notes:     list[str] = Field(default_factory=list)
    links:               SimulationLinks


class RecommendedBuildPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id:                 str
    label:              str
    summary:            str
    complexity:         Literal['simple', 'moderate', 'advanced', 'expert']
    confidence:         float
    final_score:        float
    composition_score:  float
    buildability_score: float
    economy_result:     dict[str, float] = Field(default_factory=dict)
    port_economy_summary: list[str] = Field(default_factory=list)
    cp_result:          SimulationCPResult
    build_order:        list[SimulateBuildPlacement] = Field(default_factory=list)
    strengths:          list[str] = Field(default_factory=list)
    warnings:           list[str] = Field(default_factory=list)
    tradeoffs:          list[str] = Field(default_factory=list)
    next_actions:       list[str] = Field(default_factory=list)
    selected_body_id:   Optional[str] = None
    selected_body_name: Optional[str] = None
    body_selection_reason: str = ''
    mechanics_basis:    list[str] = Field(default_factory=list)
    economy_caveats:    list[str] = Field(default_factory=list)
    assumptions:        list[str] = Field(default_factory=list)
    regional_role:      Optional[str] = None
    nearest_colony_distance: Optional[float] = None
    archetype_regional_fit: Optional[float] = None
    regional_rationale: dict[str, Any] = Field(default_factory=dict)
    decision_explanation: dict[str, Any] = Field(default_factory=dict)
    rank_breakdown:     dict[str, float] = Field(default_factory=dict)
    simulation_request: SimulateBuildRequest
    is_default:         bool = False


class RecommendedBuildsResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_id64:              int
    mechanics_version:        str = ''
    target_archetype:         str
    best_suggested_archetype: str
    recommended_next_action:  str
    plans:                   list[RecommendedBuildPlan] = Field(default_factory=list)
    warnings:                list[str] = Field(default_factory=list)


class RegionalAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_id64:                 int
    mechanics_version:           str = ''
    claim_range_ly:              float = 0.0
    analysis_radius_ly:          float = 0.0
    nearest_colonised_system:    Optional[dict[str, Any]] = None
    counts:                      dict[str, int] = Field(default_factory=dict)
    scores:                      dict[str, float] = Field(default_factory=dict)
    regional_role:               str = 'unknown'
    archetype_regional_fit:      dict[str, float] = Field(default_factory=dict)
    rationale:                   dict[str, Any] = Field(default_factory=dict)
    data_quality:                dict[str, str] = Field(default_factory=dict)
    confidence_signals:          list[dict[str, Any]] = Field(default_factory=list)
    computed_at:                 Optional[Any] = None


class FacilityTemplateResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id:                  str
    name:                str
    category:            str
    tier:                int
    economy:             Optional[str] = None
    is_port:             bool
    is_support_facility: bool
    allowed_location:    str
    pad_size:            Optional[str] = None
    confidence:          Optional[str] = None
    canonical_confidence: Optional[dict[str, Any]] = None  # EDRE-aligned canonical shape
    notes:               Optional[str] = None
    # list[dict[str, Any]] / dict[str, Any] still emit as
    # Record<string, never> via openapi-typescript (its handling of Pydantic's
    # `additionalProperties: {}` schema for a bare Any value type) — the exact
    # unusable-generated-type trap this file's own module docstring warns
    # about. These three catalogue fields are genuinely free-form (frontend
    # code defensively probes several possible keys), so `Any` is the
    # documented remedy rather than inventing a fixed sub-model shape.
    prerequisites:       list[Any] = Field(default_factory=list)
    economy_effects:     Any = Field(default_factory=dict)
    yellow_cp_generated: int = 0
    green_cp_generated:  int = 0
    yellow_cp_cost:      int = 0
    green_cp_cost:       int = 0
    stat_effects:        Any = Field(default_factory=dict)


SimulationSource = Literal['precomputed', 'computed', 'insufficient_data']
SlotDataSource = Literal['eddn', 'spansh', 'none']
SlotPredictionStatus = Literal['predicted', 'unknown', 'observed']


class SlotReason(BaseModel):
    """One explainability note for a per-body slot prediction."""
    model_config = ConfigDict(extra='allow')

    factor:       str
    delta:        Optional[float] = None
    note:         Optional[str]   = None


class BodySlotPrediction(BaseModel):
    """One body row returned by GET /api/systems/{id64}/slot-predictions."""
    model_config = ConfigDict(extra='allow')

    system_address: int
    body_id:        int
    body_name:      Optional[str]   = None
    planet_class:   Optional[str]   = None
    predicted_ground_slots: Optional[int] = None
    predicted_orbital_slots: Optional[int] = None
    prediction_status: SlotPredictionStatus = 'unknown'
    confidence_label: Optional[str] = None
    prediction_version: Optional[str] = None
    validation_note: Optional[str] = None
    required_input_missing: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    source_label: Optional[str] = None
    estimated_surface_slots: Optional[int] = None
    estimated_orbital_slots: Optional[int] = None
    slot_confidence: Optional[float] = None
    slot_source:    Optional[str] = None
    reasons:        list[SlotReason]= Field(default_factory=list)
    is_ringed:      Optional[bool]  = None
    is_landable:    Optional[bool]  = None
    radius:         Optional[float] = None


class SlotPredictionResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_id64:             int
    data_source:             SlotDataSource
    body_count:              int
    predicted_orbital_slots_total: Optional[int] = None
    predicted_ground_slots_total:  Optional[int] = None
    prediction_status: SlotPredictionStatus = 'unknown'
    prediction_version: str
    confidence_label: Optional[str] = None
    disclaimer: str
    validation_note: str
    required_input_missing: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    source_label: Optional[str] = None
    estimated_orbital_slots: Optional[int] = None
    estimated_ground_slots:  Optional[int] = None
    slot_confidence:         Optional[float] = None
    slot_confidence_label:   Optional[str] = None
    predictions:             list[BodySlotPrediction] = Field(default_factory=list)
    note:                    Optional[str] = None


class BuildabilityIssue(BaseModel):
    """Bottleneck/opportunity item with frontend-friendly wording."""
    model_config = ConfigDict(extra='allow')

    type:        str
    description: str
    severity:    Optional[str] = None
    detail:      Optional[str] = None


class BuildabilityBottleneck(BuildabilityIssue):
    """A buildability limitation surfaced to the frontend."""


class BuildabilityOpportunity(BuildabilityIssue):
    """A buildability advantage surfaced to the frontend."""


class RecommendedBuildStep(BaseModel):
    model_config = ConfigDict(extra='allow')

    step:                 int
    facility_id:          Optional[str] = None
    facility_name:        Optional[str] = None
    location:             Optional[str] = None
    notes:                Optional[str] = None
    cumulative_yellow_cp: Optional[int] = None
    cumulative_green_cp:  Optional[int] = None


class BuildabilityData(BaseModel):
    """Stable buildability payload used both directly and inside summaries."""
    model_config = ConfigDict(extra='allow')

    source:                  SimulationSource
    estimated_orbital_slots: Optional[int]   = None
    estimated_ground_slots:  Optional[int]   = None
    slot_confidence:         Optional[float] = None
    slot_confidence_label:   Optional[str]   = None
    estimated_yellow_cp:     Optional[int]   = None
    estimated_green_cp:      Optional[int]   = None
    max_t2_ports:            Optional[int]   = None
    max_t3_ports:            Optional[int]   = None
    cp_bottleneck_score:     Optional[float] = None
    slot_exhaustion_risk:    Optional[float] = None
    build_order_sensitivity: Optional[float] = None
    build_complexity:        Optional[str]   = None
    bottlenecks:             list[BuildabilityBottleneck] = Field(default_factory=list)
    opportunities:           list[BuildabilityOpportunity] = Field(default_factory=list)
    recommended_build_order: list[RecommendedBuildStep] = Field(default_factory=list)
    warnings:                list[str]       = Field(default_factory=list)
    note:                    Optional[str]   = None


class TopologyContextResponse(BaseModel):
    model_config = ConfigDict(extra='allow')

    orbital_slots:          int   = 0
    surface_slots:          int   = 0
    has_ringed_body:        bool  = False
    has_viable_surface:     bool  = True
    has_deep_anchor:        bool  = False
    orbital_synergy:        float = 0.0
    ground_synergy:         float = 0.0
    build_flexibility:      float = 0.0
    contamination_risk:     float = 0.0
    strong_link_potential:  float = 0.0
    weak_link_stability:    float = 0.0
    nesting_potential:      float = 0.0
    slot_confidence:        float = 0.0


class BuildabilityResponse(BuildabilityData):
    model_config = ConfigDict(extra='forbid')

    system_id64:      int
    system_name:      Optional[str] = None
    archetype:        Optional[str] = None
    topology_summary: list[str] = Field(default_factory=list)
    topology:         Optional[TopologyContextResponse] = None


# Backwards-compatible public name used by older imports/docs.
SystemBuildabilityResponse = BuildabilityResponse


class SimulationClassification(BaseModel):
    model_config = ConfigDict(extra='allow')

    primary_archetype:   Optional[str]   = None
    secondary_archetype: Optional[str]   = None
    confidence:          Optional[float] = None
    overall_potential:   Optional[float] = None
    purity_score:        Optional[float] = None
    display_tags:        list[str]       = Field(default_factory=list)
    data_confidence:     Optional[float] = None
    rationale:           Optional[Any]   = None


class SimulationBodySummary(BaseModel):
    model_config = ConfigDict(extra='forbid')

    elw_count:           int = 0
    hmc_count:           int = 0
    gas_giant_count:     int = 0
    terraformable_count: int = 0
    bio_signal_total:    int = 0
    geo_signal_total:    int = 0
    scanned_body_count:  int = 0


class SimulationSummaryResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    system_id64:       int
    mechanics_version: str = ''
    system_name:       Optional[str] = None
    archetype:         Optional[str] = None
    classification:    Optional[SimulationClassification] = None
    archetype_scores:  dict[str, float] = Field(default_factory=dict)
    body_summary:      Optional[SimulationBodySummary] = None
    buildability:      BuildabilityData
    topology_summary:  list[str] = Field(default_factory=list)
    regional_context:  Optional[RegionalAnalysisResponse] = None
    distance_to_sol:   Optional[float] = None
    main_star_type:    Optional[str] = None


# ── Profile entry: GET /api/archetypes/profiles ──────────────────────
class ArchetypeProfile(BaseModel):
    """One static preset rerank blend (F3 slice 2: V3 archetype keys only)."""
    model_config = ConfigDict(extra='forbid')

    id:          str
    label:       str
    description: str
    archetype:   Optional[ArchetypeKey]    = None
    weights:     ArchetypeRerankWeights


# ── Response: GET /api/archetypes/profiles ───────────────────────────
class ArchetypesProfilesResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    profiles: list[ArchetypeProfile]


# ── Optimiser V1: Candidate Generation (Stage 5A) ────────────────────
class OptimiserCandidatePlacement(BaseModel):
    """One facility placement in an optimiser candidate."""
    model_config = ConfigDict(extra='forbid')

    facility_template_id: str
    local_body_id: Optional[str] = None
    is_primary_port: bool = False
    build_order: int = Field(default=1, ge=1)


class OptimiserCandidatePreviewSummary(BaseModel):
    """Lightweight optimiser-specific summary of Simulation Preview output."""
    model_config = ConfigDict(extra='forbid')

    final_score: Optional[float] = None
    composition_score: Optional[float] = None
    buildability_score: Optional[float] = None
    confidence: Optional[float] = None
    build_complexity: Optional[str] = None
    warnings_count: int = 0
    cp_negative: Optional[bool] = None
    top_two_alignment: Optional[str] = None


class OptimiserCandidate(BaseModel):
    """A single bounded Stage 5A candidate plan."""
    model_config = ConfigDict(extra='forbid')

    candidate_id: str
    label: str
    target_archetype: str
    strategy: str
    placements: list[OptimiserCandidatePlacement]
    rationale: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    preview_summary: Optional[OptimiserCandidatePreviewSummary] = None


class OptimiserRankBreakdown(BaseModel):
    """Structured explanation for a ranked optimiser candidate."""
    model_config = ConfigDict(extra='forbid')

    preview_score_component: float = 0.0
    composition_component: float = 0.0
    buildability_component: float = 0.0
    confidence_component: float = 0.0
    alignment_component: float = 0.0
    warning_penalty: float = 0.0
    cp_penalty: float = 0.0
    strategy_modifier: float = 0.0
    total_score: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class OptimiserRankedCandidate(BaseModel):
    """Ranking entry that references a candidate by ID without duplicating it."""
    model_config = ConfigDict(extra='forbid')

    candidate_id: str
    rank: int = Field(ge=1)
    rank_score: float = Field(ge=0.0, le=100.0)
    rank_tier: str
    rank_breakdown: OptimiserRankBreakdown


class OptimiserRankingResponse(BaseModel):
    """Top-level optional Stage 5B ranking result."""
    model_config = ConfigDict(extra='forbid')

    target_archetype: str
    ranked_candidates: list[OptimiserRankedCandidate]
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class OptimiserCandidatesRequest(BaseModel):
    """Request for bounded deterministic Stage 5A candidate generation."""
    model_config = ConfigDict(extra='forbid')

    system_id64: int
    target_archetype: Optional[str] = None
    target_archetype_key: Optional[str] = None
    max_candidates: int = Field(default=5, ge=1, le=10)
    preferred_body_ids: list[str] = Field(default_factory=list)
    allow_estimated_data: bool = True
    run_preview: bool = True
    include_ranking: bool = False


class OptimiserCandidatesResponse(BaseModel):
    """Response envelope for bounded deterministic Stage 5A candidates."""
    model_config = ConfigDict(extra='forbid')

    system_id64: int
    target_archetype: str
    candidate_count: int
    candidates: list[OptimiserCandidate]
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    ranking: Optional[OptimiserRankingResponse] = None


# Backwards-compatible singular names for older imports.
OptimiserCandidateRequest = OptimiserCandidatesRequest
OptimiserCandidateResponse = OptimiserCandidatesResponse


# ─────────────────────────────────────────────────────────────────────────────
# Real-Star LOD Viewport Endpoint (Phase 1)
# ─────────────────────────────────────────────────────────────────────────────

class MapViewportSystem(BaseModel):
    """Individual system in a viewport query result."""
    model_config = ConfigDict(extra='allow')

    id64: int
    name: str
    x: float
    y: float
    z: float
    main_star_class: Optional[str] = None
    populated: bool


class MapViewportRequest(BaseModel):
    """Request for systems within a bounding box."""
    model_config = ConfigDict(extra='forbid')

    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float
    limit: int = Field(default=10000, ge=1, le=40000)
    min_importance: Optional[int] = None

    @field_validator('min_x', 'min_y', 'min_z')
    @classmethod
    def validate_mins(cls, v: float) -> float:
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError('must be a finite number')
        return v

    @field_validator('max_x', 'max_y', 'max_z')
    @classmethod
    def validate_maxes(cls, v: float) -> float:
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError('must be a finite number')
        return v

    @field_validator('max_x')
    @classmethod
    def validate_max_x_bounds(cls, v: float, info: ValidationInfo) -> float:
        data = info.data
        if 'min_x' in data and v < data['min_x']:
            raise ValueError('max_x must be >= min_x')
        return v

    @field_validator('max_y')
    @classmethod
    def validate_max_y_bounds(cls, v: float, info: ValidationInfo) -> float:
        data = info.data
        if 'min_y' in data and v < data['min_y']:
            raise ValueError('max_y must be >= min_y')
        return v

    @field_validator('max_z')
    @classmethod
    def validate_max_z_bounds(cls, v: float, info: ValidationInfo) -> float:
        data = info.data
        if 'min_z' in data and v < data['min_z']:
            raise ValueError('max_z must be >= min_z')
        return v


class MapViewportResponse(BaseModel):
    """Response envelope for viewport system query."""
    model_config = ConfigDict(extra='forbid')

    systems: list[MapViewportSystem]
    truncated: bool = False
