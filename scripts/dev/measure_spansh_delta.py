#!/usr/bin/env python3
"""Measure one Spansh full-detail delta dump (galaxy_1day / _7days / _1month).

Streams the file one system at a time (no database, no network), maps every
system through the frozen V3 canonical adapter, and writes JSON statistics:
systems, bodies, fixed stations and fleet carriers, bytes, update-time
distributions, a timestamp-based admission proxy and -- when the previous
night's digest index or file is supplied -- the overlap between consecutive
nights and how many repeat systems actually changed V3 content.

Memory: one system at a time, plus a 40-byte digest record per system for the
overlap index (about 80 MB for two million systems).  See
docs/development/spansh-delta-measurement.md for how to read the output.
"""

from __future__ import annotations

import argparse
from array import array
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import struct
import sys
import time
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/importer/src"))

from spansh_delta import (  # noqa: E402
    DIGEST_NAMES, SpanshStream, content_digests, normalize_system, record_touch_profile,
)

STATS_SCHEMA = "ed-finder.spansh-delta-stats/1"
INDEX_MAGIC = b"EDF-SPANSH-DELTA-INDEX/1\n"
DIGESTS = DIGEST_NAMES
INDEX_RECORD = struct.Struct("<q" + "Q" * len(DIGESTS))
MAX_DISTINCT_DATES = 4000
MAX_DISTINCT_UNMAPPED = 2000
MAX_ERROR_SAMPLES = 20
COUNT_EDGES = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)
BYTE_EDGES = tuple(2**power for power in range(8, 29))  # 256 B .. 256 MiB
AGE_EDGES_HOURS = (1, 2, 4, 8, 12, 16, 20, 24, 30, 36, 48, 72, 168, 336, 720, 2160, 8760)


class MeasurementError(RuntimeError):
    """A fail-closed refusal (bad input, size mismatch, malformed index)."""


# -- small bounded accumulators ----------------------------------------------

class Histogram:
    """Fixed-edge histogram; memory does not grow with the input."""

    def __init__(self, edges: Iterable[float], unit: str):
        self.edges = tuple(edges)
        self.unit = unit
        self.counts = [0] * (len(self.edges) + 1)
        self.n = 0
        self.total = 0.0
        self.minimum: float | None = None
        self.maximum: float | None = None

    def add(self, value: float) -> None:
        index = 0
        while index < len(self.edges) and value >= self.edges[index]:
            index += 1
        self.counts[index] += 1
        self.n += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    def _label(self, index: int) -> str:
        if index == 0:
            return f"<{_fmt(self.edges[0])}"
        if index == len(self.edges):
            return f">={_fmt(self.edges[-1])}"
        return f"{_fmt(self.edges[index - 1])}-<{_fmt(self.edges[index])}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "n": self.n,
            "mean": round(self.total / self.n, 3) if self.n else None,
            "min": self.minimum,
            "max": self.maximum,
            "buckets": {self._label(index): count for index, count in enumerate(self.counts) if count},
        }


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


class TimeProfile:
    """Age (hours before the reference time) and UTC-date counts for one timestamp kind."""

    def __init__(self, reference: datetime):
        self.reference = reference
        self.ages = Histogram(AGE_EDGES_HOURS, "hours_before_reference")
        self.missing = 0
        self.future = 0
        self.dates: Counter[str] = Counter()
        self.dates_overflow = 0
        self.earliest: datetime | None = None
        self.latest: datetime | None = None

    def add(self, value: datetime | None) -> None:
        if value is None:
            self.missing += 1
            return
        hours = (self.reference - value).total_seconds() / 3600
        if hours < 0:
            self.future += 1
        else:
            self.ages.add(round(hours, 3))
        day = value.date().isoformat()
        if day in self.dates or len(self.dates) < MAX_DISTINCT_DATES:
            self.dates[day] += 1
        else:
            self.dates_overflow += 1
        self.earliest = value if self.earliest is None else min(self.earliest, value)
        self.latest = value if self.latest is None else max(self.latest, value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "missing": self.missing,
            "after_reference": self.future,
            "earliest": _iso(self.earliest),
            "latest": _iso(self.latest),
            "age": self.ages.as_dict(),
            "by_utc_date": dict(sorted(self.dates.items())),
            "by_utc_date_overflow": self.dates_overflow,
        }


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


