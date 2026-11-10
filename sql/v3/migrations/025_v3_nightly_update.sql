-- 025: let the nightly jobs update published scores and Find rows.
--
-- Until now a published set of scores could never change. Once a generation is
-- published, a statement trigger on each table refuses every INSERT, UPDATE and
-- DELETE. That suited whole rebuilds. The galaxy data now changes every night,
-- so the nightly job has to replace the rows of the systems it re-scored.
--
-- The rules themselves are not touched: no guard function changes. Each
-- INSERT / UPDATE / DELETE guard on the five tables below gains one condition.
-- It is skipped inside a transaction that has announced itself as the nightly
-- update:
--
--     SET LOCAL edfinder.nightly_update = 'on';
--
-- The announcement ends with that transaction. Every other write is refused
-- exactly as before, and TRUNCATE stays refused for everyone.
--
-- This is a seatbelt, not a lock: any session that can write to these tables
-- can make the announcement. It stops accidents, which is what the old rule
-- was for.
--
-- Safe to run twice: the second run changes nothing.
-- Swapping a trigger needs a brief exclusive lock on its table. With
-- lock_timeout the migration gives up instead of queueing behind a long query;
-- nothing is changed in that case, so it can simply be run again.

BEGIN;

SET LOCAL lock_timeout = '15s';

DO $nightly$
DECLARE
    tables constant text[] := ARRAY[
        'system_rating_vector', 'body_mechanics', 'economy_opportunity',
        'system_search', 'system_archetype'
    ];
    marker constant text := 'edfinder.nightly_update';
    condition constant text :=
        $c$(current_setting('edfinder.nightly_update', true) IS DISTINCT FROM 'on')$c$;
    statement_guard constant text := ' FOR EACH STATEMENT EXECUTE FUNCTION ';
    name_ text;
    target record;
    remaining integer;
BEGIN
    FOREACH name_ IN ARRAY tables LOOP
        IF to_regclass('v3_derived.' || name_) IS NULL THEN
            RAISE EXCEPTION 'v3_derived.% does not exist', name_;
        END IF;
    END LOOP;

    FOR target IN
        SELECT t.tgname,
               format('%I.%I', n.nspname, c.relname) AS relation,
               pg_get_triggerdef(t.oid) AS definition
          FROM pg_trigger t
          JOIN pg_class c ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE NOT t.tgisinternal
           AND n.nspname = 'v3_derived'
           AND c.relname = ANY (tables)
         ORDER BY c.relname, t.tgname
    LOOP
        -- TRUNCATE is never opened up.
        CONTINUE WHEN position('TRUNCATE' IN target.definition) > 0;
        -- Already done by an earlier run.
        CONTINUE WHEN position(marker IN target.definition) > 0;
        IF position(statement_guard IN target.definition) = 0
           OR position(' WHEN (' IN target.definition) > 0 THEN
            RAISE EXCEPTION 'trigger % on % is not a plain statement guard: %',
                target.tgname, target.relation, target.definition;
        END IF;
        EXECUTE format('DROP TRIGGER %I ON %s', target.tgname, target.relation);
        EXECUTE replace(
            target.definition,
            statement_guard,
            ' FOR EACH STATEMENT WHEN ' || condition || ' EXECUTE FUNCTION '
        );
    END LOOP;

    -- Every write guard on the five tables must now carry the condition.
    SELECT count(*) INTO remaining
      FROM pg_trigger t
      JOIN pg_class c ON c.oid = t.tgrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE NOT t.tgisinternal
       AND n.nspname = 'v3_derived'
       AND c.relname = ANY (tables)
       AND position('TRUNCATE' IN pg_get_triggerdef(t.oid)) = 0
       AND position(marker IN pg_get_triggerdef(t.oid)) = 0;
    IF remaining <> 0 THEN
        RAISE EXCEPTION '% write guards were left without the nightly condition', remaining;
    END IF;
END
$nightly$;

COMMIT;
