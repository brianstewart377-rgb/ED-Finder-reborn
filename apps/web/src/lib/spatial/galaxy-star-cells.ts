import type { CatalogueViewportRequest } from '$lib/api/client';
import type { Vec3Ly } from './contracts';
import {
  GALAXY_STAR_API_CUBE_BOUND_LY,
  LOCAL_STAR_BUDGET_BANDS,
} from './galaxy-detail-policy';
import { GALAXY_CATALOGUE_BOUNDS } from './galaxy-star-footprint';
import type { GalaxyStarViewport } from './galaxy-star-stream';

export const GALAXY_STAR_CELL_CACHE_LIMIT = 64;
export const MAX_GALAXY_STAR_ACTIVE_CELLS = 16;
/** A quarter of the smallest base cell; refinement rounds all three axes. */
export const GALAXY_STAR_REFINEMENT_QUANTUM_LY = 62.5;
const CELL_HALO_FRACTION = 0.15;
const Y_QUANTUM_LY = 250;
const MAX_CELL_SIZE_LY = 10_000;
const BOUNDS_EPSILON_LY = 1e-6;

export type GalaxyStarCell = Readonly<{
  key: string;
  request: CatalogueViewportRequest;
  centre: Vec3Ly;
  cellSizeLy: number;
  /** Refinements replace this base cell in the active query set. */
  parentKey?: string;
}>;

function baseCellSize(viewport: GalaxyStarViewport): number {
  if (viewport.cameraDistanceLy > LOCAL_STAR_BUDGET_BANDS[0].aboveDistanceLy) {
    return 2_500;
  }
  return viewport.cameraDistanceLy > LOCAL_STAR_BUDGET_BANDS[2].aboveDistanceLy
    ? 1_000
    : 250;
}

/** Merge adjacent 2x2 blocks on a stable parent grid, without enlarging them. */
function coalesceCells(cells: readonly GalaxyStarCell[]): GalaxyStarCell[] {
  const groups = new Map<string, GalaxyStarCell[]>();
  for (const cell of cells) {
    const size = cell.cellSizeLy * 2;
    // Keep individual cells where their parent would exceed the API bound.
    const key =
      size <= GALAXY_STAR_API_CUBE_BOUND_LY
        ? `${Math.floor(cell.centre.x / size)}:${Math.floor(cell.centre.z / size)}`
        : cell.key;
    const group = groups.get(key) ?? [];
    group.push(cell);
    groups.set(key, group);
  }
  return [...groups.values()].map((group) => {
    if (group.length === 1) return group[0];
    const first = group[0];
    const request = {
      ...first.request,
      minX: Math.min(...group.map((cell) => cell.request.minX)),
      maxX: Math.max(...group.map((cell) => cell.request.maxX)),
      minZ: Math.min(...group.map((cell) => cell.request.minZ)),
      maxZ: Math.max(...group.map((cell) => cell.request.maxZ)),
    };
    return {
      ...first,
      key: `merged:${first.cellSizeLy}:${request.minX}:${request.maxX}:${request.minY}:${request.maxY}:${request.minZ}:${request.maxZ}:${request.limit}`,
      request,
      centre: {
        x: (request.minX + request.maxX) / 2,
        y: (request.minY + request.maxY) / 2,
        z: (request.minZ + request.maxZ) / 2,
      },
    };
  });
}

/**
 * Quantize requests, never catalogue positions. A small halo permits reuse
 * across grid boundaries; subsequent views reuse the prior complete rectangle
 * until they leave its coverage. Adjacent local cells share a bounded request;
 * wide views use one sample. Each request retains the detail-band limit.
 */
