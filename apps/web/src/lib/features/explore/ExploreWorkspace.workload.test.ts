import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { tick } from 'svelte';
import * as api from '$lib/api/client';
import { queryClient, queryKeys } from '$lib/api/query';
import { parseId64 } from '$lib/domain/id64';
import { applicationStores } from '$lib/persistence/stores';
import {
  fakeMaps,
  resetFakeMaps,
  type FakeMap,
} from '$lib/galaxy-map/fake-almanac';
import { forgetMapLeftAt } from '$lib/galaxy-map/left-at';
import ExploreWorkloadTestHost from './ExploreWorkloadTestHost.svelte';
import { ExploreViewState } from './explore-view-state.svelte';

const { page, featureGate, goto } = vi.hoisted(() => ({
  page: {
    url: new URL('http://localhost/explore'),
    state: {} as App.PageState,
  },
  featureGate: { enabled: false },
  goto: vi.fn(),
}));

vi.mock('$app/state', () => ({ page }));
vi.mock('$app/navigation', () => ({ goto }));
vi.mock('$lib/config', () => ({
  get FINDER_F4_ENABLED() {
    return featureGate.enabled;
  },
}));
// Almanac's map needs a graphics card; the stand-in keeps what it is given.
vi.mock(
  '@elite-dangerous-almanac/galaxy-map',
  () => import('$lib/galaxy-map/fake-almanac'),
);
vi.mock('@elite-dangerous-almanac/galaxy-map/nebulae', () => ({
  nebulae: {},
}));
vi.mock('$lib/api/client', async (loadOriginal) => ({
  ...(await loadOriginal<typeof import('$lib/api/client')>()),
  autocompleteSystems: vi.fn(),
  searchExploreSystems: vi.fn(),
  getArchetypeRankings: vi.fn(),
  getSystem: vi.fn(),
  getCatalogueViewportSystems: vi.fn(),
  getCommanderTravelHeatmap: vi.fn(),
  getCommanderJournalSummary: vi.fn(),
  getAuthSession: vi.fn(),
}));

const systems: api.ExploreSystem[] = [1, 2].map((index) => ({
  id64: parseId64(`900719925474100${index}`),
  name: `Workload ${index}`,
  coords: { x: index * 10, y: index, z: index * 20 },
  main_star_type: 'G',
  overall_development_potential: 90 - index,
  ranking_score: 90 - index,
  score_kind: 'overall_potential',
}));
const ranking: api.RankingProvenance = {
  ranking_version: 'v3-colony-potential-2',
  ranking_sha256: 'ranking-sha',
  derived_generation_id: 'generation-id',
  publication_sequence: 1,
  archetype_version: 'archetype-version',
  archetype_manifest_sha256: 'manifest-sha',
  archetype_model_sha256: 'model-sha',
};
// What the site answers for the box around either system: the two systems
// themselves, with their star class, as the real answer holds the system
// asked about; a star 5 ly from "Workload 1"; and one in a corner of the
// box, beyond the range of both.
const starsNearby: api.CatalogueViewportResponse = {
  systems: [
    ...[1, 2].map((index) => ({
      id64: parseId64(`900719925474100${index}`),
      name: `Workload ${index}`,
      x: index * 10,
      y: index,
      z: index * 20,
      mainStarClass: 'G',
      populated: false,
    })),
    {
      id64: parseId64('9007199254742001'),
      name: 'Workload neighbour',
      x: 13,
      y: 1,
      z: 24,
      mainStarClass: 'M',
      populated: false,
    },
    {
      id64: parseId64('9007199254742002'),
      name: 'Workload corner',
      x: 25,
      y: 16,
      z: 55,
      mainStarClass: 'K',
      populated: false,
    },
  ],
  truncated: false,
};

function finderResponse(size: number, offset = 0) {
  const results = systems.slice(offset, offset + size);
  return {
    results,
    page: {
      page_size: size,
      count: results.length,
      total: systems.length,
      navigable_total: systems.length,
      offset,
      navigation_limit: 1_000,
      truncation: { source: 'local-search' as const, total_is_capped: false },
    },
    ranking,
  };
}

/**
 * What the page asks of the network and of the map through one ordinary
 * visit: open Explore, search near a star, select a result, page the list,
 * hide and show the Finder, leave.
 *
 * The real Svelte components and TanStack observers run throughout; only the
 * HTTP answers and Almanac's map (which needs a graphics card) are replaced.
 * The counts were measured by running this file, not worked out from the
 * code. They are here so that a change which makes the page ask twice, or
 * empty and fill the map when nothing on it changed, fails a test.
 */
