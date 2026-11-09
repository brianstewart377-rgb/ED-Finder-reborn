"""Pure-Python helpers that are called by multiple routers.

Nothing in here touches module-level state directly — takes things as
arguments, returns values. Easy to unit test.
"""
from __future__ import annotations

import logging
from typing import Any

import asyncpg

log = logging.getLogger('ed_finder')

SOL_ID64 = 10477373803


# ---------------------------------------------------------------------------
# Bounded status-failure reason codes.
#
# `local_search.local_db_status()` swallows database errors and reports
# `{available: False, reason: <code>}`; `routers/meta.py` serves that payload
# from `/api/local/status`. The reason must be one of these fixed codes —
# never the exception text, which can carry SQL, host names or DSN fragments.
# The raw text goes to the server log only.
# ---------------------------------------------------------------------------
STATUS_REASON_TIMEOUT = 'db_timeout'
STATUS_REASON_SCHEMA = 'db_schema_missing'
STATUS_REASON_CONNECTION = 'db_unavailable'
STATUS_REASON_UNKNOWN = 'db_error'
STATUS_REASON_CODES: frozenset[str] = frozenset({
    STATUS_REASON_TIMEOUT, STATUS_REASON_SCHEMA, STATUS_REASON_CONNECTION, STATUS_REASON_UNKNOWN,
})

# Exception classes → reason code, by type rather than by message text so the
# classification cannot be steered by whatever the server put in the error.
_STATUS_REASON_TIMEOUT_TYPES: tuple[type[BaseException], ...] = (
    TimeoutError,                      # asyncio.TimeoutError is an alias on 3.11+
    asyncpg.QueryCanceledError,        # statement_timeout / pg_cancel_backend
)
_STATUS_REASON_SCHEMA_TYPES: tuple[type[BaseException], ...] = (
    asyncpg.UndefinedTableError,
    asyncpg.UndefinedColumnError,
    asyncpg.UndefinedFunctionError,
    asyncpg.UndefinedObjectError,
    asyncpg.InvalidSchemaNameError,
)
_STATUS_REASON_CONNECTION_TYPES: tuple[type[BaseException], ...] = (
    asyncpg.InterfaceError,            # pool closed / connection released / not connected
    asyncpg.PostgresConnectionError,   # connection-exception class (08xxx)
    asyncpg.TooManyConnectionsError,
    asyncpg.CannotConnectNowError,
    asyncpg.InsufficientResourcesError,
    ConnectionError,
    OSError,
)


def status_failure_reason_code_for_exception(exc: BaseException) -> str:
    """Classify an exception raised by a status probe into a fixed reason code.

    Always returns one of STATUS_REASON_CODES; never any part of ``exc``.
    Ordering matters: ``QueryCanceledError`` is checked before the broad
    connection group, and ``OSError`` last so socket-level failures
    (refused, reset, unreachable) count as "unavailable".
    """
    if isinstance(exc, _STATUS_REASON_TIMEOUT_TYPES):
        return STATUS_REASON_TIMEOUT
    if isinstance(exc, _STATUS_REASON_SCHEMA_TYPES):
        return STATUS_REASON_SCHEMA
    if isinstance(exc, _STATUS_REASON_CONNECTION_TYPES):
        return STATUS_REASON_CONNECTION
    return STATUS_REASON_UNKNOWN


def safe_coords_from_row(row: Any) -> dict[str, float | None]:
    """Return a trusted coordinate triple from a DB/API row.

    The systems table historically used NOT NULL DEFAULT 0 for x/y/z, so
    import paths without coordinates produced fake origin records. Sol is the
    only real system at (0,0,0); every other all-zero triple is unknown.
    """
    if row is None:
        return {'x': None, 'y': None, 'z': None}
    get = row.get if hasattr(row, 'get') else row.__getitem__
    try:
        x = get('x')
        y = get('y')
        z = get('z')
        id64 = get('id64')
    except Exception:
        return {'x': None, 'y': None, 'z': None}

    if x is None or y is None or z is None:
        return {'x': None, 'y': None, 'z': None}

    try:
        fx, fy, fz = float(x), float(y), float(z)
    except (TypeError, ValueError):
        return {'x': None, 'y': None, 'z': None}

    try:
        numeric_id64 = int(id64) if id64 is not None else None
    except (TypeError, ValueError):
        numeric_id64 = None

    if fx == 0.0 and fy == 0.0 and fz == 0.0 and numeric_id64 != SOL_ID64:
        return {'x': None, 'y': None, 'z': None}

    return {'x': fx, 'y': fy, 'z': fz}


# ---------------------------------------------------------------------------
# DB-row flattening
# ---------------------------------------------------------------------------
def sys_row_to_dict(r: Any) -> dict:
    """Convert an asyncpg Record to the wire shape the frontend understands.

    Mirrors the column layout produced by the main search query — if the
    search SQL changes, this translator must change alongside it.
    """
    if r is None:
        return {}
    d = dict(r)
    coords = safe_coords_from_row(d)
    d['id64']        = d.get('id64')
    d['name']        = d.get('name', 'Unknown')
    d['x']           = coords['x']
    d['y']           = coords['y']
    d['z']           = coords['z']
    d['coords']      = coords
    d['distance']    = d.get('distance')
    d['population']  = d.get('population')
    d['archetype_score'] = d.get('archetype_score')
    d['archetype_tier'] = d.get('archetype_tier')
    d['primary_archetype'] = d.get('primary_archetype')
    d['secondary_archetype'] = d.get('secondary_archetype')
    d['archetype_confidence'] = d.get('archetype_confidence')
    d['overall_development_potential'] = d.get('overall_development_potential')
    d['buildability_score'] = d.get('buildability_score')
    d['build_complexity'] = d.get('build_complexity')
    d['purity_score'] = d.get('purity_score')
    d['contamination_risk'] = d.get('contamination_risk')
    d['est_total_slots'] = d.get('est_total_slots')
    d['tags'] = list(d.get('tags') or d.get('display_tags') or [])
    d['primaryEconomy']   = d.get('primary_economy',   'Unknown')
    d['secondaryEconomy'] = d.get('secondary_economy', 'None')
    d['security']         = d.get('security',   'Unknown')
    d['allegiance']       = d.get('allegiance', 'Unknown')
    d['government']       = d.get('government', 'Unknown')
    d['is_colonised']        = d.get('is_colonised', False)
    d['is_being_colonised']  = d.get('is_being_colonised', False)
    d['bodies'] = d.get('bodies', [])
    return d


# ---------------------------------------------------------------------------
# Cluster rebuild (retired V2 route).
#
# V2 ran `docker compose ... run importer build_clusters.py` from inside the
# API container. V3 never mounts the Docker socket or a compose project into
# the API, so that shell-out could only ever fail (and did, silently, as a
# "failed" admin job). The V3 derived-data path is the governed operator
# workflow, not an API-triggered subprocess. The route now answers 410 Gone;
# this constant is the single source for its problem detail.
# ---------------------------------------------------------------------------
CLUSTER_REBUILD_GONE_PROBLEM: dict[str, Any] = {
    'type': 'https://ed-finder.local/problem/v2-cluster-rebuild-retired',
    'title': 'V2 cluster rebuild route retired',
    'status': 410,
    'detail': (
        'POST /api/admin/rebuild-clusters belonged to the V2 Docker Compose deployment '
        'and shelled into a container runtime the V3 API does not have. Cluster/derived '
        'rebuilds now run only through the governed V3 operator workflows.'
    ),
}
