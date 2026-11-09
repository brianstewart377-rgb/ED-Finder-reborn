# Third-Party Notices

The machine-readable per-file rights chain is maintained in
[`assets/PROVENANCE.json`](assets/PROVENANCE.json). This notice does not apply a
repository or software license to third-party media beyond the grant stated by
that media's source.

## Coalsack background images

The following bundled files are resized and/or recompressed derivatives of
ESO image `eso1539c`, **Wide-field view of part of the Coalsack Nebula**:

- `frontend/public/bg/coalsack-1600.jpg`
- `frontend/public/bg/coalsack-2560.jpg`

Source: <https://www.eso.org/public/images/eso1539c/>

License: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)

Required credit (retained unaltered from the source):

> ESO/Digitized Sky Survey 2. Acknowledgment: Davide De Martin

The source page, the JPEGs' retained EXIF description, and
[ESO's reuse terms](https://www.eso.org/public/outreach/copyright/) were
verified on 2026-08-12. ESO permits adaptation and commercial reuse under CC
BY 4.0 when the full credit remains clear, visible, and associated with the
image. The material must not be presented as an ESO endorsement. These are
real astronomical survey images and are not derived from Frontier game media.

## Web fonts

No font binary is stored in this repository. `frontend/src/index.css` requests
these families from Google Fonts at runtime. All three families are licensed
under the **SIL Open Font License, Version 1.1 (OFL-1.1)**:

- **Orbitron** — Copyright 2018 The Orbitron Project Authors
  (<https://github.com/theleagueof/orbitron>), with Reserved Font Name:
  "Orbitron". Designer: Matt McInerney.
  [OFL text](https://github.com/google/fonts/blob/main/ofl/orbitron/OFL.txt)
- **Manrope** — Copyright 2018 The Manrope Project Authors
  (<https://github.com/sharanda/manrope>). Designer: Mikhail Sharanda.
  [OFL text](https://github.com/google/fonts/blob/main/ofl/manrope/OFL.txt)
- **JetBrains Mono** — Copyright 2020 The JetBrains Mono Project Authors
  (<https://github.com/JetBrains/JetBrainsMono>). Designer credits: JetBrains,
  Philipp Nurullin, and Konstantin Bulenkov.
  [OFL text](https://github.com/google/fonts/blob/main/ofl/jetbrainsmono/OFL.txt)

The OFL permits use, study, modification, embedding, redistribution, and sale
with software subject to its conditions; font software may not be sold by
itself. If ED-Finder later self-hosts font files, the applicable copyright and
OFL text must travel with those copies and the Orbitron reserved-name rule must
be preserved.

## EDAssets

No EDAssets media file is currently bundled or copied into ED-Finder. EDAssets
has been inspected only as a discovery and provenance catalogue:

- Site: <https://edassets.org/#/about>
- Repository: <https://github.com/Venefilyn/EDAssets>

The EDAssets repository's MIT license applies to its licensed repository code;
it is not a blanket license for all catalogued media. EDAssets' statement that
its own project was created with Frontier permission is site-specific and is
not transferable permission for ED-Finder. Any future EDAssets-sourced file
requires its own source URL, named creator credit, media license or written
grant, Frontier-derived flag, and approval record before it is added.

## Elite Dangerous Colonization Mega Guide

`docs/reference/colonisation/sources/mega-guide-v2.3.0/mega-guide-v2.3.0.txt`
is an unmodified text extraction (images omitted) of the **Elite Dangerous
Colonization Mega Guide**, version 2.3.0 (2.4.0 WIP), written by CMDR Mechan
with input from dozens of CMDRs, and released through the Frontier Forums
thread
[Colonization Mega Guide](https://forums.frontier.co.uk/threads/colonization-mega-guide-v1-0b-released.638126/).
It is redistributed under the guide's licence,
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); any adaptation
must be shared under the same licence. The guide states:

> This Elite Dangerous Colonization Mega Guide was created using assets and
> imagery from Elite Dangerous, with the permission of Frontier Developments
> plc, for non-commercial purposes. It is not endorsed by nor reflects the views
> or opinions of Frontier Developments and no employee of Frontier Developments
> was involved in the making of it. This guide is made available to you under
> CC BY-SA 4.0.

No Frontier assets or imagery from the guide are committed. The text is held as
reference material within ED-Finder's Frontier noncommercial fan-project
posture because it describes Elite Dangerous game mechanics. Attribution,
checksums and extraction details are in the folder's `README.md`.

## CRE rulebook

`docs/reference/colonisation/cre-rulebook/rulebook.json` is the repository
owner's colonisation rulebook, copied unchanged from the Colonisation Research
Engine release `cre-knowledge-v0.5.0`. It quotes short passages from the
**Elite Dangerous Colonization Mega Guide** (CMDR Mechan and contributing CMDRs,
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); see the section
above) and from Frontier Developments' update notes, and it describes Elite
Dangerous game mechanics. The quoted guide passages stay under CC BY-SA 4.0. The
file is held as reference data within ED-Finder's Frontier noncommercial
fan-project posture and is not endorsed by Frontier Developments. Release pin and
checksum are in the folder's `README.md` and `pin.json`.

## Frontier Developments fan-media boundary

Frontier's [official Elite Dangerous media guidance](https://customersupport.frontier.co.uk/hc/en-us/articles/4404292442642-How-can-I-use-Elite-Dangerous-media)
covers specified fan and community uses for noncommercial purposes, requires
clear attribution to Frontier Developments plc, prohibits misleading use or
implied endorsement, and requires advance permission for promotional or
commercial use.

ED-Finder uses Frontier-derived region names and geometry only under that
noncommercial fan-media posture and displays Frontier's preferred long-form
community-site attribution. This permission does not replace the rights or
credit of any other per-file creator. Every Frontier-derived file must retain a
separate rights chain, and no such file is approved for commercial use without
a recorded written commercial grant from Frontier and every other rights
holder.

## Community-researched exploration formula

`apps/importer/src/exploration_value.py` is an original ED-Finder Python
implementation of mathematical formulae and factual game constants documented
by Elite Dangerous community researchers in the Frontier Forums thread
[Exploration value formulae](https://forums.frontier.co.uk/threads/exploration-value-formulae.232000/).
No third-party source code is copied and the thread is not represented as
granting a source-code license. The implementation remains conservatively
within ED-Finder's Frontier noncommercial fan-project posture because it
reproduces Elite Dangerous game mechanics.

## EliteDangerousRegionMap

ED-Finder's galactic-region lookup algorithm and the region grid in
`apps/importer/src/region_map.py` and
`apps/importer/src/data/region_map.json`, together with the generated public
files `frontend/public/assets/elite-dangerous-region-map.svg` and
`frontend/public/assets/elite-dangerous-region-map.LICENSE.txt`, derive from
[`klightspeed/EliteDangerousRegionMap`](https://github.com/klightspeed/EliteDangerousRegionMap).
The audited upstream revision is commit
[`6c1191a`](https://github.com/klightspeed/EliteDangerousRegionMap/tree/6c1191a58e1e593966f44f16235ab39d1ad24d84).

MIT License

Copyright (c) 2020 Ben Peddell

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

This notice records the upstream software license. It does not make a claim
about separate rights that may apply to Elite Dangerous names or game-derived
geometry.

## Home page background (Elite Dangerous screenshot)

The home page background uses two resized WebP derivatives of an Elite Dangerous
in-game screenshot of a black hole, taken by the ED-Finder owner, who has given
permission for ED-Finder to use it:

- `apps/web/static/assets/home-hero-black-hole-1920.webp`
- `apps/web/static/assets/home-hero-black-hole-960.webp`

Required credit:

> ED-Finder was created using assets and imagery from Elite Dangerous, with the
> permission of Frontier Developments plc, for non-commercial purposes. It is not
> endorsed by nor reflects the views or opinions of Frontier Developments and no
> employee of Frontier Developments was involved in the making of it.

Frontier's [Elite Dangerous media guidance](https://customersupport.frontier.co.uk/hc/en-us/articles/4404292442642-How-can-I-use-Elite-Dangerous-media)
permits community fan use for noncommercial purposes only. The owner's
permission covers ED-Finder's use of the screenshot; because it shows Elite
Dangerous imagery, these files stay recorded as noncommercial-only and are not
approved for promotional or commercial use.
