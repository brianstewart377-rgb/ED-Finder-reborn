#!/usr/bin/env python3
"""Keep the galaxy tables fresh: fetch Spansh's 7-day file and apply it.

Spansh publishes, once a night, every system that changed in the last 7 days
(galaxy_7days.json.gz, about 3 GB). This fetches it and brings our systems,
bodies, rings and stations up to date from it (edfinder-galaxy-update.timer).
What we already have is left alone, so each night only what really changed is
written.

Why 7 days and not Spansh's one-day file: Spansh makes its files at a
different time each night (02:10 UTC on 10 November, 03:20 UTC on 11
November), so two one-day files can leave a gap between them, and a night the
update does not run would lose that day for good. Each 7-day file reaches back
over the last six nights as well, so neither can happen.

The file is chosen by how long ago the last update's file was made (the
database records that, so the choice still works after a copy to another
machine):

  * 6 days or less: the 7-day file.
  * 7 to 31 days: Spansh's 32-day file (galaxy_1month.json.gz, about 6 GB).
  * More: the update stops and says so. Only the full galaxy file reaches back
    that far; give it by hand with EDFINDER_GALAXY_URL.

A day is kept spare in both, because when exactly a file's days begin is
Spansh's to say.

    python3 galaxy_update.py check    fetch the file, then say what would change.
                                      Read only: the database is not touched.
    python3 galaxy_update.py run      fetch the file, then apply it
    python3 galaxy_update.py status   show the last update and the file on disk
    python3 galaxy_update.py check-scores
                                      work scores out again from the file on
                                      disk and compare them with the stored
                                      ones. Read only.
    python3 galaxy_update.py score    give new scores to the systems on the
                                      waiting list. Needs no file: everything
                                      the scores use is in the database.
    python3 galaxy_update.py reach    work out afresh which systems are within
                                      500 ly and 250 ly of an inhabited system
                                      (the Pi; migration 028). Needed once
                                      before the scoring can run there; after
                                      that the scoring does it each week.

Every system the update changes goes on a waiting list for new scores. When
EDFINDER_GALAXY_SCORES is "on", `run` also works through that list each time,
whether or not Spansh had anything new. So a night when the scoring fails is
made up by the next run.

    --limit N    stop after N systems (for timing a very big file)

What `run` does:

  1. Asks Spansh how big and how new the file is.
  2. Stops, successfully, if that file has already been applied.
  3. Downloads it next to the old copy, checks its size, and only then replaces
     the old copy. A failed download leaves the old copy alone.
  4. Starts a short-lived container from the image the live API is running, and
     runs the update in it (python -m spansh_delta.apply). So the update is
     always the same version as the app. The container shares the database
     container's network and connects to it as a local client: no password is
     needed, and none is stored or passed anywhere.
     If the database does not accept local clients that way, the container is
     started on the app's network with the API's own settings file instead
     (the same connection the API uses). This script only hands docker the
     file's path; it never opens the file.

The update itself is safe to interrupt and to run twice: systems are applied in
small transactions, and a second run finds the finished ones already up to date.

Settings come from the environment, then from a "deploy.conf" file beside this
script (KEY=VALUE lines, shared with edfinder_deploy.py), then from the
defaults below. Standard library only; needs Python 3.8 or newer, and
state_refresh.py beside this script (the download code is shared).
"""
from __future__ import annotations

import argparse
import email.utils
from datetime import datetime, timedelta, timezone
import fcntl
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import state_refresh as fetch  # noqa: E402  (shared download code)

