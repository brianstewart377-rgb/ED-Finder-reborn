"""The map's two star queries look up a star's class only for the systems they
return (GET /api/map/systems).

Each query chooses the systems in a box, cuts them to a limit, and gives each
one the class of its main star from the galaxy's `bodies` table. They used to
look the class up before the cut, so a box holding 55,775 systems made 55,775
lookups to return 5,001 rows; on the Raspberry Pi that took 4.5 seconds, and
the map's stars arrived late or not at all. Now the cut comes first.

These tests hold two things on the twelve real fixture systems: the rows and
their order are the same as the old queries gave, and the number of visits to
`bodies` is the number of rows returned, not the number of systems in the box.

Runs on PostgreSQL 18 with every migration applied; needs
RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

import asyncio
import json
import math
import os

import pytest
from psycopg.conninfo import conninfo_to_dict

os.environ.setdefault("CORS_ORIGINS", "https://map-stars-test.invalid")

from tests.test_galaxy_update import SOL, Galaxy, galaxy, template  # noqa: E402,F401

# A box that holds the whole fixture galaxy, and one that holds only the
# systems on the side of Sol with x below zero.
EVERYWHERE = (-100_000.0, 100_000.0, -100_000.0, 100_000.0, -100_000.0, 100_000.0)
WEST_OF_SOL = (-100_000.0, -0.001, -100_000.0, 100_000.0, -100_000.0, 100_000.0)
# TABLESAMPLE takes a share of the table's pages. All of them, so the sample
# query can be compared row for row.
EVERY_PAGE = 100.0


def old_close_up(schema: str) -> str:
    """The close-up query as it was: the class looked up before the cut."""
    return f"""
        SELECT s.id64, s.name, s.x_ly AS x, s.y_ly AS y, s.z_ly AS z,
               s.galaxy_region_id,
               FALSE AS populated,
               body.spectral_class AS main_star_type
          FROM {schema}.systems s
          LEFT JOIN LATERAL (
            SELECT b.spectral_class
              FROM {schema}.bodies b
             WHERE b.system_id64 = s.id64
               AND b.is_main_star
             ORDER BY b.body_pk
             LIMIT 1
          ) body ON TRUE
         WHERE s.grid_x BETWEEN $1 AND $2
           AND s.grid_y BETWEEN $3 AND $4
           AND s.grid_z BETWEEN $5 AND $6
           AND s.x_ly BETWEEN $7 AND $8
           AND s.y_ly BETWEEN $9 AND $10
           AND s.z_ly BETWEEN $11 AND $12
         ORDER BY s.id64
         LIMIT $13
    """


def old_sample(schema: str) -> str:
    """The wide-view sample as it was: the class looked up before the cut."""
    return f"""
        SELECT s.id64, s.name, s.x_ly AS x, s.y_ly AS y,
               s.z_ly AS z, s.galaxy_region_id,
               FALSE AS populated,
               body.spectral_class AS main_star_type
          FROM {schema}.systems s TABLESAMPLE SYSTEM ($1::float4)
          LEFT JOIN LATERAL (
            SELECT b.spectral_class
              FROM {schema}.bodies b
             WHERE b.system_id64 = s.id64
               AND b.is_main_star
             ORDER BY b.body_pk
             LIMIT 1
          ) body ON TRUE
         WHERE s.x_ly BETWEEN $2 AND $3
           AND s.y_ly BETWEEN $4 AND $5
           AND s.z_ly BETWEEN $6 AND $7
         ORDER BY md5(s.id64::text)
         LIMIT  $8
    """


def close_up_parameters(box: tuple[float, ...], rows: int) -> tuple:
    """The parameters the route sends for a close-up box: the 10 light year
    grid cells the box touches, then the box itself, then the row count."""
    lo_x, hi_x, lo_y, hi_y, lo_z, hi_z = box
    return (
        math.floor(lo_x / 10), math.floor(hi_x / 10),
        math.floor(lo_y / 10), math.floor(hi_y / 10),
        math.floor(lo_z / 10), math.floor(hi_z / 10),
        lo_x, hi_x, lo_y, hi_y, lo_z, hi_z, rows)


def sample_parameters(box: tuple[float, ...], rows: int) -> tuple:
    return (EVERY_PAGE, *box, rows)


def on_the_database(galaxy: Galaxy, work):  # noqa: F811
    """Run `work(connection)` on a real asyncpg connection, as the app does."""
    import asyncpg

    settings = conninfo_to_dict(galaxy.conninfo)

    async def scenario():
        connection = await asyncpg.connect(
            host=settings.get("host"), port=int(settings.get("port", 5432)), user=settings.get("user"),
            password=settings.get("password"), database=settings.get("dbname"),
            statement_cache_size=0)
        try:
            return await work(connection)
        finally:
            await connection.close()

    return asyncio.run(scenario())


def rows_of(galaxy: Galaxy, query: str, parameters: tuple) -> list[tuple]:  # noqa: F811
    """Every row the query gives, in the order it gives them."""
    async def work(connection):
        return [tuple(row.values()) for row in await connection.fetch(query, *parameters)]

    return on_the_database(galaxy, work)


def visits_to_bodies(galaxy: Galaxy, query: str, parameters: tuple) -> int:  # noqa: F811
    """How many times the database went into the `bodies` table to run the
    query, read from its own account of the run."""
    async def work(connection):
        return await connection.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + query, *parameters)

    plan = json.loads(on_the_database(galaxy, work))[0]["Plan"]
    visits: list[int] = []

    def walk(node: dict) -> None:
        if node.get("Relation Name") == "bodies":
            visits.append(int(node["Actual Loops"]))
        for child in node.get("Plans", []):
            walk(child)

    walk(plan)
    assert visits, "the query never read the bodies table, so no star has a class"
    return max(visits)


def main_star_class(record: dict) -> str | None:
    """The class of a system's main star, read from its real Spansh record."""
    stars = [body for body in record["bodies"] if body.get("mainStar")]
    return stars[0].get("spectralClass") if stars else None


