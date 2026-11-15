<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { fromStore } from 'svelte/store';
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { tick, untrack } from 'svelte';
  import { ArrowRight, Search, Sparkles } from '@lucide/svelte';
  import {
    ApiError,
    autocompleteSystems,
    getCatalogueViewportSystems,
    getCommanderTravelHeatmap,
    getCommanderJournalSummary,
    getArchetypeRankings,
    getSystem,
    searchExploreSystems,
    type AutocompleteSystem,
    type DistanceAnchor,
    type ExploreSearchRequest,
    type ExploreSystem,
    type FinderPageEnvelope,
  } from '$lib/api/client';
  import { queryClient, queryKeys } from '$lib/api/query';
  import { auth } from '$lib/auth/auth';
  import { FINDER_F4_ENABLED } from '$lib/config';
  import { parseId64, type Id64 } from '$lib/domain/id64';
  import {
    hasRankingSearchParams,
    lastPageCorrection,
    normalizeRankingState,
    selectFinderRequest,
    serializeRankingSearchParams,
    stripRankingSearchParams,
    type FinderRankingState,
  } from './ranking-state';
  import { archetypeLabel } from './archetypes';
  import {
    inspectHref,
    navigateWithAnchor,
    readExploreAnchor,
  } from './navigation';
  import ArchetypePicker from './ArchetypePicker.svelte';
  import ArchetypeExplanation from './ArchetypeExplanation.svelte';
  import FinderResultRanking from './FinderResultRanking.svelte';
  import BodyFilters from './BodyFilters.svelte';
  import { starClassLabel } from './star-label';
  import {
    normalizeBodyFilters,
    type BodyFilterKey,
    type BodyFilterValues,
  } from './body-filters';
  import {
    effectiveSearchRadius,
    FILTERED_SEARCH_RADIUS_CAP_LY,
    normalizeReach,
    normalizeSearchRadius,
    REACH_OPTIONS,
    SEARCH_RADIUS_OPTIONS,
    serializeExploreSearchParams,
    type Reach,
    type SearchRadius,
  } from './search-state';
  import type { ExploreResultCache } from './explore-result-cache';
  import type { ExploreViewState } from './explore-view-state.svelte.ts';
  import MapToolPopout from './MapToolPopout.svelte';
  import ExploreMapWindow from './ExploreMapWindow.svelte';
  import GalaxyMap from '$lib/galaxy-map/GalaxyMap.svelte';
  import {
    boxAround,
    chosenFromFind,
    JOURNEYS_ASKED_FOR,
    showingInWords,
    starClassInBox,
    starsInRange,
    type Journey,
    type MapSystem,
    type Showing,
  } from '$lib/galaxy-map/map-content';
  import { stellarPresentation } from '$lib/spatial/stellar-presentation';
  import WorkspaceHeader from '$lib/components/WorkspaceHeader.svelte';
  import SystemActions from './SystemActions.svelte';
  import WatchlistPanel from './WatchlistPanel.svelte';
  import ComparePanel from './ComparePanel.svelte';

  let { viewState, resultCache } = $props<{
    viewState: ExploreViewState;
    resultCache: ExploreResultCache;
  }>();
  const { selectedSystem, pins, compare, layerPreferences } = untrack(
    () => viewState,
  );
  // Only a layout remount reuses this instance. A fresh route visit retains
  // the existing query freshness/refetch policy.
  const remount = untrack(() => viewState.hasMounted);
  untrack(() => {
    viewState.hasMounted = true;
  });
  const session = fromStore(auth);
  // The travel heatmap is the signed-in account's own V3 journal travel; the
  // cache key carries the account so a sign-out/switch never reuses rows.
  const travelAccountId = $derived(
    session.current.loading ? null : (session.current.user?.account_id ?? null),
  );
  const reviewLabRun = import.meta.env.VITE_REVIEW_LAB === '1';
  let activeSuggestion = $state(-1);
  let autocompleteOpen = $state(false);
  const anchorDetail = createQuery(() => {
    const id64 = viewState.nearId;
    return {
      queryKey: [...queryKeys.all, 'explore', 'selected-point', id64],
      queryFn: ({ signal }: { signal: AbortSignal }) => {
        if (!id64) throw new Error('No search anchor');
        return getSystem(id64, signal);
      },
      enabled: id64 !== null && viewState.historyAnchor?.id64 !== id64,
      retry: 0,
    };
  });
  const anchor = $derived(viewState.resolveAnchor(anchorDetail.data));
  $effect(() => {
    // Only the small, validated view anchor survives the layout. The detail
    // response and observer keep their existing query ownership.
    viewState.resolvedAnchor = anchor;
  });
  // A colony type searched near a system: nearest first, kept to the
  // systems whose fit reaches the minimum tier.
  const typeNearAnchor = $derived(
    FINDER_F4_ENABLED &&
      anchor !== null &&
      viewState.rankingState.archetype !== 'any',
  );
  // Body filters and a picked type cap the anchored search at the API's
  // filtered radius; the URL keeps the chosen radius so it resumes when the
  // filters are cleared or Any is chosen again.
  const effectiveRadius = $derived(
    effectiveSearchRadius(
      viewState.radius,
      viewState.bodyFilters,
      typeNearAnchor,
    ),
  );
  const radiusCapped = $derived(
    typeNearAnchor || Object.keys(viewState.bodyFilters).length > 0,
  );
  let searchInput = $state<HTMLInputElement>();
  let mapSection = $state<HTMLDivElement>();
  // The control that hid the Finder panel gets focus back when it reopens.
  let panelToggleOrigin: HTMLElement | null = null;
  let stage = $state<HTMLDivElement>();
  let stageTop = $state(0);
  // The galaxy map itself, for the camera buttons of the page.
  let galaxyMap = $state<ReturnType<typeof GalaxyMap>>();
  let mapPhase = $state<'starting' | 'ready' | 'failed'>('starting');
  // Must match the phone breakpoint in this component's styles.
  const PHONE_STAGE_QUERY = '(max-width: 900px)';
  // At phone width the Finder and the map take turns, so while the Finder is
  // shown the map cannot be seen.
  let phoneWidth = $state(false);
  $effect(() => {
    const query = window.matchMedia?.(PHONE_STAGE_QUERY);
    if (!query) return;
    phoneWidth = query.matches;
    const changed = () => (phoneWidth = query.matches);
    query.addEventListener?.('change', changed);
    return () => query.removeEventListener?.('change', changed);
  });
  const mapOnScreen = $derived(!(phoneWidth && viewState.finderPanelOpen));
  $effect(() => {
    const element = stage;
    if (!element) return;
    // The selected-system bar above the page comes and goes with the
    // selection, which moves the stage without a window resize.
    void $selectedSystem.value;
    void $selectedSystem.hydrated;
    // The map fills the window below the site header. The header wraps at
    // narrower widths, so measure where the stage starts in the document.
    const measure = () => {
      stageTop = Math.max(
        0,
        Math.round(element.getBoundingClientRect().top + window.scrollY),
      );
    };
    measure();
    const frame = requestAnimationFrame(measure);
    window.addEventListener('resize', measure);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', measure);
    };
  });
  let resultList = $state<HTMLUListElement>();
  let restoredResultScroll = false;
  $effect(() => {
    const list = resultList;
    if (list && !restoredResultScroll) {
      restoredResultScroll = true;
      untrack(() => viewState.restoreResultScroll(list));
    } else if (list) {
      // An explicit search can replace the list with a new element. Preserve
      // that existing reset rather than restoring another result set's scroll.
      untrack(() => viewState.captureResultScroll(list.scrollTop));
    }
  });
  const showTravelHeatmap = $derived($layerPreferences.value.journeys);

  const normalizedQuery = $derived(viewState.query.trim());
  const suggestions = createQuery(() => ({
    queryKey: queryKeys.autocomplete(normalizedQuery.toLocaleLowerCase()),
    queryFn: ({ signal }) => autocompleteSystems(normalizedQuery, signal),
    enabled: normalizedQuery.length >= 2,
    staleTime: 60_000,
  }));
  const suggestionRows = $derived(suggestions.data?.results ?? []);

  // Body filters join every search: near a system, and galaxy-wide, where
  // they thin the window of the best 10,000. The radius needs a system.
  const radiusDisabled = $derived(!anchor);
  const galaxyFiltered = $derived(
    !anchor && Object.keys(viewState.bodyFilters).length > 0,
  );
  const radiusCappedByFilters = $derived(
    !radiusDisabled && effectiveRadius !== viewState.radius,
  );
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
  type FinderResponse = {
    results: readonly ExploreSystem[];
    page: FinderPageEnvelope;
    finderState?: FinderRankingState;
    finderIdentity?: string;
    /** Keep retained rows labelled with the radius that produced them. */
    searchRadius?: SearchRadius | null;
  };
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
  const activeArchetypeLabel = $derived(
    archetypeLabel(viewState.rankingState.archetype),
  );
  const galaxyFilteredNote = $derived(
    galaxyFiltered ? ' · body filters within the best 10,000' : '',
  );
  const resultsScope = $derived(
    typeNearAnchor && anchor
      ? `Within ${effectiveRadius.toLocaleString('en-US')} LY of ${anchor.name} · ${activeArchetypeLabel} fit, minimum tier ${viewState.rankingState.minimumTier} · sorted by distance`
      : viewState.rankingState.archetype !== 'any'
        ? `Galaxy-wide · ranked by ${activeArchetypeLabel} fit · minimum tier ${viewState.rankingState.minimumTier}${galaxyFilteredNote}`
        : anchor
          ? `Within ${effectiveRadius.toLocaleString('en-US')} LY of ${anchor.name} · sorted by distance`
          : `Galaxy-wide · best colony potential${galaxyFilteredNote}`,
  );
  const rankingDataNotReady = $derived.by(() => {
    const error = results.error;
    if (!(error instanceof ApiError) || error.status !== 503) return false;
    const body = error.body;
    const problem =
      body &&
      typeof body === 'object' &&
      'detail' in body &&
      body.detail &&
      typeof body.detail === 'object'
        ? body.detail
        : body;
    return (
      !!problem &&
      typeof problem === 'object' &&
      'reason' in problem &&
      problem.reason === 'finder_products_not_ready'
    );
  });

  $effect(() => {
    const current = page.url;
    const restoredAnchor = anchor;
    const identity = restoredAnchor ? JSON.stringify(restoredAnchor) : '';
    if (
      identity !== viewState.hydratedAnchorIdentity ||
      current.href !== viewState.hydratedExploreUrl
    ) {
      viewState.hydratedAnchorIdentity = identity;
      viewState.hydratedExploreUrl = current.href;
      viewState.query = restoredAnchor?.name ?? '';
    }
    if (FINDER_F4_ENABLED && $selectedSystem.value !== viewState.urlSelection) {
      selectedSystem.set(viewState.urlSelection);
    }
    if (!viewState.rankingUrlReady) {
      if (
        viewState.parsedSearch.issues.length ||
        viewState.parsedRanking?.issues.length
      )
        viewState.rankingNotice = viewState.parsedSearch.issues.length
          ? 'Invalid search link option removed.'
          : 'Unsupported link option removed.';
      void navigateWithAnchor(
        `${current.pathname}${viewState.canonicalSearch}${current.hash}`,
        {
          replaceState: true,
          state: {
            ...(viewState.invalidAnchorState ||
            (current.searchParams.has('near') && !viewState.parsedSearch.near)
              ? { anchor: null }
              : {}),
            // Explicit default/ignored URL parameters can canonicalize away.
            // Keep that link's search intent in its history entry.
            finderRequested:
              FINDER_F4_ENABLED &&
              !(
                viewState.rejectedNear &&
                viewState.rankingState.archetype === 'any'
              ) &&
              (hasRankingSearchParams(current.searchParams) ||
                page.state.finderRequested === true),
          },
        },
      );
    }
  });

  $effect(() => {
    // A lookup can fail, return a different id, or lack usable coordinates.
    // Never let that turn an intended nearby search into a galaxy-wide one.
    if (
      !viewState.rankingUrlReady ||
      !viewState.nearId ||
      anchor ||
      anchorDetail.isPending ||
      anchorDetail.isFetching
    )
      return;
    viewState.rankingNotice =
      'Search anchor could not be restored; choose a system again.';
    // An Any-mode link's pagination belonged to the nearby search; dropping
    // the anchor must leave no results intent behind, or the page would run a
    // galaxy-wide search the user never asked for. A picked archetype is a
    // galaxy-wide ranking in its own right and keeps its parameters.
    let params = serializeExploreSearchParams(
      {
        near: null,
        bodyFilters: viewState.bodyFilters,
        radius: viewState.radius,
        reach: viewState.reach,
      },
      page.url.searchParams,
    );
    if (viewState.rankingState.archetype === 'any')
      params = stripRankingSearchParams(params);
    void navigateWithAnchor(exploreDestination(params), {
      replaceState: true,
      state: { anchor: null, finderRequested: false },
    });
  });

  $effect(() => {
    if (pageCorrection === null) return;
    viewState.rankingNotice = 'Page reset to the last available results.';
    const params = serializeRankingSearchParams(
      { ...viewState.rankingState, offset: pageCorrection },
      page.url.searchParams,
    );
    void navigateWithAnchor(exploreDestination(params), {
      replaceState: true,
      state: { finderRequested: true },
    });
  });

  function exploreDestination(params: URLSearchParams): string {
    const search = params.toString();
    return `${page.url.pathname}${search ? `?${search}` : ''}${page.url.hash}`;
  }

  function commitRanking(
    patch: Partial<FinderRankingState>,
    resetOffset = true,
  ): void {
    viewState.rankingNotice = '';
    // A later picker/page action supersedes an unfinished anchor autofocus.
    viewState.focusedAnchorRevision = viewState.anchorSelectionRevision;
    const state = normalizeRankingState({
      ...viewState.rankingState,
      ...patch,
      ...(resetOffset ? { offset: 0 } : {}),
    });
    void navigateWithAnchor(
      exploreDestination(
        serializeRankingSearchParams(state, page.url.searchParams),
      ),
      {
        state: {
          // Paging back to defaults can remove the last URL parameter too.
          finderRequested:
            (patch.offset !== undefined || patch.pageSize !== undefined) &&
            viewState.hasResultsIntent,
        },
      },
    );
  }

  function commitSelection(id64: Id64): void {
    if (FINDER_F4_ENABLED) {
      const params = new URLSearchParams(page.url.searchParams);
      params.set('selected', id64);
      void navigateWithAnchor(
        exploreDestination(
          serializeRankingSearchParams(viewState.rankingState, params),
        ),
      );
    } else selectedSystem.set(id64);
  }

  async function clearAnchor(): Promise<void> {
    autocompleteOpen = false;
    viewState.query = '';
    const params = serializeRankingSearchParams(
      { ...viewState.rankingState, offset: 0 },
      serializeExploreSearchParams(
        {
          near: null,
          bodyFilters: viewState.bodyFilters,
          radius: viewState.radius,
          reach: viewState.reach,
        },
        page.url.searchParams,
      ),
    );
    await navigateWithAnchor(exploreDestination(params), {
      state: { anchor: null, finderRequested: false },
    });
    viewState.rankingNotice = viewState.hasResultsIntent
      ? 'Anchor cleared; searching galaxy-wide.'
      : '';
    searchInput?.focus();
  }

  function commitBodyFilters(value: BodyFilterValues): Promise<void> {
    return commitSearchInputs(value, viewState.radius);
  }

  function commitRadius(value: number): void {
    const nextRadius = normalizeSearchRadius(value);
    if (radiusDisabled || nextRadius === viewState.radius) return;
    void commitSearchInputs(viewState.bodyFilters, nextRadius);
  }

  function commitReach(value: number): void {
    const nextReach = normalizeReach(value);
    if (nextReach === viewState.reach) return;
    void commitSearchInputs(
      viewState.bodyFilters,
      viewState.radius,
      nextReach,
      true,
    );
  }

  async function commitSearchInputs(
    value: BodyFilterValues,
    nextRadius: SearchRadius,
    nextReach: Reach = viewState.reach,
    // Filters, radius and reach all apply to the search, so any change
    // starts the results again from the first page.
    resultsChange = true,
  ): Promise<void> {
    viewState.focusedAnchorRevision = viewState.anchorSelectionRevision;
    const committed = normalizeBodyFilters(value);
    const params = serializeExploreSearchParams(
      {
        near: viewState.nearId,
        bodyFilters: committed,
        radius: nextRadius,
        reach: nextReach,
      },
      page.url.searchParams,
    );
    // Paused chips do not take part in the active search, so clearing them
    // must not move a ranking off its current page.
    const nextRankingState = resultsChange
      ? { ...viewState.rankingState, offset: 0 }
      : viewState.rankingState;
    if (resultsChange) params.delete('offset');
    const destination = exploreDestination(
      FINDER_F4_ENABLED
        ? serializeRankingSearchParams(nextRankingState, params)
        : stripRankingSearchParams(params),
    );
    // Applying the same draft still means one refresh. A changed query is
    // fetched by its observer exactly once after URL hydration.
    if (
      destination === `${page.url.pathname}${page.url.search}${page.url.hash}`
    ) {
      if (resultsEnabled && resultsChange) await results.refetch();
      return;
    }
    if (resultsChange) {
      const next = selectFinderRequest(
        { ...viewState.rankingState, offset: 0 },
        anchor,
        {
          enabled: FINDER_F4_ENABLED,
          radius: effectiveSearchRadius(nextRadius, committed, typeNearAnchor),
          reach: nextReach,
          filtered: Object.keys(committed).length > 0,
        },
      );
      if (next.kind === 'any') {
        const request = Object.keys(committed).length
          ? { ...next.request, body_filters: committed }
          : next.request;
        await queryClient.invalidateQueries({
          queryKey: queryKeys.explore(
            request,
            anchorReference,
            FINDER_F4_ENABLED ? next.pageSize : null,
          ),
          exact: true,
          refetchType: 'none',
        });
      }
    }
    await navigateWithAnchor(destination);
  }

  function removeBodyFilter(key: BodyFilterKey): void {
    const next = { ...viewState.bodyFilters };
    delete next[key];
    void commitBodyFilters(next);
  }

  function followInspect(event: MouseEvent, id64: Id64): void {
    if (
      !FINDER_F4_ENABLED ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    event.preventDefault();
    void navigateWithAnchor(inspectHref(id64, page.url));
  }

  // "My journeys": every system the signed-in account has visited, from its
  // imported journals, asked for once as one marker for each system. When
  // there are more than the API returns at once, it returns the most
  // recently visited.
  const JOURNEYS_REQUEST = {
    minX: -100_000,
    maxX: 100_000,
    minY: -100_000,
    maxY: 100_000,
    minZ: -100_000,
    maxZ: 100_000,
    limit: JOURNEYS_ASKED_FOR,
  } as const;
  // At this zoom the API answers with one marker for each system, not cells.
  const JOURNEYS_ZOOM = 1;
  const commanderVisits = createQuery(() => ({
    queryKey: queryKeys.commanderTravel(
      travelAccountId ?? '',
      JOURNEYS_REQUEST,
    ),
    queryFn: ({ signal }) =>
      getCommanderTravelHeatmap(JOURNEYS_REQUEST, JOURNEYS_ZOOM, signal),
    enabled: showTravelHeatmap && travelAccountId !== null,
    staleTime: 5 * 60_000,
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 401) && failureCount < 1,
  }));
  const journalSummary = createQuery(() => ({
    queryKey: [...queryKeys.auth(), travelAccountId, 'journal-summary'],
    queryFn: ({ signal }) => getCommanderJournalSummary(signal),
    enabled: showTravelHeatmap && travelAccountId !== null,
    staleTime: 20_000,
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 401) && failureCount < 1,
  }));
  const travelHeatmapState = $derived.by(
    ():
      | 'checking'
      | 'auth-error'
      | 'summary-error'
      | 'signed-out'
      | 'off'
      | 'no-journals'
      | 'loading'
      | 'error'
      | 'empty'
      | 'ready' => {
      if (session.current.loading) return 'checking';
      // A failed session check is not "signed out": the visitor may well be
      // signed in, so offer a retry rather than a sign-in prompt.
      if (travelAccountId === null)
        return session.current.error ? 'auth-error' : 'signed-out';
      if (!showTravelHeatmap) return 'off';
      if (
        journalSummary.isError &&
        journalSummary.error instanceof ApiError &&
        journalSummary.error.status === 401
      )
        return 'signed-out';
      if (commanderVisits.isError)
        return commanderVisits.error instanceof ApiError &&
          commanderVisits.error.status === 401
          ? 'signed-out'
          : 'error';
      if (!commanderVisits.data?.count && journalSummary.isError)
        return 'summary-error';
      if (
        !commanderVisits.data?.count &&
        !journalSummary.isError &&
        journalSummary.data?.imported_files === 0
      )
        return 'no-journals';
      if (!commanderVisits.data) return 'loading';
      return commanderVisits.data.count === 0 ? 'empty' : 'ready';
    },
  );
  const journeys = $derived.by((): Journey[] | null => {
    // Never draw cached private travel once the query is failing (an expired
    // session keeps the last data alongside a 401).
    if (
      !showTravelHeatmap ||
      travelAccountId === null ||
      travelHeatmapState === 'signed-out' ||
      commanderVisits.isError ||
      !commanderVisits.data
    )
      return null;
    return commanderVisits.data.visits.flatMap((visit) =>
      visit.kind === 'marker' && visit.systemId64
        ? [
            {
              id64: visit.systemId64,
              name: visit.systemName?.trim() || `System ${visit.systemId64}`,
              x: visit.x,
              y: visit.y,
              z: visit.z,
              visits: visit.visitCount,
              firstVisit: visit.firstVisitedAt,
              lastVisit: visit.lastVisitedAt,
            },
          ]
        : [],
    );
  });
  const systems = $derived(
    viewState.hasResultsIntent ? (displayedResults?.results ?? []) : [],
  );
  const selectedId = $derived(
    FINDER_F4_ENABLED ? viewState.urlSelection : $selectedSystem.value,
  );
  const selectedOnPage = $derived(
    systems.find((system) => system.id64 === selectedId),
  );
  // F4 URL selection uses result pages or exact-system resolution, independent
  // of catalogue viewport response timing.
  const knownSelectedSystem = $derived(
    selectedOnPage ??
      (viewState.retainedSelectedSystem?.id64 === selectedId
        ? viewState.retainedSelectedSystem
        : null),
  );
  const needsSelectionResolution = $derived(
    FINDER_F4_ENABLED &&
      viewState.rankingUrlReady &&
      viewState.urlSelection !== null &&
      !selectedOnPage &&
      (viewState.retainedSelectedSystem?.id64 !== viewState.urlSelection ||
        !chosenFromFind(viewState.retainedSelectedSystem)) &&
      (!viewState.hasResultsIntent || !results.isPending),
  );
  const selectedDetail = FINDER_F4_ENABLED
    ? createQuery(() => {
        const id64 = viewState.urlSelection;
        return {
          queryKey: [...queryKeys.all, 'explore', 'selected-point', id64],
          queryFn: ({ signal }: { signal: AbortSignal }) => {
            if (!id64) throw new Error('No selected system');
            return getSystem(id64, signal);
          },
          enabled: needsSelectionResolution,
          retry: 0,
        };
      })
    : undefined;
  const selectedDetailData = $derived(selectedDetail?.data);
  const reconstructedSelection = $derived<ExploreSystem | null>(
    FINDER_F4_ENABLED &&
      selectedDetailData?.id64 === viewState.urlSelection &&
      selectedDetailData
      ? {
          id64: selectedDetailData.id64,
          name: selectedDetailData.name,
          coords: {
            x: selectedDetailData.x,
            y: selectedDetailData.y,
            z: selectedDetailData.z,
          },
        }
      : null,
  );
  const selectedMapSystem = $derived.by((): ExploreSystem | null => {
    if (FINDER_F4_ENABLED)
      return (
        knownSelectedSystem ??
        reconstructedSelection ??
        (viewState.urlSelection ? { id64: viewState.urlSelection } : null)
      );
    const finder = systems.find(
      (system) => system.id64 === $selectedSystem.value,
    );
    if (finder)
      return {
        id64: finder.id64,
        name: finder.name,
        coords: finder.coords,
        main_star_type: finder.main_star_type,
        main_star_subtype: finder.main_star_subtype,
        distance: finder.distance,
        population: finder.population,
        primaryEconomy: finder.primaryEconomy,
        allegiance: finder.allegiance,
        security: finder.security,
      };
    if (viewState.retainedSelectedSystem?.id64 === selectedId)
      return viewState.retainedSelectedSystem;
    // The star searched near is selected when it is chosen.
    return anchor && anchor.id64 === selectedId
      ? {
          id64: anchor.id64,
          name: anchor.name,
          coords: { x: anchor.x, y: anchor.y, z: anchor.z },
        }
      : null;
  });
  const selectedMapName = $derived(
    selectedMapSystem
      ? (selectedMapSystem.name ?? `System ${selectedMapSystem.id64}`)
      : null,
  );
  // The map shows the selected system: it flies there and shows the real
  // stars within 15 ly of it. A system whose position is not known cannot be
  // shown.
  const chosenSystem = $derived(chosenFromFind(selectedMapSystem));
  const starsNearChosen = createQuery(() => {
    const chosen = chosenSystem;
    const box = chosen ? boxAround(chosen) : null;
    return {
      queryKey: [
        ...queryKeys.all,
        'map',
        'stars-in-range',
        chosen?.id64 ?? null,
        JSON.stringify(box),
      ],
      queryFn: ({ signal }: { signal: AbortSignal }) => {
        if (!box) throw new Error('No system is chosen');
        return getCatalogueViewportSystems(box, signal);
      },
      // A map that could not start has nowhere to show them.
      enabled: box !== null && mapPhase !== 'failed',
      staleTime: 5 * 60_000,
      retry: 1,
    };
  });
  const showing = $derived.by((): Showing | null => {
    const chosen = chosenSystem;
    if (!chosen) return null;
    const facts = {
      potential: selectedMapSystem?.overall_development_potential,
      primary: archetypeLabel(selectedMapSystem?.primary_archetype),
      secondary: archetypeLabel(selectedMapSystem?.secondary_archetype),
      claim: selectedMapSystem?.claim_status,
    };
    const answer = starsNearChosen.data;
    if (!answer)
      return {
        chosen,
        facts,
        stars: [],
        starsState: starsNearChosen.isError ? 'failed' : 'loading',
        starsCutShort: false,
      };
    return {
      chosen: {
        ...chosen,
        star: starClassInBox(answer.systems, chosen.id64) ?? chosen.star,
      },
      facts,
      stars: starsInRange(answer.systems, chosen),
      starsState: 'ready',
      starsCutShort: answer.truncated,
    };
  });
  $effect(() => {
    if (!FINDER_F4_ENABLED && selectedMapSystem?.coords)
      viewState.retainedSelectedSystem = selectedMapSystem;
  });
  $effect(() => {
    if (!FINDER_F4_ENABLED) return;
    // Retain a selected result or exact-system resolution across page changes.
    // Resolution is keyed by id, so late responses cannot move the selection.
    const selected = selectedOnPage ?? reconstructedSelection;
    if (
      selected &&
      (chosenFromFind(selected) ||
        viewState.retainedSelectedSystem?.id64 !== selected.id64)
    )
      viewState.retainedSelectedSystem = selected;
    else if (viewState.retainedSelectedSystem?.id64 !== viewState.urlSelection)
      viewState.retainedSelectedSystem = null;
  });
  const selectedStarPresentation = $derived(
    stellarPresentation({
      type: selectedMapSystem?.main_star_type,
      subtype: selectedMapSystem?.main_star_subtype,
    }),
  );

  $effect(() => {
    if (!anchor || !results.data || results.isFetching) return;
    if (
      viewState.rankingState.archetype !== 'any' ||
      (FINDER_F4_ENABLED && !currentResponse) ||
      viewState.anchorSelectionRevision === 0 ||
      viewState.anchorSelectionRevision === viewState.focusedAnchorRevision
    )
      return;
    viewState.focusedAnchorRevision = viewState.anchorSelectionRevision;
    void tick().then(() =>
      resultList
        ?.querySelector<HTMLButtonElement>('[data-result-select]')
        ?.focus({ preventScroll: true }),
    );
  });

  function chooseSuggestion(hit: AutocompleteSystem): void {
    viewState.query = hit.name;
    autocompleteOpen = false;
    viewState.rankingNotice = '';
    const committedAnchor = readExploreAnchor({ anchor: hit });
    const params = serializeExploreSearchParams(
      {
        near: committedAnchor?.id64 ?? null,
        bodyFilters: viewState.bodyFilters,
        radius: viewState.radius,
        reach: viewState.reach,
      },
      page.url.searchParams,
    );
    // The chosen star is selected, so the map flies to it.
    if (FINDER_F4_ENABLED) params.set('selected', hit.id64);
    if (committedAnchor && viewState.rankingState.archetype === 'any')
      viewState.anchorSelectionRevision += 1;
    void navigateWithAnchor(
      exploreDestination(
        serializeRankingSearchParams(
          { ...viewState.rankingState, offset: 0 },
          params,
        ),
      ),
      { state: { anchor: committedAnchor } },
    );
    if (committedAnchor && !FINDER_F4_ENABLED) selectedSystem.set(hit.id64);
  }

  function handleQueryInput(event: Event): void {
    if (!(event.currentTarget as HTMLInputElement).value.trim() && anchor)
      void clearAnchor();
    autocompleteOpen =
      (event.currentTarget as HTMLInputElement).value.trim().length >= 2;
    activeSuggestion = -1;
  }

  function handleQueryKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape' && autocompleteOpen) {
      autocompleteOpen = false;
      event.preventDefault();
      return;
    }
    if (!autocompleteOpen || !suggestionRows.length) {
      if (event.key === 'ArrowDown' && suggestionRows.length) {
        autocompleteOpen = true;
        event.preventDefault();
      }
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      activeSuggestion = (activeSuggestion + 1) % suggestionRows.length;
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      activeSuggestion =
        activeSuggestion <= 0
          ? suggestionRows.length - 1
          : activeSuggestion - 1;
    } else if (event.key === 'Home') {
      event.preventDefault();
      activeSuggestion = 0;
    } else if (event.key === 'End') {
      event.preventDefault();
      activeSuggestion = suggestionRows.length - 1;
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const hit = suggestionRows[Math.max(0, activeSuggestion)];
      if (hit) chooseSuggestion(hit);
    }
  }

  function selectResult(system: ExploreSystem): void {
    if (FINDER_F4_ENABLED) viewState.retainedSelectedSystem = system;
    // The map shows the selected system, so selecting one flies it there.
    commitSelection(system.id64);
  }

  function showMap(
    event: MouseEvent,
    system: ExploreSystem | null = selectedMapSystem,
  ): void {
    // Presentation only: this never submits or changes a search.
    const alreadySelected = !system || system.id64 === selectedId;
    if (system && !alreadySelected) selectResult(system);
    // On a wide screen the map is already beside the results, so this only
    // centres it. At phone width the Finder covers the map: hide the panel,
    // and centre the map once it can be seen.
    if (window.matchMedia(PHONE_STAGE_QUERY).matches) {
      hideFinderPanel(event);
      if (alreadySelected) void tick().then(() => galaxyMap?.centre());
    } else if (alreadySelected) galaxyMap?.centre();
  }

  function hideFinderPanel(event: MouseEvent): void {
    panelToggleOrigin = event.currentTarget as HTMLElement;
    if (resultList) viewState.captureResultScroll(resultList.scrollTop);
    viewState.finderPanelOpen = false;
    void tick().then(() => {
      // At phone width the map fills the screen below the scrolled-away
      // header; on a wide screen it is already in view.
      if (window.matchMedia(PHONE_STAGE_QUERY).matches)
        mapSection?.scrollIntoView?.({ behavior: 'instant', block: 'start' });
      mapSection
        ?.querySelector<HTMLElement>('[data-finder-panel-show]')
        ?.focus({ preventScroll: true });
    });
  }

  function showFinderPanel(): void {
    viewState.finderPanelOpen = true;
    void tick().then(() => {
      // The results keep their scroll position, and focus returns to the
      // control that hid the panel (or the search box).
      if (resultList) viewState.restoreResultScroll(resultList);
      const target = panelToggleOrigin?.isConnected
        ? panelToggleOrigin
        : searchInput;
      target?.focus({
        preventScroll: !window.matchMedia(PHONE_STAGE_QUERY).matches,
      });
    });
  }

  function captureResultScroll(event: Event): void {
    if (!viewState.finderPanelOpen) return;
    viewState.captureResultScroll(
      (event.currentTarget as HTMLUListElement).scrollTop,
    );
  }

  /** A star or visited system on the map was asked to be shown in its own right. */
  function showFromMap(system: MapSystem): void {
    try {
      selectResult({
        id64: parseId64(system.id64),
        name: system.name,
        coords: { x: system.x, y: system.y, z: system.z },
        main_star_type: system.star,
      });
    } catch {
      // An id the site cannot hold exactly is not selected.
    }
  }

  function inspectFromMap(id64: string): void {
    try {
      const parsed = parseId64(id64);
      void navigateWithAnchor(
        FINDER_F4_ENABLED
          ? inspectHref(parsed, page.url)
          : resolve(`/inspect?system=${parsed}`),
      );
    } catch {
      // As above.
    }
  }

  function handleResultKeydown(
    event: KeyboardEvent,
    system: ExploreSystem,
  ): void {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    selectResult(system);
  }

  function toggleTravelHeatmap(): void {
    layerPreferences.set({
      ...$layerPreferences.value,
      journeys: !showTravelHeatmap,
    });
  }

  const population = (value: number | null | undefined) =>
    typeof value === 'number' && value > 0
      ? new Intl.NumberFormat(undefined, { notation: 'compact' }).format(value)
      : null;
