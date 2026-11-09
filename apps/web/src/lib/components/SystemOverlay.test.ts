import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Id64 } from '$lib/domain/id64';
import {
  getAuthSession,
  getCatalogueViewportSystems,
  getMapHeatmap,
  getSystem,
  getSystemRatings,
  searchExploreSystems,
  systemDetailFromV3,
} from '$lib/api/client';
import { referenceSystemRatings, v3System } from '$lib/api/v3-system-fixtures';
import { queryClient } from '$lib/api/query';
import ExploreWorkspaceTestHost from '$lib/features/explore/ExploreWorkspaceTestHost.svelte';
import { navigateWithAnchor } from '$lib/features/explore/navigation';
import SystemOverlayTestHost from './SystemOverlayTestHost.svelte';

const { mockedPage, mockedGoto, featureGate } = vi.hoisted(() => ({
  mockedPage: {
    url: new URL('http://localhost/explore?system=42&view=cards#section'),
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
    getSystem: vi.fn(),
    getSystemRatings: vi.fn(),
    getSystemRatingExplanation: vi.fn(),
    getAuthSession: vi.fn(),
    getCatalogueViewportSystems: vi.fn(),
    getMapHeatmap: vi.fn(),
    searchExploreSystems: vi.fn(),
  };
});
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

const mockedGetSystem = vi.mocked(getSystem);

beforeEach(() => {
  vi.clearAllMocks();
  queryClient.clear();
  localStorage.clear();
  featureGate.enabled = false;
  mockedGoto.mockImplementation(async () => undefined);
  Object.defineProperties(mockedPage, {
    url: {
      configurable: true,
      writable: true,
      value: new URL('http://localhost/explore?system=42&view=cards#section'),
    },
    state: { configurable: true, writable: true, value: {} },
  });
  vi.mocked(getSystemRatings).mockResolvedValue(referenceSystemRatings());
  mockedGetSystem.mockResolvedValue(
    systemDetailFromV3(
      v3System({ id64: '42', name: 'Test System' }),
      [],
      [],
      [],
    ),
  );
  document.body.style.overflow = '';
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.body.style.overflow = '';
});

describe('SystemOverlay', () => {
  it('moves focus into the dialog and contains forward and reverse Tab', async () => {
    render(SystemOverlayTestHost, { props: { id64: '42' as Id64 } });

    const dialog = screen.getByRole('dialog', { name: 'System Detail' });
    const close = screen.getByRole('button', { name: 'Close system detail' });
    const finalControl = document.createElement('button');
    finalControl.textContent = 'Last dialog action';
    dialog.append(finalControl);
    await waitFor(() => expect(dialog).toHaveFocus());
    expect(document.body.style.overflow).toBe('hidden');

    await fireEvent.keyDown(window, { key: 'Tab' });
    expect(close).toHaveFocus();

    finalControl.focus();
    await fireEvent.keyDown(window, { key: 'Tab' });
    expect(close).toHaveFocus();

    close.focus();
    await fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(finalControl).toHaveFocus();
  });

  it('closes on Escape, preserves the host URL, and restores prior focus', async () => {
    const opener = document.createElement('button');
    opener.textContent = 'Open detail';
    document.body.append(opener);
    opener.focus();
    const focusOpener = vi.spyOn(opener, 'focus');

    const { unmount } = render(SystemOverlayTestHost, {
      props: { id64: '42' as Id64 },
    });
    await waitFor(() =>
      expect(
        screen.getByRole('dialog', { name: 'System Detail' }),
      ).toHaveFocus(),
    );

    await fireEvent.keyDown(window, { key: 'Escape' });

    expect(mockedGoto).toHaveBeenCalledWith('/explore?view=cards#section', {
      replaceState: true,
      noScroll: true,
    });
    await waitFor(() => expect(opener).toHaveFocus());
    expect(focusOpener).toHaveBeenCalledOnce();
    expect(focusOpener).toHaveBeenCalledWith({ preventScroll: true });

    unmount();
    expect(focusOpener).toHaveBeenCalledOnce();
    opener.remove();
  });

  it('closes only Sources & freshness on the first Escape inside Inspect', async () => {
    render(SystemOverlayTestHost, { props: { id64: '42' as Id64 } });
    const overlay = screen.getByRole('dialog', { name: 'System Detail' });
    const trigger = await screen.findByRole('button', {
      name: 'Sources & freshness',
    });
    trigger.focus();
    await fireEvent.click(trigger);
    const sources = screen.getByRole('dialog', { name: 'Sources & freshness' });
    expect(sources).toHaveFocus();

    await fireEvent.keyDown(sources, { key: 'Escape' });

    expect(
      screen.queryByRole('dialog', { name: 'Sources & freshness' }),
    ).toBeNull();
    expect(overlay).toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(mockedGoto).not.toHaveBeenCalled();

    await fireEvent.keyDown(trigger, { key: 'Escape' });

    expect(mockedGoto).toHaveBeenCalledOnce();
    expect(mockedGoto).toHaveBeenCalledWith('/explore?view=cards#section', {
      replaceState: true,
      noScroll: true,
    });
  });

  it('restores body scroll and prior focus when host navigation unmounts it', async () => {
    document.body.style.overflow = 'scroll';
    const opener = document.createElement('button');
    opener.textContent = 'Navigate to detail';
    document.body.append(opener);
    opener.focus();

    const { unmount } = render(SystemOverlayTestHost, {
      props: { id64: '42' as Id64 },
    });
    await waitFor(() =>
      expect(
        screen.getByRole('dialog', { name: 'System Detail' }),
      ).toHaveFocus(),
    );
    expect(document.body.style.overflow).toBe('hidden');

    unmount();

    expect(document.body.style.overflow).toBe('scroll');
    expect(opener).toHaveFocus();
    opener.remove();
  });

  it('keeps the default-off canonical Inspect link unchanged', () => {
    render(SystemOverlayTestHost, { props: { id64: '42' as Id64 } });
    expect(
      screen.getByRole('link', { name: 'Open canonical Inspect' }),
    ).toHaveAttribute('href', '/inspect?system=42');
  });

  it('carries the originating query and anchor into canonical Inspect when enabled', async () => {
    featureGate.enabled = true;
    mockedPage.url = new URL(
      'http://localhost/explore?archetype=manufacturing_hub&min-tier=A&page_size=1&offset=2&selected=24&system=42',
    );
    mockedPage.state = {
      anchor: {
        id64: '24' as Id64,
        name: 'Anchor A',
        x: 11,
        y: 22,
        z: 33,
      },
    };
    render(SystemOverlayTestHost, { props: { id64: '42' as Id64 } });
    const link = screen.getByRole('link', { name: 'Open canonical Inspect' });
    const destination = new URL(link.getAttribute('href')!, mockedPage.url);
    expect(destination.pathname).toBe('/inspect');
    expect(destination.searchParams.get('system')).toBe('42');
    expect(destination.searchParams.get('from')).toBe(
      mockedPage.url.searchParams.toString(),
    );

    await fireEvent.click(link);

    expect(mockedGoto).toHaveBeenCalledWith(
      `${destination.pathname}${destination.search}`,
      {
        replaceState: false,
        state: mockedPage.state,
        keepFocus: true,
        noScroll: true,
      },
    );
  });

  it('preserves the anchor, coordinates and 500-LY request through overlay open and close', async () => {
    featureGate.enabled = true;
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe = vi.fn();
        disconnect = vi.fn();
        unobserve = vi.fn();
      },
    );
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    vi.mocked(getAuthSession).mockResolvedValue({
      authenticated: false,
      user: null,
    });
    vi.mocked(getCatalogueViewportSystems).mockResolvedValue({
      systems: [],
      truncated: false,
    });
    vi.mocked(getMapHeatmap).mockResolvedValue({ source: 'legacy-fallback' });
    vi.mocked(searchExploreSystems).mockResolvedValue({
      results: [],
      page: {
        page_size: 50,
        count: 0,
        total: 0,
        navigable_total: 0,
        offset: 0,
        navigation_limit: 10_000,
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
    const anchor = {
      id64: '24' as Id64,
      name: 'Anchor A',
      x: 11,
      y: 22,
      z: 33,
    };
    const state = { anchor, retainedContext: 'keep' };
    mockedGoto.mockImplementation(
      async (destination: string | URL, options = {}) => {
        mockedPage.url = new URL(destination, mockedPage.url);
        mockedPage.state = options.state ?? {};
      },
    );
    render(ExploreWorkspaceTestHost, {
      initialPage: {
        url: new URL('http://localhost/explore?selected=24&view=cards#section'),
        state,
      },
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
    const anchoredRequest = expect.objectContaining({
      reference_coords: { x: 11, y: 22, z: 33 },
      filters: { distance: { min: 0, max: 500 }, economy: 'any' },
      sort_by: 'distance',
    });
    await waitFor(() =>
      expect(searchExploreSystems).toHaveBeenCalledWith(
        anchoredRequest,
        expect.anything(),
        { id64: anchor.id64, name: anchor.name },
        50,
      ),
    );

    const opened = new URL(mockedPage.url);
    opened.searchParams.set('system', '42');
    await navigateWithAnchor(
      `${opened.pathname}${opened.search}${opened.hash}`,
    );
    const overlay = render(SystemOverlayTestHost, {
      props: { id64: '42' as Id64 },
    });
    expect(
      screen.getByRole('dialog', { name: 'System Detail' }),
    ).toBeInTheDocument();
    await fireEvent.click(
      screen.getByRole('button', { name: 'Close system detail' }),
    );
    await waitFor(() =>
      expect(mockedPage.url.searchParams.has('system')).toBe(false),
    );
    overlay.unmount();

    expect(mockedGoto).toHaveBeenLastCalledWith(
      '/explore?selected=24&view=cards&near=24#section',
      {
        replaceState: true,
        state: { ...state, finderRequested: false },
        keepFocus: true,
        noScroll: true,
      },
    );
    expect(mockedPage.state).toEqual({ ...state, finderRequested: false });
    expect(mockedPage.url.searchParams.get('selected')).toBe('24');
    expect(mockedPage.url.searchParams.get('near')).toBe('24');
    expect(screen.getByRole('combobox', { name: 'System name' })).toHaveValue(
      'Anchor A',
    );
    expect(
      screen.getByRole('button', { name: 'Clear anchor Anchor A' }),
    ).toBeInTheDocument();

    // Re-fetch after close so history hydration must reconstruct the same query.
    vi.mocked(searchExploreSystems).mockClear();
    await queryClient.invalidateQueries();
    await waitFor(() =>
      expect(searchExploreSystems).toHaveBeenCalledWith(
        anchoredRequest,
        expect.anything(),
        { id64: anchor.id64, name: anchor.name },
        50,
      ),
    );
    expect(
      vi
        .mocked(searchExploreSystems)
        .mock.calls.every(
          ([request]) =>
            !request.galaxy_wide && request.filters?.distance?.max === 500,
        ),
    ).toBe(true);
  });
});
