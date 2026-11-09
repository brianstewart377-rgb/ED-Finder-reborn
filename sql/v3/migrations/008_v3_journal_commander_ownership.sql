-- 007 is reserved by the separately reviewed Ratings code-upgrade proposal.
-- New imports can retain verified commander ownership without conflating
-- overlapping observations from two commanders under the same account.
-- Existing journal rows stay unassigned: historical default-owner selection
-- was not proof of the journal FID and must not be backfilled as verified.
BEGIN;

ALTER TABLE v3_identity.commander ADD COLUMN journal_name_observed_at timestamptz;
COMMENT ON COLUMN v3_identity.commander.journal_name_observed_at IS
    'Observation time for a private display label from a journal with a verified FID; names never establish identity or access.';

ALTER TABLE v3_private.journal_event
    ADD COLUMN owner_commander_id uuid,
    ADD CONSTRAINT journal_event_commander_access_fk
        FOREIGN KEY (owner_account_id, owner_commander_id)
        REFERENCES v3_identity.account_commander_access(account_id, commander_id);

ALTER TABLE v3_private.journal_event
    DROP CONSTRAINT journal_event_owner_account_id_event_type_event_key_key,
    ADD CONSTRAINT journal_event_owner_commander_identity_key
        UNIQUE NULLS NOT DISTINCT
        (owner_account_id, owner_commander_id, event_type, event_key);

CREATE INDEX journal_event_commander_time_idx
    ON v3_private.journal_event(owner_account_id, owner_commander_id, event_timestamp DESC);
COMMENT ON INDEX v3_private.journal_event_commander_time_idx IS
    'Use: bounded commander-specific chronology; NULL remains historical unassigned content.';
CREATE INDEX journal_event_import_idx ON v3_private.journal_event(private_import_id, owner_account_id);
COMMENT ON INDEX v3_private.journal_event_import_idx IS
    'Use: bounded import receipts and contribution extraction without scanning an entire account history.';
CREATE INDEX journal_event_file_commander_idx
    ON v3_private.journal_event(journal_file_id, owner_commander_id);
COMMENT ON INDEX v3_private.journal_event_file_commander_idx IS
    'Use: verify prior file ownership on bounded import retries without scanning event history.';
COMMENT ON COLUMN v3_private.journal_event.owner_commander_id IS
    'Explicit commander context; newly verified imports resolve journal FID against a server-verified active owner edge. Never backfill from names or historical default-owner selection.';

-- Preserve every selected-file occurrence even when semantic event dedupe wins.
CREATE TABLE v3_private.journal_file_event (
    owner_account_id uuid NOT NULL,
    journal_file_id uuid NOT NULL,
    journal_event_id uuid NOT NULL,
    PRIMARY KEY (journal_file_id, journal_event_id),
    FOREIGN KEY (journal_file_id, owner_account_id)
        REFERENCES v3_private.journal_import_file(journal_file_id, owner_account_id),
    FOREIGN KEY (journal_event_id, owner_account_id)
        REFERENCES v3_private.journal_event(journal_event_id, owner_account_id)
);
COMMENT ON TABLE v3_private.journal_file_event IS
    'Private file occurrence provenance for selected-file consent; never implies sharing or verified ownership.';
INSERT INTO v3_private.journal_file_event(owner_account_id, journal_file_id, journal_event_id)
    SELECT owner_account_id, journal_file_id, journal_event_id FROM v3_private.journal_event;

COMMIT;
