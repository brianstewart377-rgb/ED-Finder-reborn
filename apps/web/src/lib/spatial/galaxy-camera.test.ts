import { describe, expect, it } from 'vitest';

import type { Bounds3Ly, CameraState } from './contracts';
import {
  elevateGalaxyCamera,
  elevateGalaxyCameraByPixels,
  elevateGalaxyCameraStep,
  fitGalaxyCamera,
  fitGalaxyPlaneCamera,
  focusGalaxyCamera,
  GALAXY_CAMERA_FOV_RAD,
  GALAXY_CAMERA_MAX_DISTANCE_LY,
  GALAXY_CAMERA_MAX_PITCH_RAD,
  GALAXY_CAMERA_MIN_DISTANCE_LY,
  GALAXY_CAMERA_MIN_PITCH_RAD,
  GALAXY_CAMERA_NAVIGATION_BOUNDS_LY,
  GALAXY_NAVIGATION_CENTRE_LY,
  GALAXY_NAVIGATION_RADIUS_LY,
  galaxyCameraPlanePoint,
  galaxyCameraPose,
  galaxyFocusAllowanceLy,
  interpolateGalaxyCamera,
  keepGalaxyInView,
  normalizeGalaxyCamera,
  orbitGalaxyCamera,
  panGalaxyCamera,
  pinchGalaxyCamera,
  topDownGalaxyCamera,
  zoomGalaxyCamera,
  zoomGalaxyCameraTowards,
} from './galaxy-camera';
import { projectGalaxyLabel } from './galaxy-labels';

const camera: CameraState = {
  focusLy: { x: 123.5, y: -45.25, z: 678.125 },
  distanceLy: 1_000,
  bearingRad: 0,
  pitchRad: Math.PI / 4,
  projection: 'perspective',
  revision: 4,
};
const viewport = { width: 1_280, height: 720 };

