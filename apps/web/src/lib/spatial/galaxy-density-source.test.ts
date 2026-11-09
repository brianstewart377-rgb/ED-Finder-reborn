import { describe, expect, it } from 'vitest';
import {
  classifyDensityError,
  densityCoverageLabel,
  densityContributionFrom,
  heatmapVoxelSizeForDistance,
  heatmapViewportQueryKey,
  heatmapViewportRequest,
  mapHeatmapToDensityPayload,
  type GalaxyDensityResult,
} from './galaxy-density-source';
import { ApiError, type MapHeatmapPyramidResponse } from '$lib/api/client';
import type { CameraState } from './contracts';
import {
  GALAXY_CAMERA_FOV_RAD,
  GALAXY_CAMERA_NAVIGATION_BOUNDS_LY,
  galaxyCameraPlanePoint,
} from './galaxy-camera';

const pyramid: MapHeatmapPyramidResponse = {
  source: 'pyramid',
  generation_id: 'g1',
  spatial_generation_id: 's1',
  spatial_pyramid_version: 'v1',
  source_system_count: 3,
  coverage_at: '2026-09-19T00:00:00Z',
  level: 3,
  requested_level: 3,
  served_level: 3,
  level_cell_count: 2,
  coverage: 'complete',
  cell_size_ly: 100,
  voxel_size: 100,
  bounds: {
    min_x: 0,
    max_x: 200,
    min_y: 0,
    max_y: 100,
    min_z: 0,
    max_z: 100,
  },
  cells: [
    {
      origin_x_ly: 0,
      origin_y_ly: 0,
      origin_z_ly: 0,
      centroid_x_ly: 50,
      centroid_y_ly: 50,
      centroid_z_ly: 50,
      system_count: 2,
    },
    {
      origin_x_ly: 100,
      origin_y_ly: 0,
      origin_z_ly: 0,
      centroid_x_ly: 150,
      centroid_y_ly: 50,
      centroid_z_ly: 50,
      system_count: 1,
    },
  ],
  count: 2,
  max_cells: 40_000,
  truncated: false,
};

function readyResult(value: unknown) {
  const result = mapHeatmapToDensityPayload(value);
  expect(result.kind).toBe('ready');
  if (result.kind !== 'ready') throw new Error('Expected valid density');
  return result;
}

