"""Time the planner's economy rules on this machine's own galaxy. Read only.

    python -m spansh_delta.speed_trial run [--limit N]

The ratings rebuild ("Ratings rebuild — design", build step 1) works out, for
every body of a system, three facts from the planner's own rulebook code
(``edfinder_api.cpe``): what a colony port there would start with, what the
best facility of each of the seven economies would add to it, and what other
economies that port would have. Then, for each economy and each pair of
economies, the system's best body. Nothing of that is stored yet. Before it is
built, this times it here, in two parts:

  reading      each system's bodies, rings and signals from the galaxy tables
               alone: no Ratings V4 table. 500 systems at a time.
  working out  the facts and the best bodies, in Python, in one process.

It takes N systems (20,000 unless told otherwise) at random from those within
500 ly of an inhabited system (``v3_state.system_reach``, migration 028), the
same ones for the same N while reach stays the same, and says how long a
night's run and a run over every system within 500 ly would take. Both
figures are the most they would take: a night's systems are counted as if all
the reading were for them, and a run over everything would read in order,
which is quicker than this random sample.

Nothing is written. Every statement is a SELECT, and the connection is read
only.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
import itertools
import random
import resource
import sys
import time
from typing import Any

import psycopg
from psycopg import sql

from edfinder_api.cpe.catalogue import planner_facilities
from edfinder_api.cpe.economy import EconomyBody, EconomySystem, _Evaluation
from edfinder_api.cpe.plan import Site
from edfinder_api.routers.plan_economy_facts import (
    aggregate_reserve_levels,
    economy_kind,
    economy_topology,
)
from edfinder_api.routers.plan_slot_predictions import predict_surface_slots

from .apply import EXIT_NO_CONNECTION, Refused, _reason, connect, preflight, say

ECONOMIES = ("Agriculture", "Extraction", "HighTech", "Industrial", "Military", "Refinery", "Tourism")
PAIRS = tuple(itertools.combinations(ECONOMIES, 2))
# Refinery and Industrial are ranked by what a port and its ground could build.
GROUND_FIRST = ("Industrial", "Refinery")
DEFAULT_SYSTEMS = 20_000
BATCH_SYSTEMS = 500
# The sample is the same for the same N, as long as reach is the same.
SEED = 22_112_026
# A night's run: what the nightly scoring has taken on the Pi so far is about
# 200,000 systems (207,644 on 14 November 2026). Shown at half and twice that.
NIGHTS = (100_000, 200_000, 400_000)
# The Pi has four cores.
PROCESSES = 4
STATEMENT_TIMEOUT = "5min"

REACH_SQL = "SELECT systems FROM v3_state.reach_log ORDER BY reach_id DESC LIMIT 1"
SAMPLE_SQL = (
    "SELECT system_id64 FROM v3_state.system_reach TABLESAMPLE BERNOULLI (%s) REPEATABLE (%s)"
)
# As the Plan page reads a system's bodies (routers/plan.py), for many systems
# at once, with the facts it takes from Ratings V4 read from the galaxy tables
# themselves: the class (migration 027), tidal lock, and the words for
# volcanism and terraforming.
BODIES_SQL = """
SELECT body.system_id64, body.body_pk, body.name, body.direct_parent_body_pk,
       body_type.public_code AS body_type, body.is_main_star, body.is_landable,
       body.radius_km, body.surface_gravity_g, body.surface_temperature_k,
       body.surface_pressure_atm, atmosphere.display_name AS atmosphere,
       terraforming.display_name AS terraforming_state,
       volcanism.display_name AS volcanism, body.is_tidally_locked, body.body_class,
       body.spectral_class, body.signals_complete, body.lifecycle_state
  FROM {schema}.bodies body
  LEFT JOIN v3_vocab.body_type body_type USING (body_type_id)
  LEFT JOIN v3_vocab.atmosphere_classification atmosphere USING (atmosphere_classification_id)
  LEFT JOIN v3_vocab.terraforming_state terraforming USING (terraforming_state_id)
  LEFT JOIN v3_vocab.volcanism_type volcanism USING (volcanism_type_id)
 WHERE body.system_id64 = ANY(%s::bigint[])
"""
RINGS_SQL = """
SELECT ring.system_id64, ring.body_pk, ring.kind, reserve_type.display_name AS reserve,
       ring.lifecycle_state
  FROM {schema}.rings ring
  LEFT JOIN v3_vocab.reserve_type reserve_type USING (reserve_type_id)
 WHERE ring.system_id64 = ANY(%s::bigint[])
