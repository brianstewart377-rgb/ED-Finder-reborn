"""Recompute a V3.4 reference from retained inputs, without database access.

This is an explicitly reconstructed reference, never a claim about historical
production ratings. Nulls are passed to the unchanged legacy functions and every
legacy default is reported. Neither missing distances nor signal counts are
invented. Only the seven economy functions are evaluated; their old overall
score and unrelated dimensions are deliberately outside this comparison.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.ratings_v4 import legacy_v34_reference  # noqa: E402

LEGACY_PATH = ROOT / 'apps/importer/src/build_ratings.py'
REFERENCE_PATH = Path(legacy_v34_reference.__file__)
ECONOMIES = ('Agriculture', 'Refinery', 'Industrial', 'HighTech', 'Military', 'Tourism', 'Extraction')
FUNCTIONS = {
    '_distance_weight', 'classify_bodies', 'attenuate_economy_scores',
    *(f'score_{economy.lower()}' for economy in ECONOMIES),
}
CONSTANTS = {'RATING_VERSION', 'SCOOPABLE_STARS'}


def legacy_source_sha256(path: Path = LEGACY_PATH) -> str:
    """Universal-newline text reads give the same hash for LF/CRLF checkouts."""
    return hashlib.sha256(path.read_text(encoding='utf-8').encode('utf-8')).hexdigest()


def _reference_definitions(path: Path) -> tuple[dict[str, ast.FunctionDef], dict[str, Any]]:
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS]
    if len(nodes) != len(FUNCTIONS) or {node.name for node in nodes} != FUNCTIONS:
        raise ValueError('legacy scorer pure-function contract changed')
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in CONSTANTS:
                if target.id in constants:
                    raise ValueError(f'duplicate legacy scorer constant: {target.id}')
                constants[target.id] = ast.literal_eval(node.value)
    if set(constants) != CONSTANTS:
        raise ValueError('legacy scorer constant contract changed')
    return {node.name: node for node in nodes}, constants


def legacy_functions(path: Path = LEGACY_PATH) -> dict[str, Any]:
    """Use normally imported pure functions after verifying original-source parity."""
    original_nodes, original_constants = _reference_definitions(path)
    reference_nodes, reference_constants = _reference_definitions(REFERENCE_PATH)
    if original_constants.get('RATING_VERSION') != '3.4':
        raise ValueError('comparison requires legacy scorer version 3.4')
    if original_constants != reference_constants:
        raise ValueError('legacy scorer constants differ from the static reference')
    for name in sorted(FUNCTIONS):
        if ast.dump(original_nodes[name], include_attributes=False) != ast.dump(
            reference_nodes[name], include_attributes=False,
        ):
            raise ValueError(f'legacy scorer function differs from the static reference: {name}')
    return {**reference_constants, **{name: getattr(legacy_v34_reference, name) for name in FUNCTIONS}}


def recompute_legacy(bodies: list[dict], main_star_type: str | None) -> dict:
    legacy = legacy_functions()
    counts = legacy['classify_bodies'](bodies)
    raw = {}
    for economy in ECONOMIES:
        args = (counts, main_star_type) if economy in {'Agriculture', 'Military'} else (counts,)
        raw[economy] = legacy[f'score_{economy.lower()}'](*args)
    return {
        'pre_attenuation': raw,
        'post_attenuation': legacy['attenuate_economy_scores'](raw),
        'counts': counts,
    }


def _relation(export: dict, name: str, *, vocabulary: bool = False) -> list[dict]:
    rows = [entry for entry in export['extras'] if entry['relation'] == name
            and (entry['schema'] == 'v3_vocab') == vocabulary]
    if len(rows) != 1 or 'rows' not in rows[0]:
        raise ValueError(f'missing or ambiguous exported relation: {name}')
    return rows[0]['rows']


def _unique(rows: list[dict], key: str) -> dict:
    indexed = {row[key]: row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError(f'duplicate source identity: {key}')
    return indexed


def canonical_legacy_inputs(export: dict, dumps: dict[int, dict]) -> list[dict]:
    """Retain canonical facts; use exact API body identity only for subtype.

    Body signal rows supply actual numerical counts. Missing rows become zero
    only when signals_complete is true; otherwise they remain null until the
    legacy classifier applies its historical zero default. Belts are excluded
    from has_rings because the legacy importer accepted ring arrays only.
    """
    systems = _unique(export['systems'], 'id64')
    bodies = _unique(export['bodies'], 'body_pk')
    signal_codes = {row['signal_type_id']: row['public_code']
                    for row in _relation(export, 'signal_type', vocabulary=True)}
    terraform_codes = {row['terraforming_state_id']: row['public_code']
                       for row in _relation(export, 'terraforming_state', vocabulary=True)}
    signals: dict[int, dict] = defaultdict(dict)
    for row in _relation(export, 'body_signal_current'):
        if row['body_pk'] not in bodies:
            raise ValueError('signal row has no canonical body')
        code = signal_codes[row['signal_type_id']]
        if code in signals[row['body_pk']]:
            raise ValueError('duplicate canonical signal observation')
        signals[row['body_pk']][code] = row['signal_count']
    ring_bodies = set()
    for row in _relation(export, 'rings'):
        if row.get('lifecycle_state', 'ACTIVE') != 'ACTIVE' or row['kind'] != 'RING':
            continue
        parent = bodies.get(row['body_pk'])
        if parent is None or parent['system_id64'] != row['system_id64']:
            raise ValueError('ring ownership does not match canonical body')
        ring_bodies.add(row['body_pk'])
    result = []
    for system_id, system in sorted(systems.items()):
        dump = dumps[system_id]['system']
        if dump['id64'] != system_id:
            raise ValueError('Spansh system identity mismatch')
        source_bodies = _unique(dump['bodies'], 'id64')
        selected = sorted((body for body in bodies.values() if body['system_id64'] == system_id
                           and body.get('lifecycle_state', 'ACTIVE') == 'ACTIVE'),
                          key=lambda body: body['body_pk'])
        legacy_bodies = []
        gaps = []
        main_stars = []
        for body in selected:
            source = source_bodies.get(body['source_body_id64'])
            if source is None:
                raise ValueError(f"no exact Spansh identity for body {body['body_pk']}")
            if body['is_main_star'] is True:
                main_stars.append(body['spectral_class'])
            tid = body['terraforming_state_id']
            terraformable = None if tid is None else terraform_codes[tid] == 'terraformable'
            signal_values = signals[body['body_pk']]
            unknown_or_zero = 0 if body['signals_complete'] is True else None
            mapped = {
                'body_pk': body['body_pk'],
                'source_body_id64': body['source_body_id64'],
                'subtype': source.get('subType'),
                'is_landable': body['is_landable'],
                'is_terraformable': terraformable,
                'is_tidal_lock': body['is_tidally_locked'],
                'bio_signal_count': signal_values.get('saa_signaltype_biological', unknown_or_zero),
                'geo_signal_count': signal_values.get('saa_signaltype_geological', unknown_or_zero),
                'has_rings': True if body['body_pk'] in ring_bodies else None,
                'distance_from_star': body['distance_from_arrival_ls'],
            }
            for field, value in mapped.items():
                if value is None:
                    gaps.append({'body_pk': body['body_pk'], 'field': field})
            legacy_bodies.append(mapped)
        if len(main_stars) != 1:
            raise ValueError(f'expected one canonical main star for system {system_id}')
        main_star_type = main_stars[0][:1] if main_stars[0] else None
        if main_star_type is None:
            gaps.append({'body_pk': None, 'field': 'main_star_type'})
        result.append({
            'system_id64': system_id, 'name': system['name'], 'bodies': legacy_bodies,
            'main_star_type': main_star_type, 'legacy_defaulted_inputs': gaps,
            'inventory': {
                'retained_body_count': len(selected),
                'canonical_loaded_body_count': system['loaded_body_count'],
                'canonical_source_body_count': system['source_body_count'],
                'api_body_count': len(source_bodies),
            },
        })
    return result


def compare_scores(reference: dict, v4_scores: dict[str, int | None], *, major_delta: int = 25) -> dict:
    if set(v4_scores) != set(ECONOMIES):
        raise ValueError('V4 comparison requires exactly seven economy scores')
    deltas = {}
    for economy in ECONOMIES:
        score = v4_scores[economy]
        if score is not None and (type(score) is not int or not 0 <= score <= 100):
            raise ValueError('V4 scores must be null or integer 0-100')
        old = reference['post_attenuation'][economy]
        deltas[economy] = None if score is None else score - old
    reversals = []
    for index, first in enumerate(ECONOMIES):
        for second in ECONOMIES[index + 1:]:
            if v4_scores[first] is None or v4_scores[second] is None:
                continue
            old_gap = reference['post_attenuation'][first] - reference['post_attenuation'][second]
            new_gap = v4_scores[first] - v4_scores[second]
            if old_gap * new_gap < 0:
                reversals.append({'economies': [first, second], 'v3_4_gap': old_gap, 'v4_gap': new_gap})
    return {
        'v4_potential_scores': v4_scores,
        'delta_from_v3_4_post_attenuation': deltas,
        'major_deltas_for_review': [economy for economy, delta in deltas.items()
                                    if delta is not None and abs(delta) >= major_delta],
        'ordering_reversals_for_review': reversals,
        'review_threshold_points': major_delta,
        'review_status': 'requires_mechanics_review',
    }


def build_report(export: dict, dumps: dict[int, dict], v4_scores: dict | None = None) -> dict:
    rows = []
    for inputs in canonical_legacy_inputs(export, dumps):
        reference = recompute_legacy(inputs['bodies'], inputs['main_star_type'])
        row = {**inputs, 'v3_4': reference}
        row['legacy_default_counts'] = dict(Counter(gap['field'] for gap in inputs['legacy_defaulted_inputs']))
        if v4_scores is not None:
            row['comparison'] = compare_scores(reference, v4_scores[str(inputs['system_id64'])])
        rows.append(row)
    return {
        'format_version': 'ratings-v4-legacy-comparison-1',
        'reference_kind': 'recomputed_v3_4_from_canonical_plus_exact_api_classifications',
        'historical_production_scores': False,
        'legacy_scorer_sha256': legacy_source_sha256(),
        'legacy_scorer_hash_normalization': 'utf-8-lf',
        'canonical_schema': export['canonical_schema'],
        'canonical_source_metadata': export.get('source_metadata', []),
        'scope': 'seven economy scores before and after original global attenuation',
        'caveats': [
            'This is a frozen-input legacy formula reference, not exported production v3.4 ratings.',
            'Canonical fields and numerical signals are retained; only body subType comes from later API evidence.',
            'This does not replay the historical importer: its signal parsing differs from canonical signal observations.',
            'Null inputs remain null in retained bodies; legacy functions then apply their original false/zero defaults.',
            'Missing attached ring evidence is unknown, then defaults to false in the legacy classifier; belts are excluded.',
            'Distance is retained unchanged; the seven legacy economy functions do not consume weighted counts.',
            'Legacy reserve modifiers are absent. No universal overall score is compared.',
            'Delta and ordering flags require reviewed mechanics explanations; numerical comparison alone never certifies freeze.',
        ],
        'systems': rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--canonical-export', required=True, type=Path)
    parser.add_argument('--spansh-directory', required=True, type=Path)
    parser.add_argument('--v4-scores', type=Path, help='JSON: system id64 -> economy -> potential score')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    export = json.loads(args.canonical_export.read_text(encoding='utf-8'))
    dump_paths = {row['id64']: args.spansh_directory / f"{row['id64']}.json" for row in export['systems']}
    dumps = {system_id: json.loads(path.read_text(encoding='utf-8')) for system_id, path in dump_paths.items()}
    scores = json.loads(args.v4_scores.read_text(encoding='utf-8')) if args.v4_scores else None
    report = build_report(export, dumps, scores)
    paths = [args.canonical_export, *dump_paths.values()]
    if args.v4_scores:
        paths.append(args.v4_scores)
    report['input_sha256'] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f"Recomputed {len(report['systems'])} explicit legacy references: {args.output}")


if __name__ == '__main__':
    main()
