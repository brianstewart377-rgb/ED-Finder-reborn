-- Optional Search scheduler; projection and publication contracts are unchanged.
BEGIN;

CREATE TABLE v3_meta.search_rebuild_plan (
    derived_generation_id uuid PRIMARY KEY,
    product_code text NOT NULL DEFAULT 'system_search' CHECK(product_code='system_search'),
    manifest_sha256 bytea NOT NULL CHECK(octet_length(manifest_sha256)=32),
    scheduler_sha256 bytea NOT NULL CHECK(octet_length(scheduler_sha256)=32),
    workers integer NOT NULL CHECK(workers BETWEEN 1 AND 64),
    total_chunks bigint NOT NULL CHECK(total_chunks BETWEEN 1 AND 2147483647),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK(workers <= total_chunks),
    FOREIGN KEY(derived_generation_id,product_code)
        REFERENCES v3_meta.derived_product(derived_generation_id,product_code)
);

CREATE TABLE v3_meta.search_rebuild_range (
    derived_generation_id uuid NOT NULL REFERENCES v3_meta.search_rebuild_plan,
    range_id integer NOT NULL CHECK(range_id BETWEEN 0 AND 63),
    first_chunk bigint NOT NULL CHECK(first_chunk >= 0),
    end_chunk bigint NOT NULL CHECK(end_chunk > first_chunk),
    next_chunk bigint NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(derived_generation_id,range_id),
    CHECK(next_chunk BETWEEN first_chunk AND end_chunk)
);

COMMENT ON TABLE v3_meta.search_rebuild_plan IS
    'Frozen bounded Search chunk partition; workers verify projection and scheduler code identity on every restart. No publication authority.';
COMMENT ON TABLE v3_meta.search_rebuild_range IS
    'Half-open Ratings chunk range. next_chunk advances in the same transaction as Search rows and receipt; the row lock is released automatically on worker loss.';

COMMIT;
