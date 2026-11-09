import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SystemPane from './SystemPane.svelte';
import { compactPlanFixture } from './__fixtures__/compact-plan';
import { setSlotCount } from './model';

vi.mock('./preview', () => ({
  evaluatePlanPreview: vi.fn(async () => compactPlanFixture(2, 0).evaluation),
}));

function siteGroupsFixture() {
  const fixture = compactPlanFixture(2, 3);
  for (const site of fixture.model.sites) site.body_ref = '2';
  fixture.model.sites[1].slot_kind = 'surface';
  fixture.model.sites[2].slot_kind = 'orbital';
  return fixture;
}

function renderSystem(fixture = siteGroupsFixture(), onselectsite = vi.fn()) {
  return render(SystemPane, {
    ...fixture,
    selectedBody: null,
    stale: false,
    onselectbody: vi.fn(),
    onselectsite,
    onslot: vi.fn(),
    onadd: vi.fn(),
  });
}

function siteButton(step: number) {
  return screen.getByRole('button', { name: new RegExp(`^Step ${step},`) });
}

afterEach(cleanup);

describe('system schematic', () => {
  it('draws the bodies and marks planned builds without adding controls', () => {
    const fixture = compactPlanFixture(3, 2);
    fixture.model.sites[0].body_ref = '2';
    fixture.model.sites[1].body_ref = '3';
    const { container } = renderSystem(fixture);
    const schematic = container.querySelector('[data-plan-schematic]')!;
    expect(schematic).toHaveTextContent('schematic not to scale');
    expect(
      [...schematic.querySelectorAll<HTMLElement>('[data-schematic-body]')].map(
        (el) => el.dataset.schematicBody,
      ),
    ).toEqual(['1', '2', '3', 'belt:1', 'belt:2']);
    expect(schematic).toHaveTextContent('Primary Port');
    // Read-only: the picture adds no buttons or links to the page.
    expect(schematic.querySelectorAll('button, a, input')).toHaveLength(0);
  });

  it('highlights the drawn body that holds the selected moon', () => {
    const fixture = compactPlanFixture(4, 0);
    fixture.bodies.bodies[3].direct_parent_body_id = '2';
    const { container } = render(SystemPane, {
      ...fixture,
      selectedBody: '4',
      stale: false,
      onselectbody: vi.fn(),
      onselectsite: vi.fn(),
      onslot: vi.fn(),
      onadd: vi.fn(),
    });
    const selected = container.querySelectorAll(
      '[data-plan-schematic] .schematic-item.selected',
    );
    expect(selected).toHaveLength(1);
    expect((selected[0] as HTMLElement).dataset.schematicBody).toBe('2');
  });
});

