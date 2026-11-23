"""The planner's rules on real systems from the frozen fixtures ("Ratings
rebuild — design", build step 3: one set of rules for Find and the Plan page).

The inputs are made from the galaxy tables the way Find will make them
(spansh_delta/speed_trial.py), and the Plan page's own engine works them out.

Runs on PostgreSQL 18 with every migration applied; needs
RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("CORS_ORIGINS", "https://planner-rules-test.invalid")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "importer" / "src"))

from edfinder_api.cpe import Site, evaluate_economies  # noqa: E402
from edfinder_api.cpe.body_facts import (  # noqa: E402
    ECONOMIES,
    PAIRS,
    best_for_pair,
    purest,
    strongest,
)
from spansh_delta import speed_trial  # noqa: E402
from tests.test_galaxy_scores import galaxy, template  # noqa: E402,F401
from tests.test_galaxy_update import SOL, fill_classes  # noqa: E402
from tests.test_reach import pi  # noqa: E402,F401


def sol(pi):  # noqa: F811
    """Sol's planner inputs, and its bodies' references by name."""
    # Every body on the Pi has its class (the copy filled them in).
    fill_classes(pi.connection, pi.schema, pi.records.values())
    rows = speed_trial.read(pi.connection, pi.schema, [SOL])[SOL]
    return speed_trial.planner_inputs(rows), {body["name"]: str(body["body_pk"]) for body in rows.bodies}


def port_economies(inputs, *sites):
    (port,) = evaluate_economies(sites, inputs.bodies, inputs.system, "now").ports
    return {economy.name: economy for economy in port.economies}


def test_a_farm_at_titan_takes_the_rocky_ice_penalty(pi):  # noqa: F811
    # Titan is rocky ice, with no organics and no tidal lock (Sol's reserve
    # is common, which no Agriculture rule uses).
    inputs, ref = sol(pi)
    titan = ref["Titan"]
    economies = port_economies(
        inputs,
        Site("port", "coriolis_station", titan, "orbital", "complete"),
        Site("farm", "orbital_installation_space_farm", titan, "orbital", "complete"),
    )
    (farm,) = economies["Agriculture"].items
    assert farm.modifiers == (("on_or_orbiting_rocky_ice_body", -0.4),)
    assert (farm.floored, farm.value) == (True, 0.1)


def test_a_port_at_sol_gets_extraction_from_its_asteroid_belt(pi):  # noqa: F811
    # "Has rings (includes stars with asteroid belts)": Extraction (RB-0011).
    inputs, ref = sol(pi)
    star = next(body for body in inputs.bodies if body.body_ref == ref["Sol"])
    assert (star.kind, star.has_rings) == ("other_star", True)
    economies = port_economies(inputs, Site("port", "coriolis_station", ref["Sol"], "orbital", "complete"))
    assert {name: economy.strength for name, economy in economies.items()} == {
        "Military": 1.0, "Extraction": 1.0}
    assert "RB-0011" in economies["Extraction"].items[0].rule_ids


# -- facts for one body ---------------------------------------------------------


def every_system(pi):  # noqa: F811
    """Each real system's facts, and its bodies' names by reference."""
    fill_classes(pi.connection, pi.schema, pi.records.values())
    for system_id, rows in speed_trial.read(pi.connection, pi.schema, sorted(pi.records)).items():
        yield (system_id, speed_trial.facts_for(speed_trial.planner_inputs(rows)),
               {str(body["body_pk"]): body["name"] for body in rows.bodies})


def test_sol_s_picks_are_the_ones_the_design_names(pi):  # noqa: F811
    systems = {system_id: (facts, names) for system_id, facts, names in every_system(pi)}
    facts, names = systems[SOL]
    by_name = {names[body.body_ref]: body for body in facts}
    # "Refinery: strongest is Persephone (5 ground slots, up to 4.0, two other
    # economies); purest is Ganymede (2 slots, up to 2.6, nothing else)."
    found = strongest(facts, "Refinery")
    assert (names[found.body_ref], found.ground_slots, found.total("Refinery")) == ("Persephone", 5, 4.0)
    assert set(found.starts) == {"Industrial", "Extraction"}
    found = purest(facts, "Refinery")
    assert (names[found.body_ref], found.ground_slots, found.total("Refinery")) == ("Ganymede", 2, 2.6)
    assert set(found.starts) == {"Refinery"}
    # "Extraction: Persephone, each facility adds 1.2 (volcanism), port starts with 1.4."
    found = strongest(facts, "Extraction")
    assert (names[found.body_ref], found.adds["Extraction"], found.starts["Extraction"]) == (
        "Persephone", 1.2, 1.4)
    # Earth starts with four economies, so neither order picks it any more
    # (second review, row 3), though it starts with Agriculture and Tourism.
    earth = by_name["Earth"]
    assert set(earth.starts) == {"Agriculture", "HighTech", "Military", "Tourism"}
    assert earth.third_economies("Agriculture") == 2
    assert all(purest(facts, name) is not earth for name in ECONOMIES)
    # The two economies a pair is built for share the ground slots: two of
    # Persephone's five for Extraction, three for Industrial.
    found = best_for_pair(facts, "Extraction", "Industrial")
    assert (names[found.body_ref], found.pair_totals("Extraction", "Industrial")) == ("Persephone", (3.4, 3.8))


def test_every_pick_follows_the_order_on_every_real_system(pi):  # noqa: F811
    checked = 0
    for system_id, facts, _ in every_system(pi):
        for name in ECONOMIES:
            candidates = [body for body in facts if body.adds[name] > 0]
            first, pure = strongest(facts, name), purest(facts, name)
            if not candidates:
                assert first is pure is None, (system_id, name)
                continue
            # Strongest has the biggest ground part; purest the fewest third
            # economies (decision 7); both only where a facility can add it.
            assert first.total(name) == max(body.total(name) for body in candidates), (system_id, name)
            assert pure.third_economies(name) == min(body.third_economies(name) for body in candidates)
            assert first in candidates and pure in candidates
            checked += 1
        for one, two in PAIRS:
            candidates = [body for body in facts if body.adds[one] > 0 and body.adds[two] > 0]
            found = best_for_pair(facts, one, two)
            assert (found is None) == (not candidates), (system_id, one, two)
            if found:
                assert found.third_economies(one, two) == min(
                    body.third_economies(one, two) for body in candidates), (system_id, one, two)
    assert checked > 0


def test_nested_ports_and_asteroid_bases_on_real_bodies(pi):  # noqa: F811
    for system_id, facts, names in every_system(pi):
        for body in facts:
            # The Industrial Outpost can nest beside a port at any body.
            assert body.adds["Industrial"] > 0, (system_id, names[body.body_ref])
            # Refinery needs the ground, or a body that gives Refinery to a
            # nested colony outpost.
            assert (body.adds["Refinery"] > 0) == (
                "Refinery" in body.starts or "Refinery" in body.ground_adds), (system_id, names[body.body_ref])
        if system_id == SOL:
            by_name = {names[body.body_ref]: body for body in facts}
            # Sol's star has an asteroid belt, Saturn rings: room for an
            # asteroid base, which counts for Extraction only.
            assert by_name["Sol"].ringed and by_name["Saturn"].ringed
            assert by_name["Sol"].room("Extraction") == (0, True)
            assert by_name["Sol"].room("Military") == (0, False)
