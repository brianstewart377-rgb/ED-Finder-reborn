"""Find: the systems near a point, in order for one economy or a pair
(edfinder_api.find_economy, POST /api/find/economy). Ratings rebuild, build
step 5.

The galaxy is the twelve real fixture systems with Sol inhabited, as in
tests/test_economy_facts.py: eight of them are within 500 ly of Sol, five
within 250 ly. Their rows in v3_state.economy_facts are made by the nightly
scoring's own code. These tests check the search against the same rows read
and sorted the slow way in Python, for every economy, both orders and every
pair, and that Find's rules (claims, reach, body filters) and the pages and
counts hold.

Runs on PostgreSQL 18 with every migration applied; needs
RATINGS_V4_VALIDATION_DATABASE_URL, like the other database tests.
"""
from __future__ import annotations

import asyncio
import json
import math
import os

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://find-economy-test.invalid")

from fastapi import HTTPException  # noqa: E402

from edfinder_api import find_cells, find_economy, find_numbers  # noqa: E402
from edfinder_api.cpe.body_facts import (  # noqa: E402
    ECONOMIES,
    PAIRS,
    best_for_pair,
    pair_key,
    purest,
    purest_key,
    strongest,
    strongest_key,
)
from edfinder_api.fake_systems import FAKE_SYSTEMS  # noqa: E402
from spansh_delta import economy_facts as facts  # noqa: E402
from tests.test_economy_facts import body_facts_of, filled  # noqa: E402
from tests.test_find_reach import _api_pool, reach_database  # noqa: E402,F401
from tests.test_galaxy_scores import template  # noqa: E402,F401
from tests.test_galaxy_update import SOL  # noqa: E402
from tests.test_reach import FURTHER, NEAR_SOL, copy_of, pi  # noqa: E402,F401

AT_SOL = {"x": 0.0, "y": 0.0, "z": 0.0}
# One of the three systems that are within 500 ly of Sol but not within 250.
OUTER = 1457973727914
EVERY_ORDER = (
    [((economy,), order) for economy in ECONOMIES for order in ("strongest", "purest")]
    + [(pair, "pair") for pair in PAIRS]
)


@pytest.fixture(scope="module")
def near(template, tmp_path_factory):  # noqa: F811  (the fixture imported above)
    """The filled galaxy, for the tests that only read it."""
    with copy_of(template) as galaxy:
        filled(galaxy, tmp_path_factory.mktemp("near"))
        yield galaxy


def ask(galaxy, body: dict, **options) -> dict:
    async def scenario():
        pool = await _api_pool(galaxy.connection)
        try:
            return await find_economy.search_near(body, pool, **options)
        finally:
            await pool.close()
    return asyncio.run(scenario())


def request(economies, order=None, *, centre=AT_SOL, radius=250.0, **more) -> dict:
    body = {"economies": list(economies), "reference_coords": centre, "radius_ly": radius, **more}
    if order not in (None, "pair"):
        body["order"] = order
    return body


def the_slow_way(galaxy, economies, order, *, centre=AT_SOL, radius=250.0, reach=250) -> list[tuple]:
    """What the search must answer, from the tables read whole and sorted in
    Python: (id64, number, bodies_known, distance) in Find's order."""
    column, index = {
        "strongest": lambda: ("strongest", ECONOMIES.index(economies[0])),
        "purest": lambda: ("purest", ECONOMIES.index(economies[0])),
        "pair": lambda: ("pairs", PAIRS.index(tuple(economies))),
    }[order]()
    hidden = {row[0] for row in galaxy.all(
        "SELECT system_id64 FROM v3_state.system_state "
        "WHERE population > 0 OR stations_updated_at >= now() - interval '30 days'")}
    in_reach = dict(galaxy.all("SELECT system_id64, within_250 FROM v3_state.system_reach"))
    searchable = {row[0] for row in galaxy.all("SELECT system_id64 FROM v3_app.system_search")}
    found = []
    for system_id64, numbers, bodies_known, x_ly, y_ly, z_ly in galaxy.all(
            f"SELECT system_id64, {column}, bodies_known, x_ly, y_ly, z_ly FROM v3_state.economy_facts"):
        distance = math.dist((x_ly, y_ly, z_ly), (centre["x"], centre["y"], centre["z"]))
        number = numbers[index] if numbers else 0
        if (distance > radius or number <= 0 or system_id64 in hidden or system_id64 not in searchable
                or system_id64 in FAKE_SYSTEMS or system_id64 not in in_reach
                or (reach == 250 and not in_reach[system_id64])):
            continue
        found.append((system_id64, number, bodies_known, round(distance, 2)))
    return sorted(found, key=lambda row: (-row[1], -row[2], row[3], row[0]))


