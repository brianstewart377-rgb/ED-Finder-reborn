/**
 * What Inspect's card says of a body from the System Map's reading of the
 * system: what it orbits, its ground slots and what is built there. Each
 * guess the map made is said to be one.
 */
import type { MapNode, MapTree, PlacedStation } from './tree';

/** The body a node hangs from in the map, or null for the head of a row. */
export function parentOf(tree: MapTree, node: MapNode): MapNode | null {
  for (const one of tree.nodes.values())
    if (one.children.includes(node)) return one;
  return null;
}

/**
 * What a body orbits, in words, or null for a star that orbits nothing our
 * data knows of.
 *
 * A body in a pair is said to be one, with what the pair goes around. Our
 * data never says what a pair orbits, so that part is always given as the
 * guess it is. A body that goes around both of a pair is said to: where the
 * pair is two stars at the top of the system, the body is drawn in the row
 * of one of them, and that is said too.
 */
export function orbitsInWords(tree: MapTree, node: MapNode): string | null {
  const pair = node.parent === null ? undefined : tree.nodes.get(node.parent);
  if (pair?.type === 'barycentre') {
    const names = (bodies: readonly MapNode[]) =>
      bodies.map((one) => one.name).join(' and ') || 'another body';
    const around = parentOf(tree, pair);
    if (pair.members.includes(node)) {
      const others = names(pair.members.filter((one) => one !== node));
      if (!around) return `A pair with ${others}`;
      const how =
        pair.guessedParent === 'name'
          ? ' (taken from their names: our data does not say what the pair orbits)'
          : pair.guessedParent === 'distance'
            ? ' (placed by their distance: our data does not say what the pair orbits)'
            : '';
      return `A pair with ${others}, around ${around.name}${how}`;
    }
    const both = `${names(pair.members)}, which circle each other`;
    const drawn = parentOf(tree, node);
    return drawn && drawn !== pair
      ? `${both} (drawn in the row of ${drawn.name}, the nearer)`
      : both;
  }
  const parent = parentOf(tree, node);
  if (!parent) return null;
  let orbits = parent.name;
  if (node.guessedParent === 'name')
    orbits = `${orbits} (taken from its name: our data gives no parent)`;
  if (node.guessedParent === 'distance')
    orbits = `${orbits} (placed by its distance: our data gives no parent)`;
  return orbits;
}

/**
 * How a body came to be where it is on the map, where that is a guess, in a
 * few words; null where our data gives it. A body in a pair that was placed
 * by a guess carries the pair's guess.
 */
export function placedInWords(node: MapNode): string | null {
  if (node.guessedParent === 'name') return 'placed by its name';
  if (node.guessedParent === 'distance')
    return 'placed by its distance from the arrival point';
  if (node.placedBy === 'name') return 'in a pair placed by its names';
  if (node.placedBy === 'distance')
    return 'in a pair placed by its distance from the arrival point';
  return null;
}

/** What the card says of a body's ground slots. Null for a star, or where the builder's list was not fetched. */
export function groundSlotsInWords(node: MapNode): string | null {
  if (node.type === 'star' || node.type === 'barycentre') return null;
  const { count, why, missing } = node.groundSlots;
  if (why === 'not asked') return null;
  if (why === 'predicted')
    return `${count} (predicted by this site; the game is the only sure source)`;
  if (why === 'cannot land') return 'None: it cannot be landed on';
  if (why === 'facts missing')
    return `Not known: our data lacks this body's ${missing.join(', ') || 'facts'}, so the site makes no prediction`;
  return 'Not known';
}

/** A station as the card lists it. */
export function stationInWords(station: PlacedStation): string {
  return `${station.name}, ${station.kind}${station.guess ? ' (placed by its distance: our data does not say which body)' : ''}`;
}

/** What is built at a body: in orbit, on the ground, and of a kind our data does not give. */
export function builtHere(
  node: MapNode,
): { where: string; stations: readonly PlacedStation[] }[] {
  return [
    { where: 'In orbit', stations: node.orbit },
    { where: 'On the ground', stations: node.ground },
    { where: 'Kind not known', stations: node.unknownKind },
  ].filter((group) => group.stations.length > 0);
}
