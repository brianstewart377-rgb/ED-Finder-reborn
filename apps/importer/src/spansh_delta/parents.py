"""Fill in what bodies orbit, where we hold it as unknown, from a Spansh galaxy file.

    python -m spansh_delta.parents check SOURCE    read only: say how many would be filled in
    python -m spansh_delta.parents apply SOURCE    fill them in

On the Pi this is run through ``deploy/galaxy_update.py`` (``check-parents``
and ``fill-parents``), once, with Spansh's whole-galaxy file.

SOURCE is a Spansh ``galaxy*.json.gz`` file: a path, or an ``https://``
address that is read as it downloads. If the connection drops, the download
picks up where it stopped, so the 120 GB whole-galaxy file needs no disk space
and no second start. If Spansh has put up a new file by then, it stops instead
of mixing the two, and says so.

Why: until 16 November 2026 the importer read only the first link of each
body's list of what it orbits. Every centre point that two bodies orbit (a
barycentre), and every body whose first link was a centre point nobody had
scanned, got no parent, and the System Map had to guess where to draw them.
The importer now reads the whole list (``v3_spansh.adapter.parent_links``), so
new and changed systems get it right from the nightly update. This fills in
the bodies imported before then, once.

What it changes, and nothing else:

  * only ``bodies.direct_parent_body_pk``;
  * only for a body the old rule gave no parent and the importer's rule gives
    one, and only where we still hold it as empty;
  * only to that parent, and only when we hold it, in the same system.

A parent we hold is never changed. Nothing is re-scored: no score reads what
a body orbits. Afterwards the nightly update finds these rows already as it
would write them, so it has nothing extra to do.

Safe to stop and to run again: bodies are filled in small transactions, and a
second run finds the finished ones already filled. It takes the nightly
update's lock, so the two never run at the same time. While the database
fills in one batch, the next is read from the file (a second thread).

The database is found the same way as for the nightly update (the ``PG*``
environment, or the API's own DATABASE_URL when
EDFINDER_GALAXY_USE_DATABASE_URL is 1). No connection string is read from the
command line and none is ever printed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import http.client
import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
import urllib.error
import urllib.request
import zlib

import psycopg
from psycopg import sql

from v3_spansh.adapter import body_pk_of, parent_links

from .apply import (
    EXIT_NO_CONNECTION,
    LOCK_KEY,
    STATEMENT_TIMEOUT,
    Refused,
    _reason,
    connect,
    preflight,
    say,
)

# Bodies to fill in one transaction, and how often to say how it is going.
BATCH_BODIES = 2_000
PROGRESS_SECONDS = 60
# Reading an address: how much at a time, how long to wait for the server, and
# how many times in a row a dropped connection is picked up again.
CHUNK = 1024 * 1024
NETWORK_TIMEOUT = 120
RETRIES = 20
RETRY_WAIT_SECONDS = (5, 15, 60, 300)


class SourceError(Exception):
    """The file could not be read to the end; the message says why."""


_RANGE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+)")


def _version_of(headers: Any) -> str | None:
    """What tells this copy of the file from a newer one, for If-Range: its
    ETag when that is a strong one, else when it was last changed."""
    tag = headers.get("ETag")
    if tag and not tag.startswith("W/"):
        return tag
    return headers.get("Last-Modified")


# -- what to fill in ----------------------------------------------------------

def _old_parent(body: Mapping[str, Any], held: Mapping[int, int]) -> int | None:
    """The parent the importer gave before 16 November 2026: the first link of
    the body's own list, if it is a body we hold."""
    parents = body.get("parents")
    if not (isinstance(parents, list) and parents and isinstance(parents[0], dict) and parents[0]):
        return None
    _, raw = next(iter(parents[0].items()))
    try:
        return held.get(int(raw))
    except (TypeError, ValueError):
        return None


def fills(record: Mapping[str, Any]) -> list[tuple[int, int, int]]:
    """(system id64, body, parent) for each body of one Spansh system that the
    old rule gave no parent and the importer's rule gives one.

    The same keys and the same rule as ``SpanshAdapter.adapt_system``, so a
    row filled in here is the row the importer would write for this record.
    """
    system_id64 = int(record["id64"])
    bodies = [body for body in (record.get("bodies") or []) if isinstance(body, dict)]
    held = {int(body["bodyId"]): body_pk_of(system_id64, body)
            for body in bodies if body.get("bodyId") is not None}
    found = []
    for body, parent in zip(bodies, parent_links(bodies, held), strict=True):
        if parent is not None and _old_parent(body, held) is None:
            found.append((system_id64, body_pk_of(system_id64, body), parent))
    return found


