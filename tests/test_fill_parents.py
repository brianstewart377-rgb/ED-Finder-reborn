"""The one-time fill-in of what bodies orbit (``spansh_delta.parents``).

The galaxy is the twelve real systems of ``tests/fixtures/ratings_v4_sources``
as production held them on 2026-08-24: imported when the importer read only
the first link of each body's list of what it orbits, so 34 of their bodies
have no parent that the whole lists give. The files are built from the same
systems' real Spansh records fetched on 2026-09-08, edited where a test says so.

Runs on PostgreSQL 18 with every migration applied; needs
RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests. The tests
of reading a file or an address need no database.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
from uuid import uuid4

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "importer" / "src"))

from spansh_delta import apply as galaxy_update  # noqa: E402
from spansh_delta import parents  # noqa: E402
from tests.test_galaxy_update import (  # noqa: E402,F401
    HD_38179, PARENTS_FILLED, SEPTEMBER, SOL, _environment, galaxy, migrated_galaxy, queue, run, snapshot,
)
from tests.test_spansh_delta import fixture_data, records, write_galaxy  # noqa: E402,F401

SOL_BARYCENTRE_31 = 1116892718065256811
EARTH = 108086401534265707


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """The galaxy as production held it before 16 November 2026."""
    with migrated_galaxy(tmp_path_factory.mktemp("galaxy"), parents_filled=False) as built:
        yield built


def galaxy_file(directory: Path, systems) -> str:
    return str(write_galaxy(directory / f"galaxy_{uuid4().hex[:8]}.json.gz", systems))


def check(galaxy, source: str, **options) -> parents.Report:
    return parents.run(galaxy.connection, source, mode="check", progress=False, **options)


def fill(galaxy, source: str, **options) -> parents.Report:
    return parents.run(galaxy.connection, source, mode="apply", progress=False, **options)


def bodies(galaxy) -> dict[int, dict]:
    return dict(galaxy.all("SELECT body_pk, to_jsonb(b) FROM {s}.bodies b"))


def wanted(systems) -> dict[int, int]:
    return {body: parent for record in systems for _, body, parent in parents.fills(record)}


def body_named(record: dict, name: str) -> dict:
    return next(body for body in record["bodies"] if body["name"] == name)


# -- the database ---------------------------------------------------------------


def test_check_says_how_many_it_would_fill_in_and_writes_nothing(galaxy, tmp_path):
    before = snapshot(galaxy)
    report = check(galaxy, galaxy_file(tmp_path, galaxy.records.values()))
    assert snapshot(galaxy) == before
    assert (report.systems, report.found, report.filled) == (12, PARENTS_FILLED, PARENTS_FILLED)
    assert (report.already, report.other_parent, report.not_held) == (0, 0, 0)
    assert not report.stopped_early


def test_fill_in_writes_what_the_importer_now_writes_and_changes_nothing_else(galaxy, tmp_path):
    before, rows_before = snapshot(galaxy), bodies(galaxy)
    report = fill(galaxy, galaxy_file(tmp_path, galaxy.records.values()))
    assert report.filled == report.found == PARENTS_FILLED

    after, rows_after = snapshot(galaxy), bodies(galaxy)
    # Nothing but bodies: no other table, no waiting list for scores, no run recorded.
    assert {name: rows for name, rows in after.items() if name != "bodies"} == \
        {name: rows for name, rows in before.items() if name != "bodies"}
    parents_now = wanted(galaxy.records.values())
    assert {body for body in rows_before if rows_before[body] != rows_after[body]} == set(parents_now)
    for body, parent in parents_now.items():
        assert rows_before[body]["direct_parent_body_pk"] is None
        assert rows_after[body] == {**rows_before[body], "direct_parent_body_pk": parent}
    # Pluto and Charon's centre point now hangs from Sol.
    assert rows_after[SOL_BARYCENTRE_31]["direct_parent_body_pk"] == SOL


def test_afterwards_the_nightly_update_finds_the_bodies_as_it_would_write_them(galaxy, tmp_path):
    # HD 38179 was not re-scanned between August and September. Of its bodies,
    # only what two planets orbit differs from what the update would write
    # (its rings' signals differ too: the August rows have none).
    hd = galaxy.records[HD_38179]
    before = run(galaxy, tmp_path, [hd], mode="check")
    assert dict(before.systems_by_table) == {"bodies": 1, "ring_signal_current": 1}
    fill(galaxy, galaxy_file(tmp_path, [hd]))
    after = run(galaxy, tmp_path, [hd], mode="check")
    assert dict(after.systems_by_table) == {"ring_signal_current": 1}


def test_before_the_fill_in_the_nightly_update_writes_the_same_parents(galaxy, tmp_path):
    hd = galaxy.records[HD_38179]
    update = run(galaxy, tmp_path, [hd])
    assert dict(update.systems_by_table) == {"bodies": 1, "ring_signal_current": 1}
    assert list(queue(galaxy)) == [HD_38179]
    held = bodies(galaxy)
    assert {body: held[body]["direct_parent_body_pk"] for body in wanted([hd])} == wanted([hd])
    report = check(galaxy, galaxy_file(tmp_path, [hd]))
    assert (report.found, report.filled, report.already) == (2, 0, 2)


def test_a_second_run_finds_everything_filled_in_and_changes_nothing(galaxy, tmp_path):
    source = galaxy_file(tmp_path, galaxy.records.values())
    fill(galaxy, source)
    before = snapshot(galaxy)
    again = fill(galaxy, source)
    assert (again.found, again.filled) == (PARENTS_FILLED, 0)
    assert snapshot(galaxy) == before
    report = check(galaxy, source)
    assert (report.filled, report.already) == (0, PARENTS_FILLED)


def test_a_parent_we_hold_is_never_changed(galaxy, tmp_path):
    # Edited: say we hold Sol barycentre 31 as orbiting Earth.
    galaxy.connection.execute(
        f"UPDATE {galaxy.schema}.bodies SET direct_parent_body_pk = %s WHERE body_pk = %s",
        (EARTH, SOL_BARYCENTRE_31))
    source = galaxy_file(tmp_path, galaxy.records.values())
    report = check(galaxy, source)
    assert (report.filled, report.other_parent) == (PARENTS_FILLED - 1, 1)
    assert fill(galaxy, source).filled == PARENTS_FILLED - 1
    assert bodies(galaxy)[SOL_BARYCENTRE_31]["direct_parent_body_pk"] == EARTH


def test_a_body_or_parent_we_do_not_hold_is_left_alone(galaxy, tmp_path):
    systems = deepcopy(galaxy.records)
    # Edited: Sol's centre point 31 and HD 38179's star are bodies we do not hold.
    body_named(systems[SOL], "Sol barycentre 31")["id64"] += 1
    body_named(systems[HD_38179], "HD 38179")["id64"] += 1
    source = galaxy_file(tmp_path, systems.values())
    report = check(galaxy, source)
    assert (report.found, report.filled, report.not_held) == (PARENTS_FILLED, PARENTS_FILLED - 3, 3)
    assert fill(galaxy, source).filled == PARENTS_FILLED - 3
    held = bodies(galaxy)
    assert held[SOL_BARYCENTRE_31]["direct_parent_body_pk"] is None
    assert all(held[body]["direct_parent_body_pk"] is None for body in wanted([galaxy.records[HD_38179]]))


def test_a_body_is_only_ever_hung_from_a_body_of_its_own_system(galaxy, tmp_path):
    # Edited: Sol's record, saying it is HD 38179. Its bodies are held, but in Sol.
    sol = deepcopy(galaxy.records[SOL])
    sol["id64"] = HD_38179
    source = galaxy_file(tmp_path, [sol])
    report = check(galaxy, source)
    assert report.found == report.not_held == 1 and report.filled == 0
    before = snapshot(galaxy)
    assert fill(galaxy, source).filled == 0
    assert snapshot(galaxy) == before


def test_the_fill_in_does_not_start_while_a_galaxy_update_runs_but_the_check_does(galaxy, tmp_path):
    source = galaxy_file(tmp_path, galaxy.records.values())
    with psycopg.connect(galaxy.conninfo, autocommit=True) as update:
        update.execute("SELECT pg_advisory_lock(%s)", (galaxy_update.LOCK_KEY,))
        before = snapshot(galaxy)
        with pytest.raises(galaxy_update.Refused, match="galaxy update is running"):
            fill(galaxy, source)
        assert snapshot(galaxy) == before
        assert check(galaxy, source).filled == PARENTS_FILLED
    assert fill(galaxy, source).filled == PARENTS_FILLED


def test_a_galaxy_update_does_not_start_while_the_fill_in_runs(galaxy, tmp_path, monkeypatch):
    source = galaxy_file(tmp_path, galaxy.records.values())
    nightly = write_galaxy(tmp_path / "galaxy_1day.json.gz", galaxy.records.values())
    tried = []
    reading = parents.lines

    def lines(path, sizes):
        for line in reading(path, sizes):
            if not tried:
                with psycopg.connect(galaxy.conninfo, autocommit=True) as other:
                    with pytest.raises(galaxy_update.UpdateError, match="another galaxy update is running"):
                        galaxy_update.run_file(other, nightly, mode="apply", made_at=SEPTEMBER,
                                               progress=False)
                tried.append(True)
            yield line

    monkeypatch.setattr(parents, "lines", lines)
    assert fill(galaxy, source).filled == PARENTS_FILLED
    assert tried


def test_a_failed_batch_stops_the_run_and_what_was_filled_in_stays(galaxy, tmp_path, monkeypatch):
    source = galaxy_file(tmp_path, galaxy.records.values())
    monkeypatch.setattr(parents, "BATCH_BODIES", 1)
    columns, batches = parents._batch_columns, []

    def third_refused(batch):
        batches.append(batch)
        systems, bodies, found = columns(batch)
        if len(batches) == 3:
            found = [2 ** 70] * len(found)  # not a bigint: the database refuses the batch
        return systems, bodies, found

    monkeypatch.setattr(parents, "_batch_columns", third_refused)
    with pytest.raises(psycopg.errors.NumericValueOutOfRange):
        fill(galaxy, source)
    monkeypatch.undo()
    # The first two batches, of one body each, stay filled in; a second run
    # does the rest. It could start: the first let go of the lock.
    assert fill(galaxy, source).filled == PARENTS_FILLED - 2


def test_a_limit_stops_after_that_many_systems(galaxy, tmp_path):
    report = check(galaxy, galaxy_file(tmp_path, galaxy.records.values()), limit=5)
    assert (report.systems, report.stopped_early) == (5, True)
    assert "Systems read: 5 (stopped early, as asked)" in report.lines()


def test_the_command_checks_then_fills_in_and_says_so_plainly(galaxy, tmp_path, monkeypatch, capsys):
    _environment(monkeypatch, galaxy.conninfo)
    source = galaxy_file(tmp_path, galaxy.records.values())

    assert parents.main(["check", source]) == 0
    out = capsys.readouterr().out
    assert f"Bodies the old importer left without a parent that the file gives one: {PARENTS_FILLED}" in out
    assert f"  would be filled in now: {PARENTS_FILLED}" in out
    assert out.rstrip().endswith("CHECKED: nothing was changed.")

    assert parents.main(["apply", source]) == 0
    out = capsys.readouterr().out
    assert f"  filled in: {PARENTS_FILLED}" in out and out.rstrip().endswith("DONE")
    # No connection details are ever printed.
    assert galaxy.connection.info.dbname not in out and "postgresql" not in out

    with psycopg.connect(galaxy.conninfo, autocommit=True) as update:
        update.execute("SELECT pg_advisory_lock(%s)", (galaxy_update.LOCK_KEY,))
        assert parents.main(["apply", source]) == 2
        out = capsys.readouterr().out
        assert "REFUSED: a galaxy update is running" in out and "Nothing was changed." in out


def test_the_command_says_plainly_when_the_file_is_missing_or_damaged(galaxy, tmp_path, monkeypatch, capsys):
    _environment(monkeypatch, galaxy.conninfo)
    assert parents.main(["check", str(tmp_path / "galaxy.json.gz")]) == 1
    assert "FAILED: there is no file" in capsys.readouterr().out
    assert parents.main(["check", galaxy_file(tmp_path, []), "--limit", "0"]) == 1
    assert "--limit must be 1 or more" in capsys.readouterr().out

    packed = Path(galaxy_file(tmp_path, galaxy.records.values()))
    packed.write_bytes(packed.read_bytes()[:-4000])
    assert parents.main(["apply", str(packed)]) == 1
    out = capsys.readouterr().out
    assert "FAILED: the file ends part-way through" in out
    assert "What was filled in before this stays; running it again fills in the rest." in out


# -- reading a file ---------------------------------------------------------------


def systems_in(source) -> list[int]:
    return [parents.record_of(line)["id64"] for line in parents.lines(str(source), [0, 0])]


def test_a_packed_a_plain_and_a_file_packed_in_parts_all_read_the_same(records, tmp_path):
    packed = write_galaxy(tmp_path / "galaxy.json.gz", records.values())
    plain = write_galaxy(tmp_path / "galaxy.json", records.values(), compress=False)
    data = gzip.decompress(packed.read_bytes())
    parts = tmp_path / "parts.json.gz"
    parts.write_bytes(gzip.compress(data[: len(data) // 2]) + gzip.compress(data[len(data) // 2:]))
    for source in (packed, plain, parts):
        assert systems_in(source) == list(records)


def test_a_file_cut_short_or_damaged_or_of_another_kind_is_refused(records, tmp_path):
    packed = write_galaxy(tmp_path / "galaxy.json.gz", records.values()).read_bytes()
    cut = tmp_path / "cut.json.gz"
    cut.write_bytes(packed[: len(packed) // 2])
    with pytest.raises(parents.SourceError, match="ends part-way through"):
        systems_in(cut)
    damaged = tmp_path / "damaged.json.gz"
    damaged.write_bytes(packed[:-8] + bytes(byte ^ 0xFF for byte in packed[-8:-4]) + packed[-4:])
    with pytest.raises(parents.SourceError, match="damaged"):
        systems_in(damaged)
    pretty = write_galaxy(tmp_path / "pretty.json.gz", records.values(), layout="pretty")
    with pytest.raises(parents.SourceError, match="not a Spansh galaxy file"):
        list(parents.lines(str(pretty), [0, 0]))
    table = tmp_path / "systems.csv"
    table.write_bytes(b"id64,name\n10477373803,Sol\n")
    with pytest.raises(parents.SourceError, match="not a Spansh galaxy file"):
        list(parents.lines(str(table), [0, 0]))
    for line, why in ((b'{"id64": 10477373803, "name": "Sol"', "not a whole system"),
                      (b'{"name": "Sol"}', "not a system with an id64"),
                      (b'{"id64": "10477373803"}', "not a system with an id64")):
        with pytest.raises(parents.SourceError, match=why):
            parents.record_of(line)


# -- reading an address -------------------------------------------------------------


class Spansh(ThreadingHTTPServer):
    """A stand-in for Spansh's download server, on this machine.

    ``drops``: for each request in turn, how many bytes it sends before the
    connection drops (None: all of them). ``versions``: the file's version
    at each request in turn (the last one stays)."""

    def __init__(self, data: bytes, *, drops=(), versions=('"one"',), carries_on: bool = True) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.data, self.drops, self.versions, self.carries_on = data, list(drops), versions, carries_on
        self.asked: list[tuple[str | None, str | None]] = []

    @property
    def url(self) -> str:
        return "http://127.0.0.1:%d/galaxy.json.gz" % self.server_address[1]


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        site: Spansh = self.server  # type: ignore[assignment]
        if self.path != "/galaxy.json.gz":
            self.send_error(404)
            return
        site.asked.append((self.headers.get("Range"), self.headers.get("If-Range")))
        version = site.versions[min(len(site.asked), len(site.versions)) - 1]
        wanted, unchanged = self.headers.get("Range"), self.headers.get("If-Range") in (None, version)
        start = 0
        if wanted and site.carries_on and unchanged:
            start = int(wanted.removeprefix("bytes=").removesuffix("-"))
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, len(site.data) - 1, len(site.data)))
        else:
            self.send_response(200)
        body = site.data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", version)
        self.end_headers()
        drop = site.drops.pop(0) if site.drops else None
        self.wfile.write(body if drop is None else body[:drop])
        self.close_connection = True

    def log_message(self, *args):
        pass


@contextmanager
def serving(site: Spansh):
    thread = threading.Thread(target=site.serve_forever, daemon=True)
    thread.start()
    try:
        yield site
    finally:
        site.shutdown()
        site.server_close()


@pytest.fixture
def quickly(monkeypatch):
    """No proxy for this machine's own address, and no waiting between tries."""
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setattr(parents, "RETRY_WAIT_SECONDS", (0,))


