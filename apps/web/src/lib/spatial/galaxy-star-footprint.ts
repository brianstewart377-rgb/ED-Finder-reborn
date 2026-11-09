import type { CameraState, Vec3Ly } from './contracts';
import {
  GALAXY_CAMERA_FOV_RAD,
  galaxyCameraPose,
  normalizeGalaxyCamera,
  type GalaxyCameraViewport,
} from './galaxy-camera';
import { GALAXY_STAR_API_CUBE_BOUND_LY } from './galaxy-detail-policy';

export const GALAXY_CATALOGUE_BOUNDS = {
  minX: -50_000,
  maxX: 52_000,
  minZ: -25_000,
  maxZ: 78_000,
} as const;

type PlanePoint = Readonly<{ x: number; z: number }>;
type HalfSpace = Readonly<{ x: number; y: number; z: number; max: number }>;

export type GalaxyStarFootprint = Readonly<{
  /** Counterclockwise XZ polygon, including the overscan margin. */
  polygon: readonly PlanePoint[];
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
}>;

const EPSILON = 1e-8;
const OVERSCAN = 1.15;

function clip(
  polygon: readonly PlanePoint[],
  x: number,
  z: number,
  max: number,
): PlanePoint[] {
  const magnitude = Math.hypot(x, z);
  if (magnitude < 1e-12) return max >= -EPSILON ? [...polygon] : [];
  const nx = x / magnitude;
  const nz = z / magnitude;
  const limit = max / magnitude;
  const clipped: PlanePoint[] = [];
  for (let i = 0; i < polygon.length; i++) {
    const from = polygon[i];
    const to = polygon[(i + 1) % polygon.length];
    const fromDistance = nx * from.x + nz * from.z - limit;
    const toDistance = nx * to.x + nz * to.z - limit;
    const fromInside = fromDistance <= EPSILON;
    const toInside = toDistance <= EPSILON;
    if (fromInside) clipped.push(from);
    if (fromInside !== toInside) {
      const t = fromDistance / (fromDistance - toDistance);
      clipped.push({
        x: from.x + (to.x - from.x) * t,
        z: from.z + (to.z - from.z) * t,
      });
    }
  }
  return clipped;
}

/**
 * Project the frustum's intersection with a Y slab into XZ. Pairing each
 * lower Y bound with each upper bound eliminates Y exactly; unlike four
 * corner rays, this also handles views whose top edge crosses the horizon.
 */
function projectSlab(
  rectangle: readonly PlanePoint[],
  planes: readonly HalfSpace[],
): PlanePoint[] {
  let polygon = [...rectangle];
  const lower: HalfSpace[] = [];
  const upper: HalfSpace[] = [];
  for (const plane of planes) {
    if (Math.abs(plane.y) < 1e-12) {
      polygon = clip(polygon, plane.x, plane.z, plane.max);
    } else {
      const magnitude = Math.abs(plane.y);
      (plane.y < 0 ? lower : upper).push({
        x: plane.x / magnitude,
        y: Math.sign(plane.y),
        z: plane.z / magnitude,
        max: plane.max / magnitude,
      });
    }
  }
  for (const bottom of lower) {
    for (const top of upper) {
      polygon = clip(
        polygon,
        bottom.x + top.x,
        bottom.z + top.z,
        bottom.max + top.max,
      );
    }
  }
  return polygon;
}

/**
 * The visible galactic band, with 15% overscan, within the API's bounded
 * catalogue window. Coordinates remain true LY; no renderer types are needed.
 */
export function galaxyStarFootprint(
  camera: CameraState,
  viewport: GalaxyCameraViewport,
  minY: number,
  maxY: number,
): GalaxyStarFootprint {
  return projectGalaxyFootprint(
    camera,
    viewport,
    minY,
    maxY,
    GALAXY_STAR_API_CUBE_BOUND_LY / 2,
  );
}

/** Visible band with 15% overscan and galaxy bounds, without a catalogue window. */
export function galaxyVisibleFootprint(
  camera: CameraState,
  viewport: GalaxyCameraViewport,
  minY: number,
  maxY: number,
): GalaxyStarFootprint {
  return projectGalaxyFootprint(camera, viewport, minY, maxY, Infinity);
}

