"""The Spansh -> v3_state extractor, checked against real Spansh records.

The records come from tests/fixtures/ratings_v4_sources/spansh-system-dumps.zip
(twelve real systems exported from Spansh in September 2026).
"""
from __future__ import annotations

import copy
import gzip
import io
import json
import zipfile
from pathlib import Path

import pytest

import spansh_system_state as state

FIXTURE_ZIP = (Path(__file__).resolve().parent / "fixtures" / "ratings_v4_sources"
               / "spansh-system-dumps.zip")


@pytest.fixture(scope="module")
def real_systems() -> dict:
    systems = {}
    with zipfile.ZipFile(FIXTURE_ZIP) as archive:
        for name in archive.namelist():
            if name.endswith(".json"):
                record = json.loads(archive.read(name))
                system = record.get("system", record)
                systems[system["name"]] = system
    assert len(systems) == 12
    return systems


def synthetic(**overrides) -> dict:
    system = {
        "id64": 123456789,
        "name": "Test System",
        "coords": {"x": 1.5, "y": -2.25, "z": 300.0},
        "date": "2026-11-01 10:00:00+00",
        "population": 0,
        "stations": [],
        "bodies": [],
    }
    system.update(overrides)
    return system


def unescape_copy(field: str):
    if field == "\\N":
        return None
    out, index = [], 0
    while index < len(field):
        char = field[index]
        if char == "\\":
            index += 1
            out.append({"t": "\t", "n": "\n", "r": "\r", "\\": "\\"}[field[index]])
        else:
            out.append(char)
        index += 1
    return "".join(out)


def parse_copy_line(line: str) -> dict:
    assert line.endswith("\n")
    fields = line[:-1].split("\t")
    assert len(fields) == len(state.STAGE_COLUMNS)
    return dict(zip(state.STAGE_COLUMNS, (unescape_copy(field) for field in fields), strict=True))


# -- real records -----------------------------------------------------------

def test_inhabited_system_keeps_government_economy_and_powerplay(real_systems):
    row = state.system_row(real_systems["Deriv-Dar"])
    assert row["system_id64"] == 2037701937507
    assert row["population"] == 4962233163
    assert (row["allegiance"], row["government"], row["security"]) == (
        "Federation", "Corporate", "High")
    assert (row["primary_economy"], row["secondary_economy"]) == ("Agriculture", "Refinery")
    assert row["controlling_faction"] == "Sirius Special Forces"
    assert row["controlling_power"] == "Edmund Mahon"
    assert row["power_state"] == "Exploited"
    assert row["power_state_control_progress"] == pytest.approx(0.278915)
    assert row["power_state_reinforcement"] == 0
    assert row["power_state_undermining"] == 979
    assert (row["x_ly"], row["y_ly"], row["z_ly"]) == (-4.46875, 11.0, -82.71875)


def test_faction_list_is_kept_with_influence_and_states(real_systems):
    source = real_systems["Deriv-Dar"]
    row = state.system_row(source)
    assert [faction["name"] for faction in row["factions"]] == sorted(
        faction["name"] for faction in source["factions"])
    by_name = {faction["name"]: faction for faction in row["factions"]}
    assert by_name["Deriv-Dar Corporation"]["influence"] == pytest.approx(0.16075)
    assert by_name["Deriv-Dar Corporation"]["allegiance"] == "Federation"
    with_states = [faction for faction in source["factions"] if faction.get("pendingStates")]
    for faction in with_states:
        assert by_name[faction["name"]]["pendingStates"] == faction["pendingStates"]


def test_every_power_in_the_system_is_listed(real_systems):
    row = state.system_row(real_systems["Deriv-Dar"])
    assert [power["power"] for power in row["powers"]] == [
        "Edmund Mahon", "Jerome Archer", "Li Yong-Rui", "Zemina Torval"]


def test_power_conflict_progress_is_kept(real_systems):
    row = state.system_row(real_systems["Wregoe ZN-X c28-28"])
    progress = {power["power"]: power["conflict_progress"] for power in row["powers"]}
    assert progress["Nakato Kaine"] == 0.0


def test_stations_under_bodies_are_counted_as_well_as_top_level_ones(real_systems):
    source = real_systems["Sol"]
    nested = sum(len(body.get("stations") or []) for body in source["bodies"])
    assert nested == 57 and len(source["stations"]) == 10
    row = state.system_row(source)
    assert row["station_count"] == 67
    assert row["carrier_count"] == 0
    assert row["station_type_counts"]["Orbis Starport"] == 6
    assert row["station_type_counts"]["Settlement"] == 46
    assert row["station_type_counts"][state.UNKNOWN_STATION_TYPE] == 4
    assert sum(row["station_type_counts"].values()) == row["station_count"]


