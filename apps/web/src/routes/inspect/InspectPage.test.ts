import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '$lib/api/client';
import { queryClient } from '$lib/api/query';
import {
  referenceSystemDetail,
  referenceSystemRatings,
} from '$lib/api/v3-system-fixtures';
import { parseId64 } from '$lib/domain/id64';
import ExploreWorkspaceTestHost from '$lib/features/explore/ExploreWorkspaceTestHost.svelte';
import {
  inspectHref,
  navigateWithAnchor,
  type ExploreAnchor,
} from '$lib/features/explore/navigation';
import InspectPageTestHost from './InspectPageTestHost.svelte';

const { mockedPage, mockedGoto, mockedReplaceState, featureGate } = vi.hoisted(
  () => ({
    mockedPage: {
      url: new URL('http://localhost/inspect'),
      state: {} as App.PageState,
    },
    mockedGoto: vi.fn(),
    mockedReplaceState: vi.fn(),
    featureGate: { enabled: false },
  }),
);

vi.mock('$app/state', () => ({ page: mockedPage }));
/** What the browser's address bar shows after the page's last replaceState. */
let addressBar: URL | null = null;
vi.mock('$app/navigation', () => ({
  goto: mockedGoto,
  replaceState: mockedReplaceState,
}));
vi.mock('$lib/config', () => ({
  get FINDER_F4_ENABLED() {
    return featureGate.enabled;
  },
}));
vi.mock('$lib/api/client', async (loadOriginal) => ({
  ...(await loadOriginal<typeof import('$lib/api/client')>()),
  getSystem: vi.fn(),
  getSystemRatings: vi.fn(),
  autocompleteSystems: vi.fn(),
  searchExploreSystems: vi.fn(),
  getArchetypeRankings: vi.fn(),
  getCatalogueViewportSystems: vi.fn(),
}));
vi.mock('$lib/spatial/babylon/adapter', () => ({
  createBabylonSpatialRuntime: () => ({
    getStatus: () => ({ state: 'created' }),
    start: async () => ({ state: 'ready', backend: 'WEBGL2' }),
    dispatch: () => ({ status: 'executed' }),
    subscribe: () => () => {},
    resize: () => {},
    dispose: () => {},
  }),
}));

const inspectedId = parseId64('18446744073709551615');
const originalId = parseId64('9007199254740993');
const anchor: ExploreAnchor = {
  id64: parseId64('128666762'),
  name: 'Achenar',
  x: 67.5,
  y: -12.25,
  z: 42,
};

function arriveFrom(source: string, state: App.PageState = {}): void {
  mockedPage.url = new URL(
    inspectHref(inspectedId, new URL(`http://localhost/explore?${source}`)),
    'http://localhost',
  );
  mockedPage.state = state;
}

