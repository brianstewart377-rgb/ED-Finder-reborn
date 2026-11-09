"""EDDN live feed: Server-Sent Events endpoint + Redis pub/sub bridge.

The EDDN listener container publishes events on the Redis `eddn_events`
channel. Each API worker process runs one instance of
`eddn_pubsub_bridge()` (started by the lifespan) which subscribes once
and broadcasts every event to all locally-connected SSE clients.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from edfinder_api.deps import connect_redis, get_pool
from edfinder_api.state import active_jobs, sse_clients, get_redis_singleton, set_redis

log = logging.getLogger('ed_finder')

router = APIRouter(tags=['events'])

EDDN_PUBSUB_CHANNEL = 'eddn_events'

# Reconnect backoff bounds (seconds). Shared by "Redis down at boot" and
# "Redis dropped mid-stream" — both are the same recovery problem.
BRIDGE_BACKOFF_INITIAL_SECONDS = 1.0
BRIDGE_BACKOFF_MAX_SECONDS = 60.0

# Bridge state, exported as a Prometheus gauge from /api/metrics. Numeric
# codes are stable: dashboards/alerts key on them.
BRIDGE_STATE_NOT_STARTED = 0   # lifespan has not created the task
BRIDGE_STATE_CONNECTING = 1    # no Redis client yet; retrying with backoff
BRIDGE_STATE_SUBSCRIBED = 2    # subscribed and listening
BRIDGE_STATE_RETRYING = 3      # had a subscription, lost it, backing off
BRIDGE_STATE_STOPPED = 4       # cancelled by lifespan shutdown

_bridge_state: int = BRIDGE_STATE_NOT_STARTED
_bridge_reconnects_total: int = 0


def bridge_state() -> int:
    """Current EDDN→SSE bridge state code (see BRIDGE_STATE_*)."""
    return _bridge_state


def bridge_reconnects_total() -> int:
    """Count of connect/subscribe attempts that followed a failure."""
    return _bridge_reconnects_total


def _set_bridge_state(state: int) -> None:
    global _bridge_state
    _bridge_state = state


async def _redis_or_connect() -> object | None:
    """Return the process Redis client, connecting it if boot could not.

    When Redis was unreachable at boot the lifespan installs no client; the
    bridge then owns the retry and, on success, installs the client so the
    cache helpers recover as well. Returns None when the connect fails
    (the caller backs off). Never logs the connection URL.
    """
    r = get_redis_singleton()
    if r is not None:
        return r
    try:
        r = await connect_redis()
    except Exception as e:
        log.warning('EDDN pub/sub bridge: Redis still unavailable (%s)', type(e).__name__)
        return None
    set_redis(r)
    log.info('EDDN pub/sub bridge: Redis connected after boot-time failure ✓')
    return r


# ---------------------------------------------------------------------------
# Background worker — subscribes to Redis pub/sub and fans out to queues.
# Started by the lifespan in main.py at app start-up.
# ---------------------------------------------------------------------------
async def eddn_pubsub_bridge() -> None:
    """Subscribe to Redis EDDN channel and fan out to local SSE queues.

    Audit fix (2026-05-08, AUDIT_REPORT.md §H2): wrap the inner
    `pubsub.listen()` loop in a backoff-reconnect outer loop. Previously
    a single Redis blip (restart, network partition, OOM-kill) would kill
    the bridge for the lifetime of the API container — front-ends
    silently stopped receiving live events with no recovery.

    Silent-failure fix (2026-10-19): the bridge no longer returns when
    Redis is absent at boot. It retries the connection itself with the
    same capped exponential backoff, so a Redis that comes up a minute
    after the API does is picked up without a restart, and the state is
    exported as `ed_finder_sse_bridge_state` so "never connected" is
    visible rather than indistinguishable from "quiet feed".
    """
    global _bridge_reconnects_total
    backoff = BRIDGE_BACKOFF_INITIAL_SECONDS
    _set_bridge_state(BRIDGE_STATE_CONNECTING)
    while True:
        pubsub = None
        try:
            r = await _redis_or_connect()
            if r is None:
                _set_bridge_state(BRIDGE_STATE_CONNECTING)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, BRIDGE_BACKOFF_MAX_SECONDS)
                _bridge_reconnects_total += 1
                continue
            pubsub = r.pubsub()
            await pubsub.subscribe(EDDN_PUBSUB_CHANNEL)
            backoff = BRIDGE_BACKOFF_INITIAL_SECONDS  # reset on successful subscribe
            _set_bridge_state(BRIDGE_STATE_SUBSCRIBED)
            async for message in pubsub.listen():
                if message.get('type') != 'message':
                    continue
                data = message.get('data')
                if not data:
                    continue
                try:
                    event = json.loads(data) if isinstance(data, str) else data
                except (ValueError, TypeError):
                    continue
                _broadcast(event)
        except asyncio.CancelledError:
            _set_bridge_state(BRIDGE_STATE_STOPPED)
            try:
                if pubsub is not None:
                    await pubsub.unsubscribe(EDDN_PUBSUB_CHANNEL)
                    await pubsub.aclose()
            except Exception:
                pass
            raise
        except Exception as e:
            _set_bridge_state(BRIDGE_STATE_RETRYING)
            log.error('EDDN pub/sub bridge error (will reconnect in %.1fs): %s', backoff, e)
            try:
                if pubsub is not None:
                    await pubsub.aclose()
            except Exception:
                pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, BRIDGE_BACKOFF_MAX_SECONDS)
            _bridge_reconnects_total += 1
            continue


def _broadcast(event: dict) -> None:
    """Fan event out to every connected SSE client on this worker.

    On QueueFull (slow consumer) we drop the *oldest* event and put the
    new one — the connection stays subscribed. Previous behaviour was to
    detach the whole queue from `sse_clients`, which combined with the
    25 s heartbeat sleep below caused permanent silence after the first
    burst at high event rates (>10/s). See git blame for the audit-era
    incident write-up.
    """
    for q in list(sse_clients):  # snapshot — list can mutate under us
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Drop oldest event, keep the queue subscribed.
            try:
                q.get_nowait()
                q.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------
@router.get('/api/events/live')
async def live_events(request: Request):
    # 200-event buffer (was 50). At ~19 events/sec a 50-deep buffer
    # filled in <3s during a busy spell, which combined with the old
    # 25 s heartbeat sleep stalled clients for 25-second stretches.
    # 200 gives ~10s of headroom and is still tiny in memory terms.
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    sse_clients.append(queue)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # Block until an event arrives OR the heartbeat
                    # timeout fires. Crucially, this does NOT busy-poll
                    # and does NOT sleep through bursts — events are
                    # delivered the moment they're broadcast.
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except asyncio.TimeoutError:
                    # No events for 15 s → send a heartbeat to keep
                    # nginx / Cloudflare / browser-side EventSource
                    # connections alive. Loops back immediately to
                    # wait for the next event.
                    yield ": heartbeat\n\n"
        finally:
            try:
                sse_clients.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type='text/event-stream',
        headers={
            'Cache-Control':      'no-cache',
            'X-Accel-Buffering':  'no',
        },
    )


@router.get('/api/events/recent')
async def recent_events(
    limit: int = 50,
    pool:  asyncpg.Pool = Depends(get_pool),
):
    # LIMIT 0 is valid (returns no rows); the lower clamp only guards against a
    # negative LIMIT, which Postgres rejects outright (would 500 this handler).
    limit = max(0, min(limit, 200))
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT system_name, system_id64, event_type, received_at
              FROM eddn_log
             ORDER BY received_at DESC
             LIMIT $1
        """, limit)
    return {
        'events': [
            {
                'system_name': r['system_name'],
                'id64':        r['system_id64'],
                'type':        r['event_type'],
                'timestamp':   r['received_at'].isoformat() if r['received_at'] else None,
            }
            for r in rows
        ],
        'jobs': active_jobs,
    }
