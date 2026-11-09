"""Spansh full-detail delta parser, material-change classifier and measurement CLI.

Fixtures are derived at test time from the checksum-pinned Spansh system dumps
and canonical export under ``tests/fixtures/ratings_v4_sources`` (twelve real
systems, base canonical rows from the published 2026-08-24 generation, Spansh
records fetched 2026-09-08).  No network, no database.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "importer" / "src"))

from spansh_delta import (  # noqa: E402
    CONTENT_TABLES, DIGEST_NAMES, ORBITAL_PHASE_COLUMNS, LineFormatError, SpanshStream,
    base_rows_from_export, classify_material_change, content_digest, content_digests,
    normalize_system, record_touch_profile,
)
from v3_spansh.adapter import SpanshAdapter  # noqa: E402
from v3_spansh.contracts import COPY_COLUMNS  # noqa: E402
from domain.ratings_v4_canonical import load_source_fixture  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "ratings_v4_sources"
HD_38179, ALIOTH, SOL, MAIA = 158872029, 1109989017963, 10477373803, 8216113749
THAILE, BORANN = 31639717276, 5070611621289


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "measure_spansh_delta", ROOT / "scripts" / "dev" / "measure_spansh_delta.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CLI = _load_cli()


@pytest.fixture(scope="module")
def fixture_data():
    canonical, _, payloads = load_source_fixture(FIXTURE)
    records = {payload["system"]["id64"]: payload["system"] for payload in payloads}
    return base_rows_from_export(canonical), records


@pytest.fixture
def records(fixture_data):
    return deepcopy(fixture_data[1])


@pytest.fixture(scope="module")
def base(fixture_data):
    return fixture_data[0]


def _compact(record) -> bytes:
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode()


def write_galaxy(path: Path, records, *, compress: bool = True, layout: str = "spansh") -> Path:
    """Write records exactly as Spansh lays out galaxy*.json: '[', one compact
    object per line with a trailing comma (except the last), then ']'."""
    records = list(records)
    if layout == "spansh":
        lines = [b"["]
        for position, record in enumerate(records):
            suffix = b"," if position < len(records) - 1 else b""
            lines.append(b"    " + _compact(record) + suffix)
        lines.append(b"]")
        data = b"\n".join(lines) + b"\n"
    elif layout == "pretty":
        data = json.dumps(records, indent=2).encode()
    else:
        raise AssertionError(layout)
    path.write_bytes(gzip.compress(data, mtime=0) if compress else data)
    return path


def adapter_rows(record, *, observed: datetime | None = None):
    """Column-keyed canonical rows, as a database export would return them,
    with provenance deliberately different from the classifier's own."""
    adapter = SpanshAdapter(source_id=40, source_run_id=uuid4(),
                            observed_at=observed or datetime(2026, 8, 24, tzinfo=timezone.utc))
    batch = adapter.adapt_system(deepcopy(record))
    return {table: [dict(zip(COPY_COLUMNS[table], row, strict=True)) for row in batch.rows.get(table, [])]
            for table in CONTENT_TABLES}


# -- streaming ---------------------------------------------------------------

def test_line_parser_streams_spansh_layout_with_exact_receipt(tmp_path, records):
    path = write_galaxy(tmp_path / "galaxy_1day.json.gz", records.values())
    stream = SpanshStream(path)
    seen = list(stream)
    assert [item.record["id64"] for item in seen] == list(records)
    assert [item.ordinal for item in seen] == list(range(1, len(records) + 1))
    assert all(item.raw_bytes == len(_compact(records[item.record["id64"]])) for item in seen)
    receipt = stream.receipt
    assert receipt is not None and receipt.parser == "lines" and receipt.consumed_to_eof
    assert receipt.file_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert receipt.file_bytes == path.stat().st_size
    assert receipt.decompressed_bytes == len(gzip.decompress(path.read_bytes()))
    assert receipt.systems == 12 and receipt.compressed
    with pytest.raises(ValueError, match="single use"):
        list(stream)


