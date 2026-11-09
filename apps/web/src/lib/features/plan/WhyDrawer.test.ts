import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import axe from 'axe-core';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PlanEvaluationV1, PlanFacilityV1, PlanStepV1 } from '$lib/api';
import WhyDrawer from './WhyDrawer.svelte';

const step: PlanStepV1 = {
  site_id: 'port-2',
  facility_id: 'coriolis',
  status: 'planned',
  view: 'after_plan',
  result: 'BLOCKED',
  counted_port_number: 2,
  cost_t2: 3,
  cost_t3: 0,
  reward_t2: 0,
  reward_t3: 1,
  t2_before: 1,
  t3_before: 2,
  t2_after: 1,
  t3_after: 2,
  reasons: [
    'This step needs 3 Tier 2 points, but only 1 is available.',
    'No orbital slot is free at this location.',
  ],
  rule_ids: ['RB-0003'],
  payers: ['outpost'],
};

const evaluation: PlanEvaluationV1 = {
  system_id64: '42',
  rulebook_release: 'cre-knowledge-test',
  now: { t2: 1, t3: 2 },
  after_plan: { t2: 1, t3: 2 },
  next_port_cost: { t2: 3, t3: 6 },
  economies: { now: [], after_plan: [], notes: [] },
  steps: [step],
  plan_checks: [],
  summary: { OK: 0, BLOCKED: 1, NEEDS_INFO: 0, COUNTED: 0, IGNORED: 0 },
  explanations: {
    'RB-0003': {
      id: 'RB-0003',
      title: 'Port construction costs',
      status: 'confirmed',
      statement: 'Port costs depend on the counted port number.',
      quote: 'A quoted source passage returned by the API.',
      page: 12,
    },
  },
};

const facility: PlanFacilityV1 = {
  id: 'outpost',
  name: 'Orbital Outpost',
  category: 'orbital',
  tier: 1,
  allowed_location: 'orbital',
  economy: null,
  is_port: false,
  escalating_port_tier: null,
  yellow_cp_cost: 0,
  green_cp_cost: 0,
  yellow_cp_generated: 1,
  green_cp_generated: 0,
  prerequisites: [],
  rule_ids: [],
};

function props() {
  return {
    step,
    index: 1,
    name: 'Second port',
    evaluation,
    facilities: [facility],
    onclose: vi.fn(),
    oninsert: vi.fn(),
  };
}

afterEach(() => {
  cleanup();
  document.body.style.overflow = '';
});

