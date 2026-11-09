import { describe, expect, it } from 'vitest';
import type { AutocompleteSystem } from '$lib/api/client';
import { parseId64 } from '$lib/domain/id64';
import { ANY_ARCHETYPE, ARCHETYPES } from '$lib/features/explore/archetypes';
import { parseRankingSearchParams } from '$lib/features/explore/ranking-state';
import { parseExploreSearchParams } from '$lib/features/explore/search-state';
import { buildHomeSearchUrl } from './search-url';

const selected = { id64: parseId64('9007199254740993') };

describe('home search URL', () => {
  it('uses the selected lossless id64 for a near-only URL', () => {
    expect(buildHomeSearchUrl(selected)).toBe('/explore?near=9007199254740993');
    expect(
      buildHomeSearchUrl({ id64: parseId64('18446744073709551615') }),
    ).toBe('/explore?near=18446744073709551615');
  });

  it.each(ARCHETYPES)(
    'serializes enabled $label in canonical order',
    ({ key }) => {
      const destination = buildHomeSearchUrl(selected, key, true);
      expect(destination).toBe(
        `/explore?archetype=${key}&near=${selected.id64}`,
      );
      const params = new URL(destination!, 'http://localhost').searchParams;
      expect(parseExploreSearchParams(params)).toMatchObject({
        near: selected.id64,
        issues: [],
      });
      expect(parseRankingSearchParams(params)).toMatchObject({
        state: { archetype: key, minimumTier: 'B', offset: 0 },
        issues: [],
        canonical: true,
      });
    },
  );

  it('omits Any and every archetype when the feature flag is off', () => {
    for (const enabled of [false, true]) {
      expect(buildHomeSearchUrl(selected, ANY_ARCHETYPE, enabled)).toBe(
        '/explore?near=9007199254740993',
      );
    }
    for (const archetype of ['mining_hub', 'unknown', '']) {
      expect(buildHomeSearchUrl(selected, archetype, false)).toBe(
        '/explore?near=9007199254740993',
      );
    }
  });

  it.each([null, undefined])('requires a chosen suggestion: %s', (value) => {
    expect(buildHomeSearchUrl(value)).toBeNull();
  });

  it.each([
    '',
    'Sol',
    '01',
    '-1',
    '1.5',
    '1e3',
    ' 42',
    '42 ',
    '18446744073709551616',
    '1'.repeat(21),
    42,
    9007199254740993n,
    null,
    undefined,
  ])('rejects an invalid selected id64: %s', (id64) => {
    const malformed = { id64 } as unknown as Pick<AutocompleteSystem, 'id64'>;
    expect(buildHomeSearchUrl(malformed)).toBeNull();
  });

  it.each(['', 'unknown', 'Mining Hub', 'any&near=42'])(
    'fails closed on unsupported enabled archetype %s',
    (archetype) => {
      expect(buildHomeSearchUrl(selected, archetype, true)).toBeNull();
    },
  );
});
