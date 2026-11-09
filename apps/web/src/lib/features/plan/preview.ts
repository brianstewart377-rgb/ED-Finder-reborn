import {
  evaluateV3Plan,
  type PlanEvaluationV1,
  type PlanRequestV1,
} from '$lib/api';

const PREVIEW_STARTS_PER_MINUTE = 24;
const WINDOW_MS = 60_000;
const CACHE_ENTRIES = 128;

type Evaluate = (
  request: PlanRequestV1,
  signal: AbortSignal,
) => Promise<PlanEvaluationV1>;

interface PreviewTask {
  key: string;
  request: PlanRequestV1;
  signal: AbortSignal;
  controller: AbortController;
  settled: boolean;
  resolve: (evaluation: PlanEvaluationV1) => void;
  reject: (error: unknown) => void;
  abort: () => void;
}

function aborted(): DOMException {
  return new DOMException('The preview was cancelled.', 'AbortError');
}

/**
 * One FIFO per application session. Preview traffic reserves 36 of the API's
 * 60 requests/minute for live edits, including when a picker mounts repeatedly.
 * A separate factory keeps tests independent without resetting production state.
 */
export function createPlanPreviewEvaluator(evaluate: Evaluate): Evaluate {
  const cache = new Map<string, PlanEvaluationV1>();
  const queue: PreviewTask[] = [];
  let starts: number[] = [];
  let running = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function clearTimer(): void {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  }

  function cached(key: string): PlanEvaluationV1 | undefined {
    const result = cache.get(key);
    if (result) {
      cache.delete(key);
      cache.set(key, result);
    }
    return result;
  }

  function finish(
    task: PreviewTask,
    evaluation?: PlanEvaluationV1,
    error?: unknown,
  ): void {
    if (task.settled) return;
    task.settled = true;
    task.signal.removeEventListener('abort', task.abort);
    if (evaluation) task.resolve(evaluation);
    else task.reject(error);
  }

  function drain(): void {
    clearTimer();
    if (running) return;
    while (queue.length) {
      const task = queue[0];
      if (task.settled || task.signal.aborted) {
        queue.shift();
        finish(task, undefined, aborted());
        continue;
      }
      const hit = cached(task.key);
      if (hit) {
        queue.shift();
        finish(task, hit);
        continue;
      }
      const now = Date.now();
      starts = starts.filter((time) => time > now - WINDOW_MS);
      if (starts.length >= PREVIEW_STARTS_PER_MINUTE) {
        timer = setTimeout(drain, Math.max(1, starts[0] + WINDOW_MS - now));
        return;
      }
      queue.shift();
      running = true;
      void Promise.resolve()
        .then(() => {
          if (task.signal.aborted) throw aborted();
          starts.push(Date.now());
          return evaluate(task.request, task.controller.signal);
        })
        .then((evaluation) => {
          if (!task.settled && !task.signal.aborted) {
            cache.set(task.key, evaluation);
            if (cache.size > CACHE_ENTRIES)
              cache.delete(cache.keys().next().value!);
            finish(task, evaluation);
          }
        })
        .catch((error: unknown) => finish(task, undefined, error))
        .finally(() => {
          running = false;
          drain();
        });
      return;
    }
  }

  return (request, signal) => {
    if (signal.aborted) return Promise.reject(aborted());
    const key = JSON.stringify(request);
    const hit = cached(key);
    if (hit) return Promise.resolve(hit);
    return new Promise<PlanEvaluationV1>((resolve, reject) => {
      const task: PreviewTask = {
        key,
        // The queued payload must remain identical to the cache key.
        request: JSON.parse(key) as PlanRequestV1,
        signal,
        controller: new AbortController(),
        settled: false,
        resolve,
        reject,
        abort: () => {
          task.controller.abort();
          finish(task, undefined, aborted());
          const index = queue.indexOf(task);
          if (index >= 0) queue.splice(index, 1);
          drain();
        },
      };
      signal.addEventListener('abort', task.abort, { once: true });
      queue.push(task);
      drain();
    });
  };
}

export const evaluatePlanPreview = createPlanPreviewEvaluator(evaluateV3Plan);
