import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  evaluateV3Plan,
  type PlanEvaluationV1,
  type PlanRequestV1,
} from '$lib/api';

const payload: PlanRequestV1 = {
  system_id64: '9007199254740993',
  bodies: [],
  sites: [
    {
      site_id: 'primary',
      facility_id: 'orbital_outpost_civilian',
      body_ref: null,
      slot_kind: 'orbital',
      status: 'planned',
    },
  ],
};

const json = (body: unknown, status = 200, retryAfter?: string) => {
  const headers = new Headers({ 'content-type': 'application/json' });
  if (retryAfter !== undefined) headers.set('Retry-After', retryAfter);
  return new Response(JSON.stringify(body), { status, headers });
};

afterEach(() => vi.restoreAllMocks());

describe('planner evaluation transport failures', () => {
  it('returns successful evaluation data unchanged through the generated transport', async () => {
    const result: PlanEvaluationV1 = {
      rulebook_release: 'fixture',
      system_id64: payload.system_id64,
      now: { t2: 0, t3: 0 },
      after_plan: { t2: 0, t3: 0 },
      next_port_cost: { t2: 3, t3: 0 },
      economies: { now: [], after_plan: [], notes: [] },
      steps: [],
      plan_checks: [],
      summary: { OK: 0, BLOCKED: 0, NEEDS_INFO: 0, COUNTED: 0, IGNORED: 0 },
      explanations: {},
    };
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(result));
    const controller = new AbortController();

    await expect(evaluateV3Plan(payload, controller.signal)).resolves.toEqual(
      result,
    );

    expect(fetch).toHaveBeenCalledOnce();
    const request = fetch.mock.calls[0][0] as Request;
    expect(new URL(request.url).pathname).toBe('/api/v1/plan/evaluate');
    expect(request.method).toBe('POST');
    expect(request.credentials).toBe('include');
    expect(await request.json()).toEqual(payload);
    controller.abort();
    expect(request.signal.aborted).toBe(true);
  });

  it('exposes status zero and no retry delay for a network failure', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockRejectedValue(new TypeError('Network unavailable'));

    const error = await evaluateV3Plan(payload).catch(
      (error: unknown) => error,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, retryAfterSeconds: null });
    expect(fetch).toHaveBeenCalledOnce();
  });

  it.each([
    { status: 429, header: '5', seconds: 5 },
    { status: 429, header: '0', seconds: 0 },
    { status: 503, header: '12', seconds: 12 },
    { status: 500, header: undefined, seconds: null },
  ])(
    'exposes status $status and Retry-After $header without retrying automatically',
    async ({ status, header, seconds }) => {
      const body = { detail: 'Please try again.' };
      const fetch = vi
        .spyOn(globalThis, 'fetch')
        .mockResolvedValue(json(body, status, header));

      const error = await evaluateV3Plan(payload).catch(
        (error: unknown) => error,
      );

      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ status, body, retryAfterSeconds: seconds });
      expect(fetch).toHaveBeenCalledOnce();
    },
  );

  it.each([
    '',
    'later',
    '-5',
    '1.5',
    '5 seconds',
    '9007199254740992',
    'Sun, 01 Nov 2026 12:00:00 GMT',
  ])('ignores unsupported Retry-After value %j', async (header) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      json({ detail: 'Unavailable' }, 503, header),
    );

    await expect(evaluateV3Plan(payload)).rejects.toMatchObject({
      status: 503,
      retryAfterSeconds: null,
    });
  });

  it('preserves the 400 plan_invalid body and issue messages', async () => {
    const invalid = {
      error: 'plan_invalid',
      issues: [
        {
          code: 'UNKNOWN_BODY',
          site_id: 'primary',
          message: 'Choose a body from this system.',
        },
      ],
    };
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(invalid, 400));

    const error = await evaluateV3Plan(payload).catch(
      (error: unknown) => error,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 400,
      body: invalid,
      retryAfterSeconds: null,
    });
  });
});
