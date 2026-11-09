import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  buildExploreGalaxyScene,
  mapExploreSystemPoint,
} from '$lib/spatial/explore-scene';

const finderFeature = vi.hoisted(() => ({ enabled: false }));

vi.mock('$lib/config', () => ({
  get FINDER_F4_ENABLED() {
    return finderFeature.enabled;
  },
}));

// Partial mock (preserving every other real export via loadOriginal, per the
// $lib/api/client mocking convention in SystemDetail.test.ts /
// SystemOverlay.test.ts) so this file's other tests keep exercising the real
// generated SDK/transport while getMapHeatmap's test stubs only the one
// heatmap operation.
vi.mock('$lib/api/generated/sdk.gen', async (loadOriginal) => {
  const original =
    await loadOriginal<typeof import('$lib/api/generated/sdk.gen')>();
  return {
    ...original,
    mapHeatmapApiMapHeatmapGet: vi.fn(async () => ({
      data: { source: 'pyramid', cells: [] },
    })),
  };
});

import {
  ADMIN_TOKEN_SESSION_KEY,
  ApiError,
  LEGACY_ADMIN_ENDPOINTS,
  apiRequest,
  autocompleteSystems,
  claimOwner,
  getAuthIdentities,
  getAuthSession,
  getGalaxyImpact,
  getHealth,
  getCommanderTravelHeatmap,
  getCommanderJournalSummary,
  getMapHeatmap,
  getSystem,
  searchExploreSystems,
  startFrontierLink,
  unlinkAuthIdentity,
} from './client';
// Allowed only in a .test. file: exercise the generated client configuration
// (interceptors) the facade installs, on a route the facade does not wrap.
import { statusApiStatusGet } from './generated/sdk.gen';
import * as sdk from './generated/sdk.gen';
import { v3Body, v3System } from './v3-system-fixtures';

const jsonResponse = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), {
    ...init,
    headers: { 'content-type': 'application/json', ...init.headers },
  });

describe('getSystem F4 canonical origin guard', () => {
  beforeEach(() => {
    finderFeature.enabled = true;
  });

  afterEach(() => {
    finderFeature.enabled = false;
    vi.restoreAllMocks();
  });

  function serveSystem(
    id64: string,
    name: string,
    coords = { x: 0, y: 0, z: 0 },
  ) {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const path = new URL((input as Request).url).pathname;
      if (path === '/api/v1/generation') {
        return jsonResponse({
          generation_id: 'origin-guard-generation',
          publication_sequence: '1',
        });
      }
      if (path === `/api/v1/systems/${id64}`) {
        return jsonResponse(
          v3System({
            id64,
            name,
            x_ly: coords.x,
            y_ly: coords.y,
            z_ly: coords.z,
          }),
        );
      }
      return jsonResponse([]);
    });
  }

  it.each([
    ['42', 'Sol'],
    ['9007199254740993', 'Lossless origin'],
  ])(
    'retains non-Sol selection %s without inventing an origin point',
    async (rawId, name) => {
      const id64 = parseId64(rawId);
      serveSystem(id64, name);

      const detail = await getSystem(id64);
      expect(detail).toMatchObject({ id64, name });
      expect(detail.x).toBeUndefined();
      expect(detail.y).toBeUndefined();
      expect(detail.z).toBeUndefined();

      // Use the ordinary mapper, which trusts facade coordinates, rather than
      // the selected-point mapper's independent defensive origin guard.
      const selectedPoint = mapExploreSystemPoint({
        id64: detail.id64,
        name: detail.name,
        coords: { x: detail.x, y: detail.y, z: detail.z },
      });
      expect(selectedPoint).toBeNull();
      const scene = buildExploreGalaxyScene([], id64, 1, { selectedPoint });
      expect(scene.selection).toEqual([{ kind: 'system', systemId64: id64 }]);
      expect(scene.contributions.map(({ id }) => id)).toEqual([
        'finder-results',
      ]);
      expect(scene.contributions[0]?.layers[0]?.payload).toEqual({
        systems: [],
      });
    },
  );

  it('emits canonical Sol at the origin', async () => {
    const id64 = parseId64('10477373803');
    serveSystem(id64, 'Sol');

    const detail = await getSystem(id64);
    expect(detail).toMatchObject({ id64, x: 0, y: 0, z: 0 });
    const selectedPoint = mapExploreSystemPoint({
      id64: detail.id64,
      name: detail.name,
      coords: { x: detail.x, y: detail.y, z: detail.z },
    });
    expect(selectedPoint?.positionLy).toEqual({ x: 0, y: 0, z: 0 });
    const scene = buildExploreGalaxyScene([], id64, 1, { selectedPoint });
    expect(scene.selection).toEqual([{ kind: 'system', systemId64: id64 }]);
    expect(scene.contributions[1]?.id).toBe('finder-selection');
    expect(scene.contributions[1]?.layers[0]?.payload).toEqual({
      systems: [selectedPoint],
    });
  });

  it.each([
    { x: 1, y: 0, z: 0 },
    { x: 0, y: -1, z: 0 },
    { x: 0, y: 0, z: 1 },
  ])('preserves a nonzero coordinate triple %j', async (coords) => {
    const id64 = parseId64('42');
    serveSystem(id64, 'Known position', coords);

    await expect(getSystem(id64)).resolves.toMatchObject({ id64, ...coords });
  });

  it('preserves existing non-Sol origin coordinates with F4 disabled', async () => {
    finderFeature.enabled = false;
    const id64 = parseId64('42');
    serveSystem(id64, 'Legacy origin');

    await expect(getSystem(id64)).resolves.toMatchObject({
      id64,
      x: 0,
      y: 0,
      z: 0,
    });
  });
});

