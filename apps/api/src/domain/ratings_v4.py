from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import isfinite
import re
from typing import Iterable, Mapping


ECONOMIES = (
    'Agriculture', 'Refinery', 'Industrial', 'HighTech',
    'Military', 'Tourism', 'Extraction',
)

SCORER_VERSION = '4.0.0'
MECHANICS_VERSION = 'v4-mechanics-2026-09'

NATIVE_BASE = 75
MODIFIER_BASE = 55
NATIVE_AND_MODIFIER_BASE = 85
STRONG_LINK_STEP = 10
STRONG_LINK_CAP = 25
SYSTEM_WEIGHT_HUNDREDTHS = (82, 11, 5, 2)
SYSTEM_WEIGHTS = tuple(weight / 100 for weight in SYSTEM_WEIGHT_HUNDREDTHS)
ROUNDING_POLICY = 'nearest-even from exact integer hundredths'
REFINERY_GROUND_RULE = 'refinery-usable-ground-opportunity'


@dataclass(frozen=True)
class EvidenceFeature:
    feature_type: str
    known: bool
    weight: float
    confidence: float
    provenance: str | None = None
    value: str | bool | None = None


@dataclass(frozen=True)
class SpecialisationConstraint:
    rule_id: str
    satisfied: bool | None
    feature_type: str
    confidence: float = 1.0
    provenance: str | None = None


@dataclass(frozen=True)
class CandidateSpecialisation:
    candidate_id: str
    intrinsic_quality: int
    quality: int | None
    minimum_quality: int
    maximum_quality: int
    competing_economies: tuple[str, ...]
    preferred_specialisation: bool
    constraints: tuple[SpecialisationConstraint, ...]


@dataclass(frozen=True)
class Opportunity:
    economy: str
    candidate_id: str
    native: bool = False
    modifier: bool = False
    strong_positive_rules: tuple[str, ...] = ()
    strong_negative_rules: tuple[str, ...] = ()
    competing_economies: tuple[str, ...] = ()
    preferred_specialisation: bool = False
    completeness: float = 1.0
    confidence: float = 1.0
    contributors: tuple[str, ...] = ()
    evidence: tuple[EvidenceFeature, ...] = ()
    specialisation_constraints: tuple[SpecialisationConstraint, ...] = ()


@dataclass(frozen=True)
class CandidateContribution:
    candidate_id: str
    native: bool
    modifier: bool
    base_score: int
    positive_rules: tuple[str, ...]
    negative_rules: tuple[str, ...]
    positive_adjustment: int
    negative_adjustment: int
    local_score: int
    system_weight: float
    contributors: tuple[str, ...]
    evidence: tuple[EvidenceFeature, ...]


@dataclass(frozen=True)
class EconomyRating:
    economy: str
    potential_score: int
    specialisation_quality: int | None
    evidence_completeness: float
    confidence: float
    best_candidate_id: str | None
    local_scores: tuple[int, ...] = ()
    explanation: tuple[str, ...] = field(default_factory=tuple)
    mechanics_version: str = MECHANICS_VERSION
    scorer_version: str = SCORER_VERSION
    contributions: tuple[CandidateContribution, ...] = ()
    specialisation_quality_min: int = 0
    specialisation_quality_max: int = 0
    best_specialisation_candidate_id: str | None = None
    specialisation_candidates: tuple[CandidateSpecialisation, ...] = ()


@dataclass(frozen=True)
class BodyFact:
    candidate_id: str
    body_class: str
    rings: bool | None = None
    biologicals: bool | None = None
    geologicals: bool | None = None
    volcanism: bool | None = None
    terraformable: bool | None = None
    tidally_locked: bool | None = None
    completeness: float = 1.0
    confidence: float = 1.0
    spectral_class: str | None = None
    is_main_star: bool | None = None
    feature_confidence: Mapping[str, float] = field(default_factory=dict)
    feature_provenance: Mapping[str, str] = field(default_factory=dict)
    usable_ground_opportunity: bool | None = None
    luminosity_class: str | None = None
    reserve_level: str | None = None
    # Canonical adapters must use body scope even when its reserve is unknown.
    # The default preserves the historical synthetic system-reserve interface.
    reserve_scope: str = 'synthetic_system'


