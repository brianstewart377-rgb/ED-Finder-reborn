#!/usr/bin/env python3
"""Resume a complete Ratings V4 build and stop at a validated generation.

This runner never applies migrations or publishes a derived pointer. Production
execution still requires a current, reviewed V3 operation authority.
"""
from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
import os
from pathlib import Path
import re
import sys
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.ratings_v4.canonical_stream import (  # noqa: E402
    MAX_CHUNK_SYSTEMS, CanonicalSnapshot, RetainedArtifactStream,
)
from scripts.ratings_v4.production_generation import (  # noqa: E402
    BODY_COLUMNS, OPPORTUNITY_COLUMNS, VECTOR_COLUMNS, _verify_code,
    _digest, code_identity, create_generation, encode_chunk, seal_source, validate_generation, write_chunk,
)

GENERATION_KEY = re.compile(r'[a-z][a-z0-9_]{0,62}\Z')
MAX_ENCODER_WORKERS = 16
Progress = Callable[[dict], None]


def _artifact(snapshot: CanonicalSnapshot) -> tuple[str, int]:
    artifact = snapshot.metadata.get('artifact')
    if not isinstance(artifact, dict):
        raise ValueError('canonical snapshot has no retained source artifact')
    digest = artifact.get('content_sha256')
    size = artifact.get('size_bytes')
    if not isinstance(digest, str) or not re.fullmatch(r'(?:\\x)?[0-9a-f]{64}', digest):
        raise ValueError('canonical artifact checksum is invalid')
    if type(size) is not int or size <= 0:
        raise ValueError('canonical artifact size is invalid')
    return digest.removeprefix('\\x'), size


