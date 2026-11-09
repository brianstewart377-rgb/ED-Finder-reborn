import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '$lib/api';

import {
  GALAXY_STAR_IDLE_DELAY_MS,
  GalaxyStarRequests,
} from './galaxy-star-requests';

function deferred() {
  let resolve!: (value: string) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<string>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

describe('bounded catalogue cell requests', () => {
  let requests: GalaxyStarRequests;
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    requests = new GalaxyStarRequests();
  });
  afterEach(() => {
    requests.dispose();
    expect(vi.getTimerCount()).toBe(0);
    vi.useRealTimers();
  });

  it('runs at most four downloads concurrently and fills released slots in queue order', async () => {
    const downloads = Array.from({ length: 6 }, () => deferred());
    const order: number[] = [];
    const results = downloads.map((download, index) =>
      requests.run(new AbortController().signal, () => {
        order.push(index);
        return download.promise;
      }),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(order).toEqual([0, 1, 2, 3]);
    downloads[1].resolve('second');
    await vi.advanceTimersByTimeAsync(0);
    expect(order).toEqual([0, 1, 2, 3, 4]);
    downloads[3].resolve('fourth');
    await vi.advanceTimersByTimeAsync(0);
    expect(order).toEqual([0, 1, 2, 3, 4, 5]);
    downloads.forEach((download) => download.resolve('complete'));
    await expect(Promise.all(results)).resolves.toEqual([
      'complete',
      'second',
      'complete',
      'fourth',
      'complete',
      'complete',
    ]);
  });

  it('waits for foreground queries and an idle interval, then starts nearest cells first', async () => {
    requests.setPaused(true);
    const downloads = Array.from({ length: 6 }, () => deferred());
    const order: number[] = [];
    const priorities = [30, 10, 20, 0, 10, 40];
    const results = downloads.map((download, index) =>
      requests.run(
        new AbortController().signal,
        () => {
          order.push(index);
          return download.promise;
        },
        priorities[index],
      ),
    );
    await vi.advanceTimersByTimeAsync(5_000);
    expect(order).toEqual([]);
    requests.setPaused(false);
    await vi.advanceTimersByTimeAsync(GALAXY_STAR_IDLE_DELAY_MS - 1);
    expect(order).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(order).toEqual([3, 1, 4, 2]);
    downloads.forEach((download) => download.resolve('complete'));
    await Promise.all(results);
    expect(order).toEqual([3, 1, 4, 2, 0, 5]);
  });

  it('stops filling active slots when foreground work resumes', async () => {
    const active = Array.from({ length: 4 }, () => deferred());
    const results = active.map((download) =>
      requests.run(new AbortController().signal, () => download.promise),
    );
    const download = vi.fn(async () => 'queued');
    const queued = requests.run(new AbortController().signal, download);
    await vi.advanceTimersByTimeAsync(0);
    requests.setPaused(true);
    active.forEach((request) => request.resolve('complete'));
    await Promise.all(results);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(download).not.toHaveBeenCalled();
    requests.setPaused(false);
    await vi.advanceTimersByTimeAsync(GALAXY_STAR_IDLE_DELAY_MS);
    await expect(queued).resolves.toBe('queued');
  });

  it('restarts the idle interval when the camera moves and cancels obsolete viewport work', async () => {
    requests.defer();
    const obsoleteController = new AbortController();
    const obsoleteDownload = vi.fn(async () => 'obsolete');
    const obsolete = requests
      .run(obsoleteController.signal, obsoleteDownload)
      .catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(GALAXY_STAR_IDLE_DELAY_MS - 1);
    obsoleteController.abort();
    requests.defer();
    const currentDownload = vi.fn(async () => 'current');
    const current = requests.run(new AbortController().signal, currentDownload);
    await vi.advanceTimersByTimeAsync(GALAXY_STAR_IDLE_DELAY_MS - 1);
    expect(obsoleteDownload).not.toHaveBeenCalled();
    expect(currentDownload).not.toHaveBeenCalled();
    await expect(obsolete).resolves.toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(1);
    await expect(current).resolves.toBe('current');
  });

  it('probes route availability once before using all four connection slots', async () => {
    requests = new GalaxyStarRequests({ probe: true });
    const downloads = Array.from({ length: 6 }, () => deferred());
    const order: number[] = [];
    const results = downloads.map((download, index) =>
      requests.run(new AbortController().signal, () => {
        order.push(index);
        return download.promise;
      }),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(order).toEqual([0]);
    downloads[0].resolve('available');
    await vi.advanceTimersByTimeAsync(0);
    expect(order).toEqual([0, 1, 2, 3, 4]);
    downloads.forEach((download) => download.resolve('complete'));
    await Promise.all(results);
    expect(order).toEqual([0, 1, 2, 3, 4, 5]);
  });

  it.each([404, 503])(
    'fails the layer once on HTTP %s without downloading queued or future cells',
    async (status) => {
      requests = new GalaxyStarRequests({ probe: true });
      const failure = new ApiError(status, '/api/map/systems', null);
      const download = vi.fn(async () => {
        throw failure;
      });
      const results = Array.from({ length: 16 }, () =>
        requests
          .run(new AbortController().signal, download)
          .catch((error: unknown) => error),
      );
      await expect(Promise.all(results)).resolves.toEqual(
        Array(16).fill(failure),
      );
      expect(download).toHaveBeenCalledOnce();
      requests.defer();
      requests.setPaused(true);
      requests.setPaused(false);
      await expect(
        requests.run(new AbortController().signal, download),
      ).rejects.toBe(failure);
      await vi.advanceTimersByTimeAsync(120_000);
      expect(download).toHaveBeenCalledOnce();
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it('aborts active cells and rejects the whole queue when an established route becomes unavailable', async () => {
    requests = new GalaxyStarRequests({ probe: true });
    await requests.run(new AbortController().signal, async () => 'available');
    const downloads = Array.from({ length: 6 }, () => deferred());
    const signals: AbortSignal[] = [];
    const results = downloads.map((download) =>
      requests
        .run(new AbortController().signal, (signal) => {
          signals.push(signal);
          return download.promise;
        })
        .catch((error: unknown) => error),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(signals).toHaveLength(4);
    const failure = new ApiError(503, '/api/map/systems', null);
    downloads[0].reject(failure);
    await expect(Promise.all(results)).resolves.toEqual(Array(6).fill(failure));
    expect(signals.every((signal) => signal.aborted)).toBe(true);
    downloads.forEach((download) => download.resolve('late completion'));
    await vi.advanceTimersByTimeAsync(120_000);
    expect(signals).toHaveLength(4);
  });

  it('does not latch a transport failure or a stale cancelled response as route unavailability', async () => {
    requests = new GalaxyStarRequests({ probe: true });
    const stale = deferred();
    const controller = new AbortController();
    const cancelled = requests
      .run(controller.signal, () => stale.promise)
      .catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    stale.reject(new ApiError(503, '/api/map/systems', null));
    await expect(cancelled).resolves.toMatchObject({ name: 'AbortError' });
    const failure = new ApiError(500, '/api/map/systems', null);
    await expect(
      requests.run(new AbortController().signal, async () => {
        throw failure;
      }),
    ).rejects.toBe(failure);
    await expect(
      requests.run(new AbortController().signal, async () => 'recovered'),
    ).resolves.toBe('recovered');
  });

  it('removes cancelled queued work and rejects already-aborted signals without downloading', async () => {
    const active = Array.from({ length: 4 }, () => deferred());
    const activeResults = active.map((download) =>
      requests.run(new AbortController().signal, () => download.promise),
    );
    const controller = new AbortController();
    const cancelledRequest = vi.fn(async () => 'cancelled');
    const cancelled = requests
      .run(controller.signal, cancelledRequest)
      .catch((error: unknown) => error);
    const nextRequest = vi.fn(async () => 'next');
    const next = requests.run(new AbortController().signal, nextRequest);
    controller.abort();
    await expect(cancelled).resolves.toMatchObject({ name: 'AbortError' });
    await expect(
      requests.run(controller.signal, cancelledRequest),
    ).rejects.toMatchObject({ name: 'AbortError' });
    active[0].resolve('released');
    await vi.advanceTimersByTimeAsync(0);
    expect(cancelledRequest).not.toHaveBeenCalled();
    expect(nextRequest).toHaveBeenCalledOnce();
    await expect(next).resolves.toBe('next');
    active.forEach((download) => download.resolve('complete'));
    await Promise.all(activeResults);
  });

  it('aborts active transport but holds its slot until it settles', async () => {
    const active = Array.from({ length: 4 }, () => deferred());
    const controller = new AbortController();
    let transportSignal: AbortSignal | undefined;
    const cancelled = requests
      .run(controller.signal, (signal) => {
        transportSignal = signal;
        return active[0].promise;
      })
      .catch((error: unknown) => error);
    const others = active
      .slice(1)
      .map((download) =>
        requests.run(new AbortController().signal, () => download.promise),
      );
    const nextRequest = vi.fn(async () => 'next');
    const next = requests.run(new AbortController().signal, nextRequest);
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    expect(transportSignal?.aborted).toBe(true);
    await expect(cancelled).resolves.toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(0);
    expect(nextRequest).not.toHaveBeenCalled();
    active[0].resolve('transport settled');
    await vi.advanceTimersByTimeAsync(0);
    expect(nextRequest).toHaveBeenCalledOnce();
    active.forEach((download) => download.resolve('complete'));
    await Promise.all([...others, next]);
  });

  it('limits starts to 48 in a rolling minute and resumes only as earlier starts expire', async () => {
    const request = vi.fn(async () => 'system');
    const enqueue = (count: number) =>
      Array.from({ length: count }, () =>
        requests.run(new AbortController().signal, request),
      );
    await Promise.all(enqueue(24));
    expect(request).toHaveBeenCalledTimes(24);
    await vi.advanceTimersByTimeAsync(30_000);
    const second = enqueue(28);
    await vi.advanceTimersByTimeAsync(0);
    expect(request).toHaveBeenCalledTimes(48);
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(request).toHaveBeenCalledTimes(48);
    await vi.advanceTimersByTimeAsync(1);
    await Promise.all(second);
    expect(request).toHaveBeenCalledTimes(52);
    const third = enqueue(24);
    await vi.advanceTimersByTimeAsync(0);
    expect(request).toHaveBeenCalledTimes(72);
    await vi.advanceTimersByTimeAsync(29_999);
    expect(request).toHaveBeenCalledTimes(72);
    await vi.advanceTimersByTimeAsync(1);
    await Promise.all(third);
    expect(request).toHaveBeenCalledTimes(76);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('clears the rate-limit timer when the final queued request is cancelled', async () => {
    await Promise.all(
      Array.from({ length: 48 }, () =>
        requests.run(new AbortController().signal, async () => 'system'),
      ),
    );
    const controller = new AbortController();
    const download = vi.fn(async () => 'never');
    const queued = requests
      .run(controller.signal, download)
      .catch((error: unknown) => error);
    expect(vi.getTimerCount()).toBe(1);
    controller.abort();
    await expect(queued).resolves.toMatchObject({ name: 'AbortError' });
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(download).not.toHaveBeenCalled();
  });

  it('disposes queued and active work and never starts new requests after late completions', async () => {
    const active = Array.from({ length: 4 }, () => deferred());
    const signals: AbortSignal[] = [];
    const results = active.map((download) =>
      requests
        .run(new AbortController().signal, (signal) => {
          signals.push(signal);
          return download.promise;
        })
        .catch((error: unknown) => error),
    );
    const queuedDownload = vi.fn(async () => 'never');
    const queued = requests
      .run(new AbortController().signal, queuedDownload)
      .catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(0);
    requests.dispose();
    requests.dispose();
    expect(signals).toHaveLength(4);
    expect(signals.every((signal) => signal.aborted)).toBe(true);
    for (const result of [...results, queued]) {
      await expect(result).resolves.toMatchObject({ name: 'AbortError' });
    }
    await expect(
      requests.run(new AbortController().signal, queuedDownload),
    ).rejects.toMatchObject({ name: 'AbortError' });
    active.forEach((download) => download.resolve('late completion'));
    await vi.advanceTimersByTimeAsync(120_000);
    expect(queuedDownload).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('disposes a rate-limited queue without leaving a timer', async () => {
    await Promise.all(
      Array.from({ length: 48 }, () =>
        requests.run(new AbortController().signal, async () => 'system'),
      ),
    );
    const download = vi.fn(async () => 'never');
    const queued = requests
      .run(new AbortController().signal, download)
      .catch((error: unknown) => error);
    expect(vi.getTimerCount()).toBe(1);
    requests.dispose();
    await expect(queued).resolves.toMatchObject({ name: 'AbortError' });
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(download).not.toHaveBeenCalled();
  });

  it('releases capacity after rejected and synchronously thrown requests', async () => {
    const failure = new Error('offline');
    const results = Array.from({ length: 8 }, (_, index) =>
      requests
        .run(new AbortController().signal, () => {
          if (index % 2) throw failure;
          return Promise.reject(failure);
        })
        .catch((error: unknown) => error),
    );
    await expect(Promise.all(results)).resolves.toEqual(Array(8).fill(failure));
    await expect(
      requests.run(new AbortController().signal, async () => 'recovered'),
    ).resolves.toBe('recovered');
  });
});
