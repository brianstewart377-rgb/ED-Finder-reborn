"""Bounded, read-only canonical input for the frozen V4 scorer.

The retained importer is evidence of canonical normalization, never invoked to
rewrite a published generation. A source artifact supplies only missing subType.
All other mechanics facts still come from the pinned canonical relations.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'apps' / 'api' / 'src'))

from domain.ratings_v4_canonical import (  # noqa: E402
    adapt_canonical_export, normalized_sha256,
)

STREAM_VERSION = 'v4-canonical-stream-1'
MAX_CHUNK_SYSTEMS = 1000
MAX_CHUNK_BODIES = 100_000
VOCABULARIES = ('body_type', 'reserve_type', 'signal_type',
                'volcanism_type', 'terraforming_state')


def verify_recovered_source(root: Path = ROOT) -> dict:
    manifest = json.loads((root / 'docs/development/v3-canonical-source-recovery.json').read_bytes())
    for relative, expected in manifest['files_sha256'].items():
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('invalid recovery manifest path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f'recovered canonical source checksum mismatch: {relative}')
    package = root / 'apps/importer/src/v3_spansh'
    actual = hashlib.sha256(b''.join(path.read_bytes() for path in sorted(package.glob('*.py')))).hexdigest()
    if actual != manifest['importer_package_sha256']:
        raise ValueError('recovered importer package checksum mismatch')
    return manifest


class _HashedReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.bytes_read = 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.digest.update(data)
        self.bytes_read += len(data)
        return data


class _ArrayRootReader:
    """Check the root while forwarding unchanged bytes to the native parser.

    Checking each bounded read also handles arbitrarily long leading whitespace
    without retaining a prefix or depending on gzip.peek(). read(0) is the parser's
    binary-stream probe and must not be mistaken for end of input.
    """
    def __init__(self, stream):
        self.stream = stream
        self.checked = False

    def read(self, size=-1):
        data = self.stream.read(size)
        if size != 0 and not self.checked:
            # Match the existing YAJL backend's ASCII whitespace handling. The
            # parser still validates the complete document, including its tail.
            prefix = data.lstrip(b' \t\r\n\v\f')
            if prefix:
                if prefix[:1] != b'[':
                    raise ValueError('Spansh artifact must be a JSON array')
                self.checked = True
            elif not data:
                raise ValueError('Spansh artifact must be a JSON array')
        return data


class RetainedArtifactStream:
    """Hash the compressed bytes actually parsed; only EOF produces a receipt.

    Consumers may stage chunks before EOF, but must not mark a generation READY
    without ``receipt``. An interrupted, shortened or corrupt stream has none.
    No files or canonical tables are written by this reader.
    """
    def __init__(self, path: Path, *, sha256: str, size_bytes: int):
        if not re.fullmatch(r'[0-9a-f]{64}', sha256):
            raise ValueError('invalid retained artifact SHA256')
        if type(size_bytes) is not int or size_bytes <= 0:
            raise ValueError('invalid retained artifact size')
        self.path = path
        self.expected_sha256 = sha256
        self.expected_size = size_bytes
        self.receipt = None
        self.started = False

    def chunks(self, chunk_size=500):
        import ijson

        if type(chunk_size) is not int or not 1 <= chunk_size <= MAX_CHUNK_SYSTEMS:
            raise ValueError('chunk size outside bounded contract')
        if self.started:
            raise ValueError('artifact stream is single use')
        self.started = True
        systems = bodies = 0
        with self.path.open('rb') as source:
            reader = _HashedReader(source)
            chunk = []
            chunk_bodies = 0
            with gzip.GzipFile(fileobj=reader, mode='rb') as decoded:
                records = ijson.items(_ArrayRootReader(decoded), 'item', use_float=True)
                for record in records:
                    if not isinstance(record, dict) or not isinstance(record.get('bodies', []), list):
                        raise ValueError('invalid Spansh system record')
                    _system_ids([record.get('id64')])
                    body_count = len(record.get('bodies', []))
                    if body_count > MAX_CHUNK_BODIES:
                        raise ValueError('system exceeds bounded body inventory')
                    # Bound both system count and the more variable body count.
                    if chunk and (len(chunk) >= chunk_size or
                                  chunk_bodies + body_count > MAX_CHUNK_BODIES):
                        yield chunk
                        chunk = []
                        chunk_bodies = 0
                    chunk.append(record)
                    chunk_bodies += body_count
                    systems += 1
                    bodies += body_count
                if decoded.read(1):
                    raise ValueError('unconsumed source data')
            if reader.bytes_read != self.expected_size or reader.digest.hexdigest() != self.expected_sha256:
                raise ValueError('retained source artifact checksum/size mismatch')
            if systems == 0:
                raise ValueError('empty source artifact cannot establish galaxy coverage')
            if chunk:
                yield chunk
        self.receipt = {'artifact_sha256': self.expected_sha256,
                        'size_bytes': reader.bytes_read, 'systems': systems,
                        'bodies': bodies, 'consumed_to_eof': True}


def _system_ids(values: Iterable[int]) -> list[int]:
    values = list(values)
    if (not 1 <= len(values) <= MAX_CHUNK_SYSTEMS or
            any(type(value) is not int or not 0 <= value <= 2**63 - 1 for value in values) or
            len(set(values)) != len(values)):
        raise ValueError('invalid, duplicate or unbounded system ids')
    return values


def adapt_retained_chunk(canonical: Mapping, metadata: Mapping,
                         source_records: Iterable[Mapping]) -> dict:
    """Use exact source identities; preserve canonical facts and genuine lineage.

    The generation builder must bind the stream's verified EOF receipt to the
    canonical artifact SHA before validation/publication. This function does not
    claim that an individual in-memory record verifies an entire galaxy file.
    """
    records = tuple(source_records)
    identifiers = _system_ids(record['id64'] for record in records)
    if set(identifiers) != {row['id64'] for row in canonical['systems']}:
        raise ValueError('source/canonical chunk system inventory mismatch')
    payloads = []
    bodies_by_system = defaultdict(set)
    for row in canonical['bodies']:
        bodies_by_system[row['system_id64']].add((row['source_body_id64'], row.get('frontier_body_id')))
    for record in records:
        raw_bodies = record.get('bodies', [])
        if not isinstance(raw_bodies, list) or any(not isinstance(body, dict) for body in raw_bodies):
            raise ValueError('invalid source body inventory')
        expected = bodies_by_system[record['id64']]
        observed = {(body.get('id64'), body.get('bodyId')) for body in raw_bodies}
        if expected != observed or len(observed) != len(raw_bodies):
            raise ValueError('source/canonical chunk body inventory mismatch')
        payloads.append({'system': {'id64': record['id64'], 'bodies': [
            {key: body[key] for key in ('id64', 'bodyId', 'type', 'subType', 'updateTime') if key in body}
            for body in raw_bodies]}})
    facts = adapt_canonical_export(canonical, metadata, payloads)
    artifact_hash = metadata['artifact']['content_sha256'].removeprefix('\\x')
    hashes = {payload['system']['id64']: normalized_sha256(payload) for payload in payloads}
    result = {}
    for identifier, system in facts.items():
        bodies = []
        for body in system.bodies:
            provenance = dict(body.feature_provenance)
            classification = json.loads(provenance['body_class'])
            if classification.get('field') == 'subType':
                classification.update(source='Retained Spansh galaxy artifact',
                                      artifact_sha256=artifact_hash,
                                      source_projection_sha256=hashes[identifier],
                                      adapter=STREAM_VERSION)
                # The inherited hash describes the compact projection, not an
                # API response or full raw record. Give it its precise name.
                classification.pop('source_normalized_sha256', None)
                provenance['body_class'] = json.dumps(classification, sort_keys=True, separators=(',', ':'))
            bodies.append(replace(body, feature_provenance=provenance))
        result[identifier] = replace(system, bodies=tuple(bodies))
    return result


@dataclass(frozen=True)
class CanonicalSnapshot:
    generation_id: str
    publication_sequence: int
    schema: str
    expected_systems: int
    expected_bodies: int
    metadata: dict
    vocabularies: tuple[dict, ...]

    @classmethod
    def pin(cls, connection):
        """Resolve pointer and source metadata together under one read snapshot."""
        with connection.transaction():
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            row = connection.execute('''
                SELECT g.generation_id::text, c.publication_sequence, g.relation_schema,
                       g.validation_receipt->>'systems', g.validation_receipt->>'bodies',
                       json_build_object('status','success','read_only',true,'db_writes_performed',false,
                                         'run',to_jsonb(r),'artifact',to_jsonb(a),'source',to_jsonb(s),
                                         'generation_id',g.generation_id,
                                         'generation_inputs',(
                                             SELECT jsonb_agg(jsonb_build_object(
                                                 'input',to_jsonb(i),'run',to_jsonb(ir),
                                                 'artifact',to_jsonb(ia),'source',to_jsonb(src))
                                                 ORDER BY i.input_ordinal)
                                             FROM v3_meta.canonical_generation_input i
                                             JOIN v3_source.source_run ir USING(source_run_id)
                                             JOIN v3_source.source src ON src.source_id=ir.source_id
                                             LEFT JOIN v3_source.source_artifact ia ON ia.artifact_id=ir.artifact_id
                                             WHERE i.generation_id=g.generation_id))
                FROM v3_meta.current_canonical_generation c
                JOIN v3_meta.canonical_generation g USING(generation_id)
                JOIN v3_source.source_run r ON r.source_run_id=g.build_source_run_id
                JOIN v3_source.source_artifact a ON a.artifact_id=r.artifact_id
                JOIN v3_source.source s ON s.source_id=r.source_id
                WHERE g.lifecycle_state='PUBLISHED'
            ''').fetchone()
            if row is None or not re.fullmatch(r'v3_gen_[a-z][a-z0-9_]{0,30}', row[2]):
                raise ValueError('no valid published canonical generation')
            if not row[5]['run'].get('is_complete_snapshot'):
                raise ValueError('full generation requires complete canonical source acquisition')
            from psycopg import sql
            vocabularies = tuple({'schema': 'v3_vocab', 'relation': name,
                                  'rows': [item[0] for item in connection.execute(
                                      sql.SQL('SELECT to_jsonb(v) FROM v3_vocab.{} v ORDER BY {}').format(
                                          sql.Identifier(name), sql.Identifier(f'{name}_id'))) ]}
                                 for name in VOCABULARIES)
        return cls(row[0], row[1], row[2], int(row[3]), int(row[4]), row[5], vocabularies)

    def export_chunk(self, connection, system_ids: Iterable[int]) -> dict:
        """Read only this generation, regardless of subsequent pointer changes."""
        from psycopg import sql

        identifiers = _system_ids(system_ids)

        def rows(relation, column, values, limit, identity):
            query = sql.SQL('SELECT to_jsonb(t) FROM {}.{} t WHERE {}=ANY(%s) ORDER BY {} LIMIT %s').format(
                sql.Identifier(self.schema), sql.Identifier(relation), sql.Identifier(column),
                sql.SQL(', ').join(map(sql.Identifier, identity)))
            result = [row[0] for row in connection.execute(query, (values, limit + 1))]
            if len(result) > limit:
                raise ValueError(f'canonical {relation} exceeds bounded chunk limit')
            return result

        with connection.transaction():
            connection.execute('SET TRANSACTION READ ONLY')
            connection.execute("SET LOCAL statement_timeout='60s'")
            systems = rows('systems', 'id64', identifiers, MAX_CHUNK_SYSTEMS, ('id64',))
            if {row['id64'] for row in systems} != set(identifiers):
                raise ValueError('source system missing from pinned canonical generation')
            bodies = rows('bodies', 'system_id64', identifiers, MAX_CHUNK_BODIES, ('system_id64', 'body_pk'))
            rings = rows('rings', 'system_id64', identifiers, MAX_CHUNK_BODIES * 4, ('system_id64', 'ring_pk'))
            signals = rows('body_signal_current', 'body_pk', [body['body_pk'] for body in bodies],
                           MAX_CHUNK_BODIES * 8, ('body_pk', 'signal_type_id'))
        return {'status': 'success', 'read_only': True, 'db_writes_performed': False,
                'canonical_schema': self.schema, 'systems': systems, 'bodies': bodies,
                'extras': [*self.vocabularies,
                           {'schema': self.schema, 'relation': 'rings', 'rows': rings},
                           {'schema': self.schema, 'relation': 'body_signal_current', 'rows': signals}]}
