#!/usr/bin/env python3
'''Build and validate the generation-scoped V3 wide Archetype product.'''
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import time
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import v3_system_archetype_model as model  # noqa: E402

PRODUCT_CODE = 'system_archetype'
PRODUCT_VERSION = model.ARCHETYPE_VERSION
GENERATION_KEY = re.compile(r'[a-z][a-z0-9_]{0,62}\Z')
SCHEMA_NAME = re.compile(r'v3_gen_[a-z][a-z0-9_]{0,30}\Z')
SHA256_HEX = re.compile(r'[0-9a-f]{64}\Z')
Progress = Callable[[dict], None]


class ExpectedManifestMismatchError(ValueError):
    """The product manifest does not match the operator-approved identity."""


@dataclass(frozen=True)
class Generation:
    identifier: str
    key: str
    state: str
    canonical_generation_id: str
    canonical_publication_sequence: int
    canonical_schema: str
    expected_systems: int


def _json(value) -> str:
    def convert(item):
        if isinstance(item, (bytes, bytearray, memoryview)):
            return bytes(item).hex()
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat()
        return str(item)

    return json.dumps(
        value, default=convert, sort_keys=True, separators=(',', ':'),
        ensure_ascii=True, allow_nan=False,
    )


def _digest(value) -> bytes:
    return hashlib.sha256(_json(value).encode()).digest()


def code_identity(root: Path = ROOT) -> dict[str, str]:
    files = (
        'scripts/v3_system_archetype.py',
        'shared_contracts/v3_system_archetype_model.py',
        'scripts/v3_system_archetype_model.py',
        'sql/v3/migrations/011_v3_system_archetype.sql',
    )
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in files
    }


def _generation(connection, generation_key: str) -> Generation:
    if not GENERATION_KEY.fullmatch(generation_key):
        raise ValueError('invalid derived generation key')
    row = connection.execute(
        '''SELECT derived_generation_id::text,generation_key,lifecycle_state,
                  canonical_generation_id::text,canonical_publication_sequence,
                  manifest,expected_systems
             FROM v3_meta.derived_generation
            WHERE generation_key=%s''',
        (generation_key,),
    ).fetchone()
    if row is None:
        raise ValueError('unknown derived generation')
    manifest = row[5]
    schema = manifest.get('canonical_schema') if isinstance(manifest, dict) else None
    if not isinstance(schema, str) or not SCHEMA_NAME.fullmatch(schema):
        raise ValueError('derived generation has invalid canonical schema')
    return Generation(
        identifier=row[0], key=row[1], state=row[2],
        canonical_generation_id=row[3],
        canonical_publication_sequence=int(row[4]),
        canonical_schema=schema, expected_systems=int(row[6]),
    )


def _anchors() -> dict:
    return {
        key: {'required': list(required), 'supporting': list(supporting)}
        for key, (required, supporting) in model._ANCHORS.items()
    }


def _coefficients() -> dict:
    return {
        'alpha': model.ALPHA,
        'spec_floor': model.SPEC_FLOOR,
        'spec_span': model.SPEC_SPAN,
        'cap_floor': model.CAP_FLOOR,
        'cap_span': model.CAP_SPAN,
        'capacity_target': model.CAPACITY_TARGET,
        'synergy_bonus': model.SYNERGY_BONUS,
        'synergy_threshold': model.SYNERGY_THRESHOLD,
        'spec_unknown_conf': model.SPEC_UNKNOWN_CONF,
        'breadth_pot': model.BREADTH_POT,
        'breadth_qual': model.BREADTH_QUAL,
        'breadth_target': model.BREADTH_TARGET,
    }


def _weighted_key_sql(key: str) -> str:
    if key not in model.ARCHETYPE_KEYS:
        raise ValueError('unknown archetype key')
    return (
        f'(({key}_score::bigint * {key}_confidence_ppm * completeness_ppm) '
        '+ 50000000) / 100000000'
    )


def _wide_columns_manifest() -> list[dict[str, str]]:
    columns = [
        {'column': 'derived_generation_id', 'rule': 'generation identity'},
        {'column': 'system_id64', 'rule': 'Ratings V4 system identity'},
        {'column': 'archetype_version', 'rule': 'deployed model version'},
    ]
    for key in model.ARCHETYPE_KEYS:
        columns.extend((
            {'column': f'{key}_score', 'rule': 'score_raw_system'},
            {'column': f'{key}_tier', 'rule': 'tier_of(score)'},
            {
                'column': f'{key}_confidence_ppm',
                'rule': 'floor(round(confidence,6)*1000000+0.5)',
            },
            {
                'column': f'{key}_weighted_e4',
                'rule': _weighted_key_sql(key),
            },
        ))
    columns.extend(
        {'column': name, 'rule': rule}
        for name, rule in (
            ('primary_archetype', 'summarise'),
            ('secondary_archetype', 'summarise'),
            ('best_colony_potential', 'summarise'),
            ('best_tier', 'summarise'),
            ('archetype_confidence_ppm', 'floor(round(confidence,6)*1000000+0.5)'),
            ('weighted_potential', 'unrounded double precision product'),
            ('completeness_ppm', 'min(system_rating_vector.completeness) * 100'),
            ('computed_at', 'one explicitly assigned UTC instant per chunk'),
        )
    )
    return columns


