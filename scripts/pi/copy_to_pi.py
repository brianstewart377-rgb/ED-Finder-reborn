#!/usr/bin/env python3
"""Copy the database from the current server to the Raspberry Pi.

Runs on the Pi, as the user ``pi``, with PostgreSQL started from
deploy/pi/postgres.yml (docs/pi-move.md, section 4). It reaches the server with
the Pi's key for the move (~/.ssh/edf_move).

    python3 copy_to_pi.py check      say what a copy would do, and whether both
                                     machines are ready. Changes nothing.
    python3 copy_to_pi.py copy       copy, or carry on with a copy that
                                     stopped part-way
    python3 copy_to_pi.py validate   check the rows against the links and rules
                                     that the copy added unchecked. Can run
                                     while the app is in use, and can be
                                     stopped and run again.
    python3 copy_to_pi.py tidy       remove the list of systems in reach that
                                     the copy keeps on the server while it runs
                                     (a finished copy removes it itself)

What moves (docs/pi-move.md, "Every table on the current server"): every table
whole, except

  * the five big score tables, cut to the systems within 500 ly of an
    inhabited system;
  * body_parent_evidence, which is made but left empty;
  * the schemas r1_meta and r1_plan, which are left behind.

Each body's class is filled in on the way: its own if it has one, else the one
in its score row on the server, else its plain type for the two kinds of body
that are never scored. So the waiting list for new scores must be empty: a
waiting system's score rows may be out of date.

How it goes:

  1. On the server, the systems within 500 ly are worked out once and kept in a
     table of their own (schema edfinder_copy) until the copy has finished.
     Nothing else on the server is changed.
  2. One session on the server holds a snapshot of the database, and every
     table is read through it, so all of them are copied as they were at one
     moment.
  3. The Pi's database is made afresh with the server's structure, without
     indexes, links between tables, or the rules on columns, so loading is
     quick.
  4. The tables are copied several at a time, each straight from the server's
     database into the Pi's, compressed by ssh on the way. A table is kept only
     if all of it arrived, and its rows are counted on both machines.
  5. Indexes, keys and triggers are built. The links between tables and the
     rules on columns are added without checking the rows already there: they
     were checked on the server. `validate` checks them on the Pi later.
  6. Statistics for the query planner.

A copy that stops part-way (a dropped connection, say) carries on where it left
off when `copy` is run again; tables already copied are kept. That is only
right if the server's data has not changed in between, so the copy will not
start while the server's nightly galaxy update or state refresh is switched on,
and stops if either has run since it began.

Settings come from the environment (EDFINDER_COPY_*); the defaults are the real
machines. Standard library only; needs Python 3.9 or newer.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

DEFAULTS: Dict[str, str] = {
    # The current server, reached with the Pi's key for the move.
    "EDFINDER_COPY_SERVER": "root@nb79a3d.mevnode.com",
    "EDFINDER_COPY_KEY": "~/.ssh/edf_move",
    # The database containers and databases: the same names on both machines.
    "EDFINDER_COPY_SERVER_CONTAINER": "edfinder-v3-phase4c-full-20260827_r5-postgres",
    "EDFINDER_COPY_PI_CONTAINER": "edfinder-v3-phase4c-full-20260827_r5-postgres",
    "EDFINDER_COPY_SERVER_DATABASE": "edfinder_v3_phase4c_full_20260827_r5",
    "EDFINDER_COPY_PI_DATABASE": "edfinder_v3_phase4c_full_20260827_r5",
    "EDFINDER_COPY_USER": "edfinder_v3",
    # Where the copy keeps its own files on the Pi (on the 2 TB drive).
    "EDFINDER_COPY_DIR": "/srv/data/move",
    # How many tables are copied, and indexes built, at the same time.
    "EDFINDER_COPY_JOBS": "4",
    # Below these the list of systems in reach is surely wrong (for example the
    # state refresh has never run), and the copy stops rather than cut the
    # scores down to nearly nothing.
    "EDFINDER_COPY_MIN_INHABITED": "10000",
    "EDFINDER_COPY_MIN_REACH": "1000000",
    # Seconds to wait before trying a table a second time.
    "EDFINDER_COPY_RETRY_PAUSE": "30",
    # Room the Pi must have beyond the tables' size on the server: 10% more,
    # and this many GB besides.
    "EDFINDER_COPY_SPARE_GB": "20",
}

REACH_LY = 500
CUT_TABLES = ("economy_opportunity", "system_search", "body_mechanics",
              "system_rating_vector", "system_archetype")
EMPTY_TABLE = "body_parent_evidence"
LEFT_BEHIND = ("r1_meta", "r1_plan")
SCRATCH = "edfinder_copy"
# What scripts/pi/time_table_copy.sh leaves on the Pi: a copy may replace it.
TRIAL_TABLE = "v3_gen_phase4c_full_20260827_r5.systems"
SERVER_TIMERS = ("edfinder-galaxy-update.timer", "edfinder-state-refresh.timer")
SERVER_JOBS = ("edfinder-galaxy-update.service", "edfinder-state-refresh.service")
UPDATE_CONTAINER = "edfinder-galaxy-update"
# The two kinds of body that are never scored: their class is their plain type,
# as in apps/importer/src/spansh_delta/apply.py (UNSCORED_BODY_TYPES).
UNSCORED_BODY_TYPES = ("barycentre", "belt_cluster")
# Settings whose value is a list, written without quotes (as pg_dump does).
LIST_SETTINGS = ("search_path", "temp_tablespaces", "session_preload_libraries",
                 "local_preload_libraries", "shared_preload_libraries")
# Parts of the structure built side by side once the rows are in.
BUILT_TOGETHER = ("INDEX", "CONSTRAINT", "CHECK CONSTRAINT")

STATE_FILE = "copy-state.json"
SCHEMA_FILE = "copy-schema.dump"
ROLES_FILE = "copy-roles.sql"
SEP = "\x1f"
LIST_SEP = "\x1e"
PROGRESS_EVERY = 300.0
SPARE_SHARE = 0.1

_print_lock = threading.Lock()


class CopyError(Exception):
    """A plain message for the person running the copy."""


def say(message: str) -> None:
    with _print_lock:
        print("%s  %s" % (time.strftime("%H:%M:%S"), message), flush=True)


def ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def gigabytes(size: float) -> str:
    return "%s GB" % format(round(size / 1e9, 1), ",")


def number(value: int) -> str:
    return format(value, ",")


def took(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return "%d s" % seconds
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return "%d min %d s" % (minutes, seconds)
    hours, minutes = divmod(minutes, 60)
    return "%d h %d min" % (hours, minutes)


def first_lines(text: Any, count: int = 3) -> str:
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return " / ".join(lines[:count]) or "(no message)"


def rows_of(output: bytes) -> List[List[str]]:
    """psql's unaligned rows. Split on newlines only: str.splitlines would also
    split on the separator used inside lists."""
    return [line.split(SEP) for line in output.decode("utf-8").split("\n") if line]


class Settings:
    def __init__(self, environ: Optional[Dict[str, str]] = None) -> None:
        source = os.environ if environ is None else environ
        values = {key: source.get(key, default) for key, default in DEFAULTS.items()}
        self.server = values["EDFINDER_COPY_SERVER"]
        self.key = os.path.expanduser(values["EDFINDER_COPY_KEY"])
        self.server_container = values["EDFINDER_COPY_SERVER_CONTAINER"]
        self.pi_container = values["EDFINDER_COPY_PI_CONTAINER"]
        self.server_database = values["EDFINDER_COPY_SERVER_DATABASE"]
        self.pi_database = values["EDFINDER_COPY_PI_DATABASE"]
        self.user = values["EDFINDER_COPY_USER"]
        self.directory = Path(values["EDFINDER_COPY_DIR"])
        try:
            self.jobs = max(1, int(values["EDFINDER_COPY_JOBS"]))
            self.min_inhabited = int(values["EDFINDER_COPY_MIN_INHABITED"])
            self.min_reach = int(values["EDFINDER_COPY_MIN_REACH"])
            self.retry_pause = float(values["EDFINDER_COPY_RETRY_PAUSE"])
            self.spare = float(values["EDFINDER_COPY_SPARE_GB"]) * 1e9
        except ValueError:
            raise CopyError("EDFINDER_COPY_JOBS, _MIN_INHABITED, _MIN_REACH, _RETRY_PAUSE and "
                            "_SPARE_GB must be numbers") from None


# -- running things ----------------------------------------------------------


class Machines:
    """Commands on the server (over ssh) and on the Pi (here)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def ssh(self, args: Sequence[str]) -> List[str]:
        s = self.settings
        return ["ssh", "-T", "-i", s.key, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=10",
                "-o", "Compression=yes", "-o", "LogLevel=ERROR", s.server,
                " ".join(shlex.quote(arg) for arg in args)]

    def server_psql(self, *extra: str) -> List[str]:
        s = self.settings
        return self.ssh(["docker", "exec", "-i", s.server_container, "psql", "-X", "-q",
                         "-v", "ON_ERROR_STOP=1", "-U", s.user, "-d", s.server_database, *extra])

    def server_tool(self, *args: str) -> List[str]:
        return self.ssh(["docker", "exec", self.settings.server_container, *args])

    def pi_psql(self, *extra: str, database: Optional[str] = None, quiet: bool = True,
                stop_on_error: bool = True) -> List[str]:
        s = self.settings
        return ["docker", "exec", "-i", s.pi_container, "psql", "-X", *(["-q"] if quiet else []),
                *(["-v", "ON_ERROR_STOP=1"] if stop_on_error else []),
                "-U", s.user, "-d", database or s.pi_database, *extra]

    def pi_tool(self, *args: str) -> List[str]:
        return ["docker", "exec", "-i", self.settings.pi_container, *args]

    @staticmethod
    def run(command: Sequence[str], *, data: Optional[bytes] = None,
            timeout: Optional[float] = None) -> subprocess.CompletedProcess:
        try:
            if data is None:
                return subprocess.run(list(command), stdin=subprocess.DEVNULL, capture_output=True,
                                      timeout=timeout)
            return subprocess.run(list(command), input=data, capture_output=True, timeout=timeout)
        except FileNotFoundError:
            raise CopyError("%s is not installed here" % command[0]) from None
        except subprocess.TimeoutExpired:
            raise CopyError("%s did not answer within %d s" % (command[0], timeout or 0)) from None

    def server_rows(self, statement: str) -> List[List[str]]:
        result = self.run(self.server_psql("-At", "-F", SEP, "-c", "SET statement_timeout = 0",
                                           "-c", statement))
        if result.returncode != 0:
            raise CopyError("the server's database refused a query: " + first_lines(result.stderr))
        return rows_of(result.stdout)

    def server_script(self, statements: Sequence[str]) -> None:
        args: List[str] = []
        for statement in statements:
            args += ["-c", statement]
        result = self.run(self.server_psql(*args))
        if result.returncode != 0:
            raise CopyError("the server's database refused a statement: " + first_lines(result.stderr))

    def pi_rows(self, statement: str, database: Optional[str] = None) -> List[List[str]]:
        result = self.run(self.pi_psql("-At", "-F", SEP, "-c", "SET statement_timeout = 0",
                                       "-c", statement, database=database))
        if result.returncode != 0:
            raise CopyError("the Pi's database refused a query: " + first_lines(result.stderr))
        return rows_of(result.stdout)

    def pi_sql(self, sql: str, database: Optional[str] = None) -> subprocess.CompletedProcess:
        """Run a script on the Pi's database, in one session."""
        return self.run(self.pi_psql("-f", "-", database=database), data=sql.encode("utf-8"))


