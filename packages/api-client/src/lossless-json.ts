import { JSONParse } from 'json-with-bigint';
import { parseId64 } from './id64';

const INTEGER_TOKEN = /^\d+$/;

export function isId64Field(key: string): boolean {
  return (
    key === 'id64' ||
    key === 'id64s' ||
    /(?:^|_)(?:from_|to_)?(?:system_)?id64s?$/.test(key)
  );
}

/** Parse before the platform JSON parser can round an id64 token. */
export function parseLosslessJson(text: string): unknown {
  const parsed = JSONParse(
    text,
    (_key: string, value: unknown, context?: { source: string }) => {
      if (
        context &&
        typeof value === 'number' &&
        !Number.isSafeInteger(value) &&
        INTEGER_TOKEN.test(context.source)
      ) {
        return BigInt(context.source);
      }
      return value;
    },
  ) as unknown;
  return normaliseResponseIds(parsed);
}

/** Project identifier-bearing response fields to the application Id64 boundary. */
export function normaliseResponseIds(value: unknown, key = ''): unknown {
  if (isId64Field(key)) {
    if (Array.isArray(value)) return value.map((item) => normaliseId(item));
    if (value === null || value === undefined) return value;
    return normaliseId(value);
  }
  if (typeof value === 'bigint') return value.toString(10);
  if (Array.isArray(value))
    return value.map((item) => normaliseResponseIds(item));
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(
        ([childKey, child]) => [
          childKey,
          normaliseResponseIds(child, childKey),
        ],
      ),
    );
  }
  return value;
}

function normaliseId(value: unknown): string {
  if (typeof value === 'bigint' || typeof value === 'string')
    return parseId64(value);
  if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) {
    return parseId64(BigInt(value));
  }
  throw new SyntaxError(`Invalid id64 response value for '${String(value)}'`);
}
