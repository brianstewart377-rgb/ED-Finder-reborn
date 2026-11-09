-- Ratings V4 derived storage. This migration never changes canonical relations.
-- Application through production requires the separately reviewed V3 migration operation.
BEGIN;
CREATE SCHEMA v3_derived;
CREATE SCHEMA v3_app;

CREATE TABLE v3_meta.derived_generation (
    derived_generation_id uuid PRIMARY KEY,
    canonical_generation_id uuid NOT NULL REFERENCES v3_meta.canonical_generation,
    canonical_publication_sequence bigint NOT NULL CHECK(canonical_publication_sequence > 0),
    generation_key text NOT NULL UNIQUE CHECK(generation_key ~ '^[a-z][a-z0-9_]{0,62}$'),
    mechanics_version text NOT NULL,
    scorer_version text NOT NULL,
    adapter_version text NOT NULL,
    lifecycle_state text NOT NULL DEFAULT 'BUILDING'
        CHECK(lifecycle_state IN ('BUILDING','VALIDATING','READY','PUBLISHED','RETIRED','FAILED')),
    manifest jsonb NOT NULL CHECK(jsonb_typeof(manifest)='object'),
    manifest_sha256 bytea NOT NULL CHECK(octet_length(manifest_sha256)=32),
    expected_systems bigint NOT NULL CHECK(expected_systems > 0),
    expected_bodies bigint NOT NULL CHECK(expected_bodies >= 0),
    source_receipt jsonb,
    validation_receipt jsonb,
    content_sha256 bytea CHECK(octet_length(content_sha256)=32),
    created_at timestamptz NOT NULL DEFAULT now(),
    validated_at timestamptz,
    published_at timestamptz,
    failed_at timestamptz,
    failure text,
    CHECK((lifecycle_state='FAILED')=(failed_at IS NOT NULL)),
    CHECK(lifecycle_state NOT IN ('READY','PUBLISHED','RETIRED') OR
          (validated_at IS NOT NULL AND validation_receipt IS NOT NULL
           AND content_sha256 IS NOT NULL AND source_receipt IS NOT NULL))
);

