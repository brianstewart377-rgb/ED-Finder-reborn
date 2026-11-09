"""Stream a Spansh ``galaxy*.json`` / ``galaxy*.json.gz`` one system at a time.

Format contract (verified against the repository's retained importer, not
against a live download -- this sandbox cannot reach Spansh):

* The document is one top-level JSON array of system objects in Spansh's
  ``galaxy.schema.json`` shape (``id64``, ``name``, ``coords``, ``date``,
  ``bodies[]``, ``stations[]``, faction fields, ...).
* In practice Spansh writes the opening ``[`` on its own line, then one compact
  system object per physical line followed by ``,`` (none after the last), then
  ``]`` on its own line.  ``v3_spansh.pipeline._seek_macmillan_record`` relies on
  exactly this layout for the published 2026-08-24 ``galaxy.json.gz``.

The default ``auto`` parser reads that line layout with the standard-library
JSON decoder (fast, and memory bounded by one system line).  If any line does
not fit the layout it raises :class:`LineFormatError` internally and ``auto``
replays the file with ``ijson`` (format-agnostic, still one system at a time),
skipping -- after checking -- the systems it had already yielded.  ``lines`` and
``ijson`` force one parser.  Truncation or corruption is never "fixed" by the
fallback: both parsers fail closed, and only a stream consumed to EOF produces a
:class:`StreamReceipt`.
"""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, BinaryIO, Iterator

PARSERS = ("auto", "lines", "ijson")
MAX_RECORD_BYTES = 256 * 1024 * 1024
MAX_SYSTEM_BODIES = 100_000
MAX_ID64 = 2**63 - 1
_GZIP_MAGIC = b"\x1f\x8b"
_READ_SIZE = 1024 * 1024


class LineFormatError(ValueError):
    """The file is valid-looking JSON but not one system object per line."""


@dataclass(frozen=True)
class SpanshRecord:
    """One raw Spansh system object.

    ``raw_bytes`` is the size of the system's compact JSON text: the bytes of
    its line for the line parser, or the record re-encoded compactly (UTF-8,
    ``","``/``":"`` separators) for the ``ijson`` parser.
    """

    record: dict[str, Any]
    raw_bytes: int | None
    ordinal: int


@dataclass(frozen=True)
class StreamReceipt:
    """Exists only after the whole file was read to EOF without error."""

    file_name: str
    compressed: bool
    file_bytes: int
    file_sha256: str
    decompressed_bytes: int
    systems: int
    parser: str
    fallback_after_records: int | None
    fallback_reason: str | None
    consumed_to_eof: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "file_name": self.file_name,
            "compressed": self.compressed,
            "file_bytes": self.file_bytes,
            "file_sha256": self.file_sha256,
            "decompressed_bytes": self.decompressed_bytes,
            "systems": self.systems,
            "parser": self.parser,
            "fallback_after_records": self.fallback_after_records,
            "fallback_reason": self.fallback_reason,
            "consumed_to_eof": self.consumed_to_eof,
        }


class _HashingReader:
    """Hash and count the file bytes the decoder actually consumed."""

    def __init__(self, stream: BinaryIO):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = self.stream.read(size)
        self.digest.update(data)
        self.bytes_read += len(data)
        return data


class _CountingArrayReader:
    """Count decoded bytes and require a JSON array root before ijson starts.

    ``ijson.items(..., "item")`` silently yields nothing for a non-array root,
    which would look like an empty delta file; refuse that instead.  It also
    builds each system object in memory before yielding it, so the bytes read
    since the last yielded system are bounded by ``MAX_RECORD_BYTES`` (plus one
    read buffer) -- the same per-system limit the line parser applies.
    """

    def __init__(self, stream: Any):
        self.stream = stream
        self.bytes_read = 0
        self.since_item = 0
        self.checked = False

    def item_done(self) -> None:
        self.since_item = 0

    def read(self, size: int = -1) -> bytes:
        data = self.stream.read(size)
        self.bytes_read += len(data)
        self.since_item += len(data)
        if self.since_item > MAX_RECORD_BYTES + _READ_SIZE:
            raise ValueError("Spansh system record exceeds the bounded record size")
        if size != 0 and not self.checked:
            prefix = data.lstrip(b" \t\r\n\v\f")
            if prefix:
                if prefix[:1] != b"[":
                    raise ValueError("Spansh artifact must be a JSON array")
                self.checked = True
            elif not data:
                raise ValueError("Spansh artifact must be a JSON array")
        return data


def _compact_size(record: dict[str, Any]) -> int:
    return len(json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode())


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON constant in Spansh record: {token}")


def _validated(record: object) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("invalid Spansh system record: not a JSON object")
    id64 = record.get("id64")
    if type(id64) is not int or not 0 <= id64 <= MAX_ID64:
        raise ValueError(f"invalid Spansh system id64: {id64!r}")
    for key in ("bodies", "stations"):
        value = record.get(key)
        if value is not None and not isinstance(value, list):
            raise ValueError(f"system {id64}: {key} is not a list")
    if len(record.get("bodies") or ()) > MAX_SYSTEM_BODIES:
        raise ValueError(f"system {id64} exceeds the bounded body inventory")
    return record


