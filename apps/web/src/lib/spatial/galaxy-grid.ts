import type { CameraState, Vec3Ly } from './contracts';
import { GALAXY_CAMERA_FOV_RAD, normalizeGalaxyCamera } from './galaxy-camera';

export type GalaxyGridLine = readonly [Vec3Ly, Vec3Ly, Vec3Ly];

export type GalaxyReferenceGridSpec = Readonly<{
  key: string;
  nominalPlaneY: 0;
  renderPlaneY: number;
  minorStepLy: number;
  majorStepLy: number;
  bounds: Readonly<{
    minX: number;
    maxX: number;
    minZ: number;
    maxZ: number;
  }>;
  minorLines: readonly GalaxyGridLine[];
  majorLines: readonly GalaxyGridLine[];
  axisLines: readonly GalaxyGridLine[];
}>;

const NICE_INTERVALS = [1, 2, 5, 10] as const;
const TARGET_MINOR_DIVISIONS = 28;
const MAX_LINES_PER_AXIS = 80;

function niceInterval(raw: number): number {
  const exponent = Math.floor(Math.log10(raw));
  const scale = 10 ** exponent;
  const normalized = raw / scale;
  return (
    (NICE_INTERVALS.find((candidate) => candidate >= normalized) ?? 10) * scale
  );
}

function multipleOf(value: number, interval: number): boolean {
  const quotient = value / interval;
  return Math.abs(quotient - Math.round(quotient)) < 1e-8;
}

function coordinateRange(
  focus: number,
  halfExtent: number,
  interval: number,
): readonly [number, number] {
  return [
    Math.floor((focus - halfExtent) / interval) * interval,
    Math.ceil((focus + halfExtent) / interval) * interval,
  ];
}

function lineAlongZ(
  x: number,
  y: number,
  minZ: number,
  maxZ: number,
): GalaxyGridLine {
  return [
    { x, y, z: minZ },
    { x, y, z: minZ / 2 + maxZ / 2 },
    { x, y, z: maxZ },
  ];
}

function lineAlongX(
  z: number,
  y: number,
  minX: number,
  maxX: number,
): GalaxyGridLine {
  return [
    { x: minX, y, z },
    { x: minX / 2 + maxX / 2, y, z },
    { x: maxX, y, z },
  ];
}

/**
 * Build a scale-aware grid on the canonical Galactic Y=0 plane.
 *
 * The camera changes only interval and visible bounds: it never moves systems,
 * projects them onto the plane, or changes their canonical light-year values.
 */
export function galaxyReferenceGrid(
  camera: CameraState,
  viewport: Readonly<{ width: number; height: number }>,
): GalaxyReferenceGridSpec {
  const state = normalizeGalaxyCamera(camera);
  if (
    !Number.isFinite(viewport.width) ||
    viewport.width <= 0 ||
    !Number.isFinite(viewport.height) ||
    viewport.height <= 0
  ) {
    throw new RangeError('Galaxy grid viewport must be finite and positive');
  }

  const verticalSpan =
    2 * state.distanceLy * Math.tan(GALAXY_CAMERA_FOV_RAD / 2);
  const visibleSpan =
    verticalSpan * Math.max(1, viewport.width / viewport.height);
  const minorStepLy = niceInterval(visibleSpan / TARGET_MINOR_DIVISIONS);
  const majorStepLy = minorStepLy * 5;
  const halfExtent = Math.max(visibleSpan * 0.72, majorStepLy * 3);
  const [minX, maxX] = coordinateRange(
    state.focusLy.x,
    halfExtent,
    minorStepLy,
  );
  const [minZ, maxZ] = coordinateRange(
    state.focusLy.z,
    halfExtent,
    minorStepLy,
  );
  const renderPlaneY = -minorStepLy * 0.002;
  const minorLines: GalaxyGridLine[] = [];
  const majorLines: GalaxyGridLine[] = [];
  const axisLines: GalaxyGridLine[] = [];

  for (
    let x = minX, count = 0;
    x <= maxX + minorStepLy * 1e-8 && count < MAX_LINES_PER_AXIS;
    x += minorStepLy, count += 1
  ) {
    const line = lineAlongZ(x, renderPlaneY, minZ, maxZ);
    if (Math.abs(x) < minorStepLy * 1e-8) axisLines.push(line);
    else if (multipleOf(x, majorStepLy)) majorLines.push(line);
    else minorLines.push(line);
  }
  for (
    let z = minZ, count = 0;
    z <= maxZ + minorStepLy * 1e-8 && count < MAX_LINES_PER_AXIS;
    z += minorStepLy, count += 1
  ) {
    const line = lineAlongX(z, renderPlaneY, minX, maxX);
    if (Math.abs(z) < minorStepLy * 1e-8) axisLines.push(line);
    else if (multipleOf(z, majorStepLy)) majorLines.push(line);
    else minorLines.push(line);
  }

  const bounds = { minX, maxX, minZ, maxZ };
  return {
    key: [
      minorStepLy,
      minX,
      maxX,
      minZ,
      maxZ,
      viewport.width,
      viewport.height,
    ].join(':'),
    nominalPlaneY: 0,
    renderPlaneY,
    minorStepLy,
    majorStepLy,
    bounds,
    minorLines,
    majorLines,
    axisLines,
  };
}
