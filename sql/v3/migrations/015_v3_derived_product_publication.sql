-- Attach independently validated Finder products to a PUBLISHED Ratings V4
-- generation and expose them only after an explicit, audited publication.
-- This migration is additive to the applied 006 lifecycle migration.
BEGIN;

ALTER TABLE v3_meta.derived_product
    ADD COLUMN published_at timestamptz;

CREATE TABLE v3_meta.derived_product_publication_audit (
    derived_generation_id uuid NOT NULL,
    product_code text NOT NULL,
    product_version text NOT NULL,
    manifest_sha256 bytea NOT NULL CHECK (octet_length(manifest_sha256) = 32),
    published_at timestamptz NOT NULL,
    actor text NOT NULL CHECK (btrim(actor) <> ''),
    PRIMARY KEY (derived_generation_id, product_code),
    FOREIGN KEY (derived_generation_id, product_code)
        REFERENCES v3_meta.derived_product
);

CREATE OR REPLACE FUNCTION v3_meta.guard_derived_product()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    base_state text;
    current_generation uuid;
BEGIN
    -- Registration, finalization, publication and pointer flips share one lock.
    PERFORM pg_advisory_xact_lock(764003001);

    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'derived product manifests are retained';
    END IF;

    SELECT lifecycle_state INTO base_state
      FROM v3_meta.derived_generation
     WHERE derived_generation_id = NEW.derived_generation_id
     FOR SHARE;
    IF base_state IS NULL THEN
        RAISE EXCEPTION 'unknown derived generation %', NEW.derived_generation_id;
    END IF;
    IF base_state NOT IN ('BUILDING', 'VALIDATING', 'READY', 'PUBLISHED') THEN
        RAISE EXCEPTION 'derived products cannot change while generation is %', base_state;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.lifecycle_state <> 'BUILDING'
           OR NEW.validation_receipt IS NOT NULL
           OR NEW.validation_sha256 IS NOT NULL
           OR NEW.validated_at IS NOT NULL
           OR NEW.failed_at IS NOT NULL
           OR NEW.failure IS NOT NULL
           OR NEW.published_at IS NOT NULL THEN
            RAISE EXCEPTION
                'new derived products must start BUILDING without terminal or publication evidence';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.published_at IS DISTINCT FROM OLD.published_at THEN
        IF ROW(NEW.derived_generation_id, NEW.product_code, NEW.product_version,
               NEW.lifecycle_state, NEW.manifest, NEW.manifest_sha256,
               NEW.expected_rows, NEW.validation_receipt, NEW.validation_sha256,
               NEW.created_at, NEW.validated_at, NEW.failed_at, NEW.failure)
           IS DISTINCT FROM
           ROW(OLD.derived_generation_id, OLD.product_code, OLD.product_version,
               OLD.lifecycle_state, OLD.manifest, OLD.manifest_sha256,
               OLD.expected_rows, OLD.validation_receipt, OLD.validation_sha256,
               OLD.created_at, OLD.validated_at, OLD.failed_at, OLD.failure) THEN
            RAISE EXCEPTION 'product publication cannot change any other column';
        END IF;
        IF OLD.published_at IS NOT NULL OR NEW.published_at IS NULL THEN
            RAISE EXCEPTION 'derived product publication is one-way';
        END IF;

        SELECT derived_generation_id INTO current_generation
          FROM v3_meta.current_derived_generation
         WHERE singleton;
        IF current_generation IS DISTINCT FROM NEW.derived_generation_id
           OR base_state <> 'PUBLISHED' THEN
            RAISE EXCEPTION 'only a product on the current PUBLISHED generation can be published';
        END IF;
        IF NEW.lifecycle_state <> 'READY'
           OR NEW.validation_receipt->>'status' IS DISTINCT FROM 'VERIFIED' THEN
            RAISE EXCEPTION 'published derived product requires READY/VERIFIED evidence';
        END IF;
        IF NEW.published_at IS DISTINCT FROM transaction_timestamp() THEN
            RAISE EXCEPTION 'product publication timestamp must equal transaction_timestamp()';
        END IF;
        RETURN NEW;
    END IF;

    IF ROW(NEW.derived_generation_id, NEW.product_code, NEW.product_version,
           NEW.manifest, NEW.manifest_sha256, NEW.expected_rows, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.derived_generation_id, OLD.product_code, OLD.product_version,
           OLD.manifest, OLD.manifest_sha256, OLD.expected_rows, OLD.created_at) THEN
        RAISE EXCEPTION 'derived product manifest identity is immutable';
    END IF;
    IF OLD.lifecycle_state <> 'BUILDING'
       OR NEW.lifecycle_state NOT IN ('READY', 'FAILED') THEN
        RAISE EXCEPTION 'invalid derived product lifecycle transition';
    END IF;
    IF NEW.lifecycle_state = 'READY' AND
       (NEW.validation_receipt IS NULL
        OR NEW.validation_receipt->>'status' IS DISTINCT FROM 'VERIFIED'
        OR NEW.validation_sha256 IS NULL
        OR NEW.validated_at IS NULL
        OR NEW.failed_at IS NOT NULL
        OR NEW.failure IS NOT NULL) THEN
        RAISE EXCEPTION 'READY derived product requires a VERIFIED validation receipt';
    END IF;
    IF NEW.lifecycle_state = 'FAILED' AND
       (NEW.failed_at IS NULL OR NEW.failure IS NULL OR btrim(NEW.failure) = '') THEN
        RAISE EXCEPTION 'FAILED derived product requires failure evidence';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION v3_meta.require_derived_product_publication_audit()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.published_at IS NULL THEN
        RETURN NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM v3_meta.derived_product_publication_audit a
         WHERE a.derived_generation_id = NEW.derived_generation_id
           AND a.product_code = NEW.product_code
           AND a.product_version = NEW.product_version
           AND a.manifest_sha256 = NEW.manifest_sha256
           AND a.published_at = NEW.published_at
    ) THEN
        RAISE EXCEPTION 'published derived product requires an exact audit row';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM v3_meta.current_derived_generation c
          JOIN v3_meta.derived_generation d USING (derived_generation_id)
         WHERE c.singleton
           AND c.derived_generation_id = NEW.derived_generation_id
           AND d.lifecycle_state = 'PUBLISHED'
    ) THEN
        RAISE EXCEPTION 'published derived product base is not the current PUBLISHED generation';
    END IF;
    RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER derived_product_publication_has_audit
