-- Capability only. No purge, payload DELETE or VACUUM executes during migration.
-- Owner-approved targets and Approach B: v3-governed-derived-purge-design.md.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- Roles are cluster-wide; another database may already have installed 016.
-- Adopt only the same restricted capability, never repair an unsafe role.
DO $$
DECLARE
    executor pg_roles%ROWTYPE;
    executor_exists boolean := true;
    has_password boolean;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='v3_derived_purge_executor') THEN
        executor_exists := false;
        -- PostgreSQL 18 gives a non-superuser creator SET, without INHERIT,
        -- only at creation; it cannot grant itself that membership afterward.
        SET LOCAL createrole_self_grant = 'set';
        CREATE ROLE v3_derived_purge_executor NOLOGIN NOINHERIT;
    END IF;
    SELECT * INTO STRICT executor FROM pg_roles WHERE rolname='v3_derived_purge_executor';
    IF executor.rolcanlogin OR executor.rolsuper OR executor.rolcreaterole
       OR executor.rolcreatedb OR executor.rolreplication OR executor.rolbypassrls
       OR executor.rolinherit OR executor.rolconnlimit NOT IN (-1,0) THEN
        RAISE EXCEPTION 'unsafe v3_derived_purge_executor attributes: requires NOLOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS NOINHERIT and CONNECTION LIMIT -1 or 0';
    END IF;
    -- pg_roles masks passwords. Non-superuser creators may not read pg_authid;
    -- skip only that check, explicitly, rather than requiring superuser access.
    BEGIN
        SELECT rolpassword IS NOT NULL INTO STRICT has_password
        FROM pg_authid WHERE oid=executor.oid;
        IF has_password THEN
            RAISE EXCEPTION 'unsafe v3_derived_purge_executor password: requires NULL';
        END IF;
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'v3_derived_purge_executor password check skipped: pg_authid is not readable';
    END;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member=executor.oid) THEN
        RAISE EXCEPTION 'unsafe v3_derived_purge_executor membership: must not be a member of any role';
    END IF;
    IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) AND NOT EXISTS (
        SELECT 1 FROM pg_auth_members m
        JOIN pg_roles r ON r.oid=m.roleid JOIN pg_roles c ON c.oid=m.member
        WHERE r.rolname='v3_derived_purge_executor' AND c.rolname=current_user
          AND m.set_option AND NOT m.inherit_option
    ) THEN RAISE EXCEPTION 'purge creator needs SET without INHERIT'; END IF;
    -- Adoption validates every incoming grant, including separate grants by
    -- different grantors. A superuser creator needs no membership at all.
    -- Fresh non-superuser creation has PostgreSQL's implicit bootstrap ADMIN
    -- grant; do not mistake that unavoidable creation behavior for adoption.
    IF executor_exists AND EXISTS (
        SELECT 1 FROM pg_auth_members
        WHERE roleid=executor.oid AND (
            member<>current_user::regrole OR NOT set_option OR inherit_option OR admin_option)
    ) THEN
        RAISE EXCEPTION 'unsafe v3_derived_purge_executor incoming membership: only the purge creator may be a member, with SET TRUE, INHERIT FALSE and ADMIN FALSE';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE oid IN (
        'v3_derived.system_search'::regclass,'v3_derived.economy_opportunity'::regclass,
        'v3_derived.body_mechanics'::regclass,'v3_derived.system_rating_vector'::regclass)
        AND relowner<>current_user::regrole) THEN
        RAISE EXCEPTION 'purge functions must be installed by the payload table owner';
    END IF;
    -- APPLICATION_ROLE is pinned by v3_schema_identity.DATABASE_IDENTITY and
    -- the target authority's API database identity, also used by F2d actions.
    -- A fresh disposable schema (Review Lab) need not have the production
    -- application login. The constant resolves at fence invocation and fails
    -- closed if absent; the migration never creates an application login.
    IF current_user <> 'edfinder_v3'
       AND NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
       AND NOT ((SELECT rolcreaterole FROM pg_roles WHERE rolname=current_user)
         AND EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid
                     WHERE r.rolname='edfinder_v3' AND m.member=current_user::regrole
                       AND m.admin_option)) THEN
        RAISE EXCEPTION 'purge owner cannot alter APPLICATION_ROLE defaults';
    END IF;
END $$;

CREATE TABLE v3_meta.derived_generation_purge_request (
    purge_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    derived_generation_id uuid NOT NULL UNIQUE REFERENCES v3_meta.derived_generation,
    generation_key text NOT NULL,
    manifest_sha256 bytea NOT NULL CHECK(octet_length(manifest_sha256)=32),
    original_state text NOT NULL,
    expected_canonical_id uuid NOT NULL,
    expected_canonical_sequence bigint NOT NULL CHECK(expected_canonical_sequence>0),
    expected_derived_id uuid NOT NULL,
    expected_derived_sequence bigint NOT NULL CHECK(expected_derived_sequence>0),
    expected_spatial_id uuid NOT NULL,
    expected_spatial_sequence bigint NOT NULL CHECK(expected_spatial_sequence>0),
    plan_sha256 text NOT NULL CHECK(plan_sha256 ~ '^[0-9a-f]{64}$'),
    source_git_sha text NOT NULL CHECK(source_git_sha ~ '^[0-9a-f]{40}$'),
    actor text NOT NULL CHECK(length(btrim(actor)) BETWEEN 1 AND 256),
    reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2048),
    frozen_snapshot jsonb NOT NULL CHECK(jsonb_typeof(frozen_snapshot)='object'),
    reviewed_plan jsonb NOT NULL CHECK(jsonb_typeof(reviewed_plan)='object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(plan_sha256,generation_key),
    CHECK ((generation_key='ratings_v4_prod_p4'
             AND derived_generation_id='49a0c00d-e722-4209-afd8-9d62b877e30a'
             AND original_state='BUILDING') OR
           (generation_key='ratings_v4_prod_p4_opt1'
             AND derived_generation_id='403b2d43-820c-414a-ad57-0d4de0ca3e82'
             AND original_state='VALIDATING'))
);
CREATE TABLE v3_meta.derived_generation_purge_event (
    event_sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    purge_id uuid NOT NULL REFERENCES v3_meta.derived_generation_purge_request,
    event_kind text NOT NULL CHECK(event_kind IN (
        'AUTHORIZED','BATCH_STARTED','BATCH_COMMITTED','BATCH_ROLLED_BACK',
        'RELATION_LOGICALLY_EMPTY','REWRITE_STARTED','REWRITE_VERIFIED','REWRITE_ROLLED_BACK',
        'COMPLETED','FAILED_STOP','SERVICES_QUIESCED','SERVICES_RESTORED',
        'AUTOVACUUM_DISABLED','AUTOVACUUM_RESTORED','WRITER_FENCE_ENABLED',
        'WRITER_FENCE_RESTORED','REWRITE_CANCEL_REQUESTED','BATCH_CANCEL_REQUESTED')),
    relation_code text CHECK(relation_code IN (
        'system_search','economy_opportunity','body_mechanics','system_rating_vector')),
    relation_oid oid,
    relfilenode_before oid,
    relfilenode_after oid,
    backend_pid integer,
    backend_start timestamptz,
    recovered boolean NOT NULL DEFAULT false,
    rows_affected bigint CHECK(rows_affected>=0),
    rows_remaining bigint CHECK(rows_remaining>=0),
    target_counts jsonb,
    survivor_counts jsonb,
    relation_bytes bigint CHECK(relation_bytes>=0),
    filesystem_free_bytes bigint CHECK(filesystem_free_bytes>=0),
    wal_lsn pg_lsn,
    wal_bytes bigint CHECK(wal_bytes>=0),
    receipt_sha256 text CHECK(receipt_sha256 ~ '^[0-9a-f]{64}$'),
    operational_payload jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(operational_payload)='object'),
    failure_evidence jsonb,
    transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id(),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX derived_purge_event_progress ON v3_meta.derived_generation_purge_event
    (purge_id,relation_code,event_sequence DESC);
CREATE FUNCTION v3_meta.reject_purge_ledger_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'purge ledger is append-only'; END $$;
CREATE TRIGGER retained_purge_request BEFORE UPDATE OR DELETE OR TRUNCATE
ON v3_meta.derived_generation_purge_request FOR EACH STATEMENT
EXECUTE FUNCTION v3_meta.reject_purge_ledger_mutation();
CREATE TRIGGER retained_purge_event BEFORE UPDATE OR DELETE OR TRUNCATE
ON v3_meta.derived_generation_purge_event FOR EACH STATEMENT
EXECUTE FUNCTION v3_meta.reject_purge_ledger_mutation();

-- No caller text becomes an identifier: this is the entire relation enum.
CREATE FUNCTION v3_meta.purge_relation(code_ text) RETURNS regclass
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
    CASE code_
      WHEN 'system_search' THEN RETURN 'v3_derived.system_search'::regclass;
      WHEN 'economy_opportunity' THEN RETURN 'v3_derived.economy_opportunity'::regclass;
      WHEN 'body_mechanics' THEN RETURN 'v3_derived.body_mechanics'::regclass;
      WHEN 'system_rating_vector' THEN RETURN 'v3_derived.system_rating_vector'::regclass;
      ELSE RAISE EXCEPTION 'nonallowlisted purge relation';
    END CASE;
END $$;

-- Exact counts at phase boundaries, not a scan of the published galaxy per batch.
CREATE FUNCTION v3_meta.purge_counts(target_ uuid) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
SELECT jsonb_build_object(
 'system_search',(SELECT count(*) FROM v3_derived.system_search WHERE derived_generation_id=target_),
 'economy_opportunity',(SELECT count(*) FROM v3_derived.economy_opportunity WHERE derived_generation_id=target_),
 'body_mechanics',(SELECT count(*) FROM v3_derived.body_mechanics WHERE derived_generation_id=target_),
 'system_rating_vector',(SELECT count(*) FROM v3_derived.system_rating_vector WHERE derived_generation_id=target_))
