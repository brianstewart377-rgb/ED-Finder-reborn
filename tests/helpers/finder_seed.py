"""Build a real Finder database for tests, and make it plan like production.

The seeding, statistics import and publication steps are copied from the old
repo's ``tests/test_v3_finder_galaxy_window_plan_pg18.py``, without its receipt
and plan-probe machinery. They run the real migrations and reuse the row
helpers in ``tests/test_v3_derived_product_publication.py``, so the tables,
views and indexes are the ones production has.

The systems themselves are generated (ids 42, 43, ...), because a plan test
needs thousands of rows and the real fixture has twelve. Only the copy of each
template row's id, name, position, star class and scores is generated; every
other column is the real fixture row's.

Needs PostgreSQL 18 (``pg_restore_relation_stats``).
"""
from __future__ import annotations

import json
from typing import Any

from psycopg import sql

# Production on 2026-11-09: one generation of about 198.5 million systems.
PRODUCTION_SYSTEMS = 198_500_000
# Rows in v3_state.system_state after the 2026-11-09 load.
PRODUCTION_STATE_SYSTEMS = 160_498
FIRST_SYSTEM_ID = 42
# Planner-relevant settings of the production database.
PRODUCTION_PLANNER_SETTINGS = {
    "effective_cache_size": "128GB",
    "work_mem": "32MB",
}

_SEARCH_OVERRIDES = {
    "system_id64": "n",
    "name": "'Plan Probe ' || n",
    "x_ly": "{x}",
    "y_ly": "{y}",
    "z_ly": "{z}",
    "position_ly": "cube(ARRAY[{x}, {y}, {z}])",
    "main_star_class": "CASE WHEN mod(n, 97) = 0 THEN 'O' ELSE 'K' END",
}
# Deterministic spread: x/z across +-1,200 ly, y across +-120 ly.
_COORDINATES = {
    "x": "(mod(n * 7919, 24001) - 12000)::double precision / 10",
    "y": "(mod(n * 104729, 2401) - 1200)::double precision / 10",
    "z": "(mod(n * 15485863, 24001) - 12000)::double precision / 10",
}
_ARCHETYPE_PRIMES = (7919, 104729, 1299709, 15485863, 179424673, 2147483647, 6700417, 27644437)


def _archetype_overrides() -> dict[str, str]:
    from tests.test_v3_derived_product_publication import ARCHETYPES

    overrides = {
        "system_id64": "n",
        "weighted_potential": "mod(n * 2654435761, 1000000)::double precision / 10000",
    }
    for key, prime in zip(ARCHETYPES, _ARCHETYPE_PRIMES, strict=True):
        overrides[f"{key}_score"] = f"mod(n * {prime}, 101)::smallint"
        overrides[f"{key}_weighted_e4"] = f"mod(n * {prime}, 1000001)::integer"
    return overrides


def _copy_seed_row(connection, table: str, generation, systems: int, overrides: dict[str, str]) -> None:
    columns = [row[0] for row in connection.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='v3_derived' AND table_name=%s ORDER BY ordinal_position",
        (table,),
    ).fetchall()]
    select = [
        sql.SQL(overrides[name].format(**_COORDINATES)) if name in overrides
        else sql.Identifier("t", name)
        for name in columns
    ]
    connection.execute(sql.SQL(
        "INSERT INTO v3_derived.{} ({}) SELECT {} "
        "FROM v3_derived.{} t CROSS JOIN generate_series(%s::bigint, %s::bigint) n "
        "WHERE t.derived_generation_id=%s AND t.system_id64=%s"
    ).format(
        sql.Identifier(table),
        sql.SQL(",").join(map(sql.Identifier, columns)),
        sql.SQL(",").join(select),
        sql.Identifier(table),
    ), (FIRST_SYSTEM_ID + 1, FIRST_SYSTEM_ID + systems - 1, generation, FIRST_SYSTEM_ID))


def _seed_generation(connection, *, systems: int):
    from tests import test_v3_derived_product_publication as fixture

    generation = fixture._insert_generation(connection)
    fixture._seed_rating(connection, generation, FIRST_SYSTEM_ID)
    connection.execute(
        """
        INSERT INTO v3_derived.system_rating_vector (
            derived_generation_id, system_id64, chunk_ordinal,
            loaded_body_count, physical_body_count, subtype_projection_sha256,
            potential, quality, quality_min, quality_max, completeness, confidence
        ) SELECT derived_generation_id, n, chunk_ordinal,
                 loaded_body_count, physical_body_count, subtype_projection_sha256,
                 potential, quality, quality_min, quality_max, completeness, confidence
            FROM v3_derived.system_rating_vector CROSS JOIN generate_series(%s::bigint, %s::bigint) n
           WHERE derived_generation_id=%s AND system_id64=%s
        """,
        (FIRST_SYSTEM_ID + 1, FIRST_SYSTEM_ID + systems - 1, generation, FIRST_SYSTEM_ID),
    )
    fixture._make_ready(connection, generation)
    assert fixture._publish_generation(connection, generation) == 1
    for code in ("system_search", "system_archetype"):
        fixture._register_product(connection, generation, code)
    fixture._insert_search_row(connection, generation, FIRST_SYSTEM_ID)
    _copy_seed_row(connection, "system_search", generation, systems, _SEARCH_OVERRIDES)
    fixture._insert_archetype_row(connection, generation, FIRST_SYSTEM_ID)
    _copy_seed_row(connection, "system_archetype", generation, systems, _archetype_overrides())
    connection.execute(
        "UPDATE v3_meta.derived_product SET lifecycle_state='READY', "
        "validation_receipt=%s::jsonb, validation_sha256=%s, validated_at=transaction_timestamp() "
        "WHERE derived_generation_id=%s",
        (json.dumps({"status": "VERIFIED"}), fixture.HASH, generation),
    )
    return generation


