-- Capability only. Installation copies, swaps, drops and deletes no payload row.
-- Owner-accepted design: docs/development/v3-purge-copy-and-drop-design.md
-- (option B, copy-and-drop). Reuses 016's request, authorization, snapshot,
-- assertion, relation enum, executor role and definer pattern; 016 is unchanged.
--
-- Bulk-write safety (docs/development/bulk-database-write-safety.md): the copy
-- writes only fresh v3_purge_staging tables. It never uses replica mode,
-- disables triggers or uses COPY FREEZE; the staging copy guard, CHECK and
-- NOT NULL constraints stay enforced on every chunk.
--
-- Planner statistics: the copy preserves every live column's statistics target
-- and attribute options (including the owner-applied n_distinct = -0.13 on
-- economy_opportunity.system_id64) so the catalog fingerprint matches. After
-- the purge, recalibrating n_distinct from fresh counts is a separate owner
-- step (docs/operations/v3-postgres-server-settings.md, "After the governed
-- purge"); nothing here changes planner statistics.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- The installer must be the payload owner that already owns 016's definers,
-- because the swap moves, re-points and recreates the objects listed here.
DO $$
DECLARE missing_ text;
BEGIN
    IF to_regclass('v3_meta.derived_generation_purge_event') IS NULL
       OR to_regprocedure('v3_meta.purge_assert_request(uuid)') IS NULL
       OR to_regprocedure('v3_meta.purge_open_event(uuid,text,text,text[])') IS NULL THEN
        RAISE EXCEPTION 'copy-and-drop requires the governed purge migration 016';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='v3_derived_purge_executor') THEN
        RAISE EXCEPTION 'copy-and-drop requires the 016 executor role';
    END IF;
    SELECT string_agg(name_,',') INTO missing_ FROM unnest(ARRAY[
        'v3_derived.system_search','v3_derived.economy_opportunity','v3_derived.body_mechanics',
        'v3_derived.system_rating_vector','v3_derived.system_archetype','v3_spatial.cluster_member',
        'v3_app.system_search','v3_derived.system_economy_rating']) name_
    WHERE to_regclass(name_) IS NULL
       OR (SELECT relowner FROM pg_class WHERE oid=to_regclass(name_))<>current_user::regrole;
    IF missing_ IS NOT NULL THEN
        RAISE EXCEPTION 'copy-and-drop functions must be installed by the owner of %', missing_;
    END IF;
    IF (SELECT proowner FROM pg_proc WHERE oid='v3_meta.purge_derived_payload_batch(uuid,text,integer)'::regprocedure)
       <>current_user::regrole THEN
        RAISE EXCEPTION 'copy-and-drop installer must own the 016 purge definers';
    END IF;
    IF NOT has_schema_privilege(current_user,'v3_derived','CREATE') THEN
        RAISE EXCEPTION 'copy-and-drop installer needs CREATE on v3_derived';
    END IF;
END $$;

CREATE SCHEMA v3_purge_staging;
CREATE SCHEMA v3_retired;
COMMENT ON SCHEMA v3_purge_staging IS
    'Copy-and-drop staging copies of one derived relation, written only by v3_meta.purge_copy_chunk.';
COMMENT ON SCHEMA v3_retired IS
    'Copy-and-drop retired derived relations between their swap and their governed DROP.';

