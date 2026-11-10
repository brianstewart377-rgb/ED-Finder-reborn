from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'apps' / 'api' / 'src'))

from domain.ratings_v4 import BodyFact, ECONOMIES, SystemFacts  # noqa: E402
from scripts.ratings_v4.generation import (  # noqa: E402
    TABLES, _content_hash, bootstrap, materialize, schema_name,
)


def known_body(candidate_id='rocky', body_class='Rocky body', **overrides):
    return replace(BodyFact(
        candidate_id, body_class, rings=False, biologicals=False, geologicals=False,
        volcanism=False, terraformable=False, tidally_locked=False,
        reserve_scope='body', feature_provenance={'provenance': 'test:synthetic-generation'},
    ), **overrides)


def facts(*bodies):
    return SystemFacts(bodies=bodies, body_inventory_complete=True)


def test_materialization_persists_all_seven_ratings_with_nullable_constraints():
    rows = materialize({42: facts(known_body())})
    assert set(rows) == set(TABLES)
    assert len(rows['system_input']) == 1
    ratings = {row[1]: row for row in rows['system_economy_rating']}
    assert set(ratings) == set(ECONOMIES)
    refinery = ratings['Refinery']
    assert refinery[2:6] == [62, None, 0, 100]
    assert refinery[-1]['specialisation_quality'] is None
    constraint = next(row for row in rows['economy_rating_contributor']
                      if row[4] == 'SPECIALISATION_CONSTRAINT')
    assert constraint[1:4] == ['Refinery', 'rocky', 'refinery-usable-ground-opportunity']
    assert constraint[-1]['satisfied'] is None


@pytest.mark.parametrize(('ground', 'quality'), [(True, 100), (False, 0), (None, None)])
def test_materialization_preserves_constraint_state_without_changing_potential(ground, quality):
    rows = materialize({42: facts(known_body(usable_ground_opportunity=ground))})
    rating = next(row for row in rows['system_economy_rating'] if row[1] == 'Refinery')
    assert rating[2] == 62
    assert rating[3] == quality
    assert rating[-1]['specialisation_candidates'][0]['constraints'][0]['satisfied'] is ground


def test_mechanics_features_are_unique_per_candidate_without_economy_weights():
    body = known_body('elw', 'Earth-like world', rings=True, biologicals=True, geologicals=True)
    rows = materialize({42: facts(body)})
    keys = [tuple(row[:-1]) for row in rows['mechanics_feature']]
    assert len(keys) == len(set(keys))
    assert all('weight' not in row[-1] for row in rows['mechanics_feature'])
    exotics = next(row[-1] for row in rows['mechanics_feature'] if row[2] == 'exotic_star')
    assert exotics['known'] is True
    assert exotics['value'] is False
    for row in rows['economy_opportunity']:
        assert all('weight' in feature for feature in row[-1]['evidence'])


def test_identical_body_duplicates_cannot_duplicate_materialized_primary_keys():
    body = known_body()
    single = materialize({42: facts(body)})
    duplicates = materialize({42: facts(body, body, body)})
    assert duplicates == single
    assert _content_hash(duplicates) == _content_hash(single)


def test_conflicting_body_duplicates_are_rejected_before_staging():
    body = known_body()
    with pytest.raises(ValueError, match='conflict|duplicate'):
        materialize({42: facts(body, replace(body, rings=True))})


def test_body_and_system_input_order_do_not_change_rows_or_hash():
    first = known_body('a')
    second = known_body('b', 'Water world')
    original = materialize({42: facts(first, second), 17: facts(first)})
    reordered = materialize({17: facts(first), 42: facts(second, first)})
    assert original == reordered
    assert _content_hash(original) == _content_hash(reordered)


def test_content_hash_is_independent_of_database_row_order():
    rows = materialize({42: facts(known_body(), known_body('water', 'Water world'))})
    assert _content_hash(rows) == _content_hash({name: list(reversed(values)) for name, values in rows.items()})


def test_unknown_and_observed_absence_remain_distinct_in_materialized_inputs_and_hash():
    missing = known_body(biologicals=None)
    absent = replace(missing, biologicals=False)
    unknown_rows = materialize({42: facts(missing)})
    absent_rows = materialize({42: facts(absent)})
    assert _content_hash(unknown_rows) != _content_hash(absent_rows)
    unknown_bio = next(row[-1] for row in unknown_rows['mechanics_feature'] if row[2] == 'biologicals')
    absent_bio = next(row[-1] for row in absent_rows['mechanics_feature'] if row[2] == 'biologicals')
    assert unknown_bio['known'] is False and unknown_bio['value'] is None
    assert absent_bio['known'] is True and absent_bio['value'] is False


@pytest.mark.parametrize('system_id', [0, -1, True, '42', 42.0])
def test_materialization_requires_positive_integer_system_identities(system_id):
    with pytest.raises(ValueError, match='positive integer'):
        materialize({system_id: facts(known_body())})


def test_empty_generation_is_rejected():
    with pytest.raises(ValueError, match='empty generation'):
        materialize({})


class SchemaRecorder:
    def __init__(self):
        self.statements = []

    @contextmanager
    def transaction(self):
        yield

    def execute(self, query):
        self.statements.append(query)


@pytest.mark.parametrize('name', [
    'public', 'v3_derived', 'v3_meta', 'ratings_v4_validation_',
    'ratings_v4_validation_a;DROP SCHEMA public', 'ratings_v4_validation_A',
    'ratings_v4_validation_' + 'a' * 33, 'ratings_v4_validation_x.y',
])
def test_schema_guard_rejects_every_non_disposable_or_unsafe_name_before_execution(name):
    connection = SchemaRecorder()
    with pytest.raises(ValueError, match='disposable'):
        bootstrap(connection, name)
    assert connection.statements == []


def test_bootstrap_only_creates_a_new_isolated_schema_and_constrained_tables():
    connection = SchemaRecorder()
    name = 'ratings_v4_validation_test_42'
    assert schema_name(name) == name
    bootstrap(connection, name)
    assert connection.statements[0] == f'CREATE SCHEMA {name}'
    sql = '\n'.join(connection.statements)
    assert 'IF NOT EXISTS' not in sql
    assert 'DROP ' not in sql and 'v3_meta' not in sql
    assert 'specialisation_quality_min < specialisation_quality_max AND specialisation_quality IS NULL' in sql
    assert 'REFERENCES ratings_v4_validation_test_42.economy_opportunity' in sql