def answered(response: dict) -> list[tuple]:
    return [(row["id64"], row["order_number"], row["bodies_known"], row["distance"])
            for row in response["results"]]


# -- a request is checked before the database is touched ---------------------------------


def test_one_economy_and_a_pair_name_their_column_of_numbers():
    for position, economy in enumerate(ECONOMIES, start=1):
        for order in ("strongest", "purest"):
            search = find_economy.prepare(request((economy,), order))
            assert (search.column, search.index, search.order) == (order, position, order)
        assert find_economy.prepare(request((economy,))).order == "strongest"
    for position, pair in enumerate(PAIRS, start=1):
        for named in (pair, pair[::-1]):
            search = find_economy.prepare(request(named))
            assert (search.column, search.index, search.order, search.economies) == ("pairs", position, "pair", pair)


@pytest.mark.parametrize("change, said", [
    ({"economies": []}, "one economy, or two"),
    ({"economies": ["Refinery", "Industrial", "Tourism"]}, "one economy, or two"),
    ({"economies": ["Mining"]}, "Unknown economy 'Mining'"),
    ({"economies": ["Refinery", "Refinery"]}, "two different economies"),
    ({"economies": ["Refinery", "Industrial"], "order": "purest"}, "a pair has one order"),
    ({"order": "nearest"}, "order must be one of"),
    ({"order": "pair"}, "order must be one of"),
    ({"reference_coords": {"x": 0.0, "y": 0.0}}, "must include x, y and z"),
    ({"reference_coords": None}, "must include x, y and z"),
    ({"reference_coords": {"x": float("nan"), "y": 0.0, "z": 0.0}}, "finite numbers within the galaxy"),
    ({"reference_coords": {"x": 200000.0, "y": 0.0, "z": 0.0}}, "finite numbers within the galaxy"),
    ({"radius_ly": 0}, "above 0 and at most 250"),
    ({"radius_ly": 250.5}, "above 0 and at most 250"),
    ({"radius_ly": float("inf")}, "above 0 and at most 250"),
    ({"reach_ly": 300}, "reach_ly must be one of"),
    ({"body_filters": {"moons": {"min": 1}}}, "Unsupported body filter 'moons'"),
    ({"size": 0}, "size must be from 1 to"),
    ({"size": 501}, "size must be from 1 to"),
    ({"size": 500, "from": 501}, "from + size at most 1000"),
    ({"from": -1}, "from must be 0 or more"),
    ({"size": 2.5}, "size must be a whole number"),
])
def test_a_request_that_cannot_be_answered_is_refused(change, said):
    with pytest.raises(HTTPException) as refused:
        find_economy.prepare({**request(("Refinery",)), **change})
    assert refused.value.status_code == 422 and said in refused.value.detail


RULE_TABLES = ("v3_state.system_state", "v3_state.system_reach", "v3_app.system_search")


def test_the_rules_are_applied_to_the_sorted_systems_not_inside_the_sort():
    search = find_economy.prepare(request(("Extraction",), body_filters={"elw_count": {"min": 1, "max": 3}}))
    page, values = find_economy.page_query(search)
    # Three levels, fenced from each other.
    assert page.count("OFFSET 0\n") == 2
    sorted_systems = page[page.index("SELECT f.system_id64"):page.index(") c\n")]
    rules = page[page.index("SELECT c.system_id64"):page.index("FROM (\n        SELECT f.")]
    assert "f.strongest[2] > 0" in sorted_systems and "ORDER BY number DESC, " in sorted_systems
    for table in RULE_TABLES:
        assert table not in sorted_systems and table in rules, table
    assert "s.elw_count >= " in rules and "s.elw_count <= " in rules and ") AS kept" in rules
    # The cheapest rule first: no look-up, then the small table, then the big ones.
    assert rules.index("NOT IN (") < rules.index("system_state") < rules.index("system_reach") \
        < rules.index("system_search")
    # Each level names the order, so the systems are sorted once.
    in_order = "number DESC, {0}bodies_known DESC, {0}distance ASC, {0}system_id64 ASC"
    assert page.count("ORDER BY") == 3
    assert ") c\n    ORDER BY c." + in_order.format("c.") + "\n    OFFSET 0\n) k\n" in page
    assert page.endswith("WHERE k.kept\nORDER BY k." + in_order.format("k.") + "\nLIMIT $8 OFFSET $9")
    assert values[-2:] == [50, 0] and values[-4:-2] == [1, 3]
    count, count_values = find_economy.count_query(search)
    assert "LIMIT 1001" in count and "ORDER BY" not in count
    assert count_values == values[:-2]
    # No text of the request is in the SQL: only places for its values.
    assert "Extraction" not in page + count


