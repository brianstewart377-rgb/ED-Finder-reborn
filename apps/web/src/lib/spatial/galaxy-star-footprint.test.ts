import { describe, expect, it } from 'vitest';

import type { CameraState, Vec3Ly } from './contracts';
import { GALAXY_CAMERA_FOV_RAD, galaxyCameraPose } from './galaxy-camera';
import { GALAXY_STAR_API_CUBE_BOUND_LY } from './galaxy-detail-policy';
import {
  GALAXY_CATALOGUE_BOUNDS,
  galaxyStarFootprint,
  withinGalaxyStarFootprint,
} from './galaxy-star-footprint';

const camera: CameraState = {
  focusLy: { x: 310, y: 75, z: 920 },
  distanceLy: 1_000,
  bearingRad: 0.7,
  pitchRad: 0.8,
  projection: 'perspective',
  revision: 1,
};

/** Independent screen-ray intersection with a horizontal factual plane. */
function screenPlanePoint(
  state: CameraState,
  aspect: number,
  screenX: number,
  screenY: number,
  planeY: number,
): Vec3Ly | null {
  const pose = galaxyCameraPose(state);
  const right = {
    x: Math.cos(state.bearingRad),
    y: 0,
    z: Math.sin(state.bearingRad),
  };
  const forward = {
    x: (state.focusLy.x - pose.positionLy.x) / state.distanceLy,
    y: (state.focusLy.y - pose.positionLy.y) / state.distanceLy,
    z: (state.focusLy.z - pose.positionLy.z) / state.distanceLy,
  };
  const tangent = Math.tan(GALAXY_CAMERA_FOV_RAD / 2);
  const screenOffset = {
    x: tangent * (right.x * screenX * aspect + pose.up.x * screenY),
    y: tangent * pose.up.y * screenY,
    z: tangent * (right.z * screenX * aspect + pose.up.z * screenY),
  };
  const origin =
    state.projection === 'perspective'
      ? pose.positionLy
      : {
          x: pose.positionLy.x + screenOffset.x * state.distanceLy,
          y: pose.positionLy.y + screenOffset.y * state.distanceLy,
          z: pose.positionLy.z + screenOffset.z * state.distanceLy,
        };
  const direction =
    state.projection === 'orthographic'
      ? forward
      : {
          x: forward.x + screenOffset.x,
          y: forward.y + screenOffset.y,
          z: forward.z + screenOffset.z,
        };
  const distance = (planeY - origin.y) / direction.y;
  if (!Number.isFinite(distance) || distance < 0) return null;
  return {
    x: origin.x + direction.x * distance,
    y: planeY,
    z: origin.z + direction.z * distance,
  };
}

function onScreen(state: CameraState, aspect: number, point: Vec3Ly): boolean {
  const pose = galaxyCameraPose(state);
  const offset = {
    x: point.x - pose.positionLy.x,
    y: point.y - pose.positionLy.y,
    z: point.z - pose.positionLy.z,
  };
  const depth =
    (offset.x * (state.focusLy.x - pose.positionLy.x) +
      offset.y * (state.focusLy.y - pose.positionLy.y) +
      offset.z * (state.focusLy.z - pose.positionLy.z)) /
    state.distanceLy;
  const horizontal =
    offset.x * Math.cos(state.bearingRad) +
    offset.z * Math.sin(state.bearingRad);
  const vertical =
    offset.x * pose.up.x + offset.y * pose.up.y + offset.z * pose.up.z;
  const halfHeight =
    Math.tan(GALAXY_CAMERA_FOV_RAD / 2) *
    (state.projection === 'perspective' ? depth : state.distanceLy);
  return (
    depth >= 0 &&
    Math.abs(horizontal) <= halfHeight * aspect &&
    Math.abs(vertical) <= halfHeight
  );
}

