"""Economy stack scoring v2.

This module scores the identity of the resulting economy stack without trying
to optimise the build.  It consumes the already-simulated economy percentages
and returns explainable archetype fit, purity, contamination, and warnings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edfinder_api.domain.colonisation_rules import get_target_profile
from edfinder_api.mechanics.economy_rules import (
    BROAD_STACK_ALLOWED_COUNT,
    BROAD_STACK_PENALTY_PER_ECONOMY,
    BROAD_STACK_THRESHOLD,
    ECONOMY_STACK_FIT_SCORES,
    ECONOMY_STACK_SCORE_WEIGHTS,
    ELW_REFINERY_INDUSTRIAL_PENALTY,
    LOW_PURITY_SPECIALISED_PENALTY,
    LOW_PURITY_THRESHOLD,
    MIXED_TOLERANT_ARCHETYPES,
    PURITY_BASE_SCORE,
    TERTIARY_HEAVY_PENALTY,
    TERTIARY_HIGH_RISK_THRESHOLD,
    TERTIARY_HEAVY_PRESSURE_THRESHOLD,
    TERTIARY_LIGHT_PENALTY,
    TERTIARY_PRESSURE_THRESHOLD,
)


@dataclass(frozen=True)
class EconomyStackResult:
    top_two: list[str]
    tertiary: list[dict[str, float]]
    purity_score: float
    archetype_fit_score: float
    contamination_risk: str
    compatibility_risk: str
    alignment: str
    warnings: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return round(
            self.purity_score * ECONOMY_STACK_SCORE_WEIGHTS['purity']
            + self.archetype_fit_score * ECONOMY_STACK_SCORE_WEIGHTS['archetype_fit'],
            1,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'top_two': self.top_two,
            'tertiary': self.tertiary,
            'purity_score': round(self.purity_score, 1),
            'archetype_fit_score': round(self.archetype_fit_score, 1),
            'contamination_risk': self.contamination_risk,
            'compatibility_risk': self.compatibility_risk,
            'warnings': self.warnings,
            'strengths': self.strengths,
        }


def analyse_economy_stack(
    composition: dict[str, float],
    target_archetype: str,
    inherited_profiles: list[Any] | None = None,
) -> EconomyStackResult:
    ordered = sorted(composition, key=composition.get, reverse=True)
    top_two = ordered[:2]
    tertiary = [
        {'economy': economy, 'value': round(float(composition[economy]), 1)}
        for economy in ordered[2:5]
    ]
    warnings: list[str] = []
    strengths: list[str] = []
    recommendations: list[str] = []

    if not composition:
        return EconomyStackResult(
            top_two=[],
            tertiary=[],
            purity_score=0.0,
            archetype_fit_score=0.0,
            contamination_risk='unknown',
            compatibility_risk='unknown',
            alignment='none',
            warnings=['No economy-producing facilities are present in the proposed build.'],
            recommendations=['Add economy-producing ports or support facilities before judging the build.'],
        )

    target = get_target_profile(target_archetype)
    expected = target.expected_economies
    archetype_fit, alignment = _archetype_fit(top_two, expected)
    if alignment == 'excellent':
        strengths.append('Target economies are protected in the top two economy slots.')
    elif alignment == 'good':
        strengths.append('Target economies are both in the top two, but primary and secondary are flipped.')
        recommendations.append('Place or reinforce the intended primary economy earlier in the build.')
    elif alignment == 'third_place':
        warnings.append('A target economy is stuck in third place and may not define the colony identity.')
        recommendations.append('Add another matching support or move target support earlier.')
    elif alignment in {'partial', 'poor'} and expected:
        warnings.append('The top economy stack does not protect the requested archetype pair.')

    purity_score, contamination_risk, contamination_warnings = _purity(
        composition,
        ordered,
        expected,
        target_archetype,
        inherited_profiles or [],
    )
    warnings.extend(contamination_warnings)
    compatibility_risk = _compatibility_risk(top_two, expected, ordered)

    return EconomyStackResult(
        top_two=top_two,
        tertiary=tertiary,
        purity_score=purity_score,
        archetype_fit_score=archetype_fit,
        contamination_risk=contamination_risk,
        compatibility_risk=compatibility_risk,
        alignment=alignment,
        warnings=_unique(warnings),
        strengths=_unique(strengths),
        recommendations=_unique(recommendations),
    )


def _archetype_fit(top_two: list[str], expected: list[str]) -> tuple[float, str]:
    if not expected:
        return ECONOMY_STACK_FIT_SCORES['flexible'], 'flexible'
    if len(expected) == 1:
        if top_two[:1] == expected:
            return ECONOMY_STACK_FIT_SCORES['single_excellent'], 'excellent'
        if expected[0] in top_two:
            return ECONOMY_STACK_FIT_SCORES['single_partial'], 'partial'
        return ECONOMY_STACK_FIT_SCORES['single_poor'], 'poor'
    if top_two == expected[:2]:
        return ECONOMY_STACK_FIT_SCORES['pair_excellent'], 'excellent'
    if set(top_two) == set(expected[:2]):
        return ECONOMY_STACK_FIT_SCORES['pair_good_flipped'], 'good'
    if any(economy in top_two for economy in expected[:2]):
        return ECONOMY_STACK_FIT_SCORES['pair_partial'], 'partial'
    return ECONOMY_STACK_FIT_SCORES['pair_poor'], 'poor'


def _purity(
    composition: dict[str, float],
    ordered: list[str],
    expected: list[str],
    target_archetype: str,
    inherited_profiles: list[Any],
) -> tuple[float, str, list[str]]:
    warnings: list[str] = []
    score = PURITY_BASE_SCORE
    tertiary = ordered[2] if len(ordered) > 2 else None
    tertiary_value = composition.get(tertiary, 0.0) if tertiary else 0.0
    non_target_tertiary = bool(
        tertiary and expected and tertiary not in expected and tertiary_value >= TERTIARY_PRESSURE_THRESHOLD
    )
    broad_count = len([value for value in composition.values() if value >= BROAD_STACK_THRESHOLD])
    broad_penalty = (
        0.0 if target_archetype in MIXED_TOLERANT_ARCHETYPES
        else max(0, broad_count - BROAD_STACK_ALLOWED_COUNT) * BROAD_STACK_PENALTY_PER_ECONOMY
    )
    low_purity = [
        profile for profile in inherited_profiles
        if float(getattr(profile, 'purity', 1.0)) < LOW_PURITY_THRESHOLD
    ]
    elw_broad = any('elw_mixed' in getattr(profile, 'strategic_tags', []) for profile in inherited_profiles)

    if non_target_tertiary:
        penalty = TERTIARY_LIGHT_PENALTY if tertiary_value < TERTIARY_HEAVY_PRESSURE_THRESHOLD else TERTIARY_HEAVY_PENALTY
        score -= penalty
        warnings.append(f'{tertiary} is strong enough to pressure the target pair as a tertiary economy.')
    if broad_penalty:
        score -= broad_penalty
        warnings.append('Broad-spectrum mixed inheritance is diluting a specialised economy stack.')
    if low_purity and target_archetype not in MIXED_TOLERANT_ARCHETYPES:
        score -= LOW_PURITY_SPECIALISED_PENALTY
        warnings.append('Low-purity body inheritance may reduce economy stack stability.')
    if elw_broad and target_archetype == 'refinery_industrial':
        score -= ELW_REFINERY_INDUSTRIAL_PENALTY
        warnings.append('ELW broad-stack pressure is a poor fit for specialised Refinery / Industrial identity.')

    if (
        tertiary_value >= TERTIARY_HIGH_RISK_THRESHOLD
        or (broad_penalty >= BROAD_STACK_PENALTY_PER_ECONOMY * 2)
        or (low_purity and target_archetype not in MIXED_TOLERANT_ARCHETYPES)
    ):
        risk = 'high'
    elif tertiary_value >= TERTIARY_PRESSURE_THRESHOLD or broad_penalty or low_purity:
        risk = 'medium'
    else:
        risk = 'low'
    return max(0.0, min(100.0, score)), risk, warnings


def _compatibility_risk(top_two: list[str], expected: list[str], ordered: list[str]) -> str:
    if not expected:
        return 'low'
    if top_two == expected[:len(top_two)]:
        return 'low'
    if set(top_two) == set(expected[:2]):
        return 'medium'
    if any(item in ordered[2:4] for item in expected):
        return 'high'
    if any(item in top_two for item in expected):
        return 'medium'
    return 'high'


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