-- One copy request per reviewed 016 plan. Its frozen counts come from 016's
-- authorized snapshot and reviewed plan, never from a new observation.
CREATE TABLE v3_meta.derived_purge_copy_request (
    copy_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    purge_id uuid NOT NULL UNIQUE REFERENCES v3_meta.derived_generation_purge_request,
    plan_sha256 text NOT NULL UNIQUE CHECK(plan_sha256 ~ '^[0-9a-f]{64}$'),
    survivor_generation_id uuid NOT NULL,
    target_generation_ids uuid[] NOT NULL CHECK(cardinality(target_generation_ids) BETWEEN 1 AND 2),
    frozen_survivors jsonb NOT NULL CHECK(jsonb_typeof(frozen_survivors)='object'),
    published_search_survivors bigint CHECK(published_search_survivors>=0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE v3_meta.derived_purge_copy_event (
    event_sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    copy_id uuid NOT NULL REFERENCES v3_meta.derived_purge_copy_request,
    purge_id uuid NOT NULL REFERENCES v3_meta.derived_generation_purge_request,
    event_kind text NOT NULL CHECK(event_kind IN (
        'COPY_STARTED','CHUNK_COPIED','INDEXES_BUILT','VERIFIED','SWAPPED',
        'SWAPPED_BACK','FK_VALIDATED','DROPPED','COMPLETED','FAILED_STOP')),
    relation_code text CHECK(relation_code IN (
        'system_search','economy_opportunity','body_mechanics','system_rating_vector')),
    live_oid oid,
    staging_oid oid,
    chunk_low jsonb,
    chunk_high jsonb,
    rows_affected bigint CHECK(rows_affected>=0),
    rows_total bigint CHECK(rows_total>=0),
    source_digest text CHECK(source_digest ~ '^[0-9a-f]{64}$'),
    relation_bytes bigint CHECK(relation_bytes>=0),
    operational_payload jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(operational_payload)='object'),
    failure_evidence jsonb,
    backend_pid integer NOT NULL DEFAULT pg_backend_pid(),
    transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id(),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK((event_kind='COMPLETED')=(relation_code IS NULL)),
    CHECK(event_kind<>'CHUNK_COPIED' OR (rows_affected>0 AND rows_total IS NOT NULL
          AND chunk_low IS NOT NULL AND chunk_high IS NOT NULL AND source_digest IS NOT NULL))
);
CREATE INDEX derived_purge_copy_event_progress ON v3_meta.derived_purge_copy_event
    (copy_id,relation_code,event_sequence DESC);
CREATE TRIGGER retained_purge_copy_request BEFORE UPDATE OR DELETE OR TRUNCATE
ON v3_meta.derived_purge_copy_request FOR EACH STATEMENT
EXECUTE FUNCTION v3_meta.reject_purge_ledger_mutation();
CREATE TRIGGER retained_purge_copy_event BEFORE UPDATE OR DELETE OR TRUNCATE
ON v3_meta.derived_purge_copy_event FOR EACH STATEMENT
EXECUTE FUNCTION v3_meta.reject_purge_ledger_mutation();

-- Relation names come only from 016's enum; location names are fixed literals.
CREATE FUNCTION v3_meta.purge_copy_relname(code_ text) RETURNS text
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
BEGIN
    PERFORM v3_meta.purge_relation(code_);
    RETURN CASE code_ WHEN 'system_search' THEN 'system_search'
        WHEN 'economy_opportunity' THEN 'economy_opportunity'
        WHEN 'body_mechanics' THEN 'body_mechanics'
        WHEN 'system_rating_vector' THEN 'system_rating_vector' END;
END $$;
CREATE FUNCTION v3_meta.purge_copy_table(code_ text, location_ text) RETURNS regclass
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
BEGIN
    RETURN to_regclass(format('%I.%I', CASE location_
        WHEN 'live' THEN 'v3_derived' WHEN 'staging' THEN 'v3_purge_staging'
        WHEN 'retired' THEN 'v3_retired' ELSE NULL END, v3_meta.purge_copy_relname(code_)));
END $$;
-- Primary-key order (generation first) and the key tuple after it.
CREATE FUNCTION v3_meta.purge_copy_order_columns(code_ text) RETURNS text
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
BEGIN
    RETURN 'derived_generation_id,' || v3_meta.purge_order_columns(code_);
END $$;
CREATE FUNCTION v3_meta.purge_copy_key_params(code_ text, parameter_ integer) RETURNS text
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
BEGIN
    PERFORM v3_meta.purge_relation(code_);
    IF parameter_ NOT IN (2,3) THEN RAISE EXCEPTION 'fixed key parameter required'; END IF;
    RETURN CASE code_
        WHEN 'economy_opportunity' THEN format('$%s[1],$%s[2],$%s[3]::smallint',parameter_,parameter_,parameter_)
        WHEN 'body_mechanics' THEN format('$%s[1],$%s[2]',parameter_,parameter_)
        ELSE format('$%s[1]',parameter_) END;
END $$;
CREATE FUNCTION v3_meta.purge_copy_expected_children(code_ text) RETURNS text[]
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
BEGIN
    PERFORM v3_meta.purge_relation(code_);
    RETURN CASE code_
        WHEN 'body_mechanics' THEN ARRAY['v3_derived.economy_opportunity']
        WHEN 'system_rating_vector' THEN ARRAY['v3_derived.body_mechanics','v3_derived.system_archetype',
            'v3_derived.system_search','v3_spatial.cluster_member']
        ELSE ARRAY[]::text[] END;
END $$;
CREATE FUNCTION v3_meta.purge_copy_expected_views(code_ text) RETURNS text[]
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
BEGIN
    PERFORM v3_meta.purge_relation(code_);
    RETURN CASE code_
        WHEN 'system_search' THEN ARRAY['v3_app.system_search']
        WHEN 'system_rating_vector' THEN ARRAY['v3_derived.system_economy_rating']
        ELSE ARRAY[]::text[] END;
END $$;

-- Catalog fingerprint: columns, constraints (definition, deferrability,
-- enforcement, validity), indexes with predicates, triggers, reloptions, ACL,
-- owner and comments. Table names inside definitions are normalized so the
-- same shape compares equal across v3_derived, v3_purge_staging and v3_retired.
CREATE FUNCTION v3_meta.purge_copy_fingerprint(relation_ regclass) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
SELECT jsonb_build_object(
 'relation',(SELECT jsonb_build_object('kind',c.relkind,'persistence',c.relpersistence,
     'access_method',(SELECT amname FROM pg_am WHERE oid=c.relam),
     'tablespace',(SELECT spcname FROM pg_tablespace WHERE oid=c.reltablespace),
     'replica_identity',c.relreplident,'row_security',c.relrowsecurity,
     'force_row_security',c.relforcerowsecurity,'partition',c.relispartition,
     'typed',c.reloftype<>0,'has_toast',c.reltoastrelid<>0,
     'owner',pg_get_userbyid(c.relowner),'comment',obj_description(c.oid,'pg_class'))
   FROM pg_class c WHERE c.oid=relation_),
 'acl',(SELECT COALESCE(jsonb_agg(item ORDER BY item::text),'[]'::jsonb) FROM (
     SELECT jsonb_build_array(
        CASE a.grantor WHEN 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantor) END,
        CASE a.grantee WHEN 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
        a.privilege_type,a.is_grantable) AS item
     FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
     WHERE c.oid=relation_) acl),
 'reloptions',(SELECT COALESCE(jsonb_agg(o ORDER BY o),'[]'::jsonb)
   FROM pg_class c CROSS JOIN LATERAL unnest(c.reloptions) o WHERE c.oid=relation_),
 'toast_reloptions',(SELECT COALESCE(jsonb_agg(o ORDER BY o),'[]'::jsonb)
   FROM pg_class c JOIN pg_class t ON t.oid=c.reltoastrelid
   CROSS JOIN LATERAL unnest(t.reloptions) o WHERE c.oid=relation_),
 'columns',(SELECT jsonb_agg(jsonb_build_object('name',a.attname,
        'type',format_type(a.atttypid,a.atttypmod),'not_null',a.attnotnull,
        'default',pg_get_expr(d.adbin,d.adrelid),
        'collation',(SELECT n.nspname||'.'||l.collname FROM pg_collation l
                     JOIN pg_namespace n ON n.oid=l.collnamespace WHERE l.oid=a.attcollation),
        'storage',a.attstorage,'compression',a.attcompression,'statistics',a.attstattarget,
        'identity',a.attidentity,'generated',a.attgenerated,'options',a.attoptions,
        'acl',a.attacl::text,'comment',col_description(a.attrelid,a.attnum)) ORDER BY a.attnum)
   FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
   WHERE a.attrelid=relation_ AND a.attnum>0 AND NOT a.attisdropped),
 'constraints',(SELECT COALESCE(jsonb_object_agg(k.conname,jsonb_build_object('type',k.contype,
        'definition',regexp_replace(pg_get_constraintdef(k.oid),' NOT VALID$',''),
        'deferrable',k.condeferrable,'deferred',k.condeferred,'enforced',k.conenforced,
        'no_inherit',k.connoinherit,'local',k.conislocal,'inherited',k.coninhcount,
        'validated',k.convalidated,'index',(SELECT relname FROM pg_class WHERE oid=k.conindid),
        'comment',obj_description(k.oid,'pg_constraint'))),'{}'::jsonb)
   FROM pg_constraint k WHERE k.conrelid=relation_),
 'indexes',(SELECT COALESCE(jsonb_object_agg(ic.relname,jsonb_build_object(
        'definition',replace(pg_get_indexdef(i.indexrelid),
            format(' INDEX %I ON %s ',ic.relname,relation_::text),' INDEX <index> ON <relation> '),
        'unique',i.indisunique,'primary',i.indisprimary,'exclusion',i.indisexclusion,
        'immediate',i.indimmediate,'nulls_not_distinct',i.indnullsnotdistinct,
        'replica_identity',i.indisreplident,'clustered',i.indisclustered,
        'valid',i.indisvalid,'ready',i.indisready,'live',i.indislive,
        'tablespace',(SELECT spcname FROM pg_tablespace WHERE oid=ic.reltablespace),
        'reloptions',ic.reloptions,'comment',obj_description(ic.oid,'pg_class'))),'{}'::jsonb)
   FROM pg_index i JOIN pg_class ic ON ic.oid=i.indexrelid WHERE i.indrelid=relation_),
 'triggers',(SELECT COALESCE(jsonb_object_agg(t.tgname,jsonb_build_object(
        'definition',replace(pg_get_triggerdef(t.oid),format(' ON %s ',relation_::text),' ON <relation> '),
        'enabled',t.tgenabled,'comment',obj_description(t.oid,'pg_trigger'))),'{}'::jsonb)
   FROM pg_trigger t WHERE t.tgrelid=relation_ AND NOT t.tgisinternal),
 'statistics',(SELECT COALESCE(jsonb_agg(jsonb_build_array(s.stxname,s.stxkind::text,
        pg_get_statisticsobjdef_columns(s.oid),s.stxstattarget) ORDER BY s.stxname),'[]'::jsonb)
   FROM pg_statistic_ext s WHERE s.stxrelid=relation_),
 'policies',(SELECT count(*) FROM pg_policy WHERE polrelid=relation_),
 'rules',(SELECT count(*) FROM pg_rewrite WHERE ev_class=relation_),
 'inheritance',(SELECT count(*) FROM pg_inherits WHERE inhrelid=relation_ OR inhparent=relation_),
 'publications',(SELECT count(*) FROM pg_publication_rel WHERE prrelid=relation_))
