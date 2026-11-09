import { describe, expect, it, vi } from 'vitest';
import { OBSERVED_FACTS_QUERY_KEY_ROOTS, invalidateObservedFactQueries } from './observedFactsQueryKeys';

describe('observedFactsQueryKeys', () => {
  it('lists every known observed-facts query-key root', () => {
    expect(OBSERVED_FACTS_QUERY_KEY_ROOTS).toEqual([
      'observed-facts',
      'provenance-cockpit-observed-facts',
      'observed-facts-export',
      'role-review-observed-facts',
    ]);
  });

  it('invalidates every root for the given system, prefix-keyed', () => {
    const invalidateQueries = vi.fn();
    const queryClient = { invalidateQueries };

    invalidateObservedFactQueries(queryClient, 123);

    expect(invalidateQueries).toHaveBeenCalledTimes(OBSERVED_FACTS_QUERY_KEY_ROOTS.length);
    for (const root of OBSERVED_FACTS_QUERY_KEY_ROOTS) {
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: [root, 123] });
    }
  });
});