describe('mapHeatmapToDensityPayload', () => {
  it('maps a pyramid response into an explicitly ready validated payload', () => {
    const result = readyResult(pyramid);
    expect(result.area).toBe('complete');
    expect(result.payload.generationId).toBe('g1');
    expect(result.payload.cellSizeLy).toBe(100);
    expect(result.payload.coveredSystemCount).toBe(3);
    expect(result.payload.complete).toBe(true);
    expect(result.payload.cells).toHaveLength(2);
    expect(result.payload.cells[1].index).toEqual({ x: 1, y: 0, z: 0 });
  });

  it('reports no published pyramid when the endpoint returns its fallback', () => {
    expect(
      mapHeatmapToDensityPayload({ source: 'legacy-fallback', cells: [] }),
    ).toEqual({ kind: 'none' });
  });

  it('never accepts a legacy non-pyramid response even when it has cells', () => {
    expect(
      mapHeatmapToDensityPayload({ ...pyramid, source: 'legacy-fallback' }),
    ).toEqual({ kind: 'none' });
  });

  it.each([
    ['null response', null],
    ['missing source', {}],
    ['unrecognized source', { source: 'unknown', cells: [] }],
    ['zero cell size', { ...pyramid, cell_size_ly: 0 }],
    ['missing bounds', { ...pyramid, bounds: undefined }],
    ['missing cells', { ...pyramid, cells: undefined }],
    ['malformed cells', { ...pyramid, cells: 'not-an-array' }],
    ['missing truncation', { ...pyramid, truncated: undefined }],
    ['missing coverage', { ...pyramid, coverage: undefined }],
    ['inconsistent coverage', { ...pyramid, coverage: 'truncated' }],
    ['missing requested level', { ...pyramid, requested_level: undefined }],
    ['different served level', { ...pyramid, served_level: 99 }],
    ['missing level count', { ...pyramid, level_cell_count: undefined }],
    ['incomplete cell list', { ...pyramid, level_cell_count: 3 }],
    ['mismatched cell count', { ...pyramid, count: 0 }],
  ])('reports invalid without throwing for %s', (_label, value) => {
    expect(() => mapHeatmapToDensityPayload(value)).not.toThrow();
    expect(mapHeatmapToDensityPayload(value)).toMatchObject({
      kind: 'invalid',
      reason: expect.any(String),
    });
  });

  it('infers null maximum bounds from all cells while retaining validation', () => {
    const result = readyResult({
      ...pyramid,
      bounds: {
        min_x: 0,
        max_x: null,
        min_y: 0,
        max_y: null,
        min_z: 0,
        max_z: null,
      },
    });
    const payload = result.payload;
    expect(payload.boundsLy.max).toEqual({ x: 200, y: 100, z: 100 });
    for (const cell of payload.cells) {
      expect(cell.centroidLy.x).toBeLessThanOrEqual(payload.boundsLy.max.x);
      expect(cell.centroidLy.y).toBeLessThanOrEqual(payload.boundsLy.max.y);
      expect(cell.centroidLy.z).toBeLessThanOrEqual(payload.boundsLy.max.z);
    }
  });

  it('reports a valid empty published area separately from no pyramid', () => {
    const result = readyResult({
      ...pyramid,
      cells: [],
      count: 0,
      level_cell_count: 0,
      source_system_count: 0,
    });
    expect(result.area).toBe('empty');
    expect(result.payload.cells).toEqual([]);
    expect(result.payload.coveredSystemCount).toBe(0);
    expect(result.payload.complete).toBe(true);
  });

  it('accepts an empty published pyramid with null bounds as zero extent', () => {
    const result = readyResult({
      ...pyramid,
      cells: [],
      count: 0,
      level_cell_count: 0,
      source_system_count: 0,
      bounds: {
        min_x: null,
        max_x: null,
        min_y: null,
        max_y: null,
        min_z: null,
        max_z: null,
      },
    });
    expect(result.area).toBe('empty');
    expect(result.payload.boundsLy).toEqual({
      min: { x: 0, y: 0, z: 0 },
      max: { x: 0, y: 0, z: 0 },
    });
  });

  it.each([
    [
      'unreconciled global source count',
      {
        source_system_count: 3,
        bounds: {
          min_x: null,
          max_x: null,
          min_y: null,
          max_y: null,
          min_z: null,
          max_z: null,
        },
      },
    ],
    ['invalid metadata', { generation_id: '' }],
    ['invalid bounds', { bounds: {} }],
  ])('still rejects empty payloads with %s', (_label, invalidField) => {
    expect(
      mapHeatmapToDensityPayload({
        ...pyramid,
        cells: [],
        count: 0,
        level_cell_count: 0,
        source_system_count: 0,
        ...invalidField,
      }).kind,
    ).toBe('invalid');
  });

  it('reports a valid truncated area without pretending coverage is complete', () => {
    const result = readyResult({
      ...pyramid,
      cells: pyramid.cells.slice(0, 1),
      count: 1,
      truncated: true,
      coverage: 'truncated',
    });
    expect(result.area).toBe('truncated');
    expect(result.payload.complete).toBe(false);
    expect(result.payload.coveredSystemCount).toBe(2);
    expect(result.payload.sourceSystemCount).toBe(3);
  });

  it('accepts complete viewport coverage while preserving the global source count', () => {
    const result = readyResult({ ...pyramid, source_system_count: 1_000 });
    expect(result.payload.coverageScope).toBe('viewport');
    expect(result.payload.complete).toBe(true);
    expect(result.payload.sourceSystemCount).toBe(1_000);
    expect(result.payload.coveredSystemCount).toBe(3);
  });

  it('keeps the served grid aligned when request bounds cut through cells', () => {
    const result = readyResult({
      ...pyramid,
      bounds: {
        min_x: 21,
        max_x: 170,
        min_y: 21,
        max_y: 79,
        min_z: 21,
        max_z: 79,
      },
    });
    expect(result.payload.cellOriginLy).toEqual({ x: 0, y: 0, z: 0 });
    expect(result.payload.cells[1].index).toEqual({ x: 1, y: 0, z: 0 });
    expect(result.payload.boundsLy.min).toEqual({ x: 21, y: 21, z: 21 });
  });
});

