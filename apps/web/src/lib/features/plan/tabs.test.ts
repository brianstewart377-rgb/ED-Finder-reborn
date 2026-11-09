import { describe, expect, it } from 'vitest';
import { planTabUrl, readPlanTab } from './tabs';

describe('plan task URL state', () => {
  it.each([
    '',
    'tab=system-layout',
    'tab=unknown',
    'tab=',
    'tab=BUILD-ORDER',
    'tab=build-order&tab=system-layout',
    `tab=${'x'.repeat(10000)}`,
  ])('ignores unsupported or ambiguous task state: %.60s', (search) => {
    expect(readPlanTab(new URLSearchParams(search))).toBe('system-layout');
  });

  it('round-trips both tasks while retaining the system and unrelated URL context', () => {
    const source = new URL(
      'https://example.test/plan?system=42&from=inspect&tab=invalid#details',
    );
    const order = planTabUrl(source, 'build-order');
    expect(readPlanTab(order.searchParams)).toBe('build-order');
    expect(order.searchParams.getAll('tab')).toEqual(['build-order']);
    expect(order.searchParams.get('system')).toBe('42');
    expect(order.searchParams.get('from')).toBe('inspect');
    expect(order.hash).toBe('#details');
    const layout = planTabUrl(order, 'system-layout');
    expect(readPlanTab(layout.searchParams)).toBe('system-layout');
    expect(layout.searchParams.has('tab')).toBe(false);
    expect(source.searchParams.get('tab')).toBe('invalid');
  });
});
