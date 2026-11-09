-- Copy-and-drop swap lock order. Capability only: replaces the locking of three
-- 019 functions and adds one internal helper. Installation copies, swaps, drops
-- and deletes no payload row and changes no table, view, grant or ledger row.
--
-- Why (smoke rehearsal run 40466831980, 2026-10-27): the first swap stopped
-- because a concurrent Finder read of v3_app.system_search died with
-- "deadlock detected". 019's swap locked the live table and only later the
-- dependent view, while a reader locks a view before the tables it reads, so
-- each waited for the other and PostgreSQL cancelled the reader. 019 also took
-- the view's lock with LOCK TABLE, which on a view locks every relation the
-- view reads, including v3_meta.current_derived_generation, derived_generation
-- and derived_product; every Finder read stalled for the whole swap, not only
-- reads of the relation being swapped.
--
-- What changes: swap and swap-back take every relocation lock up front, in
-- reader order, through v3_meta.purge_copy_lock_relocation:
--   1. the dependent view (019 fixes at most one per relation), locked by a
--      no-op owner change, which takes ACCESS EXCLUSIVE on the view alone;
--   2. the outgoing table, the incoming table and the fixed child tables, each
--      ACCESS EXCLUSIVE NOWAIT (the outgoing table may wait when the relation
--      has no dependent view, because it is then the first lock).
-- Only the first of these may wait, under the function's lock_timeout. A
-- transaction that never waits for a lock while it holds one that a reader
-- needs cannot close a deadlock cycle, so a concurrent Finder read is only
-- delayed until the swap commits. A reader still holding a later relation
-- makes the attempt fail at once with SQLSTATE 55P03; the attempt rolls back
-- whole and the operator retries it with backoff, exactly as for a lock
-- timeout. purge_copy_relocate no longer locks anything itself; it refuses
-- unless those locks are already held.
--
-- Correction to 019's closing comment (2026-10-26 external review, F5): a
-- function-level SET statement_timeout never bounds the function's own call,
-- so the 60s clauses below bound nothing; every call is bounded by the
-- operator's SET LOCAL statement_timeout in the same transaction
-- (scripts/operator/lib/v3_purge_copy.py, PHASES). The function-level
-- lock_timeout='2s' does win over the caller's value; after 020 it governs
-- only the single acquisition that may wait.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $$
BEGIN
    IF to_regprocedure('v3_meta.purge_copy_relocate(text,regclass,text)') IS NULL
       OR to_regprocedure('v3_meta.purge_copy_swap(uuid,text)') IS NULL
       OR to_regprocedure('v3_meta.purge_copy_swap_back(uuid,text)') IS NULL THEN
        RAISE EXCEPTION 'swap lock order requires the copy-and-drop migration 019';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_proc WHERE oid IN ('v3_meta.purge_copy_relocate(text,regclass,text)'::regprocedure,
               'v3_meta.purge_copy_swap(uuid,text)'::regprocedure,'v3_meta.purge_copy_swap_back(uuid,text)'::regprocedure)
               AND proowner<>current_user::regrole) THEN
        RAISE EXCEPTION 'swap lock order must be installed by the owner of the 019 definers';
    END IF;
    IF EXISTS (SELECT 1 FROM unnest(ARRAY['v3_app.system_search','v3_derived.system_economy_rating']) v(name_)
               WHERE to_regclass(v.name_) IS NULL
                  OR (SELECT relowner FROM pg_class WHERE oid=to_regclass(v.name_))<>current_user::regrole) THEN
        RAISE EXCEPTION 'swap lock order must be installed by the owner of the dependent views';
    END IF;
END $$;