@dataclass(frozen=True)
class SystemFacts:
    bodies: tuple[BodyFact, ...]
    reserve_level: str | None = None
    exotic_star: str | None = None
    body_inventory_complete: bool = False
    feature_confidence: Mapping[str, float] = field(default_factory=dict)
    feature_provenance: Mapping[str, str] = field(default_factory=dict)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _local_components(opportunity: Opportunity) -> tuple[int, int, int]:
    if opportunity.native and opportunity.modifier:
        base = NATIVE_AND_MODIFIER_BASE
    elif opportunity.native:
        base = NATIVE_BASE
    elif opportunity.modifier:
        base = MODIFIER_BASE
    else:
        return (0, 0, 0)

    positives = len(set(opportunity.strong_positive_rules)) * STRONG_LINK_STEP
    negatives = len(set(opportunity.strong_negative_rules)) * STRONG_LINK_STEP
    positives = min(positives, STRONG_LINK_CAP)
    negatives = min(negatives, STRONG_LINK_CAP)
    return base, positives, negatives


def local_opportunity_score(opportunity: Opportunity) -> int:
    base, positives, negatives = _local_components(opportunity)
    return round(_clamp(base + positives - negatives, 0, 100))


def _intrinsic_specialisation(opportunity: Opportunity) -> int:
    if not (opportunity.native or opportunity.modifier):
        return 0
    competitors = len(set(opportunity.competing_economies) - {opportunity.economy})
    if competitors == 0:
        penalty = 0
    elif competitors == 1:
        penalty = 8
    elif competitors == 2:
        penalty = 20
    else:
        penalty = 35
    bonus = 5 if opportunity.preferred_specialisation else 0
    return round(_clamp(100 - penalty + bonus, 0, 100))


def _specialisation_constraints(opportunity: Opportunity) -> tuple[SpecialisationConstraint, ...]:
    constraints: dict[str, SpecialisationConstraint] = {}
    for constraint in opportunity.specialisation_constraints:
        if constraint.satisfied is not None and type(constraint.satisfied) is not bool:
            raise ValueError('constraint state must be True, False or None')
        _ratio(constraint.confidence)
        if constraint.rule_id in constraints and constraints[constraint.rule_id] != constraint:
            raise ValueError(f'conflicting specialisation constraint: {constraint.rule_id}')
        constraints[constraint.rule_id] = constraint
    if opportunity.economy == 'Refinery':
        constraints.setdefault(REFINERY_GROUND_RULE, SpecialisationConstraint(
            REFINERY_GROUND_RULE, None, 'usable_ground_opportunity',
        ))
    return tuple(constraints[key] for key in sorted(constraints))


def _candidate_specialisation(opportunity: Opportunity) -> CandidateSpecialisation:
    intrinsic = _intrinsic_specialisation(opportunity)
    constraints = _specialisation_constraints(opportunity)
    blocked = any(constraint.satisfied is False for constraint in constraints)
    unknown = any(constraint.satisfied is None for constraint in constraints)
    minimum = 0 if blocked or unknown else intrinsic
    maximum = 0 if blocked else intrinsic
    return CandidateSpecialisation(
        candidate_id=opportunity.candidate_id,
        intrinsic_quality=intrinsic,
        quality=minimum if minimum == maximum else None,
        minimum_quality=minimum,
        maximum_quality=maximum,
        competing_economies=tuple(sorted(set(opportunity.competing_economies) - {opportunity.economy})),
        preferred_specialisation=opportunity.preferred_specialisation,
        constraints=constraints,
    )


def candidate_specialisation(opportunity: Opportunity) -> int | None:
    return _candidate_specialisation(opportunity).quality


def _system_specialisation(
    candidates: tuple[CandidateSpecialisation, ...], *, optimistic: bool,
) -> tuple[int, str | None]:
    def quality(candidate: CandidateSpecialisation) -> int:
        return candidate.maximum_quality if optimistic else candidate.minimum_quality

    eligible = sorted(
        (candidate for candidate in candidates if quality(candidate) > 0),
        key=lambda candidate: (-quality(candidate), candidate.candidate_id),
    )
    if not eligible:
        return 0, None
    clean_extra = sum(not candidate.competing_economies for candidate in eligible[1:])
    return min(100, quality(eligible[0]) + min(10, 3 * clean_extra)), eligible[0].candidate_id