def build_finder_database(connection, *, systems: int) -> str:
    """Apply the real migrations and seed one published generation whose two
    Finder products are published too, so the ``v3_app`` views serve it."""
    from tests import test_v3_derived_product_publication as fixture

    for name in (*fixture.PRE_015, "015_v3_derived_product_publication.sql",
                 "024_v3_system_state.sql"):
        connection.execute((fixture.MIGRATIONS / name).read_text(encoding="utf-8"))
    # Imported statistics must not be replaced by autovacuum mid-test.
    for relation in (
        "v3_derived.system_search", "v3_derived.system_archetype",
        "v3_meta.current_derived_generation", "v3_meta.derived_generation",
        "v3_meta.derived_product", "v3_state.system_state",
    ):
        connection.execute(f"ALTER TABLE {relation} SET (autovacuum_enabled = false)")
    generation = _seed_generation(connection, systems=systems)
    # The publication gate takes both products in one transaction.
    with connection.transaction():
        for code in ("system_search", "system_archetype"):
            connection.execute(
                "SELECT v3_meta.publish_derived_product(%s, %s, 'finder-seed-test')",
                (generation, code),
            )
    connection.execute("VACUUM (ANALYZE) v3_derived.system_search")
    connection.execute("VACUUM (ANALYZE) v3_derived.system_archetype")
    return str(generation)


def import_production_statistics(connection) -> dict[str, float]:
    """Make every Finder relation and index plan as production-sized.

    ``reltuples`` is set to the production row count while ``relpages`` stays
    the real page count, so the planner's density x current-pages estimate is
    the production row count. Partial indexes keep their real fraction of the
    table. Column statistics come from the real ANALYZE."""
    targets = {
        ("v3_derived", "system_archetype"): float(PRODUCTION_SYSTEMS),
        ("v3_derived", "system_search"): float(PRODUCTION_SYSTEMS),
        ("v3_state", "system_state"): float(PRODUCTION_STATE_SYSTEMS),
    }
    imported: dict[str, float] = {}
    for (schema, table), production_rows in targets.items():
        qualified = f"{schema}.{table}"
        table_tuples = connection.execute(
            "SELECT reltuples FROM pg_class WHERE oid = %s::regclass", (qualified,),
        ).fetchone()[0]
        assert table_tuples > 0, qualified
        relations = connection.execute(
            """
            SELECT c.relname, c.reltuples, pg_relation_size(c.oid) / current_setting('block_size')::int
              FROM pg_class c
             WHERE c.oid = %s::regclass
                OR c.oid IN (SELECT indexrelid FROM pg_index WHERE indrelid = %s::regclass)
            """,
            (qualified, qualified),
        ).fetchall()
        for relname, tuples, pages in relations:
            scaled = round(production_rows * float(tuples) / float(table_tuples))
            connection.execute(
                "SELECT pg_restore_relation_stats('schemaname', %s::text, 'relname', %s::text, "
                "'relpages', %s::integer, 'reltuples', %s::real, 'relallvisible', %s::integer)",
                (schema, relname, int(pages), float(scaled), int(pages) if relname == table else 0),
            )
            imported[relname] = float(scaled)
    for table in ("current_derived_generation", "derived_generation", "derived_product"):
        connection.execute(f"ANALYZE v3_meta.{table}")
    return imported


def _prepared(connection, statement_sql: str, params: list[Any], prefix: str):
    """Run ``prefix EXECUTE`` of one builder statement with its parameters.

    The builder writes asyncpg placeholders ($1, $2, ...) and reuses them, so
    the statement is prepared as written and executed with literals."""
    name = sql.Identifier("finder_seed_statement")
    connection.execute(sql.SQL("PREPARE {} AS ").format(name) + sql.SQL(statement_sql))
    try:
        if params:
            literals = sql.SQL(", ").join(sql.Literal(value) for value in params)
            execute = sql.SQL(prefix + "EXECUTE {} ({})").format(name, literals)
        else:
            execute = sql.SQL(prefix + "EXECUTE {}").format(name)
        return connection.execute(execute).fetchall()
    finally:
        connection.execute(sql.SQL("DEALLOCATE {}").format(name))


def run_statement(connection, statement_sql: str, params: list[Any]) -> list[tuple]:
    """Rows of one builder statement."""
    return _prepared(connection, statement_sql, params, "")


def generic_plan(connection, statement_sql: str, params: list[Any]) -> dict[str, Any]:
    """The generic plan PostgreSQL uses for one builder statement, with the
    production planner settings, as the API's prepared statements get."""
    with connection.transaction(force_rollback=True):
        for name, value in PRODUCTION_PLANNER_SETTINGS.items():
            connection.execute("SELECT set_config(%s, %s, true)", (name, value))
        connection.execute("SET LOCAL plan_cache_mode = 'force_generic_plan'")
        rows = _prepared(connection, statement_sql, params, "EXPLAIN (FORMAT JSON) ")
    value = rows[0][0]
    document = json.loads(value) if isinstance(value, str) else value
    return document[0]["Plan"]


def plan_nodes(node: dict[str, Any]):
    yield node
    for child in node.get("Plans") or []:
        yield from plan_nodes(child)