AFTER UPDATE OF published_at ON v3_meta.derived_product
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
WHEN (NEW.published_at IS DISTINCT FROM OLD.published_at)
EXECUTE FUNCTION v3_meta.require_derived_product_publication_audit();

CREATE FUNCTION v3_meta.require_valid_derived_product_publication_audit()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM v3_meta.derived_product p
         WHERE p.derived_generation_id = NEW.derived_generation_id
           AND p.product_code = NEW.product_code
           AND p.product_version = NEW.product_version
           AND p.manifest_sha256 = NEW.manifest_sha256
           AND p.published_at = NEW.published_at
    ) THEN
        RAISE EXCEPTION 'product publication audit does not match the published product';
    END IF;
    RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER valid_derived_product_publication_audit
AFTER INSERT ON v3_meta.derived_product_publication_audit
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION v3_meta.require_valid_derived_product_publication_audit();

CREATE FUNCTION v3_meta.reject_derived_product_publication_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'derived product publication audit is insert-only';
END
$$;

CREATE TRIGGER immutable_derived_product_publication_audit_update
BEFORE UPDATE ON v3_meta.derived_product_publication_audit
FOR EACH STATEMENT
EXECUTE FUNCTION v3_meta.reject_derived_product_publication_audit_mutation();
CREATE TRIGGER immutable_derived_product_publication_audit_delete
BEFORE DELETE ON v3_meta.derived_product_publication_audit
FOR EACH STATEMENT
EXECUTE FUNCTION v3_meta.reject_derived_product_publication_audit_mutation();
CREATE TRIGGER immutable_derived_product_publication_audit_truncate
BEFORE TRUNCATE ON v3_meta.derived_product_publication_audit
FOR EACH STATEMENT
EXECUTE FUNCTION v3_meta.reject_derived_product_publication_audit_mutation();

