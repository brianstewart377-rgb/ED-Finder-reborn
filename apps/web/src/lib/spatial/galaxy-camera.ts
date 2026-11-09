import type { Bounds3Ly, CameraState, Vec3Ly } from './contracts';

export const GALAXY_CAMERA_FOV_RAD = 0.72;
export const GALAXY_CAMERA_MIN_DISTANCE_LY = 2;
export const GALAXY_CAMERA_MAX_DISTANCE_LY = 250_000;
/**
 * The distance of the app's "local" view of a system: where Explore centres a
 * selected result and where a flight to a selected system ends (never farther
 * than the camera already is).
 */
export const GALAXY_LOCAL_VIEW_DISTANCE_LY = 400;
export const GALAXY_CAMERA_MIN_PITCH_RAD = 0.12;
export const GALAXY_CAMERA_MAX_PITCH_RAD = Math.PI / 2;
export const GALAXY_CAMERA_ZOOM_SENSITIVITY = 0.001;
export const GALAXY_CAMERA_TRANSITION_MS = 500;
/** Safety envelope until catalogue bounds are available; not catalogue coverage. */
export const GALAXY_CAMERA_NAVIGATION_BOUNDS_LY: Bounds3Ly = {
  min: {
    x: -GALAXY_CAMERA_MAX_DISTANCE_LY,
    y: -GALAXY_CAMERA_MAX_DISTANCE_LY,
    z: -GALAXY_CAMERA_MAX_DISTANCE_LY,
  },
  max: {
    x: GALAXY_CAMERA_MAX_DISTANCE_LY,
    y: GALAXY_CAMERA_MAX_DISTANCE_LY,
    z: GALAXY_CAMERA_MAX_DISTANCE_LY,
  },
};

export type GalaxyCameraViewport = Readonly<{ width: number; height: number }>;

/** Sagittarius A*, the centre the galaxy map keeps in view. */
export const GALAXY_NAVIGATION_CENTRE_LY: Vec3Ly = {
  x: 25.21875,
  y: -20.90625,
  z: 25_899.96875,
};
/** Encloses every known system with a margin (Beagle Point is ~39,500 ly out). */
export const GALAXY_NAVIGATION_RADIUS_LY = 50_000;

/** Canvas-local CSS pixels, independent of the renderer's device pixel ratio. */
export type GalaxyCameraPoint = Readonly<{ x: number; y: number }>;
export type GalaxyCameraPinch = readonly [GalaxyCameraPoint, GalaxyCameraPoint];
export type GalaxyCameraPose = Readonly<{
  positionLy: Vec3Ly;
  targetLy: Vec3Ly;
  /** Unit screen-up direction, including at the exact top-down pole. */
  up: Vec3Ly;
}>;

function finite(value: number, name: string): number {
  if (!Number.isFinite(value)) throw new RangeError(`${name} must be finite`);
  return value;
}

