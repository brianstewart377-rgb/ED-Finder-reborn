from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import random
import re
import sys
import uuid

import pytest

import shared_contracts.v3_system_archetype_model as canonical_model
from scripts.v3_system_archetype_model import (
    ARCHETYPE_KEYS,
    ARCHETYPE_VERSION,
    OPPORTUNITY_READ_SQL,
    VECTOR_READ_SQL,
    SystemVectorRow,
    SystemVectors,
    archetype_model_sha256,
    confidence_to_ppm,
    explain_raw_system,
    fit_all,
    score_raw_system,
    search_uncertainty_factor,
    summarise,
    tier_of,
    weighted_key_e4,
    weighted_potential,
)


MODEL_PATH = Path(canonical_model.__file__)
EXPECTED_ARCHETYPE_MODEL_SHA256 = (
    'dbf62b783fb2220d92202ab84c7f9adf556ed8be43218716c5a69e052c552533'
)


def _vectors(**over):
    base = dict(
        pot=(0,) * 7,
        qual=(None,) * 7,
        qual_min=(0,) * 7,
        qual_max=(0,) * 7,
        completeness=(1.0,) * 7,
        confidence=(1.0,) * 7,
        opp={},
    )
    base.update(over)
    return SystemVectors(**base)


def _raw_vector(**over):
    base = dict(
        system_id64=7,
        potential=(0,) * 7,
        quality=(None,) * 7,
        quality_min=(0,) * 7,
        quality_max=(0,) * 7,
        completeness=(10_000,) * 7,
        confidence=(10_000,) * 7,
    )
    base.update(over)
    return SystemVectorRow(**base)


def _digest_for_source(tmp_path: Path, source: str) -> str:
    path = tmp_path / f'model_{uuid.uuid4().hex}.py'
    path.write_text(source, encoding='utf-8')
    name = f'_archetype_digest_test_{uuid.uuid4().hex}'
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        return module.archetype_model_sha256()
    finally:
        sys.modules.pop(name, None)


def _changed(source: str, old: str, new: str) -> str:
    assert source.count(old) == 1, old
    return source.replace(old, new)


def test_tier_thresholds():
    assert [tier_of(score) for score in (88, 87, 76, 60, 45, 44, 0)] == [
        'S', 'A', 'A', 'B', 'C', 'D', 'D',
    ]


def test_version_and_ordered_key_set():
    assert ARCHETYPE_VERSION == 'v3-archetype-4'
    assert ARCHETYPE_KEYS == (
        'paradise',
        'mining_hub',
        'manufacturing_hub',
        'megacomplex',
        'research_hub',
        'stronghold',
        'population_capital',
        'flexible',
    )


def test_paradise_strong_both_anchors_high_score():
    vectors = _vectors(
        pot=(90, 0, 0, 0, 0, 90, 0),
        qual=(90, None, None, None, None, 90, None),
        opp={1: (10, 80.0), 6: (10, 80.0)},
    )
    fit = {item.key: item for item in fit_all(vectors)}['paradise']
    assert fit.score >= 80 and fit.tier in ('A', 'S')
    assert fit.confidence == 1.0


def test_weakest_link_caps_score():
    vectors = _vectors(
        pot=(90, 0, 0, 0, 0, 10, 0),
        opp={1: (10, 80.0), 6: (10, 80.0)},
    )
    fit = {item.key: item for item in fit_all(vectors)}['paradise']
    assert fit.score < 40


def test_null_quality_uses_midpoint_and_lowers_confidence():
    vectors = _vectors(
        pot=(80, 0, 0, 0, 0, 80, 0),
        qual=(None,) * 7,
        qual_min=(40, 0, 0, 0, 0, 40, 0),
        qual_max=(60, 0, 0, 0, 0, 60, 0),
        opp={1: (8, 70.0), 6: (8, 70.0)},
    )
    fit = {item.key: item for item in fit_all(vectors)}['paradise']
    assert fit.confidence < 1.0
    assert fit.explanation['spec'] == round(0.85 + 0.15 * 0.5, 4)