function renderExplore(): void {
  render(ExploreWorkspaceTestHost, {
    initialPage: { url: mockedPage.url, state: mockedPage.state },
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
}

function renderInspect() {
  return render(InspectPageTestHost, {
    initialPage: { url: mockedPage.url, state: mockedPage.state },
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
}

beforeEach(() => {
  vi.clearAllMocks();
  queryClient.clear();
  featureGate.enabled = false;
  Object.defineProperties(mockedPage, {
    url: {
      configurable: true,
      writable: true,
      value: new URL(`http://localhost/inspect?system=${inspectedId}`),
    },
    state: { configurable: true, writable: true, value: {} },
  });
  mockedGoto.mockImplementation(async (destination, options) => {
    mockedPage.url = new URL(destination, mockedPage.url);
    mockedPage.state = options.state;
  });
  addressBar = null;
  mockedReplaceState.mockImplementation((destination, state) => {
    // As SvelteKit's shallow routing does: the address bar and page.state
    // change, page.url does not.
    addressBar = new URL(destination, mockedPage.url);
    mockedPage.state = state;
  });
  // Keep detail mounted under its real QueryClientProvider without starting a map.
  vi.mocked(api.getSystem).mockImplementation(() => new Promise(() => {}));
  vi.mocked(api.getSystemRatings).mockResolvedValue(referenceSystemRatings());
  vi.mocked(api.searchExploreSystems).mockImplementation(
    () => new Promise(() => {}),
  );
  vi.mocked(api.getArchetypeRankings).mockImplementation(
    () => new Promise(() => {}),
  );
  vi.mocked(api.autocompleteSystems).mockResolvedValue({ results: [] });
  vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue({
    systems: [],
    truncated: false,
  });
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe = vi.fn();
      disconnect = vi.fn();
      unobserve = vi.fn();
    },
  );
});

afterEach(() => {
  cleanup();
  queryClient.clear();
  vi.unstubAllGlobals();
});

describe('Inspect tab URL state', () => {
  beforeEach(() => {
    vi.mocked(api.getSystem).mockResolvedValue(
      referenceSystemDetail({ id64: inspectedId }),
    );
  });

  it.each([
    ['overview', 'Overview'],
    ['bodies', 'Bodies'],
    ['stations', 'Stations'],
  ])('restores the %s task from a direct link', async (value, name) => {
    mockedPage.url.searchParams.set('tab', value);
    renderInspect();
    expect(await screen.findByRole('tab', { name })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByRole('tabpanel', { name })).toBeVisible();
    expect(mockedReplaceState).not.toHaveBeenCalled();
    expect(mockedGoto).not.toHaveBeenCalled();
  });

  it.each([
    ['bodies', true],
    ['overview', false],
    ['stations', false],
  ] as const)(
    'gives the System Map the whole window width on the Bodies task only (%s)',
    async (value, wide) => {
      mockedPage.url.searchParams.set('tab', value);
      const { container } = renderInspect();
      await screen.findByRole('tablist', { name: 'Inspect sections' });
      expect(
        container
          .querySelector('main.inspect-page')
          ?.classList.contains('inspect-wide'),
      ).toBe(wide);
    },
  );

  it('widens the page as soon as Bodies is chosen, and narrows it again for Overview', async () => {
    const { container } = renderInspect();
    const main = () => container.querySelector('main.inspect-page')!;
    await fireEvent.click(await screen.findByRole('tab', { name: 'Bodies' }));
    // page.url is unchanged by the shallow replaceState; the page widens all
    // the same (live, it stayed narrow until a reload).
    expect(mockedPage.url.searchParams.get('tab')).toBeNull();
    expect(addressBar!.searchParams.get('tab')).toBe('bodies');
    await waitFor(() => expect(main()).toHaveClass('inspect-wide'));
    await fireEvent.click(screen.getByRole('tab', { name: 'Overview' }));
    await waitFor(() => expect(main()).not.toHaveClass('inspect-wide'));
  });

  it.each([null, '', 'unknown', 'BODIES', 'bodies/stations', 'x'.repeat(1024)])(
    'uses Overview for an absent or invalid tab %s',
    async (value) => {
      if (value !== null) mockedPage.url.searchParams.set('tab', value);
      renderInspect();
      expect(
        await screen.findByRole('tab', { name: 'Overview' }),
      ).toHaveAttribute('aria-selected', 'true');
      expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
      expect(screen.getByRole('tabpanel', { name: 'Overview' })).toBeVisible();
      expect(mockedReplaceState).not.toHaveBeenCalled();
    },
  );

  it('round-trips tab choices on reload while preserving the system, originating URL and navigation state', async () => {
    featureGate.enabled = true;
    arriveFrom('archetype=flexible&min-tier=A&page_size=1&campaign=survey', {
      anchor,
    });
    mockedPage.url.searchParams.set('campaign', 'inspect');
    mockedPage.url.hash = '#details';
    const originalFrom = mockedPage.url.searchParams.get('from');
    const mounted = renderInspect();
    const originalState = mockedPage.state;
    await fireEvent.click(await screen.findByRole('tab', { name: 'Bodies' }));
    expect(addressBar!.searchParams.get('tab')).toBe('bodies');
    await fireEvent.click(screen.getByRole('tab', { name: 'Stations' }));
    expect(addressBar!.searchParams.getAll('tab')).toEqual(['stations']);
    expect(addressBar!.searchParams.get('system')).toBe(inspectedId);
    expect(addressBar!.searchParams.get('from')).toBe(originalFrom);
    expect(addressBar!.searchParams.get('campaign')).toBe('inspect');
    expect(addressBar!.hash).toBe('#details');
    expect(mockedPage.state).toBe(originalState);
    expect(mockedReplaceState).toHaveBeenCalledTimes(2);
    expect(mockedGoto).not.toHaveBeenCalled();
    expect(api.getSystem).toHaveBeenCalledTimes(1);
    expect(api.getSystemRatings).toHaveBeenCalledTimes(1);

    // A reload reads the address bar.
    mounted.unmount();
    mockedPage.url = addressBar!;
    renderInspect();
    expect(
      await screen.findByRole('tab', { name: 'Stations' }),
    ).toHaveAttribute('aria-selected', 'true');
    await fireEvent.click(screen.getByRole('tab', { name: 'Overview' }));
    expect(addressBar!.searchParams.get('tab') ?? 'overview').toBe('overview');
    expect(screen.getByRole('tabpanel', { name: 'Overview' })).toBeVisible();
    expect(api.getSystem).toHaveBeenCalledTimes(1);
    expect(api.getSystemRatings).toHaveBeenCalledTimes(1);
  });

  it('responds to a history URL update and evacuates keyboard focus from the hidden task', async () => {
    mockedPage.url.searchParams.set('tab', 'bodies');
    renderInspect();
    const body = await screen.findByRole('button', {
      name: 'Reference System 3 Planet',
    });
    await waitFor(() =>
      expect(
        screen.getByRole('heading', { name: 'Reference System' }),
      ).toHaveFocus(),
    );
    body.focus();
    const destination = new URL(mockedPage.url);
    destination.searchParams.set('tab', 'stations');
    mockedPage.url = destination;
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Stations' })).toHaveAttribute(
        'aria-selected',
        'true',
      ),
    );
    expect(screen.getByRole('tab', { name: 'Stations' })).toHaveFocus();
    expect(body.closest('[role="tabpanel"]')).toHaveAttribute('hidden');
    expect(mockedReplaceState).not.toHaveBeenCalled();
    expect(api.getSystem).toHaveBeenCalledTimes(1);
    expect(api.getSystemRatings).toHaveBeenCalledTimes(1);
  });
});

