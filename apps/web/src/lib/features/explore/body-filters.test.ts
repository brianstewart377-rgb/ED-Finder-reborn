import { describe, expect, it } from 'vitest';
import {
  BODY_FILTER_GROUPS,
  BODY_FILTER_OPTIONS,
  HARD_BODY_COUNT_MAX,
  bodyFilterLabel,
  normalizeBodyFilters,
} from './body-filters';

describe('body-count filters', () => {
  it('exposes every requested count in the three groups', () => {
    expect(
      BODY_FILTER_GROUPS.map(({ label, filters }) => [label, filters.length]),
    ).toEqual([
      ['Planets', 10],
      ['Stars', 4],
      ['Features', 5],
    ]);
    expect(new Set(BODY_FILTER_OPTIONS.map(({ key }) => key)).size).toBe(19);
    expect(BODY_FILTER_OPTIONS.map(({ key }) => key)).toEqual([
      'elw_count',
      'ww_count',
      'ammonia_count',
      'terraformable_count',
      'gas_giant_count',
      'hmc_count',
      'metal_rich_count',
      'rocky_count',
      'rocky_ice_count',
      'icy_count',
      'black_hole_count',
      'neutron_count',
      'white_dwarf_count',
      'other_star_count',
      'landable_count',
      'walkable_count',
      'ring_count',
      'bio_signal_total',
      'geo_signal_total',
    ]);
    expect(HARD_BODY_COUNT_MAX).toBe(999);
  });

  it('keeps whole counts above the slider maximum through the hard bound', () => {
    expect(
      normalizeBodyFilters({
        elw_count: { min: 21, max: 999 },
        ring_count: { max: 0 },
      }),
    ).toEqual({ elw_count: { min: 21, max: 999 }, ring_count: { max: 0 } });
  });

  it.each([
    null,
    [],
    'elw_count:1-',
    { unknown: { min: 1 } },
    { elw_count: {} },
    { elw_count: { min: -1 } },
    { elw_count: { min: 1.5 } },
    { elw_count: { min: '1' } },
    { elw_count: { min: null } },
    { elw_count: { max: 1000 } },
    { elw_count: { min: Infinity } },
    { elw_count: { min: NaN } },
    { elw_count: { min: 3, max: 2 } },
    { elw_count: { min: 1, unknown: 2 } },
    { elw_count: { min: 1, weight: 2 } },
    { body_count_weight: { min: 1 } },
    { economy_weights: { min: 1 } },
  ])('drops invalid values fail closed: %j', (input) => {
    expect(normalizeBodyFilters(input)).toEqual({});
  });

  it('preserves independent valid filters and does not mutate its input', () => {
    const input = { elw_count: { min: 3, max: 1 }, ring_count: { max: 3 } };
    const result = normalizeBodyFilters(input);
    expect(result).toEqual({ ring_count: { max: 3 } });
    expect(result.ring_count).not.toBe(input.ring_count);
    expect(input.elw_count).toEqual({ min: 3, max: 1 });
  });

  it('retains all 19 count constraints without accepting speculative ranking weights', () => {
    const constraints = Object.fromEntries(
      BODY_FILTER_OPTIONS.map(({ key }, index) => [
        key,
        { min: index, max: index + 1 },
      ]),
    );
    const normalized = normalizeBodyFilters({
      ...constraints,
      weights: { elw_count: 2 },
      economy_weights: { agriculture: 1 },
    });
    expect(normalized).toEqual(constraints);
    expect(JSON.stringify(normalized)).not.toContain('weight');
    for (const { key, label } of BODY_FILTER_OPTIONS) {
      expect(bodyFilterLabel(key, normalized[key]!)).toMatch(
        new RegExp(`^${label} \\d+–\\d+$`),
      );
    }
  });

  it('labels lower, upper, closed and exact ranges', () => {
    expect(bodyFilterLabel('elw_count', { min: 1 })).toBe('Earth-like ≥ 1');
    expect(bodyFilterLabel('landable_count', { min: 2, max: 5 })).toBe(
      'Landable 2–5',
    );
    expect(bodyFilterLabel('ring_count', { max: 3 })).toBe('Rings ≤ 3');
    expect(bodyFilterLabel('neutron_count', { min: 0, max: 0 })).toBe(
      'Neutron stars = 0',
    );
  });
});
