"""Backups (deploy/backup.py), made from a real database and brought back.

The database is the scored test database: the twelve real fixture systems as
production held them in August, with their scores and Find rows. A backup is
only trusted here once it has been restored and the rows counted.

The tool runs PostgreSQL's own programs inside the database's container
(``docker exec``). A stand-in ``docker`` sends those to the real PostgreSQL 18
container the other database tests use; where there is no Docker, to the same
programs installed on this machine. Either way the real pg_dump and
pg_restore do the work.

Needs RATINGS_V4_VALIDATION_DATABASE_URL, and Docker or PostgreSQL's own
programs of the database's version. Skipped without them, unless
REQUIRE_DOCKER_TESTS=1 is set (CI sets it).
"""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from collections import namedtuple
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

from tests.test_galaxy_scores import galaxy, template  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "deploy" / "backup.py"
DSN = os.environ.get("RATINGS_V4_VALIDATION_DATABASE_URL", "")
REQUIRED = os.environ.get("REQUIRE_DOCKER_TESTS") == "1"
CONTAINER_NAME = "backup-test-db"

_spec = importlib.util.spec_from_file_location("edfinder_backup", TOOL)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


def _container() -> str:
    """The Docker container the test database runs in, found by its port."""
    if not DSN or shutil.which("docker") is None:
        return ""
    port = urlsplit(DSN).port or 5432
    found = subprocess.run(["docker", "ps", "--filter", "publish=%d" % port, "--format", "{{.Names}}"],
                           capture_output=True, text=True)
    names = found.stdout.split()
    return names[0] if found.returncode == 0 and len(names) == 1 else ""


def _own_programs() -> str:
    """A folder on this machine with pg_dump of the database's version or newer."""
    if not DSN:
        return ""
    try:
        with psycopg.connect(DSN, connect_timeout=5) as connection:
            server = int(connection.execute("SHOW server_version_num").fetchone()[0]) // 10000
    except psycopg.Error:
        return ""
    folders = [str(Path(found).parent) for found in [shutil.which("pg_dump")] if found]
    folders += sorted((str(path) for path in Path("/usr/lib/postgresql").glob("*/bin")), reverse=True)
    for folder in folders:
        try:
            said = subprocess.run([os.path.join(folder, "pg_dump"), "--version"], capture_output=True, text=True)
            if said.returncode == 0 and int(said.stdout.split()[-1].split(".")[0]) >= server:
                return folder
        except (OSError, ValueError, IndexError):
            continue
    return ""


CONTAINER = _container()
PROGRAMS = "" if CONTAINER else _own_programs()
_REASON = ("" if CONTAINER or PROGRAMS else
           "no PostgreSQL programs for the test database: needs RATINGS_V4_VALIDATION_DATABASE_URL, and "
           "Docker or pg_dump of the database's version")
if _REASON and REQUIRED:
    raise RuntimeError("Docker tests are required but cannot run: " + _REASON)
pytestmark = pytest.mark.skipif(bool(_REASON), reason=_REASON or "available")

FAKE_DOCKER = r'''#!%(python)s
"""docker exec: PostgreSQL's own program, in the real test container or, where
there is no Docker, installed on this machine. FAKE_DOCKER_FAIL names a
program that writes half a file and then fails, as a lost connection would."""
import os, sys
args = sys.argv[1:]
if args[:1] != ["exec"]:
    sys.exit(64)
args = args[1:]
flags = []
while args and args[0].startswith("-"):
    flags.append(args.pop(0))
container, program, rest = args[0], args[1], args[2:]
if container != %(name)r:
    sys.stderr.write("Error response from daemon: No such container: %%s\n" %% container)
    sys.exit(1)
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(program + "\n")
if os.environ.get("FAKE_DOCKER_FAIL") == program:
    sys.stdout.write("half a file")
    sys.stdout.flush()
    sys.stderr.write("%%s: error: connection to server was lost\n" %% program)
    sys.exit(1)
real = os.environ.get("FAKE_DOCKER_REAL")
if real:
    os.execv(real, ["docker", "exec", *flags, os.environ["FAKE_DOCKER_CONTAINER"], program, *rest])
os.execv(os.path.join(os.environ["FAKE_PG_PROGRAMS"], program),
         [program, "-h", os.environ["FAKE_PG_HOST"], "-p", os.environ["FAKE_PG_PORT"], *rest])
'''

