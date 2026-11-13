# The Almanac map trial

A page that shows what a "show me on map" button in Find would do, on
Almanac's galaxy map. It answers one question: does Almanac's map do the job
we want of a map, on a real graphics card, well enough to replace our own?

It is a trial tool, like the map recorder. It is not part of the site and
nothing in `apps/web` uses it.

The trial passed on 13 November 2026 (`docs/pi-move.md`, "The Almanac trial:
passed"). This folder stays until the map is built into the site, as the
working example of how our data goes into Almanac. It goes after that.

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

- **A player's own systems, all at once.** The second step of the map's
  roadmap. When the data holds systems of the player's own, the page opens on
  "All my systems": each one on the map with its 15 light years around it,
  framed together. Built, being built and planned systems are separate
  groups, so Almanac's own switches hide and show each kind. Picking one
  gives a SHOW ON MAP button that flies in to it and its stars in range.
  On the site the builder will keep this list. In the trial it is a file:
  see "Your own systems" below.

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

## The System Map trial (a second page)

The same folder holds a second trial, for a different question: does the
System Map need a 3D engine at all? The page is `system-map.html`.

It draws a system in the manner of the game's own system map, with shapes
the browser draws itself (SVG) and no 3D engine:

- Each star heads a row, its planets follow along a line, each planet's
  moons hang below it, and stars are stacked when there are two or more.
- Each body is shaded as a lit ball. Gas giants carry bands, and rings pass
  behind and in front of their planet. Colours are by class, by our own
  map's rules; they are not pictures of the real surface.
- Each station in orbit is a small marker hanging from its body, and each
  body shows how many sites it has on the ground.
- Every body and station is an ordinary page element: it can be clicked,
  reached with the Tab key, and read by a screen reader.

It shows Sol, LHS 3447 (two stars), Polaris (a giant with a small companion)
and the player's own systems, all from the live site
(`GET /api/v1/systems/<id>`, `.../bodies`, `.../rings`, `.../stations`).

Two things our data does not always say, and how the page handles them. Both
are marked on the page, so a guess is never passed off as a fact:

- **Which body a station is at.** Sites on the ground come with their body.
  Stations in orbit mostly do not. Those are put at the body nearest to them
  by distance from the arrival point, and drawn with a dashed outline. This
  can be wrong: distance cannot tell a planet from a moon close beside it.
- **What a body orbits.** A moon with no parent in our data is hung from the
  planet its name points to ("B 1 d" from "B 1"). A pair of planets with no
  parent goes in the row of the nearest star.

Because it needs no graphics card, this page can be checked by ordinary
tests in CI, which the galaxy map cannot.

To look at it, start the small server as below and open
`http://localhost:5199/system-map.html`.

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

## Your own systems

The list is yours, so it is not kept in git. To give the trial one, make a
file named `my-systems.local.json` in this folder, in the form of
`my-systems.example.json`:

Reference text, the form of the file (not a command):

```
[
  { "name": "Sol", "status": "built" },
  { "name": "Colonia", "status": "building", "progress": 50 },
  { "name": "Wregoe SJ-I d9-89", "status": "planned" }
]
```

- `name` is the system's exact name.
- `status` is `built`, `building` or `planned`.
- `progress` is optional: how far along a build is, from 0 to 100.

The data step below then looks each one up by name on the live site and
fetches its stars in range. With no such file, the page simply has no "My
systems".

## Building the page from nothing

On any computer with Node 22.18 or newer and the internet, in this folder
(`scripts/almanac-trial`). Four commands, in this order.

Download Almanac's map and the bundler:

```
npm ci
```

Fetch the systems, their stars and their bodies from the live site (two to
three minutes; it asks about once a second):

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
| `fixtures/` | For the tests, all real and unchanged answers of the live site: the first 60 rows of Find's answer near Sol, the 30 ly box around Sol, a name search for "Sol", and the bodies, rings and stations of Sol and of Polaris. |
| `fetch-data.mjs` | Fetches Find's results and the stars around each system from the live site, and the player's own systems if a list is given. |
| `system-map.html`, `src/system-map/` | The System Map trial: `tree.mjs` (what orbits what, what is built where), `layout.mjs` (where everything goes), `draw.mjs` (the picture), `body-look.mjs` (a checked copy of our own planet colours), `page.js` (the page). |
| `my-systems.example.json` | The form of a player's own list. The real one, `my-systems.local.json`, stays out of git. |
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
