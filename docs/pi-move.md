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

- [ ] **Scoring speed.** Run `scripts/dev/score_speed_test.py` on the current
      server and on the Pi and compare the two results. The old server's real
      builds did about 36,000 systems a minute per worker. If the Pi is 5 to 10
      times slower, a night's changes (about 116,000 systems in Spansh's
      one-day file on 9 November) take 15 to 30 minutes, and re-scoring all 37
      million from scratch takes one to two and a half days.

Already known:

- **The home line is not a limit.** 1 Gbit fibre to the property, no data cap.
  The nightly downloads (about 9.3 GB for the two state files plus about
  1.3 GB for the galaxy changes) take a few minutes. The one-off copy, perhaps
  150 to 250 GB (a guess until the copy script exists), will be limited by how
  fast the Pi can write.

## 3. Code that has to exist first

- [x] **Images for the Pi's processor.** The published images cover both
      amd64 (the current server) and arm64 (the Pi).
- [ ] **A decision on changing scores in place.** The database refuses any
      change to a published set of scores: its rows are insert-only. That
      suited whole rebuilds; a nightly update of changed systems needs a
      reviewed migration that lets the nightly job, and only it, replace the
      rows of the systems it re-scored.
- [ ] **The scoring code is not in this repo yet.** It is still in the old
      repo (`scripts/ratings_v4/` and the Finder table builders). Copy it
      across with its tests.
- [ ] **A nightly "only what changed" scoring job.** Today's scoring code only
      does whole runs. The job re-scores the systems the night's import
      touched, plus systems that came within 500 ly because a new colony
      appeared.
- [ ] **A copy script** that exports the tables above from the current server,
      with the scored tables cut down to the 37 million systems in reach. A
      logical dump (`pg_dump`), not a copy of the data folder: the two
      machines have different processors.

## 4. The copy

- [ ] Fresh backup of the current database first.
- [ ] PostgreSQL 18 running on the Pi, data on the 2 TB drive.
- [ ] Run the copy script. Check row counts on both sides, table by table.
- [ ] Load the system state on the Pi (`deploy/state_refresh.py run`).

## 5. The app on the Pi

- [ ] `deploy.conf` beside the deploy tool with the Pi's own settings: network
      name, API settings file, and the psql command for the state refresh.
- [ ] `EDFINDER_FIND_CHECK=0` until the Finder data is in place, then remove
      that line.
- [ ] `python3 edfinder_deploy.py live main` and try Find, Look up and Plan on
      the Pi's own address.

## 6. Nightly jobs on the Pi

- [ ] Galaxy import (Spansh's one-day file).
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