def product_manifest(generation: Generation) -> dict:
    canonical_model = ROOT / 'shared_contracts/v3_system_archetype_model.py'
    return {
        'product_code': PRODUCT_CODE,
        'product_version': PRODUCT_VERSION,
        'derived_generation_id': generation.identifier,
        'generation_key': generation.key,
        'canonical_generation_id': generation.canonical_generation_id,
        'canonical_publication_sequence': generation.canonical_publication_sequence,
        'canonical_schema': generation.canonical_schema,
        'expected_rows': generation.expected_systems,
        'code_sha256': code_identity(),
        'model_file_sha256': hashlib.sha256(canonical_model.read_bytes()).hexdigest(),
        'archetype_model_sha256': model.archetype_model_sha256(),
        # Major.minor only: the complete read, the build and finalize all run
        # in the active API image, and a patch-level CPython bump between
        # them must not invalidate the expected manifest.
        'cpython_version': '.'.join(platform.python_version_tuple()[:2]),
        'storage_layout': 'one-row-per-system-v1',
        'explanation_delivery': 'on-demand',
        'confidence_storage': 'integer-parts-per-million',
        'confidence_rounding': (
            'floor(already-six-decimal-confidence * 1000000 + 0.5)'
        ),
        'wide_columns': _wide_columns_manifest(),
        'archetype_keys': list(model.ARCHETYPE_KEYS),
        'anchors': _anchors(),
        'coefficients': _coefficients(),
        'field_policy': {
            'archetype_score': (
                'round(clamp(core*spec*cap + synergy_bonus, 0, 100)); '
                'flexible uses breadth instead of core/spec/cap'
            ),
            'tier': 'S>=88, A>=76, B>=60, C>=45, else D',
            'confidence': (
                'min(confidence[a] for a in required anchors) * '
                'min(evidence_completeness[a] for a in required anchors), '
                'multiplied by spec_unknown_conf when any anchor quality was '
                'bounded-null; each factor is within [0, 1] so the product is '
                'bounded [0, 1]. flexible uses mean(confidence) * '
                'mean(evidence_completeness) over all anchors instead of min.'
            ),
            'confidence_storage': (
                'integer parts per million from the already-six-decimal model '
                'confidence using floor(value*1000000+0.5); exact comparison'
            ),
            'completeness_ppm': (
                'min(system_rating_vector.completeness) * 100 from unscaled smallint[]'
            ),
            'weighted_key': (
                '((score::bigint * confidence_ppm * completeness_ppm) + '
                '50000000) / 100000000 using non-negative integer division'
            ),
            'primary_archetype': (
                'highest score, ties broken by archetype_keys order'
            ),
            'secondary_archetype': (
                'next highest score; null only if every other score is 0'
            ),
            'weighted_potential': (
                'unrounded best_colony_potential * ((min(confidence)/10000) * '
                '(min(completeness)/10000))'
            ),
            'source': (
                'pinned Ratings V4 generation system_rating_vector and '
                'economy_opportunity only; no public schema reads'
            ),
        },
    }


def _product(connection, generation_id: str):
    return connection.execute(
        '''SELECT product_version,lifecycle_state,manifest,manifest_sha256,
                  expected_rows,validation_receipt,validation_sha256
             FROM v3_meta.derived_product
            WHERE derived_generation_id=%s AND product_code=%s''',
        (generation_id, PRODUCT_CODE),
    ).fetchone()


def _require_verified_search(connection, generation_id: str) -> None:
    row = connection.execute(
        '''SELECT lifecycle_state,validation_receipt
             FROM v3_meta.derived_product
            WHERE derived_generation_id=%s AND product_code='system_search' ''',
        (generation_id,),
    ).fetchone()
    if (
        row is None or row[0] != 'READY' or not isinstance(row[1], dict)
        or row[1].get('status') != 'VERIFIED'
    ):
        raise ValueError('same-generation system_search product is not READY/VERIFIED')


