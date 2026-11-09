"""Pure port economies and link explanations from the pinned CRE rulebook.

Inputs are in build order. Unknown observations remain explicit in notes;
all arithmetic stays in integer hundredths until the public result is built.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Literal

from .catalogue import planner_facilities
from .plan import Site
from .rules import rule


BodyKind = Literal[
    'black_hole', 'neutron_star', 'white_dwarf', 'brown_dwarf', 'other_star',
    'earth_like_world', 'water_world', 'ammonia_world', 'gas_giant',
    'high_metal_content_world', 'metal_rich_body', 'rocky_ice_body', 'rocky_body', 'icy_body',
]
PortRole = Literal['main', 'remaining_planetary', 'converted', 'unplaced']
ItemKind = Literal['own', 'strong_link', 'passed_on_link', 'planetary_port_link', 'weak_links']
View = Literal['now', 'after_plan']
_STARS = frozenset({'black_hole', 'neutron_star', 'white_dwarf', 'brown_dwarf', 'other_star'})


@dataclass(frozen=True)
class EconomyBody:
    body_ref: str
    parent_ref: str | None = None
    in_binary_pair: bool = False
    kind: BodyKind | None = None
    tidally_locked: bool | None = None
    volcanism: bool | None = None
    terraformable: bool | None = None
    has_rings: bool = False
    geological_signals: int | None = None
    biological_signals: int | None = None


@dataclass(frozen=True)
class EconomySystem:
    reserve: Literal['pristine', 'major', 'common', 'low', 'depleted'] | None = None
    has_black_hole: bool = False
    has_white_dwarf: bool = False
    has_neutron_star: bool = False


@dataclass(frozen=True)
class EconomyItem:
    kind: ItemKind
    base: float
    modifiers: tuple[tuple[str, float], ...]
    floored: bool
    value: float
    source_site_ids: tuple[str, ...]
    via_site_id: str | None
    rule_ids: tuple[str, ...]
    count: int | None = None


@dataclass(frozen=True)
class Economy:
    name: str
    strength: float
    items: tuple[EconomyItem, ...]


@dataclass(frozen=True)
class PortEconomy:
    site_id: str
    body_ref: str | None
    role: PortRole
    supports: str | None
    economies: tuple[Economy, ...]


@dataclass(frozen=True)
class EconomyNote:
    code: str
    site_id: str | None
    body_ref: str | None
    message: str


@dataclass(frozen=True)
class EconomyResult:
    ports: tuple[PortEconomy, ...]
    notes: tuple[EconomyNote, ...]


def _value(rule_id: str) -> Mapping[str, Any]:
    value = rule(rule_id).value
    if not isinstance(value, Mapping):
        raise ValueError(f'{rule_id} must supply a structured value.')
    return value


def _hundredths(value: float) -> int:
    scaled = Decimal(str(value)) * 100
    if scaled != scaled.to_integral_value():
        raise ValueError('Economy rule values must be exact hundredths.')
    return int(scaled)


@dataclass(frozen=True)
class _Item:
    """Internal contributions: base, modifiers and value are all hundredths."""

    kind: ItemKind
    base: int
    modifiers: tuple[tuple[str, int], ...]
    floored: bool
    value: int
    source_site_ids: tuple[str, ...]
    via_site_id: str | None
    rule_ids: tuple[str, ...]
    count: int | None = None

    def public(self) -> EconomyItem:
        return EconomyItem(
            self.kind, self.base / 100,
            tuple((condition, delta / 100) for condition, delta in self.modifiers),
            self.floored, self.value / 100, self.source_site_ids, self.via_site_id,
            self.rule_ids, self.count,
        )


class _Evaluation:
    def __init__(
        self, sites: Sequence[Site], bodies: Sequence[EconomyBody], system: EconomySystem,
    ) -> None:
        self.sites = sites
        self.bodies = {body.body_ref: body for body in bodies}
        self.system = system
        self.catalogue = planner_facilities()
        self.body_economies = _value('RB-0010')['economies']
        self.added_economies = _value('RB-0011')['economies']
        self.exclusions = _value('RB-0027')['bodies_without_geological_or_biological_overrides']
        self.conditions = _value('RB-0018')['economies']
        self.sizes = _value('RB-0019')
        self.floor = _hundredths(self.sizes['minimum_strong_link'])
        self.tiers = _value('RB-0015')['strong_by_tier']
        self.weak = _hundredths(_value('RB-0016')['weak'])
        self.notes: dict[EconomyNote, None] = {}
        self.roles: dict[str, PortRole] = {}
        self.supports: dict[str, str] = {}
        self.main: dict[str, Site] = {}
        self.planetary: dict[str, Site] = {}
        self.items: dict[str, dict[str, list[_Item]]] = {}
        self.own: dict[str, dict[str, _Item]] = {}

    def note(
        self, code: str, message: str, body_ref: str | None = None, site_id: str | None = None,
    ) -> None:
        self.notes.setdefault(EconomyNote(code, site_id, body_ref, message), None)

    def body(self, site: Site) -> EconomyBody:
        # Unplaced sites never use this placeholder for links.
        return self.bodies.get(site.body_ref, EconomyBody(site.body_ref or ''))

    def observe_unknowns(self) -> None:
        if self.system.reserve is None:
            self.note('unknown_reserve', 'System reserve is unknown; no resource modifier applied.')
        seen: set[str] = set()
        for site in self.sites:
            if site.body_ref is None:
                is_port = self.catalogue[site.facility_id].is_port
                self.note(
                    'unplaced_port' if is_port else 'unplaced_facility',
                    'Unplaced site forms and receives no links.', site_id=site.site_id,
                )
                continue
            if site.body_ref in seen:
                continue
            seen.add(site.body_ref)
            if site.body_ref not in self.bodies:
                self.note('unknown_body_ref', 'Body reference is missing; all body facts are unknown.',
                          site.body_ref)
            body = self.body(site)
            unknowns = (
                ('kind', 'unknown_body_kind', 'Body kind is unknown; no inherited own economies.'),
                ('geological_signals', 'unknown_geological_signals', 'Geological signals are unknown; treated as zero.'),
                ('biological_signals', 'unknown_biological_signals', 'Biological signals are unknown; treated as zero.'),
                ('volcanism', 'unknown_volcanism', 'Volcanism is unknown; no volcanism modifier applied.'),
                ('tidally_locked', 'unknown_tidal_lock', 'Tidal lock is unknown; treated as false.'),
            )
            for field, code, message in unknowns:
                if getattr(body, field) is None:
                    self.note(code, message, body.body_ref)

    def select_ports(self) -> None:
        """RB-0020: highest tier, then first in the original build order."""
        groups: dict[str, list[Site]] = {}
        for site in self.sites:
            if not self.catalogue[site.facility_id].is_port:
                continue
            self.items[site.site_id] = {}
            if site.body_ref is None:
                self.roles[site.site_id] = 'unplaced'
            else:
                groups.setdefault(site.body_ref, []).append(site)
        for body_ref, ports in groups.items():
            orbital = [site for site in ports if site.slot_kind == 'orbital']
            surface = [site for site in ports if site.slot_kind == 'surface']
            main = max(orbital or surface, key=lambda site: self.catalogue[site.facility_id].tier)
            self.main[body_ref] = main
            self.roles[main.site_id] = 'main'
            if orbital and surface:
                planetary = max(surface, key=lambda site: self.catalogue[site.facility_id].tier)
                self.planetary[body_ref] = planetary
                self.roles[planetary.site_id] = 'remaining_planetary'
            for site in ports:
                if site.site_id in self.roles:
                    continue
                self.roles[site.site_id] = 'converted'
                target = self.planetary.get(body_ref, main) if site.slot_kind == 'surface' else main
                self.supports[site.site_id] = target.site_id

    def signal_flags(self, body: EconomyBody) -> tuple[bool, bool]:
        allowed = body.kind not in self.exclusions
        return (
            allowed and (body.geological_signals or 0) > 0,
            allowed and (body.biological_signals or 0) > 0,
        )

    def condition_flags(self, site: Site) -> dict[str, bool]:
        body = self.body(site)
        parent = self.bodies.get(body.parent_ref)
        grandparent = self.bodies.get(parent.parent_ref) if parent else None
        locked_to_star = bool(body.tidally_locked and parent and parent.kind in _STARS)
        locked_moon = bool(
            body.tidally_locked and parent and parent.kind is not None and parent.kind not in _STARS
            and parent.tidally_locked and grandparent and grandparent.kind in _STARS
        )
        geologicals, organics = self.signal_flags(body)
        return {
            'orbiting_earth_like_world': body.kind == 'earth_like_world',
            'orbiting_water_world': body.kind == 'water_world',
            'orbiting_ammonia_world': body.kind == 'ammonia_world',
            'on_or_orbiting_body_with_organics': organics,
            'on_or_orbiting_body_with_geologicals': geologicals,
            'on_or_orbiting_icy_body': body.kind == 'icy_body',
            'on_or_orbiting_planet_tidally_locked_to_star': locked_to_star,
            'orbiting_moon_tidally_locked_to_planet_tidally_locked_to_star': locked_moon and site.slot_kind == 'orbital',
            'on_moon_tidally_locked_to_planet_tidally_locked_to_star': locked_moon and site.slot_kind == 'surface',
            'on_or_orbiting_terraformable_body': body.terraformable is True,
            'system_major_or_pristine_resources': self.system.reserve in ('major', 'pristine'),
            'system_low_or_depleted_resources': self.system.reserve in ('low', 'depleted'),
            'on_or_orbiting_body_with_volcanism': body.volcanism is True,
            'system_with_black_hole': self.system.has_black_hole,
            'system_with_white_dwarf': self.system.has_white_dwarf,
            'system_with_neutron_star': self.system.has_neutron_star,
        }

    def modified_item(
        self, site: Site, economy: str, kind: ItemKind, base: int, rule_ids: tuple[str, ...],
    ) -> _Item:
        flags = self.condition_flags(site)
        modifiers: list[tuple[str, int]] = []
        rules = [*rule_ids, 'RB-0018', 'RB-0019']
        conditions = self.conditions.get(economy, {})
        for direction, size in (('boosts', 'boost'), ('decreases', 'decrease')):
            for condition in conditions.get(direction, ()):
                condition_id = condition['condition']
                if not flags[condition_id]:
                    continue
                if condition['status'] == 'Disputed':
                    self.note(
                        'terraformable_boost_not_applied',
                        'Agriculture terraformable boost is Disputed (RB-0018, C-0013); not applied.',
                        site.body_ref,
                    )
                    continue
                modifiers.append((condition_id, _hundredths(self.sizes[size])))
                if condition_id == 'on_or_orbiting_body_with_volcanism':
                    rules.append('RB-0013')
                if condition_id in ('on_or_orbiting_body_with_organics', 'on_or_orbiting_body_with_geologicals'):
                    rules.append('RB-0027')
                if 'tidally_locked' in condition_id and self.body(site).in_binary_pair:
                    rules.append('RB-0028')
        total = base + sum(delta for _, delta in modifiers)
        floored = total < self.floor
        if floored:
            qualification = ' Own-economy floor is assumed from RB-0019, not measured.' if kind == 'own' else ''
            self.note('floor_applied', f'{economy}: RB-0019 minimum applied.{qualification}',
                      site.body_ref, site.site_id)
        return _Item(kind, base, tuple(modifiers), floored, max(self.floor, total),
                     (site.site_id,), None, tuple(dict.fromkeys(rules)))

    def own_economies(self, site: Site) -> dict[str, _Item]:
        template = self.catalogue[site.facility_id]
        if not template.is_colony_port:
            baselines = _value('RB-0009')['specialised_baseline_strength']
            ground = site.facility_id in (
                'surface_planetary_port_outpost_industrial',
                'surface_planetary_port_outpost_scientific',
            )
            baseline = baselines['industrial_or_scientific_ground_port' if ground else 'unnamed_orbital_versions']
            message = 'Specialised-port baseline is not measured (RB-0009).'
            if not ground:
                message += ' The guide does not identify which orbital versions have a 1.0 baseline.'
            self.note('specialised_baseline_not_measured', message, site.body_ref, site.site_id)
            if not template.economy:
                return {}
            base = _hundredths(baseline)
            return {template.economy: _Item('own', base, (), False, base, (site.site_id,), None, ('RB-0009',))}
        body = self.body(site)
        if body.kind is None:
            return {}
        inherited = {name: ['RB-0010'] for name in self.body_economies[body.kind]}
        geologicals, organics = self.signal_flags(body)
        for flag, active in (('rings', body.has_rings), ('organics', organics), ('geologicals', geologicals)):
            if active:
                for name in self.added_economies[flag]:
                    inherited.setdefault(name, []).append('RB-0011')
        base = _hundredths(_value('RB-0012')['inherited_economy_strength'])
        own = {}
        for name, sources in inherited.items():
            rules = (*sources, 'RB-0012', 'RB-0027', 'RB-0028')
            if name == 'Extraction' and 'RB-0010' in sources and 'RB-0011' in sources:
                rules += ('RB-0014',)
            own[name] = self.modified_item(site, name, 'own', base, rules)
        return own

    def add(self, target: Site, economy: str, item: _Item) -> None:
        self.items[target.site_id].setdefault(economy, []).append(item)

    def initialise_own(self) -> None:
        for site in self.sites:
            if site.site_id not in self.roles:
                continue
            self.own[site.site_id] = self.own_economies(site)
            for name, item in self.own[site.site_id].items():
                self.add(site, name, item)
            if self.roles[site.site_id] == 'converted':
                self.note('converted_port_links_not_measured',
                          'Converted-port strong links use its tier and own economies; not measured (RB-0020).',
                          site.body_ref, site.site_id)
                self.note('converted_port_weak_links_inferred',
                          'One weak link per own economy of a converted port is inferred, not stated in the rulebook.',
                          site.body_ref, site.site_id)

    def sender_economies(self, site: Site) -> tuple[str, ...]:
        if site.body_ref is None:
            return ()
        role = self.roles.get(site.site_id)
        if role == 'converted':
            return tuple(self.own[site.site_id])
        if role is not None:
            return ()
        economy = self.catalogue[site.facility_id].economy
        return (economy,) if economy else ()

    def link(self, site: Site, economy: str, kind: ItemKind) -> _Item:
        tier = self.catalogue[site.facility_id].tier
        rules = ('RB-0015', 'RB-0017')
        if site.site_id in self.roles:
            rules += (*self.own[site.site_id][economy].rule_ids, 'RB-0020')
        if kind == 'planetary_port_link':
            rules += ('RB-0029',)
        return self.modified_item(site, economy, kind, _hundredths(self.tiers[str(tier)]), rules)

    def local_links(self) -> None:
        # RB-0029: surface links go through the remaining planetary port;
        # orbital links only go to the orbital main. Preserve each source value.
        for site in self.sites:
            main = self.main.get(site.body_ref)
            if main is None:
                continue
            planetary = self.planetary.get(site.body_ref)
            target = planetary if planetary and site.slot_kind == 'surface' else main
            for economy in self.sender_economies(site):
                item = self.link(site, economy, 'strong_link')
                if planetary:
                    item = replace(item, rule_ids=tuple(dict.fromkeys((*item.rule_ids, 'RB-0029'))))
                self.add(target, economy, item)
                if target is planetary:
                    self.add(main, economy, replace(item, kind='passed_on_link', via_site_id=planetary.site_id))
        for body_ref, planetary in self.planetary.items():
            for economy in self.own[planetary.site_id]:
                self.add(self.main[body_ref], economy, self.link(planetary, economy, 'planetary_port_link'))

    def weak_links(self) -> None:
        senders = [(site, self.sender_economies(site)) for site in self.sites]
        for target in self.sites:
            if self.roles.get(target.site_id) not in ('main', 'remaining_planetary'):
                continue
            grouped: dict[str, list[str]] = {}
            for source, economies in senders:
                if source.body_ref == target.body_ref:
                    continue
                for economy in economies:
                    grouped.setdefault(economy, []).append(source.site_id)
            for economy, sources in grouped.items():
                value = self.weak * len(sources)
                self.add(target, economy, _Item(
                    'weak_links', value, (), False, value, tuple(sources), None,
                    ('RB-0016', 'RB-0020'), len(sources),
                ))

    def result(self) -> EconomyResult:
        item_order = {kind: i for i, kind in enumerate((
            'own', 'strong_link', 'passed_on_link', 'planetary_port_link', 'weak_links',
        ))}
        ports = []
        for site in self.sites:
            if site.site_id not in self.roles:
                continue
            economies = []
            for name, items in self.items[site.site_id].items():
                ordered = sorted(items, key=lambda item: item_order[item.kind])
                economies.append(Economy(name, sum(item.value for item in ordered) / 100,
                                         tuple(item.public() for item in ordered)))
            economies.sort(key=lambda economy: (-economy.strength, economy.name))
            ports.append(PortEconomy(site.site_id, site.body_ref, self.roles[site.site_id],
                                     self.supports.get(site.site_id), tuple(economies)))
        return EconomyResult(tuple(ports), tuple(self.notes))


def evaluate_economies(
    sites: Sequence[Site], bodies: Sequence[EconomyBody], system: EconomySystem, view: View,
) -> EconomyResult:
    """Calculate complete sites now, or all non-demolished sites after the plan.

    Missing body references are accepted as unknown observations. Unplaced sites
    cannot send or receive links. Site and body identifiers must be unique.
    """
    if view not in ('now', 'after_plan'):
        raise ValueError('Economy view must be now or after_plan.')
    if len(sites) > 300 or len(bodies) > 1000:
        raise ValueError('Economy inputs are limited to 300 sites and 1000 bodies.')
    if len({site.site_id for site in sites}) != len(sites):
        raise ValueError('Site ids must be unique.')
    if len({body.body_ref for body in bodies}) != len(bodies):
        raise ValueError('Body references must be unique.')
    if any(site.status not in ('planned', 'building', 'complete', 'demolished') for site in sites):
        raise ValueError('Unknown site status.')
    counted = tuple(site for site in sites if site.status == 'complete' or (
        view == 'after_plan' and site.status in ('planned', 'building')
    ))
    evaluation = _Evaluation(counted, bodies, system)
    # Require the routing authority to be present in the pinned release.
    rule('RB-0029')
    if system.reserve not in (None, 'pristine', 'major', 'common', 'low', 'depleted'):
        raise ValueError('Unknown reserve level.')
    for body in bodies:
        if body.kind is not None and body.kind not in evaluation.body_economies:
            raise ValueError(f'Unknown body kind: {body.kind!r}.')
    for site in counted:
        if site.slot_kind not in ('orbital', 'surface'):
            raise ValueError('Unknown site slot kind.')
        if site.facility_id not in evaluation.catalogue:
            raise ValueError(f'Unknown facility: {site.facility_id!r}.')
    evaluation.observe_unknowns()
    evaluation.select_ports()
    evaluation.initialise_own()
    evaluation.local_links()
    evaluation.weak_links()
    return evaluation.result()
