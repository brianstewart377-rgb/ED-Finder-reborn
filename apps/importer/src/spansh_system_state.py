#!/usr/bin/env python3
"""Turn Spansh galaxy dumps into a load stream for the v3_state tables.

Reads one or more Spansh dumps (galaxy_stations.json.gz,
galaxy_populated.json.gz, or any dump in the same one-system-per-line format)
and writes a psql script to standard output. Pipe that into psql:

    python3 spansh_system_state.py --snapshot galaxy_stations.json.gz | psql ...

A system is kept when it has a population or at least one station that is not
a fleet carrier. Everything else in the dump is skipped.

The whole load is one transaction. If this program stops early for any reason
(bad file, cut-off download, Ctrl-C) the stream ends without COMMIT and the
database is left exactly as it was.

Standard library only, so it runs on any machine with Python 3.8 or newer.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import time
from typing import Any, BinaryIO, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

STAGE_COLUMNS: Tuple[str, ...] = (
    "seq", "system_id64", "name", "x_ly", "y_ly", "z_ly", "population",
    "allegiance", "government", "security", "primary_economy", "secondary_economy",
    "controlling_faction", "controlling_faction_state", "controlling_power",
    "power_state", "power_state_control_progress", "power_state_reinforcement",
    "power_state_undermining", "station_type_counts", "station_count",
    "carrier_count", "construction_site_count", "colonisation_ship_present",
    "field_timestamps", "system_updated_at", "content_hash", "factions", "powers",
)

UNKNOWN_STATION_TYPE = "Unknown"
# A load is abandoned when more than 1% of a file's records (and more than
# this many) cannot be used.
MAX_BAD_RECORDS_FLOOR = 10

_TIMESTAMP = re.compile(
    r"^(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)(?:\.\d+)?(?:Z|[+-]00(?::?00)?)?$"
)
_CARRIER_CALLSIGN = re.compile(r"^[A-Z0-9]{3}-[A-Z0-9]{3}$")
_FACTION_STATE_LISTS = ("activeStates", "pendingStates", "recoveringStates")


class SkipSystem(Exception):
    """Raised when a source record cannot be turned into a row."""


def normalise_timestamp(value: Any) -> Optional[str]:
    """Return a UTC timestamp as 'YYYY-MM-DD HH:MM:SS+00', or None.

    Spansh writes UTC in two spellings ('2026-09-07 18:50:19+00' and
    '2026-09-07T18:48:58Z'). The normal form sorts correctly as text, so the
    newest of several timestamps is simply max().
    """
    if not isinstance(value, str):
        return None
    match = _TIMESTAMP.match(value.strip())
    if not match:
        return None
    return "%s %s+00" % (match.group(1), match.group(2))


def _text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.replace("\x00", "").strip()
    return cleaned or None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _whole(value: Any) -> Optional[int]:
    number = _number(value)
    return None if number is None else int(number)


def is_fleet_carrier(station: Dict[str, Any]) -> bool:
    station_type = _text(station.get("type"))
    if station_type is not None:
        return "carrier" in station_type.casefold()
    name = _text(station.get("name")) or ""
    return bool(_CARRIER_CALLSIGN.match(name))


def iter_stations(system: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """Every station in a system: the top-level list plus those under bodies."""
    seen: set = set()

    def unseen(station: Any) -> bool:
        if not isinstance(station, dict):
            return False
        station_id = station.get("id")
        if isinstance(station_id, bool) or not isinstance(station_id, (int, str)):
            return True
        if station_id in seen:
            return False
        seen.add(station_id)
        return True

    for station in system.get("stations") or []:
        if unseen(station):
            yield station
    for body in system.get("bodies") or []:
        if not isinstance(body, dict):
            continue
        for station in body.get("stations") or []:
            if unseen(station):
                yield station


def _faction_rows(system: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for faction in system.get("factions") or []:
        if not isinstance(faction, dict):
            continue
        name = _text(faction.get("name"))
        if name is None:
            continue
        row: Dict[str, Any] = {
            "name": name,
            "allegiance": _text(faction.get("allegiance")),
            "government": _text(faction.get("government")),
            "influence": _number(faction.get("influence")),
            "state": _text(faction.get("state")),
        }
        for key in _FACTION_STATE_LISTS:
            value = faction.get(key)
            if isinstance(value, list) and value:
                row[key] = value
        rows.append(row)
    rows.sort(key=lambda item: item["name"])
    return rows


def _power_rows(system: Dict[str, Any], controlling_power: Optional[str]) -> List[Dict[str, Any]]:
    progress: Dict[str, Optional[float]] = {}
    for power in system.get("powers") or []:
        name = _text(power)
        if name is not None:
            progress.setdefault(name, None)
    for entry in system.get("powerConflictProgress") or []:
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("power"))
        if name is not None:
            progress[name] = _number(entry.get("progress"))
    if controlling_power is not None:
        progress.setdefault(controlling_power, None)
    return [
        {"power": name, "conflict_progress": progress[name]}
        for name in sorted(progress)
    ]


def system_row(system: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build one staging row from a Spansh system record.

    Returns None when the system has neither a population nor a non-carrier
    station (nothing worth storing). Raises SkipSystem when the record is
    unusable (no id, name, coordinates or timestamp).
    """
    if not isinstance(system, dict):
        raise SkipSystem("record is not an object")
    system_id64 = system.get("id64")
    if isinstance(system_id64, bool) or not isinstance(system_id64, int):
        raise SkipSystem("missing id64")
    name = _text(system.get("name"))
    if name is None:
        raise SkipSystem("missing name")
    coords = system.get("coords")
    if not isinstance(coords, dict):
        raise SkipSystem("missing coords")
    x, y, z = (_number(coords.get(axis)) for axis in ("x", "y", "z"))
    if x is None or y is None or z is None:
        raise SkipSystem("missing coords")

    population = _whole(system.get("population")) or 0
    if population < 0:
        population = 0

    type_counts: Dict[str, int] = {}
    station_count = 0
    carrier_count = 0
    construction_site_count = 0
    colonisation_ship_present = False
    timestamps: List[str] = []
    for station in iter_stations(system):
        if is_fleet_carrier(station):
            carrier_count += 1
            continue
        station_count += 1
        station_type = _text(station.get("type")) or UNKNOWN_STATION_TYPE
        type_counts[station_type] = type_counts.get(station_type, 0) + 1
        folded_type = station_type.casefold()
        folded_name = (_text(station.get("name")) or "").casefold()
        if "construction" in folded_type:
            construction_site_count += 1
        if ("colonisation" in folded_type or "colonisation ship" in folded_name
                or "colonisationship" in folded_name
                or "colonization ship" in folded_name or "colonization" in folded_type):
            colonisation_ship_present = True
        updated = normalise_timestamp(station.get("updateTime"))
        if updated is not None:
            timestamps.append(updated)

    if population == 0 and station_count == 0:
        return None

    field_timestamps: Dict[str, str] = {}
    raw_timestamps = system.get("timestamps")
    if isinstance(raw_timestamps, dict):
        for key in sorted(raw_timestamps):
            normalised = normalise_timestamp(raw_timestamps[key])
            if normalised is not None and isinstance(key, str):
                field_timestamps[key] = normalised
                timestamps.append(normalised)
    system_date = normalise_timestamp(system.get("date"))
    if system_date is not None:
        timestamps.append(system_date)
    if not timestamps:
        raise SkipSystem("no usable timestamp")

    controlling = system.get("controllingFaction")
    controlling_faction: Optional[str] = None
    controlling_faction_state: Optional[str] = None
    if isinstance(controlling, dict):
        controlling_faction = _text(controlling.get("name"))
        controlling_faction_state = _text(controlling.get("state"))
    else:
        controlling_faction = _text(controlling)
    controlling_power = _text(system.get("controllingPower"))

    row: Dict[str, Any] = {
        "system_id64": system_id64,
        "name": name,
        "x_ly": x,
        "y_ly": y,
        "z_ly": z,
        "population": population,
        "allegiance": _text(system.get("allegiance")),
        "government": _text(system.get("government")),
        "security": _text(system.get("security")),
        "primary_economy": _text(system.get("primaryEconomy")),
        "secondary_economy": _text(system.get("secondaryEconomy")),
        "controlling_faction": controlling_faction,
        "controlling_faction_state": controlling_faction_state,
        "controlling_power": controlling_power,
        "power_state": _text(system.get("powerState")),
        "power_state_control_progress": _number(system.get("powerStateControlProgress")),
        "power_state_reinforcement": _whole(system.get("powerStateReinforcement")),
        "power_state_undermining": _whole(system.get("powerStateUndermining")),
        "station_type_counts": dict(sorted(type_counts.items())),
        "station_count": station_count,
        "carrier_count": carrier_count,
        "construction_site_count": construction_site_count,
        "colonisation_ship_present": colonisation_ship_present,
        "field_timestamps": field_timestamps,
        "system_updated_at": max(timestamps),
        "factions": _faction_rows(system),
        "powers": _power_rows(system, controlling_power),
    }
    row["content_hash"] = content_hash(row)
    return row


