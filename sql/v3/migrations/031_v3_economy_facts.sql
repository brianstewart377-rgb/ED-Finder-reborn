-- 031: Find's facts for each system within 500 ly (v3_state.economy_facts),
-- and a record of each full fill of them (v3_state.economy_facts_fill).
--
-- The ratings rebuild ("Ratings rebuild — design", build step 4) ranks
-- systems only on what the game's rules and their known bodies show. For each
-- system in reach (v3_state.system_reach, migration 028), the nightly scoring
-- (apps/importer/src/spansh_delta/economy_facts.py) keeps one row:
--
--   * bodies_known  its stars, planets and moons that we hold: Find takes it
--                   as the system's orbital room (decision 10);
--   * ground_slots  the predicted ground slots of all its bodies together;
--   * strongest,    for each of the seven economies, one number for where the
--     purest        system's best body stands in that order, 0 where no body
--                   suits; a system with no known bodies has none;
--   * pairs         the same for each of the 21 pairs;
--   * worked_out_at when the rows they were worked out from were read.
--
-- A fill works out every system in reach once, from where it stopped, a
-- little after each nightly run; economy_facts_fill says how far it has got
-- and which version of the rules it used, so that numbers of two versions are
-- never taken for one.
--
-- Additive only: two new tables. Nothing that exists is read or changed, and
-- nothing reads these yet. Safe to run more than once.

BEGIN;

CREATE TABLE IF NOT EXISTS v3_state.economy_facts (
    system_id64 bigint PRIMARY KEY,
    bodies_known smallint NOT NULL CHECK (bodies_known >= 0),
    ground_slots smallint NOT NULL CHECK (ground_slots >= 0),
    strongest bigint[] CHECK (array_ndims(strongest) = 1 AND cardinality(strongest) = 7
                              AND 0 <= ALL (strongest)),
    purest bigint[] CHECK (array_ndims(purest) = 1 AND cardinality(purest) = 7
                           AND 0 <= ALL (purest)),
    pairs bigint[] CHECK (array_ndims(pairs) = 1 AND cardinality(pairs) = 21
                          AND 0 <= ALL (pairs)),
    worked_out_at timestamptz NOT NULL,
    -- The numbers are there exactly when a body is known.
    CHECK ((strongest IS NULL) = (bodies_known = 0)
           AND (purest IS NULL) = (bodies_known = 0)
           AND (pairs IS NULL) = (bodies_known = 0))
);
COMMENT ON TABLE v3_state.economy_facts IS
    'Find''s facts for each system within 500 ly: known bodies, predicted ground slots, '
    'and where its best bodies stand in each economy''s and pair''s order.';

CREATE TABLE IF NOT EXISTS v3_state.economy_facts_fill (
    fill_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- The rulebook's release and the layout of the numbers.
    version text NOT NULL CHECK (version <> ''),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz CHECK (finished_at >= started_at),
    -- The last system it has worked out; it carries on after it.
    last_system_id64 bigint,
    systems bigint NOT NULL DEFAULT 0 CHECK (systems >= 0)
);
COMMENT ON TABLE v3_state.economy_facts_fill IS
    'Each full fill of v3_state.economy_facts: the version of the rules it used and how far it has got.';

COMMIT;