def test_plain_json_and_empty_array_are_accepted(tmp_path, records):
    plain = write_galaxy(tmp_path / "galaxy.json", list(records.values())[:2], compress=False)
    stream = SpanshStream(plain)
    assert len(list(stream)) == 2 and not stream.receipt.compressed
    empty = tmp_path / "empty.json.gz"
    empty.write_bytes(gzip.compress(b"[\n]\n", mtime=0))
    stream = SpanshStream(empty)
    assert list(stream) == [] and stream.receipt.systems == 0


@pytest.mark.parametrize("damage", ["cut_gzip", "no_closing_bracket", "missing_comma",
                                    "trailing_comma", "data_after_close", "bad_id64", "nan"])
def test_damaged_files_fail_closed_without_receipt(tmp_path, records, damage):
    some = list(records.values())[:3]
    if damage == "bad_id64":
        some[1]["id64"] = -5
    path = write_galaxy(tmp_path / "galaxy_1day.json.gz", some)
    text = gzip.decompress(path.read_bytes())
    if damage == "cut_gzip":
        path.write_bytes(path.read_bytes()[: path.stat().st_size // 2])
    else:
        if damage == "no_closing_bracket":
            text = text.rsplit(b"]", 1)[0]
        elif damage == "missing_comma":
            text = text.replace(b"},\n", b"}\n", 1)
        elif damage == "trailing_comma":
            text = text.replace(b"}\n]", b"},\n]")
        elif damage == "data_after_close":
            text += b"[]\n"
        elif damage == "nan":
            text = re.sub(rb'"population":[0-9]+', b'"population":NaN', text, count=1)
            assert b"NaN" in text
        path.write_bytes(gzip.compress(text, mtime=0))
    stream = SpanshStream(path, parser="lines")
    with pytest.raises(ValueError):
        list(stream)
    assert stream.receipt is None


def test_forced_line_parser_refuses_other_layouts(tmp_path, records):
    path = write_galaxy(tmp_path / "pretty.json.gz", list(records.values())[:2], layout="pretty")
    with pytest.raises(LineFormatError):
        list(SpanshStream(path, parser="lines"))


def test_auto_falls_back_to_ijson_for_pretty_printed_files(tmp_path, records):
    pytest.importorskip("ijson")
    path = write_galaxy(tmp_path / "pretty.json.gz", records.values(), layout="pretty")
    stream = SpanshStream(path)
    assert [item.record["id64"] for item in stream] == list(records)
    assert stream.receipt.parser == "ijson-fallback"
    assert stream.receipt.fallback_after_records == 0
    assert [item.raw_bytes for item in SpanshStream(path, parser="ijson")] == [
        len(_compact(record)) for record in records.values()]


def test_auto_fallback_mid_file_replays_without_duplicates(tmp_path, records):
    pytest.importorskip("ijson")
    values = list(records.values())
    path = write_galaxy(tmp_path / "mixed.json.gz", values)
    text = gzip.decompress(path.read_bytes()).split(b"\n")
    # Pretty-print the fourth record across several lines.
    text[4] = json.dumps(values[3], indent=1).encode() + b","
    path.write_bytes(gzip.compress(b"\n".join(text), mtime=0))
    stream = SpanshStream(path)
    ids = [item.record["id64"] for item in stream]
    assert ids == list(records)
    assert stream.receipt.parser == "ijson-fallback"
    assert stream.receipt.fallback_after_records == 3


def test_ijson_path_bounds_the_size_of_one_system(tmp_path, records, monkeypatch):
    pytest.importorskip("ijson")
    from spansh_delta import stream as stream_module

    path = write_galaxy(tmp_path / "pretty.json.gz", list(records.values())[:3], layout="pretty")
    monkeypatch.setattr(stream_module, "MAX_RECORD_BYTES", 1024)
    monkeypatch.setattr(stream_module, "_READ_SIZE", 1024)
    stream = SpanshStream(path)
    with pytest.raises(ValueError, match="bounded record size"):
        list(stream)
    assert stream.receipt is None


def test_non_array_root_is_refused_by_both_parsers(tmp_path, records):
    pytest.importorskip("ijson")
    path = tmp_path / "object.json.gz"
    path.write_bytes(gzip.compress(_compact(records[HD_38179]), mtime=0))
    for parser in ("auto", "ijson"):
        with pytest.raises(ValueError, match="JSON array"):
            list(SpanshStream(path, parser=parser))


# -- normalization and material change against the real 2026-08-24 base -------

BASE_TABLES = ("systems", "bodies", "body_composition", "rings",
               "body_signal_current", "body_genus_current")


def test_unchanged_real_system_matches_production_base_exactly(records, base):
    # HD 38179 was last updated in Spansh in 2021: its later record must map to
    # the very rows the published generation holds, float4 composition included.
    system = normalize_system(records[HD_38179])
    for policy in ("strict", "stable"):
        verdict = classify_material_change(system, base, policy=policy, tables=BASE_TABLES)
        assert verdict.status == "unchanged", verdict
        assert not verdict.admit


def test_rescan_only_moves_orbital_phase_columns(records, base):
    system = normalize_system(records[ALIOTH])
    strict = classify_material_change(system, base, policy="strict", tables=BASE_TABLES)
    stable = classify_material_change(system, base, policy="stable", tables=BASE_TABLES)
    assert strict.status == "changed" and strict.changed_tables == ("bodies",)
    assert {tuple(column.split(".")) for column in strict.changed_columns} <= ORBITAL_PHASE_COLUMNS
    assert stable.status == "unchanged"


def test_real_signal_changes_are_detected(records, base):
    sol = classify_material_change(normalize_system(records[SOL]), base, tables=BASE_TABLES)
    assert sol.status == "changed"
    assert "body_signal_current" in sol.changed_tables and "bodies.signals_complete" in sol.changed_columns
    maia = classify_material_change(normalize_system(records[MAIA]), base, tables=BASE_TABLES)
    assert maia.changed_tables == ("body_signal_current",) and maia.rows_removed == 44


def test_base_export_tables_must_cover_the_comparison(records, base):
    system = normalize_system(records[HD_38179])
    with pytest.raises(ValueError, match="lack compared tables"):
        classify_material_change(system, base)  # the export has no station tables
    without_bodies = {table: rows for table, rows in base.items() if table != "bodies"}
    with pytest.raises(ValueError, match=r"\['bodies'\]"):
        classify_material_change(system, without_bodies, tables=("systems", "body_composition"))
    with pytest.raises(ValueError, match="systems table"):
        classify_material_change(system, base, tables=("bodies",))
    assert classify_material_change(system).status == "new"
    other = {table: [] for table in CONTENT_TABLES}
    assert classify_material_change(system, other).status == "new"


# -- V3-stored vs not-stored fields -------------------------------------------

def test_faction_population_and_market_churn_is_not_material(records):
    record = records[THAILE]
    base_rows = adapter_rows(record)
    churned = deepcopy(record)
    churned.update(population=123456, allegiance="Federation", government="Democracy",
                   security="High", controllingFaction={"name": "Somebody"},
                   factions=[{"name": "Somebody", "influence": 0.5, "state": "Boom"}],
                   powers=["Someone"], powerState="Exploited", date="2026-10-20 00:00:00+00")
    for station in churned["stations"]:
        station.update(updateTime="2026-10-20 00:00:00+00", controllingFactionState="War",
                       market={"commodities": [{"name": "Gold", "buyPrice": 1}]},
                       landingPads={"large": 1})
    verdict = classify_material_change(normalize_system(churned), base_rows, policy="strict")
    assert verdict.status == "unchanged", verdict


def test_station_and_body_changes_are_material(records):
    record = records[THAILE]
    base_rows = adapter_rows(record)

    added_body = deepcopy(record)
    new_body = deepcopy(added_body["bodies"][-1])
    new_body.update(id64=new_body["id64"] + (1 << 55), bodyId=999, name="Thaile HW-V e2-7 Z")
    new_body.pop("stations", None)
    added_body["bodies"].append(new_body)
    verdict = classify_material_change(normalize_system(added_body), base_rows)
    assert verdict.status == "changed" and "bodies" in verdict.changed_tables and verdict.rows_added >= 1

    renamed_carrier = deepcopy(record)
    renamed_carrier["stations"][0]["carrierName"] = "A New Name"
    renamed_carrier["stations"][0]["services"] = renamed_carrier["stations"][0]["services"][:3]
    with_carriers = classify_material_change(normalize_system(renamed_carrier), base_rows)
    without = classify_material_change(normalize_system(renamed_carrier), base_rows, include_carriers=False)
    assert with_carriers.status == "changed"
    assert {"stations.name", "station_service_current"} & (
        set(with_carriers.changed_columns) | set(with_carriers.changed_tables))
    assert without.status == "unchanged"  # Thaile has only carriers


def test_a_station_turning_into_a_carrier_changes_the_carrier_free_view(records):
    record = records[THAILE]
    fixed = deepcopy(record)
    fixed["stations"][0]["type"] = "Outpost"
    fixed["stations"][0]["name"] = fixed["stations"][0].pop("carrierName")
    base_rows = adapter_rows(fixed)  # base: a fixed Outpost with this Market ID
    verdict = classify_material_change(normalize_system(record), base_rows, include_carriers=False)
    assert verdict.status == "changed" and verdict.rows_removed >= 1
    assert content_digests(normalize_system(record))["stable_excluding_carriers"] != \
        content_digests(normalize_system(fixed))["stable_excluding_carriers"]


def test_orbital_phase_policy_and_digests(records):
    record = records[THAILE]
    base_rows = adapter_rows(record)
    moved = deepcopy(record)
    for body in moved["bodies"]:
        if "meanAnomaly" in body:
            body["meanAnomaly"] = (body["meanAnomaly"] + 10) % 360
            body["distanceToArrival"] = (body.get("distanceToArrival") or 0) + 1
    original, shifted = normalize_system(record), normalize_system(moved)
    assert classify_material_change(shifted, base_rows, policy="strict").status == "changed"
    assert classify_material_change(shifted, base_rows, policy="stable").status == "unchanged"
    assert content_digest(original, policy="strict") != content_digest(shifted, policy="strict")
    assert content_digest(original, policy="stable") == content_digest(shifted, policy="stable")

    reordered = deepcopy(record)
    reordered["bodies"].reverse()
    reordered["stations"].reverse()
    assert content_digest(normalize_system(reordered), policy="strict") == content_digest(original, policy="strict")
    with pytest.raises(ValueError, match="policy"):
        content_digest(original, policy="loose")
    digests = content_digests(shifted)
    assert set(digests) == set(DIGEST_NAMES)
    for policy in ("strict", "stable"):
        assert digests[policy] == content_digest(shifted, policy=policy)
        assert digests[f"{policy}_excluding_carriers"] == content_digest(
            shifted, policy=policy, include_carriers=False)
    # Thaile has only carriers: excluding them equals a carrier-free copy.
    no_carriers = deepcopy(moved)
    no_carriers["stations"] = []
    assert digests["stable_excluding_carriers"] == content_digest(normalize_system(no_carriers))
    assert digests["stable"] != digests["stable_excluding_carriers"]


def test_float4_composition_compares_at_stored_precision(records):
    record = records[HD_38179]
    base_rows = adapter_rows(record)
    for row in base_rows["body_composition"]:
        # What PostgreSQL ``real`` returns through to_jsonb.
        for column in ("ice_pct", "metal_pct", "rock_pct"):
            if row[column] is not None:
                row[column] = float(f"{row[column]:.7g}")
        row["materials"] = json.loads(row["materials"]) if row["materials"] else None
    assert classify_material_change(normalize_system(record), base_rows, policy="strict").status == "unchanged"


def test_adapter_rejections_surface_as_value_errors(records):
    broken = deepcopy(records[HD_38179])
    broken.pop("coords")
    with pytest.raises(ValueError, match="coordinates"):
        normalize_system(broken)


def test_normalized_counts_separate_fleet_carriers(records):
    thaile, borann = normalize_system(records[THAILE]), normalize_system(records[BORANN])
    assert (thaile.fixed_stations, thaile.fleet_carriers) == (0, 2)
    assert borann.fleet_carriers == 19 and borann.fixed_stations > 0


# -- touch profile -------------------------------------------------------------

def test_touch_profile_categories(records):
    window = datetime(2026, 9, 8, tzinfo=timezone.utc)
    thaile = deepcopy(records[THAILE])
    for body in thaile["bodies"]:
        body["updateTime"] = "2020-01-01 00:00:00+00"
        if isinstance(body.get("signals"), dict):
            body["signals"]["updateTime"] = "2020-01-01 00:00:00+00"
    assert record_touch_profile(thaile, window_start=window).category == "carriers_only"
    for station in thaile["stations"]:
        station["updateTime"] = "2020-01-01 00:00:00+00"
    assert record_touch_profile(thaile, window_start=window).category == "system_only"
    ringed = deepcopy(thaile)
    ring_body = next(body for body in ringed["bodies"] if body.get("rings") or body.get("belts"))
    ring = (ring_body.get("rings") or ring_body.get("belts"))[0]
    ring["signals"] = {"signals": {"Painite": 3}, "updateTime": "2026-09-08 12:00:00+00"}
    assert record_touch_profile(ringed, window_start=window).category == "bodies"
    thaile["date"] = "2020-01-01 00:00:00+00"
    assert record_touch_profile(thaile, window_start=window).category == "outside_window"
    bare = {"id64": 1, "name": "x", "coords": {"x": 0, "y": 0, "z": 0}}
    assert record_touch_profile(bare, window_start=window).category == "no_timestamps"
    profile = record_touch_profile(records[SOL], window_start=window - timedelta(days=1))
    assert profile.category in {"bodies", "fixed_stations"}
    assert profile.latest_v3_time is not None and profile.latest_v3_time.tzinfo is not None


# -- measurement CLI -------------------------------------------------------------

def _run_cli(*argv: str) -> int:
    return CLI.main(list(argv))


def test_cli_measures_counts_bytes_and_times(tmp_path, records):
    night = write_galaxy(tmp_path / "galaxy_1day.json.gz", records.values())
    headers = tmp_path / "headers.txt"
    headers.write_text(
        "HTTP/2 302\r\nlocation: https://example.invalid/x\r\n\r\n"
        f"HTTP/2 200\r\netag: \"abc\"\r\nlast-modified: Tue, 08 Sep 2026 20:00:00 GMT\r\n"
        f"content-length: {night.stat().st_size}\r\n\r\n", encoding="latin-1")
    output, summary, index = tmp_path / "stats.json", tmp_path / "summary.md", tmp_path / "night1.idx"
    assert _run_cli("--input", str(night), "--output", str(output), "--http-headers", str(headers),
                    "--write-index", str(index), "--markdown-summary", str(summary)) == 0
    stats = json.loads(output.read_text())
    assert stats["schema"] == CLI.STATS_SCHEMA
    assert stats["source"]["reference_time"] == "2026-09-08T20:00:00Z"
    assert stats["source"]["reference_time_source"] == "http_last_modified"
    assert stats["source"]["http"]["etag"] == '"abc"'
    counts = stats["counts"]
    expected = [normalize_system(record) for record in records.values()]
    assert counts["systems"] == counts["distinct_id64"] == 12
    assert counts["bodies"] == sum(system.count("bodies") for system in expected)
    assert counts["fleet_carriers"] == sum(system.fleet_carriers for system in expected) == 66
    assert counts["fixed_stations"] == sum(system.fixed_stations for system in expected)
    assert stats["canonical_rows"]["stations"] == counts["fixed_stations"] + counts["fleet_carriers"]
    assert stats["bytes"]["file_bytes"] == night.stat().st_size
    assert stats["per_system"]["record_bytes"]["n"] == 12
    assert sum(stats["touch_proxy"]["categories"].values()) == 12
    system_dates = stats["update_times"]["system_date"]
    assert system_dates["earliest"] == "2021-02-11T13:43:11Z"
    assert sum(system_dates["by_utc_date"].values()) == 12
    assert stats["overlap"] is None
    assert "Fleet carriers | 66" in summary.read_text()


def test_cli_overlap_between_consecutive_nights(tmp_path, records):
    night1 = write_galaxy(tmp_path / "n1.json.gz", records.values())
    index1, stats1 = tmp_path / "n1.idx", tmp_path / "n1.json"
    assert _run_cli("--input", str(night1), "--output", str(stats1), "--write-index", str(index1),
                    "--reference-time", "2026-09-08T20:00:00Z") == 0

    second = deepcopy(records)
    del second[MAIA]  # only in night 1
    for body in second[ALIOTH]["bodies"]:  # orbital phase only
        if "meanAnomaly" in body:
            body["meanAnomaly"] = (body["meanAnomaly"] + 1) % 360
    second[THAILE]["stations"][0]["carrierName"] = "Renamed"  # carrier only
    second[SOL]["bodies"][0]["name"] = "Sol Prime"  # real change
    newcomer = deepcopy(records[HD_38179])
    newcomer["id64"] = 4242
    for body in newcomer["bodies"]:
        body["id64"] = body["id64"] + (4242 << 40)
    second[4242] = newcomer
    night2 = write_galaxy(tmp_path / "n2.json.gz", second.values())

    for previous in (("--previous-index", str(index1)), ("--previous-file", str(night1))):
        output = tmp_path / f"n2{previous[0]}.json"
        assert _run_cli("--input", str(night2), "--output", str(output), *previous,
                        "--previous-stats", str(stats1), "--reference-time", "2026-09-09T20:00:00Z") == 0
        overlap = json.loads(output.read_text())["overlap"]
        assert overlap["systems_in_both"] == 11
        assert overlap["only_in_current"] == 1 and overlap["only_in_previous"] == 1
        assert overlap["content_changed_in_both"] == {
            "strict": 3, "strict_excluding_carriers": 2,
            "stable": 2, "stable_excluding_carriers": 1}
        assert overlap["same_source_file"] is False
    previous_stats = json.loads(output.read_text())["previous_stats"]
    assert previous_stats["systems"] == 12 and previous_stats["systems_change"] == 0


def test_cli_refuses_previous_stats_without_counts(tmp_path, records):
    path = write_galaxy(tmp_path / "n.json.gz", list(records.values())[:2])
    not_modified = tmp_path / "prev.json"
    not_modified.write_text(json.dumps({"schema": "ed-finder.spansh-delta-not-modified/1"}))
    assert _run_cli("--input", str(path), "--previous-stats", str(not_modified)) == 1
    hollow = tmp_path / "hollow.json"
    hollow.write_text(json.dumps({"schema": CLI.STATS_SCHEMA}))
    assert _run_cli("--input", str(path), "--previous-stats", str(hollow)) == 1


def test_cli_index_round_trip_duplicates_and_corruption(tmp_path, records):
    values = list(records.values())
    path = write_galaxy(tmp_path / "dup.json.gz", values + [values[0]])
    index = tmp_path / "dup.idx"
    output = tmp_path / "dup.json"
    assert _run_cli("--input", str(path), "--output", str(output), "--write-index", str(index)) == 0
    stats = json.loads(output.read_text())
    assert stats["counts"]["systems"] == 13
    assert stats["counts"]["distinct_id64"] == 12 and stats["counts"]["duplicate_id64_records"] == 1
    header, loaded = CLI.DigestIndex.read(index)
    assert header["records"] == len(loaded) == 12 and list(loaded.ids) == sorted(records)

    corrupt = tmp_path / "corrupt.idx"
    corrupt.write_bytes(index.read_bytes()[:-5])
    assert _run_cli("--input", str(path), "--output", str(output), "--previous-index", str(corrupt)) == 1
    corrupt.write_bytes(b"not an index")
    assert _run_cli("--input", str(path), "--output", str(output), "--previous-index", str(corrupt)) == 1


def test_cli_refuses_size_mismatch_and_truncated_input(tmp_path, records, capsys):
    path = write_galaxy(tmp_path / "galaxy_1day.json.gz", list(records.values())[:2])
    headers = tmp_path / "headers.txt"
    headers.write_text(f"HTTP/1.1 200 OK\ncontent-length: {path.stat().st_size + 1}\n\n")
    assert _run_cli("--input", str(path), "--http-headers", str(headers)) == 1
    assert "does not match Content-Length" in capsys.readouterr().err
    path.write_bytes(path.read_bytes()[:-10])
    assert _run_cli("--input", str(path)) == 1
