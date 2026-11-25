"""Find across the whole band (edfinder_api.find_economy with scope "band",
POST /api/find/economy). Ratings rebuild, build step 5.

The galaxy is the one of tests/test_find_economy.py: the real fixture systems
with Sol inhabited, eight within 500 ly of it and five within 250. The ranked
lists are written by the nightly scoring's own code
(spansh_delta.economy_top). Each search is held against the same facts read
whole and sorted the slow way in Python: every economy in both orders, every
pair, and no economy at all.

Runs on PostgreSQL 18 with every migration applied; needs
RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

import json
import math
import os

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://find-band-test.invalid")

from fastapi import HTTPException  # noqa: E402

from edfinder_api import find_economy, find_numbers  # noqa: E402
from edfinder_api.cpe.body_facts import ECONOMIES, PAIRS  # noqa: E402
from edfinder_api.fake_systems import FAKE_SYSTEMS  # noqa: E402
from spansh_delta import economy_facts as facts  # noqa: E402
from spansh_delta import economy_top as top  # noqa: E402
from tests.test_economy_facts import before_033, filled  # noqa: E402
from tests.test_find_economy import (  # noqa: E402,F401
    AT_SOL,
    EVERY_ORDER,
    PI_PLANNER_SETTINGS,
    ask,
    near,
    pi_plan,
    post,
    production_sized,
    refusal,
    under_a_subplan,
)
from tests.test_find_reach import reach_database  # noqa: E402,F401
from tests.test_galaxy_scores import template  # noqa: E402,F401
from tests.test_galaxy_update import SOL  # noqa: E402
from tests.test_reach import NEAR_SOL, WITHIN_500_OF_SOL, pi  # noqa: E402,F401

EVERY_LIST = EVERY_ORDER + [((), "ground_slots")]
READERS = {"strongest": find_numbers.read_strongest, "purest": find_numbers.read_purest,
           "pair": find_numbers.read_pair}


def band(economies, order=None, **more) -> dict:
    body = {"scope": "band", "economies": list(economies), **more}
    if order not in (None, "pair", "ground_slots"):
        body["order"] = order
    return body


def expression_of(economies, order) -> str:
    return {
        "strongest": lambda: f"strongest[{ECONOMIES.index(economies[0]) + 1}]",
        "purest": lambda: f"purest[{ECONOMIES.index(economies[0]) + 1}]",
        "pair": lambda: f"pairs[{PAIRS.index(tuple(economies)) + 1}]",
        "ground_slots": lambda: "ground_slots",
    }[order]()


def the_slow_way(galaxy, economies, order, *, reach=250, depth=20_000, keep=lambda counts: True) -> list[tuple]:
    """What a search across the band must answer: the facts read whole and
    put in the list's order, its first ``depth`` systems, and of those the
    ones Find's rules let through. (id64, number, bodies_known, place)."""
    expression = expression_of(economies, order)
    listed = galaxy.all(
        f"SELECT system_id64, {expression}, bodies_known, landable_count FROM v3_state.economy_facts "
        f"WHERE {expression} > 0")
    listed.sort(key=lambda row: (-row[1], -row[2], row[0]))
    hidden = {row[0] for row in galaxy.all(
        "SELECT system_id64 FROM v3_state.system_state "
        "WHERE population > 0 OR stations_updated_at >= now() - interval '30 days'")}
    in_reach = dict(galaxy.all("SELECT system_id64, within_250 FROM v3_state.system_reach"))
    searchable = {row[0] for row in galaxy.all("SELECT system_id64 FROM v3_app.system_search")}
    found = []
    for place, (system_id64, number, bodies_known, landable) in enumerate(listed[:depth], 1):
        if (system_id64 in hidden or system_id64 not in searchable or system_id64 in FAKE_SYSTEMS
                or system_id64 not in in_reach or (reach == 250 and not in_reach[system_id64])
                or not keep(landable)):
            continue
        found.append((system_id64, number, bodies_known, place))
    return found


def answered(response: dict) -> list[tuple]:
    return [(row["id64"], row["order_number"], row["bodies_known"], row["place"])
            for row in response["results"]]


# -- a request is checked before the database is touched ---------------------------------


