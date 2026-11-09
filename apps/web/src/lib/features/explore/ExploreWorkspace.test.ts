import {
  cleanup,
  fireEvent,
  render as renderComponent,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axe from 'axe-core';
import { tick, type ComponentProps } from 'svelte';
import { isTopmostPopout, registerPopout } from '$lib/components/popout-stack';
import * as api from '$lib/api/client';
import { queryClient, queryKeys } from '$lib/api/query';
import { auth } from '$lib/auth/auth';
import { FINDER_F4_ENABLED } from '$lib/config';
import { PERSISTENCE_KEYS } from '$lib/persistence/storage';
import { parseId64 } from '$lib/domain/id64';
import { applicationStores, pins, syncKey } from '$lib/persistence/stores';
import { createHeadlessEngine } from '$lib/spatial/babylon/test-support';
import type {
  RuntimeEvent,
  RuntimeCommand,
  SpatialRuntimeStatus,
  GalaxySceneContract,
  GalaxySystemsPayload,
} from '$lib/spatial/contracts';
import type { ExploreAnchor } from './navigation';
import * as galaxyRegions from '$lib/spatial/galaxy-regions';
import * as galaxyNebulae from '$lib/spatial/galaxy-nebulae';
import { galaxyCameraPlanePoint } from '$lib/spatial/galaxy-camera';
import {
  DENSITY_CROSSFADE_NEAR_LY,
  GALAXY_WIDE_STAR_LIMIT,
  LOCAL_STAR_ENTER_LY,
  LOCAL_STAR_EXIT_LY,
} from '$lib/spatial/galaxy-detail-policy';
import { createManagedSpatialRuntime } from '$lib/spatial/lifecycle';
import { COMMANDER_HISTORY_GALAXY_BOUNDS } from '$lib/spatial/commander-history-viewport';
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
    getMapHeatmap: vi.fn(),
    getWatchlist: vi.fn(),
  };
});

const runtimeAdapter = vi.hoisted(() => ({ create: vi.fn() }));
vi.mock('$lib/spatial/babylon/adapter', () => ({
  createBabylonSpatialRuntime: runtimeAdapter.create,
}));

const pyramidFixture: api.MapHeatmapPyramidResponse = {
  source: 'pyramid',
  generation_id: 'gen-1',
  spatial_generation_id: 'gen-1',
  spatial_pyramid_version: 'v1',
  source_system_count: 200,
  coverage_at: '2026-01-01T00:00:00Z',
  level: 0,
  requested_level: 0,
  served_level: 0,
  level_cell_count: 2,
  coverage: 'complete',
  cell_size_ly: 1000,
  voxel_size: 1000,
  bounds: {
    min_x: 0,
    max_x: 2000,
    min_y: 0,
    max_y: 1000,
    min_z: 0,
    max_z: 1000,
  },
  cells: [
    {
      origin_x_ly: 0,
      origin_y_ly: 0,
      origin_z_ly: 0,
      centroid_x_ly: 500,
      centroid_y_ly: 500,
      centroid_z_ly: 500,
      system_count: 120,
    },
    {
      origin_x_ly: 1000,
      origin_y_ly: 0,
      origin_z_ly: 0,
      centroid_x_ly: 1500,
      centroid_y_ly: 500,
      centroid_z_ly: 500,
      system_count: 80,
    },
  ],
  count: 2,
  max_cells: 40_000,
  truncated: false,
};

const coarsePyramidFixture: api.MapHeatmapPyramidResponse = {
  ...pyramidFixture,
  cells: [{ ...pyramidFixture.cells[0], system_count: 200 }],
  count: 1,
  level_cell_count: 1,
};

const legacyFixture: api.MapHeatmapResponse = { source: 'legacy-fallback' };

const emptyPyramidFixture: api.MapHeatmapPyramidResponse = {
  ...pyramidFixture,
  cells: [],
  count: 0,
  level_cell_count: 0,
  source_system_count: 0,
};

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

async function openLayers(): Promise<HTMLElement> {
  const trigger = screen.getByRole('button', { name: /^Layers/ });
  if (trigger.getAttribute('aria-expanded') !== 'true')
    await fireEvent.click(trigger);
  return screen.getByRole('dialog', { name: 'Map layers' });
}

function densityStatus(): HTMLElement {
  return screen.getByRole('status', { name: 'Catalogue density' });
}

function exactStarStatus(): HTMLElement {
  return document.querySelector<HTMLElement>('[data-catalogue-star-count]')!;
}

async function waitForCatalogueCells(): Promise<void> {
  await vi.waitFor(
    () => {
      const cells = queryClient
        .getQueryCache()
        .getAll()
        .filter(
          (query) =>
            query.queryKey[2] === 'catalogue-stars' &&
            query.getObserversCount() > 0,
        );
      expect(cells.length).toBeGreaterThan(0);
      expect(cells.every((query) => query.state.status === 'success')).toBe(
        true,
      );
    },
    { interval: 1 },
  );
  await tick();
}

let runtimeResizeObserverCallback: ResizeObserverCallback;

function stubRuntime(): void {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(callback: ResizeObserverCallback) {
        runtimeResizeObserverCallback = callback;
      }

      observe = vi.fn();
      disconnect = vi.fn();
      unobserve = vi.fn();
    },
  );
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query === '(prefers-reduced-motion: reduce)',
  }));
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(640);
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(360);
  runtimeAdapter.create.mockImplementation(
    (_canvas, onStatus: (status: SpatialRuntimeStatus) => void) => {
      let listener: ((event: RuntimeEvent) => void) | undefined;
      return {
        getStatus: () => ({ state: 'created' as const }),
        start: vi.fn(async () => {
          const ready = { state: 'ready' as const, backend: 'WEBGL2' as const };
          onStatus(ready);
          return ready;
        }),
        dispatch: vi.fn((command: RuntimeCommand) => {
          if (command.type === 'FLY_TO')
            queueMicrotask(() =>
              listener?.({
                type: 'TRANSITION_FINISHED',
                target: command.target,
              }),
            );
          return { status: 'executed' as const };
        }),
        subscribe: vi.fn((next: (event: RuntimeEvent) => void) => {
          listener = next;
          return vi.fn();
        }),
        resize: vi.fn(),
        dispose: vi.fn(),
      };
    },
  );
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

