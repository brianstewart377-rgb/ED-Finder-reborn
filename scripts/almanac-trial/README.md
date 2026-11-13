# The Almanac map trial

A page that shows Almanac's galaxy map with ED-Finder's own candidates on it.
It answers one question: does Almanac's map work with our data, on a real
graphics card, well enough to replace our own map?

It is a trial tool, like the map recorder. It is not part of the site and
nothing in `apps/web` uses it. When the trial ends, either way, this folder
goes.

## What it shows

- **Almanac's map exactly as published**: the npm package
  `@elite-dangerous-almanac/galaxy-map`, version 0.7.1, with no changes of
  ours. Its licence is PolyForm Noncommercial 1.0.0. None of its code is kept
  in this repository; the build downloads it.
- **Two sets of our own data**: the first 10,000 candidates Find lists within
  500 light years of Sol, and the same around Colonia, fetched from the live
  site. 10,000 is the most Find gives for one search.
- **Groups by second use.** On 13 Nov 2026 all 10,000 candidates near Sol had
  the same score (100), tier (S) and best use (flexible), so those cannot tell
  them apart on a map. What else each one suits can: stronghold, manufacturing
  hub, megacomplex, research hub, mining hub, paradise, population capital.

Almost everything on the screen is Almanac's own:

| On the screen | Whose it is |
|---|---|
| The set chooser in the top bar ("Candidates near Sol") | Almanac's |
| The Filters panel: search by name, the groups with their counts, on and off | Almanac's |
| The map options: region names, system names, grid, nebulae | Almanac's |
| The panel about one system, and its position, region, star and bodies | Almanac's |
| In that panel: score, tier, best as, also suits, distance from Colonia, claim, id | Ours, from Find |
| The OPEN IN INSPECT button, which opens the system on ed-finder.app | Ours |

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

To open on Colonia instead, use `http://localhost:5199/?anchor=colonia`.

## Building the page from nothing

On any computer with Node 20 or newer and the internet, in this folder
(`scripts/almanac-trial`). Four commands, in this order.

Download Almanac's map and the bundler:

```
npm ci
```

Fetch the candidates from the live site (a few minutes; it asks about once a
second):

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
| `src/logic.mjs` | The page's own rules: how a candidate from Find becomes a marker, its group, and what the panel says. |
| `fetch-data.mjs` | Fetches the candidates from the live site. |
| `worker-files.mjs`, `vite.config.mjs` | The build. Almanac's background workers ask for one picture the bundler misses; this copies it. |
| `serve.mjs`, `serve-files.mjs` | A small server for this computer only. Node's own modules, nothing to install. |
| `trial.test.mjs` | Tests of all the rules above. |

## Tests

```
node --test
```

Nothing to install first. CI runs this on every pull request.

The tests cover our own rules only. They cannot start the map: CI has no
graphics card, and the map will not draw in software. Whether the map looks
right and moves smoothly is judged by eye on the gaming PC, and with the map
recorder.

## Seeing how many markers the map carries

With the page open, in the browser's console:

```
trial.addMadeUp(39999, 'sol')
```

That adds made-up systems in a cube around Sol, in a red group of their own
called "Made-up", up to Almanac's limit of 50,000 systems at a time. It
answers with how many were added and how long that took. Reloading the page
clears them.
