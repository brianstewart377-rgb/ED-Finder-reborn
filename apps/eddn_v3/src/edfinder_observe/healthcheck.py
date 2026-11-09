"""Container health probe: ``python -m edfinder_observe.healthcheck``.

Probes /healthz on the configured metrics bind address (a wildcard bind is
probed on loopback). Exit 0 when healthy, 1 otherwise. Reads only the two
metrics variables; never the database URL.
"""
from __future__ import annotations

import ipaddress
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping


def probe_url(env: Mapping[str, str]) -> str:
    """Fixed scheme and path; host must be an IPv4 literal, port an integer."""
    host = ipaddress.IPv4Address(env.get('EDDN_V3_METRICS_HOST') or '127.0.0.1')
    if host.is_unspecified:
        host = ipaddress.IPv4Address('127.0.0.1')
    port = int(env.get('EDDN_V3_METRICS_PORT') or '9465', 10)
    if not 1024 <= port <= 65535:
        raise ValueError('EDDN_V3_METRICS_PORT out of range')
    return f'http://{host}:{port}/healthz'


def main() -> int:
    try:
        url = probe_url(os.environ)
        with urllib.request.urlopen(url, timeout=4) as response:  # nosemgrep: dynamic-urllib-use-detected -- fixed http scheme and /healthz path; host validated as an IPv4 literal and port as an integer by probe_url
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, OSError, ValueError):
        return 1


if __name__ == '__main__':
    sys.exit(main())
