"""Measured CRE economy examples and the pure planner's link explanations."""
from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps' / 'api' / 'src'))

from edfinder_api.cpe import (  # noqa: E402
    Economy,
    EconomyBody,
    EconomyItem,
    EconomyNote,
    EconomyResult,
    EconomySystem,
    PortEconomy,
    Site,
    evaluate_economies,
)
from edfinder_api.cpe.catalogue import facility  # noqa: E402
from edfinder_api.cpe.rules import rule  # noqa: E402


def body(body_ref: str = 'A', kind: str | None = 'high_metal_content_world', **facts) -> EconomyBody:
    defaults = dict(
        tidally_locked=False, volcanism=False, terraformable=False,
        geological_signals=0, biological_signals=0,
    )
    defaults.update(facts)
    return EconomyBody(body_ref=body_ref, kind=kind, **defaults)


def site(
    site_id: str,
    facility_id: str = 'coriolis_station',
    body_ref: str | None = 'A',
    status: str = 'complete',
    slot_kind: str | None = None,
) -> Site:
    location = facility(facility_id).allowed_location
    return Site(
        site_id, facility_id, body_ref,
        slot_kind or ('surface' if location == 'surface' else 'orbital'), status,
    )


def strengths(port: PortEconomy) -> dict[str, float]:
    return {economy.name: economy.strength for economy in port.economies}


def economy(port: PortEconomy, name: str) -> Economy:
    return next(item for item in port.economies if item.name == name)


def note_codes(result: EconomyResult) -> set[str]:
    return {note.code for note in result.notes}


def weak_sources(**counts: int) -> tuple[Site, ...]:
    catalogue_ids = {
        'Agriculture': 'orbital_installation_space_farm',
        'Extraction': 'orbital_installation_mining_outpost',
        'HighTech': 'orbital_installation_relay_station',
        'Industrial': 'surface_settlement_industrial_t1_s',
        'Military': 'orbital_installation_security_station',
        'Refinery': 'refinery',
        'Tourism': 'tourism_installation',
    }
    return tuple(
        site(f'weak-{name}-{index}', catalogue_ids[name], 'remote')
        for name, count in counts.items()
        for index in range(count)
    )


@pytest.mark.parametrize(('facility_id', 'host', 'reserve', 'expected'), [
    # Macbeth Vision (EV-0006): the pristine-resource boost affects its own Extraction.
    ('dodecahedron_t3', body(), 'pristine', {'Extraction': 1.40}),
    # Pordenone Vision (EV-0006): volcanism adds a second Extraction boost.
    ('orbital_outpost_civilian', body(volcanism=True), 'pristine', {'Extraction': 1.80}),
    # Cabral Gateway (EV-0006): volcanism does not boost Refinery.
    ('coriolis_station', body(kind='rocky_body', volcanism=True), 'pristine', {'Refinery': 1.40}),
    # Heyerdahl Beacon (EV-0006): water boosts Agriculture and Tourism.
    ('orbital_outpost_civilian', body(kind='water_world', volcanism=True), 'common',
     {'Agriculture': 1.40, 'Tourism': 1.40}),
], ids=['Macbeth Vision', 'Pordenone Vision', 'Cabral Gateway', 'Heyerdahl Beacon'])
def test_measured_own_economies(facility_id, host, reserve, expected) -> None:
    result = evaluate_economies((site('port', facility_id),), (host,), EconomySystem(reserve), 'now')
    assert strengths(result.ports[0]) == expected
    for value in result.ports[0].economies:
        assert len(value.items) == 1
        own = value.items[0]
        assert own.kind == 'own'
        assert own.base == 1.0
        assert own.source_site_ids == ('port',)
        assert {'RB-0010', 'RB-0012', 'RB-0028'} <= set(own.rule_ids)


def test_simaks_progress_disputed_terraforming_boost_is_not_applied() -> None:
    # Simak's Progress (EV-0006): terraforming does not cancel the tidal decrease.
    host = body(kind='water_world', tidally_locked=True, parent_ref='star', terraformable=True)
    result = evaluate_economies(
        (site('port', 'orbital_outpost_civilian'),),
        (body('star', 'other_star'), host), EconomySystem('pristine'), 'now',
    )
    assert strengths(result.ports[0]) == {'Agriculture': 1.0, 'Tourism': 1.4}
    assert economy(result.ports[0], 'Agriculture').items[0].modifiers == (
        ('orbiting_water_world', 0.4),
        ('on_or_orbiting_planet_tidally_locked_to_star', -0.4),
    )
    assert 'terraformable_boost_not_applied' in note_codes(result)