"""
SIGNALS_SQL = """
SELECT body.system_id64, signal.body_pk,
       COALESCE(SUM(signal.signal_count) FILTER
           (WHERE signal_type.display_name = 'Geological'), 0) AS geological_signals,
       COALESCE(SUM(signal.signal_count) FILTER
           (WHERE signal_type.display_name = 'Biological'), 0) AS biological_signals
  FROM {schema}.body_signal_current signal
  JOIN {schema}.bodies body USING (body_pk)
  JOIN v3_vocab.signal_type signal_type USING (signal_type_id)
 WHERE body.system_id64 = ANY(%s::bigint[])
   AND signal_type.display_name IN ('Geological', 'Biological')
 GROUP BY body.system_id64, signal.body_pk
"""


# -- reading ------------------------------------------------------------------


@dataclass
class SystemRows:
    """One system's rows, as the queries above give them."""

    bodies: list[dict[str, Any]] = field(default_factory=list)
    rings: list[dict[str, Any]] = field(default_factory=list)
    signals: dict[int, dict[str, Any]] = field(default_factory=dict)


def _rows(connection: psycopg.Connection[Any], statement: str, schema: str,
          system_ids: Sequence[int]) -> Iterator[dict[str, Any]]:
    cursor = connection.execute(
        sql.SQL(statement).format(schema=sql.Identifier(schema)), (list(system_ids),))
    names = [column.name for column in cursor.description]
    for row in cursor:
        yield dict(zip(names, row, strict=True))


def read(connection: psycopg.Connection[Any], schema: str,
         system_ids: Sequence[int]) -> dict[int, SystemRows]:
    """These systems' bodies, rings and signals, from the galaxy tables."""
    found = {system_id: SystemRows() for system_id in system_ids}
    for row in _rows(connection, BODIES_SQL, schema, system_ids):
        found[row["system_id64"]].bodies.append(row)
    for row in _rows(connection, RINGS_SQL, schema, system_ids):
        found[row["system_id64"]].rings.append(row)
    for row in _rows(connection, SIGNALS_SQL, schema, system_ids):
        found[row["system_id64"]].signals[row["body_pk"]] = row
    for rows in found.values():
        rows.bodies.sort(key=lambda body: body["body_pk"])
    return found


# -- what the planner is given ---------------------------------------------------


@dataclass(frozen=True)
class PlannerInputs:
    """What the Plan page gives the planner for one system, made from the
    galaxy tables alone."""

    bodies: tuple[EconomyBody, ...]
    system: EconomySystem
    # The Plan page's ground-slot prediction for each body (None: none made).
    slots: Mapping[str, int | None]
    rows: Mapping[str, Mapping[str, Any]]


def _words(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def mechanics(rows: SystemRows) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]], set[int]]:
    """The body facts the Plan page reads from Ratings V4, made the way the
    frozen scoring makes them (domain/ratings_v4_canonical.py): only for a
    body that is still there and is not a barycentre or a belt cluster; a
    reserve from the body's own rings and belts. Also the belts, and the
    bodies that have rings."""
    reserves: dict[int, set[str]] = defaultdict(set)
    belts, ringed = [], set()
    for ring in rows.rings:
        if ring["lifecycle_state"] != "ACTIVE":
            continue
        if ring["reserve"] is not None:
            reserves[ring["body_pk"]].add(_words(ring["reserve"]))
        if ring["kind"] == "BELT":
            belts.append({"body_pk": ring["body_pk"], "reserve": ring["reserve"],
                          "lifecycle_state": ring["lifecycle_state"]})
        else:
            ringed.add(ring["body_pk"])
    facts = {}
    for body in rows.bodies:
        if body["lifecycle_state"] != "ACTIVE" or _words(body["body_type"]).replace("_", " ") in (
                "barycentre", "belt cluster"):
            continue
        volcanism, terraforming = _words(body["volcanism"]), _words(body["terraforming_state"])
        own = reserves.get(body["body_pk"], set())
        facts[body["body_pk"]] = {
            "body_class": body["body_class"],
            "spectral_class": body["spectral_class"],
            "volcanism": volcanism != "no volcanism" if volcanism else None,
            "terraformable": terraforming != "not terraformable" if terraforming else None,
            "tidally_locked": body["is_tidally_locked"],
            "reserve_level": next(iter(own)) if len(own) == 1 else None,
        }
    return facts, belts, ringed