def systems_in(galaxy: Galaxy, box: tuple[float, ...]) -> int:  # noqa: F811
    return galaxy.one(
        "SELECT count(*) FROM {s}.systems WHERE x_ly BETWEEN %s AND %s "
        "AND y_ly BETWEEN %s AND %s AND z_ly BETWEEN %s AND %s", *box)[0]


def test_the_fixture_galaxy_can_tell_the_two_ways_apart(galaxy: Galaxy):  # noqa: F811
    """The tests below need a box with more systems than they ask for, a
    smaller box inside it, and stars that have a class to look up."""
    everywhere = systems_in(galaxy, EVERYWHERE)
    west = systems_in(galaxy, WEST_OF_SOL)
    assert everywhere == len(galaxy.records) == 12
    assert 3 < west < everywhere
    with_a_class = galaxy.one(
        "SELECT count(DISTINCT system_id64) FROM {s}.bodies "
        "WHERE is_main_star AND spectral_class IS NOT NULL")[0]
    assert with_a_class >= 6


@pytest.mark.parametrize("box", [EVERYWHERE, WEST_OF_SOL], ids=["whole galaxy", "west of Sol"])
@pytest.mark.parametrize("rows", [1, 4, 12, 13, 5001])
def test_close_up_gives_the_rows_it_gave_before(galaxy: Galaxy, box, rows):  # noqa: F811
    from edfinder_api.routers.map import close_up_stars_sql

    parameters = close_up_parameters(box, rows)
    before = rows_of(galaxy, old_close_up(galaxy.schema), parameters)
    after = rows_of(galaxy, close_up_stars_sql(galaxy.schema), parameters)

    assert after == before
    assert len(after) == min(rows, systems_in(galaxy, box))
    assert [row[0] for row in after] == sorted(row[0] for row in after)


@pytest.mark.parametrize("box", [EVERYWHERE, WEST_OF_SOL], ids=["whole galaxy", "west of Sol"])
@pytest.mark.parametrize("rows", [1, 4, 12, 13, 5001])
def test_sample_gives_the_rows_it_gave_before(galaxy: Galaxy, box, rows):  # noqa: F811
    from edfinder_api.routers.map import galaxy_sample_stars_sql

    parameters = sample_parameters(box, rows)
    before = rows_of(galaxy, old_sample(galaxy.schema), parameters)
    after = rows_of(galaxy, galaxy_sample_stars_sql(galaxy.schema), parameters)

    assert after == before
    assert len(after) == min(rows, systems_in(galaxy, box))


def test_some_returned_star_has_a_class(galaxy: Galaxy):  # noqa: F811
    """The comparison above would pass with every class missing on both sides."""
    from edfinder_api.routers.map import close_up_stars_sql, galaxy_sample_stars_sql

    close_up = rows_of(galaxy, close_up_stars_sql(galaxy.schema), close_up_parameters(EVERYWHERE, 13))
    sample = rows_of(galaxy, galaxy_sample_stars_sql(galaxy.schema), sample_parameters(EVERYWHERE, 13))

    in_the_records = {system_id64: main_star_class(record) for system_id64, record in galaxy.records.items()}
    assert in_the_records[SOL] == "G2"
    assert sum(star_class is not None for star_class in in_the_records.values()) >= 6
    assert {row[0]: row[-1] for row in close_up} == in_the_records
    assert sorted(close_up) == sorted(sample)


def test_close_up_visits_bodies_once_for_each_row_it_returns(galaxy: Galaxy):  # noqa: F811
    from edfinder_api.routers.map import close_up_stars_sql

    parameters = close_up_parameters(EVERYWHERE, 4)

    # The old way went in once for every system in the box.
    assert visits_to_bodies(galaxy, old_close_up(galaxy.schema), parameters) == 12
    assert visits_to_bodies(galaxy, close_up_stars_sql(galaxy.schema), parameters) == 4


def test_sample_visits_bodies_once_for_each_row_it_returns(galaxy: Galaxy):  # noqa: F811
    from edfinder_api.routers.map import galaxy_sample_stars_sql

    parameters = sample_parameters(EVERYWHERE, 4)

    assert visits_to_bodies(galaxy, old_sample(galaxy.schema), parameters) == 12
    assert visits_to_bodies(galaxy, galaxy_sample_stars_sql(galaxy.schema), parameters) == 4