def test_inherited_economies_do_not_stack_sources() -> None:
    # RB-0012/RB-0014, consistent with Macbeth Vision's 1.0 own baseline (EV-0006).
    result = evaluate_economies(
        (site('port', 'dodecahedron_t3'),), (body(has_rings=True, geological_signals=2),),
        EconomySystem('pristine'), 'now',
    )
    assert strengths(result.ports[0]) == {'Extraction': 1.4, 'Industrial': 1.4}
    extraction = economy(result.ports[0], 'Extraction')
    assert len(extraction.items) == 1
    assert extraction.items[0].base == 1.0
    assert {'RB-0011', 'RB-0014', 'RB-0027'} <= set(extraction.items[0].rule_ids)


def test_low_town_weak_links_and_unknown_reserve() -> None:
    # Low Town (EV-0006): two Tourism weak links add exactly 0.10.
    senders = weak_sources(Tourism=2, Military=2, HighTech=1)
    result = evaluate_economies(
        (site('port', 'orbis_t3'), *senders), (body(kind='water_world'), body('remote')),
        EconomySystem(), 'now',
    )
    assert strengths(result.ports[0]) == {
        'Agriculture': 1.4, 'Tourism': 1.5, 'Military': 0.1, 'HighTech': 0.05,
    }
    weak = economy(result.ports[0], 'Tourism').items[-1]
    assert weak.kind == 'weak_links'
    assert weak.base == weak.value == 0.1
    assert weak.count == 2
    assert weak.source_site_ids == ('weak-Tourism-0', 'weak-Tourism-1')
    assert weak.modifiers == () and not weak.floored
    assert 'RB-0016' in weak.rule_ids
    assert [note.code for note in result.notes].count('unknown_reserve') == 1


def test_vasellini_prefecture_own_industrial_does_not_stack() -> None:
    # Vasellini Prefecture (EV-0006): icy body plus geologicals is one own Industrial.
    host = body(kind='icy_body', tidally_locked=True, parent_ref='giant',
                geological_signals=2, volcanism=True)
    result = evaluate_economies(
        (site('port'), site('industrial', 'surface_settlement_industrial_t1_s')),
        (body('giant', 'gas_giant'), host), EconomySystem('pristine'), 'now',
    )
    assert strengths(result.ports[0]) == {'Industrial': 2.2, 'Extraction': 1.8}
    industrial = economy(result.ports[0], 'Industrial')
    assert industrial.strength < 2.4
    assert [(item.kind, item.value) for item in industrial.items] == [('own', 1.4), ('strong_link', 0.8)]


def test_an_icy_body_with_organics_matches_a_port_measured_in_game() -> None:
    # Measured in game (EV-0006): a consistency case, not isolated proof of the rule.
    result = evaluate_economies(
        (site('port', 'orbital_outpost_civilian'),),
        (body(kind='icy_body', biological_signals=1),), EconomySystem('major'), 'now',
    )
    assert strengths(result.ports[0]) == {'Industrial': 1.4, 'Agriculture': 1.0, 'Terraforming': 1.0}
    agriculture = economy(result.ports[0], 'Agriculture').items[0]
    assert agriculture.modifiers == (
        ('on_or_orbiting_body_with_organics', 0.4), ('on_or_orbiting_icy_body', -0.4),
    )
    assert economy(result.ports[0], 'Terraforming').items[0].modifiers == ()


def test_a_rocky_ice_body_takes_the_farm_penalty_too() -> None:
    # Added 22 November 2026 by the owner's decision: the guide's farm case
    # study (p. 88) counts rocky ice bodies; its modifier list (p. 55) names
    # icy bodies only.
    result = evaluate_economies(
        (site('port', 'orbital_outpost_civilian'), site('farm', 'orbital_installation_space_farm')),
        (body(kind='rocky_ice_body', biological_signals=1),), EconomySystem('major'), 'now',
    )
    (port,) = result.ports
    assert strengths(port) == {'Industrial': 1.4, 'Refinery': 1.4, 'Agriculture': 1.4, 'Terraforming': 1.0}
    own, farm = economy(port, 'Agriculture').items
    assert own.modifiers == farm.modifiers == (
        ('on_or_orbiting_body_with_organics', 0.4), ('on_or_orbiting_rocky_ice_body', -0.4),
    )
    assert (own.value, farm.kind, farm.value) == (1.0, 'strong_link', 0.4)
    # Without organics a space farm there falls to the floor.
    result = evaluate_economies(
        (site('port', 'orbital_outpost_civilian'), site('farm', 'orbital_installation_space_farm')),
        (body(kind='rocky_ice_body'),), EconomySystem(), 'now',
    )
    (farm,) = economy(result.ports[0], 'Agriculture').items
    assert farm.modifiers == (('on_or_orbiting_rocky_ice_body', -0.4),)
    assert (farm.floored, farm.value) == (True, 0.1)
    # Other bodies are not touched by it.
    for kind in ('rocky_body', 'icy_body', 'high_metal_content_world'):
        result = evaluate_economies(
            (site('port', 'orbital_outpost_civilian'), site('farm', 'orbital_installation_space_farm')),
            (body(kind=kind),), EconomySystem(), 'now',
        )
        (farm,) = economy(result.ports[0], 'Agriculture').items
        assert ('on_or_orbiting_rocky_ice_body', -0.4) not in farm.modifiers, kind