CREATE TABLE v3_meta.current_derived_generation (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    derived_generation_id uuid NOT NULL UNIQUE REFERENCES v3_meta.derived_generation,
    publication_sequence bigint NOT NULL CHECK(publication_sequence > 0),
    published_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION v3_meta.guard_derived_manifest() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'derived generation manifests are retained'; END IF;
    IF ROW(NEW.derived_generation_id,NEW.canonical_generation_id,NEW.canonical_publication_sequence,
           NEW.generation_key,NEW.mechanics_version,NEW.scorer_version,NEW.adapter_version,
           NEW.manifest,NEW.manifest_sha256,NEW.expected_systems,NEW.expected_bodies,NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.derived_generation_id,OLD.canonical_generation_id,OLD.canonical_publication_sequence,
           OLD.generation_key,OLD.mechanics_version,OLD.scorer_version,OLD.adapter_version,
           OLD.manifest,OLD.manifest_sha256,OLD.expected_systems,OLD.expected_bodies,OLD.created_at) THEN
        RAISE EXCEPTION 'derived manifest identity is immutable';
    END IF;
    IF NOT ((OLD.lifecycle_state='BUILDING' AND NEW.lifecycle_state IN ('VALIDATING','FAILED')) OR
            (OLD.lifecycle_state='VALIDATING' AND NEW.lifecycle_state IN ('READY','FAILED')) OR
            (OLD.lifecycle_state='READY' AND NEW.lifecycle_state='PUBLISHED') OR
            (OLD.lifecycle_state='PUBLISHED' AND NEW.lifecycle_state='RETIRED') OR
            (OLD.lifecycle_state='RETIRED' AND NEW.lifecycle_state='PUBLISHED')) THEN
        RAISE EXCEPTION 'invalid derived generation lifecycle transition';
    END IF;
    IF OLD.lifecycle_state<>'BUILDING' AND
       ROW(NEW.source_receipt,NEW.content_sha256) IS DISTINCT FROM ROW(OLD.source_receipt,OLD.content_sha256) THEN
        RAISE EXCEPTION 'derived source/content seal is immutable';
    END IF;
    IF OLD.lifecycle_state NOT IN ('BUILDING','VALIDATING') AND
       ROW(NEW.validation_receipt,NEW.validated_at) IS DISTINCT FROM ROW(OLD.validation_receipt,OLD.validated_at) THEN
        RAISE EXCEPTION 'derived validation receipt is immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER immutable_derived_manifest BEFORE UPDATE OR DELETE ON v3_meta.derived_generation
FOR EACH ROW EXECUTE FUNCTION v3_meta.guard_derived_manifest();
CREATE TABLE v3_meta.derived_publication_audit (
    publication_sequence bigint PRIMARY KEY,
    from_generation_id uuid REFERENCES v3_meta.derived_generation,
    to_generation_id uuid NOT NULL REFERENCES v3_meta.derived_generation,
    canonical_generation_id uuid NOT NULL REFERENCES v3_meta.canonical_generation,
    published_at timestamptz NOT NULL DEFAULT now(),
    actor text NOT NULL,
    reason text NOT NULL
);

CREATE TABLE v3_derived.build_chunk (
    derived_generation_id uuid NOT NULL REFERENCES v3_meta.derived_generation,
    chunk_ordinal bigint NOT NULL CHECK(chunk_ordinal >= 0),
    source_projection_sha256 bytea NOT NULL CHECK(octet_length(source_projection_sha256)=32),
    canonical_input_sha256 bytea NOT NULL CHECK(octet_length(canonical_input_sha256)=32),
    content_sha256 bytea NOT NULL CHECK(octet_length(content_sha256)=32),
    systems integer NOT NULL CHECK(systems BETWEEN 1 AND 1000),
    canonical_bodies integer NOT NULL CHECK(canonical_bodies BETWEEN 0 AND 100000),
    physical_bodies integer NOT NULL CHECK(physical_bodies BETWEEN 0 AND canonical_bodies),
    eligible_opportunities integer NOT NULL CHECK(eligible_opportunities >= 0),
    completed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(derived_generation_id,chunk_ordinal)
);

-- The vector has exactly the frozen ECONOMIES order. A view exposes one row per
-- economy; storage avoids repeating row headers, generation and system identity
-- seven times for almost two hundred million systems. Ratios are exact ten-
-- thousandths, matching the frozen scorer's four-decimal result.
CREATE FUNCTION v3_derived.valid_vector(values_ smallint[], maximum_ integer,
                                        nullable_ boolean DEFAULT false)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT COALESCE(array_ndims(values_)=1 AND array_lower(values_,1)=1
      AND cardinality(values_)=7 AND NOT EXISTS(
        SELECT 1 FROM unnest(values_) v WHERE
          (v IS NULL AND NOT nullable_) OR v < 0 OR v > maximum_),false)
$$;

CREATE TABLE v3_derived.system_rating_vector (
    derived_generation_id uuid NOT NULL,
    system_id64 bigint NOT NULL CHECK(system_id64 >= 0),
    chunk_ordinal bigint NOT NULL,
    loaded_body_count integer NOT NULL CHECK(loaded_body_count >= 0),
    physical_body_count integer NOT NULL CHECK(physical_body_count BETWEEN 0 AND loaded_body_count),
    subtype_projection_sha256 bytea NOT NULL CHECK(octet_length(subtype_projection_sha256)=32),
    potential smallint[] NOT NULL CHECK(v3_derived.valid_vector(potential,100)),
    quality smallint[] NOT NULL CHECK(v3_derived.valid_vector(quality,100,true)),
    quality_min smallint[] NOT NULL CHECK(v3_derived.valid_vector(quality_min,100)),
    quality_max smallint[] NOT NULL CHECK(v3_derived.valid_vector(quality_max,100)),
    completeness smallint[] NOT NULL CHECK(v3_derived.valid_vector(completeness,10000)),
    confidence smallint[] NOT NULL CHECK(v3_derived.valid_vector(confidence,10000)),
    PRIMARY KEY(derived_generation_id,system_id64),
    FOREIGN KEY(derived_generation_id,chunk_ordinal) REFERENCES v3_derived.build_chunk
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX system_rating_chunk_idx ON v3_derived.system_rating_vector(derived_generation_id,chunk_ordinal);

CREATE FUNCTION v3_derived.valid_quality(quality_ smallint[], minimum_ smallint[], maximum_ smallint[])
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT NOT EXISTS(SELECT 1 FROM generate_series(1,7) i WHERE
      minimum_[i] > maximum_[i] OR
      (minimum_[i]=maximum_[i] AND (quality_[i] IS NULL OR quality_[i]<>minimum_[i])) OR
      (minimum_[i]<maximum_[i] AND quality_[i] IS NOT NULL))
$$;
ALTER TABLE v3_derived.system_rating_vector ADD CHECK(v3_derived.valid_quality(quality,quality_min,quality_max));

-- A compact typed mechanics input per physical body. Generation/source and
-- canonical body identity provide provenance once, rather than copying long
-- JSON source strings into every economy/feature row. Explanations are replayed
-- from these immutable inputs with the exact frozen scorer.
CREATE TABLE v3_derived.body_mechanics (
    derived_generation_id uuid NOT NULL,
    system_id64 bigint NOT NULL,
    body_pk bigint NOT NULL CHECK(body_pk >= 0),
    source_body_id64 bigint NOT NULL CHECK(source_body_id64 >= 0),
    body_class text NOT NULL CHECK(length(body_class) BETWEEN 1 AND 128),
    spectral_class text CHECK(length(spectral_class) <= 128),
    luminosity_class text CHECK(length(luminosity_class) <= 128),
    rings boolean,
    biologicals boolean,
    geologicals boolean,
    volcanism boolean,
    terraformable boolean,
    tidally_locked boolean,
    is_main_star boolean,
    reserve_level text CHECK(reserve_level IN ('depleted','low','common','major','pristine')),
    usable_ground_opportunity boolean,
    subtype_present boolean NOT NULL,
    subtype_observed_at timestamptz,
    PRIMARY KEY(derived_generation_id,system_id64,body_pk),
    FOREIGN KEY(derived_generation_id,system_id64) REFERENCES v3_derived.system_rating_vector
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE v3_derived.economy_opportunity (
    derived_generation_id uuid NOT NULL,
    system_id64 bigint NOT NULL,
    body_pk bigint NOT NULL,
    economy_ordinal smallint NOT NULL CHECK(economy_ordinal BETWEEN 1 AND 7),
    native boolean NOT NULL,
    modifier boolean NOT NULL,
    local_score smallint NOT NULL CHECK(local_score BETWEEN 0 AND 100),
    specialisation_quality smallint CHECK(specialisation_quality BETWEEN 0 AND 100),
    specialisation_min smallint NOT NULL CHECK(specialisation_min BETWEEN 0 AND 100),
    specialisation_max smallint NOT NULL CHECK(specialisation_max BETWEEN specialisation_min AND 100),
    completeness double precision NOT NULL CHECK(completeness BETWEEN 0 AND 1),
    confidence double precision NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    CHECK(native OR modifier),
    CHECK((specialisation_min=specialisation_max AND specialisation_quality IS NOT NULL
           AND specialisation_quality=specialisation_min) OR
          (specialisation_min<specialisation_max AND specialisation_quality IS NULL)),
    PRIMARY KEY(derived_generation_id,system_id64,body_pk,economy_ordinal),
    FOREIGN KEY(derived_generation_id,system_id64,body_pk) REFERENCES v3_derived.body_mechanics
        DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE v3_derived.economy_opportunity IS
    'Eligible opportunities only. Ineligible/unknown candidate evidence remains in body_mechanics and contributes to all-system completeness; explanations replay all candidates.';

-- Statement-level guards acquire generation locks once per COPY statement.
-- They must remain enabled: replica mode would defeat immutable generation
-- enforcement and deferred referential checks.
CREATE FUNCTION v3_derived.guard_generation_write() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE identifier uuid; state_ text;
BEGIN
    IF TG_OP='TRUNCATE' THEN RAISE EXCEPTION 'derived relations cannot be truncated'; END IF;
    FOR identifier IN EXECUTE 'SELECT DISTINCT derived_generation_id FROM changed_rows ORDER BY 1' LOOP
        SELECT lifecycle_state INTO state_ FROM v3_meta.derived_generation
            WHERE derived_generation_id=identifier FOR SHARE;
        IF state_ IS DISTINCT FROM 'BUILDING' THEN
            RAISE EXCEPTION 'derived generation % is immutable in state %',identifier,state_;
        END IF;
    END LOOP;
    RETURN NULL;
END $$;
DO $$
DECLARE relation_ text;
BEGIN
    FOREACH relation_ IN ARRAY ARRAY['build_chunk','system_rating_vector','body_mechanics','economy_opportunity'] LOOP
        EXECUTE format('CREATE TRIGGER immutable_insert AFTER INSERT ON v3_derived.%I REFERENCING NEW TABLE AS changed_rows FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.guard_generation_write()',relation_);
        EXECUTE format('CREATE TRIGGER immutable_update_old AFTER UPDATE ON v3_derived.%I REFERENCING OLD TABLE AS changed_rows FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.guard_generation_write()',relation_);
        EXECUTE format('CREATE TRIGGER immutable_update_new AFTER UPDATE ON v3_derived.%I REFERENCING NEW TABLE AS changed_rows FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.guard_generation_write()',relation_);
        EXECUTE format('CREATE TRIGGER immutable_delete AFTER DELETE ON v3_derived.%I REFERENCING OLD TABLE AS changed_rows FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.guard_generation_write()',relation_);
        EXECUTE format('CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON v3_derived.%I FOR EACH STATEMENT EXECUTE FUNCTION v3_derived.guard_generation_write()',relation_);
    END LOOP;
END $$;

CREATE VIEW v3_derived.system_economy_rating AS
SELECT v.derived_generation_id,v.system_id64,
       (ARRAY['Agriculture','Refinery','Industrial','HighTech','Military','Tourism','Extraction'])[i] AS economy,
       v.potential[i] AS potential_score,v.quality[i] AS specialisation_quality,
       v.quality_min[i] AS specialisation_quality_min,v.quality_max[i] AS specialisation_quality_max,
       v.completeness[i]::double precision/10000 AS evidence_completeness,
       v.confidence[i]::double precision/10000 AS confidence
FROM v3_derived.system_rating_vector v CROSS JOIN generate_series(1,7) i;

CREATE VIEW v3_app.system_economy_rating AS
SELECT r.* FROM v3_meta.current_derived_generation c
JOIN v3_derived.system_economy_rating r USING(derived_generation_id);
COMMENT ON VIEW v3_app.system_economy_rating IS
    'Convenience single-statement read. Multi-query API responses resolve and pin derived_generation_id once.';

CREATE FUNCTION v3_meta.publish_derived_generation(target_ uuid, expected_current_ uuid,
    expected_sequence_ bigint, expected_canonical_ uuid, expected_canonical_sequence_ bigint,
    actor_ text, reason_ text) RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE candidate_ v3_meta.derived_generation%ROWTYPE;
        current_ uuid; sequence_ bigint; canonical_ uuid; canonical_sequence_ bigint;
BEGIN
    IF actor_ IS NULL OR length(trim(actor_))=0 OR reason_ IS NULL OR length(trim(reason_))=0 THEN
        RAISE EXCEPTION 'publication needs actor and reason';
    END IF;
    -- Serializes first publication as well as later swaps/rollbacks.
    PERFORM pg_advisory_xact_lock(764003001);
    SELECT generation_id,publication_sequence INTO canonical_,canonical_sequence_
        FROM v3_meta.current_canonical_generation WHERE singleton FOR SHARE;
    IF canonical_ IS DISTINCT FROM expected_canonical_ OR
       canonical_sequence_ IS DISTINCT FROM expected_canonical_sequence_ THEN
        RAISE EXCEPTION 'canonical publication changed';
    END IF;
    SELECT derived_generation_id,publication_sequence INTO current_,sequence_
        FROM v3_meta.current_derived_generation WHERE singleton FOR UPDATE;
    IF current_ IS DISTINCT FROM expected_current_ OR COALESCE(sequence_,0)<>expected_sequence_ THEN
        RAISE EXCEPTION 'derived publication changed';
    END IF;
    SELECT * INTO candidate_ FROM v3_meta.derived_generation WHERE derived_generation_id=target_ FOR UPDATE;
    IF NOT FOUND OR candidate_.lifecycle_state NOT IN ('READY','RETIRED') OR
       candidate_.canonical_generation_id<>canonical_ OR candidate_.validation_receipt->>'status'<>'VERIFIED' THEN
        RAISE EXCEPTION 'candidate is not validated for current canonical generation';
    END IF;
    IF candidate_.validation_receipt->>'status' IS NULL THEN
        RAISE EXCEPTION 'candidate validation receipt missing';
    END IF;
    sequence_ := COALESCE(sequence_,0)+1;
    UPDATE v3_meta.derived_generation SET lifecycle_state='RETIRED' WHERE derived_generation_id=current_;
    UPDATE v3_meta.derived_generation SET lifecycle_state='PUBLISHED',published_at=now() WHERE derived_generation_id=target_;
    INSERT INTO v3_meta.current_derived_generation VALUES(true,target_,sequence_,now())
        ON CONFLICT(singleton) DO UPDATE SET derived_generation_id=EXCLUDED.derived_generation_id,
            publication_sequence=EXCLUDED.publication_sequence,published_at=EXCLUDED.published_at;
    INSERT INTO v3_meta.derived_publication_audit VALUES(sequence_,current_,target_,canonical_,now(),actor_,reason_);
    RETURN sequence_;
END $$;
COMMIT;
