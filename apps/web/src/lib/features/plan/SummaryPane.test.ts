import { cleanup, render, screen, within } from '@testing-library/svelte';
import { afterEach, expect, it, vi } from 'vitest';
import SummaryPane from './SummaryPane.svelte';
import { compactPlanFixture } from './__fixtures__/compact-plan';
import { resolvePlanLocations } from './locations';

afterEach(cleanup);

it('keeps initial evaluation guidance readable before any result exists', () => {
  const fixture = compactPlanFixture(2, 0);
  render(SummaryPane, {
    model: fixture.model,
    bodies: fixture.bodies,
    facilities: fixture.facilities,
    locations: resolvePlanLocations(fixture.bodies),
    evaluation: null,
    view: 'now',
    error: '',
    pending: false,
    stale: true,
    onstep: vi.fn(),
    onbody: vi.fn(),
    onwhy: vi.fn(),
  });
  const guidance = screen.getByText(
    'The API evaluation will show your construction points here.',
  );
  expect(guidance).toBeVisible();
  expect(guidance.closest('.stale')).toBeNull();
});

it('keeps the API Tier 2 and Tier 3 balances separate in Now and After the plan', async () => {
  const fixture = compactPlanFixture(2, 4);
  fixture.evaluation.now = { t2: -3, t3: 9 };
  fixture.evaluation.after_plan = { t2: 14, t3: -2 };
  const original = structuredClone(fixture);
  const { container, rerender } = render(SummaryPane, {
    model: fixture.model,
    bodies: fixture.bodies,
    facilities: fixture.facilities,
    locations: resolvePlanLocations(fixture.bodies),
    evaluation: fixture.evaluation,
    view: 'now',
    error: '',
    pending: false,
    stale: false,
    onstep: vi.fn(),
    onbody: vi.fn(),
    onwhy: vi.fn(),
  });
  const balances = container.querySelector('.plan-totals')!;
  function expectBalance(tier: string, value: number) {
    const label = within(balances as HTMLElement).getByText(tier);
    expect(label.nextElementSibling).toHaveTextContent(
      new RegExp(`^${value}${value < 0 ? 'Deficit' : ''}$`),
    );
    expect(label.parentElement?.classList.contains('negative')).toBe(value < 0);
  }

  expect(screen.getByText('Now')).toBeVisible();
  expect(balances.querySelectorAll('dt')).toHaveLength(2);
  expectBalance('Tier 2', -3);
  expectBalance('Tier 3', 9);
  await rerender({ view: 'after_plan' });
  expect(screen.getByText('After the plan')).toBeVisible();
  expect(balances.querySelectorAll('dt')).toHaveLength(2);
  expectBalance('Tier 2', 14);
  expectBalance('Tier 3', -2);
  await rerender({ view: 'now' });
  expectBalance('Tier 2', -3);
  expectBalance('Tier 3', 9);
  expect(fixture).toEqual(original);
});

it('uses a retired former belt cluster alias to find known slot totals', async () => {
  const fixture = compactPlanFixture(2, 1);
  const belt = fixture.bodies.belts[0];
  fixture.bodies.bodies.push(
    {
      ...fixture.bodies.bodies[0],
      body_id: 'old-cluster',
      name: `${belt.name} Cluster 1`,
      body_type: 'belt cluster',
      lifecycle_state: 'RETIRED',
    },
    {
      ...fixture.bodies.bodies[0],
      body_id: 'current-cluster',
      name: `${belt.name} Cluster 2`,
      body_type: 'belt cluster',
    },
  );
  fixture.model.sites[0].body_ref = 'current-cluster';
  fixture.model.slots = {
    'old-cluster': { orbital_slots: 2, surface_slots: null },
  };
  const { rerender } = render(SummaryPane, {
    model: fixture.model,
    bodies: fixture.bodies,
    facilities: fixture.facilities,
    locations: resolvePlanLocations(fixture.bodies),
    evaluation: null,
    view: 'now',
    error: '',
    pending: false,
    stale: false,
    onstep: vi.fn(),
    onbody: vi.fn(),
    onwhy: vi.fn(),
  });
  expect(
    screen.queryByRole('button', { name: /unknown slot counts/ }),
  ).not.toBeInTheDocument();
  expect(screen.getByText('No items need attention.')).toBeVisible();

  await rerender({ model: { ...fixture.model, slots: {} } });
  expect(
    screen.getByRole('button', {
      name: '1 body has builds with unknown slot counts',
    }),
  ).toBeVisible();
});
