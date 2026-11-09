import type {
  GalaxySceneContract,
  SpatialContribution,
  Vec3Ly,
} from './contracts.ts';
import { fetchStaticAsset } from './spatial-assets.ts';

export const GALAXY_REGIONS_LAYER_ID = 'galaxy-regions';
export const GALAXY_REGIONS_SCHEMA_VERSION = 1;
export const GALAXY_REGION_COUNT = 42;
export const GALAXY_REGION_GRID_WIDTH = 2_048;
export const GALAXY_REGION_GRID_HEIGHT = 2_048;
export const GALAXY_REGION_BOUNDARY_LIMIT = 25_000;
export const GALAXY_REGION_FILL_RECTANGLE_LIMIT = 25_000;
export const GALAXY_REGION_FILL_Y_LY = -1;
export const GALAXY_REGION_ASSET_BUDGET_BYTES = 4 * 1_048_576;
export const GALAXY_REGION_SOURCE_SHA256 =
  '910142d4deb510d80128509bf68cf3b8de8a2c45006d99d4233ee33d95321152';
export const GALAXY_REGION_ASSET_PATH =
  'map/authoritative-galaxy-regions.v1.json';

export const GALAXY_REGION_NAMES = [
  'Galactic Centre',
  'Empyrean Straits',
  "Ryker's Hope",
  "Odin's Hold",
  'Norma Arm',
  'Arcadian Stream',
  'Izanami',
  'Inner Orion-Perseus Conflux',
  'Inner Scutum-Centaurus Arm',
  'Norma Expanse',
  'Trojan Belt',
  'The Veils',
  "Newton's Vault",
  'The Conduit',
  'Outer Orion-Perseus Conflux',
  'Orion-Cygnus Arm',
  'Temple',
  'Inner Orion Spur',
  "Hawking's Gap",
  "Dryman's Point",
  'Sagittarius-Carina Arm',
  'Mare Somnia',
  'Acheron',
  'Formorian Frontier',
  'Hieronymus Delta',
  'Outer Scutum-Centaurus Arm',
  'Outer Arm',
  "Aquila's Halo",
  'Errant Marches',
  'Perseus Arm',
  'Formidine Rift',
  'Vulcan Gate',
  'Elysian Shore',
  'Sanguineous Rim',
  'Outer Orion Spur',
  "Achilles's Altar",
  'Xibalba',
  "Lyra's Song",
  'Tenebrae',
  'The Abyss',
  "Kepler's Crest",
  'The Void',
] as const;

export type GalaxyRegionRun = readonly [length: number, regionId: number];

export type GalaxyRegionMapSource = Readonly<{
  version: 1;
  origin: Readonly<{ x: number; z: number }>;
  pixel_scale: number;
  regions: readonly string[];
  regionmap: readonly (readonly GalaxyRegionRun[])[];
}>;

export type GalaxyRegionRecord = Readonly<{
  id: number;
  name: string;
  labelLy: Vec3Ly;
}>;

export type GalaxyRegionIdentity = Readonly<{
  id: number;
  name: string;
}>;

export type GalaxyRegionBoundary = Readonly<{
  sourceLy: Vec3Ly;
  targetLy: Vec3Ly;
}>;

export type GalaxyRegionFillGeometry = Readonly<{
  region: GalaxyRegionIdentity;
  positions: Float32Array;
  indices: Uint32Array;
  rectangleCount: number;
  sourceCellCount: number;
}>;

export type GalaxyRegionsPayload = Readonly<{
  schemaVersion: typeof GALAXY_REGIONS_SCHEMA_VERSION;
  source: Readonly<{
    repositoryPath: 'apps/importer/src/data/region_map.json';
    sha256: typeof GALAXY_REGION_SOURCE_SHA256;
    upstreamRevision: '6c1191a58e1e593966f44f16235ab39d1ad24d84';
    width: typeof GALAXY_REGION_GRID_WIDTH;
    height: typeof GALAXY_REGION_GRID_HEIGHT;
    rightsStatus: 'noncommercial_only';
    attribution: string;
  }>;
  regions: readonly GalaxyRegionRecord[];
  boundaries: readonly GalaxyRegionBoundary[];
  lookup: GalaxyRegionMapSource;
}>;

