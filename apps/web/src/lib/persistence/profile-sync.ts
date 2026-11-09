import {
  pullProfileSync,
  pushProfileSync,
  type ProfileSyncPullResponse,
  type ProfileSyncPushResponse,
} from '$lib/api/client';
import {
  PERSISTENCE_KEYS,
  SYNC_KEY_PATTERN,
  type LocalStorageKey,
  type PersistedStore,
} from './storage';
import {
  colonyProjects,
  compare,
  expansionPlans,
  fcRoute,
  legacyColonyPassthrough,
  myWork,
  pins,
  profileSyncLast,
} from './stores';

/** Closed compatibility allowlist inherited from the React profile blob. */
export const PROFILE_SYNC_KEYS = [
  PERSISTENCE_KEYS.pins,
  PERSISTENCE_KEYS.compare,
  PERSISTENCE_KEYS.legacyColony,
  PERSISTENCE_KEYS.fcRoute,
  PERSISTENCE_KEYS.myWork,
  PERSISTENCE_KEYS.colonyProjects,
  PERSISTENCE_KEYS.expansionPlans,
] as const satisfies readonly LocalStorageKey[];

export type ProfileSyncKey = (typeof PROFILE_SYNC_KEYS)[number];

/** Credentials and session handoffs which must never enter a profile blob. */
export const PROFILE_SYNC_SECURITY_DENYLIST = [
  PERSISTENCE_KEYS.adminToken,
  PERSISTENCE_KEYS.syncKey,
] as const;

export type ProfileBlob = {
  version: 1;
  exported_at: string;
} & Partial<Record<ProfileSyncKey, unknown>>;

export interface ProfileSyncValue {
  present: boolean;
  value?: unknown;
}

/** Plain dependency boundary keeps gather/apply and commands framework-neutral. */
export interface ProfileSyncPersistence {
  read(key: ProfileSyncKey): ProfileSyncValue;
  apply(key: ProfileSyncKey, value: unknown): boolean;
  recordPush(updatedAt: string): boolean;
}

interface ProfileStoreBinding {
  read(): ProfileSyncValue;
  apply(value: unknown): boolean;
}

function bindStore<T>(store: PersistedStore<T>): ProfileStoreBinding {
  return {
    read() {
      const stored = store.readStored();
      if (
        !stored.present ||
        stored.state.diagnostic !== null ||
        stored.state.value === null
      )
        return { present: false };
      return { present: true, value: stored.state.value };
    },
    apply: (value) => store.setFromJsonValue(value),
  };
}

const browserBindings: Record<ProfileSyncKey, ProfileStoreBinding> = {
  [PERSISTENCE_KEYS.pins]: bindStore(pins),
  [PERSISTENCE_KEYS.compare]: bindStore(compare),
  [PERSISTENCE_KEYS.legacyColony]: bindStore(legacyColonyPassthrough),
  [PERSISTENCE_KEYS.fcRoute]: bindStore(fcRoute),
  [PERSISTENCE_KEYS.myWork]: bindStore(myWork),
  [PERSISTENCE_KEYS.colonyProjects]: bindStore(colonyProjects),
  [PERSISTENCE_KEYS.expansionPlans]: bindStore(expansionPlans),
};

export const browserProfileSyncPersistence: ProfileSyncPersistence = {
  read: (key) => browserBindings[key].read(),
  apply: (key, value) => browserBindings[key].apply(value),
  recordPush: (updatedAt) => profileSyncLast.set(updatedAt),
};

export function gatherProfileBlob(
  persistence: ProfileSyncPersistence = browserProfileSyncPersistence,
  exportedAt = new Date().toISOString(),
): ProfileBlob {
  const blob: Record<string, unknown> = {
    version: 1,
    exported_at: exportedAt,
  };
  for (const key of PROFILE_SYNC_KEYS) {
    const entry = persistence.read(key);
    if (entry.present && entry.value !== undefined && entry.value !== null)
      blob[key] = entry.value;
  }
  return blob as ProfileBlob;
}

export interface ProfileApplyReceipt {
  applied: ProfileSyncKey[];
  rejected: ProfileSyncKey[];
}

