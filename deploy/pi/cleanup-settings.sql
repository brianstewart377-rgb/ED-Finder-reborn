-- Clean-up settings for the big tables, for the Raspberry Pi.
--
-- PostgreSQL clears away the old versions of rows that were changed or
-- deleted (VACUUM), and refreshes the statistics its planner uses (ANALYZE),
-- by itself ("autovacuum"). By default it does that once a fifth of a table
-- has changed: tens of millions of rows on the galaxy and score tables. The
-- nightly galaxy update leaves about 1 GB of old row versions behind each
-- night, so with the default the clean-up would not start for weeks, and the
-- full galaxy file will leave far more (docs/pi-move.md, section 2).
--
-- These settings start it after a million changed rows instead (two million
-- for the statistics), whatever the table's size, on the eleven tables that
-- have more than ten million rows. The rest keep the default, which suits a
-- small table.
--
-- Only settings change: no rows and no structure. Each table's takes a moment
-- and does not stop the app reading or writing it. Safe to run more than
-- once. To undo one table's: ALTER TABLE <table> RESET (<setting>, ...).
--
-- Run on the Pi, as the user pi, once the copy's checks (copy_to_pi.py
-- validate) have finished:
--   docker exec -i edfinder-v3-phase4c-full-20260827_r5-postgres psql -X -v ON_ERROR_STOP=1 -U edfinder_v3 -d edfinder_v3_phase4c_full_20260827_r5 < /opt/edfinder/cleanup-settings.sql
BEGIN;

-- If something holds one of these tables (the copy's checks, say), give up
-- after a few seconds rather than wait; run it again later.
SET LOCAL lock_timeout = '15s';

DO $settings$
DECLARE
    galaxy text;
    item text;
    settings constant text :=
        'autovacuum_enabled = on, '
        'autovacuum_vacuum_scale_factor = 0, autovacuum_vacuum_threshold = 1000000, '
        'autovacuum_vacuum_insert_scale_factor = 0, autovacuum_vacuum_insert_threshold = 1000000, '
        'autovacuum_analyze_scale_factor = 0, autovacuum_analyze_threshold = 2000000';
BEGIN
    SELECT g.relation_schema INTO STRICT galaxy
      FROM v3_meta.current_canonical_generation c
      JOIN v3_meta.canonical_generation g USING (generation_id);
    FOREACH item IN ARRAY ARRAY['bodies', 'body_composition', 'systems', 'rings',
                                'body_signal_current', 'station_service_current'] LOOP
        EXECUTE format('ALTER TABLE %I.%I SET (%s)', galaxy, item, settings);
    END LOOP;
    FOREACH item IN ARRAY ARRAY['economy_opportunity', 'body_mechanics', 'system_search',
                                'system_rating_vector', 'system_archetype'] LOOP
        EXECUTE format('ALTER TABLE v3_derived.%I SET (%s)', item, settings);
    END LOOP;
    RAISE NOTICE 'Clean-up settings made on 11 tables (galaxy %).', galaxy;
END
$settings$;

COMMIT;
