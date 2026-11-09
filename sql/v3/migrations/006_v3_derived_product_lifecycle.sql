-- Independent lifecycle and coverage receipts for derived products attached to
-- one Ratings V4 generation. This lets Search/Spatial products finish after the
-- base ratings generation reaches READY without weakening atomic publication.
BEGIN;

CREATE TABLE v3_meta.derived_product (
    derived_generation_id uuid NOT NULL REFERENCES v3_meta.derived_generation,
    product_code text NOT NULL CHECK(product_code ~ '^[a-z][a-z0-9_]{0,62}$'),
    product_version text NOT NULL CHECK(length(product_version) BETWEEN 1 AND 128),
    lifecycle_state text NOT NULL DEFAULT 'BUILDING'
        CHECK(lifecycle_state IN ('BUILDING','READY','FAILED')),
    manifest jsonb NOT NULL CHECK(jsonb_typeof(manifest)='object'),
    manifest_sha256 bytea NOT NULL CHECK(octet_length(manifest_sha256)=32),
    expected_rows bigint NOT NULL CHECK(expected_rows > 0),
    validation_receipt jsonb
        CHECK(validation_receipt IS NULL OR jsonb_typeof(validation_receipt)='object'),
    validation_sha256 bytea CHECK(validation_sha256 IS NULL OR octet_length(validation_sha256)=32),
    created_at timestamptz NOT NULL DEFAULT now(),
    validated_at timestamptz,
    failed_at timestamptz,
    failure text,
    PRIMARY KEY(derived_generation_id,product_code),
    CHECK(lifecycle_state <> 'READY' OR
          (validation_receipt IS NOT NULL AND validation_sha256 IS NOT NULL
           AND validated_at IS NOT NULL)),
    CHECK((lifecycle_state='FAILED')=(failed_at IS NOT NULL))
);

COMMENT ON TABLE v3_meta.derived_product IS
    'Independently validated products attached to one derived generation. Publication requires every registered product to be READY/VERIFIED.';

CREATE FUNCTION v3_meta.guard_derived_product()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE base_state text;
BEGIN
    -- The publication function takes the same lock. Registration/finalization
    -- therefore cannot race a generation pointer swap.
    PERFORM pg_advisory_xact_lock(764003001);

    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'derived product manifests are retained';
    END IF;

    SELECT lifecycle_state INTO base_state
      FROM v3_meta.derived_generation
     WHERE derived_generation_id=NEW.derived_generation_id
     FOR SHARE;
    IF base_state IS NULL THEN
        RAISE EXCEPTION 'unknown derived generation %',NEW.derived_generation_id;
    END IF;
    IF base_state NOT IN ('BUILDING','VALIDATING','READY') THEN
        RAISE EXCEPTION 'derived products cannot change while generation is %',base_state;
    END IF;

    IF TG_OP='INSERT' THEN
        IF NEW.lifecycle_state<>'BUILDING'
           OR NEW.validation_receipt IS NOT NULL
           OR NEW.validation_sha256 IS NOT NULL
           OR NEW.validated_at IS NOT NULL
           OR NEW.failed_at IS NOT NULL
           OR NEW.failure IS NOT NULL THEN
            RAISE EXCEPTION 'new derived products must start BUILDING without a terminal receipt';
        END IF;
        RETURN NEW;
    END IF;

    IF ROW(NEW.derived_generation_id,NEW.product_code,NEW.product_version,
           NEW.manifest,NEW.manifest_sha256,NEW.expected_rows,NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.derived_generation_id,OLD.product_code,OLD.product_version,
           OLD.manifest,OLD.manifest_sha256,OLD.expected_rows,OLD.created_at) THEN
        RAISE EXCEPTION 'derived product manifest identity is immutable';
    END IF;
    IF OLD.lifecycle_state<>'BUILDING'
       OR NEW.lifecycle_state NOT IN ('READY','FAILED') THEN
        RAISE EXCEPTION 'invalid derived product lifecycle transition';
    END IF;
    IF NEW.lifecycle_state='READY' AND
       (NEW.validation_receipt IS NULL
        OR NEW.validation_receipt->>'status'<>'VERIFIED'
        OR NEW.validation_sha256 IS NULL
        OR NEW.validated_at IS NULL
        OR NEW.failed_at IS NOT NULL
        OR NEW.failure IS NOT NULL) THEN
        RAISE EXCEPTION 'READY derived product requires a VERIFIED validation receipt';
    END IF;
    IF NEW.lifecycle_state='FAILED' AND
       (NEW.failed_at IS NULL OR NEW.failure IS NULL OR btrim(NEW.failure)='') THEN
        RAISE EXCEPTION 'FAILED derived product requires failure evidence';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER immutable_derived_product
