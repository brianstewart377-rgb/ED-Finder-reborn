-- 024: current state of inhabited and claimed systems (v3_state).
--
-- What it is: one small schema holding what Spansh (and later EDDN) says about
-- each system that has a population or a station: population, government,
-- economy, factions, Powerplay, and which kinds of station are there
-- (including colonisation construction sites). Find uses it to tell
-- "inhabited", "being colonised" and "free to claim" apart; BGS uses the
-- faction rows.
--
-- What it is not: history. Each row is the latest known picture of a system.
--
-- Additive only: it creates a new schema and nothing else. It reads and writes
-- no existing table, so it cannot change the galaxy, scoring or account data.
-- Safe to run more than once.
--
-- Loaded by apps/importer/src/spansh_system_state.py, which writes rows into
-- v3_state.system_stage and then calls v3_state.apply_stage().
BEGIN;

CREATE SCHEMA IF NOT EXISTS v3_state;
COMMENT ON SCHEMA v3_state IS
    'Latest known state of systems that have a population or a station (Spansh dumps, later EDDN). Not history.';

CREATE TABLE IF NOT EXISTS v3_state.system_state (
    system_id64 bigint PRIMARY KEY,
    name text NOT NULL,
    x_ly double precision NOT NULL,
    y_ly double precision NOT NULL,
    z_ly double precision NOT NULL,
    population bigint NOT NULL DEFAULT 0 CHECK (population >= 0),
    allegiance text,
    government text,
    security text,
    primary_economy text,
    secondary_economy text,
    controlling_faction text,
    controlling_faction_state text,
    controlling_power text,
    power_state text,
    power_state_control_progress double precision,
    power_state_reinforcement bigint,
    power_state_undermining bigint,
    -- Stations that are not fleet carriers, counted by Spansh station type,
    -- e.g. {"Outpost": 2, "Space Construction Depot": 1}. A station with no
    -- type in the source is counted under "Unknown".
    station_type_counts jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(station_type_counts) = 'object'),
    station_count integer NOT NULL DEFAULT 0 CHECK (station_count >= 0),
    carrier_count integer NOT NULL DEFAULT 0 CHECK (carrier_count >= 0),
    construction_site_count integer NOT NULL DEFAULT 0 CHECK (construction_site_count >= 0),
    colonisation_ship_present boolean NOT NULL DEFAULT false,
    -- Spansh's per-field "last updated" times, kept as supplied.
    field_timestamps jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(field_timestamps) = 'object'),
    -- Newest "last updated" time Spansh gives for the system itself, its
    -- factions/powers and its non-carrier stations.
    system_updated_at timestamptz NOT NULL,
    -- Newest time any non-carrier station here was seen (NULL: no station).
    stations_updated_at timestamptz,
    -- Fingerprint of everything loaded for this system (including its faction
    -- and power lists), so a reload only rewrites systems that changed.
    content_hash text NOT NULL,
    source text NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE v3_state.system_state IS
    'One row per system with a population or a non-carrier station: the newest known picture.';

CREATE INDEX IF NOT EXISTS system_state_inhabited
    ON v3_state.system_state (system_id64) WHERE population > 0;
CREATE INDEX IF NOT EXISTS system_state_controlling_faction
    ON v3_state.system_state (controlling_faction) WHERE controlling_faction IS NOT NULL;

CREATE TABLE IF NOT EXISTS v3_state.system_faction (
    system_id64 bigint NOT NULL
        REFERENCES v3_state.system_state(system_id64) ON DELETE CASCADE,
    faction_name text NOT NULL,
    allegiance text,
    government text,
    influence double precision,
    state text,
    active_states jsonb NOT NULL DEFAULT '[]'::jsonb,
    pending_states jsonb NOT NULL DEFAULT '[]'::jsonb,
    recovering_states jsonb NOT NULL DEFAULT '[]'::jsonb,
    is_controlling boolean NOT NULL DEFAULT false,
    PRIMARY KEY (system_id64, faction_name)
);
COMMENT ON TABLE v3_state.system_faction IS
    'Each faction present in a system, as of system_state.system_updated_at.';
CREATE INDEX IF NOT EXISTS system_faction_by_name
    ON v3_state.system_faction (faction_name, system_id64);