</script>

<svelte:head>
  <title>Explore — ED-Finder V3</title>
  <meta
    name="description"
    content="Discover and inspect Elite Dangerous systems"
  />
</svelte:head>

<WorkspaceHeader />
<main class="explore-page product-page">
  <div
    class="explore-stage"
    class:is-finder-hidden={!viewState.finderPanelOpen}
    bind:this={stage}
    style:--explore-stage-top={`${stageTop}px`}
  >
    <section
      id="finder-panel"
      class="finder-panel"
      aria-labelledby="explore-title"
      hidden={!viewState.finderPanelOpen}
    >
      <header class="product-intro">
        <div>
          <p class="eyebrow">Explore · Finder</p>
          <h1 id="explore-title">Chart a promising system.</h1>
        </div>
        <button
          type="button"
          class="quiet-button finder-panel-toggle"
          data-finder-panel-hide
          aria-controls="finder-panel"
          aria-expanded="true"
          onclick={hideFinderPanel}>Hide Finder</button
        >
      </header>

      <nav class="shortlist-nav" aria-label="Your saved systems">
        <a href="#saved-systems"
          >Pinned and watched systems ({$pins.value.length} pinned)</a
        >
        <a href="#compare-systems">Compare ({$compare.value.length})</a>
      </nav>

      <section class="finder-bar" aria-labelledby="finder-title">
        <div>
          <p class="eyebrow">Discovery anchor</p>
          <h2 id="finder-title">Find systems near a known star</h2>
        </div>
        <div class="combobox-wrap">
          <label for="system-search">System name</label>
          <div class="search-control">
            <Search aria-hidden="true" size={19} />
            <input
              id="system-search"
              type="search"
              placeholder="Try Sol, Achenar, Lave…"
              autocomplete="off"
              role="combobox"
              aria-autocomplete="list"
              aria-expanded={autocompleteOpen && normalizedQuery.length >= 2}
              aria-controls="system-suggestions"
              aria-activedescendant={autocompleteOpen &&
              suggestionRows[activeSuggestion]
                ? `suggestion-${activeSuggestion}`
                : undefined}
              bind:this={searchInput}
              bind:value={viewState.query}
              oninput={handleQueryInput}
              onfocus={() => (autocompleteOpen = normalizedQuery.length >= 2)}
              onkeydown={handleQueryKeydown}
            />
          </div>
          {#if autocompleteOpen && normalizedQuery.length >= 2}
            <div
              id="system-suggestions"
              class="suggestions"
              role="listbox"
              aria-label="System suggestions"
            >
              {#if suggestions.isPending}
                <p role="status">Searching catalogue…</p>
              {:else if suggestions.isError}
                <div role="alert">
                  <p>Typeahead is unavailable.</p>
                  <button type="button" onclick={() => suggestions.refetch()}
                    >Retry</button
                  >
                </div>
              {:else if suggestionRows.length === 0}
                <p>No matching systems.</p>
              {:else}
                {#each suggestionRows as hit, index (hit.id64)}
                  <button
                    id={`suggestion-${index}`}
                    type="button"
                    role="option"
                    aria-selected={index === activeSuggestion}
                    class:active={index === activeSuggestion}
                    onmousedown={(event) => event.preventDefault()}
                    onclick={() => chooseSuggestion(hit)}
                  >
                    <span>{hit.name}</span><code>{hit.id64}</code>
                  </button>
                {/each}
              {/if}
            </div>
          {/if}
        </div>
      </section>

      {#snippet bodyFilterTools()}
        <BodyFilters
          value={viewState.bodyFilters}
          onapply={(value) => void commitBodyFilters(value)}
          onremove={removeBodyFilter}
          onclear={() => void commitBodyFilters({})}
        />
      {/snippet}

      {#if anchor}
        <p class="committed-anchor">
          Search anchor: {anchor.name}
          <button
            type="button"
            class="quiet-button"
            aria-label={`Clear anchor ${anchor.name}`}
            onclick={clearAnchor}>Clear</button
          >
        </p>
      {/if}
      {#snippet finderTools()}
        <div class="finder-tools">
          {@render bodyFilterTools()}
          <label class="page-size"
            >Results per page
            <input
              type="number"
              min="1"
              max="50"
              step="1"
              value={viewState.rankingState.pageSize}
              onchange={(event) =>
                commitRanking({ pageSize: event.currentTarget.valueAsNumber })}
            />
          </label>
        </div>
      {/snippet}
      {#if FINDER_F4_ENABLED}
        <ArchetypePicker
          compact
          archetype={viewState.rankingState.archetype}
          minimumTier={viewState.rankingState.minimumTier}
          onArchetypeChange={(archetype) =>
            commitRanking({ archetype, minimumTier: null })}
          onMinimumTierChange={(minimumTier) => commitRanking({ minimumTier })}
          rankingTools={finderTools}
        />
      {:else}
        {@render bodyFilterTools()}
      {/if}

      {#if viewState.rankingNotice}
        <p role="status" class="ranking-status">{viewState.rankingNotice}</p>
      {/if}

      <section
        class="results-panel"
        aria-labelledby="results-title"
        aria-busy={viewState.hasResultsIntent &&
          (FINDER_F4_ENABLED ? updatingResults : results.isFetching)}
      >
        <div class="panel-heading">
          <div>
            <p class="eyebrow">Catalogue results</p>
            <h2 id="results-title">
              {typeNearAnchor && anchor
                ? `${activeArchetypeLabel} near ${anchor.name}`
                : FINDER_F4_ENABLED &&
                    viewState.rankingState.archetype !== 'any'
                  ? `Galaxy-wide ${activeArchetypeLabel} ranking`
                  : anchor
                    ? `Near ${anchor.name}`
                    : viewState.hasResultsIntent
                      ? 'Galaxy-wide opportunities'
                      : 'Systems'}
            </h2>
          </div>
          {#if FINDER_F4_ENABLED && displayedResults}
            <FinderResultRanking
              variant="summary"
              page={displayedResults.page}
              archetype={displayedResults.finderState!.archetype}
            />
          {:else}<span>{systems.length} shown</span>{/if}
        </div>

        <div class="search-scope">
          <p id="results-scope">{resultsScope}</p>
          <button
            type="button"
            class="secondary-button pill-action"
            data-explore-map-open
            aria-controls="explore-map-window"
            disabled={selectedMapSystem !== null && !chosenSystem}
            onclick={(event) => showMap(event)}>Show on map</button
          >
          {#if selectedMapSystem && !chosenSystem}
            <span>Map coordinates unavailable for the selected system.</span>
          {/if}
          <label class="search-radius" for="search-radius">
            Within
            <select
              id="search-radius"
              value={effectiveRadius}
              disabled={radiusDisabled}
              aria-describedby={radiusDisabled
                ? 'search-radius-disabled'
                : radiusCapped
                  ? 'search-radius-capped'
                  : undefined}
              onchange={(event) =>
                commitRadius(Number(event.currentTarget.value))}
            >
              {#each SEARCH_RADIUS_OPTIONS as option (option)}
                <option
                  value={option}
                  disabled={!radiusDisabled &&
                    radiusCapped &&
                    option > FILTERED_SEARCH_RADIUS_CAP_LY}
                  >{option.toLocaleString('en-US')} LY</option
                >
              {/each}
            </select>
          </label>
          {#if radiusDisabled}
            <p id="search-radius-disabled" class="radius-hint">
              Search radius works when you search near a system.
            </p>
          {:else if radiusCapped}
            <p id="search-radius-capped" class="radius-hint">
              {Object.keys(viewState.bodyFilters).length > 0
                ? 'Body filters limit'
                : 'A colony type limits'} the search to {FILTERED_SEARCH_RADIUS_CAP_LY}
              LY{radiusCappedByFilters
                ? `; your ${viewState.radius.toLocaleString('en-US')} LY radius resumes when ${Object.keys(viewState.bodyFilters).length > 0 ? 'the filters are cleared' : 'Any is chosen again'}`
                : ''}.
            </p>
          {/if}
          <label class="search-radius" for="search-reach">
            Up to
            <select
              id="search-reach"
              data-search-reach
              aria-label="Distance from inhabited space"
              value={viewState.reach}
              aria-describedby="search-reach-hint"
              onchange={(event) =>
                commitReach(Number(event.currentTarget.value))}
            >
              {#each REACH_OPTIONS as option (option)}
                <option value={option}
                  >{option.toLocaleString('en-US')} LY</option
                >
              {/each}
            </select>
            from inhabited space
          </label>
          <p id="search-reach-hint" class="radius-hint">
            Every search keeps to systems this close to an inhabited system.
          </p>
        </div>

        {#if FINDER_F4_ENABLED && viewState.hasResultsIntent}
          <div role="status" aria-live="polite" class="ranking-status">
            {#if updatingResults}
              <p>
                {displayedResults ? 'Updating… ' : ''}{viewState.rankingState
                  .archetype === 'any'
                  ? 'Searching systems…'
                  : `Ranking systems for ${activeArchetypeLabel}…`}
              </p>
            {:else if currentResponse && displayedResults && !results.isError}
              <span
                >{displayedResults.page.count}
                {displayedResults.page.count === 1 ? 'result' : 'results'} on this
                page.</span
              >
            {/if}
          </div>
          {#if results.isError}
            <div class="state-card error" role="alert">
              <h3>
                {rankingDataNotReady
                  ? 'Ranking data not ready'
                  : 'Ranking temporarily unavailable'}
              </h3>
              <p>
                {rankingDataNotReady
                  ? 'Archetype rankings are unavailable until Finder ranking data is published.'
                  : viewState.rankingState.archetype !== 'any'
                    ? 'Archetype rankings are temporarily unavailable. Try again shortly.'
                    : 'Discovery results could not be loaded. Try again shortly.'}
              </p>
              <button type="button" onclick={() => results.refetch()}
                >Retry</button
              >
              {#if viewState.rankingState.archetype !== 'any'}
                <button
                  type="button"
                  onclick={() => commitRanking({ archetype: 'any' })}
                  >Choose Any</button
                >
              {/if}
            </div>
          {:else if currentResponse && !results.isFetching && results.data?.page.total === 0}
            <div class="state-card">
              <p>
                {viewState.rankingState.archetype === 'any'
                  ? 'No systems match this discovery area. Choose another anchor.'
                  : `No systems meet minimum tier ${viewState.rankingState.minimumTier} for ${activeArchetypeLabel}.`}
              </p>
              {#if viewState.rankingState.archetype !== 'any'}
                {#if viewState.rankingState.minimumTier !== 'B'}
                  <button
                    type="button"
                    onclick={() => commitRanking({ minimumTier: 'B' })}
                    >Reset to tier B</button
                  >
                {/if}
                <button
                  type="button"
                  onclick={() => commitRanking({ archetype: 'any' })}
                  >Choose Any</button
                >
              {/if}
            </div>
          {/if}
        {/if}

        {#if !viewState.hasResultsIntent}
          <div class="state-card">
            <p>Search for a system above to see what is near it.</p>
          </div>
        {:else if !FINDER_F4_ENABLED && results.isPending}
          <div class="state-card" role="status">
            <Sparkles aria-hidden="true" />
            <p>Building a real-system shortlist…</p>
          </div>
        {:else if !FINDER_F4_ENABLED && results.isError && (reviewLabRun || systems.length === 0)}
          <div class="state-card error" role="alert">
            <p>Discovery results could not be loaded.</p>
            <button
              class="secondary-button"
              type="button"
              onclick={() => results.refetch()}>Retry search</button
            >
          </div>
        {:else if !FINDER_F4_ENABLED && reviewLabRun && results.data?.results.length === 0}
          <div class="state-card" role="status">
            <p>No systems match this discovery area.</p>
          </div>
        {:else if !FINDER_F4_ENABLED && systems.length === 0}
          <div class="state-card">
            <p>No systems match this discovery area. Choose another anchor.</p>
          </div>
        {:else}
          <ul
            class="result-list"
            bind:this={resultList}
            onscroll={captureResultScroll}
            aria-label="System results"
            aria-describedby="results-scope"
          >
            {#each systems as system (system.id64)}
              <li
                class:selected={$selectedSystem.value === system.id64}
                data-system-result={system.id64}
              >
                <button
                  class="result-select"
                  type="button"
                  data-result-select
                  aria-pressed={$selectedSystem.value === system.id64}
                  onclick={() => selectResult(system)}
                  onkeydown={(event) => handleResultKeydown(event, system)}
                >
                  <span class="result-title"
                    ><strong
                      id={FINDER_F4_ENABLED
                        ? `finder-result-heading-${system.id64}`
                        : undefined}
                      >{system.name ?? `System ${system.id64}`}</strong
                    ><code>{system.id64}</code></span
                  >
                  {#if FINDER_F4_ENABLED && displayedResults && !(viewState.rankingState.archetype === 'any' && displayedResults.finderState!.archetype !== 'any')}
                    <FinderResultRanking
                      variant="row"
                      {system}
                      archetype={displayedResults.finderState!.archetype}
                    />
                  {/if}
                  <span class="result-facts">
                    {#if system.distance != null}<span
                        >{FINDER_F4_ENABLED
                          ? `${system.distance.toFixed(2)} LY`
                          : `${system.distance.toFixed(1)} ly`}{displayedSearchRadius !=
                        null
                          ? ` · within ${displayedSearchRadius.toLocaleString('en-US')} LY`
                          : ''}</span
                      >{/if}
                    {#if system.primaryEconomy}<span
                        >{system.primaryEconomy}</span
                      >{/if}
                    {#if population(system.population)}<span
                        >{population(system.population)} population</span
                      >{/if}
                    {#if system.main_star_type}<span
                        >{starClassLabel(system.main_star_type)}</span
                      >{/if}
                  </span>
                </button>
                {#if selectedId === system.id64}
                  <button
                    type="button"
                    class="result-map-action secondary-button pill-action"
                    data-result-map-open
                    aria-controls="explore-map-window"
                    disabled={!chosenSystem}
                    onclick={(event) => showMap(event, system)}
                    >Show on map</button
                  >
                {/if}
                <!-- eslint-disable svelte/no-navigation-without-resolve -- inspectHref resolves the Inspect route -->
                <a
                  class="inspect-link secondary-button"
                  href={FINDER_F4_ENABLED
                    ? inspectHref(system.id64, page.url)
                    : resolve(`/inspect?system=${system.id64}`)}
                  onclick={FINDER_F4_ENABLED
                    ? (event) => followInspect(event, system.id64)
                    : undefined}
                  aria-label={`Inspect ${system.name ?? system.id64}`}
                >
                  Inspect <ArrowRight aria-hidden="true" size={16} />
                </a>
                <!-- eslint-enable svelte/no-navigation-without-resolve -->
                {#if FINDER_F4_ENABLED && displayedResults && !(viewState.rankingState.archetype === 'any' && displayedResults.finderState!.archetype !== 'any')}
                  <ArchetypeExplanation
                    {system}
                    expected={system.ranking_provenance ?? null}
                    archetype={displayedResults.finderState!.archetype}
                    headingId={`finder-result-heading-${system.id64}`}
                  />
                {/if}
                <div class="finder-actions"><SystemActions {system} /></div>
              </li>
            {/each}
          </ul>
        {/if}
        {#if FINDER_F4_ENABLED && displayedResults}
          <nav class="result-paging" aria-label="Result pages">
            <button
              type="button"
              class="quiet-button"
              disabled={!currentResponse ||
                updatingResults ||
                displayedResults.page.offset === 0}
              onclick={() =>
                commitRanking(
                  {
                    offset: Math.max(
                      0,
                      displayedResults!.page.offset -
                        displayedResults!.page.page_size,
                    ),
                  },
                  false,
                )}>Previous</button
            >
            <button
              type="button"
              class="quiet-button"
              disabled={!currentResponse ||
                updatingResults ||
                displayedResults.page.offset +
                  displayedResults.page.page_size >=
                  displayedResults.page.navigable_total}
              onclick={() =>
                commitRanking(
                  {
                    offset:
                      displayedResults!.page.offset +
                      displayedResults!.page.page_size,
                  },
                  false,
                )}>Next</button
            >
          </nav>
        {/if}
      </section>
    </section>

    <ExploreMapWindow
      bind:element={mapSection}
      selectedSystemName={selectedMapName}
      finderPanelOpen={viewState.finderPanelOpen}
      onShowFinder={showFinderPanel}
    >
      {#snippet beforeCanvas()}
        <div class="map-layer-toolbar">
          <div class="map-camera-presets" role="group" aria-label="Map camera">
            <button
              type="button"
              class="centre-system"
              data-map-camera="chosen"
              disabled={!showing || mapPhase !== 'ready'}
              onclick={() => galaxyMap?.centre()}
              >{showing
                ? `Centre on ${showing.chosen.name}`
                : 'Centre on the selected system'}</button
            >
            <button
              type="button"
              data-map-camera="galaxy"
              disabled={mapPhase !== 'ready'}
              onclick={() => galaxyMap?.wholeGalaxy()}>Whole galaxy</button
            >
          </div>
          <button
            type="button"
            class="journeys-switch"
            role="switch"
            aria-checked={showTravelHeatmap}
            aria-describedby="journeys-switch-about"
            data-layer-switch="journeys"
            onclick={toggleTravelHeatmap}>My journeys</button
          >
          <span id="journeys-switch-about" class="sr-only"
            >Shows the systems you have visited, from your imported journals, on
            the map. Only you see them.</span
          >
          <div class="map-window-status">
            <span
              role="status"
              aria-label="My journeys"
              data-commander-history-count={travelHeatmapState === 'ready'
                ? (commanderVisits.data?.count ?? 0)
                : 0}
              data-commander-history-state={travelHeatmapState}
            >
              {#if travelHeatmapState === 'checking'}
                Checking your sign-in…
              {:else if travelHeatmapState === 'auth-error'}
                Account check unavailable · <button
                  type="button"
                  onclick={() => void auth.bootstrap()}>Retry</button
                >
              {:else if travelHeatmapState === 'signed-out'}
                <a href={resolve('/account')}
                  >Sign in and import your journals</a
                >
              {:else if travelHeatmapState === 'summary-error'}
                Journal import status unavailable · <button
                  type="button"
                  onclick={() => journalSummary.refetch()}>Retry</button
                >
              {:else if travelHeatmapState === 'off'}
                My journeys hidden
              {:else if travelHeatmapState === 'no-journals'}
                No imported journals yet
              {:else if travelHeatmapState === 'loading'}
                Reading imported journal visits…
              {:else if travelHeatmapState === 'error'}
                My journeys unavailable · <button
                  type="button"
                  onclick={() => commanderVisits.refetch()}>Retry</button
                >
              {:else if travelHeatmapState === 'empty'}
                No travel in your imported journals yet
              {:else}
                {(commanderVisits.data?.count ?? 0).toLocaleString()} visited systems{commanderVisits
                  .data?.truncated
                  ? ' · your most recent'
                  : ''}
              {/if}
            </span>
          </div>
          <p
            class="selection-status"
            role="status"
            data-map-showing={showing?.chosen.id64 ?? ''}
            data-map-stars={showing?.starsState ?? ''}
          >
            {#if mapPhase === 'failed'}
              The galaxy map could not start on this device.
            {:else if selectedMapSystem && !chosenSystem}
              Map coordinates unavailable for the selected system.
            {:else}
              {showingInWords(showing)}
            {/if}
            {#if mapPhase !== 'failed' && showing?.starsState === 'failed'}
              · <button type="button" onclick={() => starsNearChosen.refetch()}
                >Retry</button
              >
            {/if}
          </p>
        </div>
      {/snippet}
      <GalaxyMap
        bind:this={galaxyMap}
        bind:phase={mapPhase}
        {showing}
        {journeys}
        resume={untrack(() => viewState.mapResume)}
        onScreen={mapOnScreen}
        onLeave={(resume) => (viewState.mapResume = resume)}
        onShowOnMap={showFromMap}
        onInspect={inspectFromMap}
      />
      {#snippet afterCanvas()}
        {#if selectedMapSystem}
          <article
            class="map-system-card"
            aria-labelledby="map-system-card-title"
            data-selected-system-card={selectedMapSystem.id64}
          >
            <div class="map-system-card__identity">
              <span
                class="stellar-glyph"
                data-stellar-glyph={selectedStarPresentation.glyph}
                style:--stellar-core={`rgb(${selectedStarPresentation.coreRgb.map((channel) => Math.round(channel * 255)).join(', ')})`}
                style:--stellar-halo={`rgb(${selectedStarPresentation.haloRgb.map((channel) => Math.round(channel * 255)).join(', ')})`}
                aria-hidden="true"
              ></span>
              <div>
                <h3 id="map-system-card-title">
                  {selectedMapSystem.name ?? `System ${selectedMapSystem.id64}`}
                </h3>
              </div>
            </div>
            <MapToolPopout
              kind="selection"
              label="Details"
              title="Selected system details"
            >
              <p>System ID <code>{selectedMapSystem.id64}</code></p>
              <dl class="map-system-card__facts">
                <div>
                  <dt>Primary star</dt>
                  <dd>
                    {selectedMapSystem.main_star_type ??
                      'Unknown'}{selectedMapSystem.main_star_subtype
                      ? ` · ${selectedMapSystem.main_star_subtype}`
                      : ''}
                  </dd>
                </div>
                {#if selectedMapSystem.distance != null}<div>
                    <dt>Distance</dt>
                    <dd>{selectedMapSystem.distance.toFixed(1)} ly</dd>
                  </div>{/if}
                {#if selectedMapSystem.population != null}<div>
                    <dt>Population</dt>
                    <dd>{population(selectedMapSystem.population) ?? '0'}</dd>
                  </div>{/if}
                {#if selectedMapSystem.primaryEconomy}<div>
                    <dt>Economy</dt>
                    <dd>{selectedMapSystem.primaryEconomy}</dd>
                  </div>{/if}
                {#if selectedMapSystem.allegiance}<div>
                    <dt>Allegiance</dt>
                    <dd>{selectedMapSystem.allegiance}</dd>
                  </div>{/if}
                {#if selectedMapSystem.security}<div>
                    <dt>Security</dt>
                    <dd>{selectedMapSystem.security}</dd>
                  </div>{/if}
              </dl>
            </MapToolPopout>
          </article>
          <nav
            class="map-system-card__actions"
            aria-label="Selected system actions"
          >
            <!-- eslint-disable svelte/no-navigation-without-resolve -- inspectHref resolves the Inspect route -->
            <a
              class="inspect-map-action"
              href={FINDER_F4_ENABLED
                ? inspectHref(selectedMapSystem.id64, page.url)
                : resolve(`/inspect?system=${selectedMapSystem.id64}`)}
              onclick={FINDER_F4_ENABLED
                ? (event) => followInspect(event, selectedMapSystem!.id64)
                : undefined}><span>Inspect {selectedMapName}</span></a
            >
            <!-- eslint-enable svelte/no-navigation-without-resolve -->
            <a href={resolve(`/plan?system=${selectedMapSystem.id64}`)}
              >Plan here <ArrowRight aria-hidden="true" size={15} /></a
            >
          </nav>
        {/if}
      {/snippet}
    </ExploreMapWindow>
  </div>
  <div id="saved-systems"><WatchlistPanel /></div>
  <div id="compare-systems"><ComparePanel /></div>
</main>

<style>
  .map-layer-toolbar {
    flex-shrink: 0;
    gap: 0.4rem;
    padding: 0 0 0.4rem;
    background: transparent;
  }
  .map-camera-presets {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 0.35rem;
    min-width: 0;
  }
  .map-camera-presets button,
  .journeys-switch {
    min-height: 2rem;
    padding: 0.3rem 0.6rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-pill);
    background: var(--panel);
    background-image: var(--panel-gloss);
    color: var(--text);
    font-size: 0.75rem;
    cursor: pointer;
  }
  .centre-system {
    max-width: 16rem;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .journeys-switch[aria-checked='true'] {
    border-color: var(--accent);
    background-color: var(--accent);
    color: var(--accent-text);
  }
  .map-camera-presets button:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
  .map-camera-presets button:focus-visible,
  .journeys-switch:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 2px;
  }
  .map-system-card {
    display: flex;
    gap: 0.35rem;
    min-width: 0;
    max-width: 100%;
    padding: 0;
    border: 0;
    background: transparent;
  }
  .map-system-card__identity {
    gap: 0.3rem;
    min-width: 0;
  }
  .map-system-card__identity > div {
    min-width: 0;
  }
  .map-system-card__identity h3 {
    margin: 0;
    max-width: 12rem;
    font-size: 0.8rem;
  }
  .map-system-card .stellar-glyph {
    width: 1.25rem;
    height: 1.25rem;
    flex-basis: 1.25rem;
  }
  .map-system-card__facts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .map-system-card__actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.4rem;
    margin-left: auto;
    min-width: 0;
    max-width: 100%;
  }
  .map-system-card__actions a {
    min-width: 0;
    max-width: 100%;
    overflow-wrap: anywhere;
    border-radius: var(--radius-pill);
  }
  .map-system-card__actions .inspect-map-action {
    max-width: min(16rem, 100%);
    border-color: var(--accent);
    background: var(--accent);
    background-image: var(--panel-gloss);
    color: var(--accent-text);
    font-weight: 700;
  }
  .inspect-map-action span {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* Sits at the right of the map heading, so nothing is stacked under the
     map: the canvas keeps the whole window height. */
  .map-window-status {
    display: flex;
    flex: 1 1 16rem;
    justify-content: flex-end;
    gap: 0.5rem;
    min-width: 0;
    color: var(--text-muted);
    font-size: 0.65rem;
  }
  .map-window-status > * {
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .selection-status {
    margin: 0;
    min-height: 0;
    padding: 0;
    border: 0;
    font: inherit;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  /* Game-style workspace: the map fills the window below the site header,
     with the Finder as a panel on the left that can be hidden. */
  .explore-page {
    width: calc(100% - 1rem);
    padding: 0.5rem 0 3rem;
  }
  .explore-stage {
    --finder-panel-width: clamp(21rem, 26vw, 27rem);
    position: relative;
    display: grid;
    grid-template-columns: var(--finder-panel-width) minmax(0, 1fr);
    gap: 0.5rem;
    height: calc(100dvh - var(--explore-stage-top, 7rem) - 0.5rem);
    min-height: 30rem;
  }
  .explore-stage.is-finder-hidden {
    grid-template-columns: minmax(0, 1fr);
  }
  .finder-panel {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    min-width: 0;
    min-height: 0;
    padding: 0.6rem;
    overflow-y: auto;
    overscroll-behavior: contain;
    border: 1px solid var(--line);
    border-radius: var(--radius-lg);
    background: var(--panel-gloss), var(--bg-deep);
  }
  .finder-panel[hidden] {
    display: none;
  }
  .finder-panel > :global(*) {
    flex-shrink: 0;
  }
  .finder-panel .product-intro {
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: start;
    gap: 0.5rem;
    margin: 0;
  }
  .finder-panel .product-intro h1 {
    margin-top: 0.2rem;
    font-size: clamp(1rem, 1.35vw, 1.35rem);
    line-height: 1.1;
  }
  .finder-panel-toggle {
    padding: 0.35rem 0.75rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-pill);
    background: var(--panel-raised);
    color: var(--text);
    font: inherit;
    font-size: 0.8rem;
    white-space: nowrap;
    cursor: pointer;
  }
  .finder-panel-toggle:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 2px;
  }
  .finder-panel .shortlist-nav {
    gap: 0.25rem 1rem;
    margin: 0;
    font-size: 0.78rem;
  }
  .finder-panel .finder-bar {
    grid-template-columns: minmax(0, 1fr);
    gap: 0.35rem;
    margin: 0;
    padding: 0.5rem 0.65rem 0.6rem;
  }
  .finder-panel .finder-bar .eyebrow {
    display: none;
  }
  .finder-panel .finder-bar :global(h2) {
    margin: 0;
    font-size: 0.85rem;
  }
  .finder-panel .finder-bar :global(.combobox-wrap > label) {
    margin-bottom: 0.25rem;
    font-size: 0.78rem;
  }
  .finder-tools {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.4rem 0.75rem;
  }
  .finder-panel .page-size {
    margin: 0;
    font-size: 0.85rem;
  }
  .finder-panel .page-size input {
    width: 4rem;
    padding: 0.25rem 0.5rem;
  }
  .finder-panel :global(.panel-heading) {
    min-height: 0;
    padding: 0.6rem 0.75rem;
  }
  .finder-panel .search-scope {
    padding: 0.5rem 0.75rem;
    font-size: 0.8rem;
  }
  /* The results take the rest of the panel and scroll on their own, so the
     search and archetype controls above them stay in view. */
  .finder-panel .results-panel {
    display: flex;
    flex-direction: column;
    flex: 1 0 auto;
    min-height: min(26rem, 70%);
  }
  .finder-panel .result-list {
    flex: 1 1 0;
    min-height: 12rem;
    max-height: none;
  }
  .explore-stage > :global(.explore-map-window) {
    min-height: 0;
  }
  .result-map-action {
    justify-self: start;
    margin: 0.25rem 1.15rem;
  }
  @media (max-width: 700px) {
    .map-system-card__actions .inspect-map-action {
      max-width: min(12rem, 100%);
    }
    .map-system-card__identity h3 {
      max-width: 7rem;
    }
  }
  @media (max-width: 900px) {
    .explore-page {
      width: calc(100% - 0.5rem);
      padding-top: 0.25rem;
    }
    /* Phone: the Finder and the map take turns. The page scrolls (no nested
       panel scroll); the map fills the screen once the header scrolls away. */
    .explore-stage {
      display: block;
      height: auto;
      min-height: 0;
    }
    .finder-panel {
      overflow: visible;
    }
    .finder-panel .results-panel {
      min-height: 0;
    }
    .finder-panel .result-list {
      flex: none;
      max-height: 70dvh;
    }
    .explore-stage > :global(.explore-map-window) {
      height: calc(100dvh - 0.5rem);
      min-height: 26rem;
      scroll-margin-top: 0.25rem;
    }
    /* While the Finder is shown, keep the map laid out at the same size off
       screen, so its canvas never resizes and refetches when it returns. */
    .explore-stage:not(.is-finder-hidden) > :global(.explore-map-window) {
      position: absolute;
      top: 0;
      left: -300vw;
      width: 100%;
      visibility: hidden;
    }
  }
  .committed-anchor,
  .result-paging {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem;
  }
  .committed-anchor {
    color: var(--text-muted);
  }
  .committed-anchor button {
    padding: 0.35rem 0.75rem;
    font: inherit;
  }
  .page-size {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-block: 1rem;
  }
  .page-size input {
    width: 5rem;
    padding: 0.4rem 0.6rem;
  }
  .results-panel .panel-heading {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.65rem;
  }
  /* The results column is narrow even on desktop. Give the identity and
     facts the full row, with actions following in their existing focus order. */
  .result-list li {
    grid-template-columns: minmax(0, 1fr);
    gap: 0.25rem;
    padding-block: 0.25rem 0.75rem;
  }
  .result-select {
    width: 100%;
    padding: 0.9rem 1.15rem 0.65rem;
  }
  .result-title strong {
    overflow: visible;
    white-space: normal;
    overflow-wrap: anywhere;
    font-size: 1rem;
    line-height: 1.45;
  }
  .result-title code {
    overflow-wrap: anywhere;
    color: var(--text-muted);
  }
  .result-facts {
    display: grid;
    gap: 0.4rem;
    font-size: 0.85rem;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
  .result-list .inspect-link {
    justify-self: start;
    margin: 0.25rem 1.15rem 0.5rem;
  }
  .search-scope {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.6rem 1rem;
    padding: 0.75rem 1.15rem;
    background: var(--panel-sunken);
    color: var(--text-muted);
    font-size: 0.85rem;
    line-height: 1.5;
    border-bottom: 1px solid var(--line);
  }
  .search-scope p {
    margin: 0;
  }
  .search-radius {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
  }
  .search-radius select {
    padding: 0.35rem 0.5rem;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: var(--panel-raised);
    color: inherit;
    font: inherit;
  }
  .search-radius select:disabled {
    cursor: not-allowed;
    opacity: 0.65;
  }
  .search-radius select:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 2px;
  }
  .radius-hint {
    flex-basis: 100%;
  }
  .ranking-status,
  .result-paging {
    padding-inline: 1.15rem;
  }
  .ranking-status {
    color: var(--text-muted);
  }
  .result-paging {
    margin-top: 1rem;
    padding-bottom: 1rem;
  }
  .shortlist-nav {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem 1.5rem;
    margin-block: 1rem;
  }
  .finder-actions {
    grid-column: 1 / -1;
    padding: 0 1.15rem;
  }
  #saved-systems,
  #compare-systems {
    margin-top: 1.5rem;
    scroll-margin-top: 5rem;
  }
</style>
