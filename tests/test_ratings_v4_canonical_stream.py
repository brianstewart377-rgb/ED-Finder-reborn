from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.ratings_v4.canonical_stream import (  # noqa: E402
    RetainedArtifactStream, _system_ids, adapt_retained_chunk, verify_recovered_source,
)
from scripts.ratings_v4 import canonical_stream  # noqa: E402
from domain.ratings_v4 import rate_system_facts  # noqa: E402
from domain.ratings_v4_canonical import (  # noqa: E402
    adapt_canonical_export, canonical_lineage, load_source_fixture, normalized_sha256,
)
from scripts.ratings_v4.generation import digest, materialize  # noqa: E402
from tests.ratings_v4_pg_fixture import canonical_database  # noqa: E402


def without_provenance(value):
    if isinstance(value, dict):
        return {key: without_provenance(item) for key, item in value.items()
                if key not in {'provenance', 'feature_provenance'}}
    if isinstance(value, (list, tuple)):
        return [without_provenance(item) for item in value]
    return value


def test_importer_differs_from_the_august_import_only_where_we_changed_it():
    """The recovery manifest fingerprints the importer that made the galaxy
    import of 27 August 2026. This repo then stopped the importer writing
    body_parent_evidence, which changed adapter.py and coverage.py, and made
    it keep each body's class (migration 027), which changed adapter.py and
    contracts.py. Nothing else in the manifest may drift, and the old check
    must say so plainly instead of passing."""
    manifest = json.loads((ROOT / 'docs/development/v3-canonical-source-recovery.json').read_bytes())
    changed = {
        relative for relative, expected in manifest['files_sha256'].items()
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected
    }
    assert changed == {
        'apps/importer/src/v3_spansh/adapter.py',
        'apps/importer/src/v3_spansh/contracts.py',
        'apps/importer/src/v3_spansh/coverage.py',
    }
    with pytest.raises(ValueError, match='recovered canonical source checksum mismatch'):
        verify_recovered_source()
    _, metadata, _ = load_source_fixture(ROOT / 'tests/fixtures/ratings_v4_sources')
    assert manifest['importer_package_sha256'] == metadata['run']['importer_code_sha256'].removeprefix('\\x')
    assert manifest['normalizer_sha256'] == metadata['run']['normalizer_sha256'].removeprefix('\\x')


def test_retained_source_adapter_preserves_all_84_frozen_ratings_and_unknowns():
    canonical, metadata, payloads = load_source_fixture(ROOT / 'tests/fixtures/ratings_v4_sources')
    expected = adapt_canonical_export(canonical, metadata, payloads)
    actual = adapt_retained_chunk(canonical, metadata, [payload['system'] for payload in payloads])
    assert len(actual) == 12
    for identifier, facts in actual.items():
        assert without_provenance(asdict(facts)) == without_provenance(asdict(expected[identifier]))
        for economy, rating in rate_system_facts(facts).items():
            assert without_provenance(asdict(rating)) == without_provenance(
                asdict(rate_system_facts(expected[identifier])[economy]))
        for body in facts.bodies:
            lineage = json.loads(body.feature_provenance['body_class'])
            assert lineage['source'] == 'Retained Spansh galaxy artifact'
            assert lineage['artifact_sha256'] == metadata['artifact']['content_sha256'].removeprefix('\\x')
            assert 'source_projection_sha256' in lineage
            assert body.reserve_scope == 'body'
            assert body.usable_ground_opportunity is None


@pytest.mark.parametrize('mutation', ['missing_system', 'missing_body', 'duplicate_body', 'wrong_frontier'])
def test_retained_source_requires_exact_canonical_inventory(mutation):
    canonical, metadata, payloads = deepcopy(load_source_fixture(ROOT / 'tests/fixtures/ratings_v4_sources'))
    records = [payload['system'] for payload in payloads]
    if mutation == 'missing_system':
        records.pop()
    elif mutation == 'missing_body':
        records[0]['bodies'].pop()
    elif mutation == 'duplicate_body':
        records[0]['bodies'].append(records[0]['bodies'][0])
    else:
        records[0]['bodies'][0]['bodyId'] += 1
    with pytest.raises(ValueError, match='inventory mismatch'):
        adapt_retained_chunk(canonical, metadata, records)


