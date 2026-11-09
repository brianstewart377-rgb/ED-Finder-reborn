<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { fromStore } from 'svelte/store';
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { onMount, tick, untrack, type Snippet } from 'svelte';
  import { ArrowRight, Search, Sparkles } from '@lucide/svelte';
  import {
    ApiError,
    autocompleteSystems,
    getCommanderTravelHeatmap,
    getCommanderJournalSummary,
    getMapHeatmap,
    getArchetypeRankings,
    getSystem,
    searchExploreSystems,
    type AutocompleteSystem,
    type CatalogueViewportResponse,
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
    normalizeSearchRadius,
    SEARCH_RADIUS_OPTIONS,
    serializeExploreSearchParams,
    type SearchRadius,
  } from './search-state';
  import type { ExploreResultCache } from './explore-result-cache';
  import type { ExploreViewState } from './explore-view-state.svelte.ts';
  import MapLayers from './MapLayers.svelte';
  import MapToolPopout from './MapToolPopout.svelte';
  import ExploreMapWindow from './ExploreMapWindow.svelte';
  import type {
    CameraState,
    GalaxySystemPoint,
    RuntimeEvent,
    SpatialContribution,
    SystemCameraState,
  } from '$lib/spatial/contracts';
  import {
    buildExploreGalaxyScene,
    createExploreFinderContribution,
    createExploreSelectionContribution,
    mapExploreSelectedSystemPoint,
  } from '$lib/spatial/explore-scene';
  import { collectGalaxySpatialContributions } from '$lib/spatial/galaxy-overlays';
  import {
    classifyDensityError,
    densityCoverageLabel,
    densityContributionFrom,
    heatmapViewportQueryKey,
    heatmapViewportRequest,
    mapHeatmapToDensityPayload,
  } from '$lib/spatial/galaxy-density-source';
  import {
    DENSITY_CROSSFADE_NEAR_LY,
    galaxyDetailMode,
  } from '$lib/spatial/galaxy-detail-policy';
  import {
    fitGalaxyPlaneCamera,
    focusGalaxyCamera,
    GALAXY_LOCAL_VIEW_DISTANCE_LY,
  } from '$lib/spatial/galaxy-camera';
  import {
    createGalaxyRegionsContribution,
    fetchAuthoritativeGalaxyRegions,
    type GalaxyRegionsPayload,
  } from '$lib/spatial/galaxy-regions';
  import {
    createGalaxyNebulaeContribution,
    fetchGalaxyNebulae,
    GALAXY_NEBULAE_LAYER_ID,
    type GalaxyNebula,
  } from '$lib/spatial/galaxy-nebulae';
  import { galaxyNebulaVisibility } from '$lib/spatial/galaxy-nebulae-policy';
  import { stellarPresentation } from '$lib/spatial/stellar-presentation';
  import WorkspaceHeader from '$lib/components/WorkspaceHeader.svelte';
  import SystemActions from './SystemActions.svelte';
  import WatchlistPanel from './WatchlistPanel.svelte';
  import ComparePanel from './ComparePanel.svelte';
  import { createWatchlistQuery } from './watchlist-query.svelte';
  import { reservedExploreGalaxyPoints } from './reserved-galaxy-points';

  import { createCommanderHistoryContribution } from '$lib/spatial/commander-history';
  import { commanderHistoryViewport } from '$lib/spatial/commander-history-viewport';
  import {
    createCatalogueStarsContribution,
    galaxyStarViewport,
    withinGalaxyDisk,
  } from '$lib/spatial/galaxy-star-stream';
  import { withinGalaxyStarFootprint } from '$lib/spatial/galaxy-star-footprint';
  import { createGalaxyStarQuery } from '$lib/spatial/galaxy-star-query.svelte';

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
  const watchedSystems = createWatchlistQuery();
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
  // Body filters cap the anchored search at the API's filtered radius; the
  // URL keeps the chosen radius so it resumes when the filters are cleared.
  const effectiveRadius = $derived(
    effectiveSearchRadius(viewState.radius, viewState.bodyFilters),
  );
  const mapAnchorPoint = $derived<GalaxySystemPoint | null>(
    anchor &&
      typeof anchor.x === 'number' &&
      typeof anchor.y === 'number' &&
      typeof anchor.z === 'number'
      ? {
          systemId64: anchor.id64,
          name: anchor.name,
          positionLy: { x: anchor.x, y: anchor.y, z: anchor.z },
        }
      : null,
  );
  let searchInput = $state<HTMLInputElement>();
  let spatialCameraTransitionActive = $state(false);
  let regionLoadAttempt = 0;
  let regionsLoading = false;
  let mapSection = $state<HTMLDivElement>();
  // The control that hid the Finder panel gets focus back when it reopens.
  let panelToggleOrigin: HTMLElement | null = null;
  let stage = $state<HTMLDivElement>();
  let stageTop = $state(0);
  // One string so markup reflowing can never split its phrases with newlines.
  const MAP_SOURCES_NOTE =
    'Stars use exact catalogue coordinates and class-specific presentation. ' +
    'Amber marks active selections. Region geometry is the pinned in-game ' +
    'source; no synthetic geography, stars, or density are substituted.';
  // Must match the phone breakpoint in this component's styles.
  const PHONE_STAGE_QUERY = '(max-width: 900px)';
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
  let nebulaLoadAttempt = 0;
  let nebulaController: AbortController | null = null;
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
  let viewportCameraTimeout: number | null = null;
  let pendingViewportCamera: CameraState | null = null;
  const showDensity = $derived($layerPreferences.value.density);
  const showTravelHeatmap = $derived($layerPreferences.value.journeys);
  const nebulaKinds = $derived({
    diffuse: viewState.nebulae?.nebulae.some(
      (nebula: GalaxyNebula) => nebula.kind === 'nebula',
    ),
    planetary: viewState.nebulae?.nebulae.some(
      (nebula: GalaxyNebula) => nebula.kind === 'planetary-nebula',
    ),
  });
  const nebulaDisplayStatus = $derived.by(() => {
    if (!viewState.showNebulae) return 'Nebula layer hidden';
    if (
      !viewState.nebulae ||
      !viewState.renderedNebulaReceipt ||
      viewState.renderedNebulaReceipt.contributionRevision !==
        viewState.nebulaResourceRevision ||
      viewState.nebulaCameraDistanceLy === null
    )
      return 'Preparing nebula landmarks…';
    const visibility = galaxyNebulaVisibility(viewState.nebulaCameraDistanceLy);
    const count = viewState.renderedNebulaReceipt.renderedTargetCount;
    if (count === 0) {
      if (nebulaKinds.planetary && visibility['planetary-nebula'] === 0)
        return 'No landmarks at this zoom · planetary nebulae appear at close range';
      return 'No landmarks at this zoom';
    }
    const kinds = [
      nebulaKinds.diffuse ? 'diffuse' : null,
      nebulaKinds.planetary && visibility['planetary-nebula'] > 0
        ? 'planetary'
        : null,
    ].filter(Boolean);
    return `${count.toLocaleString()} ${count === 1 ? 'landmark' : 'landmarks'} shown (${kinds.join(' and ')} nebulae)${nebulaKinds.diffuse ? ' · schematic extent (clouds fade on approach)' : ''}`;
  });

  const normalizedQuery = $derived(viewState.query.trim());
  const suggestions = createQuery(() => ({
    queryKey: queryKeys.autocomplete(normalizedQuery.toLocaleLowerCase()),
    queryFn: ({ signal }) => autocompleteSystems(normalizedQuery, signal),
    enabled: normalizedQuery.length >= 2,
    staleTime: 60_000,
  }));
  const suggestionRows = $derived(suggestions.data?.results ?? []);

  const bodyFiltersPaused = $derived(
    !anchor || viewState.rankingState.archetype !== 'any',
  );
  const radiusCappedByFilters = $derived(
    !bodyFiltersPaused && effectiveRadius !== viewState.radius,
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
      { enabled: FINDER_F4_ENABLED, radius: effectiveRadius },
    );
    if (
      selected.kind === 'any' &&
      !bodyFiltersPaused &&
      Object.keys(viewState.bodyFilters).length
    )
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
  const resultsScope = $derived(
    viewState.rankingState.archetype !== 'any'
      ? `Galaxy-wide · ranked by ${activeArchetypeLabel} fit · minimum tier ${viewState.rankingState.minimumTier}`
      : anchor
        ? `Within ${effectiveRadius.toLocaleString('en-US')} LY of ${anchor.name} · sorted by distance`
        : 'Galaxy-wide · best colony potential',
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
      untrack(() => {
        viewState.selectionRevision += 1;
      });
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
    } else {
      selectedSystem.set(id64);
      viewState.selectionRevision += 1;
    }
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
    if (bodyFiltersPaused || nextRadius === viewState.radius) return;
    void commitSearchInputs(viewState.bodyFilters, nextRadius);
  }

  async function commitSearchInputs(
    value: BodyFilterValues,
    nextRadius: SearchRadius,
  ): Promise<void> {
    viewState.focusedAnchorRevision = viewState.anchorSelectionRevision;
    const committed = normalizeBodyFilters(value);
    const params = serializeExploreSearchParams(
      { near: viewState.nearId, bodyFilters: committed, radius: nextRadius },
      page.url.searchParams,
    );
    // Paused chips do not take part in the active search, so clearing them
    // must not move a ranking off its current page.
    const nextRankingState = bodyFiltersPaused
      ? viewState.rankingState
      : { ...viewState.rankingState, offset: 0 };
    if (!bodyFiltersPaused) params.delete('offset');
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
      if (resultsEnabled && !bodyFiltersPaused) await results.refetch();
      return;
    }
    if (!bodyFiltersPaused) {
      const next = selectFinderRequest(
        { ...viewState.rankingState, offset: 0 },
        anchor,
        {
          enabled: FINDER_F4_ENABLED,
          radius: effectiveSearchRadius(nextRadius, committed),
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

  const starViewport = $derived(
    viewState.starViewportSize
      ? galaxyStarViewport(
          viewState.streamCamera,
          viewState.starDetailMode,
          viewState.starViewportSize,
        )
      : null,
  );
  const journeyViewport = $derived(
    commanderHistoryViewport(
      viewState.streamCamera,
      viewState.starViewportSize,
    ),
  );
  const catalogueStars = createGalaxyStarQuery(
    () => starViewport,
    () => viewState.streamCamera?.focusLy ?? { x: 0, y: 0, z: 0 },
  );
  const footprintKey = $derived(JSON.stringify(starViewport));
  $effect(() => {
    if (
      catalogueStars.data &&
      starViewport &&
      (catalogueStars.data !== viewState.appliedCataloguePacket ||
        footprintKey !== viewState.appliedFootprintKey)
    ) {
      viewState.appliedCataloguePacket = catalogueStars.data;
      viewState.appliedCatalogueViewport = starViewport;
      viewState.appliedFootprintKey = footprintKey;
      viewState.appliedCatalogueRevision =
        untrack(() => viewState.appliedCatalogueRevision) + 1;
    }
  });
  const commanderVisits = createQuery(() => ({
    queryKey: queryKeys.commanderTravel(
      travelAccountId ?? '',
      journeyViewport ?? {
        minX: 0,
        maxX: 0,
        minY: 0,
        maxY: 0,
        minZ: 0,
        maxZ: 0,
        limit: 1,
      },
    ),
    queryFn: ({ signal }) => {
      if (!journeyViewport || !viewState.streamCamera)
        throw new Error('No commander-history viewport requested');
      return getCommanderTravelHeatmap(
        journeyViewport,
        viewState.streamCamera.distanceLy,
        signal,
      );
    },
    enabled:
      showTravelHeatmap && journeyViewport !== null && travelAccountId !== null,
    staleTime: 20_000,
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 401) && failureCount < 1,
    // Keep the previous viewport's points while panning, but never carry one
    // account's travel into another account's view.
    placeholderData: (previousData, previousQuery) =>
      previousQuery?.queryKey[3] === travelAccountId ? previousData : undefined,
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
      | 'zoom'
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
      if (!journeyViewport) return 'zoom';
      if (!commanderVisits.data) return 'loading';
      return commanderVisits.data.count === 0 ? 'empty' : 'ready';
    },
  );
  // Request and cache key share the bucketed projection bounds, updated after
  // the camera debounce or a canvas resize. The server chooses a complete level.
  const heatmapRequest = $derived(
    heatmapViewportRequest(
      viewState.streamCamera,
      viewState.starViewportSize ?? undefined,
    ),
  );
  const heatmapRequestIdentity = $derived(
    heatmapViewportQueryKey(heatmapRequest)[1],
  );
  const densityEnabled = $derived(
    showDensity &&
      (viewState.streamCamera?.distanceLy ?? 0) >= DENSITY_CROSSFADE_NEAR_LY,
  );
  const heatmap = createQuery(() => {
    // Equivalent bounds must preserve a pending query when the canvas first
    // reports its size or a proportional resize leaves its aspect unchanged.
    // React only to the JSON identity and capture its matching request once.
    const request = untrack(() => heatmapRequest);
    const queryKey = heatmapViewportQueryKey(request);
    return {
      queryKey: [queryKey[0], heatmapRequestIdentity] as const,
      queryFn: ({ signal }) => getMapHeatmap(request, signal),
      // Enabled across the zoomed-out + overlap band; below `nearLy` density is
      // fully faded so no fetch needed.
      enabled: densityEnabled,
      staleTime: 60_000,
      placeholderData: (previousData) => previousData,
    };
  });
  // Reserve its overlay position before the first response. A late patch must
  // not append density after stars and make the next selection rebuild them.
  const emptyDensityContribution: SpatialContribution = {
    id: 'catalogue-density-base',
    owner: 'CATALOGUE',
    revision: 0,
    layers: [],
  };
  const densityResult = $derived(
    heatmap.isError
      ? classifyDensityError(heatmap.error)
      : heatmap.data === undefined
        ? undefined
        : mapHeatmapToDensityPayload(heatmap.data),
  );
  $effect(() => {
    // Query caches may settle after a switch-off. They must never restore a
    // disabled contribution or the retained cells used after refresh failures.
    if (!showDensity) return;
    // A placeholder is the already-applied density, not a new empty revision.
    if (heatmap.isPlaceholderData) return;
    const result = densityResult;
    // A transport or validation failure must not erase the last good layer.
    // A valid unpublished/empty response, on the other hand, replaces it.
    if (!result || (result.kind !== 'ready' && result.kind !== 'none')) return;
    const data = heatmap.data;
    if (data === viewState.appliedDensityData) return;
    viewState.appliedDensityData = data;
    // Cached viewport responses can have older timestamps. The renderer's
    // patch revision must advance even when returning to one of those views.
    const nextDensityRevision = untrack(() => viewState.densityRevision) + 1;
    viewState.densityRevision = nextDensityRevision;
    const mapped = densityContributionFrom(result, nextDensityRevision);
    const replacement = mapped.kind === 'ready' ? mapped.contribution : null;
    viewState.lastGoodDensity = result.kind === 'ready' ? result : null;
    if (!replacement && untrack(() => viewState.densityContribution) !== null) {
      // A null contribution is not dispatched as a patch; reload to remove it.
      viewState.densityRemovalRevision =
        untrack(() => viewState.densityRemovalRevision) + 1;
    }
    viewState.densityContribution = replacement;
  });
  const densityFailure = $derived(
    densityResult?.kind === 'unavailable' || densityResult?.kind === 'invalid'
      ? densityResult
      : null,
  );
  const densityState = $derived(
    !showDensity
      ? 'off'
      : !densityEnabled
        ? 'zoom'
        : densityFailure
          ? viewState.lastGoodDensity
            ? 'stale'
            : densityFailure.kind
          : heatmap.isPending || heatmap.isPlaceholderData
            ? 'loading'
            : densityResult?.kind === 'none'
              ? 'none'
              : viewState.lastGoodDensity?.area === 'empty'
                ? 'empty'
                : viewState.lastGoodDensity?.area === 'truncated'
                  ? 'truncated'
                  : 'ready',
  );
  const densityCellCount = $derived(
    viewState.lastGoodDensity?.payload.cells.length ?? 0,
  );
  const densityCoverage = $derived(
    viewState.lastGoodDensity
      ? densityCoverageLabel(viewState.lastGoodDensity)
      : null,
  );
  let previousFilteredSystems: CatalogueViewportResponse['systems'] = [];
  const filteredCatalogueSystems = $derived.by(
    (): CatalogueViewportResponse['systems'] => {
      const packetSystems: CatalogueViewportResponse['systems'] =
        viewState.appliedCataloguePacket?.systems ?? [];
      const filtered = packetSystems.filter((system) => {
        // Keep the accepted packet's visibility mask until its replacement is
        // ready. Applying the next viewport's mask to placeholder stars would
        // reveal wide-sample corners or remove local stars during the hand-off.
        const viewport = viewState.appliedCatalogueViewport;
        if (!viewport) return true;
        return (
          system.y >= viewport.minY &&
          system.y <= viewport.maxY &&
          withinGalaxyStarFootprint(viewport.footprint, system.x, system.z) &&
          (!viewport.wide || withinGalaxyDisk(system.x, system.z))
        );
      });
      if (
        filtered.length === previousFilteredSystems.length &&
        filtered.every(
          (system, index) => system === previousFilteredSystems[index],
        )
      )
        return previousFilteredSystems;
      previousFilteredSystems = filtered;
      return filtered;
    },
  );
  const viewportSystems = $derived(
    filteredCatalogueSystems.map((system): ExploreSystem => ({
      id64: system.id64,
      name: system.name,
      coords: { x: system.x, y: system.y, z: system.z },
      main_star_type: system.mainStarClass,
    })),
  );
  const systems = $derived(
    viewState.hasResultsIntent
      ? (displayedResults?.results ??
          (FINDER_F4_ENABLED ||
          !anchor ||
          Object.keys(viewState.bodyFilters).length
            ? []
            : viewportSystems))
      : [],
  );
  let finderSequence = 0;
  const finderRevision = $derived.by(() => {
    // Freshness, placeholder and retained-envelope changes do not change the
    // map. TanStack preserves the row array's identity when its data is equal.
    // Clearing must advance the revision even before a response has arrived.
    void systems;
    return ++finderSequence;
  });
  const finderContribution = $derived(
    createExploreFinderContribution(systems, finderRevision),
  );
  const commanderHistoryContribution = $derived(
    // Never render cached private travel once the query is failing (an expired
    // session keeps the last data alongside a 401).
    showTravelHeatmap &&
      travelAccountId !== null &&
      travelHeatmapState !== 'signed-out' &&
      !commanderVisits.isError &&
      commanderVisits.data
      ? createCommanderHistoryContribution(
          commanderVisits.data.mode,
          commanderVisits.data.visits,
          commanderVisits.dataUpdatedAt,
          commanderVisits.data.truncated,
        )
      : null,
  );
  let commanderHistorySequence = 0;
  const commanderHistoryRevision = $derived.by(() => {
    // Removing cached private visits (for example after a session expires)
    // does not advance the response timestamp, but must advance the scene.
    void commanderHistoryContribution;
    return ++commanderHistorySequence;
  });
  const nebulaContribution = $derived(
    viewState.showNebulae && viewState.nebulae
      ? createGalaxyNebulaeContribution(
          viewState.nebulae,
          viewState.nebulaResourceRevision,
        )
      : null,
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
        !mapExploreSelectedSystemPoint(viewState.retainedSelectedSystem)) &&
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
    const catalogue = viewState.appliedCataloguePacket?.systems.find(
      (system: CatalogueViewportResponse['systems'][number]) =>
        system.id64 === $selectedSystem.value,
    );
    return catalogue
      ? {
          id64: catalogue.id64,
          name: catalogue.name,
          coords: { x: catalogue.x, y: catalogue.y, z: catalogue.z },
          main_star_type: catalogue.mainStarClass,
          main_star_subtype: null,
          distance: null,
          population: null,
          primaryEconomy: null,
          allegiance: null,
          security: null,
        }
      : viewState.retainedSelectedSystem?.id64 === selectedId
        ? viewState.retainedSelectedSystem
        : null;
  });
  const selectedMapName = $derived(
    selectedMapSystem
      ? (selectedMapSystem.name ?? `System ${selectedMapSystem.id64}`)
      : null,
  );
  const selectedPoint = $derived(
    FINDER_F4_ENABLED && selectedMapSystem
      ? (mapExploreSelectedSystemPoint(selectedMapSystem) ??
          (viewState.retainedSelectedSystem?.id64 === viewState.urlSelection
            ? mapExploreSelectedSystemPoint(viewState.retainedSelectedSystem)
            : null) ??
          (reconstructedSelection
            ? mapExploreSelectedSystemPoint(reconstructedSelection)
            : null))
      : null,
  );
  const selectedWindowPoint = $derived.by((): GalaxySystemPoint | null => {
    if (selectedPoint) return selectedPoint;
    if (!selectedMapSystem) return mapAnchorPoint;
    return (
      mapExploreSelectedSystemPoint(selectedMapSystem) ??
      (mapAnchorPoint?.systemId64 === selectedMapSystem.id64
        ? mapAnchorPoint
        : null)
    );
  });
  const localCameraUnavailable = $derived(
    !selectedMapSystem
      ? 'Select a system to use the Local camera preset.'
      : !selectedWindowPoint
        ? 'Coordinates are unavailable for the selected system.'
        : null,
  );
  const reservedPoints = $derived(
    reservedExploreGalaxyPoints({
      selected:
        selectedPoint ??
        (selectedMapSystem
          ? mapExploreSelectedSystemPoint(selectedMapSystem)
          : null),
      anchor,
      // Only actual Finder rows are reserved, never the catalogue fallback.
      systems: viewState.hasResultsIntent
        ? (displayedResults?.results ?? [])
        : [],
      pins: $pins.value,
      watched:
        watchedSystems.ready && !watchedSystems.query.isError
          ? (watchedSystems.query.data?.watchlist ?? [])
          : [],
    }),
  );
  $effect(() => {
    viewState.reservedPoints = reservedPoints;
  });
  let catalogueSequence = 0;
  let previousCatalogueContribution: SpatialContribution | null = null;
  const catalogueLimit = $derived(
    viewState.appliedCatalogueViewport?.limit ?? starViewport?.limit ?? 0,
  );
  const catalogueTruncated = $derived(
    viewState.appliedCataloguePacket?.truncated ?? false,
  );
  const catalogueAvailable = $derived(
    filteredCatalogueSystems.length > 0 || reservedPoints.length > 0,
  );
  const catalogueStarsContribution = $derived.by(() => {
    previousCatalogueContribution = catalogueAvailable
      ? createCatalogueStarsContribution(
          filteredCatalogueSystems,
          ++catalogueSequence,
          catalogueTruncated,
          reservedPoints,
          catalogueLimit,
          previousCatalogueContribution,
        )
      : null;
    return previousCatalogueContribution;
  });
  $effect(() => {
    if (!FINDER_F4_ENABLED && selectedMapSystem?.coords)
      viewState.retainedSelectedSystem = selectedMapSystem;
  });
  let selectionSequence = 0;
  const selectionContribution = $derived(
    FINDER_F4_ENABLED
      ? createExploreSelectionContribution(
          selectedPoint,
          systems,
          ++selectionSequence,
        )
      : null,
  );
  $effect(() => {
    if (!FINDER_F4_ENABLED) return;
    // Retain a selected result or exact-system resolution across page changes.
    // Resolution is keyed by id, so late responses cannot move the selection.
    const selected = selectedOnPage ?? reconstructedSelection;
    if (
      selected &&
      (mapExploreSelectedSystemPoint(selected) ||
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
  let sceneSequence = 0;
  const scene = $derived.by(() => {
    if (FINDER_F4_ENABLED) {
      // Density patches and camera receipts have their own renderer updates.
      // Snapshot them only when visual scene inputs change. Neither timestamps
      // nor placeholder bookkeeping may rebuild all the scene contributions.
      void viewState.densityRemovalRevision;
      return buildExploreGalaxyScene(systems, selectedId, ++sceneSequence, {
        finderContribution,
        finderRevision,
        selectedPoint,
        selectionContribution,
        camera: untrack(() => viewState.retainedCamera?.camera ?? null),
        spatialContributions: collectGalaxySpatialContributions({
          regions: viewState.regionContribution,
          nebulae: nebulaContribution,
          density: untrack(() =>
            showDensity
              ? (viewState.densityContribution ?? emptyDensityContribution)
              : null,
          ),
          catalogueStars: catalogueStarsContribution,
          commanderHistory: commanderHistoryContribution,
        }),
        selectedRegionId: viewState.selectedRegionId,
      });
    }
    return buildExploreGalaxyScene(
      systems,
      $selectedSystem.value,
      finderRevision +
        viewState.selectionRevision +
        viewState.regionResourceRevision +
        viewState.streamRevision +
        viewState.layerToggleRevision +
        viewState.appliedCatalogueRevision +
        (catalogueStarsContribution?.revision ?? 0) +
        commanderVisits.dataUpdatedAt +
        commanderHistoryRevision +
        viewState.nebulaResourceRevision +
        viewState.densityRevision,
      {
        finderContribution,
        finderRevision,
        camera: viewState.retainedCamera?.camera ?? null,
        spatialContributions: collectGalaxySpatialContributions({
          regions: viewState.regionContribution,
          nebulae: nebulaContribution,
          density: showDensity ? viewState.densityContribution : null,
          catalogueStars: catalogueStarsContribution,
          commanderHistory: commanderHistoryContribution,
        }),
        selectedRegionId: viewState.selectedRegionId,
      },
    );
  });

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
    if (viewportCameraTimeout !== null) {
      window.clearTimeout(viewportCameraTimeout);
      viewportCameraTimeout = null;
    }
    pendingViewportCamera = null;
    viewState.query = hit.name;
    autocompleteOpen = false;
    viewState.rankingNotice = '';
    const committedAnchor = readExploreAnchor({ anchor: hit });
    const params = serializeExploreSearchParams(
      {
        near: committedAnchor?.id64 ?? null,
        bodyFilters: viewState.bodyFilters,
        radius: viewState.radius,
      },
      page.url.searchParams,
    );
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
    if (!committedAnchor) return;
    if (!FINDER_F4_ENABLED) {
      selectedSystem.set(hit.id64);
      viewState.selectionRevision += 1;
    }
    viewState.streamRevision += 1;
    updateStreamCamera({
      focusLy: {
        x: hit.x ?? 0,
        y: hit.y ?? 0,
        z: hit.z ?? 0,
      },
      distanceLy: GALAXY_LOCAL_VIEW_DISTANCE_LY,
      bearingRad: 0,
      pitchRad: 0.55,
      projection: 'perspective',
      revision: viewState.streamRevision,
    });
    viewState.focusTarget = { kind: 'system', systemId64: hit.id64 };
    viewState.focusRevision += 1;
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
    commitSelection(system.id64);
    viewState.focusTarget = { kind: 'system', systemId64: system.id64 };
    viewState.focusRevision += 1;
  }

  function showMap(
    event: MouseEvent,
    system: ExploreSystem | null = selectedMapSystem,
  ): void {
    const point =
      system?.id64 === selectedMapSystem?.id64
        ? selectedWindowPoint
        : system
          ? mapExploreSelectedSystemPoint(system)
          : mapAnchorPoint;
    if (
      point &&
      (point.systemId64 !== viewState.mapFocusedSystemId ||
        !viewState.retainedCamera)
    ) {
      // This is presentation intent only. The existing camera command path
      // focuses actual coordinates without submitting or changing a search.
      const camera = focusGalaxyCamera(
        viewState.retainedCamera?.camera ?? scene.camera,
        point.positionLy,
        GALAXY_LOCAL_VIEW_DISTANCE_LY,
      );
      viewState.initialGalaxyViewRequested = true;
      viewState.retainedCamera = { finderRevision, camera };
      updateStreamCamera(camera);
      requestView(camera);
      viewState.mapFocusedSystemId = point.systemId64;
      viewState.mapCameraPreset = 'local';
    }
    viewState.mapResetCamera ??=
      viewState.retainedCamera?.camera ?? scene.camera;
    // An already-selected result can have a completed or queued FLY_TO. The
    // explicit camera request above, or the retained camera on reopen, wins.
    viewState.completedFocusRevision = viewState.focusRevision;
    // On a wide screen the map is already beside the results, so this only
    // centres it. At phone width the Finder covers the map: hide the panel.
    if (window.matchMedia(PHONE_STAGE_QUERY).matches) hideFinderPanel(event);
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

  function selectMapSystem(point: GalaxySystemPoint): void {
    try {
      selectResult({
        id64: parseId64(point.systemId64),
        name: point.name,
        coords: point.positionLy,
        main_star_type: point.primaryStar?.type,
        main_star_subtype: point.primaryStar?.subtype,
        population: point.summary?.population,
        primaryEconomy: point.summary?.primaryEconomy,
        secondaryEconomy: point.summary?.secondaryEconomy,
        security: point.summary?.security,
        allegiance: point.summary?.allegiance,
        government: point.summary?.government,
      });
    } catch {
      // Preserve the same fail-closed identity boundary as canvas picking.
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

  function updateStreamCamera(camera: CameraState): void {
    viewState.starDetailMode = galaxyDetailMode(
      camera.distanceLy,
      viewState.starDetailMode,
    );
    viewState.streamCamera = camera;
  }

  function retainCamera(camera: CameraState): void {
    viewState.retainedCamera = { finderRevision, camera };
    pendingViewportCamera = camera;
    if (viewportCameraTimeout !== null) {
      window.clearTimeout(viewportCameraTimeout);
    }
    viewportCameraTimeout = window.setTimeout(() => {
      viewportCameraTimeout = null;
      const nextCamera = pendingViewportCamera;
      pendingViewportCamera = null;
      if (nextCamera) updateStreamCamera(nextCamera);
    }, 180);
  }

  function acknowledgeCameraRequest(camera: CameraState): void {
    const requested = viewState.cameraRequest;
    if (
      requested &&
      camera.focusLy.x === requested.focusLy.x &&
      camera.focusLy.y === requested.focusLy.y &&
      camera.focusLy.z === requested.focusLy.z &&
      camera.distanceLy === requested.distanceLy &&
      camera.bearingRad === requested.bearingRad &&
      camera.pitchRad === requested.pitchRad &&
      camera.projection === requested.projection
    )
      viewState.appliedCameraRequestRevision = viewState.cameraRequestRevision;
  }

  function handleRuntimeEvent(event: RuntimeEvent): void {
    if (
      event.type === 'TRANSITION_FINISHED' &&
      event.target &&
      JSON.stringify(event.target) === JSON.stringify(viewState.focusTarget)
    ) {
      viewState.completedFocusRevision = viewState.focusRevision;
      if (event.target.kind === 'system')
        viewState.mapFocusedSystemId = event.target.systemId64;
    }
    if (
      event.type === 'SCENE_APPLIED' ||
      event.type === 'CONTRIBUTION_APPLIED'
    ) {
      viewState.renderedNebulaReceipt =
        event.renderedLayers.find(
          (layer) =>
            layer.layerId === GALAXY_NEBULAE_LAYER_ID &&
            layer.contributionRevision === viewState.nebulaResourceRevision,
        ) ?? null;
    } else if (event.type === 'RESOURCE_LOST') {
      viewState.renderedNebulaReceipt = null;
    }
    if (event.type === 'CAMERA_CHANGED' && 'focusLy' in event.camera) {
      acknowledgeCameraRequest(event.camera);
      // Retained navigation state may already hold a flight's destination.
      // Status follows the camera actually applied by the renderer each frame.
      viewState.nebulaCameraDistanceLy = event.camera.distanceLy;
      if (spatialCameraTransitionActive) return;
      retainCamera(event.camera);
    } else if (event.type === 'TARGET_HOVERED') {
      viewState.hoveredRegionId =
        event.target?.kind === 'region' ? event.target.id : null;
      viewState.hoveredSystemId =
        event.target?.kind === 'system' ? event.target.systemId64 : null;
    } else if (event.type === 'TARGET_PICKED') {
      if (event.target?.kind === 'region') {
        selectRegion(event.target.id);
      } else if (event.target?.kind === 'system') {
        try {
          const picked = parseId64(event.target.systemId64);
          commitSelection(picked);
          viewState.lastPickedId64 = picked;
          viewState.mapFocusedSystemId = picked;
        } catch {
          // Renderer events fail closed if an adapter emits a non-Id64 target.
        }
      }
    }
  }

  function toggleTravelHeatmap(): void {
    layerPreferences.set({
      ...$layerPreferences.value,
      journeys: !showTravelHeatmap,
    });
    viewState.layerToggleRevision += 1;
  }

  function toggleDensity(): void {
    const enabled = !showDensity;
    layerPreferences.set({ ...$layerPreferences.value, density: enabled });
    if (!enabled) {
      // Cancel in-flight work/retries and mark all zoom caches stale without
      // starting requests. Re-enabling fetches at the current zoom, even if it
      // has a fresh cached response. Only switch positions are persisted.
      void queryClient.cancelQueries({ queryKey: ['map-heatmap'] });
      void queryClient.invalidateQueries({
        queryKey: ['map-heatmap'],
        refetchType: 'none',
      });
      viewState.lastGoodDensity = null;
      viewState.densityContribution = null;
      viewState.appliedDensityData = undefined;
    }
    viewState.densityRemovalRevision += 1;
    viewState.layerToggleRevision += 1;
  }

  function toggleNebulae(): void {
    viewState.showNebulae = !viewState.showNebulae;
    viewState.renderedNebulaReceipt = null;
    viewState.layerToggleRevision += 1;
  }

  function regionName(regionId: string | null): string {
    if (!regionId || !viewState.regions) return 'None';
    return (
      viewState.regions.regions.find(
        (region: GalaxyRegionsPayload['regions'][number]) =>
          String(region.id) === regionId,
      )?.name ?? 'Unknown'
    );
  }

  function selectRegion(regionId: string | null): void {
    if (regionId === viewState.selectedRegionId || !viewState.regions) return;
    viewState.selectedRegionId = regionId;
    viewState.selectionRevision += 1;
  }

  function requestView(camera: CameraState): void {
    viewState.cameraRequest = camera;
    viewState.cameraRequestRevision += 1;
  }

  function handleCameraTransitionChange(
    active: boolean,
    camera?: CameraState | SystemCameraState | null,
  ): void {
    spatialCameraTransitionActive = active;
    // SET_CAMERA already knows its destination. Retain it when the flight
    // starts so the viewport debounce cannot load the pre-flight close view
    // and then switch back to the overview on settle. FLY_TO supplies its camera
    // only on completion; its in-flight samples are still ignored.
    if ((FINDER_F4_ENABLED || !active) && camera && 'focusLy' in camera) {
      acknowledgeCameraRequest(camera);
      retainCamera(camera);
    }
  }

  function centreSelectedSystem(): void {
    if (!selectedMapSystem || !selectedWindowPoint) return;
    viewState.initialGalaxyViewRequested = true;
    viewState.mapCameraPreset = 'local';
    requestView(
      focusGalaxyCamera(
        viewState.retainedCamera?.camera ?? scene.camera,
        selectedWindowPoint.positionLy,
        GALAXY_LOCAL_VIEW_DISTANCE_LY,
      ),
    );
  }

  function overview(): void {
    if (!viewState.regions) return;
    viewState.mapCameraPreset = 'galaxy';
    const { origin, pixel_scale } = viewState.regions.lookup;
    const canvas = mapSection?.querySelector('canvas');
    requestView(
      fitGalaxyPlaneCamera(
        {
          ...(FINDER_F4_ENABLED
            ? (viewState.retainedCamera?.camera ?? scene.camera)
            : scene.camera),
          bearingRad: 0,
          pitchRad: Math.PI / 2,
        },
        {
          min: { x: origin.x, y: 0, z: origin.z },
          max: {
            x: origin.x + viewState.regions.source.width * pixel_scale,
            y: 0,
            z: origin.z + viewState.regions.source.height * pixel_scale,
          },
        },
        {
          width: canvas?.clientWidth || 1000,
          height: canvas?.clientHeight || 500,
        },
      ),
    );
  }

  function viewSelectedRegion(): void {
    const region = viewState.regions?.regions.find(
      (candidate: GalaxyRegionsPayload['regions'][number]) =>
        String(candidate.id) === viewState.selectedRegionId,
    );
    if (region) {
      viewState.mapCameraPreset = null;
      requestView(
        focusGalaxyCamera(
          FINDER_F4_ENABLED
            ? (viewState.retainedCamera?.camera ?? scene.camera)
            : scene.camera,
          region.labelLy,
          18_000,
        ),
      );
    }
  }

  async function loadRegions(): Promise<void> {
    if (regionsLoading) return;
    regionsLoading = true;
    const attempt = ++regionLoadAttempt;
    viewState.regionState = 'loading';
    try {
      const accepted = await fetchAuthoritativeGalaxyRegions();
      if (attempt !== regionLoadAttempt) return;
      viewState.regions = accepted;
      viewState.regionContribution = createGalaxyRegionsContribution(
        accepted,
        1,
      );
      viewState.regionResourceRevision += 1;
      viewState.regionState = 'ready';
      if (!viewState.initialGalaxyViewRequested) {
        viewState.initialGalaxyViewRequested = true;
        void tick().then(overview);
      }
    } catch {
      if (attempt !== regionLoadAttempt) return;
      viewState.regions = null;
      viewState.regionContribution = null;
      viewState.regionState = 'failed';
    } finally {
      regionsLoading = false;
    }
  }

  async function loadNebulae(): Promise<void> {
    const attempt = ++nebulaLoadAttempt;
    nebulaController?.abort();
    const controller = new AbortController();
    nebulaController = controller;
    viewState.nebulaState = 'loading';
    viewState.renderedNebulaReceipt = null;
    try {
      const accepted = await fetchGalaxyNebulae(controller.signal);
      if (attempt !== nebulaLoadAttempt || controller.signal.aborted) return;
      viewState.nebulae = accepted;
      viewState.nebulaResourceRevision += 1;
      viewState.nebulaState = 'ready';
    } catch {
      if (attempt !== nebulaLoadAttempt || controller.signal.aborted) return;
      viewState.nebulae = null;
      viewState.nebulaState = 'failed';
    } finally {
      if (nebulaController === controller) nebulaController = null;
    }
  }

  $effect(() => {
    const nebulaeEnabled = viewState.showNebulae;
    untrack(() => {
      if (!viewState.regions && viewState.regionState !== 'failed')
        void loadRegions();
      if (
        nebulaeEnabled &&
        !viewState.nebulae &&
        viewState.nebulaState !== 'failed'
      )
        void loadNebulae();
    });
  });

  onMount(() => {
    return () => {
      regionLoadAttempt += 1;
      nebulaLoadAttempt += 1;
      nebulaController?.abort();
      // Cancel a pending viewport-camera debounce so it can't fire after teardown.
      if (viewportCameraTimeout !== null) {
        window.clearTimeout(viewportCameraTimeout);
        viewportCameraTimeout = null;
      }
    };
  });

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
          disabled={bodyFiltersPaused}
          paused={bodyFiltersPaused}
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
        {#if FINDER_F4_ENABLED && viewState.rankingState.archetype !== 'any'}
          <p>
            Archetype ranking is galaxy-wide today; choose Any to search around {anchor.name}.
          </p>
        {/if}
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
          (FINDER_F4_ENABLED
            ? updatingResults
            : results.isFetching || catalogueStars.isFetching)}
      >
        <div class="panel-heading">
          <div>
            <p class="eyebrow">Catalogue results</p>
            <h2 id="results-title">
              {FINDER_F4_ENABLED && viewState.rankingState.archetype !== 'any'
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
            disabled={selectedMapSystem !== null && !selectedWindowPoint}
            onclick={(event) => showMap(event)}>Show on map</button
          >
          {#if selectedMapSystem && !selectedWindowPoint}
            <span>Map coordinates unavailable for the selected system.</span>
          {/if}
          <label class="search-radius" for="search-radius">
            Within
            <select
              id="search-radius"
              value={effectiveRadius}
              disabled={bodyFiltersPaused}
              aria-describedby={bodyFiltersPaused
                ? 'search-radius-disabled'
                : Object.keys(viewState.bodyFilters).length > 0
                  ? 'search-radius-capped'
                  : undefined}
              onchange={(event) =>
                commitRadius(Number(event.currentTarget.value))}
            >
              {#each SEARCH_RADIUS_OPTIONS as option (option)}
                <option
                  value={option}
                  disabled={!bodyFiltersPaused &&
                    Object.keys(viewState.bodyFilters).length > 0 &&
                    option > FILTERED_SEARCH_RADIUS_CAP_LY}
                  >{option.toLocaleString('en-US')} LY</option
                >
              {/each}
            </select>
          </label>
          {#if bodyFiltersPaused}
            <p id="search-radius-disabled" class="radius-hint">
              Search radius works when you search near a system with Any
              selected.
            </p>
          {:else if Object.keys(viewState.bodyFilters).length > 0}
            <p id="search-radius-capped" class="radius-hint">
              Body filters limit the search to {FILTERED_SEARCH_RADIUS_CAP_LY} LY{radiusCappedByFilters
                ? `; your ${viewState.radius.toLocaleString('en-US')} LY radius resumes when the filters are cleared`
                : ''}.
            </p>
          {/if}
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
        {:else if !FINDER_F4_ENABLED && results.isPending && catalogueStars.isPending}
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
                    disabled={!selectedWindowPoint}
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

    {#snippet mapCameraActions(cameraHelp: Snippet)}
      {#if selectedMapSystem}
        <button
          type="button"
          class="centre-system"
          disabled={!selectedWindowPoint}
          onclick={centreSelectedSystem}>Centre on {selectedMapName}</button
        >
      {/if}
      <MapToolPopout
        kind="sources"
        label="Sources"
        title="Map sources and controls"
      >
        <p class="map-note">{MAP_SOURCES_NOTE}</p>
        {@render cameraHelp()}
      </MapToolPopout>
    {/snippet}
    <ExploreMapWindow
      bind:element={mapSection}
      selectedSystemName={selectedMapName}
      finderPanelOpen={viewState.finderPanelOpen}
      onShowFinder={showFinderPanel}
      hoveredSystemId={viewState.hoveredSystemId}
      canvasProps={{
        scene,
        cameraActions: mapCameraActions,
        resetCamera: viewState.mapResetCamera,
        anchorPoint: mapAnchorPoint,
        onSystemSelect: selectMapSystem,
        systemInspectHref: (point) =>
          FINDER_F4_ENABLED
            ? inspectHref(parseId64(point.systemId64), page.url)
            : resolve(`/inspect?system=${point.systemId64}`),
        onSystemInspect: FINDER_F4_ENABLED
          ? (event, point) => followInspect(event, parseId64(point.systemId64))
          : undefined,
        onViewportChange: (viewport) => {
          if (
            viewState.starViewportSize?.width !== viewport.width ||
            viewState.starViewportSize?.height !== viewport.height
          )
            viewState.starViewportSize = viewport;
        },
        cameraRequest:
          viewState.cameraRequestRevision ===
          viewState.appliedCameraRequestRevision
            ? null
            : viewState.cameraRequest,
        cameraRequestRevision: viewState.cameraRequestRevision,
        focusTarget:
          viewState.focusRevision === viewState.completedFocusRevision
            ? null
            : viewState.focusTarget,
        focusRevision: viewState.focusRevision,
        contributionPatch:
          FINDER_F4_ENABLED && showDensity
            ? viewState.densityContribution
            : null,
        contributionPatchRevision: FINDER_F4_ENABLED
          ? (viewState.densityContribution?.revision ?? 0)
          : 0,
        onRuntimeEvent: handleRuntimeEvent,
        onTransitionChange: handleCameraTransitionChange,
      }}
    >
      {#snippet beforeCanvas()}
        <div class="map-layer-toolbar">
          <div
            class="map-camera-presets"
            role="group"
            aria-label="Camera presets"
          >
            <button
              type="button"
              data-map-camera-preset="local"
              aria-pressed={viewState.mapCameraPreset === 'local'}
              disabled={localCameraUnavailable !== null}
              aria-describedby={localCameraUnavailable
                ? 'local-camera-reason'
                : undefined}
              title={localCameraUnavailable ?? 'Focus on the selected system'}
              onclick={centreSelectedSystem}>Local</button
            >
            <button
              type="button"
              data-map-camera-preset="galaxy"
              aria-pressed={viewState.mapCameraPreset === 'galaxy'}
              disabled={!viewState.regions}
              onclick={overview}>Galaxy</button
            >
          </div>
          {#if localCameraUnavailable}<span
              id="local-camera-reason"
              class="sr-only">{localCameraUnavailable}</span
            >{/if}
          <MapToolPopout
            kind="regions"
            label="Regions 42"
            title="Galactic regions"
          >
            <p data-region-resource-state={viewState.regionState}>
              {viewState.regionState === 'ready'
                ? '42/42 regions'
                : 'canonical ly'}
            </p>
            {#if viewState.regionState === 'loading'}
              <p class="region-load-state" role="status">
                Loading all 42 authoritative regions…
              </p>
            {:else if viewState.regionState === 'failed'}
              <div class="region-load-state error" role="alert">
                <span>The authoritative region resource failed validation.</span
                >
                <button type="button" onclick={loadRegions}
                  >Retry regions</button
                >
              </div>
            {:else if viewState.regions}
              <div class="product-region-controls">
                <label for="explore-region-picker">
                  <span>Galactic region</span>
                  <select
                    id="explore-region-picker"
                    value={viewState.selectedRegionId ?? ''}
                    onchange={(event) =>
                      selectRegion(event.currentTarget.value || null)}
                  >
                    <option value="">No region selected</option>
                    {#each viewState.regions.regions as region (region.id)}
                      <option
                        value={String(region.id)}
                        data-galaxy-region-option={region.id}
                      >
                        {region.id}. {region.name}
                      </option>
                    {/each}
                  </select>
                </label>
                <div
                  class="product-region-actions"
                  role="group"
                  aria-label="Map views"
                >
                  <button type="button" onclick={overview}
                    >All 42 regions</button
                  >
                  <button
                    type="button"
                    disabled={!viewState.selectedRegionId}
                    onclick={viewSelectedRegion}>View selected</button
                  >
                </div>
                <div class="product-region-status" aria-live="polite">
                  <span
                    >Selected: <strong
                      data-selected-region-id={viewState.selectedRegionId ?? ''}
                      >{regionName(viewState.selectedRegionId)}</strong
                    ></span
                  >
                  <span
                    >Hovered: <strong
                      data-hovered-region-id={viewState.hoveredRegionId ?? ''}
                      >{regionName(viewState.hoveredRegionId)}</strong
                    ></span
                  >
                </div>
              </div>
            {/if}
          </MapToolPopout>

          <MapLayers
            densityEnabled={showDensity}
            journeysEnabled={showTravelHeatmap}
            nebulaeEnabled={viewState.showNebulae}
            onDensityToggle={toggleDensity}
            onJourneysToggle={toggleTravelHeatmap}
            onNebulaeToggle={toggleNebulae}
            densityCellSizeLy={viewState.lastGoodDensity?.payload.cellSizeLy ??
              null}
            densityGeneration={viewState.lastGoodDensity?.payload
              .generationId ?? null}
            {densityCoverage}
            journeysMaxVisits={travelHeatmapState === 'ready'
              ? Math.max(
                  1,
                  ...(commanderVisits.data?.visits.map(
                    (visit) => visit.visitCount,
                  ) ?? []),
                )
              : undefined}
          >
            {#snippet densityStatus()}
              <span
                role="status"
                aria-label="Catalogue density"
                data-density-state={densityState}
                data-density-reason={densityEnabled
                  ? densityFailure?.reason
                  : undefined}
                data-density-cell-count={densityCellCount}
              >
                {#if densityState === 'off'}
                  Density layer hidden
                {:else if densityState === 'zoom'}
                  Zoom out to see known-system density
                {:else if densityState === 'loading'}
                  Loading catalogue density…{viewState.lastGoodDensity
                    ? ' · showing previous area'
                    : ''}
                {:else if densityState === 'stale'}
                  {densityCellCount.toLocaleString()} density cells · {densityCoverage}
                  · stale · {densityFailure?.kind === 'invalid'
                    ? 'density response invalid'
                    : 'density refresh unavailable'}
                {:else if densityState === 'unavailable'}
                  Density layer unavailable
                {:else if densityState === 'invalid'}
                  Density response invalid
                {:else if densityState === 'none'}
                  No published density pyramid
                {:else if densityState === 'empty'}
                  No density cells in this area
                {:else}
                  {densityCellCount.toLocaleString()} density cells{viewState
                    .lastGoodDensity?.area === 'truncated'
                    ? ` · ${densityCoverage}`
                    : ''}
                {/if}
                {#if densityEnabled && densityFailure}
                  · <button type="button" onclick={() => void heatmap.refetch()}
                    >Retry</button
                  >
                {/if}
              </span>
            {/snippet}
            {#snippet journeysStatus()}
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
                {:else if travelHeatmapState === 'zoom'}
                  Travel history appears when individual-star range is active
                {:else if travelHeatmapState === 'loading'}
                  Reading imported journal visits…
                {:else if travelHeatmapState === 'error'}
                  Travel heatmap unavailable · <button
                    type="button"
                    onclick={() => commanderVisits.refetch()}>Retry</button
                  >
                {:else if travelHeatmapState === 'empty'}
                  No imported journal travel in this view
                {:else}
                  {(commanderVisits.data?.count ?? 0).toLocaleString()} visited
                  {commanderVisits.data?.mode === 'density'
                    ? ' cells'
                    : ' systems'}{commanderVisits.data?.truncated
                    ? ' · bounded view'
                    : ''}
                {/if}
              </span>
            {/snippet}
            {#snippet nebulaeStatus()}
              <span
                role="status"
                aria-label="Nebulae"
                data-nebula-resource-state={viewState.nebulaState}
              >
                {#if viewState.nebulaState === 'loading'}
                  Loading complete nebula inventory…
                {:else if viewState.nebulaState === 'failed'}
                  Nebula layer unavailable · <button
                    type="button"
                    onclick={loadNebulae}>Retry</button
                  >
                {:else if viewState.nebulae}
                  {`${viewState.nebulae.nebulae.length.toLocaleString()} catalogue ${
                    viewState.nebulae.nebulae.length === 1
                      ? 'record'
                      : 'records'
                  }`} · {nebulaDisplayStatus} ·
                  <a
                    href="https://edastro.com/mapcharts/files.html"
                    target="_blank"
                    rel="noreferrer">{viewState.nebulae.attribution}</a
                  >
                  ·
                  <a
                    href="https://edastro.com/mapcharts/files/nebulae-coordinates.csv"
                    target="_blank"
                    rel="noreferrer">source CSV</a
                  >
                  · {viewState.nebulae.rightsNotice}
                {/if}
              </span>
            {/snippet}
          </MapLayers>
        </div>
        <div class="map-window-status">
          <span
            class="map-catalogue-status"
            data-catalogue-star-count={catalogueStarsContribution?.layers[0]
              ?.targetCount ?? 0}
          >
            {#if !starViewport}
              Zoom in for individual catalogue stars
            {:else if catalogueStars.isPending && !viewState.appliedCataloguePacket}
              Loading exact catalogue stars…
            {:else if catalogueStars.isError}
              Exact-star layer unavailable
            {:else}
              {(
                catalogueStarsContribution?.layers[0]?.targetCount ?? 0
              ).toLocaleString()} exact stars{catalogueStarsContribution
                ?.layers[0]?.truncated
                ? ' · bounded view'
                : ''}
            {/if}
          </span>

          <p
            class="selection-status"
            aria-live="polite"
            data-last-picked-id64={viewState.lastPickedId64}
          >
            {#if viewState.lastPickedId64}Spatial pick selected <code
                >{viewState.lastPickedId64}</code
              >.{:else if $selectedSystem.value}Active system <code
                >{$selectedSystem.value}</code
              >.{:else}Select a result to focus the map.{/if}
          </p>
        </div>
      {/snippet}
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
  }
  .map-camera-presets button,
  .centre-system {
    padding-inline: 0.35rem;
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
    max-width: 100%;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .map-camera-presets button:first-child {
    border-radius: var(--radius-pill) 0 0 var(--radius-pill);
  }
  .map-camera-presets button:last-child {
    border-radius: 0 var(--radius-pill) var(--radius-pill) 0;
  }
  .map-camera-presets button[aria-pressed='true'] {
    border-color: var(--accent);
    background-color: var(--accent);
    color: var(--accent-text);
  }
  .map-camera-presets button:disabled,
  .centre-system:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
  .map-camera-presets button:focus-visible,
  .centre-system:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 2px;
  }
  .product-region-controls {
    grid-template-columns: minmax(0, 1fr);
    padding: 0;
    border: 0;
    background: transparent;
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
