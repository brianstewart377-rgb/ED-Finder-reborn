// Run with:  node --test   (in this folder). Nothing to install first.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  ANCHORS, MINE_ID, OPENING_DISTANCE_LY, RANGE_LY, RESULTS_OFFERED, STATUSES, anchorInBox, boxAround, contentOf,
  datasetEntry, detailsOf, inspectAddress, mineAmong, mineContent, mineEntry, pickByName, rangeSphere, readMine,
  showing, showingMine, starCategories, starGroup, starsInRange, systemInBox, trimRow,
} from './src/logic.mjs';
import { TYPES, fileFor, typeOf } from './serve-files.mjs';
import { FAMILIES, FAMILY_OF_CLASS, familyOf, isBanded } from './src/system-map/body-look.mjs';
import {
  RING_KINDS, buildTree, groundSlotsOf, nearEnough, readSystem, ringsInWords, shortName,
} from './src/system-map/tree.mjs';
import { drawnRadius, entriesOf, groundMarkOf, layoutOf } from './src/system-map/layout.mjs';
import {
  RING_COLOURS, lookOf, pictureOf, ringBandsOf, safe, spokenOf, spokenOfStation,
} from './src/system-map/draw.mjs';
import { patternOf, pixelsFor, seedOf, surface } from './src/system-map/surface.mjs';
import { LICENCE_FILES, addLicences, noticesMissingFrom, requiredNotices } from './licence-files.mjs';
import { copyWorkerFiles, filesAskedFor } from './worker-files.mjs';
import { readFileSync } from 'node:fs';
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

// The first row Find gave for "within 500 ly of Sol" on 13 Nov 2026, with the
// fields the page does not use left out.
const WREGOE = {
  id64: 3068561197395, name: 'Wregoe SJ-I d9-89',
  coords: { x: 308.4375, y: -23.25, z: -253.0625 }, distance: 399.64,
  main_star_type: 'F (White) Star', archetype_score: 100.0, archetype_tier: 'S',
  primary_archetype: 'flexible', secondary_archetype: 'stronghold', claim_status: 'free', body_count: 18,
};

test('a row from Find is cut down to what the page shows', () => {
  assert.deepEqual(trimRow(WREGOE), {
    id64: '3068561197395', name: 'Wregoe SJ-I d9-89', x: 308.4375, y: -23.25, z: -253.0625,
    score: 100, tier: 'S', archetype: 'flexible', second: 'stronghold', distance: 399.64,
    star: 'F (White) Star', bodies: 18, claim: 'free',
  });
});

test('a row with no id, no name or no position is left out', () => {
  assert.equal(trimRow({ ...WREGOE, id64: null }), null);
  assert.equal(trimRow({ ...WREGOE, name: '' }), null);
  assert.equal(trimRow({ ...WREGOE, coords: { x: 1, y: null, z: 3 } }), null);
  assert.equal(trimRow(undefined), null);
  assert.equal(trimRow({ ...WREGOE, id64: 2 ** 53 + 2 }), null, 'too large to be held exactly');
  assert.equal(trimRow({ ...WREGOE, id64: '9007199254740994' }).id64, '9007199254740994');
});

// Every name Find gave for a main star among the 20,000 candidates near Sol
// and Colonia on 13 Nov 2026, with the group our own map's rule puts it in.
const REAL_STAR_NAMES = [
  ['M (Red dwarf) Star', 'M class star'],
  ['K (Yellow-Orange) Star', 'K class star'],
  ['G (White-Yellow) Star', 'G class star'],
  ['F (White) Star', 'F class star'],
  ['A (Blue-White) Star', 'A class star'],
  ['B (Blue-White) Star', 'B class star'],
  ['L (Brown dwarf) Star', 'L brown dwarf'],
  ['T (Brown dwarf) Star', 'Y / T brown dwarf'],
  ['Y (Brown dwarf) Star', 'Y / T brown dwarf'],
  ['T Tauri Star', 'T Tauri / AeBe Herbig'],
  ['K (Yellow-Orange giant) Star', 'M / K giant or supergiant'],
  ['M (Red giant) Star', 'M / K giant or supergiant'],
  ['G (White-Yellow super giant) Star', 'G giant or supergiant'],
  ['F (White super giant) Star', 'F giant or supergiant'],
  ['A (Blue-White super giant) Star', 'O / B / A giant or supergiant'],
  ['Neutron Star', 'Neutron star'],
  ['Black Hole', 'Black hole'],
  ['White Dwarf (DA) Star', 'White dwarf'],
  ['White Dwarf (DAB) Star', 'White dwarf'],
  ['White Dwarf (DAV) Star', 'White dwarf'],
  ['White Dwarf (DAZ) Star', 'White dwarf'],
  ['White Dwarf (DB) Star', 'White dwarf'],
  ['White Dwarf (DBV) Star', 'White dwarf'],
  ['White Dwarf (DC) Star', 'White dwarf'],
  ['White Dwarf (DCV) Star', 'White dwarf'],
];

test('every real star name from Find lands in a star group, in a colour the map can draw', () => {
  for (const [star, group] of REAL_STAR_NAMES) {
    const found = starGroup(star);
    assert.equal(found.name, group, star);
    assert.ok(found.color.length === 3 && found.color.every((part) => Number.isInteger(part) && part >= 0 && part <= 255), star);
    assert.ok(Math.max(...found.color) >= 100, `${star} would be too dark to see`);
  }
  assert.equal(starGroup(null).name, 'Unknown star class');
});

test('a star takes the colour our own map draws its class in', () => {
  // From apps/web/src/lib/spatial/stellar-presentation.ts: an M class core is
  // [1, 0.2, 0.06] and a G class core [1, 0.98, 0.66]. A black hole's core is
  // black, so it takes its ring's colour, [0.56, 0.43, 0.34].
  assert.deepEqual(starGroup('M (Red dwarf) Star').color, [255, 51, 15]);
  assert.deepEqual(starGroup('G (White-Yellow) Star').color, [255, 250, 168]);
  assert.deepEqual(starGroup('Black Hole').color, [143, 110, 87]);
});

test('the star groups among some candidates come commonest first, each once', () => {
  const rows = ['M (Red dwarf) Star', 'K (Yellow-Orange) Star', 'M (Red dwarf) Star', 'M (Red giant) Star', null]
    .map((star) => ({ star }));
  assert.deepEqual(starCategories(rows).map((category) => category.name), [
    'M class star', 'K class star', 'M / K giant or supergiant', 'Unknown star class',
  ]);
  assert.deepEqual(starCategories([]), []);
});

test('the Inspect button opens the system on the live site', () => {
  // https://ed-finder.app/s/<id64> answered 302 to /inspect?system=<id64> on 13 Nov 2026.
  assert.equal(inspectAddress('3068561197395'), 'https://ed-finder.app/s/3068561197395');
});

