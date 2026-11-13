# ED-Finder (reborn)

A colonisation helper for Elite Dangerous, built for one invite-only group of
10–20 commanders (usually 2–3 online at once), served from a Raspberry Pi at
home.

The journey is **Find → Look up → Plan → Haul**, with BGS alongside and the
galaxy map underneath. The full plan is in
[`docs/plan-2026-11-08.pdf`](docs/plan-2026-11-08.pdf).

## Where this came from

This repo was copied from the parts of
[`ed-finder`](https://github.com/brianstewart377-rgb/ed-finder) that work, at
commit `8f4d395` (9 November 2026). The old repo is kept read-only as
reference: its history, PRs and research stay there.

## What's here

| Folder | What it is |
|---|---|
| `apps/web` | The app: SvelteKit 2, Svelte 5, TypeScript. Babylon.js draws the maps. |
| `apps/api` | The API: Python 3.14, FastAPI, asyncpg. |
| `apps/importer` | Loads the Spansh galaxy dumps into PostgreSQL. |
| `apps/eddn_v3` | The live EDDN listener (written, not yet switched on). |
| `packages` | Shared TypeScript: the generated API client and planner core. |
| `shared_contracts` | Shared Python used by the API and importer. |
| `sql/v3` | Database migrations. |
| `sql/measure` | Read-only scripts that report on the database. |
| `deploy` | The compose file, the deploy tool, the two nightly jobs and how they report a failure. |
| `tests` | Python tests for the importer and the database loaders. |
| `assets` | Asset provenance records. |

`docker-compose.yml` and `env.example` are copied unchanged from the old
server. They get cut down for the Pi during the move.

## System state: population, factions and claims

The `v3_state` tables hold the latest picture of every system that has a
population or a station: population, government, economy, factions, Powerplay
and which kinds of station are there (including colonisation construction
sites). Find uses them to tell inhabited, being-colonised and free systems
apart. They are a snapshot, not a history.

- Tables and the merge function: `sql/v3/migrations/024_v3_system_state.sql`
- Loader: `apps/importer/src/spansh_system_state.py` (Python standard library
  only). It reads Spansh's `galaxy_stations.json.gz` and writes a psql script:

```bash
python3 apps/importer/src/spansh_system_state.py --snapshot galaxy_stations.json.gz | psql -X -v ON_ERROR_STOP=1 "$DATABASE_URL"
```

A load is one transaction. If the file is cut off or anything fails, nothing
changes. A newer record always wins over an older one.

## Deploying

Every push to `main` builds two images and publishes them to GHCR:
`ghcr.io/brianstewart377-rgb/ed-finder-reborn/api` and `.../web`, tagged
`main` and `git-<commit>`.

On the machine that runs the site, `deploy/edfinder_deploy.py` (Python
standard library only) starts them with `deploy/compose.yml`:

| Command | What it does |
|---|---|
| `check` | Shows what is running and whether the machine is ready. Changes nothing. |
| `try main` | Starts the release on a spare port, tests it, removes it. The live site is not touched. |
| `live main` | Tests the release on the spare port, then switches the site to it. |
| `rollback` | Switches back to the release that was live before. |
| `restore-old` | Stops this app and restarts the containers it first replaced. |
| `status` | Shows what is live. |

The tool has to be run as root. On the Raspberry Pi, where you log in as
`pi`, put `sudo` in front, for example
`sudo python3 /opt/edfinder/edfinder_deploy.py check`. Run without it, the
tool says so and changes nothing.

If a release fails its checks after the switch, the previous one is put back
automatically. Settings (network, ports, the API settings file) have defaults
for the current server and can be changed in a `deploy.conf` file beside the
script; see the top of the script.

Before a release goes live the tool also runs three real Find searches against
it. On a machine whose database has no Finder data yet, put
`EDFINDER_FIND_CHECK=0` in `deploy.conf`.

## Keeping the system state fresh

Find hides inhabited systems and systems being claimed. It knows which those
are from the `v3_state` tables, which are only as fresh as their last load.
`deploy/state_refresh.py` (Python standard library only) keeps them fresh:

| Command | What it does |
|---|---|
| `run` | Asks Spansh what is new, downloads the changed files (about 9.3 GB for both), and loads them. If nothing is new it does nothing. If anything fails, the database is left as it was. |
| `status` | Shows the last load and the files on disk. |

`deploy/edfinder-state-refresh.timer` runs it every night at 03:30 UTC. It
needs `spansh_system_state.py` beside it and about 20 GB of free disk space.

## Keeping the galaxy fresh

The galaxy itself (systems, bodies, rings, stations) changes too: commanders
scan new bodies, fleet carriers move. Every night Spansh publishes every system
that changed in the last 7 days as one file (`galaxy_7days.json.gz`, about
3 GB). `deploy/galaxy_update.py` (Python standard library only) fetches it and
applies it:

| Command | What it does |
|---|---|
| `check` | Downloads the file and says what would change. Read only: the database is not touched. |
| `run` | Downloads the file and applies it. If Spansh has nothing new there is nothing to apply. With scoring switched on, it then gives new scores to the systems on the waiting list, either way. |
| `status` | Shows the last update, how many systems are waiting for new scores, whether scoring is switched on, which file each run takes, and the file on disk. |
| `check-scores` | Works scores out again for the systems of the file on disk, from Spansh's records and from our own rows, and compares them with the stored ones. Read only. |
| `score` | Gives new scores to the systems on the waiting list. Needs no file. |
| `reach` | Works out afresh which systems are within 500 ly and 250 ly of an inhabited system. Needs migration `028` (the Pi). |

Which file:

- Each night the 7-day file, not Spansh's one-day file. Spansh makes its files
  at a different time each night (02:10 UTC on 10 November, 03:20 UTC on
  11 November), so two one-day files can leave a gap between them, and a night
  the update does not run would lose that day for good. The 7-day file reaches
  back over the last six nights too. What we already have is left alone, so
  only what really changed is written and scored.
- The launcher chooses by how long ago the file of the last finished update
  was made. The database records that (`v3_source.source_run.effective_at`),
  so the choice still works after the database is copied to another machine.
  6 days or less: the 7-day file. 7 to 31 days: the 32-day file
  (`galaxy_1month.json.gz`, about 6 GB). More: it stops and says so, because
  only the full galaxy file reaches back that far. A day is kept spare in both,
  because when exactly a file's days begin is Spansh's to say.
- `EDFINDER_GALAXY_URL` gives one fixed file instead, for the full galaxy file
  or for trying something out.
- After a successful run only the file just applied is kept on disk.

How it works:

- The work is done by `python -m spansh_delta.apply`
  (`apps/importer/src/spansh_delta/apply.py`), which ships inside the API
  image. The launcher runs it in a short-lived container made from the image
  the live API is running, so the update is always the same version as the
  app. The container shares the database container's network and connects as
  a local client: no password is stored or passed anywhere. If the database
  refuses that, the container is started on the app's network with the API's
  own settings file instead; the launcher hands docker the file's path and
  never opens it.
- Each system's rows are worked out by the same adapter the full import used
  and compared with what we hold. Identical systems are not touched.
- A system that differs has its rows replaced and is added to
  `v3_state.rescore_queue`, in the same transaction, so its scores and Find
  row get rebuilt.
- A body, ring or station the file no longer lists is kept and marked
  `RETIRED`, never deleted. Every reader already skips rows that are not
  `ACTIVE`.
- It is safe to interrupt and safe to run twice.

Scores are worked out from a system's rows in the galaxy tables, by the frozen
scoring code (`scripts/ratings_v4`, `scripts/v3_system_*`, `shared_contracts`).
One thing they use is each body's class ("Earth-like world", "Icy body":
Spansh's sub-type). The galaxy tables hold it in `bodies.body_class` since
migration `027`, and the galaxy update writes it with every row it writes. So
a system the update has changed can be scored from the database alone, at any
time: no Spansh file has to be at hand.

A body row written before migration `027` has no class of its own: the column
is empty. Its class is in its score row
(`v3_derived.body_mechanics.body_class`), which holds the same thing. The
galaxy update compares by that, so it does not rewrite a system just to fill
the column in. The copy to the Raspberry Pi fills every row.

`score` (`apps/importer/src/spansh_delta/scores.py`) works through the waiting
list. A system waiting for new scores gets its score tables replaced where
they differ and its Find row rebuilt, and leaves the list, all in one
transaction. A system that only gained or lost a station has its Find row
rebuilt. A system that cannot be scored stays on the list, and the report says
why. With `EDFINDER_GALAXY_SCORES=on` in `deploy.conf`, `run` does the scoring
itself every time, whether or not Spansh had anything new, so a night when the
scoring fails is made up by the next run.

One reason a system cannot be scored is "the class of one of its bodies is not
in the database". That is a system which a galaxy update from before migration
`027` rewrote, so its rows have no classes, and which was not scored before
the new code arrived. Its old score rows are not used for the classes, because
a body's class may be the very thing that changed. The next galaxy update that
lists the system writes its classes, and it is scored then.

On the Raspberry Pi the scores are kept only for systems within 500 ly of an
inhabited system ("reach"; migration `028`, `apps/importer/src/spansh_delta/reach.py`).
`v3_state.system_reach` lists those systems and says which are within 250 ly
too. Where that table exists, the scoring:

- brings reach up to date first, and will not start until it has been worked
  out once (`galaxy_update.py reach`; on the Pi it takes a while);
- works it out afresh once a week, from the systems inhabited at the time
  (`v3_state.system_state`), and in between measures the systems the galaxy
  update has just found against the same inhabited systems;
- takes systems further than 500 ly off the waiting list unscored;
- after the waiting list, scores any system in reach that has no scores yet.

So a new colony counts within a week. The systems it brings within 500 ly lie
250 to 500 ly from it, far from where anyone colonises next. Working reach out
afresh stops, changing nothing, if the inhabited systems or the systems in
reach have dropped by more than a tenth since last time: colonies do not
vanish, so that would mean the system state is damaged. Without migration
`028` (the current server) the scoring takes every system on the waiting
list, as before.

`check-scores` writes nothing. For the systems of the file on disk whose
records say what our rows say, it reports three things: whether systems
nothing has changed get exactly their stored scores and Find row back; whether
scoring from our own rows gives the same as scoring from Spansh's records; and
whether each system waiting for new scores can be scored. Its line "only the
record's stamp differs" counts systems whose scores are identical and whose
stamp is not. The stamp says which record the scores were made from (a
fingerprint of it, and the date of a body's class). It differs whenever the
stored scores were made from our own rows, or Spansh has re-dated a body. It
is not a score.

`deploy/edfinder-galaxy-update.timer` runs it at 04:30 UTC, after Spansh
publishes the file (between 02:00 and 03:30 UTC on the days we have looked),
and again at 16:30 UTC in case the file was late or the first run failed. It
needs `state_refresh.py` beside it (they share the download code) and
migrations `026`, `027` and `029` applied. Apply `027` before deploying a build that
has it: until the column is there the update refuses to run and says so. The
same goes for `029`, which adds a signal type (a planet's mining locations) to
the database's list: apply it first, then deploy the build that knows the word. The
app itself does not mind either way.

The move to the Raspberry Pi is in [`docs/pi-move.md`](docs/pi-move.md).

## Hearing when a nightly job fails

A nightly job that fails, or does not run at all, would otherwise tell nobody.
`deploy/report_in.py` (Python standard library only) tells
[healthchecks.io](https://healthchecks.io) how each run ended, and
healthchecks.io emails the owner:

- when a job says it failed. The last lines the job printed are sent with
  that, so the reason is kept with the failure on healthchecks.io;
- when a job has said nothing for longer than its check allows: the machine
  is off, has no internet, or the job is stuck.

systemd runs it when a job ends, whichever way the job ended (the
`ExecStopPost=` line in `edfinder-galaxy-update.service` and
`edfinder-state-refresh.service`). It cannot make the job itself fail. It
belongs beside the other scripts, as `/opt/edfinder/report_in.py`; if it is
not there, the jobs run as before and nothing is reported.

Each job has its own check on healthchecks.io, and each check has its own web
address. The address is a secret, because anyone who has it can report "all is
well". It is kept in a file that only root can read, and is never printed:

| Job | The file with its address | The check's period and grace time |
|---|---|---|
| Galaxy update (04:30 and 16:30 UTC) | `/etc/edfinder/report/galaxy-update.url` | 12 hours, then 6 hours |
| System state refresh (03:30 UTC) | `/etc/edfinder/report/state-refresh.url` | 1 day, then 3 hours |

A job reports when it ends, not when it starts, so the grace time has to
cover the longest a run normally takes. Until a job's file exists, nothing is
reported and the job's log says so.

To set one up:

1. On healthchecks.io, add a check for the job with the period and grace time
   above, and copy its address (it starts `https://hc-ping.com/`).
2. On the machine, make the folder, then save the address in the job's file.
   The second command waits for you to paste the address; press Enter, then
   Ctrl+D. Done this way the address is not left in the shell's history.

   ```bash
   sudo install -d -m 700 /etc/edfinder/report
   sudo sh -c 'umask 077; cat > /etc/edfinder/report/galaxy-update.url'
   ```

3. Try it. This sends one "it worked" and says whether healthchecks.io took it:

   ```bash
   sudo python3 /opt/edfinder/report_in.py galaxy-update --test
   ```

Two things to know:

- Only runs started by systemd report (the timers, or
  `sudo systemctl start edfinder-galaxy-update.service`). A run started by
  hand with `python3 galaxy_update.py run` does not.
- A timed run that finds a run by hand still going stops with "another galaxy
  update is already running", and that is reported as a failure. Switch the
  timers off for the length of a long run by hand, such as the catch-up.

## Running the Python tests

```bash
python -m pip install -r tests/requirements-ci.txt
python -m pytest -q tests
```

The database tests need `SYSTEM_STATE_TEST_DSN` pointing at a throwaway
PostgreSQL database whose name ends in `_test`. CI runs them on PostgreSQL 18.

## Checking the web app

Run these in `apps/web`:

```bash
pnpm install --frozen-lockfile
pnpm check
pnpm test
pnpm build
```

Every pull request runs them too, with the lint and formatting checks and the
shared packages' own checks (the `web` job in `.github/workflows/tests.yml`).

None of that can say how the map looks or how smoothly it moves: CI has no
graphics card. `scripts/map-recorder` records a fixed tour of the map in a
real browser on a real graphics card (the owner's gaming PC) and saves a
video, pictures and frame timings. Its own README has the commands.
