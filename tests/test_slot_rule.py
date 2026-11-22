"""The ground-slot rule is Raven Colonial's (apps/api/src/ingest/slot_prediction.py).

Checked three ways:

1. Against the game. These six bodies had their ground slots read in the
   game's System Architect view in November 2026. Their systems are not named,
   and their radius, temperature and gravity are rounded; the rule gives the
   same answer either way. Until 22 November 2026 the rule here gave three of
   the six one slot off.
2. On real bodies from Spansh, one for each way that older rule differed from
   Raven's: life signals add nothing, any atmosphere adds 2, the bonuses have
   no limit of their own (only the 7 overall), and 3 base slots reach up to
   6,000 km.
3. Against the owner's sheet of in-game counts: 7,403 bodies in 446 systems
   (tests/fixtures/slot_counts). The rule is right on all but three of them.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from edfinder_api.domain.ratings_v4_canonical import load_source_fixture
from edfinder_api.ingest.slot_prediction import PREDICTION_VERSION, predict_body_slots

ROOT = Path(__file__).resolve().parents[1]
HMC = "High metal content world"


def ground_slots(*, radius_km: float, temperature_k: float, gravity_g: float, atmosphere: str,
                 planet_class: str, terraform_state: str | None = "Not terraformable",
                 volcanism: str | None = None, geological: int = 0) -> int | None:
    """The rule's answer for a body that can be landed on."""
    return predict_body_slots({
        "body_id": 1, "is_landable": True, "radius": radius_km * 1000,
        "surface_temp": temperature_k, "gravity": gravity_g, "atmosphere": atmosphere,
        "planet_class": planet_class, "terraform_state": terraform_state,
        "volcanism": volcanism, "geo_signal_count": geological,
    }).predicted_ground_slots


# Read in the game, November 2026.
IN_GAME = [
    # A high metal content world with no signals. The older rule gave a thin
    # atmosphere 1 rather than 2, so it said 4.
    (dict(radius_km=2940, temperature_k=220, gravity_g=0.5, atmosphere="Thin Carbon dioxide",
          planet_class=HMC), 5),
    # Two more like it, each with a life signal, which adds nothing.
    (dict(radius_km=2280, temperature_k=160, gravity_g=0.3, atmosphere="Thin Sulphur dioxide",
          planet_class=HMC), 5),
    (dict(radius_km=2720, temperature_k=180, gravity_g=0.4, atmosphere="Thin Carbon dioxide",
          planet_class=HMC), 5),
    # Small airless rocky moons. Volcanism and geological signals add 1
    # between them. The first and third also have a life signal, which the
    # older rule counted, so it said 3 and 4.
    (dict(radius_km=850, temperature_k=470, gravity_g=0.1, atmosphere="No atmosphere",
          planet_class="Rocky body", volcanism="Minor Rocky Magma", geological=3), 2),
    (dict(radius_km=730, temperature_k=470, gravity_g=0.1, atmosphere="No atmosphere",
          planet_class="Rocky body", volcanism="No volcanism"), 1),
    (dict(radius_km=1540, temperature_k=470, gravity_g=0.2, atmosphere="No atmosphere",
          planet_class="Rocky body", volcanism="Minor Metallic Magma", geological=3), 3),
]


@pytest.mark.parametrize(("body", "in_game"), IN_GAME)
def test_the_rule_gives_the_count_read_in_the_game(body, in_game):
    assert ground_slots(**body) == in_game


def fixture_body(name: str) -> dict:
    """A body of the twelve real fixture systems, as Spansh's file holds it."""
    _, _, payloads = load_source_fixture(ROOT / "tests" / "fixtures" / "ratings_v4_sources")
    for payload in payloads:
        for body in payload["system"]["bodies"]:
            if body["name"] == name:
                return body
    raise KeyError(name)


def rule_on(record: dict) -> int | None:
    """The rule's answer for a body as Spansh records it."""
    signals = (record.get("signals") or {}).get("signals") or {}
    return ground_slots(
        radius_km=record["radius"], temperature_k=record["surfaceTemperature"],
        gravity_g=record["gravity"], atmosphere=record.get("atmosphereType") or "No atmosphere",
        planet_class=record["subType"], terraform_state=record.get("terraformingState"),
        volcanism=record.get("volcanismType"),
        geological=sum(count for kind, count in signals.items() if "Geological" in kind))


