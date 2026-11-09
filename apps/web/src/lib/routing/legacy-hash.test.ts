import { describe, expect, it, vi } from 'vitest';
import { applyLegacyHash, legacyHashDestination } from './legacy-hash';

describe('legacy hash compatibility', () => {
  it.each([
    ['#finder', '/explore', ''],
    ['#map', '/explore', ''],
    ['#system/9007199254740993', '/inspect', '?system=9007199254740993'],
    [
      '#map/system/18446744073709551615',
      '/inspect',
      '?system=18446744073709551615',
    ],
    ['#my-work', '/review', '?legacy=my-work'],
    ['#compare', '/review', '?legacy=compare'],
    ['#fc', '/plan', '?view=fleet-carrier'],
  ])('maps %s into the canonical journey', (hash, pathname, search) => {
    expect(legacyHashDestination(hash)).toEqual({ pathname, search });
  });

  it('preserves bounded planner state without restoring a planner catch-all route', () => {
    expect(
      legacyHashDestination(
        '#colony-planner/system/18446744073709551615/project/a%20b/mode/export/detail/9007199254740993',
        '?from=share',
      ),
    ).toEqual({
      pathname: '/plan',
      search:
        '?from=share&system=18446744073709551615&project=a+b&mode=export&detail=9007199254740993',
    });
  });

  it.each([
    '#system/-1',
    '#system/1e3',
    '#system/1.5',
    '#system/%201',
    '#system/18446744073709551616',
  ])('drops unsafe id64 in %s', (hash) => {
    expect(legacyHashDestination(hash)).toEqual({
      pathname: '/inspect',
      search: '',
    });
  });

  it('is inactive after replacement and otherwise replaces exactly once', () => {
    const replace = vi.fn();
    expect(applyLegacyHash({ hash: '', search: '' } as Location, replace)).toBe(
      false,
    );
    expect(
      applyLegacyHash(
        {
          hash: '#system/18446744073709551615',
          search: '?source=legacy',
        } as Location,
        replace,
      ),
    ).toBe(true);
    expect(replace).toHaveBeenCalledOnce();
    expect(replace).toHaveBeenCalledWith(
      '/inspect?source=legacy&system=18446744073709551615',
    );
  });
});
