"""Pure adapters from published body facts to the planner's economy inputs.

Ratings V4 is frozen. These mappings belong to the planner and deliberately
do not import its scorer or canonical adapter.
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
