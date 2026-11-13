#!/usr/bin/env node
// Fetches what the trial page shows, from the live site, and writes
// public/data/shown.json. For each anchor (Sol, Colonia): the anchor itself
// and the first few results Find lists near it, each with every real star
// within 15 ly.
//
//     node fetch-data.mjs
//
// About 25 questions to the site, a little over a second apart.
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import {
  ANCHORS, RANGE_LY, RESULTS_OFFERED, anchorInBox, boxAround, showing, trimRow,
} from './src/logic.mjs';

const SITE = process.env.EDFINDER_SITE ?? 'https://ed-finder.app';
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

const showings = [];
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

const folder = fileURLToPath(new URL('./public/data/', import.meta.url));
await mkdir(folder, { recursive: true });
const file = { fetchedOn: new Date().toISOString().slice(0, 10), rangeLy: RANGE_LY, showings };
await writeFile(`${folder}shown.json`, JSON.stringify(file));
console.log(`public/data/shown.json: ${showings.length} systems to show`);
