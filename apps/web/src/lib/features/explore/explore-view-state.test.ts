import { fromStore, get, writable } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  SOL_ID64,
  type ExploreSystem,
  type SystemDetail,
} from '$lib/api/client';
import { parseId64, type Id64 } from '$lib/domain/id64';
import type { PersistenceContext } from '$lib/persistence/context';
import { legacyPersistenceFixtures } from '$lib/persistence/fixtures';
import {
  PERSISTENCE_KEYS,
  compareCodec,
  createPersistedStore,
  id64StringCodec,
  nullableCodec,
  pinnedCodec,
  type CompareEntry,
  type PinnedEntry,
} from '$lib/persistence/storage';
import type { CameraState } from '$lib/spatial/contracts';
import { ExploreViewState } from './explore-view-state.svelte';
import type { ExploreAnchor } from './navigation';
import { DEFAULT_RANKING_STATE } from './ranking-state';
import { MAX_BODIES_PARAMETER_LENGTH } from './search-state';

vi.mock('$app/state', () => ({ page: { state: {} } }));
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

type Page = { url: URL; state: App.PageState };
type Selections = Pick<
  PersistenceContext,
  'selectedSystem' | 'pins' | 'compare'
>;

function selections(): Selections {
  return {
    selectedSystem: createPersistedStore<Id64 | null>({
      key: PERSISTENCE_KEYS.selectedSystem,
      initial: () => null,
      codec: nullableCodec(id64StringCodec),
    }),
    pins: createPersistedStore<PinnedEntry[]>({
      key: PERSISTENCE_KEYS.pins,
      initial: () => [],
      codec: pinnedCodec,
    }),
    compare: createPersistedStore<CompareEntry[]>({
      key: PERSISTENCE_KEYS.compare,
      initial: () => [],
      codec: compareCodec,
    }),
  };
}

function createView(search = '', state: App.PageState = {}, enabled = true) {
  const page: Page = {
    url: new URL(`http://localhost/explore${search}`),
    state,
  };
  const persistence = selections();
  return {
    page,
    persistence,
    view: new ExploreViewState(() => page, enabled, persistence),
  };
}

