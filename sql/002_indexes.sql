-- =============================================================================
-- ED Finder — Indexes
-- Version: 2.0
--
-- RUN THIS AFTER BULK IMPORT IS COMPLETE — not before.
--
-- Changes in v2.0:
--   • Added galaxy_region_id indexes (region filter + region-aware cluster search)
--   • Added macro_grid_id index on systems
--   • Added needs_permit partial index
--   • Added main_star_class index (generated column, same as main_star_type)
--   • Added macro_grid table indexes
--   • Added import_errors indexes
--   • Added cluster_summary.macro_grid_id index
--   • Added ratings component indexes (slots, body_quality, etc.)
--
-- Estimated build time on 186M systems + 800M bodies (NVMe):
--   systems indexes:  ~30-50 minutes
--   bodies indexes:   ~2-4 hours
--   ratings indexes:  ~10-20 minutes
--   cluster indexes:  ~5-10 minutes
--   Total:            ~3-5 hours
-- =============================================================================

DO $$ BEGIN RAISE NOTICE 'Building indexes — this will take several hours. Monitor with:
SELECT phase, blocks_done, blocks_total, tuples_done, tuples_total
FROM pg_stat_progress_create_index;'; END $$;

-- =============================================================================
-- SYSTEMS TABLE INDEXES
-- =============================================================================

-- CRITICAL for build_grid.py resume detection
CREATE INDEX IF NOT EXISTS idx_sys_grid_null
    ON systems(id64)
    WHERE grid_cell_id IS NULL;

-- CRITICAL for build_grid.py macro-grid resume detection
CREATE INDEX IF NOT EXISTS idx_sys_macro_null
    ON systems(id64)
    WHERE macro_grid_id IS NULL;

-- Primary spatial indexes
CREATE INDEX IF NOT EXISTS idx_sys_x ON systems(x);
CREATE INDEX IF NOT EXISTS idx_sys_y ON systems(y);
CREATE INDEX IF NOT EXISTS idx_sys_z ON systems(z);
CREATE INDEX IF NOT EXISTS idx_sys_coords ON systems(x, y, z);

-- Spatial grid cell
CREATE INDEX IF NOT EXISTS idx_sys_grid ON systems(grid_cell_id) WHERE grid_cell_id IS NOT NULL;

-- Macro-grid cell (cluster builder unit)
CREATE INDEX IF NOT EXISTS idx_sys_macro ON systems(macro_grid_id) WHERE macro_grid_id IS NOT NULL;

-- Galaxy region (named ED region filter)
CREATE INDEX IF NOT EXISTS idx_sys_region ON systems(galaxy_region_id) WHERE galaxy_region_id IS NOT NULL;

-- Economy searches
CREATE INDEX IF NOT EXISTS idx_sys_primary_economy ON systems(primary_economy);
CREATE INDEX IF NOT EXISTS idx_sys_secondary_economy ON systems(secondary_economy);

-- Colonisation state
CREATE INDEX IF NOT EXISTS idx_sys_colonised ON systems(is_colonised);
CREATE INDEX IF NOT EXISTS idx_sys_population ON systems(population);

-- Permit filter (small partial index — only permit-locked systems)
CREATE INDEX IF NOT EXISTS idx_sys_permit ON systems(needs_permit) WHERE needs_permit = TRUE;

-- Compound: uncolonised + economy
CREATE INDEX IF NOT EXISTS idx_sys_econ_pop
    ON systems(primary_economy, population)
    WHERE population = 0;

-- Compound: uncolonised within grid cell
CREATE INDEX IF NOT EXISTS idx_sys_grid_pop
    ON systems(grid_cell_id, population)
    WHERE population = 0 AND grid_cell_id IS NOT NULL;

-- Compound: uncolonised within macro-grid cell (cluster builder)
CREATE INDEX IF NOT EXISTS idx_sys_macro_pop
    ON systems(macro_grid_id, population)
    WHERE population = 0 AND macro_grid_id IS NOT NULL;

-- Compound: region + uncolonised (region-aware cluster search)
CREATE INDEX IF NOT EXISTS idx_sys_region_pop
    ON systems(galaxy_region_id, population)
    WHERE population = 0 AND galaxy_region_id IS NOT NULL;

-- Name search
CREATE INDEX IF NOT EXISTS idx_sys_name ON systems(name);
CREATE INDEX IF NOT EXISTS idx_sys_name_trgm ON systems USING gin(name gin_trgm_ops);

-- Data quality / dirty flags
CREATE INDEX IF NOT EXISTS idx_sys_has_bodies ON systems(has_body_data) WHERE has_body_data = TRUE;
CREATE INDEX IF NOT EXISTS idx_sys_rating_dirty ON systems(id64) WHERE rating_dirty = TRUE;
CREATE INDEX IF NOT EXISTS idx_sys_cluster_dirty ON systems(cluster_dirty) WHERE cluster_dirty = TRUE;

