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
| Leftovers from old builds (`r1_plan`, `r1_meta`) | under 1 MB | Nothing |
| The old test galaxy `v3_gen_phase4b_a` | 0.3 MB | All of it: the list of galaxies still names it (12 Nov) |

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
- [ ] (12 Nov) Nobody uses the site yet, so the catch-up moves to the Pi,
      after the switch (section 3), and the copy comes next. Aim to switch over
      around 20 November and avoid the December payment. Check how much notice
      MevSpace needs to cancel.

## 1. The Pi itself (owner)

- [x] Raspberry Pi OS Lite (64-bit), booting from the 500 GB NVMe (an SK
      Hynix; `nvme0n1` on 11 November). User `pi`, name `Raspi`.
- [x] Docker installed (11 Nov), from Docker's own install script, which
      takes Docker's Debian packages for the 64-bit Pi.
- [x] The 2 TB NVMe (a Netac; `nvme1n1` on 11 November) fitted, formatted
      (ext4, named `edfdata`, 1% held back) and mounted at `/srv/data` by its
      name at every start-up, with `nofail` so the Pi still starts without
      it. PostgreSQL's data lives there, nothing else of ours on the boot
      drive.
- [x] (11 Nov) A key on the Pi (`~/.ssh/edf_move`) that may log in to the
      current server as root, for fetching data during the move. Its line on
      the server starts with `restrict`, so it can run commands but forward
      nothing. Removed again before the server is cancelled (section 8).
- [ ] (11 Nov) Owner's call: a small battery backup. PostgreSQL survives a
      power cut, but a cut during a write can still damage a drive's file
      system.

## 2. Things to measure before copying

- [x] **Scoring speed.** Run `scripts/dev/score_speed_test.py` on the Pi and
      compare with the current server, which scored 4,013 systems a minute
      with one worker and 61,555 with all 32 (10 November). The old server's real
      builds did about 36,000 systems a minute per worker. If the Pi is 5 to 10
      times slower, a night's changes (about 116,000 systems in Spansh's
      one-day file on 9 November) take 15 to 30 minutes, and re-scoring all 37
      million from scratch takes one to two and a half days.
      (11 Nov) Measured: 1,424 a minute with one worker and 5,311 with all 4,
      so about 3 times slower per core and 12 times slower in all; four
      workers did 3.7 times the work of one. A night's scoring (7.5 minutes
      on the server on 11 November) should take 20 to 40 minutes on the Pi;
      re-scoring all 37 million, half a day to a day.

- [ ] **Free the space old row versions leave behind.** One night's galaxy
      update grew the database by about 1 GB (10 November). Roughly 0.15 GB of
      that is new systems and bodies; the rest is the old versions of rows
      that were rewritten, which PostgreSQL reuses once its clean-up
      (autovacuum) has run. With default settings it would not run on tables
      this large for about a year. On the Pi, set it to run on the galaxy
      tables and the score tables after a few million changed rows.

- [x] (11 Nov) **The Pi images run at all.** They are built for the Pi's
      processor but have never run on one. Fetch the API image on the Pi and
      run its built-in help; one minute. Done 11 November with the API image
      of build `6cda1dd`. The web image is tried when the app is set up.

- [x] (11 Nov) **How long a restore takes.** Building indexes on
      600-million-row tables with 16 GB of memory could take a day or more.
      Time one large table with its indexes first, and build only the indexes
      the app and the nightly jobs use. (11 Nov) `scripts/pi/time_table_copy.sh`
      does that for one galaxy table, starting with `systems` (53 GB, 198.5
      million rows): it fetches the table from the server with the Pi's key,
      loads it, builds its indexes, and logs the time of each step.
      (12 Nov) Measured on `systems`: fetched in 27 minutes (16 GB, one
      stream), rows loaded in 18 minutes (35 GB), its three indexes built in
      10 minutes (17 GB). For the whole copy that suggests half a day, a day at
      most; the copy script fetches several tables at once and leaves the
      checks of the rows to the end, which should bring it under that.

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
- [x] **A nightly re-score** of the systems noted as changed, replacing their
      score rows and Find rows. Checked against real data first, read only
      (`deploy/galaxy_update.py check-scores`): systems nothing has changed
      must get exactly their stored scores back. First run by hand on the
      current server, 10 November: 96,511 systems given scores, 67,122 of
      them for the first time, none failed, in 8 min 8 s. (11 Nov) At first a
      system could be scored only while its record from Spansh's file was in
      hand, because the score uses each body's class and the galaxy tables
      did not hold it. With the class column below, the scoring works from the
      database alone, through the waiting list, so a night when it fails is
      made up by the next run. (12 Nov) Switched on on the current server on
      11 November. Its first night by itself, 12 November: 81,659 systems given
      scores (57,765 of them for the first time), none failed, 4 min 38 s.
- [x] (11 Nov) **Each body's class in the galaxy tables.** One new column on
      the bodies table (`body_class`, migration `027`), holding the class as
      the scoring uses it. Needed because the Pi keeps scores only within
      500 ly: when a new colony brings more systems into range they have to
      be scored, and their bodies' classes would be nowhere on the Pi. It also
      means a system on the waiting list can be scored at any time, without
      Spansh's file, and that a class changing with nothing else is seen as a
      change. On the current server the column is filled for the rows the
      nightly update writes; a row from before has its class in its score
      row, and the copy fills every row. Built 11 November; applied on the
      current server the same day. (12 Nov) Look up reads the class from
      here, so it shows for systems with no scores; a row from before the
      column still gets it from its score row.
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
      the first million systems before the full run. (12 Nov) Moved to the Pi,
      after the switch and before the group is invited: nobody uses the site
      yet, so the gap from 24 August (the date of the file the galaxy was
      built from) to 10 November (the first nightly update) harms no one, and
      doing it here first would hold up the copy by days.