@pytest.mark.parametrize('farms_demolished', [False, True])
def test_binary_pair_water_worlds_lose_0_45_each_when_their_farms_go(farms_demolished: bool) -> None:
    # Measured in game (EV-0006): two tidally locked water worlds in a binary
    # pair, each with a port and a farm; removing the farms drops each by 0.45.
    status = 'demolished' if farms_demolished else 'complete'
    hosts = (
        body('star', 'other_star'),
        body('first', 'water_world', tidally_locked=True, parent_ref='star', in_binary_pair=True),
        body('second', 'water_world', tidally_locked=True, parent_ref='star', in_binary_pair=True),
        body('remote'),
    )
    sites = (
        site('first-port', 'coriolis_station', 'first'), site('second-port', 'ocellus_station', 'second'),
        site('first-farm', 'orbital_installation_space_farm', 'first', status),
        site('second-farm', 'orbital_installation_space_farm', 'second', status),
        *weak_sources(Agriculture=2, Military=1, Extraction=1),
    )
    result = evaluate_economies(sites, hosts, EconomySystem('pristine'), 'now')
    for port in result.ports:
        assert strengths(port) == {
            'Agriculture': 1.1 if farms_demolished else 1.55,
            'Tourism': 1.4, 'Military': 0.05, 'Extraction': 0.05,
        }
        agriculture = economy(port, 'Agriculture')
        for item in agriculture.items:
            if item.kind != 'weak_links':
                assert ('on_or_orbiting_planet_tidally_locked_to_star', -0.4) in item.modifiers
                assert 'RB-0028' in item.rule_ids
        assert agriculture.items[-1].count == (2 if farms_demolished else 3)
        assert all(source not in ('first-port', 'second-port') for source in agriculture.items[-1].source_site_ids)


def test_measured_planetary_links_and_passed_on_links() -> None:
    # Measured in game (EV-0007): one body with a Dodec and a planetary port.
    sites = (
        site('dodec', 'dodecahedron_t3'), site('planetary', 'planetary_port'),
        site('mine', 'orbital_installation_mining_outpost'),
        site('security', 'orbital_installation_security_station'),
        site('government', 'orbital_installation_government'),
        site('extraction', 'surface_settlement_extraction_t2_l'),
        *(site(f'refinery-{i}', 'refinery') for i in range(3)),
        *weak_sources(Extraction=7, Industrial=3, HighTech=5, Military=4, Refinery=1, Agriculture=1),
    )
    hosts = (
        body('star', 'other_star'),
        body(parent_ref='star', tidally_locked=True, volcanism=True, geological_signals=3),
        body('remote'),
    )
    result = evaluate_economies(sites, hosts, EconomySystem('pristine'), 'now')
    dodec, planetary = result.ports
    assert (dodec.role, planetary.role) == ('main', 'remaining_planetary')
    assert dodec.supports is planetary.supports is None
    assert strengths(dodec) == {
        'Extraction': 6.95, 'Refinery': 3.65, 'Industrial': 3.15,
        'Military': 1.0, 'HighTech': 0.25, 'Agriculture': 0.05,
    }
    assert strengths(planetary) == {
        'Extraction': 3.75, 'Refinery': 3.65, 'Industrial': 1.55,
        'HighTech': 0.25, 'Military': 0.2, 'Agriculture': 0.05,
    }
    assert [(item.kind, item.value) for item in economy(dodec, 'Extraction').items] == [
        ('own', 1.8), ('strong_link', 1.2), ('passed_on_link', 1.6),
        ('planetary_port_link', 2.0), ('weak_links', 0.35),
    ]
    planetary_link = next(item for item in economy(dodec, 'Industrial').items
                          if item.kind == 'planetary_port_link')
    assert planetary_link.base == 1.2 and planetary_link.value == 1.6
    assert planetary_link.source_site_ids == ('planetary',)
    assert 'RB-0029' in planetary_link.rule_ids
    passed = [item for item in economy(dodec, 'Refinery').items if item.kind == 'passed_on_link']
    assert [item.value for item in passed] == [1.2, 1.2, 1.2]
    assert [item.source_site_ids for item in passed] == [('refinery-0',), ('refinery-1',), ('refinery-2',)]
    assert all(item.via_site_id == 'planetary' and 'RB-0029' in item.rule_ids for item in passed)
    assert not any(
        source in {'mine', 'security', 'government'}
        for value in planetary.economies for item in value.items for source in item.source_site_ids
    )
    for port in result.ports:
        for value in port.economies:
            assert value.strength == round(sum(item.value for item in value.items), 2)


