#!/usr/bin/env python3
"""Compare source parsers on repeated frozen inputs without production access.

This is a parsing benchmark, not an estimate of complete-generation throughput.
Both paths retain all record fields, bounded chunks and compressed EOF checks.
An optional complete-builder comparison uses only the local validation database.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack, contextmanager, nullcontext
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
from unittest.mock import patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.ratings_v4.canonical_stream import (  # noqa: E402
    RetainedArtifactStream, _ArrayRootReader,
)
from domain.ratings_v4_canonical import load_source_fixture  # noqa: E402


@contextmanager
def legacy_event_parser():
    """Reproduce the previous parse → Python events → items path for comparison."""
    import ijson

    original_items = ijson.items

    def items(source, prefix, **kwargs):
        if isinstance(source, _ArrayRootReader):
            source = source.stream
        events = ijson.parse(source, **kwargs)
        if next(events, None) != ('', 'start_array', None):
            raise ValueError('Spansh artifact must be a JSON array')
        return original_items(events, prefix)

    with patch.object(ijson, 'items', items):
        yield


def consume(path, sha256, size_bytes, *, legacy, verify=False):
    stream = RetainedArtifactStream(path, sha256=sha256, size_bytes=size_bytes)
    digest = hashlib.sha256() if verify else None
    systems = chunks = 0
    context = legacy_event_parser() if legacy else nullcontext()
    with context:
        started = time.perf_counter()
        for chunk in stream.chunks(12):
            systems += len(chunk)
            chunks += 1
            if digest is not None:
                # Separate parity runs keep JSON reserialization out of timings.
                digest.update(json.dumps(chunk, sort_keys=True, separators=(',', ':'),
                                         allow_nan=False).encode())
        seconds = time.perf_counter() - started
    if stream.receipt is None:
        raise ValueError('benchmark source was not verified to EOF')
    return {'seconds': seconds, 'systems': systems, 'chunks': chunks,
            'records_sha256': digest.hexdigest() if digest is not None else None,
            'source_receipt': stream.receipt}


def benchmark(*, repetitions=8, rounds=6):
    import ijson

    if type(repetitions) is not int or not 1 <= repetitions <= 8:
        raise ValueError('benchmark repetitions must be between 1 and 8')
    if type(rounds) is not int or not 2 <= rounds <= 10:
        raise ValueError('benchmark rounds must be between 2 and 10')
    _, _, payloads = load_source_fixture(ROOT / 'tests/fixtures/ratings_v4_sources')
    cohort = [payload['system'] for payload in payloads]
    cohort_bytes = json.dumps(cohort, separators=(',', ':')).encode()[1:-1]
    samples = []
    with tempfile.TemporaryDirectory(prefix='ratings-v4-parser-') as temporary:
        source = Path(temporary) / 'galaxy.json.gz'
        with source.open('wb') as output:
            with gzip.GzipFile(fileobj=output, mode='wb', mtime=0) as compressed:
                compressed.write(b'[')
                for repeat in range(repetitions):
                    if repeat:
                        compressed.write(b',')
                    compressed.write(cohort_bytes)
                compressed.write(b']')
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        size = source.stat().st_size
        parity = [consume(source, digest, size, legacy=legacy, verify=True)
                  for legacy in (True, False)]
        for field in ('systems', 'chunks', 'records_sha256', 'source_receipt'):
            if parity[0][field] != parity[1][field]:
                raise ValueError(f'parser parity failed: {field}')
        # Warm both paths, then alternate AB / BA to reduce ordering bias.
        for legacy in (True, False):
            consume(source, digest, size, legacy=legacy)
        for repeat in range(rounds):
            order = (True, False) if repeat % 2 == 0 else (False, True)
            for legacy in order:
                result = consume(source, digest, size, legacy=legacy)
                samples.append({'round': repeat, 'parser': 'legacy' if legacy else 'direct',
                                'seconds': result['seconds']})
    medians = {name: statistics.median(row['seconds'] for row in samples if row['parser'] == name)
               for name in ('legacy', 'direct')}
    return {
        'status': 'VERIFIED', 'scope': 'source parsing only; repeated frozen cohort',
        'python': sys.version, 'platform': platform.platform(), 'logical_cpus': os.cpu_count(),
        'ijson_version': ijson.__version__, 'ijson_backend': ijson.backend,
        'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                          for name in ('scripts/ratings_v4/canonical_stream.py',
                                       'scripts/ratings_v4/benchmark_source_parser.py')},
        'systems': len(cohort) * repetitions,
        'bodies': sum(len(record.get('bodies', [])) for record in cohort) * repetitions,
        'json_bytes': len(cohort_bytes) * repetitions + repetitions + 1,
        'gzip_bytes': size, 'gzip_sha256': digest,
        'rounds': rounds, 'samples': samples, 'median_seconds': medians,
        'parsing_speedup': medians['legacy'] / medians['direct'],
        'full_record_parity': True, 'records_sha256': parity[0]['records_sha256'],
        'source_receipt': parity[0]['source_receipt'],
        'database_access_performed': False, 'production_changes_performed': False,
    }


def benchmark_postgres_builder(*, rounds=4):
    """Measure complete builds only in the guarded disposable validation DB."""
    from psycopg import sql
    from scripts.ratings_v4 import production_generation as production, run_generation as runner
    from tests.ratings_v4_pg_fixture import canonical_database

    if not os.environ.get('RATINGS_V4_VALIDATION_DATABASE_URL'):
        raise ValueError('isolated PostgreSQL validation URL is required')
    if type(rounds) is not int or not 2 <= rounds <= 10:
        raise ValueError('benchmark rounds must be between 2 and 10')
    _, _, payloads = load_source_fixture(ROOT / 'tests/fixtures/ratings_v4_sources')
    records = [payload['system'] for payload in payloads]
    compressed = gzip.compress(json.dumps(records).encode(), mtime=0)

    def retained_fixture(_canonical, metadata):
        metadata['artifact']['content_sha256'] = '\\x' + hashlib.sha256(compressed).hexdigest()
        metadata['artifact']['size_bytes'] = len(compressed)
        return ()

    def build_one(source, legacy):
        stages = defaultdict(float)

        def timed(name, function):
            def wrapped(*args, **kwargs):
                started = time.perf_counter()
                try:
                    return function(*args, **kwargs)
                finally:
                    stages[name] += time.perf_counter() - started
            return wrapped

        original_chunks = runner.RetainedArtifactStream.chunks

        def chunks(stream, *args, **kwargs):
            iterator = original_chunks(stream, *args, **kwargs)
            while True:
                started = time.perf_counter()
                try:
                    chunk = next(iterator)
                except StopIteration:
                    return
                finally:
                    stages['source_parse'] += time.perf_counter() - started
                yield chunk

        # canonical_database rejects remote hosts and every non-validation DB
        # before creating its random v4_test_* database. No production DSN is read.
        with canonical_database(prepare=retained_fixture) as (connection, _, _, _):
            connection.execute((ROOT / 'sql/v3/migrations/003_ratings_v4_derived.sql').read_text())
            with ExitStack() as stack:
                stack.enter_context(patch.object(production, 'uuid4', lambda: UUID(
                    '6842309d-9775-448d-b8c7-44f081c11563')))
                if legacy:
                    stack.enter_context(legacy_event_parser())
                stack.enter_context(patch.object(runner.RetainedArtifactStream, 'chunks', chunks))
                stack.enter_context(patch.object(runner.CanonicalSnapshot, 'export_chunk',
                    timed('canonical_reads', runner.CanonicalSnapshot.export_chunk)))
                for name, label in (('_compact_source_records', 'source_compaction'),
                                    ('_write_encoded_chunk', 'ordered_writes'),
                                    ('_drain_oldest', 'drain_including_writes'),
                                    ('seal_source', 'source_seal'),
                                    ('validate_generation', 'validation')):
                    stack.enter_context(patch.object(runner, name, timed(label, getattr(runner, name))))
                started = time.perf_counter()
                receipt = runner.build_generation(connection, connection, source, 'parser_benchmark',
                                                  chunk_size=3, workers=2)
                seconds = time.perf_counter() - started
            if receipt['status'] != 'VERIFIED' or receipt['lifecycle_state'] != 'READY':
                raise ValueError('benchmark build did not pass full generation validation')
            output = {}
            for table, columns, order in (
                ('system_rating_vector', production.VECTOR_COLUMNS, 'system_id64'),
                ('body_mechanics', production.BODY_COLUMNS, 'system_id64,body_pk'),
                ('economy_opportunity', production.OPPORTUNITY_COLUMNS,
                 'system_id64,body_pk,economy_ordinal'),
            ):
                query = sql.SQL('SELECT {} FROM v3_derived.{} ORDER BY {}').format(
                    sql.SQL(',').join(map(sql.Identifier, columns)),
                    sql.Identifier(table), sql.SQL(order))
                output[table] = connection.execute(query).fetchall()
            output['chunks'] = connection.execute('''SELECT chunk_ordinal,
                source_projection_sha256,canonical_input_sha256,content_sha256,systems,
                canonical_bodies,physical_bodies,eligible_opportunities
                FROM v3_derived.build_chunk ORDER BY chunk_ordinal''').fetchall()
            output_sha256 = production._digest(output).hex()
        stages['encoder_wait_and_drain_overhead'] = (
            stages.pop('drain_including_writes') - stages['ordered_writes'])
        stages['setup_pool_and_other'] = seconds - sum(stages.values())
        return {'parser': 'legacy' if legacy else 'direct', 'seconds': seconds,
                'stages_seconds': dict(stages), 'output_sha256': output_sha256,
                'rows': {table: len(rows) for table, rows in output.items()},
                'validated': True, 'publication_performed': receipt['publication_performed']}

    samples = []
    with tempfile.TemporaryDirectory(prefix='ratings-v4-parser-build-') as temporary:
        source = Path(temporary) / 'galaxy.json.gz'
        source.write_bytes(compressed)
        warmups = [build_one(source, legacy) for legacy in (True, False)]
        for repeat in range(rounds):
            for legacy in (True, False) if repeat % 2 == 0 else (False, True):
                samples.append(dict(build_one(source, legacy), round=repeat))
    if len({row['output_sha256'] for row in [*warmups, *samples]}) != 1:
        raise ValueError('complete builder rows or chunk hashes differ between parsers')
    medians = {name: statistics.median(row['seconds'] for row in samples if row['parser'] == name)
               for name in ('legacy', 'direct')}
    stage_medians = {name: {stage: statistics.median(
        row['stages_seconds'][stage] for row in samples if row['parser'] == name)
        for stage in samples[0]['stages_seconds']} for name in ('legacy', 'direct')}
    return {'status': 'VERIFIED', 'scope': 'complete disposable PostgreSQL build and validation',
            'systems': len(records), 'chunk_size': 3, 'encoder_workers': 2,
            'rounds': rounds, 'samples': samples, 'median_seconds': medians,
            'stage_median_seconds': stage_medians,
            'fixture_build_speedup': medians['legacy'] / medians['direct'],
            'exact_rows_and_chunk_hashes_match': True,
            'output_sha256': samples[0]['output_sha256'],
            'production_changes_performed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=8)
    parser.add_argument('--rounds', type=int, default=6)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--postgres', action='store_true',
                        help='also benchmark complete builds in the guarded local validation DB')
    args = parser.parse_args()
    result = benchmark(repetitions=args.repetitions, rounds=args.rounds)
    if args.postgres:
        result['postgres_builder'] = benchmark_postgres_builder(rounds=args.rounds)
        result['database_access_performed'] = True
        result['database_scope'] = 'new disposable local validation databases only'
    text = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.receipt:
        args.receipt.write_text(text)
    print(text, end='')


if __name__ == '__main__':
    main()
