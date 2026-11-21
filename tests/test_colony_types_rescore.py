"""Scoring every system's colony types afresh after the rule changes.

Runs on PostgreSQL 18 with every migration applied, on the database of
``tests/test_galaxy_scores.py``: the twelve real fixture systems with scores
made by the frozen build code, and 200 generated Find systems. Their colony
type rows are first made to look as if an older rule wrote them; then
``spansh_delta.colony_types`` has to give every one the deployed rule's
scores, and the API has to explain them.

Needs RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys

import psycopg
import pytest
from fastapi import HTTPException

os.environ.setdefault("CORS_ORIGINS", "https://colony-types-test.invalid")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "importer" / "src"))

from scripts import v3_system_archetype as archetype_build  # noqa: E402
from shared_contracts import v3_system_archetype_model as model  # noqa: E402
from spansh_delta import apply as galaxy_update  # noqa: E402
from spansh_delta import colony_types  # noqa: E402
from tests.test_find_reach import _api_pool  # noqa: E402
from tests.test_galaxy_scores import galaxy, template  # noqa: E402,F401  (the shared fixtures)
from tests.test_galaxy_update import HD_38179, MAIA, SOL  # noqa: E402

pytestmark = pytest.mark.db

OLD_VERSION = "v3-archetype-4"
COLUMNS = ("archetype_version", *archetype_build.STORED_VALUE_COLUMNS)


def _rows(connection) -> dict[int, tuple]:
    return {row[0]: row[1:] for row in connection.execute(
        "SELECT system_id64, " + ", ".join(COLUMNS) + " FROM v3_derived.system_archetype "
        "ORDER BY system_id64").fetchall()}


def _as_if_the_old_rule_wrote_them(connection) -> None:
    """Every row: the old version, and a Flexible score of 100 at the top,
    as the old rule gave systems with four economies over 55."""
    with connection.transaction():
        connection.execute("SET LOCAL edfinder.nightly_update = 'on'")
        connection.execute(
            "UPDATE v3_derived.system_archetype SET archetype_version = %s, "
            "flexible_score = 100, flexible_tier = 'S', best_colony_potential = 100, "
            "best_tier = 'S', primary_archetype = 'flexible', "
            "secondary_archetype = CASE WHEN secondary_archetype = 'flexible' "
            "THEN 'paradise' ELSE secondary_archetype END", (OLD_VERSION,))


def _expected(connection, generation) -> dict[int, tuple]:
    """What the model gives each system, read by the model's own queries."""
    expected = {}
    for system_id in _rows(connection):
        scope = {"generation_id": generation, "chunk_ordinal": None, "system_id64": system_id}
        vector = model.SystemVectorRow.from_row(
            connection.execute(model.VECTOR_READ_SQL, scope).fetchone())
        groups = tuple(tuple(row[1:]) for row in connection.execute(model.OPPORTUNITY_READ_SQL, scope))
        scored = model.score_raw_system(vector, groups)
        expected[system_id] = (model.ARCHETYPE_VERSION,
                               *(scored[column] for column in archetype_build.STORED_VALUE_COLUMNS))
    return expected


def test_the_batch_reads_give_what_the_models_own_reads_give(galaxy):
    connection, generation = galaxy.connection, galaxy.generation
    system_ids = sorted(_rows(connection))
    assert len(system_ids) == 212
    scope = {"generation_id": generation, "system_ids": system_ids}
    vectors = {row[0]: row for row in connection.execute(colony_types.VECTOR_BATCH_SQL, scope)}
    groups: dict[int, list] = {}
    for row in connection.execute(colony_types.OPPORTUNITY_BATCH_SQL, scope):
        groups.setdefault(row[0], []).append(row)
    for system_id in system_ids:
        one = {"generation_id": generation, "chunk_ordinal": None, "system_id64": system_id}
        assert vectors[system_id] == connection.execute(model.VECTOR_READ_SQL, one).fetchone()
        assert groups.get(system_id, []) == connection.execute(
            model.OPPORTUNITY_READ_SQL, one).fetchall()
    # The real systems have bodies that suit economies; the reads are not empty.
    assert all(groups.get(system_id) for system_id in (SOL, HD_38179, MAIA))


