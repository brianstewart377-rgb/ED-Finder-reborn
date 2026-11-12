# The map recorder

Records a tour of the map in a real browser, on the graphics card of the
machine it runs on. It is for the owner's gaming PC.

Tests in CI run with no graphics card, so they cannot say how the map looks or
how smoothly it moves. The old repo's lesson was "tests passed while the map
looked broken". This gives something to look at instead: the same tour, flown
the same way every time, saved as a video and pictures with frame timings.
Fly it on our own map and on a trial of another map, and the two can be put
side by side.

The plan's rule holds: Playwright is used only for these recordings. Pass/fail
journeys are Cypress's job, in CI.

## What a recording is

One command opens the map in a window and flies the steps of a tour file
(search, zoom, pan, turn, tilt). It saves a new folder under `recordings/`:

| File | What it is |
|---|---|
| `index.html` | The report. Open this one. |
| `video.webm` | The whole tour as a video. |
| `shots/` | A picture after each step. |
| `report.json` | The same numbers, for a program to read. |

The report says which graphics card drew the map, and warns in red if the
browser drew it in software. It also says which drawing method the map itself
chose (ours says `WEBGPU` or `WEBGL2`): they are two different paths through
the map's code, so a recording of one says nothing about the other.

For each step it gives frames a second, the slowest frame, and how often the
picture hung. It lists every error the page or the browser's console gave,
and every request that failed, each with the step it happened in.

Requests the page dropped itself are listed apart and are not failures. The
map drops its request for the stars of a view once the view has moved on, and
the search box drops its question for "So" when "Sol" is typed. A tour of 30
steps gives about 30 of these.

## Setting it up, once

It needs Node 20 or newer and Google Chrome (or Microsoft Edge).

These are commands for PowerShell on the gaming PC, run in this folder
(`scripts\map-recorder`).

Install the recorder's one dependency, Playwright:

```powershell
npm install
```

Fetch the small program Playwright uses to save video (about 2 MB):

```powershell
npx playwright install ffmpeg
```

## Recording

Record the live site's Explore map in Chrome:

```powershell
node record.mjs --name our-map
```

It ends by printing how the map drew, the slowest step, the longest single
frame, which steps had a stall, what went wrong, where the recording went and
which file to open.

Other things it can do:

| Add this | To |
|---|---|
| `--browser msedge` | use Microsoft Edge |
| `--browser firefox` | use Firefox (first run `npx playwright install firefox`, once) |
| `--scale 1.5` | imitate Windows display scaling at 150% |
| `--pause` | open the page, then wait for Enter before the tour starts (to sign in first, say) |
| `--url http://127.0.0.1:4173` | record another copy of the site |
| `--tour tours/other.json` | fly a different tour |
| `--help` | list every option |

The recorder's browser has its own profile (the `.profile` folder here), kept
between runs and separate from your everyday browser. A sign-in made with
`--pause` is remembered there.

Leave the window alone while it records: the mouse and keyboard are being
driven by the tour.

## Tours

A tour is a file in `tours/`. It says which page to open, how to tell that the
map is ready, and the steps to fly. `tours/explore.json` is the tour of our own
Explore map.

Places on the screen are fractions of the window, `[across, down]`, so a tour
works at any size. `[0.5, 0.5]` is the middle.

| Step (`do`) | What it does | Needs |
|---|---|---|
| `wait` | waits | `seconds` |
| `waitFor` | waits until something is on the page | `selector` |
| `type` | types into a box (`"enter": true` presses Enter after) | `selector`, `text` |
| `click` | clicks a place or a thing on the page | `at` or `selector` |
| `focus` | puts the keyboard on something | `selector` |
| `keys` | presses a key several times | `key`, `times`, `seconds` |
| `wheel` | turns the mouse wheel (below 0 is forward) | `deltaY`, `ticks`, `seconds` |
| `drag` | drags the mouse (`"button": "right"` for the right button) | `from`, `to`, `seconds` |
| `move` | moves the mouse | `at` |
| `shot` | only takes a picture | |

Every step needs a `name`, which names its picture. A picture is taken after
every step unless the step says `"shot": false`. `settleSeconds` (1.5 unless
said) is how long the map is given to come to rest before the picture.

A step that does not work is marked in the report and the tour carries on.

## Trying the recorder itself

The recorder's own logic has tests that need no browser. On any machine with
Node, in this folder (`scripts/map-recorder`):

```bash
node --test
```

`--headless` runs with no window. That is for trying the recorder only: the
graphics card is not used, and the report says so.