describe('typed V3 API facade over the generated Hey API SDK', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  it.each([
    'Earth-like world',
    'Unrecognised catalogue class',
    null,
    undefined,
  ])(
    'preserves the raw published body class %s and its source',
    async (bodyClass) => {
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const path = new URL((input as Request).url).pathname;
        if (path === '/api/v1/systems/42')
          return jsonResponse(v3System({ id64: '42' }));
        if (path === '/api/v1/systems/42/bodies')
          return jsonResponse([
            v3Body({
              body_type: 'planet',
              body_class: bodyClass,
              body_class_source:
                bodyClass == null ? null : 'ratings-v4-derived',
            }),
          ]);
        return jsonResponse([]);
      });

      const result = await getSystem(parseId64('42'));
      expect(result.bodies[0]).toMatchObject({
        bodyClass: bodyClass ?? null,
        bodyClassSource: bodyClass == null ? null : 'ratings-v4-derived',
      });
    },
  );

  it('loads private galaxy totals through the generated own-account route with cancellation', async () => {
    const summary = {
      systems_discovered: 1302,
      bodies_scanned: 9000,
      earth_like_worlds: 6,
      water_worlds: 4,
      ammonia_worlds: 2,
      terraformable_candidates: 7,
      gas_giants: 38,
    };
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse(summary));
    const controller = new AbortController();

    await expect(getGalaxyImpact(controller.signal)).resolves.toEqual(summary);

    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(request).toBeInstanceOf(Request);
    expect(new URL(request.url).pathname).toBe('/api/v1/journal/galaxy-impact');
    expect(new URL(request.url).search).toBe('');
    expect(request.credentials).toBe('include');
    controller.abort();
    expect(request.signal.aborted).toBe(true);
  });

  it('delegates ordinary bootstrap operations to the generated SDK over the credentialed same-origin transport', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        jsonResponse({ status: 'ok', database: 'connected' }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ authenticated: false, user: null }),
      );

    await expect(getHealth()).resolves.toMatchObject({ status: 'ok' });
    await expect(getAuthSession()).resolves.toMatchObject({
      authenticated: false,
    });

    // The generated client-fetch transport dispatches a single Request object,
    // not a (url, init) pair — proving the facade delegates to the generated
    // operation rather than hand-rolling the fetch.
    const healthRequest = fetchMock.mock.calls[0]?.[0] as Request;
    const sessionRequest = fetchMock.mock.calls[1]?.[0] as Request;
    expect(healthRequest).toBeInstanceOf(Request);
    expect(fetchMock.mock.calls[0]?.[1]).toBeUndefined();
    expect(healthRequest.url).toContain('/api/health');
    expect(healthRequest.credentials).toBe('include');
    expect(sessionRequest).toBeInstanceOf(Request);
    expect(sessionRequest.url).toContain('/api/v1/auth/session');
    expect(sessionRequest.credentials).toBe('include');
  });

  it('surfaces the V3 system 404 as a structured not-found ApiError', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const path = new URL((input as Request).url).pathname;
      return path === '/api/v1/systems/42'
        ? jsonResponse({ detail: 'V3 system 42 not found' }, { status: 404 })
        : jsonResponse([]);
    });

    const failure = await getSystem('42' as never).catch((error) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(404);
  });

  it('reads account-wide journal import metadata with session credentials', async () => {
    const summary = {
      imported_files: 0,
      events_stored: 0,
      systems_observed: 0,
      unique_bodies: 0,
      unique_bio_observations: 0,
      last_imported_at: null,
      event_counts: {},
    };
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse(summary));
    const controller = new AbortController();
    expect(await getCommanderJournalSummary(controller.signal)).toEqual(
      summary,
    );
    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(new URL(request.url).pathname).toBe('/api/v1/journal/summary');
    expect(request.credentials).toBe('include');
    controller.abort();
    expect(request.signal.aborted).toBe(true);
  });

  it('reads the signed-in travel heatmap from the V3 journal route', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({
        mode: 'density',
        cell_size_ly: 400,
        points: [
          {
            kind: 'density',
            system_id64: null,
            system_name: null,
            x: 200,
            y: 200,
            z: 200,
            visit_count: 5,
            first_visited_at: '2026-01-01T01:00:00+00:00',
            last_visited_at: '2026-01-01T05:00:00+00:00',
          },
        ],
        count: 1,
        truncated: false,
      }),
    );

    const result = await getCommanderTravelHeatmap(
      {
        minX: -1,
        maxX: 1,
        minY: -2,
        maxY: 2,
        minZ: -3,
        maxZ: 3,
        limit: 50_000,
      },
      9_000,
    );

    expect(result).toEqual({
      mode: 'density',
      visits: [
        {
          kind: 'density',
          systemId64: null,
          systemName: null,
          x: 200,
          y: 200,
          z: 200,
          visitCount: 5,
          firstVisitedAt: '2026-01-01T01:00:00+00:00',
          lastVisitedAt: '2026-01-01T05:00:00+00:00',
          cellSizeLy: 400,
        },
      ],
      count: 1,
      truncated: false,
    });
    const request = fetchMock.mock.calls[0]?.[0] as Request;
    const url = new URL(request.url);
    expect(url.pathname).toBe('/api/v1/journal/travel-heatmap');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      min_x: '-1',
      max_x: '1',
      min_y: '-2',
      max_y: '2',
      min_z: '-3',
      max_z: '3',
      zoom: '5000',
      limit: '20000',
    });
    expect(request.credentials).toBe('include');
  });

  it('uses typed generated Explore operations and normalizes every result id64', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(
          '{"results":[{"id64":9007199254740993,"name":"Far Reach","x":1,"y":2,"z":3}]}',
          { headers: { 'content-type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          '{"results":[{"id64":18446744073709551615,"name":"Edge","coords":{"x":4,"y":5,"z":6}}],"count":1}',
          { headers: { 'content-type': 'application/json' } },
        ),
      );

    await expect(autocompleteSystems('Far')).resolves.toMatchObject({
      results: [{ id64: '9007199254740993', name: 'Far Reach' }],
    });
    await expect(
      searchExploreSystems({ galaxy_wide: true, size: 24 }),
    ).resolves.toMatchObject({
      results: [{ id64: '18446744073709551615', name: 'Edge' }],
    });

    const autocompleteRequest = fetchMock.mock.calls[0]?.[0] as Request;
    const searchRequest = fetchMock.mock.calls[1]?.[0] as Request;
    expect(autocompleteRequest.url).toContain(
      '/api/local/autocomplete?q=Far&limit=10',
    );
    expect(searchRequest.url).toContain('/api/local/search');
    expect(await searchRequest.clone().json()).toEqual({
      galaxy_wide: true,
      size: 24,
    });
  });

  it('raises a structured ApiError carrying status and path on a non-2xx response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ detail: 'unavailable' }, { status: 503 }),
    );

    const error = await getHealth().catch((cause: unknown) => cause);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect((error as ApiError).path).toContain('/api/health');
  });

  it('keeps identity management on the credentialed same-origin facade', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        jsonResponse([
          {
            external_identity_id: 'identity-1',
            provider: 'frontier',
            linked_at: '2026-09-09T12:00:00Z',
          },
        ]),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          authorization_url: 'https://auth.frontierstore.net/auth?state=one',
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ authenticated: true, user: null }));

    await expect(getAuthIdentities()).resolves.toEqual([
      {
        external_identity_id: 'identity-1',
        provider: 'frontier',
        linked_at: '2026-09-09T12:00:00Z',
      },
    ]);
    await expect(startFrontierLink('/')).resolves.toContain(
      'auth.frontierstore.net',
    );
    await expect(unlinkAuthIdentity('identity-1')).resolves.toMatchObject({
      authenticated: true,
    });

    // Every identity operation is dispatched by the generated client as a
    // credentialed same-origin Request, exactly like the bootstrap lane.
    const identitiesRequest = fetchMock.mock.calls[0]?.[0] as Request;
    const linkRequest = fetchMock.mock.calls[1]?.[0] as Request;
    const unlinkRequest = fetchMock.mock.calls[2]?.[0] as Request;
    expect(identitiesRequest).toBeInstanceOf(Request);
    expect(identitiesRequest.url).toContain('/api/v1/auth/identities');
    expect(identitiesRequest.credentials).toBe('include');
    expect(linkRequest.url).toContain('/api/v1/auth/frontier/link');
    expect(linkRequest.url).toContain('return_to=%2F');
    expect(linkRequest.method).toBe('POST');
    expect(unlinkRequest.url).toContain('/api/v1/auth/identities/identity-1');
    expect(unlinkRequest.method).toBe('DELETE');
  });

  it('injects the bounded session admin token only for require_admin routes', async () => {
    sessionStorage.setItem(ADMIN_TOKEN_SESSION_KEY, 'session-admin');
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse({ ok: true }));

    // /api/status is in the require_admin inventory.
    await statusApiStatusGet({ throwOnError: true });
    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(request.headers.get('X-Admin-Token')).toBe('session-admin');
  });

  it('keeps the one-time owner secret in the body and off reusable headers', async () => {
    sessionStorage.setItem(ADMIN_TOKEN_SESSION_KEY, 'session-admin');
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({
        authenticated: true,
        user: { commander_name: 'CMDR', is_owner: true },
      }),
    );

    await claimOwner('one-time-owner-secret');
    const request = fetchMock.mock.calls[0]?.[0] as Request;
    // /api/v1/auth/owner/claim is not an admin-classified route: the reusable
    // session token must not be attached, and the one-time secret rides only
    // in the body.
    expect(request).toBeInstanceOf(Request);
    expect(request.headers.has('X-Admin-Token')).toBe(false);
    expect(await request.clone().text()).toBe(
      JSON.stringify({ admin_token: 'one-time-owner-secret' }),
    );
    expect(request.url).toContain('/api/v1/auth/owner/claim');
  });

  it('re-exports the single shared transport inventory rather than duplicating it', () => {
    expect(LEGACY_ADMIN_ENDPOINTS).toHaveLength(28);
    expect(apiRequest).toBeTypeOf('function');
  });
});