export type GalaxyRegionsSceneLayer = Readonly<{
  contributionId: string;
  contributionRevision: number;
  layerVersion: number;
  payload: GalaxyRegionsPayload;
}>;

const REGION_ATTRIBUTION =
  'Copyright (c) 2020 Ben Peddell. Elite Dangerous material is used by ED-Finder with the permission of Frontier Developments plc for non-commercial purposes; ED-Finder is unofficial and not endorsed by Frontier.';
const axes = ['x', 'y', 'z'] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function readVec3(value: unknown, label: string): Vec3Ly {
  if (!isRecord(value) || axes.some((axis) => !finite(value[axis]))) {
    throw new Error(`${label} must contain finite x, y and z coordinates`);
  }
  return { x: value.x as number, y: value.y as number, z: value.z as number };
}

/** Strictly validate the pinned 2048² RLE source before deriving any geometry. */
export function validateGalaxyRegionMapSource(
  value: unknown,
): GalaxyRegionMapSource {
  if (!isRecord(value) || value.version !== 1) {
    throw new Error('Galaxy region source version must be 1');
  }
  if (
    !isRecord(value.origin) ||
    !finite(value.origin.x) ||
    !finite(value.origin.z) ||
    !finite(value.pixel_scale) ||
    value.pixel_scale <= 0
  ) {
    throw new Error('Galaxy region source coordinates are invalid');
  }
  const regionNames = value.regions;
  if (
    !Array.isArray(regionNames) ||
    regionNames.length !== GALAXY_REGION_COUNT + 1 ||
    regionNames[0] !== '' ||
    GALAXY_REGION_NAMES.some((name, index) => regionNames[index + 1] !== name)
  ) {
    throw new Error(
      'Galaxy region source must contain the exact 42 ID/name pairs',
    );
  }
  if (
    !Array.isArray(value.regionmap) ||
    value.regionmap.length !== GALAXY_REGION_GRID_HEIGHT
  ) {
    throw new Error(
      `Galaxy region source must contain ${GALAXY_REGION_GRID_HEIGHT} rows`,
    );
  }

  const seen = new Set<number>();
  value.regionmap.forEach((row, rowIndex) => {
    if (!Array.isArray(row) || row.length === 0) {
      throw new Error(`Galaxy region source row ${rowIndex} is empty`);
    }
    let width = 0;
    for (const run of row) {
      if (
        !Array.isArray(run) ||
        run.length !== 2 ||
        !Number.isSafeInteger(run[0]) ||
        run[0] <= 0 ||
        !Number.isSafeInteger(run[1]) ||
        run[1] < 0 ||
        run[1] > GALAXY_REGION_COUNT
      ) {
        throw new Error(
          `Galaxy region source row ${rowIndex} has an invalid run`,
        );
      }
      width += run[0];
      seen.add(run[1]);
    }
    if (width !== GALAXY_REGION_GRID_WIDTH) {
      throw new Error(
        `Galaxy region source row ${rowIndex} must decode to ${GALAXY_REGION_GRID_WIDTH}`,
      );
    }
  });
  for (let id = 0; id <= GALAXY_REGION_COUNT; id += 1) {
    if (!seen.has(id)) {
      throw new Error(`Galaxy region source does not contain region ID ${id}`);
    }
  }
  return value as unknown as GalaxyRegionMapSource;
}