# -- the overlap digest index -------------------------------------------------

class DigestIndex:
    """id64 -> first 8 bytes of three content digests, in typed arrays."""

    def __init__(self) -> None:
        self.ids = array("q")
        self.columns = {name: array("Q") for name in DIGESTS}
        self.duplicates = 0
        self.sorted = False

    def __len__(self) -> int:
        return len(self.ids)

    def add(self, id64: int, digests: dict[str, bytes]) -> None:
        self.ids.append(id64)
        for name in DIGESTS:
            self.columns[name].append(int.from_bytes(digests[name][:8], "little"))
        self.sorted = False

    def finish(self) -> None:
        """Sort by id64; a repeated id64 keeps its last occurrence."""
        if self.sorted:
            return
        order = sorted(range(len(self.ids)), key=self.ids.__getitem__)
        ids = array("q")
        columns = {name: array("Q") for name in DIGESTS}
        duplicates = 0
        for position in order:
            identifier = self.ids[position]
            if ids and ids[-1] == identifier:
                duplicates += 1
                for name in DIGESTS:
                    columns[name][-1] = self.columns[name][position]
                continue
            ids.append(identifier)
            for name in DIGESTS:
                columns[name].append(self.columns[name][position])
        del order
        self.ids, self.columns, self.duplicates, self.sorted = ids, columns, duplicates, True

    def write(self, path: Path, header: dict[str, Any]) -> None:
        self.finish()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".partial")
        with temporary.open("wb") as handle:
            handle.write(INDEX_MAGIC)
            handle.write(json.dumps({**header, "records": len(self.ids)}, sort_keys=True).encode() + b"\n")
            for position, identifier in enumerate(self.ids):
                handle.write(INDEX_RECORD.pack(identifier, *(self.columns[name][position] for name in DIGESTS)))
        os.replace(temporary, path)

    @classmethod
    def read(cls, path: Path) -> tuple[dict[str, Any], "DigestIndex"]:
        index = cls()
        with path.open("rb") as handle:
            if handle.read(len(INDEX_MAGIC)) != INDEX_MAGIC:
                raise MeasurementError(f"not a spansh delta index: {path}")
            line = handle.readline(1 << 20)
            try:
                header = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MeasurementError(f"malformed index header: {path}") from exc
            records = header.get("records")
            if type(records) is not int or records < 0:
                raise MeasurementError(f"malformed index record count: {path}")
            previous = None
            for _ in range(records):
                chunk = handle.read(INDEX_RECORD.size)
                if len(chunk) != INDEX_RECORD.size:
                    raise MeasurementError(f"truncated index: {path}")
                identifier, *values = INDEX_RECORD.unpack(chunk)
                if previous is not None and identifier <= previous:
                    raise MeasurementError(f"index is not strictly ascending: {path}")
                previous = identifier
                index.ids.append(identifier)
                for name, value in zip(DIGESTS, values, strict=True):
                    index.columns[name].append(value)
            if handle.read(1):
                raise MeasurementError(f"trailing bytes in index: {path}")
        index.sorted = True
        return header, index


def overlap(current: DigestIndex, previous: DigestIndex) -> dict[str, Any]:
    current.finish()
    previous.finish()
    both = only_current = only_previous = 0
    changed = dict.fromkeys(DIGESTS, 0)
    left = right = 0
    while left < len(current.ids) and right < len(previous.ids):
        a, b = current.ids[left], previous.ids[right]
        if a == b:
            both += 1
            for name in DIGESTS:
                if current.columns[name][left] != previous.columns[name][right]:
                    changed[name] += 1
            left += 1
            right += 1
        elif a < b:
            only_current += 1
            left += 1
        else:
            only_previous += 1
            right += 1
    only_current += len(current.ids) - left
    only_previous += len(previous.ids) - right
    union = both + only_current + only_previous
    return {
        "systems_in_both": both,
        "only_in_current": only_current,
        "only_in_previous": only_previous,
        "jaccard": round(both / union, 6) if union else None,
        "content_changed_in_both": changed,
        "content_unchanged_in_both": {name: both - count for name, count in changed.items()},
    }


# -- input helpers --------------------------------------------------------------

