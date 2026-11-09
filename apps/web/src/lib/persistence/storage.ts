import { writable, type Readable } from 'svelte/store';
import { ADMIN_TOKEN_SESSION_KEY as SHARED_ADMIN_TOKEN_SESSION_KEY } from '@ed-finder/api-client/core';

import { FINDER_F4_ENABLED } from '../config';
import { parseId64, type Id64 } from '../domain/id64';
import { isArchetypeKey } from '../features/explore/archetypes';

/**
 * The one browser-storage key authority for the Svelte application. Runtime
 * consumers import these names instead of redeclaring string keys.
 */
export const PERSISTENCE_KEYS = {
  pins: 'ed_pinned',
  compare: 'ed_compare_v2',
  legacyColony: 'ed_colony_v2',
  syncKey: 'ed_sync_key',
  selectedRoute: 'ed_selected_route',
  myWork: 'ed_my_work_v1',
  colonyProjects: 'ed_colony_projects_v1',
  expansionPlans: 'ed_expansion_plans_v1',
  fcRoute: 'ed_fc_v2',
  profileSyncKey: 'ed_profile_sync_key',
  profileSyncLast: 'ed_profile_sync_last',
  selectedSystem: 'ed-finder:selected-system-context',
  density: 'ed_density_v1',
  mapLayers: 'ed-finder:map-layers:v1',
  adminToken: SHARED_ADMIN_TOKEN_SESSION_KEY,
} as const;

export const LOCAL_STORAGE_KEYS = [
  PERSISTENCE_KEYS.pins,
  PERSISTENCE_KEYS.compare,
  PERSISTENCE_KEYS.legacyColony,
  PERSISTENCE_KEYS.syncKey,
  PERSISTENCE_KEYS.selectedRoute,
  PERSISTENCE_KEYS.myWork,
  PERSISTENCE_KEYS.colonyProjects,
  PERSISTENCE_KEYS.expansionPlans,
  PERSISTENCE_KEYS.fcRoute,
  PERSISTENCE_KEYS.profileSyncKey,
  PERSISTENCE_KEYS.profileSyncLast,
  PERSISTENCE_KEYS.selectedSystem,
  PERSISTENCE_KEYS.density,
  PERSISTENCE_KEYS.mapLayers,
] as const;

export const SESSION_STORAGE_KEYS = [PERSISTENCE_KEYS.adminToken] as const;

export const ADMIN_TOKEN_SESSION_KEY = PERSISTENCE_KEYS.adminToken;

export type LocalStorageKey = (typeof LOCAL_STORAGE_KEYS)[number];
export type SessionStorageKey = (typeof SESSION_STORAGE_KEYS)[number];
export type StorageAreaName = 'local' | 'session';
export type PersistenceProblem =
  'corrupt-json' | 'invalid-shape' | 'unsupported-version' | 'unavailable';

export interface PersistenceDiagnostic {
  key: LocalStorageKey | SessionStorageKey;
  problem: PersistenceProblem;
  /** Raw data is deliberately not included: session values may be sensitive. */
  detail: string;
  recoverable: boolean;
}

export interface PersistedState<T> {
  value: T;
  hydrated: boolean;
  diagnostic: PersistenceDiagnostic | null;
}

export interface StoredPersistedState<T> {
  present: boolean;
  state: PersistedState<T>;
}

type InspectedPersistedState<T> = StoredPersistedState<T> & {
  raw: string | null;
};

type DecodeResult<T> =
  | { ok: true; value: T }
  | { ok: false; problem: PersistenceProblem; detail: string };

export interface Codec<T> {
  readonly version: number;
  decode(raw: string): DecodeResult<T>;
  encode(value: T): string;
}

export interface PersistedStore<T> extends Readable<PersistedState<T>> {
  readonly key: LocalStorageKey | SessionStorageKey;
  readonly area: StorageAreaName;
  readonly crossTab: boolean;
  hydrate(): void;
  set(value: T): boolean;
  setFromJsonValue(value: unknown): boolean;
  clear(): boolean;
  read(): PersistedState<T>;
  readStored(): StoredPersistedState<T>;
}

type PersistedStoreOptions<T> = {
  initial: () => T;
  codec: Codec<T>;
  crossTab?: boolean;
  persistInitial?: boolean;
  migrateOnHydrate?: boolean;
} & (
  | { key: LocalStorageKey; area?: 'local' }
  | { key: SessionStorageKey; area: 'session' }
);

export function nullableCodec<T>(codec: Codec<T>): Codec<T | null> {
  return {
    version: codec.version,
    decode: codec.decode,
    // createPersistedStore removes null values before encoding.
    encode: (value) => (value === null ? '' : codec.encode(value)),
  };
}

