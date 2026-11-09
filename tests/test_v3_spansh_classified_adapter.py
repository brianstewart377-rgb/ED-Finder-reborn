from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from itertools import permutations
from pathlib import Path
import sys
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "importer" / "src"))

from v3_spansh.adapter import SpanshAdapter
from v3_spansh.contracts import COPY_COLUMNS, KM_PER_AU, KM_PER_SOLAR_RADIUS
from v3_spansh_classified.adapter import ClassifiedSpanshAdapter, normalize_system


OBSERVED_AT = datetime(2026, 9, 20, tzinfo=timezone.utc)
SOURCE = {"source_id": 40, "source_run_id": UUID(int=40), "observed_at": OBSERVED_AT}


def star(identifier: int, **extra) -> dict:
    return {
        "id64": identifier,
        "bodyId": identifier,
        "name": f"Test {identifier}",
        "type": "Star",
        "subType": "G (White-Yellow) Star",
        **extra,
    }


def system(*bodies: dict) -> dict:
    return {
        "id64": 100,
        "name": "Test",
        "date": "2026-09-20T00:00:00Z",
        "coords": {"x": 1, "y": 2, "z": 3},
        "bodies": list(bodies),
    }


def selected_body_ids(record: dict) -> list[int]:
    normalized, _ = normalize_system(record)
    return [body["id64"] for body in normalized["bodies"] if body["mainStar"]]


def body_rows(batch) -> list[dict]:
    return [dict(zip(COPY_COLUMNS["bodies"], row, strict=True)) for row in batch.rows["bodies"]]


@pytest.mark.parametrize("field", ("mainStar", "isMainStar", "is_main_star", "MainStar"))
def test_explicit_main_star_wins_over_zero_distance_and_body_zero(field):
    record = system(star(10, bodyId=0, distanceToArrival=0), star(20, distanceToArrival=1000, **{field: True}))
    assert selected_body_ids(record) == [20]


def test_unflagged_zero_distance_wins_over_frontier_body_zero():
    record = system(star(10, bodyId=0, distanceToArrival=10), star(20, distanceToArrival=0))
    assert selected_body_ids(record) == [20]


def test_frontier_body_zero_wins_when_no_zero_distance_exists():
    record = system(star(10, bodyId=0), star(20, distanceToArrival=1))
    assert selected_body_ids(record) == [10]


def test_unflagged_nearest_finite_nonnegative_distance_wins():
    record = system(
        star(10, distanceToArrival=-1), star(20, distanceToArrival=float("nan")),
        star(30), star(40, distanceToArrival=20), star(50, distanceToArrival=2),
    )
    assert selected_body_ids(record) == [50]


def test_unknown_distances_fall_back_to_stable_body_identity():
    record = system(star(30), star(10), star(20, distanceToArrival=float("inf")))
    assert selected_body_ids(record) == [10]


def test_arrival_distance_ties_select_same_star_independent_of_dump_order():
    bodies = [star(30, distanceToArrival=1), star(10, distanceToArrival=1), star(20, distanceToArrival=1)]
    for ordering in permutations(bodies):
        assert selected_body_ids(system(*ordering)) == [10]


def test_missing_external_body_ids_still_have_order_independent_selection():
    bodies = [star(10), star(20), star(30)]
    for body in bodies:
        del body["id64"]
    selected_names = set()
    for ordering in permutations(bodies):
        normalized, _ = normalize_system(system(*ordering))
        selected_names.add(next(body["name"] for body in normalized["bodies"] if body["mainStar"]))
    assert len(selected_names) == 1


def test_explicit_false_is_excluded_from_fallback():
    assert selected_body_ids(system(star(10, bodyId=0, distanceToArrival=0, mainStar=False), star(20))) == [20]


def test_all_explicitly_false_does_not_invent_a_main_star():
    record = system(star(10, bodyId=0, distanceToArrival=0, mainStar=False), star(20, isMainStar=False))
    assert selected_body_ids(record) == []
    assert all(row["is_main_star"] is False for row in body_rows(ClassifiedSpanshAdapter(**SOURCE).adapt_system(record)))


def test_multiple_explicit_main_stars_have_deterministic_selection_and_evidence():
    bodies = [star(30, mainStar=True, distanceToArrival=0), star(20, mainStar=True, distanceToArrival=1)]
    for ordering in permutations(bodies):
        normalized, unmapped = normalize_system(system(*ordering))
        assert [body["id64"] for body in normalized["bodies"] if body["mainStar"]] == [30]
        assert ("main_star_selection", "multiple explicit main stars") in unmapped


def test_multiple_explicit_main_star_distance_ties_use_body_identity():
    for ordering in permutations([star(30, mainStar=True), star(20, mainStar=True)]):
        assert selected_body_ids(system(*ordering)) == [20]


@pytest.mark.parametrize("value", ("true", "false", 0, 1, [], {}))
def test_main_star_flags_must_be_json_booleans(value):
    with pytest.raises(ValueError, match="consistent JSON booleans"):
        normalize_system(system(star(10, mainStar=value)))


def test_contradictory_main_star_aliases_fail_closed():
    with pytest.raises(ValueError, match="consistent JSON booleans"):
        normalize_system(system(star(10, mainStar=True, is_main_star=False)))


def test_consistent_aliases_and_null_flags_are_allowed():
    assert selected_body_ids(system(star(10, mainStar=True, MainStar=True, isMainStar=None))) == [10]
    assert selected_body_ids(system(star(10, mainStar=None))) == [10]


