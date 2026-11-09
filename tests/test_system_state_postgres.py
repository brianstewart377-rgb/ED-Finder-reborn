"""End-to-end: Spansh dump -> load stream -> psql -> v3_state tables.

Needs a real PostgreSQL and the psql client. Point SYSTEM_STATE_TEST_DSN at a
throwaway database whose name ends in "_test"; the tests drop and recreate the
v3_state schema in it. Without the variable the tests are skipped, unless
REQUIRE_DB_TESTS=1 is set (CI sets it so they can never be skipped silently).
"""
from __future__ import annotations

import copy
import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import spansh_system_state as state

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "v3" / "migrations" / "024_v3_system_state.sql"
LOADER = ROOT / "apps" / "importer" / "src" / "spansh_system_state.py"
FIXTURE_ZIP = ROOT / "tests" / "fixtures" / "ratings_v4_sources" / "spansh-system-dumps.zip"
DSN = os.environ.get("SYSTEM_STATE_TEST_DSN", "")
REQUIRED = os.environ.get("REQUIRE_DB_TESTS") == "1"
SEPARATOR = "\x1f"


def _unavailable_reason() -> str:
    if not DSN:
        return "SYSTEM_STATE_TEST_DSN is not set"
    if shutil.which("psql") is None:
        return "psql is not installed"
    database = urlsplit(DSN).path.lstrip("/")
    if not database.endswith("_test"):
        return "refusing to use database %r: its name must end in _test" % database
    return ""


_REASON = _unavailable_reason()
if _REASON and REQUIRED:
    raise RuntimeError("database tests are required but cannot run: " + _REASON)
pytestmark = pytest.mark.skipif(bool(_REASON), reason=_REASON or "available")


def psql(*args: str, stdin: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", DSN, *args],
        input=stdin, capture_output=True)
    if check and result.returncode != 0:
        raise AssertionError("psql failed:\n" + result.stderr.decode("utf-8", "replace"))
    return result


def query(sql: str) -> list[list[str]]:
    result = psql("-At", "-F", SEPARATOR, "-c", sql)
    return [line.split(SEPARATOR) for line in result.stdout.decode("utf-8").splitlines() if line]


def scalar(sql: str) -> str:
    rows = query(sql)
    assert len(rows) == 1 and len(rows[0]) == 1, rows
    return rows[0][0]


@pytest.fixture(autouse=True)
def fresh_schema():
    psql("-c", "DROP SCHEMA IF EXISTS v3_state CASCADE")
    psql("-f", str(MIGRATION))
    yield


@pytest.fixture(scope="module")
def real_systems() -> dict:
    systems = {}
    with zipfile.ZipFile(FIXTURE_ZIP) as archive:
        for name in archive.namelist():
            if name.endswith(".json"):
                record = json.loads(archive.read(name))
                system = record.get("system", record)
                systems[system["name"]] = system
    return systems


def made_up(number: int, **overrides) -> dict:
    system = {
        "id64": 9_000_000 + number,
        "name": "Made Up %d" % number,
        "coords": {"x": float(number), "y": 0.0, "z": 0.0},
        "date": "2026-10-01 00:00:00+00",
        "population": 1000 + number,
        "factions": [{"name": "Faction %d" % number, "influence": 1.0, "state": "None"}],
        "stations": [],
        "bodies": [],
    }
    system.update(overrides)
    return system


def stream_for(tmp_path: Path, *file_contents, snapshot: bool, not_after=None) -> bytes:
    paths = []
    for index, systems in enumerate(file_contents):
        path = tmp_path / ("dump%d_%d.json.gz" % (index, len(list(tmp_path.iterdir()))))
        lines = ",\n".join(json.dumps(system) for system in systems)
        with gzip.open(path, "wb") as handle:
            handle.write(("[\n" + lines + "\n]\n").encode("utf-8"))
        paths.append(str(path))
    out = io.BytesIO()
    state.write_load_stream(paths, out, snapshot=snapshot, source_label="test",
                            not_after=not_after)
    return out.getvalue()


def load(tmp_path: Path, *file_contents, snapshot: bool = False, check: bool = True):
    return psql(stdin=stream_for(tmp_path, *file_contents, snapshot=snapshot), check=check)


def last_log() -> dict:
    row = query("SELECT full_snapshot, staged_rows, staged_systems, systems_written, "
                "systems_unchanged, systems_removed FROM v3_state.load_log "
                "ORDER BY load_id DESC LIMIT 1")[0]
    keys = ("full_snapshot", "staged_rows", "staged_systems", "written", "unchanged", "removed")
    return dict(zip(keys, [row[0]] + [int(value) for value in row[1:]], strict=True))


