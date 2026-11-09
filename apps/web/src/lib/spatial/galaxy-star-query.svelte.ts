import { createQueries, useQueryClient } from '@tanstack/svelte-query';
import { untrack } from 'svelte';
import {
  ApiError,
  getCatalogueViewportSystems,
  type CatalogueViewportResponse,
} from '$lib/api/client';
import { queryKeys } from '$lib/api/query';
import type { Vec3Ly } from './contracts';
import {
  GalaxyStarCellCache,
  MAX_GALAXY_STAR_ACTIVE_CELLS,
  planGalaxyStarCells,
  refineGalaxyStarCells,
  type GalaxyStarCell,
} from './galaxy-star-cells';
import { GalaxyStarRequests } from './galaxy-star-requests';
import {
  sampleCatalogueCellSystems,
  type GalaxyStarViewport,
} from './galaxy-star-stream';
import { LOCAL_STAR_CLOSE_LIMIT } from './galaxy-detail-policy';

/** Cell queries own cached packets, with bounded sub-volumes for dense cells. */
export function createGalaxyStarQuery(
  getViewport: () => GalaxyStarViewport | null,
  getFocus: () => Vec3Ly,
) {
  const client = useQueryClient();
  const cache = new GalaxyStarCellCache();
  const requests = new GalaxyStarRequests({ probe: true });
  requests.setPaused(true);
  let unavailable = $state(false);
  const isCatalogueKey = (key: readonly unknown[]) =>
    key[queryKeys.all.length] === 'map' &&
    key[queryKeys.all.length + 1] === 'catalogue-stars';
  const prioritizeForeground = () =>
    requests.setPaused(
      client.isFetching({
        predicate: (query) => !isCatalogueKey(query.queryKey),
      }) > 0,
    );
  let previousCells: readonly GalaxyStarCell[] = [];
  const baseCells = $derived.by(() => {
    const viewport = unavailable ? null : getViewport();
    previousCells = viewport
      ? planGalaxyStarCells(viewport, previousCells)
      : [];
    return previousCells;
  });
  const cellKey = (cell: GalaxyStarCell) =>
    [...queryKeys.all, 'map', 'catalogue-stars', cell.key] as const;
  let packetRevision = $state(0);
  let previousRefinements: readonly GalaxyStarCell[] = [];
  // Re-plan after a packet arrives, including first entry directly into a
  // close view. Read the actual cache instead of retaining duplicate packets
  // or an unbounded set of truncation flags outside its eviction policy.
  // Cache notifications can run inside another component's query effect.
  // Never attach that effect to our packet revision or scheduling state.
  const unsubscribe = client.getQueryCache().subscribe((event) =>
    untrack(() => {
      // Pause synchronously, before a completion can dispatch another queued
      // cell. This includes ranking, autocomplete, density and journey queries,
      // even when they are owned by another component.
      if (!isCatalogueKey(event.query.queryKey)) prioritizeForeground();
      if (
        event.type === 'updated' &&
        (event.action.type === 'success' || event.action.type === 'error') &&
        isCatalogueKey(event.query.queryKey) &&
        // createQueries replaces its observer when the query set changes. Wait
        // for this batch to settle so refinement cannot abort pending siblings.
        previousRefinements.every(
          (cell) => client.getQueryState(cellKey(cell))?.fetchStatus === 'idle',
        )
      )
        packetRevision += 1;
    }),
  );
  const cells = $derived.by(() => {
    void packetRevision;
    const viewport = unavailable ? null : getViewport();
    const next = viewport
      ? refineGalaxyStarCells(
          viewport,
          baseCells,
          (cell) =>
            client.getQueryData<CatalogueViewportResponse>(cellKey(cell)),
          previousRefinements,
        )
      : [];
    const focus = getFocus();
    const distance = (cell: GalaxyStarCell) =>
      (cell.centre.x - focus.x) ** 2 +
      (cell.centre.y - focus.y) ** 2 +
      (cell.centre.z - focus.z) ** 2;
    const ordered = [...next].sort(
      (a, b) => distance(a) - distance(b) || a.key.localeCompare(b.key),
    );
    // Stable options also keep failed siblings from retrying on each packet.
    if (
      ordered.length !== previousRefinements.length ||
      ordered.some((cell, index) => cell.key !== previousRefinements[index].key)
    )
      previousRefinements = ordered;
    return previousRefinements;
  });
  const batch = createQueries(() => ({
    queries: cells.map((cell, priority) => ({
      queryKey: cellKey(cell),
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        requests.run(
          signal,
          async (requestSignal) => {
            let packet: CatalogueViewportResponse;
            try {
              packet = await getCatalogueViewportSystems(
                cell.request,
                requestSignal,
              );
            } catch (error) {
              if (
                !requestSignal.aborted &&
                error instanceof ApiError &&
                (error.status === 404 || error.status === 503)
              )
                unavailable = true;
              throw error;
            }
            // Keep twice a cell's share of the rendered band budget for overscan,
            // and at least the full close-view budget. A dense 40k band therefore
            // retains <=5k rows/local packet. Wide views keep their full 8k
            // sample; even an all-wide cache is bounded to 512k rows.
            const retainedLimit = cell.key.startsWith('wide:')
              ? cell.request.limit
              : Math.max(
                  LOCAL_STAR_CLOSE_LIMIT,
                  Math.ceil(
                    (2 * cell.request.limit) / MAX_GALAXY_STAR_ACTIVE_CELLS,
                  ),
                );
            const systems = sampleCatalogueCellSystems(
              packet.systems,
              retainedLimit,
            );
            return {
              systems,
              truncated:
                packet.truncated || systems.length < packet.systems.length,
            };
          },
          priority,
        ),
      staleTime: Infinity,
      gcTime: Infinity,
      retry: 0,
    })),
    // Keep a stable outer property as the cell set grows/shrinks. Publishing a
    // variable-length createQueries array can strand unrelated Svelte query
    // observers even though their HTTP requests/cache states have settled.
    combine: (results) => ({ results }),
  }));
  const queries = $derived(batch.results);
  $effect(() => {
    // A camera settle gets a short idle window as well as foreground priority.
    // Query abort signals discard the previous view's queued/active work.
    void getViewport();
    untrack(() => {
      requests.defer();
      prioritizeForeground();
    });
  });
  $effect(() => {
    // Pin parents as well as replacements: losing a parent's truncation
    // evidence would fall back to its broad request. At most 32 of 64 cache
    // entries are pinned, while only the <=16 replacement queries are active.
    const evicted = cache.retain([...baseCells, ...cells], getFocus());
    untrack(() => {
      for (const cell of evicted)
        client.removeQueries({ queryKey: cellKey(cell), exact: true });
    });
  });
  $effect(() => () => {
    unsubscribe();
    requests.dispose();
    // The cache is scoped to this page. Neither inactive cells nor unresolved
    // requests survive unmount and accumulate in the application QueryClient.
    client.removeQueries({
      queryKey: [...queryKeys.all, 'map', 'catalogue-stars'],
    });
  });
  let assembledPackets: readonly (CatalogueViewportResponse | undefined)[] = [];
  let assembledData: CatalogueViewportResponse | undefined;
  const data = $derived.by((): CatalogueViewportResponse | undefined => {
    if (unavailable || !getViewport() || queries.length !== cells.length)
      return undefined;
    // Keep the accepted view while its replacement loads, then publish all
    // successful cells even if a sibling failed. One failed cell must not
    // strand the camera on its old packet indefinitely.
    if (queries.some((query) => query.isPending)) return undefined;
    if (queries.length && queries.every((query) => !query.data))
      return undefined;
    const packets = queries.map((query) => query.data);
    if (
      assembledData &&
      packets.length === assembledPackets.length &&
      packets.every((packet, index) => packet === assembledPackets[index])
    )
      return assembledData;
    // This is a transient assembly index, not reactive application state.
    // eslint-disable-next-line svelte/prefer-svelte-reactivity
    const systems = new Map<
      string,
      CatalogueViewportResponse['systems'][number]
    >();
    for (const query of queries) {
      for (const system of query.data?.systems ?? [])
        systems.set(system.id64, system);
    }
    assembledPackets = packets;
    assembledData = {
      systems: [...systems.values()],
      truncated: queries.some((query) => !query.data || query.data.truncated),
    };
    return assembledData;
  });
  return {
    get data() {
      return data;
    },
    get isPending() {
      return !unavailable && queries.some((query) => query.isPending);
    },
    get isFetching() {
      return !unavailable && queries.some((query) => query.isFetching);
    },
    get isError() {
      return unavailable || queries.some((query) => query.isError);
    },
  };
}
