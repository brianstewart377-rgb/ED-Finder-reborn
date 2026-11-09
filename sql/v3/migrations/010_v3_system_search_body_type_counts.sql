-- sql/v3/migrations/010_v3_system_search_body_type_counts.sql
-- Adds per-body-type COUNT columns to v3_derived.system_search for Finder
-- body-composition sliders. Additive only; does not touch canonical relations.
--
-- The bounded production runner has COMMAND_TIMEOUT=120s, while validating
-- inline CHECK constraints would scan roughly 198.5M existing rows under
-- ACCESS EXCLUSIVE. Defer their initial validation so application is bounded;
-- they still enforce every subsequent INSERT and UPDATE. Existing rows satisfy
-- them by construction because the new columns default to 0 and migration 004
-- enforces landable_count >= 0. VALIDATE CONSTRAINT is a separate governed
-- operation in Finder rollout step 3.
BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

ALTER TABLE v3_derived.system_search
    ADD COLUMN elw_count            integer NOT NULL DEFAULT 0,
    ADD COLUMN ww_count             integer NOT NULL DEFAULT 0,
    ADD COLUMN ammonia_count        integer NOT NULL DEFAULT 0,
    ADD COLUMN terraformable_count  integer NOT NULL DEFAULT 0,
    ADD COLUMN gas_giant_count      integer NOT NULL DEFAULT 0,
    ADD COLUMN hmc_count            integer NOT NULL DEFAULT 0,
    ADD COLUMN metal_rich_count     integer NOT NULL DEFAULT 0,
    ADD COLUMN rocky_count          integer NOT NULL DEFAULT 0,
    ADD COLUMN rocky_ice_count      integer NOT NULL DEFAULT 0,
    ADD COLUMN icy_count            integer NOT NULL DEFAULT 0,
    ADD COLUMN black_hole_count     integer NOT NULL DEFAULT 0,
    ADD COLUMN neutron_count        integer NOT NULL DEFAULT 0,
    ADD COLUMN white_dwarf_count    integer NOT NULL DEFAULT 0,
    ADD COLUMN other_star_count     integer NOT NULL DEFAULT 0,
    ADD COLUMN ring_count           integer NOT NULL DEFAULT 0,
    ADD COLUMN walkable_count       integer NOT NULL DEFAULT 0,
    ADD COLUMN bio_signal_total     integer NOT NULL DEFAULT 0,
    ADD COLUMN geo_signal_total     integer NOT NULL DEFAULT 0;

ALTER TABLE v3_derived.system_search
    ADD CONSTRAINT system_search_elw_count_check
        CHECK(elw_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_ww_count_check
        CHECK(ww_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_ammonia_count_check
        CHECK(ammonia_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_terraformable_count_check
        CHECK(terraformable_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_gas_giant_count_check
        CHECK(gas_giant_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_hmc_count_check
        CHECK(hmc_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_metal_rich_count_check
        CHECK(metal_rich_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_rocky_count_check
        CHECK(rocky_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_rocky_ice_count_check
        CHECK(rocky_ice_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_icy_count_check
        CHECK(icy_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_black_hole_count_check
        CHECK(black_hole_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_neutron_count_check
        CHECK(neutron_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_white_dwarf_count_check
        CHECK(white_dwarf_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_other_star_count_check
        CHECK(other_star_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_ring_count_check
        CHECK(ring_count >= 0) NOT VALID,
    ADD CONSTRAINT system_search_walkable_count_check
        CHECK(walkable_count BETWEEN 0 AND landable_count) NOT VALID,
    ADD CONSTRAINT system_search_bio_signal_total_check
        CHECK(bio_signal_total >= 0) NOT VALID,
    ADD CONSTRAINT system_search_geo_signal_total_check
        CHECK(geo_signal_total >= 0) NOT VALID;

-- v3_app.system_search is SELECT s.*; recreate so new columns are exposed.
CREATE OR REPLACE VIEW v3_app.system_search AS
SELECT s.* FROM v3_meta.current_derived_generation c
JOIN v3_derived.system_search s USING(derived_generation_id);

COMMIT;
