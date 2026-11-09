import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import BuildOrder from './BuildOrder.svelte';
import SystemPane from './SystemPane.svelte';
import { compactPlanFixture } from './__fixtures__/compact-plan';
import { resolvePlanLocations } from './locations';
import { primaryPortLocks, PRIMARY_PORT_PROTECTION, updateSite } from './model';

type Fixture = ReturnType<typeof compactPlanFixture>;

function renderSystem(fixture: Fixture) {
  return render(SystemPane, {
    ...fixture,
    selectedBody: null,
    stale: false,
    onselectbody: vi.fn(),
    onselectsite: vi.fn(),
    onslot: vi.fn(),
    onadd: vi.fn(),
  });
}

function renderOrder(fixture: Fixture, onupdate = vi.fn()) {
  return render(BuildOrder, {
    model: fixture.model,
    facilities: fixture.facilities,
    locations: resolvePlanLocations(fixture.bodies),
    systemName: fixture.systemName,
    evaluation: fixture.evaluation,
    stale: false,
    selectedBody: null,
    selectedSite: null,
    filter: 'All',
    onfilter: vi.fn(),
    onwhy: vi.fn(),
    onmove: vi.fn(),
    onupdate,
    onremove: vi.fn(),
  });
}

afterEach(cleanup);

describe('compact planner layout', () => {
  it('keeps 40 bodies, two belts and 80 sites in compact body rows, individual site lines and two-line closed steps', () => {
    const fixture = compactPlanFixture();
    const system = renderSystem(fixture);
    const bodyRows = system.container.querySelectorAll('.compact-body-row');
    expect(bodyRows).toHaveLength(42);
    for (const row of bodyRows) {
      expect(row.querySelectorAll('.body-name')).toHaveLength(1);
      expect(row.querySelectorAll('.body-features')).toHaveLength(1);
      expect(row.querySelectorAll('.slot-box').length).toBeGreaterThan(0);
      expect(row.lastElementChild).toHaveClass('add-build');
      expect(row.querySelector('p, h3, br')).toBeNull();
    }
    for (const body of fixture.bodies.bodies) {
      const row = system.container.querySelector(
        `[data-plan-body="${body.body_id}"] > .compact-body-row`,
      )!;
      expect(row.querySelectorAll('.slot-box')).toHaveLength(2);
      const groups = row.parentElement!.querySelectorAll('.plan-site-group');
      expect(groups.length).toBeGreaterThan(0);
      for (const group of groups) {
        expect(row.contains(group)).toBe(false);
        expect(group.querySelectorAll('.site-group-heading')).toHaveLength(1);
        expect(group.querySelectorAll('ul.plan-sites')).toHaveLength(1);
      }
      const sites = row.parentElement!.querySelectorAll(
        '.plan-sites > .plan-site-row',
      );
      expect(sites).toHaveLength(2);
      for (const site of sites) {
        expect(site.tagName).toBe('LI');
        expect(site.children).toHaveLength(1);
        const line = site.firstElementChild!;
        expect(line).toHaveClass('plan-tile');
        expect(line.tagName).toBe('BUTTON');
        expect(line.children[0]).toHaveClass('tile-number');
        expect(line.children[1].tagName.toLowerCase()).toBe('svg');
        expect(line.children[2]).toHaveClass('tile-label');
        expect(line.querySelectorAll('.site-name')).toHaveLength(1);
        expect(line.querySelectorAll('.site-type')).toHaveLength(1);
        expect(line.querySelectorAll('.plan-chip')).toHaveLength(1);
        expect(line.querySelector('p, h3, h4, br')).toBeNull();
      }
    }
    expect(system.container.querySelectorAll('.plan-site-row')).toHaveLength(
      80,
    );
    expect(system.container.querySelectorAll('.plan-tile')).toHaveLength(80);

    const order = renderOrder(fixture);
    const steps = order.container.querySelectorAll('.build-step');
    expect(steps).toHaveLength(80);
    for (const [index, step] of [...steps].entries()) {
      expect(step).toHaveClass('compact-step');
      expect(step.children).toHaveLength(2);
      expect(step.children[0]).toHaveClass('step-title');
      expect(step.children[1]).toHaveClass('step-detail-row');
      expect(step.children[0].querySelector('h3')).toHaveTextContent(
        fixture.model.sites[index].name,
      );
      expect(
        step.children[0].querySelector('.evaluation-value'),
      ).toBeInTheDocument();
      const detailRow = step.children[1];
      expect(
        [...detailRow.querySelectorAll('.step-actions button')].map((control) =>
          control.getAttribute('aria-label'),
        ),
      ).toEqual(
        ['Move up', 'Move down', 'Why?'].map(
          (action) => `${action} ${fixture.model.sites[index].name}`,
        ),
      );
      expect(detailRow.querySelector('summary')).toHaveAttribute(
        'aria-label',
        `Edit ${fixture.model.sites[index].name}`,
      );
      expect(step.querySelector('.step-edit')).not.toHaveAttribute('open');
    }
  }, 15_000);

  it('retains full feature names and slot names alongside short visible markers', () => {
    const fixture = compactPlanFixture(2, 4);
    const { container } = renderSystem(fixture);
    const row = container.querySelector('[data-plan-body="2"]')!;
    for (const label of [
      'Landable',
      'Rings',
      'Geological 2',
      'Biological 1',
      'Volcanism: Major Silicate Vapour Geysers',
      'Atmosphere: Hot thick Argon-rich',
      'Terraformable: Candidate for terraforming',
    ]) {
      const marker = within(row as HTMLElement).getByTitle(label);
      expect(marker).toHaveAttribute('aria-label', label);
      expect(marker).toHaveAccessibleName(label);
      expect(marker).toHaveClass('body-feature');
      expect(marker.textContent!.trim()).not.toBe('');
      expect(marker.textContent!.length).toBeLessThan(label.length);
    }
    for (const label of ['Metal Rich', 'Rocky', 'Pristine']) {
      for (const marker of screen.getAllByTitle(label)) {
        expect(marker).toHaveAttribute('aria-label', label);
        expect(marker).toHaveAccessibleName(label);
      }
    }
    expect(
      screen.getByRole('button', {
        name: 'Orbital slots on Compact Test System A: 2 used, unknown total',
      }),
    ).toHaveTextContent('2/?');
    expect(
      screen.getByRole('button', {
        name: 'Surface slots on Compact Test System A: not available',
      }),
    ).toBeDisabled();
  });

  it('allows location and status edits for a completed Unplaced primary and locks them once placed', async () => {
    const fixture = compactPlanFixture(2, 4);
    fixture.model.sites[0].body_ref = null;
    const onupdate = vi.fn();
    const order = renderOrder(fixture, onupdate);

    await fireEvent.click(screen.getByLabelText('Edit Colony site 1'));
    const location = screen.getByRole('combobox', {
      name: 'Body for step 1',
    });
    const status = screen.getByRole('combobox', {
      name: 'Status for step 1',
    });
    expect(location).toBeEnabled();
    expect(location).toHaveValue('');
    expect(status).toBeEnabled();
    expect(status).toHaveValue('complete');
    expect(
      screen.getByRole('combobox', { name: 'Facility for step 1' }),
    ).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: 'Remove Colony site 1' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(PRIMARY_PORT_PROTECTION)).toBeVisible();

    await fireEvent.change(location, { target: { value: '1' } });
    expect(onupdate).toHaveBeenLastCalledWith('site-1', { body_ref: '1' });

    const placed = updateSite(fixture.model, 'site-1', { body_ref: '1' });
    await order.rerender({ model: placed });
    expect(location).toHaveValue('1');
    expect(location).toBeDisabled();
    expect(status).toHaveValue('complete');
    expect(status).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: 'Remove Colony site 1' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(PRIMARY_PORT_PROTECTION)).toBeVisible();
  });

  it.each([
    { status: 'planned', body_ref: null },
    { status: 'planned', body_ref: '1' },
    { status: 'building', body_ref: null },
    { status: 'building', body_ref: '1' },
    { status: 'complete', body_ref: null },
    { status: 'complete', body_ref: '1' },
    { status: 'demolished', body_ref: null },
    { status: 'demolished', body_ref: '1' },
  ] as const)(
    'uses the Primary Port locks for a $status primary on body $body_ref',
    async ({ status, body_ref }) => {
      const fixture = compactPlanFixture(2, 4);
      const primary = fixture.model.sites[0];
      primary.status = status;
      primary.body_ref = body_ref;
      const locks = primaryPortLocks(primary);
      renderOrder(fixture);

      await fireEvent.click(screen.getByLabelText('Edit Colony site 1'));
      for (const [field, locked] of [
        ['Facility', locks.facility],
        ['Body', locks.body],
        ['Status', locks.status],
      ] as const) {
        const control = screen.getByRole('combobox', {
          name: `${field} for step 1`,
        });
        if (locked) expect(control).toBeDisabled();
        else expect(control).toBeEnabled();
      }
      expect(
        screen.getByRole('textbox', { name: 'Name for step 1' }),
      ).toBeEnabled();
      expect(
        screen.queryByRole('button', { name: 'Remove Colony site 1' }),
      ).not.toBeInTheDocument();
      expect(screen.getByText(PRIMARY_PORT_PROTECTION)).toBeVisible();
      const statusControl = screen.getByRole('combobox', {
        name: 'Status for step 1',
      });
      expect(
        within(statusControl)
          .getAllByRole('option')
          .map((option) => option.getAttribute('value')),
      ).toEqual(['planned', 'building', 'complete']);
      expect(
        within(statusControl).queryByRole('option', { name: 'demolished' }),
      ).not.toBeInTheDocument();
      const facilityControl = screen.getByRole('combobox', {
        name: 'Facility for step 1',
      });
      expect(
        within(facilityControl)
          .getAllByRole('option')
          .map((option) => option.getAttribute('value')),
      ).toEqual(['primary-port']);
      for (const direction of ['up', 'down']) {
        const control = screen.getByRole('button', {
          name: `Move ${direction} Colony site 1`,
        });
        expect(control).toBeDisabled();
        expect(control).toHaveAttribute('title', PRIMARY_PORT_PROTECTION);
      }
    },
  );

  it('keeps secondary sites editable and removable with all four statuses', async () => {
    const fixture = compactPlanFixture(2, 4);
    renderOrder(fixture);
    await fireEvent.click(screen.getByLabelText('Edit Colony site 2'));

    for (const field of ['Facility', 'Body', 'Status']) {
      expect(
        screen.getByRole('combobox', { name: `${field} for step 2` }),
      ).toBeEnabled();
    }
    const status = screen.getByRole('combobox', { name: 'Status for step 2' });
    expect(
      within(status)
        .getAllByRole('option')
        .map((option) => option.getAttribute('value')),
    ).toEqual(['planned', 'building', 'complete', 'demolished']);
    expect(
      screen.getByRole('button', { name: 'Remove Colony site 2' }),
    ).toBeEnabled();
  });

  it('uses SVG status and result icons and keeps Primary Port protection inside the edit disclosure', async () => {
    const fixture = compactPlanFixture(2, 4);
    const system = renderSystem(fixture);
    for (const site of fixture.model.sites) {
      const tile = within(system.container).getByRole('button', {
        name: new RegExp(
          `^Step ${fixture.model.sites.indexOf(site) + 1}, ${fixture.model.sites.indexOf(site) === 0 ? 'Primary Port, ' : ''}${site.status},`,
        ),
      });
      expect(tile.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    }
    const order = renderOrder(fixture);
    const resultWords = ['OK', 'Blocked', 'Needs info', 'OK'];
    for (const [index, step] of [
      ...order.container.querySelectorAll('.build-step'),
    ].entries()) {
      expect(step.querySelector('.step-state svg')).toHaveAttribute(
        'aria-hidden',
        'true',
      );
      expect(step.querySelector('.step-state')).toHaveTextContent(
        fixture.model.sites[index].status,
      );
      expect(step.querySelector('.evaluation-value svg')).toHaveAttribute(
        'aria-hidden',
        'true',
      );
      expect(step.querySelector('.evaluation-value')).toHaveTextContent(
        resultWords[index],
      );
    }
    const primary = order.container.querySelector('.build-step')!;
    const protection = within(primary as HTMLElement).getByText(
      PRIMARY_PORT_PROTECTION,
    );
    const disclosure = primary.querySelector('details')!;
    expect(disclosure.contains(protection)).toBe(true);
    expect(protection).not.toBeVisible();
    for (const direction of ['up', 'down']) {
      const control = within(primary as HTMLElement).getByRole('button', {
        name: `Move ${direction} Colony site 1`,
      });
      expect(control).toBeDisabled();
      expect(control).toHaveAttribute('title', PRIMARY_PORT_PROTECTION);
    }
    await fireEvent.click(screen.getByLabelText('Edit Colony site 1'));
    expect(disclosure).toHaveAttribute('open');
    expect(protection).toBeVisible();
  });
});