$$;
-- Returns the differing fingerprint keys; empty means equal. Mode 'staging'
-- leaves triggers and reloptions to the caller (the copy guard and disabled
-- autovacuum are deliberate there); mode 'full' compares everything. The only
-- tolerated difference is a constraint NOT VALID on live and valid on the copy.
CREATE FUNCTION v3_meta.purge_copy_fingerprint_mismatch(live_ jsonb, copy_ jsonb, mode_ text) RETURNS text[]
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE key_ text; name_ text; result_ text[] := '{}';
BEGIN
    IF mode_ NOT IN ('staging','full') THEN RAISE EXCEPTION 'fingerprint mode must be staging or full'; END IF;
    IF live_ IS NULL OR copy_ IS NULL THEN RETURN ARRAY['missing']; END IF;
    FOR key_ IN SELECT k FROM (SELECT jsonb_object_keys(live_) k UNION SELECT jsonb_object_keys(copy_)) s ORDER BY k LOOP
        IF mode_='staging' AND key_ IN ('triggers','reloptions','toast_reloptions') THEN CONTINUE; END IF;
        IF key_='constraints' THEN
            FOR name_ IN SELECT k FROM (SELECT jsonb_object_keys(live_->'constraints') k
                                        UNION SELECT jsonb_object_keys(copy_->'constraints') k) s ORDER BY k LOOP
                IF (live_->'constraints'->name_) IS NULL OR (copy_->'constraints'->name_) IS NULL
                   OR (live_->'constraints'->name_)-'validated' IS DISTINCT FROM (copy_->'constraints'->name_)-'validated'
                   OR ((live_->'constraints'->name_->'validated')='true'::jsonb
                       AND (copy_->'constraints'->name_->'validated') IS DISTINCT FROM 'true'::jsonb) THEN
                    result_ := array_append(result_,'constraints.'||name_);
                END IF;
            END LOOP;
        ELSIF live_->key_ IS DISTINCT FROM copy_->key_ THEN
            result_ := array_append(result_,key_);
        END IF;
    END LOOP;
    RETURN result_;
END $$;

-- Non-owned objects that depend on a relation or its row type. A view is its
-- _RETURN rule; an FK is the referencing table's constraint. Anything else
-- is reported verbatim so the caller fails closed.
CREATE FUNCTION v3_meta.purge_copy_external_dependents(relation_ regclass) RETURNS text[]
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
SELECT COALESCE(array_agg(DISTINCT label ORDER BY label),'{}') FROM (
 SELECT CASE
   WHEN d.classid='pg_rewrite'::regclass THEN
     'view:'||(SELECT ev_class::regclass::text FROM pg_rewrite WHERE oid=d.objid)
   WHEN d.classid='pg_constraint'::regclass AND (SELECT contype FROM pg_constraint WHERE oid=d.objid)='f' THEN
     'fk:'||(SELECT conrelid::regclass::text FROM pg_constraint WHERE oid=d.objid)
   ELSE 'other:'||pg_describe_object(d.classid,d.objid,d.objsubid) END AS label
 FROM pg_depend d
 WHERE d.deptype IN ('n','a')
   AND ((d.refclassid='pg_class'::regclass AND d.refobjid=relation_)
        OR (d.refclassid='pg_type'::regclass AND d.refobjid IN (
            SELECT reltype FROM pg_class WHERE oid=relation_
            UNION SELECT typarray FROM pg_type WHERE oid=(SELECT reltype FROM pg_class WHERE oid=relation_))))
   AND NOT (d.classid='pg_constraint'::regclass AND EXISTS (
        SELECT 1 FROM pg_constraint WHERE oid=d.objid AND conrelid=relation_))
   AND NOT (d.classid='pg_attrdef'::regclass AND EXISTS (
        SELECT 1 FROM pg_attrdef WHERE oid=d.objid AND adrelid=relation_))
   AND NOT (d.classid='pg_class'::regclass AND EXISTS (
        SELECT 1 FROM pg_index WHERE indexrelid=d.objid AND indrelid=relation_))
   AND NOT (d.classid='pg_trigger'::regclass AND EXISTS (
        SELECT 1 FROM pg_trigger WHERE oid=d.objid AND tgrelid=relation_))
   AND NOT (d.classid='pg_rewrite'::regclass AND EXISTS (
        SELECT 1 FROM pg_rewrite WHERE oid=d.objid AND ev_class=relation_))
   AND NOT (d.classid='pg_statistic_ext'::regclass AND EXISTS (
        SELECT 1 FROM pg_statistic_ext WHERE oid=d.objid AND stxrelid=relation_))
   AND NOT (d.classid='pg_type'::regclass AND d.objid=(SELECT typarray FROM pg_type
        WHERE oid=(SELECT reltype FROM pg_class WHERE oid=relation_)))
   AND NOT (d.classid='pg_class'::regclass AND d.objid=relation_)
) s
$$;

-- The staging copy guard. UPDATE, DELETE and TRUNCATE are always rejected;
-- INSERT only inside purge_copy_chunk as its definer for this relation (the
-- 016 PG_CONTEXT pattern). A spoofed GUC alone is never sufficient.
CREATE FUNCTION v3_meta.purge_copy_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE context_ text;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'staging copy relation accepts only governed chunk inserts';
    END IF;
    GET DIAGNOSTICS context_ = PG_CONTEXT;
    IF TG_TABLE_SCHEMA<>'v3_purge_staging' OR TG_TABLE_NAME NOT IN
       ('system_search','economy_opportunity','body_mechanics','system_rating_vector')
       OR current_user::regrole<>(SELECT proowner FROM pg_proc
           WHERE oid='v3_meta.purge_copy_chunk(uuid,text,integer)'::regprocedure)
       OR position('function v3_meta.purge_copy_chunk(' IN context_)=0
       OR current_setting('v3.purge_copy_relation',true) IS DISTINCT FROM TG_TABLE_NAME THEN
        RAISE EXCEPTION 'staging INSERT requires governed purge copy chunk context';
    END IF;
    RETURN NULL;
END $$;

-- Mirrors scripts/operator/lib/v3_purge_state.py STATE_SQL: any open 016
-- destructive operation or fence, or any 016 integrity stop, refuses the copy.
-- Those events hold the old relation OIDs.
CREATE FUNCTION v3_meta.purge_copy_assert_016_clear() RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF EXISTS (
        WITH scopes AS MATERIALIZED (
            SELECT DISTINCT purge_id,relation_code,event_kind FROM v3_meta.derived_generation_purge_event
            WHERE event_kind IN ('BATCH_STARTED','REWRITE_STARTED','SERVICES_QUIESCED',
                                 'WRITER_FENCE_ENABLED','AUTOVACUUM_DISABLED'))
        SELECT 1 FROM scopes s CROSS JOIN LATERAL v3_meta.purge_open_event(s.purge_id,s.relation_code,s.event_kind,
            CASE s.event_kind
              WHEN 'BATCH_STARTED' THEN ARRAY['BATCH_COMMITTED','BATCH_ROLLED_BACK']
              WHEN 'REWRITE_STARTED' THEN ARRAY['REWRITE_VERIFIED','REWRITE_ROLLED_BACK','FAILED_STOP']
              WHEN 'SERVICES_QUIESCED' THEN ARRAY['SERVICES_RESTORED']
              WHEN 'WRITER_FENCE_ENABLED' THEN ARRAY['WRITER_FENCE_RESTORED']
              WHEN 'AUTOVACUUM_DISABLED' THEN ARRAY['AUTOVACUUM_RESTORED'] END) e
        WHERE e.event_sequence IS NOT NULL)
    OR EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event
        WHERE event_kind='FAILED_STOP' AND failure_evidence->'integrity_failed'='true'::jsonb) THEN
        RAISE EXCEPTION 'open governed purge event; copy-and-drop refused';
    END IF;
END $$;

