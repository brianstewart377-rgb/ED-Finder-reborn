import {
  cleanup,
  fireEvent,
  render as renderComponent,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axe from 'axe-core';
import { tick, type ComponentProps } from 'svelte';
import * as api from '$lib/api/client';
import { queryClient, queryKeys } from '$lib/api/query';
import { auth } from '$lib/auth/auth';
import { FINDER_F4_ENABLED } from '$lib/config';
import { parseId64 } from '$lib/domain/id64';
import { applicationStores } from '$lib/persistence/stores';
import {
  failNextMap,
  fakeMaps,
  resetFakeMaps,
  type FakeMap,
} from '$lib/galaxy-map/fake-almanac';
import solBox from '$lib/galaxy-map/fixtures/map-systems-sol-box.json';
import { forgetMapLeftAt } from '$lib/galaxy-map/left-at';
import type { ExploreAnchor } from './navigation';
import ExploreWorkspaceTestHost from './ExploreWorkspaceTestHost.svelte';
import { ExploreViewState } from './explore-view-state.svelte';

// The map is always on screen beside the Finder panel.
function render(
  component: typeof ExploreWorkspaceTestHost,
  props: ComponentProps<typeof ExploreWorkspaceTestHost> = {},
) {
  const viewState =
    props.viewState ??
    new ExploreViewState(
      () => mockedPage,
      featureGate.enabled,
      applicationStores,
    );
  return renderComponent(component, { ...props, viewState });
}

const { mockedPage, mockedGoto, featureGate } = vi.hoisted(() => ({
  mockedPage: {
    url: new URL('http://localhost/explore'),
    state: {} as App.PageState,
  },
  mockedGoto: vi.fn().mockResolvedValue(undefined),
  featureGate: { enabled: false },
}));

vi.mock('$app/state', () => ({ page: mockedPage }));
vi.mock('$app/navigation', () => ({ goto: mockedGoto }));
vi.mock('$lib/config', () => ({
  get FINDER_F4_ENABLED() {
    return featureGate.enabled;
  },
}));
vi.mock('$lib/api/client', async (loadOriginal) => {
  const original = await loadOriginal<typeof import('$lib/api/client')>();
  return {
    ...original,
    autocompleteSystems: vi.fn(),
    searchExploreSystems: vi.fn(),
    getArchetypeRankings: vi.fn(),
    getArchetypeExplanation: vi.fn(),
    getSystem: vi.fn(),
    getCatalogueViewportSystems: vi.fn(),
    getCommanderTravelHeatmap: vi.fn(),
    getCommanderJournalSummary: vi.fn(),
    getAuthSession: vi.fn(),
    getWatchlist: vi.fn(),
  };
});

// The map draws with WebGL2, which the test browser does not have. A stand-in
// keeps what the page hands the map (see $lib/galaxy-map/fake-almanac.ts).
vi.mock(
  '@elite-dangerous-almanac/galaxy-map',
  () => import('$lib/galaxy-map/fake-almanac'),
);
vi.mock('@elite-dangerous-almanac/galaxy-map/nebulae', () => ({
  nebulae: {},
}));

const catalogueFixture: api.CatalogueViewportResponse = {
  systems: [
    {
      id64: parseId64('9007199254740993'),
      name: 'Catalogue star',
      x: 0,
      y: 0,
      z: 0,
      mainStarClass: 'G',
      populated: false,
    },
  ],
  truncated: false,
};

/** The map of the page under test, once it has started. */
async function theMap(): Promise<FakeMap> {
  await waitFor(() =>
    expect(document.querySelector('[data-galaxy-map="ready"]')).not.toBeNull(),
  );
  return fakeMaps.at(-1)!;
}

/** The ids of the systems on the map, in the order the map was given them. */
const onTheMap = (map: FakeMap) => [...map.systems.keys()];

function stubRuntime(): void {
  resetFakeMaps();
  // Each test is a new visit to the site: the map was not left anywhere.
  forgetMapLeftAt();
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe = vi.fn();
      disconnect = vi.fn();
      unobserve = vi.fn();
    },
  );
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query === '(prefers-reduced-motion: reduce)',
  }));
  // The map beside the Finder on a 1920 by 1080 screen, as measured on the
  // built page: wide enough for the map's own panel to open beside it.
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1446);
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(765);
}

function mockFacadeDefaults(): void {
  localStorage.clear();
  featureGate.enabled = false;
  Object.defineProperties(mockedPage, {
    url: {
      configurable: true,
      writable: true,
      value: new URL('http://localhost/explore'),
    },
    state: { configurable: true, writable: true, value: {} },
  });
  mockedGoto.mockImplementation(async () => undefined);
  vi.mocked(api.autocompleteSystems).mockResolvedValue({ results: [] });
  mockedPage.url = new URL('http://localhost/explore');
  vi.mocked(api.searchExploreSystems).mockResolvedValue({
    results: [],
    page: {
      page_size: 24,
      count: 0,
      total: 0,
      navigable_total: 0,
      offset: 0,
      navigation_limit: 1_000,
      truncation: { source: 'local-search', total_is_capped: false },
    },
    ranking: {
      ranking_version: 'v3-colony-potential-2',
      ranking_sha256: 'ranking-sha',
      derived_generation_id: 'generation-id',
      publication_sequence: 1,
      archetype_version: 'archetype-version',
      archetype_manifest_sha256: 'manifest-sha',
      archetype_model_sha256: 'model-sha',
    },
  });
  vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue({
    systems: [],
    truncated: false,
  });
  vi.mocked(api.getWatchlist).mockImplementation(async (key) => ({
    sync_key: key,
    watchlist: [],
  }));
  vi.mocked(api.getCommanderTravelHeatmap).mockResolvedValue({
    mode: 'markers',
    visits: [],
    count: 0,
    truncated: false,
  });
  vi.mocked(api.getCommanderJournalSummary).mockResolvedValue({
    imported_files: 1,
    events_stored: 2,
    unique_bodies: 0,
    unique_bio_observations: 0,
    systems_observed: 1,
    last_imported_at: null,
    event_counts: {},
  });
  vi.mocked(api.getAuthSession).mockResolvedValue({
    authenticated: false,
    user: null,
  });
}

describe('ExploreWorkspace Finder F4 gate (default off)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    stubRuntime();
    mockFacadeDefaults();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('is built with the gate off', () => {
    expect(FINDER_F4_ENABLED).toBe(false);
  });

  it('replace-strips a hand-authored archetype URL without requesting results or rendering ranking UI', async () => {
    mockedPage.url = new URL(
      'http://localhost/explore?archetype=flexible&min-tier=A&page_size=1&offset=1&selected=9007199254740993&system=158872029&view=cards#results',
    );

    render(ExploreWorkspaceTestHost);

    await waitFor(() => expect(mockedGoto).toHaveBeenCalledTimes(1));

    // F4 parameters are stripped by replacement; `selected`, `system` and
    // unrelated parameters (plus the hash) survive in their original order.
    expect(mockedGoto).toHaveBeenCalledWith(
      '/explore?selected=9007199254740993&system=158872029&view=cards#results',
      {
        replaceState: true,
        noScroll: true,
        keepFocus: true,
        state: { finderRequested: false },
      },
    );

    // Disabled F4 parameters cannot activate either results endpoint.
    expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    expect(api.getArchetypeExplanation).not.toHaveBeenCalled();
    expect(
      document.querySelector('[data-archetype-explanation-toggle]'),
    ).not.toBeInTheDocument();
    expect(api.searchExploreSystems).not.toHaveBeenCalled();
    // Before a search the map is in front, with a prompt in the Finder.
    expect(
      screen.getByText('Search for a system above to see what is near it.'),
    ).toBeInTheDocument();

    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/minimum tier/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Best Colony Potential/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Galaxy-wide .* ranking/),
    ).not.toBeInTheDocument();
  });

  it('leaves a URL without F4 parameters alone (no replacement navigation)', async () => {
    mockedPage.url = new URL(
      'http://localhost/explore?selected=9007199254740993&view=cards',
    );

    render(ExploreWorkspaceTestHost);

    await theMap();
    expect(api.searchExploreSystems).not.toHaveBeenCalled();
    expect(mockedGoto).not.toHaveBeenCalled();
  });
});

const rankingIdentity: api.RankingProvenance = {
  ranking_version: 'v3-colony-potential-2',
  ranking_sha256: 'ranking-sha',
  derived_generation_id: 'generation-id',
  publication_sequence: 1,
  archetype_version: 'archetype-version',
  archetype_manifest_sha256: 'manifest-sha',
  archetype_model_sha256: 'model-sha',
};
const anchorA: ExploreAnchor = {
  id64: parseId64('9007199254740993'),
  name: 'Anchor A',
  x: 10,
  y: 20,
  z: 30,
};
const anchorB: ExploreAnchor = {
  id64: parseId64('9007199254740994'),
  name: 'Anchor B',
  x: -10,
  y: -20,
  z: -30,
};
const rankedRows: api.ExploreSystem[] = [1, 2, 3].map((index) => ({
  id64: parseId64(`900719925474100${index}`),
  name: `Ranked ${index}`,
  coords: { x: index * 10, y: index, z: index * 20 },
  overall_development_potential: 90 - index,
  ranking_score: 80 - index,
  ranking_tier: 'A',
  score_kind: 'selected_fit',
  selected_archetype: 'manufacturing_hub',
  ranking_provenance: rankingIdentity,
  primary_archetype: 'manufacturing_hub',
  confidence: 0.9,
  completeness: 0.9,
}));

function finderPage(offset: number, pageSize = 1, total = 3, selected = false) {
  return {
    results: rankedRows.slice(offset, Math.min(total, offset + pageSize)),
    page: {
      page_size: pageSize,
      offset,
      total,
      navigable_total: total,
      count: Math.max(0, Math.min(pageSize, total - offset)),
      navigation_limit: 1_000,
      truncation: selected
        ? { source: 'rankings' as const, is_truncated: false }
        : { source: 'local-search' as const, total_is_capped: false },
    },
    ranking: rankingIdentity,
  };
}

function mockRankedPages(total = 3): void {
  vi.mocked(api.searchExploreSystems).mockImplementation(
    async (request, _signal, _anchor, pageSize) =>
      finderPage(request.from ?? 0, pageSize ?? request.size ?? 50, total),
  );
  vi.mocked(api.getArchetypeRankings).mockImplementation(async (request) => ({
    ...finderPage(request.offset, request.page_size, total, true),
    archetype: request.archetype,
    archetype_label: 'Manufacturing Hub',
  }));
}

function anchorDetail(anchor: ExploreAnchor): api.SystemDetail {
  return {
    ...anchor,
    lifecycleState: 'ACTIVE',
    sourceBodyCount: null,
    loadedBodyCount: 0,
    sourceUpdatedAt: null,
    bodies: [],
    stations: [],
  };
}

/** The Finder shows tier C or better near a system unless told otherwise. */
const floorC = (enabled: boolean) =>
  enabled ? { min_development_score: 45 } : {};

function renderEnabled(
  query = 'page_size=1',
  state: App.PageState = {},
  viewState?: ExploreViewState,
) {
  const entries = [
    { url: new URL(`http://localhost/explore?${query}`), state },
  ];
  let index = 0;
  mockedGoto.mockImplementation(
    async (destination: string | URL, options = {}) => {
      const entry = {
        url: new URL(destination, mockedPage.url),
        state: options.state ?? {},
      };
      if (options.replaceState) entries[index] = entry;
      else {
        entries.splice(index + 1);
        entries.push(entry);
        index += 1;
      }
      mockedPage.url = entry.url;
      mockedPage.state = entry.state;
    },
  );
  const stateOwner =
    viewState ??
    new ExploreViewState(
      () => mockedPage,
      featureGate.enabled,
      applicationStores,
    );
  const component = renderComponent(ExploreWorkspaceTestHost, {
    viewState: stateOwner,
    initialPage: entries[0],
    installPage(model) {
      Object.defineProperties(mockedPage, {
        url: {
          configurable: true,
          get: () => model.url,
          set: (url: URL) => {
            model.url = url;
          },
        },
        state: {
          configurable: true,
          get: () => model.state,
          set: (state: App.PageState) => {
            model.state = state;
          },
        },
      });
    },
  });
  return {
    rerender: component.rerender,
    back() {
      index -= 1;
      mockedPage.url = entries[index].url;
      mockedPage.state = entries[index].state;
    },
    forward() {
      index += 1;
      mockedPage.url = entries[index].url;
      mockedPage.state = entries[index].state;
    },
    restore(query: string, restored: App.PageState = state) {
      mockedPage.url = new URL(`http://localhost/explore?${query}`);
      mockedPage.state = restored;
    },
  };
}

async function expectPage(index: number): Promise<void> {
  await waitFor(() =>
    expect(
      within(screen.getByRole('list', { name: 'System results' })).getByText(
        `Ranked ${index + 1}`,
      ),
    ).toBeInTheDocument(),
  );
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Next' })).toHaveProperty(
      'disabled',
      index === 2,
    ),
  );
  const list = screen.getByRole('list', { name: 'System results' });
  expect(within(list).getAllByRole('listitem')).toHaveLength(1);
  expect(within(list).getByText(`Ranked ${index + 1}`)).toBeInTheDocument();
  expect(
    screen.getByText(`Showing 1 of 3 (results ${index + 1}–${index + 1})`),
  ).toBeInTheDocument();
}

const emptyResultsPrompt = 'Search for a system above to see what is near it.';