describe('density viewport requests', () => {
  const viewport = { width: 1_280, height: 720 };
  const navigation = GALAXY_CAMERA_NAVIGATION_BOUNDS_LY;
  const camera: CameraState = {
    focusLy: { x: 12_000, y: 350, z: -8_000 },
    distanceLy: 10_000,
    bearingRad: 0,
    pitchRad: 0.55,
    projection: 'perspective',
    revision: 0,
  };

  it('covers the far visible strip at 10,000 LY, 0.55 pitch and 16:9', () => {
    const originCamera = { ...camera, focusLy: { x: 0, y: 0, z: 0 } };
    const request = heatmapViewportRequest(originCamera, viewport);
    expect(request).toMatchObject({
      coverage: 'complete',
      min_systems: 1,
      max_cells: 40_000,
      voxel_size: 200,
    });
    const farEdge = galaxyCameraPlanePoint(
      originCamera,
      { x: viewport.width / 2, y: 0 },
      viewport,
    );
    expect(farEdge?.z).toBeCloseTo(18_652.774, 3);
    expect(request.max_z).toBeGreaterThanOrEqual(18_653);
    expect(request.max_z).toBeLessThan(navigation.max.z);
    expect(request.min_y).toBeLessThan(-camera.distanceLy);
    expect(request.max_y).toBeGreaterThan(camera.distanceLy);
  });

  it.each([
    [0.55, 0, 16 / 9, 'perspective'],
    [0.55, 0.8, 9 / 16, 'perspective'],
    [0.37, -0.7, 16 / 9, 'perspective'],
    [0.12, 0.58, 16 / 9, 'perspective'],
    [Math.PI / 2, Math.PI / 3, 4, 'perspective'],
    [0.55, 0.8, 16 / 9, 'orthographic'],
    [0.12, -0.8, 16 / 9, 'orthographic'],
  ] as const)(
    'encloses the visible plane at pitch %s, bearing %s, aspect %s, %s',
    (pitchRad, bearingRad, aspect, projection) => {
      const view = { width: 720 * aspect, height: 720 };
      const state = { ...camera, pitchRad, bearingRad, projection };
      const request = heatmapViewportRequest(state, view);
      // Sample the interior too, especially near shallow-pitch horizons.
      for (let x = 0; x <= 10; x++) {
        for (let y = 0; y <= 10; y++) {
          const point = galaxyCameraPlanePoint(
            state,
            { x: (x / 10) * view.width, y: (y / 10) * view.height },
            view,
          );
          if (!point) continue;
          for (const axis of ['x', 'z'] as const) {
            const coordinate = Math.max(
              navigation.min[axis],
              Math.min(navigation.max[axis], point[axis]),
            );
            expect(request[`min_${axis}`]).toBeLessThanOrEqual(coordinate);
            expect(request[`max_${axis}`]).toBeGreaterThanOrEqual(coordinate);
          }
        }
      }
    },
  );

  it('extends the forward and sideways horizon bounds at shallow pitch', () => {
    const shallow = { ...camera, pitchRad: 0.12 };
    expect(
      galaxyCameraPlanePoint(shallow, { x: 0, y: 0 }, viewport),
    ).toBeNull();
    const request = heatmapViewportRequest(shallow, viewport);
    expect(request.max_z).toBe(navigation.max.z);
    expect(request.min_z).toBeGreaterThan(navigation.min.z);
    expect(request.min_x).toBe(navigation.min.x);
    expect(request.max_x).toBe(navigation.max.x);

    const rotated = heatmapViewportRequest(
      { ...shallow, bearingRad: Math.PI / 2 },
      viewport,
    );
    expect(rotated.min_x).toBe(navigation.min.x);
    expect(rotated.max_x).toBeLessThan(navigation.max.x);
    expect(rotated.min_z).toBe(navigation.min.z);
    expect(rotated.max_z).toBe(navigation.max.z);
  });

  it('uses the horizon direction when a missed sky ray points to a different X side', () => {
    const shallow = { ...camera, pitchRad: 0.12, bearingRad: 0.58 };
    const horizonY =
      ((1 - Math.tan(shallow.pitchRad) / Math.tan(GALAXY_CAMERA_FOV_RAD / 2)) *
        viewport.height) /
      2;
    const nearHorizon = galaxyCameraPlanePoint(
      shallow,
      { x: viewport.width, y: horizonY + 0.001 },
      viewport,
    );
    expect(nearHorizon!.x).toBeGreaterThan(navigation.max.x);
    const request = heatmapViewportRequest(shallow, viewport);
    expect(request.min_x).toBe(navigation.min.x);
    expect(request.max_x).toBe(navigation.max.x);
    expect(request.max_z).toBe(navigation.max.z);
  });

  it('clamps outward-snapped bounds to the navigation envelope', () => {
    const request = heatmapViewportRequest({
      ...camera,
      focusLy: { x: 245_000, y: -245_000, z: 245_000 },
    });
    for (const axis of ['x', 'y', 'z'] as const) {
      expect(request[`min_${axis}`]).toBeGreaterThanOrEqual(
        navigation.min[axis],
      );
      expect(request[`max_${axis}`]).toBeLessThanOrEqual(navigation.max[axis]);
      expect(request[`min_${axis}`]).toBeLessThan(request[`max_${axis}`]);
    }
    expect(request.max_x).toBe(navigation.max.x);
    expect(request.min_y).toBe(navigation.min.y);
    expect(request.max_z).toBe(navigation.max.z);
  });

  it('retains the vertical radius around the camera elevation', () => {
    const request = heatmapViewportRequest(camera, viewport);
    const elevated = heatmapViewportRequest(
      {
        ...camera,
        focusLy: { ...camera.focusLy, y: camera.focusLy.y + 16_384 },
      },
      viewport,
    );
    expect(elevated).toEqual({
      ...request,
      min_y: request.min_y + 16_384,
      max_y: request.max_y + 16_384,
    });
  });

  it('still covers the whole galaxy-region extent in the default overview', () => {
    const request = heatmapViewportRequest(null, viewport);
    expect(request.min_x).toBeLessThanOrEqual(-50_000);
    expect(request.max_x).toBeGreaterThanOrEqual(52_000);
    expect(request.min_z).toBeLessThanOrEqual(-25_000);
    expect(request.max_z).toBeGreaterThanOrEqual(78_000);
    expect(request.min_y).toBeLessThanOrEqual(-2_400);
    expect(request.max_y).toBeGreaterThanOrEqual(2_400);
  });

  it('shrinks the bounded region as the view zooms in, even at the same fine voxel size', () => {
    const wide = heatmapViewportRequest({ ...camera, distanceLy: 118_000 });
    const regional = heatmapViewportRequest(camera);
    const near = heatmapViewportRequest({ ...camera, distanceLy: 3_000 });
    expect(wide.max_x - wide.min_x).toBeGreaterThan(
      regional.max_x - regional.min_x,
    );
    expect(regional.max_x - regional.min_x).toBeGreaterThan(
      near.max_x - near.min_x,
    );
    expect(near.voxel_size).toBe(regional.voxel_size);
    expect(heatmapViewportQueryKey(near)).not.toEqual(
      heatmapViewportQueryKey(regional),
    );
  });

  it.each([10_000, 118_000])(
    'reuses the cache for nearby zoom stops and small moves at %s LY',
    (distanceLy) => {
      const wide = {
        ...camera,
        focusLy: { x: 0, y: 0, z: 0 },
        distanceLy,
      };
      const first = heatmapViewportRequest(wide);
      const nearby = heatmapViewportRequest({
        ...wide,
        distanceLy: distanceLy + 50,
        focusLy: { x: 20, y: 20, z: 20 },
      });
      expect(heatmapViewportQueryKey(first)).toEqual(
        heatmapViewportQueryKey(nearby),
      );
    },
  );

  it('keys by aspect-dependent bounds, independent of pixel density', () => {
    const first = heatmapViewportQueryKey(
      heatmapViewportRequest(camera, viewport),
    );
    const resized = heatmapViewportQueryKey(
      heatmapViewportRequest(camera, {
        width: viewport.width * 2,
        height: viewport.height,
      }),
    );
    const scaled = heatmapViewportQueryKey(
      heatmapViewportRequest(camera, {
        width: viewport.width * 2,
        height: viewport.height * 2,
      }),
    );
    expect(resized).not.toEqual(first);
    expect(scaled).toEqual(first);
  });

  it.each([
    { width: 0, height: 720 },
    { width: 1_280, height: -1 },
    { width: Infinity, height: 720 },
    { width: 1_280, height: NaN },
  ])('rejects invalid viewport dimensions %j', (view) => {
    expect(() => heatmapViewportRequest(camera, view)).toThrow();
  });

  it('partitions the cache when panning to a different bounds bucket at the same zoom', () => {
    const first = heatmapViewportRequest(camera);
    const moved = heatmapViewportRequest({
      ...camera,
      focusLy: { ...camera.focusLy, x: 42_000 },
    });
    expect(moved.voxel_size).toBe(first.voxel_size);
    expect(heatmapViewportQueryKey(moved)).not.toEqual(
      heatmapViewportQueryKey(first),
    );
    expect(heatmapViewportQueryKey(moved)[0]).toBe('map-heatmap');
  });

  it('rejects non-finite camera coordinates before sending a request', () => {
    expect(() =>
      heatmapViewportRequest({ ...camera, distanceLy: -1 }),
    ).toThrow();
    expect(() =>
      heatmapViewportRequest({ ...camera, pitchRad: NaN }),
    ).toThrow();
    expect(() =>
      heatmapViewportRequest({ ...camera, bearingRad: Infinity }),
    ).toThrow();
    expect(() =>
      heatmapViewportRequest({ ...camera, distanceLy: NaN }),
    ).toThrow();
    expect(() =>
      heatmapViewportRequest({
        ...camera,
        focusLy: { x: Infinity, y: 0, z: 0 },
      }),
    ).toThrow();
  });
});