def register_product(
    connection, generation_key: str, expected_manifest_sha256: str | None = None,
) -> tuple[Generation, str, bytes]:
    if (
        expected_manifest_sha256 is not None
        and SHA256_HEX.fullmatch(expected_manifest_sha256) is None
    ):
        raise ExpectedManifestMismatchError('expected_manifest_mismatch')
    generation = _generation(connection, generation_key)
    if generation.state not in {'BUILDING', 'VALIDATING', 'READY', 'PUBLISHED'}:
        raise ValueError('generation cannot accept an Archetype product')
    _require_verified_search(connection, generation.identifier)
    manifest = product_manifest(generation)
    manifest_sha = _digest(manifest)
    existing = _product(connection, generation.identifier)
    registered_manifest_sha = (
        manifest_sha if existing is None else bytes(existing[3])
    )
    if (
        expected_manifest_sha256 is not None
        and registered_manifest_sha.hex() != expected_manifest_sha256
    ):
        raise ExpectedManifestMismatchError('expected_manifest_mismatch')
    if existing is None:
        with connection.transaction():
            connection.execute(
                '''INSERT INTO v3_meta.derived_product(
                       derived_generation_id,product_code,product_version,
                       manifest,manifest_sha256,expected_rows)
                   VALUES (%s,%s,%s,%s::jsonb,%s,%s)''',
                (
                    generation.identifier, PRODUCT_CODE, PRODUCT_VERSION,
                    _json(manifest), manifest_sha, generation.expected_systems,
                ),
            )
        existing = _product(connection, generation.identifier)
    if existing is None:
        raise ValueError('Archetype product registration failed')
    if (
        existing[0] != PRODUCT_VERSION or existing[2] != manifest
        or bytes(existing[3]) != manifest_sha
        or int(existing[4]) != generation.expected_systems
    ):
        raise ValueError('existing Archetype product manifest differs from current code/input')
    return generation, existing[1], manifest_sha


def _source_projection_sha(
    generation: Generation, manifest_sha: bytes, ordinal: int,
    canonical_input_sha: bytes, ratings_content_sha: bytes,
) -> bytes:
    return _digest({
        'product': PRODUCT_CODE,
        'product_version': PRODUCT_VERSION,
        'derived_generation_id': generation.identifier,
        'canonical_generation_id': generation.canonical_generation_id,
        'canonical_publication_sequence': generation.canonical_publication_sequence,
        'chunk_ordinal': ordinal,
        'product_manifest_sha256': manifest_sha.hex(),
        'ratings_canonical_input_sha256': bytes(canonical_input_sha).hex(),
        'ratings_content_sha256': bytes(ratings_content_sha).hex(),
    })


def _ratings_chunks(connection, generation_id: str):
    return connection.execute(
        '''SELECT b.chunk_ordinal,b.systems,b.canonical_input_sha256,b.content_sha256
             FROM v3_derived.build_chunk b
            WHERE b.derived_generation_id=%s ORDER BY b.chunk_ordinal''',
        (generation_id,),
    ).fetchall()


def _ratings_chunks_missing_archetype(connection, generation_id: str):
    return connection.execute(
        '''SELECT b.chunk_ordinal,b.systems,b.canonical_input_sha256,b.content_sha256
             FROM v3_derived.build_chunk b
        LEFT JOIN v3_derived.archetype_build_chunk a
               ON a.derived_generation_id=b.derived_generation_id
              AND a.chunk_ordinal=b.chunk_ordinal
            WHERE b.derived_generation_id=%s AND a.chunk_ordinal IS NULL
            ORDER BY b.chunk_ordinal''',
        (generation_id,),
    ).fetchall()


LOCK_TIMEOUT = '30s'
STATEMENT_TIMEOUT = '30min'


def configure_session(connection) -> None:
    '''Bound the session and keep every chunk read on its index path.

    lock_timeout keeps a blocked FOR SHARE / FOR NO KEY UPDATE from hanging the
    worker silently; statement_timeout is generous because validation's
    weighted-key gate scans the whole wide relation. VACUUM (ANALYZE) runs
    from the operator action through psql, not this session.

    The shared read constants (`VECTOR_READ_SQL`, `OPPORTUNITY_READ_SQL`)
    select their branch with parameter NULL tests. psycopg names a statement
    after five executions and the server may then plan it generically, without
    the parameter values: the opportunity join degrades to a hash join over
    full scans of both tables, which production run 39155668281 saw cancel at
    the calibration probe's 60 s statement timeout. This build issues the same
    two reads ~200,000 times, so custom plans are forced for the session, as
    `v3_archetype_calibration_complete` already does.
    '''
    connection.execute(
        "SELECT set_config('plan_cache_mode','force_custom_plan',false)",
    )
    connection.execute(
        "SELECT set_config('lock_timeout',%s,false)", (LOCK_TIMEOUT,),
    )
    connection.execute(
        "SELECT set_config('statement_timeout',%s,false)", (STATEMENT_TIMEOUT,),
    )


def _read_chunk_vectors(connection, generation_id: str, ordinal: int):
    '''Purely fetch raw arrays and integer opportunity aggregates.'''
    scope = {
        'generation_id': generation_id,
        'chunk_ordinal': ordinal,
        'system_id64': None,
    }
    vector_rows = connection.execute(
        model.VECTOR_READ_SQL, scope,
    ).fetchall()
    opportunity_rows = connection.execute(
        model.OPPORTUNITY_READ_SQL, scope,
    ).fetchall()
    groups: dict[int, list[tuple[int, int, int]]] = {}
    for system_id64, economy_ordinal, row_count, local_score_sum in opportunity_rows:
        groups.setdefault(int(system_id64), []).append(
            (int(economy_ordinal), int(row_count), int(local_score_sum or 0)),
        )
    return {
        int(row[0]): (
            model.SystemVectorRow.from_row(row),
            tuple(groups.get(int(row[0]), [])),
        )
        for row in vector_rows
    }


