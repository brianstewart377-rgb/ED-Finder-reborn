from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any


@dataclass(frozen=True)
class ScalarDataInvariantCheck:
    key: str
    label: str
    sql: str


PRODUCTION_SAFE_STORED_MISSING_BODY_FLAG_SQL = """
SELECT COUNT(*)
FROM systems
WHERE COALESCE(has_body_data, FALSE) = FALSE
  AND COALESCE(body_count, 0) > 0;
"""

STORED_ZERO_BODY_COUNT_SQL = """
SELECT COUNT(*)
FROM systems
WHERE has_body_data = TRUE
  AND COALESCE(body_count, 0) = 0;
"""

DIRTY_TRUTHFUL_NO_BODIES_SQL = """
SELECT COUNT(*)
FROM systems s
WHERE s.rating_dirty = TRUE
  AND COALESCE(s.has_body_data, FALSE) = FALSE
  AND NOT EXISTS (
      SELECT 1
      FROM bodies b
      WHERE b.system_id64 = s.id64
  );
"""

EVIDENCE_ACTIVE_DUPLICATE_SUBJECTS_SQL = """
SELECT COUNT(*)
FROM (
    SELECT system_id64,
           subject_type,
           COALESCE(subject_id, '') AS subject_id_norm,
           evidence_type
    FROM evidence_records
    WHERE record_status = 'active'
    GROUP BY system_id64, subject_type, COALESCE(subject_id, ''), evidence_type
    HAVING COUNT(*) > 1
) duplicates;
"""

EVIDENCE_SUPERSEDED_FRESHNESS_DRIFT_SQL = """
SELECT COUNT(*)
FROM evidence_records
WHERE record_status = 'superseded'
  AND freshness_status IS DISTINCT FROM 'superseded';
"""

EVIDENCE_ACTIVE_SUPERSEDED_FRESHNESS_SQL = """
SELECT COUNT(*)
FROM evidence_records
WHERE record_status = 'active'
  AND freshness_status = 'superseded';
"""

COLONISATION_STATUS_AGE_BUCKETS_SQL = """
SELECT
    COUNT(*) FILTER (
        WHERE is_colonised IS TRUE OR is_being_colonised IS TRUE
    ) AS tracked_total,
    COUNT(*) FILTER (
        WHERE (is_colonised IS TRUE OR is_being_colonised IS TRUE)
          AND COALESCE(eddn_updated_at, updated_at) >= NOW() - INTERVAL '3 days'
    ) AS age_0_3d,
    COUNT(*) FILTER (
        WHERE (is_colonised IS TRUE OR is_being_colonised IS TRUE)
          AND COALESCE(eddn_updated_at, updated_at) >= NOW() - INTERVAL '7 days'
          AND COALESCE(eddn_updated_at, updated_at) < NOW() - INTERVAL '3 days'
    ) AS age_3_7d,
    COUNT(*) FILTER (
        WHERE (is_colonised IS TRUE OR is_being_colonised IS TRUE)
          AND COALESCE(eddn_updated_at, updated_at) >= NOW() - INTERVAL '14 days'
          AND COALESCE(eddn_updated_at, updated_at) < NOW() - INTERVAL '7 days'
    ) AS age_7_14d,
    COUNT(*) FILTER (
        WHERE (is_colonised IS TRUE OR is_being_colonised IS TRUE)
          AND COALESCE(eddn_updated_at, updated_at) < NOW() - INTERVAL '14 days'
    ) AS age_over_14d
FROM systems;
"""

COLONISATION_STATUS_AGE_BUCKET_KEYS = (
    'tracked_total',
    'age_0_3d',
    'age_3_7d',
    'age_7_14d',
    'age_over_14d',
)

