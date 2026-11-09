"""Service unlock modelling constants."""
from __future__ import annotations


SERVICE_STATUS_ACTIVE = 'active'
SERVICE_STATUS_LOCKED = 'locked'
SERVICE_STATUS_UNKNOWN = 'unknown'

MODELLED_SERVICES = {
    'commodity_market',
    'shipyard',
    'outfitting',
    'universal_cartographics',
    'vista_genomics',
    'black_market',
    'crew_lounge',
    'pioneer_supplies',
}

SERVICE_CONFIDENCE_LABELS = {
    SERVICE_STATUS_ACTIVE: 'verified',
    SERVICE_STATUS_LOCKED: 'inferred',
    SERVICE_STATUS_UNKNOWN: 'unknown',
}
