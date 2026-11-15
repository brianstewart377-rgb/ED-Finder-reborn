import { beforeEach, describe, expect, it } from 'vitest';
import type { MapResume } from './GalaxyMap.svelte';
import { forgetMapLeftAt, mapLeftAtFor, rememberMapLeftAt } from './left-at';

const view = {
  cursor: [1, 2, 3] as [number, number, number],
  distance: 70,
  yaw: 0,
  pitch: 35,
};
const home = {
  cursor: [0, 0, 0] as [number, number, number],
  distance: 60_000,
  yaw: 0,
  pitch: 60,
};
const leftAt = (shownId: string | null): MapResume => ({ view, home, shownId });

describe('where the map was left, for the visit to the site', () => {
  beforeEach(() => forgetMapLeftAt());

  it('has nowhere to open on a first visit', () => {
    expect(mapLeftAtFor(null)).toBeNull();
    expect(mapLeftAtFor('10477373803')).toBeNull();
  });

  it('opens there again for the system it was showing, and for no other', () => {
    const left = leftAt('10477373803');
    rememberMapLeftAt(left);
    expect(mapLeftAtFor('10477373803')).toBe(left);
    // Another system, or none: the map starts on the whole galaxy.
    expect(mapLeftAtFor('1178708478315')).toBeNull();
    expect(mapLeftAtFor(null)).toBeNull();
  });

  it('opens there again when it was showing no system and still is', () => {
    const left = leftAt(null);
    rememberMapLeftAt(left);
    expect(mapLeftAtFor(null)).toBe(left);
    expect(mapLeftAtFor('10477373803')).toBeNull();
  });

  it('keeps only the last place it was left', () => {
    rememberMapLeftAt(leftAt('1'));
    const last = leftAt('2');
    rememberMapLeftAt(last);
    expect(mapLeftAtFor('1')).toBeNull();
    expect(mapLeftAtFor('2')).toBe(last);
  });
});