def test_summary_picks_primary_and_secondary_by_score():
    vectors = _vectors(
        pot=(90, 90, 90, 0, 0, 0, 0),
        qual=(90, 90, 90, None, None, None, None),
        opp={1: (9, 80.0), 2: (9, 80.0), 3: (9, 80.0)},
    )
    fits = fit_all(vectors)
    summary = summarise(fits)
    assert summary['best_colony_potential'] == max(fit.score for fit in fits)
    assert summary['primary_archetype'] in (
        'manufacturing_hub', 'paradise', 'population_capital',
    )
    assert 0.0 <= summary['archetype_confidence'] <= 1.0


def test_flexible_rewards_breadth():
    broad = _vectors(
        pot=(60, 60, 60, 60, 0, 0, 0),
        qual=(60, 60, 60, 60, None, None, None),
    )
    narrow = _vectors(
        pot=(90, 0, 0, 0, 0, 0, 0),
        qual=(90, None, None, None, None, None, None),
    )
    broad_fit = {item.key: item for item in fit_all(broad)}['flexible']
    narrow_fit = {item.key: item for item in fit_all(narrow)}['flexible']
    assert broad_fit.score > narrow_fit.score


def test_confidences_retain_existing_six_decimal_semantics():
    anchored = _vectors(
        pot=(90, 0, 0, 0, 0, 90, 0),
        qual=(90, None, None, None, None, 90, None),
        opp={1: (10, 80.0), 6: (10, 80.0)},
        completeness=(0.5, 0, 0, 0, 0, 0.5, 0),
    )
    assert {item.key: item for item in fit_all(anchored)}[
        'paradise'
    ].confidence == 0.5
    flexible = _vectors(
        pot=(60,) * 7,
        qual=(60,) * 7,
        completeness=(0.5,) * 7,
    )
    assert {item.key: item for item in fit_all(flexible)}[
        'flexible'
    ].confidence == 0.5


def test_weighted_potential_is_exact_and_uses_search_minima():
    vectors = _vectors(
        completeness=(1.0, 1.0, 0.25, 1.0, 1.0, 1.0, 1.0),
        confidence=(1.0,) * 7,
    )
    assert search_uncertainty_factor(vectors) == 0.25
    assert weighted_potential(61, vectors) == 15.25
    assert weighted_potential(100, _vectors()) == 100.0
    assert weighted_potential(
        100, _vectors(completeness=(0.0,) * 7)
    ) == 0.0


def test_confidence_half_up_is_applied_after_six_decimal_model_rounding():
    assert confidence_to_ppm(0.0) == 0
    assert confidence_to_ppm(0.5) == 500_000
    assert confidence_to_ppm(1.0) == 1_000_000
    assert round(0.9999 * 0.9950, 6) == 0.9949
    assert confidence_to_ppm(round(0.9999 * 0.9950, 6)) == 994_900


def test_confidence_ppm_conversion_is_exact_for_every_model_output():
    for ppm in range(1_000_001):
        assert confidence_to_ppm(round(ppm / 1_000_000, 6)) == ppm


def test_weighted_key_half_boundary_and_endpoints():
    assert weighted_key_e4(0, 0, 0) == 0
    assert weighted_key_e4(1, 50, 1_000_000) == 1
    assert weighted_key_e4(100, 1_000_000, 1_000_000) == 1_000_000


def test_equal_weighted_keys_are_resolved_by_system_id64():
    key = weighted_key_e4(76, 500_000, 800_000)
    candidates = [(2, key), (1, key)]
    assert sorted(candidates, key=lambda item: (-item[1], item[0])) == [
        (1, key),
        (2, key),
    ]


