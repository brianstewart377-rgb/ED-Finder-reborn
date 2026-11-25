-- 033: the body counts Find filters on, in v3_state.economy_facts, and the
-- ranked lists for Find's search across the whole band
-- (v3_state.economy_top and v3_state.economy_top_build).
--
-- "Ratings rebuild — design", build step 5, second half. Agreed with the owner
-- on 25 November 2026.
--
-- 1. The counts. Find's body filters ("at least one Earth-like world", "no
--    more than two gas giants") are 19 counts kept on each system's row of the
--    search table. A search that sorts the facts table had to look every
--    system up there, one by one, to apply a filter. Each facts row now
--    carries the same 19 counts, copied from the search row whenever the facts
--    row is written, so a filter is answered from the row already in hand.
--    About 40 bytes a row: the table goes from about 12 GB to about 13.5 GB.
--
--    The rows already filled are given their counts here, by one UPDATE from
--    the search table, so the first fill does NOT start again. That statement
--    is the slow part: it writes every row filled so far once more. It runs
--    after the table change has been committed, so nothing waits behind it,
--    and it only touches rows that have no counts yet: run again, it carries
--    on with those. A count above 32,767 is stored as 32,767.
--
-- 2. The lists. Across the band there is no point to search around, and the
--    facts table has 41 million rows: sorting them all for one search would
--    take minutes on the Pi. So after each scoring run the best systems of
--    the whole band are written down, in order, as 36 lists:
--
--      1 to 7     each economy, strongest;
--      8 to 14    each economy, purest;
--      15 to 35   each pair;
--      36         no economy chosen: most ground slots, then most bodies.
--
--    A row is one place in one list: the system, the number it was placed by,
--    and the facts a search shows or filters on. Each rebuild writes a whole
--    new set under a new build_id and then marks that build current, in one
--    transaction, so a search always reads one complete set; the set before
--    it is deleted afterwards. economy_top_build says which version of the
--    rules a set was made from and how deep its lists go.
--
-- Apply it AFTER deploying the build that writes the counts. That build
-- writes its rows without counts for as long as this has not been applied,
-- and this gives them theirs, so a nightly run between the two does no harm.
-- The other way round does: a build from before this one would write rows
-- with no counts, or leave a changed system its old ones, after this has run.
--
-- Best applied when no nightly run is under way: one that is writing rows at
-- the same moment can make this stop with an error (nothing is lost; run it
-- again). Nothing that exists is dropped or emptied. Safe to run more than
-- once.

BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE v3_state.economy_facts
    ADD COLUMN IF NOT EXISTS landable_count smallint,
    ADD COLUMN IF NOT EXISTS terraformable_count smallint,
    ADD COLUMN IF NOT EXISTS elw_count smallint,
    ADD COLUMN IF NOT EXISTS ww_count smallint,
    ADD COLUMN IF NOT EXISTS ammonia_count smallint,
    ADD COLUMN IF NOT EXISTS gas_giant_count smallint,
    ADD COLUMN IF NOT EXISTS hmc_count smallint,
    ADD COLUMN IF NOT EXISTS metal_rich_count smallint,
    ADD COLUMN IF NOT EXISTS rocky_count smallint,
    ADD COLUMN IF NOT EXISTS rocky_ice_count smallint,
    ADD COLUMN IF NOT EXISTS icy_count smallint,
    ADD COLUMN IF NOT EXISTS neutron_count smallint,
    ADD COLUMN IF NOT EXISTS black_hole_count smallint,
    ADD COLUMN IF NOT EXISTS white_dwarf_count smallint,
    ADD COLUMN IF NOT EXISTS other_star_count smallint,
    ADD COLUMN IF NOT EXISTS ring_count smallint,
    ADD COLUMN IF NOT EXISTS walkable_count smallint,
    ADD COLUMN IF NOT EXISTS bio_signal_total smallint,
    ADD COLUMN IF NOT EXISTS geo_signal_total smallint;

COMMENT ON COLUMN v3_state.economy_facts.landable_count IS
    'This and the 18 columns after it: the counts Find filters on, copied from the system''s '
    'search row when this row was written. NULL where the system had no search row.';