DEFAULTS: Dict[str, str] = {
    # Where the downloaded file is kept between runs.
    "EDFINDER_GALAXY_DIR": "/var/lib/edfinder-galaxy",
    # Where Spansh keeps its files.
    "EDFINDER_GALAXY_SOURCE": "https://downloads.spansh.co.uk/",
    # One fixed file to apply, instead of the choice above: for the full galaxy
    # file, or for trying something out. Empty means choose.
    "EDFINDER_GALAXY_URL": "",
    # The database: its container, and the user and database name inside it.
    # No password belongs here.
    "EDFINDER_DB_CONTAINER": "edfinder-v3-phase4c-full-20260827_r5-postgres",
    "EDFINDER_DB_USER": "edfinder_v3",
    "EDFINDER_DB_NAME": "edfinder_v3_phase4c_full_20260827_r5",
    # The compose project of the live app (as in edfinder_deploy.py). The update
    # runs from the image of its API container.
    "EDFINDER_PROJECT": "edfinder",
    # How the update reaches the database:
    #   local  as a local client inside the database container's network
    #   app    on the app's network, with the API's own settings file
    #   auto   local first; app if the database refuses the local connection
    "EDFINDER_GALAXY_CONNECT": "auto",
    # For "app": the app's Docker network and the API's settings file (as in
    # edfinder_deploy.py). The file is passed to docker, never read here.
    "EDFINDER_NETWORK": "edfinder-v3-production",
    "EDFINDER_API_ENV_FILE": "/etc/ed-finder/v3-production/api.env",
    # "on": `run` also gives new scores to the systems on the waiting list.
    "EDFINDER_GALAXY_SCORES": "off",
}

JOB_NAME = "edfinder-galaxy-update"

# Spansh's files of recent changes, and how far back each is trusted to reach
# (its own span, less a day).
DAY_FILE = "galaxy_1day.json.gz"
WEEK_FILE = "galaxy_7days.json.gz"
MONTH_FILE = "galaxy_1month.json.gz"
WEEK_REACH = timedelta(days=6)
MONTH_REACH = timedelta(days=31)
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

# The update's own exit code for "could not connect to the database".
EXIT_NO_CONNECTION = 3

LAST_RUN_SQL = (
    "SELECT r.run_state, "
    "to_char(coalesce(r.completed_at, r.started_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI'), "
    "coalesce(r.scope_contract->>'file', ''), coalesce(r.scope_contract->>'systems_read', ''), "
    "coalesce(r.scope_contract->>'changed', ''), coalesce(r.scope_contract->>'new', '') "
    "FROM v3_source.source_run r WHERE r.acquisition_kind = 'DELTA' "
    "ORDER BY r.started_at DESC LIMIT 1"
)
# When the file of the last finished update was made: everything Spansh had
# changed up to then is in the database.
COVERED_SQL = (
    "SELECT coalesce(to_char(max(r.effective_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'), '') "
    "FROM v3_source.source_run r WHERE r.acquisition_kind = 'DELTA' AND r.run_state = 'SUCCEEDED'"
)
WAITING_SQL = "SELECT count(*) FROM v3_state.rescore_queue"
# When reach was last worked out afresh (migration 028; the Pi).
REACH_SQL = (
    "SELECT to_char(worked_out_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI'), anchors, systems, within_250 "
    "FROM v3_state.reach_log ORDER BY reach_id DESC LIMIT 1"
)
# What the database accepts from a local client. Names and methods only.
RULES_SQL = (
    "SELECT type, array_to_string(database, ','), array_to_string(user_name, ','), "
    "coalesce(address, ''), auth_method FROM pg_hba_file_rules WHERE error IS NULL ORDER BY rule_number"
)


class UpdateError(Exception):
    """Something went wrong; the message says what, in plain words."""


say = fetch.say