describe('Explore load and interaction work budget', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetFakeMaps();
    forgetMapLeftAt();
    queryClient.clear();
    localStorage.clear();
    sessionStorage.clear();
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe = vi.fn();
        disconnect = vi.fn();
        unobserve = vi.fn();
      },
    );
    vi.stubGlobal('matchMedia', () => ({ matches: false }));
    // The map beside the Finder on a 1920 px screen, as measured on the
    // built page: wide enough for the map's own panel to open beside it.
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1446);
    vi.mocked(api.autocompleteSystems).mockResolvedValue({ results: [] });
    vi.mocked(api.searchExploreSystems).mockImplementation(async (request) =>
      finderResponse(request.size ?? 24, request.from ?? 0),
    );
    vi.mocked(api.getCatalogueViewportSystems).mockImplementation(async () =>
      structuredClone(starsNearby),
    );
    vi.mocked(api.getCommanderTravelHeatmap).mockResolvedValue({
      mode: 'markers',
      visits: [],
      count: 0,
      truncated: false,
    });
    vi.mocked(api.getAuthSession).mockResolvedValue({
      authenticated: false,
      user: null,
    });
    goto.mockImplementation(async (destination: string | URL, options = {}) => {
      page.url = new URL(destination, page.url);
      page.state = options.state ?? {};
    });
  });

  afterEach(() => {
    cleanup();
    queryClient.clear();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it.each([false, true])(
    'pins real load and interaction counts with F4=%s',
    async (enabled) => {
      featureGate.enabled = enabled;
      const settle = async () => {
        for (let round = 0; round < 4; round += 1) {
          await vi.advanceTimersByTimeAsync(200);
          await tick();
        }
      };
      const theMap = () => fakeMaps[0] as FakeMap;
      const work = () => ({
        maps: fakeMaps.length,
        // Times the map was emptied, handed systems, flown, and its panel
        // opened.
        fills: theMap().fills,
        adds: theMap().adds,
        flights: theMap().flights.length,
        panelOpenings: theMap().opened.length,
        onTheMap: theMap().systemCount(),
        activeQueries: queryClient
          .getQueryCache()
          .getAll()
          .filter((query) => query.getObserversCount() > 0).length,
        api: {
          finder: vi.mocked(api.searchExploreSystems).mock.calls.length,
          rankings: vi.mocked(api.getArchetypeRankings).mock.calls.length,
          stars: vi.mocked(api.getCatalogueViewportSystems).mock.calls.length,
          selected: vi.mocked(api.getSystem).mock.calls.length,
          autocomplete: vi.mocked(api.autocompleteSystems).mock.calls.length,
          travel: vi.mocked(api.getCommanderTravelHeatmap).mock.calls.length,
          journalSummary: vi.mocked(api.getCommanderJournalSummary).mock.calls
            .length,
          auth: vi.mocked(api.getAuthSession).mock.calls.length,
        },
      });
      const viewState = new ExploreViewState(
        () => page,
        enabled,
        applicationStores,
      );
      // The map is always on screen beside the Finder panel.
      const mounted = render(ExploreWorkloadTestHost, {
        viewState,
        installPage(model) {
          Object.defineProperties(page, {
            url: {
              configurable: true,
              get: () => model.url,
              set: (value: URL) => {
                model.url = value;
              },
            },
            state: {
              configurable: true,
              get: () => model.state,
              set: (value: App.PageState) => {
                model.state = value;
              },
            },
          });
        },
      });
      await vi.dynamicImportSettled();
      await settle();

      // Opening Explore: the map starts with nothing on it and nothing is
      // asked of Find or of the galaxy data.
      expect(
        screen.getByText('Search for a system above to see what is near it.'),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole('list', { name: 'System results' }),
      ).not.toBeInTheDocument();
      expect(document.querySelectorAll('canvas')).toHaveLength(1);
      const loaded = work();

      vi.mocked(api.autocompleteSystems).mockResolvedValueOnce({
        results: [
          { id64: systems[1].id64, name: 'Workload 2', x: 20, y: 2, z: 40 },
        ],
      });
      await fireEvent.input(
        screen.getByRole('combobox', { name: 'System name' }),
        { target: { value: 'Workload 2' } },
      );
      await settle();
      await fireEvent.click(screen.getByRole('option', { name: /Workload 2/ }));
      await settle();
      expect(
        screen.getByRole('heading', { name: 'Near Workload 2' }),
      ).toBeInTheDocument();
      const anchored = work();

      await fireEvent.click(
        within(screen.getByRole('list', { name: 'System results' })).getByRole(
          'button',
          { name: /^Workload 1\b/ },
        ),
      );
      await settle();
      const selected = work();
      // The selected system and the one star within 15 ly of it.
      expect(theMap().getSelection()?.name).toBe('Workload 1');
      expect(theMap().namesIn('M class star')).toEqual(['Workload neighbour']);

      // The same answers come again: asked for once more, and nothing on the
      // map is emptied, filled, reopened or flown.
      await queryClient.invalidateQueries({ queryKey: queryKeys.all });
      await settle();
      const refreshed = work();
      expect({ ...refreshed, api: null }).toEqual({ ...selected, api: null });

      // A quiescent page must not restart work.
      for (let sample = 0; sample < 24; sample += 1) {
        await vi.advanceTimersByTimeAsync(500);
        await tick();
      }
      expect(work()).toEqual(refreshed);
      expect(fakeMaps).toHaveLength(1);
      expect(theMap().disposed).toBe(false);

      const quiet = {
        rankings: 0,
        selected: 0,
        travel: 0,
        journalSummary: 0,
        auth: 0,
      };
      expect(loaded).toEqual({
        maps: 1,
        // The map is handed its (empty) contents once, when it is ready.
        fills: 1,
        adds: 1,
        flights: 0,
        panelOpenings: 0,
        onTheMap: 0,
        // Watched but not asked: nobody is signed in and nothing is chosen.
        // With the type picker on, one more is watched and not asked: the
        // count of a page that the API answered before counting its
        // matches, which no page here is.
        activeQueries: enabled ? 8 : 7,
        api: { ...quiet, finder: 0, stars: 0, autocomplete: 0 },
      });
      // Choosing a star to search near: one search and one request for its
      // stars. The star is shown and flown to at once, from the position
      // its suggestion came with, before the search has answered. Its star
      // class comes with its stars a moment later: it is taken off and put
      // back in its class's colour, which is a second fill, add, flight and
      // panel opening. (With the type picker on, what Find says of the star
      // comes while the map is still flying there. The flight is left
      // alone: the panel is opened again for it on landing.)
      expect(anchored).toEqual({
        maps: 1,
        fills: 2,
        adds: 3,
        flights: 2,
        panelOpenings: 2,
        onTheMap: 1,
        activeQueries: enabled ? 8 : 7,
        api: { ...quiet, finder: 1, stars: 1, autocomplete: 1 },
      });
      // Selecting a result: no new search. The map is emptied once, to take
      // the last system off, then handed the new one and, when they arrive,
      // its stars: one flight, the panel opened once.
      expect(selected).toEqual({
        maps: 1,
        fills: anchored.fills + 1,
        adds: anchored.adds + 2,
        flights: anchored.flights + 1,
        panelOpenings: anchored.panelOpenings + 1,
        onTheMap: 2,
        // With the type picker on, the selected result is watched by id,
        // and so is the count that may be asked for later.
        activeQueries: enabled ? 9 : 7,
        api: { ...quiet, finder: 1, stars: 2, autocomplete: 1 },
      });
      // Everything on screen asked for once more, and the map untouched.
      expect(refreshed).toEqual({
        ...selected,
        api: { ...quiet, finder: 2, stars: 3, autocomplete: 2 },
      });

      const assertCleanup = async () => {
        // Show on map, then hiding and showing the Finder: the one map stays
        // and nothing is asked for again.
        const opener = document.querySelector<HTMLButtonElement>(
          '[data-explore-map-open]',
        )!;
        await fireEvent.click(opener);
        await settle();
        const visible = work();
        await fireEvent.click(
          screen.getByRole('button', { name: 'Hide Finder' }),
        );
        await settle();
        expect(viewState.finderPanelOpen).toBe(false);
        expect(work()).toEqual(visible);
        expect(document.querySelectorAll('canvas')).toHaveLength(1);
        await fireEvent.click(
          screen.getByRole('button', { name: 'Show Finder' }),
        );
        await settle();
        expect(viewState.finderPanelOpen).toBe(true);
        expect(work()).toEqual(visible);

        mounted.unmount();
        await settle();
        expect(document.querySelectorAll('canvas')).toHaveLength(0);
        expect(fakeMaps).toHaveLength(1);
        expect(theMap().disposed).toBe(true);
        expect(work().activeQueries).toBe(0);
        expect(work().api).toEqual(visible.api);
      };

      if (!enabled) {
        await assertCleanup();
        return;
      }

      // Paging the list, or a search that fails, is no business of the map:
      // the selected system stays on it, untouched.
      const mapWork = () => {
        const current = work();
        return {
          fills: current.fills,
          adds: current.adds,
          flights: current.flights,
          panelOpenings: current.panelOpenings,
          onTheMap: current.onTheMap,
          stars: current.api.stars,
        };
      };
      const beforePaging = mapWork();
      await fireEvent.change(
        screen.getByRole('spinbutton', { name: 'Results per page' }),
        { target: { value: '1' } },
      );
      await settle();
      await fireEvent.click(screen.getByRole('button', { name: 'Next' }));
      await settle();
      await fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
      await settle();
      vi.mocked(api.searchExploreSystems).mockRejectedValueOnce(
        new api.ApiError(503, '/api/local/search', {
          detail: 'Fixture temporarily unavailable',
        }),
      );
      await fireEvent.click(
        screen.getByRole('radio', { name: 'Manufacturing Hub' }),
      );
      await settle();
      expect(
        screen.getByRole('heading', {
          name: 'Ranking temporarily unavailable',
        }),
      ).toBeInTheDocument();
      await fireEvent.click(screen.getByRole('radio', { name: 'Any' }));
      await settle();
      expect(mapWork()).toEqual(beforePaging);
      expect(theMap().getSelection()?.name).toBe('Workload 1');
      // Five more searches (three pages, the failed type, back to Any), and
      // still nothing asked of the galaxy data or of the map.
      expect(work()).toEqual({
        ...refreshed,
        api: { ...refreshed.api, finder: 7 },
      });
      expect(api.getSystem).not.toHaveBeenCalled();
      await assertCleanup();
    },
    30_000,
  );
});
