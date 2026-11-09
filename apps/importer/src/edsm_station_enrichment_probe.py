#!/usr/bin/env python3
"""Targeted EDSM station/body enrichment dry-run and metadata-only apply.

This command compares one ED-Finder system against EDSM per-system station and
body payloads. It reports possible station-data and station/body-link
enrichment. By default it performs no writes; --apply-metadata may update only
safe local station metadata fields.
"""

from __future__ import annotations

import argparse
import email.utils
import http.client
import json
import os
import socket
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping, Sequence
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg
from psycopg.rows import dict_row

from api_source_resolver import add_api_source_to_path


API_SRC = add_api_source_to_path(__file__, required_paths=('station_body_resolver.py',))

from station_body_resolver import (  # noqa: E402
    DISTANCE_MATCH_TOLERANCE_LS,
    classify_station_lane,
    is_permanent_colony_slot_station_type,
    is_transient_non_slot_station_type,
    normalise_station_type_label,
)


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


EDSM_SYSTEM_API_BASE = 'https://www.edsm.net/api-system-v1'
DEFAULT_TIMEOUT_SECONDS = _env_float('EDSM_HTTP_TIMEOUT_SECONDS', 60.0)
DEFAULT_HTTP_RETRIES = _env_int('EDSM_HTTP_RETRIES', 3)
DEFAULT_HTTP_RETRY_BACKOFF_SECONDS = _env_float('EDSM_HTTP_RETRY_BACKOFF_SECONDS', 3.0)
DEFAULT_HTTP_REQUEST_DELAY_SECONDS = _env_float('EDSM_HTTP_REQUEST_DELAY_SECONDS', 0.5)
DEFAULT_HTTP_429_BACKOFF_SECONDS = _env_float('EDSM_HTTP_429_BACKOFF_SECONDS', 60.0)
DEFAULT_HTTP_429_BACKOFF_MULTIPLIER = _env_float('EDSM_HTTP_429_BACKOFF_MULTIPLIER', 2.0)
# EDSM occasionally answers 429 with a tiny Retry-After (e.g. 1 or 2 seconds);
# obeying that literally still trips the next request and pushes us into a
# tighter rate-limit. Treat any Retry-After below this floor as the floor.
MIN_RATE_LIMIT_RETRY_AFTER_SECONDS = _env_float('EDSM_MIN_RETRY_AFTER_SECONDS', 5.0)
# Number of consecutive 429s after which we log a TODO/recommendation that the
# operator should raise the global backoff or pause and resume later.
REPEATED_RATE_LIMIT_WARNING_THRESHOLD = _env_int('EDSM_REPEATED_429_WARNING_THRESHOLD', 3)
STATION_DISTANCE_CONFLICT_TOLERANCE_LS = DISTANCE_MATCH_TOLERANCE_LS
TRUSTED_STATION_DISTANCE_PRECISION_TOLERANCE_LS = 0.05
BODY_DISTANCE_MATCH_TOLERANCE_LS = DISTANCE_MATCH_TOLERANCE_LS
TRUSTED_EDSM_SOURCE = 'edsm_system_api'
TRUSTED_STATION_IDENTITY_CONFIDENCE = 'exact_station_identity'
SUPPORTED_STATION_METADATA_FIELDS = {
    'station_type',
    'distance_from_star',
    'body_name',
}
NON_BENIGN_STATION_CONFLICT_TYPES = {
    'id_name_mismatch',
    'known_station_type_mismatch',
    'station_economy_mismatch',
}
IDENTITY_CONTEXT_UNSAFE_MARKERS = {
    'identity_unsafe',
    'context_unsafe',
    'identity_context_unsafe',
    'station_write_unsafe',
}
STATION_WRITE_SUPPRESSED_REASON = 'station_write_suppressed_non_benign_conflict'

VALID_ECONOMIES = {
    'HighTech', 'Agriculture', 'Refinery', 'Industrial', 'Military',
    'Tourism', 'Extraction', 'Colony', 'Terraforming', 'Prison',
    'Damaged', 'Rescue', 'Repair', 'Carrier', 'None', 'Unknown',
}


class EdsmFetchError(RuntimeError):
    """Raised when an EDSM endpoint cannot be fetched after configured retries."""

    def __init__(
        self,
        message: str,
        *,
        system_name: str,
        system_id64: int | None,
        endpoint: str,
        attempts: int,
        reason: str,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        retry_after_header: str | None = None,
    ) -> None:
        super().__init__(message)
        self.system_name = system_name
        self.system_id64 = system_id64
        self.endpoint = endpoint
        self.attempts = attempts
        self.reason = reason
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.retry_after_header = retry_after_header
        self.rate_limited = status_code == 429 or 'too many requests' in reason.lower()


class EdsmRateLimitError(EdsmFetchError):
    """Raised when EDSM keeps returning 429/Too Many Requests after retries."""


class _EdsmHttpStatusError(RuntimeError):
    def __init__(self, status_code: int, reason: str, headers: Any = None) -> None:
        super().__init__(f'HTTP {status_code} {reason}')
        self.status = status_code
        self.reason = reason
        self.headers = headers


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Dry-run targeted EDSM station/body enrichment for one system.',
    )
    parser.add_argument('--dsn', default=os.environ.get('DATABASE_URL'), help='Postgres DSN. Defaults to DATABASE_URL.')
    parser.add_argument('--system-name', default=None, help='System name to probe.')
    parser.add_argument('--system-id64', type=int, default=None, help='Local ED system address/id64, if known.')
    parser.add_argument('--dry-run', action='store_true', help='Force dry-run mode. This is the default.')
    parser.add_argument(
        '--apply-metadata',
        action='store_true',
        help='Apply trusted station metadata/provenance only for one --system-id64.',
    )
    parser.add_argument(
        '--apply-station-metadata',
        action='store_true',
        help='Alias for --apply-metadata.',
    )
    parser.add_argument(
        '--apply-confirmed-links',
        action='store_true',
        help='Apply confirmed station_body_links from exact EDSM bodyName matches for one --system-id64.',
    )
    parser.add_argument('--json', action='store_true', help='Emit machine-readable JSON report.')
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT_SECONDS, help='EDSM request timeout in seconds.')
    parser.add_argument('--edsm-retries', type=int, default=DEFAULT_HTTP_RETRIES, help='EDSM retry attempts after the initial request.')
    parser.add_argument(
        '--edsm-retry-backoff-seconds',
        type=float,
        default=DEFAULT_HTTP_RETRY_BACKOFF_SECONDS,
        help='Initial EDSM retry backoff in seconds; later retries use exponential backoff.',
    )
    parser.add_argument(
        '--edsm-request-delay-seconds',
        type=float,
        default=DEFAULT_HTTP_REQUEST_DELAY_SECONDS,
        help='Delay between EDSM HTTP requests. Defaults to EDSM_HTTP_REQUEST_DELAY_SECONDS.',
    )
    parser.add_argument(
        '--edsm-429-backoff-seconds',
        type=float,
        default=DEFAULT_HTTP_429_BACKOFF_SECONDS,
        help='Initial EDSM 429 retry backoff in seconds when Retry-After is absent.',
    )
    parser.add_argument(
        '--edsm-429-backoff-multiplier',
        type=float,
        default=DEFAULT_HTTP_429_BACKOFF_MULTIPLIER,
        help='Multiplier for repeated EDSM 429 retry backoff when Retry-After is absent.',
    )
    parser.add_argument('--no-network', action='store_true', help='Skip EDSM requests and report local-only unresolved matches.')
    parser.add_argument('--local-only', action='store_true', help='Alias for --no-network.')
    parser.add_argument('--apply', action='store_true', help='Not implemented. Use --apply-metadata for metadata-only writes.')
    return parser.parse_args(argv)


def fetch_local_payload(conn, *, system_name: str | None, system_id64: int | None) -> dict[str, Any]:
    """Load one local system and its station/body/link evidence."""
    if system_name is None and system_id64 is None:
        raise ValueError('--system-name or --system-id64 is required.')

    with conn.cursor(row_factory=dict_row) as cur:
        if system_id64 is not None:
            cur.execute("""
                SELECT id64, name, x, y, z
                FROM systems
                WHERE id64 = %s
            """, (system_id64,))
        else:
            cur.execute("""
                SELECT id64, name, x, y, z
                FROM systems
                WHERE lower(name) = lower(%s)
                ORDER BY name
                LIMIT 2
            """, (system_name,))
        system_rows = [dict(row) for row in cur.fetchall()]
        if not system_rows:
            raise LookupError('Local system not found.')
        if len(system_rows) > 1:
            raise LookupError(f'Local system name {system_name!r} matched multiple rows.')
        system = system_rows[0]

        if system_name is not None and _normalise_name(system['name']) != _normalise_name(system_name):
            raise LookupError(
                f'Local system id64 {system["id64"]} is {system["name"]!r}, not {system_name!r}.'
            )

        cur.execute("""
            SELECT id, system_id64, name, body_type::text AS body_type, subtype, distance_from_star
            FROM bodies
            WHERE system_id64 = %s
        """, (system['id64'],))
        bodies = [dict(row) for row in cur.fetchall()]

        cur.execute("""
            SELECT id, id AS market_id, system_id64, name, station_type::text AS station_type,
                   distance_from_star, body_name AS station_body_name, body_name,
                   distance_source, distance_confidence, distance_updated_at,
                   station_type_source, station_type_confidence, station_type_updated_at,
                   body_name_source, body_name_confidence, body_name_updated_at,
                   primary_economy::text AS primary_economy,
                   secondary_economy::text AS secondary_economy,
                   has_market, has_shipyard, has_outfitting,
                   has_refuel, has_repair, has_rearm
            FROM stations
            WHERE system_id64 = %s
        """, (system['id64'],))
        stations = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT to_regclass('public.station_body_links') IS NOT NULL AS exists")
        has_station_links = bool(cur.fetchone()['exists'])
        existing_links: dict[int, dict[str, Any]] = {}
        if has_station_links:
            cur.execute("""
                SELECT station_id, market_id, system_id64, body_id, body_name, lane,
                       association_status, association_confidence, association_source,
                       resolver_notes
                FROM station_body_links
                WHERE system_id64 = %s
            """, (system['id64'],))
            existing_links = {
                int(row['station_id']): dict(row)
                for row in cur.fetchall()
                if _read_int(row.get('station_id')) is not None
            }

    return {
        'system': system,
        'bodies': bodies,
        'stations': stations,
        'existing_links': existing_links,
    }


