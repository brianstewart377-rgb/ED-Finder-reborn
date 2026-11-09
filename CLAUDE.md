# ED-Finder: rules for coding agents

## The scale

- 10–20 invite-only users, usually 2–3 at once. One Raspberry Pi at home.
- Build for that. Don't build for 198 million systems or for a public site.
- The whole raw galaxy stays in the database. Scoring happens on demand near
  the colonisation frontier, not for the whole galaxy in advance.

## How we work

- **Copy, don't rewrite.** Code here came from the old `ed-finder` repo.
  Improve it one change at a time, through a pull request.
- **Keep the process small.** No governance docs, receipts or sign-off
  ledgers. A pull request needs green CI and a Codex review; if Codex is out
  of quota, the owner's OK is enough.
- **Don't add a changelog file.** Git history and PR descriptions are the
  record. (Every PR editing the same changelog spot caused merge conflicts.)
- **The plan** is `docs/plan-2026-11-08.pdf`. If a change alters the plan,
  say so in the PR.
- The database schema stays as it is. Migrations are reviewed SQL, applied
  only after a fresh backup.

## Tests: one job per tool

- **Vitest**: unit and component tests, every push.
- **Cypress**: pass/fail user journeys in CI, with video and screenshots.
- **Playwright**: only map recordings on a real GPU, run on the owner's
  gaming PC.
- A test lives in exactly one of these.
- Fixtures come from real data only. Map checks test meaning (star classes
  differ, markers show, no empty frames), not "some pixels changed".
- Flaky tests are quarantined and fixed, never retried until green.

## Code layout

- `apps/web`: the only frontend. Svelte owns the app; Babylon only draws.
  Use the generated API client in `apps/web/src/lib/api/`.
- `apps/api`: Python 3.14, FastAPI, asyncpg. Parameterised SQL only.
- Never commit secrets, passwords, tokens, keys or private `.env` files.

## Talking to the owner

Plain, concrete language. Anything to run: the exact command, one per block,
saying which machine it runs on.
