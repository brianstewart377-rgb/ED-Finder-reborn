import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  evaluateV3Plan,
  getPlanSystem,
  getV3PlanCatalogue,
  listV3PlanBodies,
} from './client';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
afterEach(() => vi.restoreAllMocks());

describe('V3 planner facade', () => {
  it('uses the generated credentialed routes with exact string IDs and cancellation', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        json({ facilities: [], rulebook_release: 'fixture' }),
      )
      .mockResolvedValueOnce(
        json({ system_id64: '9007199254740993', bodies: [], belts: [] }),
      )
      .mockResolvedValueOnce(json({ id64: '9007199254740993', name: 'Exact' }));
    const controller = new AbortController();
    await getV3PlanCatalogue(controller.signal);
    await expect(
      listV3PlanBodies('9007199254740993', controller.signal),
    ).resolves.toMatchObject({ system_id64: '9007199254740993', belts: [] });
    await getPlanSystem('9007199254740993', controller.signal);
    const requests = fetch.mock.calls.map(([request]) => request as Request);
    expect(requests.map((request) => new URL(request.url).pathname)).toEqual([
      '/api/v1/plan/catalogue',
      '/api/v1/plan/systems/9007199254740993/bodies',
      '/api/v1/systems/9007199254740993',
    ]);
    expect(requests.every((request) => request.credentials === 'include')).toBe(
      true,
    );
    controller.abort();
    expect(requests.every((request) => request.signal.aborted)).toBe(true);
  });

  it('preserves evaluation inputs and PlanInvalid issue messages for the summary', async () => {
    const body = {
      system_id64: '9007199254740993',
      sites: [],
      bodies: [
        {
          body_ref: 'belt:12',
          orbital_slots: null,
          surface_slots: 0,
          is_landable: false,
          is_ringed: true,
        },
      ],
    };
    const invalid = {
      error: 'plan_invalid',
      issues: [
        {
          code: 'BAD_FACILITY',
          site_id: 'one',
          message: 'Choose a catalogue facility.',
        },
      ],
    };
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(json(invalid, 400));
    const error = await evaluateV3Plan(body).catch((error: unknown) => error);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 400, body: invalid });
    const request = fetch.mock.calls[0][0] as Request;
    expect(request.method).toBe('POST');
    expect(new URL(request.url).pathname).toBe('/api/v1/plan/evaluate');
    expect(await request.json()).toEqual(body);
  });
});