def parse_http_headers(path: Path) -> dict[str, str]:
    """The final response block of a ``curl -D`` dump, lower-cased names."""
    text = path.read_text(encoding="latin-1")
    blocks = [block for block in text.replace("\r\n", "\n").split("\n\n") if block.strip()]
    final = [block for block in blocks if block.lstrip().startswith("HTTP/")]
    if not final:
        raise MeasurementError(f"no HTTP response in {path}")
    lines = final[-1].strip().split("\n")
    headers = {"status": lines[0].strip()}
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if separator:
            headers[name.strip().lower()] = value.strip()
    return headers


def parse_reference(text: str) -> datetime:
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        from email.utils import parsedate_to_datetime
        try:
            value = parsedate_to_datetime(text)
        except (TypeError, ValueError) as exc:
            raise MeasurementError(f"unparseable reference time: {text!r}") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _peak_rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak) * (1 if sys.platform == "darwin" else 1024)


def _progress(label: str, systems: int, started: float) -> None:
    elapsed = time.monotonic() - started
    rate = systems / elapsed if elapsed else 0.0
    print(f"[{label}] {systems:,} systems, {elapsed:,.0f}s, {rate:,.0f}/s", file=sys.stderr, flush=True)


def build_index(path: Path, parser: str) -> tuple[dict[str, Any], DigestIndex]:
    """Digest index of a previous night's file (no other statistics)."""
    stream = SpanshStream(path, parser=parser)
    index = DigestIndex()
    started = time.monotonic()
    for item in stream:
        try:
            system = normalize_system(item.record)
        except (ValueError, TypeError, KeyError):
            continue
        index.add(system.id64, content_digests(system))
        if len(index) % 250_000 == 0:
            _progress("previous", len(index), started)
    assert stream.receipt is not None
    index.finish()
    return {"source": "file", **stream.receipt.as_dict()}, index


# -- the measurement -------------------------------------------------------------