// The live site's real answer for the 30 ly box around Sol (see the file's own note).
const SOL_BOX = JSON.parse(readFileSync(new URL('./fixtures/map-systems-sol-box.json', import.meta.url), 'utf8')).response.systems;
// The real first rows of Find's answer near Sol (see the file's own note).
const FIRST_60 = JSON.parse(readFileSync(new URL('./fixtures/find-sol-first-60.json', import.meta.url), 'utf8')).results;

const solShown = () => showing(anchorInBox(SOL_BOX, ANCHORS.sol), SOL_BOX, 'Sol');

test('the box asked for is the range around the system, widened to whole light years', () => {
  assert.equal(RANGE_LY, 15);
  assert.deepEqual(boxAround(ANCHORS.sol), { min_x: -15, max_x: 15, min_y: -15, max_y: 15, min_z: -15, max_z: 15 });
  assert.deepEqual(boxAround({ x: 308.4375, y: -23.25, z: -253.0625 }), {
    min_x: 293, max_x: 324, min_y: -39, max_y: -8, min_z: -269, max_z: -238,
  });
});

test('an anchor is found in its own box, as the site holds it', () => {
  assert.deepEqual(anchorInBox(SOL_BOX, ANCHORS.sol), { id64: '10477373803', name: 'Sol', x: 0, y: 0, z: 0, star: 'G2' });
  assert.equal(anchorInBox(SOL_BOX, ANCHORS.colonia), null);
});

test('the stars in range are those within 15 ly, nearest first, without the chosen system', () => {
  const sol = anchorInBox(SOL_BOX, ANCHORS.sol);
  const stars = starsInRange(SOL_BOX, sol);
  // The box holds 84 systems. 33 sit in its corners, further than 15 ly; Sol is the chosen one.
  assert.equal(SOL_BOX.length, 84);
  assert.equal(stars.length, 50);
  assert.ok(stars.every((star) => star.distance <= 15 && star.id64 !== sol.id64));
  assert.ok(stars.every((star, index) => index === 0 || stars[index - 1].distance <= star.distance));
  // Our data holds a system named "Test" one light year from Sol, with no star class.
  assert.deepEqual(stars[0], { id64: '7780433924818', name: 'Test', x: -1, y: 0, z: 0, star: null, distance: 1 });
  assert.equal(stars[1].name, 'Alpha Centauri');
  assert.equal(stars[1].distance.toFixed(2), '4.38');
  assert.equal(stars.at(-1).name, 'LHS 449');
  assert.equal(starsInRange(SOL_BOX, sol, 5).length, 2);
});

test('every star class in the real box lands in a star group', () => {
  for (const system of SOL_BOX) {
    const group = starGroup(system.main_star_class).name;
    if (system.main_star_class === null) assert.equal(group, 'Unknown star class');
    else assert.notEqual(group, 'Unknown star class', system.main_star_class);
  }
  assert.equal(starGroup('G2').name, 'G class star');
  assert.equal(starGroup('D5').name, 'White dwarf');
  assert.equal(starGroup('Y0').name, 'Y / T brown dwarf');
  // From the real box around Colonia on the same day.
  assert.equal(starGroup('TTS7').name, 'T Tauri / AeBe Herbig');
  assert.equal(starGroup('B0').name, 'B class star');
});

test('a showing is one entry of the map\'s own list, opening close on the chosen system', () => {
  const entry = datasetEntry(solShown());
  assert.equal(entry.id, '10477373803');
  assert.equal(entry.label, 'Sol');
  assert.equal(entry.collection, 'Where the searches start');
  assert.equal(entry.systemCount, 51);
  assert.deepEqual(entry.view, { cursor: [0, 0, 0], distance: OPENING_DISTANCE_LY, pitch: 35 });
  assert.ok(entry.description.startsWith('Sol and the 50 real stars within 15 light years of it'));
  assert.equal(entry.load, undefined, 'the page adds the loader');

  const row = trimRow(FIRST_60[0]);
  const chosen = { id64: row.id64, name: row.name, x: row.x, y: row.y, z: row.z, star: row.star };
  assert.equal(datasetEntry(showing(chosen, [], 'Sol', row)).collection, 'Chosen in Find, near Sol');
});

test('the map is handed the chosen system first, then every star, each in its own star\'s group', () => {
  const content = contentOf(solShown());
  assert.equal(content.systems.length, 51);
  assert.deepEqual(content.systems[0], {
    name: 'Sol', id64: '10477373803', coords: { x: 0, y: 0, z: 0 },
    categories: ['G class star'], primaryStar: 'G2',
  });
  assert.deepEqual(content.systems[2], {
    name: 'Alpha Centauri', id64: '1178708478315', coords: { x: 3.03125, y: -0.09375, z: 3.15625 },
    categories: ['G class star'], primaryStar: 'G2',
  });
  assert.deepEqual(content.categories.map((category) => category.name), [
    'M class star', 'Y / T brown dwarf', 'G class star', 'K class star', 'F class star', 'A class star',
    'L brown dwarf', 'Unknown star class', 'White dwarf',
  ]);
  const told = new Set(content.categories.map((category) => category.name));
  for (const system of content.systems) assert.ok(told.has(system.categories[0]), system.name);
});

test('the claim range is a 15 ly ball around the chosen system', () => {
  assert.deepEqual(rangeSphere(solShown()), {
    name: '15 ly around Sol', position: [0, 0, 0], radius: 15, color: [255, 170, 80], opacity: 0.1,
  });
});

test('the panel says how far a star is from the chosen system', () => {
  const shown = solShown();
  const alpha = shown.stars[1];
  assert.deepEqual(detailsOf(shown, alpha.id64), {
    description: 'A real star within 15 ly of Sol.',
    values: [{ label: 'FROM SOL', value: '4.4 ly' }, { label: 'ID', value: '1178708478315', copy: '1178708478315' }],
  });
  assert.equal(detailsOf(shown, 'no such id'), null);
});

test('the panel says what Find said of a system chosen in Find', () => {
  const row = trimRow(FIRST_60[0]);
  const chosen = { id64: row.id64, name: row.name, x: row.x, y: row.y, z: row.z, star: row.star };
  const details = detailsOf(showing(chosen, [], 'Sol', row), row.id64);
  assert.deepEqual(details.values, [
    { label: 'SCORE', value: '100' },
    { label: 'TIER', value: 'S' },
    { label: 'BEST AS', value: 'flexible' },
    { label: 'ALSO SUITS', value: 'stronghold' },
    { label: 'CLAIM', value: 'free' },
    { label: 'STARS IN RANGE', value: '0' },
    { label: 'ID', value: '3068561197395', copy: '3068561197395' },
  ]);
  assert.ok(details.description.startsWith('Chosen in Find, near Sol.'));
  assert.ok(details.values.length <= 12, 'the panel takes at most 12 values');
  // An anchor was not chosen in Find, so it has no score to show.
  assert.deepEqual(detailsOf(solShown(), '10477373803').values, [
    { label: 'STARS IN RANGE', value: '50' },
    { label: 'ID', value: '10477373803', copy: '10477373803' },
  ]);
});

