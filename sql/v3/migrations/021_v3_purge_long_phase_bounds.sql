-- Copy-and-drop long-phase bounds. Capability only: replaces three 019
-- functions — purge_copy_prepare, purge_copy_build_indexes and
-- purge_copy_verify — so that no single statement of the copy route has to
-- read or build a whole relation's index in one go. Installation copies,
-- swaps, drops and deletes no payload row and changes no table, view, grant or
-- ledger row.
--
-- Why (one_percent purge rehearsal run 40571954621, 2026-10-28): the rehearsal
-- passed its own checks, but three extrapolated long phases exceeded the
-- reviewed 7,200 s envelope: body_mechanics verification (about 11,400 s),
-- economy_opportunity verification (about 16,000 s) and the system_search index
-- build (about 15,300 s).
--
-- 1. Verification (purge_copy_verify). It grew faster than the tables: from
--    the smoke rehearsal (run 40535321292) to the one_percent rehearsal the
--    rows grew 6.67 times; body_mechanics verification took 13.6 times as long
--    (8.4 s -> 114.7 s) and economy_opportunity 14.7 times (11.0 s -> 161.7 s),
--    while the copy of the same rows took 8.5 and 9.5 times as long. 019
--    bounded each chunk with two row comparisons that start with
--    derived_generation_id, plus a separate derived_generation_id=$1. With
--    that equality on the index's leading column, the btree scan's end
--    condition is the end of the generation, not the chunk's high key: it
--    reads index entries from the chunk's low key to the end of the survivor
--    generation and filters out everything after the chunk, so verification
--    costs in proportion to the square of the table size. (The planner also
--    estimates a row comparison by its first column only, so it costs every
--    chunk as if it were the whole table.) Measured on PostgreSQL 18.4 with a
--    body_mechanics-shaped table: one 100,000-row chunk read 4,976 buffers in a
--    600,000-row table and 23,158 in a 3,600,000-row table.
--
--    Each chunk is now re-read exactly the way purge_copy_chunk read it from
--    the live table: rows at or after the chunk's low key, in primary-key
--    order, LIMIT the ledgered row count (CHUNK_COPIED rows always carry
--    rows_affected > 0, by 019's table check). The same chunk then read 2,670
--    buffers at both table sizes. The checks are unchanged and prove the same
--    thing: the staging primary key exists before verification (phase
--    INDEXES_BUILT), so keys are unique, and the first rows_affected rows at or
--    after the low key end exactly at the high key only if the rows between
--    the two keys are exactly those rows; their count, lowest and highest keys
--    and digest are compared with the ledger as before. Everything else in the
--    function is 019's.
--
-- 2. GiST indexes (purge_copy_prepare, purge_copy_build_indexes). The Search
--    index build is dominated by its GiST index on position_ly (cube has no
--    sorted GiST build, so CREATE INDEX inserts every row one by one either
--    way). Prepare now creates every live non-constraint GiST index, from the
--    same catalog generator, on the still-empty staging table; each bounded,
--    resumable chunk transaction then fills it as it inserts its rows, so no
--    statement builds it over the whole copy. purge_copy_build_indexes builds
--    every other index as before and leaves such an index alone only if it is
--    a GiST index whose definition is exactly the live one. Verification's
--    catalog fingerprint still compares every staging index with the live one,
--    and requires all of them valid. Chunks get slower by that index's insert
--    cost; the chunk statement timeout, gate 2 and the WAL throttle bound them
--    as before. Only system_search has a GiST index.
--
-- Additions to prepare and the index build are marked "021 begin" / "021 end";
-- outside those marks both functions are 019's. CREATE OR REPLACE keeps each
-- function's owner and its EXECUTE grant to v3_derived_purge_executor.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $$
BEGIN
    IF to_regprocedure('v3_meta.purge_copy_verify(uuid,text)') IS NULL
       OR to_regprocedure('v3_meta.purge_copy_prepare(uuid,text)') IS NULL
       OR to_regprocedure('v3_meta.purge_copy_build_indexes(uuid,text)') IS NULL
       OR to_regprocedure('v3_meta.purge_copy_lock_relocation(text,regclass)') IS NULL THEN
        RAISE EXCEPTION 'long-phase bounds require the copy-and-drop migrations 019 and 020';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_proc WHERE oid IN ('v3_meta.purge_copy_verify(uuid,text)'::regprocedure,
               'v3_meta.purge_copy_prepare(uuid,text)'::regprocedure,
               'v3_meta.purge_copy_build_indexes(uuid,text)'::regprocedure)
               AND proowner<>current_user::regrole) THEN
        RAISE EXCEPTION 'long-phase bounds must be installed by the owner of the 019 definers';
    END IF;
