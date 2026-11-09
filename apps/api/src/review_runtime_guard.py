from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit


REVIEW_STACK_MARKER_ENV = 'ED_FINDER_REVIEW_STACK_MARKER'
EXPECTED_REVIEW_STACK_MARKER = 'edfinder-review'
EXPECTED_REVIEW_DATABASE_HOST = 'review-postgres'
EXPECTED_REVIEW_DATABASE_NAME = 'edfinder_local_review'
EXPECTED_REVIEW_REDIS_HOST = 'review-redis'


class ReviewRuntimeGuardError(RuntimeError):
    """Raised when review-only runtime wiring is activated on an unsafe target."""


@dataclass(frozen=True)
class ReviewRuntimeTarget:
    marker: str
    database_host: str
    database_name: str
    redis_host: str


def validate_review_runtime_env(env: Mapping[str, str]) -> ReviewRuntimeTarget:
    marker = (env.get(REVIEW_STACK_MARKER_ENV) or '').strip()
    if marker != EXPECTED_REVIEW_STACK_MARKER:
        raise ReviewRuntimeGuardError(
            f'review stack marker must be {EXPECTED_REVIEW_STACK_MARKER!r}'
        )

    database_url = (env.get('DATABASE_URL') or '').strip()
    if not database_url:
        raise ReviewRuntimeGuardError('DATABASE_URL is required for review runtime')
    database_target = urlsplit(database_url)
    database_host = (database_target.hostname or '').strip()
    database_name = database_target.path.lstrip('/')
    if database_host != EXPECTED_REVIEW_DATABASE_HOST:
        raise ReviewRuntimeGuardError(
            f'review runtime requires database host {EXPECTED_REVIEW_DATABASE_HOST!r}'
        )
    if database_name != EXPECTED_REVIEW_DATABASE_NAME:
        raise ReviewRuntimeGuardError(
            f'review runtime requires database name {EXPECTED_REVIEW_DATABASE_NAME!r}'
        )

    redis_url = (env.get('REDIS_URL') or '').strip()
    if not redis_url:
        raise ReviewRuntimeGuardError('REDIS_URL is required for review runtime')
    redis_target = urlsplit(redis_url)
    redis_host = (redis_target.hostname or '').strip()
    if redis_host != EXPECTED_REVIEW_REDIS_HOST:
        raise ReviewRuntimeGuardError(
            f'review runtime requires redis host {EXPECTED_REVIEW_REDIS_HOST!r}'
        )

    return ReviewRuntimeTarget(
        marker=marker,
        database_host=database_host,
        database_name=database_name,
        redis_host=redis_host,
    )
