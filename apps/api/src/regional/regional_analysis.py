"""Compute raw regional positioning metrics for systems."""
from __future__ import annotations

import math
from typing import Any, Optional

from edfinder_api.mechanics.confidence import (
    ConfidenceLevel,
    ConfidenceSignal,
    default_data_quality,
    signals_to_dict,
)
from edfinder_api.mechanics.regional_rules import REGIONAL_DISTANCE_BUCKETS
from edfinder_api.mechanics.versions import MECHANICS_VERSION
from edfinder_api.regional.regional_roles import classify_regional_role
from edfinder_api.regional.regional_scoring import (
    archetype_regional_fit,
    regional_rationale,
    regional_scores,
)

COLONISATION_CLAIM_RANGE_LY = 16.0
REGIONAL_ANALYSIS_RADIUS_LY = float(REGIONAL_DISTANCE_BUCKETS[-1])


def distance_ly(a: dict[str, Any], b: dict[str, Any]) -> float:
    return math.sqrt(
        (float(a['x']) - float(b['x'])) ** 2
        + (float(a['y']) - float(b['y'])) ** 2
        + (float(a['z']) - float(b['z'])) ** 2
    )


def is_colonised_system(row: dict[str, Any]) -> bool:
    return bool(
        row.get('is_colonised')
        or row.get('is_being_colonised')
        or int(row.get('population') or 0) > 0
        or int(row.get('station_count') or 0) > 0
    )


def compute_regional_analysis(
    system: dict[str, Any],
    candidate_systems: list[dict[str, Any]],
) -> dict[str, Any]:
    if not _has_coordinates(system):
        return _unknown(system.get('id64'))

    colonised = [
        row for row in candidate_systems
        if row.get('id64') != system.get('id64') and _has_coordinates(row) and is_colonised_system(row)
    ]
    if not colonised:
        return _unknown(system.get('id64'))

    distances = sorted(
        ((distance_ly(system, row), row) for row in colonised),
        key=lambda item: item[0],
    )
    nearest_distance, nearest = distances[0]
    bucket_25, bucket_50, bucket_100, bucket_250 = REGIONAL_DISTANCE_BUCKETS
    within_25 = sum(1 for distance, _row in distances if distance <= bucket_25)
    within_50 = sum(1 for distance, _row in distances if distance <= bucket_50)
    within_100 = sum(1 for distance, _row in distances if distance <= bucket_100)
    within_250 = sum(1 for distance, _row in distances if distance <= bucket_250)
    role = classify_regional_role(
        nearest_distance_ly=nearest_distance,
        within_25=within_25,
        within_50=within_50,
        within_100=within_100,
        within_250=within_250,
    )
    scores = regional_scores(
        nearest_distance_ly=nearest_distance,
        within_25=within_25,
        within_50=within_50,
        within_100=within_100,
        within_250=within_250,
    )
    fit = archetype_regional_fit(
        regional_role=role,
        nearest_distance_ly=nearest_distance,
        scores=scores,
        within_50=within_50,
        within_100=within_100,
    )
    return {
        'system_id64': system.get('id64'),
        'nearest_colonised_system_id64': nearest.get('id64'),
        'nearest_colonised_system_name': nearest.get('name'),
        'nearest_colonised_system_distance_ly': round(nearest_distance, 2),
        'colonised_within_25ly': within_25,
        'colonised_within_50ly': within_50,
        'colonised_within_100ly': within_100,
        'colonised_within_250ly': within_250,
        'regional_isolation_score': scores['isolation'],
        'regional_density_score': scores['density'],
        'regional_expansion_score': scores['expansion'],
        'regional_competition_score': scores['competition'],
        'regional_role': role,
        'archetype_regional_fit': fit,
        'rationale': regional_rationale(
            regional_role=role,
            nearest_distance_ly=nearest_distance,
            within_100=within_100,
            fit=fit,
        ),
        'data_source': 'computed',
    }


