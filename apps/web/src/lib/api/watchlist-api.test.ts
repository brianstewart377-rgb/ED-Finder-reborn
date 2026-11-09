import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseId64 } from '$lib/domain/id64';
import {
  ApiError,
  addWatchlist,
  getWatchlist,
  removeWatchlist,
} from './client';

const syncKey = 'watchlist-test-key-1234';
const id64 = parseId64('18446744073709551615');
const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });

afterEach(() => vi.restoreAllMocks());

describe('watchlist API facade', () => {
  it('loads the scoped list through the generated credentialed transport with lossless identifiers', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(
          `{"sync_key":"${syncKey}","watchlist":[{"system_id64":18446744073709551615,"name":"Far Reach","x":1,"y":2,"z":3,"population":0,"is_colonised":false,"added_at":"2026-09-21T12:00:00Z","archetype_score":87,"purity_score":63,"primary_archetype":"industrial","economy_suggestion":null}]}`,
          { headers: { 'content-type': 'application/json' } },
        ),
      );
    const controller = new AbortController();

    await expect(
      getWatchlist(syncKey, controller.signal),
    ).resolves.toMatchObject({
      sync_key: syncKey,
      watchlist: [
        {
          system_id64: id64,
          name: 'Far Reach',
          x: 1,
          population: 0,
          is_colonised: false,
          archetype_score: 87,
          purity_score: 63,
          primary_archetype: 'industrial',
          economy_suggestion: null,
        },
      ],
    });
    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(request).toBeInstanceOf(Request);
    expect(new URL(request.url).pathname).toBe(`/api/v2/watchlist/${syncKey}`);
    expect(request.credentials).toBe('include');
    controller.abort();
    expect(request.signal.aborted).toBe(true);
  });

  it('accepts an empty scoped watchlist', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ sync_key: syncKey, watchlist: [] }),
    );
    await expect(getWatchlist(syncKey)).resolves.toEqual({
      sync_key: syncKey,
      watchlist: [],
    });
  });

  it.each([
    null,
    { sync_key: syncKey, watchlist: {} },
    { sync_key: 'another-watchlist-key', watchlist: [] },
    { sync_key: syncKey, watchlist: [{ name: 'Missing identifier' }] },
    { sync_key: syncKey, watchlist: [{ system_id64: '44', name: null }] },
    {
      sync_key: syncKey,
      watchlist: [{ system_id64: '44', name: 'Malformed', population: 'many' }],
    },
    {
      sync_key: syncKey,
      watchlist: [
        { system_id64: '44', name: 'Malformed', is_colonised: 'false' },
      ],
    },
    {
      sync_key: syncKey,
      watchlist: [
        { system_id64: '44', name: 'Malformed', primary_archetype: 42 },
      ],
    },
  ])(
    'rejects an invalid response without trusting the unknown generated shape',
    async (body) => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(body));
      await expect(getWatchlist(syncKey)).rejects.toBeInstanceOf(TypeError);
    },
  );

  it('adds and removes an exact oversized id64 without coercion or legacy endpoints', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ ok: true, sync_key: syncKey }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    await expect(addWatchlist(syncKey, id64)).resolves.toEqual({
      ok: true,
      sync_key: syncKey,
    });
    await expect(removeWatchlist(syncKey, id64)).resolves.toEqual({ ok: true });

    for (const [index, method] of ['POST', 'DELETE'].entries()) {
      const request = fetchMock.mock.calls[index]?.[0] as Request;
      expect(request).toBeInstanceOf(Request);
      expect(new URL(request.url).pathname).toBe(
        `/api/v2/watchlist/${syncKey}/${id64}`,
      );
      expect(request.method).toBe(method);
      expect(request.credentials).toBe('include');
      expect(await request.clone().text()).toBe('');
    }
  });

  it('rejects invalid mutation receipts', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        jsonResponse({ ok: true, sync_key: 'another-watchlist-key' }),
      )
      .mockResolvedValueOnce(jsonResponse({ ok: false }));
    await expect(addWatchlist(syncKey, id64)).rejects.toBeInstanceOf(TypeError);
    await expect(removeWatchlist(syncKey, id64)).rejects.toBeInstanceOf(
      TypeError,
    );
  });

  it('preserves structured API errors for a failed mutation', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ detail: 'System not found' }, 404),
    );
    const error = await addWatchlist(syncKey, id64).catch(
      (cause: unknown) => cause,
    );
    expect(error).toMatchObject({
      status: 404,
      path: `/api/v2/watchlist/${syncKey}/${id64}`,
    });
    expect(error).toBeInstanceOf(ApiError);
  });
});