def artifact(tmp_path, records):
    data = gzip.compress(json.dumps(records).encode(), mtime=0)
    path = tmp_path / 'galaxy.json.gz'
    path.write_bytes(data)
    return RetainedArtifactStream(path, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))


def test_stream_seals_only_after_complete_consumption(tmp_path):
    stream = artifact(tmp_path, [{'id64': value, 'bodies': []} for value in range(7)])
    chunks = stream.chunks(3)
    assert len(next(chunks)) == 3
    assert stream.receipt is None
    assert [len(chunk) for chunk in chunks] == [3, 1]
    assert stream.receipt['systems'] == 7
    assert stream.receipt['consumed_to_eof'] is True
    with pytest.raises(ValueError, match='single use'):
        list(stream.chunks())


@pytest.mark.parametrize('prefix', [b'', b' \n\t' * 30_000], ids=['plain', 'long-whitespace'])
def test_valid_array_does_not_depend_on_gzip_peek(tmp_path, monkeypatch, prefix):
    records = [{'id64': 1, 'bodies': []}]
    stream = artifact(tmp_path, records)
    data = gzip.compress(prefix + json.dumps(records).encode(), mtime=0)
    stream.path.write_bytes(data)
    stream.expected_size = len(data)
    stream.expected_sha256 = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(gzip.GzipFile, 'peek', lambda *args: b'')
    assert list(stream.chunks()) == [records]
    assert stream.receipt['systems'] == 1


def test_object_with_item_key_cannot_impersonate_array(tmp_path):
    stream = artifact(tmp_path, {'item': {'id64': 1, 'bodies': []}})
    with pytest.raises(ValueError, match='JSON array'):
        list(stream.chunks())
    assert stream.receipt is None


def test_chunk_body_limit_resets_at_body_and_system_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(canonical_stream, 'MAX_CHUNK_BODIES', 5)
    counts = [3, 2, 1, 0, 4, 2]
    records = [{'id64': index, 'bodies': [{}] * count} for index, count in enumerate(counts)]
    stream = artifact(tmp_path, records)
    chunks = list(stream.chunks(2))
    assert [[r['id64'] for r in chunk] for chunk in chunks] == [[0, 1], [2, 3], [4], [5]]
    assert stream.receipt['systems'] == 6
    assert stream.receipt['bodies'] == sum(counts)
    oversized = artifact(tmp_path, [{'id64': 7, 'bodies': [{}] * 6}])
    with pytest.raises(ValueError, match='system exceeds bounded body inventory'):
        list(oversized.chunks())
    assert oversized.receipt is None


def test_chunk_body_accounting_visits_each_record_a_constant_number_of_times(tmp_path, monkeypatch):
    import ijson

    visits = 0

    class CountedRecord(dict):
        def get(self, key, default=None):
            nonlocal visits
            if key == 'bodies':
                visits += 1
            return super().get(key, default)

    original_items = ijson.items

    def counted_items(*args, **kwargs):
        for record in original_items(*args, **kwargs):
            yield CountedRecord(record)

    monkeypatch.setattr(ijson, 'items', counted_items)
    records = [{'id64': value, 'bodies': [{}]} for value in range(600)]
    stream = artifact(tmp_path, records)
    assert [len(chunk) for chunk in stream.chunks(500)] == [500, 100]
    assert visits <= 3 * len(records)
    assert stream.receipt['bodies'] == len(records)