def _weighted_mean(values: Iterable[tuple[float, float]]) -> float:
    numerator = 0.0
    denominator = 0.0
    for value, weight in values:
        numerator += value * weight
        denominator += weight
    return numerator / denominator if denominator else 0.0


def _round_hundredths(value: int) -> int:
    """Round the exact roll-up to nearest integer, resolving ties to even."""
    whole, remainder = divmod(value, 100)
    return whole + int(remainder > 50 or (remainder == 50 and whole % 2 == 1))


def _ratio(value: float) -> float:
    if not isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f'evidence value must be finite and in 0..1: {value}')
    return value


def _unique_opportunities(opportunities: Iterable[Opportunity]) -> tuple[Opportunity, ...]:
    unique: dict[tuple[str, str], Opportunity] = {}
    for item in opportunities:
        if item.economy not in ECONOMIES:
            raise ValueError(f'unknown economy: {item.economy}')
        _ratio(item.completeness)
        _ratio(item.confidence)
        item = replace(item, specialisation_constraints=_specialisation_constraints(item))
        key = (item.economy, item.candidate_id)
        if key in unique and unique[key] != item:
            raise ValueError(f'conflicting duplicate candidate: {key}')
        unique[key] = item
    return tuple(unique[key] for key in sorted(unique))


def rate_economy(economy: str, opportunities: Iterable[Opportunity]) -> EconomyRating:
    if economy not in ECONOMIES:
        raise ValueError(f'unknown economy: {economy}')

    candidates = [item for item in _unique_opportunities(opportunities) if item.economy == economy]
    if not candidates:
        return EconomyRating(economy, 0, 0, 0.0, 0.0, None)

    scored = sorted(
        ((local_opportunity_score(item), item) for item in candidates if item.native or item.modifier),
        key=lambda pair: (-pair[0], pair[1].candidate_id),
    )
    top = scored[:4]
    potential = _round_hundredths(sum(
        weight * score
        for weight, (score, _) in zip(SYSTEM_WEIGHT_HUNDREDTHS, top, strict=False)
    ))

    specialisation_candidates = tuple(
        _candidate_specialisation(item)
        for item in sorted((item for _, item in scored), key=lambda item: item.candidate_id)
    )
    specialisation_min, confirmed_best = _system_specialisation(specialisation_candidates, optimistic=False)
    specialisation_max, _ = _system_specialisation(specialisation_candidates, optimistic=True)
    specialisation = specialisation_min if specialisation_min == specialisation_max else None

    completeness = _weighted_mean((item.completeness, 1.0) for item in candidates)
    source_confidence = _weighted_mean((item.confidence, 1.0) for item in candidates)
    confidence = min(source_confidence, 0.5 + 0.5 * completeness)

    contributions = []
    explanation = [f'{len(scored)} eligible candidate(s)']
    for weight, (score, item) in zip(SYSTEM_WEIGHTS, top, strict=False):
        base, positives, negatives = _local_components(item)
        positive_rules = tuple(sorted(set(item.strong_positive_rules)))
        negative_rules = tuple(sorted(set(item.strong_negative_rules)))
        contributions.append(CandidateContribution(
            item.candidate_id, item.native, item.modifier, base,
            positive_rules, negative_rules, positives, negatives, score, weight,
            tuple(sorted(set(item.contributors))), item.evidence,
        ))
        explanation.extend((
            f'{item.candidate_id}: base {base}; positive +{positives}; negative -{negatives}; '
            f'local {score}; weight {weight}; weighted contribution {weight * score:.2f}',
            *(f'{item.candidate_id}: {value}' for value in sorted(set(item.contributors))),
            *(f'{item.candidate_id}: +{STRONG_LINK_STEP} {rule} (positive cap {STRONG_LINK_CAP})' for rule in positive_rules),
            *(f'{item.candidate_id}: -{STRONG_LINK_STEP} {rule} (negative cap {STRONG_LINK_CAP})' for rule in negative_rules),
        ))
    if not scored:
        explanation.append('no known eligible opportunity')
    for item in candidates:
        missing = [feature.feature_type for feature in item.evidence if not feature.known]
        if missing:
            explanation.append(f'{item.candidate_id}: unknown evidence: {", ".join(missing)}')
    for candidate in specialisation_candidates:
        for constraint in candidate.constraints:
            state = 'unknown' if constraint.satisfied is None else ('satisfied' if constraint.satisfied else 'blocked')
            explanation.append(
                f'{candidate.candidate_id}: specialisation constraint {constraint.rule_id}: {state}; '
                f'intrinsic {candidate.intrinsic_quality}; quality range '
                f'{candidate.minimum_quality}..{candidate.maximum_quality}'
            )
    if specialisation is None:
        explanation.append(f'specialisation unresolved: {specialisation_min}..{specialisation_max}')

    return EconomyRating(
        economy=economy,
        potential_score=potential,
        specialisation_quality=specialisation,
        evidence_completeness=round(_clamp(completeness, 0, 1), 4),
        confidence=round(_clamp(confidence, 0, 1), 4),
        best_candidate_id=scored[0][1].candidate_id if scored else None,
        local_scores=tuple(score for score, _ in scored),
        explanation=tuple(explanation),
        contributions=tuple(contributions),
        specialisation_quality_min=specialisation_min,
        specialisation_quality_max=specialisation_max,
        best_specialisation_candidate_id=confirmed_best if specialisation is not None else None,
        specialisation_candidates=specialisation_candidates,
    )


