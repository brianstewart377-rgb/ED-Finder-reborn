# Moving ED-Finder to the Raspberry Pi

A checklist, in order. Each step gets its exact commands when we reach it;
this page says what happens and why. Numbers were measured on the current
server on 9 November 2026 unless a line says otherwise.

Reviewed against the plan and the code on 11 November. What that review added
is marked "(11 Nov)".

## What moves, and what stays behind

| | Size now | On the Pi |
|---|---|---|
| Raw galaxy data (every system and body we know of) | 576 GB | All of it, except the 116 GB "body parent evidence" table, which the app never reads and the importer no longer writes. Each body's class is added to it (see section 3) |
| Scores and Finder tables | 424 GB for all 198.5 million systems | Only the 37 million systems within 500 ly of an inhabited system. With them, whole, 0.2 GB of build records that the score rows are tied to by a rule in the database (11 Nov) |
| Density map data | 14 GB | All of it |
| System state (population, factions, claims) | 0.5 GB | All of it |
| Everything users have written: accounts, sign-ins, journal imports, watchlists | under 0.1 GB | All of it. Copied twice: with everything else, and again at switch-over (section 7) |
| Bookkeeping: which galaxy data and which scores are current, where each row came from, the lookup lists, records of the old builds' jobs | 0.4 GB | All of it. The scoring and two of the users' tables depend on it (11 Nov) |
| Leftovers from old builds (`r1_plan`, `r1_meta`, the old test galaxy `v3_gen_phase4b_a`) | under 1 MB | Nothing |

Estimate for the 2 TB drive: 550 to 630 GB, plus about 10 GB for the body
classes. The sizes above are from the server's own list of tables, taken on
11 November, and the estimate still stands.

This table is by group. Every table on the server is listed at the end of this
page, each marked move, cut down or leave (11 Nov). There are no tables
outside these groups. An earlier version of this page said users' notes and
synced profiles were kept in older tables: those tables do not exist on the
server, and no screen of the app uses them.

**One band, not two.** Everything out to 500 ly is scored and kept up to date
every night. Find shows 250 ly by default and can be widened to 500. Nothing
is scored "on request". Confirmed by the owner on 11 November.

## Before anything else

- [ ] Pay the current server for one more month. It renews around 15 November,
      and the move needs both machines running side by side.
- [ ] (11 Nov) That month ends about 15 December. The work left (body class,
      catch-up, reach, a week of running, the copy, a quiet week) ends about
      then with nothing to spare. Expect to pay for one month more, or drop
      the quiet week.

## 1. The Pi itself (owner)

- [ ] Raspberry Pi OS Lite (64-bit), booting from the 500 GB NVMe.
- [ ] Docker installed.
- [ ] When the 2 TB NVMe arrives: fitted, formatted and mounted as the data
      drive. PostgreSQL's data lives there, nothing else of ours on the boot
      drive.
- [ ] (11 Nov) Owner's call: a small battery backup. PostgreSQL survives a
      power cut, but a cut during a write can still damage a drive's file
      system.

## 2. Things to measure before copying

- [ ] **Scoring speed.** Run `scripts/dev/score_speed_test.py` on the Pi and
      compare with the current server, which scored 4,013 systems a minute
      with one worker and 61,555 with all 32 (10 November). The old server's real
      builds did about 36,000 systems a minute per worker. If the Pi is 5 to 10
      times slower, a night's changes (about 116,000 systems in Spansh's
      one-day file on 9 November) take 15 to 30 minutes, and re-scoring all 37
      million from scratch takes one to two and a half days.

- [ ] **Free the space old row versions leave behind.** One night's galaxy
      update grew the database by about 1 GB (10 November). Roughly 0.15 GB of
      that is new systems and bodies; the rest is the old versions of rows
      that were rewritten, which PostgreSQL reuses once its clean-up
      (autovacuum) has run. With default settings it would not run on tables
      this large for about a year. On the Pi, set it to run on the galaxy
      tables and the score tables after a few million changed rows.

- [ ] (11 Nov) **The Pi images run at all.** They are built for the Pi's
      processor but have never run on one. Fetch the API image on the Pi and
      run its built-in help; one minute.