function expectEmptyResults(): void {
  // Before a search the map is in front, with a prompt in the Finder.
  expect(screen.getByText(emptyResultsPrompt)).toBeInTheDocument();
  expect(
    screen.queryByRole('region', { name: /^(Near|Systems|Galaxy-wide)/ }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('list', { name: 'System results' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /^(Pin|Compare|Watch) Ranked/ }),
  ).not.toBeInTheDocument();
}

describe.each([false, true])(
  'ExploreWorkspace search intent (F4=%s)',
  (enabled) => {
    beforeEach(() => {
      vi.clearAllMocks();
      queryClient.clear();
      localStorage.clear();
      stubRuntime();
      mockFacadeDefaults();
      featureGate.enabled = enabled;
      mockRankedPages();
      vi.mocked(api.autocompleteSystems).mockResolvedValue({
        results: [anchorA],
      });
    });

    afterEach(() => {
      cleanup();
      featureGate.enabled = false;
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
    });

    it('starts empty without requesting results, even with cached rows, and with nothing on the map', async () => {
      const request = {
        galaxy_wide: true,
        sort_by: 'development' as const,
        size: enabled ? 50 : 24,
        from: 0,
      };
      queryClient.setQueryData(
        queryKeys.explore(request, null, enabled ? 50 : null),
        finderPage(0, request.size),
      );
      renderEnabled('');
      const map = await theMap();
      expectEmptyResults();
      // Nothing is chosen, so the map shows the galaxy alone and no stars
      // are asked for.
      expect(map.systemCount()).toBe(0);
      expect(map.flights).toHaveLength(0);
      expect(api.getCatalogueViewportSystems).not.toHaveBeenCalled();
      expect(api.searchExploreSystems).not.toHaveBeenCalled();
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
      expect(
        screen.queryByText(
          /Searching systems|Building a real-system shortlist/,
        ),
      ).not.toBeInTheDocument();
    });

    it('keeps the chosen reach when a system is picked from the suggestions', async () => {
      renderEnabled('reach=500');
      const input = screen.getByRole('combobox', { name: 'System name' });
      await fireEvent.input(input, { target: { value: 'Anchor' } });
      await fireEvent.click(
        await screen.findByRole('option', { name: /Anchor A/ }),
      );
      await screen.findByText('Ranked 1');
      expect(mockedPage.url.searchParams.get('reach')).toBe('500');
      expect(
        vi.mocked(api.searchExploreSystems).mock.lastCall?.[0].reach_ly,
      ).toBe(500);
    });

    it('loads nearby results only after an autocomplete anchor is chosen, then clears the rows', async () => {
      renderEnabled('');
      const input = screen.getByRole('combobox', { name: 'System name' });
      await fireEvent.input(input, { target: { value: 'Anchor' } });
      const option = await screen.findByRole('option', { name: /Anchor A/ });
      expect(api.searchExploreSystems).not.toHaveBeenCalled();
      await fireEvent.click(option);
      await screen.findByText('Ranked 1');
      expect(
        screen.getByRole('heading', { name: 'Near Anchor A' }),
      ).toBeInTheDocument();
      expect(vi.mocked(api.searchExploreSystems).mock.calls[0][0]).toEqual({
        reference_coords: { x: 10, y: 20, z: 30 },
        filters: { distance: { min: 0, max: 500 }, economy: 'any' },
        sort_by: 'distance',
        size: enabled ? 50 : 24,
        from: 0,
        reach_ly: 250,
        ...floorC(enabled),
      });
      const calls = vi.mocked(api.searchExploreSystems).mock.calls.length;
      await fireEvent.input(input, { target: { value: '' } });
      await waitFor(expectEmptyResults);
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(calls);
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    });

    it('loads an anchor restored from page state and reacts to history restoring or clearing it', async () => {
      const history = renderEnabled('', { anchor: anchorA });
      await screen.findByText('Ranked 1');
      expect(
        screen.getByRole('heading', { name: 'Near Anchor A' }),
      ).toBeInTheDocument();
      expect(screen.getByRole('combobox', { name: 'System name' })).toHaveValue(
        'Anchor A',
      );
      history.restore('', { anchor: anchorB });
      await waitFor(() =>
        expect(
          vi.mocked(api.searchExploreSystems).mock.lastCall?.[0]
            .reference_coords,
        ).toEqual({ x: -10, y: -20, z: -30 }),
      );
      expect(
        screen.getByRole('heading', { name: 'Near Anchor B' }),
      ).toBeInTheDocument();
      history.restore('', {});
      await waitFor(expectEmptyResults);
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(2);
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    });

    it('stays empty when the search is cleared before its response arrives', async () => {
      let finish!: (page: ReturnType<typeof finderPage>) => void;
      vi.mocked(api.searchExploreSystems).mockImplementation(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
      const navigation = renderEnabled('');
      await theMap();
      navigation.restore('', { anchor: anchorA });
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(1),
      );
      await fireEvent.input(
        screen.getByRole('combobox', { name: 'System name' }),
        { target: { value: '' } },
      );
      await waitFor(expectEmptyResults);
      finish(finderPage(0, enabled ? 50 : 24));
      await tick();
      expectEmptyResults();
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    });
  },
);

describe('ExploreWorkspace enabled Finder ranking surface', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    localStorage.clear();
    stubRuntime();
    mockFacadeDefaults();
    featureGate.enabled = true;
    mockRankedPages();
    vi.mocked(api.getSystem).mockImplementation(async (id64) => ({
      id64,
      name: `Restored ${id64}`,
      lifecycleState: 'ACTIVE',
      x: 50,
      y: 60,
      z: 70,
      sourceBodyCount: null,
      loadedBodyCount: 0,
      sourceUpdatedAt: null,
      bodies: [],
      stations: [],
    }));
  });

  afterEach(() => {
    cleanup();
    featureGate.enabled = false;
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it.each([
    'offset=1',
    'offset=0',
    'min-tier=A',
    'page_size=2',
    'archetype=any',
  ])(
    'loads an explicit Finder deep link without an anchor: %s',
    async (query) => {
      renderEnabled(query);
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(1),
      );
      expect(
        vi.mocked(api.searchExploreSystems).mock.calls[0][0],
      ).toMatchObject({ galaxy_wide: true });
      expect(
        screen.getByRole('heading', { name: 'Galaxy-wide opportunities' }),
      ).toBeInTheDocument();
      expect(screen.queryByText(emptyResultsPrompt)).not.toBeInTheDocument();
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    },
  );

  it('loads an archetype deep link immediately without an anchor', async () => {
    renderEnabled('archetype=manufacturing_hub&min-tier=A');
    await screen.findByText('Ranked 1');
    expect(api.getArchetypeRankings).toHaveBeenCalledWith(
      {
        archetype: 'manufacturing_hub',
        min_score: 76,
        page_size: 50,
        offset: 0,
        reach_ly: 250,
      },
      expect.anything(),
    );
    expect(api.searchExploreSystems).not.toHaveBeenCalled();
  });

  it('keeps an offset-only deep link active when its last-page correction removes the offset', async () => {
    renderEnabled('offset=9');
    await screen.findByText('Ranked 1');
    expect(mockedPage.url.search).toBe('');
    expect(
      vi
        .mocked(api.searchExploreSystems)
        .mock.calls.map(([request]) => request.from),
    ).toEqual([9, 0]);
    expect(
      screen.getByRole('heading', { name: 'Galaxy-wide opportunities' }),
    ).toBeInTheDocument();
    expect(screen.queryByText(emptyResultsPrompt)).not.toBeInTheDocument();
    expect(api.getArchetypeRankings).not.toHaveBeenCalled();
  });

  it('keeps results when Previous removes the last Finder URL parameter', async () => {
    renderEnabled('offset=1');
    await screen.findByText('Ranked 2');
    await fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    await screen.findByText('Ranked 1');
    expect(mockedPage.url.search).toBe('');
    expect(
      vi
        .mocked(api.searchExploreSystems)
        .mock.calls.map(([request]) => request.from),
    ).toEqual([1, 0]);
    expect(screen.queryByText(emptyResultsPrompt)).not.toBeInTheDocument();
    expect(api.getArchetypeRankings).not.toHaveBeenCalled();
  });

  it('loads galaxy-wide rankings when an archetype is chosen and returns to empty on Any', async () => {
    renderEnabled('');
    await theMap();
    expectEmptyResults();
    expect(api.searchExploreSystems).not.toHaveBeenCalled();
    expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    await fireEvent.change(
      screen.getByRole('combobox', { name: 'Colony archetype' }),
      { target: { value: 'manufacturing_hub' } },
    );
    await screen.findByText('Ranked 1');
    expect(
      screen.getByRole('heading', {
        name: 'Galaxy-wide Manufacturing Hub ranking',
      }),
    ).toBeInTheDocument();
    await fireEvent.change(
      screen.getByRole('combobox', { name: 'Colony archetype' }),
      { target: { value: 'any' } },
    );
    await waitFor(expectEmptyResults);
    expect(api.searchExploreSystems).not.toHaveBeenCalled();
    expect(api.getArchetypeRankings).toHaveBeenCalledTimes(1);
  });

  it('clears the default Any anchor without another request and restores it with Back', async () => {
    const history = renderEnabled('', { anchor: anchorA });
    await screen.findByText('Ranked 1');
    await fireEvent.click(
      screen.getByRole('button', { name: 'Stop searching near Anchor A' }),
    );
    await waitFor(expectEmptyResults);
    expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
    expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    history.back();
    await screen.findByText('Ranked 1');
    expect(
      screen.getByRole('heading', { name: 'Near Anchor A' }),
    ).toBeInTheDocument();
  });

  it('keeps the list empty when a cleared anchor request finishes late', async () => {
    let finish!: (page: ReturnType<typeof finderPage>) => void;
    vi.mocked(api.searchExploreSystems).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    renderEnabled('', { anchor: anchorA });
    await waitFor(() =>
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1),
    );
    await fireEvent.click(
      screen.getByRole('button', { name: 'Stop searching near Anchor A' }),
    );
    await waitFor(expectEmptyResults);
    finish(finderPage(0, 50));
    await tick();
    expectEmptyResults();
    expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
    expect(api.getArchetypeRankings).not.toHaveBeenCalled();
  });

  it('resolves a selection-only link without requesting a results list', async () => {
    renderEnabled(`selected=${anchorA.id64}`);
    await waitFor(() =>
      expect(api.getSystem).toHaveBeenCalledWith(
        anchorA.id64,
        expect.anything(),
      ),
    );
    // The selected system is put on the map and flown to.
    const map = await theMap();
    await waitFor(() => expect(map.getSelection()?.id64).toBe(anchorA.id64));
    expect(map.flights.at(-1)!.target).toMatchObject({ cursor: [50, 60, 70] });
    expectEmptyResults();
    expect(api.searchExploreSystems).not.toHaveBeenCalled();
    expect(api.getArchetypeRankings).not.toHaveBeenCalled();
  });

  it.each([
    [0, '0 results on this page.'],
    [1, '1 result on this page.'],
    [3, '3 results on this page.'],
  ])('announces the correct status for %i rows', async (count, status) => {
    mockRankedPages(count);
    renderEnabled('page_size=3');

    expect(await screen.findByText(status)).toBeInTheDocument();
  });

  it.each(['click', 'Enter', ' '])(
    "opens a result's details with %s, where its explanation opens apart from selection",
    async (activation) => {
      vi.mocked(api.getArchetypeExplanation).mockResolvedValue({
        kind: 'explanation_unavailable',
      });
      renderEnabled('archetype=manufacturing_hub&page_size=3');
      await screen.findByText('3 results on this page.');
      const list = screen.getByRole('list', { name: 'System results' });
      const rows = within(list).getAllByRole('listitem');
      const selection = rows.map((row) =>
        row.querySelector<HTMLButtonElement>('[data-result-select]')!,
      );
      const disclosureOf = (row: HTMLElement) =>
        row.querySelector<HTMLButtonElement>(
          '[data-archetype-explanation-toggle]',
        );

      // Each line gives the fit it is ranked by, with its tier; the details
      // wait for the result to be selected.
      expect(within(selection[0]).getByText('79')).toBeVisible();
      expect(within(selection[0]).getByText('Tier A')).toBeVisible();
      expect(disclosureOf(rows[0])).toBeNull();

      selection[0].focus();
      if (activation === 'click') await fireEvent.click(selection[0]);
      else await fireEvent.keyDown(selection[0], { key: activation });
      await waitFor(() =>
        expect(selection[0]).toHaveAttribute('aria-pressed', 'true'),
      );
      const disclosure = disclosureOf(rows[0])!;
      expect(disclosure).toBeInstanceOf(HTMLButtonElement);
      expect(disclosure.closest('[data-result-select]')).toBeNull();
      expect(disclosure.closest('[data-system-result]')).toBe(rows[0]);
      expect(
        within(rows[0]).getByText(/Best Colony Potential: 89/),
      ).toBeVisible();
      expect(
        within(rows[0]).getByText(/Manufacturing Hub fit: 79/),
      ).toBeVisible();
      expect(api.getArchetypeExplanation).not.toHaveBeenCalled();

      disclosure.focus();
      expect(disclosure).toHaveFocus();
      if (activation === 'click') await fireEvent.click(disclosure);
      else {
        await fireEvent.keyDown(disclosure, { key: activation });
        await fireEvent.keyUp(disclosure, { key: activation });
        // jsdom does not synthesize the native button click from a keyboard
        // event. detail: 0 is the browser's keyboard activation click.
        await fireEvent.click(disclosure, { detail: 0 });
      }
      await screen.findByText(
        'Explanation unavailable for this ranking version',
      );
      expect(disclosure).toHaveAttribute('aria-expanded', 'true');
      // Opening the explanation leaves the selection alone.
      expect(selection[0]).toHaveAttribute('aria-pressed', 'true');
      expect(api.getArchetypeExplanation).toHaveBeenCalledTimes(1);
      expect(api.getArchetypeExplanation).toHaveBeenCalledWith(
        rankedRows[0].id64,
        expect.objectContaining({
          derived_generation_id: rankingIdentity.derived_generation_id,
          archetype_version: rankingIdentity.archetype_version,
          archetype_manifest_sha256: rankingIdentity.archetype_manifest_sha256,
        }),
        expect.any(AbortSignal),
      );
      const region = within(rows[0]).getByRole('region', { name: 'Ranked 1' });
      expect(region).toHaveAttribute(
        'aria-labelledby',
        `finder-result-heading-${rankedRows[0].id64}`,
      );

      // Selecting another result moves the details to it, explanation shut.
      await fireEvent.click(selection[2]);
      await waitFor(() =>
        expect(selection[2]).toHaveAttribute('aria-pressed', 'true'),
      );
      expect(disclosureOf(rows[0])).toBeNull();
      expect(disclosureOf(rows[2])).toHaveAttribute('aria-expanded', 'false');
      expect(api.getArchetypeExplanation).toHaveBeenCalledTimes(1);
    },
  );

  /** A page as the API answers it before its matches are counted. */
  function uncountedPage(offset: number, pageSize: number, total = 3) {
    const counted = finderPage(offset, pageSize, total);
    const reaches = offset + counted.page.count;
    return {
      ...counted,
      page: {
        ...counted.page,
        // Not a count: how many matches the page shows there to be.
        total: reaches,
        navigable_total: counted.page.count >= pageSize ? reaches + 1 : reaches,
        truncation: {
          source: 'local-search' as const,
          total_is_capped: false,
          total_is_counted: false,
        },
      },
    };
  }
  const countsAskedFor = () =>
    vi
      .mocked(api.searchExploreSystems)
      .mock.calls.filter(([request]) => request.wait_for_count === true);

  it('shows the rows before a slow count, asks for the count on its own, and puts it in when it comes', async () => {
    // Seen on the live site on 15 Nov 2026: the first "Manufacturing Hub
    // near Sol" after the weekly backup failed after 18 s, because its count
    // passed the database's time limit, though its rows had been read in
    // under a second. The API now answers the rows without the count.
    let answerCount!: (page: ReturnType<typeof finderPage>) => void;
    vi.mocked(api.searchExploreSystems).mockImplementation(
      async (request, _signal, _anchor, pageSize) => {
        const size = pageSize ?? request.size ?? 50;
        if (request.wait_for_count)
          return new Promise((resolve) => {
            answerCount = () => resolve(finderPage(request.from ?? 0, size));
          });
        return uncountedPage(request.from ?? 0, size);
      },
    );
    renderEnabled('page_size=1');
    await screen.findByText('Showing results 1–1');
    expect(screen.getByText('Counting the matches…')).toBeInTheDocument();
    expect(
      within(screen.getByRole('list', { name: 'System results' })).getByText(
        'Ranked 1',
      ),
    ).toBeInTheDocument();
    // The page is full, so the next one can be asked for meanwhile.
    expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled();
    // The same search, told to wait for its count, once.
    await waitFor(() => expect(countsAskedFor()).toHaveLength(1));
    expect(countsAskedFor()[0][0]).toMatchObject({
      size: 1,
      from: 0,
      wait_for_count: true,
    });

    answerCount(finderPage(0, 1));
    await screen.findByText('Showing 1 of 3 (results 1–1)');
    expect(screen.getByText('3 matches')).toBeInTheDocument();
    expect(screen.queryByText('Counting the matches…')).toBeNull();
    expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled();
    expect(countsAskedFor()).toHaveLength(1);
  });

  it('says so when the matches could not be counted, and still pages to the end', async () => {
    vi.mocked(api.searchExploreSystems).mockImplementation(
      async (request, _signal, _anchor, pageSize) => {
        const size = pageSize ?? request.size ?? 50;
        // The count, given the whole time limit, still does not come.
        if (request.wait_for_count)
          return uncountedPage(request.from ?? 0, size);
        return uncountedPage(request.from ?? 0, size);
      },
    );
    renderEnabled('page_size=2');
    await screen.findByText('Showing results 1–2');
    await screen.findByText(
      /^At least 2\s+matches; they could not be counted in\s+time\.$/u,
    );
    expect(screen.queryByText('Counting the matches…')).toBeNull();
    const next = screen.getByRole('button', { name: 'Next' });
    expect(next).toBeEnabled();
    await fireEvent.click(next);
    // The second page is short: it holds the last of the three matches,
    // which the API reports as a count.
    vi.mocked(api.searchExploreSystems).mockImplementation(
      async (request, _signal, _anchor, pageSize) =>
        finderPage(request.from ?? 0, pageSize ?? request.size ?? 50),
    );
    await screen.findByText('Showing 1 of 3 (results 3–3)');
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
  });

  it('says so, and keeps the rows, when asking for the count fails', async () => {
    vi.mocked(api.searchExploreSystems).mockImplementation(
      async (request, _signal, _anchor, pageSize) => {
        if (request.wait_for_count)
          throw new api.ApiError(503, '/api/local/search', {});
        return uncountedPage(request.from ?? 0, pageSize ?? request.size ?? 50);
      },
    );
    renderEnabled('page_size=1');
    await screen.findByText(
      /^At least 1\s+match; they could not be counted in\s+time\.$/u,
    );
    expect(
      within(screen.getByRole('list', { name: 'System results' })).getByText(
        'Ranked 1',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(countsAskedFor()).toHaveLength(1);
  });

  it('asks for no count on its own where the page came with one', async () => {
    renderEnabled('page_size=1');
    await screen.findByText('Showing 1 of 3 (results 1–1)');
    await new Promise((done) => setTimeout(done, 20));
    expect(countsAskedFor()).toHaveLength(0);
  });

  it('styles both paging buttons and keeps Previous disabled on the first page', async () => {
    renderEnabled('page_size=1');
    await screen.findByText('1 result on this page.');

    const paging = within(
      screen.getByRole('navigation', { name: 'Result pages' }),
    );
    const previous = paging.getByRole('button', { name: 'Previous' });
    const next = paging.getByRole('button', { name: 'Next' });
    expect(previous).toHaveClass('quiet-button');
    expect(next).toHaveClass('quiet-button');
    expect(previous).toBeDisabled();
    expect(next).toBeEnabled();
  });

  it.each(['any', 'selected'])(
    'keeps %s one-row page requests, range and list together across three pages',
    async (mode) => {
      renderEnabled(
        mode === 'any'
          ? 'page_size=1'
          : 'archetype=manufacturing_hub&page_size=1',
      );
      for (let index = 0; index < 3; index += 1) {
        await expectPage(index);
        expect(screen.getByRole('button', { name: 'Previous' })).toHaveProperty(
          'disabled',
          index === 0,
        );
        if (index < 2)
          await fireEvent.click(screen.getByRole('button', { name: 'Next' }));
      }
      if (mode === 'any') {
        expect(
          vi
            .mocked(api.searchExploreSystems)
            .mock.calls.map(([request, , , pageSize]) => ({
              size: request.size,
              from: request.from,
              pageSize,
            })),
        ).toEqual([0, 1, 2].map((from) => ({ size: 1, from, pageSize: 1 })));
        expect(api.getArchetypeRankings).not.toHaveBeenCalled();
      } else {
        expect(
          vi
            .mocked(api.getArchetypeRankings)
            .mock.calls.map(([request]) => request),
        ).toEqual(
          [0, 1, 2].map((offset) => ({
            archetype: 'manufacturing_hub',
            min_score: 60,
            page_size: 1,
            offset,
            reach_ly: 250,
          })),
        );
        expect(api.searchExploreSystems).not.toHaveBeenCalled();
      }
    },
  );

  it('replaces a positive stale offset once and refetches with the committed anchor', async () => {
    renderEnabled(`page_size=1&offset=9&view=cards&near=${anchorA.id64}`, {
      anchor: anchorA,
    });
    await screen.findByText('Page reset to the last available results.');
    await waitFor(() =>
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(2),
    );
    expect(mockedGoto).toHaveBeenCalledTimes(1);
    expect(mockedGoto).toHaveBeenCalledWith(
      `/explore?page_size=1&offset=2&view=cards&near=${anchorA.id64}`,
      expect.objectContaining({
        replaceState: true,
        state: { anchor: anchorA, finderRequested: true },
      }),
    );
    expect(
      vi.mocked(api.searchExploreSystems).mock.calls.map(([request]) => ({
        from: request.from,
        reference: request.reference_coords,
      })),
    ).toEqual([
      { from: 9, reference: { x: 10, y: 20, z: 30 } },
      { from: 2, reference: { x: 10, y: 20, z: 30 } },
    ]);
    await expectPage(2);
    expect(screen.queryByText(/No systems match/)).not.toBeInTheDocument();
  });

  it('resets offset on tier and page-size commits and resizes the list page', async () => {
    renderEnabled(
      'archetype=manufacturing_hub&min-tier=A&page_size=1&offset=1',
    );
    await screen.findByText('Ranked 2');
    await fireEvent.change(
      screen.getByRole('combobox', { name: 'Show only' }),
      { target: { value: 'S' } },
    );
    await waitFor(() =>
      expect(api.getArchetypeRankings).toHaveBeenLastCalledWith(
        {
          archetype: 'manufacturing_hub',
          min_score: 88,
          page_size: 1,
          offset: 0,
          reach_ly: 250,
        },
        expect.anything(),
      ),
    );
    expect(mockedPage.url.searchParams.has('offset')).toBe(false);
    // Results per page sits with Previous and Next, under the list.
    const pageSize = screen.getByRole('spinbutton', {
      name: 'Results per page',
    });
    expect(
      screen.getByRole('navigation', { name: 'Result pages' }),
    ).toContainElement(pageSize);
    await fireEvent.change(pageSize, { target: { value: '2' } });
    await screen.findByText('Showing 2 of 3 (results 1–2)');
    expect(api.getArchetypeRankings).toHaveBeenLastCalledWith(
      {
        archetype: 'manufacturing_hub',
        min_score: 88,
        page_size: 2,
        offset: 0,
        reach_ly: 250,
      },
      expect.anything(),
    );
    expect(
      within(screen.getByRole('list', { name: 'System results' })).getAllByRole(
        'listitem',
      ),
    ).toHaveLength(2);
  });

  it('keeps a genuine zero-result selected page empty without rewriting its offset', async () => {
    mockRankedPages(0);
    renderEnabled(
      'archetype=manufacturing_hub&min-tier=S&page_size=1&offset=9',
    );
    await screen.findByText(
      'No systems meet minimum tier S for Manufacturing Hub.',
    );
    expect(mockedGoto).not.toHaveBeenCalled();
    expect(
      screen.getByRole('button', { name: 'Reset to tier B' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Choose Any' }),
    ).toBeInTheDocument();
    await fireEvent.click(
      screen.getByRole('button', { name: 'Reset to tier B' }),
    );
    await waitFor(() =>
      expect(mockedPage.url.search).toBe(
        '?archetype=manufacturing_hub&page_size=1',
      ),
    );
  });

  it('canonicalizes a fresh huge offset before the first request reaches the API', async () => {
    vi.mocked(api.searchExploreSystems).mockImplementation(async (request) => ({
      ...finderPage(request.from ?? 0, 1, 1_000),
      results: [rankedRows[0]],
    }));
    renderEnabled('page_size=1&offset=2000000000');
    await screen.findByText('Ranked 1');
    expect(mockedGoto).toHaveBeenCalledTimes(1);
    expect(mockedGoto).toHaveBeenCalledWith(
      '/explore?page_size=1&offset=999',
      expect.objectContaining({ replaceState: true }),
    );
    expect(
      vi.mocked(api.searchExploreSystems).mock.calls.map(([request]) => ({
        from: request.from,
        size: request.size,
      })),
    ).toEqual([{ from: 999, size: 1 }]);
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
  });

  it('canonicalizes a non-finite restored anchor as unanchored before dispatch', async () => {
    renderEnabled('page_size=1', { anchor: { ...anchorA, x: Infinity } });
    await screen.findByText('Ranked 1');
    expect(mockedPage.state.anchor).toBeNull();
    expect(mockedGoto).toHaveBeenCalledTimes(1);
    expect(mockedGoto.mock.calls[0][1].replaceState).toBe(true);
    expect(api.searchExploreSystems).toHaveBeenCalledWith(
      expect.objectContaining({ galaxy_wide: true }),
      expect.anything(),
      null,
      1,
    );
    expect(
      screen.queryByRole('button', { name: /Stop searching near/ }),
    ).not.toBeInTheDocument();
  });

  it('keeps old envelopes visibly updating during history navigation without correcting from placeholder totals', async () => {
    let finish!: (response: api.ArchetypeRankingsPage) => void;
    vi.mocked(api.getArchetypeRankings).mockImplementation((request) => {
      if (request.offset === 9)
        return new Promise((resolve) => {
          finish = resolve;
        });
      return Promise.resolve({
        ...finderPage(0, 1, 1, true),
        archetype: request.archetype,
        archetype_label: 'Manufacturing Hub',
      });
    });
    const history = renderEnabled('archetype=manufacturing_hub&page_size=1');
    await screen.findByText('Ranked 1');
    history.restore('archetype=manufacturing_hub&page_size=1&offset=9');
    await screen.findByText(/Updating…/);
    expect(screen.getByText('Ranked 1')).toBeInTheDocument();
    expect(mockedGoto).not.toHaveBeenCalled();
    expect(screen.queryByText(/No systems meet/)).not.toBeInTheDocument();
    finish({
      ...finderPage(9, 1, 10, true),
      results: [rankedRows[2]],
      archetype: 'manufacturing_hub',
      archetype_label: 'Manufacturing Hub',
    });
    await screen.findByText('Showing 1 of 10 (results 10–10)');
    expect(mockedGoto).not.toHaveBeenCalled();
  });

  it('retains the committed anchor while draft text changes and selected mode switches back to Any', async () => {
    let finishAny!: (
      page: Awaited<ReturnType<typeof api.searchExploreSystems>>,
    ) => void;
    // The type search near the anchor answers at once; the Any search that
    // follows waits, so the type's rows are what stays on screen meanwhile.
    vi.mocked(api.searchExploreSystems).mockImplementation(
      (request, _signal, _anchor, pageSize) =>
        request.archetype
          ? Promise.resolve(finderPage(request.from ?? 0, pageSize ?? 1))
          : new Promise((resolve) => {
              finishAny = resolve;
            }),
    );
    renderEnabled(
      `archetype=manufacturing_hub&page_size=1&offset=1&near=${anchorA.id64}`,
      {
        anchor: anchorA,
      },
    );
    await screen.findByText('Ranked 2');
    const input = screen.getByRole('combobox', { name: 'System name' });
    expect(input).toHaveValue('Anchor A');
    await fireEvent.input(input, { target: { value: 'Draft other star' } });
    expect(mockedPage.state.anchor).toEqual(anchorA);
    expect(mockedPage.url.searchParams.get('offset')).toBe('1');
    expect(mockedGoto).not.toHaveBeenCalled();
    await fireEvent.change(
      screen.getByRole('combobox', { name: 'Colony archetype' }),
      { target: { value: 'any' } },
    );
    await waitFor(() =>
      expect(api.searchExploreSystems).toHaveBeenLastCalledWith(
        expect.objectContaining({
          reference_coords: { x: 10, y: 20, z: 30 },
          from: 0,
        }),
        expect.anything(),
        { id64: anchorA.id64, name: anchorA.name },
        1,
      ),
    );
    expect(
      vi.mocked(api.searchExploreSystems).mock.lastCall?.[0],
    ).not.toHaveProperty('archetype');
    const retainedList = screen.getByRole('list', { name: 'System results' });
    expect(within(retainedList).getByText('Ranked 2')).toBeInTheDocument();
    expect(retainedList).not.toHaveTextContent(/fit/i);
    expect(
      within(retainedList).getByRole('button', { name: /^Ranked 2/ }),
    ).not.toHaveAccessibleName(/fit/i);
    expect(screen.getByText(/Updating…/)).toBeInTheDocument();
    finishAny(finderPage(0));
    await screen.findByText('Ranked 1');
  });

  it('clears anchor state in one push, preserves selection/detail, resets offset and restores anchor with Back', async () => {
    const history = renderEnabled(
      `page_size=1&offset=1&selected=9007199254741001&system=9007199254741002&view=cards&near=${anchorA.id64}`,
      { anchor: anchorA },
    );
    await screen.findByText('Ranked 2');
    await fireEvent.click(
      screen.getByRole('button', { name: 'Stop searching near Anchor A' }),
    );
    await screen.findByText('Showing the whole galaxy, best first.');
    expect(mockedPage.state.anchor).toBeNull();
    expect(mockedPage.url.search).toBe(
      '?page_size=1&selected=9007199254741001&system=9007199254741002&view=cards',
    );
    expect(mockedGoto).toHaveBeenCalledTimes(1);
    expect(mockedGoto.mock.calls[0][1].replaceState).toBe(false);
    expect(screen.getByRole('combobox', { name: 'System name' })).toHaveFocus();
    expect(screen.getByRole('combobox', { name: 'System name' })).toHaveValue(
      '',
    );
    expect(
      screen.queryByRole('button', { name: /Stop searching near/ }),
    ).not.toBeInTheDocument();
    await waitFor(() =>
      expect(api.searchExploreSystems).toHaveBeenLastCalledWith(
        expect.objectContaining({ galaxy_wide: true, from: 0 }),
        expect.anything(),
        null,
        1,
      ),
    );
    history.back();
    await screen.findByRole('button', { name: 'Stop searching near Anchor A' });
    expect(screen.getByRole('combobox', { name: 'System name' })).toHaveValue(
      'Anchor A',
    );
    await waitFor(() =>
      expect(api.searchExploreSystems).toHaveBeenLastCalledWith(
        expect.objectContaining({
          reference_coords: { x: 10, y: 20, z: 30 },
          from: 1,
        }),
        expect.anything(),
        { id64: anchorA.id64, name: anchorA.name },
        1,
      ),
    );
  });

  it('keeps selected archetype and tier when clearing its remembered anchor', async () => {
    renderEnabled(
      'archetype=manufacturing_hub&min-tier=A&page_size=1&offset=1',
      { anchor: anchorA },
    );
    await screen.findByText('Ranked 2');
    await fireEvent.click(
      screen.getByRole('button', { name: 'Stop searching near Anchor A' }),
    );
    await waitFor(() => expect(mockedPage.state.anchor).toBeNull());
    expect(mockedPage.url.search).toBe(
      '?archetype=manufacturing_hub&min-tier=A&page_size=1',
    );
    // Near the anchor it was a type search near a system; without it, the
    // galaxy-wide ranking for the same type and tier.
    expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(api.getArchetypeRankings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          archetype: 'manufacturing_hub',
          min_score: 76,
          offset: 0,
        }),
        expect.anything(),
      ),
    );
    expect(screen.getByRole('combobox', { name: 'Show only' })).toHaveValue(
      'A',
    );
  });

  it('exposes the search box clear button with no automated accessibility violations', async () => {
    renderEnabled('page_size=1', { anchor: anchorA });
    const clear = await screen.findByRole('button', {
      name: 'Stop searching near Anchor A',
    });
    // An × inside the search box; its name says what it does.
    expect(clear.closest('.search-control')).toContainElement(
      screen.getByRole('combobox', { name: 'System name' }),
    );
    expect(clear.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    expect(clear).toHaveAttribute('type', 'button');
    expect(clear.tabIndex).toBe(0);
    const result = await axe.run(clear.parentElement!, {
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });

  it('commits anchor B and restores anchor A coordinates, input, selection and offset with Back', async () => {
    vi.mocked(api.autocompleteSystems).mockResolvedValue({
      results: [anchorB],
    });
    const history = renderEnabled(
      'page_size=1&offset=1&selected=9007199254740993',
      { anchor: anchorA },
    );
    await screen.findByText('Ranked 2');
    await fireEvent.input(
      screen.getByRole('combobox', { name: 'System name' }),
      { target: { value: 'Anchor B' } },
    );
    await fireEvent.click(
      await screen.findByRole('option', { name: /Anchor B/ }),
    );
    await waitFor(() => expect(mockedPage.state.anchor).toEqual(anchorB));
    expect(mockedPage.url.search).toBe(
      '?page_size=1&selected=9007199254740994&near=9007199254740994',
    );
    history.back();
    await screen.findByRole('button', { name: 'Stop searching near Anchor A' });
    expect(screen.getByRole('combobox', { name: 'System name' })).toHaveValue(
      'Anchor A',
    );
    expect(mockedPage.url.search).toBe(
      '?page_size=1&offset=1&selected=9007199254740993&near=9007199254740993',
    );
    await waitFor(() =>
      expect(api.searchExploreSystems).toHaveBeenLastCalledWith(
        expect.objectContaining({
          reference_coords: { x: 10, y: 20, z: 30 },
          from: 1,
        }),
        expect.anything(),
        { id64: anchorA.id64, name: anchorA.name },
        1,
      ),
    );
  });

  it('a result, and SHOW ON MAP in the map’s panel, commit lossless passive selection without opening detail', async () => {
    // A star 3 ly from Ranked 1, so it is one of its stars in range.
    const star = {
      ...catalogueFixture.systems[0],
      id64: parseId64('9007199254741200'),
      name: 'Nearby star',
      x: 12,
      y: 1,
      z: 22,
      mainStarClass: 'M3',
    };
    vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue({
      systems: [star],
      truncated: false,
    });
    renderEnabled('page_size=1&view=cards', { anchor: anchorA });
    await screen.findByText('Ranked 1');
    await fireEvent.click(screen.getByRole('button', { name: /^Ranked 1\b/ }));
    await waitFor(() =>
      expect(mockedPage.url.searchParams.get('selected')).toBe(
        rankedRows[0].id64,
      ),
    );
    expect(mockedPage.url.searchParams.has('system')).toBe(false);
    expect(mockedPage.state.anchor).toEqual(anchorA);
    expect(mockedPage.url.searchParams.get('view')).toBe('cards');

    const map = await theMap();
    await waitFor(() =>
      expect(onTheMap(map)).toEqual([rankedRows[0].id64, star.id64]),
    );
    const panel = (await map.panel(star.id64))!;
    expect(panel.actions!.map((action) => action.label)).toEqual([
      'SHOW ON MAP',
      'INSPECT',
    ]);
    panel.actions![0].onSelect(map.systems.get(star.id64)!);
    await waitFor(() =>
      expect(mockedPage.url.searchParams.get('selected')).toBe(star.id64),
    );
    expect(mockedPage.url.searchParams.has('system')).toBe(false);
    expect(mockedPage.state.anchor).toEqual(anchorA);
    // The star is now the chosen system: selected and flown to, with no
    // request for a position the map already gave.
    await waitFor(() => expect(map.getSelection()?.id64).toBe(star.id64));
    expect(map.flights.at(-1)!.target).toMatchObject({ cursor: [12, 1, 22] });
    expect(api.getSystem).not.toHaveBeenCalledWith(
      star.id64,
      expect.anything(),
    );
  });

  it('carries the Finder query and committed anchor when the map’s panel opens a star in Inspect', async () => {
    const star = {
      ...catalogueFixture.systems[0],
      id64: parseId64('9007199254741200'),
      name: 'Nearby G2 star',
      x: 12,
      y: 1,
      z: 22,
      mainStarClass: 'G2',
    };
    vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue({
      systems: [star],
      truncated: false,
    });
    renderEnabled(`page_size=1&view=cards&selected=${rankedRows[0].id64}`, {
      anchor: anchorA,
    });
    await screen.findAllByText('Ranked 1');
    const map = await theMap();
    await waitFor(() => expect(onTheMap(map)).toContain(star.id64));
    const origin = mockedPage.url.searchParams.toString();
    mockedGoto.mockClear();
    mockedGoto.mockResolvedValueOnce(undefined);

    const panel = (await map.panel(star.id64))!;
    panel.actions![1].onSelect(map.systems.get(star.id64)!);
    await waitFor(() => expect(mockedGoto).toHaveBeenCalledOnce());
    const destination = new URL(mockedGoto.mock.calls[0][0], mockedPage.url);
    expect(destination.pathname).toBe('/inspect');
    expect(destination.searchParams.get('from')).toBe(origin);
    expect(destination.searchParams.get('system')).toBe(star.id64);
    expect(mockedGoto.mock.calls[0][1].state.anchor).toEqual(anchorA);
  });

  it('keeps the selected system on the map across pages without asking for its position again', async () => {
    renderEnabled('page_size=1');
    await expectPage(0);
    await fireEvent.click(screen.getByRole('button', { name: /^Ranked 1\b/ }));
    const map = await theMap();
    await waitFor(() =>
      expect(map.getSelection()?.id64).toBe(rankedRows[0].id64),
    );
    expect(onTheMap(map)).toEqual([rankedRows[0].id64]);
    await fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Ranked 2');
    // The map shows the selected system, not the page of results.
    expect(onTheMap(map)).toEqual([rankedRows[0].id64]);
    expect(map.getSelection()?.id64).toBe(rankedRows[0].id64);
    expect(map.flights).toHaveLength(1);
    expect(api.getSystem).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    await expectPage(0);
    expect(onTheMap(map)).toEqual([rankedRows[0].id64]);
    expect(map.flights).toHaveLength(1);
  });

  it.each(['any', 'selected'])(
    'shows an off-page %s selection on the map only once getSystem has given its position',
    async (mode) => {
      let finishSelection!: (detail: api.SystemDetail) => void;
      vi.mocked(api.getSystem).mockImplementation(
        () =>
          new Promise((resolve) => {
            finishSelection = resolve;
          }),
      );
      renderEnabled(
        `${mode === 'selected' ? 'archetype=manufacturing_hub&' : ''}page_size=1&offset=1&selected=${anchorA.id64}`,
      );
      await screen.findByText('Ranked 2');
      await waitFor(() =>
        expect(api.getSystem).toHaveBeenCalledExactlyOnceWith(
          anchorA.id64,
          expect.anything(),
        ),
      );
      const map = await theMap();
      expect(
        screen.getByRole('heading', { name: `System ${anchorA.id64}` }),
      ).toBeInTheDocument();
      // Its position is not known yet: nothing is on the map, least of all
      // a marker at a made-up place.
      expect(map.systemCount()).toBe(0);
      expect(map.flights).toHaveLength(0);
      finishSelection({
        id64: anchorA.id64,
        name: 'Resolved selection',
        lifecycleState: 'ACTIVE',
        x: 50,
        y: 60,
        z: 70,
        sourceBodyCount: null,
        loadedBodyCount: 0,
        sourceUpdatedAt: null,
        bodies: [],
        stations: [],
      });
      await screen.findByRole('heading', { name: 'Resolved selection' });
      expect(
        within(
          screen.getByRole('list', { name: 'System results' }),
        ).queryByText('Resolved selection'),
      ).not.toBeInTheDocument();
      await waitFor(() => expect(onTheMap(map)).toEqual([anchorA.id64]));
      expect(map.getSelection()).toMatchObject({
        name: 'Resolved selection',
        position: [50, 60, 70],
      });
      expect(api.getSystem).toHaveBeenCalledTimes(1);
      expect(mockedPage.url.searchParams.get('selected')).toBe(anchorA.id64);
      expect(mockedPage.url.searchParams.has('system')).toBe(false);
    },
  );

  it.each([
    {
      label: 'finite coordinates',
      id64: anchorA.id64,
      x: 50,
      y: 60,
      z: 70,
      visible: true,
    },
    {
      label: 'missing coordinates',
      id64: anchorA.id64,
      x: null,
      y: null,
      z: null,
      visible: false,
    },
    {
      label: 'nonfinite coordinates',
      id64: anchorA.id64,
      x: Infinity,
      y: 1,
      z: 2,
      visible: false,
    },
    {
      label: 'legacy origin',
      id64: anchorA.id64,
      x: 0,
      y: 0,
      z: 0,
      visible: false,
    },
    {
      label: 'Sol at the origin',
      id64: parseId64('10477373803'),
      x: 0,
      y: 0,
      z: 0,
      visible: true,
    },
  ])(
    'puts a selection read from getSystem on the map with $label, never at a place it does not have',
    async ({ id64, x, y, z, visible }) => {
      mockRankedPages(0);
      vi.mocked(api.getSystem).mockResolvedValue({
        id64,
        name: 'Reconstructed selection',
        lifecycleState: 'ACTIVE',
        x,
        y,
        z,
        sourceBodyCount: null,
        loadedBodyCount: 0,
        sourceUpdatedAt: null,
        bodies: [],
        stations: [],
      } as api.SystemDetail);
      renderEnabled(`page_size=1&selected=${id64}`);
      await screen.findByRole('heading', { name: 'Reconstructed selection' });
      expect(api.getSystem).toHaveBeenCalledWith(id64, expect.anything());
      const map = await theMap();
      await waitFor(() => expect(onTheMap(map)).toEqual(visible ? [id64] : []));
      if (visible)
        expect(map.getSelection()).toMatchObject({ id64, position: [x, y, z] });
      else {
        expect(map.flights).toHaveLength(0);
        expect(
          screen.getAllByText(
            'Map coordinates unavailable for the selected system.',
          ).length,
        ).toBeGreaterThan(0);
      }
      expect(mockedPage.url.searchParams.get('selected')).toBe(id64);
      expect(mockedPage.url.searchParams.has('system')).toBe(false);
    },
  );

  it('retains passive selection and an empty map when getSystem fails', async () => {
    mockRankedPages(0);
    vi.mocked(api.getSystem).mockRejectedValue(
      new api.ApiError(503, '/api/system', {}),
    );
    renderEnabled(`page_size=1&selected=${anchorA.id64}`);
    await waitFor(() => expect(api.getSystem).toHaveBeenCalledTimes(1));
    const map = await theMap();
    await screen.findByRole('heading', { name: `System ${anchorA.id64}` });
    expect(map.systemCount()).toBe(0);
    expect(map.flights).toHaveLength(0);
    expect(mockedPage.url.searchParams.get('selected')).toBe(anchorA.id64);
  });

  it('ignores a late selected-point response after selection changes', async () => {
    mockRankedPages(0);
    let finishA!: (value: api.SystemDetail) => void;
    vi.mocked(api.getSystem).mockImplementation((id64) => {
      if (id64 === anchorA.id64)
        return new Promise((resolve) => {
          finishA = resolve;
        });
      return Promise.resolve({
        id64,
        name: 'Selection B',
        x: 40,
        y: 50,
        z: 60,
        lifecycleState: 'ACTIVE',
        sourceBodyCount: null,
        loadedBodyCount: 0,
        sourceUpdatedAt: null,
        bodies: [],
        stations: [],
      });
    });
    const history = renderEnabled(`page_size=1&selected=${anchorA.id64}`);
    await waitFor(() =>
      expect(api.getSystem).toHaveBeenCalledWith(
        anchorA.id64,
        expect.anything(),
      ),
    );
    history.restore(`page_size=1&selected=${anchorB.id64}`);
    await screen.findByRole('heading', { name: 'Selection B' });
    finishA({
      id64: anchorA.id64,
      name: 'Late A',
      x: 1,
      y: 2,
      z: 3,
      lifecycleState: 'ACTIVE',
      sourceBodyCount: null,
      loadedBodyCount: 0,
      sourceUpdatedAt: null,
      bodies: [],
      stations: [],
    });
    const map = await theMap();
    await waitFor(() => expect(onTheMap(map)).toEqual([anchorB.id64]));
    expect(
      screen.queryByRole('heading', { name: 'Late A' }),
    ).not.toBeInTheDocument();
    expect(map.getSelection()).toMatchObject({
      id64: anchorB.id64,
      position: [40, 50, 60],
    });
  });

  it('clears URL-owned selection when a history entry omits selected while preserving explicit detail', async () => {
    const history = renderEnabled(`page_size=1&selected=${rankedRows[0].id64}`);
    const map = await theMap();
    await waitFor(() =>
      expect(map.getSelection()?.id64).toBe(rankedRows[0].id64),
    );
    history.restore(`page_size=1&system=${rankedRows[0].id64}`);
    // Nothing is selected any more, so nothing is on the map.
    await waitFor(() => expect(map.systemCount()).toBe(0));
    expect(map.getSelection()).toBeNull();
    expect(mockedPage.url.searchParams.get('system')).toBe(rankedRows[0].id64);
  });

  it('carries the query and committed anchor on result Inspect navigation', async () => {
    renderEnabled(
      'archetype=manufacturing_hub&min-tier=A&page_size=1&offset=1',
      { anchor: anchorA },
    );
    await screen.findByText('Ranked 2');
    const origin = mockedPage.url.searchParams.toString();
    mockedGoto.mockResolvedValueOnce(undefined);
    await fireEvent.click(
      screen.getByRole('link', { name: 'Inspect Ranked 2' }),
    );
    const destination = new URL(mockedGoto.mock.lastCall![0], mockedPage.url);
    expect(destination.pathname).toBe('/inspect');
    expect(destination.searchParams.get('from')).toBe(origin);
    expect(destination.searchParams.get('system')).toBe(rankedRows[1].id64);
    expect(mockedGoto.mock.lastCall![1].state.anchor).toEqual(anchorA);
  });

  it.each([
    {
      body: { detail: { reason: 'finder_products_not_ready' } },
      heading: 'Ranking data not ready',
    },
    {
      body: { reason: 'finder_products_not_ready' },
      heading: 'Ranking data not ready',
    },
    {
      body: { detail: 'finder_products_not_ready' },
      heading: 'Ranking temporarily unavailable',
    },
    {
      body: 'finder_products_not_ready',
      heading: 'Ranking temporarily unavailable',
    },
  ])(
    'selects readiness copy only from the typed reason: $heading',
    async ({ body, heading }) => {
      vi.mocked(api.getArchetypeRankings).mockRejectedValue(
        new api.ApiError(503, '/api/archetypes/rankings', body),
      );
      renderEnabled('archetype=manufacturing_hub&page_size=1');
      await screen.findByRole('heading', { name: heading });
      expect(screen.queryByText(/No systems meet/)).not.toBeInTheDocument();
      expect(
        within(
          screen.getByRole('region', {
            name: 'Galaxy-wide Manufacturing Hub ranking',
          }),
        ).getByRole('button', { name: 'Retry' }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: 'Choose Any' }),
      ).toBeInTheDocument();
    },
  );

  it('preserves picker and pagination focus instead of focusing results on ordinary refetches', async () => {
    renderEnabled('page_size=1', { anchor: anchorA });
    await expectPage(0);
    const next = screen.getByRole('button', { name: 'Next' });
    next.focus();
    await fireEvent.click(next);
    await screen.findByText('Ranked 2');
    await waitFor(() => expect(next).toHaveFocus());
    const picker = screen.getByRole('combobox', { name: 'Colony archetype' });
    picker.focus();
    await fireEvent.change(picker, { target: { value: 'manufacturing_hub' } });
    await waitFor(() =>
      expect(
        vi.mocked(api.searchExploreSystems).mock.lastCall?.[0],
      ).toMatchObject({ archetype: 'manufacturing_hub' }),
    );
    expect(picker).toHaveFocus();
  });

  it('focuses the first result only after an explicit new Any anchor', async () => {
    vi.mocked(api.autocompleteSystems).mockResolvedValue({
      results: [anchorA],
    });
    renderEnabled('page_size=1');
    await expectPage(0);
    const input = screen.getByRole('combobox', { name: 'System name' });
    input.focus();
    await fireEvent.input(input, { target: { value: 'Anchor A' } });
    await fireEvent.click(
      await screen.findByRole('option', { name: /Anchor A/ }),
    );
    await waitFor(() =>
      expect(
        within(screen.getByRole('list', { name: 'System results' })).getByRole(
          'button',
          { name: /^Ranked 1\b/ },
        ),
      ).toHaveFocus(),
    );
    expect(mockedPage.state.anchor).toEqual(anchorA);
  });

  it('keeps the map on the selected system when Find’s results are fetched again', async () => {
    renderEnabled('page_size=2', { anchor: anchorA });
    await screen.findByText('Showing 2 of 3 (results 1–2)');
    await fireEvent.click(
      within(screen.getByRole('list', { name: 'System results' })).getByRole(
        'button',
        { name: /^Ranked 1\b/ },
      ),
    );
    const map = await theMap();
    await waitFor(() =>
      expect(map.getSelection()?.id64).toBe(rankedRows[0].id64),
    );
    expect(map.flights).toHaveLength(1);
    expect(map.flights[0].target).toEqual({
      cursor: [10, 1, 20],
      distance: 70,
      pitch: 35,
    });
    expect(mockedPage.url.searchParams.get('selected')).toBe(
      rankedRows[0].id64,
    );
    expect(mockedPage.state.anchor).toEqual(anchorA);

    // The results are fetched again while the flight is still in the air.
    let finish!: (
      response: Awaited<ReturnType<typeof api.searchExploreSystems>>,
    ) => void;
    vi.mocked(api.searchExploreSystems).mockImplementationOnce(
      () => new Promise((resolve) => (finish = resolve)),
    );
    const refetch = queryClient.invalidateQueries({
      queryKey: [...queryKeys.all, 'explore', 'results'],
    });
    await screen.findByText(/Updating…/);
    expect(screen.getByText('Ranked 2')).toBeInTheDocument();
    finish({
      ...finderPage(0, 2),
      results: [rankedRows[0], rankedRows[2]],
    });
    await refetch;
    await screen.findByText('Ranked 3');

    // The flight lands where it was sent, and the system stays selected.
    map.land();
    await tick();
    expect(map.getView()).toMatchObject({ cursor: [10, 1, 20], distance: 70 });
    expect(map.getSelection()?.id64).toBe(rankedRows[0].id64);
    expect(onTheMap(map)).toEqual([rankedRows[0].id64]);
  });

  it('cancels pending anchor autofocus when the user changes archetype before the anchored response', async () => {
    let finishAnchor!: (
      response: Awaited<ReturnType<typeof api.searchExploreSystems>>,
    ) => void;
    vi.mocked(api.searchExploreSystems).mockImplementation((request) =>
      request.reference_coords
        ? new Promise((resolve) => {
            finishAnchor = resolve;
          })
        : Promise.resolve(finderPage(0)),
    );
    vi.mocked(api.autocompleteSystems).mockResolvedValue({
      results: [anchorA],
    });
    renderEnabled('page_size=1');
    await expectPage(0);
    const input = screen.getByRole('combobox', { name: 'System name' });
    input.focus();
    await fireEvent.input(input, { target: { value: 'Anchor A' } });
    await fireEvent.click(
      await screen.findByRole('option', { name: /Anchor A/ }),
    );
    await waitFor(() =>
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(2),
    );
    const picker = screen.getByRole('combobox', { name: 'Colony archetype' });
    picker.focus();
    await fireEvent.change(picker, { target: { value: 'manufacturing_hub' } });
    // The type is searched near the same anchor, which supersedes the Any one.
    await waitFor(() =>
      expect(
        vi.mocked(api.searchExploreSystems).mock.lastCall?.[0],
      ).toMatchObject({ archetype: 'manufacturing_hub' }),
    );
    finishAnchor(finderPage(0));
    await waitFor(() => expect(picker).toHaveFocus());
    mockRankedPages();
    const any = screen.getByRole('combobox', { name: 'Colony archetype' });
    any.focus();
    await fireEvent.change(any, { target: { value: 'any' } });
    await waitFor(() =>
      expect(
        screen.getByRole('region', { name: 'Near Anchor A' }),
      ).toHaveAttribute('aria-busy', 'false'),
    );
    expect(any).toHaveFocus();
  });
});

describe('ExploreWorkspace My journeys (V3 journal store)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    stubRuntime();
    mockFacadeDefaults();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  async function signIn(): Promise<void> {
    vi.mocked(api.getAuthSession).mockResolvedValue({
      authenticated: true,
      user: {
        account_id: 'account-a',
        commander_name: 'CMDR Test',
        is_owner: false,
      },
    });
    await auth.bootstrap();
  }

  const journeysSwitch = () =>
    screen.getByRole('switch', { name: 'My journeys' });

  async function showTravel(): Promise<HTMLElement> {
    if (journeysSwitch().getAttribute('aria-checked') !== 'true')
      await fireEvent.click(journeysSwitch());
    return document.querySelector<HTMLElement>(
      '[data-commander-history-state]',
    )!;
  }

  // Two real systems, with visits that are this test's own: nobody's
  // journal is kept in the repository.
  const visitTo = (
    id64: string | null,
    systemName: string,
    position: [number, number, number],
    visitCount: number,
  ) => ({
    kind: 'marker' as const,
    systemId64: id64 === null ? null : parseId64(id64),
    systemName,
    x: position[0],
    y: position[1],
    z: position[2],
    visitCount,
    firstVisitedAt: '2026-01-01T00:00:00Z',
    lastVisitedAt: '2026-01-02T00:00:00Z',
    cellSizeLy: null,
  });
  const SOL_VISIT = visitTo('10477373803', 'Sol', [0, 0, 0], 3);
  const ALPHA_VISIT = visitTo(
    '1178708478315',
    'Alpha Centauri',
    [3.03125, -0.09375, 3.15625],
    1,
  );
  const travel = (
    visits: ReturnType<typeof visitTo>[],
    truncated = false,
  ): api.CommanderTravelHeatmapResponse => ({
    mode: 'markers',
    visits,
    count: visits.length,
    truncated,
  });

  it('is off until asked for, and asks for nothing while it is off', async () => {
    await signIn();
    render(ExploreWorkspaceTestHost);
    const map = await theMap();
    expect(journeysSwitch()).toHaveAttribute('aria-checked', 'false');
    expect(
      document.querySelector('[data-commander-history-state]'),
    ).toHaveTextContent('My journeys hidden');
    expect(api.getCommanderTravelHeatmap).not.toHaveBeenCalled();
    expect(map.groups.has('My journeys')).toBe(false);
  });

  it('asks a signed-out visitor to sign in and never requests travel', async () => {
    await auth.bootstrap();
    render(ExploreWorkspaceTestHost);
    await theMap();

    const status = await showTravel();

    await waitFor(() =>
      expect(status).toHaveAttribute(
        'data-commander-history-state',
        'signed-out',
      ),
    );
    expect(
      screen.getByRole('link', { name: 'Sign in and import your journals' }),
    ).toHaveAttribute('href', '/account');
    expect(api.getCommanderTravelHeatmap).not.toHaveBeenCalled();
    expect(api.getCommanderJournalSummary).not.toHaveBeenCalled();
  });

  it('offers a retry, not a sign-in prompt, when the account check fails', async () => {
    vi.mocked(api.getAuthSession).mockRejectedValueOnce(
      new api.ApiError(503, '/api/v1/auth/session', {}),
    );
    await auth.bootstrap();
    render(ExploreWorkspaceTestHost);
    await theMap();

    const status = await showTravel();

    await waitFor(() =>
      expect(status).toHaveAttribute(
        'data-commander-history-state',
        'auth-error',
      ),
    );
    expect(
      screen.queryByRole('link', { name: 'Sign in and import your journals' }),
    ).not.toBeInTheDocument();
    expect(api.getCommanderTravelHeatmap).not.toHaveBeenCalled();
    await fireEvent.click(
      Array.from(status.querySelectorAll('button')).find(
        (button) => button.textContent === 'Retry',
      )!,
    );
    await waitFor(() =>
      expect(status).toHaveAttribute(
        'data-commander-history-state',
        'signed-out',
      ),
    );
  });

  it('reads the signed-in account’s visited systems once, for the whole galaxy, and puts them on the map', async () => {
    await signIn();
    vi.mocked(api.getCommanderTravelHeatmap).mockResolvedValue(
      travel([
        SOL_VISIT,
        ALPHA_VISIT,
        // A visit the journal gave no system id for cannot be told apart
        // from other systems, so it is not drawn.
        visitTo(null, 'No id', [9, 9, 9], 1),
      ]),
    );
    render(ExploreWorkspaceTestHost);
    const map = await theMap();

    const status = await showTravel();

    await waitFor(() =>
      expect(status).toHaveAttribute('data-commander-history-state', 'ready'),
    );
    expect(status).toHaveAttribute('data-commander-history-count', '3');
    expect(status.textContent).toMatch(/3 visited\s+systems/u);
    expect(status.textContent).not.toContain('most recent');
    // One marker for each system (zoom 1), for the whole galaxy, as many as
    // the API returns at once.
    expect(api.getCommanderTravelHeatmap).toHaveBeenCalledExactlyOnceWith(
      {
        minX: -100_000,
        maxX: 100_000,
        minY: -100_000,
        maxY: 100_000,
        minZ: -100_000,
        maxZ: 100_000,
        limit: 20_000,
      },
      1,
      expect.anything(),
    );
    await waitFor(() =>
      expect(map.namesIn('My journeys')).toEqual(['Sol', 'Alpha Centauri']),
    );
    expect(map.groups.get('My journeys')).toMatchObject({
      markerStyle: 'disc',
      visible: true,
    });
    // Showing them moves nothing: the camera stays where the player has it.
    expect(map.flights).toHaveLength(0);
    expect(map.getSelection()).toBeNull();
    expect((await map.panel(SOL_VISIT.systemId64!))!.values).toEqual([
      { label: 'YOUR VISITS', value: '3' },
      { label: 'FIRST VISIT', value: '1 Jan 2026' },
      { label: 'LAST VISIT', value: '2 Jan 2026' },
      { label: 'ID', value: '10477373803', copy: '10477373803' },
    ]);

    // Switched off, they leave the map; switched on again, they come back
    // without being asked for a second time.
    await fireEvent.click(journeysSwitch());
    await waitFor(() => expect(map.groups.has('My journeys')).toBe(false));
    expect(status).toHaveTextContent('My journeys hidden');
    await fireEvent.click(journeysSwitch());
    await waitFor(() =>
      expect(map.namesIn('My journeys')).toEqual(['Sol', 'Alpha Centauri']),
    );
    expect(api.getCommanderTravelHeatmap).toHaveBeenCalledTimes(1);
  });

  it('says so when only the most recently visited systems are shown', async () => {
    await signIn();
    vi.mocked(api.getCommanderTravelHeatmap).mockResolvedValue(
      travel([SOL_VISIT], true),
    );
    render(ExploreWorkspaceTestHost);
    await theMap();
    const status = await showTravel();
    await waitFor(() =>
      expect(status).toHaveAttribute('data-commander-history-state', 'ready'),
    );
    expect(status.textContent).toMatch(
      /1 visited\s+systems · your most recent/u,
    );
  });

  it('draws a long trip as a line, and says the lines are only the trips over 1,000 ly', async () => {
    await signIn();
    // Sol and three real systems on the line towards the galaxy's centre,
    // each within a jump of the next and the last 1,346 ly out (the live
    // site, 15 Nov 2026). When each was first reached is this test's own.
    const trip = [
      SOL_VISIT,
      visitTo('83852530482', 'HIP 86404', [7.0625, 2, 461.90625], 1),
      visitTo('83852530570', 'HIP 86522', [11.90625, -1.125, 898.4375], 1),
      visitTo(
        '3686969379315',
        'Praea Euq EI-B d13-107',
        [-9, -5.0625, 1345.53125],
        1,
      ),
    ].map((visit, day) => ({
      ...visit,
      firstVisitedAt: `2026-03-0${day + 1}T12:00:00Z`,
    }));
    vi.mocked(api.getCommanderTravelHeatmap).mockResolvedValue(travel(trip));
    render(ExploreWorkspaceTestHost);
    const map = await theMap();
    const status = await showTravel();
    await waitFor(() =>
      expect(status).toHaveAttribute('data-commander-history-state', 'ready'),
    );
    expect(status.textContent).toMatch(
      /4 visited\s+systems · lines are trips over 1,000 ly/u,
    );
    await waitFor(() => expect(map.lines).toHaveLength(1));
    expect(map.lines[0]).toMatchObject({
      name: 'Your route from Sol to Praea Euq EI-B d13-107',
      points: [
        [0, 0, 0],
        [-9, -5.0625, 1345.53125],
      ],
      categories: ['My trips over 1,000 ly'],
    });
    // From far out a route is its line: the markers are drawn near where
    // the map looks.
    expect(map.groups.get('My journeys')).toMatchObject({
      maxDrawRange: 1_000,
    });
  });

  it('says nothing of lines where no trip is long enough to have one', async () => {
    await signIn();
    vi.mocked(api.getCommanderTravelHeatmap).mockResolvedValue(
      travel([SOL_VISIT, ALPHA_VISIT]),
    );
    render(ExploreWorkspaceTestHost);
    const map = await theMap();
    const status = await showTravel();
    await waitFor(() =>
      expect(status).toHaveAttribute('data-commander-history-state', 'ready'),
    );
    await waitFor(() => expect(map.namesIn('My journeys')).toHaveLength(2));
    expect(status.textContent).toMatch(/2 visited\s+systems/u);
    expect(status.textContent).not.toContain('lines');
    expect(map.lines).toEqual([]);
  });

  it('distinguishes an account without imported journals from one with no travel', async () => {
    await signIn();
    vi.mocked(api.getCommanderJournalSummary).mockResolvedValue({
      imported_files: 0,
      events_stored: 0,
      unique_bodies: 0,
      unique_bio_observations: 0,
      systems_observed: 0,
      last_imported_at: null,
      event_counts: {},
    });
    render(ExploreWorkspaceTestHost);
    await theMap();
    const status = await showTravel();

    await waitFor(() =>
      expect(status).toHaveTextContent('No imported journals yet'),
    );
    expect(status).toHaveAttribute(
      'data-commander-history-state',
      'no-journals',
    );
    expect(api.getCommanderJournalSummary).toHaveBeenCalled();
  });

  it('retries unavailable journal import metadata independently from the travel request', async () => {
    await signIn();
    vi.mocked(api.getCommanderJournalSummary).mockRejectedValue(
      new api.ApiError(503, '/api/v1/journal/summary', {}),
    );
    render(ExploreWorkspaceTestHost);
    await theMap();
    const status = await showTravel();
    await waitFor(
      () =>
        expect(status).toHaveAttribute(
          'data-commander-history-state',
          'summary-error',
        ),
      { timeout: 3_000 },
    );
    expect(status).toHaveTextContent('Journal import status unavailable');
    expect(status).not.toHaveTextContent('No imported journals yet');
    const summaryCalls = vi.mocked(api.getCommanderJournalSummary).mock.calls
      .length;
    const travelCalls = vi.mocked(api.getCommanderTravelHeatmap).mock.calls
      .length;
    vi.mocked(api.getCommanderJournalSummary).mockResolvedValue({
      imported_files: 0,
      events_stored: 0,
      unique_bodies: 0,
      unique_bio_observations: 0,
      systems_observed: 0,
      last_imported_at: null,
      event_counts: {},
    });

    await fireEvent.click(
      within(status).getByRole('button', { name: 'Retry' }),
    );

    await waitFor(() =>
      expect(status).toHaveTextContent('No imported journals yet'),
    );
    expect(api.getCommanderJournalSummary).toHaveBeenCalledTimes(
      summaryCalls + 1,
    );
    expect(api.getCommanderTravelHeatmap).toHaveBeenCalledTimes(travelCalls);
  });

  it.each([false, true])(
    'takes cached private visits off the map when the journal summary reports an expired session (F4=%s)',
    async (enabled) => {
      featureGate.enabled = enabled;
      await signIn();
      vi.mocked(api.getCommanderTravelHeatmap).mockResolvedValue(
        travel([SOL_VISIT]),
      );
      render(ExploreWorkspaceTestHost);
      const map = await theMap();
      const status = await showTravel();
      await waitFor(() => expect(map.namesIn('My journeys')).toEqual(['Sol']));
      vi.mocked(api.getCommanderJournalSummary).mockRejectedValue(
        new api.ApiError(401, '/api/v1/journal/summary', {}),
      );

      await queryClient.invalidateQueries({
        queryKey: [...queryKeys.auth(), 'account-a', 'journal-summary'],
      });

      await waitFor(() =>
        expect(status).toHaveAttribute(
          'data-commander-history-state',
          'signed-out',
        ),
      );
      expect(status).toHaveAttribute('data-commander-history-count', '0');
      await waitFor(() => expect(map.systemCount()).toBe(0));
      expect(map.groups.has('My journeys')).toBe(false);
    },
  );

  it('shows an honest empty state, and a sign-in prompt on a 401', async () => {
    await signIn();
    render(ExploreWorkspaceTestHost);
    await theMap();
    const status = await showTravel();
    await waitFor(() =>
      expect(status).toHaveAttribute('data-commander-history-state', 'empty'),
    );
    expect(status.textContent).toContain(
      'No travel in your imported journals yet',
    );
    cleanup();
    queryClient.clear();

    vi.mocked(api.getCommanderTravelHeatmap).mockRejectedValue(
      new api.ApiError(401, '/api/v1/journal/travel-heatmap', {}),
    );
    render(ExploreWorkspaceTestHost);
    await theMap();
    const expired = await showTravel();
    await waitFor(() =>
      expect(expired).toHaveAttribute(
        'data-commander-history-state',
        'signed-out',
      ),
    );
    expect(api.getCommanderTravelHeatmap).toHaveBeenCalledTimes(2);
  });
});

