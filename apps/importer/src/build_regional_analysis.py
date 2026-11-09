#!/usr/bin/env python3
"""Build system_regional_analysis from system coordinates.

Uses normal x/y/z Euclidean distance.  No PostGIS dependency is required.

Candidate lookup is a one-time load, not a per-target query. The candidate
pool (systems that are colonised, being colonised, or populated — ~144K rows
in production, versus ~188M in `systems`) is loaded once into memory and
sorted by x; each target system then does a binary-search slice plus a
vectorized y/z filter instead of a fresh SQL round trip. The original
per-target query (one `systems` index-range scan + `stations` join per row)
measured ~100ms-1s each on production, which made a 5,000,000-row nightly
`--limit` a genuine multi-day job — see the 2026-08-06 incident where two
nights' runs were still both mid-flight, contending with each other, when a
third was about to start on top of them.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from typing import Any

import numpy as np
import psycopg
from psycopg.rows import dict_row

from api_source_resolver import add_api_source_to_path

API_SRC = add_api_source_to_path(
    __file__,
    required_paths=('mechanics/regional_rules.py', 'regional/regional_analysis.py'),
)

from mechanics.regional_rules import REGIONAL_DISTANCE_BUCKETS
from regional.regional_analysis import compute_regional_analysis

# Distinguishes a placeholder row written for a NaN/Infinity-coordinate
# target (see _load_targets) from a real `computed` analysis, so default
# mode's target query can tell the two apart and re-check the former
# instead of treating it as permanently done.
NON_FINITE_COORDS_SOURCE = 'non_finite_coords'


UPSERT = """
INSERT INTO system_regional_analysis (
    system_id64,
    nearest_colonised_system_id64,
    nearest_colonised_system_name,
    nearest_colonised_system_distance_ly,
    colonised_within_25ly,
    colonised_within_50ly,
    colonised_within_100ly,
    colonised_within_250ly,
    regional_isolation_score,
    regional_density_score,
    regional_expansion_score,
    regional_competition_score,
    regional_role,
    archetype_regional_fit,
    rationale,
    data_source,
    computed_at
) VALUES (
    %(system_id64)s,
    %(nearest_colonised_system_id64)s,
    %(nearest_colonised_system_name)s,
    %(nearest_colonised_system_distance_ly)s,
    %(colonised_within_25ly)s,
    %(colonised_within_50ly)s,
    %(colonised_within_100ly)s,
    %(colonised_within_250ly)s,
    %(regional_isolation_score)s,
    %(regional_density_score)s,
    %(regional_expansion_score)s,
    %(regional_competition_score)s,
    %(regional_role)s,
    %(archetype_regional_fit)s::jsonb,
    %(rationale)s::jsonb,
    %(data_source)s,
    now()
)
ON CONFLICT (system_id64) DO UPDATE SET
    nearest_colonised_system_id64 = EXCLUDED.nearest_colonised_system_id64,
    nearest_colonised_system_name = EXCLUDED.nearest_colonised_system_name,
    nearest_colonised_system_distance_ly = EXCLUDED.nearest_colonised_system_distance_ly,
    colonised_within_25ly = EXCLUDED.colonised_within_25ly,
    colonised_within_50ly = EXCLUDED.colonised_within_50ly,
    colonised_within_100ly = EXCLUDED.colonised_within_100ly,
    colonised_within_250ly = EXCLUDED.colonised_within_250ly,
    regional_isolation_score = EXCLUDED.regional_isolation_score,
    regional_density_score = EXCLUDED.regional_density_score,
    regional_expansion_score = EXCLUDED.regional_expansion_score,
    regional_competition_score = EXCLUDED.regional_competition_score,
    regional_role = EXCLUDED.regional_role,
    archetype_regional_fit = EXCLUDED.archetype_regional_fit,
    rationale = EXCLUDED.rationale,
    data_source = EXCLUDED.data_source,
    computed_at = now()
