import { Blob as NativeBlob } from 'node:buffer';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/svelte';
import { tick } from 'svelte';
import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  evaluateV3Plan,
  getV3PlanCatalogue,
  listV3PlanBodies,
  getPlanSystem,
  type PlanBodiesV1,
  type PlanEvaluationV1,
  type PlanFacilityV1,
} from '$lib/api';
import { queryClient } from '$lib/api/query';
import { v3System } from '$lib/api/v3-system-fixtures';
import { colonyProjects } from '$lib/persistence/stores';
import { PERSISTENCE_KEYS } from '$lib/persistence/storage';
import {
  emptyPlan,
  exportPlan,
  importPlan,
  plannerToRecord,
  recordToPlanner,
  MAX_IMPORT_BYTES,
  type PlannerSite,
} from './model';
import PlanWorkspaceTestHost from './PlanWorkspaceTestHost.svelte';
import ravenLayout from './__fixtures__/raven-workspace-layout.json';
import preP5bExport from './__fixtures__/pre-p5b-plan-export.json';

vi.mock('$lib/api/client', async (loadOriginal) => ({
  ...(await loadOriginal<typeof import('$lib/api/client')>()),
  evaluateV3Plan: vi.fn(),
  getV3PlanCatalogue: vi.fn(),
  listV3PlanBodies: vi.fn(),
  getPlanSystem: vi.fn(),
}));

const bodyData: PlanBodiesV1 = {
  system_id64: '42',
  generation_id: 'test',
  publication_sequence: '1',
  belts: [],
  bodies: [
    {
      body_id: '1',
      name: 'Test System A',
      direct_parent_body_id: null,
      body_type: 'star',
      is_main_star: true,
      is_landable: false,
      radius_km: 400000,
      surface_gravity_g: null,
      surface_temperature_k: null,
      atmosphere: null,
      terraforming_state: null,
      volcanism: null,
      distance_from_arrival_ls: 0,
      has_rings: false,
      geological_signals: 0,
      biological_signals: 0,
      signals_complete: null,
      lifecycle_state: 'ACTIVE',
    },
  ],
};
const port: PlanFacilityV1 = {
  id: 'primary-port',
  name: 'Primary Outpost',
  category: 'port',
  tier: 1,
  allowed_location: 'orbital',
  economy: null,
  is_port: true,
  escalating_port_tier: null,
  yellow_cp_cost: 0,
  green_cp_cost: 0,
  yellow_cp_generated: 0,
  green_cp_generated: 0,
  prerequisites: [],
  rule_ids: [],
};
const nonPort: PlanFacilityV1 = {
  ...port,
  id: 'installation',
  name: 'Installation',
  category: 'installation',
  is_port: false,
};
const catalogue = { rulebook_release: 'test', facilities: [port, nonPort] };
const system = v3System({ id64: '42', name: 'Test System' });

function evaluated(t2: number, blocked = 0): PlanEvaluationV1 {
  return {
    system_id64: '42',
    rulebook_release: 'test',
    now: { t2, t3: 0 },
    after_plan: { t2, t3: 0 },
    next_port_cost: { t2: 3, t3: 6 },
    economies: { now: [], after_plan: [], notes: [] },
    steps: [],
    plan_checks: [],
    summary: { OK: 0, BLOCKED: blocked, NEEDS_INFO: 0, COUNTED: 0, IGNORED: 0 },
    explanations: {},
  };
}

function blockedPrimary(): PlanEvaluationV1 {
  return {
    ...evaluated(17, 1),
    steps: [
      {
        site_id: 'imported-port',
        facility_id: port.id,
        status: 'planned',
        view: 'after_plan',
        result: 'BLOCKED',
        counted_port_number: 1,
        cost_t2: 3,
        cost_t3: 0,
        reward_t2: 0,
        reward_t3: 0,
        t2_before: 0,
        t3_before: 0,
        t2_after: 0,
        t3_after: 0,
        reasons: ['INSUFFICIENT_T2'],
        rule_ids: [],
        payers: [],
      },
    ],
  };
}

function mockWidth(wide: boolean) {
  const media = Object.assign(new EventTarget(), {
    matches: wide,
    media: '(min-width: 1400px)',
  });
  const matchMedia = vi.fn().mockReturnValue(media);
  vi.stubGlobal('matchMedia', matchMedia);
  return { media, matchMedia };
}

function buildOrderTab() {
  // The Review pop-out has a tab of the same name; this is the page's own tab.
  const [tab] = screen
    .getAllByRole('tab', { name: 'Build order' })
    .filter((candidate) => !candidate.closest('[role="dialog"]'));
  return tab;
}

function systemLayoutTab() {
  return screen.getByRole('tab', { name: 'System layout' });
}

async function openBuildOrder() {
  await fireEvent.click(buildOrderTab());
  await tick();
}

function importedSite(name = 'Imported port'): PlannerSite {
  return {
    schema: 'cpe_site_v1',
    site_id: 'imported-port',
    facility_id: port.id,
    body_ref: '1',
    slot_kind: 'orbital',
    status: 'planned',
    name,
  };
}

function seedPrimary(patch: Partial<PlannerSite> = {}) {
  const record = plannerToRecord({
    ...emptyPlan('42'),
    sites: [{ ...importedSite('Primary port'), ...patch }],
  });
  colonyProjects.set({
    version: 3,
    state: { projects: { [record.id]: record } },
  });
  return record;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

async function advance(ms: number) {
  await vi.advanceTimersByTimeAsync(ms);
  await tick();
}

async function editOrbitalSlots() {
  await fireEvent.click(
    screen.getByRole('button', { name: /Orbital slots on Test System A:/ }),
  );
  await fireEvent.click(
    screen.getByRole('button', { name: 'Increase orbital slots' }),
  );
  await fireEvent.click(screen.getByRole('button', { name: 'Done' }));
}

async function upload(text: string) {
  const file = new File([text], 'plan.json', { type: 'application/json' });
  Object.defineProperty(file, 'text', { value: async () => text });
  await fireEvent.change(screen.getByLabelText('Import plan file'), {
    target: { files: [file] },
  });
  await advance(0);
}

async function uploadRaven(text: string, size?: number) {
  const file = new File([text], 'raven-layout.json', {
    type: 'application/json',
  });
  Object.defineProperty(file, 'text', { value: async () => text });
  if (size !== undefined) Object.defineProperty(file, 'size', { value: size });
  await fireEvent.change(
    screen.getByLabelText('Import Raven Colonial layout file'),
    { target: { files: [file] } },
  );
  await advance(0);
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  localStorage.clear();
  colonyProjects.hydrate();
  queryClient.clear();
  vi.mocked(getV3PlanCatalogue).mockResolvedValue(catalogue);
  vi.mocked(listV3PlanBodies).mockResolvedValue(bodyData);
  vi.mocked(getPlanSystem).mockResolvedValue(system);
  vi.mocked(evaluateV3Plan).mockResolvedValue(evaluated(17));
  queryClient.setQueryData(['ed-finder', 'plan-catalogue'], catalogue);
  queryClient.setQueryData(['ed-finder', 'plan-bodies', '42'], bodyData);
  queryClient.setQueryData(['ed-finder', 'plan-system', '42'], system);
});

