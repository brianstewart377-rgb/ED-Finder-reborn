const count = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0;

const bodies = (n: number): string =>
  `${n.toLocaleString('en-US')} ${n === 1 ? 'body' : 'bodies'}`;

/**
 * How much of a system we hold, as a plain fact: how many of its bodies
 * (stars included) are in the catalogue, against the count the game gives
 * when the system is scanned. `null` when we do not know how many we hold.
 */
export function bodiesKnownText(
  loaded: number | null | undefined,
  source: number | null | undefined,
): string | null {
  if (!count(loaded)) return null;
  if (loaded === 0) return 'No bodies known';
  if (!count(source))
    return `${bodies(loaded)} known; the game's total is not on record`;
  if (loaded >= source)
    return loaded === 1 ? 'Its 1 body is known' : `All ${bodies(loaded)} known`;
  return `${loaded.toLocaleString('en-US')} of ${bodies(source)} known`;
}