def fetch_edsm_system(
    system_name: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_HTTP_RETRIES,
    retry_backoff_seconds: float = DEFAULT_HTTP_RETRY_BACKOFF_SECONDS,
    request_delay_seconds: float = DEFAULT_HTTP_REQUEST_DELAY_SECONDS,
    rate_limit_backoff_seconds: float = DEFAULT_HTTP_429_BACKOFF_SECONDS,
    rate_limit_backoff_multiplier: float = DEFAULT_HTTP_429_BACKOFF_MULTIPLIER,
    sleep: Any = time.sleep,
    logger: Any = None,
    system_id64: int | None = None,
) -> dict[str, Any]:
    """Fetch EDSM station and body payloads for one named system."""
    stations = _fetch_edsm_endpoint(
        'stations',
        system_name,
        timeout=timeout,
        retries=retries,
        retry_backoff_seconds=retry_backoff_seconds,
        rate_limit_backoff_seconds=rate_limit_backoff_seconds,
        rate_limit_backoff_multiplier=rate_limit_backoff_multiplier,
        sleep=sleep,
        logger=logger,
        system_id64=system_id64,
    )
    delay = max(0.0, float(request_delay_seconds or 0.0))
    if delay > 0:
        sleep(delay)
    bodies = _fetch_edsm_endpoint(
        'bodies',
        system_name,
        timeout=timeout,
        retries=retries,
        retry_backoff_seconds=retry_backoff_seconds,
        rate_limit_backoff_seconds=rate_limit_backoff_seconds,
        rate_limit_backoff_multiplier=rate_limit_backoff_multiplier,
        sleep=sleep,
        logger=logger,
        system_id64=system_id64,
    )
    return {'stations': stations, 'bodies': bodies}


def _fetch_edsm_endpoint(
    endpoint: str,
    system_name: str,
    *,
    timeout: float,
    retries: int = DEFAULT_HTTP_RETRIES,
    retry_backoff_seconds: float = DEFAULT_HTTP_RETRY_BACKOFF_SECONDS,
    rate_limit_backoff_seconds: float = DEFAULT_HTTP_429_BACKOFF_SECONDS,
    rate_limit_backoff_multiplier: float = DEFAULT_HTTP_429_BACKOFF_MULTIPLIER,
    sleep: Any = time.sleep,
    logger: Any = None,
    system_id64: int | None = None,
) -> Any:
    query = urlencode({'systemName': system_name})
    url = f'{EDSM_SYSTEM_API_BASE}/{endpoint}?{query}'
    request = Request(url, headers={'User-Agent': 'ed-finder-edsm-station-enrichment-probe/1.0'})
    max_retries = max(0, int(retries or 0))
    attempts = max_retries + 1
    label = _edsm_system_label(system_name, system_id64)
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:  # nosemgrep: dynamic-urllib-use-detected -- host is EDSM_SYSTEM_API_BASE (hardcoded), only the urlencoded query is dynamic
                status_code = _http_status_code(response)
                if status_code is not None and status_code >= 400:
                    raise _EdsmHttpStatusError(
                        status_code,
                        _http_response_reason(response, status_code),
                        _http_response_headers(response),
                    )
                return json.loads(response.read().decode('utf-8'))
        except Exception as exc:
            reason = _exception_reason(exc)
            status_code = _http_status_code(exc)
            retry_after_header = _retry_after_header(exc)
            retry_after_seconds = _retry_after_seconds(retry_after_header)
            rate_limited = _is_rate_limit_exception(exc, reason=reason, status_code=status_code)
            if attempt >= attempts:
                _log(logger, f'EDSM fetch failed system={label} endpoint={endpoint} attempt={attempt}/{attempts}: {reason}')
                error_cls = EdsmRateLimitError if rate_limited else EdsmFetchError
                raise error_cls(
                    f'EDSM {endpoint} fetch failed for {system_name!r} after {attempts} attempt(s): {reason}',
                    system_name=system_name,
                    system_id64=system_id64,
                    endpoint=endpoint,
                    attempts=attempts,
                    reason=reason,
                    status_code=status_code,
                    retry_after_seconds=retry_after_seconds,
                    retry_after_header=retry_after_header,
                ) from exc
            if rate_limited:
                delay = _rate_limit_delay_seconds(
                    retry_after_seconds=retry_after_seconds,
                    backoff_seconds=rate_limit_backoff_seconds,
                    multiplier=rate_limit_backoff_multiplier,
                    failed_attempt=attempt,
                )
                retry_after_text = (
                    f' retry_after={retry_after_header!r}'
                    if retry_after_header is not None
                    else ''
                )
                _log(
                    logger,
                    f'EDSM rate limit retry system={label} endpoint={endpoint} '
                    f'next_attempt={attempt + 1}/{attempts} reason={reason}{retry_after_text} '
                    f'backoff_seconds={delay:g}',
                )
            else:
                delay = _retry_delay_seconds(retry_backoff_seconds, attempt)
                _log(
                    logger,
                    f'EDSM fetch retry system={label} endpoint={endpoint} '
                    f'next_attempt={attempt + 1}/{attempts} reason={reason} backoff_seconds={delay:g}',
                )
            if delay > 0:
                sleep(delay)


def is_edsm_fetch_error(exc: BaseException) -> bool:
    return isinstance(exc, (EdsmFetchError, TimeoutError, socket.timeout, URLError, http.client.HTTPException))


def _retry_delay_seconds(backoff_seconds: float, failed_attempt: int) -> float:
    base_delay = max(0.0, float(backoff_seconds or 0.0))
    return base_delay * (2 ** max(0, failed_attempt - 1))


def _rate_limit_delay_seconds(
    *,
    retry_after_seconds: float | None,
    backoff_seconds: float,
    multiplier: float,
    failed_attempt: int,
) -> float:
    if retry_after_seconds is not None:
        # Honour Retry-After but clamp to the safety floor so a hostile/short
        # Retry-After does not push us into another 429 on the very next call.
        return max(MIN_RATE_LIMIT_RETRY_AFTER_SECONDS, float(retry_after_seconds))
    base_delay = max(0.0, float(backoff_seconds or 0.0))
    factor = max(1.0, float(multiplier or 1.0))
    return max(
        MIN_RATE_LIMIT_RETRY_AFTER_SECONDS,
        base_delay * (factor ** max(0, failed_attempt - 1)),
    )


def _retry_after_header(exc_or_response: Any) -> str | None:
    headers = getattr(exc_or_response, 'headers', None) or getattr(exc_or_response, 'hdrs', None)
    if headers is None:
        return None
    for name in ('Retry-After', 'retry-after'):
        try:
            value = headers.get(name)
        except AttributeError:
            value = headers.get(name) if isinstance(headers, Mapping) else None
        if value is not None:
            text = str(value).strip()
            return text or None
    return None


def _retry_after_seconds(header: str | None, *, now: datetime | None = None) -> float | None:
    if header is None:
        return None
    text = header.strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        retry_at = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0.0, (retry_at - current).total_seconds())


def _http_response_headers(response: Any) -> Any:
    headers = getattr(response, 'headers', None) or getattr(response, 'hdrs', None)
    if headers is not None:
        return headers
    info = getattr(response, 'info', None)
    if callable(info):
        try:
            return info()
        except Exception:
            return None
    return None


def _http_response_reason(response: Any, status_code: int) -> str:
    reason = getattr(response, 'reason', None) or getattr(response, 'msg', None)
    if reason:
        return str(reason)
    if status_code == 429:
        return 'Too Many Requests'
    return 'HTTP error'


def _http_status_code(exc_or_response: Any) -> int | None:
    for attr in ('code', 'status'):
        value = getattr(exc_or_response, attr, None)
        parsed = _read_int(value)
        if parsed is not None:
            return parsed
    getcode = getattr(exc_or_response, 'getcode', None)
    if callable(getcode):
        try:
            return _read_int(getcode())
        except Exception:
            return None
    return None


def _is_rate_limit_exception(
    exc: BaseException,
    *,
    reason: str | None = None,
    status_code: int | None = None,
) -> bool:
    status = status_code if status_code is not None else _http_status_code(exc)
    if status == 429:
        return True
    text = reason if reason is not None else _exception_reason(exc)
    return 'too many requests' in text.lower()


def _exception_reason(exc: BaseException) -> str:
    status_code = _http_status_code(exc)
    if status_code is not None:
        message = getattr(exc, 'reason', None) or getattr(exc, 'msg', None) or str(exc).strip()
        message_text = str(message).strip()
        return f'HTTP {status_code} {message_text}'.strip()
    reason = getattr(exc, 'reason', None)
    if reason is not None:
        return str(reason)
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _edsm_system_label(system_name: str, system_id64: int | None) -> str:
    if system_id64 is None:
        return repr(system_name)
    return f'{system_name!r} id64={system_id64}'


def _log(logger: Any, message: str) -> None:
    if logger is not None:
        logger(message)


def _stderr_log(message: str) -> None:
    print(message, file=sys.stderr)


