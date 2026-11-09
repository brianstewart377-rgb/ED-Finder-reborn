import type { CameraState, SpatialContribution } from './contracts';
import {
  GALAXY_CAMERA_FOV_RAD,
  GALAXY_CAMERA_NAVIGATION_BOUNDS_LY,
  galaxyCameraPlanePoint,
  normalizeGalaxyCamera,
  type GalaxyCameraViewport,
} from './galaxy-camera';
import {
  CATALOGUE_DENSITY_SCHEMA_VERSION,
  createCatalogueDensityContribution,
  validateCatalogueDensityPayload,
  type CatalogueDensityPayload,
} from './galaxy-density';
import { ApiError, type MapHeatmapPyramidResponse } from '$lib/api/client';

export type GalaxyDensityResult =
  | {
      kind: 'ready';
      payload: CatalogueDensityPayload;
      area: 'complete' | 'empty' | 'truncated';
      levelCellCount: number | null;
    }
  | { kind: 'unavailable'; reason: string }
  | { kind: 'invalid'; reason: string }
  | { kind: 'none' };

/** Transport failures are distinct from a valid, empty published pyramid. */
export function classifyDensityError(
  error: unknown,
): Extract<GalaxyDensityResult, { kind: 'unavailable' | 'invalid' }> {
  if (
    error instanceof SyntaxError ||
    (error instanceof ApiError && error.body instanceof SyntaxError)
  ) {
    return { kind: 'invalid', reason: 'malformed-json' };
  }
  return {
    kind: 'unavailable',
    reason:
      error instanceof ApiError && error.status > 0
        ? `http-${error.status}`
        : 'network',
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function minBy(
  cells: MapHeatmapPyramidResponse['cells'],
  k: 'origin_x_ly' | 'origin_y_ly' | 'origin_z_ly',
) {
  return cells.reduce((m, c) => Math.min(m, c[k]), Number.POSITIVE_INFINITY);
}

function maxBy(
  cells: MapHeatmapPyramidResponse['cells'],
  k: 'origin_x_ly' | 'origin_y_ly' | 'origin_z_ly',
) {
  return cells.reduce((m, c) => Math.max(m, c[k]), Number.NEGATIVE_INFINITY);
}

export function mapHeatmapToDensityPayload(
  value: unknown,
): GalaxyDensityResult {
  // This endpoint uses the legacy lane when no published pyramid is available.
  // It must never be accepted as the catalogue's canonical density layer.
  if (isRecord(value) && value.source === 'legacy-fallback') {
    return { kind: 'none' };
  }
  // The facade casts external JSON to `MapHeatmapResponse`, so a malformed
  // `source: 'pyramid'` body (missing/malformed `bounds` or `cells`) is still
  // possible. Keep every response-shape dereference inside the guarded block
  // so a bad payload fails closed to `invalid` rather than throwing out of the
  // synchronous `$derived` evaluation that maps it.
  try {
    if (!isRecord(value) || value.source !== 'pyramid') {
      throw new Error('Density response must identify its pyramid source');
    }
    if (!isRecord(value.bounds) || !Array.isArray(value.cells)) {
      throw new Error('Density response must include bounds and a cells array');
    }
    for (const bound of [
      'min_x',
      'min_y',
      'min_z',
      'max_x',
      'max_y',
      'max_z',
    ]) {
      const coordinate = value.bounds[bound];
      if (coordinate !== null && !Number.isFinite(coordinate)) {
        throw new Error(
          'Density bounds must contain finite or null coordinates',
        );
      }
    }
    if (typeof value.truncated !== 'boolean') {
      throw new Error('Density response truncated must be boolean');
    }
    if (value.count !== value.cells.length) {
      throw new Error('Density response count must match its cells');
    }
    if (
      !Number.isSafeInteger(value.requested_level) ||
      Number(value.requested_level) < 0 ||
      !Number.isSafeInteger(value.served_level) ||
      Number(value.served_level) < 0 ||
      value.served_level !== value.level
    ) {
      throw new Error(
        'Density response must identify its requested and served levels',
      );
    }
    if (
      (value.coverage !== 'complete' && value.coverage !== 'truncated') ||
      (value.coverage === 'truncated') !== value.truncated
    ) {
      throw new Error('Density coverage must agree with truncation');
    }
    if (
      value.level_cell_count !== null &&
      (!Number.isSafeInteger(value.level_cell_count) ||
        Number(value.level_cell_count) < value.cells.length)
    ) {
      throw new Error('Density level cell count must cover the returned cells');
    }
    if (
      value.coverage === 'complete' &&
      value.level_cell_count !== value.cells.length
    ) {
      throw new Error(
        'Complete density must return every cell at its served level',
      );
    }
    const res = value as unknown as MapHeatmapPyramidResponse;
    const cells = res.cells;
    // Request bounds need not align with the served level's cell grid. Anchor
    // indices to actual cell origins, preserving each factual centroid/cell.
    // An empty published area still passes metadata/count validation.
    const originX = cells.length
      ? minBy(cells, 'origin_x_ly')
      : (res.bounds.min_x ?? 0);
    const originY = cells.length
      ? minBy(cells, 'origin_y_ly')
      : (res.bounds.min_y ?? 0);
    const originZ = cells.length
      ? minBy(cells, 'origin_z_ly')
      : (res.bounds.min_z ?? 0);
    const payload = validateCatalogueDensityPayload({
      schemaVersion: CATALOGUE_DENSITY_SCHEMA_VERSION,
      generationId: res.generation_id,
      pyramidVersion: res.spatial_pyramid_version,
      level: res.level,
      cellSizeLy: res.cell_size_ly,
      cellOriginLy: { x: originX, y: originY, z: originZ },
      boundsLy: {
        min: {
          x: res.bounds.min_x ?? originX,
          y: res.bounds.min_y ?? originY,
          z: res.bounds.min_z ?? originZ,
        },
        max: {
          x:
            res.bounds.max_x ??
            (cells.length
              ? maxBy(cells, 'origin_x_ly') + res.cell_size_ly
              : originX),
          y:
            res.bounds.max_y ??
            (cells.length
              ? maxBy(cells, 'origin_y_ly') + res.cell_size_ly
              : originY),
          z:
            res.bounds.max_z ??
            (cells.length
              ? maxBy(cells, 'origin_z_ly') + res.cell_size_ly
              : originZ),
        },
      },
      sourceSystemCount: res.source_system_count,
      coveredSystemCount: cells.reduce((n, c) => n + c.system_count, 0),
      coverageAsOf: res.coverage_at ?? new Date(0).toISOString(),
      coverageScope: Object.values(res.bounds).some((bound) => bound !== null)
        ? 'viewport'
        : 'generation',
      complete: res.coverage === 'complete',
      cells: cells.map((c) => ({
        index: {
          x: Math.round((c.origin_x_ly - originX) / res.cell_size_ly),
          y: Math.round((c.origin_y_ly - originY) / res.cell_size_ly),
          z: Math.round((c.origin_z_ly - originZ) / res.cell_size_ly),
        },
        centroidLy: {
          x: c.centroid_x_ly,
          y: c.centroid_y_ly,
          z: c.centroid_z_ly,
        },
        systemCount: c.system_count,
      })),
    });
    return {
      kind: 'ready',
      payload,
      area: res.truncated ? 'truncated' : cells.length ? 'complete' : 'empty',
      levelCellCount: res.level_cell_count,
    };
  } catch (error) {
    return {
      kind: 'invalid',
      reason:
        error instanceof Error ? error.message : 'Invalid density payload',
    };
  }
}

export function densityContributionFrom(
  result: GalaxyDensityResult,
  revision: number,
):
  | (Extract<GalaxyDensityResult, { kind: 'ready' }> & {
      contribution: SpatialContribution;
    })
  | Exclude<GalaxyDensityResult, { kind: 'ready' }> {
  return result.kind === 'ready'
    ? {
        ...result,
        contribution: createCatalogueDensityContribution(
          result.payload,
          revision,
        ),
      }
    : result;
}

/**
 * Normalize a raw camera distance into the discrete `voxel_size` requested
 * from `/api/map/heatmap`. Keying the heatmap query by this normalized size
 * (rather than the raw floating-point distance) lets several nearby zoom stops
 * collapse onto one cache entry, so the query's `staleTime` can reuse a result
 * instead of minting a fresh request — and hitting the route's rate limit — on
 * every debounced zoom settle.
 */
export function heatmapVoxelSizeForDistance(distanceLy: number): number {
  return Math.max(200, Math.round(distanceLy / 200));
}

/**
 * Enclose the visible focus plane, including its perspective far edge. Zoom
 * buckets, overscan and outward-snapped bounds reuse nearby settled views
 * without clipping their visible cells. The fallback aspect is used only until
 * the canvas reports its size.
 */
export function heatmapViewportRequest(
  camera: CameraState | null,
  viewport: GalaxyCameraViewport = { width: 1_280, height: 720 },
) {
  const distance = camera?.distanceLy ?? 118_000;
  const focus = camera?.focusLy ?? { x: 0, y: 0, z: 0 };
  if (
    !Number.isFinite(distance) ||
    distance < 0 ||
    Object.values(focus).some((coordinate) => !Number.isFinite(coordinate))
  )
    throw new RangeError(
      'Density camera must have finite coordinates and distance',
    );
  const state = normalizeGalaxyCamera(
    camera ?? {
      focusLy: focus,
      distanceLy: distance,
      bearingRad: 0,
      pitchRad: 0.55,
      projection: 'perspective',
      revision: 0,
    },
  );
  const distanceBucket = Math.max(
    1_000,
    Math.round(state.distanceLy / 1_000) * 1_000,
  );
  const step = Math.max(200, 2 ** Math.floor(Math.log2(distanceBucket / 8)));
  const voxelSize = heatmapVoxelSizeForDistance(state.distanceLy);
  const navigation = GALAXY_CAMERA_NAVIGATION_BOUNDS_LY;
  // The far edge encloses every distance in this zoom bucket at a fixed pose.
  const projectionCamera = { ...state, distanceLy: distanceBucket + 500 };
  const min = { x: focus.x, z: focus.z };
  const max = { ...min };
  for (const [u, v] of [
    [0, 0],
    [0.5, 0],
    [1, 0],
    [0, 0.5],
    [1, 0.5],
    [0, 1],
    [0.5, 1],
    [1, 1],
  ] as const) {
    const point = galaxyCameraPlanePoint(
      projectionCamera,
      { x: u * viewport.width, y: v * viewport.height },
      viewport,
    );
    if (point) {
      for (const axis of ['x', 'z'] as const) {
        min[axis] = Math.min(min[axis], point[axis]);
        max[axis] = Math.max(max[axis], point[axis]);
      }
    } else if (state.projection === 'orthographic') {
      // Orthographic rays can start below the plane at a shallow pitch. They
      // have no perspective horizon; retain the navigation envelope instead.
      Object.assign(min, { x: navigation.min.x, z: navigation.min.z });
      Object.assign(max, { x: navigation.max.x, z: navigation.max.z });
    } else {
      // At a shallow pitch the footprint diverges towards the horizon. Use
      // the limiting ray there (screenY = tan(pitch)), not the missed sky ray:
      // its XZ signs can differ at oblique bearings. Side rays can diverge
      // sideways as well as forwards, so extend each affected world-axis side.
      const screenX =
        (2 * u - 1) *
        Math.tan(GALAXY_CAMERA_FOV_RAD / 2) *
        (viewport.width / viewport.height);
      const sinBearing = Math.sin(state.bearingRad);
      const cosBearing = Math.cos(state.bearingRad);
      const horizonForward = 1 / Math.cos(state.pitchRad);
      const direction = {
        x: cosBearing * screenX - sinBearing * horizonForward,
        z: sinBearing * screenX + cosBearing * horizonForward,
      };
      for (const axis of ['x', 'z'] as const) {
        if (direction[axis] <= 1e-9) min[axis] = navigation.min[axis];
        if (direction[axis] >= -1e-9) max[axis] = navigation.max[axis];
      }
    }
  }
  for (const axis of ['x', 'z'] as const) {
    const margin = (max[axis] - min[axis]) * 0.15 + voxelSize;
    min[axis] = Math.floor((min[axis] - margin) / step) * step;
    max[axis] = Math.ceil((max[axis] + margin) / step) * step;
  }
  // Preserve the vertical-radius policy around the camera's focus elevation.
  const verticalRadius =
    Math.ceil(((distanceBucket + 500) * 1.5 + step / 2) / step) * step;
  const centreY = Math.round(focus.y / step) * step;
  const clamp = (value: number, axis: 'x' | 'y' | 'z') =>
    Math.max(navigation.min[axis], Math.min(navigation.max[axis], value));
  return {
    voxel_size: voxelSize,
    min_systems: 1,
    max_cells: 40_000,
    coverage: 'complete' as const,
    min_x: clamp(min.x, 'x'),
    max_x: clamp(max.x, 'x'),
    min_y: clamp(centreY - verticalRadius, 'y'),
    max_y: clamp(centreY + verticalRadius, 'y'),
    min_z: clamp(min.z, 'z'),
    max_z: clamp(max.z, 'z'),
  };
}

export function heatmapViewportQueryKey(
  request: ReturnType<typeof heatmapViewportRequest>,
) {
  return ['map-heatmap', JSON.stringify(request)] as const;
}

export function densityCoverageLabel(
  result: Extract<GalaxyDensityResult, { kind: 'ready' }>,
): string {
  const size = result.payload.cellSizeLy.toLocaleString();
  if (result.area !== 'truncated') return `Complete at ${size} LY`;
  const shown = result.payload.cells.length.toLocaleString();
  const total = result.levelCellCount?.toLocaleString();
  return `Truncated · ${shown}${total ? ` of ${total}` : ''} cells shown at ${size} LY`;
}