def _generation(connection, snapshot: CanonicalSnapshot, generation_key: str, *, upgrade_actor=None):
    if not GENERATION_KEY.fullmatch(generation_key):
        raise ValueError('invalid derived generation key')
    row = connection.execute('''SELECT derived_generation_id,lifecycle_state,manifest,
        validation_receipt,manifest_sha256 FROM v3_meta.derived_generation WHERE generation_key=%s''',
        (generation_key,)).fetchone()
    if row is None:
        if upgrade_actor is not None:
            raise ValueError('parser upgrade requires an existing generation')
        identifier = create_generation(connection, snapshot, generation_key)
        return identifier, 'BUILDING', None, False, None
    identifier, state, manifest, validation, manifest_hash = row
    if _digest(manifest) != bytes(manifest_hash):
        raise ValueError('generation manifest checksum mismatch')
    compatible = manifest.get('code_sha256_lf') != code_identity()
    if compatible:
        from scripts.ratings_v4.resume_upgrade import verify_origin
        verify_origin(manifest, check_contract=True)
    if compatible and upgrade_actor is not None:
        from scripts.ratings_v4.resume_upgrade import approved_target, recorded_upgrade
        if recorded_upgrade(connection, identifier, manifest) is None:
            if state != 'BUILDING':
                raise ValueError('parser upgrade requires a BUILDING generation')
            if connection.execute("SELECT to_regclass('v3_meta.derived_code_upgrade')").fetchone()[0] is None:
                raise ValueError('reviewed parser upgrade migration is not installed')
            approved_target(connection)
    else:
        _verify_code(manifest, connection, identifier)
    expected = {
        'canonical_generation_id': snapshot.generation_id,
        'canonical_publication_sequence': snapshot.publication_sequence,
        'canonical_schema': snapshot.schema,
        'source_metadata': snapshot.metadata,
        'expected_systems': snapshot.expected_systems,
        'expected_bodies': snapshot.expected_bodies,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError('existing generation key belongs to another canonical input')
    return identifier, state, validation, True, manifest if compatible else None


def _write_encoded_chunk(connection, generation_id, ordinal, canonical, metadata,
                         payload, checkpoint):
    """Commit one already-encoded chunk with the same atomic checks as write_chunk."""
    from psycopg import sql

    if checkpoint[0] != generation_id or checkpoint[1] != ordinal:
        raise ValueError('encoded checkpoint identity mismatch')
    with connection.transaction():
        generation = connection.execute('''SELECT lifecycle_state,manifest FROM v3_meta.derived_generation
            WHERE derived_generation_id=%s FOR SHARE''', (generation_id,)).fetchone()
        if generation is None or generation[0] != 'BUILDING':
            raise ValueError('generation is not BUILDING')
        manifest = generation[1]
        _verify_code(manifest, connection, generation_id)
        if (manifest['source_metadata'] != metadata or
                manifest['canonical_schema'] != canonical['canonical_schema']):
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


def _emit_progress(progress, identifier, ordinal, systems, written):
    if progress is not None:
        progress({
            'derived_generation_id': str(identifier),
            'chunk_ordinal': ordinal,
            'systems': systems,
            'written': written,
        })


def _compact_source_records(records):
    """Keep only source fields that participate in V4 adaptation/checkpointing."""
    return tuple({
        'id64': record['id64'],
        'bodies': [
            {key: body[key] for key in ('id64', 'bodyId', 'type', 'subType', 'updateTime') if key in body}
            for body in record.get('bodies', [])
        ],
    } for record in records)


def _drain_oldest(pending, write_connection, identifier, metadata, progress):
    ordinal, systems, canonical, future = pending.popleft()
    payload, checkpoint = future.result()
    written = _write_encoded_chunk(
        write_connection, identifier, ordinal, canonical, metadata, payload, checkpoint,
    )
    _emit_progress(progress, identifier, ordinal, systems, written)
    return int(written)


def build_generation(read_connection, write_connection, source_path: Path,
                     generation_key: str, *, chunk_size: int = 500,
                     workers: int = 1, progress: Progress | None = None,
                     upgrade_actor: str | None = None) -> dict:
    """Build or resume one exact generation, validate it, and never publish it.

    Source parsing and canonical reads remain sequential and authoritative. With
    ``workers > 1``, only pure chunk encoding/scoring is sent to bounded child
    processes. Encoded chunks are committed in ordinal order by the parent using
    one derived connection, preserving the existing atomic checkpoint contract.
    """
    if type(chunk_size) is not int or not 1 <= chunk_size <= MAX_CHUNK_SYSTEMS:
        raise ValueError('chunk size outside bounded contract')
    if type(workers) is not int or not 1 <= workers <= MAX_ENCODER_WORKERS:
        raise ValueError('encoder worker count outside bounded contract')
    if upgrade_actor is not None and (not isinstance(upgrade_actor, str) or
                                     not upgrade_actor.strip() or len(upgrade_actor) > 200):
        raise ValueError('explicit parser upgrade actor is required')
    source_path = Path(source_path)
    if source_path.is_symlink() or not source_path.is_file():
        raise ValueError('retained artifact must be a regular non-symlink file')

    snapshot = CanonicalSnapshot.pin(read_connection)
    digest, size = _artifact(snapshot)
    if source_path.stat().st_size != size:
        raise ValueError('retained artifact size differs from canonical metadata')
    identifier, state, validation, resumed, upgrade_manifest = _generation(
        write_connection, snapshot, generation_key, upgrade_actor=upgrade_actor,
    )
    chunks_seen = chunks_written = 0
    chunks_reused = 0

    if state == 'BUILDING':
        stream = RetainedArtifactStream(source_path, sha256=digest, size_bytes=size)
        if upgrade_manifest is not None:
            from scripts.ratings_v4.resume_upgrade import remaining_chunks

            def reused(item):
                nonlocal chunks_seen, chunks_reused
                chunks_seen += 1
                chunks_reused += 1
                if progress is not None:
                    progress(item)

            chunks = remaining_chunks(stream, chunk_size, write_connection, identifier,
                                      upgrade_manifest, actor=upgrade_actor, progress=reused)
        else:
            chunks = enumerate(stream.chunks(chunk_size))
        if workers == 1:
            for ordinal, records in chunks:
                canonical = snapshot.export_chunk(
                    read_connection, [record['id64'] for record in records],
                )
                written = write_chunk(
                    write_connection, identifier, ordinal, canonical,
                    snapshot.metadata, records,
                )
                chunks_seen += 1
                chunks_written += int(written)
                _emit_progress(progress, identifier, ordinal, len(records), written)
        else:
            # Explicit spawn avoids depending on Python/platform multiprocessing
            # defaults and ensures child workers inherit no live DB connection.
            context = multiprocessing.get_context('spawn')
            executor = ProcessPoolExecutor(max_workers=workers, mp_context=context)
            pending = deque()
            try:
                for ordinal, records in chunks:
                    canonical = snapshot.export_chunk(
                        read_connection, [record['id64'] for record in records],
                    )
                    compact_records = _compact_source_records(records)
                    future = executor.submit(
                        encode_chunk, identifier, ordinal, canonical,
                        snapshot.metadata, compact_records,
                    )
                    pending.append((ordinal, len(records), canonical, future))
                    # Bound memory/IPC while still keeping every encoder busy.
                    if len(pending) >= workers:
                        chunks_written += _drain_oldest(
                            pending, write_connection, identifier, snapshot.metadata, progress,
                        )
                        chunks_seen += 1
                while pending:
                    chunks_written += _drain_oldest(
                        pending, write_connection, identifier, snapshot.metadata, progress,
                    )
                    chunks_seen += 1
            except BaseException:
                for _, _, _, future in pending:
                    future.cancel()
                executor.shutdown(wait=True, cancel_futures=True)
                raise
            else:
                executor.shutdown(wait=True)
        if stream.receipt is None:
            raise ValueError('retained artifact did not produce a verified EOF receipt')
        seal_source(write_connection, identifier, stream.receipt)
        state = 'VALIDATING'

    if state == 'VALIDATING':
        validation = validate_generation(write_connection, identifier)
        state = 'READY'
    elif state not in {'READY', 'PUBLISHED', 'RETIRED'}:
        raise ValueError(f'generation cannot resume from lifecycle state {state}')
    if not isinstance(validation, dict) or validation.get('status') != 'VERIFIED':
        raise ValueError('generation has no verified validation receipt')

    return {
        'status': 'VERIFIED',
        'derived_generation_id': str(identifier),
        'generation_key': generation_key,
        'lifecycle_state': state,
        'canonical_generation_id': snapshot.generation_id,
        'canonical_publication_sequence': snapshot.publication_sequence,
        'chunks_seen': chunks_seen,
        'chunks_written': chunks_written,
        'chunks_reused': chunks_reused,
        'resumed': resumed,
        'publication_performed': False,
        'validation_receipt': validation,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--generation-key', required=True)
    parser.add_argument('--chunk-size', type=int, default=500)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--upgrade-parser-actor', default=None,
                        help='Explicit actor for the reviewed legacy-parser transition; '
                             'requires its additive audit migration and a stopped old writer')
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    read_dsn = os.environ.get('RATINGS_V4_CANONICAL_DATABASE_URL')
    write_dsn = os.environ.get('RATINGS_V4_DERIVED_DATABASE_URL')
    if not read_dsn or not write_dsn:
        print(json.dumps({'status': 'FAILED', 'error': 'database_authority_missing'}), file=sys.stderr)
        return 64
    try:
        import psycopg
        with (psycopg.connect(read_dsn, autocommit=True) as read_connection,
              psycopg.connect(write_dsn, autocommit=True) as write_connection):
            receipt = build_generation(
                read_connection, write_connection, args.source,
                args.generation_key, chunk_size=args.chunk_size,
                workers=args.workers,
                upgrade_actor=args.upgrade_parser_actor,
                progress=lambda item: print(json.dumps({'status': 'PROGRESS', **item}, sort_keys=True)),
            )
    except Exception as exc:  # CLI emits no connection or source exception details.
        print(json.dumps({'status': 'FAILED', 'error': type(exc).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
