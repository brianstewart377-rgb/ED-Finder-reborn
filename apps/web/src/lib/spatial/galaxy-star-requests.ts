import { ApiError } from '$lib/api';

export const GALAXY_STAR_IDLE_DELAY_MS = 150;

type RequestJob = {
  priority: number;
  start: () => void;
  cancel: () => void;
  fail: (error: Error) => void;
};

/** Background-only downloads bounded by concurrency and the route's 60/minute budget. */
export class GalaxyStarRequests {
  private active = 0;
  private starts: number[] = [];
  private queue: RequestJob[] = [];
  private jobs = new Set<RequestJob>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private disposed = false;
  private paused = false;
  private resumeAt = 0;
  private available = false;
  private unavailable: ApiError | undefined;

  constructor(private readonly options: { probe?: boolean } = {}) {}

  /** Foreground queries own the connection budget until they settle. */
  setPaused(paused: boolean): void {
    if (this.paused === paused) return;
    this.paused = paused;
    if (paused) this.drain();
    else this.defer();
  }

  /** Give a settled camera's foreground requests time to enter the query cache. */
  defer(): void {
    this.resumeAt = Date.now() + GALAXY_STAR_IDLE_DELAY_MS;
    this.drain();
  }

  run<T>(
    signal: AbortSignal,
    request: (signal: AbortSignal) => Promise<T>,
    priority = 0,
  ): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const controller = new AbortController();
      let cancelled = false;
      const cleanup = () => {
        signal.removeEventListener('abort', job.cancel);
        this.jobs.delete(job);
      };
      const job = {
        priority,
        cancel: () =>
          job.fail(new DOMException('Cell request cancelled', 'AbortError')),
        fail: (error: Error) => {
          if (cancelled) return;
          cancelled = true;
          this.queue = this.queue.filter((queued) => queued !== job);
          controller.abort();
          cleanup();
          reject(error);
          this.drain();
        },
        start: () => {
          this.active += 1;
          this.starts.push(Date.now());
          void Promise.resolve()
            .then(() => {
              if (controller.signal.aborted) {
                throw new DOMException('Cell request cancelled', 'AbortError');
              }
              return request(controller.signal);
            })
            .then(
              (result) => {
                if (!cancelled) this.available = true;
                resolve(result);
              },
              (error: unknown) => {
                if (
                  !cancelled &&
                  error instanceof ApiError &&
                  (error.status === 404 || error.status === 503)
                ) {
                  // A missing route/generation applies to the whole layer, not
                  // individual cells. Do not probe it again as the camera moves.
                  this.unavailable = error;
                  for (const pending of [...this.jobs]) pending.fail(error);
                }
                reject(error);
              },
            )
            .finally(() => {
              cleanup();
              // An aborted transport may take time to settle. Keep its slot
              // occupied until then so cancellation cannot exceed concurrency.
              this.active -= 1;
              this.drain();
            });
        },
      };
      if (signal.aborted || this.disposed) job.cancel();
      else if (this.unavailable) job.fail(this.unavailable);
      else {
        signal.addEventListener('abort', job.cancel, { once: true });
        this.jobs.add(job);
        this.queue.push(job);
        this.drain();
      }
    });
  }

  private drain(): void {
    clearTimeout(this.timer);
    this.timer = undefined;
    if (this.disposed || this.paused || this.unavailable || !this.queue.length)
      return;
    const now = Date.now();
    if (now < this.resumeAt) {
      this.timer = setTimeout(() => this.drain(), this.resumeAt - now);
      return;
    }
    this.starts = this.starts.filter((start) => now - start < 60_000);
    // Stable sorting preserves enqueue order for equally distant cells.
    this.queue.sort((left, right) => left.priority - right.priority);
    const concurrency = this.options.probe && !this.available ? 1 : 4;
    while (
      this.queue.length &&
      this.active < concurrency &&
      this.starts.length < 48
    ) {
      this.queue.shift()!.start();
    }
    if (this.queue.length && this.starts.length >= 48) {
      this.timer = setTimeout(
        () => this.drain(),
        60_001 - (Date.now() - this.starts[0]),
      );
    }
  }

  dispose(): void {
    this.disposed = true;
    clearTimeout(this.timer);
    this.timer = undefined;
    for (const job of [...this.jobs]) job.cancel();
  }
}
