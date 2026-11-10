#!/usr/bin/env python3
"""Keep the system state tables fresh: fetch Spansh's files and load them.

Find's claim rule (which systems are inhabited or being claimed) reads the
v3_state tables. They are only as fresh as the last load, so this runs once a
night (edfinder-state-refresh.timer).

    python3 state_refresh.py run      fetch what is new, then load it
    python3 state_refresh.py status   show the last load and the files on disk

What `run` does:

  1. Asks Spansh how big and how new each file is.
  2. Stops, successfully, if nothing changed since the last load.
  3. Downloads each changed file next to the old copy, checks its size, and only
     then replaces the old copy. A failed download leaves the old copy alone.
  4. Pipes the loader (spansh_system_state.py) into psql. The load is one
     transaction: if anything fails, the database is left exactly as it was.

Settings come from the environment, then from a "deploy.conf" file beside this
script (KEY=VALUE lines, shared with edfinder_deploy.py), then from the
defaults below. Standard library only; needs Python 3.8 or newer.
"""
from __future__ import annotations

import argparse
import fcntl
import http.client
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent

DEFAULTS: Dict[str, str] = {
    # Where the downloaded files are kept between runs (about 9.5 GB).
    "EDFINDER_REFRESH_DIR": "/var/lib/edfinder-state",
    # The files to load, in this order. The first must be the stations file:
    # together they are every system that has a population or a station.
    "EDFINDER_REFRESH_URLS": (
        "https://downloads.spansh.co.uk/galaxy_stations.json.gz "
        "https://downloads.spansh.co.uk/galaxy_populated.json.gz"
    ),
    # The psql command that reaches the database. No password belongs here.
    "EDFINDER_REFRESH_PSQL": (
        "docker exec -i edfinder-v3-phase4c-full-20260827_r5-postgres "
        "psql -X -U edfinder_v3 -d edfinder_v3_phase4c_full_20260827_r5"
    ),
    # The loader program. Empty: spansh_system_state.py beside this script.
    "EDFINDER_REFRESH_LOADER": "",
}

CHUNK = 1024 * 1024
PROGRESS_EVERY = 512 * 1024 * 1024
SPARE_BYTES = 1024 * 1024 * 1024
NETWORK_TIMEOUT = 120


class RefreshError(Exception):
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


class Settings:
    def __init__(self, environ: Optional[Dict[str, str]] = None,
                 conf: Optional[Dict[str, str]] = None) -> None:
        environ = dict(os.environ if environ is None else environ)
        conf = read_conf(HERE / "deploy.conf") if conf is None else conf
        values = {key: environ.get(key, conf.get(key, default))
                  for key, default in DEFAULTS.items()}
        self.directory = Path(values["EDFINDER_REFRESH_DIR"])
        self.urls = values["EDFINDER_REFRESH_URLS"].split()
        if not self.urls:
            raise RefreshError("EDFINDER_REFRESH_URLS names no file to load")
        for url in self.urls:
            if not url.startswith(("https://", "http://")) or not file_name(url):
                raise RefreshError("EDFINDER_REFRESH_URLS: %r is not a file address" % url)
        if len({file_name(url) for url in self.urls}) != len(self.urls):
            raise RefreshError("EDFINDER_REFRESH_URLS: two files have the same name")
        self.psql = shlex.split(values["EDFINDER_REFRESH_PSQL"])
        if not self.psql:
            raise RefreshError("EDFINDER_REFRESH_PSQL is empty")
        loader = values["EDFINDER_REFRESH_LOADER"] or str(HERE / "spansh_system_state.py")
        self.loader = Path(loader)

    @property
    def record(self) -> Path:
        return self.directory / "last-load.json"


def file_name(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


# -- what Spansh has ------------------------------------------------------------

def _open(url: str, method: str = "GET"):
    request = urllib.request.Request(
        url, method=method, headers={"User-Agent": "edfinder-state-refresh/1"})
    return urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT)


def remote_version(url: str) -> Dict[str, Any]:
    """Size and age of the file Spansh is offering right now."""
    try:
        with _open(url, "HEAD") as response:
            size = response.headers.get("Content-Length")
            if size is None or not size.isdigit() or int(size) == 0:
                raise RefreshError("%s: the server did not say how big the file is" % url)
            return {
                "url": url,
                "size": int(size),
                "modified": response.headers.get("Last-Modified") or "",
                "etag": response.headers.get("ETag") or "",
            }
    except urllib.error.HTTPError as error:
        error.close()
        raise RefreshError("%s: could not be reached (the server answered %d)" % (
            url, error.code)) from None
    except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
        raise RefreshError("%s: could not be reached (%s)" % (url, error)) from error