test('the trial offers the first ten of Find\'s results near each anchor', () => {
  assert.equal(RESULTS_OFFERED, 10);
  assert.equal(FIRST_60.map(trimRow).filter(Boolean).slice(0, RESULTS_OFFERED).at(-1).name, 'Swoiwns GO-H b2-0');
});

// The live site's real answer to a name search for "Sol" (see the file's own note).
const NAMED_SOL = JSON.parse(readFileSync(new URL('./fixtures/autocomplete-sol.json', import.meta.url), 'utf8')).response.results;

// A player's list for the tests. The systems are real ones from the fixtures;
// which is built and which planned is made up here, as a player would type it.
const A_LIST = [
  { name: 'Sol', status: 'built' },
  { name: 'Wregoe SJ-I d9-89', status: 'building', progress: 86 },
  { name: 'HIP 101979', status: 'planned' },
];

function someOfMine() {
  const sol = systemInBox(SOL_BOX, pickByName(NAMED_SOL, 'Sol'));
  const rows = FIRST_60.map(trimRow);
  const asChosen = (row) => ({ id64: row.id64, name: row.name, x: row.x, y: row.y, z: row.z, star: row.star });
  const list = readMine(A_LIST);
  return [
    showingMine(sol, SOL_BOX, list[0]),
    showingMine(asChosen(rows[0]), [], list[1]),
    showingMine(asChosen(rows[1]), [], list[2]),
  ];
}

test('a player\'s list is read as written, and a mistake in it is named plainly', () => {
  assert.deepEqual(readMine(A_LIST), A_LIST);
  assert.deepEqual(readMine([{ name: '  Sol ', status: 'built', progress: null }]), [{ name: 'Sol', status: 'built' }]);
  assert.throws(() => readMine({ name: 'Sol' }), /must be a list/u);
  assert.throws(() => readMine([{ status: 'built' }]), /Entry 1 of your systems has no name/u);
  assert.throws(() => readMine([{ name: 'Sol', status: 'done' }]), /Entry 1 of your systems \(Sol\) needs a status: built, building or planned/u);
  assert.throws(() => readMine([A_LIST[0], { name: 'X', status: 'building', progress: 140 }]), /Entry 2 of your systems \(X\) has a progress/u);
});

test('a system is found by its exact name among the site\'s answers, whatever the capitals', () => {
  // The site answered "Sol" with Sol, Solati, Solibamba, Solitude and Sollaro.
  assert.deepEqual(NAMED_SOL.map((found) => found.name), ['Sol', 'Solati', 'Solibamba', 'Solitude', 'Sollaro']);
  assert.deepEqual(pickByName(NAMED_SOL, 'sol '), { id64: '10477373803', name: 'Sol', x: 0, y: 0, z: 0 });
  assert.equal(pickByName(NAMED_SOL, 'Solitude').name, 'Solitude');
  assert.equal(pickByName(NAMED_SOL, 'So'), null, 'the start of a name is not the name');
  assert.equal(pickByName([], 'Sol'), null);
});

test('a system takes its star class from the site\'s record of it in its own box', () => {
  assert.equal(systemInBox(SOL_BOX, pickByName(NAMED_SOL, 'Sol')).star, 'G2');
  assert.equal(systemInBox([], pickByName(NAMED_SOL, 'Sol')).star, null);
});

test('one of the player\'s systems is shown on the map like any other, and says its status first', () => {
  const [sol, wregoe] = someOfMine();
  assert.equal(sol.stars.length, 50);
  assert.equal(datasetEntry(sol).collection, 'My systems');
  assert.deepEqual(detailsOf(sol, sol.chosen.id64), {
    description: 'One of your systems. The shaded ball is 15 ly around it.',
    values: [
      { label: 'STATUS', value: 'built' },
      { label: 'STARS IN RANGE', value: '50' },
      { label: 'ID', value: '10477373803', copy: '10477373803' },
    ],
  });
  assert.deepEqual(detailsOf(wregoe, wregoe.chosen.id64).values.slice(0, 2), [
    { label: 'STATUS', value: 'being built' },
    { label: 'PROGRESS', value: '86 %' },
  ]);
});

test('all the player\'s systems are one set, framed together, with a group for each status in use', () => {
  const showings = [...someOfMine(), solShown()];
  const mine = mineAmong(showings);
  assert.equal(mine.length, 3, 'the anchor is not the player\'s');
  const entry = mineEntry(mine);
  assert.equal(entry.id, MINE_ID);
  assert.equal(entry.label, 'All my systems');
  assert.equal(entry.systemCount, 3);
  assert.deepEqual(entry.view, { fit: 'systems', pitch: 35 });

  const content = mineContent(mine);
  assert.deepEqual(content.categories, [
    { name: 'Built', color: STATUSES.built.color, markerStyle: 'disc' },
    { name: 'Being built', color: STATUSES.building.color, markerStyle: 'disc' },
    { name: 'Planned', color: STATUSES.planned.color, markerStyle: 'disc' },
  ]);
  assert.deepEqual(content.systems.map((system) => [system.name, system.categories]), [
    ['Sol', ['Built']], ['Wregoe SJ-I d9-89', ['Being built']], ['HIP 101979', ['Planned']],
  ]);
  assert.deepEqual(content.systems[0], {
    name: 'Sol', id64: '10477373803', coords: { x: 0, y: 0, z: 0 }, categories: ['Built'], primaryStar: 'G2',
  });
  // Each has one group only: its status. So switching "Planned" off hides the planned ones.
  assert.ok(content.systems.every((system) => system.categories.length === 1));
  // A status nobody has gets no group.
  assert.deepEqual(mineContent(mine.slice(0, 1)).categories.map((category) => category.name), ['Built']);
  assert.deepEqual(mine.map(rangeSphere).map((sphere) => sphere.radius), [15, 15, 15]);
});

test('the server only hands out files inside its folder', () => {
  const root = resolve('/srv/site');
  assert.equal(fileFor(root, '/'), join(root, 'index.html'));
  assert.equal(fileFor(root, '/assets/main.js?x=1#top'), join(root, 'assets', 'main.js'));
  assert.equal(fileFor(root, '/data/'), join(root, 'data', 'index.html'));
  assert.equal(fileFor(root, '/a/..hidden/b.js'), join(root, 'a', '..hidden', 'b.js'));
  assert.equal(fileFor(root, '/../secret.txt'), null);
  assert.equal(fileFor(root, '/assets/../../secret.txt'), null);
  assert.equal(fileFor(root, '/%2e%2e/%2e%2e/etc/passwd'), null);
  assert.equal(fileFor(root, '/..\\..\\secret.txt'), null);
  assert.equal(fileFor(root, '/%zz'), null);
  assert.equal(fileFor(root, '/a%00b'), null);
});