$$;
CREATE FUNCTION v3_meta.purge_retained_counts() RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
SELECT jsonb_build_object(
 'generations',(SELECT count(*) FROM v3_meta.derived_generation),
 'products',(SELECT count(*) FROM v3_meta.derived_product),
 'publication_audit',(SELECT count(*) FROM v3_meta.derived_publication_audit),
 'product_publication_audit',(SELECT count(*) FROM v3_meta.derived_product_publication_audit),
 'build_chunk',(SELECT count(*) FROM v3_derived.build_chunk),
 'search_build_chunk',(SELECT count(*) FROM v3_derived.search_build_chunk),
 'archetype_build_chunk',(SELECT count(*) FROM v3_derived.archetype_build_chunk),
 'search_rebuild_plan',(SELECT count(*) FROM v3_meta.search_rebuild_plan),
 'search_rebuild_range',(SELECT count(*) FROM v3_meta.search_rebuild_range))
$$;
CREATE FUNCTION v3_meta.purge_snapshot() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
SELECT jsonb_build_object(
 'canonical',(SELECT to_jsonb(c) FROM v3_meta.current_canonical_generation c WHERE singleton),
 'derived',(SELECT to_jsonb(c) FROM v3_meta.current_derived_generation c WHERE singleton),
 'spatial',(SELECT to_jsonb(c) FROM v3_spatial.current_spatial_generation c WHERE singleton),
 'retained',v3_meta.purge_retained_counts(),
 'survivors',v3_meta.purge_counts((SELECT derived_generation_id FROM v3_meta.current_derived_generation WHERE singleton)),
 'schema_ledger_sha256',(SELECT encode(sha256(convert_to(COALESCE(jsonb_agg(
     jsonb_build_array(migration_name,encode(migration_sha256,'hex')) ORDER BY migration_name)::text,'[]'),'UTF8')),'hex')
     FROM v3_meta.schema_migration))
$$;

CREATE FUNCTION v3_meta.purge_assert_empty_exclusions() RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM v3_spatial.cluster_member WHERE derived_generation_id IN
        ('49a0c00d-e722-4209-afd8-9d62b877e30a','403b2d43-820c-414a-ad57-0d4de0ca3e82'))
    OR EXISTS (SELECT 1 FROM v3_spatial.cluster WHERE derived_generation_id IN
        ('49a0c00d-e722-4209-afd8-9d62b877e30a','403b2d43-820c-414a-ad57-0d4de0ca3e82'))
    OR EXISTS (SELECT 1 FROM v3_spatial.cluster_run WHERE derived_generation_id IN
        ('49a0c00d-e722-4209-afd8-9d62b877e30a','403b2d43-820c-414a-ad57-0d4de0ca3e82'))
    OR EXISTS (SELECT 1 FROM v3_derived.system_archetype WHERE derived_generation_id IN
        ('49a0c00d-e722-4209-afd8-9d62b877e30a','403b2d43-820c-414a-ad57-0d4de0ca3e82')) THEN
        RAISE EXCEPTION 'unexpected cluster or archetype payload; separate authority required';
    END IF;
END $$;

CREATE FUNCTION v3_meta.purge_assert_request(purge_ uuid) RETURNS v3_meta.derived_generation_purge_request
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
    IF (SELECT encode(sha256(convert_to(COALESCE(jsonb_agg(jsonb_build_array(migration_name,
          encode(migration_sha256,'hex')) ORDER BY migration_name)::text,'[]'),'UTF8')),'hex')
          FROM v3_meta.schema_migration) IS DISTINCT FROM r.frozen_snapshot->>'schema_ledger_sha256' THEN
        RAISE EXCEPTION 'purge schema ledger drift';
    END IF;
    PERFORM v3_meta.purge_assert_empty_exclusions();
    RETURN r;
END $$;

CREATE FUNCTION v3_meta.begin_derived_generation_purge(plan_ jsonb) RETURNS uuid[]
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t jsonb; r uuid; result_ uuid[] := '{}'; d v3_meta.derived_generation; snapshot_ jsonb;
BEGIN
    IF NOT pg_try_advisory_xact_lock(764003001) OR NOT pg_try_advisory_xact_lock(764004001) THEN
        RAISE EXCEPTION 'competing derived or spatial operation';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_stat_activity
        WHERE datname=current_database() AND pid<>pg_backend_pid()
               AND (backend_xid IS NOT NULL OR (backend_type='client backend' AND state IS NULL))) THEN
        RAISE EXCEPTION 'competing writing transaction';
    END IF;
    PERFORM 1 FROM v3_meta.current_canonical_generation WHERE singleton FOR SHARE;
    PERFORM 1 FROM v3_meta.current_derived_generation WHERE singleton FOR SHARE;
    PERFORM 1 FROM v3_spatial.current_spatial_generation WHERE singleton FOR SHARE;
    PERFORM 1 FROM v3_meta.derived_generation WHERE generation_key IN
        ('ratings_v4_prod_p4','ratings_v4_prod_p4_opt1') ORDER BY derived_generation_id FOR UPDATE;
    snapshot_ := v3_meta.purge_snapshot();
    IF plan_->>'plan_sha256' IS DISTINCT FROM encode(sha256(convert_to((plan_-'plan_sha256')::text,'UTF8')),'hex')
       OR plan_->'workers_stopped' IS DISTINCT FROM 'true'::jsonb
       OR plan_->'snapshot' IS DISTINCT FROM snapshot_
       OR plan_->>'plan_sha256' IS NULL OR plan_->>'source_git_sha' IS NULL
       OR snapshot_->'canonical' IS NULL OR snapshot_->'canonical'='null'::jsonb
       OR snapshot_->'derived' IS NULL OR snapshot_->'derived'='null'::jsonb
       OR snapshot_->'spatial' IS NULL OR snapshot_->'spatial'='null'::jsonb
       OR jsonb_array_length(plan_->'targets') IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION 'purge plan, worker attestation or pointer mismatch';
    END IF;
    IF EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_request
               WHERE plan_sha256=plan_->>'plan_sha256') THEN
        RAISE EXCEPTION 'reviewed purge plan already used';
    END IF;
    PERFORM v3_meta.purge_assert_empty_exclusions();
    FOR t IN SELECT value FROM jsonb_array_elements(plan_->'targets') LOOP
        SELECT * INTO d FROM v3_meta.derived_generation
            WHERE derived_generation_id=(t->>'id')::uuid FOR UPDATE;
        IF NOT FOUND OR d.generation_key IS DISTINCT FROM t->>'key'
           OR encode(d.manifest_sha256,'hex') IS DISTINCT FROM t->>'manifest_sha256'
           OR d.canonical_generation_id IS DISTINCT FROM (snapshot_->'canonical'->>'generation_id')::uuid
           OR d.canonical_publication_sequence IS DISTINCT FROM (snapshot_->'canonical'->>'publication_sequence')::bigint
           OR d.lifecycle_state IS DISTINCT FROM t->>'state'
           OR t->'payload_counts' IS DISTINCT FROM v3_meta.purge_counts(d.derived_generation_id)
           OR d.derived_generation_id=(snapshot_->'derived'->>'derived_generation_id')::uuid
           OR EXISTS (SELECT 1 FROM v3_meta.derived_publication_audit
               WHERE from_generation_id=d.derived_generation_id OR to_generation_id=d.derived_generation_id)
           OR EXISTS (SELECT 1 FROM v3_meta.derived_product_publication_audit
               WHERE derived_generation_id=d.derived_generation_id) THEN
            RAISE EXCEPTION 'purge target identity, state or nonpublication mismatch';
        END IF;
        INSERT INTO v3_meta.derived_generation_purge_request (
            derived_generation_id,generation_key,manifest_sha256,original_state,
            expected_canonical_id,expected_canonical_sequence,expected_derived_id,expected_derived_sequence,
            expected_spatial_id,expected_spatial_sequence,plan_sha256,source_git_sha,actor,reason,
            frozen_snapshot,reviewed_plan)
        VALUES (d.derived_generation_id,d.generation_key,d.manifest_sha256,d.lifecycle_state,
            (snapshot_->'canonical'->>'generation_id')::uuid,(snapshot_->'canonical'->>'publication_sequence')::bigint,
            (snapshot_->'derived'->>'derived_generation_id')::uuid,(snapshot_->'derived'->>'publication_sequence')::bigint,
            (snapshot_->'spatial'->>'spatial_generation_id')::uuid,(snapshot_->'spatial'->>'publication_sequence')::bigint,
            plan_->>'plan_sha256',plan_->>'source_git_sha',plan_->>'actor',plan_->>'reason',snapshot_,plan_)
        RETURNING purge_id INTO r;
        UPDATE v3_meta.derived_generation SET lifecycle_state='FAILED',failed_at=clock_timestamp(),
            failure='Owner-authorized governed purge superseded the build; plan ' || (plan_->>'plan_sha256')
            WHERE derived_generation_id=d.derived_generation_id;
        INSERT INTO v3_meta.derived_generation_purge_event(purge_id,event_kind)
            VALUES(r,'AUTHORIZED');
        result_ := array_append(result_,r);
    END LOOP;
    RETURN result_;
END $$;

-- Context checks supplement the request. The executor cannot become the definer,
-- write a ledger row or invoke internal helpers, even if it spoofs a custom GUC.
CREATE FUNCTION v3_meta.guard_purge_delete() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r v3_meta.derived_generation_purge_request; context_ text; id_ uuid;
BEGIN
    GET DIAGNOSTICS context_ = PG_CONTEXT;
    IF TG_TABLE_SCHEMA <> 'v3_derived' OR TG_TABLE_NAME NOT IN
       ('system_search','economy_opportunity','body_mechanics','system_rating_vector')
       OR current_user::regrole <> (SELECT proowner FROM pg_proc
           WHERE oid='v3_meta.purge_derived_payload_batch(uuid,text,integer)'::regprocedure)
       OR position('function v3_meta.purge_derived_payload_batch(' IN context_)=0
       OR NULLIF(current_setting('v3.purge_id',true),'') IS NULL THEN
        RAISE EXCEPTION 'payload DELETE requires governed purge function context';
    END IF;
    r := v3_meta.purge_assert_request(current_setting('v3.purge_id')::uuid);
    FOR id_ IN SELECT DISTINCT derived_generation_id FROM changed_rows LOOP
        IF id_<>r.derived_generation_id THEN RAISE EXCEPTION 'mixed-generation purge DELETE'; END IF;
    END LOOP;
    RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION v3_derived.guard_generation_write() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE identifier uuid; state_ text;
