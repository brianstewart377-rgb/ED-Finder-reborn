#!/usr/bin/env python3
"""Back up the ED-Finder database on the machine that runs the site.

    sudo python3 backup.py check    say what a backup would do, and whether the
                                    machine is ready. Changes nothing.
    sudo python3 backup.py small    back up what cannot be made again
    sudo python3 backup.py full     back up the whole database
    sudo python3 backup.py status   list the backups that are there

Two kinds of backup, because the database is two kinds of thing.

  small   What cannot be made again: everything users have written (their
          accounts, watchlists and journal imports) and this machine's own
          settings files. A few megabytes and a minute. Every night.

  full    The whole database in one file. The galaxy in it could be built
          again from Spansh's public files, but that takes days; this file
          brings it back in hours. Tens of gigabytes and a few hours. Once a
          week.

Where they go: EDFINDER_BACKUP_DIR, which must be on a different drive from
the database. A backup on the database's own drive is lost with it, so the
tool refuses that. Each backup is written under a name ending in ".partial"
and given its real name only when it is complete and has been read back, so a
backup that is there is a whole one.

How many are kept: the newest 14 small ones and the newest 2 full ones. Older
ones are removed only after a new one is safely there. If the drive is too
full for a new full backup, the oldest full ones go first, but never the last
one. Only files this tool made, by their names, are ever removed.

A full backup reads every table for hours, and while it does, no job can
have a table to itself. The nightly state refresh needs that for a moment
(it empties one table and fills it again), and would wait behind the backup
with everything that reads that table waiting behind it. So a full backup
will not start while a nightly job is running, and its timer is set for a
time when none starts for hours. Start one by hand through systemd, so that
the nightly jobs wait their turn:

    sudo systemctl start edfinder-backup-full.service

To bring a backup back, see the README ("Backups").

Settings come from the environment, then from a "deploy.conf" file beside
this script (KEY=VALUE lines, shared with the other tools), then from the
defaults below. No setting holds a password, and this tool never shows the
contents of a settings file: it copies them into the backup unread.

Standard library only; needs Python 3.9 or newer.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent

DEFAULTS: Dict[str, str] = {
    # Where backups are kept. On the Pi, a folder on the drive it starts
    # from, which is not the drive the database is on.
    "EDFINDER_BACKUP_DIR": "/var/backups/edfinder",
    # The folder the database's drive is mounted at. Backups may not go on
    # that drive. Empty: do not check (a machine with one drive only).
    "EDFINDER_BACKUP_DATA_DIR": "/srv/data",
    # How many of each kind to keep.
    "EDFINDER_BACKUP_KEEP_SMALL": "14",
    "EDFINDER_BACKUP_KEEP_FULL": "2",
    # How pg_dump squeezes the file.
    "EDFINDER_BACKUP_COMPRESS": "zstd:3",
    # The small backup: the parts of the database that hold what users have
    # written, and the settings files and folders of this machine.
    "EDFINDER_BACKUP_SCHEMAS": "v3_identity v3_private",
    "EDFINDER_BACKUP_SETTINGS": "/etc/edfinder /opt/edfinder/deploy.conf",
    # Room that must still be free on the backup drive after a full backup.
    "EDFINDER_BACKUP_SPARE_GB": "20",
    # The nightly jobs a full backup must not run beside (systemd units).
    "EDFINDER_BACKUP_NOT_DURING": "edfinder-galaxy-update.service edfinder-state-refresh.service",
    # The database: its container, and the user and database name inside it
    # (the same settings the galaxy update uses). No password belongs here.
    "EDFINDER_DB_CONTAINER": "edfinder-v3-phase4c-full-20260827_r5-postgres",
    "EDFINDER_DB_USER": "edfinder_v3",
    "EDFINDER_DB_NAME": "edfinder_v3_phase4c_full_20260827_r5",
}

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
COMPRESS = re.compile(r"^(none|gzip|lz4|zstd)(:[0-9]{1,2})?$")
STAMP = "%Y%m%dT%H%M%SZ"
# The names this tool gives its backups. Nothing else is ever removed.
SMALL_NAME = re.compile(r"^edfinder-small-[0-9]{8}T[0-9]{6}Z$")
FULL_NAME = re.compile(r"^edfinder-full-[0-9]{8}T[0-9]{6}Z\.dump$")
PARTIAL = ".partial"
# With no earlier full backup to go by, how big one is taken to be: this
# share of the database's size on disk. A dump leaves the indexes out and is
# squeezed, so the real figure is well under it.
FIRST_FULL_SHARE = 0.4
# With an earlier one to go by: its size, and this much more.
NEXT_FULL_GROWTH = 1.25

TABLES_SQL = "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
SIZE_SQL = "SELECT pg_database_size(current_database())"


class BackupError(Exception):
    """Something went wrong; the message says what, in plain words."""


def say(message: str) -> None:
    print(message, flush=True)


def read_conf(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def size_text(count: float) -> str:
    for unit, step in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if count >= step:
            return "%.1f %s" % (count / step, unit)
    return "%d bytes" % count


def took(seconds: float) -> str:
    if seconds >= 3600:
        return "%d h %02d min" % (seconds // 3600, seconds % 3600 // 60)
    if seconds >= 60:
        return "%d min %02d s" % (seconds // 60, seconds % 60)
    return "%d s" % seconds


class Settings:
    def __init__(self, environ: Optional[Dict[str, str]] = None,
                 conf: Optional[Dict[str, str]] = None) -> None:
        environ = dict(os.environ if environ is None else environ)
        conf = read_conf(HERE / "deploy.conf") if conf is None else conf
        values = {key: environ.get(key, conf.get(key, default)) for key, default in DEFAULTS.items()}
        self.directory = Path(values["EDFINDER_BACKUP_DIR"])
        if not self.directory.is_absolute():
            raise BackupError("EDFINDER_BACKUP_DIR: %r is not a full path" % values["EDFINDER_BACKUP_DIR"])
        data = values["EDFINDER_BACKUP_DATA_DIR"].strip()
        self.data_directory: Optional[Path] = Path(data) if data else None
        try:
            self.keep_small = int(values["EDFINDER_BACKUP_KEEP_SMALL"])
            self.keep_full = int(values["EDFINDER_BACKUP_KEEP_FULL"])
            self.spare = float(values["EDFINDER_BACKUP_SPARE_GB"]) * 1e9
        except ValueError:
            raise BackupError("EDFINDER_BACKUP_KEEP_SMALL, _KEEP_FULL and _SPARE_GB must be numbers") from None
        if self.keep_small < 1 or self.keep_full < 1 or self.spare < 0:
            raise BackupError("EDFINDER_BACKUP_KEEP_SMALL and _KEEP_FULL must be 1 or more, and _SPARE_GB not below 0")
        self.compress = values["EDFINDER_BACKUP_COMPRESS"].strip()
        if not COMPRESS.match(self.compress):
            raise BackupError("EDFINDER_BACKUP_COMPRESS: %r is not a way pg_dump squeezes a file "
                              "(for example zstd:3 or gzip:6)" % self.compress)
        self.schemas = values["EDFINDER_BACKUP_SCHEMAS"].split()
        self.settings_paths = [Path(item) for item in values["EDFINDER_BACKUP_SETTINGS"].split()]
        self.db_container = values["EDFINDER_DB_CONTAINER"]
        self.db_user = values["EDFINDER_DB_USER"]
        self.db_name = values["EDFINDER_DB_NAME"]
        for key, value in (("EDFINDER_DB_CONTAINER", self.db_container), ("EDFINDER_DB_USER", self.db_user),
                           ("EDFINDER_DB_NAME", self.db_name),
                           *(("EDFINDER_BACKUP_SCHEMAS", schema) for schema in self.schemas)):
            if not NAME.match(value):
                raise BackupError("%s: %r is not a plain name" % (key, value))
        if not self.schemas:
            raise BackupError("EDFINDER_BACKUP_SCHEMAS names nothing to back up")
        for path in self.settings_paths:
            if not path.is_absolute():
                raise BackupError("EDFINDER_BACKUP_SETTINGS: %r is not a full path" % str(path))
        self.not_during = values["EDFINDER_BACKUP_NOT_DURING"].split()
        for unit in self.not_during:
            if not NAME.match(unit):
                raise BackupError("EDFINDER_BACKUP_NOT_DURING: %r is not a unit's name" % unit)

    @property
    def small_directory(self) -> Path:
        return self.directory / "small"

    @property
    def full_directory(self) -> Path:
        return self.directory / "full"

    def tool(self, name: str, *args: str, stdin: bool = False) -> List[str]:
        """A PostgreSQL tool run inside the database's container, as the
        database's own local user."""
        return ["docker", "exec", *(["-i"] if stdin else []), self.db_container, name, *args]