afterEach(() => {
  cleanup();
  queryClient.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('economy explanations', () => {
  it('opens the existing Why drawer from an economy and restores segment focus', async () => {
    const economyBodies = { ...bodyData, economy_facts_generation_id: 'facts' };
    vi.mocked(listV3PlanBodies).mockResolvedValue(economyBodies);
    queryClient.setQueryData(['ed-finder', 'plan-bodies', '42'], economyBodies);
    const result = evaluated(17);
    result.economies.after_plan = [
      {
        site_id: 'imported-port',
        body_ref: '1',
        role: 'main',
        supports: null,
        economies: [
          {
            name: 'Extraction',
            strength: 1.5,
            items: [
              {
                kind: 'own',
                base: 1.5,
                modifiers: [],
                floored: false,
                value: 1.5,
                source_site_ids: ['imported-port'],
                via_site_id: null,
                rule_ids: ['RB-0009'],
                count: null,
              },
            ],
          },
        ],
      },
    ];
    const rule = {
      id: 'RB-0009',
      title: 'Economy calculation',
      status: 'confirmed',
      statement: 'Returned economy statement.',
      quote: 'Returned economy quote.',
      page: 9,
    };
    result.explanations = {
      'RB-0009': rule,
      'RB-0003': { ...rule, id: 'RB-0003', title: 'Unrelated budget rule' },
    };
    vi.mocked(evaluateV3Plan).mockResolvedValue(result);
    seedPrimary();
    render(PlanWorkspaceTestHost);
    await advance(300);
    const section = screen.getByRole('region', { name: 'Port economies' });
    const segment = within(section).getAllByRole('button', {
      name: /Extraction/,
    })[0];
    segment.focus();
    await fireEvent.click(segment);
    await advance(0);
    const dialog = screen.getByRole('dialog', {
      name: 'Primary port · Extraction',
    });
    expect(dialog).toHaveFocus();
    expect(
      within(dialog).getByText('RB-0009 · Economy calculation'),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByText(/Unrelated budget rule/),
    ).not.toBeInTheDocument();
    await fireEvent.keyDown(window, { key: 'Escape' });
    await advance(0);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(segment).toHaveFocus();
  });
});

describe('Raven layout import', () => {
  beforeEach(() => {
    const ravenCatalogue = {
      ...catalogue,
      facilities: [
        ...catalogue.facilities,
        { ...port, id: 'orbital_outpost_commercial' },
        { ...nonPort, id: 'orbital_installation_comm_station' },
        { ...nonPort, id: 'orbital_installation_satellite' },
      ],
    };
    vi.mocked(getV3PlanCatalogue).mockResolvedValue(ravenCatalogue);
    queryClient.setQueryData(['ed-finder', 'plan-catalogue'], ravenCatalogue);
  });

  it('imports sites in order, leaves unknown locations Unplaced, and announces dismissible notes', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(PlanWorkspaceTestHost);
    const input = screen.getByLabelText('Import Raven Colonial layout file');
    const click = vi.spyOn(input, 'click');
    await fireEvent.click(screen.getByText('Import'));
    await fireEvent.click(
      screen.getByRole('button', { name: 'Import from Raven' }),
    );
    expect(click).toHaveBeenCalledOnce();
    expect(input).toHaveAttribute('accept', 'application/json,.json');
    await uploadRaven(JSON.stringify(ravenLayout));

    expect(confirm).toHaveBeenCalledExactlyOnceWith(
      "Replace this system's planner sites and slot counts with 3 sites from Raven Colonial? 1 will be Unplaced and 1 items need checking. The first site becomes the Primary Port.",
    );
    expect(
      screen.getByText('3 sites imported from Raven Colonial.'),
    ).toHaveAttribute('aria-live', 'polite');
    const record = Object.values(get(colonyProjects).value.state.projects)[0];
    expect(recordToPlanner(record)).toMatchObject({
      sites: [
        { name: 'Lumen Port', body_ref: '1', status: 'planned' },
        { name: 'Echo Relay', body_ref: '1', status: 'building' },
        { name: 'Far Lantern', body_ref: null, status: 'complete' },
      ],
      slots: { '1': { orbital_slots: 5, surface_slots: null } },
    });
    // Keep only planner data; raw import fields never reach browser storage.
    expect(JSON.stringify(record)).not.toContain('buildType');
    expect(JSON.stringify(record)).not.toContain('raven_body_name');
    await fireEvent.click(screen.getByText('Import notes (1)'));
    const notes = screen.getByText('Import notes (1)').closest('details')!;
    expect(within(notes).getAllByRole('listitem')).toHaveLength(1);
    expect(notes).toHaveTextContent('Test System Lost Moon');

    await openBuildOrder();
    const order = screen.getByRole('tabpanel', { name: 'Build order' });
    expect(
      within(order)
        .getAllByRole('heading', { level: 3 })
        .map((heading) => heading.textContent),
    ).toEqual(['Lumen Port', 'Echo Relay', 'Far Lantern']);
    const unplaced = within(order)
      .getByRole('heading', { name: 'Far Lantern' })
      .closest('[data-plan-site]')!;
    expect(unplaced).toHaveTextContent('Unplaced');
    await fireEvent.click(
      within(notes).getByRole('button', { name: 'Dismiss' }),
    );
    expect(screen.queryByText('Import notes (1)')).not.toBeInTheDocument();
    expect(buildOrderTab()).toHaveAccessibleName('Build order');
    expect(buildOrderTab()).toHaveTextContent('(3)');
  });

  it('refuses a different system before confirmation and preserves the current plan', async () => {
    const record = seedPrimary();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(PlanWorkspaceTestHost);
    await uploadRaven(
      JSON.stringify({ ...ravenLayout, id64: '43', name: 'Other Lantern' }),
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      "This Raven layout is for Other Lantern (43). Open that system's planner to import it.",
    );
    expect(confirm).not.toHaveBeenCalled();
    expect(get(colonyProjects).value.state.projects[record.id]).toEqual(record);
  });

  it('preserves sites and slot counts when replacement is cancelled', async () => {
    const record = seedPrimary();
    const before = localStorage.getItem(PERSISTENCE_KEYS.colonyProjects);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(PlanWorkspaceTestHost);
    await uploadRaven(JSON.stringify(ravenLayout));
    expect(confirm).toHaveBeenCalledOnce();
    expect(get(colonyProjects).value.state.projects[record.id]).toEqual(record);
    expect(localStorage.getItem(PERSISTENCE_KEYS.colonyProjects)).toBe(before);
    expect(
      screen.queryByText(/sites imported from Raven Colonial/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Import notes/)).not.toBeInTheDocument();
  });

  it('keeps a completed Primary Port locked through replacePlan (RB-0008)', async () => {
    const record = seedPrimary({ status: 'complete' });
    const before = localStorage.getItem(PERSISTENCE_KEYS.colonyProjects);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(PlanWorkspaceTestHost);
    await uploadRaven(JSON.stringify(ravenLayout));
    expect(confirm).toHaveBeenCalledOnce();
    expect(screen.getByRole('alert')).toHaveTextContent('RB-0008');
    expect(get(colonyProjects).value.state.projects[record.id]).toEqual(record);
    expect(localStorage.getItem(PERSISTENCE_KEYS.colonyProjects)).toBe(before);
    expect(
      screen.queryByText(/sites imported from Raven Colonial/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Import notes/)).not.toBeInTheDocument();
  });

  it('rejects a non-port first site before confirmation', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(PlanWorkspaceTestHost);
    await uploadRaven(
      JSON.stringify({ ...ravenLayout, sites: ravenLayout.sites.slice(1) }),
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      "This plan's first site is not a port, so it cannot be the Primary Port.",
    );
    expect(confirm).not.toHaveBeenCalled();
    expect(Object.keys(get(colonyProjects).value.state.projects)).toEqual([]);
  });

  it.each([
    '{"v":6,"id64":9007199254740993,"name":"Far Lantern","sites":[]}',
    '{"v":6,"id64":"42","name":"Test System","sites":[{"id":9007199254740993,"name":"Lumen Port","buildType":"plutus","status":"plan"}]}',
  ])(
    'rejects unsafe numeric ids from raw file text without rounding or importing: %s',
    async (text) => {
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
      render(PlanWorkspaceTestHost);
      await uploadRaven(text);
      expect(screen.getByRole('alert')).toHaveTextContent(
        'not a safe integer; its exact digits cannot be imported',
      );
      expect(confirm).not.toHaveBeenCalled();
      expect(Object.keys(get(colonyProjects).value.state.projects)).toEqual([]);
    },
  );

  it('preserves a quoted large system id exactly in the refusal', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(PlanWorkspaceTestHost);
    await uploadRaven(
      '{"v":6,"id64":"9007199254740993","name":"Far Lantern","sites":[]}',
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      "This Raven layout is for Far Lantern (9007199254740993). Open that system's planner to import it.",
    );
    expect(confirm).not.toHaveBeenCalled();
  });

  it.each([
    ['{', 'This file is not a Raven Colonial layout (it is not valid JSON).'],
    [
      JSON.stringify({ ...ravenLayout, v: 5 }),
      'Only Raven layout version 6 is supported.',
    ],
  ])(
    'reports invalid file data before confirmation: %s',
    async (text, message) => {
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
      render(PlanWorkspaceTestHost);
      await uploadRaven(text);
      expect(screen.getByRole('alert')).toHaveTextContent(message);
      expect(confirm).not.toHaveBeenCalled();
    },
  );

  it('rejects files larger than the import limit', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(PlanWorkspaceTestHost);
    await uploadRaven(JSON.stringify(ravenLayout), MAX_IMPORT_BYTES + 1);
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Plan files must be no larger than 2 MB.',
    );
    expect(confirm).not.toHaveBeenCalled();
  });

  it('waits for the system bodies before mapping and confirming', async () => {
    queryClient.removeQueries({ queryKey: ['ed-finder', 'plan-bodies', '42'] });
    vi.mocked(listV3PlanBodies).mockReturnValue(
      deferred<PlanBodiesV1>().promise,
    );
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(PlanWorkspaceTestHost);
    await uploadRaven(JSON.stringify(ravenLayout));
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Wait for the system bodies to load before importing a plan.',
    );
    expect(confirm).not.toHaveBeenCalled();
  });
});