-- Every lock a relocation needs, once, in reader order; see the header.
CREATE FUNCTION v3_meta.purge_copy_lock_relocation(code_ text, in_ regclass) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE views_ text[]; view_ regclass; name_ text;
BEGIN
    views_ := v3_meta.purge_copy_expected_views(code_);
    IF cardinality(views_)>1 THEN RAISE EXCEPTION 'at most one dependent view per relation'; END IF;
    FOREACH name_ IN ARRAY views_ LOOP
        view_ := name_::regclass;
        IF (SELECT relkind FROM pg_class WHERE oid=view_)<>'v' THEN
            RAISE EXCEPTION 'dependent view expected: %', name_;
        END IF;
        -- An owner change to the current owner changes nothing and locks only
        -- the view; LOCK TABLE on a view would lock every relation it reads.
        EXECUTE format('ALTER VIEW %s OWNER TO %s',view_,(SELECT relowner::regrole FROM pg_class WHERE oid=view_));
    END LOOP;
    EXECUTE format('LOCK TABLE %s IN ACCESS EXCLUSIVE MODE%s',v3_meta.purge_copy_table(code_,'live'),
                   CASE WHEN cardinality(views_)=0 THEN '' ELSE ' NOWAIT' END);
    EXECUTE format('LOCK TABLE %s IN ACCESS EXCLUSIVE MODE NOWAIT',in_);
    FOREACH name_ IN ARRAY v3_meta.purge_copy_expected_children(code_) LOOP
        EXECUTE format('LOCK TABLE %s IN ACCESS EXCLUSIVE MODE NOWAIT',name_::regclass);
    END LOOP;
END $$;
REVOKE ALL ON FUNCTION v3_meta.purge_copy_lock_relocation(text,regclass) FROM PUBLIC;

-- 019's relocation with its own LOCK statements replaced by a check that
-- purge_copy_lock_relocation already took them. Unchanged otherwise.
CREATE OR REPLACE FUNCTION v3_meta.purge_copy_relocate(code_ text, in_ regclass, out_schema_ text) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE current_ regclass; relname_ text; children_ text[]; views_ text[]; expected_ text[];
        fk record; v record; fks_ jsonb := '[]'; view_defs_ jsonb := '[]'; definition_ text; comment_ text;
        t record; repointed_ jsonb := '[]';
