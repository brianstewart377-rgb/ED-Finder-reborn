import type { Id64 } from '$lib/domain/id64';
import type {
  CameraState,
  GalaxySystemPoint,
  GalaxySystemsPayload,
  SpatialContribution,
  Vec3Ly,
} from './contracts';
import {
  galaxyDetailMode,
  galaxyLocalStarLimit,
  GALAXY_WIDE_STAR_LIMIT,
  LOCAL_STAR_MIN_RADIUS_LY,
  LOCAL_STAR_MAX_VERTICAL_RADIUS_LY,
  type GalaxyDetailMode,
} from './galaxy-detail-policy';
import type { GalaxyCameraViewport } from './galaxy-camera';
import {
  galaxyStarFootprint,
  type GalaxyStarFootprint,
} from './galaxy-star-footprint';

export type GalaxyStarViewport = Readonly<{
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  minZ: number;
  maxZ: number;
  limit: number;
  cameraDistanceLy: number;
  wide: boolean;
  footprint: GalaxyStarFootprint;
}>;

export type CatalogueViewportSystem = Readonly<{
  id64: Id64;
  name: string;
  x: number;
  y: number;
  z: number;
  mainStarClass?: string | null;
  populated: boolean;
}>;

// ---------------------------------------------------------------------------
// Galactic-disk shaping for the wide sample lane.
//
// The API returns a real-system sample bounded by an axis-aligned box. Drawn
// verbatim that reads as a rectangular slab of points with hard square edges.
// The Milky Way is a disk, so at galaxy scale we keep only the sample that
// falls inside the canonical galactic disk and feather the rim so the edge
// dissolves instead of clipping to a square (or a hard circle). Positions are
// never moved — this is a visibility mask over real coordinates.
// ---------------------------------------------------------------------------

/** Sagittarius A* sits ~25,900 LY galactic-north of Sol along +Z. */
export const GALACTIC_CENTRE_LY = { x: 0, z: 25_900 } as const;
/** Canonical playable-disk radius in light years. */
export const GALACTIC_DISK_RADIUS_LY = 46_000;
const DISK_CORE_FRACTION = 0.86;
const DISK_RIM_FRACTION = 1.08;

/** Deterministic 0..1 hash of a light-year XZ position (stable per render). */
function galaxyPositionHash(x: number, z: number): number {
  const seed = Math.sin(x * 12.9898 + z * 78.233) * 43_758.5453;
  return seed - Math.floor(seed);
}

function sampleByPosition<T>(
  values: readonly T[],
  limit: number,
  position: (value: T) => Vec3Ly,
  identity: (value: T) => string,
): readonly T[] {
  if (limit <= 0) return [];
  if (values.length <= limit) return values;
  // Calculate each hash once. Comparators must not repeat trigonometry over
  // a dense response, and network arrival/id64 order must not decide detail.
  return values
    .map((value) => {
      const point = position(value);
      return {
        value,
        hash: galaxyPositionHash(point.x + point.y * 0.137, point.z),
      };
    })
    .sort(
      (a, b) =>
        a.hash - b.hash || identity(a.value).localeCompare(identity(b.value)),
    )
    .slice(0, limit)
    .map(({ value }) => value);
}

/** A stable sample of one whole cell, bounded before entering the query cache. */
export function sampleCatalogueCellSystems(
  systems: readonly CatalogueViewportSystem[],
  limit: number,
): readonly CatalogueViewportSystem[] {
  return sampleByPosition(
    systems,
    limit,
    (system) => system,
    (system) => system.id64,
  );
}

/** Normalised distance from the galactic centre in the disk plane (1 == rim). */
export function galaxyDiskNormalizedRadius(x: number, z: number): number {
  const dx = (x - GALACTIC_CENTRE_LY.x) / GALACTIC_DISK_RADIUS_LY;
  const dz = (z - GALACTIC_CENTRE_LY.z) / GALACTIC_DISK_RADIUS_LY;
  return Math.hypot(dx, dz);
}

/**
 * True when a wide-sample star should remain visible. Everything inside the
 * core radius is kept; the rim is probabilistically feathered with a
 * position-stable hash so the disk edge softens rather than clipping.
 */
export function withinGalaxyDisk(x: number, z: number): boolean {
  const radius = galaxyDiskNormalizedRadius(x, z);
  if (radius <= DISK_CORE_FRACTION) return true;
  if (radius >= DISK_RIM_FRACTION) return false;
  const keepProbability =
    (DISK_RIM_FRACTION - radius) / (DISK_RIM_FRACTION - DISK_CORE_FRACTION);
  return galaxyPositionHash(x, z) < keepProbability;
}

/**
 * Exact-star detail is bounded to a 15,000 LY API cube. The deliberately
 * descending budget keeps the medium view rich while close inspection sheds
 * clutter. Wide Galaxy structure remains the aggregate catalogue layer.
 */
