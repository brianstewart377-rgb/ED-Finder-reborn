// Run with:  node --test   (in this folder). Nothing to install first.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  ANCHORS, OPENING_DISTANCE_LY, RANGE_LY, RESULTS_OFFERED, anchorInBox, boxAround, contentOf, datasetEntry,
  detailsOf, inspectAddress, rangeSphere, showing, starCategories, starGroup, starsInRange, trimRow,
} from './src/logic.mjs';
import { TYPES, fileFor, typeOf } from './serve-files.mjs';
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
