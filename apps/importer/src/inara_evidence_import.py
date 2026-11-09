from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from typing import TYPE_CHECKING

import source_run_artifacts
import source_run_ledger

if TYPE_CHECKING:
    from inara_api import InaraClient


IMPORTER_NAME = 'inara_evidence_import'
IMPORTER_VERSION = '2026-07-06'
ARTIFACT_SCHEMA_VERSION = 'inara_station_services_import_v1'
DEFAULT_ARTIFACT_DIR = Path('artifacts') / 'inara'
EVIDENCE_RECORDS_TABLE = 'evidence_records'


@dataclass(frozen=True)
class InaraFetchResult:
    requested_name: str
    fetched_at: datetime
    payload: Mapping[str, Any] | None


@dataclass(frozen=True)
class InaraImportSummary:
    rows_read: int
    rows_staged: int
    rows_rejected: int
    rows_skipped: int
    artifact_payload: Mapping[str, Any]


def normalise_requested_system_names(names: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_name in names:
        name = str(raw_name or '').strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(name)
    return deduped


async def fetch_inara_systems(
    system_names: Sequence[str],
    *,
    client: InaraClient | None = None,
) -> list[InaraFetchResult]:
    if client is None:
        from inara_api import InaraClient as RuntimeInaraClient  # noqa: PLC0415

    owned_client = client is None
    active_client = client or RuntimeInaraClient()
    try:
        results: list[InaraFetchResult] = []
        for system_name in normalise_requested_system_names(system_names):
            fetched_at = utc_now()
            payload = await active_client.get_system(system_name)
            results.append(
                InaraFetchResult(
                    requested_name=system_name,
                    fetched_at=fetched_at,
                    payload=payload,
                ),
            )
        return results
    finally:
        if owned_client:
            await active_client.close()


def resolve_system_id64(conn: Any, system_name: str) -> int | None:
    sql = """
        SELECT id64
        FROM systems
        WHERE lower(name) = lower(%s)
        ORDER BY id64
        LIMIT 1
    """
    cur = conn.cursor()
    try:
        cur.execute(sql, (system_name,))
        row = cur.fetchone()
    finally:
        close_cursor(cur)
    if row is None:
        return None
    if isinstance(row, Mapping):
        value = row.get('id64')
    elif hasattr(row, 'keys'):
        value = row['id64']
    else:
        value = row[0]
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def build_station_service_evidence_records(
    *,
    system_id64: int,
    requested_name: str,
    fetched_at: datetime,
    payload: Mapping[str, Any],
    source_run_key: str | None,
    trigger_context: str,
) -> list[dict[str, Any]]:
    system_name = str(payload.get('name') or requested_name).strip() or requested_name
    system_url = payload.get('inara_url')
    observed_at = to_utc_iso(fetched_at)
    records: list[dict[str, Any]] = []
    for station in payload.get('stations') or []:
        if not isinstance(station, Mapping):
            continue
        station_name = str(station.get('name') or '').strip()
        if not station_name:
            continue
        subject_id = canonical_station_subject_id(system_id64, station_name)
        services = normalise_services(station.get('services'))
        station_key = stable_hash({
            'system_id64': system_id64,
            'station_name': station_name.casefold(),
        })[:16]
        evidence_key = f'inara-station-services:{station_key}'
        summary = f'Inara station services snapshot for {station_name} in {system_name}'
        value = {
            'system_name': system_name,
            'system_id64': system_id64,
            'station_name': station_name,
            'station_type': station.get('type'),
            'distance_ls': station.get('distance_ls'),
            'economy': station.get('economy'),
            'services': services,
            'inara_url': station.get('inara_url'),
            'system_inara_url': system_url,
            'system_context': {
                'allegiance': payload.get('allegiance'),
                'government': payload.get('government'),
                'economy': payload.get('economy'),
                'second_economy': payload.get('second_economy'),
                'security': payload.get('security'),
                'population': payload.get('population'),
                'controlling_faction': payload.get('controlling_faction'),
            },
        }
        provenance = {
            'source': 'inara',
            'api_event': 'getSystem',
            'query_system_name': requested_name,
            'resolved_system_name': system_name,
            'fetched_at': observed_at,
            'station_inara_url': station.get('inara_url'),
            'system_inara_url': system_url,
        }
        metadata = {
            'writer': IMPORTER_NAME,
            'importer_version': IMPORTER_VERSION,
            'trigger_context': trigger_context,
            'schema_version': ARTIFACT_SCHEMA_VERSION,
            'service_count': len(services),
        }
        tags = ['inara', 'station_services', 'bounded_import']
        if station.get('type'):
            tags.append(f"station_type:{slug_text(station.get('type'))}")
        records.append({
            'evidence_key': evidence_key,
            'system_id64': system_id64,
            'source_name': 'inara',
            'origin': 'imported',
            'subject_type': 'station',
            'subject_id': subject_id,
            'evidence_type': 'station_services_snapshot',
            'record_status': 'active',
            'freshness_status': 'current',
            'confidence': 'medium',
            'summary': summary,
            'source_record_id': station.get('inara_url') or subject_id,
            'source_run_key': source_run_key,
            'observed_at': observed_at,
            'value_json': value,
            'provenance_json': provenance,
            'tags_json': tags,
            'metadata_json': metadata,
        })
    return records


def import_station_service_snapshots(
    conn: Any,
    fetch_results: Sequence[InaraFetchResult],
    *,
    source_run_key: str | None,
    trigger_context: str,
    dry_run: bool,
) -> InaraImportSummary:
    rows_read = len(fetch_results)
    rows_staged = 0
    rows_rejected = 0
    rows_skipped = 0
    artifact_items: list[dict[str, Any]] = []

    cur = conn.cursor()
    try:
        for fetch_result in fetch_results:
            payload = fetch_result.payload
            if not payload:
                rows_skipped += 1
                artifact_items.append({
                    'requested_name': fetch_result.requested_name,
                    'status': 'not_found',
                    'station_count': 0,
                })
                continue

            resolved_name = str(payload.get('name') or fetch_result.requested_name).strip() or fetch_result.requested_name
            system_id64 = resolve_system_id64(conn, resolved_name)
            if system_id64 is None:
                rows_rejected += 1
                artifact_items.append({
                    'requested_name': fetch_result.requested_name,
                    'resolved_name': resolved_name,
                    'status': 'missing_local_system',
                    'station_count': len(payload.get('stations') or []),
                })
                continue

            records = build_station_service_evidence_records(
                system_id64=system_id64,
                requested_name=fetch_result.requested_name,
                fetched_at=fetch_result.fetched_at,
                payload=payload,
                source_run_key=source_run_key,
                trigger_context=trigger_context,
            )
            if not records:
                rows_skipped += 1
                artifact_items.append({
                    'requested_name': fetch_result.requested_name,
                    'resolved_name': resolved_name,
                    'system_id64': system_id64,
                    'status': 'no_stations',
                    'station_count': 0,
                })
                continue

            if not dry_run:
                for record in records:
                    upsert_evidence_record(cur, record)
            rows_staged += len(records)
            artifact_items.append({
                'requested_name': fetch_result.requested_name,
                'resolved_name': resolved_name,
                'system_id64': system_id64,
                'status': 'staged' if not dry_run else 'dry_run_preview',
                'station_count': len(records),
                'station_names': [record['value_json']['station_name'] for record in records],
                'evidence_keys': [record['evidence_key'] for record in records],
            })
    finally:
        close_cursor(cur)

    artifact_payload = {
        'systems': artifact_items,
        'summary': {
            'requested_systems': rows_read,
            'records_prepared': rows_staged,
            'systems_rejected': rows_rejected,
            'systems_skipped': rows_skipped,
            'dry_run': dry_run,
        },
    }
    return InaraImportSummary(
        rows_read=rows_read,
        rows_staged=rows_staged,
        rows_rejected=rows_rejected,
        rows_skipped=rows_skipped,
        artifact_payload=artifact_payload,
    )


def upsert_evidence_record(cur: Any, record: Mapping[str, Any]) -> None:
    sql = f"""
        INSERT INTO {EVIDENCE_RECORDS_TABLE} (
            evidence_key,
            system_id64,
            source_name,
            origin,
            subject_type,
            subject_id,
            evidence_type,
            record_status,
            freshness_status,
            confidence,
            summary,
            source_record_id,
            source_run_key,
            observed_at,
            value_json,
            provenance_json,
            tags_json,
            metadata_json
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb
        )
        ON CONFLICT (evidence_key) DO UPDATE SET
            system_id64 = EXCLUDED.system_id64,
            subject_id = EXCLUDED.subject_id,
            freshness_status = EXCLUDED.freshness_status,
            confidence = EXCLUDED.confidence,
            summary = EXCLUDED.summary,
            source_record_id = EXCLUDED.source_record_id,
            source_run_key = COALESCE(EXCLUDED.source_run_key, {EVIDENCE_RECORDS_TABLE}.source_run_key),
            observed_at = EXCLUDED.observed_at,
            value_json = EXCLUDED.value_json,
            provenance_json = EXCLUDED.provenance_json,
            tags_json = EXCLUDED.tags_json,
            metadata_json = EXCLUDED.metadata_json,
            updated_at = NOW()
    """
    cur.execute(
        sql,
        (
            record['evidence_key'],
            record['system_id64'],
            record['source_name'],
            record['origin'],
            record['subject_type'],
            record['subject_id'],
            record['evidence_type'],
            record['record_status'],
            record['freshness_status'],
            record['confidence'],
            record['summary'],
            record['source_record_id'],
            record['source_run_key'],
            record['observed_at'],
            jsonb(record['value_json']),
            jsonb(record['provenance_json']),
            jsonb(record['tags_json']),
            jsonb(record['metadata_json']),
        ),
    )


def run_inara_station_services_import(
    *,
    conn: Any,
    system_names: Sequence[str],
    artifact_dir: str | Path,
    git_commit_sha: str,
    trigger_context: str,
    source_run_key: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    requested_names = normalise_requested_system_names(system_names)
    if not requested_names:
        raise ValueError('at least one system name is required')

    run_key = source_run_key or default_source_run_key()
    import_scope = 'review_packet' if dry_run else 'bounded_write_reviewed'
    artifact_path = Path(artifact_dir) / f'{run_key}.json'
    source_run_kwargs = {
        'source_run_key': run_key,
        'source_name': 'inara',
        'source_category': 'source_of_evidence',
        'domain': 'station_services',
        'import_scope': import_scope,
        'git_commit_sha': git_commit_sha,
        'importer_name': IMPORTER_NAME,
        'importer_version': IMPORTER_VERSION,
        'trigger_context': trigger_context,
        'status': 'running',
        'source_uri': 'https://inara.cz/inara-api-devguide/',
        'safety_boundary': {
            'requested_system_count': len(requested_names),
            'requested_system_names': requested_names,
            'dry_run': dry_run,
        },
        'metadata': {
            'artifact_schema_version': ARTIFACT_SCHEMA_VERSION,
        },
    }

    source_run = source_run_ledger.create_source_run(conn, **source_run_kwargs)
    conn.commit()
    started_at = utc_now()

    try:
        fetch_results = asyncio.run(fetch_inara_systems(requested_names))
        summary = import_station_service_snapshots(
            conn,
            fetch_results,
            source_run_key=run_key,
            trigger_context=trigger_context,
            dry_run=dry_run,
        )
        artifact_payload = source_run_artifacts.build_artifact_payload_shell(
            schema_version=ARTIFACT_SCHEMA_VERSION,
            source_run_key=run_key,
            source_name='inara',
            source_category='source_of_evidence',
            domain='station_services',
            import_scope=import_scope,
            git_commit_sha=git_commit_sha,
            importer_name=IMPORTER_NAME,
            importer_version=IMPORTER_VERSION,
            trigger_context=trigger_context,
            source_uri='https://inara.cz/inara-api-devguide/',
            safety_boundary=source_run_kwargs['safety_boundary'],
            summary=summary.artifact_payload['summary'],
            payload=summary.artifact_payload,
        )
        artifact_record = source_run_artifacts.write_source_run_artifact(artifact_path, artifact_payload)
        duration_ms = max(0, int((utc_now() - started_at).total_seconds() * 1000))
        completion = source_run_artifacts.complete_source_run_with_artifact(
            conn,
            run_key,
            status='succeeded',
            artifact_record=artifact_record,
            rows_read=summary.rows_read,
            rows_staged=summary.rows_staged,
            rows_rejected=summary.rows_rejected,
            rows_skipped=summary.rows_skipped,
            duration_ms=duration_ms,
            metadata={'dry_run': dry_run},
        )
        conn.commit()
        return {
            'source_run': source_run,
            'completion': completion,
            'artifact_record': artifact_record,
            'summary': summary.artifact_payload['summary'],
        }
    except Exception as exc:
        conn.rollback()
        duration_ms = max(0, int((utc_now() - started_at).total_seconds() * 1000))
        failure_payload = source_run_artifacts.build_artifact_payload_shell(
            schema_version=ARTIFACT_SCHEMA_VERSION,
            source_run_key=run_key,
            source_name='inara',
            source_category='source_of_evidence',
            domain='station_services',
            import_scope=import_scope,
            git_commit_sha=git_commit_sha,
            importer_name=IMPORTER_NAME,
            importer_version=IMPORTER_VERSION,
            trigger_context=trigger_context,
            source_uri='https://inara.cz/inara-api-devguide/',
            safety_boundary=source_run_kwargs['safety_boundary'],
            summary={'status': 'failed'},
            payload={'error': str(exc)},
        )
        artifact_record = source_run_artifacts.write_source_run_artifact(artifact_path, failure_payload)
        completion = source_run_artifacts.complete_source_run_with_artifact(
            conn,
            run_key,
            status='failed',
            artifact_record=artifact_record,
            error_code='inara_import_failed',
            error_summary=str(exc),
            duration_ms=duration_ms,
        )
        conn.commit()
        raise RuntimeError(
            f'Inara station-services import failed for source run {run_key}: {exc}'
        ) from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Fetch bounded Inara station-services evidence and write it to evidence_records.',
    )
    parser.add_argument('--dsn', default=os.getenv('DATABASE_URL'), help='Postgres DSN')
    parser.add_argument(
        '--system',
        action='append',
        dest='systems',
        default=[],
        help='System name to fetch from Inara. Repeat to import multiple systems.',
    )
    parser.add_argument(
        '--systems-file',
        help='Optional text file with one system name per line.',
    )
    parser.add_argument(
        '--artifact-dir',
        default=str(DEFAULT_ARTIFACT_DIR),
        help='Directory for the source-run JSON artifact.',
    )
    parser.add_argument(
        '--git-commit-sha',
        default=os.getenv('GIT_COMMIT_SHA', 'unknown'),
        help='Git SHA recorded in source_runs.',
    )
    parser.add_argument(
        '--trigger-context',
        default='manual_operator_source',
        help='Human-readable trigger context recorded in source_runs.',
    )
    parser.add_argument(
        '--source-run-key',
        help='Optional explicit source_run_key. Defaults to a generated Inara key.',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Fetch and build the artifact without writing evidence_records.',
    )
    args = parser.parse_args(argv)
    args.systems = normalise_requested_system_names(load_requested_systems(args.systems, args.systems_file))
    if not args.dsn:
        parser.error('a Postgres DSN is required via --dsn or DATABASE_URL')
    if not args.systems:
        parser.error('at least one --system or a non-empty --systems-file is required')
    return args


def load_requested_systems(
    systems: Sequence[str],
    systems_file: str | None,
) -> list[str]:
    loaded = list(systems)
    if systems_file:
        loaded.extend(
            line.strip()
            for line in Path(systems_file).read_text(encoding='utf-8').splitlines()
            if line.strip()
        )
    return loaded


def default_source_run_key(now: datetime | None = None) -> str:
    stamp = (now or utc_now()).strftime('%Y%m%dT%H%M%SZ')
    return f'inara-station-services-{stamp}'


def canonical_station_subject_id(system_id64: int, station_name: str) -> str:
    return f'{system_id64}:{slug_text(station_name)}'


def normalise_services(services: Any) -> list[str]:
    if not isinstance(services, list):
        return []
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_value in services:
        value = str(raw_value or '').strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def slug_text(value: Any) -> str:
    collapsed = re.sub(r'[^a-z0-9]+', '-', str(value or '').strip().lower())
    return collapsed.strip('-') or 'unknown'


def stable_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def jsonb(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def close_cursor(cur: Any) -> None:
    close = getattr(cur, 'close', None)
    if close is not None:
        close()


def connect_db(dsn: str) -> Any:
    import psycopg  # noqa: PLC0415

    return psycopg.connect(dsn)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    with connect_db(args.dsn) as conn:
        result = run_inara_station_services_import(
            conn=conn,
            system_names=args.systems,
            artifact_dir=args.artifact_dir,
            git_commit_sha=args.git_commit_sha,
            trigger_context=args.trigger_context,
            source_run_key=args.source_run_key,
            dry_run=bool(args.dry_run),
        )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
