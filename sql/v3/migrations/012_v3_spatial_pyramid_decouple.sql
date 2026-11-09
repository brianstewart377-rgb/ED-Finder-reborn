-- Decouple the spatial density pyramid from the ratings derived_generation.
-- The pyramid becomes an independently-published artifact keyed to the
-- canonical generation, with its own lifecycle and CAS publish pointer.
-- Clean-replaces the derived-keyed cell_summary + spatial_pyramid product path
-- (empty/unused on prod). Clusters (v3_spatial.cluster_*) are untouched.
BEGIN;

-- 1. Drop the derived-keyed pyramid storage (its migration-004 immutability
--    triggers drop with the table). cell_level (version/level keyed) is kept.
DROP TABLE IF EXISTS v3_spatial.cell_summary;

-- 2. Spatial generation: the build envelope, keyed to the canonical generation.
CREATE TABLE v3_spatial.spatial_generation (
    spatial_generation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_generation_id uuid NOT NULL
        REFERENCES v3_meta.canonical_generation(generation_id),
    pyramid_version text NOT NULL
        CHECK(pyramid_version ~ '^[a-z][a-z0-9_]{0,62}$'),
    lifecycle_state text NOT NULL DEFAULT 'BUILDING'
        CHECK(lifecycle_state IN
              ('BUILDING','VALIDATING','READY','PUBLISHED','RETIRED','FAILED')),
    expected_systems bigint NOT NULL CHECK(expected_systems > 0),
    validation_receipt jsonb
        CHECK(validation_receipt IS NULL OR jsonb_typeof(validation_receipt)='object'),
    validation_sha256 bytea CHECK(validation_sha256 IS NULL OR octet_length(validation_sha256)=32),
    created_at timestamptz NOT NULL DEFAULT now(),
    validated_at timestamptz,
    published_at timestamptz,
    failed_at timestamptz,
    failure text,
    UNIQUE(canonical_generation_id, pyramid_version),
    CHECK(lifecycle_state <> 'READY' OR
          (validation_receipt IS NOT NULL AND validation_sha256 IS NOT NULL
           AND validated_at IS NOT NULL)),
    CHECK((lifecycle_state='FAILED')=(failed_at IS NOT NULL))
);

COMMENT ON TABLE v3_spatial.spatial_generation IS
    'Independently-published spatial density pyramid keyed to one canonical generation. Decoupled from the ratings derived_generation lifecycle.';

-- 3. Cell summary: pure-density cells for a spatial generation.
CREATE TABLE v3_spatial.cell_summary (
    spatial_generation_id uuid NOT NULL REFERENCES v3_spatial.spatial_generation,
    spatial_pyramid_version text NOT NULL,
    level smallint NOT NULL,
    cell_key text NOT NULL,
    system_count bigint NOT NULL CHECK(system_count > 0),
    representative_system_id64 bigint NOT NULL,
    origin_x_ly double precision NOT NULL,
    origin_y_ly double precision NOT NULL,
    origin_z_ly double precision NOT NULL,
    centroid_x_ly double precision NOT NULL,
    centroid_y_ly double precision NOT NULL,
    centroid_z_ly double precision NOT NULL,
    PRIMARY KEY(spatial_generation_id, level, cell_key),
    FOREIGN KEY(spatial_pyramid_version, level)
        REFERENCES v3_spatial.cell_level(spatial_pyramid_version, level)
);
CREATE INDEX cell_summary_gen_level
    ON v3_spatial.cell_summary(spatial_generation_id, level);

COMMENT ON COLUMN v3_spatial.cell_summary.representative_system_id64 IS
    'Canonical id64 of the system nearest the cell data-centroid (tiebreak min(id64)); the density->real-star handoff for the map client.';

