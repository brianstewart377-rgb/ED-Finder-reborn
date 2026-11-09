import { describe, expect, it } from 'vitest';
import { starClassLabel } from './star-label';

describe('catalogue star labels', () => {
  it.each([
    ['G', 'G star'],
    ['G (White-Yellow) Star', 'G (White-Yellow) Star'],
    ['Neutron star', 'Neutron star'],
    ['Wolf-Rayet STAR', 'Wolf-Rayet STAR'],
    ['White Dwarf (DA)', 'White Dwarf (DA) star'],
  ])('presents %s without repeating its suffix', (value, expected) => {
    expect(starClassLabel(value)).toBe(expected);
  });
});