describe.each([false, true])(
  'Explore shareable body search (F4=%s)',
  (enabled) => {
    beforeEach(() => {
      vi.clearAllMocks();
      queryClient.clear();
      localStorage.clear();
      stubRuntime();
      mockFacadeDefaults();
      featureGate.enabled = enabled;
      mockRankedPages();
      vi.mocked(api.getSystem).mockImplementation(async (id64) =>
        anchorDetail(id64 === anchorB.id64 ? anchorB : anchorA),
      );
    });

    afterEach(() => {
      cleanup();
      featureGate.enabled = false;
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
    });

    it.each([
      ['G', 'G star'],
      ['G (White-Yellow) Star', 'G (White-Yellow) Star'],
    ])(
      'renders catalogue star class %s once in the result card',
      async (value, label) => {
        vi.mocked(api.searchExploreSystems).mockResolvedValue({
          ...finderPage(0, 1),
          results: [{ ...finderPage(0, 1).results[0], main_star_type: value }],
        });
        renderEnabled(`near=${anchorA.id64}`, { anchor: anchorA });
        await screen.findByText('Ranked 1');
        const row = within(
          screen.getByRole('list', { name: 'System results' }),
        ).getByRole('listitem');
        expect(
          within(row).getByText(label, { exact: true }),
        ).toBeInTheDocument();
        expect(row).not.toHaveTextContent(/Star star/i);
        expect(
          within(row).getByRole('link', { name: 'Inspect Ranked 1' }),
        ).toBeInTheDocument();
      },
    );

    it('describes the anchored search scope, bounds the radius choices and includes the radius with card distances', async () => {
      const anchor = { ...anchorA, name: 'Achenar' };
      vi.mocked(api.searchExploreSystems).mockImplementation(
        async (request, _signal, _anchor, pageSize) => {
          const response = finderPage(
            request.from ?? 0,
            pageSize ?? request.size ?? 50,
          );
          return {
            ...response,
            results: response.results.map((system) => ({
              ...system,
              distance: 12.5,
            })),
          };
        },
      );
      renderEnabled(`near=${anchor.id64}`, { anchor });
      await screen.findByText('Ranked 1');

      // The Finder shows tier C or better by default near a system.
      const scope = enabled
        ? 'Within 500 LY of Achenar · tier C or better · sorted by distance'
        : 'Within 500 LY of Achenar · sorted by distance';
      expect(document.getElementById('results-scope')).toHaveTextContent(scope);
      const list = screen.getByRole('list', { name: 'System results' });
      expect(list).toHaveAttribute('aria-describedby', 'results-scope');
      expect(list).toHaveAccessibleDescription(scope);
      // Each line gives its distance; the scope above says how far was searched.
      expect(within(list).getAllByRole('listitem')[0]).toHaveTextContent(
        enabled ? '12.50 LY' : '12.5 ly',
      );
      expect(list).not.toHaveTextContent('within');
      const radius = screen.getByRole('combobox', { name: 'Within' });
      expect(radius).toBeEnabled();
      expect(radius).toHaveValue('500');
      expect(
        within(radius)
          .getAllByRole('option')
          .map((option) => ({
            value: (option as HTMLOptionElement).value,
            label: option.textContent,
          })),
      ).toEqual([
        { value: '100', label: '100 LY' },
        { value: '250', label: '250 LY' },
        { value: '500', label: '500 LY' },
        { value: '1000', label: '1,000 LY' },
        { value: '2000', label: '2,000 LY' },
      ]);
      // A "Show only" tier is a filter, so it takes the filtered cap too.
      expect(
        within(radius)
          .getAllByRole('option')
          .map((option) => (option as HTMLOptionElement).disabled),
      ).toEqual(
        enabled
          ? [false, false, false, true, true]
          : [false, false, false, false, false],
      );
      if (enabled)
        expect(radius).toHaveAccessibleDescription(
          '"Show only" limits the search to 500 LY.',
        );
    });

    it('describes galaxy-wide Any scope and disables radius without an anchor', async () => {
      renderEnabled(enabled ? 'page_size=1' : '');
      if (enabled) await screen.findByText('Ranked 1');
      const scope = 'Galaxy-wide · best colony potential';
      expect(document.getElementById('results-scope')).toHaveTextContent(scope);
      if (enabled) {
        expect(
          screen.getByRole('list', { name: 'System results' }),
        ).toHaveAccessibleDescription(scope);
      }
      const radius = screen.getByRole('combobox', { name: 'Within' });
      expect(radius).toBeDisabled();
      expect(radius).toHaveAccessibleDescription(
        'Search radius works when you search near a system.',
      );
    });

    it.runIf(enabled)(
      'offers "Show only" near a system from tier C, and every system on request',
      async () => {
        renderEnabled(`near=${anchorA.id64}&radius=1000`, { anchor: anchorA });
        await screen.findByText('Ranked 1');
        const showOnly = screen.getByRole('combobox', { name: 'Show only' });
        expect(showOnly).toHaveValue('C');
        expect(
          vi.mocked(api.searchExploreSystems).mock.lastCall?.[0],
        ).toMatchObject({
          filters: { distance: { min: 0, max: 500 } },
          min_development_score: 45,
        });
        expect(screen.getByRole('combobox', { name: 'Within' })).toHaveValue(
          '500',
        );

        await fireEvent.change(showOnly, { target: { value: 'all' } });
        await waitFor(() =>
          expect(api.searchExploreSystems).toHaveBeenCalledTimes(2),
        );
        const request = vi.mocked(api.searchExploreSystems).mock.lastCall?.[0];
        expect(request).not.toHaveProperty('min_development_score');
        // The chosen 1,000 LY resumes once nothing is filtered.
        expect(request?.filters?.distance).toEqual({ min: 0, max: 1000 });
        expect(mockedPage.url.searchParams.get('min-tier')).toBe('all');
        expect(document.getElementById('results-scope')).toHaveTextContent(
          'Within 1,000 LY of Anchor A · sorted by distance',
        );
      },
    );

    it.runIf(enabled)(
      'has no "Show only" for Any galaxy-wide, which is best first already',
      async () => {
        renderEnabled('page_size=1');
        await screen.findByText('Ranked 1');
        expect(
          screen.queryByRole('combobox', { name: 'Show only' }),
        ).not.toBeInTheDocument();
        expect(
          vi.mocked(api.searchExploreSystems).mock.lastCall?.[0],
        ).not.toHaveProperty('min_development_score');
      },
    );

    it.runIf(enabled)(
      'offers every system when no system of the tier is that close',
      async () => {
        mockRankedPages(0);
        renderEnabled(`min-tier=A&near=${anchorA.id64}`, { anchor: anchorA });
        await screen.findByText(
          'No systems of tier A or better are this close.',
        );
        await fireEvent.click(
          screen.getByRole('button', { name: 'Show all systems' }),
        );
        await waitFor(() =>
          expect(mockedPage.url.searchParams.get('min-tier')).toBe('all'),
        );
      },
    );

    it('commits one radius search, resets pagination and restores its URL on reload', async () => {
      renderEnabled(
        `${enabled ? 'min-tier=all&page_size=1&offset=1&' : ''}selected=${anchorA.id64}&view=cards&near=${anchorA.id64}`,
        { anchor: anchorA },
      );
      await screen.findByText(enabled ? 'Ranked 2' : 'Ranked 1');
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      expect(mockedGoto).not.toHaveBeenCalled();

      await fireEvent.change(screen.getByRole('combobox', { name: 'Within' }), {
        target: { value: '1000' },
      });
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(2),
      );
      await tick();
      expect(mockedGoto).toHaveBeenCalledTimes(1);
      expect(mockedGoto.mock.calls[0][1].replaceState).toBe(false);
      expect(mockedPage.url.searchParams.get('radius')).toBe('1000');
      expect(mockedPage.url.searchParams.has('offset')).toBe(false);
      expect(mockedPage.url.searchParams.get('near')).toBe(anchorA.id64);
      expect(mockedPage.url.searchParams.get('selected')).toBe(anchorA.id64);
      expect(mockedPage.url.searchParams.get('view')).toBe('cards');
      expect(vi.mocked(api.searchExploreSystems).mock.lastCall?.[0]).toEqual({
        reference_coords: { x: 10, y: 20, z: 30 },
        filters: { distance: { min: 0, max: 1000 }, economy: 'any' },
        sort_by: 'distance',
        size: enabled ? 1 : 24,
        from: 0,
        reach_ly: 250,
      });
      expect(document.getElementById('results-scope')).toHaveTextContent(
        'Within 1,000 LY of Anchor A · sorted by distance',
      );

      const reloadQuery = mockedPage.url.search.slice(1);
      cleanup();
      queryClient.clear();
      vi.mocked(api.searchExploreSystems).mockClear();
      mockedGoto.mockClear();
      renderEnabled(reloadQuery);
      await screen.findByText('Ranked 1');
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      expect(mockedGoto).not.toHaveBeenCalled();
      expect(screen.getByRole('combobox', { name: 'Within' })).toHaveValue(
        '1000',
      );
      expect(document.getElementById('results-scope')).toHaveTextContent(
        'Within 1,000 LY of Anchor A · sorted by distance',
      );
      expect(
        vi.mocked(api.searchExploreSystems).mock.lastCall?.[0].filters
          ?.distance,
      ).toEqual({ min: 0, max: 1000 });
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    });

    it('commits one reach search, resets pagination and restores its URL on reload', async () => {
      renderEnabled(
        `${enabled ? 'page_size=1&offset=1&' : ''}view=cards&near=${anchorA.id64}`,
        { anchor: anchorA },
      );
      await screen.findByText(enabled ? 'Ranked 2' : 'Ranked 1');
      const reach = screen.getByRole('combobox', {
        name: 'Distance from inhabited space',
      });
      expect(reach).toBeEnabled();
      expect(reach).toHaveValue('250');
      expect(reach).toHaveAccessibleDescription(
        'Every search keeps to systems this close to an inhabited system.',
      );
      expect(
        vi.mocked(api.searchExploreSystems).mock.lastCall?.[0].reach_ly,
      ).toBe(250);

      await fireEvent.change(reach, { target: { value: '500' } });
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(2),
      );
      await tick();
      expect(mockedGoto).toHaveBeenCalledTimes(1);
      expect(mockedPage.url.searchParams.get('reach')).toBe('500');
      expect(mockedPage.url.searchParams.has('offset')).toBe(false);
      expect(mockedPage.url.searchParams.get('view')).toBe('cards');
      expect(vi.mocked(api.searchExploreSystems).mock.lastCall?.[0]).toEqual({
        reference_coords: { x: 10, y: 20, z: 30 },
        filters: { distance: { min: 0, max: 500 }, economy: 'any' },
        sort_by: 'distance',
        size: enabled ? 1 : 24,
        from: 0,
        ...floorC(enabled),
        reach_ly: 500,
      });

      const reloadQuery = mockedPage.url.search.slice(1);
      cleanup();
      queryClient.clear();
      vi.mocked(api.searchExploreSystems).mockClear();
      mockedGoto.mockClear();
      renderEnabled(reloadQuery);
      await screen.findByText('Ranked 1');
      expect(mockedGoto).not.toHaveBeenCalled();
      expect(
        screen.getByRole('combobox', { name: 'Distance from inhabited space' }),
      ).toHaveValue('500');
      expect(
        vi.mocked(api.searchExploreSystems).mock.lastCall?.[0].reach_ly,
      ).toBe(500);
    });

    it('caps a filtered search at the API radius and resumes the chosen radius once filters clear', async () => {
      // The API rejects anchored searches with body filters beyond 500 LY, so
      // the wider options are disabled and a wider URL radius is applied as 500.
      renderEnabled(
        `${enabled ? 'min-tier=all&page_size=1&' : ''}near=${anchorA.id64}&bodies=elw_count%3A1-&radius=1000`,
        { anchor: anchorA },
      );
      await screen.findByText('Ranked 1');
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      expect(vi.mocked(api.searchExploreSystems).mock.lastCall?.[0]).toEqual({
        reference_coords: { x: 10, y: 20, z: 30 },
        filters: { distance: { min: 0, max: 500 }, economy: 'any' },
        body_filters: { elw_count: { min: 1 } },
        sort_by: 'distance',
        size: enabled ? 1 : 24,
        from: 0,
        reach_ly: 250,
      });
      const radius = screen.getByRole('combobox', { name: 'Within' });
      expect(radius).toHaveValue('500');
      expect(radius).toHaveAccessibleDescription(
        'Body filters limit the search to 500 LY; your 1,000 LY radius resumes when the filters are cleared.',
      );
      expect(
        within(radius)
          .getAllByRole('option')
          .map((option) => (option as HTMLOptionElement).disabled),
      ).toEqual([false, false, false, true, true]);
      expect(document.getElementById('results-scope')).toHaveTextContent(
        'Within 500 LY of Anchor A · sorted by distance',
      );
      // The URL still records the commander's choice.
      expect(mockedPage.url.searchParams.get('radius')).toBe('1000');

      await fireEvent.change(radius, { target: { value: '250' } });
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(2),
      );
      expect(
        vi.mocked(api.searchExploreSystems).mock.lastCall?.[0].filters
          ?.distance,
      ).toEqual({ min: 0, max: 250 });
      expect(mockedPage.url.searchParams.get('radius')).toBe('250');
      expect(radius).toHaveAccessibleDescription(
        'Body filters limit the search to 500 LY.',
      );
    });

    it('shows the column headings only once there are results to head', async () => {
      let finish!: (answer: ReturnType<typeof finderPage>) => void;
      vi.mocked(api.searchExploreSystems).mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
      renderEnabled(`near=${anchorA.id64}`, { anchor: anchorA });
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(1),
      );
      // While the first page loads: no headings (they used to read
      // "Economy and population") and no "0 shown".
      expect(document.querySelector('.result-columns')).toBeNull();
      if (enabled) expect(screen.queryByText('0 shown')).toBeNull();

      finish(finderPage(0, enabled ? 50 : 24));
      await screen.findByText('Ranked 1');
      expect(document.querySelector('.result-columns')).not.toBeNull();
    });

    it('keeps the lines of the shown answer while the next radius is loading', async () => {
      const response = {
        ...finderPage(0, enabled ? 50 : 24),
        results: rankedRows.map((system) => ({ ...system, distance: 12.5 })),
      };
      let finish!: (page: typeof response) => void;
      vi.mocked(api.searchExploreSystems)
        .mockResolvedValueOnce(response)
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              finish = resolve;
            }),
        );
      renderEnabled(`${enabled ? 'min-tier=all&' : ''}near=${anchorA.id64}`, {
        anchor: anchorA,
      });
      await screen.findByText('Ranked 1');
      const list = screen.getByRole('list', { name: 'System results' });
      // A line gives the system's distance only: no line claims a radius,
      // so none can claim the next one before its answer arrives.
      expect(within(list).getAllByRole('listitem')[0]).toHaveTextContent(
        enabled ? '12.50 LY' : '12.5 ly',
      );
      expect(list).not.toHaveTextContent('within');

      await fireEvent.change(screen.getByRole('combobox', { name: 'Within' }), {
        target: { value: '1000' },
      });
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(2),
      );
      expect(document.getElementById('results-scope')).toHaveTextContent(
        'Within 1,000 LY of Anchor A · sorted by distance',
      );
      if (enabled) {
        const retained = screen.getByRole('list', { name: 'System results' });
        expect(within(retained).getAllByRole('listitem')[0]).toHaveTextContent(
          '12.50 LY',
        );
        expect(retained).not.toHaveTextContent('within');
      }

      finish(response);
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(2),
      );
      expect(
        within(
          await screen.findByRole('list', { name: 'System results' }),
        ).getAllByRole('listitem')[0],
      ).toHaveTextContent(enabled ? '12.50 LY' : '12.5 ly');
    });

    it('clears an invalid radius with the existing notice and searches at the default radius once', async () => {
      renderEnabled(`near=${anchorA.id64}&radius=3000&bodies=elw_count:1-`, {
        anchor: anchorA,
      });
      await screen.findByText('Invalid search link option removed.');
      await screen.findByText('Ranked 1');
      expect(mockedPage.url.searchParams.has('radius')).toBe(false);
      expect(mockedPage.url.searchParams.get('near')).toBe(anchorA.id64);
      expect(mockedPage.url.searchParams.get('bodies')).toBe('elw_count:1-');
      expect(screen.getByRole('combobox', { name: 'Within' })).toHaveValue(
        '500',
      );
      expect(document.getElementById('results-scope')).toHaveTextContent(
        enabled
          ? 'Within 500 LY of Anchor A · tier C or better · sorted by distance'
          : 'Within 500 LY of Anchor A · sorted by distance',
      );
      expect(mockedGoto).toHaveBeenCalledTimes(1);
      expect(mockedGoto.mock.calls[0][1].replaceState).toBe(true);
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      expect(
        vi.mocked(api.searchExploreSystems).mock.calls[0][0].filters?.distance,
      ).toEqual({ min: 0, max: 500 });
    });

    it('restores the anchor and filters from a reload with no history payload', async () => {
      renderEnabled(
        `near=${anchorA.id64}&bodies=elw_count:1-,landable_count:2-5`,
      );
      await screen.findByText('Ranked 1');
      expect(screen.getByRole('combobox', { name: 'System name' })).toHaveValue(
        'Anchor A',
      );
      expect(
        screen.getByRole('heading', { name: 'Near Anchor A' }),
      ).toBeInTheDocument();
      expect(screen.getByText('Earth-like ≥ 1')).toBeInTheDocument();
      expect(screen.getByText('Landable 2–5')).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: 'Body filters' }),
      ).toBeEnabled();
      expect(api.getSystem).toHaveBeenCalledWith(
        anchorA.id64,
        expect.anything(),
      );
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      expect(vi.mocked(api.searchExploreSystems).mock.calls[0][0]).toEqual({
        reference_coords: { x: 10, y: 20, z: 30 },
        filters: { distance: { min: 0, max: 500 }, economy: 'any' },
        body_filters: {
          elw_count: { min: 1 },
          landable_count: { min: 2, max: 5 },
        },
        sort_by: 'distance',
        size: enabled ? 50 : 24,
        from: 0,
        reach_ly: 250,
        ...floorC(enabled),
      });
      expect(mockedPage.url.searchParams.get('near')).toBe(anchorA.id64);
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    });

    it('waits for URL resolution and ignores a late response for an earlier anchor', async () => {
      let finish!: (detail: api.SystemDetail) => void;
      vi.mocked(api.getSystem).mockImplementation((id64) =>
        id64 === anchorA.id64
          ? new Promise((resolve) => {
              finish = resolve;
            })
          : Promise.resolve(anchorDetail(anchorB)),
      );
      const history = renderEnabled(`near=${anchorA.id64}&bodies=elw_count:1-`);
      await waitFor(() => expect(api.getSystem).toHaveBeenCalledTimes(1));
      expect(api.searchExploreSystems).not.toHaveBeenCalled();
      expect(document.querySelectorAll('[data-system-result]')).toHaveLength(0);
      history.restore(`near=${anchorB.id64}&bodies=elw_count:1-`, {
        anchor: anchorA,
      });
      await screen.findByText('Ranked 1');
      finish(anchorDetail(anchorA));
      await tick();
      expect(
        screen.getByRole('heading', { name: 'Near Anchor B' }),
      ).toBeInTheDocument();
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      expect(
        vi.mocked(api.searchExploreSystems).mock.calls[0][0].reference_coords,
      ).toEqual({ x: -10, y: -20, z: -30 });
    });

    it.each(['near=bad', 'near=9007199254740993&near=9007199254740994'])(
      'removes invalid near without reviving stale history: %s',
      async (near) => {
        renderEnabled(`${near}&bodies=elw_count:1-`, { anchor: anchorA });
        await screen.findByText('Invalid search link option removed.');
        await waitFor(() =>
          expect(mockedPage.url.searchParams.has('near')).toBe(false),
        );
        expect(mockedPage.state.anchor).toBeNull();
        expect(api.getSystem).not.toHaveBeenCalled();
        expect(api.searchExploreSystems).not.toHaveBeenCalled();
        expect(screen.getByText('Earth-like ≥ 1')).toBeInTheDocument();
        // Filters are never paused now; with no anchor and no ranking asked
        // for, there is simply no search to apply them to yet.
        expect(screen.queryByText('Paused')).not.toBeInTheDocument();
        expect(
          screen.getByRole('button', { name: 'Body filters' }),
        ).toBeEnabled();
      },
    );

    it.each(['error', 'different-id', 'origin', 'coordinates', 'name'])(
      'clears an unresolvable near safely: %s',
      async (failure) => {
        if (failure === 'error')
          vi.mocked(api.getSystem).mockRejectedValue(
            new Error('Missing system'),
          );
        else
          vi.mocked(api.getSystem).mockResolvedValue({
            ...anchorDetail(anchorA),
            ...(failure === 'different-id' ? { id64: anchorB.id64 } : {}),
            ...(failure === 'origin' ? { x: 0, y: 0, z: 0 } : {}),
            ...(failure === 'coordinates' ? { x: undefined } : {}),
            ...(failure === 'name' ? { name: '' } : {}),
          });
        renderEnabled(`near=${anchorA.id64}&bodies=elw_count:1-`);
        await screen.findByText(
          'Search anchor could not be restored; choose a system again.',
        );
        expect(mockedPage.url.searchParams.has('near')).toBe(false);
        expect(mockedPage.url.searchParams.get('bodies')).toBe('elw_count:1-');
        expect(api.searchExploreSystems).not.toHaveBeenCalled();
        expect(screen.queryByText('Paused')).not.toBeInTheDocument();
      },
    );

    it('drops Any-mode pagination and intent with a malformed near so no galaxy-wide search runs', async () => {
      renderEnabled(
        'near=bad&page_size=25&offset=25&bodies=elw_count:1-&selected=9007199254740993',
      );
      await screen.findByText('Invalid search link option removed.');
      await waitFor(() =>
        expect(mockedPage.url.searchParams.has('page_size')).toBe(false),
      );
      expect(mockedPage.url.searchParams.has('offset')).toBe(false);
      expect(mockedPage.url.searchParams.has('near')).toBe(false);
      expect(mockedPage.url.searchParams.get('bodies')).toBe('elw_count:1-');
      expect(mockedPage.url.searchParams.get('selected')).toBe(
        '9007199254740993',
      );
      expect(mockedPage.state.finderRequested).not.toBe(true);
      expect(api.searchExploreSystems).not.toHaveBeenCalled();
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    });

    it('drops Any-mode pagination with an unresolvable near so no galaxy-wide search runs', async () => {
      vi.mocked(api.getSystem).mockRejectedValue(new Error('Missing system'));
      renderEnabled(
        `near=${anchorA.id64}&page_size=25&offset=25&bodies=elw_count:1-`,
      );
      await screen.findByText(
        'Search anchor could not be restored; choose a system again.',
      );
      await waitFor(() =>
        expect(mockedPage.url.searchParams.has('page_size')).toBe(false),
      );
      expect(mockedPage.url.searchParams.has('offset')).toBe(false);
      expect(mockedPage.url.searchParams.has('near')).toBe(false);
      expect(mockedPage.url.searchParams.get('bodies')).toBe('elw_count:1-');
      expect(mockedPage.state.finderRequested).not.toBe(true);
      expect(api.searchExploreSystems).not.toHaveBeenCalled();
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
      expect(screen.queryByText('Paused')).not.toBeInTheDocument();
    });

    it('accepts Sol as a real origin anchor', async () => {
      vi.mocked(api.getSystem).mockResolvedValue(
        anchorDetail({
          id64: api.SOL_ID64,
          name: 'Sol',
          x: 0,
          y: 0,
          z: 0,
        }),
      );
      renderEnabled(`near=${api.SOL_ID64}`);
      await screen.findByText('Ranked 1');
      expect(
        vi.mocked(api.searchExploreSystems).mock.calls[0][0].reference_coords,
      ).toEqual({ x: 0, y: 0, z: 0 });
    });

    it('does not search while drafting; Apply, removal and Clear all each search once even with cached results', async () => {
      renderEnabled(`near=${anchorA.id64}`, { anchor: anchorA });
      await screen.findByText('Ranked 1');
      const trigger = screen.getByRole('button', { name: 'Body filters' });
      await fireEvent.click(trigger);
      const dialog = within(
        screen.getByRole('dialog', { name: 'Body filters' }),
      );
      await fireEvent.input(
        dialog.getByRole('slider', { name: 'Earth-like minimum slider' }),
        { target: { value: '1' } },
      );
      await fireEvent.input(
        dialog.getByRole('spinbutton', { name: 'Water worlds maximum' }),
        { target: { value: '3' } },
      );
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      await fireEvent.click(dialog.getByRole('button', { name: 'Apply' }));
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(2),
      );
      expect(mockedPage.url.searchParams.get('bodies')).toBe(
        'elw_count:1-,ww_count:-3',
      );
      expect(trigger).toHaveFocus();
      await fireEvent.click(
        screen.getByRole('button', { name: 'Remove Earth-like filter' }),
      );
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(3),
      );
      expect(
        vi.mocked(api.searchExploreSystems).mock.lastCall?.[0].body_filters,
      ).toEqual({ ww_count: { max: 3 } });
      await fireEvent.click(screen.getByRole('button', { name: 'Clear all' }));
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(4),
      );
      expect(mockedPage.url.searchParams.has('bodies')).toBe(false);
      expect(
        vi.mocked(api.searchExploreSystems).mock.lastCall?.[0],
      ).not.toHaveProperty('body_filters');
      await fireEvent.click(trigger);
      await fireEvent.click(
        within(screen.getByRole('dialog', { name: 'Body filters' })).getByRole(
          'button',
          {
            name: 'Apply',
          },
        ),
      );
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(5),
      );
      expect(trigger).toHaveFocus();
    });

    it('keeps tab keys, draft edits, Reset and Cancel local until Apply', async () => {
      renderEnabled(`near=${anchorA.id64}&bodies=elw_count:1-`, {
        anchor: anchorA,
      });
      await screen.findByText('Ranked 1');
      const trigger = screen.getByRole('button', { name: 'Body filters' });
      const applied = screen.getByRole('group', {
        name: 'Applied body filters',
      });
      expect(applied.querySelector('.kit-chip')).toHaveTextContent('Applied');
      expect(screen.queryByRole('slider')).not.toBeInTheDocument();
      await fireEvent.click(trigger);
      let dialog = screen.getByRole('dialog', { name: 'Body filters' });
      const planets = within(dialog).getByRole('tab', { name: /^Planets\b/ });
      expect(planets).toHaveFocus();
      await fireEvent.keyDown(planets, { key: 'ArrowRight' });
      expect(
        within(dialog).getByRole('tab', { name: /^Stars\b/ }),
      ).toHaveFocus();
      await fireEvent.input(
        within(dialog).getByRole('spinbutton', {
          name: 'Neutron stars minimum',
        }),
        { target: { value: '2' } },
      );
      await fireEvent.keyDown(
        within(dialog).getByRole('tab', { name: /^Stars\b/ }),
        { key: 'Home' },
      );
      await fireEvent.click(
        within(dialog).getByRole('button', { name: 'Reset' }),
      );
      await fireEvent.click(
        within(dialog).getByRole('button', { name: 'Cancel' }),
      );
      expect(trigger).toHaveFocus();
      expect(screen.getByText('Earth-like ≥ 1')).toBeInTheDocument();
      expect(mockedPage.url.searchParams.get('bodies')).toBe('elw_count:1-');
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      await fireEvent.click(trigger);
      dialog = screen.getByRole('dialog', { name: 'Body filters' });
      expect(
        within(dialog).getByRole('spinbutton', { name: 'Earth-like minimum' }),
      ).toHaveValue(1);
      await fireEvent.input(
        within(dialog).getByRole('spinbutton', { name: 'Earth-like minimum' }),
        { target: { value: '2' } },
      );
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      await fireEvent.click(
        within(dialog).getByRole('button', { name: 'Apply' }),
      );
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(2),
      );
      expect(trigger).toHaveFocus();
      expect(
        screen.queryByRole('dialog', { name: 'Body filters' }),
      ).not.toBeInTheDocument();
      expect(screen.queryByRole('slider')).not.toBeInTheDocument();
      expect(
        screen
          .getByRole('group', { name: 'Applied body filters' })
          .querySelector('.kit-chip'),
      ).toHaveTextContent('Earth-like ≥ 2');
    });

    it('runs no search from a body-only link, and keeps the filters when clearing the anchor', async () => {
      const history = renderEnabled('bodies=elw_count:1-');
      const trigger = screen.getByRole('button', { name: 'Body filters' });
      expect(trigger).toBeEnabled();
      expect(api.searchExploreSystems).not.toHaveBeenCalled();
      history.restore(`near=${anchorA.id64}&bodies=elw_count:1-`, {
        anchor: anchorA,
      });
      await screen.findByText('Ranked 1');
      await fireEvent.click(
        screen.getByRole('button', { name: 'Stop searching near Anchor A' }),
      );
      await waitFor(() =>
        expect(mockedPage.url.searchParams.has('near')).toBe(false),
      );
      expect(mockedPage.url.searchParams.get('bodies')).toBe('elw_count:1-');
      expect(trigger).toBeEnabled();
      expect(screen.queryByText('Paused')).not.toBeInTheDocument();
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
    });
  },
);

