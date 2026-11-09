import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { writable } from 'svelte/store';
import {
  addWatchlist,
  getWatchlist,
  removeWatchlist,
  type WatchlistEntry,
} from '$lib/api/client';
import { queryClient, queryKeys } from '$lib/api/query';
import { parseId64 } from '$lib/domain/id64';
import type { AuthState } from '$lib/auth/auth';
import type { PersistenceContext } from '$lib/persistence/context';
import { applicationStores } from '$lib/persistence/stores';
import {
  createPersistedStore,
  compareCodec,
  pinnedCodec,
  syncKeyCodec,
  PERSISTENCE_KEYS,
  SNAPSHOT_SOL_ID64,
  type PinnedEntry,
} from '$lib/persistence/storage';
import WatchlistTestHost from './WatchlistTestHost.svelte';
import InspectPageTestHost from '../../../routes/inspect/InspectPageTestHost.svelte';

const { mockedPage, mockedGoto, featureGate } = vi.hoisted(() => ({
  mockedPage: {
    url: new URL('http://localhost/explore'),
    state: {} as App.PageState,
  },
  mockedGoto: vi.fn(),
  featureGate: { enabled: false },
}));

vi.mock('$app/state', () => ({ page: mockedPage }));
vi.mock('$app/navigation', () => ({ goto: mockedGoto }));
vi.mock('$lib/config', () => ({
  get FINDER_F4_ENABLED() {
    return featureGate.enabled;
  },
}));

vi.mock('$lib/api/client', async (loadOriginal) => ({
  ...(await loadOriginal<typeof import('$lib/api/client')>()),
  getWatchlist: vi.fn(),
  addWatchlist: vi.fn(),
  removeWatchlist: vi.fn(),
  getSystem: vi.fn(() => new Promise(() => {})),
}));
vi.mock('$lib/persistence/context', async (loadOriginal) => ({
  ...(await loadOriginal<typeof import('$lib/persistence/context')>()),
  usePersistenceContext: () => services,
}));
vi.mock('$lib/auth/auth', () => ({
  auth: {
    subscribe: (run: (value: AuthState) => void) => session.subscribe(run),
  },
}));

let services: PersistenceContext;
const signedOut: AuthState = {
  loading: false,
  authenticated: false,
  user: null,
  ownerClaimAvailable: false,
  error: null,
};
const session = writable<AuthState>(signedOut);
const id64 = parseId64('18446744073709551615');
const entry: WatchlistEntry = {
  system_id64: id64,
  name: 'Far Reach',
  added_at: '2026-09-01T12:00:00Z',
  x: 3,
  y: 4,
  z: 0,
  population: 1200,
  is_colonised: true,
  archetype_score: 72,
  primary_archetype: 'industrial',
  secondary_archetype: 'refinery',
  economy_suggestion: 'Industrial',
  score: 65,
};
const getMock = vi.mocked(getWatchlist);
const addMock = vi.mocked(addWatchlist);
const removeMock = vi.mocked(removeWatchlist);

function f4Pin(id: string, fields: Partial<PinnedEntry> = {}): PinnedEntry {
  return {
    id64: parseId64(id),
    name: `Candidate ${id}`,
    pinned_at: '2026-09-01T12:00:00Z',
    score_kind: 'selected_fit',
    selected_archetype: 'flexible',
    ranking_score: 95,
    ranking_tier: 'B',
    overall_development_potential: 80,
    primary_archetype: 'flexible',
    secondary_archetype: 'old_custom_key',
    distance: 0,
    distance_reference: { kind: 'sol', id64: SNAPSHOT_SOL_ID64, name: 'Sol' },
    ranking_provenance: {
      ranking_version: 'ranking-v1',
      ranking_sha256: 'a'.repeat(64),
      archetype_version: 'archetype-v1',
      archetype_model_sha256: 'b'.repeat(64),
      derived_generation_id: 'generation-1',
      publication_sequence: 1,
      archetype_manifest_sha256: 'c'.repeat(64),
    },
    ...fields,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  queryClient.clear();
  localStorage.clear();
  session.set(signedOut);
  featureGate.enabled = false;
  mockedPage.url = new URL('http://localhost/explore');
  mockedPage.state = {};
  mockedGoto.mockImplementation(async (destination, options) => {
    mockedPage.url = new URL(destination, mockedPage.url);
    mockedPage.state = options.state;
  });
  services = {
    ...applicationStores,
    pins: createPersistedStore({
      key: PERSISTENCE_KEYS.pins,
      initial: () => [],
      codec: pinnedCodec,
    }),
    compare: createPersistedStore({
      key: PERSISTENCE_KEYS.compare,
      initial: () => [],
      codec: compareCodec,
    }),
    syncKey: createPersistedStore({
      key: PERSISTENCE_KEYS.syncKey,
      initial: () => ({
        state: { syncKey: 'commander-a-key-1234' },
        version: 0,
      }),
      codec: syncKeyCodec,
    }),
  };
  services.pins.hydrate();
  services.compare.hydrate();
  services.syncKey.hydrate();
  getMock.mockResolvedValue({
    sync_key: 'commander-a-key-1234',
    watchlist: [],
  });
  addMock.mockResolvedValue({ ok: true, sync_key: 'commander-a-key-1234' });
  removeMock.mockResolvedValue({ ok: true });
});
afterEach(() => {
  cleanup();
  queryClient.clear();
});

