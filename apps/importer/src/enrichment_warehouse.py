"""Boundary definitions for the enrichment warehouse.

This module is deliberately small and dependency-free. It centralises the
warehouse table names and conservative SQL safety checks used by the offline
staging loader so moving the warehouse behind a different connection later does
not require hunting table names across importer modules.
"""
from __future__ import annotations

import re


_IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

WAREHOUSE_SOURCE_RUNS_TABLE = 'enrichment_source_runs'
WAREHOUSE_SOURCE_FILES_TABLE = 'enrichment_source_files'
WAREHOUSE_RAW_RECORDS_TABLE = 'enrichment_raw_records'
WAREHOUSE_STAGING_STATIONS_TABLE = 'staging_edsm_stations'
WAREHOUSE_STAGING_BODIES_TABLE = 'staging_edsm_bodies'
WAREHOUSE_STAGING_BODY_RINGS_TABLE = 'staging_body_rings'
EVIDENCE_RECORDS_TABLE = 'evidence_records'

WAREHOUSE_BASE_TABLES = (
    WAREHOUSE_SOURCE_RUNS_TABLE,
    WAREHOUSE_SOURCE_FILES_TABLE,
    WAREHOUSE_RAW_RECORDS_TABLE,
)
WAREHOUSE_STATION_WRITE_TABLES = WAREHOUSE_BASE_TABLES + (
    WAREHOUSE_STAGING_STATIONS_TABLE,
    EVIDENCE_RECORDS_TABLE,
)
WAREHOUSE_BODY_RING_WRITE_TABLES = WAREHOUSE_BASE_TABLES + (
    WAREHOUSE_STAGING_BODIES_TABLE,
    WAREHOUSE_STAGING_BODY_RINGS_TABLE,
)
WAREHOUSE_WRITE_TABLES = tuple(dict.fromkeys(
    WAREHOUSE_STATION_WRITE_TABLES + WAREHOUSE_BODY_RING_WRITE_TABLES
))
WAREHOUSE_READ_TABLES = WAREHOUSE_WRITE_TABLES

CANONICAL_SYSTEMS_TABLE = 'systems'
CANONICAL_STATIONS_TABLE = 'stations'
CANONICAL_BODIES_TABLE = 'bodies'
CANONICAL_BODY_RINGS_TABLE = 'body_rings'
CANONICAL_BODY_SCAN_FACTS_TABLE = 'body_scan_facts'
CANONICAL_STATION_BODY_LINKS_TABLE = 'station_body_links'
CANONICAL_TABLE_DENYLIST = frozenset((
    CANONICAL_SYSTEMS_TABLE,
    CANONICAL_STATIONS_TABLE,
    CANONICAL_BODIES_TABLE,
    CANONICAL_BODY_RINGS_TABLE,
    CANONICAL_BODY_SCAN_FACTS_TABLE,
    CANONICAL_STATION_BODY_LINKS_TABLE,
))

_WRITE_KEYWORD_RE = re.compile(
    r'\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|ALTER)\b',
    re.IGNORECASE,
)
_LEADING_KEYWORD_RE = re.compile(r'^\s*(?:WITH\s+[\s\S]+?\)\s*)?([A-Za-z]+)\b', re.IGNORECASE)
_INSERT_TARGET_RE = re.compile(
    r'\bINSERT\s+INTO\s+(?:ONLY\s+)?(?P<table>"?[A-Za-z_][A-Za-z0-9_]*"?(?:\."?[A-Za-z_][A-Za-z0-9_]*"?)?)',
    re.IGNORECASE,
)
_CANONICAL_WRITE_RE = re.compile(
    r'\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO|TRUNCATE|DROP\s+TABLE|ALTER\s+TABLE)\s+'
    r'(?:ONLY\s+)?(?P<table>"?[A-Za-z_][A-Za-z0-9_]*"?(?:\."?[A-Za-z_][A-Za-z0-9_]*"?)?)',
    re.IGNORECASE,
)
_TABLE_REFERENCE_RE = re.compile(
    r'\b(?:FROM|JOIN|INTO|UPDATE|TABLE|TRUNCATE)\s+'
    r'(?:ONLY\s+)?(?P<table>"?[A-Za-z_][A-Za-z0-9_]*"?(?:\."?[A-Za-z_][A-Za-z0-9_]*"?)?)',
    re.IGNORECASE,
)


def warehouse_write_tables_for_source(source: str | None) -> tuple[str, ...]:
    """Return the warehouse tables a source-specific staging write may touch."""
    if source == 'edsm_nightly_bodies':
        return WAREHOUSE_BODY_RING_WRITE_TABLES
    return WAREHOUSE_STATION_WRITE_TABLES


def render_table_name(table: str, schema: str | None = None) -> str:
    """Render an optionally schema-qualified table name with strict identifiers."""
    _validate_identifier(table, 'table')
    if schema is None:
        return table
    _validate_identifier(schema, 'schema')
    return f'{schema}.{table}'


def is_write_sql(sql: str) -> bool:
    """Return true if a SQL string contains a write or DDL keyword."""
    return bool(_WRITE_KEYWORD_RE.search(_strip_sql_comments(sql)))


def extract_referenced_table_names(sql: str) -> set[str]:
    """Conservatively extract referenced table names from simple SQL text."""
    text = _strip_sql_comments(sql)
    return {
        _normalise_table_name(match.group('table'))
        for match in _TABLE_REFERENCE_RE.finditer(text)
    }


def assert_staging_write_sql_is_safe(sql: str) -> None:
    """Reject staging write SQL that targets anything outside the warehouse."""
    text = _strip_sql_comments(sql)
    leading_keyword = _leading_keyword(text)
    if leading_keyword != 'insert':
        raise ValueError('staging writes must be INSERT/UPSERT statements')

    insert_match = _INSERT_TARGET_RE.search(text)
    if not insert_match:
        raise ValueError('staging write SQL must include an INSERT target')
    target_table = _normalise_table_name(insert_match.group('table'))
    if target_table not in WAREHOUSE_WRITE_TABLES:
        raise ValueError(f'staging write target is not a warehouse table: {target_table}')

    for match in _CANONICAL_WRITE_RE.finditer(text):
        written_table = _normalise_table_name(match.group('table'))
        if written_table in CANONICAL_TABLE_DENYLIST:
            raise ValueError(f'canonical table writes are not allowed: {written_table}')


def assert_reconciliation_sql_is_read_only(sql: str) -> None:
    """Reject reconciliation SQL that contains write or DDL statements."""
    if is_write_sql(sql):
        raise ValueError('reconciliation SQL must be read-only')


def _leading_keyword(sql: str) -> str | None:
    match = _LEADING_KEYWORD_RE.search(sql)
    if not match:
        return None
    return match.group(1).lower()


def _normalise_table_name(name: str) -> str:
    table = name.replace('"', '').split('.')[-1]
    return table.lower()


def _validate_identifier(identifier: str, kind: str) -> None:
    if not _IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f'unsafe {kind} identifier: {identifier!r}')


def _strip_sql_comments(sql: str) -> str:
    without_line_comments = re.sub(r'--.*?$', '', sql, flags=re.MULTILINE)
    return re.sub(r'/\*.*?\*/', '', without_line_comments, flags=re.DOTALL)