def _strip_nul(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nul(item) for item in value]
    if isinstance(value, dict):
        return {_strip_nul(key): _strip_nul(item) for key, item in value.items()}
    return value


def _json(value: Any) -> str:
    return json.dumps(_strip_nul(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def content_hash(row: Dict[str, Any]) -> str:
    """Fingerprint of everything stored for a system (not seq or the hash)."""
    payload = {key: row[key] for key in STAGE_COLUMNS if key not in ("seq", "content_hash")}
    return hashlib.sha1(_json(payload).encode("utf-8")).hexdigest()


def _copy_field(value: Any) -> str:
    """One value in PostgreSQL COPY text format."""
    if value is None:
        return "\\N"
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (dict, list)):
        value = _json(value)
    text = str(value).replace("\x00", "")
    return (text.replace("\\", "\\\\").replace("\t", "\\t")
                .replace("\n", "\\n").replace("\r", "\\r"))


def copy_line(seq: int, row: Dict[str, Any]) -> str:
    values = dict(row)
    values["seq"] = seq
    return "\t".join(_copy_field(values[column]) for column in STAGE_COLUMNS) + "\n"


def iter_dump_records(stream: Iterable[bytes]) -> Iterator[Tuple[int, Any]]:
    """Yield (line number, decoded record) from a Spansh dump.

    Spansh dumps are a JSON array with one system object per line. Plain JSON
    Lines (no surrounding brackets) is read the same way.
    """
    for line_number, line in enumerate(stream, start=1):
        payload = line.strip()
        if not payload or payload in (b"[", b"]"):
            continue
        if payload.endswith(b","):
            payload = payload[:-1]
        try:
            record = json.loads(payload)
        except ValueError:
            yield line_number, None
            continue
        if isinstance(record, dict) and isinstance(record.get("system"), dict) and "id64" not in record:
            record = record["system"]
        yield line_number, record


def open_dump(path: str) -> BinaryIO:
    if path == "-":
        return sys.stdin.buffer
    with open(path, "rb") as probe:
        magic = probe.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rb")  # type: ignore[return-value]
    return open(path, "rb")


def stream_header() -> str:
    return (
        "\\set ON_ERROR_STOP on\n"
        "SET client_encoding = 'UTF8';\n"
        "BEGIN;\n"
        "SET LOCAL statement_timeout = 0;\n"
        "SET LOCAL lock_timeout = '60s';\n"
        "TRUNCATE v3_state.system_stage;\n"
        "COPY v3_state.system_stage (%s) FROM stdin;\n" % ", ".join(STAGE_COLUMNS)
    )


def stream_footer(snapshot: bool, source_label: str) -> str:
    quoted = "'" + source_label.replace("\x00", "").replace("'", "''") + "'"
    return (
        "\\.\n"
        "SELECT * FROM v3_state.apply_stage(%s, %s);\n"
        "TRUNCATE v3_state.system_stage;\n"
        "COMMIT;\n" % ("true" if snapshot else "false", quoted)
    )


class LoadStats:
    def __init__(self) -> None:
        self.files: List[Dict[str, Any]] = []
        self.kept_ids_by_file: List[set] = []
        self.populated_ids_by_file: List[set] = []

    def start_file(self, path: str) -> Dict[str, Any]:
        entry = {"file": path, "records": 0, "kept": 0, "no_state": 0,
                 "unusable": 0, "unreadable_lines": 0}
        self.files.append(entry)
        self.kept_ids_by_file.append(set())
        self.populated_ids_by_file.append(set())
        return entry

    def summary_lines(self) -> List[str]:
        lines: List[str] = []
        for index, entry in enumerate(self.files):
            lines.append(
                "%s: %d systems read, %d kept, %d skipped (no population and no "
                "station), %d unusable records, %d unreadable lines" % (
                    os.path.basename(entry["file"]), entry["records"], entry["kept"],
                    entry["no_state"], entry["unusable"], entry["unreadable_lines"]))
            if len(self.files) > 1:
                others_kept: set = set()
                for other, ids in enumerate(self.kept_ids_by_file):
                    if other != index:
                        others_kept |= ids
                only_here = self.kept_ids_by_file[index] - others_kept
                populated_only_here = self.populated_ids_by_file[index] - others_kept
                lines.append(
                    "%s: %d kept systems appear in no other input file "
                    "(%d of them have a population)" % (
                        os.path.basename(entry["file"]), len(only_here),
                        len(populated_only_here)))
        all_kept: set = set()
        for ids in self.kept_ids_by_file:
            all_kept |= ids
        lines.append("total: %d different systems staged" % len(all_kept))
        return lines


def write_load_stream(paths: Sequence[str], out: BinaryIO, *, snapshot: bool,
                      source_label: str, log: Any = None,
                      progress_every: int = 25_000) -> LoadStats:
    """Write the full psql load script for the given dumps to `out`."""
    stats = LoadStats()
    started = time.monotonic()
    seq = 0
    out.write(stream_header().encode("utf-8"))
    for path in paths:
        entry = stats.start_file(path)
        if log is not None:
            print("Reading %s (a full dump takes several minutes) ..." % os.path.basename(path),
                  file=log, flush=True)
        kept_ids = stats.kept_ids_by_file[-1]
        populated_ids = stats.populated_ids_by_file[-1]
        handle = open_dump(path)
        try:
            for _line_number, record in iter_dump_records(handle):
                if record is None:
                    entry["unreadable_lines"] += 1
                    continue
                entry["records"] += 1
                try:
                    row = system_row(record)
                    line = None if row is None else copy_line(seq + 1, row).encode("utf-8")
                except (SkipSystem, ValueError, TypeError, OverflowError) as error:
                    # One odd record must not sink a whole load; too many do
                    # (see the check after the file is read).
                    entry["unusable"] += 1
                    if log is not None and entry["unusable"] <= 5:
                        print("  unusable record at line %d: %s: %s" % (
                            _line_number, type(error).__name__, error), file=log, flush=True)
                    continue
                if row is None or line is None:
                    entry["no_state"] += 1
                else:
                    seq += 1
                    out.write(line)
                    entry["kept"] += 1
                    kept_ids.add(row["system_id64"])
                    if row["population"] > 0:
                        populated_ids.add(row["system_id64"])
                if log is not None and progress_every and entry["records"] % progress_every == 0:
                    print("  %s: %d systems read, %d kept, %.0f s" % (
                        os.path.basename(path), entry["records"], entry["kept"],
                        time.monotonic() - started), file=log, flush=True)
        finally:
            if handle is not sys.stdin.buffer:
                handle.close()
        bad = entry["unusable"] + entry["unreadable_lines"]
        if bad > max(MAX_BAD_RECORDS_FLOOR, entry["records"] // 100):
            raise RuntimeError(
                "%s: %d of %d records could not be used; that is too many to trust "
                "the file" % (os.path.basename(path), bad,
                              entry["records"] + entry["unreadable_lines"]))
    if seq == 0:
        raise RuntimeError("no system with a population or a station was found; nothing to load")
    out.write(stream_footer(snapshot, source_label).encode("utf-8"))
    out.flush()
    return stats


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write a psql load script for v3_state from Spansh dumps.")
    parser.add_argument("dumps", nargs="+", metavar="DUMP",
                        help="Spansh dump file (.json.gz or .json); '-' reads standard input")
    parser.add_argument("--snapshot", action="store_true",
                        help="the input is every system that has a station "
                             "(galaxy_stations.json.gz): also remove stored systems "
                             "that are no longer in it")
    parser.add_argument("--source", default=None,
                        help="label recorded with the load (default: spansh:<file names>)")
    args = parser.parse_args(argv)

    if sys.stdout.isatty():
        print("This writes a database load script; pipe it into psql instead of "
              "printing it to the screen.", file=sys.stderr)
        return 2
    source_label = args.source or "spansh:" + ",".join(
        os.path.basename(path) for path in args.dumps)
    try:
        stats = write_load_stream(args.dumps, sys.stdout.buffer, snapshot=args.snapshot,
                                  source_label=source_label, log=sys.stderr)
    except BaseException as error:  # no COMMIT was written, so nothing is applied
        print("STOPPED, nothing was loaded: %s: %s" % (type(error).__name__, error),
              file=sys.stderr)
        return 1
    for line in stats.summary_lines():
        print(line, file=sys.stderr)
    print("Load script complete; the result table printed by psql shows what changed.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
