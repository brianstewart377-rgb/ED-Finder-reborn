from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'apps' / 'api' / 'src'))

from domain.ratings_v4 import ECONOMIES, MECHANICS_VERSION, SCORER_VERSION  # noqa: E402
from scripts.ratings_v4 import verify_freeze  # noqa: E402


@pytest.fixture(scope='module')
def verified():
    return verify_freeze.verify_contract()


def test_frozen_contract_verifies_sources_coefficients_cohort_and_rebuild_inputs(verified):
    manifest, systems, cohort = verified
    assert manifest['status'] == 'FROZEN'
    assert manifest['scorer_version'] == SCORER_VERSION == '4.0.0'
    assert manifest['mechanics_version'] == MECHANICS_VERSION == 'v4-mechanics-2026-09'
    assert manifest['coefficients'] == verify_freeze.coefficients()
    assert manifest['coefficients']['system_weight_hundredths'] == [82, 11, 5, 2]
    assert manifest['coefficients']['rounding'] == 'nearest-even from exact integer hundredths'
    assert len(systems) == len(cohort['systems']) == 12
    assert sum(len(system.bodies) for system in systems.values()) == 360


def test_frozen_cohort_exposes_84_independent_economy_results_without_overall(verified):
    _, _, cohort = verified
    assert sum(len(system['ratings']) for system in cohort['systems']) == 84
    for system in cohort['systems']:
        assert set(system['ratings']) == set(ECONOMIES)
        assert 'overall_score' not in system and 'overall' not in system['ratings']
        for economy, rating in system['ratings'].items():
            assert rating['economy'] == economy
            assert type(rating['potential_score']) is int and 0 <= rating['potential_score'] <= 100
            assert 0 <= rating['confidence'] <= 1 and 0 <= rating['evidence_completeness'] <= 1
            assert rating['scorer_version'] == SCORER_VERSION
            assert rating['mechanics_version'] == MECHANICS_VERSION
            assert 0 <= rating['specialisation_quality_min'] <= rating['specialisation_quality_max'] <= 100


def test_frozen_ground_constraints_remain_unknown_without_fabricated_usable_sites(verified):
    _, systems, cohort = verified
    assert all(body.usable_ground_opportunity is None for system in systems.values() for body in system.bodies)
    refinery = {row['name']: row['ratings']['Refinery'] for row in cohort['systems']}
    no_refinery = {'Borann', 'HD 38179'}
    for name, rating in refinery.items():
        assert rating['specialisation_quality_min'] == 0
        assert rating['best_specialisation_candidate_id'] is None
        if name in no_refinery:
            assert rating['potential_score'] == rating['specialisation_quality'] == rating['specialisation_quality_max'] == 0
            assert rating['constraint_count'] == 0
        else:
            assert rating['potential_score'] > 0
            assert rating['specialisation_quality'] is None
            assert rating['specialisation_quality_max'] > 0
            assert rating['constraint_count'] > 0


def test_frozen_canonical_reserves_are_body_scoped_and_missing_observations_stay_unknown(verified):
    _, systems, _ = verified
    bodies = [body for facts in systems.values() for body in facts.bodies]
    assert all(facts.reserve_level is None for facts in systems.values())
    assert all(body.reserve_scope == 'body' for body in bodies)
    assert sum(body.reserve_level is not None for body in bodies) == 46
    assert any(body.geologicals is None for body in bodies)
    assert any(body.rings is None for body in bodies)
    assert all(body.feature_provenance['provenance'] for body in bodies)


