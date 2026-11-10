"""Fail-closed helpers for local/disposable database tests.

The helpers in this module are test-only. They intentionally reject
production-looking targets, avoid secret output, and keep host Postgres on
5432 out of the default path unless CI or an explicit local opt-in says the
target is disposable.
"""
from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, NoReturn
from urllib.parse import quote, urlsplit, urlunsplit


DEFAULT_TEST_DB_HOST = '127.0.0.1'
DEFAULT_TEST_DB_PORT = '55432'
DEFAULT_TEST_DB_NAME = 'edfinder'
DEFAULT_TEST_DB_USER = 'edfinder'
DEFAULT_TEST_DB_PASSWORD = 'edfinder'

ALLOWED_TEST_HOSTS = {'localhost', '127.0.0.1', '::1', 'postgres'}
PRODUCTION_MARKERS = (
    'prod',
    'production',
    'live',
    'hetzner',
    'ed-finder.app',
    'edfinder.app',
)
HOST_5432_OPT_IN_ENV = 'EDFINDER_ALLOW_HOST_5432_TEST_DB'
DESTRUCTIVE_RESET_OPT_IN_ENV = 'EDFINDER_TEST_DB_ALLOW_DESTRUCTIVE_RESET'
# Set to "1" by the CI jobs that provide a database service. Database-backed
# tests then FAIL instead of skipping when the database is unreachable,
# unsafe, unconfigured, or a driver is missing, so a DB lane can no longer go
# green with zero tests run. Skips about the *state* of a reachable database
# (V2-only schema, no published generation, missing seed rows) are lane
# conditions, not availability failures, and are not routed through this gate.
DB_TESTS_REQUIRED_ENV = 'EDF_DB_TESTS_REQUIRED'

_SAFE_SCHEMA_RE = re.compile(r'^[a-z][a-z0-9_]{0,62}$')


class DbIsolationError(RuntimeError):
    """Raised when a test database target is unsafe or ambiguous."""


@dataclass(frozen=True)
class DbTarget:
    dsn: str
    redacted_dsn: str
    host: str
    port: str
    database: str
    user: str | None
    password_present: bool
    host_postgres_5432_targeted: bool
    host_5432_allowed: bool
    source: str

    def safe_summary(self) -> dict[str, object]:
        return {
            'dsn': self.redacted_dsn,
            'host': self.host,
            'port': self.port,
            'database': self.database,
            'user': self.user,
            'password_present': self.password_present,
            'host_postgres_5432_targeted': self.host_postgres_5432_targeted,
            'host_5432_allowed': self.host_5432_allowed,
            'source': self.source,
        }


def default_database_url() -> str:
    return (
        f'postgresql://{DEFAULT_TEST_DB_USER}:{DEFAULT_TEST_DB_PASSWORD}'
        f'@{DEFAULT_TEST_DB_HOST}:{DEFAULT_TEST_DB_PORT}/{DEFAULT_TEST_DB_NAME}'
    )


def default_target(env: Mapping[str, str] | None = None) -> DbTarget:
    source_env = {} if env is None else env
    return validate_test_db_target(default_database_url(), env=source_env, source='default_test_db')


def target_from_env(
    env: Mapping[str, str] | None = None,
    *,
    dsn_env: str = 'DATABASE_URL',
    source: str | None = None,
) -> DbTarget:
    source_env = os.environ if env is None else env
    if source_env.get(dsn_env):
        return validate_test_db_target(source_env[dsn_env], env=source_env, source=source or dsn_env)
    return target_from_pg_env(source_env, source=source or 'pg_env')