describe('Plan task tabs', () => {
  it.each(['bodies', 'catalogue'] as const)(
    'waits for %s to load before exposing task tabs and their panels',
    async (resource) => {
      const bodyRequest = deferred<PlanBodiesV1>();
      const catalogueRequest = deferred<typeof catalogue>();
      if (resource === 'bodies') {
        queryClient.removeQueries({
          queryKey: ['ed-finder', 'plan-bodies', '42'],
        });
        vi.mocked(listV3PlanBodies).mockReturnValue(bodyRequest.promise);
      } else {
        queryClient.removeQueries({
          queryKey: ['ed-finder', 'plan-catalogue'],
        });
        vi.mocked(getV3PlanCatalogue).mockReturnValue(catalogueRequest.promise);
      }
      render(PlanWorkspaceTestHost);
      await advance(0);
      expect(screen.queryByRole('tablist', { name: 'Plan tasks' })).toBeNull();
      expect(screen.getByRole('status')).toHaveTextContent(
        'Loading planner catalogue and system bodies',
      );
      expect(screen.queryByRole('tabpanel')).toBeNull();

      bodyRequest.resolve(bodyData);
      catalogueRequest.resolve(catalogue);
      await advance(0);
      expect(buildOrderTab()).toBeEnabled();
      await openBuildOrder();
      expect(buildOrderTab()).toHaveAttribute('aria-selected', 'true');
      expect(
        screen.getByRole('tabpanel', { name: 'Build order' }),
      ).toBeVisible();
    },
  );

  it.each(['bodies', 'catalogue'] as const)(
    'keeps unavailable task panels out of navigation when %s fail',
    async (resource) => {
      if (resource === 'bodies') {
        queryClient.removeQueries({
          queryKey: ['ed-finder', 'plan-bodies', '42'],
        });
        vi.mocked(listV3PlanBodies).mockRejectedValue(new Error('Unavailable'));
      } else {
        queryClient.removeQueries({
          queryKey: ['ed-finder', 'plan-catalogue'],
        });
        vi.mocked(getV3PlanCatalogue).mockRejectedValue(
          new Error('Unavailable'),
        );
      }
      render(PlanWorkspaceTestHost);
      await advance(0);
      expect(screen.getByRole('alert')).toHaveTextContent(
        'The catalogue or system bodies could not be loaded.',
      );
      expect(screen.queryByRole('tablist', { name: 'Plan tasks' })).toBeNull();
      expect(screen.queryByRole('tabpanel')).toBeNull();
    },
  );

  it('labels the tablist and links one selected tab to each retained, inert hidden panel', async () => {
    seedPrimary();
    vi.mocked(evaluateV3Plan).mockResolvedValue(blockedPrimary());
    render(PlanWorkspaceTestHost);
    await advance(250);
    const list = screen.getByRole('tablist', { name: 'Plan tasks' });
    expect(within(list).getAllByRole('tab')).toHaveLength(2);
    const layout = systemLayoutTab();
    const order = buildOrderTab();
    const layoutPanel = document.getElementById('plan-system-layout')!;
    const orderPanel = document.getElementById('plan-build-order')!;
    for (const [tab, panel] of [
      [layout, layoutPanel],
      [order, orderPanel],
    ]) {
      expect(tab).toHaveAttribute('aria-controls', panel.id);
      expect(panel).toHaveAttribute('role', 'tabpanel');
      expect(panel).toHaveAttribute('aria-labelledby', tab.id);
    }
    expect(layout).toHaveAttribute('aria-selected', 'true');
    expect(layout).toHaveAttribute('tabindex', '0');
    expect(order).toHaveAttribute('aria-selected', 'false');
    expect(order).toHaveAttribute('tabindex', '-1');
    expect(order).toHaveAccessibleName('Build order');
    expect(order).toHaveTextContent('1 blocked');
    expect(orderPanel).toHaveAttribute('hidden');
    expect(orderPanel).toHaveProperty('inert', true);
    expect(layoutPanel).not.toHaveAttribute('hidden');
    expect(layoutPanel).toHaveProperty('inert', false);

    await openBuildOrder();
    expect(order).toHaveFocus();
    expect(order).toHaveAttribute('aria-selected', 'true');
    expect(order).toHaveAttribute('tabindex', '0');
    expect(layout).toHaveAttribute('aria-selected', 'false');
    expect(layout).toHaveAttribute('tabindex', '-1');
    expect(layoutPanel).toHaveAttribute('hidden');
    expect(layoutPanel).toHaveProperty('inert', true);
    expect(orderPanel).not.toHaveAttribute('hidden');
    expect(orderPanel).toHaveProperty('inert', false);
    expect(
      screen.getByRole('complementary', { name: 'Summary' }),
    ).toBeVisible();
  });

  it('uses Arrow keys, Home and End for automatic activation and roving focus', async () => {
    const ontabchange = vi.fn();
    render(PlanWorkspaceTestHost, { ontabchange });
    const layout = systemLayoutTab();
    const order = buildOrderTab();
    layout.focus();
    for (const [key, tab, value] of [
      ['ArrowRight', order, 'build-order'],
      ['ArrowRight', layout, 'system-layout'],
      ['ArrowLeft', order, 'build-order'],
      ['Home', layout, 'system-layout'],
      ['End', order, 'build-order'],
    ] as const) {
      await fireEvent.keyDown(document.activeElement!, { key });
      expect(tab).toHaveFocus();
      expect(tab).toHaveAttribute('aria-selected', 'true');
      expect(tab).toHaveAttribute('tabindex', '0');
      expect(ontabchange).toHaveBeenLastCalledWith(value);
      expect(
        screen.getAllByRole('tab').filter((item) => item.tabIndex === 0),
      ).toEqual([tab]);
    }
    const tab = new KeyboardEvent('keydown', {
      key: 'Tab',
      bubbles: true,
      cancelable: true,
    });
    order.dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(false);
    expect(order).toHaveFocus();
  });

  it('restores the supplied task on remount without consulting the retired toggle preference', async () => {
    localStorage.setItem('ed-finder.plan.buildOrderOpen', 'true');
    const first = render(PlanWorkspaceTestHost);
    expect(systemLayoutTab()).toHaveAttribute('aria-selected', 'true');
    await openBuildOrder();
    first.unmount();
    const second = render(PlanWorkspaceTestHost, { activeTab: 'build-order' });
    expect(buildOrderTab()).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel', { name: 'Build order' })).toBeVisible();
    second.unmount();
    render(PlanWorkspaceTestHost, { activeTab: 'system-layout' });
    expect(systemLayoutTab()).toHaveAttribute('aria-selected', 'true');
  });

  it.each([false, true])(
    'shows only the selected main task at wide layout=%s',
    async (wide) => {
      mockWidth(wide);
      render(PlanWorkspaceTestHost);
      const layout = screen.getByRole('tabpanel', { name: 'System layout' });
      await openBuildOrder();
      expect(layout).toHaveAttribute('hidden');
      expect(layout).toHaveProperty('inert', true);
      expect(
        screen.queryByRole('tabpanel', { name: 'System layout' }),
      ).toBeNull();
      expect(
        screen.getByRole('tabpanel', { name: 'Build order' }),
      ).toBeVisible();
      expect(
        screen.getByRole('complementary', { name: 'Summary' }),
      ).toBeVisible();
    },
  );

  it('retains an open slot editor and its value while suspending its keyboard trap on the hidden tab', async () => {
    render(PlanWorkspaceTestHost);
    await fireEvent.click(
      screen.getByRole('button', {
        name: 'Orbital slots on Test System A: 0 used, unknown total',
      }),
    );
    const editor = screen.getByRole('dialog', {
      name: 'Orbital slots on Test System A',
    });
    await fireEvent.click(
      within(editor).getByRole('button', { name: 'Increase orbital slots' }),
    );
    const stored = localStorage.getItem(PERSISTENCE_KEYS.colonyProjects);
    editor.focus();
    await fireEvent.pointerDown(buildOrderTab());
    await openBuildOrder();
    expect(editor).toBeInTheDocument();
    expect(editor).not.toBeVisible();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(buildOrderTab()).toHaveFocus();
    const tab = new KeyboardEvent('keydown', {
      key: 'Tab',
      bubbles: true,
      cancelable: true,
    });
    buildOrderTab().dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(false);
    expect(buildOrderTab()).toHaveFocus();
    expect(localStorage.getItem(PERSISTENCE_KEYS.colonyProjects)).toBe(stored);

    await fireEvent.click(systemLayoutTab());
    expect(
      screen.getByRole('dialog', { name: 'Orbital slots on Test System A' }),
    ).toBe(editor);
    expect(
      within(editor).getByLabelText('Orbital slot total'),
    ).toHaveTextContent('1');
    expect(
      screen.getByRole('button', {
        name: 'Orbital slots on Test System A: 0 used, 1 total',
      }),
    ).toHaveAttribute('aria-expanded', 'true');
    await fireEvent.click(within(editor).getByRole('button', { name: 'Done' }));
  });

  it('retains selected body/site, build filter, expanded editor and scroll without mutating or refetching', async () => {
    const record = seedPrimary();
    vi.mocked(evaluateV3Plan).mockResolvedValue(blockedPrimary());
    render(PlanWorkspaceTestHost);
    await advance(300);
    const layout = screen.getByRole('tabpanel', { name: 'System layout' });
    const body = layout.querySelector<HTMLElement>('[data-plan-body="1"]')!;
    await fireEvent.click(body.querySelector('.body-name')!);
    const chip = within(body).getByRole('button', { name: /^Step 1,/ });
    expect(chip).toHaveAccessibleName(/Primary Port/);
    expect(within(chip).getByText('★')).toHaveAttribute('aria-hidden', 'true');
    expect(chip.querySelector('.tile-number')).toHaveTextContent(/^★\s*1$/);
    await fireEvent.click(chip);
    await fireEvent.keyDown(window, { key: 'Escape' });
    layout.scrollTop = 91;
    await openBuildOrder();
    const order = screen.getByRole('tabpanel', { name: 'Build order' });
    const step = order.querySelector<HTMLElement>(
      '[data-plan-site="imported-port"]',
    )!;
    await fireEvent.click(
      within(order).getByRole('button', { name: 'Blocked' }),
    );
    const editor = within(order)
      .getByLabelText('Edit Primary port')
      .closest('details')!;
    await fireEvent.click(within(order).getByLabelText('Edit Primary port'));
    expect(editor).toHaveAttribute('open');
    order.scrollTop = 173;
    const stored = localStorage.getItem(PERSISTENCE_KEYS.colonyProjects);
    const before = get(colonyProjects).value.state.projects[record.id];
    const reads = [
      getV3PlanCatalogue,
      listV3PlanBodies,
      getPlanSystem,
      evaluateV3Plan,
    ].map((fn) => vi.mocked(fn).mock.calls.length);

    await fireEvent.click(systemLayoutTab());
    await openBuildOrder();
    await advance(1000);
    expect(document.getElementById('plan-system-layout')).toBe(layout);
    expect(document.getElementById('plan-build-order')).toBe(order);
    expect(document.getElementById('plan-step-imported-port')).toBe(step);
    expect(body).toHaveClass('selected');
    expect(step).toHaveClass('selected');
    expect(
      within(order).getByRole('button', { name: 'Blocked' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(editor).toHaveAttribute('open');
    expect(layout.scrollTop).toBe(91);
    expect(order.scrollTop).toBe(173);
    expect(get(colonyProjects).value.state.projects[record.id]).toEqual(before);
    expect(localStorage.getItem(PERSISTENCE_KEYS.colonyProjects)).toBe(stored);
    expect(
      [getV3PlanCatalogue, listV3PlanBodies, getPlanSystem, evaluateV3Plan].map(
        (fn) => vi.mocked(fn).mock.calls.length,
      ),
    ).toEqual(reads);
  });

  it('moves focus out of a hidden panel when the active tab changes externally', async () => {
    seedPrimary();
    const view = render(PlanWorkspaceTestHost, { activeTab: 'build-order' });
    const order = screen.getByRole('tabpanel', { name: 'Build order' });
    within(order).getByLabelText('Edit Primary port').focus();
    await view.rerender({ activeTab: 'system-layout' });
    await tick();
    expect(order).toHaveAttribute('hidden');
    expect(order).toHaveProperty('inert', true);
    expect(systemLayoutTab()).toHaveFocus();
    expect(document.activeElement?.closest('[hidden], [inert]')).toBeNull();
  });

  it('keeps the active task when Escape closes Why and returns to its visible opener', async () => {
    seedPrimary();
    vi.mocked(evaluateV3Plan).mockResolvedValue(blockedPrimary());
    render(PlanWorkspaceTestHost);
    await advance(250);
    await openBuildOrder();
    const order = screen.getByRole('tabpanel', { name: 'Build order' });
    const why = within(order).getByRole('button', {
      name: 'Why? Primary port',
    });
    why.focus();
    await fireEvent.click(why);
    expect(screen.getByRole('dialog')).toHaveFocus();
    await fireEvent.keyDown(why, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(order).not.toHaveAttribute('hidden');
    expect(buildOrderTab()).toHaveAttribute('aria-selected', 'true');
    expect(why).toHaveFocus();
    await fireEvent.keyDown(why, { key: 'Escape' });
    expect(order).not.toHaveAttribute('hidden');
    expect(why).toHaveFocus();
  });

  it.each(['Close', 'Escape'] as const)(
    'returns focus to the active task when %s closes Why after its opener becomes hidden',
    async (action) => {
      seedPrimary();
      vi.mocked(evaluateV3Plan).mockResolvedValue(blockedPrimary());
      const view = render(PlanWorkspaceTestHost, {
        activeTab: 'system-layout',
      });
      await advance(250);
      const chip = screen.getByRole('button', { name: /^Step 1,/ });
      chip.focus();
      await fireEvent.click(chip);
      const drawer = screen.getByRole('dialog', {
        name: 'Step 1: Primary port',
      });
      expect(drawer).toHaveFocus();
      await view.rerender({ activeTab: 'build-order' });
      expect(drawer).toHaveFocus();
      if (action === 'Close') {
        await fireEvent.click(
          within(drawer).getByRole('button', { name: 'Close why panel' }),
        );
      } else {
        await fireEvent.keyDown(drawer, { key: 'Escape' });
      }
      await advance(0);
      expect(screen.queryByRole('dialog')).toBeNull();
      expect(buildOrderTab()).toHaveAttribute('aria-selected', 'true');
      expect(buildOrderTab()).toHaveFocus();
      expect(chip).not.toHaveFocus();
      expect(document.activeElement?.closest('[hidden], [inert]')).toBeNull();
    },
  );

  it('opens Build order from a Summary step link and focuses the selected step', async () => {
    seedPrimary();
    vi.mocked(evaluateV3Plan).mockResolvedValue(blockedPrimary());
    render(PlanWorkspaceTestHost);
    await advance(250);
    const step = document.getElementById('plan-step-imported-port')!;
    const scrollIntoView = vi.fn();
    Object.defineProperty(step, 'scrollIntoView', { value: scrollIntoView });
    await fireEvent.click(
      screen.getByRole('button', { name: 'First blocked step: Primary port' }),
    );
    expect(
      screen.getByRole('tabpanel', { name: 'Build order' }),
    ).toContainElement(step);
    expect(buildOrderTab()).toHaveAttribute('aria-selected', 'true');
    expect(step).toHaveFocus();
    expect(step).toHaveClass('selected');
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
  });

  it('opens System layout and focuses its selected body from Summary', async () => {
    seedPrimary();
    render(PlanWorkspaceTestHost);
    await openBuildOrder();
    await fireEvent.click(
      screen.getByRole('button', {
        name: '1 body has builds with unknown slot counts',
      }),
    );
    expect(systemLayoutTab()).toHaveAttribute('aria-selected', 'true');
    const body = screen
      .getByRole('tabpanel', { name: 'System layout' })
      .querySelector('[data-plan-body="1"]')!;
    expect(body).toHaveClass('selected');
    expect(body.querySelector('.body-name')).toHaveFocus();
  });
});

describe('Review and DRAFT export', () => {
  function seedReviewPlan() {
    const reviewBodies: PlanBodiesV1 = {
      ...bodyData,
      bodies: [
        ...bodyData.bodies,
        {
          ...bodyData.bodies[0],
          body_id: '2',
          name: 'Test System A 1',
          direct_parent_body_id: '1',
          body_type: 'planet',
          is_main_star: false,
          is_landable: true,
          radius_km: 2000,
          predicted_surface_slots: 3,
          prediction_status: 'predicted',
          prediction_version: 'surface_slots_v1',
          missing_inputs: [],
        },
        {
          ...bodyData.bodies[0],
          body_id: '3',
          name: 'Test System A 2',
          direct_parent_body_id: '1',
          body_type: 'planet',
          is_main_star: false,
          is_landable: null,
          radius_km: null,
          prediction_status: 'unknown',
          missing_inputs: ['is_landable', 'radius_km'],
        },
      ],
    };
    vi.mocked(listV3PlanBodies).mockResolvedValue(reviewBodies);
    queryClient.setQueryData(['ed-finder', 'plan-bodies', '42'], reviewBodies);
    const result = blockedPrimary();
    result.now = { t2: 17, t3: 9 };
    result.after_plan = { t2: -2, t3: 6 };
    vi.mocked(evaluateV3Plan).mockResolvedValue(result);
    seedPrimary();
  }

  async function openReview() {
    const opener = screen.getByRole('button', { name: 'Review & export' });
    opener.focus();
    await fireEvent.click(opener);
    await advance(0);
    return screen.getByRole('dialog', { name: 'Review & export' });
  }

  it('keeps both imports inside one Import menu that starts closed and closes after a choice, Escape or an outside click', async () => {
    seedReviewPlan();
    render(PlanWorkspaceTestHost);
    await advance(300);
    const menu = document.querySelector<HTMLDetailsElement>('.plan-import');
    const summary = menu?.querySelector('summary') as HTMLElement;
    expect(menu).not.toBeNull();
    expect(menu?.open).toBe(false);
    expect(summary).toHaveTextContent('Import');
    expect(
      [...(menu?.querySelectorAll('button') ?? [])].map((b) => b.textContent),
    ).toEqual(['Import plan', 'Import from Raven']);

    const open = async () => {
      await fireEvent.click(summary);
      await advance(0);
      expect(menu?.open).toBe(true);
    };
    const planInput = screen.getByLabelText('Import plan file');
    const pick = vi.spyOn(planInput, 'click').mockImplementation(() => {});
    await open();
    await fireEvent.click(screen.getByRole('button', { name: 'Import plan' }));
    await advance(0);
    expect(pick).toHaveBeenCalledOnce();
    expect(menu?.open).toBe(false);

    await open();
    summary.focus();
    await fireEvent.keyDown(summary, { key: 'Escape' });
    await advance(0);
    expect(menu?.open).toBe(false);
    expect(summary).toHaveFocus();

    await open();
    await fireEvent.click(document.body);
    await advance(0);
    expect(menu?.open).toBe(false);
  });

  it('marks Review & export as the one primary header action', async () => {
    seedReviewPlan();
    render(PlanWorkspaceTestHost);
    await advance(300);
    const primary = document.querySelectorAll(
      '.plan-header-actions .plan-review-button',
    );
    expect(primary).toHaveLength(1);
    expect(primary[0]).toHaveAccessibleName('Review & export');
  });

  it.each(['Close', 'Escape'] as const)(
    'opens Review from the header without another request, and %s returns focus to its opener',
    async (action) => {
      seedReviewPlan();
      render(PlanWorkspaceTestHost);
      await advance(300);
      const requests = [
        getV3PlanCatalogue,
        listV3PlanBodies,
        getPlanSystem,
        evaluateV3Plan,
      ].map((fn) => vi.mocked(fn).mock.calls.length);
      const before = localStorage.getItem(PERSISTENCE_KEYS.colonyProjects);
      const review = await openReview();
      expect(review).toHaveAttribute('aria-modal', 'false');
      expect(review).toHaveTextContent('Test System');
      expect(review).toHaveTextContent('Unknown orbital slots: Test System A');
      expect(review).toHaveTextContent(
        'Prediction-only surface slots: Test System A 1',
      );
      expect(review).toHaveTextContent('Missing body data: Test System A 2');
      expect(review).toHaveTextContent('Blocked build: Primary port');
      expect(review).not.toHaveTextContent(/eligible|approved|ready to build/i);
      if (action === 'Close')
        await fireEvent.click(
          within(review).getByRole('button', { name: /close/i }),
        );
      else await fireEvent.keyDown(review, { key: 'Escape' });
      await advance(0);
      expect(
        screen.queryByRole('dialog', { name: 'Review & export' }),
      ).toBeNull();
      expect(
        screen.getByRole('button', { name: 'Review & export' }),
      ).toHaveFocus();
      expect(localStorage.getItem(PERSISTENCE_KEYS.colonyProjects)).toBe(
        before,
      );
      expect(
        [
          getV3PlanCatalogue,
          listV3PlanBodies,
          getPlanSystem,
          evaluateV3Plan,
        ].map((fn) => vi.mocked(fn).mock.calls.length),
      ).toEqual(requests);
    },
  );

  it('links each unresolved item to its exact visible task control while keeping Review open', async () => {
    seedReviewPlan();
    const ontabchange = vi.fn();
    render(PlanWorkspaceTestHost, { ontabchange });
    await advance(300);
    const review = await openReview();
    const cases = [
      {
        link: '[data-review-kind="unknown-slot"][data-body-ref="1"][data-slot-kind="orbital"]',
        target: '[data-plan-body="1"] [data-slot-kind="orbital"]',
        tab: 'system-layout',
      },
      {
        link: '[data-review-kind="prediction"][data-body-ref="2"]',
        target: '[data-plan-body="2"] [data-slot-kind="surface"]',
        tab: 'system-layout',
      },
      {
        link: '[data-review-kind="missing-data"][data-body-ref="3"]',
        target: '[data-plan-body="3"] .body-name',
        tab: 'system-layout',
      },
      {
        link: '[data-review-kind="build"][data-site-id="imported-port"]',
        target: '#plan-step-imported-port',
        tab: 'build-order',
      },
    ];
    for (const { link, target, tab } of cases) {
      // Start on the other task so every link must activate its destination.
      await fireEvent.click(
        tab === 'system-layout' ? buildOrderTab() : systemLayoutTab(),
      );
      const control = document.querySelector<HTMLElement>(target)!;
      const scrollIntoView = vi.fn();
      Object.defineProperty(control, 'scrollIntoView', {
        value: scrollIntoView,
        configurable: true,
      });
      await fireEvent.click(review.querySelector<HTMLElement>(link)!);
      await advance(0);
      expect(ontabchange).toHaveBeenLastCalledWith(tab);
      expect(control).toBeVisible();
      expect(control).toHaveFocus();
      expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
      expect(review).toBeVisible();
      expect(document.activeElement?.closest('[hidden], [inert]')).toBeNull();
    }

    // The existing Summary shortcuts still use the same destinations.
    await fireEvent.click(
      screen.getByRole('button', { name: 'First blocked step: Primary port' }),
    );
    expect(document.getElementById('plan-step-imported-port')).toHaveFocus();
    await fireEvent.click(
      screen.getByRole('button', {
        name: '1 body has builds with unknown slot counts',
      }),
    );
    expect(
      document.querySelector('[data-plan-body="1"] .body-name'),
    ).toHaveFocus();
    expect(review).toBeVisible();
  });

  it('keeps unresolved links disabled until the task editors load, then enables their focus destinations', async () => {
    seedReviewPlan();
    const catalogueRequest = deferred<typeof catalogue>();
    queryClient.removeQueries({ queryKey: ['ed-finder', 'plan-catalogue'] });
    vi.mocked(getV3PlanCatalogue).mockReturnValue(catalogueRequest.promise);
    render(PlanWorkspaceTestHost);
    await advance(300);
    const review = await openReview();
    const unresolved = within(review).getByRole('list', {
      name: 'Unresolved information',
    });
    const links = within(unresolved).getAllByRole('button');
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) expect(link).toBeDisabled();
    expect(review).toHaveTextContent(
      'Body and build editors are unavailable until planner data loads successfully.',
    );
    expect(
      within(review).getByRole('button', { name: 'Export DRAFT plan' }),
    ).toBeEnabled();
    // The page's own task panels are not mounted yet (the Review pop-out has
    // its own tab panels).
    expect(
      screen
        .queryAllByRole('tabpanel')
        .filter((panel) => !review.contains(panel)),
    ).toEqual([]);

    catalogueRequest.resolve(catalogue);
    await advance(0);
    for (const link of links) expect(link).toBeEnabled();
    const orbital = within(unresolved).getByRole('button', {
      name: 'Unknown orbital slots: Test System A',
    });
    await fireEvent.click(orbital);
    await advance(0);
    expect(systemLayoutTab()).toHaveAttribute('aria-selected', 'true');
    expect(
      document.querySelector('[data-plan-body="1"] [data-slot-kind="orbital"]'),
    ).toHaveFocus();
    expect(review).toBeVisible();
  });

  it('updates unresolved slots in the open Review from the current browser plan', async () => {
    seedReviewPlan();
    render(PlanWorkspaceTestHost);
    await advance(300);
    const review = await openReview();
    await fireEvent.click(
      review.querySelector<HTMLElement>(
        '[data-review-kind="unknown-slot"][data-body-ref="1"]',
      )!,
    );
    const slot = document.activeElement!;
    await fireEvent.click(slot);
    const editor = screen.getByRole('dialog', {
      name: 'Orbital slots on Test System A',
    });
    await fireEvent.click(
      within(editor).getByRole('button', { name: 'Increase orbital slots' }),
    );
    expect(
      review.querySelector(
        '[data-review-kind="unknown-slot"][data-body-ref="1"]',
      ),
    ).toBeNull();
    await fireEvent.keyDown(editor, { key: 'Escape' });
    await advance(0);
    expect(editor).not.toBeInTheDocument();
    expect(slot).toHaveFocus();
    expect(review).toBeVisible();
    await fireEvent.keyDown(slot, { key: 'Escape' });
    await advance(0);
    expect(review).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Review & export' }),
    ).toHaveFocus();
  });

  it('links a reported invalid build to Build order and clears the issue after a successful reevaluation', async () => {
    seedReviewPlan();
    vi.mocked(evaluateV3Plan).mockRejectedValueOnce(
      new ApiError(400, '/api/v1/plan/evaluate', {
        error: 'plan_invalid',
        issues: [
          {
            code: 'INVALID_LOCATION',
            site_id: 'imported-port',
            message: 'This build location needs checking.',
          },
        ],
      }),
    );
    render(PlanWorkspaceTestHost);
    await advance(300);
    const review = await openReview();
    const invalid = within(review).getByRole('button', {
      name: /Invalid build: Primary port/,
    });
    expect(invalid.closest('li')).toHaveTextContent(
      'This build location needs checking.',
    );
    const step = document.getElementById('plan-step-imported-port')!;
    const scrollIntoView = vi.fn();
    Object.defineProperty(step, 'scrollIntoView', { value: scrollIntoView });
    await fireEvent.click(invalid);
    await advance(0);
    expect(buildOrderTab()).toHaveAttribute('aria-selected', 'true');
    expect(step).toBeVisible();
    expect(step).toHaveFocus();
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
    expect(review).toBeVisible();

    vi.mocked(evaluateV3Plan).mockResolvedValue(evaluated(23));
    await fireEvent.click(systemLayoutTab());
    await editOrbitalSlots();
    await advance(300);
    expect(
      within(review).queryByRole('button', { name: /Invalid build:/ }),
    ).toBeNull();
    expect(
      within(review).queryByText('This build location needs checking.'),
    ).toBeNull();
    expect(review).toBeVisible();
  });

  it('closes only the topmost Why tool before Review on Escape', async () => {
    seedReviewPlan();
    render(PlanWorkspaceTestHost);
    await advance(300);
    const review = await openReview();
    await fireEvent.click(
      review.querySelector<HTMLElement>('[data-review-kind="build"]')!,
    );
    const why = screen.getByRole('button', { name: 'Why? Primary port' });
    why.focus();
    await fireEvent.click(why);
    const drawer = screen.getByRole('dialog', {
      name: 'Step 1: Primary port',
    });
    expect(drawer).toHaveFocus();
    await fireEvent.keyDown(drawer, { key: 'Escape' });
    await advance(0);
    expect(drawer).not.toBeInTheDocument();
    expect(review).toBeVisible();
    expect(why).toHaveFocus();
    await fireEvent.keyDown(why, { key: 'Escape' });
    await advance(0);
    expect(review).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Review & export' }),
    ).toHaveFocus();
  });

  it('imports a pre-P5b export without changing its model', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(PlanWorkspaceTestHost);
    await upload(JSON.stringify(preP5bExport));
    const records = Object.values(get(colonyProjects).value.state.projects);
    expect(records).toHaveLength(1);
    expect(recordToPlanner(records[0])).toEqual({
      system_id64: preP5bExport.system_id64,
      sites: preP5bExport.sites,
      slots: preP5bExport.slots,
    });
  });

  it('closes facility preview, picker and Review in order on consecutive Escape presses', async () => {
    seedReviewPlan();
    render(PlanWorkspaceTestHost);
    await advance(300);
    const review = await openReview();
    const add = screen.getByRole('button', {
      name: 'Add a build on Test System A',
    });
    add.focus();
    await fireEvent.click(add);
    const facility = screen.getByRole('button', { name: 'Installation' });
    await fireEvent.click(facility);
    const preview = screen.getByRole('heading', {
      name: 'Preview: Installation',
    });
    expect(preview).toHaveFocus();
    await fireEvent.keyDown(preview, { key: 'Escape' });
    await advance(0);
    expect(preview).not.toBeInTheDocument();
    expect(facility).toHaveFocus();
    expect(review).toBeVisible();
    await fireEvent.keyDown(facility, { key: 'Escape' });
    await advance(0);
    expect(screen.queryByRole('button', { name: 'Close picker' })).toBeNull();
    expect(add).toHaveFocus();
    expect(review).toBeVisible();
    await fireEvent.keyDown(add, { key: 'Escape' });
    await advance(0);
    expect(review).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Review & export' }),
    ).toHaveFocus();
  });

  it.each(['header', 'Review'] as const)(
    'exports DRAFT from %s with the exact filename and imports the unchanged V1 model',
    async (source) => {
      const original = importPlan(JSON.stringify(preP5bExport), '42');
      const record = plannerToRecord(original);
      colonyProjects.set({
        version: 3,
        state: { projects: { [record.id]: record } },
      });
      // Native Blob exposes text() without FileReader's task timers, so the
      // evaluator's debounce stays under the test's fake-clock control.
      vi.stubGlobal('Blob', NativeBlob);
      const createObjectURL = vi.fn<(blob: Blob | MediaSource) => string>(
        () => 'blob:plan-draft',
      );
      const revokeObjectURL = vi.fn();
      vi.stubGlobal(
        'URL',
        class extends URL {
          static createObjectURL = createObjectURL;
          static revokeObjectURL = revokeObjectURL;
        },
      );
      const downloads: string[] = [];
      vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(
        function (this: HTMLAnchorElement) {
          downloads.push(this.download);
          expect(this.href).toBe('blob:plan-draft');
        },
      );
      vi.spyOn(window, 'confirm').mockReturnValue(true);
      render(PlanWorkspaceTestHost);
      const scope = source === 'Review' ? within(await openReview()) : screen;
      const action = scope.getByRole('button', { name: 'Export DRAFT plan' });
      expect(action).toHaveTextContent('Export DRAFT plan');
      expect(action).toHaveAttribute('aria-label', 'Export DRAFT plan');
      await fireEvent.click(action);
      expect(downloads).toEqual(['colony-plan-DRAFT-42.json']);
      expect(createObjectURL).toHaveBeenCalledOnce();
      const blob = createObjectURL.mock.calls[0][0];
      if (!(blob instanceof NativeBlob))
        throw new Error('Expected a downloaded JSON Blob');
      expect(blob.type).toBe('application/json');
      const exported = await blob.text();
      expect(JSON.parse(exported)).toEqual(preP5bExport);
      expect(importPlan(exported, '42')).toEqual(original);
      await fireEvent.click(
        screen.getByRole('button', { name: 'Abandon plan' }),
      );
      await upload(exported);
      const restored = Object.values(get(colonyProjects).value.state.projects);
      expect(restored).toHaveLength(1);
      expect(recordToPlanner(restored[0])).toEqual(original);
      await advance(1000);
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:plan-draft');
    },
  );
});