test('the server says what kind of file it is handing out', () => {
  assert.equal(typeOf('/x/index.html'), TYPES['.html']);
  assert.equal(typeOf('/x/main.JS'), TYPES['.js']);
  assert.equal(typeOf('/x/point-cloud.worker.js'), 'text/javascript; charset=utf-8');
  assert.equal(typeOf('/x/thing.unknown'), 'application/octet-stream');
});

// The call as it stands in the map library's point-cloud worker (0.7.1).
const WORKER_LINE = 'const e=new URL(`galaxy-detail-D3YNdpKQ.png`,import.meta.url);fetch(e)';

test('the files a worker asks for beside itself are found, and nothing outside its folder', () => {
  assert.deepEqual(filesAskedFor(WORKER_LINE), ['galaxy-detail-D3YNdpKQ.png']);
  assert.deepEqual(filesAskedFor(`new URL("a.png", import.meta.url); new URL('a.png',import.meta.url)`), ['a.png']);
  assert.deepEqual(filesAskedFor('new URL("../up.png", import.meta.url)'), []);
  assert.deepEqual(filesAskedFor('new URL("sub/down.png", import.meta.url)'), []);
  assert.deepEqual(filesAskedFor('new URL(`${name}.png`, import.meta.url)'), []);
  assert.deepEqual(filesAskedFor('new URL("https://example.org/x.png")'), []);
});

test('what the workers ask for is copied next to them, once, and a missing file stops the build', async () => {
  const folder = await mkdtemp(join(tmpdir(), 'almanac-trial-'));
  const built = join(folder, 'built');
  const library = join(folder, 'library');
  await mkdir(built);
  await mkdir(library);
  await writeFile(join(built, 'point-cloud.worker-abc.js'), WORKER_LINE);
  await writeFile(join(built, 'index-abc.js'), 'new URL("not-a-worker.png", import.meta.url)');
  await writeFile(join(library, 'galaxy-detail-D3YNdpKQ.png'), 'the picture');

  assert.deepEqual(await copyWorkerFiles(built, library), ['galaxy-detail-D3YNdpKQ.png']);
  assert.equal(await readFile(join(built, 'galaxy-detail-D3YNdpKQ.png'), 'utf8'), 'the picture');
  assert.deepEqual(await copyWorkerFiles(built, library), []);

  await writeFile(join(built, 'volume.worker-abc.js'), 'new URL("gone.bin", import.meta.url)');
  await assert.rejects(copyWorkerFiles(built, library), /volume\.worker-abc\.js asks for gone\.bin/u);
});

// The first lines of the map's real licence file (LICENSE.md of the package,
// 0.7.1), and the worked example further down in the same file.
const NOTICE = 'Required Notice: Copyright 2026 Dark Session (https://github.com/Elite-Dangerous-Almanac/Galaxy-Map)';
const LICENCE_EXCERPT = `${NOTICE}

# PolyForm Noncommercial License 1.0.0

<https://polyformproject.org/licenses/noncommercial/1.0.0>

beginning with \`Required Notice:\` that the licensor provided
with the software. For example:

> Required Notice: Copyright Yoyodyne, Inc. (http://example.com)
`;

test('the required notice is read from the licence, and its worked example is not', () => {
  assert.deepEqual(requiredNotices(LICENCE_EXCERPT), [NOTICE]);
  assert.deepEqual(requiredNotices(LICENCE_EXCERPT.replaceAll('\n', '\r\n')), [NOTICE]);
  assert.deepEqual(requiredNotices('MIT License\n\nCopyright (c) 2026'), []);
});

test('the page as kept in git shows the required notice and where the licence terms are', () => {
  const page = readFileSync(new URL('./index.html', import.meta.url), 'utf8');
  assert.deepEqual(noticesMissingFrom(page, [NOTICE]), []);
  assert.ok(page.includes('https://polyformproject.org/licenses/noncommercial/1.0.0'));
  assert.ok(page.includes('./licences/galaxy-map-LICENSE.md'));
  assert.deepEqual(noticesMissingFrom('<p>Map by someone</p>', [NOTICE]), [NOTICE]);
  assert.deepEqual(noticesMissingFrom(`<p>Required  Notice: Copyright 2026 <b>Dark Session</b>\n (https://github.com/Elite-Dangerous-Almanac/Galaxy-Map)</p>`, [NOTICE]), []);
});

test('the build puts the licence files beside the page and stops if the page lacks the notice', async () => {
  const folder = await mkdtemp(join(tmpdir(), 'almanac-trial-licence-'));
  const site = join(folder, 'site');
  const modules = join(folder, 'node_modules');
  await mkdir(site);
  for (const [from] of LICENCE_FILES) {
    await mkdir(join(modules, from, '..'), { recursive: true });
    await writeFile(join(modules, from), from === LICENCE_FILES[0][0] ? LICENCE_EXCERPT : `text of ${from}`);
  }
  await writeFile(join(site, 'index.html'), `<p>${NOTICE}</p>`);
  const done = await addLicences(site, modules);
  assert.deepEqual(done.notices, [NOTICE]);
  assert.equal(done.files.length, LICENCE_FILES.length);
  assert.equal(await readFile(join(site, 'licences', 'galaxy-map-LICENSE.md'), 'utf8'), LICENCE_EXCERPT);
  assert.equal(await readFile(join(site, 'licences', 'gl-matrix-LICENSE.md'), 'utf8'), 'text of gl-matrix/LICENSE.md');

  await writeFile(join(site, 'index.html'), '<p>no notice here</p>');
  await assert.rejects(addLicences(site, modules), /The page does not show: Required Notice: Copyright 2026 Dark Session/u);
});

// ---------------------------------------------------------------------------
// The System Map trial.
// ---------------------------------------------------------------------------

// The live site's real answers for Sol and for Polaris (see each file's own note).
const fixture = (name) => JSON.parse(readFileSync(new URL(`./fixtures/${name}.json`, import.meta.url), 'utf8'));
const solTree = () => buildTree(readSystem(fixture('system-sol')));
const polarisTree = () => buildTree(readSystem(fixture('system-polaris')));
// LHS 3447 comes with a fifth answer: the builder's list, with its predicted ground slots.
const lhsTree = () => buildTree(readSystem(fixture('system-lhs3447')));
const named = (tree, name) => [...tree.nodes.values()].find((node) => node.name === name);