"""


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError('must be a non-negative integer')
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--all', action='store_true', help='Process all systems.')
    parser.add_argument('--dirty', action='store_true', help='Process dirty/rating-dirty systems when available.')
    parser.add_argument('--limit', type=non_negative_int, default=None)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    dsn = os.environ['DATABASE_URL']
    # These backfill queries scan 188M rows, so the role's 15s timeout is far too low.
    # Keep this connection-level: SET LOCAL would not survive each 1,000-row commit.
    with psycopg.connect(dsn, options='-c statement_timeout=1800000') as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            targets = _load_targets(cur, args)
            pool = _load_candidate_pool(cur)
            for index, system in enumerate(targets, start=1):
                candidates = _candidates_for(pool, system)
                analysis = compute_regional_analysis(system, candidates)
                _write(cur, analysis)
                if index % 1000 == 0:
                    conn.commit()
                    print(f'Processed {index} systems')
        conn.commit()
    print(f'Processed {len(targets)} systems')


def _load_targets(cur: Any, args: argparse.Namespace) -> list[dict[str, Any]]:
    coord_filter = 'x IS NOT NULL AND y IS NOT NULL AND z IS NOT NULL'
    missing_coord_filter = 'x IS NULL OR y IS NULL OR z IS NULL'
    if args.dirty:
        where = (
            f'WHERE ({coord_filter}) AND (rating_dirty = TRUE '
            'OR (cluster_dirty = TRUE '
            'AND has_body_data = TRUE '
            'AND macro_grid_id IS NOT NULL)'
            ')'
        )
        excluded_where = (
            f'WHERE ({missing_coord_filter}) AND (rating_dirty = TRUE '
            'OR (cluster_dirty = TRUE '
            'AND has_body_data = TRUE '
            'AND macro_grid_id IS NOT NULL)'
            ')'
        )
    elif not args.all:
        # A system stays eligible if it has no row yet, OR its only row is
        # the NON_FINITE_COORDS_SOURCE placeholder written below for a
        # target that failed the isfinite check at write time. Without the
        # second half, a system whose bad coordinates later get corrected
        # by a later import would never be reprocessed: default mode has no
        # other trigger to revisit it (coordinate updates only ever mark
        # cluster_dirty, and there's no scheduled --dirty regional-analysis
        # run to catch that). Since a system only reaches `targets` in the
        # first place when its current coordinates pass the isfinite check
        # below, re-selecting a NON_FINITE_COORDS_SOURCE row here can only
        # mean the coordinates are now good — the isfinite filter itself is
        # what decides that, this clause just stops the stale placeholder
        # from permanently blocking the re-check.
        where = (
            f'WHERE ({coord_filter}) AND ('
            'NOT EXISTS (SELECT 1 FROM system_regional_analysis r WHERE r.system_id64 = systems.id64) '
            'OR EXISTS (SELECT 1 FROM system_regional_analysis r WHERE r.system_id64 = systems.id64 '
            f"AND r.data_source = '{NON_FINITE_COORDS_SOURCE}')"
            ')'
        )
        excluded_where = (
            f'WHERE ({missing_coord_filter}) AND NOT EXISTS '
            '(SELECT 1 FROM system_regional_analysis r '
            'WHERE r.system_id64 = systems.id64)'
        )
    else:
        where = f'WHERE {coord_filter}'
        excluded_where = f'WHERE {missing_coord_filter}'
    limit = f' LIMIT {int(args.limit)}' if args.limit is not None else ''
    cur.execute(f"""
        SELECT id64, name, x, y, z, population, is_colonised, is_being_colonised
        FROM systems
        {where}
        ORDER BY id64
        {limit}
    """)
    fetched = [dict(row) for row in cur.fetchall()]
    # x/y/z IS NOT NULL above doesn't catch NaN/Infinity (`real` allows both).
    # Filtered here, once, rather than per-target inside the main loop's
    # _candidates_for call — a target that fails this check would only ever
    # get a degenerate `_unknown()` analysis anyway (see _candidates_for's
    # own NaN short-circuit), so there's no reason to pay for a full
    # compute_regional_analysis + DB write for it 5,000,000 times over.
    targets = []
    non_finite = []
    for row in fetched:
        if math.isfinite(row['x']) and math.isfinite(row['y']) and math.isfinite(row['z']):
            targets.append(row)
        else:
            non_finite.append(row)
    # Codex Review finding on #426: filtering these out here without writing
    # anything for them means they never get a system_regional_analysis row,
    # so the NOT EXISTS above keeps re-selecting the same non-finite systems
    # on every future run, permanently eating into the --limit batch. Write
    # the same degenerate row a zero-candidate finite target would get
    # (compute_regional_analysis returns _unknown() whenever candidates is
    # empty, regardless of the target's own coordinates), but tagged with
    # NON_FINITE_COORDS_SOURCE instead of the usual 'computed' — see the
    # `where` clause above for why: that tag is what lets this row be
    # revisited later instead of permanently blocking reprocessing.
    for row in non_finite:
        analysis = compute_regional_analysis(row, [])
        analysis['data_source'] = NON_FINITE_COORDS_SOURCE
        _write(cur, analysis)
    cur.execute(f"""
        SELECT COUNT(*) AS excluded_count
        FROM systems
        {excluded_where}
    """)
    excluded_count = int(cur.fetchone()['excluded_count']) + len(non_finite)
    print(f'Excluded {excluded_count:,} coordinate-less systems from regional analysis this run')
    return targets


@dataclass(frozen=True)
class CandidatePool:
    """The full colonised/being-colonised/populated candidate set, loaded
    once and sorted by x so _candidates_for can binary-search a target's
    x-range instead of re-querying Postgres per target system."""
    id64: np.ndarray
    name: list[str]
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    population: np.ndarray
    is_colonised: np.ndarray
    is_being_colonised: np.ndarray
    station_count: np.ndarray


def _load_candidate_pool(cur: Any) -> CandidatePool:
    """Same WHERE/JOIN as the old per-target query, run once instead of once
    per target. Rows with a NULL or non-finite (NaN/Infinity — `real` allows
    both) coordinate are dropped here rather than filtered in SQL: the old
    `x BETWEEN lo AND hi` would never have matched any of those either
    (comparisons against NULL or NaN are never true under IEEE 754), so this
    preserves the same exclusion with no SQL WHERE-clause change needed. A
    NaN slipping through would also violate the sorted-array precondition
    np.searchsorted relies on below, silently corrupting lookups for
    unrelated targets — not just this one candidate."""
    cur.execute("""
        SELECT s.id64, s.name, s.x, s.y, s.z, s.population, s.is_colonised, s.is_being_colonised,
               COUNT(st.id) AS station_count
        FROM systems s
        LEFT JOIN stations st ON st.system_id64 = s.id64
        WHERE s.is_colonised = TRUE OR s.is_being_colonised = TRUE OR s.population > 0
        GROUP BY s.id64
    """)

    def _has_finite_coords(row: dict[str, Any]) -> bool:
        return (
            row['x'] is not None and row['y'] is not None and row['z'] is not None
            and math.isfinite(row['x']) and math.isfinite(row['y']) and math.isfinite(row['z'])
        )

    rows = [row for row in cur.fetchall() if _has_finite_coords(row)]
    rows.sort(key=lambda row: row['x'])
    return CandidatePool(
        id64=np.array([row['id64'] for row in rows], dtype=np.int64),
        name=[row['name'] for row in rows],
        x=np.array([row['x'] for row in rows], dtype=np.float64),
        y=np.array([row['y'] for row in rows], dtype=np.float64),
        z=np.array([row['z'] for row in rows], dtype=np.float64),
        population=np.array([row['population'] or 0 for row in rows], dtype=np.int64),
        is_colonised=np.array([bool(row['is_colonised']) for row in rows], dtype=bool),
        is_being_colonised=np.array([bool(row['is_being_colonised']) for row in rows], dtype=bool),
        station_count=np.array([row['station_count'] for row in rows], dtype=np.int64),
    )


def _candidates_for(pool: CandidatePool, system: dict[str, Any]) -> list[dict[str, Any]]:
    """In-memory equivalent of the old per-target SQL box filter
    (`x/y/z BETWEEN target±radius AND id64 != target`). x is pre-sorted, so
    the x-range is a binary search (np.searchsorted); y/z/self-exclusion are
    a vectorized filter over just that x-slice. Both stages use float64
    throughout — the same precision Postgres evaluates `real BETWEEN
    double precision` at — so boundary systems land on the same side as the
    original SQL would have put them."""
    x, y, z = float(system['x']), float(system['y']), float(system['z'])
    if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
        # A NaN/Infinity target coordinate: the old SQL's `x BETWEEN
        # (x-r) AND (x+r)` would never be true for a NaN/Infinity bound
        # either, so this always returned zero candidates. Short-circuit
        # explicitly rather than trust np.searchsorted with a NaN key,
        # which (unlike a real comparison) doesn't reliably return an
        # empty slice.
        return []
    radius = max(REGIONAL_DISTANCE_BUCKETS)
    lo = int(np.searchsorted(pool.x, x - radius, side='left'))
    hi = int(np.searchsorted(pool.x, x + radius, side='right'))
    if lo >= hi:
        return []
    mask = (
        (pool.y[lo:hi] >= y - radius) & (pool.y[lo:hi] <= y + radius)
        & (pool.z[lo:hi] >= z - radius) & (pool.z[lo:hi] <= z + radius)
        & (pool.id64[lo:hi] != system['id64'])
    )
    return [
        {
            'id64': int(pool.id64[lo + i]),
            'name': pool.name[lo + i],
            'x': float(pool.x[lo + i]),
            'y': float(pool.y[lo + i]),
            'z': float(pool.z[lo + i]),
            'population': int(pool.population[lo + i]),
            'is_colonised': bool(pool.is_colonised[lo + i]),
            'is_being_colonised': bool(pool.is_being_colonised[lo + i]),
            'station_count': int(pool.station_count[lo + i]),
        }
        for i in np.nonzero(mask)[0]
    ]


def _write(cur: Any, analysis: dict[str, Any]) -> None:
    row = dict(analysis)
    row['archetype_regional_fit'] = json.dumps(row.get('archetype_regional_fit') or {})
    row['rationale'] = json.dumps(row.get('rationale') or {})
    cur.execute(UPSERT, row)


if __name__ == '__main__':
    main()
