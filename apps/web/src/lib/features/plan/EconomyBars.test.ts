import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/svelte';
import { tick } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { EconomyItemV1, EconomyV1, PortEconomyV1 } from '$lib/api';
import EconomyBars from './EconomyBars.svelte';
import { compactPlanFixture } from './__fixtures__/compact-plan';

afterEach(cleanup);

function item(
  kind: EconomyItemV1['kind'],
  value: number,
  ruleIds = ['RB-0010'],
): EconomyItemV1 {
  return {
    kind,
    value,
    base: value,
    modifiers: [],
    floored: false,
    source_site_ids: ['site-2'],
    via_site_id: null,
    rule_ids: ruleIds,
    count: null,
  };
}

function economy(
  name: string,
  strength: number,
  items = [item('own', strength)],
): EconomyV1 {
  return { name, strength, items };
}

function snapshot(siteId: string, economies: EconomyV1[]): PortEconomyV1 {
  return {
    site_id: siteId,
    body_ref: '1',
    role: 'main',
    supports: null,
    economies,
  };
}

function fixture() {
  const fixture = compactPlanFixture(2, 4);
  fixture.bodies.economy_facts_generation_id = fixture.bodies.generation_id;
  fixture.model.sites[2].facility_id = fixture.facilities[0].id;
  fixture.model.sites[3].facility_id = fixture.facilities[0].id;
  fixture.evaluation.economies = {
    now: [
      snapshot('site-1', [
        economy('Extraction', 0.4),
        economy('Industrial', 0.6),
      ]),
      snapshot('site-3', []),
    ],
    after_plan: [
      snapshot('site-1', [
        economy('Extraction', 0.7),
        economy('Industrial', 0.3),
      ]),
      snapshot('site-3', [economy('Agriculture', 1)]),
    ],
    notes: [],
  };
  return { ...fixture, onwhy: vi.fn() };
}