class SpanshStream:
    """Single-use iterator over the systems of one Spansh galaxy dump."""

    def __init__(self, path: Path | str, *, parser: str = "auto"):
        if parser not in PARSERS:
            raise ValueError(f"parser must be one of {PARSERS}")
        self.path = Path(path)
        self.parser = parser
        self.receipt: StreamReceipt | None = None
        self._started = False

    def _is_gzip(self) -> bool:
        with self.path.open("rb") as handle:
            return handle.read(2) == _GZIP_MAGIC

    def __iter__(self) -> Iterator[SpanshRecord]:
        if self._started:
            raise ValueError("Spansh stream is single use")
        self._started = True
        compressed = self._is_gzip()
        if self.parser == "ijson":
            yield from self._run_ijson(compressed, skip=0, skip_digest=None,
                                       fallback_reason=None)
            return
        yielded = 0
        identity = hashlib.sha256()
        try:
            for item in self._run_lines(compressed):
                identity.update(item.record["id64"].to_bytes(8, "little"))
                yielded += 1
                yield item
        except LineFormatError as exc:
            if self.parser == "lines":
                raise
            yield from self._run_ijson(compressed, skip=yielded,
                                       skip_digest=identity.hexdigest(),
                                       fallback_reason=str(exc))

    # -- one-system-per-line parser -------------------------------------
    def _run_lines(self, compressed: bool) -> Iterator[SpanshRecord]:
        with self.path.open("rb") as raw:
            hashing = _HashingReader(raw)
            if compressed:
                decoded: Any = gzip.GzipFile(fileobj=hashing, mode="rb")
            else:
                decoded = None
            try:
                state = "start"
                pending_comma = False
                systems = 0
                decompressed = 0
                while True:
                    if decoded is not None:
                        try:
                            line = decoded.readline(MAX_RECORD_BYTES + 1)
                        except EOFError as exc:
                            raise ValueError("Spansh gzip stream is truncated") from exc
                    else:
                        line = raw.readline(MAX_RECORD_BYTES + 1)
                        hashing.digest.update(line)
                        hashing.bytes_read += len(line)
                    if not line:
                        break
                    decompressed += len(line)
                    if len(line) > MAX_RECORD_BYTES:
                        raise ValueError("Spansh system line exceeds the bounded record size")
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if state == "start":
                        if stripped == b"[":
                            state = "open"
                            continue
                        if stripped == b"[]":
                            state = "closed"
                            continue
                        raise LineFormatError("first non-blank line is not a bare '['")
                    if state == "closed":
                        raise ValueError("data after the closing ']' of the Spansh array")
                    if stripped == b"]":
                        if pending_comma:
                            raise ValueError("trailing comma before the closing ']'")
                        state = "closed"
                        continue
                    if systems and not pending_comma:
                        raise ValueError("Spansh records are not comma separated")
                    pending_comma = stripped.endswith(b",")
                    payload = stripped[:-1] if pending_comma else stripped
                    try:
                        record = json.loads(payload, parse_constant=_reject_constant)
                    except json.JSONDecodeError as exc:
                        raise LineFormatError(
                            f"line after system {systems} is not one compact JSON object: {exc.msg}"
                        ) from exc
                    systems += 1
                    yield SpanshRecord(_validated(record), len(payload), systems)
                if state != "closed":
                    raise ValueError("Spansh artifact ended before its closing ']' (truncated)")
            finally:
                if decoded is not None:
                    decoded.close()
            self.receipt = StreamReceipt(
                file_name=self.path.name, compressed=compressed,
                file_bytes=hashing.bytes_read, file_sha256=hashing.digest.hexdigest(),
                decompressed_bytes=decompressed, systems=systems, parser="lines",
                fallback_after_records=None, fallback_reason=None,
            )

    # -- format-agnostic ijson parser -----------------------------------
    def _run_ijson(self, compressed: bool, *, skip: int, skip_digest: str | None,
                   fallback_reason: str | None) -> Iterator[SpanshRecord]:
        import ijson

        replay = hashlib.sha256()
        with self.path.open("rb") as raw:
            hashing = _HashingReader(raw)
            decoded: Any = gzip.GzipFile(fileobj=hashing, mode="rb") if compressed else hashing
            counter = _CountingArrayReader(decoded)
            systems = 0
            try:
                for record in ijson.items(counter, "item", use_float=True, buf_size=_READ_SIZE):
                    counter.item_done()
                    record = _validated(record)
                    systems += 1
                    if systems <= skip:
                        replay.update(record["id64"].to_bytes(8, "little"))
                        if systems == skip and replay.hexdigest() != skip_digest:
                            raise ValueError("ijson replay does not match the systems already yielded")
                        continue
                    yield SpanshRecord(record, _compact_size(record), systems)
                if counter.read(1):
                    raise ValueError("unconsumed data after the Spansh array")
            except EOFError as exc:
                raise ValueError("Spansh gzip stream is truncated") from exc
            except ijson.JSONError as exc:
                raise ValueError(f"invalid Spansh JSON: {exc}") from exc
            finally:
                if compressed:
                    decoded.close()
            if systems < skip:
                raise ValueError("ijson replay found fewer systems than were already yielded")
            if not counter.checked:
                raise ValueError("Spansh artifact must be a JSON array")
            self.receipt = StreamReceipt(
                file_name=self.path.name, compressed=compressed,
                file_bytes=hashing.bytes_read, file_sha256=hashing.digest.hexdigest(),
                decompressed_bytes=counter.bytes_read, systems=systems,
                parser="ijson" if fallback_reason is None else "ijson-fallback",
                fallback_after_records=skip if fallback_reason is not None else None,
                fallback_reason=fallback_reason,
            )
