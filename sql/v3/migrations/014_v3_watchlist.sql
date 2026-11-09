-- V3 private, sync-key-scoped watchlist snapshots. No legacy data is imported.
-- Only new empty relations are created: no canonical/derived scans, foreign
-- keys to generation tables, or ACCESS EXCLUSIVE locks on populated tables.
BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE v3_private.watchlist (
    sync_key text NOT NULL CHECK (sync_key ~ '^[A-Za-z0-9_-]{16,128}$'),
    system_id64 bigint NOT NULL CHECK (system_id64 > 0),
    id bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    name text NOT NULL,
    x double precision NOT NULL,
    y double precision NOT NULL,
    z double precision NOT NULL,
    population bigint CHECK (population >= 0),
    is_colonised boolean,
    alert_min_score smallint CHECK (alert_min_score BETWEEN 0 AND 100),
    alert_economy text CHECK (length(alert_economy) <= 64),
    added_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    last_checked_at timestamptz,
    last_status text,
    PRIMARY KEY (sync_key, system_id64)
);

CREATE INDEX watchlist_scope_added_idx
    ON v3_private.watchlist (sync_key, added_at DESC, system_id64);

COMMENT ON TABLE v3_private.watchlist IS
    'Guest sync_key is the credential. Canonical name/coordinate snapshots survive generation publication; unknown facts stay NULL.';

CREATE TABLE v3_private.watchlist_changelog (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sync_key text NOT NULL,
    system_id64 bigint NOT NULL,
    system_name text NOT NULL,
    change_type text NOT NULL,
    old_value text,
    new_value text,
    detected_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    FOREIGN KEY (sync_key, system_id64)
        REFERENCES v3_private.watchlist (sync_key, system_id64) ON DELETE CASCADE
);

CREATE INDEX watchlist_changelog_scope_detected_idx
    ON v3_private.watchlist_changelog (sync_key, detected_at DESC, id DESC);

COMMENT ON TABLE v3_private.watchlist_changelog IS
    'Private per-watch change evidence. No V3 change detector is active yet; an empty feed means no recorded changes.';

COMMIT;
