"""Archetype-aware regional fit scoring.

The scores here deliberately do not collapse into one universal "near is good"
metric.  Different colony designs want different regional positions.
"""
from __future__ import annotations

from typing import Any, Optional

from edfinder_api.mechanics.regional_rules import (
    REGIONAL_ARCHETYPE_FORMULAS,
    REGIONAL_ARCHETYPE_ROLE_BONUSES,
    REGIONAL_FIT_LABEL_THRESHOLDS,
    REGIONAL_SCORE_WEIGHTS,
)


ARCHETYPES = [
    'refinery_industrial',
    'extraction_refinery',
    'agriculture_terraforming',
    'hitech_tourism',
    'expansion_capital',
    'flexible_multirole',
]


def regional_scores(
    *,
    nearest_distance_ly: Optional[float],
    within_25: int,
    within_50: int,
    within_100: int,
    within_250: int,
) -> dict[str, float]:
    if nearest_distance_ly is None:
        return {'isolation': 0.0, 'density': 0.0, 'expansion': 0.0, 'competition': 0.0}
    w = REGIONAL_SCORE_WEIGHTS
    isolation = _clamp((nearest_distance_ly / w['isolation_distance_scale']) * 100.0)
    density = _clamp(
        (within_25 * w['density_within_25'])
        + (within_50 * w['density_within_50'])
        + (within_100 * w['density_within_100'])
        + (within_250 * w['density_within_250'])
    )
    competition = _clamp(
        (within_25 * w['competition_within_25'])
        + (within_50 * w['competition_within_50'])
        + max(0, within_100 - w['competition_within_100_free']) * w['competition_extra_within_100']
    )
    expansion = _clamp(
        100.0
        - abs(nearest_distance_ly - w['expansion_target_distance']) * w['expansion_distance_penalty']
        - max(0, within_50 - w['expansion_dense_50_free']) * w['expansion_dense_50_penalty']
    )
    return {
        'isolation': round(isolation, 1),
        'density': round(density, 1),
        'expansion': round(expansion, 1),
        'competition': round(competition, 1),
    }


def archetype_regional_fit(
    *,
    regional_role: str,
    nearest_distance_ly: Optional[float],
    scores: dict[str, float],
    within_50: int,
    within_100: int,
) -> dict[str, float]:
    if nearest_distance_ly is None:
        return {key: 0.0 for key in ARCHETYPES}
    isolation = scores['isolation']
    density = scores['density']
    expansion = scores['expansion']
    competition = scores['competition']
    f = REGIONAL_ARCHETYPE_FORMULAS
    bonuses = REGIONAL_ARCHETYPE_ROLE_BONUSES

    fit = {
        'expansion_capital': _clamp(expansion + _role_bonus(regional_role, bonuses['expansion_capital'])),
        'extraction_refinery': _clamp(
            isolation * f['extraction_isolation_weight']
            + (100 - competition) * f['extraction_low_competition_weight']
            + _role_bonus(regional_role, bonuses['extraction_refinery'])
        ),
        'refinery_industrial': _clamp(
            f['refinery_base_score']
            - abs(nearest_distance_ly - f['refinery_target_distance']) * f['refinery_distance_penalty']
            + (100 - competition) * f['refinery_low_competition_weight']
            + _role_bonus(regional_role, bonuses['refinery_industrial'])
        ),
        'hitech_tourism': _clamp(
            density * f['hitech_density_weight']
            + (100 - isolation) * f['hitech_access_weight']
            + _role_bonus(regional_role, bonuses['hitech_tourism'])
        ),
        'agriculture_terraforming': _clamp(
            f['agriculture_base_score']
            - abs(nearest_distance_ly - f['agriculture_target_distance']) * f['agriculture_distance_penalty']
            - max(0, within_50 - f['agriculture_dense_50_free']) * f['agriculture_dense_50_penalty']
            + _role_bonus(regional_role, bonuses['agriculture_terraforming'])
        ),
        'flexible_multirole': _clamp(
            f['flexible_base_score']
            - abs(scores['density'] - f['flexible_density_target']) * f['flexible_density_penalty']
            + _role_bonus(regional_role, bonuses['flexible_multirole'])
        ),
    }
    return {key: round(value, 1) for key, value in fit.items()}


def regional_rationale(
    *,
    regional_role: str,
    nearest_distance_ly: Optional[float],
    within_100: int,
    fit: dict[str, float],
) -> dict[str, Any]:
    if nearest_distance_ly is None:
        return {
            'summary': 'Regional position is unknown because system coordinates or colonised-neighbour data are missing.',
            'strengths': [],
            'warnings': ['Regional analysis could not be computed for this system.'],
            'archetype_notes': {},
        }
    best = max(fit, key=fit.get) if fit else 'flexible_multirole'
    strengths = [f'Regional role classified as {regional_role.replace("_", " ")}.']
    warnings: list[str] = []
    if regional_role == 'oversaturated_region':
        warnings.append('High nearby colony density may create competition for specialised expansion roles.')
    if regional_role == 'isolated_frontier':
        warnings.append('Extreme isolation helps frontier dominance but may hurt tourism and logistics access.')
    return {
        'summary': (
            f'Nearest colonised system is {nearest_distance_ly:.1f} LY away with {within_100} colonised systems '
            f'within 100 LY; strongest regional fit is {best.replace("_", " ")}.'
        ),
        'strengths': strengths,
        'warnings': warnings,
        'archetype_notes': {
            key: _fit_note(value)
            for key, value in fit.items()
        },
    }


def _fit_note(score: float) -> str:
    thresholds = REGIONAL_FIT_LABEL_THRESHOLDS
    if score >= thresholds['excellent']:
        return 'excellent regional fit'
    if score >= thresholds['good']:
        return 'good regional fit'
    if score >= thresholds['mixed']:
        return 'mixed regional fit'
    return 'weak regional fit'


def _role_bonus(role: str, values: dict[str, float]) -> float:
    return values.get(role, 0.0)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))