- [ ] (11 Nov) **How long a restore takes.** Building indexes on
      600-million-row tables with 16 GB of memory could take a day or more.
      Time one large table with its indexes first, and build only the indexes
      the app and the nightly jobs use.

- [ ] (11 Nov) **Everything else the Pi does every day.** Only scoring is
      measured above. Find matters most: the app gives up on any database
      query after 15 seconds, and the Pi has 16 GB of memory where the current
      server has 192. Also time the nightly galaxy update, the state refresh
      and the upkeep of reach.

Already known:

- About 67,000 systems a day are new to us (10 November). The galaxy has
  always grown at about that rate: 198.5 million systems in 12 years is
  46,000 a day. Most are deep space with few scanned bodies.
- Spansh publishes its files once a day, all at the same moment, but not at
  the same time each day: 02:10 UTC on 10 November, 03:20 UTC on 11 November.
- Besides the one-day file (1.1 GB) and the whole galaxy (118.9 GB), Spansh
  publishes a 7-day file (3 GB) and a 32-day file (5.7 GB) (11 Nov).
- The images can be fetched without signing in to GitHub (11 Nov).

- **The home line is not a limit.** 1 Gbit fibre to the property, no data cap.
  The nightly downloads (about 9.3 GB for the two state files plus about
  3 GB for the galaxy changes) take a few minutes. The one-off copy, perhaps
  150 to 250 GB (a guess until the copy script exists), will be limited by how
  fast the Pi can write.

## 3. Database work, all on the current server first

Decided 10 November: every change to how the database works is built and
proved on the current server, which is far faster than the Pi, and runs there
for a week before the copy. No data is deleted on the current server. The
cut to 500 ly and the 116 GB table are left out during the copy instead, so
the old server stays whole as the way back.

Found the same day: the galaxy data (systems and bodies) was imported once, on
27 to 28 August, and nothing has updated it since. Every score is that old.

In order:

- [x] **Images for the Pi's processor.** The published images cover both
      amd64 (the current server) and arm64 (the Pi).
- [x] **The scoring code.** The parts that work out a system's scores and its
      Find rows are here, unchanged (`scripts/ratings_v4/`,
      `scripts/v3_system_search.py`, `scripts/v3_system_archetype.py`). The old
      build's proof-and-receipt steps were left behind.
- [x] **A database change that lets the nightly jobs update published data.**
      Migration `025`, applied on the current server on 10 November after a
      fresh backup. The score and Find tables accept changes only from a
      transaction that announces itself as the nightly update.
- [x] **A nightly galaxy update** from Spansh's one-day file (1.1 GB). For
      each system whose rows differ from ours, replace its rows and note it as
      changed (`deploy/galaxy_update.py`,
      `apps/importer/src/spansh_delta/apply.py`, migration `026`). First run on
      the current server, 10 November: 101,429 systems read, 29,369 changed,
      67,122 new, 4,938 identical, in 6 min 56 s; none refused. It has run by
      itself every night since. (11 Nov) Moving to the 7-day file: see "Never
      miss a day".
- [ ] **A nightly re-score** of the systems noted as changed, replacing their
      score rows and Find rows. Checked against real data first, read only
      (`deploy/galaxy_update.py check-scores`): systems nothing has changed
      must get exactly their stored scores back. First run by hand on the
      current server, 10 November: 96,511 systems given scores, 67,122 of
      them for the first time, none failed, in 8 min 8 s. (11 Nov) At first a
      system could be scored only while its record from Spansh's file was in
      hand, because the score uses each body's class and the galaxy tables
      did not hold it. With the class column below, the scoring works from the
      database alone, through the waiting list, so a night when it fails is
      made up by the next run. The nightly switch is still off.
- [ ] (11 Nov) **Each body's class in the galaxy tables.** One new column on
      the bodies table (`body_class`, migration `027`), holding the class as
      the scoring uses it. Needed because the Pi keeps scores only within
      500 ly: when a new colony brings more systems into range they have to
      be scored, and their bodies' classes would be nowhere on the Pi. It also
      means a system on the waiting list can be scored at any time, without
      Spansh's file, and that a class changing with nothing else is seen as a
      change. On the current server the column is filled for the rows the
      nightly update writes; a row from before has its class in its score
      row, and the copy fills every row. Built 11 November; not yet applied
      on the current server. Still to do: Look up reads the class from here,
      so it shows for systems with no scores.
