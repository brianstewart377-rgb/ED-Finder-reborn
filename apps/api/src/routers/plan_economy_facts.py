"""Pure adapters from the galaxy tables' body facts to the planner's economy
inputs.

The facts are made from the galaxy tables themselves (``galaxy_body_facts``), the
way Ratings V4 made them for its own table (v3_derived.body_mechanics), which
the Plan page read until 22 November 2026. Ratings V4 is frozen and is being
retired ("Ratings rebuild — design", build step 2). These mappings belong to
the planner and deliberately do not import its scorer or canonical adapter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

from edfinder_api.cpe.economy import BodyKind


ReserveLevel = Literal['pristine', 'major', 'common', 'low', 'depleted']
_RESERVES = frozenset({'pristine', 'major', 'common', 'low', 'depleted'})
_SKIPPED_PARENTS = frozenset({'barycentre', 'belt cluster'})
_BODY_KINDS: dict[str, BodyKind] = {
    'earth like world': 'earth_like_world',
    'water world': 'water_world',
    'ammonia world': 'ammonia_world',
    'high metal content world': 'high_metal_content_world',
    'high metal content body': 'high_metal_content_world',
    'metal rich body': 'metal_rich_body',
    'rocky ice world': 'rocky_ice_body',
    'rocky ice body': 'rocky_ice_body',
    'rocky body': 'rocky_body',
    'icy body': 'icy_body',
    'black hole': 'black_hole',
    'supermassive black hole': 'black_hole',
    'neutron star': 'neutron_star',
}


def _normalise(value: str | None) -> str:
    return ' '.join((value or '').casefold().replace('-', ' ').split())


def economy_kind(body_class: str | None, spectral_class: str | None = None) -> BodyKind | None:
    """Map a Spansh subtype (or a broad Star with spectra) to RB-0010."""
    kind = _normalise(body_class)
    if kind in _BODY_KINDS:
        return _BODY_KINDS[kind]
    if 'gas giant' in kind:
        return 'gas_giant'
    if 'white dwarf' in kind:
        return 'white_dwarf'
    if kind.endswith('(brown dwarf) star'):
        return 'brown_dwarf'
    if kind.endswith(' star'):
        return 'other_star'
    if kind != 'star':
        return None

    spectral = ''.join(char for char in (spectral_class or '') if not char.isdigit()).strip().upper()
    if spectral == 'H':
        return 'black_hole'
    if spectral == 'N':
        return 'neutron_star'
    if spectral.startswith('D'):
        return 'white_dwarf'
    if spectral in {'L', 'T', 'Y'}:
        return 'brown_dwarf'
    return 'other_star' if spectral else None


def _words(value: str | None) -> str:
    return ' '.join((value or '').casefold().split())


def galaxy_body_facts(
    bodies: Sequence[Mapping[str, Any]],
    rings: Sequence[Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Each body's facts for the planner, from the galaxy tables: its class,
    spectral class, tidal lock, volcanism, whether it is terraformable, and
    its reserve.

    Made as Ratings V4 made them (domain/ratings_v4_canonical.py): only for a
    body that is still there and is not a barycentre or a belt cluster, and a
    reserve only where the body's own rings and belts that are still there
    agree on one. On 22 November 2026 every fact of every such body of the
    twelve real test systems came out the same as Ratings V4's.

    ``bodies``: body_pk, body_type, lifecycle_state, body_class,
    spectral_class, is_tidally_locked, and the words for volcanism and
    terraforming_state. ``rings`` (rings and belts): body_pk, reserve (its
    words) and lifecycle_state.
    """
    reserves: dict[int, set[str]] = {}
    for ring in rings:
        if ring['lifecycle_state'] == 'ACTIVE' and ring['reserve'] is not None:
            reserves.setdefault(ring['body_pk'], set()).add(_words(ring['reserve']))
    facts = {}
    for body in bodies:
        if body['lifecycle_state'] != 'ACTIVE' or _words(body['body_type']).replace('_', ' ') in (
                'barycentre', 'belt cluster'):
            continue
        volcanism, terraforming = _words(body['volcanism']), _words(body['terraforming_state'])
        own = reserves.get(body['body_pk'], set())
        facts[body['body_pk']] = {
            'body_class': body['body_class'],
            'spectral_class': body['spectral_class'],
            'volcanism': volcanism != 'no volcanism' if volcanism else None,
            'terraformable': terraforming != 'not terraformable' if terraforming else None,
            'tidally_locked': body['is_tidally_locked'],
            'reserve_level': next(iter(own)) if len(own) == 1 else None,
        }
    return facts


def economy_topology(
    bodies: Sequence[Mapping[str, Any]],
) -> dict[int, tuple[str | None, bool]]:
    """Return physical parents and binary-planet flags using canonical IDs.

    Barycentres and belt clusters are topology nodes, not economy parents.
    A broken reference or cycle cannot establish an economy parent.
    """
    by_id = {body['body_pk']: body for body in bodies}
    active_planets: dict[int, set[int]] = {}
    for body in bodies:
        if body['lifecycle_state'] == 'ACTIVE' and _normalise(body['body_type']) == 'planet':
            active_planets.setdefault(body['direct_parent_body_pk'], set()).add(body['body_pk'])

    result = {}
    for body in bodies:
        body_id = body['body_pk']
        direct_parent = body['direct_parent_body_pk']
        parent = by_id.get(direct_parent)
        in_binary_pair = (
            _normalise(body['body_type']) == 'planet'
            and parent is not None
            and _normalise(parent['body_type']) == 'barycentre'
            and bool(active_planets.get(direct_parent, set()) - {body_id})
        )
        parent_ref = None
        visited = {body_id}
        while parent is not None and parent['body_pk'] not in visited:
            parent_id = parent['body_pk']
            visited.add(parent_id)
            if _normalise(parent['body_type']) not in _SKIPPED_PARENTS:
                parent_ref = str(parent_id)
                break
            parent = by_id.get(parent['direct_parent_body_pk'])
        result[body_id] = (parent_ref, in_binary_pair)
    return result


def aggregate_reserve_levels(
    bodies: Sequence[Mapping[str, Any]],
    mechanics_by_body: Mapping[int, Mapping[str, Any]],
    belts: Sequence[Mapping[str, Any]],
) -> tuple[ReserveLevel | None, list[str]]:
    """Combine active body and belt observations without guessing a reserve."""
    observed = [
        mechanics_by_body[body['body_pk']]['reserve_level']
        for body in bodies
        if body['lifecycle_state'] == 'ACTIVE' and body['body_pk'] in mechanics_by_body
    ]
    observed.extend(belt['reserve'] for belt in belts if belt['lifecycle_state'] == 'ACTIVE')
    values = {
        _normalise(value).removesuffix(' resources').strip()
        for value in observed if value is not None
    }
    values &= _RESERVES
    if len(values) > 1:
        return None, [f'reserve levels disagree: {", ".join(sorted(values))}']
    return cast(ReserveLevel | None, next(iter(values), None)), []
