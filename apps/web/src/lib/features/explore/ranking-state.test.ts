import { describe, expect, it } from 'vitest';
import {
  DEFAULT_PAGE_SIZE,
  DEFAULT_RANKING_STATE,
  LEGACY_EXPLORE_PAGE_SIZE,
  NAVIGATION_LIMIT,
  PAGE_SIZE_MAX,
  PAGE_SIZE_MIN,
  RANKING_URL_PARAMETERS,
  WEIGHT_CONTRACT_DEFINED,
  hasRankingSearchParams,
  lastPageCorrection,
  lastValidOffset,
  legacyExploreRequest,
  normalizeRankingState,
  parseRankingSearchParams,
  parseWeightParameters,
  rankingWireWindow,
  selectFinderRequest,
  serializeRankingSearchParams,
  stripRankingSearchParams,
} from './ranking-state';
import { SEARCH_RADIUS_OPTIONS } from './search-state';
import { BODY_FILTER_OPTIONS } from './body-filters';

const params = (query: string) => new URLSearchParams(query);
const parse = (query: string) => parseRankingSearchParams(params(query));
const serialize = (
  state: Parameters<typeof serializeRankingSearchParams>[0],
  base = '',
) => serializeRankingSearchParams(state, params(base)).toString();

describe('FinderRankingState normalization', () => {
  it('defaults to Any, no floor, page size 50, offset 0 and no weights', () => {
    expect(DEFAULT_RANKING_STATE).toEqual({
      archetype: 'any',
      minimumTier: null,
      pageSize: 50,
      offset: 0,
      weights: {},
    });
    expect(normalizeRankingState(undefined)).toEqual(DEFAULT_RANKING_STATE);
    expect(PAGE_SIZE_MIN).toBe(1);
    expect(PAGE_SIZE_MAX).toBe(50);
    expect(DEFAULT_PAGE_SIZE).toBe(50);
    expect(NAVIGATION_LIMIT).toBe(10_000);
  });

  it('never lets Any retain a hidden tier floor', () => {
    expect(
      normalizeRankingState({ archetype: 'any', minimumTier: 'A' }).minimumTier,
    ).toBeNull();
  });

  it('defaults a selected archetype to B and fails closed on C/D', () => {
    expect(normalizeRankingState({ archetype: 'flexible' })).toMatchObject({
      archetype: 'flexible',
      minimumTier: 'B',
    });
    expect(
      normalizeRankingState({ archetype: 'flexible', minimumTier: 'S' })
        .minimumTier,
    ).toBe('S');
    expect(
      normalizeRankingState({
        archetype: 'flexible',
        minimumTier: 'C' as never,
      }).minimumTier,
    ).toBe('B');
    expect(
      normalizeRankingState({ archetype: 'bogus' as never }),
    ).toMatchObject({ archetype: 'any', minimumTier: null });
  });

  it('accepts page sizes 1..50 inclusive and otherwise uses 50', () => {
    expect(normalizeRankingState({ pageSize: 1 }).pageSize).toBe(1);
    expect(normalizeRankingState({ pageSize: 50 }).pageSize).toBe(50);
    for (const pageSize of [0, 51, 24.5, -1, Number.NaN, Infinity]) {
      expect(normalizeRankingState({ pageSize }).pageSize).toBe(50);
    }
  });

  it('applies the exclusive 10,000 offset ceiling before any request', () => {
    expect(normalizeRankingState({ offset: 9_999 }).offset).toBe(9_999);
    expect(normalizeRankingState({ offset: 10_000 }).offset).toBe(9_950);
    expect(normalizeRankingState({ offset: 2_000_000_000 }).offset).toBe(9_950);
    expect(
      normalizeRankingState({ offset: 2_000_000_000, pageSize: 1 }).offset,
    ).toBe(9_999);
    expect(
      normalizeRankingState({ offset: 2_000_000_000, pageSize: 24 }).offset,
    ).toBe(9_984);
    for (const offset of [-1, 1.5, Number.NaN]) {
      expect(normalizeRankingState({ offset }).offset).toBe(0);
    }
  });

  it('keeps weights empty until the slice-2 contract exists', () => {
    expect(WEIGHT_CONTRACT_DEFINED).toBe(false);
    expect(
      normalizeRankingState({ weights: { capacity: 2500 } }).weights,
    ).toEqual({});
  });
});

