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
| `assets` | Asset provenance records. |

`docker-compose.yml` and `env.example` are copied unchanged from the old
server. They get cut down for the Pi during the move.

## Checking the web app

Run these in `apps/web`:

```bash
pnpm install --frozen-lockfile
pnpm check
pnpm test
pnpm build
```
