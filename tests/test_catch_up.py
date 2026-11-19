"""Catching up with Spansh's whole-galaxy file: saving the systems changed since
a day (``spansh_delta.recent``), then applying them with the nightly update.

The records are the twelve real Spansh records of
``tests/fixtures/ratings_v4_sources`` (fetched 2026-09-08), edited where a test
says so. Ten were changed in Spansh after 22 August 2026; two were not (HD 38179
last in 2021). The database tests run on
PostgreSQL 18 with every migration applied, against the galaxy as production
held it on 2026-08-24 (imported from Spansh's whole-galaxy file of that day),
and need RATINGS_V4_VALIDATION_DATABASE_URL like the other database tests.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import importlib.util
import json
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "importer" / "src"))

from spansh_delta import apply as galaxy_update  # noqa: E402
from spansh_delta import recent, source  # noqa: E402
from spansh_delta.stream import SpanshStream  # noqa: E402
from tests.test_galaxy_update import (  # noqa: E402,F401
    HD_38179, SEPTEMBER, galaxy, queue, run, template,
)
from tests.test_spansh_delta import fixture_data, records, write_galaxy  # noqa: E402,F401

_spec = importlib.util.spec_from_file_location("galaxy_update_launcher", ROOT / "deploy" / "galaxy_update.py")
launcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launcher)

SINCE = date(2026, 8, 22)
TODAY = date(2026, 11, 19)
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")


def dated(record: dict, day: str) -> dict:
    """The record as if Spansh had last changed anything in it on ``day``."""
    def redate(value):
        if isinstance(value, dict):
            return {key: redate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redate(item) for item in value]
        if isinstance(value, str) and _DAY.match(value):
            return day + value[10:]
        return value
    return redate(deepcopy(record))


def newest(record: dict) -> str:
    """The newest date anywhere in a record, as Spansh writes it."""
    found = []

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and _DAY.match(value):
            found.append(value)
    walk(record)
    return max(found)


def changed_since(records: dict, day: date) -> list[int]:
    """The systems with any date on or after ``day``, in file order."""
    return [system for system, record in records.items() if newest(record)[:10] >= day.isoformat()]


def saved_in(path: Path) -> list[int]:
    return [item.record["id64"] for item in SpanshStream(path, parser="lines")]


def line_of(record: dict) -> bytes:
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode()


# -- which systems count as changed ----------------------------------------------


def test_a_line_counts_from_the_first_moment_of_the_day_on():
    quick, full = recent.marks(SINCE, TODAY)
    for stamp, counts in (("2026-08-21 23:59:59+00", False), ("2026-08-22 00:00:00+00", True),
                          ("2026-08-31T12:00:00", True), ("2026-09-01 00:00:00+00", True),
                          ("2026-12-31 23:59:59+00", True), ("2025-09-01 00:00:00+00", False),
                          ("2021-08-25 10:00:00+00", False)):
        line = line_of({"id64": 1, "name": "Sol", "bodies": [{"updateTime": stamp}]})
        assert recent.changed(line, quick, full) is counts, stamp


def test_a_day_late_in_the_year_reaches_into_the_next():
    quick, full = recent.marks(date(2025, 12, 30), date(2026, 1, 5))
    for stamp, counts in (("2025-12-29 23:00:00", False), ("2025-12-30 00:00:00", True),
                          ("2026-01-01 00:00:00", True), ("2024-12-31 00:00:00", False)):
        assert recent.changed(line_of({"id64": 1, "date": stamp}), quick, full) is counts, stamp
    with pytest.raises(ValueError, match="after today"):
        recent.marks(date(2026, 11, 20), TODAY)


def test_every_date_in_a_real_record_is_looked_at(records):
    quick, full = recent.marks(SINCE, TODAY)
    changed = changed_since(records, SINCE)
    assert len(changed) == 10 and HD_38179 not in changed
    for system, record in records.items():
        assert recent.changed(line_of(record), quick, full) is (system in changed)
        # Every date moved before the day, or after it.
        assert not recent.changed(line_of(dated(record, "2021-03-04")), quick, full)
        assert recent.changed(line_of(dated(record, "2026-09-30")), quick, full)


# -- saving them ---------------------------------------------------------------------


def test_the_changed_systems_are_saved_as_spansh_wrote_them(records, tmp_path):
    whole = write_galaxy(tmp_path / "galaxy.json.gz", records.values())
    output = tmp_path / "galaxy_since_2026-08-22.json.gz"

    report = recent.save(str(whole), output, SINCE, today=TODAY, progress=False)

    changed = changed_since(records, SINCE)
    assert (report.systems, report.saved, report.stopped_early) == (12, 10, False)
    assert saved_in(output) == changed
    # Each saved line is the very line Spansh wrote.
    wanted = [line for line in source.lines(str(whole), [0, 0])
              if source.record_of(line)["id64"] in changed]
    assert list(source.lines(str(output), [0, 0])) == wanted
    assert json.loads(output.with_name(output.name + ".json").read_text()) == {
        "since": "2026-08-22", "systems_read": 12, "systems_saved": 10, "stopped_early": False}
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "galaxy.json.gz", output.name, output.name + ".json"]


def test_nothing_changed_saves_an_empty_file_the_update_can_read(records, tmp_path):
    whole = write_galaxy(tmp_path / "galaxy.json.gz", [dated(r, "2021-03-04") for r in records.values()])
    output = tmp_path / "saved.json.gz"
    assert recent.save(str(whole), output, SINCE, today=TODAY, progress=False).saved == 0
    assert saved_in(output) == []


def test_a_file_that_cannot_be_read_to_the_end_saves_nothing(records, tmp_path):
    whole = write_galaxy(tmp_path / "galaxy.json.gz", records.values())
    whole.write_bytes(whole.read_bytes()[:-4000])
    output = tmp_path / "saved.json.gz"
    with pytest.raises(source.SourceError, match="ends part-way"):
        recent.save(str(whole), output, SINCE, today=TODAY, progress=False)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["galaxy.json.gz"]


def test_a_limit_is_noted_as_stopping_early(records, tmp_path):
    whole = write_galaxy(tmp_path / "galaxy.json.gz", records.values())
    output = tmp_path / "saved.json.gz"
    report = recent.save(str(whole), output, SINCE, today=TODAY, limit=5, progress=False)
    first_five = dict(list(records.items())[:5])
    assert (report.systems, report.saved, report.stopped_early) == (
        5, len(changed_since(first_five, SINCE)), True)
    assert json.loads(output.with_name(output.name + ".json").read_text())["stopped_early"] is True


def test_the_command_saves_and_says_so_plainly(records, tmp_path, capsys):
    whole = write_galaxy(tmp_path / "galaxy.json.gz", records.values())
    output = tmp_path / "saved.json.gz"
    assert recent.main(["save", str(whole), str(output), "--since", "2026-08-22"]) == 0
    out = capsys.readouterr().out
    assert "Systems read: 12" in out and "Changed on or after 22 Aug 2026, saved: 10" in out
    assert out.rstrip().endswith("SAVED")
    assert recent.main(["save", str(tmp_path / "missing.json.gz"), str(output), "--since", "2026-08-22"]) == 1
    assert "FAILED: there is no file" in capsys.readouterr().out


# -- applying them -------------------------------------------------------------------


def test_applying_the_saved_systems_brings_exactly_them_up_to_date(galaxy, tmp_path):
    whole = write_galaxy(tmp_path / "galaxy.json.gz", galaxy.records.values())
    saved = tmp_path / "galaxy_since_2026-08-22.json.gz"
    changed = changed_since(galaxy.records, SINCE)
    left = [system for system in galaxy.records if system not in changed]
    assert recent.save(str(whole), saved, SINCE, today=TODAY, progress=False).saved == len(changed)

    def versions(systems):
        return sorted(galaxy.all("SELECT body_pk, xmin::text FROM {s}.bodies WHERE system_id64 = ANY(%s)",
                                 systems))
    before = versions(left)

    report = galaxy_update.run_file(galaxy.connection, saved, mode="apply", made_at=SEPTEMBER,
                                    progress=False, total=len(changed))

    assert report.systems_read == len(changed) and not report.set_aside
    after = run(galaxy, tmp_path, [galaxy.records[system] for system in changed], mode="check")
    assert (after.changed, after.unchanged) == (0, len(changed))
    assert set(queue(galaxy)) <= set(changed)
    # The systems Spansh had not changed since then were not in the saved
    # file, so their rows were not touched.
    assert HD_38179 in left and versions(left) == before


def test_the_progress_says_how_long_is_left_when_the_total_is_known():
    report = galaxy_update.Report("apply", "galaxy_since_2026-08-22.json.gz")
    report.systems_read = 250
    line = galaxy_update._progress(report, 1_000, 3_600)
    assert line.startswith("  ... 250 of 1,000 systems read, ")
    assert line.endswith(", 60 min, about 3 h 0 min to go")
    # Without a total, as every night: no guess.
    assert galaxy_update._progress(report, None, 3_600).endswith("changed or new, 60 min")


# -- where the galaxy is up to (the launcher's queries) ------------------------------


def test_the_launcher_knows_when_the_galaxy_was_imported_and_last_brought_up_to_date(galaxy, tmp_path):
    def answer(statement: str) -> str:
        return galaxy.connection.execute(statement).fetchone()[0]

    assert answer(launcher.IMPORTED_SQL) == "2026-08-24"
    # Before any update, the choice of file starts from the import.
    assert answer(launcher.COVERED_SQL).startswith("2026-08-24 ")
    run(galaxy, tmp_path, list(galaxy.records.values())[:2])
    assert answer(launcher.COVERED_SQL) == "2026-09-08 16:00:00"
    assert answer(launcher.IMPORTED_SQL) == "2026-08-24"