export function findGalaxyRegionAt(
  lookup: GalaxyRegionMapSource,
  point: Readonly<{ x: number; z: number }>,
): GalaxyRegionIdentity | null {
  const px = Math.floor((point.x - lookup.origin.x) / lookup.pixel_scale);
  const pz = Math.floor((point.z - lookup.origin.z) / lookup.pixel_scale);
  if (
    px < 0 ||
    px >= GALAXY_REGION_GRID_WIDTH ||
    pz < 0 ||
    pz >= GALAXY_REGION_GRID_HEIGHT
  ) {
    return null;
  }
  const row = lookup.regionmap[pz];
  if (!row) return null;
  let cursor = 0;
  for (const [length, regionId] of row) {
    cursor += length;
    if (px >= cursor) continue;
    if (regionId === 0) return null;
    const name = lookup.regions[regionId];
    return name ? { id: regionId, name } : null;
  }
  return null;
}

type ActiveFillRectangle = {
  regionId: number;
  pxStart: number;
  pxEnd: number;
  pzStart: number;
};

type ClosedFillRectangle = ActiveFillRectangle & { pzEnd: number };

/**
 * Convert every non-zero RLE cell into exact renderer-neutral fill rectangles.
 * Equal spans are merged only across consecutive rows, so the result preserves
 * source membership without smoothing, interpolation or nearest-region fill.
 */
export function buildGalaxyRegionFillGeometry(
  lookup: GalaxyRegionMapSource,
): readonly GalaxyRegionFillGeometry[] {
  const rectanglesByRegion = Array.from(
    { length: GALAXY_REGION_COUNT + 1 },
    () => [] as ClosedFillRectangle[],
  );
  const sourceCellCounts = new Uint32Array(GALAXY_REGION_COUNT + 1);
  const active = new Map<string, ActiveFillRectangle>();
  let rectangleCount = 0;

  const closeRectangle = (
    rectangle: ActiveFillRectangle,
    pzEnd: number,
  ): void => {
    rectanglesByRegion[rectangle.regionId]!.push({ ...rectangle, pzEnd });
    rectangleCount += 1;
    if (rectangleCount > GALAXY_REGION_FILL_RECTANGLE_LIMIT) {
      throw new Error(
        `Galaxy region fill exceeds ${GALAXY_REGION_FILL_RECTANGLE_LIMIT} rectangles`,
      );
    }
  };

  lookup.regionmap.forEach((row, pz) => {
    const present = new Set<string>();
    let px = 0;
    for (const [length, regionId] of row) {
      const pxEnd = px + length;
      if (regionId > 0) {
        const key = `${regionId}:${px}:${pxEnd}`;
        present.add(key);
        sourceCellCounts[regionId] += length;
        if (!active.has(key)) {
          active.set(key, { regionId, pxStart: px, pxEnd, pzStart: pz });
        }
      }
      px = pxEnd;
    }
    for (const [key, rectangle] of active) {
      if (present.has(key)) continue;
      closeRectangle(rectangle, pz);
      active.delete(key);
    }
  });
  for (const rectangle of active.values()) {
    closeRectangle(rectangle, lookup.regionmap.length);
  }

  return GALAXY_REGION_NAMES.map((name, offset) => {
    const regionId = offset + 1;
    const rectangles = rectanglesByRegion[regionId]!;
    const positions = new Float32Array(rectangles.length * 12);
    const indices = new Uint32Array(rectangles.length * 6);
    rectangles.forEach((rectangle, rectangleIndex) => {
      const x0 = lookup.origin.x + rectangle.pxStart * lookup.pixel_scale;
      const x1 = lookup.origin.x + rectangle.pxEnd * lookup.pixel_scale;
      const z0 = lookup.origin.z + rectangle.pzStart * lookup.pixel_scale;
      const z1 = lookup.origin.z + rectangle.pzEnd * lookup.pixel_scale;
      const vertexOffset = rectangleIndex * 12;
      positions.set(
        [
          x0,
          GALAXY_REGION_FILL_Y_LY,
          z0,
          x1,
          GALAXY_REGION_FILL_Y_LY,
          z0,
          x1,
          GALAXY_REGION_FILL_Y_LY,
          z1,
          x0,
          GALAXY_REGION_FILL_Y_LY,
          z1,
        ],
        vertexOffset,
      );
      const baseVertex = rectangleIndex * 4;
      indices.set(
        [
          baseVertex,
          baseVertex + 1,
          baseVertex + 2,
          baseVertex,
          baseVertex + 2,
          baseVertex + 3,
        ],
        rectangleIndex * 6,
      );
    });
    return {
      region: { id: regionId, name },
      positions,
      indices,
      rectangleCount: rectangles.length,
      sourceCellCount: sourceCellCounts[regionId]!,
    };
  });
}