def measure(args: argparse.Namespace) -> dict[str, Any]:
    source: Path = args.input
    if not source.is_file():
        raise MeasurementError(f"input file not found: {source}")
    headers = parse_http_headers(args.http_headers) if args.http_headers else None
    file_bytes = source.stat().st_size
    if headers and "content-length" in headers:
        try:
            declared = int(headers["content-length"])
        except ValueError as exc:
            raise MeasurementError("invalid Content-Length header") from exc
        if declared != file_bytes:
            raise MeasurementError(f"download size {file_bytes} does not match Content-Length {declared}")
    if args.reference_time:
        reference, reference_source = parse_reference(args.reference_time), "argument"
    elif headers and headers.get("last-modified"):
        reference, reference_source = parse_reference(headers["last-modified"]), "http_last_modified"
    else:
        reference = datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc)
        reference_source = "file_mtime"
    window_start = reference - timedelta(hours=args.window_hours)

    stream = SpanshStream(source, parser=args.parser)
    index = DigestIndex()
    started = time.monotonic()
    systems = 0
    rejections: Counter[str] = Counter()
    rejection_samples: list[dict[str, Any]] = []
    rows: Counter[str] = Counter()
    raw: Counter[str] = Counter()
    population: Counter[str] = Counter()
    touch: Counter[str] = Counter()
    unmapped: Counter[str] = Counter()
    unmapped_overflow = 0
    per_system = {
        "bodies": Histogram(COUNT_EDGES, "bodies"),
        "fixed_stations": Histogram(COUNT_EDGES, "stations"),
        "fleet_carriers": Histogram(COUNT_EDGES, "carriers"),
        "record_bytes": Histogram(BYTE_EDGES, "bytes"),
    }
    times = {name: TimeProfile(reference) for name in (
        "system_date", "latest_body_update", "latest_fixed_station_update",
        "latest_carrier_update", "latest_v3_relevant_update")}

    for item in stream:
        systems += 1
        record = item.record
        if item.raw_bytes is not None:
            per_system["record_bytes"].add(item.raw_bytes)
        raw["station_objects"] += len(record.get("stations") or [])
        for body in record.get("bodies") or []:
            if isinstance(body, dict):
                raw["body_objects"] += 1
                raw["station_objects"] += len(body.get("stations") or [])
        profile = record_touch_profile(record, window_start=window_start)
        touch[profile.category] += 1
        times["system_date"].add(profile.system_time)
        times["latest_body_update"].add(profile.latest_body_time)
        times["latest_fixed_station_update"].add(profile.latest_fixed_station_time)
        times["latest_carrier_update"].add(profile.latest_carrier_time)
        times["latest_v3_relevant_update"].add(profile.latest_v3_time)
        try:
            system = normalize_system(record)
        except (ValueError, TypeError, KeyError) as exc:
            reason = f"{type(exc).__name__}: {str(exc).split(':')[0][:80]}"
            rejections[reason] += 1
            if len(rejection_samples) < MAX_ERROR_SAMPLES:
                rejection_samples.append({"id64": record.get("id64"), "reason": str(exc)[:200]})
            continue
        for table, table_rows in system.tables.items():
            rows[table] += len(table_rows)
        bodies, fixed, carriers = system.count("bodies"), system.fixed_stations, system.fleet_carriers
        per_system["bodies"].add(bodies)
        per_system["fixed_stations"].add(fixed)
        per_system["fleet_carriers"].add(carriers)
        population["with_bodies" if bodies else "without_bodies"] += 1
        if fixed:
            population["with_fixed_stations"] += 1
        if carriers:
            population["with_fleet_carriers"] += 1
            if not fixed:
                population["carriers_but_no_fixed_station"] += 1
        for domain, token in system.unmapped_vocabulary:
            key = f"{domain}:{token}"
            if key in unmapped or len(unmapped) < MAX_DISTINCT_UNMAPPED:
                unmapped[key] += 1
            else:
                unmapped_overflow += 1
        index.add(system.id64, content_digests(system))
        if systems % 250_000 == 0:
            _progress("current", systems, started)
    receipt = stream.receipt
    if receipt is None:  # pragma: no cover - the stream raises instead
        raise MeasurementError("stream ended without an EOF receipt")
    index.finish()
    elapsed = time.monotonic() - started

    stats: dict[str, Any] = {
        "schema": STATS_SCHEMA,
        "measured_at": _iso(datetime.now(timezone.utc)),
        "source": {
            **receipt.as_dict(),
            "http": {key: headers[key] for key in ("status", "etag", "last-modified", "content-length", "date")
                     if headers and key in headers} or None,
            "reference_time": _iso(reference),
            "reference_time_source": reference_source,
            "window_hours": args.window_hours,
        },
        "run": {
            "elapsed_seconds": round(elapsed, 3),
            "systems_per_second": round(systems / elapsed, 1) if elapsed else None,
            "peak_rss_bytes": _peak_rss_bytes(),
            "python": sys.version.split()[0],
        },
        "counts": {
            "systems": systems,
            "systems_normalized": systems - sum(rejections.values()),
            "systems_rejected_by_v3_adapter": sum(rejections.values()),
            "distinct_id64": len(index),
            "duplicate_id64_records": index.duplicates,
            "bodies": rows["bodies"],
            "fixed_stations": int(per_system["fixed_stations"].total),
            "fleet_carriers": int(per_system["fleet_carriers"].total),
            "raw_body_objects": raw["body_objects"],
            "raw_station_objects": raw["station_objects"],
            "systems_by_content": dict(sorted(population.items())),
        },
        "bytes": {
            "file_bytes": receipt.file_bytes,
            "decompressed_bytes": receipt.decompressed_bytes,
            "file_bytes_per_system": round(receipt.file_bytes / systems, 1) if systems else None,
            "decompressed_bytes_per_system": round(receipt.decompressed_bytes / systems, 1) if systems else None,
        },
        "per_system": {name: histogram.as_dict() for name, histogram in per_system.items()},
        "canonical_rows": dict(sorted(rows.items())),
        "update_times": {name: profile.as_dict() for name, profile in times.items()},
        "touch_proxy": {
            "window_start": _iso(window_start),
            "categories": dict(sorted(touch.items())),
        },
        "v3_adapter_rejections": {
            "by_reason": dict(rejections.most_common(50)),
            "samples": rejection_samples,
        },
        "unmapped_vocabulary": {
            "top": dict(unmapped.most_common(50)),
            "distinct": len(unmapped),
            "overflow": unmapped_overflow,
        },
        "overlap": None,
        "previous_stats": None,
    }

    previous_header: dict[str, Any] | None = None
    previous_index: DigestIndex | None = None
    if args.previous_index:
        previous_header, previous_index = DigestIndex.read(args.previous_index)
        previous_header = {"source": "index", **previous_header}
    elif args.previous_file:
        previous_header, previous_index = build_index(args.previous_file, args.parser)
    if previous_index is not None and previous_header is not None:
        stats["overlap"] = {
            "previous": {key: previous_header.get(key) for key in (
                "source", "file_name", "file_sha256", "reference_time", "systems")},
            "same_source_file": previous_header.get("file_sha256") == receipt.file_sha256,
            **overlap(index, previous_index),
        }
    if args.previous_stats:
        previous = json.loads(args.previous_stats.read_text(encoding="utf-8"))
        if previous.get("schema") != STATS_SCHEMA:
            raise MeasurementError("previous stats file has an unknown schema")
        previous_source = previous.get("source")
        previous_counts = previous.get("counts")
        if not isinstance(previous_source, dict) or not isinstance(previous_counts, dict) \
                or type(previous_counts.get("systems")) is not int:
            raise MeasurementError("previous stats file lacks source/counts.systems")
        stats["previous_stats"] = {
            "file_sha256": previous_source.get("file_sha256"),
            "reference_time": previous_source.get("reference_time"),
            "same_source_file": previous_source.get("file_sha256") == receipt.file_sha256,
            "systems": previous_counts.get("systems"),
            "systems_change": systems - previous_counts["systems"],
        }
    if args.write_index:
        index.write(args.write_index, {
            "schema": INDEX_MAGIC.decode().strip(),
            "file_name": receipt.file_name,
            "file_sha256": receipt.file_sha256,
            "reference_time": _iso(reference),
            "systems": systems,
            "digests": list(DIGESTS),
        })
    return stats