BEGIN
    IF TG_OP='TRUNCATE' THEN RAISE EXCEPTION 'derived relations cannot be truncated'; END IF;
    IF TG_OP='DELETE' THEN
        -- Invoke the same context/request checks using this trigger's transition table.
        -- Trigger functions cannot be called directly; this branch repeats them below.
        IF TG_TABLE_SCHEMA<>'v3_derived' OR TG_TABLE_NAME NOT IN
            ('economy_opportunity','body_mechanics','system_rating_vector') THEN
            RAISE EXCEPTION 'retained relation DELETE is forbidden';
        END IF;
        DECLARE r v3_meta.derived_generation_purge_request; context_ text;
        BEGIN
            GET DIAGNOSTICS context_ = PG_CONTEXT;
            IF current_user::regrole <> (SELECT proowner FROM pg_proc
                WHERE oid='v3_meta.purge_derived_payload_batch(uuid,text,integer)'::regprocedure)
                OR position('function v3_meta.purge_derived_payload_batch(' IN context_)=0
                OR NULLIF(current_setting('v3.purge_id',true),'') IS NULL THEN
                RAISE EXCEPTION 'payload DELETE requires governed purge function context';
            END IF;
            r := v3_meta.purge_assert_request(current_setting('v3.purge_id')::uuid);
            FOR identifier IN SELECT DISTINCT derived_generation_id FROM changed_rows LOOP
                IF identifier<>r.derived_generation_id THEN RAISE EXCEPTION 'mixed-generation purge DELETE'; END IF;
            END LOOP;
        END;
        RETURN NULL;
    END IF;
    FOR identifier IN EXECUTE 'SELECT DISTINCT derived_generation_id FROM changed_rows ORDER BY 1' LOOP
        SELECT lifecycle_state INTO state_ FROM v3_meta.derived_generation
            WHERE derived_generation_id=identifier FOR SHARE;
        IF state_ IS DISTINCT FROM 'BUILDING' THEN
            RAISE EXCEPTION 'derived generation % is immutable in state %',identifier,state_;
        END IF;
    END LOOP;
    RETURN NULL;
END $$;
DROP TRIGGER product_delete ON v3_derived.system_search;
CREATE TRIGGER product_delete AFTER DELETE ON v3_derived.system_search
REFERENCING OLD TABLE AS changed_rows FOR EACH STATEMENT
EXECUTE FUNCTION v3_meta.guard_purge_delete();

CREATE FUNCTION v3_meta.purge_open_event(purge_ uuid, code_ text, opening_ text, closing_ text[])
RETURNS v3_meta.derived_generation_purge_event
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
-- The latest opening event of this kind that no later closing event of the
-- same request and relation resolves. "No closing event after e" is exactly
-- "e is later than the last closing event", so one grouped pass replaces a
-- per-event anti-join whose cost grew with the square of the batch count.
WITH scope AS (
 SELECT p.purge_id FROM v3_meta.derived_generation_purge_request p
 WHERE p.plan_sha256=(SELECT plan_sha256 FROM v3_meta.derived_generation_purge_request WHERE purge_id=purge_)
 AND (opening_<>'BATCH_STARTED' OR p.purge_id=purge_)
), closed AS (
 SELECT c.purge_id, max(c.event_sequence) AS last_closing
 FROM v3_meta.derived_generation_purge_event c JOIN scope USING (purge_id)
 WHERE c.relation_code IS NOT DISTINCT FROM code_ AND c.event_kind=ANY(closing_)
 AND (opening_<>'WRITER_FENCE_ENABLED' OR c.operational_payload->'verified'='true'::jsonb)
 GROUP BY c.purge_id
)
SELECT e FROM v3_meta.derived_generation_purge_event e
JOIN scope USING (purge_id)
LEFT JOIN closed k USING (purge_id)
WHERE e.relation_code IS NOT DISTINCT FROM code_ AND e.event_kind=opening_
AND (k.last_closing IS NULL OR e.event_sequence>k.last_closing)
ORDER BY e.event_sequence DESC LIMIT 1
$$;
CREATE FUNCTION v3_meta.purge_assert_phase(purge_ uuid, code_ text) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r v3_meta.derived_generation_purge_request; prior_ text; relation_ regclass;
BEGIN
    r := v3_meta.purge_assert_request(purge_);
    relation_ := v3_meta.purge_relation(code_);
    IF EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event
        WHERE purge_id=purge_ AND event_kind='COMPLETED') THEN
        RAISE EXCEPTION 'purge already complete';
    END IF;
    -- Every earlier relation must have a verified physical rewrite, not merely
    -- zero rows. Both requests share the physical relation and reviewed plan.
    FOREACH prior_ IN ARRAY ARRAY['system_search','economy_opportunity','body_mechanics','system_rating_vector'] LOOP
        EXIT WHEN prior_=code_;
        IF NOT EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event e
            JOIN v3_meta.derived_generation_purge_request p USING(purge_id)
            WHERE p.plan_sha256=r.plan_sha256 AND e.relation_code=prior_ AND e.event_kind='REWRITE_VERIFIED') THEN
            RAISE EXCEPTION 'wrong purge dependency phase';
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_stat_progress_vacuum
               WHERE relid IN (relation_::oid,(SELECT reltoastrelid FROM pg_class WHERE oid=relation_))) THEN
        RAISE EXCEPTION 'vacuum active on purge relation';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_stat_activity WHERE datid=(SELECT oid FROM pg_database WHERE datname=current_database())
               AND pid<>pg_backend_pid()
               AND (backend_xid IS NOT NULL OR (backend_type='client backend' AND state IS NULL))) THEN
        RAISE EXCEPTION 'competing writing transaction';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_replication_slots WHERE NOT active OR wal_status IN ('extended','unreserved','lost'))
       OR EXISTS (SELECT 1 FROM pg_stat_archiver WHERE last_failed_time>COALESCE(last_archived_time,'-infinity')) THEN
        RAISE EXCEPTION 'unhealthy WAL retention or archiving';
    END IF;
END $$;

-- Restoration intentionally authenticates the immutable request without requiring
-- unchanged pointers: an integrity stop may itself be pointer drift. An explicit
-- acknowledgement is mandatory, and no writer is restored beside an open job.
CREATE FUNCTION v3_meta.purge_recovery_request(purge_ uuid) RETURNS v3_meta.derived_generation_purge_request
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r v3_meta.derived_generation_purge_request;
BEGIN
    IF NOT pg_try_advisory_xact_lock(764003001) OR NOT pg_try_advisory_xact_lock(764004001) THEN
        RAISE EXCEPTION 'competing operation during recovery';
    END IF;
    SELECT * INTO r FROM v3_meta.derived_generation_purge_request WHERE purge_id=purge_;
    IF NOT FOUND THEN RAISE EXCEPTION 'unauthorized purge id'; END IF;
    RETURN r;
END $$;
CREATE FUNCTION v3_meta.purge_assert_recovery_safe(purge_ uuid, acknowledge_failed_stop_ bigint DEFAULT NULL) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r v3_meta.derived_generation_purge_request;
BEGIN
    r:=v3_meta.purge_recovery_request(purge_);
    IF acknowledge_failed_stop_ IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM v3_meta.derived_generation_purge_event e
        JOIN v3_meta.derived_generation_purge_request p USING(purge_id)
        WHERE p.plan_sha256=r.plan_sha256 AND e.event_sequence=acknowledge_failed_stop_
          AND e.event_kind='FAILED_STOP' AND e.failure_evidence->'integrity_failed'='true'::jsonb) THEN
        RAISE EXCEPTION 'acknowledgement must name the exact integrity failure';
    END IF;
    IF EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event e
        JOIN v3_meta.derived_generation_purge_request p USING(purge_id)
        WHERE p.plan_sha256=r.plan_sha256 AND e.event_kind IN ('BATCH_STARTED','REWRITE_STARTED')
        AND NOT EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event z
            WHERE z.purge_id=e.purge_id AND z.relation_code=e.relation_code AND z.event_sequence>e.event_sequence
            AND ((e.event_kind='BATCH_STARTED' AND z.event_kind IN ('BATCH_COMMITTED','BATCH_ROLLED_BACK'))
              OR (e.event_kind='REWRITE_STARTED' AND z.event_kind IN ('REWRITE_VERIFIED','REWRITE_ROLLED_BACK'))
              -- Owner acknowledgement can restore writers after an integrity
              -- stop on a committed rewrite, but never beside its backend or
              -- a maintenance operation. This grants no rewrite verification,
              -- autovacuum restoration, or further purge authority.
              OR (e.event_kind='REWRITE_STARTED' AND z.event_kind='FAILED_STOP'
                  AND z.event_sequence=acknowledge_failed_stop_
                  AND z.failure_evidence->'integrity_failed'='true'::jsonb
                  AND z.failure_evidence->'rewrite_committed'='true'::jsonb
                  AND z.failure_evidence->>'start_event'=e.event_sequence::text
                  AND NOT EXISTS (SELECT 1 FROM pg_stat_activity a
                      WHERE a.pid=e.backend_pid AND a.backend_start=e.backend_start)
                  AND NOT EXISTS (SELECT 1 FROM pg_stat_progress_cluster WHERE relid=e.relation_oid)
                  AND NOT EXISTS (SELECT 1 FROM pg_stat_progress_analyze WHERE relid=e.relation_oid))))) THEN
        RAISE EXCEPTION 'resolve open destructive operation before recovery';
    END IF;
    IF EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event e
        JOIN v3_meta.derived_generation_purge_request p USING(purge_id)
        WHERE p.plan_sha256=r.plan_sha256 AND e.event_kind='FAILED_STOP'
        AND e.failure_evidence->'integrity_failed'='true'::jsonb
        AND e.event_sequence IS DISTINCT FROM acknowledge_failed_stop_
        AND NOT EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event ack
            JOIN v3_meta.derived_generation_purge_request ap ON ap.purge_id=ack.purge_id
            WHERE ap.plan_sha256=r.plan_sha256 AND ack.event_kind IN ('FAILED_STOP','WRITER_FENCE_RESTORED')
            AND ack.event_sequence>e.event_sequence
            AND ack.operational_payload->>'acknowledge_failed_stop'=e.event_sequence::text)) THEN
        RAISE EXCEPTION 'integrity failure needs separately acknowledged recovery';
    END IF;
