import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getSystem, getSystemRatings } from '$lib/api/client';
import { queryClient } from '$lib/api/query';
import {
  referenceSystemDetail,
  referenceSystemRatings,
} from '$lib/api/v3-system-fixtures';
import { auth } from '$lib/auth/auth';
import { parseId64 } from '$lib/domain/id64';
import { selectedSystem } from '$lib/persistence/stores';
import InspectPageTestHost from '../../routes/inspect/InspectPageTestHost.svelte';
import AppShellTestHost from './AppShellTestHost.svelte';

type TestPage = { url: URL; state: App.PageState };
const { navigation, featureGate, mockedGoto } = vi.hoisted(() => ({
  navigation: {
    page: {
      url: new URL('http://localhost/explore'),
      state: {} as App.PageState,
    },
  },
  featureGate: { enabled: false },
  mockedGoto: vi.fn(),
}));
vi.mock('$app/state', () => ({
  get page() {
    return navigation.page;
  },
}));
vi.mock('$app/navigation', () => ({ goto: mockedGoto }));
vi.mock('$lib/config', () => ({
  get FINDER_F4_ENABLED() {
    return featureGate.enabled;
  },
}));
vi.mock('$lib/api/client', async (loadOriginal) => ({
  ...(await loadOriginal<typeof import('$lib/api/client')>()),
  getSystem: vi.fn(),
  getSystemRatings: vi.fn(),
  getSystemRatingExplanation: vi.fn(),
}));

const selectedId = parseId64('9007199254740993');
const inspectedId = parseId64('42');
const anchor = {
  id64: parseId64('43'),
  name: 'Anchor A',
  x: 12,
  y: -34,
  z: 56,
};

function mount(path: string, state: App.PageState = {}) {
  return render(AppShellTestHost, {
    props: {
      initialPage: { url: new URL(path, 'http://localhost'), state },
      installPage: (page: TestPage) => {
        navigation.page = page;
      },
    },
  });
}

function restore(path: string, state: App.PageState = {}) {
  navigation.page.state = state;
  navigation.page.url = new URL(path, 'http://localhost');
}

beforeEach(() => {
  vi.clearAllMocks();
  queryClient.clear();
  localStorage.clear();
  selectedSystem.hydrate();
  selectedSystem.set(null);
  featureGate.enabled = false;
  vi.spyOn(auth, 'bootstrap').mockResolvedValue(undefined);
  vi.mocked(getSystem).mockImplementation(async (id64) =>
    referenceSystemDetail({ id64, name: `System ${id64}` }),
  );
  vi.mocked(getSystemRatings).mockResolvedValue(referenceSystemRatings());
  mockedGoto.mockImplementation(
    async (destination: string | URL, options?: { state?: App.PageState }) => {
      restore(String(destination), options?.state ?? {});
    },
  );
});

afterEach(() => {
  cleanup();
  queryClient.clear();
  vi.restoreAllMocks();
});