describe('getMapHeatmap', () => {
  it('calls the heatmap SDK with query + throwOnError and returns data', async () => {
    const out = await getMapHeatmap({ voxel_size: 500, max_cells: 40000 });
    expect(out).toMatchObject({ source: 'pyramid' });
    expect(vi.mocked(sdk.mapHeatmapApiMapHeatmapGet)).toHaveBeenCalledWith(
      expect.objectContaining({
        query: { voxel_size: 500, max_cells: 40000 },
        throwOnError: true,
      }),
    );
  });

  it('forwards complete coverage and the bounded viewport through the facade', async () => {
    const query = {
      coverage: 'complete' as const,
      voxel_size: 640,
      min_systems: 1,
      max_cells: 40_000,
      min_x: -4_000,
      max_x: 4_000,
      min_y: -2_000,
      max_y: 2_000,
      min_z: 12_000,
      max_z: 20_000,
    };
    const signal = new AbortController().signal;
    await getMapHeatmap(query, signal);
    expect(vi.mocked(sdk.mapHeatmapApiMapHeatmapGet)).toHaveBeenLastCalledWith({
      query,
      signal,
      throwOnError: true,
    });
  });
});

// ---------------------------------------------------------------------------
// Finder F4a: typed ranking foundation (page envelopes, row normalization)
// ---------------------------------------------------------------------------