FAKE_SYSTEMCTL = r'''#!%(python)s
"""systemctl is-active: the units named in FAKE_SYSTEMCTL_RUNNING are running."""
import os, sys
running = set((os.environ.get("FAKE_SYSTEMCTL_RUNNING") or "").split())
units = sys.argv[2:]
for unit in units:
    print("activating" if unit in running else "inactive")
sys.exit(0 if units and all(unit in running for unit in units) else 3)
'''


class Kit:
    """One test's backup folder, settings files and stand-in programs."""

    def __init__(self, galaxy, tmp_path: Path, monkeypatch) -> None:  # noqa: F811
        self.galaxy = galaxy
        self.monkeypatch = monkeypatch
        self.settings = conninfo_to_dict(galaxy.conninfo)
        self.directory = tmp_path / "backups"
        self.log = tmp_path / "docker.log"
        self.log.write_text("")
        # This machine's settings, as the Pi keeps them: a folder and a file.
        self.secret = "owner-token-" + uuid4().hex
        etc = tmp_path / "etc-edfinder"
        etc.mkdir()
        (etc / "api.env").write_text("OWNER_TOKEN=%s\n" % self.secret)
        (etc / "postgres-password").write_text(uuid4().hex + "\n")
        (tmp_path / "deploy.conf").write_text("EDFINDER_NETWORK=edfinder\n")
        self.settings_paths = [etc, tmp_path / "deploy.conf"]
        programs = tmp_path / "bin"
        programs.mkdir()
        for name, text in (("docker", FAKE_DOCKER), ("systemctl", FAKE_SYSTEMCTL)):
            path = programs / name
            path.write_text(text % {"python": sys.executable, "name": CONTAINER_NAME})
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        real_docker = shutil.which("docker") if CONTAINER else None
        values = {
            "PATH": str(programs) + os.pathsep + os.environ["PATH"],
            "FAKE_DOCKER_LOG": str(self.log),
            "FAKE_PG_PROGRAMS": PROGRAMS,
            "FAKE_PG_HOST": self.settings.get("host", "localhost"),
            "FAKE_PG_PORT": str(self.settings.get("port", 5432)),
            "EDFINDER_BACKUP_DIR": str(self.directory),
            # Not checked unless a test says where the database's drive is.
            "EDFINDER_BACKUP_DATA_DIR": "",
            "EDFINDER_BACKUP_SETTINGS": " ".join(str(path) for path in self.settings_paths),
            "EDFINDER_BACKUP_SPARE_GB": "0",
            "EDFINDER_DB_CONTAINER": CONTAINER_NAME,
            "EDFINDER_DB_USER": self.settings["user"],
            "EDFINDER_DB_NAME": self.settings["dbname"],
        }
        if real_docker:
            values.update(FAKE_DOCKER_REAL=real_docker, FAKE_DOCKER_CONTAINER=CONTAINER)
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        for key in ("FAKE_DOCKER_FAIL", "FAKE_SYSTEMCTL_RUNNING"):
            monkeypatch.delenv(key, raising=False)
        self._stamps = iter(())

    def stamps(self, *texts: str) -> None:
        """The names the next backups get, instead of the clock's."""
        self._stamps = iter(texts)
        self.monkeypatch.setattr(tool, "stamp", lambda: next(self._stamps))

    def run(self, command: str, capsys) -> tuple[int, str]:
        capsys.readouterr()
        code = tool.main([command])
        return code, capsys.readouterr().out

    def names(self, kind: str) -> list[str]:
        folder = self.directory / kind
        return sorted(item.name for item in folder.iterdir()) if folder.is_dir() else []

    def program(self, name: str, *args: str, stdin=None) -> subprocess.CompletedProcess:
        """One of PostgreSQL's programs, by the same road the tool takes."""
        return subprocess.run(["docker", "exec", *(["-i"] if stdin is not None else []), CONTAINER_NAME, name, *args],
                              stdin=stdin if stdin is not None else subprocess.DEVNULL, capture_output=True)


@pytest.fixture
def kit(galaxy, tmp_path, monkeypatch):  # noqa: F811
    return Kit(galaxy, tmp_path, monkeypatch)


