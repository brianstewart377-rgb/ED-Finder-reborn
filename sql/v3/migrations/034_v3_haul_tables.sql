-- 034: the tables for Haul, the shopping list: what each construction site a
-- member is working on still needs, where to buy it, and what is on the
-- member's own fleet carrier.
--
-- "Haul — design", step 1. The owner agreed these eight tables on
-- 28 November 2026 (decisions 2 to 4, 9 and 10 in that document).
--
--   v3_vocab.commodity            the game's goods: id, symbol, name, kind,
--                                 and whether construction sites ask for it.
--   v3_identity.plugin_key        the keys members make for ED-Finder's EDMC
--                                 plugin; only a SHA-256 of each key is kept.
--   v3_private.haul_site_reading  a member's readings of a construction site,
--                                 one row each time the game's list differs
--                                 from the member's last one for that site.
--   v3_private.haul_carrier       the member's own fleet carrier.
--   v3_private.haul_carrier_stock what is on it, good by good, and where each
--                                 figure came from.
--   v3_state.haul_market_fill     each nightly pass over Spansh's stations
--                                 file, and how far it has got.
--   v3_state.haul_market_station  each fixed station whose market Haul keeps.
--   v3_state.haul_market          the construction goods it sells, with
--                                 price and stock.
--
-- The first rule of Haul: a site's list is the game's own. Each time a player
-- docks at a site the game writes the whole list to the journal (needed and
-- delivered so far, by everyone, for each good); Haul keeps the newest list
-- and never adds up deliveries.
--
-- This file creates tables only. Every row is written by the site or by the
-- nightly work: the commodity list from a copy in the repository, markets
-- from Spansh's galaxy_stations.json.gz in batches that can stop and carry
-- on. Nothing reads these tables until a later build does, so the build and
-- this migration can come in either order. Nothing that exists is changed.
-- The private tables are in the schemas the nightly small backup takes
-- (v3_identity, v3_private); the market tables can be filled again from the
-- next night's file and are only in the weekly full backup. Safe to run more
-- than once.

BEGIN;
SET LOCAL lock_timeout = '5s';

-- -- The game's goods ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS v3_vocab.commodity (
    -- The game's own number for the good, as Spansh's file and the game's
    -- market files give it.
    commodity_id bigint PRIMARY KEY CHECK (commodity_id > 0),
    -- The game's short name in lower case: 'aluminium' for the journal's
    -- '$aluminium_name;' and Spansh's 'Aluminium'.
    symbol text NOT NULL UNIQUE CHECK (symbol ~ '^[a-z0-9_]+$'),
    display_name text NOT NULL CHECK (display_name <> ''),
    category text NOT NULL CHECK (category <> ''),
    -- Whether construction sites ask for it (about 55 goods do).
    construction boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true
);
COMMENT ON TABLE v3_vocab.commodity IS
    'The game''s goods, from the community table of the game''s ids (EDCD/FDevIDs commodity.csv), '
    'filled by the nightly work from a copy kept in the repository.';

-- -- Keys for the EDMC plugin ------------------------------------------------

CREATE TABLE IF NOT EXISTS v3_identity.plugin_key (
    plugin_key_id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES v3_identity.account(account_id),
    -- SHA-256 of the key. The key itself is shown to the member once, when it
    -- is made, and never stored.
    key_sha256 bytea NOT NULL UNIQUE CHECK (octet_length(key_sha256) = 32),
    -- The member's own name for it, such as 'gaming PC'.
    label text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 60),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    last_used_at timestamptz CHECK (last_used_at >= created_at),
    revoked_at timestamptz CHECK (revoked_at >= created_at)
);
CREATE INDEX IF NOT EXISTS plugin_key_account_idx
    ON v3_identity.plugin_key (account_id) WHERE revoked_at IS NULL;
COMMENT ON TABLE v3_identity.plugin_key IS
    'Keys members make on the Account page for ED-Finder''s EDMC plugin. Only a SHA-256 of '
    'each key is kept. A cancelled key has revoked_at set and is never accepted again.';

-- -- A member's readings of construction sites ---------------------------------

CREATE TABLE IF NOT EXISTS v3_private.haul_site_reading (
    owner_account_id uuid NOT NULL REFERENCES v3_identity.account(account_id),
    -- The site's market number (the journal's MarketID).
    market_id bigint NOT NULL CHECK (market_id > 0),
    -- The journal's time for the reading.
    read_at timestamptz NOT NULL,
    -- From the Docked event with the same market number; empty until one is
    -- seen, because the depot event names no system or station.
    system_id64 bigint CHECK (system_id64 > 0),
    site_name text CHECK (site_name <> ''),
    site_type text CHECK (site_type <> ''),
    -- The game's own figures.
    progress real NOT NULL CHECK (progress BETWEEN 0 AND 1),
    complete boolean NOT NULL,
    failed boolean NOT NULL,
    -- For each good: its symbol, required, provided and payment, as the game
    -- wrote them.
    needs jsonb NOT NULL CHECK (jsonb_typeof(needs) = 'array'),
    received_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (owner_account_id, market_id, read_at)
);
COMMENT ON TABLE v3_private.haul_site_reading IS
    'A member''s readings of a construction site: one row each time the game''s list for the '
    'site differs from the member''s last one. The newest row is the shopping list.';

