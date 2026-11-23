"""Facts for one body, and which body suits an economy or a pair best.

"Ratings rebuild — design", build step 3: Find shows, for each body and each
economy, what a port there starts with and what each facility there adds;
this module works both out with the Plan page's own engine (economy.py), so
Find and the Plan page cannot disagree. Build step 4 stores the answers for
every system; nothing reads them yet.

Every number is the engine's: a port's own economies, and the strong link of
the best facility that is not a port, with the rulebook's bonuses and
penalties. Unknowns count as absent (no guessed signals or reserves).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
import itertools

from .catalogue import planner_facilities
from .economy import EconomyBody, EconomySystem, _Evaluation
from .plan import Site

# The seven economies a player builds for. Contraband and Terraforming are
# not offered: no body gives or boosts Contraband, and no facility builds
# Terraforming.
ECONOMIES = ('Agriculture', 'Extraction', 'HighTech', 'Industrial', 'Military', 'Refinery', 'Tourism')
PAIRS = tuple(itertools.combinations(ECONOMIES, 2))
# A small port nested beside a bigger one at the same body passes its own
# economies on as a tier 1 link (guide p. 96). Counted only for these two,
# where it is the only way into orbit: the Industrial Outpost at any body;
# for Refinery, a colony outpost at a body that gives Refinery.
NESTED_LINK = 40


@cache
def best_tiers() -> Mapping[str, Mapping[str, int]]:
    """Per economy, the biggest strong link (hundredths) a facility that is
    not a port gives, in orbit and on the ground."""
    out = {name: {'orbital': 0, 'surface': 0} for name in ECONOMIES}
    for template in planner_facilities().values():
        if template.is_port or template.economy not in out:
            continue
        base = round(template.strong_link_value * 100)
        if template.can_go_orbital:
            out[template.economy]['orbital'] = max(out[template.economy]['orbital'], base)
        if template.can_go_surface:
            out[template.economy]['surface'] = max(out[template.economy]['surface'], base)
    return out


@dataclass(frozen=True)
class BodyFacts:
    """What Find shows for one body.

    ``starts``: what a colony port there (Coriolis, Orbis and the like)
    starts with, by economy. ``adds``: what each facility there adds, by
    economy, in orbit or on the ground; 0 where none can go. ``ground_adds``:
    the same on the ground only. ``ground_slots``: predicted ground slots
    where facilities can go (0 when not landable or not predicted).
    ``ringed``: rings or an asteroid belt, room for an asteroid base.
    """

    body_ref: str
    kind: str
    starts: Mapping[str, float]
    adds: Mapping[str, float]
    ground_adds: Mapping[str, float]
    ground_slots: int
    ringed: bool

    def total(self, economy: str) -> float:
        """What the port starts with, plus one facility on every ground slot:
        the ground part, which can be added up ("up to"). Orbit can't: its
        slots are only known in game."""
        return round(self.starts.get(economy, 0.0)
                     + self.ground_slots * self.ground_adds.get(economy, 0.0), 2)

    def pair_totals(self, first: str, second: str) -> tuple[float, float]:
        """The weaker and the stronger of the two economies' ground parts,
        with the ground slots shared out between them as best helps the
        weaker."""
        best = (0.0, 0.0)
        for share in range(self.ground_slots + 1):
            one = self.starts.get(first, 0.0) + share * self.ground_adds.get(first, 0.0)
            two = self.starts.get(second, 0.0) + (self.ground_slots - share) * self.ground_adds.get(second, 0.0)
            best = max(best, (round(min(one, two), 2), round(max(one, two), 2)))
        return best

    def third_economies(self, *chosen: str) -> int:
        """How many economies the port there would have beyond the two at the
        top, once the chosen ones are built: a second economy is harmless,
        the top two tolerate each other (guide p. 69, 71)."""
        return max(0, len(set(self.starts) | set(chosen)) - 2)

    def room(self, *chosen: str) -> tuple[int, bool]:
        """Room, which only orders bodies the rules call equal: ground slots,
        then, for Extraction, rings or a belt (an asteroid base)."""
        return self.ground_slots, 'Extraction' in chosen and self.ringed


def body_facts(
    bodies: Sequence[EconomyBody],
    system: EconomySystem,
    ground_slots: Mapping[str, int | None],
    landable: Mapping[str, bool],
) -> list[BodyFacts]:
    """The facts for every body a port could go at (its kind is known).

    ``ground_slots``: each body's predicted ground slots (None: none made);
    ``landable``: whether each body can be landed on.
    """
    tiers = best_tiers()
    evaluation = _Evaluation([], bodies, system)
    found = []
    for body in bodies:
        if body.kind is None:
            continue
        slots = ground_slots.get(body.body_ref)
        on_ground = landable.get(body.body_ref) is True and (slots is None or slots > 0)
        orbital = Site('port', 'coriolis_station', body.body_ref, 'orbital', 'complete')
        ground = Site('hub', 'refinery', body.body_ref, 'surface', 'complete')
        starts = {name: item.value / 100 for name, item in evaluation.own_economies(orbital).items()}
        adds, ground_adds = {}, {}
        for name in ECONOMIES:
            best = 0.0
            for place, site in (('orbital', orbital), ('surface', ground)):
                base = tiers[name][place]
                if base == 0 or (place == 'surface' and not on_ground):
                    continue
                value = evaluation.modified_item(site, name, 'strong_link', base, ()).value / 100
                best = max(best, value)
                if place == 'surface':
                    ground_adds[name] = value
            if name == 'Industrial' or (name == 'Refinery' and 'Refinery' in starts):
                nested = evaluation.modified_item(orbital, name, 'strong_link', NESTED_LINK, ())
                best = max(best, nested.value / 100)
            adds[name] = round(best, 2)
        found.append(BodyFacts(
            body.body_ref, body.kind, starts, adds, ground_adds,
            (slots or 0) if on_ground else 0, body.has_rings,
        ))
    return found


def _first(candidates: Sequence[BodyFacts], key: Callable[[BodyFacts], tuple]) -> BodyFacts | None:
    """The body with the biggest key; on a tie, the first one given."""
    if not candidates:
        return None
    return max(enumerate(candidates), key=lambda pair: (key(pair[1]), -pair[0]))[1]


def strongest(facts: Sequence[BodyFacts], economy: str) -> BodyFacts | None:
    """Find's default for one economy: the biggest ground part (decision 8),
    then what each facility adds, then fewer third economies, then what the
    port starts with, then room. Only bodies where a facility can add it."""
    return _first([body for body in facts if body.adds[economy] > 0], lambda body: (
        body.total(economy), body.adds[economy], -body.third_economies(economy),
        body.starts.get(economy, 0.0), body.room(economy)))


def purest(facts: Sequence[BodyFacts], economy: str) -> BodyFacts | None:
    """The guide's "pure market" choice (p. 51-52): fewest third economies
    once it is built, a second one being harmless (second review, rows 3 and
    4), then a port that starts with it, then the ground part, what each
    facility adds and room."""
    return _first([body for body in facts if body.adds[economy] > 0], lambda body: (
        -body.third_economies(economy), economy in body.starts,
        body.total(economy), body.adds[economy], body.room(economy)))


def best_for_pair(facts: Sequence[BodyFacts], first: str, second: str) -> BodyFacts | None:
    """The one body that suits both: fewest third economies first (decision
    7), then the weaker and the stronger ground part with the ground slots
    shared out (decision 8), then the weaker and the stronger of what each
    facility adds, then what the port starts with for the two, then room."""
    def key(body: BodyFacts) -> tuple:
        adds = (body.adds[first], body.adds[second])
        return (-body.third_economies(first, second), body.pair_totals(first, second),
                min(adds), max(adds),
                round(body.starts.get(first, 0.0) + body.starts.get(second, 0.0), 2),
                body.room(first, second))
    return _first([body for body in facts if body.adds[first] > 0 and body.adds[second] > 0], key)