def test_retained_inventory_indexes_canonical_bodies_once(monkeypatch):
    canonical, metadata, payloads = load_source_fixture(ROOT / 'tests/fixtures/ratings_v4_sources')
    visits = 0

    class CountedBody(dict):
        def __getitem__(self, key):
            nonlocal visits
            if key == 'system_id64':
                visits += 1
            return super().__getitem__(key)

    canonical['bodies'] = [CountedBody(row) for row in canonical['bodies']]
    # Isolate inventory validation from the separately tested scorer adapter.
    monkeypatch.setattr(canonical_stream, 'adapt_canonical_export', lambda *args: {})
    adapt_retained_chunk(canonical, metadata, [payload['system'] for payload in payloads])
    assert visits == len(canonical['bodies'])


@pytest.mark.parametrize('fault', ['hash', 'size', 'truncated', 'empty', 'object'])
def test_bad_artifact_never_receives_a_completion_receipt(tmp_path, fault):
    records = [] if fault == 'empty' else {} if fault == 'object' else [{'id64': 1}]
    stream = artifact(tmp_path, records)
    if fault == 'hash':
        stream.expected_sha256 = '0' * 64
    elif fault == 'size':
        stream.expected_size += 1
    elif fault == 'truncated':
        stream.path.write_bytes(stream.path.read_bytes()[:-4])
    with pytest.raises((ValueError, EOFError)):
        list(stream.chunks())
    assert stream.receipt is None


@pytest.mark.parametrize('values', [[], [True], [-1], [2**63], [1, 1], list(range(1001))])
def test_reader_rejects_ambiguous_or_unbounded_identity_lists(values):
    with pytest.raises(ValueError):
        _system_ids(values)


def test_postgres_reader_replays_real_canonical_relations_and_stays_pinned():
    from scripts.ratings_v4.canonical_stream import CanonicalSnapshot

    with canonical_database() as (connection, canonical, metadata, payloads):
        snapshot = CanonicalSnapshot.pin(connection)
        assert snapshot.expected_systems == 12
        assert snapshot.expected_bodies == 389
        assert snapshot.metadata['artifact']['content_sha256'] == metadata['artifact']['content_sha256']
        identifiers = [row['id64'] for row in canonical['systems']]
        exported = snapshot.export_chunk(connection, identifiers)
        actual = adapt_retained_chunk(exported, snapshot.metadata, [payload['system'] for payload in payloads])
        expected = adapt_canonical_export(canonical, metadata, payloads)
        for identifier in identifiers:
            assert without_provenance(asdict(actual[identifier])) == without_provenance(asdict(expected[identifier]))
        # An absent current pointer cannot redirect this reader to some other
        # canonical generation. It is already pinned to immutable relations.
        connection.execute('DELETE FROM v3_meta.current_canonical_generation')
        assert snapshot.export_chunk(connection, identifiers)['canonical_schema'] == snapshot.schema
        with pytest.raises(ValueError, match='no valid published'):
            CanonicalSnapshot.pin(connection)


def test_postgres_canonical_order_and_hashes_are_stable_across_scan_plans():
    def reverse_rows(canonical, metadata):
        canonical['systems'].sort(key=lambda row: row['id64'], reverse=True)
        canonical['bodies'].sort(key=lambda row: (row['system_id64'], row['body_pk']), reverse=True)
        for extra in canonical['extras']:
            if extra['relation'] == 'rings':
                keys = ('system_id64', 'ring_pk')
            elif extra['relation'] == 'body_signal_current':
                keys = ('body_pk', 'signal_type_id')
            elif extra['relation'] in canonical_stream.VOCABULARIES:
                keys = (extra['relation'] + '_id',)
            else:
                continue
            extra['rows'].sort(key=lambda row: tuple(row[key] for key in keys), reverse=True)
        return ()

    with canonical_database(prepare=reverse_rows) as (connection, canonical, _, payloads):
        identifiers = [row['id64'] for row in canonical['systems']]
        exports, derived = [], []
        for enabled in (False, True):
            # Force heap order first, then allow indexes. Source insert order is
            # deliberately reversed so an unordered read cannot pass by luck.
            connection.execute('SET enable_indexscan=' + ('on' if enabled else 'off'))
            connection.execute('SET enable_bitmapscan=' + ('on' if enabled else 'off'))
            snapshot = canonical_stream.CanonicalSnapshot.pin(connection)
            exported = snapshot.export_chunk(connection, identifiers)
            exports.append(exported)
            assert [r['id64'] for r in exported['systems']] == sorted(identifiers)
            body_ids = [(r['system_id64'], r['body_pk']) for r in exported['bodies']]
            assert body_ids == sorted(body_ids)
            for extra in exported['extras']:
                keys = (('system_id64', 'ring_pk') if extra['relation'] == 'rings' else
                        ('body_pk', 'signal_type_id') if extra['relation'] == 'body_signal_current' else
                        (extra['relation'] + '_id',))
                identities = [tuple(row[key] for key in keys) for row in extra['rows']]
                assert identities == sorted(identities)
            facts = adapt_retained_chunk(exported, snapshot.metadata, [p['system'] for p in payloads])
            derived.append(materialize(facts))
        assert exports[0] == exports[1]
        assert normalized_sha256(exports[0]) == normalized_sha256(exports[1])
        assert derived[0] == derived[1]
        assert digest(derived[0]) == digest(derived[1])