def test_a_search_across_the_band_names_its_list():
    for economies, order in EVERY_ORDER:
        search = find_economy.prepare(band(economies, order))
        assert (search.scope, search.centre, search.radius_ly) == ("band", None, None)
        assert search.list_no == find_numbers.list_no(search.column, search.index)
        assert find_numbers.list_expressions()[search.list_no - 1] == expression_of(economies, order)
    assert len({find_economy.prepare(band(economies, order)).list_no for economies, order in EVERY_ORDER}) == 35
    none = find_economy.prepare(band(()))
    assert (none.list_no, none.order, none.economies) == (36, "ground_slots", ())
    assert find_economy.prepare({"scope": "band", "economies": [], "order": "ground_slots"}).list_no == 36
    # A point is only where distances are given from.
    assert find_economy.prepare(band(("Refinery",), reference_coords=AT_SOL)).centre == (0.0, 0.0, 0.0)
    # Near a point is still what a request means that does not say.
    assert find_economy.prepare({"economies": ["Refinery"], "reference_coords": AT_SOL}).scope == "near"


@pytest.mark.parametrize("body, why", [
    (band(("Refinery",), radius_ly=100), "radius_ly is for a search near a point"),
    (band((), order="strongest"), "order is for one economy; with none"),
    ({"scope": "band", "economies": ["Refinery"], "order": "ground_slots"}, "order must be one of"),
    (band(("Refinery",), reference_coords={"x": 1, "y": 2}), "reference_coords must include x, y and z"),
    (band(("Refinery",), reference_coords={"x": 1e9, "y": 0, "z": 0}), "finite numbers within the galaxy"),
    (band(("Refinery", "Refinery")), "A pair is two different economies"),
    (band(("Mining",)), "Unknown economy"),
    ({"scope": "galaxy", "economies": ["Refinery"]}, "scope must be 'near' or 'band'"),
    ({"economies": [], "reference_coords": AT_SOL}, "Only a search across the band can be made with none"),
    ({"scope": "near", "economies": ["Refinery"]}, "reference_coords must include x, y and z"),
])
def test_a_request_that_cannot_be_answered_is_refused(body, why):
    with pytest.raises(HTTPException) as refused:
        find_economy.prepare(body)
    assert refused.value.status_code == 422 and why in refused.value.detail


def test_the_statements_read_one_list_from_its_first_place_and_carry_no_text_of_the_request():
    search = find_economy.prepare(band(("Extraction",), reach_ly=500, size=20, **{"from": 40},
                                       body_filters={"elw_count": {"min": 1, "max": 3}}))
    page, values = find_economy.band_page_query(search, 7, 20_000)
    assert values[:5] == [7, find_numbers.list_no("strongest", 2), 20_000, 1, 3] and values[-2:] == [20, 40]
    listed = page[page.index("FROM v3_state.economy_top t"):page.index(") c")]
    assert ("WHERE t.build_id = $1\n          AND t.list_no = $2\n          AND t.place <= $3\n"
            "          AND t.elw_count >= $4::bigint\n          AND t.elw_count <= $5::bigint\n"
            "        ORDER BY t.place\n        OFFSET 0") in listed
    rules = page[page.index("c.place,\n"):page.index("    FROM (\n        SELECT t.")]
    assert "elw_count" not in rules and ") AS kept" in rules
    assert rules.index("NOT IN (") < rules.index("system_state") < rules.index("system_reach") \
        < rules.index("v3_app.system_search")
    assert page.count("OFFSET 0") == 2 and page.rstrip().endswith("ORDER BY k.place\nLIMIT $6 OFFSET $7")
    assert "economy_facts" not in page
    count, count_values = find_economy.band_count_query(search, 7, 20_000)
    assert count_values == values[:-2] and "LIMIT 1001" in count and "ORDER BY" not in count
    assert "t.elw_count >= $4::bigint" in count
    assert "Extraction" not in page + count


# -- what a search answers ------------------------------------------------------------------


@pytest.mark.parametrize("reach", [250, 500])
@pytest.mark.parametrize("economies, order", EVERY_LIST,
                         ids=["-".join(economies) + "-" + order for economies, order in EVERY_LIST])
def test_a_search_is_its_list_with_finds_rules_applied(near, economies, order, reach):
    response = ask(near, band(economies, order, reach_ly=reach))
    expected = the_slow_way(near, economies, order, reach=reach)
    assert answered(response) == expected
    # Sol is inhabited, so it is in the list but not in the answer; the three
    # systems further out are in reach at 500 ly only.
    assert {row[0] for row in expected} == (WITHIN_500_OF_SOL if reach == 500 else NEAR_SOL) - {SOL}
    assert (response["total"], response["total_is_counted"], response["count"]) == (len(expected), True,
                                                                                   len(expected))
    assert (response["scope"], response["order"], response["economies"], response["radius_ly"]) == (
        "band", order, list(economies), None)
    assert response["reach_ly"] == reach