END $$;

-- 019's prepare; adds only the GiST creation on the empty staging table.
CREATE OR REPLACE FUNCTION v3_meta.purge_copy_prepare(purge_ uuid, code_ text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
SET lock_timeout='5s' SET statement_timeout='60s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request;
        s v3_meta.purge_copy_relation_state; live_ regclass; staging_ regclass; a record; event_ bigint; published_ jsonb;
        survivors_ jsonb; comment_ text; k record; o record;
        -- 021 begin
        i record; definition_ text; prefix_ text;
        -- 021 end
BEGIN
    r := v3_meta.purge_copy_context(purge_,false);
    live_ := v3_meta.purge_relation(code_);
    survivors_ := r.frozen_snapshot->'survivors';
    IF jsonb_typeof(survivors_) IS DISTINCT FROM 'object' OR EXISTS (
        SELECT 1 FROM unnest(ARRAY['system_search','economy_opportunity','body_mechanics','system_rating_vector']) c
        WHERE jsonb_typeof(survivors_->c) IS DISTINCT FROM 'number'
           OR (survivors_->>c)::numeric<0 OR (survivors_->>c)::numeric<>floor((survivors_->>c)::numeric)) THEN
        RAISE EXCEPTION 'frozen survivor counts missing from purge authorization';
    END IF;
    SELECT * INTO q FROM v3_meta.derived_purge_copy_request WHERE plan_sha256=r.plan_sha256;
    IF NOT FOUND THEN
        published_ := r.reviewed_plan->'published_survivors'->'products'->'system_search'->'expected_rows';
        IF published_ IS NOT NULL AND (jsonb_typeof(published_)<>'number'
           OR published_::text::numeric<>(survivors_->>'system_search')::numeric) THEN
            RAISE EXCEPTION 'published Search survivors differ from frozen survivors';
        END IF;
        INSERT INTO v3_meta.derived_purge_copy_request(purge_id,plan_sha256,survivor_generation_id,
            target_generation_ids,frozen_survivors,published_search_survivors)
        SELECT purge_,r.plan_sha256,r.expected_derived_id,
               array_agg(p.derived_generation_id ORDER BY p.derived_generation_id),survivors_,
               published_::text::bigint
          FROM v3_meta.derived_generation_purge_request p WHERE p.plan_sha256=r.plan_sha256
        RETURNING * INTO q;
    END IF;
    q := v3_meta.purge_copy_request_for(r);
    IF EXISTS (SELECT 1 FROM v3_meta.derived_purge_copy_event WHERE copy_id=q.copy_id AND event_kind='COMPLETED') THEN
        RAISE EXCEPTION 'purge copy already complete';
    END IF;
    PERFORM v3_meta.purge_copy_assert_order(q.copy_id,code_);
    s := v3_meta.purge_copy_assert_position(q.copy_id,code_);
    IF s.copy_position<>'none' THEN RAISE EXCEPTION 'copy already started for %', code_; END IF;
    PERFORM v3_meta.purge_copy_assert_generations(q,code_);
    IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=live_ AND attnum>0 AND attacl IS NOT NULL)
       OR EXISTS (SELECT 1 FROM pg_class WHERE oid=live_ AND (relkind<>'r' OR relispartition
           OR relowner<>current_user::regrole OR reltablespace<>0)) THEN
        RAISE EXCEPTION 'live relation shape outside the copy generator contract';
    END IF;
    EXECUTE format('CREATE TABLE %I.%I (LIKE %s INCLUDING ALL EXCLUDING INDEXES)',
        'v3_purge_staging',v3_meta.purge_copy_relname(code_),live_);
    staging_ := v3_meta.purge_copy_table(code_,'staging');
    comment_ := obj_description(live_::oid,'pg_class');
    IF comment_ IS NOT NULL THEN
        EXECUTE format('COMMENT ON TABLE %s IS %L',staging_,comment_);
    END IF;
    -- LIKE copies neither per-column statistics targets nor attribute options.
    -- Production carries owner-applied planner statistics (economy_opportunity
    -- system_id64: SET STATISTICS 1000 and n_distinct = -0.13, 2026-10-19,
    -- docs/operations/v3-postgres-server-settings.md). The copy deliberately
    -- preserves the live values so the fingerprint matches. n_distinct is a
    -- fraction of the current row count, so after the purge the owner must
    -- recalibrate it from fresh counts (that document's "After the governed
    -- purge" section); this capability never changes planner statistics.
    FOR k IN SELECT attname,attstattarget,attoptions FROM pg_attribute
             WHERE attrelid=live_ AND attnum>0 AND NOT attisdropped ORDER BY attnum LOOP
        IF k.attstattarget IS NOT NULL THEN
            EXECUTE format('ALTER TABLE %s ALTER COLUMN %I SET STATISTICS %s',staging_,k.attname,k.attstattarget);
        END IF;
        FOR o IN SELECT option_name,option_value FROM pg_options_to_table(k.attoptions) LOOP
            IF o.option_name NOT IN ('n_distinct','n_distinct_inherited') THEN
                RAISE EXCEPTION 'unsupported attribute option % on %', o.option_name,k.attname;
            END IF;
            EXECUTE format('ALTER TABLE %s ALTER COLUMN %I SET (%I = %L)',staging_,k.attname,
                           o.option_name,o.option_value);
        END LOOP;
    END LOOP;
    FOR a IN SELECT * FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) x
             WHERE c.oid=live_ AND x.grantee<>c.relowner LOOP
        IF a.privilege_type NOT IN ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER','MAINTAIN') THEN
            RAISE EXCEPTION 'unexpected live privilege';
        END IF;
        EXECUTE format('GRANT %s ON %s TO %s%s',a.privilege_type,staging_,
            CASE a.grantee WHEN 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(a.grantee)) END,
            CASE WHEN a.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END);
    END LOOP;
    PERFORM v3_meta.purge_copy_apply_reloptions(staging_,live_,true);
    EXECUTE format('CREATE TRIGGER purge_copy_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON %s '
                   'FOR EACH STATEMENT EXECUTE FUNCTION v3_meta.purge_copy_guard()',staging_);
    -- Fail fast: any production-only catalog difference refuses here, before
    -- a single row is copied, not hours later at verify.
    PERFORM v3_meta.purge_copy_assert_staging_shape(live_,staging_,true);
    -- 021 begin: GiST indexes are created now, on the empty table, so the chunk
    -- inserts fill them and no single statement builds them over the whole copy.
    -- Same generator as purge_copy_build_indexes, which then leaves them alone.
    FOR i IN SELECT x.indexrelid,c.relname,x.indisunique,c.reltablespace FROM pg_index x
             JOIN pg_class c ON c.oid=x.indexrelid JOIN pg_am m ON m.oid=c.relam
             WHERE x.indrelid=live_ AND m.amname='gist' AND NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conindid=x.indexrelid AND conrelid=live_) ORDER BY c.relname LOOP
        IF i.reltablespace<>0 THEN
            RAISE EXCEPTION 'index tablespace outside the copy generator contract';
        END IF;
        definition_ := pg_get_indexdef(i.indexrelid);
        prefix_ := format('CREATE %sINDEX %I ON %s USING ',CASE WHEN i.indisunique THEN 'UNIQUE ' ELSE '' END,
                          i.relname,live_);
        IF left(definition_,length(prefix_))<>prefix_ THEN
            RAISE EXCEPTION 'unexpected live index definition %', i.relname;
        END IF;
        EXECUTE format('CREATE %sINDEX %I ON %s USING ',CASE WHEN i.indisunique THEN 'UNIQUE ' ELSE '' END,
                       i.relname,staging_) || substr(definition_,length(prefix_)+1);
        IF obj_description(i.indexrelid,'pg_class') IS NOT NULL THEN
            EXECUTE format('COMMENT ON INDEX %I.%I IS %L','v3_purge_staging',i.relname,
                           obj_description(i.indexrelid,'pg_class'));
        END IF;
    END LOOP;
    -- 021 end
    INSERT INTO v3_meta.derived_purge_copy_event(copy_id,purge_id,event_kind,relation_code,live_oid,staging_oid,
        rows_total,operational_payload)
        VALUES(q.copy_id,purge_,'COPY_STARTED',code_,live_,staging_,0,
            jsonb_build_object('frozen_survivors',(q.frozen_survivors->>code_)::bigint,
                'live_relfilenode',(SELECT relfilenode FROM pg_class WHERE oid=live_)))
        RETURNING event_sequence INTO event_;
    RETURN event_;
