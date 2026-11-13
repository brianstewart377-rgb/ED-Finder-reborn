// The trial page: Almanac's galaxy map, unchanged, with Find's own candidates
// on it. Everything the page decides is in logic.mjs; this file only hands it
// to the map. The page has no controls of its own: choosing between the sets,
// searching by name, the groups with their counts and the panel about one
// system are all the map library's own.
import { createGalaxyMap, MAX_SYSTEMS } from '@elite-dangerous-almanac/galaxy-map';
import { nebulae } from '@elite-dangerous-almanac/galaxy-map/nebulae';
import {
  ANCHORS, contentOf, datasetEntry, detailsOf, inspectAddress, madeUpRecords,
} from './logic.mjs';

const problem = document.querySelector('#problem');

/** Every candidate fetched so far, by id, with the anchor it was listed for. */
const candidates = new Map();
/** What the last load did, for looking in from the console. */
const loads = [];

function fail(error) {
  problem.textContent = String(error?.message ?? error);
  console.error(error);
}

async function load(key, signal) {
  problem.textContent = '';
  try {
    const started = performance.now();
    const response = await fetch(`./data/${key}.json`, { signal });
    if (!response.ok) throw new Error(`The candidates file data/${key}.json did not load (${response.status}).`);
    const data = await response.json();
    for (const row of data.rows) candidates.set(row.id64, { row, anchor: data.anchor.name });
    loads.push({ key, candidates: data.rows.length, fetchedOn: data.fetchedOn, fetchMs: performance.now() - started });
    return contentOf(data);
  } catch (error) {
    if (!signal?.aborted) fail(error);
    throw error;
  }
}

const wanted = new URLSearchParams(location.search).get('anchor');

const map = createGalaxyMap(document.querySelector('#map'), {
  grid: true,
  systemNames: true,
  // The library's own nebulae, the way its public demo draws them.
  nebulae,
  datasets: Object.entries(ANCHORS).map(([key, anchor]) => ({
    ...datasetEntry(key, anchor),
    load: (signal) => load(key, signal),
  })),
  dataset: wanted === 'colonia' ? 'colonia' : 'sol',
  hud: {
    title: 'ALMANAC TRIAL',
    details: (system) => {
      const found = candidates.get(system.id64);
      if (!found) return null;
      return {
        ...detailsOf(found.row, found.anchor),
        actions: [{
          label: 'OPEN IN INSPECT',
          onSelect: () => window.open(inspectAddress(found.row.id64), '_blank', 'noopener'),
        }],
      };
    },
  },
});

/**
 * Made-up systems on top, to see how many markers the map carries. Called
 * from the browser's console (window.trial.addMadeUp(20000, 'sol')); there is
 * no button, so nobody mistakes them for candidates.
 */
function addMadeUp(count, key = 'sol') {
  const wantedCount = Math.max(0, Math.min(count, MAX_SYSTEMS - map.systemCount()));
  map.addCategories([{ name: 'Made-up', color: [255, 110, 110], description: 'Not real: for counting only' }]);
  const started = performance.now();
  const report = map.addSystems(madeUpRecords(wantedCount, ANCHORS[key], 1000));
  return {
    asked: count, added: report.added, onTheMap: map.systemCount(), limit: MAX_SYSTEMS,
    addMs: performance.now() - started,
  };
}

// For looking in from the browser's console, and for timing.
window.trial = { map, candidates, loads, addMadeUp, MAX_SYSTEMS };

map.ready.catch((error) => fail(error));