BEFORE INSERT OR UPDATE OR DELETE ON v3_meta.derived_product
FOR EACH ROW EXECUTE FUNCTION v3_meta.guard_derived_product();

CREATE TRIGGER immutable_derived_product_truncate
BEFORE TRUNCATE ON v3_meta.derived_product
FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.guard_generation_write();

CREATE TABLE v3_derived.search_build_chunk (
    derived_generation_id uuid NOT NULL,
    chunk_ordinal bigint NOT NULL CHECK(chunk_ordinal >= 0),
    product_code text NOT NULL DEFAULT 'system_search'
        CHECK(product_code='system_search'),
    projection_version text NOT NULL CHECK(length(projection_version) BETWEEN 1 AND 128),
    source_projection_sha256 bytea NOT NULL CHECK(octet_length(source_projection_sha256)=32),
    content_sha256 bytea NOT NULL CHECK(octet_length(content_sha256)=32),
    systems integer NOT NULL CHECK(systems BETWEEN 1 AND 1000),
    completed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(derived_generation_id,chunk_ordinal),
    FOREIGN KEY(derived_generation_id,chunk_ordinal)
        REFERENCES v3_derived.build_chunk(derived_generation_id,chunk_ordinal)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(derived_generation_id,product_code)
        REFERENCES v3_meta.derived_product(derived_generation_id,product_code)
        DEFERRABLE INITIALLY DEFERRED
);

COMMENT ON TABLE v3_derived.search_build_chunk IS
    'Per-Ratings-chunk Search projection receipt. Source seal binds the immutable Ratings input plus Search product manifest; content seal binds the materialized Search rows.';

-- Migration 004 attached system_search directly to the base generation lifecycle.
-- Search is now an independently validated product, so replace only that table's
-- triggers. Other 004 relations keep the original generation guard unchanged.
DROP TRIGGER immutable_insert ON v3_derived.system_search;
DROP TRIGGER immutable_update_old ON v3_derived.system_search;
DROP TRIGGER immutable_update_new ON v3_derived.system_search;
DROP TRIGGER immutable_delete ON v3_derived.system_search;
DROP TRIGGER immutable_truncate ON v3_derived.system_search;

CREATE FUNCTION v3_derived.guard_system_search_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE identifier uuid; base_state text; product_state text;
BEGIN
    FOR identifier IN SELECT DISTINCT derived_generation_id FROM changed_rows ORDER BY 1 LOOP
        SELECT lifecycle_state INTO base_state
          FROM v3_meta.derived_generation
         WHERE derived_generation_id=identifier
         FOR SHARE;
        IF base_state NOT IN ('BUILDING','VALIDATING','READY') THEN
            RAISE EXCEPTION 'system_search generation % is immutable in state %',
                identifier,base_state;
        END IF;

        SELECT lifecycle_state INTO product_state
          FROM v3_meta.derived_product
         WHERE derived_generation_id=identifier
           AND product_code='system_search'
         FOR SHARE;
        IF product_state IS DISTINCT FROM 'BUILDING' THEN
            RAISE EXCEPTION 'system_search product % is not BUILDING',identifier;
        END IF;
    END LOOP;
    RETURN NULL;
END
$$;

CREATE FUNCTION v3_derived.reject_system_search_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'system_search rows and receipts are insert-only';
END
$$;

CREATE TRIGGER product_insert
AFTER INSERT ON v3_derived.system_search
REFERENCING NEW TABLE AS changed_rows
FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.guard_system_search_insert();

CREATE TRIGGER product_update
BEFORE UPDATE ON v3_derived.system_search
FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.reject_system_search_mutation();

CREATE TRIGGER product_delete
BEFORE DELETE ON v3_derived.system_search
FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.reject_system_search_mutation();

