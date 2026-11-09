"""The importer must not store a parent-evidence row per body again.

That table reached 116 GB and nothing in the app read it. The resolved parent
lives in bodies.direct_parent_body_pk; the importer only counts matches.
"""
from v3_spansh.adapter import CanonicalBatch


def test_batch_counts_parent_matches_without_storing_rows():
    batch = CanonicalBatch()
    batch.parent_resolution["resolved"] = 2
    other = CanonicalBatch()
    other.parent_resolution["resolved"] = 1
    other.parent_resolution["unresolved"] = 1
    batch.merge(other)
    assert batch.parent_resolution == {"resolved": 3, "unresolved": 1}
    assert "body_parent_evidence" not in batch.rows


def test_importer_resolves_parents_without_storing_evidence_rows():
    from test_v3_spansh_classified_adapter import SOURCE, body_rows, star, system
    from v3_spansh.adapter import SpanshAdapter

    record = system(
        star(10, spectralClass="G2", distanceToArrival=0),
        {"id64": 20, "bodyId": 2, "name": "Test planet", "type": "Planet",
         "subType": "Rocky body", "parents": [{"Star": 10}]},
        {"id64": 30, "bodyId": 3, "name": "Orphan", "type": "Planet",
         "subType": "Rocky body", "parents": [{"Planet": 99}]},
    )
    batch = SpanshAdapter(**SOURCE).adapt_system(record)
    assert "body_parent_evidence" not in batch.rows
    assert body_rows(batch)[1]["direct_parent_body_pk"] == 10
    assert batch.parent_resolution == {"resolved": 1, "unresolved": 1}
