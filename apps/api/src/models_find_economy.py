"""The request and answer of ``POST /api/find/economy`` (``find_economy.py``)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from edfinder_api.models import BodyFilters, CoordsModel
from edfinder_api.models_economy import EconomyName
from edfinder_api.ranking.profile import (
    RANKING_OFFSET_PLUS_LIMIT_MAX,
    RANKING_PAGE_LIMIT_MAX,
    RANKING_PAGE_LIMIT_MIN,
)


class EconomySearchRequest(BaseModel):
    # One economy, or two for a pair.
    economies:        list[EconomyName]        = Field(min_length=1, max_length=2)
    # For one economy: strongest (the default) or purest. A pair has one order.
    order:            Optional[Literal['strongest', 'purest', 'pair']] = None
    reference_coords: CoordsModel
    # How far from the point to look: above 0, at most 250 ly.
    radius_ly:        float                    = Field(default=100.0, gt=0, le=250)
    # Find's reach rule: within 250 ly of inhabited space (the default) or 500.
    reach_ly:         Literal[250, 500]        = 250
    body_filters:     Optional[BodyFilters]    = None
    size:             int                      = Field(
        default=50, ge=RANKING_PAGE_LIMIT_MIN, le=RANKING_PAGE_LIMIT_MAX
    )
    from_:            int                      = Field(default=0, ge=0, le=2_147_483_647, alias='from')
    # As for /api/local/search: give the count the whole time limit.
    wait_for_count:   bool                     = False

    model_config = {'populate_by_name': True}

    @model_validator(mode='after')
    def _bounded_result_window(self) -> 'EconomySearchRequest':
        if self.from_ + self.size > RANKING_OFFSET_PLUS_LIMIT_MAX:
            raise ValueError(
                'from + size must be less than or equal to '
                f'{RANKING_OFFSET_PLUS_LIMIT_MAX}'
            )
        return self


class _BestBody(BaseModel):
    """The steps of the order that the system's best body was placed by, read
    back from its number (``find_numbers.py``). These three are in every
    order; each order adds its own."""
    model_config = ConfigDict(extra='forbid')

    # How many economies a port there would have beyond its top two.
    third_economies:    int
    # The build body's predicted ground slots.
    ground_slots:       int
    # For Extraction: the body has rings or a belt, so an asteroid base fits.
    asteroid_base_room: bool


class EconomyStrongestBody(_BestBody):
    """order = strongest."""
    # What the port starts with plus one facility on every ground slot; what
    # each facility adds; what the port starts with.
    ground_part:        float
    each_facility_adds: float
    port_starts_with:   float


class EconomyPurestBody(_BestBody):
    """order = purest."""
    # The port starts with the economy.
    port_starts_with_it: bool
    ground_part:         float
    each_facility_adds:  float


class EconomyPairBody(_BestBody):
    """order = pair. The weaker and the stronger of the two economies, the
    ground slots shared out between them; which economy is the weaker is not
    in the number."""
    weaker_ground_part:          float
    stronger_ground_part:        float
    weaker_each_facility_adds:   float
    stronger_each_facility_adds: float
    port_starts_with_both:       float


class EconomySearchRow(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id64:      int
    id:        str
    name:      str
    coords:    CoordsModel
    distance:  float
    main_star: Optional[str] = None
    claim_status:          Optional[str] = None
    claim_station_seen_at: Optional[str] = None
    # The system's stars, planets and moons that are known: Find takes this
    # as its orbital room. Then the predicted ground slots of all its bodies.
    bodies_known:  int
    ground_slots:  int
    # Where the system stands in the asked order: higher is first. Systems
    # with the same number and the same bodies_known are level.
    order_number:  int
    # Which of the three shapes it has goes with the answer's `order`.
    best_body:     EconomyStrongestBody | EconomyPurestBody | EconomyPairBody
    # The search row's counts.
    body_count:          Optional[int] = None
    station_count:       Optional[int] = None
    elw_count:           Optional[int] = None
    ww_count:            Optional[int] = None
    ammonia_count:       Optional[int] = None
    gas_giant_count:     Optional[int] = None
    landable_count:      Optional[int] = None
    terraformable_count: Optional[int] = None
    bio_signal_total:    Optional[int] = None
    geo_signal_total:    Optional[int] = None
    neutron_count:       Optional[int] = None
    black_hole_count:    Optional[int] = None
    white_dwarf_count:   Optional[int] = None
    hmc_count:           Optional[int] = None
    metal_rich_count:    Optional[int] = None
    rocky_count:         Optional[int] = None
    rocky_ice_count:     Optional[int] = None
    icy_count:           Optional[int] = None
    other_star_count:    Optional[int] = None
    ring_count:          Optional[int] = None
    walkable_count:      Optional[int] = None


class EconomyFactsState(BaseModel):
    """Which fill of the facts the answer was read from."""
    model_config = ConfigDict(extra='forbid')

    version:      str
    fill_id:      int
    finished_at:  str
    # The rows have been put in order of place on disk, so searches are quick.
    in_order_on_disk: bool
    # A later fill is under way (a new rulebook): rows of both stand side by side.
    updating:              bool
    updating_to:           Optional[str] = None
    updating_systems_done: Optional[int] = None


class EconomySearchResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    results: list[EconomySearchRow]
    count:   int
    total:   int
    total_is_capped:  bool
    # False when the matches could not be counted in the time given; `total`
    # is then how many matches the page itself shows there to be.
    total_is_counted: bool
    query_ms: int
    source:   str
    economies: list[EconomyName]
    order:     Literal['strongest', 'purest', 'pair']
    radius_ly: float
    reach_ly:  Literal[250, 500]
    facts:     EconomyFactsState
