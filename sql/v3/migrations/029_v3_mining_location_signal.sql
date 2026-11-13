-- 029: a new kind of signal on a body, "planetary mining location".
--
-- Spansh's files have listed it since the game's mining update of September
-- 2026: a count, for each planet, of the places on its surface that can be
-- mined, written as "$PlanetaryMiningLocation_Name;" beside the biological and
-- geological counts. The importer did not know the word, so it left the signal
-- out: it was in 99,693 systems of the 7-day file applied on 13 November 2026.
--
-- Signals are stored by number, and the numbers are listed in
-- v3_vocab.signal_type. This adds number 8 for the new signal. The importer
-- that knows the word (apps/importer/src/v3_spansh/adapter.py) writes it from
-- then on, for every system a file lists.
--
-- Apply this BEFORE deploying a build whose importer knows the word. The
-- galaxy update checks that every number it knows is in this list, and refuses
-- to run until it is. An older build is not troubled by the extra row.
--
-- What it does: adds one row to v3_vocab.signal_type. If number 8 or the code
-- is already taken by something else, it stops and changes nothing.
--
-- Additive only. Safe to run more than once.
BEGIN;

SET LOCAL lock_timeout = '15s';

DO $migration$
DECLARE
    new_id constant smallint := 8;
    -- The word without its "$" and ";", in lower case: the same rule the
    -- import uses for every code in this list.
    new_code constant text := 'planetarymininglocation_name';
    new_name constant text := 'Planetary Mining Location';
    taken text;
    added integer;
BEGIN
    SELECT public_code INTO taken FROM v3_vocab.signal_type
     WHERE signal_type_id = new_id AND public_code <> new_code;
    IF FOUND THEN
        RAISE EXCEPTION '029: signal type % is already "%", not "%". Nothing was changed.',
            new_id, taken, new_code;
    END IF;

    SELECT signal_type_id::text INTO taken FROM v3_vocab.signal_type
     WHERE public_code = new_code AND signal_type_id <> new_id;
    IF FOUND THEN
        RAISE EXCEPTION '029: "%" is already signal type %, not %. Nothing was changed.',
            new_code, taken, new_id;
    END IF;

    INSERT INTO v3_vocab.signal_type (signal_type_id, public_code, display_name)
    VALUES (new_id, new_code, new_name)
    ON CONFLICT (signal_type_id) DO NOTHING;
    GET DIAGNOSTICS added = ROW_COUNT;

    IF added = 1 THEN
        RAISE NOTICE '029: added signal type % (%).', new_id, new_name;
    ELSE
        RAISE NOTICE '029: signal type % (%) was already there. Nothing was changed.',
            new_id, new_name;
    END IF;
END
$migration$;

COMMIT;
