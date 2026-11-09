import {
  DEFAULT_FC_CONFIG,
  PERSISTENCE_KEYS,
  colonyProjectsCodec,
  compareCodec,
  createPersistedStore,
  densityCodec,
  expansionPlansCodec,
  fcCodec,
  generateSyncKey,
  id64StringCodec,
  myWorkCodec,
  nullableCodec,
  opaqueJsonCodec,
  pinnedCodec,
  profileSyncKeyCodec,
  rawStringCodec,
  selectedRouteCodec,
  syncKeyCodec,
  type ColonyProjectCollection,
  type CompareEntry,
  type ExpansionPlanCollection,
  type FcState,
  type MyWorkCollection,
  type PinnedEntry,
} from './storage';
import type { Id64 } from '../domain/id64';

/**
 * Active local stores use one native-storage-event listener per singleton.
 * Same-tab callers use `.set`; listeners only re-read and never write, so
 * profile imports do not need synthetic events and cannot form feedback loops.
 */
export const CROSS_TAB_POLICY = 'guarded-native-storage-event' as const;
const CROSS_TAB = true;
const initialSyncKey = generateSyncKey();

export const pins = createPersistedStore<PinnedEntry[]>({
  key: PERSISTENCE_KEYS.pins,
  initial: () => [],
  codec: pinnedCodec,
  crossTab: CROSS_TAB,
  migrateOnHydrate: true,
});
export const compare = createPersistedStore<CompareEntry[]>({
  key: PERSISTENCE_KEYS.compare,
  initial: () => [],
  codec: compareCodec,
  crossTab: CROSS_TAB,
  migrateOnHydrate: true,
});
export const syncKey = createPersistedStore({
  key: PERSISTENCE_KEYS.syncKey,
  initial: () => ({ state: { syncKey: initialSyncKey }, version: 0 }),
  codec: syncKeyCodec,
  crossTab: CROSS_TAB,
  persistInitial: true,
});
export const selectedRoute = createPersistedStore({
  key: PERSISTENCE_KEYS.selectedRoute,
  initial: () => ({ state: { selectedRouteId: null }, version: 0 }),
  codec: selectedRouteCodec,
  crossTab: CROSS_TAB,
});
export const myWork = createPersistedStore<MyWorkCollection>({
  key: PERSISTENCE_KEYS.myWork,
  initial: () => ({ state: { systems: {} }, version: 1 }),
  codec: myWorkCodec,
  crossTab: CROSS_TAB,
  migrateOnHydrate: true,
});
export const colonyProjects = createPersistedStore<ColonyProjectCollection>({
  key: PERSISTENCE_KEYS.colonyProjects,
  initial: () => ({ state: { projects: {} }, version: 3 }),
  codec: colonyProjectsCodec,
  crossTab: CROSS_TAB,
  migrateOnHydrate: true,
});
export const expansionPlans = createPersistedStore<ExpansionPlanCollection>({
  key: PERSISTENCE_KEYS.expansionPlans,
  initial: () => ({ state: { plans: {} }, version: 1 }),
  codec: expansionPlansCodec,
  crossTab: CROSS_TAB,
  migrateOnHydrate: true,
});
export const fcRoute = createPersistedStore<FcState>({
  key: PERSISTENCE_KEYS.fcRoute,
  initial: () => ({ waypoints: [], config: { ...DEFAULT_FC_CONFIG } }),
  codec: fcCodec,
  crossTab: CROSS_TAB,
  migrateOnHydrate: true,
});
export const profileSyncKey = createPersistedStore({
  key: PERSISTENCE_KEYS.profileSyncKey,
  initial: () => '',
  codec: profileSyncKeyCodec,
  crossTab: CROSS_TAB,
});
export const profileSyncLast = createPersistedStore({
  key: PERSISTENCE_KEYS.profileSyncLast,
  initial: () => '',
  codec: rawStringCodec(),
  crossTab: CROSS_TAB,
});
export const selectedSystem = createPersistedStore<Id64 | null>({
  key: PERSISTENCE_KEYS.selectedSystem,
  initial: () => null,
  codec: nullableCodec(id64StringCodec),
  crossTab: CROSS_TAB,
});
export const density = createPersistedStore({
  key: PERSISTENCE_KEYS.density,
  initial: () => 'comfortable',
  codec: densityCodec,
  crossTab: CROSS_TAB,
  persistInitial: true,
});
export const adminToken = createPersistedStore({
  key: PERSISTENCE_KEYS.adminToken,
  area: 'session',
  initial: () => '',
  codec: rawStringCodec(),
});

/** Opaque legacy profile payload; there is deliberately no active feature store. */
export const legacyColonyPassthrough = createPersistedStore<unknown | null>({
  key: PERSISTENCE_KEYS.legacyColony,
  initial: () => null,
  codec: nullableCodec(opaqueJsonCodec),
});

export const applicationStores = {
  pins,
  compare,
  syncKey,
  selectedRoute,
  myWork,
  colonyProjects,
  expansionPlans,
  fcRoute,
  profileSyncKey,
  profileSyncLast,
  selectedSystem,
  density,
};

let applicationStoresHydrated = false;

export function hydrateApplicationStores(): void {
  if (applicationStoresHydrated) return;
  applicationStoresHydrated = true;
  // The credential is hydrated for its dedicated control but is never exposed
  // through the application/profile-sync service inventory.
  adminToken.hydrate();
  for (const store of Object.values(applicationStores)) store.hydrate();
}

export type MyWorkState = MyWorkCollection;
