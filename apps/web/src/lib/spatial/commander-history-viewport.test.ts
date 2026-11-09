import { describe, expect, it } from 'vitest';
import type { CameraState } from './contracts';
import { GALAXY_CAMERA_FOV_RAD } from './galaxy-camera';
import {
  COMMANDER_HISTORY_GALAXY_BOUNDS,
  COMMANDER_HISTORY_POINT_LIMIT,
  commanderHistoryViewport,
} from './commander-history-viewport';
import { galaxyStarViewport } from './galaxy-star-stream';

const camera: CameraState = {
  focusLy: { x: 0, y: 0, z: 25_900 },
  distanceLy: 118_000,
  bearingRad: 0,
  pitchRad: 0.55,
  projection: 'perspective',
  revision: 0,
};
const viewport = { width: 1600, height: 900 };

describe('commander-history visible area', () => {
  it('covers distant imported travel at overview while catalogue stars stay clipped', () => {
    const journeys = commanderHistoryViewport(camera, viewport)!;
    const catalogue = galaxyStarViewport(camera, 'wide', viewport)!;
    expect(journeys).toEqual({
      ...COMMANDER_HISTORY_GALAXY_BOUNDS,
      limit: COMMANDER_HISTORY_POINT_LIMIT,
    });
    const distantVisit = { x: 25_000, y: 4_000, z: 60_000 };
    for (const [axis, low, high] of [
      ['x', 'minX', 'maxX'],
      ['y', 'minY', 'maxY'],
      ['z', 'minZ', 'maxZ'],
    ] as const) {
      expect(distantVisit[axis]).toBeGreaterThanOrEqual(journeys[low]);
      expect(distantVisit[axis]).toBeLessThanOrEqual(journeys[high]);
    }
    expect(distantVisit.x).toBeGreaterThan(catalogue.maxX);
    expect(distantVisit.z).toBeGreaterThan(catalogue.maxZ);
    expect(catalogue.maxX - catalogue.minX).toBeLessThanOrEqual(15_000);
    expect(catalogue.maxZ - catalogue.minZ).toBeLessThanOrEqual(15_000);
    expect(
      commanderHistoryViewport(
        { ...camera, focusLy: { x: 12_000, y: 500, z: 40_000 } },
        viewport,
      ),
    ).toEqual(journeys);
  });

  it('follows a zoomed camera footprint with overscan at the full journal row cap', () => {
    const state: CameraState = {
      ...camera,
      focusLy: { x: 21_000, y: 3_500, z: 60_000 },
      distanceLy: 100,
      projection: 'orthographic',
      pitchRad: Math.PI / 2,
    };
    const journeys = commanderHistoryViewport(state, viewport)!;
    const halfHeight = Math.tan(GALAXY_CAMERA_FOV_RAD / 2) * state.distanceLy;
    const halfWidth = halfHeight * (viewport.width / viewport.height);
    expect(journeys.minX).toBeCloseTo(state.focusLy.x - halfWidth * 1.15);
    expect(journeys.maxX).toBeCloseTo(state.focusLy.x + halfWidth * 1.15);
    expect(journeys.minZ).toBeCloseTo(state.focusLy.z - halfHeight * 1.15);
    expect(journeys.maxZ).toBeCloseTo(state.focusLy.z + halfHeight * 1.15);
    expect(journeys.minY).toBe(3_466);
    expect(journeys.maxY).toBe(3_534);
    expect(journeys.limit).toBe(COMMANDER_HISTORY_POINT_LIMIT);
  });

  it('retains visible travel past the catalogue window in a tilted regional view', () => {
    const state = { ...camera, distanceLy: 8_000, pitchRad: 0.2 };
    const journeys = commanderHistoryViewport(state, viewport)!;
    const catalogue = galaxyStarViewport(state, 'local', viewport)!;
    expect(journeys.maxZ).toBeGreaterThan(catalogue.maxZ);
    expect(journeys.minX).toBeLessThan(catalogue.minX);
    expect(journeys.minX).toBeGreaterThanOrEqual(
      COMMANDER_HISTORY_GALAXY_BOUNDS.minX,
    );
    expect(journeys.maxX).toBeLessThanOrEqual(
      COMMANDER_HISTORY_GALAXY_BOUNDS.maxX,
    );
    expect(journeys.maxZ).toBeLessThanOrEqual(
      COMMANDER_HISTORY_GALAXY_BOUNDS.maxZ,
    );
  });

  it('waits for the camera and measured viewport', () => {
    expect(commanderHistoryViewport(null, viewport)).toBeNull();
    expect(commanderHistoryViewport(camera, null)).toBeNull();
  });
});
