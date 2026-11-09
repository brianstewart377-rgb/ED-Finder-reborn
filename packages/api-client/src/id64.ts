/** Lossless, canonical unsigned 64-bit Elite Dangerous identifier. */
export type Id64 = string & { readonly __id64: unique symbol };

export const MAX_ID64 = 18_446_744_073_709_551_615n;

/**
 * Convert an identifier to its one canonical decimal representation.
 * JavaScript numbers are deliberately excluded: even apparently safe values
 * encourage a boundary which will silently round larger identifiers.
 */
export function parseId64(value: string | bigint): Id64 {
  if (typeof value !== 'string' && typeof value !== 'bigint') {
    throw new TypeError('Id64 cannot be represented by a JavaScript number');
  }
  if (typeof value === 'string' && !/^(?:0|[1-9]\d*)$/.test(value)) {
    throw new TypeError('Id64 must be an unsigned canonical decimal string');
  }
  const integer = typeof value === 'bigint' ? value : BigInt(value);
  if (integer < 0n || integer > MAX_ID64) {
    throw new RangeError('Id64 is outside the unsigned 64-bit range');
  }
  return integer.toString(10) as Id64;
}

export const formatId64 = (value: string | bigint): Id64 => parseId64(value);

export function compareId64(
  left: string | bigint,
  right: string | bigint,
): -1 | 0 | 1 {
  const a = BigInt(parseId64(left));
  const b = BigInt(parseId64(right));
  return a < b ? -1 : a > b ? 1 : 0;
}

export function isId64(value: unknown): value is Id64 {
  if (typeof value !== 'string') return false;
  try {
    return parseId64(value) === value;
  } catch {
    return false;
  }
}