def planner_inputs(rows: SystemRows) -> PlannerInputs:
    """The planner's inputs for one system, as the Plan page makes them
    (routers/plan.py), from these rows alone."""
    facts, belts, ringed = mechanics(rows)
    topology = economy_topology(rows.bodies)
    reserve, _ = aggregate_reserve_levels(rows.bodies, facts, belts)
    kinds = {body_pk: economy_kind(fact["body_class"], fact["spectral_class"])
             for body_pk, fact in facts.items()}
    belt_parents = {belt["body_pk"] for belt in belts}
    bodies, slots, by_ref = [], {}, {}
    for row in rows.bodies:
        if row["lifecycle_state"] != "ACTIVE":
            continue
        body_pk, ref = row["body_pk"], str(row["body_pk"])
        fact = facts.get(body_pk)
        signal = rows.signals.get(body_pk)
        parent, binary = topology.get(body_pk, (None, None))
        bodies.append(EconomyBody(
            body_ref=ref, parent_ref=parent, in_binary_pair=bool(binary),
            kind=kinds.get(body_pk),
            tidally_locked=fact["tidally_locked"] if fact else None,
            volcanism=fact["volcanism"] if fact else None,
            terraformable=fact["terraformable"] if fact else None,
            has_rings=body_pk in ringed or body_pk in belt_parents,
            geological_signals=signal["geological_signals"] if signal else 0,
            biological_signals=signal["biological_signals"] if signal else 0,
        ))
        slots[ref] = predict_surface_slots(row, fact, signal, generation_reason=None).predicted_surface_slots
        by_ref[ref] = row
    active = set(kinds.values())
    system = EconomySystem(
        reserve=reserve, has_black_hole="black_hole" in active,
        has_white_dwarf="white_dwarf" in active, has_neutron_star="neutron_star" in active)
    return PlannerInputs(tuple(bodies), system, slots, by_ref)


# -- working out ---------------------------------------------------------------


def best_tiers() -> dict[str, dict[str, int]]:
    """Per economy, the biggest strong link (hundredths) a facility that is
    not a port gives, in orbit and on the ground."""
    out = {name: {"orbital": 0, "surface": 0} for name in ECONOMIES}
    for template in planner_facilities().values():
        if template.is_port or template.economy not in out:
            continue
        base = round(template.strong_link_value * 100)
        if template.can_go_orbital:
            out[template.economy]["orbital"] = max(out[template.economy]["orbital"], base)
        if template.can_go_surface:
            out[template.economy]["surface"] = max(out[template.economy]["surface"], base)
    return out


@dataclass(frozen=True)
class BodyFacts:
    """For one body: what a port there starts with, and per economy the best
    link a facility there would add (orbit or ground) and the ground one."""

    body_ref: str
    kind: str
    starts: Mapping[str, float]
    link: Mapping[str, float]
    ground_link: Mapping[str, float]
    landable: bool
    slots: int


def body_facts(inputs: PlannerInputs, tiers: Mapping[str, Mapping[str, int]]) -> list[BodyFacts]:
    """The three facts for every body a port could go at."""
    evaluation = _Evaluation([], inputs.bodies, inputs.system)
    found = []
    for body in inputs.bodies:
        if body.kind is None:
            continue
        slots = inputs.slots[body.body_ref]
        landable = inputs.rows[body.body_ref]["is_landable"] is True and (slots is None or slots > 0)
        orbital = Site("port", "coriolis_station", body.body_ref, "orbital", "complete")
        ground = Site("hub", "refinery", body.body_ref, "surface", "complete")
        starts = {name: item.value / 100 for name, item in evaluation.own_economies(orbital).items()}
        link, ground_link = {}, {}
        for name in ECONOMIES:
            best = 0.0
            for place, site in (("orbital", orbital), ("surface", ground)):
                base = tiers[name][place]
                if base == 0 or (place == "surface" and not landable):
                    continue
                value = evaluation.modified_item(site, name, "strong_link", base, ()).value / 100
                best = max(best, value)
                if place == "surface":
                    ground_link[name] = value
            # A tier 1 port beside a bigger one at the same body passes its
            # economy on as a 0.4 link: the Industrial Outpost anywhere, the
            # Refinery one only where the body gives Refinery.
            if name == "Industrial" or (name == "Refinery" and "Refinery" in starts):
                best = max(best, evaluation.modified_item(orbital, name, "strong_link", 40, ()).value / 100)
            link[name] = round(best, 2)
        found.append(BodyFacts(body.body_ref, body.kind, starts, link, ground_link,
                               landable, (slots or 0) if landable else 0))
    return found


