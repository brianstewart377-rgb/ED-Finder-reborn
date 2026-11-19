"""Read a Spansh galaxy file one system at a time: from disk, or from an
``https://`` address as it downloads.

For the jobs that read Spansh's whole-galaxy file once (``spansh_delta.parents``
and ``spansh_delta.recent``). That file is 120 GB, so it is read as it
downloads and never kept:

  * if the connection drops, the download carries on from where it stopped;
  * if Spansh has put up a newer file by then, it stops instead of mixing the
    two (If-Range), and says so;
  * a gzip file is unpacked, in one part or several; a plain one passes through;
  * a file cut short, damaged, or not in Spansh's layout (one system per line,
    between "[" and "]") is refused with a plain reason (:class:`SourceError`).
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path
import re
import time
from typing import Any, Iterable, Iterator, Mapping
import urllib.error
import urllib.request
import zlib

from .apply import say

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


def _from_address(url: str, sizes: list[int]) -> Iterator[bytes]:
    """The file's bytes, picking up where they stopped if the connection drops.

    ``sizes`` gets the file's size (once known) and the bytes read so far, so
    progress can be told."""
    received, total, failures, version = 0, None, 0, None
    while total is None or received < total:
        headers = {"User-Agent": "edfinder-galaxy/1", "Accept-Encoding": "identity"}
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


def hours(seconds: float) -> str:
    minutes = int(seconds) // 60
    return f"{minutes // 60} h {minutes % 60} min"
