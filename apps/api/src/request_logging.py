"""What is kept out of the logs and the error reports.

Two kinds of address carry something that must not be written down:

- Frontier's sign-in sends its one-time code in the query of the callback
  address, so the whole query is left off;
- a watchlist address carries the browser's own key as part of its path, so
  the key is replaced.

The web server hides the same two in its own log
(apps/web/nginx/default.conf.template).
"""
from __future__ import annotations

import logging
import re
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


# A watchlist address: /api/v2/watchlist/<the browser's key>[/more].
WATCHLIST_KEY_IN_PATH = re.compile(r'^(/api/v2/watchlist/)[^/]+')
HIDDEN = '(hidden)'


def redact_watchlist_key(target: str) -> str:
    """Replace the browser's key in a watchlist address; leave any other alone."""
    parsed = urlsplit(target)
    path = WATCHLIST_KEY_IN_PATH.sub(lambda found: found.group(1) + HIDDEN, parsed.path)
    if path == parsed.path:
        return target
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def redact_request_target(target: str) -> str:
    """What may be written down in place of the address a request asked for."""
    return redact_watchlist_key(redact_frontier_oauth_target(target))


class FrontierOAuthAccessLogFilter(logging.Filter):
    """Redact the path-with-query argument emitted by Uvicorn access logging."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            redacted_target = redact_request_target(args[2])
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
    if not isinstance(raw_url, str):
        return event
    if not is_frontier_oauth_callback(raw_url):
        # Any other address keeps its query and cookies as before; only a
        # watchlist key is replaced.
        request['url'] = redact_watchlist_key(raw_url)
        env = request.get('env')
        if isinstance(env, dict):
            for name in ('RAW_URI', 'REQUEST_URI', 'PATH_INFO'):
                value = env.get(name)
                if isinstance(value, str):
                    env[name] = redact_watchlist_key(value)
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
