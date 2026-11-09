"""Enforce defaults after FastAPI routing without wrapping response streams."""

from math import ceil
import time

from fastapi import Request, Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from edfinder_api.config import limiter, trusted_client_address


DEFAULT_RATE_LIMIT_EXEMPT_PATHS = frozenset({'/api/health', '/api/metrics'})


async def default_rate_limit(request: Request) -> None:
    """Apply the configured default once per client, method and path template.

    FastAPI supplies the matched APIRoute here, including for included routers.
    Slowapi's middleware instead walks app.routes, which can resolve an included
    route to the SPA fallback. A dependency also leaves SSE streaming alone.
    """
    if not limiter.enabled:
        return

    route = request.scope['route']
    if route.path in DEFAULT_RATE_LIMIT_EXEMPT_PATHS:
        return

    endpoint = route.endpoint
    endpoint_name = f'{endpoint.__module__}.{endpoint.__name__}'
    if endpoint_name in limiter._route_limits or endpoint_name in limiter._dynamic_route_limits:
        # The existing decorator owns its limit and key (including per-user keys).
        return

    args = [trusted_client_address(request), f'{request.method} {route.path}']
    # Reuse the defaults built from settings.rate_limit_default, and the same
    # in-memory moving-window backend as decorators. No second store to reset.
    for group in limiter._default_limits:
        for limit in group:
            request.state.view_rate_limit = (limit.limit, args)
            if not limiter.limiter.hit(limit.limit, *args):
                raise RateLimitExceeded(limit)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Keep slowapi's 429 body and add a retry delay for either enforcement path."""
    response = _rate_limit_exceeded_handler(request, exc)
    limit, args = request.state.view_rate_limit
    reset_at, _remaining = limiter.limiter.get_window_stats(limit, *args)
    response.headers['Retry-After'] = str(max(1, ceil(reset_at - time.time())))
    return response
