import { describe, expect, it } from 'vitest';

import type {
  AutocompleteSystem,
  ExploreSystem,
  WatchlistEntry,
} from '$lib/api/client';
import { parseId64 } from '$lib/domain/id64';
import type { PinnedEntry } from '$lib/persistence/storage';
import type { GalaxySystemPoint } from '$lib/spatial/contracts';
import { reservedExploreGalaxyPoints } from './reserved-galaxy-points';

type Context = Parameters<typeof reservedExploreGalaxyPoints>[0];
const context = (overrides: Partial<Context> = {}): Context => ({
  selected: null,
  anchor: null,
  systems: [],
  pins: [],
  watched: [],
  ...overrides,
});
const position = { x: 12.5, y: -40, z: 800.25 };
const selected: GalaxySystemPoint = {
  systemId64: '9007199254740993',
  name: 'Selected reach',
  positionLy: position,
  primaryStar: { type: 'G', subtype: 'G2' },
  summary: { population: 1200, primaryEconomy: 'Industrial' },
};
const anchor: AutocompleteSystem = {
  id64: parseId64('2'),
  name: 'Anchor',
  ...position,
};
const result: ExploreSystem = {
  id64: parseId64('3'),
  name: 'Finder result',
  coords: position,
};
const pin: PinnedEntry = {
  id64: parseId64('4'),
  name: 'Pin',
  pinned_at: '2026-11-02T00:00:00Z',
  coords: position,
};
const watched: WatchlistEntry = {
  system_id64: parseId64('5'),
  name: 'Watched',
  ...position,
};

describe('reserved Explore galaxy points', () => {
  it('retains known positions from selection, anchor, Finder page, pins and watchlist', () => {
    const points = reservedExploreGalaxyPoints(
      context({
        selected,
        anchor,
        systems: [result],
        pins: [pin],
        watched: [watched],
      }),
    );
    expect(
      points.map((point) => [point.systemId64, point.name, point.positionLy]),
    ).toEqual([
      [selected.systemId64, selected.name, position],
      [anchor.id64, anchor.name, position],
      [result.id64, result.name, position],
      [pin.id64, pin.name, position],
      [watched.system_id64, watched.name, position],
    ]);
    expect(points[0]).toBe(selected);
  });

  it('accepts both current nested pin coordinates and historical flat snapshots', () => {
    const points = reservedExploreGalaxyPoints(
      context({
        pins: [
          { ...pin, main_star_type: 'K' },
          {
            id64: parseId64('6'),
            name: 'Legacy pin',
            pinned_at: pin.pinned_at,
            ...position,
          },
        ],
      }),
    );
    expect(points).toMatchObject([
      {
        systemId64: pin.id64,
        positionLy: position,
        primaryStar: { type: 'K' },
      },
      { systemId64: '6', positionLy: position },
    ]);
  });

  it('does not invent population or stellar brightness for anchor, pin or watchlist positions', () => {
    const points = reservedExploreGalaxyPoints(
      context({ anchor, pins: [pin], watched: [watched] }),
    );
    expect(points).toHaveLength(3);
    for (const point of points) {
      expect(point.summary).not.toHaveProperty('population');
      expect(point).not.toHaveProperty('primaryStar');
      expect(point).not.toHaveProperty('brightness');
      expect(point.summary).not.toHaveProperty('brightness');
    }
  });

  it('preserves known Finder stellar and domain metadata without deriving brightness', () => {
    const points = reservedExploreGalaxyPoints(
      context({
        systems: [
          {
            ...result,
            main_star_type: 'G',
            main_star_subtype: 'G2',
            population: 1200,
            distance: 42,
            primaryEconomy: 'Industrial',
            secondaryEconomy: 'Refinery',
            security: 'High',
            allegiance: 'Independent',
            government: 'Democracy',
          },
        ],
      }),
    );
    expect(points).toEqual([
      {
        systemId64: result.id64,
        name: result.name,
        positionLy: position,
        primaryStar: { type: 'G', subtype: 'G2' },
        summary: {
          population: 1200,
          distanceLy: 42,
          primaryEconomy: 'Industrial',
          secondaryEconomy: 'Refinery',
          security: 'High',
          allegiance: 'Independent',
          government: 'Democracy',
        },
      },
    ]);
  });

  it.each([
    { x: 0, y: 0, z: 0 },
    { x: NaN, y: 1, z: 2 },
    { x: 1, y: Infinity, z: 2 },
  ])('rejects unknown or invalid selected coordinates %o', (positionLy) => {
    expect(
      reservedExploreGalaxyPoints(
        context({ selected: { ...selected, positionLy } }),
      ),
    ).toEqual([]);
  });

  it.each([
    { x: 0, y: 0, z: 0 },
    { x: 1, y: NaN, z: 2 },
  ])('rejects unknown or invalid anchor coordinates %o', (coordinates) => {
    expect(
      reservedExploreGalaxyPoints(
        context({ anchor: { ...anchor, ...coordinates } }),
      ),
    ).toEqual([]);
  });

  it('omits missing, nonfinite and legacy zero-coordinate Finder and watchlist rows', () => {
    const badCoordinates = [
      undefined,
      null,
      { x: null, y: 2, z: 3 },
      { x: 1, y: Infinity, z: 3 },
      { x: 0, y: 0, z: 0 },
    ];
    expect(
      reservedExploreGalaxyPoints(
        context({
          systems: badCoordinates.map((coords) => ({ ...result, coords })),
          watched: badCoordinates.map((coords) => ({
            system_id64: watched.system_id64,
            name: watched.name,
            ...coords,
          })),
        }),
      ),
    ).toEqual([]);
  });

  it('omits missing and invalid persisted pin coordinates without coercing strings or arrays', () => {
    const badCoordinates = [
      undefined,
      null,
      [],
      { x: '12', y: 2, z: 3 },
      { x: NaN, y: 2, z: 3 },
      { x: 1, y: 2 },
      { x: 0, y: 0, z: 0 },
    ];
    expect(
      reservedExploreGalaxyPoints(
        context({ pins: badCoordinates.map((coords) => ({ ...pin, coords })) }),
      ),
    ).toEqual([]);
  });

  it('retains the real Sol origin from every source', () => {
    const solId = parseId64('10477373803');
    const origin = { x: 0, y: 0, z: 0 };
    const points = reservedExploreGalaxyPoints(
      context({
        selected: { systemId64: solId, name: 'Sol', positionLy: origin },
        anchor: { ...anchor, id64: solId, name: 'Sol', ...origin },
        systems: [{ ...result, id64: solId, name: 'Sol', coords: origin }],
        pins: [{ ...pin, id64: solId, name: 'Sol', coords: origin }],
        watched: [{ ...watched, system_id64: solId, name: 'Sol', ...origin }],
      }),
    );
    expect(points).toHaveLength(5);
    expect(points.every((point) => point.systemId64 === solId)).toBe(true);
    expect(points.map((point) => point.positionLy)).toEqual(
      Array(5).fill(origin),
    );
  });

  it('does not fabricate targets when all page sources are absent', () => {
    expect(reservedExploreGalaxyPoints(context())).toEqual([]);
  });
});
