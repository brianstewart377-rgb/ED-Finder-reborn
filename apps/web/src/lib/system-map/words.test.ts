import { describe, expect, it } from 'vitest';
import { lhs3447WithOlderPlan, nodeNamed, treeOf } from './recorded-systems';
import {
  builtHere,
  groundSlotsInWords,
  orbitsInWords,
  stationInWords,
} from './words';

describe('what the body card says', () => {
  it('names what a body orbits, and says when the map placed it by a guess', () => {
    const sol = treeOf('sol');
    expect(orbitsInWords(sol, nodeNamed(sol, 'Moon'))).toBe('Earth');
    expect(orbitsInWords(sol, nodeNamed(sol, 'Sol'))).toBeNull();
    expect(orbitsInWords(sol, nodeNamed(sol, 'Pluto'))).toBe(
      'A pair with Charon',
    );
    const polaris = treeOf('polaris');
    expect(orbitsInWords(polaris, nodeNamed(polaris, 'Polaris B 1 d'))).toBe(
      'Polaris B 1 (taken from its name: our data gives no parent)',
    );
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