describe('surface slot sources', () => {
  it.each([0, 5])(
    'labels an API surface prediction of %s, leaving orbital slots unknown',
    (count) => {
      const fixture = compactPlanFixture(2, 0);
      fixture.model.slots = {};
      Object.assign(fixture.bodies.bodies[1], {
        predicted_surface_slots: count,
        prediction_status: 'predicted',
      });
      renderSystem(fixture);
      const surface = screen.getByRole('button', {
        name: `Surface slots on Compact Test System A 1: 0 used, ${count} total`,
      });
      expect(surface).toHaveTextContent(`0/${count}`);
      expect(surface).toHaveAccessibleDescription('predicted');
      expect(screen.getByText('predicted')).toBeVisible();
      expect(
        screen.getByRole('button', {
          name: 'Orbital slots on Compact Test System A 1: 0 used, unknown total',
        }),
      ).toHaveTextContent('0/?');
    },
  );

  it('lets an entered zero win, keeps it editable, and offers the prediction again when cleared', async () => {
    const fixture = compactPlanFixture(2, 0);
    fixture.model.slots = {};
    Object.assign(fixture.bodies.bodies[1], {
      predicted_surface_slots: 5,
      prediction_status: 'predicted',
    });
    const { rerender } = renderSystem(fixture);
    const onslot = vi.fn();
    const model = setSlotCount(fixture.model, '2', 'surface', 0);
    await rerender({ model, onslot });
    const surface = screen.getByRole('button', {
      name: 'Surface slots on Compact Test System A 1: 0 used, 0 total',
    });
    expect(surface).toHaveAccessibleDescription('entered by you');
    expect(screen.getByText('entered by you')).toBeVisible();
    expect(screen.queryByText('predicted')).not.toBeInTheDocument();
    await fireEvent.click(surface);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Increase surface slots' }),
    );
    expect(onslot).toHaveBeenLastCalledWith('2', 'surface', 1);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Use prediction' }),
    );
    expect(onslot).toHaveBeenLastCalledWith('2', 'surface', null);
    await rerender({ model: setSlotCount(model, '2', 'surface', null) });
    expect(surface).toHaveTextContent('0/5');
    expect(surface).toHaveAccessibleDescription('predicted');
    expect(screen.getByLabelText('Surface slot total')).toHaveTextContent('5');
    expect(
      screen.getByText(
        /Predicted slots — high-accuracy algorithm, not guaranteed/,
      ),
    ).toHaveTextContent('Verify in Architect Mode.');
  });

  it('shows an unknown prediction as unknown without a predicted source label', async () => {
    const fixture = compactPlanFixture(2, 0);
    fixture.model.slots = {};
    Object.assign(fixture.bodies.bodies[1], {
      predicted_surface_slots: null,
      prediction_status: 'unknown',
      missing_inputs: ['atmosphere'],
    });
    renderSystem(fixture);
    const surface = screen.getByRole('button', {
      name: 'Surface slots on Compact Test System A 1: 0 used, unknown total',
    });
    expect(surface).toHaveTextContent('0/?');
    expect(screen.queryByText('predicted')).not.toBeInTheDocument();
    expect(screen.queryByText('entered by you')).not.toBeInTheDocument();
    await fireEvent.click(surface);
    expect(screen.getByRole('button', { name: 'Unknown' })).toBeVisible();
    expect(screen.getByText(/Surface slots are unknown/)).toHaveTextContent(
      'Verify in Architect Mode.',
    );
  });
});