# -- what is where -----------------------------------------------------------


class Table:
    WHOLE, CUT, EMPTY, FILL = "whole", "cut", "empty", "fill"

    def __init__(self, schema: str, name: str, size: int, estimate: int, columns: List[str]) -> None:
        self.schema, self.name, self.size, self.estimate = schema, name, size, estimate
        self.columns = columns
        self.rule = Table.WHOLE

    @property
    def key(self) -> str:
        return "%s.%s" % (self.schema, self.name)

    @property
    def qualified(self) -> str:
        return "%s.%s" % (ident(self.schema), ident(self.name))

    def column_list(self) -> str:
        return ", ".join(ident(column) for column in self.columns)


class ServerFacts:
    """What the copy needs to know about the server's database."""

    def __init__(self, machines: Machines) -> None:
        self.machines = machines
        row = machines.server_rows("""
            SELECT (SELECT g.relation_schema FROM v3_meta.current_canonical_generation c
                      JOIN v3_meta.canonical_generation g USING (generation_id)
                     WHERE g.lifecycle_state = 'PUBLISHED' LIMIT 1),
                   (SELECT string_agg(relation_schema, '%(list)s') FROM v3_meta.canonical_generation),
                   (SELECT g.derived_generation_id::text FROM v3_meta.current_derived_generation c
                      JOIN v3_meta.derived_generation g USING (derived_generation_id)
                     WHERE g.lifecycle_state = 'PUBLISHED' LIMIT 1),
                   (SELECT count(*) FROM v3_state.rescore_queue),
                   (SELECT count(*) FROM v3_state.system_state WHERE population > 0),
                   (SELECT coalesce(max(effective_at)::text, '') FROM v3_source.source_run
                     WHERE acquisition_kind = 'DELTA' AND run_state = 'SUCCEEDED'),
                   (SELECT coalesce(max(loaded_at)::text, '') FROM v3_state.load_log),
                   (SELECT count(*) FROM pg_largeobject_metadata),
                   (SELECT n.nspname FROM pg_extension e
                      JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'cube'),
                   to_regclass('%(scratch)s.reach') IS NOT NULL,
                   pg_database_size(current_database()),
                   (SELECT string_agg(extname, '%(list)s' ORDER BY extname) FROM pg_extension),
                   (SELECT count(*) FROM pg_class WHERE reltablespace NOT IN
                       (0, (SELECT oid FROM pg_tablespace WHERE spcname = 'pg_global')))
        """ % {"list": LIST_SEP, "scratch": SCRATCH})[0]
        self.galaxy = row[0] or None
        self.galaxies = [name for name in row[1].split(LIST_SEP) if name]
        self.generation = row[2] or None
        self.waiting = int(row[3])
        self.inhabited = int(row[4])
        # Two marks that move when the server's data changes. A copy carried on
        # in a second run must find them where they were when it began.
        self.marks = {"last_galaxy_update": row[5], "last_state_refresh": row[6]}
        self.large_objects = int(row[7])
        self.cube_schema = row[8] or None
        self.has_reach = row[9] == "t"
        self.database_size = int(row[10])
        self.extensions = [name for name in row[11].split(LIST_SEP) if name]
        self.in_other_tablespaces = int(row[12])

        left_out = ", ".join(literal(name) for name in LEFT_BEHIND + (SCRATCH,))
        self.tables: List[Table] = []
        for schema, name, size, estimate, columns in machines.server_rows("""
            SELECT n.nspname, c.relname, pg_total_relation_size(c.oid),
                   greatest(c.reltuples, 0)::bigint,
                   (SELECT string_agg(a.attname, '%(list)s' ORDER BY a.attnum)
                      FROM pg_attribute a
                     WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                       AND a.attgenerated = '')
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind = 'r'
               AND n.nspname NOT IN ('pg_catalog', 'information_schema', %(left_out)s)
               AND n.nspname !~ '^pg_(toast|temp)'
               AND NOT EXISTS (SELECT 1 FROM pg_depend d
                                WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
                                  AND d.deptype = 'e')
             ORDER BY 3 DESC, 1, 2
        """ % {"list": LIST_SEP, "left_out": left_out}):
            table = Table(schema, name, int(size), int(estimate),
                          [column for column in columns.split(LIST_SEP) if column])
            if schema == "v3_derived" and name in CUT_TABLES:
                table.rule = Table.CUT
            elif name == EMPTY_TABLE and schema in self.galaxies:
                table.rule = Table.EMPTY
            elif (schema == self.galaxy and name == "bodies" and "body_class" in table.columns
                  and self.generation is not None):
                table.rule = Table.FILL
            self.tables.append(table)
        self.left_behind = [found[0] for found in machines.server_rows(
            "SELECT nspname FROM pg_namespace WHERE nspname IN (%s) ORDER BY 1"
            % ", ".join(literal(name) for name in LEFT_BEHIND))]

    def systems(self) -> int:
        for table in self.tables:
            if table.schema == self.galaxy and table.name == "systems":
                return max(1, table.estimate)
        return 1

    def reach_counts(self) -> Tuple[int, int]:
        row = self.machines.server_rows(
            "SELECT (SELECT count(*) FROM %(s)s.inhabited), (SELECT count(*) FROM %(s)s.reach)"
            % {"s": SCRATCH})[0]
        return int(row[0]), int(row[1])