describe('Inspect Explore navigation', () => {
  it('places the action row above the system card', async () => {
    render(InspectPageTestHost);
    const actions = screen.getByRole('navigation', { name: 'System actions' });
    const card = await waitFor(() => {
      const found = document.querySelector('[data-system-id64]');
      expect(found).not.toBeNull();
      return found as Element;
    });
    // Back to Explore leads the row; the forward actions follow it.
    expect(
      [...actions.querySelectorAll('a')].map((link) =>
        link.textContent?.trim(),
      ),
    ).toEqual(['Back to Explore', 'Open in Explore', 'Continue to Plan']);
    expect(
      actions.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('keeps the default-off links unchanged even with originating Finder context', () => {
    arriveFrom('archetype=flexible&min-tier=A&page_size=1&offset=2', {
      anchor,
    });
    render(InspectPageTestHost);
    expect(
      screen.getByRole('link', { name: 'Back to Explore' }),
    ).toHaveAttribute('href', '/explore');
    expect(
      screen.getByRole('link', { name: 'Open in Explore' }),
    ).toHaveAttribute('href', `/explore?system=${inspectedId}`);
    expect(mockedGoto).not.toHaveBeenCalled();
  });

  it('Search near this system keeps the search, centred on and selecting this system', async () => {
    featureGate.enabled = true;
    arriveFrom(
      `archetype=flexible&min-tier=A&page_size=1&offset=2&near=${originalId}&selected=${originalId}&selected=42&system=43&system=44&campaign=survey`,
      { anchor },
    );
    render(InspectPageTestHost);
    await fireEvent.click(
      screen.getByRole('link', { name: 'Search near this system' }),
    );
    expect(mockedPage.url.pathname).toBe('/explore');
    expect(mockedPage.url.searchParams.getAll('near')).toEqual([inspectedId]);
    expect(mockedPage.url.searchParams.getAll('selected')).toEqual([
      inspectedId,
    ]);
    // A new search: from its first page, with no detail overlay open.
    expect(mockedPage.url.searchParams.has('offset')).toBe(false);
    expect(mockedPage.url.searchParams.has('system')).toBe(false);
    expect(Object.fromEntries(mockedPage.url.searchParams)).toMatchObject({
      archetype: 'flexible',
      'min-tier': 'A',
      page_size: '1',
      campaign: 'survey',
    });
    expect(mockedGoto).toHaveBeenCalledWith(expect.any(String), {
      state: expect.anything(),
      replaceState: false,
      keepFocus: true,
      noScroll: true,
    });
  });

  it('hands Explore the position Inspect already has, so the system is not looked up again', async () => {
    featureGate.enabled = true;
    const detail = referenceSystemDetail({ id64: inspectedId });
    vi.mocked(api.getSystem).mockResolvedValue(detail);
    arriveFrom('archetype=flexible', { anchor });
    render(InspectPageTestHost);
    await screen.findByRole('heading', { name: detail.name, level: 1 });
    await fireEvent.click(
      screen.getByRole('link', { name: 'Search near this system' }),
    );
    expect(mockedPage.state.anchor).toEqual({
      id64: inspectedId,
      name: detail.name,
      x: detail.x,
      y: detail.y,
      z: detail.z,
    });
  });

  it('opened with no search to go back to, offers Explore and a search near the system', () => {
    featureGate.enabled = true;
    render(InspectPageTestHost);
    expect(
      screen.getByRole('link', { name: 'Back to Explore' }),
    ).toHaveAttribute('href', '/explore');
    expect(
      screen.getByRole('link', { name: 'Search near this system' }),
    ).toHaveAttribute(
      'href',
      `/explore?near=${inspectedId}&selected=${inspectedId}`,
    );
  });

  it.each(['Back to Explore'])(
    'uses the enabled bare-link fallback for %s',
    async (action) => {
      featureGate.enabled = true;
      const mounted = render(InspectPageTestHost);
      const href = '/explore';
      expect(screen.getByRole('link', { name: action })).toHaveAttribute(
        'href',
        href,
      );
      await fireEvent.click(screen.getByRole('link', { name: action }));
      expect(mockedPage.url.href).toBe(`http://localhost${href}`);
      expect(mockedPage.state).toEqual({});
      mounted.unmount();
      renderExplore();
      expect(
        await screen.findByText(
          'Search for a system above to see what is near it.',
        ),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole('list', { name: 'System results' }),
      ).not.toBeInTheDocument();
      expect(api.searchExploreSystems).not.toHaveBeenCalled();
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    },
  );

  it('Back to results restores the floor, page and anchor without selecting the inspected system', async () => {
    featureGate.enabled = true;
    const source = 'archetype=flexible&min-tier=A&page_size=1&offset=2';
    mockedPage.url = new URL(`http://localhost/explore?${source}`);
    mockedPage.state = { anchor };
    const originalState = mockedPage.state;
    await navigateWithAnchor(inspectHref(inspectedId, mockedPage.url));
    expect(mockedPage.state).toBe(originalState);
    const mounted = render(InspectPageTestHost);
    await fireEvent.click(
      screen.getByRole('link', { name: 'Back to results' }),
    );
    expect(mockedPage.url.searchParams.toString()).toBe(source);
    expect(mockedPage.url.searchParams.has('selected')).toBe(false);
    expect(mockedPage.url.searchParams.has('system')).toBe(false);
    expect(mockedPage.state).toBe(originalState);
    mounted.unmount();
    renderExplore();
    // With the anchor back, the type is searched near it, on the same page.
    await waitFor(() =>
      expect(api.searchExploreSystems).toHaveBeenCalledWith(
        {
          reference_coords: { x: 67.5, y: -12.25, z: 42 },
          filters: { distance: { min: 0, max: 500 }, economy: 'any' },
          sort_by: 'distance',
          size: 1,
          from: 2,
          reach_ly: 250,
          archetype: 'flexible',
          min_score: 76,
        },
        expect.any(AbortSignal),
        { id64: anchor.id64, name: anchor.name },
        1,
      ),
    );
    expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    expect(
      screen.getByRole('combobox', { name: 'Colony archetype' }),
    ).toHaveValue('flexible');
    expect(screen.getByRole('combobox', { name: 'Show only' })).toHaveValue(
      'A',
    );
    expect(screen.getByRole('combobox', { name: 'System name' })).toHaveValue(
      'Achenar',
    );
    await fireEvent.change(
      screen.getByRole('combobox', { name: 'Colony archetype' }),
      { target: { value: 'any' } },
    );
    await waitFor(() =>
      expect(api.searchExploreSystems).toHaveBeenCalledWith(
        {
          reference_coords: { x: 67.5, y: -12.25, z: 42 },
          filters: { distance: { min: 0, max: 500 }, economy: 'any' },
          sort_by: 'distance',
          size: 1,
          from: 0,
          reach_ly: 250,
          // Any near a system shows tier C or better unless told otherwise.
          min_development_score: 45,
        },
        expect.any(AbortSignal),
        { id64: anchor.id64, name: anchor.name },
        1,
      ),
    );
    expect(mockedPage.url.searchParams.has('selected')).toBe(false);
  });

  it('Back replays a prior passive selection without replacing it with the inspected system', async () => {
    featureGate.enabled = true;
    const source = `page_size=1&offset=2&selected=${originalId}`;
    arriveFrom(source, { anchor });
    render(InspectPageTestHost);
    await fireEvent.click(
      screen.getByRole('link', { name: 'Back to results' }),
    );
    expect(mockedPage.url.searchParams.toString()).toBe(source);
    expect(mockedPage.url.searchParams.getAll('selected')).toEqual([
      originalId,
    ]);
    expect(mockedPage.url.searchParams.has('system')).toBe(false);
  });
});
