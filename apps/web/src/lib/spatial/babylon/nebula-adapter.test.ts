import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { Constants } from '@babylonjs/core/Engines/constants.js';
import { NullEngine } from '@babylonjs/core/Engines/nullEngine.js';
import type { Mesh } from '@babylonjs/core/Meshes/mesh.js';
import { describe, expect, it, vi } from 'vitest';

import type { GalaxySceneContract, RenderedLayerReceipt } from '../contracts';
import {
  createGalaxyNebulaeContribution,
  parseGalaxyNebulaeAsset,
  type GalaxyNebulaePayload,
} from '../galaxy-nebulae';
import { createBabylonGalaxyScene, createBabylonSession } from './adapter';

const fixtureAsset = {
  schemaVersion: 2,
  datasetId: 'fixture-nebula-inventory',
  sourceUrl: 'https://edastro.com/mapcharts/files/nebulae-coordinates.csv',
  catalogueUrl: 'https://edastro.com/mapcharts/files.html',
  rightsNotice: 'Copyright © CMDR Orvidius — All Rights Reserved',
  usageBasis: 'Purpose-published CSV; non-commercial use accepted by the owner',
  attribution: 'EDAstro / CMDR Orvidius',
  sourceLastModified: null,
  sourceByteCount: 123,
  sourceSha256: 'a'.repeat(64),
};

const diffuse = {
  id: 'MAPCHARTS:1',
  source: 'EDAstro Mapcharts',
  sourceId: '1',
  name: 'Regional Cloud',
  systemName: 'Cloud AA-A h1',
  regionName: 'Inner Orion Spur',
  regionId: 18,
  kind: 'nebula',
  coordinates: [125, -34, 912],
  poiUrl: null,
};

const planetary = [2, 3, 4].map((id) => ({
  ...diffuse,
  id: `MAPCHARTS:${id}`,
  sourceId: String(id),
  name: `Planetary ${id}`,
  kind: 'planetary-nebula',
  coordinates: [id * 10, -id, id * 20],
}));

function parseFixture(nebulae: readonly unknown[]): GalaxyNebulaePayload {
  return parseGalaxyNebulaeAsset({
    ...fixtureAsset,
    count: nebulae.length,
    nebulae,
  });
}

function contractAt(
  payload: GalaxyNebulaePayload,
  distanceLy: number,
): GalaxySceneContract {
  return {
    kind: 'galaxy',
    revision: 1,
    camera: {
      focusLy: { x: 0, y: 0, z: 0 },
      distanceLy,
      bearingRad: 0,
      pitchRad: 0.5,
      projection: 'perspective',
      revision: 1,
    },
    selection: [],
    contributions: [createGalaxyNebulaeContribution(payload, 1)],
  };
}

function visibleInstances(mesh: Mesh | null): number {
  return mesh?.isEnabled() && mesh.visibility > 0 ? mesh.thinInstanceCount : 0;
}

