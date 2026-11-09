import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import axe from 'axe-core';
import { afterEach, describe, expect, it, vi } from 'vitest';
import GalaxySystemHoverCard from './GalaxySystemHoverCard.svelte';
import type { GalaxySystemPoint } from './contracts';

afterEach(cleanup);

const system: GalaxySystemPoint = {
  systemId64: '10477373803',
  name: 'Catalogue G2 star',
  positionLy: { x: 13, y: 24, z: 42 },
  primaryStar: { type: 'G2', subtype: 'G (White-Yellow) Star' },
  // This is a previous query's distance, not the current anchor distance.
  summary: { distanceLy: 999 },
};
const anchor = { name: 'Sol', positionLy: { x: 10, y: 20, z: 30 } };

function card(point = system) {
  const onSelect = vi.fn();
  const onInspect = vi.fn((event: MouseEvent) => event.preventDefault());
  const onDismiss = vi.fn();
  const result = render(GalaxySystemHoverCard, {
    props: {
      system: point,
      anchor,
      onSelect,
      inspectHref: `/inspect?system=${point.systemId64}`,
      onInspect,
      onDismiss,
    },
  });
  return { ...result, onSelect, onInspect, onDismiss };
}

describe('Galaxy system hover card', () => {
  it('shows catalogue class text and distance from the current anchor', () => {
    card();
    const tooltip = screen.getByRole('tooltip', {
      name: 'System Catalogue G2 star',
    });
    expect(tooltip).toHaveAttribute('data-system-id64', system.systemId64);
    expect(screen.getByText('G2', { exact: true })).toBeInTheDocument();
    expect(screen.getByText('G (White-Yellow) Star')).toBeInTheDocument();
    expect(screen.queryByText('G class star')).not.toBeInTheDocument();
    expect(screen.getByText('Distance from Sol')).toBeInTheDocument();
    expect(screen.getByText('13.0 LY')).toBeInTheDocument();
    expect(screen.queryByText('999.0 LY')).not.toBeInTheDocument();
  });

  it('omits unavailable stellar facts and distance without an anchor', () => {
    render(GalaxySystemHoverCard, {
      system: {
        systemId64: '42',
        name: 'Unclassified',
        positionLy: { x: 0, y: 0, z: 0 },
      },
      onSelect: vi.fn(),
      inspectHref: '/inspect?system=42',
    });
    expect(screen.getByText('Unclassified')).toBeInTheDocument();
    expect(screen.queryByText('Star class')).not.toBeInTheDocument();
    expect(screen.queryByText('Star subtype')).not.toBeInTheDocument();
    expect(screen.queryByText(/Distance from/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/unknown|main sequence/i),
    ).not.toBeInTheDocument();
  });

  it('keeps a measured zero distance and updates with the anchor', async () => {
    const { rerender } = card();
    await rerender({
      anchor: { name: system.name, positionLy: system.positionLy },
    });
    expect(screen.getByText('0.0 LY')).toBeInTheDocument();
    await rerender({ anchor: null });
    expect(screen.queryByText(/Distance from/)).not.toBeInTheDocument();
  });

  it('exposes native Select and Inspect actions with the same system identity', async () => {
    const { onSelect, onInspect } = card();
    await fireEvent.click(screen.getByRole('button', { name: 'Select' }));
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(system);
    const inspect = screen.getByRole('link', { name: 'Inspect' });
    expect(inspect).toHaveAttribute(
      'href',
      `/inspect?system=${system.systemId64}`,
    );
    await fireEvent.click(inspect);
    expect(onInspect).toHaveBeenCalledExactlyOnceWith(
      expect.any(MouseEvent),
      system,
    );
  });

  it('does not steal or trap keyboard focus and dismisses on Escape', async () => {
    const { onDismiss } = card();
    const select = screen.getByRole('button', { name: 'Select' });
    const inspect = screen.getByRole('link', { name: 'Inspect' });
    expect(select).not.toHaveFocus();
    select.focus();
    expect(select).toHaveFocus();
    expect(await fireEvent.keyDown(select, { key: 'Tab' })).toBe(true);
    // jsdom leaves native Tab traversal to the browser. There is no key
    // cancellation or tabindex override intercepting that traversal.
    expect(select).not.toHaveAttribute('tabindex');
    expect(inspect).not.toHaveAttribute('tabindex');
    inspect.focus();
    expect(inspect).toHaveFocus();
    await fireEvent.keyDown(inspect, { key: 'Escape' });
    expect(onDismiss).toHaveBeenCalledOnce();
    expect(inspect).toHaveFocus();
  });

  it('has no automated accessibility violations', async () => {
    const { container } = card();
    const result = await axe.run(container, {
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });
});