def target_from_pg_env(env: Mapping[str, str] | None = None, *, source: str = 'pg_env') -> DbTarget:
    source_env = os.environ if env is None else env
    host = _first_text(source_env.get('PGHOST'), DEFAULT_TEST_DB_HOST)
    port = _first_text(source_env.get('PGPORT'), DEFAULT_TEST_DB_PORT)
    database = _first_text(source_env.get('PGDATABASE'), DEFAULT_TEST_DB_NAME)
    user = _first_text(source_env.get('PGUSER'), DEFAULT_TEST_DB_USER)
    password = _first_text(
        source_env.get('PGPASSWORD'),
        source_env.get('POSTGRES_PASSWORD'),
        DEFAULT_TEST_DB_PASSWORD,
    )
    dsn = f'postgresql://{quote(user)}:{quote(password)}@{host}:{port}/{database}'
    return validate_test_db_target(dsn, env=source_env, source=source)


def db_tests_required(env: Mapping[str, str] | None = None) -> bool:
    source_env = os.environ if env is None else env
    return source_env.get(DB_TESTS_REQUIRED_ENV) == '1'


def database_unavailable(
    reason: str,
    *,
    allow_module_level: bool = False,
    env: Mapping[str, str] | None = None,
) -> NoReturn:
    """Skip a database-backed test, or fail it when ``EDF_DB_TESTS_REQUIRED=1``.

    Every "the disposable database is not usable" exit (unreachable server,
    unsafe/unconfigured target, missing driver or client tool) must go through
    here instead of a bare ``pytest.skip`` so that a lane which promised a
    database cannot pass while the tests it exists for never ran.
    """
    import pytest

    if db_tests_required(env):
        pytest.fail(
            f'{DB_TESTS_REQUIRED_ENV}=1 but a database-backed test could not run: {reason}',
            pytrace=False,
        )
    pytest.skip(reason, allow_module_level=allow_module_level)


def import_db_module(name: str, *, env: Mapping[str, str] | None = None) -> Any:
    """``pytest.importorskip`` for database drivers, honouring the required gate."""
    import importlib

    try:
        return importlib.import_module(name)
    except ImportError as exc:
        database_unavailable(f'{name} is not importable: {exc}', env=env)


def confirmed_target_or_skip(
    *,
    dsn_env: str,
    confirm_env: str,
    purpose: str,
    env: Mapping[str, str] | None = None,
) -> DbTarget:
    import pytest

    source_env = os.environ if env is None else env
    dsn = source_env.get(dsn_env)
    confirmed = source_env.get(confirm_env) == 'yes'
    if not dsn or not confirmed:
        database_unavailable(
            f'{purpose} requires {dsn_env} and {confirm_env}=yes',
            allow_module_level=True,
            env=source_env,
        )
    try:
        return validate_test_db_target(dsn, env=source_env, source=dsn_env)
    except DbIsolationError as exc:
        pytest.fail(str(exc))


def validate_test_db_target(
    dsn: str,
    *,
    env: Mapping[str, str] | None = None,
    source: str = 'dsn',
) -> DbTarget:
    source_env = os.environ if env is None else env
    parsed = parse_dsn(dsn)
    host = _first_text(parsed.get('host'), '')
    port = _first_text(parsed.get('port'), DEFAULT_TEST_DB_PORT)
    database = _first_text(parsed.get('database'), '')
    user = parsed.get('user')
    password_present = bool(parsed.get('password_present'))

    if not host:
        raise DbIsolationError('test DB target has no host')
    if host not in ALLOWED_TEST_HOSTS:
        raise DbIsolationError(f'unsafe test DB host {host!r}; expected local/disposable host')
    if not database:
        raise DbIsolationError('test DB target has no database name')
    if _contains_production_marker(' '.join((dsn, host, database, str(user or '')))):
        raise DbIsolationError('unsafe test DB target contains production-like marker')
    if not password_present:
        raise DbIsolationError('test DB target must include credentials without printing them')

    host_5432 = host in {'localhost', '127.0.0.1', '::1'} and str(port) == '5432'
    host_5432_allowed = host_5432_is_allowed(source_env)
    if host_5432 and not host_5432_allowed:
        raise DbIsolationError(
            'localhost:5432 is not a default-safe test DB target; use the isolated '
            f'{DEFAULT_TEST_DB_PORT} port or set {HOST_5432_OPT_IN_ENV}=yes for a disposable DB'
        )

    return DbTarget(
        dsn=dsn,
        redacted_dsn=redact_dsn(dsn),
        host=host,
        port=str(port),
        database=database,
        user=str(user) if user is not None else None,
        password_present=password_present,
        host_postgres_5432_targeted=host_5432,
        host_5432_allowed=host_5432_allowed,
        source=source,
    )