RING_ASSOCIATION_STATUS_DRIFT_SQL = """
WITH name_matches AS (
    SELECT br.id AS ring_id,
           COUNT(b.id)::integer AS match_count
    FROM body_rings br
    LEFT JOIN bodies b
      ON b.system_id64 = br.system_id64
     AND b.name = br.body_name
    GROUP BY br.id
),
classified AS (
    SELECT br.id,
           CASE
               WHEN br.source = 'eddn_scan'
                    AND same_system_body.id IS NULL
                    AND (
                        br.source_body_id = 0
                        OR br.body_id = 0
                        OR br.body_name ILIKE '%% belt%%'
                        OR br.ring_name ILIKE '%% belt%%'
                    )
                   THEN 'belt_source_evidence'
               WHEN same_system_body.id IS NOT NULL
                    AND (
                        br.body_name IS NULL
                        OR same_system_body.name = br.body_name
                    )
                   THEN 'local_matched'
               WHEN same_system_body.id IS NOT NULL
                    AND br.body_name IS DISTINCT FROM same_system_body.name
                   THEN 'conflict'
               WHEN COALESCE(nm.match_count, 0) > 1
                   THEN 'ambiguous_body_identity'
               WHEN br.body_id IS NULL OR same_system_body.id IS NULL
                   THEN 'unresolved_body_identity'
               ELSE 'local_matched'
           END AS expected_association_status
    FROM body_rings br
    LEFT JOIN bodies same_system_body
      ON same_system_body.system_id64 = br.system_id64
     AND same_system_body.id = br.body_id
    LEFT JOIN name_matches nm ON nm.ring_id = br.id
),
ranked_local AS (
    SELECT br.id,
           ROW_NUMBER() OVER (
               PARTITION BY br.system_id64, br.body_id, br.ring_name, br.source
               ORDER BY br.id
           ) AS duplicate_rank
    FROM body_rings br
    JOIN classified c ON c.id = br.id
    WHERE br.body_id IS NOT NULL
      AND c.expected_association_status = 'local_matched'
),
final_status AS (
    SELECT br.id,
           CASE
               WHEN COALESCE(rl.duplicate_rank, 1) > 1 THEN 'conflict'
               ELSE c.expected_association_status
           END AS expected_association_status
    FROM body_rings br
    JOIN classified c ON c.id = br.id
    LEFT JOIN ranked_local rl ON rl.id = br.id
)
SELECT COUNT(*)
FROM body_rings br
JOIN final_status fs ON fs.id = br.id
WHERE br.association_status IS DISTINCT FROM fs.expected_association_status;
"""

TRUSTED_RING_ROWS_WITHOUT_LOCAL_BODY_SQL = """
SELECT COUNT(*)
FROM body_rings br
LEFT JOIN bodies b
  ON b.system_id64 = br.system_id64
 AND b.id = br.body_id
WHERE br.association_status = 'local_matched'
  AND (br.body_id IS NULL OR b.id IS NULL);
"""

TRUSTED_RING_BODY_NAME_MISMATCH_SQL = """
SELECT COUNT(*)
FROM body_rings br
JOIN bodies b
  ON b.system_id64 = br.system_id64
 AND b.id = br.body_id
WHERE br.association_status = 'local_matched'
  AND br.body_name IS NOT NULL
  AND br.body_name IS DISTINCT FROM b.name;
"""

DUPLICATE_TRUSTED_RING_ROWS_SQL = """
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY system_id64, body_id, ring_name, source
               ORDER BY id
           ) AS duplicate_rank
    FROM body_rings
    WHERE body_id IS NOT NULL
      AND association_status = 'local_matched'
)
SELECT COUNT(*)
FROM ranked
WHERE duplicate_rank > 1;
"""

CONFIRMED_STATION_LINKS_WITHOUT_BODY_SQL = """
SELECT COUNT(*)
FROM station_body_links
WHERE association_status = 'confirmed'
  AND body_id IS NULL;
"""

STATION_LINK_BODY_SYSTEM_MISMATCH_SQL = """
SELECT COUNT(*)
FROM station_body_links l
JOIN bodies b ON b.id = l.body_id
WHERE l.system_id64 IS DISTINCT FROM b.system_id64;
"""

STATION_LINK_STATION_SYSTEM_MISMATCH_SQL = """
SELECT COUNT(*)
FROM station_body_links l
JOIN stations st ON st.id = l.station_id
WHERE l.system_id64 IS DISTINCT FROM st.system_id64;
"""

STATION_LINK_BODY_NAME_MISMATCH_SQL = """
SELECT COUNT(*)
FROM station_body_links l
JOIN bodies b ON b.id = l.body_id
WHERE COALESCE(l.body_name, '') IS DISTINCT FROM COALESCE(b.name, '');
"""

