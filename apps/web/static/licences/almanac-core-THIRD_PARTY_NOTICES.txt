# Attributions and third-party notices

Elite Dangerous Almanac incorporates community-researched algorithms and factual
galaxy data. The project's own implementation is MIT-licensed; upstream material
remains subject to its own terms, several of which are **non-commercial**. Read
this file and `LICENSE` before redistributing the data or using it commercially.

**This is the one place a source is described.** Each source is described in one of the
sections below — its author, its link and its licence position — and where an entry names
a source another section covers, it does so as a cross-reference rather than a second
description. Nothing else in the repository repeats that: a data file's comment header
names the source and points here, and each
`data/<domain>/SOURCES.md` records what was taken from it — the acquisition date, the
pinned revision, the derivation and every manual correction — referring to it by name
alone. Test fixtures carry their own provenance in their file header.

This file is shipped to npm consumers inside the package as `THIRD_PARTY_NOTICES.md`,
a verbatim copy produced at build time — edit this file, never the copy.

## Algorithms

- **Procedural sector and system naming** — reverse-engineered by the Elite Dangerous
  community and ported from the EDTS reference implementation (`edtslib/pgdata.py`,
  `edtslib/pgnames.py`) by **Andy Martin** (Esvandiary),
  <https://bitbucket.org/Esvandiary/edts>, **BSD 3-Clause, © 2016 Andy Martin** —
  reproduced in full at the end of this file, as its terms require. The TypeScript port
  passed through canonn-signals (credited under Data below) before being restructured
  here. (EDTS lives on Bitbucket, not GitHub.)