def _stored_value_columns() -> tuple[str, ...]:
    columns: list[str] = []
    for key in model.ARCHETYPE_KEYS:
        columns.extend((
            f'{key}_score', f'{key}_tier', f'{key}_confidence_ppm',
            f'{key}_weighted_e4',
        ))
    columns.extend((
        'primary_archetype', 'secondary_archetype', 'best_colony_potential',
        'best_tier', 'archetype_confidence_ppm', 'weighted_potential',
        'completeness_ppm',
    ))
    return tuple(columns)


STORED_VALUE_COLUMNS = _stored_value_columns()
ALL_STORED_COLUMNS = (
    'derived_generation_id', 'system_id64', 'archetype_version',
    *STORED_VALUE_COLUMNS, 'computed_at',
)


def _wide_tuple(
    generation_id: str, system_id64: int, scored: dict, computed_at: datetime,
) -> tuple:
    return (
        generation_id, system_id64, PRODUCT_VERSION,
        *(scored[column] for column in STORED_VALUE_COLUMNS), computed_at,
    )


def _chunk_rows(connection, generation_id: str, ordinal: int) -> list[tuple]:
    columns = ','.join(f'a.{column}' for column in ALL_STORED_COLUMNS)
    return connection.execute(
        f'''SELECT {columns}
              FROM v3_derived.system_archetype a
              JOIN v3_derived.system_rating_vector v
                ON v.derived_generation_id=a.derived_generation_id
               AND v.system_id64=a.system_id64
             WHERE a.derived_generation_id=%s AND v.chunk_ordinal=%s
             ORDER BY a.system_id64''',
        (generation_id, ordinal),
    ).fetchall()


def _chunk_content_sha(connection, generation_id: str, ordinal: int) -> bytes:
    return _rows_content_sha(_chunk_rows(connection, generation_id, ordinal))


def _rows_content_sha(rows: list[tuple]) -> bytes:
    return _digest(rows)


def _build_chunk(
    connection, generation: Generation, manifest_sha: bytes, chunk,
) -> tuple[bool, int]:
    ordinal, systems, canonical_input_sha, ratings_content_sha = chunk
    ordinal, systems = int(ordinal), int(systems)
    if ordinal < 0 or not 1 <= systems <= 1000:
        raise ValueError('Archetype chunk is outside the bounded Ratings contract')
    source_sha = _source_projection_sha(
        generation, manifest_sha, ordinal,
        bytes(canonical_input_sha), bytes(ratings_content_sha),
    )
    with connection.transaction():
        base = connection.execute(
            '''SELECT lifecycle_state FROM v3_meta.derived_generation
                WHERE derived_generation_id=%s FOR SHARE''',
            (generation.identifier,),
        ).fetchone()
        if base is None or base[0] not in {
            'BUILDING', 'VALIDATING', 'READY', 'PUBLISHED',
        }:
            raise ValueError('generation cannot accept Archetype rows')
        product = connection.execute(
            '''SELECT lifecycle_state,manifest_sha256
                 FROM v3_meta.derived_product
                WHERE derived_generation_id=%s AND product_code=%s FOR SHARE''',
            (generation.identifier, PRODUCT_CODE),
        ).fetchone()
        if product is None or product[0] != 'BUILDING':
            raise ValueError('Archetype product is not BUILDING')
        if bytes(product[1]) != manifest_sha:
            raise ValueError('Archetype product manifest changed')
        source = connection.execute(
            '''SELECT systems,canonical_input_sha256,content_sha256
                 FROM v3_derived.build_chunk
                WHERE derived_generation_id=%s AND chunk_ordinal=%s
                FOR NO KEY UPDATE''',
            (generation.identifier, ordinal),
        ).fetchone()
        if (
            source is None or int(source[0]) != systems
            or bytes(source[1]) != bytes(canonical_input_sha)
            or bytes(source[2]) != bytes(ratings_content_sha)
        ):
            raise ValueError('Archetype Ratings chunk source changed')
        receipt = connection.execute(
            '''SELECT archetype_version,source_projection_sha256,content_sha256,systems
                 FROM v3_derived.archetype_build_chunk
                WHERE derived_generation_id=%s AND chunk_ordinal=%s''',
            (generation.identifier, ordinal),
        ).fetchone()
        if receipt is not None:
            if (
                receipt[0] != PRODUCT_VERSION or bytes(receipt[1]) != source_sha
                or int(receipt[3]) != systems
            ):
                raise ValueError('resumed Archetype chunk source/coverage changed')
            return False, 0
        preexisting = connection.execute(
            '''SELECT count(*) FROM v3_derived.system_archetype a
                 JOIN v3_derived.system_rating_vector v
                   ON v.derived_generation_id=a.derived_generation_id
                  AND v.system_id64=a.system_id64
                WHERE a.derived_generation_id=%s AND v.chunk_ordinal=%s''',
            (generation.identifier, ordinal),
        ).fetchone()[0]
        if preexisting:
            raise ValueError('unreceipted Archetype rows already exist for chunk')
        raw = _read_chunk_vectors(connection, generation.identifier, ordinal)
        if len(raw) != systems:
            raise ValueError('Archetype chunk did not cover every Ratings system')
        computed_at = datetime.now(timezone.utc)
        rows = []
        for system_id64 in sorted(raw):
            vector_row, opportunity_groups = raw[system_id64]
            scored = model.score_raw_system(vector_row, opportunity_groups)
            rows.append(_wide_tuple(
                generation.identifier, system_id64, scored, computed_at,
            ))
        placeholders = ','.join(['%s'] * len(ALL_STORED_COLUMNS))
        columns = ','.join(ALL_STORED_COLUMNS)
        with connection.cursor() as cursor:
            cursor.executemany(
                f'''INSERT INTO v3_derived.system_archetype({columns})
                    VALUES ({placeholders})''',
                rows,
            )
        content_sha = _chunk_content_sha(connection, generation.identifier, ordinal)
        connection.execute(
            '''INSERT INTO v3_derived.archetype_build_chunk(
                   derived_generation_id,chunk_ordinal,product_code,archetype_version,
                   source_projection_sha256,content_sha256,systems)
               VALUES (%s,%s,%s,%s,%s,%s,%s)''',
            (
                generation.identifier, ordinal, PRODUCT_CODE, PRODUCT_VERSION,
                source_sha, content_sha, systems,
            ),
        )
    return True, systems