def parse_dsn(dsn: str) -> dict[str, object]:
    if '://' in dsn:
        parsed = urlsplit(dsn)
        return {
            'host': parsed.hostname,
            'port': str(parsed.port) if parsed.port is not None else None,
            'database': parsed.path.lstrip('/') or None,
            'user': parsed.username,
            'password_present': bool(parsed.password),
        }
    parts = dict(part.split('=', 1) for part in dsn.split() if '=' in part)
    return {
        'host': parts.get('host'),
        'port': parts.get('port'),
        'database': parts.get('dbname') or parts.get('database'),
        'user': parts.get('user'),
        'password_present': bool(parts.get('password')),
    }


def redact_dsn(dsn: str) -> str:
    if '://' in dsn:
        parsed = urlsplit(dsn)
        netloc = parsed.netloc
        if parsed.password is not None:
            user = quote(parsed.username or '', safe='')
            host = parsed.hostname or ''
            if ':' in host and not host.startswith('['):
                host = f'[{host}]'
            port = f':{parsed.port}' if parsed.port is not None else ''
            netloc = f'{user}:***@{host}{port}'
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    redacted_parts: list[str] = []
    for part in dsn.split():
        if part.lower().startswith('password='):
            redacted_parts.append('password=***')
        else:
            redacted_parts.append(part)
    return ' '.join(redacted_parts)


def require_destructive_reset_opt_in(env: Mapping[str, str] | None = None) -> None:
    source_env = os.environ if env is None else env
    if source_env.get(DESTRUCTIVE_RESET_OPT_IN_ENV) == 'yes':
        return
    if source_env.get('CI') == 'true':
        return
    raise DbIsolationError(f'destructive reset requires {DESTRUCTIVE_RESET_OPT_IN_ENV}=yes')


def safe_schema_name(prefix: str, token: str | None = None) -> str:
    normalized = re.sub(r'[^a-z0-9_]+', '_', prefix.lower()).strip('_')
    if not normalized or not normalized[0].isalpha():
        normalized = f't_{normalized}'
    suffix = token or uuid.uuid4().hex[:12]
    schema = f'{normalized}_{suffix}'
    schema = schema[:63].rstrip('_')
    if not _SAFE_SCHEMA_RE.match(schema):
        raise DbIsolationError(f'unsafe generated schema name {schema!r}')
    return schema


def validate_schema_name(schema: str) -> str:
    if not _SAFE_SCHEMA_RE.match(schema):
        raise DbIsolationError(f'unsafe schema name {schema!r}')
    return schema


@contextmanager
def rollback_transaction(
    connect: Callable[[str], Any],
    target: DbTarget | str,
    *,
    readonly: bool = False,
) -> Iterator[Any]:
    dsn = target.dsn if isinstance(target, DbTarget) else target
    conn = connect(dsn)
    try:
        conn.read_only = readonly
        conn.autocommit = False
        yield conn
    finally:
        rollback = getattr(conn, 'rollback', None)
        if callable(rollback):
            rollback()
        close = getattr(conn, 'close', None)
        if callable(close):
            close()


def host_5432_is_allowed(env: Mapping[str, str] | None = None) -> bool:
    source_env = os.environ if env is None else env
    return source_env.get(HOST_5432_OPT_IN_ENV) == 'yes' or source_env.get('CI') == 'true'


def _contains_production_marker(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PRODUCTION_MARKERS)


def _first_text(*values: object) -> str:
    for value in values:
        if value is not None and str(value):
            return str(value)
    return ''