def row_counts(conninfo: str) -> dict[str, int]:
    """Every table of a database with its number of rows."""
    with psycopg.connect(conninfo, autocommit=True) as connection:
        tables = connection.execute(
            "SELECT schemaname, tablename FROM pg_tables "
            "WHERE schemaname NOT IN ('pg_catalog', 'information_schema') ORDER BY 1, 2").fetchall()
        return {"%s.%s" % (schema, table): connection.execute(
            sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table))).fetchone()[0]
            for schema, table in tables}


# -- the full backup ------------------------------------------------------------


def test_a_full_backup_brings_every_row_back(kit, capsys):
    code, said = kit.run("full", capsys)
    assert code == 0, said
    names = kit.names("full")
    dumps = [name for name in names if name.endswith(".dump")]
    assert len(dumps) == 1 and tool.FULL_NAME.match(dumps[0])
    assert names == [dumps[0], dumps[0] + ".json"], "nothing unfinished is left"
    backup = kit.directory / "full" / dumps[0]
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    about = json.loads((kit.directory / "full" / (dumps[0] + ".json")).read_text())
    assert about["kind"] == "full" and about["bytes"] == backup.stat().st_size and about["tables"] == 106
    assert "DONE:" in said and "106 tables" in said

    # Bring it back into an empty database, and count.
    live = row_counts(kit.galaxy.conninfo)
    assert len(live) == 106
    # Among them every body of the twelve real systems.
    bodies = sum(len(record["bodies"]) for record in kit.galaxy.records.values())
    assert live[kit.galaxy.schema + ".bodies"] == bodies == 389
    scratch = "backup_restore_" + uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(scratch)))
        try:
            with open(backup, "rb") as handle:
                done = kit.program("pg_restore", "-U", kit.settings["user"], "-d", scratch,
                                   "--no-owner", "--no-acl", "--exit-on-error", stdin=handle)
            assert done.returncode == 0, done.stderr.decode()[-500:]
            conninfo = make_conninfo(DSN, dbname=scratch)
            assert row_counts(conninfo) == live
            # The rules that guard the score tables came back with them.
            with psycopg.connect(conninfo, autocommit=True) as restored:
                with pytest.raises(psycopg.errors.RaiseException, match="governed purge"):
                    restored.execute("DELETE FROM v3_derived.system_search")
                # The count can tell a missing row: take one away and it differs.
                restored.execute("DELETE FROM v3_identity.role WHERE ctid = (SELECT min(ctid) FROM v3_identity.role)")
            assert row_counts(conninfo) != live
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(scratch)))


