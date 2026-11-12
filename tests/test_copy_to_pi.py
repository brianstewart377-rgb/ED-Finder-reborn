"""The copy to the Pi (scripts/pi/copy_to_pi.py), run end to end.

Both machines are databases in the one PostgreSQL 18 container the other
database tests use. The server's is the scored test database: the twelve real
fixture systems as production held them in August, their scores, archetypes
and Find rows (made by the frozen build code), and the real system state of
the nine of them that are inhabited. The Pi's database starts empty. Stand-ins
for ssh (runs the command here), docker (sends both machines' container names
to the real container) and systemctl (the server's nightly jobs) let the script
run unchanged.

Needs Docker and RATINGS_V4_VALIDATION_DATABASE_URL. Skipped without them,
unless REQUIRE_DOCKER_TESTS=1 is set (CI sets it).
"""
from __future__ import annotations

import gzip
import io
import json
import math
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pi" / "copy_to_pi.py"
FIXTURE_ZIP = ROOT / "tests" / "fixtures" / "ratings_v4_sources" / "spansh-system-dumps.zip"
DSN = os.environ.get("RATINGS_V4_VALIDATION_DATABASE_URL", "")
REQUIRED = os.environ.get("REQUIRE_DOCKER_TESTS") == "1"
SERVER_CONTAINER = "copy-test-server-db"
PI_CONTAINER = "copy-test-pi-db"
LEFT_OUT_SCHEMAS = ("pg_catalog", "information_schema", "r1_meta", "r1_plan", "edfinder_copy")
CUT = ("economy_opportunity", "system_search", "body_mechanics", "system_rating_vector", "system_archetype")

sys.path.insert(0, str(ROOT / "scripts" / "pi"))
import copy_to_pi  # noqa: E402

# The scored database with the twelve real systems, built once for this module.
from tests.test_galaxy_scores import template  # noqa: E402,F401


def _container() -> str:
    """The Docker container the test database runs in, found by its port."""
    if not DSN:
        return ""
    if shutil.which("docker") is None:
        return ""
    port = urlsplit(DSN).port or 5432
    found = subprocess.run(["docker", "ps", "--filter", "publish=%d" % port, "--format", "{{.Names}}"],
                           capture_output=True, text=True)
    names = found.stdout.split()
    return names[0] if found.returncode == 0 and len(names) == 1 else ""


CONTAINER = _container()
_REASON = ("" if CONTAINER else
           "the test database is not in a Docker container this test can reach "
           "(needs Docker and RATINGS_V4_VALIDATION_DATABASE_URL)")
if _REASON and REQUIRED:
    raise RuntimeError("Docker tests are required but cannot run: " + _REASON)
pytestmark = pytest.mark.skipif(bool(_REASON), reason=_REASON or "available")


# -- stand-ins for the server's tools ------------------------------------------

FAKE_SSH = r'''#!/usr/bin/env python3
"""ssh: run the command here. FAKE_SSH_BREAK = {"text": times}: a COPY whose
command holds the text is cut off before its end that many times, every row
sent but not the end of the table, as a dropped connection would."""
import hashlib, json, os, subprocess, sys
args = sys.argv[1:]
i = 0
while args[i].startswith("-"):
    i += 2 if args[i] in ("-i", "-o", "-p", "-l", "-F") else 1
command = " ".join(args[i + 1:])
breaks = json.loads(os.environ.get("FAKE_SSH_BREAK") or "{}")
for text, times in breaks.items():
    if "COPY (SELECT" in command and text in command:
        tally = os.path.join(os.environ["FAKE_SSH_TALLY"], hashlib.md5(text.encode()).hexdigest())
        done = int(open(tally).read()) if os.path.exists(tally) else 0
        if done < times:
            open(tally, "w").write(str(done + 1))
            out = subprocess.run(["sh", "-c", command], capture_output=True).stdout
            lines = out.splitlines(keepends=True)
            sys.stdout.buffer.write(b"".join(lines[:-2]))
            sys.stdout.flush()
            sys.stderr.write("Connection to server closed by remote host.\n")
            sys.exit(255)
os.execvp("sh", ["sh", "-c", command])
'''

