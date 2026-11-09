import type { QueryKey } from '@tanstack/svelte-query';
import {
  SOL_ID64,
  type CatalogueViewportResponse,
  type ExploreSystem,
  type MapHeatmapResponse,
} from '$lib/api/client';
import { parseId64, type Id64 } from '$lib/domain/id64';
import type { PersistenceContext } from '$lib/persistence/context';
import { createMapLayerPreferences } from '$lib/persistence/map-layers';
import type {
  CameraState,
  GalaxySystemPoint,
  RenderedLayerReceipt,
  SpatialContribution,
  SpatialTarget,
} from '$lib/spatial/contracts';
import type { GalaxyCameraViewport } from '$lib/spatial/galaxy-camera';
import type { GalaxyDetailMode } from '$lib/spatial/galaxy-detail-policy';
import type { GalaxyDensityResult } from '$lib/spatial/galaxy-density-source';
import type { GalaxyNebulaePayload } from '$lib/spatial/galaxy-nebulae';
import type { GalaxyRegionsPayload } from '$lib/spatial/galaxy-regions';
import type { GalaxyStarViewport } from '$lib/spatial/galaxy-star-stream';
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
 * Owns view intent and immutable CPU-side map snapshots. Query observers and
 * renderer resources stay in the workspace/window. There are no effects, subscriptions or
 * timers here. URL inputs remain a projection of the router (including Back),
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
  selectionRevision = $state(0);
  focusRevision = $state(0);
  completedFocusRevision = 0;
  focusTarget = $state<SpatialTarget | null>(null);
  lastPickedId64 = $state<Id64 | null>(null);
  selectedRegionId = $state<string | null>(null);
  hoveredRegionId = $state<string | null>(null);
  hoveredSystemId = $state<string | null>(null);
  cameraRequest = $state<CameraState | null>(null);
  cameraRequestRevision = $state(0);
  appliedCameraRequestRevision = 0;
  retainedCamera = $state<{
    finderRevision: number;
    camera: CameraState;
  } | null>(null);
  initialGalaxyViewRequested = false;
  streamCamera = $state<CameraState | null>({
    focusLy: { x: 0, y: 0, z: 0 },
    distanceLy: 118_000,
    bearingRad: 0,
    pitchRad: 0.55,
    projection: 'perspective',
    revision: 0,
  });
  streamRevision = $state(0);
  starDetailMode = $state<GalaxyDetailMode>('wide');
  starViewportSize = $state<GalaxyCameraViewport | null>(null);
  layerToggleRevision = $state(0);
  showNebulae = $state(true);
  // The map is always on screen. Whether the Finder panel beside it is shown
  // is route-local presentation, never URL or storage state.
  finderPanelOpen = $state(true);
  mapCameraPreset = $state<'local' | 'galaxy' | null>(null);
  mapFocusedSystemId = $state<string | null>(null);
  mapResetCamera = $state<CameraState | null>(null);
  regions: GalaxyRegionsPayload | null = $state.raw(null);
  regionContribution: SpatialContribution | null = $state.raw(null);
  regionState = $state<'loading' | 'ready' | 'failed'>('loading');
  regionResourceRevision = $state(0);
  nebulae: GalaxyNebulaePayload | null = $state.raw(null);
  nebulaState = $state<'loading' | 'ready' | 'failed'>('loading');
  nebulaResourceRevision = $state(0);
  renderedNebulaReceipt: RenderedLayerReceipt | null = $state.raw(null);
  nebulaCameraDistanceLy = $state<number | null>(null);
  reservedPoints: readonly GalaxySystemPoint[] = $state.raw([]);
  appliedCataloguePacket: CatalogueViewportResponse | null = $state.raw(null);
  appliedCatalogueViewport: GalaxyStarViewport | null = $state.raw(null);
  appliedCatalogueRevision = $state(0);
  appliedFootprintKey = '';
  lastGoodDensity: Extract<GalaxyDensityResult, { kind: 'ready' }> | null =
    $state.raw(null);
  densityContribution: SpatialContribution | null = $state.raw(null);
  appliedDensityData: MapHeatmapResponse | undefined;
  densityRevision = $state(0);
  densityRemovalRevision = $state(0);
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
      { near: this.nearId, bodyFilters: this.bodyFilters, radius: this.radius },
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