def test_equal_tier_converted_port_sends_strong_and_weak_links_but_receives_none() -> None:
    sites = (site('earlier'), site('later'), site('other', body_ref='B'),
             *weak_sources(Extraction=1))
    result = evaluate_economies(sites, (body(), body('B', 'rocky_body'), body('remote')),
                                EconomySystem('pristine'), 'now')
    earlier, later, other = result.ports
    assert [port.site_id for port in result.ports] == ['earlier', 'later', 'other']
    assert earlier.role == 'main' and later.role == 'converted'
    assert later.supports == 'earlier'
    assert strengths(later) == {'Extraction': 1.4}
    assert [item.kind for item in economy(later, 'Extraction').items] == ['own']
    sent = next(item for item in economy(earlier, 'Extraction').items if item.kind == 'strong_link')
    assert sent.source_site_ids == ('later',) and sent.base == 0.8 and sent.value == 1.2
    assert strengths(earlier) == {'Extraction': 2.65}
    weak = economy(other, 'Extraction').items[0]
    assert weak.source_site_ids == ('later', 'weak-Extraction-0') and weak.value == 0.1
    assert {'converted_port_links_not_measured', 'converted_port_weak_links_inferred'} <= note_codes(result)


def test_later_higher_tier_port_becomes_main() -> None:
    result = evaluate_economies((site('earlier'), site('later', 'orbis_t3')), (body(),),
                                EconomySystem('common'), 'now')
    assert [(port.site_id, port.role, port.supports) for port in result.ports] == [
        ('earlier', 'converted', 'later'), ('later', 'main', None),
    ]


@pytest.mark.parametrize('first_planetary', ['surface_planetary_port_outpost_civilian', 'planetary_port'])
def test_mixed_ports_keep_highest_tier_earliest_planetary_and_pass_converted_links(first_planetary) -> None:
    sites = (site('orbital'), site('surface-first', first_planetary), site('surface-last', 'planetary_port'))
    result = evaluate_economies(sites, (body(),), EconomySystem('common'), 'now')
    orbital, first, last = result.ports
    assert orbital.role == 'main'
    remaining, converted = (first, last) if first_planetary == 'planetary_port' else (last, first)
    assert remaining.role == 'remaining_planetary'
    assert converted.role == 'converted' and converted.supports == remaining.site_id
    direct = next(item for item in economy(remaining, 'Extraction').items if item.kind == 'strong_link')
    passed = next(item for item in economy(orbital, 'Extraction').items if item.kind == 'passed_on_link')
    assert direct.source_site_ids == passed.source_site_ids == (converted.site_id,)
    assert passed.value == direct.value
    assert passed.via_site_id == remaining.site_id


def test_surface_only_ports_use_the_same_main_port_selection() -> None:
    sites = (
        site('outpost', 'surface_planetary_port_outpost_civilian'),
        site('main', 'planetary_port'), site('equal-later', 'planetary_port'),
        site('orbital-farm', 'orbital_installation_space_farm'),
    )
    result = evaluate_economies(sites, (body(),), EconomySystem('common'), 'now')
    assert [(port.role, port.supports) for port in result.ports] == [
        ('converted', 'main'), ('main', None), ('converted', 'main'),
    ]
    assert economy(result.ports[1], 'Agriculture').items[0].source_site_ids == ('orbital-farm',)


@pytest.mark.parametrize('status', ['planned', 'building', 'complete', 'demolished'])
def test_views_filter_facilities_and_ports_before_selecting_roles(status) -> None:
    sites = (site('original'), site('farm', 'orbital_installation_space_farm', status=status),
             site('higher', 'orbis_t3', status=status))
    for view in ('now', 'after_plan'):
        counted = status == 'complete' or (view == 'after_plan' and status in ('planned', 'building'))
        result = evaluate_economies(sites, (body(),), EconomySystem('common'), view)
        assert len(result.ports) == (2 if counted else 1)
        if counted:
            assert result.ports[0].role == 'converted' and result.ports[1].role == 'main'
            assert strengths(result.ports[1])['Agriculture'] == 0.4
        else:
            assert result.ports[0].role == 'main'
            assert 'Agriculture' not in strengths(result.ports[0])


def test_noncounted_sites_leave_no_notes_or_weak_links() -> None:
    sites = (site('port'), site('planned', 'orbital_installation_space_farm', 'missing', 'planned'),
             site('dead', body_ref=None, status='demolished'))
    result = evaluate_economies(sites, (body(),), EconomySystem('common'), 'now')
    assert len(result.ports) == 1 and result.notes == ()
    assert strengths(result.ports[0]) == {'Extraction': 1.0}