describe('last valid page calculation', () => {
  it('aligns to the page size and never exceeds the navigation window', () => {
    expect(lastValidOffset(0, 50)).toBe(0);
    expect(lastValidOffset(1, 50)).toBe(0);
    expect(lastValidOffset(50, 50)).toBe(0);
    expect(lastValidOffset(51, 50)).toBe(50);
    expect(lastValidOffset(3, 1)).toBe(2);
    expect(lastValidOffset(10_000, 50)).toBe(9_950);
    expect(lastValidOffset(10_000, 1)).toBe(9_999);
    expect(lastValidOffset(2_000_000_000, 50)).toBe(9_950);
    expect(lastValidOffset(-5, 50)).toBe(0);
    expect(lastValidOffset(120, 0)).toBe(100);
  });

  const envelope = (overrides = {}) => ({
    total: 3,
    navigable_total: 3,
    navigation_limit: 10_000,
    page_size: 1,
    offset: 0,
    ...overrides,
  });

  it('corrects a positive out-of-range response using its own page size', () => {
    expect(lastPageCorrection(envelope({ offset: 3 }))).toBe(2);
    expect(
      lastPageCorrection(
        envelope({ total: 57, navigable_total: 57, page_size: 24, offset: 72 }),
      ),
    ).toBe(48);
    expect(lastPageCorrection(envelope({ total: 1, offset: 50 }))).toBe(0);
  });

  it('keeps a genuine zero-result response at the requested offset', () => {
    for (const offset of [0, 3, 9_999]) {
      expect(
        lastPageCorrection(envelope({ total: 0, navigable_total: 0, offset })),
      ).toBeNull();
    }
  });

  it('does not replace valid pages, including a partially filled final page', () => {
    for (const offset of [0, 1, 2]) {
      expect(lastPageCorrection(envelope({ offset }))).toBeNull();
    }
    expect(
      lastPageCorrection(
        envelope({ total: 57, navigable_total: 57, page_size: 24, offset: 48 }),
      ),
    ).toBeNull();
  });

  it('aligns a crossing page to the last page of the 10,000 navigation window', () => {
    const saturated = {
      total: 10_000,
      navigable_total: 10_000,
      page_size: 50,
    };
    expect(lastPageCorrection(envelope({ ...saturated, offset: 9_975 }))).toBe(
      9_950,
    );
    expect(
      lastPageCorrection(envelope({ ...saturated, offset: 9_950 })),
    ).toBeNull();
    expect(
      lastPageCorrection(
        envelope({ ...saturated, page_size: 24, offset: 9_999 }),
      ),
    ).toBe(9_984);
    expect(
      lastPageCorrection(
        envelope({ ...saturated, page_size: 24, offset: 9_984 }),
      ),
    ).toBeNull();
  });

  it('bounds correction to the envelope navigation total and hard ceiling', () => {
    expect(
      lastPageCorrection(
        envelope({
          total: 20_000,
          navigable_total: 20_000,
          navigation_limit: 20_000,
          page_size: 50,
          offset: 10_000,
        }),
      ),
    ).toBe(9_950);
  });
});

