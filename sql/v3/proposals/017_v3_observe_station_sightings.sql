-- Proposed additive migration 017. UNREGISTERED: it is not in
-- sql/v3/migration-manifest.txt and nothing applies it. Registration as
-- migration 017 (manifest, schema transition, schema identity), the role
-- membership on the production application login and the worker's first run
-- are post-go-live steps that need the owner's explicit go.
--
-- Public-source, non-canonical station observations (EDDN Docked, Spansh
-- galaxy_stations). Evidence only, never canonical truth: nothing here reads
-- or writes v3_gen_*, v3_derived, v3_spatial or v3_app, and no foreign key
-- points at a generation relation. Only new empty relations are created plus
-- two seed rows each in the existing v3_source registry; no populated table is
-- locked beyond a row insert.
--
-- Privacy: uploader identifiers are never stored. There is deliberately no
-- column that could hold one. No personal data lives in this schema, so it
-- needs no entry in sql/v3/governance/personal_data_registry.json (whose
-- coverage is v3_identity/v3_private). Journal-upload observations are personal
-- and must stay in a separate v3_private relation, never in this schema.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

-- Roles are cluster-wide; another database may already have created this one.
-- Adopt only the same restricted NOLOGIN role, never repair an unsafe one.
DO $$
DECLARE
    observer pg_roles%ROWTYPE;
    observer_exists boolean := true;
    has_password boolean;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='v3_station_observer') THEN
        observer_exists := false;
        SET LOCAL createrole_self_grant = 'set';
        CREATE ROLE v3_station_observer NOLOGIN NOINHERIT;
    END IF;
    SELECT * INTO STRICT observer FROM pg_roles WHERE rolname='v3_station_observer';
    IF observer.rolcanlogin OR observer.rolsuper OR observer.rolcreaterole
       OR observer.rolcreatedb OR observer.rolreplication OR observer.rolbypassrls
       OR observer.rolinherit OR observer.rolconnlimit NOT IN (-1,0) THEN
        RAISE EXCEPTION 'unsafe v3_station_observer attributes: requires NOLOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS NOINHERIT';
    END IF;
    BEGIN
        SELECT rolpassword IS NOT NULL INTO STRICT has_password
        FROM pg_authid WHERE oid=observer.oid;
        IF has_password THEN
            RAISE EXCEPTION 'unsafe v3_station_observer password: requires NULL';
        END IF;
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'v3_station_observer password check skipped: pg_authid is not readable';
    END;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member=observer.oid) THEN
        RAISE EXCEPTION 'unsafe v3_station_observer membership: must not be a member of any role';
    END IF;
    -- Adoption: anyone able to SET ROLE to the observer could append sightings
    -- here once the grants below apply. Only the application login may hold it,
    -- with SET TRUE, INHERIT FALSE and ADMIN FALSE. (A fresh non-superuser
    -- creation's implicit bootstrap grant is not adoption.)
    IF observer_exists AND EXISTS (
        SELECT 1 FROM pg_auth_members m JOIN pg_roles c ON c.oid=m.member
        WHERE m.roleid=observer.oid
          AND (c.rolname<>'edfinder_v3' OR NOT m.set_option OR m.inherit_option OR m.admin_option)
    ) THEN
        RAISE EXCEPTION 'unsafe v3_station_observer incoming membership: only edfinder_v3 may be a member, with SET TRUE, INHERIT FALSE and ADMIN FALSE';
    END IF;
END $$;

CREATE SCHEMA v3_observe;
REVOKE ALL ON SCHEMA v3_observe FROM PUBLIC;
COMMENT ON SCHEMA v3_observe IS
    'Public-source, non-canonical station observations (EDDN, Spansh). Evidence only; never canonical truth. No uploader identifiers. Rights: INTERNAL_USE, not redistributed.';

CREATE TYPE v3_observe.sighting_source AS ENUM ('spansh', 'eddn');
COMMENT ON TYPE v3_observe.sighting_source IS
    'Which public feed produced a sighting. Change detection is per (source, market_id) because feed vocabularies differ.';

-- ---------------------------------------------------------------------------
-- Source registry: bind each sighting source to the existing V3 source ledger
-- and its rights policy (INTERNAL_USE, distribution not allowed).
-- ---------------------------------------------------------------------------

-- Identity values are taken past the current maximum rather than from the
-- sequences: earlier seeds may have inserted explicit ids without advancing
-- them. A pre-existing source_code fails this migration loudly (unique key).
INSERT INTO v3_source.source (source_id, source_code, display_name, endpoint_identity, authority_class)
SELECT (SELECT COALESCE(max(source_id), 0) FROM v3_source.source) + seed.ordinal,
       seed.source_code, seed.display_name, seed.endpoint_identity, 'SECONDARY_AGGREGATED'
FROM (VALUES
    (1, 'eddn.journal.docked', 'EDDN journal/1 Docked station sightings',
        'tcp://eddn.edcd.io:9500 schema https://eddn.edcd.io/schemas/journal/1'),
    (2, 'spansh.galaxy-stations', 'Spansh galaxy_stations dump station sightings',
        'https://downloads.spansh.co.uk/galaxy_stations.json.gz')
) AS seed(ordinal, source_code, display_name, endpoint_identity);

INSERT INTO v3_source.source_rights_policy (
    rights_policy_id, source_id, policy_version, rights_class, retention_class,
    distribution_allowed, effective_at)
SELECT (SELECT COALESCE(max(rights_policy_id), 0) FROM v3_source.source_rights_policy)
           + row_number() OVER (ORDER BY s.source_code),
       s.source_id, 'v3-observe-2026-10-21', 'INTERNAL_USE', 'OBSERVATION_CHANGE_HISTORY_INDEFINITE',
       false, '2026-10-21T00:00:00Z'
FROM v3_source.source s
WHERE s.source_code IN ('eddn.journal.docked', 'spansh.galaxy-stations');

-- Keep both identity sequences ahead of the explicit ids just taken, so a
-- later default-valued insert cannot collide with them.
SELECT setval(pg_get_serial_sequence('v3_source.source', 'source_id'),
              GREATEST((SELECT max(source_id) FROM v3_source.source),
                       (SELECT last_value FROM v3_source.source_source_id_seq)));
SELECT setval(pg_get_serial_sequence('v3_source.source_rights_policy', 'rights_policy_id'),
              GREATEST((SELECT max(rights_policy_id) FROM v3_source.source_rights_policy),
                       (SELECT last_value FROM v3_source.source_rights_policy_rights_policy_id_seq)));

CREATE TABLE v3_observe.sighting_source_binding (
    source v3_observe.sighting_source PRIMARY KEY,
    source_id smallint NOT NULL UNIQUE REFERENCES v3_source.source(source_id),
    rights_policy_id bigint NOT NULL,
    FOREIGN KEY (rights_policy_id, source_id)
        REFERENCES v3_source.source_rights_policy(rights_policy_id, source_id)
);
COMMENT ON TABLE v3_observe.sighting_source_binding IS
    'Binds each sighting source to its v3_source.source row and INTERNAL_USE rights policy. Written only by this migration.';

INSERT INTO v3_observe.sighting_source_binding (source, source_id, rights_policy_id)
SELECT binding.source, s.source_id, p.rights_policy_id
FROM (VALUES
    ('eddn'::v3_observe.sighting_source, 'eddn.journal.docked'),
    ('spansh'::v3_observe.sighting_source, 'spansh.galaxy-stations')
) AS binding(source, source_code)
JOIN v3_source.source s ON s.source_code = binding.source_code
JOIN v3_source.source_rights_policy p
  ON p.source_id = s.source_id AND p.policy_version = 'v3-observe-2026-10-21';

DO $$
BEGIN
    IF (SELECT count(*) FROM v3_observe.sighting_source_binding) <> 2 THEN
        RAISE EXCEPTION 'v3_observe source binding seed incomplete';
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Bounds helpers used by CHECK constraints (pure, immutable).
-- ---------------------------------------------------------------------------

CREATE FUNCTION v3_observe.services_bounded(services text[]) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT cardinality(services) <= 128
       AND array_ndims(services) IS NOT DISTINCT FROM CASE WHEN cardinality(services) = 0 THEN NULL ELSE 1 END
       AND NOT EXISTS (
           SELECT 1 FROM unnest(services) AS item
           WHERE item IS NULL OR length(item) NOT BETWEEN 1 AND 128)
$$;
COMMENT ON FUNCTION v3_observe.services_bounded(text[]) IS
    'At most 128 non-null service names of 1-128 characters each.';

CREATE FUNCTION v3_observe.economies_bounded(economies jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT jsonb_typeof(economies) = 'array'
       AND jsonb_array_length(economies) <= 8
       AND NOT EXISTS (
           SELECT 1 FROM jsonb_array_elements(economies) AS item
           WHERE jsonb_typeof(item) <> 'object'
              OR (SELECT count(*) FROM jsonb_object_keys(item)) <> 2
              OR jsonb_typeof(item->'name') IS DISTINCT FROM 'string'
              OR length(item->>'name') NOT BETWEEN 1 AND 128
              OR jsonb_typeof(item->'proportion') IS DISTINCT FROM 'number'
              OR (item->>'proportion')::numeric NOT BETWEEN 0 AND 1)
$$;
COMMENT ON FUNCTION v3_observe.economies_bounded(jsonb) IS
    'An array of at most 8 {"name": 1-128 chars, "proportion": 0..1} objects.';

CREATE FUNCTION v3_observe.landing_pads_bounded(pads jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT jsonb_typeof(pads) = 'object'
       AND (SELECT count(*) FROM jsonb_object_keys(pads)) = 3
       AND NOT EXISTS (
           SELECT 1 FROM jsonb_each(pads) AS item(key, value)
           WHERE item.key NOT IN ('small', 'medium', 'large')
              OR jsonb_typeof(item.value) <> 'number'
              OR (item.value::text)::numeric NOT BETWEEN 0 AND 255
              OR (item.value::text)::numeric <> trunc((item.value::text)::numeric))
$$;
COMMENT ON FUNCTION v3_observe.landing_pads_bounded(jsonb) IS
    'An object with exactly the keys small, medium and large, each an integer count 0..255.';

-- ---------------------------------------------------------------------------
-- Change-only sighting history.
-- ---------------------------------------------------------------------------

CREATE TABLE v3_observe.station_sighting (
    sighting_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source v3_observe.sighting_source NOT NULL,
    market_id bigint NOT NULL CHECK (market_id > 0),
    system_id64 bigint NOT NULL CHECK (system_id64 > 0),
    system_name text CHECK (length(system_name) BETWEEN 1 AND 128),
    station_name text NOT NULL CHECK (length(station_name) BETWEEN 1 AND 128),
    station_type text CHECK (length(station_type) BETWEEN 1 AND 64),
    economies jsonb NOT NULL CHECK (v3_observe.economies_bounded(economies)),
    services text[] NOT NULL CHECK (v3_observe.services_bounded(services)),
    landing_pads jsonb CHECK (v3_observe.landing_pads_bounded(landing_pads)),
    -- Spansh nesting only; EDDN Docked carries no body.
    body_id smallint CHECK (body_id BETWEEN 0 AND 4095),
    body_name text CHECK (length(body_name) BETWEEN 1 AND 128),
    -- Informational at the time of the change; excluded from the state hash
    -- because it drifts with orbital position.
    distance_ls double precision CHECK (distance_ls >= 0 AND distance_ls <= 1e9),
    state_sha256 bytea NOT NULL CHECK (octet_length(state_sha256) = 32),
    -- The record's own time: EDDN journal event timestamp / Spansh updateTime.
    source_record_ts timestamptz NOT NULL
        CHECK (source_record_ts >= '2014-01-01T00:00:00Z'),
    -- EDDN gateway receipt time; NULL for Spansh.
    gateway_ts timestamptz CHECK (gateway_ts >= '2014-01-01T00:00:00Z'),
    -- When our collector received/normalised the record.
    observed_at timestamptz NOT NULL CHECK (observed_at >= '2014-01-01T00:00:00Z'),
    -- When this store first recorded the state.
    first_seen_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    -- EDDN header allowlist only. uploaderID is never read or stored.
    software_name text CHECK (length(software_name) BETWEEN 1 AND 128),
    software_version text CHECK (length(software_version) BETWEEN 1 AND 128),
    game_version text CHECK (length(game_version) BETWEEN 1 AND 64),
    game_build text CHECK (length(game_build) BETWEEN 1 AND 64),
    CHECK (source = 'eddn' OR (gateway_ts IS NULL AND software_name IS NULL
                               AND software_version IS NULL AND game_build IS NULL)),
    CHECK (source = 'spansh' OR (body_id IS NULL AND body_name IS NULL)),
    -- Upper bounds: a far-future source time would win station_current
    -- forever (the history is append-only) and make every later sighting stale.
    CHECK (source_record_ts <= observed_at + interval '1 hour'),
    CHECK (gateway_ts <= observed_at + interval '1 hour'),
    CHECK (observed_at <= first_seen_at + interval '1 hour')
);
COMMENT ON TABLE v3_observe.station_sighting IS
    'Append-only change history: one row per (source, market_id) state change, kept indefinitely. Fleet carriers excluded. No uploader identifiers.';
COMMENT ON COLUMN v3_observe.station_sighting.state_sha256 IS
    'SHA-256 of the canonical research state (name, type, economies, services, pads, body). Writers insert only when it differs from the current state for (source, market_id).';

CREATE INDEX station_sighting_current_idx
    ON v3_observe.station_sighting (source, market_id, source_record_ts DESC, sighting_id DESC)
    INCLUDE (state_sha256);
COMMENT ON INDEX v3_observe.station_sighting_current_idx IS
    'Use: current state per (source, market_id) for change detection and research reads.';
CREATE INDEX station_sighting_system_idx
    ON v3_observe.station_sighting (system_id64, source_record_ts DESC);
COMMENT ON INDEX v3_observe.station_sighting_system_idx IS
    'Use: change history for every station in one system.';

CREATE FUNCTION v3_observe.reject_sighting_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'v3_observe.station_sighting is append-only change history';
END $$;
CREATE TRIGGER station_sighting_append_only BEFORE UPDATE OR DELETE
    ON v3_observe.station_sighting FOR EACH ROW
    EXECUTE FUNCTION v3_observe.reject_sighting_mutation();
CREATE TRIGGER station_sighting_append_only_truncate BEFORE TRUNCATE
    ON v3_observe.station_sighting FOR EACH STATEMENT
    EXECUTE FUNCTION v3_observe.reject_sighting_mutation();

-- Current state per (source, market_id): the latest record by source time,
-- ties broken by insertion. A view, not a table, so writers never UPDATE a hot
-- row (V2's EDDN update churn is what bloated its disk).
CREATE VIEW v3_observe.station_current WITH (security_invoker = true) AS
SELECT DISTINCT ON (source, market_id)
       source, market_id, sighting_id, system_id64, system_name, station_name,
       station_type, economies, services, landing_pads, body_id, body_name,
       distance_ls, state_sha256, source_record_ts, gateway_ts, observed_at,
       first_seen_at
FROM v3_observe.station_sighting
ORDER BY source, market_id, source_record_ts DESC, sighting_id DESC;
COMMENT ON VIEW v3_observe.station_current IS
    'Latest recorded state per (source, market_id). Filter on source and market_id so the predicate reaches station_sighting_current_idx.';

-- ---------------------------------------------------------------------------
-- Least-privilege writer role. Workers connect with existing credentials and
-- SET ROLE v3_station_observer; it can only read the registry and append
-- sightings.
-- ---------------------------------------------------------------------------

GRANT USAGE ON SCHEMA v3_observe TO v3_station_observer;
GRANT SELECT ON v3_observe.sighting_source_binding TO v3_station_observer;
-- No sequence grant: an identity column draws from its implicit sequence
-- without a separate sequence privilege (tested under SET ROLE).
GRANT SELECT, INSERT ON v3_observe.station_sighting TO v3_station_observer;
GRANT SELECT ON v3_observe.station_current TO v3_station_observer;

-- A superuser application login (production today) can SET ROLE without
-- membership. A non-superuser application login gets SET without INHERIT.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='edfinder_v3' AND NOT rolsuper)
       AND NOT EXISTS (
           SELECT 1 FROM pg_auth_members m
           JOIN pg_roles r ON r.oid=m.roleid JOIN pg_roles c ON c.oid=m.member
           WHERE r.rolname='v3_station_observer' AND c.rolname='edfinder_v3') THEN
        GRANT v3_station_observer TO edfinder_v3 WITH INHERIT FALSE, SET TRUE;
    END IF;
END $$;

COMMIT;