CREATE TABLE IF NOT EXISTS v3_state.economy_top_build (
    build_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- The facts' version (the rulebook's release and the layout of the numbers).
    version text NOT NULL CHECK (version <> ''),
    -- How many places each list has at most.
    depth integer NOT NULL CHECK (depth > 0),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz CHECK (finished_at >= started_at),
    -- The one set that searches read.
    is_current boolean NOT NULL DEFAULT false,
    CHECK (NOT is_current OR finished_at IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS economy_top_build_current_idx
    ON v3_state.economy_top_build (is_current) WHERE is_current;
COMMENT ON TABLE v3_state.economy_top_build IS
    'Each set of Find''s ranked lists across the band: the facts version it was made from, '
    'how deep its lists go, and which set searches read now.';

CREATE TABLE IF NOT EXISTS v3_state.economy_top (
    build_id bigint NOT NULL REFERENCES v3_state.economy_top_build ON DELETE CASCADE,
    list_no smallint NOT NULL CHECK (list_no BETWEEN 1 AND 36),
    -- 1 is the best system of the list.
    place integer NOT NULL CHECK (place >= 1),
    system_id64 bigint NOT NULL,
    -- What the list is in order of: the economy's or pair's number, or for
    -- list 36 the system's ground slots.
    number bigint NOT NULL CHECK (number > 0),
    bodies_known smallint NOT NULL CHECK (bodies_known >= 0),
    ground_slots smallint NOT NULL CHECK (ground_slots >= 0),
    landable_count smallint,
    terraformable_count smallint,
    elw_count smallint,
    ww_count smallint,
    ammonia_count smallint,
    gas_giant_count smallint,
    hmc_count smallint,
    metal_rich_count smallint,
    rocky_count smallint,
    rocky_ice_count smallint,
    icy_count smallint,
    neutron_count smallint,
    black_hole_count smallint,
    white_dwarf_count smallint,
    other_star_count smallint,
    ring_count smallint,
    walkable_count smallint,
    bio_signal_total smallint,
    geo_signal_total smallint,
    PRIMARY KEY (build_id, list_no, place),
    UNIQUE (build_id, list_no, system_id64)
);
COMMENT ON TABLE v3_state.economy_top IS
    'Find''s ranked lists across the band: for each economy (strongest and purest), each pair, '
    'and no economy, the best systems within 500 ly in order, rebuilt after each scoring run.';

COMMIT;

-- The rows filled before this migration: their counts, from the search table.
-- The table is measured first, so that PostgreSQL knows every row is waiting
-- for its counts and reads the two tables side by side, and again afterwards,
-- so that searches know how common each count is.
ANALYZE v3_state.economy_facts;

-- The copy takes as long as it takes, whatever time limit the session has.
SET statement_timeout = 0;

UPDATE v3_state.economy_facts f
   SET landable_count = LEAST(s.landable_count, 32767),
       terraformable_count = LEAST(s.terraformable_count, 32767),
       elw_count = LEAST(s.elw_count, 32767),
       ww_count = LEAST(s.ww_count, 32767),
       ammonia_count = LEAST(s.ammonia_count, 32767),
       gas_giant_count = LEAST(s.gas_giant_count, 32767),
       hmc_count = LEAST(s.hmc_count, 32767),
       metal_rich_count = LEAST(s.metal_rich_count, 32767),
       rocky_count = LEAST(s.rocky_count, 32767),
       rocky_ice_count = LEAST(s.rocky_ice_count, 32767),
       icy_count = LEAST(s.icy_count, 32767),
       neutron_count = LEAST(s.neutron_count, 32767),
       black_hole_count = LEAST(s.black_hole_count, 32767),
       white_dwarf_count = LEAST(s.white_dwarf_count, 32767),
       other_star_count = LEAST(s.other_star_count, 32767),
       ring_count = LEAST(s.ring_count, 32767),
       walkable_count = LEAST(s.walkable_count, 32767),
       bio_signal_total = LEAST(s.bio_signal_total, 32767),
       geo_signal_total = LEAST(s.geo_signal_total, 32767)
  FROM v3_app.system_search s
 WHERE s.system_id64 = f.system_id64
   AND f.landable_count IS NULL;

ANALYZE v3_state.economy_facts;

RESET statement_timeout;
