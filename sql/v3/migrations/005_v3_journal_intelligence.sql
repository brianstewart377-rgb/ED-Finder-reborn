-- ED-Finder V3 migration 005: account-scoped journal intelligence foundation.
--
-- Private journal-ingestion lane for the personal journal workstream
-- (docs/architecture/V3_JOURNAL_INTELLIGENCE_AND_EDRE_EVIDENCE_PIPELINE.md).
-- Private trust zone only (ADR-011): nothing here reaches public/canonical
-- galaxy tables; promotion requires a separately authorized write lane.
-- Client-side parsing uploads normalized allowlisted events only; raw
-- journal files are never stored.

BEGIN;

DO $baseline_guard$
BEGIN
    IF to_regclass('v3_meta.schema_migration') IS NULL
       OR to_regclass('v3_private.private_import') IS NULL
       OR to_regclass('v3_source.source_artifact') IS NULL
       OR to_regclass('v3_source.source_run') IS NULL
       OR to_regclass('v3_identity.account') IS NULL THEN
        RAISE EXCEPTION
            '005_v3_journal_intelligence.sql requires the frozen V3 baseline';
    END IF;
END;
$baseline_guard$;

-- ---------------------------------------------------------------------------
-- Account-scoped journal acquisition files.
-- One row per physical journal file admitted for one account. The file
-- content hash is the ADR-010 acquisition artifact identity and the
-- level-1 dedupe key: renaming or re-uploading a file must never create
-- new events (A-1 provenance contract carried forward).
-- ---------------------------------------------------------------------------

CREATE TABLE v3_private.journal_import_file (
    journal_file_id uuid PRIMARY KEY,
    private_import_id uuid NOT NULL,
    owner_account_id uuid NOT NULL,
    file_name text NOT NULL,
    content_sha256 bytea NOT NULL,
    size_bytes bigint NOT NULL,
    line_count bigint NOT NULL DEFAULT 0,
    event_count integer NOT NULL DEFAULT 0,
    first_event_at timestamptz,
    last_event_at timestamptz,
    file_state text NOT NULL DEFAULT 'ADMITTED' CHECK (file_state IN (
        'ADMITTED', 'REJECTED'
    )),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    FOREIGN KEY (private_import_id, owner_account_id)
        REFERENCES v3_private.private_import(private_import_id, owner_account_id),
    UNIQUE (journal_file_id, owner_account_id),
    UNIQUE (owner_account_id, content_sha256),
    CHECK (octet_length(content_sha256) = 32),
    CHECK (size_bytes >= 0),
    CHECK (line_count >= 0),
    CHECK (event_count >= 0),
    CHECK (btrim(file_name) <> '' AND length(file_name) <= 512),
    CHECK (last_event_at IS NULL OR first_event_at IS NULL OR last_event_at >= first_event_at)
);

COMMENT ON TABLE v3_private.journal_import_file IS
    'Account-owned journal file provenance. content_sha256 is the file-level dedupe identity; file_name is display/provenance only and must not be used as identity.';

CREATE INDEX journal_import_file_import_idx
    ON v3_private.journal_import_file(private_import_id, created_at);
COMMENT ON INDEX v3_private.journal_import_file_import_idx IS
    'Use: enumerate the files of one owned import.';

-- ---------------------------------------------------------------------------
-- Account-scoped normalized journal events.
-- Level-2 dedupe: UNIQUE(owner_account_id, event_type, event_key) where
-- event_key is the canonical semantic identity (see the V3.0 event identity
-- contract). event_timestamp is deliberately NOT part of the key for
-- stable-identity classes. event_payload carries only allowlisted fields;
-- the server strips non-allowlisted keys before insert.
-- ---------------------------------------------------------------------------

CREATE TABLE v3_private.journal_event (
    journal_event_id uuid PRIMARY KEY,
    owner_account_id uuid NOT NULL,
    private_import_id uuid NOT NULL,
    journal_file_id uuid NOT NULL,
    source_run_id uuid NOT NULL REFERENCES v3_source.source_run(source_run_id),
    event_type text NOT NULL CHECK (event_type IN (
        'ApproachBody', 'CarrierJump', 'CodexEntry', 'Commander', 'Died',
        'Disembark', 'Docked', 'Embark', 'Fileheader', 'FSDJump', 'FSDTarget',
        'FSSAllBodiesFound', 'FSSBodySignals', 'FSSDiscoveryScan', 'LeaveBody',
        'Liftoff', 'LoadGame', 'Location', 'MultiSellExplorationData',
        'NavRoute', 'NavRouteClear', 'Resurrect', 'SAAScanComplete',
        'SAASignalsFound', 'Scan', 'ScanOrganic', 'Screenshot',
        'SellExplorationData', 'SellOrganicData', 'Touchdown'
    )),
    event_key jsonb NOT NULL,
    event_payload jsonb NOT NULL,
    event_timestamp timestamptz NOT NULL,
    source_record_hash bytea NOT NULL,
    source_offset bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    FOREIGN KEY (private_import_id, owner_account_id)
        REFERENCES v3_private.private_import(private_import_id, owner_account_id),
    FOREIGN KEY (journal_file_id, owner_account_id)
        REFERENCES v3_private.journal_import_file(journal_file_id, owner_account_id),
    UNIQUE (journal_event_id, owner_account_id),
    UNIQUE (owner_account_id, event_type, event_key),
    CHECK (jsonb_typeof(event_key) = 'object'),
    CHECK (jsonb_typeof(event_payload) = 'object'),
    CHECK (octet_length(source_record_hash) = 32),
    CHECK (source_offset >= 0)
);

