import { beforeEach, describe, expect, it, vi } from 'vitest';
import { parseId64 } from '$lib/domain/id64';
import {
  inspectHref,
  navigateWithAnchor,
  readExploreAnchor,
  type ExploreAnchor,
} from './navigation';

const { mockedPage, mockedGoto } = vi.hoisted(() => ({
  mockedPage: { state: {} as App.PageState },
  mockedGoto: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('$app/state', () => ({ page: mockedPage }));
vi.mock('$app/navigation', () => ({ goto: mockedGoto }));

const anchor: ExploreAnchor = {
  id64: parseId64('9007199254740993'),
  name: 'Anchor A',
  x: 0,
  y: -2,
  z: 3,
};

beforeEach(() => {
  mockedGoto.mockClear();
  mockedPage.state = { anchor };
});

describe('anchor-preserving SvelteKit navigation', () => {
  it('pushes with the current history state and retains focus and scroll', async () => {
    await navigateWithAnchor('/explore?offset=1');
    expect(mockedGoto).toHaveBeenCalledWith('/explore?offset=1', {
      replaceState: false,
      state: mockedPage.state,
      keepFocus: true,
      noScroll: true,
    });
    expect(mockedGoto.mock.calls[0][1].state).toBe(mockedPage.state);
  });

  it('reads the latest state for replacement instead of capturing an earlier anchor', async () => {
    const restored = { anchor: { ...anchor, name: 'Anchor B', x: 100 } };
    mockedPage.state = restored;
    const destination = new URL('http://localhost/explore?offset=2');
    await navigateWithAnchor(destination, { replaceState: true });
    expect(mockedGoto).toHaveBeenCalledWith(destination, {
      replaceState: true,
      state: restored,
      keepFocus: true,
      noScroll: true,
    });
  });

  it('merges explicit anchor commits and clears without mutating earlier history', async () => {
    const prior = { anchor, retainedContext: 'keep' };
    mockedPage.state = prior;
    await navigateWithAnchor('/explore', { state: { anchor: null } });
    expect(mockedGoto.mock.calls[0][1].state).toEqual({
      anchor: null,
      retainedContext: 'keep',
    });
    const next = { ...anchor, name: 'Anchor B', x: 100 };
    await navigateWithAnchor('/explore?selected=9007199254740993', {
      state: { anchor: next },
    });
    expect(mockedGoto.mock.calls[1][1].state).toEqual({
      anchor: next,
      retainedContext: 'keep',
    });
    expect(prior.anchor).toBe(anchor);
  });

  it('carries history state on the outbound Inspect navigation', async () => {
    const source = new URL(
      'http://localhost/explore?archetype=flexible&min-tier=A&page_size=1&offset=2&selected=7&system=8&view=cards',
    );
    const href = inspectHref(anchor.id64, source);
    const url = new URL(href, source);
    expect(url.pathname).toBe('/inspect');
    expect(url.searchParams.get('system')).toBe('9007199254740993');
    expect(url.searchParams.get('from')).toBe(source.searchParams.toString());
    await navigateWithAnchor(href);
    expect(mockedGoto.mock.calls[0][1].state).toBe(mockedPage.state);
  });
});

describe('committed-anchor history hydration', () => {
  it('retains lossless id64, identity and finite coordinates including zero', () => {
    expect(readExploreAnchor({ anchor })).toEqual(anchor);
    expect(
      readExploreAnchor({ anchor: { ...anchor, x: 0, y: 0, z: 0 } }),
    ).toEqual({ ...anchor, x: 0, y: 0, z: 0 });
  });

  it('treats missing and explicitly cleared state as unanchored', () => {
    for (const state of [undefined, null, {}, { anchor: null }, 'bad']) {
      expect(readExploreAnchor(state)).toBeNull();
    }
  });

  it('rejects malformed identity and missing or non-finite coordinates', () => {
    for (const invalid of [
      { ...anchor, id64: 123 },
      { ...anchor, id64: '01' },
      { ...anchor, id64: '18446744073709551616' },
      { ...anchor, name: '' },
      { ...anchor, name: '   ' },
      { id64: anchor.id64, name: anchor.name },
      { ...anchor, x: null },
      { ...anchor, x: Number.NaN },
      { ...anchor, y: Number.POSITIVE_INFINITY },
      { ...anchor, z: Number.NEGATIVE_INFINITY },
    ]) {
      expect(readExploreAnchor({ anchor: invalid })).toBeNull();
    }
  });
});
