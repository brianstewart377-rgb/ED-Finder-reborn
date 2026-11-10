"""Versioned V4 generation builder; explicit connections, no implicit deployment.

Canonical reads and derived writes use separate connections. Chunk transactions
keep all rows, their source seal and their checkpoint atomic. Production callers
must additionally enforce the reviewed target/resource/operation authority.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'apps/api/src'))

from domain.ratings_v4 import (  # noqa: E402
    BodyFact, SystemFacts, ECONOMIES, SCORER_VERSION, MECHANICS_VERSION,
    _candidate_specialisation, local_opportunity_score, opportunities_from_facts, rate_all,
)
from domain.ratings_v4_canonical import normalized_sha256  # noqa: E402
from scripts.ratings_v4.canonical_stream import (  # noqa: E402
    STREAM_VERSION, adapt_retained_chunk, verify_recovered_source,
)
from scripts.ratings_v4.verify_freeze import verify_contract  # noqa: E402

BUILDER_VERSION = 'v4-derived-builder-1'
BODY_FIELDS = ('body_class', 'spectral_class', 'luminosity_class', 'rings',
               'biologicals', 'geologicals', 'volcanism', 'terraformable',
               'tidally_locked', 'is_main_star', 'reserve_level', 'usable_ground_opportunity')
VECTOR_FIELDS = ('potential', 'quality', 'quality_min', 'quality_max', 'completeness', 'confidence')
BODY_COLUMNS = ('derived_generation_id', 'system_id64', 'body_pk', 'source_body_id64',
                *BODY_FIELDS, 'subtype_present', 'subtype_observed_at')
VECTOR_COLUMNS = ('derived_generation_id', 'system_id64', 'chunk_ordinal',
                  'loaded_body_count', 'physical_body_count', 'subtype_projection_sha256', *VECTOR_FIELDS)
OPPORTUNITY_COLUMNS = ('derived_generation_id', 'system_id64', 'body_pk', 'economy_ordinal',
                       'native', 'modifier', 'local_score', 'specialisation_quality',
                       'specialisation_min', 'specialisation_max', 'completeness', 'confidence')


def code_identity():
    return {name: hashlib.sha256((ROOT / name).read_text(encoding='utf-8').encode()).hexdigest()
            for name in ('scripts/ratings_v4/production_generation.py',
                         'scripts/ratings_v4/run_generation.py',
                         'scripts/ratings_v4/canonical_stream.py',
                         'sql/v3/migrations/003_ratings_v4_derived.sql',
                         'scripts/ratings_v4/resume_upgrade.py',
                         'sql/v3/proposals/007_ratings_v4_code_upgrade.sql',
                         'apps/api/src/domain/ratings_v4.py',
                         'apps/api/src/domain/ratings_v4_canonical.py',
                         'scripts/ratings_v4/verify_freeze.py')}


def _verify_code(manifest, connection=None, generation_id=None):
    if manifest.get('code_sha256_lf') != code_identity():
        from scripts.ratings_v4.resume_upgrade import recorded_upgrade
        receipt = recorded_upgrade(connection, generation_id, manifest)
        if receipt is None:
            raise ValueError('generation builder/adapter/schema code identity changed')
        return receipt
    return None


def _json(value):
    def convert(item):
        if isinstance(item, (bytes, bytearray, memoryview)):
            return bytes(item).hex()
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat()
        return str(item)
    return json.dumps(value, default=convert, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).digest()


def _projection(record):
    return {'system': {'id64': record['id64'], 'bodies': [
        {key: body[key] for key in ('id64', 'bodyId', 'type', 'subType', 'updateTime') if key in body}
        for body in record.get('bodies', [])]}}


def _vectors(ratings):
    rows = [ratings[economy] for economy in ECONOMIES]
    return ([row.potential_score for row in rows], [row.specialisation_quality for row in rows],
            [row.specialisation_quality_min for row in rows], [row.specialisation_quality_max for row in rows],
            [round(row.evidence_completeness * 10000) for row in rows],
            [round(row.confidence * 10000) for row in rows])


def _opportunity_rows(generation_id, identifier, opportunities):
    rows = []
    for opportunity in opportunities:
        if not (opportunity.native or opportunity.modifier):
            continue
        quality = _candidate_specialisation(opportunity)
        rows.append((generation_id, identifier, int(opportunity.candidate_id),
                     ECONOMIES.index(opportunity.economy) + 1,
                     opportunity.native, opportunity.modifier, local_opportunity_score(opportunity),
                     quality.quality, quality.minimum_quality, quality.maximum_quality,
                     opportunity.completeness, opportunity.confidence))
    return sorted(rows, key=lambda row: (row[1], row[2], row[3]))


def create_generation(connection, snapshot, generation_key):
    frozen, _, _ = verify_contract()
    recovery = verify_recovered_source()
    generation_id = uuid4()
    manifest = {'builder_version': BUILDER_VERSION, 'scorer_version': SCORER_VERSION,
                'mechanics_version': MECHANICS_VERSION, 'adapter_version': STREAM_VERSION,
                'economies': list(ECONOMIES), 'freeze_sha256': normalized_sha256(frozen),
                'code_sha256_lf': code_identity(),
                'importer_sha256': recovery['importer_package_sha256'],
                'canonical_generation_id': snapshot.generation_id,
                'canonical_publication_sequence': snapshot.publication_sequence,
                'canonical_schema': snapshot.schema, 'source_metadata': snapshot.metadata,
                'expected_systems': snapshot.expected_systems, 'expected_bodies': snapshot.expected_bodies,
                'capabilities': ['ratings-v4'], 'ground_policy': 'unknown',
                'provenance_policy': 'generation source manifest + immutable canonical body identity + subtype projection hash'}
    with connection.transaction():
        connection.execute('''INSERT INTO v3_meta.derived_generation(
            derived_generation_id,canonical_generation_id,canonical_publication_sequence,generation_key,
            mechanics_version,scorer_version,adapter_version,manifest,manifest_sha256,expected_systems,expected_bodies)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)''',
            (generation_id, snapshot.generation_id, snapshot.publication_sequence, generation_key,
             MECHANICS_VERSION, SCORER_VERSION, STREAM_VERSION, _json(manifest), _digest(manifest),
             snapshot.expected_systems, snapshot.expected_bodies))
    return generation_id


def encode_chunk(generation_id, ordinal, canonical, metadata, source_records):
    if type(ordinal) is not int or ordinal < 0:
        raise ValueError('invalid chunk ordinal')
    source_records = tuple(source_records)
    facts = adapt_retained_chunk(canonical, metadata, source_records)
    records = {record['id64']: record for record in source_records}
    source_bodies = {(record['id64'], body['id64']): body
                     for record in source_records for body in record.get('bodies', [])}
    canonical_bodies = {str(row['body_pk']): row for row in canonical['bodies']}
    counts = {row['id64']: row['loaded_body_count'] for row in canonical['systems']}
    vectors, bodies, opportunities = [], [], []
    for identifier, system in sorted(facts.items()):
        local = opportunities_from_facts(system)
        ratings = rate_all(local)
        projection_hash = bytes.fromhex(normalized_sha256(_projection(records[identifier])))
        vectors.append((generation_id, identifier, ordinal, counts[identifier], len(system.bodies),
                        projection_hash, *_vectors(ratings)))
        for body in sorted(system.bodies, key=lambda item: int(item.candidate_id)):
            canonical_body = canonical_bodies[body.candidate_id]
            source_id = canonical_body['source_body_id64']
            subtype_present = bool(source_bodies[(identifier, source_id)].get('subType'))
            observed = source_bodies[(identifier, source_id)].get('updateTime') if subtype_present else None
            timestamp = datetime.fromisoformat(observed.replace('Z', '+00:00')) if observed else None
            if timestamp is not None and timestamp.tzinfo is None:
                raise ValueError('source classification timestamp must include timezone')
            bodies.append((generation_id, identifier, int(body.candidate_id), source_id,
                           *(getattr(body, field) for field in BODY_FIELDS), subtype_present, timestamp))
        opportunities.extend(_opportunity_rows(generation_id, identifier, local))
    result = {'vectors': vectors, 'bodies': bodies, 'opportunities': opportunities}
    content_hash = _digest(result)
    checkpoint = (generation_id, ordinal, _digest([_projection(record) for record in source_records]),
                  _digest(canonical), content_hash, len(vectors), len(canonical['bodies']), len(bodies), len(opportunities))
    return result, checkpoint


def write_chunk(connection, generation_id, ordinal, canonical, metadata, source_records):
    from psycopg import sql

    payload, checkpoint = encode_chunk(generation_id, ordinal, canonical, metadata, source_records)
    with connection.transaction():
        # Canonical identities never change; these are derived INSERTs. The
        # immutable-state and deferred FK triggers must remain enabled.
        generation = connection.execute('''SELECT lifecycle_state,manifest FROM v3_meta.derived_generation
            WHERE derived_generation_id=%s FOR SHARE''', (generation_id,)).fetchone()
        if generation is None or generation[0] != 'BUILDING':
            raise ValueError('generation is not BUILDING')
        manifest = generation[1]
        _verify_code(manifest, connection, generation_id)
        if (manifest['source_metadata'] != metadata or manifest['canonical_schema'] != canonical['canonical_schema']):
            raise ValueError('chunk canonical source differs from generation manifest')
        old = connection.execute('''SELECT source_projection_sha256,canonical_input_sha256,content_sha256
            FROM v3_derived.build_chunk WHERE derived_generation_id=%s AND chunk_ordinal=%s''',
            (generation_id, ordinal)).fetchone()
        if old is not None:
            if tuple(bytes(item) for item in old) != checkpoint[2:5]:
                raise ValueError('resumed chunk source/content changed')
            return False
        for table, columns, rows in (
            ('system_rating_vector', VECTOR_COLUMNS, payload['vectors']),
            ('body_mechanics', BODY_COLUMNS, payload['bodies']),
            ('economy_opportunity', OPPORTUNITY_COLUMNS, payload['opportunities']),
        ):
            command = sql.SQL('COPY v3_derived.{} ({}) FROM STDIN').format(
                sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, columns)))
            with connection.cursor().copy(command) as copy:
                for row in rows:
                    copy.write_row(row)
        connection.execute('''INSERT INTO v3_derived.build_chunk(
            derived_generation_id,chunk_ordinal,source_projection_sha256,canonical_input_sha256,
            content_sha256,systems,canonical_bodies,physical_bodies,eligible_opportunities)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''', checkpoint)
    return True


def replay_facts(manifest, vector, body_rows):
    """Reconstruct all candidates, including unknown/ineligible coverage.

    Provenance references immutable materialized inputs and the canonical source
    manifest. It never labels generated values as new source observations.
    """
    identifier = vector[1]
    generation_id = str(vector[0])
    source = {'derived_generation_id': generation_id,
              'canonical_generation_id': manifest['canonical_generation_id'],
              'canonical_schema': manifest['canonical_schema'],
              'source_run_id': manifest['source_metadata']['run']['source_run_id'],
              'artifact_sha256': manifest['source_metadata']['artifact']['content_sha256'].removeprefix('\\x'),
              'system_id64': identifier}
    system_provenance = _json({**source, 'relation': 'systems', 'loaded_body_count': vector[3]})
    bodies = []
    for row in body_rows:
        if row[0] != vector[0] or row[1] != identifier:
            raise ValueError('mechanics input has conflicting generation/system identity')
        values = dict(zip(BODY_FIELDS, row[4:-2], strict=True))
        body_source = {**source, 'body_pk': row[2], 'source_body_id64': row[3],
                       'mechanics_relation': 'v3_derived.body_mechanics'}
        provenance = {field: _json({**body_source, 'field': field}) for field in BODY_FIELDS}
        provenance['provenance'] = _json(body_source)
        if row[-2]:
            provenance['body_class'] = _json({**body_source, 'field': 'subType',
                'source': 'Retained Spansh galaxy artifact', 'source_projection_sha256': bytes(vector[5]).hex(),
                'source_updated_at': row[-1]})
        else:
            provenance['body_class'] = _json({**body_source, 'field': 'body_type_id',
                'relation': 'bodies', 'subtype_state': 'UNKNOWN'})
        bodies.append(BodyFact(candidate_id=str(row[2]), reserve_scope='body',
                               feature_provenance=provenance, **values))
    if len(bodies) != vector[4]:
        raise ValueError('materialized physical body count mismatch')
    return SystemFacts(tuple(bodies), body_inventory_complete=True,
                       feature_provenance={'body_inventory': system_provenance, 'provenance': system_provenance})


def _read_chunk(connection, generation_id, ordinal):
    from psycopg import sql

    def select(table, columns, order):
        query = sql.SQL('''SELECT {} FROM v3_derived.{} t WHERE t.derived_generation_id=%s
            AND t.system_id64 IN (SELECT system_id64 FROM v3_derived.system_rating_vector
                WHERE derived_generation_id=%s AND chunk_ordinal=%s) ORDER BY {}''').format(
            sql.SQL(',').join(sql.Identifier('t', column) for column in columns), sql.Identifier(table),
            sql.SQL(',').join(sql.Identifier('t', column) for column in order))
        return connection.execute(query, (generation_id, generation_id, ordinal)).fetchall()
    return {'vectors': select('system_rating_vector', VECTOR_COLUMNS, ['system_id64']),
            'bodies': select('body_mechanics', BODY_COLUMNS, ['system_id64', 'body_pk']),
            'opportunities': select('economy_opportunity', OPPORTUNITY_COLUMNS,
                                    ['system_id64', 'body_pk', 'economy_ordinal'])}


def seal_source(connection, generation_id, receipt):
    """Stop writes only after verified EOF and complete, contiguous checkpoints."""
    if not isinstance(receipt, dict) or receipt.get('consumed_to_eof') is not True:
        raise ValueError('source stream has no verified EOF receipt')
    with connection.transaction():
        row = connection.execute('''SELECT lifecycle_state,manifest FROM v3_meta.derived_generation
            WHERE derived_generation_id=%s FOR UPDATE''', (generation_id,)).fetchone()
        if row is None or row[0] != 'BUILDING':
            raise ValueError('generation is not BUILDING')
        manifest = row[1]
        _verify_code(manifest, connection, generation_id)
        artifact = manifest['source_metadata']['artifact']
        if (receipt.get('artifact_sha256') != artifact['content_sha256'].removeprefix('\\x') or
                receipt.get('size_bytes') != artifact['size_bytes'] or
                receipt.get('systems') != manifest['expected_systems'] or
                receipt.get('bodies') != manifest['expected_bodies']):
            raise ValueError('verified source coverage differs from canonical manifest')
        chunks = connection.execute('''SELECT chunk_ordinal,systems,canonical_bodies,content_sha256
            FROM v3_derived.build_chunk WHERE derived_generation_id=%s ORDER BY chunk_ordinal''',
            (generation_id,)).fetchall()
        if (not chunks or any(row[0] != index for index, row in enumerate(chunks)) or
                sum(row[1] for row in chunks) != manifest['expected_systems'] or
                sum(row[2] for row in chunks) != manifest['expected_bodies']):
            raise ValueError('staged chunk coverage is incomplete')
        content_hash = _digest([(row[0], bytes(row[3]).hex()) for row in chunks])
        connection.execute('''UPDATE v3_meta.derived_generation SET lifecycle_state='VALIDATING',
            source_receipt=%s::jsonb,content_sha256=%s WHERE derived_generation_id=%s''',
            (_json(receipt), content_hash, generation_id))


def validate_generation(connection, generation_id):
    """Read every stored chunk and recompute every system with the frozen scorer."""
    started = datetime.now(timezone.utc)
    row = connection.execute('''SELECT lifecycle_state,manifest,manifest_sha256,content_sha256
        FROM v3_meta.derived_generation WHERE derived_generation_id=%s''', (generation_id,)).fetchone()
    if row is None or row[0] != 'VALIDATING':
        raise ValueError('generation is not VALIDATING')
    manifest = row[1]
    upgrade = _verify_code(manifest, connection, generation_id)
    if _digest(manifest) != bytes(row[2]):
        raise ValueError('generation manifest checksum mismatch')
    if (manifest['scorer_version'] != SCORER_VERSION or manifest['mechanics_version'] != MECHANICS_VERSION or
            manifest['adapter_version'] != STREAM_VERSION or manifest['builder_version'] != BUILDER_VERSION):
        raise ValueError('generation replay version mismatch')
    verify_contract()
    checkpoints = connection.execute('''SELECT chunk_ordinal,content_sha256,systems,canonical_bodies,
        physical_bodies,eligible_opportunities FROM v3_derived.build_chunk
        WHERE derived_generation_id=%s ORDER BY chunk_ordinal''', (generation_id,)).fetchall()
    if _digest([(item[0], bytes(item[1]).hex()) for item in checkpoints]) != bytes(row[3]):
        raise ValueError('generation content seal mismatch')
    systems = bodies = opportunities = 0
    unknown_quality = [0] * 7
    minima, maxima = [100] * 7, [0] * 7
    for checkpoint in checkpoints:
        payload = _read_chunk(connection, generation_id, checkpoint[0])
        if _digest(payload) != bytes(checkpoint[1]):
            raise ValueError(f'stored chunk {checkpoint[0]} content checksum mismatch')
        if (len(payload['vectors']), sum(vector[3] for vector in payload['vectors']),
                len(payload['bodies']), len(payload['opportunities'])) != checkpoint[2:]:
            raise ValueError('stored chunk inventory mismatch')
        grouped = {}
        for body in payload['bodies']:
            grouped.setdefault(body[1], []).append(body)
        expected_opportunities = []
        for vector in payload['vectors']:
            facts = replay_facts(manifest, vector, grouped.get(vector[1], []))
            local = opportunities_from_facts(facts)
            ratings = rate_all(local)
            if tuple(vector[6:]) != _vectors(ratings):
                raise ValueError(f'system {vector[1]} score/coverage differs from replay')
            expected_opportunities.extend(_opportunity_rows(generation_id, vector[1], local))
            for index in range(7):
                unknown_quality[index] += vector[7][index] is None
                minima[index] = min(minima[index], vector[6][index])
                maxima[index] = max(maxima[index], vector[6][index])
        if expected_opportunities != payload['opportunities']:
            raise ValueError('stored opportunities differ from replay')
        systems += len(payload['vectors'])
        bodies += len(payload['bodies'])
        opportunities += len(payload['opportunities'])
    if systems != manifest['expected_systems']:
        raise ValueError('validated system coverage is incomplete')
    receipt = {'status': 'VERIFIED', 'systems': systems, 'ratings': systems * 7,
               'physical_bodies': bodies, 'eligible_opportunities': opportunities,
               'unknown_quality': dict(zip(ECONOMIES, unknown_quality, strict=True)),
               'potential_min': dict(zip(ECONOMIES, minima, strict=True)),
               'potential_max': dict(zip(ECONOMIES, maxima, strict=True)),
               'content_sha256': bytes(row[3]).hex(), 'manifest_sha256': bytes(row[2]).hex(),
               'every_system_replayed': True, 'every_stored_chunk_read_back': True,
               'elapsed_seconds': (datetime.now(timezone.utc) - started).total_seconds()}
    if upgrade is not None:
        receipt['code_upgrade'] = upgrade
    with connection.transaction():
        updated = connection.execute('''UPDATE v3_meta.derived_generation SET lifecycle_state='READY',
            validation_receipt=%s::jsonb,validated_at=now()
            WHERE derived_generation_id=%s AND lifecycle_state='VALIDATING' RETURNING derived_generation_id''',
            (_json(receipt), generation_id)).fetchone()
        if updated is None:
            raise ValueError('generation changed during validation')
    return receipt


def explain_system(connection, generation_id, system_id64):
    from psycopg import sql

    row = connection.execute('''SELECT manifest,lifecycle_state FROM v3_meta.derived_generation
        WHERE derived_generation_id=%s''', (generation_id,)).fetchone()
    if row is None or row[1] not in {'READY', 'PUBLISHED', 'RETIRED'}:
        raise ValueError('no validated generation')
    _verify_code(row[0], connection, generation_id)
    vector = connection.execute(sql.SQL('SELECT {} FROM v3_derived.system_rating_vector WHERE derived_generation_id=%s AND system_id64=%s').format(
        sql.SQL(',').join(map(sql.Identifier, VECTOR_COLUMNS))), (generation_id, system_id64)).fetchone()
    if vector is None:
        return None
    bodies = connection.execute(sql.SQL('SELECT {} FROM v3_derived.body_mechanics WHERE derived_generation_id=%s AND system_id64=%s ORDER BY body_pk').format(
        sql.SQL(',').join(map(sql.Identifier, BODY_COLUMNS))), (generation_id, system_id64)).fetchall()
    facts = replay_facts(row[0], vector, bodies)
    ratings = rate_all(opportunities_from_facts(facts))
    if tuple(vector[6:]) != _vectors(ratings):
        raise ValueError('stored score does not match explanation replay')
    return {'derived_generation_id': str(generation_id), 'system_id64': system_id64,
            'canonical_generation_id': row[0]['canonical_generation_id'],
            'scorer_version': SCORER_VERSION, 'mechanics_version': MECHANICS_VERSION,
            'ratings': {economy: asdict(rating) for economy, rating in ratings.items()}}