import {
  FINDER_NAVIGATION_LIMIT,
  SOL_ID64,
  distanceReferenceForAnchor,
  getArchetypeRankings,
  getArchetypeExplanation,
  normalizeAnySearchResponse,
  normalizeRankingProvenance,
  normalizeRankingsResponse,
  type ArchetypeRankingsPage,
  type DistanceAnchor,
} from './client';
import { parseId64 } from '@ed-finder/api-client/id64';
import { ARCHETYPE_KEYS } from '$lib/features/explore/archetypes';

const provenance = {
  ranking_version: 'v3-colony-potential-2',
  ranking_sha256: 'ranking-sha',
  derived_generation_id: 'generation-id',
  publication_sequence: 1,
  archetype_version: 'v3-archetype-1',
  archetype_manifest_sha256: 'manifest-sha',
  archetype_model_sha256: 'model-sha',
};

const rankingRow = (id64: string, score: number, tier: 'S' | 'A' | 'B') => ({
  id64: id64 as unknown as number,
  name: `System ${id64}`,
  coords: { x: 1, y: 2, z: 3 },
  distance_to_sol: 139.45,
  score,
  tier,
  selected_archetype: 'flexible',
  primary_archetype: 'flexible',
  secondary_archetype: null,
  archetype_confidence: 0.91,
  overall_development_potential: 100,
  confidence: 0.82,
  completeness: 0.64,
  elw_count: 1,
});

const rankingsEnvelope = (
  results: ReturnType<typeof rankingRow>[],
  extra: Record<string, unknown> = {},
) => ({
  archetype: 'flexible',
  archetype_label: 'Flexible Multi-Role Colony',
  results,
  total: results.length,
  count: results.length,
  is_truncated: false,
  ranking: provenance,
  ...extra,
});

const flexibleRequest = {
  archetype: 'flexible' as const,
  min_score: 60 as const,
  page_size: 1,
  offset: 0,
};