function boundaryPair(left: number, right: number): string {
  return left < right ? `${left}:${right}` : `${right}:${left}`;
}

/** Derive renderer-neutral labels, boundaries and lookup from the pinned source. */
export function buildGalaxyRegionsPayload(
  rawSource: unknown,
  sourceSha256: string,
): GalaxyRegionsPayload {
  if (sourceSha256 !== GALAXY_REGION_SOURCE_SHA256) {
    throw new Error(
      'Galaxy region source hash does not match reviewed provenance',
    );
  }
  const source = validateGalaxyRegionMapSource(rawSource);
  const toGalaxy = (px: number, pz: number): Vec3Ly => ({
    x: px * source.pixel_scale + source.origin.x,
    y: 0,
    z: pz * source.pixel_scale + source.origin.z,
  });
  const stats = Array.from({ length: source.regions.length }, () => ({
    count: 0,
    sumX: 0,
    sumZ: 0,
    spans: [] as Array<{ px: number; pz: number }>,
  }));
  const rows = source.regionmap.map((row, pz) => {
    const decoded = new Uint8Array(GALAXY_REGION_GRID_WIDTH);
    let px = 0;
    for (const [length, regionId] of row) {
      decoded.fill(regionId, px, px + length);
      if (regionId > 0) {
        const region = stats[regionId]!;
        region.count += length;
        region.sumX += length * (px + (length - 1) / 2);
        region.sumZ += length * pz;
        region.spans.push({ px: px + length / 2, pz });
      }
      px += length;
    }
    return decoded;
  });

  const boundaries: GalaxyRegionBoundary[] = [];
  const activeVertical = new Map<string, { px: number; startPz: number }>();
  rows.forEach((row, pz) => {
    const present = new Set<string>();
    for (let px = 1; px < GALAXY_REGION_GRID_WIDTH; px += 1) {
      const left = row[px - 1]!;
      const right = row[px]!;
      if (left === right || (left === 0 && right === 0)) continue;
      const key = `${px}:${boundaryPair(left, right)}`;
      present.add(key);
      if (!activeVertical.has(key)) {
        activeVertical.set(key, { px, startPz: pz });
      }
    }
    activeVertical.forEach((segment, key) => {
      if (present.has(key)) return;
      boundaries.push({
        sourceLy: toGalaxy(segment.px, segment.startPz),
        targetLy: toGalaxy(segment.px, pz),
      });
      activeVertical.delete(key);
    });
  });
  activeVertical.forEach((segment) => {
    boundaries.push({
      sourceLy: toGalaxy(segment.px, segment.startPz),
      targetLy: toGalaxy(segment.px, rows.length),
    });
  });

  for (let pz = 1; pz < rows.length; pz += 1) {
    const previous = rows[pz - 1]!;
    const current = rows[pz]!;
    let runStart = 0;
    let activePair = '';
    for (let px = 0; px <= GALAXY_REGION_GRID_WIDTH; px += 1) {
      const above = px < GALAXY_REGION_GRID_WIDTH ? previous[px]! : 0;
      const below = px < GALAXY_REGION_GRID_WIDTH ? current[px]! : 0;
      const pair =
        px < GALAXY_REGION_GRID_WIDTH &&
        above !== below &&
        (above > 0 || below > 0)
          ? boundaryPair(above, below)
          : '';
      if (pair === activePair) continue;
      if (activePair) {
        boundaries.push({
          sourceLy: toGalaxy(runStart, pz),
          targetLy: toGalaxy(px, pz),
        });
      }
      runStart = px;
      activePair = pair;
    }
  }
  if (boundaries.length > GALAXY_REGION_BOUNDARY_LIMIT) {
    throw new Error(
      `Galaxy region derivation exceeds ${GALAXY_REGION_BOUNDARY_LIMIT} boundaries`,
    );
  }

  const regions = GALAXY_REGION_NAMES.map((name, offset) => {
    const id = offset + 1;
    const region = stats[id]!;
    if (region.count === 0) {
      throw new Error(`Galaxy region ${id} has no source cells`);
    }
    const centroid = {
      px: region.sumX / region.count,
      pz: region.sumZ / region.count,
    };
    const interior = region.spans.reduce<{
      px: number;
      pz: number;
      distance: number;
    }>(
      (best, span) => {
        const distance =
          (span.px - centroid.px) ** 2 + (span.pz - centroid.pz) ** 2;
        return distance < best.distance ? { ...span, distance } : best;
      },
      { px: 0, pz: 0, distance: Number.POSITIVE_INFINITY },
    );
    return { id, name, labelLy: toGalaxy(interior.px, interior.pz) };
  });

  return validateGalaxyRegionsPayload({
    schemaVersion: GALAXY_REGIONS_SCHEMA_VERSION,
    source: {
      repositoryPath: 'apps/importer/src/data/region_map.json',
      sha256: GALAXY_REGION_SOURCE_SHA256,
      upstreamRevision: '6c1191a58e1e593966f44f16235ab39d1ad24d84',
      width: GALAXY_REGION_GRID_WIDTH,
      height: GALAXY_REGION_GRID_HEIGHT,
      rightsStatus: 'noncommercial_only',
      attribution: REGION_ATTRIBUTION,
    },
    regions,
    boundaries,
    lookup: source,
  });
}