CREATE FUNCTION v3_meta.publish_derived_product(
    generation_id_ uuid, product_code_ text, actor_ text
) RETURNS timestamptz
LANGUAGE plpgsql
AS $$
DECLARE
    publication_time timestamptz := transaction_timestamp();
    product_ v3_meta.derived_product%ROWTYPE;
BEGIN
    IF actor_ IS NULL OR btrim(actor_) = '' THEN
        RAISE EXCEPTION 'product publication needs an actor';
    END IF;

    PERFORM pg_advisory_xact_lock(764003001);
    IF NOT EXISTS (
        SELECT 1
          FROM v3_meta.current_derived_generation c
          JOIN v3_meta.derived_generation d USING (derived_generation_id)
         WHERE c.singleton
           AND c.derived_generation_id = generation_id_
           AND d.lifecycle_state = 'PUBLISHED'
    ) THEN
        RAISE EXCEPTION 'product publication requires the current PUBLISHED generation';
    END IF;

    SELECT * INTO product_
      FROM v3_meta.derived_product
     WHERE derived_generation_id = generation_id_
       AND product_code = product_code_
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown derived product % on generation %',
            product_code_, generation_id_;
    END IF;
    IF product_.lifecycle_state <> 'READY'
       OR product_.validation_receipt->>'status' IS DISTINCT FROM 'VERIFIED' THEN
        RAISE EXCEPTION 'derived product % is not READY/VERIFIED', product_code_;
    END IF;
    IF product_.published_at IS NOT NULL THEN
        RAISE EXCEPTION 'derived product % is already published', product_code_;
    END IF;

    UPDATE v3_meta.derived_product
       SET published_at = publication_time
     WHERE derived_generation_id = generation_id_
       AND product_code = product_code_;
    INSERT INTO v3_meta.derived_product_publication_audit (
        derived_generation_id, product_code, product_version, manifest_sha256,
        published_at, actor
    ) VALUES (
        generation_id_, product_code_, product_.product_version,
        product_.manifest_sha256, publication_time, actor_
    );
    RETURN publication_time;
END
$$;

-- Products may be built and validated while their immutable ratings base is
-- already PUBLISHED. Product rows themselves remain writable only in BUILDING.
CREATE OR REPLACE FUNCTION v3_derived.guard_system_search_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE identifier uuid; base_state text; product_state text;
BEGIN
    FOR identifier IN SELECT DISTINCT derived_generation_id FROM changed_rows ORDER BY 1 LOOP
        SELECT lifecycle_state INTO base_state
          FROM v3_meta.derived_generation
         WHERE derived_generation_id = identifier
         FOR SHARE;
        IF base_state NOT IN ('BUILDING', 'VALIDATING', 'READY', 'PUBLISHED') THEN
            RAISE EXCEPTION 'system_search generation % is immutable in state %',
                identifier, base_state;
        END IF;

        SELECT lifecycle_state INTO product_state
          FROM v3_meta.derived_product
         WHERE derived_generation_id = identifier
           AND product_code = 'system_search'
         FOR SHARE;
        IF product_state IS DISTINCT FROM 'BUILDING' THEN
            RAISE EXCEPTION 'system_search product % is not BUILDING', identifier;
        END IF;
    END LOOP;
    RETURN NULL;
END
$$;

