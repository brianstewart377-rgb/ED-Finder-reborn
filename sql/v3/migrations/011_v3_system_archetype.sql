-- sql/v3/migrations/011_v3_system_archetype.sql
-- One-row-per-system archetype judgement product attached to one Ratings V4
-- generation. Additive; does not touch canonical relations. Mirrors the
-- system_search product lifecycle from 004/006, scoped to
-- product_code='system_archetype'.
BEGIN;

CREATE TABLE v3_derived.system_archetype (
    derived_generation_id uuid NOT NULL REFERENCES v3_meta.derived_generation,
    system_id64 bigint NOT NULL CHECK (system_id64 >= 0),
    archetype_version text NOT NULL
        CHECK (length(archetype_version) BETWEEN 1 AND 128),

    paradise_score smallint NOT NULL CHECK (paradise_score BETWEEN 0 AND 100),
    paradise_tier char(1) NOT NULL CHECK (paradise_tier IN ('S','A','B','C','D')),
    paradise_confidence_ppm integer NOT NULL
        CHECK (paradise_confidence_ppm BETWEEN 0 AND 1000000),
    paradise_weighted_e4 integer NOT NULL
        CHECK (paradise_weighted_e4 BETWEEN 0 AND 1000000),

    mining_hub_score smallint NOT NULL CHECK (mining_hub_score BETWEEN 0 AND 100),
    mining_hub_tier char(1) NOT NULL CHECK (mining_hub_tier IN ('S','A','B','C','D')),
    mining_hub_confidence_ppm integer NOT NULL
        CHECK (mining_hub_confidence_ppm BETWEEN 0 AND 1000000),
    mining_hub_weighted_e4 integer NOT NULL
        CHECK (mining_hub_weighted_e4 BETWEEN 0 AND 1000000),

    manufacturing_hub_score smallint NOT NULL
        CHECK (manufacturing_hub_score BETWEEN 0 AND 100),
    manufacturing_hub_tier char(1) NOT NULL
        CHECK (manufacturing_hub_tier IN ('S','A','B','C','D')),
    manufacturing_hub_confidence_ppm integer NOT NULL
        CHECK (manufacturing_hub_confidence_ppm BETWEEN 0 AND 1000000),
    manufacturing_hub_weighted_e4 integer NOT NULL
        CHECK (manufacturing_hub_weighted_e4 BETWEEN 0 AND 1000000),

    megacomplex_score smallint NOT NULL CHECK (megacomplex_score BETWEEN 0 AND 100),
    megacomplex_tier char(1) NOT NULL CHECK (megacomplex_tier IN ('S','A','B','C','D')),
    megacomplex_confidence_ppm integer NOT NULL
        CHECK (megacomplex_confidence_ppm BETWEEN 0 AND 1000000),
    megacomplex_weighted_e4 integer NOT NULL
        CHECK (megacomplex_weighted_e4 BETWEEN 0 AND 1000000),

    research_hub_score smallint NOT NULL CHECK (research_hub_score BETWEEN 0 AND 100),
    research_hub_tier char(1) NOT NULL CHECK (research_hub_tier IN ('S','A','B','C','D')),
    research_hub_confidence_ppm integer NOT NULL
        CHECK (research_hub_confidence_ppm BETWEEN 0 AND 1000000),
    research_hub_weighted_e4 integer NOT NULL
        CHECK (research_hub_weighted_e4 BETWEEN 0 AND 1000000),

    stronghold_score smallint NOT NULL CHECK (stronghold_score BETWEEN 0 AND 100),
    stronghold_tier char(1) NOT NULL CHECK (stronghold_tier IN ('S','A','B','C','D')),
    stronghold_confidence_ppm integer NOT NULL
        CHECK (stronghold_confidence_ppm BETWEEN 0 AND 1000000),
    stronghold_weighted_e4 integer NOT NULL
        CHECK (stronghold_weighted_e4 BETWEEN 0 AND 1000000),

    population_capital_score smallint NOT NULL
        CHECK (population_capital_score BETWEEN 0 AND 100),
    population_capital_tier char(1) NOT NULL
        CHECK (population_capital_tier IN ('S','A','B','C','D')),
    population_capital_confidence_ppm integer NOT NULL
        CHECK (population_capital_confidence_ppm BETWEEN 0 AND 1000000),
    population_capital_weighted_e4 integer NOT NULL
        CHECK (population_capital_weighted_e4 BETWEEN 0 AND 1000000),

    flexible_score smallint NOT NULL CHECK (flexible_score BETWEEN 0 AND 100),
    flexible_tier char(1) NOT NULL CHECK (flexible_tier IN ('S','A','B','C','D')),
    flexible_confidence_ppm integer NOT NULL
        CHECK (flexible_confidence_ppm BETWEEN 0 AND 1000000),
    flexible_weighted_e4 integer NOT NULL
        CHECK (flexible_weighted_e4 BETWEEN 0 AND 1000000),

    primary_archetype text NOT NULL,
    secondary_archetype text,
    best_colony_potential smallint NOT NULL
        CHECK (best_colony_potential BETWEEN 0 AND 100),
    best_tier char(1) NOT NULL CHECK (best_tier IN ('S','A','B','C','D')),
    archetype_confidence_ppm integer NOT NULL
        CHECK (archetype_confidence_ppm BETWEEN 0 AND 1000000),
    weighted_potential double precision NOT NULL
        CHECK (weighted_potential BETWEEN 0 AND 100),
    completeness_ppm integer NOT NULL
        CHECK (completeness_ppm BETWEEN 0 AND 1000000),
    computed_at timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (derived_generation_id, system_id64),
    FOREIGN KEY (derived_generation_id, system_id64)
        REFERENCES v3_derived.system_rating_vector
        DEFERRABLE INITIALLY DEFERRED,
    CHECK (primary_archetype IN (
        'paradise','mining_hub','manufacturing_hub','megacomplex',
        'research_hub','stronghold','population_capital','flexible'
    )),
    CHECK (secondary_archetype IS NULL OR secondary_archetype IN (
        'paradise','mining_hub','manufacturing_hub','megacomplex',
        'research_hub','stronghold','population_capital','flexible'
    )),
    CHECK (secondary_archetype IS NULL OR secondary_archetype <> primary_archetype)
);