export function validateGalaxyRegionsPayload(
  value: unknown,
): GalaxyRegionsPayload {
  if (
    !isRecord(value) ||
    value.schemaVersion !== GALAXY_REGIONS_SCHEMA_VERSION ||
    !isRecord(value.source)
  ) {
    throw new Error('Galaxy regions payload has an invalid schema');
  }
  const sourceReceipt = value.source;
  if (
    sourceReceipt.repositoryPath !== 'apps/importer/src/data/region_map.json' ||
    sourceReceipt.sha256 !== GALAXY_REGION_SOURCE_SHA256 ||
    sourceReceipt.upstreamRevision !==
      '6c1191a58e1e593966f44f16235ab39d1ad24d84' ||
    sourceReceipt.width !== GALAXY_REGION_GRID_WIDTH ||
    sourceReceipt.height !== GALAXY_REGION_GRID_HEIGHT ||
    sourceReceipt.rightsStatus !== 'noncommercial_only' ||
    sourceReceipt.attribution !== REGION_ATTRIBUTION
  ) {
    throw new Error('Galaxy regions payload source receipt is invalid');
  }
  const lookup = validateGalaxyRegionMapSource(value.lookup);
  if (
    !Array.isArray(value.regions) ||
    value.regions.length !== GALAXY_REGION_COUNT
  ) {
    throw new Error(
      `Galaxy regions payload must contain ${GALAXY_REGION_COUNT} regions`,
    );
  }
  const regions = value.regions.map((entry, offset) => {
    if (!isRecord(entry)) throw new Error('Galaxy region record is invalid');
    const id = offset + 1;
    const labelLy = readVec3(entry.labelLy, `Galaxy region ${id} labelLy`);
    if (entry.id !== id || entry.name !== GALAXY_REGION_NAMES[offset]) {
      throw new Error(
        'Galaxy regions payload has an incorrect ID/name mapping',
      );
    }
    const labelRegion = findGalaxyRegionAt(lookup, labelLy);
    if (labelRegion?.id !== id) {
      throw new Error(
        `Galaxy region ${id} label is not inside its lookup area`,
      );
    }
    return { id, name: entry.name as string, labelLy };
  });
  if (
    !Array.isArray(value.boundaries) ||
    value.boundaries.length > GALAXY_REGION_BOUNDARY_LIMIT
  ) {
    throw new Error('Galaxy regions payload has an invalid boundary count');
  }
  const boundaries = value.boundaries.map((boundary) => {
    if (!isRecord(boundary)) {
      throw new Error('Galaxy region boundary is invalid');
    }
    return {
      sourceLy: readVec3(boundary.sourceLy, 'Galaxy region boundary sourceLy'),
      targetLy: readVec3(boundary.targetLy, 'Galaxy region boundary targetLy'),
    };
  });
  return {
    schemaVersion: GALAXY_REGIONS_SCHEMA_VERSION,
    source: sourceReceipt as GalaxyRegionsPayload['source'],
    regions,
    boundaries,
    lookup,
  };
}

