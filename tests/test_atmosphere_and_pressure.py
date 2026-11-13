"""A body with no atmosphere gets its ground slots predicted, and a pressure is
read in its true unit (GET /api/v1/plan/systems/{id}/bodies and
GET /api/v1/systems/{id}/bodies).

Two things about Spansh's galaxy file, both found on 13 November 2026:

1. It leaves the atmosphere kind empty for a body that has no atmosphere. The
   slot rule read "empty" as "not known" and made no prediction, so most
   bodies that can be landed on got none.
2. It gives surface pressure in atmospheres. The import took it for pascals
   and divided by 101,325, so every stored pressure is that much too small.

The stored numbers stay as they are (shared_contracts/surface_pressure.py says
why). The pressure is put right where it is read, and a body that can be
landed on, with no kind and a known pressure under 100 pascals, is given to
the slot rule as "No atmosphere".

The galaxy is the twelve real fixture systems, as Spansh holds them. Among
their 191 bodies that can be landed on are all three kinds: 166 with no kind
and a pressure of 0, 6 with no kind and a trace of pressure, and 19 with a
named thin atmosphere.

The database tests run on PostgreSQL 18 with every migration applied; they
need RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

import asyncio
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from psycopg.conninfo import conninfo_to_dict

os.environ.setdefault("CORS_ORIGINS", "https://atmosphere-test.invalid")

from edfinder_api.domain.ratings_v4_canonical import load_source_fixture  # noqa: E402
from edfinder_api.routers.plan_slot_predictions import (  # noqa: E402
    ATMOSPHERE_NAMED_FROM_PASCALS,
    NO_ATMOSPHERE,
    atmosphere_in_words,
)
from shared_contracts.surface_pressure import (  # noqa: E402
    PASCALS_PER_ATMOSPHERE,
    true_atmospheres,
    true_pascals,
)
from tests.test_galaxy_scores import galaxy, template  # noqa: E402,F401
from tests.test_galaxy_update import SOL, Galaxy  # noqa: E402
from v3_spansh.contracts import COPY_COLUMNS  # noqa: E402
from v3_spansh_classified.adapter import ClassifiedSpanshAdapter  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
THAILE = 31639717276


def real_records() -> dict[int, dict]:
    """The twelve real systems, exactly as Spansh's file holds them."""
    _, _, payloads = load_source_fixture(ROOT / "tests" / "fixtures" / "ratings_v4_sources")
    return {payload["system"]["id64"]: payload["system"] for payload in payloads}


def stored_bodies(record: dict) -> dict[str, dict]:
    """Each body's row as the import writes it to the database, by name."""
    batch = ClassifiedSpanshAdapter(
        source_id=40, source_run_id=UUID(int=40), observed_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
    ).adapt_system(record)
    rows = [dict(zip(COPY_COLUMNS["bodies"], row, strict=True)) for row in batch.rows["bodies"]]
    return {row["name"]: row for row in rows}


def as_the_route_reads(body: dict) -> dict:
    """A real record's body as the builder's route reads it from the database."""
    pressure = body.get("surfacePressure")
    return {
        "atmosphere": body.get("atmosphereType"),
        "is_landable": body.get("isLandable"),
        "surface_pressure_atm": None if pressure is None else pressure / PASCALS_PER_ATMOSPHERE,
    }


# -- the pressure's unit -----------------------------------------------------


def test_the_import_and_the_reader_agree_on_a_real_record():
    """The three that must change together: what Spansh gives, what the import
    stores, and how the stored number is read back."""
    sol = real_records()[SOL]
    given = {body["name"]: body.get("surfacePressure") for body in sol["bodies"]}
    # Spansh gives atmospheres: Earth is one atmosphere, near enough.
    assert given["Earth"] == 0.999078768813225
    stored = stored_bodies(sol)
    # The import stores a number 101,325 times too small...
    assert stored["Earth"]["surface_pressure_atm"] == pytest.approx(9.860140822237601e-06, rel=1e-12)
    # ...and reading it back puts it right, for every body of the system.
    for name, row in stored.items():
        if given[name] is None:
            assert true_atmospheres(row["surface_pressure_atm"]) is None
        else:
            assert true_atmospheres(row["surface_pressure_atm"]) == pytest.approx(given[name], rel=1e-12), name
    assert true_pascals(stored["Earth"]["surface_pressure_atm"]) == pytest.approx(101231.65, abs=0.01)
    assert true_atmospheres(stored["Mercury"]["surface_pressure_atm"]) == 0
    assert true_atmospheres(None) is None and true_pascals(None) is None