# -- the answers, against the tables sorted the slow way -------------------------------------


@pytest.mark.parametrize("economies, order", EVERY_ORDER)
def test_every_order_near_sol_is_the_tables_sorted_the_slow_way(near, economies, order):
    response = ask(near, request(economies, order))
    expected = the_slow_way(near, economies, order)
    assert answered(response) == expected
    assert (response["count"], response["total"]) == (len(expected), len(expected))
    assert (response["total_is_capped"], response["total_is_counted"]) == (False, True)
    assert (response["economies"], response["order"]) == (list(economies), order)
    # Sol is inhabited, and the three further systems are beyond the radius.
    assert not {row["id64"] for row in response["results"]} & ({SOL} | FURTHER)


def test_the_systems_near_sol_are_the_four_that_can_be_claimed(near):
    response = ask(near, request(("Refinery",)))
    assert {row["id64"] for row in response["results"]} == NEAR_SOL - {SOL}
    assert response["facts"]["version"] == find_numbers.VERSION
    assert (response["facts"]["updating"], response["facts"]["in_order_on_disk"]) == (False, True)
    assert response["source"] == "facts:" + find_numbers.VERSION
    assert (response["radius_ly"], response["reach_ly"]) == (250.0, 250)
    for row in response["results"]:
        assert row["id"] == str(row["id64"]) and row["claim_status"] == "free"
        name, bodies = near.one("SELECT name, body_count FROM v3_app.system_search WHERE system_id64 = %s",
                                row["id64"])
        assert (row["name"], row["body_count"]) == (name, bodies)
        assert row["coords"] == dict(zip("xyz", near.one(
            "SELECT x_ly, y_ly, z_ly FROM {s}.systems WHERE id64 = %s", row["id64"]), strict=True))
    json.dumps(response)


@pytest.mark.parametrize("economies, order", EVERY_ORDER)
def test_each_result_names_the_steps_its_best_body_was_placed_by(near, economies, order):
    """The number read back is the planner's own order key for the system's
    best body."""
    for row in ask(near, request(economies, order))["results"]:
        bodies = body_facts_of(near, row["id64"])
        read = row["best_body"]
        if order == "strongest":
            best = strongest(bodies, economies[0])
            key = strongest_key(best, economies[0])
            assert (read["ground_part"], read["each_facility_adds"], -read["third_economies"],
                    read["port_starts_with"], (read["ground_slots"], read["asteroid_base_room"])) == key
        elif order == "purest":
            best = purest(bodies, economies[0])
            key = purest_key(best, economies[0])
            assert (-read["third_economies"], read["port_starts_with_it"], read["ground_part"],
                    read["each_facility_adds"], (read["ground_slots"], read["asteroid_base_room"])) == key
        else:
            best = best_for_pair(bodies, *economies)
            key = pair_key(best, *economies)
            assert (-read["third_economies"], (read["weaker_ground_part"], read["stronger_ground_part"]),
                    read["weaker_each_facility_adds"], read["stronger_each_facility_adds"],
                    read["port_starts_with_both"], (read["ground_slots"], read["asteroid_base_room"])) == key
        assert row["bodies_known"] > 0


def test_the_radius_is_kept_to_the_light_year(near):
    distances = sorted(row[3] for row in the_slow_way(near, ("Refinery",), "strongest"))
    assert distances == [82.53, 83.57, 123.06, 139.45]
    for radius, shown in ((82.52, 0), (82.54, 1), (83.58, 2), (123.07, 3), (139.46, 4), (250.0, 4)):
        response = ask(near, request(("Refinery",), radius=radius))
        assert response["count"] == shown == len(the_slow_way(near, ("Refinery",), "strongest", radius=radius))
        assert all(row["distance"] <= radius for row in response["results"])
    # Measured from the point given, wherever it is.
    centre = dict(zip("xyz", near.one("SELECT x_ly, y_ly, z_ly FROM {s}.systems WHERE id64 = %s", OUTER),
                      strict=True))
    response = ask(near, request(("Refinery",), centre=centre, radius=250.0, reach_ly=500))
    assert answered(response) == the_slow_way(near, ("Refinery",), "strongest", centre=centre, reach=500)
    assert response["results"] and min(row["distance"] for row in response["results"]) == 0.0


def test_the_pages_of_a_search_are_its_whole_answer_in_order(near):
    whole = the_slow_way(near, ("Industrial",), "strongest")
    assert len(whole) == 4
    pages = []
    for start in (0, 1, 2, 3):
        response = ask(near, request(("Industrial",), size=1, **{"from": start}), count_budget_ms=15000)
        assert (response["count"], response["total"], response["total_is_counted"]) == (1, 4, True)
        pages += answered(response)
    assert pages == whole
    two = ask(near, request(("Industrial",), size=3, **{"from": 3}))
    assert answered(two) == whole[3:] and two["total"] == 4
    past = ask(near, request(("Industrial",), size=3, **{"from": 4}))
    assert (past["results"], past["total"]) == ([], 4)