# -- the database ---------------------------------------------------------------


def query(settings: Settings, statement: str) -> str:
    command = settings.tool("psql", "-X", "-U", settings.db_user, "-d", settings.db_name, "-At", "-c", statement)
    try:
        done = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BackupError("the database could not be asked: %s" % error) from error
    if done.returncode != 0:
        raise BackupError("the database did not answer: %s" % done.stderr.strip()[-300:])
    return done.stdout.strip()


def tables_in(settings: Settings, schemas: Optional[Sequence[str]] = None) -> int:
    if schemas is None:
        return int(query(settings, TABLES_SQL))
    listed = ", ".join("'%s'" % schema for schema in schemas)
    return int(query(settings, "SELECT count(*) FROM pg_tables WHERE schemaname IN (%s)" % listed))


def dump(settings: Settings, target: Path, schemas: Optional[Sequence[str]] = None) -> None:
    """One pg_dump file: the whole database, or the schemas named."""
    command = settings.tool("pg_dump", "-U", settings.db_user, "-d", settings.db_name,
                            "--format=custom", "--compress=" + settings.compress)
    if schemas is not None:
        # A schema that is not there is a mistake to hear about, not to skip.
        command += ["--strict-names", *(part for schema in schemas for part in ("-n", schema))]
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as out:
            done = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.PIPE)
    except OSError as error:
        raise BackupError("pg_dump could not be run: %s" % error) from error
    if done.returncode != 0:
        raise BackupError("pg_dump stopped with an error: %s"
                          % done.stderr.decode("utf-8", "replace").strip()[-300:])
    if target.stat().st_size == 0:
        raise BackupError("pg_dump wrote an empty file")


