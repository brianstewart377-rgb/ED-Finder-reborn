#!/usr/bin/env python3
"""Say how the machine that runs ED-Finder is doing. Changes nothing.

    sudo python3 pi_report.py

One page of facts to read, or to paste to whoever is tuning the machine:

  the machine    model, memory, how busy it is
  heat and power temperature, and whether the Pi has slowed itself down for
                 heat or for a weak power supply
  drives         room, how each is mounted, and the speed of the link to each
                 NVMe drive
  memory limits  whether the limits set on the containers are being kept
  containers     which are running, and what each is using
  the database   its size, the settings that matter for speed, how often it
                 finds what it wants in memory, slow statements in the last day
  the site       how long the health check and a Find search take, three times

It only reads: files under /proc and /sys, "docker" to list and to ask the
database questions, and the site's own address on this machine. It writes no
file, changes no setting and restarts nothing. It shows no password and no
contents of any settings file. Statements the database logged as slow are
counted and timed, never shown: their text can hold what a user searched for.

Anything it cannot read on this machine it says so and carries on; it runs
on a machine that is not a Pi, with less to say.

Settings come from the environment, then from a "deploy.conf" file beside
this script (KEY=VALUE lines, shared with the other tools), then from the
defaults below. Without sudo, the parts that need "docker" may be refused.

Standard library only; needs Python 3.9 or newer.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent

DEFAULTS: Dict[str, str] = {
    # The site's own address on this machine (the deploy tool's live address).
    "EDFINDER_LIVE_BIND": "127.0.0.1:58080",
    # The folders whose drives matter: the database's, and the backups'.
    "EDFINDER_BACKUP_DATA_DIR": "/srv/data",
    "EDFINDER_BACKUP_DIR": "/var/backups/edfinder",
    # The database: its container, and the user and database name inside it.
    "EDFINDER_DB_CONTAINER": "edfinder-v3-phase4c-full-20260827_r5-postgres",
    "EDFINDER_DB_USER": "edfinder_v3",
    "EDFINDER_DB_NAME": "edfinder_v3_phase4c_full_20260827_r5",
    # For tests: a folder that stands in for "/" when reading /proc and /sys.
    "EDFINDER_REPORT_ROOT": "/",
}

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
BIND = re.compile(r"^(127\.0\.0\.1|localhost|\[::1\]):[0-9]{1,5}$")

# The database settings that decide how fast it is on this machine.
DATABASE_SETTINGS = (
    "shared_buffers", "effective_cache_size", "work_mem", "maintenance_work_mem", "max_connections",
    "max_parallel_workers", "max_parallel_workers_per_gather", "random_page_cost",
    "effective_io_concurrency", "io_method", "io_workers", "jit", "jit_above_cost",
    "max_wal_size", "checkpoint_timeout", "wal_level", "wal_compression", "synchronous_commit",
    "huge_pages", "autovacuum", "track_io_timing", "log_min_duration_statement",
)

# What each bit of the Pi's "throttled" number means (vcgencmd get_throttled).
THROTTLED_NOW = {0: "the power supply is too weak", 1: "the processor's speed is capped",
                 2: "the processor is slowed down", 3: "it is near its heat limit"}
THROTTLED_SINCE_START = {16: "the power supply has been too weak", 17: "the processor's speed has been capped",
                         18: "the processor has been slowed down", 19: "it has been near its heat limit"}

# One Find search, as the site's own page asks it: the best 50 within 250 ly of Sol.
FIND_SEARCH = {"reference_coords": {"x": 0, "y": 0, "z": 0}, "size": 50, "from": 0,
               "filters": {"distance": {"max": 250}}}


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
    for unit, step in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if count >= step:
            return "%.1f %s" % (count / step, unit)
    return "%d bytes" % count


class Report:
    """The page being written, and what stood out while writing it."""

    def __init__(self) -> None:
        self.lines: List[str] = []
        self.notes: List[str] = []

    def title(self, text: str) -> None:
        self.lines += ["", text.upper()]

    def row(self, label: str, value: object) -> None:
        # At least two spaces after the label, however long it is.
        self.lines.append("  %-24s  %s" % (label, value))

    def note(self, text: str) -> None:
        self.notes.append(text)

    def text(self) -> str:
        end = ["", "WORTH A LOOK"] + ["  - " + note for note in self.notes] if self.notes \
            else ["", "Nothing stands out."]
        return "\n".join(self.lines + end) + "\n"


class Machine:
    """Where this tool reads from. Tests point it at stand-ins."""

    def __init__(self, environ: Optional[Dict[str, str]] = None,
                 conf: Optional[Dict[str, str]] = None) -> None:
        environ = dict(os.environ if environ is None else environ)
        conf = read_conf(HERE / "deploy.conf") if conf is None else conf
        values = {key: environ.get(key, conf.get(key, default)) for key, default in DEFAULTS.items()}
        self.root = Path(values["EDFINDER_REPORT_ROOT"])
        self.bind = values["EDFINDER_LIVE_BIND"].strip()
        self.folders = [("the database", values["EDFINDER_BACKUP_DATA_DIR"].strip()),
                        ("the backups", values["EDFINDER_BACKUP_DIR"].strip())]
        self.db_container = values["EDFINDER_DB_CONTAINER"]
        self.db_user = values["EDFINDER_DB_USER"]
        self.db_name = values["EDFINDER_DB_NAME"]
        self.problems: List[str] = []
        for key, value in (("EDFINDER_DB_CONTAINER", self.db_container), ("EDFINDER_DB_USER", self.db_user),
                           ("EDFINDER_DB_NAME", self.db_name)):
            if not NAME.match(value):
                self.problems.append("%s: %r is not a plain name" % (key, value))
        if not BIND.match(self.bind):
            self.problems.append("EDFINDER_LIVE_BIND: %r must look like 127.0.0.1:58080 "
                                 "(this tool only asks this machine)" % self.bind)

    def read(self, path: str) -> Optional[str]:
        """A file under /proc or /sys, or None where it cannot be read."""
        try:
            return (self.root / path.lstrip("/")).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def listing(self, path: str) -> List[Path]:
        try:
            return sorted((self.root / path.lstrip("/")).iterdir())
        except OSError:
            return []


def run(command: Sequence[str], timeout: float = 60, errors_too: bool = True) -> Tuple[Optional[int], str]:
    """A program's exit code and what it said; (None, why) if it could not run.
    errors_too: what it said on its error stream as well (a database's log
    comes out there); without it, only its answer."""
    try:
        done = subprocess.run(list(command), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT if errors_too else subprocess.DEVNULL,
                              text=True, errors="replace", timeout=timeout)
    except FileNotFoundError:
        return None, "%s is not installed here" % command[0]
    except subprocess.TimeoutExpired:
        return None, "%s gave no answer in %d s" % (command[0], timeout)
    except OSError as error:
        return None, "%s could not be run (%s)" % (command[0], error.strerror or error)
    return done.returncode, done.stdout


def last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:200] if lines else "no reason given"


# -- the machine ---------------------------------------------------------------


def meminfo(text: Optional[str]) -> Dict[str, int]:
    """/proc/meminfo as bytes by name."""
    found: Dict[str, int] = {}
    for line in (text or "").splitlines():
        match = re.match(r"^(\w+):\s+(\d+) kB", line)
        if match:
            found[match.group(1)] = int(match.group(2)) * 1024
    return found


def the_machine(machine: Machine, report: Report) -> None:
    report.title("The machine")
    model = (machine.read("/proc/device-tree/model") or "").strip("\x00\n ")
    report.row("Model", model or "not a Raspberry Pi, or it does not say")
    report.row("System", "Linux %s, %s" % (platform.release(), platform.machine()))
    try:
        report.row("Memory page", "%d kB" % (os.sysconf("SC_PAGE_SIZE") // 1024))
    except (ValueError, OSError):
        pass
    memory = meminfo(machine.read("/proc/meminfo"))
    if memory.get("MemTotal"):
        report.row("Memory", "%s, of which %s is free for use" % (
            size_text(memory["MemTotal"]), size_text(memory.get("MemAvailable", 0))))
        swap = memory.get("SwapTotal", 0)
        used = swap - memory.get("SwapFree", 0)
        report.row("Swap", "%s, %s in use" % (size_text(swap), size_text(used)) if swap else "none")
        if swap and used > 0.25 * swap and used > 256e6:
            report.note("%s of swap is in use: memory has run short at some point." % size_text(used))
    load = (machine.read("/proc/loadavg") or "").split()
    cores = os.cpu_count() or 1
    if len(load) >= 3:
        report.row("Busy", "%s over the last minute, %s over 15 minutes, on %d cores "
                           "(%d would be every core busy)" % (load[0], load[2], cores, cores))
    uptime = (machine.read("/proc/uptime") or "").split()
    if uptime:
        seconds = float(uptime[0])
        report.row("Running for", "%d days %d h" % (seconds // 86400, seconds % 86400 // 3600))


def throttled_in_words(said: str) -> Tuple[Optional[int], List[str], List[str]]:
    """vcgencmd's "throttled=0x50005" as (the number, what is so now, what has been so)."""
    match = re.search(r"throttled=(0x[0-9a-fA-F]+)", said)
    if not match:
        return None, [], []
    number = int(match.group(1), 16)
    now = [words for bit, words in THROTTLED_NOW.items() if number & (1 << bit)]
    since = [words for bit, words in THROTTLED_SINCE_START.items() if number & (1 << bit)]
    return number, now, since


