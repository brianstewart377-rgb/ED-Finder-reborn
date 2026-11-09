import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ReviewExport from './ReviewExport.svelte';
import { compactPlanFixture } from './__fixtures__/compact-plan';
import { resolvePlanLocations } from './locations';

afterEach(() => {
  cleanup();
  document
    .querySelectorAll('[data-review-test-trigger]')
    .forEach((el) => el.remove());
});

function fixture() {
  const fixture = compactPlanFixture(3, 3);
  fixture.model.slots['1'].orbital_slots = null;
  fixture.model.slots['2'].surface_slots = null;
  Object.assign(fixture.bodies.bodies[1], {
    predicted_surface_slots: 5,
    prediction_status: 'predicted',
    prediction_version: 'surface-slots-v1',
  });
  Object.assign(fixture.bodies.bodies[2], {
    prediction_status: 'unknown',
    missing_inputs: ['atmosphere'],
    prediction_reason: 'Atmosphere data is missing.',
  });
  fixture.evaluation.steps[1].reasons = ['Not enough Tier 2 points.'];
  fixture.evaluation.steps[1].rule_ids = ['RB-0012'];
  fixture.evaluation.steps[2].reasons = ['Slot count is unknown.'];
  fixture.evaluation.now = { t2: -3, t3: 9 };
  fixture.evaluation.after_plan = { t2: 14, t3: -2 };
  return fixture;
}

function props() {
  const data = fixture();
  const trigger = document.createElement('button');
  trigger.textContent = 'Review & export';
  trigger.dataset.reviewTestTrigger = '';
  document.body.append(trigger);
  return {
    ...data,
    locations: resolvePlanLocations(data.bodies),
    open: true,
    stale: false,
    pending: false,
    error: '',
    trigger,
    onclose: vi.fn(),
    onexport: vi.fn(),
    onstep: vi.fn(),
    onbody: vi.fn(),
  };
}