describe('ranking URL parsing', () => {
  it('names the exact F4 parameters', () => {
    expect(RANKING_URL_PARAMETERS).toEqual([
      'archetype',
      'min-tier',
      'page_size',
      'offset',
      'weight',
    ]);
  });

  it('parses an empty URL as the default state, canonically', () => {
    expect(parse('')).toEqual({
      state: DEFAULT_RANKING_STATE,
      issues: [],
      canonical: true,
    });
  });

  it('round-trips a complete selected-mode URL', () => {
    const parsed = parse(
      'archetype=manufacturing_hub&min-tier=A&page_size=1&offset=50',
    );
    expect(parsed.state).toEqual({
      archetype: 'manufacturing_hub',
      minimumTier: 'A',
      pageSize: 1,
      offset: 50,
      weights: {},
    });
    expect(parsed.issues).toEqual([]);
    expect(parsed.canonical).toBe(true);
    expect(serialize(parsed.state)).toBe(
      'archetype=manufacturing_hub&min-tier=A&page_size=1&offset=50',
    );
  });

  it('canonicalizes archetype=any to omission', () => {
    const parsed = parse('archetype=any');
    expect(parsed.state.archetype).toBe('any');
    expect(parsed.issues).toEqual([]);
    expect(parsed.canonical).toBe(false);
    expect(serialize(parsed.state)).toBe('');
  });

  it('fails an unknown archetype closed to Any with a non-blocking notice', () => {
    const parsed = parse('archetype=agri_hub&min-tier=A');
    expect(parsed.state).toEqual(DEFAULT_RANKING_STATE);
    expect(parsed.issues).toEqual([
      { parameter: 'archetype', reason: 'unsupported', value: 'agri_hub' },
      { parameter: 'min-tier', reason: 'ignored', value: 'A' },
    ]);
    expect(parsed.canonical).toBe(false);
  });

  it('ignores and removes min-tier in Any mode and omits the B default', () => {
    expect(parse('min-tier=S').state.minimumTier).toBeNull();
    expect(serialize(parse('min-tier=S').state)).toBe('');
    const selectedB = parse('archetype=flexible&min-tier=B');
    expect(selectedB.state.minimumTier).toBe('B');
    expect(selectedB.canonical).toBe(false);
    expect(serialize(selectedB.state)).toBe('archetype=flexible');
  });

  it('treats C/D as unsupported and falls back to B in selected mode', () => {
    const parsed = parse('archetype=flexible&min-tier=C');
    expect(parsed.state.minimumTier).toBe('B');
    expect(parsed.issues).toEqual([
      { parameter: 'min-tier', reason: 'unsupported', value: 'C' },
    ]);
  });

  it('rejects duplicate, fractional, negative and out-of-range page sizes/offsets', () => {
    expect(parse('page_size=1&page_size=2')).toMatchObject({
      state: { pageSize: 50 },
      issues: [{ parameter: 'page_size', reason: 'duplicate' }],
    });
    expect(parse('offset=5&offset=6')).toMatchObject({
      state: { offset: 0 },
      issues: [{ parameter: 'offset', reason: 'duplicate' }],
    });
    for (const value of ['1.5', '-1', 'abc', '', '+3', '0x10', '1e3']) {
      expect(parse(`page_size=${value}`).state.pageSize).toBe(50);
      expect(parse(`offset=${value}`).state.offset).toBe(0);
    }
    expect(parse('page_size=0')).toMatchObject({
      state: { pageSize: 50 },
      issues: [{ parameter: 'page_size', reason: 'out-of-range' }],
    });
    expect(parse('page_size=51').state.pageSize).toBe(50);
    expect(parse('page_size=50').state.pageSize).toBe(50);
    expect(parse('page_size=1').state.pageSize).toBe(1);
    expect(parse('offset=0').canonical).toBe(false);
    expect(parse('offset=1').state.offset).toBe(1);
  });

  it('corrects an offset at or beyond 10,000 to the last valid page from the URL alone', () => {
    const fresh = parse('offset=2000000000');
    expect(fresh.state.offset).toBe(9_950);
    expect(fresh.issues).toEqual([
      { parameter: 'offset', reason: 'out-of-range', value: '2000000000' },
    ]);
    expect(fresh.canonical).toBe(false);
    expect(serialize(fresh.state)).toBe('offset=9950');
    expect(parse('page_size=1&offset=10000').state.offset).toBe(9_999);
    expect(parse('offset=9999').state.offset).toBe(9_999);
    expect(parse('offset=99999999999999999999').state.offset).toBe(9_950);
  });

  it('preserves selected, system and unrelated parameters in serialization', () => {
    const base =
      'view=cards&offset=50&selected=9007199254740993&archetype=flexible&system=158872029&zoom=2';
    const parsed = parseRankingSearchParams(params(base));
    expect(parsed.canonical).toBe(false);
    expect(serialize(parsed.state, base)).toBe(
      'archetype=flexible&offset=50&selected=9007199254740993&system=158872029&view=cards&zoom=2',
    );
    const stripped = stripRankingSearchParams(params(base));
    expect(stripped.toString()).toBe(
      'view=cards&selected=9007199254740993&system=158872029&zoom=2',
    );
    expect(hasRankingSearchParams(params(base))).toBe(true);
    expect(hasRankingSearchParams(stripped)).toBe(false);
    expect(hasRankingSearchParams(params('weight=capacity:2500'))).toBe(true);
  });

  it('serializes keys in the canonical order so one state yields one URL', () => {
    const state = {
      offset: 100,
      pageSize: 10,
      minimumTier: 'S' as const,
      archetype: 'stronghold' as const,
      weights: {},
    };
    expect(serialize(state)).toBe(
      'archetype=stronghold&min-tier=S&page_size=10&offset=100',
    );
    expect(
      serialize(state, 'offset=3&page_size=7&min-tier=A&archetype=paradise'),
    ).toBe('archetype=stronghold&min-tier=S&page_size=10&offset=100');
  });

  it('does not parse or emit weights until slice 2 defines the contract', () => {
    const parsed = parse('weight=capacity:2500&weight=risk:100');
    expect(parsed.state.weights).toEqual({});
    expect(parsed.issues).toEqual([
      { parameter: 'weight', reason: 'ignored', value: 'capacity:2500' },
      { parameter: 'weight', reason: 'ignored', value: 'risk:100' },
    ]);
    expect(serialize(parsed.state)).toBe('');
  });

  it('never promotes supported body counts or economy names into ranking weights', () => {
    const dimensions = [
      ...BODY_FILTER_OPTIONS.map(({ key }) => key),
      'agriculture',
      'industrial',
    ];
    for (const archetype of ['any', 'paradise'] as const) {
      const query = new URLSearchParams({ archetype });
      for (const dimension of dimensions)
        query.append('weight', `${dimension}:2500`);
      const parsed = parseRankingSearchParams(query);
      expect(parsed.state.weights).toEqual({});
      expect(parsed.issues).toEqual(
        dimensions.map((dimension) => ({
          parameter: 'weight',
          reason: 'ignored',
          value: `${dimension}:2500`,
        })),
      );
      expect(
        serializeRankingSearchParams(parsed.state, query).has('weight'),
      ).toBe(false);
      for (const enabled of [false, true]) {
        for (const anchor of [null, { x: 1, y: 2, z: 3 }]) {
          const request = selectFinderRequest(parsed.state, anchor, {
            enabled,
          });
          expect(request).toEqual(
            selectFinderRequest(normalizeRankingState({ archetype }), anchor, {
              enabled,
            }),
          );
          expect(request.request).not.toHaveProperty('weights');
          expect(request.request).not.toHaveProperty('weight');
        }
      }
    }
  });

  it('parses basis-point weights only when the contract is explicitly enabled', () => {
    const enabled = parseWeightParameters(
      ['capacity:2500', 'risk:10000', 'risk:5', 'bad', 'over:10001', 'neg:-1'],
      { contractDefined: true },
    );
    expect(enabled.weights).toEqual({ capacity: 2500, risk: 10_000 });
    expect(enabled.issues).toEqual([
      { parameter: 'weight', reason: 'duplicate', value: 'risk:5' },
      { parameter: 'weight', reason: 'invalid', value: 'bad' },
      { parameter: 'weight', reason: 'invalid', value: 'over:10001' },
      { parameter: 'weight', reason: 'invalid', value: 'neg:-1' },
    ]);
    expect(parseWeightParameters(['capacity:2500']).weights).toEqual({});
  });
});

