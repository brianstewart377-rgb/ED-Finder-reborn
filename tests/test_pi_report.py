"""The machine report (deploy/pi_report.py): it only reads, it says what it
cannot read and carries on, and it never shows what it must not.

The database it asks is the scored test database, on PostgreSQL 18, by the
same road as the backup tests: a stand-in ``docker`` sends ``docker exec``
to the real container, or, where there is no Docker, to PostgreSQL's own
programs on this machine. The site it times is a small web server started
by the test on this machine's own address.

The Pi's own files (/proc, /sys) cannot be had here. The few lines that
stand in for them are in the formats the Pi's system and "vcgencmd"
document; the first run on the Pi itself is the real test of those.

Needs RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

from tests.test_backup import CONTAINER, PROGRAMS, _REASON
from tests.test_galaxy_scores import galaxy, template  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "deploy" / "pi_report.py"
CONTAINER_NAME = "report-test-db"

_spec = importlib.util.spec_from_file_location("edfinder_pi_report", TOOL)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

needs_database = pytest.mark.skipif(bool(_REASON), reason=_REASON or "available")

# A statement a user's search could have put in the database's log. The
# report may count and time it; it must never show it.
SEARCHED_FOR = "a-system-name-somebody-searched-for"

FAKE_DOCKER = r'''#!%(python)s
"""docker, as far as the report uses it. "exec" goes to PostgreSQL's own psql,
in the real test container or installed on this machine; the rest answer as
Docker does."""
import os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(" ".join(args[:2]) + "\n")
if os.environ.get("FAKE_DOCKER_DOWN"):
    sys.stderr.write("Cannot connect to the Docker daemon at unix:///var/run/docker.sock.\n")
    sys.exit(1)
if args[:1] == ["ps"]:
    print("%(name)s\tUp 3 days (healthy)")
    print("edfinder-api\t" + os.environ.get("FAKE_DOCKER_API_STATUS", "Up 2 hours (healthy)"))
elif args[:1] == ["stats"]:
    print("%(name)s\t1.50%%\t5.1GiB / 15.6GiB")
    print("edfinder-api\t0.20%%\t310MiB / 4GiB")
elif args[:1] == ["info"]:
    for line in (os.environ.get("FAKE_DOCKER_WARNINGS") or "").split("|"):
        if line:
            print(line)
elif args[:1] == ["logs"]:
    sys.stderr.write(os.environ.get("FAKE_DOCKER_DB_LOG") or "")
elif args[:1] == ["exec"]:
    container, program, rest = args[1], args[2], args[3:]
    if container != %(name)r or program != "psql":
        sys.stderr.write("Error response from daemon: No such container: %%s\n" %% container)
        sys.exit(1)
    real = os.environ.get("FAKE_DOCKER_REAL")
    if real:
        os.execv(real, ["docker", "exec", os.environ["FAKE_DOCKER_CONTAINER"], program, *rest])
    os.execv(os.path.join(os.environ["FAKE_PG_PROGRAMS"], program),
             [program, "-h", os.environ["FAKE_PG_HOST"], "-p", os.environ["FAKE_PG_PORT"], *rest])
else:
    sys.exit(64)
'''

FAKE_VCGENCMD = r'''#!%(python)s
import os, sys
print(os.environ.get("FAKE_THROTTLED", "throttled=0x0") if sys.argv[1:] == ["get_throttled"] else "unknown")
'''


class Site(BaseHTTPRequestHandler):
    """The site's two addresses the report asks, and what was asked of them."""
    asked: list = []
    find_status = 200

    def do_GET(self):  # noqa: N802
        type(self).asked.append(("GET", self.path, None))
        self._answer(200 if self.path == "/api/health" else 404, {"status": "ok"})

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        type(self).asked.append(("POST", self.path, json.loads(body)))
        self._answer(type(self).find_status if self.path == "/api/local/search" else 404, {"results": [1, 2, 3]})

    def _answer(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture()
def site():
    Site.asked, Site.find_status = [], 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "127.0.0.1:%d" % server.server_address[1]
    server.shutdown()
    server.server_close()


def a_pi(root: Path) -> None:
    """The files a Pi 5 with two NVMe drives has, as far as the report reads them."""
    files = {
        "proc/device-tree/model": "Raspberry Pi 5 Model B Rev 1.1\x00",
        "proc/meminfo": "MemTotal:       16608096 kB\nMemFree:          412345 kB\nMemAvailable:   11203456 kB\n"
                        "SwapTotal:       2097136 kB\nSwapFree:        2097136 kB\n",
        "proc/loadavg": "0.42 0.38 0.35 1/512 12345\n",
        "proc/uptime": "270000.12 1000000.00\n",
        "proc/cmdline": "console=serial0,115200 console=tty1 root=PARTUUID=0a1b2c3d-02 rootfstype=ext4 "
                        "cgroup_disable=memory rootwait\n",
        "proc/mounts": "/dev/nvme0n1p2 / ext4 rw,noatime 0 0\n"
                       "/dev/nvme0n1p1 /boot/firmware vfat rw,relatime 0 0\n"
                       "/dev/nvme1n1p1 /srv/data ext4 rw,relatime 0 0\n",
        "sys/fs/cgroup/cgroup.controllers": "cpuset cpu io pids\n",
        "sys/class/thermal/thermal_zone0/temp": "48350\n",
        "sys/devices/system/cpu/cpu0/cpufreq/scaling_governor": "ondemand\n",
        "sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq": "1500000\n",
        "sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq": "2400000\n",
        "sys/class/nvme/nvme0/model": "Example SSD 500GB\n",
        "sys/class/nvme/nvme0/device/current_link_speed": "5.0 GT/s PCIe\n",
        "sys/class/nvme/nvme0/device/max_link_speed": "8.0 GT/s PCIe\n",
        "sys/class/nvme/nvme0/device/current_link_width": "1\n",
        "sys/class/nvme/nvme1/model": "Example SSD 2TB\n",
        "sys/class/nvme/nvme1/device/current_link_speed": "5.0 GT/s PCIe\n",
        "sys/class/nvme/nvme1/device/max_link_speed": "5.0 GT/s PCIe\n",
        "sys/class/nvme/nvme1/device/current_link_width": "1\n",
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


class Kit:
    def __init__(self, tmp_path: Path, monkeypatch, bind: str, database=None) -> None:
        self.root = tmp_path / "root"
        self.root.mkdir()
        self.log = tmp_path / "docker.log"
        self.log.write_text("")
        programs = tmp_path / "bin"
        programs.mkdir()
        for name, text in (("docker", FAKE_DOCKER), ("vcgencmd", FAKE_VCGENCMD)):
            path = programs / name
            path.write_text(text % {"python": sys.executable, "name": CONTAINER_NAME})
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        values = {
            "PATH": str(programs) + os.pathsep + os.environ["PATH"],
            "FAKE_DOCKER_LOG": str(self.log),
            "EDFINDER_REPORT_ROOT": str(self.root),
            "EDFINDER_LIVE_BIND": bind,
            "EDFINDER_DB_CONTAINER": CONTAINER_NAME,
            "EDFINDER_BACKUP_DATA_DIR": "/srv/data",
            "EDFINDER_BACKUP_DIR": "/var/backups/edfinder",
        }
        if database is not None:
            settings = conninfo_to_dict(database.conninfo)
            values.update(FAKE_PG_PROGRAMS=PROGRAMS, FAKE_PG_HOST=settings.get("host", "localhost"),
                          FAKE_PG_PORT=str(settings.get("port", 5432)),
                          EDFINDER_DB_USER=settings["user"], EDFINDER_DB_NAME=settings["dbname"])
            real_docker = shutil.which("docker") if CONTAINER else None
            if real_docker:
                values.update(FAKE_DOCKER_REAL=real_docker, FAKE_DOCKER_CONTAINER=CONTAINER)
        else:
            values["FAKE_DOCKER_DOWN"] = "1"
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        for key in ("FAKE_THROTTLED", "FAKE_DOCKER_WARNINGS", "FAKE_DOCKER_DB_LOG", "FAKE_DOCKER_API_STATUS"):
            monkeypatch.delenv(key, raising=False)
        if database is not None:
            monkeypatch.delenv("FAKE_DOCKER_DOWN", raising=False)

    def run(self, capsys) -> tuple[int, str, dict[str, str], list[str]]:
        """The report: its exit code, its text, its rows by label, and what stood out."""
        capsys.readouterr()
        code = tool.main([])
        said = capsys.readouterr().out
        rows, notes = {}, []
        for line in said.splitlines():
            if line.startswith("  - "):
                notes.append(line[4:])
            else:
                row = re.match(r"^  (\S.*?)\s{2,}(\S.*)$", line)
                if row:
                    rows[row.group(1)] = row.group(2)
        return code, said, rows, notes


# -- the small readers ------------------------------------------------------------


def test_the_pis_throttled_number_is_put_in_words():
    assert tool.throttled_in_words("throttled=0x0") == (0, [], [])
    number, now, since = tool.throttled_in_words("throttled=0x50005")
    assert number == 0x50005
    assert now == ["the power supply is too weak", "the processor is slowed down"]
    assert since == ["the power supply has been too weak", "the processor has been slowed down"]
    assert tool.throttled_in_words("throttled=0x80000")[2] == ["it has been near its heat limit"]
    assert tool.throttled_in_words("VCHI initialization failed") == (None, [], [])


def test_a_folder_is_found_on_the_drive_that_holds_it():
    table = tool.mounts("/dev/nvme0n1p2 / ext4 rw,noatime 0 0\n"
                        "/dev/nvme1n1p1 /srv/data ext4 rw,relatime 0 0\n"
                        "overlay /srv/data/docker/overlay2/x/merged overlay rw 0 0\n")
    assert tool.mount_of("/srv/data", table)[0] == "/dev/nvme1n1p1"
    assert tool.mount_of("/srv/data/postgres", table)[0] == "/dev/nvme1n1p1"
    assert tool.mount_of("/srv/database", table)[0] == "/dev/nvme0n1p2", "a name that only starts the same"
    assert tool.mount_of("/var/backups/edfinder", table)[0] == "/dev/nvme0n1p2"
    assert tool.mount_of("/", table)[3] == ["rw", "noatime"]
    assert tool.mount_of("/srv/data", []) is None


def test_slow_statements_are_timed_and_never_kept():
    log = ("2026-11-13 21:00:01.123 UTC [77] LOG:  duration: 2345.678 ms  statement: SELECT * FROM x WHERE name = '%s'\n"
           "2026-11-13 21:00:09.000 UTC [78] LOG:  checkpoint complete: wrote 12 buffers\n"
           "2026-11-13 21:05:00.500 UTC [79] LOG:  duration: 15001.2 ms  execute <unnamed>: SELECT 1\n") % SEARCHED_FOR
    assert tool.durations(log) == [2345.678, 15001.2]
    assert tool.durations("") == []


# -- the whole report -------------------------------------------------------------


def test_on_a_machine_that_is_not_a_pi_it_says_what_it_cannot_read_and_carries_on(tmp_path, monkeypatch, capsys):
    """Nothing under /proc or /sys, Docker not answering, no site."""
    kit = Kit(tmp_path, monkeypatch, "127.0.0.1:9")
    monkeypatch.setenv("PATH", str(tmp_path / "bin-with-docker-only"))
    (tmp_path / "bin-with-docker-only").mkdir()
    shutil.copy(tmp_path / "bin" / "docker", tmp_path / "bin-with-docker-only" / "docker")
    code, said, rows, notes = kit.run(capsys)
    assert code == 0
    assert rows["Model"] == "not a Raspberry Pi, or it does not say"
    assert rows["Temperature"] == "not readable here"
    assert rows["Slowed down"] == "not readable here (vcgencmd is not installed here)"
    assert rows["Kept"] == "cannot tell here"
    assert rows["Docker"].startswith("cannot be asked (Cannot connect to the Docker daemon")
    assert "THE DATABASE" not in said, "with no Docker, the database is not asked"
    assert rows["Health check"] == "no answer at 127.0.0.1:9"
    assert "The site did not answer at 127.0.0.1:9 (Health check)." in notes


@needs_database
def test_on_a_pi_it_reports_the_machine_the_database_and_the_site(galaxy, tmp_path, monkeypatch, capsys, site):  # noqa: F811
    kit = Kit(tmp_path, monkeypatch, site, database=galaxy)
    a_pi(kit.root)
    monkeypatch.setenv("FAKE_DOCKER_WARNINGS", "WARNING: No memory limit support|WARNING: No swap limit support")
    monkeypatch.setenv("FAKE_DOCKER_DB_LOG", (
        "2026-11-13 21:00:01.123 UTC [77] LOG:  duration: 2345.678 ms  statement: SELECT '%s'\n"
        "2026-11-13 21:05:00.500 UTC [79] LOG:  duration: 15001.2 ms  execute <unnamed>: SELECT 1\n") % SEARCHED_FOR)
    code, said, rows, notes = kit.run(capsys)
    assert code == 0
    # The machine.
    assert rows["Model"] == "Raspberry Pi 5 Model B Rev 1.1"
    assert rows["Memory"] == "17.0 GB, of which 11.5 GB is free for use"
    assert rows["Swap"] == "2.1 GB, 0 bytes in use"
    assert rows["Busy"].startswith("0.42 over the last minute, 0.35 over 15 minutes")
    assert rows["Running for"] == "3 days 3 h"
    assert rows["Temperature"].startswith("48.4 C")
    assert rows["Slowed down"] == "no, and not since it was started"
    assert rows["Processor speed rule"] == "ondemand (now 1.5 GHz of 2.4)"
    # The drives: which is which, how mounted, and the link to each.
    assert rows["nvme0"] == ("Example SSD 500GB; link 5.0 GT/s PCIe x1 (the drive can do 8.0 GT/s PCIe); "
                             "holds /, /boot/firmware")
    assert rows["nvme1"] == "Example SSD 2TB; link 5.0 GT/s PCIe x1; holds /srv/data"
    assert rows["/srv/data (the database)"].startswith("nvme1n1p1, ext4, ")
    assert rows["/srv/data (the database)"].endswith("; relatime")
    assert rows["/var/backups/edfinder (the backups)"] == "on the same drive as /"
    # Memory limits: this system starts with memory accounting off, and says so.
    assert rows["Start-up settings"] == "cgroup_disable=memory", "only the words about this, not the whole line"
    assert rows["Kept"].startswith("NO")
    assert rows["Docker's own warnings"] == "WARNING: No memory limit support; WARNING: No swap limit support"
    # The containers.
    assert rows[CONTAINER_NAME] == "Up 3 days (healthy); processor 1.50%, memory 5.1GiB / 15.6GiB"
    # The database: really asked, on PostgreSQL 18.
    assert rows["PostgreSQL"].startswith("18.")
    assert rows["Size on disk"].endswith(" MB")
    for name in ("shared_buffers", "jit", "io_method", "io_workers", "effective_io_concurrency", "wal_level",
                 "max_wal_size", "random_page_cost"):
        assert rows[name], name
    assert rows["io_method"] in ("worker", "io_uring", "sync")
    assert rows["jit"] in ("on", "off")
    assert rows["Connections"].endswith(" at work")
    assert rows["Found in its own memory"].split("%")[0].replace(".", "").isdigit()
    assert " on the clock, " in rows["Checkpoints"]
    assert rows["Slow statements, 24 h"] == "2 (the longest: 15.0 s, 2.3 s)"
    # The site: each address asked three times, Find as the site's own page asks it.
    assert rows["Health check"].endswith("; answers 200; 16 bytes")
    assert rows["Find, 250 ly of Sol"].count(" s") == 3
    assert [asked[:2] for asked in Site.asked] == [("GET", "/api/health")] * 3 + [("POST", "/api/local/search")] * 3
    assert Site.asked[-1][2] == {"reference_coords": {"x": 0, "y": 0, "z": 0}, "size": 50, "from": 0,
                                 "filters": {"distance": {"max": 250}}}
    # What it must never show: a logged statement's text, or any more of the
    # start-up line than the words about memory limits.
    assert SEARCHED_FOR not in said and "statement:" not in said
    assert "PARTUUID" not in said and "0a1b2c3d" not in said
    assert any(note.startswith("Memory limits on the containers are not being kept") for note in notes)


@needs_database
def test_it_only_reads(galaxy, tmp_path, monkeypatch, capsys, site):  # noqa: F811
    """The database is asked nothing but questions, Docker only to list and
    to ask, and no file is written."""
    kit = Kit(tmp_path, monkeypatch, site, database=galaxy)
    a_pi(kit.root)
    before_files = sorted(str(path) for path in kit.root.rglob("*"))
    before_rows = galaxy.one("SELECT sum(n_tup_ins + n_tup_upd + n_tup_del) FROM pg_stat_user_tables")
    assert kit.run(capsys)[0] == 0
    assert sorted(str(path) for path in kit.root.rglob("*")) == before_files
    asked = set(kit.log.read_text().split("\n")) - {""}
    assert asked == {"ps --format", "stats --no-stream", "info --format", "logs --since",
                     "exec %s" % CONTAINER_NAME}
    galaxy.connection.execute("SELECT pg_stat_force_next_flush()")
    assert galaxy.one("SELECT sum(n_tup_ins + n_tup_upd + n_tup_del) FROM pg_stat_user_tables") == before_rows
    source = TOOL.read_text()
    for word in ("INSERT", "UPDATE ", "DELETE", "ALTER", "TRUNCATE", "DROP", "pg_reload_conf", "write_text",
                 "write_bytes", "os.remove", "unlink", "rmtree"):
        assert word not in source, word
    assert re.search(r"(?<![.\w])open\(", source) is None, "it opens no file of its own to write"


@needs_database
def test_what_is_wrong_is_said_at_the_end(galaxy, tmp_path, monkeypatch, capsys, site):  # noqa: F811
    kit = Kit(tmp_path, monkeypatch, site, database=galaxy)
    a_pi(kit.root)
    (kit.root / "sys/class/thermal/thermal_zone0/temp").write_text("78200\n")
    (kit.root / "sys/fs/cgroup/cgroup.controllers").write_text("cpuset cpu io memory pids\n")
    monkeypatch.setenv("FAKE_THROTTLED", "throttled=0x50000")
    monkeypatch.setenv("FAKE_DOCKER_API_STATUS", "Up 5 minutes (unhealthy)")
    Site.find_status = 500
    code, said, rows, notes = kit.run(capsys)
    assert code == 0, "a report of trouble is still a report"
    assert rows["Kept"] == "yes"
    assert not any("Memory limits" in note for note in notes)
    assert "The processor is at 78 C, close to where it slows itself down (80 C)." in notes
    assert "The Pi reports: the power supply has been too weak; the processor has been slowed down." in notes
    assert "The container edfinder-api says: Up 5 minutes (unhealthy)." in notes
    assert "Find, 250 ly of Sol did not answer 200 every time (500)." in notes


def test_a_setting_that_is_not_a_plain_name_or_this_machines_address_is_refused(tmp_path, monkeypatch, capsys):
    kit = Kit(tmp_path, monkeypatch, "example.org:80")
    monkeypatch.setenv("EDFINDER_DB_CONTAINER", "db; rm -rf /")
    code, said, rows, notes = kit.run(capsys)
    assert code == 0
    assert any("EDFINDER_DB_CONTAINER" in note and "not a plain name" in note for note in notes)
    assert any("EDFINDER_LIVE_BIND" in note and "only asks this machine" in note for note in notes)
    assert "THE SITE" not in said and "THE DATABASE" not in said
    assert "exec" not in kit.log.read_text()


def test_it_takes_no_arguments(capsys):
    assert tool.main(["--help"]) == 0 and "Changes nothing" in capsys.readouterr().out
    assert tool.main(["live"]) == 2 and "takes no arguments" in capsys.readouterr().out
