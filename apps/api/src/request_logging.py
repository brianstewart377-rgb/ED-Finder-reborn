"""Narrow redaction helpers for Frontier OAuth callback query secrets."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit


FRONTIER_OAUTH_CALLBACK_PATHS = frozenset({
    '/api/auth/frontier/callback',
    '/api/v1/auth/frontier/callback',
})


def is_frontier_oauth_callback(target: str) -> bool:
    """Return whether *target* names either supported Frontier callback path."""
    return urlsplit(target).path in FRONTIER_OAUTH_CALLBACK_PATHS


def redact_frontier_oauth_target(target: str) -> str:
    """Remove the complete query/fragment from a Frontier callback target."""
    parsed = urlsplit(target)
    if parsed.path not in FRONTIER_OAUTH_CALLBACK_PATHS:
        return target
    if parsed.scheme or parsed.netloc:
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
    return parsed.path


class FrontierOAuthAccessLogFilter(logging.Filter):
    """Redact the path-with-query argument emitted by Uvicorn access logging."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            redacted_target = redact_frontier_oauth_target(args[2])
            if redacted_target != args[2]:
                redacted_args = list(args)
                redacted_args[2] = redacted_target
                record.args = tuple(redacted_args)
        return True


def configure_uvicorn_access_log_redaction() -> None:
    """Install the callback filter once on Uvicorn's dedicated access logger."""
    logger = logging.getLogger('uvicorn.access')
    if not any(isinstance(item, FrontierOAuthAccessLogFilter) for item in logger.filters):
        logger.addFilter(FrontierOAuthAccessLogFilter())


def redact_frontier_oauth_sentry_event(
    event: dict[str, Any],
    _hint: dict[str, Any],
) -> dict[str, Any]:
    """Strip callback query/cookie material before a Sentry-compatible send."""
    request = event.get('request')
    if not isinstance(request, dict):
        return event

    raw_url = request.get('url')
    if not isinstance(raw_url, str) or not is_frontier_oauth_callback(raw_url):
        return event

    request['url'] = redact_frontier_oauth_target(raw_url)
    request.pop('query_string', None)
    request.pop('cookies', None)

    headers = request.get('headers')
    if isinstance(headers, dict):
        for name in tuple(headers):
            if str(name).casefold() == 'cookie':
                headers.pop(name, None)

    env = request.get('env')
    if isinstance(env, dict):
        env.pop('QUERY_STRING', None)
        for name in ('RAW_URI', 'REQUEST_URI'):
            value = env.get(name)
            if isinstance(value, str):
                env[name] = redact_frontier_oauth_target(value)

    return event
