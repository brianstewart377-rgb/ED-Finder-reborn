// Find on the Explore page: the request the page's settings add up to, its
// results, the rows kept on screen while the next page loads, and the count
// of matches that comes later where counting is slow.
//
// The page creates this once and reads what it needs through it. The page's
// own values (its view state, the star searched near, the radius in force)
// are passed in as functions so that they stay live.
import {
  type DistanceAnchor,
  type ExploreSearchRequest,
  type ExploreSystem,
  type FinderPageEnvelope,
  getArchetypeRankings,
  pageIsUncounted,
  searchExploreSystems,
} from '$lib/api/client';
import { queryClient, queryKeys } from '$lib/api/query';
import { FINDER_F4_ENABLED } from '$lib/config';
import { createQuery } from '@tanstack/svelte-query';
import { untrack } from 'svelte';
import {
  type FinderRankingState,
  lastPageCorrection,
  selectFinderRequest,
} from './ranking-state';
import { type SearchRadius } from './search-state';
import type { ExploreResultCache } from './explore-result-cache';
import type { ExploreViewState } from './explore-view-state.svelte.ts';
import type { ExploreAnchor } from './navigation';

/** One answer from Find, with what it was asked for kept beside it. */
export type FinderResponse = {
  results: readonly ExploreSystem[];
  page: FinderPageEnvelope;
  finderState?: FinderRankingState;
  finderIdentity?: string;
  /** Keep retained rows labelled with the radius that produced them. */
  searchRadius?: SearchRadius | null;
};

