"""Save the systems Spansh has changed since a date, from its whole-galaxy file.

    python -m spansh_delta.recent save SOURCE OUTPUT --since YYYY-MM-DD [--limit N]

For a galaxy that missed some of Spansh's changes: the nightly update
(``python -m spansh_delta.apply``) then applies the saved file like any other,
and each system in it comes out as Spansh has it now. On the Pi both halves are
run by ``deploy/galaxy_update.py catch-up``.

Why: the galaxy on the Pi was imported from Spansh's whole-galaxy file of
24 August 2026, and the nightly updates only started on 10 November, the first
ones with a file that reached back a day. What Spansh changed in between never
arrived. Spansh's files of recent changes reach back a month at most; only the
whole-galaxy file (120 GB) holds the rest.

SOURCE is the whole-galaxy file: a path, or an ``https://`` address read as it
downloads (``spansh_delta.source``). OUTPUT is where the systems changed on or
after --since are saved, in Spansh's own layout, gzip-packed. It is written
under a temporary name and takes OUTPUT's name only when complete, so a file
called OUTPUT is always whole; ``OUTPUT.json`` then says what is in it.

A system counts as changed when any date in its record (its own, or any
body's, station's or market's) is on or after --since. Spansh writes each date
as text that starts with the year, month and day, so this is a check on the
bytes of each line: no system is unpacked, and no database is read.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import gzip
import json
import os
from pathlib import Path
import sys
import time
from typing import Sequence

from .apply import say
from .source import SourceError, hours, lines

PROGRESS_SECONDS = 60
# Fast packing: the file is read once more, by the update, and then removed.
PACKING = 1


def marks(since: date, today: date) -> tuple[tuple[bytes, ...], tuple[bytes, ...]]:
    """Text, one piece of which is in every line holding a date on or after
    ``since`` (and not after the end of ``today``'s year).

    A quick check, '"YYYY-' for each year from since's to today's, and the full
    one: '"YYYY-MM-DD' for each day left in since's month, '"YYYY-MM' for each
    later month of its year, and '"YYYY' for each later year.
    """
    if since > today:
        raise ValueError("--since is after today")
    quick = tuple(b'"%04d-' % year for year in range(since.year, today.year + 1))
    next_month = date(since.year + since.month // 12, since.month % 12 + 1, 1)
    full = [b'"%04d-%02d-%02d' % (since.year, since.month, day)
            for day in range(since.day, (next_month - timedelta(days=1)).day + 1)]
    full += [b'"%04d-%02d' % (since.year, month) for month in range(since.month + 1, 13)]
    full += [b'"%04d' % year for year in range(since.year + 1, today.year + 1)]
    return quick, tuple(full)


def changed(line: bytes, quick: Sequence[bytes], full: Sequence[bytes]) -> bool:
    return any(mark in line for mark in quick) and any(mark in line for mark in full)


@dataclass
class Report:
    since: date
    systems: int = 0
    saved: int = 0
    saved_bytes: int = 0
    stopped_early: bool = False
    seconds: float = 0.0

    def lines(self) -> list[str]:
        return [
            f"Systems read: {self.systems:,}" + (" (stopped early, as asked)" if self.stopped_early else ""),
            f"Changed on or after {self.since:%d %b %Y}, saved: {self.saved:,} "
            f"({self.saved_bytes / 1e9:.1f} GB)",
            f"Time: {hours(self.seconds)}",
        ]


def _progress(report: Report, sizes: Sequence[int], seconds: float) -> str:
    total, done = sizes
    read = f"{done / 1e9:.1f} of {total / 1e9:.1f} GB read, " if total else ""
    left = f", about {hours(seconds * (total - done) / done)} to go" if total and done else ""
    return (f"  ... {read}{report.systems:,} systems, {report.saved:,} changed since "
            f"{report.since:%d %b %Y} saved, {hours(seconds)} so far{left}")


def save(source: str, output: Path, since: date, *, today: date | None = None,
         limit: int | None = None, progress: bool = True) -> Report:
    """Save to ``output`` the systems of ``source`` changed on or after ``since``."""
    quick, full = marks(since, today or datetime.now(timezone.utc).date())
    report = Report(since)
    started = last = time.monotonic()
    sizes = [0, 0]
    partial = output.with_name(output.name + ".part")
    try:
        with gzip.open(partial, "wb", compresslevel=PACKING) as out:
            out.write(b"[\n")
            for line in lines(source, sizes):
                if limit is not None and report.systems >= limit:
                    report.stopped_early = True
                    break
                report.systems += 1
                if changed(line, quick, full):
                    out.write((b",\n    " if report.saved else b"    ") + line)
                    report.saved += 1
                if progress and time.monotonic() - last >= PROGRESS_SECONDS:
                    last = time.monotonic()
                    say(_progress(report, sizes, last - started))
            out.write(b"\n]\n")
        report.saved_bytes = partial.stat().st_size
        os.replace(partial, output)
        about = output.with_name(output.name + ".json")
        about_partial = about.with_name(about.name + ".part")
        about_partial.write_text(json.dumps({
            "since": since.isoformat(), "systems_read": report.systems,
            "systems_saved": report.saved, "stopped_early": report.stopped_early,
        }) + "\n")
        os.replace(about_partial, about)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        report.seconds = time.monotonic() - started
    return report


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("not a date like 2026-08-22: %r" % text) from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m spansh_delta.recent",
        description="Save the systems Spansh has changed since a date, from its whole-galaxy file.")
    parser.add_argument("mode", choices=("save",))
    parser.add_argument("source", help="Spansh's galaxy.json.gz: a path, or an https:// address")
    parser.add_argument("output", type=Path, help="where to save the changed systems (.json.gz)")
    parser.add_argument("--since", type=_day, required=True,
                        help="the first day whose changes are saved (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after this many systems (for a trial)")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        say("FAILED: --limit must be 1 or more")
        return 1
    if not args.source.startswith(("https://", "http://")) and not Path(args.source).is_file():
        say(f"FAILED: there is no file {args.source}")
        return 1
    say(f"Saving the systems changed since {args.since:%d %b %Y} from {args.source}"
        + (", read as it downloads" if args.source.startswith(("https://", "http://")) else ""))
    try:
        report = save(args.source, args.output, args.since, limit=args.limit)
    except (SourceError, OSError, ValueError) as error:
        say(f"FAILED: {error}")
        say("Nothing was saved.")
        return 1
    for line in report.lines():
        say(line)
    say("SAVED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