-- 4. Lifecycle guard on spatial_generation.
CREATE FUNCTION v3_spatial.guard_spatial_generation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(764004001);
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'spatial generations are retained';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.lifecycle_state<>'BUILDING'
           OR NEW.validation_receipt IS NOT NULL
           OR NEW.validated_at IS NOT NULL
           OR NEW.published_at IS NOT NULL
           OR NEW.failed_at IS NOT NULL THEN
            RAISE EXCEPTION 'new spatial generations must start BUILDING without receipts';
        END IF;
        RETURN NEW;
    END IF;
    -- UPDATE: validate the transition shape. Pointer integrity for
    -- PUBLISHED/RETIRED is enforced by publish_spatial_pyramid under the same
    -- advisory lock; here we only allow well-formed transitions.
    IF OLD.canonical_generation_id<>NEW.canonical_generation_id
       OR OLD.pyramid_version<>NEW.pyramid_version
       OR OLD.expected_systems<>NEW.expected_systems
       OR OLD.created_at<>NEW.created_at THEN
        RAISE EXCEPTION 'spatial generation identity is immutable';
    END IF;
    IF NOT (
        (OLD.lifecycle_state='BUILDING'  AND NEW.lifecycle_state IN ('VALIDATING','READY','FAILED'))
     OR (OLD.lifecycle_state='VALIDATING' AND NEW.lifecycle_state IN ('READY','FAILED'))
     OR (OLD.lifecycle_state='READY'      AND NEW.lifecycle_state IN ('PUBLISHED','RETIRED'))
     OR (OLD.lifecycle_state='PUBLISHED'  AND NEW.lifecycle_state='RETIRED')
     OR (OLD.lifecycle_state='RETIRED'    AND NEW.lifecycle_state='PUBLISHED')
    ) THEN
        RAISE EXCEPTION 'invalid spatial generation transition % -> %',
            OLD.lifecycle_state, NEW.lifecycle_state;
    END IF;
    IF NEW.lifecycle_state='READY' AND
       (NEW.validation_receipt IS NULL
        OR NEW.validation_receipt->>'status'<>'VERIFIED'
        OR NEW.validation_sha256 IS NULL
        OR NEW.validated_at IS NULL) THEN
        RAISE EXCEPTION 'READY spatial generation requires a VERIFIED validation receipt';
    END IF;
    IF NEW.lifecycle_state='FAILED' AND
       (NEW.failed_at IS NULL OR NEW.failure IS NULL OR btrim(NEW.failure)='') THEN
        RAISE EXCEPTION 'FAILED spatial generation requires failure evidence';
    END IF;
    IF NEW.lifecycle_state='PUBLISHED' AND NEW.published_at IS NULL THEN
        RAISE EXCEPTION 'PUBLISHED spatial generation requires published_at';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER guard_spatial_generation
BEFORE INSERT OR UPDATE OR DELETE ON v3_spatial.spatial_generation
FOR EACH ROW EXECUTE FUNCTION v3_spatial.guard_spatial_generation();

-- 5. Freeze cell_summary once its spatial generation is not mutable.
CREATE FUNCTION v3_spatial.guard_cell_summary()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE identifier uuid; base_state text;
BEGIN
    IF TG_OP='TRUNCATE' THEN
        RAISE EXCEPTION 'cell_summary is insert-only';
    END IF;
    FOR identifier IN
        SELECT DISTINCT spatial_generation_id FROM changed_rows ORDER BY 1
    LOOP
        SELECT lifecycle_state INTO base_state
          FROM v3_spatial.spatial_generation
         WHERE spatial_generation_id=identifier FOR SHARE;
        IF base_state NOT IN ('BUILDING','VALIDATING','READY') THEN
            RAISE EXCEPTION 'cell_summary for spatial generation % is immutable in state %',
                identifier, base_state;
        END IF;
    END LOOP;
    RETURN NULL;
END $$;

CREATE FUNCTION v3_spatial.reject_cell_summary_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'cell_summary rows are insert-only';
END $$;

CREATE TRIGGER cell_summary_insert
AFTER INSERT ON v3_spatial.cell_summary
REFERENCING NEW TABLE AS changed_rows
FOR EACH STATEMENT EXECUTE FUNCTION v3_spatial.guard_cell_summary();

CREATE TRIGGER cell_summary_update
BEFORE UPDATE ON v3_spatial.cell_summary
FOR EACH STATEMENT EXECUTE FUNCTION v3_spatial.reject_cell_summary_mutation();

