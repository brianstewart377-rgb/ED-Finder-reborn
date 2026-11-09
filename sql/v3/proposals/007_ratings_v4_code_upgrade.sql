-- Proposed additive migration. Not in the production migration manifest yet.
-- Promote through the reviewed migration operation together with its operator.
BEGIN;
-- Populated separately from the reviewed target JSON by the migration operator.
-- The candidate builder has no policy-registration path.
CREATE TABLE v3_meta.derived_code_upgrade_target (
    upgrade_id text PRIMARY KEY CHECK(upgrade_id='ratings-v4-direct-parser-1'),
    code_sha256_lf jsonb NOT NULL CHECK(jsonb_typeof(code_sha256_lf)='object'),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE v3_meta.derived_code_upgrade (
    derived_generation_id uuid PRIMARY KEY REFERENCES v3_meta.derived_generation,
    receipt jsonb NOT NULL CHECK(jsonb_typeof(receipt)='object'),
    receipt_sha256 bytea NOT NULL CHECK(octet_length(receipt_sha256)=32),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK(receipt->>'upgrade_id' = 'ratings-v4-direct-parser-1'),
    CHECK(receipt->>'derived_generation_id' = derived_generation_id::text)
);
CREATE FUNCTION v3_meta.guard_derived_code_upgrade() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'derived code upgrade attestations are retained and immutable';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM v3_meta.derived_generation g
        WHERE g.derived_generation_id=NEW.derived_generation_id
          AND g.lifecycle_state='BUILDING'
          AND encode(g.manifest_sha256,'hex')=NEW.receipt->>'manifest_sha256'
          AND g.manifest->'code_sha256_lf'=NEW.receipt->'from_code_sha256_lf') THEN
        RAISE EXCEPTION 'upgrade must match the original BUILDING generation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM v3_meta.derived_code_upgrade_target t
        WHERE t.upgrade_id=NEW.receipt->>'upgrade_id'
          AND t.code_sha256_lf=NEW.receipt->'to_code_sha256_lf') THEN
        RAISE EXCEPTION 'upgrade must match the independently approved target';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER guard_code_upgrade BEFORE INSERT OR UPDATE OR DELETE
    ON v3_meta.derived_code_upgrade FOR EACH ROW
    EXECUTE FUNCTION v3_meta.guard_derived_code_upgrade();
CREATE TRIGGER guard_code_upgrade_truncate BEFORE TRUNCATE
    ON v3_meta.derived_code_upgrade FOR EACH STATEMENT
    EXECUTE FUNCTION v3_meta.guard_derived_code_upgrade();
CREATE TRIGGER guard_code_upgrade_target BEFORE UPDATE OR DELETE
    ON v3_meta.derived_code_upgrade_target FOR EACH ROW
    EXECUTE FUNCTION v3_meta.guard_derived_code_upgrade();
CREATE TRIGGER guard_code_upgrade_target_truncate BEFORE TRUNCATE
    ON v3_meta.derived_code_upgrade_target FOR EACH STATEMENT
    EXECUTE FUNCTION v3_meta.guard_derived_code_upgrade();
COMMIT;