describe('port economy bars', () => {
  it('shows evaluate shares in Now and After plan rows, Primary Port first and remaining ports in build order', () => {
    const data = fixture();
    // Returned snapshot ordering is deliberately different from build order.
    data.evaluation.economies.after_plan.reverse();
    const { container } = render(EconomyBars, data);
    const ports = [...container.querySelectorAll('[data-economy-port]')];
    expect(ports.map((port) => port.getAttribute('data-economy-port'))).toEqual(
      ['site-1', 'site-3', 'site-4'],
    );
    expect(
      within(ports[0] as HTMLElement).getByText('Primary Port'),
    ).toBeVisible();
    expect(within(ports[0] as HTMLElement).getByText('Now')).toBeVisible();
    expect(
      within(ports[0] as HTMLElement).getByText('After plan'),
    ).toBeVisible();
    const before = screen.getByRole('button', {
      name: 'Now: Extraction 40%. Increases by 30 percentage points. Open economy rules.',
    });
    const after = screen.getByRole('button', {
      name: 'After plan: Extraction 70%. Increases by 30 percentage points. Open economy rules.',
    });
    expect(before).toHaveStyle('width: 40%');
    expect(after).toHaveStyle('width: 70%');
    expect(
      screen.getAllByRole('list', { name: /economy legend/ }),
    ).toHaveLength(3);
    expect(container.querySelector('[data-economy-total-note]')).toBeNull();
  });

  it('retains over-100 strengths and uses an explicit shared scale without normalizing values', () => {
    const data = fixture();
    data.evaluation.economies.now = [
      snapshot('site-1', [
        economy('Extraction', 1.5),
        economy('Industrial', 0.75),
      ]),
    ];
    data.evaluation.economies.after_plan = [
      snapshot('site-1', [economy('Extraction', 1.5)]),
    ];
    render(EconomyBars, data);
    expect(
      screen.getByRole('button', {
        name: 'Now: Extraction 150%. Open economy rules.',
      }),
    ).toHaveStyle('width: 66.66666666666666%');
    expect(
      screen.getByRole('button', {
        name: 'After plan: Extraction 150%. Open economy rules.',
      }),
    ).toHaveStyle('width: 66.66666666666666%');
    expect(screen.getByText('Shared bar scale: 0–225%.')).toBeVisible();
    expect(
      screen.getByText(
        'Returned shares total 225%, not 100%. Values are shown as returned, without normalizing.',
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        'Returned shares total 150%, not 100%. Values are shown as returned, without normalizing.',
      ),
    ).toBeVisible();
  });

  it('highlights appeared, disappeared and strictly greater than 5 percentage point changes', () => {
    const data = fixture();
    data.evaluation.economies.now = [
      snapshot('site-1', [
        economy('Extraction', 0.4),
        economy('Industrial', 0.3),
        economy('Tourism', 0.2),
        economy('Military', 0.1),
      ]),
    ];
    data.evaluation.economies.after_plan = [
      snapshot('site-1', [
        economy('Extraction', 0.46),
        economy('Industrial', 0.35),
        economy('Agriculture', 0.1),
        economy('Military', 0.09),
      ]),
    ];
    const { container } = render(EconomyBars, data);
    expect(
      screen.getByRole('button', { name: /Now: Extraction 40%/ }),
    ).toHaveAttribute(
      'data-economy-change',
      'Increases by 6 percentage points',
    );
    expect(
      screen.getByRole('button', { name: /Now: Tourism 20%/ }),
    ).toHaveAttribute('data-economy-change', 'Disappears after plan');
    expect(
      screen.getByRole('button', { name: /After plan: Agriculture 10%/ }),
    ).toHaveAttribute('data-economy-change', 'Appears after plan');
    for (const segment of container.querySelectorAll(
      '[data-economy-name="Industrial"], [data-economy-name="Military"]',
    ))
      expect(segment).not.toHaveAttribute('data-economy-change');
    expect(screen.getByText(/more than 5 percentage points/)).toBeVisible();
  });

  it('shows the unavailable-generation message and evaluate notes without any bars', () => {
    const data = fixture();
    data.bodies.economy_facts_generation_id = null;
    data.evaluation.economies.notes = [
      {
        code: 'MISSING_FACTS',
        site_id: 'site-1',
        body_ref: '1',
        message: 'The reserve level is unknown.',
      },
    ];
    const { container } = render(EconomyBars, data);
    expect(
      screen.getByText(
        "Economy facts unavailable for this system's published generation",
      ),
    ).toBeVisible();
    expect(
      screen.getByRole('list', { name: 'Economy notes' }),
    ).toHaveTextContent('Colony site 1: The reserve level is unknown.');
    expect(container.querySelector('[data-economy-port]')).toBeNull();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('shows No economy for empty or absent returned port snapshots, including planned and demolished ports', () => {
    const { container } = render(EconomyBars, fixture());
    const newPort = container.querySelector('[data-economy-port="site-3"]')!;
    const demolished = container.querySelector('[data-economy-port="site-4"]')!;
    expect(
      within(newPort as HTMLElement).getByText('No economy'),
    ).toBeVisible();
    expect(
      within(demolished as HTMLElement).getAllByText('No economy'),
    ).toHaveLength(2);
    expect(demolished.querySelector('.economy-track')).toBeNull();
  });

  it('announces all five contribution kinds on keyboard focus, preserves returned details, and opens returned rules', async () => {
    const data = fixture();
    const contributions = [
      item('own', 0.4),
      item('strong_link', 0.2, ['RB-0015']),
      item('passed_on_link', 0.1, ['RB-0017']),
      item('planetary_port_link', 0.2, ['RB-0029']),
      item('weak_links', 0.1, ['RB-0020']),
    ];
    contributions[1] = {
      ...contributions[1],
      base: 0.3,
      modifiers: [{ condition: 'reserve_low', delta: -0.1 }],
      floored: true,
      via_site_id: 'site-3',
      count: 2,
    };
    data.evaluation.economies.now = [
      snapshot('site-1', [economy('Extraction', 1, contributions)]),
    ];
    data.evaluation.economies.after_plan = [
      snapshot('site-1', [
        economy('Extraction', 1, [item('own', 1, ['RB-0028', 'RB-0010'])]),
      ]),
    ];
    render(EconomyBars, data);
    const segment = screen.getByRole('button', {
      name: 'Now: Extraction 100%. Open economy rules.',
    });
    expect(segment).not.toHaveAttribute('tabindex', '-1');
    segment.focus();
    await tick();
    expect(segment).toHaveFocus();
    const tooltip = screen.getByRole('tooltip');
    expect(segment).toHaveAttribute('aria-describedby', tooltip.id);
    expect(segment).toHaveAccessibleDescription(
      /Own.*Strong link.*Passed on.*Planetary port.*Weak link/,
    );
    for (const label of [
      'Own',
      'Strong link',
      'Passed on',
      'Planetary port',
      'Weak link',
    ])
      expect(within(tooltip).getByText(label)).toBeVisible();
    expect(tooltip).toHaveTextContent(
      '20% (base 30%) · Modifiers: reserve_low -10% · Minimum applied · Count: 2 · From: Colony site 2 · Via: Colony site 3 · Rules: RB-0015',
    );
    await fireEvent.click(segment);
    expect(data.onwhy).toHaveBeenCalledWith({
      siteId: 'site-1',
      economyName: 'Extraction',
      ruleIds: [
        'RB-0010',
        'RB-0015',
        'RB-0017',
        'RB-0029',
        'RB-0020',
        'RB-0028',
      ],
    });
    await fireEvent.keyDown(segment, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    expect(segment).toHaveFocus();
  });

  it('offers hover breakdowns and explicitly says when a contribution is not returned', async () => {
    render(EconomyBars, fixture());
    const segment = screen.getByRole('button', { name: /Now: Extraction 40%/ });
    await fireEvent.mouseEnter(segment);
    const tooltip = screen.getByRole('tooltip');
    expect(within(tooltip).getAllByText('None returned')).toHaveLength(4);
    await fireEvent.mouseLeave(segment.closest('[data-economy-view]')!);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });

  it('keeps economy colours stable across row order and views, with a fallback for an unknown returned kind', () => {
    const data = fixture();
    data.evaluation.economies.after_plan[0].economies.reverse();
    data.evaluation.economies.after_plan[0].economies.push(
      economy('New returned kind', 0.1),
    );
    const { container } = render(EconomyBars, data);
    const segments = [
      ...container.querySelectorAll<HTMLElement>(
        '[data-economy-name="Extraction"]',
      ),
    ];
    expect(segments[0].style.getPropertyValue('--economy-colour')).toBe(
      segments[1].style.getPropertyValue('--economy-colour'),
    );
    const unknown = screen.getByRole('button', {
      name: /After plan: New returned kind 10%/,
    });
    expect(unknown.style.getPropertyValue('--economy-colour')).toContain(
      '--text-muted',
    );
  });

  it('distinguishes pending and wrong-system results from empty economies and labels stale results', async () => {
    const data = fixture();
    const { rerender } = render(EconomyBars, { ...data, evaluation: null });
    expect(
      screen.getByText(
        'Economy bars are waiting for a successful plan evaluation.',
      ),
    ).toBeVisible();
    expect(screen.queryByText('No economy')).not.toBeInTheDocument();
    await rerender({ evaluation: { ...data.evaluation, system_id64: '99' } });
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    await rerender({ evaluation: data.evaluation, stale: true });
    expect(screen.getByRole('status')).toHaveTextContent(
      'Economy bars may not match the current plan.',
    );
    expect(
      screen.getByRole('button', { name: /Now: Extraction 40%/ }),
    ).toBeVisible();
    await rerender({ model: { ...data.model, sites: [] } });
    expect(screen.getByText('Add a port to see its economies.')).toBeVisible();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