function coordinates(value: Vec3Ly): Vec3Ly {
  return {
    x: finite(value.x, 'focus x'),
    y: finite(value.y, 'focus y'),
    z: finite(value.z, 'focus z'),
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function wrapBearing(value: number): number {
  const turn = Math.PI * 2;
  return ((((value + Math.PI) % turn) + turn) % turn) - Math.PI;
}

function validateViewport(viewport: GalaxyCameraViewport): void {
  if (
    !Number.isFinite(viewport.width) ||
    viewport.width <= 0 ||
    !Number.isFinite(viewport.height) ||
    viewport.height <= 0
  ) {
    throw new RangeError(
      'Camera viewport must have finite positive dimensions',
    );
  }
}

/** Navigation bounds affect moved axes only, never factual focus/fit/pose data. */
function translateFocus(
  focus: Vec3Ly,
  delta: Partial<Vec3Ly>,
  bounds: Bounds3Ly = GALAXY_CAMERA_NAVIGATION_BOUNDS_LY,
): Vec3Ly {
  const min = coordinates(bounds.min);
  const max = coordinates(bounds.max);
  if (min.x > max.x || min.y > max.y || min.z > max.z) {
    throw new RangeError('Galaxy camera navigation bounds must be ordered');
  }
  return {
    x: delta.x === undefined ? focus.x : clamp(focus.x + delta.x, min.x, max.x),
    y: delta.y === undefined ? focus.y : clamp(focus.y + delta.y, min.y, max.y),
    z: delta.z === undefined ? focus.z : clamp(focus.z + delta.z, min.z, max.z),
  };
}

/**
 * Coordinates remain canonical Elite light-years. Invalid input fails closed;
 * finite distance/pitch are clamped and bearing is wrapped to [-pi, pi).
 * Pitch measures elevation above the x/z plane: pi/2 is exact top-down.
 */
export function normalizeGalaxyCamera(camera: CameraState): CameraState {
  if (!Number.isSafeInteger(camera.revision) || camera.revision < 0) {
    throw new RangeError('Camera revision must be a non-negative safe integer');
  }
  if (
    camera.projection !== 'perspective' &&
    camera.projection !== 'orthographic'
  ) {
    throw new RangeError('Unsupported Galaxy camera projection');
  }
  return {
    focusLy: coordinates(camera.focusLy),
    distanceLy: clamp(
      finite(camera.distanceLy, 'distance'),
      GALAXY_CAMERA_MIN_DISTANCE_LY,
      GALAXY_CAMERA_MAX_DISTANCE_LY,
    ),
    bearingRad: wrapBearing(finite(camera.bearingRad, 'bearing')),
    pitchRad: clamp(
      finite(camera.pitchRad, 'pitch'),
      GALAXY_CAMERA_MIN_PITCH_RAD,
      GALAXY_CAMERA_MAX_PITCH_RAD,
    ),
    projection: camera.projection,
    revision: camera.revision,
  };
}

/** Bearing zero looks towards +z from -z; positive bearing orbits towards +x. */
export function galaxyCameraPose(camera: CameraState): GalaxyCameraPose {
  const state = normalizeGalaxyCamera(camera);
  const sinBearing = Math.sin(state.bearingRad);
  const cosBearing = Math.cos(state.bearingRad);
  const sinPitch = Math.sin(state.pitchRad);
  // Avoid even a tiny horizontal offset at the pole. A world-Y up vector would
  // be collinear here, so derive screen-up from the same spherical basis.
  const cosPitch =
    state.pitchRad === Math.PI / 2 ? 0 : Math.cos(state.pitchRad);
  const horizontalDistance = state.distanceLy * cosPitch;
  return {
    positionLy: coordinates({
      x: state.focusLy.x + sinBearing * horizontalDistance,
      y: state.focusLy.y + sinPitch * state.distanceLy,
      z: state.focusLy.z - cosBearing * horizontalDistance,
    }),
    targetLy: state.focusLy,
    up: {
      x: -sinBearing * sinPitch,
      y: cosPitch,
      z: cosBearing * sinPitch,
    },
  };
}

/**
 * Move focus screen-right/down by pixels on the plane through focusLy.y.
 * Negate pointer-drag deltas for grab-to-pan. The distance/FOV scale also
 * defines the orthographic focus-plane span; browser adapters must match it.
 */
export function panGalaxyCamera(
  camera: CameraState,
  deltaXPx: number,
  deltaYPx: number,
  viewport: GalaxyCameraViewport,
  bounds?: Bounds3Ly,
): CameraState {
  const state = normalizeGalaxyCamera(camera);
  validateViewport(viewport);
  const lyPerPixel =
    (2 * state.distanceLy * Math.tan(GALAXY_CAMERA_FOV_RAD / 2)) /
    viewport.height;
  const rightLy = finite(deltaXPx, 'horizontal pan') * lyPerPixel;
  const upLy =
    (-finite(deltaYPx, 'vertical pan') * lyPerPixel) / Math.sin(state.pitchRad);
  const sinBearing = Math.sin(state.bearingRad);
  const cosBearing = Math.cos(state.bearingRad);
  const delta = {
    x: cosBearing * rightLy - sinBearing * upLy,
    z: sinBearing * rightLy + cosBearing * upLy,
  };
  return normalizeGalaxyCamera({
    ...state,
    focusLy: bounds
      ? translateFocus(state.focusLy, delta, bounds)
      : {
          x: state.focusLy.x + cosBearing * rightLy - sinBearing * upLy,
          y: state.focusLy.y,
          z: state.focusLy.z + sinBearing * rightLy + cosBearing * upLy,
        },
    revision: state.revision + 1,
  });
}

/** Positive travel raises the focus along galactic Y without changing the orbit. */
export function elevateGalaxyCamera(
  camera: CameraState,
  deltaLy: number,
  bounds?: Bounds3Ly,
): CameraState {
  const state = normalizeGalaxyCamera(camera);
  return normalizeGalaxyCamera({
    ...state,
    focusLy: translateFocus(
      state.focusLy,
      { y: finite(deltaLy, 'elevation delta') },
      bounds,
    ),
    revision: state.revision + 1,
  });
}

/** Accessible toolbar travel uses a tenth of the current range per step. */
export function elevateGalaxyCameraStep(
  camera: CameraState,
  direction: 1 | -1,
  bounds?: Bounds3Ly,
): CameraState {
  const state = normalizeGalaxyCamera(camera);
  if (direction !== 1 && direction !== -1) {
    throw new RangeError('Elevation step direction must be 1 or -1');
  }
  return elevateGalaxyCamera(state, direction * state.distanceLy * 0.1, bounds);
}

/** Scale elevation drag by the current range; positive pixels move up. */
export function elevateGalaxyCameraByPixels(
  camera: CameraState,
  deltaYPx: number,
  viewport: GalaxyCameraViewport,
  bounds?: Bounds3Ly,
): CameraState {
  const state = normalizeGalaxyCamera(camera);
  validateViewport(viewport);
  return elevateGalaxyCamera(
    state,
    (finite(deltaYPx, 'elevation drag') *
      2 *
      state.distanceLy *
      Math.tan(GALAXY_CAMERA_FOV_RAD / 2)) /
      viewport.height,
    bounds,
  );
}

/**
 * Intersect a screen ray with a galactic x/z plane: by default the one through
 * the current focus Y, or `planeYLy` (for example the plane the region fills
 * are drawn on, which an elevated focus is not on). Rays that never reach the
 * plane going forward (above the horizon at shallow pitches) have no
 * intersection, so callers must fall back to unanchored navigation.
 */
export function galaxyCameraPlanePoint(
  camera: CameraState,
  point: GalaxyCameraPoint,
  viewport: GalaxyCameraViewport,
  planeYLy?: number,
): Vec3Ly | null {
  const state = normalizeGalaxyCamera(camera);
  validateViewport(viewport);
  const tangent = Math.tan(GALAXY_CAMERA_FOV_RAD / 2);
  const screenX =
    ((2 * finite(point.x, 'pointer x')) / viewport.width - 1) *
    tangent *
    (viewport.width / viewport.height);
  const screenY =
    (1 - (2 * finite(point.y, 'pointer y')) / viewport.height) * tangent;
  const pose = galaxyCameraPose(state);
  const sinBearing = Math.sin(state.bearingRad);
  const cosBearing = Math.cos(state.bearingRad);
  const sinPitch = Math.sin(state.pitchRad);
  const cosPitch =
    state.pitchRad === Math.PI / 2 ? 0 : Math.cos(state.pitchRad);
  const right = { x: cosBearing, y: 0, z: sinBearing };
  const forward = {
    x: -sinBearing * cosPitch,
    y: -sinPitch,
    z: cosBearing * cosPitch,
  };
  const screenOffset = {
    x: right.x * screenX + pose.up.x * screenY,
    y: pose.up.y * screenY,
    z: right.z * screenX + pose.up.z * screenY,
  };
  const perspective = state.projection === 'perspective';
  const origin = perspective
    ? pose.positionLy
    : {
        x: pose.positionLy.x + screenOffset.x * state.distanceLy,
        y: pose.positionLy.y + screenOffset.y * state.distanceLy,
        z: pose.positionLy.z + screenOffset.z * state.distanceLy,
      };
  const direction = perspective
    ? {
        x: forward.x + screenOffset.x,
        y: forward.y + screenOffset.y,
        z: forward.z + screenOffset.z,
      }
    : forward;
  const planeY =
    planeYLy === undefined ? state.focusLy.y : finite(planeYLy, 'plane y');
  if (Math.abs(direction.y) <= 1e-9) return null;
  const travel = (planeY - origin.y) / direction.y;
  if (travel <= 0 || !Number.isFinite(travel)) return null;
  const x = origin.x + direction.x * travel;
  const z = origin.z + direction.z * travel;
  return Number.isFinite(x) && Number.isFinite(z) ? { x, y: planeY, z } : null;
}

function anchorGalaxyCamera(
  before: CameraState,
  after: CameraState,
  previousPoint: GalaxyCameraPoint,
  currentPoint: GalaxyCameraPoint,
  viewport: GalaxyCameraViewport,
  bounds: Bounds3Ly = GALAXY_CAMERA_NAVIGATION_BOUNDS_LY,
): CameraState {
  const previous = galaxyCameraPlanePoint(before, previousPoint, viewport);
  const current = galaxyCameraPlanePoint(after, currentPoint, viewport);
  if (!previous || !current) {
    // The pointer can cross the horizon while pinching. Continue with the
    // established pan scale without using a backwards/near-parallel ray.
    return {
      ...panGalaxyCamera(
        after,
        previousPoint.x - currentPoint.x,
        previousPoint.y - currentPoint.y,
        viewport,
        bounds,
      ),
      revision: after.revision,
    };
  }
  return normalizeGalaxyCamera({
    ...after,
    focusLy: translateFocus(
      after.focusLy,
      { x: previous.x - current.x, z: previous.z - current.z },
      bounds,
    ),
  });
}

/** Positive wheel delta increases viewing distance without moving focus. */
export function zoomGalaxyCamera(
  camera: CameraState,
  delta: number,
): CameraState {
  const state = normalizeGalaxyCamera(camera);
  const logDistance = clamp(
    Math.log(state.distanceLy) +
      finite(delta, 'zoom delta') * GALAXY_CAMERA_ZOOM_SENSITIVITY,
    Math.log(GALAXY_CAMERA_MIN_DISTANCE_LY),
    Math.log(GALAXY_CAMERA_MAX_DISTANCE_LY),
  );
  return normalizeGalaxyCamera({
    ...state,
    distanceLy: Math.exp(logDistance),
    revision: state.revision + 1,
  });
}

/** Keep the same focus-plane point under the cursor while changing range. */
export function zoomGalaxyCameraTowards(
  camera: CameraState,
  delta: number,
  point: GalaxyCameraPoint,
  viewport: GalaxyCameraViewport,
  bounds?: Bounds3Ly,
): CameraState {
  const state = normalizeGalaxyCamera(camera);
  return anchorGalaxyCamera(
    state,
    zoomGalaxyCamera(state, delta),
    point,
    point,
    viewport,
    bounds,
  );
}

function pinchMidpoint(points: GalaxyCameraPinch): GalaxyCameraPoint {
  return {
    x:
      finite(points[0].x, 'pointer x') / 2 +
      finite(points[1].x, 'pointer x') / 2,
    y:
      finite(points[0].y, 'pointer y') / 2 +
      finite(points[1].y, 'pointer y') / 2,
  };
}

/**
 * Finger separation controls range by its exact reciprocal ratio; midpoint
 * movement grabs the same focus-plane point. Coincident fingers only pan.
 */
export function pinchGalaxyCamera(
  camera: CameraState,
  previous: GalaxyCameraPinch,
  current: GalaxyCameraPinch,
  viewport: GalaxyCameraViewport,
  bounds?: Bounds3Ly,
): CameraState {
  const state = normalizeGalaxyCamera(camera);
  const previousMidpoint = pinchMidpoint(previous);
  const currentMidpoint = pinchMidpoint(current);
  const previousDistance = Math.hypot(
    previous[1].x - previous[0].x,
    previous[1].y - previous[0].y,
  );
  const currentDistance = Math.hypot(
    current[1].x - current[0].x,
    current[1].y - current[0].y,
  );
  const delta =
    previousDistance > 1e-6 && currentDistance > 1e-6
      ? (Math.log(previousDistance) - Math.log(currentDistance)) /
        GALAXY_CAMERA_ZOOM_SENSITIVITY
      : 0;
  return anchorGalaxyCamera(
    state,
    zoomGalaxyCamera(state, delta),
    previousMidpoint,
    currentMidpoint,
    viewport,
    bounds,
  );
}

export function orbitGalaxyCamera(
  camera: CameraState,
  deltaBearingRad: number,
  deltaPitchRad: number,
): CameraState {
  const state = normalizeGalaxyCamera(camera);
  return normalizeGalaxyCamera({
    ...state,
    bearingRad: state.bearingRad + finite(deltaBearingRad, 'bearing delta'),
    pitchRad: state.pitchRad + finite(deltaPitchRad, 'pitch delta'),
    revision: state.revision + 1,
  });
}

/** Top-down preserves bearing and focus so orientation does not unexpectedly spin. */
export function topDownGalaxyCamera(camera: CameraState): CameraState {
  const state = normalizeGalaxyCamera(camera);
  return normalizeGalaxyCamera({
    ...state,
    pitchRad: GALAXY_CAMERA_MAX_PITCH_RAD,
    revision: state.revision + 1,
  });
}

export function focusGalaxyCamera(
  camera: CameraState,
  focusLy: Vec3Ly,
  distanceLy = camera.distanceLy,
): CameraState {
  const state = normalizeGalaxyCamera(camera);
  return normalizeGalaxyCamera({
    ...state,
    focusLy,
    distanceLy,
    revision: state.revision + 1,
  });
}

/** Fit the bounds' enclosing sphere, subject to the navigation distance limit. */
export function fitGalaxyCamera(
  camera: CameraState,
  bounds: Bounds3Ly,
  viewport: GalaxyCameraViewport,
  padding = 1.15,
): CameraState {
  const state = normalizeGalaxyCamera(camera);
  validateViewport(viewport);
  const min = coordinates(bounds.min);
  const max = coordinates(bounds.max);
  if (min.x > max.x || min.y > max.y || min.z > max.z) {
    throw new RangeError('Galaxy camera bounds must be ordered');
  }
  if (!Number.isFinite(padding) || padding < 1) {
    throw new RangeError(
      'Galaxy camera fit padding must be finite and at least one',
    );
  }
  const focusLy = {
    x: min.x / 2 + max.x / 2,
    y: min.y / 2 + max.y / 2,
    z: min.z / 2 + max.z / 2,
  };
  const radius = Math.hypot(
    max.x / 2 - min.x / 2,
    max.y / 2 - min.y / 2,
    max.z / 2 - min.z / 2,
  );
  const verticalHalfFov = GALAXY_CAMERA_FOV_RAD / 2;
  const horizontalHalfFov = Math.atan(
    (Math.tan(verticalHalfFov) * viewport.width) / viewport.height,
  );
  const halfFov = Math.min(verticalHalfFov, horizontalHalfFov);
  const divisor =
    state.projection === 'perspective' ? Math.sin(halfFov) : Math.tan(halfFov);
  return focusGalaxyCamera(
    state,
    focusLy,
    Math.min(GALAXY_CAMERA_MAX_DISTANCE_LY, (radius * padding) / divisor),
  );
}

/**
 * Fit a flat x/z map in an exact top-down view. Unlike the general enclosing
 * sphere fit, this does not reserve empty depth for the corners of a planar
 * source and therefore uses the viewport much more effectively.
 */
export function fitGalaxyPlaneCamera(
  camera: CameraState,
  bounds: Bounds3Ly,
  viewport: GalaxyCameraViewport,
  padding = 1.1,
): CameraState {
  const state = normalizeGalaxyCamera(camera);
  validateViewport(viewport);
  const min = coordinates(bounds.min);
  const max = coordinates(bounds.max);
  if (min.x > max.x || min.z > max.z) {
    throw new RangeError('Galaxy camera bounds must be ordered');
  }
  if (!Number.isFinite(padding) || padding < 1) {
    throw new RangeError(
      'Galaxy camera fit padding must be finite and at least one',
    );
  }
  const halfX = (max.x - min.x) / 2;
  const halfZ = (max.z - min.z) / 2;
  const sinBearing = Math.abs(Math.sin(state.bearingRad));
  const cosBearing = Math.abs(Math.cos(state.bearingRad));
  const horizontalExtent = cosBearing * halfX + sinBearing * halfZ;
  const verticalExtent = sinBearing * halfX + cosBearing * halfZ;
  const verticalTangent = Math.tan(GALAXY_CAMERA_FOV_RAD / 2);
  const horizontalTangent =
    verticalTangent * (viewport.width / viewport.height);
  const distanceLy =
    Math.max(
      horizontalExtent / horizontalTangent,
      verticalExtent / verticalTangent,
      GALAXY_CAMERA_MIN_DISTANCE_LY,
    ) * padding;
  return normalizeGalaxyCamera({
    ...state,
    focusLy: {
      x: min.x / 2 + max.x / 2,
      y: min.y / 2 + max.y / 2,
      z: min.z / 2 + max.z / 2,
    },
    distanceLy,
    pitchRad: GALAXY_CAMERA_MAX_PITCH_RAD,
    revision: state.revision + 1,
  });
}

/**
 * Sample a cancellable transition: sine easing, logarithmic distance and the
 * shortest bearing turn. Endpoints retain their exact normalized states;
 * interior samples use the destination revision and the source projection.
 */
export function interpolateGalaxyCamera(
  from: CameraState,
  to: CameraState,
  progress: number,
): CameraState {
  const source = normalizeGalaxyCamera(from);
  const target = normalizeGalaxyCamera(to);
  const t = clamp(finite(progress, 'transition progress'), 0, 1);
  if (t === 0) return source;
  if (t === 1) return target;
  const eased = 0.5 - 0.5 * Math.cos(Math.PI * t);
  const mix = (a: number, b: number) => a * (1 - eased) + b * eased;
  return normalizeGalaxyCamera({
    focusLy: {
      x: mix(source.focusLy.x, target.focusLy.x),
      y: mix(source.focusLy.y, target.focusLy.y),
      z: mix(source.focusLy.z, target.focusLy.z),
    },
    distanceLy: Math.exp(
      mix(Math.log(source.distanceLy), Math.log(target.distanceLy)),
    ),
    bearingRad:
      source.bearingRad +
      wrapBearing(target.bearingRad - source.bearingRad) * eased,
    pitchRad: mix(source.pitchRad, target.pitchRad),
    projection: source.projection,
    revision: target.revision,
  });
}

function focusOffsetLy(camera: CameraState): number {
  return Math.hypot(
    camera.focusLy.x - GALAXY_NAVIGATION_CENTRE_LY.x,
    camera.focusLy.z - GALAXY_NAVIGATION_CENTRE_LY.z,
  );
}

/**
 * How far the focus may sit from the galactic centre at this range. Once the
 * whole galaxy fits on screen the answer is zero, so a zoomed-out galaxy stays
 * centred; zoomed in, the focus may travel anywhere inside the galaxy.
 */
export function galaxyFocusAllowanceLy(
  camera: CameraState,
  viewport: GalaxyCameraViewport,
): number {
  const state = normalizeGalaxyCamera(camera);
  validateViewport(viewport);
  const halfSpanLy =
    state.distanceLy *
    Math.tan(GALAXY_CAMERA_FOV_RAD / 2) *
    Math.min(1, viewport.width / viewport.height);
  return Math.max(0, GALAXY_NAVIGATION_RADIUS_LY - halfSpanLy);
}

/**
 * Keep the galaxy on screen after a pan, zoom or pinch. Pass the camera the
 * gesture started from: a view that is already further out than allowed (a
 * restored link, a fly-to) is never yanked back in one jump. It can move
 * towards the centre freely, cannot drift further out, and zooming out draws
 * it in only as fast as the allowance shrinks.
 */
export function keepGalaxyInView(
  next: CameraState,
  viewport: GalaxyCameraViewport,
  previous?: CameraState,
): CameraState {
  const state = normalizeGalaxyCamera(next);
  const allowance = galaxyFocusAllowanceLy(state, viewport);
  let limit = allowance;
  if (previous) {
    const shrink = Math.max(
      0,
      galaxyFocusAllowanceLy(previous, viewport) - allowance,
    );
    limit = Math.max(allowance, focusOffsetLy(previous) - shrink);
  }
  const offset = focusOffsetLy(state);
  // The gesture's own camera is already normalised; return it untouched so
  // repeated gestures never accumulate rounding in the bearing.
  if (offset <= limit) return next;
  const scale = limit / offset;
  return {
    ...next,
    focusLy: {
      x:
        GALAXY_NAVIGATION_CENTRE_LY.x +
        (state.focusLy.x - GALAXY_NAVIGATION_CENTRE_LY.x) * scale,
      y: state.focusLy.y,
      z:
        GALAXY_NAVIGATION_CENTRE_LY.z +
        (state.focusLy.z - GALAXY_NAVIGATION_CENTRE_LY.z) * scale,
    },
  };
}
