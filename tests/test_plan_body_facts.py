"""The Plan page makes its body facts from the galaxy tables, not Ratings V4
("Ratings rebuild — design", build step 2).

Until 22 November 2026 the Plan page read six facts about each body from
Ratings V4's table (v3_derived.body_mechanics): its class, spectral class,
tidal lock, volcanism, whether it is terraformable, and its reserve. The
slot predictions took the class from there too. Now the page makes them from
the galaxy tables (plan_economy_facts.galaxy_body_facts), so retiring Ratings
V4 cannot break it.

Runs on PostgreSQL 18 with every migration applied; needs
RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("CORS_ORIGINS", "https://plan-body-facts-test.invalid")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "importer" / "src"))

from edfinder_api.routers.plan_economy_facts import galaxy_body_facts  # noqa: E402
from spansh_delta import speed_trial  # noqa: E402
from tests.test_atmosphere_and_pressure import ask  # noqa: E402
from tests.test_galaxy_scores import galaxy, template  # noqa: E402,F401
from tests.test_galaxy_update import SOL, fill_classes  # noqa: E402
from tests.test_reach import pi  # noqa: E402,F401

RATINGS_V4_FACTS = """
SELECT body_pk, body_class, spectral_class, volcanism, terraformable, tidally_locked, reserve_level
  FROM v3_derived.body_mechanics
 WHERE derived_generation_id = (
           SELECT c.derived_generation_id
             FROM v3_meta.current_derived_generation c
             JOIN v3_meta.derived_generation d USING (derived_generation_id)
            WHERE d.lifecycle_state = 'PUBLISHED')
   AND system_id64 = %s
"""
FACTS = ("body_class", "spectral_class", "volcanism", "terraformable", "tidally_locked", "reserve_level")


def test_the_galaxy_tables_give_every_fact_ratings_v4_gave(pi):  # noqa: F811
    # Every body on the Pi has its class (the copy filled them in).
    fill_classes(pi.connection, pi.schema, pi.records.values())
    compared = 0
    for system_id in sorted(pi.records):
        rows = speed_trial.read(pi.connection, pi.schema, [system_id])[system_id]
        ours = galaxy_body_facts(rows.bodies, rows.rings)
        ratings_v4 = {row[0]: dict(zip(FACTS, row[1:], strict=True))
                      for row in pi.connection.execute(RATINGS_V4_FACTS, (system_id,))}
        assert ours == ratings_v4, system_id
        compared += len(ours)
    # Every body of the twelve systems that is still there, but for the
    # barycentres and belt clusters, which have no facts.
    assert compared == 360


def test_the_plan_page_has_its_facts_with_ratings_v4_gone(pi):  # noqa: F811
    from edfinder_api.routers import plan

    fill_classes(pi.connection, pi.schema, pi.records.values())
    pi.connection.execute("ALTER TABLE v3_derived.body_mechanics RENAME TO body_mechanics_gone")
    pi.connection.execute(
        "ALTER TABLE v3_meta.current_derived_generation RENAME TO current_derived_generation_gone")

    counted = {"still there": 0, "with a class": 0, "with ground slots": 0}
    for system_id in sorted(pi.records):
        page = ask(pi, lambda pool, system_id=system_id: plan.bodies(str(system_id), pool))
        # The facts come from the galaxy, so the page names its generation.
        assert page.economy_facts_generation_id == page.generation_id, system_id
        assert None not in (page.has_black_hole, page.has_white_dwarf, page.has_neutron_star)
        for body in page.bodies:
            if body.lifecycle_state == "ACTIVE":
                counted["still there"] += 1
                counted["with a class"] += body.economy_kind is not None
                counted["with ground slots"] += body.prediction_status == "predicted"
    # As with Ratings V4: every body but the barycentres and belt clusters has
    # a class, and every body that can be landed on its ground slots.
    assert counted == {"still there": 389, "with a class": 360, "with ground slots": 191}

    sol = ask(pi, lambda pool: plan.bodies(str(SOL), pool))
    by_name = {body.name: body for body in sol.bodies}
    earth, mercury = by_name["Earth"], by_name["Mercury"]
    assert (earth.economy_kind, earth.tidally_locked, earth.volcanism_present, earth.terraformable) == (
        "earth_like_world", False, True, False)
    assert (mercury.economy_kind, mercury.predicted_surface_slots) == ("metal_rich_body", 2)
    assert sol.reserve_level == "common"