CREATE TABLE IF NOT EXISTS v3_state.system_power (
    system_id64 bigint NOT NULL
        REFERENCES v3_state.system_state(system_id64) ON DELETE CASCADE,
    power_name text NOT NULL,
    is_controlling boolean NOT NULL DEFAULT false,
    conflict_progress double precision,
    PRIMARY KEY (system_id64, power_name)
);
COMMENT ON TABLE v3_state.system_power IS
    'Each Powerplay power active in a system, as of system_state.system_updated_at.';
CREATE INDEX IF NOT EXISTS system_power_by_name
    ON v3_state.system_power (power_name, system_id64);

-- Landing table for one load. Emptied before and after every load; never read
-- by the app. UNLOGGED because its contents never need to survive a crash.
CREATE UNLOGGED TABLE IF NOT EXISTS v3_state.system_stage (
    seq bigint NOT NULL,
    system_id64 bigint NOT NULL,
    name text NOT NULL,
    x_ly double precision NOT NULL,
    y_ly double precision NOT NULL,
    z_ly double precision NOT NULL,
    population bigint NOT NULL,
    allegiance text,
    government text,
    security text,
    primary_economy text,
    secondary_economy text,
    controlling_faction text,
    controlling_faction_state text,
    controlling_power text,
    power_state text,
    power_state_control_progress double precision,
    power_state_reinforcement bigint,
    power_state_undermining bigint,
    station_type_counts jsonb NOT NULL,
    station_count integer NOT NULL,
    carrier_count integer NOT NULL,
    construction_site_count integer NOT NULL,
    colonisation_ship_present boolean NOT NULL,
    field_timestamps jsonb NOT NULL,
    system_updated_at timestamptz NOT NULL,
    stations_updated_at timestamptz,
    content_hash text NOT NULL,
    factions jsonb NOT NULL,
    powers jsonb NOT NULL
);

-- Added after the first production load; a no-op on a fresh install.
ALTER TABLE v3_state.system_state ADD COLUMN IF NOT EXISTS stations_updated_at timestamptz;
ALTER TABLE v3_state.system_stage ADD COLUMN IF NOT EXISTS stations_updated_at timestamptz;

CREATE TABLE IF NOT EXISTS v3_state.load_log (
    load_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL,
    full_snapshot boolean NOT NULL,
    staged_rows bigint NOT NULL,
    staged_systems bigint NOT NULL,
    systems_written bigint NOT NULL,
    systems_unchanged bigint NOT NULL,
    systems_removed bigint NOT NULL,
    newest_record_at timestamptz
);
COMMENT ON TABLE v3_state.load_log IS
    'One row per completed load, so the app can show how fresh the state is.';

-- Merge the staged rows into the live tables.
--
--   * If a system is staged more than once, the newest record wins.
--   * A staged record replaces the stored one only when it is newer, or the
--     same age with different content. An older dump can never overwrite
--     newer data (for example from EDDN).
--   * full_snapshot = true means "the staged rows are every system that
--     currently has a population or a station". Stored systems missing from
--     the stage are then removed, unless they were updated after the newest
--     staged record. To guard against an incomplete input, a full snapshot
--     that would remove more than a fifth of the stored systems is refused.
--   * A time more than two days in the future cannot be real (Spansh's data
--     has a few). Staged records dated like that are refused, and a stored
--     row dated like that is treated as out of date: any load may replace or
--     remove it.
CREATE OR REPLACE FUNCTION v3_state.apply_stage(full_snapshot boolean, source_label text)
RETURNS TABLE (
    staged_rows bigint,
    staged_systems bigint,
    systems_written bigint,
    systems_unchanged bigint,
    systems_removed bigint,
    newest_record_at timestamptz
)
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $apply$
DECLARE
    staged_rows_ bigint;
    staged_systems_ bigint;
    written_ bigint;
    removed_ bigint := 0;
    newest_ timestamptz;
    stored_before_ bigint;
    would_remove_ bigint;
    horizon_ timestamptz := now() + interval '2 days';