def build_enrichment_report(
    *,
    local_system: Mapping[str, Any],
    local_stations: Sequence[Mapping[str, Any]],
    local_bodies: Sequence[Mapping[str, Any]],
    existing_links: Mapping[int, Mapping[str, Any]] | None,
    edsm_stations_payload: Any,
    edsm_bodies_payload: Any,
    network_enabled: bool = True,
) -> dict[str, Any]:
    """Build a station-centric dry-run report from local and EDSM evidence."""
    edsm_stations = [_normalise_edsm_station(row) for row in _extract_edsm_stations(edsm_stations_payload)]
    edsm_bodies = [_normalise_edsm_body(row) for row in _extract_edsm_bodies(edsm_bodies_payload)]
    links = existing_links or {}
    local_name_counts = Counter(_normalise_name(station.get('name')) for station in local_stations)

    station_reports = []
    matched_edsm_indexes: set[int] = set()
    for station in local_stations:
        station_report, matched_index = _build_station_report(
            station,
            local_stations=local_stations,
            local_name_counts=local_name_counts,
            local_bodies=local_bodies,
            existing_link=links.get(_read_int(station.get('id')) or -1),
            edsm_stations=edsm_stations,
            edsm_bodies=edsm_bodies,
        )
        if matched_index is not None:
            matched_edsm_indexes.add(matched_index)
        station_reports.append(station_report)

    unmatched_edsm_stations = [
        _public_edsm_station(station)
        for index, station in enumerate(edsm_stations)
        if index not in matched_edsm_indexes
    ]
    station_metadata_changes = [
        change for station in station_reports
        if (change := _station_metadata_change_entry(station)) is not None
    ]
    association_changes = [
        change for station in station_reports
        if (change := _association_change_entry(station)) is not None
    ]
    conflicts = [
        _conflict_entry(station, conflict)
        for station in station_reports
        for conflict in station['conflicts']
    ]
    ignored_transient_non_slot = [
        ignored for station in station_reports
        if (ignored := _ignored_transient_entry(station)) is not None
    ]
    station_writes_suppressed = _station_write_suppressed_entries(station_reports)
    metadata_updates = _metadata_update_candidates(station_reports)
    confirmed_link_updates = _confirmed_link_update_candidates(station_reports)
    skipped = _metadata_skipped_entries(station_reports)
    unresolved = [
        entry for station in station_reports
        if (entry := _unresolved_entry(station)) is not None
    ]

    summary = Counter()
    for station in station_reports:
        proposed = station['proposed']
        summary[f"association:{proposed['association_status']}"] += 1
        summary[f"lane:{proposed['lane']}"] += 1
        summary[f"station_match:{station['station_match']['status']}"] += 1
        if station['fields_that_would_change']:
            summary['stations_with_changes'] += 1
        if station['conflicts']:
            summary['stations_with_conflicts'] += 1
        if station.get('association_would_change'):
            summary['stations_with_association_changes'] += 1
        if station.get('ignored_transient_non_slot'):
            summary['stations_ignored_transient_non_slot'] += 1

    return {
        'dry_run': True,
        'network_enabled': network_enabled,
        'source': 'edsm_system_api',
        'system': {
            'id64': _read_int(local_system.get('id64')),
            'name': _clean_text(local_system.get('name')),
        },
        'counts': {
            'local_stations': len(local_stations),
            'local_bodies': len(local_bodies),
            'edsm_stations': len(edsm_stations),
            'edsm_bodies': len(edsm_bodies),
            'unmatched_edsm_stations': len(unmatched_edsm_stations),
            'station_metadata_changes': len(station_metadata_changes),
            'metadata_updates_planned': len(metadata_updates),
            'metadata_updates_applied': 0,
            'confirmed_link_updates_planned': len(confirmed_link_updates),
            'confirmed_link_updates_applied': 0,
            'station_write_suppressed_non_benign_conflict': len(station_writes_suppressed),
            'association_changes': len(association_changes),
            'conflicts': len(conflicts),
            'ignored_transient_non_slot': len(ignored_transient_non_slot),
            'skipped': len(skipped),
            'unresolved': len(unresolved),
            **dict(sorted(summary.items())),
        },
        'apply_mode': 'dry_run',
        'metadata_apply_contract': {
            'safe_fields': ['station_type', 'distance_from_star', 'body_name'],
            'provenance_source': TRUSTED_EDSM_SOURCE,
            'provenance_confidence': TRUSTED_STATION_IDENTITY_CONFIDENCE,
            'station_identity_rule': 'EDSM id/marketId must match local id/market_id and station name must match.',
            'station_type_rule': 'Unknown -> known permanent station type only.',
            'distance_rule': (
                'distanceToArrival may replace legacy local station distance only after exact station identity; '
                f'trusted exact EDSM station distances within {TRUSTED_STATION_DISTANCE_PRECISION_TOLERANCE_LS:g} ls '
                'are treated as stored precision noise.'
            ),
            'body_name_rule': 'EDSM bodyName may update stations.body_name only when it matches exactly one local same-system body.',
            'non_benign_conflict_rule': 'Any identity/context unsafe conflict suppresses all station metadata writes for that station in this run.',
            'never_applied': [
                'body_id',
                'association_status',
                'association_confidence',
            ],
        },
        'confirmed_link_apply_contract': {
            'safe_fields': ['station_body_links'],
            'rule': 'Only confirmed/exact EDSM bodyName links for permanent stations are applied, scoped to one --system-id64.',
            'non_benign_conflict_rule': 'Any identity/context unsafe conflict suppresses confirmed station_body_links writes for that station in this run.',
            'never_applied': ['inferred_distance_links', 'transient_non_slot_links'],
        },
        'matching_rules': {
            'station_identity': [
                'EDSM id/marketId is exact only when it also matches the local station name.',
                'A unique exact station name in the same local system is exact name evidence.',
                'distanceToArrival supports or conflicts with identity; it is not used by itself.',
            ],
            'body_association': [
                'EDSM bodyName/body.name must match exactly one local same-system body to confirm.',
                'EDSM station bodyId is mapped through the EDSM bodies payload name before local use.',
                f'distance-only body association is inferred only when exactly one body is within {BODY_DISTANCE_MATCH_TOLERANCE_LS:g} ls.',
            ],
        },
        'station_metadata_changes': station_metadata_changes,
        'metadata_updates_planned': metadata_updates,
        'metadata_updates_applied': [],
        'confirmed_link_updates_planned': confirmed_link_updates,
        'confirmed_link_updates_applied': [],
        'station_writes_suppressed': station_writes_suppressed,
        'association_changes': association_changes,
        'conflicts': conflicts,
        'ignored_transient_non_slot': ignored_transient_non_slot,
        'skipped': skipped,
        'unresolved': unresolved,
        'stations': station_reports,
        'unmatched_edsm_stations': unmatched_edsm_stations,
    }


def _build_station_report(
    local_station: Mapping[str, Any],
    *,
    local_stations: Sequence[Mapping[str, Any]],
    local_name_counts: Counter,
    local_bodies: Sequence[Mapping[str, Any]],
    existing_link: Mapping[str, Any] | None,
    edsm_stations: Sequence[Mapping[str, Any]],
    edsm_bodies: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], int | None]:
    station_match, matched_index, station_conflicts = _match_edsm_station(
        local_station,
        local_stations=local_stations,
        local_name_counts=local_name_counts,
        edsm_stations=edsm_stations,
    )
    conflicts = list(station_conflicts)
    fields_that_would_change: list[str] = []
    local_public = _public_local_station(local_station)
    existing_public = _public_existing_link(existing_link)

    if matched_index is None:
        proposed = _unresolved_proposal(
            station_type=normalise_station_type_label(local_station.get('station_type')) or 'Unknown',
            note=station_match['reason'],
        )
        return ({
            'local_station': local_public,
            'existing_link': existing_public,
            'station_match': station_match,
            'edsm_station': None,
            'proposed': proposed,
            'fields_that_would_change': fields_that_would_change,
            'association_would_change': False,
            'ignored_transient_non_slot': False,
            'station_type_evidence': {
                'local': local_public['station_type'],
                'edsm': None,
                'proposed': proposed['station_type'],
                'diagnostic_only': False,
            },
            'conflicts': conflicts,
        }, None)

    edsm_station = edsm_stations[matched_index]
    edsm_public = _public_edsm_station(edsm_station)
    local_type = normalise_station_type_label(local_station.get('station_type')) or 'Unknown'
    edsm_type = _clean_text(edsm_station.get('station_type')) or 'Unknown'
    station_type_fields_that_would_change: list[str] = []
    proposed_type = _proposed_station_type(local_type, edsm_type, conflicts, station_type_fields_that_would_change)
    trusted_identity = _has_trusted_station_identity(station_match)
    lane, lane_note = classify_station_lane(proposed_type)
    station_type_evidence = {
        'local': local_type,
        'edsm': edsm_type,
        'proposed': proposed_type,
        'trusted_identity': trusted_identity,
        'diagnostic_only': False,
    }
    is_transient_non_slot = (
        is_transient_non_slot_station_type(local_type)
        or is_transient_non_slot_station_type(edsm_type)
        or is_transient_non_slot_station_type(proposed_type)
    )
    if is_transient_non_slot:
        station_type_evidence['diagnostic_only'] = True
        body_proposal = _unresolved_proposal(
            station_type=proposed_type,
            lane=lane,
            note=_join_notes(
                lane_note,
                'Transient/mobile infrastructure is ignored for colony-planning station_body_links.',
            ),
        )
        return ({
            'local_station': local_public,
            'existing_link': existing_public,
            'station_match': station_match,
            'edsm_station': edsm_public,
            'proposed': body_proposal,
            'fields_that_would_change': [],
            'association_would_change': False,
            'ignored_transient_non_slot': True,
            'station_type_evidence': station_type_evidence,
            'conflicts': conflicts,
        }, matched_index)

    fields_that_would_change.extend(station_type_fields_that_would_change)

    if is_permanent_colony_slot_station_type(proposed_type):
        body_proposal = _propose_body_association(
            edsm_station,
            local_bodies=local_bodies,
            edsm_bodies=edsm_bodies,
            lane=lane,
            lane_note=lane_note,
            station_match=station_match,
            trusted_station_identity=trusted_identity,
            conflicts=conflicts,
        )
    else:
        body_proposal = _unresolved_proposal(
            station_type=proposed_type,
            lane=lane,
            note=_join_notes(
                lane_note,
                'Station type is not a permanent colony-planning slot type; association evidence is diagnostic only.',
            ),
        )

    _append_station_data_changes(
        local_station,
        edsm_station,
        proposed_type=proposed_type,
        body_proposal=body_proposal,
        trusted_station_identity=trusted_identity,
        fields_that_would_change=fields_that_would_change,
        conflicts=conflicts,
    )

    _preserve_confirmed_link(
        existing_link,
        proposed=body_proposal,
        fields_that_would_change=fields_that_would_change,
        conflicts=conflicts,
    )

    association_would_change = _association_would_change(
        local_station=local_station,
        existing_link=existing_link,
        proposed=body_proposal,
    )

    return ({
        'local_station': local_public,
        'existing_link': existing_public,
        'station_match': station_match,
        'edsm_station': edsm_public,
        'proposed': body_proposal,
        'fields_that_would_change': sorted(set(fields_that_would_change)),
        'association_would_change': association_would_change,
        'ignored_transient_non_slot': False,
        'station_type_evidence': station_type_evidence,
        'conflicts': conflicts,
    }, matched_index)


