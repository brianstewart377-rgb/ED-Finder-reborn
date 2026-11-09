import { describe, expect, it } from 'vitest';
import { NullEngine } from '@babylonjs/core/Engines/nullEngine.js';
import { ImageProcessingConfiguration } from '@babylonjs/core/Materials/imageProcessingConfiguration.js';

import { buildAuthoritativeGalaxyRegionsAsset } from '../../../../vite.galaxy-regions';
import {
  createBabylonGalaxyScene,
  updateGalaxyRegionFillAppearance,
} from './adapter';
import type { GalaxySceneContract } from '../contracts';
import {
  createGalaxyRegionsContribution,
  GALAXY_REGION_COUNT,
  GALAXY_REGION_SOURCE_SHA256,
  validateGalaxyRegionsPayload,
} from '../galaxy-regions';

describe('Babylon authoritative Galaxy regions renderer', () => {
  const assetBody = buildAuthoritativeGalaxyRegionsAsset();
  const payload = validateGalaxyRegionsPayload(
    JSON.parse(assetBody) as unknown,
  );

  it('renders the complete accepted region set from canonical x/z coordinates', () => {
    const revision = 8;
    const scene: GalaxySceneContract = {
      kind: 'galaxy',
      revision,
      camera: {
        focusLy: { x: 0, y: 0, z: 25_000 },
        distanceLy: 120_000,
        bearingRad: 0,
        pitchRad: 0.5,
        projection: 'perspective',
        revision,
      },
      selection: [],
      contributions: [createGalaxyRegionsContribution(payload, revision)],
    };
    const engine = new NullEngine();

    const product = createBabylonGalaxyScene(engine, scene);

    expect(product.regionBoundaryMesh).not.toBeNull();
    expect(product.regionBoundaryMesh?.metadata).toMatchObject({
      spatialLayer: {
        id: 'galaxy-regions',
        sourceSha256: GALAXY_REGION_SOURCE_SHA256,
        regionCount: 42,
        boundaryCount: 22_595,
        lookupWidth: 2_048,
        lookupHeight: 2_048,
        pickableRegionCount: 42,
      },
    });
    expect(product.regionFillMeshes).toHaveLength(GALAXY_REGION_COUNT);
    expect(product.visualGlow.intensity).toBe(0.42);
    expect(product.visualGlow.blurKernelSize).toBe(20);
    expect(product.scene.imageProcessingConfiguration.toneMappingType).toBe(
      ImageProcessingConfiguration.TONEMAPPING_ACES,
    );
    expect(product.scene.metadata.spatialScene.visualProfile).toMatchObject({
      toneMapping: 'ACES',
      emissiveGlow: true,
      ambientDensitySubstitute: false,
    });
    expect(product.regionFillMeshes.every((fill) => fill.mesh.isPickable)).toBe(
      true,
    );
    expect(
      product.regionFillMeshes.reduce(
        (total, fill) =>
          total + Number(fill.mesh.metadata.spatialLayer.sourceCellCount ?? 0),
        0,
      ),
    ).toBeGreaterThan(0);
    expect(product.renderedLayers).toEqual([
      expect.objectContaining({
        layerId: 'galaxy-regions',
        acceptedTargetCount: 42,
        renderedTargetCount: 42,
      }),
    ]);
    const positions = product.regionBoundaryMesh?.getVerticesData('position');
    expect(positions?.length).toBeGreaterThan(0);
    expect(
      positions
        ?.filter((_value, index) => index % 3 === 1)
        .every((y) => y === 0),
    ).toBe(true);

    product.scene.dispose();
    engine.dispose();
  });

  it('renders semantic region selection with a distinct exact-fill treatment', () => {
    const revision = 9;
    const scene: GalaxySceneContract = {
      kind: 'galaxy',
      revision,
      camera: {
        focusLy: { x: 0, y: 0, z: 0 },
        distanceLy: 120_000,
        bearingRad: 0,
        pitchRad: 0.5,
        projection: 'perspective',
        revision,
      },
      selection: [{ kind: 'region', id: '18' }],
      contributions: [createGalaxyRegionsContribution(payload, revision)],
    };
    const engine = new NullEngine();

    const product = createBabylonGalaxyScene(engine, scene);
    const selected = product.regionFillMeshes.find(
      (fill) => fill.regionId === 18,
    );
    const neighbour = product.regionFillMeshes.find(
      (fill) => fill.regionId === 17,
    );

    expect(product.selectedRegionId).toBe(18);
    expect(selected?.regionName).toBe('Inner Orion Spur');
    expect(selected?.material.alpha).toBeGreaterThan(neighbour!.material.alpha);
    expect(selected?.material.alpha).toBeGreaterThanOrEqual(0.3);
    expect(selected?.material.emissiveColor.asArray()).not.toEqual(
      neighbour?.material.emissiveColor.asArray(),
    );
    expect(product.visualGlow.hasMesh(selected!.mesh)).toBe(false);

    product.scene.dispose();
    engine.dispose();
  });

  it('lights the exact hovered region and restores it when hover clears', () => {
    const revision = 10;
    const scene: GalaxySceneContract = {
      kind: 'galaxy',
      revision,
      camera: {
        focusLy: { x: 0, y: 0, z: 0 },
        distanceLy: 120_000,
        bearingRad: 0,
        pitchRad: 0.5,
        projection: 'perspective',
        revision,
      },
      selection: [],
      contributions: [createGalaxyRegionsContribution(payload, revision)],
    };
    const engine = new NullEngine();
    const product = createBabylonGalaxyScene(engine, scene);
    const hovered = product.regionFillMeshes.find(
      (fill) => fill.regionId === 18,
    )!;
    const neighbour = product.regionFillMeshes.find(
      (fill) => fill.regionId === 17,
    )!;
    const baseAlpha = hovered.material.alpha;
    const baseEmissive = hovered.material.emissiveColor.asArray();

    expect(product.visualGlow.hasMesh(hovered.mesh)).toBe(false);
    updateGalaxyRegionFillAppearance(product, hovered.regionId);

    expect(hovered.material.alpha).toBeGreaterThan(baseAlpha);
    expect(hovered.material.alpha).toBeGreaterThan(neighbour.material.alpha);
    expect(hovered.material.alpha).toBeGreaterThanOrEqual(0.25);
    expect(hovered.material.emissiveColor.asArray()).not.toEqual(baseEmissive);
    expect(product.visualGlow.hasMesh(hovered.mesh)).toBe(false);
    expect(product.visualGlow.hasMesh(neighbour.mesh)).toBe(false);

    updateGalaxyRegionFillAppearance(product, null);

    expect(hovered.material.alpha).toBe(baseAlpha);
    expect(hovered.material.emissiveColor.asArray()).toEqual(baseEmissive);
    expect(product.visualGlow.hasMesh(hovered.mesh)).toBe(false);

    product.scene.dispose();
    engine.dispose();
  });
});