def test_within_250_ly_of_inhabited_space_or_the_whole_band(near):
    centre = dict(zip("xyz", near.one("SELECT x_ly, y_ly, z_ly FROM {s}.systems WHERE id64 = %s", OUTER),
                      strict=True))
    whole_band = ask(near, request(("Refinery",), centre=centre, reach_ly=500))
    close_in = ask(near, request(("Refinery",), centre=centre, reach_ly=250))
    assert OUTER in {row["id64"] for row in whole_band["results"]}
    assert answered(close_in) == the_slow_way(near, ("Refinery",), "strongest", centre=centre, reach=250)
    assert not {row["id64"] for row in close_in["results"]} & FURTHER
    assert {row["id64"] for row in whole_band["results"]} & FURTHER


def test_body_filters_keep_the_systems_whose_search_row_passes(near):
    counts = dict(near.all("SELECT system_id64, landable_count FROM v3_app.system_search "
                           "WHERE system_id64 = ANY(%s)", sorted(NEAR_SOL - {SOL})))
    levels = sorted(set(counts.values()))
    assert len(levels) > 1
    floor = levels[-1]
    response = ask(near, request(("Refinery",), body_filters={"landable_count": {"min": floor}}))
    kept = [row for row in the_slow_way(near, ("Refinery",), "strongest") if counts[row[0]] >= floor]
    assert answered(response) == kept and 0 < len(kept) < 4
    assert response["total"] == len(kept)
    assert all(row["landable_count"] >= floor for row in response["results"])
    none = ask(near, request(("Refinery",), body_filters={"landable_count": {"max": levels[0] - 1}}))
    assert (none["results"], none["total"], none["total_is_counted"]) == ([], 0, True)


def test_systems_that_are_level_are_listed_nearer_first(pi, tmp_path):
    """Decision 10: with the same number and as many known bodies, two
    systems are level, and the nearer one is listed first. The galaxy's own
    systems near Sol are never level in both, so one real system is given
    another's number and count of bodies here."""
    filled(pi, tmp_path)
    before = answered(ask(pi, request(("Refinery",))))
    # A system listed above one that is nearer to Sol.
    above, nearer = next((first, second) for position, first in enumerate(before)
                         for second in before[position + 1:] if second[3] < first[3])
    pi.connection.execute(
        "UPDATE v3_state.economy_facts SET strongest = other.strongest, bodies_known = other.bodies_known "
        "FROM v3_state.economy_facts other WHERE other.system_id64 = %s AND economy_facts.system_id64 = %s",
        (above[0], nearer[0]))
    after = answered(ask(pi, request(("Refinery",))))
    listed = [row[0] for row in after]
    assert listed.index(nearer[0]) + 1 == listed.index(above[0])
    level = [row for row in after if row[0] in (above[0], nearer[0])]
    assert level[0][1:3] == level[1][1:3] and level[0][3] < level[1][3]
    assert after == the_slow_way(pi, ("Refinery",), "strongest")


# -- claims --------------------------------------------------------------------------------


def test_a_system_being_claimed_is_left_out_and_an_old_claim_is_noted(pi, tmp_path):
    filled(pi, tmp_path)
    before = answered(ask(pi, request(("Refinery",))))
    being_claimed, old_claim = before[0][0], before[1][0]
    for system_id64, seen in ((being_claimed, "3 days"), (old_claim, "90 days")):
        pi.connection.execute(
            "INSERT INTO v3_state.system_state (system_id64, name, x_ly, y_ly, z_ly, population, "
            "station_count, system_updated_at, stations_updated_at, content_hash, source) "
            "VALUES (%s, 'Claim', 0, 0, 0, 0, 1, now(), now() - %s::interval, 'test', 'test')",
            (system_id64, seen))
    response = ask(pi, request(("Refinery",)))
    assert answered(response) == [row for row in before if row[0] != being_claimed]
    assert answered(response) == the_slow_way(pi, ("Refinery",), "strongest")
    assert response["total"] == len(before) - 1
    notes = {row["id64"]: (row["claim_status"], row["claim_station_seen_at"]) for row in response["results"]}
    assert notes[old_claim][0] == "claim_ended_unknown" and notes[old_claim][1] is not None
    assert all(status == "free" for system_id64, (status, _) in notes.items() if system_id64 != old_claim)


