export interface ObservedFactsQueryInvalidator {
  invalidateQueries(options: { queryKey: readonly unknown[] }): unknown;
}

/**
 * Every query-key root that reads `listObservedFacts` for a system, across
 * every consumer. Four independent call sites each invented their own root
 * (`observed-facts`, `provenance-cockpit-observed-facts`,
 * `observed-facts-export`, `role-review-observed-facts`) with no shared
 * hook — a mutation invalidating only the roots its author happened to
 * know about left the other panels showing stale evidence after a create/
 * update/delete. Kept as an explicit list (not derived) so a 5th consumer
 * shows up here as a one-line addition instead of another chance to miss
 * a root.
 */
export const OBSERVED_FACTS_QUERY_KEY_ROOTS = [
  'observed-facts',
  'provenance-cockpit-observed-facts',
  'observed-facts-export',
  'role-review-observed-facts',
] as const;

/**
 * Invalidate every observed-facts read model for a system in one call.
 * TanStack Query's default `invalidateQueries` matching is prefix-based,
 * so `['root', systemId64]` matches a query keyed `['root', systemId64,
 * targetArchetype]` regardless of the trailing params each consumer adds.
 */
export function observedFactQueryKeys(systemId64: number): ReadonlyArray<readonly [string, number]> {
  return OBSERVED_FACTS_QUERY_KEY_ROOTS.map((root) => [root, systemId64] as const);
}

export function invalidateObservedFactQueries(
  queryClient: ObservedFactsQueryInvalidator,
  systemId64: number,
): void {
  for (const queryKey of observedFactQueryKeys(systemId64)) {
    void queryClient.invalidateQueries({ queryKey });
  }
}