def response_from_row(row: dict[str, Any] | None, system_id64: int) -> dict[str, Any]:
    if not row:
        return {
            'system_id64': system_id64,
            'mechanics_version': MECHANICS_VERSION,
            'claim_range_ly': COLONISATION_CLAIM_RANGE_LY,
            'analysis_radius_ly': REGIONAL_ANALYSIS_RADIUS_LY,
            'nearest_colonised_system': None,
            'counts': {'within_25ly': 0, 'within_50ly': 0, 'within_100ly': 0, 'within_250ly': 0},
            'scores': {'isolation': 0.0, 'density': 0.0, 'expansion': 0.0, 'competition': 0.0},
            'regional_role': 'unknown',
            'archetype_regional_fit': {},
            'rationale': {
                'summary': 'Regional analysis has not been computed for this system yet.',
                'strengths': [],
                'warnings': ['Run build_regional_analysis.py to populate regional positioning data.'],
                'archetype_notes': {},
            },
            'data_quality': default_data_quality(has_regional_context=False),
            'confidence_signals': signals_to_dict([ConfidenceSignal(
                area='regional_position',
                level=ConfidenceLevel.UNKNOWN,
                reason='Regional analysis has not been computed for this system yet.',
            )]),
            'computed_at': None,
        }
    return {
        'system_id64': system_id64,
        'mechanics_version': MECHANICS_VERSION,
        'claim_range_ly': COLONISATION_CLAIM_RANGE_LY,
        'analysis_radius_ly': REGIONAL_ANALYSIS_RADIUS_LY,
        'nearest_colonised_system': {
            'id64': row.get('nearest_colonised_system_id64'),
            'name': row.get('nearest_colonised_system_name'),
            'distance_ly': row.get('nearest_colonised_system_distance_ly'),
        } if row.get('nearest_colonised_system_id64') else None,
        'counts': {
            'within_25ly': row.get('colonised_within_25ly') or 0,
            'within_50ly': row.get('colonised_within_50ly') or 0,
            'within_100ly': row.get('colonised_within_100ly') or 0,
            'within_250ly': row.get('colonised_within_250ly') or 0,
        },
        'scores': {
            'isolation': row.get('regional_isolation_score') or 0.0,
            'density': row.get('regional_density_score') or 0.0,
            'expansion': row.get('regional_expansion_score') or 0.0,
            'competition': row.get('regional_competition_score') or 0.0,
        },
        'regional_role': row.get('regional_role') or 'unknown',
        'archetype_regional_fit': row.get('archetype_regional_fit') or {},
        'rationale': row.get('rationale') or {},
        'data_quality': default_data_quality(has_regional_context=True),
        'confidence_signals': signals_to_dict([ConfidenceSignal(
            area='regional_position',
            level=ConfidenceLevel.INFERRED,
            reason='Regional metrics are inferred from stored system coordinates and colonised-neighbour counts.',
        )]),
        'computed_at': row.get('computed_at'),
    }


def _has_coordinates(row: dict[str, Any]) -> bool:
    return row.get('x') is not None and row.get('y') is not None and row.get('z') is not None


def _unknown(system_id64: Optional[int]) -> dict[str, Any]:
    return {
        'system_id64': system_id64,
        'nearest_colonised_system_id64': None,
        'nearest_colonised_system_name': None,
        'nearest_colonised_system_distance_ly': None,
        'colonised_within_25ly': 0,
        'colonised_within_50ly': 0,
        'colonised_within_100ly': 0,
        'colonised_within_250ly': 0,
        'regional_isolation_score': 0.0,
        'regional_density_score': 0.0,
        'regional_expansion_score': 0.0,
        'regional_competition_score': 0.0,
        'regional_role': 'unknown',
        'archetype_regional_fit': {},
        'rationale': regional_rationale(
            regional_role='unknown',
            nearest_distance_ly=None,
            within_100=0,
            fit={},
        ),
        'data_source': 'computed',
    }
