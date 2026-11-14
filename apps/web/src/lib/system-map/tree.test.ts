import { describe, expect, it } from 'vitest';
import {
  answersOf,
  detailOf,
  lhs3447WithOlderPlan,
  nodeNamed,
  planOf,
  treeOf,
} from './recorded-systems';
import { buildTree, ringsInWords, systemFromDetail } from './tree';

const names = (nodes: readonly { name: string }[]) =>
  nodes.map((node) => node.name);

describe('what orbits what, read from the live site', () => {
  it("hangs Sol's planets from Sol in the order they are met going outward, and each moon from its planet", () => {
    const tree = treeOf('sol');
    expect(names(tree.heads)).toEqual(['Sol']);
    const sol = nodeNamed(tree, 'Sol');
    expect(names(sol.children).slice(0, 8)).toEqual([
      'Mercury',
      'Venus',
      'Earth',
      'Mars',
      'Jupiter',
      'Saturn',
      'Uranus',
      'Neptune',
    ]);
    expect(names(nodeNamed(tree, 'Earth').children)).toEqual(['Moon']);
    expect(names(nodeNamed(tree, 'Jupiter').children)).toEqual([
      'Io',
      'Europa',
      'Ganymede',
      'Callisto',
    ]);
    // Every one of these parents is in our data: nothing here is a guess.
    for (const name of ['Mercury', 'Earth', 'Moon', 'Callisto', 'Triton'])
      expect(nodeNamed(tree, name).guessedParent).toBe(false);
  });

  it('heads one row for each star, in the game’s own order', () => {
    expect(names(treeOf('lhs3447').heads)).toEqual([
      'LHS 3447',
      'Gliese 748.2 B',
    ]);
    expect(names(treeOf('polaris').heads)).toEqual(['Polaris A', 'Polaris B']);
  });

  it('puts a pair our data gives no parent by the star nearest its distance, and marks it a guess', () => {
    const tree = treeOf('sol');
    const pair = nodeNamed(tree, 'Sol barycentre 31');
    expect(pair.type).toBe('barycentre');
    expect(pair.guessedParent).toBe('distance');
    expect(nodeNamed(tree, 'Sol').children).toContain(pair);
    expect(names(pair.children)).toEqual(['Pluto', 'Charon']);
  });

  it('hangs a moon our data gives no parent from the body its name points to, and marks it a guess', () => {
    const tree = treeOf('polaris');
    const planet = nodeNamed(tree, 'Polaris B 1');
    for (const name of ['Polaris B 1 d', 'Polaris B 1 e']) {
      const moon = nodeNamed(tree, name);
      expect(moon.guessedParent).toBe('name');
      expect(planet.children).toContain(moon);
    }
    expect(nodeNamed(tree, 'Polaris B 1 a').guessedParent).toBe(false);
  });

  it('still draws a system whose catalogue holds no star, from its bodies without a parent', () => {
    const detail = detailOf('polaris');
    const starless = {
      ...detail,
      bodies: detail.bodies.filter((body) => body.bodyType !== 'star'),
    };
    const tree = buildTree(systemFromDetail(starless));
    expect(names(tree.heads)).toEqual(['Polaris B 1', 'Polaris B 3']);
    expect(nodeNamed(tree, 'Polaris B 1').children).toHaveLength(7);
  });
});

describe('what is built where', () => {
  it('puts a station on the ground at the body our data names, as a fact', () => {
    const ganymede = nodeNamed(treeOf('sol'), 'Ganymede');
    expect(ganymede.ground).toHaveLength(5);
    expect(ganymede.ground.every((station) => !station.guess)).toBe(true);
  });

  it('puts a station in orbit that our data gives no body at the body nearest its distance, and marks it a guess', () => {
    const tree = treeOf('sol');
    const earth = nodeNamed(tree, 'Earth');
    expect(earth.orbit.length).toBeGreaterThan(0);
    for (const station of earth.orbit) {
      expect(station.body).toBeNull();
      expect(station.guess).toBe(true);
      expect(
        Math.abs((station.arrivalLs ?? 0) - (earth.arrivalLs ?? 0)),
      ).toBeLessThanOrEqual(Math.max(3, (station.arrivalLs ?? 0) * 0.005));
    }
  });

  it('lists a station near no body as not placed, rather than guessing far off', () => {
    expect(
      treeOf('sol')
        .notPlaced.map((station) => station.name)
        .sort(),
    ).toEqual(['M.Gorbachev', 'Rescue Ship - Li Qing Jao']);
  });

  it('leaves fleet carriers out: they come and go', () => {
    const carriers = answersOf('lhs3447').stations.filter(
      (station) =>
        (station as { station_type: string | null }).station_type ===
        'fleet_carrier',
    );
    expect(carriers.length).toBeGreaterThan(10);
    const tree = treeOf('lhs3447');
    const shown = [
      ...[...tree.nodes.values()].flatMap((node) => [
        ...node.orbit,
        ...node.ground,
        ...node.unknownKind,
      ]),
      ...tree.notPlaced,
    ];
    expect(shown.length).toBeGreaterThan(0);
    expect(shown.some((station) => station.type === 'fleet_carrier')).toBe(
      false,
    );
  });
});

describe('ground slots and temperatures, from the builder’s list', () => {
  it('carries each predicted count, and the reason where there is none', () => {
    const tree = treeOf('lhs3447');
    expect(nodeNamed(tree, 'LHS 3447 A 3').groundSlots).toEqual({
      count: 4,
      why: 'predicted',
      missing: [],
    });
    expect(nodeNamed(tree, 'LHS 3447 A 4').groundSlots.why).toBe('cannot land');
    // On 13 November the builder still lacked facts about A 1, and said which.
    const lacking = nodeNamed(
      lhs3447WithOlderPlan(),
      'LHS 3447 A 1',
    ).groundSlots;
    expect(lacking.why).toBe('facts missing');
    expect(lacking.count).toBeNull();
    expect(lacking.missing.length).toBeGreaterThan(0);
  });

  it('carries each body’s surface temperature', () => {
    const tree = treeOf('altair');
    expect(nodeNamed(tree, 'Altair 2').surfaceK).toBeCloseTo(1753, 0);
    expect(nodeNamed(tree, 'Altair 1').surfaceK).toBeCloseTo(591, 0);
  });

  it('says "not asked" for every body when the builder’s list was not fetched', () => {
    const tree = treeOf('lhs3447', false);
    for (const node of tree.nodes.values()) {
      expect(node.groundSlots.why).toBe('not asked');
      expect(node.surfaceK).toBeNull();
    }
    expect(planOf('lhs3447')).not.toBeNull();
  });
});

describe('rings', () => {
  it('names a planet’s rings by their kind, from the inside out', () => {
    const tree = treeOf('sol');
    expect(nodeNamed(tree, 'Saturn').ringWords).toBe('icy ring');
    expect(nodeNamed(tree, 'Jupiter').ringWords).toBe('rocky ring');
    // A belt is the star's, not a ring of a planet.
    expect(nodeNamed(tree, 'Sol').belts.map((belt) => belt.name)).toEqual([
      'Asteroid Belt',
    ]);
    expect(nodeNamed(tree, 'Sol').rings).toEqual([]);
  });

  it('says how many there are and of which kinds when there are several', () => {
    const rings = nodeNamed(treeOf('lhs3447'), 'LHS 3447 B 2').rings;
    expect(rings).toHaveLength(2);
    // Our data: a metal-rich ring inside an icy one.
    expect(ringsInWords(rings)).toBe('rings: metal rich, icy');
    expect(ringsInWords([])).toBeNull();
  });
});