def test_weighted_key_matches_floating_half_up_for_500000_random_triples():
    generator = random.Random(0xF2D)
    disagreements = 0
    compared = 0
    for _ in range(500_000):
        score = generator.randrange(101)
        confidence_ppm = generator.randrange(1_000_001)
        completeness_ppm = generator.randrange(1_000_001)
        numerator = score * confidence_ppm * completeness_ppm
        if numerator % 100_000_000 == 50_000_000:
            continue
        floating = math.floor(numerator / 100_000_000.0 + 0.5)
        disagreements += weighted_key_e4(
            score, confidence_ppm, completeness_ppm
        ) != floating
        compared += 1
    assert compared > 499_000
    assert disagreements == 0


def test_score_raw_system_returns_every_wide_persisted_model_column():
    row = _raw_vector(
        potential=(90, 0, 0, 0, 0, 90, 0),
        quality=(90, None, None, None, None, 90, None),
        quality_min=(90, 0, 0, 0, 0, 90, 0),
        quality_max=(90, 0, 0, 0, 0, 90, 0),
        completeness=(10_000, 9_000, 9_000, 9_000, 9_000, 10_000, 9_000),
    )
    result = score_raw_system(row, ((1, 3, 4), (6, 2, 160)))
    expected = {
        name
        for key in ARCHETYPE_KEYS
        for name in (
            f'{key}_score',
            f'{key}_tier',
            f'{key}_confidence_ppm',
            f'{key}_weighted_e4',
        )
    }
    expected.update({
        'primary_archetype',
        'secondary_archetype',
        'best_colony_potential',
        'best_tier',
        'archetype_confidence_ppm',
        'weighted_potential',
        'completeness_ppm',
    })
    assert set(result) == expected
    assert result['completeness_ppm'] == 900_000
    for key in ARCHETYPE_KEYS:
        assert result[f'{key}_weighted_e4'] == weighted_key_e4(
            result[f'{key}_score'],
            result[f'{key}_confidence_ppm'],
            900_000,
        )


def test_integer_opportunity_mean_is_divided_once_in_model():
    row = _raw_vector(
        potential=(90, 0, 0, 0, 0, 90, 0),
        quality=(90, None, None, None, None, 90, None),
        quality_min=(90, 0, 0, 0, 0, 90, 0),
        quality_max=(90, 0, 0, 0, 0, 90, 0),
    )
    raw = score_raw_system(row, ((1, 3, 4), (6, 3, 4)))
    scaled = _vectors(
        pot=row.potential,
        qual=row.quality,
        qual_min=row.quality_min,
        qual_max=row.quality_max,
        opp={1: (3, 4 / 3), 6: (3, 4 / 3)},
    )
    fit = {item.key: item for item in fit_all(scaled)}['paradise']
    assert raw['paradise_score'] == fit.score


def test_explain_raw_system_assembles_the_same_eight_explanations():
    row = _raw_vector(
        potential=(90, 80, 70, 60, 50, 90, 80),
        quality=(90, 80, None, 60, 50, 90, 80),
        quality_min=(90, 80, 30, 60, 50, 90, 80),
        quality_max=(90, 80, 70, 60, 50, 90, 80),
    )
    opportunity_groups = ((1, 3, 4), (2, 2, 120), (6, 4, 320))
    expected = {
        fit.key: fit.explanation
        for fit in fit_all(canonical_model._scaled_vectors(
            row,
            opportunity_groups,
        ))
    }
    assert explain_raw_system(row, opportunity_groups) == expected