BEGIN
    IF source_label IS NULL OR btrim(source_label) = '' THEN
        RAISE EXCEPTION 'source_label is required';
    END IF;
    IF full_snapshot IS NULL THEN
        RAISE EXCEPTION 'full_snapshot must be true or false';
    END IF;

    -- One load at a time; readers are not blocked.
    LOCK TABLE v3_state.system_state IN SHARE ROW EXCLUSIVE MODE;

    SELECT count(*), count(DISTINCT s.system_id64), max(s.system_updated_at)
      INTO staged_rows_, staged_systems_, newest_
      FROM v3_state.system_stage s;

    IF staged_rows_ = 0 THEN
        RAISE EXCEPTION 'nothing staged: refusing to apply an empty load';
    END IF;
    IF newest_ > horizon_ THEN
        RAISE EXCEPTION
            'staged data holds a record dated in the future (%); the loader should have ignored it, nothing was changed',
            newest_;
    END IF;

    CREATE TEMP TABLE picked_ ON COMMIT DROP AS
    SELECT DISTINCT ON (s.system_id64) s.*
      FROM v3_state.system_stage s
     ORDER BY s.system_id64, s.system_updated_at DESC, s.seq DESC;
    CREATE UNIQUE INDEX picked_system_id64_ ON picked_ (system_id64);
    ANALYZE picked_;

    IF full_snapshot THEN
        SELECT count(*) INTO stored_before_ FROM v3_state.system_state;
        SELECT count(*) INTO would_remove_
          FROM v3_state.system_state t
         WHERE (t.system_updated_at <= newest_ OR t.system_updated_at > horizon_)
           AND NOT EXISTS (SELECT 1 FROM picked_ p WHERE p.system_id64 = t.system_id64);
        IF would_remove_ * 5 > stored_before_ THEN
            RAISE EXCEPTION
                'full snapshot would remove % of % stored systems; the input looks incomplete, nothing was changed',
                would_remove_, stored_before_;
        END IF;
    END IF;

    CREATE TEMP TABLE written_ids_ (system_id64 bigint PRIMARY KEY) ON COMMIT DROP;
    WITH upserted AS (
        INSERT INTO v3_state.system_state AS t (
            system_id64, name, x_ly, y_ly, z_ly, population, allegiance, government,
            security, primary_economy, secondary_economy, controlling_faction,
            controlling_faction_state, controlling_power, power_state,
            power_state_control_progress, power_state_reinforcement,
            power_state_undermining, station_type_counts, station_count,
            carrier_count, construction_site_count, colonisation_ship_present,
            field_timestamps, system_updated_at, stations_updated_at, content_hash,
            source, loaded_at
        )
        SELECT p.system_id64, p.name, p.x_ly, p.y_ly, p.z_ly, p.population, p.allegiance,
               p.government, p.security, p.primary_economy, p.secondary_economy,
               p.controlling_faction, p.controlling_faction_state, p.controlling_power,
               p.power_state, p.power_state_control_progress, p.power_state_reinforcement,
               p.power_state_undermining, p.station_type_counts, p.station_count,
               p.carrier_count, p.construction_site_count, p.colonisation_ship_present,
               p.field_timestamps, p.system_updated_at, p.stations_updated_at,
               p.content_hash, source_label, now()
          FROM picked_ p
        ON CONFLICT (system_id64) DO UPDATE SET
            name = EXCLUDED.name,
            x_ly = EXCLUDED.x_ly, y_ly = EXCLUDED.y_ly, z_ly = EXCLUDED.z_ly,
            population = EXCLUDED.population,
            allegiance = EXCLUDED.allegiance,
            government = EXCLUDED.government,
            security = EXCLUDED.security,
            primary_economy = EXCLUDED.primary_economy,
            secondary_economy = EXCLUDED.secondary_economy,
            controlling_faction = EXCLUDED.controlling_faction,
            controlling_faction_state = EXCLUDED.controlling_faction_state,
            controlling_power = EXCLUDED.controlling_power,
            power_state = EXCLUDED.power_state,
            power_state_control_progress = EXCLUDED.power_state_control_progress,
            power_state_reinforcement = EXCLUDED.power_state_reinforcement,
            power_state_undermining = EXCLUDED.power_state_undermining,
            station_type_counts = EXCLUDED.station_type_counts,
            station_count = EXCLUDED.station_count,
            carrier_count = EXCLUDED.carrier_count,
            construction_site_count = EXCLUDED.construction_site_count,
            colonisation_ship_present = EXCLUDED.colonisation_ship_present,
            field_timestamps = EXCLUDED.field_timestamps,
            system_updated_at = EXCLUDED.system_updated_at,
            stations_updated_at = EXCLUDED.stations_updated_at,
            content_hash = EXCLUDED.content_hash,
            source = EXCLUDED.source,
            loaded_at = EXCLUDED.loaded_at
        WHERE EXCLUDED.system_updated_at > t.system_updated_at
           OR (EXCLUDED.system_updated_at = t.system_updated_at
               AND EXCLUDED.content_hash <> t.content_hash)
           OR t.system_updated_at > horizon_
        RETURNING t.system_id64
    )
    INSERT INTO written_ids_ (system_id64) SELECT u.system_id64 FROM upserted u;
    SELECT count(*) INTO written_ FROM written_ids_;

    -- Faction and power lists are replaced whole for every system just written.
    DELETE FROM v3_state.system_faction f USING written_ids_ w WHERE f.system_id64 = w.system_id64;
    INSERT INTO v3_state.system_faction (
        system_id64, faction_name, allegiance, government, influence, state,
        active_states, pending_states, recovering_states, is_controlling
    )
    SELECT DISTINCT ON (p.system_id64, f.name)
           p.system_id64, f.name, f.allegiance, f.government, f.influence, f.state,
           coalesce(f."activeStates", '[]'::jsonb),
           coalesce(f."pendingStates", '[]'::jsonb),
           coalesce(f."recoveringStates", '[]'::jsonb),
           p.controlling_faction IS NOT NULL AND f.name = p.controlling_faction
      FROM picked_ p
      JOIN written_ids_ w ON w.system_id64 = p.system_id64
     CROSS JOIN LATERAL jsonb_to_recordset(p.factions) AS f(
           name text, allegiance text, government text, influence double precision,
           state text, "activeStates" jsonb, "pendingStates" jsonb, "recoveringStates" jsonb)
     WHERE f.name IS NOT NULL AND f.name <> ''
     ORDER BY p.system_id64, f.name;

    DELETE FROM v3_state.system_power pw USING written_ids_ w WHERE pw.system_id64 = w.system_id64;
    INSERT INTO v3_state.system_power (system_id64, power_name, is_controlling, conflict_progress)
    SELECT DISTINCT ON (p.system_id64, pw.power)
           p.system_id64, pw.power,
           p.controlling_power IS NOT NULL AND pw.power = p.controlling_power,
           pw.conflict_progress
      FROM picked_ p
      JOIN written_ids_ w ON w.system_id64 = p.system_id64
     CROSS JOIN LATERAL jsonb_to_recordset(p.powers)
           AS pw(power text, conflict_progress double precision)
     WHERE pw.power IS NOT NULL AND pw.power <> ''
     ORDER BY p.system_id64, pw.power;

    IF full_snapshot THEN
        DELETE FROM v3_state.system_state t
         WHERE (t.system_updated_at <= newest_ OR t.system_updated_at > horizon_)
           AND NOT EXISTS (SELECT 1 FROM picked_ p WHERE p.system_id64 = t.system_id64);
        GET DIAGNOSTICS removed_ = ROW_COUNT;
    END IF;

    INSERT INTO v3_state.load_log (
        source, full_snapshot, staged_rows, staged_systems, systems_written,
        systems_unchanged, systems_removed, newest_record_at
    ) VALUES (
        source_label, full_snapshot, staged_rows_, staged_systems_, written_,
        staged_systems_ - written_, removed_, newest_
    );

    DROP TABLE picked_;
    DROP TABLE written_ids_;

    RETURN QUERY SELECT staged_rows_, staged_systems_, written_,
                        staged_systems_ - written_, removed_, newest_;
END
$apply$;

COMMENT ON FUNCTION v3_state.apply_stage(boolean, text) IS
    'Merge v3_state.system_stage into the live state tables: newest record wins; a full snapshot also removes systems that are no longer present.';

COMMIT;