@pytest.mark.parametrize("body_type", ("Planet", "Barycentre", "Belt Cluster", "Mystery"))
def test_nonstar_explicit_main_star_fails_closed(body_type):
    record = system({"id64": 10, "name": "Test 1", "type": body_type, "mainStar": True})
    with pytest.raises(ValueError, match="only a classified star"):
        normalize_system(record)


def test_planets_and_barycentres_are_never_selected_by_arrival_or_body_id():
    record = system(
        {"id64": 1, "bodyId": 0, "name": "Test barycentre", "type": "Barycentre", "distanceToArrival": 0},
        {"id64": 2, "name": "Test planet", "subType": "Water world", "distanceToArrival": 0},
        star(30, distanceToArrival=100),
    )
    assert selected_body_ids(record) == [30]
    assert selected_body_ids(system(*record["bodies"][:2])) == []


def test_main_star_with_unknown_spectrum_is_not_replaced_by_classified_companion():
    record = system(
        {"id64": 10, "bodyId": 0, "name": "Unknown main", "type": "Star", "mainStar": True,
         "SpectralClass": "Uncatalogued", "subType": "Future star class"},
        star(20, distanceToArrival=0),
    )
    batch = ClassifiedSpanshAdapter(**SOURCE).adapt_system(record)
    rows = {row["body_pk"]: row for row in body_rows(batch)}
    assert rows[10]["is_main_star"] is True
    assert rows[10]["spectral_class"] is None
    assert rows[20]["is_main_star"] is False
    assert rows[20]["spectral_class"] == "G"
    assert ("spectral_class", "Uncatalogued") in batch.unmapped
    assert ("body_type", "Future star class") in batch.unmapped


def test_duplicate_body_identity_is_rejected_before_adaptation():
    with pytest.raises(ValueError, match="duplicate Spansh body identity"):
        normalize_system(system(star(10), star(10, name="Another name")))


def test_custom_public_code_ids_are_used_in_written_body_rows():
    vocabulary = {"star": 41, "planet": 42, "barycentre": 43, "belt_cluster": 44}
    record = system(
        star(10),
        {"id64": 20, "name": "Planet", "PlanetClass": "Rocky body"},
        {"id64": 30, "name": "Barycentre", "type": "Barycentre"},
        {"id64": 40, "name": "Belt", "type": "Belt Cluster"},
        {"id64": 50, "name": "Unknown", "type": "Mystery"},
    )
    batch = ClassifiedSpanshAdapter(body_type_ids=vocabulary, **SOURCE).adapt_system(record)
    assert [row["body_type_id"] for row in body_rows(batch)] == [41, 42, 43, 44, None]
    assert vocabulary == {"star": 41, "planet": 42, "barycentre": 43, "belt_cluster": 44}


def test_missing_required_public_code_fails_closed():
    with pytest.raises(ValueError, match="missing required public codes"):
        ClassifiedSpanshAdapter(body_type_ids={"star": 1, "planet": 2}, **SOURCE)


def test_normalization_and_adaptation_preserve_original_source_evidence():
    record = system(star(10, spectralClass="G2", parents=[{"Null": 0}], mainStar=None))
    original = deepcopy(record)
    normalized, _ = normalize_system(record)
    ClassifiedSpanshAdapter(**SOURCE).adapt_system(record)
    assert record == original
    assert normalized is not record
    assert normalized["bodies"][0] is not record["bodies"][0]
    assert normalized["bodies"][0]["spectralClass"] == "G"
    assert record["bodies"][0]["spectralClass"] == "G2"


def test_canonical_physical_fields_and_nonbody_rows_reuse_retained_adapter():
    record = system(
        star(10, spectralClass="G2", luminosity="V", solarRadius=2, solarMasses=1.2,
             absoluteMagnitude=4.1, surfaceTemperature=5800, distanceToArrival=0),
        {"id64": 20, "bodyId": 2, "name": "Test planet", "type": "Planet", "subType": "Rocky body",
         "radius": 6000, "earthMasses": 0.8, "gravity": 9.80665, "gravityUnit": "m/s2",
         "surfacePressure": 202650, "surfaceTemperature": 300, "semiMajorAxis": 2,
         "orbitalPeriod": 400, "rotationalPeriod": 1.1, "orbitalEccentricity": 0.1,
         "parents": [{"Star": 10}], "solidComposition": {"Ice": 10, "Rock": 90}},
    )
    old = SpanshAdapter(**SOURCE).adapt_system(record)
    new = ClassifiedSpanshAdapter(**SOURCE).adapt_system(record)
    expected = body_rows(old)
    actual = body_rows(new)
    for old_body, new_body in zip(expected, actual, strict=True):
        for column in COPY_COLUMNS["bodies"]:
            if column not in {"spectral_class", "is_main_star"}:
                assert new_body[column] == old_body[column], column
    assert actual[0]["radius_km"] == 2 * KM_PER_SOLAR_RADIUS
    assert actual[1]["radius_km"] == 6000
    assert actual[1]["surface_gravity_g"] == pytest.approx(1)
    assert actual[1]["surface_pressure_atm"] == 2
    assert actual[1]["semi_major_axis_km"] == 2 * KM_PER_AU
    assert actual[1]["direct_parent_body_pk"] == 10
    assert {key: rows for key, rows in new.rows.items() if key != "bodies"} == {
        key: rows for key, rows in old.rows.items() if key != "bodies"
    }
