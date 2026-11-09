import type { Bounds3Ly, Vec3Ly } from './contracts';
import {
  CATALOGUE_DENSITY_SCHEMA_VERSION,
  type CatalogueDensityPayload,
  validateCatalogueDensityPayload,
} from './galaxy-density';

export type CatalogueDensityFixturePoint = Readonly<{ positionLy: Vec3Ly }>;

export type CatalogueDensityFixtureOptions = Readonly<{
  generationId: `fixture:${string}`;
  pyramidVersion: string;
  level: number;
  cellSizeLy: number;
  cellOriginLy: Vec3Ly;
  boundsLy: Bounds3Ly;
  coverageAsOf: string;
}>;

type MutableCell = {
  index: { x: number; y: number; z: number };
  count: number;
  sum: { x: number; y: number; z: number };
};

/**
 * Deterministic fixture-only aggregation used by the Review Lab and tests.
 * Production catalogue density must come from the published V3 spatial builder.
 */
export function buildFixtureCatalogueDensity(
  points: readonly CatalogueDensityFixturePoint[],
  options: CatalogueDensityFixtureOptions,
): CatalogueDensityPayload {
  const cells = new Map<string, MutableCell>();
  for (const point of points) {
    const index = {
      x: Math.floor(
        (point.positionLy.x - options.cellOriginLy.x) / options.cellSizeLy,
      ),
      y: Math.floor(
        (point.positionLy.y - options.cellOriginLy.y) / options.cellSizeLy,
      ),
      z: Math.floor(
        (point.positionLy.z - options.cellOriginLy.z) / options.cellSizeLy,
      ),
    };
    const key = `${index.x}:${index.y}:${index.z}`;
    const cell = cells.get(key) ?? {
      index,
      count: 0,
      sum: { x: 0, y: 0, z: 0 },
    };
    cell.count += 1;
    cell.sum.x += point.positionLy.x;
    cell.sum.y += point.positionLy.y;
    cell.sum.z += point.positionLy.z;
    cells.set(key, cell);
  }

  const orderedCells = [...cells.values()]
    .sort(
      (left, right) =>
        left.index.x - right.index.x ||
        left.index.y - right.index.y ||
        left.index.z - right.index.z,
    )
    .map((cell) => ({
      index: cell.index,
      centroidLy: {
        x: cell.sum.x / cell.count,
        y: cell.sum.y / cell.count,
        z: cell.sum.z / cell.count,
      },
      systemCount: cell.count,
    }));

  return validateCatalogueDensityPayload({
    schemaVersion: CATALOGUE_DENSITY_SCHEMA_VERSION,
    generationId: options.generationId,
    pyramidVersion: options.pyramidVersion,
    level: options.level,
    cellSizeLy: options.cellSizeLy,
    cellOriginLy: options.cellOriginLy,
    boundsLy: options.boundsLy,
    sourceSystemCount: points.length,
    coveredSystemCount: points.length,
    coverageAsOf: options.coverageAsOf,
    complete: true,
    cells: orderedCells,
  });
}