def rate_all(opportunities: Iterable[Opportunity]) -> dict[str, EconomyRating]:
    items = _unique_opportunities(opportunities)
    return {economy: rate_economy(economy, items) for economy in ECONOMIES}


def _body_key(value: str) -> str:
    key = ' '.join(value.lower().replace('-', ' ').split())
    aliases = {
        'high metal content body': 'high metal content world',
        'rocky ice world': 'rocky ice body',
        'earthlike body': 'earth like world',
        'earth like body': 'earth like world',
    }
    return aliases.get(key, key)


def _classification(body: BodyFact) -> str:
    key = _body_key(body.body_class)
    # A retained planetary classification cannot be overwritten by unrelated
    # stellar metadata. Broad Star rows are resolved from stellar evidence below.
    if _native_economies(key) and key not in {
        'black hole', 'neutron star', 'white dwarf', 'brown dwarf',
        'main sequence star', 'ordinary star', 'giant star',
    }:
        return key
    spectral = (body.spectral_class or '').strip().upper()
    spectral = re.sub(r'\d.*$', '', spectral).strip()
    if key in {'black hole', 'supermassive black hole'} or spectral in {'H', 'SUPERMASSIVEBLACKHOLE'}:
        return 'black hole'
    if key == 'neutron star' or spectral == 'N':
        return 'neutron star'
    if 'white dwarf' in key or spectral in {'D', 'DA', 'DAB', 'DAO', 'DAZ', 'DAV', 'DB', 'DBZ', 'DBV', 'DO', 'DOV', 'DQ', 'DC', 'DCV', 'DX'}:
        return 'white dwarf'
    if 'brown dwarf' in key or spectral in {'L', 'T', 'Y'}:
        return 'brown dwarf'
    # A main-star flag alone does not distinguish ordinary from exotic stars.
    # Broad canonical Star rows need the retained spectral classification.
    if key == 'main sequence star':
        return 'main sequence star'
    ordinary_spectral = {'O', 'B', 'A', 'F', 'G', 'K', 'M', 'C', 'CN', 'CJ', 'CH', 'CHD', 'S', 'MS', 'W', 'WC', 'WN', 'WNC', 'WO'}
    ordinary_subtype = (
        key.endswith(' star') and key.split(' ', 1)[0].upper() in ordinary_spectral
    ) or 'wolf rayet' in key or 'carbon star' in key
    if ordinary_subtype or spectral in ordinary_spectral or key in {'ordinary star', 'giant star'}:
        luminosity = (body.luminosity_class or '').strip().upper()
        if 'giant' in key or re.fullmatch(r'(?:I|II|III|IV)[AB]*', luminosity):
            return 'giant star'
        if re.fullmatch(r'V[AB]*', luminosity):
            return 'main sequence star'
        # Spectral colour alone does not establish luminosity/evolutionary class.
        return 'ordinary star'
    return key