CREATE TRIGGER product_truncate
BEFORE TRUNCATE ON v3_derived.system_search
FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.reject_system_search_mutation();

CREATE TRIGGER product_receipt_insert
AFTER INSERT ON v3_derived.search_build_chunk
REFERENCING NEW TABLE AS changed_rows
FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.guard_system_search_insert();

CREATE TRIGGER product_receipt_update
BEFORE UPDATE ON v3_derived.search_build_chunk
FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.reject_system_search_mutation();

CREATE TRIGGER product_receipt_delete
BEFORE DELETE ON v3_derived.search_build_chunk
FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.reject_system_search_mutation();

CREATE TRIGGER product_receipt_truncate
BEFORE TRUNCATE ON v3_derived.search_build_chunk
FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.reject_system_search_mutation();

-- Preserve the 003 compare-and-swap publication contract and add one rule:
-- every product registered on the candidate must independently be READY/VERIFIED.
CREATE OR REPLACE FUNCTION v3_meta.publish_derived_generation(
    target_ uuid, expected_current_ uuid, expected_sequence_ bigint,
    expected_canonical_ uuid, expected_canonical_sequence_ bigint,
    actor_ text, reason_ text
) RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE candidate_ v3_meta.derived_generation%ROWTYPE;
        current_ uuid; sequence_ bigint; canonical_ uuid; canonical_sequence_ bigint;
BEGIN
    IF actor_ IS NULL OR length(trim(actor_))=0
       OR reason_ IS NULL OR length(trim(reason_))=0 THEN
        RAISE EXCEPTION 'publication needs actor and reason';
    END IF;

    PERFORM pg_advisory_xact_lock(764003001);

    SELECT generation_id,publication_sequence INTO canonical_,canonical_sequence_
      FROM v3_meta.current_canonical_generation
     WHERE singleton
     FOR SHARE;
    IF canonical_ IS DISTINCT FROM expected_canonical_
       OR canonical_sequence_ IS DISTINCT FROM expected_canonical_sequence_ THEN
        RAISE EXCEPTION 'canonical publication changed';
    END IF;

    SELECT derived_generation_id,publication_sequence INTO current_,sequence_
      FROM v3_meta.current_derived_generation
     WHERE singleton
     FOR UPDATE;
    IF current_ IS DISTINCT FROM expected_current_
       OR COALESCE(sequence_,0)<>expected_sequence_ THEN
        RAISE EXCEPTION 'derived publication changed';
    END IF;

    SELECT * INTO candidate_
      FROM v3_meta.derived_generation
     WHERE derived_generation_id=target_
     FOR UPDATE;
    IF NOT FOUND
       OR candidate_.lifecycle_state NOT IN ('READY','RETIRED')
       OR candidate_.canonical_generation_id<>canonical_
       OR candidate_.validation_receipt->>'status'<>'VERIFIED' THEN
        RAISE EXCEPTION 'candidate is not validated for current canonical generation';
    END IF;
    IF candidate_.validation_receipt->>'status' IS NULL THEN
        RAISE EXCEPTION 'candidate validation receipt missing';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM v3_meta.derived_product p
         WHERE p.derived_generation_id=target_
           AND (p.lifecycle_state<>'READY'
                OR p.validation_receipt->>'status' IS DISTINCT FROM 'VERIFIED')
    ) THEN
        RAISE EXCEPTION 'candidate has unverified derived products';
    END IF;

    sequence_ := COALESCE(sequence_,0)+1;
    UPDATE v3_meta.derived_generation
       SET lifecycle_state='RETIRED'
     WHERE derived_generation_id=current_;
    UPDATE v3_meta.derived_generation
       SET lifecycle_state='PUBLISHED',published_at=now()
     WHERE derived_generation_id=target_;
    INSERT INTO v3_meta.current_derived_generation
        VALUES(true,target_,sequence_,now())
    ON CONFLICT(singleton) DO UPDATE
        SET derived_generation_id=EXCLUDED.derived_generation_id,
            publication_sequence=EXCLUDED.publication_sequence,
            published_at=EXCLUDED.published_at;
    INSERT INTO v3_meta.derived_publication_audit
        VALUES(sequence_,current_,target_,canonical_,now(),actor_,reason_);
    RETURN sequence_;
END
$$;

COMMIT;