def test_source_faithful_cohort_retains_the_twelve_documented_economy_roles(verified):
    _, _, cohort = verified
    scores = {row['name']: {economy: rating['potential_score'] for economy, rating in row['ratings'].items()}
              for row in cohort['systems']}
    wregoe = scores['Wregoe ZN-X c28-28']
    assert wregoe['Extraction'] > wregoe['Industrial'] > wregoe['Refinery'] >= 75
    praea = scores['Praea Euq WV-W b2-2']
    assert min(praea['Agriculture'], praea['Tourism']) >= 80
    assert abs(praea['Agriculture'] - praea['Tourism']) <= 1
    assert min(praea['Industrial'], praea['Extraction']) >= 80
    assert sum(score >= 75 for score in scores['Sol'].values()) >= 6
    achenar = scores['Achenar']
    assert all(achenar[economy] > achenar['Military'] for economy in ('Agriculture', 'HighTech', 'Tourism'))
    alioth = scores['Alioth']
    assert alioth['Agriculture'] >= 90 and alioth['Tourism'] > alioth['Refinery']
    maia = scores['Maia']
    assert maia['Extraction'] > maia['Tourism'] > 0
    assert min(maia['HighTech'], maia['Military']) > 0
    borann = scores['Borann']
    assert borann['Industrial'] >= 85 and borann['Extraction'] >= 80 and borann['Refinery'] == 0
    hd = scores['HD 38179']
    assert hd['Extraction'] == 100 and hd['Extraction'] > hd['Military'] >= 70
    col = scores['Col 285 Sector BW-U c3-5']
    assert col['Industrial'] >= 80 and col['Refinery'] >= 75
    smojoo = scores['Smojoo ZE-R d4-109']
    assert min(smojoo.values()) >= 70 and sum(score >= 80 for score in smojoo.values()) >= 4
    thaile = scores['Thaile HW-V e2-7']
    assert thaile['Tourism'] == max(thaile.values()) >= 95
    assert thaile['Agriculture'] >= 90 and thaile['HighTech'] >= 80
    deriv = scores['Deriv-Dar']
    assert deriv['Agriculture'] >= 90 and deriv['Tourism'] >= 80


def test_frozen_source_hash_detects_rule_changes(tmp_path):
    source = tmp_path / 'scorer.py'
    source.write_text('NATIVE_BASE = 75\n', encoding='utf-8')
    manifest = {'text_files_sha256_lf': {'scorer.py': verify_freeze.text_sha256(source)}}
    verify_freeze.verify_files(manifest, tmp_path)
    source.write_text('NATIVE_BASE = 76\n', encoding='utf-8')
    with pytest.raises(ValueError, match='frozen source/rule drift'):
        verify_freeze.verify_files(manifest, tmp_path)


def test_source_hash_normalizes_git_checkout_newlines(tmp_path):
    unix, windows = tmp_path / 'unix.py', tmp_path / 'windows.py'
    unix.write_bytes(b'NATIVE_BASE = 75\nMODIFIER_BASE = 55\n')
    windows.write_bytes(b'NATIVE_BASE = 75\r\nMODIFIER_BASE = 55\r\n')
    assert verify_freeze.text_sha256(unix) == verify_freeze.text_sha256(windows)


def test_manifest_cannot_read_outside_the_repository(tmp_path):
    root = tmp_path / 'repository'
    root.mkdir()
    with pytest.raises(ValueError, match='outside repository'):
        verify_freeze.verify_files({'text_files_sha256_lf': {'../outside.py': 'untrusted'}}, root)


@pytest.mark.parametrize(('field', 'replacement', 'message'), [
    ('status', 'CANDIDATE', 'version mismatch'),
    ('scorer_version', '4.0-candidate-4', 'version mismatch'),
    ('mechanics_version', 'unreviewed', 'mechanics/coefficient drift'),
    ('source_lineage', {'canonical_source_run_id': 'wrong-source'}, 'lineage'),
    ('economies', ['Agriculture'], 'econom'),
])
def test_unfrozen_or_mismatched_manifest_is_rejected(verified, tmp_path, monkeypatch, field, replacement, message):
    manifest = dict(verified[0])
    manifest[field] = replacement
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (tmp_path / 'cohort.json').write_text(json.dumps(verified[2]), encoding='utf-8')
    monkeypatch.setattr(verify_freeze, 'FREEZE_DIRECTORY', tmp_path)
    with pytest.raises(ValueError, match=message):
        verify_freeze.verify_contract()


def test_cohort_score_tampering_is_rejected(verified, tmp_path, monkeypatch):
    manifest, _, cohort = verified
    modified = json.loads(json.dumps(cohort))
    rating = modified['systems'][0]['ratings']['Agriculture']
    rating['potential_score'] = (rating['potential_score'] + 1) % 101
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (tmp_path / 'cohort.json').write_text(json.dumps(modified), encoding='utf-8')
    monkeypatch.setattr(verify_freeze, 'FREEZE_DIRECTORY', tmp_path)
    with pytest.raises(ValueError, match='frozen cohort drift'):
        verify_freeze.verify_contract()
