import type { QueryClient, QueryKey } from '@tanstack/svelte-query';

type Lease = {
  queryKey: QueryKey;
  gcTime: number;
  waitingForRequest: boolean;
};

/**
 * Keeps the current/last displayed Finder cache entries available while their
 * route is alive, including a suspended layout. Payloads remain in TanStack;
 * this owner retains only query identities and their normal disposal policy.
 * There are no observers, subscriptions, timers or requests of its own.
 */
export class ExploreResultCache {
  private readonly leases = new Map<string, Lease>();
  private retained = new Set<string>();
  private releaseQueued = false;
  private disposed = false;

  constructor(private readonly client: QueryClient) {}

  /** Register before creating/updating the workspace's existing observer. */
  gcTime(queryKey: QueryKey): number {
    const options = this.client.defaultQueryOptions({ queryKey });
    if (this.disposed) return options.gcTime ?? 300_000;
    if (!this.leases.has(options.queryHash)) {
      this.leases.set(options.queryHash, {
        queryKey,
        gcTime: options.gcTime ?? 300_000,
        waitingForRequest: false,
      });
    }
    return Infinity;
  }

  /** Only the submitted query and the last displayed result need a lease. */
  retain(queryKeys: readonly QueryKey[]): void {
    if (this.disposed) return;
    this.retained = new Set(
      queryKeys.map(
        (queryKey) => this.client.defaultQueryOptions({ queryKey }).queryHash,
      ),
    );
    this.queueRelease();
  }

  /** Run after the route's children have released their existing observers. */
  dispose(): void {
    this.disposed = true;
    this.retained.clear();
    this.queueRelease();
  }

  private queueRelease(): void {
    if (this.releaseQueued) return;
    this.releaseQueued = true;
    queueMicrotask(() => {
      this.releaseQueued = false;
      const cache = this.client.getQueryCache();
      for (const [hash, lease] of this.leases) {
        if (this.retained.has(hash)) continue;
        const query = cache.get(hash);
        if (!query) {
          this.leases.delete(hash);
          continue;
        }
        // A replacement route may already own this same query. Never remove
        // an observed entry; its new route owner will release that lease.
        if (query.getObserversCount() > 0) {
          if (this.disposed) this.leases.delete(hash);
          continue;
        }
        // Unobserved requests that do not consume AbortSignal may still finish.
        // Preserve that work and release after settlement without another fetch.
        if (query.state.fetchStatus !== 'idle' && query.promise) {
          if (!lease.waitingForRequest) {
            lease.waitingForRequest = true;
            const settled = () => {
              lease.waitingForRequest = false;
              this.queueRelease();
            };
            void query.promise.then(settled, settled);
          }
          continue;
        }
        // TanStack only increases a query's gcTime. Recreate an inactive entry
        // with its exact state/options to restore ordinary GC without a fetch.
        const state = query.state;
        const options = {
          ...query.options,
          queryKey: lease.queryKey,
          gcTime: lease.gcTime,
        };
        cache.remove(query);
        cache.build(this.client, options, state);
        this.leases.delete(hash);
      }
    });
  }
}