describe('Explore body filters in archetype mode', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    localStorage.clear();
    stubRuntime();
    mockFacadeDefaults();
    featureGate.enabled = true;
    mockRankedPages();
  });
  afterEach(() => {
    cleanup();
    featureGate.enabled = false;
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it.each([
    { tier: 'B', query: '' },
    { tier: 'A', query: '&min-tier=A' },
  ])(
    'describes galaxy-wide archetype scope at tier $tier and disables the retained radius',
    async ({ tier, query }) => {
      renderEnabled(`archetype=manufacturing_hub&radius=1000${query}`, {
        anchor: null,
      });
      await screen.findByText('Ranked 1');
      const scope = `Galaxy-wide · ranked by Manufacturing Hub fit · minimum tier ${tier}`;
      expect(document.getElementById('results-scope')).toHaveTextContent(scope);
      const list = screen.getByRole('list', { name: 'System results' });
      expect(list).toHaveAttribute('aria-describedby', 'results-scope');
      expect(list).toHaveAccessibleDescription(scope);
      const radius = screen.getByRole('combobox', { name: 'Within' });
      expect(radius).toBeDisabled();
      expect(radius).toHaveValue('1000');
      expect(radius).toHaveAccessibleDescription(
        'Search radius works when you search near a system.',
      );
      expect(vi.mocked(api.getArchetypeRankings).mock.calls[0][0]).toEqual({
        archetype: 'manufacturing_hub',
        min_score: tier === 'B' ? 60 : 76,
        page_size: 50,
        offset: 0,
        reach_ly: 250,
      });
      expect(api.searchExploreSystems).not.toHaveBeenCalled();
    },
  );

  it('sends the reach with a type ranking and starts it again from the first page', async () => {
    renderEnabled('archetype=manufacturing_hub&page_size=1&offset=1');
    await screen.findByText('Ranked 2');
    expect(
      vi.mocked(api.getArchetypeRankings).mock.lastCall?.[0].reach_ly,
    ).toBe(250);
    await fireEvent.change(
      screen.getByRole('combobox', { name: 'Distance from inhabited space' }),
      { target: { value: '500' } },
    );
    await waitFor(() =>
      expect(api.getArchetypeRankings).toHaveBeenLastCalledWith(
        {
          archetype: 'manufacturing_hub',
          min_score: 60,
          page_size: 1,
          offset: 0,
          reach_ly: 500,
        },
        expect.anything(),
      ),
    );
    expect(mockedPage.url.searchParams.get('reach')).toBe('500');
    expect(mockedPage.url.searchParams.has('offset')).toBe(false);
    expect(api.searchExploreSystems).not.toHaveBeenCalled();
  });

  it('searches a type near a system nearest first, within the capped radius', async () => {
    renderEnabled(
      `archetype=manufacturing_hub&min-tier=A&radius=1000&near=${anchorA.id64}`,
      { anchor: anchorA },
    );
    await screen.findByText('Ranked 1');
    const scope =
      'Within 500 LY of Anchor A · Manufacturing Hub fit, minimum tier A · sorted by distance';
    expect(document.getElementById('results-scope')).toHaveTextContent(scope);
    expect(
      screen.getByRole('heading', { name: 'Manufacturing Hub near Anchor A' }),
    ).toBeInTheDocument();
    const radius = screen.getByRole('combobox', { name: 'Within' });
    expect(radius).toBeEnabled();
    expect(radius).toHaveValue('500');
    expect(radius).toHaveAccessibleDescription(
      'A colony type limits the search to 500 LY; your 1,000 LY radius resumes when Any is chosen again.',
    );
    expect(vi.mocked(api.searchExploreSystems).mock.calls[0][0]).toEqual({
      reference_coords: { x: 10, y: 20, z: 30 },
      filters: { distance: { min: 0, max: 500 }, economy: 'any' },
      sort_by: 'distance',
      size: 50,
      from: 0,
      reach_ly: 250,
      archetype: 'manufacturing_hub',
      min_score: 76,
    });
    expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    expect(mockedPage.url.searchParams.get('radius')).toBe('1000');
  });

  it('thins a type ranking galaxy-wide with body filters and goes back to the ranking once they are cleared', async () => {
    renderEnabled(
      'archetype=manufacturing_hub&page_size=1&offset=1&bodies=elw_count:1-,ww_count:2-',
      { anchor: null },
    );
    await screen.findByText('Ranked 2');
    expect(screen.queryByText('Paused')).not.toBeInTheDocument();
    expect(document.getElementById('results-scope')).toHaveTextContent(
      'Galaxy-wide · ranked by Manufacturing Hub fit · minimum tier B · body filters within the best 10,000',
    );
    expect(vi.mocked(api.searchExploreSystems).mock.calls[0][0]).toEqual({
      galaxy_wide: true,
      sort_by: 'development',
      size: 1,
      from: 1,
      reach_ly: 250,
      archetype: 'manufacturing_hub',
      min_score: 60,
      body_filters: { elw_count: { min: 1 }, ww_count: { min: 2 } },
    });
    expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    await fireEvent.click(
      screen.getByRole('button', { name: 'Remove Earth-like filter' }),
    );
    await waitFor(() =>
      expect(mockedPage.url.searchParams.get('bodies')).toBe('ww_count:2-'),
    );
    // A change of filters starts the results again from the first page.
    expect(mockedPage.url.searchParams.has('offset')).toBe(false);
    await waitFor(() =>
      expect(
        vi.mocked(api.searchExploreSystems).mock.lastCall?.[0],
      ).toMatchObject({ from: 0, body_filters: { ww_count: { min: 2 } } }),
    );
    await fireEvent.click(screen.getByRole('button', { name: 'Clear all' }));
    await waitFor(() =>
      expect(mockedPage.url.searchParams.has('bodies')).toBe(false),
    );
    await waitFor(() =>
      expect(api.getArchetypeRankings).toHaveBeenLastCalledWith(
        expect.objectContaining({
          archetype: 'manufacturing_hub',
          offset: 0,
        }),
        expect.anything(),
      ),
    );
  });

  it('thins the galaxy-wide Any list with body filters', async () => {
    renderEnabled('page_size=1&bodies=elw_count:1-', { anchor: null });
    await screen.findByText('Ranked 1');
    expect(document.getElementById('results-scope')).toHaveTextContent(
      'Galaxy-wide · best colony potential · body filters within the best 10,000',
    );
    expect(vi.mocked(api.searchExploreSystems).mock.calls[0][0]).toEqual({
      galaxy_wide: true,
      sort_by: 'development',
      size: 1,
      from: 0,
      reach_ly: 250,
      body_filters: { elw_count: { min: 1 } },
    });
    expect(screen.getByRole('button', { name: 'Body filters' })).toBeEnabled();
    expect(screen.getByRole('combobox', { name: 'Within' })).toBeDisabled();
  });

  it('applies body filters to a type near a system and keeps them on Any', async () => {
    renderEnabled(
      `archetype=manufacturing_hub&near=${anchorA.id64}&bodies=elw_count:1-`,
      { anchor: anchorA },
    );
    await screen.findByText('Ranked 1');
    const trigger = screen.getByRole('button', { name: 'Body filters' });
    expect(trigger).toBeEnabled();
    expect(screen.getByText('Earth-like ≥ 1')).toBeInTheDocument();
    expect(screen.queryByText('Paused')).not.toBeInTheDocument();
    expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    expect(vi.mocked(api.searchExploreSystems).mock.calls[0][0]).toMatchObject({
      archetype: 'manufacturing_hub',
      min_score: 60,
      body_filters: { elw_count: { min: 1 } },
    });
    await fireEvent.change(
      screen.getByRole('combobox', { name: 'Colony archetype' }),
      { target: { value: 'any' } },
    );
    await waitFor(() =>
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(2),
    );
    const anyRequest = vi.mocked(api.searchExploreSystems).mock.calls[1][0];
    expect(anyRequest.body_filters).toEqual({ elw_count: { min: 1 } });
    expect(anyRequest).not.toHaveProperty('archetype');
    expect(mockedPage.url.searchParams.get('bodies')).toBe('elw_count:1-');
    expect(
      screen
        .getByRole('group', { name: 'Applied body filters' })
        .querySelector('.kit-chip'),
    ).toHaveTextContent('Applied');
  });
});

