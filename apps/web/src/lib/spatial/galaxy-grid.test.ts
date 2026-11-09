import { describe, expect, it } from 'vitest';

import type { CameraState } from './contracts';
import { galaxyReferenceGrid } from './galaxy-grid';

const camera = (distanceLy: number): CameraState => ({
  focusLy: { x: 123.4, y: 900, z: -456.7 },
  distanceLy,
  bearingRad: 0.4,
  pitchRad: 0.7,
  projection: 'perspective',
  revision: 1,
});

describe('Galaxy reference grid', () => {
  it('keeps the reference geometry on Galactic Y=0 without flattening camera focus', () => {
    const spec = galaxyReferenceGrid(camera(1_000), {
      width: 1_440,
      height: 900,
    });

    expect(spec.nominalPlaneY).toBe(0);
    expect(spec.renderPlaneY).toBeLessThan(0);
    expect(spec.minorStepLy).toBeGreaterThan(0);
    expect(spec.majorStepLy).toBe(spec.minorStepLy * 5);
    expect(
      [...spec.minorLines, ...spec.majorLines, ...spec.axisLines].every(
        (line) => line.every((point) => point.y === spec.renderPlaneY),
      ),
    ).toBe(true);
    expect(spec.bounds.minX).toBeLessThan(camera(1_000).focusLy.x);
    expect(spec.bounds.maxX).toBeGreaterThan(camera(1_000).focusLy.x);
    expect(spec.bounds.minZ).toBeLessThan(camera(1_000).focusLy.z);
    expect(spec.bounds.maxZ).toBeGreaterThan(camera(1_000).focusLy.z);
  });

  it('uses coarser truthful light-year intervals while zooming out', () => {
    const local = galaxyReferenceGrid(camera(80), {
      width: 1_280,
      height: 720,
    });
    const regional = galaxyReferenceGrid(camera(8_000), {
      width: 1_280,
      height: 720,
    });
    const galaxy = galaxyReferenceGrid(camera(180_000), {
      width: 1_280,
      height: 720,
    });

    expect(regional.minorStepLy).toBeGreaterThan(local.minorStepLy);
    expect(galaxy.minorStepLy).toBeGreaterThan(regional.minorStepLy);
    for (const spec of [local, regional, galaxy]) {
      expect(
        spec.minorLines.length + spec.majorLines.length + spec.axisLines.length,
      ).toBeLessThanOrEqual(160);
    }
  });

  it('is deterministic and changes its key only when visible grid geometry changes', () => {
    const first = galaxyReferenceGrid(camera(2_500), {
      width: 1_280,
      height: 720,
    });
    const repeated = galaxyReferenceGrid(camera(2_500), {
      width: 1_280,
      height: 720,
    });
    const zoomed = galaxyReferenceGrid(camera(250), {
      width: 1_280,
      height: 720,
    });

    expect(repeated).toEqual(first);
    expect(zoomed.key).not.toBe(first.key);
  });

  it('rejects invalid viewports', () => {
    expect(() =>
      galaxyReferenceGrid(camera(100), { width: 0, height: 720 }),
    ).toThrow(/viewport/u);
  });
});