def test_the_answer_says_which_lists_it_read_and_how_deep_they_go(near):
    response = ask(near, band(("Refinery",)))
    build_id, written = near.one(
        "SELECT build_id, finished_at FROM v3_state.economy_top_build WHERE is_current")
    assert response["lists"] == {"version": facts.VERSION, "build_id": build_id,
                                 "written_at": written.isoformat(), "depth": 20_000, "places": 8}
    assert response["source"] == f"lists:{facts.VERSION}"
    assert response["facts"]["version"] == facts.VERSION and response["facts"]["updating"] is False
    # A search near a point has no list.
    close = ask(near, {"economies": ["Refinery"], "reference_coords": AT_SOL, "radius_ly": 250})
    assert (close["scope"], close["lists"], close["radius_ly"]) == ("near", None, 250.0)
    assert {row["place"] for row in close["results"]} == {None}


def test_each_row_is_the_systems_own_and_reads_its_number_back(near):
    for economies, order in ((("Refinery",), "strongest"), (("HighTech",), "purest"),
                             (("Extraction", "Refinery"), "pair"), ((), "ground_slots")):
        response = ask(near, band(economies, order, reach_ly=500))
        assert response["results"]
        for row in response["results"]:
            name, x_ly, y_ly, z_ly, landable = near.one(
                "SELECT name, x_ly, y_ly, z_ly, landable_count FROM v3_app.system_search WHERE system_id64 = %s",
                row["id64"])
            assert (row["name"], row["coords"], row["landable_count"]) == (
                name, {"x": x_ly, "y": y_ly, "z": z_ly}, landable)
            # No point was given to measure from.
            assert row["distance"] is None and row["id"] == str(row["id64"])
            bodies_known, ground_slots = near.one(
                "SELECT bodies_known, ground_slots FROM v3_state.economy_facts WHERE system_id64 = %s",
                row["id64"])
            assert (row["bodies_known"], row["ground_slots"]) == (bodies_known, ground_slots)
            if order == "ground_slots":
                assert row["best_body"] is None and row["order_number"] == ground_slots
            else:
                assert row["best_body"] == READERS[order](row["order_number"])


def test_with_a_point_each_row_says_how_far_from_it_the_system_is(near):
    response = ask(near, band(("Refinery",), reach_ly=500, reference_coords=AT_SOL))
    assert answered(response) == the_slow_way(near, ("Refinery",), "strongest", reach=500)
    for row in response["results"]:
        assert row["distance"] == round(math.dist(
            (row["coords"]["x"], row["coords"]["y"], row["coords"]["z"]), (0, 0, 0)), 2) > 0
    # The point changes no place in the list.
    assert [row["id64"] for row in response["results"]] == [
        row["id64"] for row in ask(near, band(("Refinery",), reach_ly=500,
                                              reference_coords={"x": 300.0, "y": -40.0, "z": 12.0}))["results"]]