def heat_and_power(machine: Machine, report: Report) -> None:
    report.title("Heat and power")
    raw = (machine.read("/sys/class/thermal/thermal_zone0/temp") or "").strip()
    if raw.lstrip("-").isdigit():
        degrees = int(raw) / 1000
        report.row("Temperature", "%.1f C (a Pi 5 slows itself down from 80 C)" % degrees)
        if degrees >= 75:
            report.note("The processor is at %.0f C, close to where it slows itself down (80 C)." % degrees)
    else:
        report.row("Temperature", "not readable here")
    code, said = run(["vcgencmd", "get_throttled"], timeout=10)
    number, now, since = throttled_in_words(said) if code == 0 else (None, [], [])
    if number is None:
        report.row("Slowed down", "not readable here (%s)" % last_line(said))
    elif number == 0:
        report.row("Slowed down", "no, and not since it was started")
    else:
        report.row("Slowed down", "now: %s; since it was started: %s" % (
            ", ".join(now) or "no", ", ".join(since) or "no"))
        report.note("The Pi reports: %s." % "; ".join(now + since))
    governor = (machine.read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor") or "").strip()
    speed = (machine.read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq") or "").strip()
    top = (machine.read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq") or "").strip()
    if governor:
        at = " (now %.1f GHz of %.1f)" % (int(speed) / 1e6, int(top) / 1e6) if speed.isdigit() and top.isdigit() else ""
        report.row("Processor speed rule", governor + at)


# -- drives ----------------------------------------------------------------------


def mounts(text: Optional[str]) -> List[Tuple[str, str, str, List[str]]]:
    """/proc/mounts as (device, where, kind, options)."""
    found = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 4:
            found.append((parts[0], parts[1].replace("\\040", " "), parts[2], parts[3].split(",")))
    return found


def mount_of(folder: str, table: List[Tuple[str, str, str, List[str]]]) -> Optional[Tuple[str, str, str, List[str]]]:
    """The mount a folder is on: the longest mount point that is the folder or above it."""
    best = None
    for entry in table:
        where = entry[1].rstrip("/") or "/"
        if folder == where or folder.startswith(where.rstrip("/") + "/") or where == "/":
            if best is None or len(where) >= len(best[1].rstrip("/") or "/"):
                best = entry
    return best


def drives(machine: Machine, report: Report) -> None:
    report.title("Drives")
    table = mounts(machine.read("/proc/mounts"))
    seen = set()
    for what, folder in [("the system", "/")] + machine.folders:
        if not folder:
            continue
        entry = mount_of(folder, table)
        if entry is None:
            report.row(folder, "not found among the mounted drives")
            continue
        device, where, kind, options = entry
        label = "%s (%s)" % (folder, what)
        if where in seen:
            report.row(label, "on the same drive as %s" % where)
            continue
        seen.add(where)
        try:
            usage = shutil.disk_usage(folder)
            room = "%s, %s free" % (size_text(usage.total), size_text(usage.free))
            if usage.free < 0.1 * usage.total:
                report.note("%s has under a tenth of its room left (%s free)." % (folder, size_text(usage.free)))
        except OSError:
            room = "room not readable"
        times = "noatime" if "noatime" in options else "relatime" if "relatime" in options else "atime"
        report.row(label, "%s, %s, %s; %s" % (os.path.basename(device), kind, room, times))
    controllers = [path for path in machine.listing("/sys/class/nvme") if path.name.startswith("nvme")]
    for controller in controllers:
        relative = "/sys/class/nvme/%s" % controller.name
        model = (machine.read(relative + "/model") or "").strip()
        speed = (machine.read(relative + "/device/current_link_speed") or "").strip()
        most = (machine.read(relative + "/device/max_link_speed") or "").strip()
        width = (machine.read(relative + "/device/current_link_width") or "").strip()
        holds = sorted({where for device, where, _, _ in table
                        if os.path.basename(device).startswith(controller.name + "n")})
        report.row(controller.name, "%s; link %s%s%s; holds %s" % (
            model or "model not readable", speed or "speed not readable",
            " x%s" % width if width else "", " (the drive can do %s)" % most if most and most != speed else "",
            ", ".join(holds) or "nothing mounted"))
    if not controllers:
        report.row("NVMe drives", "none found here")


# -- memory limits -----------------------------------------------------------------


def memory_limits(machine: Machine, report: Report) -> None:
    report.title("Memory limits on the containers")
    command_line = (machine.read("/proc/cmdline") or "").split()
    tokens = [token for token in command_line if token.startswith(("cgroup", "swapaccount"))]
    controllers = machine.read("/sys/fs/cgroup/cgroup.controllers")
    report.row("Start-up settings", " ".join(tokens) or "none about this")
    if controllers is None:
        report.row("Kept", "cannot tell here")
        return
    kept = "memory" in controllers.split()
    report.row("Kept", "yes" if kept else "NO: the memory limits in the compose files are not being kept")
    if not kept:
        report.note("Memory limits on the containers are not being kept: this system starts with memory "
                    "accounting switched off. Nothing is wrong today; a container that ran away would "
                    "not be stopped at its limit.")


# -- containers --------------------------------------------------------------------


def containers(machine: Machine, report: Report) -> bool:
    """List the containers; False if docker cannot be asked at all."""
    report.title("Containers")
    code, said = run(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"], timeout=30)
    if code != 0:
        report.row("Docker", "cannot be asked (%s)" % last_line(said))
        return False
    status = dict(line.split("\t", 1) for line in said.splitlines() if "\t" in line)
    code, said = run(["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"],
                     timeout=60)
    using = {}
    if code == 0:
        for line in said.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                using[parts[0]] = "processor %s, memory %s" % (parts[1], parts[2])
    for name in sorted(status):
        report.row(name, "%s; %s" % (status[name], using.get(name, "use not readable")))
        if "unhealthy" in status[name] or "Restarting" in status[name]:
            report.note("The container %s says: %s." % (name, status[name]))
    if not status:
        report.row("Running", "none")
    code, said = run(["docker", "info", "--format", "{{range .Warnings}}{{println .}}{{end}}"], timeout=30)
    if code == 0:
        warnings = [line.strip() for line in said.splitlines() if line.strip().startswith("WARNING")]
        report.row("Docker's own warnings", "; ".join(warnings) or "none")
    else:
        report.row("Docker's own warnings", "could not be read (%s)" % last_line(said))
    return True


# -- the database --------------------------------------------------------------------


def ask(machine: Machine, sql: str) -> Optional[List[List[str]]]:
    """Rows from the database, by psql inside its container; None if it did not answer."""
    code, said = run(["docker", "exec", machine.db_container, "psql", "-U", machine.db_user, "-d", machine.db_name,
                      "-X", "-At", "-F", "\t", "-v", "ON_ERROR_STOP=1", "-c", sql], timeout=60, errors_too=False)
    if code != 0:
        return None
    return [line.split("\t") for line in said.splitlines() if line]


def durations(log: str) -> List[float]:
    """How long each statement the database logged as slow took, in ms. Only
    the times are taken; the statements themselves are never kept or shown."""
    return [float(found) for found in re.findall(r"duration: ([0-9]+(?:\.[0-9]+)?) ms", log)]


def the_database(machine: Machine, report: Report) -> None:
    report.title("The database")
    version = ask(machine, "SHOW server_version")
    if not version:
        report.row("PostgreSQL", "did not answer in the container %s" % machine.db_container)
        report.note("The database did not answer this tool's questions.")
        return
    report.row("PostgreSQL", version[0][0])
    size = ask(machine, "SELECT pg_database_size(current_database())")
    if size:
        report.row("Size on disk", size_text(float(size[0][0])))
    wanted = ", ".join("'%s'" % name for name in DATABASE_SETTINGS)
    settings = ask(machine, "SELECT name, current_setting(name) FROM pg_settings WHERE name IN (%s) ORDER BY name"
                   % wanted) or []
    for name, value in settings:
        report.row(name, value)
    as_set = dict((name, value) for name, value in settings)
    able = ask(machine, "SELECT pg_jit_available()")
    if able and able[0][0] == "t" and as_set.get("jit") == "on":
        report.note("jit is on: for a costly query the database first builds a faster program for it, which "
                    "on a small processor can take longer than it saves. Worth timing Find with it off.")
    busy = ask(machine, "SELECT count(*), count(*) FILTER (WHERE state = 'active') FROM pg_stat_activity "
                        "WHERE backend_type = 'client backend'")
    if busy:
        report.row("Connections", "%s open, %s at work" % (busy[0][0], busy[0][1]))
    memory = ask(machine, "SELECT blks_hit, blks_read, temp_files, temp_bytes, deadlocks, "
                          "to_char(stats_reset AT TIME ZONE 'UTC', 'YYYY-MM-DD') "
                          "FROM pg_stat_database WHERE datname = current_database()")
    if memory:
        hit, read, temp_files, temp_bytes, deadlocks, since = memory[0]
        total = float(hit) + float(read)
        if total > 0:
            share = 100 * float(hit) / total
            report.row("Found in its own memory", "%.1f%% of what it read%s (the rest came from the system's "
                                                   "memory or the drive)" % (share, " since " + since if since else ""))
        report.row("Sorts too big for memory", "%s, %s written to the drive for them" % (
            temp_files, size_text(float(temp_bytes))))
        if int(deadlocks):
            report.note("%s deadlocks since the counts began." % deadlocks)
    checkpoints = ask(machine, "SELECT num_timed, num_requested FROM pg_stat_checkpointer")
    if checkpoints:
        timed, requested = int(checkpoints[0][0]), int(checkpoints[0][1])
        report.row("Checkpoints", "%d on the clock, %d forced early" % (timed, requested))
        if requested > timed and requested > 20:
            report.note("More checkpoints were forced early (%d) than came on the clock (%d): big writes "
                        "outrun max_wal_size." % (requested, timed))
    dead = ask(machine, "SELECT schemaname || '.' || relname, n_live_tup, n_dead_tup, "
                        "coalesce(to_char(greatest(last_autovacuum, last_vacuum) AT TIME ZONE 'UTC', "
                        "'YYYY-MM-DD HH24:MI'), 'never') FROM pg_stat_user_tables "
                        "WHERE n_dead_tup > 0 ORDER BY n_dead_tup DESC LIMIT 5")
    for name, live, gone, cleaned in dead or []:
        report.row("Rows to clean up", "%s: %s of %s, last cleaned %s" % (name, gone, live, cleaned))
        if int(gone) > 1_000_000 and int(gone) > 0.2 * max(int(live), 1):
            report.note("%s has %s rows waiting to be cleaned up, over a fifth of it." % (name, gone))
    code, said = run(["docker", "logs", "--since", "24h", machine.db_container], timeout=60)
    if code == 0:
        slow = sorted(durations(said), reverse=True)
        longest = ", ".join("%.1f s" % (found / 1000) for found in slow[:5])
        report.row("Slow statements, 24 h", "%d%s" % (len(slow), " (the longest: %s)" % longest if slow else ""))
    else:
        report.row("Slow statements, 24 h", "the log could not be read (%s)" % last_line(said))


# -- the site ----------------------------------------------------------------------


def timed(url: str, body: Optional[bytes] = None) -> Tuple[Optional[int], float, int]:
    """One request to this machine: (status or None, seconds, bytes read)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, data=body, headers={"content-type": "application/json"} if body else {})
    began = time.monotonic()
    try:
        with opener.open(request, timeout=60) as response:
            return response.status, time.monotonic() - began, len(response.read())
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, time.monotonic() - began, 0
    except (OSError, ValueError):
        return None, time.monotonic() - began, 0


def the_site(machine: Machine, report: Report) -> None:
    report.title("The site, asked from this machine")
    base = "http://%s" % machine.bind
    for label, path, body in (("Health check", "/api/health", None),
                              ("Find, 250 ly of Sol", "/api/local/search", json.dumps(FIND_SEARCH).encode())):
        results = [timed(base + path, body) for _ in range(3)]
        statuses = {status for status, _, _ in results}
        if statuses == {None}:
            report.row(label, "no answer at %s" % machine.bind)
            report.note("The site did not answer at %s (%s)." % (machine.bind, label))
            continue
        times = ", ".join("%.2f s" % seconds for _, seconds, _ in results)
        report.row(label, "%s; answers %s; %s" % (
            times, ", ".join(str(status) for status in sorted(statuses, key=str)), size_text(results[-1][2])))
        if statuses != {200}:
            report.note("%s did not answer 200 every time (%s)." % (label, ", ".join(map(str, statuses))))
        elif body and min(seconds for _, seconds, _ in results) > 2:
            report.note("A Find search takes over 2 s even when asked again straight away.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        print(__doc__.strip() if arguments[0] in ("-h", "--help", "help") else
              "This tool takes no arguments. Run: sudo python3 pi_report.py")
        return 0 if arguments[0] in ("-h", "--help", "help") else 2
    machine = Machine()
    report = Report()
    report.lines.append("ED-Finder: how this machine is doing. Nothing is changed by this tool.")
    report.lines.append("Taken %s UTC" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    for problem in machine.problems:
        report.note(problem)
    the_machine(machine, report)
    heat_and_power(machine, report)
    drives(machine, report)
    memory_limits(machine, report)
    names_ok = not any("EDFINDER_DB" in problem for problem in machine.problems)
    if containers(machine, report) and names_ok:
        the_database(machine, report)
    if not any("EDFINDER_LIVE_BIND" in problem for problem in machine.problems):
        the_site(machine, report)
    sys.stdout.write(report.text())
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
