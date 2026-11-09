import { JSONParse } from 'json-with-bigint';

const INTEGER_TOKEN = /^-?\d+$/;

/**
 * Reviver used for Frontier journal JSON. This follows the kayahr/edsm
 * approach: inspect the original numeric token and promote unsafe integers
 * before JavaScript can silently round an identifier.
 *
 * `parseJournalJson` uses json-with-bigint as a compatibility layer because
 * the third reviver argument (`context.source`) is not available in every
 * browser we support yet.
 */
export function journalJsonReviver(
  key: string,
  value: unknown,
  context?: { source: string },
): unknown {
  if (
    context
    && typeof value === 'number'
    && !Number.isSafeInteger(value)
    && INTEGER_TOKEN.test(context.source)
  ) {
    if (isIdentifierKey(key)) return BigInt(context.source);
    if (String(value) !== context.source) {
      throw new SyntaxError(
        `Journal property '${key}' contains an unsafe integer (${context.source}).`,
      );
    }
  }
  return value;
}

export function parseJournalJson(text: string): Record<string, unknown> {
  const parsed = JSONParse(text, journalJsonReviver) as unknown;
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new SyntaxError('Journal line must contain one JSON object.');
  }
  return parsed as Record<string, unknown>;
}

export function decimalString(value: unknown, options: { allowZero?: boolean } = {}): string | null {
  const allowZero = options.allowZero ?? false;
  let integer: bigint;

  if (typeof value === 'bigint') {
    integer = value;
  } else if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) return null;
    integer = BigInt(value);
  } else if (typeof value === 'string' && INTEGER_TOKEN.test(value.trim())) {
    integer = BigInt(value.trim());
  } else {
    return null;
  }

  if (integer < 0n || (!allowZero && integer === 0n)) return null;
  return integer.toString(10);
}

/** Convert BigInts and ID-like numeric fields to JSON-safe decimal strings. */
export function toJournalTransportValue(value: unknown, key = ''): unknown {
  if (typeof value === 'bigint') return value.toString(10);
  if (typeof value === 'number' && Number.isInteger(value) && isIdentifierKey(key)) {
    return value.toString(10);
  }
  if (Array.isArray(value)) {
    return value.map((item) => toJournalTransportValue(item));
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .map(([itemKey, item]) => [itemKey, toJournalTransportValue(item, itemKey)]),
    );
  }
  return value;
}

function isIdentifierKey(key: string): boolean {
  return key === 'id64'
    || key === 'systemId64'
    || key.endsWith('ID')
    || key.endsWith('Id')
    || key.endsWith('Address');
}
