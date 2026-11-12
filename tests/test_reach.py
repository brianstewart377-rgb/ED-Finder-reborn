"""Reach: which systems are within 500 ly and 250 ly of an inhabited system,
and the nightly scoring keeping to them (spansh_delta.reach, migration 028).

The galaxy is the twelve real fixture systems, with their real positions:
eight lie within 500 ly of Sol (five of them within 250 ly), and four lie
further out (Praea Euq WV-W b2-2 at 678 ly, and three thousands of ly away).
Which systems are inhabited is set in v3_state.system_state, as the nightly
state refresh would set it.

Runs on PostgreSQL 18 with every migration applied; needs
RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import sys
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

os.environ.setdefault("CORS_ORIGINS", "https://reach-test.invalid")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "importer" / "src"))

from spansh_delta import apply as galaxy_update  # noqa: E402
from spansh_delta import reach  # noqa: E402
from spansh_delta import scores  # noqa: E402
from tests.helpers import finder_seed  # noqa: E402
from tests.test_galaxy_scores import ANNOUNCE, SCORE_TABLES, template  # noqa: E402,F401
from tests.test_galaxy_update import (  # noqa: E402
    HD_38179, SOL, THAILE, Galaxy, _environment, as_new_system, queue, run,
)

ACHENAR, MAIA, BORANN = 164098653, 8216113749, 5070611621289
PRAEA = 5076786292249  # Praea Euq WV-W b2-2: 678 ly from Sol
NEAR_SOL = {SOL, 1109989017963, 2037701937507, BORANN, ACHENAR}  # within 250 ly
FURTHER = {1457973727914, 7780836610810, MAIA}  # 287, 298 and 383 ly from Sol
WITHIN_500_OF_SOL = NEAR_SOL | FURTHER


@pytest.fixture
def pi(template):  # noqa: F811  (the fixture imported above)
    """A copy of the scored galaxy as the Pi has it: it keeps reach."""
    database, schema, records, generation = template
    dsn = os.environ["RATINGS_V4_VALIDATION_DATABASE_URL"]
    name = "reach_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
            sql.Identifier(name), sql.Identifier(database)))
        try:
            conninfo = make_conninfo(dsn, dbname=name)
            with psycopg.connect(conninfo, autocommit=True) as connection:
                found = Galaxy(connection, schema, deepcopy(records), conninfo)
                found.generation = generation
                yield found
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def inhabit(galaxy: Galaxy, system_id64: int, x: float, y: float, z: float, population: int = 1000) -> None:
    """Say a system is inhabited, as the nightly state refresh would."""
    galaxy.connection.execute(
        "INSERT INTO v3_state.system_state (system_id64, name, x_ly, y_ly, z_ly, population, "
        "system_updated_at, content_hash, source) VALUES (%s, %s, %s, %s, %s, %s, now(), 'test', 'test') "
        "ON CONFLICT (system_id64) DO UPDATE SET x_ly = excluded.x_ly, y_ly = excluded.y_ly, "
        "z_ly = excluded.z_ly, population = excluded.population",
        (system_id64, "Colony %d" % system_id64, x, y, z, population))


def position(galaxy: Galaxy, system_id64: int) -> tuple[float, float, float]:
    return galaxy.one("SELECT x_ly, y_ly, z_ly FROM {s}.systems WHERE id64 = %s", system_id64)


def in_reach(galaxy: Galaxy) -> dict[int, bool]:
    return dict(galaxy.all("SELECT system_id64, within_250 FROM v3_state.system_reach"))


def expected(galaxy: Galaxy) -> dict[int, bool]:
    """Reach worked out the slow way, from every system's distance to every
    inhabited one."""
    anchors = galaxy.all("SELECT x_ly, y_ly, z_ly FROM v3_state.system_state WHERE population > 0")
    found = {}
    for system_id, *where in galaxy.all("SELECT id64, x_ly, y_ly, z_ly FROM {s}.systems"):
        nearest = min(math.dist(where, anchor) for anchor in anchors)
        if nearest <= reach.REACH_LY:
            found[system_id] = nearest <= reach.NEAR_LY
    return found


def work_out(galaxy: Galaxy) -> reach.ReachReport:
    target = galaxy_update.preflight(galaxy.connection, need_queue=False)
    return reach.rebuild(galaxy.connection, target, progress=False)


def waiting(galaxy: Galaxy, **options) -> scores.ApplyReport:
    return scores.apply_waiting(galaxy.connection, progress=False, **options)


def has_scores(galaxy: Galaxy, system_id64: int) -> dict[str, int]:
    return {table: galaxy.one(f"SELECT count(*) FROM v3_derived.{table} WHERE system_id64 = %s",
                              system_id64)[0] for table in SCORE_TABLES}


def newcomer(galaxy: Galaxy, id64: int, where: tuple[float, float, float], shift: int) -> dict:
    """A newly found system at ``where``: HD 38179's bodies (it has no
    stations) under new identities. ``shift`` keeps each newcomer's body ids
    apart from the others'."""
    record = as_new_system(galaxy.records[HD_38179], id64, "Newcomer %d" % id64)
    record["coords"] = dict(zip("xyz", where, strict=True))
    for body in record["bodies"]:
        body["id64"] += shift
        for ring in [*(body.get("rings") or []), *(body.get("belts") or [])]:
            if "id64" in ring:
                ring["id64"] += shift
    return record


