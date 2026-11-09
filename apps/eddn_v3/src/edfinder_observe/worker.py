"""The EDDN relay loop: subscribe, filter Docked, buffer, flush change-only rows.

This is the V3 EDDN station-sighting worker. It is not the V2 listener
(apps/eddn) and not the API-lifespan simulation ingest
(apps/api/src/ingest/eddn_client.py): it writes only v3_observe, never
canonical, derived or V2 relations, and it is not a journal-import scheduler or
journal-import canonical promotion path.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from .config import WorkerConfig
from .eddn_docked import Filtered, decode_frame, normalise_docked
from .metrics import Metrics
from .sightings import (
    SeenWatermarks,
    SightingRejected,
    StationSighting,
    WriteResult,
    configure_observer_session,
    record_sightings,
)

log = logging.getLogger('edfinder_observe.worker')

APPLICATION_NAME = 'edfinder-eddn-v3-observer'
POLL_MILLISECONDS = 1000
INITIAL_BACKOFF_SECONDS = 1.0


class FlushFailureThresholdExceeded(RuntimeError):
    """N consecutive flushes failed; the process exits non-zero so the
    container supervisor restarts it instead of the loop pretending to be live."""


class FinalFlushFailed(RuntimeError):
    """Shutdown was requested but the final flush failed; buffered sightings
    are lost, so the process must not report a clean exit."""

    def __init__(self, lost_rows: int):
        super().__init__(f'final flush failed; {lost_rows} buffered sighting(s) lost')
        self.lost_rows = lost_rows


class RelaySubscriber(Protocol):
    def recv(self, timeout_ms: int) -> bytes | None: ...

    def close(self) -> None: ...


# Upper bound on bytes ZeroMQ may hold in its receive queue before frames reach
# the bounded application buffer. The queue limit (RCVHWM) counts messages, so
# it is derived from this byte budget and the largest frame allowed: 512 frames
# at the default 256 KiB limit, 128 at the 1 MiB maximum. Frames beyond it are
# dropped by ZeroMQ (EDDN is lossy by design; the Spansh nightly is the net).
RECEIVE_QUEUE_BYTE_BUDGET = 128 * 1024 * 1024


def receive_high_water_mark(max_message_bytes: int) -> int:
    """Message-count queue limit that keeps the queue within the byte budget."""
    return max(1, RECEIVE_QUEUE_BYTE_BUDGET // max(1, max_message_bytes))


class ZmqSubscriber:
    """A ZeroMQ SUB socket on the EDDN relay with bounded queueing."""

    def __init__(self, relay_url: str, *, max_message_bytes: int = 262_144):
        import zmq

        self._zmq = zmq
        self._context = zmq.Context.instance()
        self._socket = self._context.socket(zmq.SUB)
        self._socket.setsockopt(zmq.SUBSCRIBE, b'')
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.setsockopt(zmq.RCVHWM, receive_high_water_mark(max_message_bytes))
        # Bound bytes before allocation: an oversized inbound frame makes ZeroMQ
        # drop that connection instead of buffering it. ZeroMQ does not redial
        # after that protocol error, so the feed goes quiet and the worker's
        # idle-reconnect path (EDDN_V3_RECV_IDLE_RECONNECT_SECONDS) builds a
        # fresh subscriber.
        self._socket.setsockopt(zmq.MAXMSGSIZE, max_message_bytes)
        self._socket.setsockopt(zmq.TCP_KEEPALIVE, 1)
        self._socket.setsockopt(zmq.TCP_KEEPALIVE_IDLE, 60)
        self._socket.connect(relay_url)
        self._poller = zmq.Poller()
        self._poller.register(self._socket, zmq.POLLIN)

    def recv(self, timeout_ms: int) -> bytes | None:
        if not dict(self._poller.poll(timeout_ms)).get(self._socket):
            return None
        return self._socket.recv(self._zmq.NOBLOCK)

    def close(self) -> None:
        self._socket.close(linger=0)


class PostgresWriter:
    """Lazily connects with the existing credential, then SET ROLE.

    One connection, autocommit so each batch is its own real transaction. Any
    write error closes the connection; the next flush reconnects.
    """

    def __init__(self, database_url: str, *, connect: Callable | None = None,
                 seen: SeenWatermarks | None = None):
        self._database_url = database_url
        self._connect = connect
        self._conn = None
        self.seen = seen

    def _connection(self):
        if self._conn is None or self._conn.closed:
            if self._connect is None:
                import psycopg

                self._connect = psycopg.connect
            conn = self._connect(self._database_url, autocommit=True,
                                 application_name=APPLICATION_NAME, connect_timeout=10)
            try:
                configure_observer_session(conn)
            except BaseException:
                conn.close()
                raise
            self._conn = conn
        return self._conn

    def __call__(self, batch: Sequence[StationSighting]) -> WriteResult:
        try:
            return record_sightings(self._connection(), batch, seen=self.seen)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None


def _error_label(exc: BaseException) -> str:
    """Exception type plus SQLSTATE: never message text, never row content."""
    sqlstate = getattr(exc, 'sqlstate', None)
    return f'{type(exc).__name__}{f" sqlstate={sqlstate}" if sqlstate else ""}'


class Worker:
    def __init__(
        self,
        config: WorkerConfig,
        *,
        subscriber_factory: Callable[[str], RelaySubscriber],
        writer: Callable[[Sequence[StationSighting]], WriteResult],
        metrics: Metrics,
        stop_event: threading.Event | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        wait: Callable[[float], bool] | None = None,
    ):
        self.config = config
        self._factory = subscriber_factory
        self._writer = writer
        self.metrics = metrics
        self.stop_event = stop_event or threading.Event()
        self._clock = clock
        self._wall = wall_clock
        self._wait = wait or self.stop_event.wait
        self.buffer: deque[StationSighting] = deque(maxlen=config.buffer_max_rows)
        self._last_flush = clock()
        self.session_received = False

    # -- message handling --------------------------------------------------

    def handle(self, raw: bytes) -> None:
        self.metrics.message_received()
        try:
            frame = decode_frame(raw, max_bytes=self.config.max_message_bytes)
            sighting = normalise_docked(
                frame,
                received_at=self._wall(),
                max_event_age=self.config.max_event_age,
                max_clock_skew=self.config.max_clock_skew,
            )
        except Filtered as exc:
            self.metrics.filtered(exc.reason)
            return
        except SightingRejected as exc:
            self.metrics.rejected(exc.reason)
            return
        except Exception as exc:  # never let one frame stop the loop
            log.debug('frame rejected: %s', _error_label(exc))
            self.metrics.rejected('other')
            return
        dropped = 1 if len(self.buffer) == self.buffer.maxlen else 0
        self.buffer.append(sighting)
        self.metrics.accepted()
        self.metrics.buffer_state(len(self.buffer), dropped)

    # -- flushing ----------------------------------------------------------

    def flush_due(self, now: float) -> bool:
        return bool(self.buffer) and (
            len(self.buffer) >= self.config.flush_max_rows
            or now - self._last_flush >= self.config.flush_interval_seconds)

    def flush_once(self) -> bool:
        """Write one bounded batch. Returns True on success.

        A failed batch is put back at the front of the buffer (oldest first) to
        be retried on the next flush; if that overflows the bounded buffer the
        oldest rows are dropped and counted. Retrying the same rows also means a
        persistently broken write path reaches the failure breaker even when the
        relay goes quiet.
        """
        size = min(len(self.buffer), self.config.flush_max_rows)
        batch = [self.buffer.popleft() for _ in range(size)]
        self._last_flush = self._clock()
        if not batch:
            self.metrics.buffer_state(len(self.buffer))
            return True
        self.metrics.flush_attempt()
        try:
            result = self._writer(batch)
        except Exception as exc:
            consecutive = self.metrics.flush_failure(len(batch))
            dropped = self._requeue(batch)
            log.warning('flush of %d sightings failed (%s); %d consecutive failure(s); '
                        'requeued for retry, %d dropped from a full buffer',
                        len(batch), _error_label(exc), consecutive, dropped)
            if consecutive >= self.config.max_consecutive_flush_failures:
                raise FlushFailureThresholdExceeded(
                    f'{consecutive} consecutive flush failures') from exc
            return False
        self.metrics.buffer_state(len(self.buffer))
        self.metrics.flush_success(result.inserted, result.unchanged, result.stale)
        log.debug('flushed %d sightings: %s', len(batch), result)
        return True

    def _requeue(self, batch: list[StationSighting]) -> int:
        combined = batch + list(self.buffer)
        dropped = max(0, len(combined) - self.buffer.maxlen)
        self.buffer = deque(combined[dropped:], maxlen=self.buffer.maxlen)
        self.metrics.buffer_state(len(self.buffer), dropped)
        return dropped

    def maybe_flush(self, now: float) -> None:
        while self.flush_due(now) and self.flush_once():
            if len(self.buffer) < self.config.flush_max_rows:
                break

    def drain(self) -> bool:
        """Flush everything once (shutdown / before a reconnect wait).

        Returns False if a flush failed; the failed rows stay buffered.
        """
        while self.buffer:
            if not self.flush_once():
                return False
        return True

    # -- relay loop --------------------------------------------------------

    def consume(self, subscriber: RelaySubscriber) -> None:
        """Read until idle timeout, error or stop.

        ``self.session_received`` records whether any frame arrived, even when
        the session later ends with a receive error.
        """
        self.session_received = False
        last_message = self._clock()
        while not self.stop_event.is_set():
            raw = subscriber.recv(POLL_MILLISECONDS)
            now = self._clock()
            if raw is None:
                self.maybe_flush(now)
                if now - last_message >= self.config.recv_idle_reconnect_seconds:
                    log.info('relay idle for %.0fs; reconnecting', now - last_message)
                    return
                continue
            self.session_received = True
            last_message = now
            self.handle(raw)
            self.maybe_flush(now)

    def run(self) -> None:
        """Run until stopped.

        Raises FlushFailureThresholdExceeded when the breaker trips and
        FinalFlushFailed when shutdown cannot flush the buffered sightings.
        """
        backoff = INITIAL_BACKOFF_SECONDS
        while not self.stop_event.is_set():
            self.session_received = False
            try:
                subscriber = self._factory(self.config.relay_url)
            except Exception as exc:
                log.warning('relay connect failed (%s)', _error_label(exc))
            else:
                log.info('subscribed to %s', self.config.relay_url)
                try:
                    self.consume(subscriber)
                except FlushFailureThresholdExceeded:
                    raise
                except Exception as exc:
                    log.warning('relay receive failed (%s)', _error_label(exc))
                finally:
                    subscriber.close()
            if self.stop_event.is_set():
                break
            self.drain()
            self.metrics.reconnected()
            if self.session_received:
                backoff = INITIAL_BACKOFF_SECONDS
            log.info('reconnecting in %.0fs', backoff)
            self._wait(backoff)
            backoff = min(backoff * 2, self.config.reconnect_backoff_max_seconds)
        if not self.drain():
            raise FinalFlushFailed(len(self.buffer))