- [ ] **Reach.** How far each system is from inhabited space, kept up to date
      as colonies appear. Gives Find its 250 ly default and 500 ly limit, and
      tells the copy which systems to take. When a colony brings systems into
      range, they go on the waiting list to be scored. (12 Nov) The copy works
      out its own list of the systems within 500 ly (below); keeping reach up
      to date, and Find's 250 ly default, are built on the Pi after the switch.
      Until then the Pi's nightly scoring stays switched off, so that it does
      not score systems far outside the 500 ly.
      (12 Nov) Keeping reach up to date is built (migration `028`,
      `apps/importer/src/spansh_delta/reach.py`), simpler than first planned:
      * Reach is worked out afresh for the whole galaxy, from the systems
        inhabited at the time, once a week: by hand the first time
        (`python3 galaxy_update.py reach`), then by the nightly scoring when
        the last time is a week old. Only the rows that differ change.
      * Each night, the systems the galaxy update has just found are measured
        against the same inhabited systems.
      * The nightly scoring scores only the systems within 500 ly, takes the
        rest off the waiting list unscored, and gives scores to any system in
        reach that has none. It will not start before reach has been worked
        out once, where migration `028` has been applied.
      So a new colony counts within a week, not the same night: the systems
      it brings within 500 ly (or 250) are 250 to 500 ly from it, far from
      where anyone colonises next. It stops, changing nothing, if the
      inhabited systems or the systems in reach drop by more than a tenth
      since last time, which would mean a damaged system state.
      Still to do: Find's 250 ly default and its 500 ly choice.
- [x] (11 Nov) **The table list.** Every table on the current server, each
      marked move, cut down or leave: 130 tables, 1,015 GB. It is at the end
      of this page.
- [x] **A copy script** that exports the tables as that list marks them,
      with the scored tables cut down to the systems in reach. A logical dump
      (`pg_dump`), not a copy of the data folder: the two machines have
      different processors. (11 Nov) It also fills the class of every body
      (from the body's own row, or else from its score row, so the copy is
      made when no system is waiting for new scores),
      takes the small tables the app's views and the scoring read (which
      galaxy data and which scores are current), and sets up the database's
      users, which a dump does not carry. (12 Nov) Built:
      `scripts/pi/copy_to_pi.py`, run on the Pi. See section 4.

## 4. The copy

- [ ] Fresh backup of the current database first. (12 Nov) Last night's backup
      is enough: the copy only reads the server's data (see below).
- [ ] PostgreSQL 18 running on the Pi, data on the 2 TB drive. (11 Nov) With
      its own start-up file in this repo and settings for a 16 GB machine
      (about 4 GB of its own cache; the current server's settings assume
      192 GB), enough shared memory for Docker to allow queries that use
      several processor cores, and the same text encoding and sort order as
      the current database. Start-up file: `deploy/pi/postgres.yml`. The
      current server runs PostgreSQL 18.6 built on Debian 12, UTF8,
      `en_US.utf8`, libc (11 November). The Pi runs 18.6 with the same text
      settings, built on Debian 13 (`postgres:18.6-trixie`): the copy builds
      every index afresh, Debian 13 is supported for longer, and the two
      differ only in how rare characters sort. Same container, user and
      database names as the server, so every command works on both. No port
      is opened; the password is in a file only the database can read.
- [ ] (12 Nov) On the server, switch the galaxy update and the state refresh
      off for the length of the copy (the copy will not start otherwise), and
      make sure no system is waiting for new scores.
- [ ] Run the copy script on the Pi: `python3 copy_to_pi.py check`, then
      `python3 copy_to_pi.py copy`. (12 Nov) How it works:
      * On the server it makes one table of its own, the list of systems
        within 500 ly of an inhabited system (schema `edfinder_copy`), and
        removes it at the end. Nothing else on the server changes.
      * Every table is read through one snapshot, so they all match.
      * The Pi's database is made afresh from the server's structure. Tables
        are copied four at a time, straight into the Pi's database, compressed
        by ssh. A table is kept only if all of it arrived, and its rows are
        counted on both machines.
      * Indexes, keys and triggers are built after the rows are in. The links
        between tables and the rules on columns go back on unchecked: checking
        them as the rows went in would cost hours on the Pi (one table's rules
        alone, `body_composition`'s, measured at 23 microseconds a row, 444
        million rows). `python3 copy_to_pi.py validate` checks them afterwards
        and can run with the app in use.
      * A copy that stops part-way carries on when run again, keeping the
        tables already copied, as long as the server's data has not changed.
- [ ] Switch the server's galaxy update and state refresh on again.
- [ ] Clean-up settings on the galaxy and score tables (see section 2). The
      copy gathers the planner's statistics itself. (12 Nov)
      `deploy/pi/cleanup-settings.sql`: on the eleven tables with more than
      ten million rows, the clean-up starts after a million changed rows (the
      statistics after two million) instead of after a fifth of the table.
      Settings only; run once, after `copy_to_pi.py validate` has finished.
- [ ] Load the system state on the Pi (`deploy/state_refresh.py run`).
- [ ] (11 Nov) Bring in what changed between taking the copy and the Pi's
      first nightly run. The nightly job does this by itself: it takes the
      7-day or 32-day file by how old the copy's last update is. The Pi's
      first run has to be within 31 days of the current server's last update.

## 5. The app on the Pi

