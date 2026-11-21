import { describe, expect, it } from 'vitest';
import { bodiesKnownText } from './bodies-known';

describe('how much of a system is known, as a plain fact', () => {
  it('says all of them when we hold as many as the game counts', () => {
    // Teegarden's star and Charick Drift, as the live site held them on
    // 21 November 2026.
    expect(bodiesKnownText(8, 8)).toBe('All 8 bodies known');
    expect(bodiesKnownText(2, 2)).toBe('All 2 bodies known');
    expect(bodiesKnownText(1, 1)).toBe('Its 1 body is known');
  });

  it('says all of them when we hold more than the game counts', () => {
    // DG Canum Venaticorum: the game counts 24, the catalogue holds 25.
    expect(bodiesKnownText(25, 24)).toBe('All 25 bodies known');
  });

  it('says how many of the total when some are missing', () => {
    expect(bodiesKnownText(12, 24)).toBe('12 of 24 bodies known');
    expect(bodiesKnownText(1, 1200)).toBe('1 of 1,200 bodies known');
  });

  it('says so when the game has given no total', () => {
    expect(bodiesKnownText(5, null)).toBe(
      "5 bodies known; the game's total is not on record",
    );
    expect(bodiesKnownText(1, undefined)).toBe(
      "1 body known; the game's total is not on record",
    );
  });

  it('says no bodies are known, whatever the total', () => {
    // Witch's Reach: nothing held, no total.
    expect(bodiesKnownText(0, null)).toBe('No bodies known');
    expect(bodiesKnownText(0, 7)).toBe('No bodies known');
  });

  it('says nothing when it does not know how many we hold', () => {
    expect(bodiesKnownText(null, 8)).toBeNull();
    expect(bodiesKnownText(undefined, undefined)).toBeNull();
    expect(bodiesKnownText(Number.NaN, 8)).toBeNull();
    expect(bodiesKnownText(-1, 8)).toBeNull();
  });
});