@pytest.mark.parametrize('missing_ref', [False, True])
def test_unknown_body_kind_or_reference_has_no_own_economies_but_keeps_links(missing_ref) -> None:
    hosts = () if missing_ref else (EconomyBody('A'),)
    sites = (site('port'), site('mine', 'orbital_installation_mining_outpost'))
    result = evaluate_economies(sites, hosts, EconomySystem('pristine'), 'now')
    assert strengths(result.ports[0]) == {'Extraction': 0.8}
    assert [item.kind for item in economy(result.ports[0], 'Extraction').items] == ['strong_link']
    expected = {'unknown_body_kind', 'unknown_geological_signals', 'unknown_biological_signals',
                'unknown_volcanism', 'unknown_tidal_lock'}
    if missing_ref:
        expected.add('unknown_body_ref')
    assert note_codes(result) == expected
    assert len(result.notes) == len(expected)
    assert all(note.body_ref == 'A' and note.site_id is None for note in result.notes)


def test_unknown_facts_noted_once_per_used_body_even_when_irrelevant() -> None:
    hosts = (EconomyBody('A', kind='other_star'), EconomyBody('remote', kind='other_star'),
             EconomyBody('unused'))
    sites = (site('port'), site('other-port'), *weak_sources(Military=3))
    result = evaluate_economies(sites, hosts, EconomySystem(), 'now')
    for code in ('unknown_geological_signals', 'unknown_biological_signals',
                 'unknown_volcanism', 'unknown_tidal_lock'):
        notes = [note for note in result.notes if note.code == code]
        assert [note.body_ref for note in notes] == ['A', 'remote']
    assert [note.code for note in result.notes].count('unknown_reserve') == 1
    assert not any(note.body_ref == 'unused' for note in result.notes)
    assert len(set(result.notes)) == len(result.notes)


def test_unplaced_sites_form_and_receive_no_links() -> None:
    sites = (site('port'), site('unplaced', body_ref=None),
             site('farm', 'orbital_installation_space_farm', None))
    result = evaluate_economies(sites, (body(),), EconomySystem('common'), 'now')
    placed, unplaced = result.ports
    assert strengths(placed) == {'Extraction': 1.0}
    assert unplaced.role == 'unplaced' and unplaced.supports is None and unplaced.economies == ()
    assert 'unplaced_port' in note_codes(result)
    assert any(note.site_id == 'farm' for note in result.notes)


@pytest.mark.parametrize(('facility_id', 'name', 'baseline'), [
    ('surface_planetary_port_outpost_industrial', 'Industrial', 0.5),
    ('surface_planetary_port_outpost_scientific', 'HighTech', 0.5),
    ('orbital_outpost_criminal', 'Contraband', 1.0),
    ('orbital_outpost_industrial', 'Industrial', 1.0),
    ('orbital_outpost_military', 'Military', 1.0),
    ('orbital_outpost_scientific', 'HighTech', 1.0),
    ('asteroid_base', 'Extraction', 1.0),
])
def test_specialised_ports_ignore_body_and_modifiers_and_report_unmeasured_baseline(facility_id, name, baseline) -> None:
    host = body(kind='earth_like_world', volcanism=True, has_rings=True,
                geological_signals=2, biological_signals=2)
    result = evaluate_economies((site('port', facility_id),), (host,), EconomySystem('pristine'), 'now')
    assert strengths(result.ports[0]) == {name: baseline}
    item = result.ports[0].economies[0].items[0]
    assert item.base == item.value == baseline and item.modifiers == ()
    assert 'RB-0009' in item.rule_ids
    notes = [note for note in result.notes if note.code == 'specialised_baseline_not_measured']
    assert len(notes) == 1 and notes[0].site_id == 'port'


@pytest.mark.parametrize('kind', ['earth_like_world', 'water_world', 'ammonia_world', 'gas_giant'])
def test_rb0027_excludes_signal_inheritance_and_modifiers_but_preserves_rings(kind) -> None:
    sites = (site('port'), site('hightech', 'orbital_installation_relay_station'),
             site('tourism', 'tourism_installation'))
    clean = body(kind=kind, has_rings=True)
    signals = replace(clean, geological_signals=10, biological_signals=10)
    expected = evaluate_economies(sites, (clean,), EconomySystem('common'), 'now')
    actual = evaluate_economies(sites, (signals,), EconomySystem('common'), 'now')
    assert actual == expected
    assert strengths(actual.ports[0])['Extraction'] == 1.0
    assert 'Terraforming' not in strengths(actual.ports[0])


@pytest.mark.parametrize(('reserve', 'strength'), [('pristine', 0.8), ('major', 0.8), ('common', 0.4),
                                                 ('low', 0.1), ('depleted', 0.1), (None, 0.4)])
