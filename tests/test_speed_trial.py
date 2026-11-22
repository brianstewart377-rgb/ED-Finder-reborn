"""The speed trial (spansh_delta.speed_trial): it gives the planner what the
Plan page gives it, from the galaxy tables alone, and it writes nothing.

The Plan page takes six facts about each body from Ratings V4
(v3_derived.body_mechanics). The trial makes them from the galaxy tables, as
the ratings rebuild will (design doc, build steps 1 and 2). For every body of
the twelve real fixture systems, that has to come out the same.

Runs on PostgreSQL 18 with every migration applied; needs
RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://speed-trial-test.invalid")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "importer" / "src"))

from spansh_delta import speed_trial  # noqa: E402
from tests.test_atmosphere_and_pressure import ask  # noqa: E402
from tests.test_galaxy_scores import galaxy, template  # noqa: E402,F401
from tests.test_galaxy_update import SOL, _environment, fill_classes  # noqa: E402
from tests.test_reach import WITHIN_500_OF_SOL, inhabit, pi, position, work_out  # noqa: E402,F401


def test_the_planner_gets_what_the_plan_page_gives_it(pi):  # noqa: F811
    from edfinder_api.routers import plan

    # Every body on the Pi has its class (the copy filled them in).
    fill_classes(pi.connection, pi.schema, pi.records.values())
    bodies_compared = 0
    for system_id in sorted(pi.records):
        page = ask(pi, lambda pool, system_id=system_id: plan.bodies(str(system_id), pool))
        rows = speed_trial.read(pi.connection, pi.schema, [system_id])[system_id]
        ours = speed_trial.planner_inputs(rows)

        assert (ours.system.reserve, ours.system.has_black_hole, ours.system.has_white_dwarf,
                ours.system.has_neutron_star) == (
            page.reserve_level, page.has_black_hole, page.has_white_dwarf, page.has_neutron_star), system_id
        belt_parents = {belt.parent_body_id for belt in page.belts if belt.lifecycle_state == "ACTIVE"}
        expected = {
            body.body_id: (
                body.economy_kind, body.economy_parent_id, bool(body.in_binary_pair),
                body.tidally_locked, body.volcanism_present, body.terraformable,
                body.has_rings or body.body_id in belt_parents,
                body.geological_signals, body.biological_signals, body.predicted_surface_slots,
            )
            for body in page.bodies if body.lifecycle_state == "ACTIVE"
        }
        found = {
            body.body_ref: (
                body.kind, body.parent_ref, body.in_binary_pair, body.tidally_locked,
                body.volcanism, body.terraformable, body.has_rings,
                body.geological_signals, body.biological_signals, ours.slots[body.body_ref],
            )
            for body in ours.bodies
        }
        assert found == expected, system_id
        bodies_compared += len(found)
    # Every body of the twelve systems that is still there.
    assert bodies_compared == 389


def test_every_real_system_is_worked_out_and_its_best_bodies_found(pi):  # noqa: F811
    fill_classes(pi.connection, pi.schema, pi.records.values())
    found = speed_trial.read(pi.connection, pi.schema, sorted(pi.records))
    tiers = speed_trial.best_tiers()
    for system_id, rows in found.items():
        facts = speed_trial.body_facts(speed_trial.planner_inputs(rows), tiers)
        singles, pairs = speed_trial.best_bodies(facts)
        refs = {body.body_ref for body in facts}
        assert set(singles.values()) <= refs and set(pairs.values()) <= refs, system_id
    # Sol: Earth starts with Agriculture, High Tech, Military and Tourism.
    sol = speed_trial.body_facts(speed_trial.planner_inputs(found[SOL]), tiers)
    earth = next(body for body in sol if body.kind == "earth_like_world")
    assert set(earth.starts) == {"Agriculture", "HighTech", "Military", "Tourism"}


class Recorder:
    """A connection that keeps every statement it is given."""

    def __init__(self, connection):
        self.connection, self.statements = connection, []

    def execute(self, query, params=None, **options):
        text = query if isinstance(query, str) else query.as_string(self.connection)
        self.statements.append(" ".join(text.split()))
        return self.connection.execute(query, params, **options)

    def __getattr__(self, name):
        return getattr(self.connection, name)


def test_the_trial_takes_systems_within_500_ly_and_writes_nothing(pi):  # noqa: F811
    fill_classes(pi.connection, pi.schema, pi.records.values())
    inhabit(pi, SOL, *position(pi, SOL))
    work_out(pi)
    recorder = Recorder(pi.connection)
    notes = []
    trial = speed_trial.run(recorder, limit=100, progress=notes.append)

    assert trial.in_reach == trial.sampled == len(WITHIN_500_OF_SOL)
    active = pi.one("SELECT count(*) FROM {s}.bodies WHERE system_id64 = ANY(%s) AND lifecycle_state = 'ACTIVE'",
                    sorted(WITHIN_500_OF_SOL))[0]
    assert (trial.with_bodies, trial.bodies, trial.failed) == (len(WITHIN_500_OF_SOL), active, 0)
    assert trial.reading_seconds > 0 and trial.working_seconds > 0
    # Only reads, on a session that refuses writes.
    assert {statement.split()[0].upper() for statement in recorder.statements} <= {"SELECT", "SET", "WITH"}
    assert pi.one("SHOW default_transaction_read_only") == ("on",)
    lines = trial.lines()
    assert lines[0] == f"Systems within 500 ly: {len(WITHIN_500_OF_SOL)}. Sample: {len(WITHIN_500_OF_SOL)}, " \
                       f"picked in {trial.picking_seconds:.1f} s."
    assert "A night's changed systems (they have bodies), at most, in one process:" in lines
    assert any(line.startswith(f"Every system within 500 ly ({len(WITHIN_500_OF_SOL)}): at most ")
               for line in lines)


def test_a_smaller_sample_is_the_same_systems_every_time(pi):  # noqa: F811
    inhabit(pi, SOL, *position(pi, SOL))
    work_out(pi)
    first, in_reach = speed_trial.sample(pi.connection, 3)
    assert len(first) == 3 and set(first) <= WITHIN_500_OF_SOL and in_reach == len(WITHIN_500_OF_SOL)
    assert speed_trial.sample(pi.connection, 3)[0] == first


def test_without_reach_the_trial_refuses(galaxy):  # noqa: F811
    # The old server's database keeps no reach, so there is nothing to sample.
    with pytest.raises(speed_trial.Refused, match="does not keep reach"):
        speed_trial.run(galaxy.connection, limit=10, progress=None)


def test_before_reach_is_worked_out_the_trial_refuses(pi):  # noqa: F811
    with pytest.raises(speed_trial.Refused, match="reach has not been worked out yet"):
        speed_trial.run(pi.connection, limit=10, progress=None)


def test_the_command_says_what_it_found_and_that_nothing_changed(pi, monkeypatch, capsys):  # noqa: F811
    fill_classes(pi.connection, pi.schema, pi.records.values())
    inhabit(pi, SOL, *position(pi, SOL))
    work_out(pi)
    _environment(monkeypatch, pi.conninfo)
    assert speed_trial.main(["run", "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Speed trial of the planner's economy rules, on 5 systems. Read only.")
    assert "Sample: 5," in out and "Could not be worked out: 0." in out
    assert out.rstrip().endswith("DONE: nothing was changed.")
    assert speed_trial.main(["run", "--limit", "0"]) == 1
    assert "FAILED: --limit must be 1 or more" in capsys.readouterr().out


@pytest.mark.parametrize(("seconds", "words"), [
    (20, "under a minute"), (90 * 60, "1 h 30 min"), (45 * 60, "45 min"),
    (3 * 86400 + 3600, "3 d 1 h 0 min"),
])
def test_durations_in_plain_words(seconds, words):
    assert speed_trial.duration(seconds) == words
