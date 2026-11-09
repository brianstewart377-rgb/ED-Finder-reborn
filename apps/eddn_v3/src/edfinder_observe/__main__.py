"""Entry point: ``python -m edfinder_observe``.

Exit codes: 0 after SIGTERM/SIGINT and a successful final flush; 2 for
invalid configuration; 3 when the flush breaker trips (N consecutive failed
flushes), so the container supervisor restarts the worker; 4 when shutdown
could not flush the buffered sightings (they are lost).
"""
from __future__ import annotations

import logging
import signal
import sys
import threading

from .config import ConfigError, load_config
from .metrics import Metrics, serve_metrics
from .sightings import SeenWatermarks
from .worker import (
    FinalFlushFailed,
    FlushFailureThresholdExceeded,
    PostgresWriter,
    Worker,
    ZmqSubscriber,
)

log = logging.getLogger('edfinder_observe')


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f'edfinder_observe: configuration error: {exc}', file=sys.stderr)
        return 2
    logging.basicConfig(level=config.log_level,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        log.info('signal %d received; stopping after a final flush', signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    metrics = Metrics()
    server = serve_metrics(metrics, config.metrics_host, config.metrics_port,
                           config.health_stale_seconds)
    writer = PostgresWriter(config.database_url,
                            seen=SeenWatermarks(config.watermark_max_stations))
    worker = Worker(
        config,
        subscriber_factory=lambda url: ZmqSubscriber(
            url, max_message_bytes=config.max_message_bytes),
        writer=writer, metrics=metrics, stop_event=stop)
    try:
        worker.run()
        return 0
    except FlushFailureThresholdExceeded as exc:
        log.error('write path unhealthy, exiting for restart: %s', exc)
        return 3
    except FinalFlushFailed as exc:
        log.error('%s', exc)
        return 4
    finally:
        server.shutdown()
        writer.close()


if __name__ == '__main__':
    sys.exit(main())