def _reserve_key(value: str | None) -> str | None:
    key = (value or '').strip().lower().removesuffix('resources').strip()
    return key if key in {'pristine', 'major', 'common', 'low', 'depleted'} else None


def _classification_feature(body: BodyFact) -> str:
    if body.spectral_class and not _native_economies(_classification(replace(body, spectral_class=None))):
        return 'spectral_class'
    return 'body_class'


def _native_economies(body_class: str) -> tuple[str, ...]:
    key = _body_key(body_class)
    if key in {'black hole', 'neutron star', 'white dwarf'}:
        return ('HighTech', 'Tourism')
    if key in {'brown dwarf', 'main sequence star', 'ordinary star', 'giant star'}:
        return ('Military',)
    if key in {'earth like world', 'earthlike world', 'elw'}:
        return ('Agriculture', 'HighTech', 'Military', 'Tourism')
    if key in {'water world', 'ww'}:
        return ('Agriculture', 'Tourism')
    if key in {'ammonia world', 'ammonia'}:
        return ('HighTech', 'Tourism')
    if 'gas giant' in key:
        return ('HighTech', 'Industrial')
    if key in {'high metal content world', 'high metal content', 'hmc', 'metal rich body', 'metal rich'}:
        return ('Extraction',)
    if key in {'rocky ice body', 'rocky ice'}:
        return ('Industrial', 'Refinery')
    if key in {'rocky body', 'rocky'}:
        return ('Refinery',)
    if key in {'icy body', 'icy'}:
        return ('Industrial',)
    return ()


def _preferred(economy: str, body_class: str, body: BodyFact) -> bool:
    key = _body_key(body_class)
    if economy == 'Agriculture':
        return key in {'water world', 'ww'}
    if economy == 'Refinery':
        return key in {'rocky body', 'rocky'} and not any(
            value is True for value in (body.rings, body.biologicals, body.geologicals)
        )
    if economy == 'Industrial':
        return key in {'icy body', 'icy'} and not any(
            value is True for value in (body.rings, body.biologicals, body.geologicals)
        )
    if economy == 'Extraction':
        return key in {'high metal content world', 'high metal content', 'hmc', 'metal rich body', 'metal rich'}
    if economy == 'Military':
        return key in {'brown dwarf', 'main sequence star', 'ordinary star', 'giant star'} and body.rings is not True
    if economy in {'HighTech', 'Tourism'}:
        if economy == 'Tourism' and key in {'water world', 'ww'}:
            return True
        return key in {'ammonia world', 'ammonia', 'black hole', 'neutron star', 'white dwarf'}
    return False