def test_body_filters_keep_the_listed_systems_whose_counts_pass(near):
    counts = dict(near.all("SELECT system_id64, landable_count FROM v3_app.system_search "
                           "WHERE system_id64 = ANY(%s)", sorted(WITHIN_500_OF_SOL - {SOL})))
    levels = sorted(set(counts.values()))
    assert len(levels) > 2
    floor = levels[len(levels) // 2]
    response = ask(near, band(("Refinery",), reach_ly=500, body_filters={"landable_count": {"min": floor}}))
    kept = the_slow_way(near, ("Refinery",), "strongest", reach=500, keep=lambda landable: landable >= floor)
    assert answered(response) == kept and 0 < len(kept) < 7
    assert response["total"] == len(kept) and all(row["landable_count"] >= floor for row in response["results"])
    # The places are still the list's own: a filter leaves gaps, it does not renumber.
    assert [row["place"] for row in response["results"]] == [row[3] for row in kept]
    between = ask(near, band(("Refinery",), reach_ly=500,
                             body_filters={"landable_count": {"min": levels[1], "max": levels[-2]}}))
    assert answered(between) == the_slow_way(near, ("Refinery",), "strongest", reach=500,
                                             keep=lambda landable: levels[1] <= landable <= levels[-2])
    for outsized in (40_000, 10 ** 30):
        none = ask(near, band(("Refinery",), body_filters={"landable_count": {"min": outsized}}))
        assert (none["results"], none["total"], none["total_is_counted"]) == ([], 0, True)


def test_pages_follow_one_another_down_the_list(near):
    whole = the_slow_way(near, (), "ground_slots", reach=500)
    assert len(whole) == 7
    pages = [ask(near, band((), reach_ly=500, size=3, **{"from": start})) for start in (0, 3, 6)]
    assert [row for page in pages for row in answered(page)] == whole
    assert [page["count"] for page in pages] == [3, 3, 1]
    assert {page["total"] for page in pages} == {7}
    assert ask(near, band((), reach_ly=500, size=3, **{"from": 9}))["results"] == []


# -- the depth of a list --------------------------------------------------------------------------


def test_a_search_looks_within_the_lists_depth_and_no_further(pi, tmp_path):
    """The agreed limit: a list holds the best systems of the band to a depth,
    and a search across the band is a search of those. With three places a
    list, a system that is fourth in the band is not found, however well it
    passes the rules."""
    filled(pi, tmp_path)
    everything = the_slow_way(pi, ("Refinery",), "strongest", reach=500)
    assert top.rebuild(pi.connection, depth=3, percent=100, progress=False).failed is None
    response = ask(pi, band(("Refinery",), reach_ly=500))
    expected = the_slow_way(pi, ("Refinery",), "strongest", reach=500, depth=3)
    assert answered(response) == expected == [row for row in everything if row[3] <= 3]
    assert 0 < len(expected) < len(everything)
    assert (response["lists"]["depth"], response["lists"]["places"], response["total"]) == (3, 3, len(expected))
    # The set says how deep its lists may be read, whatever rows it holds.
    assert top.rebuild(pi.connection, progress=False).failed is None
    pi.connection.execute("UPDATE v3_state.economy_top_build SET depth = 2 WHERE is_current")
    response = ask(pi, band(("Refinery",), reach_ly=500))
    assert answered(response) == [row for row in everything if row[3] <= 2]
    assert (response["lists"]["depth"], response["lists"]["places"]) == (2, 2)


def test_a_search_reads_the_set_that_is_current_when_it_starts(pi, tmp_path):
    filled(pi, tmp_path)
    first = ask(pi, band(("Refinery",), reach_ly=500))
    report = top.rebuild(pi.connection, progress=False)
    second = ask(pi, band(("Refinery",), reach_ly=500))
    assert second["lists"]["build_id"] == report.build_id > first["lists"]["build_id"]
    assert second["results"] == first["results"]


# -- when the lists cannot be read ----------------------------------------------------------------------


def test_the_search_says_so_until_the_lists_have_been_made(pi, tmp_path):
    filled(pi, tmp_path)
    pi.connection.execute("DELETE FROM v3_state.economy_top_build")
    assert refusal(pi, band(("Refinery",))) == (
        503, "Find's lists across the band have not been made yet: the nightly scoring makes them once "
             "Find's facts have been worked out.")
    # A search near a point does not need them.
    assert ask(pi, {"economies": ["Refinery"], "reference_coords": AT_SOL, "radius_ly": 250})["count"] == 4
    # Lists written for another layout of the numbers cannot be read by this build.
    assert top.rebuild(pi.connection, progress=False).failed is None
    pi.connection.execute("UPDATE v3_state.economy_top_build SET version = 'a rulebook/facts-0'")
    assert refusal(pi, band(("Refinery",))) == (
        503, "Find's lists across the band were made for another build of ED-Finder; the next nightly "
             "run makes them again.")
    # Another rulebook's release with this layout can.
    pi.connection.execute("UPDATE v3_state.economy_top_build SET version = 'an older rulebook/facts-1'")
    assert ask(pi, band(("Refinery",)))["lists"]["version"] == "an older rulebook/facts-1"


def test_the_search_says_so_where_migration_033_has_not_been_applied(pi, tmp_path):
    before_033(pi)
    filled(pi, tmp_path)
    assert refusal(pi, band(("Refinery",))) == (
        503, "Find's lists across the band are not kept in this database yet "
             "(migration 033 has not been applied).")
    assert ask(pi, {"economies": ["Refinery"], "reference_coords": AT_SOL, "radius_ly": 250})["count"] == 4


def test_the_search_waits_for_the_facts_as_a_search_near_a_point_does(pi):
    assert refusal(pi, band(("Refinery",))) == (
        503, "Find's facts for each system have not been worked out yet: the first fill has not started.")


# -- how the database is asked to do it --------------------------------------------------------------------


@pytest.fixture(scope="module")
def production_lists(production_sized):  # noqa: F811  (the fixture imported above)
    """The plan tests' database with its ranked lists written: 36 lists of
    20,000 of the 40,000 made-up systems, as large as the Pi's will be."""
    connection = production_sized
    connection.execute(
        "INSERT INTO v3_state.economy_facts_fill (version, finished_at, systems) VALUES (%s, now(), 40000)",
        (facts.VERSION,))
    report = top.rebuild(connection, percent=100, progress=False)
    assert (report.failed, report.skipped, report.lists, report.places) == (None, None, 36, 720_000)
    return connection, report.build_id


@pytest.mark.db
@pytest.mark.parametrize("mode", ["force_generic_plan", "force_custom_plan"])
@pytest.mark.parametrize("economies, order", [(("Extraction",), "strongest"), (("HighTech",), "purest"),
                                              (("Extraction", "Refinery"), "pair"), ((), "ground_slots")])
@pytest.mark.parametrize("filters", [None, {"landable_count": {"min": 1}}, {"elw_count": {"min": 1}}],
                         ids=["plain", "common-filter", "rare-filter"])
def test_at_the_pis_sizes_a_list_is_read_by_its_key_and_the_rules_come_after(
        production_lists, economies, order, filters, mode):
    """A page is one list read in place order by the table's key, never the
    whole table, and never the facts; each rule is then looked up for one
    listed system at a time, so a full page ends the look-ups."""
    from tests.helpers.finder_seed import plan_nodes

    connection, build_id = production_lists
    search = find_economy.prepare(band(economies, order, body_filters=filters or {}))
    plan = pi_plan(connection, *find_economy.band_page_query(search, build_id, 20_000), mode)
    found = list(plan_nodes(plan))
    assert plan["Node Type"] == "Limit"
    listed = [node for node in found if node.get("Relation Name") == "economy_top"]
    assert len(listed) == 1 and not under_a_subplan(plan, listed[0])
    assert listed[0]["Node Type"] in ("Index Scan", "Bitmap Heap Scan"), listed[0]["Node Type"]
    keyed = next(node for node in plan_nodes(listed[0]) if "Index Cond" in node)
    assert keyed["Index Name"] == "economy_top_pkey"
    for column in ("build_id", "list_no", "place"):
        assert column in keyed["Index Cond"], keyed["Index Cond"]
    # The body filter is answered by the list's own row.
    for column in filters or {}:
        assert column in listed[0].get("Filter", ""), listed[0]
        assert not any(column in json.dumps(node.get(key, "")) for node in found
                       if node.get("Relation Name") == "system_search" for key in ("Filter", "Index Cond"))
    # Nothing but one list's rows is ever sorted, and the facts are not read.
    for sort in (node for node in found if node["Node Type"] in ("Sort", "Incremental Sort")):
        assert {node.get("Relation Name") for node in plan_nodes(sort)} - {None} == {"economy_top"}
    assert not any(node.get("Relation Name") == "economy_facts" for node in found)
    # No join: every rule is a look-up made for one system.
    joins = [node for node in found if "Join" in node["Node Type"] or node["Node Type"] == "Nested Loop"]
    assert all(under_a_subplan(plan, node) for node in joins), [node["Node Type"] for node in joins]
    for table in ("system_state", "system_reach", "system_search"):
        scans = [node for node in found if node.get("Relation Name") == table]
        assert scans and all(under_a_subplan(plan, node) for node in scans), table
        assert table == "system_state" or all(
            node["Node Type"] in ("Index Scan", "Index Only Scan") for node in scans), table
    filtered = next(node for node in found if node.get("Filter") == "k.kept")
    assert filtered["Node Type"] == "Subquery Scan" and listed[0] in list(plan_nodes(filtered))

    counted = pi_plan(connection, *find_economy.band_count_query(search, build_id, 20_000), mode)
    count_nodes = list(plan_nodes(counted))
    assert not any(node["Node Type"] == "Seq Scan" and node["Relation Name"] in {
        "economy_top", "system_search", "system_reach", "economy_facts"} for node in count_nodes)
    # The count needs no order: it reads the one list by either of the table's keys.
    (counted_list,) = [node for node in count_nodes if node.get("Relation Name") == "economy_top"]
    assert counted_list["Index Name"] in ("economy_top_pkey", "economy_top_build_id_list_no_system_id64_key")
    assert "build_id" in counted_list["Index Cond"] and "list_no" in counted_list["Index Cond"]
    assert not any(node["Node Type"] in ("Sort", "Incremental Sort") for node in count_nodes)


@pytest.mark.db
def test_a_page_asks_the_rules_about_the_places_it_reaches_and_no_further(production_lists):
    """Run for real on a list of 20,000: a page of ten asks the rules about
    the first places of the list, not about all of it; and with a filter one
    system in 400 passes, only about the ones that pass."""
    from tests.helpers.finder_seed import _prepared, plan_nodes

    connection, build_id = production_lists

    def run(body: dict) -> tuple[dict, list[dict]]:
        search = find_economy.prepare(body)
        statement, values = find_economy.band_page_query(search, build_id, 20_000)
        with connection.transaction(force_rollback=True):
            for name, value in PI_PLANNER_SETTINGS.items():
                connection.execute("SELECT set_config(%s, %s, true)", (name, value))
            rows = _prepared(connection, statement, values, "EXPLAIN (ANALYZE, TIMING OFF, FORMAT JSON) ")
        document = rows[0][0]
        plan = (json.loads(document) if isinstance(document, str) else document)[0]["Plan"]
        return plan, list(plan_nodes(plan))

    plan, found = run(band(("Extraction",), size=10, reach_ly=500))
    assert plan["Actual Rows"] == 10
    (listed,) = [node for node in found if node.get("Relation Name") == "economy_top"]
    looked_up = max(node["Actual Loops"] for node in found if node.get("Relation Name") == "system_search")
    assert 10 <= looked_up <= listed["Actual Rows"] * listed["Actual Loops"] < 2_000

    plan, found = run(band(("Extraction",), size=10, reach_ly=500, body_filters={"elw_count": {"min": 1}}))
    (listed,) = [node for node in found if node.get("Relation Name") == "economy_top"]
    passed = listed["Actual Rows"] * listed["Actual Loops"]
    assert 0 < passed <= 100 and listed["Rows Removed by Filter"] > 5_000
    looked_up = max(node["Actual Loops"] for node in found if node.get("Relation Name") == "system_search")
    assert plan["Actual Rows"] <= looked_up <= passed


# -- the route -----------------------------------------------------------------------------------------------


def test_the_route_answers_a_search_across_the_band(near, monkeypatch):
    # Both asked of the database before the route's database step is replaced.
    real = ask(near, band(("Refinery", "Extraction"), reach_ly=500))
    with_none = ask(near, band((), reference_coords=AT_SOL))
    status, body, asked = post(monkeypatch, {"scope": "band", "economies": ["Refinery", "Extraction"],
                                             "reach_ly": 500}, real)
    assert status == 200 and body == json.loads(json.dumps(real))
    assert asked[0][0] == {"scope": "band", "economies": ["Refinery", "Extraction"], "order": None,
                           "reference_coords": None, "radius_ly": None, "reach_ly": 500,
                           "body_filters": {}, "size": 50, "from": 0}
    assert (body["lists"]["depth"], body["results"][0]["place"], body["results"][0]["distance"]) == (
        20_000, real["results"][0]["place"], None)
    # No economy at all, and a point to measure from.
    status, body, asked = post(monkeypatch, {"scope": "band", "economies": [], "reference_coords": AT_SOL},
                               with_none)
    assert status == 200 and body == json.loads(json.dumps(with_none))
    assert (body["order"], body["count"], body["results"][0]["best_body"]) == ("ground_slots", 4, None)
    assert asked[0][0]["reference_coords"] == AT_SOL and body["results"][0]["distance"] > 0
    for bad in ({"scope": "band", "economies": ["Refinery"], "radius_ly": 100},
                {"scope": "band", "economies": [], "order": "strongest"},
                {"scope": "everywhere", "economies": ["Refinery"]},
                {"scope": "near", "economies": []},
                {"economies": ["Refinery"]}):
        status, _, asked = post(monkeypatch, bad, real)
        assert status == 422 and asked == [], bad