def read_record(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_record(path: Path, value: Dict[str, Any]) -> None:
    partial = path.with_name(path.name + ".part")
    partial.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(partial, path)


def have_version(target: Path, version: Dict[str, Any]) -> bool:
    """True when `target` is already this exact version of the file."""
    noted = read_record(target.with_name(target.name + ".version"))
    return (target.is_file() and noted == version
            and target.stat().st_size == version["size"])


def download(version: Dict[str, Any], target: Path) -> None:
    """Fetch one file beside the old copy and swap it in only when complete."""
    partial = target.with_name(target.name + ".part")
    received = 0
    next_report = PROGRESS_EVERY
    started = time.monotonic()
    try:
        with _open(version["url"]) as response, open(partial, "wb") as out:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                received += len(chunk)
                if received >= next_report:
                    say("  %s: %.1f of %.1f GB" % (
                        target.name, received / 1e9, version["size"] / 1e9))
                    next_report += PROGRESS_EVERY
        if received != version["size"]:
            raise RefreshError(
                "%s: the download stopped at %d of %d bytes" % (
                    target.name, received, version["size"]))
    except urllib.error.HTTPError as error:
        error.close()
        partial.unlink(missing_ok=True)
        raise RefreshError("%s: the download failed (the server answered %d)" % (
            target.name, error.code)) from None
    except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
        partial.unlink(missing_ok=True)
        raise RefreshError("%s: the download failed (%s)" % (
            target.name, type(error).__name__ if not str(error) else error)) from error
    except RefreshError:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, target)
    write_record(target.with_name(target.name + ".version"), version)
    say("  %s: downloaded %.1f GB in %.0f s" % (
        target.name, received / 1e9, time.monotonic() - started))


# -- the load --------------------------------------------------------------------

def load(settings: Settings, files: Sequence[Path]) -> None:
    """Pipe the loader into psql. Either everything is applied or nothing is."""
    if not settings.loader.is_file():
        raise RefreshError("the loader program is missing: %s" % settings.loader)
    try:
        psql = subprocess.Popen(
            [*settings.psql, "-v", "ON_ERROR_STOP=1"], stdin=subprocess.PIPE)
    except OSError as error:
        raise RefreshError("psql could not be started (%s): %s" % (
            " ".join(settings.psql[:3]), error)) from error
    loader = subprocess.Popen(
        [sys.executable, str(settings.loader), "--snapshot", *map(str, files)],
        stdout=psql.stdin)
    assert psql.stdin is not None
    psql.stdin.close()  # the loader holds the only writing end now
    loader_code = loader.wait()
    psql_code = psql.wait()
    if loader_code != 0 or psql_code != 0:
        # Either one stopping makes the other stop too; their own messages
        # are just above this one.
        raise RefreshError(
            "nothing was loaded: the loader ended with code %d and psql with code %d" % (
                loader_code, psql_code))


LAST_LOAD_SQL = (
    "SELECT to_char(loaded_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI'), staged_systems, "
    "systems_written, systems_removed, "
    "coalesce(to_char(newest_record_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI'), '') "
    "FROM v3_state.load_log ORDER BY load_id DESC LIMIT 1"
)


def last_load(settings: Settings) -> Optional[Tuple[str, ...]]:
    try:
        done = subprocess.run(
            [*settings.psql, "-At", "-F", "|", "-c", LAST_LOAD_SQL],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RefreshError("psql could not be run: %s" % error) from error
    if done.returncode != 0:
        raise RefreshError("psql reported an error: %s" % done.stderr.strip()[-300:])
    line = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ""
    return tuple(line.split("|")) if line else None


def describe(row: Optional[Tuple[str, ...]]) -> str:
    if row is None or len(row) != 5:
        return "No load has been recorded yet."
    loaded_at, systems, written, removed, newest = row
    return ("Last load: %s UTC. %s systems read, %s changed, %s removed. "
            "Newest record: %s UTC." % (loaded_at, systems, written, removed, newest or "unknown"))


# -- commands --------------------------------------------------------------------

def cmd_run(settings: Settings) -> int:
    settings.directory.mkdir(parents=True, exist_ok=True)
    with open(settings.directory / "lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RefreshError("another refresh is already running") from None

        say("Asking Spansh what is new ...")
        # One (version Spansh offers, where it is kept) pair per file.
        files = [(remote_version(url), settings.directory / file_name(url))
                 for url in settings.urls]
        versions = [version for version, _ in files]
        targets = [target for _, target in files]
        if (read_record(settings.record).get("loaded") == versions
                and all(have_version(target, version) for version, target in files)):
            say("Spansh has published nothing new since the last load. Nothing to do.")
            return 0

        wanted = [(v, t) for v, t in files if not have_version(t, v)]
        needed = sum(v["size"] for v, _ in wanted) + SPARE_BYTES
        free = shutil.disk_usage(settings.directory).free
        if free < needed:
            raise RefreshError(
                "not enough free disk space in %s: %.1f GB free, %.1f GB needed" % (
                    settings.directory, free / 1e9, needed / 1e9))
        for version, target in wanted:
            say("Downloading %s (%.1f GB) ..." % (target.name, version["size"] / 1e9))
            download(version, target)
        for version, target in files:
            if not have_version(target, version):
                raise RefreshError("%s is not the version Spansh offered" % target.name)

        say("Loading into the database (several minutes) ...")
        load(settings, targets)
        write_record(settings.record, {"loaded": versions})
        say("DONE. " + describe(last_load(settings)))
    return 0


def cmd_status(settings: Settings) -> int:
    say(describe(last_load(settings)))
    for url in settings.urls:
        target = settings.directory / file_name(url)
        if target.is_file():
            noted = read_record(target.with_name(target.name + ".version"))
            say("%s: %.1f GB, published %s" % (
                target.name, target.stat().st_size / 1e9, noted.get("modified") or "unknown"))
        else:
            say("%s: not downloaded yet" % target.name)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Spansh's system state files and load them.")
    parser.add_argument("command", choices=("run", "status"))
    args = parser.parse_args(argv)
    try:
        settings = Settings()
        return cmd_run(settings) if args.command == "run" else cmd_status(settings)
    except RefreshError as error:
        say("FAILED: %s" % error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