def test_postgres_pin_preserves_complete_admitted_manifest_and_row_lineage():
    extra_runs = []

    def add_sources(canonical, metadata):
        for _ in range(2):
            extra = deepcopy(metadata)
            extra['run']['source_run_id'] = str(uuid4())
            extra['run']['idempotency_key'] = str(uuid4())
            extra['run']['artifact_id'] = extra['artifact']['artifact_id'] = str(uuid4())
            extra['artifact']['content_sha256'] = '\\x' + hashlib.sha256(extra['run']['source_run_id'].encode()).hexdigest()
            extra_runs.append(extra)
        # Keep coordinator rows, add another admitted run across every relation,
        # and retain a third admitted run with no rows in this exported chunk.
        for rows in [canonical['systems'], canonical['bodies'], *[
                item['rows'] for item in canonical['extras'] if item['schema'] == canonical['canonical_schema']]]:
            for row in rows[::2]:
                row['source_run_id'] = extra_runs[0]['run']['source_run_id']
        return extra_runs

    with canonical_database(prepare=add_sources) as (connection, canonical, metadata, payloads):
        snapshot = canonical_stream.CanonicalSnapshot.pin(connection)
        entries = snapshot.metadata['generation_inputs']
        assert [entry['input']['input_ordinal'] for entry in entries] == [0, 1, 2]
        assert [entry['run']['source_run_id'] for entry in entries] == [
            item['run']['source_run_id'] for item in [metadata, *extra_runs]]
        assert all(entry['input']['generation_id'] == snapshot.generation_id for entry in entries)
        for entry in entries:
            assert entry['input']['source_run_id'] == entry['run']['source_run_id']
            assert entry['input']['artifact_id'] == entry['artifact']['artifact_id']
            assert entry['source']['source_id'] == entry['run']['source_id']
            assert entry['input']['input_role'] == 'TEST_FIXTURE'
            assert entry['input']['admitted_at']
        exported = snapshot.export_chunk(connection, [row['id64'] for row in canonical['systems']])
        facts = adapt_retained_chunk(exported, snapshot.metadata, [p['system'] for p in payloads])
        assert len(facts) == 12
        assert canonical_lineage(exported, snapshot.metadata, payloads)['canonical_generation_inputs'] == entries
        by_body = {row['body_pk']: row for row in exported['bodies']}
        by_run = {entry['run']['source_run_id']: entry['artifact'] for entry in entries}
        for system in facts.values():
            for body in system.bodies:
                expected_run = by_body[int(body.candidate_id)]['source_run_id']
                provenance = json.loads(body.feature_provenance['provenance'])
                assert provenance['source_run_id'] == expected_run
                assert provenance['artifact_sha256'] == by_run[expected_run]['content_sha256'].removeprefix('\\x')
        exported['bodies'][0]['source_run_id'] = str(uuid4())
        with pytest.raises(ValueError, match='unregistered source run'):
            adapt_retained_chunk(exported, snapshot.metadata, [p['system'] for p in payloads])