-- -- The member's own fleet carrier -------------------------------------------

CREATE TABLE IF NOT EXISTS v3_private.haul_carrier (
    owner_account_id uuid NOT NULL REFERENCES v3_identity.account(account_id),
    -- The carrier's market number (the journal's CarrierID).
    carrier_id bigint NOT NULL CHECK (carrier_id > 0),
    callsign text CHECK (callsign ~ '^[A-Z0-9]{3}-[A-Z0-9]{3}$'),
    -- The carrier's own total of cargo, in tonnes, from CarrierStats, and when
    -- it was seen.
    total_cargo_t integer CHECK (total_cargo_t >= 0),
    total_seen_at timestamptz,
    CHECK ((total_cargo_t IS NULL) = (total_seen_at IS NULL)),
    PRIMARY KEY (owner_account_id, carrier_id)
);
COMMENT ON TABLE v3_private.haul_carrier IS
    'The fleet carrier a member owns, as their journal shows it. Private: never shown to other '
    'members.';

CREATE TABLE IF NOT EXISTS v3_private.haul_carrier_stock (
    owner_account_id uuid NOT NULL,
    carrier_id bigint NOT NULL,
    -- The good's symbol, as in v3_vocab.commodity.
    symbol text NOT NULL CHECK (symbol ~ '^[a-z0-9_]+$'),
    tonnes integer NOT NULL CHECK (tonnes >= 0),
    -- Where the figure was last set from, and the time of that figure:
    -- 'market' (the carrier's market screen), 'frontier' (Frontier's list
    -- through EDMC), 'typed' (by the member) or 'transfers' (only added up).
    source text NOT NULL CHECK (source IN ('market', 'frontier', 'typed', 'transfers')),
    source_at timestamptz NOT NULL,
    -- The last transfer or trade applied on top of that figure, if any.
    changed_at timestamptz CHECK (changed_at >= source_at),
    -- A transfer would have taken it below zero; it was stopped at zero.
    does_not_add_up boolean NOT NULL DEFAULT false,
    PRIMARY KEY (owner_account_id, carrier_id, symbol),
    FOREIGN KEY (owner_account_id, carrier_id)
        REFERENCES v3_private.haul_carrier (owner_account_id, carrier_id) ON DELETE CASCADE
);
COMMENT ON TABLE v3_private.haul_carrier_stock IS
    'What is on a member''s own carrier, good by good: kept up by transfers, and reset from the '
    'game''s own figures whenever they arrive.';

-- -- Markets, from Spansh's nightly stations file -------------------------------

CREATE TABLE IF NOT EXISTS v3_state.haul_market_fill (
    fill_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Spansh's Last-Modified time for the file this pass reads.
    file_modified_at timestamptz NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    -- How far it has got, so a pass cut off carries on from here.
    stations_read bigint NOT NULL DEFAULT 0 CHECK (stations_read >= 0),
    stations_written bigint NOT NULL DEFAULT 0 CHECK (stations_written >= 0),
    finished_at timestamptz CHECK (finished_at >= started_at)
);
COMMENT ON TABLE v3_state.haul_market_fill IS
    'Each nightly pass that keeps Haul''s markets from Spansh''s galaxy_stations.json.gz, in '
    'batches, and how far it has got.';

CREATE TABLE IF NOT EXISTS v3_state.haul_market_station (
    market_id bigint PRIMARY KEY CHECK (market_id > 0),
    system_id64 bigint NOT NULL CHECK (system_id64 > 0),
    station_name text NOT NULL CHECK (station_name <> ''),
    station_type text,
    -- The largest landing pad: L, M or S. NULL when Spansh gives none.
    largest_pad text CHECK (largest_pad IN ('L', 'M', 'S')),
    distance_to_arrival_ls real CHECK (distance_to_arrival_ls >= 0),
    -- The market's own update time in Spansh's file: as old as the last player
    -- who reported it.
    market_updated_at timestamptz NOT NULL,
    -- The pass that last saw it, and when Haul last wrote it.
    seen_fill_id bigint NOT NULL REFERENCES v3_state.haul_market_fill(fill_id),
    kept_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS haul_market_station_system_idx
    ON v3_state.haul_market_station (system_id64);
COMMENT ON TABLE v3_state.haul_market_station IS
    'Each fixed station (no fleet carriers) whose market Haul keeps, from Spansh''s nightly '
    'stations file.';

CREATE TABLE IF NOT EXISTS v3_state.haul_market (
    market_id bigint NOT NULL REFERENCES v3_state.haul_market_station(market_id) ON DELETE CASCADE,
    commodity_id bigint NOT NULL REFERENCES v3_vocab.commodity(commodity_id),
    buy_price integer NOT NULL CHECK (buy_price >= 0),
    -- Stock for sale; only goods with some in stock are kept.
    supply integer NOT NULL CHECK (supply > 0),
    PRIMARY KEY (market_id, commodity_id)
);
CREATE INDEX IF NOT EXISTS haul_market_commodity_idx
    ON v3_state.haul_market (commodity_id, market_id);
COMMENT ON TABLE v3_state.haul_market IS
    'The construction goods each kept station sells, with price and stock, as of its market''s '
    'update time.';

COMMIT;
