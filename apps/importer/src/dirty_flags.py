"""Dirty-flag helpers for deferred rating/topology rebuilds.

The helpers only mark affected systems dirty. They never recalculate ratings
inline, so import and ingestion paths can call them safely in batches.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from shared_contracts.bulk_update_helper import bulk_update_replica_mode


RATING_AFFECTING_SYSTEM_FIELDS: tuple[str, ...] = (
    # Current v3.4 scorer reads main_star_type and updated_at directly.
    'main_star_type',
    'updated_at',
    # Conservatively retained because these fields represent colonisation state
    # or imported data quality and are already part of the dirty trigger contract.
    'primary_economy',
    'secondary_economy',
    'population',
    'is_colonised',
    'is_being_colonised',
    'main_star_subtype',
    'main_star_is_scoopable',
    'has_body_data',
    'body_count',
    'data_quality',
)

RATING_AFFECTING_BODY_FIELDS: tuple[str, ...] = (
    'system_id64',
    'body_type',
    'subtype',
    'is_main_star',
    'distance_from_star',
    'is_tidal_lock',
    'is_terraformable',
    'is_landable',
    'is_water_world',
    'is_earth_like',
    'is_ammonia_world',
    'bio_signal_count',
    'geo_signal_count',
    'spectral_class',
    'is_scoopable',
)

CLUSTER_AFFECTING_SYSTEM_FIELDS: tuple[str, ...] = (
    'x',
    'y',
    'z',
    'population',
    'is_colonised',
    'is_being_colonised',
    'galaxy_region_id',
)

PLANNER_AFFECTING_STATION_FIELDS: tuple[str, ...] = (
    'system_id64',
    'station_type',
    'distance_from_star',
    'body_name',
    'landing_pad_size',
    'primary_economy',
    'secondary_economy',
)

DEFAULT_DIRTY_CHUNK_SIZE = 10_000


def mark_system_rating_dirty(
    conn,
    system_id64: Any,
    *,
    logger: logging.Logger | None = None,
) -> int:
    """Mark one system for deferred rating recalculation."""
    return mark_systems_rating_dirty(conn, [system_id64], logger=logger)


def _chunks(values: list[int], chunk_size: int):
    chunk_size = max(1, int(chunk_size))
    for start in range(0, len(values), chunk_size):
        yield values[start:start + chunk_size]


def _normalise_system_ids(system_ids: Iterable[Any]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for raw in system_ids:
        if raw is None:
            continue
        sid = int(raw)
        if sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
    return out


def mark_systems_rating_dirty(
    conn,
    system_ids: Iterable[Any],
    *,
    chunk_size: int = DEFAULT_DIRTY_CHUNK_SIZE,
    logger: logging.Logger | None = None,
) -> int:
    """Mark the given systems for deferred rating recalculation.

    The update is idempotent: rows already marked dirty are not touched again.
    Returns the number of rows changed from clean to dirty.
    """
    return _mark_systems_dirty(
        conn,
        system_ids,
        set_rating_dirty=True,
        set_cluster_dirty=False,
        chunk_size=chunk_size,
        logger=logger,
    )


def mark_systems_rating_and_cluster_dirty(
    conn,
    system_ids: Iterable[Any],
    *,
    chunk_size: int = DEFAULT_DIRTY_CHUNK_SIZE,
    logger: logging.Logger | None = None,
) -> int:
    """Mark systems for both rating and cluster rebuilds."""
    return _mark_systems_dirty(
        conn,
        system_ids,
        set_rating_dirty=True,
        set_cluster_dirty=True,
        chunk_size=chunk_size,
        logger=logger,
    )


def _mark_systems_dirty(
    conn,
    system_ids: Iterable[Any],
    *,
    set_rating_dirty: bool,
    set_cluster_dirty: bool,
    chunk_size: int,
    logger: logging.Logger | None,
) -> int:
    ids = _normalise_system_ids(system_ids)
    if not ids:
        if logger:
            logger.debug("No systems to mark dirty.")
        return 0

    assignments: list[str] = []
    predicates: list[str] = []
    if set_rating_dirty:
        assignments.append("rating_dirty = TRUE")
        predicates.append("s.rating_dirty IS DISTINCT FROM TRUE")
    if set_cluster_dirty:
        assignments.append("cluster_dirty = TRUE")
        predicates.append("s.cluster_dirty IS DISTINCT FROM TRUE")
    if not assignments:
        return 0

    sql = f"""
        UPDATE systems s
           SET {', '.join(assignments)}
          FROM (SELECT unnest(%s::bigint[]) AS id64) dirty
         WHERE s.id64 = dirty.id64
           AND ({' OR '.join(predicates)})
    """

    marked = 0
    with bulk_update_replica_mode(conn):
        with conn.cursor() as cur:
            for chunk in _chunks(ids, chunk_size):
                cur.execute(sql, (chunk,))
                marked += max(cur.rowcount, 0)
        conn.commit()

    if logger:
        logger.info(
            "Marked %s/%s systems dirty%s.",
            marked,
            len(ids),
            " (rating+cluster)" if set_cluster_dirty else " (rating)",
        )
    return marked