def test_a_dropped_download_carries_on_where_it_stopped(records, tmp_path, quickly, capsys):
    data = write_galaxy(tmp_path / "galaxy.json.gz", records.values()).read_bytes()
    third = len(data) // 3
    with serving(Spansh(data, drops=(third, third))) as site:
        assert systems_in(site.url) == list(records)
    assert site.asked == [(None, None), ("bytes=%d-" % third, '"one"'), ("bytes=%d-" % (2 * third), '"one"')]
    assert "The download stopped at 0.0 GB" in capsys.readouterr().out


def test_a_download_that_cannot_carry_on_stops_rather_than_mixing_two_files(records, tmp_path, quickly):
    data = write_galaxy(tmp_path / "galaxy.json.gz", records.values()).read_bytes()
    # Spansh has put up a new file by the time the connection is picked up again.
    with serving(Spansh(data, drops=(len(data) // 2,), versions=('"one"', '"two"'))) as site:
        with pytest.raises(parents.SourceError, match="would not carry on from there"):
            systems_in(site.url)
    # A server that cannot send part of a file.
    with serving(Spansh(data, drops=(len(data) // 2,), carries_on=False)) as site:
        with pytest.raises(parents.SourceError, match="would not carry on from there"):
            systems_in(site.url)


def test_a_download_that_keeps_failing_gives_up_and_a_missing_file_is_said_so(
        records, tmp_path, quickly, monkeypatch):
    data = write_galaxy(tmp_path / "galaxy.json.gz", records.values()).read_bytes()
    monkeypatch.setattr(parents, "RETRIES", 2)
    with serving(Spansh(data, drops=(0, 0, 0, 0))) as site:
        with pytest.raises(parents.SourceError, match="could not be picked up again"):
            systems_in(site.url)
        assert len(site.asked) == 3
        with pytest.raises(parents.SourceError, match="the server answered 404"):
            systems_in(site.url.replace("galaxy.json.gz", "galaxy_1day.json.gz"))
