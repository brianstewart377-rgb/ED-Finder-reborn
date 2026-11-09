import { QueryClient } from '@tanstack/svelte-query';
import type { Id64 } from '$lib/domain/id64';
import type {
  ArchetypeExplanationIdentity,
  ArchetypeRankingsRequest,
  CatalogueViewportRequest,
  DistanceAnchor,
  ExploreSearchRequest,
  RatingsIdentity,
} from './client';

/**
 * Stable fingerprint of the committed anchor's identity. Two anchors with the
 * same coordinates but different identities (a renamed system, or two
 * catalogue entries at one position) must never share cached normalized rows,
 * because the facade stamps every row's `distance_reference` from it.
 */
const anchorIdentity = (anchor: DistanceAnchor | null | undefined): string =>
  anchor ? `${anchor.id64}:${anchor.name}` : '';

export const queryKeys = {
  all: ['ed-finder'] as const,
  auth: () => [...queryKeys.all, 'auth'] as const,
  galaxyImpact: (accountId: string) =>
    [...queryKeys.auth(), accountId, 'galaxy-impact'] as const,
  health: () => [...queryKeys.all, 'health'] as const,
  system: (id64: Id64) => [...queryKeys.all, 'system', id64] as const,
  /** Ratings V4 economy potentials for one system. */
  systemRatings: (id64: Id64) =>
    [...queryKeys.all, 'system', id64, 'ratings'] as const,
  /**
   * The replayed explanation, partitioned by the generation identity of the
   * ratings on screen so a republish never reuses a cached explanation.
   */
  systemRatingExplanation: (id64: Id64, identity: RatingsIdentity | null) =>
    [
      ...queryKeys.all,
      'system',
      id64,
      'ratings',
      'explanation',
      identity?.derivedGenerationId ?? null,
      identity?.publicationSequence ?? null,
    ] as const,
  autocomplete: (query: string) =>
    [...queryKeys.all, 'explore', 'autocomplete', query] as const,
  /**
   * Any-mode results: the wire request, the facade page size (distinct from
   * the clamped wire `size` near the 10,000 ceiling, and recorded in the
   * normalized envelope) and the committed anchor identity.
   */
  explore: (
    request: ExploreSearchRequest,
    anchor: DistanceAnchor | null = null,
    pageSize: number | null = null,
  ) =>
    [
      ...queryKeys.all,
      'explore',
      'results',
      JSON.stringify(request),
      anchorIdentity(anchor),
      pageSize ?? request.size ?? null,
    ] as const,
  /** Selected-archetype rankings: archetype, floor, facade page size, offset. */
  rankings: (request: ArchetypeRankingsRequest) =>
    [
      ...queryKeys.all,
      'explore',
      'rankings',
      request.archetype,
      request.min_score,
      request.page_size,
      request.offset,
    ] as const,
  /** Every identity sent to the explanation gate, including hash-only rebuilds. */
  archetypeExplanation: (
    id64: Id64,
    expected: ArchetypeExplanationIdentity | null,
  ) =>
    [
      ...queryKeys.all,
      'explore',
      'archetype-explanation',
      id64,
      expected?.derived_generation_id ?? null,
      expected?.archetype_version ?? null,
      expected?.archetype_manifest_sha256 ?? null,
    ] as const,
  catalogueViewport: (request: CatalogueViewportRequest) =>
    [
      ...queryKeys.all,
      'map',
      'catalogue-stars',
      JSON.stringify(request),
    ] as const,
  commanderTravel: (accountId: string, request: CatalogueViewportRequest) =>
    [
      ...queryKeys.all,
      'map',
      'commander-history',
      accountId,
      JSON.stringify(request),
    ] as const,
  compare: (id64s: readonly Id64[]) =>
    [...queryKeys.all, 'compare', ...id64s] as const,
  watchlist: (accountId: string | null, syncKey: string) =>
    [...queryKeys.all, 'watchlist', accountId, syncKey] as const,
};

/** The one application query client; feature providers should reuse this root. */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 300_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
    mutations: { retry: 0 },
  },
});