- [ ] (11 Nov) **Never miss a day.** The one-day file holds only the last 24
      hours, so a night the job does not run is a day of changes lost, and
      two files a day apart can leave a gap between them: Spansh made its
      files at 02:10 UTC on 10 November and 03:20 UTC on 11 November, so 70
      minutes of changes fell between those two. Built 11 November, simpler
      than first planned: every night takes the 7-day file, which also reaches
      back over the last six nights; after more than 6 days away, the 32-day
      file; after more than 31 days it stops and says so. No weekly sweep is
      needed. Chosen from the database's record of the last update, so it
      also works on the Pi after the copy. Not yet installed on the current
      server.
- [ ] **One catch-up pass** over Spansh's full galaxy file (118.9 GB) with the
      same code, because no smaller file reaches back to 27 August. Timed on
      the first million systems before the full run.
- [ ] **Reach.** How far each system is from inhabited space, kept up to date
      as colonies appear. Gives Find its 250 ly default and 500 ly limit, and
      tells the copy which systems to take. When a colony brings systems into
      range, they go on the waiting list to be scored.
- [x] (11 Nov) **The table list.** Every table on the current server, each
      marked move, cut down or leave: 130 tables, 1,015 GB. It is at the end
      of this page.
- [ ] **A copy script** that exports the tables as that list marks them,
      with the scored tables cut down to the systems in reach. A logical dump
      (`pg_dump`), not a copy of the data folder: the two machines have
      different processors. (11 Nov) It also fills the class of every body
      (from the body's own row, or else from its score row, so the copy is
      made when no system is waiting for new scores),
      takes the small tables the app's views and the scoring read (which
      galaxy data and which scores are current), and sets up the database's
      users, which a dump does not carry.

## 4. The copy

- [ ] Fresh backup of the current database first.
- [ ] PostgreSQL 18 running on the Pi, data on the 2 TB drive. (11 Nov) With
      its own start-up file in this repo and settings for a 16 GB machine
      (about 4 GB of its own cache; the current server's settings assume
      192 GB), enough shared memory for Docker to allow queries that use
      several processor cores, and the same text encoding and sort order as
      the current database.
- [ ] Run the copy script. Check row counts on both sides, table by table.
- [ ] Clean-up settings on the galaxy and score tables (see section 2), then
      one full clean-up and statistics pass over the restored database.
- [ ] Load the system state on the Pi (`deploy/state_refresh.py run`).
- [ ] (11 Nov) Bring in what changed between taking the copy and the Pi's
      first nightly run. The nightly job does this by itself: it takes the
      7-day or 32-day file by how old the copy's last update is. The Pi's
      first run has to be within 31 days of the current server's last update.

## 5. The app on the Pi

- [ ] (11 Nov) The API's settings file, written by hand on the Pi: the
      database address, the Frontier client id and secret, the site's
      address, the owner token. Nothing in it protects stored data, so new
      values lose nothing.
- [ ] `deploy.conf` beside the deploy tool with the Pi's own settings: network
      name, API settings file, and the psql command for the state refresh.
      (11 Nov) Also the galaxy update's: the database container's name, where
      downloads go, and scoring switched on.
- [ ] `EDFINDER_FIND_CHECK=0` until the Finder data is in place, then remove
      that line.
- [ ] `python3 edfinder_deploy.py live main` and try Find, Look up and Plan on
      the Pi's own address.
- [ ] (11 Nov) **The web container works out each visitor's address itself.**
      On the current server a front web server does that from what Cloudflare
      sends. The Pi has no front web server, so without this change the app
      sees the whole group as one visitor and they share one rate limit (30
      searches a minute between everyone). A change to the web image, made
      and tested before the switch.

## 6. Nightly jobs on the Pi

- [ ] Galaxy update (`edfinder-galaxy-update.timer`), choosing its file as in
      section 3.