CREATE TRIGGER cell_summary_delete
BEFORE DELETE ON v3_spatial.cell_summary
FOR EACH STATEMENT EXECUTE FUNCTION v3_spatial.reject_cell_summary_mutation();

CREATE TRIGGER cell_summary_truncate
BEFORE TRUNCATE ON v3_spatial.cell_summary
FOR EACH STATEMENT EXECUTE FUNCTION v3_spatial.reject_cell_summary_mutation();

-- 6. Publish pointer + audit + CAS function.
CREATE TABLE v3_spatial.current_spatial_generation (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    spatial_generation_id uuid NOT NULL REFERENCES v3_spatial.spatial_generation,
    publication_sequence bigint NOT NULL,
    published_at timestamptz NOT NULL
);

CREATE TABLE v3_spatial.spatial_publication_audit (
    publication_sequence bigint PRIMARY KEY,
    previous_spatial_generation_id uuid,
    published_spatial_generation_id uuid NOT NULL,
    canonical_generation_id uuid NOT NULL,
    published_at timestamptz NOT NULL,
    actor text NOT NULL CHECK(btrim(actor) <> ''),
    reason text NOT NULL CHECK(btrim(reason) <> '')
);

CREATE FUNCTION v3_spatial.publish_spatial_pyramid(
    target_ uuid, expected_current_ uuid, expected_sequence_ bigint,
    expected_canonical_ uuid, actor_ text, reason_ text
) RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE candidate_ v3_spatial.spatial_generation%ROWTYPE;
        current_ uuid; sequence_ bigint; canonical_ uuid;
BEGIN
    IF actor_ IS NULL OR btrim(actor_)='' OR reason_ IS NULL OR btrim(reason_)='' THEN
        RAISE EXCEPTION 'publication needs actor and reason';
    END IF;
    PERFORM pg_advisory_xact_lock(764004001);

    SELECT generation_id INTO canonical_
      FROM v3_meta.current_canonical_generation WHERE singleton FOR SHARE;
    IF canonical_ IS DISTINCT FROM expected_canonical_ THEN
        RAISE EXCEPTION 'canonical publication changed';
    END IF;

    SELECT spatial_generation_id, publication_sequence INTO current_, sequence_
      FROM v3_spatial.current_spatial_generation WHERE singleton FOR UPDATE;
    IF current_ IS DISTINCT FROM expected_current_
       OR COALESCE(sequence_,0)<>expected_sequence_ THEN
        RAISE EXCEPTION 'spatial publication changed';
    END IF;

    SELECT * INTO candidate_ FROM v3_spatial.spatial_generation
     WHERE spatial_generation_id=target_ FOR UPDATE;
    IF NOT FOUND
       OR candidate_.lifecycle_state NOT IN ('READY','RETIRED')
       OR candidate_.canonical_generation_id<>canonical_
       OR candidate_.validation_receipt->>'status'<>'VERIFIED' THEN
        RAISE EXCEPTION 'candidate is not a READY pyramid for the current canonical generation';
    END IF;

    sequence_ := COALESCE(sequence_,0)+1;
    IF current_ IS NOT NULL THEN
        UPDATE v3_spatial.spatial_generation SET lifecycle_state='RETIRED'
         WHERE spatial_generation_id=current_;
    END IF;
    UPDATE v3_spatial.spatial_generation
       SET lifecycle_state='PUBLISHED', published_at=now()
     WHERE spatial_generation_id=target_;
    INSERT INTO v3_spatial.current_spatial_generation
        VALUES(true, target_, sequence_, now())
    ON CONFLICT(singleton) DO UPDATE
        SET spatial_generation_id=EXCLUDED.spatial_generation_id,
            publication_sequence=EXCLUDED.publication_sequence,
            published_at=EXCLUDED.published_at;
    INSERT INTO v3_spatial.spatial_publication_audit
        VALUES(sequence_, current_, target_, canonical_, now(), actor_, reason_);
    RETURN sequence_;
END $$;

COMMIT;