def stored_ids() -> set[int]:
    return {int(row[0]) for row in query("SELECT system_id64 FROM v3_state.system_state")}


# -- first load ---------------------------------------------------------------

def test_first_load_stores_inhabited_and_claimed_systems(real_systems, tmp_path):
    load(tmp_path, real_systems.values(), snapshot=True)
    assert scalar("SELECT count(*) FROM v3_state.system_state") == "9"
    assert last_log() == {"full_snapshot": "t", "staged_rows": 9, "staged_systems": 9,
                          "written": 9, "unchanged": 0, "removed": 0}
    assert scalar("SELECT count(*) FROM v3_state.system_stage") == "0"

    row = query(
        "SELECT name, population, allegiance, government, security, primary_economy, "
        "secondary_economy, controlling_faction, controlling_power, power_state, "
        "power_state_undermining, station_count, carrier_count, source, "
        "to_char(system_updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') "
        "FROM v3_state.system_state WHERE system_id64 = 2037701937507")[0]
    assert row == ["Deriv-Dar", "4962233163", "Federation", "Corporate", "High",
                   "Agriculture", "Refinery", "Sirius Special Forces", "Edmund Mahon",
                   "Exploited", "979", "45", "0", "test", "2026-09-07 19:12:35"]


def test_factions_are_stored_one_row_each_with_the_controlling_one_marked(real_systems, tmp_path):
    load(tmp_path, real_systems.values(), snapshot=True)
    source = real_systems["Deriv-Dar"]
    rows = query("SELECT faction_name, influence, is_controlling FROM v3_state.system_faction "
                 "WHERE system_id64 = 2037701937507 ORDER BY faction_name")
    assert [row[0] for row in rows] == sorted(f["name"] for f in source["factions"])
    assert [row[0] for row in rows if row[2] == "t"] == ["Sirius Special Forces"]
    influence = {f["name"]: f["influence"] for f in source["factions"]}
    for name, stored, _ in rows:
        assert float(stored) == pytest.approx(influence[name])
    expected_total = sum(len(system.get("factions") or [])
                         for system in real_systems.values()
                         if state.system_row(system) is not None)
    assert scalar("SELECT count(*) FROM v3_state.system_faction") == str(expected_total)


def test_faction_states_are_stored(real_systems, tmp_path):
    load(tmp_path, real_systems.values(), snapshot=True)
    expected = sum(1 for system in real_systems.values() if state.system_row(system)
                   for faction in system.get("factions") or [] if faction.get("activeStates"))
    assert expected > 0
    assert scalar("SELECT count(*) FROM v3_state.system_faction "
                  "WHERE jsonb_array_length(active_states) > 0") == str(expected)


def test_powers_are_stored_with_the_controlling_one_marked(real_systems, tmp_path):
    load(tmp_path, real_systems.values(), snapshot=True)
    rows = query("SELECT power_name, is_controlling FROM v3_state.system_power "
                 "WHERE system_id64 = 2037701937507 ORDER BY power_name")
    assert rows == [["Edmund Mahon", "t"], ["Jerome Archer", "f"],
                    ["Li Yong-Rui", "f"], ["Zemina Torval", "f"]]
    assert scalar("SELECT conflict_progress FROM v3_state.system_power "
                  "WHERE power_name = 'Nakato Kaine'") == "0"


def test_construction_sites_can_be_found_after_loading(real_systems, tmp_path):
    load(tmp_path, real_systems.values(), snapshot=True)
    rows = query("SELECT name, construction_site_count, "
                 "station_type_counts->>'Space Construction Depot' "
                 "FROM v3_state.system_state WHERE construction_site_count > 0 ORDER BY name")
    assert rows == [["Praea Euq WV-W b2-2", "2", ""], ["Wregoe ZN-X c28-28", "4", "2"]]


def test_a_faction_can_be_looked_up_across_systems(tmp_path):
    shared = {"name": "Shared Faction", "influence": 0.4, "state": "Boom"}
    systems = [made_up(n, factions=[shared, {"name": "Local %d" % n, "influence": 0.6}])
               for n in range(3)]
    load(tmp_path, systems)
    rows = query("SELECT s.name FROM v3_state.system_faction f "
                 "JOIN v3_state.system_state s USING (system_id64) "
                 "WHERE f.faction_name = 'Shared Faction' ORDER BY s.name")
    assert rows == [["Made Up 0"], ["Made Up 1"], ["Made Up 2"]]


# -- reloading ----------------------------------------------------------------

