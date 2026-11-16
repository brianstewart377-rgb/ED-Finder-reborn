"""What a body orbits: the importer reads the whole of each body's list.

``v3_spansh.adapter.parent_links`` since 16 November 2026. Before then the
importer read only the first link, so every centre point that two bodies
orbit (a barycentre), and every body whose first link was a centre point
nobody had scanned, got no parent, and the System Map had to guess them.

The records are the real Spansh records of ``tests/fixtures/ratings_v4_sources``
(fetched 2026-09-08), edited where a test says so. No network, no database.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "importer" / "src"))

from spansh_delta.parents import CENTRE_POINT, fills  # noqa: E402
from tests.test_spansh_delta import HD_38179, MAIA, SOL, fixture_data, records  # noqa: E402,F401
from v3_spansh.adapter import SpanshAdapter  # noqa: E402
from v3_spansh.contracts import COPY_COLUMNS  # noqa: E402

SOURCE = {"source_id": 40, "source_run_id": UUID(int=0),
          "observed_at": datetime(2026, 9, 8, 16, 0, tzinfo=timezone.utc)}
DERIV_DAR = 2037701937507


def adapted(record):
    batch = SpanshAdapter(**SOURCE).adapt_system(deepcopy(record))
    return batch, [dict(zip(COPY_COLUMNS["bodies"], row, strict=True)) for row in batch.rows["bodies"]]


def parents(record) -> dict[str, str | None]:
    """Each body's name, and the name of what the importer says it orbits."""
    _, rows = adapted(record)
    names = {row["body_pk"]: row["name"] for row in rows}
    return {row["name"]: names.get(row["direct_parent_body_pk"]) for row in rows}


def body(record, name):
    return next(one for one in record["bodies"] if one["name"] == name)


def test_a_pair_scanned_as_a_body_orbits_what_its_members_lists_say(records):
    # Pluto and Charon orbit a centre point, which Spansh lists as a body of its
    # own with no list. Pluto's list (centre point 31, then Sol) says the pair
    # orbits Sol.
    found = parents(records[SOL])
    assert found["Sol barycentre 31"] == "Sol"
    assert found["Pluto"] == found["Charon"] == "Sol barycentre 31"
    assert found["Moon"] == "Earth" and found["Earth"] == "Sol"
    assert found["Sol"] is None, "the top of the system orbits nothing we hold"


def test_pairs_inside_pairs_are_followed_up_the_lists(records):
    found = parents(records[DERIV_DAR])
    assert found["Deriv-Dar barycentre 29"] == "Deriv-Dar barycentre 28"
    assert found["Deriv-Dar barycentre 28"] == "Deriv-Dar barycentre 1"
    assert found["Deriv-Dar barycentre 46"] == "Deriv-Dar C"


def test_a_body_whose_first_link_is_a_point_nobody_scanned_hangs_from_what_the_pair_orbits(records):
    # HD 38179 5 and 6 orbit centre point 19, which Spansh does not list.
    assert body(records[HD_38179], "HD 38179 5")["parents"] == [{"Null": 19}, {"Star": 0}]
    found = parents(records[HD_38179])
    assert found["HD 38179 5"] == found["HD 38179 6"] == "HD 38179"


def test_maias_six_pairs_get_what_they_orbit(records):
    found = parents(records[MAIA])
    assert {name: found[name] for name in found if "barycentre" in name} == {
        "Maia barycentre 8": "Maia A", "Maia barycentre 14": "Maia A",
        "Maia barycentre 24": "Maia B 1", "Maia barycentre 28": "Maia B 1",
        "Maia barycentre 32": "Maia B", "Maia barycentre 36": "Maia B 2",
    }


def test_a_missing_planet_or_star_is_never_passed_over(records):
    # Edited: Pluto's list names a planet Spansh does not list, first or after
    # a centre point. A moon must never be hung straight from a star because
    # its planet is missing, so the search stops there.
    for links in ([{"Planet": 77}, {"Null": 31}, {"Star": 0}],
                  [{"Null": 99}, {"Planet": 77}, {"Star": 0}]):
        record = deepcopy(records[SOL])
        body(record, "Pluto")["parents"] = links
        assert parents(record)["Pluto"] is None


def test_a_parent_the_first_link_gives_is_never_changed(records):
    # On all twelve systems, wherever the first link is a body we hold, that
    # body is still the parent: the rule only adds parents.
    for record in records.values():
        _, rows = adapted(record)
        held = {one["bodyId"]: one["id64"] for one in record["bodies"] if "bodyId" in one}
        for one, row in zip([one for one in record["bodies"] if isinstance(one, dict)], rows, strict=True):
            first = (one.get("parents") or [{}])[0]
            if first and next(iter(first.values())) in held:
                assert row["direct_parent_body_pk"] == held[next(iter(first.values()))], one["name"]


def test_damaged_lists_that_would_make_a_loop_keep_only_their_first_link(records):
    # Edited: Pluto's list says it orbits Charon and Charon's says it orbits
    # Pluto, each through a centre point nobody scanned. Following both would
    # make a loop, so each keeps what its first link gives: nothing.
    record = deepcopy(records[SOL])
    body(record, "Pluto")["parents"] = [{"Null": 90}, {"Planet": 33}]
    body(record, "Charon")["parents"] = [{"Null": 91}, {"Planet": 32}]
    found = parents(record)
    assert found["Pluto"] is None and found["Charon"] is None


def test_the_counts_say_how_many_parents_were_found(records):
    batch, rows = adapted(records[SOL])
    assert batch.parent_resolution == {"resolved": sum(row["direct_parent_body_pk"] is not None
                                                      for row in rows)}
    assert "body_parent_evidence" not in batch.rows


def test_what_is_filled_in_is_what_the_importer_now_writes_and_the_old_rule_left_out(records):
    total = 0
    for record in records.values():
        _, rows = adapted(record)
        written = {row["body_pk"]: row["direct_parent_body_pk"] for row in rows}
        held = {one["bodyId"]: one["id64"] for one in record["bodies"] if "bodyId" in one}
        for system_id64, body_pk, parent in fills(record):
            assert system_id64 == record["id64"] and written[body_pk] == parent
            old = next(one for one in record["bodies"] if one["id64"] == body_pk).get("parents")
            assert not old or next(iter(old[0].values())) not in held
        total += len(fills(record))
        # A system whose lists name no centre point has nothing to fill in, so
        # the one-time job passes such systems over without reading them in full.
        if CENTRE_POINT not in json.dumps(record).encode():
            assert fills(record) == []
    assert total == 34
