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
import { createHeadlessEngine } from '$lib/spatial/babylon/test-support';
import * as sceneBuilder from '$lib/spatial/explore-scene';
import * as regionsSource from '$lib/spatial/galaxy-regions';
import * as nebulaeSource from '$lib/spatial/galaxy-nebulae';
import { createManagedSpatialRuntime } from '$lib/spatial/lifecycle';
import type {
  RuntimeCommand,
  SpatialRuntimeStatus,
} from '$lib/spatial/contracts';
import ExploreWorkloadTestHost from './ExploreWorkloadTestHost.svelte';
import { ExploreViewState } from './explore-view-state.svelte';

const { page, featureGate, goto, adapter } = vi.hoisted(() => ({
  page: {
    url: new URL('http://localhost/explore'),
    state: {} as App.PageState,
  },
  featureGate: { enabled: false },
  goto: vi.fn(),
  adapter: vi.fn(),
}));
vi.mock('$app/state', () => ({ page }));
vi.mock('$app/navigation', () => ({ goto }));
vi.mock('$lib/config', () => ({
  get FINDER_F4_ENABLED() {
    return featureGate.enabled;
  },
}));
vi.mock('$lib/spatial/babylon/adapter', () => ({
  createBabylonSpatialRuntime: adapter,
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
  getMapHeatmap: vi.fn(),
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
const density: api.MapHeatmapPyramidResponse = {
  source: 'pyramid',
  generation_id: 'gen-1',
  spatial_generation_id: 'gen-1',
  spatial_pyramid_version: 'v1',
  source_system_count: 200,
  coverage_at: '2026-01-01T00:00:00Z',
  level: 0,
  requested_level: 0,
  served_level: 0,
  level_cell_count: 1,
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
      system_count: 200,
    },
  ],
  count: 1,
  max_cells: 40_000,
  truncated: false,
};
const regions = regionsSource.buildGalaxyRegionsPayload(
  {
    version: 1,
    origin: { x: -49_985, z: -24_105 },
    pixel_scale: 49.34939759036145,
    regions: ['', ...regionsSource.GALAXY_REGION_NAMES],
    regionmap: Array.from({ length: 2_048 }, (_, row) => [
      [2_048, row === 0 ? 0 : Math.min(42, Math.floor(row / 49) + 1)],
    ]),
  },
  regionsSource.GALAXY_REGION_SOURCE_SHA256,
);
const nebulae: nebulaeSource.GalaxyNebulaePayload = {
  datasetId: 'workload',
  sourceUrl: 'https://example.test/nebulae',
  catalogueUrl: 'https://example.test/nebulae',
  rightsNotice: 'Test fixture',
  usageBasis: 'Test fixture',
  attribution: 'Test fixture',
  sourceLastModified: null,
  sourceByteCount: 1,
  sourceSha256: 'a'.repeat(64),
  nebulae: [
    {
      id: 'workload-nebula',
      source: 'fixture',
      sourceId: '1',
      name: 'Workload nebula',
      systemName: 'Workload nebula',
      regionName: null,
      regionId: null,
      kind: 'nebula',
      positionLy: {
        value: { x: 1000, y: 0, z: 1000 },
        representation: 'AUTHORITATIVE',
        provenance: [{ source: 'fixture' }],
      },
      poiUrl: null,
    },
  ],
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
      navigation_limit: 10_000,
      truncation: { source: 'local-search' as const, total_is_capped: false },
    },
    ranking,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

/**
 * Baseline captured by copying this harness/host into git-archive snapshots of
 * main 13bc0e69dc7cbc7c53bf14702c1eaad2aef7bca6 and branch 5790f05f,
 * then running Vitest with the same locked dependencies and Node 24. The main
 * values were measured, not inferred from the implementation under test. The
 * current counts include the empty initial page and an explicit anchor choice.
 *
 * The real Svelte components, TanStack observers, scene mapper, managed runtime,
 * Babylon scene construction, and camera state machine run throughout. Only
 * HTTP/static-resource responses, GPU drawing, and the frame clock are replaced.
 * Staggered resource arrivals are intentional: renderer work must stay bounded
 * regardless of which network response completes after the initial overview.
 * P2a also re-ran this unchanged budget on main ab78d39d before extracting view
 * state; request/build/scene counts below are identical after the extraction.
 */
describe('Explore load and interaction work budget', () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(640);
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(360);
    vi.mocked(api.autocompleteSystems).mockResolvedValue({ results: [] });
    vi.mocked(api.searchExploreSystems).mockImplementation(async (request) =>
      finderResponse(request.size ?? 24, request.from ?? 0),
    );
    vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue({
      systems: [],
      truncated: false,
    });
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
      const regionResponse = deferred<regionsSource.GalaxyRegionsPayload>();
      const nebulaResponse = deferred<nebulaeSource.GalaxyNebulaePayload>();
      const densityResponse = deferred<api.MapHeatmapPyramidResponse>();
      vi.spyOn(
        regionsSource,
        'fetchAuthoritativeGalaxyRegions',
      ).mockReturnValue(regionResponse.promise);
      vi.spyOn(nebulaeSource, 'fetchGalaxyNebulae').mockReturnValue(
        nebulaResponse.promise,
      );
      vi.mocked(api.getMapHeatmap).mockImplementation(
        () => densityResponse.promise,
      );
      const builds = vi.spyOn(sceneBuilder, 'buildExploreGalaxyScene');
      const { createBabylonSession } = await vi.importActual<
        typeof import('$lib/spatial/babylon/adapter')
      >('$lib/spatial/babylon/adapter');
      let animationTime = 0;
      const rendererScenes = vi.fn();
      const frames = new Map<number, () => void>();
      let nextFrame = 0;
      const commands: RuntimeCommand[] = [];
      const disposals = vi.fn();
      adapter.mockImplementation(
        (_canvas, onStatus: (status: SpatialRuntimeStatus) => void) => {
          const engine = createHeadlessEngine();
          const engineDispose = vi.spyOn(engine, 'dispose');
          const session = createBabylonSession(
            engine,
            'WEBGL2',
            () => animationTime,
          );
          engine.onNewSceneAddedObservable.add((scene) => {
            rendererScenes();
            vi.spyOn(scene, 'render').mockImplementation(() => undefined);
            vi.spyOn(scene, 'isReady').mockReturnValue(true);
          });
          const runtime = createManagedSpatialRuntime(
            { createWebGpu: async () => null, createWebGl2: () => session },
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
          const dispatch = runtime.dispatch.bind(runtime);
          vi.spyOn(runtime, 'dispatch').mockImplementation((command) => {
            commands.push(command);
            return dispatch(command);
          });
          const dispose = runtime.dispose.bind(runtime);
          vi.spyOn(runtime, 'dispose').mockImplementation(() => {
            const liveScenes = [...engine.scenes];
            disposals();
            dispose();
            expect(engineDispose).toHaveBeenCalledTimes(1);
            expect(engine.scenes).toHaveLength(0);
            expect(liveScenes.every((scene) => scene.isDisposed)).toBe(true);
          });
          return runtime;
        },
      );
      const settle = async () => {
        for (let round = 0; round < 4; round += 1) {
          await vi.advanceTimersByTimeAsync(200);
          for (let frame = 0; frames.size && frame < 150; frame += 1) {
            animationTime += 1_000;
            const callbacks = [...frames.values()];
            frames.clear();
            callbacks.forEach((callback) => callback());
            await tick();
          }
          expect(frames.size).toBe(0);
          await tick();
        }
      };
      const work = () => ({
        builds: builds.mock.calls.length,
        rendererScenes: rendererScenes.mock.calls.length,
        loads: commands.filter((command) => command.type === 'LOAD_SCENE')
          .length,
        patches: commands.filter(
          (command) => command.type === 'PATCH_CONTRIBUTION',
        ).length,
        activeQueries: queryClient
          .getQueryCache()
          .getAll()
          .filter((query) => query.getObserversCount() > 0).length,
        transitions: commands.filter(
          (command) =>
            command.type === 'FLY_TO' ||
            (command.type === 'SET_CAMERA' && command.transition),
        ).length,
        api: {
          finder: vi.mocked(api.searchExploreSystems).mock.calls.length,
          rankings: vi.mocked(api.getArchetypeRankings).mock.calls.length,
          catalogue: vi.mocked(api.getCatalogueViewportSystems).mock.calls
            .length,
          density: vi.mocked(api.getMapHeatmap).mock.calls.length,
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
      regionResponse.resolve(regions);
      await settle();
      // The initial close camera can finish its local cells before the overview
      // starts density. Once density is pending, catalogue must yield to it.
      const catalogueBeforeDensity = vi.mocked(api.getCatalogueViewportSystems)
        .mock.calls.length;
      expect(
        queryClient.isFetching({ queryKey: ['map-heatmap'] }),
      ).toBeGreaterThan(0);
      await settle();
      expect(api.getCatalogueViewportSystems).toHaveBeenCalledTimes(
        catalogueBeforeDensity,
      );
      densityResponse.resolve(density);
      await settle();
      nebulaResponse.resolve(nebulae);
      await settle();
      expect(
        screen.getByRole('heading', { name: 'Systems' }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole('list', { name: 'System results' }),
      ).not.toBeInTheDocument();
      expect(api.searchExploreSystems).not.toHaveBeenCalled();
      expect(api.getArchetypeRankings).not.toHaveBeenCalled();
      expect(document.querySelectorAll('[data-spatial-canvas]')).toHaveLength(
        1,
      );
      expect(adapter).toHaveBeenCalledTimes(1);
      expect(disposals).not.toHaveBeenCalled();
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
      expect(api.searchExploreSystems).toHaveBeenCalledTimes(1);
      expect(
        screen.getByRole('heading', { name: 'Near Workload 2' }),
      ).toBeInTheDocument();

      // Selecting a real result exercises fly-to, exact-star loading, and the
      // late-data camera protection through the real Babylon camera controller.
      await fireEvent.click(
        within(screen.getByRole('list', { name: 'System results' })).getByRole(
          'button',
          { name: /Workload 1.*9007199254741001/ },
        ),
      );
      await settle();
      // An unchanged response advances dataUpdatedAt but carries no new geometry.
      void queryClient.invalidateQueries({
        queryKey: [...queryKeys.all, 'explore', 'results'],
      });
      void queryClient.invalidateQueries({ queryKey: ['map-heatmap'] });
      await settle();
      const beforeTools = work().api;
      await fireEvent.click(screen.getByRole('button', { name: 'Regions 42' }));
      await settle();
      expect(work().api).toEqual(beforeTools);
      await fireEvent.click(
        screen.getByRole('button', { name: 'All 42 regions' }),
      );
      await settle();
      await fireEvent.keyDown(window, { key: 'Escape' });
      const beforeSources = work().api;
      await fireEvent.click(screen.getByRole('button', { name: 'Sources' }));
      await settle();
      expect(work().api).toEqual(beforeSources);
      expect(adapter).toHaveBeenCalledTimes(1);
      await fireEvent.keyDown(window, { key: 'Escape' });
      await fireEvent.click(screen.getByRole('button', { name: /^Layers · / }));
      await fireEvent.click(screen.getByRole('switch', { name: 'Nebulae' }));
      await settle();
      const interacted = work();
      // A quiescent page must not restart work while the browser checks its URL.
      for (let sample = 0; sample < 24; sample += 1) {
        await vi.advanceTimersByTimeAsync(500);
        expect(page.url.href).toContain('/explore');
        await tick();
      }
      expect(work()).toEqual(interacted);
      expect(document.querySelectorAll('[data-spatial-canvas]')).toHaveLength(
        1,
      );
      expect(adapter).toHaveBeenCalledTimes(1);
      expect(disposals).not.toHaveBeenCalled();
      const catalogueQueries = queryClient
        .getQueryCache()
        .getAll()
        .filter((query) => query.queryKey[2] === 'catalogue-stars');
      expect(catalogueQueries.length).toBeLessThanOrEqual(64);
      expect(
        catalogueQueries.filter((query) => query.getObserversCount() > 0)
          .length,
      ).toBeLessThanOrEqual(16);
      for (const [request] of vi.mocked(api.getCatalogueViewportSystems).mock
        .calls) {
        expect(request.maxX - request.minX).toBeLessThanOrEqual(15_000);
        expect(request.maxY - request.minY).toBeLessThanOrEqual(15_000);
        expect(request.maxZ - request.minZ).toBeLessThanOrEqual(15_000);
      }
      expect(
        regionsSource.fetchAuthoritativeGalaxyRegions,
      ).toHaveBeenCalledTimes(1);
      expect(nebulaeSource.fetchGalaxyNebulae).toHaveBeenCalledTimes(1);
      const idleApi = {
        rankings: 0,
        selected: 0,
        autocomplete: 0,
        travel: 0,
        journalSummary: 0,
        auth: 0,
      };
      expect(loaded).toEqual({
        builds: enabled ? 3 : 17,
        rendererScenes: enabled ? 3 : 4,
        loads: enabled ? 3 : 6,
        patches: enabled ? 1 : 0,
        // One wide sample replaces overview cell fanout; the Layers pop-out
        // adds the disabled account-summary observer in both modes.
        activeQueries: 8,
        transitions: 1,
        api: { ...idleApi, finder: 0, catalogue: 5, density: 2 },
      });
      // Measured after bounding request concurrency, yielding to foreground
      // queries and coalescing adjacent local cells. Reserved anchors remain
      // immediate; empty/equal samples retain their contribution identities.
      // Pending/equal responses below must still avoid scene reconstruction.
      expect(interacted).toEqual({
        // Window presentation reads the non-F4 scene once more; GPU scene
        // loads and all request counts retain the measured main budget.
        builds: enabled ? 7 : 36,
        rendererScenes: enabled ? 6 : 7,
        loads: enabled ? 7 : 12,
        patches: enabled ? 1 : 0,
        // The committed anchor and selected result have separate query
        // identities; the account-summary observer adds one in both modes.
        activeQueries: enabled ? 9 : 8,
        transitions: 4,
        api: {
          ...idleApi,
          autocomplete: 1,
          finder: 2,
          // Selecting a result flies to the local view (400 ly) in both modes,
          // so both stream the same nearby cells. Before, the flight ended at a
          // marker-derived distance that differed by mode (10/5 and 14/4).
          catalogue: 13,
          density: 3,
        },
      });
      const assertCleanup = async () => {
        // Focus the selected result before measuring the presentation-only
        // lifecycle. This first focus may stream its visible viewport; hiding
        // and restoring the Finder panel must reuse the one canvas and start
        // no new work for the same footprint.
        const layersClose = screen.queryByRole('button', {
          name: 'Close map layers',
        });
        if (layersClose) await fireEvent.click(layersClose);
        const opener = document.querySelector<HTMLButtonElement>(
          '[data-explore-map-open]',
        )!;
        await fireEvent.click(opener);
        await settle();
        const visibleApi = work().api;
        const camera = viewState.retainedCamera?.camera;
        await fireEvent.click(
          screen.getByRole('button', { name: 'Hide Finder' }),
        );
        await settle();
        expect(viewState.finderPanelOpen).toBe(false);
        expect(work().api).toEqual(visibleApi);
        expect(adapter).toHaveBeenCalledTimes(1);
        expect(document.querySelectorAll('[data-spatial-canvas]')).toHaveLength(
          1,
        );
        await fireEvent.click(
          screen.getByRole('button', { name: 'Show Finder' }),
        );
        await settle();
        expect(viewState.finderPanelOpen).toBe(true);
        expect(adapter).toHaveBeenCalledTimes(1);
        expect(disposals).not.toHaveBeenCalled();
        expect(viewState.retainedCamera?.camera).toEqual(camera);
        expect(viewState.showNebulae).toBe(false);
        expect(work().api).toEqual(visibleApi);
        const requestsBeforeUnmount = work().api;
        mounted.unmount();
        await settle();
        expect(document.querySelectorAll('[data-spatial-canvas]')).toHaveLength(
          0,
        );
        expect(adapter).toHaveBeenCalledTimes(1);
        expect(disposals).toHaveBeenCalledTimes(1);
        expect(work().activeQueries).toBe(0);
        expect(work().api).toEqual(requestsBeforeUnmount);
        expect(frames.size).toBe(0);
      };
      if (!enabled) {
        await assertCleanup();
        return;
      }

      const visualWork = () => {
        const current = work();
        return {
          builds: current.builds,
          rendererScenes: current.rendererScenes,
          loads: current.loads,
          patches: current.patches,
          transitions: current.transitions,
        };
      };
      const beforePage = visualWork();
      const firstPage = deferred<ReturnType<typeof finderResponse>>();
      vi.mocked(api.searchExploreSystems).mockReturnValueOnce(
        firstPage.promise,
      );
      await fireEvent.change(
        screen.getByRole('spinbutton', { name: 'Results per page' }),
        { target: { value: '1' } },
      );
      await settle();
      expect(visualWork()).toEqual(beforePage);
      firstPage.resolve(finderResponse(1));
      await settle();
      const paged = visualWork();
      expect(paged).toEqual({
        ...beforePage,
        builds: beforePage.builds + 1,
        rendererScenes: beforePage.rendererScenes + 1,
        loads: beforePage.loads + 1,
      });

      const nextPage = deferred<ReturnType<typeof finderResponse>>();
      vi.mocked(api.searchExploreSystems).mockReturnValueOnce(nextPage.promise);
      await fireEvent.click(screen.getByRole('button', { name: 'Next' }));
      await settle();
      expect(visualWork()).toEqual(paged);
      nextPage.resolve(finderResponse(1, 1));
      await settle();
      const next = visualWork();
      expect(next).toEqual({
        ...paged,
        builds: paged.builds + 1,
        rendererScenes: paged.rendererScenes + 1,
        loads: paged.loads + 1,
      });
      expect(api.getSystem).not.toHaveBeenCalled();

      // Return to a cached page (staleTime=0 refetches it), then reject a mode
      // change. Neither equal network data nor retained-on-error rows are a
      // second visual revision of the same displayed geometry.
      await fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
      await settle();
      const returned = visualWork();
      expect(returned).toEqual({
        ...next,
        builds: next.builds + 1,
        rendererScenes: next.rendererScenes + 1,
        loads: next.loads + 1,
      });
      vi.mocked(api.getArchetypeRankings).mockRejectedValueOnce(
        new api.ApiError(503, '/api/archetypes/rankings', {
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
      expect(visualWork()).toEqual(returned);
      await fireEvent.click(screen.getByRole('radio', { name: 'Any' }));
      await settle();
      expect(visualWork()).toEqual(returned);
      expect(work()).toEqual({
        builds: 10,
        rendererScenes: 9,
        loads: 10,
        patches: 1,
        // Includes the disabled account-summary observer from the Layers pop-out.
        activeQueries: 9,
        transitions: 4,
        api: {
          ...idleApi,
          autocomplete: 1,
          finder: 6,
          rankings: 1,
          // The same local-view flight as above; the archetype steps in this
          // path add no catalogue or density requests of their own.
          catalogue: 13,
          density: 3,
        },
      });
      await assertCleanup();
    },
    30_000,
  );
});
