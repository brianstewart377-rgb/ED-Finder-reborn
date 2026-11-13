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
- **Find's results, marked.** People find a system in Find, not on the map;
  the map shows where the results are. So the page marks the first 50 results
  Find lists within 500 light years of Sol (one page, as our own app shows
  it), and the same around Colonia. They are plain discs in one colour.
- **The other candidates as stars, if wanted.** A second set for each anchor
  adds the rest of Find's first 10,000 candidates around the results as
  stars. Each star takes the colour of its star class, by the same rule our
  own map uses (`apps/web/src/lib/spatial/stellar-presentation.ts`, read
  directly, not copied).
- **Stars only close up.** The stars draw when you zoom in to 400 light years
  or closer. From further out, ten thousand markers are one bright ball that
  hides the results. That is the one rule the page adds to the map.

The data is fetched from the live site. 10,000 is the most Find gives for one
search.

Almost everything on the screen is Almanac's own:

| On the screen | Whose it is |
|---|---|
| The set chooser in the top bar ("Results near Sol") | Almanac's |
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

To open on another set, add its name to the address, for example
`http://localhost:5199/?set=colonia-stars`. The four names are `sol`,
`sol-stars`, `colonia` and `colonia-stars`.

## Building the page from nothing

On any computer with Node 22.18 or newer and the internet, in this folder
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
| `src/logic.mjs` | The page's own rules: which candidates are results and which are stars, a star's group and colour, when stars draw, and what the panel says. |
| `fixtures/find-sol-first-60.json` | The real first 60 rows of Find's answer near Sol, for the tests. |
| `fetch-data.mjs` | Fetches the candidates from the live site. |
| `worker-files.mjs`, `vite.config.mjs` | The build. Almanac's background workers ask for one picture the bundler misses; this copies it. |
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

## Seeing how many markers the map carries

With the page open, in the browser's console:

```
trial.addMadeUp(39999, 'sol')
```

That adds made-up systems in a cube around Sol, in a red group of their own
called "Made-up", up to Almanac's limit of 50,000 systems at a time. Use it
on a set with stars (`sol-stars`), so the count starts from 10,001. It
answers with how many were added and how long that took. Reloading the page
clears them.
