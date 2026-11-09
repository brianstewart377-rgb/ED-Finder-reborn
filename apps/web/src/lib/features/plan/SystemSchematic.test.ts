import { cleanup, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it } from 'vitest';
import SystemSchematic from './SystemSchematic.svelte';
import type { SchematicItem } from './schematic';

afterEach(cleanup);

function item(overrides: Partial<SchematicItem>): SchematicItem {
  return {
    body_ref: '1',
    name: 'Tavgi',
    kind: 'body',
    radius_km: 3000,
    moons: 0,
    builds: 0,
    marker: null,
    primary: false,
    ...overrides,
  };
}

const items: SchematicItem[] = [
  item({ body_ref: 'star', name: 'Tavgi', kind: 'star', radius_km: 400000 }),
  item({
    body_ref: '1',
    name: 'Tavgi 1',
    builds: 1,
    marker: 'Primary Port',
    primary: true,
    moons: 2,
  }),
  item({ body_ref: '2', name: 'Tavgi 2', builds: 2, marker: '2 builds' }),
  item({ body_ref: 'belt:1', name: 'Tavgi A Belt', kind: 'belt' }),
];

describe('SystemSchematic', () => {
  it('is one labelled picture that says it is not to scale and lists bodies and builds', () => {
    render(SystemSchematic, { items });
    const picture = screen.getByRole('img');
    expect(picture).toHaveAccessibleName(
      'System layout schematic, not to scale: Tavgi, Tavgi 1 (+2 moons), Tavgi 2, Tavgi A Belt. Builds in the plan: Primary Port on Tavgi 1; 2 builds on Tavgi 2.',
    );
    expect(screen.getByText(/schematic not to scale/)).toBeVisible();
    // A long system scrolls sideways, so keyboard users must be able to reach it.
    expect(picture).toHaveAttribute('tabindex', '0');
  });

  it('draws every body in order with a marker only where builds are planned', () => {
    const { container } = render(SystemSchematic, {
      items,
    });
    const drawn = [
      ...container.querySelectorAll<HTMLElement>('[data-schematic-body]'),
    ];
    expect(drawn.map((el) => el.dataset.schematicBody)).toEqual([
      'star',
      '1',
      '2',
      'belt:1',
    ]);
    const markers = drawn.map((el) =>
      el.querySelector('[data-schematic-marker]')?.textContent?.trim(),
    );
    expect(markers).toEqual(['', 'Primary Port', '2 builds', '']);
    expect(drawn[1]).toHaveTextContent('+2 moons');
  });

  it('highlights the selected body and nothing else', () => {
    const { container } = render(SystemSchematic, {
      items,
      selectedRef: '2',
    });
    const selected = container.querySelectorAll('.schematic-item.selected');
    expect(selected).toHaveLength(1);
    expect((selected[0] as HTMLElement).dataset.schematicBody).toBe('2');
  });

  it('says so when nothing is planned', () => {
    render(SystemSchematic, {
      items: [items[0], item({ body_ref: '1', name: 'Tavgi 1' })],
    });
    expect(screen.getByRole('img')).toHaveAccessibleName(
      'System layout schematic, not to scale: Tavgi, Tavgi 1. No builds are planned on these bodies yet.',
    );
  });
});