# -- whether the facts can be read ------------------------------------------------------------


def refusal(galaxy, body: dict) -> tuple[int, str]:
    with pytest.raises(HTTPException) as refused:
        ask(galaxy, body)
    return refused.value.status_code, refused.value.detail


def test_the_search_says_so_until_a_fill_has_finished(pi, tmp_path):
    body = request(("Refinery",))
    assert refusal(pi, body) == (503, "Find's facts for each system have not been worked out yet: "
                                      "the first fill has not started.")
    filled(pi, tmp_path)
    pi.connection.execute("UPDATE v3_state.economy_facts_fill SET finished_at = NULL, systems = 3")
    assert refusal(pi, body) == (503, "Find's facts for each system are still being worked out: "
                                      "3 of 8 systems done so far.")
    pi.connection.execute("UPDATE v3_state.economy_facts_fill SET finished_at = now()")
    assert ask(pi, body)["facts"]["updating"] is False

    # A new rulebook: its fill is under way, and the old numbers are still read.
    pi.connection.execute("INSERT INTO v3_state.economy_facts_fill (version, systems) "
                          "VALUES ('a later rulebook/facts-1', 5)")
    state = ask(pi, body)["facts"]
    assert (state["version"], state["updating"], state["updating_to"], state["updating_systems_done"]) \
        == (find_numbers.VERSION, True, "a later rulebook/facts-1", 5)
    pi.connection.execute("UPDATE v3_state.economy_facts_fill SET finished_at = now() WHERE finished_at IS NULL")
    state = ask(pi, body)["facts"]
    # This build still runs the older rulebook: the next nightly run refills.
    assert (state["version"], state["updating"], state["updating_to"]) \
        == ("a later rulebook/facts-1", True, find_numbers.VERSION)

    # Numbers of another layout cannot be read at all.
    pi.connection.execute("UPDATE v3_state.economy_facts_fill SET version = 'any rulebook/facts-0'")
    assert refusal(pi, body) == (503, "Find's facts for each system were worked out for another build "
                                      "of ED-Finder; the next nightly run starts them again.")
    pi.connection.execute("DROP TABLE v3_state.economy_facts, v3_state.economy_facts_fill")
    assert refusal(pi, body) == (503, "Find's facts for each system are not kept in this database yet.")


def test_a_count_that_runs_out_of_time_does_not_take_the_page_with_it(near, monkeypatch):
    async def out_of_time(connection, count_sql, count_params, budget_ms):
        return None

    monkeypatch.setattr(find_economy, "count_within", out_of_time)
    response = ask(near, request(("Refinery",), size=2))
    assert (response["count"], response["total"], response["total_is_counted"]) == (2, 2, False)
    assert answered(response) == the_slow_way(near, ("Refinery",), "strongest")[:2]


# -- how it is read ---------------------------------------------------------------------------

# As on the Pi on 24 November 2026: systems within 500 ly of inhabited space.
PRODUCTION_FACTS_ROWS = 40_956_071
# The Pi's own settings that shape a plan (deploy/pi/postgres.yml).
PI_PLANNER_SETTINGS = {"effective_cache_size": "10GB", "work_mem": "32MB", "random_page_cost": "1.1"}
BIG_TABLES = {"economy_facts", "system_search", "system_reach"}


@pytest.fixture(scope="module")
def production_sized(reach_database):
    """The plan tests' database (tests/test_find_reach.py: 40,000 made-up
    systems, only ever used for plans), with a facts row for each system and
    every table's statistics set to the Pi's sizes."""
    from tests.helpers import finder_seed

    connection = reach_database
    connection.execute(
        """
        INSERT INTO v3_state.economy_facts (system_id64, bodies_known, ground_slots, strongest, purest,
                                           pairs, worked_out_at, x_ly, y_ly, z_ly)
        SELECT s.system_id64, 1 + mod(s.system_id64, 40)::int, mod(s.system_id64, 30)::int,
               (SELECT array_agg(268435456 + mod(s.system_id64 * n, 1000)) FROM generate_series(1, 7) n),
               (SELECT array_agg(268435456 + mod(s.system_id64 * n, 1000)) FROM generate_series(1, 7) n),
               (SELECT array_agg(8796093022208 + mod(s.system_id64 * n, 1000)) FROM generate_series(1, 21) n),
               now(), s.x_ly, s.y_ly, s.z_ly
          FROM v3_app.system_search s
        ON CONFLICT (system_id64) DO NOTHING
        """)
    connection.execute("CLUSTER v3_state.economy_facts USING economy_facts_cell_idx")
    connection.execute("ANALYZE v3_state.economy_facts")
    finder_seed.import_production_statistics(connection)
    for table in ("economy_facts", "system_reach"):
        rows = connection.execute(
            """
            SELECT c.relname, c.reltuples, pg_relation_size(c.oid) / current_setting('block_size')::int
              FROM pg_class c
             WHERE c.oid = %s::regclass
                OR c.oid IN (SELECT indexrelid FROM pg_index WHERE indrelid = %s::regclass)
            """, (f"v3_state.{table}", f"v3_state.{table}")).fetchall()
        table_tuples = next(tuples for name, tuples, _ in rows if name == table)
        assert table_tuples > 0
        for name, tuples, pages in rows:
            connection.execute(
                "SELECT pg_restore_relation_stats('schemaname', 'v3_state', 'relname', %s::text, "
                "'relpages', %s::integer, 'reltuples', %s::real, 'relallvisible', %s::integer)",
                (name, int(pages), float(round(PRODUCTION_FACTS_ROWS * float(tuples) / float(table_tuples))),
                 int(pages) if name == table else 0))
    return connection


