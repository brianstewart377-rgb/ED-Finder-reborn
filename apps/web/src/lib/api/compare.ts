import { parseId64 } from '$lib/domain/id64';
import {
  SNAPSHOT_SOL_ID64,
  snapshotRankingIdentity,
  type CompareEntry,
} from '$lib/persistence/storage';
import { isArchetypeKey } from '$lib/features/explore/archetypes';
import { getSystemArchetypes, normalizeRankingProvenance } from './client';

/** Refresh one saved candidate with one read of the published Finder product. */
export async function refreshCompareSnapshot(
  previous: CompareEntry,
  signal?: AbortSignal,
): Promise<CompareEntry> {
  const data = await getSystemArchetypes(previous.id64, signal);
  // The configured SDK transport preserves wire identifiers as decimal strings.
  const rawId: unknown = data.id64;
  const id64 = parseId64(
    typeof rawId === 'number' && Number.isSafeInteger(rawId)
      ? String(rawId)
      : (rawId as string),
  );
  if (id64 !== previous.id64) throw new Error('Comparison identity mismatch');
  const identity = snapshotRankingIdentity(previous);
  const selected =
    identity.score_kind === 'selected_fit' &&
    isArchetypeKey(identity.selected_archetype)
      ? data.archetypes[identity.selected_archetype]
      : null;
  const capturedAt = new Date().toISOString();
  const coordsKnown =
    [data.coords.x, data.coords.y, data.coords.z].every(Number.isFinite) &&
    (id64 === SNAPSHOT_SOL_ID64 ||
      [data.coords.x, data.coords.y, data.coords.z].some(
        (value) => value !== 0,
      ));
  const solDistance = identity.distance_reference?.id64 === SNAPSHOT_SOL_ID64;
  // An anchor's coordinates are not in a saved reference. Keep that measurement
  // with its original capture time instead of silently switching it to Sol.
  return {
    id64,
    name: data.name,
    coords: coordsKnown ? data.coords : { x: null, y: null, z: null },
    ...data.facts,
    main_star_type: data.main_star_class ?? null,
    primary_archetype: data.primary_archetype,
    secondary_archetype: data.secondary_archetype ?? null,
    archetype_confidence: data.archetype_confidence,
    overall_development_potential: data.overall_development_potential,
    best_tier: data.best_tier,
    archetype_score: data.overall_development_potential,
    score_kind: identity.score_kind,
    selected_archetype: identity.selected_archetype,
    ranking_score:
      identity.score_kind === 'overall_potential'
        ? data.overall_development_potential
        : (selected?.score ?? null),
    ranking_tier:
      identity.score_kind === 'overall_potential'
        ? data.best_tier
        : (selected?.tier ?? null),
    ranking_provenance: normalizeRankingProvenance(data.ranking),
    distance_reference: identity.distance_reference,
    distance: solDistance
      ? coordsKnown
        ? data.distance_to_sol
        : null
      : (previous.distance ?? null),
    distance_captured_at: solDistance
      ? capturedAt
      : previous.distance_captured_at !== undefined
        ? previous.distance_captured_at
        : previous.captured_at !== undefined
          ? previous.captured_at
          : (previous.pinned_at ?? null),
    captured_at: capturedAt,
  };
}