describe.each([false, true])(
  'Explore view-state remount (F4=%s)',
  (enabled) => {
    beforeEach(() => {
      vi.clearAllMocks();
      queryClient.clear();
      localStorage.clear();
      stubRuntime();
      mockFacadeDefaults();
      featureGate.enabled = enabled;
      mockRankedPages();
      vi.mocked(api.autocompleteSystems).mockResolvedValue({
        results: [anchorB],
      });
      vi.mocked(api.getSystem).mockImplementation(async (id64) =>
        anchorDetail(id64 === anchorB.id64 ? anchorB : anchorA),
      );
    });

    afterEach(() => {
      cleanup();
      featureGate.enabled = false;
      vi.useRealTimers();
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
    });

    it('retains a URL-only anchor, draft and results beyond query garbage-collection time', async () => {
      const viewState = new ExploreViewState(
        () => mockedPage,
        enabled,
        applicationStores,
      );
      const host = renderEnabled(
        `${enabled ? 'page_size=3&' : ''}near=${anchorA.id64}`,
        {},
        viewState,
      );
      await screen.findByText('Ranked 1');
      const list = screen.getByRole('list', { name: 'System results' });
      expect(within(list).getAllByRole('listitem')).toHaveLength(3);
      const resultText = list.textContent;
      expect(api.getSystem).toHaveBeenCalledWith(
        anchorA.id64,
        expect.anything(),
      );
      await fireEvent.input(
        screen.getByRole('combobox', { name: 'System name' }),
        { target: { value: 'Retained URL-only draft' } },
      );
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);

      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
      await host.rerender({ mounted: false });
      expect(document.querySelectorAll('canvas')).toHaveLength(0);
      await vi.advanceTimersByTimeAsync(300_001);
      vi.useRealTimers();
      await host.rerender({ mounted: true });

      const restoredList = await screen.findByRole('list', {
        name: 'System results',
      });
      expect(restoredList.textContent).toBe(resultText);
      expect(within(restoredList).getAllByRole('listitem')).toHaveLength(3);
      expect(
        screen.getByRole('heading', { name: 'Near Anchor A' }),
      ).toBeInTheDocument();
      expect(screen.getByRole('combobox', { name: 'System name' })).toHaveValue(
        'Retained URL-only draft',
      );
      expect(document.querySelectorAll('canvas')).toHaveLength(1);
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    });

    it('opens the map again where it was left, showing the same system, without another flight', async () => {
      const viewState = new ExploreViewState(
        () => mockedPage,
        enabled,
        applicationStores,
      );
      const host = renderEnabled(
        `${enabled ? 'page_size=3&' : ''}near=${anchorA.id64}`,
        { anchor: anchorA },
        viewState,
      );
      await screen.findByText('Ranked 1');
      const list = screen.getByRole('list', { name: 'System results' });
      await fireEvent.click(
        within(list).getByRole('button', {
          name: /^Ranked 2\b/,
        }),
      );
      const first = await theMap();
      await waitFor(() =>
        expect(first.getSelection()?.id64).toBe(rankedRows[1].id64),
      );
      expect(first.flights.at(-1)!.target).toMatchObject({
        cursor: [20, 2, 40],
        distance: 70,
      });
      first.land();
      await tick();
      // The player then looks somewhere else.
      first.playerMovesTo({ cursor: [100, 20, 300], distance: 400, yaw: 25 });

      await host.rerender({ mounted: false });
      expect(first.disposed).toBe(true);
      expect(document.querySelectorAll('canvas')).toHaveLength(0);
      await host.rerender({ mounted: true });
      await waitFor(() => expect(fakeMaps).toHaveLength(2));
      const second = await theMap();
      await waitFor(() =>
        expect(onTheMap(second)).toEqual([rankedRows[1].id64]),
      );
      expect(second.getView()).toMatchObject({
        cursor: [100, 20, 300],
        distance: 400,
        yaw: 25,
      });
      expect(second.flights).toHaveLength(0);
      expect(document.querySelectorAll('canvas')).toHaveLength(1);
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
    });

    it('retains the draft, committed search, selection, scroll and My journeys switch without repeating a stale search', async () => {
      const viewState = new ExploreViewState(
        () => mockedPage,
        enabled,
        applicationStores,
      );
      const host = renderEnabled(
        `${enabled ? 'page_size=3&' : ''}near=${anchorA.id64}&radius=250&bodies=elw_count:1-`,
        { anchor: anchorA },
        viewState,
      );
      await screen.findByText('Ranked 1');
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      expect(vi.mocked(api.searchExploreSystems).mock.calls[0][0]).toEqual({
        reference_coords: { x: 10, y: 20, z: 30 },
        filters: { distance: { min: 0, max: 250 }, economy: 'any' },
        body_filters: { elw_count: { min: 1 } },
        sort_by: 'distance',
        size: enabled ? 3 : 24,
        from: 0,
        reach_ly: 250,
        ...floorC(enabled),
      });
      const list = screen.getByRole('list', { name: 'System results' });
      await fireEvent.click(
        within(list).getByRole('button', {
          name: /^Ranked 2\b/,
        }),
      );
      const firstMap = await theMap();
      await waitFor(() =>
        expect(firstMap.getSelection()?.id64).toBe(rankedRows[1].id64),
      );
      await fireEvent.click(
        screen.getByRole('switch', { name: 'My journeys' }),
      );
      await fireEvent.input(
        screen.getByRole('combobox', { name: 'System name' }),
        { target: { value: 'Unsubmitted next destination' } },
      );
      list.scrollTop = 256;
      await fireEvent.scroll(list);
      expect(viewState.resultScrollTop).toBe(256);
      const resultText = list.textContent;
      const committedUrl = mockedPage.url.href;
      expect(document.querySelectorAll('canvas')).toHaveLength(1);
      expect(fakeMaps).toHaveLength(1);

      await host.rerender({ mounted: false });
      expect(document.querySelectorAll('canvas')).toHaveLength(0);
      expect(firstMap.disposed).toBe(true);
      // A fresh TanStack cache alone must not mask an accidental search on
      // remount. A layout change also preserves results older than staleTime.
      const cachedResults = queryClient
        .getQueryCache()
        .getAll()
        .filter((cached) => cached.queryKey[2] === 'results');
      expect(cachedResults).toHaveLength(1);
      for (const cached of cachedResults) {
        queryClient.setQueryData(cached.queryKey, cached.state.data, {
          updatedAt: 1,
        });
      }

      await host.rerender({ mounted: true });
      await screen.findByRole('list', { name: 'System results' });
      await waitFor(() => expect(fakeMaps).toHaveLength(2));
      const secondMap = await theMap();
      const restoredList = screen.getByRole('list', { name: 'System results' });
      expect(restoredList.textContent).toBe(resultText);
      expect(restoredList.scrollTop).toBe(256);
      expect(screen.getByRole('combobox', { name: 'System name' })).toHaveValue(
        'Unsubmitted next destination',
      );
      expect(screen.getByRole('combobox', { name: 'Within' })).toHaveValue(
        '250',
      );
      expect(screen.getByText('Earth-like ≥ 1')).toBeInTheDocument();
      expect(
        screen.getByRole('heading', { name: 'Near Anchor A' }),
      ).toBeInTheDocument();
      expect(
        within(restoredList).getByRole('button', {
          name: /^Ranked 2\b/,
        }),
      ).toHaveAttribute('aria-pressed', 'true');
      expect(mockedPage.url.href).toBe(committedUrl);
      // The same system is on the new map, and the switch is as it was left.
      await waitFor(() =>
        expect(onTheMap(secondMap)).toEqual([rankedRows[1].id64]),
      );
      expect(secondMap.disposed).toBe(false);
      expect(document.querySelectorAll('canvas')).toHaveLength(1);
      expect(
        screen.getByRole('switch', { name: 'My journeys' }),
      ).toHaveAttribute('aria-checked', 'true');
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();

      await fireEvent.input(
        screen.getByRole('combobox', { name: 'System name' }),
        { target: { value: 'Anchor B' } },
      );
      await fireEvent.click(
        await screen.findByRole('option', { name: /Anchor B/ }),
      );
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(2),
      );
      expect(vi.mocked(api.searchExploreSystems).mock.lastCall?.[0]).toEqual({
        reference_coords: { x: -10, y: -20, z: -30 },
        filters: { distance: { min: 0, max: 250 }, economy: 'any' },
        body_filters: { elw_count: { min: 1 } },
        sort_by: 'distance',
        size: enabled ? 3 : 24,
        from: 0,
        reach_ly: 250,
        ...floorC(enabled),
      });
      // The star now searched near is selected, so the map flies to it.
      await waitFor(() =>
        expect(secondMap.getSelection()?.id64).toBe(anchorB.id64),
      );
      expect(secondMap.flights.at(-1)!.target).toMatchObject({
        cursor: [-10, -20, -30],
        distance: 70,
      });
      await host.rerender({ mounted: false });
      expect(secondMap.disposed).toBe(true);
    });

    if (enabled) {
      it('keeps the previous Any results through a failed ranking and remount without retrying it', async () => {
        vi.mocked(api.getArchetypeRankings).mockRejectedValue(
          new api.ApiError(503, '/api/archetypes/rankings', {
            detail: 'Temporarily unavailable',
          }),
        );
        const viewState = new ExploreViewState(
          () => mockedPage,
          enabled,
          applicationStores,
        );
        // Galaxy-wide, so picking a type asks the rankings route.
        const host = renderEnabled('page_size=3', { anchor: null }, viewState);
        await screen.findByText('Ranked 1');
        const list = screen.getByRole('list', {
          name: 'System results',
        });
        const resultText = list.textContent;
        expect(within(list).getAllByRole('listitem')).toHaveLength(3);
        await fireEvent.change(
          screen.getByRole('combobox', { name: 'Colony archetype' }),
          { target: { value: 'manufacturing_hub' } },
        );
        await screen.findByRole('heading', {
          name: 'Ranking temporarily unavailable',
        });
        expect(
          screen.getByRole('list', { name: 'System results' }).textContent,
        ).toBe(resultText);
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
        expect(api.getArchetypeRankings).toHaveBeenCalledTimes(1);

        await host.rerender({ mounted: false });
        await host.rerender({ mounted: true });

        await screen.findByRole('heading', {
          name: 'Ranking temporarily unavailable',
        });
        const restoredList = screen.getByRole('list', {
          name: 'System results',
        });
        expect(restoredList.textContent).toBe(resultText);
        expect(within(restoredList).getAllByRole('listitem')).toHaveLength(3);
        expect(
          screen.getByRole('combobox', { name: 'Colony archetype' }),
        ).toHaveValue('manufacturing_hub');
        expect(document.querySelectorAll('canvas')).toHaveLength(1);
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
        expect(api.getArchetypeRankings).toHaveBeenCalledTimes(1);

        await fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
        await waitFor(() =>
          expect(api.getArchetypeRankings).toHaveBeenCalledTimes(2),
        );
        await screen.findByRole('heading', {
          name: 'Ranking temporarily unavailable',
        });
        expect(
          screen.getByRole('list', { name: 'System results' }).textContent,
        ).toBe(resultText);
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      });

      it('retains a type near a system, its tier, page and filters without repeating the search', async () => {
        const viewState = new ExploreViewState(
          () => mockedPage,
          enabled,
          applicationStores,
        );
        const host = renderEnabled(
          `archetype=manufacturing_hub&min-tier=A&page_size=2&offset=1&near=${anchorA.id64}&radius=1000&bodies=elw_count:1-`,
          { anchor: anchorA },
          viewState,
        );
        await screen.findByText('Ranked 2');
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
        expect(vi.mocked(api.searchExploreSystems).mock.calls[0][0]).toEqual({
          reference_coords: { x: 10, y: 20, z: 30 },
          filters: { distance: { min: 0, max: 500 }, economy: 'any' },
          body_filters: { elw_count: { min: 1 } },
          sort_by: 'distance',
          size: 2,
          from: 1,
          reach_ly: 250,
          archetype: 'manufacturing_hub',
          min_score: 76,
        });
        expect(api.getArchetypeRankings).not.toHaveBeenCalled();
        expect(screen.getByRole('combobox', { name: 'Within' })).toHaveValue(
          '500',
        );
        expect(viewState.radius).toBe(1000);
        const list = screen.getByRole('list', { name: 'System results' });
        await fireEvent.click(
          within(list).getByRole('button', {
            name: /^Ranked 2\b/,
          }),
        );
        await fireEvent.input(
          screen.getByRole('combobox', { name: 'System name' }),
          { target: { value: 'Another unsubmitted destination' } },
        );
        list.scrollTop = 128;
        await fireEvent.scroll(list);
        const ranking = viewState.rankingState;
        const committedUrl = mockedPage.url.href;

        await host.rerender({ mounted: false });
        const cachedRankings = queryClient
          .getQueryCache()
          .getAll()
          .filter((cached) => cached.queryKey[2] === 'results');
        expect(cachedRankings).toHaveLength(1);
        for (const cached of cachedRankings) {
          queryClient.setQueryData(cached.queryKey, cached.state.data, {
            updatedAt: 1,
          });
        }
        await host.rerender({ mounted: true });

        const restoredList = await screen.findByRole('list', {
          name: 'System results',
        });
        expect(within(restoredList).getByText('Ranked 2')).toBeInTheDocument();
        expect(
          screen.getByRole('combobox', { name: 'Colony archetype' }),
        ).toHaveValue('manufacturing_hub');
        expect(screen.getByRole('combobox', { name: 'Show only' })).toHaveValue(
          'A',
        );
        expect(
          screen.getByRole('combobox', { name: 'System name' }),
        ).toHaveValue('Another unsubmitted destination');
        expect(screen.getByRole('combobox', { name: 'Within' })).toHaveValue(
          '500',
        );
        expect(viewState.radius).toBe(1000);
        expect(screen.getByRole('combobox', { name: 'Within' })).toBeEnabled();
        expect(screen.getByText('Earth-like ≥ 1')).toBeInTheDocument();
        expect(screen.queryByText('Paused')).not.toBeInTheDocument();
        expect(restoredList.scrollTop).toBe(128);
        expect(
          within(restoredList).getByRole('button', {
            name: /^Ranked 2\b/,
          }),
        ).toHaveAttribute('aria-pressed', 'true');
        expect(viewState.rankingState).toEqual(ranking);
        expect(mockedPage.url.href).toBe(committedUrl);
        expect(document.querySelectorAll('canvas')).toHaveLength(1);
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
        expect(api.getArchetypeRankings).not.toHaveBeenCalled();

        await fireEvent.change(
          screen.getByRole('combobox', { name: 'Colony archetype' }),
          { target: { value: 'any' } },
        );
        await waitFor(() =>
          expect(api.searchExploreSystems).toHaveBeenCalledTimes(2),
        );
        expect(api.getArchetypeRankings).not.toHaveBeenCalled();
        const anyRequest = vi.mocked(api.searchExploreSystems).mock
          .lastCall?.[0];
        expect(anyRequest?.body_filters).toEqual({ elw_count: { min: 1 } });
        expect(anyRequest).not.toHaveProperty('archetype');
      });
    }
  },
);

