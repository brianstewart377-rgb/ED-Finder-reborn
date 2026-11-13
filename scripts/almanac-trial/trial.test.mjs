// Run with:  node --test   (in this folder). Nothing to install first.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  ANCHORS, ANCHOR_GROUP, FIND_PAGE, OPENING_DISTANCE_LY, RESULT_GROUP, SETS, STARS_SHOW_WITHIN_LY,
  anchorRecord, contentOf, dataFile, datasetEntry, detailsOf, inspectAddress, madeUpRecords,
  starCategories, starGroup, starsShownAt, toRecord, trimRow,
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

test('a candidate goes to the map with its id as text, its position and its star\'s group', () => {
  assert.deepEqual(toRecord(trimRow(WREGOE)), {
    name: 'Wregoe SJ-I d9-89', id64: '3068561197395',
    coords: { x: 308.4375, y: -23.25, z: -253.0625 },
    categories: ['F class star'], primaryStar: 'F (White) Star', bodyCount: 18,
  });
  assert.deepEqual(toRecord(trimRow(WREGOE), RESULT_GROUP).categories, ['Find results']);
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

test('the panel says the score, the tier, both uses, the claim and the id', () => {
  const details = detailsOf(trimRow(WREGOE), 'Sol');
  assert.deepEqual(details.values, [
    { label: 'SCORE', value: '100' },
    { label: 'TIER', value: 'S' },
    { label: 'BEST AS', value: 'flexible' },
    { label: 'ALSO SUITS', value: 'stronghold' },
    { label: 'CLAIM', value: 'free' },
    { label: 'ID', value: '3068561197395', copy: '3068561197395' },
  ]);
  assert.equal(details.description, 'One of the first 10,000 candidates Find lists within 500 ly of Sol.');
});

test('the panel adds the distance from the anchor only when the anchor is not Sol', () => {
  // The map's own panel always shows the distance from Sol.
  const labels = (anchor) => detailsOf(trimRow(WREGOE), anchor).values.map((value) => value.label);
  assert.ok(!labels('Sol').some((label) => label.startsWith('FROM')));
  const colonia = detailsOf(trimRow({ ...WREGOE, distance: 4.5 }), 'Colonia');
  assert.deepEqual(colonia.values[4], { label: 'FROM COLONIA', value: '4.5 ly' });
  assert.ok(colonia.values.length <= 12, 'the panel takes at most 12 values');
});

test('a code from Find is shown in plain words', () => {
  const row = trimRow({ ...WREGOE, secondary_archetype: 'manufacturing_hub', claim_status: 'claim_ended_unknown' });
  const byLabel = Object.fromEntries(detailsOf(row, 'Sol').values.map((value) => [value.label, value.value]));
  assert.equal(byLabel['ALSO SUITS'], 'manufacturing hub');
  assert.equal(byLabel.CLAIM, 'claim ended unknown');
});

test('the Inspect button opens the system on the live site', () => {
  // https://ed-finder.app/s/<id64> answered 302 to /inspect?system=<id64> on 13 Nov 2026.
  assert.equal(inspectAddress('3068561197395'), 'https://ed-finder.app/s/3068561197395');
});

test('the map is offered four sets: each anchor\'s results alone, and among stars', () => {
  const entries = SETS.map(datasetEntry);
  assert.deepEqual(entries.map((entry) => [entry.id, entry.label]), [
    ['sol', 'Results near Sol'],
    ['sol-stars', 'Results near Sol, among stars'],
    ['colonia', 'Results near Colonia'],
    ['colonia-stars', 'Results near Colonia, among stars'],
  ]);
  assert.deepEqual(entries[2].view.cursor, [-9530.5, -910.28125, 19808.125]);
  assert.equal(entries[2].view.distance, OPENING_DISTANCE_LY);
  assert.ok(entries[0].description.startsWith('The first 50 results Find lists within 500 light years of Sol'));
  assert.ok(entries[1].description.includes('10,000 candidates') && entries[1].description.includes('star class'));
  for (const entry of entries) assert.equal(entry.load, undefined, 'the page adds the loader');
});

// The real first 60 rows of Find's answer near Sol (see the file's own note).
const FIRST_60 = JSON.parse(readFileSync(new URL('./fixtures/find-sol-first-60.json', import.meta.url), 'utf8')).results;
const sixtyRows = () => dataFile(ANCHORS.sol, FIRST_60, '2026-11-13');

test('a set of results alone holds the anchor and Find\'s first page, all in one group', () => {
  const content = contentOf(sixtyRows(), false);
  assert.equal(FIND_PAGE, 50);
  assert.equal(content.systems.length, 1 + FIND_PAGE);
  assert.deepEqual(content.systems[0], { name: 'Sol', coords: { x: 0, y: 0, z: 0 }, categories: [ANCHOR_GROUP] });
  assert.equal(content.systems[1].name, 'Wregoe SJ-I d9-89');
  assert.equal(content.systems[50].name, 'Col 285 Sector FM-U c3-4');
  assert.ok(content.systems.slice(1).every((system) => system.categories[0] === RESULT_GROUP));
  assert.deepEqual(content.categories.map((category) => category.name), [RESULT_GROUP, ANCHOR_GROUP]);
});

test('a set among stars adds every other candidate in its star\'s group', () => {
  const content = contentOf(sixtyRows(), true);
  assert.equal(content.systems.length, 1 + 60);
  const others = content.systems.slice(1 + FIND_PAGE);
  // Rows 51 to 60 as Find gave them: four G stars, three M, two F and one K.
  assert.deepEqual(others.map((system) => system.name), [
    'Synuefai SZ-M d8-44', 'Wregoe YL-L c21-28', 'R CrA Sector AF-A d92', 'Synuefe DT-O d7-60',
    'Col 285 Sector AR-D c12-1', 'Synuefe PI-H b30-0', 'Col 359 Sector IG-X d1-92', 'Synuefe HN-Y b34-2',
    'Synuefe OF-L d9-29', 'Praea Euq CE-K b8-12',
  ]);
  assert.deepEqual(content.categories.map((category) => category.name), [
    RESULT_GROUP, 'G class star', 'M class star', 'F class star', 'K class star', ANCHOR_GROUP,
  ]);
  const counts = {};
  for (const system of others) counts[system.categories[0]] = (counts[system.categories[0]] ?? 0) + 1;
  assert.deepEqual(counts, { 'G class star': 4, 'M class star': 3, 'F class star': 2, 'K class star': 1 });
  assert.equal(new Set(content.categories.map((category) => category.name)).size, content.categories.length);
});

test('stars draw close up and not from far out, and a set opens with them not drawn', () => {
  assert.equal(STARS_SHOW_WITHIN_LY, 400);
  assert.equal(starsShownAt(60), true);
  assert.equal(starsShownAt(400), true);
  assert.equal(starsShownAt(401), false);
  assert.equal(starsShownAt(OPENING_DISTANCE_LY), false);
});

test('made-up systems are the same each time, stay in their cube and have their own group', () => {
  const first = madeUpRecords(500, { x: 100, y: -50, z: 20 }, 1000);
  assert.deepEqual(first, madeUpRecords(500, { x: 100, y: -50, z: 20 }, 1000));
  assert.equal(new Set(first.map((record) => record.name)).size, 500);
  for (const record of first) {
    assert.deepEqual(record.categories, ['Made-up']);
    assert.ok(Math.abs(record.coords.x - 100) <= 500 && Math.abs(record.coords.y + 50) <= 500
      && Math.abs(record.coords.z - 20) <= 500);
  }
  assert.ok(!contentOf(sixtyRows(), true).categories.some((category) => category.name === 'Made-up'));
});

test('the data file keeps each system once and says where and when it came from', () => {
  const file = dataFile(ANCHORS.sol, [WREGOE, WREGOE, { ...WREGOE, id64: 7, name: 'Other' }, { name: 'no id' }], '2026-11-13');
  assert.deepEqual(file.anchor, { name: 'Sol', x: 0, y: 0, z: 0 });
  assert.equal(file.fetchedOn, '2026-11-13');
  assert.deepEqual(file.rows.map((row) => row.name), ['Wregoe SJ-I d9-89', 'Other']);
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