# -- reading the file ---------------------------------------------------------

def _from_address(url: str, sizes: list[int]) -> Iterator[bytes]:
    """The file's bytes, picking up where they stopped if the connection drops.

    ``sizes`` gets the file's size (once known) and the bytes read so far, so
    progress can be told."""
    received, total, failures, version = 0, None, 0, None
    while total is None or received < total:
        headers = {"User-Agent": "edfinder-fill-parents/1", "Accept-Encoding": "identity"}
        if received:
            headers["Range"] = "bytes=%d-" % received
            if version:
                # If the file has been replaced meanwhile, the server sends the
                # whole new file instead of the rest of this one.
                headers["If-Range"] = version
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                        timeout=NETWORK_TIMEOUT) as response:
                if received:
                    carried_on = _RANGE.fullmatch(response.headers.get("Content-Range", "").strip())
                    if (response.status != 206 or carried_on is None
                            or (int(carried_on[1]), int(carried_on[3])) != (received, total)):
                        raise SourceError(
                            "the download stopped part-way, and the server would not carry on from "
                            "there (Spansh has probably put up a newer file since)")
                else:
                    length = response.headers.get("Content-Length")
                    if length is None or not length.isdigit():
                        raise SourceError("the server did not say how big the file is")
                    total = int(length)
                    version = _version_of(response.headers)
                    sizes[:] = [total, 0]
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    received += len(chunk)
                    sizes[1] = received
                    failures = 0
                    yield chunk
            if received < total:
                raise http.client.IncompleteRead(b"", total - received)
        except urllib.error.HTTPError as error:
            error.close()
            raise SourceError("the server answered %d" % error.code) from None
        except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
            failures += 1
            if failures > RETRIES:
                raise SourceError("the download stopped at %.1f GB and could not be picked up again (%s)"
                                  % (received / 1e9, type(error).__name__)) from error
            wait = RETRY_WAIT_SECONDS[min(failures, len(RETRY_WAIT_SECONDS)) - 1]
            say("  The download stopped at %.1f GB (%s). Picking it up again in %d s ..."
                % (received / 1e9, type(error).__name__, wait))
            time.sleep(wait)


def _from_path(path: Path, sizes: list[int]) -> Iterator[bytes]:
    sizes[:] = [path.stat().st_size, 0]
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            sizes[1] += len(chunk)
            yield chunk


