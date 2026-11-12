"""Look up shows each body's class from the galaxy tables, so it shows for a
system that has no scores (GET /api/v1/systems/{id}/bodies).

On the Raspberry Pi only systems within 500 ly of an inhabited system have
scores. The class used to be read from the score rows alone, so a system
further out would have shown its bodies with no class. Since migration 027 the
galaxy's own body rows hold the class; a row written before it still gets its
class from the score row.

The galaxy is the twelve real fixture systems with their real scores. Their
body rows start without a class of their own, as rows from before migration
027 are; the nightly galaxy update, run here on the same real records, writes
it.

Runs on PostgreSQL 18 with every migration applied; needs
RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

import asyncio
import os

from psycopg.conninfo import conninfo_to_dict

os.environ.setdefault("CORS_ORIGINS", "https://look-up-test.invalid")

from tests.test_galaxy_scores import ANNOUNCE, SCORE_TABLES, galaxy, template  # noqa: E402,F401
from tests.test_galaxy_update import HD_38179, SOL, Galaxy, run  # noqa: E402

FROM_SCORES = "ratings-v4-derived"


def look_up(galaxy: Galaxy, system_id64: int) -> dict[str, tuple[str | None, str | None, str | None]]:  # noqa: F811
    """The route's own function on a real asyncpg pool: each body's name with
    its plain type, its class and where the class was read from."""
    import asyncpg

    from edfinder_api.routers import v3

    settings = conninfo_to_dict(galaxy.conninfo)

    async def scenario():
        pool = await asyncpg.create_pool(
            host=settings.get("host"), port=int(settings.get("port", 5432)), user=settings.get("user"),
            password=settings.get("password"), database=settings.get("dbname"),
            min_size=1, max_size=2, statement_cache_size=0)
        try:
            return await v3.bodies(str(system_id64), pool)
        finally:
            await pool.close()

    bodies = asyncio.run(scenario())
    assert len({body.name for body in bodies}) == len(bodies)
    return {body.name: (body.body_type, body.body_class, body.body_class_source) for body in bodies}


def scored_classes(galaxy: Galaxy, system_id64: int) -> dict[str, str]:  # noqa: F811
    """Each scored body's name with the class in its score row."""
    return dict(galaxy.all(
        "SELECT b.name, m.body_class FROM {s}.bodies b JOIN v3_derived.body_mechanics m USING (body_pk) "
        "WHERE b.system_id64 = %s AND m.subtype_present", system_id64))


def forget_scores(galaxy: Galaxy, system_id64: int) -> None:  # noqa: F811
    """Leave a system with no scores at all, as one out of reach is on the Pi."""
    with galaxy.connection.transaction():
        galaxy.connection.execute(ANNOUNCE)
        for table in SCORE_TABLES:
            galaxy.connection.execute(f"DELETE FROM v3_derived.{table} WHERE system_id64 = %s", (system_id64,))
    assert galaxy.one("SELECT count(*) FROM v3_derived.body_mechanics WHERE system_id64 = %s", system_id64) == (0,)


def test_a_row_from_before_the_class_column_gets_its_class_from_the_score_row(galaxy):  # noqa: F811
    assert galaxy.one("SELECT count(*) FROM {s}.bodies WHERE body_class IS NOT NULL") == (0,)
    for system_id in sorted(galaxy.records):
        scored = scored_classes(galaxy, system_id)
        found = look_up(galaxy, system_id)
        assert found and scored
        for name, (plain, body_class, source) in found.items():
            if name in scored:
                assert (body_class, source) == (scored[name], FROM_SCORES)
            else:
                # Barycentres are never scored, so nothing says what they are
                # beyond their plain type.
                assert (plain, body_class, source) == ("barycentre", None, None)


def test_the_class_comes_from_the_galaxy_table_once_the_row_has_it(galaxy, tmp_path):  # noqa: F811
    before = {system_id: look_up(galaxy, system_id) for system_id in galaxy.records}
    run(galaxy, tmp_path, galaxy.records.values())
    assert galaxy.one("SELECT count(*) FROM {s}.bodies WHERE body_class IS NULL") == (0,)
    # A barycentre's stored class is only its plain type: not a fact to show.
    assert galaxy.all("SELECT DISTINCT b.body_class FROM {s}.bodies b JOIN v3_vocab.body_type t "
                      "USING (body_type_id) WHERE t.public_code = 'barycentre'") == [("barycentre",)]
    for system_id, was in before.items():
        found = look_up(galaxy, system_id)
        # The same classes as the score rows gave, now with no source named:
        # they are the galaxy tables' own, like every other fact of the body.
        assert {name: (plain, body_class) for name, (plain, body_class, _) in found.items()} \
            == {name: (plain, body_class) for name, (plain, body_class, _) in was.items()}
        assert {source for _, _, source in found.values()} == {None}
    # 360 bodies have a class to show: every one that is not a barycentre.
    shown = [body_class for system_id in galaxy.records
             for _, body_class, _ in look_up(galaxy, system_id).values() if body_class is not None]
    assert len(shown) == 360


def test_a_system_with_no_scores_still_shows_its_classes(galaxy, tmp_path):  # noqa: F811
    """The Pi, for a system further than 500 ly from an inhabited one."""
    for system_id in (SOL, HD_38179):
        was = look_up(galaxy, system_id)
        run(galaxy, tmp_path, [galaxy.records[system_id]])
        forget_scores(galaxy, system_id)
        found = look_up(galaxy, system_id)
        assert {name: body_class for name, (_, body_class, _) in found.items()} \
            == {name: body_class for name, (_, body_class, _) in was.items()}
    assert look_up(galaxy, SOL)["Earth"] == ("planet", "Earth-like world", None)


def test_with_neither_a_class_of_its_own_nor_a_score_row_no_class_is_shown(galaxy):  # noqa: F811
    forget_scores(galaxy, SOL)
    found = look_up(galaxy, SOL)
    assert len(found) == 41 and {(body_class, source) for _, body_class, source in found.values()} == {(None, None)}


def test_the_rows_own_class_is_used_even_where_the_score_row_says_otherwise(galaxy, tmp_path):  # noqa: F811
    """A body row can be newer than its scores: the galaxy update writes the
    row, and the scoring comes to the system later."""
    run(galaxy, tmp_path, [galaxy.records[SOL]])
    assert scored_classes(galaxy, SOL)["Venus"] == "High metal content world"

    def set_class(name: str, body_class: str, keep_type: bool = True) -> None:
        changed = galaxy.connection.execute(
            "UPDATE " + galaxy.schema + ".bodies SET body_class = %s"
            + ("" if keep_type else ", body_type_id = NULL") + " WHERE system_id64 = %s AND name = %s",
            (body_class, SOL, name)).rowcount
        assert changed == 1

    set_class("Venus", "Water world")
    # Spansh's newest record gave no sub-type: the stored class is the plain
    # type, which is not shown, and the older score row is not used either.
    set_class("Mars", "planet")
    # A body of no known type and no sub-type is stored as "Unknown".
    set_class("Mercury", "Unknown", keep_type=False)
    found = look_up(galaxy, SOL)
    assert found["Venus"] == ("planet", "Water world", None)
    assert found["Mars"] == ("planet", None, None)
    assert found["Mercury"] == (None, None, None)
    assert found["Earth"] == ("planet", "Earth-like world", None)