COMMENT ON TABLE v3_private.journal_event IS
    'Private normalized journal event. The semantic dedupe identity is (owner_account_id, event_type, event_key); event_timestamp and source_record_hash are attributes, not identity. Deliberately has no canonical-generation or canonical-entity foreign key (ADR-011 private zone).';

CREATE INDEX journal_event_owner_time_idx
    ON v3_private.journal_event(owner_account_id, event_timestamp DESC);
COMMENT ON INDEX v3_private.journal_event_owner_time_idx IS
    'Use: personal exploration chronology for one account.';

CREATE INDEX journal_event_owner_type_idx
    ON v3_private.journal_event(owner_account_id, event_type);
COMMENT ON INDEX v3_private.journal_event_owner_type_idx IS
    'Use: per-event-type personal projections.';

CREATE INDEX journal_event_system_idx
    ON v3_private.journal_event(owner_account_id, event_type, (event_key->>'SystemAddress'));
COMMENT ON INDEX v3_private.journal_event_system_idx IS
    'Use: visited/scanned systems per account.';

CREATE INDEX journal_event_body_idx
    ON v3_private.journal_event(owner_account_id, event_type, (event_key->>'SystemAddress'), (event_key->>'BodyID'));
COMMENT ON INDEX v3_private.journal_event_body_idx IS
    'Use: scanned bodies per account.';

-- ---------------------------------------------------------------------------
-- Versioned research-consent decisions (R7). Append-only per
-- (account, consent_version, decision): a GRANT and a later WITHDRAW row
-- coexist so decision history is preserved. Effective state = the row with
-- the greatest decided_at for the account. Consent is never inferred from
-- journal upload or account creation.
-- ---------------------------------------------------------------------------

CREATE TABLE v3_private.research_consent (
    research_consent_id uuid PRIMARY KEY,
    owner_account_id uuid NOT NULL,
    consent_version text NOT NULL,
    sanitized_contract_version text NOT NULL,
    purpose text NOT NULL,
    audience_code text NOT NULL,
    decision text NOT NULL CHECK (decision IN ('GRANT', 'WITHDRAW')),
    decided_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    withdrawn_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    UNIQUE (research_consent_id, owner_account_id),
    UNIQUE (owner_account_id, consent_version, decision),
    CHECK (btrim(consent_version) <> ''),
    CHECK (btrim(sanitized_contract_version) <> ''),
    CHECK (btrim(purpose) <> ''),
    CHECK (btrim(audience_code) <> ''),
    CHECK ((decision = 'WITHDRAW') = (withdrawn_at IS NOT NULL))
);

COMMENT ON TABLE v3_private.research_consent IS
    'Versioned, explicit, revocable research-contribution consent. Separate from journal upload; a WITHDRAW row supersedes the matching GRANT row by decided_at.';

CREATE INDEX research_consent_owner_decided_idx
    ON v3_private.research_consent(owner_account_id, decided_at DESC);
COMMENT ON INDEX v3_private.research_consent_owner_decided_idx IS
    'Use: resolve the effective consent decision for one account.';

-- ---------------------------------------------------------------------------
-- Research export batch receipts (R9/R10). The payload itself is built
-- deterministically on demand; the receipt stores the payload SHA-256 so a
-- replay can be proven byte-identical. Retraction is supersede-not-delete:
-- withdrawal supersedes batches by lineage token, never deletes them.
-- ---------------------------------------------------------------------------

CREATE TABLE v3_private.research_export_batch (
    export_batch_id uuid PRIMARY KEY,
    owner_account_id uuid NOT NULL,
    consent_version text NOT NULL,
    sanitized_contract_version text NOT NULL,
    lineage_token text NOT NULL,
    observation_count integer NOT NULL DEFAULT 0,
    manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
    payload_sha256 bytea NOT NULL,
    batch_state text NOT NULL DEFAULT 'CREATED' CHECK (batch_state IN (
        'CREATED', 'SUPERSEDED'
    )),
    superseded_by_batch_id uuid,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    FOREIGN KEY (superseded_by_batch_id, owner_account_id)
        REFERENCES v3_private.research_export_batch(export_batch_id, owner_account_id),
    UNIQUE (export_batch_id, owner_account_id),
    CHECK (octet_length(payload_sha256) = 32),
    CHECK (observation_count >= 0),
    CHECK (jsonb_typeof(manifest) = 'object'),
    CHECK (lineage_token ~ '^[A-Za-z0-9_-]{16,64}$'),
    CHECK (btrim(consent_version) <> ''),
    CHECK (btrim(sanitized_contract_version) <> '')
);

COMMENT ON TABLE v3_private.research_export_batch IS
    'Deterministic sanitized export batch receipt. payload_sha256 proves replayability; lineage_token keys supersede-not-delete retraction on consent withdrawal.';

CREATE INDEX research_export_batch_owner_time_idx
    ON v3_private.research_export_batch(owner_account_id, created_at DESC);
COMMENT ON INDEX v3_private.research_export_batch_owner_time_idx IS
    'Use: list one account export receipts.';

CREATE INDEX research_export_batch_lineage_idx
    ON v3_private.research_export_batch(lineage_token);
COMMENT ON INDEX v3_private.research_export_batch_lineage_idx IS
    'Use: locate batches to supersede when consent is withdrawn.';

COMMIT;
