"""The nightly refresh of the system state tables (deploy/state_refresh.py).

A small local web server stands in for Spansh. The download tests need
nothing else. The load tests pipe the real loader into the real psql and need
SYSTEM_STATE_TEST_DSN (a database whose name ends in _test).
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "deploy" / "state_refresh.py"
LOADER = ROOT / "apps" / "importer" / "src" / "spansh_system_state.py"

_spec = importlib.util.spec_from_file_location("state_refresh", TOOL)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


class Spansh:
    """Serves named files; counts requests; can cut a download short."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, str]] = {}
        self.requests: list[tuple[str, str]] = []
        self.cut_short: set[str] = set()
        site = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, with_body: bool) -> None:
                name = self.path.lstrip("/")
                site.requests.append((self.command, name))
                if name not in site.files:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body, modified = site.files[name]
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Last-Modified", modified)
                self.send_header("ETag", '"%s-%d"' % (modified[-12:-4], len(body)))
                self.end_headers()
                if with_body:
                    self.wfile.write(body[: len(body) // 2] if name in site.cut_short else body)

            def do_HEAD(self):  # noqa: N802
                self._send(False)

            def do_GET(self):  # noqa: N802
                self._send(True)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def origin(self) -> str:
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def publish(self, name: str, body: bytes, modified: str) -> None:
        self.files[name] = (body, modified)

    def downloads(self) -> list[str]:
        return [name for method, name in self.requests if method == "GET"]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def spansh(monkeypatch):
    # The workspace may route HTTP through a proxy; the stand-in is local.
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    site = Spansh()
    yield site
    site.close()


def dump(*systems: dict) -> bytes:
    lines = ",\n".join(json.dumps(system) for system in systems)
    return gzip.compress(("[\n" + lines + "\n]\n").encode("utf-8"))


def system(number: int, **overrides) -> dict:
    record = {
        "id64": 9_000_000 + number,
        "name": "Made Up %d" % number,
        "coords": {"x": float(number), "y": 0.0, "z": 0.0},
        "date": "2026-10-01 00:00:00+00",
        "population": 1000 + number,
        "factions": [{"name": "Faction %d" % number, "influence": 1.0, "state": "None"}],
        "stations": [],
        "bodies": [],
    }
    record.update(overrides)
    return record


MONDAY = "Mon, 09 Nov 2026 02:10:00 GMT"
TUESDAY = "Tue, 10 Nov 2026 02:10:00 GMT"


def settings(spansh: Spansh, directory: Path, **extra: str):
    environ = {
        "EDFINDER_REFRESH_DIR": str(directory),
        "EDFINDER_REFRESH_URLS": "%s/stations.json.gz %s/populated.json.gz" % (
            spansh.origin, spansh.origin),
        "EDFINDER_REFRESH_PSQL": "psql -X -q -d " + DSN,
        "EDFINDER_REFRESH_LOADER": str(LOADER),
    }
    environ.update(extra)
    return tool.Settings(environ=environ, conf={})


# -- settings ---------------------------------------------------------------------

def test_defaults_point_at_spansh_and_the_production_database():
    chosen = tool.Settings(environ={}, conf={})
    assert chosen.urls == [
        "https://downloads.spansh.co.uk/galaxy_stations.json.gz",
        "https://downloads.spansh.co.uk/galaxy_populated.json.gz",
    ]
    assert chosen.psql[:3] == ["docker", "exec", "-i"] and "psql" in chosen.psql
    assert chosen.loader == TOOL.parent / "spansh_system_state.py"
    assert "password" not in " ".join(chosen.psql).lower()


def test_environment_wins_over_the_conf_file_which_wins_over_defaults():
    chosen = tool.Settings(
        environ={"EDFINDER_REFRESH_DIR": "/from-environment"},
        conf={"EDFINDER_REFRESH_DIR": "/from-conf", "EDFINDER_REFRESH_PSQL": "psql -d x"})
    assert str(chosen.directory) == "/from-environment"
    assert chosen.psql == ["psql", "-d", "x"]


@pytest.mark.parametrize("urls, message", [
    ("", "names no file"),
    ("ftp://example.test/a.gz", "is not a file address"),
    ("https://a.test/x.gz https://b.test/x.gz", "two files have the same name"),
])
def test_bad_file_list_is_refused(urls, message):
    with pytest.raises(tool.RefreshError, match=message):
        tool.Settings(environ={"EDFINDER_REFRESH_URLS": urls}, conf={})


# -- downloads --------------------------------------------------------------------

def test_download_is_checked_and_then_swapped_in(spansh, tmp_path):
    spansh.publish("stations.json.gz", b"first version", MONDAY)
    target = tmp_path / "stations.json.gz"
    version = tool.remote_version(spansh.origin + "/stations.json.gz")
    assert version["size"] == 13 and version["modified"] == MONDAY

    assert not tool.have_version(target, version)
    tool.download(version, target)
    assert target.read_bytes() == b"first version"
    assert tool.have_version(target, version)
    assert not (tmp_path / "stations.json.gz.part").exists()

    spansh.publish("stations.json.gz", b"second, longer version", TUESDAY)
    newer = tool.remote_version(spansh.origin + "/stations.json.gz")
    assert not tool.have_version(target, newer)


def test_cut_off_download_fails_and_keeps_the_old_copy(spansh, tmp_path):
    spansh.publish("stations.json.gz", b"old but whole", MONDAY)
    target = tmp_path / "stations.json.gz"
    old = tool.remote_version(spansh.origin + "/stations.json.gz")
    tool.download(old, target)

    spansh.publish("stations.json.gz", b"new file that will be cut in half", TUESDAY)
    spansh.cut_short.add("stations.json.gz")
    new = tool.remote_version(spansh.origin + "/stations.json.gz")
    with pytest.raises(tool.RefreshError, match="download (failed|stopped)"):
        tool.download(new, target)

    assert target.read_bytes() == b"old but whole"
    assert tool.have_version(target, old) and not tool.have_version(target, new)
    assert not (tmp_path / "stations.json.gz.part").exists()


def test_missing_file_and_unreachable_server_are_reported_plainly(spansh, tmp_path):
    with pytest.raises(tool.RefreshError, match="could not be reached"):
        tool.remote_version(spansh.origin + "/nothing-here.json.gz")
    with pytest.raises(tool.RefreshError, match="could not be reached"):
        tool.remote_version("http://127.0.0.1:9/stations.json.gz")


def test_run_refuses_when_the_disk_is_too_full(spansh, tmp_path, monkeypatch, capsys):
    spansh.publish("stations.json.gz", dump(system(1)), MONDAY)
    spansh.publish("populated.json.gz", dump(system(1)), MONDAY)
    monkeypatch.setattr(tool.shutil, "disk_usage",
                        lambda _: types.SimpleNamespace(free=10 * 1024 * 1024))
    with pytest.raises(tool.RefreshError, match="not enough free disk space"):
        tool.cmd_run(settings(spansh, tmp_path / "work"))
    assert spansh.downloads() == []


def test_two_refreshes_cannot_run_at_once(spansh, tmp_path):
    import fcntl

    work = tmp_path / "work"
    work.mkdir()
    with open(work / "lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(tool.RefreshError, match="already running"):
            tool.cmd_run(settings(spansh, work))
    assert spansh.requests == []


def test_main_prints_a_plain_failure_and_exits_1(spansh, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EDFINDER_REFRESH_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("EDFINDER_REFRESH_URLS", spansh.origin + "/absent.json.gz")
    assert tool.main(["run"]) == 1
    assert "FAILED: " in capsys.readouterr().out


# -- the load, with the real loader and the real psql -------------------------------

DSN = os.environ.get("SYSTEM_STATE_TEST_DSN", "")


@pytest.fixture
def database():
    from tests import test_system_state_postgres as state_db

    if state_db._REASON:
        pytest.skip(state_db._REASON)
    state_db.psql("-c", "DROP SCHEMA IF EXISTS v3_state CASCADE")
    state_db.psql("-f", str(state_db.MIGRATION))
    return state_db


def test_run_downloads_loads_and_then_has_nothing_to_do(spansh, tmp_path, database, capsys):
    claimed = system(6, population=0, stations=[
        {"name": "Depot", "type": "Space Construction Depot",
         "updateTime": "2026-10-02 00:00:00+00"}])
    inhabited = [system(n) for n in range(1, 6)]
    spansh.publish("stations.json.gz", dump(*inhabited[:2], claimed), MONDAY)
    spansh.publish("populated.json.gz", dump(*inhabited), MONDAY)
    chosen = settings(spansh, tmp_path / "work")

    assert tool.cmd_run(chosen) == 0
    everything = {9_000_000 + n for n in range(1, 7)}
    assert database.stored_ids() == everything
    assert database.last_log()["full_snapshot"] == "t"
    out = capsys.readouterr().out
    assert "DONE. Last load: " in out and "6 systems read, 6 changed, 0 removed" in out
    assert sorted(spansh.downloads()) == ["populated.json.gz", "stations.json.gz"]

    # Same files next night: no download, no load.
    loads = database.scalar("SELECT count(*) FROM v3_state.load_log")
    assert tool.cmd_run(chosen) == 0
    assert "nothing new since the last load" in capsys.readouterr().out
    assert len(spansh.downloads()) == 2
    assert database.scalar("SELECT count(*) FROM v3_state.load_log") == loads

    # Only the stations file changed: only it is downloaded, both are loaded,
    # and the claim that has left the files is removed.
    newer = system(1, date="2026-11-09 00:00:00+00")
    spansh.publish("stations.json.gz", dump(newer, inhabited[1]), TUESDAY)
    assert tool.cmd_run(chosen) == 0
    assert spansh.downloads().count("stations.json.gz") == 2
    assert spansh.downloads().count("populated.json.gz") == 1
    assert database.stored_ids() == everything - {9_000_006}
    assert "1 removed" in capsys.readouterr().out

    assert tool.cmd_status(chosen) == 0
    status = capsys.readouterr().out
    assert "Last load: " in status and "stations.json.gz: " in status and TUESDAY in status


def test_failed_load_changes_nothing_and_is_retried_without_downloading_again(
        spansh, tmp_path, database, capsys):
    spansh.publish("stations.json.gz", dump(system(1)), MONDAY)
    spansh.publish("populated.json.gz", dump(system(1)), MONDAY)
    work = tmp_path / "work"

    broken = settings(spansh, work, EDFINDER_REFRESH_PSQL="psql -X -q -d " + DSN + "_missing")
    with pytest.raises(tool.RefreshError, match="nothing was loaded"):
        tool.cmd_run(broken)
    assert database.stored_ids() == set()
    assert len(spansh.downloads()) == 2

    assert tool.cmd_run(settings(spansh, work)) == 0
    assert database.stored_ids() == {9_000_001}
    assert len(spansh.downloads()) == 2  # the files from the failed night were reused


def test_damaged_file_loads_nothing(spansh, tmp_path, database, capsys):
    whole = dump(*(system(n) for n in range(1, 400)))
    spansh.publish("stations.json.gz", whole[: len(whole) // 2], MONDAY)  # cut-off gzip
    spansh.publish("populated.json.gz", dump(system(1)), MONDAY)
    with pytest.raises(tool.RefreshError, match="nothing was loaded"):
        tool.cmd_run(settings(spansh, tmp_path / "work"))
    assert database.stored_ids() == set()
    assert database.scalar("SELECT count(*) FROM v3_state.load_log") == "0"