END $$;

CREATE FUNCTION v3_meta.purge_set_relation_autovacuum(purge_ uuid, code_ text, enabled_ boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r v3_meta.derived_generation_purge_request; relation_ regclass; toast_ oid;
        e v3_meta.derived_generation_purge_event; options_ jsonb; config_ jsonb;
        c record; xid_limit bigint; mxid_limit bigint; value_ text; key_ text;
BEGIN
    IF enabled_ IS NULL THEN RAISE EXCEPTION 'autovacuum enable must be boolean'; END IF;
    IF enabled_ THEN r:=v3_meta.purge_recovery_request(purge_);
    ELSE r:=v3_meta.purge_assert_request(purge_); END IF;
    relation_ := v3_meta.purge_relation(code_);
    SELECT reltoastrelid INTO toast_ FROM pg_class WHERE oid=relation_;
    SELECT * INTO e FROM v3_meta.purge_open_event(purge_,code_,'AUTOVACUUM_DISABLED',ARRAY['AUTOVACUUM_RESTORED']);
    IF NOT enabled_ AND EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event x
        JOIN v3_meta.derived_generation_purge_request p USING(purge_id)
        WHERE p.plan_sha256=r.plan_sha256 AND x.relation_code=code_ AND x.event_kind='REWRITE_VERIFIED') THEN
        RAISE EXCEPTION 'rewrite already verified for purge relation; no new autovacuum fence';
    END IF;
    IF enabled_ THEN
        IF e.event_sequence IS NULL THEN RETURN; END IF;
        PERFORM v3_meta.purge_assert_recovery_safe(purge_);
        IF NOT EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event x
            JOIN v3_meta.derived_generation_purge_request p USING(purge_id)
            WHERE p.plan_sha256=r.plan_sha256 AND x.relation_code=code_ AND x.event_kind='REWRITE_VERIFIED'
              AND x.event_sequence>e.event_sequence) THEN
            SELECT operational_payload INTO options_ FROM v3_meta.derived_generation_purge_event x
                JOIN v3_meta.derived_generation_purge_request p USING(purge_id)
                WHERE p.plan_sha256=r.plan_sha256 AND x.relation_code=code_ AND x.event_kind='FAILED_STOP'
                  AND x.operational_payload->'aborted'='true'::jsonb AND x.event_sequence>e.event_sequence
                ORDER BY event_sequence DESC LIMIT 1;
            PERFORM v3_meta.purge_check_measurements(options_);
            IF NOT COALESCE((options_->>'autovacuum_budget_bytes')::numeric>=pg_total_relation_size(relation_)
                AND (options_->>'filesystem_free_bytes')::numeric>=100000000000+(options_->>'autovacuum_budget_bytes')::numeric,false) THEN
                RAISE EXCEPTION 'autovacuum restoration requires verified rewrite or budgeted abort';
            END IF;
        END IF;
        options_ := e.operational_payload;
        FOREACH key_ IN ARRAY ARRAY['heap','toast'] LOOP
            IF key_='toast' AND toast_=0 THEN CONTINUE; END IF;
            value_ := options_->key_->>'prior';
            IF value_ IS NULL THEN
                EXECUTE format('ALTER TABLE %s RESET (%sautovacuum_enabled)',relation_,
                               CASE key_ WHEN 'toast' THEN 'toast.' ELSE '' END);
            ELSE
                EXECUTE format('ALTER TABLE %s SET (%sautovacuum_enabled = %s)',relation_,
                               CASE key_ WHEN 'toast' THEN 'toast.' ELSE '' END,
                               CASE value_ WHEN 'true' THEN 'true' WHEN 'false' THEN 'false' ELSE NULL END);
            END IF;
        END LOOP;
        INSERT INTO v3_meta.derived_generation_purge_event(purge_id,event_kind,relation_code,relation_oid,operational_payload)
            VALUES(e.purge_id,'AUTOVACUUM_RESTORED',code_,relation_,options_);
        RETURN;
    END IF;
    PERFORM v3_meta.purge_assert_phase(purge_,code_);
    IF e.event_sequence IS NOT NULL THEN RETURN; END IF;
    config_ := r.reviewed_plan->'autovacuum'->code_;
    options_ := '{}'::jsonb;
    FOR c IN SELECT oid,reloptions,age(relfrozenxid)::bigint AS xid_age,
                    mxid_age(relminmxid)::bigint AS mxid_age
             FROM pg_class WHERE oid IN (relation_::oid,toast_) LOOP
        key_ := CASE WHEN c.oid=relation_::oid THEN 'heap' ELSE 'toast' END;
        SELECT least(current_setting('autovacuum_freeze_max_age')::bigint,
               COALESCE((SELECT option_value::bigint FROM pg_options_to_table(c.reloptions)
                         WHERE option_name='autovacuum_freeze_max_age'),9223372036854775807)),
               least(current_setting('autovacuum_multixact_freeze_max_age')::bigint,
               COALESCE((SELECT option_value::bigint FROM pg_options_to_table(c.reloptions)
                         WHERE option_name='autovacuum_multixact_freeze_max_age'),9223372036854775807))
          INTO xid_limit,mxid_limit;
        IF config_ IS NULL OR NOT COALESCE(
            (config_->>'xid_ceiling')::bigint < xid_limit AND
            (config_->>'mxid_ceiling')::bigint < mxid_limit AND
            (config_->>'xid_rate')::numeric>=0 AND (config_->>'mxid_rate')::numeric>=0 AND
            (config_->>'remaining_seconds')::numeric>0 AND
            c.xid_age+(config_->>'xid_rate')::numeric*(config_->>'remaining_seconds')::numeric < (config_->>'xid_ceiling')::bigint AND
            c.mxid_age+(config_->>'mxid_rate')::numeric*(config_->>'remaining_seconds')::numeric < (config_->>'mxid_ceiling')::bigint,false) THEN
            RAISE EXCEPTION 'autovacuum age ceiling or completion deadline unsafe';
        END IF;
        SELECT option_value::boolean::text INTO value_ FROM pg_options_to_table(c.reloptions) WHERE option_name='autovacuum_enabled';
        options_ := options_ || jsonb_build_object(key_,jsonb_build_object('prior',value_,
            'xid_age',c.xid_age,'mxid_age',c.mxid_age,'xid_ceiling',config_->'xid_ceiling',
            'mxid_ceiling',config_->'mxid_ceiling','xid_rate',config_->'xid_rate','mxid_rate',config_->'mxid_rate'));
    END LOOP;
    EXECUTE format('ALTER TABLE %s SET (autovacuum_enabled = false)',relation_);
    IF toast_<>0 THEN EXECUTE format('ALTER TABLE %s SET (toast.autovacuum_enabled = false)',relation_); END IF;
    INSERT INTO v3_meta.derived_generation_purge_event(purge_id,event_kind,relation_code,relation_oid,operational_payload)
        VALUES(purge_,'AUTOVACUUM_DISABLED',code_,relation_,options_);
END $$;

-- APPLICATION_ROLE is a single constant, never a caller-supplied role name.
-- Both catalog scopes are preserved; database-specific defaults override global.
CREATE FUNCTION v3_meta.purge_set_writer_fence(
    purge_ uuid, enabled_ boolean, acknowledge_failed_stop_ bigint DEFAULT NULL) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE application_role CONSTANT text := 'edfinder_v3';
        e v3_meta.derived_generation_purge_event; prior_ jsonb; setting_ text; scope_ text;
BEGIN
    IF enabled_ IS NULL THEN RAISE EXCEPTION 'writer fence enable must be boolean'; END IF;
    IF enabled_ AND acknowledge_failed_stop_ IS NOT NULL THEN
        RAISE EXCEPTION 'acknowledgement is only valid for writer restoration';
    END IF;
    IF enabled_ THEN PERFORM v3_meta.purge_assert_request(purge_);
    ELSE PERFORM v3_meta.purge_recovery_request(purge_); END IF;
    SELECT * INTO e FROM v3_meta.purge_open_event(purge_,NULL,'WRITER_FENCE_ENABLED',ARRAY['WRITER_FENCE_RESTORED']);
    IF enabled_ THEN
        IF e.event_sequence IS NOT NULL THEN RETURN; END IF;
        SELECT jsonb_build_object(
            'global',(SELECT split_part(v,'=',2) FROM pg_db_role_setting s CROSS JOIN unnest(s.setconfig) v
                WHERE s.setrole=application_role::regrole AND s.setdatabase=0 AND v LIKE 'default_transaction_read_only=%'),
            'database',(SELECT split_part(v,'=',2) FROM pg_db_role_setting s CROSS JOIN unnest(s.setconfig) v
                WHERE s.setrole=application_role::regrole AND s.setdatabase=(SELECT oid FROM pg_database WHERE datname=current_database())
                  AND v LIKE 'default_transaction_read_only=%')) INTO prior_;
        IF COALESCE(prior_->>'database','off')<>'off' OR COALESCE(prior_->>'global','off')<>'off' THEN
            RAISE EXCEPTION 'application role is already read-only';
        END IF;
        EXECUTE format('ALTER ROLE %I SET default_transaction_read_only = on',application_role);
        EXECUTE format('ALTER ROLE %I IN DATABASE %I SET default_transaction_read_only = on',application_role,current_database());
        INSERT INTO v3_meta.derived_generation_purge_event(purge_id,event_kind,operational_payload)
            VALUES(purge_,'WRITER_FENCE_ENABLED',jsonb_build_object('role',application_role,'prior',prior_,'verified',false));
    ELSE
        PERFORM v3_meta.purge_assert_recovery_safe(purge_,acknowledge_failed_stop_);
        IF e.event_sequence IS NULL THEN RETURN; END IF;
        FOREACH scope_ IN ARRAY ARRAY['global','database'] LOOP
            setting_ := e.operational_payload->'prior'->>scope_;
            IF setting_ IS NOT NULL AND setting_<>'off' THEN RAISE EXCEPTION 'invalid ledgered writer prior value'; END IF;
            EXECUTE format('ALTER ROLE %I %s %s',application_role,
                CASE scope_ WHEN 'database' THEN format('IN DATABASE %I',current_database()) ELSE '' END,
                CASE WHEN setting_ IS NULL THEN 'RESET default_transaction_read_only'
                     ELSE 'SET default_transaction_read_only = off' END);
        END LOOP;
        INSERT INTO v3_meta.derived_generation_purge_event(purge_id,event_kind,operational_payload)
            VALUES(e.purge_id,'WRITER_FENCE_RESTORED',e.operational_payload || jsonb_build_object('verified',false)
                || CASE WHEN acknowledge_failed_stop_ IS NULL THEN '{}'::jsonb ELSE
                    jsonb_build_object('acknowledge_failed_stop',acknowledge_failed_stop_) END);
    END IF;