describe('renderer-neutral Galaxy camera', () => {
  it('clamps navigation values without scaling or rounding factual coordinates', () => {
    expect(
      normalizeGalaxyCamera({
        ...camera,
        distanceLy: -1,
        pitchRad: -1,
        bearingRad: 3 * Math.PI,
      }),
    ).toEqual({
      ...camera,
      distanceLy: GALAXY_CAMERA_MIN_DISTANCE_LY,
      pitchRad: GALAXY_CAMERA_MIN_PITCH_RAD,
      bearingRad: -Math.PI,
    });
    expect(
      normalizeGalaxyCamera({ ...camera, distanceLy: 1e10, pitchRad: 4 }),
    ).toMatchObject({
      distanceLy: GALAXY_CAMERA_MAX_DISTANCE_LY,
      pitchRad: GALAXY_CAMERA_MAX_PITCH_RAD,
    });
  });

  it.each([
    { distanceLy: NaN },
    { pitchRad: Infinity },
    { bearingRad: -Infinity },
    { focusLy: { x: 0, y: NaN, z: 0 } },
    { revision: -1 },
    { revision: 0.5 },
    { projection: 'invented' },
  ])('rejects invalid state %o before it reaches the renderer', (invalid) => {
    expect(() =>
      normalizeGalaxyCamera({ ...camera, ...invalid } as CameraState),
    ).toThrow(RangeError);
  });

  it('maintains true spherical distance and an orthogonal up basis at every bearing', () => {
    for (const bearingRad of [0, Math.PI / 2, Math.PI, -Math.PI / 2]) {
      for (const pitchRad of [
        GALAXY_CAMERA_MIN_PITCH_RAD,
        Math.PI / 4,
        Math.PI / 2,
      ]) {
        const pose = galaxyCameraPose({ ...camera, bearingRad, pitchRad });
        const offset = {
          x: pose.positionLy.x - pose.targetLy.x,
          y: pose.positionLy.y - pose.targetLy.y,
          z: pose.positionLy.z - pose.targetLy.z,
        };
        expect(Math.hypot(offset.x, offset.y, offset.z)).toBeCloseTo(
          camera.distanceLy,
        );
        expect(Math.hypot(pose.up.x, pose.up.y, pose.up.z)).toBeCloseTo(1);
        expect(
          offset.x * pose.up.x + offset.y * pose.up.y + offset.z * pose.up.z,
        ).toBeCloseTo(0);
      }
    }
    const front = galaxyCameraPose(camera);
    expect(front.positionLy.z).toBeLessThan(camera.focusLy.z);
    expect(front.positionLy.x).toBe(camera.focusLy.x);
    const side = galaxyCameraPose({ ...camera, bearingRad: Math.PI / 2 });
    expect(side.positionLy.x).toBeGreaterThan(camera.focusLy.x);
    expect(side.positionLy.z).toBeCloseTo(camera.focusLy.z);
  });

  it('uses exact top-down coordinates and preserves bearing through the pole', () => {
    const topDown = topDownGalaxyCamera({ ...camera, bearingRad: Math.PI / 2 });
    const pose = galaxyCameraPose(topDown);
    expect(topDown.bearingRad).toBeCloseTo(Math.PI / 2);
    expect(topDown.revision).toBe(camera.revision + 1);
    expect(pose.positionLy).toEqual({
      x: camera.focusLy.x,
      y: camera.focusLy.y + camera.distanceLy,
      z: camera.focusLy.z,
    });
    expect(pose.up.x).toBeCloseTo(-1);
    expect(pose.up.y).toBe(0);
    expect(pose.up.z).toBeCloseTo(0);
  });

  it('maps pan to the actual screen axes as bearing rotates and preserves focus height', () => {
    const topDown = { ...camera, pitchRad: Math.PI / 2 };
    const span = 2 * camera.distanceLy * Math.tan(GALAXY_CAMERA_FOV_RAD / 2);
    const right = panGalaxyCamera(topDown, viewport.height, 0, viewport);
    expect(right.focusLy.x - camera.focusLy.x).toBeCloseTo(span);
    expect(right.focusLy.y).toBe(camera.focusLy.y);
    expect(right.focusLy.z).toBeCloseTo(camera.focusLy.z);
    const down = panGalaxyCamera(topDown, 0, viewport.height, viewport);
    expect(down.focusLy.z - camera.focusLy.z).toBeCloseTo(-span);
    const rotatedRight = panGalaxyCamera(
      { ...topDown, bearingRad: Math.PI / 2 },
      viewport.height,
      0,
      viewport,
    );
    expect(rotatedRight.focusLy.x).toBeCloseTo(camera.focusLy.x);
    expect(rotatedRight.focusLy.z - camera.focusLy.z).toBeCloseTo(span);
    const rotatedDown = panGalaxyCamera(
      { ...topDown, bearingRad: Math.PI / 2 },
      0,
      viewport.height,
      viewport,
    );
    expect(rotatedDown.focusLy.x - camera.focusLy.x).toBeCloseTo(span);
    const tiltedDown = panGalaxyCamera(camera, 0, viewport.height, viewport);
    expect(tiltedDown.focusLy.z - camera.focusLy.z).toBeCloseTo(
      -span / Math.sin(camera.pitchRad),
    );
  });

  it('zooms exponentially with reciprocal steps, a fixed focus, and extreme-input bounds', () => {
    const out = zoomGalaxyCamera(camera, 250);
    const back = zoomGalaxyCamera(out, -250);
    expect(out.distanceLy / camera.distanceLy).toBeCloseTo(Math.exp(0.25));
    expect(back.distanceLy).toBeCloseTo(camera.distanceLy);
    expect(back.focusLy).toEqual(camera.focusLy);
    expect(back.revision).toBe(camera.revision + 2);
    expect(zoomGalaxyCamera(camera, Number.MAX_VALUE).distanceLy).toBeCloseTo(
      GALAXY_CAMERA_MAX_DISTANCE_LY,
    );
    expect(zoomGalaxyCamera(camera, -Number.MAX_VALUE).distanceLy).toBe(
      GALAXY_CAMERA_MIN_DISTANCE_LY,
    );
  });

  it('elevates only focus Y, with range-scaled dragging and catalogue bounds', () => {
    const bounds: Bounds3Ly = {
      min: { x: -1_000, y: -100, z: -1_000 },
      max: { x: 1_000, y: 100, z: 1_000 },
    };
    expect(elevateGalaxyCamera(camera, 30, bounds)).toEqual({
      ...camera,
      focusLy: { ...camera.focusLy, y: camera.focusLy.y + 30 },
      revision: camera.revision + 1,
    });
    for (const deltaLy of [Number.MAX_VALUE, -Number.MAX_VALUE]) {
      expect(elevateGalaxyCamera(camera, deltaLy, bounds)).toEqual({
        ...camera,
        focusLy: { ...camera.focusLy, y: deltaLy > 0 ? 100 : -100 },
        revision: camera.revision + 1,
      });
    }
    const dragged = elevateGalaxyCameraByPixels(camera, 30, viewport);
    const farther = elevateGalaxyCameraByPixels(
      { ...camera, distanceLy: camera.distanceLy * 2 },
      30,
      viewport,
    );
    expect(dragged.focusLy.y).toBeGreaterThan(camera.focusLy.y);
    expect(farther.focusLy.y - camera.focusLy.y).toBeCloseTo(
      (dragged.focusLy.y - camera.focusLy.y) * 2,
    );
    expect(elevateGalaxyCamera(camera, Number.MAX_VALUE).focusLy.y).toBe(
      GALAXY_CAMERA_NAVIGATION_BOUNDS_LY.max.y,
    );
    expect(elevateGalaxyCameraStep(camera, 1).focusLy.y).toBe(
      camera.focusLy.y + camera.distanceLy * 0.1,
    );
    expect(elevateGalaxyCameraStep(camera, -1).focusLy.y).toBe(
      camera.focusLy.y - camera.distanceLy * 0.1,
    );
  });

  it.each(['perspective', 'orthographic'] as const)(
    'keeps the cursor plane point projected in place for %s zoom at several pitches and aspects',
    (projection) => {
      for (const pitchRad of [0.12, Math.PI / 4, Math.PI / 2]) {
        for (const view of [
          viewport,
          { width: 400, height: 900 },
          { width: 900, height: 900 },
        ]) {
          for (const bearingRad of [0, 1.1]) {
            const state = { ...camera, projection, pitchRad, bearingRad };
            for (const point of [
              { x: view.width * 0.8, y: view.height * 0.6 },
              { x: view.width * 0.2, y: view.height * 0.45 },
            ]) {
              const anchor = galaxyCameraPlanePoint(state, point, view)!;
              expect(anchor).not.toBeNull();
              expect(anchor.y).toBe(camera.focusLy.y);
              const before = projectGalaxyLabel(anchor, state, view)!;
              expect(Math.abs(before.xPx - point.x)).toBeLessThan(1e-6);
              expect(Math.abs(before.yPx - point.y)).toBeLessThan(1e-6);
              for (const delta of [-600, 450]) {
                const zoomed = zoomGalaxyCameraTowards(
                  state,
                  delta,
                  point,
                  view,
                );
                const after = projectGalaxyLabel(anchor, zoomed, view)!;
                expect(Math.abs(after.xPx - before.xPx)).toBeLessThan(1e-6);
                expect(Math.abs(after.yPx - before.yPx)).toBeLessThan(1e-6);
                expect(zoomed.focusLy.y).toBe(camera.focusLy.y);
                expect(zoomed.bearingRad).toBeCloseTo(bearingRad);
                expect(zoomed.pitchRad).toBe(pitchRad);
                expect(zoomed.revision).toBe(camera.revision + 1);
              }
            }
          }
        }
      }
    },
  );

  it.each(['perspective', 'orthographic'] as const)(
    'scales %s pinch distance by finger ratio and carries the old midpoint to the new one',
    (projection) => {
      const state = { ...camera, projection, bearingRad: 0.7 };
      const previous = [
        { x: 300, y: 400 },
        { x: 500, y: 400 },
      ] as const;
      const current = [
        { x: 260, y: 420 },
        { x: 660, y: 500 },
      ] as const;
      const previousMidpoint = { x: 400, y: 400 };
      const currentMidpoint = { x: 460, y: 460 };
      const anchor = galaxyCameraPlanePoint(state, previousMidpoint, viewport)!;
      const pinched = pinchGalaxyCamera(state, previous, current, viewport);
      expect(pinched.distanceLy).toBeCloseTo(
        (camera.distanceLy * 200) / Math.hypot(400, 80),
        9,
      );
      const projected = projectGalaxyLabel(anchor, pinched, viewport)!;
      expect(Math.abs(projected.xPx - currentMidpoint.x)).toBeLessThan(1e-6);
      expect(Math.abs(projected.yPx - currentMidpoint.y)).toBeLessThan(1e-6);
      expect(pinched.focusLy.y).toBe(camera.focusLy.y);
      expect(pinched.revision).toBe(camera.revision + 1);

      const centered = [
        { x: 200, y: 400 },
        { x: 600, y: 400 },
      ] as const;
      const fixedMidpoint = pinchGalaxyCamera(
        state,
        previous,
        centered,
        viewport,
      );
      const fixedProjection = projectGalaxyLabel(
        anchor,
        fixedMidpoint,
        viewport,
      )!;
      expect(fixedMidpoint.distanceLy).toBeCloseTo(camera.distanceLy / 2, 9);
      expect(Math.abs(fixedProjection.xPx - previousMidpoint.x)).toBeLessThan(
        1e-6,
      );
      expect(Math.abs(fixedProjection.yPx - previousMidpoint.y)).toBeLessThan(
        1e-6,
      );
    },
  );

  it('pans equal-separation and coincident fingers without changing range', () => {
    for (const separation of [0, 200]) {
      const previous = [
        { x: 300, y: 400 },
        { x: 300 + separation, y: 400 },
      ] as const;
      const current = [
        { x: 330, y: 420 },
        { x: 330 + separation, y: 420 },
      ] as const;
      const point = { x: 300 + separation / 2, y: 400 };
      const anchor = galaxyCameraPlanePoint(camera, point, viewport)!;
      const moved = pinchGalaxyCamera(camera, previous, current, viewport);
      const projected = projectGalaxyLabel(anchor, moved, viewport)!;
      expect(moved.distanceLy).toBeCloseTo(camera.distanceLy);
      expect(Math.abs(projected.xPx - point.x - 30)).toBeLessThan(1e-6);
      expect(Math.abs(projected.yPx - point.y - 20)).toBeLessThan(1e-6);
    }
  });

  it('keeps distance and translated focus within bounds while retaining factual coordinates', () => {
    const bounds: Bounds3Ly = {
      min: { x: -10, y: -10, z: -10 },
      max: { x: 10, y: 10, z: 10 },
    };
    const state = {
      ...camera,
      focusLy: { x: 9, y: 3, z: 0 },
      pitchRad: Math.PI / 2,
    };
    const point = { x: viewport.width, y: viewport.height / 2 };
    const zoomed = zoomGalaxyCameraTowards(
      state,
      -1e10,
      point,
      viewport,
      bounds,
    );
    expect(zoomed.distanceLy).toBe(GALAXY_CAMERA_MIN_DISTANCE_LY);
    expect(zoomed.focusLy).toEqual({ x: 10, y: 3, z: 0 });
    const pinched = pinchGalaxyCamera(
      state,
      [
        { x: 0, y: 360 },
        { x: 1e9, y: 360 },
      ],
      [
        { x: 0, y: 360 },
        { x: 1, y: 360 },
      ],
      viewport,
      bounds,
    );
    expect(pinched.distanceLy).toBeCloseTo(GALAXY_CAMERA_MAX_DISTANCE_LY);
    expect(pinched.focusLy.x).toBeLessThanOrEqual(bounds.max.x);
    expect(pinched.focusLy.x).toBeGreaterThanOrEqual(bounds.min.x);
    expect(pinched.focusLy.y).toBe(state.focusLy.y);
    expect(panGalaxyCamera(state, 1e6, 1e6, viewport, bounds).focusLy).toEqual({
      x: 10,
      y: 3,
      z: -10,
    });
    expect(
      focusGalaxyCamera(camera, { x: 1e6, y: 1e6, z: 1e6 }).focusLy,
    ).toEqual({
      x: 1e6,
      y: 1e6,
      z: 1e6,
    });
  });

  it('uses center zoom above the horizon and ordinary pan when a pinch crosses it', () => {
    const state = { ...camera, pitchRad: GALAXY_CAMERA_MIN_PITCH_RAD };
    const point = { x: 400, y: 0 };
    expect(galaxyCameraPlanePoint(state, point, viewport)).toBeNull();
    expect(
      galaxyCameraPlanePoint(
        { ...state, projection: 'orthographic' },
        { x: 400, y: viewport.height },
        viewport,
      ),
    ).toBeNull();
    expect(zoomGalaxyCameraTowards(state, -300, point, viewport)).toEqual(
      zoomGalaxyCamera(state, -300),
    );
    const pinched = pinchGalaxyCamera(
      state,
      [
        { x: 300, y: 0 },
        { x: 500, y: 0 },
      ],
      [
        { x: 330, y: 10 },
        { x: 530, y: 10 },
      ],
      viewport,
    );
    expect(pinched.focusLy.x).toBeCloseTo(
      panGalaxyCamera(state, -30, -10, viewport).focusLy.x,
    );
    expect(pinched.focusLy.z).toBeCloseTo(
      panGalaxyCamera(state, -30, -10, viewport).focusLy.z,
    );
    expect(pinched.revision).toBe(camera.revision + 1);
  });

  it('bounds orbital elevation and wraps bearing without changing the target or distance', () => {
    const orbit = orbitGalaxyCamera(camera, 3 * Math.PI, -2 * Math.PI);
    expect(orbit).toMatchObject({
      focusLy: camera.focusLy,
      distanceLy: camera.distanceLy,
      bearingRad: -Math.PI,
      pitchRad: GALAXY_CAMERA_MIN_PITCH_RAD,
      revision: camera.revision + 1,
    });
  });

  it('interpolates the short 20-degree turn across wrap, with geometric distance and exact endpoints', () => {
    const from = {
      ...camera,
      bearingRad: (170 * Math.PI) / 180,
      distanceLy: 100,
    };
    const to = {
      ...camera,
      focusLy: { x: 500, y: 20, z: -90 },
      bearingRad: (-170 * Math.PI) / 180,
      pitchRad: Math.PI / 2,
      distanceLy: 10_000,
      revision: 8,
    };
    expect(interpolateGalaxyCamera(from, to, -1)).toEqual(
      normalizeGalaxyCamera(from),
    );
    expect(interpolateGalaxyCamera(from, to, 2)).toEqual(
      normalizeGalaxyCamera(to),
    );
    const half = interpolateGalaxyCamera(from, to, 0.5);
    expect(Math.abs(half.bearingRad)).toBeCloseTo(Math.PI);
    expect(half.distanceLy).toBeCloseTo(1_000);
    expect(half.focusLy.x).toBeCloseTo((from.focusLy.x + to.focusLy.x) / 2);
    expect(half.focusLy.y).toBeCloseTo((from.focusLy.y + to.focusLy.y) / 2);
    expect(half.focusLy.z).toBeCloseTo((from.focusLy.z + to.focusLy.z) / 2);
    expect(half.revision).toBe(to.revision);
    expect(interpolateGalaxyCamera(to, from, 0.25).bearingRad).toBeLessThan(
      to.bearingRad,
    );
  });

  it('focuses exactly and fits enclosing geometry for both portrait and landscape views', () => {
    const bounds = {
      min: { x: -10, y: -20, z: -30 },
      max: { x: 30, y: 40, z: 50 },
    };
    const focused = focusGalaxyCamera(camera, bounds.max, 5_000);
    expect(focused).toEqual({
      ...camera,
      focusLy: bounds.max,
      distanceLy: 5_000,
      revision: camera.revision + 1,
    });
    const fit = fitGalaxyCamera(camera, bounds, viewport, 1);
    const portrait = fitGalaxyCamera(
      camera,
      bounds,
      { width: 300, height: 900 },
      1,
    );
    expect(fit.focusLy).toEqual({ x: 10, y: 10, z: 10 });
    expect(fit.distanceLy).toBeCloseTo(
      Math.hypot(20, 30, 40) / Math.sin(GALAXY_CAMERA_FOV_RAD / 2),
    );
    expect(portrait.distanceLy).toBeGreaterThan(fit.distanceLy);
    const orthographic = fitGalaxyCamera(
      { ...camera, projection: 'orthographic' },
      bounds,
      viewport,
      1,
    );
    expect(orthographic.distanceLy).toBeCloseTo(
      Math.hypot(20, 30, 40) / Math.tan(GALAXY_CAMERA_FOV_RAD / 2),
    );
  });

  it('fits a top-down planar source without enclosing empty depth', () => {
    const bounds = {
      min: { x: -50_000, y: 0, z: -50_000 },
      max: { x: 50_000, y: 0, z: 50_000 },
    };
    const planar = fitGalaxyPlaneCamera(
      camera,
      bounds,
      { width: 1_200, height: 600 },
      1.1,
    );
    expect(planar.pitchRad).toBe(Math.PI / 2);
    expect(planar.focusLy).toEqual({ x: 0, y: 0, z: 0 });
    expect(planar.distanceLy).toBeCloseTo(
      (50_000 / Math.tan(GALAXY_CAMERA_FOV_RAD / 2)) * 1.1,
    );
    expect(planar.distanceLy).toBeLessThan(
      fitGalaxyCamera(camera, bounds, { width: 1_200, height: 600 }).distanceLy,
    );
  });

  it('rejects invalid gesture, transition, and fit inputs without mutating prior camera state', () => {
    expect(() =>
      panGalaxyCamera(camera, 0, 1, { width: 0, height: 720 }),
    ).toThrow(RangeError);
    expect(() => panGalaxyCamera(camera, NaN, 0, viewport)).toThrow(RangeError);
    expect(() => zoomGalaxyCamera(camera, Infinity)).toThrow(RangeError);
    expect(() => elevateGalaxyCamera(camera, NaN)).toThrow(RangeError);
    expect(() =>
      elevateGalaxyCameraByPixels(camera, 1, { width: 0, height: 1 }),
    ).toThrow(RangeError);
    expect(() =>
      elevateGalaxyCamera(camera, 1, {
        min: { x: 0, y: 20, z: 0 },
        max: { x: 0, y: 10, z: 0 },
      }),
    ).toThrow(RangeError);
    expect(() =>
      zoomGalaxyCameraTowards(camera, 1, { x: NaN, y: 0 }, viewport),
    ).toThrow(RangeError);
    expect(() =>
      pinchGalaxyCamera(
        camera,
        [
          { x: 0, y: 0 },
          { x: 0, y: 0 },
        ],
        [
          { x: 0, y: 0 },
          { x: Infinity, y: 0 },
        ],
        viewport,
      ),
    ).toThrow(RangeError);
    expect(() => orbitGalaxyCamera(camera, Infinity, 0)).toThrow(RangeError);
    expect(() => interpolateGalaxyCamera(camera, camera, NaN)).toThrow(
      RangeError,
    );
    expect(() =>
      fitGalaxyCamera(
        camera,
        {
          min: { x: 1, y: 0, z: 0 },
          max: { x: 0, y: 0, z: 0 },
        },
        viewport,
      ),
    ).toThrow(RangeError);
    expect(camera.revision).toBe(4);
    expect(camera.focusLy).toEqual({ x: 123.5, y: -45.25, z: 678.125 });
  });
});

