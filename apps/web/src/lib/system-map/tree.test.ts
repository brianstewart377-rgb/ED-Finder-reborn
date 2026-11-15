import { describe, expect, it } from 'vitest';
import {
  answersOf,
  detailOf,
  lhs3447WithOlderPlan,
  nodeNamed,
  planOf,
  treeOf,
} from './recorded-systems';
import { layoutOf } from './layout';
import {
  buildTree,
  pairMembers,
  ringsInWords,
  systemFromDetail,
  type MapNode,
} from './tree';

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

  it('puts a pair whose names point nowhere by the star nearest its distance, and marks it and its members a guess', () => {
    const tree = treeOf('sol');
    const pair = nodeNamed(tree, 'Sol barycentre 31');
    expect(pair.type).toBe('barycentre');
    expect(pair.guessedParent).toBe('distance');
    expect(nodeNamed(tree, 'Sol').children).toContain(pair);
    expect(names(pair.children)).toEqual(['Pluto', 'Charon']);
    // Our data gives Pluto its parent, the pair. Where the pair is drawn is
    // the guess, and Pluto and Charon are drawn there.
    for (const name of ['Pluto', 'Charon']) {
      expect(nodeNamed(tree, name).guessedParent).toBe(false);
      expect(nodeNamed(tree, name).placedBy).toBe('distance');
    }
    expect(nodeNamed(tree, 'Neptune').placedBy).toBe(false);
  });

  it('hangs a pair from the body both its members are named after: moons from their planet, planets from their star', () => {
    // Maia: star A is where a ship arrives; star B is 300,817 ls away and
    // what circles it lies between 296,458 and 307,624 ls. Our data gives no
    // pair a parent.
    const tree = treeOf('maia');
    expect(names(tree.heads)).toEqual(['Maia A', 'Maia B']);
    const childrenOf = (name: string) => names(nodeNamed(tree, name).children);
    // Two moons that circle each other, around the brown dwarf B 1, in
    // their place among its other moons going outward.
    expect(childrenOf('Maia B 1')).toEqual([
      'Maia B 1 a',
      'Maia B 1 b',
      'Maia barycentre 24',
      'Maia B 1 e',
      'Maia barycentre 28',
      'Maia B 1 h',
    ]);
    expect(childrenOf('Maia barycentre 24')).toEqual([
      'Maia B 1 c',
      'Maia B 1 d',
    ]);
    expect(childrenOf('Maia barycentre 28')).toEqual([
      'Maia B 1 f',
      'Maia B 1 g',
    ]);
    // The brown dwarf B 2 and the gas giant B 3 circle each other around
    // star B, and B 2's own pair of moons hangs from B 2.
    expect(childrenOf('Maia B')).toEqual([
      'Maia B 1',
      'Maia barycentre 32',
      'Maia B 4',
      'Maia B 5',
    ]);
    expect(childrenOf('Maia barycentre 32')).toEqual(['Maia B 2', 'Maia B 3']);
    expect(childrenOf('Maia B 2')).toContain('Maia barycentre 36');
    // Star A's row holds only what is named after A: nothing of B's.
    expect(childrenOf('Maia A')).toEqual([
      'Maia A 1',
      'Maia A 2',
      'Maia barycentre 8',
      'Maia A 5',
      'Maia barycentre 14',
    ]);
    const below = (node: {
      name: string;
      children: readonly unknown[];
    }): string[] =>
      (node.children as (typeof node)[]).flatMap((child) => [
        child.name,
        ...below(child),
      ]);
    expect(
      below(nodeNamed(tree, 'Maia A')).filter((name) =>
        name.startsWith('Maia B'),
      ),
    ).toEqual([]);
    // Each is marked as placed by its names, pair and members both.
    for (const pair of [8, 14, 24, 28, 32, 36]) {
      const node = nodeNamed(tree, `Maia barycentre ${pair}`);
      expect(node.guessedParent).toBe('name');
      for (const member of node.children) {
        expect(member.guessedParent).toBe(false);
        expect(member.placedBy).toBe('name');
      }
    }
    // A moon of a member is where our data says: no guess of its own.
    expect(nodeNamed(tree, 'Maia A 3 a').placedBy).toBe(false);
    // Every body of the system is in the picture exactly once.
    const drawn = tree.heads.flatMap((head) => [head.name, ...below(head)]);
    expect(drawn).toHaveLength(tree.nodes.size);
    expect(new Set(drawn).size).toBe(tree.nodes.size);
  });

  it('gives each of two stars that circle each other a row of its own, and draws what goes around both in the nearer star’s row', () => {
    // Alpha Centauri and Alpha Centauri B circle each other; three planets
    // go around both; Proxima Centauri is 6.4 million ls away. Our data
    // gives the pair no parent.
    const tree = treeOf('alphaCentauri');
    // The star a ship arrives at heads the first row, not a row under Proxima.
    expect(names(tree.heads)).toEqual([
      'Alpha Centauri',
      'Alpha Centauri B',
      'Proxima Centauri',
    ]);
    const pair = nodeNamed(tree, 'Alpha Centauri barycentre 1');
    // Five bodies are under the pair in our data. Two circle each other.
    expect(names(pair.members)).toEqual(['Alpha Centauri', 'Alpha Centauri B']);
    expect(pair.children).toEqual([]);
    expect(names(nodeNamed(tree, 'Alpha Centauri').children)).toEqual([]);
    // The planets around both are 4,391 and 11,905 ls out; B is at 4,544.
    expect(names(nodeNamed(tree, 'Alpha Centauri B').children)).toEqual([
      'Alpha Centauri B 1',
      '2045 PC2',
      'Lagrange',
      '2071 AC3',
    ]);
    expect(names(nodeNamed(tree, 'Proxima Centauri').children)).toEqual([
      'Eden',
    ]);
    // Our data gives each its parent, so none is marked as a guess.
    for (const name of ['2045 PC2', 'Lagrange', '2071 AC3']) {
      const planet = nodeNamed(tree, name);
      expect(planet.parent).toBe(pair.id);
      expect(planet.guessedParent).toBe(false);
      expect(planet.placedBy).toBe(false);
    }
    // No bracket says the three planets are a pair with the stars: the only
    // joining lines are the one down the left and one to each of the stars.
    const joins = layoutOf(tree).lines.filter((line) => line.kind === 'pair');
    expect(joins).toHaveLength(4);
    // Every body is in the picture once; the pair itself is not drawn.
    const below = (node: MapNode): MapNode[] =>
      node.children.flatMap((child) => [child, ...below(child)]);
    const drawn = tree.heads.flatMap((head) => [head, ...below(head)]);
    expect(names(drawn).sort()).toEqual(
      names(
        [...tree.nodes.values()].filter((node) => node.type !== 'barycentre'),
      ).sort(),
    );
  });

  it('takes the two smallest orbits under a pair as the pair, and all of them where an orbit is not known', () => {
    const tree = treeOf('alphaCentauri');
    const under = [
      'Alpha Centauri',
      'Alpha Centauri B',
      '2045 PC2',
      'Lagrange',
      '2071 AC3',
    ].map((name) => nodeNamed(tree, name));
    expect(names(pairMembers(under))).toEqual([
      'Alpha Centauri',
      'Alpha Centauri B',
    ]);
    // Without one orbit's size there is no telling which two are the pair.
    const unknown = under.map((node) =>
      node.name === 'Lagrange' ? { ...node, axisKm: null } : node,
    );
    expect(pairMembers(unknown)).toHaveLength(5);
    // Two bodies are a pair whatever is known of their orbits.
    expect(pairMembers(unknown.slice(2, 4))).toHaveLength(2);
  });

  it('puts a body with no parent and no telling name in the row of the star nearest its distance, not the last star it is beyond', () => {
    // LHS 3447 A 6 is 91,929 ls from the arrival point; its star, LHS 3447,
    // is further, at 92,255 ls. The arrival star (Gliese 748.2 B) is at 0.
    const detail = detailOf('lhs3447');
    const orphaned = {
      ...detail,
      bodies: detail.bodies.map((body) =>
        body.name === 'LHS 3447 A 6'
          ? { ...body, name: 'Unnamed', directParentBodyKey: null }
          : body,
      ),
    };
    const tree = buildTree(systemFromDetail(orphaned));
    const body = nodeNamed(tree, 'Unnamed');
    expect(body.guessedParent).toBe('distance');
    expect(nodeNamed(tree, 'LHS 3447').children).toContain(body);
    expect(nodeNamed(tree, 'Gliese 748.2 B').children).not.toContain(body);
  });

  it('never hangs a body from one of its own descendants', () => {
    // A planet with no parent, named as if it were a moon of its own moon.
    const detail = detailOf('polaris');
    const planet = detail.bodies.find((body) => body.name === 'Polaris B 1')!;
    const moon = detail.bodies.find((body) => body.name === 'Polaris B 1 a')!;
    expect(moon.directParentBodyKey).toBe(planet.bodyKey);
    const twisted = {
      ...detail,
      bodies: detail.bodies.map((body) =>
        body === planet
          ? { ...body, name: 'Polaris B 1 a x', directParentBodyKey: null }
          : body,
      ),
    };
    const tree = buildTree(systemFromDetail(twisted));
    const node = nodeNamed(tree, 'Polaris B 1 a x');
    // Not from its own moon: it goes to a star's row by its distance.
    expect(node.guessedParent).toBe('distance');
    expect(names(tree.heads)).toContain(
      [...tree.nodes.values()].find((one) => one.children.includes(node))!.name,
    );
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
