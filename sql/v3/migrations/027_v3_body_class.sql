-- 027: each body's class, in the galaxy tables (bodies.body_class).
--
-- A body's class is what Spansh calls its sub-type: "Earth-like world",
-- "Icy body", "M (Red dwarf) Star". The scores are worked out from it, and the
-- galaxy tables did not hold it: the first import left it out, and the scoring
-- read it from Spansh's file each time. So a system could be scored only while
-- that file was at hand, and a class that changed with nothing else changing
-- went unseen by the galaxy update.
--
-- The new column holds the class the way the scoring uses it:
--
--   * Spansh's sub-type, where it gives one;
--   * otherwise the body's plain type: star, planet, barycentre, belt_cluster;
--   * otherwise "Unknown".
--
-- NULL means "not recorded yet": the row was last written before this column
-- existed. The class of such a body is in its score row
-- (v3_derived.body_mechanics.body_class), which holds the same thing. The
-- nightly galaxy update fills the column for every system it writes; nothing
-- here fills it for the rows that already exist, so this does not rewrite the
-- table. The copy to the Raspberry Pi fills every row.
--
-- What it does:
--
--   1. Adds the column to the bodies table of every galaxy that exists, with
--      a rule that a class is never blank and never longer than 128
--      characters (the limit the score tables put on the same value, so
--      nothing can be stored here that could not be scored). Adding a column
--      that may be empty changes no rows: it takes a moment, however large
--      the table. The rule is added without checking the existing rows,
--      which are all NULL.
--   2. Makes galaxies created from now on have the same column and rule, by
--      adding them to the function that creates a galaxy's tables.
--
-- Additive only. Safe to run more than once.
BEGIN;

-- Changing a table needs it to itself for a moment. If something is using it
-- (a long search), give up after a few seconds rather than queue everyone else
-- behind this; run it again when it is quiet.
SET LOCAL lock_timeout = '15s';

DO $migration$
DECLARE
    galaxy record;
    bodies regclass;
    definition text;
    galaxies integer := 0;
    -- The first rule of the bodies table in the function: the new column goes
    -- just before it, which is after the last column, as in step 1.
    first_rule constant text := 'CHECK (eccentricity IS NULL OR eccentricity BETWEEN 0 AND 1),';
    addition constant text :=
        'body_class text,' || E'\n            '
        || 'CONSTRAINT bodies_body_class_valid CHECK (body_class IS NULL OR '
        || '(btrim(body_class) <> '''' AND length(body_class) <= 128)),'
        || E'\n            ';
    note constant text :=
        'The body''s class as the scoring uses it: Spansh''s sub-type, or the plain type '
        '(star, planet, barycentre, belt_cluster) where Spansh gives none, or Unknown. '
        'NULL: not recorded yet (the row was last written before migration 027); the class '
        'is then in the body''s score row, v3_derived.body_mechanics.body_class.';
BEGIN
    -- 1. Every galaxy that exists.
    FOR galaxy IN
        SELECT g.relation_schema AS name
          FROM v3_meta.canonical_generation g
         WHERE to_regclass(format('%I.bodies', g.relation_schema)) IS NOT NULL
         ORDER BY 1
    LOOP
        bodies := format('%I.bodies', galaxy.name)::regclass;
        EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS body_class text', bodies);
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conrelid = bodies AND conname = 'bodies_body_class_valid') THEN
            EXECUTE format(
                'ALTER TABLE %s ADD CONSTRAINT bodies_body_class_valid CHECK (body_class IS NULL OR '
                '(btrim(body_class) <> '''' AND length(body_class) <= 128)) NOT VALID', bodies);
        END IF;
        EXECUTE format('COMMENT ON COLUMN %s.body_class IS %L', bodies, note);
        galaxies := galaxies + 1;
    END LOOP;

    -- 2. Galaxies created from now on.
    definition := pg_get_functiondef('v3_meta.create_canonical_generation_relations(uuid)'::regprocedure);
    IF position('bodies_body_class_valid' IN definition) = 0 THEN
        IF (length(definition) - length(replace(definition, first_rule, ''))) <> length(first_rule) THEN
            RAISE EXCEPTION '027: the function that creates a galaxy''s tables is not the one this '
                'migration was written for (expected the bodies table''s first rule exactly once). '
                'Nothing was changed.';
        END IF;
        EXECUTE replace(definition, first_rule, addition || first_rule);
    END IF;

    RAISE NOTICE '027 done: the body_class column is on the bodies table of each galaxy here '
        '(number of galaxies: %). Galaxies created from now on will have it too.', galaxies;
END
$migration$;

COMMIT;
