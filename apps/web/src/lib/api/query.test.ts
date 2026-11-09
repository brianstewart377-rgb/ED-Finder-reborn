import { describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/svelte-query';
import { queryClient, queryKeys } from './query';
import { parseId64 } from '$lib/domain/id64';

describe('Svelte Query application contract', () => {
  it('uses the accepted root defaults', () => {
    expect(queryClient.getDefaultOptions()).toMatchObject({
      queries: {
        staleTime: 30_000,
        gcTime: 300_000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
      mutations: { retry: 0 },
    });
  });

  it('keeps canonical Id64 in query keys', () => {
    expect(queryKeys.system(parseId64('9007199254740993'))).toEqual([
      'ed-finder',
      'system',
      '9007199254740993',
    ]);
  });

  it('isolates watchlists by both account and sync credential', () => {
    expect(queryKeys.watchlist('account-a', 'sync-a')).not.toEqual(
      queryKeys.watchlist('account-b', 'sync-a'),
    );
    expect(queryKeys.watchlist('account-a', 'sync-a')).not.toEqual(
      queryKeys.watchlist('account-a', 'sync-b'),
    );
    expect(queryKeys.watchlist(null, 'sync-a')).not.toEqual(
      queryKeys.watchlist('account-a', 'sync-a'),
    );
  });
});

describe('Finder ranking query keys', () => {
  const request = {
    reference_coords: { x: 1, y: 2, z: 3 },
    filters: { distance: { min: 0, max: 500 }, economy: 'any' as const },
    sort_by: 'distance',
    size: 50,
    from: 0,
  };

  it('partitions Any results by the committed anchor identity, not only its coordinates', () => {
    const first = queryKeys.explore(request, {
      id64: parseId64('158872029'),
      name: 'HD 38179',
    });
    const renamed = queryKeys.explore(request, {
      id64: parseId64('158872029'),
      name: 'HD 38179 (renamed)',
    });
    const twin = queryKeys.explore(request, {
      id64: parseId64('158872030'),
      name: 'HD 38179',
    });
    expect(first).not.toEqual(renamed);
    expect(first).not.toEqual(twin);
    expect(first).toEqual(
      queryKeys.explore(request, {
        id64: parseId64('158872029'),
        name: 'HD 38179',
      }),
    );
    expect(queryKeys.explore(request)).toEqual(
      queryKeys.explore(request, null),
    );
    expect(queryKeys.explore(request)).not.toEqual(first);
  });

  it('keys Any results by page size and offset through the wire request', () => {
    expect(queryKeys.explore({ ...request, size: 1 })).not.toEqual(
      queryKeys.explore(request),
    );
    expect(queryKeys.explore({ ...request, from: 2 })).not.toEqual(
      queryKeys.explore(request),
    );
  });

  it('keys selected rankings by archetype, floor, facade page size and offset', () => {
    const base = {
      archetype: 'flexible' as const,
      min_score: 60 as const,
      page_size: 1,
      offset: 0,
    };
    expect(queryKeys.rankings(base)).toEqual([
      'ed-finder',
      'explore',
      'rankings',
      'flexible',
      60,
      1,
      0,
    ]);
    expect(queryKeys.rankings({ ...base, page_size: 50 })).not.toEqual(
      queryKeys.rankings(base),
    );
    expect(queryKeys.rankings({ ...base, min_score: 88 })).not.toEqual(
      queryKeys.rankings(base),
    );
    expect(queryKeys.rankings({ ...base, offset: 1 })).not.toEqual(
      queryKeys.rankings(base),
    );
    expect(queryKeys.rankings({ ...base, archetype: 'paradise' })).not.toEqual(
      queryKeys.rankings(base),
    );
  });

  it('keys Any results by the facade page size even when the clamped wire request is identical', () => {
    const clamped = {
      galaxy_wide: true,
      sort_by: 'development',
      size: 25,
      from: 9_975,
    };
    expect(queryKeys.explore(clamped, null, 50)).not.toEqual(
      queryKeys.explore(clamped, null, 25),
    );
    expect(queryKeys.explore(clamped, null, 25)).toEqual(
      queryKeys.explore(clamped),
    );
  });
});

describe('Finder archetype explanation query keys', () => {
  const id64 = parseId64('9007199254740993');
  const expected = {
    derived_generation_id: 'generation-1',
    archetype_version: 'archetype-v1',
    archetype_manifest_sha256: 'manifest-a',
  };

  it('partitions by the system and every identity sent to the endpoint', () => {
    const key = queryKeys.archetypeExplanation(id64, expected);
    expect(key).toEqual([
      'ed-finder',
      'explore',
      'archetype-explanation',
      id64,
      'generation-1',
      'archetype-v1',
      'manifest-a',
    ]);
    expect(
      queryKeys.archetypeExplanation(parseId64('42'), expected),
    ).not.toEqual(key);
    for (const field of Object.keys(expected) as (keyof typeof expected)[]) {
      expect(
        queryKeys.archetypeExplanation(id64, {
          ...expected,
          [field]: 'changed',
        }),
      ).not.toEqual(key);
    }
    expect(queryKeys.archetypeExplanation(id64, null)).not.toEqual(key);
  });

  it('fetches again for a manifest-only rebuild while the previous explanation is still fresh', async () => {
    const client = new QueryClient({
      defaultOptions: queryClient.getDefaultOptions(),
    });
    const queryFn = vi.fn(async () => ({ kind: 'explained' }));
    try {
      const original = {
        queryKey: queryKeys.archetypeExplanation(id64, expected),
        queryFn,
      };
      await client.fetchQuery(original);
      await client.fetchQuery(original);
      expect(queryFn).toHaveBeenCalledTimes(1);

      await client.fetchQuery({
        queryKey: queryKeys.archetypeExplanation(id64, {
          ...expected,
          archetype_manifest_sha256: 'manifest-b',
        }),
        queryFn,
      });
      expect(queryFn).toHaveBeenCalledTimes(2);
    } finally {
      client.clear();
    }
  });
});