FAKE_DOCKER = r'''#!/usr/bin/env python3
"""docker: the real one, with the machines' container names put right."""
import json, os, sys
names = json.loads(os.environ["FAKE_DOCKER_CONTAINERS"])
args = [names.get(arg, arg) for arg in sys.argv[1:]]
os.execv(os.environ["FAKE_DOCKER_REAL"], ["docker"] + args)
'''

FAKE_SYSTEMCTL = r'''#!/usr/bin/env python3
"""systemctl is-active: the units named in FAKE_SYSTEMCTL_ACTIVE are active."""
import os, sys
active = set((os.environ.get("FAKE_SYSTEMCTL_ACTIVE") or "").split())
units = sys.argv[2:]
for unit in units:
    print("active" if unit in active else "inactive")
sys.exit(0 if units and all(unit in active for unit in units) else 3)
'''


@pytest.fixture(scope="module")
def tools(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("fakebin")
    for name, text in (("ssh", FAKE_SSH), ("docker", FAKE_DOCKER), ("systemctl", FAKE_SYSTEMCTL)):
        path = folder / name
        path.write_text(text.replace("/usr/bin/env python3", sys.executable, 1), encoding="utf-8")
        path.chmod(0o755)
    return folder


def in_container(*args: str, data: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", "exec", "-i", CONTAINER, *args], input=data, capture_output=True)
    if check and result.returncode != 0:
        raise AssertionError("%s failed:\n%s" % (args[0], result.stderr.decode("utf-8", "replace")))
    return result


@pytest.fixture(scope="module")
def server_template(template):  # noqa: F811 (the fixture imported above)
    """The scored database plus the real system state, built once."""
    import spansh_system_state as state

    database, _schema, _records, _generation = template
    name = "copy_server_" + uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
            sql.Identifier(name), sql.Identifier(database)))
    try:
        dumps = []
        with zipfile.ZipFile(FIXTURE_ZIP) as archive:
            for member in archive.namelist():
                record = json.loads(archive.read(member))
                dumps.append(record.get("system", record))
        folder = Path(os.environ.get("TMPDIR", "/tmp")) / ("copy_state_" + uuid4().hex)
        folder.mkdir()
        path = folder / "galaxy_populated.json.gz"
        with gzip.open(path, "wb") as handle:
            handle.write(("[\n" + ",\n".join(json.dumps(system) for system in dumps) + "\n]\n").encode())
        stream = io.BytesIO()
        state.write_load_stream([str(path)], stream, snapshot=True, source_label="copy-test")
        shutil.rmtree(folder)
        in_container("psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", name,
                     data=stream.getvalue())
        with psycopg.connect(make_conninfo(DSN, dbname=name), autocommit=True) as connection:
            # Everything is scored, so nothing waits; the r1 leftovers are made
            # as production has them, to be left behind.
            connection.execute("DELETE FROM v3_state.rescore_queue")
            connection.execute("CREATE SCHEMA r1_meta; CREATE TABLE r1_meta.note (id int); "
                               "INSERT INTO r1_meta.note VALUES (1)")
            if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname = 'copy_test_reader'").fetchone():
                connection.execute("CREATE ROLE copy_test_reader NOLOGIN")
            connection.execute("GRANT USAGE ON SCHEMA v3_app TO copy_test_reader")
            # A note whose text has a blank line and a line that looks like an
            # SQL comment must arrive as it is.
            galaxy = connection.execute("SELECT relation_schema FROM v3_meta.canonical_generation "
                                        "ORDER BY relation_schema DESC LIMIT 1").fetchone()[0]
            connection.execute(sql.SQL("COMMENT ON INDEX {}.systems_macro_grid_idx IS {}").format(
                sql.Identifier(galaxy), sql.Literal("Grid key.\n\n-- not an SQL comment\nEnd.")))
            connection.execute("GRANT SELECT ON v3_app.system_search TO copy_test_reader")
            assert connection.execute(
                "SELECT count(*) FROM v3_state.system_state WHERE population > 0").fetchone()[0] == 9
        yield name
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


