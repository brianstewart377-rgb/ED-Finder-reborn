import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  getSystem,
  getSystemRatingExplanation,
  getSystemRatings,
  systemDetailFromV3,
} from './client';
import {
  ratingsIdentity,
  v3Body,
  v3Ring,
  v3Station,
  v3System,
} from './v3-system-fixtures';
import { parseId64 } from '$lib/domain/id64';

afterEach(() => vi.restoreAllMocks());

const GENERATION_1 =
  '{"generation_id":"g-1","generation_key":"k1","publication_sequence":"1","published_at":"2026-10-01T00:00:00+00:00"}';
const GENERATION_2 =
  '{"generation_id":"g-2","generation_key":"k2","publication_sequence":"2","published_at":"2026-10-02T00:00:00+00:00"}';

const json = (body: string) =>
  new Response(body, {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });

const pathOf = (input: unknown) => new URL((input as Request).url).pathname;

describe('id64-bearing application API facade', () => {
  it('reads Inspect from the V3 canonical routes with the exact decimal id64', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        const path = pathOf(input);
        if (path === '/api/v1/generation') return json(GENERATION_1);
        if (path === '/api/v1/systems/9007199254740993')
          return json(
            '{"id64":"9007199254740993","name":"Lossless","x_ly":1,"y_ly":2,"z_ly":3,"source_body_count":1,"loaded_body_count":1,"lifecycle_state":"ACTIVE","source_updated_at":null}',
          );
        if (path === '/api/v1/systems/9007199254740993/bodies')
          return json(
            '[{"body_id":"36028797018963969","source_body_id64":"36028797018963969","frontier_body_id":"0","direct_parent_body_id":null,"name":"Lossless","body_type":"star","is_main_star":true,"spectral_class":"K1","luminosity_class":"V","is_landable":null,"radius_km":500000,"planet_mass_earth":null,"stellar_mass_solar":0.8,"surface_pressure_atm":null,"semi_major_axis_km":null,"distance_from_arrival_ls":0,"lifecycle_state":"ACTIVE","signals":[]}]',
          );
        return json('[]');
      });

    const result = await getSystem(parseId64('9007199254740993'));

    expect(result.id64).toBe('9007199254740993');
    expect(result.name).toBe('Lossless');
    expect(result.canonicalGenerationId).toBe('g-1');
    expect(result.canonicalGenerationLabel).toBe('k1');
    expect(result.sourceUpdatedAt).toBeNull();
    expect(result.bodies).toHaveLength(1);
    // Canonical body keys beyond 2^53 stay lossless decimal strings.
    expect(result.bodies[0]?.bodyKey).toBe('36028797018963969');
    expect(result.bodies[0]?.id).toBe(0);
    const requests = fetchMock.mock.calls.map(([input]) => input as Request);
    // The four reads are fenced by the generation identity on both sides.
    expect(pathOf(requests[0])).toBe('/api/v1/generation');
    expect(pathOf(requests.at(-1))).toBe('/api/v1/generation');
    expect(requests.map(pathOf).sort()).toEqual([
      '/api/v1/generation',
      '/api/v1/generation',
      '/api/v1/systems/9007199254740993',
      '/api/v1/systems/9007199254740993/bodies',
      '/api/v1/systems/9007199254740993/rings',
      '/api/v1/systems/9007199254740993/stations',
    ]);
    for (const request of requests) {
      // The generated Hey API transport dispatches a single same-origin,
      // credentialed Request object rather than a (url, init) pair.
      expect(request).toBeInstanceOf(Request);
      expect(request.credentials).toBe('include');
    }
    expect(fetchMock.mock.calls[0]?.[1]).toBeUndefined();
  });

  it('retries a read that straddles a publication, then fails closed', async () => {
    const generations = [
      GENERATION_1,
      GENERATION_2,
      GENERATION_2,
      GENERATION_2,
    ];
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        const path = pathOf(input);
        if (path === '/api/v1/generation') return json(generations.shift()!);
        if (path === '/api/v1/systems/42')
          return json(
            '{"id64":"42","name":"Fenced","x_ly":0,"y_ly":0,"z_ly":0,"source_body_count":null,"loaded_body_count":0,"lifecycle_state":"ACTIVE","source_updated_at":null}',
          );
        return json('[]');
      });

    // The first attempt straddles g-1 -> g-2; the retry sees g-2 on both sides.
    const fenced = await getSystem(parseId64('42'));
    expect(fenced.name).toBe('Fenced');
    expect(fenced.canonicalGenerationId).toBe('g-2');
    expect(fenced.canonicalGenerationLabel).toBe('k2');
    expect(
      fetchMock.mock.calls.filter(
        ([input]) => pathOf(input) === '/api/v1/systems/42',
      ),
    ).toHaveLength(2);

    generations.push(GENERATION_1, GENERATION_2, GENERATION_1, GENERATION_2);
    const failure = await getSystem(parseId64('42')).catch((error) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(503);
  });

  it('re-reads a 404 that straddled a publication and reports a stable 404', async () => {
    const generations = [
      GENERATION_1,
      GENERATION_2,
      GENERATION_2,
      GENERATION_2,
    ];
    let published = false;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const path = pathOf(input);
      if (path === '/api/v1/generation') {
        const next = generations.shift()!;
        published = next === GENERATION_2;
        return json(next);
      }
      if (/^\/api\/v1\/systems\/[0-9]+$/u.test(path))
        return path.endsWith('/77') && published && generations.length < 3
          ? json(
              '{"id64":"77","name":"Newly published","x_ly":0,"y_ly":0,"z_ly":0,"source_body_count":null,"loaded_body_count":0,"lifecycle_state":"ACTIVE","source_updated_at":null}',
            )
          : new Response('{"detail":"not found"}', {
              status: 404,
              headers: { 'content-type': 'application/json' },
            });
      return json('[]');
    });

    // Attempt 1 sees g-1 before and g-2 after with a 404 in between: the 404
    // is not trusted, and the retry (g-2 on both sides) finds the system.
    await expect(getSystem(parseId64('77'))).resolves.toMatchObject({
      name: 'Newly published',
    });

    generations.push(GENERATION_2, GENERATION_2);
    published = false;
    const missing = await getSystem(parseId64('4242')).catch((error) => error);
    expect(missing).toBeInstanceOf(ApiError);
    expect((missing as ApiError).status).toBe(404);
  });
});

