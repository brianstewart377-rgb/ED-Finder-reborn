"""A ring's and a belt's size is read in true kilometres
(GET /api/v1/systems/{id}/rings and GET /api/v1/plan/systems/{id}/bodies).

Found on 13 November 2026: Spansh's galaxy file gives a ring's inner and
outer edge in metres. The import stores the number as it comes, in columns
named for kilometres, so every stored ring and belt is 1,000 times too big.
Saturn's D Ring came out starting 74,500,000 km from Saturn, and Sol's
asteroid belt 308 thousand million km from the Sun: far past Pluto.

The stored numbers stay as they are (shared_contracts/ring_radius.py says
why). They are put right where they are read.

The galaxy is the twelve real fixture systems, as Spansh holds them: 68
rings and 13 belts.

The database tests run on PostgreSQL 18 with every migration applied; they
need RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://ring-size-test.invalid")

from shared_contracts.ring_radius import METRES_PER_KILOMETRE, true_kilometres  # noqa: E402
from tests.test_atmosphere_and_pressure import ask, real_records  # noqa: E402
from tests.test_galaxy_scores import galaxy, template  # noqa: E402,F401
from tests.test_galaxy_update import SOL  # noqa: E402
from v3_spansh.contracts import COPY_COLUMNS  # noqa: E402
from v3_spansh_classified.adapter import ClassifiedSpanshAdapter  # noqa: E402

KM_PER_AU = 149_597_870.7
SUN_RADIUS_KM = 695_700.0


def given_rings(record: dict) -> dict[str, dict]:
    """Every ring and belt of a real record, by name, with the body it is around."""
    found = {}
    for body in record["bodies"]:
        for kind in ("rings", "belts"):
            for ring in body.get(kind) or []:
                found[ring["name"]] = {**ring, "kind": kind, "around": body}
    return found


def stored_rings(record: dict) -> dict[str, dict]:
    """Each ring's and belt's row as the import writes it to the database, by name."""
    batch = ClassifiedSpanshAdapter(
        source_id=40, source_run_id=UUID(int=40), observed_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
    ).adapt_system(record)
    rows = [dict(zip(COPY_COLUMNS["rings"], row, strict=True)) for row in batch.rows["rings"]]
    return {row["name"]: row for row in rows}


def test_the_import_and_the_reader_agree_on_a_real_record():
    """The three that must change together: what Spansh gives, what the import
    stores, and how the stored number is read back."""
    sol = real_records()[SOL]
    given = given_rings(sol)
    # Spansh gives metres. Saturn's D Ring starts 74,500 km from Saturn's centre.
    assert (given["D Ring"]["innerRadius"], given["D Ring"]["outerRadius"]) == (74_500_000.0, 140_180_000.0)
    stored = stored_rings(sol)
    assert set(stored) == set(given)
    # The import stores the number as it comes, under a name that says kilometres...
    assert stored["D Ring"]["inner_radius_km"] == 74_500_000.0
    # ...and reading it back puts it right, for every ring and belt of the system.
    for name, row in stored.items():
        assert true_kilometres(row["inner_radius_km"]) == pytest.approx(given[name]["innerRadius"] / 1000, rel=1e-12)
        assert true_kilometres(row["outer_radius_km"]) == pytest.approx(given[name]["outerRadius"] / 1000, rel=1e-12)
    assert true_kilometres(stored["D Ring"]["inner_radius_km"]) == 74_500.0
    assert true_kilometres(None) is None
    assert METRES_PER_KILOMETRE == 1000.0


def test_only_as_metres_do_the_real_rings_make_sense():
    """What the correction rests on, seen in the twelve real systems. Read as
    metres, every ring lies just outside the body it is around. Read as
    kilometres, none would lie within a thousand times the body's own size."""
    rings, belts = [], []
    for record in real_records().values():
        for ring in given_rings(record).values():
            around = ring["around"]
            radius_km = around.get("radius") or around["solarRadius"] * SUN_RADIUS_KM
            inner = ring["innerRadius"] / 1000 / radius_km
            outer = ring["outerRadius"] / 1000 / radius_km
            assert 1 < inner < outer, ring["name"]
            (rings if ring["kind"] == "rings" else belts).append((inner, outer, ring))
    assert (len(rings), len(belts)) == (68, 13)
    # A ring starts between 1.2 and 27 times its body's radius out, and ends within 67.
    assert min(inner for inner, _, _ in rings) == pytest.approx(1.21, abs=0.01)
    assert max(inner for inner, _, _ in rings) == pytest.approx(26.43, abs=0.01)
    assert max(outer for _, outer, _ in rings) == pytest.approx(66.14, abs=0.01)
    # Sol's asteroid belt lies between Mars and Jupiter, as it does.
    sol = real_records()[SOL]
    axis_au = {body["name"]: body.get("semiMajorAxis") for body in sol["bodies"]}
    belt = given_rings(sol)["Asteroid Belt"]
    inner_au = true_kilometres(belt["innerRadius"]) / KM_PER_AU
    outer_au = true_kilometres(belt["outerRadius"]) / KM_PER_AU
    assert (inner_au, outer_au) == (pytest.approx(2.06, abs=0.005), pytest.approx(3.27, abs=0.005))
    assert axis_au["Mars"] < inner_au < outer_au < axis_au["Jupiter"]