END $$;

-- Step two runs in a later transaction. Pools reconnect only after step one
-- commits. The later operator must also make a fresh application-role probe.
CREATE FUNCTION v3_meta.purge_recycle_writer_sessions(purge_ uuid, enabled_ boolean) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE e v3_meta.derived_generation_purge_event; n integer := 0; c record;
BEGIN
    IF enabled_ THEN PERFORM v3_meta.purge_assert_request(purge_);
    ELSE PERFORM v3_meta.purge_recovery_request(purge_); END IF;
    SELECT * INTO e FROM v3_meta.derived_generation_purge_event WHERE purge_id IN
        (SELECT p.purge_id FROM v3_meta.derived_generation_purge_request p WHERE p.plan_sha256=
          (SELECT plan_sha256 FROM v3_meta.derived_generation_purge_request WHERE purge_id=purge_))
        AND event_kind=CASE WHEN enabled_ THEN 'WRITER_FENCE_ENABLED' ELSE 'WRITER_FENCE_RESTORED' END
        ORDER BY event_sequence DESC LIMIT 1;
    IF e.event_sequence IS NULL OR e.transaction_id = pg_current_xact_id_if_assigned() THEN
        RAISE EXCEPTION 'writer fence step one must commit before recycling';
    END IF;
    FOR c IN SELECT pid FROM pg_stat_activity WHERE usename='edfinder_v3'
        AND datname=current_database() AND pid<>pg_backend_pid() LOOP
        IF pg_terminate_backend(c.pid) THEN n:=n+1; END IF;
    END LOOP;
    INSERT INTO v3_meta.derived_generation_purge_event(purge_id,event_kind,operational_payload)
        VALUES(e.purge_id,e.event_kind,e.operational_payload || jsonb_build_object('sessions_recycled',n,'recycled',true,'recycle_started_at',statement_timestamp()));
    RETURN n;
END $$;
CREATE FUNCTION v3_meta.purge_verify_writer_fence(purge_ uuid, enabled_ boolean, observed_ text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE e v3_meta.derived_generation_purge_event; expected_ text;
BEGIN
    IF enabled_ THEN PERFORM v3_meta.purge_assert_request(purge_);
    ELSE PERFORM v3_meta.purge_recovery_request(purge_); END IF;
    SELECT * INTO e FROM v3_meta.derived_generation_purge_event WHERE purge_id IN
        (SELECT p.purge_id FROM v3_meta.derived_generation_purge_request p WHERE p.plan_sha256=
          (SELECT plan_sha256 FROM v3_meta.derived_generation_purge_request WHERE purge_id=purge_))
        AND event_kind=CASE WHEN enabled_ THEN 'WRITER_FENCE_ENABLED' ELSE 'WRITER_FENCE_RESTORED' END
        ORDER BY event_sequence DESC LIMIT 1;
    expected_ := CASE WHEN enabled_ THEN 'on' ELSE 'off' END;
    IF e.event_sequence IS NULL OR e.operational_payload->'recycled' IS DISTINCT FROM 'true'::jsonb
        OR observed_ IS DISTINCT FROM expected_ THEN RAISE EXCEPTION 'writer fence fresh probe not verified'; END IF;
    INSERT INTO v3_meta.derived_generation_purge_event(purge_id,event_kind,operational_payload)
        VALUES(e.purge_id,e.event_kind,e.operational_payload || jsonb_build_object('verified',true,'observed',observed_));
END $$;

CREATE FUNCTION v3_meta.purge_assert_fences(purge_ uuid, code_ text, delete_ boolean DEFAULT true) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r v3_meta.derived_generation_purge_request; e v3_meta.derived_generation_purge_event; c record;
BEGIN
    r:=v3_meta.purge_assert_request(purge_);
    SELECT * INTO e FROM v3_meta.derived_generation_purge_event WHERE purge_id IN
        (SELECT p.purge_id FROM v3_meta.derived_generation_purge_request p WHERE p.plan_sha256=
          (SELECT plan_sha256 FROM v3_meta.derived_generation_purge_request WHERE purge_id=purge_))
        AND event_kind IN ('WRITER_FENCE_ENABLED','WRITER_FENCE_RESTORED') ORDER BY event_sequence DESC LIMIT 1;
    IF e.event_kind IS DISTINCT FROM 'WRITER_FENCE_ENABLED'
       OR e.operational_payload->'verified' IS DISTINCT FROM 'true'::jsonb
       OR e.transaction_id=pg_current_xact_id_if_assigned() THEN
        RAISE EXCEPTION 'committed verified writer fence required';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_db_role_setting s CROSS JOIN unnest(s.setconfig) v
        WHERE s.setrole='edfinder_v3'::regrole AND s.setdatabase=(SELECT oid FROM pg_database WHERE datname=current_database())
          AND v='default_transaction_read_only=on')
       OR EXISTS (SELECT 1 FROM pg_stat_activity WHERE usename='edfinder_v3' AND datname=current_database()
          AND pid<>pg_backend_pid() AND backend_start<=(e.operational_payload->>'recycle_started_at')::timestamptz) THEN
        RAISE EXCEPTION 'writer fence catalog or session drift';
    END IF;
    FOR c IN SELECT p.purge_id FROM v3_meta.derived_generation_purge_request p WHERE p.plan_sha256=r.plan_sha256 LOOP
        IF EXISTS (SELECT 1 FROM v3_meta.purge_open_event(c.purge_id,NULL,'SERVICES_QUIESCED',ARRAY['SERVICES_RESTORED'])
                   WHERE event_sequence IS NOT NULL) THEN RAISE EXCEPTION 'services require restoration'; END IF;
        IF EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event x WHERE x.purge_id=c.purge_id
              AND x.event_kind='FAILED_STOP' AND x.failure_evidence->'integrity_failed'='true'::jsonb) THEN
            RAISE EXCEPTION 'integrity failure stops purge';
        END IF;
        IF EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event x WHERE x.purge_id=c.purge_id
          AND x.relation_code<>code_ AND x.event_kind='AUTOVACUUM_DISABLED'
          AND NOT EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event z WHERE z.purge_id=x.purge_id
              AND z.relation_code=x.relation_code AND z.event_kind='AUTOVACUUM_RESTORED' AND z.event_sequence>x.event_sequence)) THEN
            RAISE EXCEPTION 'different relation autovacuum fence open';
        END IF;
    END LOOP;
    SELECT * INTO e FROM v3_meta.purge_open_event(purge_,code_,'AUTOVACUUM_DISABLED',ARRAY['AUTOVACUUM_RESTORED']);
    IF e.event_sequence IS NULL THEN RAISE EXCEPTION 'autovacuum fence required'; END IF;
    FOR c IN SELECT CASE WHEN oid=e.relation_oid THEN 'heap' ELSE 'toast' END AS scope,
        age(relfrozenxid)::bigint AS xid_age,mxid_age(relminmxid)::bigint AS mxid_age
        FROM pg_class WHERE oid IN (e.relation_oid,(SELECT reltoastrelid FROM pg_class WHERE oid=e.relation_oid)) LOOP
        IF delete_ AND (c.xid_age>=(e.operational_payload->c.scope->>'xid_ceiling')::bigint
           OR c.mxid_age>=(e.operational_payload->c.scope->>'mxid_ceiling')::bigint) THEN
            RAISE EXCEPTION 'autovacuum fence age ceiling exceeded';
        END IF;
    END LOOP;
END $$;
CREATE FUNCTION v3_meta.purge_key_expression(code_ text) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
    PERFORM v3_meta.purge_relation(code_);
    RETURN CASE code_ WHEN 'economy_opportunity' THEN 'ARRAY[system_id64,body_pk,economy_ordinal::bigint]'
        WHEN 'body_mechanics' THEN 'ARRAY[system_id64,body_pk]' ELSE 'ARRAY[system_id64]' END;
END $$;

-- Host observations must be supplied afresh by P2's fixed operator, never
-- inferred by this database from an old plan. WAL/catalog facts are rechecked.
CREATE FUNCTION v3_meta.purge_check_measurements(evidence_ jsonb) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF NOT COALESCE((evidence_->>'filesystem_free_bytes')::numeric >= 100000000000 +
          1.5*(evidence_->>'batch_peak_bytes')::numeric AND
          (evidence_->>'batch_peak_bytes')::numeric>=0 AND (evidence_->>'batch_peak_bytes')::numeric<1000000000 AND
          (evidence_->>'wal_bytes')::bigint>=0 AND
          evidence_->'wal_healthy'='true'::jsonb AND evidence_->'wal_same_filesystem'='true'::jsonb AND
          evidence_->'workers_stopped'='true'::jsonb AND
          (evidence_->>'observed_at')::timestamptz>clock_timestamp()-interval '30 seconds' AND
          (evidence_->>'observed_at')::timestamptz<=clock_timestamp(),false) THEN
        RAISE EXCEPTION 'fresh disk, WAL or worker evidence missing or unsafe';
    END IF;