describe('keeping the galaxy on screen', () => {
  const centre = GALAXY_NAVIGATION_CENTRE_LY;
  const halfSpan = (distanceLy: number) =>
    distanceLy * Math.tan(GALAXY_CAMERA_FOV_RAD / 2);
  const at = (offsetLy: number, distanceLy: number, y = 7): CameraState => ({
    focusLy: { x: centre.x + offsetLy * 0.6, y, z: centre.z + offsetLy * 0.8 },
    distanceLy,
    bearingRad: 0.3,
    pitchRad: Math.PI / 2,
    projection: 'perspective',
    revision: 9,
  });
  const offsetOf = (state: CameraState) =>
    Math.hypot(state.focusLy.x - centre.x, state.focusLy.z - centre.z);
  // The galaxy just fits on screen at this range.
  const fitDistance =
    GALAXY_NAVIGATION_RADIUS_LY / Math.tan(GALAXY_CAMERA_FOV_RAD / 2);

  it('allows no offset once the whole galaxy fits, and the rest of the radius when zoomed in', () => {
    expect(galaxyFocusAllowanceLy(at(0, fitDistance * 1.2), viewport)).toBe(0);
    expect(galaxyFocusAllowanceLy(at(0, 4_000), viewport)).toBeCloseTo(
      GALAXY_NAVIGATION_RADIUS_LY - halfSpan(4_000),
      6,
    );
    // The shorter side of a portrait screen decides how much is visible.
    expect(
      galaxyFocusAllowanceLy(at(0, 4_000), { width: 360, height: 720 }),
    ).toBeCloseTo(GALAXY_NAVIGATION_RADIUS_LY - halfSpan(4_000) / 2, 6);
  });

  it('keeps a zoomed-out galaxy centred however far it is dragged', () => {
    const wide = at(0, fitDistance * 1.2);
    const dragged = keepGalaxyInView(
      at(30_000, fitDistance * 1.2),
      viewport,
      wide,
    );
    expect(offsetOf(dragged)).toBeCloseTo(0, 6);
    expect(dragged.focusLy.y).toBe(7);
    expect(dragged.revision).toBe(9);
  });

  it('lets a zoomed-in view travel anywhere inside the galaxy but not beyond it', () => {
    const close = at(0, 4_000);
    const allowance = GALAXY_NAVIGATION_RADIUS_LY - halfSpan(4_000);
    const inside = at(allowance - 100, 4_000);
    expect(keepGalaxyInView(inside, viewport, close)).toEqual(inside);
    const beyond = keepGalaxyInView(
      at(allowance + 20_000, 4_000),
      viewport,
      close,
    );
    expect(offsetOf(beyond)).toBeCloseTo(allowance, 6);
    // Pulled straight back towards the centre, not sideways.
    expect(
      (beyond.focusLy.z - centre.z) / (beyond.focusLy.x - centre.x),
    ).toBeCloseTo(0.8 / 0.6, 6);
  });

  it('never yanks a view that already sits further out, but lets it come back', () => {
    const restored = at(30_000, fitDistance * 1.2);
    // Dragging further out is held where it was...
    expect(
      offsetOf(
        keepGalaxyInView(at(40_000, fitDistance * 1.2), viewport, restored),
      ),
    ).toBeCloseTo(30_000, 6);
    // ...dragging back towards the centre is followed exactly...
    const back = at(10_000, fitDistance * 1.2);
    expect(keepGalaxyInView(back, viewport, restored)).toEqual(back);
    // ...and zooming in keeps it exactly where it is.
    const zoomedIn = at(30_000, fitDistance);
    expect(keepGalaxyInView(zoomedIn, viewport, restored)).toEqual(zoomedIn);
  });

  it('draws the view in as you zoom out, only as fast as the allowance shrinks', () => {
    // Already further out than this range allows (12,370 ly here).
    const before = at(20_000, 100_000);
    const shrink =
      galaxyFocusAllowanceLy(before, viewport) -
      galaxyFocusAllowanceLy(at(0, 110_000), viewport);
    const zoomedOut = keepGalaxyInView(at(20_000, 110_000), viewport, before);
    expect(shrink).toBeGreaterThan(0);
    expect(offsetOf(zoomedOut)).toBeCloseTo(20_000 - shrink, 6);
    // Inside the allowance, zooming out leaves the view alone.
    const inside = at(5_000, 110_000);
    expect(keepGalaxyInView(inside, viewport, at(5_000, 100_000))).toBe(inside);
  });

  it('applies the plain limit when there is no earlier camera', () => {
    expect(
      offsetOf(keepGalaxyInView(at(30_000, fitDistance * 1.2), viewport)),
    ).toBe(0);
  });
});