const ECONOMIES = [
  'Agriculture',
  'Refinery',
  'Industrial',
  'HighTech',
  'Military',
  'Tourism',
  'Extraction',
];

const ratingRow = (
  economy: string,
  overrides: Record<string, unknown> = {},
) => ({
  economy,
  potential_score: economy === 'Tourism' ? 70 : 10,
  specialisation_quality: null,
  specialisation_quality_min: 0,
  specialisation_quality_max: 40,
  evidence_completeness: 0.5,
  confidence: 0.75,
  ...overrides,
});

const ratingsEnvelope = (rows: unknown[], publicationSequence = 1) =>
  // system_id64 stays a raw unsafe integer token on the wire.
  JSON.stringify({
    derived_generation_id: '8f9a7e3c-1d2b-4c5d-9e6f-0a1b2c3d4e5f',
    publication_sequence: publicationSequence,
    canonical_generation_id: 'c-1',
    canonical_publication_sequence: 1,
    scorer_version: '4.0.0',
    mechanics_version: 'v4-mechanics-2026-09',
    system_id64: 'SYSTEM_ID64',
    ratings: rows,
  }).replace('"SYSTEM_ID64"', '9007199254740993');

describe('Ratings V4 facade', () => {
  it('reads the seven economies for the exact unsafe id64, in scorer order', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        json(
          ratingsEnvelope([...ECONOMIES].reverse().map((e) => ratingRow(e))),
        ),
      );

    const result = await getSystemRatings(parseId64('9007199254740993'));

    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(pathOf(request)).toBe('/api/ratings/v4/systems/9007199254740993');
    expect(request.credentials).toBe('include');
    expect(result.identity).toEqual(ratingsIdentity());
    expect(result.ratings.map((rating) => rating.economy)).toEqual(ECONOMIES);
    expect(result.ratings.find((r) => r.economy === 'Tourism')).toEqual({
      economy: 'Tourism',
      potential: 70,
      specialisation: null,
      specialisationMin: 0,
      specialisationMax: 40,
      completeness: 0.5,
      confidence: 0.75,
    });
  });

  it('fails closed on an out-of-range score or a missing economy', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        json(
          ratingsEnvelope(
            ECONOMIES.map((e) =>
              ratingRow(e, e === 'Military' ? { potential_score: 101 } : {}),
            ),
          ),
        ),
      )
      .mockResolvedValueOnce(
        json(ratingsEnvelope(ECONOMIES.slice(1).map((e) => ratingRow(e)))),
      )
      .mockResolvedValueOnce(
        json(
          ratingsEnvelope(
            ECONOMIES.map((e) =>
              ratingRow(e, e === 'Refinery' ? { confidence: 1.5 } : {}),
            ),
          ),
        ),
      );

    for (let attempt = 0; attempt < 3; attempt += 1)
      await expect(getSystemRatings(parseId64('42'))).rejects.toBeInstanceOf(
        TypeError,
      );
  });

  it('surfaces an unrated system as a structured 404', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        '{"detail":"System is not in the published Ratings V4 generation"}',
        {
          status: 404,
          headers: { 'content-type': 'application/json' },
        },
      ),
    );
    const failure = await getSystemRatings(parseId64('42')).catch((e) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(404);
  });

  it('explains only the publication on screen and maps contributions', async () => {
    const contribution = {
      candidate_id: '36028797018963969',
      native: true,
      modifier: false,
      base_score: 75,
      positive_rules: ['water-world-tourism'],
      negative_rules: [],
      positive_adjustment: 10,
      negative_adjustment: 0,
      local_score: 85,
      system_weight: 0.82,
      contributors: ['native:water world'],
      evidence: [
        {
          feature_type: 'rings',
          known: false,
          weight: 0.05,
          confidence: 0,
          provenance: null,
          value: null,
        },
        {
          feature_type: 'rings',
          known: false,
          weight: 0.05,
          confidence: 0,
          provenance: null,
          value: null,
        },
        {
          feature_type: 'body_class',
          known: true,
          weight: 0.175,
          confidence: 1,
          provenance: 'x',
          value: 'water world',
        },
      ],
    };
    const explained = (sequence: number) =>
      ratingsEnvelope(
        ECONOMIES.map((economy) => ({
          ...ratingRow(economy),
          best_candidate_id: null,
          local_scores: [],
          explanation: [],
          mechanics_version: 'v4-mechanics-2026-09',
          scorer_version: '4.0.0',
          contributions: economy === 'Tourism' ? [contribution] : [],
          best_specialisation_candidate_id: null,
          specialisation_candidates: [],
        })),
        sequence,
      );
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json(explained(1)))
      .mockResolvedValueOnce(json(explained(2)));

    const result = await getSystemRatingExplanation(
      parseId64('9007199254740993'),
      ratingsIdentity(),
    );
    expect(pathOf(fetchMock.mock.calls[0]?.[0])).toBe(
      '/api/ratings/v4/systems/9007199254740993/explanation',
    );
    if (result.kind !== 'explained') throw new Error('expected an explanation');
    const tourism = result.economies.find((row) => row.economy === 'Tourism');
    expect(tourism?.contributions).toEqual([
      {
        bodyKey: '36028797018963969',
        native: true,
        modifier: false,
        base: 75,
        positive: 10,
        negative: 0,
        local: 85,
        weight: 0.82,
        positiveRules: ['water-world-tourism'],
        negativeRules: [],
        contributors: ['native:water world'],
        unknownEvidence: ['rings'],
      },
    ]);

    // A later publication is reported, never shown against older scores.
    await expect(
      getSystemRatingExplanation(
        parseId64('9007199254740993'),
        ratingsIdentity(),
      ),
    ).resolves.toEqual({
      kind: 'republished',
      identity: ratingsIdentity({ publicationSequence: '2' }),
    });
  });
});