class Machines:
    """A server database (a fresh copy of the template) and a Pi database name."""

    def __init__(self, server: str, pi: str, folder: Path, tools: Path) -> None:
        self.server, self.pi, self.folder = server, pi, folder
        self.environ = {
            **os.environ,
            "PATH": "%s:%s" % (tools, os.environ.get("PATH", "")),
            "FAKE_DOCKER_REAL": shutil.which("docker") or "docker",
            "FAKE_DOCKER_CONTAINERS": json.dumps({SERVER_CONTAINER: CONTAINER, PI_CONTAINER: CONTAINER}),
            "FAKE_SSH_TALLY": str(folder),
            "EDFINDER_COPY_SERVER": "root@server.invalid",
            "EDFINDER_COPY_KEY": str(folder / "key"),
            "EDFINDER_COPY_SERVER_CONTAINER": SERVER_CONTAINER,
            "EDFINDER_COPY_PI_CONTAINER": PI_CONTAINER,
            "EDFINDER_COPY_SERVER_DATABASE": server,
            "EDFINDER_COPY_PI_DATABASE": pi,
            "EDFINDER_COPY_USER": "postgres",
            "EDFINDER_COPY_DIR": str(folder),
            "EDFINDER_COPY_JOBS": "4",
            "EDFINDER_COPY_MIN_INHABITED": "1",
            "EDFINDER_COPY_MIN_REACH": "1",
            "EDFINDER_COPY_RETRY_PAUSE": "0",
            "EDFINDER_COPY_SPARE_GB": "1",
        }

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
        # COPY_TO_PI_PYTHON: try the script with another Python (the Pi's own).
        python = os.environ.get("COPY_TO_PI_PYTHON") or sys.executable
        return subprocess.run([python, str(SCRIPT), *args], env={**self.environ, **extra},
                              capture_output=True, text=True, timeout=900)

    def connect(self, database: str) -> psycopg.Connection:
        return psycopg.connect(make_conninfo(DSN, dbname=database), autocommit=True)

    def rows(self, database: str, statement: str, params=()) -> list:
        with self.connect(database) as connection:
            return connection.execute(statement, params).fetchall()

    def state(self) -> dict:
        return json.loads((self.folder / "copy-state.json").read_text())


@pytest.fixture
def machines(server_template, tools, tmp_path):
    server = "copy_server_" + uuid4().hex
    pi = "copy_pi_" + uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
            sql.Identifier(server), sql.Identifier(server_template)))
    (tmp_path / "key").write_text("stands in for the Pi's key; the stand-in ssh never reads it\n")
    try:
        yield Machines(server, pi, tmp_path, tools)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            for name in (server, pi):
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def tables(machines: Machines, database: str) -> dict[str, int]:
    return {"%s.%s" % (schema, name): count for schema, name, count in machines.rows(database, """
        SELECT n.nspname, c.relname, (xpath('/row/c/text()', query_to_xml(format(
               'SELECT count(*) AS c FROM ONLY %%I.%%I', n.nspname, c.relname), false, true, '')))[1]::text::int
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind = 'r' AND n.nspname <> ALL(%s) AND n.nspname !~ '^pg_'""",
        (list(LEFT_OUT_SCHEMAS),))}


def content(machines: Machines, database: str, table: str, *, leave_out: str = "",
            systems: set[int] | None = None) -> list[str]:
    schema, name = table.split(".", 1)
    columns = [row[0] for row in machines.rows(database, """
        SELECT a.attname FROM pg_attribute a WHERE a.attrelid = %s::regclass AND a.attnum > 0
           AND NOT a.attisdropped ORDER BY a.attnum""", ('"%s"."%s"' % (schema, name),))
                if row[0] != leave_out]
    select = sql.SQL("SELECT ({})::text FROM ONLY {}.{} t").format(
        sql.SQL(", ").join(sql.Identifier("t", column) for column in columns),
        sql.Identifier(schema), sql.Identifier(name))
    if systems is not None:
        select += sql.SQL(" WHERE t.system_id64 = ANY(%s)")
    with machines.connect(database) as connection:
        return sorted(row[0] for row in connection.execute(
            select, (sorted(systems),) if systems is not None else ()).fetchall())


