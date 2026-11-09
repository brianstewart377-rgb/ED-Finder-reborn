import { QueryClient, QueryObserver } from '@tanstack/svelte-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ExploreResultCache } from './explore-result-cache';

const key = (page: number) =>
  ['ed-finder', 'explore', 'results', page] as const;

describe('Explore route result cache ownership', () => {
  let client: QueryClient;
  let cache: ExploreResultCache;

  beforeEach(() => {
    vi.useFakeTimers();
    client = new QueryClient({
      defaultOptions: { queries: { gcTime: 300_000, retry: false } },
    });
    cache = new ExploreResultCache(client);
  });

  afterEach(() => {
    cache.dispose();
    client.clear();
    vi.useRealTimers();
  });

  const flushRelease = async () => {
    await Promise.resolve();
    await Promise.resolve();
  };

  it('keeps stale results through a suspension longer than normal GC without another observer or request', async () => {
    const queryKey = key(1);
    const data = { results: [{ id64: '9007199254741001' }] };
    const fetch = vi.fn().mockResolvedValue(data);
    client.setQueryData(queryKey, data, { updatedAt: 1 });
    const options = {
      queryKey,
      queryFn: fetch,
      gcTime: cache.gcTime(queryKey),
      refetchOnMount: false,
    };
    const observer = new QueryObserver(client, options);
    const unsubscribe = observer.subscribe(() => undefined);
    cache.retain([queryKey]);
    await flushRelease();
    expect(client.getQueryCache().find({ queryKey })?.getObserversCount()).toBe(
      1,
    );
    unsubscribe();
    expect(client.getQueryCache().find({ queryKey })?.getObserversCount()).toBe(
      0,
    );
    await vi.advanceTimersByTimeAsync(600_000);
    expect(client.getQueryData(queryKey)).toBe(data);
    const remounted = new QueryObserver(client, options);
    const stopRemounted = remounted.subscribe(() => undefined);
    expect(remounted.getCurrentResult().data).toBe(data);
    expect(fetch).not.toHaveBeenCalled();
    expect(client.getQueryCache().find({ queryKey })?.getObserversCount()).toBe(
      1,
    );
    stopRemounted();
  });

  it('restores exact cached state/options and normal GC after the route children unmount', async () => {
    const queryKey = key(1);
    const fetch = vi.fn().mockResolvedValue({ results: [] });
    client.setQueryData(queryKey, { results: [] }, { updatedAt: 123 });
    const observer = new QueryObserver(client, {
      queryKey,
      queryFn: fetch,
      gcTime: cache.gcTime(queryKey),
      refetchOnMount: false,
      meta: { owner: 'finder' },
    });
    const unsubscribe = observer.subscribe(() => undefined);
    cache.retain([queryKey]);
    const before = client.getQueryCache().find({ queryKey })!;
    const state = before.state;
    cache.dispose();
    unsubscribe();
    await flushRelease();
    const restored = client.getQueryCache().find({ queryKey })!;
    expect(restored).not.toBe(before);
    expect(restored.state).toBe(state);
    expect(restored.options).toEqual({ ...before.options, gcTime: 300_000 });
    expect(restored.gcTime).toBe(300_000);
    expect(restored.getObserversCount()).toBe(0);
    expect(fetch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(299_999);
    expect(client.getQueryCache().find({ queryKey })).toBe(restored);
    await vi.advanceTimersByTimeAsync(1);
    expect(client.getQueryCache().find({ queryKey })).toBeUndefined();
  });

  it('bounds leased pages to the current query and last displayed results', async () => {
    const fetch = vi.fn();
    for (let page = 1; page <= 5; page += 1) {
      const queryKey = key(page);
      client.setQueryData(queryKey, { page });
      const observer = new QueryObserver(client, {
        queryKey,
        queryFn: fetch,
        gcTime: cache.gcTime(queryKey),
        refetchOnMount: false,
      });
      const unsubscribe = observer.subscribe(() => undefined);
      cache.retain([queryKey, key(page - 1)]);
      unsubscribe();
      await flushRelease();
      expect(
        client
          .getQueryCache()
          .getAll()
          .filter((query) => query.gcTime === Infinity),
      ).toHaveLength(Math.min(page, 2));
    }
    await vi.advanceTimersByTimeAsync(300_000);
    expect(
      client
        .getQueryCache()
        .getAll()
        .map((query) => query.queryKey),
    ).toEqual([key(4), key(5)]);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('uses configured per-query GC defaults and leaves a fresh visit free to refetch stale rows', async () => {
    const queryKey = key(1);
    client.setQueryDefaults(queryKey, { gcTime: 60_000 });
    client.setQueryData(queryKey, { page: 'old' }, { updatedAt: 1 });
    const oldObserver = new QueryObserver(client, {
      queryKey,
      gcTime: cache.gcTime(queryKey),
      enabled: false,
    });
    const unsubscribe = oldObserver.subscribe(() => undefined);
    cache.retain([queryKey]);
    unsubscribe();
    cache.dispose();
    await flushRelease();
    expect(client.getQueryCache().find({ queryKey })?.gcTime).toBe(60_000);
    const nextRoute = new ExploreResultCache(client);
    const fetch = vi.fn().mockResolvedValue({ page: 'fresh' });
    const nextObserver = new QueryObserver(client, {
      queryKey,
      queryFn: fetch,
      gcTime: nextRoute.gcTime(queryKey),
      staleTime: 0,
    });
    const stopNext = nextObserver.subscribe(() => undefined);
    await flushRelease();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(queryKey)).toEqual({ page: 'fresh' });
    stopNext();
    nextRoute.dispose();
    await flushRelease();
    expect(client.getQueryCache().find({ queryKey })?.gcTime).toBe(60_000);
  });

  it('does not remove a query already observed by the next route', async () => {
    const queryKey = key(1);
    client.setQueryData(queryKey, { results: [] });
    const first = new QueryObserver(client, {
      queryKey,
      gcTime: cache.gcTime(queryKey),
      enabled: false,
    });
    const stopFirst = first.subscribe(() => undefined);
    const nextRoute = new ExploreResultCache(client);
    const next = new QueryObserver(client, {
      queryKey,
      gcTime: nextRoute.gcTime(queryKey),
      enabled: false,
    });
    const stopNext = next.subscribe(() => undefined);
    const shared = client.getQueryCache().find({ queryKey });
    cache.dispose();
    stopFirst();
    await flushRelease();
    expect(client.getQueryCache().find({ queryKey })).toBe(shared);
    expect(shared?.getObserversCount()).toBe(1);
    stopNext();
    nextRoute.dispose();
    await flushRelease();
    expect(client.getQueryCache().find({ queryKey })?.gcTime).toBe(300_000);
  });

  it('allows an already-running unobserved request to finish before restoring GC', async () => {
    const queryKey = key(1);
    let finish!: (value: { page: number }) => void;
    const fetch = vi.fn(
      () =>
        new Promise<{ page: number }>((resolve) => {
          finish = resolve;
        }),
    );
    const observer = new QueryObserver(client, {
      queryKey,
      queryFn: fetch,
      gcTime: cache.gcTime(queryKey),
    });
    const unsubscribe = observer.subscribe(() => undefined);
    cache.retain([queryKey]);
    const inFlight = client.getQueryCache().find({ queryKey });
    unsubscribe();
    cache.dispose();
    await flushRelease();
    expect(client.getQueryCache().find({ queryKey })).toBe(inFlight);
    expect(inFlight?.state.fetchStatus).toBe('fetching');
    finish({ page: 1 });
    await vi.advanceTimersByTimeAsync(0);
    expect(client.getQueryData(queryKey)).toEqual({ page: 1 });
    expect(client.getQueryCache().find({ queryKey })?.gcTime).toBe(300_000);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(client.getQueryCache().find({ queryKey })?.getObserversCount()).toBe(
      0,
    );
  });

  it('preserves failed query state without retrying it when releasing a route', async () => {
    const queryKey = key(1);
    const error = new Error('Ranking temporarily unavailable');
    const fetch = vi.fn().mockRejectedValue(error);
    await expect(
      client.fetchQuery({
        queryKey,
        queryFn: fetch,
        gcTime: cache.gcTime(queryKey),
      }),
    ).rejects.toBe(error);
    const state = client.getQueryState(queryKey);
    cache.dispose();
    await flushRelease();
    expect(client.getQueryState(queryKey)).toBe(state);
    expect(client.getQueryState(queryKey)?.error).toBe(error);
    expect(client.getQueryCache().find({ queryKey })?.gcTime).toBe(300_000);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