- [ ] System state refresh (`edfinder-state-refresh.timer`).
- [ ] Re-score what is on the waiting list.
- [ ] (11 Nov) **Backups, sized for the Pi.** The plan says a full dump every
      night and an automatic restore test every week; for about 600 GB on a
      Pi that is hours every night and a day or more every week. Suggested
      instead: every night, a dump of what cannot be re-created (everything
      users have written, and the settings), to the USB drives, with an
      encrypted copy off-site; once a week, a full dump; a full restore
      tested once before we rely on it.
- [ ] (11 Nov) **Hearing about failures.** Today a nightly job that fails
      tells nobody. Each night's jobs report in when they finish, and
      something off the Pi notices when they have not.

## 7. Switch over

- [ ] (11 Nov) **Copy what users have written a second time.** Days or weeks
      pass between the copy and this step. Stop the old site, copy the small
      user tables again, then switch.
- [ ] Cloudflare Tunnel from the Pi; point `ed-finder.app` at it.
- [ ] (11 Nov) The uptime check from Contabo, the error-tracking address in
      the API's settings, and the health page (disk, memory, temperature,
      database size), all from the plan.
- [ ] Keep the old server running, untouched, for one quiet week as the way
      back.

## 8. Before cancelling the old server

- [ ] **Octopus runs on it** (three containers). Move it to Contabo or switch
      it off.
- [ ] The old repo's GitHub workflows that reach the old server are switched
      off.
- [ ] One last full backup, copied to the USB drives.
- [ ] Cancel the server.

## Changes to the plan since 8 November

The plan says the database stays exactly as it is. Three small additions have
been made or agreed since, each as a reviewed migration after a fresh backup:

- `025` (10 November): the nightly jobs may change published scores.
- `026` (10 November): the waiting list of systems that need new scores.
- `027` (built 11 November, not yet applied): each body's class, one column
  on the bodies table.

Also decided since: one band of scores out to 500 ly, kept up to date every
night (owner, 11 November), in place of 250 ly every night and 500 ly on
request (9 November).

## Not part of the move

The map (Almanac trial), bridge routes, BGS and the BGS-Tally link come after
Find and Plan work on the Pi.

## Every table on the current server

From the server's own list, 11 November 2026: 130 tables, 1,015 GB, all of
them ordinary tables. Sizes include each table's indexes.

**The rule.** Every table moves whole, with four exceptions:

1. The five big score tables move only for systems within 500 ly.
2. `body_parent_evidence` exists on the Pi but stays empty.
3. `r1_meta` and `r1_plan` are not moved.
4. The old test galaxy `v3_gen_phase4b_a` is not moved.

Moved whole: 475 GB. The score tables cut down: 79 GB if systems in reach
have as many bodies as the average system, and they have more, which is why
the estimate above runs to 630 GB.

### The galaxy: `v3_gen_phase4c_full_20260827_r5` (14 tables, 576 GB)

| Table | Size | Rows, about | On the Pi |
|---|---|---|---|
| `bodies` | 248 GB | 599 million | All. Each body's class is filled in during the copy |
| `body_composition` | 127 GB | 444 million | All. Nothing in the app reads it today; it moves because the plan keeps the whole raw galaxy. Owner's call if it should be left like the table below |
| `body_parent_evidence` | 116 GB | 539 million | Empty. The app never reads it and the nightly update does not write it |
| `systems` | 53 GB | 198.5 million | All |
| `rings` | 26 GB | 94 million | All |
| `body_signal_current` | 2.4 GB | 25 million | All |
| `station_service_current` | 1.5 GB | 12.6 million | All |
| `body_genus_current` | 0.7 GB | 7.2 million | All |
| `station_placement`, `ring_signal_current`, `stations`, `station_economy_current` | 1.1 GB together | | All |
| `body_alias`, `station_alias` | empty | | Empty |

### Scores and Find: `v3_derived` (8 tables, 424 GB)

| Table | Size | Rows, about | On the Pi |
|---|---|---|---|
| `economy_opportunity` | 105 GB | 740 million | Systems within 500 ly |
| `system_search` (the Find table) | 96 GB | 198.5 million | Systems within 500 ly |
| `body_mechanics` | 85 GB | 578 million | Systems within 500 ly |
| `system_rating_vector` | 69 GB | 198.5 million | Systems within 500 ly |
| `system_archetype` | 69 GB | 198.5 million | Systems within 500 ly |
| `build_chunk`, `search_build_chunk`, `archetype_build_chunk` | 0.2 GB together | | All. Each system's main score row points at its row in `build_chunk`, and the database enforces that, so it cannot be left. The other two point at it and move with it |

