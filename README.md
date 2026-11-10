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
| `deploy` | The compose file, the deploy tool and the two nightly jobs. |
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
scan new bodies, fleet carriers move. Spansh publishes every system that
changed in the last day as one file, about 1.2 GB. `deploy/galaxy_update.py`
(Python standard library only) fetches it and applies it:

| Command | What it does |
|---|---|
| `check` | Downloads the file and says what would change. Read only: the database is not touched. |
| `run` | Downloads the file and applies it. If Spansh has nothing new it does nothing. |
| `status` | Shows the last update, how many systems are waiting for new scores, and the file on disk. |

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

`deploy/edfinder-galaxy-update.timer` runs it at 04:30 UTC, after Spansh
publishes the file at about 02:10 UTC, and again at 16:30 UTC in case the file
was late or the first run failed. It
needs `state_refresh.py` beside it (they share the download code) and
migration `026` applied.

The move to the Raspberry Pi is in [`docs/pi-move.md`](docs/pi-move.md).

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