def build_available(
    connection, generation: Generation, manifest_sha: bytes, *,
    max_chunks: int | None = None, progress: Progress | None = None,
) -> dict:
    if max_chunks is not None and (type(max_chunks) is not int or max_chunks < 0):
        raise ValueError('max_chunks must be a non-negative integer')
    chunks_seen = chunks_written = systems_written = 0
    if max_chunks != 0:
        for chunk in _ratings_chunks_missing_archetype(
            connection, generation.identifier,
        ):
            written, systems = _build_chunk(
                connection, generation, manifest_sha, chunk,
            )
            chunks_seen += 1
            if written:
                chunks_written += 1
                systems_written += systems
                if progress is not None:
                    progress({
                        'derived_generation_id': generation.identifier,
                        'product_code': PRODUCT_CODE,
                        'chunk_ordinal': int(chunk[0]),
                        'systems': systems,
                        'written': True,
                    })
            if max_chunks is not None and chunks_written >= max_chunks:
                break
    return {
        'chunks_seen': chunks_seen,
        'chunks_written': chunks_written,
        'systems_written': systems_written,
    }


def _failed_receipt(generation: Generation, reasons: list[str], counts: dict) -> dict:
    return {
        'status': 'FAILED',
        'derived_generation_id': generation.identifier,
        'product_code': PRODUCT_CODE,
        'base_lifecycle_state': generation.state,
        'expected_systems': generation.expected_systems,
        **counts,
        'reasons': reasons,
    }


def _gate_stored_weighted_keys(connection, generation_id: str) -> list[str]:
    comparisons = ' OR '.join(
        f'{key}_weighted_e4 <> {_weighted_key_sql(key)}'
        for key in model.ARCHETYPE_KEYS
    )
    bad = int(connection.execute(
        f'''SELECT count(*) FROM v3_derived.system_archetype
             WHERE derived_generation_id=%s AND ({comparisons})''',
        (generation_id,),
    ).fetchone()[0])
    if bad:
        return [
            f'invariants: {bad} wide row(s) have a weighted key that does not '
            'match stored completeness_ppm',
        ]
    return []


