import { describe, expect, it } from 'vitest';
import {
  GALAXY_STAR_API_CUBE_BOUND_LY,
  GALAXY_WIDE_STAR_LIMIT,
  galaxyLocalStarLimit,
} from './galaxy-detail-policy';
import {
  GalaxyStarCellCache,
  GALAXY_STAR_REFINEMENT_QUANTUM_LY,
  MAX_GALAXY_STAR_ACTIVE_CELLS,
  planGalaxyStarCells,
  refineGalaxyStarCells,
  type GalaxyStarCell,
} from './galaxy-star-cells';
import { GALAXY_CATALOGUE_BOUNDS } from './galaxy-star-footprint';
import type { GalaxyStarViewport } from './galaxy-star-stream';

function viewport(
  x = 0,
  z = 0,
  width = 8_000,
  depth = 6_000,
  distance = 4_000,
): GalaxyStarViewport {
  const bounds = {
    minX: x - width / 2,
    maxX: x + width / 2,
    minZ: z - depth / 2,
    maxZ: z + depth / 2,
  };
  return {
    ...bounds,
    minY: -340,
    maxY: 340,
    limit: galaxyLocalStarLimit(distance),
    cameraDistanceLy: distance,
    wide: false,
    footprint: {
      ...bounds,
      polygon: [
        { x: bounds.minX, z: bounds.minZ },
        { x: bounds.maxX, z: bounds.minZ },
        { x: bounds.maxX, z: bounds.maxZ },
        { x: bounds.minX, z: bounds.maxZ },
      ],
    },
  };
}

