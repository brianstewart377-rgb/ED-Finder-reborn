"""The nightly galaxy update: a Spansh file applied to the galaxy tables.

Runs on PostgreSQL 18 with every migration applied. The galaxy is the twelve
real systems of ``tests/fixtures/ratings_v4_sources`` as production held them
on 2026-08-24; the files applied to it are built from the same systems'
real Spansh records fetched on 2026-09-08, edited where a test says so.

Needs RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

os.environ.setdefault("CORS_ORIGINS", "https://galaxy-update-test.invalid")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "importer" / "src"))

from spansh_delta import apply as galaxy_update  # noqa: E402
from spansh_delta.normalize import CONTENT_TABLES  # noqa: E402
from tests.helpers.finder_seed import apply_migration, migration_names  # noqa: E402
from tests.ratings_v4_pg_fixture import canonical_database  # noqa: E402
from tests.test_spansh_delta import write_galaxy  # noqa: E402
from v3_spansh.adapter import SpanshAdapter, body_class  # noqa: E402

HD_38179, SOL, MAIA, THAILE, BORANN = 158872029, 10477373803, 8216113749, 31639717276, 5070611621289
SMOJOO = 3754967435947
SEPTEMBER = datetime(2026, 9, 8, 16, 0, tzinfo=timezone.utc)
EXTRA_TABLES = ("body_alias", "body_parent_evidence", "station_alias")
QUEUE = "v3_state.rescore_queue"


@dataclass
class Galaxy:
    connection: psycopg.Connection
    schema: str
    records: dict[int, dict]
    conninfo: str

    def one(self, query: str, *params):
        return self.connection.execute(query.replace("{s}", self.schema), params).fetchone()

    def all(self, query: str, *params):
        return self.connection.execute(query.replace("{s}", self.schema), params).fetchall()


def fill_classes(connection, schema: str, records) -> None:
    """Give every stored body the class its record states, as a galaxy holds
    them once its rows have been written since migration 027."""
    statement = sql.SQL("UPDATE {}.bodies SET body_class = %s WHERE body_pk = %s").format(
        sql.Identifier(schema))
    for record in records:
        for body in record["bodies"]:
            connection.execute(statement, (body_class(body.get("type"), body.get("subType")), body["id64"]))


@pytest.fixture(scope="module")
def template():
    """One migrated galaxy, built once; each test gets its own copy of it.

    Its bodies have their classes. A galaxy whose rows were all written
    before migration 027 has none of its own and takes them from its score
    rows: ``tests/test_galaxy_scores.py`` has that galaxy.
    """
    migrations = ROOT / "sql" / "v3" / "migrations"
    with canonical_database() as (connection, canonical, _, payloads):
        for name in migration_names():
            connection.execute((migrations / name).read_text(encoding="utf-8"))
        database = connection.info.dbname
        records = {payload["system"]["id64"]: payload["system"] for payload in payloads}
        fill_classes(connection, canonical["canonical_schema"], records.values())
        connection.close()
        yield database, canonical["canonical_schema"], records


@pytest.fixture
def galaxy(template):
    database, schema, records = template
    dsn = os.environ["RATINGS_V4_VALIDATION_DATABASE_URL"]
    name = "galaxy_update_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
            sql.Identifier(name), sql.Identifier(database)))
        try:
            conninfo = make_conninfo(dsn, dbname=name)
            with psycopg.connect(conninfo, autocommit=True) as connection:
                yield Galaxy(connection, schema, deepcopy(records), conninfo)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def run(galaxy: Galaxy, tmp_path: Path, records, *, mode: str = "apply",
        made_at: datetime = SEPTEMBER, **options):
    path = write_galaxy(tmp_path / f"galaxy_{uuid4().hex[:8]}.json.gz", records)
    return galaxy_update.run_file(galaxy.connection, path, mode=mode, made_at=made_at,
                                  progress=False, **options)


def snapshot(galaxy: Galaxy) -> dict[str, list[str]]:
    """Every galaxy row with its row version, plus the bookkeeping tables."""
    tables = {table: f"{galaxy.schema}.{table}" for table in (*CONTENT_TABLES, *EXTRA_TABLES)}
    tables.update(queue=QUEUE, runs="v3_source.source_run", files="v3_source.source_artifact",
                  words="v3_source.unmapped_vocabulary")
    return {
        name: sorted(row[0] for row in galaxy.connection.execute(
            f"SELECT xmin::text || ' ' || t::text FROM {relation} t"))
        for name, relation in tables.items()
    }


# For each galaxy table: the rows that belong to the systems given as %s.
BELONGS = {
    "systems": "t.id64 = ANY(%s)",
    "bodies": "t.system_id64 = ANY(%s)",
    "rings": "t.system_id64 = ANY(%s)",
    "stations": "t.system_id64 = ANY(%s)",
    "body_composition": "t.body_pk IN (SELECT body_pk FROM {s}.bodies WHERE system_id64 = ANY(%s))",
    "body_signal_current": "t.body_pk IN (SELECT body_pk FROM {s}.bodies WHERE system_id64 = ANY(%s))",
    "body_genus_current": "t.body_pk IN (SELECT body_pk FROM {s}.bodies WHERE system_id64 = ANY(%s))",
    "ring_signal_current": "t.ring_pk IN (SELECT ring_pk FROM {s}.rings WHERE system_id64 = ANY(%s))",
    "station_placement": "t.system_id64 = ANY(%s)",
    "station_service_current":
        "t.station_pk IN (SELECT station_pk FROM {s}.stations WHERE system_id64 = ANY(%s))",
    "station_economy_current":
        "t.station_pk IN (SELECT station_pk FROM {s}.stations WHERE system_id64 = ANY(%s))",
    "body_alias": "t.body_pk IN (SELECT body_pk FROM {s}.bodies WHERE system_id64 = ANY(%s))",
    "body_parent_evidence": "t.system_id64 = ANY(%s)",
    "station_alias": "t.station_pk IN (SELECT station_pk FROM {s}.stations WHERE system_id64 = ANY(%s))",
}


def stored_matches_file(galaxy: Galaxy, records) -> None:
    """What is stored and ACTIVE is exactly what the importer makes of the file."""
    adapter = SpanshAdapter(source_id=40, source_run_id=uuid4(), observed_at=SEPTEMBER)
    expected = {table: 0 for table in CONTENT_TABLES}
    ids = []
    for record in records:
        ids.append(record["id64"])
        batch = adapter.adapt_system(deepcopy(record))
        for table in CONTENT_TABLES:
            expected[table] += len(batch.rows.get(table, ()))
    stored = {}
    for table in CONTENT_TABLES:
        live = " AND t.lifecycle_state = 'ACTIVE'" if table in ("bodies", "rings", "stations") else ""
        stored[table] = galaxy.one(
            f"SELECT count(*) FROM {{s}}.{table} t WHERE {BELONGS[table]}{live}", ids)[0]
    assert stored == expected


def rows_of_other_systems(galaxy: Galaxy, system_id: int) -> dict[str, list[str]]:
    """Every galaxy row that does not belong to ``system_id``, with its row version."""
    return {
        table: sorted(row[0] for row in galaxy.all(
            f"SELECT t.xmin::text || ' ' || t::text FROM {{s}}.{table} t WHERE NOT ({where})", [system_id]))
        for table, where in BELONGS.items()
    }


def scans(galaxy: Galaxy, table: str) -> int:
    """How many times ``table`` has been read, by any kind of scan."""
    galaxy.connection.execute("SELECT pg_stat_force_next_flush()")
    galaxy.connection.execute("SELECT pg_stat_clear_snapshot()")
    return galaxy.one(
        "SELECT coalesce(seq_scan, 0) + coalesce(idx_scan, 0) FROM pg_stat_user_tables "
        "WHERE schemaname = %s AND relname = %s", galaxy.schema, table)[0]


def queue(galaxy: Galaxy) -> dict[int, list[str]]:
    return dict(galaxy.all(f"SELECT system_id64, changed_tables FROM {QUEUE}"))


def carrier_from(record: dict) -> dict:
    return next(station for station in record["stations"] if station.get("type") == "Drake-Class Carrier")


# -- migration 026 ----------------------------------------------------------


def test_migration_026_is_safe_to_run_twice_and_only_adds_the_queue(galaxy):
    migration = (ROOT / "sql" / "v3" / "migrations" / "026_v3_rescore_queue.sql").read_text(encoding="utf-8")
    statements = [line for line in migration.splitlines() if line and not line.lstrip().startswith("--")]
    assert statements[0] == "BEGIN;" and statements[-1] == "COMMIT;"
    assert not [line for line in statements if line.upper().startswith(("DROP", "ALTER", "DELETE", "UPDATE"))]
    tables_before = galaxy.all("SELECT schemaname, tablename FROM pg_tables ORDER BY 1, 2")
    galaxy.connection.execute(migration)
    assert galaxy.all("SELECT schemaname, tablename FROM pg_tables ORDER BY 1, 2") == tables_before


def test_the_queue_lists_a_system_once_and_must_say_what_changed(galaxy):
    run_id = galaxy.one("SELECT source_run_id FROM v3_source.source_run LIMIT 1")[0]
    galaxy.connection.execute(
        f"INSERT INTO {QUEUE} (system_id64, changed_tables, source_run_id) VALUES (1, '{{bodies}}', %s)",
        (run_id,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        galaxy.connection.execute(
            f"INSERT INTO {QUEUE} (system_id64, changed_tables, source_run_id) VALUES (1, '{{rings}}', %s)",
            (run_id,))
    with pytest.raises(psycopg.errors.CheckViolation):
        galaxy.connection.execute(
            f"INSERT INTO {QUEUE} (system_id64, changed_tables, source_run_id) VALUES (2, '{{}}', %s)",
            (run_id,))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        galaxy.connection.execute(
            f"INSERT INTO {QUEUE} (system_id64, changed_tables, source_run_id) VALUES (3, '{{bodies}}', %s)",
            (uuid4(),))


# -- migration 027: each body's class ---------------------------------------


COLUMNS = "SELECT table_schema, table_name, column_name FROM information_schema.columns"
CREATES_A_GALAXY = "SELECT pg_get_functiondef('v3_meta.create_canonical_generation_relations(uuid)'::regprocedure)"


def test_migration_027_adds_one_column_rewrites_no_row_and_new_galaxies_get_it():
    migrations = ROOT / "sql" / "v3" / "migrations"
    with canonical_database() as (connection, canonical, _, _payloads):
        schema = canonical["canonical_schema"]
        for name in migration_names(through=26):
            connection.execute((migrations / name).read_text(encoding="utf-8"))
        columns = set(connection.execute(COLUMNS).fetchall())
        versions = connection.execute(
            f"SELECT xmin::text, body_pk FROM {schema}.bodies ORDER BY body_pk").fetchall()

        apply_migration(connection, 27)

        after = set(connection.execute(COLUMNS).fetchall())
        assert after - columns == {(schema, "bodies", "body_class")} and not columns - after
        # Adding the column touched no row, and every row starts without a class.
        assert connection.execute(
            f"SELECT xmin::text, body_pk FROM {schema}.bodies ORDER BY body_pk").fetchall() == versions
        assert connection.execute(
            f"SELECT count(*), count(body_class) FROM {schema}.bodies").fetchone() == (len(versions), 0)
        # A class is never blank, and never longer than the score tables allow.
        for refused in (" ", "x" * 129):
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(f"UPDATE {schema}.bodies SET body_class = %s", (refused,))

        # Running it again changes nothing.
        function = connection.execute(CREATES_A_GALAXY).fetchone()[0]
        assert function.count("body_class text,") == 1
        apply_migration(connection, 27)
        assert set(connection.execute(COLUMNS).fetchall()) == after
        assert connection.execute(CREATES_A_GALAXY).fetchone()[0] == function

        # A galaxy created from now on has the column and its rule.
        generation = uuid4()
        connection.execute(
            """INSERT INTO v3_meta.canonical_generation(
                   generation_id, generation_key, relation_schema, manifest_sha256, build_source_run_id)
               SELECT %s, 'made_later', 'v3_gen_made_later', %s, build_source_run_id
                 FROM v3_meta.canonical_generation LIMIT 1""", (generation, bytes(32)))
        connection.execute("SELECT v3_meta.create_canonical_generation_relations(%s)", (generation,))
        assert ("v3_gen_made_later", "bodies", "body_class") in set(connection.execute(COLUMNS).fetchall())
        assert connection.execute(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'bodies_body_class_valid'").fetchone()[0] == 2


def test_every_body_written_gets_its_class(galaxy, tmp_path):
    record = deepcopy(galaxy.records[HD_38179])
    star = next(body for body in record["bodies"] if body.get("type") == "Star" and body.get("subType"))
    planet = next(body for body in record["bodies"] if body.get("type") == "Planet" and body.get("subType"))
    bare = next(body for body in record["bodies"]
                if body.get("type") == "Planet" and body is not planet)
    bare.pop("subType", None)
    new = as_new_system(record, 99158872029, "Test Sector AA-A a1")
    run(galaxy, tmp_path, [new])
    classes = dict(galaxy.all("SELECT source_body_id64, body_class FROM {s}.bodies WHERE system_id64 = %s",
                              new["id64"]))
    # Spansh's sub-type where it gives one; otherwise the plain type.
    assert classes[star["id64"] + 7] == star["subType"]
    assert classes[planet["id64"] + 7] == planet["subType"]
    assert classes[bare["id64"] + 7] == "planet"
    assert None not in classes.values() and len(classes) == len(record["bodies"])
    assert body_class("Barycentre", None) == "barycentre" and body_class("Belt Cluster", "") == "belt_cluster"
    assert body_class("Something new", None) == "Unknown" and body_class(None, " Icy body ") == "Icy body"
    # A sub-type that is blank, or is not text, counts as none.
    assert body_class("Planet", "  ") == "planet" and body_class("Planet", 5) == "planet"


def test_a_class_spansh_changed_with_nothing_else_is_a_change(galaxy, tmp_path):
    run(galaxy, tmp_path, galaxy.records.values())
    galaxy.connection.execute(f"DELETE FROM {QUEUE}")
    record = deepcopy(galaxy.records[HD_38179])
    body = next(body for body in record["bodies"] if body.get("type") == "Planet" and body.get("subType"))
    now = "Rocky body" if body["subType"] != "Rocky body" else "High metal content world"
    body["subType"] = now

    report = run(galaxy, tmp_path, [record, galaxy.records[SOL]], made_at=SEPTEMBER + timedelta(days=1))

    assert (report.changed, report.unchanged) == (1, 1)
    assert dict(report.systems_by_table) == {"bodies": 1}
    # The scores are worked out from the class, so the system waits for new ones.
    assert queue(galaxy) == {HD_38179: ["bodies"]}
    assert galaxy.one("SELECT body_class FROM {s}.bodies WHERE body_pk = %s", body["id64"])[0] == now


def test_a_stored_body_with_no_class_known_makes_its_system_count_as_changed_once(galaxy, tmp_path):
    """This galaxy has no scores, so a body row without a class of its own has
    no score row to take one from either. Writing the system fills it in."""
    run(galaxy, tmp_path, galaxy.records.values())
    galaxy.connection.execute(f"DELETE FROM {QUEUE}")
    galaxy.connection.execute(
        f"UPDATE {galaxy.schema}.bodies SET body_class = NULL WHERE system_id64 = %s", (HD_38179,))

    report = run(galaxy, tmp_path, galaxy.records.values(), made_at=SEPTEMBER + timedelta(days=1))

    assert (report.changed, report.unchanged) == (1, 11)
    assert queue(galaxy) == {HD_38179: ["bodies"]}
    assert galaxy.one("SELECT count(*) FROM {s}.bodies WHERE system_id64 = %s AND body_class IS NULL "
                      "AND lifecycle_state = 'ACTIVE'", HD_38179)[0] == 0
    again = run(galaxy, tmp_path, galaxy.records.values(), made_at=SEPTEMBER + timedelta(days=2))
    assert (again.changed, again.unchanged) == (0, 12)


def test_a_kind_of_body_that_is_never_scored_is_known_by_its_type_alone(galaxy, tmp_path):
    """A barycentre has no score row and Spansh gives it no sub-type: its
    class is its type, recorded or not. An empty one is not a difference."""
    with_barycentre = deepcopy(galaxy.records[HD_38179])
    with_barycentre["bodies"].append({
        "id64": with_barycentre["bodies"][0]["id64"] + (99 << 55), "bodyId": 99,
        "name": with_barycentre["name"] + " Barycentre", "type": "Barycentre",
        "updateTime": "2026-09-01 00:00:00+00"})
    run(galaxy, tmp_path, [with_barycentre])
    galaxy.connection.execute(f"DELETE FROM {QUEUE}")
    cleared = galaxy.all(
        "UPDATE {s}.bodies SET body_class = NULL WHERE system_id64 = %s AND body_class = 'barycentre' "
        "RETURNING body_pk", HD_38179)
    assert len(cleared) == 1
    before = snapshot(galaxy)

    report = run(galaxy, tmp_path, [with_barycentre], made_at=SEPTEMBER + timedelta(days=1))

    assert (report.changed, report.unchanged) == (0, 1)
    after = snapshot(galaxy)
    # Only the record of the run itself moved: no galaxy row was written.
    assert {name for name in after if after[name] != before[name]} == {"runs"}
    assert queue(galaxy) == {}


# -- the usual night --------------------------------------------------------


def test_check_says_what_would_change_and_writes_nothing(galaxy, tmp_path):
    before = snapshot(galaxy)
    report = run(galaxy, tmp_path, galaxy.records.values(), mode="check")
    assert snapshot(galaxy) == before
    assert (report.systems_read, report.changed, report.unchanged, report.new) == (12, 12, 0, 0)
    assert report.queued == 12 and not report.set_aside and not report.unusable
    # Nine of the twelve were re-scanned between August and September; the base
    # fixture holds no stations, so the ten systems that have any gain them.
    assert report.systems_by_table["bodies"] == 9
    assert report.systems_by_table["stations"] == 10
    text = "\n".join(report.lines())
    assert "Systems that would be rewritten" in text and "Rows that would be written" in text


def test_check_needs_no_queue_table_but_apply_does(template, tmp_path):
    migrations = ROOT / "sql" / "v3" / "migrations"
    with canonical_database() as (connection, canonical, _, payloads):
        for name in migration_names(through=25):
            connection.execute((migrations / name).read_text(encoding="utf-8"))
        path = write_galaxy(tmp_path / "galaxy.json.gz", [payload["system"] for payload in payloads])
        # Without the class column nothing runs, not even a check.
        with pytest.raises(galaxy_update.Refused, match="migration 027"):
            galaxy_update.run_file(connection, path, mode="check", made_at=SEPTEMBER, progress=False)
        apply_migration(connection, 27)
        report = galaxy_update.run_file(connection, path, mode="check", made_at=SEPTEMBER, progress=False)
        assert report.changed == 12
        with pytest.raises(galaxy_update.Refused, match="migration 026"):
            galaxy_update.run_file(connection, path, mode="apply", made_at=SEPTEMBER, progress=False)
        assert connection.execute(
            "SELECT count(*) FROM v3_source.source_run WHERE acquisition_kind = 'DELTA'").fetchone()[0] == 0


def test_apply_makes_the_galaxy_match_the_file_and_a_second_run_finds_nothing_to_do(galaxy, tmp_path):
    planned = run(galaxy, tmp_path, galaxy.records.values(), mode="check")
    path = write_galaxy(tmp_path / "galaxy_1day.json.gz", galaxy.records.values())
    report = galaxy_update.run_file(galaxy.connection, path, mode="apply", made_at=SEPTEMBER, progress=False)

    # The read-only check predicted exactly what apply then did.
    for name in ("systems_read", "changed", "new", "unchanged", "queued", "carriers_moved"):
        assert getattr(report, name) == getattr(planned, name), name
    assert report.rows == planned.rows and report.retired == planned.retired
    assert report.systems_by_table == planned.systems_by_table

    stored_matches_file(galaxy, galaxy.records.values())
    # Sol: every body is where the September record says it is.
    earth = next(body for body in galaxy.records[SOL]["bodies"] if body["name"] == "Earth")
    assert galaxy.one("SELECT mean_anomaly_deg FROM {s}.bodies WHERE body_pk = %s",
                      earth["id64"])[0] == pytest.approx(earth["meanAnomaly"])
    # Fleet carriers are stored as carriers.
    assert galaxy.one("SELECT count(*) FROM {s}.stations WHERE system_id64 = %s AND is_fleet_carrier",
                      MAIA)[0] == 45

    # The run is recorded as a change file from Spansh, and the rows point at it.
    kind, state, done, summary, file_name, size = galaxy.one(
        """SELECT r.acquisition_kind, r.run_state, r.completed_at IS NOT NULL, r.scope_contract,
                  a.storage_locator, a.size_bytes
             FROM v3_source.source_run r JOIN v3_source.source_artifact a USING (artifact_id)
            WHERE r.acquisition_kind = 'DELTA'""")
    assert (kind, state, done, file_name, size) == ("DELTA", "SUCCEEDED", True, path.name, path.stat().st_size)
    assert summary["changed"] == 12 and summary["updater"] == galaxy_update.UPDATER_VERSION
    assert galaxy.one(
        """SELECT count(*) FROM {s}.systems s JOIN v3_source.source_run r USING (source_run_id)
            WHERE r.acquisition_kind = 'DELTA' AND s.freshness_checked_at = %s""", SEPTEMBER)[0] == 12

    # Every rewritten system waits for new scores, listing what changed.
    waiting = queue(galaxy)
    assert set(waiting) == set(galaxy.records)
    # HD 38179 was not re-scanned: only its ring signals, which the August
    # fixture does not hold, are new.
    assert waiting[HD_38179] == ["ring_signal_current"]
    assert "stations" in waiting[MAIA] and "bodies" in waiting[MAIA]

    # Second run of the same file: nothing to do, and not one row is touched.
    before = snapshot(galaxy)
    again = galaxy_update.run_file(galaxy.connection, path, mode="apply", made_at=SEPTEMBER, progress=False)
    assert (again.unchanged, again.changed, again.new, again.queued) == (12, 0, 0, 0)
    after = snapshot(galaxy)
    for name in after:
        if name != "runs":  # the run row records the second attempt
            assert after[name] == before[name], name
    assert galaxy.one("SELECT count(*) FROM v3_source.source_run WHERE acquisition_kind = 'DELTA'")[0] == 1


def test_only_the_system_that_changed_is_rewritten(galaxy, tmp_path):
    run(galaxy, tmp_path, galaxy.records.values())
    galaxy.connection.execute(f"DELETE FROM {QUEUE}")
    before = rows_of_other_systems(galaxy, HD_38179)

    changed = deepcopy(galaxy.records)
    body = changed[HD_38179]["bodies"][1]
    body["surfaceTemperature"] = 1234.5
    report = run(galaxy, tmp_path, changed.values(), made_at=SEPTEMBER + timedelta(days=1))

    assert (report.changed, report.unchanged) == (1, 11)
    assert queue(galaxy) == {HD_38179: ["bodies"]}
    assert galaxy.one("SELECT surface_temperature_k FROM {s}.bodies WHERE body_pk = %s",
                      body["id64"])[0] == 1234.5
    # Not one row of any other system was touched.
    assert rows_of_other_systems(galaxy, HD_38179) == before
    assert galaxy.all("SELECT DISTINCT system_id64 FROM {s}.bodies WHERE freshness_checked_at = %s",
                      SEPTEMBER + timedelta(days=1)) == [(HD_38179,)]


def test_what_changed_adds_up_while_a_system_waits_for_new_scores(galaxy, tmp_path):
    run(galaxy, tmp_path, [galaxy.records[THAILE]])
    galaxy.connection.execute(f"DELETE FROM {QUEUE}")
    record = deepcopy(galaxy.records[THAILE])
    record["bodies"][0]["surfaceTemperature"] = 4321.0
    run(galaxy, tmp_path, [record], made_at=SEPTEMBER + timedelta(days=1))
    first_queued = galaxy.one(f"SELECT first_queued_at FROM {QUEUE}")[0]
    record["stations"].remove(carrier_from(record))
    run(galaxy, tmp_path, [record], made_at=SEPTEMBER + timedelta(days=2))
    tables, first, last = galaxy.one(
        f"SELECT changed_tables, first_queued_at, last_queued_at FROM {QUEUE} WHERE system_id64 = %s", THAILE)
    assert tables == sorted(tables) and {"bodies", "stations"} <= set(tables)
    assert first == first_queued and last > first


# -- rows that disappear ----------------------------------------------------


def test_a_body_no_longer_listed_is_retired_not_deleted_and_can_come_back(galaxy, tmp_path):
    run(galaxy, tmp_path, galaxy.records.values())
    full = galaxy.records[SMOJOO]
    # A body that has rings, so the rings have to go with it.
    ringed = next(body for body in full["bodies"] if body.get("rings"))
    pk, slot = ringed["id64"], ringed["bodyId"]
    evidence = galaxy.one("SELECT count(*) FROM {s}.body_parent_evidence")[0]
    assert galaxy.one("SELECT count(*) FROM {s}.body_parent_evidence WHERE body_pk = %s", pk)[0] == 1

    without = deepcopy(full)
    without["bodies"] = [body for body in without["bodies"] if body["id64"] != pk]
    # Nothing that points at a body may be touched, or even looked at: on
    # production body_parent_evidence is 116 GB with no index for that lookup.
    scans_before = scans(galaxy, "body_parent_evidence")
    report = run(galaxy, tmp_path, [without], made_at=SEPTEMBER + timedelta(days=1))
    assert scans(galaxy, "body_parent_evidence") == scans_before

    assert report.changed == 1 and report.retired["bodies"] == 1
    assert report.retired["rings"] == len(ringed["rings"])
    state, retired_at, reason, missing_run, frontier = galaxy.one(
        """SELECT lifecycle_state, retired_at, retired_reason, first_missing_run_id, frontier_body_id
             FROM {s}.bodies WHERE body_pk = %s""", pk)
    assert (state, reason, frontier) == ("RETIRED", galaxy_update.RETIRED_REASON, None)
    assert retired_at == SEPTEMBER + timedelta(days=1) and missing_run is not None
    assert {row[0] for row in galaxy.all("SELECT lifecycle_state FROM {s}.rings WHERE body_pk = %s", pk)} \
        == {"RETIRED"}
    for table in ("body_composition", "body_signal_current", "body_genus_current"):
        assert galaxy.one(f"SELECT count(*) FROM {{s}}.{table} WHERE body_pk = %s", pk)[0] == 0
    stored_matches_file(galaxy, [without])
    assert galaxy.one("SELECT count(*) FROM {s}.body_parent_evidence")[0] == evidence
    # (The scan counter does notice such a lookup: deleting the row makes one.)
    scans_before = scans(galaxy, "body_parent_evidence")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        galaxy.connection.execute(f"DELETE FROM {galaxy.schema}.bodies WHERE body_pk = %s", (pk,))
    assert scans(galaxy, "body_parent_evidence") > scans_before

    # The same file again: the retired body is not a difference.
    again = run(galaxy, tmp_path, [without], made_at=SEPTEMBER + timedelta(days=2))
    assert (again.unchanged, again.changed) == (1, 0)

    # Listed again: it is simply ACTIVE again, with its slot number back.
    back = run(galaxy, tmp_path, [full], made_at=SEPTEMBER + timedelta(days=3))
    assert back.changed == 1 and back.retired["bodies"] == 0
    assert galaxy.one(
        "SELECT lifecycle_state, retired_at, first_missing_run_id, frontier_body_id FROM {s}.bodies "
        "WHERE body_pk = %s", pk) == ("ACTIVE", None, None, slot)
    stored_matches_file(galaxy, [full])


def test_a_station_no_longer_listed_is_retired_and_stops_counting(galaxy, tmp_path):
    run(galaxy, tmp_path, [galaxy.records[THAILE]])
    record = deepcopy(galaxy.records[THAILE])
    gone = carrier_from(record)
    record["stations"].remove(gone)
    report = run(galaxy, tmp_path, [record], made_at=SEPTEMBER + timedelta(days=1))
    assert report.retired["stations"] == 1
    assert galaxy.one("SELECT lifecycle_state, system_id64 FROM {s}.stations WHERE station_pk = %s",
                      gone["id"]) == ("RETIRED", THAILE)
    for table in ("station_placement", "station_service_current", "station_economy_current"):
        assert galaxy.one(f"SELECT count(*) FROM {{s}}.{table} WHERE station_pk = %s", gone["id"])[0] == 0
    stored_matches_file(galaxy, [record])


# -- fleet carriers ---------------------------------------------------------


@pytest.mark.parametrize("order", ["left first", "arrived first", "only the arrival is in the file"])
def test_a_fleet_carrier_that_moves_keeps_one_row_and_both_systems_are_queued(galaxy, tmp_path, order):
    run(galaxy, tmp_path, [galaxy.records[THAILE], galaxy.records[HD_38179]])
    galaxy.connection.execute(f"DELETE FROM {QUEUE}")
    left, arrived = deepcopy(galaxy.records[THAILE]), deepcopy(galaxy.records[HD_38179])
    carrier = carrier_from(left)
    left["stations"].remove(carrier)
    arrived.setdefault("stations", []).append(carrier)
    records = {"left first": [left, arrived], "arrived first": [arrived, left],
               "only the arrival is in the file": [arrived]}[order]

    report = run(galaxy, tmp_path, records, made_at=SEPTEMBER + timedelta(days=1))

    assert not report.set_aside
    assert galaxy.all("SELECT system_id64, lifecycle_state FROM {s}.stations WHERE station_pk = %s",
                      carrier["id"]) == [(HD_38179, "ACTIVE")]
    assert galaxy.all("SELECT system_id64 FROM {s}.station_placement WHERE station_pk = %s",
                      carrier["id"]) == [(HD_38179,)]
    waiting = queue(galaxy)
    assert "stations" in waiting[HD_38179] and "stations" in waiting[THAILE]
    assert report.carriers_moved == 1
    stored_matches_file(galaxy, [left, arrived])
    assert galaxy.one("SELECT count(*) FROM {s}.stations WHERE system_id64 = %s AND lifecycle_state = 'ACTIVE'",
                      THAILE)[0] == 1


def test_a_carrier_listed_in_two_systems_at_once_ends_in_the_one_listed_last(galaxy, tmp_path):
    here, there = deepcopy(galaxy.records[THAILE]), deepcopy(galaxy.records[HD_38179])
    carrier = carrier_from(here)
    there.setdefault("stations", []).append(carrier)
    report = run(galaxy, tmp_path, [here, there])
    assert not report.set_aside and report.changed == 2
    assert galaxy.all("SELECT system_id64, lifecycle_state FROM {s}.stations WHERE station_pk = %s",
                      carrier["id"]) == [(HD_38179, "ACTIVE")]
    assert galaxy.all("SELECT system_id64 FROM {s}.station_placement WHERE station_pk = %s",
                      carrier["id"]) == [(HD_38179,)]
    assert set(queue(galaxy)) == {THAILE, HD_38179}


# -- systems that cannot be applied -----------------------------------------


def as_new_system(record: dict, id64: int, name: str) -> dict:
    """The same real system under a new identity, as a newly discovered one."""
    copy = deepcopy(record)
    copy["id64"], copy["name"] = id64, name
    copy["coords"] = {axis: value + 500.0 for axis, value in copy["coords"].items()}
    for body in copy["bodies"]:
        body["id64"] += 7
        body["name"] = body["name"].replace(record["name"], name)
        for ring in [*(body.get("rings") or []), *(body.get("belts") or [])]:
            if "id64" in ring:
                ring["id64"] += 7
    return copy


def test_a_new_system_is_added_and_queued(galaxy, tmp_path):
    new = as_new_system(galaxy.records[HD_38179], 99158872029, "Test Sector AA-A a1")
    report = run(galaxy, tmp_path, [new])
    assert not report.set_aside and not report.unusable, (report.set_aside, report.unusable)
    assert (report.new, report.changed, report.queued) == (1, 0, 1)
    assert galaxy.one("SELECT name, loaded_body_count FROM {s}.systems WHERE id64 = %s",
                      new["id64"]) == ("Test Sector AA-A a1", 14)
    stored_matches_file(galaxy, [new])
    assert "systems" in queue(galaxy)[new["id64"]] and "bodies" in queue(galaxy)[new["id64"]]


def test_a_system_claiming_another_systems_bodies_is_set_aside_and_the_rest_is_applied(galaxy, tmp_path):
    thief = as_new_system(galaxy.records[HD_38179], 99158872029, "Test Sector AA-A a1")
    for body, original in zip(thief["bodies"], galaxy.records[HD_38179]["bodies"], strict=True):
        body["id64"] = original["id64"]
    before = galaxy.all("SELECT body_pk, system_id64, xmin::text FROM {s}.bodies WHERE system_id64 = %s",
                        HD_38179)
    report = run(galaxy, tmp_path, [galaxy.records[SOL], thief, galaxy.records[MAIA]])
    assert list(report.set_aside) == [thief["id64"]]
    assert "already belongs to system 158872029" in report.set_aside[thief["id64"]]
    assert (report.changed, report.new, report.queued) == (2, 0, 2)
    assert galaxy.one("SELECT count(*) FROM {s}.systems WHERE id64 = %s", thief["id64"])[0] == 0
    assert galaxy.all("SELECT body_pk, system_id64, xmin::text FROM {s}.bodies WHERE system_id64 = %s",
                      HD_38179) == before
    stored_matches_file(galaxy, [galaxy.records[SOL], galaxy.records[MAIA]])
    assert "set aside after an error" in "\n".join(report.lines())


def test_a_record_the_importer_refuses_is_counted_and_the_rest_is_applied(galaxy, tmp_path):
    broken = deepcopy(galaxy.records[HD_38179])
    del broken["coords"]
    report = run(galaxy, tmp_path, [broken, galaxy.records[SOL]])
    assert sum(report.unusable.values()) == 1 and report.changed == 1
    assert "missing name or finite coordinates" in next(iter(report.unusable))
    assert set(queue(galaxy)) == {SOL}


def test_a_system_the_database_refuses_is_set_aside_alone(galaxy, tmp_path):
    bad = deepcopy(galaxy.records[HD_38179])
    bad["bodies"][1]["orbitalEccentricity"] = 1.5  # the table allows 0 to 1
    report = run(galaxy, tmp_path, [galaxy.records[SOL], bad, galaxy.records[THAILE]])
    assert list(report.set_aside) == [HD_38179]
    assert "bodies_eccentricity_check" in report.set_aside[HD_38179]
    assert report.changed == 2 and set(queue(galaxy)) == {SOL, THAILE}
    stored_matches_file(galaxy, [galaxy.records[SOL], galaxy.records[THAILE]])
    # The refused system is exactly as it was.
    assert galaxy.one("SELECT count(*) FROM {s}.bodies b JOIN v3_source.source_run r USING (source_run_id) "
                      "WHERE b.system_id64 = %s AND r.acquisition_kind = 'DELTA'", HD_38179)[0] == 0


def test_a_class_too_long_to_be_scored_sets_only_that_system_aside(galaxy, tmp_path):
    """The score tables take a class of up to 128 characters, and the galaxy
    tables keep the same limit, so nothing gets in that could never be scored."""
    bad = deepcopy(galaxy.records[HD_38179])
    next(body for body in bad["bodies"] if body.get("subType"))["subType"] = "x" * 129
    report = run(galaxy, tmp_path, [galaxy.records[SOL], bad])
    assert list(report.set_aside) == [HD_38179]
    assert "bodies_body_class_valid" in report.set_aside[HD_38179]
    assert report.changed == 1 and set(queue(galaxy)) == {SOL}


def test_too_many_failures_stop_the_run_and_say_so(galaxy, tmp_path, monkeypatch):
    monkeypatch.setattr(galaxy_update, "MAX_SET_ASIDE", 0)
    monkeypatch.setattr(galaxy_update, "MAX_SET_ASIDE_SHARE", 0.0)
    monkeypatch.setattr(galaxy_update, "BATCH_SYSTEMS", 2)
    bad = deepcopy(galaxy.records[HD_38179])
    bad["bodies"][1]["orbitalEccentricity"] = 1.5
    with pytest.raises(galaxy_update.UpdateError, match="1 systems could not be applied"):
        run(galaxy, tmp_path, [galaxy.records[SOL], bad, galaxy.records[THAILE], galaxy.records[MAIA]])
    state, code = galaxy.one(
        "SELECT run_state, failure_code FROM v3_source.source_run WHERE acquisition_kind = 'DELTA'")
    assert (state, code) == ("FAILED", "GALAXY_UPDATE_FAILED")
    # The batch before the failure is complete and stays; later ones never ran.
    assert set(queue(galaxy)) == {SOL}


def test_any_other_database_error_stops_the_run_at_once(galaxy, tmp_path):
    # Not a refused row but a broken database: nothing should be "set aside".
    galaxy.connection.execute("ALTER TABLE v3_state.rescore_queue RENAME COLUMN changed_tables TO renamed")
    before = snapshot(galaxy)
    with pytest.raises(psycopg.errors.UndefinedColumn):
        run(galaxy, tmp_path, [galaxy.records[SOL], galaxy.records[THAILE]])
    after = snapshot(galaxy)
    assert all(after[name] == before[name] for name in CONTENT_TABLES)
    assert galaxy.one(
        "SELECT run_state FROM v3_source.source_run WHERE acquisition_kind = 'DELTA'")[0] == "FAILED"


# -- order and repeats ------------------------------------------------------


def test_an_older_file_never_overwrites_what_a_newer_file_wrote(galaxy, tmp_path):
    newer = deepcopy(galaxy.records[HD_38179])
    newer["bodies"][1]["surfaceTemperature"] = 1234.5
    run(galaxy, tmp_path, [newer], made_at=SEPTEMBER + timedelta(days=5))
    before = snapshot(galaxy)
    report = run(galaxy, tmp_path, [galaxy.records[HD_38179]], made_at=SEPTEMBER)
    assert (report.older, report.changed, report.queued) == (1, 0, 0)
    after = snapshot(galaxy)
    assert all(after[name] == before[name] for name in after if name not in ("runs", "files"))
    assert "already newer here" in "\n".join(report.lines())


def test_a_system_listed_twice_ends_as_its_last_listing(galaxy, tmp_path):
    first, last = deepcopy(galaxy.records[HD_38179]), deepcopy(galaxy.records[HD_38179])
    first["bodies"][1]["surfaceTemperature"] = 1111.0
    last["bodies"][1]["surfaceTemperature"] = 2222.0
    report = run(galaxy, tmp_path, [first, last])
    assert report.repeated == 1 and report.systems_read == 2
    assert galaxy.one("SELECT surface_temperature_k FROM {s}.bodies WHERE body_pk = %s",
                      last["bodies"][1]["id64"])[0] == 2222.0


def test_a_run_that_stopped_half_way_is_finished_by_running_the_file_again(galaxy, tmp_path, monkeypatch):
    monkeypatch.setattr(galaxy_update, "BATCH_SYSTEMS", 5)
    path = write_galaxy(tmp_path / "galaxy_1day.json.gz", galaxy.records.values())
    real, calls = galaxy_update.process_batch, []

    def stop_on_second_batch(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("the machine was switched off")
        return real(*args, **kwargs)

    monkeypatch.setattr(galaxy_update, "process_batch", stop_on_second_batch)
    with pytest.raises(OSError):
        galaxy_update.run_file(galaxy.connection, path, mode="apply", made_at=SEPTEMBER, progress=False)
    assert galaxy.one("SELECT run_state FROM v3_source.source_run WHERE acquisition_kind = 'DELTA'")[0] \
        == "FAILED"
    assert len(queue(galaxy)) == 5

    monkeypatch.setattr(galaxy_update, "process_batch", real)
    report = galaxy_update.run_file(galaxy.connection, path, mode="apply", made_at=SEPTEMBER, progress=False)
    assert (report.unchanged, report.changed) == (5, 7)
    assert galaxy.all("SELECT run_state FROM v3_source.source_run WHERE acquisition_kind = 'DELTA'") \
        == [("SUCCEEDED",)]
    assert len(queue(galaxy)) == 12
    stored_matches_file(galaxy, galaxy.records.values())


def test_a_run_left_running_by_a_crash_is_closed_by_the_next_one(galaxy, tmp_path):
    run(galaxy, tmp_path, [galaxy.records[HD_38179]])
    galaxy.connection.execute(
        "UPDATE v3_source.source_run SET run_state = 'RUNNING', completed_at = NULL "
        "WHERE acquisition_kind = 'DELTA'")
    run(galaxy, tmp_path, [galaxy.records[SOL]], made_at=SEPTEMBER + timedelta(days=1))
    assert sorted(row[0] for row in galaxy.all(
        "SELECT run_state FROM v3_source.source_run WHERE acquisition_kind = 'DELTA'")) \
        == ["FAILED", "SUCCEEDED"]


def test_the_limit_stops_early_and_records_a_partial_run(galaxy, tmp_path):
    report = run(galaxy, tmp_path, galaxy.records.values(), limit=5)
    assert report.systems_read == 5 and report.stopped_early
    assert galaxy.one("SELECT run_state FROM v3_source.source_run WHERE acquisition_kind = 'DELTA'")[0] \
        == "PARTIAL"
    assert "stopped at the limit" in report.lines()[0]


def test_two_updates_cannot_write_at_once(galaxy, tmp_path):
    with psycopg.connect(galaxy.conninfo, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (galaxy_update.LOCK_KEY,))
        with pytest.raises(galaxy_update.UpdateError, match="another galaxy update is running"):
            run(galaxy, tmp_path, [galaxy.records[HD_38179]])
        # Checking is read only, so it is allowed alongside.
        assert run(galaxy, tmp_path, [galaxy.records[HD_38179]], mode="check").changed == 1
    assert queue(galaxy) == {}
    assert run(galaxy, tmp_path, [galaxy.records[HD_38179]]).changed == 1


def test_a_run_that_cannot_start_leaves_no_lock_behind(galaxy, tmp_path):
    galaxy.connection.execute("UPDATE v3_source.source SET source_code = 'renamed' WHERE source_code = 'spansh'")
    with pytest.raises(galaxy_update.Refused, match="no 'spansh' source"):
        run(galaxy, tmp_path, [galaxy.records[HD_38179]])
    galaxy.connection.execute("UPDATE v3_source.source SET source_code = 'spansh' WHERE source_code = 'renamed'")
    assert run(galaxy, tmp_path, [galaxy.records[HD_38179]]).changed == 1


# -- a database that is not what the tool expects ---------------------------


def test_a_region_filled_in_by_another_job_is_kept_and_is_not_a_difference(galaxy, tmp_path):
    run(galaxy, tmp_path, [galaxy.records[HD_38179]])
    galaxy.connection.execute(
        "INSERT INTO v3_vocab.galaxy_region (galaxy_region_id, public_code, display_name) "
        "VALUES (7, 'test_region', 'Test Region')")
    galaxy.connection.execute(
        f"UPDATE {galaxy.schema}.systems SET galaxy_region_id = 7 WHERE id64 = %s", (HD_38179,))
    assert run(galaxy, tmp_path, [galaxy.records[HD_38179]], mode="check").unchanged == 1
    record = deepcopy(galaxy.records[HD_38179])
    record["bodies"][1]["surfaceTemperature"] = 1234.5
    assert run(galaxy, tmp_path, [record], made_at=SEPTEMBER + timedelta(days=1)).changed == 1
    assert galaxy.one("SELECT galaxy_region_id FROM {s}.systems WHERE id64 = %s", HD_38179)[0] == 7


@pytest.mark.parametrize("damage, message", [
    ("CREATE FUNCTION public.noop() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NULL; END'; "
     "CREATE TRIGGER surprise AFTER UPDATE ON {s}.bodies FOR EACH STATEMENT EXECUTE FUNCTION public.noop()",
     "table bodies has a trigger (surprise)"),
    ("ALTER TABLE {s}.rings ADD COLUMN colour text NOT NULL DEFAULT 'red'; "
     "ALTER TABLE {s}.rings ALTER COLUMN colour DROP DEFAULT",
     "table rings has required columns this tool does not fill: ['colour']"),
    ("ALTER TABLE {s}.station_economy_current DROP COLUMN is_secondary",
     "table station_economy_current lacks columns ['is_secondary']"),
    ("DELETE FROM v3_vocab.station_service WHERE station_service_id = "
     "(SELECT max(station_service_id) FROM v3_vocab.station_service)",
     "vocabulary v3_vocab.station_service lacks ids"),
    ("UPDATE v3_vocab.atmosphere_classification SET public_code = 'renumbered' "
     "WHERE atmosphere_classification_id = 3",
     "vocabulary v3_vocab.atmosphere_classification id 3 is 'renumbered' in the database but"),
    ("UPDATE v3_meta.current_canonical_generation SET singleton = singleton; "
     "DROP TABLE v3_state.rescore_queue",
     "v3_state.rescore_queue does not exist (migration 026 has not been applied)"),
])
def test_a_database_in_an_unexpected_shape_is_refused_before_anything_is_written(
        galaxy, tmp_path, damage, message):
    galaxy.connection.execute(damage.replace("{s}", galaxy.schema))
    with pytest.raises(galaxy_update.Refused) as refusal:
        run(galaxy, tmp_path, [galaxy.records[HD_38179]])
    assert message in str(refusal.value)
    assert galaxy.one("SELECT count(*) FROM v3_source.source_run WHERE acquisition_kind = 'DELTA'")[0] == 0


def test_the_importers_vocabulary_is_exactly_what_production_holds(galaxy):
    """The fixture's vocabulary rows are production's own. Every id the
    importer can write must exist there under the same code, with none over."""
    from v3_spansh.adapter import VOCABULARY

    for domain, table in galaxy_update.VOCABULARY_TABLES.items():
        stored = dict(galaxy.all(f"SELECT {table}_id, public_code FROM v3_vocab.{table}"))
        known: dict[int, set[str]] = {}
        for token, (identifier, _) in VOCABULARY[domain].items():
            known.setdefault(identifier, set()).add(galaxy_update.vocabulary_code(token))
        assert set(stored) == set(known), domain
        assert all(stored[identifier] in codes for identifier, codes in known.items()), domain
    assert set(galaxy_update.VOCABULARY_TABLES) == {
        domain for domain in VOCABULARY if domain in galaxy_update.VOCABULARY_TABLES}


# -- the command ------------------------------------------------------------


def _environment(monkeypatch, conninfo: str) -> None:
    settings = conninfo_to_dict(conninfo)
    for name in [name for name in os.environ if name.startswith("PG")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("PGHOST", str(settings["host"]))
    monkeypatch.setenv("PGPORT", str(settings.get("port", 5432)))
    monkeypatch.setenv("PGUSER", str(settings["user"]))
    monkeypatch.setenv("PGDATABASE", str(settings["dbname"]))
    if settings.get("password"):
        monkeypatch.setenv("PGPASSWORD", str(settings["password"]))


def test_command_checks_then_applies_and_prints_plain_results(galaxy, tmp_path, monkeypatch, capsys):
    _environment(monkeypatch, galaxy.conninfo)
    path = write_galaxy(tmp_path / "galaxy_1day.json.gz", galaxy.records.values())

    assert galaxy_update.main(["check", str(path), "--made-at", "2026-09-08T16:00:00+00:00"]) == 0
    out = capsys.readouterr().out
    assert "Galaxy update (check): galaxy_1day.json.gz" in out
    assert out.rstrip().endswith("CHECKED: nothing was changed.")
    assert queue(galaxy) == {}

    assert galaxy_update.main(["apply", str(path), "--made-at", "2026-09-08T16:00:00+00:00"]) == 0
    out = capsys.readouterr().out
    assert "Systems rewritten, by what changed:" in out and out.rstrip().endswith("DONE")
    assert len(queue(galaxy)) == 12
    assert galaxy.one("SELECT DISTINCT freshness_checked_at FROM {s}.systems")[0] == SEPTEMBER


def test_command_failures_are_plain_and_show_no_connection_details(galaxy, tmp_path, monkeypatch, capsys):
    path = write_galaxy(tmp_path / "galaxy_1day.json.gz", [galaxy.records[HD_38179]])
    _environment(monkeypatch, galaxy.conninfo)

    assert galaxy_update.main(["apply", str(tmp_path / "missing.json.gz")]) == 1
    assert "there is no file" in capsys.readouterr().out

    galaxy.connection.execute("DROP TABLE v3_state.rescore_queue")
    assert galaxy_update.main(["apply", str(path)]) == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and "migration 026" in out and out.rstrip().endswith("Nothing was changed.")

    monkeypatch.setenv("PGPORT", "1")
    monkeypatch.setenv("PGPASSWORD", "hunter2-not-a-real-secret")
    assert galaxy_update.main(["check", str(path)]) == galaxy_update.EXIT_NO_CONNECTION
    out = capsys.readouterr().out
    assert "Could not connect to the database" in out
    assert "hunter2" not in out and "127.0.0.1" not in out


def test_command_can_use_the_apis_own_database_setting(galaxy, tmp_path, monkeypatch, capsys):
    path = write_galaxy(tmp_path / "galaxy_1day.json.gz", [galaxy.records[HD_38179]])
    for name in [name for name in os.environ if name.startswith("PG")]:
        monkeypatch.delenv(name)
    settings = conninfo_to_dict(galaxy.conninfo)
    url = "postgresql://%s@%s:%s/%s" % (
        settings["user"], settings["host"], settings.get("port", 5432), settings["dbname"])

    # Without the switch, DATABASE_URL is ignored (and here nothing else is set).
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("PGPORT", "1")
    monkeypatch.setenv("PGHOST", "127.0.0.1")
    assert galaxy_update.main(["check", str(path)]) == galaxy_update.EXIT_NO_CONNECTION
    capsys.readouterr()

    monkeypatch.setenv("EDFINDER_GALAXY_USE_DATABASE_URL", "1")
    assert galaxy_update.main(["apply", str(path), "--made-at", "2026-09-08T16:00:00+00:00"]) == 0
    assert capsys.readouterr().out.rstrip().endswith("DONE")
    assert set(queue(galaxy)) == {HD_38179}

    monkeypatch.setenv("DATABASE_URL", "postgresql://app:hunter2-not-real@127.0.0.1:1/nowhere")
    assert galaxy_update.main(["check", str(path)]) == galaxy_update.EXIT_NO_CONNECTION
    out = capsys.readouterr().out
    assert "Could not connect to the database" in out and "hunter2" not in out and "nowhere" not in out

    monkeypatch.delenv("DATABASE_URL")
    assert galaxy_update.main(["check", str(path)]) == galaxy_update.EXIT_NO_CONNECTION
    assert "DATABASE_URL is not set" in capsys.readouterr().out


def test_made_at_needs_a_time_zone(tmp_path, capsys):
    with pytest.raises(SystemExit):
        galaxy_update.main(["check", str(tmp_path / "x.json.gz"), "--made-at", "2026-09-08T16:00:00"])
    assert "time zone" in capsys.readouterr().err