test('the copy of our own planet colours still matches the web app, word for word', () => {
  const source = readFileSync(new URL('../../apps/web/src/lib/spatial/system-scene.ts', import.meta.url), 'utf8');
  const palette = source.slice(source.indexOf('export const BODY_FAMILY_PRESENTATIONS = {'), source.indexOf('} as const satisfies Readonly<Record<string, BodyFamilyPresentation>>;'));
  const theirs = {};
  for (const [, key, label, colour] of palette.matchAll(/'?([a-zA-Z-]+)'?:\s*\{\s*label:\s*'([^']+)',\s*colour:\s*\[([0-9., ]+)\],?\s*\}/gu)) {
    theirs[key] = { label, colour: colour.split(',').map(Number) };
  }
  assert.deepEqual(FAMILIES, theirs);
  const rule = source.slice(source.indexOf('export function bodyFamilyFromClass'), source.indexOf('type BodyFamilyPresentation'));
  const classes = [...rule.matchAll(/case '([^']+)':/gu)].map((found) => found[1]);
  assert.deepEqual(Object.keys(FAMILY_OF_CLASS), classes);
  assert.equal(familyOf('Class I gas giant'), 'gas-giant-I');
  assert.equal(familyOf('Earth-like world'), 'earth-like');
  assert.equal(familyOf('something new'), 'unknown');
  assert.equal(familyOf(null), 'unknown');
  assert.ok(isBanded('gas-giant-III') && isBanded('water-giant') && !isBanded('icy'));
});

test('Sol is read as one star with its planets in a row, going outward', () => {
  const tree = solTree();
  assert.equal(tree.name, 'Sol');
  assert.deepEqual(tree.stars.map((star) => star.name), ['Sol']);
  // Pluto and Charon circle each other; our data gives the pair no parent, so it is placed by distance.
  assert.deepEqual(tree.stars[0].children.map((child) => child.short), [
    'Mercury', 'Venus', 'Earth', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune', 'barycentre 31', '90482 Orcus',
    '(307261) 2002 MS4', 'Salacia', 'Haumea', 'Quaoar', 'Makemake', '(225088) 2007 OR10', 'Eris', 'Sedna', 'Persephone',
  ]);
  const pair = named(tree, 'Sol barycentre 31');
  assert.equal(pair.guessedParent, 'distance');
  assert.deepEqual(pair.children.map((child) => child.name).sort(), ['Charon', 'Pluto']);
  assert.equal(named(tree, 'Earth').guessedParent, false);
  // Moons go by the size of their orbit, not by their distance from the arrival point.
  assert.deepEqual(named(tree, 'Jupiter').children.map((moon) => moon.name), ['Io', 'Europa', 'Ganymede', 'Callisto']);
  assert.equal(named(tree, 'Saturn').rings.length, 1);
  assert.equal(tree.stars[0].belts.length, 1);
});

test('a body\'s name loses the system\'s name from its front, and nothing else', () => {
  assert.equal(shortName({ name: 'Sol barycentre 31' }, 'Sol'), 'barycentre 31');
  assert.equal(shortName({ name: 'Solati 2' }, 'Sol'), 'Solati 2');
  assert.equal(shortName({ name: 'Earth' }, 'Sol'), 'Earth');
});

test('what is built is put at its body: a fact where our data says, a marked guess where it does not', () => {
  const tree = solTree();
  const all = [...tree.nodes.values()];
  // Sites on the ground come with their body: 53 of them, none a guess.
  const ground = all.flatMap((node) => node.ground);
  assert.equal(ground.length, 53);
  assert.ok(ground.every((site) => site.guess === false));
  assert.equal(named(tree, 'Callisto').ground.length, 7);
  // Our data ties none of Sol's stations in orbit to a body. Eight sit close enough to one by distance.
  const orbit = all.flatMap((node) => node.orbit.map((station) => [node.name, station.name, station.mark, station.guess]));
  assert.deepEqual(orbit.sort((one, other) => one[1].localeCompare(other[1])), [
    ['Earth', 'Abraham Lincoln', 'starport', true],
    ['Venus', 'Burnell Station', 'outpost', true],
    ['Jupiter', 'Columbus', 'starport', true],
    ['Mercury', 'Daedalus', 'starport', true],
    ['Moon', 'Galileo', 'starport', true],
    ['Earth', 'Li Qing Jao', 'starport', true],
    ['Mars', 'Mars High', 'starport', true],
    ['Titan', 'Titan City', 'starport', true],
  ]);
  // Two are not close enough to any body, and are said to be not placed.
  assert.deepEqual(tree.notPlaced.map((station) => station.name).sort(), ['M.Gorbachev', 'Rescue Ship - Li Qing Jao']);
  // Four have a body but no kind in our data: claimed neither for orbit nor for the ground.
  assert.deepEqual(all.flatMap((node) => node.unknownKind.map((station) => station.name)).sort(), [
    'Chargaff Reach', 'Daimler Camp', "Fung's Claim", 'Illy Enterprise',
  ]);
});

test('a station is near enough to a body within 3 light seconds, or half a percent when far out', () => {
  assert.ok(nearEnough(159.72, 159.66));
  assert.ok(nearEnough(506.91, 504.0));
  assert.ok(!nearEnough(506.91, 502.09), 'M.Gorbachev, 4.8 ls from Earth in our data');
  assert.ok(nearEnough(9143.99, 9180));
  assert.ok(!nearEnough(9143.99, 9284));
});

test('with two stars, each heads a row in the game\'s own order, and a moon is hung by its name', () => {
  const tree = polarisTree();
  assert.deepEqual(tree.stars.map((star) => star.name), ['Polaris A', 'Polaris B']);
  assert.equal(tree.stars[0].children.length, 0);
  const b1 = named(tree, 'Polaris B 1');
  // "B 1 d" and "B 1 e" come with no parent; their names say they are moons of "B 1".
  assert.equal(named(tree, 'Polaris B 1 d').guessedParent, 'name');
  assert.ok(b1.children.includes(named(tree, 'Polaris B 1 d')) && b1.children.includes(named(tree, 'Polaris B 1 e')));
  assert.deepEqual(b1.children.map((moon) => moon.short).sort(), ['B 1 a', 'B 1 b', 'B 1 c', 'B 1 d', 'B 1 e', 'B 1 f', 'B 1 g']);
});

test('sizes are for reading: a star is largest, a giant larger than a rock, and nothing vanishes', () => {
  const tree = solTree();
  const size = (name) => Number(drawnRadius(named(tree, name)).toFixed(1));
  assert.deepEqual(['Sol', 'Jupiter', 'Earth', 'Moon', 'Enceladus'].map(size), [46, 24.5, 8.2, 4.6, 3.5]);
  const polaris = polarisTree();
  assert.ok(drawnRadius(named(polaris, 'Polaris A')) > drawnRadius(named(polaris, 'Polaris B')));
  assert.ok(drawnRadius(named(polaris, 'Polaris A')) <= 78);
});