CREATE INDEX system_archetype_summary_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, weighted_potential DESC NULLS LAST, system_id64);

-- The literal tier floors let PostgreSQL prove partial-index applicability for
-- generic plans.
CREATE INDEX system_archetype_paradise_b_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, paradise_weighted_e4 DESC, system_id64)
    WHERE paradise_score >= 60;
CREATE INDEX system_archetype_paradise_a_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, paradise_weighted_e4 DESC, system_id64)
    WHERE paradise_score >= 76;
CREATE INDEX system_archetype_paradise_s_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, paradise_weighted_e4 DESC, system_id64)
    WHERE paradise_score >= 88;

CREATE INDEX system_archetype_mining_hub_b_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, mining_hub_weighted_e4 DESC, system_id64)
    WHERE mining_hub_score >= 60;
CREATE INDEX system_archetype_mining_hub_a_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, mining_hub_weighted_e4 DESC, system_id64)
    WHERE mining_hub_score >= 76;
CREATE INDEX system_archetype_mining_hub_s_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, mining_hub_weighted_e4 DESC, system_id64)
    WHERE mining_hub_score >= 88;

CREATE INDEX system_archetype_manufacturing_hub_b_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, manufacturing_hub_weighted_e4 DESC, system_id64)
    WHERE manufacturing_hub_score >= 60;
CREATE INDEX system_archetype_manufacturing_hub_a_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, manufacturing_hub_weighted_e4 DESC, system_id64)
    WHERE manufacturing_hub_score >= 76;
CREATE INDEX system_archetype_manufacturing_hub_s_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, manufacturing_hub_weighted_e4 DESC, system_id64)
    WHERE manufacturing_hub_score >= 88;

CREATE INDEX system_archetype_megacomplex_b_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, megacomplex_weighted_e4 DESC, system_id64)
    WHERE megacomplex_score >= 60;
CREATE INDEX system_archetype_megacomplex_a_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, megacomplex_weighted_e4 DESC, system_id64)
    WHERE megacomplex_score >= 76;
CREATE INDEX system_archetype_megacomplex_s_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, megacomplex_weighted_e4 DESC, system_id64)
    WHERE megacomplex_score >= 88;

CREATE INDEX system_archetype_research_hub_b_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, research_hub_weighted_e4 DESC, system_id64)
    WHERE research_hub_score >= 60;
CREATE INDEX system_archetype_research_hub_a_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, research_hub_weighted_e4 DESC, system_id64)
    WHERE research_hub_score >= 76;
CREATE INDEX system_archetype_research_hub_s_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, research_hub_weighted_e4 DESC, system_id64)
    WHERE research_hub_score >= 88;

CREATE INDEX system_archetype_stronghold_b_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, stronghold_weighted_e4 DESC, system_id64)
    WHERE stronghold_score >= 60;
CREATE INDEX system_archetype_stronghold_a_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, stronghold_weighted_e4 DESC, system_id64)
    WHERE stronghold_score >= 76;
CREATE INDEX system_archetype_stronghold_s_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, stronghold_weighted_e4 DESC, system_id64)
    WHERE stronghold_score >= 88;