describe('AppShell selection hydration', () => {
  it('keeps the default-off system-driven selection and plain Inspect link', async () => {
    mount(`/explore?selected=${selectedId}&system=${inspectedId}`);
    await screen.findByRole('dialog', { name: 'System Detail' });
    const chip = screen.getByTestId('selected-system-context');
    expect(chip).toHaveTextContent(inspectedId);
    expect(within(chip).getByRole('link', { name: 'Inspect' })).toHaveAttribute(
      'href',
      `/inspect?system=${inspectedId}`,
    );
    await fireEvent.click(
      screen.getByRole('button', { name: 'Close system detail' }),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(get(selectedSystem).value).toBe(inspectedId);
    expect(navigation.page.url.searchParams.get('selected')).toBe(selectedId);
  });

  it('does not apply selected or clear persisted selection when disabled', async () => {
    selectedSystem.set(inspectedId);
    mount(`/explore?selected=${selectedId}`);
    expect(
      await screen.findByTestId('selected-system-context'),
    ).toHaveTextContent(inspectedId);
    restore('/explore');
    await waitFor(() => expect(get(selectedSystem).value).toBe(inspectedId));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(mockedGoto).not.toHaveBeenCalled();
  });

  it("retains main's exact selection writes across disabled detail visits", async () => {
    const setSelection = vi.spyOn(selectedSystem, 'set');
    mount(`/explore?system=${inspectedId}`);
    await screen.findByRole('dialog', { name: 'System Detail' });
    expect(setSelection.mock.calls).toEqual([[inspectedId]]);

    // Unrelated query changes leave the same visit and selection untouched.
    restore(`/explore?system=${inspectedId}&selected=${selectedId}`);
    await waitFor(() =>
      expect(navigation.page.url.searchParams.get('selected')).toBe(selectedId),
    );
    expect(setSelection.mock.calls).toEqual([[inspectedId]]);

    restore('/explore');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(setSelection.mock.calls).toEqual([[inspectedId]]);
    restore(`/explore?system=${inspectedId}`);
    await screen.findByRole('dialog', { name: 'System Detail' });
    // Main restarts the URL-selection latch after a visit without system=.
    expect(setSelection.mock.calls).toEqual([[inspectedId], [inspectedId]]);
  });

  it('hydrates a lossless passive selection without opening detail', async () => {
    featureGate.enabled = true;
    mount(`/explore?selected=${selectedId}`);
    expect(
      await screen.findByTestId('selected-system-context'),
    ).toHaveTextContent(selectedId);
    expect(get(selectedSystem).value).toBe(selectedId);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(getSystem).not.toHaveBeenCalled();
    expect(mockedGoto).not.toHaveBeenCalled();
  });

  it('opens explicit detail without creating a passive selection', async () => {
    featureGate.enabled = true;
    selectedSystem.set(selectedId);
    mount(`/explore?system=${inspectedId}`);
    await screen.findByRole('dialog', { name: 'System Detail' });
    expect(get(selectedSystem).value).toBeNull();
    expect(screen.queryByTestId('selected-system-context')).toBeNull();
  });

  it('closes only system, and clearing selected leaves the independent overlay open', async () => {
    featureGate.enabled = true;
    const path = `/explore?archetype=flexible&selected=${selectedId}&system=${inspectedId}&view=cards`;
    mount(path, { anchor });
    await screen.findByRole('dialog', { name: 'System Detail' });
    expect(get(selectedSystem).value).toBe(selectedId);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Close system detail' }),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(navigation.page.url.searchParams.toString()).toBe(
      `archetype=flexible&selected=${selectedId}&view=cards`,
    );
    expect(get(selectedSystem).value).toBe(selectedId);
    expect(navigation.page.state.anchor).toEqual(anchor);

    restore(path, { anchor });
    await screen.findByRole('dialog', { name: 'System Detail' });
    const cleared = new URL(navigation.page.url);
    cleared.searchParams.delete('selected');
    restore(cleared.href, { anchor });
    await waitFor(() => expect(get(selectedSystem).value).toBeNull());
    expect(
      screen.getByRole('dialog', { name: 'System Detail' }),
    ).toBeInTheDocument();
    expect(navigation.page.url.searchParams.get('system')).toBe(inspectedId);
    expect(navigation.page.url.searchParams.get('archetype')).toBe('flexible');
    expect(navigation.page.url.searchParams.get('view')).toBe('cards');
    expect(screen.queryByTestId('selected-system-context')).toBeNull();
  });

  it('rehydrates navigation and Back/Forward page URLs without writing history', async () => {
    featureGate.enabled = true;
    const paths = [
      `/explore?selected=${selectedId}`,
      `/explore?selected=${inspectedId}`,
      '/explore',
    ];
    mount(paths[0]);
    await waitFor(() => expect(get(selectedSystem).value).toBe(selectedId));
    restore(paths[1]);
    await waitFor(() => expect(get(selectedSystem).value).toBe(inspectedId));
    restore(paths[2]);
    await waitFor(() => expect(get(selectedSystem).value).toBeNull());
    // SvelteKit publishes the restored URL through page on popstate.
    for (const [path, expected] of [
      [paths[1], inspectedId],
      [paths[0], selectedId],
      [paths[1], inspectedId],
    ] as const) {
      restore(path);
      await fireEvent(window, new PopStateEvent('popstate'));
      await waitFor(() => expect(get(selectedSystem).value).toBe(expected));
    }
    expect(mockedGoto).not.toHaveBeenCalled();
  });

  it.each(['selected=invalid', 'selected=42&selected=43', 'selected='])(
    'fails closed on %s without falling back to the overlay id',
    async (query) => {
      featureGate.enabled = true;
      selectedSystem.set(selectedId);
      mount(`/explore?${query}&system=${inspectedId}`);
      await screen.findByRole('dialog', { name: 'System Detail' });
      expect(get(selectedSystem).value).toBeNull();
    },
  );

  it('retains the existing detail selection contract on other routes', async () => {
    featureGate.enabled = true;
    mount(`/inspect?system=${inspectedId}&selected=${selectedId}`);
    expect(
      await screen.findByTestId('selected-system-context'),
    ).toHaveTextContent(inspectedId);
    expect(screen.queryByRole('dialog')).toBeNull();
    restore('/plan');
    await waitFor(() => expect(get(selectedSystem).value).toBe(inspectedId));
    restore('/explore');
    await waitFor(() => expect(get(selectedSystem).value).toBeNull());
  });

  it('round trips the Explore query and current anchor through the context chip Inspect link', async () => {
    featureGate.enabled = true;
    const shell = mount(
      `/explore?archetype=flexible&min-tier=A&page_size=1&offset=2&selected=${selectedId}`,
      { anchor },
    );
    const chip = await screen.findByTestId('selected-system-context');
    const link = within(chip).getByRole('link', { name: 'Inspect' });
    const origin = navigation.page.url.searchParams.toString();
    const destination = new URL(
      link.getAttribute('href')!,
      navigation.page.url,
    );
    expect(destination.pathname).toBe('/inspect');
    expect(destination.searchParams.get('from')).toBe(origin);
    expect(destination.searchParams.get('system')).toBe(selectedId);
    const currentAnchor = { ...anchor, x: 99 };
    navigation.page.state = { anchor: currentAnchor };
    await fireEvent.click(link);
    expect(mockedGoto).toHaveBeenCalledWith(
      destination.pathname + destination.search,
      {
        replaceState: false,
        state: { anchor: currentAnchor },
        keepFocus: true,
        noScroll: true,
      },
    );
    shell.unmount();
    const inspect = render(InspectPageTestHost, {
      props: {
        initialPage: navigation.page,
        installPage: (page: TestPage) => {
          navigation.page = page;
        },
      },
    });
    await fireEvent.click(
      screen.getByRole('link', { name: 'Open in Explore' }),
    );
    const expected = new URLSearchParams(origin);
    expected.set('system', selectedId);
    expect(navigation.page.url.searchParams.toString()).toBe(
      expected.toString(),
    );
    expect(navigation.page.state.anchor).toEqual(currentAnchor);
    inspect.unmount();
    mount(navigation.page.url.href, navigation.page.state);
    await screen.findByRole('dialog', { name: 'System Detail' });
    expect(get(selectedSystem).value).toBe(selectedId);
  });
});
