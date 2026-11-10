"""Verify frozen V4 sources, rules, outputs and optional disposable PG18 rebuild."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'apps' / 'api' / 'src'))

from domain import ratings_v4 as scorer  # noqa: E402
from domain.ratings_v4_canonical import (  # noqa: E402
    adapt_canonical_export, canonical_lineage, load_source_fixture,
)
from scripts.ratings_v4.generation import (  # noqa: E402
    bootstrap, digest, materialize, normalized, schema_name, stage, validate,
)

FREEZE_DIRECTORY = ROOT / 'docs/development/ratings-v4-freeze'


def text_sha256(path: Path) -> str:
    """Source text is hashed as UTF-8 with LF, independently of Git autocrlf."""
    return hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()


def coefficients() -> dict:
    return {
        'native': scorer.NATIVE_BASE, 'modifier': scorer.MODIFIER_BASE,
        'native_and_modifier': scorer.NATIVE_AND_MODIFIER_BASE,
        'strong_link_step': scorer.STRONG_LINK_STEP, 'strong_link_cap_each_sign': scorer.STRONG_LINK_CAP,
        'system_weight_hundredths': list(scorer.SYSTEM_WEIGHT_HUNDREDTHS),
        'rounding': scorer.ROUNDING_POLICY,
        'specialisation_competition_penalties': [0, 8, 20, 35],
        'preferred_specialisation_bonus': 5,
        'clean_additional_candidate_bonus': 3, 'clean_depth_cap': 10,
    }


def cohort_snapshot(sources, systems) -> dict:
    names = {row['id64']: row['name'] for row in sources[0]['systems']}
    rows = []
    for system_id, facts in sorted(systems.items()):
        ratings = scorer.rate_system_facts(facts)
        summaries = {}
        for economy, rating in ratings.items():
            summary = asdict(rating)
            summary['contribution_count'] = len(summary.pop('contributions'))
            candidates = summary.pop('specialisation_candidates')
            summary['specialisation_candidate_count'] = len(candidates)
            summary['constraint_count'] = sum(len(item['constraints']) for item in candidates)
            summary.pop('explanation')
            summaries[economy] = summary
        rows.append({'system_id64': system_id, 'name': names[system_id], 'body_count': len(facts.bodies),
                     'ratings': summaries})
    return normalized({'scorer_version': scorer.SCORER_VERSION, 'mechanics_version': scorer.MECHANICS_VERSION,
                       'source_lineage': canonical_lineage(*sources), 'systems': rows})


def verify_files(manifest: dict, root: Path = ROOT) -> None:
    for relative, expected in manifest['text_files_sha256_lf'].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('manifest path outside repository')
        if text_sha256(path) != expected:
            raise ValueError(f'frozen source/rule drift: {relative}')


def verify_contract() -> tuple[dict, dict, dict]:
    manifest = json.loads((FREEZE_DIRECTORY / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['status'] != 'FROZEN' or manifest['scorer_version'] != scorer.SCORER_VERSION:
        raise ValueError('freeze/scorer version mismatch')
    if manifest['mechanics_version'] != scorer.MECHANICS_VERSION or manifest['coefficients'] != coefficients():
        raise ValueError('frozen mechanics/coefficient drift')
    if manifest['economies'] != list(scorer.ECONOMIES):
        raise ValueError('frozen economy identity drift')
    verify_files(manifest)
    sources = load_source_fixture(ROOT / 'tests/fixtures/ratings_v4_sources')
    systems = adapt_canonical_export(*sources)
    cohort = cohort_snapshot(sources, systems)
    if manifest['source_lineage'] != cohort['source_lineage']:
        raise ValueError('frozen source lineage drift')
    retained = json.loads((FREEZE_DIRECTORY / 'cohort.json').read_text(encoding='utf-8'))
    if cohort != retained or digest(cohort) != manifest['cohort_normalized_sha256']:
        raise ValueError('frozen cohort drift')
    if digest(materialize(systems)['system_input']) != manifest['derived_input_sha256']:
        raise ValueError('frozen derived-input drift')
    return manifest, systems, cohort


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres', action='store_true', help='Run a new disposable PG18 rebuild using RATINGS_V4_VALIDATION_DATABASE_URL')
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    manifest, systems, cohort = verify_contract()
    receipt = {'status': 'VERIFIED', 'scorer_version': scorer.SCORER_VERSION,
               'systems': len(systems), 'ratings': 7 * len(systems), 'published': False}
    if args.postgres:
        import psycopg
        url = os.environ['RATINGS_V4_VALIDATION_DATABASE_URL']
        schema = schema_name('ratings_v4_validation_' + uuid4().hex)
        with psycopg.connect(url, autocommit=True) as connection:
            if connection.info.server_version // 10000 != 18:
                raise ValueError('freeze proof requires PostgreSQL 18')
            bootstrap(connection, schema)
            receipt = validate(connection, schema, stage(connection, schema, systems, {
                'canonical_generation': cohort['source_lineage']['canonical_schema'],
                'source_lineage': cohort['source_lineage'],
                'freeze_manifest_sha256': digest(manifest),
            }))
            receipt['disposable_schema'] = schema
            receipt['freeze_manifest_sha256'] = digest(manifest)
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
