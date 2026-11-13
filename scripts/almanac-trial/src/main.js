// The trial page: Almanac's galaxy map, unchanged, doing what a "show me on
// map" button in Find would do. Everything the page decides is in logic.mjs;
// this file only hands it to the map.
//
// The page has no controls of its own. The map's own list of sets (top
// centre) stands in for the button: each entry is one system chosen in Find.
// Choosing one flies to it, selects it, shows the real stars within 15 ly
// and draws that range.
import { createGalaxyMap } from '@elite-dangerous-almanac/galaxy-map';
import { nebulae } from '@elite-dangerous-almanac/galaxy-map/nebulae';
import { contentOf, datasetEntry, detailsOf, inspectAddress, rangeSphere } from './logic.mjs';

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
  const wanted = new URLSearchParams(location.search).get('system');
  const current = () => showings.get(map.getLoadedDataset()?.id) ?? null;

  const map = createGalaxyMap(document.querySelector('#map'), {
    grid: true,
    systemNames: true,
    // The library's own nebulae, the way its public demo draws them.
    nebulae,
    datasets: data.showings.map((shown) => ({ ...datasetEntry(shown), load: () => contentOf(shown) })),
    dataset: showings.has(wanted) ? wanted : data.showings[0].chosen.id64,
    hud: {
      title: 'SHOW ON MAP',
      details: (system) => {
        const shown = current();
        const details = shown ? detailsOf(shown, system.id64) : null;
        if (!details) return null;
        return {
          ...details,
          actions: [{
            label: 'OPEN IN INSPECT',
            onSelect: () => window.open(inspectAddress(system.id64), '_blank', 'noopener'),
          }],
        };
      },
    },
  });

  // A new set arrives with no shapes and nothing selected: draw the chosen
  // system's range and select it, as the button would.
  map.onDatasetChange(() => {
    const shown = current();
    if (!shown) return;
    map.addSpheres([rangeSphere(shown)]);
    map.setSelection(shown.chosen.id64);
  });

  // For looking in from the browser's console, and for timing.
  window.trial = { map, showings, fetchedOn: data.fetchedOn };
  await map.ready;
}

start().catch(fail);