describe('Finder archetype explanation facade', () => {
  const id64 = parseId64('9007199254740993');
  const expected = {
    derived_generation_id: provenance.derived_generation_id,
    archetype_version: provenance.archetype_version,
    archetype_manifest_sha256: provenance.archetype_manifest_sha256,
  };
  const explanationEnvelope = () => ({
    id64,
    ranking: { ...provenance },
    completeness: 0.84,
    summary: {
      primary_archetype: 'flexible',
      secondary_archetype: 'paradise',
      best_colony_potential: 88,
      best_tier: 'S',
      archetype_confidence: 0.91,
      weighted_potential: 80,
    },
    archetypes: ARCHETYPE_KEYS.map((key, index) => ({
      key,
      score: 88 - index,
      tier: index === 0 ? 'S' : 'A',
      confidence: 0.91,
      weighted_e4: 800_000,
      explanation: { contributions: { Agriculture: 0.2 } },
    })),
  });

  afterEach(() => vi.restoreAllMocks());

  it('calls only the generated explanation endpoint, pins all three identities and retains eight stored score/tier/confidence entries', async () => {
    const envelope = explanationEnvelope();
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(envelope).replace(`"${id64}"`, id64), {
        headers: { 'content-type': 'application/json' },
      }),
    );
    const result = await getArchetypeExplanation(id64, expected);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const request = fetchMock.mock.calls[0]?.[0] as Request;
    const url = new URL(request.url);
    expect(url.pathname).toBe(`/api/archetypes/system/${id64}/explanation`);
    expect(url.pathname).not.toBe(`/api/archetypes/system/${id64}`);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      expected_generation_id: expected.derived_generation_id,
      expected_archetype_version: expected.archetype_version,
      expected_manifest_sha256: expected.archetype_manifest_sha256,
    });
    expect(request.credentials).toBe('include');
    expect(result).toEqual({ kind: 'explained', ...envelope });
  });

  it('returns explanation_unavailable for HTTP 409 without exposing server detail', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ detail: 'internal mismatch detail' }, { status: 409 }),
    );
    await expect(getArchetypeExplanation(id64, expected)).resolves.toEqual({
      kind: 'explanation_unavailable',
    });
  });

  it.each([
    'derived_generation_id',
    'archetype_version',
    'archetype_manifest_sha256',
  ] as const)('rejects a 200 response with a changed %s', async (field) => {
    const envelope = explanationEnvelope();
    envelope.ranking[field] = 'republished';
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(envelope));
    await expect(getArchetypeExplanation(id64, expected)).resolves.toEqual({
      kind: 'explanation_unavailable',
    });
  });

  it.each([404, 503])(
    'returns the bounded HTTP %s state without throwing',
    async (status) => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        jsonResponse({ detail: 'private diagnostic detail' }, { status }),
      );
      await expect(getArchetypeExplanation(id64, expected)).resolves.toEqual({
        kind: 'error',
        status,
      });
    },
  );

  it('contains transport failures', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('offline'));
    await expect(getArchetypeExplanation(id64, expected)).resolves.toEqual({
      kind: 'error',
      status: 0,
    });
  });

  it('does not issue an unpinned request when identity is missing', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    await expect(
      getArchetypeExplanation(id64, {
        ...expected,
        archetype_manifest_sha256: '',
      }),
    ).resolves.toEqual({ kind: 'explanation_unavailable' });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    'missing',
    'duplicate',
    'unknown',
    'score',
    'confidence',
    'tier',
    'explanation',
    'system',
  ])('fails closed for a malformed %s in a 200 response', async (malformed) => {
    const envelope = explanationEnvelope();
    if (malformed === 'missing') envelope.archetypes.pop();
    if (malformed === 'duplicate')
      envelope.archetypes[1] = envelope.archetypes[0];
    if (malformed === 'unknown')
      envelope.archetypes[0].key = 'unknown' as never;
    if (malformed === 'score') envelope.archetypes[0].score = 101;
    if (malformed === 'confidence') envelope.archetypes[0].confidence = -1;
    if (malformed === 'tier') envelope.archetypes[0].tier = 'Z';
    if (malformed === 'explanation')
      envelope.archetypes[0].explanation = null as never;
    if (malformed === 'system') envelope.id64 = parseId64('42');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(envelope));
    await expect(getArchetypeExplanation(id64, expected)).resolves.toEqual({
      kind: 'error',
      status: 0,
    });
  });
});