export function createGalaxyRegionsContribution(
  payload: GalaxyRegionsPayload,
  revision: number,
): SpatialContribution {
  const accepted = validateGalaxyRegionsPayload(payload);
  return {
    id: 'authoritative-galaxy-regions',
    owner: 'SPATIAL_PLATFORM',
    revision,
    layers: [
      {
        id: GALAXY_REGIONS_LAYER_ID,
        version: GALAXY_REGIONS_SCHEMA_VERSION,
        representation: 'AUTHORITATIVE',
        payload: accepted,
        targetCount: accepted.regions.length,
        truncated: false,
      },
    ],
  };
}

export function galaxyRegionsSceneLayer(
  scene: GalaxySceneContract,
): GalaxyRegionsSceneLayer | null {
  const matches = scene.contributions.flatMap((contribution) =>
    contribution.layers
      .filter((layer) => layer.id === GALAXY_REGIONS_LAYER_ID)
      .map((layer) => ({ contribution, layer })),
  );
  if (matches.length > 1) {
    throw new Error(
      'Galaxy scene must contain at most one galaxy-regions layer',
    );
  }
  const match = matches[0];
  if (!match) return null;
  if (match.contribution.owner !== 'SPATIAL_PLATFORM') {
    throw new Error('Galaxy regions must be owned by SPATIAL_PLATFORM');
  }
  if (match.layer.representation !== 'AUTHORITATIVE') {
    throw new Error('Galaxy regions must use AUTHORITATIVE representation');
  }
  if (
    match.layer.version !== GALAXY_REGIONS_SCHEMA_VERSION ||
    match.layer.truncated
  ) {
    throw new Error('Galaxy regions layer must be complete schema version 1');
  }
  const payload = validateGalaxyRegionsPayload(match.layer.payload);
  if (match.layer.targetCount !== GALAXY_REGION_COUNT) {
    throw new Error(
      `Galaxy regions targetCount must be ${GALAXY_REGION_COUNT}`,
    );
  }
  return {
    contributionId: match.contribution.id,
    contributionRevision: match.contribution.revision,
    layerVersion: match.layer.version,
    payload,
  };
}

export async function fetchAuthoritativeGalaxyRegions(
  baseUrl = import.meta.env.BASE_URL,
): Promise<GalaxyRegionsPayload> {
  const response = await fetchStaticAsset(
    `${baseUrl}${GALAXY_REGION_ASSET_PATH}`,
    { cache: 'no-cache' },
  );
  const body = await response.text();
  if (
    new TextEncoder().encode(body).byteLength > GALAXY_REGION_ASSET_BUDGET_BYTES
  ) {
    throw new Error('Galaxy regions asset exceeds its response budget');
  }
  return validateGalaxyRegionsPayload(JSON.parse(body) as unknown);
}