def crowded_cells(connection, crowded: bool) -> None:
    """How many systems PostgreSQL expects in a cell. Crowded: about 400, so
    it expects a million systems in a 250 ly sphere, which is more than any
    sphere of the real galaxy is known to hold. Not crowded: one or two, as
    in the made-up galaxy."""
    connection.execute(
        "SELECT pg_restore_attribute_stats('schemaname', 'v3_state', 'relname', 'economy_facts', "
        "'attname', 'cell', 'inherited', false, 'n_distinct', %s::real)",
        (100_000.0 if crowded else -0.6,))


def pi_plan(connection, statement: str, values: list, mode: str) -> dict:
    """The plan PostgreSQL makes for one statement with the Pi's settings:
    ``force_generic_plan`` knows none of the values, ``force_custom_plan``
    knows them all. The API's prepared statements get one or the other."""
    from tests.helpers import finder_seed

    with connection.transaction(force_rollback=True):
        for name, value in PI_PLANNER_SETTINGS.items():
            connection.execute("SELECT set_config(%s, %s, true)", (name, value))
        connection.execute("SELECT set_config('plan_cache_mode', %s, true)", (mode,))
        rows = finder_seed._prepared(connection, statement, values, "EXPLAIN (FORMAT JSON) ")
    found = rows[0][0]
    return (json.loads(found) if isinstance(found, str) else found)[0]["Plan"]


def tables_under(node: dict) -> set[str]:
    from tests.helpers.finder_seed import plan_nodes

    return {found["Relation Name"] for found in plan_nodes(node) if "Relation Name" in found}


@pytest.mark.db
@pytest.mark.parametrize("crowded", [False, True], ids=["sparse", "crowded"])
@pytest.mark.parametrize("mode", ["force_generic_plan", "force_custom_plan"])
@pytest.mark.parametrize("economies, order", [(("Extraction",), "strongest"), (("HighTech",), "purest"),
                                              (("Extraction", "Refinery"), "pair")])
@pytest.mark.parametrize("filters", [None, {"landable_count": {"min": 1}}, {"elw_count": {"min": 2}}],
                         ids=["plain", "common-filter", "rare-filter"])
def test_at_the_pis_sizes_the_systems_are_sorted_once_and_the_rules_come_after(
        production_sized, economies, order, filters, mode, crowded):
    """Whatever PostgreSQL expects of the sphere and of a filter, a page is
    read the same way: the sphere's systems come from the facts table by its
    cell index and are sorted once; each rule is then looked up for one
    sorted system at a time, so a full page ends the look-ups, and nothing
    sorts the systems again."""
    from tests.helpers.finder_seed import plan_nodes

    crowded_cells(production_sized, crowded)
    search = find_economy.prepare(request(economies, order, body_filters=filters or {}))
    plan = pi_plan(production_sized, *find_economy.page_query(search), mode)
    found = list(plan_nodes(plan))
    assert plan["Node Type"] == "Limit"
    sorts = [node for node in found if node["Node Type"] in ("Sort", "Incremental Sort")]
    assert len(sorts) == 1
    # Only the facts table is read to sort, and by its cell index.
    assert tables_under(sorts[0]) == {"economy_facts"}
    assert {node.get("Index Name") for node in plan_nodes(sorts[0])} - {None} == {"economy_facts_cell_idx"}
    assert not any(node["Node Type"] == "Seq Scan" for node in plan_nodes(sorts[0]))
    # No join anywhere: every rule is a look-up made for one system
    # (a SubPlan), above the sort.
    joins = [node for node in found if "Join" in node["Node Type"] or node["Node Type"] == "Nested Loop"]
    assert all(node.get("Subplan Name", "").startswith("SubPlan") or under_a_subplan(plan, node)
               for node in joins), [node["Node Type"] for node in joins]
    for table in ("system_state", "system_reach", "system_search"):
        scans = [node for node in found if node.get("Relation Name") == table]
        assert scans and all(under_a_subplan(plan, node) for node in scans), table
        # The claim rule's table is small (160,498 systems): PostgreSQL may
        # read it whole, once, and keep it in memory. The others are by key.
        assert table == "system_state" or all(
            node["Node Type"] in ("Index Scan", "Index Only Scan") for node in scans), table
    filtered = next(node for node in found if node.get("Filter") == "k.kept")
    assert filtered["Node Type"] == "Subquery Scan" and sorts[0] in list(plan_nodes(filtered))

    counted = pi_plan(production_sized, *find_economy.count_query(search), mode)
    count_nodes = list(plan_nodes(counted))
    assert not any(node["Node Type"] in ("Sort", "Incremental Sort") for node in count_nodes)
    assert not any(node["Node Type"] == "Seq Scan" and node["Relation Name"] in BIG_TABLES
                   for node in count_nodes)
    assert any(node.get("Index Name") == "economy_facts_cell_idx" for node in count_nodes)