END $$;
CREATE FUNCTION v3_meta.purge_order_columns(code_ text) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
    PERFORM v3_meta.purge_relation(code_);
    RETURN CASE code_ WHEN 'economy_opportunity' THEN 'system_id64,body_pk,economy_ordinal'
        WHEN 'body_mechanics' THEN 'system_id64,body_pk' ELSE 'system_id64' END;
END $$;
CREATE FUNCTION v3_meta.purge_range_predicate(code_ text) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
    PERFORM v3_meta.purge_relation(code_);
    RETURN CASE code_
        WHEN 'economy_opportunity' THEN '(system_id64,body_pk,economy_ordinal) >= ($2[1],$2[2],$2[3]::smallint) AND (system_id64,body_pk,economy_ordinal) <= ($3[1],$3[2],$3[3]::smallint)'
        WHEN 'body_mechanics' THEN '(system_id64,body_pk) >= ($2[1],$2[2]) AND (system_id64,body_pk) <= ($3[1],$3[2])'
        ELSE 'system_id64 >= $2[1] AND system_id64 <= $3[1]' END;
END $$;
CREATE FUNCTION v3_meta.purge_start_payload_batch(purge_ uuid, code_ text, max_rows_ integer, evidence_ jsonb)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r v3_meta.derived_generation_purge_request; relation_ regclass; keys_ jsonb;
        event_ bigint; c record;
BEGIN
    IF max_rows_ IS NULL OR max_rows_ NOT BETWEEN 1 AND 100000 THEN RAISE EXCEPTION 'batch limit out of range'; END IF;
    r:=v3_meta.purge_assert_request(purge_);
    PERFORM v3_meta.purge_assert_phase(purge_,code_);
    PERFORM v3_meta.purge_assert_fences(purge_,code_);
    PERFORM v3_meta.purge_check_measurements(evidence_);
    FOR c IN SELECT p.purge_id FROM v3_meta.derived_generation_purge_request p WHERE p.plan_sha256=r.plan_sha256 LOOP
        IF EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event e WHERE e.purge_id=c.purge_id
           AND e.event_kind IN ('BATCH_STARTED','REWRITE_STARTED') AND NOT EXISTS (
             SELECT 1 FROM v3_meta.derived_generation_purge_event z WHERE z.purge_id=e.purge_id
               AND z.relation_code=e.relation_code AND z.event_sequence>e.event_sequence
               AND ((e.event_kind='BATCH_STARTED' AND z.event_kind IN ('BATCH_COMMITTED','BATCH_ROLLED_BACK'))
                 OR (e.event_kind='REWRITE_STARTED' AND z.event_kind IN ('REWRITE_VERIFIED','REWRITE_ROLLED_BACK'))))) THEN
            RAISE EXCEPTION 'unresolved destructive operation';
        END IF;
    END LOOP;
    relation_:=v3_meta.purge_relation(code_);
    EXECUTE format('SELECT jsonb_agg(k ORDER BY k) FROM (SELECT %s AS k FROM %s WHERE derived_generation_id=$1 ORDER BY %s LIMIT $2) b',
        v3_meta.purge_key_expression(code_),relation_,v3_meta.purge_order_columns(code_)) INTO keys_ USING r.derived_generation_id,max_rows_;
    INSERT INTO v3_meta.derived_generation_purge_event(purge_id,event_kind,relation_code,relation_oid,backend_pid,backend_start,operational_payload)
      SELECT purge_,'BATCH_STARTED',code_,relation_,pg_backend_pid(),backend_start,
          evidence_ || jsonb_build_object('low',keys_->0,'high',keys_->-1,'selected',COALESCE(jsonb_array_length(keys_),0),'max_rows',max_rows_)
      FROM pg_stat_activity WHERE pid=pg_backend_pid() RETURNING event_sequence INTO event_;
    RETURN event_;
END $$;
CREATE FUNCTION v3_meta.purge_derived_payload_batch(purge_ uuid, code_ text, max_rows_ integer)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
SET lock_timeout='5s' SET statement_timeout='60s' AS $$
DECLARE r v3_meta.derived_generation_purge_request; e v3_meta.derived_generation_purge_event;
        relation_ regclass; n bigint; remaining_ bigint; low_ bigint[]; high_ bigint[];
BEGIN
    IF max_rows_ IS NULL OR max_rows_ NOT BETWEEN 1 AND 100000 THEN RAISE EXCEPTION 'batch limit out of range'; END IF;
    r:=v3_meta.purge_assert_request(purge_);
    PERFORM v3_meta.purge_assert_phase(purge_,code_);
    PERFORM v3_meta.purge_assert_fences(purge_,code_);
    SELECT * INTO e FROM v3_meta.purge_open_event(purge_,code_,'BATCH_STARTED',ARRAY['BATCH_COMMITTED','BATCH_ROLLED_BACK']);
    IF e.event_sequence IS NULL OR e.backend_pid<>pg_backend_pid()
       OR e.backend_start IS DISTINCT FROM (SELECT backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid())
       OR e.transaction_id=pg_current_xact_id_if_assigned()
       OR (e.operational_payload->>'max_rows')::integer<>max_rows_ THEN
        RAISE EXCEPTION 'committed matching batch start required';
    END IF;
    PERFORM v3_meta.purge_check_measurements(e.operational_payload);
    relation_:=v3_meta.purge_relation(code_);
    SELECT array_agg(value::bigint ORDER BY ordinality) INTO low_
        FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(e.operational_payload->'low')='array' THEN e.operational_payload->'low' ELSE '[]'::jsonb END) WITH ORDINALITY;
    SELECT array_agg(value::bigint ORDER BY ordinality) INTO high_
        FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(e.operational_payload->'high')='array' THEN e.operational_payload->'high' ELSE '[]'::jsonb END) WITH ORDINALITY;
    PERFORM set_config('v3.purge_id',purge_::text,true);
    EXECUTE format('WITH chosen AS (SELECT ctid FROM %s WHERE derived_generation_id=$1 AND %s ORDER BY %s LIMIT $4) DELETE FROM %s t USING chosen b WHERE t.ctid=b.ctid AND t.derived_generation_id=$1',
        relation_,v3_meta.purge_range_predicate(code_),v3_meta.purge_order_columns(code_),relation_)
        USING r.derived_generation_id,low_,high_,max_rows_;
    GET DIAGNOSTICS n=ROW_COUNT;
    IF n<>(e.operational_payload->>'selected')::bigint THEN RAISE EXCEPTION 'batch deterministic range drift'; END IF;
    SET CONSTRAINTS ALL IMMEDIATE;
    SELECT rows_remaining INTO remaining_ FROM v3_meta.derived_generation_purge_event
        WHERE purge_id=purge_ AND relation_code=code_ AND event_kind='BATCH_COMMITTED'
        ORDER BY event_sequence DESC LIMIT 1;
    IF remaining_ IS NULL THEN
        SELECT (value->'payload_counts'->>code_)::bigint INTO remaining_
            FROM jsonb_array_elements(r.reviewed_plan->'targets')
            WHERE value->>'id'=r.derived_generation_id::text;
    END IF;
    remaining_:=remaining_-n;
    IF remaining_ IS NULL OR remaining_<0 THEN RAISE EXCEPTION 'batch target count drift'; END IF;
    INSERT INTO v3_meta.derived_generation_purge_event(purge_id,event_kind,relation_code,relation_oid,
        rows_affected,rows_remaining,operational_payload,wal_lsn)
        VALUES(purge_,'BATCH_COMMITTED',code_,relation_,n,remaining_,jsonb_build_object('start_event',e.event_sequence),pg_current_wal_lsn());
    PERFORM set_config('v3.purge_id','',true);
    RETURN n;
END $$;

CREATE FUNCTION v3_meta.purge_assert_boundary(purge_ uuid, code_ text) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r v3_meta.derived_generation_purge_request; counts_ jsonb;
BEGIN
    r:=v3_meta.purge_assert_request(purge_);
    IF v3_meta.purge_snapshot() IS DISTINCT FROM r.frozen_snapshot THEN
        RAISE EXCEPTION 'survivor, retained metadata or schema ledger drift';
    END IF;
    IF EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_request p
        WHERE p.plan_sha256=r.plan_sha256 AND (v3_meta.purge_counts(p.derived_generation_id)->>code_)::bigint<>0) THEN
        RAISE EXCEPTION 'target payload remains in relation';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_index WHERE indrelid=v3_meta.purge_relation(code_) AND (NOT indisvalid OR NOT indisready))
       OR EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=v3_meta.purge_relation(code_) AND contype='f' AND NOT convalidated)
       OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=v3_meta.purge_relation(code_) AND tgenabled<>'O') THEN
        RAISE EXCEPTION 'purge FK, index or trigger integrity failure';
    END IF;
    SET CONSTRAINTS ALL IMMEDIATE;
END $$;
CREATE FUNCTION v3_meta.complete_derived_payload_relation(purge_ uuid, code_ text, receipt_ jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    PERFORM v3_meta.purge_assert_phase(purge_,code_);
    PERFORM v3_meta.purge_assert_boundary(purge_,code_);
    IF NOT COALESCE((receipt_->>'filesystem_free_bytes')::bigint>=100000000000
        AND (receipt_->>'wal_bytes')::bigint>=0 AND receipt_->>'receipt_sha256' ~ '^[0-9a-f]{64}$',false) THEN
        RAISE EXCEPTION 'post-phase measurements or receipt missing';
    END IF;
    IF EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event WHERE purge_id=purge_
        AND relation_code=code_ AND event_kind='RELATION_LOGICALLY_EMPTY') THEN RETURN; END IF;
    INSERT INTO v3_meta.derived_generation_purge_event(purge_id,event_kind,relation_code,relation_oid,rows_remaining,
        target_counts,survivor_counts,relation_bytes,filesystem_free_bytes,wal_lsn,wal_bytes,receipt_sha256)
        SELECT purge_,'RELATION_LOGICALLY_EMPTY',code_,v3_meta.purge_relation(code_),0,
            v3_meta.purge_counts(r.derived_generation_id),v3_meta.purge_counts(r.expected_derived_id),
            pg_total_relation_size(v3_meta.purge_relation(code_)),(receipt_->>'filesystem_free_bytes')::bigint,
            pg_current_wal_lsn(),(receipt_->>'wal_bytes')::bigint,receipt_->>'receipt_sha256'
        FROM v3_meta.derived_generation_purge_request r WHERE purge_id=purge_;
