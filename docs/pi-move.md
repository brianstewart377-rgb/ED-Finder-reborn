# Moving ED-Finder to the Raspberry Pi

A checklist, in order. Each step gets its exact commands when we reach it;
this page says what happens and why. Numbers were measured on the current
server on 9 November 2026 unless a line says otherwise.

## What moves, and what stays behind

| | Size now | On the Pi |
|---|---|---|
| Raw galaxy data (every system and body we know of) | 574 GB | All of it, except the 116 GB "body parent evidence" table, which the app never reads and the importer no longer writes |
| Scores and Finder tables | 426 GB for all 198.5 million systems | Only the 37 million systems within 500 ly of an inhabited system |
| Density map data | 14 GB | All of it |
| System state (population, factions, claims) | under 1 GB | All of it |
| Accounts, plans, journals, watchlists | small | All of it |
| Leftovers from old builds (`r1_plan`, `r1_meta`, `v3_gen_phase4b_a`, build-progress tables) | | Nothing |

Estimate for the 2 TB drive: 550 to 630 GB.

**One band, not two.** Everything out to 500 ly is scored and kept up to date.
Find shows 250 ly by default and can be widened to 500. Nothing is scored "on
request".

## Before anything else

- [ ] Pay the current server for one more month. It renews around 15 November,
      and the move needs both machines running side by side.

## 1. The Pi itself (owner)

- [ ] Raspberry Pi OS Lite (64-bit), booting from the 500 GB NVMe.
- [ ] Docker installed.
- [ ] When the 2 TB NVMe arrives: fitted, formatted and mounted as the data
      drive. PostgreSQL's data lives there, nothing else of ours on the boot
      drive.

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
      tables after a few million changed rows.

Already known:

- About 67,000 systems a day are new to us (10 November). The galaxy has
  always grown at about that rate: 198.5 million systems in 12 years is
  46,000 a day. Most are deep space with few scanned bodies.
- Spansh publishes the one-day file at about 02:10 UTC.

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
      67,122 new, 4,938 identical, in 6 min 56 s; none refused.
- [ ] **A nightly re-score** of the systems noted as changed, replacing their
      score rows and Find rows. A system can only be scored while its record
      from Spansh's file is in hand, because the score uses each body's
      sub-type and the galaxy tables do not hold it. So scoring runs on the
      same file as the galaxy update, straight after it. Systems updated
      before scoring was live are scored when a later file holds their
      current record (the catch-up below, or Spansh's one-month file).
      Checked against real data first, read only
      (`deploy/galaxy_update.py check-scores`): systems nothing has changed
      must get exactly their stored scores back.
- [ ] **One catch-up pass** over Spansh's full galaxy file (118.9 GB) with the
      same code, because no smaller file reaches back to 27 August. Timed on
      the first million systems before the full run.
- [ ] **Reach.** How far each system is from inhabited space, kept up to date
      as colonies appear. Gives Find its 250 ly default and 500 ly limit, and
      tells the copy which systems to take.
- [ ] **A copy script** that exports the tables above from the current server,
      with the scored tables cut down to the systems in reach. A logical dump
      (`pg_dump`), not a copy of the data folder: the two machines have
      different processors.

## 4. The copy

- [ ] Fresh backup of the current database first.
- [ ] PostgreSQL 18 running on the Pi, data on the 2 TB drive.
- [ ] Run the copy script. Check row counts on both sides, table by table.
- [ ] Clean-up settings on the galaxy tables (see section 2), then one full
      clean-up and statistics pass over the restored database.
- [ ] Load the system state on the Pi (`deploy/state_refresh.py run`).

## 5. The app on the Pi

- [ ] `deploy.conf` beside the deploy tool with the Pi's own settings: network
      name, API settings file, and the psql command for the state refresh.
- [ ] `EDFINDER_FIND_CHECK=0` until the Finder data is in place, then remove
      that line.
- [ ] `python3 edfinder_deploy.py live main` and try Find, Look up and Plan on
      the Pi's own address.

## 6. Nightly jobs on the Pi

- [ ] Galaxy update from Spansh's one-day file (`edfinder-galaxy-update.timer`).
- [ ] System state refresh (`edfinder-state-refresh.timer`).
- [ ] Re-score what changed.
- [ ] Backup to the USB drives, and one restore test before we rely on it.

## 7. Switch over

- [ ] Cloudflare Tunnel from the Pi; point `ed-finder.app` at it.
- [ ] Keep the old server running, untouched, for one quiet week as the way
      back.

## 8. Before cancelling the old server

- [ ] **Octopus runs on it** (three containers). Move it to Contabo or switch
      it off.
- [ ] The old repo's GitHub workflows that reach the old server are switched
      off.
- [ ] One last full backup, copied to the USB drives.
- [ ] Cancel the server.

## Not part of the move

The map (Almanac trial), bridge routes, BGS and the BGS-Tally link come after
Find and Plan work on the Pi.
