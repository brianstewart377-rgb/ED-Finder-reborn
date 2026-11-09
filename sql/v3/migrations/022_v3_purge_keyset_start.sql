-- Copy-and-drop keyset start. Capability only: replaces 019's purge_copy_chunk,
-- 021's purge_copy_verify and 016's purge_assert_request, and adds one internal
-- helper. Installation copies, swaps, drops and deletes no payload row and
-- changes no table, view, grant or ledger row.
--
-- Why (production copy-economy-opportunity, 2026-10-30/31): under the same
-- 9,000 s chunk budget, run 41140926027 copied 267,900,000 rows in 2,679 chunks
-- (2.92 s of chunk statement per 100,000-row chunk) and run 41155213939 only
-- 123,500,000 rows in 1,235 chunks (6.85 s per chunk). The Search copy had the
-- same slope (runs 41050714814 and 41070830382: 3.32 s, then 5.25 s per chunk):
-- each chunk cost about 2.0e-8 s more for every survivor row copied before it.
-- 019's chunk read and 021's verification re-read bound each chunk with a row
-- comparison led by derived_generation_id plus a separate equality
-- derived_generation_id=$1. A btree scan takes at most one boundary key per
-- index column and an equality wins over an inequality, so the equality placed
-- the scan at the first survivor entry and the row comparison was only checked
-- entry by entry: every chunk walked every index entry of the survivor
-- generation before its low key. The total cost is quadratic in the relation.
-- The rest of the economy_opportunity copy would have needed about five more
-- dispatches, and its verification (7,401 chunks; Search's 1,986 took 4,247 s)
-- about 40,000-55,000 s, which can never fit its 19,200 s bound. 021's
-- per-chunk measurement (2,670 buffers at both table sizes) did not vary the
-- chunk's position, so it could not show this.
--
-- Measured on PostgreSQL 18.4 with a 5,400,000-row economy_opportunity-shaped
-- table holding 3,000,000 survivor rows: one 100,000-row chunk read 1,930
-- buffers after 100,000 survivor rows, 10,263 after 1,500,000 and 18,596 after
-- 2,900,000; with 022's predicate it read 1,335 after 100,000 and 1,334 after
-- 2,900,000, and returned the same rows.
--
-- 1. The keyset predicate (v3_meta.purge_copy_keyset). After the first chunk
--    the generation is bounded by derived_generation_id<=$1 instead of =$1.
--    With no equality on the leading column the row comparison becomes the
--    scan's start key, extended by its lower-order columns, so the scan starts
--    at the chunk's low key; <= ends it at the end of the generation, which the
--    LIMIT reaches sooner. The rows are the same: (generation, key) > ($1, low)
--    together with generation <= $1 holds exactly when generation = $1 and
--    key > low. The planner still estimates a row comparison by its first
--    column only, as it did 019's predicate, so the plan keeps its shape (the
--    ordered primary-key index scan under the LIMIT). The first chunk keeps
--    derived_generation_id=$1. purge_copy_chunk (019's, only the predicate) and
--    purge_copy_verify (021's, only the predicate) both use the helper, and the
--    chunk ledger, digests and checks are unchanged, so chunks copied before 022
--    verify after it.
--
-- 2. Ledger tolerance (purge_assert_request). 016 froze a hash of the applied
--    migrations ledger at authorize (production: run 40971335172, through 021)
--    and refuses every forward step when the live ledger differs ("purge schema
--    ledger drift"). A purge authorized before 022 froze the ledger without
--    022's row, so the request check now also accepts the live ledger minus
--    exactly the row named 022_v3_purge_keyset_start.sql. Any other added,
--    removed or changed row still refuses, and a purge authorized after 022
--    compares the whole ledger as before. The purge controller separately
--    requires the live ledger to equal the committed manifest, which pins 022's
--    SHA-256. Recovery steps (abandon, swap-back, record-failure) authenticate
--    through purge_recovery_request, which never compared the ledger.
--
-- Not changed: purge_copy_build_indexes keeps its completeness probe (one
-- EXISTS after the last copied key, once per relation, inside the index-build
-- long phase), whose single pass over the generation is not repeated per
-- chunk. Governed migration apply refuses while any relation is staged or
-- swapped (v3_purge_state), so production applies 022 with no copy in
-- progress: the partial economy_opportunity copy is abandoned first and copied
-- again with 022.
--
-- Changes inside the replaced functions are marked "022"; outside those marks
-- each is its source migration's text. CREATE OR REPLACE keeps each function's
-- owner and its EXECUTE grants.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $$
BEGIN
    IF to_regprocedure('v3_meta.purge_assert_request(uuid)') IS NULL
       OR to_regprocedure('v3_meta.purge_copy_chunk(uuid,text,integer)') IS NULL
       OR to_regprocedure('v3_meta.purge_copy_verify(uuid,text)') IS NULL
       OR to_regprocedure('v3_meta.purge_copy_lock_relocation(text,regclass)') IS NULL
       OR position('-- 021 begin' IN pg_get_functiondef('v3_meta.purge_copy_prepare(uuid,text)'::regprocedure))=0 THEN
        RAISE EXCEPTION 'keyset start requires the purge migrations 016, 019, 020 and 021';
    END IF;
    IF to_regprocedure('v3_meta.purge_copy_keyset(text,boolean)') IS NOT NULL THEN
        RAISE EXCEPTION 'keyset start is already installed';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_proc WHERE oid IN ('v3_meta.purge_assert_request(uuid)'::regprocedure,
               'v3_meta.purge_copy_chunk(uuid,text,integer)'::regprocedure,
               'v3_meta.purge_copy_verify(uuid,text)'::regprocedure)
               AND proowner<>current_user::regrole) THEN
        RAISE EXCEPTION 'keyset start must be installed by the owner of the 016 and 019 functions';
    END IF;
END $$;

-- The keyset predicate the chunk copy and the verification re-read share. $1 is
-- the survivor generation and $2 the key array (019's purge_copy_key_params).
-- The generation is bounded by <= instead of =, so the row comparison is the
-- btree scan's start key; with derived_generation_id<=$1 the comparison holds
-- exactly when derived_generation_id=$1 and the key is after $2 (at or after
-- when inclusive_). Internal: called only inside the 019 and 021 definers.
CREATE FUNCTION v3_meta.purge_copy_keyset(code_ text, inclusive_ boolean) RETURNS text
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
BEGIN
    IF inclusive_ IS NULL THEN RAISE EXCEPTION 'keyset bound required'; END IF;
    RETURN format('derived_generation_id<=$1 AND (%s) %s ($1,%s)',v3_meta.purge_copy_order_columns(code_),
                  CASE WHEN inclusive_ THEN '>=' ELSE '>' END,v3_meta.purge_copy_key_params(code_,2));
END $$;
REVOKE ALL ON FUNCTION v3_meta.purge_copy_keyset(text,boolean) FROM PUBLIC;

-- 019's chunk copy; only the keyset predicate changes (point 1 above).
CREATE OR REPLACE FUNCTION v3_meta.purge_copy_chunk(purge_ uuid, code_ text, max_rows_ integer) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
SET lock_timeout='5s' SET statement_timeout='60s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request; s v3_meta.purge_copy_relation_state;
        live_ regclass; staging_ regclass; last_ bigint[]; order_ text; key_ text; keyset_ text;
        inserted_ bigint; selected_ bigint; low_ bigint[]; high_ bigint[]; digest_ text; output_ jsonb;
BEGIN
    IF max_rows_ IS NULL OR max_rows_ NOT BETWEEN 1 AND 100000 THEN RAISE EXCEPTION 'chunk limit out of range'; END IF;
    r := v3_meta.purge_copy_context(purge_,false);
    q := v3_meta.purge_copy_request_for(r);
    PERFORM v3_meta.purge_copy_assert_order(q.copy_id,code_);
    s := v3_meta.purge_copy_assert_position(q.copy_id,code_);
    IF s.copy_position<>'staged' OR s.failed OR s.phase NOT IN ('COPY_STARTED','CHUNK_COPIED') THEN
        RAISE EXCEPTION 'copy phase required for chunk';
    END IF;
    IF EXISTS (SELECT 1 FROM v3_meta.derived_purge_copy_event WHERE copy_id=q.copy_id
               AND event_kind='CHUNK_COPIED' AND transaction_id=pg_current_xact_id()) THEN
        RAISE EXCEPTION 'one copy chunk per transaction';
    END IF;
    live_ := v3_meta.purge_relation(code_);
    staging_ := v3_meta.purge_copy_table(code_,'staging');
    IF s.last_high IS NOT NULL THEN
        SELECT array_agg(value::bigint ORDER BY ordinality) INTO last_
            FROM jsonb_array_elements_text(s.last_high) WITH ORDINALITY;
    END IF;
    order_ := v3_meta.purge_copy_order_columns(code_);
    key_ := v3_meta.purge_key_expression(code_);
    -- 022: the first chunk reads from the start of the generation, every later
    -- chunk from the row after the last copied key (see the header).
    keyset_ := CASE WHEN last_ IS NULL THEN 'derived_generation_id=$1'
        ELSE v3_meta.purge_copy_keyset(code_,false) END;
    PERFORM set_config('v3.purge_copy_relation',v3_meta.purge_copy_relname(code_),true);
    output_ := v3_meta.purge_copy_output_settings(NULL);
    EXECUTE format('WITH chosen AS MATERIALIZED (SELECT * FROM %1$s WHERE %2$s ORDER BY %3$s LIMIT $3),
        inserted AS (INSERT INTO %4$s SELECT * FROM chosen ORDER BY %3$s RETURNING 1)
        SELECT (SELECT count(*) FROM inserted),count(*),min(%5$s),max(%5$s),
               encode(sha256(convert_to(COALESCE(string_agg(md5(c::text),'''' ORDER BY %3$s),''''),''UTF8'')),''hex'')
        FROM chosen c',live_,keyset_,order_,staging_,key_)
        INTO inserted_,selected_,low_,high_,digest_ USING q.survivor_generation_id,last_,max_rows_;
    PERFORM v3_meta.purge_copy_output_settings(output_);
    PERFORM set_config('v3.purge_copy_relation','',true);
    IF inserted_<>selected_ THEN RAISE EXCEPTION 'copy chunk insert count drift'; END IF;
    IF selected_=0 THEN RETURN 0; END IF;
    IF s.rows_total+selected_>(q.frozen_survivors->>code_)::bigint THEN
        RAISE EXCEPTION 'copy exceeds frozen survivor count';
    END IF;
    INSERT INTO v3_meta.derived_purge_copy_event(copy_id,purge_id,event_kind,relation_code,live_oid,staging_oid,
        chunk_low,chunk_high,rows_affected,rows_total,source_digest,operational_payload)
        VALUES(q.copy_id,purge_,'CHUNK_COPIED',code_,live_,staging_,to_jsonb(low_),to_jsonb(high_),
            selected_,s.rows_total+selected_,digest_,
            jsonb_build_object('chunk_index',s.chunks+1,'max_rows',max_rows_));
    RETURN selected_;
END $$;

-- 021's verification; only the per-chunk re-read's keyset predicate changes.
CREATE OR REPLACE FUNCTION v3_meta.purge_copy_verify(purge_ uuid, code_ text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='5s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request; s v3_meta.purge_copy_relation_state;
        live_ regclass; staging_ regclass; e record; k record; low_ bigint[]; high_ bigint[];
        n bigint; total_ bigint; digest_ text; min_ bigint[]; max_ bigint[]; mismatch_ bigint;
        frozen_ bigint; order_ text; key_ text; keyset_ text; fks_ text[] := '{}'; event_ bigint; output_ jsonb;
BEGIN
    r := v3_meta.purge_copy_context(purge_,false);
    q := v3_meta.purge_copy_request_for(r);
    PERFORM v3_meta.purge_copy_assert_order(q.copy_id,code_);
    s := v3_meta.purge_copy_assert_position(q.copy_id,code_);
    IF s.copy_position<>'staged' OR s.failed OR s.phase NOT IN ('INDEXES_BUILT','SWAPPED_BACK') THEN
        RAISE EXCEPTION 'index build or swap-back required before verification';
    END IF;
    live_ := v3_meta.purge_relation(code_);
    staging_ := v3_meta.purge_copy_table(code_,'staging');
    frozen_ := (q.frozen_survivors->>code_)::bigint;
    -- Maintenance: the manual VACUUM (FREEZE, ANALYZE) after the final insert.
    PERFORM pg_stat_clear_snapshot();
    IF NOT EXISTS (SELECT 1 FROM pg_stat_user_tables WHERE relid=staging_
        AND last_vacuum>s.last_copy_at AND last_analyze>s.last_copy_at) THEN
        RAISE EXCEPTION 'staging VACUUM and ANALYZE after the final copy chunk required';
    END IF;
    -- Counts: frozen survivors, the copy ledger, and Search's published proof.
    EXECUTE format('SELECT count(*),count(*) FILTER (WHERE derived_generation_id=$1) FROM %s',staging_)
        INTO total_,n USING q.survivor_generation_id;
    IF total_<>frozen_ OR n<>frozen_ OR s.rows_total<>frozen_
       OR (code_='system_search' AND q.published_search_survivors IS NOT NULL
           AND q.published_search_survivors<>frozen_) THEN
        RAISE EXCEPTION 'survivor count mismatch: staging %, ledger %, frozen %', total_,s.rows_total,frozen_;
    END IF;
    -- Content: every ledgered chunk's range recounted and re-digested.
    order_ := v3_meta.purge_copy_order_columns(code_);
    key_ := v3_meta.purge_key_expression(code_);
    -- 021: each chunk is re-read the way purge_copy_chunk read it (keyset from
    -- the chunk's low key, primary-key order, LIMIT the ledgered row count),
    -- so the scan stops after that chunk; see the header. 022: and it starts
    -- at that low key, not at the start of the generation (022's header).
    keyset_ := v3_meta.purge_copy_keyset(code_,true);
    total_ := 0;
    output_ := v3_meta.purge_copy_output_settings(NULL);
    FOR e IN SELECT * FROM v3_meta.derived_purge_copy_event WHERE copy_id=q.copy_id AND relation_code=code_
             AND event_kind='CHUNK_COPIED' AND event_sequence>s.copy_started ORDER BY event_sequence LOOP
        SELECT array_agg(value::bigint ORDER BY ordinality) INTO low_ FROM jsonb_array_elements_text(e.chunk_low) WITH ORDINALITY;
        SELECT array_agg(value::bigint ORDER BY ordinality) INTO high_ FROM jsonb_array_elements_text(e.chunk_high) WITH ORDINALITY;
        EXECUTE format('WITH chosen AS MATERIALIZED (SELECT * FROM %3$s WHERE %4$s
                ORDER BY %2$s LIMIT $3)
            SELECT count(*),min(%1$s),max(%1$s),
            encode(sha256(convert_to(COALESCE(string_agg(md5(c::text),'''' ORDER BY %2$s),''''),''UTF8'')),''hex'')
            FROM chosen c',key_,order_,staging_,keyset_)
            INTO n,min_,max_,digest_ USING q.survivor_generation_id,low_,e.rows_affected;
        IF n<>e.rows_affected OR min_ IS DISTINCT FROM low_ OR max_ IS DISTINCT FROM high_
           OR digest_<>e.source_digest THEN
            RAISE EXCEPTION 'copied chunk % content mismatch', e.event_sequence;
        END IF;
        total_ := total_+n;
    END LOOP;
    PERFORM v3_meta.purge_copy_output_settings(output_);
    IF total_<>frozen_ THEN RAISE EXCEPTION 'chunk ledger does not cover the survivors'; END IF;
    -- Chunk receipts: rows per Ratings chunk equal the survivor's build receipts.
    -- vector: systems; body: physical_bodies; opportunity: eligible_opportunities;
    -- Search: search_build_chunk.systems. Non-vector rows map through the live vector.
    EXECUTE format('WITH observed AS (SELECT %1$s AS chunk_ordinal,count(*) AS n FROM %2$s x %3$s
            WHERE x.derived_generation_id=$1 GROUP BY 1),
        expected AS (SELECT chunk_ordinal,%4$s AS n FROM %5$s WHERE derived_generation_id=$1)
        SELECT count(*) FROM observed o FULL JOIN expected e USING (chunk_ordinal)
        WHERE o.n IS DISTINCT FROM e.n AND COALESCE(o.n,0)+COALESCE(e.n,0)<>0',
        CASE code_ WHEN 'system_rating_vector' THEN 'x.chunk_ordinal' ELSE 'v.chunk_ordinal' END,
        staging_,
        CASE code_ WHEN 'system_rating_vector' THEN '' ELSE
            'JOIN v3_derived.system_rating_vector v ON v.derived_generation_id=x.derived_generation_id AND v.system_id64=x.system_id64' END,
        CASE code_ WHEN 'system_rating_vector' THEN 'systems::bigint' WHEN 'body_mechanics' THEN 'physical_bodies::bigint'
            WHEN 'economy_opportunity' THEN 'eligible_opportunities::bigint' ELSE 'systems::bigint' END,
        CASE code_ WHEN 'system_search' THEN 'v3_derived.search_build_chunk' ELSE 'v3_derived.build_chunk' END)
        INTO mismatch_ USING q.survivor_generation_id;
    IF mismatch_<>0 THEN RAISE EXCEPTION 'chunk receipt count mismatch in % chunks', mismatch_; END IF;
    -- The live table holds nothing beyond the survivors and the purge targets.
    PERFORM v3_meta.purge_copy_assert_generations(q,code_);
    -- FKs to live parents: added NOT VALID with canonical names, then validated.
    FOR k IN SELECT oid,conname FROM pg_constraint WHERE conrelid=live_ AND contype='f' ORDER BY conname LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=staging_ AND conname=k.conname) THEN
            EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I %s NOT VALID',staging_,k.conname,
                regexp_replace(pg_get_constraintdef(k.oid),' NOT VALID$',''));
            IF obj_description(k.oid,'pg_constraint') IS NOT NULL THEN
                EXECUTE format('COMMENT ON CONSTRAINT %I ON %s IS %L',k.conname,staging_,obj_description(k.oid,'pg_constraint'));
            END IF;
        END IF;
        EXECUTE format('ALTER TABLE %s VALIDATE CONSTRAINT %I',staging_,k.conname);
        fks_ := array_append(fks_,k.conname::text);
    END LOOP;
    -- Catalog: fingerprint, the exact copy guard, staging reloptions, valid indexes.
    PERFORM v3_meta.purge_copy_assert_staging_shape(live_,staging_,false);
    IF EXISTS (SELECT 1 FROM pg_index WHERE indrelid=staging_ AND (NOT indisvalid OR NOT indisready)) THEN
        RAISE EXCEPTION 'staging index not valid';
    END IF;
    INSERT INTO v3_meta.derived_purge_copy_event(copy_id,purge_id,event_kind,relation_code,live_oid,staging_oid,
        rows_total,relation_bytes,operational_payload)
        VALUES(q.copy_id,purge_,'VERIFIED',code_,live_,staging_,frozen_,pg_total_relation_size(staging_),
            jsonb_build_object('chunks',s.chunks,'foreign_keys',to_jsonb(fks_),
                'fingerprint_sha256',encode(sha256(convert_to(v3_meta.purge_copy_fingerprint(staging_)::text,'UTF8')),'hex')))
        RETURNING event_sequence INTO event_;
    RETURN event_;
END $$;

-- 016's request authentication; only the schema-ledger comparison changes
-- (point 2 above).
CREATE OR REPLACE FUNCTION v3_meta.purge_assert_request(purge_ uuid) RETURNS v3_meta.derived_generation_purge_request
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r v3_meta.derived_generation_purge_request; d v3_meta.derived_generation;
        c jsonb; g jsonb; s jsonb;
BEGIN
    IF NOT pg_try_advisory_xact_lock(764003001) OR NOT pg_try_advisory_xact_lock(764004001) THEN
        RAISE EXCEPTION 'competing derived or spatial operation';
    END IF;
    SELECT * INTO r FROM v3_meta.derived_generation_purge_request WHERE purge_id=purge_;
    IF NOT FOUND THEN RAISE EXCEPTION 'unauthorized purge id'; END IF;
    SELECT to_jsonb(p) INTO c FROM v3_meta.current_canonical_generation p WHERE singleton FOR SHARE;
    SELECT to_jsonb(p) INTO g FROM v3_meta.current_derived_generation p WHERE singleton FOR SHARE;
    SELECT to_jsonb(p) INTO s FROM v3_spatial.current_spatial_generation p WHERE singleton FOR SHARE;
    IF c IS DISTINCT FROM r.frozen_snapshot->'canonical'
       OR g IS DISTINCT FROM r.frozen_snapshot->'derived'
       OR s IS DISTINCT FROM r.frozen_snapshot->'spatial' THEN
        RAISE EXCEPTION 'purge publication pointer or sequence drift';
    END IF;
    SELECT * INTO d FROM v3_meta.derived_generation
        WHERE derived_generation_id=r.derived_generation_id FOR SHARE;
    IF d.lifecycle_state IS DISTINCT FROM 'FAILED' OR d.generation_key<>r.generation_key
       OR d.manifest_sha256<>r.manifest_sha256
       OR d.derived_generation_id=r.expected_derived_id
       OR EXISTS (SELECT 1 FROM v3_meta.derived_publication_audit
          WHERE from_generation_id=d.derived_generation_id OR to_generation_id=d.derived_generation_id)
       OR EXISTS (SELECT 1 FROM v3_meta.derived_product_publication_audit
          WHERE derived_generation_id=d.derived_generation_id) THEN
        RAISE EXCEPTION 'purge target identity, state or nonpublication mismatch';
    END IF;
    -- 022 begin: a purge authorized before 022 froze the ledger without 022's
    -- own row; it accepts the live ledger minus exactly that row, and any other
    -- difference still refuses (see 022's header).
    IF (SELECT encode(sha256(convert_to(COALESCE(jsonb_agg(jsonb_build_array(migration_name,
          encode(migration_sha256,'hex')) ORDER BY migration_name)::text,'[]'),'UTF8')),'hex')
          FROM v3_meta.schema_migration) IS DISTINCT FROM r.frozen_snapshot->>'schema_ledger_sha256'
       AND (SELECT encode(sha256(convert_to(COALESCE(jsonb_agg(jsonb_build_array(migration_name,
          encode(migration_sha256,'hex')) ORDER BY migration_name)::text,'[]'),'UTF8')),'hex')
          FROM v3_meta.schema_migration WHERE migration_name<>'022_v3_purge_keyset_start.sql')
          IS DISTINCT FROM r.frozen_snapshot->>'schema_ledger_sha256' THEN
        RAISE EXCEPTION 'purge schema ledger drift';
    END IF;
    -- 022 end
    PERFORM v3_meta.purge_assert_empty_exclusions();
    RETURN r;
END $$;

COMMIT;
