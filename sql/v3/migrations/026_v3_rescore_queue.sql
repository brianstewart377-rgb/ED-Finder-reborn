-- 026: a list of systems whose galaxy rows changed (v3_state.rescore_queue).
--
-- The nightly galaxy update replaces the stored rows of every system that
-- Spansh's file shows differently from what we hold. Scores and Find rows are
-- worked out from those rows, so each such system has to be worked out again.
-- This table is the hand-over between the two jobs:
--
--   * the galaxy update adds a system here in the same transaction that
--     changes its rows, so a system can never change without being listed;
--   * the re-score job takes systems from here, rebuilds their scores and
--     Find row, and removes them.
--
-- A system is listed once. If it changes again before it has been re-scored,
-- the tables that changed are added to its entry.
--
-- Additive only: it creates one new table and touches nothing else.
-- Safe to run more than once.
BEGIN;

CREATE TABLE IF NOT EXISTS v3_state.rescore_queue (
    system_id64 bigint PRIMARY KEY,
    -- Which galaxy tables changed for this system, for example
    -- {bodies,body_signal_current}. A system that only gained or lost a fleet
    -- carrier lists {stations} and needs its Find row rebuilt, not its scores.
    changed_tables text[] NOT NULL CHECK (cardinality(changed_tables) > 0),
    first_queued_at timestamptz NOT NULL DEFAULT now(),
    last_queued_at timestamptz NOT NULL DEFAULT now(),
    -- The galaxy update run that last changed the system.
    source_run_id uuid NOT NULL REFERENCES v3_source.source_run (source_run_id)
);
COMMENT ON TABLE v3_state.rescore_queue IS
    'Systems whose galaxy rows changed and whose scores and Find row have not been rebuilt yet.';

COMMIT;