BEGIN
    IF out_schema_ NOT IN ('v3_retired','v3_purge_staging') THEN RAISE EXCEPTION 'fixed relocation schema required'; END IF;
    relname_ := v3_meta.purge_copy_relname(code_);
    current_ := v3_meta.purge_copy_table(code_,'live');
    IF EXISTS (SELECT 1 FROM unnest(ARRAY[current_::text,in_::text] || v3_meta.purge_copy_expected_children(code_)
                                    || v3_meta.purge_copy_expected_views(code_)) x(name_)
               WHERE NOT EXISTS (SELECT 1 FROM pg_locks l
                   WHERE l.locktype='relation' AND l.pid=pg_backend_pid() AND l.granted
                     AND l.mode='AccessExclusiveLock' AND l.relation=x.name_::regclass
                     AND l.database=(SELECT oid FROM pg_database WHERE datname=current_database()))) THEN
        RAISE EXCEPTION 'relocation locks not held; purge_copy_lock_relocation runs first';
    END IF;
    SELECT COALESCE(array_agg(x ORDER BY x),'{}') INTO expected_ FROM (
        SELECT 'fk:'||c AS x FROM unnest(v3_meta.purge_copy_expected_children(code_)) c
        UNION SELECT 'view:'||w FROM unnest(v3_meta.purge_copy_expected_views(code_)) w) e;
    IF v3_meta.purge_copy_external_dependents(current_) IS DISTINCT FROM expected_ THEN
        RAISE EXCEPTION 'unexpected dependents of %: %', current_,
            array_to_string(v3_meta.purge_copy_external_dependents(current_),',');
    END IF;
    IF cardinality(v3_meta.purge_copy_external_dependents(in_))<>0 THEN
        RAISE EXCEPTION 'incoming relation has dependents';
    END IF;
    FOR fk IN SELECT k.oid,k.conrelid::regclass AS child,k.conname,pg_get_constraintdef(k.oid) AS definition,
                     obj_description(k.oid,'pg_constraint') AS comment
              FROM pg_constraint k WHERE k.confrelid=current_ AND k.contype='f' AND k.conrelid<>current_
              ORDER BY k.conrelid::regclass::text,k.conname LOOP
        fks_ := fks_ || jsonb_build_array(jsonb_build_object('child',fk.child::text,'name',fk.conname,
            'definition',regexp_replace(fk.definition,' NOT VALID$',''),'comment',fk.comment));
    END LOOP;
    FOR v IN SELECT DISTINCT rw.ev_class::regclass AS view FROM pg_depend d JOIN pg_rewrite rw ON rw.oid=d.objid
             WHERE d.classid='pg_rewrite'::regclass AND d.refclassid='pg_class'::regclass AND d.refobjid=current_
               AND rw.ev_class<>current_ ORDER BY 1 LOOP
        IF (SELECT reloptions FROM pg_class WHERE oid=v.view) IS NOT NULL THEN
            RAISE EXCEPTION 'dependent view options outside the copy contract';
        END IF;
        view_defs_ := view_defs_ || jsonb_build_array(jsonb_build_object('view',v.view::text,
            'definition',pg_get_viewdef(v.view,false)));
    END LOOP;
    EXECUTE format('ALTER TABLE %s SET SCHEMA %I',current_,out_schema_);
    EXECUTE format('ALTER TABLE %s SET SCHEMA %I',in_,'v3_derived');
    FOR v IN SELECT value FROM jsonb_array_elements(view_defs_) LOOP
        definition_ := rtrim(v.value->>'definition',E'; \n');
        EXECUTE format('CREATE OR REPLACE VIEW %s AS %s',(v.value->>'view')::regclass,definition_);
        IF pg_get_viewdef((v.value->>'view')::regclass,false) IS DISTINCT FROM v.value->>'definition' THEN
            RAISE EXCEPTION 'recreated view definition differs: %', v.value->>'view';
        END IF;
    END LOOP;
    FOR fk IN SELECT value FROM jsonb_array_elements(fks_) LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',(fk.value->>'child')::regclass,fk.value->>'name');
        EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I %s NOT VALID',(fk.value->>'child')::regclass,
                       fk.value->>'name',fk.value->>'definition');
        IF fk.value->>'comment' IS NOT NULL THEN
            EXECUTE format('COMMENT ON CONSTRAINT %I ON %s IS %L',fk.value->>'name',
                           (fk.value->>'child')::regclass,fk.value->>'comment');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=(fk.value->>'child')::regclass
                       AND conname=fk.value->>'name' AND confrelid=in_ AND NOT convalidated) THEN
            RAISE EXCEPTION 'child foreign key was not re-pointed';
        END IF;
        repointed_ := repointed_ || jsonb_build_array(jsonb_build_object('child',fk.value->>'child','name',fk.value->>'name'));
    END LOOP;
    IF v3_meta.purge_copy_external_dependents(in_) IS DISTINCT FROM expected_
       OR cardinality(v3_meta.purge_copy_external_dependents(current_))<>0 THEN
        RAISE EXCEPTION 'dependents did not follow the relocation';
    END IF;
    RETURN jsonb_build_object('repointed',repointed_,'views',(SELECT COALESCE(jsonb_agg(x->'view'),'[]'::jsonb)
        FROM jsonb_array_elements(view_defs_) x),'outgoing_oid',current_::oid,'incoming_oid',in_::oid);
END $$;