describe('request selection', () => {
  const anchor = { x: 1, y: 2, z: 3 };

  it("keeps the disabled build on exactly today's request for any state", () => {
    const hand = parse('archetype=flexible&min-tier=A&page_size=1&offset=1');
    expect(selectFinderRequest(hand.state, null, { enabled: false })).toEqual({
      kind: 'any',
      request: {
        galaxy_wide: true,
        sort_by: 'development',
        size: 24,
        from: 0,
      },
      pageSize: 24,
    });
    expect(selectFinderRequest(hand.state, anchor, { enabled: false })).toEqual(
      {
        kind: 'any',
        request: {
          reference_coords: { x: 1, y: 2, z: 3 },
          filters: { distance: { min: 0, max: 500 }, economy: 'any' },
          sort_by: 'distance',
          size: 24,
          from: 0,
        },
        pageSize: 24,
      },
    );
    expect(LEGACY_EXPLORE_PAGE_SIZE).toBe(24);
    expect(legacyExploreRequest({ x: 1, y: null, z: 3 })).toEqual({
      galaxy_wide: true,
      sort_by: 'development',
      size: 24,
      from: 0,
    });
  });

  it('sends Any to local search with no tier field and codec-driven size/from', () => {
    expect(
      selectFinderRequest(parse('').state, null, { enabled: true }),
    ).toEqual({
      kind: 'any',
      request: {
        galaxy_wide: true,
        sort_by: 'development',
        size: 50,
        from: 0,
      },
      pageSize: 50,
    });
    const page = selectFinderRequest(
      parse('page_size=1&offset=2').state,
      anchor,
      { enabled: true },
    );
    expect(page).toEqual({
      kind: 'any',
      request: {
        reference_coords: { x: 1, y: 2, z: 3 },
        filters: { distance: { min: 0, max: 500 }, economy: 'any' },
        sort_by: 'distance',
        size: 1,
        from: 2,
      },
      pageSize: 1,
    });
    expect(page.request).not.toHaveProperty('min_development_score');
  });

  it.each(SEARCH_RADIUS_OPTIONS)(
    'uses radius %s for anchored Any in both ranking builds',
    (radius) => {
      for (const enabled of [false, true]) {
        const page = selectFinderRequest(
          parse('page_size=1&offset=2').state,
          anchor,
          {
            enabled,
            radius,
          },
        );
        expect(page).toEqual({
          kind: 'any',
          request: {
            reference_coords: anchor,
            filters: { distance: { min: 0, max: radius }, economy: 'any' },
            sort_by: 'distance',
            size: enabled ? 1 : 24,
            from: enabled ? 2 : 0,
          },
          pageSize: enabled ? 1 : 24,
        });
      }
    },
  );

  it('uses the default radius for invalid runtime input', () => {
    for (const enabled of [false, true]) {
      expect(
        selectFinderRequest(parse('').state, anchor, {
          enabled,
          radius: 2001 as never,
        }).request,
      ).toMatchObject({ filters: { distance: { min: 0, max: 500 } } });
    }
  });

  it('leaves galaxy-wide and archetype requests unchanged by the radius', () => {
    for (const enabled of [false, true]) {
      expect(
        selectFinderRequest(parse('').state, null, { enabled, radius: 1000 }),
      ).toEqual(selectFinderRequest(parse('').state, null, { enabled }));
    }
    const state = parse(
      'archetype=mining_hub&min-tier=S&page_size=24&offset=48',
    ).state;
    expect(
      selectFinderRequest(state, anchor, { enabled: true, radius: 1000 }),
    ).toEqual(selectFinderRequest(state, anchor, { enabled: true }));
  });

  it('sends a selected archetype to rankings with the B default and facade page size', () => {
    expect(
      selectFinderRequest(parse('archetype=flexible').state, anchor, {
        enabled: true,
      }),
    ).toEqual({
      kind: 'rankings',
      request: {
        archetype: 'flexible',
        min_score: 60,
        page_size: 50,
        offset: 0,
      },
    });
    expect(
      selectFinderRequest(
        parse('archetype=mining_hub&min-tier=S&page_size=1&offset=2').state,
        null,
        { enabled: true },
      ),
    ).toEqual({
      kind: 'rankings',
      request: {
        archetype: 'mining_hub',
        min_score: 88,
        page_size: 1,
        offset: 2,
      },
    });
  });

  it('clamps the wire window at the 10,000 boundary', () => {
    expect(rankingWireWindow({ pageSize: 50, offset: 9_950 })).toEqual({
      limit: 50,
      offset: 9_950,
    });
    expect(rankingWireWindow({ pageSize: 50, offset: 9_975 })).toEqual({
      limit: 25,
      offset: 9_975,
    });
    expect(rankingWireWindow({ pageSize: 50, offset: 10_000 })).toEqual({
      limit: 50,
      offset: 9_950,
    });
    const nearCeiling = selectFinderRequest(parse('offset=9975').state, null, {
      enabled: true,
    });
    expect(nearCeiling.request).toMatchObject({ size: 25, from: 9_975 });
  });

  it('falls back to the galaxy-wide request for non-finite anchor coordinates', () => {
    for (const bad of [
      { x: Number.NaN, y: 2, z: 3 },
      { x: 1, y: Number.POSITIVE_INFINITY, z: 3 },
      { x: 1, y: 2, z: Number.NEGATIVE_INFINITY },
    ]) {
      expect(legacyExploreRequest(bad)).toEqual({
        galaxy_wide: true,
        sort_by: 'development',
        size: 24,
        from: 0,
      });
      expect(
        selectFinderRequest(parse('').state, bad, { enabled: true }).request,
      ).toEqual({
        galaxy_wide: true,
        sort_by: 'development',
        size: 50,
        from: 0,
      });
    }
  });

  it('keeps the facade page size distinct from the clamped wire size near the ceiling in both modes', () => {
    const any = selectFinderRequest(
      parse('page_size=50&offset=9975').state,
      null,
      { enabled: true },
    );
    expect(any).toEqual({
      kind: 'any',
      request: {
        galaxy_wide: true,
        sort_by: 'development',
        size: 25,
        from: 9_975,
      },
      pageSize: 50,
    });
    const selected = selectFinderRequest(
      parse('archetype=flexible&page_size=50&offset=9975').state,
      null,
      { enabled: true },
    );
    expect(selected).toEqual({
      kind: 'rankings',
      request: {
        archetype: 'flexible',
        min_score: 60,
        page_size: 50,
        offset: 9_975,
      },
    });
  });
});