-- Ledger state of one relation. phase ignores FK_VALIDATED; copy_position is
-- where the copy physically is: none (never started or abandoned), staged,
-- swapped, dropped. failed: a non-abandon FAILED_STOP since the latest
-- COPY_STARTED. fk_validated: an FK_VALIDATED after the latest swap/swap-back.
CREATE TYPE v3_meta.purge_copy_relation_state AS (
    phase text, phase_sequence bigint, copy_position text, failed boolean,
    copy_started bigint, copy_started_at timestamptz, verified bigint,
    swapped bigint, swapped_back bigint, fk_validated bigint,
    rows_total bigint, chunks bigint, last_high jsonb, last_copy_at timestamptz,
    staging_oid oid, retired_oid oid);
CREATE FUNCTION v3_meta.purge_copy_state(copy_ uuid, code_ text) RETURNS v3_meta.purge_copy_relation_state
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
DECLARE e v3_meta.derived_purge_copy_event; c v3_meta.derived_purge_copy_event;
        s v3_meta.purge_copy_relation_state; dropped_ boolean;
BEGIN
    PERFORM v3_meta.purge_relation(code_);
    s.failed := false; s.rows_total := 0; s.chunks := 0; s.copy_position := 'none';
    SELECT * INTO e FROM v3_meta.derived_purge_copy_event x WHERE x.copy_id=copy_ AND x.relation_code=code_
        AND x.event_kind IN ('COPY_STARTED','CHUNK_COPIED','INDEXES_BUILT','VERIFIED','SWAPPED',
                             'SWAPPED_BACK','DROPPED','FAILED_STOP')
        ORDER BY x.event_sequence DESC LIMIT 1;
    s.phase := e.event_kind; s.phase_sequence := e.event_sequence;
    SELECT x.event_sequence,x.created_at,x.staging_oid INTO s.copy_started,s.copy_started_at,s.staging_oid
        FROM v3_meta.derived_purge_copy_event x
        WHERE x.copy_id=copy_ AND x.relation_code=code_ AND x.event_kind='COPY_STARTED'
        ORDER BY x.event_sequence DESC LIMIT 1;
    IF s.copy_started IS NULL OR (e.event_kind='FAILED_STOP' AND e.operational_payload->'abandoned'='true'::jsonb) THEN
        RETURN s;
    END IF;
    SELECT max(x.event_sequence) FILTER (WHERE x.event_kind='VERIFIED'),
           max(x.event_sequence) FILTER (WHERE x.event_kind='SWAPPED'),
           max(x.event_sequence) FILTER (WHERE x.event_kind='SWAPPED_BACK'),
           COALESCE(bool_or(x.event_kind='FAILED_STOP'
               AND x.operational_payload->'abandoned' IS DISTINCT FROM 'true'::jsonb),false),
           COALESCE(bool_or(x.event_kind='DROPPED'),false)
      INTO s.verified,s.swapped,s.swapped_back,s.failed,dropped_
      FROM v3_meta.derived_purge_copy_event x
     WHERE x.copy_id=copy_ AND x.relation_code=code_ AND x.event_sequence>s.copy_started;
    s.copy_position := CASE WHEN dropped_ THEN 'dropped'
        WHEN COALESCE(s.swapped,0)>COALESCE(s.swapped_back,0) THEN 'swapped' ELSE 'staged' END;
    SELECT max(x.event_sequence) INTO s.fk_validated FROM v3_meta.derived_purge_copy_event x
        WHERE x.copy_id=copy_ AND x.relation_code=code_ AND x.event_kind='FK_VALIDATED'
          AND x.event_sequence>GREATEST(COALESCE(s.swapped,0),COALESCE(s.swapped_back,0),s.copy_started);
    SELECT * INTO c FROM v3_meta.derived_purge_copy_event x
        WHERE x.copy_id=copy_ AND x.relation_code=code_ AND x.event_kind='CHUNK_COPIED'
          AND x.event_sequence>s.copy_started ORDER BY x.event_sequence DESC LIMIT 1;
    IF c.event_sequence IS NOT NULL THEN
        s.rows_total := c.rows_total; s.last_high := c.chunk_high; s.last_copy_at := c.created_at;
        SELECT count(*) INTO s.chunks FROM v3_meta.derived_purge_copy_event x
            WHERE x.copy_id=copy_ AND x.relation_code=code_ AND x.event_kind='CHUNK_COPIED'
              AND x.event_sequence>s.copy_started;
    ELSE
        s.last_copy_at := s.copy_started_at;
    END IF;
    SELECT x.live_oid INTO s.retired_oid FROM v3_meta.derived_purge_copy_event x
        WHERE x.copy_id=copy_ AND x.relation_code=code_ AND x.event_kind='SWAPPED'
          AND x.event_sequence>s.copy_started ORDER BY x.event_sequence DESC LIMIT 1;
    RETURN s;
END $$;

-- Authenticate through 016 and serialize copy operations. Forward steps use
-- purge_assert_request (pointer, lifecycle and schema-ledger drift refuse) and
-- the shared 016 fence; undo steps use 016's recovery authentication.
CREATE FUNCTION v3_meta.purge_copy_context(purge_ uuid, recovery_ boolean)
RETURNS v3_meta.derived_generation_purge_request
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r v3_meta.derived_generation_purge_request;
BEGIN
    IF recovery_ IS NULL THEN RAISE EXCEPTION 'copy context mode required'; END IF;
    IF recovery_ THEN r := v3_meta.purge_recovery_request(purge_);
    ELSE
        r := v3_meta.purge_assert_request(purge_);
        PERFORM v3_meta.purge_copy_assert_016_clear();
    END IF;
    IF NOT pg_try_advisory_xact_lock(764019001) THEN
        RAISE EXCEPTION 'competing purge copy operation';
    END IF;
    RETURN r;
END $$;
CREATE FUNCTION v3_meta.purge_copy_request_for(r v3_meta.derived_generation_purge_request)
RETURNS v3_meta.derived_purge_copy_request
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
DECLARE q v3_meta.derived_purge_copy_request;
BEGIN
    SELECT * INTO q FROM v3_meta.derived_purge_copy_request WHERE plan_sha256=r.plan_sha256;
    IF NOT FOUND THEN RAISE EXCEPTION 'no copy request for purge plan'; END IF;
    IF q.survivor_generation_id<>r.expected_derived_id THEN
        RAISE EXCEPTION 'copy request survivor differs from purge request';
    END IF;
    RETURN q;
END $$;
-- Children before parents: system_search, economy_opportunity, body_mechanics,
-- system_rating_vector. Every earlier relation must be DROPPED (its DROP funds
-- the next copy) and no later relation may have started.
CREATE FUNCTION v3_meta.purge_copy_assert_order(copy_ uuid, code_ text) RETURNS void
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
DECLARE other_ text; seen_ boolean := false; s v3_meta.purge_copy_relation_state;
BEGIN
    PERFORM v3_meta.purge_relation(code_);
    FOREACH other_ IN ARRAY ARRAY['system_search','economy_opportunity','body_mechanics','system_rating_vector'] LOOP
        IF other_=code_ THEN seen_ := true; CONTINUE; END IF;
        s := v3_meta.purge_copy_state(copy_,other_);
        IF (NOT seen_ AND s.copy_position<>'dropped') OR (seen_ AND s.copy_position<>'none') THEN
            RAISE EXCEPTION 'wrong copy-and-drop relation order';
        END IF;
    END LOOP;