describe('selected-archetype rankings facade', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('maps facade page_size=1 to wire limit=1 and sends the tier floor, no hard filter', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify(
          rankingsEnvelope([rankingRow('9007199254740993', 100, 'S')], {
            total: 3,
          }),
        ).replace('"9007199254740993"', '9007199254740993'),
        { headers: { 'content-type': 'application/json' } },
      ),
    );

    const page = await getArchetypeRankings(flexibleRequest);

    const request = fetchMock.mock.calls[0]?.[0] as Request;
    const url = new URL(request.url);
    expect(url.pathname).toBe('/api/archetypes/rankings');
    expect(Object.fromEntries(url.searchParams.entries())).toEqual({
      archetype: 'flexible',
      min_score: '60',
      limit: '1',
      offset: '0',
    });
    expect(request.credentials).toBe('include');
    expect(page.page).toEqual({
      page_size: 1,
      count: 1,
      total: 3,
      navigable_total: 3,
      offset: 0,
      navigation_limit: 10_000,
      truncation: { source: 'rankings', is_truncated: false },
    });
    expect(page.ranking).toEqual(provenance);
    expect(page.results[0]).toMatchObject({
      id64: '9007199254740993',
      ranking_score: 100,
      ranking_tier: 'S',
      score_kind: 'selected_fit',
      selected_archetype: 'flexible',
      distance: 139.45,
      distance_reference: { kind: 'sol', id64: SOL_ID64, name: 'Sol' },
      ranking_provenance: provenance,
      overall_development_potential: 100,
      confidence: 0.82,
      completeness: 0.64,
      archetype_score: null,
      archetype_tier: null,
    });
    expect(page.results[0]).not.toHaveProperty('best_tier');
  });

  it.each([79, 0])(
    'carries the rival archetype and score %i when present',
    (score) => {
      const rows = [
        {
          ...rankingRow('1', 95, 'S'),
          rival_archetype: 'megacomplex',
          rival_score: score,
        },
      ];
      const page = normalizeRankingsResponse(
        rankingsEnvelope(rows),
        flexibleRequest,
      );

      expect(page.results[0]).toMatchObject({
        rival_archetype: 'megacomplex',
        rival_score: score,
      });
    },
  );

  it('leaves rival fields absent when the server omits them', () => {
    const page = normalizeRankingsResponse(
      rankingsEnvelope([rankingRow('1', 95, 'S')]),
      flexibleRequest,
    );

    expect(page.results[0]).not.toHaveProperty('rival_archetype');
    expect(page.results[0]).not.toHaveProperty('rival_score');
  });

  it('clamps the wire limit at the 10,000 window and rejects offsets beyond it', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(rankingsEnvelope([])), {
        headers: { 'content-type': 'application/json' },
      }),
    );
    await getArchetypeRankings({
      ...flexibleRequest,
      page_size: 50,
      offset: 9_975,
    });
    const url = new URL((fetchMock.mock.calls[0]?.[0] as Request).url);
    expect(url.searchParams.get('limit')).toBe('25');
    expect(url.searchParams.get('offset')).toBe('9975');

    for (const offset of [10_000, -1, 1.5, 2_000_000_000]) {
      await expect(
        getArchetypeRankings({ ...flexibleRequest, offset }),
      ).rejects.toThrow(RangeError);
    }
    for (const page_size of [0, 51, 24.5]) {
      await expect(
        getArchetypeRankings({ ...flexibleRequest, page_size }),
      ).rejects.toThrow(RangeError);
    }
    await expect(
      getArchetypeRankings({ ...flexibleRequest, archetype: 'agri' as never }),
    ).rejects.toThrow(RangeError);
    expect(FINDER_NAVIGATION_LIMIT).toBe(10_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('preserves the independent API-owned truncation flag and total = min(qualifying, 10000)', () => {
    const rows = [rankingRow('1', 95, 'S')];
    const truncated = normalizeRankingsResponse(
      rankingsEnvelope(rows, { total: 10_000, is_truncated: true }),
      { ...flexibleRequest, page_size: 50, offset: 9_950 },
    );
    expect(truncated.page).toMatchObject({
      total: 10_000,
      navigable_total: 10_000,
      offset: 9_950,
      truncation: { source: 'rankings', is_truncated: true },
    });
    const exact = normalizeRankingsResponse(
      rankingsEnvelope(rows, { total: 10_000, is_truncated: false }),
      flexibleRequest,
    );
    expect(exact.page.truncation).toEqual({
      source: 'rankings',
      is_truncated: false,
    });
    const below = normalizeRankingsResponse(
      rankingsEnvelope(rows, { total: 3, is_truncated: false }),
      flexibleRequest,
    );
    expect(below.page.navigable_total).toBe(3);
  });

  it('fails closed on a truncated empty window and on a row without score/tier', () => {
    expect(() =>
      normalizeRankingsResponse(
        rankingsEnvelope([], { total: 0, is_truncated: true }),
        flexibleRequest,
      ),
    ).toThrow(TypeError);
    expect(() =>
      normalizeRankingsResponse(
        rankingsEnvelope([{ ...rankingRow('1', 95, 'S'), tier: 'X' as never }]),
        flexibleRequest,
      ),
    ).toThrow(TypeError);
    expect(() =>
      normalizeRankingsResponse(
        rankingsEnvelope([], { ranking: { ranking_version: 'v' } }),
        flexibleRequest,
      ),
    ).toThrow(TypeError);
  });

  it('copies all seven provenance fields and yields null for an incomplete envelope', () => {
    expect(normalizeRankingProvenance(provenance)).toEqual(provenance);
    const { archetype_model_sha256: _dropped, ...sixOnly } = provenance;
    void _dropped;
    expect(normalizeRankingProvenance(sixOnly)).toBeNull();
    expect(normalizeRankingProvenance(undefined)).toBeNull();
    expect(
      normalizeRankingProvenance({ ...provenance, publication_sequence: -1 }),
    ).toBeNull();
  });

  it('passes an abort signal through to the generated operation', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(rankingsEnvelope([])), {
        headers: { 'content-type': 'application/json' },
      }),
    );
    const controller = new AbortController();
    const page: ArchetypeRankingsPage = await getArchetypeRankings(
      flexibleRequest,
      controller.signal,
    );
    expect(page.results).toEqual([]);
    controller.abort();
    expect((fetchMock.mock.calls[0]?.[0] as Request).signal.aborted).toBe(true);
  });
});