describe('System pane site groups', () => {
  it('shows two orbital sites before one surface site in named lists beneath the body row', () => {
    const { container } = renderSystem();
    const body = container.querySelector<HTMLElement>('[data-plan-body="2"]')!;
    const orbital = within(body).getByRole('list', {
      name: 'Orbital sites on A 1',
    });
    const surface = within(body).getByRole('list', {
      name: 'Surface sites on A 1',
    });
    expect(within(body).getAllByRole('list')).toEqual([orbital, surface]);
    expect(within(orbital).getAllByRole('listitem')).toHaveLength(2);
    expect(within(surface).getAllByRole('listitem')).toHaveLength(1);
    expect(within(orbital).getAllByRole('button')).toEqual([
      siteButton(1),
      siteButton(3),
    ]);
    expect(within(surface).getAllByRole('button')).toEqual([siteButton(2)]);
    expect(body.firstElementChild).toHaveClass('compact-body-row');
    for (const [name, list] of [
      ['Orbital', orbital],
      ['Surface', surface],
    ] as const) {
      const group = list.parentElement!;
      const heading = within(group).getByRole('heading', { name });
      expect(group.children[0]).toBe(heading);
      expect(group.children[1]).toBe(list);
      expect(heading.querySelector('svg')).toHaveAttribute(
        'aria-hidden',
        'true',
      );
      expect(heading.querySelector('svg')).toHaveAttribute(
        'focusable',
        'false',
      );
    }
    expect(surface.parentElement).toHaveClass('surface');
    expect(orbital.parentElement).not.toHaveClass('surface');
  });

  it('renders only the surface group for a body with no orbital sites', () => {
    const fixture = siteGroupsFixture();
    for (const site of fixture.model.sites) site.slot_kind = 'surface';
    renderSystem(fixture);

    expect(screen.queryByRole('heading', { name: 'Orbital' })).toBeNull();
    expect(screen.queryByRole('list', { name: /Orbital sites/ })).toBeNull();
    const surface = screen.getByRole('list', { name: 'Surface sites on A 1' });
    expect(within(surface).getAllByRole('listitem')).toHaveLength(3);
    expect(screen.getByRole('heading', { name: 'Surface' })).toBeVisible();
  });

  it('does not add empty site groups beneath a body with no sites', () => {
    const { container } = renderSystem();
    const body = container.querySelector<HTMLElement>('[data-plan-body="1"]')!;
    expect(within(body).queryByRole('list')).toBeNull();
    expect(within(body).queryByRole('heading')).toBeNull();
  });

  it('places the full station name before its muted facility type and tier', () => {
    const fixture = siteGroupsFixture();
    const name = 'Deep Space Communications and Exploration Anchorage';
    fixture.model.sites[2].name = name;
    fixture.facilities[1].name = 'Communication Installation';
    renderSystem(fixture);

    const button = siteButton(3);
    const label = button.querySelector('.tile-label')!;
    expect(label.children).toHaveLength(2);
    expect(label.children[0]).toHaveClass('site-name');
    expect(label.children[0].tagName).toBe('STRONG');
    expect(label.children[0]).toHaveTextContent(name);
    expect(label.children[1]).toHaveClass('site-type');
    expect(label.children[1]).toHaveTextContent('Communication Installation');
    expect(label.children[1]).toHaveAttribute(
      'title',
      'Communication Installation',
    );
    expect(label.nextElementSibling).toHaveClass('plan-chip');
    expect(label.nextElementSibling).toHaveTextContent('T2');
    const fullLabel = `Step 3, planned, ${name}, Communication Installation, orbital, Needs info`;
    expect(button).toHaveAccessibleName(fullLabel);
    expect(button).toHaveAttribute('title', fullLabel);
  });

  it('uses the facility type once in the name position when the station has no name', () => {
    const fixture = siteGroupsFixture();
    fixture.model.sites[1].name = '';
    renderSystem(fixture);

    const button = siteButton(2);
    expect(button.querySelector('.site-name')).toHaveTextContent(
      'Research Installation',
    );
    expect(button.querySelector('.site-type')).toBeNull();
    expect(within(button).getAllByText('Research Installation')).toHaveLength(
      1,
    );
    expect(button).toHaveAccessibleName(
      'Step 2, building, Research Installation, surface',
    );
    expect(button).toHaveAttribute(
      'title',
      'Step 2, building, Research Installation, surface',
    );
  });

  it('switches to Types only without repeating types, and restores station names', async () => {
    const fixture = siteGroupsFixture();
    renderSystem(fixture);
    const toggle = screen.getByRole('button', { name: 'Names and types' });
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    await fireEvent.click(toggle);

    expect(toggle).toHaveAccessibleName('Types only');
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    for (const [index, site] of fixture.model.sites.entries()) {
      const button = siteButton(index + 1);
      const facility = fixture.facilities.find(
        (entry) => entry.id === site.facility_id,
      )!;
      expect(button.querySelector('.site-name')).toHaveTextContent(
        facility.name,
      );
      expect(button.querySelector('.site-type')).toBeNull();
      expect(screen.queryByText(site.name)).toBeNull();
      expect(button.getAttribute('aria-label')).not.toContain(site.name);
      expect(button.getAttribute('title')).not.toContain(site.name);
    }
    expect(siteButton(1)).toHaveAccessibleName(
      'Step 1, Primary Port, complete, Primary Outpost, orbital',
    );
    await fireEvent.click(toggle);
    expect(toggle).toHaveAccessibleName('Names and types');
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    for (const site of fixture.model.sites) {
      expect(screen.getByText(site.name)).toBeVisible();
    }
  });

  it('starts the primary accessible name with its step and Primary Port, and retains Why selection', async () => {
    const onselectsite = vi.fn();
    renderSystem(siteGroupsFixture(), onselectsite);
    const button = siteButton(1);
    const fullLabel =
      'Step 1, Primary Port, complete, Colony site 1, Primary Outpost, orbital';
    expect(button).toHaveAccessibleName(fullLabel);
    expect(button).toHaveAttribute('title', fullLabel);
    expect(button.querySelector('.tile-number')).toHaveTextContent(/^★\s*1$/);
    expect(within(button).getByText('★')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    expect(button.querySelectorAll('svg')).toHaveLength(1);
    expect(siteButton(3).querySelectorAll('svg')).toHaveLength(2);

    await fireEvent.click(button);
    expect(onselectsite).toHaveBeenCalledExactlyOnceWith('site-1');
  });

  it('groups null and unresolved Unplaced locations by slot kind in the same order', () => {
    const fixture = siteGroupsFixture();
    fixture.model.sites[0].body_ref = null;
    fixture.model.sites[1].body_ref = 'unknown-body';
    fixture.model.sites[2].body_ref = null;
    renderSystem(fixture);

    expect(screen.getByRole('heading', { name: 'Unplaced' })).toBeVisible();
    const orbital = screen.getByRole('list', {
      name: 'Orbital sites in Unplaced',
    });
    const surface = screen.getByRole('list', {
      name: 'Surface sites in Unplaced',
    });
    expect(screen.getAllByRole('list', { name: /sites in Unplaced$/ })).toEqual(
      [orbital, surface],
    );
    expect(within(orbital).getAllByRole('listitem')).toHaveLength(2);
    expect(within(surface).getAllByRole('listitem')).toHaveLength(1);
    expect(within(orbital).getAllByRole('button')).toEqual([
      siteButton(1),
      siteButton(3),
    ]);
    expect(within(surface).getAllByRole('button')).toEqual([siteButton(2)]);
    expect(surface.parentElement).toHaveClass('surface');
    expect(screen.queryByRole('list', { name: /sites on / })).toBeNull();
  });

  it('keeps retired and missing locations in their own muted group above Unplaced with Why selection', async () => {
    const fixture = compactPlanFixture(4, 4);
    const retiredBody = fixture.bodies.bodies[1];
    retiredBody.lifecycle_state = 'RETIRED';
    const missingBody = fixture.bodies.bodies[2];
    missingBody.lifecycle_state = 'MISSING_CANDIDATE';
    const retiredBelt = fixture.bodies.belts[0];
    retiredBelt.lifecycle_state = 'RETIRED';
    fixture.model.sites[0].body_ref = retiredBody.body_id;
    fixture.model.sites[1].body_ref = missingBody.body_id;
    fixture.model.sites[1].slot_kind = 'surface';
    fixture.model.sites[2].body_ref = `belt:${retiredBelt.belt_id}`;
    fixture.model.sites[3].body_ref = null;
    const savedSites = structuredClone(fixture.model.sites);
    const onselectsite = vi.fn();
    renderSystem(fixture, onselectsite);

    const heading = screen.getByRole('heading', {
      name: 'No longer in the galaxy data',
    });
    expect(heading).toBeVisible();
    expect(heading).toHaveClass('plan-muted');
    const headings = screen.getAllByRole('heading');
    expect(headings.indexOf(heading)).toBeLessThan(
      headings.indexOf(screen.getByRole('heading', { name: 'Unplaced' })),
    );
    const orbital = screen.getByRole('list', {
      name: 'Orbital sites on locations no longer in the galaxy data',
    });
    const surface = screen.getByRole('list', {
      name: 'Surface sites on locations no longer in the galaxy data',
    });
    expect(within(orbital).getAllByRole('button')).toEqual([
      siteButton(1),
      siteButton(3),
    ]);
    expect(within(surface).getAllByRole('button')).toEqual([siteButton(2)]);
    for (const [step, location, state] of [
      [1, retiredBody.name, 'retired'],
      [2, missingBody.name, 'missing from the latest data'],
      [3, retiredBelt.name, 'retired'],
    ] as const) {
      const button = siteButton(step);
      expect(button).toHaveClass('plan-tile');
      expect(button).toHaveAccessibleName(
        expect.stringContaining(
          `${location}, ${state}, location no longer current`,
        ),
      );
      expect(button.getAttribute('aria-label')).toMatch(
        /, location no longer current$/,
      );
      expect(within(button).getByText(`${location} · ${state}`)).toBeVisible();
      await fireEvent.click(button);
      expect(onselectsite).toHaveBeenLastCalledWith(`site-${step}`);
    }
    expect(
      within(
        screen.getByRole('list', { name: 'Surface sites in Unplaced' }),
      ).getAllByRole('button'),
    ).toEqual([siteButton(4)]);
    expect(fixture.model.sites).toEqual(savedSites);
  });

  it('shows a belt slot total and usage saved under a cluster the belt no longer selects', () => {
    // The belt's first cluster was retired after a site and a slot total were
    // saved on it, so the belt row now uses cluster 2. The row must show the
    // same capacity and occupancy as the plan check, not "0/?".
    const fixture = compactPlanFixture(2, 1);
    const belt = fixture.bodies.belts[0];
    const cluster = (id: string, number: number, lifecycle_state: string) => ({
      ...fixture.bodies.bodies[1],
      body_id: id,
      name: `${belt.name} Cluster ${number}`,
      body_type: 'belt cluster',
      direct_parent_body_id: '1',
      lifecycle_state,
    });
    fixture.bodies.bodies.push(
      cluster('c1', 1, 'RETIRED'),
      cluster('c2', 2, 'ACTIVE'),
    );
    fixture.model.sites[0] = {
      ...fixture.model.sites[0],
      body_ref: 'c1',
      slot_kind: 'orbital',
      status: 'complete',
    };
    fixture.model.slots = {
      c1: { orbital_slots: 2, surface_slots: null },
    };
    renderSystem(fixture);
    expect(
      screen.getByRole('button', {
        name: `Orbital slots on ${belt.name}: 1 used, 2 total`,
      }),
    ).toBeVisible();
  });

  it('offers the facility picker only on current locations', async () => {
    const fixture = compactPlanFixture(3, 0);
    fixture.bodies.bodies[1].lifecycle_state = 'RETIRED';
    fixture.bodies.bodies[2].lifecycle_state = 'MISSING_CANDIDATE';
    fixture.bodies.belts[0].lifecycle_state = 'RETIRED';
    renderSystem(fixture);

    for (const name of [
      fixture.bodies.bodies[1].name,
      fixture.bodies.bodies[2].name,
      fixture.bodies.belts[0].name,
    ]) {
      expect(
        screen.queryByRole('button', { name: `Add a build on ${name}` }),
      ).toBeNull();
    }
    const currentBody = fixture.bodies.bodies[0];
    await fireEvent.click(
      screen.getByRole('button', {
        name: `Add a build on ${currentBody.name}`,
      }),
    );
    expect(
      screen.getByRole('region', {
        name: `Facilities for ${currentBody.name}`,
      }),
    ).toBeVisible();
    expect(
      screen.getByRole('heading', {
        name: `Add a facility on ${currentBody.name}`,
      }),
    ).toBeVisible();
  });
});

describe('System pane facility popup', () => {
  it('opens a labelled non-modal dialog outside the body list and focuses the selected tab', async () => {
    const fixture = compactPlanFixture(2, 0);
    renderSystem(fixture);
    const opener = screen.getByRole('button', {
      name: `Add a build on ${fixture.bodies.bodies[1].name}`,
    });
    opener.focus();
    await fireEvent.click(opener);
    const dialog = screen.getByRole('dialog', {
      name: `Add a facility on ${fixture.bodies.bodies[1].name}`,
    });
    expect(dialog).toHaveAttribute('aria-modal', 'false');
    expect(dialog.closest('.plan-body-tree')).toBeNull();
    expect(opener).toHaveAttribute('aria-expanded', 'true');
    await waitFor(() =>
      expect(
        within(dialog).getByRole('tab', { name: 'Orbital' }),
      ).toHaveFocus(),
    );
  });

  it('limits the popup to the visible viewport while a mobile keyboard shrinks it', async () => {
    const listeners = new Map<string, EventListener>();
    const visualViewport = {
      width: 390,
      height: 300,
      offsetLeft: 0,
      offsetTop: 0,
      addEventListener: (type: string, listener: EventListener) =>
        listeners.set(type, listener),
      removeEventListener: (type: string) => listeners.delete(type),
    };
    const original = Object.getOwnPropertyDescriptor(window, 'visualViewport');
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: visualViewport,
    });
    try {
      const fixture = compactPlanFixture(2, 0);
      renderSystem(fixture);
      await fireEvent.click(
        screen.getByRole('button', {
          name: `Add a build on ${fixture.bodies.bodies[1].name}`,
        }),
      );
      const dialog = screen.getByRole('dialog');
      await waitFor(() =>
        expect(dialog.style.maxHeight).toBe('min(38rem, 284px)'),
      );
      // The keyboard closes: the visible height grows and the limit follows.
      visualViewport.height = 700;
      listeners.get('resize')?.(new Event('resize'));
      await waitFor(() =>
        expect(dialog.style.maxHeight).toBe('min(38rem, 684px)'),
      );
    } finally {
      if (original) Object.defineProperty(window, 'visualViewport', original);
      else delete (window as { visualViewport?: unknown }).visualViewport;
    }
  });

  it.each(['Escape', 'Close', 'outside'] as const)(
    'closes with %s and returns focus to the add button',
    async (method) => {
      const fixture = compactPlanFixture(2, 0);
      renderSystem(fixture);
      const opener = screen.getByRole('button', {
        name: `Add a build on ${fixture.bodies.bodies[1].name}`,
      });
      opener.focus();
      await fireEvent.click(opener);
      await waitFor(() =>
        expect(screen.getByRole('tab', { name: 'Orbital' })).toHaveFocus(),
      );
      if (method === 'Escape')
        await fireEvent.keyDown(window, { key: 'Escape' });
      else if (method === 'Close')
        await fireEvent.click(
          screen.getByRole('button', { name: /Close picker/ }),
        );
      else
        await fireEvent.click(screen.getByRole('heading', { name: 'System' }));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(opener).toHaveAttribute('aria-expanded', 'false');
      expect(opener).toHaveFocus();
    },
  );

  it('switches to a second body with one popup and returns focus to its opener', async () => {
    const fixture = compactPlanFixture(2, 0);
    renderSystem(fixture);
    const first = screen.getByRole('button', {
      name: `Add a build on ${fixture.bodies.bodies[0].name}`,
    });
    const second = screen.getByRole('button', {
      name: `Add a build on ${fixture.bodies.bodies[1].name}`,
    });
    await fireEvent.click(first);
    await fireEvent.click(second);
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(screen.getByRole('dialog')).toHaveAccessibleName(
      `Add a facility on ${fixture.bodies.bodies[1].name}`,
    );
    expect(first).toHaveAttribute('aria-expanded', 'false');
    expect(second).toHaveAttribute('aria-expanded', 'true');
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Orbital' })).toHaveFocus(),
    );
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(second).toHaveFocus();
  });

  it('previews without adding, then explicitly adds to its body, announces, closes, and restores the opener', async () => {
    const fixture = compactPlanFixture(2, 0);
    const onadd = vi.fn();
    render(SystemPane, {
      ...fixture,
      selectedBody: null,
      stale: false,
      onselectbody: vi.fn(),
      onselectsite: vi.fn(),
      onslot: vi.fn(),
      onadd,
    });
    const opener = screen.getByRole('button', {
      name: `Add a build on ${fixture.bodies.bodies[1].name}`,
    });
    await fireEvent.click(opener);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Primary Outpost' }),
    );
    expect(onadd).not.toHaveBeenCalled();
    expect(
      screen.getByRole('region', { name: 'Preview: Primary Outpost' }),
    ).toBeVisible();
    await fireEvent.click(screen.getByRole('button', { name: 'Add to plan' }));
    expect(onadd).toHaveBeenCalledExactlyOnceWith(fixture.facilities[0], '2');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(screen.getByRole('status')).toHaveTextContent(
      'Primary Outpost added to plan on Compact Test System A 1.',
    );
  });

  it('dismisses the preview before the picker with Escape and never adds', async () => {
    const fixture = compactPlanFixture(2, 0);
    const onadd = vi.fn();
    const { rerender } = renderSystem(fixture);
    await rerender({ onadd });
    const opener = screen.getByRole('button', {
      name: `Add a build on ${fixture.bodies.bodies[1].name}`,
    });
    await fireEvent.click(opener);
    const choice = screen.getByRole('button', { name: 'Primary Outpost' });
    await fireEvent.click(choice);
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(
      screen.queryByRole('region', { name: 'Preview: Primary Outpost' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeVisible();
    expect(choice).toHaveFocus();
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(onadd).not.toHaveBeenCalled();
  });

  it('keeps a rejected addition open without announcing success', async () => {
    const fixture = compactPlanFixture(2, 0);
    const { rerender } = renderSystem(fixture);
    const onadd = vi.fn(() => false);
    await rerender({ onadd });
    await fireEvent.click(
      screen.getByRole('button', {
        name: `Add a build on ${fixture.bodies.bodies[1].name}`,
      }),
    );
    await fireEvent.click(
      screen.getByRole('button', { name: 'Primary Outpost' }),
    );
    const add = screen.getByRole('button', { name: 'Add to plan' });
    add.focus();
    await fireEvent.click(add);
    expect(onadd).toHaveBeenCalledExactlyOnceWith(fixture.facilities[0], '2');
    expect(
      screen.getByRole('region', { name: 'Preview: Primary Outpost' }),
    ).toBeVisible();
    expect(add).toHaveFocus();
    expect(screen.getByRole('status').textContent?.trim()).toBe('');
  });

  it('keeps a slot editor mounted while its task panel is hidden and suspends global keyboard handling', async () => {
    const { rerender } = renderSystem(compactPlanFixture(2, 0));
    const trigger = screen.getByRole('button', {
      name: /^Orbital slots on Compact Test System A 1:/,
    });
    await fireEvent.click(trigger);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Increase orbital slots' }),
    );
    const editor = screen.getByRole('dialog', {
      name: 'Orbital slots on Compact Test System A 1',
    });
    expect(screen.getByLabelText('Orbital slot total')).toHaveTextContent('3');
    await rerender({ hidden: true });
    expect(editor).not.toBeVisible();
    const tab = document.createElement('button');
    document.body.append(tab);
    tab.focus();
    const key = new KeyboardEvent('keydown', { key: 'Tab', cancelable: true });
    window.dispatchEvent(key);
    expect(key.defaultPrevented).toBe(false);
    await fireEvent.keyDown(window, { key: 'Escape' });
    await fireEvent.pointerDown(tab);
    expect(tab).toHaveFocus();
    await rerender({ hidden: false });
    expect(editor).toBeVisible();
    expect(screen.getByLabelText('Orbital slot total')).toHaveTextContent('3');
    tab.remove();
  });
});