CREATE OR REPLACE FUNCTION v3_derived.guard_system_archetype_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE identifier uuid; base_state text; product_state text;
BEGIN
    FOR identifier IN SELECT DISTINCT derived_generation_id FROM changed_rows ORDER BY 1 LOOP
        SELECT lifecycle_state INTO base_state
          FROM v3_meta.derived_generation
         WHERE derived_generation_id = identifier
         FOR SHARE;
        IF base_state NOT IN ('BUILDING', 'VALIDATING', 'READY', 'PUBLISHED') THEN
            RAISE EXCEPTION 'system_archetype generation % is immutable in state %',
                identifier, base_state;
        END IF;
        SELECT lifecycle_state INTO product_state
          FROM v3_meta.derived_product
         WHERE derived_generation_id = identifier
           AND product_code = 'system_archetype'
         FOR SHARE;
        IF product_state IS DISTINCT FROM 'BUILDING' THEN
            RAISE EXCEPTION 'system_archetype product % is not BUILDING', identifier;
        END IF;
    END LOOP;
    RETURN NULL;
END
$$;

-- READY is not enough: application views expose only explicitly published
-- products on the current PUBLISHED base generation.
CREATE OR REPLACE VIEW v3_app.system_search AS
SELECT s.*
FROM v3_meta.current_derived_generation c
JOIN v3_meta.derived_generation d USING (derived_generation_id)
JOIN v3_meta.derived_product p USING (derived_generation_id)
JOIN v3_derived.system_search s USING (derived_generation_id)
WHERE d.lifecycle_state = 'PUBLISHED'
  AND p.product_code = 'system_search'
  AND p.lifecycle_state = 'READY'
  AND p.published_at IS NOT NULL;

CREATE OR REPLACE VIEW v3_app.system_archetype AS
SELECT
    s.*,
    (s.paradise_confidence_ppm / 1000000.0)::double precision AS paradise_confidence,
    (s.mining_hub_confidence_ppm / 1000000.0)::double precision AS mining_hub_confidence,
    (s.manufacturing_hub_confidence_ppm / 1000000.0)::double precision
        AS manufacturing_hub_confidence,
    (s.megacomplex_confidence_ppm / 1000000.0)::double precision AS megacomplex_confidence,
    (s.research_hub_confidence_ppm / 1000000.0)::double precision AS research_hub_confidence,
    (s.stronghold_confidence_ppm / 1000000.0)::double precision AS stronghold_confidence,
    (s.population_capital_confidence_ppm / 1000000.0)::double precision
        AS population_capital_confidence,
    (s.flexible_confidence_ppm / 1000000.0)::double precision AS flexible_confidence
FROM v3_meta.current_derived_generation c
JOIN v3_meta.derived_generation d USING (derived_generation_id)
JOIN v3_meta.derived_product p USING (derived_generation_id)
JOIN v3_derived.system_archetype s USING (derived_generation_id)
WHERE d.lifecycle_state = 'PUBLISHED'
  AND p.product_code = 'system_archetype'
  AND p.lifecycle_state = 'READY'
  AND p.published_at IS NOT NULL;

CREATE OR REPLACE VIEW v3_app.system_archetype_summary AS
SELECT s.derived_generation_id, s.system_id64,
       s.primary_archetype, s.secondary_archetype,
       s.best_colony_potential, s.best_tier,
       (s.archetype_confidence_ppm / 1000000.0)::double precision AS archetype_confidence,
       s.weighted_potential, s.computed_at
FROM v3_meta.current_derived_generation c
JOIN v3_meta.derived_generation d USING (derived_generation_id)
JOIN v3_meta.derived_product p USING (derived_generation_id)
JOIN v3_derived.system_archetype s USING (derived_generation_id)
WHERE d.lifecycle_state = 'PUBLISHED'
  AND p.product_code = 'system_archetype'
  AND p.lifecycle_state = 'READY'
  AND p.published_at IS NOT NULL;