describe('ExploreWorkspace density cross-fade wiring', () => {
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

  it('fetches the galaxy heatmap when zoomed out and shows the live density cell count', async () => {
    vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);

    render(ExploreWorkspaceTestHost);
    await openLayers();

    await waitFor(() => expect(api.getMapHeatmap).toHaveBeenCalled());
    expect(await screen.findByText(/2 density cells/)).toBeInTheDocument();
    await waitForCatalogueCells();
    expect(densityStatus()).toHaveAttribute('data-density-state', 'ready');
    expect(exactStarStatus()).toHaveTextContent('0 exact stars');
    expect(
      screen.queryByText(/Known-system density · zoom in/),
    ).not.toBeInTheDocument();
    // Ask for every occupied cell in a finite camera-derived area; the server
    // can coarsen the discrete requested resolution to retain sparse regions.
    expect(api.getMapHeatmap).toHaveBeenCalledWith(
      expect.objectContaining({
        min_systems: 1,
        voxel_size: 590,
        max_cells: 40_000,
        coverage: 'complete',
        min_x: expect.any(Number),
        max_x: expect.any(Number),
        min_y: expect.any(Number),
        max_y: expect.any(Number),
        min_z: expect.any(Number),
        max_z: expect.any(Number),
      }),
      expect.anything(),
    );
    expect(
      screen.getByLabelText('Known-system density legend'),
    ).toHaveTextContent('Complete at 1,000 LY');
  });

  it('debounces camera movement and keys density requests by the bounds bucket', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
    render(ExploreWorkspaceTestHost);
    await openLayers();
    await vi.waitFor(() => expect(api.getMapHeatmap).toHaveBeenCalled(), {
      interval: 1,
    });
    await vi.waitFor(() => expect(currentScene()).toBeDefined(), {
      interval: 1,
    });
    const initial = vi.mocked(api.getMapHeatmap).mock.calls.at(-1)![0];
    const initialCalls = vi.mocked(api.getMapHeatmap).mock.calls.length;
    const camera = currentScene().camera;
    emitRuntime({
      type: 'CAMERA_CHANGED',
      camera: {
        ...camera,
        distanceLy: 118_050,
        focusLy: { x: 20, y: 0, z: 0 },
        revision: 1,
      },
    });
    await vi.advanceTimersByTimeAsync(180);
    expect(api.getMapHeatmap).toHaveBeenCalledTimes(initialCalls);
    emitRuntime({
      type: 'CAMERA_CHANGED',
      camera: {
        ...camera,
        distanceLy: 10_000,
        focusLy: { x: 10_000, y: 0, z: 0 },
        revision: 2,
      },
    });
    await vi.advanceTimersByTimeAsync(100);
    expect(api.getMapHeatmap).toHaveBeenCalledTimes(initialCalls);
    emitRuntime({
      type: 'CAMERA_CHANGED',
      camera: {
        ...camera,
        distanceLy: 10_000,
        focusLy: { x: 30_000, y: 0, z: 0 },
        revision: 3,
      },
    });
    await vi.advanceTimersByTimeAsync(180);
    await vi.waitFor(
      () => expect(api.getMapHeatmap).toHaveBeenCalledTimes(initialCalls + 1),
      { interval: 1 },
    );
    const moved = vi.mocked(api.getMapHeatmap).mock.calls.at(-1)![0];
    expect(moved.coverage).toBe('complete');
    expect(moved.max_x! - moved.min_x!).toBeLessThan(
      initial.max_x! - initial.min_x!,
    );
    const farCorner = galaxyCameraPlanePoint(
      {
        ...camera,
        distanceLy: 10_000,
        focusLy: { x: 30_000, y: 0, z: 0 },
      },
      { x: 640, y: 0 },
      { width: 640, height: 360 },
    )!;
    expect(moved.max_x).toBeGreaterThanOrEqual(farCorner.x);
    expect(moved.max_z).toBeGreaterThanOrEqual(farCorner.z);
    expect(moved.min_x).toBeGreaterThan(0);
  });

  it('refreshes density bounds and query keys when the canvas aspect changes at a fixed camera', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    let finishDensity!: (response: api.MapHeatmapResponse) => void;
    vi.mocked(api.getMapHeatmap).mockReturnValue(
      new Promise((resolve) => (finishDensity = resolve)),
    );
    render(ExploreWorkspaceTestHost);
    await vi.waitFor(() => expect(currentScene()).toBeDefined(), {
      interval: 1,
    });
    emitRuntime({
      type: 'CAMERA_CHANGED',
      camera: {
        ...currentScene().camera,
        focusLy: { x: 0, y: 0, z: 0 },
        distanceLy: 10_000,
        pitchRad: 0.55,
        revision: 1,
      },
    });
    await vi.advanceTimersByTimeAsync(180);
    await vi.waitFor(
      () =>
        expect(api.getMapHeatmap).toHaveBeenLastCalledWith(
          expect.objectContaining({ voxel_size: 200 }),
          expect.anything(),
        ),
      { interval: 1 },
    );
    const initial = vi.mocked(api.getMapHeatmap).mock.calls.at(-1)![0];
    const initialCalls = vi.mocked(api.getMapHeatmap).mock.calls.length;
    const initialKeys = queryClient
      .getQueryCache()
      .findAll({ queryKey: ['map-heatmap'] })
      .map((query) => query.queryHash);

    // A proportional resize has the same bounds. It must preserve even an
    // in-flight query, rather than aborting and restarting identical work.
    runtimeResizeObserverCallback(
      [
        {
          contentRect: { width: 1280, height: 720 },
        } as unknown as ResizeObserverEntry,
      ],
      {} as ResizeObserver,
    );
    await tick();
    expect(api.getMapHeatmap).toHaveBeenCalledTimes(initialCalls);
    expect(
      queryClient
        .getQueryCache()
        .findAll({ queryKey: ['map-heatmap'] })
        .map((query) => query.queryHash),
    ).toEqual(initialKeys);

    runtimeResizeObserverCallback(
      [
        {
          contentRect: { width: 1280, height: 360 },
        } as unknown as ResizeObserverEntry,
      ],
      {} as ResizeObserver,
    );
    await vi.waitFor(
      () => expect(api.getMapHeatmap).toHaveBeenCalledTimes(initialCalls + 1),
      { interval: 1 },
    );
    const resized = vi.mocked(api.getMapHeatmap).mock.calls.at(-1)![0];
    expect(resized.min_x).toBeLessThan(initial.min_x!);
    expect(resized.max_x).toBeGreaterThan(initial.max_x!);
    expect(resized.min_z).toBe(initial.min_z);
    expect(resized.max_z).toBe(initial.max_z);
    expect(
      queryClient
        .getQueryCache()
        .findAll({ queryKey: ['map-heatmap'] })
        .some((query) => !initialKeys.includes(query.queryHash)),
    ).toBe(true);
    expect(document.querySelector('.spatial-canvas')).toHaveAttribute(
      'data-camera-revision',
      '1',
    );
    finishDensity(pyramidFixture);
    await vi.waitFor(
      () =>
        expect(queryClient.isFetching({ queryKey: ['map-heatmap'] })).toBe(0),
      { interval: 1 },
    );
  });

  it('disables density requests and removes its cached cells without changing stars, camera or selection', async () => {
    featureGate.enabled = true;
    mockRankedPages();
    vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
    vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue(
      catalogueFixture,
    );
    renderEnabled('page_size=1');
    await screen.findByText('Ranked 1');
    await fireEvent.click(
      screen.getByRole('button', { name: /Ranked 1.*9007199254741001/ }),
    );
    await openLayers();
    await screen.findByText('2 density cells');
    await waitFor(() =>
      expect(currentScene().selection).toContainEqual({
        kind: 'system',
        systemId64: rankedRows[0].id64,
      }),
    );
    // The stub runtime reports completed flights but does not emit their camera
    // receipts. Supply a settled overview camera before testing a layer change.
    // Focus the overview on the fixture star so the viewport-aware catalogue
    // sample keeps it in frame at galaxy scale.
    const camera = {
      ...currentScene().camera,
      focusLy: { x: 0, y: 0, z: 0 },
      distanceLy: 118_000,
      revision: 10,
    };
    emitRuntime({ type: 'CAMERA_CHANGED', camera });
    await waitFor(() =>
      expect(document.querySelector('[data-camera-distance]')).toHaveAttribute(
        'data-camera-distance',
        '118000',
      ),
    );
    const selection = currentScene().selection;
    await waitFor(() =>
      expect(scenePointIds()).toContain(catalogueFixture.systems[0].id64),
    );
    const stars = scenePointIds();
    const fetches = vi.mocked(api.getMapHeatmap).mock.calls.length;
    const density = screen.getByRole('switch', {
      name: 'Known-system density',
    });

    await fireEvent.click(density);

    expect(density).toHaveAttribute('aria-checked', 'false');
    await waitFor(() =>
      expect(
        currentScene().contributions.flatMap(({ layers }) => layers),
      ).not.toContainEqual(
        expect.objectContaining({ id: 'catalogue-density' }),
      ),
    );
    expect(scenePointIds()).toEqual(stars);
    expect(currentScene().camera).toEqual(camera);
    expect(currentScene().selection).toEqual(selection);
    expect(densityStatus()).toHaveAttribute('data-density-state', 'off');
    expect(densityStatus()).not.toHaveTextContent('2 density cells');

    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
    emitRuntime({
      type: 'CAMERA_CHANGED',
      camera: { ...camera, distanceLy: 180_000, revision: 1 },
    });
    await vi.advanceTimersByTimeAsync(300);
    await queryClient.invalidateQueries({ queryKey: ['map-heatmap'] });
    await tick();
    expect(api.getMapHeatmap).toHaveBeenCalledTimes(fetches);
    expect(scenePointIds()).toEqual(stars);
    expect(currentScene().selection).toEqual(selection);

    await fireEvent.click(density);
    await vi.waitFor(() =>
      expect(api.getMapHeatmap).toHaveBeenCalledTimes(fetches + 1),
    );
    expect(api.getMapHeatmap).toHaveBeenLastCalledWith(
      expect.objectContaining({ voxel_size: 900 }),
      expect.anything(),
    );
    expect(density).toHaveAttribute('aria-checked', 'true');
    expect(densityStatus()).toHaveAttribute('data-density-state', 'ready');
    expect(currentScene().selection).toEqual(selection);
  });

  it('refetches after density is turned off and on at the same zoom', async () => {
    vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
    render(ExploreWorkspaceTestHost);
    await openLayers();
    await screen.findByText('2 density cells');
    const fetches = vi.mocked(api.getMapHeatmap).mock.calls.length;
    const density = screen.getByRole('switch', {
      name: 'Known-system density',
    });
    await fireEvent.click(density);
    vi.mocked(api.getMapHeatmap).mockResolvedValue(coarsePyramidFixture);

    await fireEvent.click(density);

    await waitFor(() =>
      expect(api.getMapHeatmap).toHaveBeenCalledTimes(fetches + 1),
    );
    await screen.findByText('1 density cells');
  });

  it('does not restore density cells when an in-flight response arrives after the switch is off', async () => {
    let finish!: (response: api.MapHeatmapResponse) => void;
    const pending = new Promise<api.MapHeatmapResponse>((resolve) => {
      finish = resolve;
    });
    vi.mocked(api.getMapHeatmap).mockReturnValueOnce(pending);
    render(ExploreWorkspaceTestHost);
    await openLayers();
    await waitFor(() =>
      expect(densityStatus()).toHaveAttribute('data-density-state', 'loading'),
    );
    await fireEvent.click(
      screen.getByRole('switch', { name: 'Known-system density' }),
    );

    finish(pyramidFixture);
    await pending;
    await tick();
    await tick();

    expect(densityStatus()).toHaveAttribute('data-density-state', 'off');
    expect(densityStatus()).not.toHaveTextContent('2 density cells');
    await waitFor(() => expect(currentScene()).toBeDefined());
    expect(
      currentScene().contributions.flatMap(({ layers }) => layers),
    ).not.toContainEqual(expect.objectContaining({ id: 'catalogue-density' }));
    vi.mocked(api.getMapHeatmap).mockResolvedValue(coarsePyramidFixture);
    await fireEvent.click(
      screen.getByRole('switch', { name: 'Known-system density' }),
    );
    await screen.findByText('1 density cells');
  });

  it('restores browser switches on remount without persisting journal content or nebula visibility', async () => {
    await auth.bootstrap();
    vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
    render(ExploreWorkspaceTestHost);
    await openLayers();
    await screen.findByText('2 density cells');
    await fireEvent.click(
      screen.getByRole('switch', { name: 'Known-system density' }),
    );
    await fireEvent.click(screen.getByRole('switch', { name: 'My journeys' }));
    await fireEvent.click(screen.getByRole('switch', { name: 'Nebulae' }));
    expect(
      JSON.parse(localStorage.getItem(PERSISTENCE_KEYS.mapLayers)!),
    ).toEqual({
      version: 1,
      density: false,
      journeys: true,
    });
    cleanup();
    queryClient.clear();
    vi.mocked(api.getMapHeatmap).mockClear();

    render(ExploreWorkspaceTestHost);
    await openLayers();
    await screen.findByText(/exact stars/);

    expect(
      screen.getByRole('switch', { name: 'Known-system density' }),
    ).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByRole('switch', { name: 'My journeys' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(screen.getByRole('switch', { name: 'Nebulae' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(
      screen.getByRole('button', { name: 'Layers · 2' }),
    ).toBeInTheDocument();
    expect(api.getMapHeatmap).not.toHaveBeenCalled();
    expect(api.getCommanderTravelHeatmap).not.toHaveBeenCalled();
  });

  it('reports no published pyramid separately from exact stars for a legacy fallback', async () => {
    vi.mocked(api.getMapHeatmap).mockResolvedValue(legacyFixture);

    render(ExploreWorkspaceTestHost);
    await openLayers();

    await waitFor(() => expect(api.getMapHeatmap).toHaveBeenCalled());
    await screen.findByText(/exact stars/);
    await waitFor(() =>
      expect(densityStatus()).toHaveAttribute('data-density-state', 'none'),
    );
    expect(densityStatus()).toHaveTextContent(/no published/i);
    expect(
      within(densityStatus()).queryByRole('button'),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/density cells/)).not.toBeInTheDocument();
    expect(
      within(screen.getByRole('dialog', { name: 'Map layers' })).queryByRole(
        'alert',
      ),
    ).not.toBeInTheDocument();
  });

  it('prioritizes the initial density query before loading exact stars', async () => {
    let finish!: (response: api.MapHeatmapResponse) => void;
    vi.mocked(api.getMapHeatmap).mockImplementation(
      () => new Promise((resolve) => (finish = resolve)),
    );
    vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue(
      catalogueFixture,
    );
    render(ExploreWorkspaceTestHost);
    await openLayers();

    await waitFor(() => expect(api.getMapHeatmap).toHaveBeenCalled());
    expect(densityStatus()).toHaveAttribute('data-density-state', 'loading');
    expect(densityStatus()).toHaveTextContent(/loading/i);
    await waitFor(() =>
      expect(exactStarStatus()).toHaveTextContent(
        'Loading exact catalogue stars',
      ),
    );
    expect(api.getCatalogueViewportSystems).not.toHaveBeenCalled();
    finish(pyramidFixture);
    await waitFor(() =>
      expect(densityStatus()).toHaveAttribute('data-density-state', 'ready'),
    );
    await screen.findByText('1 exact stars');
  });

  it.each([
    { name: '503', error: new api.ApiError(503, '/api/map/heatmap', {}) },
    { name: 'network', error: new TypeError('Failed to fetch') },
  ])(
    'offers a density-only Retry after a $name failure and recovers',
    async ({ error }) => {
      vi.mocked(api.getMapHeatmap).mockRejectedValue(error);
      vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue(
        catalogueFixture,
      );
      render(ExploreWorkspaceTestHost);
      await openLayers();

      await waitFor(
        () =>
          expect(densityStatus()).toHaveAttribute(
            'data-density-state',
            'unavailable',
          ),
        { timeout: 3_000 },
      );
      await waitForCatalogueCells();
      expect(exactStarStatus()).toHaveTextContent('1 exact stars');
      expect(
        screen.queryByText('Exact-star layer unavailable'),
      ).not.toBeInTheDocument();
      const calls = vi.mocked(api.getMapHeatmap).mock.calls.length;
      const starCalls = vi.mocked(api.getCatalogueViewportSystems).mock.calls
        .length;
      vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
      await fireEvent.click(
        within(densityStatus()).getByRole('button', { name: 'Retry' }),
      );

      await waitFor(() =>
        expect(densityStatus()).toHaveAttribute('data-density-state', 'ready'),
      );
      expect(api.getMapHeatmap).toHaveBeenCalledTimes(calls + 1);
      expect(densityStatus()).toHaveTextContent('2 density cells');
      expect(
        within(densityStatus()).queryByRole('button'),
      ).not.toBeInTheDocument();
      expect(api.getCatalogueViewportSystems).toHaveBeenCalledTimes(starCalls);
      expect(exactStarStatus()).toHaveTextContent('1 exact stars');
    },
  );

  it('reports an invalid density payload with Retry independently from exact stars', async () => {
    featureGate.enabled = true;
    vi.mocked(api.getMapHeatmap).mockResolvedValue({
      source: 'pyramid',
    } as api.MapHeatmapPyramidResponse);
    vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue(
      catalogueFixture,
    );
    render(ExploreWorkspaceTestHost);
    await openLayers();

    await waitFor(() =>
      expect(densityStatus()).toHaveAttribute('data-density-state', 'invalid'),
    );
    await waitForCatalogueCells();
    expect(exactStarStatus()).toHaveTextContent('1 exact stars');
    vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
    await fireEvent.click(
      within(densityStatus()).getByRole('button', { name: 'Retry' }),
    );
    await waitFor(() =>
      expect(densityStatus()).toHaveAttribute('data-density-state', 'ready'),
    );
    expect(densityStatus()).toHaveTextContent('2 density cells');
  });

  it.each([
    { state: 'empty', fixture: emptyPyramidFixture },
    {
      state: 'truncated',
      fixture: {
        ...coarsePyramidFixture,
        truncated: true,
        coverage: 'truncated' as const,
        level_cell_count: null,
      },
    },
  ])(
    'distinguishes a valid $state density area from failure',
    async ({ state, fixture }) => {
      vi.mocked(api.getMapHeatmap).mockResolvedValue(fixture);
      render(ExploreWorkspaceTestHost);
      await openLayers();

      await waitFor(() =>
        expect(densityStatus()).toHaveAttribute('data-density-state', state),
      );
      await waitForCatalogueCells();
      expect(
        within(densityStatus()).queryByRole('button'),
      ).not.toBeInTheDocument();
      expect(exactStarStatus()).toHaveTextContent('0 exact stars');
      if (state === 'truncated') {
        expect(densityStatus()).toHaveTextContent('1 density cells');
        expect(densityStatus()).toHaveTextContent(
          'Truncated · 1 cells shown at 1,000 LY',
        );
      }
    },
  );

  it.each([false, true])(
    'keeps the exact-star failure status independent of ready density (F4=%s)',
    async (enabled) => {
      featureGate.enabled = enabled;
      vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
      vi.mocked(api.getCatalogueViewportSystems).mockRejectedValue(
        new api.ApiError(503, '/api/map/catalogue-viewport', {}),
      );
      render(ExploreWorkspaceTestHost);
      await openLayers();

      await screen.findByText(
        'Exact-star layer unavailable',
        {},
        { timeout: 3_000 },
      );
      expect(densityStatus()).toHaveAttribute('data-density-state', 'ready');
      expect(densityStatus()).toHaveTextContent('2 density cells');
      expect(exactStarStatus()).toHaveTextContent(
        'Exact-star layer unavailable',
      );
    },
  );

  it.each([
    { enabled: false, failure: 'unavailable' },
    { enabled: true, failure: 'unavailable' },
    { enabled: false, failure: 'invalid' },
    { enabled: true, failure: 'invalid' },
  ])(
    'keeps last good density after $failure on refetch (F4=$enabled)',
    async ({ enabled, failure }) => {
      featureGate.enabled = enabled;
      vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
      render(ExploreWorkspaceTestHost);
      await openLayers();
      await screen.findByText('2 density cells');
      await waitFor(() => expect(currentScene()).toBeDefined());
      await waitForCatalogueCells();
      const loads = runtimeCommands().filter(
        (command) => command.type === 'LOAD_SCENE',
      ).length;
      const patches = runtimeCommands().filter(
        (command) => command.type === 'PATCH_CONTRIBUTION',
      ).length;
      if (failure === 'unavailable') {
        vi.mocked(api.getMapHeatmap).mockRejectedValue(
          new api.ApiError(503, '/api/map/heatmap', {}),
        );
      } else {
        vi.mocked(api.getMapHeatmap).mockResolvedValue({
          source: 'pyramid',
        } as api.MapHeatmapPyramidResponse);
      }
      await queryClient.invalidateQueries({ queryKey: ['map-heatmap'] });

      await waitFor(
        () =>
          expect(densityStatus()).toHaveAttribute(
            'data-density-state',
            'stale',
          ),
        { timeout: 3_000 },
      );
      expect(densityStatus().getAttribute('data-density-reason')).toBeTruthy();
      expect(densityStatus()).toHaveTextContent(/stale/i);
      expect(densityStatus()).toHaveTextContent('2 density cells');
      expect(densityStatus()).toHaveAttribute('data-density-cell-count', '2');
      expect(
        runtimeCommands().filter((command) => command.type === 'LOAD_SCENE'),
      ).toHaveLength(loads);
      expect(
        runtimeCommands().filter(
          (command) => command.type === 'PATCH_CONTRIBUTION',
        ),
      ).toHaveLength(patches);
      expect(exactStarStatus()).toHaveTextContent('0 exact stars');

      vi.mocked(api.getMapHeatmap).mockResolvedValue(coarsePyramidFixture);
      await fireEvent.click(
        within(densityStatus()).getByRole('button', { name: 'Retry' }),
      );
      await waitFor(() =>
        expect(densityStatus()).toHaveAttribute('data-density-state', 'ready'),
      );
      expect(densityStatus()).toHaveTextContent('1 density cells');
      expect(densityStatus()).not.toHaveAttribute('data-density-reason');
    },
  );

  it.each([false, true])(
    'retains last good density when a different viewport fails (F4=%s)',
    async (enabled) => {
      featureGate.enabled = enabled;
      vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
      render(ExploreWorkspaceTestHost);
      await openLayers();
      await screen.findByText('2 density cells');
      await waitFor(() => expect(currentScene()).toBeDefined());
      vi.mocked(api.getMapHeatmap).mockRejectedValue(
        new TypeError('Failed to fetch'),
      );
      emitRuntime({
        type: 'CAMERA_CHANGED',
        camera: { ...currentScene().camera, distanceLy: 180_000, revision: 1 },
      });

      await waitFor(
        () =>
          expect(densityStatus()).toHaveAttribute(
            'data-density-state',
            'stale',
          ),
        { timeout: 3_000 },
      );
      expect(api.getMapHeatmap).toHaveBeenLastCalledWith(
        expect.objectContaining({ voxel_size: 900 }),
        expect.anything(),
      );
      expect(densityStatus()).toHaveTextContent('2 density cells');
      expect(densityStatus()).toHaveAttribute('data-density-reason', 'network');
      expect(
        currentScene().contributions.flatMap(
          (contribution) => contribution.layers,
        ),
      ).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ id: 'catalogue-density', targetCount: 2 }),
        ]),
      );
    },
  );

  it('patches an older cached density viewport without reloading the scene', async () => {
    featureGate.enabled = true;
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
    vi.mocked(api.getMapHeatmap).mockImplementation(async (request) =>
      request.voxel_size === 590 ? pyramidFixture : coarsePyramidFixture,
    );
    render(ExploreWorkspaceTestHost);
    await openLayers();
    await vi.waitFor(
      () => {
        expect(screen.getByText(/2 density cells/)).toBeInTheDocument();
        expect(
          runtimeCommands().some((command) => command.type === 'LOAD_SCENE'),
        ).toBe(true);
      },
      { interval: 1 },
    );
    await waitForCatalogueCells();
    const initialLoads = runtimeCommands().filter(
      (command) => command.type === 'LOAD_SCENE',
    ).length;
    const densityPatches = () =>
      runtimeCommands().filter(
        (command) => command.type === 'PATCH_CONTRIBUTION',
      );
    emitRuntime({
      type: 'CAMERA_CHANGED',
      camera: { ...currentScene().camera, distanceLy: 180_000, revision: 1 },
    });
    await vi.advanceTimersByTimeAsync(180);
    await vi.waitFor(
      () => expect(screen.getByText(/1 density cells/)).toBeInTheDocument(),
      { interval: 1 },
    );
    const newerRevision = densityPatches().at(-1)!.contribution.revision;
    const densityQueries = () =>
      queryClient.getQueryCache().findAll({ queryKey: ['map-heatmap'] });
    const queryAt = (voxel: number) =>
      densityQueries().find(
        (query) => JSON.parse(String(query.queryKey[1])).voxel_size === voxel,
      )!;
    const cachedUpdatedAt = queryAt(590).state.dataUpdatedAt;
    expect(cachedUpdatedAt).toBeLessThan(queryAt(900).state.dataUpdatedAt);
    const fetchesBeforeReturn = vi.mocked(api.getMapHeatmap).mock.calls.length;

    emitRuntime({
      type: 'CAMERA_CHANGED',
      camera: { ...currentScene().camera, distanceLy: 118_000, revision: 2 },
    });
    await vi.advanceTimersByTimeAsync(180);
    await vi.waitFor(
      () => expect(screen.getByText(/2 density cells/)).toBeInTheDocument(),
      { interval: 1 },
    );
    expect(api.getMapHeatmap).toHaveBeenCalledTimes(fetchesBeforeReturn);
    expect(queryAt(590).state.dataUpdatedAt).toBe(cachedUpdatedAt);
    expect(densityPatches().at(-1)!.contribution.revision).toBeGreaterThan(
      newerRevision,
    );
    expect(densityPatches().at(-1)!.contribution.layers[0].targetCount).toBe(2);
    expect(
      runtimeCommands().filter((command) => command.type === 'LOAD_SCENE'),
    ).toHaveLength(initialLoads);
  });

  it('removes a density contribution once when a refreshed response reports no published pyramid', async () => {
    featureGate.enabled = true;
    vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
    render(ExploreWorkspaceTestHost);
    await openLayers();
    await screen.findByText(/2 density cells/);
    await waitFor(() => expect(currentScene()).toBeDefined());
    await waitForCatalogueCells();
    const initialLoads = runtimeCommands().filter(
      (command) => command.type === 'LOAD_SCENE',
    ).length;
    vi.mocked(api.getMapHeatmap).mockResolvedValue(legacyFixture);
    await queryClient.invalidateQueries({ queryKey: ['map-heatmap'] });
    await waitFor(() =>
      expect(
        runtimeCommands().filter((command) => command.type === 'LOAD_SCENE'),
      ).toHaveLength(initialLoads + 1),
    );
    expect(
      currentScene()
        .contributions.flatMap((contribution) => contribution.layers)
        .some((layer) => layer.id === 'catalogue-density'),
    ).toBe(false);
    expect(screen.queryByText(/density cells/)).not.toBeInTheDocument();
    expect(densityStatus()).toHaveAttribute('data-density-state', 'none');
    await queryClient.invalidateQueries({ queryKey: ['map-heatmap'] });
    await tick();
    expect(
      runtimeCommands().filter((command) => command.type === 'LOAD_SCENE'),
    ).toHaveLength(initialLoads + 1);
  });

  it('coordinates local detail with the density overlap and retains the wide sample until detail arrives', async () => {
    featureGate.enabled = true;
    vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
    const wideFixture = {
      ...catalogueFixture,
      systems: [
        ...catalogueFixture.systems,
        {
          ...catalogueFixture.systems[0],
          id64: parseId64('9007199254740995'),
          name: 'Masked galaxy corner',
          x: 52_000,
          z: 78_000,
        },
      ],
    };
    vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue(wideFixture);
    render(ExploreWorkspaceTestHost);
    await openLayers();
    await screen.findByText('1 exact stars');
    await screen.findByText('2 density cells');
    await waitFor(() =>
      expect(scenePointIds()).toEqual([catalogueFixture.systems[0].id64]),
    );
    let finish!: (response: api.CatalogueViewportResponse) => void;
    const pendingLocal = new Promise<api.CatalogueViewportResponse>(
      (resolve) => (finish = resolve),
    );
    vi.mocked(api.getCatalogueViewportSystems).mockImplementation((request) =>
      request.limit === GALAXY_WIDE_STAR_LIMIT
        ? Promise.resolve(wideFixture)
        : pendingLocal,
    );
    emitRuntime({
      type: 'CAMERA_CHANGED',
      camera: {
        ...currentScene().camera,
        distanceLy: LOCAL_STAR_ENTER_LY,
        revision: 1,
      },
    });
    await waitFor(() =>
      expect(api.getCatalogueViewportSystems).toHaveBeenLastCalledWith(
        expect.objectContaining({ limit: 40_000 }),
        expect.anything(),
      ),
    );
    expect(api.getCatalogueViewportSystems).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 40_000 }),
      expect.anything(),
    );
    expect(api.getMapHeatmap).toHaveBeenLastCalledWith(
      expect.objectContaining({
        voxel_size: 200,
        min_systems: 1,
        max_cells: 40_000,
      }),
      expect.anything(),
    );
    await waitFor(() =>
      expect(densityStatus()).toHaveAttribute('data-density-state', 'ready'),
    );
    expect(exactStarStatus()).toHaveTextContent('1 exact stars');
    expect(exactStarStatus()).toHaveAttribute('data-catalogue-star-count', '1');
    expect(scenePointIds()).toEqual([catalogueFixture.systems[0].id64]);
    const localStars = {
      systems: [
        ...catalogueFixture.systems,
        {
          ...catalogueFixture.systems[0],
          id64: parseId64('9007199254740994'),
          name: 'Local star',
        },
      ],
      truncated: false,
    };
    finish(localStars);
    await waitFor(() =>
      expect(scenePointIds()).toEqual(
        localStars.systems.map((system) => system.id64),
      ),
    );
    expect(exactStarStatus()).toHaveAttribute('data-catalogue-star-count', '2');

    const starFetches = vi.mocked(api.getCatalogueViewportSystems).mock.calls
      .length;
    emitRuntime({
      type: 'CAMERA_CHANGED',
      camera: {
        ...currentScene().camera,
        distanceLy: DENSITY_CROSSFADE_NEAR_LY,
        revision: 2,
      },
    });
    await new Promise((resolve) => setTimeout(resolve, 220));
    await tick();
    // A closer camera inside the same budget band can reuse the covering
    // cells; the old shifted-box request would have refetched here.
    expect(api.getCatalogueViewportSystems).toHaveBeenCalledTimes(starFetches);
    // Active-query invalidation must still refetch at the fade endpoint;
    // cached data alone would also be visible with an incorrectly disabled query.
    // The finer bounds now select a new cache entry at this same voxel size;
    // let that fetch finish so invalidation isn't deduplicated with it.
    await waitFor(() =>
      expect(queryClient.isFetching({ queryKey: ['map-heatmap'] })).toBe(0),
    );
    const fetches = vi.mocked(api.getMapHeatmap).mock.calls.length;
    await queryClient.invalidateQueries({ queryKey: ['map-heatmap'] });
    await waitFor(() =>
      expect(api.getMapHeatmap).toHaveBeenCalledTimes(fetches + 1),
    );
    expect(densityStatus()).toHaveAttribute('data-density-state', 'ready');
  });

  it('filters retained local detail against the current footprint while the returning wide cells load', async () => {
    featureGate.enabled = true;
    vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
    vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue(
      catalogueFixture,
    );
    render(ExploreWorkspaceTestHost);
    await openLayers();
    await screen.findByText('1 exact stars');
    await waitFor(() => expect(currentScene()).toBeDefined());
    const focusLy = { x: 52_000, y: 0, z: 78_000 };
    const localFixture = {
      ...catalogueFixture,
      systems: [
        {
          ...catalogueFixture.systems[0],
          ...focusLy,
          id64: parseId64('9007199254740994'),
          name: 'Local rim star',
        },
        {
          ...catalogueFixture.systems[0],
          x: 59_000,
          z: 85_000,
          id64: parseId64('9007199254740995'),
          name: 'Masked local corner',
        },
      ],
    };
    vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue(localFixture);
    emitRuntime({
      type: 'CAMERA_CHANGED',
      camera: {
        ...currentScene().camera,
        focusLy,
        distanceLy: LOCAL_STAR_ENTER_LY,
        revision: 1,
      },
    });
    await waitFor(() =>
      expect(scenePointIds()).toEqual([localFixture.systems[0].id64]),
    );
    queryClient.removeQueries({
      queryKey: [...queryKeys.all, 'map', 'catalogue-stars'],
      type: 'inactive',
    });
    let finish!: (response: api.CatalogueViewportResponse) => void;
    const pendingWide = new Promise<api.CatalogueViewportResponse>(
      (resolve) => (finish = resolve),
    );
    vi.mocked(api.getCatalogueViewportSystems).mockImplementation(
      () => pendingWide,
    );
    emitRuntime({
      type: 'CAMERA_CHANGED',
      camera: {
        ...currentScene().camera,
        focusLy,
        distanceLy: LOCAL_STAR_EXIT_LY + 100,
        revision: 2,
      },
    });
    await waitFor(() =>
      expect(api.getCatalogueViewportSystems).toHaveBeenLastCalledWith(
        expect.objectContaining({ limit: GALAXY_WIDE_STAR_LIMIT }),
        expect.anything(),
      ),
    );
    expect(exactStarStatus()).toHaveTextContent('1 exact stars');
    // Retained points remain constrained to the current camera footprint;
    // the off-catalogue corner never becomes visible during the hand-off.
    expect(scenePointIds()).toEqual([localFixture.systems[0].id64]);
    finish(catalogueFixture);
    await waitFor(() => expect(scenePointIds()).toEqual([]));
  });

  it('keeps the star viewport mode while the camera reverses inside the hysteresis band', async () => {
    featureGate.enabled = true;
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
    vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
    const localFixture: api.CatalogueViewportResponse = {
      ...catalogueFixture,
      systems: [
        ...catalogueFixture.systems,
        {
          ...catalogueFixture.systems[0],
          id64: parseId64('9007199254740994'),
          name: 'Local star',
        },
      ],
    };
    vi.mocked(api.getCatalogueViewportSystems).mockImplementation(
      async (request) =>
        request.limit === GALAXY_WIDE_STAR_LIMIT
          ? catalogueFixture
          : localFixture,
    );
    render(ExploreWorkspaceTestHost);
    await openLayers();
    await vi.waitFor(
      () => expect(exactStarStatus()).toHaveTextContent('1 exact stars'),
      { interval: 1 },
    );

    await vi.waitFor(() => expect(currentScene()).toBeDefined(), {
      interval: 1,
    });

    const path = [
      { distance: LOCAL_STAR_ENTER_LY + 300, count: 1 },
      { distance: LOCAL_STAR_ENTER_LY - 100, count: 2 },
      { distance: LOCAL_STAR_ENTER_LY + 200, count: 2 },
      { distance: LOCAL_STAR_EXIT_LY + 100, count: 1 },
      { distance: LOCAL_STAR_ENTER_LY - 200, count: 2 },
    ];
    for (const [index, step] of path.entries()) {
      emitRuntime({
        type: 'CAMERA_CHANGED',
        camera: {
          ...currentScene().camera,
          distanceLy: step.distance,
          revision: index + 1,
        },
      });
      await vi.advanceTimersByTimeAsync(180);
      await vi.waitFor(
        () =>
          expect(exactStarStatus()).toHaveTextContent(
            `${step.count} exact stars`,
          ),
        { interval: 1 },
      );
    }
    const requests = vi
      .mocked(api.getCatalogueViewportSystems)
      .mock.calls.map(([request]) => request);
    expect(new Set(requests.map((request) => request.limit))).toEqual(
      new Set([GALAXY_WIDE_STAR_LIMIT, 40_000]),
    );
    // Revisiting the hysteresis band reuses whole cached cells; no shifted
    // copy of an already requested cell is fetched during this reversal.
    expect(
      new Set(requests.map((request) => JSON.stringify(request))).size,
    ).toBe(requests.length);
  });

  it('reuses cells for a small camera pan and updates their coverage when the viewport widens', async () => {
    featureGate.enabled = true;
    vi.mocked(api.getMapHeatmap).mockResolvedValue(legacyFixture);
    let resizeViewport!: (width: number, height: number) => void;
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(callback: ResizeObserverCallback) {
          resizeViewport = (width, height) =>
            callback(
              [{ contentRect: { width, height } } as ResizeObserverEntry],
              this as unknown as ResizeObserver,
            );
        }
        observe = vi.fn();
        disconnect = vi.fn();
        unobserve = vi.fn();
      },
    );
    render(ExploreWorkspaceTestHost);
    await waitForCatalogueCells();
    resizeViewport(360, 360);
    const camera = {
      ...currentScene().camera,
      focusLy: { x: 0, y: 0, z: 0 },
      distanceLy: 1_000,
      pitchRad: Math.PI / 2,
      bearingRad: 0,
      revision: 1,
    };
    emitRuntime({ type: 'CAMERA_CHANGED', camera });
    await waitFor(() =>
      expect(api.getCatalogueViewportSystems).toHaveBeenLastCalledWith(
        expect.objectContaining({ limit: 12_000 }),
        expect.anything(),
      ),
    );
    await waitForCatalogueCells();
    const localCalls = vi.mocked(api.getCatalogueViewportSystems).mock.calls
      .length;
    emitRuntime({
      type: 'CAMERA_CHANGED',
      camera: {
        ...camera,
        focusLy: { x: 50, y: 0, z: 0 },
        revision: 2,
      },
    });
    await new Promise((resolve) => setTimeout(resolve, 220));
    await tick();
    expect(api.getCatalogueViewportSystems).toHaveBeenCalledTimes(localCalls);

    resizeViewport(840, 360);
    await waitFor(() =>
      expect(
        vi.mocked(api.getCatalogueViewportSystems).mock.calls.length,
      ).toBeGreaterThan(localCalls),
    );
    await waitForCatalogueCells();
    const cachedCells = queryClient
      .getQueryCache()
      .getAll()
      .filter((query) => query.queryKey[2] === 'catalogue-stars');
    expect(cachedCells.length).toBeLessThanOrEqual(64);
    expect(
      cachedCells.filter((query) => query.getObserversCount() > 0).length,
    ).toBeLessThanOrEqual(16);
  });
});