CONFIRMED_STATION_LINKS_UNKNOWN_LANE_SQL = """
SELECT COUNT(*)
FROM station_body_links
WHERE association_status = 'confirmed'
  AND lane NOT IN ('orbital', 'surface');
"""

CONFIRMED_STATION_LINKS_NONEXACT_SQL = """
SELECT COUNT(*)
FROM station_body_links
WHERE association_status = 'confirmed'
  AND association_confidence IS DISTINCT FROM 'exact';
"""

SHARED_DATA_INVARIANT_SCALAR_CHECKS = (
    ScalarDataInvariantCheck('stored_zero_body_count', 'zero_body_count_drift', STORED_ZERO_BODY_COUNT_SQL),
    ScalarDataInvariantCheck('stored_missing_body_flag', 'missing_body_flag_rows', PRODUCTION_SAFE_STORED_MISSING_BODY_FLAG_SQL),
    ScalarDataInvariantCheck('dirty_truthful_no_bodies', 'dirty_truthful_no_bodies', DIRTY_TRUTHFUL_NO_BODIES_SQL),
    ScalarDataInvariantCheck('evidence_active_duplicate_subjects', 'evidence_active_dupes', EVIDENCE_ACTIVE_DUPLICATE_SUBJECTS_SQL),
    ScalarDataInvariantCheck('evidence_superseded_freshness_drift', 'evidence_superseded_drift', EVIDENCE_SUPERSEDED_FRESHNESS_DRIFT_SQL),
    ScalarDataInvariantCheck('evidence_active_superseded_freshness', 'evidence_active_freshness', EVIDENCE_ACTIVE_SUPERSEDED_FRESHNESS_SQL),
    ScalarDataInvariantCheck('ring_association_status_drift', 'ring_status_drift', RING_ASSOCIATION_STATUS_DRIFT_SQL),
    ScalarDataInvariantCheck('trusted_ring_rows_without_local_body', 'trusted_rings_no_body', TRUSTED_RING_ROWS_WITHOUT_LOCAL_BODY_SQL),
    ScalarDataInvariantCheck('trusted_ring_body_name_mismatch', 'trusted_ring_name_drift', TRUSTED_RING_BODY_NAME_MISMATCH_SQL),
    ScalarDataInvariantCheck('duplicate_trusted_ring_rows', 'duplicate_trusted_rings', DUPLICATE_TRUSTED_RING_ROWS_SQL),
    ScalarDataInvariantCheck('confirmed_station_links_without_body', 'confirmed_links_no_body', CONFIRMED_STATION_LINKS_WITHOUT_BODY_SQL),
    ScalarDataInvariantCheck('station_link_body_system_mismatch', 'link_body_system_drift', STATION_LINK_BODY_SYSTEM_MISMATCH_SQL),
    ScalarDataInvariantCheck('station_link_station_system_mismatch', 'link_station_system_drift', STATION_LINK_STATION_SYSTEM_MISMATCH_SQL),
    ScalarDataInvariantCheck('station_link_body_name_mismatch', 'link_body_name_drift', STATION_LINK_BODY_NAME_MISMATCH_SQL),
    ScalarDataInvariantCheck('confirmed_station_links_unknown_lane', 'confirmed_unknown_lane', CONFIRMED_STATION_LINKS_UNKNOWN_LANE_SQL),
    ScalarDataInvariantCheck('confirmed_station_links_nonexact', 'confirmed_nonexact', CONFIRMED_STATION_LINKS_NONEXACT_SQL),
)

SHARED_DATA_INVARIANT_SCALAR_CHECKS_BY_KEY = {
    check.key: check
    for check in SHARED_DATA_INVARIANT_SCALAR_CHECKS
}

ADMIN_DATA_INVARIANT_CHECK_KEYS = tuple(
    check.key
    for check in SHARED_DATA_INVARIANT_SCALAR_CHECKS
)


def normalise_colonisation_status_age_buckets(row: Mapping[str, Any] | tuple[Any, ...]) -> dict[str, int]:
    if isinstance(row, Mapping):
        return {
            key: int(row.get(key) or 0)
            for key in COLONISATION_STATUS_AGE_BUCKET_KEYS
        }
    return {
        key: int((row[index] if index < len(row) else 0) or 0)
        for index, key in enumerate(COLONISATION_STATUS_AGE_BUCKET_KEYS)
    }
