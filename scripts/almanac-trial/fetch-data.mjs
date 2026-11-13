#!/usr/bin/env node
// Fetches what the trial page shows, from the live site, and writes
// public/data/shown.json. For each anchor (Sol, Colonia): the anchor itself
// and the first few results Find lists near it, each with every real star
// within 15 ly.
//
// If the file my-systems.local.json is here, the player's own systems in it
// are looked up by name and fetched the same way, for the "My systems" view.
// my-systems.example.json shows the form.
//
//     node fetch-data.mjs
//
// It also writes public/data/systems.json for the System Map trial: the
// bodies, rings and stations of three example systems and of the player's own.
//
// About 40 questions to the site, and six more for each of the player's
// systems, a little over a second apart: two to three minutes.
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import {
  ANCHORS, RANGE_LY, RESULTS_OFFERED, anchorInBox, boxAround, pickByName, readMine, showing,
  showingMine, systemInBox, trimRow,
} from './src/logic.mjs';

const SITE = process.env.EDFINDER_SITE ?? 'https://ed-finder.app';
/** Systems every copy of the System Map trial shows: one star, two stars, and a giant with a small companion. */
const SYSTEM_MAP_EXAMPLES = ['Sol', 'LHS 3447', 'Polaris'];
const pause = () => new Promise((done) => setTimeout(done, 1200));

async function answer(response, what) {
  if (!response.ok) throw new Error(`The site answered ${response.status} for ${what}`);
  return response.json();
}

/** The first results Find lists within 500 ly of an anchor. */
async function findNear(anchor) {
  const response = await fetch(`${SITE}/api/local/search`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      reference_coords: { x: anchor.x, y: anchor.y, z: anchor.z },
      size: 50,
      from: 0,
      filters: { distance: { max: 500 } },
    }),
    signal: AbortSignal.timeout(90_000),
  });
  const found = (await answer(response, `Find near ${anchor.name}`)).results;
  return found.map(trimRow).filter(Boolean).slice(0, RESULTS_OFFERED);
}

/** Every system in the box around a point, as the site holds them. */
async function boxOf(centre, name) {
  const query = new URLSearchParams({ ...boxAround(centre), limit: '10000' });
  const response = await fetch(`${SITE}/api/map/systems?${query}`, { signal: AbortSignal.timeout(90_000) });
  const box = await answer(response, `the stars around ${name}`);
  // A cut-short answer would leave stars out without saying so.
  if (box.truncated) throw new Error(`The site cut short its list of stars around ${name}`);
  return box.systems;
}

/** The player's own list, or none when the file is not there. */
async function mineFromFile() {
  let text;
  try {
    text = await readFile(new URL('./my-systems.local.json', import.meta.url), 'utf8');
  } catch (error) {
    if (error.code === 'ENOENT') return [];
    throw error;
  }
  return readMine(JSON.parse(text));
}

/** The site's own record of a system, found by its exact name. */
async function byName(name) {
  const query = new URLSearchParams({ q: name, limit: '10' });
  const response = await fetch(`${SITE}/api/local/autocomplete?${query}`, { signal: AbortSignal.timeout(90_000) });
  const found = pickByName((await answer(response, `the name ${name}`)).results, name);
  if (!found) throw new Error(`The site holds no system named exactly "${name}"`);
  return found;
}

const showings = [];
for (const mine of await mineFromFile()) {
  const found = await byName(mine.name);
  await pause();
  const box = await boxOf(found, found.name);
  showings.push(showingMine(systemInBox(box, found), box, mine));
  console.log(`Yours, ${found.name}: ${showings.at(-1).stars.length} stars in range`);
  await pause();
}
for (const anchor of Object.values(ANCHORS)) {
  const anchorBox = await boxOf(anchor, anchor.name);
  const chosen = anchorInBox(anchorBox, anchor);
  if (!chosen) throw new Error(`The site's stars around ${anchor.name} do not include ${anchor.name}`);
  showings.push(showing(chosen, anchorBox, anchor.name));
  console.log(`${anchor.name}: ${showings.at(-1).stars.length} stars in range`);
  await pause();

  for (const row of await findNear(anchor)) {
    await pause();
    const chosenRow = { id64: row.id64, name: row.name, x: row.x, y: row.y, z: row.z, star: row.star };
    showings.push(showing(chosenRow, await boxOf(row, row.name), anchor.name, row));
    console.log(`${row.name}: ${showings.at(-1).stars.length} stars in range`);
  }
}

/** A system's bodies, rings and stations, as the site gives them, for the System Map trial. */
async function wholeSystem(found) {
  const ask = async (part) => {
    await pause();
    const response = await fetch(`${SITE}/api/v1/systems/${found.id64}${part}`, { signal: AbortSignal.timeout(90_000) });
    return answer(response, `${found.name}${part}`);
  };
  const system = await ask('');
  const bodies = await ask('/bodies');
  const rings = await ask('/rings');
  // Fleet carriers come and go, and nobody built them here.
  const stations = (await ask('/stations')).filter((station) => station.station_type !== 'fleet_carrier');
  console.log(`System map, ${found.name}: ${bodies.length} bodies, ${rings.length} rings and belts, ${stations.length} stations`);
  return { system, bodies, rings, stations };
}

// The System Map trial: one star, two stars, a giant star, and the player's own.
const systems = [];
for (const name of [...SYSTEM_MAP_EXAMPLES, ...(await mineFromFile()).map((mine) => mine.name)]) {
  systems.push(await wholeSystem(await byName(name)));
}

const folder = fileURLToPath(new URL('./public/data/', import.meta.url));
await mkdir(folder, { recursive: true });
await writeFile(`${folder}systems.json`, JSON.stringify({ fetchedOn: new Date().toISOString().slice(0, 10), systems }));
console.log(`public/data/systems.json: ${systems.length} systems for the System Map trial`);
const file = { fetchedOn: new Date().toISOString().slice(0, 10), rangeLy: RANGE_LY, showings };
await writeFile(`${folder}shown.json`, JSON.stringify(file));
console.log(`public/data/shown.json: ${showings.length} systems to show`);