describe('camera-aware catalogue footprint', () => {
  for (const projection of ['perspective', 'orthographic'] as const) {
    for (const pitchRad of [0.2, 0.8, Math.PI / 2]) {
      it.each([1, 21 / 9])(
        `contains visible plane corners and horizon-clipped regions at ${projection} pitch ${pitchRad}, aspect %s`,
        (aspect) => {
          const state = { ...camera, projection, pitchRad };
          const minY = state.focusLy.y - 80;
          const maxY = state.focusLy.y + 80;
          const footprint = galaxyStarFootprint(
            state,
            { width: 900 * aspect, height: 900 },
            minY,
            maxY,
          );
          expect(footprint.polygon.length).toBeGreaterThanOrEqual(3);
          const halfCube = GALAXY_STAR_API_CUBE_BOUND_LY / 2;
          const inCube = (point: Vec3Ly) =>
            Math.abs(point.x - state.focusLy.x) <= halfCube &&
            Math.abs(point.z - state.focusLy.z) <= halfCube;
          let checkedCorners = 0;
          for (const planeY of [minY, state.focusLy.y, maxY]) {
            for (const screenX of [-1, 1]) {
              for (const screenY of [-1, 1]) {
                const point = screenPlanePoint(
                  state,
                  aspect,
                  screenX,
                  screenY,
                  planeY,
                );
                if (point && inCube(point)) {
                  expect(
                    withinGalaxyStarFootprint(footprint, point.x, point.z),
                  ).toBe(true);
                  checkedCorners++;
                }
              }
            }
            // At shallow perspective pitch the upper rays point above the
            // horizon. Check the visible region through the cube's far edge too.
            for (let x = -halfCube; x <= halfCube; x += 750) {
              for (let z = -halfCube; z <= halfCube; z += 750) {
                const point = {
                  x: x + state.focusLy.x,
                  y: planeY,
                  z: z + state.focusLy.z,
                };
                if (onScreen(state, aspect, point)) {
                  expect(
                    withinGalaxyStarFootprint(footprint, point.x, point.z),
                  ).toBe(true);
                }
              }
            }
          }
          expect(checkedCorners).toBeGreaterThan(0);
          expect(footprint.maxX - footprint.minX).toBeLessThanOrEqual(
            GALAXY_STAR_API_CUBE_BOUND_LY + 1e-8,
          );
          expect(footprint.maxZ - footprint.minZ).toBeLessThanOrEqual(
            GALAXY_STAR_API_CUBE_BOUND_LY + 1e-8,
          );
        },
      );
    }
  }

  it('keeps on-screen shallow-pitch stars beyond the former radius and drops off-screen box corners', () => {
    const state = {
      ...camera,
      focusLy: { x: 0, y: 0, z: 0 },
      bearingRad: 0,
      pitchRad: 0.2,
    };
    const footprint = galaxyStarFootprint(
      state,
      { width: 2100, height: 900 },
      0,
      0,
    );
    const visible = screenPlanePoint(state, 21 / 9, 0.75, 0.3, 0)!;
    expect(Math.hypot(visible.x, visible.z)).toBeGreaterThan(
      state.distanceLy * 0.82,
    );
    expect(withinGalaxyStarFootprint(footprint, visible.x, visible.z)).toBe(
      true,
    );
    expect(
      withinGalaxyStarFootprint(footprint, footprint.maxX, footprint.minZ),
    ).toBe(false);
    expect(withinGalaxyStarFootprint(footprint, 0, -2_000)).toBe(false);
    expect(withinGalaxyStarFootprint(footprint, NaN, 0)).toBe(false);
  });

  it('adds a 15% overscan margin and follows bearing and viewport aspect', () => {
    const state = { ...camera, pitchRad: Math.PI / 2, bearingRad: 0 };
    const square = galaxyStarFootprint(
      state,
      { width: 1000, height: 1000 },
      camera.focusLy.y,
      camera.focusLy.y,
    );
    const wide = galaxyStarFootprint(
      state,
      { width: 2100, height: 900 },
      camera.focusLy.y,
      camera.focusLy.y,
    );
    const rotated = galaxyStarFootprint(
      { ...state, bearingRad: Math.PI / 2 },
      { width: 2100, height: 900 },
      camera.focusLy.y,
      camera.focusLy.y,
    );
    const expectedWidth =
      2 * state.distanceLy * Math.tan(GALAXY_CAMERA_FOV_RAD / 2) * 1.15;
    expect(square.maxX - square.minX).toBeCloseTo(expectedWidth);
    expect(wide.maxX - wide.minX).toBeCloseTo((expectedWidth * 21) / 9);
    expect(rotated.maxZ - rotated.minZ).toBeCloseTo(wide.maxX - wide.minX);
    expect(rotated.maxX - rotated.minX).toBeCloseTo(wide.maxZ - wide.minZ);
  });

  it.each([
    { x: -49_800, z: -24_800 },
    { x: 51_800, z: 77_800 },
  ])(
    'clamps wide footprints to the catalogue and the API cube near %o',
    (focus) => {
      const footprint = galaxyStarFootprint(
        { ...camera, focusLy: { ...focus, y: 0 }, distanceLy: 100_000 },
        { width: 2100, height: 900 },
        -2400,
        2400,
      );
      expect(footprint.minX).toBeGreaterThanOrEqual(
        GALAXY_CATALOGUE_BOUNDS.minX,
      );
      expect(footprint.maxX).toBeLessThanOrEqual(GALAXY_CATALOGUE_BOUNDS.maxX);
      expect(footprint.minZ).toBeGreaterThanOrEqual(
        GALAXY_CATALOGUE_BOUNDS.minZ,
      );
      expect(footprint.maxZ).toBeLessThanOrEqual(GALAXY_CATALOGUE_BOUNDS.maxZ);
      expect(footprint.maxX - footprint.minX).toBeLessThanOrEqual(
        GALAXY_STAR_API_CUBE_BOUND_LY,
      );
      expect(footprint.maxZ - footprint.minZ).toBeLessThanOrEqual(
        GALAXY_STAR_API_CUBE_BOUND_LY,
      );
    },
  );

  it('returns an empty footprint outside catalogue coverage or behind the camera', () => {
    const outside = galaxyStarFootprint(
      { ...camera, focusLy: { x: 200_000, y: 0, z: 0 } },
      { width: 1000, height: 1000 },
      -100,
      100,
    );
    expect(outside.polygon).toEqual([]);
    expect(withinGalaxyStarFootprint(outside, outside.minX, outside.minZ)).toBe(
      false,
    );
    const behind = galaxyStarFootprint(
      { ...camera, pitchRad: Math.PI / 2 },
      { width: 1000, height: 1000 },
      2000,
      3000,
    );
    expect(behind.polygon).toEqual([]);
  });

  it('rejects invalid dimensions and slab bounds', () => {
    expect(() =>
      galaxyStarFootprint(camera, { width: 0, height: 1000 }, 0, 100),
    ).toThrow(RangeError);
    expect(() =>
      galaxyStarFootprint(camera, { width: 1000, height: Infinity }, 0, 100),
    ).toThrow(RangeError);
    expect(() =>
      galaxyStarFootprint(camera, { width: 1000, height: 1000 }, 100, 0),
    ).toThrow(RangeError);
  });
});