test('what hangs from a body comes in order: what is in orbit, then its children with belts among them', () => {
  const tree = solTree();
  assert.deepEqual(entriesOf(named(tree, 'Earth')).map((entry) => entry.station?.name ?? entry.node.name), [
    'Abraham Lincoln', 'Li Qing Jao', 'Moon',
  ]);
  const row = entriesOf(tree.stars[0]);
  assert.equal(row.filter((entry) => entry.kind === 'belt').length, 1);
  // The pair is not drawn itself: Pluto and Charon take its place, marked as one pair.
  const pair = row.filter((entry) => entry.pair);
  assert.deepEqual(pair.map((entry) => entry.node.name).sort(), ['Charon', 'Pluto']);
  assert.equal(new Set(pair.map((entry) => entry.pair)).size, 1);
});

test('every body is drawn once, none on top of another, in the manner of the game\'s map', () => {
  for (const tree of [solTree(), polarisTree(), lhsTree()]) {
    const layout = layoutOf(tree);
    const bodies = layout.items.filter((item) => item.kind === 'body');
    const real = [...tree.nodes.values()].filter((node) => node.type !== 'barycentre');
    assert.equal(bodies.length, real.length);
    assert.equal(new Set(bodies.map((item) => item.id)).size, bodies.length);
    for (let one = 0; one < bodies.length; one += 1) {
      for (let other = one + 1; other < bodies.length; other += 1) {
        const apart = Math.hypot(bodies[one].x - bodies[other].x, bodies[one].y - bodies[other].y);
        assert.ok(apart >= bodies[one].r + bodies[other].r, `${bodies[one].node.name} and ${bodies[other].node.name} overlap`);
      }
    }
    for (const item of layout.items) {
      assert.ok(item.x >= layout.box.x0 && item.x <= layout.box.x1 && item.y >= layout.box.y0 && item.y <= layout.box.y1);
    }
  }
  const layout = layoutOf(solTree());
  const at = (name) => layout.items.find((item) => item.kind === 'body' && item.node.name === name);
  // Planets run to the right of the star on its line; moons hang straight below their planet.
  assert.ok(at('Mercury').x > at('Sol').x && at('Earth').x > at('Venus').x && at('Persephone').x > at('Neptune').x);
  assert.equal(at('Earth').y, at('Sol').y);
  assert.equal(at('Moon').x, at('Earth').x);
  assert.ok(at('Moon').y > at('Earth').y);
  assert.ok(at('Callisto').y > at('Ganymede').y && at('Ganymede').y > at('Europa').y && at('Europa').y > at('Io').y);
  // Earth's two stations hang between it and the Moon.
  const stations = layout.items.filter((item) => item.kind === 'station' && item.x === at('Earth').x);
  assert.deepEqual(stations.map((item) => item.station.name), ['Abraham Lincoln', 'Li Qing Jao']);
  assert.ok(stations.every((item) => item.y > at('Earth').y && item.y < at('Moon').y));
  // Polaris: the two stars are stacked, their centres in one column, joined by a bracket.
  const two = layoutOf(polarisTree());
  const stars = two.items.filter((item) => item.kind === 'body' && item.node.type === 'star');
  assert.equal(stars[0].x, stars[1].x);
  assert.ok(stars[1].y > stars[0].y + stars[0].r + stars[1].r);
  assert.ok(two.lines.some((line) => line.kind === 'pair' && line.x1 === line.x2 && line.x1 < stars[0].x));
});

test('a body is coloured and named by its class, by our own rules', () => {
  const tree = solTree();
  assert.deepEqual(lookOf(named(tree, 'Earth')).colour, [0.16, 0.58, 0.57]);
  assert.equal(lookOf(named(tree, 'Jupiter')).banded, true);
  assert.equal(lookOf(named(tree, 'Earth')).banded, false);
  const sol = lookOf(named(tree, 'Sol'));
  assert.ok(sol.star && !sol.dark);
  assert.deepEqual(sol.colour, [1, 0.98, 0.66], 'the G class core of apps/web/src/lib/spatial/stellar-presentation.ts');
  assert.equal(spokenOf(named(tree, 'Earth')), 'Earth, Earth-like world; 2 in orbit');
  assert.equal(spokenOf(named(tree, 'Saturn')), 'Saturn, Class I gas giant; icy ring');
  assert.equal(spokenOf(named(tree, 'Europa')), 'Europa, Icy body; 2 on the ground');
  const daedalus = named(tree, 'Mercury').orbit[0];
  assert.equal(spokenOfStation(daedalus), 'Daedalus, orbis starport, placed by its distance: our data does not say which body');
});