def forget_scores(galaxy: Galaxy, system_id64: int) -> None:
    with galaxy.connection.transaction():
        galaxy.connection.execute(ANNOUNCE)
        for table in SCORE_TABLES:
            galaxy.connection.execute(f"DELETE FROM v3_derived.{table} WHERE system_id64 = %s", (system_id64,))


# -- the migration --------------------------------------------------------------


def test_the_migration_adds_three_empty_tables_and_can_run_again(pi):
    assert reach.available(pi.connection)
    for table in ("reach_anchor", "system_reach", "reach_log"):
        assert pi.one(f"SELECT count(*) FROM v3_state.{table}") == (0,)
    finder_seed.apply_migration(pi.connection, 28)
    assert reach.available(pi.connection)
    assert reach.status_lines(pi.connection) == ["Reach has not been worked out yet."]


# -- working reach out afresh ------------------------------------------------------


def test_reach_is_every_system_within_500_ly_and_says_which_are_within_250(pi):
    inhabit(pi, SOL, 0, 0, 0)
    report = work_out(pi)
    assert in_reach(pi) == expected(pi) == {
        system_id: system_id in NEAR_SOL for system_id in WITHIN_500_OF_SOL}
    assert pi.all("SELECT system_id64 FROM v3_state.reach_anchor") == [(SOL,)]
    assert (report.rebuilt, report.anchors, report.systems, report.within_250) == (True, 1, 8, 5)
    assert (report.added, report.removed, report.crossed_250) == (8, 0, 0)
    assert pi.one("SELECT anchors, systems, within_250, added, removed, crossed_250 FROM v3_state.reach_log") \
        == (1, 8, 5, 8, 0, 0)
    text = "\n".join(report.lines())
    assert "Reach worked out afresh (asked for)" in text and "systems within 500 ly:" in text
    # The tables it is built in beside the real ones are gone again.
    assert pi.one("SELECT to_regclass('v3_state.system_reach_next'), "
                  "to_regclass('v3_state.reach_anchor_next')") == (None, None)
    assert reach.status_lines(pi.connection)[1] == "Systems within 500 ly now: 8 (within 250 ly: 5)."


def changes(before: dict[int, bool], after: dict[int, bool]) -> tuple[set[int], set[int], set[int]]:
    """Systems that came within 500 ly, left it, and crossed the 250 ly line."""
    return (set(after) - set(before), set(before) - set(after),
            {system_id for system_id in set(before) & set(after) if before[system_id] != after[system_id]})