def test_shared_sql_contracts_are_raw_integer_reads():
    collapsed_vector = ' '.join(VECTOR_READ_SQL.split()).lower()
    collapsed_opportunity = ' '.join(OPPORTUNITY_READ_SQL.split()).lower()
    assert 'potential, v.quality, v.quality_min, v.quality_max' in collapsed_vector
    assert 'v.completeness, v.confidence' in collapsed_vector
    assert '/ 10000' not in collapsed_vector
    assert 'count(*)::int' in collapsed_opportunity
    assert 'sum(o.local_score)::bigint' in collapsed_opportunity
    assert 'avg(' not in collapsed_opportunity
    assert 'double precision' not in collapsed_opportunity
    assert 'group by o.system_id64, o.economy_ordinal' in collapsed_opportunity
    expected_parameters = {'generation_id', 'chunk_ordinal', 'system_id64'}
    assert set(re.findall(r'%\(([^)]+)\)s', VECTOR_READ_SQL)) == expected_parameters
    assert set(re.findall(r'%\(([^)]+)\)s', OPPORTUNITY_READ_SQL)) == expected_parameters


def test_canonical_model_digest_is_computable_and_stable():
    digest = archetype_model_sha256()
    assert len(digest) == 64
    assert int(digest, 16) >= 0
    assert digest == archetype_model_sha256()
    assert digest == EXPECTED_ARCHETYPE_MODEL_SHA256


def test_model_digest_excludes_version_and_explanation_construction(tmp_path):
    source = MODEL_PATH.read_text(encoding='utf-8')
    baseline = _digest_for_source(tmp_path, source)
    version_edit = _changed(
        source,
        "ARCHETYPE_VERSION = 'v3-archetype-4'",
        "ARCHETYPE_VERSION = 'storage-only-version'",
    )
    explanation_edit = _changed(
        source,
        "'breadth': breadth,",
        "'breadth': breadth + 999,",
    )
    assert _digest_for_source(tmp_path, version_edit) == baseline
    assert _digest_for_source(tmp_path, explanation_edit) == baseline


def test_model_digest_ignores_comments_docstrings_and_formatting(tmp_path):
    source = MODEL_PATH.read_text(encoding='utf-8')
    baseline = _digest_for_source(tmp_path, source)
    edited = _changed(
        source,
        "    '''Convert an already-six-decimal model confidence with shared half-up.'''",
        "    '''A different function docstring with no scoring effect.'''",
    )
    edited = _changed(
        edited,
        '    return int(math.floor(confidence * 1_000_000 + 0.5))',
        '    return int( math.floor( confidence * 1_000_000 + 0.5 ) )  # format',
    )
    assert _digest_for_source(tmp_path, edited) == baseline


@pytest.mark.parametrize(
    ('old', 'new'),
    (
        ('ALPHA = 0.6', 'ALPHA = 0.61'),
        ('if score >= threshold:', 'if score > threshold:'),
        ('return lo if x < lo else hi if x > hi else x',
         'return lo if x <= lo else hi if x > hi else x'),
        ('/ 2.0, True', '/ 2.01, True'),
        ('core * spec * cap + synergy', 'core * spec * cap + synergy + 0.01'),
        ('breadth / BREADTH_TARGET', 'breadth / (BREADTH_TARGET + 1)'),
        ('for key in ARCHETYPE_KEYS\n', 'for key in reversed(ARCHETYPE_KEYS)\n'),
        ('return min(v.confidence) * min(v.completeness)',
         'return min(v.confidence) * min(v.completeness) * 0.99'),
        ('return _clamp(raw, 0.0, 100.0)', 'return _clamp(raw, 0.0, 99.0)'),
        ('return -fit.score, ARCHETYPE_KEYS.index(fit.key)',
         'return -fit.score, -ARCHETYPE_KEYS.index(fit.key)'),
        ("'secondary_archetype': second.key if second_score > 0 else None,",
         "'secondary_archetype': second.key if second_score >= 0 else None,"),
        ('confidence * 1_000_000 + 0.5', 'confidence * 1_000_000 + 0.4'),
        ('return min(completeness) * 100', 'return min(completeness) * 99'),
        ('numerator + 50_000_000', 'numerator + 49_999_999'),
        ('local_score_sum / row_count', 'local_score_sum / (row_count + 1)'),
        ('value / 10_000.0 for value in vector_row.confidence',
         'value / 9_999.0 for value in vector_row.confidence'),
        ("result[f'{fit.key}_score'] = fit.score",
         "result[f'{fit.key}_score'] = fit.score + 1"),
    ),
)
def test_model_digest_changes_for_every_covered_scoring_member(
    tmp_path,
    old,
    new,
):
    source = MODEL_PATH.read_text(encoding='utf-8')
    baseline = _digest_for_source(tmp_path, source)
    assert _digest_for_source(tmp_path, _changed(source, old, new)) != baseline