def _association_would_change(
    *,
    local_station: Mapping[str, Any],
    existing_link: Mapping[str, Any] | None,
    proposed: Mapping[str, Any],
) -> bool:
    proposed_status = _clean_text(proposed.get('association_status'))
    if proposed_status not in ('confirmed', 'inferred'):
        return False
    if not is_permanent_colony_slot_station_type(proposed.get('station_type')):
        return False
    if _read_int(proposed.get('body_id')) is None and _clean_text(proposed.get('body_name')) is None:
        return False
    if existing_link and existing_link.get('association_status') == 'confirmed':
        return False

    current_body_id = _read_int(existing_link.get('body_id')) if existing_link else None
    current_body_name = (
        _clean_text(existing_link.get('body_name'))
        if existing_link else _clean_text(local_station.get('body_name'))
    )
    current_lane = _clean_text(existing_link.get('lane')) if existing_link else None
    current_status = _clean_text(existing_link.get('association_status')) if existing_link else None
    current_confidence = _clean_text(existing_link.get('association_confidence')) if existing_link else None
    current_source = _clean_text(existing_link.get('association_source')) if existing_link else None

    return any((
        current_body_id != _read_int(proposed.get('body_id')),
        _normalise_name(current_body_name) != _normalise_name(proposed.get('body_name')),
        current_lane != _clean_text(proposed.get('lane')),
        current_status != proposed_status,
        current_confidence != _clean_text(proposed.get('association_confidence')),
        current_source != _clean_text(proposed.get('association_source')),
    ))


def _station_metadata_change_entry(station: Mapping[str, Any]) -> dict[str, Any] | None:
    fields = [
        field for field in station.get('fields_that_would_change', [])
        if field != 'station_body_links'
    ]
    if not fields:
        return None
    proposed = station['proposed']
    return {
        'local_station': station['local_station'],
        'edsm_station': station.get('edsm_station'),
        'station_match': station['station_match'],
        'fields_that_would_change': sorted(set(fields)),
        'station_type_evidence': station.get('station_type_evidence'),
        'proposed_station_type': proposed.get('station_type'),
    }


def _association_change_entry(station: Mapping[str, Any]) -> dict[str, Any] | None:
    if not station.get('association_would_change'):
        return None
    proposed = station['proposed']
    return {
        'local_station': station['local_station'],
        'existing_link': station.get('existing_link'),
        'edsm_station': station.get('edsm_station'),
        'station_match': station['station_match'],
        'proposed_link': {
            'body_id': _read_int(proposed.get('body_id')),
            'body_name': _clean_text(proposed.get('body_name')),
            'lane': _clean_text(proposed.get('lane')),
            'association_status': _clean_text(proposed.get('association_status')),
            'association_confidence': _clean_text(proposed.get('association_confidence')),
            'association_source': _clean_text(proposed.get('association_source')),
            'resolver_notes': _clean_text(proposed.get('resolver_notes')),
        },
    }


def _conflict_entry(station: Mapping[str, Any], conflict: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'local_station': station['local_station'],
        'edsm_station': station.get('edsm_station'),
        'station_match': station['station_match'],
        'conflict': dict(conflict),
    }


def _ignored_transient_entry(station: Mapping[str, Any]) -> dict[str, Any] | None:
    if not station.get('ignored_transient_non_slot'):
        return None
    edsm_station = station.get('edsm_station') or {}
    return {
        'local_station': station['local_station'],
        'edsm_station': station.get('edsm_station'),
        'station_match': station['station_match'],
        'station_type_evidence': station.get('station_type_evidence'),
        'body_evidence': {
            'body_id': _read_int(edsm_station.get('body_id')),
            'body_name': _clean_text(edsm_station.get('body_name')),
            'distance_to_arrival': _read_float(edsm_station.get('distance_to_arrival')),
        },
        'reason': _clean_text(station['proposed'].get('resolver_notes')),
        'conflicts': station.get('conflicts', []),
    }


def _unresolved_entry(station: Mapping[str, Any]) -> dict[str, Any] | None:
    if station['station_match'].get('status') == 'matched':
        return None
    return {
        'local_station': station['local_station'],
        'station_match': station['station_match'],
        'reason': station['station_match'].get('reason') or 'Station was not matched to EDSM.',
    }


