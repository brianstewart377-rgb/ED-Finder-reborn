import { describe, expect, it } from 'vitest';
import { compareId64, formatId64, isId64, MAX_ID64, parseId64 } from './id64';

describe('Id64', () => {
  it.each(['0', '9007199254740993', '18446744073709551615'])(
    'preserves %s exactly',
    (value) => expect(parseId64(value)).toBe(value),
  );

  it('formats bigint and compares without number coercion', () => {
    expect(formatId64(MAX_ID64)).toBe('18446744073709551615');
    expect(compareId64('9007199254740993', '9007199254740992')).toBe(1);
    expect(compareId64(0n, '0')).toBe(0);
  });

  it.each([
    '',
    ' 1',
    '1 ',
    '+1',
    '-1',
    '01',
    '1.0',
    '1e3',
    '18446744073709551616',
  ])('rejects invalid input %j', (value) => {
    expect(() => parseId64(value)).toThrow();
    expect(isId64(value)).toBe(false);
  });

  it('rejects every JavaScript number, including unsafe and non-finite numbers', () => {
    for (const value of [0, 1, 9_007_199_254_740_992, NaN, Infinity]) {
      expect(() => parseId64(value as never)).toThrow();
    }
  });
});