def test_construction_sites_are_counted_in_orbit_and_on_the_ground(real_systems):
    row = state.system_row(real_systems["Wregoe ZN-X c28-28"])
    assert row["station_type_counts"]["Space Construction Depot"] == 2
    assert row["station_type_counts"]["Planetary Construction Depot"] == 2
    assert row["construction_site_count"] == 4


def test_construction_site_is_found_by_type_even_when_its_name_does_not_say_so(real_systems):
    # Praea Euq WV-W b2-2 has two "Planetary Construction Depot" stations named
    # like ordinary settlements ("Okada Chemical Productions").
    row = state.system_row(real_systems["Praea Euq WV-W b2-2"])
    assert row["construction_site_count"] == 2


def test_fleet_carriers_are_counted_apart_from_stations(real_systems):
    row = state.system_row(real_systems["Maia"])
    assert row["carrier_count"] == 45
    assert row["station_count"] == 100
    assert not any("arrier" in key for key in row["station_type_counts"])


def test_system_with_only_fleet_carriers_is_not_stored(real_systems):
    source = real_systems["Thaile HW-V e2-7"]
    assert source["population"] == 0 and len(source["stations"]) == 2
    assert state.system_row(source) is None


def test_empty_system_is_not_stored(real_systems):
    assert state.system_row(real_systems["HD 38179"]) is None
    assert state.system_row(real_systems["Smojoo ZE-R d4-109"]) is None


def test_updated_time_is_the_newest_of_system_fields_and_stations(real_systems):
    source = real_systems["Sol"]
    assert source["date"] == "2026-09-08 19:54:55+00"
    row = state.system_row(source)
    # Daedalus was updated at 19:55:40, after the system's own date.
    assert row["system_updated_at"] == "2026-09-08 19:55:40+00"


def test_carrier_updates_do_not_make_a_system_look_fresh(real_systems):
    source = copy.deepcopy(real_systems["Maia"])
    row_before = state.system_row(source)
    for station in source["stations"]:
        if "Carrier" in (station.get("type") or ""):
            station["updateTime"] = "2030-01-01 00:00:00+00"
    assert state.system_row(source)["system_updated_at"] == row_before["system_updated_at"]


def test_spansh_field_timestamps_are_kept(real_systems):
    row = state.system_row(real_systems["Deriv-Dar"])
    assert row["field_timestamps"] == {
        "controllingPower": "2026-09-07 18:48:58+00",
        "factions": "2026-09-07 18:48:58+00",
        "powerState": "2026-09-07 18:48:58+00",
        "powers": "2026-09-07 18:48:58+00",
    }


def test_nine_of_the_twelve_real_systems_are_kept(real_systems):
    kept = sorted(name for name, system in real_systems.items()
                  if state.system_row(system) is not None)
    assert kept == sorted([
        "Sol", "Alioth", "Col 285 Sector BW-U c3-5", "Achenar", "Deriv-Dar", "Borann",
        "Praea Euq WV-W b2-2", "Wregoe ZN-X c28-28", "Maia"])


# -- claimed-but-empty systems ------------------------------------------------

def test_claimed_system_with_no_population_is_kept():
    row = state.system_row(synthetic(stations=[
        {"id": 1, "name": "System Colonisation Ship", "type": "Mega ship",
         "updateTime": "2026-11-02 09:00:00+00"},
        {"id": 2, "name": "Orbital Construction Site: Hope", "type": "Space Construction Depot",
         "updateTime": "2026-11-03 09:00:00+00"},
    ]))
    assert row is not None
    assert row["population"] == 0
    assert row["colonisation_ship_present"] is True
    assert row["construction_site_count"] == 1
    assert row["station_count"] == 2
    assert row["system_updated_at"] == "2026-11-03 09:00:00+00"


@pytest.mark.parametrize("name", [
    "System Colonisation Ship", "$EXT_PANEL_ColonisationShip; Younghusband Reach",
    "system colonization ship"])
def test_colonisation_ship_is_recognised_by_name(name):
    row = state.system_row(synthetic(stations=[{"id": 1, "name": name, "type": None}]))
    assert row["colonisation_ship_present"] is True


def test_ordinary_station_is_not_a_colonisation_ship_or_construction_site():
    row = state.system_row(synthetic(stations=[{"id": 1, "name": "Jameson Memorial",
                                                "type": "Orbis Starport"}]))
    assert row["colonisation_ship_present"] is False
    assert row["construction_site_count"] == 0


def test_untyped_carrier_is_recognised_by_its_callsign():
    assert state.system_row(synthetic(stations=[{"id": 1, "name": "T9J-26T"}])) is None
    row = state.system_row(synthetic(population=5, stations=[{"id": 1, "name": "T9J-26T"}]))
    assert (row["carrier_count"], row["station_count"]) == (1, 0)


