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