-- 019's swap; only its LOCK statement changes (to purge_copy_lock_relocation).
CREATE OR REPLACE FUNCTION v3_meta.purge_copy_swap(purge_ uuid, code_ text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
SET lock_timeout='2s' SET statement_timeout='60s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request; s v3_meta.purge_copy_relation_state;
        live_ regclass; staging_ regclass; retired_ regclass; triggers_ text[]; t text; diff_ text[];
        moved_ jsonb; event_ bigint;
BEGIN
    r := v3_meta.purge_copy_context(purge_,false);
    q := v3_meta.purge_copy_request_for(r);
    PERFORM v3_meta.purge_copy_assert_order(q.copy_id,code_);
    s := v3_meta.purge_copy_assert_position(q.copy_id,code_);
    IF s.copy_position<>'staged' OR s.failed OR s.phase<>'VERIFIED' THEN
        RAISE EXCEPTION 'verified staging copy required for swap';
    END IF;
    live_ := v3_meta.purge_relation(code_);
    staging_ := v3_meta.purge_copy_table(code_,'staging');
    PERFORM v3_meta.purge_copy_lock_relocation(code_,staging_);
    -- Under the lock: no build has added a generation since verification, and
    -- the staging shape is still exactly what was verified.
    PERFORM v3_meta.purge_copy_assert_generations(q,code_);
    PERFORM v3_meta.purge_copy_assert_staging_shape(live_,staging_,false);
    IF EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=live_ AND NOT tgisinternal AND tgenabled<>'O') THEN
        RAISE EXCEPTION 'live trigger state outside the copy contract';
    END IF;
    SELECT COALESCE(array_agg(pg_get_triggerdef(oid) ORDER BY tgname),'{}') INTO triggers_
        FROM pg_trigger WHERE tgrelid=live_ AND NOT tgisinternal;
    IF EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=live_ AND NOT tgisinternal
               AND obj_description(oid,'pg_trigger') IS NOT NULL) THEN
        RAISE EXCEPTION 'live trigger comments outside the copy contract';
    END IF;
    moved_ := v3_meta.purge_copy_relocate(code_,staging_,'v3_retired');
    retired_ := v3_meta.purge_copy_table(code_,'retired');
    -- The copy guard is replaced by the production triggers, verbatim.
    EXECUTE format('DROP TRIGGER purge_copy_guard ON %s',staging_);
    FOREACH t IN ARRAY triggers_ LOOP EXECUTE t; END LOOP;
    PERFORM v3_meta.purge_copy_apply_reloptions(staging_,retired_,false);
    EXECUTE format('GRANT SELECT, MAINTAIN ON %s TO v3_derived_purge_executor',staging_);
    diff_ := v3_meta.purge_copy_fingerprint_mismatch(v3_meta.purge_copy_fingerprint(retired_),
                                                     v3_meta.purge_copy_fingerprint(staging_),'full');
    IF cardinality(diff_)<>0 THEN
        RAISE EXCEPTION 'swapped catalog fingerprint mismatch: %', array_to_string(diff_,',');
    END IF;
    IF v3_meta.purge_copy_table(code_,'live')::oid<>s.staging_oid OR retired_::oid<>live_::oid THEN
        RAISE EXCEPTION 'swap relocation identity mismatch';
    END IF;
    INSERT INTO v3_meta.derived_purge_copy_event(copy_id,purge_id,event_kind,relation_code,live_oid,staging_oid,
        rows_total,relation_bytes,operational_payload)
        VALUES(q.copy_id,purge_,'SWAPPED',code_,live_,staging_,s.rows_total,pg_total_relation_size(retired_),moved_)
        RETURNING event_sequence INTO event_;
    RETURN event_;
END $$;

-- 019's swap-back; only its LOCK statement changes (to purge_copy_lock_relocation).
CREATE OR REPLACE FUNCTION v3_meta.purge_copy_swap_back(purge_ uuid, code_ text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
SET lock_timeout='2s' SET statement_timeout='60s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request; s v3_meta.purge_copy_relation_state;
        copy_ regclass; retired_ regclass; t record; moved_ jsonb; event_ bigint;
BEGIN
    r := v3_meta.purge_copy_context(purge_,true);
    q := v3_meta.purge_copy_request_for(r);
    s := v3_meta.purge_copy_assert_position(q.copy_id,code_);
    IF s.copy_position<>'swapped' THEN RAISE EXCEPTION 'swapped relation required for swap-back'; END IF;
    copy_ := v3_meta.purge_copy_table(code_,'live');
    retired_ := v3_meta.purge_copy_table(code_,'retired');
    PERFORM v3_meta.purge_copy_lock_relocation(code_,retired_);
    moved_ := v3_meta.purge_copy_relocate(code_,retired_,'v3_purge_staging');
    FOR t IN SELECT tgname FROM pg_trigger WHERE tgrelid=copy_ AND NOT tgisinternal ORDER BY tgname LOOP
        EXECUTE format('DROP TRIGGER %I ON %s',t.tgname,copy_);
    END LOOP;
    EXECUTE format('CREATE TRIGGER purge_copy_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON %s '
                   'FOR EACH STATEMENT EXECUTE FUNCTION v3_meta.purge_copy_guard()',copy_);
    PERFORM v3_meta.purge_copy_apply_reloptions(copy_,retired_,true);
    PERFORM v3_meta.purge_copy_assert_staging_shape(retired_,copy_,false);
    INSERT INTO v3_meta.derived_purge_copy_event(copy_id,purge_id,event_kind,relation_code,live_oid,staging_oid,
        rows_total,operational_payload)
        VALUES(q.copy_id,purge_,'SWAPPED_BACK',code_,retired_,copy_,s.rows_total,moved_)
        RETURNING event_sequence INTO event_;
    RETURN event_;
END $$;

COMMIT;