-- One implementation of the two flip rules is used by the publisher and the
-- deferred pointer-table contract.
CREATE FUNCTION v3_meta.enforce_finder_product_flip_rules(
    outgoing_generation_ uuid, incoming_generation_ uuid
) RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    IF outgoing_generation_ IS NULL THEN
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM v3_meta.derived_product outgoing
         WHERE outgoing.derived_generation_id = outgoing_generation_
           AND outgoing.product_code IN ('system_search', 'system_archetype')
           AND outgoing.published_at IS NOT NULL
           AND NOT EXISTS (
               SELECT 1
                 FROM v3_meta.derived_product incoming
                WHERE incoming.derived_generation_id = incoming_generation_
                  AND incoming.product_code = outgoing.product_code
                  AND incoming.lifecycle_state = 'READY'
                  AND incoming.validation_receipt->>'status' = 'VERIFIED'
                  AND incoming.published_at IS NOT NULL
           )
    ) THEN
        RAISE EXCEPTION 'published Finder product lacks an incoming published counterpart';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM v3_meta.derived_product outgoing
         WHERE outgoing.derived_generation_id = outgoing_generation_
           AND (outgoing.lifecycle_state = 'BUILDING'
                OR (outgoing.lifecycle_state = 'READY'
                    AND outgoing.published_at IS NULL))
           AND NOT EXISTS (
               SELECT 1
                 FROM v3_meta.derived_product incoming
                WHERE incoming.derived_generation_id = incoming_generation_
                  AND incoming.product_code = outgoing.product_code
                  AND incoming.lifecycle_state = 'READY'
                  AND incoming.validation_receipt->>'status' = 'VERIFIED'
                  AND incoming.published_at = transaction_timestamp()
           )
    ) THEN
        RAISE EXCEPTION 'outgoing generation has an unfinished derived product';
    END IF;
END
$$;

CREATE FUNCTION v3_meta.guard_current_derived_generation_row()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    canonical_generation uuid;
    candidate_state text;
    candidate_canonical uuid;
    candidate_validation jsonb;
    old_state text;
BEGIN
    PERFORM pg_advisory_xact_lock(764003001);

    IF TG_OP = 'INSERT' THEN
        IF EXISTS (SELECT 1 FROM v3_meta.current_derived_generation) THEN
            RAISE EXCEPTION 'current derived generation singleton already exists';
        END IF;
        IF NEW.publication_sequence <> 1 THEN
            RAISE EXCEPTION 'first derived publication sequence must be 1';
        END IF;
    ELSE
        IF NEW.derived_generation_id = OLD.derived_generation_id THEN
            RAISE EXCEPTION 'derived publication target must change';
        END IF;
        IF NEW.publication_sequence <> OLD.publication_sequence + 1 THEN
            RAISE EXCEPTION 'derived publication sequence must increment by one';
        END IF;
        SELECT lifecycle_state INTO old_state
          FROM v3_meta.derived_generation
         WHERE derived_generation_id = OLD.derived_generation_id
         FOR UPDATE;
        IF old_state IS DISTINCT FROM 'PUBLISHED' THEN
            RAISE EXCEPTION 'outgoing derived generation is not PUBLISHED';
        END IF;
    END IF;

    SELECT generation_id INTO canonical_generation
      FROM v3_meta.current_canonical_generation
     WHERE singleton
     FOR SHARE;
    SELECT lifecycle_state, canonical_generation_id, validation_receipt
      INTO candidate_state, candidate_canonical, candidate_validation
      FROM v3_meta.derived_generation
     WHERE derived_generation_id = NEW.derived_generation_id
     FOR UPDATE;
    IF candidate_state NOT IN ('READY', 'RETIRED')
       OR candidate_canonical IS DISTINCT FROM canonical_generation
       OR candidate_validation->>'status' IS DISTINCT FROM 'VERIFIED' THEN
        RAISE EXCEPTION 'candidate is not validated for current canonical generation';
    END IF;
    IF NEW.published_at IS DISTINCT FROM transaction_timestamp() THEN
        RAISE EXCEPTION 'derived publication timestamp must equal transaction_timestamp()';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER guard_current_derived_generation_insert
BEFORE INSERT ON v3_meta.current_derived_generation
FOR EACH ROW
EXECUTE FUNCTION v3_meta.guard_current_derived_generation_row();
CREATE TRIGGER guard_current_derived_generation_update
BEFORE UPDATE ON v3_meta.current_derived_generation
FOR EACH ROW
EXECUTE FUNCTION v3_meta.guard_current_derived_generation_row();