- **[EDCD/Coriolis](https://github.com/EDCD/coriolis)** — the Coriolis _application_, by
  the **Coriolis contributors**, whose code is **MIT**-licensed. (Its data repository is a
  separate credit, under Data below.) The **build-metric algorithms** are ported as fact
  (our own implementation) from it: the power budget and its priority groups; loaded speed,
  boost and rotation through the thruster-mass and ENG-pip curves; shield strength and its
  mass curve; shield collapse delay, regeneration and SYS-capacitor recovery timing; armour
  hit points; resistance stacking with its diminishing returns; weapon DPS / capacitor
  draw / heat; SYS- and WEP-pip recharge; and weapons-capacitor endurance. It credits the
  original Frontier-forum research the formulas come from.
- **The heat model** — the equilibrium heat level a thermal load settles at, how heat
  moves towards it over time, and the multiplier a drained weapons capacitor puts on a
  weapon's thermal load — is ported as fact from EDSY's `edsy.js`
  (`getEquilibriumHeatLevel`, `getTimeUntilHeatLevel`, `getHeatLevelAtTime`,
  `getEffectiveWeaponThermalLoad`, `updateUIStatsThm`), which credits the Frontier-forum
  research thread
  [Research: detailed heat mechanics](https://forums.frontier.co.uk/threads/research-detailed-heat-mechanics.286628/)
  the mechanics were reverse-engineered in. Frontier publishes no heat formula and
  coriolis-data models none, so the model is community reverse-engineering, and the
  per-hull dissipation figures it reads (credited with EDSY's other data and the
  running-game audits below) are community _measurements_ of the game rather than stats
  the game displays. Both
  describe Frontier's game and fall under the same Frontier notice as every other stat
  here; what differs is how they were arrived at, not who owns them.
- **The jump-range and fuel algorithm**, **per-axis ENG-pip handling**, **ENG-capacitor
  pip-scaled recharge**, the **engineered ammunition rounding** rule and the **build metrics
  above** are cross-checked against, or ported as fact from, EDSY (credited under Data below).
  The jump-range model derives from
  Frontier's "mass effect on hyperspace range" description; Coriolis carries the same maths
  for the metrics, and a clip round-up that omits EDSY's burst step, where EDSY is followed.
- **Galactic codex region lookup** — resolving a region from galactic coordinates or from a
  boxel, ported as fact (our own implementation) from `RegionMap.js` in
  EliteDangerousRegionMap, whose region tables are credited under Data below.
- **SLEF parsing and writing** — both follow the
  [Inara Ship Loadout Export Format specification](https://inara.cz/elite/inara-impexp-slef/)
  published by **Inara** (Artie).
- **Body calculations** — bulk density, the rigid and fluid Roche limits, the Hill radius,
  a primary's angular diameter, orbit extents and their eccentricity bands, spin-orbit
  resonance, equatorial velocity, ring dynamics and surface density, the invisible-ring
  heuristic, ring-particle densities, main-sequence lifetime, absolute bolometric
  magnitude, the Schwarzschild radius, the degeneracy-pressure mass limits and the
  neutron-star classes are ported as fact (our own implementation, re-expressed in the
  journal's units) from **canonn-signals**' `body-physics.service.ts`,
  `stellar-physics.service.ts` and `stellar-reference.ts` — credited under Data below.
  Most of it is textbook astrophysics; three parts are the **Canonn Research Group's own
  observational research** into how the game behaves, and are theirs rather than
  astronomy's: the `3/8` nominal-radius factor that recovers a ring's single rigid-body
  rotation period, the invisible-ring width and surface-density thresholds, and the
  neutron-star classification bands with the observed in-game mass past which neutron
  stars become rare.
  Each carries that provenance in its own TSDoc.
- **The journal `Scan` event shape** — the body-scan import interface describes the format
  **Frontier Developments plc** writes its Player Journal in, and its field set, types and
  conditional presence are cross-checked against
  [jixxed/ed-journal-schemas](https://github.com/jixxed/ed-journal-schemas), the
  community-maintained JSON Schemas for post-Odyssey journal events, **Apache-2.0**.
  Field names and types are facts about Frontier's format; no schema text is redistributed
  and no code from that project is used.

## Data

- **[EDCD FDevIDs](https://github.com/EDCD/FDevIDs)** — the community-maintained registry
  of Frontier's internal ids and names. Supplies the identities of materials and micro
  resources, ships and outfitting modules, and standard and rare market commodities.
  FDevIDs states no explicit licence; consult the repository terms before redistributing
  the raw identifiers.
- **[EDCD/coriolis-data](https://github.com/EDCD/coriolis-data)** — ship and module
  stats, per-hull slot layouts, engineering blueprint modifiers and their material
  recipes. coriolis-data releases only its _code_ under **MIT**; the JSON stat values are
  **Elite Dangerous game data, property of Frontier Developments plc** — see the Frontier
  notice below.
- **[EDSY](https://github.com/taleden/EDSY)** by **taleden**, **CC BY-NC 4.0** —
  `eddb.js` and `edsy.js`. Supplies the values coriolis-data leaves blank or carries
  wrongly (module mass, integrity, power draw, boot time, the base stats engineering
  recipes scale), the experimental-effect modifiers and their recipes, the module-group
  engineering menus, per-ship module-count limits and fitted stabiliser increases, the
  baseline per-hull maximum heat dissipation, the
  Lynx Highliner's hull/slot figures and zero-pip minimum pitch, the journal slot names,
  localized module, blueprint, experimental-effect and engineering option-group names, and the
  attribute-to-journal-Label mapping. The values are Elite Dangerous game data — see the
  Frontier notice below. Heat dissipation is the one entry the game never shows a player:
  it is community measurement of Frontier's game, described under Algorithms above, and
  carried here on the same terms as the rest.
- **[EDCD/EDMarketConnector](https://github.com/EDCD/EDMarketConnector)** by the
  **EDMarketConnector contributors**, **GPL-2.0** for its code — supplies the Frontier
  journal `SlotName` keys that identify a suit's weapon mounts. What is taken are the
  verbatim loadout events quoted in its `monitor.py` comments; no EDMarketConnector code
  is incorporated. The values are game data — see the Frontier notice below.
- **[EDDI](https://github.com/EDCD/EDDI)** by the **EDDI contributors**, **Apache 2.0**
  for its code — its module definitions and resource tables supply localized outfitting
  names. The names are factual Elite Dangerous game data; no EDDI code is incorporated.
- **[Inara](https://inara.cz/)** — the blueprint and outfitting registries behind the
  Operations pre-engineered blueprints, shop rows and the per-roll Merc-Coin crafting
  costs, the ship pages used to corroborate hull layouts, and the component pages that
  grade the Thargoid caustic / Titan materials absent from FDevIDs and classify Power
  Megaship Data as an Odyssey data resource.
- **[EliteDangerousRegionMap](https://github.com/klightspeed/EliteDangerousRegionMap)** by
  **Ben Peddell** ([klightspeed](https://github.com/klightspeed)), **MIT** — the 42
  galactic codex regions, their ids and their lookup geometry. Original region-boundary
  research on the
  [Frontier forums](https://forums.frontier.co.uk/threads/determining-the-region-of-a-system.537845/).
- **[canonn-science/canonn-signals](https://github.com/canonn-science/canonn-signals)** by
  the **Canonn Research Group**, **MIT**, © 2023 — the route by which two of the sources
  below were obtained, the TypeScript port the procedural-naming algorithm passed through,
  and the source of the body calculations credited under Algorithms above.
- **EDAstro nebulae coordinates** published by **CMDR Orvidius**
  ([EDAstro](https://edastro.com/mapcharts/)) — nebula names, catalogued systems,
  coordinates, classes and region ids, obtained via canonn-signals. EDAstro states no
  explicit licence for the dataset; consult the site's terms before redistributing it.
- **"Elite Dangerous Permit Database"** — the community-maintained spreadsheet behind the
  permit-locked systems and regions, obtained via canonn-signals. Permit status is
  published in no game file or API, so the list is hand-maintained and best-effort.
- **[EDSM](https://www.edsm.net)** and **[Spansh](https://spansh.co.uk)**, maintained by
  their respective community contributors — factual system names, coordinates and
  addresses, used for the hand-authored region spheres, the named-region origins and the
  permit-locked systems' `id64` values. Consult each service's published terms for use of
  its API or data.
- **[Odyssey Materials Helper](https://github.com/jixxed/ed-odyssey-materials-helper)** by
  **Jixxed**, **MIT** — supplies the personal suit and handheld-weapon identities, stats,
  journal symbols, grade-upgrade recipes and engineer-applied modification recipes. A
  CAPI response in its test resources also corroborates the six bundle-granted Vessel
  Hangar variants. Its language tables supply localized engineering-material and Odyssey
  micro-resource display names. The values and response are factual Elite Dangerous game
  data. Its MIT notice is reproduced below.
- **[msarilar/EDEngineer](https://github.com/msarilar/EDEngineer)**, **MIT** for its code
  — its `blueprints.json` corroborates which weapons the Overcharged recipe gives a clip
  penalty to. The values are game data.
- **[Elite Dangerous Wiki](https://elite-dangerous.fandom.com/)** (Fandom,
  **CC BY-SA 3.0**) — how the Corrosion Resistant Cargo Racks are obtained, which is why
  two of them carry no list price.
- **Frontier Developments plc** — the game itself, the ship models represented by the
  shared vector illustrations, the galaxy-map marker artwork redrawn as the shared
  vector markers in `assets/galaxy-map/`, the official
  [Elite Dangerous Gamestore](https://www.elitedangerous.com/store/) product names used
  to correct the four Karma and three TK handheld weapons, and its published statements:
  the [media-usage rules](https://forums.frontier.co.uk/threads/elite-dangerous-media-usage-rules.510879/)
  quoted below, the Operations and Lynx
  [update notes](https://forums.frontier.co.uk/threads/648012/), and the
  [Rhea Disaster Community Goal](https://forums.frontier.co.uk/threads/deliver-critical-aid-for-the-rhea-disaster.626528/)
  with its [announcement](https://x.com/EliteDangerous/status/1812792503776489745).
  Frontier's own **in-game localisation** supplies the outfitting category labels behind
  the module families and their German, Spanish, French, Brazilian Portuguese and Russian
  display text, the engineering modification names in the six stored locales, the market
  commodity names in `data/i18n/commodity-names.jsonc`, the galactic codex region names
  in `data/i18n/codex-region-names.jsonc`, and the personal-equipment
  display text in `data/i18n/`: the suit names and descriptions, the suit tool names, the
  handheld-weapon descriptions, and the names and descriptions of the engineer-applied
  suit and weapon modifications. The game's own **commodity registry** supplies the
  symbols and names of the mineral and chemical goods
  `data/commodities/commodities.jsonc` holds beyond the FDevIDs snapshot.
  Values read directly from the running game are Frontier's too — see the notice below.
- **A [community description](https://www.reddit.com/r/EliteDangerous/comments/1uk2zhp/plasma_laser_theorycrafting_following_new/)
  by u/Techno3020** — linked only as corroboration that the Operations Plasma conversion's
  Plasma share is absolute damage. The post states no redistribution licence; none of its
  text or media is redistributed.

## Test fixtures

The fixtures in `fixtures/` are not bundled into any published package. Each one carries
its own provenance in its file header; the projects that published the captures they came
from are credited here:

- **[adam-drewery/EliteAssist](https://github.com/adam-drewery/EliteAssist)**, **WTFPL** —
  a Frontier journal `Loadout` event in its example data.
- **[UFO-Studios/EDDP](https://github.com/UFO-Studios/EDDP)** — a captured journal log the
  project ships as example data. That repository's own code is under the **UFO Licence
  1.0**, which permits use with credit but not redistribution of the project; the licence
  covers UFO Studios' project, not the Frontier journal line quoted from its example data,
  which travels under Frontier's media-usage terms like every other capture. Credit to UFO
  Studios & AW2C Systems Ltd for capturing and publishing the log. No code from that
  project is used.
- **[Coriolis](https://coriolis.io/)** (`s.orbis.zone`) and **[EDSY](https://edsy.org/)**
  — the two share-link formats the 181-build community corpus was decoded from, using each
  tool's own published serialisation and id tables (credited above). No code from either
  is vendored here.
- **[Inara](https://inara.cz/)** — SLEF exports produced by its loadout tool.

Every capture is Elite Dangerous game output and remains the property of Frontier
Developments plc — see the notice below.

## Elite Dangerous game data and imagery (Frontier media-usage notice)

The ship and module stat values, the ship illustrations, and the galaxy-map marker
colours and vectors are the property of **Frontier Developments plc** and are used
under Frontier's
[media-usage rules](https://forums.frontier.co.uk/threads/elite-dangerous-media-usage-rules.510879/):

> Elite Dangerous Almanac was created using assets and imagery from Elite
> Dangerous, with the permission of Frontier Developments plc, for non-commercial
> purposes. It is not endorsed by nor reflects the views or opinions of Frontier
> Developments and no employee of Frontier Developments was involved in the making
> of it.

Projects that redistribute this data should include the same notice.

## Where the provenance lives

This file credits. What was taken from each source, when, from which revision, how it was
derived and every manual correction are recorded with the data itself, in
[`data/astro/SOURCES.md`](https://github.com/Elite-Dangerous-Almanac/Almanac-Core/blob/main/data/astro/SOURCES.md),
[`data/materials/SOURCES.md`](https://github.com/Elite-Dangerous-Almanac/Almanac-Core/blob/main/data/materials/SOURCES.md),
[`data/equipment/SOURCES.md`](https://github.com/Elite-Dangerous-Almanac/Almanac-Core/blob/main/data/equipment/SOURCES.md),
[`data/i18n/SOURCES.md`](https://github.com/Elite-Dangerous-Almanac/Almanac-Core/blob/main/data/i18n/SOURCES.md),
[`data/ships/SOURCES.md`](https://github.com/Elite-Dangerous-Almanac/Almanac-Core/blob/main/data/ships/SOURCES.md),
[`data/commodities/SOURCES.md`](https://github.com/Elite-Dangerous-Almanac/Almanac-Core/blob/main/data/commodities/SOURCES.md)
and
[`data/galaxy-map/SOURCES.md`](https://github.com/Elite-Dangerous-Almanac/Almanac-Core/blob/main/data/galaxy-map/SOURCES.md),
with the rules those files follow in
[`data/SNAPSHOTS.md`](https://github.com/Elite-Dangerous-Almanac/Almanac-Core/blob/main/data/SNAPSHOTS.md).
The npm package includes matching copies under `PROVENANCE/` (without the `data/`
prefix); the links above are absolute so they resolve from inside the package too.

If you add or change data, port an algorithm, or add a dependency that warrants credit,
add it here and record the provenance where the data lives.

---

## Odyssey Materials Helper license

The personal-equipment data and localized material and micro-resource name tables are
derived from Odyssey Materials Helper, whose MIT notice is reproduced as required:

```
Copyright (c) 2026 Jixxed

Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software
without restriction, including without limitation the rights to use, copy, modify,
merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE
FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
```

---

## EDTS license (procedural naming)

The procedural sector- and system-naming algorithm and its region tables derive
from EDTS by Andy Martin (<https://bitbucket.org/Esvandiary/edts>), whose full
license is reproduced here as required by its terms:

```
Copyright (c) 2016, Andy Martin
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * Neither the name of the EDTS Project nor the
      names of its contributors may be used to endorse or promote products
      derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE EDTS PROJECT OR ANDY MARTIN BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
