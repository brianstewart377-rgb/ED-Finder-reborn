"""Pure planner adapters preserve unknowns and canonical body topology."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/api/src'))

from edfinder_api.routers.plan_economy_facts import (  # noqa: E402
    aggregate_reserve_levels,
    economy_kind,
    economy_topology,
)


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(('body_class', 'expected'), [
    ('Earth-like world', 'earth_like_world'),
    ('Water world', 'water_world'),
    ('Ammonia world', 'ammonia_world'),
    ('Class I gas giant', 'gas_giant'),
    ('Class II gas giant', 'gas_giant'),
    ('Class III gas giant', 'gas_giant'),
    ('Class IV gas giant', 'gas_giant'),
    ('Class V gas giant', 'gas_giant'),
    ('Helium gas giant', 'gas_giant'),
    ('Helium-rich gas giant', 'gas_giant'),
    ('Gas giant with water-based life', 'gas_giant'),
    ('Gas giant with ammonia-based life', 'gas_giant'),
    ('Water giant', None),
    ('High metal content world', 'high_metal_content_world'),
    ('High metal content body', 'high_metal_content_world'),
    ('Metal-rich body', 'metal_rich_body'),
    ('Rocky ice world', 'rocky_ice_body'),
    ('Rocky ice body', 'rocky_ice_body'),
    ('Rocky body', 'rocky_body'),
    ('Icy body', 'icy_body'),
    ('Black Hole', 'black_hole'),
    ('Supermassive Black Hole', 'black_hole'),
    ('Neutron Star', 'neutron_star'),
    ('White Dwarf (DA) Star', 'white_dwarf'),
    ('White Dwarf (DAB) Star', 'white_dwarf'),
    ('White Dwarf (DAO) Star', 'white_dwarf'),
    ('White Dwarf (DAZ) Star', 'white_dwarf'),
    ('White Dwarf (DAV) Star', 'white_dwarf'),
    ('White Dwarf (DB) Star', 'white_dwarf'),
    ('White Dwarf (DBZ) Star', 'white_dwarf'),
    ('White Dwarf (DBV) Star', 'white_dwarf'),
    ('White Dwarf (DO) Star', 'white_dwarf'),
    ('White Dwarf (DOV) Star', 'white_dwarf'),
    ('White Dwarf (DQ) Star', 'white_dwarf'),
    ('White Dwarf (DC) Star', 'white_dwarf'),
    ('White Dwarf (DCV) Star', 'white_dwarf'),
    ('White Dwarf (DX) Star', 'white_dwarf'),
    ('L (Brown dwarf) Star', 'brown_dwarf'),
    ('T (Brown dwarf) Star', 'brown_dwarf'),
    ('Y (Brown dwarf) Star', 'brown_dwarf'),
    ('O (Blue-White) Star', 'other_star'),
    ('B (Blue-White) Star', 'other_star'),
    ('A (Blue-White) Star', 'other_star'),
    ('F (White) Star', 'other_star'),
    ('G (White-Yellow) Star', 'other_star'),
    ('K (Yellow-Orange) Star', 'other_star'),
    ('M (Red dwarf) Star', 'other_star'),
    ('A (Blue-White super giant) Star', 'other_star'),
    ('F (White super giant) Star', 'other_star'),
    ('G (White-Yellow super giant) Star', 'other_star'),
    ('K (Yellow-Orange giant) Star', 'other_star'),
    ('M (Red giant) Star', 'other_star'),
    ('M (Red super giant) Star', 'other_star'),
    ('T Tauri Star', 'other_star'),
    ('Herbig Ae/Be Star', 'other_star'),
    ('Wolf-Rayet Star', 'other_star'),
    ('Wolf-Rayet N Star', 'other_star'),
    ('Wolf-Rayet NC Star', 'other_star'),
    ('Wolf-Rayet C Star', 'other_star'),
    ('Wolf-Rayet O Star', 'other_star'),
    ('C Star', 'other_star'),
    ('CN Star', 'other_star'),
    ('CJ Star', 'other_star'),
    ('CH Star', 'other_star'),
    ('CHd Star', 'other_star'),
    ('MS-type Star', 'other_star'),
    ('S-type Star', 'other_star'),
    ('Belt Cluster', None),
    ('Barycentre', None),
    ('Unknown', None),
    ('Planet', None),
    ('Star', None),
    ('not a recognised body', None),
    ('', None),
    (None, None),
    ('  eArTh-LiKe   WoRlD ', 'earth_like_world'),
    ('HIGH-METAL-CONTENT-BODY', 'high_metal_content_world'),
    ('rocky-ice-body', 'rocky_ice_body'),
    ('white-dwarf', 'white_dwarf'),
])
def test_body_class_mapping(body_class, expected) -> None:
    assert economy_kind(body_class) == expected


@pytest.mark.parametrize(('spectral', 'expected'), [
    ('H', 'black_hole'), ('h0', 'black_hole'),
    ('N', 'neutron_star'), ('N7', 'neutron_star'),
    ('D', 'white_dwarf'), ('DA5', 'white_dwarf'), ('d0av', 'white_dwarf'),
    ('L', 'brown_dwarf'), ('T9', 'brown_dwarf'), ('y3', 'brown_dwarf'),
    ('O5', 'other_star'), ('B0', 'other_star'), ('A2', 'other_star'),
    ('F1', 'other_star'), ('G2', 'other_star'), ('K4', 'other_star'),
    ('M7', 'other_star'), ('C', 'other_star'), ('S', 'other_star'),
    ('  wN5  ', 'other_star'), ('unknown', 'other_star'),
    ('', None), ('  ', None), ('0123', None), (None, None),
])
def test_broad_star_spectral_mapping(spectral, expected) -> None:
    assert economy_kind('sTaR', spectral) == expected


@pytest.mark.parametrize('body_class', ['Planet', 'Unknown', 'Barycentre', 'Water giant', None])
def test_spectral_class_does_not_supply_a_non_star_kind(body_class) -> None:
    assert economy_kind(body_class, 'H0') is None


def test_subtype_takes_precedence_over_spectral_class() -> None:
    assert economy_kind('K (Yellow-Orange) Star', 'D5') == 'other_star'
    assert economy_kind('Earth-like world', 'H0') == 'earth_like_world'


def _body(body_pk, body_type='Planet', parent=None, lifecycle='ACTIVE'):
    return {
        'body_pk': body_pk,
        'body_type': body_type,
        'direct_parent_body_pk': parent,
        'lifecycle_state': lifecycle,
    }


def test_parent_and_binary_walk_skips_barycentre_and_belt_cluster() -> None:
    topology = economy_topology([
        _body(1, 'Star'),
        _body(2, 'Barycentre', 1),
        _body(3, parent=2),
        _body(4, parent=2),
        _body(5, parent=3),
        _body(6, 'Belt Cluster', 1),
        _body(7, parent=6),
        _body(8, 'Barycentre', 6),
        _body(9, parent=8),
    ])
    assert topology == {
        1: (None, False),
        2: ('1', False),
        3: ('1', True),
        4: ('1', True),
        5: ('3', False),
        6: ('1', False),
        7: ('1', False),
        8: ('1', False),
        9: ('1', False),
    }


def test_binary_pair_requires_another_active_planet_at_the_direct_barycentre() -> None:
    topology = economy_topology([
        _body(1, 'Star'), _body(2, 'barycentre', 1),
        _body(3, parent=2), _body(4, parent=2, lifecycle='RETIRED'),
        _body(5, 'Star', 2), _body(6, 'Belt Cluster', 2),
        _body(7, parent=1), _body(8, parent=1),
    ])
    assert topology[3] == ('1', False)
    assert topology[5] == ('1', False)
    assert topology[6] == ('1', False)
    assert topology[7] == ('1', False)
    assert topology[8] == ('1', False)


@pytest.mark.parametrize('bodies', [
    [_body(1, parent=1)],
    [_body(1, parent=2), _body(2, 'Barycentre', 1)],
    [_body(1, parent=2), _body(2, 'Barycentre', 3), _body(3, 'Belt Cluster', 2)],
    [_body(1, parent=404)],
])
def test_parent_walk_guards_cycles_and_missing_ancestors(bodies) -> None:
    assert economy_topology(bodies)[1][0] is None


@pytest.mark.parametrize('reserve', ['pristine', 'major', 'common', 'low', 'depleted'])
def test_reserve_aggregation_normalises_case_and_resource_suffix(reserve) -> None:
    actual = aggregate_reserve_levels(
        [_body(1), _body(2)],
        {1: {'reserve_level': reserve}, 2: {'reserve_level': f' {reserve.upper()} Resources '}},
        [{'reserve': reserve.title(), 'lifecycle_state': 'ACTIVE'}],
    )
    assert actual == (reserve, [])


def test_reserve_aggregation_can_use_only_belt_observations() -> None:
    assert aggregate_reserve_levels(
        [_body(1)], {}, [{'reserve': 'Pristine Resources', 'lifecycle_state': 'ACTIVE'}],
    ) == ('pristine', [])


def test_reserve_aggregation_leaves_missing_unknown() -> None:
    assert aggregate_reserve_levels([], {}, []) == (None, [])
    assert aggregate_reserve_levels(
        [_body(1), _body(2)], {1: {'reserve_level': None}},
        [{'reserve': None, 'lifecycle_state': 'ACTIVE'}],
    ) == (None, [])


def test_reserve_aggregation_reports_sorted_distinct_disagreement() -> None:
    actual = aggregate_reserve_levels(
        [_body(1), _body(2)],
        {1: {'reserve_level': 'Pristine'}, 2: {'reserve_level': 'Common'}},
        [
            {'reserve': 'Low Resources', 'lifecycle_state': 'ACTIVE'},
            {'reserve': 'PRISTINE', 'lifecycle_state': 'ACTIVE'},
        ],
    )
    assert actual == (None, ['reserve levels disagree: common, low, pristine'])


def test_reserve_aggregation_ignores_retired_bodies_belts_and_unmatched_mechanics() -> None:
    actual = aggregate_reserve_levels(
        [_body(1), _body(2, lifecycle='RETIRED')],
        {
            1: {'reserve_level': 'major'},
            2: {'reserve_level': 'depleted'},
            3: {'reserve_level': 'low'},
        },
        [{'reserve': 'common', 'lifecycle_state': 'RETIRED'}],
    )
    assert actual == ('major', [])