def test_reserve_modifiers_and_strong_link_floor(reserve, strength) -> None:
    result = evaluate_economies(
        (site('port'), site('mine', 'orbital_installation_mining_outpost')),
        (body(kind='other_star'),), EconomySystem(reserve), 'now',
    )
    link = economy(result.ports[0], 'Extraction').items[0]
    assert link.base == 0.4 and link.value == strength
    assert link.floored is (reserve in ('low', 'depleted'))
    assert ('floor_applied' in note_codes(result)) is link.floored
    assert {'RB-0015', 'RB-0018', 'RB-0019'} <= set(link.rule_ids)


@pytest.mark.parametrize(('slot', 'facility_id', 'condition'), [
    ('orbital', 'orbital_installation_space_farm',
     'orbiting_moon_tidally_locked_to_planet_tidally_locked_to_star'),
    ('surface', 'surface_settlement_agriculture_t1_s',
     'on_moon_tidally_locked_to_planet_tidally_locked_to_star'),
])
def test_moon_tidal_decrease_uses_source_slot_and_its_parent_chain(slot, facility_id, condition) -> None:
    hosts = (body('star', 'other_star'),
             body('parent', 'gas_giant', parent_ref='star', tidally_locked=True),
             body(parent_ref='parent', tidally_locked=True))
    sites = (site('port'), site('farm', facility_id, slot_kind=slot))
    result = evaluate_economies(sites, hosts, EconomySystem('common'), 'now')
    link = economy(result.ports[0], 'Agriculture').items[0]
    assert link.modifiers == ((condition, -0.4),)
    assert link.value == 0.1 and link.floored
    unlocked = (hosts[0], replace(hosts[1], tidally_locked=False), hosts[2])
    without_chain = evaluate_economies(sites, unlocked, EconomySystem('common'), 'now')
    assert economy(without_chain.ports[0], 'Agriculture').items[0].value == 0.4


def test_tourism_stellar_boosts_and_signals_follow_pinned_condition_order() -> None:
    result = evaluate_economies(
        (site('port'), site('tourism', 'tourism_installation'),
         site('hightech', 'orbital_installation_relay_station')),
        (body(kind='rocky_body', geological_signals=2, biological_signals=1),),
        EconomySystem('common', has_black_hole=True, has_white_dwarf=True, has_neutron_star=True), 'now',
    )
    tourism = economy(result.ports[0], 'Tourism').items[0]
    assert tourism.value == 2.8
    assert tourism.modifiers == (
        ('system_with_black_hole', 0.4), ('on_or_orbiting_body_with_geologicals', 0.4),
        ('on_or_orbiting_body_with_organics', 0.4), ('system_with_white_dwarf', 0.4),
        ('system_with_neutron_star', 0.4),
    )
    assert economy(result.ports[0], 'HighTech').items[0].value == 1.2


def test_no_economy_facilities_send_no_strong_or_weak_links() -> None:
    sites = (site('port'), site('local', 'orbital_installation_government'),
             site('distant', 'orbital_installation_comm_station', 'remote'))
    result = evaluate_economies(sites, (body(), body('remote')), EconomySystem('common'), 'now')
    assert strengths(result.ports[0]) == {'Extraction': 1.0}
    assert [item.kind for item in result.ports[0].economies[0].items] == ['own']


def test_twenty_weak_links_are_exact_and_results_are_deterministic_sorted_and_frozen() -> None:
    sites = (site('port'), *weak_sources(Agriculture=20, Military=20, HighTech=20))
    hosts = (body(kind='rocky_body'), body('remote'))
    system = EconomySystem('common')
    first = evaluate_economies(sites, hosts, system, 'now')
    assert first == evaluate_economies(sites, hosts, system, 'now')
    assert strengths(first.ports[0]) == {'Agriculture': 1.0, 'HighTech': 1.0, 'Military': 1.0, 'Refinery': 1.0}
    assert [value.name for value in first.ports[0].economies] == ['Agriculture', 'HighTech', 'Military', 'Refinery']
    for value in first.ports[0].economies:
        assert value.strength == round(sum(item.value for item in value.items), 2)
        for item in value.items:
            if item.kind == 'weak_links':
                assert item.count == 20 and item.value == 1.0
    note = EconomyNote('example', None, None, 'Example')
    objects = (hosts[0], system, first, first.ports[0], first.ports[0].economies[0],
               first.ports[0].economies[0].items[0], note)
    expected_types = (EconomyBody, EconomySystem, EconomyResult, PortEconomy, Economy, EconomyItem, EconomyNote)
    for obj, expected_type in zip(objects, expected_types, strict=True):
        assert isinstance(obj, expected_type)
        field = next(iter(obj.__dataclass_fields__))
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field, None)