def markdown_summary(stats: dict[str, Any]) -> str:
    source, counts, touch = stats["source"], stats["counts"], stats["touch_proxy"]["categories"]
    lines = [
        f"### Spansh delta measurement: `{source['file_name']}`",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| File bytes | {source['file_bytes']:,} |",
        f"| Decompressed bytes | {source['decompressed_bytes']:,} |",
        f"| Parser | {source['parser']} |",
        f"| Reference time ({source['reference_time_source']}) | {source['reference_time']} |",
        f"| Systems | {counts['systems']:,} |",
        f"| Rejected by V3 adapter | {counts['systems_rejected_by_v3_adapter']:,} |",
        f"| Bodies | {counts['bodies']:,} |",
        f"| Fixed stations | {counts['fixed_stations']:,} |",
        f"| Fleet carriers | {counts['fleet_carriers']:,} |",
        f"| Elapsed seconds | {stats['run']['elapsed_seconds']:,} |",
    ]
    for category, count in touch.items():
        lines.append(f"| Touch proxy: {category} | {count:,} |")
    if stats["overlap"]:
        overlap_stats = stats["overlap"]
        lines.append(f"| Systems also in previous night | {overlap_stats['systems_in_both']:,} |")
        for name, count in overlap_stats["content_changed_in_both"].items():
            lines.append(f"| Repeat systems with changed content ({name}) | {count:,} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, required=True, help="galaxy*.json.gz (or plain .json)")
    parser.add_argument("--output", type=Path, help="stats JSON path (default: stdout)")
    parser.add_argument("--parser", choices=("auto", "lines", "ijson"), default="auto")
    parser.add_argument("--http-headers", type=Path, help="curl -D header dump of the download")
    parser.add_argument("--reference-time", help="ISO-8601 or HTTP date; default Last-Modified, else file mtime")
    parser.add_argument("--window-hours", type=float, default=24.0,
                        help="touch-proxy window before the reference time (24 for galaxy_1day)")
    parser.add_argument("--write-index", type=Path, help="write tonight's digest index here")
    previous = parser.add_mutually_exclusive_group()
    previous.add_argument("--previous-index", type=Path, help="previous night's digest index")
    previous.add_argument("--previous-file", type=Path, help="previous night's delta file")
    parser.add_argument("--previous-stats", type=Path, help="previous night's stats JSON")
    parser.add_argument("--markdown-summary", type=Path, help="append a Markdown summary here")
    args = parser.parse_args(argv)
    if args.window_hours <= 0:
        parser.error("--window-hours must be positive")
    try:
        stats = measure(args)
    except (MeasurementError, ValueError, OSError) as exc:
        print(f"spansh delta measurement refused: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(stats, indent=2, sort_keys=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    if args.markdown_summary:
        with args.markdown_summary.open("a", encoding="utf-8") as handle:
            handle.write(markdown_summary(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
