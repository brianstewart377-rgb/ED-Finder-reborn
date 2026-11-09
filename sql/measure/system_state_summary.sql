-- Read-only summary of what is in v3_state after a load.
-- Changes nothing: the whole script runs in a READ ONLY transaction.
\pset pager off
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5min';

\echo '=== 1. Loads so far (newest first) ==='
SELECT load_id, to_char(loaded_at, 'YYYY-MM-DD HH24:MI') AS loaded_at, source,
       full_snapshot AS snapshot, staged_systems, systems_written AS written,
       systems_unchanged AS unchanged, systems_removed AS removed,
       to_char(newest_record_at, 'YYYY-MM-DD HH24:MI') AS newest_record
  FROM v3_state.load_log
 ORDER BY load_id DESC
 LIMIT 5;

\echo '=== 2. How many systems of each kind ==='
SELECT count(*)                                                        AS stored_systems,
       count(*) FILTER (WHERE population > 0)                          AS inhabited,
       count(*) FILTER (WHERE population = 0)                          AS no_population,
       count(*) FILTER (WHERE construction_site_count > 0)             AS with_construction_site,
       count(*) FILTER (WHERE colonisation_ship_present)               AS with_colonisation_ship,
       count(*) FILTER (WHERE population = 0
                          AND (construction_site_count > 0
                               OR colonisation_ship_present))          AS no_population_being_colonised,
       count(*) FILTER (WHERE population > 0
                          AND (construction_site_count > 0
                               OR colonisation_ship_present))          AS inhabited_still_building
  FROM v3_state.system_state;

\echo '=== 3. Station types (fleet carriers left out) ==='
SELECT t.key AS station_type,
       sum(t.value::int) AS stations,
       count(*) AS systems,
       count(*) FILTER (WHERE s.population = 0) AS systems_without_population
  FROM v3_state.system_state s
 CROSS JOIN LATERAL jsonb_each_text(s.station_type_counts) AS t(key, value)
 GROUP BY t.key
 ORDER BY sum(t.value::int) DESC;

\echo '=== 4. Systems with no population: which station types they have ==='
SELECT (SELECT string_agg(k, ' + ' ORDER BY k)
          FROM jsonb_object_keys(s.station_type_counts) AS k) AS station_types_present,
       s.colonisation_ship_present AS colonisation_ship,
       count(*) AS systems
  FROM v3_state.system_state s
 WHERE s.population = 0
 GROUP BY 1, 2
 ORDER BY count(*) DESC
 LIMIT 25;

\echo '=== 5. How fresh the records are ==='
SELECT count(*) FILTER (WHERE system_updated_at >= now() - interval '7 days')   AS updated_last_7_days,
       count(*) FILTER (WHERE system_updated_at >= now() - interval '30 days')  AS updated_last_30_days,
       count(*) FILTER (WHERE system_updated_at >= now() - interval '365 days') AS updated_last_year,
       count(*) AS all_systems,
       to_char(min(system_updated_at), 'YYYY-MM-DD') AS oldest,
       to_char(max(system_updated_at), 'YYYY-MM-DD HH24:MI') AS newest
  FROM v3_state.system_state;

\echo '=== 6. Factions and powers ==='
SELECT (SELECT count(*) FROM v3_state.system_faction)                     AS faction_rows,
       (SELECT count(DISTINCT faction_name) FROM v3_state.system_faction) AS different_factions,
       (SELECT count(*) FROM v3_state.system_power)                       AS power_rows,
       (SELECT count(DISTINCT power_name) FROM v3_state.system_power)     AS different_powers;

\echo '=== 7. Size on disk ==='
SELECT c.relname AS table_name, pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'v3_state' AND c.relkind = 'r'
 ORDER BY pg_total_relation_size(c.oid) DESC;

COMMIT;