def test_working_it_out_again_changes_only_what_differs(pi, monkeypatch):
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    before = in_reach(pi)
    versions = dict(pi.all("SELECT system_id64, xmin::text FROM v3_state.system_reach"))

    # A colony 10 ly from Praea Euq brings it in, within 250 ly; one 100 ly
    # from Maia brings Maia, and Col 285 Sector beside it, within 250 ly.
    x, y, z = position(pi, PRAEA)
    inhabit(pi, 9001, x + 10, y, z)
    x, y, z = position(pi, MAIA)
    inhabit(pi, 9002, x, y + 100, z)
    report = work_out(pi)
    after = in_reach(pi)
    assert after == expected(pi)
    added, removed, crossed = changes(before, after)
    assert (added, removed, crossed) == ({PRAEA}, set(), {MAIA, 1457973727914})
    assert (report.added, report.removed, report.crossed_250) == (1, 0, 2)
    assert after[PRAEA] is True
    # Only the rows that changed were written.
    rewritten = {system_id for system_id, version in pi.all(
        "SELECT system_id64, xmin::text FROM v3_state.system_reach") if versions.get(system_id) != version}
    assert rewritten == added | crossed

    # The colony at Praea is abandoned. (Fewer inhabited systems than before:
    # allowed here, where there are only three.)
    monkeypatch.setattr(reach, "SHRINK_LIMIT", 0.0)
    inhabit(pi, 9001, *position(pi, PRAEA), population=0)
    report = work_out(pi)
    assert in_reach(pi) == expected(pi) and changes(after, in_reach(pi)) == (set(), {PRAEA}, set())
    assert (report.anchors, report.added, report.removed, report.crossed_250) == (2, 0, 1, 0)
    assert pi.all("SELECT system_id64 FROM v3_state.reach_anchor ORDER BY 1") == [(9002,), (SOL,)]
    assert pi.one("SELECT count(*) FROM v3_state.reach_log") == (3,)


def test_reach_is_left_as_it_was_when_the_system_state_looks_damaged(pi):
    # Nothing inhabited at all: has the state refresh ever run?
    with pytest.raises(galaxy_update.Refused, match="no system is inhabited"):
        work_out(pi)
    assert pi.one("SELECT count(*) FROM v3_state.reach_log") == (0,)

    # Ten colonies near Sol, and one near Thaile.
    for offset in range(10):
        inhabit(pi, 9100 + offset, offset, 0, 0)
    x, y, z = position(pi, THAILE)
    inhabit(pi, 9200, x + 5, y, z)
    work_out(pi)
    kept = in_reach(pi)
    assert THAILE in kept

    # Two colonies gone at once is more than a tenth of them.
    inhabit(pi, 9100, 0, 0, 0, population=0)
    inhabit(pi, 9101, 1, 0, 0, population=0)
    with pytest.raises(galaxy_update.Refused, match="only 9 systems are inhabited now, against 11"):
        work_out(pi)
    assert in_reach(pi) == kept and pi.one("SELECT count(*) FROM v3_state.reach_log") == (1,)
    inhabit(pi, 9100, 0, 0, 0)
    inhabit(pi, 9101, 1, 0, 0)

    # As many colonies as before, but reach shrinks from 9 systems to 8.
    inhabit(pi, 9200, 2, 2, 2)
    with pytest.raises(galaxy_update.Refused, match="only 8 systems are within 500 ly now, against 9"):
        work_out(pi)
    assert in_reach(pi) == kept
    assert pi.one("SELECT to_regclass('v3_state.system_reach_next')") == (None,)


# -- before each night's scoring -----------------------------------------------------


def test_the_scoring_will_not_start_until_reach_has_been_worked_out(pi, tmp_path, monkeypatch, capsys):
    run(pi, tmp_path, [pi.records[HD_38179]])
    with pytest.raises(galaxy_update.Refused, match="reach has not been worked out yet"):
        waiting(pi)
    assert set(queue(pi)) == {HD_38179}
    _environment(monkeypatch, pi.conninfo)
    assert scores.main(["apply"]) == 2
    assert "REFUSED: reach has not been worked out yet" in capsys.readouterr().out
    assert set(queue(pi)) == {HD_38179}