def test_all_explanation_rule_ids_resolve_in_the_pinned_rulebook() -> None:
    sites = (site('orbital'), site('planetary', 'planetary_port'),
             site('converted', 'planetary_port'), site('farm', 'orbital_installation_space_farm'),
             site('surface', 'surface_settlement_agriculture_t1_s'), *weak_sources(Agriculture=1))
    result = evaluate_economies(sites, (body(biological_signals=1), body('remote')),
                                EconomySystem('common'), 'now')
    for port in result.ports:
        for value in port.economies:
            for item in value.items:
                assert item.rule_ids
                assert len(item.rule_ids) == len(set(item.rule_ids))
                for rule_id in item.rule_ids:
                    assert rule(rule_id).id == rule_id


@pytest.mark.parametrize(('kind', 'expected'), [
    ('black_hole', {'HighTech': 1.0, 'Tourism': 1.0}),
    ('neutron_star', {'HighTech': 1.0, 'Tourism': 1.0}),
    ('white_dwarf', {'HighTech': 1.0, 'Tourism': 1.0}),
    ('brown_dwarf', {'Military': 1.0}),
    ('other_star', {'Military': 1.0}),
    ('earth_like_world', {'Agriculture': 1.4, 'HighTech': 1.4, 'Military': 1.0, 'Tourism': 1.4}),
    ('water_world', {'Agriculture': 1.4, 'Tourism': 1.4}),
    ('ammonia_world', {'HighTech': 1.4, 'Tourism': 1.4}),
    ('gas_giant', {'HighTech': 1.0, 'Industrial': 1.0}),
    ('high_metal_content_world', {'Extraction': 1.0}),
    ('metal_rich_body', {'Extraction': 1.0}),
    ('rocky_ice_body', {'Industrial': 1.0, 'Refinery': 1.0}),
    ('rocky_body', {'Refinery': 1.0}),
    ('icy_body', {'Industrial': 1.0}),
])
def test_every_body_kind_inherits_its_pinned_economies(kind, expected) -> None:
    result = evaluate_economies((site('port'),), (body(kind=kind),), EconomySystem('common'), 'now')
    assert strengths(result.ports[0]) == expected


def test_star_belts_add_extraction() -> None:
    result = evaluate_economies((site('port'),), (body(kind='other_star', has_rings=True),),
                                EconomySystem('common'), 'now')
    assert strengths(result.ports[0]) == {'Military': 1.0, 'Extraction': 1.0}


def test_unknown_kind_does_not_inherit_even_with_known_rings_or_signals() -> None:
    host = body(kind=None, has_rings=True, geological_signals=3, biological_signals=2, volcanism=True)
    result = evaluate_economies(
        (site('port'), site('mine', 'orbital_installation_mining_outpost')),
        (host,), EconomySystem('pristine'), 'now',
    )
    assert strengths(result.ports[0]) == {'Extraction': 1.2}
    assert [item.kind for item in result.ports[0].economies[0].items] == ['strong_link']
    assert note_codes(result) == {'unknown_body_kind'}


def test_link_items_keep_build_order_with_direct_links_before_passed_links() -> None:
    sites = (
        site('port', 'dodecahedron_t3'), site('planetary', 'planetary_port'),
        site('surface-z', 'surface_settlement_extraction_t1_s'),
        site('orbital-z', 'orbital_installation_mining_outpost'),
        site('surface-a', 'surface_settlement_extraction_t1_s'),
        site('orbital-a', 'orbital_installation_mining_outpost'),
        site('weak-z', 'orbital_installation_mining_outpost', 'remote'),
        site('weak-a', 'orbital_installation_mining_outpost', 'remote'),
    )
    result = evaluate_economies(sites, (body(), body('remote')), EconomySystem('common'), 'now')
    items = economy(result.ports[0], 'Extraction').items
    assert [(item.kind, item.source_site_ids) for item in items] == [
        ('own', ('port',)),
        ('strong_link', ('orbital-z',)), ('strong_link', ('orbital-a',)),
        ('passed_on_link', ('surface-z',)), ('passed_on_link', ('surface-a',)),
        ('planetary_port_link', ('planetary',)), ('weak_links', ('weak-z', 'weak-a')),
    ]


def test_converted_orbital_port_links_only_to_the_orbital_main() -> None:
    sites = (site('converted'), site('main', 'orbis_t3'), site('planetary', 'planetary_port'))
    result = evaluate_economies(sites, (body(),), EconomySystem('common'), 'now')
    converted, main, planetary = result.ports
    assert converted.role == 'converted' and converted.supports == 'main'
    assert strengths(main) == {'Extraction': 3.0}
    assert strengths(planetary) == {'Extraction': 1.0}
    assert all(item.kind != 'passed_on_link' for item in economy(main, 'Extraction').items)