function storageFor(area: StorageAreaName): Storage | null {
  if (typeof window === 'undefined') return null;
  try {
    return area === 'local' ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}

export function createPersistedStore<T>(
  options: PersistedStoreOptions<T>,
): PersistedStore<T> {
  const area = options.area ?? 'local';
  const inner = writable<PersistedState<T>>({
    value: options.initial(),
    hydrated: false,
    diagnostic: null,
  });
  let listening = false;

  function inspectStored(): InspectedPersistedState<T> {
    const storage = storageFor(area);
    if (!storage) {
      return {
        present: false,
        raw: null,
        state: {
          value: options.initial(),
          hydrated: true,
          diagnostic:
            typeof window === 'undefined'
              ? null
              : diagnostic('unavailable', 'Storage is unavailable', true),
        },
      };
    }
    let raw: string | null;
    try {
      raw = storage.getItem(options.key);
    } catch {
      return {
        present: false,
        raw: null,
        state: {
          value: options.initial(),
          hydrated: true,
          diagnostic: diagnostic('unavailable', 'Storage read failed', true),
        },
      };
    }
    if (raw === null)
      return {
        present: false,
        raw: null,
        state: {
          value: options.initial(),
          hydrated: true,
          diagnostic: null,
        },
      };
    const decoded = options.codec.decode(raw);
    return {
      present: true,
      raw,
      state: decoded.ok
        ? { value: decoded.value, hydrated: true, diagnostic: null }
        : {
            value: options.initial(),
            hydrated: true,
            diagnostic: diagnostic(decoded.problem, decoded.detail, true),
          },
    };
  }

  function readStored(): StoredPersistedState<T> {
    const { present, state } = inspectStored();
    return { present, state };
  }

  function read(): PersistedState<T> {
    return inspectStored().state;
  }

  function diagnostic(
    problem: PersistenceProblem,
    detail: string,
    recoverable: boolean,
  ): PersistenceDiagnostic {
    return { key: options.key, problem, detail, recoverable };
  }

  function hydrate() {
    const stored = inspectStored();
    inner.set(stored.state);
    if (
      options.persistInitial &&
      !stored.present &&
      stored.state.diagnostic === null
    ) {
      set(stored.state.value);
    }
    if (
      options.migrateOnHydrate &&
      stored.present &&
      stored.state.diagnostic === null &&
      options.codec.encode(stored.state.value) !== stored.raw
    ) {
      set(stored.state.value);
    }
    if (
      !listening &&
      options.crossTab &&
      area === 'local' &&
      typeof window !== 'undefined'
    ) {
      listening = true;
      window.addEventListener('storage', (event) => {
        const local = storageFor('local');
        if (
          (event.storageArea === null || event.storageArea === local) &&
          event.key === options.key
        )
          inner.set(read());
      });
    }
  }

  function setFromJsonValue(value: unknown): boolean {
    let raw: string | undefined;
    try {
      raw = JSON.stringify(value);
    } catch {
      return false;
    }
    if (raw === undefined) return false;
    const decoded = options.codec.decode(raw);
    return decoded.ok ? set(decoded.value) : false;
  }

  function set(value: T): boolean {
    const storage = storageFor(area);
    if (!storage) {
      inner.set({
        value,
        hydrated: true,
        diagnostic: diagnostic('unavailable', 'Storage is unavailable', true),
      });
      return false;
    }
    if (value === null) {
      try {
        storage.removeItem(options.key);
        inner.set({ value, hydrated: true, diagnostic: null });
        return true;
      } catch {
        return false;
      }
    }
    // A future envelope belongs to a newer application. Never erase it.
    let current: string | null;
    try {
      current = storage.getItem(options.key);
    } catch {
      inner.set({
        value,
        hydrated: true,
        diagnostic: diagnostic('unavailable', 'Storage read failed', true),
      });
      return false;
    }
    if (current) {
      const parsed = safeJson(current);
      if (
        parsed.ok &&
        isRecord(parsed.value) &&
        typeof parsed.value.version === 'number' &&
        Number.isFinite(parsed.value.version) &&
        Number.isInteger(parsed.value.version) &&
        parsed.value.version >= 0 &&
        parsed.value.version > options.codec.version
      ) {
        const existing = read();
        inner.set({
          value: existing.value,
          hydrated: true,
          diagnostic: diagnostic(
            'unsupported-version',
            `Stored version ${parsed.value.version} is newer than ${options.codec.version}`,
            true,
          ),
        });
        return false;
      }
    }
    try {
      storage.setItem(options.key, options.codec.encode(value));
      inner.set({ value, hydrated: true, diagnostic: null });
      return true;
    } catch {
      inner.set({
        value,
        hydrated: true,
        diagnostic: diagnostic('unavailable', 'Storage write failed', true),
      });
      return false;
    }
  }

  function clear(): boolean {
    const storage = storageFor(area);
    if (!storage) return false;
    try {
      storage.removeItem(options.key);
      inner.set({ value: options.initial(), hydrated: true, diagnostic: null });
      return true;
    } catch {
      return false;
    }
  }

  return {
    key: options.key,
    area,
    crossTab: Boolean(options.crossTab && area === 'local'),
    subscribe: inner.subscribe,
    hydrate,
    set,
    setFromJsonValue,
    clear,
    read,
    readStored,
  };
}

function safeJson(raw: string): { ok: true; value: unknown } | { ok: false } {
  try {
    return { ok: true, value: JSON.parse(raw) as unknown };
  } catch {
    return { ok: false };
  }
}

const jsonCodec = <T>(
  validate: (value: unknown) => T | null,
  version = 0,
): Codec<T> => ({
  version,
  decode(raw) {
    const parsed = safeJson(raw);
    if (!parsed.ok)
      return {
        ok: false,
        problem: 'corrupt-json',
        detail: 'Stored value is not valid JSON',
      };
    if (
      isRecord(parsed.value) &&
      typeof parsed.value.version === 'number' &&
      Number.isFinite(parsed.value.version) &&
      Number.isInteger(parsed.value.version) &&
      parsed.value.version >= 0 &&
      parsed.value.version > version
    ) {
      return {
        ok: false,
        problem: 'unsupported-version',
        detail: `Stored version ${parsed.value.version} is newer than ${version}`,
      };
    }
    const value = validate(parsed.value);
    return value === null
      ? {
          ok: false,
          problem: 'invalid-shape',
          detail: 'Stored value has the wrong shape',
        }
      : { ok: true, value };
  },
  encode: JSON.stringify,
});

export function rawStringCodec(
  validate: (value: string) => boolean = () => true,
): Codec<string> {
  return {
    version: 0,
    decode: (raw) =>
      validate(raw)
        ? { ok: true, value: raw }
        : {
            ok: false,
            problem: 'invalid-shape',
            detail: 'Stored string is invalid',
          },
    encode: (value) => value,
  };
}

export type JsonRecord = Record<string, unknown>;
export type PinnedEntry = JsonRecord & {
  id64: Id64;
  name: string;
  pinned_at: string;
};
export type CompareEntry = JsonRecord & { id64: Id64; name: string };

/**
 * Persisted ranking identity of a pin/compare snapshot (Finder F4a, design
 * section 6). New saves write every field explicitly; a legacy snapshot that
 * predates F4a carries none of them and reads as `score_kind: 'unknown'` with
 * `null` provenance and reference, so it is listed with its stored values but
 * never enters a score or distance comparison.
 */
export const SNAPSHOT_SCORE_KINDS = [
  'overall_potential',
  'selected_fit',
  'unknown',
] as const;
export type SnapshotScoreKind = (typeof SNAPSHOT_SCORE_KINDS)[number];
export const SNAPSHOT_TIERS = ['S', 'A', 'B', 'C', 'D'] as const;
export type SnapshotTier = (typeof SNAPSHOT_TIERS)[number];

export interface SnapshotDistanceReference extends JsonRecord {
  kind: 'anchor' | 'sol';
  id64: Id64;
  name: string;
}

export interface SnapshotRankingProvenance extends JsonRecord {
  ranking_version: string;
  ranking_sha256: string;
  derived_generation_id: string;
  publication_sequence: number;
  archetype_version: string;
  archetype_manifest_sha256: string;
  archetype_model_sha256: string;
}

export interface SnapshotRankingIdentity extends JsonRecord {
  ranking_score: number | null;
  ranking_tier: SnapshotTier | null;
  score_kind: SnapshotScoreKind;
  selected_archetype: string | null;
  distance_reference: SnapshotDistanceReference | null;
  ranking_provenance: SnapshotRankingProvenance | null;
}

export const RANKING_IDENTITY_FIELDS = [
  'ranking_score',
  'ranking_tier',
  'score_kind',
  'selected_archetype',
  'distance_reference',
  'ranking_provenance',
] as const;

const finiteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

export function normaliseSnapshotProvenance(
  value: unknown,
): SnapshotRankingProvenance | null {
  if (!isRecord(value)) return null;
  const {
    ranking_version,
    ranking_sha256,
    derived_generation_id,
    publication_sequence,
    archetype_version,
    archetype_manifest_sha256,
    archetype_model_sha256,
  } = value;
  if (
    typeof ranking_version !== 'string' ||
    typeof ranking_sha256 !== 'string' ||
    typeof derived_generation_id !== 'string' ||
    !Number.isInteger(publication_sequence) ||
    (publication_sequence as number) < 0 ||
    typeof archetype_version !== 'string' ||
    typeof archetype_manifest_sha256 !== 'string' ||
    typeof archetype_model_sha256 !== 'string'
  )
    return null;
  return {
    ranking_version,
    ranking_sha256,
    derived_generation_id,
    publication_sequence: publication_sequence as number,
    archetype_version,
    archetype_manifest_sha256,
    archetype_model_sha256,
  };
}

/** Sol's canonical id64 (`apps/api/src/helpers.py` `SOL_ID64`). */
export const SNAPSHOT_SOL_ID64: Id64 = parseId64('10477373803');

/**
 * A `sol` reference is trusted only when it carries Sol's id64; an `anchor`
 * reference whose id64 is Sol normalizes to `sol`, exactly as the facade
 * normalizes a Sol anchor, so equal origins always compare equal.
 */
export function normaliseSnapshotDistanceReference(
  value: unknown,
): SnapshotDistanceReference | null {
  if (!isRecord(value)) return null;
  if (value.kind !== 'anchor' && value.kind !== 'sol') return null;
  const id64 = canonicaliseId(value.id64);
  if (id64 === INVALID_PERSISTED_VALUE || typeof value.name !== 'string')
    return null;
  if (id64 === SNAPSHOT_SOL_ID64)
    return { kind: 'sol', id64, name: value.name };
  if (value.kind === 'sol') return null;
  return { kind: 'anchor', id64, name: value.name };
}

/**
 * Read a snapshot's ranking identity, normalizing a legacy record (no F4
 * fields) to unknown/null rather than guessing a mode or reference.
 */
export function snapshotRankingIdentity(
  entry: JsonRecord,
): SnapshotRankingIdentity {
  const scoreKind = entry.score_kind;
  const tier = entry.ranking_tier;
  const archetype = entry.selected_archetype;
  return {
    ranking_score: finiteNumber(entry.ranking_score)
      ? entry.ranking_score
      : null,
    ranking_tier: (SNAPSHOT_TIERS as readonly unknown[]).includes(tier)
      ? (tier as SnapshotTier)
      : null,
    score_kind: (SNAPSHOT_SCORE_KINDS as readonly unknown[]).includes(scoreKind)
      ? (scoreKind as SnapshotScoreKind)
      : 'unknown',
    // Only a canonical V3 key is a selected-fit identity; anything else is
    // unknown rather than a value two corrupted entries could agree on.
    selected_archetype: isArchetypeKey(archetype) ? archetype : null,
    distance_reference: normaliseSnapshotDistanceReference(
      entry.distance_reference,
    ),
    ranking_provenance: normaliseSnapshotProvenance(entry.ranking_provenance),
  };
}

/**
 * Codec step for pins/compare: validate every ranking identity field that is
 * present (an invalid value is normalized, never trusted) and leave a legacy
 * record's stored shape otherwise untouched.
 */
function normaliseSnapshotRankingFields<T extends JsonRecord>(entry: T): T {
  if (!RANKING_IDENTITY_FIELDS.some((field) => field in entry)) return entry;
  return {
    ...entry,
    ...snapshotRankingIdentity(entry),
    // Historical keys remain readable evidence, even though they cannot be a
    // V3 selected-fit identity. Keep the raw key on hydration/write-through;
    // comparison eligibility always uses snapshotRankingIdentity instead.
    ...(FINDER_F4_ENABLED && typeof entry.selected_archetype === 'string'
      ? { selected_archetype: entry.selected_archetype }
      : {}),
  };
}

/**
 * Recover an invalid nested distance reference locally, before the recursive
 * id64 canonicalization runs: a malformed `distance_reference.id64` would
 * otherwise poison its entry and make the whole collection decode as
 * `invalid-shape`, discarding every valid snapshot for one optional field.
 */
function recoverSnapshotReferences(value: unknown): unknown {
  if (!Array.isArray(value)) return value;
  return value.map((entry) =>
    isRecord(entry) && 'distance_reference' in entry
      ? {
          ...entry,
          distance_reference: normaliseSnapshotDistanceReference(
            entry.distance_reference,
          ),
        }
      : entry,
  );
}

export const DEFAULT_FC_CONFIG = {
  jump_range_ly: 500,
  cargo_t: 25_000,
  tritium_per_jump: 50,
  tritium_price_cr: 50_000,
} as const;

export interface FcConfig extends JsonRecord {
  jump_range_ly: number;
  cargo_t: number;
  tritium_per_jump: number;
  tritium_price_cr: number;
}

export interface FcState extends JsonRecord {
  waypoints: Array<JsonRecord & { id64?: Id64 | null }>;
  config: FcConfig;
}

export interface VersionedCollection extends JsonRecord {
  state: JsonRecord;
  version: number;
}

export type SavedSystemLabel = 'considering' | 'favourite' | 'ready_to_plan';

export interface MyWorkSystemRecord extends JsonRecord {
  id64: Id64;
  name: string;
  x: number | null;
  y: number | null;
  z: number | null;
  population: number | null;
  is_colonised: boolean;
  labels: SavedSystemLabel[];
  explicit_colonised_at: string | null;
  updated_at: string;
}

export interface MyWorkCollection extends VersionedCollection {
  state: JsonRecord & { systems: Record<string, MyWorkSystemRecord> };
  version: 1;
}

export interface ColonyProjectRecord extends JsonRecord {
  id: string;
  system_id64: Id64;
  build_plan_placements: JsonRecord[];
  selected_body_assignments: JsonRecord;
  declared_roles: JsonRecord[];
  status: 'draft' | 'ready_to_build' | 'building' | 'established';
  archived_at: string | null;
}

export interface ColonyProjectCollection extends VersionedCollection {
  state: JsonRecord & { projects: Record<string, ColonyProjectRecord> };
  version: 3;
}

export interface ExpansionPlanSlot extends JsonRecord {
  slot_index: number;
  system_id64: Id64;
  colony_project_id: string | null;
}

export interface ExpansionPlanRecord extends JsonRecord {
  id: string;
  anchor_system_id64: Id64;
  slots: ExpansionPlanSlot[];
  archived_at: string | null;
}

export interface ExpansionPlanCollection extends VersionedCollection {
  state: JsonRecord & { plans: Record<string, ExpansionPlanRecord> };
  version: 1;
}

const INVALID_PERSISTED_VALUE = Symbol('invalid-persisted-value');

function canonicaliseId(value: unknown): Id64 | typeof INVALID_PERSISTED_VALUE {
  try {
    // Safe legacy React snapshots used numbers. Converting only safe integers
    // recovers those records without accepting already-rounded identifiers.
    if (typeof value === 'number') {
      if (!Number.isSafeInteger(value) || value < 0)
        return INVALID_PERSISTED_VALUE;
      return parseId64(String(value));
    }
    return parseId64(value as string | bigint);
  } catch {
    return INVALID_PERSISTED_VALUE;
  }
}

function canonicaliseIds(value: unknown, key = ''): unknown {
  if (key === 'id64' || key.endsWith('_id64')) {
    if (value === null) return null;
    return canonicaliseId(value);
  }
  if (key === 'id64s' || key.endsWith('_id64s')) {
    if (!Array.isArray(value)) return INVALID_PERSISTED_VALUE;
    const ids = value.map(canonicaliseId);
    return ids.includes(INVALID_PERSISTED_VALUE)
      ? INVALID_PERSISTED_VALUE
      : ids;
  }
  if (Array.isArray(value)) {
    const output = value.map((item) => canonicaliseIds(item));
    return output.includes(INVALID_PERSISTED_VALUE)
      ? INVALID_PERSISTED_VALUE
      : output;
  }
  if (!isRecord(value)) return value;
  const output: JsonRecord = {};
  for (const [childKey, child] of Object.entries(value)) {
    const normalised = canonicaliseIds(child, childKey);
    if (normalised === INVALID_PERSISTED_VALUE) return INVALID_PERSISTED_VALUE;
    output[childKey] = normalised;
  }
  return output;
}

function entryArray<T extends JsonRecord & { id64: Id64 }>(
  value: unknown,
  validate: (entry: JsonRecord & { id64: Id64 }) => entry is T,
): T[] | null {
  if (!Array.isArray(value)) return null;
  const result: T[] = [];
  for (const candidate of value) {
    const normalised = canonicaliseIds(candidate);
    if (
      !isRecord(normalised) ||
      typeof normalised.id64 !== 'string' ||
      !validate(normalised as JsonRecord & { id64: Id64 })
    )
      return null;
    result.push(normalised as T);
  }
  return result;
}

function collectionEnvelope(
  value: unknown,
  acceptedVersion: number,
): VersionedCollection | null {
  const normalised = canonicaliseIds(value);
  if (
    !isRecord(normalised) ||
    !isRecord(normalised.state) ||
    typeof normalised.version !== 'number' ||
    !Number.isFinite(normalised.version) ||
    !Number.isInteger(normalised.version) ||
    normalised.version < 0 ||
    normalised.version > acceptedVersion
  )
    return null;
  return normalised as VersionedCollection;
}

function isSupportedCollectionEnvelope(
  value: unknown,
  acceptedVersion: number,
): value is JsonRecord & { state: JsonRecord; version: number } {
  return (
    isRecord(value) &&
    isRecord(value.state) &&
    typeof value.version === 'number' &&
    Number.isFinite(value.version) &&
    Number.isInteger(value.version) &&
    value.version >= 0 &&
    value.version <= acceptedVersion
  );
}

function normaliseCollectionEnvelope<T extends VersionedCollection>(
  value: unknown,
  collectionKey: string,
  version: number,
  normaliseCollection: (collection: unknown) => JsonRecord,
): T | null {
  if (!isSupportedCollectionEnvelope(value, version)) return null;

  // Canonicalise every field outside the record collection as one unit. Bad
  // records are recovered independently below, while an unsafe identifier in
  // envelope metadata still fails closed.
  const canonical = canonicaliseIds({
    ...value,
    state: { ...value.state, [collectionKey]: {} },
  });
  if (!isRecord(canonical) || !isRecord(canonical.state)) return null;
  canonical.state[collectionKey] = normaliseCollection(
    value.state[collectionKey],
  );
  canonical.version = version;
  return canonical as T;
}

function normaliseLabels(value: unknown): SavedSystemLabel[] {
  if (!Array.isArray(value)) return [];
  const labels = new Set<SavedSystemLabel>();
  for (const candidate of value) {
    if (
      candidate === 'considering' ||
      candidate === 'favourite' ||
      candidate === 'ready_to_plan'
    )
      labels.add(candidate);
  }
  return [...labels];
}

function finiteNumberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function finiteNumberOrDefault(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function normaliseFcConfig(value: unknown): FcConfig {
  const config = isRecord(value) ? value : {};
  return {
    ...config,
    jump_range_ly: finiteNumberOrDefault(
      config.jump_range_ly,
      DEFAULT_FC_CONFIG.jump_range_ly,
    ),
    cargo_t: finiteNumberOrDefault(config.cargo_t, DEFAULT_FC_CONFIG.cargo_t),
    tritium_per_jump: finiteNumberOrDefault(
      config.tritium_per_jump,
      DEFAULT_FC_CONFIG.tritium_per_jump,
    ),
    tritium_price_cr: finiteNumberOrDefault(
      config.tritium_price_cr,
      DEFAULT_FC_CONFIG.tritium_price_cr,
    ),
  };
}

/** React-compatible My Work recovery with the lossless Id64 policy applied. */
export function normaliseMyWorkRecord(
  value: unknown,
  fallbackUpdatedAt = new Date().toISOString(),
): Record<string, MyWorkSystemRecord> {
  const candidates = Array.isArray(value)
    ? value
    : isRecord(value)
      ? Object.values(value)
      : [];
  const systems: Record<string, MyWorkSystemRecord> = {};
  for (const candidate of candidates) {
    const normalised = canonicaliseIds(candidate);
    if (!isRecord(normalised) || typeof normalised.id64 !== 'string') continue;
    const id64 = normalised.id64 as Id64;
    systems[id64] = {
      ...normalised,
      id64,
      name:
        typeof normalised.name === 'string' && normalised.name.trim()
          ? normalised.name.trim()
          : `System ${id64}`,
      x: finiteNumberOrNull(normalised.x),
      y: finiteNumberOrNull(normalised.y),
      z: finiteNumberOrNull(normalised.z),
      population: finiteNumberOrNull(normalised.population),
      is_colonised: Boolean(normalised.is_colonised),
      labels: normaliseLabels(normalised.labels),
      explicit_colonised_at:
        typeof normalised.explicit_colonised_at === 'string'
          ? normalised.explicit_colonised_at
          : null,
      updated_at:
        typeof normalised.updated_at === 'string' && normalised.updated_at
          ? normalised.updated_at
          : fallbackUpdatedAt,
    };
  }
  return systems;
}

function normalisePlacements(value: unknown): JsonRecord[] {
  // Placements are versioned by their owning planner. V2 items are read-only
  // evidence and CPE items must survive hydration without legacy conversion.
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function normaliseColonyStatus(value: unknown): ColonyProjectRecord['status'] {
  return value === 'ready_to_build' ||
    value === 'building' ||
    value === 'established'
    ? value
    : 'draft';
}

export function normaliseColonyProjectRecord(
  value: unknown,
): Record<string, ColonyProjectRecord> {
  const candidates = Array.isArray(value)
    ? value
    : isRecord(value)
      ? Object.values(value)
      : [];
  const projects: Record<string, ColonyProjectRecord> = {};
  for (const candidate of candidates) {
    if (!isRecord(candidate)) continue;
    // These opaque planner-owned values must remain byte-for-byte JSON values,
    // including unknown V2 fields and assignment namespaces owned elsewhere.
    const {
      build_plan_placements: placements,
      selected_body_assignments: assignments,
      ...projectFields
    } = candidate;
    const normalised = canonicaliseIds(projectFields);
    if (
      !isRecord(normalised) ||
      typeof normalised.id !== 'string' ||
      !normalised.id ||
      typeof normalised.system_id64 !== 'string'
    )
      continue;
    projects[normalised.id] = {
      ...normalised,
      id: normalised.id,
      system_id64: normalised.system_id64 as Id64,
      build_plan_placements: normalisePlacements(placements),
      selected_body_assignments: isRecord(assignments) ? assignments : {},
      declared_roles: Array.isArray(normalised.declared_roles)
        ? normalised.declared_roles.filter(isRecord)
        : [],
      objective: normalised.objective ?? null,
      start_approach: normalised.start_approach ?? null,
      created_from: normalised.created_from ?? null,
      status: normaliseColonyStatus(normalised.status),
      archived_at:
        typeof normalised.archived_at === 'string'
          ? normalised.archived_at
          : null,
    };
  }
  return projects;
}

function normalisePlanSlot(value: unknown): ExpansionPlanSlot | null {
  const normalised = canonicaliseIds(value);
  if (
    !isRecord(normalised) ||
    typeof normalised.slot_index !== 'number' ||
    !Number.isFinite(normalised.slot_index) ||
    !Number.isInteger(normalised.slot_index) ||
    typeof normalised.system_id64 !== 'string'
  )
    return null;
  return {
    ...normalised,
    slot_index: normalised.slot_index,
    system_id64: normalised.system_id64 as Id64,
    colony_project_id:
      typeof normalised.colony_project_id === 'string'
        ? normalised.colony_project_id
        : null,
  };
}

/**
 * Recover expansion plans record-by-record so malformed profile data cannot
 * make selectors call `.some`/`.find` on a non-array. Valid slots and unknown
 * forward-compatible fields survive migration.
 */
export function normalisePlanRecord(
  value: unknown,
): Record<string, ExpansionPlanRecord> {
  const candidates = Array.isArray(value)
    ? value
    : isRecord(value)
      ? Object.values(value)
      : [];
  const plans: Record<string, ExpansionPlanRecord> = {};
  for (const candidate of candidates) {
    if (!isRecord(candidate)) continue;
    const { slots: candidateSlots, ...candidateWithoutSlots } = candidate;
    const normalised = canonicaliseIds(candidateWithoutSlots);
    if (
      !isRecord(normalised) ||
      typeof normalised.id !== 'string' ||
      !normalised.id ||
      typeof normalised.anchor_system_id64 !== 'string'
    )
      continue;
    const slots = Array.isArray(candidateSlots)
      ? candidateSlots
          .map(normalisePlanSlot)
          .filter((slot): slot is ExpansionPlanSlot => slot !== null)
      : [];
    plans[normalised.id] = {
      ...normalised,
      id: normalised.id,
      anchor_system_id64: normalised.anchor_system_id64 as Id64,
      slots,
      archived_at:
        typeof normalised.archived_at === 'string'
          ? normalised.archived_at
          : null,
    };
  }
  return plans;
}

export interface ExpansionPlanSlotSystemUpdate extends JsonRecord {
  system_id64: Id64;
  system_name: string;
  scores: Record<string, number>;
  distance_from_anchor_ly: number | null;
}

/** Immutable slot replacement; changing a system invalidates only its old project link. */
export function replaceExpansionPlanSlotSystem(
  plan: ExpansionPlanRecord,
  slotIndex: number,
  system: ExpansionPlanSlotSystemUpdate,
  updatedAt = new Date().toISOString(),
): ExpansionPlanRecord {
  return {
    ...plan,
    slots: plan.slots.map((slot) =>
      slot.slot_index === slotIndex
        ? { ...slot, ...system, colony_project_id: null }
        : slot,
    ),
    updated_at: updatedAt,
  };
}

export const pinnedCodec = jsonCodec<PinnedEntry[]>(
  (value) =>
    entryArray<PinnedEntry>(
      recoverSnapshotReferences(value),
      (entry): entry is PinnedEntry =>
        typeof entry.name === 'string' &&
        entry.name.trim().length > 0 &&
        typeof entry.pinned_at === 'string',
    )?.map(normaliseSnapshotRankingFields) ?? null,
);
export const compareCodec = jsonCodec<CompareEntry[]>(
  (value) =>
    entryArray<CompareEntry>(
      recoverSnapshotReferences(value),
      (entry): entry is CompareEntry =>
        typeof entry.name === 'string' && entry.name.trim().length > 0,
    )?.map(normaliseSnapshotRankingFields) ?? null,
);
export const fcCodec = jsonCodec<FcState>((value) => {
  const normalised = canonicaliseIds(value);
  if (!isRecord(normalised)) return null;
  return {
    ...normalised,
    waypoints: Array.isArray(normalised.waypoints)
      ? normalised.waypoints.filter(isRecord)
      : [],
    config: normaliseFcConfig(normalised.config),
  };
});
export const collectionCodec = (version: number) =>
  jsonCodec<VersionedCollection>(
    (value) => collectionEnvelope(value, version),
    version,
  );

export const myWorkCodec = jsonCodec<MyWorkCollection>(
  (value) =>
    normaliseCollectionEnvelope<MyWorkCollection>(
      value,
      'systems',
      1,
      (systems) => normaliseMyWorkRecord(systems),
    ),
  1,
);

export const colonyProjectsCodec = jsonCodec<ColonyProjectCollection>(
  (value) =>
    normaliseCollectionEnvelope<ColonyProjectCollection>(
      value,
      'projects',
      3,
      (projects) => normaliseColonyProjectRecord(projects),
    ),
  3,
);

export const expansionPlansCodec = jsonCodec<ExpansionPlanCollection>(
  (value) =>
    normaliseCollectionEnvelope<ExpansionPlanCollection>(
      value,
      'plans',
      1,
      (plans) => normalisePlanRecord(plans),
    ),
  1,
);

export const opaqueJsonCodec: Codec<unknown> = {
  version: 0,
  decode(raw) {
    const parsed = safeJson(raw);
    if (!parsed.ok)
      return {
        ok: false,
        problem: 'corrupt-json',
        detail: 'Stored value is not valid JSON',
      };
    const normalised = canonicaliseIds(parsed.value);
    return normalised === INVALID_PERSISTED_VALUE
      ? {
          ok: false,
          problem: 'invalid-shape',
          detail: 'Stored value contains an unsafe identifier',
        }
      : { ok: true, value: normalised };
  },
  encode(value) {
    const raw = JSON.stringify(value);
    if (raw === undefined)
      throw new TypeError('Value is not JSON serialisable');
    return raw;
  },
};

export const SYNC_KEY_PATTERN = /^[A-Za-z0-9_-]{16,128}$/;
const SYNC_KEY_ALPHABET =
  'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';

export function generateSyncKey(length = 32): string {
  if (!Number.isInteger(length) || length < 16 || length > 128)
    throw new RangeError('Sync-key length must be an integer from 16 to 128');
  const bytes = new Uint8Array(length);
  if (typeof crypto === 'undefined' || !('getRandomValues' in crypto))
    throw new Error('Secure random generation is unavailable');
  crypto.getRandomValues(bytes);
  return Array.from(
    bytes,
    (byte) => SYNC_KEY_ALPHABET[byte % SYNC_KEY_ALPHABET.length],
  ).join('');
}

export const syncKeyCodec = jsonCodec<{
  state: { syncKey: string };
  version: number;
}>((value) => {
  if (
    !isRecord(value) ||
    !isRecord(value.state) ||
    value.version !== 0 ||
    typeof value.state.syncKey !== 'string' ||
    !SYNC_KEY_PATTERN.test(value.state.syncKey) ||
    value.state.syncKey === 'legacy'
  )
    return null;
  return {
    ...value,
    state: { ...value.state, syncKey: value.state.syncKey },
    version: 0,
  } as { state: { syncKey: string }; version: number };
});

export const profileSyncKeyCodec = rawStringCodec((value) =>
  SYNC_KEY_PATTERN.test(value),
);
export const selectedRouteCodec = jsonCodec<{
  state: { selectedRouteId: string | null };
  version: number;
}>((value) => {
  if (
    !isRecord(value) ||
    !isRecord(value.state) ||
    value.version !== 0 ||
    (value.state.selectedRouteId !== null &&
      typeof value.state.selectedRouteId !== 'string')
  )
    return null;
  return {
    ...value,
    state: { ...value.state, selectedRouteId: value.state.selectedRouteId },
    version: 0,
  } as { state: { selectedRouteId: string | null }; version: number };
});

export const id64StringCodec: Codec<Id64> = {
  version: 0,
  decode(raw) {
    try {
      return { ok: true, value: parseId64(raw) };
    } catch {
      return {
        ok: false,
        problem: 'invalid-shape',
        detail: 'Stored identifier is not a canonical uint64',
      };
    }
  },
  encode: (value) => value,
};
export const densityCodec = rawStringCodec(
  (value) =>
    value === 'compact' || value === 'comfortable' || value === 'spacious',
);

function isRecord(value: unknown): value is JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