-- EDDN update tracking
CREATE INDEX IF NOT EXISTS idx_sys_updated_at ON systems(updated_at);
CREATE INDEX IF NOT EXISTS idx_sys_eddn_updated ON systems(eddn_updated_at) WHERE eddn_updated_at IS NOT NULL;

-- Star type quick filter
CREATE INDEX IF NOT EXISTS idx_sys_main_star ON systems(main_star_type) WHERE main_star_type IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sys_main_star_class ON systems(main_star_class) WHERE main_star_class IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sys_scoopable ON systems(main_star_is_scoopable) WHERE main_star_is_scoopable = TRUE;

-- =============================================================================
-- BODIES TABLE INDEXES
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_body_system ON bodies(system_id64);
CREATE INDEX IF NOT EXISTS idx_body_subtype ON bodies(subtype) WHERE subtype IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_body_type ON bodies(body_type);

CREATE INDEX IF NOT EXISTS idx_body_elw        ON bodies(system_id64) WHERE is_earth_like = TRUE;
CREATE INDEX IF NOT EXISTS idx_body_ww         ON bodies(system_id64) WHERE is_water_world = TRUE;
CREATE INDEX IF NOT EXISTS idx_body_ammonia    ON bodies(system_id64) WHERE is_ammonia_world = TRUE;
CREATE INDEX IF NOT EXISTS idx_body_terraformable ON bodies(system_id64) WHERE is_terraformable = TRUE;
CREATE INDEX IF NOT EXISTS idx_body_landable   ON bodies(system_id64) WHERE is_landable = TRUE;
CREATE INDEX IF NOT EXISTS idx_body_main_star  ON bodies(system_id64) WHERE is_main_star = TRUE;

CREATE INDEX IF NOT EXISTS idx_body_bio_signals
    ON bodies(system_id64, bio_signal_count)
    WHERE bio_signal_count > 0;

CREATE INDEX IF NOT EXISTS idx_body_geo_signals
    ON bodies(system_id64, geo_signal_count)
    WHERE geo_signal_count > 0;

CREATE INDEX IF NOT EXISTS idx_body_scan_value
    ON bodies(estimated_scan_value DESC NULLS LAST)
    WHERE estimated_scan_value IS NOT NULL;