def test_loading_the_same_dump_again_rewrites_nothing(real_systems, tmp_path):
    load(tmp_path, real_systems.values(), snapshot=True)
    before = query("SELECT system_id64, loaded_at, xmin FROM v3_state.system_state ORDER BY 1")
    factions_before = query("SELECT system_id64, faction_name, xmin FROM v3_state.system_faction ORDER BY 1, 2")
    load(tmp_path, real_systems.values(), snapshot=True)
    assert last_log()["written"] == 0 and last_log()["unchanged"] == 9
    assert query("SELECT system_id64, loaded_at, xmin FROM v3_state.system_state ORDER BY 1") == before
    assert query("SELECT system_id64, faction_name, xmin FROM v3_state.system_faction ORDER BY 1, 2") == factions_before


def test_newer_record_replaces_the_system_and_its_faction_list(real_systems, tmp_path):
    load(tmp_path, real_systems.values())
    newer = copy.deepcopy(real_systems["Deriv-Dar"])
    newer["date"] = "2026-10-15 00:00:00+00"
    newer["population"] = 5
    removed = newer["factions"].pop()
    newer["controllingFaction"] = {"name": newer["factions"][0]["name"]}
    load(tmp_path, [newer])
    assert last_log()["written"] == 1
    assert scalar("SELECT population FROM v3_state.system_state "
                  "WHERE system_id64 = 2037701937507") == "5"
    names = [row[0] for row in query(
        "SELECT faction_name FROM v3_state.system_faction WHERE system_id64 = 2037701937507")]
    assert removed["name"] not in names
    assert len(names) == len(newer["factions"])
    assert query("SELECT faction_name FROM v3_state.system_faction "
                 "WHERE system_id64 = 2037701937507 AND is_controlling") == [
                     [newer["factions"][0]["name"]]]
    # the other eight systems were left alone
    assert scalar("SELECT count(*) FROM v3_state.system_state") == "9"


def test_older_record_never_overwrites_a_newer_one(real_systems, tmp_path):
    newer = copy.deepcopy(real_systems["Deriv-Dar"])
    newer["date"] = "2026-10-15 00:00:00+00"
    newer["population"] = 5
    newer["factions"] = newer["factions"][:2]
    load(tmp_path, [newer])
    load(tmp_path, [real_systems["Deriv-Dar"]])
    assert last_log() == {"full_snapshot": "f", "staged_rows": 1, "staged_systems": 1,
                          "written": 0, "unchanged": 1, "removed": 0}
    assert scalar("SELECT population FROM v3_state.system_state "
                  "WHERE system_id64 = 2037701937507") == "5"
    assert scalar("SELECT count(*) FROM v3_state.system_faction "
                  "WHERE system_id64 = 2037701937507") == "2"


def test_same_age_record_with_different_content_is_applied(tmp_path):
    load(tmp_path, [made_up(1)])
    changed = made_up(1, factions=[{"name": "Someone Else", "influence": 1.0}])
    load(tmp_path, [changed])
    assert last_log()["written"] == 1
    assert query("SELECT faction_name FROM v3_state.system_faction") == [["Someone Else"]]


@pytest.mark.parametrize("newest_first", [True, False])
def test_when_two_files_hold_the_same_system_the_newest_wins(tmp_path, newest_first):
    old = made_up(1, date="2026-10-01 00:00:00+00", population=111)
    new = made_up(1, date="2026-10-02 00:00:00+00", population=222)
    files = ([new], [old]) if newest_first else ([old], [new])
    load(tmp_path, *files)
    assert last_log()["staged_rows"] == 2 and last_log()["staged_systems"] == 1
    assert scalar("SELECT population FROM v3_state.system_state") == "222"


# -- full snapshots -----------------------------------------------------------

def test_snapshot_removes_a_system_that_is_no_longer_present(tmp_path):
    systems = [made_up(n) for n in range(10)]
    load(tmp_path, systems, snapshot=True)
    load(tmp_path, systems[:9], snapshot=True)
    assert last_log()["removed"] == 1
    assert stored_ids() == {system["id64"] for system in systems[:9]}
    assert scalar("SELECT count(*) FROM v3_state.system_faction "
                  "WHERE faction_name = 'Faction 9'") == "0"


def test_load_that_is_not_a_snapshot_removes_nothing(tmp_path):
    systems = [made_up(n) for n in range(10)]
    load(tmp_path, systems, snapshot=True)
    load(tmp_path, systems[:1], snapshot=False)
    assert last_log()["removed"] == 0
    assert len(stored_ids()) == 10