def under_a_subplan(plan: dict, wanted: dict) -> bool:
    """Whether ``wanted`` is inside a SubPlan: a look-up made for one row."""
    def walk(node: dict, inside: bool) -> bool | None:
        inside = inside or node.get("Parent Relationship") == "SubPlan"
        if node is wanted:
            return inside
        for child in node.get("Plans") or []:
            answer = walk(child, inside)
            if answer is not None:
                return answer
        return None

    return bool(walk(plan, False))


@pytest.mark.db
def test_a_page_looks_up_the_rules_for_the_systems_it_reaches_and_no_further(production_sized):
    """Run for real on the 40,000 made-up systems: a page of ten out of a
    sphere of hundreds asks the rules about a few systems, not all of them."""
    from tests.helpers.finder_seed import _prepared, plan_nodes

    search = find_economy.prepare(request(("Extraction",), size=10))
    statement, values = find_economy.page_query(search)
    reads = {}
    for mode in ("force_generic_plan", "force_custom_plan"):
        with production_sized.transaction(force_rollback=True):
            production_sized.execute("SELECT set_config('plan_cache_mode', %s, true)", (mode,))
            rows = _prepared(production_sized, statement, values, "EXPLAIN (ANALYZE, TIMING OFF, FORMAT JSON) ")
        document = rows[0][0]
        plan = (json.loads(document) if isinstance(document, str) else document)[0]["Plan"]
        found = list(plan_nodes(plan))
        in_sphere = sum(node["Actual Rows"] * node["Actual Loops"] for node in found
                        if node.get("Relation Name") == "economy_facts")
        looked_up = max(node["Actual Loops"] for node in found if node.get("Relation Name") == "system_search")
        assert plan["Actual Rows"] == 10
        reads[mode] = (in_sphere, looked_up)
        assert in_sphere > 500 and 10 <= looked_up <= 40, reads
    assert reads["force_generic_plan"] == reads["force_custom_plan"]


def test_the_page_is_read_without_building_a_program_for_it(near, monkeypatch):
    """PostgreSQL would build one ("jit") for the work it expects, and the
    page stops long before that. Off for the search only."""
    seen = []
    real = find_economy.facts_state

    async def spy(connection):
        seen.append(await connection.fetchval("SHOW jit"))
        return await real(connection)

    monkeypatch.setattr(find_economy, "facts_state", spy)

    async def scenario():
        pool = await _api_pool(near.connection)
        try:
            async with pool.acquire() as connection:
                before = await connection.fetchval("SHOW jit")
            answer = await find_economy.search_near(request(("Refinery",)), pool)
            async with pool.acquire() as connection:
                return before, answer, await connection.fetchval("SHOW jit")
        finally:
            await pool.close()

    before, answer, afterwards = asyncio.run(scenario())
    assert (before, seen, afterwards) == ("on", ["off"], "on") and answer["count"] == 4


def test_the_cells_asked_for_hold_every_system_in_the_sphere(near):
    stored = near.all("SELECT cell, x_ly, y_ly, z_ly FROM v3_state.economy_facts")
    for radius in (1.0, 82.53, 100.0, 250.0):
        cells = set(find_cells.cells_within(0.0, 0.0, 0.0, radius))
        for cell, x_ly, y_ly, z_ly in stored:
            if math.dist((x_ly, y_ly, z_ly), (0.0, 0.0, 0.0)) <= radius:
                assert cell in cells
    assert find_cells.cells_within(0.0, 0.0, 0.0, 0.0) == [find_cells.cell_of(0.0, 0.0, 0.0)]
    # Away from a corner, a small sphere is in one cell; at Sol, a corner, it touches eight.
    assert len(find_cells.cells_within(16.0, 16.0, 16.0, 15.0)) == 1
    assert len(find_cells.cells_within(0.0, 0.0, 0.0, 1.0)) == 8
    with pytest.raises(ValueError, match="a radius must be 0 or more"):
        find_cells.cells_within(0.0, 0.0, 0.0, -1.0)