# -- no kind, and a pressure that says "none" --------------------------------


def test_in_the_real_records_the_line_is_at_100_pascals():
    """What the rule rests on, seen in the twelve real systems: every body
    that can be landed on and has no kind is under 100 pascals, and every one
    with a named atmosphere is above."""
    unnamed, named = [], []
    for record in real_records().values():
        for body in record["bodies"]:
            if body.get("isLandable"):
                pascals = body["surfacePressure"] * PASCALS_PER_ATMOSPHERE
                (named if body.get("atmosphereType") else unnamed).append(pascals)
    assert (len(unnamed), len(named)) == (172, 19)
    assert sum(1 for pascals in unnamed if pascals == 0) == 166
    assert max(unnamed) == pytest.approx(85.57, abs=0.01)
    assert min(named) == pytest.approx(121.61, abs=0.01)
    assert max(unnamed) < ATMOSPHERE_NAMED_FROM_PASCALS < min(named)


def test_a_body_that_can_be_landed_on_with_no_kind_has_no_atmosphere():
    words = Counter()
    for record in real_records().values():
        for body in record["bodies"]:
            if body.get("isLandable"):
                found = atmosphere_in_words(as_the_route_reads(body))
                # A named atmosphere is passed on as it stands.
                assert found == (body.get("atmosphereType") or NO_ATMOSPHERE), body["name"]
                words[found == NO_ATMOSPHERE] += 1
    assert words == {True: 172, False: 19}


def test_a_body_that_cannot_be_landed_on_is_never_called_airless():
    """A gas giant comes with no kind and a surface pressure of 0; Earth with
    no kind at all. Neither has "no atmosphere"."""
    sol = {body["name"]: body for body in real_records()[SOL]["bodies"]}
    for name in ("Earth", "Jupiter", "Saturn"):
        assert sol[name].get("atmosphereType") is None and sol[name]["isLandable"] is False
        assert atmosphere_in_words(as_the_route_reads(sol[name])) is None
    assert sol["Jupiter"]["surfacePressure"] == 0
    assert atmosphere_in_words(as_the_route_reads(sol["Venus"])) == "Hot thick Carbon dioxide"
    # Every such body in the twelve systems keeps exactly what Spansh says.
    for record in real_records().values():
        for body in record["bodies"]:
            if not body.get("isLandable"):
                assert atmosphere_in_words(as_the_route_reads(body)) == body.get("atmosphereType")


def test_nothing_is_claimed_when_the_pressure_does_not_say():
    """No real record here has these: they are what a broken or half-scanned
    row would look like, and the answer must stay "not known"."""
    mercury = as_the_route_reads(next(
        body for body in real_records()[SOL]["bodies"] if body["name"] == "Mercury"))
    assert atmosphere_in_words(mercury) == NO_ATMOSPHERE
    at_the_line = ATMOSPHERE_NAMED_FROM_PASCALS / PASCALS_PER_ATMOSPHERE / PASCALS_PER_ATMOSPHERE
    for pressure in (None, at_the_line, at_the_line * 50, -at_the_line):
        assert atmosphere_in_words({**mercury, "surface_pressure_atm": pressure}) is None, pressure
    assert atmosphere_in_words({**mercury, "surface_pressure_atm": at_the_line * 0.9999}) == NO_ATMOSPHERE
    assert atmosphere_in_words({**mercury, "is_landable": None}) is None


# -- the two routes, on a real database --------------------------------------