def test_snapshot_keeps_a_system_updated_after_the_dump_was_made(tmp_path):
    systems = [made_up(n) for n in range(10)]
    load(tmp_path, systems, snapshot=True)
    psql("-c", "UPDATE v3_state.system_state SET system_updated_at = '2026-10-20Z' "
               "WHERE system_id64 = %d" % systems[9]["id64"])
    load(tmp_path, systems[:9], snapshot=True)
    assert last_log()["removed"] == 0
    assert systems[9]["id64"] in stored_ids()


def test_snapshot_that_would_remove_too_much_is_refused_and_changes_nothing(tmp_path):
    systems = [made_up(n) for n in range(10)]
    load(tmp_path, systems, snapshot=True)
    logs_before = scalar("SELECT count(*) FROM v3_state.load_log")
    changed = [made_up(n, population=5, date="2026-11-01 00:00:00+00") for n in range(7)]
    result = load(tmp_path, changed, snapshot=True, check=False)
    assert result.returncode != 0
    assert b"would remove 3 of 10 stored systems" in result.stderr
    assert len(stored_ids()) == 10
    assert scalar("SELECT count(*) FROM v3_state.system_state WHERE population = 5") == "0"
    assert scalar("SELECT count(*) FROM v3_state.load_log") == logs_before
    assert scalar("SELECT count(*) FROM v3_state.system_stage") == "0"


def test_snapshot_removing_exactly_a_fifth_is_allowed(tmp_path):
    systems = [made_up(n) for n in range(10)]
    load(tmp_path, systems, snapshot=True)
    load(tmp_path, systems[:8], snapshot=True)
    assert last_log()["removed"] == 2


# -- failure paths -------------------------------------------------------------

def test_stream_cut_off_in_the_middle_changes_nothing(real_systems, tmp_path):
    load(tmp_path, [made_up(1)])
    whole = stream_for(tmp_path, real_systems.values(), snapshot=True)
    cut_at = whole.index(b"\\.\n")
    for cut in (whole[:cut_at], whole[:cut_at - 500], whole[:whole.index(b"COMMIT")]):
        psql(stdin=cut, check=False)
        assert stored_ids() == {made_up(1)["id64"]}
        assert scalar("SELECT count(*) FROM v3_state.system_stage") == "0"
        assert scalar("SELECT count(*) FROM v3_state.load_log") == "1"


