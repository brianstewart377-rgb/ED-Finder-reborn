// The trial page: Almanac's galaxy map, unchanged, with Find's own results on
// it. Everything the page decides is in logic.mjs; this file only hands it to
// the map. The page has no controls of its own: choosing between the sets,
// searching by name, the groups with their counts and the panel about one
// system are all the map library's own. The one thing the page does while the
// map runs is draw the stars only when the view is close (see followZoom).
import { createGalaxyMap, MAX_SYSTEMS } from '@elite-dangerous-almanac/galaxy-map';
import { nebulae } from '@elite-dangerous-almanac/galaxy-map/nebulae';
import {
  ANCHORS, ANCHOR_GROUP, RESULT_GROUP, SETS, contentOf, datasetEntry, detailsOf, inspectAddress,
  madeUpRecords, starsShownAt,
} from './logic.mjs';

const problem = document.querySelector('#problem');

/** Every candidate fetched so far, by id, with the anchor it was listed for. */
const candidates = new Map();
/** Each anchor's file once fetched, so switching sets fetches nothing twice. */
const files = new Map();
/** What each load did, for looking in from the console. */
const loads = [];
/** The star groups of each set, so they can be drawn or not as the view zooms. */
const starGroupsOf = new Map();

function fail(error) {
  problem.textContent = String(error?.message ?? error);
  console.error(error);
}

async function fileOf(anchor, signal) {
  if (!files.has(anchor)) {
    const response = await fetch(`./data/${anchor}.json`, { signal });
    if (!response.ok) throw new Error(`The candidates file data/${anchor}.json did not load (${response.status}).`);
    const data = await response.json();
    for (const row of data.rows) candidates.set(row.id64, { row, anchor: data.anchor.name });
    files.set(anchor, data);
  }
  return files.get(anchor);
}

async function load(set, signal) {
  problem.textContent = '';
  try {
    const started = performance.now();
    const data = await fileOf(set.anchor, signal);
    const content = contentOf(data, set.others);
    starGroupsOf.set(set.id, content.categories
      .map((category) => category.name)
      .filter((name) => name !== RESULT_GROUP && name !== ANCHOR_GROUP));
    loads.push({ set: set.id, systems: content.systems.length, fetchedOn: data.fetchedOn, ms: performance.now() - started });
    return content;
  } catch (error) {
    if (!signal?.aborted) fail(error);
    throw error;
  }
}

const wanted = new URLSearchParams(location.search).get('set');

const map = createGalaxyMap(document.querySelector('#map'), {
  grid: true,
  systemNames: true,
  // The library's own nebulae, the way its public demo draws them.
  nebulae,
  datasets: SETS.map((set) => ({ ...datasetEntry(set), load: (signal) => load(set, signal) })),
  dataset: SETS.some((set) => set.id === wanted) ? wanted : SETS[0].id,
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
 * Draws the stars of the set on the map only while the view is close enough.
 * Find's results and the anchor always draw.
 */
let starsDrawn = null;
function followZoom() {
  const groups = starGroupsOf.get(map.getLoadedDataset()?.id) ?? [];
  const wanted = starsShownAt(map.getView().distance);
  if (wanted === starsDrawn) return;
  starsDrawn = wanted;
  for (const name of groups) map.setCategoryVisible(name, wanted);
}
map.onViewChange(followZoom);
map.onDatasetChange(() => {
  // A new set comes with every group drawn, so the rule is applied afresh.
  starsDrawn = null;
  followZoom();
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
window.trial = { map, candidates, loads, addMadeUp, starGroupsOf, MAX_SYSTEMS };

map.ready.catch((error) => fail(error));