CREATE FUNCTION v3_meta.require_valid_current_derived_generation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    canonical_generation uuid;
BEGIN
    SELECT generation_id INTO canonical_generation
      FROM v3_meta.current_canonical_generation
     WHERE singleton;
    IF NOT EXISTS (
        SELECT 1
          FROM v3_meta.derived_generation d
         WHERE d.derived_generation_id = NEW.derived_generation_id
           AND d.canonical_generation_id = canonical_generation
           AND d.lifecycle_state = 'PUBLISHED'
           AND d.validation_receipt->>'status' = 'VERIFIED'
           AND d.published_at = NEW.published_at
    ) THEN
        RAISE EXCEPTION 'current derived generation is not PUBLISHED/VERIFIED for current canonical';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.derived_generation_id = OLD.derived_generation_id
           OR NEW.publication_sequence <> OLD.publication_sequence + 1 THEN
            RAISE EXCEPTION 'invalid derived publication pointer transition';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM v3_meta.derived_generation
             WHERE derived_generation_id = OLD.derived_generation_id
               AND lifecycle_state = 'RETIRED'
        ) THEN
            RAISE EXCEPTION 'outgoing derived generation was not retired';
        END IF;
    ELSE
        IF NEW.publication_sequence <> 1 THEN
            RAISE EXCEPTION 'first derived publication sequence must be 1';
        END IF;
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM v3_meta.derived_publication_audit a
         WHERE a.publication_sequence = NEW.publication_sequence
           AND a.from_generation_id IS NOT DISTINCT FROM
               CASE WHEN TG_OP = 'UPDATE' THEN OLD.derived_generation_id ELSE NULL END
           AND a.to_generation_id = NEW.derived_generation_id
           AND a.canonical_generation_id = canonical_generation
           AND a.published_at = NEW.published_at
           AND btrim(a.actor) <> ''
           AND btrim(a.reason) <> ''
    ) THEN
        RAISE EXCEPTION 'derived publication pointer requires an exact audit row';
    END IF;

    PERFORM v3_meta.enforce_finder_product_flip_rules(
        CASE WHEN TG_OP = 'UPDATE' THEN OLD.derived_generation_id ELSE NULL END,
        NEW.derived_generation_id
    );
    RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER valid_current_derived_generation_insert
AFTER INSERT ON v3_meta.current_derived_generation
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION v3_meta.require_valid_current_derived_generation();
CREATE CONSTRAINT TRIGGER valid_current_derived_generation_update
AFTER UPDATE ON v3_meta.current_derived_generation
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION v3_meta.require_valid_current_derived_generation();

CREATE FUNCTION v3_meta.reject_current_derived_generation_removal()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'current derived generation pointer cannot be removed';
END
$$;

CREATE TRIGGER reject_current_derived_generation_delete
BEFORE DELETE ON v3_meta.current_derived_generation
FOR EACH STATEMENT
EXECUTE FUNCTION v3_meta.reject_current_derived_generation_removal();
CREATE TRIGGER reject_current_derived_generation_truncate
BEFORE TRUNCATE ON v3_meta.current_derived_generation
FOR EACH STATEMENT
EXECUTE FUNCTION v3_meta.reject_current_derived_generation_removal();

DROP FUNCTION v3_meta.publish_derived_generation(
    uuid, uuid, bigint, uuid, bigint, text, text
);

CREATE FUNCTION v3_meta.publish_derived_generation(
    target_ uuid, expected_current_ uuid, expected_sequence_ bigint,
    expected_canonical_ uuid, expected_canonical_sequence_ bigint,
    actor_ text, reason_ text, product_codes_ text[]
) RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    candidate_ v3_meta.derived_generation%ROWTYPE;
    current_ uuid;
    sequence_ bigint;
    canonical_ uuid;
    canonical_sequence_ bigint;
    product_code_ text;
    publication_time timestamptz := transaction_timestamp();