def test_a_full_backup_will_not_run_beside_a_nightly_job(kit, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_SYSTEMCTL_RUNNING", "edfinder-state-refresh.service")
    code, said = kit.run("full", capsys)
    assert code == 1
    assert "FAILED: edfinder-state-refresh.service is running" in said
    assert kit.names("full") == [] and "pg_dump" not in kit.log.read_text()
    # The small backup takes a minute and holds nothing up: it runs.
    code, said = kit.run("small", capsys)
    assert code == 0, said


def test_a_full_backup_makes_room_only_when_that_is_enough(kit, capsys, monkeypatch):
    monkeypatch.setenv("EDFINDER_BACKUP_KEEP_FULL", "3")
    kit.stamps("20261101T070000Z", "20261108T070000Z", "20261115T070000Z", "20261122T070000Z")
    assert kit.run("full", capsys)[0] == 0 and kit.run("full", capsys)[0] == 0
    first, second = (name for name in kit.names("full") if name.endswith(".dump"))
    folder = kit.directory / "full"
    size = (folder / second).stat().st_size
    needed = size * tool.NEXT_FULL_GROWTH
    Usage = namedtuple("Usage", "total used free")

    def drive(free_when_empty: float):
        def usage(_path):
            used = sum(item.stat().st_size for item in folder.iterdir() if item.is_file())
            return Usage(0, used, free_when_empty - used)
        return usage

    # Too little room even with the oldest gone: it stops, and removes nothing.
    monkeypatch.setattr(tool.shutil, "disk_usage", drive(size * 2.1))
    code, said = kit.run("full", capsys)
    assert code == 1 and "not enough room" in said and "Nothing was removed" in said
    assert [name for name in kit.names("full") if name.endswith(".dump")] == [first, second]
    # Room once the oldest has gone: the oldest goes, the newest stays, and the new one is made.
    monkeypatch.setattr(tool.shutil, "disk_usage", drive(size * 2 + needed * 0.9))
    code, said = kit.run("full", capsys)
    assert code == 0, said
    assert "Making room: removing the oldest full backup, " + first in said
    kept = [name for name in kit.names("full") if name.endswith(".dump")]
    assert kept == [second, "edfinder-full-20261115T070000Z.dump"]
    assert not (folder / (first + ".json")).exists()


# -- the small backup -----------------------------------------------------------


def test_a_small_backup_holds_what_users_have_written_and_the_settings(kit, capsys):
    code, said = kit.run("small", capsys)
    assert code == 0, said
    (name,) = kit.names("small")
    assert tool.SMALL_NAME.match(name)
    folder = kit.directory / "small" / name
    assert sorted(item.name for item in folder.iterdir()) == ["about.json", "settings.tar.gz", "users.dump"]
    about = json.loads((folder / "about.json").read_text())
    # The 26 tables of the two schemas, as on the real machine.
    assert about["tables"] == 26 and about["schemas"] == ["v3_identity", "v3_private"]
    assert "26 tables" in said

    # The settings come back byte for byte, in a file only its owner can read.
    assert stat.S_IMODE((folder / "settings.tar.gz").stat().st_mode) == 0o600
    assert stat.S_IMODE((folder / "users.dump").stat().st_mode) == 0o600
    with tarfile.open(folder / "settings.tar.gz") as archive:
        held = {member.name: archive.extractfile(member).read() for member in archive.getmembers() if member.isfile()}
    wanted = {}
    for path in kit.settings_paths:
        for item in ([path] if path.is_file() else sorted(path.iterdir())):
            wanted[str(item).lstrip("/")] = item.read_bytes()
    assert held == wanted and len(held) == 3
    # ...and nothing in them is ever shown.
    assert kit.secret not in said and kit.secret.encode() in held[str(kit.settings_paths[0] / "api.env").lstrip("/")]

    # The rows come back. The roles are the four real rows the migrations put there.
    live = kit.galaxy.connection
    assert live.execute("SELECT count(*) FROM v3_identity.role").fetchone() == (4,)
    roles = live.execute("SELECT * FROM v3_identity.role ORDER BY 1").fetchall()
    live.execute("DELETE FROM v3_identity.role")
    assert live.execute("SELECT count(*) FROM v3_identity.role").fetchone() == (0,)
    with open(folder / "users.dump", "rb") as handle:
        done = kit.program("pg_restore", "-U", kit.settings["user"], "-d", kit.settings["dbname"],
                           "--data-only", "--exit-on-error", stdin=handle)
    assert done.returncode == 0, done.stderr.decode()[-500:]
    assert live.execute("SELECT * FROM v3_identity.role ORDER BY 1").fetchall() == roles


def test_a_small_backup_fails_when_a_settings_file_or_a_schema_is_missing(kit, capsys, monkeypatch):
    monkeypatch.setenv("EDFINDER_BACKUP_SETTINGS", "%s /nowhere/api.env" % kit.settings_paths[0])
    code, said = kit.run("small", capsys)
    assert code == 1 and "names what is not there: /nowhere/api.env" in said
    assert kit.names("small") == []
    monkeypatch.setenv("EDFINDER_BACKUP_SETTINGS", str(kit.settings_paths[0]))
    monkeypatch.setenv("EDFINDER_BACKUP_SCHEMAS", "v3_identity v3_gone")
    code, said = kit.run("small", capsys)
    assert code == 1 and "pg_dump stopped with an error" in said and "v3_gone" in said
    assert kit.names("small") == []


# -- what is kept, and what is left alone ------------------------------------------


def test_only_the_newest_are_kept_and_nothing_else_is_touched(kit, capsys, monkeypatch):
    monkeypatch.setenv("EDFINDER_BACKUP_KEEP_SMALL", "2")
    monkeypatch.setenv("EDFINDER_BACKUP_KEEP_FULL", "1")
    kit.directory.mkdir()
    # What the owner has put in the same folder is not this tool's to remove.
    (kit.directory / "from-server").mkdir()
    (kit.directory / "from-server" / "users.dump").write_text("kept")
    for kind in ("small", "full"):
        (kit.directory / kind).mkdir()
        (kit.directory / kind / "edfinder-notes.txt").write_text("kept")
        (kit.directory / kind / "edfinder-small-2026.dump").write_text("kept: not one of this tool's names")
    kit.stamps("20261110T023000Z", "20261111T023000Z", "20261112T023000Z", "20261108T070000Z", "20261115T070000Z")
    for _ in range(3):
        assert kit.run("small", capsys)[0] == 0
    for _ in range(2):
        code, said = kit.run("full", capsys)
        assert code == 0, said
    assert "Removing the oldest: edfinder-full-20261108T070000Z.dump" in said
    assert kit.names("small") == sorted(["edfinder-notes.txt", "edfinder-small-20261111T023000Z",
                                         "edfinder-small-20261112T023000Z", "edfinder-small-2026.dump"])
    assert kit.names("full") == sorted(["edfinder-full-20261115T070000Z.dump",
                                        "edfinder-full-20261115T070000Z.dump.json",
                                        "edfinder-notes.txt", "edfinder-small-2026.dump"])
    assert (kit.directory / "from-server" / "users.dump").read_text() == "kept"


def test_a_dump_that_fails_leaves_no_backup_and_keeps_the_old_ones(kit, capsys, monkeypatch):
    kit.stamps("20261110T023000Z", "20261108T070000Z", "20261111T023000Z", "20261115T070000Z",
               "20261112T023000Z", "20261122T070000Z")
    assert kit.run("small", capsys)[0] == 0 and kit.run("full", capsys)[0] == 0
    before = {kind: kit.names(kind) for kind in ("small", "full")}
    monkeypatch.setenv("FAKE_DOCKER_FAIL", "pg_dump")
    for kind in ("small", "full"):
        code, said = kit.run(kind, capsys)
        assert code == 1 and "FAILED: pg_dump stopped with an error" in said
        assert "connection to server was lost" in said
    # No half-written file under any name, and the earlier backups untouched.
    assert {kind: kit.names(kind) for kind in ("small", "full")} == before
    # A backup that does not read back is no backup either.
    monkeypatch.setenv("FAKE_DOCKER_FAIL", "pg_restore")
    for kind in ("small", "full"):
        code, said = kit.run(kind, capsys)
        assert code == 1 and "does not read back" in said
    assert {kind: kit.names(kind) for kind in ("small", "full")} == before


def test_an_unfinished_backup_from_before_is_cleared_away(kit, capsys):
    for kind in ("small", "full"):
        (kit.directory / kind).mkdir(parents=True)
    half_small = kit.directory / "small" / "edfinder-small-20261101T023000Z.partial"
    half_small.mkdir()
    (half_small / "users.dump").write_text("half")
    half_full = kit.directory / "full" / "edfinder-full-20261101T070000Z.dump.partial"
    half_full.write_text("half")
    code, said = kit.run("status", capsys)
    assert code == 0 and "none yet" in said, "an unfinished one is not counted as a backup"
    assert kit.run("small", capsys)[0] == 0 and kit.run("full", capsys)[0] == 0
    assert not half_small.exists() and not half_full.exists()
    assert len(kit.names("small")) == 1 and len(kit.names("full")) == 2


def test_two_backups_cannot_run_at_once(kit, capsys):
    kit.directory.mkdir()
    with open(kit.directory / ".lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for kind in ("small", "full"):
            code, said = kit.run(kind, capsys)
            assert code == 1 and "another backup is already running" in said
    assert kit.names("small") == [] and kit.names("full") == []
    assert kit.run("small", capsys)[0] == 0


# -- where backups may go ---------------------------------------------------------


def test_backups_are_refused_on_the_databases_own_drive(kit, capsys, monkeypatch, tmp_path):
    database_drive = tmp_path / "srv-data"
    database_drive.mkdir()
    monkeypatch.setenv("EDFINDER_BACKUP_DATA_DIR", str(database_drive))
    for command in ("small", "full", "check"):
        code, said = kit.run(command, capsys)
        assert code == 1 and "is on the same drive as the database" in said, command
    assert not kit.directory.exists(), "nothing was written"
    # A folder that is not there cannot be compared, so that is refused too.
    monkeypatch.setenv("EDFINDER_BACKUP_DATA_DIR", str(tmp_path / "not-there"))
    code, said = kit.run("small", capsys)
    assert code == 1 and "is not there" in said
    # Another drive is fine. /dev/shm is one on Linux.
    other = Path("/dev/shm")
    if other.is_dir() and os.stat(other).st_dev != os.stat(tmp_path).st_dev:
        monkeypatch.setenv("EDFINDER_BACKUP_DATA_DIR", str(other))
        code, said = kit.run("check", capsys)
        assert code == 0 and "That is a different drive from the database's" in said and "READY." in said
        assert kit.run("small", capsys)[0] == 0


# -- looking ----------------------------------------------------------------------


def test_check_and_status_change_nothing_and_say_what_is_there(kit, capsys):
    code, said = kit.run("check", capsys)
    assert code == 0, said
    assert "The small backup: 26 tables (v3_identity, v3_private)." in said and "READY." in said
    assert "Not checked: whether that is the database's own drive" in said
    code, said = kit.run("status", capsys)
    assert code == 0 and said.count("none yet") == 2
    assert not kit.directory.exists(), "looking makes nothing"
    assert kit.log.read_text().count("pg_dump") == 0

    kit.stamps("20200101T023000Z", "20200105T070000Z")
    assert kit.run("small", capsys)[0] == 0 and kit.run("full", capsys)[0] == 0
    code, said = kit.run("status", capsys)
    assert code == 0
    assert "Newest edfinder-small-20200101T023000Z" in said and "Newest edfinder-full-20200105T070000Z.dump" in said
    assert said.count("That is older than it should be") == 2
    assert kit.secret not in said


def test_settings_that_make_no_sense_are_refused(kit, capsys, monkeypatch):
    for key, value, words in (
            ("EDFINDER_DB_CONTAINER", "db; rm -rf /", "is not a plain name"),
            ("EDFINDER_BACKUP_SCHEMAS", "v3_identity;drop", "is not a plain name"),
            ("EDFINDER_BACKUP_DIR", "backups", "is not a full path"),
            ("EDFINDER_BACKUP_KEEP_FULL", "0", "must be 1 or more"),
            ("EDFINDER_BACKUP_COMPRESS", "zstd:3 --file=/etc/passwd", "is not a way pg_dump squeezes a file"),
            ("EDFINDER_BACKUP_NOT_DURING", "a.service;b", "is not a unit's name")):
        with monkeypatch.context() as patch:
            patch.setenv(key, value)
            code, said = kit.run("small", capsys)
            assert code == 1 and words in said, key
    assert kit.log.read_text() == "" and not kit.directory.exists()


# -- the timers -------------------------------------------------------------------


def unit(name: str) -> dict[str, list[str]]:
    lines = [line for line in (ROOT / "deploy" / name).read_text().splitlines()
             if line and not line.startswith(("#", "["))]
    found: dict[str, list[str]] = {}
    for line in lines:
        key, _, value = line.partition("=")
        found.setdefault(key, []).append(value)
    return found


def test_the_timers_run_each_backup_at_a_time_no_nightly_job_starts():
    assert unit("edfinder-backup-small.service")["ExecStart"] == ["/usr/bin/python3 /opt/edfinder/backup.py small"]
    assert unit("edfinder-backup-full.service")["ExecStart"] == ["/usr/bin/python3 /opt/edfinder/backup.py full"]
    assert unit("edfinder-backup-small.timer")["OnCalendar"] == ["*-*-* 02:30:00 UTC"]
    assert unit("edfinder-backup-full.timer")["OnCalendar"] == ["Sun *-*-* 07:00:00 UTC"]
    # The nightly jobs start at 03:30, and at 04:30 and 16:30: the full backup
    # has from 07:00 to 16:30 to itself.
    assert unit("edfinder-state-refresh.timer")["OnCalendar"] == ["*-*-* 03:30:00 UTC"]
    assert unit("edfinder-galaxy-update.timer")["OnCalendar"] == ["*-*-* 04,16:30:00 UTC"]
    # And each side waits for the other if they ever meet.
    nightly = ("edfinder-state-refresh.service", "edfinder-galaxy-update.service")
    assert set(nightly) <= set(unit("edfinder-backup-full.service")["After"][0].split())
    for name in nightly:
        assert "edfinder-backup-full.service" in unit(name)["After"][0].split()
    assert tool.DEFAULTS["EDFINDER_BACKUP_NOT_DURING"].split() == sorted(nightly)
    # Both report in when they end, and a report can never fail the backup.
    assert unit("edfinder-backup-small.service")["ExecStopPost"] == ["-/usr/bin/python3 /opt/edfinder/report_in.py backup-small"]
    assert unit("edfinder-backup-full.service")["ExecStopPost"] == ["-/usr/bin/python3 /opt/edfinder/report_in.py backup-full"]