def export_query(table: Table, generation: Optional[str]) -> str:
    """The query the server copies a table out with."""
    if table.rule == Table.CUT:
        return ("SELECT %s FROM ONLY %s WHERE system_id64 IN (SELECT system_id64 FROM %s.reach)"
                % (table.column_list(), table.qualified, SCRATCH))
    if table.rule == Table.FILL:
        kinds = " ".join("WHEN %s THEN %s" % (literal(kind), literal(kind)) for kind in UNSCORED_BODY_TYPES)
        columns = [("coalesce(b.body_class, m.body_class, CASE t.public_code %s END) AS body_class" % kinds)
                   if column == "body_class" else "b." + ident(column) for column in table.columns]
        return ("SELECT %s FROM ONLY %s b "
                "LEFT JOIN v3_derived.body_mechanics m ON m.derived_generation_id = %s::uuid "
                "AND m.system_id64 = b.system_id64 AND m.body_pk = b.body_pk "
                "LEFT JOIN v3_vocab.body_type t ON t.body_type_id = b.body_type_id"
                % (", ".join(columns), table.qualified, literal(generation or "")))
    return "SELECT %s FROM ONLY %s" % (table.column_list(), table.qualified)


def count_query(table: Table) -> str:
    """How many rows the export sends (the joins of a filled table add none:
    each matches at most one row)."""
    if table.rule == Table.CUT:
        return ("SELECT count(*) FROM ONLY %s WHERE system_id64 IN (SELECT system_id64 FROM %s.reach)"
                % (table.qualified, SCRATCH))
    return "SELECT count(*) FROM ONLY %s" % table.qualified


# -- the copy's own record ---------------------------------------------------