test('the picture holds every body and station once, marks each guess, and lets no name break it', () => {
  const picture = pictureOf(layoutOf(solTree()));
  assert.ok(picture.startsWith('<svg xmlns="http://www.w3.org/2000/svg"'));
  assert.equal(picture.match(/class="sm-body/gu).length, 40);
  assert.equal(picture.match(/data-station="/gu).length, 8);
  assert.equal(picture.match(/class="sm-station sm-guess"/gu).length, 8, 'all eight are placed by distance');
  assert.equal(picture.match(/class="sm-belt"/gu).length, 1);
  // Four ringed planets, each with a ring behind, a ring in front and the ring's shadow.
  assert.equal(picture.match(/rotate\(-12\)/gu).length, 12);
  assert.ok(picture.includes('aria-label="Earth, Earth-like world; 2 in orbit"'));
  assert.ok(picture.includes('&#9650;7'), 'Callisto shows its seven sites on the ground');
  // A name from data cannot close a tag or open one.
  assert.equal(safe(`<b a="1" c='2'>&`), '&lt;b a=&quot;1&quot; c=&#39;2&#39;&gt;&amp;');
  assert.ok(picture.includes('Fung&#39;s Claim') || !picture.includes("Fung's Claim"));
  const raw = fixture('system-sol');
  raw.bodies = raw.bodies.map((body) => (body.name === 'Earth' ? { ...body, name: 'Earth<script>x</script>' } : body));
  const unsafe = pictureOf(layoutOf(buildTree(readSystem(raw))));
  assert.ok(!unsafe.includes('<script>') && unsafe.includes('Earth&lt;script&gt;x&lt;/script&gt;'));
});

test('ground slots are the site\'s prediction, or the reason there is none, and never a made-up number', () => {
  assert.deepEqual(groundSlotsOf({ prediction_status: 'predicted', predicted_surface_slots: 3 }), { count: 3, why: 'predicted', missing: [] });
  assert.deepEqual(
    groundSlotsOf({ prediction_status: 'unknown', predicted_surface_slots: null, missing_inputs: ['atmosphere'] }),
    { count: null, why: 'facts missing', missing: ['atmosphere'] },
  );
  assert.deepEqual(groundSlotsOf({ prediction_status: null, is_landable: false }), { count: null, why: 'cannot land', missing: [] });
  assert.deepEqual(groundSlotsOf({ prediction_status: null, is_landable: null }), { count: null, why: 'no prediction', missing: [] });
  assert.deepEqual(groundSlotsOf(undefined), { count: null, why: 'not asked', missing: [] });
  // A count that is said to be predicted but is no whole number is not passed on.
  assert.equal(groundSlotsOf({ prediction_status: 'predicted', predicted_surface_slots: null }).count, null);

  const tree = lhsTree();
  const all = [...tree.nodes.values()];
  const predicted = all.filter((node) => node.groundSlots.why === 'predicted').map((node) => [node.short, node.groundSlots.count]);
  assert.deepEqual(predicted.sort(), [['A 3', 4], ['B 3 a', 3], ['B 3 b', 3], ['B 3 c', 3], ['B 3 d', 3], ['B 4 d', 3]]);
  // Of the 23 bodies that can be landed on, the site predicts for 6. For the
  // other 17 our data has no atmosphere, and the site says so.
  const landable = all.filter((node) => node.landable);
  assert.equal(landable.length, 23);
  const lacking = landable.filter((node) => node.groundSlots.why === 'facts missing');
  assert.equal(lacking.length, 17);
  assert.ok(lacking.every((node) => node.groundSlots.missing.join() === 'atmosphere'));
  assert.equal(named(tree, 'LHS 3447 B 3').groundSlots.why, 'cannot land');
  // Sol's fixture has no builder's list: nothing is claimed for any body.
  assert.ok([...solTree().nodes.values()].every((node) => node.groundSlots.why === 'not asked' && node.groundSlots.count === null));
});

test('sites on the ground and ground slots are two numbers side by side, not a fraction', () => {
  const tree = lhsTree();
  const mark = (name) => {
    const found = groundMarkOf(named(tree, name));
    return found && [found.built, found.slots];
  };
  // Our data holds five sites on A 3 and the site predicts four slots: the
  // two do not count the same thing, so neither is shown as part of the other.
  assert.deepEqual(mark('LHS 3447 A 3'), [5, 4]);
  assert.deepEqual(mark('LHS 3447 B 3 b'), [3, 3]);
  assert.deepEqual(mark('LHS 3447 A 2'), [5, null], 'sites, and no prediction');
  assert.equal(mark('LHS 3447 B 3'), null, 'a gas giant has neither');
  const layout = layoutOf(tree);
  const marked = layout.items.filter((item) => item.kind === 'body' && item.mark);
  assert.equal(marked.length, 21);
  for (const item of marked) {
    // The mark sits below the body's own line and inside the picture.
    assert.ok(item.mark.y >= 12.5 && item.mark.x > 0 && item.mark.w > 0);
    assert.ok(item.x + item.mark.x + item.mark.w <= layout.box.x1 && item.y + item.mark.y <= layout.box.y1, item.node.name);
  }
  const picture = pictureOf(layout);
  assert.equal(picture.match(/class="sm-slots"/gu).length, 6);
  assert.ok(picture.includes('<tspan>&#9650;5</tspan><tspan class="sm-slots" dx="3">&#9633;4</tspan>'));
  assert.ok(!/&#9650;\d+\//u.test(picture), 'no fraction');
  assert.equal(spokenOf(named(tree, 'LHS 3447 A 3')), 'A 3, High metal content world; 1 in orbit; 5 on the ground; 4 ground slots predicted');
  // Without the builder's list there is no square anywhere.
  assert.ok(!pictureOf(layoutOf(solTree())).includes('sm-slots'));
});

test('each ring and belt says what kind it is, in words as well as in colour', () => {
  const ring = (ringClass) => ({ ringClass });
  assert.equal(ringsInWords([]), null);
  assert.equal(ringsInWords([ring('icy')]), 'icy ring');
  assert.equal(ringsInWords([ring('metal_rich'), ring('metal_rich')]), '2 metal rich rings');
  assert.equal(ringsInWords([ring('metal_rich'), ring('icy')]), 'rings: metal rich, icy');
  assert.equal(ringsInWords([ring(null)]), 'ring, kind not known');
  assert.equal(ringsInWords([ring('icy'), ring('something new')]), 'rings: icy, not known');
  assert.deepEqual(Object.keys(RING_COLOURS), Object.keys(RING_KINDS), 'a colour for every kind we name');
  assert.equal(new Set(Object.values(RING_COLOURS).map(String)).size, 4, 'and no two alike');

  const sol = solTree();
  assert.deepEqual(
    ['Jupiter', 'Saturn', 'Uranus', 'Neptune'].map((name) => named(sol, name).ringWords),
    ['rocky ring', 'icy ring', 'icy ring', 'icy ring'],
  );
  assert.equal(named(sol, 'Earth').ringWords, null);
  // The words are on the picture: under each ringed planet's name, and over the belt.
  const captions = layoutOf(sol).labels.filter((label) => label.small).map((label) => label.text);
  assert.deepEqual(captions, ['rocky ring', 'icy ring', 'icy ring', 'icy ring', 'rocky', 'belt']);
  const picture = pictureOf(layoutOf(sol));
  assert.equal(picture.match(/class="sm-caption"/gu).length, 6);
  assert.ok(picture.includes('<title>Asteroid Belt, rocky belt</title>'));
  assert.ok(picture.includes(`fill="rgb(${RING_COLOURS.rocky.join(',')})"`) && picture.includes(`fill="rgb(${RING_COLOURS.icy.join(',')})"`));

  // LHS 3447 B 2 has two rings of different kinds: two bands, the inner one first, not touching.
  const lhs = lhsTree();
  const b2 = named(lhs, 'LHS 3447 B 2');
  assert.deepEqual(b2.rings.map((one) => one.name), ['LHS 3447 B 2 A Ring', 'LHS 3447 B 2 B Ring']);
  assert.equal(b2.ringWords, 'rings: metal rich, icy');
  const bands = ringBandsOf(b2);
  assert.deepEqual(bands.map((band) => band.kind), ['metal rich', 'icy']);
  assert.deepEqual(bands.map((band) => band.colour), [RING_COLOURS.metal_rich, RING_COLOURS.icy]);
  assert.ok(bands[0].from >= 1.3 && bands[0].to < bands[1].from && bands[1].to <= 2.1 + 1e-9);
  assert.equal(spokenOf(b2), 'B 2, Gas giant with ammonia-based life; rings: metal rich, icy');
  assert.deepEqual(
    layoutOf(lhs).labels.filter((label) => label.small).map((label) => label.text),
    ['icy ring', 'metallic', 'belt', 'rings: metal rich, icy', 'icy ring', 'metallic', 'belt'],
  );
});

// ---------------------------------------------------------------------------
// Painted surfaces: each body's picture, made by arithmetic.
// ---------------------------------------------------------------------------

/** How bright the solid pixels of a square part of a picture are, on average (0 to 255). */
function brightness({ size, data }, x0, y0, x1, y1) {
  let total = 0;
  let count = 0;
  for (let y = Math.round(y0 * size); y < Math.round(y1 * size); y += 1) {
    for (let x = Math.round(x0 * size); x < Math.round(x1 * size); x += 1) {
      const at = (y * size + x) * 4;
      if (data[at + 3] !== 255) continue;
      total += 0.2126 * data[at] + 0.7152 * data[at + 1] + 0.0722 * data[at + 2];
      count += 1;
    }
  }
  return total / count;
}

const painted = (family, extra = {}) => surface({
  seed: seedOf(`body of ${family}`), size: 96, family, colour: FAMILIES[family]?.colour ?? [1, 0.98, 0.66], ...extra,
});

test('a body is painted as a ball: solid inside, clear outside, and the same every time', () => {
  const rock = painted('rocky');
  assert.equal(rock.data.length, 96 * 96 * 4);
  const solid = (x, y) => rock.data[(y * 96 + x) * 4 + 3];
  assert.equal(solid(0, 0), 0, 'a corner is outside the ball');
  assert.equal(solid(95, 95), 0);
  assert.equal(solid(48, 48), 255, 'the middle is inside');
  assert.ok(solid(48, 0) > 0 && solid(48, 0) < 255, 'the edge is soft, not a staircase');
  // The same body gives the same picture; another body of the same class does not.
  assert.deepEqual(painted('rocky').data, rock.data);
  assert.notDeepEqual(surface({ seed: seedOf('another body'), size: 96, family: 'rocky', colour: FAMILIES.rocky.colour }).data, rock.data);
  assert.equal(seedOf('Sol'), seedOf('Sol'));
  assert.notEqual(seedOf('Sol'), seedOf('Sol 2'));
});

test('a planet is lit from the upper left and dark on the far side; a star is not shaded', () => {
  for (const family of ['rocky', 'icy', 'earth-like', 'gas-giant-I', 'water-world', 'ammonia-world', 'metal-rich']) {
    const picture = painted(family);
    const lit = brightness(picture, 0.2, 0.2, 0.45, 0.45);
    const far = brightness(picture, 0.55, 0.55, 0.8, 0.8);
    assert.ok(lit > far * 1.3, `${family}: lit ${lit.toFixed(0)}, far side ${far.toFixed(0)}`);
  }
  const star = painted('star', { star: true, halo: [1, 0.6, 0.2] });
  const one = brightness(star, 0.2, 0.2, 0.45, 0.45);
  const other = brightness(star, 0.55, 0.55, 0.8, 0.8);
  assert.ok(Math.abs(one - other) < 12, 'a star shines the same all round');
  // ...and is darker toward its edge than in its middle.
  assert.ok(brightness(star, 0.4, 0.4, 0.6, 0.6) > brightness(star, 0.44, 0.02, 0.56, 0.08));
  // A black hole is a black ball.
  const hole = painted('star', { star: true, dark: true });
  assert.equal(brightness(hole, 0.3, 0.3, 0.7, 0.7), 0);
});

test('the class decides the pattern: bands on a gas giant, sea and land on a world with seas', () => {
  assert.equal(patternOf('gas-giant-III'), 'bands');
  assert.equal(patternOf('earth-like'), 'seas');
  assert.equal(patternOf('rocky'), 'ground');
  assert.equal(patternOf('a class not seen before'), 'ground');
  // Every class our own map draws with bands is painted with bands here, and no other.
  for (const family of Object.keys(FAMILIES)) {
    assert.equal(patternOf(family) === 'bands', isBanded(family), family);
  }
  assert.deepEqual(Object.keys(FAMILIES).filter((family) => patternOf(family) === 'seas'), ['earth-like', 'water-world', 'ammonia-world']);

  // Bands run across the ball: going down it the picture changes more than
  // going along it. Bare ground changes about the same both ways.
  const downOverAcross = (picture) => {
    const { size, data } = picture;
    const bright = (at) => 0.2126 * data[at] + 0.7152 * data[at + 1] + 0.0722 * data[at + 2];
    let down = 0;
    let across = 0;
    for (let y = 29; y < 67; y += 1) {
      for (let x = 29; x < 67; x += 1) {
        const at = (y * size + x) * 4;
        down += Math.abs(bright(at + size * 4 * 3) - bright(at));
        across += Math.abs(bright(at + 4 * 3) - bright(at));
      }
    }
    return down / across;
  };
  for (const family of Object.keys(FAMILIES).filter(isBanded)) {
    const ratio = downOverAcross(painted(family));
    assert.ok(ratio > 1.2, `${family}: ${ratio.toFixed(2)}`);
  }
  for (const family of ['rocky', 'icy', 'metal-rich', 'high-metal-content']) {
    const ratio = downOverAcross(painted(family));
    assert.ok(ratio > 0.85 && ratio < 1.18, `${family}: ${ratio.toFixed(2)}`);
  }

  // A world with seas shows sea, land and cloud: many more colours than bare rock of one colour.
  const colours = (picture) => {
    const seen = new Set();
    for (let at = 0; at < picture.data.length; at += 4) {
      if (picture.data[at + 3] === 255) seen.add(`${picture.data[at] >> 5},${picture.data[at + 1] >> 5},${picture.data[at + 2] >> 5}`);
    }
    return seen.size;
  };
  assert.ok(colours(painted('earth-like')) > colours(painted('rocky')) * 1.5);
});

test('a picture is as big as the body is drawn, within bounds', () => {
  assert.deepEqual([3.5, 8, 24, 46, 78].map(pixelsFor), [40, 48, 144, 256, 256]);
});

test('the page may give each body a painted picture; without one the plain ball is drawn', () => {
  const layout = layoutOf(solTree());
  const plain = pictureOf(layout);
  assert.ok(!plain.includes('<image'));
  const asked = [];
  const withPictures = pictureOf(layout, 28, (item, look) => {
    asked.push(item.node.name);
    return item.node.name === 'Pluto' ? null : `data:image/png;base64,${look.star ? 'STAR' : 'BODY'}`;
  });
  assert.equal(asked.length, 40, 'every body is offered one');
  assert.equal(withPictures.match(/<image class="sm-surface"/gu).length, 39);
  assert.ok(withPictures.includes('href="data:image/png;base64,STAR"'));
  // Still every body an element that can be clicked and read aloud, and rings and marks as before.
  assert.equal(withPictures.match(/class="sm-body/gu).length, 40);
  assert.equal(withPictures.match(/class="sm-ring"/gu).length, plain.match(/class="sm-ring"/gu).length);
  assert.ok(withPictures.includes('aria-label="Earth, Earth-like world; 2 in orbit"'));
  // An address from anywhere cannot break out of the picture.
  assert.ok(!pictureOf(layout, 28, () => '"><script>x</script>').includes('<script>'));
});
