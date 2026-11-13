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

- [ ] (11 Nov) The API's settings file, written by hand on the Pi: the
      database address, the Frontier client id and secret, the site's
      address, the owner token. Nothing in it protects stored data, so new
      values lose nothing. (12 Nov) Simpler than by hand:
      `scripts/pi/make_api_env.py` takes the server's file down the Pi's key
      and writes `/etc/edfinder/api.env` (root only) with the Pi's own
      database and cache addresses; the read-only database address is left
      out (the Pi has no such login). Nothing is shown on the screen.
- [ ] (12 Nov) Redis for the API's cache: `deploy/pi/redis.yml`. A cache only,
      never saved to disk.
- [ ] `deploy.conf` beside the deploy tool with the Pi's own settings: network
      name, API settings file, and the psql command for the state refresh.
      (11 Nov) Also the galaxy update's: the database container's name, where
      downloads go, and scoring switched on. (12 Nov) `deploy/pi/deploy.conf`:
      the network, the settings file, Cloudflare's header for the visitor's
      address, downloads on the 2 TB drive, and no public check until the
      switch. Everything else is the same name as on the server, so the
      defaults fit. Scoring stays off until reach is kept up to date
      (section 3).
- [ ] `EDFINDER_FIND_CHECK=0` until the Finder data is in place, then remove
      that line. (12 Nov) Not needed: the copy brings the Finder data.
- [ ] `python3 edfinder_deploy.py live main` and try Find, Look up and Plan on
      the Pi's own address.
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

- [ ] Galaxy update (`edfinder-galaxy-update.timer`), choosing its file as in
      section 3.
- [ ] System state refresh (`edfinder-state-refresh.timer`).
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
- The whole map turns teal when the mouse rests on it at close zoom: the
  region under the mouse is highlighted, and up close one region fills the
  view.
- The heading reads "SYSTEM 10477373803" until the name arrives. Reset view
  goes to Sol close up and tilted, though the page opens on the whole galaxy
  from above. The nearest result to Sol is a system named "Test", 1 ly away,
  with no bodies.

These are what the Almanac trial is compared against.

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
