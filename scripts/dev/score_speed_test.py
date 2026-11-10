#!/usr/bin/env python3
"""How fast does this machine score systems?

Scores the twelve real systems in tests/fixtures/ratings_v4_sources over and
over with the functions the scoring build uses, and prints systems per minute
for one worker and for every processor core.

Run the same command on two machines and compare the numbers. Only the ratio
means anything: these twelve systems have 14 to 50 bodies each, far more than
an average system, so the figures are much lower than a real build's.

No database and nothing to install: Python standard library only.

    python3 scripts/dev/score_speed_test.py
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from domain.ratings_v4 import opportunities_from_facts, rate_all  # noqa: E402
from domain.ratings_v4_canonical import (  # noqa: E402
    adapt_canonical_export,
    load_source_fixture,
)

FIXTURE = ROOT / "tests" / "fixtures" / "ratings_v4_sources"


def score_for(seconds: float) -> int:
    """Score the fixture systems for about `seconds`; return how many were scored."""
    canonical, metadata, records = load_source_fixture(FIXTURE)
    scored = 0
    stop_at = time.perf_counter() + seconds
    while time.perf_counter() < stop_at:
        # The same two steps the build does for every system: turn the stored
        # facts into scoring input, then rate every economy.
        for system in adapt_canonical_export(canonical, metadata, records).values():
            rate_all(opportunities_from_facts(system))
            scored += 1
    return scored


def per_minute(workers: int, seconds: float) -> int:
    started = time.perf_counter()
    if workers == 1:
        scored = score_for(seconds)
    else:
        with multiprocessing.Pool(workers) as pool:
            scored = sum(pool.map(score_for, [seconds] * workers))
    return round(scored / (time.perf_counter() - started) * 60)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Time system scoring on this machine.")
    parser.add_argument("--seconds", type=float, default=20.0,
                        help="how long each of the two measurements runs (default 20)")
    args = parser.parse_args(argv)
    if args.seconds <= 0:
        parser.error("--seconds must be above zero")
    cores = os.cpu_count() or 1
    print("Machine: %s, %d processor cores, Python %s" % (
        platform.machine(), cores, platform.python_version()), flush=True)
    print("Measuring for about %d seconds ..." % round(args.seconds * 2), flush=True)
    one = per_minute(1, args.seconds)
    print("1 worker:   %d systems a minute" % one, flush=True)
    every = per_minute(cores, args.seconds)
    print("%d workers:  %d systems a minute" % (cores, every), flush=True)
    print("Compare these with the same two lines from another machine.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