def structure(machines: Machines, database: str) -> dict[str, set]:
    """Names of indexes, constraints (with whether checked), triggers and views."""
    left_out = list(LEFT_OUT_SCHEMAS)
    return {
        "indexes": {tuple(row) for row in machines.rows(database, """
            SELECT schemaname, tablename, indexname, indexdef FROM pg_indexes
             WHERE schemaname <> ALL(%s)""", (left_out,))},
        "constraints": {tuple(row) for row in machines.rows(database, """
            SELECT n.nspname, c.relname, k.conname, k.contype,
                   replace(pg_get_constraintdef(k.oid), ' NOT VALID', '')
              FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname <> ALL(%s)""", (left_out,))},
        "checked": {tuple(row) for row in machines.rows(database, """
            SELECT n.nspname, c.relname, k.conname FROM pg_constraint k
              JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE k.convalidated AND n.nspname <> ALL(%s)""", (left_out,))},
        "triggers": {tuple(row) for row in machines.rows(database, """
            SELECT event_object_schema, event_object_table, trigger_name, action_timing, event_manipulation
              FROM information_schema.triggers WHERE event_object_schema <> ALL(%s)""", (left_out,))},
        "comments": {tuple(row) for row in machines.rows(database, """
            SELECT pg_describe_object(classoid, objoid, objsubid), description FROM pg_description
             WHERE objoid >= 16384
               AND pg_describe_object(classoid, objoid, objsubid) !~ '(r1_meta|r1_plan|edfinder_copy)'""")},
        "views": {tuple(row) for row in machines.rows(database, """
            SELECT schemaname, viewname, definition FROM pg_views WHERE schemaname <> ALL(%s)""", (left_out,))},
        "grants": {tuple(row) for row in machines.rows(database, """
            SELECT n.nspname, '', coalesce(array_to_string(n.nspacl, ','), '') FROM pg_namespace n
             WHERE n.nspname <> ALL(%s) AND n.nspname !~ '^pg_'
            UNION ALL
            SELECT n.nspname, c.relname, coalesce(array_to_string(c.relacl, ','), '') FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname <> ALL(%s) AND n.nspname !~ '^pg_'""", (left_out, left_out))},
    }


def systems_in_reach(machines: Machines, database: str) -> set[int]:
    """Worked out here, by brute force: systems within 500 ly of an inhabited one."""
    galaxy = machines.rows(database, """
        SELECT g.relation_schema FROM v3_meta.current_canonical_generation c
          JOIN v3_meta.canonical_generation g USING (generation_id)""")[0][0]
    inhabited = machines.rows(database, "SELECT x_ly, y_ly, z_ly FROM v3_state.system_state WHERE population > 0")
    return {system for system, x, y, z in machines.rows(
        database, sql.SQL("SELECT id64, x_ly, y_ly, z_ly FROM {}.systems").format(sql.Identifier(galaxy)))
            if any(math.dist((x, y, z), point) <= 500 for point in inhabited)}


# -- the tests -------------------------------------------------------------------