def _evidence_for_economy(
    economy: str, body: BodyFact, facts: SystemFacts,
    candidate_economies: set[str], reserve: str | None, reserve_is_system: bool,
    exotic_known: bool, exotic_value: str | bool | None,
    exotic_confidence: float, exotic_provenance: str | None,
) -> tuple[EvidenceFeature, ...]:
    classification_known = bool(_native_economies(_classification(body)))
    inheritance_fields = {
        'Agriculture': ('biologicals',),
        'Industrial': ('geologicals',),
        'Extraction': ('rings', 'geologicals'),
    }.get(economy, ())
    potentially_eligible = (
        economy in candidate_economies or not classification_known
        or any(getattr(body, name) is None for name in inheritance_fields)
    )
    local_fields = {
        'Agriculture': ('biologicals',),
        'Industrial': ('geologicals',),
        'HighTech': ('biologicals', 'geologicals'),
        'Tourism': ('biologicals', 'geologicals'),
        'Extraction': ('rings', 'geologicals', 'volcanism'),
    }.get(economy, ()) if potentially_eligible else inheritance_fields
    evidence = []

    def add(name: str, known: bool, weight: float, system: bool = False) -> None:
        source = facts if system else body
        fallback = 1.0 if system else body.confidence
        if name in {'body_class', 'spectral_class'}:
            value = _classification(body) if known else None
        elif name == 'body_inventory':
            value = True if known else None
        elif name == 'reserve_level':
            value = reserve
        elif name == 'provenance':
            value = source.feature_provenance.get(name)
        else:
            value = getattr(source, name)
        evidence.append(EvidenceFeature(
            name, known, weight,
            _ratio(source.feature_confidence.get(name, fallback)),
            source.feature_provenance.get(name),
            value,
        ))

    # Identity family (0.35) includes both classification and catalogue coverage.
    uses_luminosity = body.luminosity_class is not None and _classification(body) in {
        'ordinary star', 'giant star', 'main sequence star',
    }
    add(_classification_feature(body), classification_known, 0.0875 if uses_luminosity else 0.175)
    if uses_luminosity:
        add('luminosity_class', True, 0.0875)
    add('body_inventory', facts.body_inventory_complete, 0.175, system=True)
    for name in local_fields:
        add(name, getattr(body, name) is not None, 0.25 / len(local_fields))
    if potentially_eligible and economy in {'Extraction', 'Industrial', 'Refinery'}:
        add('reserve_level', reserve is not None, 0.20, system=reserve_is_system)
    if potentially_eligible and economy == 'Tourism':
        evidence.append(EvidenceFeature(
            'exotic_star', exotic_known, 0.20, exotic_confidence, exotic_provenance, exotic_value,
        ))
    if potentially_eligible and economy == 'Agriculture':
        for name in ('terraformable', 'tidally_locked'):
            add(name, getattr(body, name) is not None, 0.05)
    add('provenance', bool(body.feature_provenance.get('provenance', '').strip()), 0.10)
    return tuple(evidence)