describe('Babylon nebula inventory and current camera receipts', () => {
  it.each([
    { distanceLy: 118_000, diffuseShown: 249, planetaryShown: 0 },
    { distanceLy: 2_000, diffuseShown: 249, planetaryShown: 5_496 },
  ])(
    'reports the real parsed asset and actual instances at $distanceLy LY',
    ({ distanceLy, diffuseShown, planetaryShown }) => {
      const payload = parseGalaxyNebulaeAsset(
        JSON.parse(
          readFileSync(
            resolve(
              process.cwd(),
              'static/assets/edastro-mapcharts-nebulae.json',
            ),
            'utf8',
          ),
        ) as unknown,
      );
      const engine = new NullEngine();
      try {
        const product = createBabylonGalaxyScene(
          engine,
          contractAt(payload, distanceLy),
        );
        expect(payload.nebulae).toHaveLength(5_842);
        expect(product.nebulaMesh?.thinInstanceCount).toBe(249);
        expect(product.nebulaCloudMesh?.thinInstanceCount).toBe(747);
        expect(product.planetaryNebulaMesh?.thinInstanceCount).toBe(5_496);
        const renderedTargetCount =
          visibleInstances(product.nebulaMesh) +
          visibleInstances(product.planetaryNebulaMesh);
        expect(renderedTargetCount).toBe(diffuseShown + planetaryShown);
        expect(product.renderedLayers).toContainEqual(
          expect.objectContaining({
            layerId: 'galaxy-nebulae',
            acceptedTargetCount: payload.nebulae.length,
            renderedTargetCount,
          }),
        );
        expect(product.scene.metadata).toMatchObject({
          spatialScene: {
            nebulae: {
              catalogueRecordCount: 5_842,
              acceptedFeatureCount: 5_842,
              selectedLandmarkCount: 5_745,
              deduplicatedEntryCount: 97,
              cappedEntryCount: 0,
              currentlyVisibleCount: renderedTargetCount,
              countsByKind: {
                nebula: {
                  catalogueRecordCount: 346,
                  acceptedFeatureCount: 346,
                  selectedLandmarkCount: 249,
                  deduplicatedEntryCount: 97,
                  currentlyVisibleCount: diffuseShown,
                  clouds: {
                    selectedLandmarkCount: 249,
                    enabledLandmarkCount: distanceLy > 2_000 ? 249 : 0,
                    enabledQuadCount: distanceLy > 2_000 ? 747 : 0,
                    quadsPerLandmark: 3,
                  },
                },
                'planetary-nebula': {
                  catalogueRecordCount: 5_496,
                  acceptedFeatureCount: 5_496,
                  selectedLandmarkCount: 5_496,
                  deduplicatedEntryCount: 0,
                  currentlyVisibleCount: planetaryShown,
                },
              },
            },
          },
        });
        expect(product.nebulaMesh?.metadata).toMatchObject({
          spatialLayer: {
            kind: 'nebula',
            landmarkCellLy: 1_400,
            landmarkLimit: 260,
            currentlyVisibleCount: diffuseShown,
            renderedPointCount: diffuseShown,
          },
        });
        expect(product.planetaryNebulaMesh?.metadata).toMatchObject({
          spatialLayer: {
            kind: 'planetary-nebula',
            currentlyVisibleCount: planetaryShown,
            renderedPointCount: planetaryShown,
          },
        });
      } finally {
        engine.dispose();
      }
    },
  );

  it('batches non-pickable clouds on one soft additive atlas and disposes their GPU resources', () => {
    const engine = new NullEngine();
    try {
      const product = createBabylonGalaxyScene(
        engine,
        contractAt(
          parseFixture([
            diffuse,
            { ...diffuse, id: 'MAPCHARTS:5', coordinates: [8_000, 0, 0] },
          ]),
          8_000,
        ),
      );
      const markerMesh = product.nebulaMesh!;
      const cloudMesh = product.nebulaCloudMesh!;
      const material = cloudMesh.material!;
      expect(markerMesh.thinInstanceCount).toBe(2);
      expect(markerMesh.isPickable).toBe(false);
      expect(markerMesh.thinInstanceEnablePicking).toBe(false);
      expect(
        markerMesh
          .thinInstanceGetWorldMatrices()[0]
          ?.getTranslation()
          .asArray(),
      ).toEqual([125, -34, 912]);
      expect(cloudMesh.thinInstanceCount).toBe(6);
      expect(cloudMesh.getTotalVertices()).toBe(4);
      expect(cloudMesh.getTotalIndices()).toBe(6);
      expect(cloudMesh.subMeshes).toHaveLength(1);
      expect(cloudMesh.isPickable).toBe(false);
      expect(cloudMesh.thinInstanceEnablePicking).toBe(false);
      expect(material.alphaMode).toBe(Constants.ALPHA_ADD);
      expect(material.disableDepthWrite).toBe(true);
      expect(material.backFaceCulling).toBe(false);
      const textures = material.getActiveTextures();
      expect(textures).toHaveLength(1);
      const atlas = textures[0]!;
      expect(atlas.getSize()).toEqual({ width: 128, height: 128 });
      const disposeAtlas = vi.spyOn(atlas, 'dispose');
      const disposeMaterial = vi.spyOn(material, 'dispose');
      cloudMesh.dispose();
      expect(disposeMaterial).toHaveBeenCalledOnce();
      expect(disposeAtlas).toHaveBeenCalledOnce();
      product.scene.dispose();
      expect(disposeMaterial).toHaveBeenCalledOnce();
      expect(disposeAtlas).toHaveBeenCalledOnce();
    } finally {
      engine.dispose();
    }
  });

  it.each([
    {
      label: 'mixed overview',
      entries: [diffuse, ...planetary],
      distanceLy: 8_000,
      shown: 1,
    },
    {
      label: 'mixed close',
      entries: [diffuse, ...planetary],
      distanceLy: 2_000,
      shown: 4,
    },
    {
      label: 'mixed overlap',
      entries: [diffuse, ...planetary],
      distanceLy: 3_500,
      shown: 4,
    },
    {
      label: 'planetary only overview',
      entries: planetary,
      distanceLy: 8_000,
      shown: 0,
    },
    {
      label: 'planetary only close',
      entries: planetary,
      distanceLy: 3_000,
      shown: 3,
    },
    { label: 'empty', entries: [], distanceLy: 3_000, shown: 0 },
    {
      label: 'diffuse landmark persists when its cloud fades out',
      entries: [diffuse],
      distanceLy: 2_000,
      shown: 1,
    },
  ])(
    'keeps accepted and rendered counts distinct for $label',
    ({ entries, distanceLy, shown }) => {
      const payload = parseFixture(entries);
      const engine = new NullEngine();
      try {
        const product = createBabylonGalaxyScene(
          engine,
          contractAt(payload, distanceLy),
        );
        expect(product.renderedLayers).toContainEqual(
          expect.objectContaining({
            layerId: 'galaxy-nebulae',
            acceptedTargetCount: entries.length,
            renderedTargetCount: shown,
          }),
        );
        expect(
          visibleInstances(product.nebulaMesh) +
            visibleInstances(product.planetaryNebulaMesh),
        ).toBe(shown);
        expect(product.scene.metadata).toMatchObject({
          spatialScene: {
            nebulae: {
              catalogueRecordCount: entries.length,
              acceptedFeatureCount: entries.length,
              currentlyVisibleCount: shown,
            },
          },
        });
      } finally {
        engine.dispose();
      }
    },
  );

  it('keeps diffuse markers while clouds fade with hysteresis and updates receipts without reloading', () => {
    const engine = new NullEngine();
    const session = createBabylonSession(engine, 'WEBGL2');
    const events = vi.fn();
    const contract = contractAt(parseFixture([diffuse, ...planetary]), 8_000);
    try {
      expect(
        session.execute?.({ type: 'LOAD_SCENE', scene: contract }, events),
      ).toEqual({ status: 'executed' });
      const scene = engine.scenes.at(-1)!;
      const diffuseMesh = scene.meshes.find(
        (mesh) => mesh.metadata?.spatialLayer?.kind === 'nebula',
      ) as Mesh;
      const planetaryMesh = scene.meshes.find(
        (mesh) => mesh.metadata?.spatialLayer?.kind === 'planetary-nebula',
      ) as Mesh;
      const cloudMesh = scene.meshes.find(
        (mesh) => mesh.metadata?.spatialLayer?.kind === 'nebula-cloud',
      ) as Mesh;
      expect(cloudMesh.thinInstanceCount).toBe(3);
      const cloudMatrices = cloudMesh.thinInstanceGetWorldMatrices();
      expect(planetaryMesh.isPickable).toBe(false);
      expect(
        planetaryMesh
          .thinInstanceGetWorldMatrices()[0]
          ?.getTranslation()
          .asArray(),
      ).toEqual([20, -2, 40]);
      const markerVertices = planetaryMesh.getVerticesData('position')!;
      let markerRadius = 0;
      for (let index = 0; index < markerVertices.length; index += 3) {
        markerRadius = Math.max(
          markerRadius,
          Math.hypot(
            markerVertices[index]!,
            markerVertices[index + 1]!,
            markerVertices[index + 2]!,
          ),
        );
      }
      expect(markerRadius).toBeCloseTo(3);

      for (const [index, sample] of [
        {
          distanceLy: 8_000,
          planetaryVisibility: 0,
          cloudVisibility: 1,
          shown: 1,
        },
        {
          distanceLy: 4_000,
          planetaryVisibility: 0,
          cloudVisibility: 1 / 3,
          shown: 1,
        },
        {
          distanceLy: 3_500,
          planetaryVisibility: 0.5,
          cloudVisibility: 0.25,
          shown: 4,
        },
        {
          distanceLy: 3_000,
          planetaryVisibility: 1,
          cloudVisibility: 1 / 6,
          shown: 4,
        },
        {
          distanceLy: 2_000,
          planetaryVisibility: 1,
          cloudVisibility: 0,
          shown: 4,
        },
        {
          distanceLy: 2_200,
          planetaryVisibility: 1,
          cloudVisibility: 0,
          shown: 4,
        },
        {
          distanceLy: 2_400,
          planetaryVisibility: 1,
          cloudVisibility: 1 / 15,
          shown: 4,
        },
        {
          distanceLy: 2_200,
          planetaryVisibility: 1,
          cloudVisibility: 1 / 30,
          shown: 4,
        },
        {
          distanceLy: 400,
          planetaryVisibility: 1,
          cloudVisibility: 0,
          shown: 4,
        },
        {
          distanceLy: 118_000,
          planetaryVisibility: 0,
          cloudVisibility: 1,
          shown: 1,
        },
      ].entries()) {
        expect(
          session.execute?.(
            {
              type: 'SET_CAMERA',
              camera: {
                ...contract.camera,
                distanceLy: sample.distanceLy,
                revision: index + 2,
              },
            },
            events,
          ),
        ).toEqual({ status: 'executed' });
        expect(engine.scenes.at(-1)).toBe(scene);
        expect(planetaryMesh.visibility).toBeCloseTo(
          sample.planetaryVisibility,
        );
        expect(diffuseMesh.visibility).toBe(1);
        expect(diffuseMesh.isEnabled()).toBe(true);
        expect(cloudMesh.visibility).toBeCloseTo(sample.cloudVisibility);
        expect(planetaryMesh.isEnabled()).toBe(sample.planetaryVisibility > 0);
        expect(cloudMesh.isEnabled()).toBe(sample.cloudVisibility > 0);
        expect(cloudMesh.thinInstanceGetWorldMatrices()).toBe(cloudMatrices);
        expect(
          visibleInstances(diffuseMesh) + visibleInstances(planetaryMesh),
        ).toBe(sample.shown);
        const receipts = scene.metadata.spatialScene
          .renderedLayers as readonly RenderedLayerReceipt[];
        expect(
          receipts.find((receipt) => receipt.layerId === 'galaxy-nebulae'),
        ).toMatchObject({
          acceptedTargetCount: 4,
          renderedTargetCount: sample.shown,
        });
        expect(planetaryMesh.metadata.spatialLayer.renderedPointCount).toBe(
          sample.planetaryVisibility > 0 ? 3 : 0,
        );
        expect(diffuseMesh.metadata.spatialLayer.renderedPointCount).toBe(1);
        expect(cloudMesh.metadata.spatialLayer).toMatchObject({
          enabledLandmarkCount: sample.cloudVisibility > 0 ? 1 : 0,
          enabledQuadCount: sample.cloudVisibility > 0 ? 3 : 0,
        });
        expect(scene.metadata.spatialScene.nebulae.currentlyVisibleCount).toBe(
          sample.shown,
        );
      }
      expect(events).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'CONTRIBUTION_APPLIED',
          contributionId: 'catalogue:galaxy-nebulae',
          renderedLayers: expect.arrayContaining([
            expect.objectContaining({
              acceptedTargetCount: 4,
              renderedTargetCount: 4,
            }),
          ]),
        }),
      );
    } finally {
      session.dispose();
      engine.dispose();
    }
  });

  it('keeps the same significant landmark and instance transforms when source order changes', () => {
    const entries = [
      {
        ...diffuse,
        id: 'MAPCHARTS:0',
        name: diffuse.systemName,
        regionName: null,
      },
      { ...diffuse, id: 'MAPCHARTS:9', coordinates: [130, -30, 915] },
      { ...diffuse, id: 'MAPCHARTS:2', coordinates: [140, -20, 920] },
      { ...diffuse, id: 'MAPCHARTS:5', coordinates: [8_000, 0, 0] },
    ];
    const engine = new NullEngine();
    try {
      const forward = createBabylonGalaxyScene(
        engine,
        contractAt(parseFixture(entries), 8_000),
      );
      const reverse = createBabylonGalaxyScene(
        engine,
        contractAt(parseFixture([...entries].reverse()), 8_000),
      );
      expect(forward.nebulaMesh?.thinInstanceCount).toBe(2);
      expect(forward.nebulaCloudMesh?.thinInstanceCount).toBe(6);
      expect(
        forward.nebulaMesh?.metadata.spatialLayer.selectedLandmarkIds,
      ).toEqual(['MAPCHARTS:2', 'MAPCHARTS:5']);
      expect(
        reverse.nebulaMesh?.metadata.spatialLayer.selectedLandmarkIds,
      ).toEqual(forward.nebulaMesh?.metadata.spatialLayer.selectedLandmarkIds);
      expect(
        reverse.nebulaMesh
          ?.thinInstanceGetWorldMatrices()
          .map((matrix) => Array.from(matrix.asArray())),
      ).toEqual(
        forward.nebulaMesh
          ?.thinInstanceGetWorldMatrices()
          .map((matrix) => Array.from(matrix.asArray())),
      );
      expect(
        reverse.nebulaCloudMesh
          ?.thinInstanceGetWorldMatrices()
          .map((matrix) => Array.from(matrix.asArray())),
      ).toEqual(
        forward.nebulaCloudMesh
          ?.thinInstanceGetWorldMatrices()
          .map((matrix) => Array.from(matrix.asArray())),
      );
      expect(forward.nebulaMesh?.metadata.spatialLayer).toMatchObject({
        acceptedFeatureCount: 4,
        selectedLandmarkCount: 2,
        deduplicatedEntryCount: 2,
        cappedEntryCount: 0,
      });
    } finally {
      engine.dispose();
    }
  });
});
