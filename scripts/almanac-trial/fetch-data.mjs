#!/usr/bin/env node
// Fetches what the trial page shows: the candidates Find lists within 500 ly
// of Sol and of Colonia, from the live site, 500 at a time up to the 10,000
// Find gives. Writes public/data/sol.json and public/data/colonia.json.
//
//     node fetch-data.mjs                     both anchors
//     node fetch-data.mjs --from raw.json sol  cut down an answer saved earlier
//
// It asks about once a second, so both anchors take a few minutes.
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { ANCHORS, dataFile } from './src/logic.mjs';

const SITE = process.env.EDFINDER_SITE ?? 'https://ed-finder.app';
const PAGE = 500;
const MOST = 10000;

async function ask(anchor, from) {
  const response = await fetch(`${SITE}/api/local/search`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      reference_coords: { x: anchor.x, y: anchor.y, z: anchor.z },
      size: PAGE,
      from,
      filters: { distance: { max: 500 } },
    }),
    signal: AbortSignal.timeout(90_000),
  });
  if (!response.ok) throw new Error(`Find answered ${response.status} for ${anchor.name} from ${from}`);
  return (await response.json()).results;
}

async function fetchAnchor(key) {
  const anchor = ANCHORS[key];
  const results = [];
  for (let from = 0; from < MOST; from += PAGE) {
    const page = await ask(anchor, from);
    results.push(...page);
    console.log(`${anchor.name}: ${results.length}`);
    if (page.length < PAGE) break;
    await new Promise((done) => setTimeout(done, 1200));
  }
  return results;
}

async function main(args) {
  const folder = fileURLToPath(new URL('./public/data/', import.meta.url));
  await mkdir(folder, { recursive: true });
  const today = new Date().toISOString().slice(0, 10);
  const saved = args[0] === '--from';
  for (const key of saved ? [args[2]] : Object.keys(ANCHORS)) {
    if (!ANCHORS[key]) throw new Error(`Unknown anchor "${key}": use sol or colonia`);
    // One anchor after the other: the live site is asked one thing at a time.
    const results = saved ? JSON.parse(await readFile(args[1], 'utf8')).results : await fetchAnchor(key);
    const file = dataFile(ANCHORS[key], results, today);
    await writeFile(`${folder}${key}.json`, JSON.stringify(file));
    console.log(`public/data/${key}.json: ${file.rows.length} candidates`);
  }
}

main(process.argv.slice(2)).catch((error) => {
  console.error(String(error?.message ?? error));
  process.exit(1);
});
