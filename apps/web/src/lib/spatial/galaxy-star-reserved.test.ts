import { describe, expect, it } from 'vitest';
import { parseId64 } from '$lib/domain/id64';
import type { GalaxySystemPoint, GalaxySystemsPayload } from './contracts';
import {
  createCatalogueStarsContribution,
  type CatalogueViewportSystem,
} from './galaxy-star-stream';

const background = (id: number): CatalogueViewportSystem => ({
  id64: parseId64(String(id)),
  name: `Catalogue ${id}`,
  x: id * 11,
  y: 0,
  z: id * 23,
  populated: false,
});

const known = (id: number): GalaxySystemPoint => ({
  systemId64: String(id),
  name: `Known ${id}`,
  positionLy: { x: id * 100_000, y: 90, z: -50_000 },
});

describe('reserved catalogue systems', () => {
  it('reuses unchanged rendered content when selecting an already reserved result', () => {
    const points = [known(90), known(91)];
    const previous = createCatalogueStarsContribution(
      [background(1)],
      1,
      false,
      points,
      5,
    );
    expect(
      createCatalogueStarsContribution(
        [background(1)],
        2,
        false,
        [points[1], ...points],
        5,
        previous,
      ),
    ).toBe(previous);
    const moved = { ...points[1], positionLy: { x: 1, y: 2, z: 3 } };
    expect(
      createCatalogueStarsContribution(
        [background(1)],
        3,
        false,
        [moved, ...points],
        5,
        previous,
      ),
    ).not.toBe(previous);
  });
  it('keeps selected, anchor, Finder and pinned systems outside the sample before spending the background budget', () => {
    const reserved = [known(90), known(91), known(92), known(93)];
    const contribution = createCatalogueStarsContribution(
      [background(1), background(2), background(3)],
      2,
      false,
      reserved,
      5,
    );
    const payload = contribution.layers[0]!.payload as GalaxySystemsPayload;
    expect(payload.systems).toHaveLength(5);
    expect(payload.systems.slice(0, 4)).toEqual(
      reserved.map((point) => ({ ...point, reserved: true })),
    );
    expect(payload.systems[4]?.reserved).toBeUndefined();
    expect(contribution.layers[0]).toMatchObject({
      targetCount: 5,
      truncated: true,
    });
  });

  it('never drops known targets when their count exceeds the background budget', () => {
    const reserved = [known(90), known(91), known(92)];
    const contribution = createCatalogueStarsContribution(
      [background(1)],
      3,
      false,
      reserved,
      1,
    );
    const payload = contribution.layers[0]!.payload as GalaxySystemsPayload;
    expect(payload.systems).toEqual(
      reserved.map((point) => ({ ...point, reserved: true })),
    );
  });

  it('deduplicates overlapping sources and preserves known metadata without inventing population or brightness', () => {
    const selected = known(90);
    const finder = {
      ...known(90),
      summary: { population: 12_345, primaryEconomy: 'Industrial' },
    };
    const contribution = createCatalogueStarsContribution(
      [
        { ...background(90), mainStarClass: 'K5', populated: true },
        background(91),
        background(91),
      ],
      1,
      false,
      [selected, finder, known(92)],
      3,
    );
    const payload = contribution.layers[0]!.payload as GalaxySystemsPayload;
    expect(payload.systems).toEqual([
      {
        ...selected,
        reserved: true,
        primaryStar: { type: 'K5' },
        summary: finder.summary,
      },
      { ...known(92), reserved: true },
      {
        systemId64: '91',
        name: 'Catalogue 91',
        positionLy: { x: 1001, y: 0, z: 2093 },
      },
    ]);
  });

  it('chooses the same budgeted background sample regardless of cell response order', () => {
    const systems = Array.from({ length: 20 }, (_, index) =>
      background(index + 1),
    );
    const contribution = createCatalogueStarsContribution(
      systems,
      1,
      false,
      [known(90)],
      5,
    );
    const reordered = createCatalogueStarsContribution(
      [...systems].reverse(),
      1,
      false,
      [known(90)],
      5,
    );
    expect(reordered).toEqual(contribution);
  });
});