def tables_in_file(settings: Settings, target: Path) -> int:
    """Read the backup back: how many tables' rows it says it holds."""
    try:
        with open(target, "rb") as handle:
            done = subprocess.run(settings.tool("pg_restore", "--list", stdin=True), stdin=handle,
                                  capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BackupError("the backup could not be read back: %s" % error) from error
    if done.returncode != 0:
        raise BackupError("the backup does not read back: %s"
                          % done.stderr.decode("utf-8", "replace").strip()[-300:])
    return sum(1 for line in done.stdout.decode("utf-8", "replace").splitlines() if " TABLE DATA " in line)


# -- the folder -------------------------------------------------------------------


def same_drive(one: Path, other: Path) -> bool:
    return os.stat(one).st_dev == os.stat(other).st_dev


def nearest_existing(path: Path) -> Path:
    while not path.exists():
        path = path.parent
    return path


def drive_problem(settings: Settings) -> Optional[str]:
    """Why backups may not go where they are set to go, or None."""
    if settings.data_directory is None:
        return None
    if not settings.data_directory.exists():
        return ("EDFINDER_BACKUP_DATA_DIR: %s is not there, so it cannot be told whether the backups "
                "would be on the database's own drive" % settings.data_directory)
    if same_drive(nearest_existing(settings.directory), settings.data_directory):
        return ("%s is on the same drive as the database (%s). A backup there would be lost with the "
                "database. Set EDFINDER_BACKUP_DIR to a folder on another drive"
                % (settings.directory, settings.data_directory))
    return None


def prepare(settings: Settings) -> None:
    problem = drive_problem(settings)
    if problem:
        raise BackupError(problem)
    try:
        for folder in (settings.directory, settings.small_directory, settings.full_directory):
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    except PermissionError:
        raise BackupError("%s cannot be written. Run this with sudo" % settings.directory) from None


def lock(settings: Settings, kind: str):
    """One backup of a kind at a time. The two kinds each have their own
    lock: they write to different folders and only read the database, so the
    nightly small backup must not fail because the weekly full one, which
    takes hours, is still going."""
    handle = open(settings.directory / (".lock-" + kind), "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise BackupError("another %s backup is already running" % kind) from None
    return handle


def backups(folder: Path, pattern: "re.Pattern[str]") -> List[Path]:
    """This tool's finished backups in a folder, oldest first."""
    if not folder.is_dir():
        return []
    return sorted(item for item in folder.iterdir() if pattern.match(item.name))


def size_of(item: Path) -> int:
    if item.is_dir():
        return sum(part.stat().st_size for part in item.rglob("*") if part.is_file())
    return item.stat().st_size


def remove(item: Path) -> None:
    if item.is_dir():
        shutil.rmtree(item)
    else:
        item.unlink()
        note = item.with_name(item.name + ".json")
        if note.exists():
            note.unlink()


def clear_partials(folder: Path) -> None:
    """A backup that never finished (the machine went off, say) is no backup."""
    for item in folder.iterdir():
        if item.name.endswith(PARTIAL) and item.name.startswith("edfinder-"):
            say("Removing an unfinished backup left from before: %s" % item.name)
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()


def keep_newest(folder: Path, pattern: "re.Pattern[str]", keep: int) -> None:
    found = backups(folder, pattern)
    for item in found[:max(0, len(found) - keep)]:
        say("Removing the oldest: %s" % item.name)
        remove(item)


def running_jobs(settings: Settings) -> List[str]:
    """The nightly jobs that are running now. Empty where there is no systemd."""
    if not settings.not_during or shutil.which("systemctl") is None:
        return []
    try:
        done = subprocess.run(["systemctl", "is-active", *settings.not_during], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BackupError("systemd could not be asked what is running: %s" % error) from error
    states = done.stdout.split()
    if len(states) != len(settings.not_during):
        raise BackupError("systemd gave an answer that could not be read: %r" % done.stdout.strip()[:200])
    # A job that runs once and ends is "activating" for as long as it runs.
    return [unit for index, unit in enumerate(settings.not_during) if states[index] in ("active", "activating")]


def stamp() -> str:
    return datetime.now(timezone.utc).strftime(STAMP)


def made_at(item: Path) -> datetime:
    text = re.search(r"[0-9]{8}T[0-9]{6}Z", item.name).group(0)  # type: ignore[union-attr]
    return datetime.strptime(text, STAMP).replace(tzinfo=timezone.utc)


# -- the two kinds ------------------------------------------------------------------


def pack_settings(settings: Settings, target: Path) -> List[str]:
    """This machine's settings files, in one file only root can read. Their
    contents are copied, never read here or shown."""
    missing = [str(path) for path in settings.settings_paths if not path.exists()]
    if missing:
        raise BackupError("EDFINDER_BACKUP_SETTINGS names what is not there: %s" % ", ".join(missing))
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    names: List[str] = []
    try:
        with os.fdopen(descriptor, "wb") as out, tarfile.open(fileobj=out, mode="w:gz") as archive:
            for path in settings.settings_paths:
                archive.add(str(path), arcname=str(path).lstrip("/"))
            names = archive.getnames()
    except PermissionError as error:
        raise BackupError("a settings file cannot be read (%s). Run this with sudo" % error.filename) from None
    return names


def small(settings: Settings) -> int:
    prepare(settings)
    with lock(settings, "small"):
        folder = settings.small_directory
        clear_partials(folder)
        began = time.monotonic()
        name = "edfinder-small-" + stamp()
        if (folder / name).exists():
            raise BackupError("a small backup was made this very second (%s). Run it again" % name)
        partial = folder / (name + PARTIAL)
        partial.mkdir(mode=0o700)
        try:
            say("Backing up what users have written (%s) ..." % ", ".join(settings.schemas))
            dump(settings, partial / "users.dump", settings.schemas)
            wanted = tables_in(settings, settings.schemas)
            found = tables_in_file(settings, partial / "users.dump")
            if found != wanted or found == 0:
                raise BackupError("the backup holds %d tables, and the database has %d there" % (found, wanted))
            say("Backing up this machine's settings ...")
            names = pack_settings(settings, partial / "settings.tar.gz")
            about = {"made": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": "small",
                     "database": settings.db_name, "schemas": settings.schemas, "tables": found,
                     "settings_files": len(names), "bytes": size_of(partial)}
            (partial / "about.json").write_text(json.dumps(about, indent=1) + "\n", encoding="utf-8")
            partial.rename(folder / name)
        except BaseException:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        say("DONE: %s (%s; %d tables, %d settings files) in %s."
            % (folder / name, size_text(about["bytes"]), found, len(names), took(time.monotonic() - began)))
        keep_newest(folder, SMALL_NAME, settings.keep_small)
    return 0


def room_for_full(settings: Settings) -> Tuple[float, str]:
    """How much room a full backup is taken to need, and what that rests on."""
    existing = backups(settings.full_directory, FULL_NAME)
    if existing:
        last = existing[-1].stat().st_size
        return last * NEXT_FULL_GROWTH + settings.spare, "the last one was %s" % size_text(last)
    whole = int(query(settings, SIZE_SQL))
    return whole * FIRST_FULL_SHARE + settings.spare, ("the database is %s on disk, and a first backup "
                                                       "is taken to be under %d%% of that"
                                                       % (size_text(whole), FIRST_FULL_SHARE * 100))


def make_room(settings: Settings, needed: float) -> None:
    """Remove the oldest full backups if that, and only that, makes the room.
    The newest is never removed."""
    folder = settings.full_directory
    free = shutil.disk_usage(folder).free
    if free >= needed:
        return
    spare = backups(folder, FULL_NAME)[:-1]
    if free + sum(size_of(item) for item in spare) < needed:
        raise BackupError("not enough room on the backup drive: %s free, about %s needed. Nothing was removed"
                          % (size_text(free), size_text(needed)))
    for item in spare:
        say("Making room: removing the oldest full backup, %s" % item.name)
        remove(item)
        if shutil.disk_usage(folder).free >= needed:
            return


def full(settings: Settings) -> int:
    prepare(settings)
    with lock(settings, "full"):
        busy = running_jobs(settings)
        if busy:
            raise BackupError("%s is running. A full backup holds every table open for hours, and that job "
                              "needs one to itself. Start the backup once it has finished" % " and ".join(busy))
        folder = settings.full_directory
        clear_partials(folder)
        needed, why = room_for_full(settings)
        make_room(settings, needed)
        began = time.monotonic()
        name = "edfinder-full-" + stamp() + ".dump"
        if (folder / name).exists():
            raise BackupError("a full backup was made this very second (%s). Run it again" % name)
        partial = folder / (name + PARTIAL)
        say("Backing up the whole database to %s (%s). This takes hours on a big database ..."
            % (folder / name, why))
        try:
            dump(settings, partial)
            found = tables_in_file(settings, partial)
            if found == 0:
                raise BackupError("the backup reads back with no tables in it")
            now = tables_in(settings)
            about = {"made": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": "full",
                     "database": settings.db_name, "tables": found, "bytes": partial.stat().st_size,
                     "seconds": round(time.monotonic() - began), "compress": settings.compress}
            note = folder / (name + ".json")
            note.write_text(json.dumps(about, indent=1) + "\n", encoding="utf-8")
            os.chmod(note, 0o600)
            partial.rename(folder / name)
        except BaseException:
            if partial.exists():
                partial.unlink()
            raise
        if found != now:
            say("Note: the backup holds %d tables and the database has %d now. "
                "Tables made or removed while it ran would explain that." % (found, now))
        say("DONE: %s (%s; %d tables) in %s."
            % (folder / name, size_text(about["bytes"]), found, took(time.monotonic() - began)))
        keep_newest(folder, FULL_NAME, settings.keep_full)
    return 0


# -- looking --------------------------------------------------------------------------


def describe(kind: str, found: List[Path], keep: int, stale_days: float) -> bool:
    """Say what is there. True if the newest is fresh enough."""
    if not found:
        say("  %s: none yet." % kind)
        return False
    newest = found[-1]
    age = (datetime.now(timezone.utc) - made_at(newest)).total_seconds() / 86400
    say("  %s: %d kept (of %d at most). Newest %s, %s, %.1f days old."
        % (kind, len(found), keep, newest.name, size_text(size_of(newest)), age))
    for item in found[:-1]:
        say("    also %s, %s" % (item.name, size_text(size_of(item))))
    if age > stale_days:
        say("    That is older than it should be (more than %g days)." % stale_days)
        return False
    return True


def status(settings: Settings) -> int:
    say("Backups in %s:" % settings.directory)
    describe("Small (every night)", backups(settings.small_directory, SMALL_NAME), settings.keep_small, 2)
    describe("Full (every week)", backups(settings.full_directory, FULL_NAME), settings.keep_full, 8)
    if settings.directory.exists():
        say("  Free on that drive: %s." % size_text(shutil.disk_usage(settings.directory).free))
    return 0


def check(settings: Settings) -> int:
    problems: List[str] = []
    say("Backups go to %s." % settings.directory)
    problem = drive_problem(settings)
    if problem:
        problems.append(problem)
    elif settings.data_directory is None:
        say("  Not checked: whether that is the database's own drive (EDFINDER_BACKUP_DATA_DIR is empty).")
    else:
        say("  That is a different drive from the database's (%s)." % settings.data_directory)
    missing = [str(path) for path in settings.settings_paths if not path.exists()]
    if missing:
        problems.append("EDFINDER_BACKUP_SETTINGS names what is not there: %s" % ", ".join(missing))
    else:
        say("  Settings to keep: %s." % ", ".join(str(path) for path in settings.settings_paths))
    try:
        wanted = tables_in(settings, settings.schemas)
        if wanted == 0:
            problems.append("the database has no tables in %s" % ", ".join(settings.schemas))
        else:
            say("  The small backup: %d tables (%s)." % (wanted, ", ".join(settings.schemas)))
        needed, why = room_for_full(settings)
        free = shutil.disk_usage(nearest_existing(settings.directory)).free
        say("  The full backup: about %s of room needed at most (%s); %s free."
            % (size_text(needed), why, size_text(free)))
        removable = sum(size_of(item) for item in backups(settings.full_directory, FULL_NAME)[:-1])
        if free + removable < needed:
            problems.append("not enough room for a full backup: %s free, about %s needed"
                            % (size_text(free), size_text(needed)))
    except BackupError as error:
        problems.append(str(error))
    try:
        busy = running_jobs(settings)
        if busy:
            say("  Running now: %s. A full backup would not start until it has finished." % " and ".join(busy))
    except BackupError as error:
        problems.append(str(error))
    if os.geteuid() != 0:
        say("  Not run as root: the real backups need sudo, to read the settings files.")
    for item in problems:
        say("PROBLEM: %s." % item)
    if not problems:
        say("READY.")
    return 1 if problems else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Back up the ED-Finder database.")
    parser.add_argument("command", choices=("check", "small", "full", "status"))
    arguments = parser.parse_args(argv)
    try:
        settings = Settings()
        return {"check": check, "small": small, "full": full, "status": status}[arguments.command](settings)
    except BackupError as error:
        say("FAILED: %s." % error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
