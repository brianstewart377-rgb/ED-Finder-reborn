import { describe, expect, it } from 'vitest';
import { drawnRadius, layoutOf, type BodyItem } from './layout';
import {
  lhs3447WithOlderPlan,
  nodeNamed,
  treeOf,
  type RecordedName,
} from './recorded-systems';

const SYSTEMS: RecordedName[] = ['sol', 'lhs3447', 'polaris', 'altair'];

function bodiesOf(name: RecordedName): BodyItem[] {
  return layoutOf(treeOf(name)).items.filter(
    (item): item is BodyItem => item.kind === 'body',
  );
}

function at(items: readonly BodyItem[], name: string): BodyItem {
  const found = items.find((item) => item.node.name === name);
  if (!found) throw new Error(`${name} is not drawn`);
  return found;
}

describe('where everything goes', () => {
  it.each(SYSTEMS)(
    'draws every body of %s once, except pairs, which are drawn as their members',
    (name) => {
      const tree = treeOf(name);
      const drawn = bodiesOf(name).map((item) => item.id);
      const expected = [...tree.nodes.values()]
        .filter((node) => node.type !== 'barycentre')
        .map((node) => node.id);
      expect(new Set(drawn).size).toBe(drawn.length);
      expect([...drawn].sort()).toEqual([...expected].sort());
    },
  );

  it.each(SYSTEMS)('lets no two bodies of %s overlap', (name) => {
    const items = bodiesOf(name);
    for (const [index, one] of items.entries())
      for (const other of items.slice(index + 1)) {
        const apart = Math.hypot(one.x - other.x, one.y - other.y);
        expect(
          apart,
          `${one.node.name} and ${other.node.name}`,
        ).toBeGreaterThanOrEqual(one.r + other.r);
      }
  });

  it('runs planets to the right of their star along its row, and hangs moons below their planet', () => {
    const items = bodiesOf('sol');
    const sol = at(items, 'Sol');
    let lastX = sol.x;
    for (const planet of [
      'Mercury',
      'Venus',
      'Earth',
      'Mars',
      'Jupiter',
      'Saturn',
      'Uranus',
      'Neptune',
    ]) {
      const item = at(items, planet);
      expect(item.y, planet).toBe(sol.y);
      expect(item.x, planet).toBeGreaterThan(lastX);
      lastX = item.x;
    }
    const jupiter = at(items, 'Jupiter');
    let lastY = jupiter.y;
    for (const moon of ['Io', 'Europa', 'Ganymede', 'Callisto']) {
      const item = at(items, moon);
      expect(item.x, moon).toBe(jupiter.x);
      expect(item.y, moon).toBeGreaterThan(lastY);
      lastY = item.y;
    }
  });

  it('stacks the stars of a system one row each, joined on the left', () => {
    const layout = layoutOf(treeOf('lhs3447'));
    const items = layout.items.filter(
      (item): item is BodyItem => item.kind === 'body',
    );
    const first = at(items, 'LHS 3447');
    const second = at(items, 'Gliese 748.2 B');
    expect(second.x).toBe(first.x);
    expect(second.y).toBeGreaterThan(first.y);
    const join = layout.lines.filter(
      (line) =>
        line.kind === 'pair' && line.x1 === line.x2 && line.x1 < first.x,
    );
    expect(
      join.some((line) => line.y1 === first.y && line.y2 === second.y),
    ).toBe(true);
  });

  it('draws a bracket joining the two members of a pair', () => {
    const layout = layoutOf(treeOf('sol'));
    const items = layout.items.filter(
      (item): item is BodyItem => item.kind === 'body',
    );
    const pluto = at(items, 'Pluto');
    const charon = at(items, 'Charon');
    const across = layout.lines.find(
      (line) =>
        line.kind === 'pair' &&
        line.y1 === line.y2 &&
        line.x1 === pluto.x &&
        line.x2 === charon.x,
    );
    expect(across).toBeDefined();
  });

  it('marks the sites our data holds on a body and the slots the site predicts there', () => {
    const items = bodiesOf('lhs3447');
    expect(at(items, 'LHS 3447 A 3').mark).toMatchObject({
      built: 5,
      slots: 4,
    });
    // Nothing built and nothing predicted: no mark at all.
    expect(at(items, 'LHS 3447 A 7').mark).toBeNull();
    // With the builder's list of 13 November, which lacked A 2's facts: the
    // sites are shown and no slot count is made up.
    const older = layoutOf(lhs3447WithOlderPlan()).items.filter(
      (item): item is BodyItem => item.kind === 'body',
    );
    expect(at(older, 'LHS 3447 A 2').mark).toMatchObject({
      built: 5,
      slots: null,
    });
  });

  it('puts what is built in orbit beside its body, and each belt among the planets by its distance', () => {
    const layout = layoutOf(treeOf('sol'));
    const items = layout.items;
    const stations = items.filter((item) => item.kind === 'station');
    const sol = treeOf('sol');
    const inOrbit = [...sol.nodes.values()].reduce(
      (total, node) => total + node.orbit.length,
      0,
    );
    expect(stations).toHaveLength(inOrbit);
    const belt = items.find((item) => item.kind === 'belt');
    expect(belt).toBeDefined();
    // The Asteroid Belt lies between Mars and Jupiter.
    const bodies = items.filter(
      (item): item is BodyItem => item.kind === 'body',
    );
    expect(belt!.x).toBeGreaterThan(at(bodies, 'Mars').x);
    expect(belt!.x).toBeLessThan(at(bodies, 'Jupiter').x);
  });

  it('draws bigger bodies bigger, within bounds that keep a moon visible beside a star', () => {
    const tree = treeOf('sol');
    expect(drawnRadius(nodeNamed(tree, 'Jupiter'))).toBeGreaterThan(
      drawnRadius(nodeNamed(tree, 'Earth')),
    );
    expect(drawnRadius(nodeNamed(tree, 'Earth'))).toBeGreaterThan(
      drawnRadius(nodeNamed(tree, 'Moon')),
    );
    expect(drawnRadius(nodeNamed(tree, 'Moon'))).toBeGreaterThanOrEqual(3.5);
    expect(drawnRadius(nodeNamed(tree, 'Sol'))).toBeLessThanOrEqual(78);
  });
});
