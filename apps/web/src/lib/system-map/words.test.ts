import { describe, expect, it } from 'vitest';
import { lhs3447WithOlderPlan, nodeNamed, treeOf } from './recorded-systems';
import {
  builtHere,
  groundSlotsInWords,
  orbitsInWords,
  placedInWords,
  stationInWords,
} from './words';

describe('what the body card says', () => {
  it('names what a body orbits, and says when the map placed it by a guess', () => {
    const sol = treeOf('sol');
    expect(orbitsInWords(sol, nodeNamed(sol, 'Moon'))).toBe('Earth');
    expect(orbitsInWords(sol, nodeNamed(sol, 'Sol'))).toBeNull();
    // Our data never says what a pair orbits, so that part is a guess, said.
    expect(orbitsInWords(sol, nodeNamed(sol, 'Pluto'))).toBe(
      'A pair with Charon, around Sol (placed by their distance: our data does not say what the pair orbits)',
    );
    const polaris = treeOf('polaris');
    expect(orbitsInWords(polaris, nodeNamed(polaris, 'Polaris B 1 d'))).toBe(
      'Polaris B 1 (taken from its name: our data gives no parent)',
    );
    const maia = treeOf('maia');
    expect(orbitsInWords(maia, nodeNamed(maia, 'Maia B 1 c'))).toBe(
      'A pair with Maia B 1 d, around Maia B 1 (taken from their names: our data does not say what the pair orbits)',
    );
    expect(orbitsInWords(maia, nodeNamed(maia, 'Maia B 3'))).toBe(
      'A pair with Maia B 2, around Maia B (taken from their names: our data does not say what the pair orbits)',
    );
  });

  it('says of two stars at the top of a system that they are a pair, and of a planet around both that it is', () => {
    const tree = treeOf('alphaCentauri');
    // Each star heads a row, and is still said to be in a pair.
    expect(orbitsInWords(tree, nodeNamed(tree, 'Alpha Centauri'))).toBe(
      'A pair with Alpha Centauri B',
    );
    expect(orbitsInWords(tree, nodeNamed(tree, 'Alpha Centauri B'))).toBe(
      'A pair with Alpha Centauri',
    );
    // The planet is drawn in B's row, and goes around both stars.
    expect(orbitsInWords(tree, nodeNamed(tree, 'Lagrange'))).toBe(
      'Alpha Centauri and Alpha Centauri B, which circle each other (drawn in the row of Alpha Centauri B, the nearer)',
    );
    expect(orbitsInWords(tree, nodeNamed(tree, '2042 L1'))).toBe('Lagrange');
    expect(orbitsInWords(tree, nodeNamed(tree, 'Proxima Centauri'))).toBeNull();
    expect(placedInWords(nodeNamed(tree, 'Lagrange'))).toBeNull();
  });

  it('says in a few words how a body came to be where it is, where that is a guess', () => {
    const sol = treeOf('sol');
    expect(placedInWords(nodeNamed(sol, 'Earth'))).toBeNull();
    expect(placedInWords(nodeNamed(sol, 'Charon'))).toBe(
      'in a pair placed by its distance from the arrival point',
    );
    const polaris = treeOf('polaris');
    expect(placedInWords(nodeNamed(polaris, 'Polaris B 1 d'))).toBe(
      'placed by its name',
    );
    const maia = treeOf('maia');
    expect(placedInWords(nodeNamed(maia, 'Maia B 2 c'))).toBe(
      'in a pair placed by its names',
    );
    expect(placedInWords(nodeNamed(maia, 'Maia B 1 a'))).toBeNull();
  });

  it('gives a predicted slot count as a prediction, and says why there is none', () => {
    const tree = treeOf('lhs3447');
    expect(groundSlotsInWords(nodeNamed(tree, 'LHS 3447 A 3'))).toBe(
      '4 (predicted by this site; the game is the only sure source)',
    );
    expect(groundSlotsInWords(nodeNamed(tree, 'LHS 3447 A 4'))).toBe(
      'None: it cannot be landed on',
    );
    expect(groundSlotsInWords(nodeNamed(tree, 'LHS 3447'))).toBeNull();
    expect(
      groundSlotsInWords(nodeNamed(lhs3447WithOlderPlan(), 'LHS 3447 A 1')),
    ).toMatch(
      /^Not known: our data lacks this body's .+, so the site makes no prediction$/u,
    );
    expect(
      groundSlotsInWords(nodeNamed(treeOf('lhs3447', false), 'LHS 3447 A 3')),
    ).toBeNull();
  });

  it('lists what is built at a body, in orbit and on the ground, saying which places are guesses', () => {
    const tree = treeOf('sol');
    const earth = builtHere(nodeNamed(tree, 'Earth'));
    expect(earth.map((group) => group.where)).toEqual(['In orbit']);
    for (const station of earth[0]!.stations)
      expect(stationInWords(station)).toMatch(
        /, .+ \(placed by its distance: our data does not say which body\)$/u,
      );
    const ganymede = builtHere(nodeNamed(tree, 'Ganymede'));
    expect(ganymede.map((group) => group.where)).toEqual(['On the ground']);
    for (const station of ganymede[0]!.stations)
      expect(stationInWords(station)).not.toContain('placed by');
    expect(builtHere(nodeNamed(tree, 'Triton'))).toEqual([]);
  });
});
