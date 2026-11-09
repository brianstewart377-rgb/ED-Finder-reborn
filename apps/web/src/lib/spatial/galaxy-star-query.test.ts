import { QueryClient } from '@tanstack/svelte-query';
import { cleanup, render, screen, waitFor } from '@testing-library/svelte';
import { tick } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '$lib/api/client';
import { queryKeys } from '$lib/api/query';
import { parseId64 } from '$lib/domain/id64';
import type { CameraState } from './contracts';
import GalaxyStarQueryTestHost from './GalaxyStarQueryTestHost.svelte';
import {
  GALAXY_STAR_CELL_CACHE_LIMIT,
  MAX_GALAXY_STAR_ACTIVE_CELLS,
  planGalaxyStarCells,
} from './galaxy-star-cells';
import { GALAXY_STAR_IDLE_DELAY_MS } from './galaxy-star-requests';
import { galaxyStarViewport } from './galaxy-star-stream';
import { withinGalaxyStarFootprint } from './galaxy-star-footprint';

vi.mock('$lib/api/client', async (loadOriginal) => ({
  ...(await loadOriginal<typeof import('$lib/api/client')>()),
  getCatalogueViewportSystems: vi.fn(),
}));

const queryPrefix = [...queryKeys.all, 'map', 'catalogue-stars'];
const viewportSize = { width: 1_200, height: 800 };
const camera = (x = 100, z = 300, distanceLy = 1_000): CameraState => ({
  focusLy: { x, y: 0, z },
  distanceLy,
  bearingRad: 0,
  pitchRad: Math.PI / 2,
  projection: 'perspective',
  revision: 1,
});
const props = (client: QueryClient, state: CameraState) => ({
  client,
  viewport: galaxyStarViewport(state, 'local', viewportSize),
  focus: state.focusLy,
});

function packet(): api.CatalogueViewportResponse | undefined {
  const text = screen.getByTestId('catalogue-packet').textContent;
  return text ? JSON.parse(text) : undefined;
}

async function settled(): Promise<void> {
  await waitFor(() => {
    expect(screen.getByTestId('catalogue-packet')).toHaveAttribute(
      'data-pending',
      'false',
    );
    expect(screen.getByTestId('catalogue-packet')).toHaveAttribute(
      'data-fetching',
      'false',
    );
    expect(packet()).toBeDefined();
  });
}