describe('Inspect mapping of bodies, rings and stations', () => {
  it('preserves canonical parent keys and orbital distances without Frontier-ID joins', () => {
    const parentKey = '36028797018963969';
    const childKey = '108086391056891905';
    const missingParentKey = '180143985094819841';
    const detail = systemDetailFromV3(
      v3System(),
      [
        v3Body({ body_id: parentKey, frontier_body_id: '4' }),
        v3Body({
          body_id: childKey,
          frontier_body_id: null,
          direct_parent_body_id: parentKey,
          is_main_star: false,
          semi_major_axis_km: 149_597_870.7,
        }),
        v3Body({
          body_id: '216172782113783809',
          frontier_body_id: '7',
          direct_parent_body_id: missingParentKey,
          is_main_star: false,
        }),
      ],
      [],
      [],
    );

    expect(
      detail.bodies.find((body) => body.bodyKey === parentKey),
    ).toMatchObject({
      id: 4,
      directParentBodyKey: null,
      semiMajorAxisKm: null,
    });
    expect(
      detail.bodies.find((body) => body.bodyKey === childKey),
    ).toMatchObject({
      id: null,
      directParentBodyKey: parentKey,
      semiMajorAxisKm: 149_597_870.7,
    });
    expect(
      detail.bodies.find((body) => body.id === 7)?.directParentBodyKey,
    ).toBe(missingParentKey);
  });

  it('keeps hotspots, reserves, resolved placement and economies', () => {
    const detail = systemDetailFromV3(
      v3System(),
      [v3Body()],
      [
        v3Ring({
          body_id: '36028797018963968',
          reserve_type: 'major',
          signals: [
            { signal_type: 'tritium', count: 1 },
            { signal_type: 'painite', count: 0 },
            { signal_type: 'platinum', count: 3 },
            { signal_type: 'alexandrite', count: 1 },
          ],
        }),
      ],
      [
        v3Station({
          station_id: '1',
          name: 'Unresolved',
          body_id: '36028797018963968',
          association_state: 'CONFLICT',
          distance_from_arrival_ls: null,
        }),
        v3Station({
          station_id: '2',
          name: 'Resolved',
          body_id: '36028797018963968',
          association_state: 'BODY_RESOLVED',
          economies: [
            {
              economy: 'tourism',
              economy_weight: null,
              is_primary: false,
              is_secondary: false,
            },
            {
              economy: 'military',
              economy_weight: 0.2,
              is_primary: false,
              is_secondary: false,
            },
            {
              economy: 'high_tech',
              economy_weight: 0.5,
              is_primary: false,
              is_secondary: true,
            },
            {
              economy: 'extraction',
              economy_weight: 0.3,
              is_primary: true,
              is_secondary: false,
            },
          ],
        }),
      ],
    );

    const ring = detail.bodies[0]?.rings[0];
    expect(ring?.reserveLevel).toBe('major');
    expect(ring?.signalsComplete).toBe(true);
    // Zero counts are not hotspots; largest first, then by code.
    expect(ring?.hotspots).toEqual([
      { signalType: 'platinum', count: 3 },
      { signalType: 'alexandrite', count: 1 },
      { signalType: 'tritium', count: 1 },
    ]);

    // Known distance first; only a BODY_RESOLVED placement names a body.
    expect(detail.stations.map((station) => station.name)).toEqual([
      'Resolved',
      'Unresolved',
    ]);
    expect(detail.stations[0]?.bodyKey).toBe('36028797018963968');
    expect(detail.stations[1]?.bodyKey).toBeNull();
    // The system source date is not a station-observation timestamp.
    expect(detail.sourceUpdatedAt).not.toBeNull();
    expect(
      detail.stations.every((station) => station.lastObservedAt === null),
    ).toBe(true);
    expect(detail.stations[0]?.economies.map((e) => e.economy)).toEqual([
      'extraction',
      'high_tech',
      'military',
      'tourism',
    ]);
  });
});