describe('plan step Why drawer', () => {
  it('filters an economy disclosure to returned rule IDs without step details', () => {
    render(WhyDrawer, {
      name: 'Primary port',
      economy: { name: 'Extraction', ruleIds: ['RB-0009', 'RB-0027'] },
      evaluation: {
        ...evaluation,
        explanations: {
          ...evaluation.explanations,
          'RB-0009': {
            id: 'RB-0009',
            title: 'Port economies',
            status: 'confirmed',
            statement: 'Returned economy rule statement.',
            quote: 'Returned economy rule quotation.',
            page: 9,
          },
        },
      },
      onclose: vi.fn(),
    });
    const dialog = screen.getByRole('dialog', {
      name: 'Primary port · Extraction',
    });
    expect(within(dialog).getByText('Why this economy?')).toBeInTheDocument();
    expect(
      within(dialog).getByText('RB-0009 · Port economies'),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText('Returned economy rule quotation.'),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        'RB-0027: explanation not supplied by the evaluation.',
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByText('RB-0003 · Port construction costs'),
    ).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('table')).not.toBeInTheDocument();
    expect(
      within(dialog).queryByText('Payer suggestions'),
    ).not.toBeInTheDocument();
  });

  it('moves focus into the drawer and contains forward and reverse Tab', async () => {
    render(WhyDrawer, props());
    const dialog = screen.getByRole('dialog', { name: 'Step 2: Second port' });
    const close = screen.getByRole('button', { name: 'Close why panel' });
    const insert = screen.getByRole('button', {
      name: 'Add Orbital Outpost before this step',
    });
    await waitFor(() => expect(dialog).toHaveFocus());
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(document.body.style.overflow).toBe('hidden');
    await fireEvent.keyDown(window, { key: 'Tab' });
    expect(close).toHaveFocus();
    await fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(insert).toHaveFocus();
    await fireEvent.keyDown(window, { key: 'Tab' });
    expect(close).toHaveFocus();
  });

  it.each(['Escape', 'Close'])(
    'closes with %s and restores the opener once',
    async (action) => {
      const opener = document.createElement('button');
      opener.textContent = 'Why?';
      document.body.append(opener);
      opener.focus();
      const focus = vi.spyOn(opener, 'focus');
      const input = props();
      const { unmount } = render(WhyDrawer, input);
      await waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus());
      if (action === 'Escape') {
        await fireEvent.keyDown(window, { key: 'Escape' });
      } else {
        await fireEvent.click(
          screen.getByRole('button', { name: 'Close why panel' }),
        );
      }
      expect(input.onclose).toHaveBeenCalledOnce();
      expect(opener).toHaveFocus();
      unmount();
      expect(focus).toHaveBeenCalledOnce();
      expect(focus).toHaveBeenCalledWith({ preventScroll: true });
      expect(document.body.style.overflow).toBe('');
      opener.remove();
    },
  );

  it('restores focus and scrolling when navigation removes the drawer', async () => {
    document.body.style.overflow = 'scroll';
    const opener = document.createElement('button');
    document.body.append(opener);
    opener.focus();
    const { unmount } = render(WhyDrawer, props());
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus());
    unmount();
    expect(opener).toHaveFocus();
    expect(document.body.style.overflow).toBe('scroll');
    opener.remove();
  });

  it('shows every API reason, point value, port number and rule explanation', () => {
    render(WhyDrawer, props());
    expect(screen.getByText('✕ Blocked')).toBeInTheDocument();
    for (const reason of step.reasons) {
      expect(screen.getByText(reason)).toBeInTheDocument();
    }
    for (const [label, t2, t3] of [
      ['Before', 1, 2],
      ['Cost', 3, 0],
      ['After', 1, 2],
      ['Earns', 0, 1],
    ]) {
      const cells = within(
        screen.getByRole('row', { name: `${label} ${t2} ${t3}` }),
      ).getAllByRole('cell');
      expect(cells.map((cell) => cell.textContent)).toEqual([
        String(t2),
        String(t3),
      ]);
    }
    expect(screen.getByText('Port #2')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', {
        name: 'RB-0003 · Port construction costs',
      }),
    ).toBeInTheDocument();
    expect(screen.getByText('Status: confirmed')).toBeInTheDocument();
    expect(
      screen.getByText(evaluation.explanations['RB-0003'].statement),
    ).toBeInTheDocument();
    expect(
      screen.getByText(evaluation.explanations['RB-0003'].quote),
    ).toBeInTheDocument();
    expect(screen.getByText('Page 12')).toBeInTheDocument();
  });

  it('offers API payer IDs and disables insertion at the site limit', async () => {
    const input = props();
    const { rerender } = render(WhyDrawer, input);
    const insert = screen.getByRole('button', {
      name: 'Add Orbital Outpost before this step',
    });
    await fireEvent.click(insert);
    expect(input.oninsert).toHaveBeenCalledWith('outpost');
    await rerender({ canInsert: false });
    expect(insert).toBeDisabled();
    expect(screen.getByText(/plan has 300 sites/)).toBeInTheDocument();
  });

  it('translates API reason codes and marks an outdated evaluation', () => {
    render(WhyDrawer, {
      ...props(),
      step: {
        ...step,
        reasons: [
          'INSUFFICIENT_T2',
          'PREREQUISITE_MISSING',
          'SLOTS_UNKNOWN',
          'FUTURE_RULE_REASON',
        ],
      },
      stale: true,
      canInsert: false,
    });
    expect(
      screen.getByText(
        'There are not enough Tier 2 construction points for this step.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'A required facility must be completed earlier in the build order.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Enter the available slot count for this location.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Future rule reason.')).toBeInTheDocument();
    expect(screen.getByText(/Last good evaluation/)).toBeInTheDocument();
    expect(screen.getByRole('table')).toHaveClass('stale');
  });

  it('has no automated accessibility violations', async () => {
    const { container } = render(WhyDrawer, props());
    const result = await axe.run(container, {
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });
});
