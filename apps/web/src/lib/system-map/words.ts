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

/** What a body orbits, in words, or null for the head of a row. */
export function orbitsInWords(tree: MapTree, node: MapNode): string | null {
  const parent = parentOf(tree, node);
  if (!parent) return null;
  let orbits =
    parent.type === 'barycentre'
      ? `A pair with ${
          parent.children
            .filter((one) => one !== node)
            .map((one) => one.name)
            .join(', ') || 'another body'
        }`
      : parent.name;
  if (node.guessedParent === 'name')
    orbits = `${orbits} (taken from its name: our data gives no parent)`;
  if (node.guessedParent === 'distance')
    orbits = `${orbits} (placed by its distance: our data gives no parent)`;
  return orbits;
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