CREATE INDEX system_archetype_population_capital_b_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, population_capital_weighted_e4 DESC, system_id64)
    WHERE population_capital_score >= 60;
CREATE INDEX system_archetype_population_capital_a_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, population_capital_weighted_e4 DESC, system_id64)
    WHERE population_capital_score >= 76;
CREATE INDEX system_archetype_population_capital_s_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, population_capital_weighted_e4 DESC, system_id64)
    WHERE population_capital_score >= 88;

CREATE INDEX system_archetype_flexible_b_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, flexible_weighted_e4 DESC, system_id64)
    WHERE flexible_score >= 60;
CREATE INDEX system_archetype_flexible_a_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, flexible_weighted_e4 DESC, system_id64)
    WHERE flexible_score >= 76;
CREATE INDEX system_archetype_flexible_s_weighted
    ON v3_derived.system_archetype
       (derived_generation_id, flexible_weighted_e4 DESC, system_id64)
    WHERE flexible_score >= 88;

CREATE TABLE v3_derived.archetype_build_chunk (
    derived_generation_id uuid NOT NULL,
    chunk_ordinal bigint NOT NULL CHECK (chunk_ordinal >= 0),
    product_code text NOT NULL DEFAULT 'system_archetype'
        CHECK (product_code='system_archetype'),
    archetype_version text NOT NULL CHECK (length(archetype_version) BETWEEN 1 AND 128),
    source_projection_sha256 bytea NOT NULL CHECK (octet_length(source_projection_sha256)=32),
    content_sha256 bytea NOT NULL CHECK (octet_length(content_sha256)=32),
    systems integer NOT NULL CHECK (systems BETWEEN 1 AND 1000),
    completed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (derived_generation_id, chunk_ordinal),
    FOREIGN KEY (derived_generation_id, chunk_ordinal)
        REFERENCES v3_derived.build_chunk (derived_generation_id, chunk_ordinal)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (derived_generation_id, product_code)
        REFERENCES v3_meta.derived_product (derived_generation_id, product_code)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE FUNCTION v3_derived.guard_system_archetype_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE identifier uuid; base_state text; product_state text;
BEGIN
    FOR identifier IN SELECT DISTINCT derived_generation_id FROM changed_rows ORDER BY 1 LOOP
        SELECT lifecycle_state INTO base_state
          FROM v3_meta.derived_generation
         WHERE derived_generation_id=identifier FOR SHARE;
        IF base_state NOT IN ('BUILDING','VALIDATING','READY') THEN
            RAISE EXCEPTION 'system_archetype generation % is immutable in state %',
                identifier, base_state;
        END IF;
        SELECT lifecycle_state INTO product_state
          FROM v3_meta.derived_product
         WHERE derived_generation_id=identifier AND product_code='system_archetype'
         FOR SHARE;
        IF product_state IS DISTINCT FROM 'BUILDING' THEN
            RAISE EXCEPTION 'system_archetype product % is not BUILDING', identifier;
        END IF;
    END LOOP;
    RETURN NULL;
END $$;

CREATE FUNCTION v3_derived.reject_system_archetype_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'system_archetype rows and receipts are insert-only';
END $$;

-- The summary is now an application view, not a physical trigger target.
DO $triggers$
DECLARE relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY[
        'v3_derived.system_archetype',
        'v3_derived.archetype_build_chunk'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER product_insert AFTER INSERT ON %s '
            'REFERENCING NEW TABLE AS changed_rows FOR EACH STATEMENT '
            'EXECUTE FUNCTION v3_derived.guard_system_archetype_insert()', relation);
        EXECUTE format(
            'CREATE TRIGGER product_update BEFORE UPDATE ON %s FOR EACH STATEMENT '
            'EXECUTE FUNCTION v3_derived.reject_system_archetype_mutation()', relation);
        EXECUTE format(
            'CREATE TRIGGER product_delete BEFORE DELETE ON %s FOR EACH STATEMENT '
            'EXECUTE FUNCTION v3_derived.reject_system_archetype_mutation()', relation);
        EXECUTE format(
            'CREATE TRIGGER product_truncate BEFORE TRUNCATE ON %s FOR EACH STATEMENT '
            'EXECUTE FUNCTION v3_derived.reject_system_archetype_mutation()', relation);
    END LOOP;
END $triggers$;

CREATE VIEW v3_app.system_archetype AS
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
JOIN v3_derived.system_archetype s USING (derived_generation_id);

CREATE VIEW v3_app.system_archetype_summary AS
SELECT derived_generation_id, system_id64,
       primary_archetype, secondary_archetype,
       best_colony_potential, best_tier,
       (archetype_confidence_ppm / 1000000.0)::double precision AS archetype_confidence,
       weighted_potential, computed_at
FROM v3_app.system_archetype;

COMMIT;
