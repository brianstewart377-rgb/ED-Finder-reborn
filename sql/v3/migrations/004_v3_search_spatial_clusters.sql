-- V3 search projection, multi-resolution spatial pyramid and cluster products.
--
-- Everything here is derived, generation-scoped and rebuildable. Exact
-- coordinates remain the search truth; grid cells exist only for aggregation and
-- level-of-detail, and clusters are domain analysis rather than spatial truth.
--
-- The compatibility decision calls for PostgreSQL cube plus GiST rather than
-- PostGIS, which is not available in this image. Verified before writing this
-- migration on the retained production container: cube is available and
-- not yet installed, postgis is unavailable, and edfinder_v3 is superuser.
--
-- Application through production requires the separately reviewed V3 migration
-- operation, exactly as 003 does.
BEGIN;

CREATE EXTENSION IF NOT EXISTS cube;
CREATE SCHEMA v3_spatial;

-- Hot Finder/Explore/Inspect facts only. Heavy judgement and domain outputs stay
-- in their own relations; anything derivable but cold is joined on demand rather
-- than copied here.
CREATE TABLE v3_derived.system_search (
    derived_generation_id uuid NOT NULL REFERENCES v3_meta.derived_generation,
    system_id64 bigint NOT NULL CHECK(system_id64 >= 0),
    name text NOT NULL CHECK(length(name) BETWEEN 1 AND 256),
    x_ly double precision NOT NULL,
    y_ly double precision NOT NULL,
    z_ly double precision NOT NULL,
    position_ly cube NOT NULL,
    galaxy_region_id integer CHECK(galaxy_region_id >= 0),
    region_name text CHECK(length(region_name) BETWEEN 1 AND 128),
    main_star_class text CHECK(length(main_star_class) BETWEEN 1 AND 128),
    body_count integer NOT NULL CHECK(body_count >= 0),
    landable_count integer NOT NULL CHECK(landable_count BETWEEN 0 AND body_count),
    station_count integer NOT NULL CHECK(station_count >= 0),
    has_rings boolean NOT NULL,
    has_biologicals boolean NOT NULL,
    has_geologicals boolean NOT NULL,
    has_terraformable boolean NOT NULL,
    source_observed_at timestamptz,
    completeness double precision NOT NULL CHECK(completeness BETWEEN 0 AND 1),
    confidence double precision NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    PRIMARY KEY(derived_generation_id,system_id64),
    FOREIGN KEY(derived_generation_id,system_id64)
        REFERENCES v3_derived.system_rating_vector DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE v3_derived.system_search IS
    'Stable Finder search projection. position_ly carries the GiST index; the numeric LY columns remain the API and rendering truth.';

-- Spatial read path, name autocomplete, and region filtering. Further indexes are
-- added only against measured workloads.
CREATE INDEX system_search_position_gist ON v3_derived.system_search USING gist(position_ly);
CREATE INDEX system_search_name_lower ON v3_derived.system_search(derived_generation_id,lower(name));
CREATE INDEX system_search_region ON v3_derived.system_search(derived_generation_id,galaxy_region_id);

-- Pyramid levels are versioned as a set, because cell size and encoding can both
-- change without a schema redesign.
CREATE TABLE v3_spatial.cell_level (
    spatial_pyramid_version text NOT NULL CHECK(spatial_pyramid_version ~ '^[a-z][a-z0-9_]{0,62}$'),
    level smallint NOT NULL CHECK(level BETWEEN 0 AND 30),
    cell_size_ly double precision NOT NULL CHECK(cell_size_ly > 0),
    intended_scale text NOT NULL CHECK(length(intended_scale) BETWEEN 1 AND 64),
    target_count_min integer CHECK(target_count_min > 0),
    target_count_max integer CHECK(target_count_max >= target_count_min),
    PRIMARY KEY(spatial_pyramid_version,level)
);

-- One row per populated cell per level. Bounds and origin are deterministic
-- functions of level and cell_key, stored so a reader never has to re-derive them.
CREATE TABLE v3_spatial.cell_summary (
    derived_generation_id uuid NOT NULL REFERENCES v3_meta.derived_generation,
    spatial_pyramid_version text NOT NULL,
    level smallint NOT NULL,
    cell_key text NOT NULL CHECK(cell_key ~ '^[A-Za-z0-9_.-]{1,64}$'),
    origin_x_ly double precision NOT NULL,
    origin_y_ly double precision NOT NULL,
    origin_z_ly double precision NOT NULL,
    system_count integer NOT NULL CHECK(system_count > 0),
    centroid_x_ly double precision NOT NULL,
    centroid_y_ly double precision NOT NULL,
    centroid_z_ly double precision NOT NULL,
    landable_count integer NOT NULL CHECK(landable_count >= 0),
    station_count integer NOT NULL CHECK(station_count >= 0),
    biological_system_count integer NOT NULL CHECK(biological_system_count >= 0),
    terraformable_system_count integer NOT NULL CHECK(terraformable_system_count >= 0),
    representative_system_id64 bigint CHECK(representative_system_id64 >= 0),
    derived_summary jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(derived_summary)='object'),
    PRIMARY KEY(derived_generation_id,spatial_pyramid_version,level,cell_key),
    FOREIGN KEY(spatial_pyramid_version,level) REFERENCES v3_spatial.cell_level
);
COMMENT ON COLUMN v3_spatial.cell_summary.derived_summary IS
    'Versioned score/archetype summaries a map layer needs at this LOD. Keys are governed by spatial_pyramid_version; factual counters are never duplicated into it.';