@pytest.mark.parametrize(
    ('old', 'new'),
    (
        ('SELECT v.system_id64, v.potential',
         'SELECT v.system_id64 + 0, v.potential'),
        ('FROM v3_derived.system_rating_vector v',
         'FROM v3_derived.system_rating_vector AS v'),
        ('WHERE v.derived_generation_id = %(generation_id)s',
         'WHERE v.derived_generation_id IS NOT DISTINCT FROM %(generation_id)s'),
        ('ORDER BY v.system_id64', 'ORDER BY v.system_id64 DESC'),
        ('SELECT o.system_id64, o.economy_ordinal, count(*)::int,',
         'SELECT o.system_id64, o.economy_ordinal, count(o.*)::int,'),
        ('sum(o.local_score)::bigint', 'sum(o.local_score + 0)::bigint'),
        ('JOIN v3_derived.system_rating_vector v',
         'INNER JOIN v3_derived.system_rating_vector v'),
        ('WHERE o.derived_generation_id = %(generation_id)s',
         'WHERE o.derived_generation_id IS NOT DISTINCT FROM %(generation_id)s'),
        ('GROUP BY o.system_id64, o.economy_ordinal',
         'GROUP BY o.economy_ordinal, o.system_id64'),
        ('ORDER BY o.system_id64, o.economy_ordinal',
         'ORDER BY o.system_id64 DESC, o.economy_ordinal'),
    ),
)
def test_model_digest_changes_for_every_sql_contract_clause(
    tmp_path,
    old,
    new,
):
    source = MODEL_PATH.read_text(encoding='utf-8')
    baseline = _digest_for_source(tmp_path, source)
    assert _digest_for_source(tmp_path, _changed(source, old, new)) != baseline


def test_model_digest_changes_for_referenced_class(tmp_path):
    source = MODEL_PATH.read_text(encoding='utf-8')
    baseline = _digest_for_source(tmp_path, source)
    edited = _changed(
        source,
        '    confidence: tuple[int, ...]\n\n    @classmethod',
        '    confidence: tuple[int, ...]\n    digest_probe: int = 0\n\n    @classmethod',
    )
    assert _digest_for_source(tmp_path, edited) != baseline


def test_model_digest_fails_closed_on_imported_repository_helper(tmp_path):
    source = MODEL_PATH.read_text(encoding='utf-8')
    edited = source.replace(
        'import math\n',
        'import math\nfrom shared_contracts import data_invariant_contracts\n',
    )
    edited = _changed(
        edited,
        '    vectors = _scaled_vectors(vector_row, opportunity_groups)',
        '    data_invariant_contracts.__name__\n'
        '    vectors = _scaled_vectors(vector_row, opportunity_groups)',
    )
    with pytest.raises(ValueError, match='unsupported imported reference'):
        _digest_for_source(tmp_path, edited)


def test_model_digest_fails_closed_on_non_dataclasses_dataclass(tmp_path):
    source = MODEL_PATH.read_text(encoding='utf-8')
    edited = source.replace(
        'import dataclasses\n',
        'import attr as dataclasses\n',
    )
    edited = edited.replace(
        '@dataclasses.dataclass(frozen=True)',
        '@dataclasses.define(frozen=True)',
    )
    edited = edited.replace('default_factory=dict', 'factory=dict')
    with pytest.raises(ValueError, match='unsupported imported reference'):
        _digest_for_source(tmp_path, edited)
