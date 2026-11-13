// Run with:  node --test   (in this folder). Nothing to install first.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  ANCHORS, GROUPS, OTHER_GROUP, OPENING_DISTANCE_LY, anchorRecord, categories, categoryOf, contentOf,
  dataFile, datasetEntry, detailsOf, inspectAddress, madeUpRecords, toRecord, trimRow,
} from './src/logic.mjs';
import { TYPES, fileFor, typeOf } from './serve-files.mjs';
import { copyWorkerFiles, filesAskedFor } from './worker-files.mjs';
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

test('a candidate goes to the map with its id as text, its position and one group', () => {
  assert.deepEqual(toRecord(trimRow(WREGOE)), {
    name: 'Wregoe SJ-I d9-89', id64: '3068561197395',
    coords: { x: 308.4375, y: -23.25, z: -253.0625 },
    categories: ['Also a stronghold'], primaryStar: 'F (White) Star', bodyCount: 18,
  });
});

test('every group a candidate can be given is one the map was told about', () => {
  const told = new Set(categories().map((category) => category.name));
  for (const group of GROUPS) assert.ok(told.has(categoryOf({ second: group.second })), group.second);
  assert.equal(categoryOf({ second: 'something_new' }), OTHER_GROUP);
  assert.equal(categoryOf({ second: null }), OTHER_GROUP);
  assert.ok(told.has(OTHER_GROUP) && told.has(anchorRecord(ANCHORS.sol).categories[0]));
  assert.equal(new Set(categories().map((category) => category.name)).size, categories().length);
  for (const category of categories()) {
    assert.ok(category.color.length === 3 && category.color.every((part) => part >= 0 && part <= 255));
  }
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
  assert.equal(details.description, 'One of the candidates Find lists within 500 ly of Sol.');
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

test('each anchor is one entry of the map\'s own list of sets, opening on its anchor', () => {
  const sol = datasetEntry('sol', ANCHORS.sol);
  const colonia = datasetEntry('colonia', ANCHORS.colonia);
  assert.equal(sol.id, 'sol');
  assert.equal(sol.label, 'Candidates near Sol');
  assert.equal(colonia.label, 'Candidates near Colonia');
  assert.deepEqual(colonia.view.cursor, [-9530.5, -910.28125, 19808.125]);
  assert.equal(colonia.view.distance, OPENING_DISTANCE_LY);
  assert.ok(sol.description.includes('first 10,000') && sol.description.includes('Sol'));
  assert.equal(sol.load, undefined, 'the page adds the loader');
});

test('a set hands the map its groups, the anchor first and then every candidate', () => {
  const data = dataFile(ANCHORS.colonia, [WREGOE, { ...WREGOE, id64: 7, name: 'Other', secondary_archetype: null }], '2026-11-13');
  const content = contentOf(data);
  assert.deepEqual(content.categories, categories());
  assert.deepEqual(content.systems.map((system) => system.name), ['Colonia', 'Wregoe SJ-I d9-89', 'Other']);
  assert.deepEqual(content.systems[0], {
    name: 'Colonia', coords: { x: -9530.5, y: -910.28125, z: 19808.125 }, categories: ['Anchor'],
  });
  const told = new Set(content.categories.map((category) => category.name));
  for (const system of content.systems) assert.ok(told.has(system.categories[0]), system.name);
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
  assert.ok(!categories().some((category) => category.name === 'Made-up'));
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