END $$;

-- Operational append surface. Phase-authoritative events can only be written
-- by their dedicated verifier; payload DML and arbitrary event forgery stay out.
CREATE FUNCTION v3_meta.purge_record_event(purge_ uuid, kind_ text, code_ text, payload_ jsonb)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r v3_meta.derived_generation_purge_request; e v3_meta.derived_generation_purge_event;
        relation_ regclass; node_ oid; event_ bigint; pid_ integer; started_ timestamptz;
BEGIN
    IF kind_ IN ('FAILED_STOP','SERVICES_RESTORED','BATCH_ROLLED_BACK','REWRITE_ROLLED_BACK') THEN
        r:=v3_meta.purge_recovery_request(purge_);
    ELSE r:=v3_meta.purge_assert_request(purge_); END IF;
    IF payload_ IS NULL OR jsonb_typeof(payload_)<>'object' THEN RAISE EXCEPTION 'operational evidence required'; END IF;
    IF code_ IS NOT NULL THEN relation_:=v3_meta.purge_relation(code_); END IF;
    IF kind_ IN ('REWRITE_STARTED','REWRITE_VERIFIED','REWRITE_ROLLED_BACK') THEN
        IF relation_ IS NULL THEN RAISE EXCEPTION 'rewrite relation required'; END IF;
        SELECT * INTO e FROM v3_meta.purge_open_event(purge_,code_,'REWRITE_STARTED',ARRAY['REWRITE_VERIFIED','REWRITE_ROLLED_BACK']);
        SELECT relfilenode INTO node_ FROM pg_class WHERE oid=relation_;
        IF kind_='REWRITE_STARTED' THEN
            IF EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event x
                JOIN v3_meta.derived_generation_purge_request p USING(purge_id)
                WHERE p.plan_sha256=r.plan_sha256 AND x.relation_code=code_ AND x.event_kind='REWRITE_VERIFIED') THEN
                RAISE EXCEPTION 'rewrite already verified for purge relation; no new rewrite';
            END IF;
            PERFORM v3_meta.purge_assert_recovery_safe(purge_);
            PERFORM v3_meta.purge_assert_phase(purge_,code_);
            PERFORM v3_meta.purge_assert_fences(purge_,code_,false);
            PERFORM v3_meta.purge_assert_boundary(purge_,code_);
            PERFORM v3_meta.purge_check_measurements(payload_);
            IF NOT COALESCE((payload_->>'rewrite_peak_bytes')::numeric>=0 AND
                (payload_->>'filesystem_free_bytes')::numeric>=100000000000+
                1.5*(payload_->>'rewrite_peak_bytes')::numeric,false) THEN
                RAISE EXCEPTION 'rewrite projected headroom unsafe or unmeasured';
            END IF;
            IF e.event_sequence IS NOT NULL THEN RAISE EXCEPTION 'rewrite already unresolved'; END IF;
            IF NOT EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event x
                JOIN v3_meta.derived_generation_purge_request p USING(purge_id)
                WHERE p.plan_sha256=r.plan_sha256 AND x.relation_code=code_ AND x.event_kind='RELATION_LOGICALLY_EMPTY') THEN
                RAISE EXCEPTION 'logical completion receipt required';
            END IF;
            SELECT pid,backend_start INTO pid_,started_ FROM pg_stat_activity WHERE pid=pg_backend_pid();
        ELSE
            IF e.event_sequence IS NULL THEN RAISE EXCEPTION 'no unresolved rewrite'; END IF;
            IF EXISTS (SELECT 1 FROM pg_stat_progress_cluster WHERE relid=relation_)
                OR EXISTS (SELECT 1 FROM pg_stat_progress_analyze WHERE relid=relation_) THEN
                RAISE EXCEPTION 'rewrite or analyze still running';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid=e.backend_pid AND backend_start=e.backend_start
                AND pid<>pg_backend_pid()) THEN RAISE EXCEPTION 'recorded rewrite backend still alive'; END IF;
            IF kind_='REWRITE_ROLLED_BACK' THEN
                IF node_<>e.relfilenode_before THEN RAISE EXCEPTION 'rewrite committed; cannot mark rolled back'; END IF;
            ELSE
                PERFORM v3_meta.purge_assert_boundary(purge_,code_);
                PERFORM pg_stat_clear_snapshot();
                IF node_=e.relfilenode_before OR NOT EXISTS (SELECT 1 FROM pg_stat_user_tables
                    WHERE relid=relation_ AND last_analyze>e.created_at) THEN
                    RAISE EXCEPTION 'rewritten relation and trailing analyze not verified';
                END IF;
                IF NOT COALESCE((payload_->>'filesystem_free_bytes')::bigint>=100000000000
                    AND (payload_->>'wal_bytes')::bigint>=0 AND payload_->>'receipt_sha256' ~ '^[0-9a-f]{64}$',false) THEN
                    RAISE EXCEPTION 'rewrite measurements missing';
                END IF;
            END IF;
        END IF;
    ELSIF kind_='BATCH_ROLLED_BACK' THEN
        SELECT * INTO e FROM v3_meta.purge_open_event(purge_,code_,'BATCH_STARTED',ARRAY['BATCH_COMMITTED','BATCH_ROLLED_BACK']);
        IF e.event_sequence IS NULL OR EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid=e.backend_pid AND backend_start=e.backend_start) THEN
            RAISE EXCEPTION 'batch rollback requires open record and departed backend';
        END IF;
        -- A successful batch closes its record atomically. A departed backend
        -- with an open record therefore cannot have committed its delete.
    ELSIF kind_ IN ('SERVICES_QUIESCED','SERVICES_RESTORED') THEN
        IF code_ IS NOT NULL THEN RAISE EXCEPTION 'service recovery is not relation scoped'; END IF;
        IF payload_->'containers' IS NULL OR jsonb_typeof(payload_->'containers')<>'array' THEN
            RAISE EXCEPTION 'exact container set required';
        END IF;
        SELECT * INTO e FROM v3_meta.purge_open_event(purge_,NULL,'SERVICES_QUIESCED',ARRAY['SERVICES_RESTORED']);
        IF kind_='SERVICES_QUIESCED' AND e.event_sequence IS NOT NULL THEN
            RAISE EXCEPTION 'services already quiesced; restore the recorded container set first';
        END IF;
        IF kind_='SERVICES_RESTORED' THEN
            PERFORM v3_meta.purge_assert_recovery_safe(purge_);
            IF e.event_sequence IS NULL OR e.operational_payload->'containers' IS DISTINCT FROM payload_->'containers' THEN
                RAISE EXCEPTION 'restore container set mismatch';
            END IF;
        END IF;
    ELSIF kind_='FAILED_STOP' THEN
        IF payload_->>'acknowledge_failed_stop' IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM v3_meta.derived_generation_purge_event x
            JOIN v3_meta.derived_generation_purge_request p USING(purge_id)
            WHERE p.plan_sha256=r.plan_sha256 AND x.event_sequence=(payload_->>'acknowledge_failed_stop')::bigint
              AND x.event_kind='FAILED_STOP' AND x.failure_evidence->'integrity_failed'='true'::jsonb) THEN
            RAISE EXCEPTION 'acknowledgement must name the exact integrity failure';
        END IF;
    ELSE
        RAISE EXCEPTION 'event kind requires its dedicated verifier';
    END IF;
    INSERT INTO v3_meta.derived_generation_purge_event(purge_id,event_kind,relation_code,relation_oid,
        relfilenode_before,relfilenode_after,backend_pid,backend_start,recovered,operational_payload,
        failure_evidence,relation_bytes,filesystem_free_bytes,wal_lsn,wal_bytes,receipt_sha256)
        VALUES(COALESCE(e.purge_id,purge_),kind_,code_,relation_,
            CASE WHEN kind_='REWRITE_STARTED' THEN node_ ELSE e.relfilenode_before END,
            CASE WHEN kind_='REWRITE_VERIFIED' THEN node_ END,pid_,started_,
            COALESCE((payload_->>'recovered')::boolean,false),payload_,
            CASE WHEN kind_='FAILED_STOP' THEN payload_ END,
            CASE WHEN relation_ IS NOT NULL THEN pg_total_relation_size(relation_) END,
            (payload_->>'filesystem_free_bytes')::bigint,pg_current_wal_lsn(),
            (payload_->>'wal_bytes')::bigint,payload_->>'receipt_sha256') RETURNING event_sequence INTO event_;
    IF kind_='REWRITE_VERIFIED' THEN
        PERFORM v3_meta.purge_set_relation_autovacuum(purge_,code_,true);
    END IF;
    RETURN event_;
END $$;