describe('Any (local search) page envelope normalization', () => {
  const anyRow = (id64: string, distance: number | null = 12.5) => ({
    id64: id64 as unknown as number,
    name: `System ${id64}`,
    coords: { x: 1, y: 2, z: 3 },
    distance,
    archetype_score: 81,
    archetype_tier: 'A' as const,
    primary_archetype: 'paradise',
    secondary_archetype: 'flexible',
    overall_development_potential: 84,
    confidence: 0.9,
    completeness: 0.85,
  });
  const searchEnvelope = (
    results: ReturnType<typeof anyRow>[],
    extra: Record<string, unknown> = {},
  ) => ({
    results,
    total: results.length,
    count: results.length,
    ranking: provenance,
    ...extra,
  });
  const anchored = {
    reference_coords: { x: 1, y: 2, z: 3 },
    filters: { distance: { min: 0, max: 500 }, economy: 'any' as const },
    sort_by: 'distance',
    size: 50,
    from: 0,
  };
  const star: DistanceAnchor = {
    id64: parseId64('158872029'),
    name: 'HD 38179',
  };

  it('carries the overall score, anchor reference and all seven provenance fields on every row', () => {
    const page = normalizeAnySearchResponse(
      searchEnvelope([anyRow('42'), anyRow('43', null)], { total: 2 }),
      anchored,
      star,
    );
    expect(page.page).toEqual({
      page_size: 50,
      count: 2,
      total: 2,
      navigable_total: 2,
      offset: 0,
      navigation_limit: 10_000,
      truncation: { source: 'local-search', total_is_capped: false },
    });
    expect(page.results[0]).toMatchObject({
      id64: '42',
      // The overall potential, never the differing primary-archetype score,
      // and no tier because the API's tier belongs to that other score.
      ranking_score: 84,
      ranking_tier: null,
      score_kind: 'overall_potential',
      selected_archetype: null,
      distance_reference: {
        kind: 'anchor',
        id64: '158872029',
        name: 'HD 38179',
      },
      ranking_provenance: provenance,
      archetype_score: 81,
      overall_development_potential: 84,
      confidence: 0.9,
      completeness: 0.85,
    });
    // No measured distance means no reference, even on an anchored search.
    expect(page.results[1]?.distance_reference).toBeNull();
  });

  it('normalizes a Sol anchor to the sol reference and galaxy-wide to no reference', () => {
    const sol: DistanceAnchor = { id64: SOL_ID64, name: 'Sol' };
    expect(distanceReferenceForAnchor(sol)).toEqual({
      kind: 'sol',
      id64: SOL_ID64,
      name: 'Sol',
    });
    expect(distanceReferenceForAnchor(star)).toEqual({
      kind: 'anchor',
      ...star,
    });
    expect(distanceReferenceForAnchor(null)).toBeNull();
    const solPage = normalizeAnySearchResponse(
      searchEnvelope([anyRow('42', 0)]),
      anchored,
      sol,
    );
    expect(solPage.results[0]?.distance_reference?.kind).toBe('sol');
    expect(solPage.results[0]?.distance).toBe(0);
    const galaxyWide = normalizeAnySearchResponse(
      searchEnvelope([anyRow('42', null)], { total: 1 }),
      { galaxy_wide: true, sort_by: 'development', size: 24, from: 0 },
      star,
    );
    expect(galaxyWide.results[0]?.distance_reference).toBeNull();
    expect(galaxyWide.page.page_size).toBe(24);
  });

  it('exposes the sentinel-backed Any count states: below cap, exact 10,000 and overflow', () => {
    const request = { ...anchored, size: 1, from: 2 };
    const below = normalizeAnySearchResponse(
      searchEnvelope([anyRow('42')], { total: 3, total_is_capped: false }),
      request,
      star,
    );
    expect(below.page).toMatchObject({
      page_size: 1,
      offset: 2,
      total: 3,
      navigable_total: 3,
      truncation: { source: 'local-search', total_is_capped: false },
    });
    const exact = normalizeAnySearchResponse(
      searchEnvelope([anyRow('42')], { total: 10_000, total_is_capped: false }),
      request,
      star,
    );
    expect(exact.page).toMatchObject({
      total: 10_000,
      navigable_total: 10_000,
      truncation: { source: 'local-search', total_is_capped: false },
    });
    const overflow = normalizeAnySearchResponse(
      searchEnvelope([anyRow('42')], { total: 10_000, total_is_capped: true }),
      request,
      star,
    );
    expect(overflow.page).toMatchObject({
      total: 10_000,
      navigable_total: 10_000,
      truncation: { source: 'local-search', total_is_capped: true },
    });
    // A null/absent flag is never read as capped.
    const unflagged = normalizeAnySearchResponse(
      searchEnvelope([anyRow('42')], { total: 10_000, total_is_capped: null }),
      request,
      star,
    );
    expect(unflagged.page.truncation).toEqual({
      source: 'local-search',
      total_is_capped: false,
    });
  });

  it('lists rows without provenance when the envelope lacks the ranking identity', () => {
    const page = normalizeAnySearchResponse(
      { results: [anyRow('42')], count: 1 } as never,
      anchored,
      star,
    );
    expect(page.results[0]?.ranking_provenance).toBeNull();
    expect(page.page.total).toBe(1);
  });
});