def test_without_ports_no_links_or_port_results_are_created() -> None:
    result = evaluate_economies(
        (site('farm', 'orbital_installation_space_farm'),), (body(),), EconomySystem('common'), 'now',
    )
    assert result.ports == () and result.notes == ()


def test_binary_flag_changes_link_provenance_only() -> None:
    sites = (site('port'), site('farm', 'orbital_installation_space_farm'))
    host = body(kind='water_world', parent_ref='star', tidally_locked=True)
    system = EconomySystem('common')
    ordinary = evaluate_economies(sites, (host, body('star', 'other_star')), system, 'now')
    binary = evaluate_economies(sites, (replace(host, in_binary_pair=True), body('star', 'other_star')),
                               system, 'now')
    assert strengths(binary.ports[0]) == strengths(ordinary.ports[0])
    ordinary_link = economy(ordinary.ports[0], 'Agriculture').items[1]
    binary_link = economy(binary.ports[0], 'Agriculture').items[1]
    assert ordinary_link.modifiers == binary_link.modifiers
    assert 'RB-0028' not in ordinary_link.rule_ids and 'RB-0028' in binary_link.rule_ids


@pytest.mark.parametrize(('facility_id', 'condition'), [
    ('coriolis_station', 'orbiting_moon_tidally_locked_to_planet_tidally_locked_to_star'),
    ('planetary_port', 'on_moon_tidally_locked_to_planet_tidally_locked_to_star'),
])
def test_own_agriculture_uses_port_slot_for_moon_tidal_condition(facility_id, condition) -> None:
    hosts = (
        body('star', 'other_star'), body('parent', 'gas_giant', parent_ref='star', tidally_locked=True),
        body(kind='water_world', parent_ref='parent', tidally_locked=True),
    )
    result = evaluate_economies((site('port', facility_id),), hosts, EconomySystem('common'), 'now')
    item = economy(result.ports[0], 'Agriculture').items[0]
    assert item.value == 1.0
    assert item.modifiers == (('orbiting_water_world', 0.4), (condition, -0.4))


def test_passed_on_floored_link_keeps_value_and_records_one_source_note() -> None:
    sites = (site('orbital'), site('planetary', 'planetary_port'),
             site('settlement', 'surface_settlement_extraction_t1_s'))
    result = evaluate_economies(sites, (body(),), EconomySystem('low'), 'now')
    passed = next(item for item in economy(result.ports[0], 'Extraction').items if item.kind == 'passed_on_link')
    direct = next(item for item in economy(result.ports[1], 'Extraction').items if item.kind == 'strong_link')
    assert passed.floored and direct.floored and passed.value == direct.value == 0.1
    notes = [note for note in result.notes if note.code == 'floor_applied']
    assert len(notes) == 1 and notes[0].site_id == 'settlement'


@pytest.mark.parametrize('view', ['later', '', None])
def test_invalid_view_is_rejected(view) -> None:
    with pytest.raises(ValueError, match='view'):
        evaluate_economies((site('port'),), (body(),), EconomySystem('common'), view)


@pytest.mark.parametrize(('sites', 'bodies', 'message'), [
    ((site('duplicate'), site('duplicate')), (body(),), 'Site ids'),
    ((site('port'),), (body(), body()), 'Body references'),
])
def test_duplicate_identifiers_are_rejected(sites, bodies, message) -> None:
    with pytest.raises(ValueError, match=message):
        evaluate_economies(sites, bodies, EconomySystem('common'), 'now')


def test_unknown_system_reserve_is_noted_with_no_counted_sites() -> None:
    result = evaluate_economies((), (), EconomySystem(), 'now')
    assert result.ports == ()
    assert len(result.notes) == 1
    assert result.notes[0].code == 'unknown_reserve'
    assert result.notes[0].site_id is result.notes[0].body_ref is None


def test_strong_link_tier_strength_is_read_through_rule_lookup(monkeypatch) -> None:
    import importlib
    from types import SimpleNamespace

    module = importlib.import_module('edfinder_api.cpe.economy')
    original_rule = module.rule

    def changed_rule(rule_id):
        original = original_rule(rule_id)
        if rule_id == 'RB-0015':
            value = dict(original.value)
            value['strong_by_tier'] = {**value['strong_by_tier'], '1': 0.6}
            return SimpleNamespace(value=value)
        return original

    monkeypatch.setattr(module, 'rule', changed_rule)
    result = evaluate_economies(
        (site('port'), site('farm', 'orbital_installation_space_farm')),
        (body(kind='water_world'),), EconomySystem('common'), 'now',
    )
    agriculture = economy(result.ports[0], 'Agriculture')
    assert agriculture.strength == 2.4
    assert agriculture.items[1].base == 0.6
    assert agriculture.items[1].value == 1.0