@pytest.mark.parametrize(("name", "slots", "before"), [
    # 5,566 km with a thin neon atmosphere and a life signal: 3 + 2. The older
    # rule said 6: 4 from 5,500 km, 1 for the thin atmosphere, 1 for life.
    ("Maia B 4 e", 5, 6),
    # 4,940 km with a thin argon-rich atmosphere: 3 + 2. The older rule said 4.
    ("Borann B 1", 5, 4),
    # 5,759 km, high metal content, no atmosphere: 3 + 1. The older rule said 5.
    ("Deriv-Dar AB 3", 4, 5),
    # 3,936 km, high metal content, thin carbon dioxide: 3 + 1 + 2. The older
    # rule said 5.
    ("Deriv-Dar C 2", 6, 5),
])
def test_each_way_the_older_rule_differed(name, slots, before):
    assert rule_on(fixture_body(name)) == slots != before


# Two bodies from Spansh's body search, read on 22 November 2026.
GOMM_CRUA_B_3 = {
    "radius": 2971.875, "surfaceTemperature": 248.617737, "gravity": 0.460104211277659,
    "atmosphereType": "Thin Carbon dioxide", "subType": HMC, "terraformingState": "Terraformable",
    "volcanismType": "No volcanism",
}
UGRASHTRIM_3 = {
    "radius": 6756.98, "surfaceTemperature": 251.060104, "gravity": 1.26448597940247,
    "atmosphereType": "Thin Carbon dioxide", "subType": HMC, "terraformingState": "Terraformable",
    "volcanismType": "Minor Metallic Magma", "signals": {"signals": {"$SAA_SignalType_Geological;": 3}},
}


def test_the_bonuses_have_no_limit_of_their_own():
    # 2 + 1 (high metal content) + 1 (terraformable) + 2 (atmosphere). The
    # older rule allowed at most 3 for the bonuses together, so it said 5.
    assert rule_on(GOMM_CRUA_B_3) == 6


def test_never_more_than_seven():
    # 4 + 1 + 1 + 1 (volcanism and geology) + 2 would be 9.
    assert rule_on(UGRASHTRIM_3) == 7


def test_the_answers_carry_the_new_version_name():
    # The Plan page's export shows it, so a plan made with the older rule can
    # be told apart.
    assert PREDICTION_VERSION == "raven-slot-v1"


SHEET = ROOT / "tests" / "fixtures" / "slot_counts" / "in_game_slot_counts.csv"
# The sheet's bodies where the game has a slot more or fewer than the rule:
# (in the game, by the rule). Two hot metal-rich planets with one more, though
# the other 74 metal-rich planets between 600 and 700 K come out right; and
# one whose water geysers add no slot, though they do on nine other bodies.
MISSES = {
    ("2MASS J05363149+0945016", "B 5"): (3, 4),
    ("DM99 4.3", "1"): (6, 5),
    ("Dryio Flyuae OZ-O d6-1381", "1"): (7, 6),
}


def sheet_rows() -> list[dict[str, str]]:
    with SHEET.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def rule_on_sheet(row: dict[str, str]) -> int | None:
    """The rule's answer for a row of the sheet, given as the Plan page gives it."""
    return predict_body_slots({
        "body_id": row["body_id64"], "is_landable": row["landable"] == "true",
        "radius": float(row["radius_km"]) * 1000, "surface_temp": float(row["temperature_k"]),
        "gravity": float(row["gravity_g"]), "atmosphere": row["atmosphere"],
        "planet_class": row["subtype"],
        "terraform_state": "Terraformable" if row["terraformable"] == "true" else "Not terraformable",
        "volcanism": row["volcanism"], "geo_signal_count": 1 if row["geological"] == "true" else 0,
    }).predicted_ground_slots


def test_the_rule_against_every_count_in_the_sheet():
    rows = sheet_rows()
    assert len(rows) == 7403
    assert sum(1 for row in rows if row["landable"] == "true") == 4599
    wrong = {
        (row["system_name"], row["body"]): (int(row["ground_slots"]), slots)
        for row in rows if (slots := rule_on_sheet(row)) != int(row["ground_slots"])
    }
    # Right on 4,596 of the 4,599 bodies that can be landed on (99.93%), and
    # on every one that cannot. The rule used until 22 November 2026 was right
    # on 4,273 of them (92.91%).
    assert wrong == MISSES


def test_the_sheet_has_each_body_once():
    rows = sheet_rows()
    assert len({row["body_id64"] for row in rows}) == len(rows)
    assert {row["landable"] for row in rows} == {"true", "false"}