-- Clusters are independent published products: different domains may publish
-- different sets with different algorithms and parameters.
CREATE TABLE v3_spatial.cluster_run (
    derived_generation_id uuid NOT NULL REFERENCES v3_meta.derived_generation,
    cluster_run_id uuid NOT NULL,
    owner_domain text NOT NULL CHECK(owner_domain ~ '^[a-z][a-z0-9_]{0,62}$'),
    algorithm text NOT NULL CHECK(length(algorithm) BETWEEN 1 AND 64),
    algorithm_version text NOT NULL CHECK(length(algorithm_version) BETWEEN 1 AND 64),
    parameters jsonb NOT NULL CHECK(jsonb_typeof(parameters)='object'),
    lifecycle_state text NOT NULL DEFAULT 'BUILDING'
        CHECK(lifecycle_state IN ('BUILDING','VALIDATING','READY','PUBLISHED','FAILED','RETIRED')),
    validation_receipt jsonb CHECK(validation_receipt IS NULL OR jsonb_typeof(validation_receipt)='object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    CHECK(lifecycle_state NOT IN ('READY','PUBLISHED','RETIRED') OR validation_receipt IS NOT NULL),
    PRIMARY KEY(derived_generation_id,cluster_run_id)
);

-- cluster and cluster_member carry derived_generation_id so the shared
-- generation immutability guard applies unchanged. The composite foreign keys
-- keep that denormalisation from ever diverging from its cluster run.
CREATE TABLE v3_spatial.cluster (
    derived_generation_id uuid NOT NULL,
    cluster_run_id uuid NOT NULL,
    cluster_id text NOT NULL CHECK(cluster_id ~ '^[A-Za-z0-9_.:-]{1,64}$'),
    centroid_x_ly double precision NOT NULL,
    centroid_y_ly double precision NOT NULL,
    centroid_z_ly double precision NOT NULL,
    min_x_ly double precision NOT NULL,
    min_y_ly double precision NOT NULL,
    min_z_ly double precision NOT NULL,
    max_x_ly double precision NOT NULL,
    max_y_ly double precision NOT NULL,
    max_z_ly double precision NOT NULL,
    member_count integer NOT NULL CHECK(member_count > 0),
    summary jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(summary)='object'),
    CHECK(min_x_ly <= max_x_ly AND min_y_ly <= max_y_ly AND min_z_ly <= max_z_ly),
    PRIMARY KEY(derived_generation_id,cluster_run_id,cluster_id),
    FOREIGN KEY(derived_generation_id,cluster_run_id)
        REFERENCES v3_spatial.cluster_run DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE v3_spatial.cluster_member (
    derived_generation_id uuid NOT NULL,
    cluster_run_id uuid NOT NULL,
    cluster_id text NOT NULL,
    system_id64 bigint NOT NULL CHECK(system_id64 >= 0),
    membership_score integer CHECK(membership_score BETWEEN 0 AND 10000),
    confidence integer CHECK(confidence BETWEEN 0 AND 10000),
    PRIMARY KEY(derived_generation_id,cluster_run_id,cluster_id,system_id64),
    FOREIGN KEY(derived_generation_id,cluster_run_id,cluster_id)
        REFERENCES v3_spatial.cluster DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(derived_generation_id,system_id64)
        REFERENCES v3_derived.system_rating_vector DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE v3_spatial.cluster_member IS
    'Membership authority. Map hulls are projections of these rows, never the other way round.';
CREATE INDEX cluster_member_system ON v3_spatial.cluster_member(derived_generation_id,system_id64);

-- The same immutability guard 003 installed for its derived relations, applied to
-- every relation this migration adds. It must remain enabled: replica mode would
-- defeat immutable generation enforcement and deferred referential checks.
DO $$
DECLARE relation_ text;
BEGIN
    FOREACH relation_ IN ARRAY ARRAY[
        'v3_derived.system_search','v3_spatial.cell_summary',
        'v3_spatial.cluster_run','v3_spatial.cluster','v3_spatial.cluster_member'
    ] LOOP
        EXECUTE format('CREATE TRIGGER immutable_insert AFTER INSERT ON %s REFERENCING NEW TABLE AS changed_rows FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.guard_generation_write()',relation_);
        EXECUTE format('CREATE TRIGGER immutable_update_old AFTER UPDATE ON %s REFERENCING OLD TABLE AS changed_rows FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.guard_generation_write()',relation_);
        EXECUTE format('CREATE TRIGGER immutable_update_new AFTER UPDATE ON %s REFERENCING NEW TABLE AS changed_rows FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.guard_generation_write()',relation_);
        EXECUTE format('CREATE TRIGGER immutable_delete AFTER DELETE ON %s REFERENCING OLD TABLE AS changed_rows FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.guard_generation_write()',relation_);
        EXECUTE format('CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON %s FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.guard_generation_write()',relation_);
    END LOOP;
END $$;

-- Stable application boundary, matching 003's pattern: the convenience view
-- resolves the published generation, while multi-query responses resolve and pin
-- derived_generation_id once.
CREATE VIEW v3_app.system_search AS
SELECT s.* FROM v3_meta.current_derived_generation c
JOIN v3_derived.system_search s USING(derived_generation_id);

CREATE VIEW v3_app.cluster_member AS
SELECT m.* FROM v3_meta.current_derived_generation c
JOIN v3_spatial.cluster_member m USING(derived_generation_id);

COMMIT;
