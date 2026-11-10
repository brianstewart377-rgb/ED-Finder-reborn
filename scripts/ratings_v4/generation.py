"""Disposable PostgreSQL proof of the V4 derived contract; never a production migration.

The schema prefix is deliberately isolated. There is no publication pointer, API
route, canonical write, or operation that replaces an existing schema/generation.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import re
import sys
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'apps' / 'api' / 'src'))

from domain.ratings_v4 import (  # noqa: E402
    BodyFact, ECONOMIES, MECHANICS_VERSION, SCORER_VERSION, SystemFacts,
    local_opportunity_score, opportunities_from_facts, rate_all,
)


def normalized(value):
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def digest(value) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(',', ':'), allow_nan=False,
    ).encode()).hexdigest()


def schema_name(value: str) -> str:
    if not re.fullmatch(r'ratings_v4_validation_[a-z0-9_]{1,32}', value):
        raise ValueError('only a disposable ratings_v4_validation_* schema is allowed')
    return value


def bootstrap(connection, schema: str) -> None:
    """Create a new isolated schema. Existing schemas are intentionally rejected."""
    schema = schema_name(schema)
    with connection.transaction():
        connection.execute(f'CREATE SCHEMA {schema}')
        connection.execute(f'''
            CREATE TABLE {schema}.generation (
                generation_id uuid PRIMARY KEY,
                canonical_generation text NOT NULL,
                scorer_version text NOT NULL,
                mechanics_version text NOT NULL,
                input_sha256 text NOT NULL,
                content_sha256 text NOT NULL,
                metadata_sha256 text NOT NULL,
                manifest jsonb NOT NULL,
                status text NOT NULL CHECK(status IN ('BUILDING','VALIDATING','READY','FAILED')),
                failure text,
                created_at timestamptz NOT NULL DEFAULT now()
            );
            CREATE TABLE {schema}.system_input (
                generation_id uuid REFERENCES {schema}.generation,
                system_id64 bigint NOT NULL,
                payload jsonb NOT NULL,
                PRIMARY KEY(generation_id, system_id64)
            );
            CREATE TABLE {schema}.mechanics_feature (
                generation_id uuid,
                system_id64 bigint,
                candidate_id text NOT NULL,
                feature_type text NOT NULL,
                payload jsonb NOT NULL,
                PRIMARY KEY(generation_id, system_id64, candidate_id, feature_type),
                FOREIGN KEY(generation_id, system_id64)
                    REFERENCES {schema}.system_input
            );
            CREATE TABLE {schema}.economy_opportunity (
                generation_id uuid,
                system_id64 bigint,
                economy text NOT NULL,
                candidate_id text NOT NULL,
                local_score smallint NOT NULL CHECK(local_score BETWEEN 0 AND 100),
                eligible boolean NOT NULL,
                payload jsonb NOT NULL,
                PRIMARY KEY(generation_id, system_id64, economy, candidate_id),
                FOREIGN KEY(generation_id, system_id64)
                    REFERENCES {schema}.system_input
            );
            CREATE TABLE {schema}.system_economy_rating (
                generation_id uuid,
                system_id64 bigint,
                economy text NOT NULL CHECK(economy IN
                    ('Agriculture','Refinery','Industrial','HighTech','Military','Tourism','Extraction')),
                potential_score smallint NOT NULL CHECK(potential_score BETWEEN 0 AND 100),
                specialisation_quality smallint CHECK(specialisation_quality BETWEEN 0 AND 100),
                specialisation_quality_min smallint NOT NULL,
                specialisation_quality_max smallint NOT NULL,
                evidence_completeness double precision NOT NULL CHECK(evidence_completeness BETWEEN 0 AND 1),
                confidence double precision NOT NULL CHECK(confidence BETWEEN 0 AND 1),
                payload jsonb NOT NULL,
                CHECK(0 <= specialisation_quality_min AND
                    specialisation_quality_min <= specialisation_quality_max AND specialisation_quality_max <= 100),
                CHECK((specialisation_quality_min = specialisation_quality_max AND
                       specialisation_quality IS NOT NULL AND specialisation_quality = specialisation_quality_min)
                   OR (specialisation_quality_min < specialisation_quality_max AND specialisation_quality IS NULL)),
                PRIMARY KEY(generation_id, system_id64, economy),
                FOREIGN KEY(generation_id, system_id64)
                    REFERENCES {schema}.system_input
            );
            CREATE TABLE {schema}.economy_rating_contributor (
                generation_id uuid,
                system_id64 bigint,
                economy text NOT NULL,
                candidate_id text NOT NULL,
                rule_id text NOT NULL,
                mechanic_class text NOT NULL,
                payload jsonb NOT NULL,
                PRIMARY KEY(generation_id, system_id64, economy, candidate_id, rule_id, mechanic_class),
                FOREIGN KEY(generation_id, system_id64, economy, candidate_id)
                    REFERENCES {schema}.economy_opportunity
            );
        ''')


TABLES = (
    'system_input', 'mechanics_feature', 'economy_opportunity',
    'system_economy_rating', 'economy_rating_contributor',
)


def materialize(systems: dict[int, SystemFacts]) -> dict[str, list[list]]:
    if not systems:
        raise ValueError('empty generation')
    rows = {table: [] for table in TABLES}
    for system_id, facts in sorted(systems.items()):
        if type(system_id) is not int or system_id <= 0:
            raise ValueError('system identity must be a positive integer')
        bodies = {}
        for body in facts.bodies:
            if body.candidate_id in bodies and bodies[body.candidate_id] != body:
                raise ValueError('conflicting duplicate body identity')
            bodies[body.candidate_id] = body
        facts = replace(facts, bodies=tuple(bodies[key] for key in sorted(bodies)))
        rows['system_input'].append([system_id, asdict(facts)])
        opportunities = opportunities_from_facts(facts)
        ratings = rate_all(opportunities)
        if set(ratings) != set(ECONOMIES):
            raise ValueError('exactly seven economy ratings required')
        feature_rows = {}
        for opportunity in opportunities:
            candidate_id = opportunity.candidate_id
            for feature in opportunity.evidence:
                feature_payload = asdict(feature)
                feature_payload.pop('weight')  # economy-specific weights stay in opportunity evidence
                key = (candidate_id, feature.feature_type)
                if key in feature_rows and feature_rows[key] != feature_payload:
                    raise ValueError(f'conflicting normalized feature {key}')
                feature_rows[key] = feature_payload
            eligible = opportunity.native or opportunity.modifier
            rows['economy_opportunity'].append([
                system_id, opportunity.economy, candidate_id,
                local_opportunity_score(opportunity), eligible, asdict(opportunity),
            ])
            if not eligible:
                continue
            prefix = [system_id, opportunity.economy, candidate_id]
            evidence = [asdict(feature) for feature in opportunity.evidence]
            # Inheritance is categorical; bases/caps/roll-up weights are retained
            # in rating.contributions rather than invented per-rule additive points.
            for rule in sorted(set(opportunity.contributors)):
                kind = 'NATIVE_INHERITANCE' if rule.startswith('native:') else 'MODIFIER_INHERITANCE'
                rows['economy_rating_contributor'].append(prefix + [rule, kind, {'evidence': evidence}])
            for kind, rules in (
                ('STRONG_LINK_POSITIVE', opportunity.strong_positive_rules),
                ('STRONG_LINK_NEGATIVE', opportunity.strong_negative_rules),
            ):
                for rule in sorted(set(rules)):
                    rows['economy_rating_contributor'].append(prefix + [rule, kind, {'evidence': evidence}])
            for constraint in opportunity.specialisation_constraints:
                rows['economy_rating_contributor'].append(prefix + [
                    constraint.rule_id, 'SPECIALISATION_CONSTRAINT', asdict(constraint),
                ])
        rows['mechanics_feature'].extend(
            [system_id, candidate, feature, payload]
            for (candidate, feature), payload in sorted(feature_rows.items())
        )
        for economy, rating in sorted(ratings.items()):
            rows['system_economy_rating'].append([
                system_id, economy, rating.potential_score, rating.specialisation_quality,
                rating.specialisation_quality_min, rating.specialisation_quality_max,
                float(rating.evidence_completeness), float(rating.confidence), asdict(rating),
            ])
    return normalized(rows)


def _ordered(rows):
    return sorted(rows, key=lambda row: json.dumps(row[:-1], sort_keys=True))


def _content_hash(rows) -> str:
    return digest({table: _ordered(values) for table, values in rows.items()})


def stage(connection, schema: str, systems: dict[int, SystemFacts], manifest: dict,
          generation_id: UUID | None = None) -> UUID:
    """Atomically stage all components, leaving validation as a separate gate."""
    from psycopg.types.json import Jsonb

    schema = schema_name(schema)
    if not manifest.get('canonical_generation') or not manifest.get('source_lineage'):
        raise ValueError('canonical generation and source lineage are required')
    rows = materialize(systems)
    generation_id = generation_id or uuid4()
    with connection.transaction():
        connection.execute(f'INSERT INTO {schema}.generation VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,DEFAULT)', (
            generation_id, manifest['canonical_generation'], SCORER_VERSION, MECHANICS_VERSION,
            digest(rows['system_input']), _content_hash(rows),
            digest([manifest['canonical_generation'], SCORER_VERSION, MECHANICS_VERSION, manifest]),
            Jsonb(manifest), 'BUILDING',
        ))
        for table in TABLES:
            values = rows[table]
            if values:
                placeholders = ','.join(['%s'] * (len(values[0]) + 1))
                with connection.cursor() as cursor:
                    cursor.executemany(f'INSERT INTO {schema}.{table} VALUES ({placeholders})', [
                        [generation_id, *row[:-1], Jsonb(row[-1])] for row in values
                    ])
        connection.execute(f"UPDATE {schema}.generation SET status='VALIDATING' WHERE generation_id=%s", (generation_id,))
    return generation_id


def validate(connection, schema: str, generation_id: UUID) -> dict:
    """Read back every component and recompute independently from retained inputs."""
    schema = schema_name(schema)
    try:
        with connection.transaction():
            generation = connection.execute(
                f'SELECT status, input_sha256, content_sha256, scorer_version, mechanics_version, '
                f'canonical_generation, manifest, metadata_sha256 '
                f'FROM {schema}.generation WHERE generation_id=%s FOR UPDATE', (generation_id,),
            ).fetchone()
            if not generation or generation[0] != 'VALIDATING':
                raise ValueError('generation must be VALIDATING')
            if generation[3:5] != (SCORER_VERSION, MECHANICS_VERSION):
                raise ValueError('ruleset version mismatch')
            if digest([generation[5], generation[3], generation[4], generation[6]]) != generation[7]:
                raise ValueError('source lineage metadata digest mismatch')
            actual = {}
            for table in TABLES:
                actual[table] = normalized([
                    list(row[1:]) for row in connection.execute(
                        f'SELECT * FROM {schema}.{table} WHERE generation_id=%s', (generation_id,),
                    ).fetchall()
                ])
            inputs = sorted(actual['system_input'], key=lambda row: row[0])
            if digest(inputs) != generation[1]:
                raise ValueError('input digest mismatch')
            systems = {}
            for system_id, payload in inputs:
                fields = dict(payload)
                fields['bodies'] = tuple(BodyFact(**body) for body in fields['bodies'])
                systems[system_id] = SystemFacts(**fields)
            expected = materialize(systems)
            for table in TABLES:
                if _ordered(actual[table]) != _ordered(expected[table]):
                    raise ValueError(f'{table} differs from deterministic rebuild')
            if _content_hash(actual) != generation[2]:
                raise ValueError('content digest mismatch')
            connection.execute(
                f"UPDATE {schema}.generation SET status='READY' WHERE generation_id=%s", (generation_id,),
            )
            return {
                'status': 'READY', 'generation_id': str(generation_id),
                'postgres_version': connection.execute('SHOW server_version').fetchone()[0],
                'scorer_version': SCORER_VERSION, 'mechanics_version': MECHANICS_VERSION,
                'input_sha256': generation[1], 'content_sha256': generation[2],
                'metadata_sha256': generation[7],
                'counts': {table: len(values) for table, values in actual.items()},
                'validation': 'full readback and deterministic recomputation; all components equal',
                'published': False,
            }
    except Exception as error:
        with connection.transaction():
            connection.execute(
                f"UPDATE {schema}.generation SET status='FAILED', failure=%s "
                "WHERE generation_id=%s AND status='VALIDATING'", (str(error), generation_id),
            )
        raise