END $$;

-- 019's index build; leaves only a prepared GiST index alone.
CREATE OR REPLACE FUNCTION v3_meta.purge_copy_build_indexes(purge_ uuid, code_ text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='5s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request; s v3_meta.purge_copy_relation_state;
        live_ regclass; staging_ regclass; k record; i record; prefix_ text; definition_ text;
        last_ bigint[]; remaining_ boolean; built_ text[] := '{}'; event_ bigint;
BEGIN
    r := v3_meta.purge_copy_context(purge_,false);
    q := v3_meta.purge_copy_request_for(r);
    PERFORM v3_meta.purge_copy_assert_order(q.copy_id,code_);
    s := v3_meta.purge_copy_assert_position(q.copy_id,code_);
    IF s.copy_position<>'staged' OR s.failed OR s.phase NOT IN ('COPY_STARTED','CHUNK_COPIED') THEN
        RAISE EXCEPTION 'copy phase required for index build';
    END IF;
    live_ := v3_meta.purge_relation(code_);
    staging_ := v3_meta.purge_copy_table(code_,'staging');
    -- Completeness: the ledgered total equals the frozen survivors and no
    -- survivor row exists after the last copied key (keyset chunks are gap-free).
    IF s.rows_total<>(q.frozen_survivors->>code_)::bigint THEN
        RAISE EXCEPTION 'copy incomplete: % of % survivor rows', s.rows_total,(q.frozen_survivors->>code_)::bigint;
    END IF;
    IF s.last_high IS NOT NULL THEN
        SELECT array_agg(value::bigint ORDER BY ordinality) INTO last_
            FROM jsonb_array_elements_text(s.last_high) WITH ORDINALITY;
    END IF;
    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %s WHERE derived_generation_id=$1%s)',live_,
        CASE WHEN last_ IS NULL THEN '' ELSE format(' AND (%s) > ($1,%s)',
            v3_meta.purge_copy_order_columns(code_),v3_meta.purge_copy_key_params(code_,2)) END)
        INTO remaining_ USING q.survivor_generation_id,last_;
    IF remaining_ THEN RAISE EXCEPTION 'survivor rows remain after the last copied key'; END IF;
    FOR k IN SELECT oid,conname,contype FROM pg_constraint WHERE conrelid=live_ AND contype IN ('p','u','x')
             ORDER BY conname LOOP
        EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I %s',staging_,k.conname,pg_get_constraintdef(k.oid));
        IF obj_description(k.oid,'pg_constraint') IS NOT NULL THEN
            EXECUTE format('COMMENT ON CONSTRAINT %I ON %s IS %L',k.conname,staging_,obj_description(k.oid,'pg_constraint'));
        END IF;
        built_ := array_append(built_,k.conname::text);
    END LOOP;
    FOR i IN SELECT x.indexrelid,c.relname,x.indisunique FROM pg_index x JOIN pg_class c ON c.oid=x.indexrelid
             WHERE x.indrelid=live_ AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conindid=x.indexrelid
                 AND conrelid=live_) ORDER BY c.relname LOOP
        IF (SELECT reltablespace FROM pg_class WHERE oid=i.indexrelid)<>0 THEN
            RAISE EXCEPTION 'index tablespace outside the copy generator contract';
        END IF;
        definition_ := pg_get_indexdef(i.indexrelid);
        prefix_ := format('CREATE %sINDEX %I ON %s USING ',CASE WHEN i.indisunique THEN 'UNIQUE ' ELSE '' END,
                          i.relname,live_);
        IF left(definition_,length(prefix_))<>prefix_ THEN
            RAISE EXCEPTION 'unexpected live index definition %', i.relname;
        END IF;
        -- 021 begin: a GiST index created empty at prepare and filled by the
        -- chunk inserts is not rebuilt; it must be exactly the live definition.
        IF EXISTS (SELECT 1 FROM pg_index x JOIN pg_class c ON c.oid=x.indexrelid
                   WHERE x.indrelid=staging_ AND c.relname=i.relname) THEN
            IF (SELECT m.amname FROM pg_class c JOIN pg_am m ON m.oid=c.relam WHERE c.oid=i.indexrelid)<>'gist'
               OR (SELECT pg_get_indexdef(x.indexrelid) FROM pg_index x JOIN pg_class c ON c.oid=x.indexrelid
                   WHERE x.indrelid=staging_ AND c.relname=i.relname)
                  IS DISTINCT FROM format('CREATE %sINDEX %I ON %s USING ',
                      CASE WHEN i.indisunique THEN 'UNIQUE ' ELSE '' END,i.relname,staging_)
                      || substr(definition_,length(prefix_)+1) THEN
                RAISE EXCEPTION 'staging index % is not the prepared copy of the live index', i.relname;
            END IF;
            CONTINUE;
        END IF;
        -- 021 end
        EXECUTE format('CREATE %sINDEX %I ON %s USING ',CASE WHEN i.indisunique THEN 'UNIQUE ' ELSE '' END,
                       i.relname,staging_) || substr(definition_,length(prefix_)+1);
        IF obj_description(i.indexrelid,'pg_class') IS NOT NULL THEN
            EXECUTE format('COMMENT ON INDEX %I.%I IS %L','v3_purge_staging',i.relname,
                           obj_description(i.indexrelid,'pg_class'));
        END IF;
        built_ := array_append(built_,i.relname::text);
    END LOOP;
    INSERT INTO v3_meta.derived_purge_copy_event(copy_id,purge_id,event_kind,relation_code,live_oid,staging_oid,
        rows_total,relation_bytes,operational_payload)
        VALUES(q.copy_id,purge_,'INDEXES_BUILT',code_,live_,staging_,s.rows_total,pg_total_relation_size(staging_),
            jsonb_build_object('built',to_jsonb(built_)))
        RETURNING event_sequence INTO event_;
    RETURN event_;
