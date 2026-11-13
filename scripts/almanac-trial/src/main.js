// The trial page: Almanac's galaxy map, unchanged, doing what a "show me on
// map" button in Find would do. Everything the page decides is in logic.mjs;
// this file only hands it to the map.
//
// The page has no controls of its own. The map's own list of sets (top
// centre) stands in for the button: each entry is one system chosen in Find.
// Choosing one flies to it, selects it, shows the real stars within 15 ly
// and draws that range.
//
// When the data holds systems of the player's own, the list opens on all of
// them at once, and each has a SHOW ON MAP button of its own.
import { createGalaxyMap } from '@elite-dangerous-almanac/galaxy-map';
import { nebulae } from '@elite-dangerous-almanac/galaxy-map/nebulae';
import {
  MINE_ID, contentOf, datasetEntry, detailsOf, inspectAddress, mineAmong, mineContent, mineEntry,
  rangeSphere,
} from './logic.mjs';

const problem = document.querySelector('#problem');

function fail(error) {
  problem.textContent = String(error?.message ?? error);
  console.error(error);
}

async function start() {
  const response = await fetch('./data/shown.json');
  if (!response.ok) throw new Error(`The data file data/shown.json did not load (${response.status}).`);
  const data = await response.json();
  /** Each "show me on map", by the id of its chosen system. */
  const showings = new Map(data.showings.map((shown) => [shown.chosen.id64, shown]));
  const mine = mineAmong(data.showings);
  const wanted = new URLSearchParams(location.search).get('system');
  const loaded = () => map.getLoadedDataset()?.id ?? null;
  const opensOn = () => {
    if (showings.has(wanted)) return wanted;
    return mine.length > 0 ? MINE_ID : data.showings[0].chosen.id64;
  };
  const inspect = (id64) => ({
    label: 'OPEN IN INSPECT',
    onSelect: () => window.open(inspectAddress(id64), '_blank', 'noopener'),
  });

  const map = createGalaxyMap(document.querySelector('#map'), {
    grid: true,
    systemNames: true,
    // The library's own nebulae, the way its public demo draws them.
    nebulae,
    datasets: [
      ...(mine.length > 0 ? [{ ...mineEntry(mine), load: () => mineContent(mine) }] : []),
      ...data.showings.map((shown) => ({ ...datasetEntry(shown), load: () => contentOf(shown) })),
    ],
    dataset: opensOn(),
    hud: {
      title: 'SHOW ON MAP',
      details: (system) => {
        // In the view of all the player's systems, each one speaks for itself.
        const overview = loaded() === MINE_ID;
        const shown = showings.get(overview ? system.id64 : loaded());
        const details = shown ? detailsOf(shown, system.id64) : null;
        if (!details) return null;
        const showOnMap = { label: 'SHOW ON MAP', onSelect: () => map.loadDataset(system.id64).catch(fail) };
        return { ...details, actions: overview ? [showOnMap, inspect(system.id64)] : [inspect(system.id64)] };
      },
    },
  });

  // A new set arrives with no shapes and nothing selected. For one system:
  // draw its range and select it, as the button would. For all the player's
  // systems: draw the range of each.
  map.onDatasetChange(() => {
    if (loaded() === MINE_ID) {
      map.addSpheres(mine.map(rangeSphere));
      return;
    }
    const shown = showings.get(loaded());
    if (!shown) return;
    map.addSpheres([rangeSphere(shown)]);
    map.setSelection(shown.chosen.id64);
  });

  // For looking in from the browser's console, and for timing.
  window.trial = { map, showings, mine, fetchedOn: data.fetchedOn };
  await map.ready;
}

start().catch(fail);
