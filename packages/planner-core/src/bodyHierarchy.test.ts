import { describe, expect, it } from 'vitest';
import type { SystemBody } from '@ed-finder/api-client/types';
import { sortByBodyHierarchy } from './bodyHierarchy';

function bodies(names: string[]): SystemBody[] {
  return names.map((name, index) => ({ id: index + 1, name }));
}

function sortedNames(names: string[], systemName = 'Exioce'): string[] {
  return sortByBodyHierarchy(bodies(names), (body) => body, systemName).map((body) => body.name ?? '');
}

describe('body hierarchy sorting', () => {
  it('sorts the Exioce 4 hierarchy naturally', () => {
    expect(sortedNames([
      'Exioce 4 d a',
      'Exioce 4 d',
      'Exioce 4 b',
      'Exioce 4 a',
      'Exioce 4 a a',
      'Exioce 4',
      'Exioce 4 c',
      'Exioce 4 e',
    ])).toEqual([
      'Exioce 4',
      'Exioce 4 a',
      'Exioce 4 a a',
      'Exioce 4 b',
      'Exioce 4 c',
      'Exioce 4 d',
      'Exioce 4 d a',
      'Exioce 4 e',
    ]);
  });

  it('sorts numeric bodies as 1, 2, 10', () => {
    expect(sortedNames(['Exioce 10', 'Exioce 2', 'Exioce 1'])).toEqual([
      'Exioce 1',
      'Exioce 2',
      'Exioce 10',
    ]);
  });

  it('keeps nested moons under their parent before sibling moons', () => {
    expect(sortedNames(['Exioce 4 b', 'Exioce 4 a b', 'Exioce 4 a', 'Exioce 4 a a'])).toEqual([
      'Exioce 4 a',
      'Exioce 4 a a',
      'Exioce 4 a b',
      'Exioce 4 b',
    ]);
  });

  it('keeps unparseable names stable', () => {
    expect(sortedNames(['Experiment', "O'Rourke Colony", 'Democracy'])).toEqual([
      'Experiment',
      "O'Rourke Colony",
      'Democracy',
    ]);
  });
});
