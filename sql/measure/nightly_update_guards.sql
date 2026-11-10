-- Read-only. Shows, for the five tables the nightly update writes to, each
-- guard and whether it carries the nightly-update condition (migration 025).
--
-- Before 025: every line says "no".
-- After 025:  the five TRUNCATE guards say "never (TRUNCATE)", the other 18
--             say "yes".
SELECT c.relname AS on_table,
       t.tgname AS guard,
       CASE
           WHEN position('TRUNCATE' IN pg_get_triggerdef(t.oid)) > 0 THEN 'never (TRUNCATE)'
           WHEN position('edfinder.nightly_update' IN pg_get_triggerdef(t.oid)) > 0 THEN 'yes'
           ELSE 'no'
       END AS skipped_for_the_nightly_update
  FROM pg_trigger t
  JOIN pg_class c ON c.oid = t.tgrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE NOT t.tgisinternal
   AND n.nspname = 'v3_derived'
   AND c.relname IN ('system_rating_vector', 'body_mechanics', 'economy_opportunity',
                     'system_search', 'system_archetype')
 ORDER BY 1, 2;