export function planGalaxyStarCells(
  viewport: GalaxyStarViewport,
  previous: readonly GalaxyStarCell[] = [],
): readonly GalaxyStarCell[] {
  const width = viewport.maxX - viewport.minX;
  const depth = viewport.maxZ - viewport.minZ;
  if (width <= 0 || depth <= 0) return [];
  if (
    ![
      viewport.minX,
      viewport.maxX,
      viewport.minY,
      viewport.maxY,
      viewport.minZ,
      viewport.maxZ,
    ].every(Number.isFinite) ||
    width > GALAXY_STAR_API_CUBE_BOUND_LY + BOUNDS_EPSILON_LY ||
    depth > GALAXY_STAR_API_CUBE_BOUND_LY + BOUNDS_EPSILON_LY ||
    viewport.maxY <= viewport.minY ||
    viewport.maxY - viewport.minY >
      GALAXY_STAR_API_CUBE_BOUND_LY - 2 * Y_QUANTUM_LY
  ) {
    throw new RangeError('Cell viewport must fit the bounded catalogue cube');
  }

  if (viewport.wide) {
    // Fractional world coordinates can subtract to just over the exact API
    // bound. Trim only that numerical overshoot before sending one sample.
    const boundedMaximum = (minimum: number, maximum: number) =>
      maximum - minimum > GALAXY_STAR_API_CUBE_BOUND_LY
        ? minimum + GALAXY_STAR_API_CUBE_BOUND_LY - BOUNDS_EPSILON_LY
        : maximum;
    const request = {
      minX: viewport.minX,
      maxX: boundedMaximum(viewport.minX, viewport.maxX),
      minY: viewport.minY,
      maxY: viewport.maxY,
      minZ: viewport.minZ,
      maxZ: boundedMaximum(viewport.minZ, viewport.maxZ),
      limit: viewport.limit,
    };
    const key = `wide:${request.minX}:${request.maxX}:${request.minY}:${request.maxY}:${request.minZ}:${request.maxZ}:${request.limit}`;
    if (previous.length === 1 && previous[0].key === key) return previous;
    return [
      {
        key,
        cellSizeLy: GALAXY_STAR_API_CUBE_BOUND_LY,
        request,
        centre: {
          x: (request.minX + request.maxX) / 2,
          y: (request.minY + request.maxY) / 2,
          z: (request.minZ + request.maxZ) / 2,
        },
      },
    ];
  }

  const paddedWidth = width * (1 + CELL_HALO_FRACTION * 2);
  const paddedDepth = depth * (1 + CELL_HALO_FRACTION * 2);
  let cellSizeLy = baseCellSize(viewport);
  // Account for the extra boundary cell at any grid alignment, so tiny pans
  // cannot change resolution simply by crossing a grid line.
  while (
    (Math.ceil(paddedWidth / cellSizeLy) + 1) *
      (Math.ceil(paddedDepth / cellSizeLy) + 1) >
    MAX_GALAXY_STAR_ACTIVE_CELLS
  ) {
    cellSizeLy = Math.min(cellSizeLy * 2, MAX_CELL_SIZE_LY);
  }

  if (
    previous.length &&
    previous.every(
      (cell) =>
        cell.cellSizeLy === cellSizeLy &&
        cell.request.limit === viewport.limit &&
        cell.request.minY <= viewport.minY &&
        cell.request.maxY >= viewport.maxY,
    ) &&
    Math.min(...previous.map((cell) => cell.request.minX)) <= viewport.minX &&
    Math.max(...previous.map((cell) => cell.request.maxX)) >= viewport.maxX &&
    Math.min(...previous.map((cell) => cell.request.minZ)) <= viewport.minZ &&
    Math.max(...previous.map((cell) => cell.request.maxZ)) >= viewport.maxZ
  ) {
    return previous;
  }

  const minX = Math.max(
    GALAXY_CATALOGUE_BOUNDS.minX,
    viewport.minX - width * CELL_HALO_FRACTION,
  );
  const maxX = Math.min(
    GALAXY_CATALOGUE_BOUNDS.maxX,
    viewport.maxX + width * CELL_HALO_FRACTION,
  );
  const minZ = Math.max(
    GALAXY_CATALOGUE_BOUNDS.minZ,
    viewport.minZ - depth * CELL_HALO_FRACTION,
  );
  const maxZ = Math.min(
    GALAXY_CATALOGUE_BOUNDS.maxZ,
    viewport.maxZ + depth * CELL_HALO_FRACTION,
  );
  const minY = Math.floor(viewport.minY / Y_QUANTUM_LY) * Y_QUANTUM_LY;
  const maxY = Math.ceil(viewport.maxY / Y_QUANTUM_LY) * Y_QUANTUM_LY;
  const cells: GalaxyStarCell[] = [];
  for (
    let xIndex = Math.floor(minX / cellSizeLy);
    xIndex < Math.ceil(maxX / cellSizeLy);
    xIndex++
  ) {
    for (
      let zIndex = Math.floor(minZ / cellSizeLy);
      zIndex < Math.ceil(maxZ / cellSizeLy);
      zIndex++
    ) {
      const request = {
        minX: Math.max(xIndex * cellSizeLy, GALAXY_CATALOGUE_BOUNDS.minX),
        maxX: Math.min((xIndex + 1) * cellSizeLy, GALAXY_CATALOGUE_BOUNDS.maxX),
        minY,
        maxY,
        minZ: Math.max(zIndex * cellSizeLy, GALAXY_CATALOGUE_BOUNDS.minZ),
        maxZ: Math.min((zIndex + 1) * cellSizeLy, GALAXY_CATALOGUE_BOUNDS.maxZ),
        limit: viewport.limit,
      };
      cells.push({
        key: `${cellSizeLy}:${xIndex}:${zIndex}:${minY}:${maxY}:${viewport.limit}`,
        cellSizeLy,
        request,
        centre: {
          x: (request.minX + request.maxX) / 2,
          y: (request.minY + request.maxY) / 2,
          z: (request.minZ + request.maxZ) / 2,
        },
      });
    }
  }
  return coalesceCells(cells);
}

/**
 * A truncated id64-ordered packet cannot supply every smaller view. Replace
 * only such cells with their visible sub-volume, rounded out to 62.5 LY on
 * X/Y/Z. One replacement per base cell keeps the active count and row budgets
 * unchanged. Complete packets keep their identities until coverage is left;
 * truncated refinements can narrow again on zoom-in, down to this quantum.
 */