describe('planner workspace evaluation and browser state', () => {
  it('keeps the browser plan unchanged through preview and dismissal, then adds only on explicit Add to plan', async () => {
    render(PlanWorkspaceTestHost);
    const add = screen.getByRole('button', {
      name: 'Add a build on Test System A',
    });
    const before = localStorage.getItem(PERSISTENCE_KEYS.colonyProjects);
    await fireEvent.click(add);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Primary Outpost' }),
    );
    expect(
      screen.getByRole('heading', { name: 'Preview: Primary Outpost' }),
    ).toHaveFocus();
    expect(get(colonyProjects).value.state.projects).toEqual({});
    expect(localStorage.getItem(PERSISTENCE_KEYS.colonyProjects)).toBe(before);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Close preview' }),
    );
    expect(
      screen.getByRole('button', { name: 'Primary Outpost' }),
    ).toHaveFocus();
    expect(get(colonyProjects).value.state.projects).toEqual({});

    await fireEvent.click(
      screen.getByRole('button', { name: 'Primary Outpost' }),
    );
    await fireEvent.click(screen.getByRole('button', { name: 'Close picker' }));
    expect(screen.queryByRole('button', { name: 'Add to plan' })).toBeNull();
    expect(add).toHaveFocus();
    expect(localStorage.getItem(PERSISTENCE_KEYS.colonyProjects)).toBe(before);

    await fireEvent.click(add);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Primary Outpost' }),
    );
    await fireEvent.click(screen.getByRole('button', { name: 'Add to plan' }));
    await advance(0);
    const records = Object.values(get(colonyProjects).value.state.projects);
    expect(records).toHaveLength(1);
    expect(recordToPlanner(records[0]).sites).toEqual([
      expect.objectContaining({
        facility_id: port.id,
        body_ref: '1',
        slot_kind: 'orbital',
        status: 'planned',
      }),
    ]);
    expect(add).toHaveFocus();
    expect(screen.queryByRole('button', { name: 'Add to plan' })).toBeNull();
    expect(
      screen.getByText('Primary Outpost added to plan on Test System A.'),
    ).toHaveAttribute('aria-live', 'polite');
    expect(localStorage.getItem(PERSISTENCE_KEYS.colonyProjects)).not.toBe(
      before,
    );
  });

  it('does not send an empty plan to the evaluation endpoint', async () => {
    render(PlanWorkspaceTestHost);
    await advance(1000);
    expect(evaluateV3Plan).not.toHaveBeenCalled();
    await editOrbitalSlots();
    await advance(1000);
    expect(evaluateV3Plan).not.toHaveBeenCalled();
    expect(
      screen.getByRole('complementary', { name: 'Summary' }),
    ).toHaveAttribute('aria-busy', 'false');
  });

  it('loads the catalogue once and reuses it when the planner remounts', async () => {
    queryClient.removeQueries({ queryKey: ['ed-finder', 'plan-catalogue'] });
    const first = render(PlanWorkspaceTestHost);
    await advance(0);
    expect(getV3PlanCatalogue).toHaveBeenCalledOnce();
    first.unmount();
    render(PlanWorkspaceTestHost);
    await advance(0);
    expect(getV3PlanCatalogue).toHaveBeenCalledOnce();
  });

  it('debounces edits for 250 ms and evaluates the latest slot count', async () => {
    seedPrimary();
    render(PlanWorkspaceTestHost);
    await advance(250);
    expect(evaluateV3Plan).toHaveBeenCalledOnce();
    vi.mocked(evaluateV3Plan).mockClear();

    await editOrbitalSlots();
    await advance(200);
    expect(evaluateV3Plan).not.toHaveBeenCalled();
    await editOrbitalSlots();
    await advance(249);
    expect(evaluateV3Plan).not.toHaveBeenCalled();
    await advance(1);
    expect(evaluateV3Plan).toHaveBeenCalledOnce();
    expect(
      vi.mocked(evaluateV3Plan).mock.calls[0][0].bodies?.[0],
    ).toMatchObject({
      body_ref: '1',
      orbital_slots: 2,
    });
  });

  it('moves a planned installation between orbital and surface slots and updates capacity highlights', async () => {
    mockWidth(true);
    const landableBodies: PlanBodiesV1 = {
      ...bodyData,
      bodies: [
        ...bodyData.bodies,
        {
          ...bodyData.bodies[0],
          body_id: '2',
          name: 'Test System A 1',
          direct_parent_body_id: '1',
          body_type: 'planet',
          is_main_star: false,
          is_landable: true,
        },
      ],
    };
    const surfaceInstallation: PlanFacilityV1 = {
      ...nonPort,
      id: 'surface-installation',
      name: 'Surface Installation',
      allowed_location: 'surface',
    };
    const switchCatalogue = {
      ...catalogue,
      facilities: [...catalogue.facilities, surfaceInstallation],
    };
    vi.mocked(listV3PlanBodies).mockResolvedValue(landableBodies);
    vi.mocked(getV3PlanCatalogue).mockResolvedValue(switchCatalogue);
    queryClient.setQueryData(
      ['ed-finder', 'plan-bodies', '42'],
      landableBodies,
    );
    queryClient.setQueryData(['ed-finder', 'plan-catalogue'], switchCatalogue);
    const record = plannerToRecord({
      ...emptyPlan('42'),
      sites: [
        importedSite('Primary port'),
        {
          ...importedSite('Second build'),
          site_id: 'second',
          facility_id: nonPort.id,
          body_ref: '2',
        },
      ],
      slots: { '2': { orbital_slots: 0, surface_slots: 0 } },
    });
    colonyProjects.set({
      version: 3,
      state: { projects: { [record.id]: record } },
    });
    render(PlanWorkspaceTestHost);
    const systemPane = within(
      screen.getByRole('tabpanel', { name: 'System layout' }),
    );
    const orbital = systemPane.getByRole('button', {
      name: 'Orbital slots on Test System A 1: 1 used, 0 total, over capacity',
    });
    const surface = systemPane.getByRole('button', {
      name: 'Surface slots on Test System A 1: 0 used, 0 total',
    });
    expect(orbital).toHaveTextContent('1/0');
    expect(orbital).toHaveClass('over-capacity');
    expect(surface).toHaveTextContent('0/0');
    expect(surface).not.toHaveClass('over-capacity');

    await openBuildOrder();
    await fireEvent.click(screen.getByLabelText('Edit Second build'));
    const facility = screen.getByRole('combobox', {
      name: 'Facility for step 2',
    });
    await fireEvent.change(facility, {
      target: { value: surfaceInstallation.id },
    });
    expect(facility).toHaveValue(surfaceInstallation.id);
    expect(
      recordToPlanner(get(colonyProjects).value.state.projects[record.id])
        .sites[1],
    ).toMatchObject({
      facility_id: surfaceInstallation.id,
      slot_kind: 'surface',
      body_ref: '2',
    });
    expect(orbital).toHaveAccessibleName(
      'Orbital slots on Test System A 1: 0 used, 0 total',
    );
    expect(orbital).toHaveTextContent('0/0');
    expect(orbital).not.toHaveClass('over-capacity');
    expect(surface).toHaveAccessibleName(
      'Surface slots on Test System A 1: 1 used, 0 total, over capacity',
    );
    expect(surface).toHaveTextContent('1/0');
    expect(surface).toHaveClass('over-capacity');

    await fireEvent.change(facility, { target: { value: nonPort.id } });
    expect(facility).toHaveValue(nonPort.id);
    expect(orbital).toHaveAccessibleName(
      'Orbital slots on Test System A 1: 1 used, 0 total, over capacity',
    );
    expect(orbital).toHaveTextContent('1/0');
    expect(orbital).toHaveClass('over-capacity');
    expect(surface).toHaveAccessibleName(
      'Surface slots on Test System A 1: 0 used, 0 total',
    );
    expect(surface).toHaveTextContent('0/0');
    expect(surface).not.toHaveClass('over-capacity');
  });

  it('ignores an earlier response even if it arrives after the current evaluation', async () => {
    seedPrimary();
    const oldRequest = deferred<PlanEvaluationV1>();
    const currentRequest = deferred<PlanEvaluationV1>();
    vi.mocked(evaluateV3Plan)
      .mockImplementationOnce(() => oldRequest.promise)
      .mockImplementationOnce(() => currentRequest.promise);
    render(PlanWorkspaceTestHost);
    await advance(250);
    await editOrbitalSlots();
    await advance(250);
    expect(vi.mocked(evaluateV3Plan).mock.calls[0][1]?.aborted).toBe(true);

    currentRequest.resolve(evaluated(23));
    await advance(0);
    const summary = screen.getByRole('complementary', { name: 'Summary' });
    expect(within(summary).getByText('23')).toBeInTheDocument();
    oldRequest.resolve(evaluated(999));
    await advance(0);
    expect(within(summary).getByText('23')).toBeInTheDocument();
    expect(within(summary).queryByText('999')).not.toBeInTheDocument();
  });

  it.each([
    new TypeError('Failed to fetch'),
    new ApiError(503, '/api/v1/plan/evaluate', 'Unavailable'),
    new ApiError(429, '/api/v1/plan/evaluate', 'Too many requests'),
  ])(
    'retries a failed evaluation once with the identical payload: %s',
    async (error) => {
      seedPrimary();
      vi.mocked(evaluateV3Plan).mockRejectedValueOnce(error);
      render(PlanWorkspaceTestHost);
      await advance(250);
      const summary = screen.getByRole('complementary', { name: 'Summary' });
      expect(within(summary).getByRole('alert')).toHaveTextContent(
        'The plan could not be evaluated. Try again shortly.',
      );
      const payload = vi.mocked(evaluateV3Plan).mock.calls[0][0];
      await advance(10000);
      expect(evaluateV3Plan).toHaveBeenCalledOnce();
      const retry = within(summary).getByRole('button', { name: 'Try again' });
      const retried = deferred<PlanEvaluationV1>();
      vi.mocked(evaluateV3Plan).mockReturnValueOnce(retried.promise);
      await fireEvent.click(retry);
      await fireEvent.click(retry);
      expect(evaluateV3Plan).toHaveBeenCalledTimes(2);
      expect(vi.mocked(evaluateV3Plan).mock.calls[1][0]).toEqual(payload);
      expect(summary).toHaveAttribute('aria-busy', 'true');
      retried.resolve(evaluated(23));
      await advance(0);
      expect(within(summary).getByText('23')).toBeInTheDocument();
      expect(within(summary).queryByRole('alert')).not.toBeInTheDocument();
      expect(
        within(summary).queryByRole('button', { name: 'Try again' }),
      ).not.toBeInTheDocument();
      await advance(10000);
      expect(evaluateV3Plan).toHaveBeenCalledTimes(2);
    },
  );

  it('counts down Retry-After seconds without retrying automatically', async () => {
    seedPrimary();
    vi.mocked(evaluateV3Plan).mockRejectedValueOnce(
      Object.assign(
        new ApiError(429, '/api/v1/plan/evaluate', 'Rate limited'),
        {
          retryAfterSeconds: 5,
        },
      ),
    );
    render(PlanWorkspaceTestHost);
    await advance(250);
    const summary = screen.getByRole('complementary', { name: 'Summary' });
    const retry = within(summary).getByRole('button', {
      name: 'Try again in 5 s',
    });
    expect(retry).toBeDisabled();
    await fireEvent.click(retry);
    expect(evaluateV3Plan).toHaveBeenCalledOnce();
    for (let seconds = 4; seconds > 0; seconds -= 1) {
      await advance(1000);
      expect(retry).toHaveAccessibleName(`Try again in ${seconds} s`);
      expect(retry).toBeDisabled();
    }
    await advance(1000);
    expect(retry).toHaveAccessibleName('Try again');
    expect(retry).toBeEnabled();
    expect(evaluateV3Plan).toHaveBeenCalledOnce();
    await fireEvent.click(retry);
    await advance(0);
    expect(evaluateV3Plan).toHaveBeenCalledTimes(2);
    expect(vi.mocked(evaluateV3Plan).mock.calls[1][0]).toEqual(
      vi.mocked(evaluateV3Plan).mock.calls[0][0],
    );
  });

  it('keeps the last good evaluation stale until a manual retry succeeds', async () => {
    seedPrimary();
    render(PlanWorkspaceTestHost);
    await advance(250);
    vi.mocked(evaluateV3Plan).mockRejectedValueOnce(new TypeError('Offline'));
    await editOrbitalSlots();
    await advance(250);
    const summary = screen.getByRole('complementary', { name: 'Summary' });
    expect(within(summary).getByText('17').closest('.stale')).not.toBeNull();
    const retried = deferred<PlanEvaluationV1>();
    vi.mocked(evaluateV3Plan).mockReturnValueOnce(retried.promise);
    await fireEvent.click(
      within(summary).getByRole('button', { name: 'Try again' }),
    );
    expect(within(summary).getByText('17').closest('.stale')).not.toBeNull();
    retried.resolve(evaluated(23));
    await advance(0);
    expect(within(summary).getByText('23').closest('.stale')).toBeNull();
  });

  it('announces blocked steps and keeps the last good evaluation greyed after a 400', async () => {
    seedPrimary();
    vi.mocked(evaluateV3Plan).mockResolvedValueOnce(evaluated(17, 2));
    render(PlanWorkspaceTestHost);
    await advance(250);
    expect(
      screen.getByText('2 blocked steps. 0 steps need information.'),
    ).toHaveAttribute('aria-live', 'polite');
    vi.mocked(evaluateV3Plan).mockRejectedValueOnce(
      new ApiError(400, '/api/v1/plan/evaluate', {
        error: 'plan_invalid',
        issues: [
          {
            code: 'INVALID_PRIMARY',
            site_id: null,
            message: 'The first build must be a port.',
          },
        ],
      }),
    );
    await editOrbitalSlots();
    await advance(250);
    const summary = screen.getByRole('complementary', { name: 'Summary' });
    expect(within(summary).getByRole('alert')).toHaveTextContent(
      'The first build must be a port.',
    );
    expect(within(summary).getByText('17').closest('.stale')).not.toBeNull();
    expect(
      within(summary).getByText(/Last good evaluation/),
    ).toBeInTheDocument();
    expect(
      within(summary).queryByRole('button', { name: /Try again/ }),
    ).not.toBeInTheDocument();
  });

  it('shows the blocked count only for a current evaluation of a non-empty plan', async () => {
    seedPrimary();
    vi.mocked(evaluateV3Plan).mockResolvedValue(blockedPrimary());
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(PlanWorkspaceTestHost);
    await advance(250);
    expect(buildOrderTab()).toHaveAccessibleName('Build order');
    expect(buildOrderTab()).toHaveTextContent('1 blocked');
    await fireEvent.click(screen.getByRole('button', { name: 'Abandon plan' }));
    await advance(1000);
    // The greyed last evaluation stays in the Summary, but the header must not
    // present its blocked count as current, least of all for an empty plan.
    expect(buildOrderTab()).toHaveTextContent('(0)');
    expect(buildOrderTab()).not.toHaveTextContent('blocked');
  });

  it('keeps the last evaluation greyed when the plan is abandoned', async () => {
    seedPrimary();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(PlanWorkspaceTestHost);
    await advance(250);
    expect(evaluateV3Plan).toHaveBeenCalledOnce();
    vi.mocked(evaluateV3Plan).mockClear();
    await fireEvent.click(screen.getByRole('button', { name: 'Abandon plan' }));
    await advance(1000);
    expect(evaluateV3Plan).not.toHaveBeenCalled();
    const summary = screen.getByRole('complementary', { name: 'Summary' });
    expect(within(summary).getByText('17').closest('.stale')).not.toBeNull();
    expect(summary).toHaveAttribute('aria-busy', 'false');
  });

  it.each([
    { status: 'planned', body_ref: '1' },
    { status: 'planned', body_ref: null },
    { status: 'building', body_ref: '1' },
    { status: 'building', body_ref: null },
    { status: 'complete', body_ref: null },
  ] as const)(
    'abandons a $status primary at $body_ref with confirmation, keeps slots and announces',
    async (primary) => {
      const slots = { '1': { orbital_slots: 5, surface_slots: null } };
      const record = plannerToRecord({
        ...emptyPlan('42'),
        slots,
        sites: [
          { ...importedSite('Primary port'), ...primary },
          {
            ...importedSite('Completed installation'),
            site_id: 'installation',
            facility_id: nonPort.id,
            status: 'complete',
          },
        ],
      });
      colonyProjects.set({
        version: 3,
        state: { projects: { [record.id]: record } },
      });
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
      render(PlanWorkspaceTestHost);
      await openBuildOrder();
      const abandon = screen.getByRole('button', { name: 'Abandon plan' });
      expect(abandon).toBeEnabled();
      await fireEvent.click(abandon);
      await advance(1000);
      expect(confirm).toHaveBeenCalledExactlyOnceWith(
        'Abandon this plan? All 2 sites for Test System will be removed, as if the claim were cancelled. Slot counts are kept.',
      );
      const updated = get(colonyProjects).value.state.projects[record.id];
      expect(recordToPlanner(updated)).toEqual({
        system_id64: '42',
        sites: [],
        slots,
      });
      const stored = JSON.parse(
        localStorage.getItem(PERSISTENCE_KEYS.colonyProjects)!,
      );
      expect(stored.state.projects[record.id]).toEqual(updated);
      expect(
        within(
          screen.getByRole('tabpanel', { name: 'Build order' }),
        ).queryAllByRole('heading', { level: 3 }),
      ).toHaveLength(0);
      expect(screen.getByText('Plan abandoned.')).toHaveAttribute(
        'aria-live',
        'polite',
      );
      expect(abandon).toBeDisabled();
      expect(evaluateV3Plan).not.toHaveBeenCalled();
    },
  );

  it('keeps the plan and storage unchanged when abandonment is cancelled', async () => {
    const record = seedPrimary();
    const stored = localStorage.getItem(PERSISTENCE_KEYS.colonyProjects);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(PlanWorkspaceTestHost);
    await fireEvent.click(screen.getByRole('button', { name: 'Abandon plan' }));
    expect(confirm).toHaveBeenCalledExactlyOnceWith(
      'Abandon this plan? All 1 sites for Test System will be removed, as if the claim were cancelled. Slot counts are kept.',
    );
    expect(get(colonyProjects).value.state.projects[record.id]).toEqual(record);
    expect(localStorage.getItem(PERSISTENCE_KEYS.colonyProjects)).toBe(stored);
    expect(screen.queryByText('Plan abandoned.')).not.toBeInTheDocument();
  });

  it('disables abandonment for a complete and placed primary with its protection title', async () => {
    const record = seedPrimary({ status: 'complete' });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(PlanWorkspaceTestHost);
    const abandon = screen.getByRole('button', { name: 'Abandon plan' });
    expect(abandon).toBeDisabled();
    expect(abandon).toHaveAttribute(
      'title',
      'A completed Primary Port can never be removed (RB-0008).',
    );
    await fireEvent.click(abandon);
    expect(confirm).not.toHaveBeenCalled();
    expect(get(colonyProjects).value.state.projects[record.id]).toEqual(record);
  });

  it('disables abandonment when the plan has no sites', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(PlanWorkspaceTestHost);
    const abandon = screen.getByRole('button', { name: 'Abandon plan' });
    expect(abandon).toBeDisabled();
    await fireEvent.click(abandon);
    expect(confirm).not.toHaveBeenCalled();
    expect(localStorage.getItem(PERSISTENCE_KEYS.colonyProjects)).toBeNull();
  });

  it('keeps the abandonment announcement when an earlier evaluation arrives', async () => {
    seedPrimary();
    const pendingEvaluation = deferred<PlanEvaluationV1>();
    vi.mocked(evaluateV3Plan).mockReturnValue(pendingEvaluation.promise);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(PlanWorkspaceTestHost);
    await advance(250);
    await fireEvent.click(screen.getByRole('button', { name: 'Abandon plan' }));
    expect(vi.mocked(evaluateV3Plan).mock.calls[0][1]?.aborted).toBe(true);
    pendingEvaluation.resolve(evaluated(17, 2));
    await advance(0);
    expect(screen.getByText('Plan abandoned.')).toHaveAttribute(
      'aria-live',
      'polite',
    );
  });

  it.each([false, true])(
    'moves the third site in storage, DOM and evaluation after opening Why: %s',
    async (openWhy) => {
      const sites: PlannerSite[] = [
        { ...importedSite('Primary port'), site_id: 'primary' },
        { ...importedSite('Second build'), site_id: 'second' },
        { ...importedSite('Third build'), site_id: 'third' },
      ];
      const record = plannerToRecord({
        ...emptyPlan('42'),
        sites,
      });
      vi.mocked(evaluateV3Plan).mockResolvedValue({
        ...evaluated(17, 1),
        steps: sites.map((site) => ({
          site_id: site.site_id,
          facility_id: site.facility_id,
          status: site.status,
          view: 'after_plan',
          result: site.site_id === 'second' ? 'BLOCKED' : 'OK',
          counted_port_number: null,
          cost_t2: 3,
          cost_t3: 0,
          reward_t2: 1,
          reward_t3: 0,
          t2_before: 0,
          t3_before: 0,
          t2_after: 0,
          t3_after: 0,
          reasons: site.site_id === 'second' ? ['INSUFFICIENT_T2'] : [],
          rule_ids: [],
          payers: site.site_id === 'second' ? [port.id] : [],
        })),
      });
      colonyProjects.set({
        version: 3,
        state: { projects: { [record.id]: record } },
      });
      render(PlanWorkspaceTestHost);
      await advance(250);
      await openBuildOrder();
      if (openWhy) {
        const why = screen.getByRole('button', { name: 'Why? Second build' });
        why.focus();
        await fireEvent.click(why);
        expect(
          screen.getByRole('dialog', { name: 'Step 2: Second build' }),
        ).toHaveFocus();
        await fireEvent.keyDown(window, { key: 'Escape' });
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        expect(why).toHaveFocus();
      }
      const move = screen.getByRole('button', { name: 'Move up Third build' });
      expect(move).toBeEnabled();
      await fireEvent.click(move);
      expect(
        get(colonyProjects).value.state.projects[
          record.id
        ].build_plan_placements.map((site) => site.site_id),
      ).toEqual(['primary', 'third', 'second']);
      const order = screen.getByRole('tabpanel', { name: 'Build order' });
      expect(
        within(order)
          .getAllByRole('heading', { level: 3 })
          .map((heading) => heading.textContent),
      ).toEqual(['Primary port', 'Third build', 'Second build']);
      const stored = JSON.parse(
        localStorage.getItem(PERSISTENCE_KEYS.colonyProjects)!,
      );
      expect(
        stored.state.projects[record.id].build_plan_placements.map(
          (site: PlannerSite) => site.site_id,
        ),
      ).toEqual(['primary', 'third', 'second']);
      await advance(250);
      expect(
        vi
          .mocked(evaluateV3Plan)
          .mock.calls.at(-1)?.[0]
          .sites.map((site) => site.site_id),
      ).toEqual(['primary', 'third', 'second']);
    },
  );

  it('creates one browser project on the first edit and restores it after remount', async () => {
    const first = render(PlanWorkspaceTestHost);
    await advance(250);
    expect(localStorage.getItem(PERSISTENCE_KEYS.colonyProjects)).toBeNull();
    await editOrbitalSlots();
    const entries = Object.values(get(colonyProjects).value.state.projects);
    expect(entries).toHaveLength(1);
    expect(entries[0].id).toMatch(/^colony-42-/);
    expect(entries[0].selected_body_assignments.cpe_slots_v1).toEqual({
      '1': { orbital_slots: 1, surface_slots: null },
    });
    expect(entries[0].build_plan_placements).toEqual([]);
    expect(
      localStorage.getItem(PERSISTENCE_KEYS.colonyProjects),
    ).not.toBeNull();

    first.unmount();
    colonyProjects.hydrate();
    render(PlanWorkspaceTestHost);
    expect(
      screen.getByRole('button', {
        name: 'Orbital slots on Test System A: 0 used, 1 total',
      }),
    ).toBeInTheDocument();
    await editOrbitalSlots();
    expect(Object.keys(get(colonyProjects).value.state.projects)).toEqual([
      entries[0].id,
    ]);
  });

  it('confirms replacement before importing and preserves older placements and assignments', async () => {
    const record = plannerToRecord(emptyPlan('42'));
    record.build_plan_placements = [{ facility_template_id: 'old-v2' }];
    record.selected_body_assignments.old_assignment = { retained: true };
    colonyProjects.set({
      version: 3,
      state: { projects: { [record.id]: record } },
    });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(PlanWorkspaceTestHost);
    const text = exportPlan({
      ...emptyPlan('42'),
      sites: [importedSite()],
      slots: { '1': { orbital_slots: 5, surface_slots: null } },
    });
    await upload(text);
    expect(confirm).toHaveBeenCalledOnce();
    expect(
      get(colonyProjects).value.state.projects[record.id].build_plan_placements,
    ).toHaveLength(1);

    confirm.mockReturnValue(true);
    await upload(text);
    const updated = get(colonyProjects).value.state.projects[record.id];
    expect(updated.build_plan_placements).toEqual([
      { facility_template_id: 'old-v2' },
      importedSite(),
    ]);
    expect(updated.selected_body_assignments).toEqual({
      old_assignment: { retained: true },
      cpe_slots_v1: { '1': { orbital_slots: 5, surface_slots: null } },
    });
  });

  it('requires separate cross-system and replacement confirmations', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false);
    render(PlanWorkspaceTestHost);
    const text = exportPlan({ ...emptyPlan('43'), sites: [importedSite()] });
    await upload(text);
    expect(confirm).toHaveBeenCalledOnce();
    expect(confirm).toHaveBeenLastCalledWith(
      expect.stringContaining('system 43'),
    );
    expect(confirm).toHaveBeenLastCalledWith(
      expect.stringContaining(
        'A completed Primary Port without a location here will stay editable until you give it one.',
      ),
    );
    expect(Object.keys(get(colonyProjects).value.state.projects)).toEqual([]);
    confirm.mockReturnValueOnce(true).mockReturnValueOnce(true);
    await upload(text);
    expect(confirm).toHaveBeenCalledTimes(3);
    expect(
      Object.values(get(colonyProjects).value.state.projects)[0].system_id64,
    ).toBe('42');
  });

  it.each(['42', '43'])(
    'refuses an imported non-port primary from system %s before any confirmation',
    async (systemId) => {
      const record = seedPrimary();
      const stored = localStorage.getItem(PERSISTENCE_KEYS.colonyProjects);
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
      render(PlanWorkspaceTestHost);
      await upload(
        exportPlan({
          ...emptyPlan(systemId),
          sites: [
            {
              ...importedSite('Completed installation'),
              facility_id: nonPort.id,
              status: 'complete',
            },
          ],
        }),
      );
      expect(screen.getByRole('alert')).toHaveTextContent(
        "This plan's first site is not a port, so it cannot be the Primary Port.",
      );
      expect(confirm).not.toHaveBeenCalled();
      expect(get(colonyProjects).value.state.projects[record.id]).toEqual(
        record,
      );
      expect(localStorage.getItem(PERSISTENCE_KEYS.colonyProjects)).toBe(
        stored,
      );
    },
  );

  it('lets an imported completed primary gain a missing location, then protects it', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(PlanWorkspaceTestHost);
    await upload(
      exportPlan({
        ...emptyPlan('43'),
        sites: [{ ...importedSite(), body_ref: '99', status: 'complete' }],
      }),
    );
    await openBuildOrder();
    await fireEvent.click(screen.getByLabelText('Edit Imported port'));
    const body = screen.getByLabelText('Body for step 1');
    const status = screen.getByLabelText('Status for step 1');
    expect(body).toBeEnabled();
    expect(body).toHaveValue('');
    expect(status).toBeEnabled();
    expect(status).toHaveValue('complete');
    await fireEvent.change(body, { target: { value: '1' } });
    expect(
      Object.values(get(colonyProjects).value.state.projects)[0]
        .build_plan_placements[0],
    ).toMatchObject({ body_ref: '1', status: 'complete' });
    expect(body).toBeDisabled();
    expect(status).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: 'Remove Imported port' }),
    ).not.toBeInTheDocument();
  });
});
