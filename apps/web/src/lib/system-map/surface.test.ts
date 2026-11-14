import { describe, expect, it } from 'vitest';
import { lookOf, seedOf } from './draw';
import { nodeNamed, treeOf } from './recorded-systems';
import { patternOf, surface } from './surface';

const SIZE = 48;

function paint(name: string, seedText?: string) {
  const node = nodeNamed(treeOf('sol'), name);
  const look = lookOf(node);
  return surface({
    seed: seedOf(seedText ?? node.id),
    size: SIZE,
    family: look.family,
    colour: look.colour,
    star: look.star,
    halo: look.halo ?? look.colour,
  }).data;
}

/** How bright a quarter of the picture is, on average, over the ball only. */
function brightness(
  data: Uint8ClampedArray,
  left: boolean,
  top: boolean,
): number {
  let total = 0;
  let count = 0;
  for (let row = 0; row < SIZE; row += 1)
    for (let column = 0; column < SIZE; column += 1) {
      if (row < SIZE / 2 !== top || column < SIZE / 2 !== left) continue;
      const index = (row * SIZE + column) * 4;
      if (data[index + 3]! < 255) continue;
      total += data[index]! + data[index + 1]! + data[index + 2]!;
      count += 1;
    }
  return total / count;
}

describe('painted surfaces, where there is no WebGL', () => {
  it('paints a round ball: clear at the corners, solid in the middle', () => {
    const data = paint('Earth');
    expect(data[3]).toBe(0);
    const middle = ((SIZE / 2) * SIZE + SIZE / 2) * 4;
    expect(data[middle + 3]).toBe(255);
  });

  it('lights a planet from the upper left, and does not shade a star', () => {
    const mercury = paint('Mercury');
    expect(brightness(mercury, true, true)).toBeGreaterThan(
      brightness(mercury, false, false) * 1.5,
    );
    const sol = paint('Sol');
    expect(
      brightness(sol, true, true) / brightness(sol, false, false),
    ).toBeCloseTo(1, 0);
  });

  it('gives each class its kind of pattern', () => {
    const tree = treeOf('sol');
    expect(patternOf(lookOf(nodeNamed(tree, 'Jupiter')).family)).toBe('bands');
    expect(patternOf(lookOf(nodeNamed(tree, 'Earth')).family)).toBe('seas');
    expect(patternOf(lookOf(nodeNamed(tree, 'Mercury')).family)).toBe('ground');
  });

  it('paints a body the same every time, and two bodies of one class differently', () => {
    expect(paint('Ganymede')).toEqual(paint('Ganymede'));
    expect(paint('Ganymede')).not.toEqual(paint('Ganymede', 'another body'));
  });
});