describe('density coverage legend', () => {
  it('names the served resolution of complete coverage', () => {
    expect(
      densityCoverageLabel(readyResult({ ...pyramid, requested_level: 2 })),
    ).toBe('Complete at 100 LY');
  });

  it.each([null, 8])(
    'shows numbers for legacy truncated coverage (level count %s)',
    (count) => {
      const result = readyResult({
        ...pyramid,
        truncated: true,
        coverage: 'truncated',
        level_cell_count: count,
      });
      expect(densityCoverageLabel(result)).toBe(
        count === null
          ? 'Truncated · 2 cells shown at 100 LY'
          : 'Truncated · 2 of 8 cells shown at 100 LY',
      );
      expect(densityCoverageLabel(result)).not.toContain('bounded view');
    },
  );
});

describe('classifyDensityError', () => {
  it('reports HTTP 503 as unavailable', () => {
    expect(
      classifyDensityError(new ApiError(503, '/api/map/heatmap', {})),
    ).toEqual({ kind: 'unavailable', reason: 'http-503' });
  });

  it.each([
    new TypeError('Failed to fetch'),
    new ApiError(0, '/api/map/heatmap', new TypeError('Failed to fetch')),
  ])('reports network failures as unavailable', (error) => {
    expect(classifyDensityError(error)).toEqual({
      kind: 'unavailable',
      reason: 'network',
    });
  });

  it.each([
    new SyntaxError('Unexpected token'),
    new ApiError(200, '/api/map/heatmap', new SyntaxError('Unexpected token')),
  ])('reports malformed JSON as invalid', (error) => {
    expect(classifyDensityError(error)).toEqual({
      kind: 'invalid',
      reason: 'malformed-json',
    });
  });
});