def opportunities_from_facts(facts: SystemFacts) -> tuple[Opportunity, ...]:
    result: list[Opportunity] = []
    exotic_types = {'black hole', 'neutron star', 'white dwarf'}
    classifications = {_classification(body) for body in facts.bodies}
    exotics = classifications & exotic_types
    explicit_exotic = _body_key(facts.exotic_star or '')
    if explicit_exotic in exotic_types:
        exotics.add(explicit_exotic)
    exotic_known = bool(exotics) or (
        facts.body_inventory_complete
        and all(_native_economies(key) for key in classifications)
    )
    exotic_value = ','.join(sorted(exotics)) if exotics else (False if exotic_known else None)
    exotic_sources = [
        body for body in facts.bodies
        if _classification(body) in exotic_types or not exotics
    ]
    exotic_confidence = _ratio(facts.feature_confidence.get('exotic_star', min((
        _ratio(body.feature_confidence.get(_classification_feature(body), body.confidence))
        for body in exotic_sources
    ), default=1.0)))
    exotic_provenance = facts.feature_provenance.get('exotic_star') or '; '.join(sorted({
        f'{body.candidate_id}:{body.feature_provenance.get(_classification_feature(body), "classification")}'
        for body in exotic_sources
    })) or None

    for body in facts.bodies:
        _ratio(body.completeness)
        _ratio(body.confidence)
        if body.reserve_scope not in {'body', 'synthetic_system'}:
            raise ValueError(f'unknown reserve scope: {body.reserve_scope}')
        reserve_is_system = body.reserve_scope == 'synthetic_system' and body.reserve_level is None
        reserve = _reserve_key(facts.reserve_level if reserve_is_system else body.reserve_level)
        reserve_positive = reserve in {'major', 'pristine'}
        reserve_negative = reserve in {'low', 'depleted'}
        key = _classification(body)
        native = set(_native_economies(key))
        modifiers: set[str] = set()
        if body.rings is True:
            modifiers.add('Extraction')
        if body.biologicals is True:
            modifiers.add('Agriculture')
        if body.geologicals is True:
            modifiers.update(('Extraction', 'Industrial'))

        candidate_economies = native | modifiers
        # Retain known-absence/unknown records for coverage. Only inherited
        # opportunities can occupy a score/depth/specialisation slot.
        for economy in ECONOMIES:
            positives: list[str] = []
            negatives: list[str] = []
            contributors: list[str] = []

            if economy in native:
                contributors.append(f'native:{key}')
            if economy in modifiers:
                contributors.append(f'modifier:{economy.lower()}')

            if economy == 'Agriculture':
                if key in {'earth like world', 'earthlike world', 'elw'}:
                    positives.append('elw-agriculture')
                if key in {'water world', 'ww'}:
                    positives.append('water-world-agriculture')
                if body.terraformable is True:
                    positives.append('terraformable-agriculture')
                if body.biologicals is True:
                    positives.append('biologicals-agriculture')
                if key in {'icy body', 'icy'}:
                    negatives.append('icy-agriculture')
                if body.tidally_locked is True:
                    negatives.append('tidal-lock-agriculture')
            elif economy == 'HighTech':
                if key in {'earth like world', 'earthlike world', 'elw'}:
                    positives.append('elw-hightech')
                if key in {'water world', 'ww'}:
                    positives.append('water-world-hightech')
                if key in {'ammonia world', 'ammonia'}:
                    positives.append('ammonia-hightech')
                if body.biologicals is True:
                    positives.append('biologicals-hightech')
                if body.geologicals is True:
                    positives.append('geologicals-hightech')
            elif economy == 'Tourism':
                if key in {'earth like world', 'earthlike world', 'elw'}:
                    positives.append('elw-tourism')
                if key in {'water world', 'ww'}:
                    positives.append('water-world-tourism')
                if key in {'ammonia world', 'ammonia'}:
                    positives.append('ammonia-tourism')
                if body.biologicals is True:
                    positives.append('biologicals-tourism')
                if body.geologicals is True:
                    positives.append('geologicals-tourism')
                positives.extend(f'{exotic}-system-tourism' for exotic in sorted(exotics))
            elif economy == 'Extraction':
                if reserve_positive:
                    positives.append(f'reserves-{reserve}')
                elif reserve_negative:
                    negatives.append(f'reserves-{reserve}')
                if body.volcanism is True:
                    positives.append('volcanism-extraction')
            elif economy == 'Industrial':
                if reserve_positive:
                    positives.append(f'reserves-{reserve}')
                elif reserve_negative:
                    negatives.append(f'reserves-{reserve}')
            elif economy == 'Refinery':
                if reserve_positive:
                    positives.append(f'reserves-{reserve}')
                elif reserve_negative:
                    negatives.append(f'reserves-{reserve}')

            evidence = _evidence_for_economy(
                economy, body, facts, candidate_economies, reserve, reserve_is_system,
                exotic_known, exotic_value,
                exotic_confidence, exotic_provenance,
            )
            completeness = min(body.completeness, _weighted_mean(
                (float(feature.known), feature.weight) for feature in evidence
            ))
            confidence = _weighted_mean(
                (feature.confidence, feature.weight) for feature in evidence if feature.known
            )
            constraints = ()
            if economy == 'Refinery' and economy in candidate_economies:
                constraints = (SpecialisationConstraint(
                    REFINERY_GROUND_RULE, body.usable_ground_opportunity, 'usable_ground_opportunity',
                    confidence=body.feature_confidence.get('usable_ground_opportunity', body.confidence),
                    provenance=body.feature_provenance.get('usable_ground_opportunity'),
                ),)
            result.append(Opportunity(
                economy=economy,
                candidate_id=body.candidate_id,
                native=economy in native,
                modifier=economy in modifiers,
                strong_positive_rules=tuple(positives),
                strong_negative_rules=tuple(negatives),
                competing_economies=tuple(sorted(candidate_economies - {economy})),
                preferred_specialisation=_preferred(economy, key, body),
                completeness=completeness,
                confidence=confidence,
                contributors=tuple(contributors),
                evidence=evidence,
                specialisation_constraints=constraints,
            ))

    return tuple(result)


def rate_system_facts(facts: SystemFacts) -> dict[str, EconomyRating]:
    return rate_all(opportunities_from_facts(facts))
