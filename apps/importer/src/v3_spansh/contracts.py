"""Locked V3 Spansh conversion and COPY contracts.

Every source-to-canonical physical conversion lives here.  In particular,
Spansh economy numbers are intentionally absent: there is no calibrated
mapping from that source scale to ``economy_weight``.
"""

from __future__ import annotations

import hashlib
import math
from typing import Final

KM_PER_AU: Final[float] = 149_597_870.7
KM_PER_SOLAR_RADIUS: Final[float] = 695_700.0
EARTH_GRAVITY_M_S2: Final[float] = 9.80665
PASCALS_PER_ATMOSPHERE: Final[float] = 101_325.0
YEARS_PER_MYR: Final[float] = 1_000_000.0
GRID_EDGE_LY: Final[float] = 10.0

IMPORTER_VERSION: Final[str] = "v3-spansh-4c-correction-gate.1"
NORMALIZER_VERSION: Final[str] = "v3-spansh-normalizer-4c-correction-gate.1"
SELECTOR_VERSION: Final[str] = "v3-spansh-representative-prefix-1"
EVENT_CONTRACT_VERSION: Final[str] = "v3.canonical-generation.published/1"


COPY_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "systems": (
        "id64", "name", "x_ly", "y_ly", "z_ly", "source_body_count",
        "loaded_body_count", "galaxy_region_id", "grid_x", "grid_y", "grid_z",
        "macro_grid_key", "source_id", "source_run_id", "source_updated_at",
        "freshness_checked_at", "lifecycle_state", "last_present_run_id",
        "last_present_at", "first_missing_run_id", "first_missing_at",
        "last_missing_run_id", "retired_at", "retired_reason",
    ),
    "bodies": (
        "body_pk", "system_id64", "source_body_id64", "frontier_body_id",
        "direct_parent_body_pk", "name", "body_type_id",
        "atmosphere_classification_id", "terraforming_state_id",
        "volcanism_type_id", "is_landable", "is_tidally_locked", "is_main_star",
        "spectral_class", "luminosity_class", "absolute_magnitude", "radius_km",
        "planet_mass_earth", "stellar_mass_solar", "surface_gravity_g",
        "surface_pressure_atm", "surface_temperature_k", "semi_major_axis_km",
        "orbital_period_days", "rotation_period_days", "eccentricity",
        "orbital_inclination_deg", "ascending_node_deg",
        "argument_of_periapsis_deg", "mean_anomaly_deg", "axial_tilt_rad",
        "distance_from_arrival_ls", "stellar_age_myr", "signals_complete",
        "genera_complete", "source_id", "source_run_id", "source_updated_at",
        "freshness_checked_at", "lifecycle_state", "last_present_run_id",
        "last_present_at", "first_missing_run_id", "first_missing_at",
        "last_missing_run_id", "retired_at", "retired_reason",
    ),
    "body_composition": (
        "body_pk", "ice_pct", "metal_pct", "rock_pct", "materials",
        "materials_complete", "atmosphere", "atmosphere_complete",
        "source_run_id", "observed_at",
    ),
    "body_alias": (
        "body_pk", "identity_namespace_id", "external_id", "source_id",
        "source_run_id", "observed_at",
    ),
    "body_parent_evidence": (
        "body_pk", "system_id64", "source_run_id", "source_parent_ordinal",
        "raw_parent_label", "raw_parent_id", "resolution_state",
        "resolved_parent_body_pk", "detail",
    ),
    "rings": (
        "ring_pk", "body_pk", "system_id64", "source_ring_id64", "kind",
        "name", "ring_type_id", "reserve_type_id", "inner_radius_km",
        "outer_radius_km", "mass_mt", "signals_complete", "source_id",
        "source_run_id", "source_updated_at", "freshness_checked_at",
        "lifecycle_state", "last_present_run_id", "last_present_at",
        "first_missing_run_id", "first_missing_at", "last_missing_run_id",
        "retired_at", "retired_reason",
    ),
    "body_signal_current": (
        "body_pk", "signal_type_id", "signal_count", "source_run_id", "observed_at",
    ),
    "body_genus_current": (
        "body_pk", "genus_id", "source_run_id", "observed_at",
    ),
    "ring_signal_current": (
        "ring_pk", "signal_type_id", "signal_count", "source_run_id", "observed_at",
    ),
    "stations": (
        "station_pk", "market_id", "system_id64", "name", "station_type_id",
        "distance_from_arrival_ls", "services_complete", "economies_complete",
        "is_fleet_carrier", "source_id", "source_run_id", "source_updated_at",
        "freshness_checked_at", "lifecycle_state", "last_present_run_id",
        "last_present_at", "first_missing_run_id", "first_missing_at",
        "last_missing_run_id", "retired_at", "retired_reason",
    ),
    "station_alias": (
        "station_pk", "identity_namespace_id", "external_id", "source_id",
        "source_run_id", "observed_at",
    ),
    "station_placement": (
        "station_pk", "system_id64", "body_pk", "latitude_deg", "longitude_deg",
        "association_state", "source_run_id", "observed_at", "evidence_detail",
    ),
    "station_service_current": (
        "station_pk", "station_service_id", "source_run_id", "observed_at",
    ),
    "station_economy_current": (
        "station_pk", "economy_id", "economy_weight", "is_primary",
        "is_secondary", "source_run_id", "observed_at",
    ),
}


def finite_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def stable_int63(*parts: object) -> int:
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8")
    value = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") & ((1 << 63) - 1)
    return value or 1


def macro_grid_key(grid_x: int, grid_y: int, grid_z: int) -> int:
    return stable_int63("macro-grid-v1", grid_x, grid_y, grid_z)
