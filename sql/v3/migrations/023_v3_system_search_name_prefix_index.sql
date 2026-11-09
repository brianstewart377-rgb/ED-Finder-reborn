-- Prefix autocomplete for the published V3 Search generation. A default-opclass
-- lower(name) btree cannot support LIKE 'prefix%' under en_US.utf8; retain it
-- for its existing consumers and add the pattern-operator index separately.
--
-- Governed apply is transactional, including its migration-ledger row. A
-- non-concurrent build takes SHARE on this derived table: ordinary reads
-- continue, while generation builds/purges must wait. No canonical relation is
-- changed. Build every retained generation, not only the published ~198.5M
-- rows. Planning estimate for one such generation: 1-3 hours, 12-20 GiB final
-- index and 60 GiB additional free space for index, sort and WAL headroom;
-- scale with all retained rows and review actual capacity before apply.
-- These are estimates, not a production benchmark (see the migration runbook).
-- The governed runner gives only this migration 245 minutes and its apply job
-- 270 minutes, so PostgreSQL's four-hour bound expires before either wrapper.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '4h';

CREATE INDEX IF NOT EXISTS system_search_name_prefix
    ON v3_derived.system_search (derived_generation_id, lower(name) text_pattern_ops);

-- Reapplication is harmless only when the existing index is the intended one;
-- IF NOT EXISTS alone would silently accept a same-name incompatible object.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_index i
        JOIN pg_catalog.pg_class c ON c.oid = i.indexrelid
        JOIN pg_catalog.pg_am am ON am.oid = c.relam
        JOIN pg_catalog.pg_opclass op ON op.oid = i.indclass[1]
        WHERE i.indexrelid = 'v3_derived.system_search_name_prefix'::regclass
          AND i.indrelid = 'v3_derived.system_search'::regclass
          AND am.amname = 'btree'
          AND i.indisvalid AND i.indisready AND NOT i.indisunique
          AND i.indnkeyatts = 2 AND i.indnatts = 2
          AND i.indpred IS NULL
          AND pg_catalog.pg_get_indexdef(i.indexrelid, 1, true) = 'derived_generation_id'
          AND pg_catalog.pg_get_expr(i.indexprs, i.indrelid) = 'lower(name)'
          AND i.indcollation[1] = (
              SELECT attcollation FROM pg_catalog.pg_attribute
              WHERE attrelid = i.indrelid AND attname = 'name'
          )
          AND op.opcnamespace = 'pg_catalog'::regnamespace
          AND op.opcname = 'text_pattern_ops'
    ) THEN
        RAISE EXCEPTION 'system_search_name_prefix has an incompatible definition';
    END IF;
END $$;

COMMIT;
