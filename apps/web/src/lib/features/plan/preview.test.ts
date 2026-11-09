import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PlanEvaluationV1, PlanRequestV1 } from '$lib/api';
import { createPlanPreviewEvaluator } from './preview';

const result: PlanEvaluationV1 = {
  rulebook_release: 'test',
  system_id64: '42',
  now: { t2: 0, t3: 0 },
  after_plan: { t2: 0, t3: 0 },
  next_port_cost: { t2: 3, t3: 6 },
  economies: { now: [], after_plan: [], notes: [] },
  steps: [],
  plan_checks: [],
  summary: { OK: 0, BLOCKED: 0, NEEDS_INFO: 0, COUNTED: 0, IGNORED: 0 },
  explanations: {},
};

function request(index = 0): PlanRequestV1 {
  return {
    system_id64: '42',
    bodies: [],
    sites: [
      {
        site_id: `preview-${index}`,
        facility_id: 'outpost',
        body_ref: null,
        slot_kind: 'orbital',
        status: 'planned',
        name: '',
      },
    ],
  };
}

afterEach(() => vi.useRealTimers());

describe('bounded API cost previews', () => {
  it('reuses exact successful requests and does not cache failures', async () => {
    const api = vi.fn().mockResolvedValue(result);
    const evaluate = createPlanPreviewEvaluator(api);
    const signal = new AbortController().signal;
    await expect(evaluate(request(), signal)).resolves.toBe(result);
    await expect(evaluate(request(), signal)).resolves.toBe(result);
    expect(api).toHaveBeenCalledTimes(1);
    api.mockRejectedValueOnce(new Error('offline'));
    await expect(evaluate(request(1), signal)).rejects.toThrow('offline');
    await expect(evaluate(request(1), signal)).resolves.toBe(result);
    expect(api).toHaveBeenCalledTimes(3);
  });

  it('aborts queued work before it reaches the network and keeps FIFO ordering', async () => {
    let completeFirst!: (value: PlanEvaluationV1) => void;
    const api = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<PlanEvaluationV1>((resolve) => {
            completeFirst = resolve;
          }),
      )
      .mockResolvedValue(result);
    const evaluate = createPlanPreviewEvaluator(api);
    const first = evaluate(request(0), new AbortController().signal);
    const controller = new AbortController();
    const queued = evaluate(request(1), controller.signal);
    const third = evaluate(request(2), new AbortController().signal);
    const rejected = expect(queued).rejects.toMatchObject({
      name: 'AbortError',
    });
    controller.abort();
    await rejected;
    expect(api).toHaveBeenCalledTimes(1);
    completeFirst(result);
    await Promise.all([first, third]);
    expect(api.mock.calls.map(([body]) => body.sites[0].site_id)).toEqual([
      'preview-0',
      'preview-2',
    ]);
  });

  it('aborts in-flight transport and rejects already-aborted callers even on cache hits', async () => {
    let transportSignal!: AbortSignal;
    const api = vi
      .fn()
      .mockImplementationOnce(
        (_request: PlanRequestV1, signal: AbortSignal) => {
          transportSignal = signal;
          return new Promise((_resolve, reject) =>
            signal.addEventListener('abort', () =>
              reject(new DOMException('cancelled', 'AbortError')),
            ),
          );
        },
      )
      .mockResolvedValue(result);
    const evaluate = createPlanPreviewEvaluator(api);
    const controller = new AbortController();
    const pending = evaluate(request(), controller.signal);
    const rejected = expect(pending).rejects.toMatchObject({
      name: 'AbortError',
    });
    await Promise.resolve();
    controller.abort();
    await rejected;
    expect(transportSignal.aborted).toBe(true);
    await evaluate(request(), new AbortController().signal);
    await expect(evaluate(request(), controller.signal)).rejects.toMatchObject({
      name: 'AbortError',
    });
    expect(api).toHaveBeenCalledTimes(2);
  });

  it('defers the 25th start until the rolling minute expires while cache hits remain available', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const api = vi.fn().mockResolvedValue(result);
    const evaluate = createPlanPreviewEvaluator(api);
    const signal = new AbortController().signal;
    for (let index = 0; index < 24; index++)
      await evaluate(request(index), signal);
    const next = evaluate(request(24), signal);
    await evaluate(request(0), signal);
    await vi.advanceTimersByTimeAsync(59_999);
    expect(api).toHaveBeenCalledTimes(24);
    await vi.advanceTimersByTimeAsync(1);
    await next;
    expect(api).toHaveBeenCalledTimes(25);
  });

  it('cancels a rate-limited queued request without leaving a timer or issuing a call', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const api = vi.fn().mockResolvedValue(result);
    const evaluate = createPlanPreviewEvaluator(api);
    const signal = new AbortController().signal;
    for (let index = 0; index < 24; index++)
      await evaluate(request(index), signal);
    const controller = new AbortController();
    const pending = evaluate(request(24), controller.signal);
    const rejected = expect(pending).rejects.toMatchObject({
      name: 'AbortError',
    });
    controller.abort();
    await rejected;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(api).toHaveBeenCalledTimes(24);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('keeps at most 128 successful requests, evicting the least recently used entry', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const api = vi.fn().mockResolvedValue(result);
    const evaluate = createPlanPreviewEvaluator(api);
    const signal = new AbortController().signal;
    for (let index = 0; index < 128; index++) {
      if (index && index % 24 === 0) await vi.advanceTimersByTimeAsync(60_000);
      await evaluate(request(index), signal);
    }
    await evaluate(request(0), signal);
    await evaluate(request(128), signal);
    expect(api).toHaveBeenCalledTimes(129);
    await evaluate(request(0), signal);
    expect(api).toHaveBeenCalledTimes(129);
    await evaluate(request(1), signal);
    expect(api).toHaveBeenCalledTimes(130);
  });
});
