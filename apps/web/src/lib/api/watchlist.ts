// A browser's own watchlist, kept under its own key. Offered to the app
// through ./client, like everything in this folder.
import { parseId64 } from '@ed-finder/api-client/id64';
import type { Id64 } from '@ed-finder/api-client/id64';
import {
  addWatchlistApiV2WatchlistSyncKeyId64Post,
  getWatchlistApiV2WatchlistSyncKeyGet,
  removeWatchlistApiV2WatchlistSyncKeyId64Delete,
} from './generated/sdk.gen';
import { losslessId64 } from './values';

/** Existing sync-key watchlist rows; optional snapshots may be absent or null. */
export interface WatchlistEntry {
  readonly system_id64: Id64;
  readonly name: string;
  readonly x?: number | null;
  readonly y?: number | null;
  readonly z?: number | null;
  readonly population?: number | null;
  readonly is_colonised?: boolean | null;
  readonly added_at?: string | null;
  readonly last_checked_at?: string | null;
  readonly last_status?: string | null;
  readonly score?: number | null;
  readonly economy_suggestion?: string | null;
  readonly primary_archetype?: string | null;
  readonly secondary_archetype?: string | null;
  readonly archetype_score?: number | null;
  readonly buildability_score?: number | null;
  readonly purity_score?: number | null;
  readonly alert_min_development_score?: number | null;
  readonly alert_min_score?: number | null;
  readonly alert_economy?: string | null;
}

export interface WatchlistResponse {
  readonly sync_key: string;
  readonly watchlist: readonly WatchlistEntry[];
}

function watchlistRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new TypeError('The watchlist response is invalid');
  return value as Record<string, unknown>;
}

function watchlistNumber(
  row: Record<string, unknown>,
  field: string,
): number | null {
  const value = row[field];
  if (value == null) return null;
  if (typeof value !== 'number' || !Number.isFinite(value))
    throw new TypeError(`The watchlist ${field} is invalid`);
  return value;
}

function watchlistString(
  row: Record<string, unknown>,
  field: string,
): string | null {
  const value = row[field];
  if (value == null) return null;
  if (typeof value !== 'string')
    throw new TypeError(`The watchlist ${field} is invalid`);
  return value;
}

function watchlistBoolean(
  row: Record<string, unknown>,
  field: string,
): boolean | null {
  const value = row[field];
  if (value == null) return null;
  if (typeof value !== 'boolean')
    throw new TypeError(`The watchlist ${field} is invalid`);
  return value;
}

function normalizeWatchlistEntry(value: unknown): WatchlistEntry {
  const row = watchlistRecord(value);
  if (typeof row.name !== 'string' || !row.name.trim())
    throw new TypeError('The watchlist system name is invalid');
  return {
    system_id64: losslessId64(row.system_id64),
    name: row.name,
    x: watchlistNumber(row, 'x'),
    y: watchlistNumber(row, 'y'),
    z: watchlistNumber(row, 'z'),
    population: watchlistNumber(row, 'population'),
    is_colonised: watchlistBoolean(row, 'is_colonised'),
    added_at: watchlistString(row, 'added_at'),
    last_checked_at: watchlistString(row, 'last_checked_at'),
    last_status: watchlistString(row, 'last_status'),
    score: watchlistNumber(row, 'score'),
    economy_suggestion: watchlistString(row, 'economy_suggestion'),
    primary_archetype: watchlistString(row, 'primary_archetype'),
    secondary_archetype: watchlistString(row, 'secondary_archetype'),
    archetype_score: watchlistNumber(row, 'archetype_score'),
    buildability_score: watchlistNumber(row, 'buildability_score'),
    purity_score: watchlistNumber(row, 'purity_score'),
    alert_min_development_score: watchlistNumber(
      row,
      'alert_min_development_score',
    ),
    alert_min_score: watchlistNumber(row, 'alert_min_score'),
    alert_economy: watchlistString(row, 'alert_economy'),
  };
}

export async function getWatchlist(
  syncKey: string,
  signal?: AbortSignal,
): Promise<WatchlistResponse> {
  const { data } = await getWatchlistApiV2WatchlistSyncKeyGet({
    throwOnError: true,
    path: { sync_key: syncKey },
    signal,
  });
  // The existing backend has no response model, so the SDK returns unknown.
  // Validate that established shape here without changing the API contract.
  const response = watchlistRecord(data);
  if (response.sync_key !== syncKey || !Array.isArray(response.watchlist))
    throw new TypeError('The watchlist response is invalid');
  return {
    sync_key: syncKey,
    watchlist: response.watchlist.map(normalizeWatchlistEntry),
  };
}

export async function addWatchlist(
  syncKey: string,
  id64: Id64,
  signal?: AbortSignal,
): Promise<{ readonly ok: true; readonly sync_key: string }> {
  const { data } = await addWatchlistApiV2WatchlistSyncKeyId64Post({
    throwOnError: true,
    path: { sync_key: syncKey, id64: parseId64(id64) as unknown as number },
    signal,
  });
  const response = watchlistRecord(data);
  if (response.ok !== true || response.sync_key !== syncKey)
    throw new TypeError('The watchlist save response is invalid');
  return { ok: true, sync_key: syncKey };
}

export async function removeWatchlist(
  syncKey: string,
  id64: Id64,
  signal?: AbortSignal,
): Promise<{ readonly ok: true }> {
  const { data } = await removeWatchlistApiV2WatchlistSyncKeyId64Delete({
    throwOnError: true,
    path: { sync_key: syncKey, id64: parseId64(id64) as unknown as number },
    signal,
  });
  if (watchlistRecord(data).ok !== true)
    throw new TypeError('The watchlist remove response is invalid');
  return { ok: true };
}
