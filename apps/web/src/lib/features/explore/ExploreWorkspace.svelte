<script lang="ts">
  import { createQuery } from '@tanstack/svelte-query';
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { tick, untrack } from 'svelte';
  import { ArrowRight, Sparkles } from '@lucide/svelte';
  import {
    ApiError,
    getSystem,
    type AutocompleteSystem,
    type ExploreSystem,
  } from '$lib/api/client';
  import { queryClient, queryKeys } from '$lib/api/query';
  import { auth } from '$lib/auth/auth';
  import { FINDER_F4_ENABLED } from '$lib/config';
  import { parseId64, type Id64 } from '$lib/domain/id64';
  import {
    anyMinimumScore,
    hasRankingSearchParams,
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
  import FinderResultLine from './FinderResultLine.svelte';
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
  import { mapLeftAtFor, rememberMapLeftAt } from '$lib/galaxy-map/left-at';
  import {
    JOURNEY_LINES_IN_SHORT,
    showingInWords,
    type MapSystem,
  } from '$lib/galaxy-map/map-content';
  import WorkspaceHeader from '$lib/components/WorkspaceHeader.svelte';
  import SystemActions from './SystemActions.svelte';
  import WatchlistPanel from './WatchlistPanel.svelte';
  import ComparePanel from './ComparePanel.svelte';
  import InspectPopout from '$lib/components/InspectPopout.svelte';
  import SystemSearchBox from './SystemSearchBox.svelte';
  import { createFinderResults } from './finder-results.svelte';
  import { createMyJourneys } from './my-journeys.svelte';
  import { createSelectedSystem } from './selected-system.svelte';

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
  const reviewLabRun = import.meta.env.VITE_REVIEW_LAB === '1';
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
  // Any near a system, showing only systems of a tier or better.
  const anyFloorNearAnchor = $derived(
    FINDER_F4_ENABLED &&
      anchor !== null &&
      anyMinimumScore(viewState.rankingState) !== null,
  );
  // Body filters, a picked type and a "Show only" tier cap the anchored
  // search at the API's filtered radius; the URL keeps the chosen radius so
  // it resumes when they are taken off.
  const effectiveRadius = $derived(
    effectiveSearchRadius(
      viewState.radius,
      viewState.bodyFilters,
      typeNearAnchor || anyFloorNearAnchor,
    ),
  );
  const radiusCapped = $derived(
    typeNearAnchor ||
      anyFloorNearAnchor ||
      Object.keys(viewState.bodyFilters).length > 0,
  );
  // The system-name box, which lives in a file of its own.
  let searchBox = $state<ReturnType<typeof SystemSearchBox>>();
  // Saved systems and Compare open as pop-outs from the Finder panel, so
  // they are in reach without leaving the map and the results.
  let savedOpen = $state(false);
  let compareOpen = $state(false);
  let savedTrigger = $state<HTMLButtonElement>();
  let finderPanel = $state<HTMLElement>();
  let compareTrigger = $state<HTMLButtonElement>();
  let mapSection = $state<HTMLDivElement>();
  let resultsSection = $state<HTMLElement>();
  // The control that hid the Finder panel gets focus back when it reopens.
  let panelToggleOrigin: HTMLElement | null = null;
  let stage = $state<HTMLDivElement>();
  let stageTop = $state(0);
  // The galaxy map itself, for the camera buttons of the page.
  let galaxyMap = $state<ReturnType<typeof GalaxyMap>>();
  let mapPhase = $state<'starting' | 'ready' | 'failed'>('starting');
  // Must match the phone breakpoint in this component's styles.
  const PHONE_STAGE_QUERY = '(max-width: 900px)';
  // At phone width the Finder (with its results) and the map take turns, so
  // while the Finder is shown the map cannot be seen. On a wider screen the
  // results and the map take turns beside the Finder panel.
  let phoneWidth = $state(false);
  $effect(() => {
    const query = window.matchMedia?.(PHONE_STAGE_QUERY);
    if (!query) return;
    phoneWidth = query.matches;
    const changed = () => (phoneWidth = query.matches);
    query.addEventListener?.('change', changed);
    return () => query.removeEventListener?.('change', changed);
  });
  const resultsInFront = $derived(
    viewState.hasResultsIntent &&
      (phoneWidth ? viewState.finderPanelOpen : !viewState.mapInFront),
  );
  // Whether the map can be seen. On a wider screen the map behind the results
  // still follows the selection, so it is ready when brought in front; at
  // phone width it is given nothing to draw while the Finder covers it.
  const mapInFront = $derived(
    phoneWidth ? !viewState.finderPanelOpen : !resultsInFront,
  );
  const mapOnScreen = $derived(!(phoneWidth && viewState.finderPanelOpen));
  // On a wider screen the map can be brought in front of the results.
  const resultsBehindMap = $derived(
    !phoneWidth && viewState.hasResultsIntent && viewState.mapInFront,
  );
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

  // Body filters join every search: near a system, and galaxy-wide, where
  // they thin the window of the best 10,000. The radius needs a system.
  const radiusDisabled = $derived(!anchor);
  const galaxyFiltered = $derived(
    !anchor && Object.keys(viewState.bodyFilters).length > 0,
  );
  const radiusCappedByFilters = $derived(
    !radiusDisabled && effectiveRadius !== viewState.radius,
  );
  // Find's request, its results and their count live in a file of their own.
  const finder = createFinderResults({
    viewState: () => viewState,
    resultCache: () => resultCache,
    remount,
    anchor: () => anchor,
    effectiveRadius: () => effectiveRadius,
  });
  const results = finder.results;
  const resultsEnabled = $derived(finder.resultsEnabled);
  const anchorReference = $derived(finder.anchorReference);
  const displayedResults = $derived(finder.displayedResults);
  const currentResponse = $derived(finder.currentResponse);
  const pageCorrection = $derived(finder.pageCorrection);
  const updatingResults = $derived(finder.updatingResults);
  const shownPage = $derived(finder.shownPage);
  const countingMatches = $derived(finder.countingMatches);
  // With nothing selected, Show on map shows the system searched near.
  const anchorAsSystem = $derived<ExploreSystem | null>(
    anchor
      ? {
          id64: anchor.id64,
          name: anchor.name,
          coords: { x: anchor.x, y: anchor.y, z: anchor.z },
        }
      : null,
  );
  // The rows carry the Finder's scores (and the header names them) once the
  // shown page is the one the picker asked for.
  const rankedList = $derived(
    FINDER_F4_ENABLED &&
      displayedResults !== undefined &&
      displayedResults !== null &&
      !(
        viewState.rankingState.archetype === 'any' &&
        displayedResults.finderState!.archetype !== 'any'
      ),
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
          ? `Within ${effectiveRadius.toLocaleString('en-US')} LY of ${anchor.name} ${anyFloorNearAnchor ? ` · tier ${viewState.rankingState.minimumTier} or better` : ''} · sorted by distance`
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
    // A changed search or page is shown as a list.
    viewState.mapInFront = false;
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
    searchBox?.close();
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
      ? 'Showing the whole galaxy, best first.'
      : '';
    searchBox?.focus();
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
    if (resultsChange) viewState.mapInFront = false;
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
          radius: effectiveSearchRadius(
            nextRadius,
            committed,
            typeNearAnchor || anyFloorNearAnchor,
          ),
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

  const myJourneys = createMyJourneys(() => showTravelHeatmap);

  const systems = $derived(
    viewState.hasResultsIntent ? (displayedResults?.results ?? []) : [],
  );
  // Which system is selected, and what the map shows of it, live in a file
  // of their own.
  const selection = createSelectedSystem({
    viewState: () => viewState,
    systems: () => systems,
    results,
    anchor: () => anchor,
    mapPhase: () => mapPhase,
    selectedValue: () => $selectedSystem.value,
  });
  const selectedId = $derived(selection.selectedId);
  const selectedMapSystem = $derived(selection.selectedMapSystem);
  const selectedMapName = $derived(selection.selectedMapName);
  const chosenSystem = $derived(selection.chosenSystem);
  const selectionBeingLookedUp = $derived(selection.selectionBeingLookedUp);
  const selectionCannotBeShown = $derived(selection.selectionCannotBeShown);
  const starsNearChosen = selection.starsNearChosen;
  const showing = $derived(selection.showing);
  const selectedStarPresentation = $derived(selection.selectedStarPresentation);

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

  // The box has already written the chosen name and closed its suggestions.
  function chooseSuggestion(hit: AutocompleteSystem): void {
    viewState.rankingNotice = '';
    viewState.mapInFront = false;
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

  function selectResult(system: ExploreSystem): void {
    // Kept with its position, so a system that is no result of Find (a star
    // picked on the map) can still be shown.
    viewState.retainedSelectedSystem = system;
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
    // At phone width the Finder covers the map: hide the panel. On a wider
    // screen the results are in front of the map: bring the map in front.
    // Either way, centre the map once it can be seen.
    if (window.matchMedia(PHONE_STAGE_QUERY).matches) {
      hideFinderPanel(event);
    } else if (!viewState.mapInFront) {
      bringMapInFront(event);
    } else if (alreadySelected) {
      galaxyMap?.centre();
      return;
    }
    if (alreadySelected) void tick().then(() => galaxyMap?.centre());
  }

  function bringMapInFront(event: MouseEvent): void {
    panelToggleOrigin = event.currentTarget as HTMLElement;
    if (resultList) viewState.captureResultScroll(resultList.scrollTop);
    viewState.mapInFront = true;
    void tick().then(() =>
      mapSection
        ?.querySelector<HTMLElement>('[data-results-show]')
        ?.focus({ preventScroll: true }),
    );
  }

  function showResults(): void {
    viewState.mapInFront = false;
    void tick().then(() => {
      // The results keep their scroll position, and focus returns to the
      // control that brought the map in front (or the list).
      if (resultList) viewState.restoreResultScroll(resultList);
      const target: { focus(options?: FocusOptions): void } | undefined =
        panelToggleOrigin?.isConnected ? panelToggleOrigin : resultList;
      target?.focus({ preventScroll: true });
    });
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
      // "Show Finder" is above whichever of the results and the map is in
      // front.
      (resultsInFront ? resultsSection : mapSection)
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
      const target: { focus(options?: FocusOptions): void } | undefined =
        panelToggleOrigin?.isConnected ? panelToggleOrigin : searchBox;
      target?.focus({
        preventScroll: !window.matchMedia(PHONE_STAGE_QUERY).matches,
      });
    });
  }

  function captureResultScroll(event: Event): void {
    if (!resultsInFront) return;
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
      bind:this={finderPanel}
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
        <button
          bind:this={savedTrigger}
          type="button"
          class="quiet-button"
          data-inspect-popout-trigger
          aria-haspopup="dialog"
          aria-expanded={savedOpen}
          aria-controls={savedOpen ? 'explore-saved-systems' : undefined}
          onclick={() => (savedOpen = !savedOpen)}
          >Saved systems ({$pins.value.length} pinned)</button
        >
        <button
          bind:this={compareTrigger}
          type="button"
          class="quiet-button"
          data-inspect-popout-trigger
          aria-haspopup="dialog"
          aria-expanded={compareOpen}
          aria-controls={compareOpen ? 'explore-compare-systems' : undefined}
          onclick={() => (compareOpen = !compareOpen)}
          >Compare ({$compare.value.length})</button
        >
      </nav>
      <InspectPopout
        id="explore-saved-systems"
        title="Saved systems"
        kind="saved"
        open={savedOpen}
        trigger={savedTrigger}
        beside={finderPanel}
        onclose={() => (savedOpen = false)}
      >
        <WatchlistPanel inPopout />
      </InspectPopout>
      <InspectPopout
        id="explore-compare-systems"
        title="Compare systems"
        kind="compare"
        open={compareOpen}
        trigger={compareTrigger}
        beside={finderPanel}
        onclose={() => (compareOpen = false)}
      >
        <ComparePanel inPopout />
      </InspectPopout>

      <section class="finder-bar" aria-labelledby="finder-title">
        <div>
          <p class="eyebrow">Discovery anchor</p>
          <h2 id="finder-title">Find systems near a known star</h2>
        </div>
        <SystemSearchBox
          bind:this={searchBox}
          bind:query={viewState.query}
          onchoose={chooseSuggestion}
          onemptied={() => {
            if (anchor) void clearAnchor();
          }}
          clearLabel={anchor ? `Stop searching near ${anchor.name}` : null}
          onclear={clearAnchor}
        />
      </section>

      {#snippet bodyFilterTools()}
        <BodyFilters
          value={viewState.bodyFilters}
          onapply={(value) => void commitBodyFilters(value)}
          onremove={removeBodyFilter}
          onclear={() => void commitBodyFilters({})}
        />
      {/snippet}

      {#if FINDER_F4_ENABLED}
        <ArchetypePicker
          archetype={viewState.rankingState.archetype}
          minimumTier={viewState.rankingState.minimumTier}
          anyFloor={anchor !== null}
          onArchetypeChange={(archetype) =>
            commitRanking({ archetype, minimumTier: null })}
          onMinimumTierChange={(minimumTier) => commitRanking({ minimumTier })}
          rankingTools={bodyFilterTools}
        />
      {:else}
        {@render bodyFilterTools()}
      {/if}

      {#if viewState.rankingNotice}
        <p role="status" class="ranking-status">{viewState.rankingNotice}</p>
      {/if}

      <div class="search-distance">
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
              : typeNearAnchor
                ? 'A colony type limits'
                : '"Show only" limits'} the search to {FILTERED_SEARCH_RADIUS_CAP_LY}
            LY{radiusCappedByFilters
              ? `; your ${viewState.radius.toLocaleString('en-US')} LY radius resumes when ${Object.keys(viewState.bodyFilters).length > 0 ? 'the filters are cleared' : typeNearAnchor ? 'Any is chosen again' : 'you show all systems'}`
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
            onchange={(event) => commitReach(Number(event.currentTarget.value))}
          >
            {#each REACH_OPTIONS as option (option)}
              <option value={option}>{option.toLocaleString('en-US')} LY</option
              >
            {/each}
          </select>
          from inhabited space
        </label>
        <p id="search-reach-hint" class="radius-hint">
          Every search keeps to systems this close to an inhabited system.
        </p>
      </div>
      {#if !viewState.hasResultsIntent}
        <p class="finder-prompt">
          Search for a system above to see what is near it.
        </p>
      {/if}
    </section>

    <section
      id="explore-results"
      class="results-panel"
      bind:this={resultsSection}
      hidden={!resultsInFront}
      aria-labelledby="results-title"
      aria-busy={viewState.hasResultsIntent &&
        (FINDER_F4_ENABLED ? updatingResults : results.isFetching)}
    >
      {#if !viewState.finderPanelOpen}
        <button
          type="button"
          class="quiet-button finder-panel-toggle results-finder-show"
          data-finder-panel-show
          aria-controls="finder-panel"
          aria-expanded="false"
          onclick={showFinderPanel}>Show Finder</button
        >
      {/if}
      <div class="panel-heading">
        <div>
          <p class="eyebrow">Catalogue results</p>
          <h2 id="results-title">
            {typeNearAnchor && anchor
              ? `${activeArchetypeLabel} near ${anchor.name}`
              : FINDER_F4_ENABLED && viewState.rankingState.archetype !== 'any'
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
            page={shownPage ?? displayedResults.page}
            archetype={displayedResults.finderState!.archetype}
            counting={countingMatches}
          />
        {:else if !FINDER_F4_ENABLED}<span>{systems.length} shown</span>{/if}
      </div>

      <div class="search-scope">
        <p id="results-scope">{resultsScope}</p>
        <button
          type="button"
          class="secondary-button pill-action"
          data-explore-map-open
          aria-controls="explore-map-window"
          disabled={selectedMapSystem !== null && !chosenSystem}
          onclick={(event) =>
            showMap(event, selectedMapSystem ?? anchorAsSystem)}
          >Show on map</button
        >
        {#if selectionCannotBeShown}
          <span>Map coordinates unavailable for the selected system.</span>
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
              {displayedResults.page.count === 1 ? 'result' : 'results'} on this page.</span
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
                ? "Colony type rankings are unavailable until Find's ranking data is published."
                : viewState.rankingState.archetype !== 'any'
                  ? 'Colony type rankings are temporarily unavailable. Try again shortly.'
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
              {viewState.rankingState.archetype !== 'any'
                ? `No systems meet minimum tier ${viewState.rankingState.minimumTier} for ${activeArchetypeLabel}.`
                : anyFloorNearAnchor
                  ? `No systems of tier ${viewState.rankingState.minimumTier} or better are this close.`
                  : 'No systems match this discovery area. Choose another anchor.'}
            </p>
            {#if anyFloorNearAnchor}
              <button
                type="button"
                onclick={() => commitRanking({ minimumTier: 'all' })}
                >Show all systems</button
              >
            {/if}
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
        <!-- Hidden: before a search the map is in front. -->
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
        {#if systems.length > 0}
          <div class="result-columns" aria-hidden="true">
            <span>
              <span>System</span>
              <span>Distance</span>
              {#if rankedList}
                <span
                  >{displayedResults!.finderState!.archetype === 'any'
                    ? 'Colony potential'
                    : `${archetypeLabel(displayedResults!.finderState!.archetype)} fit`}</span
                >
                <span>Suits</span>
                <span>Bodies and signals</span>
              {:else}
                <span class="result-facts">Economy and population</span>
              {/if}
            </span>
            <span></span>
          </div>
        {/if}
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
                  >
                  <span class="result-subline"
                    >{#if system.main_star_type}<span
                        >{starClassLabel(system.main_star_type)}</span
                      >{/if}{#if FINDER_F4_ENABLED && system.claim_status === 'claim_ended_unknown'}<span
                        class="claim-chip">Claim ended?</span
                      >{/if}</span
                  ></span
                >
                <span class="result-distance"
                  >{#if system.distance != null}{FINDER_F4_ENABLED
                      ? `${system.distance.toFixed(2)} LY`
                      : `${system.distance.toFixed(1)} ly`}{/if}</span
                >
                {#if rankedList}
                  <FinderResultLine
                    {system}
                    archetype={displayedResults!.finderState!.archetype}
                  />
                {:else}
                  <span class="result-facts">
                    {#if system.primaryEconomy}<span
                        >{system.primaryEconomy}</span
                      >{/if}
                    {#if population(system.population)}<span
                        >{population(system.population)} population</span
                      >{/if}
                  </span>
                {/if}
              </button>
              <div class="result-actions">
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
                <SystemActions {system} />
              </div>
              {#if selectedId === system.id64}
                <!-- The selected result's details, under its line. -->
                <div class="result-details">
                  {#if rankedList}
                    <FinderResultRanking
                      variant="row"
                      {system}
                      archetype={displayedResults!.finderState!.archetype}
                    />
                  {/if}
                  <div class="result-detail-actions">
                    <button
                      type="button"
                      class="result-map-action secondary-button pill-action"
                      data-result-map-open
                      aria-controls="explore-map-window"
                      disabled={!chosenSystem}
                      onclick={(event) => showMap(event, system)}
                      >Show on map</button
                    >
                    {#if rankedList}
                      <ArchetypeExplanation
                        {system}
                        expected={system.ranking_provenance ?? null}
                        archetype={displayedResults!.finderState!.archetype}
                        headingId={`finder-result-heading-${system.id64}`}
                      />
                    {/if}
                  </div>
                </div>
              {/if}
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
              displayedResults.page.offset + displayedResults.page.page_size >=
                (shownPage ?? displayedResults.page).navigable_total}
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
        </nav>
      {/if}
    </section>

    <ExploreMapWindow
      bind:element={mapSection}
      selectedSystemName={selectedMapName}
      finderPanelOpen={viewState.finderPanelOpen}
      onShowFinder={showFinderPanel}
      inFront={mapInFront}
      onShowResults={resultsBehindMap ? showResults : undefined}
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
              data-commander-history-count={myJourneys.state === 'ready'
                ? myJourneys.count
                : 0}
              data-commander-history-state={myJourneys.state}
            >
              {#if myJourneys.state === 'checking'}
                Checking your sign-in…
              {:else if myJourneys.state === 'auth-error'}
                Account check unavailable · <button
                  type="button"
                  onclick={() => void auth.bootstrap()}>Retry</button
                >
              {:else if myJourneys.state === 'signed-out'}
                <a href={resolve('/account')}
                  >Sign in and import your journals</a
                >
              {:else if myJourneys.state === 'summary-error'}
                Journal import status unavailable · <button
                  type="button"
                  onclick={() => myJourneys.retrySummary()}>Retry</button
                >
              {:else if myJourneys.state === 'off'}
                My journeys hidden
              {:else if myJourneys.state === 'no-journals'}
                No imported journals yet
              {:else if myJourneys.state === 'loading'}
                Reading imported journal visits…
              {:else if myJourneys.state === 'error'}
                My journeys unavailable · <button
                  type="button"
                  onclick={() => myJourneys.retryVisits()}>Retry</button
                >
              {:else if myJourneys.state === 'empty'}
                No travel in your imported journals yet
              {:else}
                {myJourneys.count.toLocaleString()} visited systems{myJourneys.truncated
                  ? ' · your most recent'
                  : ''}{myJourneys.haveLines
                  ? ` · ${JOURNEY_LINES_IN_SHORT}`
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
            {:else if selectionCannotBeShown}
              Map coordinates unavailable for the selected system.
            {:else if selectionBeingLookedUp}
              Looking up the selected system…
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
        journeys={myJourneys.journeys}
        resume={untrack(
          () =>
            viewState.mapResume ??
            mapLeftAtFor(selectedId ? String(selectedId) : null),
        )}
        onScreen={mapOnScreen}
        onLeave={(resume) => {
          viewState.mapResume = resume;
          rememberMapLeftAt(resume);
        }}
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
    gap: 0.4rem 0.6rem;
    margin: 0;
    font-size: 0.78rem;
  }
  .finder-panel .shortlist-nav button {
    padding: 0.25rem 0.7rem;
    font: inherit;
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
  .finder-panel :global(.panel-heading) {
    min-height: 0;
    padding: 0.6rem 0.75rem;
  }
  /* How far to search, under the other search controls. */
  .search-distance {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.4rem 1rem;
    color: var(--text-muted);
    font-size: 0.8rem;
    line-height: 1.5;
  }
  .search-distance .radius-hint {
    margin: 0;
  }
  .finder-prompt {
    margin: 0;
    color: var(--text-muted);
    font-size: 0.85rem;
  }
  /* Find's results and the map take turns beside the Finder panel. Both sit
     in the same place, so behind the results the map keeps its size and does
     not resize or fetch again when it comes back in front. */
  .explore-stage > .results-panel,
  .explore-stage > :global(.explore-map-window) {
    grid-column: 2;
    grid-row: 1;
    min-height: 0;
  }
  .explore-stage.is-finder-hidden > .results-panel,
  .explore-stage.is-finder-hidden > :global(.explore-map-window) {
    grid-column: 1;
  }
  .explore-stage > :global(.explore-map-window[data-map-in-front='false']) {
    visibility: hidden;
  }
  /* The results fill their place and scroll on their own, so their heading
     and the page buttons stay in view. */
  .explore-stage > .results-panel {
    z-index: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .explore-stage > .results-panel[hidden] {
    display: none;
  }
  .results-panel .result-list {
    flex: 1 1 0;
    min-height: 0;
    max-height: none;
  }
  .results-finder-show {
    align-self: flex-start;
    margin: 0.6rem 0.75rem 0;
  }
  .result-map-action {
    margin: 0;
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
    /* The results follow the Finder's controls down the page. */
    .explore-stage > .results-panel {
      margin-top: 0.5rem;
      overflow: visible;
    }
    .results-panel .result-list {
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
  .result-paging {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem;
  }
  /* Results per page, at the end of the Previous / Next line. */
  .page-size {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    margin-left: auto;
    color: var(--text-muted);
    font-size: 0.85rem;
  }
  .page-size input {
    width: 4.5rem;
    padding: 0.35rem 0.5rem;
  }
  .results-panel .panel-heading {
    flex-wrap: wrap;
    align-items: center;
    gap: 0.65rem 1.5rem;
  }
  /* One result per line. The line is the result's select button, laid out
     in the same columns as the headings above the list; its actions follow
     at the end of the line, and the selected result's details span the
     whole width under it. Narrow, the line stacks. */
  .results-panel {
    container-type: inline-size;
  }
  .result-list li,
  .result-columns {
    display: grid;
    grid-template-columns: minmax(0, 1fr) var(--result-actions-width);
    align-items: center;
    gap: 0 0.5rem;
  }
  .result-select,
  .result-columns > :first-child {
    display: grid;
    grid-template-columns:
      minmax(9rem, 2.2fr) 5.5rem 8.5rem minmax(8rem, 2fr)
      minmax(8rem, 2fr);
    align-items: center;
    gap: 0.25rem 1rem;
    min-width: 0;
    padding: 0.55rem 0.25rem 0.55rem 1rem;
  }
  .result-select {
    width: 100%;
  }
  .results-panel {
    --result-actions-width: 21rem;
  }
  .result-columns {
    flex-shrink: 0;
    overflow: hidden;
    scrollbar-gutter: stable;
    border-bottom: 1px solid var(--line);
    color: var(--text-muted);
    font-size: 0.72rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .result-columns > :first-child {
    padding-block: 0.4rem;
  }
  /* Room for the list: a compact heading and scope line above it. */
  .results-panel .panel-heading {
    min-height: 0;
    padding: 0.6rem 1rem;
  }
  .results-panel .search-scope {
    padding: 0.4rem 1rem;
  }
  .results-panel .ranking-status {
    margin: 0;
    padding-block: 0.3rem;
    font-size: 0.8rem;
  }
  .results-panel .ranking-status :global(p) {
    margin: 0;
  }
  .results-panel .result-list {
    scrollbar-gutter: stable;
  }
  .result-title {
    gap: 0.1rem;
    min-width: 0;
  }
  .result-title strong {
    font-size: 0.95rem;
  }
  .result-subline {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.15rem 0.5rem;
    color: var(--text-muted);
    font-size: 0.78rem;
  }
  .claim-chip {
    padding: 0 0.4rem;
    border: 1px solid currentColor;
    border-radius: var(--radius-pill);
    color: var(--warn);
    font-size: 0.72rem;
    white-space: nowrap;
  }
  .result-distance {
    font-size: 0.88rem;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .result-facts {
    grid-column: 3 / -1;
    margin: 0;
    font-size: 0.85rem;
  }
  .result-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: 0.35rem;
    padding: 0.35rem 0.75rem 0.35rem 0;
  }
  .result-list .inspect-link {
    min-width: 0;
    height: auto;
    margin: 0;
    padding: 0.3rem 0.7rem;
    font-size: 0.8rem;
  }
  .result-details {
    grid-column: 1 / -1;
    display: grid;
    gap: 0.5rem;
    padding: 0 1rem 0.85rem;
  }
  .result-details :global(.ranking-facts) {
    margin: 0;
  }
  .result-detail-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    gap: 0.5rem;
  }
  /* Not wide enough for the buttons at the end of the line: they go under
     it, on the right, and the columns keep their line. */
  @container (max-width: 66rem) {
    .result-list li,
    .result-columns {
      grid-template-columns: minmax(0, 1fr);
    }
    .result-columns > :last-child {
      display: none;
    }
    .result-select {
      padding-bottom: 0.15rem;
    }
    .result-actions {
      padding: 0 0.75rem 0.45rem 1rem;
    }
  }
  /* Narrow: the line stacks. */
  @container (max-width: 44rem) {
    .result-columns {
      display: none;
    }
    .result-select {
      grid-template-columns: minmax(0, 1fr) auto;
      padding-right: 1rem;
    }
    .result-select > :global(.line-types),
    .result-select > :global(.line-bodies),
    .result-facts {
      grid-column: 1 / -1;
    }
    .result-actions {
      justify-content: flex-start;
      padding: 0 1rem 0.6rem;
    }
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
</style>
