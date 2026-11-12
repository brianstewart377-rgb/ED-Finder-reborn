-- 028: which systems are within 500 ly and 250 ly of an inhabited system
-- ("reach", in the v3_state schema).
--
-- The Raspberry Pi keeps scores and Find rows only for the systems within
-- 500 ly of an inhabited system, and Find shows those within 250 ly unless it
-- is asked for more (docs/pi-move.md, "One band, not two"). These tables say
-- which systems those are:
--
--   * reach_anchor   the inhabited systems reach was last worked out from
--                    (population above zero in v3_state.system_state), with
--                    their positions, so a new system can be measured against
--                    the same ones;
--   * system_reach   every system within 500 ly of one of them, and whether
--                    it is within 250 ly too. A system without a row is
--                    further away;
--   * reach_log      each time reach was worked out afresh, and what came of
--                    it.
--
-- apps/importer/src/spansh_delta/reach.py fills them: afresh once a week, and
-- each night for the systems the galaxy update has just brought in. Where
-- these tables exist the nightly scoring gives scores only to systems in
-- system_reach, and will not start until reach has been worked out once.
--
-- Additive only: three new tables. Nothing that exists is read or changed.
-- Safe to run more than once.
BEGIN;

CREATE TABLE IF NOT EXISTS v3_state.reach_anchor (
    system_id64 bigint PRIMARY KEY,
    -- The system's position in light years, as a point.
    position cube NOT NULL
);
COMMENT ON TABLE v3_state.reach_anchor IS
    'The inhabited systems reach was last worked out from, with their positions.';
CREATE INDEX IF NOT EXISTS reach_anchor_position
    ON v3_state.reach_anchor USING gist (position);

CREATE TABLE IF NOT EXISTS v3_state.system_reach (
    system_id64 bigint PRIMARY KEY,
    -- Within 250 ly of an inhabited system, as well as within 500 ly.
    within_250 boolean NOT NULL
);
COMMENT ON TABLE v3_state.system_reach IS
    'Every system within 500 ly of an inhabited system (reach_anchor), and whether it is within 250 ly too.';

CREATE TABLE IF NOT EXISTS v3_state.reach_log (
    reach_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    worked_out_at timestamptz NOT NULL DEFAULT now(),
    -- How many inhabited systems it was worked out from.
    anchors bigint NOT NULL CHECK (anchors >= 0),
    -- Systems within 500 ly, and of those, within 250 ly.
    systems bigint NOT NULL CHECK (systems >= 0),
    within_250 bigint NOT NULL CHECK (within_250 BETWEEN 0 AND systems),
    -- Compared with the time before: systems that came within 500 ly, that
    -- are no longer within it, and that moved across the 250 ly line.
    added bigint NOT NULL CHECK (added >= 0),
    removed bigint NOT NULL CHECK (removed >= 0),
    crossed_250 bigint NOT NULL CHECK (crossed_250 >= 0),
    seconds numeric NOT NULL CHECK (seconds >= 0)
);
COMMENT ON TABLE v3_state.reach_log IS
    'Each time reach was worked out afresh: from how many inhabited systems, and what came of it.';

COMMIT;