- [x] (11 Nov) The API's settings file, written by hand on the Pi: the
      database address, the Frontier client id and secret, the site's
      address, the owner token. Nothing in it protects stored data, so new
      values lose nothing. (12 Nov) Simpler than by hand:
      `scripts/pi/make_api_env.py` takes the server's file down the Pi's key
      and writes `/etc/edfinder/api.env` (root only) with the Pi's own
      database and cache addresses; the read-only database address is left
      out (the Pi has no such login). Nothing is shown on the screen.
      (13 Nov) Written on the Pi: 12 settings.
- [x] (12 Nov) Redis for the API's cache: `deploy/pi/redis.yml`. A cache only,
      never saved to disk. (13 Nov) Running on the Pi.
- [x] `deploy.conf` beside the deploy tool with the Pi's own settings: network
      name, API settings file, and the psql command for the state refresh.
      (11 Nov) Also the galaxy update's: the database container's name, where
      downloads go, and scoring switched on. (12 Nov) `deploy/pi/deploy.conf`:
      the network, the settings file, Cloudflare's header for the visitor's
      address, downloads on the 2 TB drive, and no public check until the
      switch. Everything else is the same name as on the server, so the
      defaults fit. Scoring stays off until reach is kept up to date
      (section 3). (13 Nov) In place on the Pi, beside the deploy tool in
      `/opt/edfinder`.
- [ ] `EDFINDER_FIND_CHECK=0` until the Finder data is in place, then remove
      that line. (12 Nov) Not needed: the copy brings the Finder data.
- [ ] `python3 edfinder_deploy.py live main` and try Find, Look up and Plan on
      the Pi's own address. (13 Nov) Live on the Pi since 08:18, build
      `e47ee21`, on `127.0.0.1:58080`. The deploy tool's own checks passed:
      the page, health, the API description, the sign-in state and three
      Find searches. The recorded tour ran against it. Look up and Plan have
      not been tried by hand yet. Builds `2700e57` and `31bd7ea` followed
      the same day. The deploy tool has to be run with `sudo`; the Pi's copy
      was fetched again that evening, with the fixes of that day.