describe('near-system body filters through the generated facade', () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it('sends count ranges exactly once as LocalSearchRequest.body_filters', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse({ results: [], total: 0, count: 0 }));
    await searchExploreSystems({
      reference_coords: { x: 12, y: -4, z: 3 },
      filters: { distance: { min: 0, max: 500 }, economy: 'any' },
      sort_by: 'distance',
      size: 24,
      body_filters: {
        elw_count: { min: 1 },
        landable_count: { min: 2, max: 5 },
        ring_count: { max: 3 },
      },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const request = fetchMock.mock.calls[0][0] as Request;
    expect(new URL(request.url).pathname).toBe('/api/local/search');
    expect(request.method).toBe('POST');
    expect(await request.clone().json()).toMatchObject({
      body_filters: {
        elw_count: { min: 1 },
        landable_count: { min: 2, max: 5 },
        ring_count: { max: 3 },
      },
    });
  });

  it('omits body_filters when the caller leaves them unset', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse({ results: [], total: 0, count: 0 }));
    await searchExploreSystems({ galaxy_wide: true, size: 24 });
    const request = fetchMock.mock.calls[0][0] as Request;
    expect(await request.clone().json()).not.toHaveProperty('body_filters');
  });
});

describe('facade page size versus clamped wire limit near the 10,000 ceiling', () => {
  const nearCeiling = (body: unknown) =>
    new Response(JSON.stringify(body), {
      headers: { 'content-type': 'application/json' },
    });

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('Any (?page_size=50&offset=9975): wire size 25, envelope page_size 50', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      nearCeiling({
        results: [],
        total: 10_000,
        count: 0,
        total_is_capped: true,
        ranking: provenance,
      }),
    );
    const response = await searchExploreSystems(
      { galaxy_wide: true, sort_by: 'development', size: 25, from: 9_975 },
      undefined,
      null,
      50,
    );
    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(await request.clone().json()).toMatchObject({
      size: 25,
      from: 9_975,
    });
    expect(response.page).toMatchObject({
      page_size: 50,
      offset: 9_975,
      total: 10_000,
      navigable_total: 10_000,
    });
    // Without an explicit facade size (the live default-off path) the wire
    // size is the page size, and an invalid facade size never dispatches.
    expect(
      normalizeAnySearchResponse(
        { results: [], total: 3, ranking: provenance },
        { galaxy_wide: true, size: 24, from: 0 },
      ).page.page_size,
    ).toBe(24);
    await expect(
      searchExploreSystems(
        { galaxy_wide: true, size: 25 },
        undefined,
        null,
        51,
      ),
    ).rejects.toThrow(RangeError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('selected (?archetype=flexible&page_size=50&offset=9975): wire limit 25, envelope page_size 50', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      nearCeiling({
        archetype: 'flexible',
        archetype_label: 'Flexible Multi-Role Colony',
        results: [],
        total: 10_000,
        count: 0,
        is_truncated: true,
        ranking: provenance,
      }),
    );
    const page = await getArchetypeRankings({
      archetype: 'flexible',
      min_score: 60,
      page_size: 50,
      offset: 9_975,
    });
    const url = new URL((fetchMock.mock.calls[0]?.[0] as Request).url);
    expect(url.searchParams.get('limit')).toBe('25');
    expect(url.searchParams.get('offset')).toBe('9975');
    expect(page.page).toMatchObject({
      page_size: 50,
      offset: 9_975,
      total: 10_000,
      truncation: { source: 'rankings', is_truncated: true },
    });
  });
});

describe('Any ranking score is the overall potential', () => {
  it('carries the tier only when it was derived from the overall value, and never falls back', () => {
    const row = {
      id64: '7' as unknown as number,
      name: 'Same',
      archetype_score: 84,
      archetype_tier: 'A' as const,
      overall_development_potential: 84,
    };
    const request = { galaxy_wide: true, size: 24, from: 0 };
    const same = normalizeAnySearchResponse(
      { results: [row], total: 1, ranking: provenance },
      request,
    );
    expect(same.results[0]).toMatchObject({
      ranking_score: 84,
      ranking_tier: 'A',
      score_kind: 'overall_potential',
    });
    const missing = normalizeAnySearchResponse(
      {
        results: [{ ...row, overall_development_potential: null }],
        total: 1,
        ranking: provenance,
      },
      request,
    );
    expect(missing.results[0]).toMatchObject({
      ranking_score: null,
      ranking_tier: null,
      archetype_score: 84,
    });
  });
});
