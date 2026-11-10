"""One explicit, audited parser upgrade; never rewrite a generation manifest.

This is deliberately not a general version-compatibility switch. Only the exact
parallel builder used for prod-p4-opt1 may enter this transition. Deployment must
stop its old writer before starting the upgrade; database locks additionally
reject a checkpoint frontier that changed during prefix verification.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

LEGACY_SOURCE_SHA = 'fe0c058134b6fec33690fa937c2ac74338b401dc'
UPGRADE_ID = 'ratings-v4-direct-parser-1'
DIRECT_PARSER_SHA256_LF = '9a501bd1584f05b68612dca672e68dfaf69867412abb75a68fad72ce2cc9ad8e'
LEGACY_CODE = {
    'scripts/ratings_v4/production_generation.py': 'd47815984c257261c6e306f7a842f24fd8a0e7732633e47c9e013fb3a007f7b5',
    'scripts/ratings_v4/run_generation.py': '43c4554e83545560bb4fceb2cb3b889c1eebc1f9bbb53c0a97423f8511c8b361',
    'scripts/ratings_v4/canonical_stream.py': '160322dbeac52908a1dddf41efa5d5f97e1cb7b6bfa46ab4bc658671108ca738',
    'sql/v3/migrations/003_ratings_v4_derived.sql': 'c7818f98ad00b4b99b7ce0f7c3fa12ece555e59e761b7b1ca15d437a39c499cb',
}


def verify_origin(manifest, *, check_contract=False):
    from scripts.ratings_v4.production_generation import (
        BUILDER_VERSION, MECHANICS_VERSION, SCORER_VERSION, STREAM_VERSION,
        normalized_sha256, verify_contract, verify_recovered_source,
    )
    if manifest.get('code_sha256_lf') != LEGACY_CODE:
        raise ValueError('unsupported parser upgrade origin')
    root = Path(__file__).resolve().parents[2]
    parser = (root / 'scripts/ratings_v4/canonical_stream.py').read_text(encoding='utf-8')
    if hashlib.sha256(parser.encode()).hexdigest() != DIRECT_PARSER_SHA256_LF:
        raise ValueError('unsupported parser upgrade target')
    expected = {
        'builder_version': BUILDER_VERSION, 'mechanics_version': MECHANICS_VERSION,
        'scorer_version': SCORER_VERSION, 'adapter_version': STREAM_VERSION,
    }
    if check_contract:
        frozen, _, _ = verify_contract()
        expected.update(freeze_sha256=normalized_sha256(frozen),
                        importer_sha256=verify_recovered_source()['importer_package_sha256'])
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError('parser upgrade frozen contract changed')


def recorded_upgrade(connection, generation_id, manifest):
    """Return an exact execution attestation, or None when none was registered."""
    from scripts.ratings_v4.production_generation import _digest

    if connection is None or generation_id is None:
        return None
    if connection.execute("SELECT to_regclass('v3_meta.derived_code_upgrade')").fetchone()[0] is None:
        return None
    row = connection.execute('''SELECT receipt,receipt_sha256 FROM v3_meta.derived_code_upgrade
        WHERE derived_generation_id=%s''', (generation_id,)).fetchone()
    if row is None:
        return None
    verify_origin(manifest)
    approved = approved_target(connection)
    receipt = row[0]
    expected = {
        'upgrade_id': UPGRADE_ID, 'derived_generation_id': str(generation_id),
        'manifest_sha256': _digest(manifest).hex(), 'from_code_sha256_lf': LEGACY_CODE,
        'to_code_sha256_lf': approved,
    }
    if (_digest(receipt) != bytes(row[1]) or
            any(receipt.get(key) != value for key, value in expected.items())):
        raise ValueError('recorded parser upgrade identity mismatch')
    return receipt


def approved_target(connection):
    """The migration operator installs this independent, immutable policy first.

    The builder never inserts or updates policy. Capturing its own hashes is not
    approval: they must match the exact separately reviewed policy in the DB.
    """
    from scripts.ratings_v4.production_generation import code_identity

    row = connection.execute('''SELECT code_sha256_lf FROM v3_meta.derived_code_upgrade_target
        WHERE upgrade_id=%s''', (UPGRADE_ID,)).fetchone()
    if row is None or row[0] != code_identity():
        raise ValueError('parser upgrade target differs from independently approved identity')
    return row[0]


@dataclass
class CommittedPrefix:
    rows: tuple
    verified_chunks: int = 0

    @property
    def systems(self):
        return sum(row[4] for row in self.rows)

    @property
    def digest(self):
        from scripts.ratings_v4.production_generation import _digest
        return _digest(self.rows)

    def verify_source(self, ordinal, records):
        from scripts.ratings_v4.production_generation import _digest, _projection
        if ordinal != self.verified_chunks:
            raise ValueError('source prefix verification must be contiguous')
        row = self.rows[ordinal]
        if (len(records) != row[4] or
                sum(len(record.get('bodies', [])) for record in records) != row[5] or
                _digest([_projection(record) for record in records]) != bytes(row[1])):
            raise ValueError(f'committed source prefix mismatch at chunk {ordinal}')
        self.verified_chunks += 1

    def verify_attestation(self, receipt):
        count = receipt.get('prefix_chunks')
        if type(count) is not int or not 0 <= count <= len(self.rows):
            raise ValueError('attested checkpoint prefix is missing')
        original = CommittedPrefix(self.rows[:count])
        if (original.digest.hex() != receipt.get('prefix_sha256') or
                original.systems != receipt.get('prefix_systems')):
            raise ValueError('attested checkpoint prefix changed')


def committed_prefix(connection, generation_id):
    rows = connection.execute('''SELECT chunk_ordinal,source_projection_sha256,
        canonical_input_sha256,content_sha256,systems,canonical_bodies,
        physical_bodies,eligible_opportunities FROM v3_derived.build_chunk
        WHERE derived_generation_id=%s ORDER BY chunk_ordinal''', (generation_id,)).fetchall()
    if any(row[0] != index for index, row in enumerate(rows)):
        raise ValueError('committed checkpoint prefix is not contiguous')
    return CommittedPrefix(tuple(rows))


def accept_verified_prefix(connection, generation_id, manifest, prefix, *, actor=None):
    """Register once, only after the caller verified every committed source chunk.

    The caller holds the stream at the first uncommitted record. This transaction
    changes only the new append-only audit table. No old chunk or manifest moves.
    Repeated resumes require the previously recorded exact execution identity.
    """
    from scripts.ratings_v4.production_generation import _digest, _json

    if prefix.verified_chunks != len(prefix.rows):
        raise ValueError('every committed source chunk must be verified before upgrade')
    with connection.transaction():
        row = connection.execute('''SELECT lifecycle_state,manifest,manifest_sha256
            FROM v3_meta.derived_generation WHERE derived_generation_id=%s FOR UPDATE''',
            (generation_id,)).fetchone()
        if (row is None or row[0] != 'BUILDING' or row[1] != manifest or
                bytes(row[2]) != _digest(manifest)):
            raise ValueError('parser upgrade generation identity/state changed')
        if committed_prefix(connection, generation_id).digest != prefix.digest:
            raise ValueError('committed frontier changed; stop the old writer before upgrading')
        existing = recorded_upgrade(connection, generation_id, manifest)
        if existing is not None:
            prefix.verify_attestation(existing)
            return existing
        verify_origin(manifest)
        target = approved_target(connection)
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 200:
            raise ValueError('explicit parser upgrade actor is required')
        receipt = {
            'upgrade_id': UPGRADE_ID, 'derived_generation_id': str(generation_id),
            'manifest_sha256': _digest(manifest).hex(), 'from_code_sha256_lf': LEGACY_CODE,
            'to_code_sha256_lf': target, 'prefix_sha256': prefix.digest.hex(),
            'prefix_chunks': len(prefix.rows), 'prefix_systems': prefix.systems,
            'actor': actor, 'source_prefix_verified': True,
        }
        connection.execute('''INSERT INTO v3_meta.derived_code_upgrade(
            derived_generation_id,receipt,receipt_sha256) VALUES (%s,%s::jsonb,%s)''',
            (generation_id, _json(receipt), _digest(receipt)))
        return receipt


def remaining_chunks(stream, chunk_size, connection, generation_id, manifest, *, actor=None,
                     progress=None):
    """Parse/hash the retained prefix without repeating canonical reads/scoring.

    Canonical relations are immutable and pinned by the unchanged manifest.
    Stored content is still read back and scored in full by final validation.
    The stream continues normally, including the compressed checksum at EOF.
    """
    prefix = committed_prefix(connection, generation_id)
    accepted = False
    seen = 0
    for ordinal, records in enumerate(stream.chunks(chunk_size)):
        if ordinal < len(prefix.rows):
            prefix.verify_source(ordinal, records)
            seen += 1
            if progress is not None:
                progress({'derived_generation_id': str(generation_id), 'chunk_ordinal': ordinal,
                          'systems': len(records), 'written': False, 'resume_verified': True})
            continue
        if not accepted:
            accept_verified_prefix(connection, generation_id, manifest, prefix, actor=actor)
            accepted = True
        yield ordinal, records
    if seen != len(prefix.rows):
        raise ValueError('source ended before committed prefix')
    if not accepted:
        accept_verified_prefix(connection, generation_id, manifest, prefix, actor=actor)