def _validate_rows_and_seals(
    connection, generation: Generation, manifest_sha: bytes, rating_chunks,
    progress: Progress | None = None,
) -> tuple[list[str], list[dict]]:
    reasons: list[str] = []
    receipt_payloads: list[dict] = []
    total = len(rating_chunks)
    for index, (ordinal, systems, canonical_input_sha, ratings_content_sha) in enumerate(
        rating_chunks, start=1,
    ):
        ordinal, systems = int(ordinal), int(systems)
        if progress is not None and (index % 1000 == 0 or index == total):
            progress({
                'derived_generation_id': generation.identifier,
                'product_code': PRODUCT_CODE,
                'phase': 'validate',
                'chunks_validated': index,
                'chunks_total': total,
                'reasons_so_far': len(reasons),
            })
        receipt = connection.execute(
            '''SELECT archetype_version,source_projection_sha256,content_sha256,systems
                 FROM v3_derived.archetype_build_chunk
                WHERE derived_generation_id=%s AND chunk_ordinal=%s''',
            (generation.identifier, ordinal),
        ).fetchone()
        if receipt is None:
            reasons.append(f'coverage: missing chunk receipt {ordinal}')
            continue
        expected_source = _source_projection_sha(
            generation, manifest_sha, ordinal,
            bytes(canonical_input_sha), bytes(ratings_content_sha),
        )
        if (
            receipt[0] != PRODUCT_VERSION or bytes(receipt[1]) != expected_source
            or int(receipt[3]) != systems
        ):
            reasons.append(f'reproducibility: chunk {ordinal} receipt mismatch')
        # One read of the wide rows serves both the content seal and the
        # per-column comparison (the build sealed the same ordered row set).
        stored = _chunk_rows(connection, generation.identifier, ordinal)
        if bytes(receipt[2]) != _rows_content_sha(stored):
            reasons.append(f'reproducibility: chunk {ordinal} content seal mismatch')
        raw = _read_chunk_vectors(connection, generation.identifier, ordinal)
        if len(raw) != systems or len(stored) != systems:
            reasons.append(f'coverage: chunk {ordinal} row count mismatch')
            continue
        stored_by_system = {int(row[1]): row for row in stored}
        for system_id64, (vector_row, opportunity_groups) in raw.items():
            row = stored_by_system.get(system_id64)
            if row is None:
                reasons.append(f'coverage: system {system_id64} wide row missing')
                continue
            scored = model.score_raw_system(vector_row, opportunity_groups)
            if row[2] != PRODUCT_VERSION:
                reasons.append(f'invariants: system {system_id64} version mismatch')
            actual_values = row[3:-1]
            expected_values = tuple(scored[column] for column in STORED_VALUE_COLUMNS)
            actual_by_column = dict(zip(
                STORED_VALUE_COLUMNS, actual_values, strict=True,
            ))
            for key in model.ARCHETYPE_KEYS:
                stored_weighted = model.weighted_key_e4(
                    actual_by_column[f'{key}_score'],
                    actual_by_column[f'{key}_confidence_ppm'],
                    actual_by_column['completeness_ppm'],
                )
                if actual_by_column[f'{key}_weighted_e4'] != stored_weighted:
                    reasons.append(
                        f'invariants: system {system_id64} {key}_weighted_e4 '
                        'does not match stored completeness_ppm',
                    )
            for column, actual, expected in zip(
                STORED_VALUE_COLUMNS, actual_values, expected_values, strict=True,
            ):
                if actual != expected:
                    reasons.append(
                        f'invariants: system {system_id64} {column} mismatch',
                    )
        receipt_payloads.append({
            'chunk_ordinal': ordinal,
            'source_projection_sha256': bytes(receipt[1]).hex(),
            'content_sha256': bytes(receipt[2]).hex(),
            'systems': int(receipt[3]),
        })
    return reasons, receipt_payloads