def _metadata_update_candidates(station_reports: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for station in station_reports:
        for field in ('station_type', 'distance_from_star', 'body_name'):
            update = _metadata_update_candidate(station, field)
            if update is not None:
                updates.append(update)
    return updates


def _confirmed_link_update_candidates(station_reports: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for station in station_reports:
        proposed = station.get('proposed') or {}
        local_station = station.get('local_station') or {}
        if _non_benign_conflict_types(station):
            continue
        if not station.get('association_would_change'):
            continue
        if station.get('ignored_transient_non_slot'):
            continue
        if not _has_trusted_station_identity(station.get('station_match') or {}):
            continue
        if proposed.get('association_status') != 'confirmed':
            continue
        if proposed.get('association_confidence') != 'exact':
            continue
        if proposed.get('association_source') != 'edsm_body_name':
            continue
        if not is_permanent_colony_slot_station_type(proposed.get('station_type')):
            continue
        station_id = _read_int(local_station.get('id'))
        system_id64 = _read_int(local_station.get('system_id64'))
        body_id = _read_int(proposed.get('body_id'))
        body_name = _clean_text(proposed.get('body_name'))
        if station_id is None or system_id64 is None or body_id is None or body_name is None:
            continue
        updates.append({
            'local_station': local_station,
            'edsm_station': station.get('edsm_station'),
            'station_match': station['station_match'],
            'station_id': station_id,
            'market_id': _read_int(local_station.get('market_id')) or station_id,
            'system_id64': system_id64,
            'body_id': body_id,
            'body_name': body_name,
            'lane': _clean_text(proposed.get('lane')) or 'unknown',
            'association_status': 'confirmed',
            'association_confidence': 'exact',
            'association_source': 'edsm_body_name',
            'resolver_notes': _clean_text(proposed.get('resolver_notes')),
        })
    return updates


def _metadata_skipped_entries(station_reports: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    skipped: list[dict[str, Any]] = []
    for station in station_reports:
        if _non_benign_conflict_types(station):
            skipped.append(_station_write_suppression_entry(station))
            continue
        if station['station_match'].get('status') != 'matched':
            skipped.append(_metadata_skip_entry(station, 'station_type', 'unresolved_station_match'))
            continue
        fields = set(station.get('fields_that_would_change', []))
        for field in sorted(fields & SUPPORTED_STATION_METADATA_FIELDS):
            reason = _metadata_apply_skip_reason(station, field)
            if reason is not None:
                skipped.append(_metadata_skip_entry(station, field, reason))
        unsupported = sorted(fields - SUPPORTED_STATION_METADATA_FIELDS - {'station_body_links'})
        if unsupported:
            skipped.append(_metadata_skip_entry(
                station,
                'unsupported',
                f"unsupported_metadata_fields:{','.join(unsupported)}",
            ))
    return [entry for entry in skipped if entry is not None]


def _metadata_update_candidate(station: Mapping[str, Any], field: str) -> dict[str, Any] | None:
    if field not in set(station.get('fields_that_would_change', [])):
        return None
    skip_reason = _metadata_apply_skip_reason(station, field)
    if skip_reason is not None:
        return None

    local_station = station['local_station']
    old_value: Any
    new_value: Any
    if field == 'station_type':
        evidence = station.get('station_type_evidence') or {}
        old_value = evidence.get('local')
        new_value = evidence.get('proposed')
    elif field == 'distance_from_star':
        old_value = local_station.get('distance_from_star')
        new_value = (station.get('edsm_station') or {}).get('distance_to_arrival')
    elif field == 'body_name':
        old_value = local_station.get('body_name')
        new_value = (station.get('proposed') or {}).get('body_name')
    else:
        return None

    return {
        'local_station': local_station,
        'edsm_station': station.get('edsm_station'),
        'station_match': station['station_match'],
        'field': field,
        'old_value': old_value,
        'new_value': new_value,
        'source': TRUSTED_EDSM_SOURCE,
        'confidence': TRUSTED_STATION_IDENTITY_CONFIDENCE,
        'station_id': _read_int(local_station.get('id')),
        'system_id64': _read_int(local_station.get('system_id64')),
    }


def _metadata_skip_entry(station: Mapping[str, Any], field: str, reason: str) -> dict[str, Any]:
    entry = {
        'local_station': station['local_station'],
        'edsm_station': station.get('edsm_station'),
        'station_match': station['station_match'],
        'field': field,
        'station_type_evidence': station.get('station_type_evidence'),
        'reason': reason,
    }
    if reason == STATION_WRITE_SUPPRESSED_REASON:
        entry.update(_station_write_suppression_details(station))
    return entry


def _metadata_apply_skip_reason(station: Mapping[str, Any], field: str) -> str | None:
    match = station['station_match']
    if match.get('status') != 'matched':
        return 'unresolved_station_match'
    if station.get('ignored_transient_non_slot'):
        return 'transient_non_slot_ignored'
    if _non_benign_conflict_types(station):
        return STATION_WRITE_SUPPRESSED_REASON

    if not _has_trusted_station_identity(match):
        return 'weak_station_identity'

    evidence = station.get('station_type_evidence') or {}
    proposed_type = evidence.get('proposed')
    if not is_permanent_colony_slot_station_type(proposed_type):
        return 'non_permanent_station_type'

    if field == 'station_type':
        local_type = evidence.get('local')
        if local_type != 'Unknown':
            return 'known_station_type_preserved'
    elif field == 'distance_from_star':
        if _read_float((station.get('edsm_station') or {}).get('distance_to_arrival')) is None:
            return 'missing_edsm_distance'
    elif field == 'body_name':
        proposed = station.get('proposed') or {}
        if proposed.get('association_status') != 'confirmed' or proposed.get('association_source') != 'edsm_body_name':
            return 'missing_exact_edsm_body_name_match'
    else:
        return 'unsupported_metadata_field'

    if _read_int(station['local_station'].get('id')) is None:
        return 'missing_station_id'
    if _read_int(station['local_station'].get('system_id64')) is None:
        return 'missing_system_id64'
    return None


def _station_write_suppressed_entries(station_reports: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        _station_write_suppression_entry(station)
        for station in station_reports
        if _non_benign_conflict_types(station)
    ]


def _station_write_suppression_entry(station: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'local_station': station['local_station'],
        'edsm_station': station.get('edsm_station'),
        'station_match': station['station_match'],
        'field': 'station_writes',
        'reason': STATION_WRITE_SUPPRESSED_REASON,
        **_station_write_suppression_details(station),
    }


def _station_write_suppression_details(station: Mapping[str, Any]) -> dict[str, Any]:
    conflict_types = _non_benign_conflict_types(station)
    metadata_fields = sorted(
        field for field in set(station.get('fields_that_would_change', []))
        if field in SUPPORTED_STATION_METADATA_FIELDS
    )
    candidate_writes = list(metadata_fields)
    if station.get('association_would_change'):
        candidate_writes.append('station_body_links')
    return {
        'conflict_types': conflict_types,
        'conflicts': [
            dict(conflict)
            for conflict in station.get('conflicts', [])
            if _is_non_benign_station_conflict(conflict)
        ],
        'suppressed_scopes': [
            'station_metadata',
            'body_name_metadata',
            'station_body_links',
        ],
        'suppressed_write_fields': candidate_writes,
        'message': 'Station writes suppressed because non-benign station identity/context conflict(s) were present.',
    }


def _non_benign_conflict_types(station: Mapping[str, Any]) -> list[str]:
    return _non_benign_conflict_types_from_conflicts(station.get('conflicts', []))


def _non_benign_conflict_types_from_conflicts(conflicts: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted({
        str(conflict.get('type'))
        for conflict in conflicts
        if conflict.get('type') is not None and _is_non_benign_station_conflict(conflict)
    })


def _is_non_benign_station_conflict(conflict: Mapping[str, Any]) -> bool:
    kind = conflict.get('type')
    if kind in NON_BENIGN_STATION_CONFLICT_TYPES:
        return True
    if any(bool(conflict.get(marker)) for marker in IDENTITY_CONTEXT_UNSAFE_MARKERS):
        return True
    write_safety = _clean_text(conflict.get('write_safety'))
    return write_safety in IDENTITY_CONTEXT_UNSAFE_MARKERS


def apply_metadata_updates(conn, report: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply planned safe station metadata updates and return applied/skipped rows."""
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for update in report.get('metadata_updates_planned', []):
        station_id = _read_int(update.get('station_id'))
        system_id64 = _read_int(update.get('system_id64'))
        field = _clean_text(update.get('field'))
        if station_id is None or system_id64 is None or field not in SUPPORTED_STATION_METADATA_FIELDS:
            skipped.append({
                **update,
                'reason': 'invalid_metadata_update_plan',
            })
            continue
        row = _apply_one_metadata_update(conn, update)
        if row is None:
            skipped.append({
                **update,
                'reason': 'station_row_changed_or_update_not_allowed',
            })
            continue
        applied.append({
            **update,
            'applied_station': _public_applied_station(row),
        })
    return applied, skipped


def _apply_one_metadata_update(conn, update: Mapping[str, Any]) -> Mapping[str, Any] | None:
    station_id = _read_int(update.get('station_id'))
    system_id64 = _read_int(update.get('system_id64'))
    source = _clean_text(update.get('source')) or TRUSTED_EDSM_SOURCE
    confidence = _clean_text(update.get('confidence')) or TRUSTED_STATION_IDENTITY_CONFIDENCE
    field = _clean_text(update.get('field'))

    if field == 'station_type':
        new_value = _clean_text(update.get('new_value'))
        if not is_permanent_colony_slot_station_type(new_value):
            return None
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                UPDATE stations
                SET station_type = %s::station_type,
                    station_type_source = %s,
                    station_type_confidence = %s,
                    station_type_updated_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND system_id64 = %s
                  AND station_type = 'Unknown'::station_type
                RETURNING id, system_id64, name, station_type::text AS station_type,
                          distance_from_star, distance_source, distance_confidence,
                          body_name, body_name_source, body_name_confidence,
                          station_type_source, station_type_confidence
            """, (new_value, source, confidence, station_id, system_id64))
            return cur.fetchone()

    if field == 'distance_from_star':
        new_value = _read_float(update.get('new_value'))
        if new_value is None:
            return None
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                UPDATE stations
                SET distance_from_star = %s,
                    distance_source = %s,
                    distance_confidence = %s,
                    distance_updated_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND system_id64 = %s
                RETURNING id, system_id64, name, station_type::text AS station_type,
                          distance_from_star, distance_source, distance_confidence,
                          body_name, body_name_source, body_name_confidence,
                          station_type_source, station_type_confidence
            """, (new_value, source, confidence, station_id, system_id64))
            return cur.fetchone()

    if field == 'body_name':
        new_value = _clean_text(update.get('new_value'))
        if new_value is None:
            return None
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                UPDATE stations
                SET body_name = %s,
                    body_name_source = %s,
                    body_name_confidence = %s,
                    body_name_updated_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND system_id64 = %s
                RETURNING id, system_id64, name, station_type::text AS station_type,
                          distance_from_star, distance_source, distance_confidence,
                          body_name, body_name_source, body_name_confidence,
                          station_type_source, station_type_confidence
            """, (new_value, source, confidence, station_id, system_id64))
            return cur.fetchone()

    return None


def _public_applied_station(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'id': _read_int(row.get('id')),
        'system_id64': _read_int(row.get('system_id64')),
        'name': _clean_text(row.get('name')),
        'station_type': _clean_text(row.get('station_type')),
        'distance_from_star': _read_float(row.get('distance_from_star')),
        'distance_source': _clean_text(row.get('distance_source')),
        'distance_confidence': _clean_text(row.get('distance_confidence')),
        'body_name': _clean_text(row.get('body_name')),
        'body_name_source': _clean_text(row.get('body_name_source')),
        'body_name_confidence': _clean_text(row.get('body_name_confidence')),
        'station_type_source': _clean_text(row.get('station_type_source')),
        'station_type_confidence': _clean_text(row.get('station_type_confidence')),
    }


def apply_metadata_result(report: dict[str, Any], applied: Sequence[Mapping[str, Any]], skipped: Sequence[Mapping[str, Any]]) -> None:
    report['dry_run'] = False
    report['apply_mode'] = 'metadata'
    report['metadata_updates_applied'] = [dict(row) for row in applied]
    report['skipped'] = [*report.get('skipped', []), *[dict(row) for row in skipped]]
    report['counts']['metadata_updates_applied'] = len(applied)
    report['counts']['skipped'] = len(report['skipped'])


def apply_confirmed_link_updates(conn, report: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply confirmed exact EDSM bodyName station_body_links only."""
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for update in report.get('confirmed_link_updates_planned', []):
        station_id = _read_int(update.get('station_id'))
        system_id64 = _read_int(update.get('system_id64'))
        body_id = _read_int(update.get('body_id'))
        body_name = _clean_text(update.get('body_name'))
        lane = _clean_text(update.get('lane'))
        if station_id is None or system_id64 is None or body_id is None or body_name is None or lane not in ('orbital', 'surface'):
            skipped.append({**update, 'reason': 'invalid_confirmed_link_plan'})
            continue
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                INSERT INTO station_body_links (
                    station_id, market_id, system_id64, body_id, body_name, lane,
                    association_status, association_confidence, association_source,
                    resolver_notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'confirmed', 'exact', 'edsm_body_name', %s)
                ON CONFLICT (station_id) DO UPDATE SET
                    market_id = EXCLUDED.market_id,
                    system_id64 = EXCLUDED.system_id64,
                    body_id = EXCLUDED.body_id,
                    body_name = EXCLUDED.body_name,
                    lane = EXCLUDED.lane,
                    association_status = EXCLUDED.association_status,
                    association_confidence = EXCLUDED.association_confidence,
                    association_source = EXCLUDED.association_source,
                    resolver_notes = EXCLUDED.resolver_notes,
                    updated_at = NOW()
                WHERE station_body_links.association_status <> 'confirmed'
                RETURNING station_id, market_id, system_id64, body_id, body_name, lane,
                          association_status, association_confidence, association_source,
                          resolver_notes
            """, (
                station_id,
                _read_int(update.get('market_id')) or station_id,
                system_id64,
                body_id,
                body_name,
                lane,
                _clean_text(update.get('resolver_notes')),
            ))
            row = cur.fetchone()
        if row is None:
            skipped.append({**update, 'reason': 'confirmed_link_preserved_or_row_changed'})
            continue
        applied.append({
            **update,
            'applied_link': {
                'station_id': _read_int(row.get('station_id')),
                'market_id': _read_int(row.get('market_id')),
                'system_id64': _read_int(row.get('system_id64')),
                'body_id': _read_int(row.get('body_id')),
                'body_name': _clean_text(row.get('body_name')),
                'lane': _clean_text(row.get('lane')),
                'association_status': _clean_text(row.get('association_status')),
                'association_confidence': _clean_text(row.get('association_confidence')),
                'association_source': _clean_text(row.get('association_source')),
                'resolver_notes': _clean_text(row.get('resolver_notes')),
            },
        })
    return applied, skipped


def apply_confirmed_links_result(report: dict[str, Any], applied: Sequence[Mapping[str, Any]], skipped: Sequence[Mapping[str, Any]]) -> None:
    report['dry_run'] = False
    current_mode = report.get('apply_mode')
    if current_mode and current_mode != 'dry_run':
        report['apply_mode'] = f'{current_mode}+confirmed_links'
    else:
        report['apply_mode'] = 'confirmed_links'
    report['confirmed_link_updates_applied'] = [dict(row) for row in applied]
    report['skipped'] = [*report.get('skipped', []), *[dict(row) for row in skipped]]
    report['counts']['confirmed_link_updates_applied'] = len(applied)
    report['counts']['skipped'] = len(report['skipped'])


def _match_edsm_station(
    local_station: Mapping[str, Any],
    *,
    local_stations: Sequence[Mapping[str, Any]],
    local_name_counts: Counter,
    edsm_stations: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], int | None, list[dict[str, Any]]]:
    local_name = _normalise_name(local_station.get('name'))
    local_ids = _station_identity_values(local_station)
    conflicts: list[dict[str, Any]] = []

    id_matches: list[tuple[int, Mapping[str, Any]]] = []
    id_name_conflicts: list[Mapping[str, Any]] = []
    for index, station in enumerate(edsm_stations):
        if not local_ids.intersection(_station_identity_values(station)):
            continue
        if _normalise_name(station.get('name')) == local_name:
            id_matches.append((index, station))
        else:
            id_name_conflicts.append(station)

    if id_name_conflicts:
        conflicts.append({
            'field': 'station_identity',
            'type': 'id_name_mismatch',
            'write_safety': 'identity_context_unsafe',
            'identity_context_unsafe': True,
            'message': 'Local station id matched an EDSM id/marketId with a different station name.',
            'edsm_candidates': [_public_edsm_station(station) for station in id_name_conflicts],
        })

    if len(id_matches) == 1:
        index, station = id_matches[0]
        confidence, distance_conflict = _station_distance_match(local_station, station)
        if distance_conflict is not None:
            conflicts.append(distance_conflict)
        return ({
            'status': 'matched',
            'confidence': 'exact',
            'source': 'id_name',
            'distance_support': confidence,
        }, index, conflicts)
    if len(id_matches) > 1:
        return ({
            'status': 'unresolved',
            'confidence': 'unresolved',
            'source': 'id_name',
            'reason': 'Multiple EDSM stations matched local id/name identity.',
        }, None, conflicts)

    name_matches = [
        (index, station)
        for index, station in enumerate(edsm_stations)
        if _normalise_name(station.get('name')) == local_name
    ]
    if len(name_matches) == 1 and local_name_counts[local_name] == 1:
        index, station = name_matches[0]
        distance_support, distance_conflict = _station_distance_match(local_station, station)
        if distance_conflict is not None:
            conflicts.append(distance_conflict)
        return ({
            'status': 'matched',
            'confidence': 'exact',
            'source': 'exact_name',
            'distance_support': distance_support,
        }, index, conflicts)
    if len(name_matches) > 1 or local_name_counts[local_name] > 1:
        return ({
            'status': 'unresolved',
            'confidence': 'unresolved',
            'source': 'exact_name',
            'reason': 'Station name matched multiple local or EDSM candidates.',
        }, None, conflicts)

    return ({
        'status': 'unresolved',
        'confidence': 'unresolved',
        'source': 'none',
        'reason': 'No exact EDSM station id/name or unique station name match.',
    }, None, conflicts)


def _station_distance_match(local_station: Mapping[str, Any], edsm_station: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None]:
    local_distance = _read_float(local_station.get('distance_from_star'))
    edsm_distance = _read_float(edsm_station.get('distance_to_arrival'))
    if local_distance is None or edsm_distance is None:
        return 'missing', None
    delta = abs(local_distance - edsm_distance)
    if delta <= STATION_DISTANCE_CONFLICT_TOLERANCE_LS:
        return 'matches', None
    return 'conflict', {
        'field': 'distance_from_star',
        'type': 'station_distance_mismatch',
        'write_safety': 'diagnostic',
        'local': local_distance,
        'edsm': edsm_distance,
        'delta_ls': delta,
        'tolerance_ls': STATION_DISTANCE_CONFLICT_TOLERANCE_LS,
        'local_distance_source': _clean_text(local_station.get('distance_source')),
        'local_distance_confidence': _clean_text(local_station.get('distance_confidence')),
        'message': 'Local station distance differs from EDSM distanceToArrival; legacy/untrusted local distance does not block trusted EDSM metadata.',
    }


def _propose_body_association(
    edsm_station: Mapping[str, Any],
    *,
    local_bodies: Sequence[Mapping[str, Any]],
    edsm_bodies: Sequence[Mapping[str, Any]],
    lane: str,
    lane_note: str | None,
    station_match: Mapping[str, Any],
    trusted_station_identity: bool,
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    station_type = _clean_text(edsm_station.get('station_type')) or 'Unknown'
    base_notes = [lane_note]
    if not trusted_station_identity:
        base_notes.append('EDSM station identity is not exact id/name; body association evidence is diagnostic only.')

    body_name = _clean_text(edsm_station.get('body_name'))
    if body_name is None:
        body_name = _body_name_from_edsm_station_body_id(edsm_station, edsm_bodies)
        if body_name is not None:
            base_notes.append('EDSM station bodyId was mapped through the EDSM bodies payload name.')

    if body_name is not None:
        name_matches = _local_bodies_by_name(local_bodies, body_name)
        if len(name_matches) == 1 and trusted_station_identity and not _has_blocking_conflict(conflicts):
            body = name_matches[0]
            return _proposal(
                station_type=station_type,
                body=body,
                body_name=_clean_text(body.get('name')) or body_name,
                lane=lane,
                status='confirmed',
                confidence='exact',
                source='edsm_body_name',
                notes=_join_notes(*base_notes, 'Exact EDSM body name matched one local same-system body.'),
            )
        if len(name_matches) == 1:
            return _unresolved_proposal(
                station_type=station_type,
                lane=lane,
                note=_join_notes(*base_notes, 'EDSM body name matched one local same-system body but is not eligible for confirmed apply.'),
            )
        if len(name_matches) > 1:
            conflicts.append({
                'field': 'body_name',
                'type': 'multiple_local_body_name_matches',
                'edsm': body_name,
                'message': 'EDSM body name matched multiple local bodies.',
            })
            return _unresolved_proposal(station_type=station_type, lane=lane, note=_join_notes(*base_notes, 'EDSM body name was ambiguous.'))
        conflicts.append({
            'field': 'body_name',
            'type': 'edsm_body_name_not_found_locally',
            'edsm': body_name,
            'message': 'EDSM body name did not match a local body in this system.',
        })
        return _unresolved_proposal(station_type=station_type, lane=lane, note=_join_notes(*base_notes, 'EDSM body name was not found locally.'))

    station_distance = _read_float(edsm_station.get('distance_to_arrival'))
    if station_distance is None:
        return _unresolved_proposal(
            station_type=station_type,
            lane=lane,
            note=_join_notes(*base_notes, 'EDSM station has no body name/body id or distanceToArrival evidence.'),
        )

    distance_matches = _unique_body_distance_candidates(station_distance, local_bodies=local_bodies, edsm_bodies=edsm_bodies)
    if len(distance_matches) == 1 and trusted_station_identity and not _has_blocking_conflict(conflicts):
        body = distance_matches[0]
        return _proposal(
            station_type=station_type,
            body=body,
            body_name=_clean_text(body.get('name')),
            lane=lane,
            status='inferred',
            confidence='strong_inference',
            source='edsm_distance',
            notes=_join_notes(
                *base_notes,
                f'Unique EDSM/local body distance match within {BODY_DISTANCE_MATCH_TOLERANCE_LS:g} ls.',
            ),
        )
    if len(distance_matches) == 1:
        return _unresolved_proposal(
            station_type=station_type,
            lane=lane,
            note=_join_notes(*base_notes, 'EDSM distance matched one body but is not eligible for inferred association.'),
        )
    if len(distance_matches) > 1:
        conflicts.append({
            'field': 'distance_to_arrival',
            'type': 'multiple_body_distance_matches',
            'edsm': station_distance,
            'candidate_body_ids': [_read_int(body.get('id')) for body in distance_matches],
            'tolerance_ls': BODY_DISTANCE_MATCH_TOLERANCE_LS,
        })
        return _unresolved_proposal(station_type=station_type, lane=lane, note=_join_notes(*base_notes, 'EDSM distance matched multiple bodies.'))

    return _unresolved_proposal(
        station_type=station_type,
        lane=lane,
        note=_join_notes(*base_notes, 'No exact body name/body id or unique distance match is available.'),
    )


def _proposed_station_type(
    local_type: str,
    edsm_type: str,
    conflicts: list[dict[str, Any]],
    fields_that_would_change: list[str],
) -> str:
    if edsm_type == 'Unknown':
        return local_type if local_type else 'Unknown'
    if local_type in (None, '', 'Unknown'):
        if is_permanent_colony_slot_station_type(edsm_type):
            fields_that_would_change.append('station_type')
        return edsm_type
    if local_type != edsm_type:
        conflicts.append({
            'field': 'station_type',
            'type': 'known_station_type_mismatch',
            'write_safety': 'identity_context_unsafe',
            'identity_context_unsafe': True,
            'local': local_type,
            'edsm': edsm_type,
            'message': 'EDSM would change a known local station type.',
        })
    return local_type


def _append_station_data_changes(
    local_station: Mapping[str, Any],
    edsm_station: Mapping[str, Any],
    *,
    proposed_type: str,
    body_proposal: Mapping[str, Any],
    trusted_station_identity: bool,
    fields_that_would_change: list[str],
    conflicts: list[dict[str, Any]],
) -> None:
    local_type = normalise_station_type_label(local_station.get('station_type')) or 'Unknown'
    if local_type != proposed_type and 'station_type' not in fields_that_would_change:
        fields_that_would_change.append('station_type')

    edsm_distance = _read_float(edsm_station.get('distance_to_arrival'))
    if (
        trusted_station_identity
        and is_permanent_colony_slot_station_type(proposed_type)
        and edsm_distance is not None
        and _station_distance_metadata_update_needed(local_station, edsm_distance)
    ):
        fields_that_would_change.append('distance_from_star')

    proposed_body_name = _clean_text(body_proposal.get('body_name'))
    if (
        trusted_station_identity
        and body_proposal.get('association_status') == 'confirmed'
        and body_proposal.get('association_source') == 'edsm_body_name'
        and proposed_body_name is not None
        and (
            _normalise_name(local_station.get('body_name')) != _normalise_name(proposed_body_name)
            or not _has_trusted_station_metadata(local_station, prefix='body_name')
        )
    ):
        fields_that_would_change.append('body_name')

    _compare_economy(local_station, edsm_station, fields_that_would_change, conflicts)
    _compare_service_bool(local_station, edsm_station, 'has_market', 'have_market', fields_that_would_change, conflicts)
    _compare_service_bool(local_station, edsm_station, 'has_shipyard', 'have_shipyard', fields_that_would_change, conflicts)


def _preserve_confirmed_link(
    existing_link: Mapping[str, Any] | None,
    *,
    proposed: Mapping[str, Any],
    fields_that_would_change: list[str],
    conflicts: list[dict[str, Any]],
) -> None:
    if not existing_link or existing_link.get('association_status') != 'confirmed':
        return
    proposed_body_id = _read_int(proposed.get('body_id'))
    proposed_body_name = _clean_text(proposed.get('body_name'))
    existing_body_id = _read_int(existing_link.get('body_id'))
    existing_body_name = _clean_text(existing_link.get('body_name'))
    proposed_status = _clean_text(proposed.get('association_status'))
    proposed_lane = _clean_text(proposed.get('lane')) or 'unknown'
    existing_lane = _clean_text(existing_link.get('lane')) or 'unknown'
    same_body = (
        (proposed_body_id is not None and proposed_body_id == existing_body_id)
        or (
            proposed_body_name is not None
            and _normalise_name(proposed_body_name) == _normalise_name(existing_body_name)
        )
    )
    if proposed_status != 'confirmed' or not same_body or proposed_lane != existing_lane:
        conflicts.append({
            'field': 'station_body_links',
            'type': 'confirmed_link_preserved',
            'message': 'Existing confirmed station_body_links row is stronger than this EDSM proposal.',
            'existing': _public_existing_link(existing_link),
            'edsm_proposal': {
                'body_id': proposed_body_id,
                'body_name': proposed_body_name,
                'association_status': proposed_status,
                'association_confidence': proposed.get('association_confidence'),
                'association_source': proposed.get('association_source'),
                'lane': proposed_lane,
            },
        })
    while 'station_body_links' in fields_that_would_change:
        fields_that_would_change.remove('station_body_links')


def _compare_economy(
    local_station: Mapping[str, Any],
    edsm_station: Mapping[str, Any],
    fields_that_would_change: list[str],
    conflicts: list[dict[str, Any]],
) -> None:
    local = _normalise_economy(local_station.get('primary_economy'))
    edsm = _normalise_economy(edsm_station.get('economy'))
    if edsm == 'Unknown':
        return
    if local in ('Unknown', 'None'):
        fields_that_would_change.append('primary_economy')
    elif local != edsm:
        conflicts.append({
            'field': 'primary_economy',
            'type': 'station_economy_mismatch',
            'write_safety': 'identity_context_unsafe',
            'identity_context_unsafe': True,
            'local': local,
            'edsm': edsm,
            'message': 'EDSM station economy conflicts with known local station economy.',
        })


def _compare_service_bool(
    local_station: Mapping[str, Any],
    edsm_station: Mapping[str, Any],
    local_key: str,
    edsm_key: str,
    fields_that_would_change: list[str],
    conflicts: list[dict[str, Any]],
) -> None:
    edsm_value = edsm_station.get(edsm_key)
    if edsm_value is None:
        return
    local_value = local_station.get(local_key)
    if local_value is None:
        fields_that_would_change.append(local_key)
    elif bool(edsm_value) and not bool(local_value):
        fields_that_would_change.append(local_key)
    elif bool(local_value) != bool(edsm_value):
        conflicts.append({
            'field': local_key,
            'type': 'station_service_mismatch',
            'write_safety': 'diagnostic',
            'local': bool(local_value),
            'edsm': bool(edsm_value),
            'message': 'EDSM station service value conflicts with local service value; service fields are not applied by this stage.',
        })


def _has_blocking_conflict(conflicts: Sequence[Mapping[str, Any]]) -> bool:
    return bool(_non_benign_conflict_types_from_conflicts(conflicts))


def _proposal(
    *,
    station_type: str,
    body: Mapping[str, Any],
    body_name: str | None,
    lane: str,
    status: str,
    confidence: str,
    source: str,
    notes: str | None,
) -> dict[str, Any]:
    return {
        'station_type': station_type,
        'body_id': _read_int(body.get('id')) or _read_int(body.get('body_id')),
        'body_name': body_name,
        'lane': lane,
        'occupies_colony_slot': lane in ('orbital', 'surface'),
        'association_status': status,
        'association_confidence': confidence,
        'association_source': source,
        'resolver_notes': notes,
    }


def _unresolved_proposal(*, station_type: str, note: str | None, lane: str | None = None) -> dict[str, Any]:
    resolved_lane, lane_note = classify_station_lane(station_type)
    if lane is not None:
        resolved_lane = lane
    return {
        'station_type': station_type,
        'body_id': None,
        'body_name': None,
        'lane': resolved_lane,
        'occupies_colony_slot': False,
        'association_status': 'unresolved',
        'association_confidence': 'unresolved',
        'association_source': 'edsm_station_probe',
        'resolver_notes': _join_notes(note, lane_note),
    }


def _normalise_edsm_station(station: Mapping[str, Any]) -> dict[str, Any]:
    raw_type = _first_text(station.get('type'), station.get('stationType'), station.get('station_type'))
    station_type = normalise_station_type_label(raw_type) or 'Unknown'
    return {
        'id': _read_int(station.get('id')),
        'market_id': _read_int(_first_present(station.get('marketId'), station.get('market_id'))),
        'name': _clean_text(station.get('name')),
        'type_raw': raw_type,
        'station_type': station_type,
        'distance_to_arrival': _first_float(
            station.get('distanceToArrival'),
            station.get('distanceFromArrival'),
            station.get('distanceFromArrivalLS'),
            station.get('distance_from_star'),
        ),
        'body_id': _read_int(_first_present(station.get('bodyId'), station.get('body_id'), station.get('bodyID'))),
        'body_name': _body_name_from_record(station),
        'economy': _station_economy_from_record(station),
        'have_market': _first_bool(station.get('haveMarket'), station.get('hasMarket'), station.get('has_market')),
        'have_shipyard': _first_bool(station.get('haveShipyard'), station.get('hasShipyard'), station.get('has_shipyard')),
        'raw': dict(station),
    }


def _normalise_edsm_body(body: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'id': _read_int(body.get('id')),
        'body_id': _read_int(_first_present(body.get('bodyId'), body.get('body_id'), body.get('bodyID'))),
        'name': _first_text(body.get('name'), body.get('bodyName'), body.get('body_name')),
        'body_type': _first_text(body.get('type'), body.get('bodyType'), body.get('body_type')),
        'subtype': _first_text(body.get('subType'), body.get('subtype'), body.get('sub_type')),
        'distance_to_arrival': _first_float(
            body.get('distanceToArrival'),
            body.get('distanceFromArrival'),
            body.get('distanceFromArrivalLS'),
            body.get('distance_from_star'),
        ),
        'raw': dict(body),
    }


def _body_name_from_record(station: Mapping[str, Any]) -> str | None:
    return _first_text(
        station.get('bodyName'),
        station.get('body_name'),
        station.get('stationBodyName'),
        station.get('station_body_name'),
        station.get('body'),
    )


def _body_name_from_edsm_station_body_id(edsm_station: Mapping[str, Any], edsm_bodies: Sequence[Mapping[str, Any]]) -> str | None:
    station_body_id = _read_int(edsm_station.get('body_id'))
    if station_body_id is None:
        return None
    for body in edsm_bodies:
        body_ids = {_read_int(body.get('id')), _read_int(body.get('body_id'))}
        if station_body_id in body_ids:
            return _clean_text(body.get('name'))
    return None


def _unique_body_distance_candidates(
    station_distance: float,
    *,
    local_bodies: Sequence[Mapping[str, Any]],
    edsm_bodies: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    candidates: dict[int, Mapping[str, Any]] = {}
    for body in local_bodies:
        body_distance = _read_float(body.get('distance_from_star'))
        if body_distance is not None and abs(body_distance - station_distance) <= BODY_DISTANCE_MATCH_TOLERANCE_LS:
            body_id = _read_int(body.get('id'))
            if body_id is not None:
                candidates[body_id] = body

    for edsm_body in edsm_bodies:
        body_distance = _read_float(edsm_body.get('distance_to_arrival'))
        if body_distance is None or abs(body_distance - station_distance) > BODY_DISTANCE_MATCH_TOLERANCE_LS:
            continue
        local_matches = _local_bodies_by_name(local_bodies, _clean_text(edsm_body.get('name')))
        if len(local_matches) == 1:
            body_id = _read_int(local_matches[0].get('id'))
            if body_id is not None:
                candidates[body_id] = local_matches[0]
    return list(candidates.values())


def _local_bodies_by_name(local_bodies: Sequence[Mapping[str, Any]], body_name: str | None) -> list[Mapping[str, Any]]:
    normalised = _normalise_name(body_name)
    if not normalised:
        return []
    return [body for body in local_bodies if _normalise_name(body.get('name')) == normalised]


def _station_identity_values(station: Mapping[str, Any]) -> set[int]:
    return {
        value
        for value in (
            _read_int(station.get('id')),
            _read_int(station.get('market_id')),
            _read_int(station.get('marketId')),
        )
        if value is not None
    }


def _has_trusted_station_identity(station_match: Mapping[str, Any]) -> bool:
    return (
        station_match.get('status') == 'matched'
        and station_match.get('confidence') == 'exact'
        and station_match.get('source') == 'id_name'
    )


def _has_trusted_station_metadata(station: Mapping[str, Any], *, prefix: str) -> bool:
    return (
        _clean_text(station.get(f'{prefix}_source')) == TRUSTED_EDSM_SOURCE
        and _clean_text(station.get(f'{prefix}_confidence')) == TRUSTED_STATION_IDENTITY_CONFIDENCE
    )


def _station_distance_metadata_update_needed(local_station: Mapping[str, Any], edsm_distance: float) -> bool:
    """Decide whether ``stations.distance_from_star`` should be replanned.

    Population: if the local row has no distance, plan to populate it.
    Replace untrusted: if the local row's distance has weak provenance,
    plan to replace it with the EDSM exact value.
    Trust-and-hold: if the local row already has trusted EDSM/exact-station
    identity provenance, do **not** replan even when the live EDSM value
    differs. Orbital stations around bodies have natural distanceToArrival
    drift between EDSM refreshes; once we have an exact-identity write we
    must not churn the column on every nightly run.
    """
    local_distance = _read_float(local_station.get('distance_from_star'))
    if local_distance is None:
        return True
    if not _has_trusted_station_metadata(local_station, prefix='distance'):
        return True
    return False


def _extract_edsm_stations(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ('stations', 'Stations'):
            stations = payload.get(key)
            if isinstance(stations, list):
                return [item for item in stations if isinstance(item, Mapping)]
    return []


def _extract_edsm_bodies(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ('bodies', 'Bodies'):
            bodies = payload.get(key)
            if isinstance(bodies, list):
                return [item for item in bodies if isinstance(item, Mapping)]
    return []


def _public_local_station(station: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'id': _read_int(station.get('id')),
        'market_id': _read_int(station.get('market_id')),
        'system_id64': _read_int(station.get('system_id64')),
        'name': _clean_text(station.get('name')),
        'station_type': normalise_station_type_label(station.get('station_type')) or 'Unknown',
        'distance_from_star': _read_float(station.get('distance_from_star')),
        'distance_source': _clean_text(station.get('distance_source')),
        'distance_confidence': _clean_text(station.get('distance_confidence')),
        'body_name': _clean_text(station.get('body_name')),
        'body_name_source': _clean_text(station.get('body_name_source')),
        'body_name_confidence': _clean_text(station.get('body_name_confidence')),
        'station_type_source': _clean_text(station.get('station_type_source')),
        'station_type_confidence': _clean_text(station.get('station_type_confidence')),
        'primary_economy': _clean_text(station.get('primary_economy')),
        'has_market': station.get('has_market'),
        'has_shipyard': station.get('has_shipyard'),
    }


def _public_edsm_station(station: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'id': _read_int(station.get('id')),
        'market_id': _read_int(station.get('market_id')),
        'name': _clean_text(station.get('name')),
        'type_raw': _clean_text(station.get('type_raw')),
        'station_type': _clean_text(station.get('station_type')) or 'Unknown',
        'distance_to_arrival': _read_float(station.get('distance_to_arrival')),
        'body_id': _read_int(station.get('body_id')),
        'body_name': _clean_text(station.get('body_name')),
        'economy': _clean_text(station.get('economy')),
        'have_market': station.get('have_market'),
        'have_shipyard': station.get('have_shipyard'),
    }


def _public_existing_link(existing_link: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not existing_link:
        return None
    return {
        'station_id': _read_int(existing_link.get('station_id')),
        'market_id': _read_int(existing_link.get('market_id')),
        'body_id': _read_int(existing_link.get('body_id')),
        'body_name': _clean_text(existing_link.get('body_name')),
        'lane': _clean_text(existing_link.get('lane')),
        'association_status': _clean_text(existing_link.get('association_status')),
        'association_confidence': _clean_text(existing_link.get('association_confidence')),
        'association_source': _clean_text(existing_link.get('association_source')),
        'resolver_notes': _clean_text(existing_link.get('resolver_notes')),
    }


def _station_economy_from_record(station: Mapping[str, Any]) -> str | None:
    economy = _first_present(
        station.get('economy'),
        station.get('primaryEconomy'),
        station.get('primary_economy'),
    )
    if isinstance(economy, Mapping):
        return _first_text(economy.get('name'), economy.get('economy'), economy.get('value'))
    return _clean_text(economy)


def _normalise_economy(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return 'Unknown'
    token = text.replace(' ', '').replace('$economy_', '').replace(';', '').lower()
    mapping = {
        'hightech': 'HighTech',
        'high_tech': 'HighTech',
        'agriculture': 'Agriculture',
        'agri': 'Agriculture',
        'refinery': 'Refinery',
        'industrial': 'Industrial',
        'military': 'Military',
        'tourism': 'Tourism',
        'extraction': 'Extraction',
        'colony': 'Colony',
        'terraforming': 'Terraforming',
        'prison': 'Prison',
        'damaged': 'Damaged',
        'rescue': 'Rescue',
        'repair': 'Repair',
        'carrier': 'Carrier',
        'none': 'None',
        'unknown': 'Unknown',
    }
    normalised = mapping.get(token, text)
    return normalised if normalised in VALID_ECONOMIES else 'Unknown'


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, Mapping):
            value = _first_text(value.get('name'), value.get('bodyName'), value.get('body_name'))
        text = _clean_text(value)
        if text:
            return text
    return None


def _first_float(*values: Any) -> float | None:
    for value in values:
        parsed = _read_float(value)
        if parsed is not None:
            return parsed
    return None


def _first_bool(*values: Any) -> bool | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            token = value.strip().lower()
            if token in ('true', 'yes', '1'):
                return True
            if token in ('false', 'no', '0'):
                return False
    return None


def _read_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _read_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and isfinite(value):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return parsed if isfinite(parsed) else None
    return None


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _normalise_name(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    return ' '.join(value.strip().lower().split())


def _join_notes(*notes: str | None) -> str | None:
    clean = [note.strip() for note in notes if isinstance(note, str) and note.strip()]
    return ' '.join(clean) if clean else None


def _json_default(value: Any) -> str:
    return str(value)


def render_text_report(report: Mapping[str, Any]) -> str:
    lines = [
        f"EDSM station enrichment report: {report['system']['name']} ({report['system']['id64']})",
        f"network={'enabled' if report.get('network_enabled') else 'skipped'} source={report['source']}",
        f"apply_mode={report.get('apply_mode', 'dry_run')}",
        'counts:',
    ]
    for key, value in sorted(report['counts'].items()):
        lines.append(f'  {key}: {value}')
    lines.append('station_metadata_changes:')
    if not report.get('station_metadata_changes'):
        lines.append('  none')
    for change in report.get('station_metadata_changes', []):
        local = change['local_station']
        fields = ', '.join(change['fields_that_would_change'])
        evidence = change.get('station_type_evidence') or {}
        lines.append(
            f"  - {local['name']} [{local['id']}]: fields={fields} "
            f"type={evidence.get('local')}->{evidence.get('proposed')}"
        )
    lines.append('metadata_updates_planned:')
    if not report.get('metadata_updates_planned'):
        lines.append('  none')
    for update in report.get('metadata_updates_planned', []):
        local = update['local_station']
        lines.append(
            f"  - {local['name']} [{local['id']}]: "
            f"{update.get('field')}={update.get('old_value')}->{update.get('new_value')} "
            f"source={update.get('source')}/{update.get('confidence')}"
        )
    lines.append('metadata_updates_applied:')
    if not report.get('metadata_updates_applied'):
        lines.append('  none')
    for update in report.get('metadata_updates_applied', []):
        local = update['local_station']
        lines.append(
            f"  - {local['name']} [{local['id']}]: "
            f"{update.get('field')}={update.get('old_value')}->{update.get('new_value')}"
        )
    lines.append('confirmed_link_updates_applied:')
    if not report.get('confirmed_link_updates_applied'):
        lines.append('  none')
    for update in report.get('confirmed_link_updates_applied', []):
        local = update['local_station']
        link = update.get('applied_link') or update
        lines.append(
            f"  - {local['name']} [{local['id']}]: body={link.get('body_name')} lane={link.get('lane')}"
        )
    lines.append('association_changes:')
    if not report.get('association_changes'):
        lines.append('  none')
    for change in report.get('association_changes', []):
        local = change['local_station']
        proposed = change['proposed_link']
        lines.append(
            f"  - {local['name']} [{local['id']}]: body={proposed['body_name'] or 'unresolved'} "
            f"lane={proposed['lane']} assoc={proposed['association_status']}/{proposed['association_confidence']}"
        )
    lines.append('conflicts:')
    if not report.get('conflicts'):
        lines.append('  none')
    for entry in report.get('conflicts', []):
        local = entry['local_station']
        conflict = entry['conflict']
        lines.append(f"  - {local['name']} [{local['id']}]: {conflict.get('type')} field={conflict.get('field')}")
    lines.append('ignored_transient_non_slot:')
    if not report.get('ignored_transient_non_slot'):
        lines.append('  none')
    for entry in report.get('ignored_transient_non_slot', []):
        local = entry['local_station']
        evidence = entry.get('station_type_evidence') or {}
        body = entry.get('body_evidence') or {}
        lines.append(
            f"  - {local['name']} [{local['id']}]: type={evidence.get('local')}->{evidence.get('proposed')} "
            f"body_evidence={body.get('body_name') or body.get('body_id') or 'none'}"
        )
    lines.append('skipped:')
    if not report.get('skipped'):
        lines.append('  none')
    for entry in report.get('skipped', []):
        local = entry.get('local_station') or {}
        lines.append(f"  - {local.get('name')} [{local.get('id')}]: {entry.get('reason')}")
    lines.append('unresolved:')
    if not report.get('unresolved'):
        lines.append('  none')
    for entry in report.get('unresolved', []):
        local = entry.get('local_station') or {}
        lines.append(f"  - {local.get('name')} [{local.get('id')}]: {entry.get('reason')}")
    return '\n'.join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    apply_station_metadata = bool(args.apply_metadata or args.apply_station_metadata)
    apply_confirmed_links = bool(args.apply_confirmed_links)
    applying = apply_station_metadata or apply_confirmed_links
    if args.apply:
        print('--apply is not implemented for EDSM station enrichment; use --apply-metadata and/or --apply-confirmed-links for scoped writes.', file=sys.stderr)
        return 2
    if applying and args.dry_run:
        print('--dry-run and apply flags are mutually exclusive.', file=sys.stderr)
        return 2
    if applying and args.system_id64 is None:
        print('Apply flags require --system-id64 to keep writes scoped to one local system.', file=sys.stderr)
        return 2
    if applying and (args.no_network or args.local_only):
        print('Apply flags require network EDSM evidence; remove --no-network/--local-only.', file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print('--timeout must be greater than zero.', file=sys.stderr)
        return 2
    if args.edsm_retries < 0:
        print('--edsm-retries must be zero or greater.', file=sys.stderr)
        return 2
    if args.edsm_retry_backoff_seconds < 0:
        print('--edsm-retry-backoff-seconds must be zero or greater.', file=sys.stderr)
        return 2
    if args.edsm_request_delay_seconds < 0:
        print('--edsm-request-delay-seconds must be zero or greater.', file=sys.stderr)
        return 2
    if args.edsm_429_backoff_seconds < 0:
        print('--edsm-429-backoff-seconds must be zero or greater.', file=sys.stderr)
        return 2
    if args.edsm_429_backoff_multiplier < 1:
        print('--edsm-429-backoff-multiplier must be at least 1.', file=sys.stderr)
        return 2
    if not args.dsn:
        print('DATABASE_URL or --dsn is required.', file=sys.stderr)
        return 2

    try:
        with psycopg.connect(args.dsn) as conn:
            local = fetch_local_payload(conn, system_name=args.system_name, system_id64=args.system_id64)
            conn.rollback()
    except Exception as exc:
        print(f'Failed to load local system evidence: {exc}', file=sys.stderr)
        return 1

    system_name = args.system_name or _clean_text(local['system'].get('name'))
    if not system_name:
        print('System name is required for EDSM lookup.', file=sys.stderr)
        return 2

    network_enabled = not (args.no_network or args.local_only)
    if network_enabled:
        try:
            edsm_payload = fetch_edsm_system(
                system_name,
                timeout=args.timeout,
                retries=args.edsm_retries,
                retry_backoff_seconds=args.edsm_retry_backoff_seconds,
                request_delay_seconds=args.edsm_request_delay_seconds,
                rate_limit_backoff_seconds=args.edsm_429_backoff_seconds,
                rate_limit_backoff_multiplier=args.edsm_429_backoff_multiplier,
                logger=_stderr_log,
                system_id64=_read_int(local['system'].get('id64')),
            )
        except Exception as exc:
            print(f'Failed to fetch EDSM evidence: {exc}', file=sys.stderr)
            return 1
    else:
        edsm_payload = {'stations': {'stations': []}, 'bodies': {'bodies': []}}

    report = build_enrichment_report(
        local_system=local['system'],
        local_stations=local['stations'],
        local_bodies=local['bodies'],
        existing_links=local['existing_links'],
        edsm_stations_payload=edsm_payload['stations'],
        edsm_bodies_payload=edsm_payload['bodies'],
        network_enabled=network_enabled,
    )

    if applying:
        try:
            with psycopg.connect(args.dsn) as conn:
                if apply_station_metadata:
                    applied, apply_skipped = apply_metadata_updates(conn, report)
                    apply_metadata_result(report, applied, apply_skipped)
                if apply_confirmed_links:
                    applied_links, link_skipped = apply_confirmed_link_updates(conn, report)
                    apply_confirmed_links_result(report, applied_links, link_skipped)
                conn.commit()
        except Exception as exc:
            print(f'Failed to apply EDSM station enrichment updates: {exc}', file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=_json_default))
    else:
        print(render_text_report(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