def test_cut_off_download_piped_through_the_real_program_changes_nothing(real_systems, tmp_path):
    load(tmp_path, [made_up(1)])
    whole = tmp_path / "galaxy_stations.json.gz"
    lines = ",\n".join(json.dumps(system) for system in real_systems.values())
    with gzip.open(whole, "wb") as handle:
        handle.write(("[\n" + lines + "\n]\n").encode("utf-8"))
    cut = tmp_path / "cut.json.gz"
    cut.write_bytes(whole.read_bytes()[:-300])

    loader = subprocess.Popen([sys.executable, str(LOADER), "--snapshot", str(cut)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    client = subprocess.run(["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", DSN],
                            stdin=loader.stdout, capture_output=True)
    loader.stdout.close()
    loader_error = loader.stderr.read().decode("utf-8")
    assert loader.wait() == 1
    assert "STOPPED, nothing was loaded" in loader_error
    assert client.returncode in (0, 3)
    assert stored_ids() == {made_up(1)["id64"]}
    assert scalar("SELECT count(*) FROM v3_state.load_log") == "1"


def test_the_real_program_loads_a_whole_dump_through_a_pipe(real_systems, tmp_path):
    whole = tmp_path / "galaxy_stations.json.gz"
    lines = ",\n".join(json.dumps(system) for system in real_systems.values())
    with gzip.open(whole, "wb") as handle:
        handle.write(("[\n" + lines + "\n]\n").encode("utf-8"))
    loader = subprocess.Popen([sys.executable, str(LOADER), "--snapshot", str(whole)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    client = subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", DSN],
                            stdin=loader.stdout, capture_output=True)
    loader.stdout.close()
    summary = loader.stderr.read().decode("utf-8")
    assert loader.wait() == 0, summary
    assert client.returncode == 0, client.stderr.decode("utf-8")
    assert "galaxy_stations.json.gz: 12 systems read, 9 kept, 3 skipped" in summary
    assert b"COMMIT" in client.stdout
    assert scalar("SELECT count(*) FROM v3_state.system_state") == "9"
    assert scalar("SELECT source FROM v3_state.load_log") == "spansh:galaxy_stations.json.gz"


def test_empty_stage_is_refused(tmp_path):
    result = psql("-c", "SELECT * FROM v3_state.apply_stage(true, 'x')", check=False)
    assert result.returncode != 0
    assert b"nothing staged" in result.stderr


def test_awkward_names_arrive_intact(tmp_path):
    name = 'Tab\there "quoted" back\\slash \u00e9\u4e2d\U0001F680 it\'s'
    faction = 'Faction "A"\\B\tC \u00fc'
    load(tmp_path, [made_up(1, name=name, factions=[{"name": faction, "influence": 0.5}])])
    assert psql("-At", "-c", "SELECT encode(convert_to(name, 'UTF8'), 'hex') "
                "FROM v3_state.system_state").stdout.decode().strip() == name.encode("utf-8").hex()
    assert psql("-At", "-c", "SELECT encode(convert_to(faction_name, 'UTF8'), 'hex') "
                "FROM v3_state.system_faction").stdout.decode().strip() == faction.encode("utf-8").hex()


def test_applying_the_migration_again_keeps_the_data(real_systems, tmp_path):
    load(tmp_path, real_systems.values(), snapshot=True)
    psql("-f", str(MIGRATION))
    assert scalar("SELECT count(*) FROM v3_state.system_state") == "9"
    load(tmp_path, real_systems.values(), snapshot=True)
    assert last_log()["unchanged"] == 9


# -- timestamps dated in the future ---------------------------------------------

FUTURE = "2198-09-24 02:41:44+00"


def test_future_dated_station_does_not_set_the_record_time(tmp_path):
    system = made_up(1, stations=[
        {"id": 1, "name": "Real", "type": "Outpost", "updateTime": "2026-10-05 00:00:00+00"},
        {"id": 2, "name": "Broken clock", "type": "Outpost", "updateTime": FUTURE}])
    load(tmp_path, [system])
    assert query("SELECT to_char(system_updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD'), "
                 "to_char(stations_updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD'), station_count "
                 "FROM v3_state.system_state") == [["2026-10-05", "2026-10-05", "2"]]
    assert scalar("SELECT to_char(newest_record_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') "
                  "FROM v3_state.load_log") == "2026-10-05"


def test_stored_row_dated_in_the_future_is_repaired_by_the_next_load(tmp_path):
    load(tmp_path, [made_up(1), made_up(2)])
    psql("-c", "UPDATE v3_state.system_state SET system_updated_at = '%s', population = 1 "
               "WHERE system_id64 = %d" % (FUTURE, made_up(1)["id64"]))
    load(tmp_path, [made_up(1), made_up(2)])
    assert last_log()["written"] == 1
    assert query("SELECT population, to_char(system_updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') "
                 "FROM v3_state.system_state WHERE system_id64 = %d" % made_up(1)["id64"]) == [
                     [str(made_up(1)["population"]), "2026-10-01"]]


def test_stored_row_dated_in_the_future_is_removed_when_a_snapshot_lacks_it(tmp_path):
    systems = [made_up(n) for n in range(10)]
    load(tmp_path, systems, snapshot=True)
    psql("-c", "UPDATE v3_state.system_state SET system_updated_at = '%s' "
               "WHERE system_id64 = %d" % (FUTURE, systems[9]["id64"]))
    load(tmp_path, systems[:9], snapshot=True)
    assert last_log()["removed"] == 1
    assert systems[9]["id64"] not in stored_ids()


def test_staged_record_dated_in_the_future_is_refused(tmp_path):
    load(tmp_path, [made_up(1)])
    bad = made_up(2, date=FUTURE)
    stream = stream_for(tmp_path, [bad], snapshot=False, not_after="9999-12-31 23:59:59+00")
    result = psql(stdin=stream, check=False)
    assert result.returncode != 0
    assert b"dated in the future" in result.stderr
    assert stored_ids() == {made_up(1)["id64"]}


def test_newest_station_time_is_stored_and_empty_without_stations(real_systems, tmp_path):
    load(tmp_path, [real_systems["Sol"], made_up(1)])
    rows = query("SELECT name, to_char(stations_updated_at AT TIME ZONE 'UTC', "
                 "'YYYY-MM-DD HH24:MI:SS') FROM v3_state.system_state ORDER BY name")
    assert rows == [["Made Up 1", ""], ["Sol", "2026-09-08 19:55:40"]]


def test_migration_adds_the_station_time_column_to_an_older_install(tmp_path):
    psql("-c", "ALTER TABLE v3_state.system_state DROP COLUMN stations_updated_at; "
               "ALTER TABLE v3_state.system_stage DROP COLUMN stations_updated_at")
    psql("-f", str(MIGRATION))
    load(tmp_path, [made_up(1, stations=[
        {"id": 1, "name": "Dock", "type": "Outpost", "updateTime": "2026-10-03 00:00:00+00"}])])
    assert scalar("SELECT to_char(stations_updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') "
                  "FROM v3_state.system_state") == "2026-10-03"