const anchor: ExploreAnchor = {
  id64: parseId64('9007199254740993'),
  name: 'Retained anchor',
  x: 12,
  y: -3,
  z: 40,
};
const camera: CameraState = {
  focusLy: { x: 110, y: -22, z: 330 },
  distanceLy: 4_000,
  bearingRad: 0.3,
  pitchRad: 0.8,
  projection: 'perspective',
  revision: 7,
};
const detail: SystemDetail = {
  ...anchor,
  lifecycleState: 'ACTIVE',
  sourceBodyCount: null,
  loadedBodyCount: 0,
  sourceUpdatedAt: null,
  bodies: [],
  stations: [],
};

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe('Explore view lifetime', () => {
  it('starts a route visit without a submitted search or inherited view context', () => {
    const { view, persistence } = createView();
    expect(view).toMatchObject({
      query: '',
      rankingNotice: '',
      resolvedAnchor: null,
      anchorSelectionRevision: 0,
      focusedAnchorRevision: 0,
      hydratedAnchorIdentity: undefined,
      hydratedExploreUrl: undefined,
      selectionRevision: 0,
      focusRevision: 0,
      completedFocusRevision: 0,
      focusTarget: null,
      lastPickedId64: null,
      selectedRegionId: null,
      hoveredRegionId: null,
      hoveredSystemId: null,
      cameraRequest: null,
      cameraRequestRevision: 0,
      appliedCameraRequestRevision: 0,
      retainedCamera: null,
      streamRevision: 0,
      starDetailMode: 'wide',
      starViewportSize: null,
      layerToggleRevision: 0,
      showNebulae: true,
      finderPanelOpen: true,
      mapFocusedSystemId: null,
      reservedPoints: [],
      renderedNebulaReceipt: null,
      appliedCataloguePacket: null,
      lastGoodDensity: null,
      retainedSelectedSystem: null,
      displayedResultsKey: null,
      initialGalaxyViewRequested: false,
      hasMounted: false,
      resultScrollTop: 0,
      nearId: null,
      historyAnchor: null,
      radius: 500,
      bodyFilters: {},
      rankingState: DEFAULT_RANKING_STATE,
      urlSelection: null,
      hasResultsIntent: false,
      rejectedNear: false,
      canonicalSearch: '',
      invalidAnchorState: false,
      rankingUrlReady: true,
    });
    expect(view.streamCamera).toEqual({
      focusLy: { x: 0, y: 0, z: 0 },
      distanceLy: 118_000,
      bearingRad: 0,
      pitchRad: 0.55,
      projection: 'perspective',
      revision: 0,
    });
    expect(get(view.layerPreferences).value).toEqual({
      density: true,
      journeys: false,
    });
    expect(view.selectedSystem).toBe(persistence.selectedSystem);
    expect(view.pins).toBe(persistence.pins);
    expect(view.compare).toBe(persistence.compare);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it('retains all mutable view fields without leaking them into another route visit', () => {
    const { view } = createView();
    const other = createView().view;
    const selected = {
      id64: anchor.id64,
      name: anchor.name,
      coords: { x: anchor.x, y: anchor.y, z: anchor.z },
    } as ExploreSystem;
    const displayedResultsKey = [
      'ed-finder',
      'explore',
      { reference_coords: { x: anchor.x, y: anchor.y, z: anchor.z } },
    ];
    const changed = {
      query: 'Unsubmitted query',
      rankingNotice: 'Unsupported link option',
      resolvedAnchor: anchor,
      anchorSelectionRevision: 4,
      focusedAnchorRevision: 3,
      hydratedAnchorIdentity: anchor.id64,
      hydratedExploreUrl: '?near=9007199254740993',
      selectionRevision: 8,
      focusRevision: 9,
      completedFocusRevision: 8,
      focusTarget: { kind: 'system' as const, systemId64: anchor.id64 },
      lastPickedId64: anchor.id64,
      selectedRegionId: '18',
      hoveredRegionId: '19',
      hoveredSystemId: '42',
      cameraRequest: camera,
      cameraRequestRevision: 10,
      appliedCameraRequestRevision: 9,
      retainedCamera: { finderRevision: 4, camera },
      streamCamera: camera,
      streamRevision: 11,
      starDetailMode: 'local' as const,
      starViewportSize: { width: 1200, height: 800 },
      layerToggleRevision: 12,
      showNebulae: false,
      finderPanelOpen: false,
      mapFocusedSystemId: anchor.id64,
      reservedPoints: [
        {
          systemId64: anchor.id64,
          name: anchor.name,
          positionLy: camera.focusLy,
        },
      ],
      retainedSelectedSystem: selected,
      displayedResultsKey,
      initialGalaxyViewRequested: true,
      hasMounted: true,
    };
    Object.assign(view, changed);
    view.captureResultScroll(325.5);
    expect(view).toMatchObject(changed);
    expect(view.retainedSelectedSystem).toBe(selected);
    expect(view.retainedSelectedSystem?.coords).toBe(selected.coords);
    expect(view.displayedResultsKey).toBe(displayedResultsKey);
    expect(view.displayedResultsKey?.[2]).toBe(displayedResultsKey[2]);
    expect(view.resultScrollTop).toBe(325.5);
    expect(view.hasResultsIntent).toBe(false);
    expect(other).toMatchObject({
      query: '',
      resolvedAnchor: null,
      lastPickedId64: null,
      focusTarget: null,
      retainedCamera: null,
      retainedSelectedSystem: null,
      displayedResultsKey: null,
      completedFocusRevision: 0,
      appliedCameraRequestRevision: 0,
      showNebulae: true,
      finderPanelOpen: true,
      reservedPoints: [],
      resultScrollTop: 0,
      initialGalaxyViewRequested: false,
    });
    expect(other.layerPreferences).not.toBe(view.layerPreferences);
    // Panel visibility and renderer receipts never become URL/storage keys.
    const beforeToggle = view.serialize().toString();
    view.finderPanelOpen = true;
    expect(view.serialize().toString()).toBe(beforeToggle);
    view.finderPanelOpen = false;
    expect(view.serialize().toString()).toBe(beforeToggle);
    expect(view.retainedCamera?.camera).toEqual(camera);
    expect(view.reservedPoints).toEqual(changed.reservedPoints);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it('restores the scroll of a replacement result list, including both bounds', () => {
    const { view } = createView();
    for (const scrollTop of [0, 0.5, 420, 10_000_000]) {
      view.captureResultScroll(scrollTop);
      const replacement = { scrollTop: -1 };
      view.restoreResultScroll(replacement);
      expect(replacement.scrollTop).toBe(scrollTop);
      expect(view.resultScrollTop).toBe(scrollTop);
    }
  });

  it.each([-1, 10_000_001, Number.NaN, Infinity, -Infinity])(
    'rejects an invalid result scroll without discarding the last usable position: %s',
    (invalid) => {
      const { view } = createView();
      view.captureResultScroll(420);
      view.captureResultScroll(invalid);
      const replacement = { scrollTop: 0 };
      view.restoreResultScroll(replacement);
      expect(replacement.scrollTop).toBe(420);
    },
  );
});

describe('Explore shareable view state', () => {
  it('follows router changes without submitting or serializing the draft query', () => {
    const location = writable<Page>({
      url: new URL('http://localhost/explore'),
      state: {},
    });
    const reactiveLocation = fromStore(location);
    const view = new ExploreViewState(
      () => reactiveLocation.current,
      true,
      selections(),
    );
    view.query = 'Unsubmitted name';
    expect(view.hasResultsIntent).toBe(false);
    expect(view.serialize().toString()).toBe('');
    location.set({
      url: new URL(
        'http://localhost/explore?near=42&radius=100&bodies=elw_count:1-',
      ),
      state: {},
    });
    expect(view.nearId).toBe('42');
    expect(view.radius).toBe(100);
    expect(view.bodyFilters).toEqual({ elw_count: { min: 1 } });
    expect(view.hasResultsIntent).toBe(true);
    location.set({ url: new URL('http://localhost/explore'), state: {} });
    expect(view.nearId).toBeNull();
    expect(view.bodyFilters).toEqual({});
    expect(view.hasResultsIntent).toBe(false);
    expect(view.query).toBe('Unsubmitted name');
  });

  it('round-trips existing search, radius, filters, ranking and passive selection URLs', () => {
    const { view } = createView(
      '?near=9007199254740993&radius=1000&bodies=elw_count:1-,ring_count:-3&archetype=flexible&min-tier=A&page_size=24&offset=48&selected=42&system=99&extra=keep',
      { anchor, finderRequested: true },
    );
    expect(view).toMatchObject({
      nearId: anchor.id64,
      historyAnchor: anchor,
      radius: 1000,
      bodyFilters: { elw_count: { min: 1 }, ring_count: { max: 3 } },
      rankingState: {
        archetype: 'flexible',
        minimumTier: 'A',
        pageSize: 24,
        offset: 48,
        weights: {},
      },
      urlSelection: '42',
      hasResultsIntent: true,
    });
    const encoded = view.serialize();
    expect(encoded.get('system')).toBe('99');
    expect(encoded.get('extra')).toBe('keep');
    const restored = view.restore(encoded, { anchor, finderRequested: true });
    const next = createView(`?${restored.search}`, restored.state).view;
    expect(next.serialize().toString()).toBe(encoded.toString());
    expect(next.rankingState).toEqual(view.rankingState);
    expect(next.bodyFilters).toEqual(view.bodyFilters);
    expect(next.historyAnchor).toEqual(anchor);
    expect(next.hasResultsIntent).toBe(true);
    expect(next.rankingUrlReady).toBe(true);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it('reads old history-only anchors and retains explicit Any-search intent', () => {
    const { view } = createView('', { anchor });
    expect(view.nearId).toBe(anchor.id64);
    expect(view.resolveAnchor(undefined)).toEqual(anchor);
    expect(view.serialize().get('near')).toBe(anchor.id64);
    expect(view.hasResultsIntent).toBe(true);
    expect(
      createView('', { finderRequested: true }).view.hasResultsIntent,
    ).toBe(true);
    expect(
      createView('', { finderRequested: false }).view.hasResultsIntent,
    ).toBe(false);
  });

  it('requires a matching, finite detail anchor and rejects the unknown legacy origin', () => {
    const { view } = createView(`?near=${anchor.id64}`);
    expect(view.resolveAnchor(undefined)).toBeNull();
    expect(view.resolveAnchor({ ...detail, id64: parseId64('42') })).toBeNull();
    expect(view.resolveAnchor({ ...detail, x: Number.NaN })).toBeNull();
    expect(view.resolveAnchor({ ...detail, y: Infinity })).toBeNull();
    expect(view.resolveAnchor({ ...detail, x: 0, y: 0, z: 0 })).toBeNull();
    expect(view.resolveAnchor(detail)).toEqual(anchor);
    const sol = createView(`?near=${SOL_ID64}`).view;
    expect(
      sol.resolveAnchor({ ...detail, id64: SOL_ID64, x: 0, y: 0, z: 0 }),
    ).toMatchObject({ id64: SOL_ID64, x: 0, y: 0, z: 0 });
  });

  it('retains validated anchor coordinates only while the URL still names the same anchor', () => {
    const location = writable<Page>({
      url: new URL(`http://localhost/explore?near=${anchor.id64}`),
      state: {},
    });
    const reactiveLocation = fromStore(location);
    const view = new ExploreViewState(
      () => reactiveLocation.current,
      true,
      selections(),
    );
    view.resolvedAnchor = view.resolveAnchor(detail);
    expect(view.resolveAnchor(undefined)).toEqual(anchor);
    expect(view.resolvedAnchor).not.toHaveProperty('bodies');
    expect(view.resolvedAnchor).not.toHaveProperty('stations');

    // Fresh invalid details cannot fall back to earlier successful coordinates.
    expect(view.resolveAnchor({ ...detail, x: Infinity })).toBeNull();
    expect(view.resolveAnchor({ ...detail, x: 0, y: 0, z: 0 })).toBeNull();
    expect(view.resolveAnchor({ ...detail, id64: parseId64('42') })).toBeNull();

    location.set({
      url: new URL('http://localhost/explore?near=42'),
      state: { anchor },
    });
    expect(view.resolveAnchor(undefined)).toBeNull();
    location.set({
      url: new URL('http://localhost/explore?near=invalid'),
      state: { anchor },
    });
    expect(view.nearId).toBeNull();
    expect(view.resolveAnchor(undefined)).toBeNull();
    expect(view.resolveAnchor(detail)).toBeNull();
    location.set({ url: new URL('http://localhost/explore'), state: {} });
    expect(view.resolveAnchor(undefined)).toBeNull();
  });

  it.each(['near=bad', 'near=1&near=2', 'near=18446744073709551616'])(
    'never revives an old history anchor or pagination after a rejected URL: %s',
    (query) => {
      const { view } = createView(`?${query}&offset=48`, { anchor });
      expect(view.nearId).toBeNull();
      expect(view.rejectedNear).toBe(true);
      expect(view.resolveAnchor(detail)).toBeNull();
      const restored = view.restore(new URLSearchParams(`${query}&offset=48`), {
        anchor,
      });
      expect(restored.search.has('near')).toBe(false);
      expect(restored.search.has('offset')).toBe(false);
    },
  );

  it('bounds restored input and discards malformed filter entries and unsupported weights', () => {
    const { view } = createView();
    const restored = view.restore(
      new URLSearchParams(
        'near=42&radius=100000&bodies=elw_count:1000-,ring_count:-3&archetype=flexible&min-tier=C&page_size=100&offset=10000&selected=bad&weight=capacity:999999',
      ),
    );
    const next = createView(`?${restored.search}`, restored.state).view;
    expect(next).toMatchObject({
      nearId: '42',
      radius: 500,
      bodyFilters: { ring_count: { max: 3 } },
      rankingState: {
        archetype: 'flexible',
        minimumTier: 'B',
        pageSize: 50,
        offset: 9950,
        weights: {},
      },
      urlSelection: null,
    });
    expect(restored.search.has('weight')).toBe(false);
    expect(restored.search.has('selected')).toBe(false);
  });

  it('rejects duplicated selections, duplicate bounds and overlong filter payloads', () => {
    const { view } = createView();
    const params = new URLSearchParams(
      'near=42&radius=100&radius=2000&selected=7&selected=8&archetype=unsupported&page_size=0&offset=-1',
    );
    params.set(
      'bodies',
      `elw_count:1-,${'x'.repeat(MAX_BODIES_PARAMETER_LENGTH)}`,
    );
    const restored = view.restore(params);
    const next = createView(`?${restored.search}`, restored.state).view;
    expect(next).toMatchObject({
      nearId: '42',
      radius: 500,
      bodyFilters: {},
      urlSelection: null,
      rankingState: DEFAULT_RANKING_STATE,
    });
    for (const key of [
      'radius',
      'selected',
      'bodies',
      'archetype',
      'page_size',
      'offset',
    ])
      expect(restored.search.has(key)).toBe(false);
  });

  it('decodes a replacement without changing the active view or supplied history and URL', () => {
    const { view } = createView('?near=7&radius=100');
    view.query = 'Unsubmitted name';
    const params = new URLSearchParams(`near=${anchor.id64}&radius=1000`);
    const originalParams = params.toString();
    const history = { anchor, finderRequested: true };
    const restored = view.restore(params, history);
    expect(restored.search.get('near')).toBe(anchor.id64);
    expect(view.nearId).toBe('7');
    expect(view.radius).toBe(100);
    expect(view.query).toBe('Unsubmitted name');
    expect(params.toString()).toBe(originalParams);
    expect(history).toEqual({ anchor, finderRequested: true });
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it('rejects invalid history coordinates and clears the invalid anchor during restore', () => {
    const invalid = {
      anchor: { ...anchor, z: Infinity },
      finderRequested: 'yes',
    };
    const { view } = createView('', invalid as unknown as App.PageState);
    expect(view.historyAnchor).toBeNull();
    expect(view.invalidAnchorState).toBe(true);
    expect(view.rankingUrlReady).toBe(false);
    const restored = view.restore(new URLSearchParams(), invalid);
    const next = createView('', restored.state).view;
    expect(next.historyAnchor).toBeNull();
    expect(next.invalidAnchorState).toBe(false);
    expect(next.hasResultsIntent).toBe(false);
  });

  it('keeps the disabled-F4 recovery path while retaining supported near filters and radius', () => {
    const { view } = createView(
      '?near=42&radius=100&bodies=elw_count:1-&archetype=flexible&min-tier=S&page_size=1&offset=9&selected=7&system=8',
      {},
      false,
    );
    expect(view.parsedRanking).toBeNull();
    expect(view.rankingState).toEqual(DEFAULT_RANKING_STATE);
    expect(view.urlSelection).toBeNull();
    const restored = view.restore(view.serialize());
    expect(restored.search.get('near')).toBe('42');
    expect(restored.search.get('radius')).toBe('100');
    expect(restored.search.get('bodies')).toBe('elw_count:1-');
    expect(restored.search.get('selected')).toBe('7');
    expect(restored.search.get('system')).toBe('8');
    for (const key of ['archetype', 'min-tier', 'page_size', 'offset'])
      expect(restored.search.has(key)).toBe(false);
  });
});

describe('existing Explore browser preferences', () => {
  it('loads legacy saved, compare, selected-system and layer keys without a new persistence format', () => {
    localStorage.setItem(
      PERSISTENCE_KEYS.pins,
      legacyPersistenceFixtures.pinnedBareArray,
    );
    localStorage.setItem(
      PERSISTENCE_KEYS.compare,
      legacyPersistenceFixtures.compareV2,
    );
    localStorage.setItem(
      PERSISTENCE_KEYS.selectedSystem,
      legacyPersistenceFixtures.selectedSystemContext,
    );
    localStorage.setItem(
      PERSISTENCE_KEYS.mapLayers,
      JSON.stringify({ version: 1, density: false, journeys: true }),
    );
    const { view, persistence } = createView();
    for (const store of Object.values(persistence)) store.hydrate();
    expect(get(view.pins).value).toMatchObject([
      { id64: '12345', name: 'Test System' },
    ]);
    expect(get(view.compare).value).toMatchObject([
      { id64: '42', name: 'Persisted' },
    ]);
    expect(get(view.selectedSystem).value).toBe('456');
    expect(get(view.layerPreferences).value).toEqual({
      density: false,
      journeys: true,
    });
    expect(view.layerPreferences.set({ density: true, journeys: false })).toBe(
      true,
    );
    expect(
      JSON.parse(localStorage.getItem(PERSISTENCE_KEYS.mapLayers)!),
    ).toEqual({ version: 1, density: true, journeys: false });
    expect(localStorage.length).toBe(4);
    expect(sessionStorage.length).toBe(0);
  });
});
