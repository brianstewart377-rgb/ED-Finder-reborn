import type { QueryKey } from '@tanstack/svelte-query';
import { SOL_ID64, type ExploreSystem } from '$lib/api/client';
import { parseId64, type Id64 } from '$lib/domain/id64';
import type { MapResume } from '$lib/galaxy-map/GalaxyMap.svelte';
import type { PersistenceContext } from '$lib/persistence/context';
import { createMapLayerPreferences } from '$lib/persistence/map-layers';
import { readExploreAnchor, type ExploreAnchor } from './navigation';
import {
  DEFAULT_RANKING_STATE,
  hasRankingSearchParams,
  parseRankingSearchParams,
  serializeRankingSearchParams,
  stripRankingSearchParams,
} from './ranking-state';
import {
  parseExploreSearchParams,
  serializeExploreSearchParams,
} from './search-state';

type ExploreLocation = Readonly<{ url: URL; state: App.PageState }>;
type ExploreSelections = Pick<
  PersistenceContext,
  'selectedSystem' | 'pins' | 'compare'
>;
export const MAX_RESULT_SCROLL_TOP = 10_000_000;

/**
 * One instance per Explore route visit, supplied to every layout of that visit.
 * Owns view intent. Query observers and the map itself stay in the
 * workspace/window. There are no effects, subscriptions or timers here. URL inputs remain a projection of the router (including Back),
 * rather than a second writable copy that can dispatch another search.
 */
export class ExploreViewState {
  constructor(
    private readonly readLocation: () => ExploreLocation,
    readonly finderEnabled: boolean,
    selections: ExploreSelections,
  ) {
    this.selectedSystem = selections.selectedSystem;
    this.pins = selections.pins;
    this.compare = selections.compare;
    // Hydrate once, before observers: a saved OFF must not issue an ON request.
    this.layerPreferences.hydrate();
  }

  // Reuse the existing persistence services/codecs and storage keys. Saved-list
  // data still belongs to its query; these are only the existing selections.
  readonly selectedSystem: ExploreSelections['selectedSystem'];
  readonly pins: ExploreSelections['pins'];
  readonly compare: ExploreSelections['compare'];
  readonly layerPreferences = createMapLayerPreferences();

  query = $state('');
  rankingNotice = $state('');
  resolvedAnchor = $state<ExploreAnchor | null>(null);
  anchorSelectionRevision = $state(0);
  focusedAnchorRevision = 0;
  hydratedAnchorIdentity: string | undefined;
  hydratedExploreUrl: string | undefined;
  // The map is always on screen. Whether the Finder panel beside it is shown
  // is route-local presentation, never URL or storage state.
  finderPanelOpen = $state(true);
  // Where the galaxy map's camera was when the page last left it. Not page
  // state: nothing is drawn from it.
  mapResume: MapResume | null = null;
  // A picked selection is immutable too; never proxy a retained API row.
  retainedSelectedSystem = $state.raw<ExploreSystem | null>(null);
  resultScrollTop = $state(0);
  hasMounted = false;
  // Retain only the displayed response identity; its immutable data stays in
  // TanStack's cache, including a prior page shown during a failed request.
  displayedResultsKey = $state.raw<QueryKey | null>(null);

  readonly parsedSearch = $derived.by(() =>
    parseExploreSearchParams(this.readLocation().url.searchParams),
  );
  readonly historyAnchor = $derived.by(() =>
    readExploreAnchor(this.readLocation().state),
  );
  // Explicit URL input wins over old history, even when malformed.
  readonly nearId = $derived.by(
    () =>
      this.parsedSearch.near ??
      (!this.readLocation().url.searchParams.has('near')
        ? (this.historyAnchor?.id64 ?? null)
        : null),
  );
  readonly bodyFilters = $derived(this.parsedSearch.bodyFilters);
  readonly radius = $derived(this.parsedSearch.radius);
  readonly reach = $derived(this.parsedSearch.reach);
  readonly parsedRanking = $derived.by(() =>
    this.finderEnabled
      ? parseRankingSearchParams(this.readLocation().url.searchParams)
      : null,
  );
  readonly rankingState = $derived.by(
    () => this.parsedRanking?.state ?? DEFAULT_RANKING_STATE,
  );
  readonly urlSelection = $derived.by((): Id64 | null => {
    if (!this.finderEnabled) return null;
    const values = this.readLocation().url.searchParams.getAll('selected');
    if (values.length !== 1) return null;
    try {
      return parseId64(values[0]);
    } catch {
      return null;
    }
  });
  readonly rejectedNear = $derived.by(
    () =>
      this.readLocation().url.searchParams.has('near') &&
      this.parsedSearch.near === null,
  );
  readonly canonicalSearch = $derived.by(() => {
    const serialized = this.serialize().toString();
    return serialized ? `?${serialized}` : '';
  });
  readonly invalidAnchorState = $derived.by(
    () =>
      this.readLocation().state?.anchor != null && this.historyAnchor === null,
  );
  readonly rankingUrlReady = $derived.by(
    () =>
      this.readLocation().url.search === this.canonicalSearch &&
      !this.invalidAnchorState,
  );
  /** Submitted search intent already lives in the URL/history, not the draft. */
  readonly hasResultsIntent = $derived.by(
    () =>
      this.nearId !== null ||
      (this.finderEnabled &&
        (this.rankingState.archetype !== 'any' ||
          hasRankingSearchParams(this.readLocation().url.searchParams) ||
          this.readLocation().state.finderRequested === true)),
  );

