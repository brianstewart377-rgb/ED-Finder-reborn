"""Worker configuration from the environment, with bounded validation.

An invalid value stops the worker before it connects anywhere. Error text
names the variable, never the value (the database URL carries credentials).
"""
from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

DEFAULT_RELAY = 'tcp://eddn.edcd.io:9500'
_RELAY = re.compile(r'^tcp://[A-Za-z0-9.-]{1,253}:(?P<port>\d{1,5})$')
_LOG_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR')


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    database_url: str
    relay_url: str = DEFAULT_RELAY
    flush_interval_seconds: float = 15.0
    flush_max_rows: int = 500
    buffer_max_rows: int = 5000
    max_consecutive_flush_failures: int = 5
    max_message_bytes: int = 262_144
    recv_idle_reconnect_seconds: float = 120.0
    reconnect_backoff_max_seconds: float = 60.0
    max_event_age: timedelta = timedelta(days=1)
    max_clock_skew: timedelta = timedelta(minutes=5)
    metrics_host: str = '127.0.0.1'
    metrics_port: int = 9465
    health_stale_seconds: float = 300.0
    watermark_max_stations: int = 200_000
    log_level: str = 'INFO'

    def __repr__(self) -> str:  # never print the credential-bearing URL
        return f'WorkerConfig(relay_url={self.relay_url!r}, database_url=<redacted>, ...)'


def _int(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = env.get(name)
    if raw is None or raw == '':
        return default
    try:
        value = int(raw.strip(), 10)
    except ValueError as exc:
        raise ConfigError(f'{name} must be an integer') from exc
    if not low <= value <= high:
        raise ConfigError(f'{name} must be between {low} and {high}')
    return value


def load_config(env: Mapping[str, str] | None = None) -> WorkerConfig:
    env = os.environ if env is None else env
    database_url = env.get('EDDN_V3_DATABASE_URL', '')
    if not database_url.startswith(('postgresql://', 'postgres://')) or len(database_url) > 2048:
        raise ConfigError('EDDN_V3_DATABASE_URL must be a postgresql:// URL')

    relay_url = env.get('EDDN_V3_RELAY_URL') or DEFAULT_RELAY
    match = _RELAY.match(relay_url)
    if not match or not 1 <= int(match.group('port')) <= 65535:
        raise ConfigError('EDDN_V3_RELAY_URL must look like tcp://host:port')

    flush_max_rows = _int(env, 'EDDN_V3_FLUSH_MAX_ROWS', 500, 1, 5000)
    buffer_max_rows = _int(env, 'EDDN_V3_BUFFER_MAX_ROWS', 5000, 100, 50_000)
    if buffer_max_rows < flush_max_rows:
        raise ConfigError('EDDN_V3_BUFFER_MAX_ROWS must be >= EDDN_V3_FLUSH_MAX_ROWS')

    metrics_host = env.get('EDDN_V3_METRICS_HOST') or '127.0.0.1'
    try:
        # The stdlib HTTP server binds AF_INET; IPv6 literals are refused here
        # rather than failing at startup.
        ipaddress.IPv4Address(metrics_host)
    except ValueError as exc:
        raise ConfigError('EDDN_V3_METRICS_HOST must be an IPv4 address literal') from exc

    log_level = (env.get('EDDN_V3_LOG_LEVEL') or 'INFO').upper()
    if log_level not in _LOG_LEVELS:
        raise ConfigError(f'EDDN_V3_LOG_LEVEL must be one of {", ".join(_LOG_LEVELS)}')

    return WorkerConfig(
        database_url=database_url,
        relay_url=relay_url,
        flush_interval_seconds=float(_int(env, 'EDDN_V3_FLUSH_INTERVAL_SECONDS', 15, 1, 300)),
        flush_max_rows=flush_max_rows,
        buffer_max_rows=buffer_max_rows,
        max_consecutive_flush_failures=_int(
            env, 'EDDN_V3_MAX_CONSECUTIVE_FLUSH_FAILURES', 5, 1, 1000),
        max_message_bytes=_int(env, 'EDDN_V3_MAX_MESSAGE_BYTES', 262_144, 1024, 1_048_576),
        recv_idle_reconnect_seconds=float(
            _int(env, 'EDDN_V3_RECV_IDLE_RECONNECT_SECONDS', 120, 10, 3600)),
        reconnect_backoff_max_seconds=float(
            _int(env, 'EDDN_V3_RECONNECT_BACKOFF_MAX_SECONDS', 60, 1, 600)),
        max_event_age=timedelta(seconds=_int(
            env, 'EDDN_V3_MAX_EVENT_AGE_SECONDS', 86_400, 60, 7 * 86_400)),
        max_clock_skew=timedelta(seconds=_int(env, 'EDDN_V3_MAX_CLOCK_SKEW_SECONDS', 300, 0, 3600)),
        metrics_host=metrics_host,
        metrics_port=_int(env, 'EDDN_V3_METRICS_PORT', 9465, 1024, 65535),
        health_stale_seconds=float(_int(env, 'EDDN_V3_HEALTH_STALE_SECONDS', 300, 30, 86_400)),
        watermark_max_stations=_int(
            env, 'EDDN_V3_WATERMARK_MAX_STATIONS', 200_000, 1_000, 2_000_000),
        log_level=log_level,
    )
