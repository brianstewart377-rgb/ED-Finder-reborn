import type {
  Bounds3Ly,
  GalaxySceneContract,
  SpatialContribution,
  Vec3Ly,
} from './contracts';

export const CATALOGUE_DENSITY_LAYER_ID = 'catalogue-density';
export const CATALOGUE_DENSITY_SCHEMA_VERSION = 1;
export const MAX_CATALOGUE_DENSITY_CELLS = 100_000;

export type CatalogueDensityCell = Readonly<{
  index: Readonly<{ x: number; y: number; z: number }>;
  centroidLy: Vec3Ly;
  systemCount: number;
}>;

export type CatalogueDensityPayload = Readonly<{
  schemaVersion: typeof CATALOGUE_DENSITY_SCHEMA_VERSION;
  generationId: string;
  pyramidVersion: string;
  level: number;
  cellSizeLy: number;
  cellOriginLy: Vec3Ly;
  boundsLy: Bounds3Ly;
  sourceSystemCount: number;
  coveredSystemCount: number;
  coverageAsOf: string;
  /** Completeness applies to the queried viewport, or the whole generation. */
  coverageScope?: 'generation' | 'viewport';
  complete: boolean;
  cells: readonly CatalogueDensityCell[];
}>;

export type CatalogueDensitySceneLayer = Readonly<{
  contributionId: string;
  contributionRevision: number;
  layerVersion: number;
  payload: CatalogueDensityPayload;
}>;

const axes = ['x', 'y', 'z'] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function finiteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function safeNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function positiveSafeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0;
}

function readVec3(value: unknown, label: string): Vec3Ly {
  if (!isRecord(value) || axes.some((axis) => !finiteNumber(value[axis]))) {
    throw new Error(`${label} must contain finite x, y and z coordinates`);
  }
  return { x: value.x as number, y: value.y as number, z: value.z as number };
}

function readBounds(value: unknown): Bounds3Ly {
  if (!isRecord(value)) {
    throw new Error('Catalogue density boundsLy must be an object');
  }
  const min = readVec3(value.min, 'Catalogue density boundsLy.min');
  const max = readVec3(value.max, 'Catalogue density boundsLy.max');
  if (axes.some((axis) => min[axis] > max[axis])) {
    throw new Error(
      'Catalogue density bounds must have min <= max on every axis',
    );
  }
  return { min, max };
}

function requiredNonEmptyString(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`${label} must be a non-empty string`);
  }
  return value;
}

function pointInBounds(point: Vec3Ly, bounds: Bounds3Ly): boolean {
  return axes.every(
    (axis) =>
      point[axis] >= bounds.min[axis] && point[axis] <= bounds.max[axis],
  );
}

function readCell(
  value: unknown,
  cellOriginLy: Vec3Ly,
  cellSizeLy: number,
  boundsLy: Bounds3Ly,
): CatalogueDensityCell {
  if (!isRecord(value) || !isRecord(value.index)) {
    throw new Error('Catalogue density cell must include an integer index');
  }
  const index = value.index;
  if (axes.some((axis) => !Number.isSafeInteger(index[axis]))) {
    throw new Error('Catalogue density cell indices must be safe integers');
  }
  if (!positiveSafeInteger(value.systemCount)) {
    throw new Error(
      'Catalogue density systemCount must be a positive safe integer',
    );
  }
  const centroidLy = readVec3(
    value.centroidLy,
    'Catalogue density cell centroidLy',
  );
  if (!pointInBounds(centroidLy, boundsLy)) {
    throw new Error(
      'Catalogue density centroid must lie inside response bounds',
    );
  }

  const typedIndex = {
    x: index.x as number,
    y: index.y as number,
    z: index.z as number,
  };
  const epsilon = Math.max(1, cellSizeLy) * 1e-9;
  for (const axis of axes) {
    const lower = cellOriginLy[axis] + typedIndex[axis] * cellSizeLy;
    const upper = lower + cellSizeLy;
    if (
      centroidLy[axis] < lower - epsilon ||
      centroidLy[axis] > upper + epsilon
    ) {
      throw new Error(
        'Catalogue density centroid must lie inside its declared cell',
      );
    }
  }

  return {
    index: typedIndex,
    centroidLy,
    systemCount: value.systemCount,
  };
}