def test_check_says_what_a_copy_would_take_and_changes_nothing(machines):
    before = tables(machines, machines.server)
    result = machines.run("check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Ready to copy." in result.stdout
    assert "5 score tables cut to the systems within 500 ly" in result.stdout
    assert "Made but left empty: body_parent_evidence" in result.stdout
    assert "Left behind: r1_meta." in result.stdout
    assert "Each body's class filled in on the way." in result.stdout
    assert tables(machines, machines.server) == before
    assert not machines.rows("postgres", "SELECT 1 FROM pg_database WHERE datname = %s", (machines.pi,))
    assert not (machines.folder / "copy-state.json").exists()


def test_the_copy_takes_every_table_as_the_list_says_and_its_links_hold(machines):
    server = machines.server
    reach = systems_in_reach(machines, server)
    assert len(reach) == 9  # the nine inhabited real systems; the other three are thousands of ly out
    before = structure(machines, server)
    server_tables = tables(machines, server)

    result = machines.run("copy")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "The copy is finished." in result.stdout
    pi_tables = tables(machines, machines.pi)

    # Every table is there; the leftovers and the copy's own list are not.
    assert set(pi_tables) == set(server_tables)
    assert not machines.rows(server, "SELECT 1 FROM pg_namespace WHERE nspname = 'edfinder_copy'")
    assert not machines.rows(machines.pi, "SELECT 1 FROM pg_namespace WHERE nspname LIKE 'r1_%%'")
    galaxy = machines.rows(server, """SELECT g.relation_schema FROM v3_meta.current_canonical_generation c
                                        JOIN v3_meta.canonical_generation g USING (generation_id)""")[0][0]
    for table, count in server_tables.items():
        schema, name = table.split(".", 1)
        if schema == "v3_derived" and name in CUT:
            kept = machines.rows(server, sql.SQL("SELECT count(*) FROM {}.{} WHERE system_id64 = ANY(%s)").format(
                sql.Identifier(schema), sql.Identifier(name)), (sorted(reach),))[0][0]
            assert pi_tables[table] == kept < count, table
            assert content(machines, machines.pi, table) == content(machines, server, table, systems=reach)
        elif name == "body_parent_evidence":
            assert pi_tables[table] == 0, table
        elif table == galaxy + ".bodies":
            assert content(machines, machines.pi, table, leave_out="body_class") == content(
                machines, server, table, leave_out="body_class")
        else:
            assert content(machines, machines.pi, table) == content(machines, server, table), table

    # Each body's class: its own, else its score row's, else its plain type for
    # the kinds that are never scored. None is left without one.
    classes = dict(machines.rows(machines.pi, sql.SQL("SELECT body_pk, body_class FROM {}.bodies").format(
        sql.Identifier(galaxy))))
    expected = dict(machines.rows(server, sql.SQL("""
        SELECT b.body_pk, coalesce(b.body_class, m.body_class, CASE t.public_code WHEN 'barycentre'
               THEN 'barycentre' WHEN 'belt_cluster' THEN 'belt_cluster' END)
          FROM {}.bodies b
          LEFT JOIN v3_derived.body_mechanics m ON m.body_pk = b.body_pk AND m.system_id64 = b.system_id64
          LEFT JOIN v3_vocab.body_type t ON t.body_type_id = b.body_type_id""").format(sql.Identifier(galaxy))))
    assert classes == expected
    assert None not in classes.values()
    assert not machines.rows(server, sql.SQL("SELECT 1 FROM {}.bodies WHERE body_class IS NOT NULL").format(
        sql.Identifier(galaxy))), "the server's own rows are left as they were"

    # The same structure, with the links and rules that were checked on the
    # server added unchecked; validate then checks every one of them.
    after = structure(machines, machines.pi)
    for part in ("indexes", "constraints", "triggers", "comments", "views", "grants"):
        assert after[part] == before[part], part
    assert after["checked"] < before["checked"]
    counters = "SELECT schemaname, sequencename, last_value FROM pg_sequences WHERE schemaname !~ '^r1_'"
    assert set(machines.rows(machines.pi, counters)) == set(machines.rows(server, counters))
    assert machines.rows(machines.pi, "SELECT count(*) FROM v3_app.system_search")[0][0] > 0

    result = machines.run("validate")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Every link and rule the copy added holds." in result.stdout
    assert structure(machines, machines.pi)["checked"] == before["checked"]
    assert machines.run("copy").returncode == 1  # a finished copy is not started again by mistake


def test_a_broken_connection_keeps_nothing_and_the_copy_carries_on_where_it_stopped(machines):
    galaxy = machines.rows(machines.server, "SELECT relation_schema FROM v3_meta.canonical_generation "
                                            "ORDER BY relation_schema DESC LIMIT 1")[0][0]
    rings = '"%s"."rings"' % galaxy
    stations = '"%s"."stations"' % galaxy
    # Rings break off once (tried again at once, and copied); stations break
    # off on both tries, so the first run ends without them.
    breaks = json.dumps({rings: 1, stations: 2})
    first = machines.run("copy", FAKE_SSH_BREAK=breaks)
    assert first.returncode == 1, first.stdout + first.stderr
    assert "%s.rings did not copy (the server's side stopped" % galaxy in first.stdout
    assert "Trying it again" in first.stdout
    assert "Not copied: %s.stations" % galaxy in first.stdout
    state = machines.state()
    assert state["phase"] == "data" and "%s.rings" % galaxy in state["tables"]
    assert "%s.stations" % galaxy not in state["tables"]
    stations_rows = machines.rows(machines.pi, "SELECT count(*) FROM %s" % stations)[0][0]
    assert stations_rows == 0, "nothing of a table that did not arrive whole is kept"
    loaded_before = dict(machines.rows(machines.pi, "SELECT relname, n_tup_ins FROM pg_stat_user_tables"))

    second = machines.run("copy", FAKE_SSH_BREAK=breaks)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "were copied by an earlier run and are kept" in second.stdout
    assert "Copying 1 tables" in second.stdout
    loaded_after = dict(machines.rows(machines.pi, "SELECT relname, n_tup_ins FROM pg_stat_user_tables"))
    assert loaded_after["rings"] == loaded_before["rings"], "copied tables are not copied again"
    assert content(machines, machines.pi, "%s.stations" % galaxy) == content(
        machines, machines.server, "%s.stations" % galaxy)


def test_the_copy_will_not_start_while_the_servers_data_can_change(machines):
    on = machines.run("copy", FAKE_SYSTEMCTL_ACTIVE="edfinder-galaxy-update.timer")
    assert on.returncode == 1
    assert "systemctl stop edfinder-galaxy-update.timer" in on.stdout
    with machines.connect(machines.server) as connection:
        galaxy = connection.execute("SELECT relation_schema FROM v3_meta.canonical_generation "
                                    "ORDER BY relation_schema DESC LIMIT 1").fetchone()[0]
        system = connection.execute(sql.SQL("SELECT id64 FROM {}.systems LIMIT 1").format(
            sql.Identifier(galaxy))).fetchone()[0]
        connection.execute("INSERT INTO v3_state.rescore_queue (system_id64, changed_tables, source_run_id) "
                           "SELECT %s, '{bodies}', source_run_id FROM v3_source.source_run LIMIT 1", (system,))
    waiting = machines.run("copy")
    assert waiting.returncode == 1
    assert "1 systems are waiting for new scores" in waiting.stdout
    assert not machines.rows("postgres", "SELECT 1 FROM pg_database WHERE datname = %s", (machines.pi,))
    assert not machines.rows(machines.server, "SELECT 1 FROM pg_namespace WHERE nspname = 'edfinder_copy'")


def test_a_copy_begun_before_the_servers_data_changed_is_not_carried_on(machines):
    galaxy = machines.rows(machines.server, "SELECT relation_schema FROM v3_meta.canonical_generation "
                                            "ORDER BY relation_schema DESC LIMIT 1")[0][0]
    breaks = json.dumps({'"%s"."rings"' % galaxy: 2})
    assert machines.run("copy", FAKE_SSH_BREAK=breaks).returncode == 1
    with machines.connect(machines.server) as connection:
        connection.execute("UPDATE v3_state.load_log SET loaded_at = loaded_at + interval '1 day'")
    again = machines.run("copy")
    assert again.returncode == 1
    assert "The server's data has changed since this copy began" in again.stdout
    assert "copy --start-over" in again.stdout


def test_a_pi_database_holding_other_tables_is_only_replaced_when_asked(machines):
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(machines.pi)))
    with machines.connect(machines.pi) as connection:
        connection.execute("CREATE TABLE public.kept (id int)")
    refused = machines.run("copy")
    assert refused.returncode == 1
    assert "To replace them, run: copy --start-over" in refused.stdout
    assert machines.rows(machines.pi, "SELECT to_regclass('public.kept') IS NOT NULL")[0][0]


