import { get } from 'svelte/store';
import type { ExploreSystem, WatchlistEntry } from '$lib/api/client';
import { FINDER_F4_ENABLED } from '$lib/config';
import { snapshotRankingIdentity } from '$lib/persistence/storage';
import type {
  CompareEntry,
  PersistedStore,
  PinnedEntry,
} from '$lib/persistence/storage';
import { COMPARE_MAX } from './compare-metrics';

type SelectionResult = { selected: boolean; error: string | null };
const saveError =
  'Your shortlist could not be saved in this browser. Check browser storage before leaving this page.';

/** Missing facts on an older saved surface stay unknown when saved again. */
function withSavedRankingIdentity(snapshot: CompareEntry): CompareEntry {
  if (!FINDER_F4_ENABLED) return snapshot;
  return {
    ...snapshotRankingIdentity(snapshot),
    overall_development_potential: null,
    // Preserve stored scores and historical keys; the identity reader decides
    // whether they are comparable without rewriting the original evidence.
    ...snapshot,
  };
}

/**
 * Snapshot a Finder row for pin/compare. The ranking identity the facade
 * stamped on the row (score kind, selected archetype, distance reference and
 * all seven provenance fields) is persisted explicitly so a later comparison
 * can tell a selected fit from an overall potential and one ranking/model
 * identity from another. A selected fit never enters generic
 * `archetype_score`: the facade leaves that field null on ranking rows, so the
 * legacy fallback below only ever sees the overall potential.
 */
export function snapshotFromExplore(system: ExploreSystem): CompareEntry {
  return {
    ...system,
    name: system.name?.trim() || `System ${system.id64}`,
    archetype_score:
      system.archetype_score ?? system.overall_development_potential ?? null,
    overall_development_potential: system.overall_development_potential ?? null,
    ranking_score: system.ranking_score ?? null,
    ranking_tier: system.ranking_tier ?? null,
    score_kind: system.score_kind ?? 'unknown',
    selected_archetype: system.selected_archetype ?? null,
    distance_reference: system.distance_reference ?? null,
    ranking_provenance: system.ranking_provenance ?? null,
  };
}

export function snapshotFromPin(pin: PinnedEntry): CompareEntry {
  return withSavedRankingIdentity({
    ...pin,
    captured_at:
      pin.captured_at === undefined ? pin.pinned_at : pin.captured_at,
    coords: pin.coords ?? {
      x: pin.x ?? null,
      y: pin.y ?? null,
      z: pin.z ?? null,
    },
    // Legacy pin.economy is a suggestion, not the system's current economy.
    primaryEconomy: pin.primaryEconomy ?? pin.primary_economy ?? null,
  });
}

export function snapshotFromWatchlist(entry: WatchlistEntry): CompareEntry {
  return withSavedRankingIdentity({
    id64: entry.system_id64,
    name: entry.name,
    coords: { x: entry.x ?? null, y: entry.y ?? null, z: entry.z ?? null },
    population: entry.population,
    is_colonised: entry.is_colonised,
    archetype_score: entry.archetype_score ?? entry.score ?? null,
    primary_archetype: entry.primary_archetype,
    secondary_archetype: entry.secondary_archetype,
    buildability_score: entry.buildability_score,
    purity_score: entry.purity_score,
    economy_suggestion: entry.economy_suggestion,
  });
}

/** Retain complete snapshots so a different Finder search never loses facts. */
export function toggleComparison(
  store: PersistedStore<CompareEntry[]>,
  snapshot: CompareEntry,
): SelectionResult {
  const entries = get(store).value;
  const exists = entries.some((entry) => entry.id64 === snapshot.id64);
  if (!exists && entries.length >= COMPARE_MAX) {
    return {
      selected: false,
      error: `Comparison is full (max ${COMPARE_MAX}). Remove a system first.`,
    };
  }
  const saved = store.set(
    exists
      ? entries.filter((entry) => entry.id64 !== snapshot.id64)
      : [
          ...entries,
          {
            ...snapshot,
            captured_at:
              snapshot.captured_at === undefined
                ? new Date().toISOString()
                : snapshot.captured_at,
          },
        ],
  );
  return {
    selected: get(store).value.some((entry) => entry.id64 === snapshot.id64),
    error: saved ? null : saveError,
  };
}

export function togglePin(
  store: PersistedStore<PinnedEntry[]>,
  snapshot: CompareEntry,
): SelectionResult {
  const entries = get(store).value;
  const exists = entries.some((entry) => entry.id64 === snapshot.id64);
  const coords =
    snapshot.coords && typeof snapshot.coords === 'object'
      ? (snapshot.coords as Record<string, unknown>)
      : {};
  const pin: PinnedEntry = {
    ...snapshot,
    captured_at:
      snapshot.captured_at === undefined
        ? new Date().toISOString()
        : snapshot.captured_at,
    x: coords.x ?? null,
    y: coords.y ?? null,
    z: coords.z ?? null,
    economy: snapshot.economy_suggestion ?? snapshot.primaryEconomy ?? null,
    pinned_at: new Date().toISOString(),
  };
  const saved = store.set(
    exists
      ? entries.filter((entry) => entry.id64 !== snapshot.id64)
      : [pin, ...entries],
  );
  return {
    selected: get(store).value.some((entry) => entry.id64 === snapshot.id64),
    error: saved ? null : saveError,
  };
}