def test_same_station_listed_twice_is_counted_once():
    station = {"id": 77, "name": "Dock", "type": "Outpost"}
    row = state.system_row(synthetic(stations=[station],
                                     bodies=[{"name": "A 1", "stations": [dict(station)]}]))
    assert row["station_count"] == 1


# -- unusable records -------------------------------------------------------

@pytest.mark.parametrize("broken", [
    {"id64": None}, {"id64": "12"}, {"name": ""}, {"name": None}, {"coords": None},
    {"coords": {"x": 1, "y": 2}}, {"coords": {"x": float("nan"), "y": 0, "z": 0}},
])
def test_record_without_identity_or_position_is_refused(broken):
    with pytest.raises(state.SkipSystem):
        state.system_row(synthetic(population=10, **broken))


def test_record_with_no_usable_timestamp_is_refused():
    with pytest.raises(state.SkipSystem):
        state.system_row(synthetic(population=10, date="yesterday"))


def test_negative_or_missing_population_counts_as_zero():
    assert state.system_row(synthetic(population=-5)) is None
    assert state.system_row(synthetic(population=None)) is None


@pytest.mark.parametrize("raw, expected", [
    ("2026-09-07 18:50:19+00", "2026-09-07 18:50:19+00"),
    ("2026-09-07T18:48:58Z", "2026-09-07 18:48:58+00"),
    ("2026-09-07T18:48:58.123Z", "2026-09-07 18:48:58+00"),
    ("2026-09-07 18:48:58+00:00", "2026-09-07 18:48:58+00"),
    ("2026-09-07 18:48:58", "2026-09-07 18:48:58+00"),
    ("2026-09-07 18:48:58+01", None),
    ("not a time", None), (None, None), (1234, None),
])
def test_timestamps_are_normalised_to_one_utc_spelling(raw, expected):
    assert state.normalise_timestamp(raw) == expected


# -- fingerprint --------------------------------------------------------------

def test_fingerprint_is_stable_and_changes_with_any_stored_value(real_systems):
    source = real_systems["Deriv-Dar"]
    first = state.system_row(source)["content_hash"]
    assert state.system_row(copy.deepcopy(source))["content_hash"] == first

    changed_faction = copy.deepcopy(source)
    changed_faction["factions"][0]["influence"] += 0.01
    assert state.system_row(changed_faction)["content_hash"] != first

    changed_station = copy.deepcopy(source)
    changed_station["stations"].append({"id": 999999, "name": "New", "type": "Outpost"})
    assert state.system_row(changed_station)["content_hash"] != first


def test_fingerprint_ignores_the_order_factions_arrive_in(real_systems):
    source = real_systems["Deriv-Dar"]
    shuffled = copy.deepcopy(source)
    shuffled["factions"].reverse()
    shuffled["powers"].reverse()
    assert state.system_row(shuffled)["content_hash"] == state.system_row(source)["content_hash"]


# -- COPY text format -------------------------------------------------------

def test_copy_line_survives_tabs_newlines_backslashes_quotes_and_unicode():
    awkward = 'Bad\tName\\ with "quotes"\nand\rmore \u00e9\u4e2d \x00nul'
    row = state.system_row(synthetic(
        population=1, name=awkward,
        factions=[{"name": 'Faction "A"\\B\tC', "influence": 0.5, "state": "None"}]))
    parsed = parse_copy_line(state.copy_line(7, row))
    assert parsed["seq"] == "7"
    assert parsed["name"] == awkward.replace("\x00", "").strip()
    assert json.loads(parsed["factions"])[0]["name"] == 'Faction "A"\\B\tC'
    assert parsed["allegiance"] is None
    assert parsed["colonisation_ship_present"] == "f"
    assert "\x00" not in state.copy_line(7, row)


def test_copy_line_has_one_field_per_stage_column(real_systems):
    for system in real_systems.values():
        row = state.system_row(system)
        if row is not None:
            line = state.copy_line(1, row)
            assert line.count("\n") == 1
            parsed = parse_copy_line(line)
            assert json.loads(parsed["station_type_counts"]) == row["station_type_counts"]
            assert int(parsed["population"]) == row["population"]


# -- reading dumps and writing the stream -------------------------------------

def dump_bytes(systems, *, array=True) -> bytes:
    lines = [json.dumps(system, separators=(",", ":")) for system in systems]
    if array:
        text = "[\n" + ",\n".join("    " + line for line in lines) + "\n]\n"
    else:
        text = "".join(line + "\n" for line in lines)
    return text.encode("utf-8")