def test_the_scoring_takes_only_systems_within_reach(pi, tmp_path):
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    run(pi, tmp_path, pi.records.values())
    assert len(queue(pi)) == 12
    stored = {table: pi.all(f"SELECT xmin::text, t::text FROM v3_derived.{table} t "
                            "WHERE system_id64 = ANY(%s) ORDER BY 2", [THAILE, HD_38179, PRAEA])
              for table in SCORE_TABLES}
    report = waiting(pi)
    assert (report.outside_reach, report.systems_read, report.still_waiting) == (4, 8, 0)
    # Seven scored; one only gained or lost a station, so only its Find row was rebuilt.
    assert (report.added + report.changed + report.same, report.find_only, report.cannot_score) == (7, 1, {})
    # The four further away were measured (they had no row), and are not in reach.
    assert (report.reach_report.rebuilt, report.reach_report.measured, report.reach_report.added) == (False, 4, 0)
    assert report.unscored_in_reach == 0
    # The four further away keep the scores they had; nothing scored them.
    assert stored == {table: pi.all(f"SELECT xmin::text, t::text FROM v3_derived.{table} t "
                                    "WHERE system_id64 = ANY(%s) ORDER BY 2", [THAILE, HD_38179, PRAEA])
                      for table in SCORE_TABLES}
    text = "\n".join(report.lines())
    assert "Taken off the waiting list, further than 500 ly:" in text and "(not scored)" in text
    assert report.as_dict()["outside_reach"] == 4 and report.as_dict()["reach"]["rebuilt"] is False


def test_new_systems_are_measured_against_the_same_inhabited_systems(pi, tmp_path):
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    # A colony founded since reach was worked out does not count until next time.
    x, y, z = position(pi, PRAEA)
    inhabit(pi, 9001, x, y, z + 5)
    newcomers = {
        7001: newcomer(pi, 7001, (10.0, 0.0, 0.0), 0),
        7002: newcomer(pi, 7002, (300.0, 0.0, 0.0), 100),
        7003: newcomer(pi, 7003, (-900.0, 0.0, 0.0), 200),
        # Beside the new colony.
        7004: newcomer(pi, 7004, (x, y, z + 20), 300),
    }
    run(pi, tmp_path, newcomers.values())
    assert set(queue(pi)) == set(newcomers)

    report = waiting(pi)
    assert (report.reach_report.measured, report.reach_report.added) == (4, 2)
    assert {system_id: in_reach(pi).get(system_id) for system_id in newcomers} \
        == {7001: True, 7002: False, 7003: None, 7004: None}
    assert (report.outside_reach, report.systems_read, report.added, report.still_waiting) == (2, 2, 2, 0)
    assert has_scores(pi, 7001)["system_search"] == 1 and has_scores(pi, 7004)["system_search"] == 0
    assert "Waiting systems measured for reach (new ones):" in "\n".join(report.lines())

    # The next time reach is worked out afresh, the new colony counts, and the
    # system beside it, which has no scores, gets them.
    report = work_out(pi)
    assert report.added == 2 and in_reach(pi)[7004] is True
    report = waiting(pi)
    assert (report.systems_read, report.unscored_in_reach, report.added) == (0, 1, 1)
    assert has_scores(pi, 7004)["system_search"] == 1


def test_a_system_that_comes_within_reach_gets_scores_from_its_own_rows(pi, tmp_path):
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    # Praea Euq has no scores (on the Pi, systems out of reach have none), and
    # the galaxy update rewrites its rows, so they have their classes.
    forget_scores(pi, PRAEA)
    run(pi, tmp_path, [pi.records[PRAEA]])
    report = waiting(pi)
    assert (report.outside_reach, report.systems_read, report.unscored_in_reach) == (1, 0, 0)
    assert set(has_scores(pi, PRAEA).values()) == {0} and queue(pi) == {}

    x, y, z = position(pi, PRAEA)
    inhabit(pi, 9001, x + 10, y, z)
    work_out(pi)
    report = waiting(pi)
    assert (report.systems_read, report.unscored_in_reach, report.added, report.cannot_score) == (0, 1, 1, {})
    assert has_scores(pi, PRAEA)["system_rating_vector"] == 1 and has_scores(pi, PRAEA)["system_search"] == 1
    assert "Systems within 500 ly that had no scores:" in "\n".join(report.lines())
    # Scored once; the next night there is nothing more to do.
    assert waiting(pi).unscored_in_reach == 0


