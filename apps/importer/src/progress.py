"""
ED Finder — Shared progress reporting helper
Version: 1.1
Used by build_ratings.py, build_grid.py, build_clusters.py

Provides:
  - fmt_num / fmt_duration / fmt_rate / fmt_eta  — formatting helpers
  - stage_banner / done_banner / crash_hint        — visual section headers
  - startup_banner                                 — script start box
  - ProgressReporter                               — interval-based progress lines with ETA
  - WorkerHeartbeat                                — lightweight heartbeat for worker processes
"""

import time
import logging
import os

log = logging.getLogger('progress')


# ─────────────────────────────────────────────────────────────────────────────
# Formatters
# ─────────────────────────────────────────────────────────────────────────────

def fmt_num(n) -> str:
    """Format large number with commas (handles int and float)."""
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def fmt_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m {s:02d}s"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m:02d}m"


def fmt_rate(n: int, elapsed: float) -> str:
    """Format items/sec rate."""
    if elapsed <= 0:
        return "?"
    rate = n / elapsed
    if rate >= 1_000_000:
        return f"{rate/1_000_000:.1f}M/s"
    elif rate >= 1_000:
        return f"{rate/1_000:.1f}k/s"
    return f"{rate:.0f}/s"


def fmt_eta(remaining: int, rate_per_sec: float) -> str:
    """Format ETA from remaining count and rate."""
    if rate_per_sec <= 0 or remaining <= 0:
        return "?"
    return fmt_duration(remaining / rate_per_sec)


def fmt_pct(done: int, total: int) -> str:
    """Format percentage, guarding against zero division."""
    if total <= 0:
        return "?.?%"
    return f"{done / total * 100:.1f}%"


# ─────────────────────────────────────────────────────────────────────────────
# Banners
# ─────────────────────────────────────────────────────────────────────────────

def startup_banner(log, script_name: str, version: str, config_lines: list = None):
    """
    Print a clearly visible startup box at the top of any build script.
    config_lines: list of (label, value) tuples shown inside the box.
    """
    width = 62
    border = "═" * width
    log.info("")
    log.info(f"╔{border}╗")
    title = f"ED FINDER — {script_name.upper()} {version}"
    log.info(f"║  {title:<{width-2}}║")
    log.info(f"╠{border}╣")
    if config_lines:
        for label, value in config_lines:
            line = f"  {label:<18}: {value}"
            log.info(f"║{line:<{width}}║")
    log.info(f"╚{border}╝")
    log.info("")


def stage_banner(log, stage_num: int, total_stages: int, title: str, resumed: bool = False):
    """Print a clear stage header."""
    resume_tag = "  ← RESUMING" if resumed else ""
    log.info("")
    log.info(f"{'─'*62}")
    log.info(f"  STAGE {stage_num}/{total_stages}: {title}{resume_tag}")
    log.info(f"{'─'*62}")


def done_banner(log, title: str, elapsed: float, extra_lines: list = None):
    """Print a clear completion banner with timing and optional stats."""
    log.info("")
    log.info(f"{'═'*62}")
    log.info(f"  ✓  {title}")
    log.info(f"     Total time: {fmt_duration(elapsed)}")
    if extra_lines:
        for line in extra_lines:
            log.info(f"     {line}")
    log.info(f"{'═'*62}")
    log.info("")


def crash_hint(log, context: str = "from this stage"):
    """
    Print a prominently visible crash-recovery hint.
    Called at the start of any long, interruptable operation.
    """
    log.info("")
    log.info("  ┌─ CRASH RECOVERY ──────────────────────────────────────┐")
    log.info("  │  If this script is interrupted or crashes, simply      │")
    log.info("  │  re-run the same command — it will resume              │")
    log.info(f"  │  {context:<54}│")
    log.info("  └───────────────────────────────────────────────────────┘")
    log.info("")


def check_and_warn_log_path(log, log_file: str):
    """
    Emit a warning if the log file directory doesn't exist or isn't writable.
    Helps catch the /data/logs/ Docker volume problem early.
    """
    log_dir = os.path.dirname(os.path.abspath(log_file))
    if not os.path.isdir(log_dir):
        log.warning(f"  ⚠  Log directory '{log_dir}' does not exist — logs only go to stdout")
    elif not os.access(log_dir, os.W_OK):
        log.warning(f"  ⚠  Log directory '{log_dir}' is not writable — logs only go to stdout")


# ─────────────────────────────────────────────────────────────────────────────
# ProgressReporter — main-process progress tracker
# ─────────────────────────────────────────────────────────────────────────────