def test_every_row_gets_the_deployed_rules_scores_and_a_second_run_does_nothing(galaxy):
    connection, generation = galaxy.connection, galaxy.generation
    _as_if_the_old_rule_wrote_them(connection)
    expected = _expected(connection, generation)
    assert _rows(connection) != expected

    report = colony_types.rescore(connection, progress=False, batch_systems=50)
    assert _rows(connection) == expected
    assert (report.to_do, report.rescored, report.left) == (212, 212, 0)
    assert report.without_inputs == [] and not report.stopped_early
    # Every system was at 100 before; none of these is now.
    assert report.top_before == 212
    assert report.top_after == sum(1 for row in expected.values()
                                   if row[COLUMNS.index("best_colony_potential")] >= 88)
    assert report.lower + report.same == 212 and report.higher == 0
    assert report.lines()[-1] == "  Systems still with older colony type scores: 0."

    again = colony_types.rescore(connection, progress=False)
    assert (again.to_do, again.rescored, again.left) == (0, 0, 0)
    assert _rows(connection) == expected


def test_a_limit_stops_part_way_and_the_next_run_carries_on(galaxy):
    connection, generation = galaxy.connection, galaxy.generation
    _as_if_the_old_rule_wrote_them(connection)
    expected = _expected(connection, generation)

    first = colony_types.rescore(connection, limit=30, progress=False, batch_systems=20)
    assert (first.rescored, first.left, first.stopped_early) == (30, 182, True)
    rows = _rows(connection)
    done = [system_id for system_id, row in rows.items() if row[0] == model.ARCHETYPE_VERSION]
    # The first 30 systems in order, and exactly what the model gives them.
    assert done == sorted(rows)[:30]
    assert all(rows[system_id] == expected[system_id] for system_id in done)

    rest = colony_types.rescore(connection, progress=False, batch_systems=20)
    assert (rest.to_do, rest.rescored, rest.left) == (182, 182, 0)
    assert _rows(connection) == expected


def test_it_does_not_start_while_the_nightly_scoring_runs(galaxy):
    connection = galaxy.connection
    _as_if_the_old_rule_wrote_them(connection)
    before = _rows(connection)
    with psycopg.connect(connection.info.dsn, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (galaxy_update.LOCK_KEY,))
        with pytest.raises(galaxy_update.UpdateError, match="did not start"):
            colony_types.rescore(connection, progress=False)
    assert _rows(connection) == before


def test_the_api_explains_a_row_only_once_the_deployed_rule_has_written_it(galaxy):
    from edfinder_api.routers.archetypes import _system_archetype_explanation_v3

    connection = galaxy.connection
    _as_if_the_old_rule_wrote_them(connection)

    async def explain(system_id: int) -> dict:
        pool = await _api_pool(connection)
        try:
            return await _system_archetype_explanation_v3(
                id64=system_id, expected_generation_id=None, expected_archetype_version=None,
                expected_manifest_sha256=None, pool=pool)
        finally:
            await pool.close()

    with pytest.raises(HTTPException) as refused:
        asyncio.run(explain(SOL))
    assert refused.value.status_code == 409

    colony_types.rescore(connection, progress=False)
    explained = asyncio.run(explain(SOL))
    assert explained["ranking"]["archetype_version"] == model.ARCHETYPE_VERSION
    flexible = next(entry for entry in explained["archetypes"] if entry["key"] == "flexible")
    assert len(flexible["explanation"]["economies"]) == model.FLEXIBLE_ECONOMIES
    stored = _rows(connection)[SOL]
    assert flexible["score"] == stored[COLUMNS.index("flexible_score")]