/**
 * Apply only present, non-null allowlisted datasets. This is a key-level
 * merge: a present dataset replaces that local dataset, while missing keys do
 * not clear state. Store setters provide same-tab fan-out; no synthetic
 * StorageEvent is dispatched.
 */
export function applyProfileBlob(
  blob: unknown,
  persistence: ProfileSyncPersistence = browserProfileSyncPersistence,
): ProfileApplyReceipt {
  const receipt: ProfileApplyReceipt = { applied: [], rejected: [] };
  if (blob === null || typeof blob !== 'object' || Array.isArray(blob))
    return receipt;
  const record = blob as Record<string, unknown>;
  for (const key of PROFILE_SYNC_KEYS) {
    if (!Object.prototype.hasOwnProperty.call(record, key)) continue;
    const value = record[key];
    if (value === undefined || value === null) continue;
    if (persistence.apply(key, value)) receipt.applied.push(key);
    else receipt.rejected.push(key);
  }
  return receipt;
}

export interface ProfileSyncTransport {
  pull(
    syncKey: string,
    signal?: AbortSignal,
  ): Promise<ProfileSyncPullResponse<unknown>>;
  push(
    syncKey: string,
    blob: ProfileBlob,
    signal?: AbortSignal,
  ): Promise<ProfileSyncPushResponse>;
}

const browserTransport: ProfileSyncTransport = {
  pull: (syncKey, signal) => pullProfileSync(syncKey, signal),
  push: (syncKey, blob, signal) => pushProfileSync(syncKey, blob, signal),
};

export type ProfilePullResult =
  | {
      kind: 'pulled';
      updated_at: string;
      blob_bytes: number;
      apply: ProfileApplyReceipt;
    }
  | { kind: 'empty'; apply: ProfileApplyReceipt };

export interface ProfilePushResult {
  kind: 'pushed';
  updated_at: string;
  blob_bytes: number;
  receipt_recorded: boolean;
  blob: ProfileBlob;
}

export interface ProfileSyncCommandService {
  pull(syncKey: string, signal?: AbortSignal): Promise<ProfilePullResult>;
  push(syncKey: string, signal?: AbortSignal): Promise<ProfilePushResult>;
}

function validateProfileSyncKey(syncKey: string): void {
  if (!SYNC_KEY_PATTERN.test(syncKey))
    throw new TypeError(
      'Profile sync key must be 16-128 URL-safe alphanumeric characters',
    );
}

function errorStatus(error: unknown): number | null {
  if (
    error !== null &&
    typeof error === 'object' &&
    'status' in error &&
    typeof error.status === 'number'
  )
    return error.status;
  return null;
}

/**
 * Manual command service. Construction has no network calls or subscriptions;
 * pull and push happen only when their explicit methods are invoked.
 */
export function createProfileSyncCommandService(
  options: {
    persistence?: ProfileSyncPersistence;
    transport?: ProfileSyncTransport;
    now?: () => string;
  } = {},
): ProfileSyncCommandService {
  const persistence = options.persistence ?? browserProfileSyncPersistence;
  const transport = options.transport ?? browserTransport;
  const now = options.now ?? (() => new Date().toISOString());

  return {
    async pull(syncKey, signal) {
      validateProfileSyncKey(syncKey);
      try {
        const response = await transport.pull(syncKey, signal);
        return {
          kind: 'pulled',
          updated_at: response.updated_at,
          blob_bytes: response.blob_bytes,
          apply: applyProfileBlob(response.blob, persistence),
        };
      } catch (error) {
        if (errorStatus(error) === 404)
          return {
            kind: 'empty',
            apply: { applied: [], rejected: [] },
          };
        throw error;
      }
    },
    async push(syncKey, signal) {
      validateProfileSyncKey(syncKey);
      const blob = gatherProfileBlob(persistence, now());
      const response = await transport.push(syncKey, blob, signal);
      return {
        kind: 'pushed',
        updated_at: response.updated_at,
        blob_bytes: response.blob_bytes,
        receipt_recorded: persistence.recordPush(response.updated_at),
        blob,
      };
    },
  };
}
