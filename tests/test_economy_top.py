"""Find's ranked lists across the band (spansh_delta.economy_top; migration
033). Ratings rebuild, build step 5.

Each list must be exactly what sorting the whole facts table would give: the
sample and the cut-offs it leads to may only ever decide how long a rebuild
takes. So every way of making a list is held against the slow sort here, on
the real fixture systems.

Runs on PostgreSQL 18 with every migration applied; needs
RATINGS_V4_VALIDATION_DATABASE_URL.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import itertools
import os
from pathlib import Path
import sys

import pytest

os.environ.setdefault("CORS_ORIGINS", "https://economy-top-test.invalid")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "importer" / "src"))

from edfinder_api import find_numbers  # noqa: E402
from edfinder_api.cpe.body_facts import ECONOMIES, PAIRS  # noqa: E402
from edfinder_api.find_numbers import COUNT_COLUMNS, LIST_COUNT, LIST_NO_ECONOMY  # noqa: E402
from spansh_delta import economy_facts as facts  # noqa: E402
from spansh_delta import economy_top as top  # noqa: E402
from tests.test_economy_facts import before_033, filled  # noqa: E402
from tests.test_galaxy_scores import template  # noqa: E402,F401
from tests.test_galaxy_update import SEPTEMBER, SOL, fill_classes, run  # noqa: E402
from tests.test_reach import WITHIN_500_OF_SOL, inhabit, pi, waiting, work_out  # noqa: E402,F401

COUNTS = ", ".join(COUNT_COLUMNS)
EXPRESSIONS = find_numbers.list_expressions()


def the_slow_way(pi, list_no: int, depth: int | None = None) -> list[tuple]:
    """One list as sorting the whole facts table gives it."""
    expression = EXPRESSIONS[list_no - 1]
    found = pi.all(
        f"SELECT system_id64, {expression}, bodies_known, ground_slots, {COUNTS} FROM v3_state.economy_facts "
        f"WHERE {expression} > 0 ORDER BY {expression} DESC, bodies_known DESC, system_id64")
    return found if depth is None else found[:depth]


def stored(pi) -> dict[int, list[tuple]]:
    """Each list as the current set holds it, in place order; and its places
    run from 1 with none missing."""
    lists: dict[int, list[tuple]] = {number: [] for number in range(1, LIST_COUNT + 1)}
    for list_no, place, *row in pi.all(
            f"SELECT t.list_no, t.place, t.system_id64, t.number, t.bodies_known, t.ground_slots, {COUNTS} "
            "FROM v3_state.economy_top t JOIN v3_state.economy_top_build b USING (build_id) "
            "WHERE b.is_current ORDER BY t.list_no, t.place"):
        lists[list_no].append(tuple(row))
        assert place == len(lists[list_no]), (list_no, place)
    return lists


def builds(pi) -> list[tuple]:
    return pi.all("SELECT build_id, version, depth, is_current, finished_at >= started_at "
                  "FROM v3_state.economy_top_build ORDER BY build_id")


# -- the numbering --------------------------------------------------------------------


def test_the_lists_are_numbered_as_the_design_has_them():
    assert (len(ECONOMIES), len(PAIRS), LIST_COUNT, LIST_NO_ECONOMY) == (7, 21, 36, 36)
    numbers = ([find_numbers.list_no("strongest", index) for index in range(1, 8)]
               + [find_numbers.list_no("purest", index) for index in range(1, 8)]
               + [find_numbers.list_no("pairs", index) for index in range(1, 22)])
    assert numbers == list(range(1, 36))
    assert EXPRESSIONS == tuple(
        [f"strongest[{index}]" for index in range(1, 8)] + [f"purest[{index}]" for index in range(1, 8)]
        + [f"pairs[{index}]" for index in range(1, 22)] + ["ground_slots::bigint"])
    for column, index in (("strongest", 0), ("strongest", 8), ("pairs", 22), ("ground_slots", 1)):
        with pytest.raises(ValueError):
            find_numbers.list_no(column, index)


# -- what a rebuild writes --------------------------------------------------------------


def test_the_scoring_writes_every_list_as_the_slow_sort_of_the_facts(pi, tmp_path):
    report = filled(pi, tmp_path)
    assert report.fill_finished_now
    lists = stored(pi)
    for list_no in range(1, LIST_COUNT + 1):
        assert lists[list_no] == the_slow_way(pi, list_no), list_no
    # Each real system has a number for every list, and the lists differ.
    assert {len(rows) for rows in lists.values()} == {8}
    assert len({tuple(row[0] for row in rows) for rows in lists.values()}) > 12
    # Systems level in a list's number are among them: the one with more
    # known bodies is listed first.
    level = [(above, below) for rows in lists.values() for above, below in itertools.pairwise(rows)
             if above[1] == below[1]]
    assert len(level) > 10 and all(above[2] > below[2] for above, below in level)
    # The list for no economy: every system with a ground slot, most slots first.
    no_economy = lists[LIST_NO_ECONOMY]
    assert [row[1] for row in no_economy] == [row[3] for row in no_economy]
    assert [row[1] for row in no_economy] == sorted((row[1] for row in no_economy), reverse=True)
    assert len(no_economy) >= 4
    # One set, and it is the one searches read.
    assert [row[1:] for row in builds(pi)] == [(facts.VERSION, top.DEPTH, True, True)]


def test_the_report_and_the_status_say_what_was_written(pi, tmp_path):
    assert top.status_lines(pi.connection) == [
        "Find's lists across the band: not made yet. The scoring makes them after each run, "
        "once the first fill of Find's facts has finished."]
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    run(pi, tmp_path, pi.records.values())
    report = waiting(pi)
    lists = stored(pi)
    places = sum(len(rows) for rows in lists.values())
    in_use = sum(1 for rows in lists.values() if rows)
    assert report.lists.as_dict() == {
        "build_id": report.lists.build_id, "depth": 20_000, "lists": in_use, "places": places,
        "read_whole": 0, "seconds": report.lists.as_dict()["seconds"]}
    assert report.as_dict()["economy_lists"] == report.lists.as_dict()
    said = "\n".join(report.lines())
    assert (f"Find's lists across the band ({facts.VERSION}): {in_use} lists written afresh, "
            f"{places:,} places in all (up to 20,000 each), 0 min ") in said
    (status,) = top.status_lines(pi.connection)
    assert status.startswith("Find's lists across the band: written 20")
    assert status.endswith(f" UTC for {facts.VERSION}, {in_use} lists, {places:,} places in all "
                           "(up to 20,000 each).")


def test_a_list_keeps_its_first_places_only_and_the_cut_offs_do_not_change_what_they_are(pi, tmp_path):
    """Three places a list, of eight systems. The sample (all of the table
    here) puts each list's cut-off twice as far down as the last place, at
    its sixth row, and the two rows below it are never sorted."""
    filled(pi, tmp_path)
    slow = {list_no: the_slow_way(pi, list_no) for list_no in range(1, LIST_COUNT + 1)}
    assert {len(whole) for whole in slow.values()} == {8}
    cuts = top.cut_offs(pi.connection, 3, 100)
    for list_no, cut in enumerate(cuts, 1):
        sixth = slow[list_no][5]
        assert cut == tuple(int(value) for value in (sixth[1], sixth[2], sixth[0])), list_no
    report = top.rebuild(pi.connection, depth=3, percent=100, progress=False)
    assert (report.failed, report.skipped, report.read_whole) == (None, None, [])
    lists = stored(pi)
    for list_no in range(1, LIST_COUNT + 1):
        assert lists[list_no] == slow[list_no][:3], list_no
    assert (report.lists, report.places, report.depth) == (36, 108, 3)
    assert [row[1:] for row in builds(pi)] == [(facts.VERSION, 3, True, True)]

    # Five places a list: twice as far down is the tenth row, and no list has
    # one. With no cut-off every system above 0 is put in order.
    assert top.cut_offs(pi.connection, 5, 100) == [top._NO_CUT_OFF] * 36
    report = top.rebuild(pi.connection, depth=5, percent=100, progress=False)
    assert (report.failed, report.read_whole, report.places) == (None, [], 180)
    lists = stored(pi)
    for list_no in range(1, LIST_COUNT + 1):
        assert lists[list_no] == slow[list_no][:5], list_no


def test_a_system_is_in_no_list_it_has_no_number_for(pi, tmp_path):
    """A row as the scoring writes it for a system with no known bodies, and
    one where no body suits one economy: 0 stands for that."""
    filled(pi, tmp_path)
    nothing_known, one_missing = sorted(WITHIN_500_OF_SOL - {SOL})[:2]
    pi.connection.execute(
        "UPDATE v3_state.economy_facts SET bodies_known = 0, ground_slots = 0, strongest = NULL, "
        "purest = NULL, pairs = NULL WHERE system_id64 = %s", (nothing_known,))
    pi.connection.execute(
        "UPDATE v3_state.economy_facts SET strongest[1] = 0 WHERE system_id64 = %s", (one_missing,))
    for depth, percent in ((20_000, 1.0), (3, 100), (5, 100)):
        report = top.rebuild(pi.connection, depth=depth, percent=percent, progress=False)
        assert report.failed is None
        lists = stored(pi)
        for list_no in range(1, LIST_COUNT + 1):
            assert lists[list_no] == the_slow_way(pi, list_no, depth), (depth, list_no)
    report = top.rebuild(pi.connection, progress=False)
    lists = stored(pi)
    assert all(nothing_known not in {row[0] for row in rows} for rows in lists.values())
    assert [len(rows) for rows in lists.values()] == [6] + [7] * 35
    assert one_missing not in {row[0] for row in lists[1]}
    assert all(one_missing in {row[0] for row in lists[list_no]} for list_no in range(2, LIST_COUNT + 1))


def test_a_cut_off_exactly_at_the_last_place_keeps_exactly_those_rows(pi, tmp_path, monkeypatch):
    """The cut-off is a whole row of the order: the number, then the known
    bodies, then the id. Set at each list's third row, exactly three rows are
    at or above it, and no list has to be read again."""
    filled(pi, tmp_path)
    third = [(tuple(int(value) for value in (whole[2][1], whole[2][2], whole[2][0])) if len(whole) >= 3
              else top._NO_CUT_OFF) for whole in (the_slow_way(pi, list_no) for list_no in range(1, LIST_COUNT + 1))]
    assert top._NO_CUT_OFF not in third
    monkeypatch.setattr(top, "cut_offs", lambda connection, depth, percent=1.0: third)
    report = top.rebuild(pi.connection, depth=3, progress=False)
    assert (report.failed, report.read_whole) == (None, [])
    lists = stored(pi)
    for list_no in range(1, LIST_COUNT + 1):
        assert lists[list_no] == the_slow_way(pi, list_no, 3), list_no


def test_a_list_whose_cut_off_was_too_high_is_read_again_whole(pi, tmp_path, monkeypatch):
    """However wrong the sample is, the lists are right: a cut-off at each
    list's very first row leaves one row above it, the list comes out short,
    and it is read from the whole table instead."""
    filled(pi, tmp_path)
    slow = {list_no: the_slow_way(pi, list_no) for list_no in range(1, LIST_COUNT + 1)}
    first = [(tuple(int(value) for value in (whole[0][1], whole[0][2], whole[0][0])) if whole
              else top._NO_CUT_OFF) for whole in slow.values()]
    monkeypatch.setattr(top, "cut_offs", lambda connection, depth, percent=1.0: first)
    report = top.rebuild(pi.connection, depth=3, progress=False)
    assert report.failed is None
    # Every list that has a row: one row is fewer than three.
    assert report.read_whole == [list_no for list_no, whole in slow.items() if whole]
    lists = stored(pi)
    for list_no in range(1, LIST_COUNT + 1):
        assert lists[list_no] == slow[list_no][:3], list_no
    assert f"  {len(report.read_whole)} of them had to be read from the whole table (lists 1, " in "\n".join(
        report.lines())


def test_systems_level_in_number_and_bodies_are_listed_in_the_order_of_their_ids(pi, tmp_path):
    """So that the lists are the same every night. Two real systems are given
    a third's number and count of bodies here: the galaxy's own systems near
    Sol are never level in both."""
    filled(pi, tmp_path)
    whole = the_slow_way(pi, LIST_NO_ECONOMY)
    assert len(whole) >= 4
    level = sorted(row[0] for row in whole[:3])
    pi.connection.execute(
        "UPDATE v3_state.economy_facts SET ground_slots = %s, bodies_known = %s WHERE system_id64 = ANY(%s)",
        (whole[0][1], whole[0][2], level))
    for depth in (2, 3, 20_000):
        report = top.rebuild(pi.connection, depth=depth, percent=100, progress=False)
        assert report.failed is None
        found = stored(pi)[LIST_NO_ECONOMY]
        assert [row[0] for row in found[:3]] == level[:depth], depth
        assert found == the_slow_way(pi, LIST_NO_ECONOMY, depth)


# -- one whole set at a time ---------------------------------------------------------------


def test_each_rebuild_replaces_the_set_before_it(pi, tmp_path):
    filled(pi, tmp_path)
    (first,) = builds(pi)
    places = pi.one("SELECT count(*) FROM v3_state.economy_top")[0]
    report = top.rebuild(pi.connection, progress=False)
    (second,) = builds(pi)
    assert second[0] == report.build_id > first[0] and second[1:] == first[1:]
    assert pi.all("SELECT DISTINCT build_id FROM v3_state.economy_top") == [(second[0],)]
    assert pi.one("SELECT count(*) FROM v3_state.economy_top")[0] == places == report.places


def test_a_changed_systems_place_follows_its_facts_the_same_night(pi, tmp_path):
    filled(pi, tmp_path)
    before = stored(pi)
    without = deepcopy(pi.records[SOL])
    ringed = next(body for body in without["bodies"] if body.get("rings") and body.get("type") == "Planet")
    without["bodies"].remove(ringed)
    run(pi, tmp_path, [without], made_at=SEPTEMBER + timedelta(days=1))
    report = waiting(pi)
    assert report.facts.changed == 1 and report.lists.failed is None
    after = stored(pi)
    assert after != before
    for list_no in range(1, LIST_COUNT + 1):
        assert after[list_no] == the_slow_way(pi, list_no), list_no
    sol_before = next(row for row in before[LIST_NO_ECONOMY] if row[0] == SOL)
    sol_after = next(row for row in after[LIST_NO_ECONOMY] if row[0] == SOL)
    # One known body fewer, and one ring fewer among its counts.
    assert sol_after[2] == sol_before[2] - 1
    rings = 4 + COUNT_COLUMNS.index("ring_count")
    assert sol_after[rings] == sol_before[rings] - 1


def test_a_rebuild_that_fails_leaves_the_lists_as_they_were_and_says_so(pi, tmp_path, monkeypatch):
    filled(pi, tmp_path)
    before, set_before = stored(pi), builds(pi)
    pi.connection.execute("SET statement_timeout = '15min'")
    monkeypatch.setattr(top, "EVERY_LIST_SQL", "SELECT 1 / (%(build)s - %(build)s) "
                        "+ %(depth)s + cardinality(%(numbers)s::bigint[]) + cardinality(%(bodies)s::integer[]) "
                        "+ cardinality(%(ids)s::bigint[])")
    report = top.rebuild(pi.connection, progress=False)
    assert report.failed == "division by zero" and report.as_dict() == {"failed": "division by zero"}
    assert report.lines() == [
        "Find's lists across the band could NOT be written afresh: division by zero. "
        "Searches across the band read the lists as they were; the next run tries again."]
    assert stored(pi) == before and builds(pi) == set_before
    # The time limit the run had is the one it has again.
    assert pi.one("SHOW statement_timeout") == ("15min",)
    monkeypatch.undo()
    assert top.rebuild(pi.connection, progress=False).failed is None
    assert pi.one("SHOW statement_timeout") == ("15min",)


def test_a_rebuild_has_its_own_time_limit_and_no_other(pi, tmp_path, monkeypatch):
    filled(pi, tmp_path)
    seen = []
    real = top.cut_offs

    def looking(connection, depth, percent=1.0):
        seen.append(connection.execute("SHOW statement_timeout").fetchone()[0])
        return real(connection, depth, percent)

    monkeypatch.setattr(top, "cut_offs", looking)
    pi.connection.execute("SET statement_timeout = '15min'")
    top.rebuild(pi.connection, progress=False)
    assert seen == ["90min"] and top.TIME_LIMIT == "90min"


# -- when there is nothing to make them from ---------------------------------------------------


def test_no_lists_are_made_until_a_fill_of_the_facts_has_finished(pi, tmp_path, monkeypatch):
    fill_classes(pi.connection, pi.schema, pi.records.values())
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    monkeypatch.setattr(facts, "BATCH_SYSTEMS", 3)
    ticks = iter([0, 0, 0])
    monkeypatch.setattr(facts, "_now", lambda: next(ticks, 10_000))
    report = facts.FactsReport()
    facts.carry_on(pi.connection, pi.schema, report, minutes=1, progress=False)
    monkeypatch.undo()
    assert facts.latest_fill(pi.connection).finished_at is None
    lists = top.rebuild(pi.connection, progress=False)
    assert lists.skipped == "not made yet: the first fill of Find's facts has not finished"
    assert lists.lines() == ["Find's lists across the band: not made yet: the first fill of Find's facts "
                             "has not finished"]
    assert builds(pi) == [] and pi.one("SELECT count(*) FROM v3_state.economy_top") == (0,)
    # The next run finishes the fill, and the lists are made.
    facts.carry_on(pi.connection, pi.schema, facts.FactsReport(), progress=False)
    assert top.rebuild(pi.connection, progress=False).lists > 0


def test_a_fill_for_another_layout_of_the_numbers_is_not_one_to_make_lists_from(pi, tmp_path):
    filled(pi, tmp_path)
    pi.connection.execute("DELETE FROM v3_state.economy_top_build")
    # The layout before this one: its rows have no body counts to list by.
    pi.connection.execute("UPDATE v3_state.economy_facts_fill SET version = 'an older rulebook/facts-1'")
    assert top.rebuild(pi.connection, progress=False).skipped == top.NOT_YET
    # Another rulebook's release with this layout: the numbers can be compared.
    pi.connection.execute("UPDATE v3_state.economy_facts_fill SET version = %s",
                          (f"an older rulebook/{find_numbers.LAYOUT}",))
    assert top.rebuild(pi.connection, progress=False).skipped is None


def test_without_migration_033_there_are_no_lists_and_the_scoring_goes_on(pi, tmp_path):
    before_033(pi)
    inhabit(pi, SOL, 0, 0, 0)
    work_out(pi)
    run(pi, tmp_path, pi.records.values())
    report = waiting(pi)
    # No facts are kept without the migration, so there is nothing to list.
    assert report.facts.skipped is not None and report.lists is None
    lists = top.rebuild(pi.connection, progress=False)
    assert lists.skipped == "not kept in this database (migration 033 has not been applied)"
    assert lists.as_dict() == {"skipped": lists.skipped}
    assert top.status_lines(pi.connection) == [
        "Find's lists across the band are not kept in this database (migration 033 has not been applied)."]