BEGIN
    IF actor_ IS NULL OR btrim(actor_) = ''
       OR reason_ IS NULL OR btrim(reason_) = '' THEN
        RAISE EXCEPTION 'publication needs actor and reason';
    END IF;
    IF product_codes_ IS NULL
       OR EXISTS (SELECT 1 FROM unnest(product_codes_) code WHERE code IS NULL OR btrim(code) = '')
       OR cardinality(product_codes_) <>
          (SELECT count(DISTINCT code) FROM unnest(product_codes_) code) THEN
        RAISE EXCEPTION 'publication product codes must be a distinct non-null array';
    END IF;

    PERFORM pg_advisory_xact_lock(764003001);

    SELECT generation_id, publication_sequence
      INTO canonical_, canonical_sequence_
      FROM v3_meta.current_canonical_generation
     WHERE singleton
     FOR SHARE;
    IF canonical_ IS DISTINCT FROM expected_canonical_
       OR canonical_sequence_ IS DISTINCT FROM expected_canonical_sequence_ THEN
        RAISE EXCEPTION 'canonical publication changed';
    END IF;

    SELECT derived_generation_id, publication_sequence
      INTO current_, sequence_
      FROM v3_meta.current_derived_generation
     WHERE singleton
     FOR UPDATE;
    IF current_ IS DISTINCT FROM expected_current_
       OR COALESCE(sequence_, 0) <> expected_sequence_ THEN
        RAISE EXCEPTION 'derived publication changed';
    END IF;

    SELECT * INTO candidate_
      FROM v3_meta.derived_generation
     WHERE derived_generation_id = target_
     FOR UPDATE;
    IF NOT FOUND
       OR candidate_.lifecycle_state NOT IN ('READY', 'RETIRED')
       OR candidate_.canonical_generation_id <> canonical_
       OR candidate_.validation_receipt->>'status' IS DISTINCT FROM 'VERIFIED' THEN
        RAISE EXCEPTION 'candidate is not validated for current canonical generation';
    END IF;
    IF target_ IS NOT DISTINCT FROM current_ THEN
        RAISE EXCEPTION 'derived publication target must change';
    END IF;

    -- Verify every product the caller asks to publish before changing the
    -- pointer. Unnamed products may remain BUILDING: migration 015 deliberately
    -- permits their build to continue after the ratings base is PUBLISHED.
    IF EXISTS (
        SELECT code
          FROM unnest(product_codes_) code
        EXCEPT
        SELECT p.product_code
          FROM v3_meta.derived_product p
         WHERE p.derived_generation_id = target_
           AND p.lifecycle_state = 'READY'
           AND p.validation_receipt->>'status' = 'VERIFIED'
           AND p.published_at IS NULL
    ) THEN
        RAISE EXCEPTION 'named derived product is not READY/VERIFIED and unpublished';
    END IF;

    sequence_ := COALESCE(sequence_, 0) + 1;
    IF current_ IS NULL THEN
        INSERT INTO v3_meta.current_derived_generation (
            singleton, derived_generation_id, publication_sequence, published_at
        ) VALUES (true, target_, sequence_, publication_time);
    ELSE
        UPDATE v3_meta.current_derived_generation
           SET derived_generation_id = target_,
               publication_sequence = sequence_,
               published_at = publication_time
         WHERE singleton;
    END IF;

    UPDATE v3_meta.derived_generation
       SET lifecycle_state = 'RETIRED'
     WHERE derived_generation_id = current_;
    UPDATE v3_meta.derived_generation
       SET lifecycle_state = 'PUBLISHED', published_at = publication_time
     WHERE derived_generation_id = target_;
    INSERT INTO v3_meta.derived_publication_audit (
        publication_sequence, from_generation_id, to_generation_id,
        canonical_generation_id, published_at, actor, reason
    ) VALUES (
        sequence_, current_, target_, canonical_, publication_time, actor_, reason_
    );

    FOREACH product_code_ IN ARRAY product_codes_ LOOP
        PERFORM v3_meta.publish_derived_product(target_, product_code_, actor_);
    END LOOP;

    PERFORM v3_meta.enforce_finder_product_flip_rules(current_, target_);
    RETURN sequence_;
END
$$;

COMMIT;
