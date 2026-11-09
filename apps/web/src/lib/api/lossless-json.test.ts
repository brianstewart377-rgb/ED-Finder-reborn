import { describe, expect, it } from 'vitest';
import { parseLosslessJson } from './lossless-json';

describe('lossless API JSON', () => {
  it('normalises nested id64 fields before precision is lost', () => {
    expect(
      parseLosslessJson(
        '{"id64":9007199254740993,"nested":{"system_id64":18446744073709551615},"id64s":[0,9007199254740993]}',
      ),
    ).toEqual({
      id64: '9007199254740993',
      nested: { system_id64: '18446744073709551615' },
      id64s: ['0', '9007199254740993'],
    });
  });

  it('rejects out-of-range and malformed identifier fields', () => {
    expect(() => parseLosslessJson('{"id64":18446744073709551616}')).toThrow();
    expect(() => parseLosslessJson('{"system_id64":" 1"}')).toThrow();
  });
});