class State:
    """What a copy has done so far, so that a second run carries on from there."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.data: Dict[str, Any] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                raise CopyError("%s is damaged. Start the copy again with: copy --start-over" % path) from None

    @property
    def phase(self) -> str:
        return self.data.get("phase", "new")

    def reset(self, marks: Dict[str, str], sizes: Dict[str, int]) -> None:
        with self.lock:
            self.data = {"version": 1, "phase": "new",
                         "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "marks": marks, "sizes": sizes, "tables": {}, "post_done": [],
                         "checks": [], "foreign_keys": [], "sequences": [], "settings": []}
            self.save()

    def update(self, **changes: Any) -> None:
        with self.lock:
            self.data.update(changes)
            self.save()

    def table_done(self, key: str, rows: int) -> None:
        with self.lock:
            self.data.setdefault("tables", {})[key] = rows
            self.save()

    def post_done(self, key: str) -> None:
        with self.lock:
            self.data.setdefault("post_done", []).append(key)
            self.save()

    def add_foreign_key(self, schema: str, table: str, constraint: str) -> None:
        with self.lock:
            self.data.setdefault("foreign_keys", []).append([schema, table, constraint])
            self.save()

    def save(self) -> None:
        with self.lock:
            partial = self.path.with_name(self.path.name + ".part")
            partial.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(partial, self.path)


# -- the snapshot everything is read through ---------------------------------


class Holder:
    """A session on the server that holds the snapshot every table is read
    through, and counts each table's rows through it."""

    def __init__(self, machines: Machines) -> None:
        try:
            self.process = subprocess.Popen(
                machines.server_psql("-At", "-f", "-"), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        except FileNotFoundError:
            raise CopyError("ssh is not installed here") from None
        self.lock = threading.Lock()
        self.snapshot = self.ask(
            "SET statement_timeout = 0;\n"
            "SET idle_in_transaction_session_timeout = 0;\n"
            "SET transaction_timeout = 0;\n"
            "SET max_parallel_workers_per_gather = 4;\n"
            "SET work_mem = '1GB';\n"
            "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
            "SELECT pg_export_snapshot()")
        if not re.fullmatch(r"[0-9A-Fa-f-]+", self.snapshot):
            raise CopyError("the server gave an unexpected snapshot name: %r" % self.snapshot)

    def ask(self, statement: str) -> str:
        with self.lock:
            assert self.process.stdin is not None and self.process.stdout is not None
            try:
                self.process.stdin.write(statement + ";\n")
                self.process.stdin.flush()
                line = self.process.stdout.readline()
            except (BrokenPipeError, OSError, ValueError):
                line = ""
        if not line:
            raise CopyError("the session holding the snapshot on the server ended: "
                            + first_lines(self.close()))
        return line.strip()

    def close(self) -> str:
        """End the session, and the snapshot with it. Its error output, if any."""
        if self.process.poll() is None:
            try:
                assert self.process.stdin is not None
                self.process.stdin.write("COMMIT;\n")
                self.process.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
            try:
                self.process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        try:
            assert self.process.stderr is not None
            return self.process.stderr.read()
        except (OSError, ValueError):
            return ""


# -- pg_restore's script, cut into its parts ---------------------------------


_HEADER = re.compile(r"^-- Name: (?P<name>.*); Type: (?P<type>[A-Z ]+); Schema: (?P<schema>[^;]*); "
                     r"Owner: (?P<owner>.*)$")
_IDENT = r'(?:"(?:[^"]|"")*"|[^\s."(]+)'
_TARGET = re.compile(r"(?:ALTER TABLE ONLY |ALTER TABLE |CREATE (?:UNIQUE )?INDEX \S+ ON (?:ONLY )?)"
                     r"(?P<schema>%s)\.(?P<table>%s)" % (_IDENT, _IDENT))
_FOREIGN_KEY = re.compile(r"ADD CONSTRAINT (?P<name>%s) FOREIGN KEY" % _IDENT)
_TRAILER = re.compile(r"^(--|-- PostgreSQL database dump complete|"
                      r"SET default_(tablespace|table_access_method) = .*;)$")


def unquote(name: str) -> str:
    return name[1:-1].replace('""', '"') if name.startswith('"') else name


def split_restore_script(script: str) -> Tuple[str, List[Dict[str, str]]]:
    """pg_restore's script for a section, cut into its parts: the settings at
    the top, and each part with its type, name, table and statement."""
    lines = [line for line in script.splitlines()
             if not line.startswith("\\restrict") and not line.startswith("\\unrestrict")]
    starts = [index for index, line in enumerate(lines) if _HEADER.match(line)]
    prologue = "\n".join(line for line in lines[:(starts[0] - 1) if starts else len(lines)]
                         if line.strip() and not line.startswith("--"))
    entries = []
    for position, start in enumerate(starts):
        header = _HEADER.match(lines[start])
        assert header is not None
        end = starts[position + 1] - 1 if position + 1 < len(starts) else len(lines)
        # Taken as it stands, since a comment's text can hold blank lines or
        # lines that begin "--". Only what pg_restore puts between parts is
        # left off the end: blank lines, its closing remark, and the setting it
        # makes for the next part (which the settings at the top cover).
        body_lines = lines[start + 2:end]
        while body_lines and (not body_lines[-1].strip() or _TRAILER.match(body_lines[-1])):
            body_lines.pop()
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        target = _TARGET.search(body)
        foreign_key = _FOREIGN_KEY.search(body)
        entries.append({
            "key": "%d %s %s %s" % (position, header.group("type"), header.group("schema"),
                                    header.group("name")),
            "type": header.group("type"), "name": header.group("name"), "sql": body,
            "schema": unquote(target.group("schema")) if target else "",
            "table": unquote(target.group("table")) if target else "",
            "constraint": unquote(foreign_key.group("name")) if foreign_key else "",
        })
    return prologue, entries


def not_valid(statement: str) -> Tuple[str, bool]:
    """A link between tables, added without checking the rows already there.
    True if the server had checked it, so that `validate` checks it here."""
    text = statement.rstrip()
    if re.search(r"\bNOT VALID\s*;$", text):
        return text, False
    if not text.endswith(";"):
        raise CopyError("unexpected statement for a link between tables: %s" % text[:200])
    return text[:-1] + " NOT VALID;", True


# -- the copy ----------------------------------------------------------------


class Copier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.machines = Machines(settings)
        self.directory = settings.directory
        self.state = State(self.directory / STATE_FILE)
        self.reach_share = 37.0 / 198.5

    # .. readiness ...........................................................

    def pi_problems(self) -> Tuple[List[str], Dict[str, Any]]:
        """What stops a copy on the Pi, and what was found there."""
        s, machines = self.settings, self.machines
        problems: List[str] = []
        found: Dict[str, Any] = {"tables": [], "size": 0, "others": 0, "free": None, "extensions": None}
        if machines.run(["docker", "info"]).returncode != 0:
            problems.append("This user cannot use Docker without sudo. Run: sudo usermod -aG docker "
                            "%s   then log out and in again." % os.environ.get("USER", "pi"))
            return problems, found
        result = machines.run(["docker", "inspect", "--format", "{{.State.Running}}", s.pi_container])
        if result.stdout.decode().strip() != "true":
            problems.append("PostgreSQL is not running on the Pi (container %s). Start it from "
                            "deploy/pi/postgres.yml." % s.pi_container)
            return problems, found
        if not self.directory.is_dir():
            problems.append("%s does not exist. Is the 2 TB drive mounted?" % self.directory)
        elif not os.access(str(self.directory), os.W_OK):
            problems.append("This user cannot write to %s." % self.directory)
        else:
            found["free"] = shutil.disk_usage(str(self.directory)).free
        found["extensions"] = {row[0] for row in machines.pi_rows(
            "SELECT name FROM pg_available_extensions", database="postgres")}
        if machines.pi_rows("SELECT 1 FROM pg_database WHERE datname = %s" % literal(s.pi_database),
                            database="postgres"):
            found["tables"] = [row[0] for row in machines.pi_rows(
                "SELECT n.nspname || '.' || c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p') "
                "AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname !~ '^pg_' "
                "ORDER BY 1")]
            found["size"] = int(machines.pi_rows("SELECT pg_database_size(current_database())")[0][0])
            found["others"] = int(machines.pi_rows(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()"
                % literal(s.pi_database), database="postgres")[0][0])
        return problems, found

    def server_problems(self, *, data_must_stay: bool = True) -> Tuple[List[str], Optional[ServerFacts]]:
        """What stops a copy on the server, and the server's facts."""
        s, machines = self.settings, self.machines
        problems: List[str] = []
        result = machines.run(machines.ssh(["true"]), timeout=120)
        if result.returncode != 0:
            problems.append("The server cannot be reached with the Pi's key (%s): %s"
                            % (s.key, first_lines(result.stderr)))
            return problems, None
        result = machines.run(machines.ssh(["docker", "inspect", "--format", "{{.State.Running}}",
                                            s.server_container]))
        if result.stdout.decode().strip() != "true":
            problems.append("The server's database container (%s) is not running." % s.server_container)
            return problems, None
        if data_must_stay:
            result = machines.run(machines.ssh(["systemctl", "is-active", *SERVER_TIMERS, *SERVER_JOBS]))
            states = result.stdout.decode().split()
            if len(states) != len(SERVER_TIMERS) + len(SERVER_JOBS):
                problems.append("Could not tell whether the server's nightly jobs are switched off: "
                                + first_lines(result.stderr or result.stdout))
            else:
                switched_on = [unit for position, unit in enumerate(SERVER_TIMERS)
                               if states[position] == "active"]
                if switched_on:
                    problems.append("On the server, the nightly %s still switched on. On the server, "
                                    "run: systemctl stop %s" % (
                                        "jobs are" if len(switched_on) > 1 else "job is",
                                        " ".join(switched_on)))
                running = [unit for position, unit in enumerate(SERVER_JOBS)
                           if states[len(SERVER_TIMERS) + position] in ("active", "activating", "reloading")]
                if running:
                    problems.append("On the server, %s is running now. Wait until it has finished."
                                    % " and ".join(running))
            result = machines.run(machines.ssh(["docker", "inspect", "--format", "{{.State.Status}}",
                                                UPDATE_CONTAINER]))
            if result.returncode == 0:
                problems.append("On the server, a galaxy update is running, or one was left behind "
                                "(container %s)." % UPDATE_CONTAINER)
        facts = ServerFacts(machines)
        if facts.galaxy is None:
            problems.append("The server's database has no published galaxy.")
        if facts.cube_schema is None:
            problems.append("The server's database lacks the cube extension, which the distances need.")
        if facts.in_other_tablespaces:
            problems.append("The server's database keeps %d tables or indexes outside its own folder "
                            "(other tablespaces), which this copy does not carry." % facts.in_other_tablespaces)
        if facts.large_objects:
            problems.append("The server's database holds %d large objects, which this copy does not "
                            "carry." % facts.large_objects)
        if facts.waiting and data_must_stay:
            problems.append("%s systems are waiting for new scores on the server, so their score rows "
                            "cannot be trusted for their bodies' classes. On the server, run: "
                            "python3 /opt/edfinder/galaxy_update.py score" % number(facts.waiting))
        if facts.inhabited < s.min_inhabited:
            problems.append("The server knows only %s inhabited systems; at least %s were expected. "
                            "Has the state refresh run?" % (number(facts.inhabited),
                                                            number(s.min_inhabited)))
        return problems, facts

    @staticmethod
    def both_problems(found: Dict[str, Any], facts: Optional[ServerFacts]) -> List[str]:
        """What stops a copy that needs both machines to be seen together."""
        if facts is None or found.get("extensions") is None:
            return []
        missing = sorted(set(facts.extensions) - found["extensions"])
        if missing:
            return ["The Pi's PostgreSQL lacks extensions the server's database uses: %s."
                    % ", ".join(missing)]
        return []

    def space_needed(self, facts: ServerFacts) -> int:
        total = sum(t.size * (self.reach_share if t.rule == Table.CUT else 1)
                    for t in facts.tables if t.rule != Table.EMPTY)
        return int(total * (1 + SPARE_SHARE) + self.settings.spare)

    @staticmethod
    def replaceable(found: Dict[str, Any]) -> bool:
        """The Pi's database holds nothing a copy would lose: no tables, or only
        what the trial copy (scripts/pi/time_table_copy.sh) left."""
        return set(found["tables"]) <= {TRIAL_TABLE}

    def plan_lines(self, facts: ServerFacts) -> List[str]:
        groups: Dict[str, List[Table]] = {}
        for table in facts.tables:
            groups.setdefault(table.rule, []).append(table)
        whole = groups.get(Table.WHOLE, []) + groups.get(Table.FILL, [])
        cut, empty = groups.get(Table.CUT, []), groups.get(Table.EMPTY, [])
        lines = ["%d tables whole (%s on the server)." % (len(whole), gigabytes(sum(t.size for t in whole)))]
        if cut:
            lines.append("%d score tables cut to the systems within %d ly (%s on the server, about %s of it)."
                         % (len(cut), REACH_LY, gigabytes(sum(t.size for t in cut)),
                            gigabytes(self.reach_share * sum(t.size for t in cut))))
        if empty:
            lines.append("Made but left empty: %s (%s on the server)." % (
                ", ".join(t.name for t in empty), gigabytes(sum(t.size for t in empty))))
        if facts.left_behind:
            lines.append("Left behind: %s." % ", ".join(facts.left_behind))
        if any(t.rule == Table.FILL for t in facts.tables):
            lines.append("Each body's class filled in on the way.")
        return lines

    def check(self) -> int:
        say("Checking the Pi ...")
        problems, found = self.pi_problems()
        if found["free"] is not None:
            say("  %s free in %s." % (gigabytes(found["free"]), self.directory))
        if found["tables"]:
            say("  Its database holds %d tables (%s)." % (len(found["tables"]), gigabytes(found["size"])))
        phase = self.state.phase
        if phase == "done":
            say("  A copy has finished already (begun %s)." % self.state.data.get("started", "?"))
        elif phase != "new":
            say("  A copy begun %s stopped part-way, with %d tables copied. `copy` carries on with it."
                % (self.state.data.get("started", "?"), len(self.state.data.get("tables", {}))))
        say("Checking the server ...")
        server_problems, facts = self.server_problems(data_must_stay=phase in ("new", "data"))
        problems += server_problems + self.both_problems(found, facts)
        if facts is not None:
            say("  Its database: %s in %d tables." % (gigabytes(facts.database_size), len(facts.tables)))
            say("  Inhabited systems: %s. Waiting for new scores: %s." % (
                number(facts.inhabited), number(facts.waiting)))
            if facts.has_reach:
                _, reach = facts.reach_counts()
                self.reach_share = reach / facts.systems()
                say("  Systems within %d ly, worked out by an earlier copy: %s." % (REACH_LY, number(reach)))
            say("What a copy would take:")
            for line in self.plan_lines(facts):
                say("  " + line)
            if phase == "new":
                needed = self.space_needed(facts)
                say("  Space needed on the Pi: about %s." % gigabytes(needed))
                room = (found["free"] or 0) + (found["size"] if self.replaceable(found) else 0)
                if found["free"] is not None and room < needed:
                    problems.append("The Pi has room for %s; about %s is needed." % (
                        gigabytes(room), gigabytes(needed)))
            elif phase == "data" and self.state.data.get("marks") != facts.marks:
                problems.append("The server's data has changed since this copy began (a galaxy update "
                                "or state refresh ran). Start again with: copy --start-over")
        if phase == "new" and not self.replaceable(found):
            problems.append("The Pi's database already holds tables a copy would replace. "
                            "To replace them, run: copy --start-over")
        if phase == "new" and found["others"]:
            problems.append("Something is connected to the Pi's database (the app?). Stop it first.")
        if problems:
            say("Not ready yet:")
            for problem in problems:
                say("  - " + problem)
            return 1
        say("Ready to copy." if phase != "done" else "Nothing to copy.")
        return 0

    # .. the systems in reach ..................................................

    def make_reach(self, facts: ServerFacts) -> None:
        """The systems within 500 ly of an inhabited system, kept on the server in
        a table of their own while the copy runs."""
        say("Working out which systems are within %d ly of an inhabited one (minutes) ..." % REACH_LY)
        started = time.monotonic()
        values = {"c": ident(facts.cube_schema or "public"), "r": REACH_LY, "s": SCRATCH,
                  "g": ident(facts.galaxy or "")}
        # Each statement on its own, so that the big one can use several of the
        # server's cores. The list is named "reach" only once it is complete.
        self.machines.server_script([
            "SET statement_timeout = 0",
            "SET max_parallel_workers_per_gather = 8",
            "CREATE SCHEMA IF NOT EXISTS %(s)s" % values,
            "DROP TABLE IF EXISTS %(s)s.reach, %(s)s.reach_partial, %(s)s.inhabited" % values,
            "CREATE UNLOGGED TABLE %(s)s.inhabited AS SELECT %(c)s.cube(ARRAY[x_ly, y_ly, z_ly]) AS position "
            "FROM v3_state.system_state WHERE population > 0" % values,
            "CREATE INDEX inhabited_position ON %(s)s.inhabited USING gist (position)" % values,
            "ANALYZE %(s)s.inhabited" % values,
            # A box around each system finds, through the index, the inhabited
            # systems worth measuring; then the distance itself decides.
            "CREATE UNLOGGED TABLE %(s)s.reach_partial AS SELECT s.id64 AS system_id64 "
            "FROM ONLY %(g)s.systems s WHERE EXISTS (SELECT 1 FROM %(s)s.inhabited i "
            "WHERE i.position OPERATOR(%(c)s.<@) %(c)s.cube("
            "ARRAY[s.x_ly - %(r)d, s.y_ly - %(r)d, s.z_ly - %(r)d], "
            "ARRAY[s.x_ly + %(r)d, s.y_ly + %(r)d, s.z_ly + %(r)d]) "
            "AND %(c)s.cube_distance(i.position, %(c)s.cube(ARRAY[s.x_ly, s.y_ly, s.z_ly])) <= %(r)d)"
            % values,
            "ALTER TABLE %(s)s.reach_partial ADD PRIMARY KEY (system_id64)" % values,
            "ANALYZE %(s)s.reach_partial" % values,
            "ALTER TABLE %(s)s.reach_partial RENAME TO reach" % values,
        ])
        say("  Done in %s." % took(time.monotonic() - started))

    def check_reach(self, facts: ServerFacts) -> None:
        inhabited, reach = facts.reach_counts()
        self.reach_share = reach / facts.systems()
        say("  %s systems are within %d ly of the %s inhabited ones." % (
            number(reach), REACH_LY, number(inhabited)))
        if inhabited < self.settings.min_inhabited or reach < self.settings.min_reach:
            raise CopyError("so few systems in reach cannot be right, and nothing was copied. "
                            "(python3 copy_to_pi.py tidy removes the list from the server.)")

    # .. making the Pi's database .............................................

    def fetch_to_file(self, command: List[str], path: Path, what: str) -> None:
        partial = path.with_name(path.name + ".part")
        try:
            with open(partial, "wb") as out:
                result = subprocess.run(command, stdout=out, stderr=subprocess.PIPE,
                                        stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise CopyError("%s is not installed here" % command[0]) from None
        if result.returncode != 0:
            partial.unlink()
            raise CopyError("could not fetch %s from the server: %s" % (what, first_lines(result.stderr)))
        os.replace(partial, path)

    def prepare_pi(self, facts: ServerFacts, snapshot: str) -> None:
        """Make the Pi's database afresh: the server's users, then its structure
        without indexes or links, then the rules on columns put aside."""
        s, machines = self.settings, self.machines
        schema_file, roles_file = self.directory / SCHEMA_FILE, self.directory / ROLES_FILE
        say("Fetching the database's structure and its users from the server ...")
        excluded: List[str] = []
        for name in LEFT_BEHIND + (SCRATCH,):
            excluded += ["--exclude-schema=" + name]
        self.fetch_to_file(machines.server_tool(
            "pg_dump", "-U", s.user, "-d", s.server_database, "--schema-only", "--no-statistics",
            "--format=custom", "--snapshot=" + snapshot, *excluded), schema_file, "the database's structure")
        # Users only, without passwords: nothing secret is fetched.
        self.fetch_to_file(machines.server_tool(
            "pg_dumpall", "-U", s.user, "--globals-only", "--no-role-passwords", "--no-tablespaces"),
            roles_file, "the database's users")
        encoding, collate, ctype, provider, locale, owner = machines.server_rows(
            "SELECT pg_encoding_to_char(encoding), datcollate, datctype, datlocprovider, "
            "coalesce(datlocale, ''), pg_get_userbyid(datdba) FROM pg_database "
            "WHERE datname = current_database()")[0]

        say("Making the Pi's database afresh ...")
        result = machines.run(machines.pi_psql(
            "-c", "DROP DATABASE IF EXISTS %s WITH (FORCE)" % ident(s.pi_database), database="postgres"))
        if result.returncode != 0:
            raise CopyError("could not remove the Pi's old database: " + first_lines(result.stderr))
        # Users that are there already (the database's own owner, at least) are
        # reported as existing and kept; anything else going wrong stops here.
        result = machines.run(machines.pi_psql("-f", "-", database="postgres", stop_on_error=False),
                              data=roles_file.read_bytes())
        unexpected = [line for line in result.stderr.decode("utf-8", "replace").splitlines()
                      if "ERROR" in line and "already exists" not in line]
        if unexpected:
            raise CopyError("could not make the database's users on the Pi: " + " / ".join(unexpected[:3]))
        options = ["TEMPLATE template0", "OWNER %s" % ident(owner), "ENCODING %s" % literal(encoding),
                   "LC_COLLATE %s" % literal(collate), "LC_CTYPE %s" % literal(ctype)]
        options.append({"c": "LOCALE_PROVIDER libc",
                        "i": "LOCALE_PROVIDER icu ICU_LOCALE %s" % literal(locale),
                        "b": "LOCALE_PROVIDER builtin BUILTIN_LOCALE %s" % literal(locale)}[provider])
        result = machines.run(machines.pi_psql(
            "-c", "CREATE DATABASE %s %s" % (ident(s.pi_database), " ".join(options)), database="postgres"))
        if result.returncode != 0:
            raise CopyError("could not make the Pi's database: " + first_lines(result.stderr))
        result = machines.run(machines.pi_tool(
            "pg_restore", "--section=pre-data", "--exit-on-error", "-U", s.user, "-d", s.pi_database),
            data=schema_file.read_bytes())
        if result.returncode != 0:
            raise CopyError("could not make the database's structure on the Pi: " + first_lines(result.stderr))

        # The rules on columns come off the tables about to be loaded, and go
        # back on afterwards unchecked: checking them as the rows go in would
        # take many hours on the Pi, for rows the server checked long ago.
        loaded = ", ".join(literal(t.key) for t in facts.tables if t.rule != Table.EMPTY) or "''"
        checks = machines.pi_rows(
            "SET search_path = pg_catalog; "
            "SELECT n.nspname, c.relname, k.conname, pg_get_constraintdef(k.oid) "
            "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE k.contype = 'c' AND k.convalidated AND k.conislocal AND k.coninhcount = 0 "
            "AND n.nspname || '.' || c.relname IN (%s) ORDER BY 1, 2, 3" % loaded)
        if checks:
            result = machines.pi_sql("BEGIN;\n" + "\n".join(
                "ALTER TABLE %s.%s DROP CONSTRAINT %s;" % (ident(schema), ident(name), ident(constraint))
                for schema, name, constraint, _ in checks) + "\nCOMMIT;\n")
            if result.returncode != 0:
                raise CopyError("could not take the rules off the Pi's tables: " + first_lines(result.stderr))
        self.state.update(phase="data", checks=[list(row) for row in checks])
        say("  Done: %d tables made; %d rules on columns put aside until the rows are in."
            % (len(facts.tables), len(checks)))

    def counters_and_settings(self) -> None:
        """The counters behind numbered columns, and the database's own settings,
        read from the server now and set on the Pi at the end."""
        left_out = ", ".join(literal(name) for name in LEFT_BEHIND + (SCRATCH,))
        sequences = [row[0] for row in self.machines.server_rows(
            "SELECT format('SELECT pg_catalog.setval(%%L, %%s, %%s);', "
            "quote_ident(schemaname) || '.' || quote_ident(sequencename), "
            "coalesce(last_value, start_value), (last_value IS NOT NULL)::text) "
            "FROM pg_sequences WHERE schemaname NOT IN (%s) ORDER BY schemaname, sequencename" % left_out)]
        settings, names = [], set()
        for role, item in self.machines.server_rows(
                "SELECT coalesce(r.rolname, ''), unnest(s.setconfig) FROM pg_db_role_setting s "
                "LEFT JOIN pg_roles r ON r.oid = s.setrole "
                "WHERE s.setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database())"):
            name, _, value = item.partition("=")
            target = ("ALTER ROLE %s IN DATABASE %s" % (ident(role), ident(self.settings.pi_database))
                      if role else "ALTER DATABASE %s" % ident(self.settings.pi_database))
            settings.append("%s SET %s TO %s;" % (
                target, ident(name), value if name in LIST_SETTINGS else literal(value)))
            names.add(name)
        if names:
            say("  The database's own settings, to be copied: %s." % ", ".join(sorted(names)))
        self.state.update(sequences=sequences, settings=settings)

    # .. copying the tables ...................................................

    def copy_table(self, table: Table, generation: Optional[str], snapshot: str) -> Tuple[bool, int, str]:
        """Copy one table. (True, the rows the Pi took in, "") or (False, 0, why not)."""
        machines = self.machines
        work = ["-c", "SET statement_timeout = 0", "-c", "SET idle_in_transaction_session_timeout = 0"]
        if table.rule in (Table.CUT, Table.FILL):
            work += ["-c", "SET work_mem = '2GB'", "-c", "SET max_parallel_workers_per_gather = 4"]
        export = machines.server_psql(
            *work,
            "-c", "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY",
            "-c", "SET TRANSACTION SNAPSHOT %s" % literal(snapshot),
            "-c", "COPY (%s) TO STDOUT" % export_query(table, generation),
            # The end of the rows, then the Pi's COMMIT. They are sent only once
            # the whole table has been read, so a broken connection keeps nothing.
            "-c", "\\echo '\\\\.'",
            "-c", "\\echo 'COMMIT;'",
            "-c", "COMMIT")
        load = machines.pi_psql(
            "-c", "SET statement_timeout = 0",
            "-c", "BEGIN",
            "-c", "TRUNCATE ONLY %s" % table.qualified,
            "-c", "COPY %s (%s) FROM STDIN WITH (FREEZE)" % (table.qualified, table.column_list()),
            "-f", "-", quiet=False)
        with tempfile.TemporaryFile() as export_errors:
            try:
                sender = subprocess.Popen(export, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=export_errors)
            except FileNotFoundError:
                return False, 0, "ssh is not installed here"
            try:
                receiver = subprocess.Popen(load, stdin=sender.stdout, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE)
            except FileNotFoundError:
                sender.kill()
                sender.wait()
                return False, 0, "docker is not installed here"
            assert sender.stdout is not None
            sender.stdout.close()
            out, err = receiver.communicate()
            if receiver.returncode != 0:
                sender.kill()  # the Pi refused the rows: stop the server sending more
            sent = sender.wait()
            export_errors.seek(0)
            server_said = export_errors.read()
        lines = out.decode("utf-8", "replace").splitlines()
        loaded = [int(line.split()[1]) for line in lines if re.fullmatch(r"COPY \d+", line)]
        if receiver.returncode != 0:
            return False, 0, "the Pi refused it: " + first_lines(err)
        if sent != 0:
            return False, 0, "the server's side stopped: " + first_lines(server_said)
        if not loaded or "COMMIT" not in lines:
            return False, 0, "the end of the table did not arrive, so nothing of it was kept"
        return True, loaded[0], ""

    def copy_tables(self, facts: ServerFacts, holder: Holder) -> List[str]:
        """Copy every table not copied yet, several at a time; the ones that failed."""
        done = self.state.data.get("tables", {})
        pending = [t for t in facts.tables if t.rule != Table.EMPTY and t.key not in done]
        if not pending:
            return []
        weight = {t.key: t.size * (self.reach_share if t.rule == Table.CUT else 1) for t in pending}
        pending.sort(key=lambda t: weight[t.key], reverse=True)
        total = sum(weight.values())
        if done:
            say("%d tables were copied by an earlier run and are kept." % len(done))
        say("Copying %d tables (about %s on the server), %d at a time ..." % (
            len(pending), gigabytes(total), self.settings.jobs))
        queue = list(pending)
        lock = threading.Lock()
        expected: Dict[str, int] = {}
        loaded: Dict[str, int] = {}
        failed: Dict[str, str] = {}
        holder_lost: List[str] = []
        finished = threading.Event()
        began = time.monotonic()

        def count_rows() -> None:
            try:
                for table in pending:
                    expected[table.key] = int(holder.ask(count_query(table)))
            except (CopyError, ValueError) as error:
                holder_lost.append(str(error))

        def work() -> None:
            while True:
                with lock:
                    if not queue or holder_lost:
                        return
                    table = queue.pop(0)
                for attempt in (1, 2):
                    started = time.monotonic()
                    ok, rows, why = self.copy_table(table, facts.generation, holder.snapshot)
                    if ok:
                        loaded[table.key] = rows
                        if table.size >= 1e9 or time.monotonic() - started >= 60:
                            say("  %s: %s rows in %s." % (table.key, number(rows),
                                                          took(time.monotonic() - started)))
                        break
                    if attempt == 1 and not holder_lost:
                        say("  %s did not copy (%s). Trying it again in %d s ..."
                            % (table.key, why, self.settings.retry_pause))
                        time.sleep(self.settings.retry_pause)
                    else:
                        failed[table.key] = why
                        say("  %s did not copy: %s" % (table.key, why))
                        break

        def report() -> None:
            while not finished.wait(PROGRESS_EVERY):
                copying = self.copy_progress(expected, facts)
                say("  ... %d of %d tables copied (%s of about %s) in %s.%s" % (
                    len(loaded), len(pending), gigabytes(sum(weight[key] for key in loaded)),
                    gigabytes(total), took(time.monotonic() - began),
                    (" Loading now: " + copying + ".") if copying else ""))

        counter = threading.Thread(target=count_rows, daemon=True)
        counter.start()
        workers = [threading.Thread(target=work, daemon=True) for _ in range(self.settings.jobs)]
        for thread in workers:
            thread.start()
        reporter = threading.Thread(target=report, daemon=True)
        reporter.start()
        for thread in workers:
            thread.join()
        finished.set()
        if not holder_lost:
            counter.join()
        for message in holder_lost:
            say("  " + message)
        for table in pending:
            if table.key in failed:
                continue
            if table.key not in loaded:
                failed[table.key] = "not begun"
            elif table.key not in expected:
                failed[table.key] = "its rows could not be counted on the server"
            elif expected[table.key] != loaded[table.key]:
                failed[table.key] = "the server sent %s rows but the Pi took in %s" % (
                    number(expected[table.key]), number(loaded[table.key]))
            else:
                self.state.table_done(table.key, loaded[table.key])
        say("Tables copied: %d of %d, in %s." % (len(pending) - len(failed), len(pending),
                                                took(time.monotonic() - began)))
        for key in sorted(failed):
            if failed[key] != "not begun":
                say("  Not copied: %s (%s)." % (key, failed[key]))
        return sorted(failed)

    def copy_progress(self, expected: Dict[str, int], facts: ServerFacts) -> str:
        try:
            rows = self.machines.pi_rows(
                "SELECT n.nspname || '.' || c.relname, p.tuples_processed FROM pg_stat_progress_copy p "
                "JOIN pg_class c ON c.oid = p.relid JOIN pg_namespace n ON n.oid = c.relnamespace ORDER BY 1")
        except CopyError:
            return ""
        estimates = {t.key: t.estimate for t in facts.tables}
        parts = []
        for key, processed in rows:
            whole = expected.get(key) or estimates.get(key) or 0
            share = " (%d%%)" % min(99, 100 * int(processed) // whole) if whole else ""
            parts.append("%s %s rows%s" % (key, number(int(processed)), share))
        return ", ".join(parts)

    # .. after the rows ........................................................

    def finish_structure(self) -> None:
        """Indexes and keys, triggers and the rest, then the links between tables
        and the rules on columns (both unchecked), the counters, the database's
        settings and the planner's statistics."""
        s, machines = self.settings, self.machines
        result = machines.run(machines.pi_tool("pg_restore", "--section=post-data", "--file=-"),
                              data=(self.directory / SCHEMA_FILE).read_bytes())
        if result.returncode != 0:
            raise CopyError("could not read the structure file: " + first_lines(result.stderr))
        prologue, entries = split_restore_script(result.stdout.decode("utf-8"))
        done = set(self.state.data.get("post_done", []))
        sizes = self.state.data.get("sizes", {})

        def make(entry: Dict[str, str]) -> None:
            started = time.monotonic()
            outcome = machines.pi_sql(prologue + "\n" + entry["sql"] + "\n")
            if outcome.returncode != 0:
                raise CopyError("could not make %s %s on the Pi: %s" % (
                    entry["type"].lower(), entry["name"], first_lines(outcome.stderr)))
            self.state.post_done(entry["key"])
            if time.monotonic() - started >= 60:
                say("  %s %s: %s." % (entry["type"].lower(), entry["name"], took(time.monotonic() - started)))

        together = [e for e in entries if e["type"] in BUILT_TOGETHER and e["key"] not in done]
        together.sort(key=lambda e: sizes.get("%s.%s" % (e["schema"], e["table"]), 0), reverse=True)
        if together:
            say("Building %d indexes and keys, %d at a time ..." % (len(together), s.jobs))
            started = time.monotonic()
            self.in_parallel(together, make)
            say("  Built in %s." % took(time.monotonic() - started))
        rest = [e for e in entries if e["type"] not in BUILT_TOGETHER + ("FK CONSTRAINT",)
                and e["key"] not in done]
        if rest:
            say("Adding %d triggers, comments and other parts ..." % len(rest))
            self.in_one_session(prologue, rest)
        links = [e for e in entries if e["type"] == "FK CONSTRAINT" and e["key"] not in done]
        if links:
            say("Adding %d links between tables, unchecked ..." % len(links))
            unchecked = []
            for entry in links:
                statement, was_checked = not_valid(entry["sql"])
                unchecked.append(dict(entry, sql=statement, was_checked=was_checked))

            def note(entry: Dict[str, Any]) -> None:
                # Links the server had checked are checked again by `validate`.
                if entry["was_checked"]:
                    self.state.add_foreign_key(entry["schema"], entry["table"], entry["constraint"])

            self.in_one_session(prologue, unchecked, after=note)
        if "rules" not in done:
            checks = self.state.data.get("checks", [])
            if checks:
                say("Putting back %d rules on columns, unchecked ..." % len(checks))
                outcome = machines.pi_sql("SET search_path = pg_catalog;\nBEGIN;\n" + "\n".join(
                    "ALTER TABLE %s.%s ADD CONSTRAINT %s %s NOT VALID;" % (
                        ident(schema), ident(name), ident(constraint), definition)
                    for schema, name, constraint, definition in checks) + "\nCOMMIT;\n")
                if outcome.returncode != 0:
                    raise CopyError("could not put back the rules on columns: " + first_lines(outcome.stderr))
            self.state.post_done("rules")
        if "counters" not in done:
            statements = self.state.data.get("sequences", []) + self.state.data.get("settings", [])
            if statements:
                say("Setting %d counters and settings ..." % len(statements))
                outcome = machines.pi_sql("\n".join(statements) + "\n")
                if outcome.returncode != 0:
                    raise CopyError("could not set the counters: " + first_lines(outcome.stderr))
            self.state.post_done("counters")
        if "statistics" not in done:
            say("Gathering statistics for the query planner ...")
            started = time.monotonic()
            outcome = machines.run(machines.pi_tool(
                "vacuumdb", "-U", s.user, "-d", s.pi_database, "--analyze-only", "--quiet",
                "--jobs=%d" % s.jobs))
            if outcome.returncode != 0:
                raise CopyError("could not gather statistics: " + first_lines(outcome.stderr))
            self.state.post_done("statistics")
            say("  Done in %s." % took(time.monotonic() - started))

    def in_one_session(self, prologue: str, entries: List[Dict[str, Any]],
                       after: Optional[Callable[[Dict[str, Any]], Any]] = None) -> None:
        """Make these parts one after the other, in one session on the Pi's
        database, noting each as it is made, so that a run that stops part-way
        carries on after the last one made."""
        script = [prologue]
        for position, entry in enumerate(entries):
            script += [entry["sql"], "\\echo 'edfinder-made %d'" % position]
        outcome = self.machines.pi_sql("\n".join(script) + "\n")
        made = [int(found) for found in re.findall(r"^edfinder-made (\d+)$",
                                                   outcome.stdout.decode("utf-8", "replace"), re.M)]
        for position in made:
            if after is not None:
                after(entries[position])
            self.state.post_done(entries[position]["key"])
        if outcome.returncode != 0 or len(made) != len(entries):
            failed = entries[len(made)] if len(made) < len(entries) else entries[-1]
            raise CopyError("could not make %s %s on the Pi: %s" % (
                failed["type"].lower(), failed["name"], first_lines(outcome.stderr)))

    def in_parallel(self, entries: List[Dict[str, str]], action: Callable[[Dict[str, str]], None]) -> None:
        queue = list(entries)
        lock = threading.Lock()
        errors: List[str] = []
        progress = {"done": 0, "said": time.monotonic()}

        def work() -> None:
            while True:
                with lock:
                    if not queue or errors:
                        return
                    entry = queue.pop(0)
                try:
                    action(entry)
                except CopyError as error:
                    with lock:
                        errors.append(str(error))
                    return
                with lock:
                    progress["done"] += 1
                    if time.monotonic() - progress["said"] >= PROGRESS_EVERY:
                        progress["said"] = time.monotonic()
                        say("  ... %d of %d done." % (progress["done"], len(entries)))

        threads = [threading.Thread(target=work, daemon=True) for _ in range(self.settings.jobs)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if errors:
            raise CopyError(errors[0])

    # .. the commands ..........................................................

    def copy(self, start_over: bool) -> int:
        if start_over:
            self.state.data = {}
        phase = self.state.phase
        if phase == "done":
            say("A copy has finished already (begun %s). To copy again from the start, run: "
                "copy --start-over" % self.state.data.get("started", "?"))
            return 1
        if phase == "structure":
            # The rows are all in: only the Pi is needed from here.
            problems, _ = self.pi_problems()
            if problems:
                say("Not ready:")
                for problem in problems:
                    say("  - " + problem)
                return 1
            return self.finish()
        say("Checking both machines ...")
        problems, found = self.pi_problems()
        server_problems, facts = self.server_problems()
        problems += server_problems + self.both_problems(found, facts)
        if phase == "new":
            # What an earlier start of this copy left is not worth keeping.
            ours = bool(self.state.data.get("started"))
            if not (start_over or ours or self.replaceable(found)):
                problems.append("The Pi's database already holds tables a copy would replace. "
                                "To replace them, run: copy --start-over")
            if found["others"]:
                problems.append("Something is connected to the Pi's database (the app?). Stop it first.")
            if facts is not None and found["free"] is not None:
                needed = self.space_needed(facts)
                if found["free"] + found["size"] < needed:
                    problems.append("The Pi has room for %s; about %s is needed." % (
                        gigabytes(found["free"] + found["size"]), gigabytes(needed)))
        elif phase == "data" and facts is not None and self.state.data.get("marks") != facts.marks:
            problems.append("The server's data has changed since this copy began (a galaxy update or "
                            "state refresh ran), so the tables copied so far no longer match the rest. "
                            "Start again with: copy --start-over")
        if problems or facts is None:
            say("Not ready:")
            for problem in problems:
                say("  - " + problem)
            return 1

        if phase == "new":
            self.state.reset(facts.marks, {t.key: t.size for t in facts.tables})
            self.make_reach(facts)
        elif phase == "data" and not facts.has_reach:
            self.make_reach(facts)
        if self.state.phase in ("new", "data"):
            self.check_reach(facts)
            say("Opening a snapshot of the server's database ...")
            holder = Holder(self.machines)
            try:
                if self.state.phase == "new":
                    self.prepare_pi(facts, holder.snapshot)
                    self.counters_and_settings()
                failed = self.copy_tables(facts, holder)
            finally:
                message = holder.close()
                if message.strip():
                    say("The snapshot's session on the server said: " + first_lines(message))
            if failed:
                say("%d tables did not copy. Run the same command again to copy them; the others "
                    "are kept." % len(failed))
                return 1
            self.state.update(phase="structure")
        return self.finish()

    def finish(self) -> int:
        self.finish_structure()
        self.state.update(phase="done")
        self.tidy(quiet=True)
        say("The copy is finished. Next, on the Pi, when convenient (it can take hours, and the app can "
            "be in use meanwhile):")
        say("  python3 copy_to_pi.py validate")
        return 0

    def validate(self) -> int:
        """Check the rows against every link and rule the copy added unchecked,
        smallest tables first."""
        if self.state.phase != "done":
            say("The copy has not finished, so there is nothing to check yet.")
            return 1
        wanted = [("link", schema, table, name) for schema, table, name in self.state.data.get("foreign_keys", [])]
        wanted += [("rule", schema, table, name) for schema, table, name, _ in self.state.data.get("checks", [])]
        unchecked = {tuple(row) for row in self.machines.pi_rows(
            "SELECT n.nspname, c.relname, k.conname FROM pg_constraint k "
            "JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE NOT k.convalidated")}
        sizes = self.state.data.get("sizes", {})
        todo = [item for item in wanted if (item[1], item[2], item[3]) in unchecked]
        todo.sort(key=lambda item: (sizes.get("%s.%s" % (item[1], item[2]), 0), item[0] != "link"))
        if not todo:
            say("Every link and rule the copy added has been checked and holds.")
            return 0
        say("Checking %d links and rules, smallest tables first ..." % len(todo))
        script = ["SET statement_timeout = 0;"]
        for position, (_kind, schema, table, name) in enumerate(todo):
            script += ["ALTER TABLE %s.%s VALIDATE CONSTRAINT %s;" % (ident(schema), ident(table), ident(name)),
                       "\\echo 'edfinder-checked %d'" % position]
        began = last = said = time.monotonic()
        checked = 0
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(self.machines.pi_psql("-f", "-"), stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=errors, text=True)
            assert process.stdin is not None and process.stdout is not None
            stdin = process.stdin

            def feed() -> None:
                try:
                    stdin.write("\n".join(script) + "\n")
                    stdin.close()
                except (BrokenPipeError, OSError):
                    pass

            feeder = threading.Thread(target=feed, daemon=True)
            feeder.start()
            for line in process.stdout:
                found = re.fullmatch(r"edfinder-checked (\d+)\s*", line)
                if not found:
                    continue
                kind, schema, table, name = todo[int(found.group(1))]
                checked += 1
                now = time.monotonic()
                if now - last >= 30:
                    say("  The %s %s on %s.%s holds (%s)." % (kind, name, schema, table, took(now - last)))
                elif now - said >= PROGRESS_EVERY:
                    say("  ... %d of %d checked." % (checked, len(todo)))
                    said = now
                last = now
            process.wait()
            feeder.join()
            errors.seek(0)
            message = errors.read()
        if process.returncode != 0 or checked != len(todo):
            kind, schema, table, name = todo[min(checked, len(todo) - 1)]
            say("  The %s %s on %s.%s does not hold: %s" % (kind, name, schema, table, first_lines(message)))
            return 1
        say("  All %d checked in %s." % (checked, took(time.monotonic() - began)))
        say("Every link and rule the copy added holds.")
        return 0

    def tidy(self, quiet: bool = False) -> int:
        result = self.machines.run(self.machines.server_psql(
            "-c", "SET statement_timeout = 0", "-c", "DROP SCHEMA IF EXISTS %s CASCADE" % SCRATCH))
        if result.returncode != 0:
            say("Could not remove the list of systems in reach from the server: " + first_lines(result.stderr))
            return 1
        if not quiet:
            say("The list of systems in reach is gone from the server.")
        return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="copy_to_pi.py", description="Copy the database from the current server to the Pi.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("command", choices=("check", "copy", "validate", "tidy"))
    parser.add_argument("--start-over", action="store_true",
                        help="copy: throw away the Pi's database and any copy begun before, and start again")
    args = parser.parse_args(argv)
    try:
        copier = Copier(Settings())
        if args.command == "check":
            return copier.check()
        if args.command == "copy":
            return copier.copy(args.start_over)
        if args.command == "validate":
            return copier.validate()
        return copier.tidy()
    except CopyError as error:
        say("STOPPED: %s" % error)
        return 1
    except KeyboardInterrupt:
        say("STOPPED by Ctrl+C. Run the same command again to carry on.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
