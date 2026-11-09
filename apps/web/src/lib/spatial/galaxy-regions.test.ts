import { describe, expect, it } from 'vitest';

import { buildAuthoritativeGalaxyRegionsAsset } from '../../../vite.galaxy-regions';
import type { GalaxySceneContract } from './contracts';
import {
  buildGalaxyRegionFillGeometry,
  createGalaxyRegionsContribution,
  findGalaxyRegionAt,
  GALAXY_REGION_ASSET_BUDGET_BYTES,
  GALAXY_REGION_BOUNDARY_LIMIT,
  GALAXY_REGION_COUNT,
  GALAXY_REGION_FILL_RECTANGLE_LIMIT,
  GALAXY_REGION_FILL_Y_LY,
  GALAXY_REGION_NAMES,
  GALAXY_REGION_SOURCE_SHA256,
  galaxyRegionsSceneLayer,
  validateGalaxyRegionsPayload,
} from './galaxy-regions';

describe('authoritative Galaxy regions', () => {
  const assetBody = buildAuthoritativeGalaxyRegionsAsset();
  const payload = validateGalaxyRegionsPayload(
    JSON.parse(assetBody) as unknown,
  );

  it('builds the pinned source into all exact 42 regions within the asset budget', () => {
    expect(new TextEncoder().encode(assetBody).byteLength).toBeLessThanOrEqual(
      GALAXY_REGION_ASSET_BUDGET_BYTES,
    );
    expect(payload.source.sha256).toBe(GALAXY_REGION_SOURCE_SHA256);
    expect(payload.regions).toHaveLength(GALAXY_REGION_COUNT);
    expect(payload.regions.map((region) => region.name)).toEqual(
      GALAXY_REGION_NAMES,
    );
    expect(payload.boundaries).toHaveLength(22_595);
    expect(payload.boundaries.length).toBeLessThanOrEqual(
      GALAXY_REGION_BOUNDARY_LIMIT,
    );
  });

  it('keeps every deterministic label anchor inside the matching lookup region', () => {
    for (const region of payload.regions) {
      expect(findGalaxyRegionAt(payload.lookup, region.labelLy)).toMatchObject({
        id: region.id,
        name: region.name,
      });
    }
    expect(
      findGalaxyRegionAt(payload.lookup, { x: -100_000, z: -100_000 }),
    ).toBeNull();
  });

  it('builds exact, bounded fills that reconcile to every non-zero RLE cell', () => {
    const geometry = buildGalaxyRegionFillGeometry(payload.lookup);
    const sourceCellCounts = new Uint32Array(GALAXY_REGION_COUNT + 1);
    for (const row of payload.lookup.regionmap) {
      for (const [length, regionId] of row) {
        sourceCellCounts[regionId] += length;
      }
    }

    expect(geometry).toHaveLength(GALAXY_REGION_COUNT);
    expect(
      geometry.reduce((total, region) => total + region.rectangleCount, 0),
    ).toBeLessThanOrEqual(GALAXY_REGION_FILL_RECTANGLE_LIMIT);

    let invalidRendererOffsets = 0;
    let lookupMismatches = 0;
    for (const region of geometry) {
      expect(region.rectangleCount).toBeGreaterThan(0);
      expect(region.positions).toHaveLength(region.rectangleCount * 12);
      expect(region.indices).toHaveLength(region.rectangleCount * 6);
      expect(region.sourceCellCount).toBe(sourceCellCounts[region.region.id]);

      for (let offset = 0; offset < region.positions.length; offset += 12) {
        const rectangle = region.positions.slice(offset, offset + 12);
        if (
          [rectangle[1], rectangle[4], rectangle[7], rectangle[10]].some(
            (y) => y !== GALAXY_REGION_FILL_Y_LY,
          )
        ) {
          invalidRendererOffsets += 1;
        }
        const identity = findGalaxyRegionAt(payload.lookup, {
          x: (rectangle[0]! + rectangle[3]!) / 2,
          z: (rectangle[2]! + rectangle[8]!) / 2,
        });
        if (
          identity?.id !== region.region.id ||
          identity.name !== region.region.name
        ) {
          lookupMismatches += 1;
        }
      }
    }
    expect(invalidRendererOffsets).toBe(0);
    expect(lookupMismatches).toBe(0);
  });

  it('rejects altered ID/name mappings even when the region count remains 42', () => {
    const altered = {
      ...payload,
      regions: payload.regions.map((region, index) =>
        index === 0 ? { ...region, name: 'Generated Core' } : region,
      ),
    };

    expect(() => validateGalaxyRegionsPayload(altered)).toThrow(/ID\/name/);
  });

  it('requires one complete spatial-platform-owned authoritative layer', () => {
    const contribution = createGalaxyRegionsContribution(payload, 7);
    const scene: GalaxySceneContract = {
      kind: 'galaxy',
      revision: 7,
      camera: {
        focusLy: { x: 0, y: 0, z: 0 },
        distanceLy: 80_000,
        bearingRad: 0,
        pitchRad: 0.5,
        projection: 'perspective',
        revision: 7,
      },
      selection: [],
      contributions: [contribution],
    };

    expect(galaxyRegionsSceneLayer(scene)).toMatchObject({
      contributionId: 'authoritative-galaxy-regions',
      contributionRevision: 7,
      payload: { regions: expect.any(Array) },
    });
    expect(() =>
      galaxyRegionsSceneLayer({
        ...scene,
        contributions: [{ ...contribution, owner: 'FINDER' }],
      }),
    ).toThrow(/SPATIAL_PLATFORM/);
  });
});