END $$;
-- The catalog must agree with the ledger before and after every step.
CREATE FUNCTION v3_meta.purge_copy_assert_position(copy_ uuid, code_ text)
RETURNS v3_meta.purge_copy_relation_state
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
DECLARE s v3_meta.purge_copy_relation_state; staging_ regclass; retired_ regclass;
BEGIN
    s := v3_meta.purge_copy_state(copy_,code_);
    staging_ := v3_meta.purge_copy_table(code_,'staging');
    retired_ := v3_meta.purge_copy_table(code_,'retired');
    IF v3_meta.purge_copy_table(code_,'live') IS NULL
       OR (s.copy_position='staged' AND (staging_ IS NULL OR retired_ IS NOT NULL OR staging_::oid<>s.staging_oid))
       OR (s.copy_position='swapped' AND (retired_ IS NULL OR staging_ IS NOT NULL OR retired_::oid<>s.retired_oid
           OR v3_meta.purge_copy_table(code_,'live')::oid<>s.staging_oid))
       OR (s.copy_position IN ('none','dropped') AND (staging_ IS NOT NULL OR retired_ IS NOT NULL)) THEN
        RAISE EXCEPTION 'copy ledger and catalog disagree for %', code_;
    END IF;
    RETURN s;
END $$;

-- Distinct generations by an index skip-scan of the primary key's leading column.
CREATE FUNCTION v3_meta.purge_copy_generations(relation_ regclass) RETURNS uuid[]
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
DECLARE result_ uuid[];
BEGIN
    EXECUTE format('WITH RECURSIVE g(id) AS ((SELECT derived_generation_id FROM %1$s ORDER BY 1 LIMIT 1)
        UNION ALL SELECT (SELECT derived_generation_id FROM %1$s WHERE derived_generation_id>g.id ORDER BY 1 LIMIT 1)
        FROM g WHERE g.id IS NOT NULL) SELECT COALESCE(array_agg(id ORDER BY id),''{}'') FROM g WHERE id IS NOT NULL',
        relation_) INTO result_;
    RETURN result_;
END $$;
CREATE FUNCTION v3_meta.purge_copy_assert_generations(q v3_meta.derived_purge_copy_request, code_ text) RETURNS void
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
BEGIN
    IF NOT v3_meta.purge_copy_generations(v3_meta.purge_copy_table(code_,'live'))
           <@ (q.target_generation_ids || q.survivor_generation_id) THEN
        RAISE EXCEPTION 'live relation holds a generation beyond the survivor and purge targets';
    END IF;
END $$;

-- Reset target reloptions to source's, optionally with autovacuum off (staging).
CREATE FUNCTION v3_meta.purge_copy_apply_reloptions(target_ regclass, source_ regclass, staging_ boolean)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE o record; target_toast_ oid; source_toast_ oid;
BEGIN
    SELECT reltoastrelid INTO target_toast_ FROM pg_class WHERE oid=target_;
    SELECT reltoastrelid INTO source_toast_ FROM pg_class WHERE oid=source_;
    FOR o IN SELECT option_name FROM pg_class c CROSS JOIN LATERAL pg_options_to_table(c.reloptions)
             WHERE c.oid=target_ LOOP
        EXECUTE format('ALTER TABLE %s RESET (%I)',target_,o.option_name);
    END LOOP;
    FOR o IN SELECT option_name FROM pg_class c CROSS JOIN LATERAL pg_options_to_table(c.reloptions)
             WHERE c.oid=target_toast_ LOOP
        EXECUTE format('ALTER TABLE %s RESET (toast.%I)',target_,o.option_name);
    END LOOP;
    FOR o IN SELECT option_name,option_value FROM pg_class c CROSS JOIN LATERAL pg_options_to_table(c.reloptions)
             WHERE c.oid=source_ LOOP
        EXECUTE format('ALTER TABLE %s SET (%I = %L)',target_,o.option_name,o.option_value);
    END LOOP;
    IF target_toast_<>0 THEN
        FOR o IN SELECT option_name,option_value FROM pg_class c CROSS JOIN LATERAL pg_options_to_table(c.reloptions)
                 WHERE c.oid=source_toast_ LOOP
            EXECUTE format('ALTER TABLE %s SET (toast.%I = %L)',target_,o.option_name,o.option_value);
        END LOOP;
    END IF;
    IF staging_ THEN
        EXECUTE format('ALTER TABLE %s SET (autovacuum_enabled = false)',target_);
        IF target_toast_<>0 THEN
            EXECUTE format('ALTER TABLE %s SET (toast.autovacuum_enabled = false)',target_);
        END IF;
    END IF;
END $$;
-- Staging-mode comparison: fingerprint (minus triggers/reloptions), exactly the
-- copy guard, and live reloptions with autovacuum forced off. With prepared_
-- (end of prepare, before any row is copied) only the parts that are final at
-- prepare are compared: relation attributes, ACL, columns (statistics targets
-- and attribute options included), CHECK and NOT NULL constraints, extended
-- statistics, policies, rules, inheritance and publications. Keys, indexes
-- and FKs are built later and compared in full at verify and swap.
CREATE FUNCTION v3_meta.purge_copy_assert_staging_shape(live_ regclass, staging_ regclass, prepared_ boolean)
RETURNS void LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
DECLARE l jsonb; s jsonb; diff_ text[];
BEGIN
    IF prepared_ IS NULL THEN RAISE EXCEPTION 'staging shape mode required'; END IF;
    l := v3_meta.purge_copy_fingerprint(live_);
    s := v3_meta.purge_copy_fingerprint(staging_);
    IF prepared_ THEN
        SELECT (x - 'indexes') || jsonb_build_object('constraints',COALESCE((SELECT jsonb_object_agg(key,value)
                   FROM jsonb_each(x->'constraints') WHERE value->>'type' IN ('c','n')),'{}'::jsonb))
          INTO l FROM (SELECT l AS x) f;
        SELECT (x - 'indexes') || jsonb_build_object('constraints',COALESCE((SELECT jsonb_object_agg(key,value)
                   FROM jsonb_each(x->'constraints') WHERE value->>'type' IN ('c','n')),'{}'::jsonb))
          INTO s FROM (SELECT s AS x) f;
    END IF;
    diff_ := v3_meta.purge_copy_fingerprint_mismatch(l,s,'staging');
    IF cardinality(diff_)<>0 THEN
        RAISE EXCEPTION '% catalog fingerprint mismatch: %', CASE WHEN prepared_ THEN 'prepared staging'
            ELSE 'staging' END, array_to_string(diff_,',');
    END IF;
    IF (SELECT array_agg(tgname::text ORDER BY tgname) FROM pg_trigger WHERE tgrelid=staging_ AND NOT tgisinternal)
           IS DISTINCT FROM ARRAY['purge_copy_guard']
       OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=staging_ AND tgname='purge_copy_guard'
           AND tgfoid='v3_meta.purge_copy_guard()'::regprocedure AND tgenabled='O' AND tgtype=62) THEN
        RAISE EXCEPTION 'staging copy guard is not exactly installed';
    END IF;
    IF (SELECT COALESCE(jsonb_agg(o ORDER BY o),'[]'::jsonb) FROM (
            SELECT o FROM jsonb_array_elements_text(l->'reloptions') o WHERE o NOT LIKE 'autovacuum\_enabled=%'
            UNION ALL SELECT 'autovacuum_enabled=false') x) IS DISTINCT FROM s->'reloptions'
       OR ((s->'relation'->'has_toast')='true'::jsonb AND
           (SELECT COALESCE(jsonb_agg(o ORDER BY o),'[]'::jsonb) FROM (
            SELECT o FROM jsonb_array_elements_text(l->'toast_reloptions') o WHERE o NOT LIKE 'autovacuum\_enabled=%'
            UNION ALL SELECT 'autovacuum_enabled=false') x) IS DISTINCT FROM s->'toast_reloptions') THEN
        RAISE EXCEPTION 'staging reloptions differ from live with autovacuum off';
    END IF;
END $$;