describe('Review & export', () => {
  it('opens as a non-modal tool, focuses it, and returns focus on Close', async () => {
    const input = props();
    const { rerender } = render(ReviewExport, { ...input, open: false });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    input.trigger.focus();
    await rerender({ open: true });
    const dialog = screen.getByRole('dialog', { name: 'Review & export' });
    expect(dialog).toHaveAttribute('aria-modal', 'false');
    await waitFor(() => expect(dialog).toHaveFocus());
    await fireEvent.click(
      within(dialog).getByRole('button', { name: 'Close' }),
    );
    expect(input.onclose).toHaveBeenCalledOnce();
    expect(input.trigger).toHaveFocus();
    await rerender({ open: false });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('closes only the topmost tool with Escape and returns its opener focus', async () => {
    const bottom = props();
    render(ReviewExport, bottom);
    const top = props();
    const { rerender } = render(ReviewExport, top);
    await waitFor(() => expect(screen.getAllByRole('dialog')[1]).toHaveFocus());

    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(top.onclose).toHaveBeenCalledOnce();
    expect(bottom.onclose).not.toHaveBeenCalled();
    expect(top.trigger).toHaveFocus();
    await rerender({ open: false });
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(bottom.onclose).toHaveBeenCalledOnce();
    expect(bottom.trigger).toHaveFocus();
  });

  it('lists each reported unresolved kind and routes each item to its editor or build', async () => {
    const input = props();
    const original = structuredClone({
      model: input.model,
      evaluation: input.evaluation,
    });
    const { container } = render(ReviewExport, input);
    const unknowns = container.querySelectorAll<HTMLButtonElement>(
      '[data-review-kind="unknown-slot"]',
    );
    // The star and both belt orbital totals are unknown; a star/belt surface
    // is unavailable, while the prediction and entered totals stay distinct.
    expect(unknowns).toHaveLength(3);
    for (const button of unknowns) {
      await fireEvent.click(button);
      expect(input.onbody).toHaveBeenLastCalledWith(
        button.dataset.bodyRef,
        button.dataset.slotKind,
      );
    }

    const missing = screen.getByRole('button', {
      name: 'Missing body data: Compact Test System A 2',
    });
    await fireEvent.click(missing);
    expect(input.onbody).toHaveBeenLastCalledWith('3');
    expect(screen.getByText('Missing inputs: atmosphere')).toBeVisible();
    expect(screen.getByText('Atmosphere data is missing.')).toBeVisible();

    await fireEvent.click(
      screen.getByRole('button', {
        name: 'Prediction-only surface slots: Compact Test System A 1',
      }),
    );
    expect(input.onbody).toHaveBeenLastCalledWith('2', 'surface');
    expect(
      screen.getByText(
        'Source: predicted · surface-slots-v1. Verify in Architect Mode.',
      ),
    ).toBeVisible();

    await fireEvent.click(
      screen.getByRole('button', {
        name: 'Blocked build: Colony site 2 (step 2)',
      }),
    );
    expect(input.onstep).toHaveBeenLastCalledWith('site-2');
    expect(screen.getByText('Not enough Tier 2 points.')).toBeVisible();
    expect(screen.getByText('Rules: RB-0012')).toBeVisible();
    await fireEvent.click(
      screen.getByRole('button', {
        name: 'Build needs information: Colony site 3 (step 3)',
      }),
    );
    expect(input.onstep).toHaveBeenLastCalledWith('site-3');

    expect(input.onclose).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeVisible();
    expect({ model: input.model, evaluation: input.evaluation }).toEqual(
      original,
    );
  });

  it('updates the current unresolved list after a slot total is entered', async () => {
    const input = props();
    const { rerender } = render(ReviewExport, input);
    expect(
      screen.getByRole('button', { name: /Prediction-only surface slots/ }),
    ).toBeVisible();
    await rerender({
      model: {
        ...input.model,
        slots: {
          ...input.model.slots,
          '2': { orbital_slots: 2, surface_slots: 5 },
        },
      },
    });
    expect(
      screen.queryByRole('button', { name: /Prediction-only surface slots/ }),
    ).not.toBeInTheDocument();
  });

  it('summarises the unresolved count in a banner whose button moves to the first listed item', async () => {
    const input = props();
    const { container } = render(ReviewExport, input);
    // The tool takes focus on opening; the jump happens after that.
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus());
    const heading = screen.getByRole('heading', {
      name: /^Unresolved information \((\d+)\)$/,
    });
    const count = Number(/\((\d+)\)/.exec(heading.textContent ?? '')?.[1]);
    expect(count).toBeGreaterThan(1);
    const banner = container.querySelector('[data-review-banner]');
    expect(banner).toHaveTextContent(`${count} unresolved items`);
    const first = container.querySelector<HTMLButtonElement>(
      '.review-unresolved-list button:not(:disabled)',
    );
    await fireEvent.click(
      within(banner as HTMLElement).getByRole('button', {
        name: 'Go to first item',
      }),
    );
    expect(first).toHaveFocus();
    // Only moving focus: nothing is opened or changed until the item is used.
    expect(input.onbody).not.toHaveBeenCalled();
    expect(input.onstep).not.toHaveBeenCalled();
  });

  it('shows no banner when nothing is listed, and no jump button while editors are unavailable', () => {
    const empty = props();
    const first = render(ReviewExport, {
      ...empty,
      bodies: undefined,
      locations: [],
      evaluation: null,
    });
    expect(first.container.querySelector('[data-review-banner]')).toBeNull();
    first.unmount();

    const offline = props();
    const second = render(ReviewExport, { ...offline, canNavigate: false });
    expect(
      second.container.querySelector('[data-review-banner]'),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Go to first item' }),
    ).not.toBeInTheDocument();
  });

  it('keeps the system, item counts and separate Now/After Tier 2 and Tier 3 balances', () => {
    render(ReviewExport, props());
    expect(screen.getByText('Compact Test System')).toBeVisible();
    expect(screen.getByText('3 plan items')).toBeVisible();
    const counts = screen.getByRole('list', { name: 'Plan item counts' });
    expect(counts).toHaveTextContent('1 planned');
    expect(counts).toHaveTextContent('1 building');
    expect(counts).toHaveTextContent('1 complete');
    expect(counts).toHaveTextContent('0 demolished');
    for (const [name, t2, t3] of [
      ['Now', -3, 9],
      ['After the plan', 14, -2],
    ] as const) {
      const balances = screen.getByRole('region', { name });
      expect(balances.querySelectorAll('dt')).toHaveLength(2);
      for (const [tier, value] of [
        ['Tier 2', t2],
        ['Tier 3', t3],
      ] as const) {
        const term = within(balances).getByText(tier);
        expect(term.nextElementSibling).toHaveTextContent(
          new RegExp(`^${value}${value < 0 ? 'Deficit' : ''}$`),
        );
        expect(term.parentElement?.classList.contains('negative')).toBe(
          value < 0,
        );
      }
    }
    expect(screen.getByText('Next Tier 2 port cost')).toBeVisible();
    expect(screen.getByText('Next Tier 3 port cost')).toBeVisible();
    expect(screen.getByText('Rulebook: compact-layout-test')).toBeVisible();
  });

  it('keeps pending, failed and last-good evaluation context without eligibility wording', () => {
    const input = props();
    render(ReviewExport, {
      ...input,
      pending: true,
      stale: true,
      error: 'Check data could not be refreshed.',
    });
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('Evaluating plan…');
    expect(dialog).toHaveTextContent(
      'Last good evaluation — awaiting a valid current result.',
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Check data could not be refreshed.',
    );
    expect(dialog.textContent).not.toMatch(
      /eligib|approved|ready to build|safe to build/i,
    );
  });

  it('keeps review open when interacting outside and labels the download explicitly DRAFT', async () => {
    const input = props();
    render(ReviewExport, input);
    const download = screen.getByRole('button', { name: 'Export DRAFT plan' });
    expect(download).toHaveAttribute('aria-label', 'Export DRAFT plan');
    expect(download).toHaveTextContent('Export DRAFT plan');
    await fireEvent.click(download);
    expect(input.onexport).toHaveBeenCalledOnce();
    await fireEvent.click(document.body);
    expect(input.onclose).not.toHaveBeenCalled();
  });

  it('does not show removed builds from a last-good evaluation', () => {
    const input = props();
    input.model.sites = [input.model.sites[0]];
    render(ReviewExport, { ...input, stale: true });
    expect(
      screen.queryByRole('button', {
        name: /Blocked build|Build needs information/,
      }),
    ).not.toBeInTheDocument();
  });

  it('shows reported missing body facts and unknown prediction reasons without inferring from null fields', async () => {
    const input = props();
    input.bodies.bodies[2].missing_inputs = [];
    input.evaluation.economies.notes = [
      {
        code: 'unknown_body_kind',
        body_ref: '2',
        site_id: null,
        message: 'Body kind is unknown; no inherited own economies.',
      },
      {
        code: 'floor_applied',
        body_ref: '1',
        site_id: 'site-1',
        message: 'Rulebook minimum applied.',
      },
    ];
    render(ReviewExport, input);
    await fireEvent.click(
      screen.getByRole('button', {
        name: 'Missing body data: Compact Test System A 1',
      }),
    );
    expect(input.onbody).toHaveBeenLastCalledWith('2');
    expect(
      screen.getByText('Body kind is unknown; no inherited own economies.'),
    ).toBeVisible();
    expect(
      screen.getByRole('button', {
        name: 'Missing body data: Compact Test System A 2',
      }),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', {
        name: 'Missing body data: Compact Test System A',
      }),
    ).not.toBeInTheDocument();
  });

  it('explains unavailable body data and evaluation instead of implying the review is complete', () => {
    const input = props();
    render(ReviewExport, {
      ...input,
      bodies: undefined,
      locations: [],
      evaluation: null,
    });
    expect(
      screen.getByText(
        'System body data is unavailable; body and slot information cannot be reviewed yet.',
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        'The API evaluation will show your construction points here.',
      ),
    ).toBeVisible();
    expect(
      screen.queryByText(
        'No unresolved information is listed in the current data.',
      ),
    ).not.toBeInTheDocument();
  });

  it('links every current invalid build and preserves each reported issue without inventing a target for global issues', async () => {
    const input = props();
    render(ReviewExport, {
      ...input,
      evaluation: null,
      error: 'The current plan has invalid items.',
      issues: [
        {
          site_id: 'site-2',
          code: 'UNKNOWN_BODY',
          message: 'Site body reference must name a body in this plan.',
        },
        {
          site_id: 'site-2',
          code: 'UNKNOWN_FACILITY',
          message: 'Facility id must be in the planner catalogue.',
        },
        {
          site_id: 'site-3',
          code: 'PRIMARY_ORDER',
          message: 'This site cannot be placed here.',
        },
        {
          site_id: 'removed-site',
          code: 'UNKNOWN_FACILITY',
          message: 'No longer in the plan.',
        },
        { site_id: null, code: 'BODY_COUNT', message: 'Too many bodies.' },
      ],
    });
    const builds = screen.getAllByRole('button', { name: /^Invalid build:/ });
    expect(builds).toHaveLength(2);
    await fireEvent.click(builds[0]);
    expect(input.onstep).toHaveBeenLastCalledWith('site-2');
    await fireEvent.click(builds[1]);
    expect(input.onstep).toHaveBeenLastCalledWith('site-3');
    expect(
      screen.getByText(
        'Site body reference must name a body in this plan. (UNKNOWN_BODY)',
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        'Facility id must be in the planner catalogue. (UNKNOWN_FACILITY)',
      ),
    ).toBeVisible();
    expect(screen.queryByText(/No longer in the plan/)).not.toBeInTheDocument();
    expect(input.onclose).not.toHaveBeenCalled();
  });

  it('disables every unresolved target while editors are unavailable, retaining DRAFT export', async () => {
    const input = props();
    const { container } = render(ReviewExport, {
      ...input,
      canNavigate: false,
      issues: [
        {
          site_id: 'site-2',
          code: 'UNKNOWN_FACILITY',
          message: 'Facility id must be in the planner catalogue.',
        },
      ],
    });
    const targets = container.querySelectorAll<HTMLButtonElement>(
      'button[data-review-kind]',
    );
    expect(
      new Set([...targets].map((target) => target.dataset.reviewKind)),
    ).toEqual(
      new Set([
        'unknown-slot',
        'missing-data',
        'prediction',
        'build',
        'invalid-build',
      ]),
    );
    for (const target of targets) expect(target).toBeDisabled();
    expect(
      screen.getByText(
        'Body and build editors are unavailable until planner data loads successfully.',
      ),
    ).toBeVisible();
    const download = screen.getByRole('button', { name: 'Export DRAFT plan' });
    expect(download).toBeEnabled();
    await fireEvent.click(download);
    expect(input.onexport).toHaveBeenCalledOnce();
    expect(input.onbody).not.toHaveBeenCalled();
    expect(input.onstep).not.toHaveBeenCalled();
  });

  it('offers Overview, Build order and Export tabs with the arrow keys, starting on Overview', async () => {
    render(ReviewExport, props());
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      'Overview',
      'Build order',
      'Export',
    ]);
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByRole('tablist')).toHaveAccessibleName('Review sections');
    // Only the selected tab is in the tab order.
    expect(tabs.map((tab) => tab.getAttribute('tabindex'))).toEqual([
      '0',
      '-1',
      '-1',
    ]);

    // The pop-out moves focus to itself when it opens; wait for that first.
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus());
    tabs[0].focus();
    await fireEvent.keyDown(tabs[0], { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: 'Build order' })).toHaveFocus();
    expect(screen.getByRole('tab', { name: 'Build order' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await fireEvent.keyDown(document.activeElement as Element, {
      key: 'End',
    });
    expect(screen.getByRole('tab', { name: 'Export' })).toHaveFocus();
    await fireEvent.keyDown(document.activeElement as Element, {
      key: 'ArrowRight',
    });
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveFocus();
    await fireEvent.keyDown(document.activeElement as Element, {
      key: 'ArrowLeft',
    });
    expect(screen.getByRole('tab', { name: 'Export' })).toHaveFocus();
    await fireEvent.keyDown(document.activeElement as Element, {
      key: 'Home',
    });
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveFocus();
  });

  it('shows only the selected tab panel and keeps Export DRAFT plan in the footer on every tab', async () => {
    render(ReviewExport, props());
    const panels = ['overview', 'build-order', 'export'].map(
      (id) => document.getElementById(`plan-review-panel-${id}`) as HTMLElement,
    );
    for (const [index, name] of [
      'Overview',
      'Build order',
      'Export',
    ].entries()) {
      await fireEvent.click(screen.getByRole('tab', { name }));
      panels.forEach((panel, panelIndex) => {
        expect(panel.hidden).toBe(panelIndex !== index);
      });
      expect(screen.getByRole('tabpanel', { name })).toBe(panels[index]);
      expect(
        screen.getByRole('button', { name: 'Export DRAFT plan' }),
      ).toBeEnabled();
    }
  });

  it('lists the builds in order with location, status and check result', async () => {
    render(ReviewExport, props());
    await fireEvent.click(screen.getByRole('tab', { name: 'Build order' }));
    // The table can scroll sideways on a narrow screen, so keyboard users must
    // be able to reach it.
    expect(
      screen.getByRole('region', { name: 'Planned builds table' }),
    ).toHaveAttribute('tabindex', '0');
    const table = screen.getByRole('table', {
      name: /Planned builds in order/,
    });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getAllByRole('cell')[0]).toHaveTextContent('1');
    expect(within(rows[0]).getByRole('rowheader')).toHaveTextContent(
      'Colony site 1',
    );
    expect(rows[0]).toHaveTextContent(/complete/);
    expect(rows[1]).toHaveTextContent(/building/);
    expect(rows[2]).toHaveTextContent(/planned/);
    const results = rows.map((row) => within(row).getAllByRole('cell')[3]);
    expect(results[0]).toHaveTextContent('OK');
    expect(results[1]).toHaveTextContent('Blocked');
    expect(results[2]).toHaveTextContent('Needs info');
  });

  it('says a build has not been evaluated instead of guessing a check result', async () => {
    render(ReviewExport, { ...props(), evaluation: null });
    await fireEvent.click(screen.getByRole('tab', { name: 'Build order' }));
    const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1);
    for (const row of rows) expect(row).toHaveTextContent('Not evaluated');
  });

  it('shows an empty note instead of a table when the plan has no builds', async () => {
    const input = props();
    render(ReviewExport, {
      ...input,
      model: { ...input.model, sites: [] },
    });
    await fireEvent.click(screen.getByRole('tab', { name: 'Build order' }));
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.getByText('No builds are in this plan yet.')).toBeVisible();
  });

  it('explains what the export file holds and leaves out, without eligibility wording', async () => {
    render(ReviewExport, props());
    await fireEvent.click(screen.getByRole('tab', { name: 'Export' }));
    const panel = screen.getByRole('tabpanel', { name: 'Export' });
    expect(
      within(panel).getByRole('heading', { name: 'Before you export' }),
    ).toBeVisible();
    expect(panel).toHaveTextContent('stays in this browser');
    expect(panel).toHaveTextContent('system ID64');
    expect(panel).toHaveTextContent('slot counts you have entered');
    expect(panel).toHaveTextContent('leaves out the catalogue facts');
    expect(panel).toHaveTextContent('colony-plan-DRAFT-42.json');
    expect(panel).toHaveTextContent('Import plan');
    expect(panel.textContent).not.toMatch(/eligible|approved|ready to build/i);
  });

  it('returns to the Overview when the pop-out is closed and opened again', async () => {
    const input = props();
    const { rerender } = render(ReviewExport, input);
    await fireEvent.click(screen.getByRole('tab', { name: 'Export' }));
    expect(screen.getByRole('tab', { name: 'Export' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await rerender({ open: false });
    await rerender({ open: true });
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('jumps back to the Overview before focusing the first unresolved item', async () => {
    render(ReviewExport, props());
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus());
    await fireEvent.click(screen.getByRole('tab', { name: 'Export' }));
    await fireEvent.click(
      screen.getByRole('button', { name: 'Go to first item' }),
    );
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute(
        'aria-selected',
        'true',
      ),
    );
    await waitFor(() =>
      expect(document.activeElement).toHaveAttribute('data-review-kind'),
    );
  });
});
