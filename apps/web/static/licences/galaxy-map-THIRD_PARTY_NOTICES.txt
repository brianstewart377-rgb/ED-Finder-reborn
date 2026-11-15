# Third-party notices

This package uses data, art and code from other projects. This file names each source
and its terms. Read it before you redistribute the package or use it for money.

The map is a non-commercial fan project. Some of the terms below are non-commercial, so
a commercial use of the package needs new permission from each holder.

**This file covers what the package ships.** The repository holds a demo site, a test
suite and a design mockup beside the package, and their sources have terms of their own.
`THIRD_PARTY_NOTICES.md` at the root of the
[repository](https://github.com/Elite-Dangerous-Almanac/Galaxy-Map) names those.
Each source is in one file alone. The Frontier terms are the one statement in both,
because the package ships game art and the demo site draws game data.

## `@elite-dangerous-almanac/core`

The map depends on [`@elite-dangerous-almanac/core`](https://github.com/Elite-Dangerous-Almanac/Almanac-Core),
pinned to the exact version **0.2.16**. The map reads five leaves of it: `astro/galaxy-grid`
and `astro/mass-code` for the sector and boxel geometry, `astro/codex-region` and
`astro/codex-region-lookup` for the galactic codex regions, and `galaxy-map/markers` for
the colours of the system marker icons.

The build also emits the **16 galaxy-map marker vectors**, which it reads from the path
`assets/galaxy-map/` inside that same package at that same version. The almanac project
redrew them from the game's own interface artwork, so they carry the Frontier terms this
file states below and not the MIT terms of the almanac's code. This package holds no copy
of a vector in its sources: the bytes come from the published package the build installs.

The almanac package's own code is under the **MIT** licence, copyright 2026 Elite
Dangerous Community. Its data keeps the terms of its own sources, and it carries its
full notices in its own published package.

## EliteDangerousRegionMap

The 42 galactic codex regions, their ids and their lookup geometry come from
[EliteDangerousRegionMap](https://github.com/klightspeed/EliteDangerousRegionMap) by Ben
Peddell (klightspeed), under the **MIT** licence. The almanac package carries the
region tables; this map reads them through it.

The MIT licence text:

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Elite Dangerous game data and visuals (Frontier Developments)

The galaxy this map draws is the game's galaxy. The data the package reads and the art
it draws are the property of **Frontier Developments plc**.

The map uses them under Frontier's
[media-usage rules](https://forums.frontier.co.uk/threads/elite-dangerous-media-usage-rules.510879/),
which permit non-commercial use only. `LICENSE.md` states the same terms for the package,
so a commercial use of the package needs new permission from Frontier.

> This map was created using assets and imagery from Elite Dangerous, with the
> permission of Frontier Developments plc, for non-commercial purposes. It is not
> endorsed by nor reflects the views or opinions of Frontier Developments and no
> employee of Frontier Developments was involved in the making of it.

## The HUD fonts

The HUD bundles two font families. The build carries the `woff2` files from the two
packages, so no host page reaches a font CDN and the browser tests stay offline.

- **Chakra Petch**, from [`@fontsource/chakra-petch`](https://www.npmjs.com/package/@fontsource/chakra-petch).
  Copyright 2018 The Chakra Petch Project Authors, under the **SIL Open Font License,
  Version 1.1**.
- **IBM Plex Mono**, from [`@fontsource/ibm-plex-mono`](https://www.npmjs.com/package/@fontsource/ibm-plex-mono).
  Copyright 2017 IBM Corp, under the **SIL Open Font License, Version 1.1**.

The licence permits use, study, change and redistribution, on these conditions: the
font files keep this notice, a changed font takes another name, and a font is not sold
by itself. Each package carries the full licence text in its own `LICENSE` file. The
full text is also at <https://openfontlicense.org/>.

## The selection pin

`packages/galaxy-map/src/app/markers.ts` draws a pin over the selected system. The shape is the system
marker of the game's own galaxy map. The eight points of the path come from
`https://edassets.org/static/img/galaxy-map/Marker-galaxy-map.svg`, which ED Assets
publishes. This project commits no copy of that file: the eight numbers are written
into a path of its own.

ED Assets states no licence on the file. This notice records where the numbers came
from. The shape itself is Frontier Developments' and falls under the same
non-commercial media usage rules as the game data above.

## The procedural naming tables

`astro/galaxy-grid` also reaches the almanac's procedural naming tables, which come
from EDTS by Andy Martin under the BSD 3-Clause licence. Those terms need the licence
text in full wherever the tables travel. The build drops the tables from the bundle:
the map reads two constants from that leaf and calls no naming function, so
tree-shaking removes them. A check after `pnpm build` confirms it. If a later build
carries the tables, add the BSD 3-Clause text here.
