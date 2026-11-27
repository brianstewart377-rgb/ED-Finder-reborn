# ED-Finder: rules for coding agents

## The scale

- 10–20 invite-only users, usually 2–3 at once. One Raspberry Pi 5 at home.
- Build for that. Don't build for 198 million systems or for a public site.
- The whole raw galaxy stays in the database. Scores and Find's facts are
  kept only for the systems within 500 ly of an inhabited system (about 41
  million), and brought up to date every night. Nothing is worked out "on
  request".

## How we work

- **Copy, don't rewrite.** Code here came from the old `ed-finder` repo.
  Improve it one change at a time, through a pull request.
- **Keep the process small.** No governance docs, receipts or sign-off
  ledgers. A pull request needs all of CI green. A Codex review is read and
  answered when one appears, but is not waited for. Once CI is green the
  agent merges its own pull request (the owner's standing instruction).
- **Catch it before pushing.** Run the whole test suite and the linters
  first. A second round of CI for something that could have been caught
  costs the owner an evening.
- **Don't add a changelog file.** Git history and PR descriptions are the
  record. (Every PR editing the same changelog spot caused merge conflicts.)
- **The plan** is `docs/plan-2026-11-08.pdf`. If a change alters the plan,
  say so in the PR. **Where things stand** is `docs/roadmap.md`: when a step
  is finished or the order of work changes, bring it up to date in the same
  PR.

## The database

- The schema stays as it is. A new table, or a change to one, needs the
  owner's agreement to its design first.
- Migrations are reviewed SQL. The owner applies them on the Pi, only after a
  fresh backup.
- **A migration changes the shape of tables and nothing else.** No statement
  in one reads or writes millions of rows. Rows are changed by the nightly
  work, in small batches that can stop and carry on. A change to what a row
  holds gets a new layout name, so that every row is written again that way
  and nothing reads a row written the old way.
- A build and its migration must be safe in either order. Where they cannot
  be, the PR says which comes first and why.

## The Pi

- The agent has no access to the Pi. The owner runs everything there, from
  commands the agent gives him.
- **No heavy one-off job** where the nightly work, in small pieces, can do
  the same thing. (What happened on 25 November 2026 is in
  `docs/pi-move.md`, under migration `033`.)
- One change per restart, so that a Pi that does not come back has one
  possible cause.
- The scripts in `deploy/` run on the Pi as root and use the Python standard
  library only.

## Tests: one job per tool

- **Vitest**: unit and component tests, every push.
- **Cypress**: pass/fail user journeys in CI, with video and screenshots.
- **Playwright**: only map recordings on a real GPU, run on the owner's
  gaming PC.
- A test lives in exactly one of these.
- Fixtures come from real data only. The one exception: rows made up only to
  give a test database the Pi's sizes, to check how PostgreSQL goes about a
  query. Those never check an answer.
- Map checks test meaning (star classes differ, markers show, no empty
  frames), not "some pixels changed".
- Flaky tests are quarantined and fixed, never retried until green.

## Code layout

- `apps/web`: the only frontend. Svelte owns the app; the two map libraries
  (Almanac's Galaxy-Map, Cosmos) only draw.
  Use the generated API client in `apps/web/src/lib/api/`.
- `apps/api`: Python 3.14, FastAPI, asyncpg. Parameterised SQL only.
- `apps/importer`: the nightly work (the galaxy update, the scoring, Find's
  facts and lists). It ships inside the API image.
- Never commit secrets, passwords, tokens, keys or private `.env` files.
- Nothing in the repo, a pull request or a document may tie a star system, a
  commander's name or a network address to the owner or to any other player.
  Real systems may be test data; never say whose they are.

## Talking to the owner

Plain, concrete language, no jargon. He needs this exactness; it is not a
matter of style.

- Anything to run: the exact command, one per block. Before each block say
  what it is (a command to run, an edit to a file, or text not to be run),
  which machine it is for, and what it should print.
- Never "the same as before": give the command again in full.
- A read-only check first, before any step that might already be done.
- No command that has to be ended with Ctrl+C or Ctrl+D. A long job runs in
  the background (`systemd-run`) and is watched with commands that return.
- Fetching a script onto the Pi: the fingerprint check and the swap are one
  command, so the check cannot be skipped.
- Say so plainly when something he suggests will not work, and when you got
  something wrong.