### Density map: `v3_spatial` (8 tables, 14 GB)

All of it. `cell_summary` is the 14 GB; `cell_level`, `cluster`,
`cluster_member`, `cluster_run`, `current_spatial_generation`,
`spatial_generation` and `spatial_publication_audit` are under 1 MB together.

### System state: `v3_state` (6 tables, 0.5 GB)

All of it: `system_state`, `system_faction`, `system_power`, `system_stage`,
`load_log`, and `rescore_queue` (the waiting list for new scores). The nightly
state refresh rebuilds the first four in any case.

### What users have written: `v3_identity` and `v3_private` (26 tables, 63 MB)

All of it, twice (section 7).

- `v3_identity`: `account`, `account_commander_access`, `account_role`,
  `commander`, `commander_external_identity`, `device_credential`,
  `external_identity`, `legacy_sync_claim`, `oauth_login_state`,
  `personal_data_request`, `role`, `security_audit_event`,
  `service_principal`, `service_principal_credential`, `session`.
- `v3_private`: `contribution_receipt`, `contribution_withdrawal`,
  `journal_event`, `journal_file_event`, `journal_import_file`,
  `private_fact`, `private_import`, `research_consent`,
  `research_export_batch`, `watchlist`, `watchlist_changelog`.

### Bookkeeping: `v3_vocab`, `v3_meta`, `v3_source`, `v3_async` (44 tables, 0.4 GB)

All of it. Some of it nothing reads from day to day (the old import's
checkpoints and job records, 0.4 GB, and the record of October's purge), but
other tables are tied to it by rules in the database, and 0.4 GB is not worth
a special case.

- `v3_vocab` (13, the lookup lists): `atmosphere_classification`, `body_type`,
  `economy`, `galaxy_region`, `genus`, `identity_namespace`, `reserve_type`,
  `ring_type`, `signal_type`, `station_service`, `station_type`,
  `terraforming_state`, `volcanism_type`.
- `v3_meta` (16, which galaxy data and which scores are current):
  `canonical_generation`, `canonical_generation_input`,
  `canonical_publication_audit`, `current_canonical_generation`,
  `current_derived_generation`, `derived_generation`,
  `derived_generation_purge_event`, `derived_generation_purge_request`,
  `derived_product`, `derived_product_publication_audit`,
  `derived_publication_audit`, `derived_purge_copy_event`,
  `derived_purge_copy_request`, `schema_migration`, `search_rebuild_plan`,
  `search_rebuild_range`.
- `v3_source` (9, where each row came from; the scoring checks every row
  against it): `canonical_evidence_group`, `journal_physical_snapshot`,
  `source`, `source_artifact`, `source_checkpoint`, `source_rejection`,
  `source_rights_policy`, `source_run`, `unmapped_vocabulary`.
- `v3_async` (6, records of the old builds' jobs; two of the users' tables
  point at `job`): `effect_receipt`, `job`, `job_attempt`, `job_chunk`,
  `job_lease`, `outbox_message`.

### Not moved (24 tables, under 1 MB)

- `r1_meta` (5 tables) and `r1_plan` (5 tables): left from an earlier
  programme. Nothing in this repo's code mentions them.
- `v3_gen_phase4b_a` (14 tables, 0.3 MB): an old test galaxy. To check when
  the copy script is written: if the list of galaxies in
  `v3_meta.canonical_generation` still names it, bringing its 0.3 MB along is
  simpler than changing that list.

### Besides tables

The copy also has to carry what is not a table: the views the app reads
(`v3_app`), the functions and the rules that guard the score tables, the
counters behind numbered columns, and the `cube` extension. A dump of the
database's structure carries all of these. It does not carry the database's
users, which the copy script sets up (section 3).

### Found while checking the list

- The API still has the old addresses for system notes and profile sync. Their
  tables do not exist on the server, so they answer with an error, and no
  screen of the app calls them. To be removed in a tidy-up; nothing to move.
