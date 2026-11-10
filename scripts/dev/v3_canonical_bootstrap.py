"""Bootstrap a published ``v3_gen_*`` canonical generation into an existing,
already-migrated psycopg connection.

Extracted verbatim from ``tests/ratings_v4_pg_fixture.canonical_database`` so the
test fixture and the Cypress V3 seed
(``scripts/dev/seed_cypress_v3_generation.py``) share one canonical-bootstrap
code path (plan D4). This module does NOT create/drop databases and does NOT
apply migrations -- the caller owns the connection lifecycle and schema.
"""
from __future__ import annotations

import json
from uuid import UUID, uuid4

from psycopg import sql


def bootstrap_published_canonical(connection, canonical: dict, metadata: dict,
                                  *, additional_sources: tuple = ()) -> UUID:
    """Create + finalize + publish the canonical generation for ``canonical``.

    Runs the exact INSERT / ``create_canonical_generation_relations`` /
    ``finalize_canonical_generation`` / ``publish_canonical_generation``
    sequence previously inline in ``canonical_database``, against an
    already-connected, already-migrated psycopg ``connection``. Returns the
    canonical ``generation_id``.
    """
    sources = [metadata, *additional_sources]

    def insert(schema, table, rows):
        relation = sql.Identifier(schema, table)
        for row in rows:
            connection.execute(sql.SQL(
                'INSERT INTO {} SELECT * FROM jsonb_populate_record(NULL::{}, %s::jsonb)'
            ).format(relation, relation), (json.dumps(row),))

    with connection.transaction():
        insert('v3_source', 'source', [metadata['source']])
        connection.execute('''INSERT INTO v3_source.source_rights_policy(
            rights_policy_id,source_id,policy_version,rights_class,retention_class,effective_at)
            VALUES (1,40,'test-fixture','CANONICAL_ELIGIBLE','TEST_ONLY',now())''')
        insert('v3_source', 'source_artifact', [item['artifact'] for item in sources])
        insert('v3_source', 'source_run', [item['run'] for item in sources])
        generation = uuid4()
        schema = canonical['canonical_schema']
        connection.execute('''INSERT INTO v3_meta.canonical_generation(
            generation_id,generation_key,relation_schema,manifest_sha256,build_source_run_id)
            VALUES (%s,%s,%s,%s,%s)''',
            (generation, schema.removeprefix('v3_gen_'), schema, bytes(32),
             metadata['run']['source_run_id']))
        connection.execute('SELECT v3_meta.create_canonical_generation_relations(%s)', (generation,))
        for ordinal, item in enumerate(sources):
            connection.execute('''INSERT INTO v3_meta.canonical_generation_input(
                generation_id,input_ordinal,source_id,source_run_id,artifact_id,input_role)
                VALUES (%s,%s,40,%s,%s,'TEST_FIXTURE')''',
                (generation, ordinal, item['run']['source_run_id'], item['artifact']['artifact_id']))
        for extra in canonical['extras']:
            if extra['schema'] == 'v3_vocab':
                insert('v3_vocab', extra['relation'], extra['rows'])
        insert(schema, 'systems', canonical['systems'])
        insert(schema, 'bodies', canonical['bodies'])
        for extra in canonical['extras']:
            if extra['schema'] == schema:
                insert(schema, extra['relation'], extra['rows'])
        receipt = {'systems': len(canonical['systems']), 'bodies': len(canonical['bodies'])}
        connection.execute('SELECT v3_meta.finalize_canonical_generation(%s,%s::jsonb)',
                           (generation, json.dumps(receipt)))
        connection.execute('SELECT v3_meta.publish_canonical_generation(%s,%s,%s)',
                           (generation, 'disposable-test', 'fixture'))
    return generation