function projectGalaxyFootprint(
  camera: CameraState,
  viewport: GalaxyCameraViewport,
  minY: number,
  maxY: number,
  halfSpanLy: number,
): GalaxyStarFootprint {
  const state = normalizeGalaxyCamera(camera);
  if (
    !Number.isFinite(viewport.width) ||
    !Number.isFinite(viewport.height) ||
    viewport.width <= 0 ||
    viewport.height <= 0 ||
    !Number.isFinite(viewport.width / viewport.height) ||
    !Number.isFinite(minY) ||
    !Number.isFinite(maxY) ||
    minY > maxY
  ) {
    throw new RangeError(
      'Star footprint requires a finite viewport and ordered Y band',
    );
  }
  const focus = state.focusLy;
  const bounds = {
    minX: Math.max(-halfSpanLy, GALAXY_CATALOGUE_BOUNDS.minX - focus.x),
    maxX: Math.min(halfSpanLy, GALAXY_CATALOGUE_BOUNDS.maxX - focus.x),
    minZ: Math.max(-halfSpanLy, GALAXY_CATALOGUE_BOUNDS.minZ - focus.z),
    maxZ: Math.min(halfSpanLy, GALAXY_CATALOGUE_BOUNDS.maxZ - focus.z),
  };
  const empty = (): GalaxyStarFootprint => {
    const x = Math.max(
      GALAXY_CATALOGUE_BOUNDS.minX,
      Math.min(GALAXY_CATALOGUE_BOUNDS.maxX, focus.x),
    );
    const z = Math.max(
      GALAXY_CATALOGUE_BOUNDS.minZ,
      Math.min(GALAXY_CATALOGUE_BOUNDS.maxZ, focus.z),
    );
    return { polygon: [], minX: x, maxX: x, minZ: z, maxZ: z };
  };
  if (bounds.minX >= bounds.maxX || bounds.minZ >= bounds.maxZ) return empty();

  const pose = galaxyCameraPose(state);
  const eye = {
    x: pose.positionLy.x - focus.x,
    y: pose.positionLy.y - focus.y,
    z: pose.positionLy.z - focus.z,
  };
  const forward = {
    x: -eye.x / state.distanceLy,
    y: -eye.y / state.distanceLy,
    z: -eye.z / state.distanceLy,
  };
  const right = {
    x: pose.up.y * forward.z - pose.up.z * forward.y,
    y: pose.up.z * forward.x - pose.up.x * forward.z,
    z: pose.up.x * forward.y - pose.up.y * forward.x,
  };
  const verticalTangent = Math.tan(GALAXY_CAMERA_FOV_RAD / 2);
  const horizontalTangent =
    verticalTangent * (viewport.width / viewport.height);
  const dotEye = (normal: Vec3Ly) =>
    normal.x * eye.x + normal.y * eye.y + normal.z * eye.z;
  const side = (axis: Vec3Ly, sign: number, tangent: number): HalfSpace => {
    const perspective = state.projection === 'perspective' ? tangent : 0;
    const normal = {
      x: sign * axis.x - perspective * forward.x,
      y: sign * axis.y - perspective * forward.y,
      z: sign * axis.z - perspective * forward.z,
    };
    return {
      ...normal,
      max:
        dotEye(normal) +
        (state.projection === 'orthographic' ? tangent * state.distanceLy : 0),
    };
  };
  const nearNormal = { x: -forward.x, y: -forward.y, z: -forward.z };
  let polygon = projectSlab(
    [
      { x: bounds.minX, z: bounds.minZ },
      { x: bounds.maxX, z: bounds.minZ },
      { x: bounds.maxX, z: bounds.maxZ },
      { x: bounds.minX, z: bounds.maxZ },
    ],
    [
      side(right, -1, horizontalTangent),
      side(right, 1, horizontalTangent),
      side(pose.up, -1, verticalTangent),
      side(pose.up, 1, verticalTangent),
      { ...nearNormal, max: dotEye(nearNormal) },
      { x: 0, y: -1, z: 0, max: focus.y - minY },
      { x: 0, y: 1, z: 0, max: maxY - focus.y },
    ],
  );
  if (polygon.length < 3) return empty();
  // A convex polygon's vertex mean lies inside it, so this expansion contains
  // the entire visible footprint even when the catalogue clips out the focus.
  const centre = polygon.reduce(
    (sum, point) => ({
      x: sum.x + point.x / polygon.length,
      z: sum.z + point.z / polygon.length,
    }),
    { x: 0, z: 0 },
  );
  polygon = polygon.map((point) => ({
    x: centre.x + (point.x - centre.x) * OVERSCAN,
    z: centre.z + (point.z - centre.z) * OVERSCAN,
  }));
  polygon = clip(polygon, -1, 0, -bounds.minX);
  polygon = clip(polygon, 1, 0, bounds.maxX);
  polygon = clip(polygon, 0, -1, -bounds.minZ);
  polygon = clip(polygon, 0, 1, bounds.maxZ);
  const worldPolygon = polygon.map((point) => ({
    x: point.x + focus.x,
    z: point.z + focus.z,
  }));
  return {
    polygon: worldPolygon,
    minX: Math.min(...worldPolygon.map((point) => point.x)),
    maxX: Math.max(...worldPolygon.map((point) => point.x)),
    minZ: Math.min(...worldPolygon.map((point) => point.z)),
    maxZ: Math.max(...worldPolygon.map((point) => point.z)),
  };
}

/** Inclusive convex-polygon test, shared by viewport assembly and cell culling. */
export function withinGalaxyStarFootprint(
  footprint: GalaxyStarFootprint,
  x: number,
  z: number,
): boolean {
  if (
    !Number.isFinite(x) ||
    !Number.isFinite(z) ||
    footprint.polygon.length < 3
  )
    return false;
  return footprint.polygon.every((from, index, polygon) => {
    const to = polygon[(index + 1) % polygon.length];
    const cross =
      (to.x - from.x) * (z - from.z) - (to.z - from.z) * (x - from.x);
    return (
      cross >= -EPSILON * Math.max(1, Math.hypot(to.x - from.x, to.z - from.z))
    );
  });
}