def built(body: BodyFacts, name: str) -> float:
    """What the port starts with, plus one facility on every ground slot."""
    return round(body.starts.get(name, 0.0) + body.slots * body.ground_link.get(name, 0.0), 2)


def best_bodies(facts: Sequence[BodyFacts]) -> tuple[dict[str, str], dict[str, str]]:
    """Per economy and per pair, the body that comes first, by the design's
    order: the link (for Refinery and Industrial, the total built on the
    ground), then fewer other economies, then a bigger start, then room on the
    ground."""
    singles, pairs = {}, {}
    for name in ECONOMIES:
        candidates = [body for body in facts if body.link[name] > 0]
        if candidates:
            singles[name] = max(candidates, key=lambda body: (
                built(body, name) if name in GROUND_FIRST else body.link[name],
                -sum(1 for other in body.starts if other != name),
                body.starts.get(name, 0.0), body.slots)).body_ref
    for first, second in PAIRS:
        candidates = [body for body in facts if body.link[first] > 0 and body.link[second] > 0]
        if candidates:
            pairs[f"{first}+{second}"] = max(candidates, key=lambda body: (
                min(body.link[first], body.link[second]), max(body.link[first], body.link[second]),
                -sum(1 for other in body.starts if other not in (first, second)),
                body.starts.get(first, 0.0) + body.starts.get(second, 0.0), body.slots)).body_ref
    return singles, pairs


# -- the trial -------------------------------------------------------------------


@dataclass
class Trial:
    in_reach: int = 0
    sampled: int = 0
    with_bodies: int = 0
    bodies: int = 0
    most_bodies: int = 0
    picking_seconds: float = 0.0
    reading_seconds: float = 0.0
    working_seconds: float = 0.0
    working_seconds_with_bodies: float = 0.0
    slowest_seconds: float = 0.0
    slowest_bodies: int = 0
    failed: int = 0
    first_failure: str = ""
    memory_mb: float = 0.0

    def lines(self) -> list[str]:
        def per(seconds: float, count: int) -> float:
            """Per 1,000 systems."""
            return 1000 * seconds / count if count else 0.0

        share = 100 * self.with_bodies / self.sampled if self.sampled else 0.0
        out = [
            f"Systems within 500 ly: {self.in_reach:,}. Sample: {self.sampled:,}, "
            f"picked in {self.picking_seconds:.1f} s.",
            f"  With bodies known: {self.with_bodies:,} ({share:.0f}%), "
            f"{self.bodies:,} bodies in all, at most {self.most_bodies:,} in one system.",
            f"Reading (galaxy tables only, {BATCH_SYSTEMS} systems at a time): "
            f"{self.reading_seconds:.1f} s, {per(self.reading_seconds, self.sampled):.2f} s "
            "per 1,000 systems.",
            f"Working out (one process): {self.working_seconds:.1f} s, "
            f"{per(self.working_seconds, self.sampled):.2f} s per 1,000 systems, "
            f"{per(self.working_seconds_with_bodies, self.with_bodies):.2f} s per 1,000 with bodies.",
            f"  Slowest system: {self.slowest_seconds:.2f} s ({self.slowest_bodies:,} bodies).",
            f"  Could not be worked out: {self.failed:,}" + (
                f" (the first: {self.first_failure})" if self.failed else "") + ".",
            f"Most memory used: {self.memory_mb:,.0f} MB.",
            "",
        ]
        if not self.with_bodies:
            return [*out, "No sampled system has bodies, so there is nothing to estimate from."]
        night = (self.reading_seconds + self.working_seconds_with_bodies) / self.with_bodies
        out.append("A night's changed systems (they have bodies), at most, in one process:")
        out += [f"  {count:>9,} systems: {duration(count * night)}" for count in NIGHTS]
        every = (self.reading_seconds + self.working_seconds) / self.sampled * self.in_reach
        out.append(f"Every system within 500 ly ({self.in_reach:,}): at most {duration(every)} "
                   f"in one process, {duration(every / PROCESSES)} in {PROCESSES} at once if the "
                   "database keeps up.")
        return out


def duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 1:
        return "under a minute"
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    parts = [f"{days} d"] if days else []
    if hours or days:
        parts.append(f"{hours} h")
    parts.append(f"{minutes} min")
    return " ".join(parts)


def sample(connection: psycopg.Connection[Any], limit: int) -> tuple[list[int], int]:
    """``limit`` systems at random from those within 500 ly, and how many
    there are."""
    if connection.execute("SELECT to_regclass('v3_state.system_reach')").fetchone()[0] is None:
        raise Refused("this database does not keep reach (migration 028), so there are no "
                      "systems within 500 ly to take a sample of")
    found = connection.execute(REACH_SQL).fetchone()
    if found is None or not found[0]:
        raise Refused("reach has not been worked out yet: python3 galaxy_update.py reach")
    in_reach = int(found[0])
    # A little over what is needed; more if chance gave too few.
    percent = min(100.0, 100.0 * 1.5 * limit / in_reach)
    while True:
        ids = sorted(row[0] for row in connection.execute(SAMPLE_SQL, (percent, SEED)))
        if len(ids) >= limit or percent >= 100.0:
            break
        percent = min(100.0, percent * 2)
    chosen = random.Random(SEED).sample(ids, min(limit, len(ids)))
    return sorted(chosen), in_reach


def run(connection: psycopg.Connection[Any], *, limit: int = DEFAULT_SYSTEMS,
        progress: Callable[[str], None] | None = say) -> Trial:
    """Time the reading and the working out for ``limit`` systems."""
    connection.execute("SET default_transaction_read_only = on")
    connection.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(STATEMENT_TIMEOUT)))
    target = preflight(connection, need_queue=False)
    trial = Trial()
    started = time.perf_counter()
    system_ids, trial.in_reach = sample(connection, limit)
    trial.picking_seconds = time.perf_counter() - started
    trial.sampled = len(system_ids)
    tiers = best_tiers()
    last_note = time.monotonic()
    for start in range(0, len(system_ids), BATCH_SYSTEMS):
        batch = system_ids[start:start + BATCH_SYSTEMS]
        began = time.perf_counter()
        found = read(connection, target.schema, batch)
        trial.reading_seconds += time.perf_counter() - began
        for system_id in batch:
            rows = found[system_id]
            began = time.perf_counter()
            try:
                best_bodies(body_facts(planner_inputs(rows), tiers))
            except (ArithmeticError, LookupError, TypeError, ValueError) as error:
                trial.failed += 1
                trial.first_failure = trial.first_failure or f"system {system_id}: {type(error).__name__}"
            took = time.perf_counter() - began
            trial.working_seconds += took
            count = sum(1 for body in rows.bodies if body["lifecycle_state"] == "ACTIVE")
            if count:
                trial.with_bodies += 1
                trial.bodies += count
                trial.most_bodies = max(trial.most_bodies, count)
                trial.working_seconds_with_bodies += took
            if took > trial.slowest_seconds:
                trial.slowest_seconds, trial.slowest_bodies = took, count
        if progress is not None and time.monotonic() - last_note >= 60:
            last_note = time.monotonic()
            progress(f"  {start + len(batch):,} of {trial.sampled:,} systems ...")
    trial.memory_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return trial


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m spansh_delta.speed_trial",
        description="Time the planner's economy rules on this machine's galaxy. Read only.")
    parser.add_argument("mode", choices=("run",))
    parser.add_argument("--limit", type=int, default=DEFAULT_SYSTEMS,
                        help=f"how many systems to take (default {DEFAULT_SYSTEMS:,})")
    args = parser.parse_args(argv)
    if args.limit < 1:
        say("FAILED: --limit must be 1 or more")
        return 1
    connection = connect()
    if connection is None:
        return EXIT_NO_CONNECTION
    with connection:
        say(f"Speed trial of the planner's economy rules, on {args.limit:,} systems. Read only.")
        try:
            trial = run(connection, limit=args.limit)
        except Refused as error:
            say(f"REFUSED: {error}")
            return 2
        except psycopg.Error as error:
            say(f"FAILED: {_reason(error)}")
            say("Nothing was changed.")
            return 1
    for line in trial.lines():
        say(line)
    say("DONE: nothing was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