-- =============================================================================
-- STATIONS TABLE INDEXES
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_sta_system ON stations(system_id64);
CREATE INDEX IF NOT EXISTS idx_sta_type ON stations(station_type);
CREATE INDEX IF NOT EXISTS idx_sta_economy ON stations(primary_economy);
CREATE INDEX IF NOT EXISTS idx_sta_name ON stations(name);
CREATE INDEX IF NOT EXISTS idx_sta_name_trgm ON stations USING gin(name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_sta_large_pad ON stations(system_id64) WHERE landing_pad_size = 'L';
CREATE INDEX IF NOT EXISTS idx_sta_shipyard ON stations(system_id64) WHERE has_shipyard = TRUE;
CREATE INDEX IF NOT EXISTS idx_sta_outfitting ON stations(system_id64) WHERE has_outfitting = TRUE;

-- =============================================================================
-- ATTRACTIONS TABLE INDEXES
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_attr_system ON attractions(system_id64);
CREATE INDEX IF NOT EXISTS idx_attr_body ON attractions(body_id) WHERE body_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_attr_type ON attractions(attraction_type);
CREATE INDEX IF NOT EXISTS idx_attr_subtype ON attractions(subtype) WHERE subtype IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_attr_genus ON attractions(genus) WHERE genus IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_attr_species ON attractions(species) WHERE species IS NOT NULL;

-- =============================================================================
-- RATINGS TABLE INDEXES
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_rat_score ON ratings(score DESC NULLS LAST)
    WHERE score IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rat_agriculture ON ratings(score_agriculture DESC NULLS LAST)
    WHERE score_agriculture IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rat_refinery ON ratings(score_refinery DESC NULLS LAST)
    WHERE score_refinery IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rat_industrial ON ratings(score_industrial DESC NULLS LAST)
    WHERE score_industrial IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rat_hightech ON ratings(score_hightech DESC NULLS LAST)
    WHERE score_hightech IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rat_military ON ratings(score_military DESC NULLS LAST)
    WHERE score_military IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rat_tourism ON ratings(score_tourism DESC NULLS LAST)
    WHERE score_tourism IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rat_econ_score
    ON ratings(economy_suggestion, score DESC NULLS LAST)
    WHERE score IS NOT NULL;

-- Body count filters
CREATE INDEX IF NOT EXISTS idx_rat_elw ON ratings(elw_count) WHERE elw_count > 0;
CREATE INDEX IF NOT EXISTS idx_rat_ammonia ON ratings(ammonia_count) WHERE ammonia_count > 0;
CREATE INDEX IF NOT EXISTS idx_rat_gas_giant ON ratings(gas_giant_count) WHERE gas_giant_count > 0;
CREATE INDEX IF NOT EXISTS idx_rat_bio ON ratings(bio_signal_total) WHERE bio_signal_total > 0;
CREATE INDEX IF NOT EXISTS idx_rat_geo ON ratings(geo_signal_total) WHERE geo_signal_total > 0;
CREATE INDEX IF NOT EXISTS idx_rat_terraformable ON ratings(terraformable_count) WHERE terraformable_count > 0;
CREATE INDEX IF NOT EXISTS idx_rat_neutron ON ratings(neutron_count) WHERE neutron_count > 0;

-- Score component indexes (v2.0)
CREATE INDEX IF NOT EXISTS idx_rat_slots ON ratings(slots DESC NULLS LAST) WHERE slots IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rat_body_quality ON ratings(body_quality DESC NULLS LAST) WHERE body_quality IS NOT NULL;

-- Dirty flag
CREATE INDEX IF NOT EXISTS idx_rat_dirty ON ratings(system_id64);

-- =============================================================================
-- SPATIAL_GRID TABLE INDEXES
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_grid_coords ON spatial_grid(cell_x, cell_y, cell_z);

-- =============================================================================
-- MACRO_GRID TABLE INDEXES
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_macro_coords ON macro_grid(cell_x, cell_y, cell_z);
CREATE INDEX IF NOT EXISTS idx_macro_anchor_count ON macro_grid(anchor_count DESC) WHERE anchor_count > 0;

-- =============================================================================
-- CLUSTER_SUMMARY TABLE INDEXES
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_clu_coverage ON cluster_summary(coverage_score DESC NULLS LAST)
    WHERE coverage_score IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_clu_diversity ON cluster_summary(economy_diversity DESC);
CREATE INDEX IF NOT EXISTS idx_clu_viable ON cluster_summary(total_viable DESC);
CREATE INDEX IF NOT EXISTS idx_clu_macro ON cluster_summary(macro_grid_id) WHERE macro_grid_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_clu_agriculture ON cluster_summary(agriculture_count DESC) WHERE agriculture_count > 0;
CREATE INDEX IF NOT EXISTS idx_clu_refinery    ON cluster_summary(refinery_count DESC)    WHERE refinery_count > 0;
CREATE INDEX IF NOT EXISTS idx_clu_industrial  ON cluster_summary(industrial_count DESC)  WHERE industrial_count > 0;
CREATE INDEX IF NOT EXISTS idx_clu_hightech    ON cluster_summary(hightech_count DESC)    WHERE hightech_count > 0;
CREATE INDEX IF NOT EXISTS idx_clu_military    ON cluster_summary(military_count DESC)    WHERE military_count > 0;
CREATE INDEX IF NOT EXISTS idx_clu_tourism     ON cluster_summary(tourism_count DESC)     WHERE tourism_count > 0;

CREATE INDEX IF NOT EXISTS idx_clu_dirty ON cluster_summary(system_id64) WHERE dirty = TRUE;

-- =============================================================================
-- WATCHLIST / NOTES / CACHE INDEXES
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_wl_system    ON watchlist(system_id64);
CREATE INDEX IF NOT EXISTS idx_wl_added     ON watchlist(added_at DESC);
CREATE INDEX IF NOT EXISTS idx_wl_alert_score ON watchlist(alert_min_score) WHERE alert_min_score IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wllog_system ON watchlist_changelog(system_id64);
CREATE INDEX IF NOT EXISTS idx_wllog_det    ON watchlist_changelog(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_cache_exp    ON api_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_eddn_recv    ON eddn_log(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_eddn_proc    ON eddn_log(processed) WHERE processed = FALSE;
CREATE INDEX IF NOT EXISTS idx_eddn_system  ON eddn_log(system_id64) WHERE system_id64 IS NOT NULL;

-- =============================================================================
-- IMPORT_ERRORS INDEXES
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_ierr_dump    ON import_errors(dump_file);
CREATE INDEX IF NOT EXISTS idx_ierr_type    ON import_errors(record_type);
CREATE INDEX IF NOT EXISTS idx_ierr_class   ON import_errors(error_class);
CREATE INDEX IF NOT EXISTS idx_ierr_time    ON import_errors(occurred_at DESC);

-- =============================================================================
-- POST-INDEX STATISTICS UPDATE
-- =============================================================================
ANALYZE systems;
ANALYZE bodies;
ANALYZE ratings;
ANALYZE cluster_summary;
ANALYZE spatial_grid;
ANALYZE macro_grid;
ANALYZE stations;
ANALYZE attractions;

DO $$ BEGIN RAISE NOTICE 'All indexes built successfully.'; END $$;
