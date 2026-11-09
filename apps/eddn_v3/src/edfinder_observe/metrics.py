"""Process metrics, liveness and the small /metrics + /healthz HTTP server.

Liveness follows the #864 pattern from apps/api/src/ingest/eddn_client.py:
the last-successful-flush timestamp only moves on a successful flush, a failed
flush is counted and never advances it, and the consecutive-failure count
resets on the next success. /healthz turns unhealthy while any flush failure
is outstanding or the relay has been silent for longer than the stale window.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FILTER_REASONS = ('schema', 'event', 'fleet_carrier', 'legacy')
REJECT_REASONS = (
    'decode', 'oversize', 'invalid_message', 'invalid_timestamp', 'invalid_market_id',
    'invalid_system_id64', 'invalid_system_name', 'invalid_station_name',
    'invalid_station_type', 'invalid_economies', 'invalid_services', 'invalid_landing_pads',
    'invalid_body', 'invalid_distance', 'invalid_header', 'invalid_source',
    'invalid_source_fields', 'incomplete_state', 'other',
)


class Metrics:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self.started_at = clock()
        self.messages_received_total = 0
        self.sightings_accepted_total = 0
        self.filtered_total = dict.fromkeys(FILTER_REASONS, 0)
        self.rejected_total = dict.fromkeys(REJECT_REASONS, 0)
        self.buffer_dropped_total = 0
        self.buffered_rows = 0
        self.flushes_total = 0
        self.flush_failures_total = 0
        self.consecutive_flush_failures = 0
        self.flush_failed_rows_total = 0
        self.rows_inserted_total = 0
        self.rows_unchanged_total = 0
        self.rows_stale_total = 0
        self.reconnects_total = 0
        self.last_message_received_at = 0.0
        self.last_flush_attempt_at = 0.0
        self.last_successful_flush_at = 0.0

    # -- recording ---------------------------------------------------------

    def message_received(self) -> None:
        with self._lock:
            self.messages_received_total += 1
            self.last_message_received_at = self._clock()

    def filtered(self, reason: str) -> None:
        with self._lock:
            key = reason if reason in self.filtered_total else 'event'
            self.filtered_total[key] += 1

    def rejected(self, reason: str) -> None:
        with self._lock:
            key = reason if reason in self.rejected_total else 'other'
            self.rejected_total[key] += 1

    def accepted(self) -> None:
        with self._lock:
            self.sightings_accepted_total += 1

    def buffer_state(self, rows: int, dropped: int = 0) -> None:
        with self._lock:
            self.buffered_rows = rows
            self.buffer_dropped_total += dropped

    def reconnected(self) -> None:
        with self._lock:
            self.reconnects_total += 1

    def flush_attempt(self) -> None:
        with self._lock:
            self.last_flush_attempt_at = self._clock()

    def flush_success(self, inserted: int, unchanged: int, stale: int) -> None:
        with self._lock:
            self.flushes_total += 1
            self.rows_inserted_total += inserted
            self.rows_unchanged_total += unchanged
            self.rows_stale_total += stale
            self.consecutive_flush_failures = 0
            self.last_successful_flush_at = self._clock()

    def flush_failure(self, rows: int) -> int:
        with self._lock:
            self.flush_failures_total += 1
            self.flush_failed_rows_total += rows
            self.consecutive_flush_failures += 1
            return self.consecutive_flush_failures

    # -- reading -----------------------------------------------------------

    def health(self, stale_seconds: float) -> tuple[bool, str]:
        with self._lock:
            now = self._clock()
            if self.consecutive_flush_failures:
                return False, f'flush failing ({self.consecutive_flush_failures} consecutive)'
            reference = self.last_message_received_at or self.started_at
            if now - reference > stale_seconds:
                return False, 'no relay message within the stale window'
            return True, 'ok'

    def render(self) -> str:
        with self._lock:
            lines: list[str] = []
            described: set[str] = set()

            def emit(name: str, kind: str, help_text: str, value: float,
                     labels: str = '') -> None:
                if name not in described:
                    described.add(name)
                    lines.append(f'# HELP {name} {help_text}')
                    lines.append(f'# TYPE {name} {kind}')
                lines.append(f'{name}{labels} {value}')

            p = 'edfinder_eddn_v3_'
            emit(p + 'messages_received_total', 'counter', 'Relay frames received.',
                 self.messages_received_total)
            emit(p + 'sightings_accepted_total', 'counter', 'Docked sightings buffered for writing.',
                 self.sightings_accepted_total)
            for reason, value in self.filtered_total.items():
                emit(p + 'messages_filtered_total', 'counter', 'Frames ignored by design.',
                     value, f'{{reason="{reason}"}}')
            for reason, value in self.rejected_total.items():
                emit(p + 'messages_rejected_total', 'counter', 'Frames failing validation.',
                     value, f'{{reason="{reason}"}}')
            emit(p + 'buffer_rows', 'gauge', 'Sightings waiting to be flushed.', self.buffered_rows)
            emit(p + 'buffer_dropped_total', 'counter', 'Oldest sightings dropped on a full buffer.',
                 self.buffer_dropped_total)
            emit(p + 'flushes_total', 'counter', 'Successful batch flushes.', self.flushes_total)
            emit(p + 'flush_failures_total', 'counter', 'Failed batch flushes.',
                 self.flush_failures_total)
            emit(p + 'consecutive_flush_failures', 'gauge',
                 'Flush failures since the last success.', self.consecutive_flush_failures)
            emit(p + 'flush_failed_rows_total', 'counter', 'Sightings in failed batches.',
                 self.flush_failed_rows_total)
            emit(p + 'rows_inserted_total', 'counter', 'State-change rows written.',
                 self.rows_inserted_total)
            emit(p + 'rows_unchanged_total', 'counter', 'Sightings matching the current state.',
                 self.rows_unchanged_total)
            emit(p + 'rows_stale_total', 'counter', 'Sightings older than the current state.',
                 self.rows_stale_total)
            emit(p + 'reconnects_total', 'counter', 'Relay reconnects.', self.reconnects_total)
            emit(p + 'last_message_received_timestamp_seconds', 'gauge',
                 'Unix time of the last relay frame (0 = never).', self.last_message_received_at)
            emit(p + 'last_flush_attempt_timestamp_seconds', 'gauge',
                 'Unix time a non-empty flush last started (0 = never).', self.last_flush_attempt_at)
            emit(p + 'last_successful_flush_timestamp_seconds', 'gauge',
                 'Unix time of the last successful flush (0 = never).', self.last_successful_flush_at)
            return '\n'.join(lines) + '\n'


def serve_metrics(metrics: Metrics, host: str, port: int,
                  stale_seconds: float) -> ThreadingHTTPServer:
    """Start /metrics and /healthz on a daemon thread; returns the server."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            if self.path == '/metrics':
                status, body = 200, metrics.render()
                content_type = 'text/plain; version=0.0.4; charset=utf-8'
            elif self.path == '/healthz':
                healthy, reason = metrics.health(stale_seconds)
                status, body = (200 if healthy else 503), reason + '\n'
                content_type = 'text/plain; charset=utf-8'
            else:
                status, body, content_type = 404, 'not found\n', 'text/plain; charset=utf-8'
            payload = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return  # no per-request access log

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name='metrics-http', daemon=True)
    thread.start()
    return server
