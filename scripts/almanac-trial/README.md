# The Almanac map trial

A page that shows what a "show me on map" button in Find would do, on
Almanac's galaxy map. It answers one question: does Almanac's map do the job
we want of a map, on a real graphics card, well enough to replace our own?

It is a trial tool, like the map recorder. It is not part of the site and
nothing in `apps/web` uses it. When the trial ends, either way, this folder
goes.

## What it shows

What the map is for is in `docs/pi-move.md` ("What the galaxy map is for"),
in the owner's words. This page is its first step.

- **A system chosen in Find, shown on the map.** The map flies to the system
  and selects it.
- **Every real star in range of it.** All the systems our own data holds
  within 15 light years, each in the colour of its star class by the same
  rule our own map uses (`apps/web/src/lib/spatial/stellar-presentation.ts`,
  read directly, not copied).
- **The range itself**, as a faint ball 15 light years around the system.

The page has no button of its own. Almanac's own list of sets, in the top
bar, stands in for the button: each entry is one system. The trial offers
Sol, Colonia, and the first ten results Find lists within 500 light years of
each.

All of it is real data fetched from the live site: Find's results from
`POST /api/local/search`, and the stars from `GET /api/map/systems`.

- **Almanac's map exactly as published**: the npm package
  `@elite-dangerous-almanac/galaxy-map`, version 0.7.1, with no changes of
  ours. Its licence is PolyForm Noncommercial 1.0.0. None of its code is kept
  in this repository; the build downloads it. The licence asks that anyone
  given a copy also gets its terms and its "Required Notice" line, so the
  built page shows that line and carries the licence files in `licences/`.

Almost everything on the screen is Almanac's own:

| On the screen | Whose it is |
|---|---|
| The list of systems in the top bar | Almanac's; it stands in for Find's button |
| The Filters panel: search by name, the star classes with their counts, on and off | Almanac's |
| The map options: region names, system names, grid, nebulae | Almanac's |
| The panel about one system, and its position, region and star | Almanac's |
| In that panel: what Find said of the chosen system, and how far each star is from it | Ours |
| The OPEN IN INSPECT button, which opens the system on ed-finder.app | Ours |
| The faint ball around the chosen system | Ours, drawn with Almanac's own sphere |

## Looking at it

The map needs a real graphics card. It refuses to start without one and says
so on the page.

On the gaming PC, in PowerShell, once the built page is in the `site` folder:

```
cd C:\Users\brian\ED-Finder-reborn
```

```
node scripts\almanac-trial\serve.mjs
```

It prints `The trial page is at http://localhost:5199`. Open that address in
Chrome. Leave the PowerShell window open while you look; Ctrl+C stops it.

To open on one system, add its id to the address, as a button in Find would:
`http://localhost:5199/?system=3238296097059` opens on Colonia. Only the
systems in the list are held.

## Building the page from nothing

On any computer with Node 22.18 or newer and the internet, in this folder
(`scripts/almanac-trial`). Four commands, in this order.

Download Almanac's map and the bundler:

```
npm ci
```

Fetch the systems and their stars from the live site (about a minute; it asks
about once a second):

```
npm run data
```

Build the page into the `site` folder:

```
npm run build
```

Hand it out on this computer:

```
npm run serve
```

`site/` and `public/data/` are not kept in git: both are made again by these
commands.

## What is in this folder

| File | What it is |
|---|---|
| `index.html`, `src/main.js` | The page. It hands our data to the map and has no controls of its own. |
| `src/logic.mjs` | The page's own rules: which stars are in range of a system, a star's group and colour, the range ball, and what the panel says. |
| `fixtures/` | For the tests, both real and unchanged: the first 60 rows of Find's answer near Sol, and the site's answer for the 30 ly box around Sol. |
| `fetch-data.mjs` | Fetches Find's results and the stars around each system from the live site. |
| `worker-files.mjs`, `vite.config.mjs` | The build. Almanac's background workers ask for one picture the bundler misses; this copies it. |
| `licence-files.mjs` | Puts Almanac's licence files beside the built page, and stops the build unless the page shows the notice the licence asks for. |
| `serve.mjs`, `serve-files.mjs` | A small server for this computer only. Node's own modules, nothing to install. |
| `trial.test.mjs` | Tests of all the rules above. |

## Tests

```
node --test
```

Nothing to install first, with Node 22.18 or newer. CI runs this on every
pull request.

The tests cover our own rules only. They cannot start the map: CI has no
graphics card, and the map will not draw in software. Whether the map looks
right and moves smoothly is judged by eye on the gaming PC, and with the map
recorder.