/**
 * Validate the factual aggregate before it reaches Babylon.
 *
 * The payload contains no colour, opacity, artwork or generated positions.
 * Presentation is derived only after source counts and spatial membership pass.
 */
export function validateCatalogueDensityPayload(
  value: unknown,
): CatalogueDensityPayload {
  if (!isRecord(value)) {
    throw new Error('Catalogue density payload must be an object');
  }
  if (value.schemaVersion !== CATALOGUE_DENSITY_SCHEMA_VERSION) {
    throw new Error(
      `Catalogue density schemaVersion must be ${CATALOGUE_DENSITY_SCHEMA_VERSION}`,
    );
  }
  const generationId = requiredNonEmptyString(
    value.generationId,
    'Catalogue density generationId',
  );
  const pyramidVersion = requiredNonEmptyString(
    value.pyramidVersion,
    'Catalogue density pyramidVersion',
  );
  if (!safeNonNegativeInteger(value.level)) {
    throw new Error(
      'Catalogue density level must be a non-negative safe integer',
    );
  }
  if (!finiteNumber(value.cellSizeLy) || value.cellSizeLy <= 0) {
    throw new Error('Catalogue density cellSizeLy must be finite and positive');
  }
  const cellOriginLy = readVec3(
    value.cellOriginLy,
    'Catalogue density cellOriginLy',
  );
  const boundsLy = readBounds(value.boundsLy);
  if (!safeNonNegativeInteger(value.sourceSystemCount)) {
    throw new Error(
      'Catalogue density sourceSystemCount must be a non-negative safe integer',
    );
  }
  if (!safeNonNegativeInteger(value.coveredSystemCount)) {
    throw new Error(
      'Catalogue density coveredSystemCount must be a non-negative safe integer',
    );
  }
  const coverageAsOf = requiredNonEmptyString(
    value.coverageAsOf,
    'Catalogue density coverageAsOf',
  );
  if (Number.isNaN(Date.parse(coverageAsOf))) {
    throw new Error(
      'Catalogue density coverageAsOf must be an ISO-compatible date',
    );
  }
  if (typeof value.complete !== 'boolean') {
    throw new Error('Catalogue density complete must be boolean');
  }
  const coverageScope = value.coverageScope ?? 'generation';
  if (coverageScope !== 'generation' && coverageScope !== 'viewport') {
    throw new Error(
      'Catalogue density coverageScope must be generation or viewport',
    );
  }
  if (!Array.isArray(value.cells)) {
    throw new Error('Catalogue density cells must be an array');
  }
  if (value.cells.length > MAX_CATALOGUE_DENSITY_CELLS) {
    throw new Error(
      `Catalogue density exceeds ${MAX_CATALOGUE_DENSITY_CELLS} cells`,
    );
  }

  const cells = value.cells.map((cell) =>
    readCell(cell, cellOriginLy, value.cellSizeLy as number, boundsLy),
  );
  const occupied = new Set<string>();
  let reconciledCount = 0;
  for (const cell of cells) {
    const key = `${cell.index.x}:${cell.index.y}:${cell.index.z}`;
    if (occupied.has(key)) {
      throw new Error(`Catalogue density contains duplicate cell ${key}`);
    }
    occupied.add(key);
    reconciledCount += cell.systemCount;
    if (!Number.isSafeInteger(reconciledCount)) {
      throw new Error(
        'Catalogue density reconciled count exceeds safe integer range',
      );
    }
  }
  if (reconciledCount !== value.coveredSystemCount) {
    throw new Error(
      'Catalogue density cell counts must reconcile to coveredSystemCount',
    );
  }
  if (value.coveredSystemCount > value.sourceSystemCount) {
    throw new Error(
      'Catalogue density coveredSystemCount cannot exceed sourceSystemCount',
    );
  }
  if (
    value.complete &&
    coverageScope === 'generation' &&
    value.coveredSystemCount !== value.sourceSystemCount
  ) {
    throw new Error(
      'Complete catalogue density must reconcile exactly to sourceSystemCount',
    );
  }

  return {
    schemaVersion: CATALOGUE_DENSITY_SCHEMA_VERSION,
    generationId,
    pyramidVersion,
    level: value.level,
    cellSizeLy: value.cellSizeLy,
    cellOriginLy,
    boundsLy,
    sourceSystemCount: value.sourceSystemCount,
    coveredSystemCount: value.coveredSystemCount,
    coverageAsOf,
    coverageScope,
    complete: value.complete,
    cells,
  } as CatalogueDensityPayload;
}