END $$;

-- 019's verification; only the per-chunk re-read changes (point 1 above).
CREATE OR REPLACE FUNCTION v3_meta.purge_copy_verify(purge_ uuid, code_ text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='5s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request; s v3_meta.purge_copy_relation_state;
        live_ regclass; staging_ regclass; e record; k record; low_ bigint[]; high_ bigint[];
        n bigint; total_ bigint; digest_ text; min_ bigint[]; max_ bigint[]; mismatch_ bigint;
        frozen_ bigint; order_ text; key_ text; after_ text; fks_ text[] := '{}'; event_ bigint; output_ jsonb;
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
    -- so the scan stops after that chunk; see the header.
    after_ := format('(%s) >= ($1,%s)',order_,v3_meta.purge_copy_key_params(code_,2));
    total_ := 0;
    output_ := v3_meta.purge_copy_output_settings(NULL);
    FOR e IN SELECT * FROM v3_meta.derived_purge_copy_event WHERE copy_id=q.copy_id AND relation_code=code_
             AND event_kind='CHUNK_COPIED' AND event_sequence>s.copy_started ORDER BY event_sequence LOOP
        SELECT array_agg(value::bigint ORDER BY ordinality) INTO low_ FROM jsonb_array_elements_text(e.chunk_low) WITH ORDINALITY;
        SELECT array_agg(value::bigint ORDER BY ordinality) INTO high_ FROM jsonb_array_elements_text(e.chunk_high) WITH ORDINALITY;
        EXECUTE format('WITH chosen AS MATERIALIZED (SELECT * FROM %3$s WHERE derived_generation_id=$1 AND %4$s
                ORDER BY %2$s LIMIT $3)
            SELECT count(*),min(%1$s),max(%1$s),
            encode(sha256(convert_to(COALESCE(string_agg(md5(c::text),'''' ORDER BY %2$s),''''),''UTF8'')),''hex'')
            FROM chosen c',key_,order_,staging_,after_)
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

COMMIT;