describe('heatmapVoxelSizeForDistance', () => {
  it('collapses nearby camera distances onto one voxel size so the query can reuse a cached result', () => {
    expect(heatmapVoxelSizeForDistance(118_000)).toBe(590);
    expect(heatmapVoxelSizeForDistance(118_050)).toBe(590);
    expect(heatmapVoxelSizeForDistance(118_000)).toBe(
      heatmapVoxelSizeForDistance(118_050),
    );
  });

  it('floors at the endpoint minimum voxel size', () => {
    expect(heatmapVoxelSizeForDistance(100)).toBe(200);
    expect(heatmapVoxelSizeForDistance(0)).toBe(200);
  });
});

describe('densityContributionFrom', () => {
  it.each<GalaxyDensityResult>([
    { kind: 'none' },
    { kind: 'unavailable', reason: 'http-503' },
    { kind: 'invalid', reason: 'Invalid bounds' },
  ])(
    'retains the explicit $kind result without making a contribution',
    (result) => {
      expect(densityContributionFrom(result, 1)).toEqual(result);
    },
  );

  it('returns a ready contribution for a validated pyramid', () => {
    const result = densityContributionFrom(readyResult(pyramid), 1);
    expect(result.kind).toBe('ready');
    if (result.kind !== 'ready')
      throw new Error('Expected density contribution');
    expect(result.contribution.owner).toBe('CATALOGUE');
    expect(result.contribution.revision).toBe(1);
    expect(result.contribution.layers[0]?.id).toBe('catalogue-density');
  });
});