describe('catalogue cell planning', () => {
  it('keeps overview and galaxy samples to one request until local detail is active', () => {
    for (const distance of [9_000, 9_500, 40_000, 120_000]) {
      const view = {
        ...viewport(425, -925, 15_000, 15_000, distance),
        wide: true,
        limit: GALAXY_WIDE_STAR_LIMIT,
      };
      const cells = planGalaxyStarCells(view);
      expect(cells).toHaveLength(1);
      expect(cells[0].request).toEqual({
        minX: view.minX,
        maxX: view.maxX,
        minY: view.minY,
        maxY: view.maxY,
        minZ: view.minZ,
        maxZ: view.maxZ,
        limit: GALAXY_WIDE_STAR_LIMIT,
      });
      expect(planGalaxyStarCells(view, cells)).toBe(cells);
      expect(
        refineGalaxyStarCells(view, cells, () => ({ truncated: true })),
      ).toEqual(cells);
      expect(
        planGalaxyStarCells({ ...view, wide: false }, cells).length,
      ).toBeGreaterThan(1);
    }
  });

  it('coalesces adjacent local cells without expanding coverage or raising the row limit', () => {
    const cells = planGalaxyStarCells(viewport(250, 250, 200, 200, 100));
    expect(cells).toHaveLength(1);
    expect(cells[0].request).toEqual({
      minX: 0,
      maxX: 500,
      minY: -500,
      maxY: 500,
      minZ: 0,
      maxZ: 500,
      limit: 1_800,
    });
    expect(
      planGalaxyStarCells(viewport(750, 250, 200, 200, 100))[0].request,
    ).toMatchObject({ minX: 500, maxX: 1_000, minZ: 0, maxZ: 500 });
  });

  it('reuses the same cells after a 5% pan and changes them after a 100% pan', () => {
    const initial = planGalaxyStarCells(viewport());
    const smallMove = planGalaxyStarCells(viewport(400, 300), initial);
    const largeMove = planGalaxyStarCells(viewport(8_000, 6_000), smallMove);
    expect(smallMove).toBe(initial);
    expect(largeMove.map((cell) => cell.key)).not.toEqual(
      initial.map((cell) => cell.key),
    );
  });

  it('uses stable world-grid keys when independent plans cover the same cells', () => {
    const initial = planGalaxyStarCells(viewport(0, 0, 6_000, 6_000));
    expect(planGalaxyStarCells(viewport(300, 300, 6_000, 6_000))).toEqual(
      initial,
    );
    expect(initial.every((cell) => cell.request.limit === 40_000)).toBe(true);
  });

  it.each([
    { distance: 140, width: 120, expected: 250 },
    { distance: 1_000, width: 1_200, expected: 1_000 },
    { distance: 4_000, width: 2_500, expected: 2_500 },
  ])(
    'starts the $distance LY band with $expected LY cells',
    ({ distance, width, expected }) => {
      const cells = planGalaxyStarCells(viewport(0, 0, width, width, distance));
      expect(cells.every((cell) => cell.cellSizeLy === expected)).toBe(true);
      expect(
        cells.every(
          (cell) => cell.request.limit === galaxyLocalStarLimit(distance),
        ),
      ).toBe(true);
    },
  );

  it('covers large shallow-view footprints without exceeding the active or API bounds', () => {
    for (const distance of [140, 1_000, 4_000, 120_000]) {
      const view = viewport(425, -925, 15_000, 15_000, distance);
      const cells = planGalaxyStarCells(view);
      expect(cells.length).toBeLessThanOrEqual(MAX_GALAXY_STAR_ACTIVE_CELLS);
      for (const { request } of cells) {
        expect(request.maxX - request.minX).toBeLessThanOrEqual(
          GALAXY_STAR_API_CUBE_BOUND_LY,
        );
        expect(request.maxY - request.minY).toBeLessThanOrEqual(
          GALAXY_STAR_API_CUBE_BOUND_LY,
        );
        expect(request.maxZ - request.minZ).toBeLessThanOrEqual(
          GALAXY_STAR_API_CUBE_BOUND_LY,
        );
        expect(request.minY).toBeLessThanOrEqual(view.minY);
        expect(request.maxY).toBeGreaterThanOrEqual(view.maxY);
      }
      // Probe the complete rectangle, including its corners and cell seams.
      for (let x = view.minX; x <= view.maxX; x += 500) {
        for (let z = view.minZ; z <= view.maxZ; z += 500) {
          expect(
            cells.some(
              ({ request }) =>
                x >= request.minX &&
                x <= request.maxX &&
                z >= request.minZ &&
                z <= request.maxZ,
            ),
          ).toBe(true);
        }
      }
    }
  });

  it('clips boundary cells to catalogue bounds without shifting the grid', () => {
    const view = viewport(50_000, 76_000, 4_000, 4_000);
    const cells = planGalaxyStarCells(view);
    expect(Math.max(...cells.map((cell) => cell.request.maxX))).toBe(
      GALAXY_CATALOGUE_BOUNDS.maxX,
    );
    expect(Math.max(...cells.map((cell) => cell.request.maxZ))).toBe(
      GALAXY_CATALOGUE_BOUNDS.maxZ,
    );
    expect(
      cells.every(
        ({ request }) =>
          request.minX >= GALAXY_CATALOGUE_BOUNDS.minX &&
          request.minZ >= GALAXY_CATALOGUE_BOUNDS.minZ,
      ),
    ).toBe(true);
  });

  it('reuses a covered Y band and changes keys when the band or budget changes', () => {
    const view = viewport();
    const cells = planGalaxyStarCells(view);
    expect(planGalaxyStarCells({ ...view, minY: -300, maxY: 400 }, cells)).toBe(
      cells,
    );
    expect(
      planGalaxyStarCells({ ...view, minY: 1_000, maxY: 1_680 }, cells)[0].key,
    ).not.toBe(cells[0].key);
    expect(
      planGalaxyStarCells({ ...view, limit: 24_000 }, cells)[0].key,
    ).not.toBe(cells[0].key);
  });

  it('does not request an empty footprint and rejects unbounded input', () => {
    expect(planGalaxyStarCells(viewport(0, 0, 0, 0))).toEqual([]);
    expect(() => planGalaxyStarCells(viewport(0, 0, 15_001, 10))).toThrow(
      RangeError,
    );
  });
});