describe('ExploreWorkspace Finder F4 gate (default off)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    stubRuntime();
    mockFacadeDefaults();
    vi.mocked(api.getMapHeatmap).mockResolvedValue(legacyFixture);
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
    expect(
      screen.getByRole('heading', { name: 'Systems' }),
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

    await screen.findByText(/exact stars/);
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
      navigation_limit: 10_000,
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

function runtimeCommands(): RuntimeCommand[] {
  const runtime = runtimeAdapter.create.mock.results.at(-1)?.value;
  return (
    runtime?.dispatch.mock.calls.map(
      ([command]: [RuntimeCommand]) => command,
    ) ?? []
  );
}

function currentScene(): GalaxySceneContract {
  const runtime = runtimeAdapter.create.mock.results.at(-1)?.value;
  const loads = runtime.dispatch.mock.calls.filter(
    ([command]: [{ type: string }]) => command.type === 'LOAD_SCENE',
  );
  return loads.at(-1)?.[0].scene;
}

function scenePointIds(): string[] {
  // The renderer deduplicates Finder points also retained in the reserved
  // catalogue contribution; inspect the same visible system identity set.
  return [
    ...new Set(
      currentScene().contributions.flatMap((contribution) =>
        contribution.layers.flatMap((layer) => {
          const payload = layer.payload as {
            systems?: { systemId64: string }[];
          };
          return payload.systems?.map((point) => point.systemId64) ?? [];
        }),
      ),
    ),
  ];
}

function emitRuntime(event: RuntimeEvent): void {
  const runtime = runtimeAdapter.create.mock.results.at(-1)?.value;
  runtime.subscribe.mock.calls[0][0](event);
}

describe('ExploreWorkspace nebula inventory status', () => {
  const publishedNebulae = galaxyNebulae.parseGalaxyNebulaeAsset(
    JSON.parse(
      readFileSync(
        resolve(process.cwd(), 'static/assets/edastro-mapcharts-nebulae.json'),
        'utf8',
      ),
    ) as unknown,
  );
  const diffuse = publishedNebulae.nebulae.find(
    (nebula) => nebula.kind === 'nebula',
  )!;
  const planetary = publishedNebulae.nebulae
    .filter((nebula) => nebula.kind === 'planetary-nebula')
    .slice(0, 3);

  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    stubRuntime();
    mockFacadeDefaults();
    featureGate.enabled = true;
    vi.mocked(api.getMapHeatmap).mockResolvedValue(legacyFixture);
    vi.spyOn(
      galaxyRegions,
      'fetchAuthoritativeGalaxyRegions',
    ).mockRejectedValue(new Error('No region geometry in this fixture'));
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  async function loadNebulaInventory(
    nebulae: readonly galaxyNebulae.GalaxyNebula[],
  ): Promise<void> {
    vi.spyOn(galaxyNebulae, 'fetchGalaxyNebulae').mockResolvedValue({
      ...publishedNebulae,
      nebulae,
    });
    render(ExploreWorkspaceTestHost);
    await openLayers();
    await waitFor(() => {
      expect(
        document.querySelector('[data-nebula-resource-state]'),
      ).toHaveAttribute('data-nebula-resource-state', 'ready');
      expect(
        currentScene()?.contributions.some(
          (contribution) => contribution.id === 'catalogue:galaxy-nebulae',
        ),
      ).toBe(true);
    });
  }

  function emitNebulaRendering(
    renderedTargetCount: number,
    distanceLy: number,
    eventType: 'SCENE_APPLIED' | 'CONTRIBUTION_APPLIED' = 'SCENE_APPLIED',
  ): void {
    const scene = currentScene();
    const contribution = scene.contributions.find(
      (entry) => entry.id === 'catalogue:galaxy-nebulae',
    )!;
    const layer = contribution.layers[0];
    emitRuntime({
      type: 'CAMERA_CHANGED',
      camera: { ...scene.camera, distanceLy },
    });
    const renderedLayers = [
      {
        contributionId: contribution.id,
        contributionRevision: contribution.revision,
        layerId: layer.id,
        layerVersion: layer.version,
        representation: layer.representation,
        acceptedTargetCount: layer.targetCount,
        renderedTargetCount,
      },
    ];
    emitRuntime(
      eventType === 'SCENE_APPLIED'
        ? { type: eventType, sceneRevision: scene.revision, renderedLayers }
        : {
            type: eventType,
            contributionId: contribution.id,
            contributionRevision: contribution.revision,
            renderedLayers,
          },
    );
  }

  function status(): Element | null {
    return document.querySelector('[data-nebula-resource-state]');
  }

  it('distinguishes the complete catalogue from landmarks confirmed in the overview scene', async () => {
    await loadNebulaInventory(publishedNebulae.nebulae);
    expect(status()).toHaveTextContent(
      '5,842 catalogue records · Preparing nebula landmarks…',
    );
    expect(status()).not.toHaveTextContent(/landmarks shown/);

    emitNebulaRendering(249, 118_000);
    await waitFor(() =>
      expect(status()).toHaveTextContent(
        '5,842 catalogue records · 249 landmarks shown (diffuse nebulae)',
      ),
    );
    expect(status()).not.toHaveTextContent('5,842 EDAstro nebula landmarks');
    expect(
      within(status() as HTMLElement).getByRole('link', {
        name: publishedNebulae.attribution,
      }),
    ).toHaveAttribute('href', 'https://edastro.com/mapcharts/files.html');
    expect(status()).toHaveTextContent(publishedNebulae.rightsNotice);
  });

  it('shows current planetary counts and persistent diffuse landmarks after zooming in', async () => {
    await loadNebulaInventory([diffuse, ...planetary]);
    emitNebulaRendering(1, 118_000);
    await waitFor(() =>
      expect(status()).toHaveTextContent(
        '4 catalogue records · 1 landmark shown (diffuse nebulae)',
      ),
    );

    emitNebulaRendering(4, 2_000, 'CONTRIBUTION_APPLIED');
    await waitFor(() =>
      expect(status()).toHaveTextContent(
        '4 catalogue records · 4 landmarks shown (diffuse and planetary nebulae) · schematic extent (clouds fade on approach)',
      ),
    );
    await fireEvent.click(screen.getByRole('switch', { name: 'Nebulae' }));
    expect(status()).toHaveTextContent(
      '4 catalogue records · Nebula layer hidden',
    );
    expect(status()).not.toHaveTextContent(/landmarks shown/);
    await fireEvent.click(screen.getByRole('switch', { name: 'Nebulae' }));
    expect(status()).toHaveTextContent('Preparing nebula landmarks…');
  });

  it('explains planetary-only inventories at overview and shows them at close range', async () => {
    await loadNebulaInventory(planetary);
    emitNebulaRendering(0, 118_000);
    await waitFor(() =>
      expect(status()).toHaveTextContent(
        '3 catalogue records · No landmarks at this zoom · planetary nebulae appear at close range',
      ),
    );
    expect(status()).not.toHaveTextContent(/landmarks shown/);

    emitNebulaRendering(3, 3_000, 'CONTRIBUTION_APPLIED');
    await waitFor(() =>
      expect(status()).toHaveTextContent(
        '3 catalogue records · 3 landmarks shown (planetary nebulae)',
      ),
    );
  });

  it('keeps the diffuse landmark count when decorative clouds fade out', async () => {
    await loadNebulaInventory([diffuse]);
    emitNebulaRendering(1, 400);
    await waitFor(() =>
      expect(status()).toHaveTextContent(
        '1 catalogue record · 1 landmark shown (diffuse nebulae) · schematic extent (clouds fade on approach)',
      ),
    );
    expect(status()).not.toHaveTextContent('landmarks faded out');
  });

  it('makes an empty inventory explicit', async () => {
    await loadNebulaInventory([]);
    emitNebulaRendering(0, 118_000);
    await waitFor(() =>
      expect(status()).toHaveTextContent(
        '0 catalogue records · No landmarks at this zoom',
      ),
    );
  });

  it('clears shown counts when renderer resources are lost', async () => {
    await loadNebulaInventory([diffuse, ...planetary]);
    emitNebulaRendering(1, 118_000);
    await waitFor(() => expect(status()).toHaveTextContent('1 landmark shown'));
    emitRuntime({ type: 'RESOURCE_LOST', detail: 'Test context loss' });
    await waitFor(() =>
      expect(status()).toHaveTextContent('Preparing nebula landmarks…'),
    );
    expect(status()).not.toHaveTextContent(/landmarks? shown/);
  });
});

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
  await waitFor(() =>
    expect(scenePointIds()).toEqual([
      rankedRows[index].id64,
      ...(mockedPage.state.anchor ? [mockedPage.state.anchor.id64] : []),
    ]),
  );
}

const emptyResultsPrompt = 'Search for a system above to see what is near it.';

function expectEmptyResults(): void {
  expect(screen.getByRole('heading', { name: 'Systems' })).toBeInTheDocument();
  expect(screen.getByText(emptyResultsPrompt)).toBeInTheDocument();
  expect(
    screen.queryByRole('list', { name: 'System results' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /^(Pin|Compare|Watch) Ranked/ }),
  ).not.toBeInTheDocument();
  const finder = currentScene().contributions.find(
    ({ id }) => id === 'finder-results',
  );
  expect(finder?.layers.every((layer) => layer.targetCount === 0)).toBe(true);
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
      vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
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

    it('starts empty without requesting results, even with cached rows and catalogue stars', async () => {
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
      vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue({
        systems: [
          {
            id64: anchorA.id64,
            name: anchorA.name,
            x: 10,
            y: 20,
            z: 30,
            mainStarClass: 'G',
            populated: false,
          },
        ],
        truncated: false,
      });
      renderEnabled('');
      await openLayers();

      await screen.findByText(/2 density cells/);
      await waitFor(() => expect(currentScene()).toBeDefined());
      await waitForCatalogueCells();
      expectEmptyResults();
      expect(api.getCatalogueViewportSystems).toHaveBeenCalled();
      expect(api.searchExploreSystems).not.toHaveBeenCalled();
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
      expect(
        screen.queryByText(
          /Searching systems|Building a real-system shortlist/,
        ),
      ).not.toBeInTheDocument();
    });

    it('loads nearby results only after an autocomplete anchor is chosen, then clears rows and markers', async () => {
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
      });
      await waitFor(() =>
        expect(scenePointIds()).toContain(rankedRows[0].id64),
      );
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

    it('removes catalogue fallback result markers when cleared before the response arrives', async () => {
      let finish!: (page: ReturnType<typeof finderPage>) => void;
      vi.mocked(api.searchExploreSystems).mockImplementation(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
      vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue({
        systems: [{ ...anchorA, populated: false }],
        truncated: false,
      });
      // A retained catalogue packet can supply fallback markers while a new
      // foreground search runs; new cell downloads now wait for that search.
      const navigation = renderEnabled('');
      await waitForCatalogueCells();
      navigation.restore('', { anchor: anchorA });
      await waitFor(() =>
        expect(api.searchExploreSystems).toHaveBeenCalledTimes(1),
      );
      await waitFor(() =>
        expect(api.getCatalogueViewportSystems).toHaveBeenCalled(),
      );
      await waitFor(() => expect(currentScene()).toBeDefined());
      if (!enabled) {
        await waitFor(() =>
          expect(
            currentScene().contributions.find(
              ({ id }) => id === 'finder-results',
            )?.layers[0].targetCount,
          ).toBe(1),
        );
      }
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
    vi.mocked(api.getMapHeatmap).mockResolvedValue(legacyFixture);
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
    await waitFor(() => expect(currentScene()).toBeDefined());
    expectEmptyResults();
    expect(api.searchExploreSystems).not.toHaveBeenCalled();
    expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    await fireEvent.click(
      screen.getByRole('radio', { name: 'Manufacturing Hub' }),
    );
    await screen.findByText('Ranked 1');
    expect(
      screen.getByRole('heading', {
        name: 'Galaxy-wide Manufacturing Hub ranking',
      }),
    ).toBeInTheDocument();
    await fireEvent.click(screen.getByRole('radio', { name: 'Any' }));
    await waitFor(expectEmptyResults);
    expect(api.searchExploreSystems).not.toHaveBeenCalled();
    expect(api.getArchetypeRankings).toHaveBeenCalledTimes(1);
  });

  it('clears the default Any anchor without another request and restores it with Back', async () => {
    const history = renderEnabled('', { anchor: anchorA });
    await screen.findByText('Ranked 1');
    await fireEvent.click(
      screen.getByRole('button', { name: 'Clear anchor Anchor A' }),
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
      screen.getByRole('button', { name: 'Clear anchor Anchor A' }),
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
    await waitFor(() => expect(scenePointIds()).toContain(anchorA.id64));
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
    'keeps explanation disclosure and result selection independent with %s activation',
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
      const disclosure = rows[0].querySelector<HTMLButtonElement>(
        '[data-archetype-explanation-toggle]',
      )!;

      expect(disclosure).toBeInstanceOf(HTMLButtonElement);
      expect(disclosure.closest('[data-result-select]')).toBeNull();
      expect(disclosure.closest('[data-system-result]')).toBe(rows[0]);
      expect(api.getArchetypeExplanation).not.toHaveBeenCalled();
      await fireEvent.click(selection[2]);
      await waitFor(() =>
        expect(selection[2]).toHaveAttribute('aria-pressed', 'true'),
      );

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
      expect(selection[0]).toHaveAttribute('aria-pressed', 'false');
      expect(selection[2]).toHaveAttribute('aria-pressed', 'true');
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
      expect(
        within(selection[0]).getByText(/Best Colony Potential: 89/),
      ).toBeVisible();
      expect(
        within(selection[0]).getByText(/Manufacturing Hub fit: 79/),
      ).toBeVisible();
      expect(within(selection[0]).getByText('Tier A')).toBeVisible();

      // Selection has its own keyboard handler and must leave the open
      // disclosure alone, even when the selected row changes.
      selection[1].focus();
      if (activation === 'click') await fireEvent.click(selection[1]);
      else await fireEvent.keyDown(selection[1], { key: activation });
      await waitFor(() =>
        expect(selection[1]).toHaveAttribute('aria-pressed', 'true'),
      );
      expect(disclosure).toHaveAttribute('aria-expanded', 'true');
      expect(region).toBeVisible();
      expect(api.getArchetypeExplanation).toHaveBeenCalledTimes(1);

      await fireEvent.click(disclosure);
      expect(disclosure).toHaveAttribute('aria-expanded', 'false');
      expect(selection[1]).toHaveAttribute('aria-pressed', 'true');
    },
  );

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
    'keeps %s one-row page requests, range, list and map together across three pages',
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

  it('resets offset on tier and page-size commits and resizes the shared list/map page', async () => {
    renderEnabled(
      'archetype=manufacturing_hub&min-tier=A&page_size=1&offset=1',
    );
    await screen.findByText('Ranked 2');
    await fireEvent.change(
      screen.getByRole('combobox', { name: 'Minimum tier' }),
      { target: { value: 'S' } },
    );
    await waitFor(() =>
      expect(api.getArchetypeRankings).toHaveBeenLastCalledWith(
        {
          archetype: 'manufacturing_hub',
          min_score: 88,
          page_size: 1,
          offset: 0,
        },
        expect.anything(),
      ),
    );
    expect(mockedPage.url.searchParams.has('offset')).toBe(false);
    await fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Results per page' }),
      { target: { value: '2' } },
    );
    await screen.findByText('Showing 2 of 3 (results 1–2)');
    expect(api.getArchetypeRankings).toHaveBeenLastCalledWith(
      {
        archetype: 'manufacturing_hub',
        min_score: 88,
        page_size: 2,
        offset: 0,
      },
      expect.anything(),
    );
    expect(
      within(screen.getByRole('list', { name: 'System results' })).getAllByRole(
        'listitem',
      ),
    ).toHaveLength(2);
    await waitFor(() =>
      expect(scenePointIds()).toEqual([rankedRows[0].id64, rankedRows[1].id64]),
    );
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
      ...finderPage(request.from ?? 0, 1, 10_000),
      results: [rankedRows[0]],
    }));
    renderEnabled('page_size=1&offset=2000000000');
    await screen.findByText('Ranked 1');
    expect(mockedGoto).toHaveBeenCalledTimes(1);
    expect(mockedGoto).toHaveBeenCalledWith(
      '/explore?page_size=1&offset=9999',
      expect.objectContaining({ replaceState: true }),
    );
    expect(
      vi.mocked(api.searchExploreSystems).mock.calls.map(([request]) => ({
        from: request.from,
        size: request.size,
      })),
    ).toEqual([{ from: 9999, size: 1 }]);
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
      screen.queryByRole('button', { name: /Clear anchor/ }),
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
    vi.mocked(api.searchExploreSystems).mockImplementation(
      () =>
        new Promise((resolve) => {
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
    expect(
      screen.getByText(/choose Any to search around Anchor A/),
    ).toBeInTheDocument();
    await fireEvent.click(screen.getByRole('radio', { name: 'Any' }));
    await waitFor(() =>
      expect(api.searchExploreSystems).toHaveBeenCalledWith(
        expect.objectContaining({
          reference_coords: { x: 10, y: 20, z: 30 },
          from: 0,
        }),
        expect.anything(),
        { id64: anchorA.id64, name: anchorA.name },
        1,
      ),
    );
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
      screen.getByRole('button', { name: 'Clear anchor Anchor A' }),
    );
    await screen.findByText('Anchor cleared; searching galaxy-wide.');
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
      screen.queryByRole('button', { name: /Clear anchor/ }),
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
    await screen.findByRole('button', { name: 'Clear anchor Anchor A' });
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
      screen.getByRole('button', { name: 'Clear anchor Anchor A' }),
    );
    await waitFor(() => expect(mockedPage.state.anchor).toBeNull());
    expect(mockedPage.url.search).toBe(
      '?archetype=manufacturing_hub&min-tier=A&page_size=1',
    );
    expect(api.searchExploreSystems).not.toHaveBeenCalled();
    expect(screen.getByRole('combobox', { name: 'Minimum tier' })).toHaveValue(
      'A',
    );
  });

  it('exposes the Clear anchor button with no automated accessibility violations', async () => {
    renderEnabled('page_size=1', { anchor: anchorA });
    const clear = await screen.findByRole('button', {
      name: 'Clear anchor Anchor A',
    });
    expect(clear).toHaveTextContent('Clear');
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
    await screen.findByRole('button', { name: 'Clear anchor Anchor A' });
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

  it('result and Babylon picks commit lossless passive selection without opening detail', async () => {
    renderEnabled('page_size=1&view=cards', { anchor: anchorA });
    await screen.findByText('Ranked 1');
    await fireEvent.click(
      screen.getByRole('button', { name: /Ranked 1.*9007199254741001/ }),
    );
    await waitFor(() =>
      expect(mockedPage.url.searchParams.get('selected')).toBe(
        rankedRows[0].id64,
      ),
    );
    expect(mockedPage.url.searchParams.has('system')).toBe(false);
    expect(mockedPage.state.anchor).toEqual(anchorA);
    expect(mockedPage.url.searchParams.get('view')).toBe('cards');
    await waitFor(() => expect(runtimeAdapter.create).toHaveBeenCalled());
    emitRuntime({
      type: 'TARGET_PICKED',
      target: { kind: 'system', systemId64: rankedRows[1].id64 },
    });
    await waitFor(() =>
      expect(mockedPage.url.searchParams.get('selected')).toBe(
        rankedRows[1].id64,
      ),
    );
    expect(mockedPage.url.searchParams.has('system')).toBe(false);
    expect(mockedPage.state.anchor).toEqual(anchorA);
  });

  it('shows raw catalogue hover facts and selects the same known system from its card', async () => {
    const star = {
      ...catalogueFixture.systems[0],
      id64: parseId64('9007199254741200'),
      name: 'Nearby G2 catalogue star',
      x: 13,
      y: 24,
      z: 42,
      mainStarClass: 'G2',
    };
    vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue({
      systems: [star],
      truncated: false,
    });
    renderEnabled('page_size=1&view=cards', { anchor: anchorA });
    await screen.findByText('Ranked 1');
    await waitFor(() => expect(scenePointIds()).toContain(star.id64));

    emitRuntime({
      type: 'TARGET_HOVERED',
      target: { kind: 'system', systemId64: star.id64, name: star.name },
    });
    const tooltip = await screen.findByRole('tooltip', {
      name: `System ${star.name}`,
    });
    expect(screen.getByRole('region', { name: /Galaxy map$/ })).toHaveAttribute(
      'data-hovered-system-id',
      star.id64,
    );
    expect(
      within(tooltip).getByText('G2', { exact: true }),
    ).toBeInTheDocument();
    expect(within(tooltip).queryByText('G class star')).not.toBeInTheDocument();
    expect(
      within(tooltip).getByText('Distance from Anchor A'),
    ).toBeInTheDocument();
    expect(within(tooltip).getByText('13.0 LY')).toBeInTheDocument();

    await fireEvent.click(
      within(tooltip).getByRole('button', { name: 'Select' }),
    );
    await waitFor(() =>
      expect(mockedPage.url.searchParams.get('selected')).toBe(star.id64),
    );
    expect(mockedPage.url.searchParams.has('system')).toBe(false);
    expect(mockedPage.state.anchor).toEqual(anchorA);
    await waitFor(() =>
      expect(currentScene().selection).toEqual([
        { kind: 'system', systemId64: star.id64 },
      ]),
    );
    const selection = document.querySelector(
      `[data-selected-system-card="${star.id64}"]`,
    ) as HTMLElement;
    expect(
      within(selection).getByRole('heading', { name: star.name }),
    ).toBeInTheDocument();
    await fireEvent.click(
      within(selection).getByRole('button', { name: 'Details' }),
    );
    expect(
      within(selection).getByText('G2', { exact: true }),
    ).toBeInTheDocument();
    expect(api.getSystem).not.toHaveBeenCalled();
  });

  it('carries catalogue identity, Finder query and committed anchor on hover-card Inspect navigation', async () => {
    const star = {
      ...catalogueFixture.systems[0],
      id64: parseId64('9007199254741200'),
      name: 'Nearby G2 catalogue star',
      x: 13,
      y: 24,
      z: 42,
      mainStarClass: 'G2',
    };
    vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue({
      systems: [star],
      truncated: false,
    });
    renderEnabled('page_size=1&view=cards', { anchor: anchorA });
    await screen.findByText('Ranked 1');
    await waitFor(() => expect(scenePointIds()).toContain(star.id64));
    const origin = mockedPage.url.searchParams.toString();
    emitRuntime({
      type: 'TARGET_HOVERED',
      target: { kind: 'system', systemId64: star.id64, name: star.name },
    });
    const tooltip = await screen.findByRole('tooltip', {
      name: `System ${star.name}`,
    });
    const inspect = within(tooltip).getByRole('link', { name: 'Inspect' });
    const href = new URL(inspect.getAttribute('href')!, mockedPage.url);
    expect(href.pathname).toBe('/inspect');
    expect(href.searchParams.get('system')).toBe(star.id64);
    mockedGoto.mockClear();
    mockedGoto.mockResolvedValueOnce(undefined);

    await fireEvent.click(inspect);
    await waitFor(() => expect(mockedGoto).toHaveBeenCalledOnce());
    const destination = new URL(mockedGoto.mock.calls[0][0], mockedPage.url);
    expect(destination.pathname).toBe('/inspect');
    expect(destination.searchParams.get('from')).toBe(origin);
    expect(destination.searchParams.get('system')).toBe(star.id64);
    expect(mockedGoto.mock.calls[0][1].state.anchor).toEqual(anchorA);
  });

  it('keeps a selected page point once across pagination without refetching its known coordinates', async () => {
    renderEnabled('page_size=1');
    await expectPage(0);
    await fireEvent.click(
      screen.getByRole('button', { name: /Ranked 1.*9007199254741001/ }),
    );
    await waitFor(() =>
      expect(currentScene().selection).toEqual([
        { kind: 'system', systemId64: rankedRows[0].id64 },
      ]),
    );
    expect(scenePointIds()).toEqual([rankedRows[0].id64]);
    await fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Ranked 2');
    await waitFor(() =>
      expect(scenePointIds().sort()).toEqual(
        [rankedRows[0].id64, rankedRows[1].id64].sort(),
      ),
    );
    expect(api.getSystem).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    await waitFor(() => expect(scenePointIds()).toEqual([rankedRows[0].id64]));
  });

  it('reserves selected, anchor, Finder, pinned and watched positions even when the cell sample is empty', async () => {
    const pinnedId = parseId64('9007199254741200');
    const watchedId = parseId64('9007199254741300');
    pins.set([
      {
        id64: pinnedId,
        name: 'Far pinned target',
        x: 45_000,
        y: -100,
        z: 70_000,
        pinned_at: '2026-11-02T00:00:00Z',
      },
      {
        id64: parseId64('9007199254741400'),
        name: 'Pin without coordinates',
        pinned_at: '2026-11-02T00:00:00Z',
      },
    ]);
    const priorSyncKey = syncKey.read().value;
    syncKey.set({
      state: { syncKey: 'm08watchlistfixture00000000' },
      version: 0,
    });
    vi.mocked(api.getWatchlist).mockImplementation(async (key) => ({
      sync_key: key,
      watchlist: [
        {
          system_id64: watchedId,
          name: 'Far watched target',
          x: -45_000,
          y: 100,
          z: 60_000,
        },
      ],
    }));
    await auth.bootstrap();
    try {
      renderEnabled(`page_size=1&selected=${anchorB.id64}`, {
        anchor: anchorA,
      });
      await screen.findByRole('heading', { name: `Restored ${anchorB.id64}` });
      await waitFor(() => {
        const contribution = currentScene().contributions.find(
          ({ id }) => id === 'catalogue-viewport-stars',
        );
        const payload = contribution?.layers[0]?.payload as
          GalaxySystemsPayload | undefined;
        expect(
          payload?.systems.map((point) => point.systemId64).sort(),
        ).toEqual(
          [
            anchorA.id64,
            anchorB.id64,
            rankedRows[0].id64,
            pinnedId,
            watchedId,
          ].sort(),
        );
        expect(payload?.systems.every((point) => point.reserved === true)).toBe(
          true,
        );
        expect(
          payload?.systems.find((point) => point.systemId64 === pinnedId),
        ).toMatchObject({
          positionLy: { x: 45_000, y: -100, z: 70_000 },
        });
        expect(
          payload?.systems.find((point) => point.systemId64 === watchedId),
        ).toMatchObject({
          positionLy: { x: -45_000, y: 100, z: 60_000 },
        });
      });
      await waitForCatalogueCells();
      expect(api.getCatalogueViewportSystems).toHaveBeenCalled();
      expect(exactStarStatus()).toHaveAttribute(
        'data-catalogue-star-count',
        '5',
      );
    } finally {
      pins.clear();
      syncKey.set(priorSyncKey);
    }
  });

  it.each(['any', 'selected'])(
    'hydrates a clean off-page %s selection from getSystem when the catalogue arrives first',
    async (mode) => {
      let finishResults!: () => void;
      const pendingResults = new Promise<void>((resolve) => {
        finishResults = resolve;
      });
      vi.mocked(api.searchExploreSystems).mockImplementation(async () => {
        await pendingResults;
        return finderPage(1);
      });
      vi.mocked(api.getArchetypeRankings).mockImplementation(async () => {
        await pendingResults;
        return {
          ...finderPage(1, 1, 3, true),
          archetype: 'manufacturing_hub',
          archetype_label: 'Manufacturing Hub',
        };
      });
      let finishSelection!: (detail: api.SystemDetail) => void;
      vi.mocked(api.getSystem).mockImplementation(
        () =>
          new Promise((resolve) => {
            finishSelection = resolve;
          }),
      );
      vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue({
        systems: [{ ...anchorA, populated: false }],
        truncated: false,
      });
      // Retain the catalogue before starting foreground results. New cell
      // downloads yield to results, but a cached point must still never supply
      // the separate Finder selection contribution.
      const navigation = renderEnabled('');
      await waitFor(() =>
        expect(
          document.querySelector('[data-catalogue-star-count]'),
        ).toHaveAttribute('data-catalogue-star-count', '1'),
      );
      navigation.restore(
        `${mode === 'selected' ? 'archetype=manufacturing_hub&' : ''}page_size=1&offset=1&selected=${anchorA.id64}`,
      );
      await waitFor(() =>
        expect(
          mode === 'selected'
            ? api.getArchetypeRankings
            : api.searchExploreSystems,
        ).toHaveBeenCalledOnce(),
      );
      expect(api.getSystem).not.toHaveBeenCalled();
      await waitFor(() =>
        expect(
          currentScene().contributions.some(
            (contribution) => contribution.id === 'finder-selection',
          ),
        ).toBe(false),
      );
      finishResults();
      await screen.findByText('Ranked 2');
      await waitFor(() =>
        expect(api.getSystem).toHaveBeenCalledExactlyOnceWith(
          anchorA.id64,
          expect.anything(),
        ),
      );
      expect(
        screen.getByRole('heading', { name: `System ${anchorA.id64}` }),
      ).toBeInTheDocument();
      expect(
        currentScene().contributions.some(
          (contribution) => contribution.id === 'finder-selection',
        ),
      ).toBe(false);
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
      await waitFor(() =>
        expect(
          currentScene()
            .contributions.filter(
              (contribution) => contribution.id === 'finder-selection',
            )
            .flatMap((contribution) => contribution.layers)
            .map((layer) => layer.payload),
        ).toEqual([
          {
            systems: [
              expect.objectContaining({
                systemId64: anchorA.id64,
                positionLy: { x: 50, y: 60, z: 70 },
              }),
            ],
          },
        ]),
      );
      expect(api.getSystem).toHaveBeenCalledTimes(1);
      expect(currentScene().selection).toEqual([
        { kind: 'system', systemId64: anchorA.id64 },
      ]);
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
    'reconstructs selection from getSystem with $label without inventing a point',
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
      await waitFor(() =>
        expect(currentScene().selection).toEqual([
          { kind: 'system', systemId64: id64 },
        ]),
      );
      expect(scenePointIds()).toEqual(visible ? [id64] : []);
      expect(mockedPage.url.searchParams.get('selected')).toBe(id64);
      expect(mockedPage.url.searchParams.has('system')).toBe(false);
    },
  );

  it('retains passive selection and an empty point set when getSystem fails', async () => {
    mockRankedPages(0);
    vi.mocked(api.getSystem).mockRejectedValue(
      new api.ApiError(503, '/api/system', {}),
    );
    renderEnabled(`page_size=1&selected=${anchorA.id64}`);
    await waitFor(() => expect(api.getSystem).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(currentScene().selection).toEqual([
        { kind: 'system', systemId64: anchorA.id64 },
      ]),
    );
    expect(
      screen.getByRole('heading', { name: `System ${anchorA.id64}` }),
    ).toBeInTheDocument();
    expect(scenePointIds()).toEqual([]);
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
    await waitFor(() => expect(scenePointIds()).toEqual([anchorB.id64]));
    expect(
      screen.queryByRole('heading', { name: 'Late A' }),
    ).not.toBeInTheDocument();
    expect(currentScene().selection).toEqual([
      { kind: 'system', systemId64: anchorB.id64 },
    ]);
  });

  it('clears URL-owned selection when a history entry omits selected while preserving explicit detail', async () => {
    const history = renderEnabled(`page_size=1&selected=${rankedRows[0].id64}`);
    await waitFor(() =>
      expect(currentScene().selection).toEqual([
        { kind: 'system', systemId64: rankedRows[0].id64 },
      ]),
    );
    history.restore(`page_size=1&system=${rankedRows[0].id64}`);
    await waitFor(() => expect(currentScene().selection).toEqual([]));
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
    const picker = screen.getByRole('radio', { name: 'Manufacturing Hub' });
    picker.focus();
    await fireEvent.click(picker);
    await waitFor(() => expect(api.getArchetypeRankings).toHaveBeenCalled());
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
          { name: /Ranked 1.*9007199254741001/ },
        ),
      ).toHaveFocus(),
    );
    expect(mockedPage.state.anchor).toEqual(anchorA);
  });

  it.each(['during', 'after', 'first after'] as const)(
    'bounds initial overview work when density arrives %s its flight',
    async (densityTiming) => {
      const { createBabylonSession } = await vi.importActual<
        typeof import('$lib/spatial/babylon/adapter')
      >('$lib/spatial/babylon/adapter');
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
      // Preserve the real overview extent and validated region contribution,
      // with simple bands so constructor counts do not require production-sized
      // boundary geometry. The adapter and camera state machine remain real.
      const regions = galaxyRegions.buildGalaxyRegionsPayload(
        {
          version: 1,
          origin: { x: -49_985, z: -24_105 },
          pixel_scale: 49.34939759036145,
          regions: ['', ...galaxyRegions.GALAXY_REGION_NAMES],
          regionmap: Array.from({ length: 2_048 }, (_, row) => [
            [2_048, row === 0 ? 0 : Math.min(42, Math.floor(row / 49) + 1)],
          ]),
        },
        galaxyRegions.GALAXY_REGION_SOURCE_SHA256,
      );
      let finishRegions!: (value: galaxyRegions.GalaxyRegionsPayload) => void;
      vi.spyOn(
        galaxyRegions,
        'fetchAuthoritativeGalaxyRegions',
      ).mockImplementation(
        () => new Promise((resolve) => (finishRegions = resolve)),
      );
      vi.spyOn(galaxyNebulae, 'fetchGalaxyNebulae').mockRejectedValue(
        new Error('No nebulae in this fixture'),
      );
      let finishDensity!: (value: api.MapHeatmapResponse) => void;
      const nextDensity = new Promise<api.MapHeatmapResponse>(
        (resolve) => (finishDensity = resolve),
      );
      vi.mocked(api.getMapHeatmap).mockImplementation((request) =>
        densityTiming === 'first after'
          ? nextDensity
          : request.voxel_size === 590
            ? Promise.resolve(pyramidFixture)
            : densityTiming === 'during'
              ? Promise.resolve(coarsePyramidFixture)
              : nextDensity,
      );
      vi.stubGlobal('matchMedia', () => ({ matches: false }));

      const engine = createHeadlessEngine();
      let animationTime = 0;
      const session = createBabylonSession(
        engine,
        'WEBGL2',
        () => animationTime,
      );
      const events = vi.fn<(event: RuntimeEvent) => void>();
      const sceneCreated = vi.fn();
      engine.onNewSceneAddedObservable.add((scene) => {
        sceneCreated();
        // NullEngine cannot provide GPU readiness; count actual scene creation
        // without shader warmup or drawing, and advance real camera frames below.
        vi.spyOn(scene, 'render').mockImplementation(() => undefined);
        vi.spyOn(scene, 'isReady').mockReturnValue(true);
      });
      const frames = new Map<number, () => void>();
      let nextFrame = 0;
      runtimeAdapter.create.mockImplementation(
        (_canvas, onStatus: (status: SpatialRuntimeStatus) => void) => {
          const runtime = createManagedSpatialRuntime(
            {
              createWebGpu: async () => null,
              createWebGl2: () => session,
            },
            onStatus,
            {
              request(callback) {
                frames.set(++nextFrame, callback);
                return nextFrame;
              },
              cancel(handle) {
                frames.delete(handle);
              },
            },
          );
          runtime.subscribe(events);
          vi.spyOn(runtime, 'dispatch');
          return runtime;
        },
      );
      const work = () => {
        const runtime = runtimeAdapter.create.mock.results.at(-1)!.value;
        const commands = runtime.dispatch.mock.calls.map(
          ([command]: [RuntimeCommand]) => command,
        ) as RuntimeCommand[];
        return {
          loads: commands.filter((command) => command.type === 'LOAD_SCENE')
            .length,
          scenes: sceneCreated.mock.calls.length,
          transitions: commands.filter(
            (command) =>
              command.type === 'FLY_TO' ||
              (command.type === 'SET_CAMERA' && command.transition),
          ).length,
        };
      };
      const finishFrames = async () => {
        for (let index = 0; frames.size && index < 150; index += 1) {
          animationTime += 1_000;
          const callbacks = [...frames.values()];
          frames.clear();
          callbacks.forEach((callback) => callback());
          await tick();
        }
        expect(frames.size).toBe(0);
      };

      const navigation = renderEnabled();
      await openLayers();
      await vi.waitFor(
        () => {
          expect(screen.getByText('Ranked 1')).toBeInTheDocument();
          if (densityTiming !== 'first after')
            expect(screen.getByText(/2 density cells/)).toBeInTheDocument();
          expect(scenePointIds()).toEqual([rankedRows[0].id64]);
        },
        { interval: 1 },
      );
      await finishFrames();
      expect(work()).toEqual({ loads: 1, scenes: 1, transitions: 0 });
      finishRegions(regions);
      await vi.waitFor(() => expect(work().transitions).toBe(1), {
        interval: 1,
      });
      expect(work()).toEqual({ loads: 2, scenes: 2, transitions: 1 });

      vi.mocked(api.searchExploreSystems).mockResolvedValueOnce({
        ...finderPage(0),
        results: [rankedRows[1]],
      });
      await queryClient.invalidateQueries({
        queryKey: [...queryKeys.all, 'explore', 'results'],
      });
      await vi.waitFor(
        () => expect(screen.getByText('Ranked 2')).toBeInTheDocument(),
        { interval: 1 },
      );
      expect(work()).toEqual({ loads: 2, scenes: 2, transitions: 1 });
      // The 180 ms viewport debounce fires during the 650 ms overview flight.
      // Both a prompt density reply and one arriving after settle must stay
      // within the pre-part-3 scene/command budget.
      await vi.advanceTimersByTimeAsync(180);
      await finishFrames();
      if (densityTiming !== 'during') finishDensity(coarsePyramidFixture);
      await vi.advanceTimersByTimeAsync(180);
      await finishFrames();
      await waitForCatalogueCells();
      await finishFrames();
      expect(scenePointIds()).toEqual([rankedRows[1].id64]);
      expect(work()).toEqual({ loads: 3, scenes: 3, transitions: 1 });
      expect(
        events.mock.calls.filter(
          ([event]) => event.type === 'TRANSITION_FINISHED',
        ),
      ).toHaveLength(1);
      expect(currentScene().camera.distanceLy).toBeGreaterThan(100_000);
      expect(
        engine.scenes.at(-1)?.getMeshByName('catalogue-density-cells')?.metadata
          .spatialLayer.renderedCellCount,
      ).toBe(1);
      const cellRequests = vi.mocked(api.getCatalogueViewportSystems).mock
        .calls;
      expect(cellRequests.length).toBeGreaterThan(0);
      expect(cellRequests.length).toBeLessThanOrEqual(16);
      expect(
        cellRequests.every(
          ([request]) => request.limit === GALAXY_WIDE_STAR_LIMIT,
        ),
      ).toBe(true);
      expect(api.getCatalogueViewportSystems).toHaveBeenCalledWith(
        expect.objectContaining({ limit: GALAXY_WIDE_STAR_LIMIT }),
        expect.anything(),
      );
      // Even a first density response after settle must keep the same overlay
      // order as the parent's next scene. Selection can then update its marker
      // in place instead of rebuilding every mesh after a late density patch.
      const settledCamera = currentScene().camera;
      navigation.restore(`page_size=1&selected=${rankedRows[1].id64}`);
      await vi.waitFor(
        () =>
          expect(currentScene().selection).toContainEqual({
            kind: 'system',
            systemId64: rankedRows[1].id64,
          }),
        { interval: 1 },
      );
      await finishFrames();
      expect(work()).toEqual({ loads: 4, scenes: 3, transitions: 1 });
      expect(currentScene().camera).toEqual(settledCamera);
      expect(
        engine.scenes.at(-1)?.getMeshByName('selected-system-marker'),
      ).toBeTruthy();
    },
  );

  it('keeps F4 results and the settled selection camera when a refetch arrives during fly-to', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }));
    renderEnabled('page_size=2', { anchor: anchorA });
    await screen.findByText('Showing 2 of 3 (results 1–2)');
    await waitFor(() =>
      expect(scenePointIds()).toEqual([
        rankedRows[0].id64,
        rankedRows[1].id64,
        anchorA.id64,
      ]),
    );

    const runtime = runtimeAdapter.create.mock.results.at(-1)!.value;
    // Match the real adapter's synchronous camera receipt on scene replacement.
    // The test controls the animation's completion while Finder data is pending.
    runtime.dispatch.mockImplementation((command: RuntimeCommand) => {
      if (command.type === 'LOAD_SCENE')
        emitRuntime({ type: 'CAMERA_CHANGED', camera: command.scene.camera });
      return { status: 'executed' as const };
    });
    const overviewCamera = {
      ...currentScene().camera,
      distanceLy: 118_000,
      revision: 10,
    };
    emitRuntime({ type: 'CAMERA_CHANGED', camera: overviewCamera });
    const target = {
      kind: 'system' as const,
      systemId64: rankedRows[0].id64,
    };
    await fireEvent.click(
      within(screen.getByRole('list', { name: 'System results' })).getByRole(
        'button',
        { name: /Ranked 1.*9007199254741001/ },
      ),
    );
    await waitFor(() =>
      expect(runtime.dispatch).toHaveBeenCalledWith({
        type: 'FLY_TO',
        target,
        reducedMotion: false,
      }),
    );
    expect(mockedPage.url.searchParams.get('selected')).toBe(target.systemId64);
    expect(mockedPage.state.anchor).toEqual(anchorA);

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
    expect(scenePointIds()).toEqual([
      rankedRows[0].id64,
      rankedRows[1].id64,
      anchorA.id64,
    ]);
    expect(screen.getByText('Ranked 2')).toBeInTheDocument();
    finish({
      ...finderPage(0, 2),
      results: [rankedRows[0], rankedRows[2]],
    });
    await refetch;
    await screen.findByText('Ranked 3');

    const settledCamera = {
      ...overviewCamera,
      focusLy: { x: 10, y: 1, z: 20 },
      distanceLy: 60,
      revision: 11,
    };
    emitRuntime({ type: 'CAMERA_CHANGED', camera: settledCamera });
    emitRuntime({ type: 'TRANSITION_FINISHED', target });

    await waitFor(() => expect(currentScene().camera).toEqual(settledCamera));
    expect(scenePointIds()).toEqual([
      rankedRows[0].id64,
      rankedRows[2].id64,
      anchorA.id64,
    ]);
    expect(currentScene().selection).toContainEqual(target);
    expect(document.querySelector('[data-camera-distance]')).toHaveAttribute(
      'data-camera-distance',
      '60',
    );
    // Label layout deliberately follows the settled camera debounce.
    await waitFor(() =>
      expect(
        document.querySelector(
          `[data-map-label-key="system:${target.systemId64}"]`,
        ),
      ).toHaveTextContent('Ranked 1'),
    );
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
    const picker = screen.getByRole('radio', { name: 'Manufacturing Hub' });
    picker.focus();
    await fireEvent.click(picker);
    await waitFor(() => expect(api.getArchetypeRankings).toHaveBeenCalled());
    finishAnchor(finderPage(0));
    await waitFor(() => expect(picker).toHaveFocus());
    mockRankedPages();
    const any = screen.getByRole('radio', { name: 'Any' });
    any.focus();
    await fireEvent.click(any);
    await waitFor(() =>
      expect(
        screen.getByRole('region', { name: 'Near Anchor A' }),
      ).toHaveAttribute('aria-busy', 'false'),
    );
    expect(any).toHaveFocus();
  });
});

