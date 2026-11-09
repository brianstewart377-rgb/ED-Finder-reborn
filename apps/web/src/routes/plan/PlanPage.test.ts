import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { tick } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { queryClient } from '$lib/api/query';
import { getPlanSystem, getV3PlanCatalogue, listV3PlanBodies } from '$lib/api';
import { colonyProjects, selectedSystem } from '$lib/persistence/stores';
import { compactPlanFixture } from '$lib/features/plan/__fixtures__/compact-plan';
import { v3System } from '$lib/api/v3-system-fixtures';
import PlanPageTestHost from './PlanPageTestHost.svelte';

const { mockedPage, goto } = vi.hoisted(() => ({
  mockedPage: {
    url: new URL('http://localhost/plan'),
    state: {} as App.PageState,
  },
  goto: vi.fn(),
}));
vi.mock('$app/state', () => ({ page: mockedPage }));
vi.mock('$app/navigation', () => ({ goto }));
vi.mock('$lib/api/client', async (original) => ({
  ...(await original<typeof import('$lib/api/client')>()),
  getPlanSystem: vi.fn(),
  getV3PlanCatalogue: vi.fn(),
  listV3PlanBodies: vi.fn(),
}));

function renderPage(search = '?system=42') {
  return render(PlanPageTestHost, {
    initialPage: {
      url: new URL(`http://localhost/plan${search}`),
      state: { finderRequested: true },
    },
    installPage(model) {
      Object.defineProperties(mockedPage, {
        url: {
          configurable: true,
          get: () => model.url,
          set: (url: URL) => {
            model.url = url;
          },
        },
        state: {
          configurable: true,
          get: () => model.state,
          set: (state: App.PageState) => {
            model.state = state;
          },
        },
      });
    },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  colonyProjects.hydrate();
  selectedSystem.hydrate();
  queryClient.clear();
  const fixture = compactPlanFixture(2, 0);
  const catalogue = {
    rulebook_release: 'test',
    facilities: fixture.facilities,
  };
  const system = v3System({ id64: '42', name: fixture.systemName });
  vi.mocked(getV3PlanCatalogue).mockResolvedValue(catalogue);
  vi.mocked(listV3PlanBodies).mockResolvedValue(fixture.bodies);
  vi.mocked(getPlanSystem).mockResolvedValue(system);
  queryClient.setQueryData(['ed-finder', 'plan-catalogue'], catalogue);
  queryClient.setQueryData(['ed-finder', 'plan-bodies', '42'], fixture.bodies);
  queryClient.setQueryData(['ed-finder', 'plan-system', '42'], system);
  goto.mockImplementation((url: URL, options: { state: App.PageState }) => {
    mockedPage.url = url;
    mockedPage.state = options.state;
    return Promise.resolve();
  });
});
afterEach(() => {
  cleanup();
  queryClient.clear();
});

describe('Plan task URL integration', () => {
  it.each(['invalid', '', 'BUILD-ORDER'])('ignores tab=%s', async (tab) => {
    renderPage(`?system=42&tab=${tab}`);
    expect(
      await screen.findByRole('tab', { name: 'System layout' }),
    ).toHaveAttribute('aria-selected', 'true');
    expect(goto).not.toHaveBeenCalled();
  });

  it('restores a linked task, writes URL state, and retains the mounted planner on history changes', async () => {
    const host = renderPage('?system=42&tab=build-order&from=inspect#body');
    const orderTab = await screen.findByRole('tab', { name: 'Build order' });
    const panel = screen.getByRole('tabpanel', { name: 'Build order' });
    const saved = get(colonyProjects).value;
    expect(orderTab).toHaveAttribute('aria-selected', 'true');
    await fireEvent.click(screen.getByRole('tab', { name: 'System layout' }));
    expect(mockedPage.url.searchParams.has('tab')).toBe(false);
    expect(mockedPage.url.searchParams.get('system')).toBe('42');
    expect(mockedPage.url.searchParams.get('from')).toBe('inspect');
    expect(mockedPage.url.hash).toBe('#body');
    expect(mockedPage.state).toEqual({ finderRequested: true });
    expect(goto).toHaveBeenLastCalledWith(mockedPage.url, {
      replaceState: true,
      keepFocus: true,
      noScroll: true,
      state: { finderRequested: true },
    });
    await fireEvent.keyDown(
      screen.getByRole('tab', { name: 'System layout' }),
      { key: 'ArrowRight' },
    );
    expect(mockedPage.url.searchParams.get('tab')).toBe('build-order');
    expect(screen.getByRole('tabpanel', { name: 'Build order' })).toBe(panel);
    const calls = goto.mock.calls.length;
    mockedPage.url = new URL('http://localhost/plan?system=42');
    await tick();
    expect(screen.getByRole('tab', { name: 'System layout' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(goto).toHaveBeenCalledTimes(calls);
    expect(get(colonyProjects).value).toEqual(saved);
    host.unmount();
    renderPage('?system=42&tab=build-order');
    expect(
      await screen.findByRole('tab', { name: 'Build order' }),
    ).toHaveAttribute('aria-selected', 'true');
  });
});