- [x] (11 Nov) **The web container works out each visitor's address itself.**
      On the current server a front web server does that from what Cloudflare
      sends. The Pi has no front web server, so without this change the app
      sees the whole group as one visitor and they share one rate limit (30
      searches a minute between everyone). A change to the web image, made
      and tested before the switch. (12 Nov) Done: on the Pi, `deploy.conf`
      says `EDFINDER_CLIENT_ADDRESS_FROM=cf_connecting_ip`, and the web
      container passes Cloudflare's own header on to the API as the visitor's
      address, replacing any the visitor sent. The current server keeps
      `x_real_ip` (the default): its front web server sets that header. Only
      the tunnel may reach the Pi's web container, so it listens on
      127.0.0.1 only (the deploy tool's default).

## 6. Nightly jobs on the Pi

- [x] Galaxy update (`edfinder-galaxy-update.timer`), choosing its file as in
      section 3. (13 Nov) First run on the Pi, started through systemd: the
      7-day file (2.9 GB, downloaded in 2 minutes), 55 minutes, 8,667,813
      rows written, 348,082 systems put on the waiting list for new scores.
      A dry run of the first 50,000 systems came first (`check --limit
      50000`, 3 minutes). The timer was switched on at 15:19: 04:30 and
      16:30 UTC.
- [x] System state refresh (`edfinder-state-refresh.timer`). (13 Nov) First
      run on the Pi, started through systemd: 28 minutes, 160,911 systems
      read, 16,585 changed. Find goes on reading while it loads. The timer
      was switched on at 15:19: 03:30 UTC.
- [ ] (13 Nov) The galaxy update met four signal types it does not know and
      left them out: `$PlanetaryMiningLocation_Name;` in 99,693 systems of
      the 7-day file, and `Cobalt`, `LiquidOxygen` and `Lepidolite` in one
      each. The owner's choice: keep them.
      The first is real and wide: a count, on a planet, of the places that
      can be mined on its surface (a real record: Drojau KR-W e1-0, four
      places). Spansh's files have listed it since the game's mining update
      of September 2026; our own test records of Alioth, from 8 September,
      list it on seven bodies.
      Migration `029` adds it to the list of known signal types and the
      importer now writes it. On the Pi, in this order: back up the word
      lists, apply `029`, then deploy the build. Deployed first, the galaxy
      update refuses to run until `029` is there. Systems already on the Pi
      get the signal when a file lists them again: within a week for those
      in the 7-day files, the rest at the catch-up.
      The other three are not new. `Cobalt` is a hotspot on one ring, in a
      record from 2019, beside two ordinary hotspots. The importer's own
      rule is to leave rare odd words out; they are written down in
      `v3_source.unmapped_vocabulary`. Left out until someone can say what
      they are.
      Done on the Pi on 13 Nov, 16:47 to 16:51: the word lists backed up
      (`/home/pi/v3_vocab-before-029.sql.gz`, 4,531 bytes), `029` applied
      ("added signal type 8"), build `31bd7ea` live. The owner took the
      backup of the word lists as the fresh backup for this change, since
      the full nightly backup is not built yet. Still to see: the 04:30 run
      of 14 Nov, the first to write the signal.
      Inspect shows it in the game's own words, "planetary mining
      locations" (the owner, 13 Nov).
- [ ] Re-score what is on the waiting list. (12 Nov) In order: apply
      migration `028` (after a fresh backup), work reach out once
      (`python3 galaxy_update.py reach`), then add `EDFINDER_GALAXY_SCORES=on`
      to `deploy.conf`.
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
      (12 Nov) Built: `deploy/report_in.py`. systemd runs it when the galaxy
      update or the state refresh ends, and it tells healthchecks.io, which
      the old servers used and which emails the owner (his choice, 12 Nov)
      about a failure or a night with no word. Still to do on the Pi: fetch
      the script with the units, add the two checks on healthchecks.io and
      save their addresses (README, "Hearing when a nightly job fails").
      Backups report the same way once they are built.

## 7. Switch over

- [ ] (11 Nov) **Copy what users have written a second time.** Days or weeks
      pass between the copy and this step. Stop the old site, copy the small
      user tables again, then switch.
- [ ] Cloudflare Tunnel from the Pi; point `ed-finder.app` at it. (12 Nov)
      `deploy/pi/tunnel.yml`; the tunnel's token goes in
      `/etc/edfinder/tunnel.env` (root only). Try it first on a second web
      address that goes to the Pi, before moving `ed-finder.app` itself.
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
- [ ] (11 Nov) Remove the Pi's key from the server: the line ending
      `pi-move` in `/root/.ssh/authorized_keys`.
- [ ] Cancel the server.

## Changes to the plan since 8 November

The plan says the database stays exactly as it is. Four small additions have
been made or agreed since, each as a reviewed migration after a fresh backup:

- `025` (10 November): the nightly jobs may change published scores.
- `026` (10 November): the waiting list of systems that need new scores.
- `027` (11 November): each body's class, one column on the bodies table.
- `028` (built 12 November, for the Pi only, not yet applied): which systems
  are within 500 ly and 250 ly of an inhabited system (three new tables).

(12 Nov) The galaxy map: the owner has chosen the Almanac map, with the
System Map staying on Babylon, where the plan left the choice open and had
Babylon going too. See "Not part of the move".

Also decided since: one band of scores out to 500 ly, kept up to date every
night (owner, 11 November), in place of 250 ly every night and 500 ly on
request (9 November). (12 Nov) The systems a new colony brings within that
band are scored within a week rather than the same night: see "Reach".

(12 Nov) The catch-up over Spansh's full galaxy file moves to the Pi, after
the switch: nobody uses the site yet, and it would have held up the copy.
The copy comes first, then keeping reach up to date and Find's 250 ly default,
then the catch-up, all before the group is invited.

## Not part of the move

The map (Almanac trial), bridge routes, BGS and the BGS-Tally link come after
Find and Plan work on the Pi.

### The Almanac trial: passed (owner, 13 November)

The galaxy map moves to Almanac. The System Map stays on Babylon, as the
owner chose on 12 November. His words on the evening of the 13th, after
seeing his own systems and Find's on the trial page on his graphics card:

> Hot damn that map looks better than anything we came up with on Babylon

> I think the trial is very much passed this is already exceeding our best
> effort on Babylon and now we have a clear plan of what we want from it

What the trial showed, all with Almanac exactly as published (0.7.1):

- It draws our own data on the owner's graphics card with no errors, at
  about 4 to 5 ms a frame, and switches from one system to another in 3 ms.
- It already has the panels the trial needed, and takes our own values and
  buttons in them.
- The three jobs on the roadmap below need nothing changed in Almanac.

The build work stays where the roadmap below puts it: after the Pi move.
`scripts/almanac-trial` stays until the map is built into the site, as the
working example of how our data goes into Almanac. It goes after that.

### The System Map: is Babylon still right? (13 November, open)

The owner, once the galaxy map was settled: "is Babylon still the right
choice for a really nice system map or is it massively excessive?"

What our System Map draws today, read from the code: each body as a plain
coloured ball, faint bands on gas giants, orbit rings, a line from each body
to its parent, and names. No surface pictures. It is a diagram drawn with a
3D engine, and once the galaxy map moves to Almanac, Babylon stays in the
app for this one view.

What the owner wants from it, shown with five pictures of the game's own
system map: "This but I'd like it to look a bit more 3d and show what
orbitals are built on each body (similar to how the ingame system map
behaves".

A trial page draws that with no 3D engine: `scripts/almanac-trial`,
`system-map.html`, with Sol, two systems of two stars and the owner's own,
from the live site's data. Each body is a shaded ball, rings pass behind and
in front, and every body and station is an ordinary page element, so
ordinary tests can check it in CI. The owner has not yet judged it. Nothing
is decided, and the System Map on the site is unchanged.

What building it showed about our data:

- Sites on the ground come with their body. Stations in orbit mostly do not:
  none of Sol's ten has one, and it is the same in the owner's systems. So
  "what is built in orbit of each body" cannot be read from our data today.
- Placing them by distance from the arrival point works for a lone planet
  and fails beside a moon. For Sol it puts Columbus at Jupiter, where the
  owner's list has Europa, and it leaves M.Gorbachev unplaced, because our
  distance for it (502 ls) is 5 ls off Earth's.
- The sources disagree about Burnell Station. Our data and INARA's page for
  Sol both give 363 ls, which is Venus's distance. EDSM ties it to Ariel.
  To be checked in the game.
- EDSM's own ties cannot be used as they are. On 13 Nov it had Daedalus,
  Galileo and Mars High at Earth, and Titan City at Io.
- For a player's own colonies the builder is the better source: its plan
  already says which body each facility is for.
- Some moons and all pairs of planets come with no parent. The page hangs a
  moon from the planet its name points to, and a pair from the nearest star,
  and marks both.

Two more things the owner asked of it, the same evening: "It's should also
show a numerical indicator of how many ground slots the body has" and
"indicate what type each ring is ice/rocky etc". The page now does both.

- **Ring kinds.** Our data holds four, for rings and belts alike: icy,
  rocky, metal rich, metallic. Each ring is its own band in its kind's
  colour, and the kind is written under the planet's name.
- **Ground slots.** The game publishes no slot counts. The owner: "We use an
  algorithm to predict surface slots". That is the builder's rule
  (`validated-slot-v1`, `apps/api/src/ingest/slot_prediction.py`); the page
  reads its answer from `GET /api/v1/plan/systems/<id>/bodies`. The owner
  recalls it as about 99.6 % accurate; neither repo records a figure, and
  the code's own note reads "only 2 true mismatches" against its evidence.
- **It predicts for few bodies today.** The rule refuses to guess when a
  fact is missing. In the six trial systems, 121 bodies can be landed on;
  the site predicts for 8. For the other 113 our data has no atmosphere at
  all, and every one of the 8 has a thin atmosphere. So a body with no
  atmosphere, the commonest kind to build on, gets no prediction. It limits
  the builder in the same way.
- **The cause is found (13 November).** Spansh leaves the atmosphere kind
  empty for a body with no atmosphere, and our import copies that
  faithfully. Read from Spansh's own records (`/api/dump/<id>`) for the six
  systems: 8 of the 121 name an atmosphere; 106 give no kind and a surface
  pressure of exactly 0; 7 give no kind and a pressure above 0. Our database
  keeps the pressure (`surface_pressure_atm`), but the builder's route does
  not read it.
- **Cross-checked against EDSM (13 November).** The owner asked: "Is there
  any other sources we can cross check with?" EDSM is built separately from
  Spansh and writes "No atmosphere" out in words
  (`https://www.edsm.net/api-system-v1/bodies?systemName=<name>`). Of the 106
  bodies with no kind and a pressure of exactly 0, EDSM holds 99 and calls
  every one "No atmosphere"; the other 7 are Polaris, which EDSM has no
  bodies for. The 8 named atmospheres carry the same name in both.
- **The line is at 100 pascals (13 November).** The owner said yes to the
  fix, and: "I want us to work on cross checking and fixing this fully". So
  the question was put to the whole galaxy, through Spansh's body search.
  Among bodies that can be landed on, the highest pressure with no atmosphere
  name is 99.99999 pascals, and the lowest with a name is 100.00002. EDSM
  agrees on the 96 it holds of the 120 bodies nearest the line: all 42 below
  it are "No atmosphere", all 54 at or above it are named, and both sources
  give the same pressure for every one. The 7 trace-pressure bodies above are
  simply under the line.
- **Only for a body that can be landed on.** Spansh leaves the kind empty
  for others too: Earth (EDSM: "Suitable for water-based life"), and gas and
  water giants, some at enormous pressures. For those, empty stays "not
  known".
- **A second fault found on the way: the pressure's unit.** Spansh's file
  gives surface pressure in atmospheres (Earth: 0.999). Our import took it
  for pascals and divided by 101,325, so every stored pressure is 101,325
  times too small (Earth: 0.00000986). Nothing showed it, because no page
  displays the pressure. The stored numbers are left as they are: every row
  has the same mistake, the nightly update writes it the same way, and
  putting it right in the database would mean rewriting a large share of the
  biggest table. It is undone where the number is read, in one place:
  `shared_contracts/surface_pressure.py`, which also says what must change
  together if the import is ever corrected. Rewriting the stored rows is a
  possible later step, as reviewed SQL after a fresh backup; nothing needs
  it.
- **The fix, made.** In the builder's list of bodies, a body that can be
  landed on, with no atmosphere kind and a known pressure under 100 pascals,
  is "No atmosphere", for the slot rule and in the answer. Look up's body
  list gives the pressure in true atmospheres. No change to the database. On
  the twelve real test systems predictions go from 19 to 191 of the 191
  bodies that can be landed on; worked out from the live site's own answers
  for the six trial systems, from 8 to 121 of 121.
- **When it shows.** On the Pi, from the next deploy. The live site runs the
  old server's code until switch-over, so the System Map trial page, which
  reads the live site, keeps showing few numbers until then.
- **Still to do: check the predictions against the game.** The rule's
  answers are predictions. The owner sent the game's own count for every
  body of one system (the System Architect view shows "used/total" beside
  each body); with the system's name, our numbers can be put beside the
  game's. One thing to watch: where our data has no signal count for a body,
  the rule quietly gives no bonus for geology or life, so it can come out
  one low.
- **Sites and slots are not the same count.** In LHS 3447 our data holds
  more sites on the ground than the slots predicted on five of the six
  bodies (5 against 4; 4 against 3). The page shows the two numbers side by
  side and never as "5 of 4". Why they differ is not checked.
- A player's own count, read in the game, would beat a prediction. The
  builder already has a place to type it in. On the real site the map could
  read it from there, as it would read which body each facility is at.

### EDDN (owner asked, 13 November)

"Do we still have the EDDN nightly import?" There is no nightly EDDN job:
EDDN is a live feed. What the new repo holds:

- The nightly jobs on the Pi take Spansh's files, not EDDN.
- A listener built into the API (`apps/api/src/ingest/eddn_client.py`) takes
  body scans from EDDN. The Pi's `deploy/compose.yml` switches it off: no
  background writer starts with the API until it is wanted.
- The old listener that took everything (`apps/eddn`) is not in this repo.
  The live site's `GET /api/events/recent`, which read its table, answers
  with an error (500) today.
- `apps/eddn_v3` (station sightings) is code only and was never deployed.

Nothing here is needed for the move. The atmosphere fix above does not need
EDDN.

### What the galaxy map is for (owner, 13 November)

This replaces every earlier idea of the map in these notes. The owner's own
words:

> I think I've previously been way to ambitious re the map I'd like the map
> to be working in tandem with the finder and also keeping track of all a
> players colonies if it can show all the stars in range (real stars) It
> would be good if we were able to show nearby system supplying needed
> materials for builds etc like raven does but show them on the map also

> Also if a player can select a system in finder they could select show me on
> map

He did not say the map should show Find's results, and it is not built to.

The map's roadmap, in this order:

1. **Show on map.** A system chosen in Find has a "show me on map" button.
   The map flies to it, shows every real star within 15 ly of it in
   star-class colours, and draws the 15 ly range. 15 ly is the claim
   distance (owner, 13 Nov: "yes I think", having asked whether 250 ly would
   be easier). Everything it needs is already held. Measured from the live
   site on 13 Nov: 51 real stars within 15 ly of Sol, 106 within 15 ly of
   Colonia. 250 ly is not easier: the 1,000 ly cube around Sol holds 922,417
   systems, so a 250 ly ball holds some 60,000 or more, which is over
   Almanac's limit and one bright ball from any distance. 250 ly stays what
   it was, the distance Find searches.
   First in the trial page, then on the site.
   (13 Nov, later) The trial page does this now, for Sol, Colonia and the
   first ten results Find lists near each: between 8 and 153 real stars in
   range of each of those 22 systems.
   **15 ly, with one loose end.** The owner, asked: "15 ly as far as I'm
   aware". The players' forum posts say the same, raised from 10. Our own
   code says 16 (`COLONISATION_CLAIM_RANGE_LY = 16.0` in
   `apps/api/src/regional/regional_analysis.py`), with no note of why. The
   map draws 15. Still to do: see what that 16 feeds before changing it.
   The number of stars in range is whatever the place holds. Counted from
   the live site on 13 Nov: 50 around Sol, 105 around Colonia, 8 to 153
   around the trial's systems, and 2,468 around Sagittarius A*, the most
   crowded place there is. That last one has been counted, not drawn; it
   would look crowded, and nobody colonises there today.
2. **A player's colonies.** The builder keeps the list of a player's
   colonies, as Raven Colonial's "nexus" does (the owner's model), and the map
   reads it from there. Today the builder keeps plans in the browser and has
   no such list, so the list is builder work and comes first; the map's part
   is then small.
   The owner, the same evening: "I'd love a player to be able to see where
   all his colonies are located on the map also". So the map shows all of
   them at once from far out, and flies in to one when it is picked.
   What Raven's nexus is, from its own About page and the sample the owner
   showed: "a plan for many systems", a chain or simply some systems to build
   in, made from the plans and build projects already saved for each system.
   It lists the systems in order with the distance of each hop, marks each
   as complete or part built, totals the cargo still to haul, lets fleet
   carriers and commanders be linked to it, and has its own "Show on map"
   button. So it holds systems that are planned as well as built. The
   carriers and cargo belong with step 3.
   The owner agrees our list should hold planned systems as well as built
   ones, "maybe as a toggle".
   (13 Nov, later) The trial page has a first cut: "All my systems" shows
   every system of the player's list with its 15 ly around it, and each has
   a SHOW ON MAP button. Built, being built and planned are three groups, so
   Almanac's own switches are the toggle. In that view a marker's colour is
   its status, not its star class, because on Almanac a marker takes the
   colour of its group; the owner has not been asked whether that suits him.
   The trial reads the list from a file kept out of the repo. Three systems
   of the owner's own were used to try it; they are his, so they are not
   named here.
3. **Build materials.** Show on the map the nearby systems that supply what
   a build needs, as Raven does. The owner is content for this to wait until
   after the move "as long as it's roadmapped". It is its own piece of work,
   and needs two things not held today: what each build needs, and what
   nearby stations sell. We hold each station's type, economy and services,
   not its goods or stock. To check first: whether the Spansh files already
   downloaded each night carry station markets.

Claude's reading of what this leaves out, to be put back only if the owner
asks: thousands of markers on the map at once, inhabited systems across the
galaxy, a heat layer on the 3D map, a raised marker limit, and a version of
Almanac of our own. None is needed for the three jobs above, and Almanac as
published does them.

(12 Nov) The owner's choice for the map: the galaxy map moves to the Almanac
map, and the System Map stays on Babylon. The plan had this open, to be
decided after a one-day trial, with Babylon going too if Almanac won. The
trial still comes first, now to confirm that Almanac works with our own
colonies and candidates on it.

The map's test set-up comes before the trial, because it is how the two maps
get compared, and it is the same whichever map is used:

- [x] (12 Nov) The web app's own checks and tests run on every pull request
      (the `web` job in `.github/workflows/tests.yml`). Until now only the
      Python tests ran.
- [ ] A recorder for the owner's gaming PC: one command flies a fixed tour of
      the map in a real browser on the real graphics card and saves a video,
      screenshots and frame timings (the plan's Playwright job).
      (12 Nov) Built: `scripts/map-recorder` (its README has the commands).
      The first recording on the gaming PC ran that night: all 30 steps of
      the tour, on the graphics card, against the live site. It showed two
      things the recorder got wrong, both fixed: it lumped requests the page
      drops on purpose in with failures, so its "31 failed requests" said
      nothing, and it did not say whether the map drew with WebGPU or WebGL2.
      (13 Nov) It also could not say why the stars near Sol took 9.5 s, as
      it only wrote down requests that went wrong. It now times every data
      request, notes each change the map reports about itself and each
      freeze, and writes them in order to `timeline.txt`. Still to do: a
      second recording with that in it.
- [ ] Three small hooks in the map, for the tests to use: set the view
      exactly, say where a given system or area is on the screen, and say
      what is drawn and whether it has finished loading. The same three
      whichever map is used.
- [ ] A small real slice of the database for tests, taken from the Pi. It
      comes straight after the first checks: heatmap results cannot be
      trusted without known numbers behind them.

(13 Nov) What the first recording showed about our own map, on the owner's
Radeon RX 6800 XT, in Chrome, drawing with WebGPU, against the server:

- Moving the map is smooth: 60 frames a second while panning, zooming,
  turning and tilting. The hitches come when data arrives: the picture stood
  still for 0.5 s as the page opened, 0.6 s when Enter was pressed on Sol,
  0.5 s when the results arrived and 0.4 s when the stars arrived.
- "Type a system and fly to it" does not look like flying. To Sol from the
  whole galaxy: a 0.6 s freeze, a zoom of under half a second, then an empty
  grid; the stars around Sol came 9.5 s after Enter. To Colonia from near
  Sol: the map goes black from one frame to the next, the marker comes about
  a second later and the stars about 2.8 s after Enter.
- Why the stars are late, from the map's code and a run with no window: the
  star requests wait until every other request has finished (the search, the
  system's details, the watchlist), then go in two rounds of four boxes, and
  nothing is drawn until the second round is in. A box takes 1 to 3 s to
  come back from the server. The Pi is likely to be slower at each link, so
  the box query is the first thing to time there.
- (13 Nov, second recording, with every request timed) The same chain with
  its real times, on a server that had just answered the same questions.
  After Enter on Sol the stars were drawn 4.0 s later. The map did not ask
  for them until 2.6 s after Enter: it waited for the search (0.8 s) and the
  system's details, and the picture stood still three times for about half
  a second each. The two rounds of four boxes then took about 0.6 s each.
  The first recording's 9.5 s was the same chain when the server had not
  been asked before. With nothing else to wait for (Reset view) the stars
  were there in under a second.
- Near Colonia one star box took 6.4 s the first time it was asked. Its
  neighbour had not answered after 6.9 s, and answered in 0.8 s when asked
  again. So a box the server has not read lately can take several seconds
  even there.
- Zooming out asked for the density picture 15 times, once for each notch
  of the wheel, and dropped 14 of them. Whether the server goes on working
  on a request the page has dropped is to be checked on the Pi.
- (13 Nov) On the Pi, first look: the stars near Sol did not come for 38 s.
  The first round of star boxes took 2.2 to 3.8 s each. The second round
  had no answer after 14.4 s, and the database's log shows 22 star box
  queries stopped at the app's 15 s limit. So the server does go on working
  on a request the page has dropped, up to that limit.
  The cause was the order of work inside the query. It looked up the star
  class of every system in the box (55,775 in one box near Sol) and only
  then cut to the 5,001 it returns. Timed by itself on the Pi: 4.5 s. With
  the cut first: 1.3 s, and the same rows. Both star queries now cut first:
  the close-up boxes and the wide-view sample (`apps/api/src/routers/map.py`,
  `tests/test_map_star_queries.py`). No change to the database.
  Still open: stopping a query when the page has dropped the request.
- (13 Nov) After that change, recorded again on the Pi: no star box went
  unanswered or was stopped at the limit, and they answered in 0.1 to 3.5 s.
  The stars near Sol came 17 s after Enter. Of those, 10.3 s is Find's own
  search, which the map waits for before it asks for stars; then two rounds
  of boxes, 3.5 s and 1.5 s. The first round is no faster than before: its
  boxes are four times the size, and most of their time goes on finding which
  systems are in the box.
- (13 Nov) Find's search on the Pi. The slow part is one query, the count of
  matching systems: 9.3 s near Sol and 4.9 s near Colonia in that recording.
  Run twice by itself it took 6.5 s, then 0.6 s. It is the same query both
  times; the difference is whether its data is in memory or has to come off
  the drive (about 35,000 reads). Left alone for 13 minutes it was still
  0.7 s. The Pi keeps about 9 GB of data in memory, of a database of about
  1,000 GB, so whatever reads most decides what stays; a star box reads tens
  of thousands of pages.
  Near Sol four in five of the nearest systems are claimed, so the count
  looks at 53,908 systems to find 10,001 free ones. A smaller default
  distance does not shorten that near Sol. A count without the nearest-first
  order was tried and is no better (5.0 s, then 0.5 s): the database read
  the index for the whole cube, 922,417 systems. Not adopted. No change to
  Find for now.
- (13 Nov) Not planned for the current map unless the Almanac trial fails:
  asking for stars without waiting for Find, smaller first-round boxes, the
  half-second freezes.
- The whole map turns teal when the mouse rests on it at close zoom: the
  region under the mouse is highlighted, and up close one region fills the
  view.
- The heading reads "SYSTEM 10477373803" until the name arrives. Reset view
  goes to Sol close up and tilted, though the page opens on the whole galaxy
  from above. The nearest result to Sol is a system named "Test", 1 ly away,
  with no bodies.

These are what the Almanac trial is compared against.

(13 Nov) Almanac's Galaxy-Map, read and measured before the trial:

- It takes at most 50,000 systems at a time, across all groups. That is one
  number in its source, chosen by its author from his own timings. With the
  number raised in a scratch copy it took a million. The work done for every
  system on every frame grows in step: comfortable to about 200,000.
- On the owner's graphics card (Radeon RX 6800 XT, Chrome, a map area of
  1374 by 813), unchanged Almanac drew a frame in 3.4 to 4.4 ms with 50,000
  markers packed around Sol, against 1.7 to 3.3 ms with none. Loading 50,000
  took 45 to 50 ms, and finding the system under the mouse 0.6 ms. Above
  50,000 is not measured.
- It has no heat layer. Its grid is drawn by working out, for each pixel,
  the point of the galactic plane it shows, so a heat layer could be added
  the same way. Not built.
- It handles touch. It will not draw without a real graphics card, so its
  own browser tests run only on its author's machine, as ours do.
- It never asks a server for stars. Our own points would be loaded by us:
  whole for small sets, by area for large ones, and as a picture for
  "everything" views.
- Any change of ours would live in a fork. Its licence allows that for
  non-commercial use. None is needed to start the trial.

(13 Nov) The trial page, `scripts/almanac-trial`: Almanac as published
(0.7.1), with the first 10,000 candidates Find lists within 500 ly of Sol and
of Colonia. What building it showed, before anyone looked at it on a real
graphics card:

- Almanac already has most of what a page around it needs: a chooser between
  sets of systems, search by name, groups with counts that switch on and off,
  and a panel about one system that takes our own values and buttons. The
  trial page has no controls of its own.
- All 10,000 candidates near Sol have the same score (100), tier (S) and best
  use (flexible); 9,999 of the 10,000 near Colonia too. Colour by score or
  tier would show one colour. The trial groups by second use instead. Find's
  ranking near Sol is a separate matter to look at.
- Our data goes in as it comes from Find: all 10,001 records of a set were
  taken, none turned down.
- The published package needs a bundler, and its build needs one fix: a
  background worker asks for a picture the bundler does not copy. The trial's
  build copies it.

(13 Nov) The trial page on the owner's graphics card (Radeon RX 6800 XT,
Chrome, a map area of 1374 by 813, the window in view):

- It started and drew both sets with no errors. The owner: "smooth as
  butter".
- A frame took about 5 ms whether the map held no markers, the 10,001 of one
  set, or 50,000: looking at Sol from 1,600 ly, 5.2, 5.0 to 5.5 and 5.1 ms;
  from 500 ly, 5.0, 5.0 and 5.2 ms; from 60 ly, 4.7, 4.0 to 5.1 and 5.3 ms.
  Markers cost nothing measurable up to Almanac's limit.
- Loading a set took 42 ms. Finding the system under the mouse took 0.1 ms
  with 10,001 markers and 0.5 ms with 50,000.
- The empty map measured about 5 ms here against 1.7 to 3.3 ms on Almanac's
  own demo that morning. The cause is not known (the window was hidden then).
- Not measured: frame pacing while the map moves under the hand.

(13 Nov) What the owner said after looking at it, in his words:

- "It looks smooth as butter really impresed my only complaint is the big
  ball of coloured dots isn't very intuitive". "It looks jarring compared to
  the rest of the map".
- "Do we need to colour all the stars like that I thought colours were for
  star class?"
- "Remember people are not going to be finding a system via the map".

(13 Nov) A wrong turn, recorded so it is not repeated. From the last of
those, Claude concluded that the map should mark Find's results, built the
second version of the trial page around that (#42), and wrote it up as the
owner's decision. He had said no such thing: "when did I ever say I wanted
the map to display results?" What the map should show is the owner's call
and is not decided. The second version stays in the repo as a trial page
only; it was not put on the owner's PC.

What that second version did show, whatever the map ends up carrying:

- Stars in star-class colours, by our own map's rule read from the web app,
  look right close up.
- Seen from far out, ten thousand of Almanac's markers are one bright ball in
  any colours. Almanac's markers are sized for places of interest, about
  12 px across at these distances, and do not shrink to points as stars do.
  The page draws them only from 400 ly or closer.

(13 Nov) A second trial page, made in another chat and reviewed here. It
shows inhabited systems within 45 ly of Colonia, the empty systems within
15 ly of them, and the 15 ly claim area as one outlined shape, on EDSM
sample data, with its own panels and jump buttons.

- Worth taking: that picture (where people live and what can be claimed
  around them), opening on the whole galaxy with jump buttons, and showing
  the notice Almanac's licence asks for. Our trial page lacked the notice;
  it has it now.
- Not worth taking: the data (EDSM's, without our claim rule or scores); its
  own hand build of Almanac with the maths library replaced; stars fetched
  live from EDSM by the browser; and the claim area painted over the map by
  working out Almanac's camera a second time, one circle per inhabited
  system on every frame. That last one is the first real case for a change
  of ours to Almanac.

(12 Nov) How a map check earns trust. The owner's brief: do not overcomplicate
it, but it must test everything fully and give results he can have confidence
in, heatmaps above all. These four rules are how, and they are the same for
stars, markers, routes and heatmaps:

1. Known data goes in: the small real slice, where the numbers are known.
2. Known view: the test sets the camera exactly and waits for the map to say
   it has settled and loaded. No timed waits.
3. The screen is checked against the data. For a heatmap: the densest area
   in the data is the hottest on screen, an empty area is dark, and the
   order of the colours follows the order of the numbers.
4. Every check is seen to fail once, by breaking what it tests on purpose,
   before it is trusted.

Whole pictures are not compared dot for dot against saved "correct" ones:
that goes red for a different graphics card or driver, and teaches everyone
to ignore red. Pictures side by side are for the owner's eyes; pass and fail
come from the checks above.

## Every table on the current server

From the server's own list, 11 November 2026: 130 tables, 1,015 GB, all of
them ordinary tables. Sizes include each table's indexes.

**The rule.** Every table moves whole, with four exceptions:

1. The five big score tables move only for systems within 500 ly.
2. `body_parent_evidence` exists on the Pi but stays empty.
3. `r1_meta` and `r1_plan` are not moved.

(12 Nov) The old test galaxy `v3_gen_phase4b_a` moves after all: the list of
galaxies still names it, so leaving it would leave that list wrong.

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

### Not moved (10 tables, under 1 MB)

- `r1_meta` (5 tables) and `r1_plan` (5 tables): left from an earlier
  programme. Nothing in this repo's code mentions them.

`v3_gen_phase4b_a` (14 tables, 0.3 MB), an old test galaxy, was listed here
until 12 November. The list of galaxies in `v3_meta.canonical_generation`
still names it (migration 027 counted two galaxies), so it moves whole.

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