def validate_product(
    connection, generation: Generation, manifest_sha: bytes,
    progress: Progress | None = None,
) -> dict:
    '''Validate every chunk and promote the product READY.

    Runs inside the detached build worker once every chunk is committed (the
    Search pattern), never in a bounded foreground container: it re-reads and
    re-scores all ~198.5 M systems, which takes hours. It is restartable from
    the start with no manual cleanup, and holds no transaction until the single
    READY promotion at the end.
    '''
    generation = _generation(connection, generation.key)
    _require_verified_search(connection, generation.identifier)
    product = _product(connection, generation.identifier)
    if product is None:
        raise ValueError('Archetype product is not registered')
    if bytes(product[3]) != manifest_sha:
        raise ValueError('Archetype product manifest changed')
    product_manifest_value = product[2]
    if not isinstance(product_manifest_value, dict):
        raise ValueError('Archetype product manifest is not an object')
    current_model_sha = model.archetype_model_sha256()
    current_file_sha = hashlib.sha256(
        (ROOT / 'shared_contracts/v3_system_archetype_model.py').read_bytes(),
    ).hexdigest()
    if (
        product_manifest_value.get('archetype_model_sha256') != current_model_sha
        or product_manifest_value.get('model_file_sha256') != current_file_sha
    ):
        raise ValueError('deployed Archetype model identity differs from product manifest')
    if product[1] == 'READY':
        receipt = product[5]
        if (
            not isinstance(receipt, dict)
            or receipt.get('status') != 'VERIFIED'
            or receipt.get('archetype_model_sha256') != current_model_sha
            or receipt.get('model_file_sha256') != current_file_sha
        ):
            raise ValueError('READY Archetype product has no VERIFIED receipt')
        return receipt
    if product[1] != 'BUILDING':
        raise ValueError('Archetype product cannot be validated from current state')
    vector_count = int(connection.execute(
        '''SELECT count(*) FROM v3_derived.system_rating_vector
            WHERE derived_generation_id=%s''',
        (generation.identifier,),
    ).fetchone()[0])
    wide_count = int(connection.execute(
        '''SELECT count(*) FROM v3_derived.system_archetype
            WHERE derived_generation_id=%s''',
        (generation.identifier,),
    ).fetchone()[0])
    rating_chunks = _ratings_chunks(connection, generation.identifier)
    receipt_count = int(connection.execute(
        '''SELECT count(*) FROM v3_derived.archetype_build_chunk
            WHERE derived_generation_id=%s''',
        (generation.identifier,),
    ).fetchone()[0])
    counts = {
        'systems': vector_count,
        'wide_rows': wide_count,
        'rating_chunks': len(rating_chunks),
        'archetype_chunks': receipt_count,
    }
    coverage_reasons = []
    if vector_count != generation.expected_systems:
        coverage_reasons.append('coverage: rating-vector count mismatch')
    if wide_count != vector_count:
        coverage_reasons.append('coverage: wide-row count mismatch')
    if receipt_count != len(rating_chunks):
        coverage_reasons.append('coverage: missing chunk receipt')
    if generation.state not in {'READY', 'PUBLISHED'}:
        return {
            'status': 'INCOMPLETE',
            'derived_generation_id': generation.identifier,
            'product_code': PRODUCT_CODE,
            'base_lifecycle_state': generation.state,
            'expected_systems': generation.expected_systems,
            **counts,
            'reasons': [
                f'base generation not ready (state={generation.state})',
                *coverage_reasons,
            ],
        }
    if coverage_reasons:
        return _failed_receipt(generation, coverage_reasons, counts)
    weighted_reasons = _gate_stored_weighted_keys(
        connection, generation.identifier,
    )
    reasons, chunk_payloads = _validate_rows_and_seals(
        connection, generation, manifest_sha, rating_chunks, progress,
    )
    reasons = [*weighted_reasons, *reasons]
    if reasons:
        return _failed_receipt(generation, reasons, counts)
    receipt = {
        'status': 'VERIFIED',
        'derived_generation_id': generation.identifier,
        'product_code': PRODUCT_CODE,
        'product_version': PRODUCT_VERSION,
        'base_lifecycle_state': generation.state,
        'expected_systems': generation.expected_systems,
        'systems': vector_count,
        'wide_rows': wide_count,
        'chunks': len(chunk_payloads),
        'coverage_complete': True,
        'invariants_ok': True,
        'seals_verified': True,
        'product_manifest_sha256': manifest_sha.hex(),
        'archetype_model_sha256': current_model_sha,
        'model_file_sha256': current_file_sha,
        'reasons': [],
    }
    validation_digest = hashlib.sha256()
    validation_digest.update(manifest_sha)
    for payload in chunk_payloads:
        validation_digest.update(_json(payload).encode())
    validation_digest.update(_json(receipt).encode())
    validation_sha = validation_digest.digest()
    with connection.transaction():
        updated = connection.execute(
            '''UPDATE v3_meta.derived_product
                  SET lifecycle_state='READY',validation_receipt=%s::jsonb,
                      validation_sha256=%s,validated_at=now()
                WHERE derived_generation_id=%s AND product_code=%s
                  AND lifecycle_state='BUILDING' ''',
            (_json(receipt), validation_sha, generation.identifier, PRODUCT_CODE),
        ).rowcount
        if updated != 1:
            refreshed = _product(connection, generation.identifier)
            if refreshed is None or refreshed[1] != 'READY':
                raise ValueError('Archetype product validation state changed')
    return receipt


