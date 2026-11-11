# Moving ED-Finder to the Raspberry Pi

A checklist, in order. Each step gets its exact commands when we reach it;
this page says what happens and why. Numbers were measured on the current
server on 9 November 2026 unless a line says otherwise.

Reviewed against the plan and the code on 11 November. What that review added
is marked "(11 Nov)".

## What moves, and what stays behind

| | Size now | On the Pi |
|---|---|---|
| Raw galaxy data (every system and body we know of) | 574 GB | All of it, except the 116 GB "body parent evidence" table, which the app never reads and the importer no longer writes. Each body's class is added to it (see section 3) |
| Scores and Finder tables | 426 GB for all 198.5 million systems | Only the 37 million systems within 500 ly of an inhabited system |
| Density map data | 14 GB | All of it |
| System state (population, factions, claims) | under 1 GB | All of it |
| Everything users have written: accounts, journal imports, watchlists, notes, synced profiles | small | All of it. Copied twice: with everything else, and again at switch-over (section 7) |
| Leftovers from old builds (`r1_plan`, `r1_meta`, `v3_gen_phase4b_a`, build-progress tables) | | Nothing |

Estimate for the 2 TB drive: 550 to 630 GB, plus about 10 GB for the body
classes.

This table is by group. The app also keeps user data in older tables outside
the main groups (`system_notes` and `profile_sync` are two), so before the
copy script is written every table on the server is listed and marked move,
cut down or leave (11 Nov).

**One band, not two.** Everything out to 500 ly is scored and kept up to date.
Find shows 250 ly by default and can be widened to 500. Nothing is scored "on
request". *Recommended on 10 November and not yet confirmed by the owner, who
agreed 250 ly every night and 500 ly on request the day before (11 Nov).*

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
  1.3 GB for the galaxy changes) take a few minutes. The one-off copy, perhaps
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
      itself every night since.
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
      two files a day apart may leave a gap between them. The nightly job
      chooses the file by how long ago it last ran (one-day, 7-day or
      32-day), and runs the 7-day file once a week as a sweep.
- [ ] **One catch-up pass** over Spansh's full galaxy file (118.9 GB) with the
      same code, because no smaller file reaches back to 27 August. Timed on
      the first million systems before the full run.
- [ ] **Reach.** How far each system is from inhabited space, kept up to date
      as colonies appear. Gives Find its 250 ly default and 500 ly limit, and
      tells the copy which systems to take. When a colony brings systems into
      range, they go on the waiting list to be scored.
- [ ] (11 Nov) **The table list.** Every table on the current server, each
      marked move, cut down or leave.
- [ ] **A copy script** that exports the tables above from the current server,
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
- [ ] (11 Nov) Apply Spansh's 32-day file once, to bring in what changed
      between taking the copy and the Pi's first nightly run.

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

## Not part of the move

The map (Almanac trial), bridge routes, BGS and the BGS-Tally link come after
Find and Plan work on the Pi.