def _unpacked(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Unzip if the file is gzip (one part or several), else pass it through."""
    decompressor = None
    started = False
    for chunk in chunks:
        if not started:
            started = True
            if chunk[:2] == b"\x1f\x8b":
                decompressor = zlib.decompressobj(31)
        if decompressor is None:
            yield chunk
            continue
        try:
            data = decompressor.decompress(chunk)
            while decompressor.eof and decompressor.unused_data:
                rest = decompressor.unused_data
                decompressor = zlib.decompressobj(31)
                data += decompressor.decompress(rest)
        except zlib.error as error:
            raise SourceError("the file is damaged (%s)" % error) from None
        if data:
            yield data
    if decompressor is not None and not decompressor.eof:
        raise SourceError("the file ends part-way through")


def lines(source: str, sizes: list[int]) -> Iterator[bytes]:
    """Each system of a Spansh galaxy file, as the line Spansh writes it on."""
    chunks = (_from_address(source, sizes) if source.startswith(("https://", "http://"))
              else _from_path(Path(source), sizes))
    left = b""
    for data in _unpacked(chunks):
        parts = (left + data).split(b"\n")
        left = parts.pop()
        for part in parts:
            line = _system_line(part)
            if line is not None:
                yield line
    line = _system_line(left)
    if line is not None:
        yield line


def _system_line(line: bytes) -> bytes | None:
    line = line.strip()
    if line.endswith(b","):
        line = line[:-1]
    if not line or line in (b"[", b"]"):
        return None
    if not line.startswith(b"{"):
        raise SourceError("this is not a Spansh galaxy file: a line does not hold one system")
    return line


def record_of(line: bytes) -> Mapping[str, Any]:
    try:
        record = json.loads(line)
    except ValueError as error:
        raise SourceError("a line of the file is not a whole system (%s)" % error) from None
    if not isinstance(record, dict) or type(record.get("id64")) is not int:
        raise SourceError("a line of the file is not a system with an id64")
    return record


# The old rule and the importer's differ only through a centre point (the
# game's {"Null": id} link): a system whose lists name none has nothing to
# fill in, and is passed over without being read in full. On Spansh's one-day
# file of 16 November 2026, 37% of systems named one, and all 50,975 bodies to
# fill in were in them.
CENTRE_POINT = b'"Null"'


# -- the database -------------------------------------------------------------

_LOOK = """
SELECT count(*) FILTER (WHERE b.body_pk IS NOT NULL AND b.direct_parent_body_pk IS NULL
                                AND p.body_pk IS NOT NULL),
       count(*) FILTER (WHERE b.direct_parent_body_pk = v.parent),
       count(*) FILTER (WHERE b.direct_parent_body_pk <> v.parent),
       count(*) FILTER (WHERE b.body_pk IS NULL OR (b.direct_parent_body_pk IS NULL
                                                     AND p.body_pk IS NULL))
  FROM unnest(%s::bigint[], %s::bigint[], %s::bigint[]) AS v(system_id64, body_pk, parent)
  LEFT JOIN {bodies} AS b ON b.body_pk = v.body_pk AND b.system_id64 = v.system_id64
  LEFT JOIN {bodies} AS p ON p.body_pk = v.parent AND p.system_id64 = v.system_id64
"""

_FILL = """
UPDATE {bodies} AS b
   SET direct_parent_body_pk = v.parent
  FROM unnest(%s::bigint[], %s::bigint[], %s::bigint[]) AS v(system_id64, body_pk, parent)
 WHERE b.body_pk = v.body_pk AND b.system_id64 = v.system_id64
   AND b.direct_parent_body_pk IS NULL
   AND EXISTS (SELECT 1 FROM {bodies} AS p
                WHERE p.body_pk = v.parent AND p.system_id64 = v.system_id64)
"""


@dataclass
class Report:
    mode: str
    systems: int = 0
    found: int = 0
    filled: int = 0
    already: int = 0
    other_parent: int = 0
    not_held: int = 0
    stopped_early: bool = False
    seconds: float = 0.0

    def lines(self) -> list[str]:
        out = [
            f"Systems read: {self.systems:,}" + (" (stopped early, as asked)" if self.stopped_early else ""),
            f"Bodies the old importer left without a parent that the file gives one: {self.found:,}",
        ]
        if self.mode == "check":
            out += [
                f"  would be filled in now: {self.filled:,}",
                f"  already filled in: {self.already:,}",
                f"  left alone, because we hold another parent for them: {self.other_parent:,}",
                f"  left alone, because we do not hold the body or its parent: {self.not_held:,}",
            ]
        else:
            out += [
                f"  filled in: {self.filled:,}",
                f"  left alone (already filled in, or the body or its parent not held): "
                f"{self.found - self.filled:,}",
            ]
        out.append(f"Time: {_hours(self.seconds)}")
        return out


def _batch_columns(batch: Sequence[tuple[int, int, int]]) -> tuple[list[int], list[int], list[int]]:
    return [row[0] for row in batch], [row[1] for row in batch], [row[2] for row in batch]


def _hours(seconds: float) -> str:
    minutes = int(seconds) // 60
    return f"{minutes // 60} h {minutes % 60} min"


class _Writer:
    """Hands batches to the database on a second thread, so the database works
    on one batch while the next is read from the file. Only this thread uses
    the connection until :meth:`close` returns."""

    def __init__(self, write: Callable[[list[tuple[int, int, int]]], None]) -> None:
        self._write = write
        self._waiting: queue.Queue = queue.Queue(maxsize=2)
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._work, name="fill-parents-writer", daemon=True)
        self._thread.start()

    def _work(self) -> None:
        while (batch := self._waiting.get()) is not None:
            if self.error is None:
                try:
                    self._write(batch)
                except BaseException as error:  # handed to the reading thread
                    self.error = error

    def put(self, batch: list[tuple[int, int, int]]) -> None:
        if self.error is not None:
            raise self.error
        self._waiting.put(batch)

    def close(self) -> BaseException | None:
        """Wait for the batches already handed over; the first error, if any."""
        self._waiting.put(None)
        self._thread.join()
        return self.error


def _progress(report: Report, sizes: Sequence[int], seconds: float) -> str:
    total, done = sizes
    read = f"{done / 1e9:.1f} of {total / 1e9:.1f} GB read, " if total else ""
    left = f", about {_hours(seconds * (total - done) / done)} to go" if total and done else ""
    return (f"  ... {read}{report.systems:,} systems, {report.found:,} bodies found, "
            f"{report.filled:,} {'would be filled in' if report.mode == 'check' else 'filled in'}, "
            f"{_hours(seconds)} so far{left}")


def run(connection: psycopg.Connection[Any], source: str, *, mode: str,
        limit: int | None = None, progress: bool = True) -> Report:
    """Check or fill in from one Spansh galaxy file. ``connection`` must be autocommit."""
    if mode not in ("check", "apply"):
        raise ValueError("mode must be 'check' or 'apply'")
    if not connection.autocommit:
        raise ValueError("the connection must be in autocommit mode")
    report = Report(mode)
    started = last = time.monotonic()
    connection.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(STATEMENT_TIMEOUT)))
    connection.execute("SET lock_timeout = '60s'")
    target = preflight(connection, need_queue=False)
    bodies = sql.Identifier(target.schema, "bodies")
    look, fill = sql.SQL(_LOOK).format(bodies=bodies), sql.SQL(_FILL).format(bodies=bodies)
    locked = False
    if mode == "apply":
        if not connection.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
            raise Refused("a galaxy update is running; try again when it has finished")
        locked = True

    def write(batch: list[tuple[int, int, int]]) -> None:
        columns = _batch_columns(batch)
        if mode == "check":
            would, already, other_parent, not_held = connection.execute(look, columns).fetchone()
            report.filled += would
            report.already += already
            report.other_parent += other_parent
            report.not_held += not_held
        else:
            with connection.transaction():
                report.filled += connection.execute(fill, columns).rowcount

    sizes = [0, 0]
    batch: list[tuple[int, int, int]] = []
    try:
        writer = _Writer(write)
        try:
            for line in lines(source, sizes):
                if limit is not None and report.systems >= limit:
                    report.stopped_early = True
                    break
                report.systems += 1
                if CENTRE_POINT not in line:
                    continue
                try:
                    found = fills(record_of(line))
                except (TypeError, ValueError, KeyError):
                    # A record the importer itself could not read: it holds no rows of ours.
                    continue
                report.found += len(found)
                batch.extend(found)
                if len(batch) >= BATCH_BODIES:
                    writer.put(batch)
                    batch = []
                if progress and time.monotonic() - last >= PROGRESS_SECONDS:
                    last = time.monotonic()
                    say(_progress(report, sizes, last - started))
            if batch:
                writer.put(batch)
        finally:
            failed = writer.close()
        if failed is not None:
            raise failed
    finally:
        report.seconds = time.monotonic() - started
        if locked:
            try:
                connection.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
            except psycopg.Error:
                pass
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m spansh_delta.parents",
        description="Fill in what bodies orbit, where we hold it as unknown, from a Spansh galaxy file.")
    parser.add_argument("mode", choices=("check", "apply"),
                        help="check: read only, say how many would be filled in. apply: fill them in.")
    parser.add_argument("source", help="a Spansh galaxy*.json.gz file, or its https:// address")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after this many systems (for timing a big file)")
    args = parser.parse_args(argv)
    is_address = args.source.startswith(("https://", "http://"))
    if not is_address and not Path(args.source).is_file():
        say(f"FAILED: there is no file {args.source}")
        return 1
    if args.limit is not None and args.limit < 1:
        say("FAILED: --limit must be 1 or more")
        return 1
    connection = connect()
    if connection is None:
        return EXIT_NO_CONNECTION
    with connection:
        say(f"Filling in parents ({args.mode}) from {args.source}"
            + (", read as it downloads" if is_address else ""))
        try:
            report = run(connection, args.source, mode=args.mode, limit=args.limit)
        except Refused as error:
            say(f"REFUSED: {error}")
            say("Nothing was changed.")
            return 2
        except (SourceError, OSError, psycopg.Error) as error:
            say(f"FAILED: {_reason(error) if isinstance(error, psycopg.Error) else error}")
            say("What was filled in before this stays; running it again fills in the rest.")
            return 1
    for line in report.lines():
        say(line)
    if os.environ.get("EDFINDER_GALAXY_JSON") == "1":
        say("JSON " + json.dumps(report.__dict__, sort_keys=True))
    say("CHECKED: nothing was changed." if args.mode == "check" else "DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