export function refineGalaxyStarCells(
  viewport: GalaxyStarViewport,
  cells: readonly GalaxyStarCell[],
  packetFor: (cell: GalaxyStarCell) => { truncated: boolean } | undefined,
  previous: readonly GalaxyStarCell[] = [],
): readonly GalaxyStarCell[] {
  return cells.map((cell) => {
    const parent = cell.request;
    if (
      viewport.wide ||
      !packetFor(cell)?.truncated ||
      (viewport.maxX - viewport.minX >= parent.maxX - parent.minX &&
        viewport.maxZ - viewport.minZ >= parent.maxZ - parent.minZ)
    )
      return cell;

    const visible = {
      minX: Math.max(parent.minX, viewport.minX),
      maxX: Math.min(parent.maxX, viewport.maxX),
      minY: Math.max(parent.minY, viewport.minY),
      maxY: Math.min(parent.maxY, viewport.maxY),
      minZ: Math.max(parent.minZ, viewport.minZ),
      maxZ: Math.min(parent.maxZ, viewport.maxZ),
    };
    if (
      visible.minX >= visible.maxX ||
      visible.minY >= visible.maxY ||
      visible.minZ >= visible.maxZ
    )
      return cell;

    const prior = previous.find((entry) => entry.parentKey === cell.key);
    if (
      prior &&
      packetFor(prior)?.truncated === false &&
      prior.request.minX <= visible.minX &&
      prior.request.maxX >= visible.maxX &&
      prior.request.minY <= visible.minY &&
      prior.request.maxY >= visible.maxY &&
      prior.request.minZ <= visible.minZ &&
      prior.request.maxZ >= visible.maxZ
    )
      return prior;

    const quantum = GALAXY_STAR_REFINEMENT_QUANTUM_LY;
    const lower = (value: number) => Math.floor(value / quantum) * quantum;
    const upper = (value: number) => Math.ceil(value / quantum) * quantum;
    const request = {
      minX: Math.max(parent.minX, lower(visible.minX)),
      maxX: Math.min(parent.maxX, upper(visible.maxX)),
      minY: Math.max(parent.minY, lower(visible.minY)),
      maxY: Math.min(parent.maxY, upper(visible.maxY)),
      minZ: Math.max(parent.minZ, lower(visible.minZ)),
      maxZ: Math.min(parent.maxZ, upper(visible.maxZ)),
      limit: parent.limit,
    };
    if (
      request.minX === parent.minX &&
      request.maxX === parent.maxX &&
      request.minY === parent.minY &&
      request.maxY === parent.maxY &&
      request.minZ === parent.minZ &&
      request.maxZ === parent.maxZ
    )
      return cell;
    return {
      ...cell,
      parentKey: cell.key,
      key: `${cell.key}:view:${request.minX}:${request.maxX}:${request.minY}:${request.maxY}:${request.minZ}:${request.maxZ}`,
      request,
      centre: {
        x: (request.minX + request.maxX) / 2,
        y: (request.minY + request.maxY) / 2,
        z: (request.minZ + request.maxZ) / 2,
      },
    };
  });
}

type CellEntry = { cell: GalaxyStarCell; lastUsed: number };

/**
 * Tracks query-cache identities without retaining a second copy of responses.
 * Active cells are pinned. Evict distant inactive cells first, using LRU for
 * equal distances, and return their descriptors so the caller removes queries.
 */
export class GalaxyStarCellCache {
  private readonly entries = new Map<string, CellEntry>();
  private clock = 0;

  constructor(readonly capacity = GALAXY_STAR_CELL_CACHE_LIMIT) {
    if (!Number.isSafeInteger(capacity) || capacity < 1) {
      throw new RangeError('Cell cache capacity must be a positive integer');
    }
  }

  get size(): number {
    return this.entries.size;
  }

  get(key: string): GalaxyStarCell | undefined {
    const entry = this.entries.get(key);
    if (entry) entry.lastUsed = ++this.clock;
    return entry?.cell;
  }

  retain(
    active: readonly GalaxyStarCell[],
    focusLy: Vec3Ly,
  ): readonly GalaxyStarCell[] {
    const activeKeys = new Set(active.map((cell) => cell.key));
    if (activeKeys.size > this.capacity) {
      throw new RangeError('Active cells exceed the cache capacity');
    }
    const lastUsed = ++this.clock;
    for (const cell of active) this.entries.set(cell.key, { cell, lastUsed });
    const distance = ({ cell }: CellEntry): number =>
      (cell.centre.x - focusLy.x) ** 2 +
      (cell.centre.y - focusLy.y) ** 2 +
      (cell.centre.z - focusLy.z) ** 2;
    const candidates = [...this.entries.values()]
      .filter(({ cell }) => !activeKeys.has(cell.key))
      .sort(
        (a, b) =>
          distance(b) - distance(a) ||
          a.lastUsed - b.lastUsed ||
          a.cell.key.localeCompare(b.cell.key),
      );
    const evicted: GalaxyStarCell[] = [];
    for (const { cell } of candidates) {
      if (this.entries.size <= this.capacity) break;
      this.entries.delete(cell.key);
      evicted.push(cell);
    }
    return evicted;
  }
}