describe('catalogue cell query integration', () => {
  let client: QueryClient;
  let responseSequence: number;

  beforeEach(() => {
    client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    responseSequence = 0;
    vi.mocked(api.getCatalogueViewportSystems).mockReset();
    vi.mocked(api.getCatalogueViewportSystems).mockImplementation(
      async (request) => ({
        systems: [
          {
            id64: parseId64(String(++responseSequence)),
            name: `Cell system ${responseSequence}`,
            x: (request.minX + request.maxX) / 2,
            y: (request.minY + request.maxY) / 2,
            z: (request.minZ + request.maxZ) / 2,
            populated: false,
          },
        ],
        truncated: false,
      }),
    );
  });

  afterEach(() => {
    cleanup();
    client.clear();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('reuses cached cells across a small pan and when returning to a previous view', async () => {
    const initial = props(client, camera());
    const view = render(GalaxyStarQueryTestHost, initial);
    await settled();
    const initialCalls = vi.mocked(api.getCatalogueViewportSystems).mock.calls
      .length;
    expect(initialCalls).toBeGreaterThan(0);
    expect(initialCalls).toBeLessThanOrEqual(MAX_GALAXY_STAR_ACTIVE_CELLS);

    await view.rerender(props(client, camera(150, 350)));
    await settled();
    expect(api.getCatalogueViewportSystems).toHaveBeenCalledTimes(initialCalls);

    const footprint = initial.viewport!;
    await view.rerender(
      props(
        client,
        camera(
          100 + footprint.maxX - footprint.minX,
          300 + footprint.maxZ - footprint.minZ,
        ),
      ),
    );
    await settled();
    const movedCalls = vi.mocked(api.getCatalogueViewportSystems).mock.calls
      .length;
    expect(movedCalls).toBeGreaterThan(initialCalls);

    await view.rerender(props(client, camera()));
    await settled();
    expect(api.getCatalogueViewportSystems).toHaveBeenCalledTimes(movedCalls);
    expect(
      client.getQueryCache().findAll({ queryKey: queryPrefix }).length,
    ).toBeLessThanOrEqual(GALAXY_STAR_CELL_CACHE_LIMIT);
  });

  it('starts with no renderer viewport, then grows and shrinks cells without stranding foreground updates', async () => {
    vi.useFakeTimers();
    const initial = props(client, camera());
    const view = render(GalaxyStarQueryTestHost, {
      ...initial,
      viewport: null,
    });
    await vi.advanceTimersByTimeAsync(1_000);
    expect(screen.getByTestId('foreground')).toHaveTextContent('initial');
    expect(api.getCatalogueViewportSystems).not.toHaveBeenCalled();
    for (const [index, state] of [
      camera(0, 0, 118_000),
      camera(),
      camera(0, 0, 118_000),
      camera(200, 200, 20),
    ].entries()) {
      await view.rerender({
        ...props(client, state),
        viewport: galaxyStarViewport(
          state,
          state.distanceLy > 9_500 ? 'wide' : 'local',
          viewportSize,
        ),
        foregroundKey: `foreground ${index}`,
      });
      await vi.advanceTimersByTimeAsync(1_000);
      expect(screen.getByTestId('foreground')).toHaveTextContent(
        `foreground ${index}`,
      );
      expect(packet()).toBeDefined();
      expect(screen.getByTestId('catalogue-packet')).toHaveAttribute(
        'data-fetching',
        'false',
      );
    }
  });

  it('publishes successful cells and marks the packet incomplete when one sibling fails', async () => {
    vi.mocked(api.getCatalogueViewportSystems).mockRejectedValueOnce(
      new Error('One cell unavailable'),
    );
    render(GalaxyStarQueryTestHost, props(client, camera()));
    await settled();
    const response = packet()!;
    expect(screen.getByTestId('catalogue-packet')).toHaveAttribute(
      'data-error',
      'true',
    );
    expect(response.truncated).toBe(true);
    expect(response.systems.length).toBeGreaterThan(0);
    expect(response.systems.length).toBe(
      vi.mocked(api.getCatalogueViewportSystems).mock.calls.length - 1,
    );
  });

  it('retrieves a star omitted by a truncated 250 LY cell when zooming onto it', async () => {
    // Model the API: bounds are applied before id64 ordering and the row cap.
    const omitted = {
      id64: parseId64('99999'),
      name: 'Previously omitted',
      x: 200,
      y: 0,
      z: 180,
      populated: false,
    };
    const catalogue = [
      ...Array.from({ length: 1_800 }, (_, index) => ({
        id64: parseId64(String(index + 1)),
        name: `Low id64 ${index}`,
        x: 40,
        y: 0,
        z: 80,
        populated: false,
      })),
      omitted,
    ];
    vi.mocked(api.getCatalogueViewportSystems).mockImplementation(
      async (request) => {
        const matches = catalogue.filter(
          (star) =>
            star.x >= request.minX &&
            star.x <= request.maxX &&
            star.y >= request.minY &&
            star.y <= request.maxY &&
            star.z >= request.minZ &&
            star.z <= request.maxZ,
        );
        return {
          systems: matches.slice(0, request.limit),
          truncated: matches.length > request.limit,
        };
      },
    );
    const view = render(
      GalaxyStarQueryTestHost,
      props(client, camera(125, 125, 140)),
    );
    await settled();
    expect(packet()!.truncated).toBe(true);
    expect(packet()!.systems.some((star) => star.id64 === omitted.id64)).toBe(
      false,
    );
    const initialCalls = vi.mocked(api.getCatalogueViewportSystems).mock.calls
      .length;
    expect(
      vi
        .mocked(api.getCatalogueViewportSystems)
        .mock.calls.some(
          ([request]) =>
            request.minX === 0 &&
            request.maxX === 250 &&
            request.minZ === 0 &&
            request.maxZ === 250,
        ),
    ).toBe(true);

    const zoomed = props(client, camera(200, 180, 20));
    await view.rerender(zoomed);
    await settled();
    expect(api.getCatalogueViewportSystems).toHaveBeenCalledTimes(
      initialCalls + 1,
    );
    expect(packet()!.systems).toContainEqual(omitted);
    expect(
      withinGalaxyStarFootprint(
        zoomed.viewport!.footprint,
        omitted.x,
        omitted.z,
      ),
    ).toBe(true);
    const refinement = vi
      .mocked(api.getCatalogueViewportSystems)
      .mock.calls.at(-1)![0];
    expect(refinement.maxX - refinement.minX).toBeLessThan(250);
    expect(refinement.maxY - refinement.minY).toBeLessThan(250);
    expect(refinement.maxZ - refinement.minZ).toBeLessThan(250);
    expect(refinement.limit).toBe(1_800);

    // A complete replacement also remains stable under further zooming.
    await view.rerender(props(client, camera(200, 180, 10)));
    await settled();
    expect(api.getCatalogueViewportSystems).toHaveBeenCalledTimes(
      initialCalls + 1,
    );
    expect(
      client
        .getQueryCache()
        .findAll({ queryKey: queryPrefix })
        .filter((query) => query.isActive()).length,
    ).toBeLessThanOrEqual(MAX_GALAXY_STAR_ACTIVE_CELLS);
  });

  it('does not re-request an untruncated 250 LY cell when zooming in', async () => {
    const view = render(
      GalaxyStarQueryTestHost,
      props(client, camera(125, 125, 140)),
    );
    await settled();
    const initialCalls = vi.mocked(api.getCatalogueViewportSystems).mock.calls
      .length;
    await view.rerender(props(client, camera(200, 180, 20)));
    await settled();
    expect(api.getCatalogueViewportSystems).toHaveBeenCalledTimes(initialCalls);
  });

  it('bounds dense cached packets without lowering the API detail-band limit', async () => {
    vi.mocked(api.getCatalogueViewportSystems).mockImplementation(
      async (request) => {
        const cellSequence = ++responseSequence;
        return {
          systems: Array.from({ length: 6_000 }, (_, index) => ({
            id64: parseId64(String(cellSequence * 10_000 + index)),
            name: `Dense cell ${cellSequence} star ${index}`,
            x:
              request.minX +
              ((index + 0.5) / 6_000) * (request.maxX - request.minX),
            y: (request.minY + request.maxY) / 2,
            z:
              request.minZ +
              ((((index * 31) % 6_000) + 0.5) / 6_000) *
                (request.maxZ - request.minZ),
            populated: false,
          })),
          truncated: false,
        };
      },
    );
    render(GalaxyStarQueryTestHost, props(client, camera(100, 300, 4_000)));
    await settled();
    expect(
      vi
        .mocked(api.getCatalogueViewportSystems)
        .mock.calls.every(([request]) => request.limit === 40_000),
    ).toBe(true);
    const cached = client
      .getQueryCache()
      .findAll({ queryKey: queryPrefix })
      .map((query) => query.state.data as api.CatalogueViewportResponse);
    expect(cached.length).toBeGreaterThan(0);
    expect(cached.every((data) => data.systems.length <= 5_000)).toBe(true);
    expect(cached.every((data) => data.truncated)).toBe(true);
    expect(packet()!.systems.length).toBeLessThanOrEqual(
      MAX_GALAXY_STAR_ACTIVE_CELLS * 5_000,
    );
    expect(packet()!.truncated).toBe(true);
  });

  it('evicts distant query packets after sustained travel while keeping the active view', async () => {
    const now = Date.now.bind(Date);
    let elapsed = 0;
    vi.spyOn(Date, 'now').mockImplementation(() => now() + elapsed);
    const view = render(
      GalaxyStarQueryTestHost,
      props(client, camera(-40_000)),
    );
    await settled();
    const firstHashes = client
      .getQueryCache()
      .findAll({ queryKey: queryPrefix })
      .map((query) => query.queryHash);
    for (let index = 1; index <= 20; index++) {
      // Separate real navigation visits in time without waiting on the route's
      // minute-based request budget in a unit test.
      elapsed += 61_000;
      await view.rerender(props(client, camera(-40_000 + index * 4_000)));
      await settled();
      const retained = client
        .getQueryCache()
        .findAll({ queryKey: queryPrefix });
      expect(retained.length).toBeLessThanOrEqual(GALAXY_STAR_CELL_CACHE_LIMIT);
      expect(
        retained
          .filter((query) => query.isActive())
          .every((query) => query.state.data),
      ).toBe(true);
    }
    expect(
      vi.mocked(api.getCatalogueViewportSystems).mock.calls.length,
    ).toBeGreaterThan(GALAXY_STAR_CELL_CACHE_LIMIT);
    expect(firstHashes.every((hash) => !client.getQueryCache().get(hash))).toBe(
      true,
    );
  });

  it('aborts active downloads, cancels queued cells and removes only its packets on unmount', async () => {
    const signals: AbortSignal[] = [];
    vi.mocked(api.getCatalogueViewportSystems).mockImplementation(
      (_request, signal) => {
        signals.push(signal!);
        return new Promise((_resolve, reject) => {
          signal!.addEventListener(
            'abort',
            () => reject(new DOMException('Aborted', 'AbortError')),
            { once: true },
          );
        });
      },
    );
    // Establish route availability before exercising the four-slot queue.
    vi.mocked(api.getCatalogueViewportSystems).mockResolvedValueOnce({
      systems: [],
      truncated: false,
    });
    client.setQueryData(['unrelated'], 'keep');
    const state = camera(5_000, 5_000, 9_000);
    const view = render(GalaxyStarQueryTestHost, {
      ...props(client, state),
      viewport: galaxyStarViewport(state, 'local', {
        width: 2_100,
        height: 900,
      }),
    });
    await waitFor(() => expect(signals).toHaveLength(4));
    expect(
      client.getQueryCache().findAll({ queryKey: queryPrefix }).length,
    ).toBeGreaterThan(4);

    view.unmount();
    await tick();
    await waitFor(() =>
      expect(signals.every((signal) => signal.aborted)).toBe(true),
    );
    expect(api.getCatalogueViewportSystems).toHaveBeenCalledTimes(5);
    expect(
      client.getQueryCache().findAll({ queryKey: queryPrefix }),
    ).toHaveLength(0);
    expect(client.getQueryData(['unrelated'])).toBe('keep');
  });

  it('waits for foreground ranking, autocomplete, density and journey requests, then for idle', async () => {
    vi.useFakeTimers();
    const finish: (() => void)[] = [];
    const foreground = [
      queryKeys.rankings({
        archetype: 'manufacturing_hub',
        min_score: 60,
        page_size: 24,
        offset: 0,
      }),
      queryKeys.autocomplete('sol'),
      ['map-heatmap', 1_000],
      [...queryKeys.all, 'map', 'commander-history', 'commander'],
    ].map((queryKey) =>
      client.fetchQuery({
        queryKey,
        queryFn: () =>
          new Promise<null>((resolve) => finish.push(() => resolve(null))),
      }),
    );
    render(GalaxyStarQueryTestHost, props(client, camera()));
    await vi.advanceTimersByTimeAsync(1_000);
    expect(api.getCatalogueViewportSystems).not.toHaveBeenCalled();
    for (const complete of finish.slice(0, -1)) complete();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(api.getCatalogueViewportSystems).not.toHaveBeenCalled();
    finish.at(-1)!();
    await Promise.all(foreground);
    await vi.advanceTimersByTimeAsync(GALAXY_STAR_IDLE_DELAY_MS - 1);
    expect(api.getCatalogueViewportSystems).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(api.getCatalogueViewportSystems).toHaveBeenCalled();
  });

  it.each([404, 503])(
    'stops the whole layer after one unavailable probe (%s), including later viewports',
    async (status) => {
      vi.useFakeTimers();
      vi.mocked(api.getCatalogueViewportSystems).mockRejectedValue(
        new api.ApiError(status, '/api/map/systems', {}),
      );
      const view = render(GalaxyStarQueryTestHost, props(client, camera()));
      await vi.advanceTimersByTimeAsync(1_000);
      expect(api.getCatalogueViewportSystems).toHaveBeenCalledOnce();
      expect(screen.getByTestId('catalogue-packet')).toHaveAttribute(
        'data-error',
        'true',
      );
      expect(screen.getByTestId('catalogue-packet')).toHaveAttribute(
        'data-pending',
        'false',
      );
      await view.rerender(props(client, camera(20_000, 20_000)));
      await vi.advanceTimersByTimeAsync(120_000);
      expect(api.getCatalogueViewportSystems).toHaveBeenCalledOnce();
      expect(screen.getByTestId('catalogue-packet')).toHaveAttribute(
        'data-error',
        'true',
      );
    },
  );

  it('keeps the single wide sample and its full 8,000-star budget', async () => {
    vi.useFakeTimers();
    const state = camera(0, 0, 118_000);
    vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue({
      systems: Array.from({ length: 8_000 }, (_, index) => ({
        id64: parseId64(String(index + 1)),
        name: `Wide ${index}`,
        x: index,
        y: 0,
        z: 0,
        populated: false,
      })),
      truncated: false,
    });
    render(GalaxyStarQueryTestHost, {
      ...props(client, state),
      viewport: galaxyStarViewport(state, 'wide', viewportSize),
    });
    await vi.advanceTimersByTimeAsync(1_000);
    expect(api.getCatalogueViewportSystems).toHaveBeenCalledOnce();
    expect(packet()!.systems).toHaveLength(8_000);
    expect(packet()!.truncated).toBe(false);
  });

  it('ignores a late unavailable response from an already cancelled viewport', async () => {
    vi.useFakeTimers();
    let rejectStale!: (error: Error) => void;
    vi.mocked(api.getCatalogueViewportSystems).mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectStale = reject;
        }),
    );
    const view = render(GalaxyStarQueryTestHost, props(client, camera()));
    await vi.advanceTimersByTimeAsync(1_000);
    const signal = vi.mocked(api.getCatalogueViewportSystems).mock.calls[0][1]!;
    await view.rerender(props(client, camera(30_000, 30_000)));
    await vi.advanceTimersByTimeAsync(0);
    expect(signal.aborted).toBe(true);
    rejectStale(new api.ApiError(503, '/api/map/systems', {}));
    await vi.advanceTimersByTimeAsync(1_000);
    expect(screen.getByTestId('catalogue-packet')).toHaveAttribute(
      'data-error',
      'false',
    );
    expect(packet()!.systems.length).toBeGreaterThan(0);
  });

  it('reserves connections for foreground work, starts nearest cells first and cancels obsolete queued cells', async () => {
    vi.useFakeTimers();
    const state = camera(5_000, 5_000, 9_000);
    const initial = {
      ...props(client, state),
      viewport: galaxyStarViewport(state, 'local', {
        width: 2_100,
        height: 900,
      }),
    };
    const expected = [...planGalaxyStarCells(initial.viewport!)].sort(
      (a, b) => {
        const distance = (cell: typeof a) =>
          (cell.centre.x - 5_000) ** 2 +
          cell.centre.y ** 2 +
          (cell.centre.z - 5_000) ** 2;
        return distance(a) - distance(b) || a.key.localeCompare(b.key);
      },
    );
    expect(expected.length).toBeGreaterThan(5);
    let active = 0;
    let maximum = 0;
    const downloads: { signal: AbortSignal; finish: () => void }[] = [];
    vi.mocked(api.getCatalogueViewportSystems).mockImplementation(
      (_request, signal) => {
        active++;
        maximum = Math.max(maximum, active);
        return new Promise((resolve, reject) => {
          const finish = () => {
            active--;
            resolve({ systems: [], truncated: false });
          };
          downloads.push({ signal: signal!, finish });
          signal!.addEventListener(
            'abort',
            () => {
              active--;
              reject(new DOMException('Aborted', 'AbortError'));
            },
            { once: true },
          );
        });
      },
    );
    const view = render(GalaxyStarQueryTestHost, initial);
    await vi.advanceTimersByTimeAsync(GALAXY_STAR_IDLE_DELAY_MS);
    expect(downloads).toHaveLength(1);
    downloads[0].finish();
    await vi.advanceTimersByTimeAsync(0);
    expect(active).toBe(4);
    expect(
      vi
        .mocked(api.getCatalogueViewportSystems)
        .mock.calls.map(([request]) => request),
    ).toEqual(expected.slice(0, 5).map((cell) => cell.request));

    // Model Firefox's six-connection budget: four catalogue slots leave room
    // for a foreground request, and no queued cell can refill while it runs.
    let completeForeground!: () => void;
    const foreground = client.fetchQuery({
      queryKey: queryKeys.autocomplete('sol'),
      queryFn: () => {
        expect(active).toBeLessThan(6);
        return new Promise<null>((resolve) => {
          completeForeground = () => resolve(null);
        });
      },
    });
    for (const download of downloads.slice(1)) download.finish();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(downloads).toHaveLength(5);
    expect(active).toBe(0);

    // Move before releasing the foreground request. None of the old queued
    // cells may hit the transport after the move.
    await view.rerender(props(client, camera(30_000, 30_000, 9_000)));
    await vi.advanceTimersByTimeAsync(0);
    completeForeground();
    await foreground;
    await vi.advanceTimersByTimeAsync(GALAXY_STAR_IDLE_DELAY_MS);
    const replacementCalls = vi
      .mocked(api.getCatalogueViewportSystems)
      .mock.calls.slice(5);
    expect(replacementCalls.length).toBeGreaterThan(0);
    expect(replacementCalls.every(([request]) => request.minX >= 10_000)).toBe(
      true,
    );
    expect(maximum).toBe(4);
    const current = downloads.slice(5);
    await view.rerender(props(client, camera(-30_000, -10_000, 9_000)));
    await vi.advanceTimersByTimeAsync(0);
    expect(current.every(({ signal }) => signal.aborted)).toBe(true);
  });
});