describe.each([false, true])(
  'Full-window map beside the Finder with F4=%s',
  (enabled) => {
    beforeEach(() => {
      vi.clearAllMocks();
      queryClient.clear();
      localStorage.clear();
      stubRuntime();
      mockFacadeDefaults();
      featureGate.enabled = enabled;
      mockRankedPages();
      vi.mocked(api.getSystem).mockResolvedValue(anchorDetail(anchorA));
    });

    afterEach(() => {
      cleanup();
      queryClient.clear();
      featureGate.enabled = false;
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
    });

    function finderPanel(): HTMLElement {
      return document.getElementById('finder-panel')!;
    }

    it('leaves Show on map unavailable when selected coordinates are missing', async () => {
      vi.mocked(api.searchExploreSystems).mockResolvedValue({
        ...finderPage(0, 3),
        results: [{ ...rankedRows[0], coords: undefined }],
      });
      renderEnabled(`${enabled ? 'page_size=3&' : ''}near=${anchorA.id64}`, {
        anchor: anchorA,
      });
      const selection = await screen.findByRole('button', {
        name: /^Ranked 1\b/,
      });
      await fireEvent.click(selection);
      for (const opener of screen.getAllByRole('button', {
        name: 'Show on map',
      }))
        expect(opener).toBeDisabled();
      // Said beside the button in the Finder, and again over the map.
      const said = screen.getAllByText(
        'Map coordinates unavailable for the selected system.',
      );
      expect(said).toHaveLength(2);
      for (const place of said) expect(place).toBeVisible();
      const map = await theMap();
      expect(map.systemCount()).toBe(0);
      expect(map.flights).toHaveLength(0);
    });

    it('opens with the map on screen beside the Finder and runs no Finder query', async () => {
      renderEnabled('');
      await tick();
      expect(
        screen.getByRole('combobox', { name: 'System name' }),
      ).toBeVisible();
      // The Finder panel is a labelled region named by the page heading.
      const panel = screen.getByRole('region', {
        name: 'Chart a promising system.',
      });
      expect(panel).toBe(finderPanel());
      expect(panel).not.toHaveAttribute('hidden');
      const map = await screen.findByRole('region', { name: 'Galaxy map' });
      expect(screen.queryByRole('dialog', { name: /Galaxy map$/ })).toBeNull();
      for (const name of ['Close map', 'Expand', 'Back to results'])
        expect(within(map).queryByRole('button', { name })).toBeNull();
      // Nothing is selected, so there is nothing to centre on.
      expect(
        within(map).getByRole('button', {
          name: 'Centre on the selected system',
        }),
      ).toBeDisabled();
      expect(document.querySelector('[inert]')).toBeNull();
      const galaxyMap = await theMap();
      expect(fakeMaps).toHaveLength(1);
      expect(galaxyMap.systemCount()).toBe(0);
      expect(
        within(map).getByRole('button', { name: 'Whole galaxy' }),
      ).toBeEnabled();
      expect(map.querySelectorAll('canvas')).toHaveLength(1);
      expect(map.querySelector('.selection-status')).toHaveTextContent(
        'Select a result to show it on the map.',
      );
      expect(api.searchExploreSystems).not.toHaveBeenCalled();
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    });

    it('moves the map’s camera with Centre on and Whole galaxy without touching the search or the URL', async () => {
      renderEnabled(`${enabled ? 'page_size=3&' : ''}near=${anchorA.id64}`, {
        anchor: anchorA,
      });
      const results = await screen.findByRole('list', {
        name: 'System results',
      });
      await fireEvent.click(
        await within(results).findByRole('button', {
          name: /^Ranked 2\b/,
        }),
      );
      const map = await screen.findByRole('region', {
        name: 'Ranked 2 · Galaxy map',
      });
      const galaxyMap = await theMap();
      await waitFor(() =>
        expect(galaxyMap.getSelection()?.id64).toBe(rankedRows[1].id64),
      );
      galaxyMap.land();
      await tick();
      const searchCount = vi.mocked(api.searchExploreSystems).mock.calls.length;
      const url = mockedPage.url.href;
      const home = { ...galaxyMap.flights[0].target };

      await fireEvent.click(
        within(map).getByRole('button', { name: 'Whole galaxy' }),
      );
      // Out to the view the map first opened on.
      expect(galaxyMap.flights.at(-1)!.target.distance).toBeGreaterThan(50_000);
      galaxyMap.land();
      await tick();
      await fireEvent.click(
        within(map).getByRole('button', { name: 'Centre on Ranked 2' }),
      );
      expect(galaxyMap.flights.at(-1)!.target).toEqual(home);
      expect(home).toEqual({ cursor: [20, 2, 40], distance: 70, pitch: 35 });
      // The selection, the search and the address are as they were.
      expect(galaxyMap.getSelection()?.id64).toBe(rankedRows[1].id64);
      expect(mockedPage.url.href).toBe(url);
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(searchCount);
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
      expect(fakeMaps).toHaveLength(1);
    });

    it('keeps Inspect and Plan here reachable from the selected-system footer', async () => {
      renderEnabled(`${enabled ? 'page_size=3&' : ''}near=${anchorA.id64}`, {
        anchor: anchorA,
      });
      const results = await screen.findByRole('list', {
        name: 'System results',
      });
      await fireEvent.click(
        await within(results).findByRole('button', {
          name: /^Ranked 2\b/,
        }),
      );
      const map = await screen.findByRole('region', {
        name: 'Ranked 2 · Galaxy map',
      });
      const origin = mockedPage.url.searchParams.toString();
      const inspect = within(map).getByRole('link', {
        name: 'Inspect Ranked 2',
      });
      const href = new URL(inspect.getAttribute('href')!, mockedPage.url);
      expect(href.pathname).toBe('/inspect');
      expect(href.searchParams.get('system')).toBe(rankedRows[1].id64);
      expect(
        within(map).getByRole('link', { name: 'Plan here' }),
      ).toHaveAttribute('href', `/plan?system=${rankedRows[1].id64}`);
      expect(
        map.querySelector(
          `[data-selected-system-card="${rankedRows[1].id64}"]`,
        ),
      ).toBeInTheDocument();
      // What the map shows is also said in words, to every reader.
      const said = map.querySelector('.selection-status')!;
      expect(said).toHaveAttribute('role', 'status');
      await waitFor(() =>
        expect(said).toHaveTextContent(
          'Ranked 2 and the 0 real stars within 15 ly of it.',
        ),
      );
      if (enabled) {
        mockedGoto.mockClear();
        await fireEvent.click(inspect);
        const destination = new URL(
          mockedGoto.mock.lastCall![0],
          mockedPage.url,
        );
        expect(destination.pathname).toBe('/inspect');
        expect(destination.searchParams.get('system')).toBe(rankedRows[1].id64);
        expect(destination.searchParams.get('from')).toBe(origin);
        expect(mockedGoto.mock.lastCall![1].state.anchor).toEqual(anchorA);
      }
    });

    it.each(['loading', 'empty'] as const)(
      'dismisses %s autocomplete on Escape and keeps the map',
      async (state) => {
        if (state === 'loading')
          vi.mocked(api.autocompleteSystems).mockImplementation(
            () => new Promise(() => {}),
          );
        else
          vi.mocked(api.autocompleteSystems).mockResolvedValue({ results: [] });
        renderEnabled('');
        const map = await screen.findByRole('region', { name: /Galaxy map$/ });
        const input = screen.getByRole('combobox', { name: 'System name' });
        input.focus();
        await fireEvent.input(input, { target: { value: 'Qzz' } });
        await screen.findByText(
          state === 'loading' ? 'Searching catalogue…' : 'No matching systems.',
        );
        expect(input).toHaveAttribute('aria-expanded', 'true');
        await fireEvent.keyDown(input, { key: 'Escape' });
        expect(input).toHaveAttribute('aria-expanded', 'false');
        expect(input).toHaveFocus();
        expect(
          screen.queryByRole('listbox', { name: 'System suggestions' }),
        ).not.toBeInTheDocument();
        await fireEvent.keyDown(input, { key: 'Escape' });
        expect(map).toBeInTheDocument();
        expect(input).toHaveFocus();
        expect(input).toHaveValue('Qzz');
        expect(api.searchExploreSystems).not.toHaveBeenCalled();
        expect(api.getArchetypeRankings).not.toHaveBeenCalled();
      },
    );

    it('hides and restores the Finder panel keeping search, scroll, selection and the one canvas', async () => {
      const viewState = new ExploreViewState(
        () => mockedPage,
        enabled,
        applicationStores,
      );
      renderEnabled(
        `${enabled ? 'page_size=3&' : ''}near=${anchorA.id64}&radius=250&bodies=elw_count:1-`,
        { anchor: anchorA },
        viewState,
      );
      const list = await screen.findByRole('list', { name: 'System results' });
      await fireEvent.click(
        await within(list).findByRole('button', {
          name: /^Ranked 2\b/,
        }),
      );
      await fireEvent.input(
        screen.getByRole('combobox', { name: 'System name' }),
        { target: { value: 'Unsubmitted draft' } },
      );
      list.scrollTop = 256;
      await fireEvent.scroll(list);
      const first = await theMap();
      const canvas = document.querySelector('canvas');
      const committedUrl = mockedPage.url.href;
      const beforeSearch = vi.mocked(api.searchExploreSystems).mock.calls
        .length;

      const hide = screen.getByRole('button', { name: 'Hide Finder' });
      expect(hide).toHaveAttribute('aria-controls', 'finder-panel');
      expect(hide).toHaveAttribute('aria-expanded', 'true');
      hide.focus();
      await fireEvent.click(hide);
      expect(viewState.finderPanelOpen).toBe(false);
      expect(finderPanel()).toHaveAttribute('hidden');
      // The results stay in front of the map, now across the whole width,
      // and "Show Finder" is above them.
      const results = screen.getByRole('region', { name: 'Near Anchor A' });
      const map = screen.getByRole('region', { name: /Galaxy map$/ });
      expect(
        within(map).queryByRole('button', { name: 'Show Finder' }),
      ).toBeNull();
      const show = within(results).getByRole('button', {
        name: 'Show Finder',
      });
      await waitFor(() => expect(show).toHaveFocus());
      expect(list.scrollTop).toBe(256);

      await fireEvent.click(show);
      expect(viewState.finderPanelOpen).toBe(true);
      expect(finderPanel()).not.toHaveAttribute('hidden');
      await waitFor(() =>
        expect(
          screen.getByRole('button', { name: 'Hide Finder' }),
        ).toHaveFocus(),
      );
      expect(list.scrollTop).toBe(256);
      expect(
        within(results).queryByRole('button', { name: 'Show Finder' }),
      ).toBeNull();
      expect(document.querySelector('canvas')).toBe(canvas);
      expect(fakeMaps).toHaveLength(1);
      expect(first.disposed).toBe(false);
      expect(screen.getByRole('combobox', { name: 'System name' })).toHaveValue(
        'Unsubmitted draft',
      );
      expect(screen.getByRole('combobox', { name: 'Within' })).toHaveValue(
        '250',
      );
      expect(screen.getByText('Earth-like ≥ 1')).toBeInTheDocument();
      expect(
        within(list).getByRole('button', {
          name: /^Ranked 2\b/,
        }),
      ).toHaveAttribute('aria-pressed', 'true');
      expect(mockedPage.url.href).toBe(committedUrl);
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(beforeSearch);
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    });

    it('at phone width Show on map hides the panel over the map and Show Finder returns to the result', async () => {
      vi.stubGlobal('matchMedia', (query: string) => ({
        matches:
          query === '(prefers-reduced-motion: reduce)' ||
          query === '(max-width: 900px)',
      }));
      const viewState = new ExploreViewState(
        () => mockedPage,
        enabled,
        applicationStores,
      );
      renderEnabled(
        `${enabled ? 'page_size=3&' : ''}near=${anchorA.id64}`,
        { anchor: anchorA },
        viewState,
      );
      const list = await screen.findByRole('list', { name: 'System results' });
      await fireEvent.click(
        await within(list).findByRole('button', {
          name: /^Ranked 2\b/,
        }),
      );
      list.scrollTop = 96;
      await fireEvent.scroll(list);
      const opener = within(list).getByRole('button', { name: 'Show on map' });
      opener.focus();
      // The Finder is in the map's place, so the map cannot be seen: it is
      // given nothing to draw and flies nowhere while nobody is looking.
      const map = await theMap();
      await tick();
      expect(map.systemCount()).toBe(0);
      expect(map.flights).toHaveLength(0);
      // In words, the page already says what the map will show.
      expect(document.querySelector('.selection-status')).toHaveTextContent(
        'Ranked 2',
      );

      await fireEvent.click(opener);
      expect(viewState.finderPanelOpen).toBe(false);
      expect(finderPanel()).toHaveAttribute('hidden');
      // Now it is seen: the system is on it and the one flight is seen too.
      await waitFor(() => expect(onTheMap(map)).toEqual([rankedRows[1].id64]));
      expect(map.flights).toHaveLength(1);
      expect(map.flights[0].target).toEqual({
        cursor: [20, 2, 40],
        distance: 70,
        pitch: 35,
      });
      const show = screen.getByRole('button', { name: 'Show Finder' });
      await waitFor(() => expect(show).toHaveFocus());
      await fireEvent.click(show);
      expect(viewState.finderPanelOpen).toBe(true);
      await waitFor(() => expect(opener).toHaveFocus());
      expect(list.scrollTop).toBe(96);
      // Back to the map for the same system: it is centred, not filled again.
      const fills = map.fills;
      map.land();
      await tick();
      map.playerMovesTo({ cursor: [900, 0, 900], distance: 5_000 });
      await fireEvent.click(opener);
      await waitFor(() => expect(map.flights).toHaveLength(2));
      expect(map.flights[1].target).toEqual(map.flights[0].target);
      expect(map.fills).toBe(fills);
      expect(fakeMaps).toHaveLength(1);
    });

    it.each([true, false])(
      'passes axe across the whole page with the Finder panel shown: %s',
      async (shown) => {
        renderEnabled(`${enabled ? 'page_size=3&' : ''}near=${anchorA.id64}`, {
          anchor: anchorA,
        });
        await screen.findByRole('list', { name: 'System results' });
        await screen.findByRole('region', { name: /Galaxy map$/ });
        if (!shown)
          await fireEvent.click(
            screen.getByRole('button', { name: 'Hide Finder' }),
          );
        // Landmark rules need the real document root (the page's <main>).
        const result = await axe.run(document.body, {
          rules: { 'color-contrast': { enabled: false } },
        });
        expect(result.violations).toEqual([]);
      },
    );

    it('Show on map on a wide screen flies back to the system and keeps the Finder panel', async () => {
      const viewState = new ExploreViewState(
        () => mockedPage,
        enabled,
        applicationStores,
      );
      renderEnabled(
        `${enabled ? 'page_size=3&' : ''}near=${anchorA.id64}`,
        { anchor: anchorA },
        viewState,
      );
      const list = await screen.findByRole('list', { name: 'System results' });
      await fireEvent.click(
        await within(list).findByRole('button', {
          name: /^Ranked 2\b/,
        }),
      );
      const mapSection = document.querySelector<HTMLElement>(
        '[data-explore-map-window]',
      )!;
      const scrollIntoView = vi.fn();
      mapSection.scrollIntoView = scrollIntoView;
      const map = await theMap();
      await waitFor(() =>
        expect(map.getSelection()?.id64).toBe(rankedRows[1].id64),
      );
      map.land();
      await tick();
      // The player looks elsewhere, then asks to be shown the system again.
      map.playerMovesTo({ cursor: [900, 0, 900], distance: 5_000 });
      await fireEvent.click(
        within(list).getByRole('button', { name: 'Show on map' }),
      );
      expect(viewState.finderPanelOpen).toBe(true);
      expect(finderPanel()).not.toHaveAttribute('hidden');
      await tick();
      expect(scrollIntoView).not.toHaveBeenCalled();
      expect(map.flights).toHaveLength(2);
      expect(map.flights[1].target).toEqual({
        cursor: [20, 2, 40],
        distance: 70,
        pitch: 35,
      });
    });

    // With the Finder on, as the site runs: the selection is in the address.
    // (Off, it is kept in a store shared with the tests before this one.)
    it.runIf(enabled)(
      'Show on map with nothing selected shows the system searched near',
      async () => {
        const viewState = new ExploreViewState(
          () => mockedPage,
          enabled,
          applicationStores,
        );
        // Arriving from the home page, a link or a reload: near, nothing selected.
        renderEnabled(
          `${enabled ? 'page_size=3&' : ''}near=${anchorA.id64}`,
          { anchor: anchorA },
          viewState,
        );
        await screen.findByRole('list', { name: 'System results' });
        await fireEvent.click(
          document.querySelector<HTMLButtonElement>('[data-explore-map-open]')!,
        );
        expect(viewState.mapInFront).toBe(true);
        const map = await theMap();
        await waitFor(() =>
          expect(map.getSelection()?.id64).toBe(anchorA.id64),
        );
      },
    );

    it('on a wide screen the results and the map take turns beside the Finder', async () => {
      const viewState = new ExploreViewState(
        () => mockedPage,
        enabled,
        applicationStores,
      );
      renderEnabled(
        `${enabled ? 'page_size=3&' : ''}near=${anchorA.id64}`,
        { anchor: anchorA },
        viewState,
      );
      const list = await screen.findByRole('list', { name: 'System results' });
      const results = screen.getByRole('region', { name: 'Near Anchor A' });
      const mapWindow = document.querySelector<HTMLElement>(
        '[data-explore-map-window]',
      )!;
      // After a search the results are in front; the map behind them keeps
      // following the selection but cannot be reached.
      expect(results).not.toHaveAttribute('hidden');
      expect(mapWindow).toHaveAttribute('data-map-in-front', 'false');
      expect(mapWindow.inert).toBe(true);
      await fireEvent.click(
        await within(list).findByRole('button', {
          name: /^Ranked 2\b/,
        }),
      );
      const map = await theMap();
      await waitFor(() =>
        expect(map.getSelection()?.id64).toBe(rankedRows[1].id64),
      );
      list.scrollTop = 128;
      await fireEvent.scroll(list);

      const opener = within(list).getByRole('button', { name: 'Show on map' });
      await fireEvent.click(opener);
      expect(viewState.mapInFront).toBe(true);
      expect(results).toHaveAttribute('hidden');
      expect(mapWindow).toHaveAttribute('data-map-in-front', 'true');
      expect(mapWindow.inert).toBe(false);
      const back = screen.getByRole('button', { name: 'Show results' });
      expect(back).toHaveAttribute('aria-controls', 'explore-results');
      await waitFor(() => expect(back).toHaveFocus());
      expect(viewState.finderPanelOpen).toBe(true);

      await fireEvent.click(back);
      expect(viewState.mapInFront).toBe(false);
      expect(results).not.toHaveAttribute('hidden');
      expect(mapWindow).toHaveAttribute('data-map-in-front', 'false');
      await waitFor(() => expect(opener).toHaveFocus());
      expect(list.scrollTop).toBe(128);
      expect(
        screen.queryByRole('button', { name: 'Show results' }),
      ).not.toBeInTheDocument();

      // A changed search shows its results, even with the map in front.
      await fireEvent.click(opener);
      expect(viewState.mapInFront).toBe(true);
      await fireEvent.change(screen.getByRole('combobox', { name: 'Within' }), {
        target: { value: '250' },
      });
      expect(viewState.mapInFront).toBe(false);
      expect(results).not.toHaveAttribute('hidden');
    });

    it('before a search the map is in front and the results are not shown', async () => {
      renderEnabled('');
      const mapWindow = document.querySelector<HTMLElement>(
        '[data-explore-map-window]',
      )!;
      expect(mapWindow).toHaveAttribute('data-map-in-front', 'true');
      expect(mapWindow.inert).toBe(false);
      expect(document.getElementById('explore-results')).toHaveAttribute(
        'hidden',
      );
      expect(
        screen.getByText('Search for a system above to see what is near it.'),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole('button', { name: 'Show results' }),
      ).not.toBeInTheDocument();
    });
  },
);