def ask(galaxy: Galaxy, call):  # noqa: F811
    """A route's own function on a real asyncpg pool."""
    import asyncpg

    settings = conninfo_to_dict(galaxy.conninfo)

    async def scenario():
        pool = await asyncpg.create_pool(
            host=settings.get("host"), port=int(settings.get("port", 5432)), user=settings.get("user"),
            password=settings.get("password"), database=settings.get("dbname"),
            min_size=1, max_size=2, statement_cache_size=0)
        try:
            return await call(pool)
        finally:
            await pool.close()

    return asyncio.run(scenario())


def test_every_real_body_that_can_be_landed_on_gets_its_ground_slots(galaxy):  # noqa: F811
    from edfinder_api.routers import plan

    words, slots = Counter(), Counter()
    by_name = {}
    for system_id, record in sorted(galaxy.records.items()):
        given = {body["name"]: body for body in record["bodies"]}
        answer = ask(galaxy, lambda pool, system_id=system_id: plan.bodies(str(system_id), pool))
        assert {body.name for body in answer.bodies} == set(given)
        for body in answer.bodies:
            by_name[body.name] = body
            if body.is_landable:
                # Before, 172 of these 191 came back "unknown", for want of an atmosphere.
                assert body.prediction_status == "predicted" and body.missing_inputs == [], body.name
                assert body.atmosphere == (given[body.name].get("atmosphereType") or NO_ATMOSPHERE)
                words[body.atmosphere == NO_ATMOSPHERE] += 1
                slots[body.predicted_surface_slots] += 1
            else:
                # Nothing changes for a body that cannot be landed on.
                assert body.atmosphere == given[body.name].get("atmosphereType"), body.name
                assert body.predicted_surface_slots is None and body.prediction_status is None
    assert words == {True: 172, False: 19}
    # What the slot rule makes of them. It is the rule's answer, not the game's.
    assert dict(sorted(slots.items())) == {0: 22, 1: 63, 2: 69, 3: 17, 4: 12, 5: 5, 6: 3}
    mercury = by_name["Mercury"]
    assert (mercury.atmosphere, mercury.predicted_surface_slots) == (NO_ATMOSPHERE, 2)
    # A trace of pressure, 85.57 pascals, and no kind: no atmosphere.
    trace = by_name["Thaile HW-V e2-7 B 11 a"]
    assert (trace.atmosphere, trace.predicted_surface_slots) == (NO_ATMOSPHERE, 1)
    # Its neighbour at 134.5 pascals has a name, and gains a slot for it.
    thin = by_name["Thaile HW-V e2-7 B 11 b"]
    assert (thin.atmosphere, thin.predicted_surface_slots) == ("Thin Ammonia", 3)
    assert by_name["Earth"].atmosphere is None and by_name["Jupiter"].atmosphere is None


def test_look_up_gives_each_pressure_in_true_atmospheres(galaxy):  # noqa: F811
    from edfinder_api.routers import v3

    for system_id, record in sorted(galaxy.records.items()):
        given = {body["name"]: body.get("surfacePressure") for body in record["bodies"]}
        found = ask(galaxy, lambda pool, system_id=system_id: v3.bodies(str(system_id), pool))
        assert {body.name for body in found} == set(given)
        for body in found:
            if given[body.name] is None:
                assert body.surface_pressure_atm is None
            else:
                assert body.surface_pressure_atm == pytest.approx(given[body.name], rel=1e-12), body.name
    sol = {body.name: body.surface_pressure_atm for body in ask(galaxy, lambda pool: v3.bodies(str(SOL), pool))}
    assert sol["Earth"] == pytest.approx(0.999078768813225)
    assert sol["Venus"] == pytest.approx(93.1895090056748)
    assert sol["Mercury"] == 0
    # The stored numbers themselves are untouched: still 101,325 times too small.
    assert galaxy.one("SELECT surface_pressure_atm FROM {s}.bodies WHERE system_id64 = %s AND name = 'Earth'", SOL) \
        == (pytest.approx(9.860140822237601e-06, rel=1e-12),)
    assert THAILE in galaxy.records
