-- ED-Finder V3 identity and Frontier OAuth support.
--
-- This is the first post-baseline migration in the independent V3 lineage.
-- It extends the fresh v3_identity schema created by 001_v3_baseline.sql.
-- It does not inspect, copy, convert, or depend on V2 OAuth tables.

DO $baseline_guard$
BEGIN
    IF to_regclass('v3_meta.schema_migration') IS NULL
       OR to_regclass('v3_identity.account') IS NULL
       OR to_regclass('v3_identity.external_identity') IS NULL
       OR to_regclass('v3_identity.commander') IS NULL
       OR to_regclass('v3_identity.account_commander_access') IS NULL
       OR to_regclass('v3_identity.session') IS NULL
       OR to_regclass('v3_identity.security_audit_event') IS NULL THEN
        RAISE EXCEPTION
            '002_v3_accounts_identity.sql requires the frozen V3 baseline';
    END IF;
END;
$baseline_guard$;

-- Stable application roles. The numeric identifiers are V3-local and have no
-- relationship to PostgreSQL roles or the retired V2 OAuth prototype.
INSERT INTO v3_identity.role(role_id, public_code, description, active)
VALUES
    (1, 'OWNER', 'Single bootstrap owner with privileged product access.', true),
    (2, 'ADMIN', 'Delegated administrative product access.', true),
    (3, 'MEMBER', 'Authenticated product member.', true),
    (4, 'SERVICE', 'Application service-principal assignment.', true);

ALTER TABLE v3_identity.account_role
    ADD COLUMN granted_by_account_id uuid,
    ADD COLUMN grant_source text NOT NULL DEFAULT 'MIGRATION';

ALTER TABLE v3_identity.account_role
    ADD CONSTRAINT account_role_granted_by_account_fk
        FOREIGN KEY (granted_by_account_id)
        REFERENCES v3_identity.account(account_id),
    ADD CONSTRAINT account_role_grant_source_not_blank
        CHECK (btrim(grant_source) <> '');

CREATE UNIQUE INDEX account_role_single_active_owner_uidx
    ON v3_identity.account_role(role_id)
    WHERE role_id = 1 AND revoked_at IS NULL;

COMMENT ON INDEX v3_identity.account_role_single_active_owner_uidx IS
    'V3 owner bootstrap is single-assignment; later role management is separate.';

ALTER TABLE v3_identity.external_identity
    ADD CONSTRAINT external_identity_provider_normalized
        CHECK (provider = lower(btrim(provider)) AND btrim(provider) <> ''),
    ADD CONSTRAINT external_identity_issuer_not_blank
        CHECK (btrim(issuer) <> ''),
    ADD CONSTRAINT external_identity_subject_not_blank
        CHECK (btrim(subject) <> '');

CREATE INDEX external_identity_account_active_idx
    ON v3_identity.external_identity(account_id, provider)
    WHERE disabled_at IS NULL;

COMMENT ON INDEX v3_identity.external_identity_account_active_idx IS
    'List active login identities for one V3 account without name-based inference.';

ALTER TABLE v3_identity.session
    ADD COLUMN token_issued_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    ADD COLUMN revocation_reason text;

ALTER TABLE v3_identity.session
    ADD CONSTRAINT session_token_issued_after_creation
        CHECK (token_issued_at >= created_at),
    ADD CONSTRAINT session_token_issued_before_absolute_expiry
        CHECK (token_issued_at < absolute_expires_at),
    ADD CONSTRAINT session_revocation_reason_state
        CHECK (
            revocation_reason IS NULL
            OR (
                session_state IN ('ROTATED', 'REVOKED')
                AND btrim(revocation_reason) <> ''
            )
        );

CREATE TABLE v3_identity.oauth_login_state (
    state_sha256 bytea PRIMARY KEY,
    code_verifier text NOT NULL,
    return_to text NOT NULL DEFAULT '/',
    intent text NOT NULL DEFAULT 'LOGIN' CHECK (intent IN ('LOGIN', 'LINK')),
    account_id uuid REFERENCES v3_identity.account(account_id),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    expires_at timestamptz NOT NULL,
    CHECK (octet_length(state_sha256) = 32),
    CHECK (btrim(code_verifier) <> ''),
    CHECK (return_to LIKE '/%' AND return_to NOT LIKE '//%'),
    CHECK (
        (intent = 'LOGIN' AND account_id IS NULL)
        OR (intent = 'LINK' AND account_id IS NOT NULL)
    ),
    CHECK (expires_at > created_at)
);

CREATE INDEX oauth_login_state_expiry_idx
    ON v3_identity.oauth_login_state(expires_at);

COMMENT ON TABLE v3_identity.oauth_login_state IS
    'Short-lived PKCE state for V3 login/link; the raw OAuth state and provider tokens are never persisted.';

CREATE INDEX security_audit_event_code_time_idx
    ON v3_identity.security_audit_event(event_code, occurred_at DESC);

COMMENT ON INDEX v3_identity.security_audit_event_code_time_idx IS
    'Review security-sensitive V3 events by stable code and time.';

COMMENT ON TABLE v3_identity.account IS
    'Fresh V3 account authority. No V2 OAuth account or session rows are migrated.';