// The live site's real answer for the 30 ly box around Sol on 13 Nov 2026
// (see the fixture's own note), as the API client hands it on.
const solBoxAnswer: api.CatalogueViewportResponse = {
  systems: solBox.response.systems.map((system) => ({
    id64: parseId64(String(system.id64)),
    name: system.name,
    x: system.x,
    y: system.y,
    z: system.z,
    mainStarClass: system.main_star_class,
    populated: system.populated,
  })),
  truncated: false,
};
const sol: ExploreAnchor = {
  id64: parseId64('10477373803'),
  name: 'Sol',
  x: 0,
  y: 0,
  z: 0,
};

describe.each([false, true])(
  'Explore shows the selected system on the map (F4=%s)',
  (enabled) => {
    beforeEach(() => {
      vi.clearAllMocks();
      queryClient.clear();
      localStorage.clear();
      stubRuntime();
      mockFacadeDefaults();
      featureGate.enabled = enabled;
      mockRankedPages();
      vi.mocked(api.autocompleteSystems).mockResolvedValue({ results: [sol] });
      vi.mocked(api.getSystem).mockResolvedValue(anchorDetail(sol));
      vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue(
        solBoxAnswer,
      );
    });

    afterEach(() => {
      cleanup();
      queryClient.clear();
      featureGate.enabled = false;
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
    });

    async function chooseSol(): Promise<FakeMap> {
      renderEnabled('');
      const map = await theMap();
      const input = screen.getByRole('combobox', { name: 'System name' });
      await fireEvent.input(input, { target: { value: 'Sol' } });
      // The watchlist's sort list also has an option that names Sol.
      const suggestions = await screen.findByRole('listbox', {
        name: 'System suggestions',
      });
      await fireEvent.click(
        await within(suggestions).findByRole('option', { name: /Sol/ }),
      );
      return map;
    }

    it('flies to a star searched near and shows the real stars within 15 ly of it, each by its star class', async () => {
      const map = await chooseSol();
      await waitFor(() => expect(map.getSelection()?.name).toBe('Sol'));
      expect(map.flights.at(-1)!.target).toEqual({
        cursor: [0, 0, 0],
        distance: 70,
        pitch: 35,
      });
      // One request, for the 30 ly box around Sol.
      await waitFor(() => expect(map.systemCount()).toBe(51));
      expect(api.getCatalogueViewportSystems).toHaveBeenCalledExactlyOnceWith(
        {
          minX: -15,
          maxX: 15,
          minY: -15,
          maxY: 15,
          minZ: -15,
          maxZ: 15,
          limit: 10_000,
        },
        expect.anything(),
      );
      // The box holds 84 systems; 33 are in its corners, beyond 15 ly.
      expect(onTheMap(map)[0]).toBe(sol.id64);
      expect(map.namesIn('G class star')).toEqual(
        expect.arrayContaining(['Sol', 'Alpha Centauri']),
      );
      expect(map.namesIn('M class star')).toContain("Barnard's Star");
      expect(map.groups.get('G class star')!.color).not.toEqual(
        map.groups.get('M class star')!.color,
      );
      expect(map.spheres).toEqual([
        expect.objectContaining({ position: [0, 0, 0], radius: 15 }),
      ]);
      // Find's results are a list beside the map; they are not on it.
      await screen.findByText('Ranked 1');
      expect(onTheMap(map)).not.toContain(rankedRows[0].id64);
      // The same is said in words, and the map is named for the system.
      expect(document.querySelector('.selection-status')).toHaveTextContent(
        'Sol and the 50 real stars within 15 ly of it.',
      );
      expect(
        screen.getByRole('region', { name: 'Sol · Galaxy map' }),
      ).toBeInTheDocument();
      expect(
        (await map.panel(sol.id64))!.values!.find(
          (value) => value.label === 'STARS IN RANGE',
        ),
      ).toEqual({ label: 'STARS IN RANGE', value: '50' });
    });

    it('says the map could not start, asks for no stars, and leaves Find working', async () => {
      failNextMap('This map needs WebGL2.');
      renderEnabled('');
      await screen.findByRole('alert');
      const input = screen.getByRole('combobox', { name: 'System name' });
      await fireEvent.input(input, { target: { value: 'Sol' } });
      const suggestions = await screen.findByRole('listbox', {
        name: 'System suggestions',
      });
      await fireEvent.click(
        await within(suggestions).findByRole('option', { name: /Sol/ }),
      );
      await screen.findByText('Ranked 1');
      const said = document.querySelector<HTMLElement>('.selection-status')!;
      expect(said).toHaveTextContent(
        'The galaxy map could not start on this device.',
      );
      expect(within(said).queryByRole('button', { name: 'Retry' })).toBeNull();
      expect(api.getCatalogueViewportSystems).not.toHaveBeenCalled();
      // The camera buttons have nothing to move.
      expect(
        screen.getByRole('button', { name: 'Whole galaxy' }),
      ).toBeDisabled();
      expect(
        screen.getByRole('button', { name: /^Centre on / }),
      ).toBeDisabled();
      // Find's own list is there all the same.
      expect(
        within(
          screen.getByRole('list', { name: 'System results' }),
        ).getAllByRole('listitem').length,
      ).toBeGreaterThan(0);
    });

    it('says so when the stars do not load, keeps the system on the map, and tries again when asked', async () => {
      vi.mocked(api.getCatalogueViewportSystems).mockRejectedValue(
        new api.ApiError(503, '/api/map/systems', {}),
      );
      const map = await chooseSol();
      const said = document.querySelector<HTMLElement>('.selection-status')!;
      await waitFor(
        () =>
          expect(said).toHaveTextContent(
            'Sol is on the map. The stars within 15 ly of it did not load.',
          ),
        { timeout: 4_000 },
      );
      expect(said).toHaveAttribute('data-map-stars', 'failed');
      expect(onTheMap(map)).toEqual([sol.id64]);
      expect(map.getSelection()?.name).toBe('Sol');

      vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue(
        solBoxAnswer,
      );
      await fireEvent.click(
        within(said).getByRole('button', { name: 'Retry' }),
      );
      await waitFor(() => expect(map.systemCount()).toBe(51));
      expect(said).toHaveTextContent(
        'Sol and the 50 real stars within 15 ly of it.',
      );
      expect(within(said).queryByRole('button', { name: 'Retry' })).toBeNull();
    });

    it('flies to the star searched near at once, before the search has answered, and never looks it up', async () => {
      // Seen on the live site: the flight waited a second for the search
      // and a lookup of the star by its id, and meanwhile the page said its
      // position was unavailable. The suggestion that was chosen carries it.
      let answerSearch!: (page: ReturnType<typeof finderPage>) => void;
      vi.mocked(api.searchExploreSystems).mockReturnValue(
        new Promise((resolve) => {
          answerSearch = resolve as typeof answerSearch;
        }),
      );
      vi.mocked(api.getSystem).mockClear();
      const map = await chooseSol();
      await waitFor(() => expect(map.getSelection()?.name).toBe('Sol'));
      expect(map.flights.at(-1)!.target).toEqual({
        cursor: [0, 0, 0],
        distance: 70,
        pitch: 35,
      });
      await waitFor(() => expect(map.systemCount()).toBe(51));
      const said = document.querySelector('.selection-status')!;
      expect(said).toHaveTextContent(
        'Sol and the 50 real stars within 15 ly of it.',
      );
      expect(
        screen.queryByText(
          'Map coordinates unavailable for the selected system.',
        ),
      ).toBeNull();
      expect(screen.queryByText('Looking up the selected system…')).toBeNull();
      // The search answers afterwards, without Sol among its results, and
      // changes nothing that is shown.
      const flights = map.flights.length;
      answerSearch(finderPage(0, enabled ? 3 : 24));
      await screen.findByText('Ranked 1');
      expect(map.getSelection()?.name).toBe('Sol');
      expect(map.flights).toHaveLength(flights);
      expect(map.systemCount()).toBe(51);
      expect(api.getSystem).not.toHaveBeenCalled();
    });

    it('keeps showing the star that was searched near when it is searched near no more', async () => {
      vi.mocked(api.getSystem).mockClear();
      const map = await chooseSol();
      await waitFor(() => expect(map.systemCount()).toBe(51));
      const flights = map.flights.length;
      await fireEvent.click(
        screen.getByRole('button', { name: 'Stop searching near Sol' }),
      );
      await waitFor(() =>
        expect(
          screen.queryByRole('button', { name: 'Stop searching near Sol' }),
        ).toBeNull(),
      );
      expect(map.getSelection()?.name).toBe('Sol');
      expect(map.systemCount()).toBe(51);
      expect(map.flights).toHaveLength(flights);
      expect(api.getSystem).not.toHaveBeenCalled();
    });

    it('shows a star in its own right when SHOW ON MAP is pressed in the map’s panel, though it is no result of Find', async () => {
      const map = await chooseSol();
      await waitFor(() => expect(map.systemCount()).toBe(51));
      // Alpha Centauri is one of Sol's stars in range, and inhabited: Find
      // does not list it.
      const star = [...map.systems.values()].find(
        (system) => system.name === 'Alpha Centauri',
      )!;
      const panel = (await map.panel(star.id64!))!;
      expect(panel.actions!.map((action) => action.label)).toEqual([
        'SHOW ON MAP',
        'INSPECT',
      ]);
      panel.actions![0].onSelect(star);
      // It is now the system shown: selected, flown to, and named.
      await waitFor(() =>
        expect(map.getSelection()?.name).toBe('Alpha Centauri'),
      );
      expect(map.flights.at(-1)!.target).toEqual({
        cursor: star.position,
        distance: 70,
        pitch: 35,
      });
      await waitFor(() =>
        expect(document.querySelector('.selection-status')).toHaveTextContent(
          /^Alpha Centauri and the \d+ real stars within 15 ly of it\.$/u,
        ),
      );
      expect(
        screen.getByRole('region', { name: 'Alpha Centauri · Galaxy map' }),
      ).toBeInTheDocument();
      // Its own panel now has INSPECT only: it is the chosen system.
      expect(
        (await map.panel(star.id64!))!.actions!.map((action) => action.label),
      ).toEqual(['INSPECT']);
      // The map gave its position: the site is not asked for it again.
      expect(api.getSystem).not.toHaveBeenCalledWith(
        star.id64,
        expect.anything(),
      );
    });

    it('opens the map where it was left when the page is opened again on the same system, and flies only for another', async () => {
      const first = await chooseSol();
      await waitFor(() => expect(first.systemCount()).toBe(51));
      first.land();
      await tick();
      first.playerMovesTo({ cursor: [4, 0, 4], distance: 120, yaw: 15 });
      const address = mockedPage.url.search.replace(/^\?/u, '');
      const state = mockedPage.state;

      // To Inspect and back: the page, and its own state, are made anew.
      cleanup();
      expect(first.disposed).toBe(true);
      renderEnabled(address, state);
      await waitFor(() => expect(fakeMaps).toHaveLength(2));
      const second = await theMap();
      expect(second).not.toBe(first);
      expect(second.options.startView).toMatchObject({
        cursor: [4, 0, 4],
        distance: 120,
        yaw: 15,
      });
      // Sol and its stars are on the map again, and nothing is flown.
      await waitFor(() => expect(second.systemCount()).toBe(51));
      expect(second.flights).toHaveLength(0);
      expect(second.getView()).toMatchObject({
        cursor: [4, 0, 4],
        distance: 120,
      });

      // Another system: the map flies to it, as ever.
      vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue({
        systems: [],
        truncated: false,
      });
      await fireEvent.click(
        await screen.findByRole('button', {
          name: /^Ranked 2\b/,
        }),
      );
      await waitFor(() =>
        expect(second.getSelection()?.id64).toBe(rankedRows[1].id64),
      );
      expect(second.flights).toHaveLength(1);
    });

    it('moves on to a result when one is selected, and asks for that system’s stars', async () => {
      const map = await chooseSol();
      await waitFor(() => expect(map.systemCount()).toBe(51));
      vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue({
        systems: [],
        truncated: false,
      });
      await fireEvent.click(
        await screen.findByRole('button', {
          name: /^Ranked 2\b/,
        }),
      );
      await waitFor(() =>
        expect(map.getSelection()?.id64).toBe(rankedRows[1].id64),
      );
      expect(map.flights.at(-1)!.target).toMatchObject({
        cursor: [20, 2, 40],
        distance: 70,
      });
      await waitFor(() =>
        expect(document.querySelector('.selection-status')).toHaveTextContent(
          'Ranked 2 and the 0 real stars within 15 ly of it.',
        ),
      );
      // Sol and its stars are gone: only the selected system is shown.
      expect(onTheMap(map)).toEqual([rankedRows[1].id64]);
      expect(map.spheres).toEqual([
        expect.objectContaining({ position: [20, 2, 40], radius: 15 }),
      ]);
      expect(api.getCatalogueViewportSystems).toHaveBeenLastCalledWith(
        expect.objectContaining({ minX: 5, maxX: 35, minZ: 25, maxZ: 55 }),
        expect.anything(),
      );
    });
  },
);
