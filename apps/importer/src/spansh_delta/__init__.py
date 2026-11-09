"""Bounded-memory reading of Spansh full-detail galaxy dumps for delta layers.

This package is repository tooling for the "nightly delta layer" study
(``galaxy_1day`` / ``galaxy_7days`` / ``galaxy_1month``).  It reads a Spansh
``galaxy*.json.gz`` one system at a time, maps each system through the frozen
V3 canonical adapter (``v3_spansh.adapter.SpanshAdapter`` -- imported, never
edited), and decides whether anything the V3 canonical schema stores actually
changed.  It performs no database access and writes nothing.
"""

from .normalize import (
    CONTENT_TABLES,
    DIGEST_NAMES,
    FLOAT4_COLUMNS,
    ORBITAL_PHASE_COLUMNS,
    POLICIES,
    MaterialChange,
    NormalizedSystem,
    TouchProfile,
    base_rows_from_export,
    classify_material_change,
    content_digest,
    content_digests,
    normalize_system,
    record_touch_profile,
)
from .stream import (
    LineFormatError,
    SpanshRecord,
    SpanshStream,
    StreamReceipt,
)

__all__ = [
    "CONTENT_TABLES",
    "DIGEST_NAMES",
    "FLOAT4_COLUMNS",
    "ORBITAL_PHASE_COLUMNS",
    "POLICIES",
    "LineFormatError",
    "MaterialChange",
    "NormalizedSystem",
    "SpanshRecord",
    "SpanshStream",
    "StreamReceipt",
    "TouchProfile",
    "base_rows_from_export",
    "classify_material_change",
    "content_digest",
    "content_digests",
    "normalize_system",
    "record_touch_profile",
]