class ProgressReporter:
    """
    Interval-based progress reporter with ETA, rate, and heartbeat.

    Logs a progress line at most every `interval` seconds.
    Also logs a heartbeat every `heartbeat` seconds to confirm the script
    is still alive during long DB operations.

    Usage:
        prog = ProgressReporter(log, total=135_000, label="cell-assign")
        for item in items:
            process(item)
            prog.update(1)
        prog.finish()
    """

    def __init__(self, log, total: int | None, label: str,
                 interval: float = 30.0, heartbeat: float = 120.0):
        self.log        = log
        self.total      = total
        self.label      = label
        self.interval   = interval
        self.heartbeat  = heartbeat
        self.done       = 0
        self.errors     = 0
        self._start     = time.time()
        self._last_log  = time.time()
        self._last_hb   = time.time()

    def update(self, n: int = 1, errors: int = 0, force: bool = False):
        """Call after completing n items. Logs if interval has elapsed."""
        self.done   += n
        self.errors += errors
        now          = time.time()

        if force or (now - self._last_log) >= self.interval:
            self._print()
            self._last_log = now
            self._last_hb  = now
        elif (now - self._last_hb) >= self.heartbeat:
            self._heartbeat()
            self._last_hb = now

    def _elapsed(self) -> float:
        return time.time() - self._start

    def _rate(self) -> float:
        e = self._elapsed()
        return self.done / e if e > 0 else 0

    def _print(self):
        elapsed   = self._elapsed()
        rate      = self._rate()
        err_str = f" | errors: {self.errors:,}" if self.errors else ""
        if self.total is None:
            self.log.info(
                f"  [{self.label}]  {fmt_num(self.done)} / unknown"
                f"  {fmt_rate(self.done, elapsed)}"
                f"  elapsed: {fmt_duration(elapsed)}"
                f"{err_str}"
            )
            return

        remaining = max(self.total - self.done, 0)
        pct       = self.done / self.total * 100 if self.total > 0 else 0
        eta       = fmt_eta(remaining, rate)
        self.log.info(
            f"  [{self.label}]  {fmt_num(self.done)} / {fmt_num(self.total)}"
            f"  ({pct:.1f}%)"
            f"  {fmt_rate(self.done, elapsed)}"
            f"  elapsed: {fmt_duration(elapsed)}"
            f"  ETA: {eta}"
            f"{err_str}"
        )

    def _heartbeat(self):
        elapsed = self._elapsed()
        if self.total is None:
            self.log.info(
                f"  ♥  [{self.label}] still alive —"
                f" {fmt_num(self.done)}/unknown"
                f" | {fmt_rate(self.done, elapsed)}"
                f" | elapsed: {fmt_duration(elapsed)}"
            )
            return

        pct = self.done / self.total * 100 if self.total > 0 else 0
        self.log.info(
            f"  ♥  [{self.label}] still alive —"
            f" {fmt_num(self.done)}/{fmt_num(self.total)} ({pct:.1f}%)"
            f" | {fmt_rate(self.done, elapsed)}"
            f" | elapsed: {fmt_duration(elapsed)}"
        )

    def finish(self, extra: str = "") -> float:
        """Call when done. Prints final summary and returns elapsed seconds."""
        elapsed = self._elapsed()
        self.log.info(
            f"  [{self.label}]  COMPLETE — {fmt_num(self.done)} items"
            f" in {fmt_duration(elapsed)}"
            f" ({fmt_rate(self.done, elapsed)})"
            + (f" | {self.errors:,} errors" if self.errors else "")
            + (f"  {extra}" if extra else "")
        )
        return elapsed


# ─────────────────────────────────────────────────────────────────────────────
# WorkerHeartbeat — for worker processes (multiprocessing)
# ─────────────────────────────────────────────────────────────────────────────

class WorkerHeartbeat:
    """
    Lightweight heartbeat for worker processes.
    Since worker processes can't write to the main logger directly,
    this writes to the worker's own logger and/or stdout so Docker
    captures it. Call .tick() after each anchor/system to emit a line
    every `interval` seconds.

    Usage (inside worker_process):
        hb = WorkerHeartbeat(worker_id, total=len(batch), interval=60)
        for item in batch:
            process(item)
            hb.tick(processed)
    """

    def __init__(self, worker_id: int, total: int,
                 label: str = "worker", interval: float = 60.0):
        self.worker_id = worker_id
        self.total     = total
        self.label     = label
        self.interval  = interval
        self._start    = time.time()
        self._last     = time.time()
        self._log      = logging.getLogger(f'worker.{worker_id}')

    def tick(self, done: int, errors: int = 0):
        """Call with current `done` count. Prints if interval elapsed."""
        now = time.time()
        if now - self._last < self.interval:
            return
        self._last = now
        elapsed = now - self._start
        rate    = done / elapsed if elapsed > 0 else 0
        pct     = done / self.total * 100 if self.total > 0 else 0
        remaining = max(self.total - done, 0)
        eta     = fmt_eta(remaining, rate)
        err_str = f" errors={errors}" if errors else ""
        self._log.info(
            f"  [W{self.worker_id}:{self.label}]"
            f" {fmt_num(done)}/{fmt_num(self.total)} ({pct:.1f}%)"
            f" {fmt_rate(done, elapsed)}"
            f" ETA:{eta}{err_str}"
        )
