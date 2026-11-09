"""
ED Finder — Galaxy region lookup loader.
=========================================

Loads the static RLE-encoded region map (data/region_map.json) and exposes
``find_galaxy_region(x, z) -> Optional[int]`` for the importer.

This module replaces the legacy 2 099-line ``RegionMapData.py`` (pure-data
file masquerading as code). The data now lives in
``backend/data/region_map.json`` so it can be edited / regenerated /
audited as data, and is no longer included in the API container image.

Algorithm credit: klightspeed/EliteDangerousRegionMap.
Coordinates are in galaxy XZ plane (Y is ignored; regions are columnar).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger("ed-finder.region_map")

_REGION_X0 = -49985
_REGION_Z0 = -24105
_PIXEL_SCALE = 83 / 4096      # px per LY (matches klightspeed pipeline)

# Resolved at module import. Either a real lookup or a no-op stub.
_REGION_NAMES: List[str] = []
_REGION_MAP:   List[List[Tuple[int, int]]] = []
_AVAILABLE = False


def _data_path() -> Path:
    """Resolve the JSON file relative to this module unless overridden."""
    override = os.getenv("REGION_MAP_JSON")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "data" / "region_map.json"


def _load() -> None:
    global _REGION_NAMES, _REGION_MAP, _AVAILABLE
    path = _data_path()
    if not path.is_file():
        log.warning(
            "Galaxy region lookup: DISABLED (region_map.json not found at %s)",
            path,
        )
        _AVAILABLE = False
        return

    try:
        with path.open(encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, ValueError) as exc:
        log.warning("Galaxy region lookup: DISABLED (failed to load %s: %s)", path, exc)
        _AVAILABLE = False
        return

    # JSON has lists-of-lists; convert inner pairs to tuples for tiny perf
    # win on the hot path (tuple unpack < list index).
    _REGION_NAMES = list(blob["regions"])
    _REGION_MAP = [[(rl, pv) for rl, pv in row] for row in blob["regionmap"]]
    _AVAILABLE = True
    log.info(
        "Galaxy region lookup: ENABLED (%d named ED codex regions, %d map rows)",
        max(0, len(_REGION_NAMES) - 1),  # index 0 is the empty/unused slot
        len(_REGION_MAP),
    )


_load()


def is_available() -> bool:
    return _AVAILABLE


def find_galaxy_region(x: float, z: float) -> Optional[int]:
    """Return the galaxy region id (1-42) for a system at (x, z), or None
    if the coordinates are outside the region map.

    Y is intentionally ignored — regions are defined in the XZ plane.
    """
    if not _AVAILABLE:
        return None
    px = int((x - _REGION_X0) * _PIXEL_SCALE)
    pz = int((z - _REGION_Z0) * _PIXEL_SCALE)
    if px < 0 or pz < 0 or pz >= len(_REGION_MAP):
        return None
    row = _REGION_MAP[pz]
    rx = 0
    pv = 0
    for rl, pv in row:
        if px < rx + rl:
            break
        rx += rl
    else:
        pv = 0
    return int(pv) if pv else None


def region_name(region_id: int) -> Optional[str]:
    """Return the canonical name for a region id, or None."""
    if not _AVAILABLE or region_id <= 0 or region_id >= len(_REGION_NAMES):
        return None
    name = _REGION_NAMES[region_id]
    return name or None


__all__ = ["find_galaxy_region", "is_available", "region_name"]