CREATE FUNCTION v3_meta.cancel_purge_backend(purge_ uuid, code_ text, rewrite_ boolean) RETURNS boolean
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE e v3_meta.derived_generation_purge_event; relation_ regclass; result_ boolean; condition_ text;
BEGIN
    relation_:=v3_meta.purge_relation(code_);
    SELECT * INTO e FROM v3_meta.purge_open_event(purge_,code_,
        CASE WHEN rewrite_ THEN 'REWRITE_STARTED' ELSE 'BATCH_STARTED' END,
        CASE WHEN rewrite_ THEN ARRAY['REWRITE_VERIFIED','REWRITE_ROLLED_BACK'] ELSE ARRAY['BATCH_COMMITTED','BATCH_ROLLED_BACK'] END);
    IF e.event_sequence IS NULL OR e.relation_oid<>relation_ OR e.backend_pid=pg_backend_pid()
        OR NOT EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid=e.backend_pid AND backend_start=e.backend_start
            AND datname=current_database() AND state='active') THEN
        RAISE EXCEPTION 'no authenticated live purge backend';
    END IF;
    IF rewrite_ THEN
        IF NOT EXISTS (SELECT 1 FROM pg_stat_progress_cluster WHERE pid=e.backend_pid AND relid=e.relation_oid)
          AND NOT EXISTS (SELECT 1 FROM pg_stat_progress_analyze WHERE pid=e.backend_pid AND relid=e.relation_oid)
          -- VACUUM FULL has no progress row while acquiring its initial lock.
          -- Authenticate the bounded command AND its actual pending lock; text
          -- alone (including a comment containing VACUUM) is never sufficient.
          AND NOT EXISTS (SELECT 1 FROM pg_stat_activity a
            JOIN pg_locks l ON l.pid=a.pid
            WHERE a.pid=e.backend_pid AND a.backend_start=e.backend_start
              AND a.datname=current_database() AND a.state='active'
              AND a.backend_type='client backend' AND a.wait_event_type='Lock'
              AND octet_length(a.query)<=1024
              AND btrim(regexp_replace(lower(left(a.query,1024)),'[[:space:]]+',' ','g'))
                ~ ('^vacuum ([(] *full( *, *analyze)? *[)]|full) v3_derived[.]'||code_||' *;?$')
              AND l.locktype='relation' AND l.database=(SELECT oid FROM pg_database WHERE datname=current_database())
              AND l.relation=e.relation_oid AND l.mode='AccessExclusiveLock' AND NOT l.granted) THEN
            RAISE EXCEPTION 'recorded backend is not rewriting or analyzing recorded relation';
        END IF;
    ELSE
        IF NOT EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid=e.backend_pid
            AND query LIKE '%purge_derived_payload_batch%')
          OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=e.backend_pid AND relation=e.relation_oid
            AND mode='RowExclusiveLock' AND granted) THEN
            RAISE EXCEPTION 'recorded backend is not deleting recorded relation';
        END IF;
    END IF;
    condition_:=COALESCE(NULLIF(current_setting('v3.purge_cancel_condition',true),''),'owner_cancel');
    IF condition_ NOT IN ('owner_cancel','controller_loss','free_space_floor','wal_archiver_failure','wal_retention','wal_filesystem') THEN
        RAISE EXCEPTION 'unknown cancellation condition';
    END IF;
    result_:=pg_cancel_backend(e.backend_pid);
    INSERT INTO v3_meta.derived_generation_purge_event(purge_id,event_kind,relation_code,relation_oid,backend_pid,backend_start,operational_payload)
        VALUES(e.purge_id,CASE WHEN rewrite_ THEN 'REWRITE_CANCEL_REQUESTED' ELSE 'BATCH_CANCEL_REQUESTED' END,
            code_,relation_,e.backend_pid,e.backend_start,jsonb_build_object('condition',condition_,'signalled',result_));
    RETURN result_;
END $$;
CREATE FUNCTION v3_meta.cancel_purge_rewrite(purge_ uuid, code_ text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$ SELECT v3_meta.cancel_purge_backend(purge_,code_,true) $$;
CREATE FUNCTION v3_meta.cancel_purge_batch(purge_ uuid, code_ text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$ SELECT v3_meta.cancel_purge_backend(purge_,code_,false) $$;

CREATE FUNCTION v3_meta.complete_derived_generation_purge(purge_ uuid, receipt_ jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r v3_meta.derived_generation_purge_request; code_ text; c record;
BEGIN
    r:=v3_meta.purge_assert_request(purge_);
    IF EXISTS (SELECT 1 FROM pg_stat_activity
        WHERE datname=current_database() AND pid<>pg_backend_pid()
               AND (backend_xid IS NOT NULL OR (backend_type='client backend' AND state IS NULL))) THEN
        RAISE EXCEPTION 'competing writing transaction';
    END IF;
    PERFORM v3_meta.purge_assert_recovery_safe(purge_);
    IF EXISTS (SELECT 1 FROM v3_meta.purge_open_event(purge_,NULL,'SERVICES_QUIESCED',ARRAY['SERVICES_RESTORED'])
               WHERE event_sequence IS NOT NULL) THEN
        RAISE EXCEPTION 'quiesced services have not been restored';
    END IF;
    FOREACH code_ IN ARRAY ARRAY['system_search','economy_opportunity','body_mechanics','system_rating_vector'] LOOP
        PERFORM v3_meta.purge_assert_boundary(purge_,code_);
        IF NOT EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event e
           JOIN v3_meta.derived_generation_purge_request p USING(purge_id)
           WHERE p.plan_sha256=r.plan_sha256 AND e.relation_code=code_ AND e.event_kind='REWRITE_VERIFIED') THEN
            RAISE EXCEPTION 'relation not physically reclaimed';
        END IF;
        SELECT * INTO c FROM v3_meta.purge_open_event(purge_,code_,'AUTOVACUUM_DISABLED',ARRAY['AUTOVACUUM_RESTORED']);
        IF c.event_sequence IS NOT NULL THEN RAISE EXCEPTION 'autovacuum fence not restored'; END IF;
        IF EXISTS (SELECT 1 FROM v3_meta.purge_open_event(purge_,code_,'REWRITE_STARTED',ARRAY['REWRITE_VERIFIED','REWRITE_ROLLED_BACK'])
                   WHERE event_sequence IS NOT NULL)
           OR EXISTS (SELECT 1 FROM v3_meta.purge_open_event(purge_,code_,'BATCH_STARTED',ARRAY['BATCH_COMMITTED','BATCH_ROLLED_BACK'])
                   WHERE event_sequence IS NOT NULL) THEN RAISE EXCEPTION 'unresolved purge operation'; END IF;
    END LOOP;
    SELECT e.* INTO c FROM v3_meta.derived_generation_purge_event e JOIN v3_meta.derived_generation_purge_request p USING(purge_id)
        WHERE p.plan_sha256=r.plan_sha256 AND e.event_kind IN ('WRITER_FENCE_ENABLED','WRITER_FENCE_RESTORED')
        ORDER BY e.event_sequence DESC LIMIT 1;
    IF c.event_kind IS DISTINCT FROM 'WRITER_FENCE_RESTORED'
       OR c.operational_payload->'verified' IS DISTINCT FROM 'true'::jsonb THEN RAISE EXCEPTION 'writer fence recovery not verified'; END IF;
    IF NOT COALESCE((receipt_->>'filesystem_free_bytes')::bigint>=100000000000 AND
        (receipt_->>'wal_bytes')::bigint>=0 AND receipt_->>'receipt_sha256' ~ '^[0-9a-f]{64}$',false) THEN
        RAISE EXCEPTION 'final measurements missing';
    END IF;
    IF EXISTS (SELECT 1 FROM v3_meta.derived_generation_purge_event WHERE purge_id=purge_ AND event_kind='COMPLETED') THEN RETURN; END IF;
    INSERT INTO v3_meta.derived_generation_purge_event(purge_id,event_kind,target_counts,survivor_counts,
        filesystem_free_bytes,wal_bytes,wal_lsn,receipt_sha256)
        VALUES(purge_,'COMPLETED',v3_meta.purge_counts(r.derived_generation_id),v3_meta.purge_counts(r.expected_derived_id),
            (receipt_->>'filesystem_free_bytes')::bigint,(receipt_->>'wal_bytes')::bigint,pg_current_wal_lsn(),receipt_->>'receipt_sha256');
END $$;

GRANT USAGE ON SCHEMA v3_meta,v3_derived,v3_spatial TO v3_derived_purge_executor;
GRANT SELECT ON ALL TABLES IN SCHEMA v3_meta,v3_derived,v3_spatial TO v3_derived_purge_executor;
GRANT MAINTAIN ON v3_derived.system_search,v3_derived.economy_opportunity,
    v3_derived.body_mechanics,v3_derived.system_rating_vector TO v3_derived_purge_executor;
-- Revoke defaults on each newly installed helper and definer, never on an
-- existing publication function. Only the public capability surface is granted.
DO $$
DECLARE f record;
BEGIN
    FOR f IN SELECT oid::regprocedure AS signature FROM pg_proc
        WHERE pronamespace='v3_meta'::regnamespace AND proname IN (
        'reject_purge_ledger_mutation','purge_relation','purge_counts','purge_retained_counts','purge_snapshot',
        'purge_assert_empty_exclusions','purge_assert_request','begin_derived_generation_purge','guard_purge_delete',
        'purge_open_event','purge_recovery_request','purge_assert_recovery_safe','purge_assert_phase','purge_set_relation_autovacuum','purge_set_writer_fence',
        'purge_recycle_writer_sessions','purge_verify_writer_fence','purge_assert_fences','purge_key_expression',
        'purge_check_measurements','purge_order_columns','purge_range_predicate','purge_start_payload_batch','purge_derived_payload_batch','purge_assert_boundary',
        'complete_derived_payload_relation','purge_record_event','cancel_purge_backend','cancel_purge_rewrite',
        'cancel_purge_batch','complete_derived_generation_purge') LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
        EXECUTE format('ALTER FUNCTION %s SET lock_timeout = %L',f.signature,'5s');
        EXECUTE format('ALTER FUNCTION %s SET statement_timeout = %L',f.signature,'60s');
    END LOOP;
END $$;
GRANT EXECUTE ON FUNCTION v3_meta.purge_snapshot(),
    v3_meta.begin_derived_generation_purge(jsonb),
    v3_meta.purge_start_payload_batch(uuid,text,integer,jsonb),
    v3_meta.purge_derived_payload_batch(uuid,text,integer),
    v3_meta.complete_derived_payload_relation(uuid,text,jsonb),
    v3_meta.complete_derived_generation_purge(uuid,jsonb),
    v3_meta.purge_set_relation_autovacuum(uuid,text,boolean),
    v3_meta.purge_set_writer_fence(uuid,boolean,bigint),
    v3_meta.purge_recycle_writer_sessions(uuid,boolean),
    v3_meta.purge_verify_writer_fence(uuid,boolean,text),
    v3_meta.purge_record_event(uuid,text,text,jsonb),
    v3_meta.cancel_purge_rewrite(uuid,text),v3_meta.cancel_purge_batch(uuid,text)
TO v3_derived_purge_executor;
COMMIT;
