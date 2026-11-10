"""scripts/dev/score_speed_test.py: the machine-speed comparison for scoring."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dev" / "score_speed_test.py"


def test_it_prints_one_worker_and_all_core_rates_with_only_the_standard_library():
    # -I: no site-packages and no environment, so anything beyond the standard
    # library would fail to import here.
    done = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--seconds", "0.4"],
        capture_output=True, text=True, timeout=120, cwd="/")
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert re.fullmatch(r"Machine: \S+, \d+ processor cores, Python 3\.\d+\.\d+\S*", lines[0])
    one = re.fullmatch(r"1 worker:   (\d+) systems a minute", lines[2])
    every = re.fullmatch(r"(\d+) workers:  (\d+) systems a minute", lines[3])
    assert one and every
    assert int(one.group(1)) > 0 and int(every.group(2)) > 0
    assert done.stderr == ""


def test_zero_seconds_is_refused():
    done = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--seconds", "0"],
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 2 and "must be above zero" in done.stderr