def test_look_up_gives_each_ring_in_true_kilometres(galaxy):  # noqa: F811
    from edfinder_api.routers import v3

    seen = 0
    for system_id, record in sorted(galaxy.records.items()):
        given = given_rings(record)
        found = ask(galaxy, lambda pool, system_id=system_id: v3.rings(str(system_id), pool))
        assert {ring.name for ring in found} == set(given)
        for ring in found:
            assert ring.inner_radius_km == pytest.approx(given[ring.name]["innerRadius"] / 1000, rel=1e-12), ring.name
            assert ring.outer_radius_km == pytest.approx(given[ring.name]["outerRadius"] / 1000, rel=1e-12), ring.name
            seen += 1
    assert seen == 81
    sol = {ring.name: ring for ring in ask(galaxy, lambda pool: v3.rings(str(SOL), pool))}
    assert (sol["D Ring"].inner_radius_km, sol["D Ring"].outer_radius_km) == (74_500.0, 140_180.0)
    assert sol["Asteroid Belt"].kind == "BELT"
    assert sol["Asteroid Belt"].inner_radius_km == 308_170_000.0
    # A ring's mass was never wrong, and is passed on as it stands.
    assert sol["D Ring"].mass_mt == 58_071.0
    # The stored numbers themselves are untouched: still 1,000 times too big.
    assert galaxy.one(
        "SELECT inner_radius_km, outer_radius_km FROM {s}.rings WHERE system_id64 = %s AND name = 'D Ring'", SOL,
    ) == (74_500_000.0, 140_180_000.0)


def test_the_builder_gives_each_belt_in_true_kilometres(galaxy):  # noqa: F811
    from edfinder_api.routers import plan

    seen = 0
    for system_id, record in sorted(galaxy.records.items()):
        given = {name: ring for name, ring in given_rings(record).items() if ring["kind"] == "belts"}
        answer = ask(galaxy, lambda pool, system_id=system_id: plan.bodies(str(system_id), pool))
        assert {belt.name for belt in answer.belts} == set(given)
        for belt in answer.belts:
            assert belt.inner_radius_km == pytest.approx(given[belt.name]["innerRadius"] / 1000, rel=1e-12), belt.name
            assert belt.outer_radius_km == pytest.approx(given[belt.name]["outerRadius"] / 1000, rel=1e-12), belt.name
            seen += 1
    assert seen == 13
    sol = ask(galaxy, lambda pool: plan.bodies(str(SOL), pool))
    assert [(belt.name, belt.inner_radius_km, belt.outer_radius_km) for belt in sol.belts] \
        == [("Asteroid Belt", 308_170_000.0, 489_190_000.0)]