describe('truncated catalogue cell refinement', () => {
  const closeView = (size: number) => ({
    ...viewport(200, 200, size, size, 100),
    minY: 10,
    maxY: 40,
  });

  it('tightens only truncated cells in all three axes without raising budgets', () => {
    const view = closeView(80);
    const bases = planGalaxyStarCells(view);
    const truncatedKey = bases.find(
      (cell) => cell.request.minX === 0 && cell.request.minZ === 0,
    )!.key;
    const refined = refineGalaxyStarCells(view, bases, (cell) => ({
      truncated: cell.key === truncatedKey,
    }));
    expect(refined).toHaveLength(bases.length);
    for (let index = 0; index < bases.length; index++) {
      const base = bases[index];
      const cell = refined[index];
      if (base.key !== truncatedKey) {
        expect(cell).toBe(base);
        continue;
      }
      expect(cell.parentKey).toBe(base.key);
      expect(cell.key).not.toBe(base.key);
      expect(cell.request).toEqual({
        minX: 125,
        maxX: 250,
        minY: 0,
        maxY: 62.5,
        minZ: 125,
        maxZ: 250,
        limit: 1_800,
      });
    }
  });

  it('narrows a still-truncated sub-volume to 62.5 LY and then reuses its key', () => {
    const bases = planGalaxyStarCells(closeView(80));
    const truncated = () => ({ truncated: true });
    const first = refineGalaxyStarCells(closeView(80), bases, truncated);
    const smaller = refineGalaxyStarCells(
      closeView(10),
      bases,
      truncated,
      first,
    );
    const smallest = smaller.find((cell) => cell.parentKey)!;
    expect(smallest.key).not.toBe(first.find((cell) => cell.parentKey)!.key);
    for (const [min, max] of [
      [smallest.request.minX, smallest.request.maxX],
      [smallest.request.minY, smallest.request.maxY],
      [smallest.request.minZ, smallest.request.maxZ],
    ])
      expect(max - min).toBe(GALAXY_STAR_REFINEMENT_QUANTUM_LY);
    expect(
      refineGalaxyStarCells(closeView(1), bases, truncated, smaller),
    ).toEqual(smaller);
  });

  it('reuses complete base cells and complete covering refinements on zoom-in', () => {
    const bases = planGalaxyStarCells(closeView(80));
    expect(
      refineGalaxyStarCells(closeView(10), bases, () => ({ truncated: false })),
    ).toEqual(bases);
    const first = refineGalaxyStarCells(closeView(80), bases, () => ({
      truncated: true,
    }));
    expect(
      refineGalaxyStarCells(
        closeView(10),
        bases,
        (cell) => ({ truncated: !cell.parentKey }),
        first,
      ),
    ).toEqual(first);
  });
});

function cell(key: string, x: number): GalaxyStarCell {
  return {
    key,
    cellSizeLy: 250,
    centre: { x, y: 0, z: 0 },
    request: {
      minX: x - 125,
      maxX: x + 125,
      minY: -250,
      maxY: 250,
      minZ: -125,
      maxZ: 125,
      limit: 1_800,
    },
  };
}

describe('catalogue cell cache retention', () => {
  const focus = { x: 0, y: 0, z: 0 };

  it('evicts distant inactive cells first while pinning the active viewport', () => {
    const cache = new GalaxyStarCellCache(3);
    cache.retain(
      [cell('near', 250), cell('far', 2_000), cell('middle', 1_000)],
      focus,
    );
    expect(
      cache.retain([cell('active', 10_000)], focus).map(({ key }) => key),
    ).toEqual(['far']);
    expect(cache.size).toBe(3);
    expect(cache.get('active')).toBeDefined();
    expect(cache.get('near')).toBeDefined();
    expect(cache.get('middle')).toBeDefined();
  });

  it('uses least-recently-used order to break equal-distance ties', () => {
    const cache = new GalaxyStarCellCache(2);
    cache.retain([cell('left', -500), cell('right', 500)], focus);
    cache.get('left');
    expect(
      cache.retain([cell('active', 0)], focus).map(({ key }) => key),
    ).toEqual(['right']);
  });

  it('changes distance priority with the camera and stays bounded over repeated pans', () => {
    const cache = new GalaxyStarCellCache(3);
    cache.retain(
      [cell('left', -500), cell('right', 500), cell('centre', 0)],
      focus,
    );
    expect(
      cache
        .retain([cell('active', 250)], { ...focus, x: 1_000 })
        .map(({ key }) => key),
    ).toEqual(['left']);
    for (let index = 0; index < 100; index++) {
      cache.retain([cell(`pan-${index}`, index * 250)], {
        ...focus,
        x: index * 250,
      });
      expect(cache.size).toBe(3);
    }
  });

  it('rejects impossible capacities instead of silently dropping active cells', () => {
    expect(() => new GalaxyStarCellCache(0)).toThrow(RangeError);
    expect(() =>
      new GalaxyStarCellCache(1).retain([cell('a', 0), cell('b', 250)], focus),
    ).toThrow(RangeError);
  });

  it('accepts a cube-bound footprint at fractional world coordinates', () => {
    const view = viewport(-30_586.52928543677, 0, 15_000, 15_000);
    expect(view.maxX - view.minX).toBeGreaterThan(15_000);
    expect(planGalaxyStarCells(view).length).toBeLessThanOrEqual(
      MAX_GALAXY_STAR_ACTIVE_CELLS,
    );
    const [{ request }] = planGalaxyStarCells({ ...view, wide: true });
    expect(request.maxX - request.minX).toBeLessThanOrEqual(
      GALAXY_STAR_API_CUBE_BOUND_LY,
    );
  });
});
