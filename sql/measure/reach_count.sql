-- Read-only measurement for the move to the Pi:
--   1-2. table and schema sizes in the current database;
--   3.   how many known systems lie within 250 ly and 500 ly of an inhabited
--        system (one with a non-carrier station), measured on the 80 ly
--        density cells. Tested on made-up data: within about 5% of an exact count.
-- Changes nothing: the whole script runs in a READ ONLY transaction.

\pset pager off
BEGIN READ ONLY;
SET LOCAL statement_timeout = '30min';

SELECT cg.relation_schema AS gen
  FROM v3_meta.current_canonical_generation c
  JOIN v3_meta.canonical_generation cg USING (generation_id) \gset
SELECT spatial_generation_id AS sg FROM v3_spatial.current_spatial_generation \gset

\echo '=== 1. Biggest tables (data + indexes) ==='
SELECT n.nspname || '.' || c.relname AS table_name,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p', 'm')
   AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
 ORDER BY pg_total_relation_size(c.oid) DESC
 LIMIT 30;

\echo '=== 2. Size by schema ==='
SELECT n.nspname AS schema_name,
       pg_size_pretty(sum(pg_total_relation_size(c.oid))) AS total_size
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p', 'm')
   AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
 GROUP BY n.nspname
 ORDER BY sum(pg_total_relation_size(c.oid)) DESC;

\echo '=== 3. Known systems within 250 ly and 500 ly of an inhabited system (one with a non-carrier station). Rough: measured on 80 ly cells ==='
WITH anchor_cell AS (
    SELECT DISTINCT floor(s.x_ly / 80)::bigint AS ix,
                    floor(s.y_ly / 80)::bigint AS iy,
                    floor(s.z_ly / 80)::bigint AS iz
      FROM :"gen".stations st
      JOIN :"gen".systems s ON s.id64 = st.system_id64
     WHERE NOT st.is_fleet_carrier
       AND st.lifecycle_state = 'ACTIVE'
       AND s.lifecycle_state = 'ACTIVE'
), offsets AS (
    SELECT dx, dy, dz, sqrt(dx*dx + dy*dy + dz*dz) * 80 AS dist_ly
      FROM generate_series(-7, 7) dx, generate_series(-7, 7) dy, generate_series(-7, 7) dz
     WHERE sqrt(dx*dx + dy*dy + dz*dz) * 80 <= 500
), reach AS (
    SELECT (a.ix + o.dx) || '.' || (a.iy + o.dy) || '.' || (a.iz + o.dz) AS cell_key,
           min(o.dist_ly) AS nearest_ly
      FROM anchor_cell a CROSS JOIN offsets o
     GROUP BY 1
)
SELECT (SELECT count(*) FROM anchor_cell)                                AS inhabited_80ly_cells,
       coalesce(sum(cs.system_count) FILTER (WHERE r.nearest_ly <= 250), 0) AS systems_within_250,
       coalesce(sum(cs.system_count), 0)                                 AS systems_within_500,
       (SELECT sum(system_count) FROM v3_spatial.cell_summary
         WHERE spatial_generation_id = :'sg' AND level = 5)               AS systems_in_galaxy
  FROM reach r
  JOIN v3_spatial.cell_summary cs
    ON cs.spatial_generation_id = :'sg' AND cs.level = 5 AND cs.cell_key = r.cell_key;

COMMIT;