def test_a_link_from_the_dump_is_added_unchecked_once():
    statement = ("ALTER TABLE ONLY v3_derived.body_mechanics\n    ADD CONSTRAINT body_mechanics_fkey "
                 "FOREIGN KEY (derived_generation_id, system_id64) REFERENCES "
                 "v3_derived.system_rating_vector(derived_generation_id, system_id64) "
                 "DEFERRABLE INITIALLY DEFERRED;")
    unchecked, was_checked = copy_to_pi.not_valid(statement)
    assert was_checked and unchecked.endswith("DEFERRABLE INITIALLY DEFERRED NOT VALID;")
    assert copy_to_pi.not_valid(unchecked) == (unchecked, False)


def test_an_extension_the_pis_postgresql_lacks_stops_the_copy_before_it_starts():
    class Facts:
        extensions = ["cube", "plpgsql", "postgis"]

    problems = copy_to_pi.Copier.both_problems({"extensions": {"cube", "plpgsql"}}, Facts())
    assert problems == ["The Pi's PostgreSQL lacks extensions the server's database uses: postgis."]
    assert copy_to_pi.Copier.both_problems({"extensions": {"cube", "plpgsql", "postgis"}}, Facts()) == []


def test_without_the_pis_key_it_says_where_it_looked(machines):
    result = machines.run("check", EDFINDER_COPY_KEY=str(machines.folder / "missing"))
    assert result.returncode == 1
    assert "The Pi's key for the move is not at %s." % (machines.folder / "missing") in result.stdout
    if os.geteuid() == 0:
        assert "type exit to get back to the user pi" in result.stdout