describe('ExploreWorkspace travel heatmap (V3 journal store)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    stubRuntime();
    mockFacadeDefaults();
    vi.mocked(api.getMapHeatmap).mockResolvedValue(legacyFixture);
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

  async function showTravel(): Promise<HTMLElement> {
    await openLayers();
    const journeys = screen.getByRole('switch', { name: 'My journeys' });
    if (journeys.getAttribute('aria-checked') !== 'true')
      await fireEvent.click(journeys);
    return screen.findByText(
      (_, element) =>
        element?.hasAttribute('data-commander-history-state') ?? false,
    );
  }

  it('asks a signed-out visitor to sign in and never requests travel', async () => {
    await auth.bootstrap();
    render(ExploreWorkspaceTestHost);
    await screen.findByText(/exact stars/);

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
    await screen.findByText(/exact stars/);

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

  it('reads the signed-in account travel from the V3 route and counts it', async () => {
    await signIn();
    vi.mocked(api.getCommanderTravelHeatmap).mockResolvedValue({
      mode: 'markers',
      visits: [
        {
          kind: 'marker',
          systemId64: null,
          systemName: 'Sol',
          x: 0,
          y: 0,
          z: 0,
          visitCount: 3,
          firstVisitedAt: '2026-01-01T00:00:00Z',
          lastVisitedAt: '2026-01-02T00:00:00Z',
          cellSizeLy: null,
        },
      ],
      count: 1,
      truncated: false,
    });
    render(ExploreWorkspaceTestHost);
    await screen.findByText(/exact stars/);

    const status = await showTravel();

    await waitFor(() =>
      expect(status).toHaveAttribute('data-commander-history-state', 'ready'),
    );
    expect(status).toHaveAttribute('data-commander-history-count', '1');
    expect(status.textContent).toMatch(/1 visited\s+systems/u);
    expect(
      within(screen.getByLabelText('My journeys visit-count legend')).getByText(
        '3 visits',
      ),
    ).toBeInTheDocument();
    const [request, distance] = vi.mocked(api.getCommanderTravelHeatmap).mock
      .calls[0]!;
    expect(request).toEqual(
      expect.objectContaining({
        minX: expect.any(Number),
        limit: expect.any(Number),
      }),
    );
    expect(distance).toBeGreaterThan(0);
  });

  it('requests the whole journey galaxy at overview independently of catalogue cells', async () => {
    await signIn();
    const distantVisit = {
      kind: 'density' as const,
      x: 25_000,
      y: 4_000,
      z: 60_000,
      visitCount: 3,
      firstVisitedAt: '2026-01-01T00:00:00Z',
      lastVisitedAt: '2026-01-02T00:00:00Z',
      cellSizeLy: 5_000,
    };
    vi.mocked(api.getCommanderTravelHeatmap).mockImplementation(
      async (request) => {
        const visits =
          distantVisit.x >= request.minX &&
          distantVisit.x <= request.maxX &&
          distantVisit.y >= request.minY &&
          distantVisit.y <= request.maxY &&
          distantVisit.z >= request.minZ &&
          distantVisit.z <= request.maxZ
            ? [distantVisit]
            : [];
        return {
          mode: 'density',
          visits,
          count: visits.length,
          truncated: false,
        };
      },
    );
    render(ExploreWorkspaceTestHost);
    await waitForCatalogueCells();
    const status = await showTravel();

    await waitFor(() =>
      expect(status).toHaveAttribute('data-commander-history-count', '1'),
    );
    expect(api.getCommanderTravelHeatmap).toHaveBeenCalledWith(
      { ...COMMANDER_HISTORY_GALAXY_BOUNDS, limit: 20_000 },
      118_000,
      expect.anything(),
    );
    const catalogueRequests = vi.mocked(api.getCatalogueViewportSystems).mock
      .calls;
    expect(catalogueRequests.length).toBeGreaterThan(0);
    for (const [request] of catalogueRequests) {
      // Whole edge cells can extend beyond the 15,000-LY footprint window,
      // but catalogue requests remain local and never absorb distant travel.
      expect(request.maxX - request.minX).toBeLessThanOrEqual(15_000);
      expect(request.maxZ - request.minZ).toBeLessThanOrEqual(15_000);
      expect(request.maxX).toBeLessThan(distantVisit.x);
      expect(request.maxZ).toBeLessThan(distantVisit.z);
    }
    await waitFor(() =>
      expect(
        currentScene().contributions.find(
          (contribution) => contribution.id === 'commander-history',
        )?.layers[0].payload,
      ).toEqual(
        expect.objectContaining({
          points: [
            expect.objectContaining({
              positionLy: { x: 25_000, y: 4_000, z: 60_000 },
            }),
          ],
        }),
      ),
    );
  });

  it('debounces journey camera changes before requesting the final zoomed footprint', async () => {
    await signIn();
    render(ExploreWorkspaceTestHost);
    await showTravel();
    await waitFor(() =>
      expect(api.getCommanderTravelHeatmap).toHaveBeenCalledTimes(1),
    );
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
    const camera = {
      ...currentScene().camera,
      focusLy: { x: 21_000, y: 3_500, z: 60_000 },
      distanceLy: 500,
      projection: 'orthographic' as const,
      pitchRad: Math.PI / 2,
      revision: 1,
    };
    emitRuntime({ type: 'CAMERA_CHANGED', camera });
    await vi.advanceTimersByTimeAsync(100);
    emitRuntime({
      type: 'CAMERA_CHANGED',
      camera: { ...camera, distanceLy: 100, revision: 2 },
    });
    await vi.advanceTimersByTimeAsync(179);
    expect(api.getCommanderTravelHeatmap).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await vi.waitFor(() =>
      expect(api.getCommanderTravelHeatmap).toHaveBeenCalledTimes(2),
    );
    const [request, distance] = vi.mocked(api.getCommanderTravelHeatmap).mock
      .calls[1];
    expect(distance).toBe(100);
    expect(request.minX).toBeGreaterThan(20_900);
    expect(request.maxX).toBeLessThan(21_100);
    expect(request.minZ).toBeGreaterThan(59_900);
    expect(request.maxZ).toBeLessThan(60_100);
    expect(request.minY).toBe(3_466);
    expect(request.maxY).toBe(3_534);
    expect(request.limit).toBe(20_000);
  });

  it('distinguishes an account without imported journals from an empty map viewport', async () => {
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

  it('retries unavailable journal import metadata independently from the travel viewport', async () => {
    await signIn();
    vi.mocked(api.getCommanderJournalSummary).mockRejectedValue(
      new api.ApiError(503, '/api/v1/journal/summary', {}),
    );
    render(ExploreWorkspaceTestHost);
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
    const viewportCalls = vi.mocked(api.getCommanderTravelHeatmap).mock.calls
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
    expect(api.getCommanderTravelHeatmap).toHaveBeenCalledTimes(viewportCalls);
  });

  it.each([false, true])(
    'removes cached private visits when the journal summary reports an expired session (F4=%s)',
    async (enabled) => {
      featureGate.enabled = enabled;
      await signIn();
      vi.mocked(api.getCommanderTravelHeatmap).mockResolvedValue({
        mode: 'markers',
        visits: [
          {
            kind: 'marker',
            systemId64: null,
            systemName: 'Sol',
            x: 0,
            y: 0,
            z: 0,
            visitCount: 3,
            firstVisitedAt: '2026-01-01T00:00:00Z',
            lastVisitedAt: '2026-01-02T00:00:00Z',
            cellSizeLy: null,
          },
        ],
        count: 1,
        truncated: false,
      });
      render(ExploreWorkspaceTestHost);
      const status = await showTravel();
      await waitFor(() =>
        expect(currentScene().contributions).toContainEqual(
          expect.objectContaining({ id: 'commander-history' }),
        ),
      );
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
      expect(
        screen.queryByLabelText('My journeys visit-count legend'),
      ).not.toBeInTheDocument();
      expect(currentScene().contributions).not.toContainEqual(
        expect.objectContaining({ id: 'commander-history' }),
      );
    },
  );

  it('shows an honest empty state and a sign-in prompt on a 401', async () => {
    await signIn();
    render(ExploreWorkspaceTestHost);
    await screen.findByText(/exact stars/);
    const status = await showTravel();
    await waitFor(() =>
      expect(status).toHaveAttribute('data-commander-history-state', 'empty'),
    );
    expect(status.textContent).toContain(
      'No imported journal travel in this view',
    );
    cleanup();
    queryClient.clear();

    vi.mocked(api.getCommanderTravelHeatmap).mockRejectedValue(
      new api.ApiError(401, '/api/v1/journal/travel-heatmap', {}),
    );
    render(ExploreWorkspaceTestHost);
    await screen.findByText(/exact stars/);
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
      vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
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

      const scope = 'Within 500 LY of Achenar · sorted by distance';
      expect(document.getElementById('results-scope')).toHaveTextContent(scope);
      const list = screen.getByRole('list', { name: 'System results' });
      expect(list).toHaveAttribute('aria-describedby', 'results-scope');
      expect(list).toHaveAccessibleDescription(scope);
      expect(within(list).getAllByRole('listitem')[0]).toHaveTextContent(
        enabled ? '12.50 LY · within 500 LY' : '12.5 ly · within 500 LY',
      );
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
        'Search radius works when you search near a system with Any selected.',
      );
    });

    it('commits one radius search, resets pagination and restores its URL on reload', async () => {
      renderEnabled(
        `${enabled ? 'page_size=1&offset=1&' : ''}selected=${anchorA.id64}&view=cards&near=${anchorA.id64}`,
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

    it('caps a filtered search at the API radius and resumes the chosen radius once filters clear', async () => {
      // The API rejects anchored searches with body filters beyond 500 LY, so
      // the wider options are disabled and a wider URL radius is applied as 500.
      renderEnabled(
        `${enabled ? 'page_size=1&' : ''}near=${anchorA.id64}&bodies=elw_count%3A1-&radius=1000`,
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

    it('keeps each card distance tied to its response radius while the next radius is loading', async () => {
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
      renderEnabled(`near=${anchorA.id64}`, { anchor: anchorA });
      await screen.findByText('Ranked 1');
      const list = screen.getByRole('list', { name: 'System results' });
      expect(within(list).getAllByRole('listitem')[0]).toHaveTextContent(
        'within 500 LY',
      );

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
          'within 500 LY',
        );
        expect(retained).not.toHaveTextContent('within 1,000 LY');
      }

      finish(response);
      await waitFor(() =>
        expect(
          within(
            screen.getByRole('list', { name: 'System results' }),
          ).getAllByRole('listitem')[0],
        ).toHaveTextContent('within 1,000 LY'),
      );
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(2);
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
        'Within 500 LY of Anchor A · sorted by distance',
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
        expect(screen.getByText('Paused')).toBeInTheDocument();
        expect(
          screen.getByRole('button', { name: 'Body filters' }),
        ).toBeDisabled();
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
        expect(screen.getByText('Paused')).toBeInTheDocument();
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
      expect(screen.getByText('Paused')).toBeInTheDocument();
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

    it('keeps body-only links paused with no request, and preserves filters when clearing the anchor', async () => {
      const history = renderEnabled('bodies=elw_count:1-');
      const trigger = screen.getByRole('button', { name: 'Body filters' });
      expect(trigger).toBeDisabled();
      expect(trigger).toHaveAccessibleDescription(
        'Body filters work when you search near a system with Any selected.',
      );
      expect(api.searchExploreSystems).not.toHaveBeenCalled();
      history.restore(`near=${anchorA.id64}&bodies=elw_count:1-`, {
        anchor: anchorA,
      });
      await screen.findByText('Ranked 1');
      await fireEvent.click(
        screen.getByRole('button', { name: 'Clear anchor Anchor A' }),
      );
      await waitFor(() => expect(trigger).toBeDisabled());
      expect(mockedPage.url.searchParams.has('near')).toBe(false);
      expect(mockedPage.url.searchParams.get('bodies')).toBe('elw_count:1-');
      expect(screen.getByText('Paused')).toBeInTheDocument();
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
    vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
  });
  afterEach(() => {
    cleanup();
    featureGate.enabled = false;
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it.each([
    { tier: 'B', query: '', anchor: null },
    { tier: 'A', query: '&min-tier=A', anchor: anchorA },
  ])(
    'describes galaxy-wide archetype scope at tier $tier and disables the retained radius',
    async ({ tier, query, anchor }) => {
      renderEnabled(
        `archetype=manufacturing_hub&radius=1000${query}${anchor ? `&near=${anchor.id64}` : ''}`,
        { anchor },
      );
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
        'Search radius works when you search near a system with Any selected.',
      );
      expect(vi.mocked(api.getArchetypeRankings).mock.calls[0][0]).toEqual({
        archetype: 'manufacturing_hub',
        min_score: tier === 'B' ? 60 : 76,
        page_size: 50,
        offset: 0,
      });
      expect(api.searchExploreSystems).not.toHaveBeenCalled();
    },
  );

  it('keeps a ranking on its page when a paused chip is removed or cleared', async () => {
    renderEnabled(
      `archetype=manufacturing_hub&page_size=1&offset=1&near=${anchorA.id64}&bodies=elw_count:1-,ww_count:2-`,
      { anchor: anchorA },
    );
    await screen.findByText('Ranked 2');
    expect(screen.getAllByText('Paused')).toHaveLength(2);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Remove Earth-like filter' }),
    );
    await waitFor(() =>
      expect(mockedPage.url.searchParams.get('bodies')).toBe('ww_count:2-'),
    );
    expect(mockedPage.url.searchParams.get('offset')).toBe('1');
    await fireEvent.click(screen.getByRole('button', { name: 'Clear all' }));
    await waitFor(() =>
      expect(mockedPage.url.searchParams.has('bodies')).toBe(false),
    );
    expect(mockedPage.url.searchParams.get('offset')).toBe('1');
    expect(screen.getByText('Ranked 2')).toBeInTheDocument();
    expect(api.searchExploreSystems).not.toHaveBeenCalled();
  });

  it('pauses filters for selected rankings and restores them on Any with one anchored request', async () => {
    renderEnabled(
      `archetype=manufacturing_hub&near=${anchorA.id64}&bodies=elw_count:1-`,
      { anchor: anchorA },
    );
    await screen.findByText('Ranked 1');
    const trigger = screen.getByRole('button', { name: 'Body filters' });
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveAccessibleDescription(
      'Body filters work when you search near a system with Any selected.',
    );
    expect(screen.getByText('Earth-like ≥ 1')).toBeInTheDocument();
    expect(screen.getByText('Paused')).toBeInTheDocument();
    expect(
      screen
        .getByRole('group', { name: 'Paused body filters' })
        .querySelector('.kit-chip'),
    ).toHaveTextContent('Paused');
    expect(api.searchExploreSystems).not.toHaveBeenCalled();
    expect(
      vi.mocked(api.getArchetypeRankings).mock.calls[0][0],
    ).not.toHaveProperty('body_filters');
    await fireEvent.click(screen.getByRole('radio', { name: 'Any' }));
    await waitFor(() =>
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1),
    );
    expect(
      vi.mocked(api.searchExploreSystems).mock.calls[0][0].body_filters,
    ).toEqual({ elw_count: { min: 1 } });
    expect(mockedPage.url.searchParams.get('bodies')).toBe('elw_count:1-');
    expect(trigger).toBeEnabled();
    expect(screen.queryByText('Paused')).not.toBeInTheDocument();
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
      vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
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

    it('replays an unfinished focus request after remount and stops replaying it once completed', async () => {
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
      await waitFor(() => expect(currentScene()).toBeDefined());
      const firstRuntime = runtimeAdapter.create.mock.results[0].value;
      // The first layout disappears while the renderer still owes its flight
      // completion receipt. The new layout must finish that explicit intent.
      firstRuntime.dispatch.mockImplementation(() => ({
        status: 'executed' as const,
      }));
      const target = {
        kind: 'system' as const,
        systemId64: rankedRows[1].id64,
      };
      await fireEvent.click(
        within(list).getByRole('button', {
          name: /Ranked 2.*9007199254741002/,
        }),
      );
      await waitFor(() =>
        expect(firstRuntime.dispatch).toHaveBeenCalledWith({
          type: 'FLY_TO',
          target,
          reducedMotion: true,
        }),
      );
      expect(viewState.completedFocusRevision).toBeLessThan(
        viewState.focusRevision,
      );

      await host.rerender({ mounted: false });
      await host.rerender({ mounted: true });
      await waitFor(() =>
        expect(runtimeAdapter.create).toHaveBeenCalledTimes(2),
      );
      const secondRuntime = runtimeAdapter.create.mock.results[1].value;
      await waitFor(() =>
        expect(secondRuntime.dispatch).toHaveBeenCalledWith({
          type: 'FLY_TO',
          target,
          reducedMotion: true,
        }),
      );
      expect(
        secondRuntime.dispatch.mock.calls.filter(
          ([command]: [RuntimeCommand]) => command.type === 'FLY_TO',
        ),
      ).toHaveLength(1);
      await waitFor(() =>
        expect(viewState.completedFocusRevision).toBe(viewState.focusRevision),
      );

      await host.rerender({ mounted: false });
      await host.rerender({ mounted: true });
      await waitFor(() =>
        expect(runtimeAdapter.create).toHaveBeenCalledTimes(3),
      );
      await waitFor(() => expect(currentScene()).toBeDefined());
      expect(
        runtimeCommands().filter((command) => command.type === 'FLY_TO'),
      ).toHaveLength(0);
      expect(document.querySelectorAll('canvas')).toHaveLength(1);
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
    });

    it('retains the draft, committed search, selection, scroll and Galaxy context without repeating a stale search', async () => {
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
      });
      const list = screen.getByRole('list', { name: 'System results' });
      await fireEvent.click(
        within(list).getByRole('button', {
          name: /Ranked 2.*9007199254741002/,
        }),
      );
      await waitFor(() =>
        expect(currentScene().selection).toContainEqual({
          kind: 'system',
          systemId64: rankedRows[1].id64,
        }),
      );
      emitRuntime({
        type: 'TARGET_PICKED',
        target: { kind: 'system', systemId64: rankedRows[1].id64 },
      });

      const layers = within(await openLayers());
      await fireEvent.click(layers.getByRole('switch', { name: 'Nebulae' }));
      await fireEvent.click(
        layers.getByRole('switch', { name: 'Known-system density' }),
      );
      await fireEvent.click(
        layers.getByRole('switch', { name: 'My journeys' }),
      );
      await fireEvent.click(
        layers.getByRole('button', { name: 'Close map layers' }),
      );

      const camera = {
        ...currentScene().camera,
        focusLy: { x: 100, y: 20, z: 300 },
        distanceLy: 400,
        bearingRad: 0.25,
        pitchRad: 0.65,
        revision: 17,
      };
      emitRuntime({ type: 'CAMERA_CHANGED', camera });
      await waitFor(() => expect(viewState.streamCamera).toEqual(camera));
      expect(viewState.starDetailMode).toBe('local');
      // Region state belongs to the retained Galaxy context even while its
      // independently loaded geometry is unavailable during a layout remount.
      viewState.selectedRegionId = '1';
      emitRuntime({
        type: 'TARGET_HOVERED',
        target: { kind: 'region', id: '2' },
      });
      await fireEvent.input(
        screen.getByRole('combobox', { name: 'System name' }),
        { target: { value: 'Unsubmitted next destination' } },
      );
      list.scrollTop = 256;
      await fireEvent.scroll(list);
      expect(viewState.resultScrollTop).toBe(256);
      expect(viewState.lastPickedId64).toBe(rankedRows[1].id64);
      expect(viewState.focusTarget).toEqual({
        kind: 'system',
        systemId64: rankedRows[1].id64,
      });
      const focusRevision = viewState.focusRevision;
      expect(focusRevision).toBeGreaterThan(0);
      const resultText = list.textContent;
      const committedUrl = mockedPage.url.href;
      const firstRuntime = runtimeAdapter.create.mock.results[0].value;
      expect(document.querySelectorAll('canvas')).toHaveLength(1);
      expect(runtimeAdapter.create).toHaveBeenCalledTimes(1);
      expect(firstRuntime.subscribe).toHaveBeenCalledTimes(1);

      await host.rerender({ mounted: false });
      expect(document.querySelectorAll('canvas')).toHaveLength(0);
      expect(firstRuntime.dispose).toHaveBeenCalledTimes(1);
      expect(
        firstRuntime.subscribe.mock.results[0].value,
      ).toHaveBeenCalledTimes(1);
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
      await waitFor(() =>
        expect(runtimeAdapter.create).toHaveBeenCalledTimes(2),
      );
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
          name: /Ranked 2.*9007199254741002/,
        }),
      ).toHaveAttribute('aria-pressed', 'true');
      expect(mockedPage.url.href).toBe(committedUrl);
      expect(viewState.lastPickedId64).toBe(rankedRows[1].id64);
      expect(viewState.focusRevision).toBe(focusRevision);
      expect(viewState.focusTarget).toEqual({
        kind: 'system',
        systemId64: rankedRows[1].id64,
      });
      expect(viewState.retainedCamera?.camera).toEqual(camera);
      expect(viewState.streamCamera).toEqual(camera);
      expect(viewState.starDetailMode).toBe('local');
      expect(viewState.selectedRegionId).toBe('1');
      expect(viewState.hoveredRegionId).toBe('2');
      expect(currentScene().camera).toEqual(camera);
      expect(document.querySelectorAll('canvas')).toHaveLength(1);
      const secondRuntime = runtimeAdapter.create.mock.results[1].value;
      expect(secondRuntime.subscribe).toHaveBeenCalledTimes(1);
      expect(secondRuntime.dispose).not.toHaveBeenCalled();
      const restoredLayers = within(await openLayers());
      expect(
        restoredLayers.getByRole('switch', { name: 'Nebulae' }),
      ).toHaveAttribute('aria-checked', 'false');
      expect(
        restoredLayers.getByRole('switch', { name: 'Known-system density' }),
      ).toHaveAttribute('aria-checked', 'false');
      expect(
        restoredLayers.getByRole('switch', { name: 'My journeys' }),
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
      });
      await host.rerender({ mounted: false });
      expect(firstRuntime.dispose).toHaveBeenCalledTimes(1);
      expect(secondRuntime.dispose).toHaveBeenCalledTimes(1);
      expect(
        secondRuntime.subscribe.mock.results[0].value,
      ).toHaveBeenCalledTimes(1);
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
        const host = renderEnabled(
          `page_size=3&near=${anchorA.id64}`,
          { anchor: anchorA },
          viewState,
        );
        await screen.findByText('Ranked 1');
        const list = screen.getByRole('list', {
          name: 'System results',
        });
        const resultText = list.textContent;
        expect(within(list).getAllByRole('listitem')).toHaveLength(3);
        await fireEvent.click(
          screen.getByRole('radio', { name: 'Manufacturing Hub' }),
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
          screen.getByRole('radio', { name: 'Manufacturing Hub' }),
        ).toBeChecked();
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

      it('retains a selected archetype, tier and page with paused filters without repeating rankings', async () => {
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
        expect(api.getArchetypeRankings).toHaveBeenCalledTimes(1);
        expect(vi.mocked(api.getArchetypeRankings).mock.calls[0][0]).toEqual({
          archetype: 'manufacturing_hub',
          min_score: 76,
          page_size: 2,
          offset: 1,
        });
        expect(screen.getByRole('combobox', { name: 'Within' })).toHaveValue(
          '500',
        );
        expect(viewState.radius).toBe(1000);
        const list = screen.getByRole('list', { name: 'System results' });
        await fireEvent.click(
          within(list).getByRole('button', {
            name: /Ranked 2.*9007199254741002/,
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
          .filter((cached) => cached.queryKey[2] === 'rankings');
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
          screen.getByRole('radio', { name: 'Manufacturing Hub' }),
        ).toBeChecked();
        expect(
          screen.getByRole('combobox', { name: 'Minimum tier' }),
        ).toHaveValue('A');
        expect(
          screen.getByRole('combobox', { name: 'System name' }),
        ).toHaveValue('Another unsubmitted destination');
        expect(screen.getByRole('combobox', { name: 'Within' })).toHaveValue(
          '500',
        );
        expect(viewState.radius).toBe(1000);
        expect(screen.getByRole('combobox', { name: 'Within' })).toBeDisabled();
        expect(screen.getByText('Earth-like ≥ 1')).toBeInTheDocument();
        expect(screen.getByText('Paused')).toBeInTheDocument();
        expect(restoredList.scrollTop).toBe(128);
        expect(
          within(restoredList).getByRole('button', {
            name: /Ranked 2.*9007199254741002/,
          }),
        ).toHaveAttribute('aria-pressed', 'true');
        expect(viewState.rankingState).toEqual(ranking);
        expect(mockedPage.url.href).toBe(committedUrl);
        expect(document.querySelectorAll('canvas')).toHaveLength(1);
        expect(api.getArchetypeRankings).toHaveBeenCalledTimes(1);
        expect(api.searchExploreSystems).not.toHaveBeenCalled();

        await fireEvent.click(screen.getByRole('radio', { name: 'Any' }));
        await waitFor(() =>
          expect(api.searchExploreSystems).toHaveBeenCalledTimes(1),
        );
        expect(api.getArchetypeRankings).toHaveBeenCalledTimes(1);
        expect(
          vi.mocked(api.searchExploreSystems).mock.lastCall?.[0].body_filters,
        ).toEqual({ elw_count: { min: 1 } });
      });
    }
  },
);

describe.each([false, true])(
  'Full-window map beside the Finder with F4=%s',
  (enabled) => {
    function regionFixture() {
      return galaxyRegions.buildGalaxyRegionsPayload(
        {
          version: 1,
          origin: { x: -49_985, z: -24_105 },
          pixel_scale: 49.34939759036145,
          regions: ['', ...galaxyRegions.GALAXY_REGION_NAMES],
          regionmap: Array.from({ length: 2_048 }, (_, row) => [
            [2_048, row === 0 ? 0 : Math.min(42, Math.floor(row / 49) + 1)],
          ]),
        },
        galaxyRegions.GALAXY_REGION_SOURCE_SHA256,
      );
    }

    beforeEach(() => {
      vi.clearAllMocks();
      queryClient.clear();
      localStorage.clear();
      stubRuntime();
      mockFacadeDefaults();
      featureGate.enabled = enabled;
      mockRankedPages();
      vi.mocked(api.getMapHeatmap).mockResolvedValue(pyramidFixture);
      vi.mocked(api.getSystem).mockResolvedValue(anchorDetail(anchorA));
      vi.spyOn(
        galaxyRegions,
        'fetchAuthoritativeGalaxyRegions',
      ).mockRejectedValue(new Error('No region fixture'));
      vi.spyOn(galaxyNebulae, 'fetchGalaxyNebulae').mockRejectedValue(
        new Error('No nebula fixture'),
      );
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
        name: /Ranked 1.*9007199254741001/,
      });
      await fireEvent.click(selection);
      for (const opener of screen.getAllByRole('button', {
        name: 'Show on map',
      }))
        expect(opener).toBeDisabled();
      expect(
        screen.getByText(
          'Map coordinates unavailable for the selected system.',
        ),
      ).toBeVisible();
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
      const local = within(map).getByRole('button', { name: 'Local' });
      expect(local).toBeDisabled();
      expect(local).toHaveAccessibleDescription(/select.*system/i);
      expect(document.querySelector('[inert]')).toBeNull();
      await waitFor(() =>
        expect(runtimeAdapter.create).toHaveBeenCalledTimes(1),
      );
      expect(document.querySelectorAll('[data-spatial-canvas]')).toHaveLength(
        1,
      );
      expect(api.searchExploreSystems).not.toHaveBeenCalled();
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
    });

    it.each(['regions', 'sources'] as const)(
      'keeps the %s tool non-modal with topmost Escape ownership and focus return',
      async (tool) => {
        renderEnabled('');
        const map = await screen.findByRole('region', { name: 'Galaxy map' });
        const trigger = map.querySelector<HTMLButtonElement>(
          `[data-map-${tool}-trigger]`,
        )!;
        expect(trigger).toBeInTheDocument();
        trigger.focus();
        await fireEvent.click(trigger);
        const panel = map.querySelector<HTMLElement>(
          `[data-map-${tool}-panel]`,
        )!;
        expect(panel).toHaveAttribute('role', 'dialog');
        expect(panel).toHaveAttribute('aria-modal', 'false');
        expect(trigger).toHaveAttribute('aria-expanded', 'true');
        expect(trigger).toHaveAttribute('aria-controls', panel.id);
        expect(isTopmostPopout(panel)).toBe(true);
        expect(document.querySelector('[inert]')).toBeNull();
        if (tool === 'sources') {
          expect(panel).toHaveTextContent(
            'Stars use exact catalogue coordinates',
          );
          expect(panel).toHaveTextContent(
            'Region geometry is the pinned in-game source',
          );
        } else {
          await waitFor(() =>
            expect(panel).toHaveTextContent(
              'The authoritative region resource failed validation.',
            ),
          );
          expect(
            within(panel).getByRole('button', { name: 'Retry regions' }),
          ).toBeEnabled();
        }
        const accessibility = await axe.run(map, {
          rules: { 'color-contrast': { enabled: false } },
        });
        expect(accessibility.violations).toEqual([]);
        const later = document.createElement('div');
        const unregister = registerPopout(later);
        try {
          await fireEvent.keyDown(window, { key: 'Escape' });
          expect(panel).toBeInTheDocument();
        } finally {
          unregister();
        }
        await fireEvent.keyDown(window, { key: 'Escape' });
        expect(panel).not.toBeInTheDocument();
        expect(trigger).toHaveFocus();
        expect(trigger).toHaveAttribute('aria-expanded', 'false');
        // The map is part of the page, so a further Escape leaves it alone.
        await fireEvent.keyDown(window, { key: 'Escape' });
        expect(map).toBeInTheDocument();
        expect(document.querySelectorAll('[data-spatial-canvas]')).toHaveLength(
          1,
        );
        expect(runtimeAdapter.create).toHaveBeenCalledOnce();
        expect(api.searchExploreSystems).not.toHaveBeenCalled();
        expect(api.getArchetypeRankings).not.toHaveBeenCalled();
      },
    );

    it('uses Local, Galaxy and Centre on as existing camera requests and retains region actions', async () => {
      const regions = regionFixture();
      vi.mocked(
        galaxyRegions.fetchAuthoritativeGalaxyRegions,
      ).mockResolvedValue(regions);
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
      const results = await screen.findByRole('list', {
        name: 'System results',
      });
      await fireEvent.click(
        await within(results).findByRole('button', {
          name: /Ranked 2.*9007199254741002/,
        }),
      );
      const map = await screen.findByRole('region', {
        name: 'Ranked 2 · Galaxy map',
      });
      await waitFor(() => expect(viewState.regionState).toBe('ready'));
      await waitFor(() => expect(currentScene()).toBeDefined());
      const selected = currentScene().selection;
      const searchCount = vi.mocked(api.searchExploreSystems).mock.calls.length;
      const url = mockedPage.url.href;
      const local = within(map).getByRole('button', { name: 'Local' });
      const galaxy = within(map).getByRole('button', { name: 'Galaxy' });
      expect(local).toHaveAttribute('data-map-camera-preset', 'local');
      expect(galaxy).toHaveAttribute('data-map-camera-preset', 'galaxy');
      expect(local).toBeEnabled();
      await fireEvent.click(galaxy);
      expect(galaxy).toHaveAttribute('aria-pressed', 'true');
      expect(local).toHaveAttribute('aria-pressed', 'false');
      const overview = viewState.cameraRequest;
      expect(overview?.pitchRad).toBe(Math.PI / 2);
      expect(overview?.distanceLy).toBeGreaterThan(100_000);
      await fireEvent.click(local);
      expect(local).toHaveAttribute('aria-pressed', 'true');
      expect(galaxy).toHaveAttribute('aria-pressed', 'false');
      expect(viewState.cameraRequest).toMatchObject({
        focusLy: rankedRows[1].coords,
        distanceLy: 400,
      });
      expect(currentScene().kind).toBe('galaxy');
      await fireEvent.click(galaxy);
      await fireEvent.click(
        within(map).getByRole('button', { name: 'Centre on Ranked 2' }),
      );
      expect(viewState.cameraRequest).toMatchObject({
        focusLy: rankedRows[1].coords,
        distanceLy: 400,
      });
      expect(currentScene().selection).toEqual(selected);
      await fireEvent.click(
        within(map).getByRole('button', { name: 'Regions 42' }),
      );
      const panel = map.querySelector<HTMLElement>('[data-map-regions-panel]')!;
      expect(
        panel.querySelectorAll('[data-galaxy-region-option]'),
      ).toHaveLength(42);
      const picker = within(panel).getByRole('combobox', {
        name: 'Galactic region',
      });
      const viewSelected = within(panel).getByRole('button', {
        name: 'View selected',
      });
      expect(viewSelected).toBeDisabled();
      await fireEvent.change(picker, { target: { value: '1' } });
      expect(viewState.selectedRegionId).toBe('1');
      expect(
        panel.querySelector('[data-selected-region-id="1"]'),
      ).toBeInTheDocument();
      await fireEvent.click(viewSelected);
      expect(viewState.cameraRequest).toMatchObject({
        focusLy: regions.regions[0].labelLy,
        distanceLy: 18_000,
      });
      await fireEvent.click(
        within(panel).getByRole('button', { name: 'All 42 regions' }),
      );
      expect(viewState.cameraRequest).toMatchObject({
        focusLy: overview!.focusLy,
        distanceLy: overview!.distanceLy,
        pitchRad: overview!.pitchRad,
      });
      expect(mockedPage.url.href).toBe(url);
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(searchCount);
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
      expect(
        galaxyRegions.fetchAuthoritativeGalaxyRegions,
      ).toHaveBeenCalledOnce();
      expect(runtimeAdapter.create).toHaveBeenCalledOnce();
      expect(document.querySelectorAll('[data-spatial-canvas]')).toHaveLength(
        1,
      );
    });

    it('keeps an explicit Local camera choice when pending regions arrive after the map starts unselected', async () => {
      let finishRegions!: (regions: galaxyRegions.GalaxyRegionsPayload) => void;
      vi.mocked(
        galaxyRegions.fetchAuthoritativeGalaxyRegions,
      ).mockImplementation(
        () => new Promise((resolve) => (finishRegions = resolve)),
      );
      vi.mocked(api.autocompleteSystems).mockResolvedValue({
        results: [anchorA],
      });
      const viewState = new ExploreViewState(
        () => mockedPage,
        enabled,
        applicationStores,
      );
      renderEnabled('', {}, viewState);
      await waitFor(() => expect(currentScene()).toBeDefined());
      expect(viewState.initialGalaxyViewRequested).toBe(false);
      expect(viewState.regionState).toBe('loading');
      await fireEvent.input(
        screen.getByRole('combobox', { name: 'System name' }),
        {
          target: { value: 'Anchor A' },
        },
      );
      await fireEvent.click(
        await screen.findByRole('option', { name: /Anchor A/ }),
      );
      await fireEvent.click(
        await screen.findByRole('button', {
          name: /Ranked 2.*9007199254741002/,
        }),
      );
      await fireEvent.click(screen.getByRole('button', { name: 'Local' }));
      const local = viewState.cameraRequest;
      expect(local).toMatchObject({
        focusLy: rankedRows[1].coords,
        distanceLy: 400,
      });
      const revision = viewState.cameraRequestRevision;
      finishRegions(regionFixture());
      await waitFor(() => expect(viewState.regionState).toBe('ready'));
      await tick();
      expect(viewState.cameraRequest).toEqual(local);
      expect(viewState.cameraRequestRevision).toBe(revision);
      expect(screen.getByRole('button', { name: 'Local' })).toHaveAttribute(
        'aria-pressed',
        'true',
      );
      expect(runtimeAdapter.create).toHaveBeenCalledOnce();
      expect(api.searchExploreSystems).toHaveBeenCalledOnce();
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
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
          name: /Ranked 2.*9007199254741002/,
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
      expect(map.querySelector('.selection-status')).toHaveAttribute(
        'aria-live',
        'polite',
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
          name: /Ranked 2.*9007199254741002/,
        }),
      );
      await fireEvent.input(
        screen.getByRole('combobox', { name: 'System name' }),
        { target: { value: 'Unsubmitted draft' } },
      );
      list.scrollTop = 256;
      await fireEvent.scroll(list);
      await waitFor(() => expect(currentScene()).toBeDefined());
      const canvas = document.querySelector('[data-spatial-canvas] canvas');
      const first = runtimeAdapter.create.mock.results[0].value;
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
      const map = screen.getByRole('region', { name: /Galaxy map$/ });
      const show = within(map).getByRole('button', { name: 'Show Finder' });
      await waitFor(() => expect(show).toHaveFocus());
      // A scroll event while hidden must not overwrite the retained position.
      list.scrollTop = 0;
      await fireEvent.scroll(list);
      expect(viewState.resultScrollTop).toBe(256);

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
        within(map).queryByRole('button', { name: 'Show Finder' }),
      ).toBeNull();
      expect(document.querySelector('[data-spatial-canvas] canvas')).toBe(
        canvas,
      );
      expect(runtimeAdapter.create).toHaveBeenCalledOnce();
      expect(first.dispose).not.toHaveBeenCalled();
      expect(screen.getByRole('combobox', { name: 'System name' })).toHaveValue(
        'Unsubmitted draft',
      );
      expect(screen.getByRole('combobox', { name: 'Within' })).toHaveValue(
        '250',
      );
      expect(screen.getByText('Earth-like ≥ 1')).toBeInTheDocument();
      expect(
        within(list).getByRole('button', {
          name: /Ranked 2.*9007199254741002/,
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
          name: /Ranked 2.*9007199254741002/,
        }),
      );
      list.scrollTop = 96;
      await fireEvent.scroll(list);
      const opener = within(list).getByRole('button', { name: 'Show on map' });
      opener.focus();
      await fireEvent.click(opener);
      expect(viewState.finderPanelOpen).toBe(false);
      expect(finderPanel()).toHaveAttribute('hidden');
      expect(viewState.retainedCamera?.camera).toMatchObject({
        focusLy: rankedRows[1].coords,
        distanceLy: 400,
      });
      const show = screen.getByRole('button', { name: 'Show Finder' });
      await waitFor(() => expect(show).toHaveFocus());
      await fireEvent.click(show);
      expect(viewState.finderPanelOpen).toBe(true);
      await waitFor(() => expect(opener).toHaveFocus());
      expect(list.scrollTop).toBe(96);
      expect(runtimeAdapter.create).toHaveBeenCalledOnce();
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

    it('Show on map on a wide screen focuses the system and keeps the Finder panel', async () => {
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
          name: /Ranked 2.*9007199254741002/,
        }),
      );
      const mapSection = document.querySelector<HTMLElement>(
        '[data-explore-map-window]',
      )!;
      const scrollIntoView = vi.fn();
      mapSection.scrollIntoView = scrollIntoView;
      await fireEvent.click(
        within(list).getByRole('button', { name: 'Show on map' }),
      );
      expect(viewState.finderPanelOpen).toBe(true);
      expect(finderPanel()).not.toHaveAttribute('hidden');
      await tick();
      expect(scrollIntoView).not.toHaveBeenCalled();
      expect(viewState.retainedCamera?.camera).toMatchObject({
        focusLy: rankedRows[1].coords,
        distanceLy: 400,
      });
      expect(viewState.mapCameraPreset).toBe('local');
    });
  },
);