class Settings:
    def __init__(self, environ: Optional[Dict[str, str]] = None,
                 conf: Optional[Dict[str, str]] = None) -> None:
        environ = dict(os.environ if environ is None else environ)
        conf = fetch.read_conf(HERE / "deploy.conf") if conf is None else conf
        values = {key: environ.get(key, conf.get(key, default))
                  for key, default in DEFAULTS.items()}
        self.directory = Path(values["EDFINDER_GALAXY_DIR"])
        self.source = values["EDFINDER_GALAXY_SOURCE"].strip()
        if not self.source.startswith(("https://", "http://")) or not self.source.endswith("/"):
            raise UpdateError("EDFINDER_GALAXY_SOURCE: %r is not a folder address ending in /"
                              % self.source)
        # A fixed file, or None to choose (see the top of this file).
        self.url: Optional[str] = values["EDFINDER_GALAXY_URL"].strip() or None
        if self.url is not None and (not self.url.startswith(("https://", "http://"))
                                     or not fetch.file_name(self.url)):
            raise UpdateError("EDFINDER_GALAXY_URL: %r is not a file address" % self.url)
        self.db_container = values["EDFINDER_DB_CONTAINER"]
        self.db_user = values["EDFINDER_DB_USER"]
        self.db_name = values["EDFINDER_DB_NAME"]
        self.project = values["EDFINDER_PROJECT"]
        self.network = values["EDFINDER_NETWORK"]
        for key, value in (("EDFINDER_DB_CONTAINER", self.db_container),
                           ("EDFINDER_DB_USER", self.db_user),
                           ("EDFINDER_DB_NAME", self.db_name),
                           ("EDFINDER_PROJECT", self.project),
                           ("EDFINDER_NETWORK", self.network)):
            if not NAME.match(value):
                raise UpdateError("%s: %r is not a plain name" % (key, value))
        self.connect = values["EDFINDER_GALAXY_CONNECT"]
        if self.connect not in ("auto", "local", "app"):
            raise UpdateError("EDFINDER_GALAXY_CONNECT must be auto, local or app")
        self.api_env_file = values["EDFINDER_API_ENV_FILE"]
        if values["EDFINDER_GALAXY_SCORES"] not in ("on", "off"):
            raise UpdateError("EDFINDER_GALAXY_SCORES must be on or off")
        self.scores = values["EDFINDER_GALAXY_SCORES"] == "on"

    def path(self, name: str) -> Path:
        return self.directory / name

    @property
    def usual_file(self) -> str:
        """The file a run takes when nothing is unusual."""
        return fetch.file_name(self.url) if self.url else WEEK_FILE

    @property
    def record(self) -> Path:
        return self.directory / "last-applied.json"

    @property
    def api_container(self) -> str:
        return self.project + "-api"

    @property
    def psql(self) -> List[str]:
        return ["docker", "exec", "-i", self.db_container, "psql", "-X",
                "-U", self.db_user, "-d", self.db_name]