def run(
    connection, generation_key: str, *, follow: bool = False,
    poll_seconds: float = 5.0, max_chunks: int | None = None,
    progress: Progress | None = None,
    expected_manifest_sha256: str | None = None,
) -> dict:
    if not isinstance(poll_seconds, (int, float)) or poll_seconds <= 0:
        raise ValueError('poll_seconds must be positive')
    generation, state, manifest_sha = register_product(
        connection, generation_key, expected_manifest_sha256,
    )
    if state not in {'BUILDING', 'READY'}:
        raise ValueError('Archetype product cannot be built from current state')
    total_seen = total_written = 0
    if state == 'READY':
        receipt = validate_product(connection, generation, manifest_sha, progress)
        return {
            'status': 'VERIFIED',
            'derived_generation_id': generation.identifier,
            'product_code': PRODUCT_CODE,
            'chunks_seen': 0,
            'chunks_written': 0,
            'validation_receipt': receipt,
        }
    while True:
        result = build_available(
            connection, generation, manifest_sha,
            max_chunks=max_chunks, progress=progress,
        )
        total_seen += result['chunks_seen']
        total_written += result['chunks_written']
        generation = _generation(connection, generation_key)
        if generation.state == 'FAILED':
            return {
                'status': 'FAILED',
                'derived_generation_id': generation.identifier,
                'product_code': PRODUCT_CODE,
                'chunks_seen': total_seen,
                'chunks_written': total_written,
                'error': 'base_generation_failed',
            }
        rating_row = connection.execute(
            '''SELECT count(*),coalesce(sum(systems),0)
                 FROM v3_derived.build_chunk WHERE derived_generation_id=%s''',
            (generation.identifier,),
        ).fetchone()
        rating_chunks, rating_systems = int(rating_row[0]), int(rating_row[1])
        archetype_chunks = int(connection.execute(
            '''SELECT count(*) FROM v3_derived.archetype_build_chunk
                WHERE derived_generation_id=%s''',
            (generation.identifier,),
        ).fetchone()[0])
        complete = (
            generation.state in {'READY', 'PUBLISHED'} and rating_chunks > 0
            and archetype_chunks == rating_chunks
            and rating_systems == generation.expected_systems
        )
        if complete:
            if max_chunks is not None:
                return {
                    'status': 'BUILT',
                    'derived_generation_id': generation.identifier,
                    'product_code': PRODUCT_CODE,
                    'chunks_seen': total_seen,
                    'chunks_written': total_written,
                }
            # Every chunk is sealed: validate in this worker and promote READY,
            # so finalize only has to confirm READY/VERIFIED and VACUUM.
            receipt = validate_product(connection, generation, manifest_sha, progress)
            status = 'VERIFIED' if receipt.get('status') == 'VERIFIED' else 'FAILED'
            return {
                'status': status,
                'derived_generation_id': generation.identifier,
                'product_code': PRODUCT_CODE,
                'chunks_seen': total_seen,
                'chunks_written': total_written,
                'validation_receipt': receipt,
            }
        if not follow or max_chunks is not None:
            return {
                'status': 'INCOMPLETE',
                'derived_generation_id': generation.identifier,
                'product_code': PRODUCT_CODE,
                'chunks_seen': total_seen,
                'chunks_written': total_written,
            }
        time.sleep(float(poll_seconds))


CREDENTIAL_URI = re.compile(r'[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@', re.IGNORECASE)


def redact_credentials(text: str, dsn: str | None = None) -> str:
    '''Strip credential-bearing URIs (and the live DSN itself) from text.

    libpq/psycopg connection errors can echo the full DSN, password included.
    '''
    if dsn:
        text = text.replace(dsn, '<redacted-dsn>')
    return CREDENTIAL_URI.sub('<redacted-dsn>@', text)


def failure_receipt(exc: BaseException, dsn: str | None = None) -> dict:
    '''Bounded failure receipt: class, message and SQLSTATE, never the DSN.'''
    dsn = dsn if dsn is not None else os.environ.get('V3_SYSTEM_ARCHETYPE_DATABASE_URL')
    detail = redact_credentials(f'{type(exc).__name__}: {exc}', dsn)
    receipt = {
        'status': 'FAILED',
        'error': type(exc).__name__,
        'error_detail': detail[:500],
    }
    sqlstate = getattr(exc, 'sqlstate', None)
    if isinstance(sqlstate, str):
        receipt['sqlstate'] = sqlstate
    return receipt


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generation-key', required=True)
    parser.add_argument('--follow', action='store_true')
    parser.add_argument('--poll-seconds', type=float, default=5.0)
    parser.add_argument('--max-chunks', type=int)
    parser.add_argument('--validate', action='store_true')
    parser.add_argument('--expected-manifest-sha256')
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    dsn = os.environ.get('V3_SYSTEM_ARCHETYPE_DATABASE_URL')
    if not dsn:
        print(
            json.dumps({'status': 'FAILED', 'error': 'database_authority_missing'}),
            file=sys.stderr,
        )
        return 64
    try:
        import psycopg
        with psycopg.connect(dsn, autocommit=True) as connection:
            configure_session(connection)
            if args.validate:
                generation, _, manifest_sha = register_product(
                    connection, args.generation_key,
                    args.expected_manifest_sha256,
                )
                result = validate_product(connection, generation, manifest_sha)
            else:
                result = run(
                    connection, args.generation_key, follow=args.follow,
                    poll_seconds=args.poll_seconds, max_chunks=args.max_chunks,
                    expected_manifest_sha256=args.expected_manifest_sha256,
                    progress=lambda item: print(json.dumps(
                        {'status': 'PROGRESS', **item}, sort_keys=True,
                    ), flush=True),
                )
    except ExpectedManifestMismatchError:
        print(
            json.dumps({
                'status': 'FAILED', 'error': 'expected_manifest_mismatch',
            }),
            file=sys.stderr,
        )
        return 65
    except Exception as exc:
        print(json.dumps(failure_receipt(exc, dsn)), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    if result.get('status') == 'FAILED':
        return 1
    if args.validate and result.get('status') != 'VERIFIED':
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