-- Phase 1: create the staging table from the live catalog, empty, guarded.
CREATE FUNCTION v3_meta.purge_copy_prepare(purge_ uuid, code_ text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
SET lock_timeout='5s' SET statement_timeout='60s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request;
        s v3_meta.purge_copy_relation_state; live_ regclass; staging_ regclass; a record; event_ bigint; published_ jsonb;
        survivors_ jsonb; comment_ text; k record; o record;
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
    INSERT INTO v3_meta.derived_purge_copy_event(copy_id,purge_id,event_kind,relation_code,live_oid,staging_oid,
        rows_total,operational_payload)
        VALUES(q.copy_id,purge_,'COPY_STARTED',code_,live_,staging_,0,
            jsonb_build_object('frozen_survivors',(q.frozen_survivors->>code_)::bigint,
                'live_relfilenode',(SELECT relfilenode FROM pg_class WHERE oid=live_)))
        RETURNING event_sequence INTO event_;
    RETURN event_;
END $$;

-- Phase 2: one resumable, bounded chunk per transaction. Rows come only from
-- the frozen survivor generation in primary-key order after the last ledgered
-- key; each chunk records its key range, row count and a source digest.
-- Row text feeds the digests, so its output settings are pinned around those
-- statements only. They are not function SET clauses: 016's request check
-- compares to_jsonb pointer rows (timestamptz) in the caller's own TimeZone.
CREATE FUNCTION v3_meta.purge_copy_output_settings(prior_ jsonb) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE name_ text; result_ jsonb := '{}';
BEGIN
    FOREACH name_ IN ARRAY ARRAY['TimeZone','DateStyle','IntervalStyle','extra_float_digits','bytea_output'] LOOP
        result_ := result_ || jsonb_build_object(name_,current_setting(name_));
        PERFORM set_config(name_, CASE WHEN prior_ IS NOT NULL THEN prior_->>name_ ELSE CASE name_
            WHEN 'TimeZone' THEN 'UTC' WHEN 'DateStyle' THEN 'ISO, YMD' WHEN 'IntervalStyle' THEN 'postgres'
            WHEN 'extra_float_digits' THEN '1' WHEN 'bytea_output' THEN 'hex' END END, true);
    END LOOP;
    RETURN result_;
END $$;

CREATE FUNCTION v3_meta.purge_copy_chunk(purge_ uuid, code_ text, max_rows_ integer) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
SET lock_timeout='5s' SET statement_timeout='60s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request; s v3_meta.purge_copy_relation_state;
        live_ regclass; staging_ regclass; last_ bigint[]; order_ text; key_ text; after_ text;
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
    after_ := CASE WHEN last_ IS NULL THEN '' ELSE
        format(' AND (%s) > ($1,%s)',order_,v3_meta.purge_copy_key_params(code_,2)) END;
    PERFORM set_config('v3.purge_copy_relation',v3_meta.purge_copy_relname(code_),true);
    output_ := v3_meta.purge_copy_output_settings(NULL);
    EXECUTE format('WITH chosen AS MATERIALIZED (SELECT * FROM %1$s WHERE derived_generation_id=$1%2$s ORDER BY %3$s LIMIT $3),
        inserted AS (INSERT INTO %4$s SELECT * FROM chosen ORDER BY %3$s RETURNING 1)
        SELECT (SELECT count(*) FROM inserted),count(*),min(%5$s),max(%5$s),
               encode(sha256(convert_to(COALESCE(string_agg(md5(c::text),'''' ORDER BY %3$s),''''),''UTF8'')),''hex'')
        FROM chosen c',live_,after_,order_,staging_,key_)
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

-- Phase 3: generate primary/unique keys and indexes from the live catalog
-- with their canonical names, after the copy is complete.
CREATE FUNCTION v3_meta.purge_copy_build_indexes(purge_ uuid, code_ text) RETURNS bigint
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

-- Phase 4: verification. Any mismatch raises; nothing is ledgered.
CREATE FUNCTION v3_meta.purge_copy_verify(purge_ uuid, code_ text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='5s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request; s v3_meta.purge_copy_relation_state;
        live_ regclass; staging_ regclass; e record; k record; low_ bigint[]; high_ bigint[];
        n bigint; total_ bigint; digest_ text; min_ bigint[]; max_ bigint[]; mismatch_ bigint;
        frozen_ bigint; order_ text; key_ text; range_ text; fks_ text[] := '{}'; event_ bigint; output_ jsonb;
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
    range_ := format('(%s) >= ($1,%s) AND (%s) <= ($1,%s)',order_,v3_meta.purge_copy_key_params(code_,2),
                     order_,v3_meta.purge_copy_key_params(code_,3));
    total_ := 0;
    output_ := v3_meta.purge_copy_output_settings(NULL);
    FOR e IN SELECT * FROM v3_meta.derived_purge_copy_event WHERE copy_id=q.copy_id AND relation_code=code_
             AND event_kind='CHUNK_COPIED' AND event_sequence>s.copy_started ORDER BY event_sequence LOOP
        SELECT array_agg(value::bigint ORDER BY ordinality) INTO low_ FROM jsonb_array_elements_text(e.chunk_low) WITH ORDINALITY;
        SELECT array_agg(value::bigint ORDER BY ordinality) INTO high_ FROM jsonb_array_elements_text(e.chunk_high) WITH ORDINALITY;
        EXECUTE format('SELECT count(*),min(%1$s),max(%1$s),
            encode(sha256(convert_to(COALESCE(string_agg(md5(c::text),'''' ORDER BY %2$s),''''),''UTF8'')),''hex'')
            FROM %3$s c WHERE derived_generation_id=$1 AND %4$s',key_,order_,staging_,range_)
            INTO n,min_,max_,digest_ USING q.survivor_generation_id,low_,high_;
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

-- Relocation shared by swap and swap-back. Moves current v3_derived.<rel> to
-- out_schema_ and the in_ table into v3_derived; then recreates the dependent
-- views from their captured definitions and re-points child FKs NOT VALID.
-- Fails closed on any dependent outside the fixed expected children and views.
CREATE FUNCTION v3_meta.purge_copy_relocate(code_ text, in_ regclass, out_schema_ text) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE current_ regclass; relname_ text; children_ text[]; views_ text[]; expected_ text[];
        fk record; v record; fks_ jsonb := '[]'; view_defs_ jsonb := '[]'; definition_ text; comment_ text;
        t record; repointed_ jsonb := '[]';
BEGIN
    IF out_schema_ NOT IN ('v3_retired','v3_purge_staging') THEN RAISE EXCEPTION 'fixed relocation schema required'; END IF;
    relname_ := v3_meta.purge_copy_relname(code_);
    current_ := v3_meta.purge_copy_table(code_,'live');
    -- Every lock first, deterministic order, under the caller's lock_timeout.
    EXECUTE format('LOCK TABLE %s, %s IN ACCESS EXCLUSIVE MODE',current_,in_);
    FOREACH definition_ IN ARRAY v3_meta.purge_copy_expected_children(code_) || v3_meta.purge_copy_expected_views(code_) LOOP
        EXECUTE format('LOCK TABLE %s IN ACCESS EXCLUSIVE MODE',definition_::regclass);
    END LOOP;
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

-- Phase 5: the swap, one transaction, lock_timeout 2s (retry is the operator's).
CREATE FUNCTION v3_meta.purge_copy_swap(purge_ uuid, code_ text) RETURNS bigint
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
    EXECUTE format('LOCK TABLE %s, %s IN ACCESS EXCLUSIVE MODE',live_,staging_);
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

-- Undo after a swap and before DROP: the exact reverse. Recovery-authenticated.
-- Child FKs come back NOT VALID; purge_copy_validate_fks validates them again.
CREATE FUNCTION v3_meta.purge_copy_swap_back(purge_ uuid, code_ text) RETURNS bigint
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
    EXECUTE format('LOCK TABLE %s, %s IN ACCESS EXCLUSIVE MODE',copy_,retired_);
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

-- After commit of a swap or swap-back: VALIDATE every re-pointed child FK
-- (SHARE UPDATE EXCLUSIVE on the child; reads continue). A relation without
-- children still records FK_VALIDATED, which DROP requires.
CREATE FUNCTION v3_meta.purge_copy_validate_fks(purge_ uuid, code_ text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET lock_timeout='5s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request; s v3_meta.purge_copy_relation_state;
        live_ regclass; k record; validated_ jsonb := '[]'; event_ bigint;
BEGIN
    r := v3_meta.purge_copy_context(purge_,true);
    q := v3_meta.purge_copy_request_for(r);
    s := v3_meta.purge_copy_assert_position(q.copy_id,code_);
    IF s.phase NOT IN ('SWAPPED','SWAPPED_BACK') THEN
        RAISE EXCEPTION 'swap or swap-back required before FK validation';
    END IF;
    live_ := v3_meta.purge_copy_table(code_,'live');
    IF (SELECT COALESCE(array_agg(DISTINCT conrelid::regclass::text ORDER BY conrelid::regclass::text),'{}')
        FROM pg_constraint WHERE confrelid=live_ AND contype='f' AND conrelid<>live_)
       IS DISTINCT FROM v3_meta.purge_copy_expected_children(code_) THEN
        RAISE EXCEPTION 'child foreign keys differ from the fixed child set';
    END IF;
    FOR k IN SELECT conrelid::regclass AS child,conname,convalidated FROM pg_constraint
             WHERE confrelid=live_ AND contype='f' AND conrelid<>live_ ORDER BY 1,2 LOOP
        IF NOT k.convalidated THEN
            EXECUTE format('ALTER TABLE %s VALIDATE CONSTRAINT %I',k.child,k.conname);
        END IF;
        validated_ := validated_ || jsonb_build_array(jsonb_build_object('child',k.child::text,'name',k.conname));
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE confrelid=live_ AND contype='f' AND NOT convalidated) THEN
        RAISE EXCEPTION 'child foreign key validation incomplete';
    END IF;
    INSERT INTO v3_meta.derived_purge_copy_event(copy_id,purge_id,event_kind,relation_code,live_oid,operational_payload)
        VALUES(q.copy_id,purge_,'FK_VALIDATED',code_,live_,jsonb_build_object('validated',validated_))
        RETURNING event_sequence INTO event_;
    RETURN event_;
END $$;

-- Phase 6: DROP the retired relation, never CASCADE. Requires VERIFIED then
-- SWAPPED in the ledger, FK_VALIDATED after that swap, every child FK of the
-- live relation valid, and no external dependents. The DROP also removes the
-- retired relation's own outgoing FK triggers, so it locks those parents too;
-- lock_timeout bounds that stall exactly as for the swap.
CREATE FUNCTION v3_meta.purge_copy_drop(purge_ uuid, code_ text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
SET lock_timeout='2s' SET statement_timeout='60s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request; s v3_meta.purge_copy_relation_state;
        retired_ regclass; live_ regclass; bytes_ bigint; dependents_ text[]; event_ bigint;
BEGIN
    r := v3_meta.purge_copy_context(purge_,false);
    q := v3_meta.purge_copy_request_for(r);
    PERFORM v3_meta.purge_copy_assert_order(q.copy_id,code_);
    s := v3_meta.purge_copy_assert_position(q.copy_id,code_);
    IF s.copy_position<>'swapped' OR s.failed OR s.phase<>'SWAPPED' OR s.verified IS NULL
       OR s.verified>s.swapped OR s.fk_validated IS NULL THEN
        RAISE EXCEPTION 'DROP requires VERIFIED, SWAPPED and FK_VALIDATED';
    END IF;
    retired_ := v3_meta.purge_copy_table(code_,'retired');
    live_ := v3_meta.purge_copy_table(code_,'live');
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE confrelid=live_ AND contype='f' AND NOT convalidated)
       OR (SELECT COALESCE(array_agg(DISTINCT conrelid::regclass::text ORDER BY conrelid::regclass::text),'{}')
           FROM pg_constraint WHERE confrelid=live_ AND contype='f' AND conrelid<>live_)
          IS DISTINCT FROM v3_meta.purge_copy_expected_children(code_) THEN
        RAISE EXCEPTION 're-pointed foreign keys not all validated';
    END IF;
    EXECUTE format('LOCK TABLE %s IN ACCESS EXCLUSIVE MODE',retired_);
    dependents_ := v3_meta.purge_copy_external_dependents(retired_);
    IF cardinality(dependents_)<>0 THEN
        RAISE EXCEPTION 'retired relation has dependents: %', array_to_string(dependents_,',');
    END IF;
    bytes_ := pg_total_relation_size(retired_);
    EXECUTE format('DROP TABLE %s RESTRICT',retired_);
    INSERT INTO v3_meta.derived_purge_copy_event(copy_id,purge_id,event_kind,relation_code,live_oid,staging_oid,
        rows_total,relation_bytes,operational_payload)
        VALUES(q.copy_id,purge_,'DROPPED',code_,s.retired_oid,s.staging_oid,s.rows_total,bytes_,
            jsonb_build_object('freed_bytes',bytes_))
        RETURNING event_sequence INTO event_;
    RETURN event_;
END $$;

-- Before a swap (or after a swap-back): drop the staging copy. Live data is
-- untouched. Records FAILED_STOP with abandoned=true so the relation can restart.
CREATE FUNCTION v3_meta.purge_copy_abandon(purge_ uuid, code_ text, evidence_ jsonb) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
SET lock_timeout='2s' SET statement_timeout='60s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request; s v3_meta.purge_copy_relation_state;
        staging_ regclass; event_ bigint;
BEGIN
    IF evidence_ IS NULL OR jsonb_typeof(evidence_)<>'object' THEN RAISE EXCEPTION 'abandon evidence required'; END IF;
    r := v3_meta.purge_copy_context(purge_,true);
    q := v3_meta.purge_copy_request_for(r);
    s := v3_meta.purge_copy_assert_position(q.copy_id,code_);
    IF s.copy_position<>'staged' THEN RAISE EXCEPTION 'only an unswapped staging copy can be abandoned'; END IF;
    staging_ := v3_meta.purge_copy_table(code_,'staging');
    IF cardinality(v3_meta.purge_copy_external_dependents(staging_))<>0 THEN
        RAISE EXCEPTION 'staging relation has dependents';
    END IF;
    EXECUTE format('DROP TABLE %s RESTRICT',staging_);
    INSERT INTO v3_meta.derived_purge_copy_event(copy_id,purge_id,event_kind,relation_code,staging_oid,
        operational_payload,failure_evidence)
        VALUES(q.copy_id,purge_,'FAILED_STOP',code_,staging_,
            evidence_ || jsonb_build_object('abandoned',true),evidence_)
        RETURNING event_sequence INTO event_;
    RETURN event_;
END $$;
-- A non-abandon stop: forward steps then refuse; swap-back and abandon remain.
CREATE FUNCTION v3_meta.purge_copy_record_failure(purge_ uuid, code_ text, evidence_ jsonb) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
SET lock_timeout='5s' SET statement_timeout='60s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request; s v3_meta.purge_copy_relation_state; event_ bigint;
BEGIN
    IF evidence_ IS NULL OR jsonb_typeof(evidence_)<>'object' OR evidence_ ? 'abandoned' THEN
        RAISE EXCEPTION 'failure evidence required';
    END IF;
    r := v3_meta.purge_copy_context(purge_,true);
    q := v3_meta.purge_copy_request_for(r);
    s := v3_meta.purge_copy_assert_position(q.copy_id,code_);
    IF s.copy_position NOT IN ('staged','swapped') THEN RAISE EXCEPTION 'no copy in progress for %', code_; END IF;
    INSERT INTO v3_meta.derived_purge_copy_event(copy_id,purge_id,event_kind,relation_code,operational_payload,failure_evidence)
        VALUES(q.copy_id,purge_,'FAILED_STOP',code_,evidence_,evidence_)
        RETURNING event_sequence INTO event_;
    RETURN event_;
END $$;

-- All four relations DROPPED, nothing left staged or retired, no target rows.
CREATE FUNCTION v3_meta.purge_copy_complete(purge_ uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
SET lock_timeout='5s' SET statement_timeout='60s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; q v3_meta.derived_purge_copy_request; s v3_meta.purge_copy_relation_state;
        code_ text; target_ uuid; event_ bigint;
BEGIN
    r := v3_meta.purge_copy_context(purge_,false);
    q := v3_meta.purge_copy_request_for(r);
    SELECT event_sequence INTO event_ FROM v3_meta.derived_purge_copy_event
        WHERE copy_id=q.copy_id AND event_kind='COMPLETED';
    IF event_ IS NOT NULL THEN RETURN event_; END IF;
    FOREACH code_ IN ARRAY ARRAY['system_search','economy_opportunity','body_mechanics','system_rating_vector'] LOOP
        s := v3_meta.purge_copy_assert_position(q.copy_id,code_);
        IF s.copy_position<>'dropped' THEN RAISE EXCEPTION 'relation % not dropped', code_; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relnamespace IN ('v3_purge_staging'::regnamespace,'v3_retired'::regnamespace)) THEN
        RAISE EXCEPTION 'staging or retired relations remain';
    END IF;
    FOREACH target_ IN ARRAY q.target_generation_ids LOOP
        IF EXISTS (SELECT 1 FROM jsonb_each_text(v3_meta.purge_counts(target_)) WHERE value::bigint<>0) THEN
            RAISE EXCEPTION 'purge target rows remain';
        END IF;
    END LOOP;
    INSERT INTO v3_meta.derived_purge_copy_event(copy_id,purge_id,event_kind,operational_payload)
        VALUES(q.copy_id,purge_,'COMPLETED',jsonb_build_object('survivors',v3_meta.purge_counts(q.survivor_generation_id)))
        RETURNING event_sequence INTO event_;
    RETURN event_;
END $$;

-- Backups skip while any relation is between COPY_STARTED and DROPPED (or its
-- abandonment), and whenever a staging or retired table physically exists.
-- Read-only; for the backup wrapper and the shared mutator fence query.
CREATE FUNCTION v3_meta.purge_copy_backup_skip() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
SET lock_timeout='5s' SET statement_timeout='20s' AS $$
SELECT EXISTS (SELECT 1 FROM pg_class WHERE relnamespace IN ('v3_purge_staging'::regnamespace,'v3_retired'::regnamespace))
    OR EXISTS (SELECT 1 FROM v3_meta.derived_purge_copy_request q
        CROSS JOIN unnest(ARRAY['system_search','economy_opportunity','body_mechanics','system_rating_vector']) c
        CROSS JOIN LATERAL v3_meta.purge_copy_state(q.copy_id,c) s
        WHERE s.copy_position IN ('staged','swapped'))
$$;
-- Read-only status for the operator and the shared fence query.
CREATE FUNCTION v3_meta.purge_copy_status() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
SET lock_timeout='5s' SET statement_timeout='20s' AS $$
SELECT jsonb_build_object(
 'requests',COALESCE((SELECT jsonb_agg(jsonb_build_object('copy_id',q.copy_id,'plan_sha256',q.plan_sha256,
     'completed',EXISTS (SELECT 1 FROM v3_meta.derived_purge_copy_event e WHERE e.copy_id=q.copy_id AND e.event_kind='COMPLETED'),
     'relations',(SELECT jsonb_object_agg(c,to_jsonb(s)) FROM unnest(ARRAY['system_search','economy_opportunity',
         'body_mechanics','system_rating_vector']) c CROSS JOIN LATERAL v3_meta.purge_copy_state(q.copy_id,c) s))
     ORDER BY q.created_at) FROM v3_meta.derived_purge_copy_request q),'[]'::jsonb),
 'staging_relations',(SELECT COALESCE(jsonb_agg(relname ORDER BY relname),'[]'::jsonb) FROM pg_class
     WHERE relnamespace='v3_purge_staging'::regnamespace AND relkind='r'),
 'retired_relations',(SELECT COALESCE(jsonb_agg(relname ORDER BY relname),'[]'::jsonb) FROM pg_class
     WHERE relnamespace='v3_retired'::regnamespace AND relkind='r'),
 'backup_skip',v3_meta.purge_copy_backup_skip())
$$;

GRANT USAGE ON SCHEMA v3_purge_staging,v3_retired TO v3_derived_purge_executor;
GRANT SELECT ON v3_meta.derived_purge_copy_request,v3_meta.derived_purge_copy_event TO v3_derived_purge_executor;
-- Revoke defaults on every new function. Timeouts are pinned on each public
-- entry point's own definition; internal helpers inherit them, so a helper
-- can never lengthen the swap's 2s lock_timeout. Bulk phases (index build,
-- verification, FK validation) inherit the operator's per-dispatch
-- statement_timeout.
DO $$
DECLARE f record;
BEGIN
    FOR f IN SELECT oid::regprocedure AS signature FROM pg_proc
        WHERE pronamespace='v3_meta'::regnamespace AND proname IN (
        'purge_copy_relname','purge_copy_table','purge_copy_order_columns','purge_copy_key_params',
        'purge_copy_expected_children','purge_copy_expected_views','purge_copy_fingerprint',
        'purge_copy_fingerprint_mismatch','purge_copy_external_dependents','purge_copy_guard',
        'purge_copy_assert_016_clear','purge_copy_state','purge_copy_context','purge_copy_request_for',
        'purge_copy_assert_order','purge_copy_assert_position','purge_copy_generations',
        'purge_copy_assert_generations','purge_copy_apply_reloptions','purge_copy_assert_staging_shape',
        'purge_copy_output_settings','purge_copy_prepare','purge_copy_chunk','purge_copy_build_indexes','purge_copy_verify',
        'purge_copy_relocate','purge_copy_swap','purge_copy_swap_back','purge_copy_validate_fks',
        'purge_copy_drop','purge_copy_abandon','purge_copy_record_failure','purge_copy_complete',
        'purge_copy_status','purge_copy_backup_skip') LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
    END LOOP;
END $$;
GRANT EXECUTE ON FUNCTION v3_meta.purge_copy_prepare(uuid,text),
    v3_meta.purge_copy_chunk(uuid,text,integer),
    v3_meta.purge_copy_build_indexes(uuid,text),
    v3_meta.purge_copy_verify(uuid,text),
    v3_meta.purge_copy_swap(uuid,text),
    v3_meta.purge_copy_swap_back(uuid,text),
    v3_meta.purge_copy_validate_fks(uuid,text),
    v3_meta.purge_copy_drop(uuid,text),
    v3_meta.purge_copy_abandon(uuid,text,jsonb),
    v3_meta.purge_copy_record_failure(uuid,text,jsonb),
    v3_meta.purge_copy_complete(uuid),
    v3_meta.purge_copy_status(),
    v3_meta.purge_copy_backup_skip(),
    v3_meta.purge_copy_fingerprint(regclass)
TO v3_derived_purge_executor;
COMMIT;