def test_dump_reader_handles_spansh_array_layout_and_json_lines(real_systems):
    systems = [real_systems["Sol"], real_systems["Maia"]]
    for array in (True, False):
        records = [record for _, record in
                   state.iter_dump_records(io.BytesIO(dump_bytes(systems, array=array)))]
        assert [record["name"] for record in records] == ["Sol", "Maia"]


def test_dump_reader_reports_an_unreadable_line_and_carries_on():
    data = b'[\n{"id64": 1},\n{broken,\n{"id64": 2}\n]\n'
    records = [record for _, record in state.iter_dump_records(io.BytesIO(data))]
    assert records == [{"id64": 1}, None, {"id64": 2}]


def write_gz(path: Path, systems) -> str:
    with gzip.open(path, "wb") as handle:
        handle.write(dump_bytes(systems))
    return str(path)


def test_load_stream_is_one_transaction_with_header_rows_and_footer(real_systems, tmp_path):
    path = write_gz(tmp_path / "galaxy_stations.json.gz", real_systems.values())
    out = io.BytesIO()
    stats = state.write_load_stream([path], out, snapshot=True, source_label="it's a test")
    text = out.getvalue().decode("utf-8")
    assert text.startswith("\\set ON_ERROR_STOP on\n")
    header, _, rest = text.partition("FROM stdin;\n")
    assert "BEGIN;" in header and "TRUNCATE v3_state.system_stage;" in header
    data, _, footer = rest.partition("\\.\n")
    rows = [parse_copy_line(line + "\n") for line in data.splitlines()]
    assert len(rows) == 9
    assert [row["seq"] for row in rows] == [str(n) for n in range(1, 10)]
    assert footer == ("SELECT * FROM v3_state.apply_stage(true, 'it''s a test');\n"
                      "TRUNCATE v3_state.system_stage;\nCOMMIT;\n")
    assert stats.files[0]["records"] == 12
    assert stats.files[0]["kept"] == 9
    assert stats.files[0]["no_state"] == 3


def test_load_stream_without_snapshot_does_not_ask_for_removals(real_systems, tmp_path):
    path = write_gz(tmp_path / "part.json.gz", [real_systems["Sol"]])
    out = io.BytesIO()
    state.write_load_stream([path], out, snapshot=False, source_label="x")
    assert b"apply_stage(false, 'x')" in out.getvalue()


def test_load_stream_refuses_input_with_nothing_to_store(real_systems, tmp_path):
    path = write_gz(tmp_path / "empty.json.gz", [real_systems["HD 38179"]])
    out = io.BytesIO()
    with pytest.raises(RuntimeError):
        state.write_load_stream([path], out, snapshot=True, source_label="x")
    assert b"COMMIT" not in out.getvalue()


def test_cut_off_download_stops_before_commit(real_systems, tmp_path):
    whole = tmp_path / "whole.json.gz"
    write_gz(whole, real_systems.values())
    cut = tmp_path / "cut.json.gz"
    cut.write_bytes(whole.read_bytes()[:-200])
    out = io.BytesIO()
    with pytest.raises(Exception):
        state.write_load_stream([str(cut)], out, snapshot=True, source_label="x")
    assert b"COMMIT" not in out.getvalue()
    assert b"apply_stage" not in out.getvalue()


def test_stats_say_which_systems_only_one_file_had(real_systems, tmp_path):
    first = write_gz(tmp_path / "galaxy_populated.json.gz",
                     [real_systems["Sol"], real_systems["Maia"]])
    claimed = synthetic(stations=[{"id": 5, "name": "System Colonisation Ship"}])
    second = write_gz(tmp_path / "galaxy_stations.json.gz",
                      [real_systems["Sol"], claimed, real_systems["Thaile HW-V e2-7"]])
    out = io.BytesIO()
    stats = state.write_load_stream([first, second], out, snapshot=True, source_label="x")
    summary = "\n".join(stats.summary_lines())
    assert "galaxy_populated.json.gz: 2 systems read, 2 kept" in summary
    assert "galaxy_populated.json.gz: 1 kept systems appear in no other input file (1 of them have a population)" in summary
    assert "galaxy_stations.json.gz: 3 systems read, 2 kept, 1 skipped" in summary
    assert "galaxy_stations.json.gz: 1 kept systems appear in no other input file (0 of them have a population)" in summary
    assert "total: 3 different systems staged" in summary


def test_plain_json_file_is_read_without_gzip(real_systems, tmp_path):
    path = tmp_path / "plain.json"
    path.write_bytes(dump_bytes([real_systems["Sol"]]))
    out = io.BytesIO()
    stats = state.write_load_stream([str(path)], out, snapshot=False, source_label="x")
    assert stats.files[0]["kept"] == 1