export function galaxyStarViewport(
  camera: CameraState | null,
  previousMode: GalaxyDetailMode = 'wide',
  viewport: GalaxyCameraViewport = { width: 1, height: 1 },
): GalaxyStarViewport | null {
  if (!camera) return null;
  const wide = galaxyDetailMode(camera.distanceLy, previousMode) === 'wide';
  const verticalRadius = Math.max(
    LOCAL_STAR_MIN_RADIUS_LY,
    Math.min(LOCAL_STAR_MAX_VERTICAL_RADIUS_LY, camera.distanceLy * 0.34),
  );
  // Preserve the existing Y bands and budgets; only the horizontal request
  // follows the actual camera. Aggregated density owns the Galaxy overview.
  const minY = wide ? -2_400 : camera.focusLy.y - verticalRadius;
  const maxY = wide ? 2_400 : camera.focusLy.y + verticalRadius;
  const footprint = galaxyStarFootprint(camera, viewport, minY, maxY);
  return {
    minX: footprint.minX,
    maxX: footprint.maxX,
    minY,
    maxY,
    minZ: footprint.minZ,
    maxZ: footprint.maxZ,
    footprint,
    limit: wide
      ? GALAXY_WIDE_STAR_LIMIT
      : galaxyLocalStarLimit(camera.distanceLy),
    cameraDistanceLy: camera.distanceLy,
    wide,
  };
}

export function createCatalogueStarsContribution(
  systems: readonly CatalogueViewportSystem[],
  revision: number,
  truncated: boolean,
  reservedSystems: readonly GalaxySystemPoint[] = [],
  limit = Infinity,
  previous?: SpatialContribution | null,
): SpatialContribution {
  const reserved = new Map<string, GalaxySystemPoint>();
  const background = new Map<string, GalaxySystemPoint>();
  for (const point of reservedSystems) {
    const existing = reserved.get(point.systemId64);
    reserved.set(point.systemId64, {
      ...point,
      ...existing,
      ...((point.primaryStar || existing?.primaryStar) && {
        primaryStar: { ...point.primaryStar, ...existing?.primaryStar },
      }),
      ...((point.summary || existing?.summary) && {
        summary: { ...point.summary, ...existing?.summary },
      }),
      reserved: true,
    });
  }
  for (const system of systems) {
    const point: GalaxySystemPoint = {
      systemId64: system.id64,
      name: system.name,
      positionLy: { x: system.x, y: system.y, z: system.z },
      ...(system.mainStarClass && {
        primaryStar: { type: system.mainStarClass },
      }),
    };
    const known = reserved.get(system.id64);
    if (known) {
      // Catalogue metadata can fill an unknown star class, but the known
      // target's position and richer domain summary retain precedence.
      reserved.set(system.id64, {
        ...point,
        ...known,
        ...((point.primaryStar || known.primaryStar) && {
          primaryStar: { ...point.primaryStar, ...known.primaryStar },
        }),
      });
    } else if (!background.has(system.id64)) {
      background.set(system.id64, point);
    }
  }
  const backgroundLimit = Math.max(0, Math.floor(limit) - reserved.size);
  const candidates = [...background.values()];
  const points = [
    ...[...reserved.values()].sort((a, b) =>
      a.systemId64.localeCompare(b.systemId64),
    ),
    ...sampleByPosition(
      candidates,
      backgroundLimit,
      (point) => point.positionLy,
      (point) => point.systemId64,
    ),
  ];
  const isTruncated = truncated || candidates.length > backgroundLimit;
  const previousLayer = previous?.layers[0];
  const previousPoints = (
    previousLayer?.payload as GalaxySystemsPayload | undefined
  )?.systems;
  if (
    previous &&
    previousLayer?.truncated === isTruncated &&
    previousPoints?.length === points.length &&
    points.every((point, index) =>
      sameCataloguePoint(point, previousPoints[index]),
    )
  ) {
    return previous;
  }
  return {
    id: 'catalogue-viewport-stars',
    owner: 'CATALOGUE',
    revision,
    layers: [
      {
        id: 'catalogue-systems',
        version: 1,
        representation: 'AUTHORITATIVE',
        payload: { systems: points },
        targetCount: points.length,
        truncated: isTruncated,
      },
    ],
  };
}

function sameCataloguePoint(
  a: GalaxySystemPoint,
  b: GalaxySystemPoint,
): boolean {
  return (
    a.systemId64 === b.systemId64 &&
    a.name === b.name &&
    a.reserved === b.reserved &&
    a.positionLy.x === b.positionLy.x &&
    a.positionLy.y === b.positionLy.y &&
    a.positionLy.z === b.positionLy.z &&
    a.primaryStar?.type === b.primaryStar?.type &&
    a.primaryStar?.subtype === b.primaryStar?.subtype &&
    a.summary?.distanceLy === b.summary?.distanceLy &&
    a.summary?.population === b.summary?.population &&
    a.summary?.primaryEconomy === b.summary?.primaryEconomy &&
    a.summary?.secondaryEconomy === b.summary?.secondaryEconomy &&
    a.summary?.security === b.summary?.security &&
    a.summary?.allegiance === b.summary?.allegiance &&
    a.summary?.government === b.summary?.government
  );
}