export function createFinderResults(source: {
  viewState: () => ExploreViewState;
  resultCache: () => ExploreResultCache;
  /** Whether the page is being shown again without having been left. */
  remount: boolean;
  /** The star searched near, if there is one. */
  anchor: () => ExploreAnchor | null;
  /** The search radius in force, after any cap. */
  effectiveRadius: () => SearchRadius;
}) {
  const { remount } = source;
  const viewState = $derived(source.viewState());
  const resultCache = $derived(source.resultCache());
  const anchor = $derived(source.anchor());
  const effectiveRadius = $derived(source.effectiveRadius());

  const resultsEnabled = $derived(
    viewState.rankingUrlReady &&
      viewState.hasResultsIntent &&
      (!viewState.nearId || anchor !== null),
  );
  const finderRequest = $derived.by(() => {
    const selected = selectFinderRequest(
      viewState.rankingState,
      anchor ? { x: anchor.x, y: anchor.y, z: anchor.z } : null,
      {
        enabled: FINDER_F4_ENABLED,
        radius: effectiveRadius,
        reach: viewState.reach,
        filtered: Object.keys(viewState.bodyFilters).length > 0,
      },
    );
    if (selected.kind === 'any' && Object.keys(viewState.bodyFilters).length)
      return {
        ...selected,
        request: { ...selected.request, body_filters: viewState.bodyFilters },
      };
    return selected;
  });
  const anchorReference = $derived<DistanceAnchor | null>(
    anchor ? { id64: anchor.id64, name: anchor.name } : null,
  );
  const searchRequest = $derived<ExploreSearchRequest>(
    finderRequest.kind === 'any' ? finderRequest.request : {},
  );
  const finderQueryKey = $derived(
    finderRequest.kind === 'rankings'
      ? queryKeys.rankings(finderRequest.request)
      : queryKeys.explore(
          finderRequest.request,
          anchorReference,
          FINDER_F4_ENABLED ? finderRequest.pageSize : null,
        ),
  );
  const finderIdentity = $derived(JSON.stringify(finderQueryKey));
  const results = createQuery<FinderResponse>(() => {
    if (!FINDER_F4_ENABLED) {
      const request = searchRequest;
      const reference = anchorReference;
      return {
        queryKey: queryKeys.explore(request, reference),
        gcTime: resultCache.gcTime(queryKeys.explore(request, reference)),
        queryFn: ({ signal }) =>
          searchExploreSystems(request, signal, reference),
        enabled: resultsEnabled,
        refetchOnMount: !remount,
        retryOnMount: !remount,
        retry: 0,
      };
    }
    // Capture the complete request identity with its envelope. Placeholder rows
    // keep their old mode, floor and offset until the replacement arrives.
    const request = finderRequest;
    const reference = anchorReference;
    const state = viewState.rankingState;
    const identity = finderIdentity;
    const searchRadius =
      request.kind === 'any' && reference ? effectiveRadius : null;
    return {
      queryKey: finderQueryKey,
      gcTime: resultCache.gcTime(finderQueryKey),
      queryFn: async ({ signal }: { signal: AbortSignal }) => ({
        ...(request.kind === 'rankings'
          ? await getArchetypeRankings(request.request, signal)
          : await searchExploreSystems(
              request.request,
              signal,
              reference,
              request.pageSize,
            )),
        finderState: state,
        finderIdentity: identity,
        searchRadius,
      }),
      enabled: resultsEnabled,
      staleTime: 0,
      refetchOnMount: !remount,
      retryOnMount: !remount,
      placeholderData: (previous: FinderResponse | undefined) => previous,
      retry: 0,
    };
  });
  // Query data is immutable and structurally shared. A deep $state proxy here
  // would give retained rows a different identity and rebuild their map layer.
  let retainedResults = $state.raw<FinderResponse | undefined>(
    untrack(() =>
      viewState.displayedResultsKey
        ? queryClient.getQueryData<FinderResponse>(
            viewState.displayedResultsKey,
          )
        : undefined,
    ),
  );
  $effect(() => {
    if (!viewState.hasResultsIntent) {
      retainedResults = undefined;
      viewState.displayedResultsKey = null;
    } else if (
      FINDER_F4_ENABLED &&
      results.data &&
      !results.isPlaceholderData
    ) {
      retainedResults = results.data;
      // The envelope carries the identity that produced these rows, even
      // while the observer is switching to another request.
      if (results.data.finderIdentity)
        viewState.displayedResultsKey = JSON.parse(results.data.finderIdentity);
    }
  });
  $effect(() => {
    resultCache.retain([
      finderQueryKey,
      ...(viewState.displayedResultsKey ? [viewState.displayedResultsKey] : []),
    ]);
  });
  const displayedResults = $derived(
    viewState.hasResultsIntent
      ? (results.data ?? (FINDER_F4_ENABLED ? retainedResults : undefined))
      : undefined,
  );
  // Only F4 retains previous rows while a new query loads. The default-off
  // path replaces its list with a loading state until the current query arrives.
  const displayedSearchRadius = $derived(
    FINDER_F4_ENABLED
      ? displayedResults?.searchRadius
      : anchor
        ? effectiveRadius
        : null,
  );
  const currentResponse = $derived(
    FINDER_F4_ENABLED &&
      resultsEnabled &&
      !results.isPlaceholderData &&
      results.data?.finderIdentity === finderIdentity &&
      results.data.page.page_size === viewState.rankingState.pageSize &&
      results.data.page.offset === viewState.rankingState.offset,
  );
  const pageCorrection = $derived(
    FINDER_F4_ENABLED && currentResponse && !results.isFetching && results.data
      ? lastPageCorrection(results.data.page)
      : null,
  );
  const updatingResults = $derived(
    FINDER_F4_ENABLED &&
      viewState.hasResultsIntent &&
      !results.isError &&
      (results.isFetching || !currentResponse || pageCorrection !== null),
  );
  // The API answers a page before its matches are counted where counting
  // them is slow, so that the rows do not wait for it (and a count that runs
  // out of time cannot fail them). The count is then asked for on its own:
  // the same search, told to wait for the count.
  const pageToCount = $derived(
    FINDER_F4_ENABLED &&
      currentResponse &&
      !results.isFetching &&
      finderRequest.kind === 'any' &&
      results.data !== undefined &&
      pageIsUncounted(results.data.page)
      ? finderRequest
      : null,
  );
  const laterCount = FINDER_F4_ENABLED
    ? createQuery(askForTheCountLater)
    : undefined;
  function askForTheCountLater() {
    const request = pageToCount;
    const reference = anchorReference;
    const asked =
      request?.kind === 'any'
        ? { ...request.request, wait_for_count: true }
        : null;
    return {
      queryKey: asked
        ? queryKeys.explore(asked, reference, request!.pageSize)
        : ([...queryKeys.all, 'explore', 'count', null] as const),
      queryFn: ({ signal }: { signal: AbortSignal }) => {
        if (!asked || !request) throw new Error('No count is asked for');
        return searchExploreSystems(asked, signal, reference, request.pageSize);
      },
      enabled: asked !== null,
      // A count that is in does not change until the search does.
      staleTime: Infinity,
      retry: 0,
    };
  }
  // The page as the list's summary and its Next button read it: with the
  // count that came later, once it is in.
  const shownPage = $derived.by((): FinderPageEnvelope | undefined => {
    const shown = displayedResults?.page;
    if (!shown || pageToCount === null || shown !== results.data?.page)
      return shown;
    const counted = laterCount?.data?.page;
    return counted &&
      !pageIsUncounted(counted) &&
      counted.offset === shown.offset &&
      counted.page_size === shown.page_size
      ? counted
      : shown;
  });
  const countingMatches = $derived(
    pageToCount !== null &&
      shownPage !== undefined &&
      pageIsUncounted(shownPage) &&
      laterCount !== undefined &&
      !laterCount.isError &&
      !laterCount.isSuccess,
  );

  return {
    /** The request for the page of results itself. */
    results,
    /** Whether there is anything to search for yet. */
    get resultsEnabled() {
      return resultsEnabled;
    },
    /** The star distances are measured from, as the API is told it. */
    get anchorReference() {
      return anchorReference;
    },
    /** The rows on screen: the current answer, or the last one while the next loads. */
    get displayedResults() {
      return displayedResults;
    },
    get displayedSearchRadius() {
      return displayedSearchRadius;
    },
    /** Whether the answer in hand is the one for what is asked for now. */
    get currentResponse() {
      return currentResponse;
    },
    /** A page beyond the last one, to be put right. */
    get pageCorrection() {
      return pageCorrection;
    },
    get updatingResults() {
      return updatingResults;
    },
    /** The page as the summary and the Next button read it, with the later count once it is in. */
    get shownPage() {
      return shownPage;
    },
    /** Whether the count is still being waited for. */
    get countingMatches() {
      return countingMatches;
    },
  };
}
