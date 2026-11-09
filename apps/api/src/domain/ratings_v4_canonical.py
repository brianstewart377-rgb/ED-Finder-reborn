"""Read-only, provenance-preserving V3 canonical snapshot adapter for Ratings V4.

Detailed subtype enrichment is a separate source snapshot. It is never treated
as an update to canonical rings, signals, reserve observations or landability.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
from zipfile import ZipFile

from .ratings_v4 import BodyFact, SystemFacts


ADAPTER_VERSION = 'v4-canonical-adapter-1'
_GENERATION = re.compile(r'^v3_gen_[a-z][a-z0-9_]{0,30}$')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')


def normalized_sha256(value: Any) -> str:
    """Hash parsed source content independently of whitespace/JSON key order."""
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _id(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f'{name} must be a nonnegative integer')
    return value


def _boolean(value: Any, name: str) -> bool | None:
    if value is not None and type(value) is not bool:
        raise ValueError(f'{name} must be boolean or null')
    return value


def _index(rows: Iterable[Mapping[str, Any]], keys: tuple[str, ...], name: str) -> dict:
    result = {}
    for row in rows:
        identity = tuple(row[key] for key in keys)
        if identity in result:
            raise ValueError(f'duplicate {name} identity: {identity}')
        result[identity] = row
    return result


def _source_contract(canonical: Mapping, metadata: Mapping) -> tuple[Mapping, Mapping, dict]:
    if (canonical.get('status') != 'success' or canonical.get('read_only') is not True
            or canonical.get('db_writes_performed') is not False):
        raise ValueError('canonical export must be a successful read-only snapshot')
    schema = canonical.get('canonical_schema', '')
    if not _GENERATION.fullmatch(schema):
        raise ValueError('invalid canonical generation schema')
    run, artifact = metadata['run'], metadata['artifact']
    if (metadata.get('status') != 'success' or metadata.get('read_only') is not True
            or metadata.get('db_writes_performed') is not False or run.get('run_state') != 'SUCCEEDED'):
        raise ValueError('source run must have succeeded')
    if artifact.get('quarantine_state') != 'ADMITTED' or run.get('trust_zone') != 'CANONICAL':
        raise ValueError('source artifact must be admitted canonical evidence')
    if run['artifact_id'] != artifact['artifact_id'] or run['source_id'] != artifact['source_id']:
        raise ValueError('source artifact/run identity mismatch')
    if metadata['source']['source_id'] != run['source_id']:
        raise ValueError('source identity mismatch')
    content_hash = artifact['content_sha256'].removeprefix('\\x')
    if not _SHA256.fullmatch(content_hash):
        raise ValueError('invalid canonical source content hash')
    generation_key = run.get('scope_contract', {}).get('generation_key')
    if generation_key is not None and schema != f'v3_gen_{generation_key}':
        raise ValueError('source run/generation mismatch')
    _boolean(run.get('is_complete_snapshot'), 'is_complete_snapshot')
    # Historical exports contain one source. Production snapshots carry the
    # complete admitted input manifest, including runs with no rows in a chunk.
    admitted = {run['source_run_id']: (run, artifact)}
    if 'generation_inputs' in metadata:
        inputs = metadata['generation_inputs']
        if not isinstance(inputs, list) or not inputs:
            raise ValueError('missing admitted source-run manifest')
        admitted = {}
        ordinals = set()
        for entry in inputs:
            admission, input_run = entry['input'], entry['run']
            input_artifact = entry['artifact']
            if (admission['generation_id'] != metadata['generation_id'] or
                    admission['source_run_id'] != input_run['source_run_id'] or
                    admission['source_id'] != input_run['source_id'] or
                    admission['artifact_id'] != input_run['artifact_id'] or
                    entry['source']['source_id'] != input_run['source_id']):
                raise ValueError('admitted source manifest identity mismatch')
            ordinal = _id(admission['input_ordinal'], 'input ordinal')
            if input_run['source_run_id'] in admitted or ordinal in ordinals:
                raise ValueError('duplicate admitted source manifest identity')
            ordinals.add(ordinal)
            if input_run.get('run_state') != 'SUCCEEDED' or input_run.get('trust_zone') != 'CANONICAL':
                raise ValueError('admitted source run must have succeeded in canonical trust zone')
            _boolean(input_run.get('is_complete_snapshot'), 'is_complete_snapshot')
            if input_artifact is None:
                if input_run['artifact_id'] is not None:
                    raise ValueError('missing admitted source artifact')
            elif (input_artifact['artifact_id'] != input_run['artifact_id'] or
                  input_artifact['source_id'] != input_run['source_id'] or
                  input_artifact.get('quarantine_state') != 'ADMITTED' or
                  not _SHA256.fullmatch(input_artifact['content_sha256'].removeprefix('\\x'))):
                raise ValueError('invalid admitted source artifact')
            admitted[input_run['source_run_id']] = (input_run, input_artifact)
        if admitted.get(run['source_run_id']) != (run, artifact):
            raise ValueError('build source run missing or inconsistent in admitted manifest')
    return run, artifact, admitted


def canonical_lineage(canonical_export: Mapping, source_metadata: Mapping,
                      subtype_sources: Iterable[Mapping]) -> dict[str, Any]:
    """Expose distinct canonical and subtype identities for derived manifests."""
    run, artifact, _ = _source_contract(canonical_export, source_metadata)
    source_hashes = {}
    for payload in subtype_sources:
        system_id = _id(payload['system']['id64'], 'subtype system id64')
        if str(system_id) in source_hashes:
            raise ValueError(f'duplicate subtype system: {system_id}')
        source_hashes[str(system_id)] = normalized_sha256(payload)
    lineage = {
        'adapter_version': ADAPTER_VERSION,
        'canonical_schema': canonical_export['canonical_schema'],
        'canonical_source_run_id': run['source_run_id'],
        'canonical_artifact_sha256': artifact['content_sha256'].removeprefix('\\x'),
        'canonical_effective_at': artifact.get('effective_at'),
        'canonical_export_normalized_sha256': normalized_sha256(canonical_export),
        'source_metadata_normalized_sha256': normalized_sha256(source_metadata),
        'subtype_sources_normalized_sha256': dict(sorted(source_hashes.items())),
        'subtype_fields_used': ['subType'],
        'mixed_snapshot_policy': 'Only subType is enriched; canonical feature facts retain canonical provenance.',
        'reserve_policy': 'Attached canonical observations only; missing or conflicting values are never broadcast.',
        'ring_absence_policy': 'No per-body ring completeness fact is retained; missing rings are unknown.',
        'ground_policy': 'Usable ground is unknown; landability does not establish an opportunity.',
    }
    if 'generation_inputs' in source_metadata:
        lineage['canonical_generation_inputs'] = source_metadata['generation_inputs']
    return lineage


def adapt_canonical_export(canonical_export: Mapping, source_metadata: Mapping,
                           subtype_sources: Iterable[Mapping]) -> dict[int, SystemFacts]:
    """Adapt explicit snapshot facts, retaining unknowns and rejecting ambiguity.

    Complete source acquisition proves catalogue inventory, not complete scan
    signals. Missing signals become false only under the body's signals_complete
    flag. Published snapshot rows and all supplied subtype identities must join.
    """
    subtype_sources = tuple(subtype_sources)
    run, _, admitted = _source_contract(canonical_export, source_metadata)
    lineage = canonical_lineage(canonical_export, source_metadata, subtype_sources)
    schema = canonical_export['canonical_schema']
    systems = {key[0]: row for key, row in _index(
        canonical_export['systems'], ('id64',), 'system').items()}
    bodies = {key[0]: row for key, row in _index(
        canonical_export['bodies'], ('body_pk',), 'body').items()}
    extras = _index(canonical_export['extras'], ('schema', 'relation'), 'relation')

    def relation(name: str, vocabulary: bool = False) -> list:
        key = ('v3_vocab' if vocabulary else schema, name)
        if key not in extras or 'export_error' in extras[key]:
            raise ValueError(f'missing canonical relation: {key}')
        return extras[key]['rows']

    def vocabulary(name: str) -> dict:
        return {key[0]: row['public_code'] for key, row in _index(
            relation(name, True), (f'{name}_id',), name).items()}

    body_types = vocabulary('body_type')
    reserves = vocabulary('reserve_type')
    signals = vocabulary('signal_type')
    volcanism = vocabulary('volcanism_type')
    terraforming = vocabulary('terraforming_state')

    def vocab_value(row: Mapping, field: str, vocab: Mapping) -> str | None:
        value = row.get(field)
        if value is not None and value not in vocab:
            raise ValueError(f'unknown vocabulary reference: {field}={value}')
        return vocab.get(value)

    def validate_source(row: Mapping) -> None:
        if row.get('source_run_id') not in admitted:
            raise ValueError('unregistered source run in canonical row')
        run, _ = admitted[row['source_run_id']]
        if 'source_id' in row and row['source_id'] != run['source_id']:
            raise ValueError('canonical row source identity mismatch')

    def provenance(row: Mapping, relation_name: str, **details: Any) -> str:
        run, artifact = admitted[row['source_run_id']]
        return json.dumps({
            'adapter': ADAPTER_VERSION, 'canonical_schema': schema,
            'source_run_id': run['source_run_id'],
            'artifact_sha256': artifact['content_sha256'].removeprefix('\\x') if artifact else None,
            'relation': relation_name, 'body_pk': row.get('body_pk'),
            'source_updated_at': row.get('source_updated_at', row.get('observed_at')),
            **details,
        }, sort_keys=True, separators=(',', ':'))

    grouped = defaultdict(list)
    external_bodies = {}
    frontier_bodies = set()
    for system_id, row in systems.items():
        _id(system_id, 'system id64')
        validate_source(row)
    for body_pk, row in bodies.items():
        _id(body_pk, 'body_pk')
        system_id = _id(row['system_id64'], 'body system id64')
        if system_id not in systems:
            raise ValueError('orphan canonical body')
        validate_source(row)
        source_body_id = row.get('source_body_id64')
        if source_body_id is not None:
            identity = (system_id, _id(source_body_id, 'source body id64'))
            if identity in external_bodies:
                raise ValueError('duplicate canonical source body identity')
            external_bodies[identity] = row
        if row.get('frontier_body_id') is not None:
            frontier = (system_id, _id(row['frontier_body_id'], 'frontier body id'))
            if frontier in frontier_bodies:
                raise ValueError('duplicate canonical Frontier body identity')
            frontier_bodies.add(frontier)
        grouped[system_id].append(row)

    enriched = {}
    for payload in subtype_sources:
        source_system = payload['system']
        system_id = _id(source_system['id64'], 'subtype system id64')
        if system_id not in systems:
            raise ValueError('unmatched subtype system')
        for source_body in source_system['bodies']:
            identity = (system_id, _id(source_body['id64'], 'subtype body id64'))
            if identity in enriched:
                raise ValueError('duplicate subtype body identity')
            if identity not in external_bodies:
                raise ValueError('unmatched subtype body identity')
            canonical_body = external_bodies[identity]
            if source_body.get('bodyId') is not None and canonical_body.get('frontier_body_id') is not None:
                if _id(source_body['bodyId'], 'subtype Frontier id') != canonical_body['frontier_body_id']:
                    raise ValueError('conflicting subtype Frontier body identity')
            broad = vocab_value(canonical_body, 'body_type_id', body_types)
            if source_body.get('type', '').lower().replace(' ', '_') != broad:
                raise ValueError('conflicting subtype body type')
            subtype = source_body.get('subType')
            if subtype is not None and (not isinstance(subtype, str) or not subtype.strip()):
                raise ValueError('invalid subtype classification')
            enriched[identity] = source_body

    ring_rows = relation('rings')
    _index(ring_rows, ('ring_pk',), 'ring')
    attached_rings = defaultdict(list)
    for ring in ring_rows:
        _id(ring['ring_pk'], 'ring_pk')
        validate_source(ring)
        body = bodies.get(ring['body_pk'])
        if body is None or body['system_id64'] != ring['system_id64']:
            raise ValueError('orphan or conflicting ring body identity')
        if ring['kind'] not in {'RING', 'BELT'}:
            raise ValueError('unknown ring kind')
        vocab_value(ring, 'reserve_type_id', reserves)
        if ring.get('lifecycle_state') == 'ACTIVE':
            attached_rings[ring['body_pk']].append(ring)

    signal_rows = relation('body_signal_current')
    _index(signal_rows, ('body_pk', 'signal_type_id'), 'body signal')
    attached_signals = defaultdict(dict)
    for signal in signal_rows:
        validate_source(signal)
        if signal['body_pk'] not in bodies:
            raise ValueError('orphan body signal')
        kind = vocab_value(signal, 'signal_type_id', signals)
        count = _id(signal['signal_count'], 'signal_count')
        attached_signals[signal['body_pk']][kind] = (count, signal)

    result = {}
    source_complete = run.get('is_complete_snapshot') is True
    for system_id, system in sorted(systems.items()):
        inventory = grouped[system_id]
        loaded_count = _id(system['loaded_body_count'], 'loaded_body_count')
        if loaded_count != len(inventory):
            raise ValueError('canonical body inventory/export count mismatch')
        facts = []
        for row in sorted(inventory, key=lambda body: body['body_pk']):
            broad = vocab_value(row, 'body_type_id', body_types)
            if row.get('lifecycle_state') != 'ACTIVE' or broad in {'barycentre', 'belt_cluster'}:
                continue
            source_body = enriched.get((system_id, row.get('source_body_id64')), {})
            body_class = source_body.get('subType') or broad or 'Unknown'
            base_provenance = provenance(row, 'bodies')
            fields = {name: base_provenance for name in (
                'provenance', 'body_class', 'spectral_class', 'luminosity_class',
                'terraformable', 'tidally_locked', 'volcanism',
            )}
            if source_body.get('subType'):
                fields['body_class'] = json.dumps({
                    'source': 'Spansh system dump API', 'field': 'subType',
                    'system_id64': system_id, 'source_body_id64': row['source_body_id64'],
                    'source_updated_at': source_body.get('updateTime'),
                    'source_normalized_sha256': lineage['subtype_sources_normalized_sha256'][str(system_id)],
                    'canonical_join_provenance': base_provenance,
                }, sort_keys=True, separators=(',', ':'))
            known_signals = _boolean(row.get('signals_complete'), 'signals_complete') is True

            def signal_presence(name: str) -> bool | None:
                observed = attached_signals[row['body_pk']].get(f'saa_signaltype_{name}')
                feature = {'biological': 'biologicals', 'geological': 'geologicals'}[name]
                fields[feature] = provenance(observed[1] if observed else row,
                    'body_signal_current' if observed else 'bodies',
                    signals_complete=known_signals, signal_type=name)
                return observed[0] > 0 if observed else (False if known_signals else None)

            rings = attached_rings[row['body_pk']]
            def ring_lineage(ring: Mapping) -> dict:
                # Preserve the frozen single-run payload while identifying
                # observations supplied by a different admitted run.
                return ({'provenance': provenance(ring, 'rings')}
                        if ring['source_run_id'] != row['source_run_id'] else {})

            fields['rings'] = provenance(row, 'rings',
                observations=[{'ring_pk': ring['ring_pk'], 'kind': ring['kind'], **ring_lineage(ring)} for ring in rings],
                source_inventory_complete=source_complete,
                absent_observations_state='UNKNOWN',
                mechanics_rule='Rings including stellar asteroid belts supply Extraction inheritance.')
            observed_reserves = {reserves[ring['reserve_type_id']] for ring in rings
                                 if ring.get('reserve_type_id') is not None}
            if len(observed_reserves) > 1:
                raise ValueError('conflicting attached reserve observations')
            fields['reserve_level'] = provenance(row, 'rings', observations=[
                {**ring, **ring_lineage(ring)} for ring in rings if ring.get('reserve_type_id') is not None])
            fields['usable_ground_opportunity'] = provenance(row, 'bodies',
                known_state='UNKNOWN', reason='No usable-ground opportunity source fact; landability is insufficient.')
            volcano = vocab_value(row, 'volcanism_type_id', volcanism)
            terra = vocab_value(row, 'terraforming_state_id', terraforming)
            if terra not in {None, 'not_terraformable', 'terraformable', 'terraformed', 'terraforming'}:
                raise ValueError('unrecognized terraforming state')
            _boolean(row.get('is_landable'), 'is_landable')
            facts.append(BodyFact(
                candidate_id=str(row['body_pk']), body_class=body_class,
                rings=True if rings else None,
                biologicals=signal_presence('biological'), geologicals=signal_presence('geological'),
                volcanism=volcano != 'no_volcanism' if volcano is not None else None,
                terraformable=terra != 'not_terraformable' if terra is not None else None,
                tidally_locked=_boolean(row.get('is_tidally_locked'), 'is_tidally_locked'),
                spectral_class=row.get('spectral_class'), luminosity_class=row.get('luminosity_class'),
                is_main_star=_boolean(row.get('is_main_star'), 'is_main_star'),
                reserve_level=next(iter(observed_reserves), None), reserve_scope='body',
                usable_ground_opportunity=None, feature_provenance=fields,
            ))
        result[system_id] = SystemFacts(
            bodies=tuple(facts), body_inventory_complete=source_complete,
            feature_provenance={'body_inventory': provenance(system, 'systems',
                loaded_body_count=loaded_count, source_inventory_complete=source_complete),
                'provenance': provenance(system, 'systems')},
        )
    return result


def load_source_fixture(directory: Path) -> tuple[dict, dict, tuple[dict, ...]]:
    """Read the retained originals only after validating their byte checksums."""
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    if set(manifest['files_sha256']) != {'canonical.json', 'source-metadata.json', 'spansh-system-dumps.zip'}:
        raise ValueError('incomplete fixture checksum manifest')
    for name, digest in manifest['files_sha256'].items():
        if Path(name).name != name or not _SHA256.fullmatch(digest):
            raise ValueError('invalid fixture manifest entry')
        if sha256((directory / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f'fixture checksum mismatch: {name}')
    canonical = json.loads((directory / 'canonical.json').read_bytes())
    metadata = json.loads((directory / 'source-metadata.json').read_bytes())
    with ZipFile(directory / 'spansh-system-dumps.zip') as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(not re.fullmatch(r'[0-9]+\.json', name) for name in names):
            raise ValueError('invalid or duplicate subtype artifact member')
        subtypes = tuple(json.loads(archive.read(name)) for name in sorted(names))
    return canonical, metadata, subtypes