describe('saved systems panel', () => {
  it('labels F4 saved scores by mode, preserves API tiers and shows referenced zero distance', async () => {
    featureGate.enabled = true;
    services.pins.set([f4Pin('42')]);
    render(WatchlistTestHost);
    const table = await screen.findByRole('table', { name: 'Pinned systems' });
    expect(
      within(table).getByRole('columnheader', { name: 'Saved scores' }),
    ).toBeInTheDocument();
    const scores = within(table).getByText(/Best Colony Potential 80/);
    expect(scores).toHaveTextContent(
      'Flexible Multi-Role Colony fit 95 · Tier B',
    );
    expect(scores).not.toHaveTextContent(/Tier S/);
    expect(
      within(table).getByText('Flexible Multi-Role Colony'),
    ).toBeInTheDocument();
    expect(within(table).getByText('old custom key')).toBeInTheDocument();
    expect(within(table).getByText('0.00 LY')).toBeInTheDocument();
    expect(within(table).getByText('Distance from Sol')).toBeInTheDocument();
    expect(
      within(table).getByText(/Ranked on generation generation-1/),
    ).toHaveTextContent('publication 1');
    await fireEvent.click(
      within(table).getByRole('button', { name: 'Compare Candidate 42' }),
    );
    expect(services.compare.read().value[0]).toMatchObject({
      overall_development_potential: 80,
      ranking_score: 95,
      ranking_tier: 'B',
      selected_archetype: 'flexible',
      distance: 0,
      distance_reference: { kind: 'sol', id64: SNAPSHOT_SOL_ID64 },
    });
  });

  it('keeps pre-F4 pins readable with unknown reference and without guessed tier or score sorting', async () => {
    featureGate.enabled = true;
    const legacy = {
      id64,
      name: 'Older candidate',
      pinned_at: '2026-09-01T12:00:00Z',
      archetype_score: 99,
      primary_archetype: 'refinery_industrial',
      distance: 0,
    };
    localStorage.setItem(PERSISTENCE_KEYS.pins, JSON.stringify([legacy]));
    services.pins.hydrate();
    render(WatchlistTestHost);
    const table = await screen.findByRole('table', { name: 'Pinned systems' });
    expect(within(table).getByText(/Stored score 99/)).toHaveTextContent(
      'Older save',
    );
    expect(
      within(table).getByRole('columnheader', { name: 'Captured at' }),
    ).toBeInTheDocument();
    expect(within(table).getByText('2026-09-01 12:00:00 UTC')).toHaveAttribute(
      'datetime',
      '2026-09-01T12:00:00.000Z',
    );
    expect(within(table).getByText('refinery industrial')).toBeInTheDocument();
    expect(within(table).getByText('Reference unknown')).toBeInTheDocument();
    expect(within(table).queryByText('0.00 LY')).not.toBeInTheDocument();
    expect(within(table).queryByText(/Tier [SABCD]/)).not.toBeInTheDocument();
    expect(
      within(screen.getByRole('combobox', { name: 'Sort pins' })).getByRole(
        'option',
        { name: /Best Colony Potential/ },
      ),
    ).toBeDisabled();
    expect(JSON.parse(localStorage.getItem(PERSISTENCE_KEYS.pins)!)).toEqual([
      legacy,
    ]);
  });

  it('sorts F4 pins on overall potential across modes and generations, never selected-fit score', async () => {
    featureGate.enabled = true;
    const second = f4Pin('43', {
      score_kind: 'overall_potential',
      selected_archetype: null,
      ranking_score: 90,
      ranking_tier: 'A',
      overall_development_potential: 90,
    });
    second.ranking_provenance = {
      ...second.ranking_provenance!,
      derived_generation_id: 'generation-2',
      publication_sequence: 2,
      archetype_manifest_sha256: 'd'.repeat(64),
    };
    services.pins.set([f4Pin('42'), second]);
    render(WatchlistTestHost);
    const table = await screen.findByRole('table', { name: 'Pinned systems' });
    const sort = screen.getByRole('combobox', { name: 'Sort pins' });
    expect(
      within(sort).getByRole('option', { name: /Best Colony Potential/ }),
    ).not.toBeDisabled();
    await fireEvent.change(sort, { target: { value: 'development' } });
    expect(within(table).getAllByRole('rowheader')[0]).toHaveTextContent(
      'Candidate 43',
    );
    services.pins.set([f4Pin('42'), { ...second, ranking_provenance: null }]);
    await waitFor(() =>
      expect(
        within(sort).getByRole('option', { name: /Best Colony Potential/ }),
      ).toBeDisabled(),
    );
    expect(sort).toHaveValue('recent');
    expect(within(table).getAllByRole('rowheader')[0]).toHaveTextContent(
      'Candidate 42',
    );
  });

  it('treats server watchlist scores as unknown provenance in F4 and preserves that when saved', async () => {
    featureGate.enabled = true;
    getMock.mockResolvedValue({
      sync_key: 'commander-a-key-1234',
      watchlist: [{ ...entry, primary_archetype: 'flexible' }],
    });
    render(WatchlistTestHost);
    const table = await screen.findByRole('table', { name: 'Watched systems' });
    expect(within(table).getByText(/Stored score 72/)).toHaveTextContent(
      'Older save',
    );
    expect(
      within(table).getByText('Flexible Multi-Role Colony'),
    ).toBeInTheDocument();
    expect(within(table).queryByText(/Tier [SABCD]/)).not.toBeInTheDocument();
    expect(
      within(
        screen.getByRole('combobox', { name: 'Sort watchlist' }),
      ).getByRole('option', { name: /Best Colony Potential/ }),
    ).toBeDisabled();
    await fireEvent.click(
      within(table).getByRole('button', { name: 'Compare Far Reach' }),
    );
    expect(services.compare.read().value[0]).toMatchObject({
      archetype_score: 72,
      score_kind: 'unknown',
      distance_reference: null,
      ranking_provenance: null,
    });
  });

  it.each(['Pinned systems', 'Watched systems'])(
    'retains Finder query and anchor through the %s Inspect round trip',
    async (tableName) => {
      featureGate.enabled = true;
      const source =
        'archetype=flexible&min-tier=A&page_size=1&offset=2&selected=42&survey=outer';
      mockedPage.url = new URL(`http://localhost/explore?${source}`);
      const anchor = {
        id64: parseId64('128666762'),
        name: 'Achenar',
        x: 67.5,
        y: -12.25,
        z: 42,
      };
      mockedPage.state = { anchor };
      const outboundState = mockedPage.state;
      services.pins.set([
        { id64, name: entry.name, pinned_at: '2026-09-01T12:00:00Z' },
      ]);
      getMock.mockResolvedValue({
        sync_key: 'commander-a-key-1234',
        watchlist: [entry],
      });
      const mounted = render(WatchlistTestHost);
      const table = await screen.findByRole('table', { name: tableName });
      const link = within(table).getByRole('link', {
        name: 'Inspect Far Reach',
      });
      const destination = new URL(link.getAttribute('href')!, mockedPage.url);
      expect(destination.pathname).toBe('/inspect');
      expect(destination.searchParams.get('system')).toBe(id64);
      expect(destination.searchParams.get('from')).toBe(source);
      await fireEvent.click(link);
      expect(mockedGoto).toHaveBeenLastCalledWith(link.getAttribute('href'), {
        state: outboundState,
        replaceState: false,
        keepFocus: true,
        noScroll: true,
      });
      expect(mockedPage.state).toBe(outboundState);
      mounted.unmount();
      render(InspectPageTestHost);
      await fireEvent.click(
        screen.getByRole('link', { name: 'Open in Explore' }),
      );
      expect(mockedPage.url.pathname).toBe('/explore');
      expect(Object.fromEntries(mockedPage.url.searchParams)).toEqual({
        archetype: 'flexible',
        'min-tier': 'A',
        page_size: '1',
        offset: '2',
        survey: 'outer',
        selected: id64,
        system: id64,
      });
      expect(mockedPage.url.searchParams.getAll('selected')).toEqual([id64]);
      expect(mockedPage.url.searchParams.getAll('system')).toEqual([id64]);
      expect(mockedPage.state).toBe(outboundState);
      expect(mockedPage.state.anchor).toEqual(anchor);
    },
  );

  it('explains the independent empty lists and sync-key ownership', async () => {
    render(WatchlistTestHost);
    expect(
      await screen.findByText('No systems watched yet'),
    ).toBeInTheDocument();
    expect(screen.getByText('No pinned systems yet')).toBeInTheDocument();
    expect(
      screen.getByText(/Pins and comparison selections stay in this browser/),
    ).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith(
      'commander-a-key-1234',
      expect.any(AbortSignal),
    );
  });

  it('waits for credential hydration and reports loading without an empty flash', async () => {
    services.syncKey = createPersistedStore({
      key: PERSISTENCE_KEYS.syncKey,
      initial: () => ({
        state: { syncKey: 'commander-a-key-1234' },
        version: 0,
      }),
      codec: syncKeyCodec,
    });
    getMock.mockImplementation(() => new Promise(() => {}));
    render(WatchlistTestHost);
    expect(getMock).not.toHaveBeenCalled();
    expect(
      screen.queryByText('No systems watched yet'),
    ).not.toBeInTheDocument();
    services.syncKey.hydrate();
    expect(await screen.findByText('Loading watchlist…')).toBeInTheDocument();
    await waitFor(() => expect(getMock).toHaveBeenCalledOnce());
  });

  it('renders the V2 watchlist columns, safe inspect links, and comparison actions', async () => {
    getMock.mockResolvedValue({
      sync_key: 'commander-a-key-1234',
      watchlist: [entry],
    });
    render(WatchlistTestHost);
    const table = await screen.findByRole('table', { name: 'Watched systems' });
    for (const name of [
      'System',
      'Coords (LY)',
      'Population',
      'Development',
      'Archetype',
      'Added',
    ]) {
      expect(
        within(table).getByRole('columnheader', { name }),
      ).toBeInTheDocument();
    }
    expect(within(table).getByText('1,200')).toBeInTheDocument();
    expect(within(table).getByText('72')).toBeInTheDocument();
    expect(within(table).getByText('5.0 ly from Sol')).toBeInTheDocument();
    expect(
      within(table).getByRole('link', { name: 'Inspect Far Reach' }),
    ).toHaveAttribute('href', `/inspect?system=${id64}`);
    await fireEvent.click(
      within(table).getByRole('button', { name: 'Compare Far Reach' }),
    );
    expect(services.compare.read().value[0]).toMatchObject({
      id64,
      name: 'Far Reach',
      archetype_score: 72,
    });
    expect(
      within(table).getByRole('button', { name: 'Compare Far Reach' }),
    ).toHaveAttribute('aria-pressed', 'true');
    await fireEvent.click(
      within(table).getByRole('button', { name: 'Pin Far Reach' }),
    );
    expect(services.pins.read().value[0]).toMatchObject({
      id64,
      name: 'Far Reach',
    });
  });

  it('renders pin snapshots, adds them to the watchlist, removes them, and exports exact IDs', async () => {
    services.pins.set([
      {
        id64,
        name: 'Far Reach',
        pinned_at: '2026-09-01T12:00:00Z',
        x: 3,
        y: 4,
        z: 0,
        archetype_score: 72,
        primary_archetype: 'industrial',
        distance: 12.5,
      },
    ]);
    render(WatchlistTestHost);
    await screen.findByText('No systems watched yet');
    const table = screen.getByRole('table', { name: 'Pinned systems' });
    expect(
      within(table).getByRole('columnheader', { name: 'Dist. from ref.' }),
    ).toBeInTheDocument();
    expect(within(table).getByText('12.5 ly')).toBeInTheDocument();
    expect(within(table).getByRole('link', { name: 'Spansh' })).toHaveAttribute(
      'href',
      `https://spansh.co.uk/system/${id64}`,
    );
    const exportLink = screen.getByRole('link', {
      name: 'Export pins as JSON',
    });
    expect(
      JSON.parse(
        decodeURIComponent(exportLink.getAttribute('href')!.split(',')[1]),
      )[0].id64,
    ).toBe(id64);
    getMock.mockResolvedValue({
      sync_key: 'commander-a-key-1234',
      watchlist: [entry],
    });
    await fireEvent.click(
      within(table).getByRole('button', { name: 'Watch Far Reach' }),
    );
    await waitFor(() =>
      expect(addMock).toHaveBeenCalledWith('commander-a-key-1234', id64),
    );
    await screen.findByRole('table', { name: 'Watched systems' });
    await fireEvent.click(
      within(table).getByRole('button', { name: 'Unpin Far Reach' }),
    );
    expect(services.pins.read().value).toEqual([]);
  });

  it('removes a watched system and refreshes only its captured cache scope', async () => {
    getMock
      .mockResolvedValueOnce({
        sync_key: 'commander-a-key-1234',
        watchlist: [entry],
      })
      .mockResolvedValue({ sync_key: 'commander-a-key-1234', watchlist: [] });
    render(WatchlistTestHost);
    await fireEvent.click(
      await screen.findByRole('button', {
        name: 'Remove Far Reach from watchlist',
      }),
    );
    await waitFor(() =>
      expect(removeMock).toHaveBeenCalledWith('commander-a-key-1234', id64),
    );
    expect(
      await screen.findByText('No systems watched yet'),
    ).toBeInTheDocument();
    expect(
      queryClient.getQueryData(
        queryKeys.watchlist(null, 'commander-a-key-1234'),
      ),
    ).toMatchObject({ watchlist: [] });
  });

  it('treats legacy zero sentinels as unknown (coords, reference distance, inhabited population)', async () => {
    services.pins.set([
      {
        id64,
        name: 'Ghost',
        pinned_at: '2026-09-01T12:00:00Z',
        x: 0,
        y: 0,
        z: 0,
        archetype_score: 50,
        primary_archetype: 'industrial',
        distance: 0,
      },
    ]);
    getMock.mockResolvedValue({
      sync_key: 'commander-a-key-1234',
      watchlist: [{ ...entry, population: 0, is_colonised: true }],
    });
    render(WatchlistTestHost);
    const pins = await screen.findByRole('table', { name: 'Pinned systems' });
    // distance 0 is the "no reference" sentinel, not a measured 0.0 ly.
    expect(within(pins).queryByText('0.0 ly')).not.toBeInTheDocument();
    // (0,0,0) on a non-Sol system is unknown, not 0 ly from Sol.
    expect(within(pins).queryByText(/ly from Sol/)).not.toBeInTheDocument();
    // Zero population on an inhabited system is unknown, not literal zero.
    const watched = await screen.findByRole('table', {
      name: 'Watched systems',
    });
    expect(within(watched).getByText('Population unknown')).toBeInTheDocument();
  });

  it('shows a retryable error without exposing backend text or credentials', async () => {
    getMock
      .mockRejectedValueOnce(
        new Error('private commander-a-key-1234 /api/watchlist'),
      )
      .mockResolvedValue({
        sync_key: 'commander-a-key-1234',
        watchlist: [entry],
      });
    render(WatchlistTestHost);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Your watchlist could not be loaded. Try again.',
    );
    expect(
      screen.queryByText(/private commander-a-key-1234/),
    ).not.toBeInTheDocument();
    await fireEvent.click(
      screen.getByRole('button', { name: 'Retry watchlist' }),
    );
    expect(
      await screen.findByRole('table', { name: 'Watched systems' }),
    ).toBeInTheDocument();
  });

  it('switches account caches without showing the previous account list', async () => {
    getMock
      .mockResolvedValueOnce({
        sync_key: 'commander-a-key-1234',
        watchlist: [entry],
      })
      .mockResolvedValue({ sync_key: 'commander-a-key-1234', watchlist: [] });
    render(WatchlistTestHost);
    await screen.findByRole('table', { name: 'Watched systems' });
    session.set({
      ...signedOut,
      authenticated: true,
      user: {
        account_id: 'another-account',
        commander_name: null,
        is_owner: false,
      },
    });
    expect(
      await screen.findByText('No systems watched yet'),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('table', { name: 'Watched systems' }),
    ).not.toBeInTheDocument();
    expect(
      queryClient.getQueryData(
        queryKeys.watchlist('another-account', 'commander-a-key-1234'),
      ),
    ).toMatchObject({ watchlist: [] });
  });

  it('sorts watchlist rows by name, development, distance, and recently added', async () => {
    const second = {
      ...entry,
      system_id64: parseId64('42'),
      name: 'Achenar',
      archetype_score: 90,
      x: 12,
      y: 0,
      z: 0,
      added_at: '2026-09-02T12:00:00Z',
    };
    getMock.mockResolvedValue({
      sync_key: 'commander-a-key-1234',
      watchlist: [entry, second],
    });
    render(WatchlistTestHost);
    const table = await screen.findByRole('table', { name: 'Watched systems' });
    const firstRowName = () =>
      within(table).getAllByRole('rowheader')[0].textContent;
    expect(firstRowName()).toContain('Achenar');
    const sort = screen.getByRole('combobox', { name: 'Sort watchlist' });
    await fireEvent.change(sort, { target: { value: 'distance' } });
    expect(firstRowName()).toContain('Far Reach');
    await fireEvent.change(sort, { target: { value: 'name' } });
    expect(firstRowName()).toContain('Achenar');
    await fireEvent.change(sort, { target: { value: 'development' } });
    expect(firstRowName()).toContain('Achenar');
  });

  it('keeps saved rows visible after a refresh fails', async () => {
    getMock
      .mockResolvedValueOnce({
        sync_key: 'commander-a-key-1234',
        watchlist: [entry],
      })
      .mockRejectedValue(new Error('unavailable'));
    render(WatchlistTestHost);
    await screen.findByRole('table', { name: 'Watched systems' });
    await fireEvent.click(
      screen.getByRole('button', { name: 'Refresh watchlist' }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Your watchlist could not be loaded. Try again.',
    );
    expect(
      screen.getByRole('table', { name: 'Watched systems' }),
    ).toBeInTheDocument();
  });

  it('retries a failed mutation while retaining the watched system', async () => {
    getMock
      .mockResolvedValueOnce({
        sync_key: 'commander-a-key-1234',
        watchlist: [entry],
      })
      .mockResolvedValue({ sync_key: 'commander-a-key-1234', watchlist: [] });
    removeMock
      .mockRejectedValueOnce(new Error('private credential'))
      .mockResolvedValue({ ok: true });
    render(WatchlistTestHost);
    await fireEvent.click(
      await screen.findByRole('button', {
        name: 'Remove Far Reach from watchlist',
      }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Your watchlist could not be updated. Try again.',
    );
    expect(
      screen.getByRole('table', { name: 'Watched systems' }),
    ).toBeInTheDocument();
    await fireEvent.click(
      screen.getByRole('button', { name: 'Retry watchlist change' }),
    );
    expect(
      await screen.findByText('No systems watched yet'),
    ).toBeInTheDocument();
    expect(removeMock).toHaveBeenCalledTimes(2);
  });

  it('retries loading without repeating an earlier failed removal', async () => {
    getMock
      .mockResolvedValueOnce({
        sync_key: 'commander-a-key-1234',
        watchlist: [entry],
      })
      .mockRejectedValueOnce(new Error('load failed'))
      .mockResolvedValue({
        sync_key: 'commander-a-key-1234',
        watchlist: [entry],
      });
    removeMock.mockRejectedValue(new Error('remove failed'));
    render(WatchlistTestHost);
    await fireEvent.click(
      await screen.findByRole('button', {
        name: 'Remove Far Reach from watchlist',
      }),
    );
    await screen.findByRole('button', { name: 'Retry watchlist change' });
    await fireEvent.click(
      screen.getByRole('button', { name: 'Refresh watchlist' }),
    );
    await fireEvent.click(
      await screen.findByRole('button', {
        name: 'Retry watchlist',
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByText('Your watchlist could not be loaded. Try again.'),
      ).not.toBeInTheDocument(),
    );
    expect(removeMock).toHaveBeenCalledOnce();
  });

  it('does not expose an old mutation error after switching the sync key', async () => {
    let rejectRemove!: (error: Error) => void;
    removeMock.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectRemove = reject;
        }),
    );
    getMock
      .mockResolvedValueOnce({
        sync_key: 'commander-a-key-1234',
        watchlist: [entry],
      })
      .mockResolvedValue({ sync_key: 'commander-b-key-1234', watchlist: [] });
    render(WatchlistTestHost);
    await fireEvent.click(
      await screen.findByRole('button', {
        name: 'Remove Far Reach from watchlist',
      }),
    );
    await waitFor(() => expect(removeMock).toHaveBeenCalledOnce());
    services.syncKey.set({
      state: { syncKey: 'commander-b-key-1234' },
      version: 0,
    });
    await screen.findByText('No systems watched yet');
    rejectRemove(new Error('late private failure'));
    await waitFor(() => expect(queryClient.isMutating()).toBe(0));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(
      queryClient.getQueryData(
        queryKeys.watchlist(null, 'commander-b-key-1234'),
      ),
    ).toMatchObject({ watchlist: [] });
  });

  it('retains the original credential when an inactive cached query is refetched', async () => {
    getMock.mockImplementation(async (key) => ({
      sync_key: key,
      watchlist: [],
    }));
    render(WatchlistTestHost);
    await screen.findByText('No systems watched yet');
    services.syncKey.set({
      state: { syncKey: 'commander-b-key-1234' },
      version: 0,
    });
    await waitFor(() =>
      expect(getMock).toHaveBeenCalledWith(
        'commander-b-key-1234',
        expect.any(AbortSignal),
      ),
    );
    await queryClient.refetchQueries({
      queryKey: queryKeys.watchlist(null, 'commander-a-key-1234'),
      exact: true,
      type: 'inactive',
    });
    expect(getMock).toHaveBeenLastCalledWith(
      'commander-a-key-1234',
      expect.any(AbortSignal),
    );
    expect(
      queryClient.getQueryData(
        queryKeys.watchlist(null, 'commander-a-key-1234'),
      ),
    ).toMatchObject({ sync_key: 'commander-a-key-1234' });
  });

  it('waits for the session bootstrap before requesting an account-scoped list', async () => {
    session.set({ ...signedOut, loading: true });
    render(WatchlistTestHost);
    expect(getMock).not.toHaveBeenCalled();
    expect(
      screen.getByRole('button', { name: 'Refresh watchlist' }),
    ).toBeDisabled();
    session.set({
      ...signedOut,
      authenticated: true,
      user: { account_id: 'captain', commander_name: null, is_owner: false },
    });
    await screen.findByText('No systems watched yet');
    expect(
      queryClient.getQueryData(
        queryKeys.watchlist('captain', 'commander-a-key-1234'),
      ),
    ).toMatchObject({ watchlist: [] });
  });

  it('confirms clearing local pins without removing the server watchlist', async () => {
    services.pins.set([
      { id64, name: 'Far Reach', pinned_at: '2026-09-01T12:00:00Z' },
    ]);
    render(WatchlistTestHost);
    await screen.findByText('No systems watched yet');
    await fireEvent.click(
      screen.getByRole('button', { name: 'Clear all pins' }),
    );
    expect(services.pins.read().value).toHaveLength(1);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Confirm clear pins' }),
    );
    expect(services.pins.read().value).toEqual([]);
    expect(removeMock).not.toHaveBeenCalled();
  });

  it('reports pins that could not be restored without exposing stored content', async () => {
    localStorage.setItem(PERSISTENCE_KEYS.pins, 'corrupt private snapshot');
    services.pins = createPersistedStore({
      key: PERSISTENCE_KEYS.pins,
      initial: () => [],
      codec: pinnedCodec,
    });
    services.pins.hydrate();
    render(WatchlistTestHost);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Your pins could not be saved or restored in this browser.',
    );
    expect(
      screen.queryByText(/corrupt private snapshot/),
    ).not.toBeInTheDocument();
  });
});
