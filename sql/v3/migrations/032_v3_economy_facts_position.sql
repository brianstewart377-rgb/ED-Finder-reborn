-- 032: where each system is, in v3_state.economy_facts, so that Find can read
-- the systems near a point from that one table.
--
-- Find's new search near a system ("Ratings rebuild — design", build step 5)
-- sorts every system within the distance asked for by one of the table's
-- numbers. Found through the search table's spatial index and then looked up
-- here one by one, 250 ly around a system is a few hundred thousand rows
-- scattered over the whole table: too slow on the Pi when they are not in
-- memory. So each row now says where its system is:
--
--   * x_ly, y_ly, z_ly  the system's coordinates, as the galaxy tables have them;
--   * cell              which 32 ly cube of space it is in, as one number, made
--                       by the database from the coordinates.
--
-- A search asks for the cells its sphere touches (economy_facts_cell_idx).
-- When a fill finishes, the nightly scoring puts the rows in cell order on
-- disk (CLUSTER), so the systems of one part of space lie together and a
-- search reads a few stretches of the table. The fill record says when that
-- was done (tidied_at). The table keeps 15% of each page free, so that a
-- system's new row can go beside its old one and the order lasts.
--
-- The rows written so far have no coordinates, and nothing reads them yet, so
-- both tables are emptied; the first fill starts again with the next scoring
-- run. This happens only the first time: run again, this migration changes
-- nothing.
--
-- Apply it AFTER deploying the build that writes the coordinates: the build
-- before it cannot write to the changed table, and would fail the nightly run.

BEGIN;
SET LOCAL lock_timeout = '5s';

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
         WHERE attrelid = 'v3_state.economy_facts'::regclass
           AND attname = 'cell' AND NOT attisdropped
    ) THEN
        TRUNCATE v3_state.economy_facts, v3_state.economy_facts_fill RESTART IDENTITY;
        ALTER TABLE v3_state.economy_facts
            ADD COLUMN x_ly double precision NOT NULL
                CHECK (x_ly >= -131072 AND x_ly < 131072),
            ADD COLUMN y_ly double precision NOT NULL
                CHECK (y_ly >= -131072 AND y_ly < 131072),
            ADD COLUMN z_ly double precision NOT NULL
                CHECK (z_ly >= -131072 AND z_ly < 131072),
            -- 13 bits an axis: floor(coordinate / 32) + 4096 is 0 to 8191.
            ADD COLUMN cell bigint NOT NULL GENERATED ALWAYS AS (
                ((floor(z_ly / 32.0)::bigint + 4096) << 26)
                | ((floor(y_ly / 32.0)::bigint + 4096) << 13)
                | (floor(x_ly / 32.0)::bigint + 4096)
            ) STORED;
    END IF;
END
$migration$;

ALTER TABLE v3_state.economy_facts SET (fillfactor = 85);
CREATE INDEX IF NOT EXISTS economy_facts_cell_idx ON v3_state.economy_facts (cell);
ALTER TABLE v3_state.economy_facts_fill ADD COLUMN IF NOT EXISTS tidied_at timestamptz;

COMMENT ON COLUMN v3_state.economy_facts.cell IS
    'Which 32 ly cube of space the system is in: 13 bits each for z, y and x (floor(ly / 32) + 4096).';
COMMENT ON COLUMN v3_state.economy_facts_fill.tidied_at IS
    'When the rows were last put in cell order on disk (CLUSTER) after this fill finished.';

COMMIT;
