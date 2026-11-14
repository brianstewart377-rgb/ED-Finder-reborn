import { describe, expect, it } from 'vitest';
import { cosmosAsk, cosmosPixels, MOLTEN_FROM_K, WIDTHS } from './cosmos-look';
import { lookOf, ringBandsOf, RING_COLOURS } from './draw';
import { askOf } from './pictures';
import { nodeNamed, treeOf, type RecordedName } from './recorded-systems';

const SYSTEMS: RecordedName[] = ['sol', 'lhs3447', 'polaris', 'altair'];

function askFor(name: RecordedName, body: string) {
  const node = nodeNamed(treeOf(name), body);
  return askOf({
    id: node.id,
    look: lookOf(node),
    r: 10,
    surfaceK: node.surfaceK,
  });
}

describe('how each body looks, from its class', () => {
  it.each(SYSTEMS)(
    'knows the class of every planet in %s that our data gives a class',
    (name) => {
      for (const node of treeOf(name).nodes.values()) {
        if (node.type !== 'planet' || node.bodyClass === null) continue;
        expect(lookOf(node).family, node.name).not.toBe('unknown');
      }
    },
  );

  it('gives each kind of world its own kind of Cosmos picture', () => {
    expect(askFor('sol', 'Earth').kind).toBe('terrain_wet');
    expect(askFor('sol', 'Mercury').kind).toBe('barren');
    expect(askFor('sol', 'Europa').kind).toBe('ice');
    expect(askFor('sol', 'Jupiter').kind).toBe('gas_giant_ringed');
    expect(askFor('lhs3447', 'LHS 3447 B 1').kind).toBe('gas_giant_ringed');
    expect(askFor('sol', 'Sol').kind).toBe('star');
  });

  it('draws the hotter of two real high-metal-content worlds molten, and the cooler one bare', () => {
    // Altair 2 is at 1,753 K and Altair 1 at 591 K in our data.
    const hot = askFor('altair', 'Altair 2');
    const cool = askFor('altair', 'Altair 1');
    expect(hot.kind).toBe('molten');
    expect(cool.kind).toBe('barren');
    expect(
      nodeNamed(treeOf('altair'), 'Altair 2').surfaceK,
    ).toBeGreaterThanOrEqual(MOLTEN_FROM_K);
  });

  it('cannot draw rock molten without a temperature', () => {
    // The same world, with the builder's list not fetched.
    const node = nodeNamed(treeOf('altair', false), 'Altair 2');
    expect(
      askOf({ id: node.id, look: lookOf(node), r: 10, surfaceK: node.surfaceK })
        .kind,
    ).toBe('barren');
  });

  it('tells two stars of different classes apart', () => {
    const tree = treeOf('polaris');
    const supergiant = lookOf(nodeNamed(tree, 'Polaris A'));
    const dwarf = lookOf(nodeNamed(tree, 'Polaris B'));
    expect(supergiant.star && dwarf.star).toBe(true);
    expect(supergiant.colour).not.toEqual(dwarf.colour);
  });

  it('keeps the app’s colours for gas giants and bare rock, and makes the ring Cosmos adds to a gas giant clear', () => {
    const jupiter = lookOf(nodeNamed(treeOf('sol'), 'Jupiter'));
    const ask = cosmosAsk({ family: jupiter.family, colour: jupiter.colour });
    expect(ask.colours?.gas3?.slice(0, 3)).toEqual([...jupiter.colour]);
    for (const ring of ['ring1', 'ring2', 'ring3', 'ring4'])
      expect(ask.colours?.[ring]?.[3]).toBe(0);
    expect(ask.width).toBe(WIDTHS.gas_giant);
  });

  it('asks Cosmos for enough pixels to stay sharp when zoomed, within bounds', () => {
    expect(cosmosPixels(5, WIDTHS.planet)).toBe(128);
    expect(cosmosPixels(78, WIDTHS.star)).toBe(512);
  });
});

describe('rings', () => {
  it('draws each of a planet’s rings as its own band in its kind’s colour, from the inside out', () => {
    const bands = ringBandsOf(nodeNamed(treeOf('lhs3447'), 'LHS 3447 B 2'));
    expect(bands.map((band) => band.kind)).toEqual(['metal rich', 'icy']);
    expect(bands.map((band) => band.colour)).toEqual([
      RING_COLOURS.metal_rich,
      RING_COLOURS.icy,
    ]);
    expect(bands[0]!.to).toBeLessThan(bands[1]!.from);
  });
});
