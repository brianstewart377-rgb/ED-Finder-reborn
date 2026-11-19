"""Reading a Spansh galaxy file one system at a time (``spansh_delta.source``):
from disk, or from an address as it downloads.

The files are built from the real Spansh records of
``tests/fixtures/ratings_v4_sources`` (fetched 2026-09-08). A small local web
server stands in for Spansh's download server. No database.
"""
from __future__ import annotations

from contextlib import contextmanager
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "importer" / "src"))

from spansh_delta import source  # noqa: E402
from tests.test_spansh_delta import fixture_data, records, write_galaxy  # noqa: E402,F401


# -- reading a file ---------------------------------------------------------------


def systems_in(place) -> list[int]:
    return [source.record_of(line)["id64"] for line in source.lines(str(place), [0, 0])]


def test_a_packed_a_plain_and_a_file_packed_in_parts_all_read_the_same(records, tmp_path):
    packed = write_galaxy(tmp_path / "galaxy.json.gz", records.values())
    plain = write_galaxy(tmp_path / "galaxy.json", records.values(), compress=False)
    data = gzip.decompress(packed.read_bytes())
    parts = tmp_path / "parts.json.gz"
    parts.write_bytes(gzip.compress(data[: len(data) // 2]) + gzip.compress(data[len(data) // 2:]))
    for place in (packed, plain, parts):
        assert systems_in(place) == list(records)


def test_a_file_cut_short_or_damaged_or_of_another_kind_is_refused(records, tmp_path):
    packed = write_galaxy(tmp_path / "galaxy.json.gz", records.values()).read_bytes()
    cut = tmp_path / "cut.json.gz"
    cut.write_bytes(packed[: len(packed) // 2])
    with pytest.raises(source.SourceError, match="ends part-way through"):
        systems_in(cut)
    damaged = tmp_path / "damaged.json.gz"
    damaged.write_bytes(packed[:-8] + bytes(byte ^ 0xFF for byte in packed[-8:-4]) + packed[-4:])
    with pytest.raises(source.SourceError, match="damaged"):
        systems_in(damaged)
    pretty = write_galaxy(tmp_path / "pretty.json.gz", records.values(), layout="pretty")
    with pytest.raises(source.SourceError, match="not a Spansh galaxy file"):
        list(source.lines(str(pretty), [0, 0]))
    table = tmp_path / "systems.csv"
    table.write_bytes(b"id64,name\n10477373803,Sol\n")
    with pytest.raises(source.SourceError, match="not a Spansh galaxy file"):
        list(source.lines(str(table), [0, 0]))
    for line, why in ((b'{"id64": 10477373803, "name": "Sol"', "not a whole system"),
                      (b'{"name": "Sol"}', "not a system with an id64"),
                      (b'{"id64": "10477373803"}', "not a system with an id64")):
        with pytest.raises(source.SourceError, match=why):
            source.record_of(line)


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
    monkeypatch.setattr(source, "RETRY_WAIT_SECONDS", (0,))


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
        with pytest.raises(source.SourceError, match="would not carry on from there"):
            systems_in(site.url)
    # A server that cannot send part of a file.
    with serving(Spansh(data, drops=(len(data) // 2,), carries_on=False)) as site:
        with pytest.raises(source.SourceError, match="would not carry on from there"):
            systems_in(site.url)


def test_a_download_that_keeps_failing_gives_up_and_a_missing_file_is_said_so(
        records, tmp_path, quickly, monkeypatch):
    data = write_galaxy(tmp_path / "galaxy.json.gz", records.values()).read_bytes()
    monkeypatch.setattr(source, "RETRIES", 2)
    with serving(Spansh(data, drops=(0, 0, 0, 0))) as site:
        with pytest.raises(source.SourceError, match="could not be picked up again"):
            systems_in(site.url)
        assert len(site.asked) == 3
        with pytest.raises(source.SourceError, match="the server answered 404"):
            systems_in(site.url.replace("galaxy.json.gz", "galaxy_1day.json.gz"))