def docker(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["docker", *args], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError("docker could not be run: %s" % error) from error


def live_image(settings: Settings) -> str:
    """The image the live API container is running."""
    done = docker("inspect", "--format", "{{.Config.Image}}", settings.api_container)
    image = done.stdout.strip()
    if done.returncode != 0 or not image:
        raise UpdateError(
            "the live API container (%s) was not found, so there is no image to run the "
            "update from. Is the app deployed on this machine?" % settings.api_container)
    return image


def published(version: Dict[str, Any]) -> Optional[datetime]:
    """When Spansh made the file (its Last-Modified time), or None."""
    try:
        when = email.utils.parsedate_to_datetime(version.get("modified") or "")
    except (TypeError, ValueError):
        return None
    if when is None or when.tzinfo is None:
        return None
    return when


def made_at(version: Dict[str, Any]) -> Optional[str]:
    """Spansh's Last-Modified time for the file, as an ISO time with a zone."""
    when = published(version)
    return when.isoformat() if when else None


def when_text(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")


def query(settings: Settings, statement: str) -> List[List[str]]:
    try:
        done = subprocess.run([*settings.psql, "-At", "-F", "|", "-c", statement],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError("psql could not be run: %s" % error) from error
    if done.returncode != 0:
        raise UpdateError("psql reported an error: %s" % done.stderr.strip()[-300:])
    return [line.split("|") for line in done.stdout.strip().splitlines() if line]


def thousands(text: str) -> str:
    return "{:,}".format(int(text)) if text.isdigit() else text


def describe(rows: List[List[str]]) -> str:
    if not rows or len(rows[0]) != 6:
        return "No galaxy update has been recorded yet."
    state, when, name, read, changed, new = rows[0]
    outcome = {"SUCCEEDED": "finished", "PARTIAL": "stopped at its limit", "RUNNING": "is running",
               "FAILED": "FAILED"}.get(state, state)
    return "Last update: %s UTC, %s (%s). %s systems read, %s changed, %s new." % (
        when, outcome, name or "file unknown", read or "?", changed or "?", new or "?")


def covered_until(settings: Settings) -> Optional[datetime]:
    """When the file of the last finished update was made, or None if none has finished."""
    rows = query(settings, COVERED_SQL)
    text = rows[0][0] if rows and rows[0] else ""
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        raise UpdateError("the database gave an unexpected time for the last update: %r" % text) from None


def applied_here(settings: Settings, version: Dict[str, Any]) -> bool:
    """True when the last update on this machine applied exactly this file."""
    return (fetch.read_record(settings.record).get("applied") == version
            and fetch.have_version(settings.path(fetch.file_name(version["url"])), version))


def choose(settings: Settings) -> Tuple[str, Dict[str, Any], bool]:
    """Which Spansh file to take: its address, its version, and whether it has
    already been applied (so there is nothing new)."""
    if settings.url:
        version = fetch.remote_version(settings.url)
        return settings.url, version, applied_here(settings, version)
    week_url = settings.source + WEEK_FILE
    week = fetch.remote_version(week_url)
    if applied_here(settings, week):
        return week_url, week, True
    made = published(week)
    if made is None:
        raise UpdateError("Spansh did not say when its 7-day file was made, so there is no "
                          "telling whether it reaches back far enough. Nothing was changed.")
    last = covered_until(settings)
    if last is None:
        raise UpdateError(
            "no galaxy update has finished in this database yet, so there is no telling how "
            "far back the first one has to reach. Apply a first file by hand with "
            "EDFINDER_GALAXY_URL (see the top of galaxy_update.py). Nothing was changed.")
    if made <= last:
        return week_url, week, True
    if made - last <= WEEK_REACH:
        return week_url, week, False
    month_url = settings.source + MONTH_FILE
    month = fetch.remote_version(month_url)
    made = published(month)
    if made is not None and made - last <= MONTH_REACH:
        say("The last update applied Spansh's files of %s, more than 6 days ago. "
            "Taking the 32-day file instead of the 7-day one." % when_text(last))
        return month_url, month, False
    raise UpdateError(
        "the last update applied Spansh's files of %s, more than 31 days ago. Spansh's "
        "32-day file no longer reaches back that far; only the full galaxy file "
        "(galaxy.json.gz, about 120 GB) does. Apply it by hand with EDFINDER_GALAXY_URL. "
        "Nothing was changed." % when_text(last))


def file_on_disk(settings: Settings) -> Optional[str]:
    """The Spansh file here that the last update applied, else the usual one, if present."""
    names = []
    applied = fetch.read_record(settings.record).get("applied")
    if isinstance(applied, dict) and isinstance(applied.get("url"), str):
        names.append(fetch.file_name(applied["url"]))
    names.append(settings.usual_file)
    for name in names:
        if name and NAME.match(name) and settings.path(name).is_file():
            return name
    return None


def tidy(settings: Settings, keep: str) -> None:
    """Remove the other files of recent changes: only the one just applied is kept."""
    for name in (DAY_FILE, WEEK_FILE, MONTH_FILE):
        if name == keep:
            continue
        for path in (settings.path(name), settings.path(name + ".version"),
                     settings.path(name + ".part")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def connection_rules(settings: Settings) -> List[str]:
    """How the database lets local clients in, for a failed connection."""
    try:
        rows = query(settings, RULES_SQL)
    except UpdateError:
        return []
    return ["    %-6s database=%s user=%s address=%s method=%s" % tuple(row)
            for row in rows if len(row) == 5]


# What each command runs inside the container: the module, its mode, and
# whether it reads the Spansh file. The scoring works from the database.
JOBS = {
    "check": ("spansh_delta.apply", "check", True),
    "run": ("spansh_delta.apply", "apply", True),
    "check-scores": ("spansh_delta.scores", "check", True),
    "score": ("spansh_delta.scores", "apply", False),
    "reach": ("spansh_delta.reach", "rebuild", False),
}


def job_command(settings: Settings, route: str, image: str, command_name: str,
                version: Dict[str, Any], limit: Optional[int], name: Optional[str]) -> List[str]:
    module, mode, reads_file = JOBS[command_name]
    # --init: a small first process in the container passes signals on.
    # Python as the container's first process ignores the polite stop signal,
    # so when systemd stopped this job (its time limit, or by hand) the job's
    # container went on working on its own, holding the name and the lock.
    command = ["docker", "run", "--rm", "--init", "--name", JOB_NAME]
    if route == "local":
        # Share the database container's network and connect as a local client.
        command += [
            "--network", "container:" + settings.db_container,
            "--env", "PGHOST=127.0.0.1", "--env", "PGPORT=5432",
            "--env", "PGUSER=" + settings.db_user, "--env", "PGDATABASE=" + settings.db_name,
        ]
    else:
        # Join the app's network and use the API's own connection settings.
        command += [
            "--network", settings.network, "--env-file", settings.api_env_file,
            "--env", "EDFINDER_GALAXY_USE_DATABASE_URL=1",
        ]
    command += [
        "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONUNBUFFERED=1",
        "--volume", "%s:/data:ro" % settings.directory,
        "--security-opt", "no-new-privileges:true", "--pids-limit", "256",
        image, "python", "-m", module, mode,
    ]
    if reads_file:
        if not name:
            raise UpdateError("no Spansh file was given for the %s" % command_name)
        command.append("/data/" + name)
    when = made_at(version) if module == "spansh_delta.apply" else None
    if when:
        command += ["--made-at", when]
    if limit is not None:
        command += ["--limit", str(limit)]
    return command


def run_job(settings: Settings, command_name: str, version: Dict[str, Any],
            limit: Optional[int], name: Optional[str] = None) -> int:
    image = live_image(settings)
    routes = ["local", "app"] if settings.connect == "auto" else [settings.connect]
    code = 1
    for position, route in enumerate(routes):
        if docker("inspect", "--format", "{{.State.Status}}", JOB_NAME).returncode == 0:
            raise UpdateError(
                "a container named %s already exists: another update is running, or one was "
                "left behind. Look with: docker ps -a --filter name=%s" % (JOB_NAME, JOB_NAME))
        if route == "app" and not os.path.isfile(settings.api_env_file):
            raise UpdateError("the API's settings file %s was not found" % settings.api_env_file)
        say("Running the %s from %s ..." % (
            {"run": "update", "score": "scoring", "reach": "work on reach"}.get(command_name, "check"), image)
            if position == 0 else
            "The database did not accept a local client. Trying the app's own connection "
            "settings instead ...")
        try:
            code = subprocess.run(
                job_command(settings, route, image, command_name, version, limit, name),
                stdin=subprocess.DEVNULL).returncode
        except OSError as error:
            raise UpdateError("docker could not be run: %s" % error) from error
        if code != EXIT_NO_CONNECTION:
            return code
    rules = connection_rules(settings)
    if rules:
        say("For reference, the database's rules for who may connect (no secrets):")
        for line in rules:
            say(line)
    return code


def make_readable(settings: Settings, name: str) -> None:
    # The update's container must be able to read the file whoever it runs as.
    os.chmod(settings.directory, 0o755)
    os.chmod(settings.path(name), 0o644)


def cmd_apply(settings: Settings, mode: str, limit: Optional[int]) -> int:
    settings.directory.mkdir(parents=True, exist_ok=True)
    with open(settings.directory / "lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise UpdateError("another galaxy update is already running") from None

        if mode == "score":
            # The waiting list is in the database, and so is everything the
            # scores are worked out from. No file is needed.
            return finished(run_job(settings, "score", {}, limit), "scoring")
        if mode == "reach":
            # Everything reach is worked out from is in the database too.
            return finished(run_job(settings, "reach", {}, None), "work on reach")
        if mode == "check-scores":
            # Compared with the file already here, normally the one the last
            # update applied, so nothing is fetched.
            name = file_on_disk(settings)
            if name:
                version = fetch.read_record(settings.path(name + ".version"))
                make_readable(settings, name)
                return finished(run_job(settings, mode, version, limit, name), "check")
            say("Asking Spansh what is new ...")
            url = settings.url or settings.source + WEEK_FILE
            version, applied = fetch.remote_version(url), False
        else:
            say("Asking Spansh what is new ...")
            url, version, applied = choose(settings)
            if mode == "run" and limit is None and applied:
                if not settings.scores:
                    say("Spansh has published nothing new since the last update. Nothing to do.")
                    return 0
                say("Spansh has published nothing new since the last update. "
                    "Looking at the waiting list for new scores ...")
                return finished(run_job(settings, "score", version, None), "scoring")

        name = fetch.file_name(url)
        target = settings.path(name)
        if not fetch.have_version(target, version):
            needed = version["size"] + fetch.SPARE_BYTES
            free = shutil.disk_usage(settings.directory).free
            if free < needed:
                raise UpdateError(
                    "not enough free disk space in %s: %.1f GB free, %.1f GB needed" % (
                        settings.directory, free / 1e9, needed / 1e9))
            say("Downloading %s (%.1f GB) ..." % (name, version["size"] / 1e9))
            fetch.download(version, target)
            if not fetch.have_version(target, version):
                raise UpdateError("%s is not the version Spansh offered" % name)
        make_readable(settings, name)

        code = run_job(settings, mode, version, limit, name)
        if code != 0 or mode != "run" or limit is not None:
            return finished(code, "update" if mode == "run" else "check")
        fetch.write_record(settings.record, {"applied": version})
        if not settings.url:
            tidy(settings, name)
        if settings.scores:
            return finished(run_job(settings, "score", version, None), "scoring")
    return 0


def finished(code: int, what: str) -> int:
    if code == EXIT_NO_CONNECTION:
        say("FAILED: the %s could not connect to the database. Nothing was changed." % what)
    elif code != 0:
        say("FAILED: the %s ended with code %d; its own messages are above." % (what, code))
    return code


def cmd_status(settings: Settings) -> int:
    say(describe(query(settings, LAST_RUN_SQL)))
    try:
        say("%s systems are waiting for new scores." % query(settings, WAITING_SQL)[0][0])
    except (UpdateError, IndexError):
        say("The list of systems waiting for new scores does not exist yet (migration 026).")
    say("Scoring after each update is switched %s." % ("on" if settings.scores else
        "off (EDFINDER_GALAXY_SCORES in deploy.conf)"))
    try:
        reach = query(settings, REACH_SQL)
    except UpdateError:
        reach = None
    if reach is None:
        say("Reach is not kept in this database (migration 028 has not been applied).")
    elif not reach or len(reach[0]) != 4:
        say("Reach has not been worked out yet. The scoring will not run until it has: "
            "python3 galaxy_update.py reach")
    else:
        when, anchors, systems, near = reach[0]
        say("Reach last worked out afresh %s UTC, from %s inhabited systems: %s systems within "
            "500 ly, %s of them within 250 ly." % (when, thousands(anchors), thousands(systems),
                                                     thousands(near)))
    if settings.url:
        say("Each run takes %s (EDFINDER_GALAXY_URL)." % settings.url)
    else:
        say("Each run takes Spansh's 7-day file, or its 32-day file when the last update "
            "is more than 6 days old.")
    name = file_on_disk(settings)
    if name:
        noted = fetch.read_record(settings.path(name + ".version"))
        say("%s: %.1f GB, published %s" % (
            name, settings.path(name).stat().st_size / 1e9, noted.get("modified") or "unknown"))
    else:
        say("No Spansh file on disk yet.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Spansh's file of recent changes to the galaxy and apply it.")
    parser.add_argument("command", choices=("check", "run", "status", "check-scores", "score", "reach"))
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after this many systems (for timing a very big file)")
    args = parser.parse_args(argv)
    try:
        if args.limit is not None and args.limit < 1:
            raise UpdateError("--limit must be 1 or more")
        settings = Settings()
        if args.command == "status":
            return cmd_status(settings)
        return cmd_apply(settings, args.command, args.limit)
    except (UpdateError, fetch.RefreshError) as error:
        say("FAILED: %s" % error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