  /** Resolve view coordinates without retaining the query or its detail payload. */
  resolveAnchor(
    detail:
      | {
          id64: Id64;
          name?: string;
          x?: number | null;
          y?: number | null;
          z?: number | null;
        }
      | undefined,
  ): ExploreAnchor | null {
    if (!this.nearId) return null;
    if (this.historyAnchor?.id64 === this.nearId) return this.historyAnchor;
    if (!detail)
      return this.resolvedAnchor?.id64 === this.nearId
        ? this.resolvedAnchor
        : null;
    if (detail.id64 !== this.nearId) return null;
    if (
      detail.id64 !== SOL_ID64 &&
      detail.x === 0 &&
      detail.y === 0 &&
      detail.z === 0
    )
      return null;
    return readExploreAnchor({ anchor: detail });
  }

  captureResultScroll(value: number): void {
    if (Number.isFinite(value) && value >= 0 && value <= MAX_RESULT_SCROLL_TOP)
      this.resultScrollTop = value;
  }

  restoreResultScroll(list: { scrollTop: number }): void {
    list.scrollTop = this.resultScrollTop;
  }

  /** Only the already-persisted URL subset; no camera/draft/new storage keys. */
  serialize(): URLSearchParams {
    let params = serializeExploreSearchParams(
      {
        near: this.nearId,
        bodyFilters: this.bodyFilters,
        radius: this.radius,
        reach: this.reach,
      },
      this.readLocation().url.searchParams,
    );
    if (this.finderEnabled) {
      params.delete('selected');
      if (this.urlSelection) params.set('selected', this.urlSelection);
      params =
        this.rejectedNear && this.rankingState.archetype === 'any'
          ? stripRankingSearchParams(params)
          : serializeRankingSearchParams(this.rankingState, params);
    } else params = stripRankingSearchParams(params);
    return params;
  }

  /**
   * Decode the old URL/history representation using its existing bounds.
   * The caller commits the returned location through the router; restoring
   * never navigates, submits a search or installs another observer itself.
   */
  restore(
    params: URLSearchParams,
    history: unknown = {},
  ): { search: URLSearchParams; state: App.PageState } {
    const parsed = parseExploreSearchParams(params);
    const anchor = readExploreAnchor(history);
    const near =
      parsed.near ?? (!params.has('near') ? (anchor?.id64 ?? null) : null);
    const ranking = this.finderEnabled
      ? parseRankingSearchParams(params).state
      : DEFAULT_RANKING_STATE;
    const rejectedNear = params.has('near') && parsed.near === null;
    let search = serializeExploreSearchParams({ ...parsed, near }, params);
    if (this.finderEnabled) {
      const selected = params.getAll('selected');
      search.delete('selected');
      if (selected.length === 1) {
        try {
          search.set('selected', parseId64(selected[0]));
        } catch {
          /* Invalid passive selection is omitted. */
        }
      }
      search =
        rejectedNear && ranking.archetype === 'any'
          ? stripRankingSearchParams(search)
          : serializeRankingSearchParams(ranking, search);
    } else search = stripRankingSearchParams(search);
    return {
      search,
      state: {
        anchor: rejectedNear ? null : anchor,
        finderRequested:
          this.finderEnabled &&
          !(rejectedNear && ranking.archetype === 'any') &&
          (hasRankingSearchParams(params) ||
            (typeof history === 'object' &&
              history !== null &&
              'finderRequested' in history &&
              history.finderRequested === true)),
      },
    };
  }
}