# -- the numbers read back ----------------------------------------------------------------------


def test_a_number_reads_back_as_the_steps_it_was_made_from():
    for steps, key in (
        (find_numbers.STRONGEST_STEPS, (11.4, 2.0, -1, 1.4, (6, True))),
        (find_numbers.PUREST_STEPS, (-2, True, 0.4, 0.4, (0, False))),
        (find_numbers.PAIR_STEPS, (0, (3.4, 3.8), 0.4, 1.2, 2.6, (7, True))),
    ):
        flat = tuple(find_numbers._flat(key))
        assert find_numbers.unpack(find_numbers.pack(key, steps), steps) == flat
    for bad in (0, -5, 1, True, 2.5, 1 << 60):
        with pytest.raises(find_numbers.PackError, match="is not a number of this layout"):
            find_numbers.unpack(bad, find_numbers.STRONGEST_STEPS)
    assert find_numbers.layout_of(find_numbers.VERSION) == find_numbers.LAYOUT == "facts-1"
    # The nightly scoring writes with this same layout.
    assert facts.VERSION == find_numbers.VERSION and facts.pack is find_numbers.pack


# -- the route --------------------------------------------------------------------------------


def post(monkeypatch, body: dict, answer=None) -> tuple[int, dict, list]:
    """Ask the route through the API itself; the database step is replaced."""
    import httpx

    from edfinder_api.main import app
    from edfinder_api.routers import find_economy as route

    asked: list = []

    async def search_near(body, pool, count_budget_ms=None):
        asked.append((body, count_budget_ms))
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def no_cache(*arguments):
        return None

    app.dependency_overrides[route.get_pool] = lambda: None
    app.dependency_overrides[route.get_redis] = lambda: None
    monkeypatch.setattr(route._find, "search_near", search_near)
    monkeypatch.setattr(route, "cache_get", no_cache)
    monkeypatch.setattr(route, "cache_set", no_cache)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://finder.test") as client:
            return await client.post("/api/find/economy", json=body)

    try:
        response = asyncio.run(scenario())
    finally:
        app.dependency_overrides.clear()
    return response.status_code, response.json(), asked


def test_the_route_answers_a_search_and_refuses_before_the_database(near, monkeypatch):
    real = ask(near, request(("Refinery", "Extraction")))
    status, body, asked = post(monkeypatch, {
        "economies": ["Refinery", "Extraction"], "reference_coords": AT_SOL, "radius_ly": 250}, real)
    assert status == 200 and body == json.loads(json.dumps(real))
    assert asked[0][0]["economies"] == ["Refinery", "Extraction"] and asked[0][1] == 1000
    assert (asked[0][0]["radius_ly"], asked[0][0]["reach_ly"], asked[0][0]["size"], asked[0][0]["from"]) \
        == (250.0, 250, 50, 0)
    # Give the count the whole time limit.
    _, _, asked = post(monkeypatch, {"economies": ["Refinery"], "reference_coords": AT_SOL,
                                    "wait_for_count": True}, real)
    assert asked[0][1] == 15000 and asked[0][0]["radius_ly"] == 100.0
    for bad in ({"economies": ["Mining"], "reference_coords": AT_SOL},
                {"economies": ["Refinery"]},
                {"economies": ["Refinery"], "reference_coords": AT_SOL, "radius_ly": 251},
                {"economies": ["Refinery", "Refinery"], "reference_coords": AT_SOL},
                {"economies": ["Refinery"], "reference_coords": {"x": 0}},
                {"economies": ["Refinery"], "reference_coords": AT_SOL, "size": 500, "from": 501}):
        status, _, asked = post(monkeypatch, bad, real)
        assert (status, asked) == (422, []), bad


def test_the_route_says_plainly_when_the_search_cannot_be_made(monkeypatch):
    asking = {"economies": ["Refinery"], "reference_coords": AT_SOL}
    status, body, _ = post(monkeypatch, asking, HTTPException(503, "still being worked out"))
    assert (status, body["detail"]) == (503, "still being worked out")
    status, body, _ = post(monkeypatch, asking, RuntimeError("the database went away"))
    assert status == 503 and body["title"] == "Search backend temporarily unavailable"