def test_reach_is_worked_out_afresh_each_week_and_after_a_full_galaxy_file(pi, tmp_path, monkeypatch):
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    assert waiting(pi).reach_report.rebuilt is False
    pi.connection.execute("UPDATE v3_state.reach_log SET worked_out_at = now() - interval '8 days'")
    x, y, z = position(pi, PRAEA)
    inhabit(pi, 9001, x, y, z + 5)
    report = waiting(pi)
    assert (report.reach_report.rebuilt, report.reach_report.why) == (True, "last done 8 days ago")
    assert PRAEA in in_reach(pi) and pi.one("SELECT count(*) FROM v3_state.reach_log") == (2,)
    assert "Reach worked out afresh (last done 8 days ago)" in "\n".join(report.lines())

    # So many systems waiting that measuring them one by one would be slower.
    monkeypatch.setattr(reach, "REBUILD_WHEN_WAITING", 3)
    run(pi, tmp_path, [pi.records[SOL], pi.records[THAILE], pi.records[HD_38179]])
    report = waiting(pi)
    assert (report.reach_report.rebuilt, report.reach_report.why) == (True, "3 systems are waiting for scores")
    assert (report.outside_reach, report.systems_read) == (2, 1)


def test_a_limit_counts_the_systems_in_reach_without_scores_too(pi, tmp_path):
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    for system_id in (ACHENAR, BORANN):
        forget_scores(pi, system_id)
    run(pi, tmp_path, [pi.records[ACHENAR], pi.records[BORANN], pi.records[MAIA]])
    galaxy = pi.connection
    galaxy.execute("DELETE FROM v3_state.rescore_queue WHERE system_id64 <> %s", (MAIA,))
    report = waiting(pi, limit=2)
    assert (report.systems_read, report.unscored_in_reach, report.added, report.stopped_early) == (1, 2, 1, True)
    report = waiting(pi, limit=1)
    assert (report.systems_read, report.added) == (0, 1)
    assert waiting(pi).unscored_in_reach == 0


# -- the command ---------------------------------------------------------------------


def test_the_command_works_reach_out_and_says_what_came_of_it(pi, monkeypatch, capsys):
    _environment(monkeypatch, pi.conninfo)
    assert reach.main(["status"]) == 0
    assert "Reach has not been worked out yet." in capsys.readouterr().out
    assert reach.main(["rebuild"]) == 2
    assert "REFUSED: no system is inhabited" in capsys.readouterr().out

    inhabit(pi, SOL, 0, 0, 0)
    assert reach.main(["rebuild"]) == 0
    out = capsys.readouterr().out
    assert "Working out reach afresh" in out and "systems within 500 ly:" in out
    assert out.rstrip().endswith("DONE")
    assert reach.main(["status"]) == 0
    out = capsys.readouterr().out
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert f"Reach last worked out afresh {today}" in out and "from 1 inhabited systems" in out

    with psycopg.connect(pi.conninfo, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (galaxy_update.LOCK_KEY,))
        assert reach.main(["rebuild"]) == 1
        assert "a galaxy update or a scoring run is going on" in capsys.readouterr().out

    pi.connection.execute("DROP TABLE v3_state.reach_log")
    assert reach.main(["rebuild"]) == 2
    assert "does not keep reach (migration 028 has not been applied)" in capsys.readouterr().out
    assert reach.main(["status"]) == 0
    assert "Reach is not kept in this database" in capsys.readouterr().out