export function createCatalogueDensityContribution(
  payload: CatalogueDensityPayload,
  revision: number,
): SpatialContribution {
  const accepted = validateCatalogueDensityPayload(payload);
  return {
    id: 'catalogue-density-base',
    owner: 'CATALOGUE',
    revision,
    layers: [
      {
        id: CATALOGUE_DENSITY_LAYER_ID,
        version: CATALOGUE_DENSITY_SCHEMA_VERSION,
        representation: 'DERIVED',
        payload: accepted,
        bounds: accepted.boundsLy,
        targetCount: accepted.cells.length,
        truncated: !accepted.complete,
      },
    ],
  };
}

/** Locate and validate the one canonical base-density layer in a scene. */
export function catalogueDensitySceneLayer(
  scene: GalaxySceneContract,
): CatalogueDensitySceneLayer | null {
  const matches = scene.contributions.flatMap((contribution) =>
    contribution.layers
      .filter((layer) => layer.id === CATALOGUE_DENSITY_LAYER_ID)
      .map((layer) => ({ contribution, layer })),
  );
  if (matches.length > 1) {
    throw new Error(
      'Galaxy scene must contain at most one catalogue-density layer',
    );
  }
  const match = matches[0];
  if (!match) return null;
  if (match.contribution.owner !== 'CATALOGUE') {
    throw new Error('Catalogue density must be owned by CATALOGUE');
  }
  if (match.layer.representation !== 'DERIVED') {
    throw new Error('Catalogue density must use DERIVED representation');
  }
  if (match.layer.version !== CATALOGUE_DENSITY_SCHEMA_VERSION) {
    throw new Error(
      'Catalogue density layer version does not match its schema',
    );
  }
  const payload = validateCatalogueDensityPayload(match.layer.payload);
  if (match.layer.targetCount !== payload.cells.length) {
    throw new Error(
      'Catalogue density targetCount must equal accepted cell count',
    );
  }
  if (match.layer.truncated !== !payload.complete) {
    throw new Error(
      'Catalogue density truncation must match payload completeness',
    );
  }
  return {
    contributionId: match.contribution.id,
    contributionRevision: match.contribution.revision,
    layerVersion: match.layer.version,
    payload,
  };
}

/** Furthest a catalogue-density splat reaches, in cells. */
export const CATALOGUE_DENSITY_MAXIMUM_SUPPORT_CELLS = 2;

/** Count-only visual transfer. It cannot invent occupied cells or positions. */
export function catalogueDensityRadiusLy(
  systemCount: number,
  maximumSystemCount: number,
  cellSizeLy: number,
): number {
  if (!positiveSafeInteger(systemCount)) {
    throw new Error('Density radius requires a positive system count');
  }
  if (!positiveSafeInteger(maximumSystemCount)) {
    throw new Error('Density radius requires a positive maximum system count');
  }
  if (!finiteNumber(cellSizeLy) || cellSizeLy <= 0) {
    throw new Error('Density radius requires a positive finite cell size');
  }
  const normalized = Math.min(
    1,
    Math.log1p(systemCount) / Math.log1p(maximumSystemCount),
  );
  // Cells sit one cell apart, so a splat that stops short of its neighbours
  // draws a grid of dots. Every splat reaches past its neighbours instead and
  // equal cells sum to an even glow; the count only changes how far a cell's
  // light spreads. Support stays compact (at most 2 cells), so an isolated
  // record is a faint local haze, never a fabricated arm.
  return Math.min(
    cellSizeLy * CATALOGUE_DENSITY_MAXIMUM_SUPPORT_CELLS,
    cellSizeLy * (1.6 + 0.4 * normalized),
  );
}
